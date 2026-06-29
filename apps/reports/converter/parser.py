"""
Markdown → typed blocks (vendored).

Uses mistune in AST mode (renderer=None) and python-frontmatter for the optional
YAML header, returning simple dicts the builder turns into python-docx content.
"""

import frontmatter
import mistune

_md = mistune.create_markdown(renderer=None, plugins=["table", "strikethrough"])


def parse(md_text: str) -> tuple[dict, list[dict]]:
    """Return (metadata, blocks). Metadata comes from optional YAML frontmatter."""
    post = frontmatter.loads(md_text)
    metadata = dict(post.metadata)
    tokens = _md(post.content)
    return metadata, _flatten(tokens)


def _flatten(tokens: list[dict]) -> list[dict]:
    blocks = []
    for tok in tokens:
        t = tok.get("type")
        if t == "heading":
            blocks.append({
                "type": "heading",
                "level": tok["attrs"]["level"],
                "text": _extract_text(tok.get("children", [])),
            })
        elif t == "paragraph":
            # Images may sit on their own line or in the middle of a paragraph;
            # split them into standalone image blocks so they embed in order.
            blocks.extend(_split_paragraph(tok.get("children", [])))
        elif t == "table":
            blocks.append(_parse_table(tok))
        elif t in ("list",):
            blocks.append(_parse_list(tok))
        elif t == "block_quote":
            for child in tok.get("children", []):
                if child.get("type") == "paragraph":
                    blocks.append({
                        "type": "paragraph",
                        "children": _extract_runs(child.get("children", [])),
                        "quote": True,
                    })
        # Thematic breaks ('---') are visual-only separators: dropped.
        elif t == "thematic_break":
            continue
    return blocks


def _split_paragraph(children: list[dict]) -> list[dict]:
    """Turn a paragraph's inline children into blocks, peeling out images.

    Text/formatting runs accumulate into ``paragraph`` blocks; each ``image``
    token becomes its own ``image`` block, preserving document order.
    """
    blocks = []
    buffer = []

    def flush():
        if buffer:
            blocks.append({"type": "paragraph", "children": list(buffer)})
            buffer.clear()

    for c in children or []:
        if c and c.get("type") == "image":
            flush()
            blocks.append({
                "type": "image",
                "url": c.get("attrs", {}).get("url", ""),
                "alt": _extract_text(c.get("children", [])),
            })
        else:
            buffer.extend(_extract_runs([c]))
    flush()
    # An empty paragraph (e.g. a line that was only an image) yields no block.
    return blocks


def _parse_table(tok: dict) -> dict:
    head, body = tok["children"][0], tok["children"][1]
    headers = [_extract_text(c.get("children", [])) for c in head["children"]]
    rows = []
    for row in body["children"]:
        rows.append([_extract_text(c.get("children", [])) for c in row["children"]])
    return {"type": "table", "headers": headers, "rows": rows}


def _parse_list(tok: dict) -> dict:
    ordered = tok["attrs"].get("ordered", False)
    items = []
    for item in tok.get("children", []):
        runs = []
        for child in item.get("children", []):
            runs.extend(_extract_runs(child.get("children", [])))
        items.append(runs)
    return {"type": "list", "ordered": ordered, "items": items}


def _extract_text(children: list[dict]) -> str:
    parts = []
    for c in children or []:
        if not c:
            continue
        if c.get("children"):
            parts.append(_extract_text(c["children"]))
        else:
            parts.append(c.get("raw", ""))
    return "".join(parts)


def _extract_runs(children: list[dict], bold=False, italic=False, code=False) -> list[dict]:
    runs = []
    for c in children or []:
        if not c:
            continue
        t = c.get("type")
        if t == "strong":
            runs.extend(_extract_runs(c["children"], bold=True, italic=italic, code=code))
        elif t == "emphasis":
            runs.extend(_extract_runs(c["children"], bold=bold, italic=True, code=code))
        elif t == "codespan":
            runs.append({"text": c.get("raw", ""), "bold": bold, "italic": italic, "code": True})
        elif t == "link":
            text = _extract_text(c.get("children", []))
            runs.append({"text": text, "bold": bold, "italic": italic,
                         "link": c["attrs"].get("url", "")})
        elif t == "linebreak" or t == "softbreak":
            runs.append({"text": "\n"})
        elif c.get("children"):
            runs.extend(_extract_runs(c["children"], bold=bold, italic=italic, code=code))
        else:
            runs.append({"text": c.get("raw", ""), "bold": bold, "italic": italic, "code": code})
    return runs
