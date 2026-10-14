from pathlib import Path

from streamlit.testing.v1 import AppTest

from ned_agent.dashboard import filter_messages, metrics
from ned_agent.demo import demo_emails


def test_all_required_demo_scenarios_present():
    fixtures = demo_emails()
    ids = {item.message_id for item in fixtures}
    expected = {
        "demo-billing",
        "demo-refund",
        "demo-technical",
        "demo-angry",
        "demo-legal",
        "demo-security",
        "demo-injection",
        "demo-ambiguous",
        "demo-duplicate",
        "demo-empty",
        "demo-llm_failure",
        "demo-gmail_failure",
    }
    assert expected == ids
    assert all(item.source == "demo" for item in fixtures)
    failures = {item.message_id: item.demo_failure for item in fixtures if item.demo_failure}
    assert failures == {"demo-llm_failure": "llm", "demo-gmail_failure": "gmail"}


def test_dashboard_helpers():
    rows = [
        {
            "sender": "Alice",
            "subject": "Invoice",
            "intent_summary": "billing",
            "status": "completed",
            "category": "Billing",
            "draft_status": "saved",
            "escalation_reasons_json": "[]",
        },
        {
            "sender": "Bob",
            "subject": "Legal",
            "intent_summary": "claim",
            "status": "failed",
            "category": "Legal",
            "draft_status": "disabled",
            "escalation_reasons_json": '["legal"]',
        },
    ]
    assert metrics(rows) == {
        "fetched": 2,
        "processed": 1,
        "pending": 0,
        "drafts": 1,
        "escalated": 1,
        "failed": 1,
    }
    assert len(filter_messages(rows, "alice", "completed", "Billing")) == 1


def test_streamlit_demo_sync_has_no_exceptions(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ui.sqlite3"))
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path)).run(timeout=15)
    assert not app.exception
    sync = next(button for button in app.button if button.label == "Sync Now")
    sync.click().run(timeout=15)
    assert not app.exception
    assert app.title[0].value == "Support Operations Agent"
