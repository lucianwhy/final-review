import re
from collections import Counter, defaultdict
from contextvars import ContextVar, Token
from threading import Lock
from typing import Callable, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .checkpoints import SurrealSaver
from .config import Settings
from .domain import DomainService
from .llm import ModelError
from .policy import SOURCE_PRIORITY
from .quiz_config import merge_quiz_input, resolve_quiz_config
from .schemas import (
    AgentRequest,
    AgentResponse,
    ExamProfile,
    GeneratedNote,
    Grades,
    GroundedAnswer,
    KnowledgePlan,
    NoteInput,
    Quiz,
    QuizInput,
    ResumeNoteRequest,
    ResumeQuizRequest,
    ResumeRequest,
    Submission,
)
from .storage import stable_key


class SessionConflict(ValueError):
    pass


class NoteProgress:
    def __init__(
        self,
        completed: dict[str, dict],
        update: Callable[..., bool],
        begin_publish: Callable[[], bool] | None = None,
        selection_plan: dict | None = None,
        policy_version: int = 2,
    ):
        self.completed = completed
        self.update = update
        self._begin_publish = begin_publish
        self.selection_plan = selection_plan
        self.policy_version = policy_version

    def report(
        self,
        stage: str,
        *,
        batch_index: int | None = None,
        batch_result: dict | None = None,
        selection_plan: dict | None = None,
    ) -> None:
        if not self.update(
            stage, batch_index=batch_index, batch_result=batch_result, selection_plan=selection_plan
        ):
            raise SessionConflict("笔记任务租约已失效")
        if batch_index is not None and batch_result is not None:
            self.completed[str(batch_index)] = batch_result
        if selection_plan is not None:
            self.selection_plan = selection_plan

    def begin_publish(self) -> None:
        if self._begin_publish and not self._begin_publish():
            raise SessionConflict("笔记任务租约已失效")


_note_progress: ContextVar[NoteProgress | None] = ContextVar("note_progress", default=None)


def bind_note_progress(progress: NoteProgress) -> Token:
    return _note_progress.set(progress)


def reset_note_progress(token: Token) -> None:
    _note_progress.reset(token)


def _chapter_requirements(scope: str) -> list[tuple[str, str]]:
    chinese_numbers = "一二三四五六七八九十"
    chapters = []
    for match in re.finditer(r"第\s*([一二三四五六七八九十\d]+)\s*章", scope.lower()):
        chapter = match.group(0).replace(" ", "")
        number = match.group(1)
        if number in chinese_numbers:
            number = str(chinese_numbers.index(number) + 1)
        if (chapter, number) not in chapters:
            chapters.append((chapter, number))
    return chapters


def _file_matches_chapter(file_name: str, chapter_field: str, chapter: str, number: str) -> bool:
    metadata = f"{file_name} {chapter_field}".lower()
    return chapter in metadata.replace(" ", "") or bool(
        re.match(rf"^{re.escape(number)}[.、_-]", metadata)
    )


def _focus_terms(scope: str) -> list[str]:
    simplified = re.sub(
        r"侧重|重点|请|按照|按|关于|以及|和|与|整理|生成|笔记|内容|相关|的|得分点",
        " ",
        scope.lower(),
    )
    return re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{2,}", simplified)


class ReviewState(TypedDict, total=False):
    run_id: str
    request: dict
    intent: str
    evidence: list[dict]
    plan: dict
    output: dict
    issues: list[str]
    attempts: int
    answers: dict
    assessment: dict
    weak_points: list[str]
    response: dict
    note_input: dict


def citation_issues(output: dict, evidence: list[dict]) -> list[str]:
    lookup = {e["chunk_id"]: e for e in evidence}
    parts = output.get("questions", output.get("points", [output]))
    issues = []
    for part in parts:
        refs = part.get("citations", [])
        if not refs:
            issues.append("缺少引用")
        sources = []
        for ref in refs:
            source = lookup.get(ref.get("chunk_id"))
            quote = ref.get("quote", "").strip()
            if source is None or not quote or quote not in source["content"]:
                issues.append("引用 ID 或原文不匹配")
            else:
                sources.append(source["source_type"])
        if "source_type" in part and sources and part["source_type"] not in sources:
            issues.append("题源标记不匹配引用")
    return issues


