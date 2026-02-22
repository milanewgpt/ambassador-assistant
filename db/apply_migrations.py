"""
Apply SQL migrations in order against the Supabase Postgres database.
Usage:
    python db/apply_migrations.py
Reads DATABASE_URL from .env or environment.
"""

import os
import sys
import glob
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL is not set. Check your .env file.")
    sys.exit(1)

MIGRATIONS_DIR = Path(__file__).parent


def get_applied_migrations(cur) -> set:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            filename text PRIMARY KEY,
            applied_at timestamptz DEFAULT now()
        );
    """)
    cur.execute("SELECT filename FROM _migrations ORDER BY filename;")
    return {row[0] for row in cur.fetchall()}


def apply():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        applied = get_applied_migrations(cur)
        conn.commit()

        sql_files = sorted(glob.glob(str(MIGRATIONS_DIR / "*.sql")))
        if not sql_files:
            print("No migration files found.")
            return

        for fpath in sql_files:
            fname = os.path.basename(fpath)
            if fname in applied:
                print(f"  SKIP  {fname} (already applied)")
                continue

            print(f"  APPLY {fname} ...")
            with open(fpath, "r", encoding="utf-8") as f:
                sql = f.read()

            cur.execute(sql)
            cur.execute(
                "INSERT INTO _migrations (filename) VALUES (%s);", (fname,)
            )
            conn.commit()
            print(f"  OK    {fname}")

        print("\nAll migrations applied.")

    except Exception as e:
        conn.rollback()
        print(f"ERROR during migration: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    apply()
