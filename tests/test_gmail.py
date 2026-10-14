import base64
import inspect
from email import message_from_bytes

import pytest

import ned_agent.gmail as gmail_module
from ned_agent.config import DEFAULT_SCOPES
from ned_agent.gmail import (
    GmailClient,
    GmailError,
    authenticate,
    build_draft_payload,
    parse_message,
    with_retry,
)


def test_recursive_mime_prefers_plain_and_ignores_attachment(gmail_raw):
    parsed = parse_message(gmail_raw)
    assert parsed.body == "Plain body"
    assert parsed.sender_email == "jane@example.test"
    assert parsed.unread is True
    assert "untrusted.pdf" not in parsed.body


def test_malformed_base64_is_handled(gmail_raw):
    gmail_raw["payload"]["parts"][0]["body"]["data"] = "%%%"
    with pytest.raises(GmailError):
        parse_message(gmail_raw)


def test_draft_payload_preserves_threading(email):
    payload = build_draft_payload(email, email.subject, "Safe response")
    assert payload["message"]["threadId"] == "t-1"
    raw = payload["message"]["raw"] + "=" * (-len(payload["message"]["raw"]) % 4)
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert message["To"] == "customer@example.test"
    assert message["In-Reply-To"] == "<m-1@example.test>"
    assert message["Subject"].startswith("Re:")


def test_retry_is_bounded(monkeypatch):
    calls = 0

    class Error(Exception):
        resp = type("Response", (), {"status": 503})()

    def operation():
        nonlocal calls
        calls += 1
        raise Error("temporary")

    monkeypatch.setattr("ned_agent.gmail.time.sleep", lambda _: None)
    with pytest.raises(Error):
        with_retry(operation, attempts=3, base_delay=0)
    assert calls == 3


def test_client_has_no_send_method():
    assert not hasattr(GmailClient, "send")
    assert not hasattr(GmailClient, "send_message")
    source = inspect.getsource(gmail_module)
    assert ".messages().send(" not in source
    assert ".drafts().send(" not in source
    assert "gmail.send" not in source


def test_draft_listing_paginates_for_reconciliation():
    class Request:
        def __init__(self, response):
            self.response = response

        def execute(self):
            return self.response

    class Service:
        def __init__(self):
            self.pages = [
                {"drafts": [{"id": "d1"}], "nextPageToken": "next"},
                {"drafts": [{"id": "d2"}]},
            ]
            self.tokens = []

        def users(self):
            return self

        def drafts(self):
            return self

        def list(self, **kwargs):
            self.tokens.append(kwargs.get("pageToken"))
            return Request(self.pages.pop(0))

    service = Service()
    drafts = GmailClient(service).list_drafts()
    assert [item["id"] for item in drafts] == ["d1", "d2"]
    assert service.tokens == [None, "next"]


def test_oauth_installed_app_flow_is_mocked(monkeypatch, tmp_path):
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_file = tmp_path / "client.json"
    client_file.write_text("{}", encoding="utf-8")
    token_file = tmp_path / "token.json"

    class Credentials:
        valid = True
        expired = False
        refresh_token = None

        def to_json(self):
            return '{"mock": true}'

    class Flow:
        def run_local_server(self, port, open_browser):
            assert port == 0 and open_browser is True
            return Credentials()

    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", lambda path, scopes: Flow())
    result = authenticate(client_file, token_file, DEFAULT_SCOPES)
    assert result.valid and token_file.exists()


def test_oauth_error_is_user_safe(monkeypatch, tmp_path):
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_file = tmp_path / "client.json"
    client_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        InstalledAppFlow,
        "from_client_secrets_file",
        lambda path, scopes: (_ for _ in ()).throw(ValueError("secret internal detail")),
    )
    with pytest.raises(GmailError, match="OAuth authorization failed") as caught:
        authenticate(client_file, tmp_path / "token.json", DEFAULT_SCOPES)
    assert "secret internal detail" not in str(caught.value)
