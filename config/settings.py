"""
Configurazione Django per l'applicazione di gestione del welfare aziendale.

Tutta la configurazione sensibile viene letta da variabili d'ambiente
(vedi .env.example).
"""

import sys
import warnings
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


DEBUG = env_bool("DEBUG", False)

# True quando è in corso `manage.py test`: alcune impostazioni di produzione
# (redirect HTTPS, manifest dei file statici) renderebbero impossibile
# l'esecuzione dei test senza un collectstatic preventivo.
RUNNING_TESTS = "test" in sys.argv[:2]

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-solo-per-sviluppo-locale"
    else:
        raise ImproperlyConfigured(
            "SECRET_KEY deve essere impostata come variabile d'ambiente in produzione."
        )

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")

# Railway espone il dominio pubblico del servizio in questa variabile.
RAILWAY_PUBLIC_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
if RAILWAY_PUBLIC_DOMAIN and RAILWAY_PUBLIC_DOMAIN not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RAILWAY_PUBLIC_DOMAIN)

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
for host in ALLOWED_HOSTS:
    if host not in {"*", "localhost", "127.0.0.1", "testserver"}:
        origin = f"https://{host}"
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "welfare",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "welfare.context_processors.welfare_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    # Nessun errore immediato: comandi che non toccano il database
    # (es. collectstatic in fase di build) devono poter funzionare.
    warnings.warn(
        "DATABASE_URL non impostata: verrà usato un placeholder locale. "
        "Impostala per connetterti a PostgreSQL.",
        RuntimeWarning,
    )
    DATABASE_URL = "postgres://welfare:welfare@localhost:5432/welfare"

DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "600")),
        conn_health_checks=True,
        ssl_require=env_bool("DB_SSL_REQUIRE", False),
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internazionalizzazione -------------------------------------------------
LANGUAGE_CODE = "it-it"
TIME_ZONE = os.environ.get("TIME_ZONE", "Europe/Rome")
USE_I18N = True
USE_TZ = True

# --- File statici -----------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# --- Allegati (Cloudflare R2, bucket privato) -------------------------------
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "")
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "")

USE_R2 = all([R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT_URL])

if USE_R2:
    default_storage_config = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": R2_ACCESS_KEY_ID,
            "secret_key": R2_SECRET_ACCESS_KEY,
            "bucket_name": R2_BUCKET_NAME,
            "endpoint_url": R2_ENDPOINT_URL,
            "region_name": os.environ.get("R2_REGION_NAME", "auto"),
            # Il bucket è privato: nessun oggetto pubblico, nessuna URL permanente.
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": int(os.environ.get("R2_URL_EXPIRE_SECONDS", "300")),
            "file_overwrite": False,
            "signature_version": "s3v4",
            "addressing_style": "virtual",
        },
    }
else:
    default_storage_config = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": str(BASE_DIR / "media"),
            # Nessuna base_url: gli allegati non sono mai serviti direttamente.
        },
    }

STORAGES = {
    "default": default_storage_config,
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or RUNNING_TESTS
            else "welfare.storage.ForgivingManifestStaticFilesStorage"
        ),
    },
}

MEDIA_ROOT = BASE_DIR / "media"

# Dimensione massima e tipi ammessi per gli allegati.
ATTACHMENT_MAX_SIZE_MB = int(os.environ.get("ATTACHMENT_MAX_SIZE_MB", "10"))
ATTACHMENT_ALLOWED_EXTENSIONS = env_list(
    "ATTACHMENT_ALLOWED_EXTENSIONS",
    "pdf,png,jpg,jpeg,gif,webp,doc,docx,xls,xlsx,odt,ods,txt,csv",
)

# --- Notifiche via email ----------------------------------------------------
# Unico destinatario delle notifiche amministrative.
WELFARE_NOTIFICATION_EMAIL = os.environ.get(
    "WELFARE_NOTIFICATION_EMAIL", "agevolazioni@studiobirardi.it"
)
# URL pubblica dell'applicazione, usata per i link dentro le email.
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "").rstrip("/")
if not SITE_BASE_URL and RAILWAY_PUBLIC_DOMAIN:
    SITE_BASE_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}"

EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "welfare@localhost"
)
EMAIL_SUBJECT_PREFIX = os.environ.get("EMAIL_SUBJECT_PREFIX", "[Welfare] ")

if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
elif DEBUG:
    # In sviluppo le email finiscono a console: nessun invio reale.
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    # Senza SMTP configurato le notifiche restano inerti (viene loggato un avviso).
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

EMAIL_CONFIGURED = bool(EMAIL_HOST)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = (ATTACHMENT_MAX_SIZE_MB + 2) * 1024 * 1024

# --- Autenticazione ---------------------------------------------------------
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# --- Sicurezza --------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# Il redirect HTTPS e i cookie "secure" restano attivi solo in esecuzione reale.
if not DEBUG and not RUNNING_TESTS:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    # L'health check di Railway viaggia in HTTP interno: non deve essere redirezionato.
    SECURE_REDIRECT_EXEMPT = [r"^health/$"]
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
