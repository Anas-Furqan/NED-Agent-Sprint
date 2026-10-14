"""Per-email triage pipeline: load -> pre-checks -> LLM -> policy/timing guardrails -> reply validation."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from . import fallback as fb
from .llm import GeminiClient, LLMUnavailable
from .policy import Policy
from .prompts import SYSTEM, build_prompt, build_repair_prompt
from .schemas import (Email, LLMReply, LLMTriage, OutboxEntry, max_priority, normalize_category,
                      normalize_priority)
from .signals import Signal, detect_signals, strip_suspicious, wants_confidentiality
from .timing import check_overdue, deadline_urgency
from .validate import detect_language, greeting_name, repair_and_check

log = logging.getLogger("helpdesk")
CODE_LIKE = re.compile(r"\b([A-Za-z]+)\s*[-_ ]?\s*(\d+)\b")


@dataclass
class Result:
    entry: OutboxEntry
    fallback: bool = False
    invalid: bool = False
    notes: list[str] = field(default_factory=list)


def clean_refs(raw: list[str], policy: Policy) -> tuple[list[str], list[str]]:
    kept, dropped = [], []
    for item in raw or []:
        m = CODE_LIKE.search(str(item))
        code = f"{m.group(1).upper()}-{m.group(2)}" if m else str(item).strip()
        if code in policy.rules:
            if code not in kept:
                kept.append(code)
        else:
            dropped.append(str(item))
    return kept, dropped


def load_email(path: Path) -> tuple[Email | None, str]:
    """Returns (email, problem). Never raises."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        return None, f"could not read file: {exc}"
    if not raw.strip():
        return None, "file is empty"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON: {exc.msg} (line {exc.lineno})"
    if not isinstance(data, dict):
        return None, "JSON is not an object"
    data = {k: ("" if v is None else v) for k, v in data.items()}
    data.setdefault("id", path.stem)
    data["id"] = str(data["id"] or path.stem)
    for key in ("from_name", "from_email", "subject", "body"):
        data[key] = str(data.get(key, "") or "")
    try:
        email = Email.model_validate({k: data.get(k) for k in Email.model_fields if k in data})
    except ValidationError as exc:
        return None, f"invalid fields: {exc.errors()[0]['msg']}"
    if not email.body.strip() and not email.subject.strip():
        return email, "email has no subject or body"
    if not email.body.strip():
        return email, "email body is empty"
    return email, ""


