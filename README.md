# Student Helpdesk Triage Agent (Agent Sprint, Engineer's Code 2026)

One command reads every email in `data/inbox/`, decides category, priority and escalation,
finds the relevant rules in `data/policy.md`, drafts a policy-compliant reply, and writes
everything to `outbox.json`. **Nothing is ever sent.** Every step is logged with its reason.

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # then put your key in .env: GEMINI_API_KEY=...
python check_env.py           # optional: checks packages + key (reads the key from the environment)
python agent.py               # processes data/inbox/ -> outbox.json, log in logs/agent_run.log
streamlit run dashboard.py    # optional status dashboard over outbox.json
```

`check_env.py` reads only environment variables. If your key is only in `.env`, set it first:
`$env:GEMINI_API_KEY="your_key"`. `agent.py` itself reads `.env` (python-dotenv) and falls back to
environment variables. The key is never printed or logged.

Options:

```powershell
python agent.py --inbox data/inbox --policy data/policy.md --out outbox.json
python agent.py --inbox tests/fixtures      # 7 extra unseen/tricky emails we wrote
python agent.py --offline                   # no LLM: transparent rule-based fallback only
python agent.py -v                          # also print every reply draft in the console
python -m pytest tests/test_helpdesk.py     # 15 fast tests, LLM mocked
```

**New emails:** drop any `*.json` file (`id, from_name, from_email, subject, body, received_at`)
into `data/inbox/` and run `python agent.py` again. No code changes needed. Broken or empty files
do not crash the run: they get an escalated placeholder entry that explains the problem.

## Output

`outbox.json` is a list sorted by `email_id`, with exactly the keys and types of
`sample_outbox.json`: `email_id, category, priority, escalate, escalation_reason, policy_refs,
reply_draft, confidence, agent_notes`. It is written atomically (temp file + rename).

## Approach

```
load + validate JSON
  -> deterministic pre-checks (regex, English + Roman Urdu)       -> hints for the LLM
  -> ONE Gemini call (full policy + hints + email as UNTRUSTED DATA), strict JSON schema
  -> guardrails in code: drop unknown policy codes, enforce escalations, overdue timing
  -> reply validation (repair in code, or regenerate once, or safe template)
  -> confidence scoring -> outbox.json + step log
