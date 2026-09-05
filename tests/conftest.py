from copy import deepcopy
from math import sqrt
from threading import RLock

import pytest
from langchain_core.embeddings import Embeddings

from final_review.agent import FinalReviewAgent
from final_review.config import Settings
from final_review.rag import KnowledgeBase
from final_review.schemas import MaterialInput


class MemoryStore:
    """Test adapter only. Production always uses SurrealDB."""

    def __init__(self):
        self.tables = {}
        self.chunks = []
        self.lock = RLock()

    def put(self, table, key, data):
        with self.lock:
            self.tables.setdefault(table, {})[key] = deepcopy(data)

    def get(self, table, key):
        with self.lock:
            return deepcopy(self.tables.get(table, {}).get(key))

    def scan(self, table, filters):
        with self.lock:
            return [
                deepcopy(row)
                for row in self.tables.get(table, {}).values()
                if all(row.get(k) == v for k, v in filters.items())
            ]

    def ingest(self, document, chunks):
        with self.lock:
            self.put("document", document["document_id"], document)
            self.chunks = [c for c in self.chunks if c["document_id"] != document["document_id"]]
            self.chunks.extend(deepcopy(chunks))

    def search(self, vector, course, chapter, limit):
        rows = []
        for chunk in self.chunks:
            if chunk["course_id"] != course or (chapter and chunk["chapter"] != chapter):
                continue
            embedding = chunk["embedding"]
            score = sum(a * b for a, b in zip(vector, embedding, strict=True)) / (
                sqrt(sum(x * x for x in vector)) * sqrt(sum(x * x for x in embedding))
            )
            rows.append(
                {**{k: v for k, v in chunk.items() if k != "embedding"}, "similarity": score}
            )
        return sorted(rows, key=lambda c: -c["similarity"])[:limit]


class TestEmbeddings(Embeddings):
    __test__ = False

    def __init__(self):
        self.document_calls = 0

    def embed_documents(self, texts):
        self.document_calls += 1
        return [[1.0, 0.1, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.1, 0.0]


class ScriptedModel:
    """Deterministic model responses for workflow tests, never claimed as LLM evaluation."""

    def __init__(self):
        self.retrieval_calls = 0
        self.bad_citations = False
        self.unsupported = False
        self.grading_calls = 0
        self.fail_grade_once = False
        self.seen_weak_points = []

    def route(self, request):
        return "quiz" if "出题" in request["message"] else "ask"

    def retrieve(self, request, kb, broaden=False):
        self.retrieval_calls += 1
        return [
            e.model_dump(mode="json")
            for e in kb.search(
                request["message"], request["course_id"], request.get("chapter", ""), broaden
            )
        ]

    def _citation(self, data):
        evidence = data["evidence"][0]
        return {
            "chunk_id": "invented" if self.bad_citations else evidence["chunk_id"],
            "quote": evidence["content"][:50],
        }

    def answer(self, data):
        return {"answer": "三次握手同步双方初始序列号。", "citations": [self._citation(data)]}

    def plan(self, data):
        self.seen_weak_points = data["weak_points"]
        return {
            "points": [
                {
                    "name": "三次握手",
                    "summary": "同步序列号",
                    "importance": 5,
                    "question_count": 1,
                    "question_types": ["short_answer"],
                    "citations": [self._citation(data)],
                }
            ]
        }

    def quiz(self, data):
        return {
            "questions": [
                {
                    "id": "model-id",
                    "knowledge_point": "三次握手",
                    "question_type": "short_answer",
                    "stem": "为什么 TCP 需要三次握手？",
                    "options": [],
                    "reference_answer": "同步双方初始序列号并确认双方收发能力。",
                    "core_point": "这题答题核心点：序列号同步与确认",
                    "must_include": ["序列号"],
                    "common_mistakes": ["只写连接可靠"],
                    "scoring_tips": "先写三步报文，再写确认关系。",
                    "source_type": "teacher_ppt",
                    "citations": [self._citation(data)],
                }
            ]
        }

    def verify(self, data):
        return {
            "supported": not self.unsupported,
            "issues": ["unsupported"] if self.unsupported else [],
        }

    def grade(self, data):
        self.grading_calls += 1
        if self.fail_grade_once:
            self.fail_grade_once = False
            raise RuntimeError("simulated provider outage")
        return {
            "items": [
                {
                    "question_id": q["id"],
                    "score": 30,
                    "error_type": "概念遗漏",
                    "feedback": "需说明序列号同步",
                    "missing_points": ["序列号"],
                }
                for q in data["quiz"]["questions"]
            ]
        }


@pytest.fixture
def settings():
    return Settings(_env_file=None, embedding_dimensions=3)


@pytest.fixture
def system(settings):
    store = MemoryStore()
    kb = KnowledgeBase(store, TestEmbeddings(), settings)
    kb.ingest(
        MaterialInput(
            course_id="net",
            chapter="TCP",
            title="授课材料",
            source_type="teacher_ppt",
            markdown="# 三次握手\n\nTCP 三次握手同步双方初始序列号并确认双方收发能力。",
        )
    )
    model = ScriptedModel()
    return FinalReviewAgent(store, kb, model, settings)
