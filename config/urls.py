"""
Root URL configuration.

App-level URLConfs are namespaced and included here. The Django admin is kept
mounted for debugging only (the user-facing UI is built with custom views per
the project spec).
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),  # debug/maintenance only
    path("accounts/", include("apps.accounts.urls")),
    path("pocs/", include("apps.pocs.urls")),
    path("reports/", include("apps.reports.urls")),
    path("ai/", include("apps.ai.urls")),
    path("", include("apps.core.urls")),
]

# Serve user-uploaded media via Django during development only.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
