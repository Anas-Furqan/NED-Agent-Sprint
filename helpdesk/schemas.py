from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .priority import PRIORITIES, max_priority, normalize_priority  # noqa: F401  (re-exported)

CATEGORIES = ("fee", "hostel", "academic", "technical", "wellbeing", "harassment", "security",
              "not_helpdesk", "unclear", "other")


class Email(BaseModel):
    id: str
    from_name: str = ""
    from_email: str = ""
    subject: str = ""
    body: str = ""
    received_at: str | None = None


class Issue(BaseModel):
    summary: str
    category: str
    policy_refs: list[str]


class LLMTriage(BaseModel):
    """Schema the model must return (kept simple: no defaults/constraints, for Gemini).
    The before-validators tolerate sloppy model output (text confidence, null lists...)."""

    primary_category: str
    secondary_categories: list[str]
    issues: list[Issue]
    priority: str
    escalate: bool
    escalation_reason: str
    policy_refs: list[str]
    reply_draft: str
    confidence: float
    detected_language: str
    agent_notes: str

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if isinstance(v, str):
            words = {"very high": 0.9, "high": 0.8, "medium": 0.6, "moderate": 0.6, "low": 0.3}
            m = re.search(r"\d+(\.\d+)?", v)
            v = float(m.group(0)) if m else words.get(v.strip().lower(), 0.5)
        v = float(v if v is not None else 0.5)
        return v / 100 if v > 1 else max(0.0, v)

    @field_validator("secondary_categories", "policy_refs", "issues", mode="before")
    @classmethod
    def none_to_list(cls, v):
        if v is None:
            return []
        return [v] if isinstance(v, (str, dict)) else v

    @field_validator("primary_category", "priority", "escalation_reason", "reply_draft",
                     "detected_language", "agent_notes", mode="before")
    @classmethod
    def none_to_str(cls, v):
        return "" if v is None else str(v)

    @field_validator("escalate", mode="before")
    @classmethod
    def coerce_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "yes", "1", "y")
        return bool(v)


# Used to fill keys the model forgot, so one missing field does not waste a retry.
TRIAGE_DEFAULTS = dict(primary_category="unclear", secondary_categories=[], issues=[], priority="medium",
                       escalate=False, escalation_reason="", policy_refs=[], reply_draft="",
                       confidence=0.5, detected_language="english", agent_notes="")


class LLMReply(BaseModel):
    reply_draft: str


class OutboxEntry(BaseModel):
    """Exactly the sample_outbox.json shape, in the same key order."""

    model_config = ConfigDict(extra="forbid")
    email_id: str
    category: str
    priority: Literal["low", "medium", "high"]
    escalate: bool
    escalation_reason: str
    policy_refs: list[str]
    reply_draft: str
    confidence: float = Field(ge=0, le=1)
    agent_notes: str

    @field_validator("confidence")
    @classmethod
    def round_conf(cls, v: float) -> float:
        return round(v, 2)


def normalize_category(value: str) -> str:
    v = (value or "").strip().lower()
    aliases = {"fees": "fee", "finance": "fee", "accounts": "fee", "academics": "academic",
               "exam": "academic", "tech": "technical", "it": "technical", "safety": "hostel",
               "medical": "wellbeing", "health": "wellbeing", "phishing": "security",
               "suspicious": "security", "legal": "other", "gibberish": "unclear",
               "spam": "not_helpdesk", "marketing": "not_helpdesk", "newsletter": "not_helpdesk",
               "auto_reply": "not_helpdesk", "autoreply": "not_helpdesk", "auto-reply": "not_helpdesk",
               "out_of_office": "not_helpdesk", "bounce": "not_helpdesk", "out_of_scope": "not_helpdesk",
               "not-helpdesk": "not_helpdesk", "irrelevant": "not_helpdesk"}
    v = aliases.get(v, v)
    return v if v in CATEGORIES else "other"
