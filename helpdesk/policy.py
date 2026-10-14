"""Parse policy.md dynamically into sections, rule codes and response windows.

Nothing about the policy content is hardcoded here: codes, texts and time windows
are all read from the markdown file at runtime.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

RULE_LINE = re.compile(r"^\s*(?:[-*]\s*)?\**([A-Z][A-Z0-9]*-\d+)\**\s*[:.\-]\s*(.+?)\s*$")
HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
# Windows the *university* commits to (not deadlines the student must meet).
RESPONSE_WINDOW = re.compile(
    r"(?i)(?:resolved|respond\w*|response|processed|outcome comes)[^.]{0,80}?"
    r"within\s+(\d+)\s+(working days|business days|hours|days)"
)
STOPWORDS = set(
    """a an the and or of to in on for with by is are be can only after before within
    from their they this that it as at if not no any all must up per gets get has have
    student students through who which there them its will may out into one two three four
    five first last day days week weeks hour hours working please also need""".split()
)


@dataclass
class Rule:
    code: str
    text: str
    section: str
    windows: list[tuple[int, str]] = field(default_factory=list)  # (amount, unit)


@dataclass
class Policy:
    text: str
    rules: dict[str, Rule]
    sections: dict[str, str]

    @property
    def codes(self) -> set[str]:
        return set(self.rules)

    def section_of(self, code: str) -> str:
        rule = self.rules.get(code)
        return rule.section if rule else ""

    def find_rules(self, keywords: list[str], section_hint: str | None = None) -> list[str]:
        """Codes whose text mentions any keyword (case-insensitive), optional section filter."""
        found = []
        for code, rule in self.rules.items():
            if section_hint and section_hint.lower() not in rule.section.lower():
                continue
            low = rule.text.lower()
            if any(k.lower() in low for k in keywords):
                found.append(code)
        return found

    def rules_in_section(self, section_keyword: str) -> list[str]:
        return [c for c, r in self.rules.items() if section_keyword.lower() in r.section.lower()]

    def best_matching(self, text: str, candidates: list[str] | None = None, top: int = 2) -> list[str]:
        """Rank rules by IDF-weighted word overlap with text (simple, transparent retrieval)."""
        tf = Counter(_stem_list(text))
        stems = {c: _stems(r.text) for c, r in self.rules.items()}
        n = len(stems)
        scored = []
        for code in candidates if candidates is not None else self.rules:
            shared = set(tf) & stems[code]
            score = sum(min(tf[w], 3) * math.log((n + 1) / sum(1 for s in stems.values() if w in s)) for w in shared)
            if score > 0.5:
                scored.append((score, code))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [c for _, c in scored[:top]]


def _stem(word: str) -> str:
    if word.endswith("ied"):
        word = word[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            break
    return word[:6]


def _stem_list(text: str) -> list[str]:
    tokens = re.findall(r"[a-z][a-z\-]+", text.lower())
    return [_stem(t) for t in tokens if t not in STOPWORDS and len(t) > 2]


def _stems(text: str) -> set[str]:
    return set(_stem_list(text))


def parse_policy(text: str) -> Policy:
    rules: dict[str, Rule] = {}
    sections: dict[str, list[str]] = {}
    current = "General"
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            current = heading.group(1).strip()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
        match = RULE_LINE.match(line)
        if match:
            code, body = match.group(1), match.group(2)
            windows = [(int(n), u.lower().replace("business", "working")) for n, u in RESPONSE_WINDOW.findall(body)]
            rules[code] = Rule(code=code, text=body, section=current, windows=windows)
    return Policy(
        text=text,
        rules=rules,
        sections={k: "\n".join(v).strip() for k, v in sections.items()},
    )


def load_policy(path: str | Path) -> Policy:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Policy file not found: {p}")
    policy = parse_policy(p.read_text(encoding="utf-8"))
    if not policy.rules:
        raise ValueError(f"No rule codes (like ABC-1:) found in {p}")
    return policy
