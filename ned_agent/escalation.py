from __future__ import annotations

import re

from .models import Classification, EscalationDecision, NormalizedEmail, Priority, RecommendedAction
from .security import detect_prompt_injection


def evaluate_escalation(
    email: NormalizedEmail, classification: Classification
) -> EscalationDecision:
    text = f"{email.subject}\n{email.body}"[:20000]
    rules = (
        (
            Priority.CRITICAL,
            "Legal or regulatory threat",
            r"\b(lawyer|legal notice|lawsuit|sue|court|regulator|regulatory action)\b",
            "Legal / regulatory review",
        ),
        (
            Priority.CRITICAL,
            "Suspected account compromise or privacy incident",
            r"\b(hacked|account takeover|compromis|data breach|identity theft|password was changed|personal data exposed)\b",
            "Security and privacy incident review",
        ),
        (
            Priority.CRITICAL,
            "Safety concern",
            r"\b(threat to life|self[- ]harm|suicide|physical harm|unsafe product|injury)\b",
            "Urgent safety review",
        ),
        (
            Priority.HIGH,
            "Serious financial dispute",
            r"\b(fraud|chargeback|large unauthorized charge|financial loss)\b",
            "Billing specialist review",
        ),
        (
            Priority.HIGH,
            "Explicit request for human review",
            r"\b(speak|talk) to (a )?(human|manager|supervisor)|human representative\b",
            "Assign a human support owner",
        ),
    )
    reasons: list[str] = []
    evidence: list[str] = []
    severity = Priority.MEDIUM
    human_action = "Review the complete message and approved policy before responding."
    for rule_severity, reason, pattern, action in rules:
        match = re.search(pattern, text, re.I)
        if match:
            reasons.append(reason)
            evidence.append(match.group(0)[:120])
            if rule_severity == Priority.CRITICAL or severity != Priority.CRITICAL:
                severity = rule_severity
                human_action = action
    injections = detect_prompt_injection(email.subject, email.body)
    if injections:
        reasons.append("Suspected prompt-injection attempt")
        evidence.append("Untrusted email attempted to alter system behavior or obtain secrets")
        severity = max((severity, Priority.HIGH), key=lambda item: list(Priority).index(item))
    if classification.confidence < 50 and classification.recommended_action in (
        RecommendedAction.MANUAL_REVIEW,
        RecommendedAction.ESCALATE,
    ):
        reasons.append("Low-confidence decision requires manual review")
    reasons.extend(reason for reason in classification.escalation_reasons if reason not in reasons)
    required = bool(reasons) or classification.recommended_action == RecommendedAction.ESCALATE
    if classification.category in ("Legal", "Security or Privacy"):
        required = True
        severity = Priority.CRITICAL
    return EscalationDecision(
        required=required,
        severity=severity,
        reasons=reasons or (["AI recommended escalation"] if required else []),
        evidence=evidence,
        policy_reference="Escalation conditions" if required else None,
        recommended_human_action=human_action if required else None,
    )
