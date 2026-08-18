"""Two MCP tools were removed, and BOTH need a structural guard rather than a
behavioural one -- a behavioural test can only ever cover the tools that exist
today, and the way each of these comes back is by someone re-adding it.

1. ``empyralis_message_agent`` was a DEAD CONTROL on the model's own tool list.
   It was advertised to every MCP client and ``fleet_message_agent`` always
   returned ``ok: false`` BY DESIGN -- its own docstring said so. CLAUDE.md's
   product law is explicit ("a control whose own label admits it does nothing
   is a design bug, not a caption"), and it is worse on a tool list than in a
   UI: a model that SEES a tool will try it, burn a turn on it, and may then
   narrate a success that never happened -- this codebase's own documented
   "silent misrouting" failure, which once told a customer work was done that
   never happened.

   The whole feature was removed, not just the MCP advertisement: the same
   dead action was advertised on two further surfaces (skills_service's
   ``fleet__message_agent`` ToolDescriptor, which is the schema handed to the
   model on EVERY turn, and skill_registry's ``fleet-message-agent``
   SkillDefinition). Deleting one advertisement and leaving two is the
   "dormant seam left behind by a cutover" shape CLAUDE.md already documents.

   It was NOT given a backend instead. The platform's decided design for real
   handoff is task/mention delivery through the scheduler; quietly making
   "message this agent" create a task would be silent misrouting under a
   friendlier name.

2. ``empyralis_assign_channel_bot`` took a plaintext BotFather/Discord bot
   token as a tool ARGUMENT (MAN-207). The secret travelled through the
   calling model's context and into that client's stored transcript before it
   ever reached us. An MCP bearer key lives in a config file; it must never
   also be a secret-INGESTION path.

   ``test_no_mcp_tool_accepts_a_credential_shaped_argument`` is the durable
   half: it bans the SHAPE across the whole surface, so the same mistake
   cannot return under a different tool name. Deleting one function does not
   stop the next author from adding ``empyralis_set_provider_key(key: str)``.

3. ``empyralis_update_task_status`` deliberately passes NO actor, and that is
   guarded here because the obvious "fix" is a one-line change that
   type-checks, looks like an improvement, and is a bug. See that test.
"""

from __future__ import annotations

import inspect
import unittest

import mcp_server


class RemovedDeadMessageAgentToolTests(unittest.IsolatedAsyncioTestCase):

    def test_message_agent_is_not_advertised_anywhere_in_the_mcp_list(self):
        self.assertNotIn("empyralis_message_agent", mcp_server.EMPYRALIST_MCP_TOOLS)

    async def test_message_agent_is_not_registered_on_the_live_server(self):
        if mcp_server.empyralist_mcp is None:
            self.skipTest("mcp SDK not installed")
        registered = {t.name for t in await mcp_server.empyralist_mcp.list_tools()}
        self.assertNotIn("empyralis_message_agent", registered)

    def test_the_backing_function_is_gone_too(self):
        """Leaving `fleet_message_agent` behind would let any of the three
        deleted advertisements be restored with a one-line import."""
        from server_modules import fleet_tools

        self.assertFalse(hasattr(fleet_tools, "fleet_message_agent"))

    def test_the_agent_facing_tool_schema_no_longer_carries_it(self):
        """skills_service's ToolDescriptor list IS the tool schema handed to
        the model on every turn -- the surface the product law is really
        about."""
        from server_modules import skills_service

        source = inspect.getsource(skills_service)
        self.assertNotIn('tool_name="fleet__message_agent"', source)

    def test_the_skill_registry_entry_and_its_executor_are_gone(self):
        from server_modules import skill_registry

        self.assertFalse(hasattr(skill_registry, "_live_fleet_message_agent_skill"))
        source = inspect.getsource(skill_registry)
        self.assertNotIn('"fleet-message-agent"', source)
        self.assertNotIn('id="fleet-message-agent"', source)

    def test_historical_ledger_rows_are_still_readable(self):
        """The removal must not make already-written history vanish. Rows with
        `message_agent_refused` exist in every production ledger; the agent
        activity query still lets that one fleet_control action through."""
        from server_modules import fleet_tools

        source = inspect.getsource(fleet_tools.fleet_get_agent_activity)
        self.assertIn("message_agent_refused", source)


