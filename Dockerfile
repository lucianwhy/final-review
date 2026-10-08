FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-impress poppler-utils tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng fonts-noto-cjk fonts-noto-core && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN uv run --no-sync playwright install --with-deps chromium
COPY src ./src
COPY db ./db
COPY SKILL.md ./SKILL.md
RUN uv sync --frozen --no-dev
EXPOSE 8080
CMD ["sh", "-c", "uv run --no-sync python -m final_review.migrations \"$DATABASE_URL\" && uv run --no-sync uvicorn final_review.api:create_app --factory --host 0.0.0.0 --port 8080 --workers 1"]
