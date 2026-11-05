"""Node: convert_markdown — markitdown adapter or accept pre-markdown."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from final_review_agent.state import AgentState

logger = logging.getLogger(__name__)


def convert_markdown(state: AgentState) -> dict[str, Any]:
    messages = list(state.get("messages") or [])
    errors = list(state.get("errors") or [])

    # Already have markdown text (demo / pre-converted)
    if state.get("input_text") and not state.get("input_path"):
        messages.append("convert_markdown: using provided input_text")
        return {"raw_markdown": state["input_text"], "messages": messages}

    input_path = state.get("input_path")
    if not input_path:
        # Fall back to any existing raw_markdown
        if state.get("raw_markdown"):
            messages.append("convert_markdown: raw_markdown already present")
            return {"messages": messages}
        msg = "convert_markdown: no input_path / input_text — empty stub"
        errors.append(msg)
        return {
            "raw_markdown": "# (空材料)\n请提供 Markdown 或文件路径。\n",
            "messages": messages + [msg],
            "errors": errors,
        }

    path = Path(input_path)
    if not path.is_file():
        msg = f"convert_markdown: file not found: {input_path}"
        errors.append(msg)
        return {
            "raw_markdown": f"# 文件未找到\n`{input_path}`\n",
            "messages": messages + [msg],
            "errors": errors,
        }

    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        messages.append(f"convert_markdown: read text file {path.name}")
        return {"raw_markdown": text, "messages": messages}

    # Try markitdown CLI
    markitdown = shutil.which("markitdown")
    if markitdown:
        try:
            result = subprocess.run(
                [markitdown, str(path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                messages.append(f"convert_markdown: markitdown OK ({path.name})")
                return {"raw_markdown": result.stdout, "messages": messages}
            errors.append(
                f"markitdown failed rc={result.returncode}: {result.stderr[:500]}"
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"markitdown error: {exc}")

    stub = (
        f"# 转换占位：{path.name}\n\n"
        f"> markitdown CLI 未安装或转换失败。\n"
        f"> 请安装 `pip install markitdown` 后重试，或先手工转为 Markdown，\n"
        f"> 再使用 `--input your.md`。\n\n"
        f"原始文件后缀：`{suffix}`\n"
    )
    messages.append(
        "convert_markdown: markitdown missing/failed — stub markdown emitted"
    )
    return {"raw_markdown": stub, "messages": messages, "errors": errors}
