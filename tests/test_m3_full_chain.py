"""One in-memory HTTP path from quiz generation through graded follow-up."""

from fastapi.testclient import TestClient
from test_m3_quiz_config import complete_input
from test_m3_quiz_config import quiz_system as quiz_system
from test_m3_quiz_edit import request_for

from final_review.api import create_app
from final_review.quiz_jobs import process_quiz_job


def test_generate_edit_confirm_attempt_grade_result_and_followup(quiz_system):
    system = quiz_system
    with TestClient(create_app(system.settings, system)) as client:
        queued = client.post(
            "/api/courses/net/quiz-jobs",
            json={"quiz_input": complete_input(total_score=10).model_dump(mode="json")},
        )
        assert queued.status_code == 202
        claimed = system.store.claim_quiz_job()
        assert claimed is not None
        process_quiz_job(system.store, system.model, system.settings, claimed)
        job = client.get(f"/api/courses/net/quiz-jobs/{queued.json()['job_id']}").json()
        assert job["status"] == "succeeded"
        card = job["result"]
        revision_root = (
            f"/api/courses/net/quizzes/{card['asset_id']}/revisions/{card['revision_id']}"
        )
        paper = client.get(revision_root).json()
        edited = client.post(
            f"/api/courses/net/quizzes/{card['asset_id']}/revisions",
            json=request_for(paper, stem="编辑后的 TCP 试题").model_dump(mode="json"),
        )
        assert edited.status_code == 200
        draft_id = edited.json()["revision"]["quiz_revision_id"]
        draft_root = f"/api/courses/net/quizzes/{card['asset_id']}/revisions/{draft_id}"
        assert client.post(draft_root + "/confirm-preview").status_code == 409
        reviewed = client.post(draft_root + "/review")
        assert reviewed.status_code == 200
        assert not reviewed.json()["revision"]["pending_semantic_review_question_ids"]
        preview = client.post(draft_root + "/confirm-preview")
        assert preview.status_code == 200
        confirmed = client.post(
            draft_root + "/confirm",
            json={"confirmation_id": preview.json()["confirmation_id"]},
        )
        assert confirmed.json()["asset"]["current_revision_id"] == draft_id
        assert (
            "答案与解析"
            not in client.get(draft_root + "/print?variant=questions").json()["markdown"]
        )

        started = client.post(f"/api/courses/net/quizzes/{card['asset_id']}/attempts")
        assert started.status_code == 200
        attempt_id = started.json()["attempt"]["attempt_id"]
        question_id = started.json()["questions"][0]["question_revision_id"]
        attempt_root = f"/api/courses/net/formal-attempts/{attempt_id}"
        assert "reference_answer" not in started.json()["questions"][0]
        saved = client.put(
            attempt_root + "/answers",
            json={
                "question_revision_id": question_id,
                "answer": "同步初始序列号",
                "expected_version": 0,
            },
        )
        assert saved.status_code == 200
        assert client.get(attempt_root).json()["attempt"]["answers"][question_id]["answer"]
        submitted = client.post(attempt_root + "/submit")
        assert submitted.status_code == 200
        assert submitted.json()["attempt"]["grading_snapshot"]["total_score"] == 10
        result = client.get(attempt_root + "/result")
        assert result.status_code == 200
        assert result.json()["items"][0]["references"]
        followup = client.post(
            attempt_root + f"/result/questions/{question_id}/follow-up",
            json={"message": "为什么需要同步序列号？"},
        )
        assert followup.status_code == 200
        assert followup.json()["citations"]
