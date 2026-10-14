"""Milestone 3: mail that is not a helpdesk request, context-dependent keywords, and more unseen cases."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from helpdesk.loader import html_to_text, load_email, strip_signature
from helpdesk.pipeline import Runner, TriageAgent
from helpdesk.policy import load_policy
from helpdesk.schemas import LLMTriage
from helpdesk.signals import detect_signals, message_kind
from helpdesk.validate import detect_language, unsupported_language_name

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "tests" / "fixtures" / "edge"
POLICY = load_policy(ROOT / "data" / "policy.md")


def fixture(name):
    return json.loads((EDGE / name).read_text(encoding="utf-8"))


def kind_of(name):
    f = fixture(name)
    return message_kind(f["subject"], f["body"], f["from_email"])


def strong(name):
    f = fixture(name)
    return {s.name for s in detect_signals(f["subject"], f["body"], POLICY, f["from_name"]) if s.strong}


# ---- context decides, not keywords ----
@pytest.mark.parametrize("name,kind", [
    ("X39_tcs_notification.json", "delivery"), ("X42_jazzcash_alert.json", "bank_alert"),
    ("X45_daraz_sale.json", "marketing"), ("X46_coaching_ad.json", "marketing"), ("X47_recruiter.json", "recruiter"),
    ("X48_circular.json", "circular"), ("X29_autoreply.json", "auto_reply"),
    ("X41_tcs_transcript.json", None), ("X44_paid_jazzcash.json", None),  # same keywords, but a student asking
])
def test_message_kinds_use_context(name, kind):
    assert kind_of(name) == kind


@pytest.mark.parametrize("name,signal", [
    ("X40_tcs_phishing.json", "suspicious"), ("X43_otp_share.json", "suspicious"),
    ("X50_fake_registrar.json", "suspicious"), ("X51_assignment_leak.json", "integrity"),
    ("X52_theft.json", "incident"), ("X53_ragging_ru.json", "harassment"), ("X62_roommate_distress.json", "wellbeing"),
])
def test_risk_signals(name, signal):
    assert signal in strong(name)


@pytest.mark.parametrize("name", ["X54_harmless_triggers.json", "X49_parent_result.json", "X64_caps_vc.json",
                                  "X41_tcs_transcript.json", "X44_paid_jazzcash.json"])
def test_no_false_alarms(name):
    assert not strong(name)


# ---- not_helpdesk handling (LLM mocked) ----
class Fake:
    def __init__(self, triage):
        self.triage, self.calls, self.daily_quota_exhausted = triage, 0, False
        self.last = {"model": "fake", "attempts": 1, "errors": [], "repaired": []}

    def generate(self, system, prompt, schema):
        self.calls += 1
        return self.triage


def model(**kw):
    base = dict(primary_category="other", secondary_categories=[], issues=[], priority="medium", escalate=False,
                escalation_reason="", policy_refs=["TECH-1"], confidence=0.8, detected_language="english",
                agent_notes="mock", reply_draft="Dear Customer,\n\nThanks for the update (TECH-1).\n\nRegards,\nStudent Helpdesk")
    return LLMTriage(**{**base, **kw})


def process(name, triage):
    return TriageAgent(POLICY, Fake(triage), 0).process(EDGE / name).entry


def test_automated_notification_gets_no_reply_and_no_refs():
    e = process("X39_tcs_notification.json", model())
    assert e.category == "not_helpdesk" and e.policy_refs == [] and e.priority == "low" and not e.escalate
    assert e.reply_draft.startswith("No reply drafted:") and "courier" in e.reply_draft
    assert "out of scope" in e.agent_notes


def test_human_out_of_scope_sender_gets_short_note():
    e = process("X47_recruiter.json", model(primary_category="not_helpdesk", policy_refs=[],
                                             reply_draft="Dear Ayesha Recruiter,\n\nThis inbox handles student helpdesk "
                                                         "queries only.\n\nRegards,\nStudent Helpdesk"))
    assert e.category == "not_helpdesk" and e.reply_draft.startswith("Dear") and e.policy_refs == []


def test_phishing_is_never_dismissed_as_not_helpdesk():
    e = process("X40_tcs_phishing.json", model(primary_category="not_helpdesk", policy_refs=[]))
    assert e.category == "security" and e.escalate and "ESC-6" in e.policy_refs
    assert "http" not in e.reply_draft


def test_incident_routes_like_the_policy_safety_rule():
    e = process("X52_theft.json", model(primary_category="hostel", policy_refs=[], priority="medium",
                                         reply_draft="Dear Shahzaib Khan,\n\nWe are sorry. This has been escalated to "
                                                     "staff.\n\nRegards,\nStudent Helpdesk"))
    assert e.escalate and e.priority == "high"
    assert "not covered by a specific policy rule" in e.escalation_reason
    assert "Hostel Warden and Security" in e.escalation_reason  # read from ESC-1 text, not hardcoded


# ---- languages, parsing ----
def test_unsupported_languages():
    assert unsupported_language_name(fixture("X55_arabic.json")["body"]) == "Arabic"
    assert detect_language(fixture("X56_chinese.json")["body"]) == "unsupported"
    assert detect_language(fixture("X11_urdu.json")["body"]) == "urdu"


def test_unsupported_language_offline_reply_in_english(tmp_path):
    e = TriageAgent(POLICY, None, 0).process(EDGE / "X55_arabic.json").entry
    assert "English or Roman Urdu" in e.reply_draft and e.confidence <= 0.3 and not e.escalate


def test_html_and_signature_cleaned():
    text, was_html = html_to_text("<div><p>Hi <b>there</b></p><p>Wi-Fi down &amp; slow</p></div>")
    assert was_html and "<" not in text and "Wi-Fi down & slow" in text
    assert strip_signature("Please help.\n--\nAli | BS CS")[0] == "Please help."
    loaded = load_email(EDGE / "X61_html.json")
    assert "HTML body converted" in " ".join(loaded.notes) and "<p>" not in loaded.newest
    assert "Sent from my iPhone" not in loaded.newest


def test_attachment_mentioned_is_noted():
    assert any("attachment" in n for n in load_email(EDGE / "X57_screenshot.json").notes)


def test_one_word_message_is_clarification(tmp_path):
    e = TriageAgent(POLICY, None, 0).process(EDGE / "X63_one_word.json").entry
    assert not e.escalate and e.confidence <= 0.4 and "too short" in e.agent_notes


# ---- duplicates are deterministic whatever the file order ----
@pytest.mark.parametrize("names", [("a_first.json", "b_second.json"), ("b_first.json", "a_second.json")])
def test_duplicate_content_marked_regardless_of_file_order(tmp_path, names):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    shutil.copy(EDGE / "X58_dup_first.json", inbox / names[0])   # received 08:00
    shutil.copy(EDGE / "X58_dup_second.json", inbox / names[1])  # received 08:05
    Runner(TriageAgent(POLICY, None, 0), inbox, tmp_path / "outbox.json").run()
    out = {e["email_id"]: e for e in json.loads((tmp_path / "outbox.json").read_text(encoding="utf-8"))}
    assert "likely duplicate of X58_dup_first" in out["X58_dup_second"]["agent_notes"]
    assert "likely duplicate" not in out["X58_dup_first"]["agent_notes"]


@pytest.mark.parametrize("text,level", [
    ("meri fees ki due date kl tk h lekin installment mil sakti hai?", "high"),  # found while testing the Live Run page
    ("fee ki last date aj hai", "high"), ("kl mera quiz hai aur LMS lock hai", "medium"), ("due date next month hai", None),
])
def test_chat_style_roman_urdu_deadlines(text, level):
    from helpdesk.timing import deadline_urgency

    got = deadline_urgency(text)
    assert (got[0] if got else None) == level
