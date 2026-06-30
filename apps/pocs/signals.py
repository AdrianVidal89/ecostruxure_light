"""
Audit logging via signals.

On every Task / Test save we record an :class:`~apps.pocs.models.AuditLog`
entry capturing the actor (from the current-user middleware), an action string,
and a before/after diff of a curated set of tracked fields.

* ``pre_save``  — snapshot the persisted (old) values onto the instance.
* ``post_save`` — diff against the new values and write the log entry.

Creates are logged as ``created``; edits that touch no tracked field are
skipped to keep the log meaningful.
"""

from django.contrib.contenttypes.models import ContentType
from django.db.models import Model
from django.db.models.fields.files import FieldFile
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.core.middleware import get_current_user

from .models import AuditLog, Task, Test

# Fields whose changes are worth recording, per model.
TRACKED_FIELDS = {
    Task: ["title", "status", "assigned_to", "notes", "due_date"],
    Test: [
        "title",
        "verdict",
        "assigned_to",
        "actual_result",
        "evidence_file",
        "evidence_url",
    ],
}


def _serialize(value):
    """Coerce a field value to something JSON-serialisable and readable."""
    if value is None or value == "":
        return None
    if isinstance(value, FieldFile):
        return value.name or None
    if hasattr(value, "isoformat"):  # date / datetime
        return value.isoformat()
    if isinstance(value, Model):  # e.g. assigned_to → "Mia Member (member)"
        return str(value)
    return value


def _snapshot(instance, fields):
    return {f: _serialize(getattr(instance, f)) for f in fields}


def _action_for(sender, changed):
    """Pick a primary action label from the set of changed fields."""
    keys = set(changed)
    if sender is Task:
        if "status" in keys:
            return "status_changed"
        if "assigned_to" in keys:
            return "reassigned"
        if "notes" in keys:
            return "notes_updated"
    else:  # Test
        if "verdict" in keys:
            return "verdict_changed"
        if "evidence_file" in keys and changed["evidence_file"]["after"]:
            return "file_uploaded"
        if "actual_result" in keys:
            return "result_added"
        if "assigned_to" in keys:
            return "reassigned"
    return "updated"


@receiver(pre_save, sender=Task)
@receiver(pre_save, sender=Test)
def _capture_old_state(sender, instance, **kwargs):
    """Snapshot the currently-persisted values before the save is applied."""
    if not instance.pk:
        instance._audit_old = None
        return
    try:
        old = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        instance._audit_old = None
        return
    instance._audit_old = _snapshot(old, TRACKED_FIELDS[sender])


@receiver(post_save, sender=Task)
@receiver(post_save, sender=Test)
def _write_audit_log(sender, instance, created, **kwargs):
    fields = TRACKED_FIELDS[sender]
    new = _snapshot(instance, fields)

    if created:
        action = "created"
        # Record the non-empty initial values.
        details = {k: {"before": None, "after": v} for k, v in new.items() if v is not None}
    else:
        old = getattr(instance, "_audit_old", None) or {}
        details = {
            f: {"before": old.get(f), "after": new[f]}
            for f in fields
            if old.get(f) != new[f]
        }
        if not details:
            return  # nothing tracked changed
        action = _action_for(sender, details)

    AuditLog.objects.create(
        content_type=ContentType.objects.get_for_model(sender),
        object_id=instance.pk,
        poc=instance.phase.poc if instance.phase_id else None,
        action=action,
        actor=get_current_user(),
        details=details,
    )
