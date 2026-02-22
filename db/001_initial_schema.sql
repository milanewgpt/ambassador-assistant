-- Ambassador Assistant: Initial Schema
-- Apply via: psql $DATABASE_URL -f db/001_initial_schema.sql
-- Or use: python db/apply_migrations.py

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- settings: singleton config row
-- ============================================================
CREATE TABLE IF NOT EXISTS settings (
    id              int PRIMARY KEY DEFAULT 1,
    timezone        text NOT NULL DEFAULT 'Asia/Jerusalem',
    telegram_bot_token   text,
    telegram_chat_id     text,
    openrouter_api_key   text,
    scoring_model        text DEFAULT 'openai/gpt-4o',
    main_x_handle        text,
    metrics_mode         text NOT NULL DEFAULT 'manual'
        CHECK (metrics_mode IN ('manual', 'auto')),
    ingest_shared_secret text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT settings_singleton CHECK (id = 1)
);

INSERT INTO settings (id) VALUES (1) ON CONFLICT DO NOTHING;

-- ============================================================
-- projects
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text UNIQUE NOT NULL,
    handles         text[] NOT NULL DEFAULT '{}',
    keywords        text[] NOT NULL DEFAULT '{}',
    priority        int NOT NULL DEFAULT 0,
    discord_servers text[] NOT NULL DEFAULT '{}',
    discord_channels text[] NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_name ON projects (name);

-- ============================================================
-- posts
-- ============================================================
CREATE TABLE IF NOT EXISTS posts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL CHECK (source IN ('x_relay', 'x_archive', 'manual')),
    url             text UNIQUE NOT NULL,
    created_at      timestamptz,
    text            text,
    project_id      uuid REFERENCES projects(id) ON DELETE SET NULL,
    featured        boolean NOT NULL DEFAULT false,
    hidden          boolean NOT NULL DEFAULT false,
    portfolio_score numeric,
    inserted_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_posts_project ON posts (project_id);
CREATE INDEX IF NOT EXISTS idx_posts_source ON posts (source);
CREATE INDEX IF NOT EXISTS idx_posts_portfolio ON posts (portfolio_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts (created_at DESC NULLS LAST);

-- ============================================================
-- signals (Discord relay)
-- ============================================================
CREATE TABLE IF NOT EXISTS signals (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text NOT NULL DEFAULT 'discord_relay',
    project_id      uuid REFERENCES projects(id) ON DELETE SET NULL,
    server          text,
    channel         text,
    preview         text,
    message_link    text UNIQUE NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    status          text NOT NULL DEFAULT 'new'
);

CREATE INDEX IF NOT EXISTS idx_signals_project ON signals (project_id);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals (status);

-- ============================================================
-- metrics_snapshots
-- ============================================================
CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id         uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    captured_at     timestamptz NOT NULL DEFAULT now(),
    likes           int,
    replies         int,
    reposts         int,
    quotes          int,
    views           int
);

CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics_snapshots (post_id);

-- ============================================================
-- llm_scores
-- ============================================================
CREATE TABLE IF NOT EXISTS llm_scores (
    post_id         uuid PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
    model           text NOT NULL,
    scored_at       timestamptz NOT NULL DEFAULT now(),
    summary_en      text,
    tags            text[] NOT NULL DEFAULT '{}',
    quality         numeric,
    relevance       numeric,
    portfolio_blurb_en text,
    risk_framing    numeric,
    specificity     numeric
);

-- ============================================================
-- score_jobs
-- ============================================================
CREATE TABLE IF NOT EXISTS score_jobs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id         uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    run_at          timestamptz NOT NULL,
    status          text NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'waiting_metrics', 'running', 'done', 'failed')),
    attempts        int NOT NULL DEFAULT 0,
    last_error      text
);

CREATE INDEX IF NOT EXISTS idx_score_jobs_due
    ON score_jobs (run_at) WHERE status IN ('scheduled', 'waiting_metrics');
CREATE INDEX IF NOT EXISTS idx_score_jobs_post ON score_jobs (post_id);
