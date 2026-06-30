# Empyralis v2

v1 (~200k LOC) has been moved to `legacy/` as reference. It is not deleted — v1 stays as fallback until v2 ships to a real user.

v2 is being built in `server/`. See [MAN-22](https://linear.app/mansurao/issue/MAN-22) for status.

## Quick start (v2, week 1)

```bash
source .venv-v2/bin/activate
python -m server.cli "What files are in my home directory?"
```

Requires: Python 3.12+, `ANTHROPIC_API_KEY` env var, `anthropic` SDK.
