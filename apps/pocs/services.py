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

import logging

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q

logger = logging.getLogger(__name__)

from .models import (
    POC,
    AuditLog,
    BasePhaseDocument,
    BaseTask,
    BaseTest,
    BlueprintVersion,
    EvidenceFile,
    FunctionalAnalysisStep,
    Phase,
    PhaseDocument,
    PhaseImage,
    PhaseKind,
    PhaseTemplate,
    Requirement,
    Task,
    Test,
    UseCase,
)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def _copy_report_template(node, phase, missing=None):
    """Make ``phase.report_template`` match ``node``'s (copy, or clear).

    Best-effort: if the blueprint node's file is missing from disk (e.g. media
    wasn't carried over on a deploy, or was deleted out-of-band), skip the
    copy and log it instead of raising — one broken attachment shouldn't sink
    ``sync_blueprint_to_pocs`` for every POC in the same transaction. Callers
    that care can pass a ``missing`` set to collect the affected node names.
    """
    if phase.report_template:
        phase.report_template.delete(save=False)
        phase.report_template = None
    if node.report_template:
        try:
            node.report_template.open("rb")
            try:
                data = node.report_template.read()
            finally:
                node.report_template.close()
        except OSError:
            logger.warning(
                "Blueprint node %s (%s): report_template file missing on disk "
                "(%s) — skipping copy for phase %s.",
                node.id, node.name, node.report_template.name, phase.id,
            )
            if missing is not None:
                missing.add(node.name)
            return
        fname = node.report_template.name.rsplit("/", 1)[-1]
        phase.report_template.save(fname, ContentFile(data), save=False)


def _instantiate_phase(node, poc, parent_phase, missing=None):
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
    _copy_report_template(node, phase, missing=missing)
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


def _requirements_section_markdown(poc):
    """Markdown listing of ``poc``'s Requirements, for a locked FA section.

    Each requirement is a level-2 heading (the section itself is level 1, so
    numbering stays contiguous — 4.1., 4.2., ... — instead of jumping to a
    level 3). Its "Use cases:" line links to the matching Use Case heading
    (see the Use Cases section) rather than repeating that section's content.
    """
    reqs = list(poc.requirements.all())
    if not reqs:
        return "_No requirements defined yet._"
    lines = []
    for req in reqs:
        lines.append(f"## {req.code}" + (f" — {req.sub_system}" if req.sub_system else ""))
        lines.append("")
        lines.append(f"**Gravity:** {req.get_req_gravity_display()}")
        lines.append(f"**Operation:** {req.get_req_operation_display()}")
        lines.append(f"**Functional:** {req.get_req_functional_display()}")
        lines.append(f"**Category:** {req.get_req_category_display()}")
        if req.life_cycle_phase:
            lines.append(f"**Lifecycle status:** {req.get_life_cycle_phase_display()}")
        lines.append("")
        if req.description:
            lines.append("**Description:**")
            lines.append("")
            lines.append(req.description)
            lines.append("")
        if req.validation_criteria:
            lines.append("**Validation criteria:**")
            lines.append("")
            lines.append(req.validation_criteria)
            lines.append("")
        use_cases = list(req.use_cases.all())
        if use_cases:
            links = ", ".join(f"[{uc.code}](#{uc.code})" for uc in use_cases)
            lines.append(f"**Use cases:** {links}")
            lines.append("")
    return "\n".join(lines).rstrip()


def _usecases_section_markdown(poc):
    """Markdown listing of ``poc``'s Use Cases, for a locked FA section.

    Each use case is a level-2 heading (see :func:`_requirements_section_markdown`
    for why). Its Requirements are not repeated here — the Functional
    Requirements chapter is the source of truth and links back to this use case.
    """
    ucs = list(poc.use_cases.all())
    if not ucs:
        return "_No use cases defined yet._"
    lines = []
    for uc in ucs:
        lines.append(f"## {uc.code} — {uc.title}")
        lines.append("")
        if uc.actor:
            lines.append(f"**Actor:** {uc.actor}")
        lines.append(f"**Priority:** {uc.get_priority_display()}")
        lines.append(f"**Status:** {uc.get_status_display()}")
        lines.append("")
        if uc.description:
            lines.append("**Description:**")
            lines.append("")
            lines.append(uc.description)
            lines.append("")
    return "\n".join(lines).rstrip()


