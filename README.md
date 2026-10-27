# Student Helpdesk Triage Agent (Agent Sprint, Engineer's Code 2026)

`python agent.py` reads every email in `data/inbox/`. For each one it decides a category,
priority and escalation, finds the relevant rules in `data/policy.md`, drafts a policy-compliant
reply, and writes everything to `outbox.json` in the exact format of `sample_outbox.json`.
**Nothing is ever sent.** Every step is logged with its reason, and a per-email decision trace
(`run_trace.json`) feeds a live dashboard.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then edit .env
```

`.env` keys (read with python-dotenv; real environment variables also work; the key is never printed or logged):

| key | meaning |
|---|---|
| `GEMINI_API_KEY` | your Gemini API key |
| `GEMINI_MODEL` | main model, default `gemini-3-flash-preview` |
| `GEMINI_FALLBACK_MODELS` | comma list tried in order when the main model is unavailable / out of quota |
| `AGENT_DELAY_SECONDS`, `AGENT_MAX_ATTEMPTS` | optional pacing and retries (default 2 s, 4 attempts) |

`check_env.py` (from the kit) reads only environment variables. To use it, set
`$env:GEMINI_API_KEY="..."` in that terminal first.


## Setup (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit .env
```

On macOS/Linux, set the key for `check_env.py` with `export GEMINI_API_KEY="..."`.



## Running the agent

```powershell
python agent.py                  # all emails; re-uses cached decisions; rewrites outbox.json
python agent.py --only-new       # only new/changed emails, merged into outbox.json
python agent.py --file data/inbox/E021.json   # one file, merged into outbox.json
python agent.py --watch          # live: polls data/inbox every 3 s, processes new files (Ctrl+C stops)
python agent.py --fresh          # ignore the cache, call the LLM again for everything
python agent.py --offline        # no LLM at all: transparent rule-based fallback
python agent.py -v               # also print reply drafts
python agent.py --inbox tests/fixtures/edge --out some/dir/outbox.json   # any folder, any output
```

**New emails:** drop any `.json` (`id, from_name, from_email, subject, body, received_at`),
`.txt` or `.eml` file into `data/inbox/`. No code changes are needed. Broken, empty or odd files
never crash the run: they get an escalated placeholder entry that says what was wrong. Other files
(for example a README) are skipped with a log line.

Outputs:
- `outbox.json`: list sorted by `email_id`, exactly the keys/types/order of `sample_outbox.json`, written atomically.
- `run_trace.json`: per-email steps, signals, model, retries, cache hit, validation, timing, duration; plus run history.
- `logs/agent_run.log`: full log (appended per run).

## Dashboard

```powershell
streamlit run dashboard.py
```

