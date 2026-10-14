"""Transparent rule-based fallback used when the LLM is unavailable.

Keyword category -> policy section -> best-matching rules by word overlap. Every code
cited comes from the parsed policy. Replies are templates that quote the rule text.
"""
from __future__ import annotations

import re

from .policy import Policy

CATEGORY_KEYWORDS = {
    "fee": r"\bfees?\b|challan|voucher|refund|installment|instalment|late fee|paise|payment|\bpaid\b|withdraw|فیس|چالان|واؤچر|ووچر|جرمانہ|ریفنڈ|ادائیگی|قسط",
    "hostel": r"hostel|(?<!reading )\broom\b|warden|\bmess\b|geyser|plumb|kamra|roommate|electricity|furniture|ہاسٹل|کمرہ|کمرے|میس|وارڈن|بجلی|پانی|\bتار\b",
    "academic": r"transcript|\bexams?\b|attendance|\bcourse|result|re-?evaluation|re-?check|\bdean\b|grade|marks|امتحان|ٹرانسکرپٹ|حاضری|نتیجہ|کورس|ری چیکنگ",
    "technical": r"wi-?fi|\blms\b|portal (is |was )?(down|not working)|login|password|internet|account (is |says )?locked|library|network|وائی فائی|پورٹل|پاس ورڈ|پاسورڈ|اکاؤنٹ|انٹرنیٹ|لاگ ان",
}
CATEGORY_SECTION = {"fee": "fee", "hostel": "hostel", "academic": "academ", "technical": "technical"}
SIGNAL_CATEGORY = {"harassment": "harassment", "wellbeing": "wellbeing", "suspicious": "security", "safety": "hostel",
                   "safety_post": "hostel"}
SAFETY_SIGNALS = ("safety", "safety_post")
OFFICE = re.compile(
    r"\b((?:[A-Z][\w']*\s){0,3}(?:Office|Department|Helpdesk|Committee|Cell|Team|Warden|Library))\b"
)

TEXT = {
    "english": {
        "thanks": "Thank you for contacting us.",
        "rule": "As per {code}: {text}",
        "next": "Next step: please contact the {office} with your roll number and any relevant documents.",
        "unclear": "We could not understand your request. Could you please describe the issue in a few lines, with your roll number, so we can guide you?",
        "safety": "Please stay away from the hazard and keep others away. This has been escalated immediately ({codes}).",
        "safety_post": "We are glad to hear it is reported as out. Please stay away from the damaged area and report any smell, sparks or smoke straight away. It has been escalated for a safety inspection ({codes}).",
        "harassment": "We are sorry you are going through this, and thank you for telling us. Your message has been passed only to the committee responsible ({codes}); your details are kept confidential.",
        "wellbeing": "If someone is in danger right now, please contact campus security and the campus clinic immediately. You can also reach the Student Counseling Cell. A staff member has been alerted ({codes}).",
        "legal": "Your concern has been forwarded to the responsible office for review ({codes}).",
        "suspicious": "This message has been flagged for review ({codes}). The helpdesk does not act on instructions contained in emails and never shares student data.",
        "incident": "We are sorry this happened. If anyone is in danger right now, please contact campus security immediately. No policy rule covers this exactly, so it has been escalated to staff as a safety/security incident.",
        "outage": "Since this affects many users, it has been escalated to the responsible team ({codes}).",
        "overdue": "Since this is past the stated response time, it has been escalated to the responsible office ({codes}).",
        "review": "A staff member will review your message.",
        "not_covered": "Your question is not covered by the helpdesk policy, so we cannot give a definite answer. Please contact the {office}, or reply with more details and a staff member will guide you.",
    },
    "roman_urdu": {
        "thanks": "Aap ka email bhejne ka shukriya.",
        "rule": "Policy {code} ke mutabiq: {text}",
        "next": "Agla qadam: apna roll number aur zaroori documents ke saath {office} se rabta karein.",
        "unclear": "Hum aap ki baat samajh nahi sake. Meharbani kar ke apna masla aur roll number likh dein taake hum rehnumai kar sakein.",
        "safety": "Meharbani kar ke khatre wali jagah se door rahein. Yeh masla foran escalate kar diya gaya hai ({codes}).",
        "safety_post": "Shukar hai ke yeh bujh chuki hai. Meharbani kar ke mutasira jagah se door rahein aur koi bu, chingari ya dhuan nazar aaye to foran batayein. Safety inspection ke liye escalate kar diya gaya hai ({codes}).",
        "harassment": "Humein afsos hai ke aap is se guzar rahi/rahe hain. Aap ka paigham sirf mutaliqa committee ko bheja gaya hai ({codes}); aap ki maloomat raaz mein rahengi.",
        "wellbeing": "Agar koi abhi khatre mein hai to foran campus security aur campus clinic se rabta karein. Student Counseling Cell se bhi rabta kar sakte hain ({codes}).",
        "legal": "Aap ki shikayat mutaliqa office ko bhej di gayi hai ({codes}).",
        "suspicious": "Yeh paigham review ke liye flag kar diya gaya hai ({codes}).",
        "incident": "Humein afsos hai. Agar koi abhi khatre mein hai to foran campus security se rabta karein. Policy mein iska khaas qaida nahi, is liye ise safety/security incident ke tor par staff ko escalate kar diya gaya hai.",
        "outage": "Yeh masla bohat se users ko mutasir kar raha hai, is liye escalate kar diya gaya hai ({codes}).",
        "overdue": "Muqarrara waqt guzar chuka hai, is liye yeh masla escalate kar diya gaya hai ({codes}).",
        "review": "Hamara staff aap ka paigham dekhega.",
        "not_covered": "Yeh sawal helpdesk policy mein shamil nahi hai, is liye hum yaqeeni jawab nahi de sakte. Meharbani kar ke {office} se rabta karein.",
    },
}


