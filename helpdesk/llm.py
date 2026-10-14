"""Gemini client: one JSON call per email, schema-validated, with retry + backoff."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("helpdesk")
T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    """Raised when the model cannot produce a valid answer after all retries."""


class GeminiClient:
    def __init__(self, api_key: str, model: str, max_attempts: int = 4, timeout_s: int = 60):
        from google import genai

        self._genai = genai
        self.client = genai.Client(api_key=api_key, http_options={"timeout": timeout_s * 1000})
        self.model = model
        self.max_attempts = max_attempts
        self.daily_quota_exhausted = False
        self.calls = 0

    def generate(self, system: str, prompt: str, schema: type[T]) -> T:
        if self.daily_quota_exhausted:
            raise LLMUnavailable("daily quota exhausted earlier in this run")
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,
        )
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                self.calls += 1
                resp = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
                parsed = getattr(resp, "parsed", None)
                if isinstance(parsed, schema):
                    return parsed
                return schema.model_validate(json.loads(_strip_fences(resp.text or "")))
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = f"invalid JSON from model: {str(exc)[:150]}"
                wait = 1.5 * 2 ** (attempt - 1)
            except Exception as exc:  # network, 429, 5xx, timeouts
                msg = str(exc)
                last_error = msg[:200]
                if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) and "PerDay" in msg:
                    self.daily_quota_exhausted = True
                    raise LLMUnavailable("daily quota exhausted (429 PerDay)") from exc
                if any(code in msg for code in ("400", "401", "403", "404")) and "429" not in msg:
                    raise LLMUnavailable(f"non-retryable error: {last_error}") from exc
                hinted = re.search(r"retry(?:Delay)?['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)s", msg, re.I)
                wait = min(float(hinted.group(1)) + 1, 45) if hinted else 2.0 * 2 ** (attempt - 1)
            if attempt < self.max_attempts:
                log.warning("    LLM attempt %d/%d failed (%s); retrying in %.0fs",
                            attempt, self.max_attempts, last_error[:90], wait)
                time.sleep(wait)
        raise LLMUnavailable(f"gave up after {self.max_attempts} attempts: {last_error}")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return text
