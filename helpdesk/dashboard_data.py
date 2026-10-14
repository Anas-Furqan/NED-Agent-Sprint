"""Data helpers for the dashboard (no Streamlit imports, so they can be unit-tested)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from .loader import decode, html_to_text, is_email_file, load_email, parse_text_email
from .policy import Policy, load_policy

PRIORITY_COLORS = {"high": "#dc2626", "medium": "#d97706", "low": "#16a34a"}
STEP_ORDER = ["Load", "Pre-check", "LLM", "Policy", "Timing", "Validate", "Decision"]
ENTRY_DEFAULTS = {"category": "unclear", "priority": "medium", "escalate": False, "escalation_reason": "",
                  "policy_refs": [], "reply_draft": "", "confidence": 0.0, "agent_notes": ""}


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_policy_safe(path: Path) -> Policy | None:
    try:
        return load_policy(path)
    except Exception:
        return None


def inbox_emails(inbox: Path) -> dict[str, dict]:
    """email_id -> original email (from the inbox files) for the detail view."""
    out = {}
    if not Path(inbox).is_dir():
        return out
    for p in sorted(Path(inbox).iterdir()):
        if not is_email_file(p):
            continue
        loaded = load_email(p)
        if loaded.email:
            out.setdefault(loaded.email.id, loaded.email.model_dump() | {"file": p.name})
    return out


def build_rows(outbox: list[dict], trace_doc: dict, emails: dict[str, dict], policy: Policy | None) -> list[dict]:
    """Merge outbox entries with their trace and original email. Never fails on missing parts."""
    traces = (trace_doc or {}).get("emails", {})
    rows = []
    for e in outbox or []:
        if not isinstance(e, dict) or "email_id" not in e:
            continue
        e = {**ENTRY_DEFAULTS, **{k: v for k, v in e.items() if v is not None}}  # partial entries never crash
        e["email_id"] = str(e["email_id"])
        try:
            e["confidence"] = float(e["confidence"])
        except (TypeError, ValueError):
            e["confidence"] = 0.0
        t = traces.get(e["email_id"], {}) if isinstance(traces, dict) else {}
        src = t.get("email") or emails.get(e["email_id"]) or {}
        rows.append({
            **e,
            "from_name": src.get("from_name", ""), "from_email": src.get("from_email", ""),
            "subject": src.get("subject", ""), "body": src.get("body", ""),
            "received_at": src.get("received_at", ""),
            "language": t.get("language") or language_from_notes(e.get("agent_notes", "")),
            "fallback": bool(t.get("fallback")) or "FALLBACK" in e.get("agent_notes", ""),
            "cache_hit": bool(t.get("cache_hit")), "model": t.get("model") or "",
            "attempts": t.get("attempts", 0), "duration_ms": t.get("duration_ms"),
            "routes": t.get("routes") or routes_for(e, policy),
            "steps": t.get("steps", []), "signals": t.get("signals", []), "timing": t.get("timing", []),
            "validation": t.get("validation", {}), "load_notes": t.get("load_notes", []),
            "llm_errors": t.get("llm_errors", []), "run_id": t.get("run_id", ""),
        })
    return sorted(rows, key=lambda r: r["email_id"])


def language_from_notes(notes: str) -> str:
    m = re.search(r"language: (\w+)", notes or "")
    return m.group(1) if m else "english"


def routes_for(entry: dict, policy: Policy | None) -> list[str]:
    """Who an escalated email goes to, read from the policy text of its rule codes."""
    if not entry.get("escalate") or policy is None:
        return []
    return policy.routes(entry.get("policy_refs", [])) or ["Duty staff member"]


def filter_rows(rows: list[dict], query: str = "", categories=(), priorities=(), escalated_only=False,
                fallback_only=False, languages=()) -> list[dict]:
    q = (query or "").strip().lower()
    out = []
    for r in rows:
        if categories and r["category"] not in categories:
            continue
        if priorities and r["priority"] not in priorities:
            continue
        if escalated_only and not r["escalate"]:
            continue
        if fallback_only and not r["fallback"]:
            continue
        if languages and r["language"] not in languages:
            continue
        if q and q not in " ".join(str(r.get(k, "")) for k in
                                   ("email_id", "from_name", "subject", "body", "reply_draft", "agent_notes",
                                    "escalation_reason", "category")).lower():
            continue
        out.append(r)
    return out


def kpis(rows: list[dict]) -> dict:
    n = len(rows)
    durations = [r["duration_ms"] for r in rows if isinstance(r.get("duration_ms"), (int, float))]
    return {
        "total": n, "escalated": sum(r["escalate"] for r in rows),
        "high": sum(r["priority"] == "high" for r in rows),
        "avg_conf": round(sum(r["confidence"] for r in rows) / n, 2) if n else 0.0,
        "fallback": sum(r["fallback"] for r in rows), "cache_hits": sum(r["cache_hit"] for r in rows),
        "avg_ms": int(sum(durations) / len(durations)) if durations else 0,
    }


def escalation_code_counts(rows: list[dict], policy: Policy | None) -> dict[str, int]:
    """Escalation-rule codes (rules in the policy's escalation section) across escalated emails."""
    c = Counter()
    for r in rows:
        if not r["escalate"]:
            continue
        codes = [x for x in r["policy_refs"] if policy and "escalat" in policy.section_of(x).lower()]
        for code in codes or ["(other)"]:
            c[code] += 1
    return dict(sorted(c.items()))


def status_pill(trace_doc: dict) -> tuple[str, str]:
    runs = (trace_doc or {}).get("runs", [])
    if not runs:
        return "NO RUNS YET", "grey"
    last = runs[-1]
    if last.get("offline"):
        return "OFFLINE (rules only)", "grey"
    if last.get("fallback"):
        return f"LIVE · {last['fallback']} FALLBACK", "amber"
    return "LIVE (Gemini)", "green"


def rule_text(policy: Policy | None, code: str) -> str:
    if policy and code in policy.rules:
        return policy.rules[code].text
    return "(rule not found in current policy.md)"


LIVE_PREFIX = "LIVE-"
LIMITS = {"from_name": 100, "from_email": 150, "subject": 200, "body": 10000}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def existing_ids(inbox: Path) -> set[str]:
    ids = set()
    for p in Path(inbox).iterdir() if Path(inbox).is_dir() else []:
        if is_email_file(p):
            loaded = load_email(p)
            ids.add(loaded.email.id if loaded.email else p.stem)
            ids.add(p.stem)
    return ids


def next_free_id(inbox: Path, prefix: str = LIVE_PREFIX, width: int = 3) -> str:
    """Next id with the given prefix (LIVE-001, LIVE-002 ...). Never reuses an existing id or file name."""
    taken = existing_ids(inbox)
    nums = [int(m.group(1)) for i in taken if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", i))]
    n = max(nums, default=0)
    while True:
        n += 1
        candidate = f"{prefix}{n:0{width}d}"
        if candidate not in taken and not (Path(inbox) / f"{candidate}.json").exists():
            return candidate


def clean_fields(data: dict) -> tuple[dict, list[str]]:
    """Strip HTML, trim, enforce limits and a body. Returns (clean fields, errors)."""
    errors, out = [], {}
    for key, limit in LIMITS.items():
        value = str(data.get(key) or "").replace("\x00", "")
        value, _ = html_to_text(value)
        value = value.strip()
        if len(value) > limit:
            errors.append(f"{key.replace('_', ' ')} is too long ({len(value)} > {limit} characters)")
        out[key] = value[:limit]
    if not out["body"]:
        errors.append("body is required")
    if out["from_email"] and not EMAIL_RE.match(out["from_email"]):
        errors.append("from email does not look like an email address")
    received = str(data.get("received_at") or "").strip()
    if received:
        try:
            datetime.fromisoformat(received.replace("Z", "+00:00"))
        except ValueError:
            errors.append("received_at must be ISO format, e.g. 2026-10-14T10:30:00")
    out["received_at"] = received or datetime.now().replace(microsecond=0).isoformat()
    return out, errors


def save_new_email(inbox: Path, fields: dict) -> tuple[Path | None, list[str]]:
    """Validate and save a dashboard test email as LIVE-xxx.json (the file name never comes from user input)."""
    clean, errors = clean_fields(fields)
    if errors:
        return None, errors
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    eid = next_free_id(inbox)
    path = inbox / f"{eid}.json"
    path.write_text(json.dumps({"id": eid, **clean}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, []


def parse_upload(name: str, content: bytes) -> tuple[dict | None, list[str], list[str]]:
    """Uploaded .json/.txt/.eml -> (fields, errors, notes). The original id is never trusted."""
    notes: list[str] = []
    if not content.strip():
        return None, ["the file is empty"], notes
    if len(content) > 200_000:
        return None, ["the file is larger than 200 KB"], notes
    text, enc = decode(content)
    if enc:
        notes.append(enc)
    if Path(name).suffix.lower() in (".txt", ".eml"):
        data = parse_text_email(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, [f"invalid JSON: {exc.msg} (line {exc.lineno})"], notes
        if not isinstance(data, dict):
            return None, ["the JSON must be one email object, e.g. {\"subject\": ..., \"body\": ...}"], notes
        unknown = sorted(set(data) - {"id", "from_name", "from_email", "subject", "body", "received_at"})
        if unknown:
            notes.append(f"ignored unknown fields {unknown[:5]}")
        if not any(k in data for k in ("subject", "body")):
            return None, ["no subject or body field found (expected id, from_name, from_email, subject, body, "
                          "received_at)"], notes
    if data.get("id"):
        notes.append(f"uploaded id '{data['id']}' replaced by a new LIVE id")
    if not str(data.get("body") or "").strip() and str(data.get("subject") or "").strip():
        data["body"] = data["subject"]
    return data, [], notes


def save_upload(inbox: Path, name: str, content: bytes) -> tuple[Path | None, list[str], list[str]]:
    data, errors, notes = parse_upload(name, content)
    if errors:
        return None, errors, notes
    path, errors = save_new_email(inbox, data)
    return path, errors, notes


def clear_live_emails(inbox: Path, outbox: Path, trace: Path) -> dict:
    """Delete LIVE-* test emails and their outbox/trace entries (under the agent's lock)."""
    from .cache import atomic_write_json
    from .lock import FileLock

    with FileLock(Path(outbox).with_name(f".{Path(outbox).stem}.lock"), timeout=30):
        files = [p for p in Path(inbox).glob(f"{LIVE_PREFIX}*") if p.is_file()]
        ids = {p.stem for p in files}
        for p in files:
            p.unlink()
        entries = read_json(outbox, [])
        kept = [e for e in entries if not str(e.get("email_id", "")).startswith(LIVE_PREFIX)]
        if Path(outbox).exists():
            atomic_write_json(Path(outbox), kept)
        doc = read_json(trace, {})
        if doc.get("emails"):
            doc["emails"] = {k: v for k, v in doc["emails"].items() if not k.startswith(LIVE_PREFIX)}
            atomic_write_json(Path(trace), doc)
    return {"files": len(files), "outbox_removed": len(entries) - len(kept), "ids": sorted(ids)}


def stale_entries(inbox: Path, outbox: list[dict]) -> list[str]:
    """Outbox ids with no matching email file (e.g. a deleted demo email)."""
    ids = existing_ids(inbox)
    return [e["email_id"] for e in outbox if isinstance(e, dict) and e.get("email_id") not in ids
            and not any(e["email_id"].startswith(i + "-") for i in ids)]


AGENT = Path(__file__).resolve().parents[1] / "agent.py"


def run_agent_stream(args: list[str], timeout: float = 300):
    """Run agent.py as a subprocess and yield its log lines live; kill it after `timeout` seconds."""
    import queue
    import threading

    proc = subprocess.Popen([sys.executable, "-u", str(AGENT), *args], stdout=subprocess.PIPE,  # noqa: S603
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            cwd=str(AGENT.parent))
    lines: queue.Queue = queue.Queue()

    def pump():
        for line in proc.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    deadline = time.monotonic() + timeout
    while True:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            if time.monotonic() > deadline:
                proc.kill()
                yield f"[stopped: the agent took longer than {int(timeout)}s and was stopped]"
                break
            continue
        if line is None:
            break
        yield line
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    yield f"[agent exited with code {proc.returncode}]"


def summarize_run(lines: list[str]) -> list[tuple[str, str]]:
    """Turn agent log lines into short user-facing messages: (level, text), level in success/info/warning/error."""
    msgs: list[tuple[str, str]] = []
    text = "\n".join(lines)
    code = re.search(r"\[agent exited with code (-?\d+)\]", text)
    for m in re.finditer(r"model '([^']+)' (is [^-]+?) -> switching to '([^']+)'", text):
        msgs.append(("info", f"Model '{m.group(1)}' {m.group(2).strip()}: switched to '{m.group(3)}'."))
    if "Skipping model(s) out of daily quota" in text:
        m = re.search(r"-> using '([^']+)'", text)
        msgs.append(("info", f"Main model is out of daily quota today: using '{m.group(1) if m else 'fallback model'}'."))
    if "Offline mode" in text or "No GEMINI_API_KEY" in text:
        msgs.append(("warning", "Running in offline mode: rule-based fallback, no LLM."))
    elif "FALLBACK" in text:
        msgs.append(("warning", "The LLM was unavailable for some emails: they used the rule-based fallback "
                                "(marked FALLBACK in agent_notes)."))
    if "Another agent run is in progress" in text:
        msgs.append(("warning", "Another agent run (e.g. --watch) was busy; try again in a moment."))
    if "[stopped:" in text:
        msgs.append(("error", "The agent took too long and was stopped (timeout)."))
    if "Traceback" in text or (code and code.group(1) not in ("0", "3")):
        tail = "\n".join(line for line in lines[-15:] if not line.startswith("[agent exited"))
        msgs.append(("error", f"The agent stopped with an error. Last log lines:\n{tail}"))
    summary = re.findall(r"Processed: (\d+) \| escalated: (\d+).*?LLM calls: (\d+)", text)
    if summary and not any(level == "error" for level, _ in msgs):
        n, esc, calls = summary[-1]
        msgs.append(("success", f"Processed {n} email(s), {esc} escalated, {calls} LLM call(s)."))
    return msgs
