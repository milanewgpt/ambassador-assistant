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
from app.services.notifications import send_telegram
from app.utils.logging import setup_logging

log = setup_logging("worker")

RUNNING = True
MAX_ATTEMPTS = 5


def _shutdown(sig, frame):
    global RUNNING
    log.info("Shutdown signal received.")
    RUNNING = False


async def process_due_jobs():
    """Find and execute all score_jobs that are due."""
    now = datetime.now(timezone.utc)

    # 1) Handle scheduled jobs whose run_at has passed
    due = await fetch_all(
        """
        SELECT sj.id, sj.post_id, sj.attempts, p.url
        FROM score_jobs sj
        JOIN posts p ON p.id = sj.post_id
        WHERE sj.status = 'scheduled' AND sj.run_at <= $1
        ORDER BY sj.run_at
        LIMIT 20;
        """,
        now,
    )

    for job in due:
        job_id = job["id"]
        post_id = job["post_id"]
        attempts = job["attempts"] + 1

        log.info("Processing job %s for post %s (attempt %d)", job_id, post_id, attempts)
        await execute(
            "UPDATE score_jobs SET status = 'running', attempts = $1 WHERE id = $2;",
            attempts,
            job_id,
        )

        # Check if metrics exist
        has_metrics = await fetch_one(
            "SELECT id FROM metrics_snapshots WHERE post_id = $1 LIMIT 1;",
            post_id,
        )

        if not has_metrics and settings.METRICS_MODE == "manual":
            log.info("No metrics for post %s — requesting via Telegram.", post_id)
            await send_telegram(
                f"📊 Metrics needed for scoring:\n{job['url']}\n\n"
                f"Reply with:\n/metrics {job['url']} <likes> <replies> <reposts> <quotes> [views]"
            )
            await execute(
                "UPDATE score_jobs SET status = 'waiting_metrics' WHERE id = $1;",
                job_id,
            )
            continue

        try:
            ok = await score_post(post_id)
            if ok:
                await execute(
                    "UPDATE score_jobs SET status = 'done' WHERE id = $1;",
                    job_id,
                )
                log.info("Job %s completed successfully.", job_id)
            else:
                raise RuntimeError("score_post returned False")
        except Exception as exc:
            error_msg = str(exc)[:500]
            log.error("Job %s failed: %s", job_id, error_msg)

            if attempts >= MAX_ATTEMPTS:
                await execute(
                    "UPDATE score_jobs SET status = 'failed', last_error = $1 WHERE id = $2;",
                    error_msg,
                    job_id,
                )
                await send_telegram(f"❌ Scoring failed after {MAX_ATTEMPTS} attempts:\n{job['url']}")
            else:
                await execute(
                    "UPDATE score_jobs SET status = 'scheduled', last_error = $1 WHERE id = $2;",
                    error_msg,
                    job_id,
                )

    # 2) Nudge waiting_metrics jobs that have been waiting > 4 hours
    stale = await fetch_all(
        """
        SELECT sj.id, p.url, sj.attempts
        FROM score_jobs sj
        JOIN posts p ON p.id = sj.post_id
        WHERE sj.status = 'waiting_metrics'
          AND sj.run_at <= $1 - interval '4 hours';
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
    log.info("Worker stopped.")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    asyncio.run(main_loop())
