"""TimescaleDB connection helper for the WAT framework.

Reads DATABASE_URL from .env. Provides a connection context manager and a
health check. Run directly to verify the DB is up and TimescaleDB is loaded:

    python tools/db.py
"""

import os
import sys
from contextlib import contextmanager

import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://fin:fin_dev_pw@localhost:5432/fin"
)


@contextmanager
def connect():
    """Yield a psycopg connection; commits on exit, rolls back on error."""
    conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def health() -> dict:
    """Return server + TimescaleDB version and hypertable count."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT version();")
        pg = cur.fetchone()[0]
        cur.execute("SELECT extversion FROM pg_extension WHERE extname='timescaledb';")
        row = cur.fetchone()
        ts = row[0] if row else None
        cur.execute("SELECT count(*) FROM timescaledb_information.hypertables;")
        hypertables = cur.fetchone()[0]
    return {"postgres": pg, "timescaledb": ts, "hypertables": hypertables}


if __name__ == "__main__":
    try:
        info = health()
    except Exception as exc:  # noqa: BLE001
        print(f"DB health check FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"postgres:     {info['postgres']}")
    print(f"timescaledb:  {info['timescaledb']}")
    print(f"hypertables:  {info['hypertables']}")
