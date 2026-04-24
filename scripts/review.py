#!/usr/bin/env python3
"""
刷题引擎 - 交互式刷题、错题记录、薄弱点分析
"""
import sys
import io
import random
from collections import Counter
from .utils import load_quiz, load_wrong_answers, save_wrong_answers, load_progress, save_progress


def _fix_encoding():
    """修复 Windows 终端 UTF-8 编码"""
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            pass


class ReviewSession:
    """刷题会话"""

    def __init__(self, subject_name, mode="all"):
        self.subject_name = subject_name
        self.mode = mode  # all / wrong / weak
        self.quiz = load_quiz(subject_name) or []
        self.wrong_answers = load_wrong_answers(subject_name) or []
        self.progress = load_progress(subject_name) or {}

        # 根据模式筛选题目
        if mode == "wrong":
            # 错题模式：只刷错题
            wrong_ids = {w.get("question", {}).get("id") for w in self.wrong_answers}
            self.questions = [q for q in self.quiz if q.get("id") in wrong_ids]
        elif mode == "weak":
            # 薄弱点模式：只刷薄弱知识点的题
            weak_topics = self._get_weak_topics()
            self.questions = [
                q for q in self.quiz
                if q.get("topic", q.get("knowledge_point", "")) in weak_topics
            ]
        else:
            self.questions = list(self.quiz)

        # 恢复进度
        last_index = self.progress.get("last_index", 0)
        self.current_index = min(last_index, len(self.questions) - 1) if self.questions else 0
        self.session_total = 0
        self.session_correct = 0
        self.session_wrong = 0

    def _get_weak_topics(self, threshold=2):
        """获取薄弱知识点（错误次数 >= threshold）"""
        topic_counter = Counter()
        for w in self.wrong_answers:
            q = w.get("question", {})
            topic = q.get("topic", q.get("knowledge_point", "未分类"))
            topic_counter[topic] += 1
        return [t for t, c in topic_counter.items() if c >= threshold]

    def has_questions(self):
        return len(self.questions) > 0

    def current_question(self):
        """获取当前题目"""
        if 0 <= self.current_index < len(self.questions):
            return self.questions[self.current_index]
        return None

    def next_question(self):
        """进入下一题"""
        self.current_index += 1
        self._save_progress()
        return self.current_question()

    def jump_to(self, index):
        """跳转到指定题目"""
        if 0 <= index < len(self.questions):
            self.current_index = index
            self._save_progress()
            return self.current_question()
        return None

    def record_answer(self, is_correct, user_answer=""):
        """记录答题结果"""
        self.session_total += 1
        if is_correct:
            self.session_correct += 1
        else:
            self.session_wrong += 1
            question = self.current_question()
            if question:
                self.wrong_answers.append({
                    "timestamp": None,
                    "question": question,
                    "user_answer": user_answer,
                })
                save_wrong_answers(self.subject_name, self.wrong_answers)
        self._save_progress()

    def _save_progress(self):
        """保存进度"""
        progress = {
            "last_index": self.current_index,
            "total_answered": self.progress.get("total_answered", 0) + self.session_total,
            "total_correct": self.progress.get("total_correct", 0) + self.session_correct,
        }
        save_progress(self.subject_name, progress)

    def get_stats(self):
        """获取本次会话统计"""
        if self.session_total == 0:
            return None
        return {
            "total": self.session_total,
            "correct": self.session_correct,
            "wrong": self.session_wrong,
            "accuracy": round(self.session_correct / self.session_total * 100, 1),
            "remaining": len(self.questions) - self.current_index - 1,
        }

    def get_weak_topics(self, threshold=2):
        """获取薄弱知识点统计"""
        topic_stats = {}  # topic -> {total, wrong}
        for q in self.quiz:
            topic = q.get("topic", q.get("knowledge_point", "未分类"))
            if topic not in topic_stats:
                topic_stats[topic] = {"total": 0, "wrong": 0}
            topic_stats[topic]["total"] += 1

        for w in self.wrong_answers:
            q = w.get("question", {})
            topic = q.get("topic", q.get("knowledge_point", "未分类"))
            if topic in topic_stats:
                topic_stats[topic]["wrong"] += 1

        weak = []
        for topic, stats in topic_stats.items():
            if stats["wrong"] >= threshold:
                weak.append({
                    "topic": topic,
                    "total": stats["total"],
                    "wrong": stats["wrong"],
                    "accuracy": round((stats["total"] - stats["wrong"]) / stats["total"] * 100, 1) if stats["total"] > 0 else 0,
                })
        weak.sort(key=lambda x: x["accuracy"])
        return weak

    def get_questions_by_topic(self, topic, limit=5):
        """按知识点获取题目（用于加练）"""
        matching = [
            q for q in self.quiz
            if q.get("topic", q.get("knowledge_point", "")) == topic
        ]
        if len(matching) > limit:
            matching = random.sample(matching, limit)
        return matching

    def format_question(self, question):
        """格式化题目为字符串（用于命令行输出）"""
        lines = []
        qtype = question.get("type", "single_choice")
        type_names = {
            "single_choice": "单选",
            "true_false": "判断",
            "fill_blank": "填空",
            "short_answer": "简答",
        }
        tname = type_names.get(qtype, qtype)
        lines.append(f"【{tname}题】{question.get('question', '')}")

        if qtype == "single_choice" and "options" in question:
            for opt in question["options"]:
                lines.append(f"  {opt}")
        elif qtype == "true_false":
            lines.append("  A. 正确")
            lines.append("  B. 错误")

        lines.append(f"\n[知识点] {question.get('topic', question.get('knowledge_point', '未分类'))}")
        lines.append(f"[难度] {question.get('difficulty', 'medium')}")
        return "\n".join(lines)

    def format_answer(self, question, show_explanation=True):
        """格式化答案解析"""
        lines = []
        answer = question.get("answer", "")
        lines.append(f"正确答案: {answer}")
        if show_explanation:
            exp = question.get("explanation", "")
            if exp:
                lines.append(f"\n解析: {exp}")
        return "\n".join(lines)


