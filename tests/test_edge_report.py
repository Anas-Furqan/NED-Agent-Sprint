"""Edge-case evaluation data + anti-cheating guarantees."""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.edge_report import check, evaluate, to_markdown

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "tests" / "fixtures" / "edge"
AGENT_SOURCES = [ROOT / "agent.py", *(ROOT / "helpdesk").glob("*.py")]

ENTRY = {"email_id": "A", "category": "security", "priority": "high", "escalate": True,
         "escalation_reason": "x", "policy_refs": ["ESC-6"], "confidence": 0.9,
         "reply_draft": "Dear Student,\n\nWe cannot share that (ESC-6).\n\nRegards,\nStudent Helpdesk",
         "agent_notes": "language: english"}


def test_check_detects_every_kind_of_mismatch():
    assert check(ENTRY, {"category": ["security"], "escalate": True, "required_refs": ["ESC-6"]}) == []
    fails = check(ENTRY, {"category": ["fee"], "escalate": False, "priority": ["low"], "required_refs": ["FEE-1"],
                          "forbidden_refs": ["ESC-6"], "max_refs": 0, "must_not_contain": ["cannot share"],
                          "reply_contains_any": ["clinic"], "reply_starts": "Dear Ali", "notes_contain": ["urdu"],
                          "max_confidence": 0.5})
    assert len(fails) == 11
    assert check(None, {}) == ["no outbox entry"]


def test_every_edge_fixture_has_a_case_and_expectation():
    cases = json.loads((EDGE / "_cases.json").read_text(encoding="utf-8"))
    expected = json.loads((EDGE / "_expected.json").read_text(encoding="utf-8"))
    fixtures = {p.name for p in EDGE.iterdir() if p.suffix in (".json", ".txt", ".eml") and not p.name.startswith("_")}
    assert fixtures == set(cases) == set(expected)
    assert all(c["case"] and c["expected"] for c in cases.values())


def test_agent_never_reads_evaluation_files():
    for src in AGENT_SOURCES:
        text = src.read_text(encoding="utf-8")
        assert not re.search(r"_expected|_cases|edge_report", text), f"{src.name} references evaluation data"


def test_metadata_files_are_not_treated_as_emails():
    from helpdesk.loader import is_email_file

    assert not is_email_file(EDGE / "_cases.json") and not is_email_file(EDGE / "_expected.json")


def test_fixtures_never_live_in_the_real_inbox():
    inbox = {p.name for p in (ROOT / "data" / "inbox").iterdir()}
    fixture_names = {p.name for p in (ROOT / "tests" / "fixtures").rglob("*") if p.is_file()}
    assert not inbox & (fixture_names - {"README.md"})
    assert not any(n.startswith(("_", "LIVE")) for n in inbox)


def test_report_renders(tmp_path):
    out = tmp_path / "x_outbox.json"
    out.write_text(json.dumps([ENTRY | {"email_id": "X15_inj_base64"}]), encoding="utf-8")
    rows = evaluate(EDGE, out)
    md = to_markdown(rows)
    assert "X15_inj_base64.json" in md and "PASS" in md and "CHECK" in md  # the others have no entry here
