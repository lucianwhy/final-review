"""Review and confirm an edited formal quiz without rewriting earlier revisions."""

from copy import deepcopy

from pydantic import ValidationError

from .domain import DomainConflict, DomainNotFound, DomainService, _now
from .quiz_contract import QuizSemanticReview
from .quiz_edit import _current_evidence, _locked
from .quiz_edit_contract import QuizQuestionEdit, QuizRevisionEdit, validate_quiz_edit_sources
from .quiz_generation import paper_hash, read_quiz
from .quiz_print import build_quiz_sections

_OBJECTIVE = {"choice", "true_false", "fill_blank"}


def _draft(store, user_id, course_id, asset_id, revision_id):
    domain = DomainService(store, user_id)
    course = _locked(store, "course", course_id)
    if course is None or course.get("user_id") != user_id:
        raise DomainNotFound(course_id)
    domain.course(course_id, writable=True)
    asset = domain._locked_asset(asset_id)
    if asset.get("course_id") != course_id or asset.get("asset_type") != "quiz":
        raise DomainNotFound(asset_id)
    revision = domain._owned("asset_revision", revision_id)
    payload = domain._owned("quiz_revision_payload", revision_id)
    if (
        asset.get("status") in {"archived", "deleted"}
        or asset.get("latest_revision_id") != revision_id
        or revision.get("asset_id") != asset_id
        or revision.get("course_id") != course_id
        or revision.get("state") != "draft"
        or payload.get("asset_id") != asset_id
        or payload.get("course_id") != course_id
        or payload.get("asset_revision_id") != revision_id
        or payload.get("state") != "draft"
    ):
        raise DomainConflict("只能复核或确认本试卷的最新草稿")
    questions = []
    for slot in payload["questions"]:
        question = domain._owned("question_revision", slot["question_revision_id"])
        if (
            question.get("quiz_revision_id") != revision_id
            or question.get("course_id") != course_id
        ):
            raise DomainConflict("题目版本关系无效")
        questions.append({**question, **slot})
    return domain, asset, revision, payload, questions


def _evidence(store, user_id, course_id, revision_id, payload, questions):
    fields = set(QuizQuestionEdit.model_fields)
    submitted = []
    for question in questions:
        item = {key: question[key] for key in fields if key in question}
        if question["question_type"] in _OBJECTIVE:
            item.pop("reference_answer", None)
        submitted.append(item)
    edit = QuizRevisionEdit(base_revision_id=revision_id, questions=submitted)
    evidence, versions = _current_evidence(store, user_id, course_id, edit)
    validate_quiz_edit_sources(
        edit,
        evidence,
        course_id=course_id,
        allow_ai_supplement=payload["config"]["allow_ai_supplement"],
    )
    for question in questions:
        for ref in question["references"]:
            if versions.get(ref["chunk_id"]) != ref["material_version_id"]:
                raise DomainConflict("引用资料版本已变化，请重新编辑并复核")
    content = [
        {**item.model_dump(mode="json"), "material_version_id": versions[chunk_id]}
        for chunk_id, item in sorted(evidence.items())
    ]
    return content, paper_hash(content)


