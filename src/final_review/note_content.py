"""Separate a recognizable legacy source appendix without rewriting saved notes."""

import re


def note_body(revision: dict) -> str:
    """Structured points are authoritative; provenance stays outside the body."""
    if revision.get("points"):
        lines = [f"# {revision['title']}"]
        for point in revision["points"]:
            lines.extend(["", f"## {point['heading']}", "", point["content"]])
        return "\n".join(lines)
    return split_source_appendix(revision["markdown"])[0]


def split_source_appendix(markdown: str) -> tuple[str, str]:
    fence = None
    offset = 0
    for line in markdown.splitlines(keepends=True):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        if fence is None and line.strip() == "## 出处与来源标记":
            appendix = markdown[offset:]
            # Only recognize the old generated appendix, not an arbitrary heading.
            if re.search(
                r"^(?:来源：|引用摘录：|历史笔记未保存逐条考点来源关系。)", appendix, re.MULTILINE
            ):
                body = markdown[:offset].rstrip()
                body = re.sub(r"\n(?:---|\*\*\*|___)\s*$", "", body).rstrip()
                return body, appendix
        offset += len(line)
    return markdown, ""
