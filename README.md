# NED Agent Sprint — Gmail Support Operations Agent

A competition-ready Streamlit application for **The Engineer's Code 2026, Module #2**. It reads
authorized Gmail messages, classifies support intent and risk, applies a trusted local policy,
creates eligible **Gmail drafts only**, and sends high-risk cases to a human escalation queue.

The app has two visibly separate modes:

- **Demo** uses deterministic synthetic fixtures and never calls Gmail.
- **Live** uses an explicitly authorized test Gmail account and can save drafts. It never sends,
  marks read, labels, archives, deletes, or otherwise modifies inbox messages.

## Architecture

```text
Gmail/Demo → MIME normalization → SQLite dedup/claim → classification → deterministic risk rules
           → trusted policy → reply generation → safety validation → draft reconciliation/create
           → transaction + audit history → Streamlit dashboard
```

`ned_agent/` contains small replaceable modules for configuration, Gmail, classification providers,
policy selection, security, escalation, persistence, and orchestration. SQLite records move through
`pending → processing → completed`, with failures retained for review or safe retry. Transactional
claims prevent overlapping workers from handling the same message. Agent-created drafts carry a stable
message marker: retries reconcile that marker before any new creation, and ambiguous create outcomes
are never blindly retried.

## Windows quick start

Python 3.11+ is recommended. From PowerShell in this repository:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process Bypass` for that terminal or
invoke `.venv\Scripts\python.exe` and `.venv\Scripts\streamlit.exe` directly.

Demo mode works without credentials. Do not paste credentials into chat, source files, or Streamlit
widgets. Edit the local `.env` and place OAuth JSON under `credentials/`; both are ignored by Git.

## Gmail OAuth setup

1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project.
2. Enable **Gmail API** under APIs & Services.
3. Configure the OAuth consent screen. For an app in **Testing**, add the competition Gmail account
   under **Test users**. Google testing-mode refresh tokens can expire (commonly after seven days), so
   authorize and rehearse close to the event.
4. Create **OAuth client ID → Desktop app**, download the JSON, and save it as
   `credentials/client_secret.json` (or update `GOOGLE_CLIENT_SECRETS_FILE`).
5. Start the app, select Live, and choose **Connect Gmail**. The installed-application flow opens a
   browser and stores a local `credentials/token.json` with restrictive permissions where supported.

The app requests only:

- `gmail.readonly`: list and retrieve messages without changing them.
- `gmail.compose`: create/manage drafts. The app intentionally does **not** request `gmail.send`.

To reauthorize, stop the app, delete only the local `credentials/token.json`, restart, and reconnect.
Do not delete the downloaded client JSON. If scopes or test users change, reauthorization is required.

## Gemini setup

Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/app/apikey), set
`GEMINI_API_KEY` in `.env`, and choose the available model with `GEMINI_MODEL`. This API key is an AI
provider credential; it is completely different from the Gmail OAuth client JSON and user token.
Without it, the app clearly labels and uses a limited rule fallback, routing ambiguous cases to manual
review.

## Configuration

`.env.example` documents every setting. The default Gmail query `in:inbox newer_than:30d` includes
new challenge emails without depending on unread state. Batch size is capped at 100. Categories are
comma-separated and can be changed without modifying pipeline code. Replace
`policies/company_policy.md` with organizer-approved policy before handling real customers.

## Run checks

```powershell
python -m pytest --cov=ned_agent --cov-report=term-missing
ruff check .
ruff format --check .
python -m compileall -q app.py ned_agent tests
streamlit run app.py --server.headless true
```

External Gmail and Gemini APIs are mocked in automated tests; tests never send email or require paid
calls. A real pre-event rehearsal remains necessary because credentials cannot be supplied by the
repository.

## Troubleshooting

- **OAuth client missing:** verify the `.env` path and that the file is Desktop-app OAuth JSON.
- **Access blocked / Error 403:** add the Gmail account as an OAuth test user and verify Gmail API is
  enabled in the same Cloud project.
- **Token expired or scope changed:** remove local `token.json` and reconnect.
- **Redirect/browser issue:** allow localhost redirects and complete the flow on the same machine.
- **Gemini unavailable:** confirm key/model access; the app safely falls back and records the provider.
- **Draft not created:** review the message status. Escalation-only and No Reply decisions intentionally
  disable draft creation. API errors remain failed/retryable rather than falsely completed.
- **Locked database:** close duplicate local app processes; SQLite WAL and a 30-second busy timeout
  protect normal overlapping dashboard activity.

Local message records may be cleared from Settings. That operation never changes Gmail.
