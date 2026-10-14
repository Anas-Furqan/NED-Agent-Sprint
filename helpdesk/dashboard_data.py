"""Data helpers for the dashboard (no Streamlit imports, so they can be unit-tested)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from .loader import is_email_file, load_email
from .policy import Policy, load_policy

PRIORITY_COLORS = {"high": "#dc2626", "medium": "#d97706", "low": "#16a34a"}
STEP_ORDER = ["Load", "Pre-check", "LLM", "Policy", "Timing", "Validate", "Decision"]


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
        t = traces.get(e["email_id"], {})
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


def next_free_id(inbox: Path) -> str:
    """Next id in the inbox's own numbering, e.g. MSG099 -> MSG100. Never reuses an existing id."""
    ids = []
    for p in Path(inbox).iterdir() if Path(inbox).is_dir() else []:
        if is_email_file(p):
            loaded = load_email(p)
            ids.append(loaded.email.id if loaded.email else p.stem)
    numbered = [m.groups() for i in ids if (m := re.fullmatch(r"([A-Za-z]+)(\d+)", i))]
    if not numbered:
        prefix, width, n = "E", 3, 0
    else:
        prefix = Counter(p for p, _ in numbered).most_common(1)[0][0]
        nums = [d for p, d in numbered if p == prefix]
        width, n = max(len(d) for d in nums), max(int(d) for d in nums)
    taken = set(ids)
    while True:
        n += 1
        candidate = f"{prefix}{n:0{width}d}"
        if candidate not in taken and not (Path(inbox) / f"{candidate}.json").exists():
            return candidate


def save_new_email(inbox: Path, from_name: str, from_email: str, subject: str, body: str,
                   received_at: str = "") -> Path:
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    eid = next_free_id(inbox)
    if not received_at:
        received_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    data = {"id": eid, "from_name": from_name.strip(), "from_email": from_email.strip(),
            "subject": subject.strip(), "body": body.strip(), "received_at": received_at.strip()}
    path = inbox / f"{eid}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_uploaded(inbox: Path, name: str, content: bytes) -> Path:
    """Store an uploaded file under a safe, unused name (never overwrites)."""
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(name).stem)[:40] or "upload"
    suffix = Path(name).suffix.lower() if Path(name).suffix.lower() in (".json", ".txt", ".eml") else ".json"
    path, i = inbox / f"{stem}{suffix}", 1
    while path.exists():
        path, i = inbox / f"{stem}_{i}{suffix}", i + 1
    path.write_bytes(content)
    return path


def run_agent_stream(args: list[str], timeout: float = 300):
    """Run agent.py as a subprocess and yield its log lines live. Stops after `timeout` seconds."""
    proc = subprocess.Popen([sys.executable, "-u", "agent.py", *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    start = time.time()
    try:
        for line in proc.stdout:
            yield line.rstrip("\n")
            if time.time() - start > timeout:
                proc.kill()
                yield f"[stopped: exceeded {int(timeout)}s timeout]"
                break
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    yield f"[agent exited with code {proc.returncode}]"
