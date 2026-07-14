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

Starts three services (all `restart: unless-stopped`):

| Service | Container | Role |
|---------|-----------|------|
| `timescaledb` | `fin_timescaledb` | TimescaleDB, port 5432, 365-day retention |
| `poller` | `fin_poller` | polls Alpaca latest 1-min bars → `market_ohlcv` |
| `signals` | `fin_signals` | computes indicators → `market_signals` (BUY/SELL/HOLD) |
| `dashboard` | `fin_dashboard` | monitoring UI at http://localhost:8000 |

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
