from final_review.note_content import split_source_appendix


def test_generated_legacy_appendix_is_separated_without_losing_body():
    body = "# Servlet\n\n正文中有学习价值的引用：原句。"
    appendix = "## 出处与来源标记\n\n### 定义\n\n来源：资料来源\n\n引用摘录：\n\n> 原句"
    assert split_source_appendix(body + "\n\n---\n\n" + appendix) == (body, appendix)


def test_similar_heading_and_code_example_are_preserved():
    for markdown in (
        "# 内容\n\n## 出处与来源标记\n\n这是学习正文，没有自动来源字段。",
        "# 示例\n\n```markdown\n## 出处与来源标记\n来源：资料来源\n```\n\n继续学习",
    ):
        assert split_source_appendix(markdown) == (markdown, "")
