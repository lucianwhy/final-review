from collections import Counter, defaultdict
from threading import Lock
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .checkpoints import SurrealSaver
from .config import Settings
from .llm import ModelError
from .policy import SOURCE_PRIORITY
from .schemas import (
    AgentRequest,
    AgentResponse,
    ExamProfile,
    Grades,
    GroundedAnswer,
    KnowledgePlan,
    Quiz,
    ResumeRequest,
    Submission,
)
from .storage import stable_key


class SessionConflict(ValueError):
    pass


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
            lambda s: "exam_profile" if s["intent"] == "quiz" else "retrieve",
            ["exam_profile", "retrieve"],
        )
        graph.add_edge("exam_profile", "retrieve")
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
        return stable_key(course, session)

    def _lock(self, key):
        return self.locks[int(key[:8], 16) % len(self.locks)]

    def _config(self, key):
        return {"configurable": {"thread_id": key}, "recursion_limit": 40}

    def invoke(self, request: AgentRequest) -> AgentResponse:
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            snapshot = self.graph.get_state(self._config(key))
            if snapshot.next:
                raise SessionConflict("此会话有未完成任务，请补充信息、提交答案或恢复任务")
            incoming = request.model_dump(mode="json")
            if incoming["exam_profile"] is None:
                incoming["exam_profile"] = snapshot.values.get("request", {}).get("exam_profile")
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
            }
            return self._run(key, state)

    def resume_profile(self, request: ResumeRequest):
        key = self._key(request.course_id, request.session_id)
        with self._lock(key):
            self._pending(key, "exam_profile")
            return self._run(key, Command(resume=request.exam_profile.model_dump(mode="json")))

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
            },
        )
        return response

    def _response(self, state, tasks=()):
        interruptions = state.get("__interrupt__", [])
        if tasks:
            interruptions = [i for task in tasks for i in task.interrupts]
        if interruptions:
            payload = interruptions[0].value
            return AgentResponse(
                session_id=state["request"]["session_id"],
                status=payload["status"],
                questions=payload.get("questions", []),
                prompt=payload.get("prompt"),
                weak_points=state.get("weak_points", []),
            )
        return AgentResponse.model_validate(state["response"])

    def _route(self, state):
        intent = state["request"]["intent"]
        return {"intent": self.model.route(state["request"]) if intent == "auto" else intent}

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
