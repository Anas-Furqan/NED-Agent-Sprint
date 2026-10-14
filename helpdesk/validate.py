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
NON_PERSON = re.compile(r"(?i)\b(admin|notice|portal|system|support|team|noreply|no-reply|test|update|service|info|"
                        r"assistant|ignore|instructions?|prompt|security|deals?|promo|offers?)\b")

PROMISE_PATTERNS = [
    r"(?i)\b(we|i|helpdesk) (have|has|will) (waive|waived|approve|approved|refund|refunded|reverse|reversed)",
    r"(?i)\b(your|the) (late )?fee (has been|is|will be) (waived|reversed|refunded|approved)",
    r"(?i)(?<!not )(?<!cannot )(?<!no )\bg[ua]+rantee(d|s)?\b(?! (nahi|nahin|not))",
    r"(?i)\bwill (definitely|surely|certainly) be\b",
    r"(?i)\b(he|she|they) will be (punished|expelled|suspended|removed|fired)",
    r"(?i)\bfull refund (is|will be) (approved|processed)",
    r"(?i)\byou will (get|receive) (a )?(full )?refund",
    r"(?i)\b(they|it|we|the [\w' ]{2,30}) will (fix|resolve|restore|repair|reverse|waive|approve|punish)\b",
    r"(?i)\b(they|he|she|someone|a technician|staff|the [\w' ]{2,30}) will (act|come|arrive|take (immediate )?action|"
    r"send someone|be there)\b",
    r"(?i)\bwill be (fixed|resolved|repaired|done|sorted|processed|approved) (immediately|today|right away|"
    r"within \d+ \w+( \w+)?|by \w+)",
    r"(?i)\b(i|we) (promise|assure you)\b",
    # Roman Urdu
    r"(?i)\b(foran|fori|fauran|turant)\b[^.\n]{0,60}\b(karenge|karein ge|kar denge|denge|dein ge|jayega|jaye ga|"
    r"jayegi|jaye gi|aa jayega|aayega|aaye ga)\b",
    r"(?i)\bzaroor\b[^.\n]{0,30}\b(ho|mil|kar|aa) ?(jayega|jaye ga|jayegi|jaye gi|denge|dein ge)\b",
    r"(?i)\bpakka\b",
    r"(?i)\baaj hi\b[^.\n]{0,30}\b(jayega|jaye ga|jayegi|denge|karenge)\b",
    r"(?i)\bhum\b[^.\n]{0,40}\b(theek kar denge|hal kar denge|waive kar denge|refund kar denge|maaf kar denge|kar denge)\b",
    r"(?i)\b(waive|maaf|refund|wapis)\b[^.\n]{0,15}\b(kar denge|ho jayega|ho jaye ga|mil jayega|mil jaye ga|"
    r"kar di jaye gi|kar diya jayega)\b",
    r"(?i)\b(iqdaam|iqdaamat|karwai)\b[^.\n]{0,20}\b(karenge|karein ge|karega|kare ga|karegi)\b",
    # Urdu script
    r"\u0641\u0648\u0631\u0627[\u064b]?[^\u06d4\n]{0,40}(\u06a9\u0631 \u062f\u06cc\u06ba \u06af\u06d2|"
    r"\u06c1\u0648 \u062c\u0627\u0626\u06d2 \u06af\u0627|\u06a9\u0631\u06cc\u06ba \u06af\u06d2)",
    r"\u0636\u0631\u0648\u0631[^\u06d4\n]{0,30}(\u06c1\u0648 \u062c\u0627\u0626\u06d2 \u06af\u0627|"
    r"\u0645\u0644 \u062c\u0627\u0626\u06d2 \u06af\u0627|\u06a9\u0631 \u062f\u06cc\u06ba \u06af\u06d2)",
    r"\u067e\u06a9\u0627", r"\u06af\u0627\u0631\u0646\u0679\u06cc",
]


def promise_spans(text: str, allowed_numbers: set[int] | frozenset[int] = frozenset()) -> list[str]:
    """Phrases promising more than the policy allows. A phrase that only quotes a policy timeline
    (its number is one of the policy's response windows, e.g. 48 hours) is allowed."""
    found = []
    for pat in PROMISE_PATTERNS:
        for m in re.finditer(pat, text or ""):
            numbers = {int(n) for n in re.findall(r"\d+", m.group(0))}
            if numbers and numbers <= set(allowed_numbers):
                continue
            found.append(m.group(0))
    return found
