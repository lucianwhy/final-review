from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from zipfile import ZipFile

import pdfplumber
import pytest
from fastapi.testclient import TestClient
from test_m2_note_review import confirm, edit_payload, setup_note

from final_review.api import create_app
from final_review.domain import DomainConflict, DomainNotFound, DomainService
from final_review.export_jobs import process_export_job, result_path
from final_review.exports import (
    ExportRenderError,
    build_snapshot,
    create_export,
    export_markdown,
    read_export,
    render_file,
    render_html,
)


def confirmed(system):
    service, asset, revision = setup_note(system)
    confirm(service, asset, revision)
    return service, asset, revision


def test_export_authorization_draft_and_revision_binding(system):
    service, asset, revision = setup_note(system)
    with pytest.raises(DomainConflict):
        create_export(service, asset, revision, "pdf")
    confirm(service, asset, revision)
    with pytest.raises(DomainNotFound):
        create_export(DomainService(system.store, "other"), asset, revision, "pdf")
    other_asset = service.create_asset(
        "net",
        {"asset_type": "note", "title": "另一个", "markdown": "正文", "source_document_ids": []},
    )
    with pytest.raises(DomainNotFound):
        create_export(service, other_asset["asset"]["asset_id"], revision, "pdf")
    for status in ("deleted", "purged"):
        course = system.store.get("course", "net")
        course["status"] = status
        system.store.put("course", "net", course)
        with pytest.raises(DomainConflict):
            create_export(service, asset, revision, "pdf")


def test_snapshot_survives_new_revision_rename_and_source_deletion(system):
    service, asset, revision = confirmed(system)
    job = create_export(service, asset, revision, "markdown")
    snapshot = deepcopy(job["snapshot"])
    assert "资料来源" in snapshot["source_markdown"]
    assert "引用摘录" in snapshot["source_markdown"]
    assert "出处与来源标记" not in snapshot["markdown"]
    assert snapshot["content_hash"] == sha256(snapshot["markdown"].encode()).hexdigest()
    assert create_export(service, asset, revision, "markdown")["export_id"] == job["export_id"]
    ref = service.note_draft(asset, revision)["revision"]["points"][0]["references"][0]
    document = system.store.get("document", ref["document_id"])
    old_name = snapshot["markdown"]
    document["file_name"] = "改名之后.pptx"
    system.store.put("document", ref["document_id"], document)
    assert build_snapshot(service, asset, revision)["markdown"] == old_name
    edited = service.edit_note(asset, edit_payload(service, asset, revision))["revision"]
    confirm(service, asset, edited["revision_id"])
    assert build_snapshot(service, asset, revision)["content_hash"] == snapshot["content_hash"]
    deletion = service.material_deletion_preview(ref["document_id"], "net")
    service.delete_material(
        ref["document_id"], deletion["confirmation_id"], "retain_source_snapshot"
    )
    deleted = build_snapshot(service, asset, revision)
    assert deleted["content_hash"] == snapshot["content_hash"]
    assert deleted["deleted_sources"]
    assert "原资料已删除" not in export_markdown(deleted)
    assert deleted["source_markdown"] == snapshot["source_markdown"]
    assert read_export(service, job["export_id"])["snapshot"] == snapshot


