# 期末速通 (Final Review)

大学生期末复习助手 Skill。上传复习资料 → AI 生成题库 → 对话式刷题 → 错题专攻 → Markdown 知识库沉淀。

## 核心理念

**学生自带资料，AI 只管出题和讲解。**

不联网搜索，不依赖外部题库。你上传什么，它就基于什么出题。PPT、PDF、Word、图片全能解析。

## 功能一览

| 功能 | 说明 |
|------|------|
| 📄 资料解析 | 支持 PPTX、PDF、Word、图片 OCR、纯文本/Markdown |
| 📝 AI 出题 | 单选、判断、填空、简答四种题型 |
| 📚 知识库 | 自动生成 Markdown 知识点总结，按章节组织 |
| ❓ 刷题模式 | 对话式一问一答，即时判分 + 详细解析 |
| ❌ 错题本 | 自动记录答错题，支持错题重刷 |
| 🎯 薄弱点专攻 | 按知识点统计正确率，低于 60% 自动标红加练 |

## 快速开始

在 WorkBuddy 中对话即可使用：

```
@期末复习 我要复习数据结构
→ 上传你的 PPT/PDF/Word 资料
→ AI 自动生成题库和知识库
→ 开始刷题
```

## 对话指令

| 指令 | 效果 |
|------|------|
| `@期末复习 我要复习<科目名>` | 开始新复习流程 |
| `@期末复习 开始刷题 <科目名>` | 进入刷题模式 |
| `@期末复习 查看错题本 <科目名>` | 查看历史错题 |
| `@期末复习 薄弱点加练 <科目名>` | 针对薄弱知识点出题 |
| `@期末复习 导出知识库 <科目名>` | 获取 Markdown 知识点文件路径 |
| `@期末复习 列出科目` | 查看所有已创建的科目 |

## 安装依赖

```bash
pip install python-pptx pdfplumber PyPDF2 python-docx Pillow
```

> 图片 OCR 额外需要 [Tesseract-OCR](https://github.com/UB-Mannheim/tesseract/wiki) 引擎（可选）。

## 题库 JSON 格式

```json
[
  {
    "id": 1,
    "type": "single_choice",
    "topic": "数据结构-绪论",
    "question": "以下哪个时间复杂度表示常数时间？",
    "options": ["A. O(n)", "B. O(log n)", "C. O(1)", "D. O(n²)"],
    "answer": "C",
    "explanation": "O(1) 表示算法执行时间不随输入规模变化...",
    "difficulty": "easy"
  }
]
```

## 存储结构

```
storage/<科目名>/
  ├── parsed_content.md      # 解析后的原文
  ├── quiz.json              # 题库
  ├── wrong_answers.json     # 错题本
  ├── progress.json          # 刷题进度
  └── knowledge_base/
      └── index.md           # Markdown 知识点总结
```

## 技术栈

- Python 3.13+
- 文件解析：python-pptx / pdfplumber / python-docx / Pillow
- 刷题引擎：自研 `ReviewSession` 类
- AI 生成：WorkBuddy 对话中调用大模型

## License

MIT
