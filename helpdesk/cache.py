"""Decision cache: same email bytes + same policy + same prompt version = same decision, no LLM call.

The key includes the policy text, so editing policy.md invalidates every cached decision.
Only real LLM decisions are cached (never fallback or invalid-input results).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

PROMPT_VERSION = "v3"


def cache_key(raw: bytes, policy_text: str, model_tag: str = "") -> str:
    h = hashlib.sha256()
    for part in (raw, policy_text.encode("utf-8"), PROMPT_VERSION.encode(), model_tag.encode()):
        h.update(part)
        h.update(b"\x00")
    return h.hexdigest()


def content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


class DecisionCache:
    def __init__(self, folder: Path = Path(".cache"), enabled: bool = True):
        self.folder = folder
        self.enabled = enabled

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        path = self.folder / f"{key}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def put(self, key: str, value: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.folder / f"{key}.json", value)


def atomic_write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)
