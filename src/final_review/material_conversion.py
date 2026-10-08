"""Bounded conversion of uploaded course files into searchable Markdown text."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Callable
from zipfile import BadZipFile, ZipFile

from markitdown import MarkItDown
from PIL import Image, UnidentifiedImageError

from .config import Settings

SUPPORTED_SUFFIXES = {
    ".md",
    ".txt",
    ".pdf",
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}
IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
MAX_IMAGE_PIXELS = 20_000_000
MAX_PRESENTATION_SLIDES = 80
PRESENTATION_TIMEOUT_SECONDS = 600
CONVERSION_TIMEOUT_SECONDS = 45
LEGACY_OFFICE_TIMEOUT_SECONDS = 120


@dataclass
class ConvertedMaterial:
    markdown: str
    sections: list[dict] | None = None
    pages: list[dict] | None = None
    pipeline: str | None = None


def _find_executable(names: tuple[str, ...], windows_relative_path: str) -> str | None:
    setting_name = {
        "pdftoppm": "poppler_executable",
        "libreoffice": "libreoffice_executable",
        "tesseract": "tesseract_executable",
    }.get(names[0])
    configured = getattr(Settings(), setting_name, "") if setting_name else ""
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute() or not candidate.is_file():
            raise ValueError(f"{setting_name.upper()} 配置无效，请设置可执行文件的绝对路径")
        return str(candidate)
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidate = Path(root) / windows_relative_path
                if candidate.is_file():
                    return str(candidate)
    return None


def _tessdata_dir() -> Path | None:
    configured = Settings().tessdata_prefix
    local_app_data = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    candidates = [Path(configured)] if configured else []
    if local_app_data:
        candidates.append(Path(local_app_data) / "FinalReview" / "tessdata")
    for directory in candidates:
        if all(
            (directory / f"{language}.traineddata").is_file() for language in ("chi_sim", "eng")
        ):
            return directory
    return None


def _check_office_archive(content: bytes, max_bytes: int) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > max_bytes * 20:
                raise ValueError("Office 文件解压体积超过限制")
    except BadZipFile as exc:
        raise ValueError("Office 文件损坏或格式与扩展名不符") from exc


def _run(command: list[str], label: str, timeout_seconds: int = CONVERSION_TIMEOUT_SECONDS) -> None:
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{label}超时，请上传较小或更清晰的文件") from exc
    except OSError as exc:
        raise ValueError(f"{label}服务不可用") from exc
    if result.returncode:
        raise ValueError(f"{label}失败，请检查文件是否损坏")


def _convert_legacy(path: Path, directory: Path) -> Path:
    executable = _find_executable(("libreoffice", "soffice"), "LibreOffice/program/soffice.com")
    if not executable:
        raise ValueError("旧版 Office 转换服务不可用")
    target_suffix = ".docx" if path.suffix == ".doc" else ".pptx"
    profile = (directory / "lo-profile").as_uri()
    _run(
        [
            executable,
            f"-env:UserInstallation={profile}",
            "--headless",
            "--convert-to",
            target_suffix[1:],
            "--outdir",
            str(directory),
            str(path),
        ],
        "旧版 Office 转换",
        LEGACY_OFFICE_TIMEOUT_SECONDS,
    )
    converted = directory / f"{path.stem}{target_suffix}"
    if not converted.is_file() or not converted.stat().st_size:
        raise ValueError("旧版 Office 转换未生成可读文件")
    return converted


def _ocr_image(
    path: Path, content: bytes, suffix: str, *, allow_empty: bool = False, sparse_text: bool = False
) -> str:
    try:
        with Image.open(BytesIO(content)) as image:
            if (
                image.format != IMAGE_FORMATS[suffix]
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("图片格式不符或像素超过限制")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("图片损坏或格式与扩展名不符") from exc
    executable = _find_executable(("tesseract",), "Tesseract-OCR/tesseract.exe")
    if not executable:
        raise ValueError("图片 OCR 服务不可用")
    command = [executable]
    tessdata_dir = _tessdata_dir()
    if tessdata_dir:
        command.extend(["--tessdata-dir", str(tessdata_dir)])
    command.extend([str(path), "stdout", "-l", "chi_sim+eng"])
    if sparse_text:
        command.extend(["--psm", "11"])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CONVERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("图片 OCR 超时，请上传较小或更清晰的图片") from exc
    except OSError as exc:
        raise ValueError("图片 OCR 服务不可用") from exc
    if result.returncode:
        raise ValueError("图片 OCR 失败，请检查中文和英文语言包")
    markdown = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", result.stdout)
    if not markdown.strip() and not allow_empty:
        raise ValueError("图片中没有识别到可检索文字")
    return markdown


def _convert_presentation_to_pdf(path: Path, directory: Path) -> Path:
    executable = _find_executable(("libreoffice", "soffice"), "LibreOffice/program/soffice.com")
    if not executable:
        raise ValueError("PPT 转 PDF 服务不可用，请安装 LibreOffice")
    pdf = path.with_suffix(".pdf")
    _run(
        [
            executable,
            f"-env:UserInstallation={(directory / 'pdf-profile').as_uri()}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(directory),
            str(path),
        ],
        "PPT 转 PDF",
        LEGACY_OFFICE_TIMEOUT_SECONDS,
    )
    if not pdf.is_file() or not pdf.stat().st_size:
        raise ValueError("PPT 转 PDF 未生成可读文件")
    return pdf


def _normalise_line(line: str) -> str:
    return "".join(
        char.casefold() for char in unicodedata.normalize("NFKC", line) if char.isalnum()
    )


def _new_lines(existing: str, candidate: str) -> str:
    """Keep new OCR/native lines while suppressing repeated slide text."""
    accepted = [line.strip() for line in existing.splitlines() if line.strip()]
    initial_count = len(accepted)
    known = [_normalise_line(line) for line in accepted]
    for line in candidate.splitlines():
        line = line.strip()
        key = _normalise_line(line)
        if not key:
            continue
        duplicate = any(
            key == old
            or (len(key) >= 4 and key in old)
            or (len(key) >= 6 and len(old) >= 6 and SequenceMatcher(None, key, old).ratio() >= 0.9)
            for old in known
        )
        if not duplicate:
            accepted.append(line)
            known.append(key)
    return "\n".join(accepted[initial_count:])


def _markitdown_slides(markdown: str, count: int) -> list[str]:
    markers = list(re.finditer(r"<!--\s*Slide number:\s*(\d+)\s*-->", markdown))
    if len(markers) != count or [int(match.group(1)) for match in markers] != list(
        range(1, count + 1)
    ):
        return [""] * count
    slides = []
    for i, match in enumerate(markers):
        slide = markdown[match.end() : markers[i + 1].start() if i + 1 < count else len(markdown)]
        slide = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", slide)
        slides.append(slide.strip())
    return slides


def _convert_presentation(
    path: Path,
    directory: Path,
    *,
    stage_callback: Callable[[str], None] | None = None,
    interpreter=None,
    cache_dir: Path | None = None,
    page_filter: set[int] | None = None,
) -> ConvertedMaterial:
    import pdfplumber
    from pptx import Presentation

    from .source_locators import _slide_sections

    started = time.monotonic()
    try:
        native = _slide_sections(Presentation(path))
        markdown = MarkItDown(enable_plugins=False).convert(str(path)).text_content
    except Exception as exc:
        raise ValueError("PPT 文字提取失败，请检查格式和可读性") from exc
    if not native or len(native) > MAX_PRESENTATION_SLIDES:
        raise ValueError(f"PPT 页数为空或超过 {MAX_PRESENTATION_SLIDES} 页限制")
    if stage_callback:
        stage_callback("convert")
    pdf = _convert_presentation_to_pdf(path, directory)
    try:
        with pdfplumber.open(pdf) as document:
            pdf_pages = len(document.pages)
    except Exception as exc:
        raise ValueError("转换后的 PDF 无法读取") from exc
    if pdf_pages != len(native):
        raise ValueError("PPT 与转换后 PDF 页数不一致，无法可靠定位 OCR 内容")
    renderer = _find_executable(("pdftoppm",), "")
    if not renderer:
        raise ValueError("PDF 页面渲染服务不可用，请安装 Poppler")
    if stage_callback:
        stage_callback("ocr")
    markitdown_slides = _markitdown_slides(markdown, len(native))
    if interpreter is not None:
        return _understand_rendered_slides(
            pdf,
            renderer,
            native,
            markitdown_slides,
            interpreter,
            cache_dir or directory,
            stage_callback,
            page_filter,
            source_signature=sha256(path.read_bytes()).hexdigest(),
        )
    sections = []
    additions = []
    for index, section in enumerate(native, 1):
        if page_filter is not None and index not in page_filter:
            continue
        if time.monotonic() - started > PRESENTATION_TIMEOUT_SECONDS:
            raise ValueError("PPT 处理超时，请拆分后上传")
        prefix = directory / f"slide-{index:03d}"
        image = prefix.with_suffix(".png")
        _run(
            [
                renderer,
                "-f",
                str(index),
                "-l",
                str(index),
                "-singlefile",
                "-scale-to",
                "2000",
                "-png",
                str(pdf),
                str(prefix),
            ],
            "PDF 页面渲染",
        )
        if not image.is_file():
            raise ValueError(f"第 {index} 页 PDF 渲染失败")
        try:
            md_extra = _new_lines(section["text"], markitdown_slides[index - 1])
            base = "\n".join(part for part in (section["text"], md_extra) if part.strip())
            ocr = _ocr_image(image, image.read_bytes(), ".png", allow_empty=True, sparse_text=True)
            ocr_extra = _new_lines(base, ocr)
            text = "\n".join(part for part in (base, ocr_extra) if part.strip())
            sections.append({**section, "text": text})
            if ocr_extra:
                additions.append(f"\n\n<!-- Slide number: {index}; OCR -->\n{ocr_extra}")
        finally:
            image.unlink(missing_ok=True)
    if not any(section["text"].strip() for section in sections):
        raise ValueError("PPT 中没有识别到可检索文字")
    return ConvertedMaterial(markdown=(markdown + "".join(additions)).strip(), sections=sections)


def _understand_rendered_slides(
    pdf,
    renderer,
    native,
    extracted,
    interpreter,
    cache_dir,
    stage_callback,
    page_filter,
    *,
    source_signature,
):
    cache_dir.mkdir(parents=True, exist_ok=True)

    def read_page(index):
        prefix = cache_dir / f"slide-{index:03d}"
        image = prefix.with_suffix(".png")
        stamp = prefix.with_suffix(".source")
        if (
            not image.is_file()
            or not stamp.is_file()
            or stamp.read_text(encoding="ascii") != source_signature
        ):
            _run(
                [
                    renderer,
                    "-f",
                    str(index),
                    "-l",
                    str(index),
                    "-singlefile",
                    "-scale-to",
                    "2000",
                    "-png",
                    str(pdf),
                    str(prefix),
                ],
                "PDF 页面渲染",
            )
            stamp.write_text(source_signature, encoding="ascii")
        return interpreter.read(
            image, native[index - 1]["text"], extracted[index - 1], cache_dir / "readings"
        )

    positions = [
        index for index in range(1, len(native) + 1) if page_filter is None or index in page_filter
    ]
    records = {}
    with ThreadPoolExecutor(max_workers=getattr(interpreter, "concurrency", 1)) as pool:
        futures = {pool.submit(read_page, index): index for index in positions}
        for future in as_completed(futures):
            index = futures[future]
            records[index] = future.result()
            if stage_callback:
                stage_callback(f"理解与核验 {len(records)}/{len(positions)} 页")
    sections, pages = [], []
    for index in positions:
        record = records[index]
        pages.append(
            {
                "position": index,
                "title": record["title"],
                "kind": record["kind"],
                "quality": record["quality"],
                "issues": record["issues"],
                "blocks": [
                    {
                        key: block.get(key)
                        for key in (
                            "block_id",
                            "title",
                            "quality",
                            "issues",
                            "evidence",
                            "warnings",
                        )
                    }
                    for block in record["blocks"]
                ],
            }
        )
        if record["quality"] in {"verified", "partial"} and record["kind"] == "knowledge":
            for block in record["blocks"]:
                if block.get("quality", record["quality"]) != "verified":
                    continue
                text = f"{block['title']}\n\n{block.get('text', block.get('markdown', ''))}"
                sections.append({"position_kind": "slide", "position": index, "text": text})
    if not sections:
        raise ValueError("PPT 没有通过质量核验的知识页，请核对原页；未将原始文字入库")
    return ConvertedMaterial(
        markdown="\n\n".join(item["text"] for item in sections),
        sections=sections,
        pages=pages,
        pipeline="visual-slides-v1",
    )


def convert_material(
    content: bytes,
    filename: str,
    max_bytes: int,
    *,
    stage_callback: Callable[[str], None] | None = None,
    interpreter=None,
    cache_dir: Path | None = None,
    page_filter: set[int] | None = None,
) -> ConvertedMaterial:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("不支持此文件格式；支持 md/txt/pdf/ppt/pptx/doc/docx/png/jpg/webp")
    if not content or len(content) > max_bytes:
        raise ValueError("文件为空或超过上传大小限制")
    if suffix in {".md", ".txt"}:
        try:
            return ConvertedMaterial(content.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ValueError("文本文件不是 UTF-8 编码") from exc
    if suffix in {".pptx", ".docx"}:
        _check_office_archive(content, max_bytes)
    with tempfile.TemporaryDirectory(prefix="final-review-") as temporary:
        directory = Path(temporary)
        path = directory / f"material{suffix}"
        path.write_bytes(content)
        if suffix in IMAGE_FORMATS:
            return ConvertedMaterial(_ocr_image(path, content, suffix))
        if suffix in {".ppt", ".doc"}:
            path = _convert_legacy(path, directory)
            _check_office_archive(path.read_bytes(), max_bytes)
        if suffix in {".ppt", ".pptx"}:
            return _convert_presentation(
                path,
                directory,
                stage_callback=stage_callback,
                interpreter=interpreter,
                cache_dir=cache_dir,
                page_filter=page_filter,
            )
        try:
            markdown = MarkItDown(enable_plugins=False).convert(str(path)).text_content
        except Exception as exc:
            raise ValueError("文件文字提取失败，请检查格式和可读性") from exc
        if not markdown.strip():
            raise ValueError("文件没有可检索文字；扫描版 PDF 暂不支持 OCR")
        return ConvertedMaterial(markdown)


def convert_upload(content: bytes, filename: str, max_bytes: int) -> str:
    """Compatibility API for callers that only need Markdown."""
    return convert_material(content, filename, max_bytes).markdown
