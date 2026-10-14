"""Robust email loading: JSON, plain-text/.eml, odd encodings, missing fields, quoted threads.

Never raises. Every assumption it makes is recorded in `notes` so it shows up in agent_notes.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .schemas import Email

EMAIL_SUFFIXES = {".json", ".txt", ".eml"}
MAX_LLM_WORDS = 1500
QUOTE_MARKERS = re.compile(
    r"(?im)^\s*(on .{3,120} wrote:|-{2,}\s*original message\s*-{2,}|from:\s.+\nsent:\s|>.*$)"
)


@dataclass
class Loaded:
    email: Email | None
    problem: str = ""  # non-empty -> cannot be triaged normally
    notes: list[str] = field(default_factory=list)
    raw: bytes = b""
    newest: str = ""  # newest message only (quoted thread removed): used for all checks
    llm_body: str = ""  # newest message, truncated if huge: sent to the LLM


def is_email_file(path: Path) -> bool:
    # names starting with "." or "_" are metadata or hidden files (sidecars), never emails
    return path.is_file() and path.suffix.lower() in EMAIL_SUFFIXES and not path.name.startswith((".", "_"))


def decode(raw: bytes) -> tuple[str, str]:
    """Return (text, encoding note)."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "decoded as UTF-16"
    try:
        return raw.decode("utf-8-sig"), ""
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "file was not UTF-8; decoded as cp1252"


def parse_text_email(text: str) -> dict:
    """Plain-text / .eml: optional 'Header: value' lines, blank line, then body."""
    data: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines) and re.match(r"^[A-Za-z-]{2,20}:\s", lines[i]):
        key, _, value = lines[i].partition(":")
        data[key.strip().lower()] = value.strip()
        i += 1
    body = "\n".join(lines[i:]).strip()
    sender = data.get("from", "")
    m = re.match(r'\s*"?([^"<]*)"?\s*<([^>]+)>', sender)
    name, addr = (m.group(1).strip(), m.group(2).strip()) if m else ("", sender if "@" in sender else "")
    if not addr and sender and "@" not in sender:
        name = sender
    return {"from_name": name, "from_email": addr, "subject": data.get("subject", ""),
            "body": body, "received_at": data.get("date", "")}


HTML_HINT = re.compile(r"(?i)<(html|body|div|p|br|table|span|font|b|i|a)\b[^>]*>")
SIGNATURE = re.compile(r"(?im)^[ \t]*(--\s*$|sent from my \w+|get outlook for \w+|this email and any attachments? (is|are) "
                       r"confidential)")
ATTACHMENT = re.compile(r"(?i)\b(attached|attachment|screenshot|find attached|pfa|see the (image|picture|photo))\b")


def html_to_text(body: str) -> tuple[str, bool]:
    """Convert an HTML body to plain text (never rendered)."""
    if not HTML_HINT.search(body):
        return body, False
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", body)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|<p\b[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip(), True


def strip_signature(body: str) -> tuple[str, bool]:
    m = SIGNATURE.search(body)
    if m and body[: m.start()].strip():
        return body[: m.start()].strip(), True
    return body, False


def newest_message(body: str) -> tuple[str, bool]:
    """Cut quoted earlier messages ('On ... wrote:', '> ...', 'Original Message')."""
    m = QUOTE_MARKERS.search(body)
    if m and body[: m.start()].strip():
        return body[: m.start()].strip(), True
    return body, False


def dedupe_repeats(text: str) -> tuple[str, int]:
    """Drop sentences/lines that repeat earlier ones (copy-paste floods confuse the model)."""
    parts = re.split(r"(?<=[.!?\u06d4\n])\s*", text)
    seen, kept, removed = set(), [], 0
    for part in parts:
        norm = re.sub(r"\W+", " ", part).strip().lower()
        if len(norm.split()) >= 3 and norm in seen:
            removed += 1
            continue
        seen.add(norm)
        kept.append(part)
    return (" ".join(p.strip() for p in kept if p.strip()), removed) if removed else (text, 0)


