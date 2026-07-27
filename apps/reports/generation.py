"""
Report generation — the integration layer between the app and the vendored
``converter`` package.

* :func:`generate_phase_report` — assemble Markdown from a Phase's tests and
  render it into the phase's attached ``.docx`` template.
* :func:`generate_custom_report` — convert a user-uploaded source document
  (Markdown / Word / text) into a :class:`ReportType`'s template.

Accepted upload formats for custom reports: ``.md``/``.markdown`` and ``.txt``
(parsed as Markdown) and ``.docx`` (its headings/paragraphs/tables are read
back into the same block structure).
"""

from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from apps.pocs.audit import record_audit

from .converter.builder import build_docx
from .converter.parser import parse
from .models import GeneratedReport


def _audit_generated(report, user, action="report_generated"):
    """Record an audit entry for a freshly generated report (best-effort)."""
    record_audit(
        report,
        action,
        user,
        {
            "title": {"before": None, "after": report.title},
            "status": {"before": None, "after": report.status},
        },
    )

ACCEPTED_SOURCE_EXTS = {"md", "markdown", "txt", "docx"}

# Test outcome → cell text used in the summary table. Tokens starting with
# PASS / NOT PASSED get coloured green / red by the builder.
_RESULT_TOKEN = {
    "passed": "PASS",
    "passed_with_comments": "PASS (with comments)",
    "not_passed": "NOT PASSED",
}
_EXECUTION_TOKEN = {
    "not_tested": "NOT TESTED",
    "in_progress": "IN PROGRESS",
    "test_completed": "COMPLETED",
    "skipped": "SKIPPED",
}


def _test_token(test):
    """Cell text for a test: its result if decided, else its execution status."""
    if test.result:
        return _RESULT_TOKEN.get(test.result, test.result.upper())
    return _EXECUTION_TOKEN.get(test.execution_status, test.execution_status.upper())


# ---------------------------------------------------------------------------
# Source ingestion (custom reports)
# ---------------------------------------------------------------------------
def _iter_block_items(doc):
    """Yield Paragraph/Table objects in document order."""
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def docx_to_blocks(file_obj):
    """Read an uploaded .docx into the converter's (metadata, blocks) form."""
    doc = Document(file_obj)
    blocks = []
    for item in _iter_block_items(doc):
        if isinstance(item, Table):
            rows = item.rows
            if not rows:
                continue
            headers = [c.text.strip() for c in rows[0].cells]
            data = [[c.text.strip() for c in r.cells] for r in rows[1:]]
            blocks.append({"type": "table", "headers": headers, "rows": data})
            continue

        text = item.text.strip()
        if not text:
            continue
        style = (item.style.name if item.style else "") or ""
        if style.startswith("Heading") or style == "Title":
            digits = "".join(ch for ch in style if ch.isdigit())
            level = int(digits) if digits else 1
            blocks.append({"type": "heading", "level": level, "text": text})
        else:
            runs = [
                {"text": r.text, "bold": bool(r.bold), "italic": bool(r.italic)}
                for r in item.runs
                if r.text
            ] or [{"text": text}]
            blocks.append({"type": "paragraph", "children": runs})
    return {}, blocks


