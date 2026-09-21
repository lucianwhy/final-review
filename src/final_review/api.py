import logging
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from markitdown import MarkItDown
from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from .agent import FinalReviewAgent, SessionConflict
from .config import Settings
from .llm import ModelError, build_models
from .rag import KnowledgeBase
from .rendering import render_markdown
from .schemas import (
    AgentRequest,
    AgentResponse,
    ChatRequest,
    ChatResponse,
    Identifier,
    MaterialInput,
    ResumeRequest,
    SourceType,
    Submission,
)
from .storage import StorageError, SurrealStore

logger = logging.getLogger(__name__)


def convert_upload(file: UploadFile, max_bytes: int) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".md", ".txt", ".pdf", ".pptx", ".docx"}:
        raise ValueError("仅支持 md/txt/pdf/pptx/docx；扫描件请先 OCR")
    content = file.file.read(max_bytes + 1)
    if not content or len(content) > max_bytes:
        raise ValueError("文件为空或超过上传大小限制")
    if suffix in {".md", ".txt"}:
        return content.decode("utf-8-sig")
    if suffix in {".pptx", ".docx"}:
        from io import BytesIO
        from zipfile import ZipFile

        with ZipFile(BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > max_bytes * 20:
                raise ValueError("Office 文件解压体积超过限制")
    with tempfile.TemporaryDirectory(prefix="final-review-") as directory:
        # Client filename is never used as a filesystem path.
        path = Path(directory) / f"material{suffix}"
        path.write_bytes(content)
        return MarkItDown(enable_plugins=False).convert(str(path)).text_content


def create_app(settings: Settings | None = None, agent: FinalReviewAgent | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        if agent is not None:
            app.state.agent = agent
            yield
            return
        # The standalone chat API can run before the RAG store and its two model
        # providers have been configured. Agent endpoints remain unavailable.
        if not (
            settings.llm_api_key.get_secret_value()
            and settings.embedding_api_key.get_secret_value()
        ):
            app.state.agent = None
            yield
            return
        store = SurrealStore(settings)
        try:
            model, embeddings = build_models(settings)
            store.setup()
            kb = KnowledgeBase(store, embeddings, settings)
            app.state.agent = FinalReviewAgent(store, kb, model, settings)
            yield
        finally:
            store.close()

    app = FastAPI(
        title="Final Review Agent",
        version="0.1.0",
        description="课程资料入库、证据问答、模拟测评与薄弱点反馈。单进程后端。",
        lifespan=lifespan,
    )
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        expected = settings.api_token.get_secret_value()
        if expected and (
            credentials is None
            or not secrets.compare_digest(
                credentials.credentials.encode(),
                expected.encode(),
            )
        ):
            raise HTTPException(401, "需要有效 Bearer Token")

    def runtime():
        if app.state.agent is None:
            raise HTTPException(503, "资料库服务尚未配置")
        return app.state.agent

    @app.exception_handler(SessionConflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def validation_handler(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def model_validation_handler(request, exc):
        logger.warning("Structured data validation failed: %s", type(exc).__name__)
        return JSONResponse(status_code=502, content={"detail": "模型或资料输出格式不符合约束"})

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": "会话不存在"})

    @app.exception_handler(StorageError)
    async def storage_handler(request, exc):
        logger.error("Storage operation failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503, content={"detail": "数据库操作失败，请检查服务日志与配置"}
        )

    async def model_handler(request, exc):
        logger.warning("Model operation failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=502, content={"detail": "模型调用失败，可使用 recover 恢复任务"}
        )

    app.add_exception_handler(ModelError, model_handler)
    app.add_exception_handler(OpenAIError, model_handler)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(authorize)])
    def chat(request: ChatRequest):
        api_key = settings.deepseek_api_key.get_secret_value()
        if not api_key:
            raise HTTPException(503, "尚未配置 DEEPSEEK_API_KEY")
        messages = [
            {
                "role": "system",
                "content": (
                    "你是考前笔记的复习助手。用中文回答，清晰、简洁、以考试得分为导向。"
                    "资料不足时说明不确定性，不要编造课程材料。"
                ),
            },
            *[item.model_dump() for item in request.history],
            {"role": "user", "content": request.message},
        ]
        client = OpenAI(api_key=api_key, base_url=settings.deepseek_base_url)
        completion = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            max_tokens=1200,
        )
        reply = completion.choices[0].message.content
        if not reply:
            raise HTTPException(502, "模型未返回可显示的内容")
        return ChatResponse(reply=reply, model=settings.deepseek_model)

    @app.post("/knowledge/ingest", dependencies=[Depends(authorize)])
    def ingest(request: MaterialInput):
        return runtime().kb.ingest(request)

    @app.post("/knowledge/upload", dependencies=[Depends(authorize)])
    def upload(
        course_id: Identifier = Form(),
        title: str = Form(),
        source_type: SourceType = Form(),
        chapter: str = Form(""),
        file: UploadFile = File(),
    ):
        try:
            markdown = convert_upload(file, settings.max_upload_mb * 1024 * 1024)
            material = MaterialInput(
                course_id=course_id,
                title=title,
                source_type=source_type,
                chapter=chapter,
                markdown=markdown,
            )
        except Exception as exc:
            raise HTTPException(422, "文件转换失败，请检查格式、大小和可读性") from exc
        finally:
            file.file.close()
        return runtime().kb.ingest(material)

    @app.post("/agent/invoke", response_model=AgentResponse, dependencies=[Depends(authorize)])
    def invoke(request: AgentRequest):
        return runtime().invoke(request)

    @app.post("/agent/resume", response_model=AgentResponse, dependencies=[Depends(authorize)])
    def resume(request: ResumeRequest):
        return runtime().resume_profile(request)

    @app.post(
        "/assessment/evaluate", response_model=AgentResponse, dependencies=[Depends(authorize)]
    )
    def evaluate(request: Submission):
        return runtime().evaluate(request)

    @app.post("/agent/recover", response_model=AgentResponse, dependencies=[Depends(authorize)])
    def recover(course_id: Identifier, session_id: Identifier):
        return runtime().recover(course_id, session_id)

    @app.get("/agent/session", response_model=AgentResponse, dependencies=[Depends(authorize)])
    def session(course_id: Identifier, session_id: Identifier):
        return runtime().read(course_id, session_id)

    @app.get("/agent/export", dependencies=[Depends(authorize)])
    def export(course_id: Identifier, session_id: Identifier):
        response = runtime().read(course_id, session_id)
        return PlainTextResponse(render_markdown(response), media_type="text/markdown")

    return app
