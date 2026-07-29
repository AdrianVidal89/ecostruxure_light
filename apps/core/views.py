"""
Core views.

The dashboard is the post-login landing page: a grid of the POCs the user
belongs to (admins see all), plus a global stats row for admins.
"""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.generic import TemplateView, View

from . import db_backup
from .forms import BrandingForm, DatabaseRestoreForm
from .mixins import AdminRequiredMixin
from .models import SiteBranding

logger = logging.getLogger(__name__)


# Imported business fields offered as fine-grain dropdown filters (admin only).
# Each tuple is (POC field name, GET param / label key, human label).
FINE_GRAIN_FILTERS = (
    ("customer_segment", "Customer Segment"),
    ("leading_organization", "Leading Organization"),
    ("l2_wbs", "L2 WBS"),
    ("investment_type", "Investment Type"),
    ("leadership", "Leadership"),
    ("pilot_requestor", "Pilot Requestor"),
    ("ecostruxure_lead", "EcoStruxure Lead"),
    ("integration_leader", "Integration Leader"),
)


class DashboardView(LoginRequiredMixin, TemplateView):
    """Unified landing page (merges the old Dashboard + POCs list).

    Shows admin stats widgets, the full filter bar (search, assigned member and
    status as main filters, plus a fine-grain row of business-field dropdowns and
    an execution-date range) and the full grid of POC cards (no pagination — all
    matching POCs are shown in one continuous scroll).
    """

    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Imported here to avoid a core → pocs import at app-load time.
        from django.contrib.auth import get_user_model
        from django.db.models import Q

        from apps.pocs.models import POC

        user = self.request.user
        is_admin = user.is_admin
        G = self.request.GET

        # Base scope: every POC for admins, own POCs otherwise.
        base = POC.objects.all() if is_admin else POC.objects.filter(memberships__user=user)
        pocs = base

        # --- Main filters (available to everyone) ---
        query = G.get("q", "").strip()
        if query:
            pocs = pocs.filter(Q(name__icontains=query) | Q(description__icontains=query))
        status = G.get("status", "").strip()
        if status:
            pocs = pocs.filter(status=status)

        # --- Admin-only filters: assigned member + fine-grain business fields ---
        member_filter = ""
        fine_values = {}
        exec_from = exec_to = ""
        if is_admin:
            member_filter = G.get("u", "").strip()
            if member_filter:
                pocs = pocs.filter(memberships__user_id=member_filter)

            for field, _label in FINE_GRAIN_FILTERS:
                val = G.get(field, "").strip()
                fine_values[field] = val
                if val:
                    # Some business fields (e.g. customer_segment) hold a
                    # comma-separated list of values, so match on containment
                    # to also catch POCs whose field bundles several values.
                    pocs = pocs.filter(**{f"{field}__icontains": val})

            exec_from = G.get("exec_from", "").strip()
            exec_to = G.get("exec_to", "").strip()
            if exec_from:
                pocs = pocs.filter(execution_start__gte=exec_from)
            if exec_to:
                pocs = pocs.filter(execution_start__lte=exec_to)

        pocs = pocs.distinct().order_by("-created_at")

        # Stats (admin) follow the current selection.
        if is_admin:
            from apps.pocs.models import Requirement

            # pending_validation mirrors Requirement.is_validated (a Python
            # property, not annotatable — see its docstring): a requirement
            # with no linked tests, or any linked test not settled
            # passed/skipped, counts as pending. prefetch_related avoids an
            # N+1 query per requirement while evaluating it in Python.
            requirements = Requirement.objects.filter(poc__in=pocs).prefetch_related("tests")
            ctx["stats"] = {
                "total": pocs.count(),
                "active": pocs.filter(status=POC.Status.ACTIVE).count(),
                "completed": pocs.filter(status=POC.Status.COMPLETED).count(),
                "pending_validation": sum(1 for r in requirements if not r.is_validated),
            }

        # No pagination — every matching POC is shown in one continuous scroll.
        ctx["pocs"] = pocs
        ctx["poc_total"] = pocs.count()

        # Filter UI state.
        ctx["query"] = query
        ctx["status_filter"] = status
        ctx["status_choices"] = POC.Status.choices
        ctx["can_manage"] = is_admin

        if is_admin:
            ctx["users"] = (
                get_user_model()
                .objects.filter(is_active=True)
                .order_by("first_name", "last_name", "username")
            )
            ctx["member_filter"] = member_filter

            # Fine-grain dropdowns: distinct non-empty values across the scope.
            # A stored value may bundle several values in one comma-separated
            # string (e.g. customer_segment); split them so each atomic value
            # appears once instead of repeating inside compound entries.
            def options(field):
                seen = set()
                for raw in (
                    base.exclude(**{field: ""})
                    .values_list(field, flat=True)
                    .distinct()
                ):
                    if not raw:
                        continue
                    for token in raw.split(","):
                        token = token.strip()
                        if token:
                            seen.add(token)
                return sorted(seen)

            ctx["fine_filters"] = [
                {
                    "name": field,
                    "label": label,
                    "value": fine_values.get(field, ""),
                    "options": options(field),
                }
                for field, label in FINE_GRAIN_FILTERS
            ]
            ctx["exec_from"] = exec_from
            ctx["exec_to"] = exec_to
            active = [v for v in fine_values.values() if v]
            if exec_from or exec_to:
                active.append("exec")
            ctx["fine_filter_count"] = len(active)
            ctx["has_fine_filter"] = bool(active)

        return ctx


