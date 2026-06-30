from __future__ import annotations

from . import flows_shared as _shared
from . import question_catalog as q
from .flows_run import guided_run_flow, run_once, run_tui_shell
from .flows_setup import configure_flow, onboard_flow, orion_setup_flow, preflight_flow

globals().update({name: value for name, value in vars(_shared).items() if not name.startswith("__")})

# Concrete launcher flow implementation (migrated from flows_shared.py).
def launcher_flow(ui: UI, api_url: str, runtime_key: str, workspace_id: str, engine: str, health: Dict[str, Any]) -> int:
    print_launcher_header(api_url, workspace_id, health)

    while True:
        action = ui.choose(q.Q_LAUNCHER_ACTION, LAUNCHER_ACTIONS, default_index=0, title=q.TITLE_LAUNCHER)
        choice_log(ui, q.Q_LAUNCHER_ACTION, action)

        if action.key == "orion_setup":
            return orion_setup_flow(ui, api_url, runtime_key, workspace_id, engine)

        if action.key == "preflight":
            return preflight_flow(ui, api_url, runtime_key, workspace_id, engine)

        if action.key == "guided":
            return guided_run_flow(ui, api_url, runtime_key, workspace_id, engine)

        if action.key == "quick":
            return run_once(
                ui=ui,
                api_url=api_url,
                runtime_key=runtime_key,
                workspace_id=workspace_id,
                engine=engine,
                goal=DEFAULT_QUICK_GOAL,
                pack=GENERAL_MODE,
                trust=TRUST_MODES[0],
                target=EXECUTION_TARGETS[0],
                pack_inputs=None,
                prompt_connectors=False,
            )

        if action.key == "goal":
            goal = ui.input(q.Q_RUN_GOAL, required=True, title=q.TITLE_RUN)
            trust = ui.choose(q.Q_RUN_TRUST_MODE, TRUST_MODES, default_index=0, title=q.TITLE_RUN)
            choice_log(ui, "Trust mode", trust)
            target = ui.choose(q.Q_RUN_EXECUTION_TARGET, EXECUTION_TARGETS, default_index=0, title=q.TITLE_RUN)
            choice_log(ui, "Execution target", target)
            pack_inputs = collect_pack_inputs(ui, "") if ui.confirm(q.Q_RUN_ADD_CONTEXT, default_yes=False) else None
            return run_once(
                ui=ui,
                api_url=api_url,
                runtime_key=runtime_key,
                workspace_id=workspace_id,
                engine=engine,
                goal=goal,
                pack=GENERAL_MODE,
                trust=trust,
                target=target,
                pack_inputs=pack_inputs,
                prompt_connectors=True,
            )

        if action.key == "specialist":
            pack = ui.choose(q.Q_RUN_SPECIALIST_TEMPLATE, SPECIALIST_PACKS, default_index=0, title=q.TITLE_RUN)
            choice_log(ui, "Specialist template", pack)
            goal = ui.input("Goal", default=f"Execute {pack.label} end-to-end with production quality.", required=True, title=q.TITLE_RUN)
            trust = ui.choose(q.Q_RUN_TRUST_MODE, TRUST_MODES, default_index=0, title=q.TITLE_RUN)
            choice_log(ui, "Trust mode", trust)
            target = ui.choose(q.Q_RUN_EXECUTION_TARGET, EXECUTION_TARGETS, default_index=0, title=q.TITLE_RUN)
            choice_log(ui, "Execution target", target)
            pack_inputs = collect_pack_inputs(ui, pack.key)
            return run_once(
                ui=ui,
                api_url=api_url,
                runtime_key=runtime_key,
                workspace_id=workspace_id,
                engine=engine,
                goal=goal,
                pack=pack,
                trust=trust,
                target=target,
                pack_inputs=pack_inputs,
                prompt_connectors=True,
            )

        if action.key == "onboard":
            return onboard_flow(ui, api_url, runtime_key, workspace_id, engine)

        if action.key == "configure":
            return configure_flow(ui, api_url, runtime_key, workspace_id, engine)

        if action.key == "connectors":
            choose_and_create_connectors(ui, api_url, runtime_key, workspace_id)
            continue

        if action.key == "doctor":
            print_doctor_summary(ui, api_url, runtime_key)
            continue

        if action.key == "tui":
            return run_tui_shell(ui, api_url, runtime_key, workspace_id, engine)

        if action.key == "exit":
            ui.note(muted("Exiting Empyralis terminal launcher."))
            return 0

__all__ = [
    "launcher_flow",
]
