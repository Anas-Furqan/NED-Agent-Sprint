from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORIES = ("fee", "hostel", "academic", "technical", "wellbeing", "harassment", "security", "unclear", "other")
PRIORITIES = ("low", "medium", "high")
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}


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
    """Schema the model must return (kept simple: no defaults/constraints, for Gemini)."""

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
               "suspicious": "security", "legal": "other", "gibberish": "unclear"}
    v = aliases.get(v, v)
    return v if v in CATEGORIES else "other"


def normalize_priority(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("urgent", "critical"):
        return "high"
    return v if v in PRIORITIES else "medium"


def max_priority(*values: str) -> str:
    return max(values, key=lambda p: PRIORITY_RANK.get(p, 1))
