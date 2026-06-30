# Empyralis v2

v1 (~200k LOC) has been moved to `legacy/` as reference. It is not deleted — v1 stays as fallback until v2 ships to a real user.

v2 is being built in `server/`. See [MAN-22](https://linear.app/mansurao/issue/MAN-22) for status.

## Quick start (v2, week 4)

### Web chat

```bash
# Terminal 1: API server
source .venv-v2/bin/activate
python -m server.api          # port 8000

# Terminal 2: Frontend
cd frontend/v2 && npm run dev # port 3000

# Terminal 3: Telegram bot (optional)
source .venv-v2/bin/activate
python -m server.bot
```

Open http://localhost:3000 → paste API key → chat with Sage.

### CLI

```bash
source .venv-v2/bin/activate
python -m server.cli "What files are in my home directory?"
```

### Connect Google Workspace

```bash
source .venv-v2/bin/activate
python -m server.cli connect google_workspace
```

Requires: Python 3.12+, Node 20+, `ANTHROPIC_API_KEY` env var, `anthropic` SDK.
