from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.transaction(immediate=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = db.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            version = int(row[0]) if row else 0
            if version < 1:
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY,
                        source TEXT NOT NULL CHECK(source IN ('live','demo')),
                        gmail_message_id TEXT NOT NULL,
                        thread_id TEXT NOT NULL,
                        sender TEXT NOT NULL DEFAULT '',
                        sender_email TEXT NOT NULL DEFAULT '',
                        recipients_json TEXT NOT NULL DEFAULT '[]',
                        subject TEXT NOT NULL DEFAULT '',
                        received_at TEXT NOT NULL,
                        snippet TEXT NOT NULL DEFAULT '',
                        body_preview TEXT NOT NULL DEFAULT '',
                        labels_json TEXT NOT NULL DEFAULT '[]',
                        unread INTEGER NOT NULL DEFAULT 0,
                        rfc_message_id TEXT,
                        references_header TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        category TEXT,
                        priority TEXT,
                        sentiment TEXT,
                        intent_summary TEXT,
                        recommended_action TEXT,
                        confidence INTEGER,
                        reasoning TEXT,
                        escalation_reasons_json TEXT NOT NULL DEFAULT '[]',
                        policy_references_json TEXT NOT NULL DEFAULT '[]',
                        missing_information_json TEXT NOT NULL DEFAULT '[]',
                        used_fallback INTEGER NOT NULL DEFAULT 0,
                        provider TEXT,
                        policy_hash TEXT,
                        reply_subject TEXT,
                        reply_preview TEXT,
                        draft_status TEXT NOT NULL DEFAULT 'none',
                        gmail_draft_id TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        error TEXT,
                        claimed_at TEXT,
                        processed_at TEXT,
                        processing_ms INTEGER,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(source, gmail_message_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(source, status);
                    CREATE INDEX IF NOT EXISTS idx_messages_received ON messages(received_at DESC);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_unique
                        ON messages(source, gmail_draft_id) WHERE gmail_draft_id IS NOT NULL;
                    CREATE TABLE IF NOT EXISTS escalations (
                        id INTEGER PRIMARY KEY,
                        message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
                        severity TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        evidence TEXT NOT NULL,
                        policy_reference TEXT,
                        recommended_human_action TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'open',
                        reviewed_at TEXT,
                        review_note TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_escalations_status ON escalations(status, severity);
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id INTEGER PRIMARY KEY,
                        source TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                        detail TEXT NOT NULL DEFAULT '',
                        level TEXT NOT NULL DEFAULT 'info',
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
                    CREATE TABLE IF NOT EXISTS sync_runs (
                        id INTEGER PRIMARY KEY,
                        source TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        status TEXT NOT NULL,
                        fetched_count INTEGER NOT NULL DEFAULT 0,
                        processed_count INTEGER NOT NULL DEFAULT 0,
                        error TEXT
                    );
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS locks (
                        name TEXT PRIMARY KEY,
                        owner TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );
                    """
                )
                db.execute("DELETE FROM schema_version")
                db.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat()

    def audit(
        self,
        source: str,
        event_type: str,
        detail: str = "",
        level: str = "info",
        message_id: int | None = None,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO audit_events(source,event_type,message_id,detail,level,created_at) VALUES(?,?,?,?,?,?)",
                (source, event_type, message_id, detail[:1000], level, self.now()),
            )

    def upsert_message(self, email: Any) -> tuple[int, bool]:
        now = self.now()
        with self.transaction(immediate=True) as db:
            existing = db.execute(
                "SELECT id FROM messages WHERE source=? AND gmail_message_id=?",
                (email.source, email.message_id),
            ).fetchone()
            if existing:
                return int(existing["id"]), False
            cursor = db.execute(
                """INSERT INTO messages(
                    source,gmail_message_id,thread_id,sender,sender_email,recipients_json,subject,
                    received_at,snippet,body_preview,labels_json,unread,rfc_message_id,references_header,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    email.source,
                    email.message_id,
                    email.thread_id,
                    email.sender,
                    email.sender_email,
                    json.dumps(email.recipients),
                    email.subject,
                    email.timestamp.isoformat(),
                    email.snippet[:500],
                    email.body[:4000],
                    json.dumps(email.labels),
                    int(email.unread),
                    email.rfc_message_id,
                    email.references,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid), True

    def claim_message(self, message_id: int, stale_seconds: int = 600) -> bool:
        cutoff = datetime.fromtimestamp(
            datetime.now(UTC).timestamp() - stale_seconds, UTC
        ).isoformat()
        with self.transaction(immediate=True) as db:
            cursor = db.execute(
                """UPDATE messages SET status='processing', claimed_at=?, updated_at=?
                WHERE id=? AND (status IN ('pending','failed') OR (status='processing' AND claimed_at<?))""",
                (self.now(), self.now(), message_id, cutoff),
            )
            return cursor.rowcount == 1

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            return dict(row) if row else None

    def list_messages(self, source: str, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM messages WHERE source=? ORDER BY received_at DESC LIMIT ?",
                    (source, limit),
                ).fetchall()
            ]

    def list_escalations(self, source: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT e.*,m.subject,m.sender,m.gmail_message_id FROM escalations e
                    JOIN messages m ON m.id=e.message_id WHERE m.source=? ORDER BY e.created_at DESC""",
                    (source,),
                ).fetchall()
            ]

    def list_audit(self, source: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audit_events WHERE source=? ORDER BY created_at DESC LIMIT ?",
                    (source, limit),
                ).fetchall()
            ]

    def review_escalation(self, escalation_id: int, note: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE escalations SET status='reviewed',reviewed_at=?,review_note=?,updated_at=? WHERE id=?",
                (self.now(), note[:1000], self.now(), escalation_id),
            )

    def clear_local_data(self, source: str) -> None:
        with self.transaction(immediate=True) as db:
            db.execute("DELETE FROM audit_events WHERE source=?", (source,))
            db.execute("DELETE FROM sync_runs WHERE source=?", (source,))
            db.execute("DELETE FROM messages WHERE source=?", (source,))
