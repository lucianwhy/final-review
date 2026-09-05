import pytest

from final_review.rag import clean_markdown, validate_vectors
from final_review.schemas import MaterialInput


def test_dedup_ingestion_avoids_embedding_cost(system):
    material = MaterialInput(
        course_id="net", title="repeat", source_type="homework", markdown="定义"
    )
    first = system.kb.ingest(material)
    calls = system.kb.embeddings.document_calls
    second = system.kb.ingest(material)
    assert first["document_id"] == second["document_id"]
    assert second["cached"]
    assert calls == system.kb.embeddings.document_calls


def test_source_priority_and_course_chapter_filter(system):
    for source in ["past_exam", "homework", "crash_course", "ai_supplement"]:
        system.kb.ingest(
            MaterialInput(
                course_id="net",
                title=source,
                chapter="TCP",
                source_type=source,
                markdown="TCP 序列号",
            )
        )
    system.kb.ingest(
        MaterialInput(
            course_id="foreign",
            title="其他课",
            source_type="past_exam",
            chapter="TCP",
            markdown="别的课程不能召回",
        )
    )
    results = system.kb.search("TCP", "net", "TCP")
    assert [e.source_type.value for e in results] == [
        "past_exam",
        "teacher_ppt",
        "homework",
        "crash_course",
        "ai_supplement",
    ]
    assert all(e.course_id == "net" for e in results)
    assert system.kb.search("TCP", "net", "不存在的章节") == []


def test_irrelevant_exam_cannot_win_by_source_weight(system):
    system.store.chunks[0]["embedding"] = [0.0, 0.0, 1.0]
    assert system.kb.search("无关", "net") == []


@pytest.mark.parametrize("vector", [[0, 0, 0], [1, 2], [float("nan"), 0, 1]])
def test_invalid_embeddings_rejected(vector):
    with pytest.raises(ValueError):
        validate_vectors([vector], 1, 3)


def test_cleaning_preserves_formula_and_code():
    text = "\ufeff# 定理\r\n\r\n$$x^2 + y^2 = z^2$$\r\n\r\n```python\nprint(1)\nprint(1)\n```"
    cleaned = clean_markdown(text)
    assert "$$x^2 + y^2 = z^2$$" in cleaned
    assert cleaned.count("print(1)") == 2
    assert "\r" not in cleaned


@pytest.mark.parametrize("text", ["  \n\x00", "\ufffd" * 10])
def test_unreadable_material_rejected(text):
    with pytest.raises(ValueError):
        clean_markdown(text)
