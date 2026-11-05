import pytest

from final_review.agent import FinalReviewAgent, SessionConflict
from final_review.schemas import AgentRequest, ResumeRequest, Submission


def request(**kwargs):
    return AgentRequest(course_id="net", session_id="test", message="请出题", **kwargs)


def start_quiz(system):
    return system.invoke(request(intent="quiz", exam_profile={"question_types": ["short_answer"]}))


def answers_for(system, text="建立连接"):
    return {q["id"]: text for q in system.recover("net", "test").questions}


def test_missing_profile_interrupt_and_restart(system):
    assert system.invoke(request(intent="quiz")).status == "needs_input"
    # Reconstruct the graph AND saver. Recovery must use persisted data rather than object memory.
    restarted = FinalReviewAgent(system.store, system.kb, system.model, system.settings)
    result = restarted.resume_profile(
        ResumeRequest(
            course_id="net",
            session_id="test",
            exam_profile={"question_types": ["short_answer"]},
        )
    )
    assert result.status == "awaiting_answers"
    assert len(result.questions) == 1
    assert "reference_answer" not in result.questions[0]
    assert not result.citations  # evidence text could disclose the answer


def test_quiz_grade_feedback_and_idempotency(system):
    assert start_quiz(system).status == "awaiting_answers"
    submission = Submission(course_id="net", session_id="test", answers=answers_for(system))
    restarted = FinalReviewAgent(system.store, system.kb, system.model, system.settings)
    result = restarted.evaluate(submission)
    assert result.assessment["score"] == 30
    assert result.weak_points == ["三次握手"]
    assert result.assessment["reference_questions"][0]["reference_answer"]
    assert restarted.evaluate(submission) == result
    assert system.model.grading_calls == 1
    start_quiz(restarted)
    assert system.model.seen_weak_points == ["三次握手"]


def test_wrong_ids_do_not_consume_interrupt(system):
    start_quiz(system)
    with pytest.raises(ValueError, match="ID"):
        system.evaluate(Submission(course_id="net", session_id="test", answers={"wrong": "答案"}))
    assert system.recover("net", "test").status == "awaiting_answers"
    assert (
        system.evaluate(
            Submission(
                course_id="net",
                session_id="test",
                answers=answers_for(system),
            )
        ).status
        == "completed"
    )


def test_active_session_not_overwritten(system):
    start_quiz(system)
    with pytest.raises(SessionConflict):
        system.invoke(request(intent="ask"))


def test_provider_failure_recover_from_checkpoint(system):
    start_quiz(system)
    system.model.fail_grade_once = True
    with pytest.raises(RuntimeError, match="outage"):
        system.evaluate(Submission(course_id="net", session_id="test", answers=answers_for(system)))
    restarted = FinalReviewAgent(system.store, system.kb, system.model, system.settings)
    assert restarted.recover("net", "test").status == "completed"
    assert system.model.retrieval_calls == 1  # completed retrieval was not repeated


@pytest.mark.parametrize("bad_citations,unsupported", [(True, False), (False, True)])
def test_bounded_evidence_repair(system, bad_citations, unsupported):
    system.model.bad_citations = bad_citations
    system.model.unsupported = unsupported
    result = system.invoke(request(intent="ask"))
    assert result.status == "insufficient_evidence"
    assert system.model.retrieval_calls == 2
    assert not result.citations


def test_no_evidence_does_not_generate(system):
    result = system.invoke(
        AgentRequest(
            course_id="unrelated",
            session_id="test",
            message="解释 TCP",
            intent="ask",
        )
    )
    assert result.status == "insufficient_evidence"


def test_ask_has_grounded_citation(system):
    result = system.invoke(request(intent="ask"))
    assert result.status == "completed"
    assert result.answer and result.citations[0].course_id == "net"


def test_exam_types_are_enforced(system):
    result = system.invoke(request(intent="quiz", exam_profile={"question_types": ["proof"]}))
    assert result.status == "insufficient_evidence"


def test_checkpoint_history_and_pending_writes(system):
    start_quiz(system)
    key = system._key("net", "test")
    history = list(system.graph.checkpointer.list(system._config(key), limit=3))
    assert len(history) == 3
    assert history[0].parent_config
    assert history[0].pending_writes  # interrupt is durable too
    before = list(
        system.graph.checkpointer.list(
            system._config(key),
            before=history[1].config,
            limit=1,
        )
    )
    assert before[0].checkpoint["id"] < history[1].checkpoint["id"]
