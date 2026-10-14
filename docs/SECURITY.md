# Security and privacy

## No-send guarantee

The integration exposes list/get Gmail methods and `drafts.create` only. There is no wrapper for
`messages.send` or `drafts.send`, no Send button, and no `gmail.send` scope. Replies require manual review
and sending in Gmail. The app never marks messages read, changes labels, archives, deletes, or follows
instructions/links found inside email.

## OAuth scopes and secrets

`gmail.readonly` is required to retrieve messages and `gmail.compose` to create drafts. Configuration
rejects `gmail.send` and the broad `mail.google.com` scope. OAuth tokens, client JSON, `.env`, databases,
logs, and Streamlit secrets are excluded from Git. Tokens live under `credentials/`, not SQLite, and are
chmod 0600 where supported. UI and audit errors are truncated and redact common secret fields.

## Untrusted email controls

Sender, subject, body, quoted text, HTML, and attachment metadata are untrusted data. Prompts place them
inside explicit data boundaries and instruct the provider not to execute or obey them. Attachments are
not processed. HTML is converted to text; active content is not rendered. Deterministic patterns detect
attempts to override instructions, obtain secrets, execute code, disable policy, or force sending.

Model output cannot bypass code-level legal, regulatory, security/privacy, safety, serious financial, or
human-request rules. These create explainable escalation records. Low-confidence or malformed provider
output falls back to limited rules and manual review. Generated text is checked for secrets, unsupported
refund approval, guarantees, commitments, and credential-harvesting language before any draft operation.

## Retention

SQLite stores metadata, a 4,000-character body preview, classifications, draft previews, errors, and
audit history for local operations. It does not store OAuth/API credentials or attachments. This bounded
preview enables judging and review but should be treated as customer data. Use Settings to clear each
source partition after the event, and delete the local ignored database when retention is no longer
required. Clearing local data never touches Gmail.

## Operational recommendations

- Use a dedicated test Gmail account with synthetic competition messages.
- Restrict filesystem access to the operator account and never screen-share `.env` or credentials.
- Replace the example policy with approved competition policy and record its SHA-256 in each result.
- Rehearse token validity shortly before the event; OAuth testing-mode tokens may expire.
- Treat every saved draft as requiring human approval in Gmail.
