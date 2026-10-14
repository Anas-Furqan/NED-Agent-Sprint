from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# Tried in order when the main model is unavailable or out of free-tier quota (separate quotas).
DEFAULT_FALLBACK_MODELS = "gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-3.5-flash"


@dataclass
class Settings:
    api_key: str | None
    model: str
    models: list[str]
    delay_seconds: float
    max_attempts: int


def load_settings(env_file: str | Path = ".env") -> Settings:
    if Path(env_file).exists():
        load_dotenv(env_file, override=False)
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
    model = (os.getenv("GEMINI_MODEL") or "gemini-3-flash-preview").replace("models/", "").strip()
    extra = os.getenv("GEMINI_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
    models = [model] + [m.strip().replace("models/", "") for m in extra.split(",") if m.strip()]
    return Settings(
        api_key=key.strip() if key else None,
        model=model,
        models=list(dict.fromkeys(models)),
        delay_seconds=float(os.getenv("AGENT_DELAY_SECONDS", "2")),
        max_attempts=int(os.getenv("AGENT_MAX_ATTEMPTS", "4")),
    )
