import json

import pytest
from pydantic import ValidationError

from ned_agent.classifier import (
    AIProvider,
    GeminiProvider,
    RuleBasedProvider,
    classify_with_fallback,
)
from ned_agent.config import DEFAULT_CATEGORIES
from ned_agent.models import Classification, Priority, RecommendedAction, Sentiment


def test_schema_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        Classification(
            category="Billing",
            priority=Priority.LOW,
            sentiment=Sentiment.NEUTRAL,
            intent_summary="Question",
            recommended_action=RecommendedAction.DRAFT_REPLY,
            confidence=101,
            reasoning="Evidence",
        )


def test_fallback_is_transparent(email):
    result = classify_with_fallback(email, None, DEFAULT_CATEGORIES, "policy")
    assert result.category == "Billing"
    assert result.used_fallback is True
    assert result.provider == "rules-fallback"


def test_provider_failure_uses_fallback(email):
    class Broken(AIProvider):
        def classify(self, email, categories, policy):
            raise TimeoutError("provider unavailable")

        def draft(self, email, classification, policy):
            raise TimeoutError

    assert classify_with_fallback(email, Broken(), DEFAULT_CATEGORIES, "policy").used_fallback


def test_ambiguous_fallback_routes_to_manual_review(email):
    email.subject, email.body = "Question", "Can you look at this?"
    result = RuleBasedProvider().classify(email, DEFAULT_CATEGORIES, "policy")
    assert result.recommended_action == RecommendedAction.MANUAL_REVIEW
    assert result.confidence < 60


def test_gemini_structured_retry_is_bounded(monkeypatch, email):
    valid = {
        "category": "Billing",
        "priority": "Medium",
        "sentiment": "Neutral",
        "intent_summary": "Duplicate invoice charge",
        "recommended_action": "Draft Reply",
        "confidence": 82,
        "reasoning": "The message reports two charges.",
        "escalation_reasons": [],
        "policy_references": ["Billing disputes"],
        "missing_information": [],
        "used_fallback": False,
        "provider": "gemini",
    }

    class Models:
        def __init__(self):
            self.calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            text = "not-json" if self.calls == 1 else json.dumps(valid)
            return type("Response", (), {"text": text})()

    provider = object.__new__(GeminiProvider)
    provider.client = type("Client", (), {"models": Models()})()
    provider.model = "test-model"
    provider.attempts = 3
    monkeypatch.setattr("ned_agent.classifier.time.sleep", lambda _: None)
    result = provider.classify(email, DEFAULT_CATEGORIES, "Trusted policy")
    assert result.category == "Billing"
    assert provider.client.models.calls == 2


def test_low_confidence_model_reply_is_downgraded(email):
    class LowConfidence(AIProvider):
        def classify(self, email, categories, policy):
            return Classification(
                category="Billing",
                priority=Priority.MEDIUM,
                sentiment=Sentiment.NEUTRAL,
                intent_summary="Possible billing question",
                recommended_action=RecommendedAction.DRAFT_REPLY,
                confidence=35,
                reasoning="Insufficient context",
            )

        def draft(self, email, classification, policy):
            return "unused"

    result = classify_with_fallback(email, LowConfidence(), DEFAULT_CATEGORIES, "policy")
    assert result.recommended_action == RecommendedAction.MANUAL_REVIEW
    assert "Low-confidence AI decision" in result.escalation_reasons


def test_refund_without_reference_requests_missing_information(email):
    email.subject = "Refund request"
    email.body = "I need a refund but cannot find the receipt or remember when I purchased it."
    result = RuleBasedProvider().classify(email, DEFAULT_CATEGORIES, "policy")
    assert result.category == "Refund"
    assert result.recommended_action == RecommendedAction.REQUEST_INFORMATION
    assert len(result.missing_information) == 2


def test_no_reply_notice_does_not_generate_reply(email):
    email.sender_email = "no-reply@example.test"
    email.subject = "Automated notice"
    result = RuleBasedProvider().classify(email, DEFAULT_CATEGORIES, "policy")
    assert result.recommended_action == RecommendedAction.NO_REPLY
