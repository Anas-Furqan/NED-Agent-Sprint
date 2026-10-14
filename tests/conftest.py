from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest

from ned_agent.database import Database
from ned_agent.models import NormalizedEmail
from ned_agent.policy import load_policy


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.sqlite3")


@pytest.fixture
def policy():
    return load_policy("policies/company_policy.md")


@pytest.fixture
def email():
    return NormalizedEmail(
        message_id="m-1",
        thread_id="t-1",
        sender="A Customer <customer@example.test>",
        sender_email="customer@example.test",
        recipients=["support@example.test"],
        subject="Duplicate charge",
        timestamp=datetime.now(UTC),
        body="I was charged twice on invoice 42. Please help.",
        snippet="I was charged twice",
        labels=["INBOX", "UNREAD"],
        unread=True,
        rfc_message_id="<m-1@example.test>",
        source="demo",
    )


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.fixture
def gmail_raw():
    return {
        "id": "abc",
        "threadId": "thread",
        "internalDate": "1710000000000",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Preview &amp; text",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "Jane <jane@example.test>"},
                {"name": "To", "value": "support@example.test"},
                {"name": "Subject", "value": "Nested MIME"},
                {"name": "Date", "value": "Sat, 09 Mar 2024 16:00:00 +0000"},
                {"name": "Message-ID", "value": "<abc@example.test>"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "headers": [],
                    "body": {"data": b64("Plain body")},
                },
                {
                    "mimeType": "text/html",
                    "filename": "",
                    "headers": [],
                    "body": {"data": b64("<b>HTML body</b><script>bad()</script>")},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "untrusted.pdf",
                    "body": {"attachmentId": "x"},
                },
            ],
        },
    }
