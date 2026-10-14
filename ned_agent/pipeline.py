from __future__ import annotations

import json
import time
from typing import Any

from .classifier import AIProvider, classify_with_fallback
from .database import Database
from .escalation import evaluate_escalation
from .gmail import GmailClient
from .logging_utils import sanitize_error
from .models import NormalizedEmail, RecommendedAction
from .policy import PolicyDocument
from .reply import generate_reply


class Pipeline:
    def __init__(
        self,
        database: Database,
        policy: PolicyDocument,
        categories: tuple[str, ...],
        provider: AIProvider | None = None,
        gmail: GmailClient | None = None,
    ):
        self.db = database
        self.policy = policy
        self.categories = categories
        self.provider = provider
        self.gmail = gmail

    def ingest(self, emails: list[NormalizedEmail]) -> tuple[int, int]:
        added = 0
        for email in emails:
            _, created = self.db.upsert_message(email)
            added += int(created)
        source = emails[0].source if emails else "demo"
        self.db.audit(
            source,
            "ingest",
            f"Fetched {len(emails)}; added {added}; duplicates {len(emails) - added}",
        )
        return len(emails), added

    def process_email(self, email: NormalizedEmail, create_live_draft: bool = True) -> str:
        message_id, _ = self.db.upsert_message(email)
        current = self.db.get_message(message_id)
        if current and current["status"] == "completed":
            return "duplicate"
        if not self.db.claim_message(message_id):
            return "busy"
        started = time.perf_counter()
        try:
            relevant_policy = self.policy.relevant([email.subject])
            classification = classify_with_fallback(
                email, self.provider, self.categories, relevant_policy
            )
            escalation = evaluate_escalation(email, classification)
            if escalation.required:
                classification.recommended_action = RecommendedAction.ESCALATE
                classification.escalation_reasons = list(
                    dict.fromkeys(classification.escalation_reasons + escalation.reasons)
                )
            relevant_policy = self.policy.relevant([classification.category])
            draft = generate_reply(email, classification, relevant_policy, self.provider)
            elapsed = int((time.perf_counter() - started) * 1000)
            with self.db.transaction(immediate=True) as db:
                db.execute(
                    """UPDATE messages SET category=?,priority=?,sentiment=?,intent_summary=?,
                    recommended_action=?,confidence=?,reasoning=?,escalation_reasons_json=?,
                    policy_references_json=?,missing_information_json=?,used_fallback=?,provider=?,
                    policy_hash=?,reply_subject=?,reply_preview=?,draft_status=?,processing_ms=?,updated_at=?
                    WHERE id=?""",
                    (
                        classification.category,
                        classification.priority.value,
                        classification.sentiment.value,
                        classification.intent_summary,
                        classification.recommended_action.value,
                        classification.confidence,
                        classification.reasoning,
                        json.dumps(classification.escalation_reasons),
                        json.dumps(classification.policy_references),
                        json.dumps(classification.missing_information),
                        int(classification.used_fallback),
                        classification.provider,
                        self.policy.sha256,
                        draft.subject,
                        draft.body,
                        "pending"
                        if draft.safe_to_save and email.source == "live"
                        else "local_preview"
                        if draft.safe_to_save
                        else "disabled",
                        elapsed,
                        self.db.now(),
                        message_id,
                    ),
                )
                if escalation.required:
                    db.execute(
                        """INSERT INTO escalations(message_id,severity,reason,evidence,policy_reference,
                        recommended_human_action,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(message_id) DO UPDATE SET severity=excluded.severity,reason=excluded.reason,
                        evidence=excluded.evidence,updated_at=excluded.updated_at""",
                        (
                            message_id,
                            escalation.severity.value,
                            "; ".join(escalation.reasons),
                            "; ".join(escalation.evidence),
                            escalation.policy_reference,
                            escalation.recommended_human_action or "Manual review",
                            "open",
                            self.db.now(),
                            self.db.now(),
                        ),
                    )
            if draft.safe_to_save and email.source == "live" and create_live_draft:
                if self.gmail is None:
                    raise RuntimeError("Live draft requested without a Gmail client")
                self._save_draft(message_id, email, draft.subject, draft.body)
            with self.db.transaction() as db:
                db.execute(
                    "UPDATE messages SET status='completed',processed_at=?,updated_at=? WHERE id=?",
                    (self.db.now(), self.db.now(), message_id),
                )
            self.db.audit(
                email.source,
                "processed",
                classification.recommended_action.value,
                message_id=message_id,
            )
            return "completed"
        except Exception as error:
            safe_error = sanitize_error(error)
            with self.db.transaction() as db:
                db.execute(
                    """UPDATE messages SET status='failed',error=?,retry_count=retry_count+1,
                    processing_ms=?,updated_at=? WHERE id=?""",
                    (
                        safe_error,
                        int((time.perf_counter() - started) * 1000),
                        self.db.now(),
                        message_id,
                    ),
                )
            self.db.audit(email.source, "processing_failed", safe_error, "error", message_id)
            return "failed"

    def _save_draft(self, message_id: int, email: NormalizedEmail, subject: str, body: str) -> None:
        row = self.db.get_message(message_id)
        if row and row.get("gmail_draft_id"):
            return
        existing = self._find_existing_draft(email)
        result = existing or self.gmail.create_draft(email, subject, body)  # type: ignore[union-attr]
        with self.db.transaction(immediate=True) as db:
            db.execute(
                "UPDATE messages SET gmail_draft_id=?,draft_status='saved',updated_at=? WHERE id=?",
                (result["id"], self.db.now(), message_id),
            )

    def _find_existing_draft(self, email: NormalizedEmail) -> dict[str, Any] | None:
        if self.gmail is None:
            return None
        for item in self.gmail.list_drafts():
            if item.get("message", {}).get("threadId") == email.thread_id:
                return item
        return None

    def run(self, emails: list[NormalizedEmail], create_live_drafts: bool = True) -> dict[str, int]:
        counts = {
            "fetched": len(emails),
            "added": 0,
            "completed": 0,
            "failed": 0,
            "duplicate": 0,
            "busy": 0,
        }
        for email in emails:
            _, created = self.db.upsert_message(email)
            counts["added"] += int(created)
            outcome = self.process_email(email, create_live_draft=create_live_drafts)
            counts[outcome] = counts.get(outcome, 0) + 1
        return counts
