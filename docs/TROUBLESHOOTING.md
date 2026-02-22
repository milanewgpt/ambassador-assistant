# Troubleshooting Guide

## Common Issues

### 1. Database Connection Fails

**Symptom:** `asyncpg.exceptions.ConnectionDoesNotExistError` or timeout on startup.

**Fix:**
- Verify `DATABASE_URL` in `.env` — it must be the **Transaction pooler** URI from
  Supabase Dashboard → Settings → Database → Connection string → URI.
- Format: `postgresql://postgres.XXXX:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres`
- Ensure your IP is not blocked by Supabase (check Database → Network restrictions).
- On Windows, ensure firewall allows outbound on port 6543.

### 2. Telegram Bot Not Responding

**Symptom:** Bot starts but doesn't reply to commands.

**Fix:**
- Confirm `TELEGRAM_BOT_TOKEN` is correct (from @BotFather).
- Confirm `TELEGRAM_CHAT_ID` is your personal chat ID (send `/start` to @userinfobot).
- Only the configured chat ID is authorized — check that you're messaging from the right account.
- Check logs for "Failed to start Telegram bot" errors.
- Ensure no other instance of the bot is running (only one process can poll the same bot token).

### 3. Ingest Endpoints Return 401

**Symptom:** PAD gets HTTP 401 from `/ingest/discord` or `/ingest/x`.

**Fix:**
- PAD must send header `X-Shared-Secret` with the exact value from `INGEST_SHARED_SECRET` in `.env`.
- Header name is case-sensitive in PAD's HTTP action: use exactly `X-Shared-Secret`.

### 4. Score Jobs Stuck in 'scheduled'

**Symptom:** Jobs exist in `score_jobs` with status `scheduled` but never process.

**Fix:**
- Ensure the worker is running: `python -m worker.scheduler` (or the `amb-worker` Docker container).
- Check worker logs in `logs/worker.log`.
- Verify `run_at` is in the past (jobs only run when `run_at <= now()`).
- Check `OPENROUTER_API_KEY` is valid.

### 5. Score Jobs Stuck in 'waiting_metrics'

**Symptom:** Job status is `waiting_metrics` and won't proceed.

**Fix:**
- This is expected in `manual` mode — you need to provide metrics via Telegram:
  `/metrics <post_url> <likes> <replies> <reposts> <quotes> [views]`
- After providing metrics, the worker will pick up the job on its next cycle.
- The worker also sends Telegram reminders for stale waiting jobs (> 4 hours).

### 6. OpenRouter Scoring Fails

**Symptom:** LLM scoring returns errors or invalid JSON.

**Fix:**
- Check `OPENROUTER_API_KEY` is valid at <https://openrouter.ai/keys>.
- Check that the model in `SCORING_MODEL` is available (e.g., `openai/gpt-4o`).
- Check OpenRouter rate limits and credit balance.
- Review the raw LLM response in logs — sometimes the model wraps JSON in markdown code fences.

### 7. PAD Flow Fails to Find UI Elements

**Symptom:** PAD throws "Element not found" errors.

**Fix:**
- Discord and X update their UI frequently. Re-record the UI elements using PAD's recorder.
- Ensure Discord/Chrome windows are **not minimized** — they must be visible (can be behind other windows, but not minimized to taskbar).
- Add longer waits between actions if elements haven't loaded yet.
- Use PAD's **On error** → **Continue flow** with retry logic.

### 8. Docker Containers Won't Start

**Symptom:** `docker compose up` fails.

**Fix:**
- Ensure `.env` file exists in the project root (copy from `.env.prod.example`).
- Check Docker is installed: `docker --version` and `docker compose version`.
- Check port 8000 isn't already in use: `ss -tlnp | grep 8000`.
- Review container logs: `docker logs amb-api` or `docker logs amb-worker`.

### 9. X Archive Import Fails

**Symptom:** `python -m importer.x_archive --archive file.zip` errors out.

**Fix:**
- Ensure the ZIP is the official X Data Archive (downloaded from X → Settings → Your Account → Download an archive).
- The archive must contain a file ending in `tweets.js` inside the `data/` folder.
- Check that `MAIN_X_HANDLE` is set in `.env` (required to construct tweet URLs).

### 10. Migrations Fail

**Symptom:** `python db/apply_migrations.py` throws an error.

**Fix:**
- Ensure `DATABASE_URL` is set and the database is reachable.
- If a migration partially applied, check the `_migrations` table and the actual schema.
- You can manually run SQL files via the Supabase SQL Editor if needed.

---

## Log Locations

| Component | Log File |
|-----------|----------|
| API server | `logs/ambassador.log` |
| Worker | `logs/worker.log` |
| Importer | `logs/importer.log` |
| Docker (API) | `docker logs amb-api` |
| Docker (Worker) | `docker logs amb-worker` |

## Useful Supabase SQL Queries

```sql
-- Recent unscored posts
SELECT url, source, created_at FROM posts
WHERE portfolio_score IS NULL
ORDER BY created_at DESC LIMIT 20;

-- Pending score jobs
SELECT sj.status, sj.run_at, sj.attempts, p.url
FROM score_jobs sj JOIN posts p ON p.id = sj.post_id
WHERE sj.status != 'done'
ORDER BY sj.run_at;

-- Top posts by score
SELECT url, portfolio_score, text
FROM posts WHERE hidden = false AND portfolio_score IS NOT NULL
ORDER BY portfolio_score DESC LIMIT 20;
```
