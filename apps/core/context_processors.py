"""
Template context processors for the core app.

``navigation`` injects the sidebar navigation model and app metadata into every
template, so the layout stays data-driven and easy to extend as new sections
come online in later build steps.
"""

from django.conf import settings


def navigation(request):
    """Provide sidebar nav items and global app metadata to all templates.

    Each nav item declares whether it is ``available`` yet. Items that point at
    URL patterns not yet wired (later build steps) render as disabled, so the
    intended information architecture is visible without breaking ``{% url %}``
    resolution.
    """
    user = getattr(request, "user", None)
    is_admin = bool(user and user.is_authenticated and getattr(user, "is_admin", False))
    authed = bool(user and user.is_authenticated)

    # Pending test-outcome validations awaiting this user (spec Fase 3b).
    validation_count = _pending_validation_count(user) if authed else 0

    nav_items = [
        {
            "label": "Dashboard",
            "url_name": "core:dashboard",
            "icon": "grid",
            "available": True,
        },
        {
            "label": "Tasks",
            "url_name": "pocs:tasks",
            "icon": "list-checks",
            "available": True,
        },
        {
            "label": "Tests",
            "url_name": "pocs:tests_overview",
            "icon": "flask-conical",
            "available": True,
        },
        {
            "label": "Validations",
            "url_name": "pocs:validations",
            "icon": "shield-check",
            "available": True,
            "badge": validation_count,
            # Only relevant to validators (admins, or when there's something).
            "show_if_badge_or_admin": True,
        },
        {
            "label": "Phase blueprint",
            "url_name": "pocs:phase_template_list",
            "icon": "git-branch",
            "available": True,
            "admin_only": True,
        },
        {
            "label": "Functional Analysis",
            "url_name": "pocs:fa_step_list",
            "icon": "clipboard-list",
            "available": True,
            "admin_only": True,
        },
        {
            "label": "Users",
            "url_name": "accounts:user_list",
            "icon": "users",
            "available": True,  # admin-only user management
            "admin_only": True,
        },
        {
            "label": "Branding",
            "url_name": "core:branding",
            "icon": "image",
            "available": True,
            "admin_only": True,
        },
        {
            "label": "Reports",
            "url_name": "reports:home",
            "icon": "file-text",
            "available": True,
        },
    ]

    # Hide admin-only entries from non-admins; hide validator-only entries from
    # users with nothing to validate (unless admin).
    visible = [
        i
        for i in nav_items
        if (not i.get("admin_only") or is_admin)
        and (
            not i.get("show_if_badge_or_admin")
            or is_admin
            or i.get("badge")
        )
    ]

    return {
        "app_name": "EcoStruxure Light",
        "app_version": getattr(settings, "APP_VERSION", "0.1.0"),
        "nav_items": visible,
        "validation_count": validation_count,
        "branding": _branding(),
    }


def _pending_validation_count(user):
    """Count of pending test outcomes this user is allowed to validate."""
    try:
        from django.db.models import Q

        from apps.pocs.models import POCMembership, TestValidation

        qs = TestValidation.objects.filter(status="pending")
        if getattr(user, "is_admin", False):
            return qs.count()
        lead_ids = POCMembership.objects.filter(
            user=user, role_in_poc="lead"
        ).values_list("poc_id", flat=True)
        return qs.filter(
            Q(test__phase__phase_leader=user)
            | Q(test__phase__poc_id__in=lead_ids)
        ).count()
    except Exception:  # noqa: BLE001
        return 0


def _branding():
    """The uploaded logo (or None). Guarded so it never breaks a request
    (e.g. before the table exists during migration)."""
    try:
        from .models import SiteBranding

        return SiteBranding.objects.first()
    except Exception:  # noqa: BLE001
        return None
