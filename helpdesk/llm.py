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
    """Calls Gemini with a JSON schema. Rotates to the next model in `models` when the current one
    is unavailable for this key (404), out of daily quota (429 PerDay) or overloaded (503 twice)."""

    def __init__(self, api_key: str, models: list[str] | str, max_attempts: int = 4, timeout_s: int = 60):
        from google import genai

        self.client = genai.Client(api_key=api_key, http_options={"timeout": timeout_s * 1000})
        self.models = list(dict.fromkeys([models] if isinstance(models, str) else models))
        self.max_attempts = max_attempts
        self.calls = 0

    @property
    def model(self) -> str:
        return self.models[0] if self.models else "none"

    @property
    def daily_quota_exhausted(self) -> bool:
        return not self.models

    def _rotate(self, reason: str, drop: bool) -> bool:
        old = self.models.pop(0)
        if not drop:
            self.models.append(old)  # temporary problem: keep it at the back of the queue
        if self.models and self.models[0] != old:
            log.warning("    model '%s' %s -> switching to '%s'", old, reason, self.models[0])
            return True
        return False

    def generate(self, system: str, prompt: str, schema: type[T]) -> T:
        if not self.models:
            raise LLMUnavailable("all configured models are out of quota/unavailable")
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,
        )
        last_error, overloaded = "", 0
        for attempt in range(1, self.max_attempts + 1):
            wait = 2.0 * 2 ** (attempt - 1)
            try:
                self.calls += 1
                resp = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
                parsed = getattr(resp, "parsed", None)
                if isinstance(parsed, schema):
                    return parsed
                return schema.model_validate(json.loads(_strip_fences(resp.text or "")))
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = f"invalid JSON from model: {str(exc)[:150]}"
            except Exception as exc:  # network, 429, 5xx, timeouts
                msg = str(exc)
                last_error = msg[:200]
                if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) and "PerDay" in msg:
                    if not self._rotate("is out of daily quota", drop=True):
                        raise LLMUnavailable("daily quota exhausted on all models (429 PerDay)") from exc
                    wait = 0
                elif "404" in msg:
                    if not self._rotate("is not available for this key (404)", drop=True):
                        raise LLMUnavailable(f"no available model: {last_error}") from exc
                    wait = 0
                elif any(code in msg for code in ("400", "401", "403")) and "429" not in msg:
                    raise LLMUnavailable(f"non-retryable error: {last_error}") from exc
                elif "503" in msg or "UNAVAILABLE" in msg or "504" in msg or "timed out" in msg:
                    overloaded += 1
                    if overloaded >= 2 and self._rotate("is overloaded/slow", drop=False):
                        overloaded, wait = 0, 0
                else:
                    hinted = re.search(r"retry(?:Delay)?['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)s", msg, re.I)
                    if hinted:
                        wait = min(float(hinted.group(1)) + 1, 45)
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
