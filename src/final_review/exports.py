"""Confirmed note snapshots and deterministic, network-free format rendering."""

import html
import json
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from .domain import DomainConflict, DomainNotFound, DomainService, _id, _now
from .note_content import note_body, split_source_appendix
from .policy import SOURCE_LABELS

RENDERER_VERSION = "note-export-v2"
PROVENANCE = {"source": "资料来源", "synthesis": "综合改编", "ai_supplement": "AI 补充"}
MEDIA_TYPES = {
    "markdown": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "print": "text/html; charset=utf-8",
}
EXTENSIONS = {"markdown": "md", "docx": "docx", "pdf": "pdf", "print": "html"}
PRINT_CSS = """
@page { size:A4; margin:20mm 18mm; }
* { box-sizing:border-box; }
body { font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;
  font-size:11pt; line-height:1.65; color:#17232a; margin:0 auto; max-width:174mm;
  padding:16px; overflow-wrap:anywhere; }
h1 { font-size:23pt; } h2 { font-size:16pt; } h3 { font-size:13pt; }
h1,h2,h3,h4 { break-after:avoid; line-height:1.35; }
p { orphans:3; widows:3; }
table { border-collapse:collapse; width:100%; table-layout:fixed; margin:1em 0; }
th,td { border:1px solid #aab4b9; padding:6px; overflow-wrap:anywhere; }
thead { display:table-header-group; } tr { break-inside:avoid; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; background:#f2f4f5; padding:10px; }
code { font-family:Consolas,monospace; font-size:9pt; }
blockquote { border-left:3px solid #aab4b9; margin:1em 0; padding-left:12px; }
math { max-width:100%; overflow-wrap:anywhere; }
@media print { body { padding:0; max-width:none; } }
"""


def export_revision(service: DomainService, asset_id: str, revision_id: str) -> tuple[dict, dict]:
    asset = service._owned("learning_asset", asset_id)
    revision = service._owned("asset_revision", revision_id)
    if asset.get("asset_type") != "note" or revision.get("asset_id") != asset_id:
        raise DomainNotFound(revision_id)
    if revision.get("course_id") != asset["course_id"]:
        raise DomainNotFound(revision_id)
    service.course(asset["course_id"], writable=True)
    if revision.get("state") not in {"confirmed", "superseded", "archived"} or not revision.get(
        "confirmed_at"
    ):
        raise DomainConflict("请先确认笔记，再导出正式版本")
    return asset, revision


def _plain(value: str) -> str:
    """Keep metadata literal instead of letting filenames inject Markdown blocks."""
    return re.sub(r"([\\`*_{}\[\]<>#+.!|$])", r"\\\1", str(value).replace("\n", " "))


