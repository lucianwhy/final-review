# 笔记导出（M2-04）

“我的笔记”详情支持 Markdown、Word、PDF 和打印预览。只有曾确认且仍属于当前
用户的版本可导出；当前正式版本和 superseded 历史正式版本都支持。草稿包括历史
草稿返回 409，跨用户、错误资产与版本配对返回 404，已删除/清除课程拒绝导出和下载。
笔记有新草稿时，打开正式版本再导出；未保存编辑不进入导出内容。

## 数据流

客户端提交 asset_id、revision_id 与 format，后端在短事务中锁定资产及来源资料，
读取不可变 revision、SourceReference 和 MaterialVersion，生成完整 Markdown 快照。
正文由已保存的标题和考点内容组成；来源标记、原文件名、位置与引用摘录独立保留，
阅读页面通过底部“查看来源与引用”弹窗查看，所有下载格式及打印预览只含正文。
不会重新调用模型。没有 points 的旧通用笔记保留原正文和版本级来源，
明确说明没有逐条来源关系，不推断不存在的引用。

Markdown 直接保存；Pandoc 将同一内容转换为 Word 或 HTML，Chromium 将该 HTML
打印为 PDF。打印预览使用相同 HTML/CSS。常见公式通过 Word OMML / HTML MathML
渲染。原始 HTML、OpenXML 和脚本只作为文字；Word/PDF/打印中的图片保留替代文字，
链接保留显示文字，不读取外部 URL 或本地文件。Markdown 下载保留原 Markdown 语法。
第一版不支持嵌入图片和任意 LaTeX 宏；不承诺三种格式分页完全一致。

内容 SHA-256 只对导出正文计算，版本标识与 hash 保留在任务及响应头中，不打印。
文件自身 SHA-256 单独记录；DOCX/PDF 的二进制无需相同。原资料已删除的状态保留在
来源信息中，不进入导出正文；原文件名和摘录仍来自历史版本。
渲染版本升级为 note-export-v2，客户端不再复用旧渲染版本的导出记录。
历史正文中可明确识别的自动来源附录仅在读取时分离，不改写已保存版本。
请求后再确认新版、改名或删除资料不会改变已有任务快照。

每种格式创建持久 export_job。相同用户/资产/revision/格式/快照/渲染版本的排队、
运行或成功任务复用；失效的缓存文件转为可重试失败。worker 通过 SKIP LOCKED 领取任务，
十分钟租约过期后可重新领取，三次中断后失败。显式重试复用原快照、追加三次尝试额度。
每次 Pandoc 调用最多 60 秒，PDF 子进程最多 120 秒；一次转换预算小于租约。

worker 先写临时文件，完成后再次检查课程、版本和当前任务尝试次数，在短事务内
原子发布。每次尝试使用不同文件名，旧 worker 无权替换新结果；失败不产生可下载半文件。
文件与数据库不能构成同一个事务，进程硬中断可能留下私有孤立文件；它们没有下载入口。

## 接口

| 接口 | 行为 |
| --- | --- |
| POST /api/notes/{asset_id}/exports | 提交 revision_id 和 markdown/docx/pdf/print，返回 202 |
| GET /api/exports/{export_id} | 查询状态、版本、内容 hash 和完成后的文件 hash |
| POST /api/exports/{export_id}/retry | 对失败任务重新排队，重复请求不重复排队 |
| GET /api/exports/{export_id}/download | 完成后授权下载，未完成返回 409 |
| GET /api/exports/{export_id}/preview | 完成的 print 任务返回受 CSP 限制的 HTML |

每个入口重新检查 owner、笔记及 revision 关系、课程状态。下载不公开磁盘路径，
不提供静态 URL；响应禁止缓存，并带 X-Revision-Id、X-Content-Hash 和 X-File-Hash。
前端保存当前版本的任务 ID，刷新可继续查看；打印预览通过同源隔离 iframe 展示。
旧 /agent/export 仍只导出 Agent 会话 Markdown，不是正式笔记导出入口。

## 启动和验证

从项目根目录执行：

```powershell
uv sync --frozen
uv run playwright install chromium
uv run python -m final_review.migrations
uv run python -m final_review.export_jobs
```

另行重启后端、更新前端构建。API 和 worker 的 EXPORTS_DIR 必须解析为同一私有目录，
因此从相同项目根目录启动，或配置绝对路径。Linux 需中文字体及 Chromium 系统依赖；
Dockerfile 已包含安装步骤，Compose 已增加共享导出目录的 export-worker。
导出无需模型或 Embedding Key。

```powershell
uv run pytest -q tests/test_exports.py
uv run python scripts/check_note_exports_postgres.py
# frontend 目录
npm run test:e2e
npm run build
```

PostgreSQL 脚本使用配置连接创建随机 final_review_m204_* 数据库，仅在该测试库应用
迁移与重建 schema，结束后删除；不会迁移课程数据库。直接运行 integration 测试时，
TEST_DATABASE_URL 必须指向可销毁测试库，不能指向用户课程库。

私有文件目录需备份和按部署策略清理。课程软删除后文件暂留，但所有入口已拒绝访问；
本卡不实现定时物理回收。验收证据见 [M2-04 验收记录](m2-04-acceptance-2026-10-04.md)。
