"""
URL routes for the cards app.

Mounted under ``api/`` by the project-level URLconf, so the patterns below
map to the ``/api/...`` endpoints the frontend expects.
"""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    BoxCheckView,
    CardCheckView,
    CardDetailView,
    CardExportView,
    CardImportView,
    CardListView,
    CategoryListView,
    DailyStatsView,
    DashboardView,
    LoginView,
    RegisterView,
)

urlpatterns = [
    # Auth (public)
    path('auth/login/', LoginView.as_view(), name='auth-login'),
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='auth-refresh'),

    # Dashboard
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # Cards
    path('cards/', CardListView.as_view(), name='card-list'),
    path('cards/import/', CardImportView.as_view(), name='card-import'),
    path('cards/export/', CardExportView.as_view(), name='card-export'),
    path('cards/<int:pk>/', CardDetailView.as_view(), name='card-detail'),
    path('cards/<int:pk>/check/', CardCheckView.as_view(), name='card-check'),

    # Categories
    path('categories/', CategoryListView.as_view(), name='category-list'),

    # Boxes
    path('boxes/<int:box_num>/check/', BoxCheckView.as_view(), name='box-check'),

    # Stats
    path('stats/daily/', DailyStatsView.as_view(), name='stats-daily'),
]
