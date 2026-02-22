"""
Pydantic models for request/response validation.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ── Ingest payloads ──────────────────────────────────────────

class DiscordIngest(BaseModel):
    server: str
    channel: str
    preview: str = ""
    message_link: str
    observed_at: datetime = Field(default_factory=datetime.utcnow)


class XIngest(BaseModel):
    url: str
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    text: Optional[str] = None


# ── Telegram metric entry ────────────────────────────────────

class MetricsEntry(BaseModel):
    post_url: str
    likes: int
    replies: int
    reposts: int
    quotes: int
    views: Optional[int] = None


# ── LLM score output ────────────────────────────────────────

class LLMScoreResult(BaseModel):
    summary_en: str
    tags: list[str]
    quality: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    portfolio_blurb_en: str
    risk_framing: float = Field(ge=0, le=1)
    specificity: float = Field(ge=0, le=1)


# ── Project creation ────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    handles: list[str] = []
    keywords: list[str] = []
    priority: int = 0
    discord_servers: list[str] = []
    discord_channels: list[str] = []


# ── Generic responses ───────────────────────────────────────

class OkResponse(BaseModel):
    ok: bool = True
    detail: str = ""
