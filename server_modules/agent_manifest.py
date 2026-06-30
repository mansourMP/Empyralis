from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AgentManifestArchetype = Literal["support_specialist", "task_automator", "intelligence_researcher", "master_os"]
AgentManifestScope = Literal["global_master", "specialist"]
AgentManifestApprovalMode = Literal["system", "guarded", "strict"]
AgentRuntimeMode = Literal["hosted_secure", "local_secure", "privileged_device"]


class AgentManifestSkillBinding(BaseModel):
    id: str
    enabled: bool = True


class AgentManifestChannels(BaseModel):
    web_chat: bool = True
    email: bool = False
    phone: bool = False
    whatsapp: bool = False
    telegram: bool = False


class AgentManifestBible(BaseModel):
    mission: str = ""
    hard_context: str = ""
    operational_policy: str = ""
    core_responsibilities: str = ""
    guardrails: str = ""
    escalation_triggers: str = ""


class AgentManifestIdentity(BaseModel):
    name: str
    role: str
    archetype: AgentManifestArchetype = "support_specialist"
    summary: str = ""
    owner_mode_enabled: bool = True
    customer_mode_enabled: bool = True


class AgentManifestRoleProfile(BaseModel):
    job_to_be_done: str = ""
    success_definition: str = ""
    escalation_owner: str = "Workspace owner"


class AgentManifestVoiceProfile(BaseModel):
    tone: str = ""
    response_style: str = ""
    service_boundaries: str = ""


class AgentManifestConnectors(BaseModel):
    requested: list[str] = Field(default_factory=list)
    bound: list[str] = Field(default_factory=list)


class AgentManifestRuntime(BaseModel):
    mode: AgentRuntimeMode = "hosted_secure"
    profile_id: str | None = None


class AgentManifestPolicy(BaseModel):
    reflection_enabled: bool = True
    approval_mode: AgentManifestApprovalMode = "guarded"


class AgentManifestBlueprint(BaseModel):
    source: Literal["system", "forge", "imported_blueprint"] = "forge"
    id: str | None = None
    title: str | None = None


class AgentManifest(BaseModel):
    version: Literal["empyralis.agent-manifest.v1"] = "empyralis.agent-manifest.v1"
    manifest_id: str
    engine: Literal["universal_operator"] = "universal_operator"
    scope: AgentManifestScope = "specialist"
    identity: AgentManifestIdentity
    role: AgentManifestRoleProfile = Field(default_factory=AgentManifestRoleProfile)
    voice: AgentManifestVoiceProfile = Field(default_factory=AgentManifestVoiceProfile)
    bible: AgentManifestBible = Field(default_factory=AgentManifestBible)
    skills: list[AgentManifestSkillBinding] = Field(default_factory=list)
    connectors: AgentManifestConnectors = Field(default_factory=AgentManifestConnectors)
    channels: AgentManifestChannels = Field(default_factory=AgentManifestChannels)
    runtime: AgentManifestRuntime = Field(default_factory=AgentManifestRuntime)
    policy: AgentManifestPolicy = Field(default_factory=AgentManifestPolicy)
    blueprint: AgentManifestBlueprint = Field(default_factory=AgentManifestBlueprint)


def manifest_has_skill(manifest: AgentManifest, skill_id: str) -> bool:
    normalized = str(skill_id or "").strip()
    return any(binding.enabled and binding.id == normalized for binding in manifest.skills)


def manifest_skill_ids(manifest: AgentManifest) -> list[str]:
    return [binding.id for binding in manifest.skills if binding.enabled]


def render_manifest_bible(manifest: AgentManifest) -> str:
    return "\n".join([
        "Mission",
        manifest.bible.mission,
        "",
        "Hard Context",
        manifest.bible.hard_context,
        "",
        "Operational Policy",
        manifest.bible.operational_policy,
        "",
        "Core Responsibilities",
        manifest.bible.core_responsibilities,
        "",
        "Guardrails",
        manifest.bible.guardrails,
        "",
        "Escalation Triggers",
        manifest.bible.escalation_triggers,
    ])


SAGE_GLOBAL_MANIFEST = AgentManifest(
    manifest_id="sage-global-manifest",
    scope="global_master",
    identity=AgentManifestIdentity(
        name="Sage",
        role="Master Operating System",
        archetype="master_os",
        summary="Global orchestrator with cross-system context and broad skill access.",
        owner_mode_enabled=False,
        customer_mode_enabled=False,
    ),
    role=AgentManifestRoleProfile(
        job_to_be_done="Act as the master operator across planning, delegation, execution, and system-wide supervision.",
        success_definition="Keep the workspace legible, route work well, and exercise sound judgment on risky actions.",
        escalation_owner="Workspace owner",
    ),
    voice=AgentManifestVoiceProfile(
        tone="Calm, supervisory, and precise.",
        response_style="Lead with the answer, then show the next action or reasoning path.",
        service_boundaries="Be transparent about policy tradeoffs and structural limits. Never pretend a blocked action can be approved.",
    ),
    bible=AgentManifestBible(
        mission="Operate as the master relationship for planning, delegation, execution, and system-wide awareness.",
        hard_context="Sage has cross-system context and is the only visible omniscient operator surface.",
        operational_policy=(
            "Route work to specialists when needed. Use your own judgment on risky or irreversible actions — "
            "there is no approval system. When the user's intent is clear and the action is well-scoped, act. "
            "When scope is ambiguous, ask for clarification in conversation. "
            "When structurally blocked (tenant isolation, budget cap, blast-radius), explain the limit. "
            "When consequences are serious (money, mass-delete, public posts), confirm specifics before acting."
        ),
        core_responsibilities="Coordinate agents, supervise runs, summarize the system, exercise real-time judgment, and keep work legible.",
        guardrails=(
            "You have NO approval system. Act on your own reasoning, like a human worker would. "
            "Structural safety (tenant isolation, budget caps, blast-radius limits, rate limits) is your backstop — "
            "it will refuse actions that breach hard limits. Your job is to use judgment for everything else. "
            "When unsure about intent or scope: ask in conversation. Never say 'this requires approval' — "
            "either act, ask for clarification, or explain the structural limit."
        ),
        escalation_triggers="ambiguous scope on irreversible actions\ncross-tenant boundary concerns\nstructurally blocked actions",
    ),
    skills=[
        AgentManifestSkillBinding(id="email-access"),
        AgentManifestSkillBinding(id="web-search"),
        AgentManifestSkillBinding(id="browser"),
        AgentManifestSkillBinding(id="calendar-access"),
        AgentManifestSkillBinding(id="task-runner"),
        AgentManifestSkillBinding(id="inventory-tool"),
        AgentManifestSkillBinding(id="crm-notes"),
    ],
    connectors=AgentManifestConnectors(
        requested=["email", "web", "calendar", "inventory", "crm", "automation"],
        bound=[],
    ),
    runtime=AgentManifestRuntime(mode="hosted_secure"),
    blueprint=AgentManifestBlueprint(source="system", title="Sage Master OS"),
    policy=AgentManifestPolicy(reflection_enabled=True, approval_mode="none"),
)
