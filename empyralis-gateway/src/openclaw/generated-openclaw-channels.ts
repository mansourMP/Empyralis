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

/** How to install a channel's plugin, read from OpenClaw's own
 *  `channel-catalog.json` `openclaw.install` block. `null` on the seven
 *  channels whose implementation ships inside the pinned bundle. */
export interface GeneratedOpenClawPluginInstall {
  readonly required: boolean;
  /** The PLUGIN id, which is what `openclaw plugins list` keys on. Equal to
   *  the channel id for the official plugins, different for every external
   *  one (`wecom` -> `wecom-openclaw-plugin`). */
  readonly plugin_id: string;
  readonly npm_package: string;
  /** Their spec verbatim, version included when THEY pinned one. */
  readonly npm_spec: string;
  readonly catalog_pinned_version: string | null;
  readonly source: string;
  readonly min_host_version: string | null;
  readonly expected_integrity: string | null;
}

export interface GeneratedOpenClawChannel {
  readonly id: string;
  readonly channel_key: string;
  readonly label: string;
  readonly origin: string;
  readonly config_schema_present: boolean;
  readonly policy_shape: GeneratedOpenClawPolicyShape | null;
  readonly plugin_install: GeneratedOpenClawPluginInstall | null;
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
      },
      "plugin_install": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "discord",
        "npm_package": "@openclaw/discord",
        "npm_spec": "@openclaw/discord",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "feishu",
        "npm_package": "@openclaw/feishu",
        "npm_spec": "@openclaw/feishu",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.5.29",
        "expected_integrity": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "googlechat",
        "npm_package": "@openclaw/googlechat",
        "npm_spec": "@openclaw/googlechat",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": null
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
      },
      "plugin_install": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "line",
        "npm_package": "@openclaw/line",
        "npm_spec": "@openclaw/line",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "matrix",
        "npm_package": "@openclaw/matrix",
        "npm_spec": "@openclaw/matrix",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "msteams",
        "npm_package": "@openclaw/msteams",
        "npm_spec": "@openclaw/msteams",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "nextcloud-talk",
        "npm_package": "@openclaw/nextcloud-talk",
        "npm_spec": "@openclaw/nextcloud-talk",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "nostr",
        "npm_package": "@openclaw/nostr",
        "npm_spec": "@openclaw/nostr",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
      }
    },
    {
      "id": "openclaw-weixin",
      "channel_key": "openclaw_openclaw-weixin",
      "label": "Weixin",
      "origin": "installable",
      "config_schema_present": false,
      "policy_shape": null,
      "plugin_install": {
        "required": true,
        "plugin_id": "openclaw-weixin",
        "npm_package": "@tencent-weixin/openclaw-weixin",
        "npm_spec": "@tencent-weixin/openclaw-weixin@2.4.3",
        "catalog_pinned_version": "2.4.3",
        "source": "external",
        "min_host_version": ">=2026.3.22",
        "expected_integrity": "sha512-dPQbidUNWigC6V10vGW4i+GLH09x+6zUhafZRjuxkJ9GDu8o62WBsnUTojp4KqUH756hz+t2v9khiCRSi0dBDw=="
      }
    },
    {
      "id": "openclaw-zaloclawbot",
      "channel_key": "openclaw_openclaw-zaloclawbot",
      "label": "Zalo ClawBot",
      "origin": "installable",
      "config_schema_present": false,
      "policy_shape": null,
      "plugin_install": {
        "required": true,
        "plugin_id": "openclaw-zaloclawbot",
        "npm_package": "@zalo-platforms/openclaw-zaloclawbot",
        "npm_spec": "@zalo-platforms/openclaw-zaloclawbot@0.1.4",
        "catalog_pinned_version": "0.1.4",
        "source": "external",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": "sha512-5IxZriHJYACLLGqkCPPsTP9tas62kXEOFqTFAFMdunAM3SPhIJwVFRp0WvoP/m7L2PX85weD0g8LOtxM93VDYg=="
      }
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "qqbot",
        "npm_package": "@openclaw/qqbot",
        "npm_spec": "@openclaw/qqbot",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "slack",
        "npm_package": "@openclaw/slack",
        "npm_spec": "@openclaw/slack",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.5.12-beta.1",
        "expected_integrity": null
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
      },
      "plugin_install": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "synology-chat",
        "npm_package": "@openclaw/synology-chat",
        "npm_spec": "@openclaw/synology-chat",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "tlon",
        "npm_package": "@openclaw/tlon",
        "npm_spec": "@openclaw/tlon",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "twitch",
        "npm_package": "@openclaw/twitch",
        "npm_spec": "@openclaw/twitch",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
      }
    },
    {
      "id": "wecom",
      "channel_key": "openclaw_wecom",
      "label": "WeCom",
      "origin": "installable",
      "config_schema_present": false,
      "policy_shape": null,
      "plugin_install": {
        "required": true,
        "plugin_id": "wecom-openclaw-plugin",
        "npm_package": "@wecom/wecom-openclaw-plugin",
        "npm_spec": "@wecom/wecom-openclaw-plugin@2026.5.7",
        "catalog_pinned_version": "2026.5.7",
        "source": "external",
        "min_host_version": null,
        "expected_integrity": "sha512-TCkP9as00WfEhgFWG8YL/rcmaWGIshAki2HQh83nTRccGfVBCoGjrEboTTqq3yDmK9koWTV11zi8u8A4dNtvug=="
      }
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "whatsapp",
        "npm_package": "@openclaw/whatsapp",
        "npm_spec": "@openclaw/whatsapp",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.25",
        "expected_integrity": null
      }
    },
    {
      "id": "yuanbao",
      "channel_key": "openclaw_yuanbao",
      "label": "Yuanbao",
      "origin": "installable",
      "config_schema_present": false,
      "policy_shape": null,
      "plugin_install": {
        "required": true,
        "plugin_id": "openclaw-plugin-yuanbao",
        "npm_package": "openclaw-plugin-yuanbao",
        "npm_spec": "openclaw-plugin-yuanbao@2.13.1",
        "catalog_pinned_version": "2.13.1",
        "source": "external",
        "min_host_version": null,
        "expected_integrity": "sha512-lH2I9/nsmrg7l0YJJSQhOSpWMEFBAa6FwKbZcRLDFHDT2+mOZkHa44XE+8KYN4VmorlUdAxHzpZQmVr7C98IuA=="
      }
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "zalo",
        "npm_package": "@openclaw/zalo",
        "npm_spec": "@openclaw/zalo",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
      },
      "plugin_install": {
        "required": true,
        "plugin_id": "zalouser",
        "npm_package": "@openclaw/zalouser",
        "npm_spec": "@openclaw/zalouser",
        "catalog_pinned_version": null,
        "source": "official",
        "min_host_version": ">=2026.4.10",
        "expected_integrity": null
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
