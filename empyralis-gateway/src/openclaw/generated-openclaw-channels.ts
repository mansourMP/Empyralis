/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Regenerate with:
 *     python3 scripts/generate_openclaw_channel_manifest.py
 *
 * The gateway's copy of the pinned OpenClaw channel manifest. Identical data
 * to server_modules/openclaw_channel_manifest.json, emitted in the same pass
 * from the same parse; `server_modules/tests/test_openclaw_channel_registry.py`
 * fails if the two ever disagree.
 *
 * It lives inside `src/` because the gateway compiles with `rootDir: ./src`
 * and ships only `dist/` — a JSON file elsewhere in the repo is not
 * guaranteed to be deployed beside the compiled gateway, and a channel set
 * that silently resolves to nothing on a customer box is exactly the failure
 * this whole manifest exists to make impossible.
 */

export interface GeneratedOpenClawPolicyShape {
  readonly dm_policy_modes: readonly string[];
  readonly group_policy_modes: readonly string[];
  readonly channel_require_mention: boolean;
  readonly per_chat_map_key: "groups" | "teams" | null;
  readonly per_chat_map_keyed_on_chat_id: boolean;
  readonly config_writes: boolean;
  readonly plugin_hook_flags: readonly string[];
  readonly unhandled_plugin_hook_flags: readonly string[];
}

export interface GeneratedOpenClawChannel {
  readonly id: string;
  readonly channel_key: string;
  readonly label: string;
  readonly origin: string;
  readonly config_schema_present: boolean;
  readonly policy_shape: GeneratedOpenClawPolicyShape | null;
}

export interface GeneratedOpenClawManifest {
  readonly schema: string;
  readonly openclaw_version: string;
  readonly generated_by: string;
  readonly channel_key_prefix: string;
  readonly channels: readonly GeneratedOpenClawChannel[];
}

