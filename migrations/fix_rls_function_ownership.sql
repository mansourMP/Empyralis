-- Reassign the four RLS helper functions to the app's own role.
--
-- WHY THIS EXISTS
--
-- `enable_rls.sql` opens with CREATE OR REPLACE FUNCTION on four helpers.
-- CREATE OR REPLACE on an existing function requires ownership, so the whole
-- script aborts on its FIRST statement when the functions are owned by
-- someone else:
--
--     psql:migrations/enable_rls.sql:9: ERROR:
--       must be owner of function empyralis_rls_current_tenant_id
--
-- On production (2026-08-08) all 92 tables were correctly owned by
-- `empyralis_app`, but these four functions were owned by `postgres` — the
-- signature of a migration applied once as the superuser. CLAUDE.md already
-- documents that hazard for tables (a superuser-created table the app cannot
-- alter crash-looped production for ~4 minutes on 2026-08-07). The same
-- mistake on a FUNCTION is quieter and worse: nothing crashes, but
-- `enable_rls.sql` can never be re-run again.
--
-- WHY THAT IS DANGEROUS RATHER THAN MERELY ANNOYING
--
-- CLAUDE.md's standing rule is that `enable_rls.sql` must be re-run after
-- adding any new table, because a table without its policy has no RLS and
-- reads return nothing. With the script aborting on line 9, every table added
-- from that point silently misses its policy. `agent_goals` (deployed
-- 2026-08-08) escaped only because its own migration happened to create its
-- policy inline — luck, not design. The next table may not.
--
-- SCOPE — deliberately narrow
--
-- ONLY the four `empyralis_rls_*` helpers. Production also has many
-- postgres-owned functions from the `btree_gist` extension (gbt_*, cash_dist,
-- date_dist, float4_dist, ...). Those are SUPPOSED to be owned by postgres;
-- reassigning an extension's functions would corrupt the extension. Verified
-- via pg_depend that these four belong to no extension before writing this.
--
-- HOW TO APPLY — as the SUPERUSER, exactly once
--
-- This is the one migration that must NOT be applied as `empyralis_app`:
-- reassigning ownership requires being the current owner (postgres). Every
-- OTHER migration must still be applied as the app role, per CLAUDE.md.
--
--     sudo -u postgres psql -d empyralis -v ON_ERROR_STOP=1 \
--       -f migrations/fix_rls_function_ownership.sql
--
-- Then re-run enable_rls.sql AS THE APP ROLE and confirm it completes:
--
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/enable_rls.sql
--
-- Idempotent: re-running when ownership is already correct is a no-op.

BEGIN;

DO $$
DECLARE
    fn record;
    reassigned int := 0;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'empyralis_app') THEN
        RAISE EXCEPTION
            'role empyralis_app does not exist — refusing to guess an owner';
    END IF;

    FOR fn IN
        SELECT p.oid::regprocedure AS sig
        FROM pg_proc p
        WHERE p.pronamespace = 'public'::regnamespace
          AND p.proname IN (
              'empyralis_rls_current_tenant_id',
              'empyralis_rls_current_workspace_id',
              'empyralis_rls_bypass',
              'empyralis_rls_scope_match'
          )
          -- Never touch a function that belongs to an extension.
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.objid = p.oid AND d.deptype = 'e'
          )
          AND pg_get_userbyid(p.proowner) <> 'empyralis_app'
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO empyralis_app', fn.sig);
        reassigned := reassigned + 1;
        RAISE NOTICE 'reassigned % to empyralis_app', fn.sig;
    END LOOP;

    RAISE NOTICE 'rls function ownership: % reassigned', reassigned;
END
$$;

COMMIT;
