"""Runtime configuration from env + CLI overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from CWD if present
load_dotenv()

DEFAULT_SKILL_URL = (
    "https://raw.githubusercontent.com/lucianwhy/final-review/master/SKILL.md"
)
DEFAULT_AGENT_URL = (
    "https://raw.githubusercontent.com/lucianwhy/final-review/master/AGENT.md"
)

PACKAGE_ROOT = Path(__file__).resolve().parent
# langgraph-agent/ (Python project root when nested in lucianwhy/final-review)
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
# Repository root (sibling of langgraph-agent/); holds SKILL.md / AGENT.md
REPO_ROOT = PROJECT_ROOT.parent
# Prefer in-repo skill docs at repo root (from this package: ../../../SKILL.md)
LOCAL_SKILL_PATH = REPO_ROOT / "SKILL.md"
LOCAL_AGENT_PATH = REPO_ROOT / "AGENT.md"
CACHE_DIR = PROJECT_ROOT / ".cache" / "skills"


def _env_nonempty(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass
class Settings:
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_api_base: str | None = field(
        default_factory=lambda: os.getenv("OPENAI_API_BASE") or None
    )
    openai_model: str = field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    # Effective skill/agent URL used when loading remotely (fallback or override).
    skill_url: str = field(default_factory=lambda: DEFAULT_SKILL_URL)
    agent_url: str = field(default_factory=lambda: DEFAULT_AGENT_URL)
    # True when SKILL_URL / AGENT_URL env or CLI --skill-url / --agent-url forced remote.
    prefer_remote_skill: bool = False
    prefer_remote_agent: bool = False
    local_skill_path: Path = field(default_factory=lambda: LOCAL_SKILL_PATH)
    local_agent_path: Path = field(default_factory=lambda: LOCAL_AGENT_PATH)
    output_format: str = field(
        default_factory=lambda: os.getenv("OUTPUT_FORMAT", "text")
    )
    cache_dir: Path = field(default_factory=lambda: CACHE_DIR)
    http_timeout: float = 30.0

    @property
    def use_mock_llm(self) -> bool:
        return not bool(self.openai_api_key and self.openai_api_key.strip())

    def with_overrides(
        self,
        *,
        skill_url: str | None = None,
        agent_url: str | None = None,
        output_format: str | None = None,
    ) -> Settings:
        prefer_remote_skill = self.prefer_remote_skill or bool(skill_url)
        prefer_remote_agent = self.prefer_remote_agent or bool(agent_url)
        return Settings(
            openai_api_key=self.openai_api_key,
            openai_api_base=self.openai_api_base,
            openai_model=self.openai_model,
            skill_url=skill_url or self.skill_url,
            agent_url=agent_url or self.agent_url,
            prefer_remote_skill=prefer_remote_skill,
            prefer_remote_agent=prefer_remote_agent,
            local_skill_path=self.local_skill_path,
            local_agent_path=self.local_agent_path,
            output_format=(output_format or self.output_format).lower(),
            cache_dir=self.cache_dir,
            http_timeout=self.http_timeout,
        )


def get_settings() -> Settings:
    skill_env = _env_nonempty("SKILL_URL")
    agent_env = _env_nonempty("AGENT_URL")
    return Settings(
        skill_url=skill_env or DEFAULT_SKILL_URL,
        agent_url=agent_env or DEFAULT_AGENT_URL,
        prefer_remote_skill=bool(skill_env),
        prefer_remote_agent=bool(agent_env),
    )