class BrandingView(AdminRequiredMixin, View):
    """Admin: upload / replace / remove the site logo."""

    template_name = "core/branding.html"

    def get(self, request):
        obj = SiteBranding.load()
        return render(request, self.template_name, {"form": BrandingForm(instance=obj), "branding_obj": obj})

    def post(self, request):
        obj = SiteBranding.load()
        if request.POST.get("remove"):
            if obj.logo:
                obj.logo.delete(save=False)
            obj.logo = None
            obj.save()
            messages.success(request, "Logo removed — the default mark is back.")
            return redirect("core:branding")

        old = obj.logo.name if obj.logo else None
        form = BrandingForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            if old and "logo" in request.FILES:  # replace → drop the old file
                obj.logo.storage.delete(old)
            form.save()
            messages.success(request, "Logo updated.")
            return redirect("core:branding")
        return render(request, self.template_name, {"form": form, "branding_obj": obj})


class DatabaseBackupView(AdminRequiredMixin, View):
    """Admin-only: download a full database backup, or restore from one.

    Restore is destructive (replaces ALL current data) — gated behind a
    typed "RESTORE" confirmation (``DatabaseRestoreForm``) on top of the
    admin-only access. A safety copy of the database as it stood right
    before the restore is always saved server-side first (see
    ``apps.core.db_backup.save_safety_backup``), so restoring the wrong file
    is recoverable.
    """

    template_name = "core/db_backup.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": DatabaseRestoreForm(), "engine": db_backup.engine()},
        )

    def post(self, request):
        form = DatabaseRestoreForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "engine": db_backup.engine()},
            )
        try:
            log = db_backup.restore_backup(form.cleaned_data["file"])
        except db_backup.RestoreError as exc:
            messages.error(request, f"Restore failed: {exc}")
            return render(
                request,
                self.template_name,
                {"form": DatabaseRestoreForm(), "engine": db_backup.engine()},
            )
        except Exception:  # noqa: BLE001
            logger.exception("Database restore failed unexpectedly")
            messages.error(request, "Restore failed unexpectedly — check the server logs.")
            return render(
                request,
                self.template_name,
                {"form": DatabaseRestoreForm(), "engine": db_backup.engine()},
            )
        logger.warning("Database restored by %s from an uploaded backup.", request.user)
        # The database (possibly the session table itself) was just replaced
        # — don't rely on Django messages/session surviving past this point.
        # Render a plain confirmation page directly instead of redirecting.
        return render(request, "core/db_restore_done.html", {"log": log})


def database_backup_download(request):
    """Stream a fresh full-database backup for download (admin-only)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if not request.user.is_admin:
        raise PermissionDenied
    try:
        filename, data = db_backup.create_backup()
    except db_backup.RestoreError as exc:
        messages.error(request, f"Backup failed: {exc}")
        return redirect("core:db_backup")
    content_type = (
        "application/x-sqlite3" if db_backup.engine() == "sqlite" else "application/octet-stream"
    )
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
