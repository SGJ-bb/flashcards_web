"""
Views for the flashcards app.

Every endpoint (except health, login, register and refresh) requires a valid
JWT. All querysets are scoped to ``request.user`` so users can only see or
mutate their own data.

Endpoint map (mounted under /api/ by the project urls):
  auth/login/        POST   {username, password}        -> {access, refresh, user}
  auth/register/     POST   {username, password, email?}-> {access, refresh, user}
  auth/refresh/      POST   {refresh}                   -> {access}            (simplejwt)
  dashboard/         GET                                -> dashboard aggregate
  cards/             GET/POST                           -> list / create
  cards/<id>/        GET/PATCH/DELETE                   -> retrieve / update / delete
  cards/<id>/check/  POST   {result}                    -> {feedback, new_box}
  cards/import/      POST   FormData(file=...)          -> {created, errors}
  cards/export/      GET                                -> CSV download
  categories/        GET                                -> [{id, name, card_count}]
  boxes/<n>/check/   GET                                -> random card | {message}
  stats/daily/       GET                                -> [{date, cards_reviewed, accuracy}]
"""

import csv

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
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Card, Category, ReviewLog
from .serializers import (
    CardSerializer,
    CategorySerializer,
    RegisterSerializer,
    ReviewLogSerializer,
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


# ─────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────
class HealthView(APIView):
    """Public liveness probe used by the frontend connection indicator."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'status': 'ok'})


# ─────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────
class LoginView(APIView):
    """Authenticate with username/password and return JWT tokens + user."""

    permission_classes = [AllowAny]

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
    """Create a new user and immediately issue JWT tokens."""

    permission_classes = [AllowAny]

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
class CardImportView(APIView):
    """
    Import cards from an uploaded CSV file (FormData field name "file").
    Expected columns: question, answer, category (optional), box (optional).
    """

    def post(self, request):
        uploaded = request.FILES.get('file')
        if uploaded is None:
            return Response(
                {'error': '未提供文件，请选择一个 CSV 文件'},
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

        reader = csv.DictReader(raw.splitlines())
        created = 0
        errors = []

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

        for index, row in enumerate(reader, start=1):
            question = (row.get('question') or '').strip()
            answer = (row.get('answer') or '').strip()
            if not question or not answer:
                errors.append(f'第 {index} 行: 问题和答案不能为空')
                continue

            category = None
            cat_name = (row.get('category') or '').strip()
            if cat_name:
                category, _ = Category.objects.get_or_create(
                    user=request.user, name=cat_name
                )

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
    """Download all of the current user's cards as a CSV file."""

    def get(self, request):
        cards = (
            Card.objects.filter(user=request.user)
            .select_related('category')
            .order_by('-created_at')
        )
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="flashcards.csv"'
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
    """List the current user's categories with per-category card counts."""

    def get(self, request):
        categories = Category.objects.filter(user=request.user).order_by('name')
        serializer = CategorySerializer(
            categories, many=True, context={'request': request}
        )
        return Response(serializer.data)


# ─────────────────────────────────────────────────────────
# Boxes — random card for review
# ─────────────────────────────────────────────────────────
class BoxCheckView(APIView):
    """Return a random card from the given Leitner box (1..5)."""

    def get(self, request, box_num):
        if not 1 <= box_num <= 5:
            return Response(
                {'message': '盒子编号必须在 1-5 之间'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        card = (
            Card.objects.filter(user=request.user, box_number=box_num)
            .order_by('?')
            .first()
        )
        if card is None:
            return Response({'message': f'Box {box_num} 中暂时没有卡片，先去添加一些吧 ✨'})

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
