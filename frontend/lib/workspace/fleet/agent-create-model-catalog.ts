"use client";

/**
 * The live half of the creation-time model picker — the network calls
 * agent-create-model.ts's pure rules are fed from. Kept separate so those
 * rules stay drivable by a plain `tsx` test with no React and no fetch.
 *
 * ```
 * GET /api/providers/profiles?workspace_id=…       which providers does this
 *   └ enabled profiles, one per saved credential   workspace ALREADY hold a
 *          │                                        usable credential for?
 *          ▼  (in parallel, only for those)
 * GET /api/providers/{id}/models?workspace_id=…    what can that credential
 *   └ adapter.list_models — the provider's OWN     actually reach right now?
 *     API, never a hand-typed catalog
 * ```
 *
 * Three deliberate properties, each of which is the honest answer to a
 * failure this codebase has already shipped once:
 *
 * **It opens with what is already known.** `choices` is never empty and
 * never waits — the two platform-credit tiers are synchronous constants, so
 * the card renders a fully usable picker on the first frame and the live
 * groups fold in when they arrive. (CLAUDE.md, the channel-card rule: "a
 * card opens with what is already known. It does not fetch on click.")
 *
 * **Every failure is silence, not a fallback.** A 403 (the profiles route is
 * owner-gated), a dead provider, an unreachable upstream — all contribute no
 * group. Never the static MODELS_BY_PROVIDER catalog: at creation there is
 * no saved model to keep visible, so a fallback could only ever put an
 * unverified id in front of someone. See agent-create-model.ts's header for
 * why this diverges from ModelTab's own fallback on purpose.
 *
 * **Freeform providers are excluded by the same rule that excludes them from
 * ModelTab's live list** (FREEFORM_MODEL_PROVIDERS): azure_openai and
 * custom_openai_compatible identify a model by a deployment name, which is
 * not discoverable, so there is nothing for this to ask. They stay a
 * Configure-only choice.
 */

import { useEffect, useRef, useState } from "react";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import {
  buildAgentCreateModelChoices,
  type AgentCreateByokProvider,
  type AgentCreateModelChoice,
} from "./agent-create-model";
import { FREEFORM_MODEL_PROVIDERS, providerLabel } from "./fleet-provider-constants";

export type AgentCreateModelCatalogState = {
  /** Always non-empty and always usable — the platform tiers are known
   *  synchronously. */
  choices: AgentCreateModelChoice[];
  /** True only while the live BYOK sweep is still in flight. The picker is
   *  never blocked on it; this exists so a caller can say "checking your
   *  providers" beside an already-working control if it ever wants to. */
  discovering: boolean;
};

/** Distinct, enabled, non-freeform providers from a /providers/profiles
 *  response body. Exported for the test; takes the parsed body rather than
 *  doing its own fetch so the parsing rule is assertable. */
export function credentialedProvidersFromProfiles(body: unknown): string[] {
  const items = Array.isArray((body as any)?.items) ? (body as any).items : [];
  const out: string[] = [];
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    if (item.enabled === false) continue;
    const provider = String((item as any).provider || "").trim().toLowerCase();
    if (!provider || FREEFORM_MODEL_PROVIDERS.has(provider)) continue;
    if (!out.includes(provider)) out.push(provider);
  }
  return out;
}

export function useAgentCreateModelCatalog(workspaceId: string): AgentCreateModelCatalogState {
  const [byok, setByok] = useState<AgentCreateByokProvider[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const requestIdRef = useRef(0);

  useEffect(() => {
    const requestId = ++requestIdRef.current;
    let cancelled = false;
    const ws = encodeURIComponent(workspaceId);
    if (!workspaceId.trim()) return;
    setDiscovering(true);

    void (async () => {
      let providers: string[] = [];
      try {
        const res = await fleetAuthorizedFetch(`/api/providers/profiles?workspace_id=${ws}`, { credentials: "include" });
        // A non-OK response (403 for a non-owner, anything else) is "no
        // groups", never an error surfaced on the creation card — the
        // picker is fully usable without it.
        providers = res.ok ? credentialedProvidersFromProfiles(await res.json().catch(() => ({}))) : [];
      } catch {
        providers = [];
      }
      const resolved = await Promise.all(
        providers.map(async (provider): Promise<AgentCreateByokProvider | null> => {
          try {
            const res = await fleetAuthorizedFetch(
              `/api/providers/${encodeURIComponent(provider)}/models?workspace_id=${ws}`,
              { credentials: "include" },
            );
            if (!res.ok) return null;
            const data = await res.json().catch(() => ({}));
            if (data?.error) return null;
            const models: string[] = Array.isArray(data?.models)
              ? data.models.filter((m: unknown): m is string => typeof m === "string" && m.trim().length > 0)
              : [];
            if (models.length === 0) return null;
            return { provider, label: providerLabel(provider), models };
          } catch {
            return null;
          }
        }),
      );
      if (cancelled || requestIdRef.current !== requestId) return;
      setByok(resolved.filter((p): p is AgentCreateByokProvider => p !== null));
      setDiscovering(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  return { choices: buildAgentCreateModelChoices(byok), discovering };
}
