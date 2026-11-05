"""Version-bound Attempt survives a later quiz confirmation in real PostgreSQL."""

import pytest
from conftest import ScriptedModel
from test_m3_quiz_edit import request_for
from test_m3_quiz_edit_postgres import generated
from test_m3_quiz_postgres import USER, database_url, prepared  # noqa: F401

from final_review.formal_attempt import (
    read_formal_attempt,
    save_formal_answer,
    start_formal_attempt,
    submit_formal_attempt,
)
from final_review.formal_grading import grade_formal_attempt
from final_review.formal_result import ask_quiz_followup, read_formal_result
from final_review.quiz_edit import save_quiz_revision
from final_review.quiz_generation import read_quiz
from final_review.quiz_review import (
    confirm_quiz_revision,
    quiz_confirm_preview,
    review_quiz_revision,
)
from final_review.storage import StorageError, stable_key

pytestmark = pytest.mark.integration


def test_formal_attempt_can_finish_after_parent_is_superseded(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        asset_id, first_id = card["asset_id"], card["revision_id"]
        token = quiz_confirm_preview(store, str(USER), "course-1", asset_id, first_id)
        confirm_quiz_revision(
            store, str(USER), "course-1", asset_id, first_id, token["confirmation_id"]
        )
        attempt = start_formal_attempt(store, str(USER), "course-1", asset_id)
        attempt_id = attempt["attempt"]["attempt_id"]
        question_id = attempt["questions"][0]["question_revision_id"]

        first = read_quiz(store, str(USER), "course-1", asset_id, first_id)
        edited = save_quiz_revision(
            store,
            str(USER),
            "course-1",
            asset_id,
            request_for(first, stem="新版 TCP 题目"),
        )
        second_id = edited["revision"]["quiz_revision_id"]
        review_quiz_revision(
            store, str(USER), "course-1", asset_id, second_id, ScriptedModel(), "default"
        )
        token = quiz_confirm_preview(store, str(USER), "course-1", asset_id, second_id)
        confirm_quiz_revision(
            store, str(USER), "course-1", asset_id, second_id, token["confirmation_id"]
        )

        saved = save_formal_answer(
            store, str(USER), "course-1", attempt_id, question_id, "同步序列号", 0
        )
        assert saved["attempt"]["quiz_revision_id"] == first_id
        assert len(store.scan("attempt_answer", {"course_id": "course-1"})) == 1
        assert (
            submit_formal_attempt(store, str(USER), "course-1", attempt_id)["attempt"]["status"]
            == "submitted"
        )
        graded = grade_formal_attempt(
            store, str(USER), "course-1", attempt_id, ScriptedModel(), "default", "fixture-v1"
        )
        assert graded["attempt"]["grading_snapshot"]["total_score"] == 10
        grade_row = store.scan("question_grade", {"course_id": "course-1"})[0]
        grade_key = "question-grade-" + stable_key(attempt_id, question_id)[:32]
        with pytest.raises(StorageError):
            store.put("question_grade", grade_key, {**grade_row, "score": 0})
        assert read_formal_result(store, str(USER), "course-1", attempt_id)["items"][0][
            "references"
        ]
        followup = ask_quiz_followup(
            store,
            str(USER),
            "course-1",
            attempt_id,
            question_id,
            "为什么？",
            ScriptedModel(),
            "default",
            "fixture-v1",
        )
        assert followup["citations"]
        assert (
            read_formal_attempt(store, str(USER), "course-1", attempt_id)["questions"][0]["stem"]
            != "新版 TCP 题目"
        )
    finally:
        store.close()
