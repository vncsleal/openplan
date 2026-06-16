from __future__ import annotations

import json
import math
import os
import time
from typing import Any

import httpx


class _TursoHTTP:
    """Turso via REST API (no native driver needed)."""
    def __init__(self, url: str, token: str) -> None:
        self._url = url.replace("libsql://", "https://")
        self._auth = f"Bearer {token}"
        self._client = httpx.Client()

    def _exec(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        stmt: dict[str, Any] = {"sql": sql}
        if params:
            stmt["args"] = [{"type": "text", "value": str(p)} for p in params]
        resp = self._client.post(
            f"{self._url}/v2/pipeline",
            json={"requests": [{"type": "execute", "stmt": stmt}]},
            headers={"Authorization": self._auth},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for result in data.get("results", []):
            cols = [c["name"] for c in result.get("cols", [])]
            for row_data in result.get("rows", []):
                rows.append(dict(zip(cols, [r.get("value") for r in row_data])))
        return rows

    def execute(self, sql: str, params: tuple = ()) -> _TursoHTTPResult:
        return _TursoHTTPResult(self._exec(sql, params))

    def executescript(self, sql: str) -> None:
        for stmt in sql.strip().split(";"):
            s = stmt.strip()
            if s:
                self._exec(s)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self._client.close()


class _TursoHTTPResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


TURSO_URL = os.environ.get("OPENPLAN_DB_URL", "")
if TURSO_URL:
    _create_conn = lambda: _TursoHTTP(TURSO_URL, os.environ.get("OPENPLAN_DB_TOKEN", ""))
else:
    import sqlite3
    _create_conn = lambda: sqlite3.connect(os.environ.get("OPENPLAN_DB_PATH", "telemetry.db"))


def get_conn():
    conn = _create_conn()
    if not TURSO_URL and hasattr(conn, "row_factory"):
        conn.row_factory = sqlite3.Row
    return conn