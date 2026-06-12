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

from openplan.core.rlhf import build_rlhf_dataset


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
        default="/session/{id}/messages",
        help="Endpoint template for session messages. Use {id} as placeholder for session ID "
             "(default: /session/{id}/messages)",
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
    args = parser.parse_args()

    dataset = build_rlhf_dataset(
        db_path=args.db_path,
        opencode_base_url=args.opencode_url,
        opencode_endpoint=args.opencode_endpoint,
        window_seconds=args.window,
        max_sessions=args.max_sessions,
    )

    indent = 2 if args.pretty else None
    json.dump(dataset, sys.stdout, indent=indent, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
