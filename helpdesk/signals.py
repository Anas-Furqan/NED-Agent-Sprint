"""Deterministic risk pre-checks (English + Roman Urdu).

Strong signals are enforced after the LLM (they can only ADD escalation). Weak signals
are passed to the LLM as hints only, so a single word out of context does not escalate.
Policy codes are located by searching the parsed policy text, never hardcoded.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from .policy import Policy


@dataclass
class SignalSpec:
    name: str
    label: str
    strong: list[str]
    weak: list[str]
    rule_keywords: list[str]  # used to locate the policy codes for this signal
    rule_section: str | None
    priority: str = "high"


@dataclass
class Signal:
    name: str
    label: str
    strong: bool
    evidence: list[str]
    codes: list[str] = field(default_factory=list)
    priority: str = "high"


SPECS: list[SignalSpec] = [
    SignalSpec(
        name="safety",
        label="safety hazard (electrical/fire/structural)",
        strong=[
            r"\bspark(s|ed|ing)?\b", r"electric(al)? shock", r"\bshock(ed)? (me|him|her|us|someone)",
            r"(exposed|naked|hanging|loose|bare|open|burnt) (wire|wiring|cable)s?",
            r"wire[^.]{0,40}(hanging|exposed|out of the wall)", r"short[- ]?circuit",
            r"\bfire\b(?! (drill|me|him|her|them|exit))", r"\bsmoke\b", r"burning smell",
            r"gas (leak|smell)", r"(ceiling|wall|roof|stair\w*|balcony)[^.]{0,30}(crack|collaps|falling|fell)",
            r"collaps", r"\baag\b", r"\bkarant\b", r"current (lag|laga|lagta)", r"\bdhu[a]?w?an\b",
            r"chhat[^.]{0,20}gir", r"taar[^.]{0,30}(nangi|latak|spark)",
        ],
        weak=[r"electric", r"wiring", r"\bsocket\b", r"dripping"],
        rule_keywords=["hazard"],
        rule_section=None,
    ),
    SignalSpec(
        name="harassment",
        label="harassment / bullying / threats",
        strong=[
            r"harass", r"\bstalk", r"(keeps?|kept|keep on) (following|messaging|texting|calling|sending|touching)",
            r"following me", r"threaten(ed|ing|s)? (me|to (hurt|kill|beat|harm))", r"\bbull(y|ied|ying)\b",
            r"blackmail", r"touch(ed|es)? me", r"(scared|afraid) (to come|of him|of her|of them|to go)",
            r"tang kar", r"p[ei]e?cha kar", r"dhamki", r"ghoorta|ghurta",
        ],
        weak=[r"\bscared\b", r"uncomfortable", r"\bthreat", r"\bdarr?\b"],
        rule_keywords=["harassment"],
        rule_section="escalation",
    ),
    SignalSpec(
        name="wellbeing",
        label="medical / wellbeing emergency",
        strong=[
            r"faint(ed|ing)", r"not responding", r"unconscious", r"(can'?t|cannot|not able to) breathe",
            r"\bbleeding\b", r"seizure", r"heart attack", r"suicid", r"kill myself", r"\bkill me\b",
            r"end (my life|it all|everything)", r"want to die", r"no (reason|point) (to|in) (live|living|go on)",
            r"(don'?t|do not) want to (live|be alive|exist)", r"better off without me", r"can'?t go on",
            r"hopeless", r"self[- ]harm", r"hurt myself", r"panic attack", r"overdose", r"behosh",
            r"jeena nahi", r"zinda nahi rehna", r"marna chaht[ai]", r"mar jana chaht[ai]", r"mar jaun",
            r"khudkushi", r"zindagi (se )?tang", r"jeene ka (koi )?(faida|maqsad) nahi", r"khud ko nuqsan",
        ],
        weak=[r"depress", r"anxi(ous|ety)", r"\bstress", r"mentally", r"can'?t cope", r"\bsick\b",
              r"killing me", r"\bkill me\b", r"i'?ll die", r"i will die", r"\bdying\b", r"can'?t take (this|it) any ?more",
              r"nobody cares", r"tired of everything"],
        rule_keywords=["wellbeing"],
        rule_section="escalation",
    ),
    SignalSpec(
        name="legal",
        label="legal / media threat",
        strong=[
            r"\blawyer", r"legal (action|notice)", r"\bsu(e|ing)\b",
            r"(take|taking) (this|it|the matter|this matter)( \w+)? to (the )?(court|media|press|news)",
            r"(go|going) to (the )?(court|media|press|news)", r"social media", r"\bmedia\b", r"\bwakeel",
            r"court (mein|me|case)", r"\bexpose\b",
        ],
        weak=[r"\bcourt\b", r"\bnews\b", r"\bpolice\b"],
        rule_keywords=["legal"],
        rule_section="escalation",
    ),
    SignalSpec(
        name="suspicious",
        label="suspicious message (prompt injection / phishing / data request)",
        strong=[
            r"ignore (all |any |the |your )?(previous |prior |above |earlier )?(instructions|rules|prompts?)",
            r"you are now", r"(admin|developer|god|dan) mode", r"jailbreak", r"system prompt",
            r"(send|give|share|forward|list|provide)[^.]{0,40}(list of|all|every|other)[^.]{0,30}"
            r"(students?|users?)'?s?[^.]{0,20}(emails?|address\w*|phone|numbers?|data|details|records|passwords?)",
            r"(email address\w*|phone numbers?|contact details|personal data|cnic)[^.]{0,30}(of|for) "
            r"(all|every|other) students?",
            r"mark (every|all)[^.]{0,40}(resolved|closed|approved)", r"(verify|confirm) your (account|password|credentials)",
            r"(enter|share|send) your (password|otp|pin)", r"reveal (the |your )?(api key|password|secret|prompt|instructions)",
            r"\bdisregard (all|the|previous|your)", r"override (your|the|all)",
            r"(marks|grades|result|phone number|address|contact|cnic|roll number) of (my |a )?"
            r"(friend|classmate|another student|other students)",
            r"\b(send|give|share|tell|provide|forward)( me)?\b[^.\n]{0,30}\b\w+'s (roll (no|number)|result|marks|"
            r"grades|phone|number|address|email|cnic|details|transcript|attendance)",
            r"\b\w+ (ka|ki) (roll (no|number)|result|marks|phone number|number|address) (bhej|bata|de)",
            r"(?m)^\s*\[?(system|assistant|developer)\]?\s*:", r"<\|?(system|im_start)",
            r"(manager|admin\w*|director|dean|registrar|head of \w+|supervisor|principal|chairperson)[^.\n]{0,30}"
            r"\b(said|told|asked|instructed|authori[sz]ed)\b[^.\n]{0,40}\bto (approve|waive|grant|refund|release|mark)",
            r"new instructions?:", r"act as (an? )?(admin|administrator|developer)",
        ],
        weak=[r"action required", r"click (here|this link)", r"\bpassword\b", r"\binstructions?\b"],
        rule_keywords=["phishing"],
        rule_section="escalation",
    ),
    SignalSpec(
        name="integrity",
        label="academic-integrity request (bribery / grade change / cheating)",
        strong=[
            r"\bbribe", r"rishwat", r"\b(pay|give) you\b[^.\n]{0,30}\b(to|for|if)\b",
            r"(change|increase|improve|raise|fix) my (grade|marks|result|attendance|gpa)",
            r"(leak|leaked|send|share|get)[^.\n]{0,20}(exam|question|final|mid) ?paper",
            r"answers? (for|to|of) (the |tomorrow'?s )?(exam|quiz|test|paper)",
            r"do my (assignment|exam|quiz|project)", r"mark me present", r"fake (medical|certificate|note)",
            r"\bpass me\b", r"under the table",
        ],
        weak=[r"\bcheat"],
        rule_keywords=[],
        rule_section=None,
        priority="medium",
    ),
    SignalSpec(
        name="outage",
        label="outage affecting many users",
        strong=[],  # handled by combined check below
        weak=[],
        rule_keywords=["outage"],
        rule_section=None,
        priority="medium",
    ),
]

NEGATION = re.compile(r"\b(no|not|never|without|nahi|nahin|koi)\W+(?:\w+\W+){0,2}$", re.I)
MANY = re.compile(
    r"(?i)\b(whole|entire|all|many|every|most|sab|saray|sare)( my| the| of the| other)? "
    r"(class|students|users|hostel|block|floor|floors|department|batch|people|rooms|log)\b"
)
OUTAGE = re.compile(
    r"(?i)(\bdown\b|not working|outage|no (internet|wi-?fi|connection)|cannot access|can'?t access|kaam nahi|band hai)"
)


def _find(patterns: list[str], text: str, check_negation: bool) -> list[str]:
    hits = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            if check_negation and NEGATION.search(text[max(0, m.start() - 40): m.start()]):
                continue
            hits.append(m.group(0))
            break
    return hits


URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
PHISH_WORDS = re.compile(r"(?i)password|log ?in|sign ?in|verify|credential|otp|\bpin\b|suspend|confirm your|account will")
BASE64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
FIGURATIVE = re.compile(r"(?i)\b(exams?|quiz\w*|assignments?|papers?|course|subject|homework|deadlines?|"
                        r"semester|class(es)?|teacher|lab|viva|project|result|marks|grades?|fail(ing)?)\b")
FIGURATIVE_ONLY = re.compile(r"(?i)kill me|killing me|die\b|dying")


def decoded_base64(text: str) -> str:
    """Decode base64-looking chunks so encoded instructions are checked too."""
    out = []
    for chunk in BASE64.findall(text):
        try:
            decoded = base64.b64decode(chunk + "=" * (-len(chunk) % 4)).decode("utf-8")
        except Exception:
            continue
        if decoded and sum(c.isprintable() for c in decoded) / len(decoded) > 0.9:
            out.append(decoded)
    return "\n".join(out)


def detect_signals(subject: str, body: str, policy: Policy, from_name: str = "") -> list[Signal]:
    text = f"{from_name}\n{subject}\n{body}"
    signals: list[Signal] = []
    hidden = decoded_base64(text)
    for spec in SPECS:
        codes = policy.find_rules(spec.rule_keywords, spec.rule_section)
        if spec.name == "outage":
            many, outage = MANY.search(text), OUTAGE.search(text)
            if many and outage:
                signals.append(Signal(spec.name, spec.label, True, [many.group(0), outage.group(0)], codes, spec.priority))
            continue
        strong = _find(spec.strong, text, check_negation=True)
        if spec.name == "wellbeing":
            # "this exam is killing me" is figurative: keep it as a hint, let the model judge
            strong = [h for h in strong if not (FIGURATIVE_ONLY.search(h) and FIGURATIVE.search(text))]
        if spec.name == "suspicious":
            if hidden and _find(spec.strong + [r"approve|waive|instruction"], hidden, check_negation=False):
                strong.append("base64-encoded instruction")
            if URL.search(text) and PHISH_WORDS.search(text):
                strong.append("link asking for login/password details")
        if strong:
            signals.append(Signal(spec.name, spec.label, True, strong[:3], codes, spec.priority))
            continue
        weak = _find(spec.weak, text, check_negation=False)
        if weak:
            signals.append(Signal(spec.name, spec.label, False, weak[:3], codes, spec.priority))
    return signals


def strip_suspicious(text: str) -> str:
    """Drop sentences that contain injection/phishing patterns, keep the genuine request (if any)."""
    spec = next(s for s in SPECS if s.name == "suspicious")
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    return " ".join(s for s in sentences if not _find(spec.strong, s, check_negation=False))


KINDS = {
    "auto_reply": re.compile(r"(?i)out of (the )?office|auto(matic|mated)?[- ]?reply|undeliverable|delivery "
                             r"(status notification|has failed|failure)|mailer-daemon|postmaster|i am currently away|"
                             r"will be back on|returning to the office"),
    "spam": re.compile(r"(?i)unsubscribe|newsletter|\d+% off|discount|limited[- ]time offer|buy now|you have won|"
                       r"promo code|special offer|sponsored|click here to (claim|win)"),
    "thanks": re.compile(r"(?i)^\W*(thanks|thank you|shukriya|jazakallah)|issue (is |has been )?(resolved|fixed|sorted)|"
                         r"problem (is |has been )?(solved|resolved|fixed)|no longer need"),
}


def message_kind(subject: str, body: str, from_email: str = "") -> str | None:
    """Messages that need no real action. Only a hint: never escalates, never hides risk signals."""
    text = f"{subject}\n{body}"
    if KINDS["auto_reply"].search(text) or re.search(r"(?i)mailer-daemon|postmaster|no-?reply@", from_email or ""):
        return "auto_reply"
    if KINDS["spam"].search(text):
        return "spam"
    if KINDS["thanks"].search(body.strip()) and len(body.split()) < 60 and "?" not in body:
        return "thanks"
    return None


CONFIDENTIAL = re.compile(
    r"(?i)(do not|don'?t|please don'?t) (tell|share|reveal|disclose)[^.]{0,30}(name|anyone|identity)"
    r"|confidential|keep (this|it|my name) (private|secret)|anonymous|kisi ko (na|mat) bata|naam (na|mat) bata"
)


def wants_confidentiality(body: str) -> bool:
    return bool(CONFIDENTIAL.search(body))
