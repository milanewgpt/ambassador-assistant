"""
Headless browser scraper for X/Twitter post metrics.
Uses Playwright (Chromium) — no API, no login required for public posts.
"""

import re
from dataclasses import dataclass
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from app.utils.logging import log

_METRICS_TIMEOUT_MS = 15_000


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


async def scrape_post_metrics(url: str) -> ScrapedMetrics | None:
    """
    Open a tweet URL in headless Chromium and scrape engagement metrics.
    Returns ScrapedMetrics on success, None on failure.
    """
    log.info("Scraping metrics for: %s", url)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=_METRICS_TIMEOUT_MS)
            await page.wait_for_timeout(5000)

            # Check for login wall
            if await page.locator('text="Sign in"').count() > 3:
                log.warning("Login wall detected for %s", url)
                await browser.close()
                return None

            metrics = await _extract_from_aria(page)
            if not metrics:
                metrics = await _extract_from_spans(page)

            await browser.close()

            if metrics:
                log.info(
                    "Scraped %s: likes=%d replies=%d reposts=%d views=%d",
                    url, metrics.likes, metrics.replies, metrics.reposts, metrics.views,
                )
            else:
                log.warning("Could not extract metrics from %s", url)

            return metrics

    except PwTimeout:
        log.warning("Timeout scraping %s", url)
        return None
    except Exception as exc:
        log.error("Scraper error for %s: %s", url, exc)
        return None
