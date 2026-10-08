from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatModelConfig(BaseModel):
    """A browser-chat provider configuration, kept entirely on the server."""

    id: str = Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    label: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: SecretStr
    grounded: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    xpeach_api_key: SecretStr = SecretStr("")
    xpeach_base_url: str = "https://fast.xpeach.codes/v1"
    xpeach_chat_model: str = ""
    xpeach_chat_label: str = ""
    chat_models: list[ChatModelConfig] = Field(default_factory=list)
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1, le=16384)
    surreal_url: str = "http://127.0.0.1:8000"
    surreal_namespace: str = "final_review"
    surreal_database: str = "review"
    surreal_username: str = "root"
    surreal_password: SecretStr = SecretStr("change-me")
    # Local, self-managed PostgreSQL. It contains both business data and auth.
    database_url: SecretStr = SecretStr("")
    auth_mode: str = "database"
    auth_cookie_name: str = "final_review_access"
    auth_cookie_secure: bool = True
    auth_cookie_domain: str = ""
    auth_session_days: int = Field(default=30, ge=1, le=365)
    api_token: SecretStr = SecretStr("")
    uploads_dir: str = "uploads"
    exports_dir: str = "exports"
    export_pandoc_executable: str = ""
    export_chromium_executable: str = ""
    poppler_executable: str = ""
    libreoffice_executable: str = ""
    tesseract_executable: str = ""
    tessdata_prefix: str = ""
    material_vision_enabled: bool = False
    material_vision_model_id: str = "gemini-3-flash"
    material_vision_model: str = ""
    material_vision_timeout: float = Field(default=90, gt=0, le=300)
    material_vision_repairs: int = Field(default=1, ge=0, le=2)
    material_vision_concurrency: int = Field(default=4, ge=1, le=4)
    max_upload_mb: int = Field(default=10, ge=1, le=50)
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_candidates: int = Field(default=20, ge=1, le=100)
    min_similarity: float = Field(default=0.25, ge=-1, le=1)
    max_repairs: int = Field(default=1, ge=0, le=3)
    model_timeout: float = Field(default=60, gt=0)
    note_model_timeout: float = Field(default=120, gt=0)
    note_model_max_retries: int = Field(default=1, ge=0, le=3)
    fast_quiz_timeout: float = Field(default=30, gt=0, le=90)
    quiz_model_timeout: float = Field(default=120, gt=0, le=300)
    quiz_max_output_tokens: int = Field(default=16384, ge=2048, le=65536)
    quiz_choice_batch_size: int = Field(default=5, ge=1, le=6)
    quiz_written_batch_size: int = Field(default=2, ge=1, le=3)
    quiz_max_repairs: int = Field(default=2, ge=0, le=2)

    @model_validator(mode="after")
    def check_candidates(self):
        if self.retrieval_candidates < self.top_k:
            raise ValueError("RETRIEVAL_CANDIDATES 必须 >= TOP_K")
        return self

    @field_validator("chat_models")
    @classmethod
    def unique_chat_model_ids(cls, models: list[ChatModelConfig]):
        if len({model.id for model in models}) != len(models):
            raise ValueError("CHAT_MODELS 中的 id 不能重复")
        return models

    def available_chat_models(self) -> list[ChatModelConfig]:
        """Return only configured providers; secret values never leave this process."""
        if self.chat_models:
            models = self.chat_models
            xpeach_model_ids = {
                "codex-auto-review",
                "glm-5.3",
                "glm-5.3-flash",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-6-astra",
            }
            if (
                self.xpeach_api_key.get_secret_value()
                and self.xpeach_chat_model
                and any(model.id in xpeach_model_ids for model in models)
            ):
                models = [model for model in models if model.id not in xpeach_model_ids]
                models.append(
                    ChatModelConfig(
                        id=self.xpeach_chat_model,
                        label=self.xpeach_chat_label or self.xpeach_chat_model,
                        model=self.xpeach_chat_model,
                        base_url=self.xpeach_base_url,
                        api_key=self.xpeach_api_key,
                        grounded=False,
                    )
                )
            return [model for model in models if model.api_key.get_secret_value()]

        models = []
        if self.deepseek_api_key.get_secret_value():
            models.append(
                ChatModelConfig(
                    id="deepseek",
                    label="DeepSeek",
                    model=self.deepseek_model,
                    base_url=self.deepseek_base_url,
                    api_key=self.deepseek_api_key,
                )
            )
        if self.llm_api_key.get_secret_value():
            models.append(
                ChatModelConfig(
                    id="default",
                    label=self.llm_model,
                    model=self.llm_model,
                    base_url=self.llm_base_url,
                    api_key=self.llm_api_key,
                )
            )
        if self.xpeach_api_key.get_secret_value() and self.xpeach_chat_model:
            models.append(
                ChatModelConfig(
                    id=self.xpeach_chat_model,
                    label=self.xpeach_chat_label or self.xpeach_chat_model,
                    model=self.xpeach_chat_model,
                    base_url=self.xpeach_base_url,
                    api_key=self.xpeach_api_key,
                    grounded=False,
                )
            )
        return models

    def get_chat_model(self, model_id: str | None) -> ChatModelConfig:
        models = self.available_chat_models()
        if not models:
            raise ValueError("尚未配置可用聊天模型")
        if model_id is None:
            return models[0]
        for model in models:
            if model.id == model_id:
                return model
        raise ValueError("所选模型不可用")
