from __future__ import annotations

from .classifier import AIProvider, RuleBasedProvider
from .models import Classification, NormalizedEmail, RecommendedAction, ReplyDraft
from .security import validate_generated_reply


def reply_subject(original: str) -> str:
    subject = original.strip() or "Your support request"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def generate_reply(
    email: NormalizedEmail,
    classification: Classification,
    policy: str,
    provider: AIProvider | None,
) -> ReplyDraft:
    if classification.recommended_action == RecommendedAction.NO_REPLY:
        return ReplyDraft(
            subject=reply_subject(email.subject),
            body="",
            safe_to_save=False,
            explanation="Policy/classification indicates that no reply should be drafted.",
        )
    if classification.recommended_action == RecommendedAction.ESCALATE:
        return ReplyDraft(
            subject=reply_subject(email.subject),
            body="",
            safe_to_save=False,
            explanation="Escalation-only case: a human must decide whether an acknowledgment is appropriate.",
        )
    generator = provider or RuleBasedProvider()
    try:
        body = generator.draft(email, classification, policy)
    except Exception:
        body = RuleBasedProvider().draft(email, classification, policy)
    safe, reasons = validate_generated_reply(body, classification)
    return ReplyDraft(
        subject=reply_subject(email.subject),
        body=body if safe else "",
        safe_to_save=safe,
        explanation="Draft passed deterministic safety validation." if safe else "; ".join(reasons),
    )
