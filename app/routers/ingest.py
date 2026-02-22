"""
Ingest endpoints — called by PAD relay flows.
Protected by X-Shared-Secret header.
"""

from datetime import timedelta
from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app.database import execute, fetch_one
from app.models import DiscordIngest, XIngest, OkResponse
from app.services.classification import classify_post, classify_signal
from app.services.notifications import send_telegram
from app.utils.logging import log

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _check_secret(secret: str | None):
    if secret != settings.INGEST_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Invalid shared secret")


# ── Discord ──────────────────────────────────────────────────

@router.post("/discord", response_model=OkResponse)
async def ingest_discord(
    body: DiscordIngest,
    x_shared_secret: str | None = Header(None),
):
    _check_secret(x_shared_secret)
    log.info("Discord ingest: %s / %s", body.server, body.channel)

    existing = await fetch_one(
        "SELECT id FROM signals WHERE message_link = $1;", body.message_link
    )
    if existing:
        log.info("Duplicate signal ignored: %s", body.message_link)
        return OkResponse(detail="duplicate")

    project_id = await classify_signal(body.server, body.channel)

    await execute(
        """
        INSERT INTO signals (source, project_id, server, channel, preview, message_link, created_at)
        VALUES ('discord_relay', $1, $2, $3, $4, $5, $6);
        """,
        project_id,
        body.server,
        body.channel,
        body.preview,
        body.message_link,
        body.observed_at,
    )

    project_label = ""
    if project_id:
        row = await fetch_one("SELECT name FROM projects WHERE id = $1;", project_id)
        project_label = f" [{row['name']}]" if row else ""

    text = (
        f"📢 Discord Signal{project_label}\n"
        f"Server: {body.server}\n"
        f"Channel: #{body.channel}\n"
        f"{body.preview[:300]}\n"
        f"{body.message_link}"
    )
    await send_telegram(text)
    log.info("Discord signal stored and forwarded.")
    return OkResponse(detail="stored")


# ── X (Twitter) ──────────────────────────────────────────────

@router.post("/x", response_model=OkResponse)
async def ingest_x(
    body: XIngest,
    x_shared_secret: str | None = Header(None),
):
    _check_secret(x_shared_secret)
    log.info("X ingest: %s", body.url)

    existing = await fetch_one(
        "SELECT id FROM posts WHERE url = $1;", body.url
    )
    if existing:
        log.info("Duplicate post ignored: %s", body.url)
        return OkResponse(detail="duplicate")

    project_id = await classify_post(body.url, body.text)

    row = await fetch_one(
        """
        INSERT INTO posts (source, url, created_at, text, project_id)
        VALUES ('x_relay', $1, $2, $3, $4)
        RETURNING id, created_at;
        """,
        body.url,
        body.observed_at,
        body.text,
        project_id,
    )

    post_id = row["id"]
    run_at = row["created_at"] + timedelta(hours=settings.SCORING_DELAY_HOURS)
    await execute(
        """
        INSERT INTO score_jobs (post_id, run_at, status)
        VALUES ($1, $2, 'scheduled');
        """,
        post_id,
        run_at,
    )

    log.info("Post stored, score job scheduled for %s", run_at.isoformat())
    return OkResponse(detail="stored")
