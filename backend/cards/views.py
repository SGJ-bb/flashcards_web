"""
Views for the flashcards app.

Every endpoint (except health, login, register and refresh) requires a valid
JWT. All querysets are scoped to ``request.user`` so users can only see or
mutate their own data.

Endpoint map (mounted under /api/ by the project urls):
  auth/login/         POST   {username, password}         -> {access, refresh, user}
  auth/register/      POST   {username, password, email?} -> {access, refresh, user}
  auth/refresh/       POST   {refresh}                    -> {access}            (simplejwt)
  auth/logout/        POST   {refresh}                    -> 204                 (blacklist)
  auth/me/            GET                                  -> current user profile
  auth/me/            PATCH  {email?}                      -> updated profile
  auth/change-password/ POST {current_password, new_password} -> {detail}
  dashboard/          GET                                  -> dashboard aggregate
  cards/              GET/POST                             -> list / create
  cards/<id>/         GET/PATCH/DELETE                     -> retrieve / update / delete
  cards/<id>/check/   POST   {result}                      -> {feedback, new_box}
  cards/import/       POST   FormData(file=...)            -> {created, errors}
  cards/export/       GET                                  -> CSV download
  categories/         GET/POST                             -> list / create
  categories/<id>/    PATCH/DELETE                         -> rename / delete
  boxes/<n>/check/    GET                                  -> random card | {message}
  stats/daily/        GET                                  -> [{date, cards_reviewed, accuracy}]
"""

import csv
import io
import random

from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt import exceptions as jwt_exceptions
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Card, Category, ReviewLog
from .serializers import (
    CardSerializer,
    CategorySerializer,
    ChangePasswordSerializer,
    RegisterSerializer,
    ReviewLogSerializer,
    UpdateUserSerializer,
    UserSerializer,
)


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────
def _issue_tokens(user):
    """Return a fresh access+refresh token pair for the given user."""
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def _compute_streak(user):
    """
    Count consecutive days (backwards from today) on which the user has at
    least one ReviewLog. Lenient: if today has no review yet, the chain is
    measured starting from yesterday so an active streak isn't reset just
    because the user hasn't reviewed yet today.
    """
    reviewed_days = set(
        ReviewLog.objects.filter(user=user).dates('reviewed_at', 'day')
    )
    streak = 0
    # Use localdate() so the "today" boundary matches the timezone-aware
    # __date lookup (which extracts dates in the current timezone).
    day = timezone.localdate()
    if day not in reviewed_days:
        day = day - timedelta(days=1)
    while day in reviewed_days:
        streak += 1
        day = day - timedelta(days=1)
    return streak


def _card_context(request):
    """Shared serializer context (request is needed for user-scoped fields)."""
    return {'request': request}


class AuthWriteThrottle(ScopedRateThrottle):
    """Rate limit for unauthenticated auth endpoints (login/register).

    NOTE: ScopedRateThrottle reads the scope from the VIEW's ``throttle_scope``
    attribute (not from this class's ``scope`` attribute) — so any view that
    wants this throttle must also set ``throttle_scope = 'auth_write'``.
    """

    scope = 'auth_write'


# ─────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────
class HealthView(APIView):
    """Public liveness probe used by the frontend connection indicator.

    Also pings the database so the indicator can distinguish "app up, DB down"
    from "app up, DB up".
    """

    permission_classes = [AllowAny]
    throttle_classes = []  # health checks must never be rate-limited

    def get(self, request):
        db_ok = True
        try:
            # Cheap DB round-trip: ask SQLite for a scalar.
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute('SELECT 1')
                cur.fetchone()
        except Exception:  # pragma: no cover - defensive
            db_ok = False
        return Response({
            'status': 'ok' if db_ok else 'degraded',
            'db': db_ok,
        })