def build_snapshot(service: DomainService, asset_id: str, revision_id: str) -> dict:
    asset, revision = export_revision(service, asset_id, revision_id)
    detail = service.note_draft(asset_id, revision_id)
    references = detail["references"]
    # Copy the persisted revision: detail adds transient availability/locator fields.
    body, legacy_appendix = split_source_appendix(revision["markdown"])
    body = note_body(revision)
    lines = ["## 出处与来源标记", ""]
    deleted = set()
    points = revision.get("points", [])
    for point in points:
        lines += [
            f"### {_plain(point['heading'])}",
            "",
            f"来源：{PROVENANCE[point['provenance']]}",
            "",
        ]
        for ref in point.get("references", []):
            stored = next(
                (
                    row
                    for row in references
                    if row.get("point_id") == point["point_id"]
                    and row.get("locator_id") == ref["chunk_id"]
                    and row.get("quote") == ref["quote"]
                ),
                None,
            )
            if stored is None:
                raise DomainConflict("正式笔记的引用记录不完整，请核对来源")
            version = service._owned("material_version", stored["material_version_id"])
            if version["course_id"] != asset["course_id"]:
                raise DomainNotFound(stored["material_version_id"])
            name = version.get("file_name") or ref.get("file_name") or "资料"
            location = "资料片段"
            position = ref.get("position")
            kind = ref.get("position_kind")
            if position is not None:
                location = (
                    f"第 {position} 页"
                    if kind == "page"
                    else f"第 {position} 张幻灯片"
                    if kind == "slide"
                    else f"片段 {position}"
                )
            label = SOURCE_LABELS.get(version.get("source_type"), "其他资料")
            lines += [
                f"- {_plain(name)} · {label} · {location}",
                "",
                "引用摘录：",
                "",
                *[f"> {_plain(line)}" for line in stored["quote"].splitlines()],
                "",
            ]
            document = service.store.get("document", stored["document_id"])
            if not document or document.get("parse_status") == "deleted":
                deleted.add(name)
    # Old generic notes have revision-level references without points.
    if not points:
        lines += ["历史笔记未保存逐条考点来源关系。", ""]
        for ref in references:
            version = service._owned("material_version", ref["material_version_id"])
            if version["course_id"] != asset["course_id"]:
                raise DomainNotFound(ref["material_version_id"])
            name = version.get("file_name", "资料")
            lines += [
                f"- {_plain(name)} · {SOURCE_LABELS.get(version.get('source_type'), '其他资料')}",
                "",
            ]
            if ref.get("quote"):
                lines += [
                    "引用摘录：",
                    "",
                    *[f"> {_plain(line)}" for line in ref["quote"].splitlines()],
                    "",
                ]
            document = service.store.get("document", ref["document_id"])
            if not document or document.get("parse_status") == "deleted":
                deleted.add(name)
    lines += [
        "## 版本信息",
        "",
        f"笔记标题：{_plain(revision['title'])}",
        "",
        f"已确认版本：v{revision['revision_no']}",
        "",
        f"Revision：{revision_id}",
        "",
        f"确认时间：{revision['confirmed_at']}",
        "",
    ]
    markdown = body.rstrip() + "\n"
    return {
        "title": revision["title"],
        "revision_id": revision_id,
        "revision_no": revision["revision_no"],
        "markdown": markdown,
        "source_markdown": legacy_appendix or "\n".join(lines),
        "content_hash": sha256(markdown.encode("utf-8")).hexdigest(),
        "deleted_sources": sorted(deleted),
        "renderer_version": RENDERER_VERSION,
    }


def create_export(service: DomainService, asset_id: str, revision_id: str, format: str) -> dict:
    if format not in MEDIA_TYPES:
        raise DomainConflict("不支持的导出格式")
    with service._transaction():
        asset = service._locked_asset(asset_id)
        # Serialize with source deletion so the snapshot is never half assembled.
        refs = service.store.scan("source_reference", {"course_id": asset["course_id"]})
        locked_get = getattr(service.store, "get_for_update", service.store.get)
        for document_id in sorted(
            {r["document_id"] for r in refs if r.get("revision_id") == revision_id}
        ):
            locked_get("document", document_id)
        snapshot = build_snapshot(service, asset_id, revision_id)
        # Repeated clicks reuse only jobs with the same immutable input and renderer.
        for existing in service.store.scan("export_job", {"course_id": asset["course_id"]}):
            if (
                existing.get("user_id") == service.user_id
                and existing.get("asset_id") == asset_id
                and existing.get("revision_id") == revision_id
                and existing.get("format") == format
                and existing.get("snapshot") == snapshot
                and existing.get("status") in {"queued", "running", "succeeded"}
            ):
                return existing
        now, export_id = _now(), _id("export")
        job = {
            "export_id": export_id,
            "user_id": service.user_id,
            "course_id": asset["course_id"],
            "asset_id": asset_id,
            "revision_id": revision_id,
            "format": format,
            "snapshot": snapshot,
            "status": "queued",
            "attempts": 0,
            "max_attempts": 3,
            "created_at": now,
            "lease_until": None,
            "error": None,
            "result": None,
        }
        service.store.put("export_job", export_id, job)
        service._audit(asset["course_id"], "note.export_requested", "learning_asset", asset_id)
    return job


def read_export(service: DomainService, export_id: str) -> dict:
    job = service._owned("export_job", export_id)
    asset, _ = export_revision(service, job["asset_id"], job["revision_id"])
    if job["course_id"] != asset["course_id"]:
        raise DomainNotFound(export_id)
    return job


def public_export(job: dict) -> dict:
    snapshot = job["snapshot"]
    return {
        key: job[key]
        for key in ("export_id", "asset_id", "revision_id", "format", "status", "error")
    } | {
        "revision_no": snapshot["revision_no"],
        "content_hash": snapshot["content_hash"],
        "renderer_version": snapshot["renderer_version"],
        "file_hash": (job.get("result") or {}).get("file_hash"),
    }


def export_markdown(snapshot: dict) -> str:
    return snapshot["markdown"]


class ExportRenderError(RuntimeError):
    pass


