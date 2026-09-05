FROM python:3.13-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY SKILL.md ./SKILL.md
RUN uv sync --frozen --no-dev
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "uvicorn", "final_review.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
