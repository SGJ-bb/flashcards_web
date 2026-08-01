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
    CategoryDetailView,
    CategoryListView,
    ChangePasswordView,
    DailyStatsView,
    DashboardView,
    LoginView,
    LogoutView,
    MeView,
    RegisterView,
)

urlpatterns = [
    # Auth (public)
    path('auth/login/', LoginView.as_view(), name='auth-login'),
    path('auth/register/', RegisterView.as_view(), name='auth-register'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='auth-refresh'),
    # Auth (authenticated)
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('auth/me/', MeView.as_view(), name='auth-me'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='auth-change-password'),

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
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    # Boxes
    path('boxes/<int:box_num>/check/', BoxCheckView.as_view(), name='box-check'),

    # Stats
    path('stats/daily/', DailyStatsView.as_view(), name='stats-daily'),
]
