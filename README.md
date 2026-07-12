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

## Usage

1. Pick a workflow in `workflows/`.
2. Run the tools it references from `tools/`.
3. Outputs go to cloud services; intermediates land in `.tmp/`.

## License

MIT — see [LICENSE](LICENSE).
