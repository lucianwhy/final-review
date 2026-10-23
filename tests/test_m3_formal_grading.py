"""Objective scores are deterministic; subjective feedback is audited and immutable."""

import pytest
from fastapi.testclient import TestClient
from quiz_fixture import fixture_draft, fixture_plan
from test_m3_formal_attempt import confirmed_case
from test_m3_quiz_config import complete_input
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case

from final_review.api import create_app
from final_review.course_chat import CourseMaterials
from final_review.domain import DomainConflict
from final_review.formal_attempt import (
    save_formal_answer,
    start_formal_attempt,
    submit_formal_attempt,
)
from final_review.formal_grading import (
    DISCLAIMER,
    SubjectiveReview,
    grade_formal_attempt,
    objective_correct,
)
from final_review.llm import ReviewModel
from final_review.quiz_config import resolve_quiz_config
from final_review.quiz_contract import QuizDraftPlan, build_quiz_context, validate_quiz_draft
from final_review.quiz_generation import publish_quiz
from final_review.quiz_review import confirm_quiz_revision, quiz_confirm_preview


class Grader:
    def __init__(self, met=True):
        self.met = met
        self.calls = 0

    def grade_formal_subjective(self, context):
        self.calls += 1
        return {
            "items": [
                {
                    "question_revision_id": item["question_revision_id"],
                    "criteria": [
                        {
                            "criterion_id": criterion_id,
                            "met": self.met,
                            "reason": "已覆盖" if self.met else "未覆盖",
                        }
                        for criterion_id, _ in item["criteria"]
                    ],
                    "feedback": "参考性反馈。",
                    "improvement_advice": "对照得分点补充答案。",
                }
                for item in context["questions"]
            ]
        }


def test_objective_rules_are_deterministic_and_type_strict():
    choice = {"question_type": "choice", "correct_option": 0}
    judgment = {"question_type": "true_false", "boolean_answer": False}
    blank = {"question_type": "fill_blank", "accepted_answers": ["ＴＣＰ", "传输控制协议"]}
    for _ in range(3):
        assert objective_correct(choice, 0)
        assert not objective_correct(choice, True)
        assert objective_correct(judgment, False)
        assert not objective_correct(judgment, 0)
        assert objective_correct(blank, " tcp ")
        assert not objective_correct(blank, "UDP")


def test_model_adapter_uses_structured_criterion_verdict():
    calls = []

    class Capture:
        def structured(self, schema, prompt, context):
            calls.append((schema, prompt, context))
            return {"items": []}

    context = {"quiz_revision_id": "revision-1", "questions": []}
    ReviewModel.grade_formal_subjective(Capture(), context)
    assert calls[0][0] is SubjectiveReview
    assert "criterion_id" in calls[0][1]
    assert calls[0][2] is context


def test_objective_only_paper_grades_without_model(quiz_system):
    system = quiz_system
    config = resolve_quiz_config(
        system.store,
        "local-user",
        "net",
        complete_input(
            blueprint=[{"question_type": "choice", "question_count": 1}], total_score=10
        ),
    ).config
    materials = CourseMaterials(system.store, "net", "local-user")
    evidence = [
        item.model_dump(mode="json") for item in materials.chunks(materials.select(["m3-ppt"]))
    ]
    context = build_quiz_context(system.store, config, evidence)
    plan = fixture_plan(context)
    candidate = fixture_draft(context, QuizDraftPlan.model_validate(plan))
    validated = validate_quiz_draft(context, plan, candidate)
    assert validated.valid
    card = publish_quiz(
        system.store,
        {
            "context": context,
            "plan": plan,
            "draft": validated.validated_draft,
            "coverage": {},
            "audit": [],
        },
        "objective-only",
        "fixture",
    )
    token = quiz_confirm_preview(
        system.store, "local-user", "net", card["asset_id"], card["revision_id"]
    )
    confirm_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        card["revision_id"],
        token["confirmation_id"],
    )
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    question_id = started["questions"][0]["question_revision_id"]
    save_formal_answer(system.store, "local-user", "net", attempt_id, question_id, 0, 0)
    submit_formal_attempt(system.store, "local-user", "net", attempt_id)
    graded = grade_formal_attempt(
        system.store, "local-user", "net", attempt_id, None, None, "none"
    )["attempt"]["grading_snapshot"]
    assert graded["total_score"] == 10
    assert graded["model_id"] is None
    assert graded["disclaimer"] is None