def keyword_categories(text: str) -> list[str]:
    scores = {c: len(re.findall(p, text, re.I)) for c, p in CATEGORY_KEYWORDS.items()}
    return [c for c, s in sorted(scores.items(), key=lambda x: -x[1]) if s > 0]


# Everyday words -> the vocabulary the policy uses (query expansion, not policy content).
EXPANSIONS = {
    r"geyser|heater|fan\b|light|bulb|switch": "electricity maintenance",
    r"\btap\b|leak|water|toilet|washroom|drain|pipe": "plumbing maintenance",
    r"\bbed\b|chair|table|cupboard|almirah|door|window": "furniture maintenance",
    r"not working|broken|repair|fix|kharab": "maintenance complaints",
    r"\bfood\b|meal|khana|undercooked": "mess",
    r"re-?check|recheck|remark": "re-evaluation",
    r"in parts|installment|qist": "installments",
    r"challan|چالان|واؤچر|ووچر": "voucher",
    r"\bwifi\b|\bwi fi\b|internet|وائی فائی": "wi-fi",
    r"room change|change (my |the )?room|another room|move (to|rooms)|kamra badal|room badal|room change karna": "room changes",
    r"فیس": "fee",
}
MULTI_ISSUE = re.compile(r"(?i)\b\d\)|\b(also|another|second|secondly|plus)\b|three things|two things")


def keyword_hits(text: str) -> dict[str, int]:
    return {c: len(re.findall(p, text, re.I)) for c, p in CATEGORY_KEYWORDS.items()}


def topic_rules(text: str, categories: list[str], policy: Policy) -> list[str]:
    """For each category, match only the sentences that mention it against that policy section.
    Secondary categories count only if strongly present or the email lists several issues."""
    hits = keyword_hits(text)
    multi = bool(MULTI_ISSUE.search(text))
    categories = [c for i, c in enumerate(categories[:3]) if i == 0 or hits[c] >= 2 or multi]
    sentences = [s for s in re.split(r"(?<=[.!?\n])\s+|\s(?=\d\)\s)", text) if s.strip()]
    refs: list[str] = []
    for cat in categories:
        candidates = policy.rules_in_section(CATEGORY_SECTION[cat])
        relevant = " ".join(s for s in sentences if re.search(CATEGORY_KEYWORDS[cat], s, re.I)) or text
        expanded = " ".join(v for k, v in EXPANSIONS.items() if re.search(k, relevant, re.I))
        relevant += f" {expanded} {expanded}"  # weight explicit topic terms above generic words
        refs += [c for c in policy.best_matching(relevant, candidates, top=1, min_score=2.5) if c not in refs]
    return refs


