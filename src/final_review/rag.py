import math
import re
from hashlib import sha256
from typing import Callable

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import ConfigDict

from .config import Settings
from .plain_material_text import plain_material_text
from .policy import SOURCE_PRIORITY
from .schemas import Evidence, MaterialInput
from .storage import Store, stable_key

EMBEDDING_BATCH_SIZE = 10


def clean_markdown(text: str) -> str:
    # Preserve equations, code blocks, headings and repeated steps. Deduplication is
    # performed on whole chunks below, avoiding accidental deletion of proof steps.
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    if text.count("\ufffd") > max(3, len(text) // 100):
        raise ValueError("资料乱码过多，请重新转换或提供可读 Markdown")
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    if not text:
        raise ValueError("资料清洗后为空；扫描 PDF 需要先 OCR")
    return text


def validate_vectors(vectors: list[list[float]], count: int, dimensions: int):
    if len(vectors) != count:
        raise ValueError("Embedding 返回数量错误")
    for vector in vectors:
        if len(vector) != dimensions or not all(math.isfinite(x) for x in vector):
            raise ValueError("Embedding 维度或数值错误")
        if not any(vector):
            raise ValueError("Embedding 不允许零向量")


class KnowledgeBase:
    def __init__(self, store: Store, embeddings: Embeddings, settings: Settings):
        self.store, self.embeddings, self.settings = store, embeddings, settings
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=1200,
            chunk_overlap=150,
            separators=["\n## ", "\n\n", "\n", "。", "；", " ", ""],
        )

    def ingest(
        self,
        material: MaterialInput,
        *,
        source_origin: str = "user_entry",
        user_id: str | None = None,
    ) -> dict:
        if material.document_id is not None:
            existing = self.store.get("document", material.document_id)
            if existing and existing.get("parse_status") == "ready":
                return {
                    "document_id": material.document_id,
                    "chunks": existing["chunk_count"],
                    "cached": True,
                }
        else:
            cleaned = clean_markdown(plain_material_text(material.markdown))
            key = stable_key(
                material.course_id,
                material.title,
                material.chapter,
                material.source_type.value,
                sha256(cleaned.encode()).hexdigest(),
            )
            existing = self.store.get("document", key)
            if existing:
                return {"document_id": key, "chunks": existing["chunk_count"], "cached": True}
        document, chunks = self.prepare(material, source_origin=source_origin)
        if user_id is not None:
            document["user_id"] = user_id
            document["parse_status"] = "ready"
        self.store.ingest(document, chunks)
        return {"document_id": document["document_id"], "chunks": len(chunks), "cached": False}

    def prepare(
        self,
        material: MaterialInput,
        *,
        source_origin: str = "user_entry",
        stage_callback: Callable[[str], None] | None = None,
        sections: list[dict] | None = None,
        plain_text: bool = False,
    ) -> tuple[dict, list[dict]]:
        if stage_callback:
            stage_callback("clean")
        cleaned = clean_markdown(
            material.markdown if plain_text else plain_material_text(material.markdown)
        )
        document_id = material.document_id or stable_key(
            material.course_id,
            material.title,
            material.chapter,
            material.source_type.value,
            sha256(cleaned.encode()).hexdigest(),
        )
        units = []
        if sections:
            for section in sections:
                source_text = (
                    section["text"] if plain_text else plain_material_text(section["text"])
                )
                source_text = clean_markdown(source_text) if source_text.strip() else ""
                if not source_text:
                    continue
                cursor = 0
                for text in self.splitter.split_text(source_text):
                    start = source_text.find(text, cursor)
                    if start < 0:
                        start = source_text.find(text)
                    units.append(
                        (
                            text,
                            section["position_kind"],
                            section["position"],
                            start if start >= 0 else None,
                            start + len(text) if start >= 0 else None,
                        )
                    )
                    cursor = start + max(1, len(text) - 150) if start >= 0 else 0
        else:
            cursor = 0
            for text in self.splitter.split_text(cleaned):
                start = cleaned.find(text, cursor)
                if start < 0:
                    start = cleaned.find(text)
                units.append(
                    (
                        text,
                        "document",
                        None,
                        start if start >= 0 else None,
                        start + len(text) if start >= 0 else None,
                    )
                )
                cursor = start + max(1, len(text) - 150) if start >= 0 else 0
        texts = [unit[0] for unit in units]
        if stage_callback:
            stage_callback("index")
        vectors = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]
            result = self.embeddings.embed_documents(batch)
            validate_vectors(result, len(batch), self.settings.embedding_dimensions)
            vectors.extend(result)
        metadata = material.model_dump(mode="json", exclude={"markdown"})
        chunks = [
            {
                **metadata,
                "document_id": document_id,
                "chunk_id": stable_key(document_id, str(i)),
                "chunk_ordinal": i,
                "content": content,
                "position_kind": units[i][1],
                "position": units[i][2],
                "text_start": units[i][3],
                "text_end": units[i][4],
                "embedding": vectors[i],
            }
            for i, content in enumerate(texts)
        ]
        return (
            {
                **metadata,
                "document_id": document_id,
                "source_origin": source_origin,
                "markdown": material.markdown,
                "cleaned_markdown": cleaned,
                "chunk_count": len(chunks),
            },
            chunks,
        )

    def search(
        self,
        query: str,
        course: str,
        chapter: str = "",
        broaden: bool = False,
        document_ids: list[str] | None = None,
    ):
        vector = self.embeddings.embed_query(query)
        validate_vectors([vector], 1, self.settings.embedding_dimensions)
        limit = min(100, self.settings.retrieval_candidates * (2 if broaden else 1))
        rows = (
            self.store.search(vector, course, chapter, limit)
            if document_ids is None
            else self.store.search(vector, course, chapter, limit, document_ids=document_ids)
        )
        return self._rank(rows)

    def search_chunks(self, query: str, chunks: list[dict]):
        vector = self.embeddings.embed_query(query)
        validate_vectors([vector], 1, self.settings.embedding_dimensions)
        rows = []
        for chunk in chunks:
            embedding = chunk["embedding"]
            validate_vectors([embedding], 1, self.settings.embedding_dimensions)
            score = sum(a * b for a, b in zip(vector, embedding, strict=True)) / (
                math.sqrt(sum(a * a for a in vector)) * math.sqrt(sum(b * b for b in embedding))
            )
            rows.append(
                {
                    **{key: value for key, value in chunk.items() if key != "embedding"},
                    "similarity": score,
                }
            )
        return self._rank(rows)

    def _rank(self, rows):
        candidates = []
        for row in rows:
            item = Evidence.model_validate(row)
            if not math.isfinite(item.similarity) or item.similarity < self.settings.min_similarity:
                continue
            # Bounded bonus cannot rescue irrelevant evidence filtered out above.
            item.rank_score = item.similarity + 0.04 * SOURCE_PRIORITY[item.source_type]
            candidates.append(item)
        candidates.sort(key=lambda item: (-item.rank_score, item.chunk_id))
        return candidates[: self.settings.top_k]


class CourseRetriever(BaseRetriever):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    knowledge_base: KnowledgeBase
    course_id: str
    chapter: str = ""
    broaden: bool = False

    def _get_relevant_documents(self, query, *, run_manager):
        return [
            Document(page_content=e.content, metadata=e.model_dump(exclude={"content"}))
            for e in self.knowledge_base.search(query, self.course_id, self.chapter, self.broaden)
        ]
