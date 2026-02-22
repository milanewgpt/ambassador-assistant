"""
Project classification — cascade logic:
  1. Handle match
  2. Keyword match (>=2 hits)
  3. Optional LLM (confidence >= 0.7)
  4. Otherwise null
"""

import json
import re
from uuid import UUID
from typing import Optional

from app.database import fetch_all, fetch_one
from app.services.scoring import call_openrouter
from app.utils.logging import log


async def _all_projects() -> list[dict]:
    rows = await fetch_all(
        "SELECT id, name, handles, keywords, discord_servers, discord_channels FROM projects ORDER BY priority DESC;"
    )
    return [dict(r) for r in rows]


async def classify_post(url: str, text: str | None) -> Optional[UUID]:
    """Classify a post by URL handle, then keyword, then optional LLM."""
    projects = await _all_projects()
    if not projects:
        return None

    url_lower = (url or "").lower()
    text_lower = (text or "").lower()

    # 1) Handle match — check if any handle appears in the URL
    for p in projects:
        for h in (p["handles"] or []):
            if h.lower() in url_lower:
                log.info("Classified post by handle '%s' -> project '%s'", h, p["name"])
                return p["id"]

    # 2) Keyword match — at least 2 keyword hits in combined text
    combined = f"{url_lower} {text_lower}"
    for p in projects:
        hits = sum(1 for kw in (p["keywords"] or []) if kw.lower() in combined)
        if hits >= 2:
            log.info("Classified post by keywords (%d hits) -> project '%s'", hits, p["name"])
            return p["id"]

    # 3) LLM fallback (optional, best-effort)
    if text:
        return await _llm_classify(text, projects)

    return None


async def classify_signal(server: str, channel: str) -> Optional[UUID]:
    """Match a Discord signal to a project by discord_servers / discord_channels."""
    projects = await _all_projects()
    server_lower = server.lower()
    channel_lower = channel.lower()

    for p in projects:
        servers = [s.lower() for s in (p["discord_servers"] or [])]
        channels = [c.lower() for c in (p["discord_channels"] or [])]
        if server_lower in servers or channel_lower in channels:
            log.info("Classified signal by Discord mapping -> project '%s'", p["name"])
            return p["id"]

    return None


async def _llm_classify(text: str, projects: list[dict]) -> Optional[UUID]:
    """Ask LLM which project matches. Returns None if confidence < 0.7."""
    try:
        project_names = [p["name"] for p in projects]
        prompt = (
            "You are a crypto/Web3 content classifier. "
            "Given the following post text, decide which project it belongs to. "
            f"Possible projects: {json.dumps(project_names)}.\n\n"
            f"Post text: {text[:1500]}\n\n"
            "Respond ONLY with JSON: {\"project\": \"<name or null>\", \"confidence\": 0.0-1.0}"
        )

        raw = await call_openrouter(prompt, max_tokens=100)
        match = re.search(r'\{[^}]+\}', raw)
        if not match:
            return None

        data = json.loads(match.group())
        confidence = float(data.get("confidence", 0))
        proj_name = data.get("project")

        if confidence < 0.7 or not proj_name or proj_name == "null":
            log.info("LLM classification below threshold (%.2f)", confidence)
            return None

        for p in projects:
            if p["name"].lower() == proj_name.lower():
                log.info("LLM classified -> project '%s' (confidence %.2f)", p["name"], confidence)
                return p["id"]

    except Exception as exc:
        log.warning("LLM classification failed (non-fatal): %s", exc)

    return None
