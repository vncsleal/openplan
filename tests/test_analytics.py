from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.analytics import compute_analytics, _mean, _stdev, _trend
from openplan.db.schema import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    yield conn
    conn.close()


def _make_node(conn: sqlite3.Connection, project: str) -> str:
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 AS next FROM nodes"
    ).fetchone()
    sid = f"S-{row['next']:06d}"
    conn.execute("INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (sid, "", project))
    return sid


def _edge(conn: sqlite3.Connection, source: str, target: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, 'implement', 10000, 0.1, 0.8)",
        (source, target),
    )


def test_mean_empty() -> None:
    assert _mean([]) == 0.0


def test_mean_values() -> None:
    assert _mean([1.0, 2.0, 3.0]) == 2.0


def test_stdev_single() -> None:
    assert _stdev([5.0], 5.0) == 0.0


def test_stdev_values() -> None:
    r = _stdev([1.0, 2.0, 3.0], 2.0)
    assert round(r, 4) == 0.8165


def test_trend_no_previous() -> None:
    assert _trend(0.5, None, "lower") == "stable"


def test_trend_small_diff() -> None:
    assert _trend(0.5, 0.51, "lower") == "stable"


def test_trend_improving_lower() -> None:
    assert _trend(0.1, 0.5, "lower") == "improving"


def test_trend_degrading_lower() -> None:
    assert _trend(0.5, 0.1, "lower") == "degrading"


def test_trend_improving_higher() -> None:
    assert _trend(0.8, 0.3, "higher") == "improving"


def test_trend_degrading_higher() -> None:
    assert _trend(0.3, 0.8, "higher") == "degrading"


def test_compute_analytics_empty(conn: sqlite3.Connection) -> None:
    assert compute_analytics(conn) == {"total_projects": 0, "projects": [], "anomalies": []}


def test_compute_analytics_single_project(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, "proj-a")
    _edge(conn, a, _make_node(conn, "proj-a"))
    result = compute_analytics(conn)
    assert result["total_projects"] == 1
    assert result["projects"][0]["project"] == "proj-a"
    assert result["anomalies"] == []


def test_compute_analytics_single_node(conn: sqlite3.Connection) -> None:
    _make_node(conn, "proj-a")
    result = compute_analytics(conn)
    assert result["projects"][0]["orphan_rate"] == 0.0
    assert result["projects"][0]["calibration_rate"] == 0.0


def test_compute_analytics_calibrated_edge(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, "proj-a")
    b = _make_node(conn, "proj-a")
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, weight_history, cost_tokens, cost_risk, prob) VALUES (?, ?, 'implement', '[{\"insight\":\"test\"}]', 10000, 0.1, 0.8)",
        (a, b),
    )
    result = compute_analytics(conn)
    assert result["projects"][0]["calibration_rate"] == 1.0


def test_compute_analytics_snapshot_trend(conn: sqlite3.Connection) -> None:
    root = _make_node(conn, "proj-a")
    _edge(conn, root, _make_node(conn, "proj-a"))
    compute_analytics(conn)
    _edge(conn, root, _make_node(conn, "proj-a"))
    _edge(conn, root, _make_node(conn, "proj-a"))
    result = compute_analytics(conn)
    assert result["projects"][0]["orphan_trend"] != "stable"


def test_compute_analytics_snapshot_saved(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, "proj-a")
    _edge(conn, a, _make_node(conn, "proj-a"))
    compute_analytics(conn)
    row = conn.execute("SELECT value FROM meta WHERE key = 'health_snapshot'").fetchone()
    assert row is not None
    snapshot = json.loads(row["value"])
    assert "proj-a" in snapshot


def test_compute_analytics_anomaly_detection(conn: sqlite3.Connection) -> None:
    for proj in ("normal-a", "normal-b", "normal-c", "normal-d"):
        root = _make_node(conn, proj)
        _edge(conn, root, _make_node(conn, proj))
    outlier_root = _make_node(conn, "outlier")
    for _ in range(9):
        _make_node(conn, "outlier")
    result = compute_analytics(conn)
    assert result["total_projects"] == 5
    anomaly_projects = [a["project"] for a in result["anomalies"]]
    assert "outlier" in anomaly_projects


def test_compute_analytics_few_projects_no_anomaly(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, "proj-a")
    b = _make_node(conn, "proj-b")
    _edge(conn, a, _make_node(conn, "proj-a"))
    _edge(conn, b, _make_node(conn, "proj-b"))
    result = compute_analytics(conn)
    assert result["anomalies"] == []


def test_compute_analytics_bad_snapshot(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('health_snapshot', 'not-json')"
    )
    a = _make_node(conn, "proj-a")
    _edge(conn, a, _make_node(conn, "proj-a"))
    result = compute_analytics(conn)
    assert result["total_projects"] == 1
    assert result["projects"][0]["orphan_trend"] == "stable"
