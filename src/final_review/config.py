from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1, le=16384)
    surreal_url: str = "http://127.0.0.1:8000"
    surreal_namespace: str = "final_review"
    surreal_database: str = "review"
    surreal_username: str = "root"
    surreal_password: SecretStr = SecretStr("change-me")
    api_token: SecretStr = SecretStr("")
    max_upload_mb: int = Field(default=10, ge=1, le=50)
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_candidates: int = Field(default=20, ge=1, le=100)
    min_similarity: float = Field(default=0.25, ge=-1, le=1)
    max_repairs: int = Field(default=1, ge=0, le=3)
    model_timeout: float = Field(default=60, gt=0)

    @model_validator(mode="after")
    def check_candidates(self):
        if self.retrieval_candidates < self.top_k:
            raise ValueError("RETRIEVAL_CANDIDATES 必须 >= TOP_K")
        return self
