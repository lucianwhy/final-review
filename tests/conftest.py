from contextlib import contextmanager
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
        self.job_user = "local-user"

    def bind_user(self, user_id):
        return user_id

    def reset_user(self, token):
        pass

    def claim_note_job(self):
        with self.lock:
            for job in self.tables.get("note_job", {}).values():
                if job["status"] == "queued":
                    job["status"] = "running"
                    job["attempts"] += 1
                    return deepcopy(job)
        return None

    def claim_quiz_job(self):
        from datetime import UTC, datetime, timedelta

        with self.lock:
            now = datetime.now(UTC)
            for job in self.tables.get("quiz_job", {}).values():
                if job["status"] == "queued" or (
                    job["status"] == "running" and datetime.fromisoformat(job["lease_until"]) < now
                ):
                    job["attempts"] += 1
                    if job["attempts"] > job["max_attempts"]:
                        job.update(status="failed", error="生成多次中断，请重试", lease_until=None)
                        continue
                    job.update(
                        status="running",
                        error=None,
                        lease_until=(now + timedelta(minutes=15)).isoformat(),
                    )
                    return deepcopy(job)
        return None

    def claim_export_job(self):
        from datetime import UTC, datetime, timedelta

        with self.lock:
            now = datetime.now(UTC)
            for job in self.tables.get("export_job", {}).values():
                if job["status"] == "queued" or (
                    job["status"] == "running" and datetime.fromisoformat(job["lease_until"]) < now
                ):
                    job["attempts"] += 1
                    if job["attempts"] > job["max_attempts"]:
                        job.update(status="failed", error="导出多次中断，请重试", lease_until=None)
                        continue
                    job.update(
                        status="running",
                        error=None,
                        lease_until=(now + timedelta(minutes=10)).isoformat(),
                    )
                    return deepcopy(job)
        return None

    def finish_export_job(self, job_id, attempt, *, result=None, error=None):
        with self.lock:
            job = self.tables.get("export_job", {}).get(job_id)
            if not job or job["status"] != "running" or job["attempts"] != attempt:
                return False
            job.update(
                status="failed" if error else "succeeded",
                result=result,
                error=error,
                lease_until=None,
            )
            return True

    def finish_note_job(self, job_id, attempt, *, result=None, error=None):
        with self.lock:
            job = self.tables["note_job"][job_id]
            if job["status"] != "running" or job["attempts"] != attempt:
                return False
            job.update(status="failed" if error else "succeeded", result=result, error=error)
            if error:
                job["publish_started"] = False
            return True

    def cancel_note_job(self, job_id):
        with self.lock:
            job = self.tables["note_job"].get(job_id)
            if job is None:
                return "missing"
            if job["status"] == "cancelled":
                return "cancelled"
            if job["status"] not in {"queued", "running", "failed"}:
                return "completed"
            if job.get("publish_started"):
                return "publishing"
            job.update(status="cancelled", lease_until=None, error=None, partial_batches={})
            return "cancelled"

    def begin_note_publish(self, job_id, attempt):
        with self.lock:
            job = self.tables["note_job"][job_id]
            if job["status"] != "running" or job["attempts"] != attempt:
                return False
            job["publish_started"] = True
            return True

    def update_note_job_progress(
        self, job_id, attempt, stage, *, batch_index=None, batch_result=None, selection_plan=None
    ):
        with self.lock:
            job = self.tables["note_job"][job_id]
            if job["status"] != "running" or job["attempts"] != attempt:
                return False
            job["stage"] = stage
            if selection_plan is not None:
                job["selection_plan"] = deepcopy(selection_plan)
            if batch_index is not None and batch_result is not None:
                job.setdefault("partial_batches", {})[str(batch_index)] = deepcopy(batch_result)
            return True

    def renew_note_job(self, job_id, attempt):
        with self.lock:
            job = self.tables["note_job"][job_id]
            return job["status"] == "running" and job["attempts"] == attempt

    @contextmanager
    def transaction(self):
        with self.lock:
            tables, chunks = deepcopy(self.tables), deepcopy(self.chunks)
            try:
                yield
            except Exception:
                self.tables, self.chunks = tables, chunks
                raise

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

    def delete(self, table, key):
        with self.lock:
            self.tables.get(table, {}).pop(key, None)

    def delete_document(self, key):
        with self.lock:
            self.tables.get("document", {}).pop(key, None)
            self.chunks = [chunk for chunk in self.chunks if chunk["document_id"] != key]

    def deindex_document(self, key):
        with self.lock:
            self.chunks = [chunk for chunk in self.chunks if chunk["document_id"] != key]

    def update_material_metadata(self, key, changes):
        from datetime import UTC, datetime

        with self.lock:
            document = self.tables.get("document", {}).get(key)
            if (
                document is None
                or document.get("parse_status") not in {"ready", "failed"}
                or document.get("updated_at") != changes["expected_updated_at"]
            ):
                return None
            for field in ("title", "chapter", "source_type"):
                document[field] = changes[field]
            document.pop("material_version_id", None)
            document["updated_at"] = datetime.now(UTC).isoformat()
            for chunk in self.chunks:
                if chunk["document_id"] == key:
                    for field in ("title", "chapter", "source_type"):
                        chunk[field] = document[field]
            return deepcopy(document)

    def ingest(self, document, chunks):
        with self.lock:
            if "user_id" in document:
                from final_review.source_locators import material_version

                document = {**document, "parse_status": document.get("parse_status", "ready")}
                document["material_version_id"] = material_version(document)["material_version_id"]
            self.put("document", document["document_id"], document)
            self.chunks = [c for c in self.chunks if c["document_id"] != document["document_id"]]
            self.chunks.extend(deepcopy(chunks))
            if "user_id" in document:
                self.ensure_material_source(document)

    def search(self, vector, course, chapter, limit, document_ids=None):
        rows = []
        for chunk in self.chunks:
            document = self.tables.get("document", {}).get(chunk["document_id"])
            if document and document.get("parse_status", "ready") != "ready":
                continue
            if chunk["course_id"] != course or (chapter and chunk["chapter"] != chapter):
                continue
            if document_ids is not None and chunk["document_id"] not in document_ids:
                continue
            embedding = chunk["embedding"]
            score = sum(a * b for a, b in zip(vector, embedding, strict=True)) / (
                sqrt(sum(x * x for x in vector)) * sqrt(sum(x * x for x in embedding))
            )
            rows.append(
                {**{k: v for k, v in chunk.items() if k != "embedding"}, "similarity": score}
            )
        return sorted(rows, key=lambda c: -c["similarity"])[:limit]

    def create_material_job(self, document, job):
        with self.lock:
            if job["idempotency_key"]:
                existing = self.find_material_job(job["course_id"], job["idempotency_key"])
                if existing:
                    return existing
            self.put("document", document["document_id"], document)
            row = {
                **job,
                "user_id": self.job_user,
                "status": "queued",
                "stage": None,
                "attempts": 0,
                "max_attempts": 3,
                "error_code": None,
                "error_message": None,
            }
            self.put("material_job", job["job_id"], row)
            return deepcopy(row)

    def find_material_job(self, course_id, key):
        return next(
            (
                deepcopy(job)
                for job in self.tables.get("material_job", {}).values()
                if job["course_id"] == course_id and job["idempotency_key"] == key
            ),
            None,
        )

    def find_duplicate_material(self, course_id, file_name, content_sha256):
        with self.lock:
            for document in self.tables.get("document", {}).values():
                if (
                    document.get("course_id") == course_id
                    and document.get("file_name") == file_name
                    and document.get("content_sha256") == content_sha256
                    and document.get("parse_status") != "deleted"
                ):
                    job = next(
                        (
                            row
                            for row in self.tables.get("material_job", {}).values()
                            if row["document_id"] == document["document_id"]
                        ),
                        None,
                    )
                    return {"document": deepcopy(document), "job": deepcopy(job) if job else None}
        return None

    def get_material_job(self, job_id):
        return self.get("material_job", job_id)

    def list_material_jobs(self, course_id):
        return self.scan("material_job", {"course_id": course_id})

    def claim_material_job(self):
        with self.lock:
            for job in self.tables.get("material_job", {}).values():
                if job["status"] == "queued":
                    job.update(status="running", stage="parse", attempts=job["attempts"] + 1)
                    self.tables["document"][job["document_id"]]["parse_status"] = "running"
                    return deepcopy(job)
        return None

    def update_material_job(self, job_id, attempt, *, stage=None, **_kwargs):
        with self.lock:
            job = self.tables["material_job"][job_id]
            if job["status"] == "running" and job["attempts"] == attempt and stage:
                job["stage"] = stage

    def fail_material_job(self, job_id, attempt, *, code, message, retry):
        with self.lock:
            job = self.tables["material_job"][job_id]
            if job["status"] != "running" or job["attempts"] != attempt:
                return False
            job.update(
                status="queued" if retry else "failed", error_code=code, error_message=message
            )
            document = self.tables["document"].get(job["document_id"])
            if document and document.get("parse_status") != "deleted":
                document.update(parse_status=job["status"], parse_error=message)
            return True

    def publish_material_job(self, job_id, attempt, document, chunks):
        with self.lock:
            job = self.tables["material_job"][job_id]
            if job["status"] != "running" or job["attempts"] != attempt:
                return False
            if self.tables["document"][job["document_id"]].get("parse_status") == "deleted":
                job.update(status="failed", error_code="source_removed", error_message="资料已删除")
                return False
            self.ingest(document, chunks)
            self.ensure_material_source(document)
            job.update(status="succeeded", stage="index")
            return True

    def list_material_chunks(self, document_id):
        return sorted(
            (deepcopy(chunk) for chunk in self.chunks if chunk["document_id"] == document_id),
            key=lambda chunk: chunk.get("chunk_ordinal", 0),
        )

    def ensure_material_source(self, document):
        from final_review.source_locators import chunk_locator, material_version

        version = material_version(document)
        self.put("material_version", version["material_version_id"], version)
        for ordinal, chunk in enumerate(self.list_material_chunks(document["document_id"])):
            locator = chunk_locator(version, chunk, ordinal)
            self.put("source_locator", locator["locator_id"], locator)
        return version

    def retry_material_job(self, job_id):
        with self.lock:
            job = self.tables["material_job"][job_id]
            if job["status"] != "failed":
                return None
            if self.tables["document"][job["document_id"]].get("parse_status") == "deleted":
                return None
            job.update(status="queued", attempts=0, stage=None, error_code=None, error_message=None)
            document = self.tables["document"][job["document_id"]]
            document["parse_status"] = "queued"
            document.pop("parse_error", None)
            return deepcopy(job)


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
    def quiz_draft_plan(self, context, issues=None):
        from quiz_fixture import fixture_plan

        return fixture_plan(context)

    def quiz_draft(self, context, plan):
        from quiz_fixture import fixture_draft

        return fixture_draft(context, plan)

    def review_quiz_draft(self, context, draft):
        return {
            "items": [
                {"question_id": q["id"], "supported": True, "issues": []}
                for q in draft["questions"]
            ]
        }

    def repair_quiz_draft(self, context, plan, draft, issues):
        from final_review.quiz_contract import QuizDraftPlan

        return self.quiz_draft(context, QuizDraftPlan.model_validate(plan))

    """Deterministic model responses for workflow tests, never claimed as LLM evaluation."""

    def __init__(self):
        self.retrieval_calls = 0
        self.bad_citations = False
        self.unsupported = False
        self.grading_calls = 0
        self.fail_grade_once = False
        self.seen_weak_points = []

    def route(self, request):
        if "出题" in request["message"]:
            return "quiz"
        return "note" if "笔记" in request["message"] else "ask"

    def note_request(self, request):
        message = request["message"]
        return {
            "note_type": None,
            "scope": "重点处理第二章和第三章，适合背诵" if "老师说" in message else "",
            "duration_minutes": None,
            "emphasis": [],
            "audience_level": None,
            "source_types": [],
        }

    def note(self, data):
        evidence = data["evidence"][0]
        return {
            "title": "TCP 复习笔记",
            "points": [
                {
                    "heading": "三次握手",
                    "content": "同步双方初始序列号并确认收发能力。",
                    "provenance": "source",
                    "citations": [
                        {"chunk_id": evidence["chunk_id"], "quote": evidence["content"][:30]}
                    ],
                }
            ],
        }

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
    return Settings(_env_file=None, embedding_dimensions=3, material_vision_enabled=False)


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
