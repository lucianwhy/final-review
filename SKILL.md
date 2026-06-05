---
name: final-review
description: |
  期末复习助手。先向学生询问考试提醒、考试蓝图和学习偏好，再把上传资料用 markitdown 统一解析成 Markdown，
  保存到 markdown格式 目录，随后基于这些 Markdown 生成题库、知识库、知识点辅导内容和刷题流程。
  支持单选、多选、判断、填空、简答、程序题，支持知识点辅导模式、错题本、薄弱点专攻、模拟考试。
  触发词：期末复习、考前突击、复习助手、刷题、生成题库、知识点总结、知识点辅导、错题本、加练、考试蓝图
---

# 期末复习助手

帮助大学生基于自己的复习资料完成一套可持续的复习流程。

## 工作原则

- 不联网找外部题库
- 只基于用户上传资料
- 所有资料先统一转成 Markdown
- 先问考试蓝图和偏好，再开始出题和讲解
- 复杂知识点要提醒用户可额外导出 HTML 版辅助理解

## 使用流程

### Phase 0: 初始化提问

开始复习前，必须先向用户确认 3 类信息：

1. 考试提醒
   - 老师强调内容
   - 题型结构
   - 分值分布
   - 程序题重点

2. 考试蓝图配置
   - 单选题数量
   - 多选题数量
   - 判断题数量
   - 填空题数量
   - 简答题数量
   - 程序题数量

3. 学习偏好
   - 偏好题型
   - 讲解详细度
   - 辅导模式（先讲后练 / 先题后讲）
   - 是否提醒导出 HTML

这些信息要保存到：

- `storage/<科目>/exam_profile.json`
- `storage/<科目>/preferences.json`

也可以通过脚本初始化：

```bash
python scripts/bootstrap_subject.py <科目名>
```

### Phase 1: 资料上传

支持格式：

- `.pptx`
- `.pdf`
- `.doc/.docx`
- `.png/.jpg/.jpeg/.bmp/.gif/.webp`
- `.txt/.md`

### Phase 2: 统一解析成 Markdown

调用：

```bash
python scripts/parse_file.py --subject <科目名> <文件1> <文件2> ...
```

处理规则：

- 优先使用 `markitdown`
- 若失败，再使用格式专用解析器兜底
- 每个文件单独保存到：
  - `storage/<科目>/markdown格式/<原文件名>.md`
- 合并后的总文本保存到：
  - `storage/<科目>/parsed_content.md`

## 题库生成要求

AI 基于 Markdown 内容生成 `quiz.json`，题型包括：

- `single_choice`
- `multiple_choice`
- `true_false`
- `fill_blank`
- `short_answer`
- `programming`

每道题建议包含字段：

- `id`
- `type`
- `topic`
- `question`
- `options`
- `answer`
- `explanation`
- `difficulty`
- `exam_types`
- `knowledge_density`
- `programming_related`
- `linux_related`

## 知识库生成要求

知识库保存到：

- `storage/<科目>/knowledge_base/index.md`

每个知识点建议包含：

- 定义
- 核心要点
- 高频考法
- 各题型运用提醒
- 易混点
- 程序题关联
- 建议题量

## 知识点辅导模式

当用户提问某个知识点时，使用知识点辅导模式，而不是只给定义。

可调用：

```bash
python scripts/tutor.py <科目名> <知识点>
```

辅导输出协议必须尽量按这个结构：

1. 先讲知识点定义
2. 再讲这个知识点可能怎么考
3. 再讲单选、多选、判断、简答、程序题分别怎么用
4. 再按知识点密度出 `1-6` 题
5. 每道题必须直接给答案和解析

题量规则：

- 知识点稀疏：`1-2` 题
- 知识点中等：`2-4` 题
- 知识点密集：`4-6` 题

如果知识点涉及实验和工具，额外补：

- 常见配置项
- 常见命令
- 常见流程
- 常见易错点

## 刷题模式

调用：

```bash
python scripts/review.py <科目名> [mode]
```

模式说明：

- `all`：全部题目
- `wrong`：错题重刷
- `weak`：薄弱点专攻
- `exam`：按考试蓝图抽题，模拟真实考试结构

其中：

- 多选题支持 `A,B,C` 形式作答
- 简答题和程序题允许人工判定正误

## HTML 提醒规则

当满足下面任一情况时，建议提醒用户额外导出 HTML：

- 流程较长
- 分类较多
- 图示价值明显
- 程序题涉及配置、命令、步骤、验证的组合展示

推荐提醒语：

```text
这个知识点结构较复杂，建议额外生成一个 HTML 版本，方便用导航目录、表格和图示复习。
```

## 存储结构

```text
storage/
└── <科目名>/
    ├── parsed_content.md
    ├── quiz.json
    ├── wrong_answers.json
    ├── progress.json
    ├── exam_profile.json
    ├── preferences.json
    ├── markdown格式/
    │   ├── 文件1.md
    │   ├── 文件2.md
    │   └── ...
    └── knowledge_base/
        └── index.md
```

## 脚本说明

| 脚本 | 功能 |
|------|------|
| `parse_file.py --subject <科目> <文件...>` | 统一将资料转成 Markdown |
| `bootstrap_subject.py <科目>` | 初始化考试蓝图和偏好 |
| `review.py <科目> [mode]` | 刷题、错题、薄弱点、模拟考试 |
| `tutor.py <科目> <知识点>` | 构建知识点辅导上下文 |
| `utils.py` | 存储与查询辅助 |

## 常用对话指令

- `@期末复习 我要复习 <科目名>`
- `@期末复习 配置考试蓝图 <科目名>`
- `@期末复习 配置偏好 <科目名>`
- `@期末复习 上传资料`
- `@期末复习 讲知识点 <科目名> <知识点>`
- `@期末复习 开始刷题 <科目名>`
- `@期末复习 查看错题本 <科目名>`
- `@期末复习 薄弱点加练 <科目名>`
- `@期末复习 导出知识库 <科目名>`

## 依赖

```bash
pip install markitdown[all] python-pptx pdfplumber PyPDF2 python-docx Pillow pytesseract
```
