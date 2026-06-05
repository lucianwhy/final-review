#!/usr/bin/env python3
"""
Build tutoring context for a knowledge point.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

try:
    from .utils import load_exam_profile, load_knowledge_base, load_preferences, load_quiz
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from utils import load_exam_profile, load_knowledge_base, load_preferences, load_quiz


def _fix_encoding() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            pass


def extract_section(markdown: str, keyword: str) -> str:
    pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.M)
    matches = list(pattern.finditer(markdown))
    for index, match in enumerate(matches):
        title = match.group(2).strip()
        if keyword.lower() not in title.lower():
            continue
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        return markdown[start:end].strip()
    return ""


def recommend_question_count(match_count: int, section_length: int) -> tuple[int, str]:
    score = match_count * 2 + section_length // 500
    if score <= 2:
        return 2, "知识点较稀疏，建议出 1 到 2 题"
    if score <= 5:
        return 4, "知识点中等，建议出 2 到 4 题"
    return 6, "知识点较密集，建议出 4 到 6 题"


def build_context(subject_name: str, keyword: str) -> str:
    knowledge_base = load_knowledge_base(subject_name) or ""
    exam_profile = load_exam_profile(subject_name)
    preferences = load_preferences(subject_name)
    quiz = load_quiz(subject_name)

    section = extract_section(knowledge_base, keyword)
    matching_questions = [
        question for question in quiz
        if keyword.lower() in (question.get("topic", "") + " " + question.get("knowledge_point", "")).lower()
    ]
    question_limit, density_note = recommend_question_count(len(matching_questions), len(section))

    lines = [
        f"# 知识点辅导上下文：{keyword}",
        "",
        "## 考试蓝图",
        f"- 考试提醒：{exam_profile.get('exam_reminder', '') or '未配置'}",
        f"- 题型结构：{exam_profile.get('question_blueprint', {})}",
        f"- 程序题重点：{exam_profile.get('programming_focus', [])}",
        "",
        "## 学生偏好",
        f"- 偏好题型：{preferences.get('preferred_question_types', [])}",
        f"- 讲解详细度：{preferences.get('explanation_style', 'standard')}",
        f"- 辅导模式：{preferences.get('tutor_mode', '先讲后练')}",
        f"- HTML 提醒：{preferences.get('html_hint_enabled', True)}",
        "",
        "## 知识点密度判断",
        f"- 匹配题目数：{len(matching_questions)}",
        f"- 推荐题量上限：{question_limit}",
        f"- 说明：{density_note}",
        "",
        "## 知识库片段",
        section or "未找到对应知识库片段，请基于解析后的 Markdown 原文自行组织。",
        "",
        "## 输出协议",
        "1. 先讲知识点定义",
        "2. 再讲这个点可能怎么考",
        "3. 再拆单选、多选、判断、简答、程序题怎么用",
        f"4. 按密度出 1 到 {question_limit} 题，并直接给答案和解析",
    ]

    if preferences.get("html_hint_enabled", True):
        lines.append("5. 如果知识点复杂、流程长、图示多，提醒用户可额外导出 HTML 辅助理解")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build tutoring context for a knowledge point")
    parser.add_argument("subject_name", help="Subject name")
    parser.add_argument("keyword", help="Knowledge point keyword")
    return parser


def main() -> None:
    _fix_encoding()
    args = build_parser().parse_args()
    print(build_context(args.subject_name, args.keyword))


if __name__ == "__main__":
    main()
