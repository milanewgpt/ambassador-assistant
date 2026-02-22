"""
Windows development runner — starts FastAPI + Worker in one process.
Usage:
    python run_dev.py
"""

import asyncio
import signal
import threading
import uvicorn

from app.utils.logging import log


def run_api():
    log.info("Starting FastAPI dev server on :8000 …")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )


def run_worker():
    log.info("Starting worker scheduler …")
    from worker.scheduler import main_loop
    asyncio.run(main_loop())


def main():
    api_thread = threading.Thread(target=run_api, daemon=True, name="api")
    worker_thread = threading.Thread(target=run_worker, daemon=True, name="worker")

    api_thread.start()
    worker_thread.start()

    log.info("Dev runner active. Press Ctrl+C to stop.")

    try:
        api_thread.join()
    except KeyboardInterrupt:
        log.info("Shutting down dev runner …")


if __name__ == "__main__":
    main()
