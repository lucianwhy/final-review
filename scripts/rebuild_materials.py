"""Build candidate visual material versions; publish atomically only after preparation.

Run from the project directory. --pages is preview-only and cannot publish partial decks.
"""

import argparse
import json
from pathlib import Path

from langchain_openai import OpenAIEmbeddings

from final_review.config import Settings
from final_review.material_conversion import convert_material
from final_review.postgres import PostgresStore
from final_review.rag import KnowledgeBase
from final_review.schemas import MaterialInput
from final_review.slide_understanding import SlideInterpreter, material_quality
from final_review.source_locators import material_version
from final_review.storage import stable_key


def rebuild(document: dict, settings: Settings, *, pages=None):
    path = Path(document["file_path"])
    interpreter = SlideInterpreter(settings)
    try:
        converted = convert_material(
            path.read_bytes(),
            document["file_name"],
            settings.max_upload_mb * 1024 * 1024,
            interpreter=interpreter,
            cache_dir=path.with_suffix(".analysis"),
            page_filter=pages,
            stage_callback=lambda stage: print(document["document_id"], stage, flush=True),
        )
    finally:
        interpreter.close()
    report = path.with_suffix(".analysis") / "candidate.json"
    report.write_text(
        json.dumps(
            {
                "markdown": converted.markdown,
                "pages": converted.pages,
                "sections": converted.sections,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return converted, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--pages", help="Comma separated slide numbers, preview only")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if args.pages and args.publish:
        parser.error("Cannot publish an incomplete deck")
    settings = Settings()
    store = PostgresStore(settings.database_url.get_secret_value())
    # Lookup owner through the administrative rebuild CLI, never through browser input.
    with store.connection.cursor() as cursor:
        cursor.execute(
            "SELECT user_id,data FROM documents WHERE document_id=%s", (args.document_id,)
        )
        row = cursor.fetchone()
    if not row or row["data"].get("parse_status") != "ready":
        raise ValueError("Only existing ready materials can be rebuilt")
    document = row["data"]
    token = store.bind_user(str(row["user_id"]))
    try:
        pages = {int(value) for value in args.pages.split(",")} if args.pages else None
        converted, report = rebuild(document, settings, pages=pages)
        print("Candidate:", report, flush=True)
        if not args.publish:
            return
        # Only verified blocks are published; uncertain pages stay visible for review.
        uncertain = [p["position"] for p in converted.pages if p["quality"] != "verified"]
        if uncertain:
            print("Excluded pages needing review:", uncertain, flush=True)
        embeddings = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.embedding_api_key.get_secret_value(),
            base_url=settings.embedding_base_url,
            dimensions=settings.embedding_dimensions,
            request_timeout=settings.model_timeout,
            max_retries=2,
            check_embedding_ctx_length=False,
        )
        kb = KnowledgeBase(store, embeddings, settings)
        material = MaterialInput(
            document_id=document["document_id"],
            course_id=document["course_id"],
            title=document["title"],
            source_type=document["source_type"],
            chapter=document.get("chapter", ""),
            markdown=converted.markdown,
        )
        prepared, chunks = kb.prepare(
            material, source_origin="user_upload", sections=converted.sections, plain_text=True
        )
        ready = {
            **document,
            **prepared,
            "processing_pipeline": converted.pipeline,
            "analysis_pages": converted.pages,
            "quality_status": material_quality(converted.pages),
        }
        history = {item["chunk_id"]: item for item in document.get("historical_chunks", [])}
        for old in store.list_material_chunks(document["document_id"]):
            history[old["chunk_id"]] = {
                key: value for key, value in old.items() if key != "embedding"
            } | {"material_version_id": document.get("material_version_id")}
        ready["historical_chunks"] = list(history.values())
        ready.pop("material_version_id", None)
        ready["material_version_id"] = material_version(ready)["material_version_id"]
        for chunk in chunks:
            chunk["chunk_id"] = stable_key(chunk["chunk_id"], converted.pipeline, chunk["content"])
        with store.transaction():
            # Fence deletion or metadata changes while model requests were in flight.
            with store.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT data FROM documents WHERE document_id=%s AND user_id=%s FOR UPDATE",
                    (document["document_id"], row["user_id"]),
                )
                latest = cursor.fetchone()
                if latest is None or latest["data"] != document:
                    raise ValueError("Material changed during rebuild; old version preserved")
            store.ingest(ready, chunks)
        print("Published:", document["document_id"], len(chunks), "chunks", flush=True)
    finally:
        store.reset_user(token)
        store.close()


if __name__ == "__main__":
    main()
