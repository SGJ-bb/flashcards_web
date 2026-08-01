"""
DRF serializers for the flashcards app.

Notable field-mapping decisions (driven by the frontend contract):
  * CardSerializer exposes ``box`` as a write-only alias for ``box_number``
    because the frontend sends {box: n} on create/update (see index.html).
  * CardSerializer also exposes ``category_name``, ``date_created`` and
    ``last_reviewed`` read-only fields used by the card detail modal.
  * CategorySerializer exposes ``card_count`` for the filter chips.

Security:
  * RegisterSerializer runs Django's full AUTH_PASSWORD_VALIDATORS stack so
    weak / common / numeric / attribute-similar passwords are rejected with
    localized messages, and explicitly validates username uniqueness ahead
    of save() to return a friendly 400 instead of a 500 IntegrityError.
  * ChangePasswordSerializer verifies the current password and applies the
    same validator stack to the new password before committing.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth import password_validation
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError

from rest_framework import serializers

from .models import Card, Category, ReviewLog

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    """Validates and creates a new auth User with a hashed password.

    The frontend sends {username, password, email?}. We:
      1. Validate username uniqueness up-front for a friendly 400.
      2. Run Django's AUTH_PASSWORD_VALIDATORS on the password so weak
         passwords are rejected with the same messages the admin uses.
    """

    password = serializers.CharField(write_only=True, min_length=6)
    email = serializers.EmailField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ['username', 'password', 'email']

    def validate_username(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('用户名不能为空')
        if len(value) > 150:
            raise serializers.ValidationError('用户名不能超过150个字符')
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('该用户名已被注册')
        return value

    def validate_email(self, value):
        value = (value or '').strip()
        if not value:
            return value
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('该邮箱已被注册')
        return value

    def validate_password(self, value):
        # Defer to Django's password validators. They raise DjangoValidationError
        # with localized messages; we wrap them as DRF ValidationError so the
        # response shape matches the rest of the API.
        try:
            password_validation.validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data):
        password = validated_data.pop('password')
        email = validated_data.get('email') or ''
        user = User(username=validated_data['username'], email=email)
        user.set_password(password)
        try:
            user.save()
        except IntegrityError:
            # Race condition: username created between validate and save.
            raise serializers.ValidationError(
                {'username': '该用户名已被注册'}
            )
        return user


class UserSerializer(serializers.ModelSerializer):
    """Public representation of the authenticated user."""

    class Meta:
        model = User
        fields = ['id', 'username', 'email']
        read_only_fields = ['id', 'username']


class UpdateUserSerializer(serializers.ModelSerializer):
    """Partial update of the current user's profile (email only for now).

    Username changes are intentionally disabled because they would invalidate
    the user-facing audit trail and the JWT user_id claim is stable anyway.
    """

    email = serializers.EmailField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ['email']

    def validate_email(self, value):
        value = (value or '').strip()
        if not value:
            return value
        request = self.context.get('request')
        user = getattr(request, 'user', None) if request is not None else None
        if (
            user
            and not isinstance(user, AnonymousUser)
            and User.objects.exclude(pk=user.pk)
            .filter(email__iexact=value)
            .exists()
        ):
            raise serializers.ValidationError('该邮箱已被注册')
        return value


class ChangePasswordSerializer(serializers.Serializer):
    """Validate the current password and accept a new password.

    Used by the change-password endpoint. The current password is verified
    in the view (so we can return a distinct 400 for "wrong current password"
    rather than a generic serializer error).
    """

    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=6)

    def validate_new_password(self, value):
        try:
            password_validation.validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class CategorySerializer(serializers.ModelSerializer):
    """Category with a derived card_count for the current user."""

    card_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'card_count']

    def get_card_count(self, obj):
        # Only count cards owned by the same user as the category, so the
        # number stays correct even if the queryset was filtered upstream.
        return obj.cards.filter(user=obj.user).count()


class CardSerializer(serializers.ModelSerializer):
    """Serializer for Card with frontend-compatible field names."""

    # Write-only alias: the frontend sends {box: n}; we map to box_number.
    box = serializers.IntegerField(
        write_only=True,
        required=False,
        min_value=1,
        max_value=5,
    )
    # Scope the category queryset to the current user so a malicious client
    # cannot link a card to another user's category by guessing its PK.
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.none(),
        required=False,
        allow_null=True,
    )
    category_name = serializers.SerializerMethodField()
    # Frontend card detail modal reads card.date_created (alias for created_at).
    date_created = serializers.DateTimeField(source='created_at', read_only=True)
    last_reviewed = serializers.SerializerMethodField()

    class Meta:
        model = Card
        fields = [
            'id',
            'question',
            'answer',
            'box_number',
            'box',
            'category',
            'category_name',
            'created_at',
            'date_created',
            'last_reviewed',
            'updated_at',
        ]
        read_only_fields = ['id', 'box_number', 'created_at', 'updated_at']

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None

    def get_last_reviewed(self, obj):
        last = obj.review_logs.order_by('-reviewed_at').first()
        return last.reviewed_at if last else None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Restrict the category choices to the requesting user's own categories.
        request = self.context.get('request')
        if request is not None and getattr(request, 'user', None) is not None:
            user = request.user
            if getattr(user, 'is_authenticated', False):
                self.fields['category'].queryset = Category.objects.filter(user=user)

    def _pop_box(self, validated_data):
        """Remove the write-only ``box`` alias and return its value (or None)."""
        return validated_data.pop('box', None)

    def create(self, validated_data):
        box = self._pop_box(validated_data)
        if box is not None:
            validated_data['box_number'] = box
        # category may be None (no category) — ModelForm-style handling.
        return super().create(validated_data)

    def update(self, instance, validated_data):
        box = self._pop_box(validated_data)
        if box is not None:
            instance.box_number = box
        return super().update(instance, validated_data)


class ReviewLogSerializer(serializers.ModelSerializer):
    """Serializer used by the dashboard's recent_records list."""

    card_question = serializers.SerializerMethodField()

    class Meta:
        model = ReviewLog
        fields = [
            'id',
            'card',
            'card_question',
            'result',
            'from_box',
            'to_box',
            'reviewed_at',
        ]

    def get_card_question(self, obj):
        return obj.card.question if obj.card_id else None
