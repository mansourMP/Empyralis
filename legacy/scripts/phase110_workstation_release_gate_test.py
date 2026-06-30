#!/usr/bin/env python3

from __future__ import annotations

import runpy
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE_SCRIPTS = [
    "scripts/phase97_desktop_transport_truth_test.py",
    "scripts/phase98_workstation_kernel_isolation_test.py",
    "scripts/phase99_workstation_shell_layout_test.py",
    "scripts/phase100_workstation_client_contract_test.py",
    "scripts/phase101_workspace_switcher_rail_test.py",
    "scripts/phase102_agent_roster_pane_test.py",
    "scripts/phase103_workstation_chat_pane_test.py",
    "scripts/phase104_dynamic_stage_pane_test.py",
    "scripts/phase105_workstation_stream_manager_test.py",
    "scripts/phase106_workstation_desktop_bridge_test.py",
    "scripts/phase107_workstation_amnesia_stress_test.py",
    "scripts/phase108_workstation_static_audit.py",
    "scripts/phase109_workstation_golden_path_test.py",
]


def main() -> None:
    for relative_path in PHASE_SCRIPTS:
        runpy.run_path(str(REPO_ROOT / relative_path), run_name="__main__")
    print("phase110 workstation release gate ok")


if __name__ == "__main__":
    main()
