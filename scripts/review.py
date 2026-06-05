#!/usr/bin/env python3
"""
Interactive quiz engine for final-review.
"""
from __future__ import annotations

import argparse
import io
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    from .utils import (
        load_exam_profile,
        load_progress,
        load_quiz,
        load_wrong_answers,
        save_progress,
        save_wrong_answers,
    )
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from utils import (
        load_exam_profile,
        load_progress,
        load_quiz,
        load_wrong_answers,
        save_progress,
        save_wrong_answers,
    )


SUBJECTIVE_TYPES = {"short_answer", "programming"}


def _fix_encoding() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            pass


def normalize_answer(value: str) -> str:
    tokens = [token.strip().upper() for token in value.replace("，", ",").split(",") if token.strip()]
    if len(tokens) > 1:
        return ",".join(sorted(tokens))
    return value.strip().upper()


class ReviewSession:
    def __init__(self, subject_name: str, mode: str = "all") -> None:
        self.subject_name = subject_name
        self.mode = mode
        self.quiz = load_quiz(subject_name) or []
        self.wrong_answers = load_wrong_answers(subject_name) or []
        self.progress = load_progress(subject_name) or {"last_index": 0, "total_answered": 0, "total_correct": 0}
        self.exam_profile = load_exam_profile(subject_name) or {}
        self.questions = self._select_questions(mode)
        self.current_index = min(self.progress.get("last_index", 0), max(len(self.questions) - 1, 0))
        self.base_total_answered = self.progress.get("total_answered", 0)
        self.base_total_correct = self.progress.get("total_correct", 0)
        self.session_total = 0
        self.session_correct = 0
        self.session_wrong = 0

    def _topic_key(self, question: dict) -> str:
        return question.get("topic") or question.get("knowledge_point") or "未分类"

    def _type_key(self, question: dict) -> str:
        return question.get("type", "single_choice")

    def _select_questions(self, mode: str) -> list[dict]:
        if mode == "wrong":
            wrong_ids = {entry.get("question", {}).get("id") for entry in self.wrong_answers}
            return [question for question in self.quiz if question.get("id") in wrong_ids]
        if mode == "weak":
            weak_topics = {item["topic"] for item in self.get_weak_topics()}
            return [question for question in self.quiz if self._topic_key(question) in weak_topics]
        if mode == "exam":
            return self._build_exam_paper()
        return list(self.quiz)

    def _build_exam_paper(self) -> list[dict]:
        blueprint = self.exam_profile.get("question_blueprint", {})
        if not blueprint:
            return list(self.quiz)

        pools: dict[str, list[dict]] = defaultdict(list)
        for question in self.quiz:
            pools[self._type_key(question)].append(question)

        selected: list[dict] = []
        for question_type, count in blueprint.items():
            pool = list(pools.get(question_type, []))
            if not pool or count <= 0:
                continue
            if len(pool) <= count:
                selected.extend(pool)
            else:
                selected.extend(random.sample(pool, count))

        if not selected:
            return list(self.quiz)

        selected.sort(key=lambda question: question.get("id", 0))
        return selected

    def has_questions(self) -> bool:
        return bool(self.questions)

    def current_question(self) -> dict | None:
        if 0 <= self.current_index < len(self.questions):
            return self.questions[self.current_index]
        return None

    def next_question(self) -> dict | None:
        self.current_index += 1
        self._save_progress()
        return self.current_question()

    def record_answer(self, is_correct: bool, user_answer: str = "") -> None:
        self.session_total += 1
        if is_correct:
            self.session_correct += 1
        else:
            self.session_wrong += 1
            question = self.current_question()
            if question:
                self.wrong_answers.append(
                    {
                        "timestamp": None,
                        "question": question,
                        "user_answer": user_answer,
                    }
                )
                save_wrong_answers(self.subject_name, self.wrong_answers)
        self._save_progress()

    def _save_progress(self) -> None:
        progress = {
            "last_index": self.current_index,
            "total_answered": self.base_total_answered + self.session_total,
            "total_correct": self.base_total_correct + self.session_correct,
        }
        save_progress(self.subject_name, progress)

    def get_stats(self) -> dict | None:
        if self.session_total == 0:
            return None
        return {
            "total": self.session_total,
            "correct": self.session_correct,
            "wrong": self.session_wrong,
            "accuracy": round(self.session_correct / self.session_total * 100, 1),
            "remaining": max(len(self.questions) - self.current_index - 1, 0),
        }

    def get_weak_topics(self, threshold: int = 2) -> list[dict]:
        total_by_topic: Counter = Counter()
        wrong_by_topic: Counter = Counter()

        for question in self.quiz:
            total_by_topic[self._topic_key(question)] += 1
        for entry in self.wrong_answers:
            wrong_by_topic[self._topic_key(entry.get("question", {}))] += 1

        weak_topics = []
        for topic, wrong_count in wrong_by_topic.items():
            total = total_by_topic.get(topic, 0)
            if wrong_count >= threshold and total:
                weak_topics.append(
                    {
                        "topic": topic,
                        "total": total,
                        "wrong": wrong_count,
                        "accuracy": round((total - wrong_count) / total * 100, 1),
                    }
                )
        return sorted(weak_topics, key=lambda item: item["accuracy"])

    def format_question(self, question: dict) -> str:
        type_names = {
            "single_choice": "单选",
            "multiple_choice": "多选",
            "true_false": "判断",
            "fill_blank": "填空",
            "short_answer": "简答",
            "programming": "程序",
        }
        question_type = question.get("type", "single_choice")
        lines = [f"【{type_names.get(question_type, question_type)}题】{question.get('question', '')}"]

        if question_type in {"single_choice", "multiple_choice"} and question.get("options"):
            lines.extend(f"  {option}" for option in question["options"])
        elif question_type == "true_false":
            lines.extend(["  A. 正确", "  B. 错误"])

        lines.append(f"\n[知识点] {self._topic_key(question)}")
        lines.append(f"[难度] {question.get('difficulty', 'medium')}")
        if question.get("exam_types"):
            lines.append(f"[可能题型] {', '.join(question['exam_types'])}")
        return "\n".join(lines)

    def format_answer(self, question: dict) -> str:
        lines = [f"参考答案: {question.get('answer', '')}"]
        explanation = question.get("explanation")
        if explanation:
            lines.append(f"\n解析: {explanation}")
        return "\n".join(lines)

    def check_objective_answer(self, question: dict, user_answer: str) -> bool:
        expected = question.get("answer", "")
        question_type = question.get("type", "single_choice")
        if question_type == "true_false":
            mapping = {"A": "TRUE", "B": "FALSE", "正确": "TRUE", "错误": "FALSE", "TRUE": "TRUE", "FALSE": "FALSE"}
            expected_normalized = mapping.get(str(expected).strip().upper(), normalize_answer(str(expected)))
            user_normalized = mapping.get(user_answer.strip().upper(), normalize_answer(user_answer))
            return expected_normalized == user_normalized
        return normalize_answer(str(expected)) == normalize_answer(user_answer)


