# 期末速通 (Final Review)

大学生期末复习助手 Skill。上传复习资料后，先用 `markitdown` 统一解析成 Markdown，再基于这些 Markdown 生成题库、知识库、知识点辅导内容和刷题流程。

## 核心理念

**学生自带资料，AI 只基于资料出题和讲解。**

- 不联网找外部题库
- 先把所有资料转成 Markdown，统一后处理链路
- 先问考试蓝图和偏好，再生成更贴考试的题和知识库

## 功能一览

| 功能 | 说明 |
|------|------|
| 资料解析 | 优先用 `markitdown` 将 PPT、PDF、Word、图片、txt、md 统一转成 Markdown |
| Markdown 沉淀 | 每个原文件都会落到 `storage/<科目>/markdown格式/` |
| AI 出题 | 支持单选、多选、判断、填空、简答、程序题 |
| 考试蓝图 | 支持记录题型分值结构、老师提醒、程序题重点 |
| 学习偏好 | 支持记录偏好题型、讲解详细度、辅导模式、HTML 提醒 |
| 知识点辅导模式 | 针对某个知识点输出“定义 -> 怎么考 -> 各题型怎么用 -> 按密度出题” |
| 刷题模式 | 对话式一问一答，支持全部/错题/薄弱点/模拟考试 |
| 错题本 | 自动记录答错题，支持错题重刷 |
| 薄弱点专攻 | 按知识点统计，自动聚焦错误较多的知识点 |
| Markdown 知识库 | 按章节输出概念、考法、易混点、程序题关联等 |

## Skill 架构

```text
上传资料
  -> markitdown 解析为 Markdown
  -> 保存到 storage/<科目>/markdown格式/
  -> 合并为 parsed_content.md
  -> AI 生成 quiz.json
  -> AI 生成 knowledge_base/index.md
  -> ReviewSession 刷题 / 错题 / 薄弱点
  -> tutor.py 为知识点辅导模式构建上下文
```

模块分工：

- `scripts/parse_file.py`
  负责把原始资料统一转成 Markdown
- `scripts/bootstrap_subject.py`
  负责初始化考试蓝图和学习偏好
- `scripts/review.py`
  负责刷题、错题、薄弱点、模拟考试
- `scripts/tutor.py`
  负责为“讲知识点”模式构建上下文
- `scripts/utils.py`
  负责存储题库、进度、知识库、考试蓝图、偏好和 Markdown 副本

## 使用流程

### 1. 初始化科目

在第一次使用某个科目时，先向用户询问：

- 考试提醒：老师强调点、考试结构、时间、注意事项
- 考试蓝图：单选、多选、判断、填空、简答、程序题数量和权重
- 偏好：偏好题型、讲解详细度、辅导模式、是否提醒导出 HTML

这些信息会保存到：

- `storage/<科目>/exam_profile.json`
- `storage/<科目>/preferences.json`

### 2. 解析资料为 Markdown

```bash
python scripts/parse_file.py --subject 大数据采集与预处理 ./资料1.pdf ./资料2.docx ./资料3.png
```

解析结果：

- 每个文件都会变成一个 `.md`
- 保存到 `storage/<科目>/markdown格式/`
- 合并后的总文本保存到 `storage/<科目>/parsed_content.md`

### 3. 生成题库

AI 基于 `parsed_content.md` 和考试蓝图生成 `quiz.json`。推荐题目字段：

```json
{
  "id": 1,
  "type": "multiple_choice",
  "topic": "ETL 与数据同步",
  "question": "下列属于 ETL 阶段的有？",
  "options": ["A. Extract", "B. Transform", "C. Load", "D. Cluster"],
  "answer": "A,B,C",
  "explanation": "ETL 包括抽取、转换、加载。",
  "difficulty": "easy",
  "exam_types": ["单选", "多选", "简答"],
  "knowledge_density": "medium",
  "programming_related": false,
  "linux_related": false
}
```

题型支持：

- `single_choice`
- `multiple_choice`
- `true_false`
- `fill_blank`
- `short_answer`
- `programming`

### 4. 生成知识库

知识库文件：

- `storage/<科目>/knowledge_base/index.md`

建议格式：

- 定义
- 核心要点
- 高频考法
- 各题型运用提醒
- 易混点
- 程序题关联
- 建议题量

### 5. 刷题

```bash
python scripts/review.py 大数据采集与预处理 all
python scripts/review.py 大数据采集与预处理 wrong
python scripts/review.py 大数据采集与预处理 weak
python scripts/review.py 大数据采集与预处理 exam
```

模式说明：

- `all`：全部题目
- `wrong`：错题重刷
- `weak`：薄弱点专攻
- `exam`：按考试蓝图抽题，生成一套模拟卷

### 6. 知识点辅导模式

```bash
python scripts/tutor.py 大数据采集与预处理 网络爬虫
```

这个脚本会输出该知识点的辅导上下文，包括：

- 考试蓝图
- 学习偏好
- 知识点密度判断
- 知识库片段
- 推荐题量

Skill 在对话中使用它时，应该按这个结构回答：

1. 定义
2. 这个知识点可能怎么考
3. 单选、多选、判断、简答、程序题分别怎么用
4. 按知识点密度出 `1-6` 题
5. 直接给答案和解析

## 复杂知识点的 HTML 建议

对于下列情况，建议提示用户让 Agent 额外产出 HTML 版本：

- 流程很长：如 ETL、Kafka 生产消费流程、Flume 采集链路
- 分类很多：如数据清洗、数据变换、网络爬虫分类
- 图示明显更好理解：如思维导图、架构图、对比表
- 程序题需要同时展示配置、命令、步骤和结果验证

推荐提示语：

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
    │   ├── 资料1.md
    │   ├── 资料2.md
    │   └── ...
    └── knowledge_base/
        └── index.md
```

## 脚本说明

| 脚本 | 功能 |
|------|------|
| `parse_file.py --subject <科目> <文件...>` | 用 `markitdown` 优先将原始资料统一转成 Markdown |
| `bootstrap_subject.py <科目>` | 初始化考试蓝图和偏好 |
| `review.py <科目> [mode]` | 刷题。`mode`: `all / wrong / weak / exam` |
| `tutor.py <科目> <知识点>` | 输出知识点辅导上下文 |
| `utils.py` | 存储和查询工具函数 |

## 常用对话指令

- `@期末复习 我要复习 <科目名>`
- `@期末复习 上传资料`
- `@期末复习 配置考试蓝图 <科目名>`
- `@期末复习 配置偏好 <科目名>`
- `@期末复习 讲知识点 <科目名> <知识点>`
- `@期末复习 开始刷题 <科目名>`
- `@期末复习 查看错题本 <科目名>`
- `@期末复习 薄弱点加练 <科目名>`
- `@期末复习 导出知识库 <科目名>`

## 安装依赖

```bash
pip install markitdown[all] python-pptx pdfplumber PyPDF2 python-docx Pillow pytesseract
```

如果要做图片 OCR，还需要安装 Tesseract-OCR 引擎。

## License

MIT
