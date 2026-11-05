"""Durable material processing, shared by the worker and in-memory tests."""

import logging
import time
from pathlib import Path

from langchain_openai import OpenAIEmbeddings

from .config import Settings
from .material_conversion import IMAGE_FORMATS, convert_material
from .postgres import PostgresStore
from .rag import KnowledgeBase
from .schemas import MaterialInput
from .slide_understanding import SlideInterpreter, VisionServiceUnavailable, material_quality
from .source_locators import located_sections, material_version

logger = logging.getLogger(__name__)


def process_material_job(store, kb: KnowledgeBase, job: dict, max_bytes: int) -> None:
    """Process a claimed job; the attempt number fences out stale workers."""
    token = store.bind_user(job["user_id"]) if hasattr(store, "bind_user") else None
    interpreter = None
    try:
        document = store.get("document", job["document_id"])
        if not document:
            raise ValueError("原始资料不存在")
        course = store.get("course", job["course_id"])
        if course and course.get("status") in {"deleted", "purged"}:
            raise ValueError("课程已删除，资料处理已停止")
        path = Path(document["file_path"])
        if not path.is_absolute():
            # Older records stored paths relative to the API's project directory.
            path = Path(__file__).resolve().parents[2] / path
        if not path.is_file():
            raise ValueError("原始文件不存在，请重新上传")
        if path.suffix.lower() in IMAGE_FORMATS:
            store.update_material_job(job["job_id"], job["attempts"], stage="ocr")
        content = path.read_bytes()
        if path.suffix.lower() in {".ppt", ".pptx"} and kb.settings.material_vision_enabled:
            interpreter = SlideInterpreter(kb.settings)
        converted = convert_material(
            content,
            document["file_name"],
            max_bytes,
            stage_callback=lambda stage: store.update_material_job(
                job["job_id"], job["attempts"], stage=stage
            ),
            interpreter=interpreter,
            cache_dir=path.with_suffix(".analysis"),
        )
        markdown = converted.markdown
        sections = converted.sections or located_sections(content, document["file_name"])
        material = MaterialInput(
            document_id=document["document_id"],
            course_id=document["course_id"],
            title=document["title"],
            source_type=document["source_type"],
            chapter=document.get("chapter", ""),
            markdown=markdown,
        )
        prepared, chunks = kb.prepare(
            material,
            source_origin="user_upload",
            sections=sections,
            plain_text=converted.pipeline is not None or path.suffix.lower() == ".txt",
            stage_callback=lambda stage: store.update_material_job(
                job["job_id"], job["attempts"], stage=stage
            ),
        )
        ready = {**document, **prepared, "parse_status": "ready"}
        if converted.pages is not None:
            ready["analysis_pages"] = converted.pages
            ready["processing_pipeline"] = converted.pipeline
            ready["quality_status"] = material_quality(converted.pages)
            ready.pop("material_version_id", None)
            for chunk in chunks:
                from .storage import stable_key

                chunk["chunk_id"] = stable_key(
                    chunk["chunk_id"], converted.pipeline, chunk["content"]
                )
        ready["material_version_id"] = material_version(ready)["material_version_id"]
        ready.pop("parse_error", None)
        store.publish_material_job(job["job_id"], job["attempts"], ready, chunks)
    except VisionServiceUnavailable as exc:
        store.fail_material_job(
            job["job_id"], job["attempts"], code="vision_unavailable", message=str(exc), retry=False
        )
    except ValueError as exc:
        store.fail_material_job(
            job["job_id"], job["attempts"], code="invalid_material", message=str(exc), retry=False
        )
    except Exception:
        logger.exception("资料 Job 处理失败: %s", job["job_id"])
        store.fail_material_job(
            job["job_id"],
            job["attempts"],
            code="processing_unavailable",
            message="资料处理服务暂时不可用，请稍后重试",
            retry=job["attempts"] < job["max_attempts"],
        )
    finally:
        if interpreter is not None:
            interpreter.close()
        if token is not None:
            store.reset_user(token)


def main() -> None:
    settings = Settings()
    key = settings.embedding_api_key.get_secret_value()
    database_url = settings.database_url.get_secret_value()
    if not key or not database_url:
        raise RuntimeError("worker 需要 DATABASE_URL 和 EMBEDDING_API_KEY")
    store = PostgresStore(database_url)
    store.setup()
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=key,
        base_url=settings.embedding_base_url,
        dimensions=settings.embedding_dimensions,
        request_timeout=settings.model_timeout,
        max_retries=2,
        check_embedding_ctx_length=False,
    )
    kb = KnowledgeBase(store, embeddings, settings)
    try:
        while True:
            job = store.claim_material_job()
            if job is None:
                time.sleep(1)
                continue
            process_material_job(store, kb, job, settings.max_upload_mb * 1024 * 1024)
    finally:
        store.close()


if __name__ == "__main__":
    main()
