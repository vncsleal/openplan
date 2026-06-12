#!/usr/bin/env python3
"""
RLHF Dataset Pipeline for OpenPlan

Reads opencode session data from a local MCP server, correlates it with
OpenPlan events stored in the project database, and outputs a structured
dataset suitable for RLHF preference tuning.

Usage:
    python scripts/rlhf_pipeline.py --db-path openplan.db > rlhf_data.json
    python scripts/rlhf_pipeline.py --db-path openplan.db --opencode-url http://localhost:4096 --window 15.0
    python scripts/rlhf_pipeline.py --db-path openplan.db --max-sessions 5 --pretty
"""

from __future__ import annotations

import argparse
import sys
import json
import os
import sqlite3
import urllib.request
import urllib.error

from openplan.core.rlhf import build_rlhf_dataset


def _get_password(args_password: str | None) -> str | None:
    return args_password or os.environ.get("OPENCODE_SERVER_PASSWORD") or None


def _dry_run(db_path: str, opencode_url: str, opencode_endpoint: str, password: str | None) -> None:
    print(f"Database: {db_path}")
    print(f"OpenCode URL: {opencode_url}")
    print()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        session_count = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS cnt FROM events WHERE session_id IS NOT NULL AND session_id != ''"
        ).fetchone()["cnt"]
        print(f"Sessions with events: {session_count}")

        events_total = conn.execute("SELECT COUNT(*) AS cnt FROM events").fetchone()["cnt"]
        print(f"Total events: {events_total}")

        sample = conn.execute(
            "SELECT DISTINCT session_id, project FROM events "
            "WHERE session_id IS NOT NULL AND session_id != '' "
            "LIMIT 5"
        ).fetchall()
        if sample:
            print()
            print("Sample session IDs:")
            for row in sample:
                print(f"  {row['session_id']}  ({row['project']})")
    finally:
        conn.close()

    headers = {"Accept": "application/json"}
    if password:
        import base64
        token = base64.b64encode(f"opencode:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    print()
    print("Testing opencode API connection...")
    try:
        req = urllib.request.Request(f"{opencode_url.rstrip('/')}/global/health", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read().decode())
            print(f"  Health: {health}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return

    if sample:
        test_sid = sample[0]["session_id"]
        test_url = f"{opencode_url.rstrip('/')}{opencode_endpoint.format(id=test_sid)}"
        print(f"  Testing endpoint: {test_url}")
        try:
            req = urllib.request.Request(test_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if isinstance(data, list):
                    print(f"  OK — received {len(data)} messages")
                elif isinstance(data, dict):
                    print(f"  OK — received dict with keys: {list(data.keys())}")
                else:
                    print(f"  OK — received: {type(data).__name__}")
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}: {e.reason}")
        except Exception as e:
            print(f"  FAILED: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an RLHF dataset from OpenPlan events and opencode conversation data",
    )
    parser.add_argument(
        "--db-path",
        default="openplan.db",
        help="Path to the OpenPlan SQLite database (default: openplan.db)",
    )
    parser.add_argument(
        "--opencode-url",
        default="http://localhost:4096",
        help="Base URL of the opencode local MCP server (default: http://localhost:4096)",
    )
    parser.add_argument(
        "--opencode-endpoint",
        default="/session/{id}/message",
        help="Endpoint template for session messages. Use {id} as placeholder for session ID "
             "(default: /session/{id}/message)",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=30.0,
        help="Time window in seconds for correlating events with messages (default: 30.0)",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=0,
        help="Maximum number of sessions to process (0 = unlimited, default: 0)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect DB and test API connection without producing output",
    )
    parser.add_argument(
        "--opencode-password",
        default=None,
        help="OpenCode server password (default: OPENCODE_SERVER_PASSWORD env var)",
    )
    args = parser.parse_args()

    password = _get_password(args.opencode_password)

    if args.dry_run:
        _dry_run(args.db_path, args.opencode_url, args.opencode_endpoint, password)
        return

    dataset = build_rlhf_dataset(
        db_path=args.db_path,
        opencode_base_url=args.opencode_url,
        opencode_endpoint=args.opencode_endpoint,
        window_seconds=args.window,
        max_sessions=args.max_sessions,
        opencode_password=password,
    )

    indent = 2 if args.pretty else None
    json.dump(dataset, sys.stdout, indent=indent, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
