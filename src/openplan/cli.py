from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "openplan"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"


def _load_credentials() -> dict[str, Any]:
    if CREDENTIALS_FILE.exists():
        try:
            return json.loads(CREDENTIALS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_credentials(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(data, indent=2))
    CREDENTIALS_FILE.chmod(0o600)


def _get_api_key() -> str | None:
    if key := os.environ.get("OPENPLAN_API_KEY"):
        return key
    creds = _load_credentials()
    return creds.get("api_key")


def cmd_auth_login(args: list[str]) -> None:
    """Authenticate with GitHub via device code flow."""
    api_url = os.environ.get("OPENPLAN_API_URL", "https://api.openplan.ai")

    print("Open the following URL in your browser and enter the code shown.")
    print()

    try:
        import httpx
        resp = httpx.post(f"{api_url}/oauth/authorize", json={
            "client_id": "openplan-cli",
            "scope": "user:email",
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Failed to start authentication: {e}")
        sys.exit(1)

    user_code = data["user_code"]
    verification_uri = data.get("verification_uri", "https://github.com/login/device")
    device_code = data["device_code"]
    interval = data.get("interval", 5)

    print(f"  {verification_uri}")
    print(f"  Enter code: {user_code}")
    print()

    import time
    try:
        import httpx
        while True:
            time.sleep(interval)
            resp = httpx.post(f"{api_url}/oauth/token", json={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": "openplan-cli",
            }, timeout=10)
            token_data = resp.json()
            if resp.status_code == 200:
                access_token = token_data["access_token"]
                refresh_token = token_data.get("refresh_token", "")

                # Exchange access token for an API key
                key_resp = httpx.post(f"{api_url}/api/keys", headers={
                    "Authorization": f"Bearer {access_token}",
                }, json={"tier": "pro"}, timeout=10)
                key_resp.raise_for_status()
                api_key = key_resp.json()["api_key"]

                _save_credentials({
                    "api_key": api_key,
                    "refresh_token": refresh_token,
                    "tier": "pro",
                })
                print("✓ Authentication complete")
                print("✓ Pro tier enabled")
                print(f"  Config: {CREDENTIALS_FILE}")
                return
            elif token_data.get("error") == "authorization_pending":
                continue
            elif token_data.get("error") == "slow_down":
                interval += 5
                continue
            elif token_data.get("error") == "access_denied":
                print("Authentication cancelled.")
                sys.exit(1)
            elif token_data.get("error") == "expired_token":
                print("Session expired. Try again.")
                sys.exit(1)
            else:
                print(f"Unexpected error: {token_data}")
                sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)


def cmd_auth_logout(args: list[str]) -> None:
    """Remove stored credentials."""
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
        api_url = os.environ.get("OPENPLAN_API_URL", "https://api.openplan.ai")
        key = _get_api_key()
        if key:
            try:
                import httpx
                httpx.post(f"{api_url}/api/keys/revoke", json={"api_key": key}, timeout=10)
            except Exception:
                pass
        print("✓ Credentials removed")
    else:
        print("No credentials found")


def cmd_auth_status(args: list[str]) -> None:
    """Show authentication status."""
    creds = _load_credentials()
    env_key = os.environ.get("OPENPLAN_API_KEY")

    if env_key:
        print("Authentication: API key from OPENPLAN_API_KEY env var")
    elif creds.get("api_key"):
        print("Authentication: stored credentials")
        print(f"  Tier: {creds.get('tier', 'unknown')}")
        print(f"  Config: {CREDENTIALS_FILE}")
    else:
        print("Authentication: none (free tier)")
        print("  Run 'openplan auth login' to authenticate with GitHub")


def cmd_subscribe(args: list[str]) -> None:
    """Open Stripe Checkout for Pro subscription."""
    api_key = _get_api_key()
    if not api_key:
        print("You need to authenticate first.")
        print("Run: openplan auth login")
        sys.exit(1)

    api_url = os.environ.get("OPENPLAN_API_URL", "https://api.openplan.ai")
    plan = args[0] if args else "pro"

    try:
        import httpx
        resp = httpx.post(f"{api_url}/checkout", json={
            "plan": plan,
            "api_key": api_key,
        }, timeout=10)
        resp.raise_for_status()
        checkout_url = resp.json()["checkout_url"]
    except Exception as e:
        print(f"Failed to create checkout session: {e}")
        sys.exit(1)

    import webbrowser
    webbrowser.open(checkout_url)
    print(f"Opened browser for Stripe Checkout ({plan} plan)")
    print("Complete payment in the browser.")

    import time
    while True:
        time.sleep(3)
        try:
            resp = httpx.get(f"{api_url}/api/subscription/status", headers={
                "Authorization": f"Bearer {api_key}",
            }, timeout=10)
            data = resp.json()
            if data.get("status") == "active":
                print("✓ Subscription active")
                print(f"  Tier: {data.get('tier', 'pro')}")
                return
        except Exception:
            pass


def cmd_status(args: list[str]) -> None:
    """Show OpenPlan status and sync info."""
    api_key = _get_api_key()
    api_url = os.environ.get("OPENPLAN_API_URL", "https://api.openplan.ai")

    creds = _load_credentials()
    tier = creds.get("tier", "free") if not os.environ.get("OPENPLAN_API_KEY") else "pro (env)"

    from openplan import VERSION
    print(f"OpenPlan v{VERSION}")
    print(f"Tier: {tier}")
    print()

    if api_key:
        try:
            import httpx
            resp = httpx.get(f"{api_url}/api/keys/usage", headers={
                "Authorization": f"Bearer {api_key}",
            }, timeout=10)
            data = resp.json()
            print(f"Events synced: {data.get('event_count', '?')}")
            print(f"Sync rate: {data.get('rate_limit', '?')}/min")
        except Exception:
            print("API: unreachable")
    else:
        print("Free tier (anonymous)")
        print("Run 'openplan auth login' to enable Pro features")


def main() -> None:
    import sys
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print("OpenPlan — Waze for AI agents planning")
        print()
        print("Usage:")
        print("  openplan                  Start MCP server")
        print("  openplan auth login       Authenticate with GitHub")
        print("  openplan auth logout      Remove credentials")
        print("  openplan auth status      Show authentication state")
        print("  openplan subscribe [plan] Start Pro subscription")
        print("  openplan status           Show OpenPlan status")
        print()
        print("Docs: https://github.com/anomalyco/opencode")
        return

    cmd = sys.argv[1]
    if cmd == "auth":
        if len(sys.argv) < 3:
            print("Usage: openplan auth <login|logout|status>")
            return
        sub = sys.argv[2]
        if sub == "login":
            cmd_auth_login(sys.argv[3:])
        elif sub == "logout":
            cmd_auth_logout(sys.argv[3:])
        elif sub == "status":
            cmd_auth_status(sys.argv[3:])
        else:
            print(f"Unknown auth command: {sub}")
    elif cmd == "subscribe":
        cmd_subscribe(sys.argv[2:])
    elif cmd == "status":
        cmd_status(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
