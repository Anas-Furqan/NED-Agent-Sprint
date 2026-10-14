from __future__ import annotations

import base64
import html
import logging
import os
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, TypeVar

import bleach
from bs4 import BeautifulSoup

from .models import NormalizedEmail

T = TypeVar("T")
LOGGER = logging.getLogger(__name__)
AGENT_MESSAGE_HEADER = "X-NED-Agent-Message-ID"


class GmailError(RuntimeError):
    """A user-safe Gmail integration error."""


def _decode(data: str | None) -> bytes:
    if not data:
        return b""
    try:
        padded = (data + "=" * (-len(data) % 4)).encode("ascii")
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as error:
        raise GmailError("Gmail returned malformed base64 content") from error


def _charset(content_type: str) -> str:
    match = re.search(r"charset=[\"']?([^;\"']+)", content_type or "", re.I)
    return match.group(1).strip() if match else "utf-8"


def _text(data: bytes, charset: str = "utf-8") -> str:
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _parts(part: dict[str, Any]) -> tuple[list[str], list[str]]:
    plain: list[str] = []
    rich: list[str] = []
    mime = part.get("mimeType", "")
    filename = part.get("filename") or ""
    headers = {h.get("name", "").lower(): h.get("value", "") for h in part.get("headers", [])}
    if filename or part.get("body", {}).get("attachmentId"):
        return plain, rich
    for child in part.get("parts") or []:
        child_plain, child_rich = _parts(child)
        plain.extend(child_plain)
        rich.extend(child_rich)
    raw = _decode(part.get("body", {}).get("data"))
    if raw and mime == "text/plain":
        plain.append(_text(raw, _charset(headers.get("content-type", ""))))
    elif raw and mime == "text/html":
        decoded = _text(raw, _charset(headers.get("content-type", "")))
        cleaned = bleach.clean(decoded, tags=[], strip=True)
        rich.append(BeautifulSoup(html.unescape(cleaned), "html.parser").get_text(" ", strip=True))
    return plain, rich


def parse_message(raw: dict[str, Any], source: str = "live") -> NormalizedEmail:
    payload = raw.get("payload") or {}
    headers = {
        item.get("name", "").lower(): item.get("value", "")
        for item in payload.get("headers") or []
        if item.get("name")
    }
    plain, rich = _parts(payload)
    body = "\n\n".join(item.strip() for item in (plain or rich) if item.strip())
    sender = headers.get("from", "")
    _, sender_email = parseaddr(sender)
    recipients = [
        address.strip() for address in headers.get("to", "").split(",") if address.strip()
    ]
    timestamp: datetime
    try:
        timestamp = parsedate_to_datetime(headers.get("date", ""))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
    except (TypeError, ValueError, OverflowError):
        timestamp = datetime.fromtimestamp(int(raw.get("internalDate", "0")) / 1000, UTC)
    message_id = str(raw.get("id") or "").strip()
    if not message_id:
        raise GmailError("Gmail message has no ID")
    thread_id = str(raw.get("threadId") or message_id)
    labels = [str(item) for item in raw.get("labelIds") or []]
    return NormalizedEmail(
        message_id=message_id,
        thread_id=thread_id,
        sender=sender,
        sender_email=sender_email,
        recipients=recipients,
        subject=headers.get("subject", "(no subject)"),
        timestamp=timestamp,
        body=body,
        snippet=html.unescape(raw.get("snippet") or ""),
        labels=labels,
        unread="UNREAD" in labels,
        rfc_message_id=headers.get("message-id"),
        references=headers.get("references"),
        source=source,
    )


def with_retry(operation: Callable[[], T], attempts: int = 3, base_delay: float = 0.5) -> T:
    last: Exception | None = None
    for number in range(attempts):
        try:
            return operation()
        except Exception as error:  # provider exceptions vary by dependency version
            last = error
            status = getattr(getattr(error, "resp", None), "status", None)
            transient_transport = isinstance(error, (TimeoutError, ConnectionError)) or type(
                error
            ).__name__ in {"Timeout", "ReadTimeout", "ConnectTimeout", "TransportError"}
            if (
                not transient_transport and status not in (408, 429, 500, 502, 503, 504)
            ) or number == attempts - 1:
                raise
            time.sleep(base_delay * (2**number) + random.uniform(0, 0.1))
    raise GmailError("Retry operation failed") from last


