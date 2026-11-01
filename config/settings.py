import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "catalog",
    "orders",
    "assistant",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "vi"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
ORDER_REVIEW_WINDOW_HOURS = 24
WORKFLOW_ENABLED = os.getenv("WORKFLOW_ENABLED", "False").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
WORKFLOW_WEBHOOK_URL = os.getenv("WORKFLOW_WEBHOOK_URL", "").strip()
WORKFLOW_WEBHOOK_TOKEN = os.getenv("WORKFLOW_WEBHOOK_TOKEN", "")
WORKFLOW_TIMEOUT_SECONDS = float(os.getenv("WORKFLOW_TIMEOUT_SECONDS", "5"))
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
AI_ENABLED = os.getenv("AI_ENABLED", "False").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").strip().lower()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = (
    os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    or "gemini-3.8-flash"
)
AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "10"))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "1"))
AI_MAX_OUTPUT_TOKENS = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "800"))
AI_MIN_CONFIDENCE = float(os.getenv("AI_MIN_CONFIDENCE", "0.55"))
AI_USER_REQUESTS_PER_MINUTE = int(os.getenv("AI_USER_REQUESTS_PER_MINUTE", "6"))
AI_GLOBAL_REQUESTS_PER_MINUTE = int(os.getenv("AI_GLOBAL_REQUESTS_PER_MINUTE", "60"))

if (
    not math.isfinite(WORKFLOW_TIMEOUT_SECONDS)
    or WORKFLOW_TIMEOUT_SECONDS <= 0
    or WORKFLOW_TIMEOUT_SECONDS > 30
):
    raise ValueError("WORKFLOW_TIMEOUT_SECONDS must be between 0 and 30 seconds.")
if not math.isfinite(AI_TIMEOUT_SECONDS) or not 1 <= AI_TIMEOUT_SECONDS <= 30:
    raise ValueError("AI_TIMEOUT_SECONDS must be between 1 and 30 seconds.")
if not 0 <= AI_MAX_RETRIES <= 2:
    raise ValueError("AI_MAX_RETRIES must be between 0 and 2.")
if not 100 <= AI_MAX_OUTPUT_TOKENS <= 4000:
    raise ValueError("AI_MAX_OUTPUT_TOKENS must be between 100 and 4000.")
if not math.isfinite(AI_MIN_CONFIDENCE) or not 0 <= AI_MIN_CONFIDENCE <= 1:
    raise ValueError("AI_MIN_CONFIDENCE must be between 0 and 1.")
if AI_USER_REQUESTS_PER_MINUTE < 1 or AI_GLOBAL_REQUESTS_PER_MINUTE < 1:
    raise ValueError("AI rate limits must be positive integers.")

_site_url = urlsplit(SITE_BASE_URL)
if (
    not _site_url.scheme
    or not _site_url.netloc
    or _site_url.username
    or _site_url.password
    or _site_url.query
    or _site_url.fragment
):
    raise ValueError("SITE_BASE_URL must be an absolute URL without credentials.")
if _site_url.scheme != "https" and not (
    _site_url.scheme == "http"
    and _site_url.hostname in {"localhost", "127.0.0.1", "::1"}
):
    raise ValueError("SITE_BASE_URL must use HTTPS except for a local address.")
