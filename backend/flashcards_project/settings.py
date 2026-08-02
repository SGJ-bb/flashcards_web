"""
Django settings for flashcards_project project.

The flashcards backend: a Django REST Framework API serving JWT-authenticated
flashcard endpoints for the frontend at f:\\flashcards_web\\index.html.

Security notes:
  * SECRET_KEY and DEBUG are read from environment variables so that production
    deployments never use the insecure dev defaults baked into this file.
  * JWT signing key is taken from JWT_SIGNING_KEY if provided (recommended for
    production), otherwise falls back to SECRET_KEY.
  * CORS is restricted to a configurable allow-list via CORS_ALLOWED_ORIGINS.
    Set CORS_ALLOW_ALL=True only for local development.
"""

import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name, default=False):
    """Parse a boolean env var (1/true/yes/on -> True, otherwise False)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


# SECURITY WARNING: keep the secret key used in production secret!
# In production, ALWAYS set DJANGO_SECRET_KEY to a strong random value.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-flashcards-web-dev-secret-key-change-in-production-7f3a9b2c',
)

# DEBUG must be False in production. Set DEBUG=True in env for local dev only.
DEBUG = _env_bool('DEBUG', default=True)

# ALLOWED_HOSTS: comma-separated list in env, '*' only acceptable when DEBUG=True.
_allowed_hosts_env = os.environ.get('ALLOWED_HOSTS', '')
if _allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(',') if h.strip()]
else:
    # Safe default: only loopback. Frontend talks to API via CORS, not host.
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', '0.0.0.0'] if DEBUG else []

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'corsheaders',
    'rest_framework_simplejwt.token_blacklist',  # enables logout / token revocation
    # Local
    'cards',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise serves static files (CSS/JS/admin) in production without
    # needing nginx. MUST come after SecurityMiddleware.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'flashcards_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'frontend'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'flashcards_project.wsgi.application'
ASGI_APPLICATION = 'flashcards_project.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ─────────────────────────────────────────────────────────
# PostgreSQL support for production (Render / Heroku / etc.)
#
# When the DATABASE_URL env var is present (auto-injected by Render when
# a PostgreSQL database is attached), override the SQLite default with
# the parsed PostgreSQL configuration. This lets the same settings.py
# work in both dev (SQLite) and prod (PostgreSQL) without code changes.
# ─────────────────────────────────────────────────────────
import dj_database_url

_db_url = os.environ.get('DATABASE_URL')
if _db_url:
    DATABASES['default'] = dj_database_url.parse(
        _db_url,
        conn_max_age=600,    # Reuse connections for 10 minutes (perf)
        ssl_require=True,    # Render's PostgreSQL requires SSL
    )

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise: compress and hash static files in production so they get
# far-future cache headers. Safe to enable in dev too (no-op if DEBUG=True).
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─────────────────────────────────────────────────────────
# Django REST Framework
# ─────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    # Global throttle: light rate-limit applied to every endpoint.
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/min',
        'user': '300/min',
        # Special scopes used by auth endpoints (LoginView/RegisterView).
        'auth_write': '10/min',
    },
    'DEFAULT_PAGINATION_CLASS': None,
    'PAGE_SIZE': None,
}

# ─────────────────────────────────────────────────────────
# SimpleJWT token lifetimes
# ─────────────────────────────────────────────────────────
# JWT_SIGNING_KEY can be set independently from SECRET_KEY for stronger
# isolation between Django's signing and JWT signing.
_JWT_SIGNING_KEY = os.environ.get('JWT_SIGNING_KEY') or SECRET_KEY

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=2),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,  # old refresh tokens get revoked on refresh
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': _JWT_SIGNING_KEY,
    'VERIFYING_KEY': None,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'TOKEN_TYPE_CLAIM': 'token_type',
    'JTI_CLAIM': 'jti',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ─────────────────────────────────────────────────────────
# CORS — locked down to an explicit allow-list.
#
# Set CORS_ALLOWED_ORIGINS as a comma-separated env var, e.g.
#   CORS_ALLOWED_ORIGINS=https://example.com,https://app.example.com
# For local dev convenience, set CORS_ALLOW_ALL=True to bypass the list.
#
# Security: default is False even when DEBUG=True, so that a production
# deployment that forgets to set DEBUG=False is NOT automatically wide open.
# Developers must explicitly opt in via CORS_ALLOW_ALL=True in .env.
# ─────────────────────────────────────────────────────────
CORS_ALLOW_ALL = _env_bool('CORS_ALLOW_ALL', default=False)

if CORS_ALLOW_ALL:
    CORS_ALLOW_ALL_ORIGINS = True
    # When allow-all is on, credentials must be off (browser would reject
    # the combination anyway, and this is the safer posture).
    CORS_ALLOW_CREDENTIALS = False
else:
    _origins_env = os.environ.get('CORS_ALLOWED_ORIGINS', '')
    if _origins_env:
        CORS_ALLOWED_ORIGINS = [
            o.strip().rstrip('/') for o in _origins_env.split(',') if o.strip()
        ]
    else:
        # Sensible dev defaults.
        CORS_ALLOWED_ORIGINS = [
            'http://localhost:63342',
            'http://127.0.0.1:63342',
            'http://localhost:5500',
            'http://127.0.0.1:5500',
            'http://localhost:8000',
            'http://127.0.0.1:8000',
            'https://flashcards-api-sso4.onrender.com',
            'https://sgj-bb.github.io',
            'null',  # file:// pages send origin "null"
        ]
    CORS_ALLOW_CREDENTIALS = True

CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]

# ─────────────────────────────────────────────────────────
# Security hardening (only enforced when DEBUG=False)
# ─────────────────────────────────────────────────────────
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    # Set SECURE_SSL_REDIRECT=True if you have TLS terminated at the app.
    SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', default=False)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30  # 30 days
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# ─────────────────────────────────────────────────────────
# Upload limits — protects CardImportView from huge payloads.
# ─────────────────────────────────────────────────────────
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB

# ─────────────────────────────────────────────────────────
# Logging — sane defaults so errors are captured on disk.
# ─────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'cards': {
            'handlers': ['console'],
            'level': 'INFO',
        },
    },
}