export const GENERATED_OPENCLAW_MANIFEST: GeneratedOpenClawManifest = {
  "schema": "empyralis.openclaw_channel_manifest.v1",
  "openclaw_version": "2026.6.10",
  "generated_by": "scripts/generate_openclaw_channel_manifest.py",
  "channel_key_prefix": "openclaw_",
  "channels": [
    {
      "id": "clickclack",
      "channel_key": "openclaw_clickclack",
      "label": "ClickClack",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [],
        "group_policy_modes": [],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "discord",
      "channel_key": "openclaw_discord",
      "label": "Discord",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "feishu",
      "channel_key": "openclaw_feishu",
      "label": "Feishu",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": true,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "googlechat",
      "channel_key": "openclaw_googlechat",
      "label": "Google Chat",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": true,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "imessage",
      "channel_key": "openclaw_imessage",
      "label": "iMessage",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "irc",
      "channel_key": "openclaw_irc",
      "label": "IRC",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "line",
      "channel_key": "openclaw_line",
      "label": "LINE",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "matrix",
      "channel_key": "openclaw_matrix",
      "label": "Matrix",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "mattermost",
      "channel_key": "openclaw_mattermost",
      "label": "Mattermost",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": true,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "msteams",
      "channel_key": "openclaw_msteams",
      "label": "Microsoft Teams",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": true,
        "per_chat_map_key": "teams",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "nextcloud-talk",
      "channel_key": "openclaw_nextcloud-talk",
      "label": "Nextcloud Talk",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "nostr",
      "channel_key": "openclaw_nostr",
      "label": "Nostr",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "openclaw-weixin",
      "channel_key": "openclaw_openclaw-weixin",
      "label": "Weixin",
      "origin": "installable",
      "config_schema_present": false,
      "policy_shape": null
    },
    {
      "id": "openclaw-zaloclawbot",
      "channel_key": "openclaw_openclaw-zaloclawbot",
      "label": "Zalo ClawBot",
      "origin": "installable",
      "config_schema_present": false,
      "policy_shape": null
    },
    {
      "id": "qqbot",
      "channel_key": "openclaw_qqbot",
      "label": "QQ Bot",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "signal",
      "channel_key": "openclaw_signal",
      "label": "Signal",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "slack",
      "channel_key": "openclaw_slack",
      "label": "Slack",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": true,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "sms",
      "channel_key": "openclaw_sms",
      "label": "SMS",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "synology-chat",
      "channel_key": "openclaw_synology-chat",
      "label": "Synology Chat",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [],
        "group_policy_modes": [],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "telegram",
      "channel_key": "openclaw_telegram",
      "label": "Telegram",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": true,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "tlon",
      "channel_key": "openclaw_tlon",
      "label": "Tlon",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [],
        "group_policy_modes": [],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "twitch",
      "channel_key": "openclaw_twitch",
      "label": "Twitch",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [],
        "group_policy_modes": [],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "wecom",
      "channel_key": "openclaw_wecom",
      "label": "WeCom",
      "origin": "installable",
      "config_schema_present": false,
      "policy_shape": null
    },
    {
      "id": "whatsapp",
      "channel_key": "openclaw_whatsapp",
      "label": "WhatsApp",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": true,
        "plugin_hook_flags": [
          "messageReceived"
        ],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "yuanbao",
      "channel_key": "openclaw_yuanbao",
      "label": "Yuanbao",
      "origin": "installable",
      "config_schema_present": false,
      "policy_shape": null
    },
    {
      "id": "zalo",
      "channel_key": "openclaw_zalo",
      "label": "Zalo",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": null,
        "per_chat_map_keyed_on_chat_id": false,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    },
    {
      "id": "zalouser",
      "channel_key": "openclaw_zalouser",
      "label": "Zalo Personal",
      "origin": "installable",
      "config_schema_present": true,
      "policy_shape": {
        "dm_policy_modes": [
          "allowlist",
          "disabled",
          "open",
          "pairing"
        ],
        "group_policy_modes": [
          "allowlist",
          "disabled",
          "open"
        ],
        "channel_require_mention": false,
        "per_chat_map_key": "groups",
        "per_chat_map_keyed_on_chat_id": true,
        "config_writes": false,
        "plugin_hook_flags": [],
        "unhandled_plugin_hook_flags": []
      }
    }
  ]
} as const;

/**
 * The channels this transport is the LIVE implementation of.
 *
 * Not every channel OpenClaw carries: some are platforms Empyralis already
 * implements first-party (Telegram, WhatsApp, Signal, iMessage, WeChat,
 * Discord, Slack, SMS). Declaring all of them must never produce two runtimes
 * on one account, so the overlapping ones are declared and visible but never
 * advertised, provisioned, or handled — see
 * `channel_lane_contract_service.OPENCLAW_TRANSPORT_OWNERSHIP`, which is the
 * ONE place that resolution happens. This array is generated from it, so the
 * gateway cannot hold a second opinion about it.
 */
export const GENERATED_OPENCLAW_ACTIVE_CHANNEL_IDS: readonly string[] = [
  "clickclack",
  "feishu",
  "googlechat",
  "irc",
  "line",
  "matrix",
  "mattermost",
  "msteams",
  "nextcloud-talk",
  "nostr",
  "openclaw-zaloclawbot",
  "qqbot",
  "synology-chat",
  "tlon",
  "twitch",
  "wecom",
  "yuanbao",
  "zalo",
  "zalouser"
] as const;

/** Carried by OpenClaw, owned by an Empyralis first-party runtime. Exported
 *  so a test can assert the two sets partition the manifest, and so nothing
 *  has to re-derive "which ones are missing and why". */
export const GENERATED_OPENCLAW_SUPERSEDED_CHANNEL_IDS: readonly string[] = [
  "discord",
  "imessage",
  "openclaw-weixin",
  "signal",
  "slack",
  "sms",
  "telegram",
  "whatsapp"
] as const;
