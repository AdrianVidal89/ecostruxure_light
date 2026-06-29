"""
Markdown → DOCX converter (vendored).

Adapted from the standalone *tests-converter* tool. Public API:

    from apps.reports.converter.parser import parse
    from apps.reports.converter.builder import build_docx

    metadata, blocks = parse(markdown_text)
    docx_bytes = build_docx(blocks, metadata, template_path=path_to_reference_docx)

``build_docx`` with a ``template_path`` inherits the reference .docx's cover
page, document-control page, table of contents and styles, dropping the sample
body after the first ``Heading 1`` and colouring PASS/FAIL table cells.
"""
