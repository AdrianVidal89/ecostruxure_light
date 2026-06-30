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

# Test verdict → cell text used in the summary table (PASS/FAIL get coloured
# by the builder).
_VERDICT_TOKEN = {
    "passed": "PASS",
    "failed": "FAIL",
    "blocked": "BLOCKED",
    "skipped": "SKIPPED",
    "pending": "PENDING",
}


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


def build_phase_body_markdown(phase):
    """Build the Markdown body (no frontmatter) for a phase's test report.

    Aggregates the whole subtree: a summary table across every test, then a
    section per sub-phase that has tests.
    """
    nodes = _preorder(phase)
    summary_rows = []
    for node in nodes:
        for t in node.tests.select_related("assigned_to").all():
            token = _VERDICT_TOKEN.get(t.verdict, t.verdict.upper())
            summary_rows.append((t, node, token))

    lines = [f"# {phase.name} — Test Report", ""]

    # Summary table (verdict tokens drive PASS/FAIL colouring).
    lines += ["## Summary", "", "| Test | Phase | Verdict |", "| --- | --- | --- |"]
    if summary_rows:
        for t, node, token in summary_rows:
            lines.append(f"| {_cell(t.title)} | {_cell(node.name)} | {token} |")
    else:
        lines.append("| _No tests_ |  |  |")
    lines.append("")

    # Detail, grouped per sub-phase that has tests.
    for node in nodes:
        node_tests = list(node.tests.select_related("assigned_to").all())
        if not node_tests:
            continue
        lines += [f"## {node.name}", ""]
        for t in node_tests:
            token = _VERDICT_TOKEN.get(t.verdict, t.verdict.upper())
            lines += [f"### {t.title}", "", f"**Verdict:** {token}", ""]
            if t.acceptance_criteria:
                lines += ["**Acceptance criteria:**", "", t.acceptance_criteria, ""]
            if t.expected_result:
                lines += ["**Expected result:**", "", t.expected_result, ""]
            if t.actual_result:
                lines += ["**Actual result:**", "", t.actual_result, ""]
            if t.evidence_url:
                lines += [f"**Evidence:** [{t.evidence_url}]({t.evidence_url})", ""]
            if t.executed_by:
                stamp = t.executed_at.strftime("%Y-%m-%d %H:%M") if t.executed_at else ""
                lines += [f"_Executed by {t.executed_by.username} {stamp}_", ""]
    return "\n".join(lines)


def _cell(text):
    """Escape pipe characters so table cells don't break."""
    return (text or "").replace("|", "\\|")


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
            blocks, metadata, template_path=resolve_phase_template_path(phase)
        )
        fname = f"{slugify(poc.name)}-{slugify(phase.name)}-report.docx"
        report.output_file.save(fname, ContentFile(docx_bytes), save=False)
        report.status = GeneratedReport.Status.READY
    except Exception as exc:  # noqa: BLE001 — surface the failure to the user
        report.status = GeneratedReport.Status.ERROR
        report.error_message = str(exc)
    report.save()
    _audit_generated(report, user)
    return report


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


def build_phase_documents_markdown(phase):
    """Concatenate a phase's documents (by order) into one Markdown body.

    Each document becomes a ``# Title`` section followed by its content. Used by
    both Documentation and Functional Analysis phases.
    """
    lines = []
    for doc in phase.documents.all():
        lines.append(f"# {doc.title}")
        lines.append("")
        if doc.content:
            lines.append(doc.content)
            lines.append("")
    return "\n".join(lines)


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
    report.save()
    _audit_generated(report, user)
    return report
