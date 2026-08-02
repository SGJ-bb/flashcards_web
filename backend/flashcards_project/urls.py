"""
URL configuration for flashcards_project.

Project-level URLs mount the cards app under ``api/`` and expose a public
health-check endpoint at ``api/health/``. The Django admin is also mounted
at ``admin/`` for convenience. The frontend SPA is served at ``/``.
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

from cards.views import HealthView

urlpatterns = [
    # Django admin
    path('admin/', admin.site.urls),
    # All app endpoints (auth, cards, categories, boxes, stats, dashboard)
    path('api/', include('cards.urls')),
    # Public health check (no auth required)
    path('api/health/', HealthView.as_view(), name='health'),
    # Frontend SPA — served from backend/templates/index.html
    path('', TemplateView.as_view(template_name='index.html'), name='home'),
]
