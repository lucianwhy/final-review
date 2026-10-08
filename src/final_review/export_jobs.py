"""Durable note exports; the API never runs document conversion."""

import logging
import time
from hashlib import sha256
from pathlib import Path

from .config import Settings
from .domain import DomainService
from .exports import EXTENSIONS, ExportRenderError, read_export, render_file
from .postgres import PostgresStore

logger = logging.getLogger(__name__)


def result_path(settings: Settings, job: dict) -> Path:
    root = Path(settings.exports_dir).resolve()
    relative = (job.get("result") or {}).get("file_name")
    if not relative:
        raise ExportRenderError("导出文件尚未就绪")
    path = (root / relative).resolve()
    if path.parent != root or not path.is_file():
        raise ExportRenderError("导出文件不可用，请重新生成")
    return path


def process_export_job(store, settings: Settings, job: dict) -> None:
    token = store.bind_user(job["user_id"])
    root = Path(settings.exports_dir).resolve()
    # Attempt-specific paths prevent a worker with an expired lease overwriting a newer result.
    name = f"{job['export_id']}-{job['attempts']}.{EXTENSIONS[job['format']]}"
    output, temporary = root / name, root / f"{name}.tmp"
    try:
        service = DomainService(store, job["user_id"])
        read_export(service, job["export_id"])
        root.mkdir(parents=True, exist_ok=True)
        render_file(job["snapshot"], job["format"], temporary, settings)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ExportRenderError("文档转换未产生文件，请重试")
        file_hash = sha256(temporary.read_bytes()).hexdigest()
        # Recheck deletion and lease before publication; rendering holds no DB lock.
        with store.transaction():
            service._locked_asset(job["asset_id"])
            course_get = getattr(store, "get_for_update", store.get)
            course_get("course", job["course_id"])
            read_export(service, job["export_id"])
            temporary.replace(output)
            if not store.finish_export_job(
                job["export_id"],
                job["attempts"],
                result={
                    "file_name": name,
                    "file_hash": file_hash,
                    "size": output.stat().st_size,
                },
            ):
                output.unlink(missing_ok=True)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        logger.exception("笔记导出失败: %s", job["export_id"])
        error = str(exc) if isinstance(exc, ExportRenderError) else "导出失败，请检查笔记状态后重试"
        store.finish_export_job(job["export_id"], job["attempts"], error=error)
    finally:
        store.reset_user(token)


def main() -> None:
    settings = Settings()
    database_url = settings.database_url.get_secret_value()
    if not database_url:
        raise RuntimeError("导出 worker 需要 DATABASE_URL")
    store = PostgresStore(database_url)
    store.setup()
    try:
        while True:
            job = store.claim_export_job()
            if job is None:
                time.sleep(1)
                continue
            process_export_job(store, settings, job)
    finally:
        store.close()


if __name__ == "__main__":
    main()