def interactive_review(subject_name, mode="all"):
    """命令行交互式刷题（备用）"""
    session = ReviewSession(subject_name, mode)
    if not session.has_questions():
        print(f"科目 '{subject_name}' 没有可用题目。请先生成题库。")
        return

    print(f"===== 开始刷题: {subject_name} =====")
    print(f"模式: {mode} | 题目数: {len(session.questions)}\n")

    while True:
        q = session.current_question()
        if not q:
            break

        print(f"\n--- 第 {session.current_index + 1}/{len(session.questions)} 题 ---")
        print(session.format_question(q))
        user_input = input("\n你的答案 (输入 q 退出): ").strip()

        if user_input.lower() == "q":
            break

        # 判断对错（简化版）
        correct_answer = str(q.get("answer", "")).strip().lower()
        is_correct = user_input.lower() == correct_answer

        session.record_answer(is_correct, user_input)

        if is_correct:
            print("✅ 正确!")
        else:
            print("❌ 错误!")
        print(session.format_answer(q))

        q = session.next_question()

    # 结束统计
    stats = session.get_stats()
    if stats:
        print(f"\n===== 本次统计 =====")
        print(f"总答题: {stats['total']} | 正确: {stats['correct']} | 错误: {stats['wrong']}")
        print(f"正确率: {stats['accuracy']}%")


def main():
    _fix_encoding()
    if len(sys.argv) < 2:
        print("用法:")
        print("  python review.py <科目名> [mode]")
        print("  mode: all(全部) / wrong(错题) / weak(薄弱点)")
        sys.exit(1)

    subject = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "all"
    interactive_review(subject, mode)


if __name__ == "__main__":
    main()
