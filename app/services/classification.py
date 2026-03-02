"""
Project classification — rule-based cascade:
  1. Explicit token extraction from post text (@handle, $TICKER, #hashtag)
  2. Handle/token match in URL
  3. Keyword match >=2 hits (existing projects)
No LLM calls in classification path.
"""

import re
from uuid import UUID
from typing import Optional

from app.config import settings
from app.database import fetch_all, fetch_one, fetch_val
from app.utils.logging import log


async def _all_projects() -> list[dict]:
    rows = await fetch_all(
        "SELECT id, name, handles, keywords, discord_servers, discord_channels "
        "FROM projects ORDER BY priority DESC;"
    )
    return [dict(r) for r in rows]


async def classify_post(url: str, text: str | None) -> Optional[UUID]:
    """Classify a post with deterministic rules. No LLM fallback."""
    projects = await _all_projects()
    url_lower = (url or "").lower()
    text_lower = (text or "").lower()
    combined = f"{url_lower} {text_lower}"

    # 1) Explicit token extraction from post text (highest priority)
    by_text_token = await _extract_match_or_create(text_lower, projects)
    if by_text_token:
        log.info("Classified by explicit token in post text")
        return by_text_token

    # 2) Handle match in URL
    for p in projects:
        for h in (p["handles"] or []):
            if h.lower() in url_lower:
                log.info("Classified by URL handle '%s' -> '%s'", h, p["name"])
                return p["id"]

    # 2b) Explicit token extraction from URL (fallback inside URL stage)
    by_url_token = await _extract_match_or_create(url_lower, projects)
    if by_url_token:
        log.info("Classified by explicit token in URL")
        return by_url_token

    # 3) Keyword match (>=2 hits) as final fallback
    for p in projects:
        hits = sum(1 for kw in (p["keywords"] or []) if kw.lower() in combined)
        if hits >= 2:
            log.info("Classified by keywords (%d hits) -> '%s'", hits, p["name"])
            return p["id"]

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


def _normalize_name(token: str) -> str:
    if token.isupper() and len(token) <= 8:
        return token
    return token.capitalize()


def _extract_candidate_tokens(combined_text: str) -> list[tuple[str, str]]:
    """
    Return explicit candidate tokens in priority order:
    mention -> cashtag -> hashtag.
    Each token is returned as (kind, token_without_prefix).
    """
    mentions = re.findall(r'@([A-Za-z0-9_]{2,30})', combined_text)
    cashtags = re.findall(r'\$([A-Za-z][A-Za-z0-9_]{1,15})', combined_text)
    hashtags = re.findall(r'#([A-Za-z][A-Za-z0-9_]{1,30})', combined_text)

    tokens: list[tuple[str, str]] = []
    for m in mentions:
        tokens.append(("mention", m))
    for c in cashtags:
        tokens.append(("cashtag", c))
    for h in hashtags:
        tokens.append(("hashtag", h))
    return tokens


async def _extract_match_or_create(combined_text: str, projects: list[dict]) -> Optional[UUID]:
    """
    Extract explicit project tokens (@, $, #), then match existing or optionally create.
    """
    if settings.CLASSIFICATION_MODE.lower() != "rules":
        # In simplified mode we only support deterministic classification.
        return None

    own_handle = settings.MAIN_X_HANDLE.lower().lstrip("@")
    stop_tokens = {"crypto", "web3", "nft", "defi", "airdrop", "giveaway", "thread"}
    candidates = _extract_candidate_tokens(combined_text)
    if not candidates:
        return None

    seen: set[str] = set()
    for kind, token in candidates:
        t = token.lower()
        if t in seen or t == own_handle or t in stop_tokens:
            continue
        seen.add(t)

        # Existing match by handles or exact project name
        for p in projects:
            if p["name"].lower() == t:
                return p["id"]
            for h in (p["handles"] or []):
                if h.lower() == t:
                    return p["id"]

        if not settings.AUTO_CREATE_PROJECTS:
            continue

        if kind == "mention":
            return await _find_or_create_project(_normalize_name(token), handles=[token], keywords=[token.lower()])
        if kind == "cashtag":
            return await _find_or_create_project(_normalize_name(token.upper()), handles=[], keywords=[token.lower()])
        if kind == "hashtag":
            return await _find_or_create_project(_normalize_name(token), handles=[], keywords=[token.lower()])

    return None
