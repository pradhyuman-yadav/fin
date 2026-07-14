# fin

Project scaffold built on the **WAT framework** (Workflows, Agents, Tools) — probabilistic AI for reasoning, deterministic code for execution.

## Architecture

- **Workflows** (`workflows/`) — Markdown SOPs. Each defines objective, inputs, tools to use, outputs, edge cases.
- **Agents** — the orchestrator. Reads a workflow, runs tools in sequence, handles failures.
- **Tools** (`tools/`) — Python scripts doing the actual work (API calls, transforms, file/db ops).

## Layout

```
.tmp/         # Temporary files. Disposable, regenerated as needed.
tools/        # Python scripts (deterministic execution)
workflows/    # Markdown SOPs
.env          # API keys / secrets (NEVER commit — gitignored)
```

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |  Unix: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys
```

## Run the stack

```bash
cp .env.example .env    # add ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY
docker compose up -d --build
```

Microservices — one concern each, own container, own logs, own healthcheck
(all `restart: unless-stopped`):

| Service | Container | Role | Writes |
|---------|-----------|------|--------|
| `timescaledb` | `fin_timescaledb` | TimescaleDB, 365-day retention | — |
| `bars_stock` | `fin_bars_stock` | real-time WebSocket bar stream (stocks) | `market_ohlcv` |
| `bars_crypto` | `fin_bars_crypto` | real-time WebSocket bar stream (crypto) | `market_ohlcv` |
| `signals` | `fin_signals` | technical indicators → BUY/SELL/HOLD | `market_signals` |
| `calendar` | `fin_calendar` | market clock + trading calendar | `market_clock`, `market_calendar` |
| `corpactions` | `fin_corpactions` | dividends/splits (daily) | `corporate_actions` |
| `news` | `fin_news` | latest headlines | `news` |
| `dashboard` | `fin_dashboard` | monitoring UI at http://localhost:8000 | — |

Each service heartbeats into `service_health`; the dashboard shows a live
**Services** strip (status + last-run per service), and Docker healthchecks
mark containers healthy/unhealthy in Portainer.

## Deploy to Portainer (central DB)

Two stacks, deployed from this repo (Git), sharing an external `fin_net` network:

1. `deploy/timescale.portainer.yml` — the central TimescaleDB (built image, schema baked in).
2. `deploy/app.portainer.yml` — the microservices above, pointing at `fin_timescaledb`.

Set stack env vars in Portainer: `POSTGRES_PASSWORD`, `ALPACA_API_KEY_ID`,
`ALPACA_API_SECRET_KEY`. No `.env` file and no n8n required.

Watch: `docker compose ps`, `docker compose logs -f poller`.

Change symbols in one place — the `watchlist` table in the DB (source of truth
for the poller and the n8n workflows):

```sql
-- add
INSERT INTO watchlist (symbol, asset_type) VALUES ('COIN','stock');
-- pause without deleting
UPDATE watchlist SET active = false WHERE symbol = 'DOGE/USD';
```

The poller picks up changes on its next cycle (n8n on its next run).
`config/watchlist.txt` is a fallback used only if the table is unavailable.

## Usage

1. Pick a workflow in `workflows/`.
2. Run the tools it references from `tools/`.
3. Outputs go to cloud services; intermediates land in `.tmp/`.

## License

MIT — see [LICENSE](LICENSE).
