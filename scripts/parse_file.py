#!/usr/bin/env python3
"""
文件解析器 - 支持 PPT、PDF、Word、图片 OCR、纯文本
"""
import sys
import io
from pathlib import Path


def _fix_encoding():
    """修复 Windows 终端 UTF-8 编码"""
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        except Exception:
            pass


def parse_pptx(file_path):
    """解析 PPTX 文件，提取所有幻灯片文本"""
    try:
        from pptx import Presentation
    except ImportError:
        return None, "缺少 python-pptx。请运行: pip install python-pptx"

    prs = Presentation(file_path)
    slides_text = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                texts.append(shape.text.strip())
        if texts:
            slides_text.append(f"=== 第{i}页 ===\n" + "\n".join(texts))
    return "\n\n".join(slides_text), None


def parse_pdf(file_path):
    """解析 PDF 文件，提取文本"""
    # 优先尝试 pdfplumber
    try:
        import pdfplumber
        texts = []
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                if text and text.strip():
                    texts.append(f"=== 第{i}页 ===\n{text.strip()}")
        return "\n\n".join(texts), None
    except ImportError:
        pass

    # 回退到 PyPDF2
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        texts = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if text and text.strip():
                texts.append(f"=== 第{i}页 ===\n{text.strip()}")
        return "\n\n".join(texts), None
    except ImportError:
        return None, "缺少 pdfplumber 或 PyPDF2。请运行: pip install pdfplumber 或 pip install PyPDF2"


def parse_word(file_path):
    """解析 Word 文档"""
    try:
        from docx import Document
    except ImportError:
        return None, "缺少 python-docx。请运行: pip install python-docx"

    doc = Document(file_path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs), None


def parse_image(file_path):
    """OCR 识别图片文字"""
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return None, "缺少 pytesseract 或 Pillow。请运行: pip install pytesseract Pillow (并安装 Tesseract-OCR 引擎)"

    image = Image.open(file_path)
    # 尝试中英文混合识别
    try:
        text = pytesseract.image_to_string(image, lang="chi_sim+eng")
    except Exception:
        try:
            text = pytesseract.image_to_string(image, lang="chi_sim")
        except Exception:
            text = pytesseract.image_to_string(image)
    return text.strip(), None


def parse_text(file_path):
    """解析纯文本/Markdown 文件"""
    path = Path(file_path)
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="gbk")
    return content, None


def parse_file(file_path):
    """
    根据文件类型自动选择解析器
    返回: (content, error)
    """
    path = Path(file_path)
    if not path.exists():
        return None, f"文件不存在: {file_path}"

    suffix = path.suffix.lower()
    parsers = {
        ".pptx": parse_pptx,
        ".pdf": parse_pdf,
        ".doc": parse_word,
        ".docx": parse_word,
        ".png": parse_image,
        ".jpg": parse_image,
        ".jpeg": parse_image,
        ".bmp": parse_image,
        ".gif": parse_image,
        ".webp": parse_image,
        ".txt": parse_text,
        ".md": parse_text,
    }

    parser = parsers.get(suffix)
    if not parser:
        return None, f"不支持的文件格式: {suffix}。支持的格式: {', '.join(parsers.keys())}"

    return parser(file_path)


def parse_multiple(files):
    """
    解析多个文件，合并内容
    files: 文件路径列表
    返回: (merged_content, errors)
    """
    contents = []
    errors = []
    for f in files:
        content, err = parse_file(f)
        if err:
            errors.append(f"{f}: {err}")
        elif content:
            contents.append(f"===== 文件: {Path(f).name} =====\n{content}")
    return "\n\n".join(contents), errors


def main():
    _fix_encoding()
    if len(sys.argv) < 2:
        print("用法: python parse_file.py <文件路径1> [文件路径2] ...")
        print("支持格式: .pptx .pdf .doc .docx .png .jpg .jpeg .txt .md")
        sys.exit(1)

    files = sys.argv[1:]
    content, errors = parse_multiple(files)

    if errors:
        for e in errors:
            print(f"[错误] {e}", file=sys.stderr)

    if content:
        print(content)
    else:
        print("未能提取到任何内容", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
