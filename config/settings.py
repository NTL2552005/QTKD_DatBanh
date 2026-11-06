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
N8N_REVIEW_WEBHOOK_URL = os.getenv("N8N_REVIEW_WEBHOOK_URL", "").strip()
N8N_REVIEW_WEBHOOK_KEY = os.getenv("N8N_REVIEW_WEBHOOK_KEY", "")
N8N_REVIEW_CALLBACK_KEY = os.getenv("N8N_REVIEW_CALLBACK_KEY", "")
N8N_REVIEW_TIMEOUT_SECONDS = float(
    os.getenv("N8N_REVIEW_TIMEOUT_SECONDS", "5")
)

if not 0 < WORKFLOW_TIMEOUT_SECONDS <= 30:
    raise ValueError("WORKFLOW_TIMEOUT_SECONDS must be between 0 and 30 seconds.")
if not 0 < N8N_REVIEW_TIMEOUT_SECONDS <= 30:
    raise ValueError("N8N_REVIEW_TIMEOUT_SECONDS must be between 0 and 30 seconds.")

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
