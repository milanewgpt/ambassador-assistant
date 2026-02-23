"""
Ingest endpoints — called by PAD relay flows.
Protected by X-Shared-Secret header.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Header, HTTPException, UploadFile, File

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


# ── Archive upload (large files, bypasses Telegram 20MB limit) ──

@router.post("/archive")
async def ingest_archive(
    file: UploadFile = File(...),
    x_shared_secret: str | None = Header(None),
):
    _check_secret(x_shared_secret)

    fname = (file.filename or "").lower()
    if not (fname.endswith(".zip") or fname.endswith(".js")):
        raise HTTPException(400, "Upload a .zip archive or tweets.js file")

    tmp_dir = tempfile.mkdtemp(prefix="amb_archive_")
    file_path = os.path.join(tmp_dir, file.filename)

    with open(file_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    log.info("Archive uploaded: %s (%.1f MB)", file.filename, os.path.getsize(file_path) / 1e6)

    from importer.x_archive import extract_tweets_from_zip, parse_tweets_js

    if fname.endswith(".js"):
        raw = open(file_path, "r", encoding="utf-8").read()
        parsed = parse_tweets_js(raw)
        tweets = [entry.get("tweet", entry) for entry in parsed]
    else:
        tweets = extract_tweets_from_zip(file_path)

    if not tweets:
        raise HTTPException(400, "No tweets found in the file")

    handle = settings.MAIN_X_HANDLE.lstrip("@")
    inserted = 0
    skipped = 0
    classified = 0

    for tw in tweets:
        tweet_id = tw.get("id_str") or tw.get("id")
        if not tweet_id:
            continue

        url = f"https://x.com/{handle}/status/{tweet_id}"
        created_str = tw.get("created_at", "")
        full_text = tw.get("full_text") or tw.get("text") or ""

        try:
            created_at = datetime.strptime(created_str, "%a %b %d %H:%M:%S %z %Y")
        except (ValueError, TypeError):
            created_at = datetime.now(timezone.utc)

        existing = await fetch_one("SELECT id FROM posts WHERE url = $1;", url)
        if existing:
            skipped += 1
            continue

        project_id = await classify_post(url, full_text)
        if project_id:
            classified += 1

        row = await fetch_one(
            """
            INSERT INTO posts (source, url, created_at, text, project_id)
            VALUES ('x_archive', $1, $2, $3, $4)
            RETURNING id;
            """,
            url, created_at, full_text, project_id,
        )

        run_at = created_at + timedelta(hours=settings.SCORING_DELAY_HOURS)
        if run_at < datetime.now(timezone.utc):
            run_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        await execute(
            "INSERT INTO score_jobs (post_id, run_at, status) VALUES ($1, $2, 'scheduled');",
            row["id"], run_at,
        )
        inserted += 1

    try:
        os.remove(file_path)
        os.rmdir(tmp_dir)
    except OSError:
        pass

    msg = f"Archive: {inserted} inserted, {skipped} duplicates, {classified} classified"
    log.info(msg)
    await send_telegram(f"📦 {msg}")

    return {"inserted": inserted, "skipped": skipped, "classified": classified}
