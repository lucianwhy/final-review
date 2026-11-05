# M1-03 手动验收

先在仓库根目录运行迁移，再分别启动 API、worker 和前端：

```powershell
uv run python -m final_review.migrations
uv run uvicorn final_review.api:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
# 新终端
uv run python -m final_review.material_jobs
# 新终端，在 frontend 目录
npm run dev
```

浏览器打开“我的资料”，并在开发者工具 Network 面板观察 `/knowledge/upload` 和
`/api/courses/{course_id}/material-jobs`。

1. 上传一份 Markdown。上传请求应返回 `202`、`job_id`、`document_id` 和状态地址；
   页面随后从“排队中”变为“可检索”。刷新后状态仍在，课程问答能检索该资料。
2. 停止 worker，再上传另一份文件。上传请求仍应迅速返回 `202`；刷新页面后仍是
   “排队中”。重启 worker 后，它应自动处理到“可检索”。
3. 上传损坏的 PNG。Job 应变为 `failed`，显示失败阶段和图片损坏原因；刷新后仍可见。
   该资料不应产生可检索片段。点击“重试”会重新排队，原 `document_id` 保持不变。
4. 在 API 调试工具中用相同 `Idempotency-Key` 重发同一文件及元数据，检查返回原
   `job_id`；保持键不变但修改文件内容，应得到 `409`。
5. 在 worker 处理较大文件时终止进程，等待租约到期并重启。Job 应由新 worker
   重新领取，最终只保留一份检索片段集合；连续中断达到上限后应失败并允许手动重试。
6. 用另一账户查询或重试该 Job，应得到 `404`；跨课程查询同样应得到 `404`。

开发环境中可运行 `uv run pytest -q tests/test_m1_jobs.py tests/test_postgres_migrations.py`
验证排队、幂等、失败隔离和 PostgreSQL 发布事务。真实 PostgreSQL 测试要求使用
**可清空的独立数据库**设置 `TEST_DATABASE_URL`，不能指向日常使用的 `DATABASE_URL`。
