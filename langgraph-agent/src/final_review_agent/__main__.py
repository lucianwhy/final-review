"""CLI: python -m final_review_agent"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from final_review_agent.config import PROJECT_ROOT, get_settings
from final_review_agent.graph import run_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Final-review LangGraph agent (local skill + optional remote)"
    )
    parser.add_argument("--input", "-i", type=str, help="Input file path (md/pdf/...)")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with demos/sample_notes.md (works without API key)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "html"],
        default=None,
        help="Output format (default from OUTPUT_FORMAT env)",
    )
    parser.add_argument(
        "--skill-url",
        type=str,
        default=None,
        help="Force remote SKILL.md URL (overrides local SKILL.md)",
    )
    parser.add_argument(
        "--agent-url",
        type=str,
        default=None,
        help="Force remote AGENT.md URL (overrides local AGENT.md)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Write final output to this file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    output_format = args.format or settings.output_format

    input_path = args.input
    input_text = None
    if args.demo:
        sample = PROJECT_ROOT / "demos" / "sample_notes.md"
        if not sample.is_file():
            print(f"Demo file missing: {sample}", file=sys.stderr)
            return 1
        input_path = str(sample)
        print(f"[demo] input={input_path}")
        print(f"[demo] mock_llm={settings.use_mock_llm}")

    if not input_path and not args.demo:
        parser.print_help()
        print("\nProvide --input PATH or --demo", file=sys.stderr)
        return 2

    result = run_agent(
        input_path=input_path,
        input_text=input_text,
        output_format=output_format,
        skill_url=args.skill_url,
        agent_url=args.agent_url,
        settings=settings,
    )

    # Progress trail
    for msg in result.get("messages") or []:
        print(f"  · {msg}", file=sys.stderr)

    if result.get("errors"):
        for err in result["errors"]:
            print(f"  ! {err}", file=sys.stderr)

    final = result.get("final_output") or ""
    print(final)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(final, encoding="utf-8")
        print(f"\n[wrote] {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
