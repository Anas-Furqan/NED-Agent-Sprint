"""Code-side checks on every reply draft, plus cheap deterministic repairs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SIGN_OFF = "Student Helpdesk"
MAX_WORDS = 150

ROMAN_URDU = set(
    """hai hain nahi nahin kya kyun kaise meri mera mere mujhe hum aap ka ki ke ko se mein
    kal aaj jaldi bata batayen bata dein karna karein raha rahi rahe ho hota hoti warna lag jayegi
    masla kar dein ab abhi bohat bahut theek shukriya kab koi kuch sab yeh woh tha thi gaya
    gayi wala wali chahiye sakta sakti hoga hogi diya liya jab tak""".split()
)
GREETING_WORDS = {"assalam", "o", "alaikum", "salam", "walaikum", "aoa"}
NON_PERSON = re.compile(r"(?i)\b(admin|notice|portal|system|support|team|noreply|no-reply|test|update|service|info)\b")

PROMISE_PATTERNS = [
    r"(?i)\b(we|i|helpdesk) (have|has|will) (waive|waived|approve|approved|refund|refunded|reverse|reversed)",
    r"(?i)\b(your|the) (late )?fee (has been|is|will be) (waived|reversed|refunded|approved)",
    r"(?i)(?<!not )(?<!cannot )(?<!no )\bguarantee(d|s)?\b",
    r"(?i)\bwill (definitely|surely|certainly) be\b",
    r"(?i)\b(he|she|they) will be (punished|expelled|suspended|removed|fired)",
    r"(?i)\bfull refund (is|will be) (approved|processed)",
    r"(?i)\byou will (get|receive) (a )?(full )?refund",
    r"(?i)\b(they|it|we|the [\w' ]{2,30}) will (fix|resolve|restore|repair|reverse|waive|approve|punish)\b",
]
CONFRONT = re.compile(r"(?i)\b(confront|talk to (him|her|them)|tell (him|her|them) to stop|ask (him|her|them) to stop|speak (to|with) (him|her|them))\b")
SECRET = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9]{20,}|Bearer\s+\S{10,})")
EMAIL_ADDR = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
CLINIC = re.compile(r"(?i)(clinic|counsel?ling)")
CONFIDENTIAL_ACK = re.compile(r"(?i)(confiden|privacy|private|discre|only with|not share your|raaz|khufia)")


def detect_language(text: str) -> str:
    tokens = [t for t in re.findall(r"[a-z']+", (text or "").lower()) if t not in GREETING_WORDS]
    if not tokens:
        return "english"
    hits = sum(1 for t in tokens if t in ROMAN_URDU)
    distinct = len({t for t in tokens if t in ROMAN_URDU})
    return "roman_urdu" if distinct >= 3 and hits / len(tokens) >= 0.15 else "english"


def greeting_name(from_name: str, body: str = "") -> str:
    name = (from_name or "").strip()
    if (not name or NON_PERSON.search(name) or re.search(r"\d|@", name) or len(name) < 3
            or not re.search(r"[aeiouAEIOU]", name)):
        return "Student"
    return name


@dataclass
class ValidationResult:
    problems: list[str] = field(default_factory=list)  # need regeneration
    repairs: list[str] = field(default_factory=list)  # fixed in code

    @property
    def ok(self) -> bool:
        return not self.problems


def word_count(text: str) -> int:
    return len(text.split())


def repair_and_check(reply: str, *, name: str, language: str, refs: list[str], sender_email: str,
                     signals: set[str], confidential: bool) -> tuple[str, ValidationResult]:
    res = ValidationResult()
    reply = (reply or "").strip()
    if not reply:
        res.problems.append("reply is empty")
        return reply, res

    first = name.split()[0]
    if first.lower() not in reply.lower()[:120]:
        reply = f"Dear {name},\n\n{reply}"
        res.repairs.append("added greeting with student name")
    # layout: greeting on its own line, body, then "Regards," + "Student Helpdesk" on their own lines
    reply = re.sub(r"^(Dear [^,\n]{1,60},)[ \t]+", r"\1\n\n", reply)
    closing_words = r"regards|best regards|warm regards|kind regards|sincerely|thanks|shukriya"
    tail = re.search(rf"(?i)[\s,]*(?:({closing_words})[\s,.]*)?student helpdesk[\s.]*$", reply)
    if tail:
        body, closing = reply[:tail.start()].rstrip(), (tail.group(1) or "Regards").capitalize()
    else:
        body = re.sub(rf"(?i)[\s,]*({closing_words})[\s,.!]*$", "", reply).rstrip()
        closing = "Regards"
        res.repairs.append("added 'Student Helpdesk' sign-off")

    if refs and not any(c in body for c in refs):
        body = f"{body}\n\nPolicy reference: {', '.join(refs)}."
        res.repairs.append("added policy code reference")
    reply = f"{body}\n\n{closing},\n{SIGN_OFF}"

    words = word_count(reply)
    if words >= MAX_WORDS:
        res.problems.append(f"reply has {words} words; must be under {MAX_WORDS}")
    if detect_language(reply) != language and language == "roman_urdu":
        res.problems.append("email is in Roman Urdu; reply must be in Roman Urdu")
    if language == "english" and detect_language(reply) == "roman_urdu":
        res.problems.append("email is in English; reply must be in English")
    for pat in PROMISE_PATTERNS:
        m = re.search(pat, reply)
        if m:
            res.problems.append(f"possible promise not allowed by policy: '{m.group(0)}'")
            break
    if "harassment" in signals:
        for m in CONFRONT.finditer(reply):
            before = reply[max(0, m.start() - 40): m.start()].lower()
            if not re.search(r"\b(not|never|don'?t|no need to|without having to|won'?t have to)\b", before):
                res.problems.append(f"harassment reply must not suggest confronting the person ('{m.group(0)}')")
                break
    if "harassment" in signals and confidential and not CONFIDENTIAL_ACK.search(reply):
        res.problems.append("student asked for confidentiality; reply must acknowledge it")
    if "wellbeing" in signals and not CLINIC.search(reply):
        res.problems.append("wellbeing reply must point to the campus clinic / Student Counseling Cell")
    if SECRET.search(reply):
        res.problems.append("reply contains something that looks like a secret")
    leaked = [e for e in EMAIL_ADDR.findall(reply) if e.lower() != (sender_email or "").lower()]
    if leaked:
        res.problems.append("reply contains an email address (possible data leak)")
    return reply, res