class NoCredentialEntersTheMcpSurfaceTests(unittest.IsolatedAsyncioTestCase):

    def test_assign_channel_bot_is_not_advertised(self):
        self.assertNotIn("empyralis_assign_channel_bot", mcp_server.EMPYRALIST_MCP_TOOLS)

    async def test_assign_channel_bot_is_not_registered_on_the_live_server(self):
        if mcp_server.empyralist_mcp is None:
            self.skipTest("mcp SDK not installed")
        registered = {t.name for t in await mcp_server.empyralist_mcp.list_tools()}
        self.assertNotIn("empyralis_assign_channel_bot", registered)

    async def test_release_channel_bot_deliberately_survives(self):
        """Releasing takes no token and only tears a binding down. Removing it
        with its sibling would cost an owner the ability to free a stuck
        binding for no security gain -- the rule is 'no credential enters this
        surface', not 'no channel tool exists'."""
        if mcp_server.empyralist_mcp is None:
            self.skipTest("mcp SDK not installed")
        registered = {t.name for t in await mcp_server.empyralist_mcp.list_tools()}
        self.assertIn("empyralis_release_channel_bot", registered)

    async def test_no_mcp_tool_accepts_a_credential_shaped_argument(self):
        """THE DURABLE ONE. Asserted against the LIVE tool schemas the server
        would answer `tools/list` with -- not against a source grep and not
        against the hand-kept name list, so a new tool is covered the moment
        it is registered rather than when someone remembers this file."""
        if mcp_server.empyralist_mcp is None:
            self.skipTest("mcp SDK not installed")
        banned = (
            "token", "secret", "password", "passwd", "api_key", "apikey",
            "private_key", "credential", "access_key", "client_secret",
            "bot_token", "passphrase",
        )
        offenders = []
        for tool in await mcp_server.empyralist_mcp.list_tools():
            schema = getattr(tool, "inputSchema", None) or {}
            for param in (schema.get("properties") or {}):
                lowered = str(param).lower()
                if any(word in lowered for word in banned):
                    offenders.append(f"{tool.name}.{param}")
        self.assertEqual(
            offenders, [],
            "An MCP tool takes a credential-shaped argument. The secret would "
            "travel through the calling model's context and into that client's "
            "stored transcript. Credentials are entered by their owner in the "
            "product, where they go straight to the vault -- never as a tool "
            f"argument. Offenders: {offenders}",
        )


class UpdateTaskStatusActorIsDeliberatelyOmittedTests(unittest.TestCase):
    """`empyralis_update_task_status` passes neither `actor_user_id` nor
    `actor_agent_id` to `project_tasks_service.update_task`, and that is
    CORRECT today, not an oversight to tidy up.

    `update_task`'s two actor parameters land in
    `project_tasks.completed_by_user_id` / `completed_by_agent_id`, which are
    FK'd to `users(id)` and `workspace_agent_installs(id)` respectively (see
    migrations/add_task_completion_attribution.sql). An MCP caller's identity
    is an `ext_agent_<hex16>` row in `mcp_external_agent_roster` -- it is in
    NEITHER table. `list_my_tasks`'s own docstring says the same thing about
    the sibling `assignee_agent_id` column: external agents cannot yet BE a
    task actor; that needs the deferred schema change.

    So passing the roster id would (a) violate the FK, (b) get swallowed by
    update_task's last-resort backstop, which retries with both columns NULLed
    -- so the stamp is silently dropped ANYWAY -- and (c) log a WARNING on
    every single external task close forever. A one-line "improvement" that
    changes nothing except adding permanent log noise.

    Attribution for this call is not lost meanwhile: `_ledger_mcp_call` writes
    an `mcp_inbound` activity event carrying the external agent's id AND its
    display name.
    """

    def test_the_mcp_status_tool_passes_no_fk_backed_actor(self):
        source = inspect.getsource(mcp_server)
        start = source.index("async def empyralis_update_task_status")
        body = source[start:start + 1600]
        self.assertIn("tasks.update_task(", body)
        for banned in ("actor_agent_id", "actor_user_id"):
            self.assertNotIn(
                banned, body,
                f"empyralis_update_task_status now passes {banned}. An MCP "
                "caller's ext_agent_<hex> id is not in users(id) or "
                "workspace_agent_installs(id); this violates the completed_by_* "
                "FK, is silently retried with the stamp dropped, and logs a "
                "warning on every close. Read this class's docstring before "
                "reinstating it.",
            )


if __name__ == "__main__":
    unittest.main()