def source_to_blocks(file_obj, filename):
    """Dispatch an uploaded source file to (metadata, blocks) by extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ACCEPTED_SOURCE_EXTS:
        raise ValueError(
            f"Unsupported file type “.{ext}”. Use .md, .docx or .txt."
        )
    if ext == "docx":
        return docx_to_blocks(file_obj)
    text = file_obj.read()
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return parse(text)


# ---------------------------------------------------------------------------
# Phase report markdown assembly
# ---------------------------------------------------------------------------
def _preorder(phase):
    """Depth-first list of a phase and its descendants (by order)."""
    nodes = [phase]
    for child in phase.children.all().order_by("order", "id"):
        nodes.extend(_preorder(child))
    return nodes


def build_phase_body_markdown(phase, base_level=1, include_title=True):
    """Build the Markdown body (no frontmatter) for a phase's test report.

    Aggregates the whole subtree: a summary table across every test, then a
    section per sub-phase that has tests. ``base_level`` shifts every heading
    down by ``base_level - 1`` levels (default 1 = its own standalone report);
    the final report embeds this under a chapter, passing a deeper level and
    ``include_title=False`` to drop the phase's own title line.
    """
    nodes = _preorder(phase)
    summary_rows = []
    for node in nodes:
        for t in node.tests.select_related("assigned_to").prefetch_related("requirements").all():
            summary_rows.append((t, node, _test_token(t)))

    h1 = "#" * base_level
    h2 = "#" * (base_level + 1)
    h3 = "#" * (base_level + 2)

    lines = []
    if include_title:
        lines += [f"{h1} {phase.name} — Test Report", ""]

    # Summary table (result tokens drive PASS/FAIL colouring). Leading "ID"
    # column is the test's own code (e.g. UT-001) so rows are traceable back
    # to the test even once sorted/exported out of the report.
    lines += [f"{h2} Summary", "", "| ID | Test | Phase | Result |", "| --- | --- | --- | --- |"]
    if summary_rows:
        for t, node, token in summary_rows:
            lines.append(f"| {_cell(t.test_code)} | {_cell(t.title)} | {_cell(node.name)} | {token} |")
    else:
        lines.append("| _No tests_ |  |  |  |")
    lines.append("")

    # Detail, grouped per sub-phase that has tests.
    for node in nodes:
        node_tests = list(
            node.tests.select_related("assigned_to")
            .prefetch_related("requirements", "parameters")
            .all()
        )
        if not node_tests:
            continue
        lines += [f"{h2} {node.name}", ""]
        for t in node_tests:
            token = _test_token(t)
            # ID + Title in every chapter heading (never title alone), so a
            # reader can always trace a chapter back to its test.
            heading = f"{t.test_code} — {t.title}" if t.test_code else t.title
            lines += [f"{h3} {heading}", "", f"**Result:** {token}", ""]
            lines += [f"**Target date:** {t.target_date or '—'}", ""]
            if t.description:
                lines += ["**Description:**", "", t.description, ""]
            # Parameters — always show the heading (even when empty) so the
            # report never silently omits this decision-relevant section;
            # the reader should never have to wonder if data is missing or
            # just not rendered.
            params = list(t.parameters.all())
            lines += ["**Parameters:**", ""]
            if params:
                lines += ["| Name | Value |", "| --- | --- |"]
                for p in params:
                    lines.append(f"| {_cell(p.name)} | {_cell(p.value)} |")
                lines.append("")
            else:
                lines += ["_No parameters defined for this test._", ""]
            # Expected result — kept adjacent to Parameters (both are inputs
            # the reader needs to judge the run), same "always show" rule.
            lines += ["**Expected result:**", ""]
            if t.expected_result:
                lines += [t.expected_result, ""]
            else:
                lines += ["_Not defined._", ""]
            reqs = list(t.requirements.all())
            if reqs:
                lines += ["**Functional requirements:**", ""]
                for r in reqs:
                    label = f"{r.code}" + (f" — {r.description}" if r.description else "")
                    lines.append(f"- {_cell(label)}")
                lines.append("")
            if t.acceptance_criteria:
                lines += ["**Acceptance criteria:**", "", t.acceptance_criteria, ""]
            if t.actual_result:
                lines += ["**Actual result:**", "", t.actual_result, ""]
            for ev in t.evidence_files.all():
                fname = ev.file.name.rsplit("/", 1)[-1]
                if ev.is_image:
                    lines += [f"![evidence]({ev.file.url})", ""]
                else:
                    lines += [f'_For evidence see attachment "{fname}"_', ""]
            if t.evidence_url:
                lines += [f"**Evidence:** [{t.evidence_url}]({t.evidence_url})", ""]
            if t.executed_by:
                stamp = t.executed_at.strftime("%Y-%m-%d %H:%M") if t.executed_at else ""
                lines += [f"_Executed by {t.executed_by.username} {stamp}_", ""]
    return "\n".join(lines)


def _cell(text):
    """Escape pipe characters so table cells don't break."""
    return (text or "").replace("|", "\\|")


def mark_after_closure(report, poc):
    """Flag a report as a post-closure "late" document (spec Fase 6b)."""
    if poc is not None and getattr(poc, "is_closed", False):
        report.after_closure = True


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
def generate_phase_report(phase, user):
    """Generate a GeneratedReport for ``phase`` using its attached template."""
    poc = phase.poc
    report = GeneratedReport(
        kind=GeneratedReport.Kind.PHASE,
        title=f"{poc.name} — {phase.name}",
        poc=poc,
        phase=phase,
        requested_by=user,
        status=GeneratedReport.Status.PROCESSING,
    )
    try:
        metadata = {
            "owner": (user.get_full_name() or user.username) if user else "",
            "date": timezone.now().date().isoformat(),
        }
        _, blocks = parse(build_phase_body_markdown(phase))
        docx_bytes = build_docx(
            blocks,
            metadata,
            template_path=resolve_phase_template_path(phase),
            image_resolver=make_test_evidence_resolver(phase),
        )
        fname = f"{slugify(poc.name)}-{slugify(phase.name)}-report.docx"
        report.output_file.save(fname, ContentFile(docx_bytes), save=False)
        report.status = GeneratedReport.Status.READY
    except Exception as exc:  # noqa: BLE001 — surface the failure to the user
        report.status = GeneratedReport.Status.ERROR
        report.error_message = str(exc)
    mark_after_closure(report, report.poc)
    report.save()
    _audit_generated(report, user)
    return report


