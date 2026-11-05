"""Node: format_output — text (Q then A) or HTML (collapsible answers)."""

from __future__ import annotations

import html
from typing import Any

from final_review_agent.state import AgentState


def _format_text(state: AgentState) -> str:
    questions = state.get("questions") or []
    explanations = {e["id"]: e for e in (state.get("explanations") or [])}
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("期末复习 · 题目区")
    lines.append("=" * 60)
    lines.append("")

    for q in questions:
        lines.append(f"【{q['id']}】[{q.get('qtype', '')}] {q.get('kp', '')}")
        lines.append(q.get("stem", ""))
        for opt in q.get("options") or []:
            lines.append(f"  {opt}")
        lines.append(f"  ({q.get('source_tag', '')})")
        lines.append("")

    lines.append("")
    lines.append("=" * 60)
    lines.append("答案与解析区")
    lines.append("=" * 60)
    lines.append("")

    for q in questions:
        e = explanations.get(q["id"])
        lines.append(f"【{q['id']}】答案：{(e or {}).get('answer', '（无）')}")
        if e and e.get("explanation"):
            lines.append(e["explanation"])
        lines.append("")

    return "\n".join(lines)


def _format_html(state: AgentState) -> str:
    questions = state.get("questions") or []
    explanations = {e["id"]: e for e in (state.get("explanations") or [])}
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8"/>',
        "<title>期末复习输出</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;line-height:1.6}",
        "details{border:1px solid #ddd;border-radius:8px;padding:.75rem 1rem;margin:.75rem 0;background:#fafafa}",
        "summary{cursor:pointer;font-weight:600;color:#2563eb}",
        ".q{margin:1.5rem 0;padding-bottom:1rem;border-bottom:1px solid #eee}",
        ".tag{color:#666;font-size:.9em}",
        ".opts{margin:.5rem 0 .5rem 1.2rem}",
        "</style></head><body>",
        "<h1>期末复习 · 题目与折叠答案</h1>",
    ]
    for q in questions:
        e = explanations.get(q["id"])
        parts.append('<div class="q">')
        parts.append(
            f"<h3>{html.escape(q['id'])} · {html.escape(q.get('qtype', ''))}</h3>"
        )
        parts.append(f"<p>{html.escape(q.get('stem', ''))}</p>")
        if q.get("options"):
            parts.append('<ul class="opts">')
            for opt in q["options"]:
                parts.append(f"<li>{html.escape(opt)}</li>")
            parts.append("</ul>")
        parts.append(
            f'<p class="tag">{html.escape(q.get("source_tag", ""))} · '
            f'KP: {html.escape(q.get("kp", ""))}</p>'
        )
        ans = html.escape((e or {}).get("answer", "（无）"))
        exp = html.escape((e or {}).get("explanation", "")).replace("\n", "<br/>")
        parts.append("<details>")
        parts.append("<summary>点击展开答案与解析</summary>")
        parts.append(f"<p><strong>答案：</strong>{ans}</p>")
        parts.append(f"<p>{exp}</p>")
        parts.append("</details>")
        parts.append("</div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def format_output(state: AgentState) -> dict[str, Any]:
    fmt = (state.get("output_format") or "text").lower()
    if fmt == "html":
        out = _format_html(state)
    else:
        out = _format_text(state)
    return {
        "final_output": out,
        "messages": list(state.get("messages") or [])
        + [f"format_output: format={fmt}, chars={len(out)}"],
    }
