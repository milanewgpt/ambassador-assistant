"""
Headless browser scraper for X/Twitter post metrics.
Uses Playwright (Chromium) — no API, no login required for public posts.
"""

import asyncio
import os
import re
import time
from dataclasses import dataclass
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from app.utils.logging import log

_METRICS_TIMEOUT_MS = 15_000
_SCRAPER_MAX_CONCURRENCY = int(os.getenv("SCRAPER_MAX_CONCURRENCY", "2"))

# Keeping a single Chromium instance per process dramatically reduces:
# - CPU spikes from repeated browser launches
# - child process churn
# - risk of accumulating zombie processes if PID 1 doesn't reap correctly
#
# Concurrency is still capped to avoid overload when multiple Telegram commands
# trigger scraping at once.
_pw = None
_browser = None
_init_lock = asyncio.Lock()
_sem = asyncio.Semaphore(_SCRAPER_MAX_CONCURRENCY)
_last_used_at = 0.0


@dataclass
class ScrapedMetrics:
    likes: int = 0
    replies: int = 0
    reposts: int = 0
    quotes: int = 0
    views: int = 0


def _parse_count(text: str) -> int:
    """Parse '1.2K', '3.4M', '520' etc. into an integer."""
    if not text:
        return 0
    text = text.strip().replace(",", "")
    m = re.match(r'^([\d.]+)\s*([KkMm]?)$', text)
    if not m:
        return 0
    num = float(m.group(1))
    suffix = m.group(2).upper()
    if suffix == "K":
        num *= 1_000
    elif suffix == "M":
        num *= 1_000_000
    return int(num)


async def _ensure_browser():
    """Start Playwright + Chromium once and reuse across calls."""
    global _pw, _browser, _last_used_at
    async with _init_lock:
        if _browser is not None:
            try:
                if _browser.is_connected():
                    _last_used_at = time.time()
                    return
            except Exception:
                # Fall through to recreate
                pass

        # Best-effort cleanup if a previous instance exists but is broken
        try:
            if _browser is not None:
                await _browser.close()
        except Exception:
            pass
        _browser = None

        if _pw is None:
            _pw = await async_playwright().start()

        _browser = await _pw.chromium.launch(headless=True)
        _last_used_at = time.time()


async def shutdown_scraper():
    """Close Chromium/Playwright (call on process shutdown)."""
    global _pw, _browser
    async with _init_lock:
        try:
            if _browser is not None:
                await _browser.close()
        finally:
            _browser = None

        try:
            if _pw is not None:
                await _pw.stop()
        finally:
            _pw = None


async def _run_in_page(url: str, fn):
    """
    Run a scraping function in an isolated context+page, reusing a single Chromium instance.
    `fn(page)` should return a value.
    """
    await _ensure_browser()

    async with _sem:
        context = await _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=_METRICS_TIMEOUT_MS)
            return await fn(page)
        finally:
            try:
                await context.close()
            except Exception:
                pass


async def _extract_from_aria(page) -> ScrapedMetrics | None:
    """Try to extract metrics from aria-label attributes on action buttons."""
    metrics = ScrapedMetrics()

    selectors = {
        'replies': '[data-testid="reply"]',
        'reposts': '[data-testid="retweet"]',
        'likes':   '[data-testid="like"]',
    }

    for field, sel in selectors.items():
        try:
            el = page.locator(sel).first
            aria = await el.get_attribute("aria-label", timeout=3000)
            if aria:
                nums = re.findall(r'([\d,.]+[KkMm]?)', aria)
                if nums:
                    setattr(metrics, field, _parse_count(nums[0]))
        except Exception:
            pass

    # Views from the "Views" or "views" text near the post
    try:
        views_el = page.locator('[data-testid="app-text-transition-container"]').first
        views_text = await views_el.inner_text(timeout=3000)
        metrics.views = _parse_count(views_text)
    except Exception:
        pass

    if metrics.likes or metrics.replies or metrics.reposts or metrics.views:
        return metrics
    return None


async def _extract_from_spans(page) -> ScrapedMetrics | None:
    """Fallback: look for metric counts in span elements near action buttons."""
    metrics = ScrapedMetrics()

    try:
        group_els = await page.locator('article [role="group"]').first.locator(
            '[data-testid="app-text-transition-container"]'
        ).all()

        values = []
        for el in group_els:
            txt = await el.inner_text(timeout=2000)
            values.append(_parse_count(txt))

        if len(values) >= 3:
            metrics.replies = values[0]
            metrics.reposts = values[1]
            metrics.likes = values[2]
        if len(values) >= 4:
            metrics.views = values[3]

        if any(v > 0 for v in values):
            return metrics
    except Exception:
        pass

    return None


async def scrape_post_text(url: str) -> str | None:
    """
    Open a tweet URL in headless Chromium and extract the tweet text.
    Returns the text or None on failure.
    """
    log.info("Scraping text for: %s", url)
    try:
        async def _fn(page):
            await page.wait_for_timeout(4000)

            text = None
            try:
                el = page.locator('[data-testid="tweetText"]').first
                text = await el.inner_text(timeout=5000)
            except Exception:
                pass

            if not text:
                try:
                    el = page.locator('article div[lang]').first
                    text = await el.inner_text(timeout=3000)
                except Exception:
                    pass

            if text and len(text.strip()) > 5:
                log.info("Scraped text (%d chars) for %s", len(text), url)
                return text.strip()
            else:
                log.warning("Could not extract text from %s", url)
                return None

        return await _run_in_page(url, _fn)

    except PwTimeout:
        log.warning("Timeout scraping text for %s", url)
        return None
    except Exception as exc:
        log.error("Text scraper error for %s: %s", url, exc)
        return None


async def scrape_post_metrics(url: str) -> ScrapedMetrics | None:
    """
    Open a tweet URL in headless Chromium and scrape engagement metrics.
    Returns ScrapedMetrics on success, None on failure.
    """
    log.info("Scraping metrics for: %s", url)

    try:
        async def _fn(page):
            await page.wait_for_timeout(5000)

            # Check for login wall
            if await page.locator('text="Sign in"').count() > 3:
                log.warning("Login wall detected for %s", url)
                return None

            metrics = await _extract_from_aria(page)
            if not metrics:
                metrics = await _extract_from_spans(page)

            if metrics:
                log.info(
                    "Scraped %s: likes=%d replies=%d reposts=%d views=%d",
                    url, metrics.likes, metrics.replies, metrics.reposts, metrics.views,
                )
            else:
                log.warning("Could not extract metrics from %s", url)

            return metrics

        return await _run_in_page(url, _fn)

    except PwTimeout:
        log.warning("Timeout scraping %s", url)
        return None
    except Exception as exc:
        log.error("Scraper error for %s: %s", url, exc)
        return None
