import argparse
import json
import os
import sys
import time

GITHUB_CLIENT_ID = os.environ.get("OPENPLAN_GITHUB_CLIENT_ID", "Ov23lib55xjCggd9BIDy")
MESH_API_URL = os.environ.get("OPENPLAN_API_URL", "https://api.openplan.cc")
CONFIG_DIR = os.path.expanduser("~/.config/openplan")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openplan",
        description="Waze for AI agents — plan, track, and learn from software projects.",
    )
    sub = parser.add_subparsers(dest="command")

    auth = sub.add_parser("auth", help="GitHub OAuth device code flow")
    subscribe = sub.add_parser("subscribe", help="Upgrade tier via Stripe checkout")
    account = sub.add_parser("account", help="Account info, tier, checkpoint count")
    status = sub.add_parser("status", help="Show current route state")
    log = sub.add_parser("log", help="Show checkpoint trail for a route or project")

    status.add_argument("project", nargs="?", help="Project name (derived from CWD if omitted)")
    status.add_argument("--json", action="store_true", help="JSON output")
    status.add_argument("--no-color", action="store_true", help="Disable color output")

    log.add_argument("id", nargs="?", help="Route ID or project name")
    log.add_argument("--json", action="store_true", help="JSON output")

    account.add_argument("--json", action="store_true", help="JSON output")
    subscribe.add_argument("--tier", default="pro", choices=["pro", "team"], help="Tier to subscribe to")

    return parser


def cmd_auth(args):
    """GitHub OAuth device code flow."""
    import httpx

    print("Starting GitHub OAuth device flow...", file=sys.stderr)

    # Step 1: Get device code
    try:
        resp = httpx.post(
            "https://github.com/login/device/code",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "scope": "read:user",
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Failed to initiate OAuth: {e}", file=sys.stderr)
        sys.exit(1)

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data.get("verification_uri", "https://github.com/login/device")
    interval = data.get("interval", 5)
    expires_in = data.get("expires_in", 900)

    print(f"\nOpen {verification_uri} in your browser", file=sys.stderr)
    print(f"Enter code: \033[1;33m{user_code}\033[0m\n", file=sys.stderr)
    print(f"Code expires in {expires_in // 60} minutes", file=sys.stderr)

    # Step 2: Poll for access token
    waited = 0
    while waited < expires_in:
        time.sleep(interval)
        waited += interval
        try:
            resp = httpx.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
                timeout=10,
            )
            token_data = resp.json()
        except Exception as e:
            print(f"Polling error: {e}", file=sys.stderr)
            continue

        if "access_token" in token_data:
            github_token = token_data["access_token"]
            break
        elif token_data.get("error") == "authorization_pending":
            continue
        elif token_data.get("error") == "slow_down":
            interval += 5
            continue
        elif token_data.get("error") == "expired_token":
            print("Code expired. Run 'openplan auth' again.", file=sys.stderr)
            sys.exit(1)
        elif token_data.get("error") == "access_denied":
            print("Authorization denied.", file=sys.stderr)
            sys.exit(1)
    else:
        print("Timed out waiting for authorization.", file=sys.stderr)
        sys.exit(1)

    # Step 3: Verify identity via GitHub API
    try:
        resp = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {github_token}", "Accept": "application/json"},
            timeout=10,
        )
        user_data = resp.json()
        github_login = user_data.get("login", "unknown")
    except Exception:
        github_login = "unknown"

    # Step 4: Exchange GitHub token for OpenPlan API key via Mesh API
    api_key = None
    try:
        resp = httpx.post(
            f"{MESH_API_URL}/v1/auth/device",
            json={"github_token": github_token, "github_login": github_login},
            timeout=10,
        )
        if resp.status_code == 200:
            api_key = resp.json().get("api_key")
    except Exception:
        pass

    if not api_key:
        # Local fallback: generate a local API key
        import uuid
        api_key = f"op_{uuid.uuid4().hex}"

    # Store config
    os.makedirs(CONFIG_DIR, exist_ok=True)
    config = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    config["api_key"] = api_key
    config["github_user"] = github_login

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    os.chmod(CONFIG_PATH, 0o600)
    print(f"\nAuthenticated as \033[1;32m{github_login}\033[0m", file=sys.stderr)
    print(f"API key stored in {CONFIG_PATH}", file=sys.stderr)


def cmd_subscribe(args):
    """Create a Stripe Checkout Session."""
    import httpx
    import uuid

    api_key = _get_api_key()
    if not api_key:
        print("Not authenticated. Run 'openplan auth' first.", file=sys.stderr)
        sys.exit(1)

    tier_label = "Pro" if args.tier == "pro" else "Team"
    tier_price = "$9/mo" if args.tier == "pro" else "$49/mo"

    try:
        resp = httpx.post(
            f"{MESH_API_URL}/v1/subscribe",
            json={"tier": args.tier, "api_key": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            checkout_url = resp.json().get("checkout_url", "")
            print(f"\nUpgrade to {tier_label} ({tier_price})", file=sys.stderr)
            print(f"Visit: \033[1;34m{checkout_url}\033[0m\n", file=sys.stderr)
            return
    except Exception:
        pass

    # Local fallback: print Stripe checkout URL directly
    checkout_url = (
        f"https://checkout.stripe.com/pay/"
        f"price_1Tix1IAag5YeWwyY15jzyVjI"
        f"?client_reference_id={api_key}"
    )
    print(f"\nUpgrade to {tier_label} ({tier_price})", file=sys.stderr)
    print(f"Visit: \033[1;34m{checkout_url}\033[0m\n", file=sys.stderr)


def cmd_account(args):
    """Show account info."""
    api_key = _get_api_key()
    data = {
        "tier": "free",
        "checkpoints": 0,
        "personal_bias": 1.0,
        "authenticated": bool(api_key),
    }

    if api_key:
        # Try to fetch from Mesh API
        import httpx
        try:
            resp = httpx.get(
                f"{MESH_API_URL}/v1/account",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5,
            )
            if resp.status_code == 200:
                data.update(resp.json())
        except Exception:
            pass

    if args.json:
        print(json.dumps(data))
    else:
        print(f"Authenticated: {'yes' if data['authenticated'] else 'no'}")
        print(f"Tier: {data['tier']}")
        print(f"Checkpoints: {data['checkpoints']}")
        print(f"Personal bias: {data['personal_bias']:.2f}x")


def cmd_status(args):
    project = args.project or os.path.basename(os.getcwd())
    data = {
        "project": project,
        "status": "no active route",
        "phases": [],
    }
    if args.json:
        print(json.dumps(data))
    else:
        print(f"Project: {project}")
        print("No active route — call plan() first.")


def cmd_log(args):
    identifier = args.id or os.path.basename(os.getcwd())
    data = {
        "route": identifier,
        "checkpoints": [],
    }
    if args.json:
        print(json.dumps(data))
    else:
        print(f"Route: {identifier}")
        print("No checkpoints recorded.")


def _get_api_key() -> str:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                config = json.load(f)
                return config.get("api_key", "")
        except (json.JSONDecodeError, OSError):
            pass
    return os.environ.get("OPENPLAN_API_KEY", "")


def main():
    parser = build_parser()
    args = parser.parse_args()

    no_color = args.no_color if hasattr(args, "no_color") else os.environ.get("NO_COLOR", "")
    if no_color:
        os.environ["NO_COLOR"] = "1"

    commands = {
        "auth": cmd_auth,
        "subscribe": cmd_subscribe,
        "account": cmd_account,
        "status": cmd_status,
        "log": cmd_log,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
