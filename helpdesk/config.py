from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    api_key: str | None
    model: str
    delay_seconds: float
    max_attempts: int


def load_settings(env_file: str | Path = ".env") -> Settings:
    if Path(env_file).exists():
        load_dotenv(env_file, override=False)
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
    model = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").replace("models/", "").strip()
    return Settings(
        api_key=key.strip() if key else None,
        model=model,
        delay_seconds=float(os.getenv("AGENT_DELAY_SECONDS", "2")),
        max_attempts=int(os.getenv("AGENT_MAX_ATTEMPTS", "4")),
    )