Tabs: **Overview** (KPIs, category / priority / escalation-rule charts), **Escalations** ("needs
a human" queue with routing read from the policy text, e.g. ESC-2 → Harassment Committee),
**Inbox & Details** (search and filters, original email next to the reply draft, rule text for every
cited code, and the step-by-step "how the agent decided" timeline), **Live Run** (process new emails,
type or upload a test email and process it on the spot, reprocess all behind a confirmation),
**Run History**. The dashboard never shows the key and has no send button.

## Tests

```powershell
python -m pytest           # 63 tests, LLM always mocked or offline
python -m ruff check .
```

They cover policy parsing, schema validation, escalation that the model cannot lower, invented
codes dropped, fallback when the LLM raises or quota runs out mid-run, caching and cache
invalidation on policy change, `--only-new` merging, output format vs `sample_outbox.json`, 35
edge-case fixtures in `tests/fixtures/edge/`, dashboard helpers plus a headless dashboard render, and
an anti-hardcoding audit that fails if any sample email id, sender name or subject appears in the source.

## How it works

```
file -> loader (JSON/.txt/.eml, encodings, missing fields, quoted threads, truncation)
     -> deterministic pre-checks (regex, English + Roman Urdu + Urdu)      -> hints
     -> cache lookup (sha256 of file + policy.md + prompt version)
     -> ONE Gemini call: full policy + hints + email as UNTRUSTED DATA, strict JSON schema
     -> guardrails in code: drop unknown codes, add escalations, overdue timing, priority minimums
     -> reply validation: repair in code / regenerate once / safe template
     -> confidence -> outbox.json + run_trace.json + log
```

**Why an LLM plus code guardrails.** The LLM is good at understanding messy, multi-issue, Roman
Urdu or Urdu emails and writing a natural reply. Code is better at rules that must never fail. So
the model proposes and code enforces. Escalation can only be added by code, never removed. Policy
codes the model invents are dropped and logged. Every reply is checked before it is saved.

**Policy retrieval.** `policy.md` is parsed at runtime into sections and rule codes (any line
like `ABC-1:`). No policy text or code is hardcoded. The full policy goes to the model, and every
returned code is checked against the parsed set. Response windows (48 hours, 5 working days...)
and escalation routes ("escalate ... to the Harassment Committee") are read from the rule wording.
Adding a rule such as `FEE-5` to policy.md works without code changes and invalidates the cache.

**Categories.** `fee, hostel, academic, technical`, plus `wellbeing, harassment, security, spam,
auto_reply, unclear, other`. Multi-issue emails get one primary category, the others are listed
in `agent_notes`, and every issue is answered in the same reply.

**Priority scheme** (one place: `helpdesk/priority.py`; code enforces minimums):
- `high`: safety hazard, harassment, medical/wellbeing, legal/media threat, suspicious message,
  outage affecting many users, or an administrative deadline (fee due date, last date,
  application) within about 48 hours.
- `medium`: blocked process (locked account, missing voucher), overdue complaint, money matters,
  academic-integrity requests, coursework deadlines (quiz/exam). Escalated emails are never `low`.
- `low`: informational questions, thank-you notes, spam, auto-replies, unclear messages.

**Escalation (deterministic, can only add).**

| Signal (English / Roman Urdu / Urdu patterns) | Rule found in policy |
|---|---|
| sparks, exposed wire, fire, smoke, structural cracks, *aag, karant* | hazard rules (HOSTEL-3, ESC-1) |
| harassment, stalking, "keeps following me", threats, *tang karna, dhamki* | ESC-2 |
| fainted, not responding, hopelessness, self-harm, *jeene ka koi faida nahi* | ESC-3 |
| lawyer, court, media/news threat, *wakeel* | ESC-4 |
| "ignore previous instructions", role tags (`SYSTEM:`), base64-encoded instructions, "the manager said to approve", requests for another student's data, links asking for passwords, in body, subject or sender name | ESC-6 |
| outage + "whole class / many students" | TECH-2, ESC-5 |
| elapsed time in the email > the rule's response window | ESC-5 (+ e.g. HOSTEL-2) |
| bribery, grade change, leaked papers | human review |

Context matters. Strong patterns are enforced. Weak words ("scared", "court", "password") and
figurative phrases ("this exam is killing me") only go to the model as hints. Negations like
"there is no fire" are ignored. `escalation_reason` is one sentence that names the rule(s) and
who it routes to. Details stay in `agent_notes` and the trace.

**Time reasoning.** "Now" is the email's `received_at`, not the machine clock, so runs are
reproducible. Durations ("25 days ago", "for 5 days", "since Monday", "do hafte pehle") are
matched per sentence to the rule that sentence is about and compared with that rule's window
(working days counted Mon-Fri). A missing or unreadable `received_at` falls back to the file's
modified time, and the notes say so.

**Reply validation.** Under 150 words. Addresses the student by name (an injected or odd sender
name is replaced by "Student"). Signs off "Student Helpdesk". Mentions a policy code when one
applies. Same language as the email (English, Roman Urdu, or Urdu script). No promises the
policy does not allow. Harassment replies never suggest confronting the person and acknowledge
confidentiality when asked. Wellbeing replies point to the campus clinic / Student Counseling Cell.
No links, no secrets, no other people's email addresses.

**Prompt-injection defense.** Email content is wrapped and labeled as untrusted data, and the
prompt says never to follow it. Code flags injection patterns (ESC-6) whatever the model says. The
fallback strips the injected sentences and still answers the genuine part of the request.

**Reliability.** temperature 0 and a fixed seed. Retries with backoff on 429/503/timeouts/invalid
JSON (max 4 attempts). Sloppy model output is repaired ("high" as confidence, missing keys, unknown
categories). When a model is unavailable, out of daily quota or overloaded, the agent switches to
the next one in `GEMINI_FALLBACK_MODELS`. If every model fails, the rule-based fallback runs
(keyword category → policy section → best rule by IDF word overlap → template reply quoting the
real rule): `agent_notes` says `FALLBACK`, confidence is capped at 0.4, and it escalates when
unsure. The run always finishes and writes `outbox.json`.

**Caching.** Decisions are cached in `.cache/`, keyed on sha256(file bytes + policy.md + prompt
version), so re-running is instant and costs no quota. Cache hits are logged (`⚡ cache hit`) and
counted. `--fresh` bypasses the cache. Fallback results are never cached.

**Confidence.** Model confidence (capped at 0.95) minus penalties for dropped invented codes,
code repairs, a needed regeneration, an escalation the model missed, truncated or re-decoded
input, unclear or out-of-scope category, and fallback (capped at 0.4).

## Project layout

```
agent.py                  CLI entry point (modes: all / --only-new / --file / --watch / --fresh / --offline)
dashboard.py              Streamlit control room
helpdesk/loader.py        robust input loading
helpdesk/policy.py        policy.md parser (sections, codes, windows, routes, retrieval)
helpdesk/signals.py       deterministic risk signals and message kinds
helpdesk/timing.py        elapsed time vs policy windows, deadlines
helpdesk/priority.py      the priority scheme
helpdesk/prompts.py       system prompt + untrusted-data framing
helpdesk/llm.py           Gemini client: schema, retries, model rotation, output repair
helpdesk/validate.py      reply checks and repairs, language detection
helpdesk/fallback.py      rule-based fallback and templates
helpdesk/cache.py         decision cache, atomic writes
helpdesk/pipeline.py      per-email pipeline, trace, run modes
helpdesk/dashboard_data.py dashboard helpers (tested without a browser)
tests/                    pytest suites; fixtures/ (unseen emails) and fixtures/edge/ (35 edge cases)
make_submission.py        builds the zip from git-tracked files, refuses if a key is found
```

## Limitations

- Regex signals can miss unusual phrasing. The LLM is the main detector; the regex is a safety net.
- Roman Urdu detection is a word list, so mixed emails count as English unless Urdu dominates.
- Durations without a reference ("a while ago") are not used for overdue checks.
- A past emergency ("there was a fire yesterday, it's out now") is still treated as a hazard and escalated: deliberately conservative.
- In our test run, the model returned empty output for a 6,000-word repetitive email. The agent fell back to rules for it, and the result was still correct.
- Fallback replies are templates: correct, but less natural than LLM replies. The fallback cannot read Urdu-script text beyond a few keywords.
- The policy's Counseling Cell contact details are a placeholder, so replies never invent numbers.
