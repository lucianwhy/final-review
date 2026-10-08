"""Real-provider positive/negative block review against the M1-09 fixture image."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageFilter

from final_review.config import Settings
from final_review.slide_understanding import (
    REVIEW_PROMPT,
    SlideInterpreter,
    SlideReading,
    SlideReview,
    reviewed_record,
    source_evidence,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    directory = args.evidence_dir.resolve()
    image = directory / "controlled-slides.analysis/slide-003.png"
    sources = source_evidence(
        'request.getParameter("name") 读取参数。',
        "### Notes:\n教师备注：参数名 name 区分大小写。",
        "GET 用于读取资源，POST 用于提交数据。",
    )
    blocks = [
        {
            "title": "图片方法语义",
            "text": "GET 用于读取资源，POST 用于提交数据。",
            "evidence": [
                {"source_id": "image-page", "quote": "GET 用于读取资源，POST 用于提交数据。"}
            ],
        },
        {
            "title": "原生代码",
            "text": 'request.getParameter("name") 读取参数。',
            "evidence": [{"source_id": "native-1", "quote": 'request.getParameter("name")'}],
        },
        {
            "title": "教学备注",
            "text": "教师备注：参数名 name 区分大小写。",
            "evidence": [{"source_id": "note-1", "quote": "参数名 name 区分大小写。"}],
        },
        {
            "title": "伪造事实",
            "text": "GET 方法会自动加密所有网络请求。",
            "evidence": [{"source_id": "image-page", "quote": "GET 用于读取资源"}],
        },
        {
            "title": "被改坏的代码",
            "text": 'request.getParameter("password") 读取参数。',
            "evidence": [{"source_id": "native-1", "quote": 'request.getParameter("name")'}],
        },
    ]
    reader = SlideInterpreter(Settings())
    report = {"model": reader.model, "checks": [], "cases": []}

    def evaluate(candidate, evidence, image_bytes):
        reading = SlideReading.model_validate(
            {"kind": "knowledge", "title": "核验反例", "blocks": candidate, "uncertainties": []}
        )
        review = SlideReview.model_validate(
            reader._ask(
                REVIEW_PROMPT,
                image_bytes,
                json.dumps(
                    {
                        "sources": evidence,
                        "blocks": [
                            {**block, "block_id": f"b{index}"}
                            for index, block in enumerate(candidate, 1)
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        )
        return reviewed_record(reading, review, evidence)

    try:
        result = evaluate(blocks, sources, image.read_bytes())
        report["cases"].append(result)
        qualities = [block["quality"] for block in result["blocks"]]
        assert qualities == ["verified"] * 3 + ["review_needed"] * 2, qualities
        report["checks"].append("image/native/note pass; fabricated fact and changed code rejected")
        blurred = directory / "unreadable-slide.png"
        with Image.open(image) as original:
            original.resize((12, 8)).resize(original.size).filter(
                ImageFilter.GaussianBlur(45)
            ).save(blurred)
        result = evaluate([blocks[0]], source_evidence("", "", ""), blurred.read_bytes())
        report["cases"].append(result)
        assert result["blocks"][0]["quality"] == "review_needed"
        report["checks"].append("unreadable original cannot certify a supplied candidate")
        report["status"] = "passed"
    except Exception:
        report["status"] = "failed"
        raise
    finally:
        reader.close()
        path = directory / "live-block-counterexamples.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Evidence:", path)
    print("PASS", report["checks"])


if __name__ == "__main__":
    main()