def test_subjective_grade_stores_rubric_model_and_feedback_once(draft_case):
    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    question_id = started["questions"][0]["question_revision_id"]
    save_formal_answer(
        system.store, "local-user", "net", attempt_id, question_id, "同步初始序列号", 0
    )
    submit_formal_attempt(system.store, "local-user", "net", attempt_id)
    grader = Grader(met=False)
    graded = grade_formal_attempt(
        system.store, "local-user", "net", attempt_id, grader, "fixture", "fixture-v1"
    )
    snapshot = graded["attempt"]["grading_snapshot"]
    assert snapshot["quiz_revision_id"] == card["revision_id"]
    assert snapshot["model_id"] == "fixture"
    assert snapshot["model_version"] == "fixture-v1"
    assert snapshot["disclaimer"] == DISCLAIMER
    assert snapshot["items"][0]["score"] == 0
    assert snapshot["items"][0]["missing_points"] == ["同步初始序列号"]
    assert snapshot["items"][0]["rubric"][0]["criterion"] == "同步初始序列号"
    grade_rows = system.store.scan("question_grade", {"course_id": "net"})
    assert len(grade_rows) == 1
    assert grade_rows[0]["question_revision_id"] == question_id
    assert grade_rows[0]["rubric"][0]["met"] is False
    assert (
        grade_formal_attempt(
            system.store, "local-user", "net", attempt_id, grader, "changed", "changed"
        )["attempt"]["grading_snapshot"]
        == snapshot
    )
    assert grader.calls == 1


def test_bad_subjective_review_does_not_create_grade_snapshot(draft_case):
    system, card = confirmed_case(draft_case)
    started = start_formal_attempt(system.store, "local-user", "net", card["asset_id"])
    attempt_id = started["attempt"]["attempt_id"]
    question_id = started["questions"][0]["question_revision_id"]
    save_formal_answer(system.store, "local-user", "net", attempt_id, question_id, "不完整答案", 0)
    submit_formal_attempt(system.store, "local-user", "net", attempt_id)

    class BadGrader:
        def grade_formal_subjective(self, context):
            return {
                "items": [
                    {
                        "question_revision_id": question_id,
                        "criteria": [{"criterion_id": 9, "met": True, "reason": "伪造得分点"}],
                        "feedback": "通过",
                        "improvement_advice": "无",
                    }
                ]
            }

    with pytest.raises(DomainConflict, match="得分点"):
        grade_formal_attempt(
            system.store, "local-user", "net", attempt_id, BadGrader(), "fixture", "v1"
        )
    assert system.store.get("attempt", attempt_id).get("grading_snapshot") is None


def test_submit_api_grades_and_freezes_feedback(draft_case, monkeypatch):
    system, card = confirmed_case(draft_case)
    monkeypatch.setattr(system.model, "grade_formal_subjective", Grader().grade_formal_subjective)
    with TestClient(create_app(system.settings, system)) as client:
        started = client.post(f"/api/courses/net/quizzes/{card['asset_id']}/attempts").json()
        attempt_id = started["attempt"]["attempt_id"]
        question_id = started["questions"][0]["question_revision_id"]
        root = f"/api/courses/net/formal-attempts/{attempt_id}"
        saved = client.put(
            root + "/answers",
            json={
                "question_revision_id": question_id,
                "answer": "同步初始序列号",
                "expected_version": 0,
            },
        )
        assert saved.status_code == 200
        submitted = client.post(root + "/submit")
        assert submitted.status_code == 200
        assert submitted.json()["attempt"]["grading_snapshot"]["total_score"] == 10
        assert (
            client.post(root + "/grade").json()["attempt"]["grading_snapshot"]
            == submitted.json()["attempt"]["grading_snapshot"]
        )
