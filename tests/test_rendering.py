import pytest

from final_review.agent import SessionConflict
from final_review.rendering import render_markdown
from final_review.schemas import AgentRequest, ResumeRequest, Submission


def test_question_answer_separation_and_old_round_rejection(system):
    request = AgentRequest(
        course_id="net",
        session_id="render",
        message="出题",
        intent="quiz",
        exam_profile={"question_types": ["short_answer"]},
    )
    quiz = system.invoke(request)
    pending = render_markdown(quiz)
    assert "## 题目" in pending and "参考答案" not in pending
    answers = {q["id"]: "不会" for q in quiz.questions}
    result = system.evaluate(Submission(course_id="net", session_id="render", answers=answers))
    text = render_markdown(result)
    assert text.index("## 题目") < text.index("## 答案与解析") < text.index("参考答案")
    assert "必须出现" in text and "常见失分点" in text
    quiz2 = system.invoke(request.model_copy(update={"exam_profile": None}))
    assert quiz2.status == "awaiting_answers"
    assert quiz2.questions[0]["id"] != quiz.questions[0]["id"]
    with pytest.raises(ValueError, match="ID"):
        system.evaluate(Submission(course_id="net", session_id="render", answers=answers))
    with pytest.raises(SessionConflict):
        system.resume_profile(
            ResumeRequest(
                course_id="net",
                session_id="render",
                exam_profile={"question_types": ["proof"]},
            )
        )
