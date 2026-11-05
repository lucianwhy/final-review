"""Persist an edited quiz as a new draft without changing any earlier revision."""

import json
from copy import deepcopy

from .course_chat import CourseMaterials
from .domain import DomainConflict, DomainNotFound, DomainService, _id, _now
from .policy import SOURCE_LABELS
from .quiz_edit_contract import QuizRevisionEdit, validate_quiz_edit, validate_quiz_edit_sources
from .quiz_generation import paper_hash, read_quiz
from .source_locators import material_version

_BODY_FIELDS = (
    "id",
    "order",
    "knowledge_point",
    "question_type",
    "stem",
    "options",
    "correct_option",
    "boolean_answer",
    "accepted_answers",
    "score",
    "reference_answer",
    "explanation",
    "core_point",
    "must_include",
    "common_mistakes",
    "scoring_tips",
    "provenance",
    "citations",
    "source_label",
    "source_types",
    "references",
)


def _locked(store, table, key):
    return getattr(store, "get_for_update", store.get)(table, key)


def _current_evidence(store, user_id, course_id, edit):
    """Resolve submitted chunk IDs from current ready documents under document locks."""
    wanted = {ref.chunk_id for question in edit.questions for ref in question.citations}
    if not wanted:
        return {}, {}
    candidates = set()
    for document in store.scan("document", {"course_id": course_id}):
        if document.get("user_id") != user_id:
            continue
        if any(
            chunk["chunk_id"] in wanted
            for chunk in store.list_material_chunks(document["document_id"])
        ):
            candidates.add(document["document_id"])
    evidence, versions = {}, {}
    for document_id in sorted(candidates):
        document = _locked(store, "document", document_id)
        if (
            document is None
            or document.get("user_id") != user_id
            or document.get("course_id") != course_id
            or document.get("parse_status") != "ready"
        ):
            continue
        chunks = [
            chunk
            for chunk in store.list_material_chunks(document_id)
            if chunk["chunk_id"] in wanted
            and chunk.get("user_id", user_id) == user_id
            and chunk.get("course_id") == course_id
        ]
        if not chunks:
            continue
        version = material_version(document)["material_version_id"]
        store.ensure_material_source(document)
        for chunk in chunks:
            if chunk["chunk_id"] in evidence:
                raise DomainConflict("来源片段 ID 重复，不能确定所属资料")
            evidence[chunk["chunk_id"]] = CourseMaterials.evidence(chunk, document)
            versions[chunk["chunk_id"]] = version
    return evidence, versions


def _references(question, evidence, versions):
    refs = []
    for citation in question["citations"]:
        item = evidence[citation["chunk_id"]]
        refs.append(
            {
                "chunk_id": item.chunk_id,
                "quote": citation["quote"],
                "document_id": item.document_id,
                "material_version_id": versions[item.chunk_id],
                "file_name": item.file_name,
                "source_type": item.source_type.value,
                "position_kind": item.position_kind,
                "position": item.position,
            }
        )
    return refs


def _source_label(question, refs):
    if question["provenance"] == "ai_supplement":
        return "AI补充"
    labels = sorted({SOURCE_LABELS[ref["source_type"]] for ref in refs})
    return (
        "综合改编（" + " + ".join(labels) + "）"
        if question["provenance"] == "synthesis"
        else "、".join(labels)
    )


