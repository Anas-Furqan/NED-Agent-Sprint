from __future__ import annotations

import logging
import re

SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|authorization|"
    r"password|\btoken|\bkey)\s*[:=]\s*[^\s,;&]+"
)
GOOGLE_API_KEY = re.compile(r"AIza[0-9A-Za-z_-]{20,}")


def sanitize_error(error: object) -> str:
    text = str(error).replace("\n", " ")[:1000]
    text = SECRET_PATTERN.sub(r"\1=[REDACTED]", text)
    return GOOGLE_API_KEY.sub("[REDACTED_GOOGLE_API_KEY]", text)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
