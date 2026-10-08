# 本地 PostgreSQL 部署

本项目使用 FastAPI 自管账户和 Session；不需要 Supabase 或 Redis。浏览器持有
HttpOnly Cookie，PostgreSQL 保存密码的 Argon2id 哈希及 Session ID 的 SHA-256 哈希。

## 首次启动

1. 复制 `.env.example` 为 `.env`，设置 `POSTGRES_PASSWORD`，并让
   `DATABASE_URL` 中的密码一致。
2. 运行 `docker compose up -d postgres`。再运行 `docker compose up -d agent`；Agent
   在接受流量前会执行版本化迁移。它适用于新卷和已有数据库，不依赖 Docker init
   目录（后者只会在新卷运行）。
3. 课程与考试功能无需模型 Key；需要 AI 资料问答与生成时，再配置模型相关环境变量并重启 Agent。

若 PostgreSQL 由本机服务而非 Compose 管理，手动执行：

```powershell
uv run python -m final_review.migrations
```

迁移记录在 `schema_migrations`，保存文件名和 SHA-256。再次运行只会校验已应用
版本，不会重新执行 SQL；文件被修改或一个旧库只具备部分 001/002 基线时会失败，
而不是猜测、删除或放宽历史数据。升级前应先备份；若旧 Attempt 没有可验证的
legacy fast-quiz session，003 会停止，需先按原始记录补齐该会话后再重试。

开发环境使用 `AUTH_COOKIE_SECURE=false`；部署到 HTTPS 后必须设为 `true`。
原始上传文件存放在 `uploads/<user_id>/<course_id>/`。该目录及 PostgreSQL 数据卷
都应纳入本机备份，但不可提交到 Git。
