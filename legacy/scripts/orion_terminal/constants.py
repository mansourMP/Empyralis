from __future__ import annotations

from dataclasses import dataclass
import os
from typing import List

@dataclass
class Choice:
    key: str
    label: str
    note: str = ""


DEFAULT_API_URL = "http://127.0.0.1:8001"
DEFAULT_QUICK_GOAL = (
    os.getenv("ORION_DEFAULT_QUICK_GOAL", "").strip()
    or "Describe the outcome you want. Empyralis will plan and execute it."
)

GENERAL_MODE = Choice("", "General Digital Worker", "General-purpose AI (default, no niche template)")

SPECIALIST_PACKS: List[Choice] = [
    Choice("customer-ops-autopilot", "Client Workflow Autopilot", "Inbox triage + follow-up + scheduling"),
    Choice("weekly-content-studio", "Weekly Content Studio", "Build weekly channel-ready content plans"),
    Choice("competitor-brief-digest", "Competitor Brief Digest", "Competitor scan + threat ranking + response plays"),
]

TRUST_MODES: List[Choice] = [
    Choice("auto", "Auto", "Recommended default: autonomous execution with policy guardrails"),
    Choice("guarded", "Guarded", "Approval when risk is detected"),
    Choice("strict", "Strict", "Approval before sensitive actions"),
    Choice("cost_guard", "Cost Guard", "Approval based on spend thresholds"),
    Choice("sensitive_guard", "Sensitive Guard", "Compliance-first approvals"),
]

PREFLIGHT_TRUST_MODES: List[Choice] = [
    Choice("auto", "Auto (Default)", "Autonomous execution with policy guardrails"),
    Choice("guarded", "Guarded", "Approval when risk is detected"),
    Choice("strict", "Strict", "Approval before sensitive actions"),
]

PREFLIGHT_OPERATOR_SAFETY_PROFILES: List[Choice] = [
    Choice("default", "Default by trust mode", "Auto=none, Guarded=high, Strict=medium/high"),
    Choice("high_only", "High risk only", "Approval only for high-risk operator commands"),
    Choice("medium_high", "Medium + high", "Approval for medium and high risk commands"),
    Choice("all", "All operator commands", "Approval for low, medium, and high risk commands"),
    Choice("none", "No operator approvals", "Fastest, least safe (use carefully)"),
]

EXECUTION_TARGETS: List[Choice] = [
    Choice("local_companion", "Local Companion", "Run on your machine via worker"),
    Choice("auto", "Auto", "Runtime picks best target"),
    Choice("cloud", "Cloud", "Server-side execution"),
]

FILE_ACCESS_SCOPES: List[Choice] = [
    Choice("workspace", "Workspace", "Safest: agent can access only workspace files"),
    Choice("restricted", "Restricted Paths", "Allowlisted paths only"),
    Choice("full", "Full", "Unrestricted file access (high risk)"),
]

LAUNCHER_ACTIONS: List[Choice] = [
    Choice("orion_setup", "Empyralis Setup", "Primary setup: gateway, provider, model, and channels"),
    Choice("preflight", "Preflight Check", "Security, provider, and scope before start"),
    Choice("guided", "Guided Run", "Recommended: multi-step setup before execution"),
    Choice("quick", "Quick Start", "One-click run with safe defaults"),
    Choice("goal", "Custom Goal", "Type a goal and choose runtime controls"),
    Choice("specialist", "Specialist Template", "Outcome pack mode"),
    Choice("onboard", "Onboard", "Simple setup (security, provider, optional channels, launch)"),
    Choice("configure", "Configure", "Section-based reconfiguration"),
    Choice("connectors", "Connect Channels", "Google Workspace, Telegram, WhatsApp"),
    Choice("doctor", "Runtime Doctor", "Health and safety checks"),
    Choice("tui", "Live TUI", "Persistent command loop for runs"),
    Choice("exit", "Exit", "Close launcher"),
]

ORION_SETUP_GATEWAY_LOCATIONS: List[Choice] = [
    Choice("local", "Local (this machine)", "Primary local runtime on this device"),
    Choice("remote", "Remote (info-only)", "Use a configured remote gateway endpoint"),
]

ORION_SETUP_CONFIG_SECTIONS: List[Choice] = [
    Choice("workspace", "Workspace", "Set workspace + sessions"),
    Choice("model", "Model", "Pick provider + auth"),
    Choice("web", "Web tools", "Configure search/fetch tools"),
    Choice("gateway", "Gateway", "Port, bind, auth"),
    Choice("daemon", "Daemon", "Manage background service"),
    Choice("channels", "Channels", "Link Telegram/WhatsApp/etc"),
    Choice("skills", "Skills", "Install/enable workspace skills"),
    Choice("health", "Health check", "Run runtime checks"),
]

ORION_SETUP_ONBOARDING_MODES: List[Choice] = [
    Choice("quickstart", "QuickStart", "Configure details later via Empyralis Configure"),
    Choice("manual", "Manual", "Configure port/network/auth options now"),
]

ORION_SETUP_CONFIG_HANDLING: List[Choice] = [
    Choice("keep", "Use existing values", "Keep currently stored defaults"),
    Choice("modify", "Update values", "Edit and save selected settings"),
    Choice("reset", "Reset", "Reset config scope before setup"),
]