class TriageAgent:
    def __init__(self, policy: Policy, client: GeminiClient | None, delay_seconds: float = 2.0):
        self.policy = policy
        self.client = client
        self.delay = delay_seconds

    # ---------- per email ----------
    def process(self, path: Path) -> Result:
        email, problem = load_email(path)
        if email is None or problem:
            return self._invalid(path, email, problem)

        text = f"{email.subject}\n{email.body}"
        language = detect_language(text)
        name = greeting_name(email.from_name)
        log.info("  1. Load       ok | language=%s | greet as '%s' | received_at=%s", language, name, email.received_at)

        signals = detect_signals(email.subject, email.body, self.policy)
        strong = [s for s in signals if s.strong]
        weak = [s for s in signals if not s.strong]
        log.info("  2. Pre-check  strong: %s | weak hints: %s",
                 _fmt_signals(strong) or "none", _fmt_signals(weak) or "none")
        hints = [f"{'STRONG' if s.strong else 'weak'} {s.label}: matched {s.evidence} -> rules {s.codes}"
                 for s in signals]
        confidential = wants_confidentiality(email.body)
        if confidential:
            hints.append("student explicitly asks for confidentiality")

        prompt = build_prompt(self.policy.text, email.model_dump(), name, language, hints, email.received_at)
        triage, llm_error = None, ""
        if self.client:
            try:
                triage = self.client.generate(SYSTEM, prompt, LLMTriage)
            except LLMUnavailable as exc:
                llm_error = str(exc)
        else:
            llm_error = "no GEMINI_API_KEY configured"

        used_fallback = triage is None
        if used_fallback:
            log.warning("  3. LLM        unavailable (%s) -> FALLBACK rule-based triage", llm_error[:100])
            triage = self._fallback_triage(email, text, name, language, signals)
        else:
            log.info("  3. LLM        category=%s (+%s) | issues=%d | priority=%s | escalate=%s | conf=%.2f",
                     triage.primary_category, ",".join(triage.secondary_categories) or "-",
                     len(triage.issues), triage.priority, triage.escalate, triage.confidence)

        # --- policy refs: keep only codes that exist in policy.md ---
        raw_refs = list(triage.policy_refs) + [r for i in triage.issues for r in i.policy_refs]
        refs, dropped = clean_refs(raw_refs, self.policy)
        if dropped:
            log.warning("  4. Policy     dropped invented/unknown refs %s", dropped)
        log.info("  4. Policy     refs from analysis: %s", refs or "none")

        category = normalize_category(triage.primary_category)
        priority = normalize_priority(triage.priority)
        escalate = bool(triage.escalate)
        reasons = [triage.escalation_reason.strip()] if escalate and triage.escalation_reason.strip() else []
        model_escalated = escalate
        code_notes: list[str] = []
        forced: list[str] = []

        # --- deterministic escalation overrides (can only add) ---
        for s in strong:
            new = [c for c in s.codes if c not in refs]
            refs += new
            priority = max_priority(priority, s.priority)
            if not escalate:
                forced.append(s.name)
            escalate = True
            if not any(c in " ".join(reasons) for c in s.codes):
                reasons.append(f"{s.label} ({', '.join(s.codes)})")
            if s.name == "suspicious":
                category = "security" if category in ("other", "unclear") else category
        if strong:
            code_notes.append("signals: " + ", ".join(s.name for s in strong))

        # --- timing: overdue vs policy response windows ---
        timing = check_overdue(email.body, email.received_at, refs, self.policy)
        for f in timing:
            status = "OVERDUE" if f.overdue else "within window"
            log.info("  5. Timing     %s: '%s' (~%.0fh) vs window %s -> %s",
                     f.code, f.elapsed_text, f.elapsed_hours, f.window_text, status)
            if f.overdue:
                esc_codes = self.policy.find_rules(["overdue"], "escalation")
                same_section = [c for c in self.policy.rules_in_section(self.policy.section_of(f.code))
                                if re.search(r"(?i)unresolved|must be escalated", self.policy.rules[c].text)]
                new = [c for c in esc_codes + same_section if c not in refs]
                refs += new
                priority = max_priority(priority, "medium")
                if not escalate:
                    forced.append("overdue")
                escalate = True
                reasons.append(f"overdue: {f.elapsed_text} exceeds {f.code} window of {f.window_text} "
                               f"({', '.join(esc_codes + same_section)})")
                code_notes.append(f"overdue {f.code}")
        if not timing:
            log.info("  5. Timing     no elapsed-time claim tied to a response window")

        # --- deadline urgency (priority only, never escalation) ---
        urgency = deadline_urgency(email.body)
        if urgency and priority != max_priority(priority, urgency[0]):
            log.info("  5. Deadline   '%s' -> priority raised %s -> %s", urgency[1], priority, urgency[0])
            priority = urgency[0]
            code_notes.append(f"deadline '{urgency[1]}'")

        if used_fallback and category in ("other", "unclear") and not escalate:
            escalate = True
            reasons.append("fallback could not classify confidently; needs human review")
        if escalate and priority == "low":
            priority = "medium"

        # --- reply validation (repair in code, regenerate once if needed) ---
        signal_names = {s.name for s in strong}
        reply, check = repair_and_check(triage.reply_draft, name=name, language=language, refs=refs,
                                        sender_email=email.from_email, signals=signal_names,
                                        confidential=confidential)
        problems = list(check.problems)
        if forced and not used_fallback:
            problems.append("code-level checks escalated this email (" + "; ".join(reasons)
                            + "); the reply must say it has been escalated and cite " + ", ".join(refs))
        repairs = list(check.repairs)
        regenerated = False
        if problems and self.client and not used_fallback:
            log.info("  6. Validate   problems: %s -> regenerating reply once", problems)
            try:
                fix = self.client.generate(SYSTEM, build_repair_prompt(prompt, reply, problems), LLMReply)
                reply2, check2 = repair_and_check(fix.reply_draft, name=name, language=language, refs=refs,
                                                  sender_email=email.from_email, signals=signal_names,
                                                  confidential=confidential)
                regenerated = True
                if check2.ok:
                    reply, problems, repairs = reply2, [], repairs + check2.repairs
                else:
                    problems = check2.problems
            except LLMUnavailable as exc:
                log.warning("    regeneration failed: %s", exc)
        elif problems and used_fallback:
            problems = [p for p in problems if not p.startswith("code-level")]
        if problems:
            log.warning("  6. Validate   still failing %s -> using safe template reply", problems)
            reply = self._template_reply(email, text, name, language, refs, strong, timing, category)
            reply, _ = repair_and_check(reply, name=name, language=language, refs=refs,
                                        sender_email=email.from_email, signals=signal_names, confidential=confidential)
            repairs.append("replaced with template reply")
        else:
            log.info("  6. Validate   passed%s", f" (code repairs: {', '.join(repairs)})" if repairs else "")

        # --- confidence ---
        conf = float(triage.confidence or 0.5)
        if conf > 1:  # model returned a percentage
            conf = conf / 100
        conf = max(0.0, min(0.95, conf))  # never fully certain: a human still reviews drafts
        conf -= 0.1 * min(len(dropped), 2)
        conf -= 0.03 * len(repairs)
        conf -= 0.1 if regenerated else 0
        conf -= 0.15 if forced and not model_escalated and not used_fallback else 0
        if category in ("unclear", "other"):
            conf = min(conf, 0.45)
        if used_fallback:
            conf = min(conf, 0.4)
        conf = round(max(0.05, min(1.0, conf)), 2)

        # --- notes ---
        notes = (triage.agent_notes or "").strip()
        secondary = sorted(({normalize_category(c) for c in triage.secondary_categories} |
                            {normalize_category(i.category) for i in triage.issues}) - {category, "other"})
        extra = []
        if used_fallback:
            extra.append(f"FALLBACK (LLM unavailable: {llm_error[:60]})")
        if secondary:
            extra.append("also covers: " + ", ".join(secondary))
        if len(triage.issues) > 1:
            extra.append(f"{len(triage.issues)} issues addressed")
        if code_notes:
            extra.append("code checks: " + "; ".join(code_notes))
        if forced and not used_fallback:
            extra.append("escalation added by deterministic guardrail: " + ", ".join(forced))
        if dropped:
            extra.append(f"dropped unknown refs {dropped}")
        if language != "english":
            extra.append(f"language: {language}")
        agent_notes = notes + (" | " if notes and extra else "") + " | ".join(extra)

        entry = OutboxEntry(
            email_id=email.id, category=category, priority=priority, escalate=escalate,
            escalation_reason="; ".join(dict.fromkeys(r for r in reasons if r)) if escalate else "",
            policy_refs=refs, reply_draft=reply, confidence=conf, agent_notes=agent_notes,
        )
        log.info("  7. Decision   %s | priority=%s | escalate=%s%s | refs=%s | conf=%.2f",
                 entry.category, entry.priority, entry.escalate,
                 f" ({entry.escalation_reason[:110]})" if entry.escalate else "",
                 entry.policy_refs, entry.confidence)
        log.debug("  reply_draft:\n%s\n  agent_notes: %s", entry.reply_draft, entry.agent_notes)
        return Result(entry, used_fallback)

    # ---------- helpers ----------
    def _fallback_triage(self, email: Email, text: str, name: str, language: str,
                         signals: list[Signal]) -> LLMTriage:
        strong = [s for s in signals if s.strong]
        clean = strip_suspicious(text)  # never act on the injected part of a message
        cats = fb.keyword_categories(clean)
        category = next((fb.SIGNAL_CATEGORY[s.name] for s in strong
                         if s.name in fb.SIGNAL_CATEGORY and s.name != "safety"), None)
        if not category and any(s.name == "safety" for s in strong):
            # category of the topic (non-escalation) rule the hazard signal points to, e.g. a hostel rule
            sections = [self.policy.section_of(c).lower() for s in strong for c in s.codes]
            category = next((cat for sec in sections for cat, key in fb.CATEGORY_SECTION.items() if key in sec), None)
        category = category or (cats[0] if cats else "unclear")
        topic = fb.topic_rules(clean, cats, self.policy)
        if cats and not topic and category == cats[0]:
            category = "other"  # topic words present but no policy rule matches
        sig_codes = [c for s in strong for c in s.codes]
        refs = list(dict.fromkeys(topic + sig_codes))
        priority = "high" if any(s.priority == "high" for s in strong) else ("medium" if cats else "low")
        reply = self._template_reply(email, text, name, language, refs, strong, [], category)
        not_covered = " Topic not covered by policy." if cats and not topic else ""
        return LLMTriage(
            primary_category=category, secondary_categories=cats[1:3], issues=[],
            priority=priority, escalate=bool(strong),
            escalation_reason="; ".join(f"{s.label} ({', '.join(s.codes)})" for s in strong),
            policy_refs=refs, reply_draft=reply, confidence=0.4 if (topic or strong) else 0.2,
            detected_language=language,
            agent_notes=f"Rule-based: keywords -> {cats or ['none']}; rules by word overlap -> {topic or 'none'}.{not_covered}",
        )

    def _template_reply(self, email: Email, text: str, name: str, language: str, refs: list[str],
                        strong: list[Signal], timing, category: str) -> str:
        clean = strip_suspicious(text)
        cats = fb.keyword_categories(clean)
        signal_codes = {c for s in strong for c in s.codes}
        topic = [c for c in refs if c not in signal_codes and not self.policy.section_of(c).lower().startswith("escalation")]
        if not topic:
            topic = fb.topic_rules(clean, cats, self.policy)
        lines = {s.name: s.codes for s in strong}
        for f in timing or []:
            if f.overdue:
                lines["overdue"] = [c for c in refs if c not in topic] or [f.code]
        unclear = category == "unclear" or (not cats and not strong)
        office = fb.office_for(self.policy.rules_in_section(fb.CATEGORY_SECTION[cats[0]]), self.policy) if cats else None
        return fb.fallback_reply(name, language, topic, lines, unclear, self.policy, not_covered_office=office)

    def _invalid(self, path: Path, email: Email | None, problem: str) -> Result:
        eid = email.id if email else path.stem
        name = greeting_name(email.from_name) if email else "Student"
        log.warning("  1. Load       INVALID: %s -> safe placeholder entry, escalated for human review", problem)
        reply = (f"Dear {name},\n\nThank you for contacting us. We could not read the content of your message. "
                 "Could you please resend it with a short description of your issue and your roll number?"
                 "\n\nRegards,\nStudent Helpdesk")
        entry = OutboxEntry(
            email_id=eid, category="unclear", priority="medium", escalate=True,
            escalation_reason=f"Input could not be processed automatically ({problem}); needs human review",
            policy_refs=[], reply_draft=reply, confidence=0.1,
            agent_notes=f"Invalid input file '{path.name}': {problem}. No LLM call made.",
        )
        return Result(entry, invalid=True)

    # ---------- whole inbox ----------
    def run(self, inbox: Path, out: Path) -> list[OutboxEntry]:
        files = sorted(p for p in inbox.glob("*.json") if p.is_file())
        log.info("Found %d email file(s) in %s | %d policy rules loaded", len(files), inbox, len(self.policy.rules))
        results: list[Result] = []
        for i, path in enumerate(files, 1):
            subject = _peek_subject(path)
            log.info("")
            log.info("━━ [%d/%d] %s  \"%s\"", i, len(files), path.stem, subject[:70])
            try:
                results.append(self.process(path))
            except Exception as exc:  # last-resort guard: never stop the run
                log.exception("  unexpected error: %s", exc)
                results.append(self._invalid(path, None, f"internal error: {type(exc).__name__}"))
            if self.client and i < len(files) and not self.client.daily_quota_exhausted:
                time.sleep(self.delay)
        entries = sorted((r.entry for r in results), key=lambda e: e.email_id)
        write_outbox(entries, out)
        _summary(results, out, self.client)
        return entries


def write_outbox(entries: list[OutboxEntry], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([e.model_dump() for e in entries], ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".outbox.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload + "\n")
    os.replace(tmp, out)


def _peek_subject(path: Path) -> str:
    try:
        return str(json.loads(path.read_text(encoding="utf-8-sig")).get("subject", ""))
    except Exception:
        return "(unreadable)"


def _fmt_signals(signals: list[Signal]) -> str:
    return "; ".join(f"{s.name}{s.evidence[:2]}" for s in signals)


def _summary(results: list[Result], out: Path, client: GeminiClient | None) -> None:
    entries = [r.entry for r in results]
    log.info("")
    log.info("════════ SUMMARY ════════")
    log.info("Processed: %d | escalated: %d | LLM fallback: %d | invalid input files: %d | LLM calls: %s",
             len(entries), sum(e.escalate for e in entries), sum(r.fallback for r in results),
             sum(r.invalid for r in results), client.calls if client else 0)
    log.info("By category: %s", dict(Counter(e.category for e in entries).most_common()))
    log.info("By priority: %s", dict(Counter(e.priority for e in entries).most_common()))
    log.info("Outbox written: %s (nothing was sent)", out)
