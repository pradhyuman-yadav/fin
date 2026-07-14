-- Per-service heartbeat / health. Each microservice upserts its row each cycle.
CREATE TABLE IF NOT EXISTS service_health (
    service    TEXT PRIMARY KEY,
    status     TEXT,
    detail     TEXT,
    last_run   TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
