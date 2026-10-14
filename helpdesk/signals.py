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
            r"\bآگ\b", r"کرنٹ", r"ننگی تار", r"تار ننگی", r"دھواں", r"شارٹ سرکٹ", r"چنگاری",
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
            r"tang kar", r"p[ei]e?cha kar", r"dhamki", r"ghoorta|ghurta", r"\bragg?ing\b",
            r"seniors?\b[^.\n]{0,40}\b(tang|pareshan|maar|zaleel|beizzat)", r"fresher(s|on)? ko (tang|maar|pareshan)",
            r"ہراساں", r"ہراسگی", r"پیچھا", r"دھمکی", r"تنگ کر",
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
            r"(hasn'?t|has not|not|nahi|nahin) (eaten|kha(ya|i|ta|ti))[^.\n]{0,20}\b(days|din)\b",
            r"\b(says|said|keeps saying|feels?|kehta|kehti) (that )?(nothing matters|kuch matter nahi)",
            r"wants? to disappear",
            r"khudkushi", r"zindagi (se )?tang", r"jeene ka (koi )?(faida|maqsad) nahi", r"khud ko nuqsan",
            r"مایوس", r"زندگی ختم", r"خودکشی", r"بیہوش", r"جینے کا کوئی فائدہ",
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
            r"court (mein|me|case)", r"\bexpose\b", r"وکیل", r"عدالت", r"میڈیا",
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
            r"(students?|users?)'?s?[^.]{0,20}(emails?|address\w*|phone|numbers?|data|details|records|passwords?|"
            r"defaults?|defaulters|fee status|results|marks|cnic)",
            r"(email address\w*|phone numbers?|contact details|personal data|cnic)[^.]{0,30}(of|for) "
            r"(all|every|other) students?",
            r"mark (every|all)[^.]{0,40}(resolved|closed|approved)", r"(verify|confirm) your (account|password|credentials)",
            r"(enter|share|send) your (password|otp|pin)", r"reveal (the |your )?(api key|password|secret|prompt|instructions)",
            r"\bdisregard (all|the|previous|your)", r"override (your|the|all)",
            r"(marks|grades|result|phone number|address|contact|cnic|roll number) of (my |a )?"
            r"(friend|classmate|another student|other students)",
            r"\b(send|give|share|tell|provide|forward)( me)?\b[^.\n]{0,30}"
            r"\b(?!(?:son|daughter|child|ward|brother|sister|kid|nephew|niece)'s)\w+'s (roll (no|number)|result|marks|"
            r"grades|phone|number|address|email|cnic|details|transcript|attendance)",
            r"\b\w+ (ka|ki) (roll (no|number)|result|marks|phone number|number|address) (bhej|bata|de)",
            r"(?m)^\s*\[?(system|assistant|developer)\]?\s*:", r"<\|?(system|im_start)",
            r"(manager|admin\w*|director|dean|registrar|head of \w+|supervisor|principal|chairperson)[^.\n]{0,30}"
            r"\b(said|told|asked|instructed|authori[sz]ed)\b[^.\n]{0,40}\bto (approve|waive|grant|refund|release|mark)",
            r"new instructions?:", r"act as (an? )?(admin|administrator|developer)",
            r"(share|send|tell|provide|reply with|give)[^.\n]{0,25}\b(otp|pin|one[- ]time (password|code)|"
            r"verification code|cvv)\b",
            r"\bI am (the )?(registrar|vc|vice chancellor|dean|director|controller|chairperson|principal|"
            r"administrator)\b[^\n]{0,80}\b(send|share|forward|provide|give)\b",
        ],
        weak=[r"action required", r"click (here|this link)", r"\bpassword\b", r"\binstructions?\b",
              r"\b(son|daughter|child|ward)'s (result|marks|attendance|grades|transcript)",
              r"\bI am (the )?(registrar|vc|dean|director|controller|chairperson|principal)\b"],
        rule_keywords=["phishing"],
        rule_section="escalation",
    ),
    SignalSpec(
        name="incident",
        label="safety/security incident (theft or violence) not covered by a specific policy rule",
        strong=[
            r"\b(stolen|stole|theft|robbed|robbery|burglar\w*|break-?in|broke into|chori|chura (liya|li))\b",
            r"\b(fight|fighting|brawl|assault(ed)?|attacked|punched|beat (me|him|her|us|up)|beaten|hit me|"
            r"maar peet|larai|jhagra|mara peeta)\b",
        ],
        weak=[r"\bmissing\b", r"\bweapon\b"],
        rule_keywords=[],
        rule_section=None,
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


JOKE = re.compile(r"(?i)\b(lol|lmao|haha+|jk|literally|metaphorically)\b|\U0001F602|\U0001F923|\U0001F525")
REAL_FIRE = re.compile(r"(?i)smoke|spark|caught fire|burn(ing|t)|short ?circuit|flames|extinguish")
URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
PHISH_WORDS = re.compile(r"(?i)password|log ?in|sign ?in|verify|credential|otp|\bpin\b|suspend|confirm your|account will")
PAY_WORDS = re.compile(r"(?i)\bpay\b|payment|customs|charges|\bfee\b|card details|bank details|rs\.? ?\d")
URGENCY = re.compile(r"(?i)on hold|suspend|blocked|within \d+ (hours|days)|immediately|last chance|failure to|"
                     r"will be (returned|cancelled|closed)|expires?")
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
        except Exception:  # noqa: S112  (not base64: skip)
            continue
        if decoded and sum(c.isprintable() for c in decoded) / len(decoded) > 0.9:
            out.append(decoded)
    return "\n".join(out)


PAST_HAZARD = re.compile(r"(?i)\b(was|were|had|there was|yesterday|last night|earlier|this morning|kal raat|kal)\b"
                         r"[^.\n]{0,60}\b(fire|spark\w*|smoke|short ?circuit|aag|blaze)")
HAZARD_OVER = re.compile(r"(?i)\b(is|it'?s|has been|was|got|now)\s+(now\s+)?(out|put out|extinguished|over|under control|"
                         r"stopped|fixed)\b|bujh (gayi|gaya|chuki)|khatam ho (gayi|gaya)|\u0628\u062c\u06be \u06af\u0626\u06cc")
RESIDUAL = re.compile(r"(?i)\b(smell\w*|smok\w*|spark\w*|black|burnt|burned|warm|hot|crack\w*|exposed|melt\w*|"
                      r"bu aa|badboo|dhuan)\b")


def post_incident(text: str) -> tuple[bool, bool]:
    """(hazard reported as already over, residual signs like smell/burn marks still present)."""
    over = bool(PAST_HAZARD.search(text) and HAZARD_OVER.search(text))
    return over, over and bool(RESIDUAL.search(HAZARD_OVER.split(text, maxsplit=1)[-1] if over else ""))


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
        if spec.name == "safety" and strong and JOKE.search(text) and not REAL_FIRE.search(text):
            strong = []  # "my laptop is literally on fire with this assignment lol": figurative
        if spec.name == "wellbeing":
            # "this exam is killing me" is figurative: keep it as a hint, let the model judge
            strong = [h for h in strong if not (FIGURATIVE_ONLY.search(h) and FIGURATIVE.search(text))]
        if spec.name == "suspicious":
            if hidden and _find(spec.strong + [r"approve|waive|instruction"], hidden, check_negation=False):
                strong.append("base64-encoded instruction")
            if URL.search(text) and (PHISH_WORDS.search(text) or (PAY_WORDS.search(text) and URGENCY.search(text))):
                strong.append("link asking for login/password details")
        if strong and spec.name == "safety":
            over, residual = post_incident(text)
            if over:  # still escalated (inspection needed), but described truthfully as past
                label = "post-incident safety inspection (hazard reported as already out" + \
                        ("; residual damage reported)" if residual else ")")
                signals.append(Signal("safety_post", label, True, strong[:3], codes, "high" if residual else "medium"))
                continue
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


FIRST_PERSON = re.compile(r"(?i)\b(i|my|me|mine|mera|meri|mere|mujhe|main|hum|hamara)\b")
AUTOMATED_SENDER = re.compile(r"(?i)(no-?reply|do-?not-?reply|notifications?|alerts?|mailer|newsletter|marketing|"
                              r"info|updates?|postmaster|bounce)[^@]*@")
KINDS = {
    "delivery": re.compile(r"(?i)\b(shipment|parcel|consignment|cn ?#|cn no|tracking (no|number|id)|out for delivery|"
                           r"has been (delivered|dispatched|shipped)|delivery (attempt|scheduled|status))\b"),
    "bank_alert": re.compile(r"(?i)\b(jazzcash|easypaisa|hbl|ubl|mcb|meezan|alfalah|allied bank|raast)\b[^.\n]{0,80}"
                             r"\b(debited|credited|received|sent|transferred|transaction|otp)\b|\btransaction (id|alert)|"
                             r"available balance|\byour otp\b|one[- ]time (password|pin)"),
    "recruiter": re.compile(r"(?i)\b(job (offer|opening|opportunit\w+)|internship (opportunit\w+|offer|program)|we are hiring|"
                            r"hiring for|your (profile|cv|resume)|position of|apply now)\b"),
    "circular": re.compile(r"(?i)\b(notification no|office order|circular|notice)\b[^\n]{0,200}\b(hereby|all students|"
                           r"all concerned|will remain closed|holiday|is notified)\b"),
    "auto_reply": re.compile(r"(?i)out of (the )?office|auto(matic|mated)?[- ]?reply|undeliverable|delivery "
                             r"(status notification|has failed|failure)|mailer-daemon|postmaster|i am currently away|"
                             r"will be back on|returning to the office"),
    "marketing": re.compile(r"(?i)unsubscribe|newsletter|\d+% off|discount|limited[- ]time offer|buy now|you have won|"
                            r"promo code|special offer|sponsored|click here to (claim|win)|\bsale\b.{0,30}\b(now|live|today)|"
                            r"admissions? open|enrol+ now|coaching (academy|centre|center)"),
    "thanks": re.compile(r"(?i)^\W*(thanks|thank you|shukriya|jazakallah)|issue (is |has been )?(resolved|fixed|sorted)|"
                         r"problem (is |has been )?(solved|resolved|fixed)|no longer need"),
}


# kinds that are not Student Helpdesk requests at all, and the ones that never get a reply
NOT_HELPDESK_KINDS = {"auto_reply": "auto-reply / out-of-office / bounce", "marketing": "marketing or newsletter",
                      "delivery": "courier / delivery notification", "bank_alert": "bank or wallet transaction alert",
                      "recruiter": "job / internship offer", "circular": "official circular or notice (informational)"}
NO_REPLY_KINDS = {"auto_reply", "marketing", "delivery", "bank_alert", "circular"}


def is_automated(from_email: str, text: str = "") -> bool:
    return bool(AUTOMATED_SENDER.search(from_email or "")) or bool(
        re.search(r"(?i)this is an? (automated|auto-generated|system generated) (message|email|notification)|"
                  r"do not reply to this (email|message)", text))


def message_kind(subject: str, body: str, from_email: str = "") -> str | None:
    """What sort of message this is when it is not a normal request. Only a hint plus no-reply handling:
    it never escalates and never hides risk signals. Context matters: a student writing in the first person
    about a parcel or a JazzCash payment is asking for help, not sending a notification."""
    text = f"{subject}\n{body}"
    automated = is_automated(from_email, text)
    asks = bool(FIRST_PERSON.search(body)) and not automated
    if KINDS["auto_reply"].search(text) or re.search(r"(?i)mailer-daemon|postmaster", from_email or ""):
        return "auto_reply"
    for kind in ("delivery", "bank_alert"):
        if KINDS[kind].search(text) and (automated or not asks):
            return kind
    if KINDS["marketing"].search(text) and not asks:
        return "marketing"
    if KINDS["recruiter"].search(text) and (automated or re.search(r"(?i)\b(your|you)\b", body)) and not re.search(
            r"(?i)\bI (got|have|received|need)\b", body):
        return "recruiter"
    if KINDS["circular"].search(text) and not asks:
        return "circular"
    if KINDS["thanks"].search(body.strip()) and len(body.split()) < 60 and "?" not in body:
        return "thanks"
    return None


CONFIDENTIAL = re.compile(
    r"(?i)(do not|don'?t|please don'?t) (tell|share|reveal|disclose)[^.]{0,30}(name|anyone|identity)"
    r"|confidential|keep (this|it|my name) (private|secret)|anonymous|kisi ko (na|mat) bata|naam (na|mat) bata"
    r"|کسی کو نہ بتا|نام نہ بتا|راز میں رکھ|خفیہ رکھ"
)


def wants_confidentiality(body: str) -> bool:
    return bool(CONFIDENTIAL.search(body))
