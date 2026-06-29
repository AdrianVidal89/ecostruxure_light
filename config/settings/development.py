"""
Development settings.

Optimised for local work and the per-step review workflow:

* ``DEBUG`` defaults to True.
* The database defaults to a local SQLite file, so the server runs with no
  external dependencies. To test against PostgreSQL locally instead, set
  ``DATABASE_URL`` in your ``.env`` and it will be picked up automatically.
"""

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env("DEBUG", default=True)

ALLOWED_HOSTS = env("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0"])

# Surface emails in the console during development.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
