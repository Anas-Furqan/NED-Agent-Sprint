from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PolicyDocument:
    text: str
    sha256: str
    sections: dict[str, str]

    def relevant(self, categories: list[str], limit: int = 12000) -> str:
        keys = {item.lower() for item in categories}
        chosen = [
            value for title, value in self.sections.items() if any(key in title for key in keys)
        ]
        common = [
            value
            for title, value in self.sections.items()
            if title
            in {"response tone and professionalism", "escalation conditions", "missing information"}
        ]
        return "\n\n".join(dict.fromkeys(common + chosen))[:limit] or self.text[:limit]


def load_policy(path: str | Path) -> PolicyDocument:
    policy_path = Path(path)
    if not policy_path.is_file():
        raise FileNotFoundError(f"Policy file not found: {policy_path}")
    text = policy_path.read_text(encoding="utf-8")
    if len(text.strip()) < 100:
        raise ValueError("Policy file is unexpectedly empty")
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^##\s+(.+)$", text, re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).strip().lower()] = text[match.start() : end].strip()
    return PolicyDocument(
        text=text, sha256=hashlib.sha256(text.encode()).hexdigest(), sections=sections
    )
