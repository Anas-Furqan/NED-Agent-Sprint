"""Deterministic elapsed-time reasoning against policy response windows.

"Now" is the email's received_at (not the machine clock) so runs are reproducible.
Durations are pulled from each sentence and matched to the policy rule that sentence
is about (word overlap); the rule's response window is parsed from policy.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .policy import Policy

NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "panch": 5, "chay": 6, "saat": 7, "das": 10,
}
UNIT_HOURS = {"hour": 1, "day": 24, "week": 24 * 7, "month": 24 * 30,
              "ghant": 1, "din": 24, "hafte": 24 * 7, "hafta": 24 * 7, "mahina": 24 * 30, "mahine": 24 * 30}
NUM = r"(\d+|" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r")"
UNIT = r"(hours?|days?|weeks?|months?|ghant[ae]|din|hafte|hafta|mahin[ae])"
DURATION_PATTERNS = [
    re.compile(rf"(?i)\b{NUM}\s+{UNIT}\s+(ago|pehle|pehlay|se)\b"),
    re.compile(rf"(?i)\b(?:for|since)\s+(?:the\s+(?:last|past)\s+)?{NUM}\s+{UNIT}\b"),
    re.compile(rf"(?i)\b(?:last|past)\s+{NUM}\s+{UNIT}\b"),
]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SINCE_WEEKDAY = re.compile(r"(?i)\bsince\s+(?:last\s+)?(" + "|".join(WEEKDAYS) + r")\b")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?\n])\s+|\s(?=\d\)\s)")


@dataclass
class OverdueFinding:
    code: str
    elapsed_text: str
    elapsed_hours: float
    window_text: str
    overdue: bool


def parse_received(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def working_days_between(start: datetime, end: datetime) -> float:
    """Count Mon-Fri days between start and end (fractional days ignored)."""
    if end <= start:
        return 0
    days, cur = 0, start
    while cur + timedelta(days=1) <= end:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def _durations(sentence: str, now: datetime | None) -> list[tuple[str, float]]:
    out = []
    for pat in DURATION_PATTERNS:
        for m in pat.finditer(sentence):
            n_raw, unit_raw = m.group(1).lower(), m.group(2).lower()
            n = int(n_raw) if n_raw.isdigit() else NUM_WORDS.get(n_raw, 0)
            unit_key = next((k for k in UNIT_HOURS if unit_raw.startswith(k)), None)
            if n and unit_key:
                out.append((m.group(0), float(n * UNIT_HOURS[unit_key])))
    if now:
        m = SINCE_WEEKDAY.search(sentence)
        if m:
            target = WEEKDAYS.index(m.group(1).lower())
            delta_days = (now.weekday() - target) % 7
            start = (now - timedelta(days=delta_days)).replace(hour=9, minute=0, second=0)
            out.append((m.group(0), max(0.0, (now - start).total_seconds() / 3600)))
    return out


SOON = (r"(tonight|today|tomorrow|this (evening|afternoon|week)|in (\d+|a few|two|three) hours|within "
        r"(24|48) hours|aaj( raat)?|aj|kal|kl|parson|parso|abhi|subah tak|raat tak|shaam tak|"
        r"by (monday|tuesday|wednesday|thursday|friday|saturday|sunday))")
ADMIN = r"(deadline|due date|due|last date|closes?|application|apply|fee|challan|voucher|form)"
COURSE = r"(quiz|exam|test|submission|assignment|viva|presentation|lab)"
DEADLINE_ADMIN = re.compile(rf"(?i)\b{SOON}\b[^.?!\n]{{0,40}}\b{ADMIN}\b|\b{ADMIN}\b[^.?!\n]{{0,40}}\b{SOON}\b")
DEADLINE_COURSE = re.compile(rf"(?i)\b{SOON}\b[^.?!\n]{{0,40}}\b{COURSE}\b|\b{COURSE}\b[^.?!\n]{{0,40}}\b{SOON}\b")


def deadline_urgency(body: str) -> tuple[str, str] | None:
    """(priority floor, matched text). Administrative deadline within ~48h -> high (missing it costs
    money or an application); coursework deadline (quiz, exam) -> medium (blocked process)."""
    for level, pat in (("high", DEADLINE_ADMIN), ("medium", DEADLINE_COURSE)):
        for m in pat.finditer(body or ""):
            if re.search(r"(?i)\b(days?|weeks?|months?)\b|from today", m.group(0)):
                continue  # "10 days from today" is not imminent
            return level, m.group(0)
    return None


def check_overdue(body: str, received_at: str | None, refs: list[str], policy: Policy) -> list[OverdueFinding]:
    now = parse_received(received_at)
    windowed = [c for c in refs if c in policy.rules and policy.rules[c].windows]
    if not windowed:
        return []
    sentences = [s for s in SENTENCE_SPLIT.split(body) if s.strip()]
    findings: dict[str, OverdueFinding] = {}
    for i, sentence in enumerate(sentences):
        durations = _durations(sentence, now)
        if not durations:
            continue
        context = " ".join(sentences[max(0, i - 1): i + 1])
        matched = policy.best_matching(sentence, windowed, top=1) or policy.best_matching(context, windowed, top=1)
        if not matched and len(windowed) == 1:
            matched = windowed
        if not matched:
            continue
        code = matched[0]
        text, hours = max(durations, key=lambda d: d[1])
        amount, unit = policy.rules[code].windows[-1]
        if unit == "hours":
            overdue = hours > amount
        elif unit == "working days":
            if now:
                overdue = working_days_between(now - timedelta(hours=hours), now) > amount
            else:
                overdue = hours / 24 * 5 / 7 > amount
        else:
            overdue = hours > amount * 24
        prev = findings.get(code)
        if not prev or hours > prev.elapsed_hours:
            findings[code] = OverdueFinding(code, text, hours, f"{amount} {unit}", overdue)
    return list(findings.values())
