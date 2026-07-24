"""Memory subsystem shared types and constants.

Extracted to break HIGH import cycles:
  - memory_service → workspace_context_memory_adapter → unified_memory_service → memory_service
  - hybrid_policy_service → unified_memory_service → memory_service

Module MUST NOT import from memory_service, unified_memory_service,
workspace_context_memory_adapter, or hybrid_policy_service.
It is a leaf — only downstream consumers import it.
"""

from __future__ import annotations

from typing import Any, Dict

# ── Memory runtime defaults ──────────────────────────────────────────────
# These are the defaults set by _RuntimeMemoryCompat in memory_service.py.
# Extracted so conversation_memory_policy can use them without importing
# memory_service (which breaks the cycle).

ORION_MEMORY_READ_K_DEFAULT: int = 5
ORION_MEMORY_RETENTION_DAYS_DEFAULT: int = 30
ORION_MEMORY_MAX_TEXT_CHARS_DEFAULT: int = 6000

# ── Memory layer specifications ──────────────────────────────────────────
# Extracted from unified_memory_service.py so hybrid_policy_service can
# reference layer specs without importing unified_memory_service.

MEMORY_LAYER_SPECS: dict[str, dict[str, Any]] = {
    "profile_memory": {
        "ownership": "Workspace owner profile and durable user preferences.",
        "storage_location": (
            "Workspace MEMORY.md index plus the memory/files/profile.md topic file "
            "(see workspace_context.py; USER.md/IDENTITY.md/SOUL.md were removed from "
            "the root-file taxonomy 2026-07-23) and future profile-specific overlays."
        ),
        "retention_rules": "Durable until the owner edits or deletes the stored profile notes.",
        "sync_rules": {
            "default": "local_only",
            "may_sync": True,
            "requires_explicit_opt_in": True,
            "notes": "Personal profile facts stay local by default and may sync only through explicit future profile-sync policy.",
        },
        "indexing_retrieval": "Context markdown is available for direct read and summary synthesis; it is not broadly searchable by specialists.",
        "privacy_level": "personal_profile",
        "readers": "Sage by default. Specialists only via explicit shared excerpts or install-specific profile notes.",
    },
    "episodic_memory": {
        "ownership": "Recent conversations, transcript summaries, and short-horizon behavioral history.",
        "storage_location": "Local transcript jsonl files plus daily logs in the workspace/install memory namespace.",
        "retention_rules": "Rolling recent context retained locally until pruned or archived by policy.",
        "sync_rules": {
            "default": "local_only",
            "may_sync": True,
            "requires_explicit_opt_in": True,
            "notes": "Only compressed summaries should ever sync, never raw transcripts by default.",
        },
        "indexing_retrieval": "Recent-first transcript and daily-log summaries with bounded excerpts.",
        "privacy_level": "behavioral_history",
        "readers": "Sage by default. Specialists can read only their own install-scoped episodic memory.",
    },
    "app_event_history": {
        "ownership": "Structured mobile/app/module event publishers.",
        "storage_location": "Durable control-plane personal_context_events feed.",
        "retention_rules": "Durable event feed until archived or pruned by control-plane retention policy.",
        "sync_rules": {
            "default": "cloud_synced",
            "may_sync": True,
            "requires_explicit_opt_in": False,
            "notes": "Structured events are the default cloud-safe context layer.",
        },
        "indexing_retrieval": "Structured filters by type, source app, priority, install scope, and seen state.",
        "privacy_level": "structured_event_feed",
        "readers": "Sage by default. Specialists only when the event scope explicitly includes them.",
    },
    "shared_operational_board": {
        "ownership": "Workspace-visible operational instructions, SOPs, handoffs, and approved artifact references.",
        "storage_location": "Append-only shared operational board records managed by shared_operational_board_service.",
        "retention_rules": "Durable and version-aware until superseded or explicitly removed by future board policy.",
        "sync_rules": {
            "default": "workspace_shared",
            "may_sync": True,
            "requires_explicit_opt_in": False,
            "notes": "Only explicitly published operational content belongs here; this is not a raw private-memory sync path.",
        },
        "indexing_retrieval": "Board entries are listed by revision history and published operational state.",
        "privacy_level": "workspace_shared_operational",
        "readers": "Sage and permitted specialists read published entries. Proposals require elevated board permissions to inspect.",
    },
    "notes_documents_retrieval": {
        "ownership": "Workspace notes, curated memory, uploaded knowledge, and retrieval snippets.",
        "storage_location": "Local memory notebook files plus the workspace knowledge directory.",
        "retention_rules": "Durable until files are edited or removed.",
        "sync_rules": {
            "default": "local_only",
            "may_sync": True,
            "requires_explicit_opt_in": True,
            "notes": "Documents and uploads remain local unless a future sync policy explicitly publishes summaries or selected files.",
        },
        "indexing_retrieval": "File listing plus bounded text snippet retrieval for notebook and knowledge sources.",
        "privacy_level": "document_knowledge",
        "readers": "Sage by default. Specialists get only their install namespace or explicitly shared document context.",
    },
    "specialist_scoped_memory": {
        "ownership": "Per-install specialist namespace.",
        "storage_location": "Install-specific context files, memory facts, notebook docs, daily logs, and transcript history.",
        "retention_rules": "Durable within the install namespace until deleted or pruned.",
        "sync_rules": {
            "default": "local_only",
            "may_sync": True,
            "requires_explicit_opt_in": True,
            "notes": "Install-private state never becomes shared memory without an explicit brokered share path.",
        },
        "indexing_retrieval": "Install-scoped snapshot, recent log summaries, and document search restricted to the install namespace.",
        "privacy_level": "specialist_private",
        "readers": "The owning specialist install and Sage when explicitly brokered. Other specialists are denied by default.",
    },
    "local_private_memory": {
        "ownership": "Device-local state and private workspace memory that should not leave the host by default.",
        "storage_location": "Local workspace context files, notebook docs, knowledge files, logs, and transcripts on the attached runtime.",
        "retention_rules": "Local until the user deletes it or a local retention policy prunes it.",
        "sync_rules": {
            "default": "never",
            "may_sync": False,
            "requires_explicit_opt_in": True,
            "notes": "This layer is intentionally excluded from cloud sync by default.",
        },
        "indexing_retrieval": "Local-only retrieval. Accessible through the local runtime, not through broad cloud sync.",
        "privacy_level": "local_private",
        "readers": "Sage through an attached local runtime only. Specialists do not inherit this layer by default.",
    },
    "cloud_synced_memory": {
        "ownership": "Cloud-safe structured summaries and explicitly synced feeds.",
        "storage_location": "Control-plane records and future cloud-safe summary stores.",
        "retention_rules": "Durable under control-plane retention rules.",
        "sync_rules": {
            "default": "cloud_synced",
            "may_sync": True,
            "requires_explicit_opt_in": False,
            "notes": "Only cloud-safe, policy-approved memory classes belong here.",
        },
        "indexing_retrieval": "Structured metadata and summary retrieval on synced payloads only.",
        "privacy_level": "cloud_safe_structured",
        "readers": "Sage by default. Specialists only when the synced memory item is explicitly scoped to them.",
    },
}
