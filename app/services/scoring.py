"""
LLM scoring via provider (OpenRouter/MiniMax) + portfolio score computation.
"""

import json
import re
import math
import httpx
from datetime import datetime, timezone

from app.config import settings
from app.database import execute, fetch_one
from app.models import LLMScoreResult
from app.utils.logging import log

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _extract_assistant_content(data: dict) -> str:
    """
    Extract assistant text from OpenAI-compatible and near-compatible responses.
    Supports:
    - choices[0].message.content (string)
    - choices[0].message.content ([{"type":"text","text":"..."}])
    - choices[0].text
    """
    choices = data.get("choices") or []
    if not choices:
        raise ValueError(f"No choices in LLM response: keys={list(data.keys())[:10]}")

    choice0 = choices[0] or {}
    message = choice0.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str) and txt:
                        parts.append(txt)
            if parts:
                return "\n".join(parts)

    text = choice0.get("text")
    if isinstance(text, str):
        return text

    raise ValueError(f"Unsupported LLM response shape: {str(data)[:400]}")


async def _call_openrouter(prompt: str, max_tokens: int = 800) -> str:
    """Raw call to OpenRouter. Returns assistant content."""
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://ambassador-assistant.local",
        "X-Title": "Ambassador Assistant",
    }
    payload = {
        "model": settings.SCORING_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return _extract_assistant_content(data)


async def _call_minimax(prompt: str, max_tokens: int = 800) -> str:
    """
    MiniMax call using OpenAI-compatible format.
    Endpoint and path are configurable via env.
    """
    if not settings.MINIMAX_API_KEY:
        raise RuntimeError("MINIMAX_API_KEY is not configured")

    base = settings.MINIMAX_BASE_URL.rstrip("/")
    path = settings.MINIMAX_CHAT_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    url = f"{base}{path}"

    headers = {
        "Authorization": f"Bearer {settings.MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.SCORING_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return _extract_assistant_content(data)


async def call_llm(prompt: str, max_tokens: int = 800) -> str:
    provider = settings.LLM_PROVIDER.lower().strip()
    if provider == "openrouter":
        return await _call_openrouter(prompt, max_tokens=max_tokens)
    if provider == "minimax":
        return await _call_minimax(prompt, max_tokens=max_tokens)
    raise RuntimeError(f"Unsupported LLM_PROVIDER: {settings.LLM_PROVIDER}")


def _build_scoring_prompt(
    post_text: str,
    url: str,
    project_context: str | None,
    metrics: dict | None,
) -> str:
    parts = [
        "You are an expert Web3 content evaluator. "
        "Score the following post and return ONLY strict JSON (no markdown).",
        f"\nPost URL: {url}",
    ]
    if post_text:
        parts.append(f"Post text:\n{post_text[:3000]}")
    if project_context:
        parts.append(f"Project context: {project_context}")
    if metrics:
        parts.append(f"Engagement metrics: {json.dumps(metrics)}")

    parts.append(
        "\nReturn JSON with these exact keys:\n"
        "{\n"
        '  "summary_en": "1-2 sentence summary in English",\n'
        '  "tags": ["thread"|"analysis"|"risk"|"tutorial"|"update"|"opinion", ...],\n'
        '  "quality": 0.0-1.0,\n'
        '  "relevance": 0.0-1.0,\n'
        '  "portfolio_blurb_en": "1-2 line blurb suitable for ambassador forms",\n'
        '  "risk_framing": 0.0-1.0,\n'
        '  "specificity": 0.0-1.0\n'
        "}"
    )
    return "\n".join(parts)


async def score_post(post_id: str, force: bool = False) -> bool:
    """
    Run LLM scoring for a post. Returns True on success.
    Expects metrics to already exist (or proceeds without).
    """
    post = await fetch_one("SELECT * FROM posts WHERE id = $1;", post_id)
    if not post:
        log.error("score_post: post %s not found", post_id)
        return False

    if settings.SCORING_MODE.lower() != "llm":
        log.warning("SCORING_MODE=%s is not supported yet", settings.SCORING_MODE)
        return False

    existing_score = await fetch_one("SELECT post_id FROM llm_scores WHERE post_id = $1;", post_id)
    if existing_score and not force:
        log.info("Post %s already scored, skipping (force=%s)", post_id, force)
        return True

    project_context = None
    if post["project_id"]:
        proj = await fetch_one("SELECT name FROM projects WHERE id = $1;", post["project_id"])
        if proj:
            project_context = proj["name"]

    metrics_row = await fetch_one(
        "SELECT likes, replies, reposts, quotes, views FROM metrics_snapshots "
        "WHERE post_id = $1 ORDER BY captured_at DESC LIMIT 1;",
        post_id,
    )
    metrics = dict(metrics_row) if metrics_row else None

    prompt = _build_scoring_prompt(post["text"], post["url"], project_context, metrics)

    try:
        raw = await call_llm(prompt, max_tokens=600)
        match = re.search(r'\{[\s\S]*\}', raw)
        if not match:
            raise ValueError(f"No JSON found in LLM response: {raw[:200]}")

        result = LLMScoreResult(**json.loads(match.group()))
    except Exception as exc:
        log.error("LLM scoring failed for post %s: %s", post_id, exc)
        return False

    await execute(
        """
        INSERT INTO llm_scores (post_id, model, summary_en, tags, quality, relevance,
                                portfolio_blurb_en, risk_framing, specificity)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (post_id) DO UPDATE SET
            model = EXCLUDED.model,
            scored_at = now(),
            summary_en = EXCLUDED.summary_en,
            tags = EXCLUDED.tags,
            quality = EXCLUDED.quality,
            relevance = EXCLUDED.relevance,
            portfolio_blurb_en = EXCLUDED.portfolio_blurb_en,
            risk_framing = EXCLUDED.risk_framing,
            specificity = EXCLUDED.specificity;
        """,
        post_id,
        settings.SCORING_MODEL,
        result.summary_en,
        result.tags,
        result.quality,
        result.relevance,
        result.portfolio_blurb_en,
        result.risk_framing,
        result.specificity,
    )

    portfolio = compute_portfolio_score(result, metrics)
    await execute(
        "UPDATE posts SET portfolio_score = $1 WHERE id = $2;",
        portfolio,
        post_id,
    )

    log.info("Post %s scored: quality=%.2f relevance=%.2f portfolio=%.2f",
             post_id, result.quality, result.relevance, portfolio)
    return True


def compute_portfolio_score(
    llm: LLMScoreResult,
    metrics: dict | None,
    created_at: datetime | None = None,
) -> float:
    """
    final = 0.45*quality + 0.20*relevance + 0.25*engagement + 0.10*recency
    """
    engagement = _engagement_score(metrics)

    if created_at:
        age_days = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400
        recency = max(0.0, 1.0 - age_days / 365)
    else:
        recency = 0.5

    score = 0.45 * llm.quality + 0.20 * llm.relevance + 0.25 * engagement + 0.10 * recency
    return round(min(1.0, max(0.0, score)), 4)


def _engagement_score(metrics: dict | None) -> float:
    if not metrics:
        return 0.0

    likes = metrics.get("likes") or 0
    replies = metrics.get("replies") or 0
    reposts = metrics.get("reposts") or 0
    quotes = metrics.get("quotes") or 0

    weighted = likes + replies * 3 + reposts * 2 + quotes * 4
    return min(1.0, math.log1p(weighted) / math.log1p(500))
