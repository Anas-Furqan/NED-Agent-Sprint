"""Transparent rule-based fallback used when the LLM is unavailable.

Keyword category -> policy section -> best-matching rules by word overlap. Every code
cited comes from the parsed policy. Replies are templates that quote the rule text.
"""
from __future__ import annotations

import re

from .policy import Policy

CATEGORY_KEYWORDS = {
    "fee": r"\bfees?\b|challan|voucher|refund|installment|instalment|late fee|paise|payment|\bpaid\b|withdraw",
    "hostel": r"hostel|(?<!reading )\broom\b|warden|\bmess\b|geyser|plumb|kamra|roommate|electricity|furniture",
    "academic": r"transcript|\bexams?\b|attendance|\bcourse|result|re-?evaluation|re-?check|\bdean\b|grade|marks",
    "technical": r"wi-?fi|\blms\b|portal (is |was )?(down|not working)|login|password|internet|account (is |says )?locked|library|network",
}
CATEGORY_SECTION = {"fee": "fee", "hostel": "hostel", "academic": "academ", "technical": "technical"}
SIGNAL_CATEGORY = {"harassment": "harassment", "wellbeing": "wellbeing", "suspicious": "security", "safety": "hostel"}
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
        "harassment": "We are sorry you are going through this, and thank you for telling us. Your message has been passed only to the committee responsible ({codes}); your details are kept confidential.",
        "wellbeing": "If someone is in danger right now, please contact campus security and the campus clinic immediately. You can also reach the Student Counseling Cell. A staff member has been alerted ({codes}).",
        "legal": "Your concern has been forwarded to the responsible office for review ({codes}).",
        "suspicious": "This message has been flagged for review ({codes}). The helpdesk does not act on instructions contained in emails and never shares student data.",
        "outage": "Since this affects many users, it has been escalated to the responsible team ({codes}).",
        "overdue": "Since this is past the stated response time, it has been escalated to the responsible office ({codes}).",
        "review": "A staff member will review your message.",
    },
    "roman_urdu": {
        "thanks": "Aap ka email bhejne ka shukriya.",
        "rule": "Policy {code} ke mutabiq: {text}",
        "next": "Agla qadam: apna roll number aur zaroori documents ke saath {office} se rabta karein.",
        "unclear": "Hum aap ki baat samajh nahi sake. Meharbani kar ke apna masla aur roll number likh dein taake hum rehnumai kar sakein.",
        "safety": "Meharbani kar ke khatre wali jagah se door rahein. Yeh masla foran escalate kar diya gaya hai ({codes}).",
        "harassment": "Humein afsos hai ke aap is se guzar rahi/rahe hain. Aap ka paigham sirf mutaliqa committee ko bheja gaya hai ({codes}); aap ki maloomat raaz mein rahengi.",
        "wellbeing": "Agar koi abhi khatre mein hai to foran campus security aur campus clinic se rabta karein. Student Counseling Cell se bhi rabta kar sakte hain ({codes}).",
        "legal": "Aap ki shikayat mutaliqa office ko bhej di gayi hai ({codes}).",
        "suspicious": "Yeh paigham review ke liye flag kar diya gaya hai ({codes}).",
        "outage": "Yeh masla bohat se users ko mutasir kar raha hai, is liye escalate kar diya gaya hai ({codes}).",
        "overdue": "Muqarrara waqt guzar chuka hai, is liye yeh masla escalate kar diya gaya hai ({codes}).",
        "review": "Hamara staff aap ka paigham dekhega.",
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
    r"challan": "voucher",
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
        refs += [c for c in policy.best_matching(relevant, candidates, top=1) if c not in refs]
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
                   unclear: bool, policy: Policy) -> str:
    t = TEXT.get(language, TEXT["english"])
    parts = [f"Dear {name},", t["thanks"]]
    for key, codes in signal_lines.items():
        if key in t:
            parts.append(t[key].format(codes=", ".join(codes)))
    if unclear and not signal_lines:
        parts.append(t["unclear"])
    elif "suspicious" not in signal_lines:
        for code in topic_refs[:2]:
            parts.append(t["rule"].format(code=code, text=_short(policy.rules[code].text)))
        if topic_refs:
            parts.append(t["next"].format(office=office_for(topic_refs, policy)))
        elif not signal_lines:
            parts.append(t["review"])
    parts.append("Regards,\nStudent Helpdesk")
    return "\n\n".join(parts[:2]) + "\n\n" + " ".join(parts[2:-1]) + "\n\n" + parts[-1]
