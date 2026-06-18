import sqlite3
import os

_conn: sqlite3.Connection | None = None


def get_connection(path: str | None = None) -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    db_path = path or os.environ.get(
        "OPENPLAN_DB_PATH",
        os.path.expanduser("~/.local/share/openplan/data.db"),
    )
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    _conn = sqlite3.connect(db_path)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def close():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
