"""Edited quiz review and confirmation keep draft and formal history separate."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_contract import draft_case as draft_case
from test_m3_quiz_edit import generated_quiz, read, request_for

from final_review.api import create_app
from final_review.domain import DomainConflict, DomainNotFound
from final_review.quiz_edit import save_quiz_revision
from final_review.quiz_print import build_quiz_markdown, build_quiz_sections, print_quiz_revision
from final_review.quiz_review import (
    confirm_quiz_revision,
    quiz_confirm_preview,
    review_quiz_revision,
)


class Reviewer:
    def __init__(self, supported=True):
        self.supported = supported
        self.calls = []

    def review_quiz_edit(self, context, draft):
        self.calls.append((context, draft))
        return {
            "items": [
                {
                    "question_id": question_id,
                    "supported": self.supported,
                    "issues": [] if self.supported else ["答案缺少资料支持"],
                }
                for question_id in context["review_question_ids"]
            ]
        }


def changed_draft(case):
    system, card = generated_quiz(case)
    original = read(system, card)
    changed = save_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        request_for(original, stem="编辑后的 TCP 题干是什么？"),
    )
    return system, card, original, changed


def test_changed_question_requires_review_before_confirmation(draft_case):
    system, card, original, changed = changed_draft(draft_case)
    revision_id = changed["revision"]["quiz_revision_id"]
    question_id = changed["revision"]["questions"][0]["question_revision_id"]
    with pytest.raises(DomainConflict, match="语义复核"):
        quiz_confirm_preview(system.store, "local-user", "net", card["asset_id"], revision_id)

    reviewer = Reviewer()
    reviewed = review_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        revision_id,
        reviewer,
        "fixture",
    )
    assert reviewer.calls[0][0]["review_question_ids"] == [question_id]
    assert reviewed["revision"]["pending_semantic_review_question_ids"] == []
    assert reviewed["revision"]["questions"][0]["references"]
    assert reviewed["revision"]["review_audit"][-1]["items"][0]["supported"]

    preview = quiz_confirm_preview(system.store, "local-user", "net", card["asset_id"], revision_id)
    confirmed = confirm_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        revision_id,
        preview["confirmation_id"],
    )
    assert confirmed["revision"]["state"] == "confirmed"
    assert confirmed["asset"]["current_revision_id"] == revision_id
    assert read(system, card)["revision"] == original["revision"]
    questions_only = print_quiz_revision(
        system.store, "local-user", "net", card["asset_id"], revision_id, "questions"
    )["markdown"]
    with_answers = print_quiz_revision(
        system.store, "local-user", "net", card["asset_id"], revision_id, "answers"
    )["markdown"]
    assert "## 题目" in questions_only
    assert "答案与解析" not in questions_only
    assert with_answers.index("## 题目") < with_answers.index("## 答案与解析")
    assert "**答案：**" in with_answers
    with pytest.raises(DomainConflict):
        confirm_quiz_revision(
            system.store,
            "local-user",
            "net",
            card["asset_id"],
            revision_id,
            preview["confirmation_id"],
        )


def test_failed_review_keeps_question_pending_and_full_audit(draft_case):
    system, card, _, changed = changed_draft(draft_case)
    revision_id = changed["revision"]["quiz_revision_id"]
    failed = review_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        revision_id,
        Reviewer(supported=False),
        "fixture",
    )
    assert failed["revision"]["pending_semantic_review_question_ids"]
    assert failed["revision"]["questions"][0]["source_review_status"] == "pending"
    with pytest.raises(DomainConflict, match="语义复核"):
        quiz_confirm_preview(system.store, "local-user", "net", card["asset_id"], revision_id)
    passed = review_quiz_revision(
        system.store,
        "local-user",
        "net",
        card["asset_id"],
        revision_id,
        Reviewer(),
        "fixture",
    )
    assert passed["revision"]["pending_semantic_review_question_ids"] == []
    assert len(passed["revision"]["review_audit"]) == 2


def test_review_does_not_publish_when_sources_change_during_model_call(draft_case):
    system, card, _, changed = changed_draft(draft_case)
    revision_id = changed["revision"]["quiz_revision_id"]
    before = deepcopy(system.store.get("quiz_revision_payload", revision_id))

    class SourceChangingReviewer(Reviewer):
        def review_quiz_edit(self, context, draft):
            document = system.store.get("document", "m3-ppt")
            document["parse_status"] = "failed"
            system.store.put("document", "m3-ppt", document)
            return super().review_quiz_edit(context, draft)

    with pytest.raises((DomainConflict, ValueError)):
        review_quiz_revision(
            system.store,
            "local-user",
            "net",
            card["asset_id"],
            revision_id,
            SourceChangingReviewer(),
            "fixture",
        )
    assert system.store.get("quiz_revision_payload", revision_id) == before


def test_confirmation_blocks_total_mismatch_and_wrong_owner(draft_case):
    system, card = generated_quiz(draft_case)
    with pytest.raises(DomainConflict, match="确认问题"):
        changed = save_quiz_revision(
            system.store,
            "local-user",
            "net",
            card["asset_id"],
            request_for(read(system, card), score=12),
        )
        quiz_confirm_preview(
            system.store,
            "local-user",
            "net",
            card["asset_id"],
            changed["revision"]["quiz_revision_id"],
        )
    with pytest.raises(DomainNotFound):
        quiz_confirm_preview(
            system.store, "other-user", "net", card["asset_id"], card["revision_id"]
        )


def test_review_and_confirmation_api(draft_case, monkeypatch):
    system, card, _, changed = changed_draft(draft_case)
    revision_id = changed["revision"]["quiz_revision_id"]
    monkeypatch.setattr(
        system.model, "review_quiz_edit", Reviewer().review_quiz_edit, raising=False
    )
    root = f"/api/courses/net/quizzes/{card['asset_id']}/revisions/{revision_id}"
    with TestClient(create_app(system.settings, system)) as client:
        assert client.post(root + "/review").status_code == 200
        preview = client.post(root + "/confirm-preview")
        assert preview.status_code == 200
        confirmed = client.post(
            root + "/confirm", json={"confirmation_id": preview.json()["confirmation_id"]}
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["revision"]["state"] == "confirmed"
        printed = client.get(root + "/print?variant=questions")
        assert printed.status_code == 200
        assert "答案与解析" not in printed.json()["markdown"]


def test_print_requires_confirmed_revision_and_uses_ordered_questions(draft_case):
    system, card = generated_quiz(draft_case)
    with pytest.raises(DomainConflict, match="请先确认"):
        print_quiz_revision(
            system.store,
            "local-user",
            "net",
            card["asset_id"],
            card["revision_id"],
            "questions",
        )
    paper = build_quiz_markdown(
        "TCP 卷",
        20,
        [
            {
                "order": 2,
                "question_type": "short_answer",
                "score": 10,
                "stem": "第二题",
                "options": [],
                "reference_answer": "答案二",
                "explanation": "解析二",
                "must_include": [],
            },
            {
                "order": 1,
                "question_type": "choice",
                "score": 10,
                "stem": "第一题",
                "options": ["A. TCP", "B. UDP"],
                "reference_answer": "B. UDP",
                "explanation": "解析一",
                "must_include": [],
            },
        ],
    )
    assert paper.index("第一题") < paper.index("第二题") < paper.index("答案与解析")
    assert "- A. TCP" in paper and "- B. UDP" in paper
    question_part, full = build_quiz_sections(
        "带标题的卷",
        10,
        [
            {
                "order": 1,
                "question_type": "short_answer",
                "score": 10,
                "stem": "题干包含 ## 答案与解析 字样",
                "options": [],
                "reference_answer": "真正答案",
                "explanation": "真正解析",
                "must_include": [],
            }
        ],
    )
    assert full.startswith(question_part)
    assert "真正答案" not in question_part
