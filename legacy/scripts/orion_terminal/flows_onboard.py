from __future__ import annotations

from . import flows_shared as _shared
from . import question_catalog as q
from .flows_orion_setup import orion_setup_flow

globals().update({name: value for name, value in vars(_shared).items() if not name.startswith("__")})

def onboard_flow(ui: UI, api_url: str, api_key: str, workspace_id: str, engine: str) -> int:
    wizard_session = WizardSession(flow="onboard", workspace_id=workspace_id)
    wizard = WizardPrompter(ui, wizard_session)
    if FULL_OUTPUT:
        wizard.intro(strong("Onboard"))

    security_msg = (
        "Security notice — review before continuing.\n\n"
        "Empyralis can read files and run actions if tools are enabled.\n"
        "A bad prompt can push unsafe behavior.\n\n"
        "If you are not comfortable with access control and secrets handling,\n"
        "pause here and review the docs before enabling broad tool access.\n\n"
        "Recommended baseline:\n"
        "- Pairing/allowlists + mention gating.\n"
        "- Sandbox + least-privilege tools.\n"
        "- Keep secrets out of the agent reachable filesystem.\n"
        "- Use strong models for untrusted inboxes.\n\n"
        "Run regularly:\n"
        "- orion doctor\n"
        "- orion --mode preflight\n\n"
        f"Must read: {ORION_SECURITY_DOC_URL}\n"
        f"Setup guide: {ORION_SETUP_DOC_URL}"
    )
    ui.message("Empyralis Security", security_msg)
    wizard_session.record("note", "Empyralis Security", "shown", title="Onboard")
    if not wizard.confirm(
        q.Q_ONBOARD_CONFIRM_SECURITY,
        default_yes=False,
        title=q.TITLE_SECURITY,
    ):
        ui.note(muted("Onboarding cancelled."))
        wizard_session.cancel("security_not_acknowledged")
        persist_wizard_session(wizard_session)
        return 0

    wizard_session.complete()
    persist_wizard_session(wizard_session)
    # Configure-first full onboarding parity lives in orion_setup_flow.
    return orion_setup_flow(ui, api_url, api_key, workspace_id, engine)



__all__ = [
    "onboard_flow",
]
