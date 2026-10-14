from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)
DEFAULT_CATEGORIES = (
    "Billing",
    "Refund",
    "Technical Support",
    "Account Access",
    "Order or Service Issue",
    "General Inquiry",
    "Feedback",
    "Security or Privacy",
    "Legal",
    "Other",
)


class Settings(BaseModel):
    app_env: str = "development"
    database_path: Path = Path("data/ned_agent.sqlite3")
    google_client_secrets_file: Path = Path("credentials/client_secret.json")
    gmail_scopes: tuple[str, ...] = DEFAULT_SCOPES
    gmail_query: str = "in:inbox newer_than:30d"
    poll_interval_seconds: int = Field(default=60, ge=15, le=3600)
    batch_size: int = Field(default=25, ge=1, le=100)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    policy_file: Path = Path("policies/company_policy.md")
    categories: tuple[str, ...] = DEFAULT_CATEGORIES
    log_level: str = "INFO"

    @field_validator("gmail_scopes")
    @classmethod
    def scopes_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        forbidden = ("gmail.send", "mail.google.com")
        if any(any(item in scope for item in forbidden) for scope in value):
            raise ValueError("Sending or full-mailbox OAuth scopes are forbidden")
        required = set(DEFAULT_SCOPES)
        if not required.issubset(value):
            raise ValueError("gmail.readonly and gmail.compose scopes are required")
        return value

    @field_validator("gmail_query")
    @classmethod
    def query_is_bounded(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Gmail query cannot be empty")
        return value

    @property
    def token_file(self) -> Path:
        return self.google_client_secrets_file.parent / "token.json"

    def missing_live_requirements(self) -> list[str]:
        missing: list[str] = []
        if not self.google_client_secrets_file.is_file():
            missing.append("Google OAuth desktop client JSON")
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY (rule-based fallback remains available)")
        return missing


def load_settings(env_file: str | Path = ".env") -> Settings:
    path = Path(env_file)
    if path.exists():
        load_dotenv(path, override=False)
    scopes = tuple(
        item.strip()
        for item in os.getenv("GMAIL_SCOPES", ",".join(DEFAULT_SCOPES)).split(",")
        if item.strip()
    )
    categories = tuple(
        item.strip()
        for item in os.getenv("CATEGORIES", ",".join(DEFAULT_CATEGORIES)).split(",")
        if item.strip()
    )
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        database_path=Path(os.getenv("DATABASE_PATH", "data/ned_agent.sqlite3")),
        google_client_secrets_file=Path(
            os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials/client_secret.json")
        ),
        gmail_scopes=scopes,
        gmail_query=os.getenv("GMAIL_QUERY", "in:inbox newer_than:30d"),
        poll_interval_seconds=os.getenv("POLL_INTERVAL_SECONDS", "60"),
        batch_size=os.getenv("BATCH_SIZE", "25"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        policy_file=Path(os.getenv("POLICY_FILE", "policies/company_policy.md")),
        categories=categories or DEFAULT_CATEGORIES,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
