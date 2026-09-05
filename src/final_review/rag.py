import math
import re
from hashlib import sha256

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import ConfigDict

from .config import Settings
from .policy import SOURCE_PRIORITY
from .schemas import Evidence, MaterialInput
from .storage import Store, stable_key


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

    def ingest(self, material: MaterialInput) -> dict:
        cleaned = clean_markdown(material.markdown)
        document_id = stable_key(
            material.course_id,
            material.title,
            material.chapter,
            material.source_type.value,
            sha256(cleaned.encode()).hexdigest(),
        )
        existing = self.store.get("document", document_id)
        if existing:
            return {"document_id": document_id, "chunks": existing["chunk_count"], "cached": True}
        texts = list(dict.fromkeys(self.splitter.split_text(cleaned)))
        vectors = []
        for start in range(0, len(texts), 32):
            batch = texts[start : start + 32]
            result = self.embeddings.embed_documents(batch)
            validate_vectors(result, len(batch), self.settings.embedding_dimensions)
            vectors.extend(result)
        metadata = material.model_dump(mode="json", exclude={"markdown"})
        chunks = [
            {
                **metadata,
                "document_id": document_id,
                "chunk_id": stable_key(document_id, str(i)),
                "content": content,
                "embedding": vectors[i],
            }
            for i, content in enumerate(texts)
        ]
        self.store.ingest(
            {
                **metadata,
                "document_id": document_id,
                "markdown": material.markdown,
                "cleaned_markdown": cleaned,
                "chunk_count": len(chunks),
            },
            chunks,
        )
        return {"document_id": document_id, "chunks": len(chunks), "cached": False}

    def search(self, query: str, course: str, chapter: str = "", broaden: bool = False):
        vector = self.embeddings.embed_query(query)
        validate_vectors([vector], 1, self.settings.embedding_dimensions)
        limit = min(100, self.settings.retrieval_candidates * (2 if broaden else 1))
        rows = self.store.search(vector, course, chapter, limit)
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
