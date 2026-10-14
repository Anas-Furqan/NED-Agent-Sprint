"""Dashboard helpers (no browser) + a headless render of dashboard.py."""
from __future__ import annotations

import json
from pathlib import Path

from helpdesk import dashboard_data as dd
from helpdesk.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = load_policy(ROOT / "data" / "policy.md")

ENTRY = {"email_id": "A1", "category": "hostel", "priority": "high", "escalate": True,
         "escalation_reason": "Needs human action: safety hazard (ESC-1, HOSTEL-3).",
         "policy_refs": ["HOSTEL-3", "ESC-1"], "reply_draft": "Dear Ali,\n\nStay away.\n\nRegards,\nStudent Helpdesk",
         "confidence": 0.9, "agent_notes": "x | language: roman_urdu"}


def test_build_rows_merges_trace_and_survives_missing_parts():
    trace = {"emails": {"A1": {"email": {"from_name": "Ali", "subject": "Spark", "body": "<b>sparks</b>"},
                               "cache_hit": True, "model": "m", "steps": [{"n": 1, "name": "Load"}],
                               "duration_ms": 1200}}}
    rows = dd.build_rows([ENTRY, {"broken": True}], trace, {}, POLICY)
    assert len(rows) == 1
    r = rows[0]
    assert r["from_name"] == "Ali" and r["cache_hit"] and r["language"] == "roman_urdu"
    assert r["routes"] == ["Hostel Warden and Security"]  # trace has no routes -> derived from ESC-1 rule text
    rows2 = dd.build_rows([ENTRY], {"emails": {"A1": {"routes": ["Hostel Warden"]}}}, {}, POLICY)
    assert rows2[0]["routes"] == ["Hostel Warden"]  # trace routes win
    assert dd.build_rows(None, None, {}, None) == []


def test_routing_is_derived_from_policy_text():
    assert dd.routes_for({"escalate": True, "policy_refs": ["ESC-2"]}, POLICY) == ["Harassment Committee"]
    assert dd.routes_for({"escalate": True, "policy_refs": ["ESC-6"]}, POLICY) == ["IT Security"]
    assert dd.routes_for({"escalate": False, "policy_refs": ["ESC-6"]}, POLICY) == []


def test_filters_and_kpis():
    rows = dd.build_rows([ENTRY, {**ENTRY, "email_id": "A2", "priority": "low", "escalate": False,
                                  "category": "fee", "agent_notes": "FALLBACK"}], {}, {}, POLICY)
    assert [r["email_id"] for r in dd.filter_rows(rows, escalated_only=True)] == ["A1"]
    assert [r["email_id"] for r in dd.filter_rows(rows, fallback_only=True)] == ["A2"]
    assert [r["email_id"] for r in dd.filter_rows(rows, categories=["fee"])] == ["A2"]
    assert [r["email_id"] for r in dd.filter_rows(rows, query="stay away")] == ["A1", "A2"]
    k = dd.kpis(rows)
    assert k["total"] == 2 and k["escalated"] == 1 and k["fallback"] == 1
    assert dd.escalation_code_counts(rows, POLICY) == {"ESC-1": 1}


def test_next_id_and_saving(tmp_path):
    for i in (1, 7, 20):
        (tmp_path / f"E{i:03d}.json").write_text(json.dumps({"id": f"E{i:03d}", "body": "x"}), encoding="utf-8")
    assert dd.next_free_id(tmp_path) == "E021"
    path = dd.save_new_email(tmp_path, "Sara", "s@example.com", "Wi-Fi", "Wi-Fi is down", "")
    assert path.name == "E021.json" and json.loads(path.read_text(encoding="utf-8"))["id"] == "E021"
    up1 = dd.save_uploaded(tmp_path, "../../evil name.json", b"{}")
    up2 = dd.save_uploaded(tmp_path, "../../evil name.json", b"{}")
    assert up1.parent == tmp_path and up1 != up2  # safe name, never overwrites


def test_status_pill():
    assert dd.status_pill({})[0] == "NO RUNS YET"
    assert dd.status_pill({"runs": [{"offline": True}]})[1] == "grey"
    assert dd.status_pill({"runs": [{"offline": False, "fallback": 2}]})[1] == "amber"


def test_dashboard_renders_headless():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.tabs) == 5
