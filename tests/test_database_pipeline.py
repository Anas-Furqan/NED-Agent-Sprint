from ned_agent.classifier import AIProvider
from ned_agent.config import DEFAULT_CATEGORIES
from ned_agent.demo import demo_emails
from ned_agent.gmail import AGENT_MESSAGE_HEADER
from ned_agent.models import Classification, NormalizedEmail, Priority, RecommendedAction, Sentiment
from ned_agent.pipeline import Pipeline


class FakeGmail:
    def __init__(self):
        self.created = 0
        self.drafts = []
        self.details = {}

    def list_drafts(self):
        return self.drafts

    def get_draft(self, draft_id):
        return self.details[draft_id]

    def create_draft(self, email, subject, body):
        self.created += 1
        result = {"id": f"draft-{self.created}", "message": {"threadId": email.thread_id}}
        self.drafts.append(result)
        self.details[result["id"]] = {
            **result,
            "message": {
                "threadId": email.thread_id,
                "payload": {"headers": [{"name": AGENT_MESSAGE_HEADER, "value": email.message_id}]},
            },
        }
        return result


def as_live(email: NormalizedEmail) -> NormalizedEmail:
    return email.model_copy(update={"source": "live"})


def test_schema_migration_and_unique_dedup(db, email):
    first, created = db.upsert_message(email)
    second, created_again = db.upsert_message(email)
    assert first == second
    assert created and not created_again
    with db.connect() as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert not {"settings", "locks", "sync_runs"} & tables


def test_repeated_demo_processing_is_idempotent(db, policy, email):
    pipeline = Pipeline(db, policy, DEFAULT_CATEGORIES)
    assert pipeline.process_email(email, False) == "completed"
    assert pipeline.process_email(email, False) == "duplicate"
    assert len(db.list_messages("demo")) == 1


def test_batch_runner_is_idempotent(db, policy, email):
    pipeline = Pipeline(db, policy, DEFAULT_CATEGORIES)
    first = pipeline.run([email], create_live_drafts=False)
    second = pipeline.run([email], create_live_drafts=False)
    assert first["completed"] == 1
    assert second["duplicate"] == 1
    assert len(db.list_messages("demo")) == 1


def test_demo_failure_scenarios_are_real_and_isolated(db, policy):
    fixtures = {item.demo_failure: item for item in demo_emails() if item.demo_failure}
    pipeline = Pipeline(db, policy, DEFAULT_CATEGORIES)
    assert pipeline.process_email(fixtures["llm"], False) == "completed"
    llm_row = next(
        row for row in db.list_messages("demo") if row["gmail_message_id"] == "demo-llm_failure"
    )
    assert llm_row["used_fallback"] == 1
    assert llm_row["provider"] == "rules-fallback"
    assert pipeline.process_email(fixtures["gmail"], False) == "failed"
    gmail_row = next(
        row for row in db.list_messages("demo") if row["gmail_message_id"] == "demo-gmail_failure"
    )
    assert "Simulated Gmail API failure" in gmail_row["error"]
    assert gmail_row["gmail_draft_id"] is None


def test_repeated_live_sync_creates_one_draft(db, policy, email):
    gmail = FakeGmail()
    live = as_live(email)
    pipeline = Pipeline(db, policy, DEFAULT_CATEGORIES, gmail=gmail)
    assert pipeline.process_email(live, True) == "completed"
    assert pipeline.process_email(live, True) == "duplicate"
    assert gmail.created == 1
    assert db.list_messages("live")[0]["gmail_draft_id"] == "draft-1"


def test_reconcile_existing_draft_prevents_creation(db, policy, email):
    gmail = FakeGmail()
    live = as_live(email)
    existing = {"id": "existing", "message": {"threadId": live.thread_id}}
    gmail.drafts.append(existing)
    gmail.details["existing"] = {
        **existing,
        "message": {
            "threadId": live.thread_id,
            "payload": {"headers": [{"name": AGENT_MESSAGE_HEADER, "value": live.message_id}]},
        },
    }
    result = Pipeline(db, policy, DEFAULT_CATEGORIES, gmail=gmail).process_email(live, True)
    assert result == "completed"
    assert gmail.created == 0
    assert db.list_messages("live")[0]["gmail_draft_id"] == "existing"


