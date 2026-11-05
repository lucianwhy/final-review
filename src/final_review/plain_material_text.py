"""Remove prose markup while preserving the contents and punctuation of code."""

import re


def plain_material_text(value: str) -> str:
    lines = []
    fenced = False
    for line in value.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
            continue
        if fenced:
            lines.append(line)
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*[▶►●•]\s*", "", line)
        line = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1（\2）", line)
        line = re.sub(r"(`+)([^`]+)\1", r"\2", line)
        line = re.sub(r"(\*\*|__)(.+?)\1", r"\2", line)
        if re.fullmatch(r"\s*(?:[-*_]\s*){3,}", line):
            continue
        if re.fullmatch(r"\s*<!--.*-->\s*", line):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
