"""Small synthetic live-model check for edited quiz review, grading and follow-up."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from final_review.config import Settings
from final_review.formal_grading import SubjectiveReview
from final_review.formal_result import QuizFollowUpReply
from final_review.llm import build_review_model
from final_review.quiz_contract import QuizSemanticReview


def main():
    parser = argparse.ArgumentParser(description="M3 合成 TCP 题真实模型小样本")
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings()
    selected = settings.get_chat_model(args.model_id)
    model = build_review_model(selected, settings, quiz_generation=True)
    quote = "TCP 建立连接的三次握手用于同步双方初始序列号，并确认双方收发能力。"
    question_id = "question-revision-synthetic-tcp"
    source = {
        "chunk_id": "synthetic-tcp-chunk",
        "document_id": "synthetic-tcp-document",
        "file_name": "合成 TCP 讲义",
        "source_type": "other_practice",
        "quote": quote,
    }
    review_context = {
        "config": {"difficulty": "basic", "allow_ai_supplement": False},
        "evidence": [{**source, "content": quote}],
        "review_question_ids": [question_id],
    }
    draft = {
        "title": "合成 TCP 测试卷",
        "questions": [
            {
                "question_revision_id": question_id,
                "question_type": "short_answer",
                "stem": "TCP 三次握手为什么要同步初始序列号？",
                "reference_answer": "让双方确认彼此的初始序列号，以便后续可靠传输。",
                "explanation": "三次握手会同步双方初始序列号并确认收发能力。",
                "must_include": ["同步双方初始序列号"],
                "score": 10,
                "citations": [{"chunk_id": source["chunk_id"], "quote": quote}],
            }
        ],
    }
    reviewed = QuizSemanticReview.model_validate(
        model.review_quiz_edit(review_context, draft)
    ).model_dump(mode="json")
    assert [item["question_id"] for item in reviewed["items"]] == [question_id]

    grading_context = {
        "quiz_revision_id": "revision-synthetic-tcp",
        "questions": [
            {
                "question_revision_id": question_id,
                "question_type": "short_answer",
                "stem": draft["questions"][0]["stem"],
                "student_answer": "三次握手会同步双方初始序列号。",
                "reference_answer": draft["questions"][0]["reference_answer"],
                "criteria": [[0, "同步双方初始序列号"]],
                "scoring_tips": "明确指出双方同步初始序列号。",
                "explanation": draft["questions"][0]["explanation"],
                "max_score": 10,
            }
        ],
    }
    graded = SubjectiveReview.model_validate(
        model.grade_formal_subjective(grading_context)
    ).model_dump(mode="json")
    assert [item["question_revision_id"] for item in graded["items"]] == [question_id]
    assert [item["criterion_id"] for item in graded["items"][0]["criteria"]] == [0]

    followup_context = {
        "quiz_revision_id": grading_context["quiz_revision_id"],
        "question_revision_id": question_id,
        "stem": draft["questions"][0]["stem"],
        "question_type": "short_answer",
        "student_answer": grading_context["questions"][0]["student_answer"],
        "reference_answer": draft["questions"][0]["reference_answer"],
        "explanation": draft["questions"][0]["explanation"],
        "feedback": graded["items"][0]["feedback"],
        "missing_points": [],
        "sources": [source],
    }
    reply = QuizFollowUpReply.model_validate(
        model.follow_up_quiz_question(followup_context, "如果只写‘建立连接’，为什么可能失分？")
    ).model_dump(mode="json")
    assert set(reply["cited_chunk_ids"]) <= {source["chunk_id"]}
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "model_id": selected.id,
        "model_name": selected.model,
        "synthetic_only": True,
        "edit_review": reviewed,
        "subjective_review": graded,
        "followup": reply,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "model_id": selected.id,
                "edit_supported": reviewed["items"][0]["supported"],
                "criterion_met": graded["items"][0]["criteria"][0]["met"],
                "followup_citations": reply["cited_chunk_ids"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
