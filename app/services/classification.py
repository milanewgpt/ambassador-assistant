"""
Project classification — cascade:
  1. Handle match (existing projects)
  2. Keyword match >=2 hits (existing projects)
  3. LLM: identify project from text → match existing OR auto-create new
"""

import json
import re
from uuid import UUID
from typing import Optional

from app.database import fetch_all, fetch_one, fetch_val
from app.services.scoring import call_openrouter
from app.utils.logging import log


async def _all_projects() -> list[dict]:
    rows = await fetch_all(
        "SELECT id, name, handles, keywords, discord_servers, discord_channels "
        "FROM projects ORDER BY priority DESC;"
    )
    return [dict(r) for r in rows]


async def classify_post(url: str, text: str | None) -> Optional[UUID]:
    """Classify a post by handle, keyword, or LLM. Auto-creates projects."""
    projects = await _all_projects()
    url_lower = (url or "").lower()
    text_lower = (text or "").lower()
    combined = f"{url_lower} {text_lower}"

    # 1) Handle match
    for p in projects:
        for h in (p["handles"] or []):
            if h.lower() in url_lower:
                log.info("Classified by handle '%s' -> '%s'", h, p["name"])
                return p["id"]

    # 2) Keyword match (>=2 hits)
    for p in projects:
        hits = sum(1 for kw in (p["keywords"] or []) if kw.lower() in combined)
        if hits >= 2:
            log.info("Classified by keywords (%d hits) -> '%s'", hits, p["name"])
            return p["id"]

    # 3) LLM — identify project, match or create
    if text and len(text.strip()) > 20:
        return await _llm_classify_or_create(text, projects)

    # 4) Extract mentions from URL/text as last resort
    return await _extract_and_create(combined, projects)


async def classify_signal(server: str, channel: str) -> Optional[UUID]:
    """Match a Discord signal to a project by discord_servers / discord_channels."""
    projects = await _all_projects()
    server_lower = server.lower()
    channel_lower = channel.lower()

    for p in projects:
        servers = [s.lower() for s in (p["discord_servers"] or [])]
        channels = [c.lower() for c in (p["discord_channels"] or [])]
        if server_lower in servers or channel_lower in channels:
            log.info("Classified signal -> '%s'", p["name"])
            return p["id"]

    return None


async def _find_or_create_project(name: str, handles: list[str] = None, keywords: list[str] = None) -> UUID:
    """Find existing project by name (case-insensitive) or create a new one."""
    existing = await fetch_one(
        "SELECT id FROM projects WHERE lower(name) = lower($1);", name
    )
    if existing:
        return existing["id"]

    new_id = await fetch_val(
        """
        INSERT INTO projects (name, handles, keywords, priority)
        VALUES ($1, $2, $3, 0)
        RETURNING id;
        """,
        name,
        handles or [],
        keywords or [],
    )
    log.info("Auto-created project: '%s' (handles=%s, keywords=%s)", name, handles, keywords)
    return new_id


async def _llm_classify_or_create(text: str, projects: list[dict]) -> Optional[UUID]:
    """
    Ask LLM to identify the crypto/Web3 project from post text.
    If the project exists — return its ID.
    If not — create it and return the new ID.
    """
    try:
        existing_names = [p["name"] for p in projects] if projects else []
        existing_hint = f"\nAlready known projects: {json.dumps(existing_names)}" if existing_names else ""

        prompt = (
            "You are a crypto/Web3 content classifier.\n"
            "Identify the MAIN project or protocol this post is about.\n\n"
            f"Post text: {text[:2000]}\n"
            f"{existing_hint}\n\n"
            "Rules:\n"
            "- Return the project/protocol name (e.g. 'Solana', 'Uniswap', 'Arbitrum')\n"
            "- If the post matches a known project, use EXACTLY that name\n"
            "- If it's a new project not in the list, give its proper name\n"
            "- If the post is generic crypto talk not about a specific project, return null\n"
            "- Include the project's Twitter handle if you know it\n\n"
            "Respond ONLY with JSON:\n"
            '{"project": "<name or null>", "handle": "<twitter_handle or null>", '
            '"keywords": ["keyword1", "keyword2"], "confidence": 0.0-1.0}'
        )

        raw = await call_openrouter(prompt, max_tokens=150)
        match = re.search(r'\{[^}]+\}', raw)
        if not match:
            return None

        data = json.loads(match.group())
        confidence = float(data.get("confidence", 0))
        proj_name = data.get("project")

        if confidence < 0.6 or not proj_name or proj_name == "null":
            log.info("LLM: no specific project (confidence=%.2f)", confidence)
            return None

        proj_name = proj_name.strip()
        handles = []
        if data.get("handle") and data["handle"] != "null":
            handles = [data["handle"].lstrip("@")]
        keywords = [k for k in (data.get("keywords") or []) if k and k != "null"]

        # Try to match existing project
        for p in projects:
            if p["name"].lower() == proj_name.lower():
                log.info("LLM matched existing project '%s' (%.2f)", p["name"], confidence)
                return p["id"]

        # Auto-create new project
        new_id = await _find_or_create_project(proj_name, handles, keywords)
        log.info("LLM -> new project '%s' (confidence=%.2f)", proj_name, confidence)
        return new_id

    except Exception as exc:
        log.warning("LLM classification failed: %s", exc)
        return None


async def _extract_and_create(combined_text: str, projects: list[dict]) -> Optional[UUID]:
    """
    Last resort: extract @mentions from text/URL and try to match or create.
    Only creates if a clear single mention is found.
    """
    mentions = re.findall(r'@(\w{2,30})', combined_text)
    if not mentions:
        return None

    from app.config import settings
    own_handle = settings.MAIN_X_HANDLE.lower().lstrip("@")
    mentions = [m for m in mentions if m.lower() != own_handle]

    if not mentions:
        return None

    target = mentions[0]

    for p in projects:
        for h in (p["handles"] or []):
            if h.lower() == target.lower():
                return p["id"]

    return None
