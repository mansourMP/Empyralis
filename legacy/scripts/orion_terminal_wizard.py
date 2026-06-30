#!/usr/bin/env python3
"""Empyralis Terminal Command Center entrypoint."""

from __future__ import annotations

from orion_terminal.app import main
from orion_terminal.errors import OrionTerminalError, RuntimeUnavailable


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeUnavailable as exc:
        print(f"\n[error] Runtime unavailable: {exc}")
        print("Restart local stack: bash scripts/start_empyralis_local_stack.sh")
        raise SystemExit(2)
    except OrionTerminalError as exc:
        print(f"\n[error] {exc}")
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"\n[error] Unexpected failure: {exc}")
        raise SystemExit(1)