def save_quiz_revision(store, user_id: str, course_id: str, asset_id: str, edit: QuizRevisionEdit):
    """Create one complete successor draft; return its owned read representation."""
    domain = DomainService(store, user_id)
    with store.transaction():
        course = _locked(store, "course", course_id)
        if course is None or course.get("user_id") != user_id:
            raise DomainNotFound(course_id)
        domain.course(course_id, writable=True)
        asset = domain._locked_asset(asset_id)
        if asset.get("course_id") != course_id or asset.get("asset_type") != "quiz":
            raise DomainNotFound(asset_id)
        if asset.get("status") in {"archived", "deleted"}:
            raise DomainConflict("此试卷不能继续编辑")
        if asset.get("latest_revision_id") != edit.base_revision_id:
            raise DomainConflict("试卷已有更新，请重新加载后编辑")
        base_revision = domain._owned("asset_revision", edit.base_revision_id)
        base_payload = domain._owned("quiz_revision_payload", edit.base_revision_id)
        if (
            base_revision.get("asset_id") != asset_id
            or base_revision.get("course_id") != course_id
            or base_payload.get("asset_id") != asset_id
            or base_payload.get("course_id") != course_id
            or base_payload.get("asset_revision_id") != edit.base_revision_id
            or not base_revision.get("quiz_contract_version")
            or base_revision.get("state") not in {"draft", "confirmed"}
            or base_payload.get("state") != base_revision["state"]
        ):
            raise DomainConflict("试卷基准版本不可编辑")

        original_questions = []
        for slot in base_payload["questions"]:
            question = domain._owned("question_revision", slot["question_revision_id"])
            if (
                question.get("quiz_revision_id") != edit.base_revision_id
                or question.get("course_id") != course_id
            ):
                raise DomainConflict("题目版本关系无效")
            original_questions.append({**question, **slot})
        result = validate_quiz_edit(
            edit, original_questions, expected_total_score=base_payload["config"].get("total_score")
        )
        evidence, versions = _current_evidence(store, user_id, course_id, edit)
        validate_quiz_edit_sources(
            edit,
            evidence,
            course_id=course_id,
            allow_ai_supplement=base_payload["config"]["allow_ai_supplement"],
        )

        now, revision_id = _now(), _id("revision")
        common = {"user_id": user_id, "course_id": course_id, "created_at": now}
        old_by_id = {question["question_revision_id"]: question for question in original_questions}
        previous_pending = set(base_payload.get("pending_semantic_review_question_ids", []))
        newly_changed = set(result.semantic_review_question_ids)
        questions, slots, source_rows, pending = [], [], [], []
        for value in sorted(result.questions, key=lambda row: row["order"]):
            old = old_by_id[value["question_revision_id"]]
            question_revision_id = _id("question-revision")
            refs = _references(value, evidence, versions)
            question = deepcopy(old)
            question.pop("order", None)
            question.pop("score", None)
            question.pop("content_hash", None)
            question.update({k: v for k, v in value.items() if k not in {"order", "score"}})
            question.update(
                {
                    **common,
                    "question_revision_id": question_revision_id,
                    "quiz_revision_id": revision_id,
                    "revision_no": old["revision_no"] + 1,
                    "edit_source": "student",
                    "references": refs,
                    "source_label": _source_label(value, refs),
                    "source_types": sorted({ref["source_type"] for ref in refs}),
                }
            )
            question["content_hash"] = paper_hash(question)
            questions.append(question)
            slots.append(
                {
                    "question_revision_id": question_revision_id,
                    "order": value["order"],
                    "score": value["score"],
                }
            )
            old_sources = sorted(
                (
                    ref["document_id"],
                    ref["material_version_id"],
                    ref["chunk_id"],
                    ref["quote"],
                    ref["source_type"],
                )
                for ref in old["references"]
            )
            new_sources = sorted(
                (
                    ref["document_id"],
                    ref["material_version_id"],
                    ref["chunk_id"],
                    ref["quote"],
                    ref["source_type"],
                )
                for ref in refs
            )
            if (
                value["question_revision_id"] in previous_pending | newly_changed
                or old_sources != new_sources
            ):
                pending.append(question_revision_id)
            for index, ref in enumerate(refs):
                source_rows.append(
                    {
                        **common,
                        **ref,
                        "source_reference_id": _id("source"),
                        "revision_id": revision_id,
                        "asset_revision_id": revision_id,
                        "question_revision_id": question_revision_id,
                        "locator_id": ref["chunk_id"],
                    }
                )

        body = {
            "config": base_payload["config"],
            "plan": base_payload["plan"],
            "draft": {
                "title": base_payload["title"],
                "questions": [
                    {
                        key: question[key] if key not in {"order", "score"} else slot[key]
                        for key in _BODY_FIELDS
                    }
                    for question, slot in zip(questions, slots, strict=True)
                ],
            },
        }
        source_ids = sorted({ref["document_id"] for ref in source_rows})
        revision = {
            **common,
            "revision_id": revision_id,
            "asset_id": asset_id,
            "revision_no": base_revision["revision_no"] + 1,
            "state": "draft",
            "title": base_payload["title"],
            "markdown": json.dumps(body, ensure_ascii=False, sort_keys=True),
            "source_document_ids": source_ids,
            "based_on_revision_id": edit.base_revision_id,
            "edit_source": "student",
            "quiz_contract_version": base_revision["quiz_contract_version"],
            "updated_at": now,
        }
        payload = {
            **deepcopy(base_payload),
            **common,
            "quiz_revision_id": revision_id,
            "asset_revision_id": revision_id,
            "state": "draft",
            "total_score": float(result.total_score),
            "questions": slots,
            "content_hash": paper_hash(body),
            "review_audit": [],
            "pending_semantic_review_question_ids": pending,
            "confirmation_issues": result.confirmation_issues,
            "based_on_revision_id": edit.base_revision_id,
            "edit_source": "student",
            "updated_at": now,
        }
        store.put("asset_revision", revision_id, revision)
        store.put("quiz_revision_payload", revision_id, payload)
        for question in questions:
            store.put("question_revision", question["question_revision_id"], question)
        for ref in source_rows:
            store.put("source_reference", ref["source_reference_id"], ref)
        asset.update(latest_revision_id=revision_id, updated_at=now)
        store.put("learning_asset", asset_id, asset)
        domain._audit(course_id, "quiz.edited", "learning_asset", asset_id)
        return read_quiz(store, user_id, course_id, asset_id, revision_id)
