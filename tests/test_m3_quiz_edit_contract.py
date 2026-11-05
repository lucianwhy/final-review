"""M3-03's pure edit contract; persistence and semantic review come later."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from final_review.quiz_edit_contract import (
    QuizRevisionEdit,
    validate_quiz_edit,
    validate_quiz_edit_sources,
)
from final_review.schemas import Evidence


def question(kind="short_answer", question_revision_id="question-revision-1", order=1):
    value = {
        "question_revision_id": question_revision_id,
        "question_type": kind,
        "order": order,
        "score": 10,
        "stem": f"第 {order} 题：TCP 的作用是什么？",
        "explanation": "依据讲义解释。",
        "provenance": "source",
        "citations": [{"chunk_id": "chunk-1", "quote": "TCP 同步序列号"}],
    }
    if kind == "choice":
        value.update(options=["同步序列号", "忽略序列号"], correct_option=0)
    elif kind == "true_false":
        value["boolean_answer"] = True
    elif kind == "fill_blank":
        value["accepted_answers"] = ["同步序列号"]
    else:
        value["reference_answer"] = "同步初始序列号。"
    return value


def edit(*questions):
    return QuizRevisionEdit(base_revision_id="revision-1", questions=list(questions))


def base(value, reference_answer="原有模型答案"):
    result = deepcopy(value)
    result.setdefault("options", [])
    result.setdefault("correct_option", None)
    result.setdefault("boolean_answer", None)
    result.setdefault("accepted_answers", [])
    result["reference_answer"] = value.get("reference_answer", reference_answer)
    return result


def evidence(chunk_id="chunk-1", document_id="document-1", course_id="course-1"):
    return Evidence(
        chunk_id=chunk_id,
        document_id=document_id,
        title="TCP 讲义",
        course_id=course_id,
        chapter="TCP",
        source_type="teacher_ppt",
        content="TCP 同步序列号，并检查收发能力。",
        similarity=1,
    )


@pytest.mark.parametrize(
    "kind", ["choice", "true_false", "fill_blank", "short_answer", "calculation", "proof"]
)
def test_all_existing_question_types_have_valid_edit_shape(kind):
    value = question(kind)
    result = validate_quiz_edit(edit(value), [base(value)])
    assert result.total_score == 10
    assert not result.confirmation_issues
    assert not result.semantic_review_question_ids


@pytest.mark.parametrize(
    "change,match",
    [
        ({"options": ["A. 同步", "B. 同步"]}, "不同的非空选项"),
        ({"options": ["A. ", "B. 忽略"]}, "不同的非空选项"),
        ({"correct_option": 2}, "有效正确选项"),
        ({"boolean_answer": True}, "不能包含判断"),
        ({"reference_answer": "错误的独立显示答案"}, "不能单独提交"),
        ({"score": True}, "分值必须是数字"),
        ({"score": float("nan")}, "finite"),
    ],
)
def test_choice_rejects_invalid_options_answers_and_score(change, match):
    with pytest.raises(ValidationError, match=match):
        edit({**question("choice"), **change})


@pytest.mark.parametrize(
    "kind,change,match",
    [
        ("true_false", {"boolean_answer": None}, "需要布尔答案"),
        ("fill_blank", {"accepted_answers": []}, "需要不重复"),
        ("fill_blank", {"accepted_answers": ["A", "a"]}, "需要不重复"),
        ("short_answer", {"reference_answer": None}, "需要参考答案"),
        ("proof", {"options": ["A", "B"]}, "主观题不能包含"),
        ("short_answer", {"order": True}, "valid integer"),
    ],
)
def test_other_types_reject_inconsistent_answers(kind, change, match):
    with pytest.raises(ValidationError, match=match):
        edit({**question(kind), **change})


def test_structured_answer_changes_derive_display_answer_and_require_review():
    before = question("choice")
    after = {**before, "options": ["同步序列号", "更新后的错误项"], "correct_option": 1}
    result = validate_quiz_edit(edit(after), [base(before)])
    assert result.questions[0]["reference_answer"] == "B. 更新后的错误项"
    assert result.semantic_review_question_ids == [before["question_revision_id"]]
    assert (
        validate_quiz_edit(
            edit({**question("true_false"), "boolean_answer": False}),
            [base(question("true_false"))],
        ).questions[0]["reference_answer"]
        == "错误"
    )
    assert (
        validate_quiz_edit(
            edit({**question("fill_blank"), "accepted_answers": ["A", "B"]}),
            [base(question("fill_blank"))],
        ).questions[0]["reference_answer"]
        == "A；B"
    )


def test_score_and_order_can_be_saved_without_changing_source_review_status():
    first = question(order=1)
    second = question(question_revision_id="question-revision-2", order=2)
    result = validate_quiz_edit(
        edit({**first, "order": 2, "score": 15}, {**second, "order": 1, "score": 5}),
        [base(first), base(second)],
        expected_total_score=20,
    )
    assert result.total_score == 20
    assert not result.confirmation_issues
    assert not result.semantic_review_question_ids
    mismatch = validate_quiz_edit(
        edit({**first, "score": 15}), [base(first)], expected_total_score=10
    )
    assert mismatch.total_score == 15
    assert mismatch.confirmation_issues  # Draft may be saved; confirmation is blocked.
    assert not validate_quiz_edit(edit({**first, "score": 15}), [base(first)]).confirmation_issues


@pytest.mark.parametrize("problem", ["missing", "duplicate", "foreign", "type", "order", "stem"])
def test_edit_preserves_entire_paper_and_question_identity(problem):
    first = question(order=1)
    second = question(question_revision_id="question-revision-2", order=2)
    values = [first, second]
    if problem == "missing":
        values = [first]
    elif problem == "duplicate":
        values = [first, first]
    elif problem == "foreign":
        values = [first, {**second, "question_revision_id": "another-paper-question"}]
    elif problem == "type":
        values = [first, {**second, "question_type": "proof"}]
    elif problem == "order":
        values = [first, {**second, "order": 3}]
    else:
        values = [first, {**second, "stem": first["stem"]}]
    with pytest.raises(ValueError):
        validate_quiz_edit(edit(*values), [base(first), base(second)])


def test_source_shape_and_server_evidence_are_required():
    first = question()
    source = {"chunk-1": evidence()}
    validate_quiz_edit_sources(edit(first), source, course_id="course-1", allow_ai_supplement=False)
    for bad_source in ({}, {"chunk-1": evidence(course_id="other-course")}):
        with pytest.raises(ValueError, match="来源片段不可用"):
            validate_quiz_edit_sources(
                edit(first), bad_source, course_id="course-1", allow_ai_supplement=False
            )
    with pytest.raises(ValueError, match="摘录与原文不符"):
        validate_quiz_edit_sources(
            edit({**first, "citations": [{"chunk_id": "chunk-1", "quote": "伪造的原文"}]}),
            source,
            course_id="course-1",
            allow_ai_supplement=False,
        )


def test_source_count_and_ai_supplement_follow_existing_categories():
    first = question()
    source = {
        "chunk-1": evidence(),
        "chunk-2": evidence(chunk_id="chunk-2", document_id="document-2"),
    }
    two_refs = [
        {"chunk_id": "chunk-1", "quote": "TCP 同步序列号"},
        {"chunk_id": "chunk-2", "quote": "TCP 同步序列号"},
    ]
    with pytest.raises(ValueError, match="一份资料"):
        validate_quiz_edit_sources(
            edit({**first, "citations": two_refs}),
            source,
            course_id="course-1",
            allow_ai_supplement=False,
        )
    validate_quiz_edit_sources(
        edit({**first, "provenance": "synthesis", "citations": two_refs}),
        source,
        course_id="course-1",
        allow_ai_supplement=False,
    )
    ai = edit({**first, "provenance": "ai_supplement", "citations": []})
    with pytest.raises(ValueError, match="不允许 AI 补充"):
        validate_quiz_edit_sources(ai, {}, course_id="course-1", allow_ai_supplement=False)
    validate_quiz_edit_sources(ai, {}, course_id="course-1", allow_ai_supplement=True)
    with pytest.raises(ValidationError, match="不能附资料引用"):
        edit({**first, "provenance": "ai_supplement"})


def test_client_cannot_supply_source_label_or_change_unlisted_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        edit({**question(), "source_label": "历年真题"})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        edit({**question(), "knowledge_point": "另一个考点"})
