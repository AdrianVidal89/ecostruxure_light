"""
Production settings.

Hardened defaults for deployment. ``SECRET_KEY``, ``ALLOWED_HOSTS`` and a
PostgreSQL ``DATABASE_URL`` MUST be provided via the environment — there are no
insecure fallbacks here.

Static files are served by WhiteNoise so the app can run behind a single
gunicorn process without a separate static file server.
"""

from .base import *  # noqa: F401,F403
from .base import env, MIDDLEWARE

DEBUG = False

# Fail loudly if these are not configured in the environment.
SECRET_KEY = env("SECRET_KEY")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# PostgreSQL is required in production. django-environ parses DATABASE_URL,
# e.g. postgres://USER:PASSWORD@HOST:5432/DBNAME
DATABASES = {"default": env.db("DATABASE_URL")}

# Persistent connections: keep a DB connection open and reuse it across requests
# instead of reconnecting every time. This is the single biggest DB win under
# concurrent users (dozens of users → much less connection churn). Django pings
# the connection before reuse so stale ones are recycled safely.
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# --- Static files via WhiteNoise -------------------------------------------
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
# CompressedStaticFilesStorage (not the *Manifest* variant): WhiteNoise still
# gzip/brotli-compresses assets, but it does NOT hash filenames nor strictly
# validate every URL referenced inside CSS. The vendored Font Awesome 4.7 CSS
# references .eot/.ttf/.svg fallbacks that we don't ship (modern browsers only
# need the .woff2 we do ship), which would make the Manifest storage fail during
# collectstatic. This avoids that without affecting how fonts render.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"
    },
}

# --- Security ---------------------------------------------------------------
# These TLS-dependent protections default to OFF so the app works behind a
# plain-HTTP reverse proxy (e.g. an internal IP without a certificate yet).
# When you put a TLS certificate in front, flip them on in the environment:
#   SECURE_SSL_REDIRECT=True
#   SESSION_COOKIE_SECURE=True
#   CSRF_COOKIE_SECURE=True
#   SECURE_HSTS_SECONDS=2592000
# (Leaving secure cookies on over plain HTTP breaks login: the browser won't
# send the cookies and SECURE_SSL_REDIRECT would loop to a non-existent https.)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=False)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=False)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# --- Logging ----------------------------------------------------------------
# Django's defaults send request errors only to "mail_admins" (unconfigured here),
# so 500 tracebacks would vanish. Send everything to stderr → systemd journal
# (view with: sudo journalctl -u ecostruxure-light -f).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # Unhandled view exceptions (500s) with full traceback.
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
