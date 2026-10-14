# Architecture

## Components

- `app.py`: Streamlit presentation, connection controls, sync actions, filters, and review workflow.
- `config.py`: environment loading and Pydantic validation, including forbidden OAuth scopes.
- `gmail.py`: installed-app OAuth, recursive MIME parsing, pagination/retries, threaded draft payloads.
- `classifier.py`: provider interface, Gemini structured output, bounded validation retries, transparent
  deterministic fallback.
- `policy.py`, `reply.py`: versioned trusted policy context and post-generation safety checks.
- `security.py`, `escalation.py`: code-enforced injection and high-risk overrides independent of AI.
- `pipeline.py`: idempotent state machine and draft reconciliation.
- `database.py`: versioned SQLite initialization, transactions, unique constraints, claims, audit trail.

## Data and state flow

Messages are normalized in memory, deduplicated on `(source, gmail_message_id)`, and atomically claimed.
Classification and reply previews are saved before any draft request. A message is completed only after
all required actions succeed. Failure increments a retry count and leaves the item retryable. Before a
new draft is created, Gmail drafts are reconciled by thread ID to cover the failure window where Gmail
succeeded but the local database write did not.

Demo and live rows use separate source partitions. A unique Gmail draft ID index and message key prevent
local duplicates; transactional claims prevent overlapping workers from processing the same row.

## State model

`pending → processing → completed` is the normal path. Errors use `processing → failed`; a later run can
claim `failed` safely. Draft state is independently `none`, `local_preview`, `pending`, `saved`, or
`disabled`. Gmail unread status is stored separately and never changed.

The current migration version is recorded in `schema_version`. Startup creates missing parent folders,
uses SQLite WAL mode and foreign keys, and applies migrations in an immediate transaction.
