"""
Explicit audit logging helper.

Task/Test changes are logged automatically via signals (see ``signals.py``).
Everything else that's worth an audit trail — phase documents, phase images,
generated/imported reports, markdown versions — is logged explicitly by the
views through :func:`record_audit`, because those actions don't map cleanly to a
single tracked-field diff.

``details`` follows the same ``{field: {"before": x, "after": y}}`` shape the
signal logger uses, so the audit tab renders every entry uniformly.
"""

from django.contrib.contenttypes.models import ContentType

from apps.core.middleware import get_current_user

from .models import AuditLog


def resolve_poc(instance):
    """Best-effort owning POC for an audited instance (for direct scoping).

    Handles the audited models: those with a ``.poc`` (e.g. GeneratedReport) and
    those reached via ``.phase.poc`` (documents/images/imports/tasks/tests).
    """
    poc = getattr(instance, "poc", None)
    if poc is not None:
        return poc
    phase = getattr(instance, "phase", None)
    return getattr(phase, "poc", None) if phase is not None else None


def record_audit(instance, action, actor=None, details=None, poc=None):
    """Write an :class:`AuditLog` entry for ``instance``.

    ``actor`` defaults to the current request user (from the middleware) when
    not given. ``poc`` is derived from the instance when not supplied. Call this
    *before* deleting an object so its ``pk`` (and POC link) are still set.
    """
    if instance.pk is None:
        return None
    return AuditLog.objects.create(
        content_type=ContentType.objects.get_for_model(type(instance)),
        object_id=instance.pk,
        poc=poc if poc is not None else resolve_poc(instance),
        action=action,
        actor=actor if actor is not None else get_current_user(),
        details=details or {},
    )