def authenticate(
    client_secrets: Path, token_file: Path, scopes: tuple[str, ...], force: bool = False
) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as error:
        raise GmailError("Google OAuth dependencies are not installed") from error
    if not client_secrets.is_file():
        raise GmailError(f"OAuth client file not found: {client_secrets}")
    credentials = None
    if token_file.exists() and not force:
        try:
            credentials = Credentials.from_authorized_user_file(str(token_file), scopes)
        except (ValueError, OSError):
            credentials = None
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            credentials = None
    try:
        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes)
            credentials = flow.run_local_server(port=0, open_browser=True)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(credentials.to_json(), encoding="utf-8")
    except Exception as error:
        raise GmailError(
            "OAuth authorization failed. Verify the Desktop client, test user, and consent settings."
        ) from error
    try:
        os.chmod(token_file, 0o600)
    except OSError:  # noqa: S110 - Windows may not implement POSIX modes
        pass
    return credentials


class GmailClient:
    def __init__(self, service: Any):
        self.service = service

    @classmethod
    def from_credentials(cls, credentials: Any) -> GmailClient:
        try:
            from googleapiclient.discovery import build
        except ImportError as error:
            raise GmailError("Google API client dependency is not installed") from error
        return cls(build("gmail", "v1", credentials=credentials, cache_discovery=False))

    def profile(self) -> dict[str, Any]:
        return with_retry(lambda: self.service.users().getProfile(userId="me").execute())

    def fetch_messages(self, query: str, max_results: int) -> list[NormalizedEmail]:
        items: list[dict[str, str]] = []
        page_token: str | None = None
        while len(items) < max_results:
            size = min(100, max_results - len(items))
            response = with_retry(
                lambda size=size, page_token=page_token: (
                    self.service.users()
                    .messages()
                    .list(userId="me", q=query, maxResults=size, pageToken=page_token)
                    .execute()
                )
            )
            items.extend(response.get("messages") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        parsed: list[NormalizedEmail] = []
        for item in items[:max_results]:
            raw = with_retry(
                lambda item_id=item["id"]: (
                    self.service.users()
                    .messages()
                    .get(userId="me", id=item_id, format="full")
                    .execute()
                )
            )
            try:
                parsed.append(parse_message(raw))
            except Exception as error:
                LOGGER.warning(
                    "Message %s could not be fully parsed: %s", item.get("id"), type(error).__name__
                )
                parsed.append(
                    NormalizedEmail(
                        message_id=str(raw.get("id") or item["id"]),
                        thread_id=str(raw.get("threadId") or item["id"]),
                        sender="",
                        sender_email="",
                        recipients=[],
                        subject="(malformed message — manual review)",
                        timestamp=datetime.now(UTC),
                        body="",
                        snippet=html.unescape(raw.get("snippet") or "")[:500],
                        labels=[str(label) for label in raw.get("labelIds") or []],
                        unread="UNREAD" in (raw.get("labelIds") or []),
                        source="live",
                    )
                )
        return parsed

    def list_drafts(self, max_results: int = 500) -> list[dict[str, Any]]:
        drafts: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(drafts) < max_results:
            size = min(100, max_results - len(drafts))
            response = with_retry(
                lambda size=size, page_token=page_token: (
                    self.service.users()
                    .drafts()
                    .list(userId="me", maxResults=size, pageToken=page_token)
                    .execute()
                )
            )
            drafts.extend(response.get("drafts") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return drafts[:max_results]

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return with_retry(
            lambda: (
                self.service.users().drafts().get(userId="me", id=draft_id, format="full").execute()
            )
        )

    def create_draft(self, email: NormalizedEmail, subject: str, body: str) -> dict[str, Any]:
        payload = build_draft_payload(email, subject, body)
        # Never automatically retry a non-idempotent create. The caller reconciles marked drafts
        # before deciding whether a later attempt is safe.
        result = self.service.users().drafts().create(userId="me", body=payload).execute()
        if not result.get("id"):
            raise GmailError("Gmail did not return a draft ID")
        return result


def build_draft_payload(email: NormalizedEmail, subject: str, body: str) -> dict[str, Any]:
    message = EmailMessage()
    message["To"] = email.sender_email
    message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    message[AGENT_MESSAGE_HEADER] = email.message_id
    if email.rfc_message_id:
        message["In-Reply-To"] = email.rfc_message_id
        refs = f"{email.references or ''} {email.rfc_message_id}".strip()
        message["References"] = refs
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    return {"message": {"raw": raw, "threadId": email.thread_id}}


def draft_matches_message(draft: dict[str, Any], message_id: str) -> bool:
    headers = draft.get("message", {}).get("payload", {}).get("headers", [])
    return any(
        header.get("name", "").casefold() == AGENT_MESSAGE_HEADER.casefold()
        and header.get("value") == message_id
        for header in headers
    )
