from __future__ import annotations

import re

from .models import Classification, RecommendedAction

INJECTION_PATTERNS = (
    r"ignore (all|any|the|your)?\s*(previous|prior|system) instructions",
    r"reveal (the |your )?(api key|token|secret|password|system prompt)",
    r"disable (the )?(safety|escalation|policy|guardrail)",
    r"execute (this |the )?(code|command|shell)",
    r"send (this |the )?email (now|automatically)",
)
SECRET_OUTPUT = re.compile(
    r"(?i)(AIza[0-9A-Za-z_-]{20,}|Bearer\s+[0-9A-Za-z._-]+|refresh[_ -]?token\s*[:=])"
)


def detect_prompt_injection(subject: str, body: str) -> list[str]:
    text = f"{subject}\n{body}"[:20000]
    return [pattern for pattern in INJECTION_PATTERNS if re.search(pattern, text, re.I)]


def validate_generated_reply(reply: str, classification: Classification) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not reply.strip():
        reasons.append("Empty reply")
    if SECRET_OUTPUT.search(reply):
        reasons.append("Possible secret disclosure")
    prohibited = (
        r"(?i)your refund (has been|is) approved",
        r"(?i)we guarantee",
        r"(?i)we will pay",
        r"(?i)click this link and enter your password",
    )
    for pattern in prohibited:
        if re.search(pattern, reply):
            reasons.append("Unsupported commitment or unsafe instruction")
            break
    if classification.recommended_action in (
        RecommendedAction.NO_REPLY,
        RecommendedAction.ESCALATE,
    ):
        reasons.append("Classification does not permit an automatic substantive draft")
    return not reasons, reasons
