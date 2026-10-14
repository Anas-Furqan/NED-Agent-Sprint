from __future__ import annotations

import json
import time
from typing import Any

from .classifier import AIProvider, classify_with_fallback
from .database import Database
from .demo import DemoFailingProvider
from .escalation import evaluate_escalation
from .gmail import GmailClient, draft_matches_message
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

    def process_email(self, email: NormalizedEmail, create_live_draft: bool = True) -> str:
        message_id, _ = self.db.upsert_message(email)
        current = self.db.get_message(message_id)
        if current and current["status"] == "completed":
            return "duplicate"
        if not self.db.claim_message(message_id):
            return "busy"
        started = time.perf_counter()
        try:
            if email.source == "demo" and email.demo_failure == "gmail":
                raise RuntimeError("Simulated Gmail API failure (Demo mode; no API call was made)")
            provider = (
                DemoFailingProvider()
                if email.source == "demo" and email.demo_failure == "llm"
                else self.provider
            )
            relevant_policy = self.policy.relevant([email.subject])
            classification = classify_with_fallback(
                email, provider, self.categories, relevant_policy
            )
            escalation = evaluate_escalation(email, classification)
            if escalation.required:
                classification.recommended_action = RecommendedAction.ESCALATE
                classification.escalation_reasons = list(
                    dict.fromkeys(classification.escalation_reasons + escalation.reasons)
                )
            relevant_policy = self.policy.relevant([classification.category])
            draft = generate_reply(email, classification, relevant_policy, provider)
            elapsed = int((time.perf_counter() - started) * 1000)
            draft_status = (
                "unknown"
                if current and current.get("draft_status") == "unknown"
                else "pending"
                if draft.safe_to_save and email.source == "live"
                else "local_preview"
                if draft.safe_to_save
                else "disabled"
            )
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
                        draft_status,
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
        if existing:
            result = existing
        elif row and row.get("draft_status") == "unknown":
            raise RuntimeError(
                "Previous Gmail draft outcome is ambiguous; no marked draft was found. "
                "Confirm the Gmail Drafts folder before a manual retry."
            )
        else:
            try:
                result = self.gmail.create_draft(email, subject, body)  # type: ignore[union-attr]
            except Exception as error:
                status = getattr(getattr(error, "resp", None), "status", None)
                draft_status = (
                    "unknown"
                    if status is None or status in (408, 429) or status >= 500
                    else "failed"
                )
                with self.db.transaction(immediate=True) as db:
                    db.execute(
                        "UPDATE messages SET draft_status=?,updated_at=? WHERE id=?",
                        (draft_status, self.db.now(), message_id),
                    )
                raise
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
                draft_id = item.get("id")
                if draft_id:
                    draft = self.gmail.get_draft(draft_id)
                    if draft_matches_message(draft, email.message_id):
                        return draft
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
