"""Regression tests for the hardened weak spots (promises, long emails, past hazards, Urdu offline, cache)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from helpdesk.cache import DecisionCache
from helpdesk.llm import LLMUnavailable
from helpdesk.loader import load_email
from helpdesk.pipeline import Runner, TriageAgent, compress_for_retry, soften_promises
from helpdesk.policy import load_policy
from helpdesk.schemas import LLMReply, LLMTriage
from helpdesk.signals import detect_signals
from helpdesk.validate import promise_spans, repair_and_check

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "tests" / "fixtures" / "edge"
POLICY = load_policy(ROOT / "data" / "policy.md")
WINDOWS = frozenset(n for r in POLICY.rules.values() for n, _ in r.windows)


# ---- a) promises ----
@pytest.mark.parametrize("text", [
    "Woh foran iqdaamat karenge.", "Aap ka masla zaroor hal ho jayega.", "Hum aap ki fee waive kar denge.",
    "Refund mil jayega.", "Yeh pakka ho jayega.", "Aaj hi ho jayega.", "They will act immediately.",
    "It will be fixed today.", "We promise a quick fix.", "یہ فوراً ٹھیک کر دیں گے", "یہ ضرور ہو جائے گا",
])
def test_promises_detected(text):
    assert promise_spans(text, WINDOWS)


@pytest.mark.parametrize("text", [
    "Your complaint will be resolved within 48 hours (HOSTEL-1).",
    "HOSTEL-1 ke mutabiq 48 ghante mein resolve hota hai.", "Foran door rahein aur kisi ko qareeb na aane dein.",
    "Urgent processing guarantee nahi hai (ACAD-3).", "We cannot guarantee urgent processing.",
    "Hum ne case escalate kar diya hai.", "The Accounts Office responds within 5 working days (FEE-3).",
])
def test_policy_timelines_and_neutral_text_allowed(text):
    assert not promise_spans(text, WINDOWS)


def test_soften_removes_promise_and_keeps_layout():
    reply = ("Dear Ali Khan,\n\nBlock D se door rahein (HOSTEL-3). Woh foran iqdaamat karenge.\n\n"
             "Regards,\nStudent Helpdesk")
    out = soften_promises(reply, "roman_urdu")
    fixed, res = repair_and_check(out, name="Ali Khan", language="roman_urdu", refs=["HOSTEL-3"], sender_email="",
                                  signals=set(), confidential=False, allowed_numbers=WINDOWS)
    assert "iqdaamat karenge" not in fixed and fixed.startswith("Dear Ali Khan,") and res.ok


class ScriptedClient:
    """Returns queued results in order (LLMTriage, LLMReply text or an exception)."""

    def __init__(self, *answers):
        self.answers, self.calls, self.daily_quota_exhausted = list(answers), 0, False
        self.last = {"model": "fake", "attempts": 0, "errors": [], "repaired": []}

    def generate(self, system, prompt, schema):
        self.calls += 1
        self.last = {"model": "fake", "attempts": 1, "errors": [], "repaired": []}
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return LLMReply(reply_draft=a) if schema is LLMReply else a


def triage(reply, **kw):
    base = dict(primary_category="hostel", secondary_categories=[], issues=[], priority="medium", escalate=False,
                escalation_reason="", policy_refs=["HOSTEL-1"], confidence=0.9, detected_language="english",
                agent_notes="mock", reply_draft=reply)
    return LLMTriage(**{**base, **kw})


def write(tmp_path, body, name="T1"):
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps({"id": name, "from_name": "Ali Khan", "from_email": "a@example.com", "subject": "Fan",
                             "body": body, "received_at": "2026-10-12T10:00:00"}), encoding="utf-8")
    return p


def test_promise_regenerated_then_softened(tmp_path):
    promise = "Dear Ali Khan,\n\nThe fan will be fixed today (HOSTEL-1).\n\nRegards,\nStudent Helpdesk"
    still = "Dear Ali Khan,\n\nLog it with your room number (HOSTEL-1). It will be fixed today.\n\nRegards,\nStudent Helpdesk"
    client = ScriptedClient(triage(promise), still)
    res = TriageAgent(POLICY, client, 0).process(write(tmp_path, "The fan in my room is broken."))
    assert client.calls == 2  # one regeneration
    assert not promise_spans(res.entry.reply_draft, WINDOWS)
    assert "softened a promise beyond policy" in res.trace["validation"]["repairs"]


# ---- b) long / repetitive emails ----
def test_repetitive_email_is_collapsed_before_llm():
    loaded = load_email(EDGE / "X10_long.json")
    assert len(loaded.llm_body.split()) < 100
    assert any("repeated sentences collapsed" in n for n in loaded.notes)


def test_truncation_marker():
    assert "[...truncated" in compress_for_retry(" ".join(f"w{i}" for i in range(1000)))
    assert compress_for_retry("short text") is None


def test_empty_model_output_retried_with_shorter_email(tmp_path):
    body = " ".join(f"word{i}" for i in range(900)) + " The fan in my room is broken."
    ok = triage("Dear Ali Khan,\n\nPlease log it with your room number (HOSTEL-1).\n\nRegards,\nStudent Helpdesk")
    client = ScriptedClient(LLMUnavailable("gave up after 4 attempts: invalid JSON from model: Expecting value"), ok)
    res = TriageAgent(POLICY, client, 0).process(write(tmp_path, body))
    assert client.calls == 2 and not res.fallback
    assert "retried with a shortened email" in res.entry.agent_notes


# ---- c) hazard already out ----
def test_past_fire_is_post_incident_inspection(tmp_path):
    body = "There was a small electrical fire in our common room yesterday. It is out now but the socket smells burnt."
    model = triage("Dear Ali Khan,\n\nStay away from the socket (HOSTEL-3, ESC-1); escalated for inspection.\n\n"
                   "Regards,\nStudent Helpdesk", escalate=True, escalation_reason="Active fire in the common room.",
                   policy_refs=["HOSTEL-3", "ESC-1"], priority="high")
    res = TriageAgent(POLICY, ScriptedClient(model), 0).process(write(tmp_path, body))
    e = res.entry
    assert e.escalate and "post-incident safety inspection" in e.escalation_reason
    assert "Active fire" not in e.escalation_reason and {"HOSTEL-3", "ESC-1"} <= set(e.policy_refs)


def test_past_fire_without_damage_is_medium_floor():
    sig = detect_signals("", "There was a fire in the kitchen yesterday, it was put out quickly.", POLICY)
    assert [(s.name, s.priority) for s in sig] == [("safety_post", "medium")]


# ---- d) Urdu script offline ----
@pytest.mark.parametrize("file,category,ref", [
    ("X11_urdu.json", "fee", "FEE-1"), ("X36_urdu_harass.json", "harassment", "ESC-2"),
    ("X37_urdu_hazard.json", "hostel", "ESC-1"), ("X38_urdu_distress.json", "wellbeing", "ESC-3"),
])
def test_urdu_script_offline(file, category, ref, tmp_path):
    shutil.copy(EDGE / file, tmp_path / file)
    res = TriageAgent(POLICY, None, 0).process(tmp_path / file)
    assert res.entry.category == category and ref in res.entry.policy_refs and res.trace["language"] == "urdu"


# ---- cache keeps the model answer and re-runs guardrails ----
def test_cache_stores_model_answer_and_reruns_guardrails(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    write(inbox, "The fan in my room is broken.")
    cache = DecisionCache(tmp_path / ".cache")
    model = triage("Dear Ali Khan,\n\nPlease log it with your room number (HOSTEL-1).\n\nRegards,\nStudent Helpdesk")
    client = ScriptedClient(model)
    Runner(TriageAgent(POLICY, client, 0, cache), inbox, tmp_path / "outbox.json").run()
    stored = json.loads(next((tmp_path / ".cache").iterdir()).read_text(encoding="utf-8"))
    assert "triage" in stored and "entry" not in stored
    results = Runner(TriageAgent(POLICY, client, 0, cache), inbox, tmp_path / "outbox.json").run()
    assert client.calls == 1 and results[0].cache_hit and results[0].trace["steps"]  # guardrails ran again


def test_greeting_followed_by_space_has_single_blank_line():
    out, _ = repair_and_check("Dear Ali, \n\nText (FEE-1).\n\nRegards, \nStudent Helpdesk", name="Ali", language="english",
                              refs=["FEE-1"], sender_email="", signals=set(), confidential=False)
    assert out == "Dear Ali,\n\nText (FEE-1).\n\nRegards,\nStudent Helpdesk"


def test_urdu_confidentiality_request_and_ack():
    from helpdesk.signals import wants_confidentiality

    assert wants_confidentiality("براہ کرم میرا نام کسی کو نہ بتائیں۔")
    _, res = repair_and_check("Dear Sara,\n\nآپ کی رازداری کا خیال رکھا جائے گا (ESC-2)۔\n\nRegards,\nStudent Helpdesk",
                              name="Sara", language="urdu", refs=["ESC-2"], sender_email="", signals={"harassment"},
                              confidential=True)
    assert res.ok
