# Competition demo guide

## Before the event

1. Install dependencies and run `python -m pytest` and `ruff check .`.
2. Replace/approve `policies/company_policy.md` and keep its path in `.env`.
3. Add the dedicated Gmail account as an OAuth test user, connect, and confirm the profile loads.
4. Send fresh benign, refund, legal, and security test emails to the account.
5. Run Live Sync, confirm eligible items appear as Gmail drafts, and confirm nothing was sent or marked
   read. Repeat Sync and verify draft counts do not increase.
6. Confirm Gemini model access. Keep Demo mode as the credential/network fallback.

## Five-minute demonstration

1. Open **Demo mode** and point out the visible mode badge. Select Sync Now. Show ordinary billing,
   missing refund information, technical, angry/non-dangerous, legal, security/privacy, injection,
   ambiguous, empty/malformed, duplicate, LLM-failure fallback, and the visibly failed synthetic Gmail
   case. Demo failures are explicit simulations and make no external API calls.
2. In Inbox, open a normal item: show grounded reasoning, non-calibrated confidence, relevant policy,
   and the label **Local preview only**.
3. Open the legal/security/injection cases in Escalations. Explain the deterministic evidence and mark
   one reviewed with an explicit note.
4. Select **Live mode**, connect Gmail, then Sync Now. Show the fetched unseen message and unread state.
5. Open Drafts and distinguish the confirmed Gmail draft ID from a preview. Open Gmail Drafts manually;
   there is deliberately no send control in the app.
6. Sync again. Show unchanged draft count and the duplicate/idempotency audit event.
7. Show Diagnostics and Settings without exposing any credential values.

## Failure fallback

- If Gemini is unavailable, continue with the visibly marked limited fallback and show that ambiguous
  cases become manual review.
- If Gmail/OAuth/network is unavailable, stay in Demo mode. Do not imply the fixtures are live. Show the
  last sanitized failure event and the already-tested mocked integration path.
- If the token expired, remove local `credentials/token.json`, reconnect, and complete browser consent.
- Never paste a key/token on screen, relax scopes, or send a draft to rescue the demo.

## Judge-introduced unseen email

Keep query `in:inbox newer_than:30d`, use Sync Now, locate it by sender/subject, explain the contextual
classification and deterministic overrides, and show either a saved draft or a clear escalation/no-draft
reason. The implementation does not classify by fixture IDs or exact challenge text.