def test_worker_fences_stale_attempt_recovers_and_never_publishes_partial(
    system, tmp_path, monkeypatch
):
    service, asset, revision = confirmed(system)
    system.settings.exports_dir = str(tmp_path)
    job = create_export(service, asset, revision, "markdown")
    stale = system.store.claim_export_job()
    expired = system.store.get("export_job", job["export_id"])
    expired["lease_until"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    system.store.put("export_job", job["export_id"], expired)
    current = system.store.claim_export_job()
    process_export_job(system.store, system.settings, stale)
    assert read_export(service, job["export_id"])["status"] == "running"
    assert list(tmp_path.iterdir()) == []
    process_export_job(system.store, system.settings, current)
    saved = read_export(service, job["export_id"])
    assert saved["status"] == "succeeded"
    assert (
        saved["result"]["file_hash"]
        == sha256(result_path(system.settings, saved).read_bytes()).hexdigest()
    )

    failed = create_export(service, asset, revision, "pdf")

    def fail(snapshot, format, output, settings):
        output.write_bytes(b"partial")
        raise ExportRenderError("模拟转换失败")

    monkeypatch.setattr("final_review.export_jobs.render_file", fail)
    process_export_job(system.store, system.settings, system.store.claim_export_job())
    assert read_export(service, failed["export_id"])["status"] == "failed"
    assert len(list(tmp_path.iterdir())) == 1


def test_export_http_download_retry_preview_and_deletion(system, tmp_path, monkeypatch):
    service, asset, revision = confirmed(system)
    system.settings.exports_dir = str(tmp_path)
    with TestClient(create_app(system.settings, system)) as client:
        created = client.post(
            f"/api/notes/{asset}/exports", json={"revision_id": revision, "format": "markdown"}
        )
        assert created.status_code == 202
        info = created.json()
        assert "snapshot" not in info and "file_path" not in info
        base = f"/api/exports/{info['export_id']}"
        assert client.get(base + "/download").status_code == 409
        process_export_job(system.store, system.settings, system.store.claim_export_job())
        response = client.get(base + "/download")
        assert response.status_code == 200
        assert response.headers["x-revision-id"] == revision
        assert response.headers["x-content-hash"] == info["content_hash"]
        assert response.headers["x-file-hash"] == sha256(response.content).hexdigest()
        assert "filename*=utf-8" in response.headers["content-disposition"].lower()
        assert response.headers["cache-control"] == "private, no-store"
        assert client.get(base + "/preview").status_code == 409
        print_job = client.post(
            f"/api/notes/{asset}/exports", json={"revision_id": revision, "format": "print"}
        ).json()
        process_export_job(system.store, system.settings, system.store.claim_export_job())
        preview = client.get(f"/api/exports/{print_job['export_id']}/preview")
        assert preview.status_code == 200 and "<html" in preview.text
        assert "script-src 'none'" in preview.headers["content-security-policy"]
        # A missing cached file must become retryable instead of trapping repeated clicks.
        printed = read_export(service, print_job["export_id"])
        result_path(system.settings, printed).unlink()
        assert client.get(f"/api/exports/{print_job['export_id']}").json()["status"] == "failed"
        assert (
            client.post(f"/api/exports/{print_job['export_id']}/retry").json()["status"] == "queued"
        )
        process_export_job(system.store, system.settings, system.store.claim_export_job())
        retry = create_export(service, asset, revision, "pdf")
        claimed = system.store.claim_export_job()
        system.store.finish_export_job(claimed["export_id"], claimed["attempts"], error="failed")
        assert client.post(f"/api/exports/{retry['export_id']}/retry").json()["status"] == "queued"
        assert client.post(f"/api/exports/{retry['export_id']}/retry").json()["status"] == "queued"
        # Cross-owner checks apply independently to every endpoint.
        job = system.store.get("export_job", info["export_id"])
        job["user_id"] = "other"
        system.store.put("export_job", info["export_id"], job)
        for suffix in ("", "/download", "/preview"):
            assert client.get(base + suffix).status_code == 404
        assert client.post(base + "/retry").status_code == 404
        job["user_id"] = "local-user"
        system.store.put("export_job", info["export_id"], job)
        course = system.store.get("course", "net")
        course["status"] = "deleted"
        system.store.put("course", "net", course)
        assert client.get(base + "/download").status_code == 409


def test_actual_formats_keep_body_math_table_and_exclude_sources(system, tmp_path):
    service, asset, revision = confirmed(system)
    payload = edit_payload(service, asset, revision)
    payload["points"][0]["content"] = (
        "共同正文标记，考试先写定义。\n\n**加粗**与列表：\n\n- 第一项\n- 第二项\n\n"
        "| 名称 | 数值 |\n|---|---|\n| 中文表格 | 42 |\n\n"
        "公式 $x^2 + y^2 = z^2$。\n\n$$\\frac{1}{2} + \\alpha = 1$$\n\n"
        "```python\nprint('hello')\n```\n\n"
        "<script>alert('x')</script>\n\n![图片说明](http://127.0.0.1:9/leak)\n\n"
        "[危险链接](javascript:alert(1))"
        "\n\n```{=openxml}\n<w:bad>原始 XML 按文字显示</w:bad>\n```"
    )
    payload["points"].append(
        {
            "point_id": "ai",
            "heading": "记忆提示",
            "content": "AI 记忆建议",
            "provenance": "ai_supplement",
            "references": [],
        }
    )
    edited = service.edit_note(asset, payload)["revision"]["revision_id"]
    confirm(service, asset, edited)
    snapshot = build_snapshot(service, asset, edited)
    document = render_html(snapshot, system.settings)
    assert "<script>" not in document and "<img" not in document
    assert "javascript:" not in document and "http://127.0.0.1:9" not in document
    assert "<math" in document and "<table" in document
    for format, suffix in (("markdown", "md"), ("docx", "docx"), ("pdf", "pdf")):
        path = tmp_path / f"sample.{suffix}"
        render_file(snapshot, format, path, system.settings)
        if format == "markdown":
            text = path.read_text(encoding="utf-8")
        elif format == "docx":
            with ZipFile(path) as archive:
                xml = archive.read("word/document.xml").decode()
                assert "m:oMath" in xml and "w:tbl" in xml
                assert (
                    "http://127.0.0.1:9"
                    not in archive.read("word/_rels/document.xml.rels").decode()
                )
            from docx import Document

            doc = Document(path)
            text = "\n".join(p.text for p in doc.paragraphs)
        else:
            with pdfplumber.open(path, unicode_norm="NFKC") as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                assert len(pdf.pages) >= 1
        assert "共同正文标记" in text and "AI 记忆建议" in text
        for marker in (
            "出处与来源标记",
            "引用摘录",
            "来源：",
            "版本信息",
            edited,
            snapshot["content_hash"],
        ):
            assert marker not in text.replace("\n", "")


def test_output_path_never_escapes_private_directory(system, tmp_path):
    system.settings.exports_dir = str(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    with pytest.raises(ExportRenderError):
        result_path(system.settings, {"result": {"file_name": "../outside.txt"}})
    outside.unlink()


def test_course_deleted_during_conversion_cannot_publish(system, tmp_path, monkeypatch):
    service, asset, revision = confirmed(system)
    system.settings.exports_dir = str(tmp_path)
    job = create_export(service, asset, revision, "markdown")

    def delete_while_rendering(snapshot, format, output, settings):
        output.write_text(export_markdown(snapshot), encoding="utf-8")
        course = system.store.get("course", "net")
        course["status"] = "deleted"
        system.store.put("course", "net", course)

    monkeypatch.setattr("final_review.export_jobs.render_file", delete_while_rendering)
    process_export_job(system.store, system.settings, system.store.claim_export_job())
    assert system.store.get("export_job", job["export_id"])["status"] == "failed"
    assert list(tmp_path.iterdir()) == []


def test_generic_confirmed_notes_keep_original_without_inventing_point_sources(system):
    service, _, _ = confirmed(system)
    generic = service.create_asset(
        "net",
        {
            "asset_type": "note",
            "title": "历史通用笔记",
            "markdown": "# 原有正文\n\n原有知识",
            "source_document_ids": [],
        },
    )
    asset = generic["asset"]["asset_id"]
    revision = generic["revision"]["revision_id"]
    service.confirm_revision(asset, revision)
    snapshot = build_snapshot(service, asset, revision)
    assert "原有知识" in snapshot["markdown"]
    assert snapshot["title"] == "历史通用笔记"
    assert "历史笔记未保存逐条考点来源关系" in snapshot["source_markdown"]
    assert "引用摘录" not in snapshot["markdown"]


def test_legacy_appendix_is_available_but_excluded_from_export(system):
    service, _, _ = confirmed(system)
    appendix = "## 出处与来源标记\n\n来源：资料来源\n\n引用摘录：\n\n> 原句"
    original = "# 原有知识\n\n继续保留\n\n---\n\n" + appendix
    generic = service.create_asset(
        "net",
        {
            "asset_type": "note",
            "title": "历史笔记",
            "markdown": original,
            "source_document_ids": [],
        },
    )
    asset, revision = generic["asset"]["asset_id"], generic["revision"]["revision_id"]
    service.confirm_revision(asset, revision)
    detail = service.note_draft(asset, revision)
    assert detail["revision"]["body_markdown"] == "# 原有知识\n\n继续保留"
    assert detail["revision"]["source_appendix"] == appendix
    assert system.store.get("asset_revision", revision)["markdown"] == original
    snapshot = build_snapshot(service, asset, revision)
    assert "继续保留" in export_markdown(snapshot)
    assert "引用摘录" not in export_markdown(snapshot)
    assert snapshot["source_markdown"] == appendix