def interactive_review(subject_name: str, mode: str = "all") -> None:
    session = ReviewSession(subject_name, mode)
    if not session.has_questions():
        print(f"科目 '{subject_name}' 没有可用题目。请先生成题库。")
        return

    print(f"===== 开始刷题: {subject_name} =====")
    print(f"模式: {mode} | 题目数: {len(session.questions)}\n")

    while True:
        question = session.current_question()
        if not question:
            break

        print(f"\n--- 第 {session.current_index + 1}/{len(session.questions)} 题 ---")
        print(session.format_question(question))
        user_input = input("\n你的答案 (输入 q 退出): ").strip()
        if user_input.lower() == "q":
            break

        question_type = question.get("type", "single_choice")
        if question_type in SUBJECTIVE_TYPES:
            print("\n" + session.format_answer(question))
            verdict = input("是否判为正确? (y/N): ").strip().lower()
            is_correct = verdict in {"y", "yes"}
        else:
            is_correct = session.check_objective_answer(question, user_input)
            print("✅ 正确!" if is_correct else "❌ 错误!")
            print(session.format_answer(question))

        session.record_answer(is_correct, user_input)
        session.next_question()

    stats = session.get_stats()
    if stats:
        print("\n===== 本次统计 =====")
        print(f"总答题: {stats['total']} | 正确: {stats['correct']} | 错误: {stats['wrong']}")
        print(f"正确率: {stats['accuracy']}% | 剩余: {stats['remaining']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive review session")
    parser.add_argument("subject_name", help="Subject name")
    parser.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=["all", "wrong", "weak", "exam"],
        help="Review mode",
    )
    return parser


def main() -> None:
    _fix_encoding()
    args = build_parser().parse_args()
    interactive_review(args.subject_name, args.mode)


if __name__ == "__main__":
    main()
