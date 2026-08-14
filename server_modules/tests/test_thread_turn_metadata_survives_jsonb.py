"""A JSONB column read through asyncpg arrives as TEXT, and the API was
silently turning every turn's metadata into `{}`.

Found 2026-08-15 by looking at the agent Work tab, which said "detailed step
tracking isn't available for this conversation" on every conversation while
`agent_traces` and `agent_trace_events` were being written perfectly. The tab
resolves a turn's `metadata.trace_id`; the endpoint it reads returned `{}`.

Proven at the boundary rather than argued:

    python type of metadata from asyncpg: str
    repr head: '{"model": "deepseek-v4-pro", ... "trace_id": "trace_9954..."'
    _coerce_dict(...) -> {}

The tell was inside the same function: `approvals`/`interventions` go through
`_coerce_list_payload`, which parses a JSON string, and survived. `metadata`
went through `_coerce_dict`, which does not. Whoever wrote it already knew
JSONB arrives as text and handled it for lists only.

These tests are deliberately at the NORMALIZER, not at HTTP: the normalizer is
the single point every thread/turn response crosses, and a behavioural test
through the route would need a live Postgres to reproduce the str at all —
which is exactly why nothing caught this. `test_metadata_is_not_a_dict_in_the_first_place`
pins the premise so a future jsonb codec on the pool (which would make this
whole class disappear) fails loudly here instead of leaving a test that
quietly asserts nothing.
"""

import json

from server_modules import runtime_runs_api


_REAL_TURN_METADATA = {
    "model": "deepseek-v4-pro",
    "channel": "ChannelOrigin.WEB",
    "trace_id": "trace_995498030cd54c12a3270c09",
    "request_id": "fleet_agent_chat-1786726496835-d0ttft",
    "effective_model": "deepseek-v4-pro",
    "requested_model": "deepseek-v4-pro",
}


def _turn_row(metadata):
    """Shaped like a `dict(asyncpg.Record)` from `SELECT * FROM agent_turns`."""
    return {
        "id": "turn-1",
        "tenant_id": "tenant-1",
        "workspace_id": "ws-1",
        "thread_id": "thread_agent_ainstall_1",
        "role": "assistant",
        "status": "complete",
        "content": "Done.",
        "metadata": metadata,
    }


def test_metadata_is_not_a_dict_in_the_first_place():
    """The premise. asyncpg gives TEXT for JSONB with no codec registered, so
    a normalizer that only accepts `dict` accepts nothing real."""
    as_text = json.dumps(_REAL_TURN_METADATA)
    assert isinstance(as_text, str)
    # control_plane_repository._coerce_dict is what used to be applied here.
    from server_modules.control_plane_repository import _coerce_dict

    assert _coerce_dict(as_text) == {}, (
        "if this ever stops being {}, a jsonb codec was registered on the pool "
        "and this whole class of bug is gone — update these tests rather than "
        "deleting them"
    )


def test_turn_metadata_survives_when_it_arrives_as_json_text():
    record = runtime_runs_api.normalize_thread_turn_record(
        _turn_row(json.dumps(_REAL_TURN_METADATA))
    )
    assert record["metadata"] == _REAL_TURN_METADATA


def test_the_trace_id_the_work_tab_reads_is_the_one_that_survives():
    """The specific field whose loss made the transparency surface blank."""
    record = runtime_runs_api.normalize_thread_turn_record(
        _turn_row(json.dumps(_REAL_TURN_METADATA))
    )
    assert record["metadata"].get("trace_id") == "trace_995498030cd54c12a3270c09"


def test_attachments_survive_too_since_they_are_derived_from_that_dict():
    """`attachments` is read out of the same metadata one line below, so it was
    being dropped by the same emptying — a second casualty, not a separate bug."""
    metadata = dict(_REAL_TURN_METADATA)
    metadata["attachments"] = [{"name": "notes.txt", "size": 12}]
    record = runtime_runs_api.normalize_thread_turn_record(
        _turn_row(json.dumps(metadata))
    )
    assert record["attachments"] == [{"name": "notes.txt", "size": 12}]


def test_a_dict_still_passes_through_unchanged():
    """The local/SQLite fallback path hands a real dict; it must be untouched."""
    record = runtime_runs_api.normalize_thread_turn_record(
        _turn_row(dict(_REAL_TURN_METADATA))
    )
    assert record["metadata"] == _REAL_TURN_METADATA


def test_garbage_still_degrades_to_empty_rather_than_raising():
    for junk in ("", "   ", "not json", "[1,2,3]", None, 17):
        record = runtime_runs_api.normalize_thread_turn_record(_turn_row(junk))
        assert record["metadata"] == {}, junk


def test_thread_metadata_survives_as_well():
    """The thread's own metadata had the identical defect one function up."""
    record = runtime_runs_api.normalize_thread_record(
        {
            "id": "thread_agent_ainstall_1",
            "tenant_id": "tenant-1",
            "workspace_id": "ws-1",
            "title": "A thread",
            "status": "active",
            "metadata": json.dumps({"source": "agent_turn", "session_id": "s-1"}),
            "turns": [],
        }
    )
    assert record["metadata"] == {"source": "agent_turn", "session_id": "s-1"}


def test_list_fields_were_never_broken_and_still_are_not():
    """Guards the asymmetry that gave the bug away: lists already parsed JSON
    text. If someone 'simplifies' both onto one helper, this keeps them honest."""
    row = _turn_row(json.dumps(_REAL_TURN_METADATA))
    row["approvals"] = json.dumps([{"id": "a1"}])
    row["interventions"] = json.dumps([{"id": "i1"}])
    record = runtime_runs_api.normalize_thread_turn_record(row)
    assert record["approvals"] == [{"id": "a1"}]
    assert record["interventions"] == [{"id": "i1"}]