CONFRONT = re.compile(r"(?i)\b(confront|talk to (him|her|them)|tell (him|her|them) to stop|ask (him|her|them) to stop|speak (to|with) (him|her|them))\b")
SECRET = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9]{20,}|Bearer\s+\S{10,})")
EMAIL_ADDR = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
CLINIC = re.compile(r"(?i)(clinic|counsel?ling)")
CONFIDENTIAL_ACK = re.compile(r"(?i)(confiden|privacy|private|discre|only with|not share your|raaz|khufia|"
                             r"رازداری|راز|خفیہ|صرف متعلقہ)")


URDU_SCRIPT = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")


URDU_ONLY = re.compile(r"[\u0679\u0688\u0691\u06ba\u06be\u06c1\u06d2\u06d3\u06cc\u06a9\u06af]")  # ٹ ڈ ڑ ں ھ ہ ے ۓ ی ک گ
SINDHI_ONLY = re.compile(r"[\u0684\u0683\u0687\u068a\u068c\u068f\u0699\u06aa\u06bb\u067b\u067d\u067f\u067a\u0680]")
ARABIC_ONLY = re.compile(r"[\u064a\u0643\u0629\u0649]")  # ي ك ة ى
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def unsupported_language_name(text: str) -> str:
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return "unknown language"
    if sum(1 for c in letters if CJK.match(c)) / len(letters) > 0.3:
        return "Chinese/Japanese/Korean"
    if sum(1 for c in letters if URDU_SCRIPT.match(c)) / len(letters) > 0.3:
        if len(SINDHI_ONLY.findall(text)) >= 2:
            return "Sindhi"
        if len(ARABIC_ONLY.findall(text)) > len(URDU_ONLY.findall(text)):
            return "Arabic"
        return ""
    if sum(1 for c in letters if c.isascii()) / len(letters) < 0.5:
        return "a non-Latin script"
    return ""


def detect_language(text: str) -> str:
    """english | roman_urdu | urdu (Nastaliq script) | unsupported (Arabic, Sindhi, Chinese, ...)."""
    if unsupported_language_name(text):
        return "unsupported"
    letters = [c for c in (text or "") if c.isalpha()]
    if letters and sum(1 for c in letters if URDU_SCRIPT.match(c)) / len(letters) > 0.3:
        return "urdu"
    tokens = [t for t in re.findall(r"[a-z']+", (text or "").lower()) if t not in GREETING_WORDS]
    if not tokens:
        return "english"
    hits = sum(1 for t in tokens if t in ROMAN_URDU)
    distinct = len({t for t in tokens if t in ROMAN_URDU})
    return "roman_urdu" if distinct >= 3 and hits / len(tokens) >= 0.15 else "english"


def greeting_name(from_name: str, body: str = "") -> str:
    name = (from_name or "").strip()
    if (not name or NON_PERSON.search(name) or re.search(r"\d|@|:|<|>", name) or len(name) < 3 or len(name) > 40
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
                     signals: set[str], confidential: bool,
                     allowed_numbers: frozenset[int] = frozenset()) -> tuple[str, ValidationResult]:
    res = ValidationResult()
    reply = (reply or "").strip()
    if not reply:
        res.problems.append("reply is empty")
        return reply, res

    first = name.split()[0]
    if not reply.lstrip().lower().startswith("dear "):
        reply = f"Dear {name},\n\n{reply}"
        res.repairs.append("added 'Dear <name>,' greeting")
    elif first.lower() not in reply.lower()[:120]:
        reply = re.sub(r"(?i)^\s*dear [^,\n]*,?", f"Dear {name},", reply, count=1)
        res.repairs.append("fixed greeting to use the student's name")
    reply = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", reply.replace("\r\n", "\n"))
    # layout: greeting on its own line, body, then "Regards," + "Student Helpdesk" on their own lines
    reply = re.sub(r"^(Dear [^,\n]{1,60},)\s*", r"\1\n\n", reply)
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
    reply_lang = detect_language(reply)
    if language == "roman_urdu" and reply_lang == "english":
        res.problems.append("email is in Roman Urdu; reply must be in Roman Urdu")
    if language == "urdu" and reply_lang == "english":
        res.problems.append("email is in Urdu; reply must be in Urdu script (or Roman Urdu)")
    if language in ("english", "unsupported") and reply_lang != "english":
        res.problems.append("reply must be in English")
    if URL.search(reply):
        res.problems.append("reply must not contain links (never repeat links from an email)")
    promises = promise_spans(reply, allowed_numbers)
    if promises:
        res.problems.append(f"possible promise not allowed by policy: '{promises[0]}'")
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
