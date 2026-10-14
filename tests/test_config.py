import pytest
from pydantic import ValidationError

from ned_agent.config import Settings, load_settings


def test_defaults_are_safe(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("GMAIL_SCOPES", "GEMINI_API_KEY", "GOOGLE_CLIENT_SECRETS_FILE"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings(tmp_path / "missing.env")
    assert all("gmail.send" not in scope for scope in settings.gmail_scopes)
    assert settings.gemini_api_key is None
    assert settings.missing_live_requirements()


def test_send_scope_is_rejected():
    with pytest.raises(ValidationError):
        Settings(gmail_scopes=("https://www.googleapis.com/auth/gmail.send",))


def test_invalid_numeric_environment_is_friendly_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("BATCH_SIZE", "not-a-number")
    with pytest.raises(ValidationError):
        load_settings(tmp_path / "none")
