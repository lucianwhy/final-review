"""M3-03 save-only proofs against an explicitly disposable PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import ScriptedModel
from test_m3_quiz_edit import request_for
from test_m3_quiz_postgres import USER, database_url, prepared  # noqa: F401

from final_review.config import Settings
from final_review.domain import DomainConflict, DomainNotFound
from final_review.postgres import PostgresStore
from final_review.quiz_edit import save_quiz_revision
from final_review.quiz_generation import read_quiz
from final_review.quiz_jobs import enqueue_quiz, process_quiz_job
from final_review.quiz_print import print_quiz_revision
from final_review.quiz_review import (
    confirm_quiz_revision,
    quiz_confirm_preview,
    review_quiz_revision,
)
from final_review.storage import StorageError

pytestmark = pytest.mark.integration


def generated(store, config):
    queued = enqueue_quiz(store, config, model_id="default")
    job = store.get("quiz_job", queued["job_id"])
    # This suite exercises revision persistence, not host/container clock agreement.
    job["available_at"] = "2000-01-01T00:00:00+00:00"
    store.put("quiz_job", queued["job_id"], job)
    claimed = store.claim_quiz_job()
    assert claimed is not None
    process_quiz_job(store, ScriptedModel(), Settings(_env_file=None), claimed)
    job = store.get("quiz_job", queued["job_id"])
    assert job["status"] == "succeeded", job["error"]
    return job["result"]


def test_real_database_preserves_old_revision_questions_sources_and_current_pointer(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        before = read_quiz(store, str(USER), "course-1", card["asset_id"], card["revision_id"])
        old_question = before["revision"]["questions"][0]
        old_row = store.get("question_revision", old_question["question_revision_id"])
        original_refs = store.scan("source_reference", {"course_id": "course-1"})
        saved = save_quiz_revision(
            store,
            str(USER),
            "course-1",
            card["asset_id"],
            request_for(before, stem="编辑后：说明 TCP 如何同步序列号？"),
        )
        new_id = saved["revision"]["quiz_revision_id"]
        new_question = saved["revision"]["questions"][0]
        assert new_id != card["revision_id"]
        assert saved["asset"]["current_revision_id"] is None
        assert saved["asset"]["latest_revision_id"] == new_id
        assert new_question["source_review_status"] == "pending"
        assert new_question["references"] == []
        assert store.get("question_revision", old_question["question_revision_id"]) == old_row
        assert (
            read_quiz(store, str(USER), "course-1", card["asset_id"], card["revision_id"])[
                "revision"
            ]["questions"][0]["source_label"]
            == "老师PPT"
        )
        for row in original_refs:
            assert store.get("source_reference", row["source_reference_id"]) == row
        refs = [
            row
            for row in store.scan("source_reference", {"course_id": "course-1"})
            if row["revision_id"] == new_id
        ]
        assert len(refs) == 1
        assert refs[0]["question_revision_id"] == new_question["question_revision_id"]
        with pytest.raises(StorageError):
            store.put(
                "question_revision", old_row["question_revision_id"], {**old_row, "stem": "篡改"}
            )
        with pytest.raises(DomainConflict, match="已有更新"):
            save_quiz_revision(store, str(USER), "course-1", card["asset_id"], request_for(before))
        with pytest.raises(DomainNotFound):
            save_quiz_revision(
                store, "other-user", "course-1", card["asset_id"], request_for(before)
            )
    finally:
        store.close()


def test_real_database_row_lock_allows_one_save_from_same_base(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        before = read_quiz(store, str(USER), "course-1", card["asset_id"], card["revision_id"])
        request = request_for(before, explanation="并发修改。")
    finally:
        store.close()

    def save(_):
        worker = PostgresStore(database_url)
        token = worker.bind_user(str(USER))
        try:
            try:
                return save_quiz_revision(worker, str(USER), "course-1", card["asset_id"], request)
            except DomainConflict as exc:
                return exc
        finally:
            worker.reset_user(token)
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, DomainConflict) for result in results) == 1
    check = PostgresStore(database_url)
    token = check.bind_user(str(USER))
    try:
        assert len(check.scan("quiz_revision_payload", {"course_id": "course-1"})) == 2
    finally:
        check.reset_user(token)
        check.close()


def test_real_database_rolls_back_partial_quiz_edit(database_url, monkeypatch):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        before = read_quiz(store, str(USER), "course-1", card["asset_id"], card["revision_id"])
        original_put = store.put

        def fail_source(table, key, value):
            if table == "source_reference" and value["revision_id"] != card["revision_id"]:
                raise RuntimeError("simulated source write failure")
            return original_put(table, key, value)

        monkeypatch.setattr(store, "put", fail_source)
        with pytest.raises(RuntimeError, match="simulated"):
            save_quiz_revision(
                store,
                str(USER),
                "course-1",
                card["asset_id"],
                request_for(before, stem="保存过程中失败的编辑"),
            )
        assert (
            store.get("learning_asset", card["asset_id"])["latest_revision_id"]
            == card["revision_id"]
        )
        assert len(store.scan("asset_revision", {"course_id": "course-1"})) == 1
        assert len(store.scan("quiz_revision_payload", {"course_id": "course-1"})) == 1
        assert len(store.scan("question_revision", {"course_id": "course-1"})) == 1
        assert len(store.scan("source_reference", {"course_id": "course-1"})) == 1
    finally:
        store.close()


def test_real_database_edit_from_confirmed_keeps_formal_revision_current(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        with store.transaction():
            revision = store.get("asset_revision", card["revision_id"])
            payload = store.get("quiz_revision_payload", card["revision_id"])
            asset = store.get("learning_asset", card["asset_id"])
            revision.update(state="confirmed", confirmed_at="2026-10-11T00:00:00+00:00")
            payload["state"] = "confirmed"
            asset.update(status="confirmed", current_revision_id=card["revision_id"])
            store.put("asset_revision", card["revision_id"], revision)
            store.put("quiz_revision_payload", card["revision_id"], payload)
            store.put("learning_asset", card["asset_id"], asset)
        before = read_quiz(store, str(USER), "course-1", card["asset_id"], card["revision_id"])
        saved = save_quiz_revision(
            store,
            str(USER),
            "course-1",
            card["asset_id"],
            request_for(before, score=12),
        )
        assert saved["asset"]["current_revision_id"] == card["revision_id"]
        assert saved["asset"]["latest_revision_id"] == saved["revision"]["quiz_revision_id"]
        assert saved["revision"]["state"] == "draft"
        assert store.get("asset_revision", card["revision_id"])["state"] == "confirmed"
        assert store.get("quiz_revision_payload", card["revision_id"])["state"] == "confirmed"
    finally:
        store.close()


def test_real_database_reviews_confirms_and_preserves_printable_history(database_url):  # noqa: F811
    store, config = prepared(database_url)
    try:
        card = generated(store, config)
        asset_id = card["asset_id"]
        first_id = card["revision_id"]
        first_token = quiz_confirm_preview(store, str(USER), "course-1", asset_id, first_id)
        confirm_quiz_revision(
            store, str(USER), "course-1", asset_id, first_id, first_token["confirmation_id"]
        )
        first_print = print_quiz_revision(
            store, str(USER), "course-1", asset_id, first_id, "questions"
        )["markdown"]
        before = read_quiz(store, str(USER), "course-1", asset_id, first_id)
        saved = save_quiz_revision(
            store,
            str(USER),
            "course-1",
            asset_id,
            request_for(before, stem="编辑后的 TCP 题干"),
        )
        second_id = saved["revision"]["quiz_revision_id"]
        with pytest.raises(DomainConflict, match="语义复核"):
            quiz_confirm_preview(store, str(USER), "course-1", asset_id, second_id)
        review_quiz_revision(
            store, str(USER), "course-1", asset_id, second_id, ScriptedModel(), "default"
        )
        second_token = quiz_confirm_preview(store, str(USER), "course-1", asset_id, second_id)
        confirm_quiz_revision(
            store, str(USER), "course-1", asset_id, second_id, second_token["confirmation_id"]
        )
        assert store.get("quiz_revision_payload", first_id)["state"] == "superseded"
        assert store.get("asset_revision", first_id)["state"] == "superseded"
        assert store.get("learning_asset", asset_id)["current_revision_id"] == second_id
        assert (
            print_quiz_revision(store, str(USER), "course-1", asset_id, first_id, "questions")[
                "markdown"
            ]
            == first_print
        )
    finally:
        store.close()
