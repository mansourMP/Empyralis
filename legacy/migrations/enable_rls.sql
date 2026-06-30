BEGIN;

CREATE OR REPLACE FUNCTION public.empyralis_rls_current_tenant_id()
RETURNS text
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.current_tenant_id', true), '');
$$;

CREATE OR REPLACE FUNCTION public.empyralis_rls_current_workspace_id()
RETURNS text
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.current_workspace_id', true), '');
$$;

CREATE OR REPLACE FUNCTION public.empyralis_rls_bypass()
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(NULLIF(current_setting('app.rls_bypass', true), ''), 'off') IN ('1', 'true', 'on');
$$;

CREATE OR REPLACE FUNCTION public.empyralis_rls_scope_match(row_tenant_id text, row_workspace_id text)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT
        public.empyralis_rls_bypass()
        OR (
            row_tenant_id = public.empyralis_rls_current_tenant_id()
            AND row_workspace_id = public.empyralis_rls_current_workspace_id()
        );
$$;

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_tenants_scope ON tenants;
CREATE POLICY empyralis_tenants_scope ON tenants
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_users_scope ON users;
CREATE POLICY empyralis_users_scope ON users
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE auth_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_identities FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_auth_identities_scope ON auth_identities;
CREATE POLICY empyralis_auth_identities_scope ON auth_identities
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspaces FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_workspaces_scope ON workspaces;
CREATE POLICY empyralis_workspaces_scope ON workspaces
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE workspace_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_memberships FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_workspace_memberships_scope ON workspace_memberships;
CREATE POLICY empyralis_workspace_memberships_scope ON workspace_memberships
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_threads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_threads_scope ON agent_threads;
CREATE POLICY empyralis_agent_threads_scope ON agent_threads
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_sessions_scope ON agent_sessions;
CREATE POLICY empyralis_agent_sessions_scope ON agent_sessions
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_turns FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_turns_scope ON agent_turns;
CREATE POLICY empyralis_agent_turns_scope ON agent_turns
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE workflow_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_definitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_workflow_definitions_scope ON workflow_definitions;
CREATE POLICY empyralis_workflow_definitions_scope ON workflow_definitions
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE workflow_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_workflow_versions_scope ON workflow_versions;
CREATE POLICY empyralis_workflow_versions_scope ON workflow_versions
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE runtime_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE runtime_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_runtime_profiles_scope ON runtime_profiles;
CREATE POLICY empyralis_runtime_profiles_scope ON runtime_profiles
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_definitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_definitions_scope ON agent_definitions;
CREATE POLICY empyralis_agent_definitions_scope ON agent_definitions
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_definition_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_definition_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_definition_versions_scope ON agent_definition_versions;
CREATE POLICY empyralis_agent_definition_versions_scope ON agent_definition_versions
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE workspace_agent_installs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_agent_installs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_workspace_agent_installs_scope ON workspace_agent_installs;
CREATE POLICY empyralis_workspace_agent_installs_scope ON workspace_agent_installs
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE workspace_inventory_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_inventory_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_workspace_inventory_items_scope ON workspace_inventory_items;
CREATE POLICY empyralis_workspace_inventory_items_scope ON workspace_inventory_items
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_manifests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_manifests_scope ON agent_manifests;
CREATE POLICY empyralis_agent_manifests_scope ON agent_manifests
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_bible_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_bible_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_bible_versions_scope ON agent_bible_versions;
CREATE POLICY empyralis_agent_bible_versions_scope ON agent_bible_versions
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_skill_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_skill_bindings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_skill_bindings_scope ON agent_skill_bindings;
CREATE POLICY empyralis_agent_skill_bindings_scope ON agent_skill_bindings
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_connector_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_connector_bindings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_connector_bindings_scope ON agent_connector_bindings;
CREATE POLICY empyralis_agent_connector_bindings_scope ON agent_connector_bindings
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_channel_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_channel_bindings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_channel_bindings_scope ON agent_channel_bindings;
CREATE POLICY empyralis_agent_channel_bindings_scope ON agent_channel_bindings
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_runtime_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_runtime_profiles_scope ON agent_runtime_profiles;
CREATE POLICY empyralis_agent_runtime_profiles_scope ON agent_runtime_profiles
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE personal_context_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE personal_context_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_personal_context_events_scope ON personal_context_events;
CREATE POLICY empyralis_personal_context_events_scope ON personal_context_events
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_scheduler_wake_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_scheduler_wake_requests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_scheduler_wake_requests_scope ON agent_scheduler_wake_requests;
CREATE POLICY empyralis_agent_scheduler_wake_requests_scope ON agent_scheduler_wake_requests
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_channel_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_channel_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_channel_events_scope ON agent_channel_events;
CREATE POLICY empyralis_agent_channel_events_scope ON agent_channel_events
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_secret_access_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_secret_access_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_secret_access_events_scope ON agent_secret_access_events;
CREATE POLICY empyralis_agent_secret_access_events_scope ON agent_secret_access_events
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_egress_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_egress_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_egress_events_scope ON agent_egress_events;
CREATE POLICY empyralis_agent_egress_events_scope ON agent_egress_events
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE agent_channel_execution_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_channel_execution_leases FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_agent_channel_execution_leases_scope ON agent_channel_execution_leases;
CREATE POLICY empyralis_agent_channel_execution_leases_scope ON agent_channel_execution_leases
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE security_control_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_control_states FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_security_control_states_scope ON security_control_states;
CREATE POLICY empyralis_security_control_states_scope ON security_control_states
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

ALTER TABLE security_control_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_control_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_security_control_events_scope ON security_control_events;
CREATE POLICY empyralis_security_control_events_scope ON security_control_events
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

COMMIT;
