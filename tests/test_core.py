import sqlite3
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openplan.db.schema import init_db, tokenize
from openplan.core.costs import estimate_cost, compute_personal_bias
from openplan.core.planner import plan_project
from openplan.core.tracker import checkpoint_phase, get_route_status
from openplan.core.reviewer import review_route
from openplan import VERSION


def get_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


class TestTokenize:
    def test_basic(self):
        assert tokenize("Build a landing page with Stripe") == "landing page stripe"

    def test_stop_words_removed(self):
        assert tokenize("the and for with using build implement") == ""

    def test_short_tokens_removed(self):
        assert tokenize("a an on at to is it") == ""

    def test_special_chars(self):
        result = tokenize("Next.js + Tailwind, deployed to Vercel!")
        assert "next.js" in result or "next" in result


class TestEstimateCost:
    def test_action_fallback(self):
        conn = get_conn()
        cost, clo, chi, level, samples = estimate_cost(conn, "implement", "", "test phase")
        assert cost == 2000.0
        assert level == "action"

    def test_exact_match_with_data(self):
        conn = get_conn()
        conn.execute(
            "INSERT INTO calibration_events (id, action, phase_label_tokens, expected_cost, actual_cost, outcome, synced, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            ("test1", "implement", "auth setup", 2000, 1800, "success", 100.0),
        )
        conn.execute(
            "INSERT INTO calibration_events (id, action, phase_label_tokens, expected_cost, actual_cost, outcome, synced, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            ("test2", "implement", "auth setup jwt", 2000, 1500, "success", 100.0),
        )
        conn.execute(
            "INSERT INTO calibration_events (id, action, phase_label_tokens, expected_cost, actual_cost, outcome, synced, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            ("test3", "implement", "auth setup", 2000, 2100, "partial", 100.0),
        )
        conn.execute(
            "INSERT INTO calibration_events (id, action, phase_label_tokens, expected_cost, actual_cost, outcome, synced, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            ("test4", "implement", "auth setup", 2000, 1900, "success", 100.0),
        )
        conn.execute(
            "INSERT INTO calibration_events (id, action, phase_label_tokens, expected_cost, actual_cost, outcome, synced, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
            ("test5", "implement", "auth setup", 2000, 1700, "success", 100.0),
        )
        conn.commit()

        cost, clo, chi, level, samples = estimate_cost(
            conn, "implement", "authentication", "Auth (Better Auth) setup"
        )
        # Should find exact match via 'auth' keyword in both goal_tokens and label_tokens
        assert samples >= 5
        assert level in ("exact", "label_keyword")

    def test_no_data_returns_fallback(self):
        conn = get_conn()
        cost, clo, chi, level, samples = estimate_cost(
            conn, "design", "", "UI mockup design"
        )
        assert level == "action"
        assert cost > 0


class TestPlan:
    def test_basic_plan(self):
        conn = get_conn()
        result = plan_project(conn, "Build a landing page", context="Next.js + Tailwind")
        assert "route_id" in result
        assert len(result["phases"]) > 0
        assert result["total_cost"] > 0


class TestCheckpoint:
    def test_checkpoint_and_review(self):
        conn = get_conn()
        result = plan_project(conn, "Build a CLI tool", api_key="test_key")
        route_id = result["route_id"]

        chk = checkpoint_phase(conn, route_id, result["phases"][0]["label"], 1500)
        assert chk["route_completed"] is False
        assert chk["deviation"]["ratio"] <= 1.3
        assert chk["next_phase"] is not None

    def test_complete_all_phases(self):
        conn = get_conn()
        result = plan_project(conn, "Simple project")
        route_id = result["route_id"]

        for i, p in enumerate(result["phases"]):
            is_last = i == len(result["phases"]) - 1
            chk = checkpoint_phase(conn, route_id, p["label"], p["expected_cost"])
            if is_last:
                assert chk["route_completed"] is True
            else:
                assert chk["next_phase"] is not None

    def test_status_check(self):
        conn = get_conn()
        result = plan_project(conn, "Test project", api_key="test_key")
        route_id = result["route_id"]

        status = get_route_status(conn, route_id)
        assert status["status"] == "active"


class TestReview:
    def test_review_after_completion(self):
        conn = get_conn()
        result = plan_project(conn, "Review test", api_key="test_key")
        route_id = result["route_id"]

        for p in result["phases"]:
            checkpoint_phase(conn, route_id, p["label"], p["expected_cost"])

        rev = review_route(conn, route_id, api_key="test_key")
        assert rev["summary"]["accuracy"] > 0
        assert rev["summary"]["phases_completed"] == len(result["phases"])


class TestVersion:
    def test_version(self):
        assert VERSION == "0.9.0"