ORION_SETUP_RESET_SCOPES: List[Choice] = [
    Choice("config", "Config only", "Reset config, keep creds/workspace"),
    Choice("config+creds+sessions", "Config + creds + sessions", "Reset config and local auth/session data"),
    Choice("full", "Full reset", "Reset config, creds, sessions, and workspace state"),
]

ORION_SETUP_PROVIDER_GROUPS: List[Choice] = [
    Choice("openai", "OpenAI", "Codex OAuth + API key"),
    Choice("anthropic", "Anthropic", "setup-token + API key"),
    Choice("chutes", "Chutes", "OAuth"),
    Choice("vllm", "vLLM", "Local/self-hosted OpenAI-compatible"),
    Choice("minimax", "MiniMax", "M2.5 (recommended)"),
    Choice("moonshot", "Moonshot AI (Kimi K2.5)", "Kimi K2.5 + Kimi Coding"),
    Choice("google", "Google", "Gemini API key + OAuth"),
    Choice("xai", "xAI (Grok)", "API key"),
    Choice("mistral", "Mistral AI", "API key"),
    Choice("volcengine", "Volcano Engine", "API key"),
    Choice("byteplus", "BytePlus", "API key"),
    Choice("openrouter", "OpenRouter", "API key"),
    Choice("kilocode", "Kilo Gateway", "API key (OpenRouter-compatible)"),
    Choice("qwen", "Qwen", "OAuth"),
    Choice("zai", "Z.AI", "GLM Coding Plan / Global / CN"),
    Choice("qianfan", "Qianfan", "API key"),
    Choice("copilot", "Copilot", "GitHub + local proxy"),
    Choice("ai-gateway", "Vercel AI Gateway", "API key"),
    Choice("opencode-zen", "OpenCode Zen", "API key"),
    Choice("xiaomi", "Xiaomi", "API key"),
    Choice("synthetic", "Synthetic", "Anthropic-compatible"),
    Choice("together", "Together AI", "API key"),
    Choice("huggingface", "Hugging Face", "Inference API token"),
    Choice("venice", "Venice AI", "Privacy-focused"),
    Choice("cloudflare-ai-gateway", "Cloudflare AI Gateway", "Account + Gateway + API key"),
    Choice("custom", "Custom Provider", "OpenAI/Anthropic compatible endpoint"),
    Choice("skip", "Skip for now", "Continue without provider override"),
]

ORION_SETUP_MODEL_PROVIDER_FILTERS: List[Choice] = [
    Choice("*", "All providers", "Show all model providers"),
    Choice("amazon-bedrock", "amazon-bedrock", ""),
    Choice("anthropic", "anthropic", ""),
    Choice("azure-openai-responses", "azure-openai-responses", ""),
    Choice("cerebras", "cerebras", ""),
    Choice("github-copilot", "github-copilot", ""),
    Choice("google", "google", ""),
    Choice("google-antigravity", "google-antigravity", ""),
    Choice("google-gemini-cli", "google-gemini-cli", ""),
    Choice("google-vertex", "google-vertex", ""),
    Choice("groq", "groq", ""),
    Choice("huggingface", "huggingface", ""),
    Choice("kimi-coding", "kimi-coding", ""),
    Choice("minimax", "minimax", ""),
    Choice("minimax-cn", "minimax-cn", ""),
    Choice("mistral", "mistral", ""),
    Choice("openai", "openai", ""),
    Choice("openai-codex", "openai-codex", ""),
    Choice("opencode", "opencode", ""),
    Choice("openrouter", "openrouter", ""),
    Choice("vercel-ai-gateway", "vercel-ai-gateway", ""),
    Choice("xai", "xai", ""),
    Choice("zai", "zai", ""),
]

ORION_SETUP_QUICKSTART_CHANNELS: List[Choice] = [
    Choice("google_workspace", "Email + Calendar (Google Workspace)", "Gmail drafts + Calendar booking actions"),
    Choice("telegram_bot", "Telegram (Bot API)", "Bot token + chat ID"),
    Choice("whatsapp_twilio", "WhatsApp (Twilio)", "Twilio credentials"),
    Choice("discord_bot", "Discord (Bot API)", "Token + guild/channel"),
    Choice("irc", "IRC (Server + Nick)", "Server, nick, and auth"),
    Choice("skip", "Skip for now", "You can add channels later"),
]

CONFIGURE_SECTIONS: List[Choice] = [
    Choice("workspace", "Workspace", "Set workspace + sessions"),
    Choice("provider", "Provider/Auth", "Select provider and verify credential"),
    Choice("profiles", "Provider Profiles", "Create or adjust fallback profile rotation"),
    Choice("web", "Web tools", "Configure search/fetch tools"),
    Choice("gateway", "Gateway", "Port, bind, auth"),
    Choice("daemon", "Daemon", "Manage background service"),
    Choice("channels", "Channels", "Link Telegram/WhatsApp/etc"),
    Choice("skills", "Skills", "Install/enable workspace skills"),
    Choice("connectors", "Connectors", "Set up external channels/tools (Legacy)"),
    Choice("doctor", "Doctor", "Run runtime diagnostic checks"),
    Choice("launch", "Launch", "Open Live TUI or web entrypoint"),
]
