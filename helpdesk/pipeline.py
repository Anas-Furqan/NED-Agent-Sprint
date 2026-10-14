"""Per-email triage pipeline: load -> pre-checks -> LLM -> policy/timing guardrails -> reply validation.

Every step is logged AND recorded in a trace (run_trace.json) so the dashboard can show how each
decision was made. outbox.json keeps exactly the sample_outbox.json format.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import fallback as fb
from . import priority as prio
from .cache import DecisionCache, atomic_write_json, cache_key, content_hash
from .llm import GeminiClient, LLMUnavailable
from .loader import Loaded, is_email_file, load_email
from .policy import Policy
from .prompts import SYSTEM, build_prompt, build_repair_prompt
from .schemas import LLMReply, LLMTriage, OutboxEntry, normalize_category
from .signals import Signal, detect_signals, message_kind, strip_suspicious, wants_confidentiality
from .timing import check_overdue, deadline_urgency
from .validate import detect_language, greeting_name, repair_and_check

log = logging.getLogger("helpdesk")
CODE_LIKE = re.compile(r"\b([A-Za-z]+)\s*[-_ ]?\s*(\d+)\b")
SIGNAL_PHRASE = {
    "safety": "safety hazard", "harassment": "harassment report", "wellbeing": "medical/wellbeing concern",
    "legal": "legal/media threat", "suspicious": "suspicious message (injection, phishing or data request)",
    "outage": "outage affecting many users", "integrity": "academic-integrity request (bribery/grade change/cheating)",
}
HISTORY_LIMIT = 50


@dataclass
class Trace:
    data: dict = field(default_factory=dict)

    def step(self, n: int, name: str, detail: str, status: str = "ok", level: int = logging.INFO) -> None:
        self.data.setdefault("steps", []).append({"n": n, "name": name, "status": status, "detail": detail})
        log.log(level, "  %d. %-10s %s", n, name, detail)


@dataclass
class Result:
    entry: OutboxEntry
    trace: dict
    fallback: bool = False
    invalid: bool = False
    cache_hit: bool = False
    llm_calls: int = 0


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


def compose_reason(model_reason: str, items: list[tuple[str, list[str]]], routes: list[str]) -> str:
    """One readable sentence. Model reason first; code-found reasons it did not mention are added."""
    base = re.split(r"(?<=[.!?])\s", (model_reason or "").strip(), maxsplit=1)[0].rstrip(" .;")
    # code-found reasons the model's own reason does not already cover (code-less ones only if no model reason)
    extra = [(p, c) for p, c in items if (c and not any(code in base for code in c)) or (not c and not base)]
    if re.search(r"(?i)overdue|unresolved|past the|exceed|response time|delay", base):
        extra = [(p, c) for p, c in extra if not p.startswith("overdue:")]  # model already explained the delay
    extra = [(p, c) for p, c in extra if p.lower() not in base.lower()]
    if base:
        sentence = base + ("; also " + " and ".join(f"{p} ({', '.join(c)})" if c else p for p, c in extra)
                           if extra else "")
    else:
        codes = sorted({code for _, c in items for code in c})
        sentence = "Needs human action: " + " and ".join(p for p, _ in items)
        sentence += f" ({', '.join(codes)})" if codes else ""
    if routes and not any(r.lower() in sentence.lower() for r in routes):
        sentence += f"; route to {', '.join(routes)}"
    return sentence[0].upper() + sentence[1:] + "."


class TriageAgent:
    def __init__(self, policy: Policy, client: GeminiClient | None, delay_seconds: float = 2.0,
                 cache: DecisionCache | None = None):
        self.policy = policy
        self.client = client
        self.delay = delay_seconds
        self.cache = cache or DecisionCache.disabled()

    # ---------- per email ----------
    def process(self, path: Path, loaded: Loaded | None = None, email_id: str | None = None) -> Result:
        start = time.perf_counter()
        loaded = loaded or load_email(path)
        trace = Trace({"file": path.name, "hash": content_hash(loaded.raw), "cache_hit": False,
                       "fallback": False, "invalid": False, "model": None, "attempts": 0,
                       "llm_errors": [], "load_notes": list(loaded.notes),
                       "timestamp": datetime.now().replace(microsecond=0).isoformat()})
        if loaded.email is not None and email_id and email_id != loaded.email.id:
            loaded.notes.append(f"duplicate id '{loaded.email.id}': renamed to '{email_id}'")
            trace.data["load_notes"] = list(loaded.notes)
            loaded.email = loaded.email.model_copy(update={"id": email_id})
        if loaded.email is not None:
            trace.data["email"] = loaded.email.model_dump()

        if loaded.email is None or loaded.problem:
            result = self._invalid(path, loaded, trace, email_id)
        else:
            key = cache_key(loaded.raw, self.policy.text, "llm" if self.client else "offline")
            cached = self.cache.get(key) if self.client else None
            if cached:
                entry = OutboxEntry.model_validate({**cached["entry"], "email_id": loaded.email.id})
                trace.data.update({k: v for k, v in cached["trace"].items() if k not in ("timestamp", "file", "email")})
                trace.data["cache_hit"] = True
                log.info("  ⚡ cache hit  same email + same policy as an earlier run: reusing decision (no LLM call)")
                log.info("  7. %-10s %s | priority=%s | escalate=%s | refs=%s | conf=%.2f", "Decision",
                         entry.category, entry.priority, entry.escalate, entry.policy_refs, entry.confidence)
                result = Result(entry, trace.data, cache_hit=True)
            else:
                result = self._triage(loaded, trace)
                if not result.fallback and self.client:
                    self.cache.put(key, {"entry": result.entry.model_dump(), "trace": result.trace})
        result.trace["duration_ms"] = int((time.perf_counter() - start) * 1000)
        result.trace["email_id"] = result.entry.email_id
        return result

    def _triage(self, loaded: Loaded, tr: Trace) -> Result:
        email = loaded.email
        body = loaded.newest
        text = f"{email.subject}\n{body}"
        language = detect_language(text)
        name = greeting_name(email.from_name)
        notes = list(loaded.notes)
        tr.data["language"] = language
        tr.step(1, "Load", f"ok | language={language} | greet as '{name}' | received_at={email.received_at}"
                + (f" | notes: {'; '.join(notes)}" if notes else ""), "warn" if notes else "ok")

        signals = detect_signals(email.subject, body, self.policy, from_name=email.from_name)
        strong = [s for s in signals if s.strong]
        weak = [s for s in signals if not s.strong]
        kind = message_kind(email.subject, body, email.from_email)
        confidential = wants_confidentiality(body)
        tr.data["signals"] = [{"name": s.name, "strong": s.strong, "evidence": s.evidence, "codes": s.codes}
                              for s in signals]
        tr.step(2, "Pre-check", f"strong: {_fmt_signals(strong) or 'none'} | weak hints: {_fmt_signals(weak) or 'none'}"
                + (f" | kind: {kind}" if kind else "") + (" | confidentiality requested" if confidential else ""),
                "alert" if strong else "ok")
        hints = [f"STRONG {s.label}: matched {s.evidence} -> rules {s.codes}" if s.strong else
                 f"weak keyword only (needs context; often figurative or harmless, do not escalate on this "
                 f"alone): {s.name} words {s.evidence}" for s in signals]
        if confidential:
            hints.append("student explicitly asks for confidentiality")
        if kind:
            hints.append(f"message looks like: {kind}")
        hints += [f"loader note: {n}" for n in notes]

        prompt_email = email.model_dump() | {"body": loaded.llm_body or body}
        prompt = build_prompt(self.policy.text, prompt_email, name, language, hints, email.received_at)
        triage, llm_error, calls = None, "", 0
        if self.client:
            try:
                triage = self.client.generate(SYSTEM, prompt, LLMTriage)
            except LLMUnavailable as exc:
                llm_error = str(exc)
            calls += self.client.last["attempts"]
            tr.data.update(model=self.client.last["model"], attempts=self.client.last["attempts"],
                           llm_errors=list(self.client.last["errors"]))
            if self.client.last["repaired"]:
                notes += self.client.last["repaired"]
        else:
            llm_error = "no GEMINI_API_KEY configured / offline mode"

        used_fallback = triage is None
        tr.data["fallback"] = used_fallback
        if used_fallback:
            tr.step(3, "LLM", f"unavailable ({llm_error[:100]}) -> FALLBACK rule-based triage", "warn", logging.WARNING)
            triage = self._fallback_triage(email, text, name, language, signals, kind)
        else:
            tr.step(3, "LLM", f"model={tr.data['model']} attempts={tr.data['attempts']} | category="
                    f"{triage.primary_category} (+{','.join(triage.secondary_categories) or '-'}) | issues="
                    f"{len(triage.issues)} | priority={triage.priority} | escalate={triage.escalate} | "
                    f"conf={triage.confidence:.2f}")

        # --- policy refs: keep only codes that exist in policy.md ---
        raw_refs = list(triage.policy_refs) + [r for i in triage.issues for r in i.policy_refs]
        refs, dropped = clean_refs(raw_refs, self.policy)
        raw_category = (triage.primary_category or "").strip().lower()
        category = normalize_category(triage.primary_category)
        if raw_category and category == "other" and raw_category != "other":
            notes.append(f"unknown category '{raw_category[:30]}' mapped to 'other'")
        tr.step(4, "Policy", f"refs: {refs or 'none'}" + (f" | dropped invented refs {dropped}" if dropped else ""),
                "warn" if dropped else "ok", logging.WARNING if dropped else logging.INFO)

        priority = prio.normalize_priority(triage.priority)
        escalate = bool(triage.escalate)
        model_escalated = escalate
        reason_items: list[tuple[str, list[str]]] = []
        code_notes: list[str] = []
        forced: list[str] = []

        # --- deterministic escalation overrides (can only add) ---
        for s in strong:
            refs += [c for c in s.codes if c not in refs]
            if not escalate:
                forced.append(s.name)
            escalate = True
            reason_items.append((SIGNAL_PHRASE.get(s.name, s.label), s.codes))
            if s.name == "suspicious" and category in ("other", "unclear", "spam"):
                category = "security"
            if s.name == "integrity" and category in ("other", "unclear"):
                category = "academic"
        if strong:
            code_notes.append("signals: " + ", ".join(s.name for s in strong))

        # message kinds that need no action (only when nothing risky was found)
        if kind in prio.NO_ACTION_CATEGORIES and not strong and category in ("other", "unclear", "spam", "auto_reply"):
            category = kind

        # --- timing: overdue vs policy response windows ---
        timing = check_overdue(body, email.received_at, refs, self.policy)
        overdue_any = False
        tr.data["timing"] = [{"code": f.code, "elapsed": f.elapsed_text, "hours": round(f.elapsed_hours),
                              "window": f.window_text, "overdue": f.overdue} for f in timing]
        details = []
        for f in timing:
            details.append(f"{f.code}: '{f.elapsed_text}' (~{f.elapsed_hours:.0f}h) vs {f.window_text} -> "
                           + ("OVERDUE" if f.overdue else "within window"))
            if f.overdue:
                overdue_any = True
                esc_codes = self.policy.find_rules(["overdue"], "escalation")
                same_section = [c for c in self.policy.rules_in_section(self.policy.section_of(f.code))
                                if re.search(r"(?i)unresolved|must be escalated", self.policy.rules[c].text)]
                refs += [c for c in esc_codes + same_section if c not in refs]
                if not escalate:
                    forced.append("overdue")
                escalate = True
                reason_items.append((f"overdue: {f.elapsed_text} exceeds the {f.window_text} window of {f.code}",
                                     esc_codes + same_section))
                code_notes.append(f"overdue {f.code} ({f.elapsed_text} > {f.window_text})")
        urgency = deadline_urgency(body)
        if urgency:
            details.append(f"deadline '{urgency[1]}' -> priority at least {urgency[0]}")
        tr.step(5, "Timing", " | ".join(details) or "no elapsed-time claim tied to a response window",
                "alert" if overdue_any else "ok")

        if used_fallback and category == "unclear" and not escalate and not kind:
            escalate = True
            reason_items.append(("fallback could not classify confidently; needs human review", []))

        # --- priority: one scheme (priority.py), code enforces minimums ---
        priority, changes = prio.enforce(priority, signals=[s.name for s in strong], category=category,
                                         overdue=overdue_any, deadline=urgency[0] if urgency else None,
                                         escalate=escalate, kind=kind)
        if changes:
            code_notes.append("priority " + ", ".join(changes))

        # --- reply validation (repair in code, regenerate once if needed) ---
        signal_names = {s.name for s in strong}
        check_args = dict(name=name, language=language, refs=refs, sender_email=email.from_email,
                          signals=signal_names, confidential=confidential)
        reply, check = repair_and_check(triage.reply_draft, **check_args)
        problems = list(check.problems)
        if forced and not used_fallback:
            problems.append("code-level checks escalated this email (" + "; ".join(p for p, _ in reason_items)
                            + "); the reply must say it has been escalated and cite " + ", ".join(refs))
        repairs = list(check.repairs)
        regenerated, template = False, False
        if problems and self.client and not used_fallback:
            log.info("  6. %-10s problems: %s -> regenerating reply once", "Validate", problems)
            try:
                fix = self.client.generate(SYSTEM, build_repair_prompt(prompt, reply, problems), LLMReply)
                calls += self.client.last["attempts"]
                reply2, check2 = repair_and_check(fix.reply_draft, **check_args)
                regenerated = True
                if check2.ok:
                    reply, problems, repairs = reply2, [], repairs + check2.repairs
                else:
                    problems = check2.problems
            except LLMUnavailable as exc:
                calls += self.client.last["attempts"]
                log.warning("    regeneration failed: %s", exc)
        elif problems and used_fallback:
            problems = [p for p in problems if not p.startswith("code-level")]
        first_problems = list(check.problems)
        if problems:
            reply = self._template_reply(text, name, language, refs, strong, timing, category, kind)
            reply, _ = repair_and_check(reply, **check_args)
            repairs.append("replaced with safe template reply")
            template = True
        tr.data["validation"] = {"problems": first_problems + [p for p in problems if p not in first_problems],
                                 "repairs": repairs, "regenerated": regenerated, "template": template}
        tr.step(6, "Validate", ("passed" if not first_problems and not template else
                                f"problems {first_problems or problems}" + (" -> regenerated" if regenerated else "")
                                + (" -> safe template" if template else " -> fixed"))
                + (f" | repairs: {', '.join(repairs)}" if repairs else ""),
                "warn" if (first_problems or template) else "ok")

        # --- confidence ---
        conf = max(0.0, min(0.95, float(triage.confidence)))  # never fully certain: a human reviews drafts
        conf -= 0.1 * min(len(dropped), 2)
        conf -= 0.03 * len(repairs)
        conf -= 0.1 if regenerated else 0
        conf -= 0.15 if forced and not model_escalated and not used_fallback else 0
        conf -= 0.05 if any("truncated" in n or "decoded" in n for n in notes) else 0
        if category in ("unclear", "other"):
            conf = min(conf, 0.45 if category == "unclear" else 0.7)
        if used_fallback:
            conf = min(conf, 0.4)
        conf = round(max(0.05, min(1.0, conf)), 2)

        # --- reason + notes ---
        routes = self.policy.routes(refs) if escalate else []
        if escalate and "Responsible office" in routes:
            # the office of the overdue rule, else of the first topic rule
            topic = [f.code for f in timing if f.overdue] or [
                c for c in refs if not self.policy.section_of(c).lower().startswith("escalation")]
            office = fb.office_for(topic, self.policy) if topic else None
            if office and office != "relevant office":
                routes = [office if r == "Responsible office" else r for r in routes]
        reason = compose_reason(triage.escalation_reason if model_escalated else "", reason_items, routes) if escalate else ""
        tr.data["routes"] = routes

        model_notes = (triage.agent_notes or "").strip()
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
        if notes:
            extra.append("input: " + "; ".join(notes))
        agent_notes = model_notes + (" | " if model_notes and extra else "") + " | ".join(extra)

        entry = OutboxEntry(email_id=email.id, category=category, priority=priority, escalate=escalate,
                            escalation_reason=reason, policy_refs=refs, reply_draft=reply, confidence=conf,
                            agent_notes=agent_notes)
        tr.step(7, "Decision", f"{entry.category} | priority={entry.priority} | escalate={entry.escalate}"
                + (f" ({reason[:110]})" if escalate else "") + f" | refs={entry.policy_refs} | conf={conf:.2f}",
                "alert" if escalate else "ok")
        log.debug("  reply_draft:\n%s\n  agent_notes: %s", entry.reply_draft, entry.agent_notes)
        return Result(entry, tr.data, fallback=used_fallback, llm_calls=calls)

    # ---------- helpers ----------
    def _fallback_triage(self, email, text: str, name: str, language: str, signals: list[Signal],
                         kind: str | None) -> LLMTriage:
        strong = [s for s in signals if s.strong]
        clean = strip_suspicious(text)  # never act on the injected part of a message
        cats = fb.keyword_categories(clean)
        category = next((fb.SIGNAL_CATEGORY[s.name] for s in strong
                         if s.name in fb.SIGNAL_CATEGORY and s.name != "safety"), None)
        if not category and any(s.name == "safety" for s in strong):
            # category of the topic (non-escalation) rule the hazard signal points to, e.g. a hostel rule
            sections = [self.policy.section_of(c).lower() for s in strong for c in s.codes]
            category = next((cat for sec in sections for cat, key in fb.CATEGORY_SECTION.items() if key in sec), None)
        if not category and kind in prio.NO_ACTION_CATEGORIES:
            category = kind
        if not category and not cats:
            category = "other" if kind == "thanks" or fb.looks_like_question(clean) else "unclear"
        category = category or cats[0]
        no_topic = category in prio.NO_ACTION_CATEGORIES or (kind == "thanks" and not strong)
        topic = [] if no_topic else fb.topic_rules(clean, cats, self.policy)
        if cats and not topic and category == cats[0] and not no_topic:
            category = "other"  # topic words present but no policy rule matches
        sig_codes = [c for s in strong for c in s.codes]
        refs = list(dict.fromkeys(topic + sig_codes))
        reply = self._template_reply(text, name, language, refs, strong, [], category, kind)
        not_covered = " Topic not covered by policy." if not topic and not no_topic and category == "other" else ""
        return LLMTriage(
            primary_category=category, secondary_categories=cats[1:3], issues=[],
            priority="medium" if cats else "low", escalate=bool(strong), escalation_reason="",
            policy_refs=refs, reply_draft=reply, confidence=0.4 if (topic or strong) else 0.2,
            detected_language=language,
            agent_notes=f"Rule-based: keywords -> {cats or ['none']}; rules by word overlap -> {topic or 'none'}.{not_covered}",
        )

    def _template_reply(self, text: str, name: str, language: str, refs: list[str], strong: list[Signal],
                        timing, category: str, kind: str | None = None) -> str:
        if category in prio.NO_ACTION_CATEGORIES and not strong:
            return fb.no_action_reply(name, language, category)
        if kind == "thanks" and not strong:
            return fb.thanks_reply(name, language)
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
        unclear = category == "unclear"
        office = fb.office_for(self.policy.rules_in_section(fb.CATEGORY_SECTION[cats[0]]), self.policy) if cats else None
        if not office and category == "other":
            office = "relevant department office"
        return fb.fallback_reply(name, language, topic, lines, unclear, self.policy, not_covered_office=office)

    def _invalid(self, path: Path, loaded: Loaded, tr: Trace, email_id: str | None) -> Result:
        email = loaded.email
        eid = email_id or (email.id if email else path.stem)
        name = greeting_name(email.from_name) if email else "Student"
        problem = loaded.problem or "unreadable input"
        tr.step(1, "Load", f"INVALID: {problem} -> safe placeholder entry, escalated for human review",
                "warn", logging.WARNING)
        reply = (f"Dear {name},\n\nThank you for contacting us. We could not read the content of your message. "
                 "Could you please resend it with a short description of your issue and your roll number?"
                 "\n\nRegards,\nStudent Helpdesk")
        extra = f" Notes: {'; '.join(loaded.notes)}." if loaded.notes else ""
        entry = OutboxEntry(
            email_id=eid, category="unclear", priority="medium", escalate=True,
            escalation_reason=f"Input could not be processed automatically ({problem}); needs human review.",
            policy_refs=[], reply_draft=reply, confidence=0.1,
            agent_notes=f"Invalid input file '{path.name}': {problem}. No LLM call made.{extra}",
        )
        tr.data["invalid"] = True
        tr.step(7, "Decision", f"unclear | priority=medium | escalate=True ({problem})", "alert")
        return Result(entry, tr.data, invalid=True)


# ---------- inbox runs ----------
def list_inbox(inbox: Path) -> tuple[list[Path], list[Path]]:
    """(email files, skipped files). Sorted by name so runs are deterministic."""
    files, skipped = [], []
    for p in sorted(inbox.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir():
            continue
        (files if is_email_file(p) else skipped).append(p)
    return files, skipped


def assign_ids(files: list[Path]) -> dict[Path, tuple[Loaded, str]]:
    """Load every file once and give each a unique, deterministic email_id (first file keeps the id)."""
    out, seen = {}, Counter()
    for path in files:
        loaded = load_email(path)
        base = loaded.email.id if loaded.email else path.stem
        seen[base] += 1
        eid = base if seen[base] == 1 else (f"{base}-{path.stem}" if path.stem != base else f"{base}-dup{seen[base]}")
        out[path] = (loaded, eid)
    return out


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_outbox(entries: list[OutboxEntry], out: Path) -> None:
    atomic_write_json(out, [e.model_dump() for e in sorted(entries, key=lambda e: e.email_id)])


class Runner:
    """Runs the agent over an inbox in different modes and keeps outbox.json + run_trace.json in sync."""

    def __init__(self, agent: TriageAgent, inbox: Path, out: Path, trace_path: Path | None = None):
        self.agent, self.inbox, self.out = agent, inbox, out
        self.trace_path = trace_path or out.with_name("run_trace.json")

    def run(self, mode: str = "all", only: list[Path] | None = None) -> list[Result]:
        started = datetime.now().replace(microsecond=0)
        files, skipped = list_inbox(self.inbox)
        for p in skipped:
            log.info("Skipping '%s': not an email file (accepted: .json, .txt, .eml)", p.name)
        ids = assign_ids(files)
        for path, (loaded, eid) in ids.items():
            if loaded.email and eid != loaded.email.id:
                log.warning("Duplicate email id '%s' in %s: using '%s'", loaded.email.id, path.name, eid)

        trace_doc = read_json(self.trace_path, {"emails": {}, "runs": []})
        outbox = {e["email_id"]: e for e in read_json(self.out, []) if isinstance(e, dict) and "email_id" in e}
        if only is not None:
            targets = [p for p in files if p.resolve() in {o.resolve() for o in only}]
        elif mode == "only-new":
            targets = [p for p in files if self._is_new(p, ids[p][1], trace_doc, outbox)]
        else:
            targets = files
        log.info("Found %d email file(s) in %s | %d to process (%s) | %d policy rules loaded",
                 len(files), self.inbox, len(targets), mode, len(self.agent.policy.rules))

        results: list[Result] = []
        for i, path in enumerate(targets, 1):
            loaded, eid = ids[path]
            subject = loaded.email.subject if loaded.email else "(unreadable)"
            log.info("")
            log.info("━━ [%d/%d] %s  \"%s\"", i, len(targets), eid, subject[:70])
            try:
                res = self.agent.process(path, loaded, eid)
            except Exception as exc:  # last-resort guard: never stop the run
                log.exception("  unexpected error: %s", exc)
                loaded.problem = f"internal error: {type(exc).__name__}"
                res = self.agent._invalid(path, loaded, Trace({"file": path.name}), eid)
            results.append(res)
            client = self.agent.client
            if client and res.llm_calls and i < len(targets) and not client.daily_quota_exhausted:
                time.sleep(self.agent.delay)

        run_id = started.strftime("%Y%m%d-%H%M%S")
        if mode == "all" and only is None:
            outbox, trace_doc["emails"] = {}, {}
        valid_ids = {eid for _, eid in ids.values()}
        for r in results:
            outbox[r.entry.email_id] = r.entry.model_dump()
            trace_doc["emails"][r.entry.email_id] = {**r.trace, "run_id": run_id}
        if mode != "all" or only is not None:  # merged run: drop entries whose file was removed
            outbox = {k: v for k, v in outbox.items() if k in valid_ids}
            trace_doc["emails"] = {k: v for k, v in trace_doc["emails"].items() if k in valid_ids}
        write_outbox([OutboxEntry.model_validate(v) for v in outbox.values()], self.out)

        ended = datetime.now().replace(microsecond=0)
        client = self.agent.client
        summary = {
            "run_id": run_id, "mode": mode if only is None else "file", "started": started.isoformat(),
            "ended": ended.isoformat(), "processed": len(results),
            "escalated": sum(r.entry.escalate for r in results),
            "fallback": sum(r.fallback for r in results), "invalid": sum(r.invalid for r in results),
            "cache_hits": sum(r.cache_hit for r in results), "llm_calls": sum(r.llm_calls for r in results),
            "models": sorted({r.trace.get("model") for r in results if r.trace.get("model")}),
            "offline": client is None, "skipped_files": [p.name for p in skipped],
            "by_category": dict(Counter(r.entry.category for r in results)),
            "by_priority": dict(Counter(r.entry.priority for r in results)),
            "avg_ms": int(sum(r.trace.get("duration_ms", 0) for r in results) / len(results)) if results else 0,
        }
        trace_doc["runs"] = (trace_doc.get("runs", []) + [summary])[-HISTORY_LIMIT:]
        trace_doc["policy_codes"] = sorted(self.agent.policy.rules)
        atomic_write_json(self.trace_path, trace_doc)
        _summary(summary, self.out, len(outbox))
        return results

    @staticmethod
    def _is_new(path: Path, eid: str, trace_doc: dict, outbox: dict) -> bool:
        if eid not in outbox:
            return True
        rec = trace_doc.get("emails", {}).get(eid, {})
        try:
            return rec.get("hash") != content_hash(path.read_bytes()) or rec.get("file") != path.name
        except Exception:
            return True

    def watch(self, interval: float = 3.0) -> None:
        log.info("Watching %s every %.0fs for new or changed emails (Ctrl+C to stop)", self.inbox, interval)
        self.run("only-new")
        seen = self._snapshot()
        try:
            while True:
                time.sleep(interval)
                now = self._snapshot()
                changed = [Path(p) for p, sig in now.items() if seen.get(p) != sig]
                if changed or set(seen) - set(now):
                    log.info("")
                    log.info("Detected %d new/changed file(s): %s", len(changed), [p.name for p in changed])
                    self.run("only-new")
                seen = now
        except KeyboardInterrupt:
            log.info("Watch stopped.")

    def _snapshot(self) -> dict[str, tuple[float, int]]:
        snap = {}
        for p in self.inbox.iterdir():
            if p.is_file():
                st = p.stat()
                snap[str(p)] = (st.st_mtime, st.st_size)
        return snap


def _fmt_signals(signals: list[Signal]) -> str:
    return "; ".join(f"{s.name}{s.evidence[:2]}" for s in signals)


def _summary(s: dict, out: Path, total: int) -> None:
    log.info("")
    log.info("════════ SUMMARY (%s) ════════", s["mode"])
    log.info("Processed: %d | escalated: %d | LLM fallback: %d | invalid input: %d | cache hits: %d | "
             "LLM calls: %d | models: %s", s["processed"], s["escalated"], s["fallback"], s["invalid"],
             s["cache_hits"], s["llm_calls"], s["models"] or ("offline" if s["offline"] else "-"))
    if s["by_category"]:
        log.info("By category: %s", s["by_category"])
        log.info("By priority: %s", s["by_priority"])
    log.info("Outbox written: %s (%d entries, nothing was sent)", out, total)
