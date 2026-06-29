"""
Colour and typography constants for the converter (vendored).

Two output modes:
  * No template: uses the colours/fonts below (Calibri, accent blue).
  * With template (reference .docx): inherits the document's styles, fonts and
    cover page; these constants then only drive the PASS/FAIL cell colouring,
    which the template doesn't provide.
"""

from pathlib import Path

from docx.shared import Pt, RGBColor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Only used by the (unused-in-Django) list_templates helper; report generation
# passes an explicit template_path instead.
TEMPLATES_DIR = PROJECT_ROOT / "_docx_templates"

# Block from which the template's sample content is stripped (everything before
# it — cover, document-control page, TOC — is preserved).
TEMPLATE_CONTENT_MARKER_STYLE = "Heading 1"

# Native table style used for test-result tables in the template.
TEMPLATE_TABLE_STYLE = "Grid Table 1 Light Accent 5"

# Document-control page labels → frontmatter keys.
COVER_FIELD_LABELS = {
    "Confidentiality Status": "confidentiality",
    "Document Owner": "owner",
    "Document Reviewer": "reviewer",
    "Document Approver": "approver",
}

# --- Brand colours (Schneider Electric) ---
COLOR_BRAND_GREEN = RGBColor(0x3D, 0xCD, 0x58)
COLOR_BRAND_BLUE = RGBColor(0x00, 0xB0, 0xF0)

# --- No-template mode ---
COLOR_HEADER_BG = RGBColor(0x26, 0x37, 0x4A)
COLOR_HEADER_FG = RGBColor(0xFF, 0xFF, 0xFF)
COLOR_ACCENT = RGBColor(0x1F, 0x49, 0x7D)

# --- PASS/FAIL cell colouring (both modes) ---
COLOR_PASS_BG = RGBColor(0xC6, 0xEF, 0xCE)
COLOR_FAIL_BG = RGBColor(0xFF, 0xC7, 0xCE)

# --- Typography (no-template mode) ---
FONT_NAME = "Calibri"
FONT_SIZE_BODY = Pt(10)
FONT_SIZE_H1 = Pt(16)
FONT_SIZE_H2 = Pt(13)
FONT_SIZE_H3 = Pt(11)

SIZE_BY_LEVEL = {1: FONT_SIZE_H1, 2: FONT_SIZE_H2, 3: FONT_SIZE_H3}
