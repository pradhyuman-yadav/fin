"""Service heartbeat: file-based for Docker healthchecks, DB-based for the
dashboard's central health view.

Each service calls beat() at the end of every successful cycle. The Docker
HEALTHCHECK runs `python tools/heartbeat.py check <max_age_seconds>` which
exits non-zero if the heartbeat file is stale.

HEARTBEAT_MAX_AGE (env, seconds) tells the dashboard how stale this service's
heartbeat may get before it should be flagged — set it to roughly 2x the
service's cycle interval. Without it the dashboard assumes 600s.
"""

import os
import sys
import time

from db import connect

HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/heartbeat")
SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
MAX_AGE_S = float(os.getenv("HEARTBEAT_MAX_AGE", "600"))

_UPSERT = """
INSERT INTO service_health (service, status, detail, max_age_s, last_run, updated_at)
VALUES (%s, %s, %s, %s, now(), now())
ON CONFLICT (service) DO UPDATE SET
  status = EXCLUDED.status, detail = EXCLUDED.detail,
  max_age_s = EXCLUDED.max_age_s, last_run = now(), updated_at = now();
"""

_ensured = False


def _ensure():
    global _ensured
    if _ensured:
        return
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS service_health ("
            "service TEXT PRIMARY KEY, status TEXT, detail TEXT, "
            "max_age_s DOUBLE PRECISION, "
            "last_run TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());"
            "ALTER TABLE service_health ADD COLUMN IF NOT EXISTS max_age_s DOUBLE PRECISION;"
        )
    _ensured = True


def beat(status="ok", detail=None, service=None):
    """Record a heartbeat. Never raises — a monitoring failure must not kill
    the service loop."""
    name = service or SERVICE_NAME
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(int(time.time())))
    except OSError:
        pass
    try:
        _ensure()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(_UPSERT, (name, status, detail, MAX_AGE_S))
    except Exception as exc:  # noqa: BLE001
        print(f"heartbeat db write failed: {exc}", flush=True)


def _check(max_age):
    try:
        age = time.time() - os.path.getmtime(HEARTBEAT_FILE)
    except OSError:
        sys.exit(1)
    sys.exit(0 if age <= max_age else 1)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        _check(float(sys.argv[2]))
    sys.exit("usage: python tools/heartbeat.py check <max_age_seconds>")
