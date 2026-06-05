#!/usr/bin/env python3
"""
Initialize exam blueprint and preferences for a subject.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

try:
    from .utils import save_exam_profile, save_preferences
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from utils import save_exam_profile, save_preferences


QUESTION_TYPES = [
    ("single_choice", "单选题数量"),
    ("multiple_choice", "多选题数量"),
    ("true_false", "判断题数量"),
    ("fill_blank", "填空题数量"),
    ("short_answer", "简答题数量"),
    ("programming", "程序题数量"),
]


def _fix_encoding() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            pass


def prompt_int(label: str) -> int:
    raw = input(f"{label} (留空表示 0): ").strip()
    return int(raw) if raw else 0


def prompt_list(label: str) -> list[str]:
    raw = input(f"{label} (用逗号分隔，可留空): ").strip()
    return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]


def interactive_bootstrap(subject_name: str) -> None:
    print(f"=== 初始化科目: {subject_name} ===")
    exam_reminder = input("考试提醒/老师强调内容: ").strip()
    programming_focus = prompt_list("程序题重点（如 配置, Linux命令, 思维导图）")

    blueprint = {}
    for key, label in QUESTION_TYPES:
        blueprint[key] = prompt_int(label)

    preferred_question_types = prompt_list("偏好题型（如 单选, 多选, 程序题）")
    explanation_style = input("讲解详细度（brief/standard/deep，默认 standard）: ").strip() or "standard"
    tutor_mode = input("辅导模式（如 先讲后练/先题后讲，默认 先讲后练）: ").strip() or "先讲后练"
    html_hint = input("复杂知识点是否提醒导出 HTML？(Y/n): ").strip().lower() not in {"n", "no"}

    exam_profile = {
        "exam_reminder": exam_reminder,
        "question_blueprint": blueprint,
        "programming_focus": programming_focus,
    }
    preferences = {
        "preferred_question_types": preferred_question_types,
        "explanation_style": explanation_style,
        "tutor_mode": tutor_mode,
        "html_hint_enabled": html_hint,
    }

    exam_profile_path = save_exam_profile(subject_name, exam_profile)
    preferences_path = save_preferences(subject_name, preferences)

    print("\n已保存:")
    print(f"  - {exam_profile_path}")
    print(f"  - {preferences_path}")
    print("\n考试蓝图:")
    print(json.dumps(exam_profile, ensure_ascii=False, indent=2))
    print("\n偏好:")
    print(json.dumps(preferences, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize a subject profile")
    parser.add_argument("subject_name", help="Subject name")
    return parser


def main() -> None:
    _fix_encoding()
    args = build_parser().parse_args()
    interactive_bootstrap(args.subject_name)


if __name__ == "__main__":
    main()