def _pandoc(settings, content: str, *args: str) -> bytes:
    import pypandoc

    executable = settings.export_pandoc_executable or pypandoc.get_pandoc_path()
    try:
        result = subprocess.run(
            [executable, *args],
            input=content.encode("utf-8"),
            capture_output=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExportRenderError("文档转换失败，请检查 Pandoc 配置后重试") from exc
    return result.stdout


def _safe_ast(snapshot: dict, settings) -> str:
    content = _pandoc(
        settings,
        export_markdown(snapshot),
        "-f",
        "markdown+tex_math_dollars+pipe_tables-raw_html-raw_tex-yaml_metadata_block-implicit_figures",
        "-t",
        "json",
    )
    ast = json.loads(content)

    def clean(node):
        if isinstance(node, list):
            return [clean(child) for child in node]
        if not isinstance(node, dict):
            return node
        # No external fetches, file reads, arbitrary links or embedded HTML.
        if node.get("t") in {"Image", "Link"}:
            return {"t": "Span", "c": [["", [], []], clean(node["c"][1])]}
        if node.get("t") == "RawBlock":
            return {"t": "Para", "c": [{"t": "Str", "c": node["c"][1]}]}
        if node.get("t") == "RawInline":
            return {"t": "Str", "c": node["c"][1]}
        if node.get("t") == "Header":
            node["c"][1] = ["", [], []]
        if node.get("t") in {"Div", "Span", "CodeBlock", "Code", "Table"}:
            node["c"][0] = ["", [], []]
        return {key: clean(value) for key, value in node.items()}

    ast = clean(ast)
    ast["meta"] = {}  # Ignore author-supplied conversion metadata/options.
    return json.dumps(ast, ensure_ascii=False)


def render_html(snapshot: dict, settings, *, ast: str | None = None) -> str:
    body = _pandoc(
        settings, ast or _safe_ast(snapshot, settings), "-f", "json", "-t", "html5", "--mathml"
    ).decode("utf-8")
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(snapshot['title'])}</title><style>{PRINT_CSS}</style>"
        f"</head><body>{body}</body></html>"
    )


def render_file(snapshot: dict, format: str, output: Path, settings) -> None:
    if format == "markdown":
        output.write_text(export_markdown(snapshot), encoding="utf-8")
        return
    ast = _safe_ast(snapshot, settings)
    if format == "docx":
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Mm, Pt, RGBColor

        with TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.docx"
            reference.write_bytes(_pandoc(settings, "", "--print-default-data-file=reference.docx"))
            doc = Document(reference)
            for section in doc.sections:
                section.page_width, section.page_height = Mm(210), Mm(297)
                section.top_margin = section.bottom_margin = Mm(20)
                section.left_margin = section.right_margin = Mm(18)
                footer = section.footer.paragraphs[0]
                footer.alignment = 2
                field = OxmlElement("w:fldSimple")
                field.set(qn("w:instr"), "PAGE")
                footer._p.append(field)
            for name in (
                "Normal",
                "Body Text",
                "First Paragraph",
                "Heading 1",
                "Heading 2",
                "Heading 3",
                "Block Text",
            ):
                style = doc.styles[name]
                style.font.name = "Microsoft YaHei"
                style.font.color.rgb = RGBColor(0, 0, 0)
                style.element.get_or_add_rPr().get_or_add_rFonts().set(
                    qn("w:eastAsia"), "Microsoft YaHei"
                )
            doc.styles["Normal"].font.size = Pt(11)
            doc.save(reference)
            _pandoc(
                settings,
                ast,
                "-f",
                "json",
                "-t",
                "docx",
                f"--reference-doc={reference}",
                "-o",
                str(output),
            )
        return
    document = render_html(snapshot, settings, ast=ast)
    if format == "print":
        output.write_text(document, encoding="utf-8")
        return
    if format != "pdf":
        raise ExportRenderError("不支持的导出格式")
    try:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "print.html"
            source.write_text(document, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "final_review.export_pdf",
                    str(source),
                    str(output.resolve()),
                    settings.export_chromium_executable,
                ],
                capture_output=True,
                timeout=120,
                check=True,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExportRenderError("PDF 转换失败，请检查 Chromium 安装后重试") from exc


def download_name(job: dict) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", job["snapshot"]["title"]).strip(" .")[:80]
    return f"{title or '笔记'}-v{job['snapshot']['revision_no']}.{EXTENSIONS[job['format']]}"
