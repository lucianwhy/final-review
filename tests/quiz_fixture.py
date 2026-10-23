"""Deterministic paper fixture for workflow and browser tests; no semantic quality claim."""

from decimal import Decimal


def fixture_plan(context):
    allocations = []
    counts = {}
    point_index = 0
    for item in context.config.blueprint:
        remaining = item.question_count
        while remaining:
            name = (context.config.knowledge_points or [f"TCP考点{i}" for i in range(1, 101)])[
                point_index
            ]
            count = min(remaining, 6 - counts.get(name, 0))
            allocations.append(
                {
                    "knowledge_point": name,
                    "question_type": item.question_type,
                    "question_count": count,
                    "importance": 5,
                    "allocation_reason": "测试资料中明确出现的考点。",
                }
            )
            counts[name] = counts.get(name, 0) + count
            remaining -= count
            if counts[name] == 6:
                point_index += 1
    return {"allocations": allocations}


def fixture_draft(context, plan):
    ref = context.evidence[0]
    total = Decimal(str(context.config.total_score or context.config.question_count * 10))
    unit = (total / context.config.question_count).quantize(Decimal("0.01"))
    questions = []
    for item in plan.allocations:
        for _ in range(item.question_count):
            order = len(questions) + 1
            global_order = (
                context.question_slots[order - 1]["order"] if context.question_slots else order
            )
            question = {
                "id": f"q{order}",
                "order": order,
                "question_type": item.question_type,
                "knowledge_point": item.knowledge_point,
                "stem": f"第{global_order}题：说明TCP同步序列号的作用。",
                "score": context.question_slots[order - 1]["score"]
                if context.question_slots
                else float(
                    total - unit * (order - 1) if order == context.config.question_count else unit
                ),
                "reference_answer": "同步初始序列号。",
                "explanation": "为可靠连接同步序列号。",
                "core_point": "序列号同步",
                "must_include": ["同步初始序列号"],
                "common_mistakes": ["遗漏同步序列号"],
                "scoring_tips": "说明序列号同步。",
                "provenance": "source",
                "citations": [{"chunk_id": ref.chunk_id, "quote": ref.content[:200]}],
            }
            if item.question_type == "choice":
                question.update(options=["同步初始序列号", "与序列号无关"], correct_option=0)
            elif item.question_type == "true_false":
                question.update(boolean_answer=True)
            elif item.question_type == "fill_blank":
                question.update(accepted_answers=["同步初始序列号"])
            questions.append(question)
    return {"title": "TCP 模拟试卷", "questions": questions}
