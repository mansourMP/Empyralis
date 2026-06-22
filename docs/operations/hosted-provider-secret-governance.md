# Hosted Provider Secret Governance

## Scope

This governs **Empyralis-hosted provider credentials** used by platform runtime lanes.
It does not change workspace BYOK ownership.

## Ownership lanes

- `workspace_byok`:
  - Source: workspace credential vault records.
  - Owner: workspace admin/member policy.
  - Used by: workspace-connected provider profiles.
- `platform_hosted`:
  - Source: hosted provider secret resolver in `server_modules/secrets_broker.py`.
  - Owner: Empyralis platform runtime.
  - Used by: hosted/runtime fallback lane only.

## Access path

Platform-hosted provider secrets now resolve through:

- `secrets_broker.resolve_hosted_provider_secret(...)`
- `secrets_broker.resolve_hosted_openai_bearer(...)`

The resolver prefers `EMPYRALIS_HOSTED_PROVIDER_SECRETS_JSON` (managed bundle path) and keeps an
explicit env var fallback only as a bootstrap compatibility path for local development.

**Security note:** Do not rely on plain environment variables for production secrets. Env vars are
readable via `/proc/<pid>/environ`. Use the managed bundle path or a key file with `0600`
permissions for production deployments.

## Audit path

Every hosted provider secret resolution appends an `agent_secret_access_events` row with:

- `secret_kind=platform_hosted_provider_secret`
- `provider_id`
- `allowed_fields` (requested secret field)
- metadata:
  - `ownership=platform_hosted`
  - `source_kind` (`managed_bundle`, `env_bootstrap`, or `missing`)
  - `source_label`
  - `purpose`

Missing hosted secrets fail safely and emit denied audit entries (`denial_code=hosted_provider_secret_missing`).

## Compatibility

- BYOK behavior remains unchanged.
- Hosted env secrets still work through explicit bootstrap fallback.
- Runtime/provider truth still marks hosted lane as platform runtime.
