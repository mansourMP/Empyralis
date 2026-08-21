"""A binding row carries "key", not "channel_key" — and reading the wrong
one is silent.

`agent_bindings_repository._list` SELECTs `{key_col} AS key`, so every row it
returns carries "key". `routes_fleet.fleet_agent_detail` read
`b.get("channel_key") == "slack"`, which is ALWAYS None — so a bound Slack
channel could never read as connected: undercounted in the Properties
"Channels" total, and the channel grid pill showed "Set up" on a live
channel. Nothing errored; the feature was simply always off.

Slack is one of the two channels the product leads with (CLAUDE.md,
"Channels: Telegram + Slack. Discord is OUT."), so this was dead on a
launch-critical path.

The producer is asserted here, not assumed — the whole class of bug is
reading a shape nobody checked against the code that builds it.
"""

import ast
import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    path = _REPO / rel
    assert path.exists(), f"canary: {rel} not found — this test is not checking what it thinks"
    return path.read_text()


def test_the_repository_really_does_alias_the_column_to_key():
    """The PRODUCER's own SQL. If this alias ever goes away, the consumer
    assertion below stops being the right requirement and this fails first."""
    src = _source("server_modules/agent_bindings_repository.py")
    assert "{key_col} AS key" in src, (
        "agent_bindings_repository no longer aliases the key column to `key`; "
        "re-check every consumer that reads a binding row by key"
    )


def test_fleet_detail_reads_the_key_the_repository_actually_returns():
    src = _source("server_modules/routes_fleet.py")
    assert '(b.get("key") or b.get("channel_key")) == "slack"' in src, (
        "the Slack binding lookup must read `key` (what the repository "
        "returns); reading only `channel_key` is always None and silently "
        "reports a live Slack channel as unconnected"
    )


def test_no_binding_row_is_matched_on_channel_key_alone():
    """A bare b.get("channel_key") over a bindings list is the exact defect.
    An AST scan, because a behavioural test passes either way when no Slack
    channel happens to be bound in the fixture."""
    tree = ast.parse(_source("server_modules/routes_fleet.py"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if (
            isinstance(left, ast.Call)
            and isinstance(left.func, ast.Attribute)
            and left.func.attr == "get"
            and len(left.args) == 1
            and isinstance(left.args[0], ast.Constant)
            and left.args[0].value == "channel_key"
            and isinstance(left.func.value, ast.Name)
            and left.func.value.id == "b"
        ):
            offenders.append(getattr(node, "lineno", -1))
    assert not offenders, (
        f"routes_fleet.py lines {offenders} compare b.get('channel_key') on what "
        "looks like a binding row; the repository returns 'key'"
    )
