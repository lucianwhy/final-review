"""Load SKILL.md / AGENT.md: local-first, then remote URL + cache."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx

from final_review_agent.config import Settings

logger = logging.getLogger(__name__)


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / f"{_url_hash(url)}.md"


def fetch_text(url: str, settings: Settings, *, force: bool = False) -> str:
    """Fetch URL text; use file cache under .cache/skills/ keyed by URL hash."""
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(settings.cache_dir, url)

    if not force and path.is_file():
        logger.info("skill cache hit: %s -> %s", url, path.name)
        return path.read_text(encoding="utf-8")

    logger.info("fetching skill: %s", url)
    with httpx.Client(timeout=settings.http_timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.text

    path.write_text(text, encoding="utf-8")
    # Also store a sidecar with the source URL for debugging
    path.with_suffix(".url").write_text(url + "\n", encoding="utf-8")
    return text


def _load_one(
    *,
    local_path: Path,
    url: str,
    prefer_remote: bool,
    settings: Settings,
    force: bool = False,
) -> tuple[str, str]:
    """
    Resolve one skill document.

    Priority:
      1. Explicit remote override (SKILL_URL / AGENT_URL / --skill-url / --agent-url)
      2. Local in-repo file when present (repo-root SKILL.md / AGENT.md)
      3. Fallback remote raw GitHub URL (cached under .cache/skills/)
    """
    if prefer_remote:
        text = fetch_text(url, settings, force=force)
        return text, url

    if local_path.is_file():
        logger.info("skill local hit: %s", local_path)
        return local_path.read_text(encoding="utf-8"), str(local_path)

    text = fetch_text(url, settings, force=force)
    return text, url


def load_skills(settings: Settings, *, force: bool = False) -> dict[str, str]:
    """Load SKILL.md and AGENT.md (local-first, optional remote override)."""
    skill_md, skill_src = _load_one(
        local_path=settings.local_skill_path,
        url=settings.skill_url,
        prefer_remote=settings.prefer_remote_skill,
        settings=settings,
        force=force,
    )
    agent_md, agent_src = _load_one(
        local_path=settings.local_agent_path,
        url=settings.agent_url,
        prefer_remote=settings.prefer_remote_agent,
        settings=settings,
        force=force,
    )
    return {
        "skill_md": skill_md,
        "agent_md": agent_md,
        "skill_url": skill_src,
        "agent_url": agent_src,
    }
