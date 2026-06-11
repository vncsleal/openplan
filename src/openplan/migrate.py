from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys


def migrate_v3_to_v31(db_path: str, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")

    changes = {"schema": [], "events": 0, "idempotency_keys": 0, "errors": []}

    if dry_run:
        print(f"[DRY RUN] Would migrate {db_path}")

    # 1. Add session_id column to events
    try:
        conn.execute("ALTER TABLE events ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
        changes["schema"].append("events.session_id")
        if not dry_run:
            print("  + Added events.session_id")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e):
            changes["schema"].append("events.session_id (already exists)")
        else:
            changes["errors"].append(f"session_id: {e}")

    # 2. Add idempotency_key column to events
    try:
        conn.execute("ALTER TABLE events ADD COLUMN idempotency_key TEXT")
        changes["schema"].append("events.idempotency_key")
        if not dry_run:
            print("  + Added events.idempotency_key")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e):
            changes["schema"].append("events.idempotency_key (already exists)")
        else:
            changes["errors"].append(f"idempotency_key: {e}")

    # 3. Populate idempotency_keys for existing rows
    if not dry_run:
        import hashlib

        rows = conn.execute(
            "SELECT id, node_id, event_type, payload FROM events WHERE idempotency_key IS NULL"
        ).fetchall()
        for row in rows:
            action = ""
            try:
                payload = json.loads(row["payload"])
                action = payload.get("action", "")
            except (json.JSONDecodeError, TypeError):
                pass
            raw = f"{row['node_id']}:{row['event_type']}:{action}"
            ikey = hashlib.sha256(raw.encode()).hexdigest()[:32]
            conn.execute(
                "UPDATE events SET idempotency_key = ? WHERE id = ?",
                (ikey, row["id"]),
            )
            changes["idempotency_keys"] += 1
        print(f"  + Populated {changes['idempotency_keys']} idempotency keys")

    # 4. Indexes
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_events_idempotency ON events(idempotency_key)",
        "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)",
    ]
    for idx_sql in indexes:
        try:
            conn.execute(idx_sql)
            name = idx_sql.split()[-1]
            changes["schema"].append(f"index {name}")
            if not dry_run:
                print(f"  + Created index {name}")
        except Exception as e:
            changes["errors"].append(f"index: {e}")

    # 5. Create events_archive if missing
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events_archive (
                id              TEXT PRIMARY KEY,
                project         TEXT NOT NULL,
                node_id         TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                payload         TEXT NOT NULL DEFAULT '{}',
                version         INTEGER NOT NULL DEFAULT 1,
                idempotency_key TEXT,
                session_id      TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
            )
        """)
        changes["schema"].append("events_archive")
        if not dry_run:
            print("  + Ensured events_archive table")
    except Exception as e:
        changes["errors"].append(f"events_archive: {e}")

    conn.execute("PRAGMA foreign_keys = ON")
    if not dry_run:
        conn.commit()
    conn.close()
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate OpenPlan v3 database to v3.1")
    parser.add_argument("--db", default="openplan.db", help="Path to the v3 database")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    print(f"OpenPlan v3 → v3.1 Migration")
    print(f"Database: {args.db}")
    print()

    changes = migrate_v3_to_v31(args.db, dry_run=args.dry_run)

    print()
    print("Summary:")
    print(f"  Schema changes: {len(changes['schema'])}")
    for s in changes["schema"]:
        print(f"    - {s}")
    if not args.dry_run:
        print(f"  Idempotency keys written: {changes['idempotency_keys']}")
    if changes["errors"]:
        print(f"  Errors: {len(changes['errors'])}")
        for e in changes["errors"]:
            print(f"    - {e}")

    success = len(changes["errors"]) == 0
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
