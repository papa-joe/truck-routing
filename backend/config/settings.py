"""Django settings for the weather-aware truck routing API.

The application is stateless: every result is derived from the request and
returned. There is therefore no database. Django's session, auth, admin and
contenttypes apps are omitted along with the middleware they require, so
``DATABASES`` is intentionally empty and ``migrate`` has nothing to do.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BASE_DIR.parent

load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = _env("DJANGO_SECRET_KEY", "dev-only-insecure-key-do-not-deploy")
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = _env_list(
    "DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1", "testserver", ".onrender.com"]
)

# Stateless by design: no database, no migrations, no sessions.
DATABASES: dict[str, dict[str, str]] = {}

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.gzip.GZipMiddleware",
]

# No template engine: the API only emits JSON and the SPA is served as a
# static file.
TEMPLATES: list[dict[str, object]] = []

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# --- Static files: the built React app -------------------------------------
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_URL = "/static/"
STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.exists() else []

# Serve the SPA from the container root so `/` and `/assets/*` resolve.
WHITENOISE_ROOT = STATIC_ROOT
WHITENOISE_INDEX_FILE = True

# --- DRF -------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "api.exceptions.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# --- Providers -------------------------------------------------------------
ORS_API_KEY = _env("ORS_API_KEY")
ORS_BASE_URL = _env("ORS_BASE_URL", "https://api.openrouteservice.org")
PHOTON_BASE_URL = _env("PHOTON_BASE_URL", "https://photon.komoot.io")
OPEN_METEO_BASE_URL = _env("OPEN_METEO_BASE_URL", "https://api.open-meteo.com")
PROVIDER_TIMEOUT_SECONDS = float(_env("PROVIDER_TIMEOUT_SECONDS", "30"))

# --- Caching ---------------------------------------------------------------
# Protects the ORS daily quota (200 requests/day on the free key) and avoids
# refetching weather for repeated views of the same corridor.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": _env("DJANGO_CACHE_DIR", str(BASE_DIR / ".cache")),
        "TIMEOUT": 1800,
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}
PLAN_CACHE_SECONDS = int(_env("PLAN_CACHE_SECONDS", "900"))
WEATHER_CACHE_SECONDS = int(_env("WEATHER_CACHE_SECONDS", "1800"))
GEOCODE_CACHE_SECONDS = int(_env("GEOCODE_CACHE_SECONDS", "86400"))

# --- Hard limits -----------------------------------------------------------
MAX_ROUTE_MILES = 3500
MAX_CHECKPOINTS_PER_ROUTE = 400
HEATMAP_HOURS = 49
HEATMAP_SAMPLE_MILES = 6
HEATMAP_LATERAL_OFFSETS_MILES = (0, 10, -10)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": _env("LOG_LEVEL", "INFO")},
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
