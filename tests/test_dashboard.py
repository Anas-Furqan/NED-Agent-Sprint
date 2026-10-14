"""Dashboard: helpers without a browser, and the real script driven with streamlit's AppTest.

The Live Run tests run agent.py for real as a subprocess, in offline mode (HELPDESK_AGENT_ARGS=--offline),
so no LLM is called. Everything happens in a temporary inbox/outbox; the real data/ is never touched.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from helpdesk import dashboard_data as dd
from helpdesk.lock import FileLock, LockBusy
from helpdesk.pipeline import Runner, TriageAgent
from helpdesk.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = load_policy(ROOT / "data" / "policy.md")
ENTRY = {"email_id": "A1", "category": "hostel", "priority": "high", "escalate": True,
         "escalation_reason": "Needs human action: safety hazard (ESC-1, HOSTEL-3).",
         "policy_refs": ["HOSTEL-3", "ESC-1"], "reply_draft": "Dear Ali,\n\nStay away.\n\nRegards,\nStudent Helpdesk",
         "confidence": 0.9, "agent_notes": "x | language: roman_urdu"}


# ---------------- helpers ----------------
def test_build_rows_merges_trace_and_survives_missing_parts():
    trace = {"emails": {"A1": {"email": {"from_name": "Ali", "subject": "Spark", "body": "<b>sparks</b>"},
                               "cache_hit": True, "model": "m", "steps": [{"n": 1, "name": "Load"}], "duration_ms": 1200}}}
    rows = dd.build_rows([ENTRY, {"broken": True}, {"email_id": "P1"}], trace, {}, POLICY)
    assert [r["email_id"] for r in rows] == ["A1", "P1"]  # entry without email_id skipped, partial one filled
    assert rows[0]["from_name"] == "Ali" and rows[0]["cache_hit"] and rows[0]["language"] == "roman_urdu"
    assert rows[0]["routes"] == ["Hostel Warden and Security"]  # derived from the ESC-1 rule text
    assert rows[1]["category"] == "unclear" and rows[1]["policy_refs"] == []
    assert dd.build_rows(None, None, {}, None) == []


def test_routing_and_filters_and_kpis():
    assert dd.routes_for({"escalate": True, "policy_refs": ["ESC-2"]}, POLICY) == ["Harassment Committee"]
    rows = dd.build_rows([ENTRY, {**ENTRY, "email_id": "A2", "priority": "low", "escalate": False, "category": "fee",
                                  "agent_notes": "FALLBACK"}], {}, {}, POLICY)
    assert [r["email_id"] for r in dd.filter_rows(rows, escalated_only=True)] == ["A1"]
    assert [r["email_id"] for r in dd.filter_rows(rows, fallback_only=True)] == ["A2"]
    assert dd.kpis(rows)["escalated"] == 1 and dd.escalation_code_counts(rows, POLICY) == {"ESC-1": 1}


def test_live_ids_validation_and_safe_names(tmp_path):
    (tmp_path / "E001.json").write_text(json.dumps({"id": "E001", "body": "x"}), encoding="utf-8")
    path, errors = dd.save_new_email(tmp_path, {"from_name": "Sara", "from_email": "s@example.com", "subject": "Wi-Fi",
                                                "body": "<b>Wi-Fi</b> is down"})
    assert not errors and path.name == "LIVE-001.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["id"] == "LIVE-001" and saved["body"] == "Wi-Fi is down"  # HTML stripped
    assert dd.next_free_id(tmp_path) == "LIVE-002"
    _, errors = dd.save_new_email(tmp_path, {"subject": "x", "body": ""})
    assert "body is required" in errors
    _, errors = dd.save_new_email(tmp_path, {"body": "x" * 20000, "from_email": "not-an-email"})
    assert any("too long" in e for e in errors) and any("email" in e for e in errors)


@pytest.mark.parametrize("name,content,ok", [
    ("../../evil name.json", json.dumps({"id": "E001", "subject": "Fee", "body": "Voucher missing"}).encode(), True),
    ("bad.json", b"{not json", False),
    ("wrong.json", json.dumps({"title": "x", "message": "y"}).encode(), False),
    ("list.json", b"[1, 2]", False),
    ("mail.txt", b"From: Ali <a@example.com>\nSubject: Wi-Fi\n\nWi-Fi is down in Block C.", True),
    ("empty.json", b"", False),
])
def test_upload_parsing(tmp_path, name, content, ok):
    path, errors, notes = dd.save_upload(tmp_path, name, content)
    assert (path is not None) == ok
    if ok:
        assert path.parent == tmp_path and path.name.startswith("LIVE-")  # never the uploaded file name
        if b"E001" in content:
            assert any("replaced by a new LIVE id" in n for n in notes)
    else:
        assert errors and not list(tmp_path.iterdir())


def test_clear_live_emails(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "E001.json").write_text("{}", encoding="utf-8")
    (inbox / "LIVE-001.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "outbox.json"
    out.write_text(json.dumps([{"email_id": "E001"}, {"email_id": "LIVE-001"}]), encoding="utf-8")
    res = dd.clear_live_emails(inbox, out, dd_trace(out))
    assert res["files"] == 1 and [e["email_id"] for e in json.loads(out.read_text())] == ["E001"]
    assert [p.name for p in inbox.iterdir()] == ["E001.json"]


def dd_trace(out):
    from helpdesk.pipeline import trace_path_for

    return trace_path_for(out)


def test_summarize_run_messages():
    lines = ["    model 'gemini-a' is out of daily quota -> switching to 'gemini-b'",
             "Processed: 1 | escalated: 0 | LLM fallback: 0 | invalid input: 0 | cache hits: 0 | LLM calls: 2 | models: x",
             "[agent exited with code 0]"]
    levels = [m[0] for m in dd.summarize_run(lines)]
    assert levels == ["info", "success"]
    crash = dd.summarize_run(["Traceback (most recent call last):", "ValueError: boom", "[agent exited with code 1]"])
    assert crash[-1][0] == "error" and "ValueError: boom" in crash[-1][1]
    assert dd.summarize_run(["[stopped: the agent took longer than 5s and was stopped]",
                             "[agent exited with code 1]"])[0][0] == "error"
    assert "offline" in dd.summarize_run(["Offline mode: rule-based fallback only"])[0][1]


def test_stream_timeout_kills_the_agent():
    lines = list(dd.run_agent_stream(["--watch", "--offline", "--inbox", str(ROOT / "tests" / "fixtures"),
                                      "--out", str(ROOT / ".pytest_tmp" / "watch_outbox.json"),
                                      "--log", str(ROOT / ".pytest_tmp" / "watch.log")], timeout=3))
    assert any("[stopped:" in line for line in lines)


# ---------------- lock ----------------
def test_lock_blocks_concurrent_runs(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    shutil.copy(ROOT / "data" / "inbox" / "E001.json", inbox)
    out = tmp_path / "outbox.json"
    with FileLock(out.with_name(".outbox.lock")):
        with pytest.raises(LockBusy):
            Runner(TriageAgent(POLICY, None, 0), inbox, out).run(lock_timeout=0.5)
        cli = subprocess.run([sys.executable, str(ROOT / "agent.py"), "--offline", "--inbox", str(inbox), "--out",  # noqa: S603
                              str(out), "--log", str(tmp_path / "a.log")], capture_output=True, text=True,
                             timeout=200, cwd=ROOT)
        assert cli.returncode == 3 and "in progress" in cli.stdout + cli.stderr
    Runner(TriageAgent(POLICY, None, 0), inbox, out).run()  # works once the lock is released
    assert not out.with_name(".outbox.lock").exists()


# ---------------- AppTest: the real dashboard script ----------------
@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in ("E001.json", "E005.json", "E012.json"):
        shutil.copy(ROOT / "data" / "inbox" / name, inbox)
    paths = {"inbox": inbox, "outbox": tmp_path / "outbox.json"}
    monkeypatch.setenv("HELPDESK_INBOX", str(inbox))
    monkeypatch.setenv("HELPDESK_OUTBOX", str(paths["outbox"]))
    monkeypatch.setenv("HELPDESK_EDGE_OUTBOX", str(tmp_path / "missing_edge.json"))
    monkeypatch.setenv("HELPDESK_AGENT_ARGS", "--offline")
    monkeypatch.setenv("HELPDESK_LOG", str(tmp_path / "agent.log"))  # path with a space ("THIS PC")
    return paths


def app(**query) -> AppTest:
    at = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=120)
    for k, v in query.items():
        at.query_params[k] = v
    return at.run()


def no_errors(at):
    assert not at.exception, [e.value for e in at.exception]


def test_all_tabs_without_outbox(env):
    at = app()
    no_errors(at)
    assert len(at.tabs) == 6


def test_all_tabs_with_normal_outbox(env):
    Runner(TriageAgent(POLICY, None, 0), env["inbox"], env["outbox"]).run()
    at = app()
    no_errors(at)
    assert any("Needs a human" in m.value for m in at.markdown)


@pytest.mark.parametrize("content", ["{broken", "[{\"email_id\": \"E001\"}]", "[1, 2, 3]", ""])
def test_corrupt_or_partial_outbox(env, content):
    env["outbox"].write_text(content, encoding="utf-8")
    no_errors(app())


@pytest.mark.parametrize("view", ["overview", "escalations", "inbox", "live", "edge", "history"])
def test_each_view_deep_link(env, view):
    Runner(TriageAgent(POLICY, None, 0), env["inbox"], env["outbox"]).run()
    no_errors(app(view=view))


def test_live_form_end_to_end(env):
    Runner(TriageAgent(POLICY, None, 0), env["inbox"], env["outbox"]).run()
    before = {e["email_id"] for e in json.loads(env["outbox"].read_text(encoding="utf-8"))}
    at = app(view="live")
    at.text_input(key="live_subject").input("Wi-Fi down in Block C")
    at.text_area(key="live_body").input("The Wi-Fi in Hostel Block C, 2nd floor has not worked since morning. "
                                        "Who should I report it to?")
    at.button(key="live_submit").click().run()
    no_errors(at)
    assert (env["inbox"] / "LIVE-001.json").exists()
    after = {e["email_id"] for e in json.loads(env["outbox"].read_text(encoding="utf-8"))}
    assert after - before == {"LIVE-001"} and before <= after  # exactly one new entry
    runs = json.loads(env["outbox"].with_name("run_trace.json").read_text(encoding="utf-8"))["runs"]
    assert runs[-1]["mode"] == "file" and runs[-1]["processed"] == 1  # only that file was processed
    assert at.query_params["view"] == ["inbox"] or at.query_params["view"] == "inbox"
    assert any("LIVE-001" in str(s.value) for s in at.selectbox)  # detail view opened on the new email


def test_live_form_rejects_empty_body(env):
    at = app(view="live")
    at.text_input(key="live_subject").input("only a subject")
    at.button(key="live_submit").click().run()
    no_errors(at)
    assert any("body is required" in w.value for w in at.warning)
    assert not list(env["inbox"].glob("LIVE-*"))


@pytest.mark.parametrize("name,content,ok", [
    ("good.json", json.dumps({"id": "E001", "from_name": "Ali", "subject": "Voucher", "body": "My fee voucher is missing"}).encode(), True),
    ("bad.json", b"{not json", False),
    ("fields.json", json.dumps({"foo": "bar"}).encode(), False),
])
def test_live_upload(env, name, content, ok):
    at = app(view="live")
    at.file_uploader(key="live_upload").upload(name, content, "application/json").run()
    at.button(key="live_upload_go").click().run()
    no_errors(at)
    created = list(env["inbox"].glob("LIVE-*"))
    assert bool(created) == ok
    if not ok:
        assert any("Upload rejected" in e.value for e in at.error)


def test_clear_live_button(env):
    dd.save_new_email(env["inbox"], {"subject": "x", "body": "My LMS account is locked"})
    at = app(view="live")
    at.checkbox(key="live_clear_ok").check().run()
    at.button(key="live_clear").click().run()
    no_errors(at)
    assert not list(env["inbox"].glob("LIVE-*"))


def test_submission_refuses_demo_leftovers(tmp_path):
    from make_submission import submission_problems

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "E001.json").write_text(json.dumps({"id": "E001", "body": "x"}), encoding="utf-8")
    out = tmp_path / "outbox.json"
    out.write_text(json.dumps([{"email_id": "E001"}]), encoding="utf-8")
    assert submission_problems(inbox, out) == []
    (inbox / "LIVE-001.json").write_text(json.dumps({"id": "LIVE-001", "body": "x"}), encoding="utf-8")
    out.write_text(json.dumps([{"email_id": "E001"}, {"email_id": "E999"}, {"email_id": "LIVE-001"}]), encoding="utf-8")
    problems = " ".join(submission_problems(inbox, out))
    assert "LIVE-001.json" in problems and "E999" in problems and "live test entries" in problems