def test_manual_draft_in_same_thread_is_not_claimed(db, policy, email):
    gmail = FakeGmail()
    live = as_live(email)
    manual = {"id": "manual", "message": {"threadId": live.thread_id}}
    gmail.drafts.append(manual)
    gmail.details["manual"] = {
        **manual,
        "message": {"threadId": live.thread_id, "payload": {"headers": []}},
    }
    result = Pipeline(db, policy, DEFAULT_CATEGORIES, gmail=gmail).process_email(live, True)
    assert result == "completed"
    assert gmail.created == 1
    assert db.list_messages("live")[0]["gmail_draft_id"] == "draft-1"


def test_model_cannot_bypass_critical_escalation(db, policy, email):
    class PermissiveProvider(AIProvider):
        name = "permissive-test"

        def classify(self, email, categories, policy):
            return Classification(
                category="General Inquiry",
                priority=Priority.LOW,
                sentiment=Sentiment.NEUTRAL,
                intent_summary="Model considered this harmless",
                recommended_action=RecommendedAction.DRAFT_REPLY,
                confidence=99,
                reasoning="Unsafe permissive model output",
                provider=self.name,
            )

        def draft(self, email, classification, policy):
            return "This should never be saved."

    gmail = FakeGmail()
    legal = as_live(email).model_copy(
        update={
            "subject": "Formal legal notice",
            "body": "My lawyer will file a lawsuit and notify the regulator.",
        }
    )
    result = Pipeline(
        db, policy, DEFAULT_CATEGORIES, provider=PermissiveProvider(), gmail=gmail
    ).process_email(legal, True)
    row = db.list_messages("live")[0]
    assert result == "completed"
    assert row["recommended_action"] == "Escalate"
    assert row["draft_status"] == "disabled"
    assert gmail.created == 0


def test_demo_live_isolation(db, email):
    db.upsert_message(email)
    db.upsert_message(as_live(email))
    assert len(db.list_messages("demo")) == 1
    assert len(db.list_messages("live")) == 1
    db.clear_local_data("demo")
    assert not db.list_messages("demo") and db.list_messages("live")


def test_ambiguous_draft_failure_is_not_blindly_retried(db, policy, email):
    class BrokenGmail(FakeGmail):
        def create_draft(self, email, subject, body):
            self.created += 1
            raise RuntimeError("authorization token=secret")

    gmail = BrokenGmail()
    pipeline = Pipeline(db, policy, DEFAULT_CATEGORIES, gmail=gmail)
    live = as_live(email)
    result = pipeline.process_email(live, True)
    row = db.list_messages("live")[0]
    assert result == "failed" and row["retry_count"] == 1
    assert row["draft_status"] == "unknown"
    assert "secret" not in row["error"]
    assert pipeline.process_email(live, True) == "failed"
    assert gmail.created == 1
    assert "ambiguous" in db.list_messages("live")[0]["error"].lower()


def test_ambiguous_failure_reconciles_marked_draft(db, policy, email):
    class AmbiguousGmail(FakeGmail):
        def create_draft(self, email, subject, body):
            self.created += 1
            result = {"id": "accepted", "message": {"threadId": email.thread_id}}
            self.drafts.append(result)
            self.details["accepted"] = {
                **result,
                "message": {
                    "threadId": email.thread_id,
                    "payload": {
                        "headers": [{"name": AGENT_MESSAGE_HEADER, "value": email.message_id}]
                    },
                },
            }
            raise TimeoutError("response lost")

    gmail = AmbiguousGmail()
    pipeline = Pipeline(db, policy, DEFAULT_CATEGORIES, gmail=gmail)
    live = as_live(email)
    assert pipeline.process_email(live, True) == "failed"
    assert pipeline.process_email(live, True) == "completed"
    row = db.list_messages("live")[0]
    assert row["gmail_draft_id"] == "accepted"
    assert gmail.created == 1


def test_definitive_authorization_failure_can_retry_safely(db, policy, email):
    class AuthorizationError(Exception):
        resp = type("Response", (), {"status": 401})()

    class ReauthorizedGmail(FakeGmail):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def create_draft(self, email, subject, body):
            self.attempts += 1
            if self.attempts == 1:
                raise AuthorizationError("expired access_token=secret")
            return super().create_draft(email, subject, body)

    gmail = ReauthorizedGmail()
    pipeline = Pipeline(db, policy, DEFAULT_CATEGORIES, gmail=gmail)
    live = as_live(email)
    assert pipeline.process_email(live, True) == "failed"
    assert db.list_messages("live")[0]["draft_status"] == "failed"
    assert pipeline.process_email(live, True) == "completed"
    assert gmail.attempts == 2
