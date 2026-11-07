"""Course-scoped data tools for agent learning tasks."""

from .domain import DomainService
from .mistakes import list_mistakes


def report_snapshot(store, user_id: str, course_id: str) -> dict:
    DomainService(store, user_id).course(course_id)
    attempts = [
        item
        for item in store.scan("attempt", {"course_id": course_id})
        if item.get("user_id") == user_id and item.get("status") == "submitted"
    ]
    attempts.sort(
        key=lambda item: item.get("submitted_at") or item.get("created_at", ""),
        reverse=True,
    )
    topic_scores: dict[str, list[float]] = {}
    question_count = 0
    for attempt in attempts:
        assessment = attempt.get("assessment", {})
        questions = {
            question.get("id"): question for question in assessment.get("reference_questions", [])
        }
        grades = assessment.get("items", [])
        question_count += len(grades)
        for grade in grades:
            question = questions.get(grade.get("question_id"), {})
            topic = question.get("knowledge_point", "未分类")
            topic_scores.setdefault(topic, []).append(float(grade.get("score", 0)))
    topics = sorted(
        [
            {"name": name, "score": round(sum(scores) / len(scores), 1)}
            for name, scores in topic_scores.items()
        ],
        key=lambda item: item["score"],
    )
    return {
        "kind": "report",
        "attempt_count": len(attempts),
        "question_count": question_count,
        "average_score": round(
            sum(float(item.get("score", 0)) for item in attempts) / len(attempts), 1
        )
        if attempts
        else None,
        "topics": topics,
        "focus_topics": topics[:5],
    }


def mistakes_snapshot(store, user_id: str, course_id: str) -> dict:
    items = list_mistakes(store, user_id, course_id=course_id)["items"]
    counts: dict[str, int] = {}
    for item in items:
        topic = item.get("knowledge_point") or "未分类"
        counts[topic] = counts.get(topic, 0) + 1
    topics = sorted(
        [{"name": name, "mistake_count": count} for name, count in counts.items()],
        key=lambda item: (-item["mistake_count"], item["name"]),
    )
    return {
        "kind": "mistakes",
        "mistake_count": len(items),
        "unmastered_count": sum(item.get("mastery_status") != "mastered" for item in items),
        "focus_topics": topics[:5],
    }


def learning_task_snapshot(store, user_id: str, course_id: str, task: str) -> dict:
    if task not in {"report", "mistakes"}:
        raise ValueError("学习任务类型无效")
    report = report_snapshot(store, user_id, course_id)
    mistakes = mistakes_snapshot(store, user_id, course_id)
    return {"kind": task, "report": report, "mistakes": mistakes}


def learning_action_cards(snapshot: dict) -> list[dict]:
    cards = [
        {"label": "打开学习报告", "href": "#report", "kind": "link"},
        {"label": "查看错题", "href": "#mistakes", "kind": "link"},
    ]
    topics = list(
        dict.fromkeys(
            [item["name"] for item in snapshot["report"]["focus_topics"]]
            + [item["name"] for item in snapshot["mistakes"]["focus_topics"]]
        )
    )
    if topics:
        cards.append(
            {
                "label": "针对薄弱点出题",
                "kind": "quiz",
                "prompt": "按报告和错题中的薄弱知识点生成一份复习试卷。",
                "quiz_input": {"scope_mode": "knowledge_points", "knowledge_points": topics},
            }
        )
    return cards
