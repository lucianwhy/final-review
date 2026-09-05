# Final Review Agent

An exam-oriented review Agent backend built with **LangChain, LangGraph, and SurrealDB**. It can run independently or be extracted as one Agent module from a larger learning system.

The backend ingests course materials, retrieves grounded evidence, answers exam-focused questions, generates mock exams, evaluates submissions, and feeds weak knowledge points into the next review round. The original [SKILL.md](SKILL.md) remains the domain protocol; executable rules live in `src/final_review/policy.py` and the graph nodes.

## What is implemented

- LangGraph `StateGraph` with conditional routing, bounded evidence repair, human interrupts, and a SurrealDB-backed checkpointer.
- LangChain chat model, tool calling (`search_course_material`), Retriever, PromptTemplate, and Pydantic structured output.
- SurrealDB document/chunk/session/checkpoint tables and course/chapter-filtered cosine retrieval with source-priority reranking.
- MarkItDown conversion for Markdown, text, PDF, PPTX, and DOCX inputs.
- One `FinalReviewAgent`, not a collection of artificial sub-agents. Deterministic parsing, retrieval, validation, and persistence stay graph nodes; model decisions stay behind the Agent interface.
- FastAPI endpoints for ingestion, invocation, exam-profile resume, answer evaluation, session recovery, and Markdown export.

The main implementation and Chinese quick-start guide are in [README.md](README.md). Architecture trade-offs are in [docs/architecture.md](docs/architecture.md). Live model evaluation cases are in [eval/README.md](eval/README.md).

## Quick start

```powershell
uv sync --frozen
Copy-Item .env.example .env
# Set model keys, compatible model URLs, and SURREAL_PASSWORD in .env
docker compose up -d surrealdb
uv run uvicorn final_review.api:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
```

Open `http://127.0.0.1:8080/docs`, or run the real end-to-end example:

```powershell
uv run python examples/demo.py
```

The test suite uses explicit in-memory adapters and fake model fixtures, so it does not require model keys:

```powershell
uv run pytest -q
uv run ruff check src tests examples eval
```

Real SurrealDB integration tests are opt-in. They create and delete an isolated throwaway database:

```powershell
$env:SURREAL_TEST_URL='http://127.0.0.1:8000'
$env:SURREAL_TEST_PASSWORD='your-local-password'
uv run pytest -m integration -q
```

The backend currently targets a small, single-process corpus. It uses exact cosine retrieval rather than claiming HNSW, hybrid retrieval, or trained reranking. It also does not include login, multi-tenant authorization, distributed locks, or a frontend. These limits are documented because the project demonstrates real Agent workflow behavior rather than a product-sized UI.

## License and domain rules

The repository started as a reusable exam-review Skill. Keep source categories honest: past exams, instructor slides, homework, crash-course material, and AI supplements have different priorities. AI-generated examples must not be labeled as real exam material.
