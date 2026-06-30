#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server_modules.scale_harness import (
    ControlPlaneScaleConfig,
    FleetScaleConfig,
    run_scale_harness,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Empyralis Phase 15 scale harness.")
    parser.add_argument("--install-count", type=int, default=10_000, help="Specialist installs to seed for the control-plane benchmark.")
    parser.add_argument("--list-iterations", type=int, default=5, help="How many list queries to time after seeding installs.")
    parser.add_argument("--total-runs", type=int, default=1_500, help="Total runtime jobs to simulate.")
    parser.add_argument("--active-threads", type=int, default=1_200, help="Distinct customer conversations represented in the runtime simulation.")
    parser.add_argument("--workers", type=int, default=180, help="Fleet worker count for the runtime simulation.")
    parser.add_argument("--seed", type=int, default=20260410, help="Deterministic random seed for the runtime simulation.")
    parser.add_argument("--keep-control-plane-fixtures", action="store_true", help="Keep the seeded install rows instead of deleting them after the benchmark.")
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    payload = await run_scale_harness(
        control_plane_config=ControlPlaneScaleConfig(
            install_count=max(1, int(args.install_count or 0)),
            list_iterations=max(1, int(args.list_iterations or 0)),
            cleanup_after_run=not bool(args.keep_control_plane_fixtures),
        ),
        fleet_config=FleetScaleConfig(
            seed=int(args.seed),
            total_runs=max(1, int(args.total_runs or 0)),
            active_threads=max(1, int(args.active_threads or 0)),
            worker_count=max(1, int(args.workers or 0)),
        ),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
