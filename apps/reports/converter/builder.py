"""
Typed blocks → python-docx Document → bytes (vendored).

Two modes:
  * template_path=None  → new document with built-in styles (Calibri).
  * template_path=...    → inherits styles/fonts/cover from the reference
    template (see template.py); adds PASS/FAIL cell colouring on top.
"""

from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from . import styles
from .template import prepare_from_template

# Cap embedded images so a large upload doesn't overflow the page width.
_MAX_IMAGE_WIDTH = Inches(6)


def build_docx(blocks: list[dict], metadata: dict | None = None,
               template_path=None, image_resolver=None) -> bytes:
    """Render typed blocks to a .docx.

    ``image_resolver`` (optional): ``url -> local file path | None``. When a
    block of type ``image`` is found its URL is resolved to a file and embedded;
    unresolved images fall back to their alt text.
    """
    use_template = template_path is not None
    if use_template:
        doc = prepare_from_template(template_path, metadata)
    else:
        doc = Document()
        _apply_base_styles(doc)
        if metadata:
            _add_cover(doc, metadata)

    table_style = _resolve_table_style(doc, use_template)

    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            _add_heading(doc, block, use_template)
        elif kind == "paragraph":
            _add_paragraph(doc, block)
        elif kind == "table":
            _add_table(doc, block, table_style, use_template)
        elif kind == "list":
            _add_list(doc, block)
        elif kind == "image":
            _add_image(doc, block, image_resolver)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _resolve_table_style(doc, use_template: bool) -> str:
    available = {s.name for s in doc.styles}
    if use_template and styles.TEMPLATE_TABLE_STYLE in available:
        return styles.TEMPLATE_TABLE_STYLE
    return "Table Grid" if "Table Grid" in available else "Normal Table"


def _apply_base_styles(doc):
    font = doc.styles["Normal"].font
    font.name = styles.FONT_NAME
    font.size = styles.FONT_SIZE_BODY


def _add_cover(doc, meta: dict):
    title = meta.get("title", "")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(str(title))
    run.bold = True
    run.font.size = Pt(24)
    run.font.color.rgb = styles.COLOR_ACCENT

    for key, val in meta.items():
        if key == "title" or val in (None, ""):
            continue
        row = doc.add_paragraph()
        label = row.add_run(f"{key.capitalize()}: ")
        label.bold = True
        row.add_run(str(val))

    doc.add_page_break()


def _add_heading(doc, block: dict, use_template: bool):
    level = min(int(block["level"]), 3)
    p = doc.add_heading(block["text"], level=level)
    if not use_template and p.runs:
        run = p.runs[0]
        run.font.color.rgb = styles.COLOR_ACCENT
        run.font.size = styles.SIZE_BY_LEVEL[level]


def _add_paragraph(doc, block: dict):
    p = doc.add_paragraph()
    _render_runs(p, block.get("children", []))


def _add_image(doc, block: dict, image_resolver):
    """Embed an image (resolved to a local path) or fall back to its alt text."""
    url = block.get("url", "")
    path = image_resolver(url) if (image_resolver and url) else None
    if path:
        try:
            picture = doc.add_picture(path)
            # Downscale only if it overflows the page; never upscale small images.
            if picture.width > _MAX_IMAGE_WIDTH:
                ratio = _MAX_IMAGE_WIDTH / picture.width
                picture.width = _MAX_IMAGE_WIDTH
                picture.height = int(picture.height * ratio)
            return
        except Exception:  # noqa: BLE001 — unreadable image → alt-text fallback
            pass
    alt = block.get("alt") or url
    if alt:
        p = doc.add_paragraph()
        run = p.add_run(f"[image: {alt}]")
        run.italic = True


def _add_list(doc, block: dict):
    available = {s.name for s in doc.styles}
    if block.get("ordered"):
        style = "List Number" if "List Number" in available else "List Paragraph"
    else:
        style = "List Bullet" if "List Bullet" in available else "List Paragraph"
    for item_runs in block["items"]:
        p = doc.add_paragraph(style=style)
        _render_runs(p, item_runs)


def _render_runs(paragraph, runs: list[dict]):
    for rd in runs:
        run = paragraph.add_run(rd.get("text", ""))
        run.bold = rd.get("bold", False)
        run.italic = rd.get("italic", False)
        if rd.get("code"):
            run.font.name = "Consolas"
        if rd.get("link"):
            run.font.underline = True
            run.font.color.rgb = styles.COLOR_ACCENT


def _add_table(doc, block: dict, table_style: str, use_template: bool):
    headers = block["headers"]
    rows = block["rows"]
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = table_style
    _enable_first_row_format(table)

    for i, text in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = text
        para = cell.paragraphs[0]
        run = para.runs[0] if para.runs else para.add_run("")
        run.bold = True
        if not use_template:
            run.font.color.rgb = styles.COLOR_HEADER_FG
            _set_cell_bg(cell, styles.COLOR_HEADER_BG)

    for r_idx, row in enumerate(rows):
        for c_idx, text in enumerate(row):
            cell = table.rows[r_idx + 1].cells[c_idx]
            cell.text = text
            upper = text.strip().upper()
            if upper.startswith("PASS") or upper in ("OK", "✓"):
                _set_cell_bg(cell, styles.COLOR_PASS_BG)
            elif upper.startswith("FAIL") or upper in ("KO", "NOK", "✗"):
                _set_cell_bg(cell, styles.COLOR_FAIL_BG)


def _enable_first_row_format(table):
    tblPr = table._tbl.tblPr
    look = tblPr.find(qn("w:tblLook"))
    if look is None:
        look = OxmlElement("w:tblLook")
        tblPr.append(look)
    look.set(qn("w:firstRow"), "1")
    look.set(qn("w:lastRow"), "0")
    look.set(qn("w:firstColumn"), "0")
    look.set(qn("w:lastColumn"), "0")
    look.set(qn("w:noHBand"), "0")
    look.set(qn("w:noVBand"), "1")


def _set_cell_bg(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = tcPr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tcPr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), f"{color[0]:02X}{color[1]:02X}{color[2]:02X}")
