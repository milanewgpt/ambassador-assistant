"""
Telegram notification helpers (simple HTTP, no bot framework needed here).
"""

import httpx
from app.config import settings
from app.utils.logging import log

TELEGRAM_API = "https://api.telegram.org"


async def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        log.warning("Telegram credentials not configured — skipping notification.")
        return False

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                log.error("Telegram send failed (%d): %s", resp.status_code, resp.text[:300])
                return False
        return True
    except Exception as exc:
        log.error("Telegram send exception: %s", exc)
        return False
