from ned_agent.classifier import RuleBasedProvider
from ned_agent.config import DEFAULT_CATEGORIES
from ned_agent.models import Classification, Priority, RecommendedAction, Sentiment
from ned_agent.reply import generate_reply


def test_policy_has_required_sections(policy):
    required = {
        "refund eligibility",
        "billing disputes",
        "technical troubleshooting",
        "account security",
        "response tone and professionalism",
        "escalation conditions",
        "missing information",
        "cases where no response should be drafted",
    }
    assert required.issubset(policy.sections)
    assert "example" in policy.text.lower()


def test_fallback_reply_does_not_approve_refund(email, policy):
    email.subject = "Refund please"
    email.body = "I want my money back."
    classification = RuleBasedProvider().classify(email, DEFAULT_CATEGORIES, policy.text)
    reply = generate_reply(email, classification, policy.relevant(["Refund"]), None)
    assert "approved" not in reply.body.lower()


def test_escalation_has_no_substantive_draft(email, policy):
    classification = Classification(
        category="Legal",
        priority=Priority.CRITICAL,
        sentiment=Sentiment.NEGATIVE,
        intent_summary="Legal threat",
        recommended_action=RecommendedAction.ESCALATE,
        confidence=90,
        reasoning="Legal language",
    )
    reply = generate_reply(email, classification, policy.text, None)
    assert not reply.safe_to_save
    assert reply.body == ""
