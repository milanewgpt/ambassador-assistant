# Ambassador Assistant

End-to-end system for crypto/Web3 ambassador workflow automation: capturing
Discord signals and X posts via Windows UI relay, scoring content with LLM via
OpenRouter, and managing everything through a Telegram bot.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Windows Server 2022 (Relay + Dev)              │
│  ┌───────────┐  ┌──────────────────────┐        │
│  │  Discord   │  │  Chrome (X profile)  │        │
│  └─────┬─────┘  └──────────┬───────────┘        │
│        │ PAD Flow A        │ PAD Flow B          │
│        └────────┬──────────┘                     │
│           HTTP POST /ingest/*                    │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│  Linux VPS (Production)                         │
│  ┌─────────┐  ┌────────┐  ┌──────────────────┐  │
│  │ FastAPI  │  │ Worker │  │  Telegram Bot    │  │
│  │  :8000   │  │ (cron) │  │  (polling)       │  │
│  └────┬─────┘  └───┬────┘  └───────┬──────────┘  │
│       └────────────┼────────────────┘            │
│                    │                             │
└────────────────────┼─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│  Supabase (Postgres)                             │
│  projects │ posts │ signals │ score_jobs │ ...    │
└──────────────────────────────────────────────────┘
```

## Repository Structure

```
ambassador-assistant/
├── app/                    # FastAPI backend + Telegram bot
│   ├── main.py             # App entry point + lifespan
│   ├── config.py           # Environment-based settings
│   ├── database.py         # asyncpg connection pool
│   ├── models.py           # Pydantic schemas
│   ├── routers/
│   │   ├── health.py       # GET /health
│   │   └── ingest.py       # POST /ingest/discord, /ingest/x
│   ├── services/
│   │   ├── classification.py  # Project classification cascade
│   │   ├── scoring.py         # OpenRouter LLM scoring
│   │   ├── notifications.py   # Telegram message sender
│   │   └── telegram_bot.py    # Bot commands (/projects, /best, etc.)
│   └── utils/
│       └── logging.py      # Rotating file + console logger
├── worker/
│   └── scheduler.py        # Delayed scoring job processor
├── importer/
│   └── x_archive.py        # X Data Archive ZIP importer
├── db/
│   ├── 001_initial_schema.sql  # Postgres migration
│   └── apply_migrations.py     # Migration runner
├── docs/
│   ├── PAD_DISCORD_RELAY.md    # Step-by-step PAD build guide
│   ├── PAD_X_RELAY.md          # Step-by-step PAD build guide
│   └── TROUBLESHOOTING.md
├── docker-compose.yml
├── Dockerfile
├── nginx.conf
├── requirements.txt
├── run_dev.py              # Windows: run API + worker together
├── .env.dev.example
├── .env.prod.example
└── .gitignore
```

---

## Setup Guide

### 1. Create Supabase Project

1. Go to <https://supabase.com/dashboard> and create a new project.
2. Note down:
   - **Project URL** (e.g. `https://abcdef.supabase.co`)
   - **Service role key** (Settings → API → service_role)
   - **Database URI** (Settings → Database → Connection string → URI → **Transaction pooler**)
     - Format: `postgresql://postgres.XXXX:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres`
3. Replace `[YOUR-PASSWORD]` in the URI with the database password you set during project creation.

### 2. Run Migrations

```bash
# Install psycopg2 if not already installed
pip install psycopg2-binary python-dotenv

# Option A: Direct SQL (from any machine with psql)
psql "postgresql://postgres.XXXX:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres" -f db/001_initial_schema.sql

# Option B: Python runner (reads DATABASE_URL from .env)
cp .env.dev.example .env   # fill in DATABASE_URL
python db/apply_migrations.py
```

You can verify tables were created in **Supabase Studio** → Table Editor.

### 3. Create Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram.
2. Send `/newbot`, follow prompts, and copy the **bot token**.
3. Send `/start` to [@userinfobot](https://t.me/userinfobot) to get your **chat ID**.
4. Set both in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF
   TELEGRAM_CHAT_ID=123456789
   ```

### 4. Get OpenRouter API Key

1. Go to <https://openrouter.ai/keys>.
2. Create an API key and add credits.
3. Set in `.env`:
   ```
   OPENROUTER_API_KEY=sk-or-v1-...
   SCORING_MODEL=openai/gpt-4o
   ```

### 5. Configure Environment

```bash
cp .env.dev.example .env
# Edit .env with your real values
```

Key settings:
- `DATABASE_URL` — Supabase Postgres connection string
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- `OPENROUTER_API_KEY`
- `MAIN_X_HANDLE` — your X handle without the `@`
- `INGEST_SHARED_SECRET` — random string, must match PAD flows

---

## Running Locally on Windows

### Prerequisites

- Python 3.11+ installed (`python --version`)
- pip available

### Install Dependencies

```powershell
cd C:\Users\Administrator\Desktop\AMB\ambassador-assistant
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Run Everything (Dev Mode)

```powershell
python run_dev.py
```

This starts both the FastAPI server (port 8000) and the worker scheduler in one process.

### Run Components Separately

```powershell
# Terminal 1: API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Worker
python -m worker.scheduler
```

### Test Health Endpoint

```powershell
curl http://localhost:8000/health
# → {"status":"ok","db":true}
```

---

## Push to GitHub (Private Repo)

```powershell
cd C:\Users\Administrator\Desktop\AMB\ambassador-assistant

git init
git add .
git commit -m "Initial commit — Ambassador Assistant"

# Create private repo on GitHub (via web UI or gh CLI)
gh repo create ambassador-assistant --private --source=. --push

# Or manually:
git remote add origin https://github.com/YOUR_USERNAME/ambassador-assistant.git
git branch -M main
git push -u origin main
```

---

## Deploy on Linux VPS

### Prerequisites

- Ubuntu 22.04+ / Debian 12+
- Docker + Docker Compose installed
- Git installed

### Steps

```bash
# 1. Clone from GitHub
git clone https://github.com/YOUR_USERNAME/ambassador-assistant.git
cd ambassador-assistant

# 2. Create production .env
cp .env.prod.example .env
nano .env   # fill in all values

# 3. Build and start
docker compose up -d --build

# 4. Check status
docker compose ps
docker logs amb-api
docker logs amb-worker

# 5. Verify health
curl http://localhost:8000/health
```

### Update Deployment

```bash
cd ambassador-assistant
git pull origin main
docker compose up -d --build
```

### HTTPS with Let's Encrypt (Optional)

```bash
# Install certbot
sudo apt install certbot

# Get certificate (stop nginx first)
docker compose stop nginx
sudo certbot certonly --standalone -d YOUR_DOMAIN

# Copy certs
mkdir -p certbot/conf
sudo cp -rL /etc/letsencrypt/* certbot/conf/

# Edit nginx.conf — uncomment the SSL server block, set YOUR_DOMAIN
# Then restart
docker compose up -d nginx
```

---

## Import X Data Archive

1. Download your archive from X → Settings → Your Account → Download an archive of your data.
2. Wait for the email, download the ZIP.
3. Run:

```bash
# Windows
python -m importer.x_archive --archive "C:\path\to\twitter-archive.zip"

# Linux (inside container)
docker compose exec api python -m importer.x_archive --archive /path/to/archive.zip
```

This parses all tweets, inserts them as `source='x_archive'`, classifies by project,
and schedules scoring jobs.

---

## PAD Flow Build Steps

Detailed step-by-step build instructions for Power Automate Desktop flows:

- **Discord Relay:** [docs/PAD_DISCORD_RELAY.md](docs/PAD_DISCORD_RELAY.md)
- **X Relay:** [docs/PAD_X_RELAY.md](docs/PAD_X_RELAY.md)

### OneDrive Provisioning Note

Power Automate Desktop requires OneDrive to save flows. Before creating flows:

1. Open <https://onedrive.live.com> in a browser on the Windows Server.
2. Sign in with a Microsoft account (can be the same one used for Windows).
3. Let OneDrive fully initialize (you'll see "Your OneDrive is ready").
4. Now PAD can save and sync flows.

---

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start`, `/help` | Show available commands |
| `/projects` | List all configured projects |
| `/project_add {json}` | Add or update a project |
| `/what <project>` | Show new signals for a project |
| `/best <project> [n]` | Top N posts by portfolio score |
| `/portfolio <project> [n]` | Detailed portfolio view with scores, summaries, metrics |
| `/feature <url> on\|off` | Toggle featured flag on a post |
| `/hide <url> on\|off` | Toggle hidden flag on a post |
| `/metrics <url> L R RP Q [V]` | Submit engagement metrics for a post |
| `/score_now <url>` | Force immediate LLM scoring |

### Adding a Project Example

```
/project_add {"name":"Solana","handles":["solaboratory","solana"],"keywords":["solana","sol","spl"],"priority":5,"discord_servers":["Solana"],"discord_channels":["announcements"]}
```

---

## Scoring Pipeline

1. Post is ingested via `/ingest/x` (or imported from archive).
2. A `score_jobs` row is created with `run_at = created_at + 48 hours`.
3. Worker polls every 5 minutes for due jobs.
4. If metrics are missing (manual mode), bot asks you via Telegram. Job enters `waiting_metrics`.
5. You provide metrics via `/metrics <url> ...`. Job transitions back to `scheduled`.
6. Worker picks up the job, calls OpenRouter for LLM scoring.
7. LLM returns structured JSON: summary, tags, quality, relevance, blurb, risk_framing, specificity.
8. Portfolio score is computed: `0.45*quality + 0.20*relevance + 0.25*engagement + 0.10*recency`.
9. Results stored in `llm_scores` and `portfolio_score` updated on the post.

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues and solutions.
