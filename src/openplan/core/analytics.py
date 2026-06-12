from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from openplan.core.graph import _graph_health


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _load_snapshot(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    row = conn.execute("SELECT value FROM meta WHERE key = 'health_snapshot'").fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return {}


def _save_snapshot(conn: sqlite3.Connection, data: dict[str, dict[str, float]]) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('health_snapshot', ?)", (json.dumps(data),))


def _trend(current: float, previous: float | None, better: str) -> str:
    if previous is None:
        return "stable"
    diff = current - previous
    if abs(diff) < 0.05:
        return "stable"
    if better == "lower":
        return "improving" if diff < 0 else "degrading"
    return "improving" if diff > 0 else "degrading"


def compute_analytics(conn: sqlite3.Connection) -> dict[str, Any]:
    projects = conn.execute("SELECT DISTINCT project FROM nodes").fetchall()
    if not projects:
        return {"total_projects": 0, "projects": [], "anomalies": []}

    previous = _load_snapshot(conn)

    projects_data: list[dict[str, Any]] = []
    for row in projects:
        p = row["project"]
        health = _graph_health(p, conn)
        orphan_rate = round(health["orphan_count"] / max(health["state_count"], 1), 4)
        cal_rate = round(health["calibration_count"] / max(health["edge_count"], 1), 4) if health["edge_count"] > 0 else 0.0
        prev = previous.get(p)
        projects_data.append({
            "project": p,
            "state_count": health["state_count"],
            "edge_count": health["edge_count"],
            "orphan_rate": orphan_rate,
            "calibration_rate": cal_rate,
            "max_depth": health["max_depth"],
            "issue_count": len(health["issues"]),
            "orphan_trend": _trend(orphan_rate, prev["orphan_rate"] if prev else None, "lower"),
            "calibration_trend": _trend(cal_rate, prev["calibration_rate"] if prev else None, "higher"),
        })

    anomaly_threshold = 2.0
    anomalies: list[dict[str, Any]] = []
    if len(projects_data) >= 3:
        orphan_rates = [p["orphan_rate"] for p in projects_data]
        cal_rates = [p["calibration_rate"] for p in projects_data]
        orphan_mean = _mean(orphan_rates)
        cal_mean = _mean(cal_rates)
        orphan_sd = _stdev(orphan_rates, orphan_mean)
        cal_sd = _stdev(cal_rates, cal_mean)

        for p in projects_data:
            if orphan_sd > 0:
                oz = (p["orphan_rate"] - orphan_mean) / orphan_sd
                if oz > anomaly_threshold:
                    anomalies.append({"project": p["project"], "metric": "orphan_rate", "value": p["orphan_rate"], "zscore": round(oz, 2)})
            if cal_sd > 0:
                cz = (cal_mean - p["calibration_rate"]) / cal_sd
                if cz > anomaly_threshold:
                    anomalies.append({"project": p["project"], "metric": "calibration_rate", "value": p["calibration_rate"], "zscore": round(cz, 2)})

    snapshot = {p["project"]: {"orphan_rate": p["orphan_rate"], "calibration_rate": p["calibration_rate"]} for p in projects_data}
    _save_snapshot(conn, snapshot)

    return {"total_projects": len(projects_data), "projects": projects_data, "anomalies": anomalies}
