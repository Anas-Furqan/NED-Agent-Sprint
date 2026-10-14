from __future__ import annotations

from typing import Any


def metrics(messages: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "fetched": len(messages),
        "processed": sum(item.get("status") == "completed" for item in messages),
        "pending": sum(item.get("status") in ("pending", "processing") for item in messages),
        "drafts": sum(item.get("draft_status") == "saved" for item in messages),
        "escalated": sum(
            bool(item.get("escalation_reasons_json") not in (None, "", "[]")) for item in messages
        ),
        "failed": sum(item.get("status") == "failed" for item in messages),
    }


def filter_messages(
    messages: list[dict[str, Any]], search: str = "", status: str = "All", category: str = "All"
) -> list[dict[str, Any]]:
    needle = search.casefold().strip()
    result = []
    for item in messages:
        haystack = f"{item.get('sender', '')} {item.get('subject', '')} {item.get('intent_summary', '')}".casefold()
        if needle and needle not in haystack:
            continue
        if status != "All" and item.get("status") != status:
            continue
        if category != "All" and item.get("category") != category:
            continue
        result.append(item)
    return result


def gmail_drafts_url() -> str:
    return "https://mail.google.com/mail/u/0/#drafts"
