from __future__ import annotations

import json

import pytest

from openplan.core.reasoning import REASONING_FIELDS, STATUS_VALUES, VALID_TYPES, ReasoningPayload


def test_default_payload() -> None:
    p = ReasoningPayload()
    assert p.type == "action"
    assert p.question == ""
    assert p.alternatives == []
    assert p.tags == []


def test_from_props_filters_unknown() -> None:
    props = {"type": "decision", "question": "what?", "unknown_key": "should be dropped", "visit_count": 5}
    p = ReasoningPayload.from_props(props)
    assert p.type == "decision"
    assert p.question == "what?"
    assert not hasattr(p, "unknown_key")
    assert not hasattr(p, "visit_count")


def test_from_json() -> None:
    raw = json.dumps({"type": "result", "conclusion": "it worked", "tags": ["fix", "deadlock"]})
    p = ReasoningPayload.from_json(raw)
    assert p.type == "result"
    assert p.conclusion == "it worked"
    assert p.tags == ["fix", "deadlock"]


def test_from_json_invalid() -> None:
    p = ReasoningPayload.from_json("not json")
    assert p.type == "action"


def test_to_dict_roundtrip() -> None:
    p = ReasoningPayload(type="hypothesis", question="what if?", reasoning="let me check", evidence="file.py:42")
    d = p.to_dict()
    assert d["type"] == "hypothesis"
    assert d["question"] == "what if?"
    assert d["evidence"] == "file.py:42"


def test_merge_into_props_preserves_existing() -> None:
    existing = {"visit_count": 3, "boost": True}
    p = ReasoningPayload(type="decision", conclusion="fixed")
    merged = p.merge_into_props(existing)
    assert merged["visit_count"] == 3
    assert merged["boost"] is True
    assert merged["type"] == "decision"
    assert merged["conclusion"] == "fixed"


def test_merge_into_props_overwrites_reasoning_keys() -> None:
    existing = {"type": "question", "visit_count": 1}
    p = ReasoningPayload(type="decision", decision="change approach")
    merged = p.merge_into_props(existing)
    assert merged["type"] == "decision"
    assert merged["visit_count"] == 1


def test_validate_valid() -> None:
    for t in VALID_TYPES:
        p = ReasoningPayload(type=t)
        p.validate()


def test_validate_invalid() -> None:
    p = ReasoningPayload(type="not-a-valid-type")
    with pytest.raises(ValueError, match="Invalid reasoning type"):
        p.validate()


def test_status_values() -> None:
    assert "pending" in STATUS_VALUES
    assert "in_progress" in STATUS_VALUES
    assert "done" in STATUS_VALUES
    assert "blocked" in STATUS_VALUES
    assert "superseded" in STATUS_VALUES


def test_reasoning_fields_constant() -> None:
    assert "type" in REASONING_FIELDS
    assert "question" in REASONING_FIELDS
    assert "reasoning" in REASONING_FIELDS
    assert "tags" in REASONING_FIELDS
    assert "visit_count" not in REASONING_FIELDS


def test_empty_alternatives() -> None:
    p = ReasoningPayload()
    assert p.alternatives == []
    p = ReasoningPayload(alternatives=["A", "B"])
    assert p.alternatives == ["A", "B"]
