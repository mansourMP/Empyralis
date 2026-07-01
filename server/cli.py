"""Dev harness + OAuth connect CLI.

Run:  python -m server.cli "your message"
      python -m server.cli connect <app>
"""

import asyncio
import sys
import urllib.parse

from server.agent import Runner
from server.agent.manifest import SAGE_MANIFEST
from server.agent.wiring import wire_runner
from server.vault.store import load_vault, save_vault, set_credential
from server.oauth.provider_configs import get_provider, PROVIDERS
from server.oauth.exchange import exchange_code

REDIRECT_URI = "http://localhost:0/oauth/callback"


def cmd_connect(app_name: str) -> None:
    """OAuth flow: print URL, accept pasted redirect, exchange tokens."""
    provider_key = app_name.strip().lower()
    if provider_key not in PROVIDERS and provider_key not in APPS:
        print(f"Unknown app: {app_name}")
        print(f"Available: {', '.join(sorted(PROVIDERS.keys()))}")
        sys.exit(1)

    config = get_provider(provider_key)
    client_id = __import__("os").environ.get(config.client_id_env, "").strip()
    if not client_id:
        print(f"Missing {config.client_id_env}. Set it in .env and retry.")
        sys.exit(1)

    # Build auth URL
    scopes = " ".join(config.scopes) if config.scopes else ""
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": scopes,
        **config.auth_params,
    }
    if provider_key == "slack":
        params["user_scope"] = scopes
        params["scope"] = ""
    url = f"{config.auth_url}?{urllib.parse.urlencode(params)}"
    print(f"\nOpen this URL in your browser:\n{url}\n")
    print("After authorizing, you'll be redirected to a URL starting with:")
    print(f"  {REDIRECT_URI}?code=...")
    print("Paste the full redirect URL here:")

    redirect = input("> ").strip()
    parsed = urllib.parse.urlparse(redirect)
    qs = urllib.parse.parse_qs(parsed.query)
    code = qs.get("code", [""])[0]
    if not code:
        print("No authorization code found in URL. Make sure you pasted the full redirect URL.")
        sys.exit(1)

    print("Exchanging code for tokens...")
    creds = exchange_code(provider_key, code, REDIRECT_URI)
    vault = load_vault()
    set_credential(vault, f"mcp:{provider_key}", creds)
    save_vault(vault)
    print(f"✓ Connected {config.label}! Credential saved as mcp:{provider_key}")


async def cmd_run(message: str) -> None:
    """Run a single message through Sage with all tools wired."""
    runner = Runner(SAGE_MANIFEST)
    await wire_runner(runner, scope="global")
    response = await runner.run(message)
    print(response)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m server.cli \"your message\"")
        print("       python -m server.cli connect <app>")
        sys.exit(1)

    if sys.argv[1] == "connect":
        if len(sys.argv) < 3:
            print("Usage: python -m server.cli connect <app>")
            print(f"Apps: {', '.join(sorted(PROVIDERS.keys()))}")
            sys.exit(1)
        cmd_connect(sys.argv[2])
    else:
        asyncio.run(cmd_run(sys.argv[1]))


if __name__ == "__main__":
    main()