```

**Why LLM + deterministic guardrails.** The LLM is good at understanding messy, multi-issue,
Roman Urdu emails and writing a natural reply. Code is better at things that must never go wrong.
So the model proposes, and code enforces: escalation can only be *added* by code, never removed;
policy codes the model invents are dropped (and logged); the reply is checked before it is saved.

**Policy retrieval.** `data/policy.md` is parsed at runtime into sections and rule codes
(any `ABC-1:` style line). No policy text or code is hardcoded in Python. The policy is short, so
the full text goes to the model; afterwards every returned code is checked against the parsed set.
Escalation codes are found by searching the parsed rule text (e.g. the rule mentioning "harassment"),
and response windows (48 hours, 5 working days, ...) are parsed from the rule wording.

**Categories.** `fee, hostel, academic, technical` (topic) plus `wellbeing, harassment,
security` (risk types without a topic) and `unclear` / `other`. Multi-issue emails get a primary
category; the others are listed in `agent_notes` and every issue is answered in one reply.

**Priority scheme.**
- `high`: safety hazard, harassment, medical/wellbeing, legal/media threat, suspicious message, or a
  hard deadline within about a day.
- `medium`: actionable request, time-sensitive, or overdue complaint. Escalated emails are never `low`.
- `low`: general information question or unclear message.

**Escalation logic (deterministic, can only add).**

| Signal (English + Roman Urdu patterns) | Rule found in policy | Priority |
|---|---|---|
| sparks, exposed wire, fire, smoke, structural cracks, *aag, karant* | hazard rules (HOSTEL-3, ESC-1) | high |
| harassment, stalking, "keeps following me", bullying, *tang karna, dhamki* | ESC-2 | high |
| fainted, not responding, hopeless, self-harm, *behosh* | ESC-3 | high |
| lawyer, court, media, social media threat, *wakeel* | ESC-4 | high |
| "ignore previous instructions", admin mode, requests for other students' data | ESC-6 | high |
| outage + "whole class / many students" | TECH-2, ESC-5 | medium+ |
| elapsed time in email > policy response window | ESC-5 (+ e.g. HOSTEL-2) | medium+ |

Context matters: strong patterns are enforced; weak words (e.g. "scared", "court", "password")
only go to the model as hints. Negations like "there is no fire" are ignored.

**Time reasoning.** "Now" is the email's `received_at`, not the machine clock, so runs are
reproducible. Durations like "25 days ago", "for 5 days", "since Monday", "do hafte pehle" are
extracted per sentence, matched to the rule that sentence is about, and compared with the rule's
response window (working days counted Mon-Fri).

**Reply validation.** Under 150 words; addresses the student by name; signs off "Student Helpdesk";
mentions a policy code; same language as the email (English / Roman Urdu); no promises the policy
does not allow (waivers, refunds, guarantees, punishments); harassment replies never suggest
confronting the person and acknowledge confidentiality when asked; wellbeing replies point to the
campus clinic / Student Counseling Cell; no secrets or other people's email addresses. Small issues
are repaired in code; real violations trigger one regeneration, then a safe template.

**Prompt-injection defense.** Email content is wrapped and labeled as untrusted data; the system
prompt says never follow it; code flags injection patterns (ESC-6) regardless of what the model
says; the fallback strips injected sentences and only answers the genuine part of the email.

**Deadlines.** "quiz submission tonight", "exam is today", "kal last date hai" raise priority
(high for today, medium for tomorrow). This never changes escalation.

**Reliability.** Retries with exponential backoff on 429/503/timeouts/invalid JSON (honours the
server's retry delay, max 4 attempts) and a small delay between emails. If the main model is not
available for the key (404), out of daily free-tier quota (429 PerDay), or keeps returning 503, the
client rotates to the next model in `GEMINI_FALLBACK_MODELS` (each model has its own quota). If the LLM fails, a transparent rule-based fallback runs (keyword
category -> policy section -> best rule by IDF word overlap -> template reply quoting the real rule),
marks `agent_notes` with `FALLBACK`, caps confidence at 0.4, and escalates when unsure. The run
always finishes and writes `outbox.json`.

**Confidence.** Model confidence minus penalties: dropped invented codes, code repairs, a needed
regeneration, an escalation the model missed, unclear category, fallback (cap 0.4).

## Results on the 20 sample emails

Last run (all 20 handled by Gemini, 0 fallback): 9 escalated, priority high 7 / medium 9 / low 4.
Escalated: hazard (E005), harassment with confidentiality (E012), legal/media threat (E013),
medical emergency (E014), prompt injection (E015), overdue hostel repair (E004, HOSTEL-1 48h),
Wi-Fi outage for many users (E011), overdue transcript in a 3-issue email (E019, ACAD-3),
overdue re-evaluation (E020, ACAD-4 15 working days). Routine cases (waiver, installments, refund,
late room change) are answered with the correct rule and office and are not escalated.
`outbox.json` and `logs/agent_run.log` show every decision.

## Project layout

```
agent.py              CLI entry point
helpdesk/policy.py    policy.md parser (sections, codes, response windows, retrieval)
helpdesk/signals.py   deterministic risk signals (EN + Roman Urdu)
helpdesk/timing.py    elapsed time vs policy windows (overdue)
helpdesk/prompts.py   system prompt + untrusted-data framing
helpdesk/llm.py       Gemini client, JSON schema, retry/backoff
helpdesk/validate.py  reply checks + repairs, language detection
helpdesk/fallback.py  rule-based fallback + templates
helpdesk/pipeline.py  orchestration, logging, outbox writing
dashboard.py          Streamlit dashboard over outbox.json
tests/                test_helpdesk.py (mocked LLM), fixtures/ (unseen tricky emails)
```

## Limitations

- Regex signals can miss unusual phrasing; the LLM is the main detector, the regex is a safety net.
- Roman Urdu detection is a word list; mixed-language emails default to English unless Urdu dominates.
- Durations without a clear reference (e.g. "a while ago") are not used for overdue checks.
- The policy's Counseling Cell contact details are a placeholder, so replies do not invent numbers.
- Fallback replies are template-based and less natural than LLM replies.