def review_quiz_revision(store, user_id, course_id, asset_id, revision_id, model, model_id):
    """Model-review only changed questions, then record a revision-scoped verdict."""
    with store.transaction():
        _, _, _, payload, questions = _draft(store, user_id, course_id, asset_id, revision_id)
        pending = list(payload.get("pending_semantic_review_question_ids", []))
        if not pending:
            return read_quiz(store, user_id, course_id, asset_id, revision_id)
        evidence, evidence_hash = _evidence(
            store, user_id, course_id, revision_id, payload, questions
        )
        content_hash = payload["content_hash"]
        context = {
            "config": payload["config"],
            "evidence": evidence,
            "review_question_ids": pending,
        }
        draft = {"title": payload["title"], "questions": deepcopy(questions)}

    try:
        verdict = QuizSemanticReview.model_validate(
            model.review_quiz_edit(context, draft)
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise DomainConflict("试卷语义复核结果结构无效") from exc
    ids = [item["question_id"] for item in verdict["items"]]
    if len(ids) != len(pending) or set(ids) != set(pending):
        raise DomainConflict("试卷语义复核遗漏或重复题目")
    failed = {item["question_id"] for item in verdict["items"] if not item["supported"]}

    with store.transaction():
        domain, _, _, payload, questions = _draft(store, user_id, course_id, asset_id, revision_id)
        if (
            payload["content_hash"] != content_hash
            or payload.get("pending_semantic_review_question_ids", []) != pending
        ):
            raise DomainConflict("试卷复核期间已有变化，请重新加载")
        _, current_hash = _evidence(store, user_id, course_id, revision_id, payload, questions)
        if current_hash != evidence_hash:
            raise DomainConflict("复核期间资料已变化，请重新复核")
        payload["pending_semantic_review_question_ids"] = [
            question_id for question_id in pending if question_id in failed
        ]
        payload.setdefault("review_audit", []).append(
            {
                "phase": "edit_review",
                "model_id": model_id,
                "reviewed_at": _now(),
                "evidence_hash": evidence_hash,
                "items": verdict["items"],
            }
        )
        payload["updated_at"] = _now()
        store.put("quiz_revision_payload", revision_id, payload)
        domain._audit(course_id, "quiz.edit_reviewed", "learning_asset", asset_id)
    return read_quiz(store, user_id, course_id, asset_id, revision_id)


def _confirmation_payload(asset, revision, payload, evidence_hash):
    return {
        "revision_id": revision["revision_id"],
        "latest_revision_id": asset["latest_revision_id"],
        "current_revision_id": asset.get("current_revision_id"),
        "content_hash": payload["content_hash"],
        "evidence_hash": evidence_hash,
        "review_hash": paper_hash(payload.get("review_audit", [])),
    }


def _confirmable(store, user_id, course_id, asset_id, revision_id):
    domain, asset, revision, payload, questions = _draft(
        store, user_id, course_id, asset_id, revision_id
    )
    if payload.get("pending_semantic_review_question_ids"):
        raise DomainConflict("请先完成已编辑题目的语义复核")
    if payload.get("confirmation_issues"):
        raise DomainConflict("请先处理试卷总分等确认问题")
    _, evidence_hash = _evidence(store, user_id, course_id, revision_id, payload, questions)
    return domain, asset, revision, payload, questions, evidence_hash


def quiz_confirm_preview(store, user_id, course_id, asset_id, revision_id):
    """Issue a short-lived confirmation token for the exact reviewed draft."""
    with store.transaction():
        domain, asset, revision, payload, _, evidence_hash = _confirmable(
            store, user_id, course_id, asset_id, revision_id
        )
        token = domain._confirmation(
            course_id,
            "quiz.confirm",
            "learning_asset",
            asset_id,
            _confirmation_payload(asset, revision, payload, evidence_hash),
        )
        return {
            "confirmation_id": token["confirmation_id"],
            "expires_at": token["expires_at"],
            "revision_id": revision_id,
            "replaces_confirmed": bool(asset.get("current_revision_id")),
            "question_count": payload["question_count"],
            "total_score": payload["total_score"],
        }


def confirm_quiz_revision(store, user_id, course_id, asset_id, revision_id, confirmation_id):
    """Atomically promote a reviewed draft while retaining old formal history."""
    with store.transaction():
        domain, asset, revision, payload, questions, evidence_hash = _confirmable(
            store, user_id, course_id, asset_id, revision_id
        )
        domain._consume(
            confirmation_id,
            action="quiz.confirm",
            resource_type="learning_asset",
            resource_id=asset_id,
            payload=_confirmation_payload(asset, revision, payload, evidence_hash),
        )
        previous_id = asset.get("current_revision_id")
        now = _now()
        previous = previous_payload = None
        if previous_id:
            previous = domain._owned("asset_revision", previous_id)
            previous_payload = domain._owned("quiz_revision_payload", previous_id)
            if previous.get("state") != "confirmed" or previous_payload.get("state") != "confirmed":
                raise DomainConflict("当前正式试卷版本状态无效")
        questions_markdown, full_markdown = build_quiz_sections(
            payload["title"], payload["total_score"], questions
        )
        revision.update(
            state="confirmed",
            confirmed_at=now,
            updated_at=now,
            markdown=full_markdown,
        )
        payload.update(
            state="confirmed", updated_at=now, print_question_length=len(questions_markdown)
        )
        store.put("asset_revision", revision_id, revision)
        store.put("quiz_revision_payload", revision_id, payload)
        asset.update(
            status="confirmed",
            current_revision_id=revision_id,
            latest_revision_id=revision_id,
            updated_at=now,
        )
        store.put("learning_asset", asset_id, asset)
        # The DB trigger requires the asset to point at the replacement before
        # its old confirmed revision may become superseded.
        if previous_id:
            previous["state"] = "superseded"
            previous_payload.update(state="superseded", updated_at=now)
            store.put("asset_revision", previous_id, previous)
            store.put("quiz_revision_payload", previous_id, previous_payload)
        domain._audit(course_id, "quiz.confirmed", "learning_asset", asset_id)
    return read_quiz(store, user_id, course_id, asset_id, revision_id)
