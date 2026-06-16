from __future__ import annotations

import math
import os
import time
from typing import Any


class _TursoAdapter:
    """Wraps libsql_client to match sqlite3.Connection interface."""
    def __init__(self, url: str, token: str) -> None:
        import libsql_client
        self._client = libsql_client.create_client_sync(url=url, auth_token=token)

    def execute(self, sql: str, params: tuple = ()) -> _TursoResult:
        rs = self._client.execute(sql, params)
        return _TursoResult(rs)

    def executescript(self, sql: str) -> None:
        for stmt in sql.strip().split(";"):
            s = stmt.strip()
            if s:
                self._client.execute(s)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self._client.close()


class _TursoResult:
    def __init__(self, rs: Any) -> None:
        self._rows = rs.rows if hasattr(rs, "rows") else []

    def fetchone(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return self._rows


TURSO_URL = os.environ.get("OPENPLAN_DB_URL", "")
if TURSO_URL:
    from functools import partial
    _create_conn = partial(_TursoAdapter, TURSO_URL, os.environ.get("OPENPLAN_DB_TOKEN", ""))
else:
    import sqlite3
    _create_conn = lambda: sqlite3.connect(os.environ.get("OPENPLAN_DB_PATH", "telemetry.db"))


def get_conn():
    conn = _create_conn()
    if not TURSO_URL and hasattr(conn, "row_factory"):
        conn.row_factory = sqlite3.Row
    return conn