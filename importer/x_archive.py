"""
Import posts from a Twitter/X Data Archive ZIP.

Usage:
    python -m importer.x_archive --archive path/to/twitter-archive.zip

The archive should contain data/tweets.js (or data/tweet.js).
Each tweet is inserted into the posts table with source='x_archive'.
Score jobs are scheduled at created_at + 48h.
"""

import argparse
import asyncio
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.database import get_pool, close_pool, execute, fetch_one
from app.services.classification import classify_post
from app.utils.logging import setup_logging

log = setup_logging("importer")


def parse_tweets_js(raw: str) -> list[dict]:
    """
    tweets.js starts with `window.YTD.tweet.part0 = [...]`
    Strip the JS assignment to get valid JSON.
    """
    match = re.search(r'=\s*(\[[\s\S]*\])', raw)
    if not match:
        raise ValueError("Could not find JSON array in tweets.js")
    return json.loads(match.group(1))


def extract_tweets_from_zip(zip_path: str) -> list[dict]:
    tweets = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        candidates = [
            n for n in zf.namelist()
            if n.endswith("tweets.js") or n.endswith("tweet.js")
        ]
        if not candidates:
            raise FileNotFoundError("No tweets.js found in archive")

        for name in candidates:
            log.info("Reading %s from archive …", name)
            raw = zf.read(name).decode("utf-8")
            parsed = parse_tweets_js(raw)
            for entry in parsed:
                tw = entry.get("tweet", entry)
                tweets.append(tw)

    log.info("Extracted %d tweets from archive.", len(tweets))
    return tweets


async def import_tweets(zip_path: str):
    handle = settings.MAIN_X_HANDLE.lstrip("@")
    if not handle:
        log.error("MAIN_X_HANDLE not configured. Set it in .env.")
        return

    tweets = extract_tweets_from_zip(zip_path)
    await get_pool()

    inserted = 0
    skipped = 0

    for tw in tweets:
        tweet_id = tw.get("id_str") or tw.get("id")
        if not tweet_id:
            continue

        url = f"https://x.com/{handle}/status/{tweet_id}"
        created_str = tw.get("created_at", "")
        full_text = tw.get("full_text") or tw.get("text") or ""

        try:
            created_at = datetime.strptime(
                created_str, "%a %b %d %H:%M:%S %z %Y"
            )
        except (ValueError, TypeError):
            created_at = datetime.now(timezone.utc)

        existing = await fetch_one("SELECT id FROM posts WHERE url = $1;", url)
        if existing:
            skipped += 1
            continue

        project_id = await classify_post(url, full_text)

        row = await fetch_one(
            """
            INSERT INTO posts (source, url, created_at, text, project_id)
            VALUES ('x_archive', $1, $2, $3, $4)
            RETURNING id;
            """,
            url,
            created_at,
            full_text,
            project_id,
        )

        run_at = created_at + timedelta(hours=settings.SCORING_DELAY_HOURS)
        if run_at < datetime.now(timezone.utc):
            run_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        await execute(
            "INSERT INTO score_jobs (post_id, run_at, status) VALUES ($1, $2, 'scheduled');",
            row["id"],
            run_at,
        )
        inserted += 1

    await close_pool()
    log.info("Import complete: %d inserted, %d skipped (duplicates).", inserted, skipped)


def main():
    parser = argparse.ArgumentParser(description="Import X archive into Ambassador Assistant")
    parser.add_argument("--archive", "-a", required=True, help="Path to Twitter/X data archive ZIP")
    args = parser.parse_args()

    if not Path(args.archive).exists():
        log.error("Archive file not found: %s", args.archive)
        return

    asyncio.run(import_tweets(args.archive))


if __name__ == "__main__":
    main()
