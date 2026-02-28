"""
Worker process — polls score_jobs and executes due scoring tasks.
Runs as a standalone process (not inside FastAPI).

Usage:
    python -m worker.scheduler
"""

import asyncio
import signal
import sys
from datetime import datetime, timezone

from app.config import settings
from app.database import get_pool, close_pool, fetch_all, fetch_one, execute
from app.services.scoring import score_post
from app.services.scraper import scrape_post_metrics, shutdown_scraper
from app.services.notifications import send_telegram
from app.utils.logging import setup_logging

log = setup_logging("worker")

RUNNING = True
MAX_ATTEMPTS = 5


def _shutdown(sig, frame):
    global RUNNING
    log.info("Shutdown signal received.")
    RUNNING = False


async def _try_scrape_and_save(post_id, url: str) -> bool:
    """Attempt to scrape metrics via headless browser. Returns True if saved."""
    try:
        metrics = await scrape_post_metrics(url)
        if metrics and (metrics.likes or metrics.replies or metrics.reposts or metrics.views):
            await execute(
                """
                INSERT INTO metrics_snapshots (post_id, likes, replies, reposts, quotes, views)
                VALUES ($1, $2, $3, $4, $5, $6);
                """,
                post_id,
                metrics.likes,
                metrics.replies,
                metrics.reposts,
                metrics.quotes,
                metrics.views,
            )
            log.info("Auto-scraped metrics for %s: L=%d R=%d RP=%d V=%d",
                     url, metrics.likes, metrics.replies, metrics.reposts, metrics.views)
            return True
    except Exception as exc:
        log.warning("Scrape failed for %s: %s", url, exc)
    return False


async def process_due_jobs():
    """Find and execute all score_jobs that are due."""
    now = datetime.now(timezone.utc)

    due = await fetch_all(
        """
        SELECT sj.id, sj.post_id, sj.attempts, p.url, p.source
        FROM score_jobs sj
        JOIN posts p ON p.id = sj.post_id
        WHERE sj.status = 'scheduled' AND sj.run_at <= $1
        ORDER BY
            CASE
                WHEN p.source = 'x_relay' THEN 0
                WHEN p.source = 'manual' THEN 1
                ELSE 2
            END,
            sj.run_at
        LIMIT 10;
        """,
        now,
    )

    for job in due:
        job_id = job["id"]
        post_id = job["post_id"]
        post_url = job["url"]
        source = job["source"]
        attempts = job["attempts"] + 1
        is_archive = source == "x_archive"

        log.info("Processing job %s for %s (attempt %d, source=%s)",
                 job_id, post_url, attempts, source)
        await execute(
            "UPDATE score_jobs SET status = 'running', attempts = $1 WHERE id = $2;",
            attempts, job_id,
        )

        already_scored = await fetch_one(
            "SELECT post_id FROM llm_scores WHERE post_id = $1;",
            post_id,
        )
        if already_scored:
            await execute("UPDATE score_jobs SET status = 'done' WHERE id = $1;", job_id)
            log.info("Job %s marked done: post already has llm_scores.", job_id)
            continue

        has_metrics = await fetch_one(
            "SELECT id FROM metrics_snapshots WHERE post_id = $1 LIMIT 1;",
            post_id,
        )

        if not has_metrics:
            scraped = await _try_scrape_and_save(post_id, post_url)
            has_metrics = scraped

        # Decide what to do if still no metrics
        if not has_metrics:
            if is_archive:
                # Archive posts: score without metrics, don't bother the user
                log.info("Archive post %s — scoring without metrics.", post_url)
            else:
                # New posts: ask user via Telegram, wait for manual entry
                log.info("No metrics for new post %s — asking user.", post_url)
                await send_telegram(
                    f"📊 Could not auto-scrape metrics for:\n{post_url}\n\n"
                    f"Please reply with:\n"
                    f"/metrics {post_url} <likes> <replies> <reposts> <quotes> [views]"
                )
                await execute(
                    "UPDATE score_jobs SET status = 'waiting_metrics' WHERE id = $1;",
                    job_id,
                )
                continue

        # Run LLM scoring
        try:
            ok = await score_post(post_id)
            if ok:
                await execute(
                    "UPDATE score_jobs SET status = 'done' WHERE id = $1;",
                    job_id,
                )
                log.info("Job %s completed.", job_id)
            else:
                raise RuntimeError("score_post returned False")
        except Exception as exc:
            error_msg = str(exc)[:500]
            log.error("Job %s failed: %s", job_id, error_msg)

            if attempts >= MAX_ATTEMPTS:
                await execute(
                    "UPDATE score_jobs SET status = 'failed', last_error = $1 WHERE id = $2;",
                    error_msg, job_id,
                )
                if not is_archive:
                    await send_telegram(f"❌ Scoring failed after {MAX_ATTEMPTS} attempts:\n{post_url}")
            else:
                await execute(
                    "UPDATE score_jobs SET status = 'scheduled', last_error = $1 WHERE id = $2;",
                    error_msg, job_id,
                )

    # Nudge waiting_metrics jobs older than 4 hours
    stale = await fetch_all(
        """
        SELECT sj.id, p.url
        FROM score_jobs sj
        JOIN posts p ON p.id = sj.post_id
        WHERE sj.status = 'waiting_metrics'
          AND sj.run_at <= ($1::timestamptz - interval '4 hours');
        """,
        now,
    )
    for s in stale:
        await send_telegram(
            f"⏰ Still waiting for metrics:\n{s['url']}\n"
            f"/metrics {s['url']} <likes> <replies> <reposts> <quotes> [views]"
        )


async def main_loop():
    log.info("Worker starting — poll interval: %ds", settings.WORKER_POLL_SECONDS)
    await get_pool()

    while RUNNING:
        try:
            await process_due_jobs()
        except Exception as exc:
            log.error("Worker loop error: %s", exc)

        for _ in range(settings.WORKER_POLL_SECONDS):
            if not RUNNING:
                break
            await asyncio.sleep(1)

    await close_pool()
    await shutdown_scraper()
    log.info("Worker stopped.")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    asyncio.run(main_loop())
