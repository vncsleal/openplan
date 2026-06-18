import sqlite3
from datetime import datetime, timezone

from openplan.db.schema import tokenize

DEFAULT_COST_PER_ACTION = {
    "implement": 2000.0,
    "design": 1500.0,
    "test": 800.0,
    "deploy": 600.0,
}

DEFAULT_CI: dict[str, tuple[float, float]] = {
    "implement": (500.0, 5000.0),
    "design": (500.0, 4000.0),
    "test": (400.0, 1500.0),
    "deploy": (300.0, 1000.0),
}


def estimate_cost(
    conn: sqlite3.Connection,
    action: str,
    goal_tokens: str,
    phase_label: str,
    api_key: str | None = None,
) -> tuple[float, float, float, str, int]:
    label_tokens = tokenize(phase_label)

    # Level 1: Phase label keyword match (5+ samples)
    if label_tokens:
        for token in label_tokens.split():
            row = conn.execute(
                """SELECT AVG(actual_cost) as avg_c, COUNT(*) as cnt,
                          MIN(actual_cost) as lo, MAX(actual_cost) as hi
                   FROM calibration_events
                   WHERE action = ? AND phase_label_tokens LIKE ?
                   HAVING cnt >= 5""",
                (action, f"%{token}%"),
            ).fetchone()
            if row and row["cnt"] >= 5:
                avg = row["avg_c"]
                spread = (row["hi"] - row["lo"]) / 2 if row["hi"] > row["lo"] else avg * 0.5
                return (round(avg, 1), round(max(0, avg - spread), 1), round(avg + spread, 1), "label_keyword", row["cnt"])

    # Level 2: Action fallback
    row = conn.execute(
        "SELECT avg_cost, ci_lo, ci_hi, sample_count FROM cost_baselines WHERE match_level = 'action' AND action = ?",
        (action,),
    ).fetchone()
    if row:
        return (row["avg_cost"], row["ci_lo"], row["ci_hi"], "action", row["sample_count"])

    default_cost = DEFAULT_COST_PER_ACTION.get(action, 2000.0)
    default_ci = DEFAULT_CI.get(action, (500.0, 5000.0))
    return (default_cost, default_ci[0], default_ci[1], "fallback", 0)


def compute_personal_bias(conn: sqlite3.Connection, api_key: str) -> float:
    row = conn.execute(
        "SELECT AVG(actual_cost / expected_cost) as bias FROM calibration_events WHERE api_key = ? AND expected_cost > 0",
        (api_key,),
    ).fetchone()
    if row and row["bias"]:
        return round(row["bias"], 4)
    return 1.0


def update_bias_for_checkpoint(
    conn: sqlite3.Connection,
    api_key: str,
    expected_cost: float,
    actual_cost: float,
) -> None:
    conn.execute(
        "INSERT INTO calibration_events (id, action, phase_label_tokens, expected_cost, actual_cost, outcome, project, session_id, api_key, synced, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (
            f"chk_{datetime.now(timezone.utc).timestamp()}",
            "implement",
            "",
            expected_cost,
            actual_cost,
            _derive_outcome(expected_cost, actual_cost),
            "",
            "",
            api_key,
            datetime.now(timezone.utc).timestamp(),
        ),
    )


def _derive_outcome(expected: float, actual: float) -> str:
    ratio = actual / expected if expected > 0 else 1.0
    if ratio <= 1.3:
        return "success"
    elif ratio <= 2.0:
        return "partial"
    return "failure"
