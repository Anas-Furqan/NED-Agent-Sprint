from ned_agent.classifier import AIProvider
from ned_agent.config import DEFAULT_CATEGORIES
from ned_agent.models import Classification, NormalizedEmail, Priority, RecommendedAction, Sentiment
from ned_agent.pipeline import Pipeline


class FakeGmail:
    def __init__(self):
        self.created = 0
        self.drafts = []

    def list_drafts(self):
        return self.drafts

    def create_draft(self, email, subject, body):
        self.created += 1
        result = {"id": f"draft-{self.created}", "message": {"threadId": email.thread_id}}
        self.drafts.append(result)
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
    gmail.drafts.append({"id": "existing", "message": {"threadId": live.thread_id}})
    result = Pipeline(db, policy, DEFAULT_CATEGORIES, gmail=gmail).process_email(live, True)
    assert result == "completed"
    assert gmail.created == 0
    assert db.list_messages("live")[0]["gmail_draft_id"] == "existing"


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


def test_failure_state_is_retryable(db, policy, email):
    class BrokenGmail(FakeGmail):
        def create_draft(self, email, subject, body):
            raise RuntimeError("authorization token=secret")

    result = Pipeline(db, policy, DEFAULT_CATEGORIES, gmail=BrokenGmail()).process_email(
        as_live(email), True
    )
    row = db.list_messages("live")[0]
    assert result == "failed" and row["retry_count"] == 1
    assert "secret" not in row["error"]