class FinalReviewAgent:
    def __init__(self, store, knowledge_base, model, settings: Settings):
        self.store, self.kb, self.model, self.settings = store, knowledge_base, model, settings
        # Bounded stripe locks prevent concurrent mutation of one session in a single worker.
        self.locks = [Lock() for _ in range(64)]
        graph = StateGraph(ReviewState)
        for name, node in {
            "route": self._route,
            "exam_profile": self._profile,
            "quiz_config": self._quiz_config,
            "note_parse": self._note_parse,
            "note_config": self._note_config,
            "note_generate": self._note_generate,
            "retrieve": self._retrieve,
            "generate": self._generate,
            "verify": self._verify,
            "wait_answers": self._wait,
            "evaluate": self._evaluate,
            "finalize": self._finalize,
            "refuse": self._refuse,
        }.items():
            graph.add_node(name, node)
        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route",
            lambda s: (
                "quiz_config"
                if s["intent"] == "quiz" and s["request"].get("quiz_input") is not None
                else {"quiz": "exam_profile", "note": "note_parse"}.get(s["intent"], "retrieve")
            ),
            ["quiz_config", "exam_profile", "note_parse", "retrieve"],
        )
        graph.add_edge("quiz_config", END)
        graph.add_edge("exam_profile", "retrieve")
        graph.add_edge("note_parse", "note_config")
        graph.add_edge("note_config", "note_generate")
        graph.add_edge("note_generate", END)
        graph.add_conditional_edges(
            "retrieve", lambda s: "generate" if s["evidence"] else "refuse", ["generate", "refuse"]
        )
        graph.add_edge("generate", "verify")
        graph.add_conditional_edges(
            "verify", self._after_verify, ["retrieve", "refuse", "wait_answers", "finalize"]
        )
        graph.add_edge("wait_answers", "evaluate")
        graph.add_edge("evaluate", "finalize")
        graph.add_edge("finalize", END)
        graph.add_edge("refuse", END)
        self.graph = graph.compile(checkpointer=SurrealSaver(store))

    def _key(self, course, session):
        if hasattr(self.store, "session_key"):
            return self.store.session_key(course, session)
        return stable_key(course, session)

    def _lock(self, key):
        return self.locks[int(key[:8], 16) % len(self.locks)]

    def _config(self, key):
        return {"configurable": {"thread_id": key}, "recursion_limit": 40}

    def invoke(self, request: AgentRequest, user_id: str | None = None) -> AgentResponse:
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            self._require_not_cancelled(key)
            snapshot = self.graph.get_state(self._config(key))
            if snapshot.next:
                raise SessionConflict("此会话有未完成任务，请补充信息、提交答案或恢复任务")
            incoming = request.model_dump(mode="json")
            incoming["note_input"] = (
                request.note_input.model_dump(mode="json", exclude_unset=True)
                if request.note_input is not None
                else {}
            )
            incoming["owner_id"] = user_id
            incoming["quiz_input"] = (
                request.quiz_input.model_dump(mode="json", exclude_unset=True)
                if request.quiz_input is not None
                else None
            )
            if incoming["exam_profile"] is None:
                incoming["exam_profile"] = snapshot.values.get("request", {}).get("exam_profile")
            if incoming["quiz_input"] is not None:
                existing = self.store.get("review_session", key) or {}
                self.store.put("review_session", key, {**existing, "pending_quiz_input": None})
            state = {
                "request": incoming,
                "run_id": uuid4().hex,
                "attempts": 0,
                "evidence": [],
                "plan": {},
                "output": {},
                "issues": [],
                "answers": {},
                "assessment": {},
                "response": {},
                "weak_points": snapshot.values.get("weak_points", []),
                "note_input": {},
            }
            return self._run(key, state)

    def resume_profile(self, request: ResumeRequest):
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            self._pending(key, "exam_profile")
            return self._run(key, Command(resume=request.exam_profile.model_dump(mode="json")))

    def resume_quiz(self, request: ResumeQuizRequest, user_id: str):
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            self._pending(key, "quiz_config")
            snapshot = self.graph.get_state(self._config(key))
            if snapshot.values["request"].get("owner_id") != user_id:
                raise SessionConflict("会话所有者不匹配")
            payload = next(i.value for task in snapshot.tasks for i in task.interrupts)
            saved = self.store.get("review_session", key) or {}
            combined = merge_quiz_input(
                saved.get("pending_quiz_input") or payload["quiz_config"], request.quiz_input
            )
            # Validate ownership before consuming the pending interrupt.
            result = resolve_quiz_config(self.store, user_id, request.course_id, combined)
            if result.status != "ready":
                partial = combined.model_dump(mode="json", exclude_unset=True)
                response = AgentResponse(
                    session_id=request.session_id,
                    status="needs_input",
                    prompt=result.prompt,
                    quiz_config=partial,
                )
                self.store.put(
                    "review_session",
                    key,
                    {
                        **saved,
                        "pending_quiz_input": partial,
                        "response": response.model_dump(mode="json"),
                    },
                )
                return response
            return self._run(
                key, Command(resume=combined.model_dump(mode="json", exclude_unset=True))
            )

    def resume_note(self, request: ResumeNoteRequest, user_id: str | None = None):
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            self._require_not_cancelled(key)
            self._pending(key, "note_config")
            snapshot = self.graph.get_state(self._config(key))
            original = snapshot.values["request"]
            if original.get("owner_id") != user_id:
                raise SessionConflict("会话所有者不匹配")
            additions = request.note_input.model_dump(mode="json", exclude_unset=True)
            combined = {**snapshot.values["note_input"], **additions}
            note = NoteInput.model_validate(combined)
            try:
                missing, _ = self._note_boundaries(original, note)
            except ValueError as exc:
                if not str(exc).startswith("所选资料中未找到"):
                    raise
                return AgentResponse(
                    session_id=request.session_id,
                    status="needs_input",
                    prompt={"message": str(exc), "required": ["source_document_ids"]},
                )
            if missing:
                return self._note_prompt(request.session_id, missing)
            return self._run(key, Command(resume=additions))

    def cancel_note(self, course_id: str, session_id: str, user_id: str | None = None):
        key = self._key(course_id, session_id)
        with self._lock(key):
            self._require_not_cancelled(key)
            self._pending(key, "note_config")
            snapshot = self.graph.get_state(self._config(key))
            if snapshot.values["request"].get("owner_id") != user_id:
                raise SessionConflict("会话所有者不匹配")
            existing = self.store.get("review_session", key) or {}
            self.store.put(
                "review_session",
                key,
                {
                    **existing,
                    "course_id": course_id,
                    "session_id": session_id,
                    "cancelled": True,
                },
            )

    def evaluate(self, request: Submission):
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            snapshot = self.graph.get_state(self._config(key))
            if not snapshot.next and snapshot.values.get("assessment"):
                if request.answers == snapshot.values.get("answers"):
                    return AgentResponse.model_validate(snapshot.values["response"])
                raise SessionConflict("本轮已评分；请开启新一轮测评")
            self._pending(key, "wait_answers")
            ids = {q["id"] for q in snapshot.values["output"]["questions"]}
            if set(request.answers) != ids:
                raise ValueError("请提交本轮所有题目的答案，题目 ID 必须完全匹配")
            return self._run(key, Command(resume=request.answers))

    def recover(self, course, session):
        key = self._key(course, session)
        with self._lock(key):
            self._require_not_cancelled(key)
            snapshot = self.graph.get_state(self._config(key))
            if not snapshot.values:
                raise KeyError("会话不存在")
            if any(task.interrupts for task in snapshot.tasks):
                return self._response(snapshot.values, snapshot.tasks)
            if not snapshot.next:
                return AgentResponse.model_validate(snapshot.values["response"])
            return self._run(key, None)

    def read(self, course, session):
        key = self._key(course, session)
        with self._lock(key):
            self._require_not_cancelled(key)
            snapshot = self.graph.get_state(self._config(key))
            if not snapshot.values:
                raise KeyError("会话不存在")
            if snapshot.next and not any(task.interrupts for task in snapshot.tasks):
                raise SessionConflict("任务尚未完成，请使用 recover 恢复")
            return self._response(snapshot.values, snapshot.tasks)

    def _pending(self, key, node):
        snapshot = self.graph.get_state(self._config(key))
        if not snapshot.values:
            raise KeyError("会话不存在")
        if node not in snapshot.next or not any(task.interrupts for task in snapshot.tasks):
            raise SessionConflict("会话当前不接受此操作")

    def _require_not_cancelled(self, key):
        if (self.store.get("review_session", key) or {}).get("cancelled"):
            raise SessionConflict("笔记任务已取消，请重新发起")

    def _run(self, key, payload):
        result = self.graph.invoke(payload, self._config(key))
        response = self._response(result)
        self.store.put(
            "review_session",
            key,
            {
                "course_id": result["request"]["course_id"],
                "session_id": result["request"]["session_id"],
                "response": response.model_dump(mode="json"),
                "weak_points": result.get("weak_points", []),
                "pending_quiz_input": (
                    response.quiz_config
                    if response.status == "needs_input"
                    and result["request"].get("quiz_input") is not None
                    else None
                ),
            },
        )
        return response

    def _response(self, state, tasks=()):
        interruptions = state.get("__interrupt__", [])
        if tasks:
            interruptions = [i for task in tasks for i in task.interrupts]
        if interruptions:
            payload = interruptions[0].value
            if "quiz_config" in payload:
                request = state["request"]
                saved = (
                    self.store.get(
                        "review_session", self._key(request["course_id"], request["session_id"])
                    )
                    or {}
                )
                partial = saved.get("pending_quiz_input") or payload["quiz_config"]
                result = resolve_quiz_config(
                    self.store,
                    request["owner_id"],
                    request["course_id"],
                    QuizInput.model_validate(partial),
                )
                payload = {**payload, "quiz_config": partial, "prompt": result.prompt}
            return AgentResponse(
                session_id=state["request"]["session_id"],
                status=payload["status"],
                questions=payload.get("questions", []),
                prompt=payload.get("prompt"),
                note_config=state.get("note_input") if state.get("intent") == "note" else None,
                quiz_config=payload.get("quiz_config"),
                weak_points=state.get("weak_points", []),
            )
        return AgentResponse.model_validate(state["response"])

    def _route(self, state):
        intent = state["request"]["intent"]
        if intent == "auto" and state["request"].get("quiz_input") is not None:
            return {"intent": "quiz"}
        intent = self.model.route(state["request"]) if intent == "auto" else intent
        if (
            intent == "quiz"
            and state["request"].get("quiz_input") is None
            and any(
                word in state["request"]["message"]
                for word in ("试卷", "模拟卷", "模拟考试", "期末卷")
            )
        ):
            return {"intent": intent, "request": {**state["request"], "quiz_input": {}}}
        return {"intent": intent}

    def _quiz_config(self, state):
        request = state["request"]
        owner = request.get("owner_id")
        if owner is None:
            raise ValueError("正式试卷配置需要用户身份")
        partial = QuizInput.model_validate(request["quiz_input"])
        result = resolve_quiz_config(self.store, owner, request["course_id"], partial)
        if result.status != "ready":
            additions = interrupt(
                {
                    "status": "needs_input",
                    "prompt": result.prompt,
                    "quiz_config": partial.model_dump(mode="json", exclude_unset=True),
                }
            )
            partial = merge_quiz_input(
                partial.model_dump(mode="json", exclude_unset=True),
                QuizInput.model_validate(additions),
            )
            result = resolve_quiz_config(self.store, owner, request["course_id"], partial)
        if result.status != "ready":
            raise SessionConflict("试卷配置尚不完整，请继续补充配置")
        return {
            "response": AgentResponse(
                session_id=request["session_id"],
                status="configured",
                answer="试卷配置已就绪，可提交正式试卷生成任务。",
                quiz_config=result.config.model_dump(mode="json"),
            ).model_dump(mode="json")
        }

    def _profile(self, state):
        request = dict(state["request"])
        if not request.get("exam_profile"):
            profile = interrupt(
                {
                    "status": "needs_input",
                    "prompt": {
                        "message": "请确认考试题型、重点与不考范围",
                        "required": ["question_types"],
                    },
                }
            )
            request["exam_profile"] = ExamProfile.model_validate(profile).model_dump(mode="json")
        return {"request": request}

    def _note_parse(self, state):
        extracted = NoteInput.model_validate(self.model.note_request(state["request"]))
        explicit = state["request"].get("note_input") or {}
        return {"note_input": {**extracted.model_dump(mode="json"), **explicit}}

    def _note_boundaries(self, request, note: NoteInput):
        missing = []
        if note.note_type is None:
            missing.append("note_type")
        if note.duration_minutes is None:
            missing.append("duration_minutes")
        selected = set(note.source_document_ids)
        if len(selected) != len(note.source_document_ids):
            raise ValueError("资料 ID 不可重复")
        if not selected:
            missing.append("source_document_ids")
            return missing, []
        available = set()
        for document in self.store.scan("document", {"course_id": request["course_id"]}):
            if document.get("parse_status") != "ready":
                continue
            owner = request.get("owner_id")
            if owner is not None and document.get("user_id") != owner:
                continue
            if note.source_types and document.get("source_type") not in note.source_types:
                continue
            if document["document_id"] not in selected:
                continue
            available.add(document["document_id"])
        if selected != available:
            raise ValueError("所选资料不存在、不可用或不属于当前课程")
        for chapter, number in _chapter_requirements(note.scope):
            matching = False
            for document_id in note.source_document_ids:
                document = self.store.get("document", document_id)
                if _file_matches_chapter(
                    document.get("file_name") or document["title"],
                    document.get("chapter", ""),
                    chapter,
                    number,
                ):
                    matching = True
                    break
                if any(
                    chapter in chunk["content"].replace(" ", "")
                    for chunk in self.store.list_material_chunks(document_id)
                ):
                    matching = True
                    break
            if not matching:
                raise ValueError(f"所选资料中未找到“{chapter}”的明确内容，请调整资料或写作要求")
        return missing, note.source_document_ids

    @staticmethod
    def _note_prompt(session_id, missing):
        labels = {
            "note_type": "笔记类型（章节笔记、考点清单、问答卡片或口诀）",
            "duration_minutes": "目标阅读时长（分钟）",
            "source_document_ids": "指定资料（请从当前课程选择至少一份可检索资料）",
        }
        return AgentResponse(
            session_id=session_id,
            status="needs_input",
            prompt={
                "message": "请补充：" + "、".join(labels[item] for item in missing),
                "required": missing,
            },
        )

    def _note_config(self, state):
        request = state["request"]
        data = state["note_input"]
        note = NoteInput.model_validate(data)
        missing, sources = self._note_boundaries(request, note)
        if missing:
            additions = interrupt(
                self._note_prompt(request["session_id"], missing).model_dump(
                    mode="json", exclude_none=True
                )
            )
            note = NoteInput.model_validate({**data, **additions})
            missing, sources = self._note_boundaries(request, note)
            if missing:
                raise ValueError("仍缺少笔记生成所需信息")
        config = note.model_dump(mode="json")
        config["audience_level"] = config["audience_level"] or "intermediate"
        config["source_document_ids"] = sources
        response = AgentResponse(
            session_id=request["session_id"], status="configured", note_config=config
        )
        return {"note_input": config, "response": response.model_dump(mode="json")}

    def _generate_note_batch(
        self,
        request,
        config,
        evidence,
        batch_index,
        batch_total,
        progress: NoteProgress | None,
        point_limit: int,
    ):
        lookup = {item["chunk_id"]: item for item in evidence}
        issues = []
        for _ in range(self.settings.max_repairs + 1):
            try:
                generated = GeneratedNote.model_validate(
                    self.model.note(
                        {
                            "note_config": config,
                            "evidence": evidence,
                            "issues_to_fix": issues,
                            "batch_index": batch_index + 1,
                            "batch_total": batch_total,
                            "batch_instruction": (
                                f"本批最多生成 {point_limit} 个考点，逐字引用本批原文；"
                                "不要输出批次字样"
                            ),
                        }
                    )
                )
            except (ModelError, ValueError):
                return self._extractive_note_batch(
                    evidence, config["note_type"], "模型响应格式错误", point_limit
                )
            points, issues = [], []
            if len(generated.points) > point_limit:
                issues.append(f"本批超过 {point_limit} 个考点，请保留最重要的内容")
            for index, point in enumerate(generated.points, 1):
                refs = []
                for citation in point.citations:
                    source = lookup.get(citation.chunk_id)
                    if source is None or citation.quote not in source["content"]:
                        issues.append(f"第 {index} 条来源片段或摘录不匹配")
                        continue
                    refs.append(
                        {
                            "chunk_id": citation.chunk_id,
                            "document_id": source["document_id"],
                            "quote": citation.quote,
                            "file_name": source["file_name"],
                            "source_type": source["source_type"],
                        }
                    )
                distinct = {ref["document_id"] for ref in refs}
                if point.provenance == "ai_supplement" and refs:
                    issues.append(f"第 {index} 条 AI 补充不得带资料引用")
                if point.provenance == "source" and len(distinct) != 1:
                    issues.append(f"第 {index} 条单来源考点须引用一份资料")
                if point.provenance == "synthesis" and len(distinct) < 2:
                    issues.append(f"第 {index} 条综合改编须引用至少两份资料")
                points.append(
                    {
                        "heading": point.heading,
                        "content": point.content,
                        "provenance": point.provenance,
                        "references": refs,
                    }
                )
            if not any(point["references"] for point in points):
                issues.append("这一部分没有可验证的资料来源")
            if not issues:
                if progress:
                    progress.report(f"正在核对第 {batch_index + 1}/{batch_total} 部分")
                try:
                    verdict = self.model.verify(
                        {
                            "request": request,
                            "output": {
                                "points": [point for point in points if point["references"]]
                            },
                            "evidence": evidence,
                        }
                    )
                except ModelError:
                    return self._extractive_note_batch(
                        evidence, config["note_type"], "模型核对响应格式错误", point_limit
                    )
                if not verdict["supported"]:
                    issues = verdict["issues"] or ["考点未通过语义证据校验"]
            if not issues:
                return {
                    "title": generated.title,
                    "points": points,
                    "source_ids": [item["chunk_id"] for item in evidence],
                }
        return self._extractive_note_batch(
            evidence, config["note_type"], "生成内容未通过来源核对", point_limit
        )

    @staticmethod
    def _extractive_note_batch(
        evidence: list[dict], note_type: str, reason: str, point_limit: int = 5
    ) -> dict:
        """Grounded fallback: only copy text already present in selected source chunks."""
        points = []
        for source in evidence:
            content = source["content"].strip()
            if not content or any(point["content"] == content[:400] for point in points):
                continue
            if source.get("position_kind") == "page" and source.get("position"):
                location = f"第 {source['position']} 页"
            elif source.get("position_kind") == "slide" and source.get("position"):
                location = f"第 {source['position']} 张幻灯片"
            else:
                location = f"片段 {source['ordinal'] + 1}"
            heading = (
                f"{source['file_name']}的{location}讲了什么？"
                if note_type == "qa_cards"
                else f"{source['file_name']} · {location}"
            )
            points.append(
                {
                    "heading": heading[:200],
                    "content": content[:400],
                    "provenance": "source",
                    "references": [
                        {
                            "chunk_id": source["chunk_id"],
                            "document_id": source["document_id"],
                            "quote": content[:120],
                            "file_name": source["file_name"],
                            "source_type": source["source_type"],
                        }
                    ],
                }
            )
            if len(points) == point_limit:
                break
        if not points:
            raise ValueError("所选资料片段均为空，无法生成笔记")
        return {
            "title": "复习资料摘录",
            "points": points,
            "source_ids": [item["chunk_id"] for item in evidence],
            "fallback_reason": reason,
        }

    @staticmethod
    def _merge_note_batches(results: list[dict], point_limit: int = 20) -> list[dict]:
        """Combine already-verified points without asking the model to invent citations."""
        merged: list[dict] = []
        by_heading: dict[tuple[str, bool], dict] = {}
        for offset in range(max(len(result["points"]) for result in results)):
            for result in results:
                if offset >= len(result["points"]):
                    continue
                point = result["points"][offset]
                key = (point["heading"].strip().casefold(), point["provenance"] == "ai_supplement")
                existing = by_heading.get(key)
                if existing is None:
                    existing = {**point, "references": list(point["references"])}
                    by_heading[key] = existing
                    merged.append(existing)
                else:
                    if point["content"] not in existing["content"]:
                        existing["content"] += "\n" + point["content"]
                    known = {(ref["chunk_id"], ref["quote"]) for ref in existing["references"]}
                    existing["references"].extend(
                        ref
                        for ref in point["references"]
                        if (ref["chunk_id"], ref["quote"]) not in known
                    )
                    if existing["provenance"] != "ai_supplement":
                        sources = {ref["document_id"] for ref in existing["references"]}
                        existing["provenance"] = "synthesis" if len(sources) > 1 else "source"
        if len(merged) > point_limit:
            raise ValueError("合并后的笔记超过预分配的成品额度")
        for index, point in enumerate(merged, 1):
            point["point_id"] = f"p{index}"
        return merged

    @staticmethod
    def _note_limits(file_count: int) -> tuple[int, int]:
        return 20 + 20 * file_count, 16 + 4 * file_count

    @staticmethod
    def _batch_point_limits(batch_count: int, total_limit: int) -> list[int]:
        # A batch of roughly ten excerpts can contribute at most five points.
        allocated = min(total_limit, batch_count * 5)
        return [
            allocated // batch_count + (index < allocated % batch_count)
            for index in range(batch_count)
        ]

    def _prepare_note_selection(self, request: dict, config: dict, legacy: bool) -> dict | None:
        grouped = {}
        file_names = {}
        chapter_scoped = {}
        if config.get("chapter"):
            from .course_chat import CourseMaterials

            chapter_scoped = {
                document["document_id"]: document
                for document in CourseMaterials(
                    self.store,
                    request["course_id"],
                    request["owner_id"],
                ).select(config["source_document_ids"], config["chapter"])
            }
        for document_id in config["source_document_ids"]:
            document = (
                chapter_scoped.get(document_id)
                if config.get("chapter")
                else self.store.get("document", document_id)
            )
            if (
                document is None
                or document.get("user_id") != request.get("owner_id")
                or document.get("course_id") != request["course_id"]
                or document.get("parse_status") != "ready"
            ):
                raise ValueError("所选资料已不可用")
            file_names[document_id] = document.get("file_name") or document["title"]
            grouped[document_id] = []
            for chunk in document.get("_chat_chunks", self.store.list_material_chunks(document_id)):
                if not chunk["content"].strip():
                    continue
                grouped[document_id].append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "document_id": document_id,
                        "title": document["title"],
                        "file_name": file_names[document_id],
                        "source_type": document["source_type"],
                        "content": chunk["content"],
                        "position_kind": chunk.get("position_kind", "document"),
                        "position": chunk.get("position"),
                        "chapter": document.get("chapter", ""),
                        "ordinal": chunk.get("chunk_ordinal", 0),
                    }
                )
        scope = config["scope"].strip().lower()
        chapter_requests = _chapter_requirements(scope)
        restrict_chapters = bool(chapter_requests) and not any(
            term in scope for term in ("重点", "侧重", "着重", "优先")
        )
        if restrict_chapters:
            matched = {}
            for document_id, items in grouped.items():
                if not items:
                    continue
                file_matches = any(
                    _file_matches_chapter(
                        items[0]["file_name"],
                        items[0]["chapter"],
                        chapter,
                        number,
                    )
                    for chapter, number in chapter_requests
                )
                relevant = (
                    items
                    if file_matches
                    else [
                        item
                        for item in items
                        if any(
                            chapter in item["content"].replace(" ", "")
                            for chapter, _ in chapter_requests
                        )
                    ]
                )
                if relevant:
                    matched[document_id] = relevant
            if not matched:
                raise ValueError("所选资料中未找到指定章节的明确内容，请调整资料或写作要求")
            grouped = matched
        terms = _focus_terms(scope)
        for items in grouped.values():
            items.sort(
                key=lambda item: (
                    -sum(
                        _file_matches_chapter(item["file_name"], item["chapter"], chapter, number)
                        or chapter in item["content"].replace(" ", "")
                        for chapter, number in chapter_requests
                    ),
                    -sum(term in item["content"].lower() for term in terms),
                    item["ordinal"],
                )
            )
        counts = {document_id: len(items) for document_id, items in grouped.items()}
        effective_count = sum(count > 0 for count in counts.values())
        if not effective_count:
            return None
        chunk_limit, point_limit = (40, 20) if legacy else self._note_limits(effective_count)
        document_order = list(grouped)
        evidence = []
        read_counts = {document_id: 0 for document_id in config["source_document_ids"]}
        while len(evidence) < chunk_limit and any(grouped.values()):
            for document_id in document_order:
                items = grouped[document_id]
                if items and len(evidence) < chunk_limit:
                    evidence.append(items.pop(0))
                    read_counts[document_id] += 1
        batches = (
            [evidence[index : index + 10] for index in range(0, len(evidence), 10)]
            if len(evidence) > 12
            else [evidence]
        )
        coverage = {
            "selected_files": len(config["source_document_ids"]),
            "effective_files": effective_count,
            "readable_chunks": sum(counts.values()),
            "read_chunks": len(evidence),
            "partial": len(evidence) < sum(counts.values()),
            "files": [
                {
                    "document_id": document_id,
                    "file_name": file_names[document_id],
                    "readable_chunks": counts.get(document_id, 0),
                    "read_chunks": read_counts[document_id],
                }
                for document_id in config["source_document_ids"]
            ],
        }
        return {
            "evidence": evidence,
            "coverage": coverage,
            "point_limit": point_limit,
            "batch_limits": (
                [5] * len(batches)
                if legacy
                else self._batch_point_limits(len(batches), point_limit)
            ),
        }

    def _note_generate(self, state):
        request, config = state["request"], state["note_input"]
        progress = _note_progress.get()
        if progress:
            progress.report("正在整理资料")
        plan = progress.selection_plan if progress else None
        if plan is None:
            plan = self._prepare_note_selection(
                request, config, legacy=bool(progress and progress.policy_version < 2)
            )
            if plan and progress:
                progress.report("已固定本次取材", selection_plan=plan)
        if not plan:
            return {"response": self._note_refusal(request["session_id"])}
        evidence = plan["evidence"]
        batches = (
            [evidence[index : index + 10] for index in range(0, len(evidence), 10)]
            if len(evidence) > 12
            else [evidence]
        )
        results = []
        for index, batch in enumerate(batches):
            source_ids = [item["chunk_id"] for item in batch]
            cached = progress.completed.get(str(index)) if progress else None
            if cached and cached.get("source_ids") == source_ids:
                result = cached
            else:
                if progress:
                    progress.report(f"正在生成第 {index + 1}/{len(batches)} 部分")
                result = self._generate_note_batch(
                    request,
                    config,
                    batch,
                    index,
                    len(batches),
                    progress,
                    plan["batch_limits"][index],
                )
                if result is None:
                    return {"response": self._note_refusal(request["session_id"])}
                if progress:
                    progress.report(
                        f"已完成第 {index + 1}/{len(batches)} 部分",
                        batch_index=index,
                        batch_result=result,
                    )
            results.append(result)
        if progress:
            progress.report("正在合并与去重")
        points = self._merge_note_batches(results, plan["point_limit"])
        if progress:
            progress.report("正在核对整份笔记")
        available = {item["chunk_id"]: item for item in evidence}
        for point in points:
            sources = set()
            for ref in point["references"]:
                source = available.get(ref["chunk_id"])
                if source is None or ref["quote"] not in source["content"]:
                    raise ValueError("合并后的笔记来源片段或摘录不匹配")
                sources.add(source["document_id"])
            if (
                (point["provenance"] == "source" and len(sources) != 1)
                or (point["provenance"] == "synthesis" and len(sources) < 2)
                or (point["provenance"] == "ai_supplement" and sources)
            ):
                raise ValueError("合并后的笔记来源类型不匹配")
        if progress:
            progress.report("正在保存草稿")
            progress.begin_publish()
        fallback_count = sum(bool(result.get("fallback_reason")) for result in results)
        fallback_reasons = "、".join(
            sorted(
                {result["fallback_reason"] for result in results if result.get("fallback_reason")}
            )
        )
        title = re.sub(r"\s*[（(]?批次\s*\d+\s*/\s*\d+[）)]?", "", results[0]["title"]).strip()
        if fallback_count:
            title = f"{title}（含 {fallback_count} 部分资料摘录）"
        coverage = plan["coverage"]
        lines = [f"# {title}"]
        for point in points:
            lines.extend(["", f"## {point['heading']}", "", point["content"]])
        created = DomainService(self.store, request["owner_id"]).create_note_draft(
            request["course_id"],
            {
                "title": title,
                "markdown": "\n".join(lines),
                "note_type": config["note_type"],
                "points": points,
                "coverage": coverage,
            },
            state["run_id"],
        )
        asset, revision = created["asset"], created["revision"]
        response = AgentResponse(
            session_id=request["session_id"],
            status="completed",
            answer=(
                f"笔记草稿已生成；因{fallback_reasons}，其中 {fallback_count} 部分使用"
                "资料原文摘录，请打开预览核对。"
                if fallback_count
                else "笔记草稿已生成，请打开预览。"
            ),
            draft={
                "asset_id": asset["asset_id"],
                "revision_id": revision["revision_id"],
                "title": asset["title"],
                "note_type": config["note_type"],
                "url": f"#note/{asset['asset_id']}/{revision['revision_id']}",
            },
        )
        return {"response": response.model_dump(mode="json")}

    @staticmethod
    def _note_refusal(session_id):
        return AgentResponse(
            session_id=session_id,
            status="insufficient_evidence",
            answer="现有资料不足，或笔记内容未通过来源校验。请补充相关资料后重试。",
        ).model_dump(mode="json")

    def _retrieve(self, state):
        evidence = self.model.retrieve(state["request"], self.kb, broaden=state["attempts"] > 0)
        return {"evidence": evidence}

    def _generate(self, state):
        data = {
            "request": state["request"],
            "evidence": state["evidence"],
            "issues_to_fix": state["issues"],
            "weak_points": state.get("weak_points", []),
        }
        if state["intent"] == "ask":
            output = GroundedAnswer.model_validate(self.model.answer(data)).model_dump(mode="json")
            return {"output": output}
        plan = KnowledgePlan.model_validate(self.model.plan(data)).model_dump(mode="json")
        output = Quiz.model_validate(self.model.quiz({**data, "plan": plan})).model_dump(
            mode="json"
        )
        for i, question in enumerate(output["questions"], start=1):
            question["id"] = f"{state['run_id'][:12]}-q{i}"
            refs = {c["chunk_id"] for c in question["citations"]}
            types = [e["source_type"] for e in state["evidence"] if e["chunk_id"] in refs]
            if types:
                question["source_type"] = max(types, key=lambda t: SOURCE_PRIORITY[t])
        return {"plan": plan, "output": output}

    def _verify(self, state):
        issues = citation_issues(state["output"], state["evidence"])
        if state["intent"] == "quiz":
            issues += citation_issues(state["plan"], state["evidence"])
            points = state["plan"]["points"]
            allowed = set(state["request"]["exam_profile"]["question_types"])
            if len({p["name"] for p in points}) != len(points):
                issues.append("知识点名称重复")
            counts = Counter(q["knowledge_point"] for q in state["output"]["questions"])
            if counts != Counter({p["name"]: p["question_count"] for p in points}):
                issues.append("知识点题量与计划不一致")
            planned = {p["name"]: set(p["question_types"]) for p in points}
            for p in points:
                if not set(p["question_types"]) <= allowed:
                    issues.append("计划使用了非考试题型")
            for q in state["output"]["questions"]:
                if q["question_type"] not in allowed & planned.get(q["knowledge_point"], set()):
                    issues.append("题型不符合考试或知识点计划")
                if q["question_type"] == "choice" and len(q["options"]) < 2:
                    issues.append("选择题缺少选项")
        if not issues:
            verdict = self.model.verify(
                {
                    "request": state["request"],
                    "output": state["output"],
                    "evidence": state["evidence"],
                }
            )
            if not verdict["supported"]:
                issues.extend(verdict["issues"] or ["语义证据校验失败"])
        return {"issues": issues, "attempts": state["attempts"] + 1}

    def _after_verify(self, state):
        if state["issues"]:
            return "retrieve" if state["attempts"] <= self.settings.max_repairs else "refuse"
        return "wait_answers" if state["intent"] == "quiz" else "finalize"

    def _wait(self, state):
        questions = [
            {
                k: q[k]
                for k in (
                    "id",
                    "knowledge_point",
                    "question_type",
                    "stem",
                    "options",
                    "source_type",
                )
            }
            for q in state["output"]["questions"]
        ]
        answers = interrupt({"status": "awaiting_answers", "questions": questions})
        return {"answers": answers}

    def _evaluate(self, state):
        items = Grades.model_validate(
            self.model.grade(
                {
                    "quiz": state["output"],
                    "answers": state["answers"],
                }
            )
        ).model_dump(mode="json")["items"]
        expected = {q["id"] for q in state["output"]["questions"]}
        if len(items) != len(expected) or {g["question_id"] for g in items} != expected:
            raise ModelError("评分结果题目 ID 不匹配")
        scores = defaultdict(list)
        lookup = {q["id"]: q["knowledge_point"] for q in state["output"]["questions"]}
        for grade in items:
            scores[lookup[grade["question_id"]]].append(grade["score"])
        weak = set(state.get("weak_points", []))
        for point, values in scores.items():
            if sum(values) / len(values) < 60:
                weak.add(point)
            else:
                weak.discard(point)
        return {
            "assessment": {
                "score": round(sum(g["score"] for g in items) / len(items), 2),
                "items": items,
                "reference_questions": state["output"]["questions"],
            },
            "weak_points": sorted(weak),
        }

    def _finalize(self, state):
        parts = state["output"].get("questions", [state["output"]])
        cited = {ref["chunk_id"] for part in parts for ref in part.get("citations", [])}
        response = AgentResponse(
            session_id=state["request"]["session_id"],
            status="completed",
            answer=state["output"].get("answer", ""),
            citations=[e for e in state["evidence"] if e["chunk_id"] in cited],
            assessment=state.get("assessment") or None,
            weak_points=state.get("weak_points", []),
            suggestions=[
                f"优先复习「{point}」的定义与失分步骤，再做同类题。"
                for point in state.get("weak_points", [])
            ],
        )
        if state.get("plan"):
            key = self._key(state["request"]["course_id"], state["request"]["session_id"])
            self.store.put(
                "knowledge_point",
                key,
                {
                    "course_id": state["request"]["course_id"],
                    **state["plan"],
                },
            )
        return {"response": response.model_dump(mode="json")}

    def _refuse(self, state):
        return {
            "response": AgentResponse(
                session_id=state["request"]["session_id"],
                status="insufficient_evidence",
                answer="现有资料不足，或生成内容未通过证据校验。请补充相关课程资料后重试。",
                weak_points=state.get("weak_points", []),
            ).model_dump(mode="json")
        }