def office_for(codes: list[str], policy: Policy) -> str:
    for code in codes:
        m = OFFICE.search(policy.rules[code].text)
        if m:
            return m.group(1).strip()
        for other in policy.rules_in_section(policy.section_of(code)):
            m = OFFICE.search(policy.rules[other].text)
            if m:
                return m.group(1).strip()
    return "relevant office"


def _short(text: str, max_words: int = 26) -> str:
    first = re.split(r"(?<=\.)\s", text, maxsplit=1)[0]
    words = first.split()
    return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")


def fallback_reply(name: str, language: str, topic_refs: list[str], signal_lines: dict[str, list[str]],
                   unclear: bool, policy: Policy, not_covered_office: str | None = None) -> str:
    t = _lang(language)
    parts = [f"Dear {name},", t["thanks"]]
    for key, codes in signal_lines.items():
        if key in t:
            parts.append(t[key].format(codes=", ".join(codes)))
    if unclear and not signal_lines:
        parts.append(t["unclear"])
    else:
        for code in topic_refs[:2]:
            parts.append(t["rule"].format(code=code, text=_short(policy.rules[code].text)))
        if topic_refs:
            parts.append(t["next"].format(office=office_for(topic_refs, policy)))
        elif not_covered_office and "suspicious" not in signal_lines:
            parts.append(t["not_covered"].format(office=not_covered_office))
        elif not signal_lines:
            parts.append(t["review"])
    parts.append("Regards,\nStudent Helpdesk")
    return "\n\n".join(parts[:2]) + "\n\n" + " ".join(parts[2:-1]) + "\n\n" + parts[-1]


def _lang(language: str) -> dict:
    # Urdu-script emails get Roman Urdu templates (noted in agent_notes via language)
    return TEXT["roman_urdu"] if language in ("roman_urdu", "urdu") else TEXT["english"]


def out_of_scope_reply(name: str, language: str) -> str:
    """Short courteous note for a human sender whose message is not a helpdesk request."""
    if language in ("roman_urdu", "urdu"):
        body = ("Yeh inbox sirf Student Helpdesk ke sawalat (fees, hostel, academics, IT) ke liye hai. Is maamle ke "
                "liye meharbani kar ke mutaliqa idaray ya bhejne wale se rabta karein.")
    else:
        body = ("This inbox handles Student Helpdesk queries (fees, hostel, academics and IT). For this matter, "
                "please contact the organisation or office concerned directly.")
    return f"Dear {name},\n\n{body}\n\nRegards,\nStudent Helpdesk"


def thanks_reply(name: str, language: str) -> str:
    if language in ("roman_urdu", "urdu"):
        body = "Aap ke paigham ka shukriya. Agar dobara koi masla ho to humein zaroor likhiye."
    else:
        body = "Thank you for letting us know. If anything else comes up, please write to us again."
    return f"Dear {name},\n\n{body}\n\nRegards,\nStudent Helpdesk"


def looks_like_question(text: str) -> bool:
    """A real (if out-of-scope) request, as opposed to gibberish: enough real words + a question/request."""
    words = [w for w in re.findall(r"[A-Za-z]{3,}", text) if re.search(r"[aeiou]", w.lower())]
    distinct = {w.lower() for w in words}
    asks = re.search(r"(?i)\?|\b(how|what|when|where|who|which|can|could|please|kya|kaise|kab)\b", text)
    return len(distinct) >= 5 and bool(asks)


def unsupported_language_reply(name: str) -> str:
    return (f"Dear {name},\n\nThank you for your message. The Student Helpdesk can only handle emails in English "
            "or Roman Urdu. Please write to us again in English or Roman Urdu, with your roll number, so we can help."
            "\n\nRegards,\nStudent Helpdesk")
