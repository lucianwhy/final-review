"""M4 mistake provenance and isolation against disposable PostgreSQL only."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import psycopg
import pytest
from conftest import ScriptedModel
from fastapi.testclient import TestClient
from test_m3_quiz_edit import request_for
from test_m3_quiz_edit_postgres import generated
from test_m3_quiz_postgres import USER, database_url, prepared  # noqa: F401
from test_postgres_migrations import SECOND_USER

from final_review.api import create_app
from final_review.config import Settings
from final_review.domain import DomainNotFound
from final_review.formal_attempt import start_formal_attempt, submit_formal_attempt
from final_review.formal_grading import grade_formal_attempt
from final_review.mistakes import (
    list_mistakes,
    mark_mastery,
    read_mistake,
    start_practice,
    submit_practice,
    sync_mistakes_for_attempt,
)
from final_review.quiz_edit import save_quiz_revision
from final_review.quiz_generation import read_quiz
from final_review.quiz_review import (
    confirm_quiz_revision,
    quiz_confirm_preview,
    review_quiz_revision,
)
from final_review.storage import StorageError

pytestmark = pytest.mark.integration


def test_mistake_survives_new_revision_and_keeps_attempt_grade_immutable(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        first_id = card["revision_id"]
        token = quiz_confirm_preview(store, str(USER), "course-1", card["asset_id"], first_id)
        confirm_quiz_revision(
            store, str(USER), "course-1", card["asset_id"], first_id, token["confirmation_id"]
        )
        started = start_formal_attempt(store, str(USER), "course-1", card["asset_id"])
        attempt_id = started["attempt"]["attempt_id"]
        question_id = started["questions"][0]["question_revision_id"]
        old_question = store.get("question_revision", question_id)
        paper = read_quiz(store, str(USER), "course-1", card["asset_id"], first_id)
        changed = save_quiz_revision(
            store,
            str(USER),
            "course-1",
            card["asset_id"],
            request_for(paper, stem="新版 TCP 题目"),
        )
        second_id = changed["revision"]["quiz_revision_id"]
        review_quiz_revision(
            store,
            str(USER),
            "course-1",
            card["asset_id"],
            second_id,
            ScriptedModel(),
            "default",
        )
        token = quiz_confirm_preview(store, str(USER), "course-1", card["asset_id"], second_id)
        confirm_quiz_revision(
            store,
            str(USER),
            "course-1",
            card["asset_id"],
            second_id,
            token["confirmation_id"],
        )
        submit_formal_attempt(store, str(USER), "course-1", attempt_id)
        grade_formal_attempt(store, str(USER), "course-1", attempt_id, None, "default", "v1")
        mistakes = list_mistakes(store, str(USER), course_id="course-1")["items"]
        assert len(mistakes) == 1
        mistake = mistakes[0]
        assert mistake["quiz_revision_id"] == first_id
        assert mistake["question_revision_id"] == question_id
        assert mistake["question"]["stem"] == old_question["stem"]
        assert mistake["question"]["stem"] != "新版 TCP 题目"
        assert mistake["question"]["references"]
        attempt_before = deepcopy(store.get("attempt", attempt_id))
        grade_before = deepcopy(store.scan("question_grade", {"course_id": "course-1"}))
        assert sync_mistakes_for_attempt(store, str(USER), "course-1", attempt_id) == []
        mark_mastery(store, str(USER), "course-1", mistake["mistake_id"], "mastered")
        task = start_practice(store, str(USER), "course-1", mistake["mistake_id"])["practice"]
        submitted = submit_practice(
            store, str(USER), "course-1", task["practice_id"], "同步初始序列号"
        )
        assert submitted["practice"]["result"]["grading_mode"] == "self_review"
        assert store.get("attempt", attempt_id) == attempt_before
        assert store.scan("question_grade", {"course_id": "course-1"}) == grade_before
        assert store.get("mistake", mistake["mistake_id"])["mastery_status"] == "mastered"
        changed_provenance = {**store.get("mistake", mistake["mistake_id"]), "reason": "tampered"}
        with pytest.raises(StorageError):
            store.put("mistake", mistake["mistake_id"], changed_provenance)
        second_token = store.bind_user(str(SECOND_USER))
        try:
            assert store.get("mistake", mistake["mistake_id"]) is None
            assert store.scan("mistake", {"course_id": "course-1"}) == []
            with pytest.raises(DomainNotFound):
                read_mistake(store, str(SECOND_USER), "course-1", mistake["mistake_id"])
        finally:
            store.reset_user(second_token)
        with pytest.raises(DomainNotFound):
            read_mistake(store, str(USER), "other-course", mistake["mistake_id"])
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "INSERT INTO app_users(id,email,password_hash) VALUES (%s,%s,%s)",
                (SECOND_USER, "m4-other@example.test", "test-only"),
            )
            for user, session in [(USER, "m4-owner"), (SECOND_USER, "m4-other")]:
                connection.execute(
                    "INSERT INTO auth_sessions(session_hash,user_id,expires_at) VALUES (%s,%s,%s)",
                    (
                        sha256(session.encode()).hexdigest(),
                        user,
                        datetime.now(UTC) + timedelta(hours=1),
                    ),
                )
        settings = Settings(
            _env_file=None,
            database_url=database_url,
            auth_mode="database",
            llm_api_key="",
            embedding_api_key="",
        )
        with TestClient(create_app(settings)) as client:
            owner = {settings.auth_cookie_name: "m4-owner"}
            other = {settings.auth_cookie_name: "m4-other"}
            root = f"/api/courses/course-1/mistakes/{mistake['mistake_id']}"
            assert (
                client.get("/api/mistakes", cookies=owner).json()["items"][0]["mistake_id"]
                == mistake["mistake_id"]
            )
            assert client.get("/api/mistakes", cookies=other).json()["items"] == []
            assert client.get(root, cookies=other).status_code == 404
            assert (
                client.put(
                    root + "/mastery", cookies=other, json={"status": "unmastered"}
                ).status_code
                == 404
            )
            assert client.post(root + "/practices", cookies=other).status_code == 404
            assert client.get(root).status_code == 401
    finally:
        store.close()
