"""The priority scheme, defined in ONE place.

high   = safety hazard, harassment, medical/wellbeing, legal/media threat, suspicious message
         (injection/phishing/data request), outage affecting many users, or an administrative
         deadline (due date / last date / application deadline) within ~48 hours.
medium = a blocked process (locked account, missing voucher), an overdue complaint, money
         (fees, refunds, installments), an academic-integrity request, or coursework deadlines.
low    = informational questions, thank-you notes, spam, auto-replies, unclear messages.

The LLM proposes a priority; code enforces these minimums for deterministic signals.
Code never lowers a priority, except for messages that need no action (spam, auto-replies).
"""
from __future__ import annotations

PRIORITIES = ("low", "medium", "high")
RANK = {p: i for i, p in enumerate(PRIORITIES)}

# minimum priority per deterministic signal name (see signals.py)
SIGNAL_FLOOR = {
    "safety": "high",
    "harassment": "high",
    "wellbeing": "high",
    "legal": "high",
    "suspicious": "high",
    "outage": "high",
    "integrity": "medium",
}
CATEGORY_FLOOR = {"fee": "medium"}  # money
NO_ACTION_CATEGORIES = ("spam", "auto_reply")
OVERDUE_FLOOR = "medium"
ESCALATED_FLOOR = "medium"


def max_priority(*values: str) -> str:
    return max(values, key=lambda p: RANK.get(p, 1))


def normalize_priority(value: str) -> str:
    v = (value or "").strip().lower()
    if v in ("urgent", "critical"):
        return "high"
    return v if v in PRIORITIES else "medium"


def enforce(priority: str, *, signals: list[str], category: str, overdue: bool,
            deadline: str | None, escalate: bool, kind: str | None = None) -> tuple[str, list[str]]:
    """Apply the minimums. Returns (priority, reasons for any change)."""
    why = []
    floors = [(SIGNAL_FLOOR[s], f"signal '{s}'") for s in signals if s in SIGNAL_FLOOR]
    no_action = (category in NO_ACTION_CATEGORIES or kind == "thanks") and not signals and not escalate
    if category in CATEGORY_FLOOR and not no_action:
        floors.append((CATEGORY_FLOOR[category], f"category '{category}'"))
    if overdue:
        floors.append((OVERDUE_FLOOR, "overdue complaint"))
    if deadline:
        floors.append((deadline, "deadline"))
    if escalate:
        floors.append((ESCALATED_FLOOR, "escalated"))
    for floor, reason in floors:
        if RANK[floor] > RANK[priority]:
            why.append(f"{priority}->{floor} ({reason})")
            priority = floor
    if no_action and priority != "low":
        why.append(f"{priority}->low (no action needed)")
        priority = "low"
    return priority, why
