"""
Admin configuration for the cards app.

Registers the three core models with sensible list displays and filters so
they can be inspected and managed through the Django admin at /admin/.
"""

from django.contrib import admin

from .models import Card, Category, ReviewLog


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'user', 'created_at')
    list_filter = ('user',)
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'question', 'box_number', 'category', 'created_at', 'updated_at')
    list_filter = ('user', 'box_number', 'category')
    search_fields = ('question', 'answer')
    ordering = ('-created_at',)
    list_editable = ('box_number',)


@admin.register(ReviewLog)
class ReviewLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'card', 'result', 'from_box', 'to_box', 'reviewed_at')
    list_filter = ('user', 'result', 'from_box', 'to_box')
    search_fields = ('card__question',)
    ordering = ('-reviewed_at',)
    readonly_fields = ('reviewed_at',)
