"""
Database models for the flashcards app.

Three core models back the Leitner-style spaced-repetition system:
  * Category  — user-scoped grouping for cards
  * Card      — a single flashcard with a box_number (1..5)
  * ReviewLog — an audit trail of every review action

The ReviewLog also stores from_box / to_box so the stats UI can render
"Box X -> Box Y" transitions for each review.
"""

from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Category(models.Model):
    """A user-defined category (e.g. "English", "Maths")."""

    name = models.CharField(max_length=100)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='categories',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A user cannot have two categories with the same name, but
        # different users may reuse the same name.
        unique_together = ('user', 'name')
        ordering = ['name']

    def __str__(self):
        return self.name


class Card(models.Model):
    """A flashcard belonging to a user, placed in one of 5 Leitner boxes."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='cards',
    )
    question = models.TextField()
    answer = models.TextField()
    box_number = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cards',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Card #{self.pk}: {self.question[:40]}'


class ReviewLog(models.Model):
    """A single review event: the user checked a card as knew/didnt_know."""

    KNEW = 'knew'
    DIDNT_KNOW = 'didnt_know'
    RESULT_CHOICES = [
        (KNEW, 'knew'),
        (DIDNT_KNOW, 'didnt_know'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='review_logs',
    )
    card = models.ForeignKey(
        Card,
        on_delete=models.CASCADE,
        related_name='review_logs',
    )
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)
    from_box = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    to_box = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-reviewed_at']

    def __str__(self):
        return f'{self.user.username} -> {self.card_id} [{self.result}]'