def make_test_evidence_resolver(phase):
    """Return a ``url -> path`` resolver for evidence images across a phase's
    subtree of tests, mirroring ``make_phase_image_resolver``."""
    from apps.pocs.models import EvidenceFile

    by_url = {}
    for ev in EvidenceFile.objects.filter(test__phase__in=[n.pk for n in _preorder(phase)], test__isnull=False):
        if not ev.is_image:
            continue
        try:
            by_url[ev.file.url] = ev.file.path
        except ValueError:
            continue

    def resolver(url):
        return by_url.get(url) or by_url.get((url or "").split("?", 1)[0])

    return resolver


def resolve_phase_template_path(phase):
    """Path of the .docx template to use for a phase report.

    Prefers the phase's own ``report_template`` *when it's a .docx*; a .zip
    template is a download-only bundle and can't drive generation, so it falls
    back to the global default (``ReportSettings``). Raises if neither yields a
    usable .docx.
    """
    from .models import ReportSettings

    if phase.has_docx_template:
        return phase.report_template.path
    default = ReportSettings.load().default_template
    if default:
        return default.path
    raise ValueError(
        "No .docx report template: attach a .docx to this phase or set a global "
        "default (Reports → settings). A .zip template is download-only."
    )


def make_phase_image_resolver(phase):
    """Return a ``url -> path`` resolver limited to this phase's images.

    Markdown image URLs are matched against each ``PhaseImage.image.url`` (the
    snippet the UI offers), so only images uploaded to this phase are embedded.
    """
    by_url = {}
    for img in phase.images.all():
        try:
            by_url[img.image.url] = img.image.path
        except ValueError:  # image with no file
            continue

    def resolver(url):
        return by_url.get(url) or by_url.get((url or "").split("?", 1)[0])

    return resolver


def build_phase_documents_markdown(phase, base_level=1):
    """Concatenate a phase's documents (by order) into one Markdown body.

    Each document becomes a ``# Title`` section (shifted to ``base_level``, so
    the final report can nest it under a chapter/phase heading) followed by
    its content. Used by both Documentation and Functional Analysis phases.
    """
    h1 = "#" * base_level
    lines = []
    for doc in phase.documents.all():
        lines.append(f"{h1} {doc.title}")
        lines.append("")
        if doc.content:
            lines.append(doc.content)
            lines.append("")
    return "\n".join(lines)


def _make_poc_image_resolver(poc):
    """A ``url -> path`` resolver spanning every image of the POC's phases
    plus any images uploaded directly to the POC (closure conclusion)."""
    from apps.pocs.models import PhaseImage, POCImage

    by_url = {}
    for img in PhaseImage.objects.filter(phase__poc=poc):
        try:
            by_url[img.image.url] = img.image.path
        except ValueError:
            continue
    for img in POCImage.objects.filter(poc=poc):
        try:
            by_url[img.image.url] = img.image.path
        except ValueError:
            continue

    def resolver(url):
        return by_url.get(url) or by_url.get((url or "").split("?", 1)[0])

    return resolver


def _all_phases_preorder(poc):
    """Every phase of a POC, depth-first, walking each root in order."""
    nodes = []
    for root in poc.phases.filter(parent__isnull=True).order_by("order", "id"):
        nodes.extend(_preorder(root))
    return nodes


def build_final_report_markdown(poc, conclusion=""):
    """Assemble the whole-POC final report as a merge of the two dedicated
    per-kind reports, under exactly three Level-1 chapters: Functional
    Analysis, Testing and Validation, Conclusions. Documentation-kind phases
    are not part of the final report (still available via their own report).

    Each Functional Analysis phase becomes an H2 (its documents reusing
    ``build_phase_documents_markdown``'s structure, shifted under it); each
    Test phase becomes an H2 (its tests reusing ``build_phase_body_markdown``'s
    summary + detail, shifted under it) — matching each phase's own dedicated
    report, just nested one level deeper so headings never skip a level.
    """
    lines = [f"# {poc.name} — Final Report", ""]
    if poc.closure_date:
        lines += [f"_Official closure date: {poc.closure_date.isoformat()}_", ""]

    all_phases = _all_phases_preorder(poc)

    # Only leaf phases actually own documents/tests — a non-leaf phase of the
    # same kind has no content of its own (its leaves are visited separately),
    # so restricting to leaves avoids double-counting a subtree twice.
    lines += ["# Functional Analysis", ""]
    for phase in all_phases:
        if not (phase.is_functional_analysis and phase.is_leaf):
            continue
        note = "" if (phase.is_approved or not phase.has_own_items) else "  _(pending / not approved)_"
        lines += [f"## {phase.name}{note}", ""]
        body = build_phase_documents_markdown(phase, base_level=3)
        if body:
            lines += [body, ""]

    lines += ["# Testing and Validation", ""]
    for phase in all_phases:
        if not (phase.is_test and phase.is_leaf):
            continue
        note = "" if (phase.is_approved or not phase.has_own_items) else "  _(pending / not approved)_"
        lines += [f"## {phase.name}{note}", ""]
        # base_level=2 (not 3): its own title is suppressed, so "Summary"/
        # sub-phase headings (base_level+1) land at H3, right under the "##
        # {phase.name}" (H2) emitted above — no level is skipped.
        body = build_phase_body_markdown(phase, base_level=2, include_title=False)
        if body:
            lines += [body, ""]

    if (conclusion or "").strip():
        lines += ["# Conclusions", "", conclusion, ""]
    return "\n".join(lines)


