from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.telemetry import TelemetryTracker
from openplan.db.schema import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def tracker() -> TelemetryTracker:
    return TelemetryTracker()


def test_record_new_session(tracker: TelemetryTracker) -> None:
    tracker.record("session-1", "act", {"state": "S-001"})
    stats = tracker.get_session_stats("session-1")
    assert stats["calls"] == 1
    assert stats["tool_counts"] == {"act": 1}


def test_record_default_session(tracker: TelemetryTracker) -> None:
    tracker.record(None, "plan")
    stats = tracker.get_session_stats("")
    assert stats["calls"] == 1


def test_record_without_args(tracker: TelemetryTracker) -> None:
    tracker.record("s", "observe")
    stats = tracker.get_session_stats("s")
    assert stats["calls"] == 1


def test_record_multiple_tools(tracker: TelemetryTracker) -> None:
    tracker.record("s", "plan")
    tracker.record("s", "branch")
    tracker.record("s", "act")
    stats = tracker.get_session_stats("s")
    assert stats["calls"] == 3
    assert stats["tool_counts"] == {"plan": 1, "branch": 1, "act": 1}


def test_session_not_found(tracker: TelemetryTracker) -> None:
    assert tracker.get_session_stats("nonexistent") == {"calls": 0}


def test_stuck_detection_three_observes(tracker: TelemetryTracker) -> None:
    tracker.record("s", "observe")
    tracker.record("s", "observe")
    tracker.record("s", "observe")
    stats = tracker.get_session_stats("s")
    assert stats.get("stuck") is True
    assert stats["stuck_detail"] == "observe called 3+ times without act"


def test_stuck_reset_by_act(tracker: TelemetryTracker) -> None:
    tracker.record("s", "observe")
    tracker.record("s", "observe")
    tracker.record("s", "act")
    stats = tracker.get_session_stats("s")
    assert stats.get("stuck") is None


def test_suggestion_followed(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s", {"tool": "act"})
    tracker.record("s", "act")
    conv = tracker.get_suggestion_conversion("s")
    assert conv is not None
    assert conv["followed"] == 1
    assert conv["ignored"] == 0
    assert conv["rate"] == 1.0


def test_suggestion_ignored(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s", {"tool": "plan"})
    tracker.record("s", "act")
    conv = tracker.get_suggestion_conversion("s")
    assert conv is not None
    assert conv["followed"] == 0
    assert conv["ignored"] == 1
    assert conv["rate"] == 0.0


def test_suggestion_no_record(tracker: TelemetryTracker) -> None:
    assert tracker.get_suggestion_conversion("s") is None


def test_suggestion_conversion_in_stats(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s", {"tool": "act"})
    tracker.record("s", "act")
    tracker.record("s", "branch")
    stats = tracker.get_session_stats("s")
    assert "suggestion_conversion" in stats
    assert stats["suggestion_conversion"]["rate"] == 0.5


def test_global_conversion_rate(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s1", {"tool": "act"})
    tracker.record("s1", "act")
    tracker.record_suggestion("s2", {"tool": "observe"})
    tracker.record("s2", "plan")
    rate = tracker.get_global_conversion_rate()
    assert rate == 0.5


def test_global_conversion_rate_no_data(tracker: TelemetryTracker) -> None:
    assert tracker.get_global_conversion_rate() is None


def test_tool_conversion_rate(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s", {"tool": "act"})
    tracker.record("s", "act")
    rate = tracker.get_tool_conversion_rate("act")
    assert rate == 1.0


def test_tool_conversion_rate_no_data(tracker: TelemetryTracker) -> None:
    assert tracker.get_tool_conversion_rate("act") is None


def test_flush_to_events_no_conn(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s", {"tool": "act"})
    tracker.record("s", "act")
    tracker.flush_to_events()


def test_flush_to_events_inserts(conn: sqlite3.Connection, tracker: TelemetryTracker) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO nodes (id, label, project) VALUES ('__telemetry__', '', '__telemetry__')"
    )
    tracker.set_conn(conn)
    tracker.record_suggestion("s", {"tool": "act"})
    tracker.record("s", "act")
    tracker.flush_to_events()
    rows = conn.execute("SELECT * FROM events WHERE event_type = 'telemetry'").fetchall()
    assert len(rows) >= 1
    payload = json.loads(rows[0]["payload"])
    assert payload["session"] == "s"
    assert payload["tool"] == "act"
    assert payload["followed"] == 1


def test_flush_skips_empty_sessions(tracker: TelemetryTracker) -> None:
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    init_db(mem)
    tracker.set_conn(mem)
    tracker.flush_to_events()
    rows = mem.execute("SELECT * FROM events WHERE event_type = 'telemetry'").fetchall()
    assert len(rows) == 0
    mem.close()


def test_reload_from_events(conn: sqlite3.Connection, tracker: TelemetryTracker) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO nodes (id, label, project) VALUES ('__telemetry__', '', '__telemetry__')"
    )
    tracker.set_conn(conn)
    tracker.record_suggestion("s", {"tool": "act"})
    tracker.record("s", "act")
    tracker.flush_to_events()
    fresh = TelemetryTracker()
    fresh.set_conn(conn)
    fresh.reload_from_events()
    assert fresh.get_global_conversion_rate() == 1.0


def test_reload_from_events_no_conn(tracker: TelemetryTracker) -> None:
    tracker.reload_from_events()


def test_loop_transition_followed_multiple(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s", {"tool": "act"})
    tracker.record("s", "act")
    tracker.record("s", "act")
    tracker.record("s", "act")
    conv = tracker.get_suggestion_conversion("s")
    assert conv["followed"] == 3


def test_loop_transition_ignored_boundary(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s", {"tool": "observe"})
    tracker.record("s", "learn")
    conv = tracker.get_suggestion_conversion("s")
    assert conv["ignored"] == 1


def test_suggestion_with_no_tool_field(tracker: TelemetryTracker) -> None:
    tracker.record_suggestion("s", {"not_tool": "act"})
    tracker.record("s", "act")
    conv = tracker.get_suggestion_conversion("s")
    assert conv is None
