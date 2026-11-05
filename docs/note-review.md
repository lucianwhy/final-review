# 笔记编辑与确认（M2-03）

“我的笔记”按当前课程读取服务端学习资产，草稿生成后即保存，可离开聊天后找回。
旧 `#note/{asset_id}/{revision_id}` 卡片进入独立详情页；详情所属课程由授权响应确定。

## 数据流与状态

```text
所选资料 → Agent 生成与来源校验 → learning_asset + draft revision + source_reference
                                      ↓
                                我的笔记 / 聊天卡片
                                      ↓
                         按考点编辑 Markdown、标题与引用
                                      ↓
                           保存新 draft revision 与引用
                                      ↓
                         差异预览 → 确认令牌 → 正式版本
```

`current_revision_id` 只指向已确认版本；`latest_revision_id` 保存最新版本入口。
列表兼容没有最新版本指针的旧资产，从版本号取最新版本。
已确认笔记的新修改先产生草稿，正式版本保持可用；再次确认后旧正式版本变为
`superseded`，标题、正文、考点及来源内容仍保持不变。历史草稿也保留供查看。

正文按考点编辑：`points` 是正文与逐条来源关系的编辑来源，后端由它生成整份
`markdown`。保存保留笔记类型与生成时的阅读覆盖情况；覆盖情况描述生成时的
资料读取，不代表用户编辑后内容经过模型重新校验。学生修改记录 `edit_source=student`。
资产的“AI 生成”标签与确认状态分别展示。

“确认归档”表示成为正式学习资产（`confirmed`）；现有资产的 `archived` 状态是另一
生命周期动作。M2-03 不提供导出、不把笔记自动加入原始资料检索库；后续 M2-04 导出见
[笔记导出](note-exports.md)。

## HTTP 接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/courses/{course_id}/notes` | 当前课程笔记列表，含状态、类型、生成方式、最新版本和来源摘要 |
| `GET /api/assets/{asset_id}/revisions/{revision_id}` | 草稿、正式或历史版本详情、来源可用性/快照、版本历史 |
| `POST /api/notes/{asset_id}/revisions` | 提交基准版本、标题、考点及逐条引用，生成新草稿 |
| `POST /api/notes/{asset_id}/confirm-preview` | 提交待确认版本，返回前后内容、变更数量及十分钟确认令牌 |
| `POST /api/notes/{asset_id}/confirm` | 提交版本与令牌，确认成为正式版本 |

引用编辑使用已有课程资料列表与 `chunks?include_content=true`。客户端只提交资料 ID、
片段 ID 与摘录，文件名、来源类型和位置由后端核实。资料来源要求一份资料；综合改编
要求多份资料；AI 补充不附资料引用。未经核实的内容不能用伪造的片段或摘录保存。
这些校验验证出处存在，不证明学生改写后的正文在语义上一定受到出处支持。

保存/确认先锁定资产，再按稳定顺序锁定来源资料。基准不是最新版本时返回 `409`。
确认令牌绑定版本内容、当前正式版本和最新版本；内容/版本改变、过期、重放均拒绝。
令牌消费、来源重验、正式指针更新、旧版本替代和审计在同一事务中执行。
通用资产编辑接口拒绝含考点结构的笔记，通用确认接口要求先经笔记确认流程，避免绕过
逐条来源保存与确认预览。所有入口按用户和课程授权，已删除课程不能继续读写笔记。

未变化的历史引用可沿用重建前资料版本与历史片段。正式版本引用的资料保留快照后删除，
详情展示删除状态、原引用摘录和来源快照，不提供失效的原文跳转。

## 兼容与升级

迁移 `007_note_review.sql` 回填笔记最新版本指针与生成方式，并增加资产版本号唯一索引。
不修改历史版本内容。部署时照常执行 `uv run python -m final_review.migrations`，
重启后端与笔记 worker，并构建前端。M2-03 验证使用随机隔离数据库，不修改用户课程。

M2-02 生成的笔记已有考点结构，可以编辑。缺少考点结构的旧通用资产只支持查看；
不自动把其任意 Markdown 拆成考点或推断来源。

## 验证

- 后端行为：`uv run pytest -q tests/test_m2_note_review.py tests/test_m2_note_draft.py tests/test_m0.py`。
- PostgreSQL：在可销毁的独立数据库设置 `TEST_DATABASE_URL`，执行
  `uv run pytest -q tests/test_postgres_migrations.py tests/test_note_review_postgres.py`。
  该套测试会重建测试库的 public schema，不能指向课程数据库。
- 浏览器：在 frontend 执行 `npm run test:e2e`；笔记专项是 `e2e/notes.spec.ts`。
- 构建与静态检查：frontend 的 `npm run build`、根目录的 `uv run ruff check src tests`。

验收证据见 [2026-10-04 验收记录](m2-03-acceptance-2026-10-04.md)。