def generate_final_report(poc, user, conclusion=""):
    """Generate & seal the POC's final report (spec Fase 6b)."""
    from .models import ReportSettings

    report = GeneratedReport(
        kind=GeneratedReport.Kind.FINAL,
        title=f"{poc.name} — Final Report",
        poc=poc,
        requested_by=user,
        status=GeneratedReport.Status.PROCESSING,
    )
    try:
        template = ReportSettings.load().default_template
        metadata = {
            "owner": (user.get_full_name() or user.username) if user else "",
            "date": timezone.now().date().isoformat(),
        }
        _, blocks = parse(build_final_report_markdown(poc, conclusion))
        docx_bytes = build_docx(
            blocks,
            metadata,
            template_path=template.path if template else None,
            image_resolver=_make_poc_image_resolver(poc),
        )
        fname = f"{slugify(poc.name)}-final-report.docx"
        report.output_file.save(fname, ContentFile(docx_bytes), save=False)
        report.status = GeneratedReport.Status.READY
    except Exception as exc:  # noqa: BLE001
        report.status = GeneratedReport.Status.ERROR
        report.error_message = str(exc)
    report.save()
    _audit_generated(report, user, action="final_report_generated")
    return report


def generate_phase_report_from_documents(phase, user):
    """Generate a phase report from its documents (Documentation / FA flow).

    Resolves the template (phase-specific or global default) and embeds any
    images referenced from the documents' Markdown.
    """
    poc = phase.poc
    report = GeneratedReport(
        kind=GeneratedReport.Kind.PHASE,
        title=f"{poc.name} — {phase.name}",
        poc=poc,
        phase=phase,
        requested_by=user,
        status=GeneratedReport.Status.PROCESSING,
    )
    try:
        template_path = resolve_phase_template_path(phase)
        metadata, blocks = parse(build_phase_documents_markdown(phase))
        docx_bytes = build_docx(
            blocks,
            metadata or None,
            template_path=template_path,
            image_resolver=make_phase_image_resolver(phase),
        )
        fname = f"{slugify(poc.name)}-{slugify(phase.name)}-report.docx"
        report.output_file.save(fname, ContentFile(docx_bytes), save=False)
        report.status = GeneratedReport.Status.READY
    except Exception as exc:  # noqa: BLE001
        report.status = GeneratedReport.Status.ERROR
        report.error_message = str(exc)
    mark_after_closure(report, report.poc)
    report.save()
    _audit_generated(report, user)
    return report


def generate_custom_report(report_type, source_file, user, poc=None):
    """Generate a GeneratedReport from an uploaded source + a ReportType template."""
    report = GeneratedReport(
        kind=GeneratedReport.Kind.CUSTOM,
        title=f"{report_type.name} — {source_file.name}",
        report_type=report_type,
        poc=poc,
        requested_by=user,
        status=GeneratedReport.Status.PROCESSING,
    )
    # Keep the uploaded source for traceability.
    report.source_file.save(source_file.name, source_file, save=False)
    try:
        # Read from the saved copy, closing the handle afterwards (avoids
        # leaking file descriptors / locking the file on Windows).
        report.source_file.open("rb")
        try:
            metadata, blocks = source_to_blocks(report.source_file, source_file.name)
        finally:
            report.source_file.close()
        docx_bytes = build_docx(
            blocks, metadata or None, template_path=report_type.template.path
        )
        fname = f"{slugify(report_type.name)}-{slugify(source_file.name) or 'report'}.docx"
        report.output_file.save(fname, ContentFile(docx_bytes), save=False)
        report.status = GeneratedReport.Status.READY
    except Exception as exc:  # noqa: BLE001
        report.status = GeneratedReport.Status.ERROR
        report.error_message = str(exc)
    mark_after_closure(report, report.poc)
    report.save()
    _audit_generated(report, user)
    return report
