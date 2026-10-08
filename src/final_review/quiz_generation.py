"""Formal paper generation: bounded model calls and atomic draft publication."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

from pydantic import ValidationError

from .course_chat import CourseMaterials
from .domain import DomainConflict, DomainNotFound, DomainService
from .quiz_contract import (
    QuizDraftPlan,
    QuizSemanticReview,
    build_quiz_context,
    validate_quiz_draft,
    validate_quiz_plan,
)
from .storage import stable_key


class QuizGenerationError(ValueError):
    def __init__(self, message, issues=None, diagnostics=None):
        super().__init__(message)
        self.issues = issues or []
        self.diagnostics = diagnostics or {}


def locked(store, table, key):
    return getattr(store, "get_for_update", store.get)(table, key)


def paper_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def quiz_context(store, config):
    materials = CourseMaterials(store, config.course_id, config.user_id)
    documents = materials.select(config.source_document_ids, config.chapter)
    # Read real chunks fairly across files; never truncate a chunk and then claim a full quote.
    evidence, coverage = materials.read(documents, budget=60000)
    evidence = [item for item in evidence if item.source_type != "ai_supplement"][:80]
    coverage.update(read_chunks=len(evidence), partial=len(evidence) < coverage["available_chunks"])
    if not evidence:
        raise QuizGenerationError("所选资料没有可用的可信证据，请调整资料或范围")
    coverage["selected_files"] = len(documents)
    coverage["read_document_ids"] = sorted({e.document_id for e in evidence})
    return build_quiz_context(
        store, config, [e.model_dump(mode="json") for e in evidence]
    ), coverage


def _batch_context(context, slots, previous):
    """A batch has its own quota; all citations still bind to the original snapshots."""
    allocations = {}
    for slot in slots:
        key = (slot["knowledge_point"], slot["question_type"])
        if key not in allocations:
            allocations[key] = {**slot["allocation"], "question_count": 0}
        allocations[key]["question_count"] += 1
    plan = {"allocations": list(allocations.values())}
    counts = {}
    for slot in slots:
        kind = slot["question_type"]
        counts[kind] = counts.get(kind, 0) + 1
    config = context.config.model_dump(mode="json")
    config.update(
        blueprint=[{"question_type": k, "question_count": v} for k, v in counts.items()],
        question_count=len(slots),
        total_score=float(sum(Decimal(str(s["score"])) for s in slots)),
        knowledge_points=sorted({s["knowledge_point"] for s in slots})
        if config["scope_mode"] == "knowledge_points"
        else [],
    )
    # Rank whole, unmodified chunks; retain source diversity when no keyword matches.
    points = {s["knowledge_point"] for s in slots}
    ranked = sorted(
        enumerate(context.evidence),
        key=lambda pair: (-sum(p in pair[1].content for p in points), pair[0]),
    )
    selected = [item for _, item in ranked[:24]]
    return context.model_copy(
        update={
            "config": type(context.config).model_validate(config),
            "evidence": selected,
            "question_slots": [{k: v for k, v in s.items() if k != "allocation"} for s in slots],
            "previous_questions": [
                {k: q[k] for k in ("id", "knowledge_point", "stem", "reference_answer")}
                for q in previous
            ],
        }
    ), plan


def generate_quiz(
    store,
    model,
    config,
    *,
    max_repairs=2,
    progress=lambda *_: None,
    checkpoint=None,
    save_checkpoint=lambda *_: None,
    choice_batch_size=5,
    written_batch_size=2,
):
    """Resume verified batches; split truncated batches; publish only a complete validated paper."""
    from .llm import ModelOutputLimitError

    progress("retrieving")
    context, coverage = quiz_context(store, config)
    fingerprint = paper_hash(context.model_dump(mode="json"))
    state = json.loads(json.dumps(checkpoint)) if checkpoint else None
    if state and (state.get("version") != 1 or state.get("context_hash") != fingerprint):
        raise QuizGenerationError("资料或生成合同已变更，请重新配置试卷")
    if not state:
        progress("planning")
        audit, issues = [], []
        for repair in range(max_repairs + 1):
            plan = model.quiz_draft_plan(context, issues=issues)
            issues = validate_quiz_plan(context, plan)
            audit.append({"phase": "plan", "repair": repair, "issues": issues})
            if not issues:
                break
        else:
            raise QuizGenerationError(
                "出题计划未满足配置，修复次数已用完", issues, {"audit": audit, "plan": plan}
            )
        count = config.question_count
        total = Decimal(str(config.total_score if config.total_score is not None else count * 10))
        # Six decimal places also support unusually small positive totals without zero scores.
        unit = total / count
        unit = unit.quantize(
            Decimal("0.01") if total >= count * Decimal("0.01") else Decimal("0.000001")
        )
        if unit <= 0 or unit * (count - 1) >= total:
            raise QuizGenerationError("总分过小，无法为每道题分配正分值，请调整总分")
        slots, pending = [], []
        for allocation in plan["allocations"]:
            for _ in range(allocation["question_count"]):
                order = len(slots) + 1
                slots.append(
                    {
                        "id": f"q{order}",
                        "order": order,
                        "knowledge_point": allocation["knowledge_point"],
                        "question_type": allocation["question_type"],
                        "score": float(total - unit * (count - 1) if order == count else unit),
                        "allocation": allocation,
                    }
                )
        for slot in slots:
            limit = (
                choice_batch_size
                if slot["question_type"] in {"choice", "true_false", "fill_blank"}
                else written_batch_size
            )
            if (
                not pending
                or len(pending[-1]) >= limit
                or (pending[-1][0]["question_type"] != slot["question_type"])
            ):
                pending.append([])
            pending[-1].append(slot)
        state = {
            "version": 1,
            "context_hash": fingerprint,
            "plan": plan,
            "pending": pending,
            "completed": [],
            "audit": audit,
            "title": None,
            "batch_repairs": max_repairs - repair,
        }
        save_checkpoint(state)
    previous = [q for batch in state["completed"] for q in batch["questions"]]
    while state["pending"]:
        slots = state["pending"][0]
        batch_context, batch_plan = _batch_context(context, slots, previous)
        try:
            generated = _generate_batch(
                model,
                batch_context,
                coverage,
                batch_plan,
                slots,
                previous,
                state["batch_repairs"],
                progress,
            )
        except ModelOutputLimitError:
            if len(slots) == 1:
                raise QuizGenerationError(
                    "单题输出仍达到长度上限；已完成题目已保存，请调整输出预算后重试",
                    [{"code": "output_length", "question_id": slots[0]["id"]}],
                ) from None
            middle = len(slots) // 2
            state["pending"][:1] = [slots[:middle], slots[middle:]]
            state["audit"].append({"phase": "split", "question_ids": [s["id"] for s in slots]})
            save_checkpoint(state)
            continue
        questions = generated["draft"]["questions"]
        ids = {q["id"]: slot["id"] for q, slot in zip(questions, slots, strict=True)}
        for entry in generated["audit"]:
            for item in (entry.get("review") or {}).get("items", []):
                item["question_id"] = ids.get(item["question_id"], item["question_id"])
        for question, slot in zip(questions, slots, strict=True):
            question.update(id=slot["id"], order=slot["order"])
        state["title"] = state["title"] or generated["draft"]["title"]
        state["completed"].append({"questions": questions, "audit": generated["audit"]})
        state["pending"].pop(0)
        previous.extend(questions)
        save_checkpoint(state)
    progress("validating")
    # Strip server-derived fields before reusing the strict candidate schema.
    from .quiz_contract import QuizDraftQuestion

    fields = set(QuizDraftQuestion.model_fields)
    candidate = {
        "title": state["title"],
        "questions": [{k: v for k, v in q.items() if k in fields} for q in previous],
    }
    validation = validate_quiz_draft(context, state["plan"], candidate)
    if not validation.valid:
        raise QuizGenerationError(
            "整卷校验未通过，已完成批次保留",
            [i.model_dump(mode="json") for i in validation.issues],
        )
    return {
        "context": context,
        "plan": state["plan"],
        "draft": validation.validated_draft,
        "coverage": coverage,
        "audit": state["audit"]
        + [
            {**entry, "question_ids": [q["id"] for q in batch["questions"]]}
            for batch in state["completed"]
            for entry in batch["audit"]
        ],
    }


def _generate_batch(model, context, coverage, plan, slots, previous, max_repairs, progress):
    audit = []
    repair = 0
    remaining_repairs = max_repairs
    progress("generating")
    candidate = model.quiz_draft(context, QuizDraftPlan.model_validate(plan))
    for repair in range(remaining_repairs + 1):
        progress("validating")
        validation = validate_quiz_draft(context, plan, candidate)
        issues = [i.model_dump(mode="json") for i in validation.issues]
        review = None
        if validation.valid:
            for index, question in enumerate(candidate["questions"]):
                if Decimal(str(question["score"])) != Decimal(str(slots[index]["score"])):
                    issues.append({"code": "slot_score", "message": "题目分值必须符合槽位分配"})
                if (question["knowledge_point"], question["question_type"]) != (
                    slots[index]["knowledge_point"],
                    slots[index]["question_type"],
                ):
                    issues.append(
                        {"code": "slot_allocation", "message": "题目顺序必须符合槽位分配"}
                    )
            stems = {"".join(q["stem"].split()) for q in previous}
            if any("".join(q["stem"].split()) in stems for q in candidate["questions"]):
                issues.append({"code": "duplicate_stem", "message": "题干与已完成批次重复"})
        if validation.valid and not issues:
            progress("reviewing")
            try:
                review = QuizSemanticReview.model_validate(
                    model.review_quiz_draft(context, validation.validated_draft)
                ).model_dump(mode="json")
                expected = {q["id"] for q in candidate["questions"]}
                ids = [i["question_id"] for i in review["items"]]
                if set(ids) != expected or len(ids) != len(expected):
                    issues = [{"code": "review_coverage", "message": "复核遗漏或重复题目"}]
                else:
                    issues = [
                        {"code": "semantic", "question_id": i["question_id"], "message": message}
                        for i in review["items"]
                        for message in i["issues"]
                    ]
            except ValidationError:
                issues = [{"code": "review_schema", "message": "语义复核结果结构无效"}]
        audit.append({"phase": "draft", "repair": repair, "issues": issues, "review": review})
        if not issues:
            return {
                "context": context,
                "plan": plan,
                "draft": validation.validated_draft,
                "coverage": coverage,
                "audit": audit,
            }
        if repair < remaining_repairs:
            progress("repairing")
            candidate = model.repair_quiz_draft(context, plan, candidate, issues)
    raise QuizGenerationError(
        "试卷未通过校验或语义复核，修复次数已用完",
        issues,
        {"audit": audit, "candidate": candidate, "plan": plan},
    )


def publish_quiz(store, generated, run_id, model_id):
    """Caller holds the job lock; all records commit together with its success state."""
    context = generated["context"]
    config = context.config
    domain = DomainService(store, config.user_id)
    asset_id = "quiz-" + stable_key(config.user_id, config.course_id, run_id)[:32]
    revision_id = "revision-" + stable_key(asset_id, "1")[:32]
    with store.transaction():
        existing = store.get("learning_asset", asset_id)
        if existing:
            return quiz_card(existing, store.get("quiz_revision_payload", revision_id))
        # Consistent lock order with material deletion; locks are held until the outer commit.
        locked(store, "course", config.course_id)
        if config.exam_id:
            locked(store, "exam", config.exam_id)
        for document_id in sorted(config.source_document_ids):
            locked(store, "document", document_id)
        build_quiz_context(store, config, [e.model_dump(mode="json") for e in context.evidence])
        domain.course(config.course_id, writable=True)
        now = datetime.now(UTC).isoformat()
        common = {"user_id": config.user_id, "course_id": config.course_id, "created_at": now}
        draft = generated["draft"]
        score = float(sum(Decimal(str(q["score"])) for q in draft["questions"]))
        asset = {
            **common,
            "asset_id": asset_id,
            "asset_type": "quiz",
            "title": draft["title"],
            "status": "draft",
            "current_revision_id": None,
            "latest_revision_id": revision_id,
            "generation_method": "ai",
            "updated_at": now,
        }
        # Include a canonical body in the existing content-hash and immutability mechanism.
        body = {"config": config.model_dump(mode="json"), "plan": generated["plan"], "draft": draft}
        revision = {
            **common,
            "asset_id": asset_id,
            "revision_id": revision_id,
            "revision_no": 1,
            "state": "draft",
            "title": draft["title"],
            "updated_at": now,
            "markdown": json.dumps(body, ensure_ascii=False, sort_keys=True),
            "source_document_ids": sorted(
                {r["document_id"] for q in draft["questions"] for r in q["references"]}
            ),
            "based_on_revision_id": None,
            "edit_source": "ai",
            "quiz_contract_version": 1,
        }
        payload = {
            **common,
            "asset_id": asset_id,
            "quiz_revision_id": revision_id,
            "state": "draft",
            "asset_revision_id": revision_id,
            "title": draft["title"],
            "config": config.model_dump(mode="json"),
            "exam_id": config.exam_id,
            "plan": generated["plan"],
            "total_score": score,
            "duration_minutes": config.duration_minutes,
            "question_count": len(draft["questions"]),
            "questions": [],
            "coverage": generated["coverage"],
            "review_audit": generated["audit"],
            "model_id": model_id,
            "prompt_version": context.prompt_version,
            "content_hash": paper_hash(body),
            "generation_run_id": run_id,
        }
        store.put("learning_asset", asset_id, asset)
        store.put("asset_revision", revision_id, revision)
        for q in draft["questions"]:
            question_id = "question-" + stable_key(asset_id, q["id"])[:32]
            question_revision_id = "question-revision-" + stable_key(revision_id, q["id"])[:32]
            question = {
                **common,
                **{k: v for k, v in q.items() if k not in {"order", "score"}},
                "question_id": question_id,
                "question_revision_id": question_revision_id,
                "quiz_revision_id": revision_id,
                "revision_no": 1,
                "edit_source": "ai",
            }
            question["content_hash"] = paper_hash(question)
            payload["questions"].append(
                {
                    "question_revision_id": question_revision_id,
                    "order": q["order"],
                    "score": q["score"],
                }
            )
            # Source references retain a question ID as well as the owning paper revision.
            for index, ref in enumerate(q["references"]):
                document = store.get("document", ref["document_id"])
                store.ensure_material_source(document)
                source_id = "source-" + stable_key(question_revision_id, str(index))[:32]
                store.put(
                    "source_reference",
                    source_id,
                    {
                        **common,
                        **ref,
                        "source_reference_id": source_id,
                        "revision_id": revision_id,
                        "asset_revision_id": revision_id,
                        "question_revision_id": question_revision_id,
                        "locator_id": ref["chunk_id"],
                    },
                )
            # The parent payload must exist before the question FK is inserted.
            store.put("quiz_revision_payload", revision_id, payload)
            store.put("question_revision", question_revision_id, question)
        domain._audit(config.course_id, "quiz.draft_generated", "learning_asset", asset_id)
        return quiz_card(asset, payload)


def quiz_card(asset, payload):
    return {
        "asset_id": asset["asset_id"],
        "revision_id": payload["quiz_revision_id"],
        "title": payload["title"],
        "state": payload["state"],
        "question_count": payload["question_count"],
        "total_score": payload["total_score"],
    }


def read_quiz(store, user_id, course_id, asset_id, revision_id):
    domain = DomainService(store, user_id)
    domain.course(course_id)
    asset = domain._owned("learning_asset", asset_id)
    payload = domain._owned("quiz_revision_payload", revision_id)
    if (
        asset["course_id"] != course_id
        or asset["asset_type"] != "quiz"
        or payload["asset_id"] != asset_id
    ):
        raise DomainNotFound(revision_id)
    questions = []
    for slot in payload["questions"]:
        question = domain._owned("question_revision", slot["question_revision_id"])
        if question["quiz_revision_id"] != revision_id or question["course_id"] != course_id:
            raise DomainConflict("题目版本关系无效")
        questions.append({**question, **slot})
        for ref in questions[-1]["references"]:
            document = store.get("document", ref["document_id"])
            ref["available"] = bool(
                document
                and document.get("user_id") == user_id
                and document.get("parse_status") == "ready"
                and any(
                    c["chunk_id"] == ref["chunk_id"]
                    for c in store.list_material_chunks(ref["document_id"])
                )
            )
    return {"asset": asset, "revision": {**payload, "questions": questions}}
