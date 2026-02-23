"""
Telegram bot — all /commands for managing the Ambassador Assistant.
Uses python-telegram-bot v20+ (async).
"""

import json
import asyncio
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from uuid import UUID

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings
from app.database import execute, fetch_all, fetch_one, fetch_val
from app.services.scoring import score_post
from app.services.classification import classify_post
from app.services.scraper import scrape_post_text
from app.utils.logging import log
from importer.x_archive import extract_tweets_from_zip


# ── Helpers ──────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Minimal HTML-escape for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _authorized(update: Update) -> bool:
    chat_id = str(update.effective_chat.id)
    return chat_id == settings.TELEGRAM_CHAT_ID


async def _deny(update: Update):
    await update.message.reply_text("⛔ Unauthorized.")


# ── /projects ────────────────────────────────────────────────

async def cmd_projects(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    rows = await fetch_all(
        "SELECT name, handles, keywords, priority FROM projects ORDER BY priority DESC, name;"
    )
    if not rows:
        return await update.message.reply_text("No projects configured yet.")

    lines = []
    for r in rows:
        handles = ", ".join(r["handles"]) if r["handles"] else "—"
        kw = ", ".join(r["keywords"][:5]) if r["keywords"] else "—"
        lines.append(f"<b>{_esc(r['name'])}</b> (p={r['priority']})\n  handles: {handles}\n  keywords: {kw}")

    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


# ── /project_add ─────────────────────────────────────────────

async def cmd_project_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    text = update.message.text.replace("/project_add", "", 1).strip()
    if not text:
        return await update.message.reply_text(
            "Paste JSON: {\"name\":\"…\",\"handles\":[],\"keywords\":[],\"priority\":0,"
            "\"discord_servers\":[],\"discord_channels\":[]}"
        )

    try:
        data = json.loads(text)
        name = data["name"]
    except (json.JSONDecodeError, KeyError) as exc:
        return await update.message.reply_text(f"Invalid JSON: {exc}")

    await execute(
        """
        INSERT INTO projects (name, handles, keywords, priority, discord_servers, discord_channels)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (name) DO UPDATE SET
            handles = EXCLUDED.handles,
            keywords = EXCLUDED.keywords,
            priority = EXCLUDED.priority,
            discord_servers = EXCLUDED.discord_servers,
            discord_channels = EXCLUDED.discord_channels;
        """,
        name,
        data.get("handles", []),
        data.get("keywords", []),
        data.get("priority", 0),
        data.get("discord_servers", []),
        data.get("discord_channels", []),
    )
    await update.message.reply_text(f"✅ Project '{name}' saved.")


# ── /what <project> ─────────────────────────────────────────

async def cmd_what(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    name = update.message.text.replace("/what", "", 1).strip()
    if not name:
        return await update.message.reply_text("Usage: /what <project_name>")

    proj = await fetch_one("SELECT id, name FROM projects WHERE lower(name) = lower($1);", name)
    if not proj:
        return await update.message.reply_text(f"Project '{name}' not found.")

    signals = await fetch_all(
        "SELECT channel, preview, message_link, created_at FROM signals "
        "WHERE project_id = $1 AND status = 'new' ORDER BY created_at DESC LIMIT 10;",
        proj["id"],
    )

    if not signals:
        return await update.message.reply_text(f"No new signals for {proj['name']}.")

    lines = [f"<b>New signals for {_esc(proj['name'])}:</b>\n"]
    for s in signals:
        preview = _esc((s["preview"] or "")[:120])
        lines.append(f"• #{s['channel']}: {preview}\n  {s['message_link']}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /best <project> [n] ─────────────────────────────────────

async def cmd_best(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    parts = update.message.text.split()
    if len(parts) < 2:
        return await update.message.reply_text("Usage: /best <project> [n=10]")

    name = parts[1]
    n = int(parts[2]) if len(parts) > 2 else 10

    proj = await fetch_one("SELECT id, name FROM projects WHERE lower(name) = lower($1);", name)
    if not proj:
        return await update.message.reply_text(f"Project '{name}' not found.")

    rows = await fetch_all(
        "SELECT url, portfolio_score, text FROM posts "
        "WHERE project_id = $1 AND hidden = false AND portfolio_score IS NOT NULL "
        "ORDER BY portfolio_score DESC LIMIT $2;",
        proj["id"],
        n,
    )

    if not rows:
        return await update.message.reply_text("No scored posts yet.")

    lines = [f"<b>Top {len(rows)} posts for {_esc(proj['name'])}:</b>\n"]
    for i, r in enumerate(rows, 1):
        snippet = _esc((r["text"] or "")[:80])
        lines.append(f"{i}. [{r['portfolio_score']:.3f}] {snippet}…\n   {r['url']}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /portfolio <project> [n] ────────────────────────────────

async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    parts = update.message.text.split()
    if len(parts) < 2:
        return await update.message.reply_text("Usage: /portfolio <project> [n=10]")

    name = parts[1]
    n = int(parts[2]) if len(parts) > 2 else 10

    proj = await fetch_one("SELECT id, name FROM projects WHERE lower(name) = lower($1);", name)
    if not proj:
        return await update.message.reply_text(f"Project '{name}' not found.")

    rows = await fetch_all(
        """
        SELECT p.url, p.portfolio_score, p.text,
               ls.summary_en, ls.tags, ls.portfolio_blurb_en,
               ms.likes, ms.replies, ms.reposts, ms.quotes, ms.views
        FROM posts p
        LEFT JOIN llm_scores ls ON ls.post_id = p.id
        LEFT JOIN LATERAL (
            SELECT * FROM metrics_snapshots WHERE post_id = p.id ORDER BY captured_at DESC LIMIT 1
        ) ms ON true
        WHERE p.project_id = $1 AND p.hidden = false AND p.portfolio_score IS NOT NULL
        ORDER BY p.portfolio_score DESC
        LIMIT $2;
        """,
        proj["id"],
        n,
    )

    if not rows:
        return await update.message.reply_text("No scored posts yet.")

    lines = [f"<b>Portfolio — {_esc(proj['name'])} (top {len(rows)}):</b>\n"]
    for i, r in enumerate(rows, 1):
        tags = ", ".join(r["tags"]) if r["tags"] else "—"
        blurb = _esc(r["portfolio_blurb_en"] or "—")
        summary = _esc(r["summary_en"] or "—")
        metrics_str = (
            f"❤️{r['likes'] or 0} 💬{r['replies'] or 0} "
            f"🔁{r['reposts'] or 0} 💎{r['quotes'] or 0}"
        )
        lines.append(
            f"{i}. <b>[{r['portfolio_score']:.3f}]</b>\n"
            f"   {r['url']}\n"
            f"   {summary}\n"
            f"   Tags: {tags}\n"
            f"   Blurb: {blurb}\n"
            f"   {metrics_str}"
        )

    text = "\n\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n… (truncated)"
    await update.message.reply_text(text, parse_mode="HTML")


# ── /feature <url> on|off ───────────────────────────────────

async def cmd_feature(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    parts = update.message.text.split()
    if len(parts) < 3:
        return await update.message.reply_text("Usage: /feature <post_url> on|off")

    url, toggle = parts[1], parts[2].lower()
    val = toggle == "on"
    result = await execute("UPDATE posts SET featured = $1 WHERE url = $2;", val, url)
    await update.message.reply_text(f"Featured = {val} for {url}")


# ── /hide <url> on|off ──────────────────────────────────────

async def cmd_hide(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    parts = update.message.text.split()
    if len(parts) < 3:
        return await update.message.reply_text("Usage: /hide <post_url> on|off")

    url, toggle = parts[1], parts[2].lower()
    val = toggle == "on"
    await execute("UPDATE posts SET hidden = $1 WHERE url = $2;", val, url)
    await update.message.reply_text(f"Hidden = {val} for {url}")


# ── /metrics <url> likes replies reposts quotes [views] ─────

async def cmd_metrics(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    parts = update.message.text.split()
    if len(parts) < 6:
        return await update.message.reply_text(
            "Usage: /metrics <post_url> <likes> <replies> <reposts> <quotes> [views]"
        )

    url = parts[1]
    try:
        likes = int(parts[2])
        replies = int(parts[3])
        reposts = int(parts[4])
        quotes = int(parts[5])
        views = int(parts[6]) if len(parts) > 6 else None
    except ValueError:
        return await update.message.reply_text("All metric values must be integers.")

    post = await fetch_one("SELECT id FROM posts WHERE url = $1;", url)
    if not post:
        return await update.message.reply_text(f"Post not found: {url}")

    await execute(
        """
        INSERT INTO metrics_snapshots (post_id, likes, replies, reposts, quotes, views)
        VALUES ($1, $2, $3, $4, $5, $6);
        """,
        post["id"],
        likes,
        replies,
        reposts,
        quotes,
        views,
    )

    # If there is a waiting_metrics job, transition it to scheduled
    await execute(
        """
        UPDATE score_jobs SET status = 'scheduled'
        WHERE post_id = $1 AND status = 'waiting_metrics';
        """,
        post["id"],
    )

    await update.message.reply_text(
        f"✅ Metrics saved for {url}\n"
        f"❤️{likes} 💬{replies} 🔁{reposts} 💎{quotes}"
        + (f" 👁{views}" if views else "")
    )


# ── /score_now <url> ────────────────────────────────────────

async def cmd_score_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    url = update.message.text.replace("/score_now", "", 1).strip()
    if not url:
        return await update.message.reply_text("Usage: /score_now <post_url>")

    post = await fetch_one("SELECT id, text FROM posts WHERE url = $1;", url)
    if not post:
        return await update.message.reply_text(f"Post not found: {url}")

    await update.message.reply_text("⏳ Scoring in progress …")

    ok = await score_post(post["id"])
    if ok:
        row = await fetch_one("SELECT portfolio_score FROM posts WHERE id = $1;", post["id"])
        await update.message.reply_text(f"✅ Scored! portfolio_score = {row['portfolio_score']}")
    else:
        await update.message.reply_text("❌ Scoring failed — check logs.")


# ── ZIP archive upload handler ───────────────────────────────

async def handle_archive_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Accept a ZIP or .js file in chat, parse X archive, import all tweets."""
    if not _authorized(update):
        return await _deny(update)

    doc = update.message.document
    fname = (doc.file_name or "").lower()

    if not (fname.endswith(".zip") or fname.endswith(".js")):
        return await update.message.reply_text(
            "Send a .zip archive or tweets.js file from your X / Twitter data export."
        )

    status_msg = await update.message.reply_text("📥 Downloading file …")

    try:
        tg_file = await ctx.bot.get_file(doc.file_id)

        tmp_dir = tempfile.mkdtemp(prefix="amb_archive_")
        file_path = os.path.join(tmp_dir, doc.file_name)
        await tg_file.download_to_drive(file_path)

        await status_msg.edit_text("📦 Extracting tweets …")

        if fname.endswith(".js"):
            from importer.x_archive import parse_tweets_js
            raw = open(file_path, "r", encoding="utf-8").read()
            parsed = parse_tweets_js(raw)
            tweets = [entry.get("tweet", entry) for entry in parsed]
        else:
            tweets = extract_tweets_from_zip(file_path)

        if not tweets:
            return await status_msg.edit_text("❌ No tweets found in the file.")

        handle = settings.MAIN_X_HANDLE.lstrip("@")
        await status_msg.edit_text(f"⚙️ Importing {len(tweets)} tweets …")

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

            if inserted % 100 == 0:
                await status_msg.edit_text(f"⚙️ Imported {inserted} / {len(tweets)} …")

        try:
            os.remove(file_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass

        await status_msg.edit_text(
            f"✅ Import complete!\n\n"
            f"📝 Inserted: {inserted}\n"
            f"🔁 Skipped (duplicates): {skipped}\n"
            f"🏷 Auto-classified: {classified}\n"
            f"📊 Score jobs created: {inserted}\n\n"
            f"Worker will start scoring in ~5 minutes."
        )
        log.info("Archive import via bot: %d inserted, %d skipped", inserted, skipped)

    except Exception as exc:
        log.error("Archive import failed: %s", exc)
        await status_msg.edit_text(f"❌ Import failed: {exc}")


# ── Post URL handler (plain text message with x.com link) ───

_X_URL_RE = re.compile(
    r'https?://(?:x\.com|twitter\.com)/(\w+)/status/(\d+)'
)

TWITTER_EPOCH_MS = 1288834974657


def tweet_id_to_datetime(tweet_id: int) -> datetime:
    """Extract publication timestamp from a Twitter snowflake ID."""
    timestamp_ms = (tweet_id >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


async def handle_post_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User sends a tweet URL — validate, deduplicate, store, schedule scoring."""
    if not _authorized(update):
        return await _deny(update)

    text = update.message.text or ""
    match = _X_URL_RE.search(text)
    if not match:
        return

    handle_in_url = match.group(1).lower()
    tweet_id_str = match.group(2)
    expected_handle = settings.MAIN_X_HANDLE.lower().lstrip("@")

    url = f"https://x.com/{handle_in_url}/status/{tweet_id_str}"

    if handle_in_url != expected_handle:
        return await update.message.reply_text(
            f"⛔ This post belongs to @{handle_in_url}, not @{expected_handle}.\n"
            f"Only your own posts can be added."
        )

    existing = await fetch_one("SELECT id FROM posts WHERE url = $1;", url)
    if existing:
        return await update.message.reply_text(
            f"ℹ️ This post is already in the database.\n{url}"
        )

    published_at = tweet_id_to_datetime(int(tweet_id_str))

    await update.message.reply_text("⏳ Fetching tweet text…")
    tweet_text = await scrape_post_text(url)

    project_id = await classify_post(url, tweet_text)

    row = await fetch_one(
        """
        INSERT INTO posts (source, url, created_at, text, project_id)
        VALUES ('x_relay', $1, $2, $3, $4)
        RETURNING id;
        """,
        url,
        published_at,
        tweet_text,
        project_id,
    )

    post_id = row["id"]
    run_at = published_at + timedelta(hours=settings.SCORING_DELAY_HOURS)
    now = datetime.now(timezone.utc)
    if run_at < now:
        run_at = now + timedelta(minutes=5)

    await execute(
        "INSERT INTO score_jobs (post_id, run_at, status) VALUES ($1, $2, 'scheduled');",
        post_id,
        run_at,
    )

    project_label = ""
    if project_id:
        proj = await fetch_one("SELECT name FROM projects WHERE id = $1;", project_id)
        if proj:
            project_label = f"\n🏷 Project: {proj['name']}"

    age = now - published_at
    age_str = f"{age.days}d {age.seconds // 3600}h ago" if age.days else f"{age.seconds // 3600}h {(age.seconds % 3600) // 60}m ago"

    if run_at <= now + timedelta(minutes=10):
        schedule_str = "~5 minutes (post is older than 48h)"
    else:
        schedule_str = f"{run_at.strftime('%Y-%m-%d %H:%M')} UTC"

    await update.message.reply_text(
        f"✅ Post added!\n"
        f"{url}\n"
        f"📅 Published: {published_at.strftime('%Y-%m-%d %H:%M')} UTC ({age_str}){project_label}\n"
        f"📊 Scoring in: {schedule_str}"
    )
    log.info("Post added via Telegram: %s (published %s)", url, published_at.isoformat())


# ── /reclassify — re-run classification on unlinked posts ────

async def cmd_reclassify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    count = await fetch_val(
        "SELECT count(*) FROM posts WHERE project_id IS NULL;"
    )
    if not count:
        return await update.message.reply_text("All posts are already classified.")

    msg = await update.message.reply_text(
        f"⚙️ Re-classifying {count} unlinked posts …"
    )

    rows = await fetch_all(
        "SELECT id, url, text FROM posts WHERE project_id IS NULL ORDER BY created_at DESC;"
    )

    classified = 0
    scraped = 0
    created_projects = set()

    for i, r in enumerate(rows):
        post_text = r["text"]

        if not post_text or len(post_text.strip()) < 10:
            tweet_text = await scrape_post_text(r["url"])
            if tweet_text:
                await execute(
                    "UPDATE posts SET text = $1 WHERE id = $2;",
                    tweet_text, r["id"],
                )
                post_text = tweet_text
                scraped += 1

        project_id = await classify_post(r["url"], post_text)
        if project_id:
            await execute(
                "UPDATE posts SET project_id = $1 WHERE id = $2;",
                project_id, r["id"],
            )
            classified += 1

            proj = await fetch_one("SELECT name FROM projects WHERE id = $1;", project_id)
            if proj:
                created_projects.add(proj["name"])

        if (i + 1) % 5 == 0:
            await msg.edit_text(f"⚙️ Processing {i + 1} / {count} (classified: {classified}) …")

    projects_list = ", ".join(sorted(created_projects)) if created_projects else "none"
    await msg.edit_text(
        f"✅ Reclassification done!\n\n"
        f"📝 Total unlinked: {count}\n"
        f"🔍 Texts scraped: {scraped}\n"
        f"🏷 Classified: {classified}\n"
        f"❓ Still unlinked: {count - classified}\n"
        f"📁 Projects used: {projects_list}"
    )
    log.info("Reclassify: %d/%d classified, %d scraped, projects: %s", classified, count, scraped, projects_list)


# ── /start & /help ──────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _deny(update)

    await update.message.reply_text(
        "<b>Ambassador Assistant Bot</b>\n\n"
        "/projects — list projects\n"
        "/project_add {json} — add/update project\n"
        "/what &lt;project&gt; — recent signals\n"
        "/best &lt;project&gt; [n] — top posts\n"
        "/portfolio &lt;project&gt; [n] — detailed portfolio\n"
        "/feature &lt;url&gt; on|off\n"
        "/hide &lt;url&gt; on|off\n"
        "/metrics &lt;url&gt; likes replies reposts quotes [views]\n"
        "/score_now &lt;url&gt; — force immediate scoring\n"
        "/reclassify — auto-classify unlinked posts\n\n"
        "🔗 <b>Add post:</b> send an x.com/…/status/… link\n"
        "📎 <b>Import archive:</b> send tweets.js or .zip (&lt;20MB)\n"
        "📎 <b>Large archive:</b> use curl (see below)",
        parse_mode="HTML",
    )


# ── Bot factory ─────────────────────────────────────────────

def build_bot_app() -> Application:
    """Build and return a python-telegram-bot Application (not started)."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("project_add", cmd_project_add))
    app.add_handler(CommandHandler("what", cmd_what))
    app.add_handler(CommandHandler("best", cmd_best))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("feature", cmd_feature))
    app.add_handler(CommandHandler("hide", cmd_hide))
    app.add_handler(CommandHandler("metrics", cmd_metrics))
    app.add_handler(CommandHandler("score_now", cmd_score_now))
    app.add_handler(CommandHandler("reclassify", cmd_reclassify))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_archive_upload))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_post_url
    ))

    return app