def build_test_export_md(test):
    """The complete test form as Markdown — everything the detail page shows.

    Written for round-tripping into a report, a ticket or an LLM prompt, so it
    carries the execution outcome and the linked requirements, not just the
    definition: a test read without knowing whether it passed is only half the
    story. Empty fields are skipped rather than emitted as blank headings.
    """
    heading = f"{test.test_code} — {test.title}" if test.test_code else test.title
    lines = [f"# {heading}", ""]

    poc = test.phase.poc
    meta = [
        ("POC", poc.name),
        ("Phase", test.phase.name),
        ("Assigned to", _user_label(test.assigned_to)),
        ("Target date", test.target_date.isoformat() if test.target_date else ""),
        ("Execution status", test.get_execution_status_display()),
        ("Result", test.get_result_display() if test.result else ""),
        ("Executed at", test.executed_at.strftime("%Y-%m-%d %H:%M") if test.executed_at else ""),
        ("Executed by", _user_label(test.executed_by)),
        ("Evidence URL", test.evidence_url or ""),
    ]
    for label, value in meta:
        if value:
            lines.append(f"**{label}:** {value}")
    lines.append("")

    for label, value in (
        ("Description", test.description),
        ("Acceptance criteria", test.acceptance_criteria),
        ("Expected result", test.expected_result),
        ("Actual result / notes", test.actual_result),
    ):
        if value:
            lines += [f"## {label}", "", value, ""]

    requirements = list(test.requirements.all())
    if requirements:
        lines += ["## Verified requirements", ""]
        for req in requirements:
            suffix = f" — {req.sub_system}" if req.sub_system else ""
            lines.append(f"- **{req.req_id}**{suffix}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _user_label(user):
    if not user:
        return ""
    return user.get_full_name() or user.username


def locked_section_markdown(poc, section_kind):
    """Live Markdown for a locked (Use Cases/Requirements) FA section."""
    if section_kind == FunctionalAnalysisStep.SectionKind.REQUIREMENTS:
        return _requirements_section_markdown(poc)
    if section_kind == FunctionalAnalysisStep.SectionKind.USE_CASES:
        return _usecases_section_markdown(poc)
    return ""


def ensure_fa_documents(phase):
    """Seed a PhaseDocument per Functional Analysis step (idempotent), and keep
    locked (Use Cases/Requirements) sections' content in sync.

    Each section is matched to its step via ``source_fa_step``; missing ones are
    created (title from the step). Free ("Other") sections are never touched, so
    any content the user wrote is preserved even if steps are added later; locked
    sections have their ``content`` refreshed from the POC's live data on every
    call, since the POC lead can't edit them directly.
    """
    if phase.kind != PhaseKind.FUNCTIONAL_ANALYSIS:
        return
    existing = {
        doc.source_fa_step_id: doc
        for doc in phase.documents.filter(source_fa_step__isnull=False)
    }
    for step in FunctionalAnalysisStep.objects.all():
        doc = existing.get(step.id)
        if doc is None:
            PhaseDocument.objects.create(
                phase=phase,
                source_fa_step=step,
                title=step.title,
                order=step.order,
                content=locked_section_markdown(phase.poc, step.section_kind),
            )
            continue
        if step.section_kind != FunctionalAnalysisStep.SectionKind.OTHER:
            fresh = locked_section_markdown(phase.poc, step.section_kind)
            if doc.content != fresh:
                doc.content = fresh
                doc.save(update_fields=["content", "updated_at"])


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


def sync_blueprint_to_pocs(pocs=None):
    """Apply the current blueprint to POCs. Returns counts.

    ``pocs`` limits the sync to a specific iterable/queryset of POCs (spec Fase
    8); when omitted it applies to every POC. For each POC: inherited phases
    (matched by ``source_template``) have their attributes refreshed from the
    blueprint; blueprint nodes missing from the POC are created (with base
    tasks/tests). Existing Tasks/Tests are untouched and no phases are deleted.
    """
    nodes = _preorder_templates()
    stats = {"pocs": 0, "created": 0, "updated": 0, "missing_templates": set()}
    target = POC.objects.all() if pocs is None else pocs
    missing = stats["missing_templates"]

    with transaction.atomic():
        for poc in target:
            stats["pocs"] += 1
            existing = {
                p.source_template_id: p
                for p in poc.phases.filter(source_template__isnull=False)
            }
            for node in nodes:
                parent_phase = existing.get(node.parent_id) if node.parent_id else None
                phase = existing.get(node.id)
                if phase is None:
                    phase = _instantiate_phase(node, poc, parent_phase, missing=missing)
                    existing[node.id] = phase
                    stats["created"] += 1
                else:
                    phase.name = node.name
                    phase.description = node.description
                    phase.lead_editable = node.lead_editable
                    phase.kind = node.kind
                    phase.order = node.order
                    phase.parent = parent_phase
                    _copy_report_template(node, phase, missing=missing)
                    phase.save()
                    # Top up FA sections (matchable by source_fa_step → no dupes).
                    ensure_fa_documents(phase)
                    stats["updated"] += 1
    return stats


# ---------------------------------------------------------------------------
# Blueprint version history (spec Fase 8 — undo / restore)
# ---------------------------------------------------------------------------
def _serialize_blueprint():
    """Serialise the whole blueprint (templates + base items) to plain data."""
    templates = [
        {
            "id": t.id,
            "parent_id": t.parent_id,
            "name": t.name,
            "description": t.description,
            "order": t.order,
            "lead_editable": t.lead_editable,
            "kind": t.kind,
            "report_template": t.report_template.name if t.report_template else "",
        }
        for t in PhaseTemplate.objects.all().order_by("id")
    ]
    base_tasks = [
        {"phase_template_id": b.phase_template_id, "title": b.title,
         "description": b.description, "order": b.order}
        for b in BaseTask.objects.all()
    ]
    base_tests = [
        {"phase_template_id": b.phase_template_id, "title": b.title,
         "description": b.description, "acceptance_criteria": b.acceptance_criteria,
         "expected_result": b.expected_result, "order": b.order}
        for b in BaseTest.objects.all()
    ]
    base_documents = [
        {"phase_template_id": b.phase_template_id, "title": b.title,
         "content": b.content, "order": b.order}
        for b in BasePhaseDocument.objects.all()
    ]
    return {
        "templates": templates,
        "base_tasks": base_tasks,
        "base_tests": base_tests,
        "base_documents": base_documents,
    }


def snapshot_blueprint(user=None, note=""):
    """Save a BlueprintVersion snapshot of the current blueprint (before a change)."""
    return BlueprintVersion.objects.create(
        data=_serialize_blueprint(), note=note[:255], created_by=user
    )


def restore_blueprint(version):
    """Restore the blueprint to a saved snapshot.

    Templates are upserted BY ID (so surviving nodes keep their id and existing
    POC phase links stay intact); templates not in the snapshot are removed; base
    tasks/tests/documents are rebuilt from the snapshot.
    """
    data = version.data
    keep_ids = {t["id"] for t in data.get("templates", [])}
    with transaction.atomic():
        PhaseTemplate.objects.exclude(id__in=keep_ids).delete()
        # Pass 1: upsert nodes without parent (avoids FK ordering issues).
        for t in data.get("templates", []):
            PhaseTemplate.objects.update_or_create(
                id=t["id"],
                defaults={
                    "parent_id": None,
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "order": t.get("order", 0),
                    "lead_editable": t.get("lead_editable", False),
                    "kind": t.get("kind", PhaseKind.TEST),
                    "report_template": t.get("report_template") or "",
                },
            )
        # Pass 2: wire parents.
        for t in data.get("templates", []):
            if t.get("parent_id"):
                PhaseTemplate.objects.filter(id=t["id"]).update(parent_id=t["parent_id"])
        # Rebuild base items from scratch (nothing links back to them).
        BaseTask.objects.all().delete()
        BaseTest.objects.all().delete()
        BasePhaseDocument.objects.all().delete()
        for b in data.get("base_tasks", []):
            BaseTask.objects.create(**b)
        for b in data.get("base_tests", []):
            BaseTest.objects.create(**b)
        for b in data.get("base_documents", []):
            BasePhaseDocument.objects.create(**b)


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
        for ev in EvidenceFile.objects.filter(test__phase__poc=poc):
            if ev.file:
                ev.file.delete(save=False)
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
