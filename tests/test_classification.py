import pytest
from pydantic import ValidationError

from ned_agent.classifier import AIProvider, RuleBasedProvider, classify_with_fallback
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
