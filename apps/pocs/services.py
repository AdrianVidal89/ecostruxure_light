"""
POC services — phase-blueprint inheritance and propagation.

* :func:`apply_phase_templates` — deep-copy the blueprint into a new POC.
* :func:`sync_blueprint_to_pocs` — push later blueprint changes to *all* POCs
  ("Apply to all"): update inherited phases' attributes (name, description,
  lead_editable, report template, order) and create any blueprint nodes a POC
  doesn't have yet (with their base tasks/tests).

Sync is intentionally non-destructive to execution data: it never edits or
deletes existing Tasks/Tests, and never removes phases (so results are safe).
"""

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q

from .models import (
    POC,
    AuditLog,
    FunctionalAnalysisStep,
    Phase,
    PhaseDocument,
    PhaseImage,
    PhaseKind,
    PhaseTemplate,
    Task,
    Test,
)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def _copy_report_template(node, phase):
    """Make ``phase.report_template`` match ``node``'s (copy, or clear)."""
    if phase.report_template:
        phase.report_template.delete(save=False)
        phase.report_template = None
    if node.report_template:
        node.report_template.open("rb")
        try:
            data = node.report_template.read()
        finally:
            node.report_template.close()
        fname = node.report_template.name.rsplit("/", 1)[-1]
        phase.report_template.save(fname, ContentFile(data), save=False)


def _instantiate_phase(node, poc, parent_phase):
    """Create a Phase from a blueprint node (with its base tasks/tests/docs)."""
    phase = Phase(
        poc=poc,
        parent=parent_phase,
        source_template=node,
        name=node.name,
        description=node.description,
        order=node.order,
        lead_editable=node.lead_editable,
        kind=node.kind,
    )
    _copy_report_template(node, phase)
    phase.save()
    for bt in node.base_tasks.all():
        Task.objects.create(phase=phase, title=bt.title, description=bt.description)
    for bt in node.base_tests.all():
        Test.objects.create(
            phase=phase,
            title=bt.title,
            description=bt.description,
            acceptance_criteria=bt.acceptance_criteria,
            expected_result=bt.expected_result,
        )
    for bd in node.base_documents.all():
        PhaseDocument.objects.create(
            phase=phase, title=bd.title, content=bd.content, order=bd.order
        )
    if phase.kind == PhaseKind.FUNCTIONAL_ANALYSIS:
        ensure_fa_documents(phase)
    return phase


def ensure_fa_documents(phase):
    """Seed a PhaseDocument per Functional Analysis step (idempotent).

    Each section is matched to its step via ``source_fa_step``; missing ones are
    created (title from the step). Existing sections are never touched, so any
    content the user wrote is preserved even if steps are added later.
    """
    if phase.kind != PhaseKind.FUNCTIONAL_ANALYSIS:
        return
    existing = set(
        phase.documents.filter(source_fa_step__isnull=False).values_list(
            "source_fa_step_id", flat=True
        )
    )
    for step in FunctionalAnalysisStep.objects.all():
        if step.id in existing:
            continue
        PhaseDocument.objects.create(
            phase=phase,
            source_fa_step=step,
            title=step.title,
            order=step.order,
        )


def _copy_node(node, poc, parent):
    """Recursively instantiate a blueprint node and its descendants."""
    phase = _instantiate_phase(node, poc, parent)
    for child in node.children.all().order_by("order", "id"):
        _copy_node(child, poc, phase)


def _preorder_templates():
    """All blueprint nodes, parents before children."""
    result = []

    def walk(qs):
        for node in qs.order_by("order", "id"):
            result.append(node)
            walk(node.children.all())

    walk(PhaseTemplate.objects.filter(parent__isnull=True))
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def apply_phase_templates(poc):
    """Instantiate the whole blueprint into a brand-new POC (no-op if it already
    has phases)."""
    if poc.phases.exists():
        return
    roots = PhaseTemplate.objects.filter(parent__isnull=True).order_by("order", "id")
    with transaction.atomic():
        for node in roots:
            _copy_node(node, poc, parent=None)


def sync_blueprint_to_pocs():
    """Apply the current blueprint to every POC. Returns counts.

    For each POC: inherited phases (matched by ``source_template``) have their
    attributes refreshed from the blueprint; blueprint nodes missing from the
    POC are created (with base tasks/tests). Existing Tasks/Tests are untouched
    and no phases are deleted.
    """
    nodes = _preorder_templates()
    stats = {"pocs": 0, "created": 0, "updated": 0}

    with transaction.atomic():
        for poc in POC.objects.all():
            stats["pocs"] += 1
            existing = {
                p.source_template_id: p
                for p in poc.phases.filter(source_template__isnull=False)
            }
            for node in nodes:
                parent_phase = existing.get(node.parent_id) if node.parent_id else None
                phase = existing.get(node.id)
                if phase is None:
                    phase = _instantiate_phase(node, poc, parent_phase)
                    existing[node.id] = phase
                    stats["created"] += 1
                else:
                    phase.name = node.name
                    phase.description = node.description
                    phase.lead_editable = node.lead_editable
                    phase.kind = node.kind
                    phase.order = node.order
                    phase.parent = parent_phase
                    _copy_report_template(node, phase)
                    phase.save()
                    # Top up FA sections (matchable by source_fa_step → no dupes).
                    ensure_fa_documents(phase)
                    stats["updated"] += 1
    return stats


# ---------------------------------------------------------------------------
# Hard delete
# ---------------------------------------------------------------------------
def delete_poc(poc):
    """Permanently delete a POC and everything attached to it.

    FK cascade removes phases → tasks/tests, memberships and generated reports.
    We additionally clean up what cascade does NOT: stored files (phase report
    templates, test evidence, generated report files) and AuditLog rows (a
    generic FK, so not cascaded). Returns the deleted POC's name.
    """
    from apps.reports.models import GeneratedReport

    name = poc.name
    task_ids = list(Task.objects.filter(phase__poc=poc).values_list("id", flat=True))
    test_ids = list(Test.objects.filter(phase__poc=poc).values_list("id", flat=True))

    with transaction.atomic():
        # Stored files (FileField rows don't delete their file on cascade).
        for phase in poc.phases.all():
            if phase.report_template:
                phase.report_template.delete(save=False)
        for test in Test.objects.filter(phase__poc=poc):
            if test.evidence_file:
                test.evidence_file.delete(save=False)
        for img in PhaseImage.objects.filter(phase__poc=poc):
            if img.image:
                img.image.delete(save=False)
        for report in GeneratedReport.objects.filter(poc=poc):
            for ff in (report.output_file, report.source_file):
                if ff:
                    ff.delete(save=False)

        # AuditLog uses a generic FK — remove this POC's task/test entries.
        task_ct = ContentType.objects.get_for_model(Task)
        test_ct = ContentType.objects.get_for_model(Test)
        AuditLog.objects.filter(
            Q(content_type=task_ct, object_id__in=task_ids)
            | Q(content_type=test_ct, object_id__in=test_ids)
        ).delete()

        poc.delete()  # cascade: phases, tasks, tests, memberships, reports
    return name
