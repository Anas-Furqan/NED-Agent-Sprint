from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Priority(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class Sentiment(StrEnum):
    POSITIVE = "Positive"
    NEUTRAL = "Neutral"
    NEGATIVE = "Negative"
    MIXED = "Mixed"


class RecommendedAction(StrEnum):
    DRAFT_REPLY = "Draft Reply"
    ESCALATE = "Escalate"
    REQUEST_INFORMATION = "Request Information"
    NO_REPLY = "No Reply"
    MANUAL_REVIEW = "Manual Review"


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=80)
    priority: Priority
    sentiment: Sentiment
    intent_summary: str = Field(min_length=1, max_length=500)
    recommended_action: RecommendedAction
    confidence: int = Field(ge=0, le=100)
    reasoning: str = Field(min_length=1, max_length=1000)
    escalation_reasons: list[str] = Field(default_factory=list, max_length=10)
    policy_references: list[str] = Field(default_factory=list, max_length=10)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    used_fallback: bool = False
    provider: str = "unknown"


class NormalizedEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    sender: str
    sender_email: str
    recipients: list[str] = Field(default_factory=list)
    subject: str = "(no subject)"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    body: str = ""
    snippet: str = ""
    labels: list[str] = Field(default_factory=list)
    unread: bool = False
    rfc_message_id: str | None = None
    references: str | None = None
    source: str = "live"

    @field_validator("body", "subject", "sender")
    @classmethod
    def strip_nul(cls, value: str) -> str:
        return value.replace("\x00", "").strip()


class EscalationDecision(BaseModel):
    required: bool = False
    severity: Priority = Priority.MEDIUM
    reasons: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    policy_reference: str | None = None
    recommended_human_action: str | None = None


class ReplyDraft(BaseModel):
    subject: str
    body: str
    safe_to_save: bool
    explanation: str