def truncate_words(text: str, limit: int = MAX_LLM_WORDS, tail: int = 300) -> tuple[str, int]:
    """Keep the first `limit` and last `tail` words; returns (text, number of words cut)."""
    words = text.split()
    if len(words) <= limit + tail:
        return text, 0
    cut = len(words) - limit - tail
    return " ".join(words[:limit]) + f"\n[...truncated {cut} words...]\n" + " ".join(words[-tail:]), cut


def _normalize_received(value: str, path: Path, notes: list[str]) -> str | None:
    mtime = datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat()
    if not value:
        notes.append("no received_at: used file modified time")
        return mtime
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(str(value))
        except Exception:
            notes.append(f"unreadable received_at '{str(value)[:30]}': used file modified time")
            return mtime
    if dt.tzinfo:
        notes.append("received_at had a timezone; compared in its local time")
    dt = dt.replace(tzinfo=None)
    if dt > datetime.now().replace(microsecond=0) and (dt - datetime.now()).days >= 1:
        notes.append("received_at is in the future; used as given for elapsed-time checks")
    return dt.isoformat()


def load_email(path: Path) -> Loaded:
    try:
        raw = path.read_bytes()
    except Exception as exc:
        return Loaded(None, f"could not read file: {exc}")
    notes: list[str] = []
    if not raw.strip():
        return Loaded(None, "file is empty", raw=raw)
    text, enc_note = decode(raw)
    if enc_note:
        notes.append(enc_note)

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return Loaded(None, f"malformed JSON: {exc.msg} (line {exc.lineno})", notes, raw)
        if not isinstance(data, dict):
            return Loaded(None, "JSON is not an object", notes, raw)
    else:
        data = parse_text_email(text)
        notes.append(f"plain-text file ({path.suffix}) parsed as an email")

    known = {"id", "from_name", "from_email", "subject", "body", "received_at"}
    extra = sorted(set(data) - known)
    if extra:
        notes.append(f"ignored unknown fields {extra[:5]}")
    clean = {k: ("" if data.get(k) is None else data.get(k)) for k in known}
    if isinstance(clean["body"], (list, dict)):
        clean["body"] = json.dumps(clean["body"], ensure_ascii=False)
    for key in ("from_name", "from_email", "subject", "body"):
        clean[key] = str(clean[key]).replace("\x00", "").strip()

    eid = str(clean["id"]).strip()
    if not eid:
        eid = path.stem
        notes.append("no id: used file name")
    elif eid != path.stem:
        notes.append(f"id '{eid}' differs from file name '{path.name}'")
    if not clean["from_name"]:
        notes.append("no sender name: greeted as 'Student'")
    clean["id"] = eid
    clean["received_at"] = _normalize_received(clean["received_at"], path, notes)

    email = Email.model_validate(clean)
    if not email.body and not email.subject:
        return Loaded(email, "email has no subject or body", notes, raw)
    if not email.body:
        if len(email.subject.split()) >= 4:
            notes.append("empty body: triaged from the subject line")
            email = email.model_copy(update={"body": email.subject})
        else:
            return Loaded(email, "email body is empty", notes, raw)

    body, was_html = html_to_text(email.body)
    if was_html:
        notes.append("HTML body converted to plain text")
        email = email.model_copy(update={"body": body})
    newest, cut = newest_message(email.body)
    if cut:
        notes.append("quoted earlier messages ignored; triaged the newest message")
    newest, sig = strip_signature(newest)
    if sig:
        notes.append("signature / footer removed")
    if ATTACHMENT.search(newest):
        notes.append("mentions an attachment, but attachment content is not available to the agent")
    llm_body, repeats = dedupe_repeats(newest)
    if repeats:
        notes.append(f"{repeats} repeated sentences collapsed before the LLM call")
    llm_body, cut = truncate_words(llm_body)
    if cut:
        notes.append(f"very long email ({len(newest.split())} words): truncated for the LLM ({cut} words cut)")
    elif repeats and len(newest.split()) > MAX_LLM_WORDS:
        notes.append(f"very long email ({len(newest.split())} words) shortened (truncated) for the LLM")
    return Loaded(email, "", notes, raw, newest, llm_body)
