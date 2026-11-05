import os
from uuid import uuid4

import pytest
from conftest import ScriptedModel, TestEmbeddings

from final_review.agent import FinalReviewAgent
from final_review.config import Settings
from final_review.rag import KnowledgeBase
from final_review.schemas import AgentRequest, MaterialInput, Submission
from final_review.storage import SurrealStore

pytestmark = pytest.mark.integration


@pytest.fixture
def real_store():
    url = os.getenv("SURREAL_TEST_URL")
    if not url:
        pytest.skip("Set SURREAL_TEST_URL to run real SurrealDB integration tests")
    settings = Settings(
        _env_file=None,
        surreal_url=url,
        surreal_namespace="final_review_tests",
        surreal_database="test_" + uuid4().hex,
        surreal_username=os.getenv("SURREAL_TEST_USER", "root"),
        surreal_password=os.getenv("SURREAL_TEST_PASSWORD", "test-only-final-review"),
        embedding_dimensions=3,
    )
    store = SurrealStore(settings)
    store.setup()
    try:
        yield store, settings
    finally:
        # Isolated throwaway test database only; never target configured application database.
        store.query(f"REMOVE DATABASE {settings.surreal_database};")
        store.close()


def test_real_surreal_ingest_vector_filter_and_restart(real_store):
    store, settings = real_store
    kb = KnowledgeBase(store, TestEmbeddings(), settings)
    for course in ("net", "foreign"):
        kb.ingest(
            MaterialInput(
                course_id=course,
                title="TCP",
                chapter="TCP",
                source_type="teacher_ppt",
                markdown="TCP 同步双方初始序列号。",
            )
        )
    evidence = kb.search("TCP", "net", "TCP")
    assert len(evidence) == 1 and evidence[0].course_id == "net"
    model = ScriptedModel()
    agent = FinalReviewAgent(store, kb, model, settings)
    result = agent.invoke(
        AgentRequest(
            course_id="net",
            session_id="durable",
            message="出题",
            intent="quiz",
            exam_profile={"question_types": ["short_answer"]},
        )
    )
    assert result.status == "awaiting_answers"
    # New DB connection, checkpointer and graph prove state is in SurrealDB.
    reconnected = SurrealStore(settings)
    try:
        new_kb = KnowledgeBase(reconnected, TestEmbeddings(), settings)
        restarted = FinalReviewAgent(reconnected, new_kb, model, settings)
        graded = restarted.evaluate(
            Submission(
                course_id="net",
                session_id="durable",
                answers={q["id"]: "连接" for q in result.questions},
            )
        )
        assert graded.assessment["score"] == 30
        assert graded.weak_points == ["三次握手"]
        assert reconnected.scan("knowledge_point", {"course_id": "net"})
    finally:
        reconnected.close()


def test_real_surreal_embedding_space_guard(real_store):
    store, settings = real_store
    changed = settings.model_copy(update={"embedding_model": "different-model"})
    other = SurrealStore(changed)
    try:
        with pytest.raises(RuntimeError, match="Embedding"):
            other.setup()
    finally:
        other.close()


def test_real_surreal_transaction_rollback_and_bound_text(real_store):
    from final_review.storage import StorageError

    store, settings = real_store
    kb = KnowledgeBase(store, TestEmbeddings(), settings)
    title = "引号 ' ; DELETE document; -- 仅是文本"
    result = kb.ingest(
        MaterialInput(
            course_id="net",
            title=title,
            source_type="homework",
            markdown="原始课程内容",
        )
    )
    document = store.get("document", result["document_id"])
    assert document["title"] == title
    with pytest.raises(StorageError):
        store.ingest({**document, "title": "应回滚"}, [42])
    assert store.get("document", result["document_id"])["title"] == title
    assert kb.search("内容", "net")[0].title == title