# ─────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────
class LoginView(APIView):
    """Authenticate with username/password and return JWT tokens + user.

    Throttled at 10/min (auth_write scope) to slow down brute-force attempts.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthWriteThrottle]
    throttle_scope = 'auth_write'  # read by ScopedRateThrottle.allow_request

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''

        if not username or not password:
            return Response(
                {'detail': '请提供用户名和密码'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response(
                {'detail': '用户名或密码错误'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        payload = _issue_tokens(user)
        payload['user'] = UserSerializer(user).data
        return Response(payload)


class RegisterView(APIView):
    """Create a new user and immediately issue JWT tokens.

    Throttled at 10/min to prevent bulk account creation.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthWriteThrottle]
    throttle_scope = 'auth_write'  # read by ScopedRateThrottle.allow_request

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            # Flatten DRF field errors into a single friendly message while
            # also returning the structured errors. The frontend reads
            # data.error first, then falls back to data.username[0].
            flat = []
            for field, messages in serializer.errors.items():
                items = messages if isinstance(messages, list) else [messages]
                for m in items:
                    label = '' if field == 'non_field_errors' else f'{field}: '
                    flat.append(f'{label}{m}')
            return Response(
                {'error': '; '.join(flat) or '注册失败', **serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = serializer.save()
        payload = _issue_tokens(user)
        payload['user'] = UserSerializer(user).data
        return Response(payload, status=status.HTTP_201_CREATED)


class LogoutView(APIView):
    """Blacklist the supplied refresh token so it can no longer be refreshed.

    Body: {"refresh": "<refresh token>"}. The access token stays valid until
    it expires (typically 2h) — this is standard JWT behaviour and cannot be
    avoided without shortening ACCESS_TOKEN_LIFETIME.

    Security: the refresh token's ``user_id`` claim must match the current
    authenticated user, otherwise a malicious user could revoke other users'
    refresh tokens by submitting tokens they obtained somehow.
    """

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response(
                {'detail': '需要提供 refresh token'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh_token)
            # Verify the token belongs to the requesting user before
            # blacklisting, so user A cannot revoke user B's sessions.
            token_user_id = token.get('user_id')
            if str(token_user_id) != str(request.user.pk):
                return Response(
                    {'detail': '只能注销当前登录用户的会话'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            token.blacklist()
        except jwt_exceptions.TokenError:
            return Response(
                {'detail': 'refresh token 无效或已过期'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            # token_blacklist app must be in INSTALLED_APPS; if it isn't,
            # blacklist() will raise. Surface a clear error rather than a 500.
            return Response(
                {'detail': '无法注销，请联系管理员'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """Get or update the current authenticated user's profile."""

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UpdateUserSerializer(
            request.user, data=request.data, partial=True,
            context={'request': request},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    """Change the current user's password after verifying the current one.

    All currently issued refresh tokens are blacklisted on success, forcing
    the user to log in again everywhere with the new password.
    """

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        current = serializer.validated_data['current_password']
        new_password = serializer.validated_data['new_password']

        user = request.user
        if not user.check_password(current):
            return Response(
                {'current_password': '当前密码不正确'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user.check_password(new_password):
            return Response(
                {'new_password': '新密码不能与当前密码相同'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()

        # Revoke all outstanding refresh tokens so other sessions must re-auth.
        from rest_framework_simplejwt.token_blacklist.models import (
            OutstandingToken,
        )
        for outstanding in OutstandingToken.objects.filter(user=user):
            try:
                RefreshToken(outstanding.token).blacklist()
            except jwt_exceptions.TokenError:
                # Already blacklisted or expired — safe to skip.
                continue

        # Issue fresh tokens for the current session so the user isn't logged
        # out immediately on the device that just changed the password.
        payload = _issue_tokens(user)
        payload['user'] = UserSerializer(user).data
        payload['detail'] = '密码修改成功'
        return Response(payload)


# ─────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────
class DashboardView(APIView):
    """Aggregate stats for the dashboard page."""

    def get(self, request):
        user = request.user
        cards_qs = Card.objects.filter(user=user)

        total_cards = cards_qs.count()

        today = timezone.localdate()
        today_logs = ReviewLog.objects.filter(user=user, reviewed_at__date=today)
        today_reviewed = today_logs.count()
        today_knew = today_logs.filter(result=ReviewLog.KNEW).count()
        today_accuracy = (
            round(today_knew / today_reviewed * 100) if today_reviewed > 0 else 0
        )

        streak_days = _compute_streak(user)

        cards_by_box = {
            str(box): cards_qs.filter(box_number=box).count()
            for box in range(1, 6)
        }

        recent_logs = (
            ReviewLog.objects.filter(user=user)
            .select_related('card')
            .order_by('-reviewed_at')[:10]
        )
        recent_records = ReviewLogSerializer(recent_logs, many=True).data

        return Response({
            'total_cards': total_cards,
            'today_reviewed': today_reviewed,
            'today_accuracy': today_accuracy,
            'streak_days': streak_days,
            'cards_by_box': cards_by_box,
            'recent_records': recent_records,
        })


# ─────────────────────────────────────────────────────────
# Cards — list / create
# ─────────────────────────────────────────────────────────
class CardListView(APIView):
    """List all of the current user's cards, or create a new one."""

    def get(self, request):
        cards = (
            Card.objects.filter(user=request.user)
            .select_related('category')
            .order_by('-created_at')
        )
        serializer = CardSerializer(cards, many=True, context=_card_context(request))
        return Response(serializer.data)

    def post(self, request):
        serializer = CardSerializer(data=request.data, context=_card_context(request))
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        card = serializer.save(user=request.user)
        return Response(
            CardSerializer(card, context=_card_context(request)).data,
            status=status.HTTP_201_CREATED,
        )


# ─────────────────────────────────────────────────────────
# Cards — retrieve / update / delete
# ─────────────────────────────────────────────────────────
class CardDetailView(APIView):
    """Retrieve, partially update or delete a single card."""

    def _get_object(self, request, pk):
        return get_object_or_404(Card, pk=pk, user=request.user)

    def get(self, request, pk):
        card = self._get_object(request, pk)
        return Response(CardSerializer(card, context=_card_context(request)).data)

    def patch(self, request, pk):
        card = self._get_object(request, pk)
        serializer = CardSerializer(
            card, data=request.data, partial=True, context=_card_context(request)
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        card = serializer.save()
        return Response(CardSerializer(card, context=_card_context(request)).data)

    def delete(self, request, pk):
        card = self._get_object(request, pk)
        card.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────
# Cards — check (Leitner review action)
# ─────────────────────────────────────────────────────────
class CardCheckView(APIView):
    """
    Record a review of the given card and move it between boxes.
      result == "knew"        -> box_number = min(box + 1, 5)
      result == "didnt_know"  -> box_number = 1
    """

    def post(self, request, pk):
        card = get_object_or_404(Card, pk=pk, user=request.user)
        result = request.data.get('result')

        if result not in (ReviewLog.KNEW, ReviewLog.DIDNT_KNOW):
            return Response(
                {'error': 'result 必须是 "knew" 或 "didnt_know"'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from_box = card.box_number
        if result == ReviewLog.KNEW:
            new_box = min(from_box + 1, 5)
            feedback = '答对了！卡片进入下一个盒子 🎉'
        else:
            new_box = 1
            feedback = '没关系，卡片回到 Box 1 重新巩固 💪'

        # Atomically move the card and append the review log so a failure
        # between the two writes can't leave the box moved but unlogged.
        with transaction.atomic():
            card.box_number = new_box
            card.save(update_fields=['box_number', 'updated_at'])
            ReviewLog.objects.create(
                user=request.user,
                card=card,
                result=result,
                from_box=from_box,
                to_box=new_box,
            )

        return Response({'feedback': feedback, 'new_box': new_box})


# ─────────────────────────────────────────────────────────
# Cards — CSV import / export
# ─────────────────────────────────────────────────────────
# Hard limit on uploaded CSV size (5 MB). Belt-and-suspenders alongside
# DATA_UPLOAD_MAX_MEMORY_SIZE in settings.
_MAX_IMPORT_BYTES = 5 * 1024 * 1024


class CardImportView(APIView):
    """
    Import cards from an uploaded CSV file (FormData field name "file").
    Expected columns: question, answer, category (optional), box (optional).

    The whole import runs in a single transaction so a failure mid-file
    leaves no partial cards behind.
    """

    def post(self, request):
        uploaded = request.FILES.get('file')
        if uploaded is None:
            return Response(
                {'error': '未提供文件，请选择一个 CSV 文件'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if uploaded.size > _MAX_IMPORT_BYTES:
            return Response(
                {'error': f'文件过大（{uploaded.size} 字节），上限为 5MB'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Read the uploaded bytes ONCE, then try decoders in order. Reading
        # twice would consume the file pointer and silently return b''.
        raw_bytes = uploaded.read()
        try:
            raw = raw_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                raw = raw_bytes.decode('gbk')
            except UnicodeDecodeError:
                return Response(
                    {'error': '文件编码不支持，请使用 UTF-8 编码的 CSV'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        reader = csv.DictReader(io.StringIO(raw))
        if reader.fieldnames is None or not reader.fieldnames:
            return Response(
                {'error': 'CSV 文件为空或缺少表头行'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if 'question' not in reader.fieldnames or 'answer' not in reader.fieldnames:
            return Response(
                {'error': 'CSV 必须包含 question 和 answer 列'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = 0
        errors = []

        # Parse everything first, then commit in one transaction. This way a
        # validation error late in the file doesn't leave early rows inserted.
        pending_rows = []
        for index, row in enumerate(reader, start=1):
            question = (row.get('question') or '').strip()
            answer = (row.get('answer') or '').strip()
            if not question or not answer:
                errors.append(f'第 {index} 行: 问题和答案不能为空')
                continue

            cat_name = (row.get('category') or '').strip()
            box_number = 1
            box_raw = (row.get('box') or '').strip()
            if box_raw:
                try:
                    box_number = int(box_raw)
                except ValueError:
                    errors.append(f'第 {index} 行: box 不是有效数字 ({box_raw})')
                    continue
                if not 1 <= box_number <= 5:
                    errors.append(f'第 {index} 行: box 必须在 1-5 之间')
                    continue

            pending_rows.append((index, question, answer, cat_name, box_number))

        with transaction.atomic():
            for index, question, answer, cat_name, box_number in pending_rows:
                category = None
                if cat_name:
                    category, _ = Category.objects.get_or_create(
                        user=request.user, name=cat_name
                    )
                Card.objects.create(
                    user=request.user,
                    question=question,
                    answer=answer,
                    box_number=box_number,
                    category=category,
                )
                created += 1

        return Response({'created': created, 'errors': errors})


class CardExportView(APIView):
    """Download all of the current user's cards as a CSV file.

    Uses csv.writer's default QUOTE_MINIMAL so fields containing commas,
    quotes or newlines are properly quoted — no manual escaping needed.
    Adds a UTF-8 BOM so Excel opens the file with the correct encoding.
    """

    def get(self, request):
        cards = (
            Card.objects.filter(user=request.user)
            .select_related('category')
            .order_by('-created_at')
        )
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="flashcards.csv"'
        # Prepend UTF-8 BOM so Excel auto-detects the encoding.
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['question', 'answer', 'category', 'box'])
        for card in cards:
            writer.writerow([
                card.question,
                card.answer,
                card.category.name if card.category else '',
                card.box_number,
            ])
        return response


# ─────────────────────────────────────────────────────────
# Categories
# ─────────────────────────────────────────────────────────
class CategoryListView(APIView):
    """List the current user's categories with per-category card counts.
    Also supports creating a new category via POST."""

    def get(self, request):
        categories = Category.objects.filter(user=request.user).order_by('name')
        serializer = CategorySerializer(
            categories, many=True, context={'request': request}
        )
        return Response(serializer.data)

    def post(self, request):
        name = (request.data.get('name') or '').strip()
        if not name:
            return Response(
                {'error': '分类名称不能为空'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(name) > 100:
            return Response(
                {'error': '分类名称不能超过100个字符'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        category, created = Category.objects.get_or_create(
            user=request.user, name=name
        )
        if not created:
            return Response(
                {'error': '该分类已存在'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = CategorySerializer(category, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CategoryDetailView(APIView):
    """Rename or delete a single category owned by the current user.

    Deleting a category sets the cards' ``category`` to NULL (SET_NULL on the
    model), so the cards themselves are preserved.
    """

    def _get_object(self, request, pk):
        return get_object_or_404(Category, pk=pk, user=request.user)

    def patch(self, request, pk):
        category = self._get_object(request, pk)
        name = (request.data.get('name') or '').strip()
        if not name:
            return Response(
                {'error': '分类名称不能为空'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(name) > 100:
            return Response(
                {'error': '分类名称不能超过100个字符'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Check duplicates against the user's other categories.
        exists = (
            Category.objects.filter(user=request.user, name__iexact=name)
            .exclude(pk=category.pk)
            .exists()
        )
        if exists:
            return Response(
                {'error': '该分类名已存在'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        category.name = name
        category.save(update_fields=['name'])
        serializer = CategorySerializer(category, context={'request': request})
        return Response(serializer.data)

    def delete(self, request, pk):
        category = self._get_object(request, pk)
        category.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────
# Boxes — random card for review
# ─────────────────────────────────────────────────────────
class BoxCheckView(APIView):
    """Return a random card from the given Leitner box (1..5).

    Uses count() + random offset instead of order_by('?') because the latter
    performs a full table shuffle on SQLite/Postgres and degrades quickly as
    a user accumulates cards.
    """

    def get(self, request, box_num):
        if not 1 <= box_num <= 5:
            return Response(
                {'message': '盒子编号必须在 1-5 之间'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = Card.objects.filter(user=request.user, box_number=box_num)
        count = qs.count()
        if count == 0:
            return Response(
                {'message': f'Box {box_num} 中暂时没有卡片，先去添加一些吧 ✨'}
            )

        # Pick a random offset and fetch that single row.
        offset = random.randrange(count)
        card = qs[offset:offset + 1].first()
        if card is None:  # pragma: no cover - race with deletion
            return Response(
                {'message': f'Box {box_num} 中暂时没有卡片，先去添加一些吧 ✨'}
            )

        return Response(CardSerializer(card, context=_card_context(request)).data)


# ─────────────────────────────────────────────────────────
# Stats — daily
# ─────────────────────────────────────────────────────────
class DailyStatsView(APIView):
    """
    Return per-day review counts and accuracy for the last 30 days.
    The frontend renders a 28-day heatmap and a 14-day bar chart from this.
    Entries are ordered most-recent-first so the frontend can slice(0, 14).
    """

    def get(self, request):
        today = timezone.localdate()
        start = today - timedelta(days=29)  # 30 days including today

        daily = (
            ReviewLog.objects.filter(
                user=request.user,
                reviewed_at__date__gte=start,
                reviewed_at__date__lte=today,
            )
            .values('reviewed_at__date')
            .annotate(
                total=Count('id'),
                knew=Count('id', filter=Q(result=ReviewLog.KNEW)),
            )
        )
        day_map = {entry['reviewed_at__date']: entry for entry in daily}

        result = []
        for offset in range(30):
            day = start + timedelta(days=offset)
            entry = day_map.get(day)
            if entry:
                total = entry['total']
                knew = entry['knew']
                accuracy = round(knew / total * 100) if total > 0 else 0
            else:
                total = 0
                accuracy = 0
            result.append({
                'date': day.isoformat(),
                'cards_reviewed': total,
                'accuracy': accuracy,
            })

        result.reverse()  # most recent first
        return Response(result)
