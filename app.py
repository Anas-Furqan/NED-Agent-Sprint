from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from pydantic import ValidationError

from ned_agent.classifier import GeminiProvider
from ned_agent.config import Settings, load_settings
from ned_agent.dashboard import filter_messages, gmail_drafts_url, metrics
from ned_agent.database import Database
from ned_agent.demo import demo_emails
from ned_agent.gmail import GmailClient, authenticate
from ned_agent.logging_utils import configure_logging, sanitize_error
from ned_agent.pipeline import Pipeline
from ned_agent.policy import load_policy

st.set_page_config(page_title="NED Support Agent", page_icon="✉", layout="wide")
st.markdown(
    """<style>
    :root { --navy:#10233f; --blue:#2563eb; --line:#dbe3ee; --muted:#64748b; }
    .stApp { background:#f7f9fc; color:var(--navy); }
    .block-container { max-width:1280px; padding-top:1.5rem; }
    h1,h2,h3 { color:var(--navy); letter-spacing:-.02em; }
    div[data-testid="stMetric"] { background:white; border:1px solid var(--line); border-radius:12px; padding:14px; box-shadow:0 2px 8px rgba(15,35,63,.04); }
    div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:10px; overflow:hidden; }
    .mode { display:inline-block; padding:5px 10px; border-radius:999px; background:#e8f0fe; color:#174ea6; font-weight:700; font-size:.78rem; }
    .muted { color:var(--muted); font-size:.9rem; }
    .saved { color:#087a55; font-weight:700; }
    .preview { color:#875a00; font-weight:700; }
    </style>""",
    unsafe_allow_html=True,
)


@st.cache_resource
def resources(database_path: str, policy_path: str):
    return Database(database_path), load_policy(policy_path)


@st.cache_resource
def sync_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="ned-agent-sync")


def provider_for(settings: Settings):
    if not settings.gemini_api_key:
        return None
    try:
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)
    except Exception:
        return None


def gmail_for(settings: Settings, force: bool = False):
    credentials = authenticate(
        settings.google_client_secrets_file, settings.token_file, settings.gmail_scopes, force=force
    )
    client = GmailClient.from_credentials(credentials)
    client.profile()
    return client


def live_sync(settings: Settings, db: Database, policy, gmail: GmailClient) -> dict[str, int]:
    emails = gmail.fetch_messages(settings.gmail_query, settings.batch_size)
    return Pipeline(db, policy, settings.categories, provider_for(settings), gmail).run(
        emails, True
    )


def demo_sync(settings: Settings, db: Database, policy) -> dict[str, int]:
    # Demo stays deterministic and makes no external provider or Gmail calls.
    return Pipeline(db, policy, settings.categories, None, None).run(
        demo_emails(), create_live_drafts=False
    )


def render_overview(db: Database, source: str, gmail_connected: bool) -> None:
    rows = db.list_messages(source)
    totals = metrics(rows)
    cols = st.columns(6)
    labels = (
        ("Emails", "fetched"),
        ("Processed", "processed"),
        ("Pending", "pending"),
        ("Saved drafts", "drafts"),
        ("Escalations", "escalated"),
        ("Failures", "failed"),
    )
    for column, (label, key) in zip(cols, labels, strict=True):
        column.metric(label, totals[key])
    st.subheader("System status")
    a, b, c = st.columns(3)
    a.write(f"**Gmail:** {'Connected' if gmail_connected else 'Not connected / Demo'}")
    b.write(
        f"**Agent:** {'Ready' if st.session_state.get('syncing') is not True else 'Processing'}"
    )
    b.caption(
        "Fallback classifier active"
        if provider_for(st.session_state.settings) is None
        else "Gemini configured"
    )
    last = db.list_audit(source, 1)
    c.write(f"**Last activity:** {last[0]['created_at'] if last else 'No activity yet'}")
    st.subheader("Recent activity")
    events = db.list_audit(source, 12)
    if events:
        st.dataframe(
            events,
            hide_index=True,
            use_container_width=True,
            column_order=("created_at", "event_type", "detail", "level"),
        )
    else:
        st.info("Run a sync to populate activity.")


def render_inbox(db: Database, source: str, categories: tuple[str, ...]) -> None:
    rows = db.list_messages(source)
    a, b, c = st.columns([2, 1, 1])
    search = a.text_input("Search", placeholder="Sender, subject, or intent")
    statuses = ["All"] + sorted({row["status"] for row in rows})
    status = b.selectbox("Status", statuses)
    category = c.selectbox("Category", ["All", *categories])
    filtered = filter_messages(rows, search, status, category)
    page_size = 20
    pages = max(1, (len(filtered) + page_size - 1) // page_size)
    page = st.number_input("Page", 1, pages, 1)
    view = filtered[(page - 1) * page_size : page * page_size]
    columns = (
        "id",
        "sender",
        "subject",
        "category",
        "priority",
        "status",
        "received_at",
        "draft_status",
    )
    st.dataframe(view, column_order=columns, hide_index=True, use_container_width=True)
    if not filtered:
        return
    options = {f"#{row['id']} · {row['subject'][:70]}": row for row in filtered}
    selected = options[st.selectbox("Open message", options)]
    left, right = st.columns([3, 2])
    with left:
        st.subheader(selected["subject"])
        st.caption(
            f"From {selected['sender']} · {selected['received_at']} · {'Unread' if selected['unread'] else 'Read'}"
        )
        st.text(selected["body_preview"] or "No readable message body.")
    with right:
        st.subheader("Agent assessment")
        st.write(f"**{selected['category'] or 'Pending'} · {selected['priority'] or '—'}**")
        st.write(selected["reasoning"] or "Not classified yet.")
        st.progress(
            (selected["confidence"] or 0) / 100,
            text=f"Confidence {selected['confidence'] or 0}/100 (not a calibrated probability)",
        )
        st.write(
            "**Policy references:**",
            ", ".join(json.loads(selected["policy_references_json"] or "[]")) or "None",
        )
    st.subheader("Reply")
    if selected["reply_preview"]:
        st.markdown(
            "<span class='saved'>Saved in Gmail</span>"
            if selected["draft_status"] == "saved"
            else "<span class='preview'>Local preview only</span>",
            unsafe_allow_html=True,
        )
        st.text_area("Reply preview", selected["reply_preview"], height=180, disabled=True)
        st.caption(f"Gmail draft ID: {selected['gmail_draft_id'] or 'Not saved'}")
    else:
        st.info("No draft is permitted or available for this message.")


def render_escalations(db: Database, source: str) -> None:
    rows = db.list_escalations(source)
    severity = st.selectbox("Severity", ["All", "Critical", "High", "Medium", "Low"])
    status = st.selectbox("Review status", ["All", "open", "reviewed"])
    rows = [
        row
        for row in rows
        if (severity == "All" or row["severity"] == severity)
        and (status == "All" or row["status"] == status)
    ]
    st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        column_order=("id", "severity", "reason", "subject", "status", "created_at"),
    )
    if rows:
        selected = rows[
            st.selectbox(
                "Open case",
                range(len(rows)),
                format_func=lambda i: f"#{rows[i]['id']} · {rows[i]['subject']}",
            )
        ]
        st.write("**Why it triggered:**", selected["reason"])
        st.write("**Evidence:**", selected["evidence"] or "Contextual classification signal")
        st.write("**Recommended action:**", selected["recommended_human_action"])
        st.write("**Review history:**", selected["review_note"] or "Not reviewed")
        if selected["status"] == "open":
            note = st.text_input("Review note", key=f"review-{selected['id']}")
            if st.button("Mark reviewed", disabled=not note.strip()):
                db.review_escalation(selected["id"], note)
                db.audit(source, "escalation_reviewed", f"Escalation {selected['id']} reviewed")
                st.rerun()


def render_drafts(db: Database, source: str) -> None:
    rows = [
        row
        for row in db.list_messages(source)
        if row["reply_preview"] or row["draft_status"] != "none"
    ]
    st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        column_order=("id", "subject", "sender", "draft_status", "gmail_draft_id", "updated_at"),
    )
    st.info("This application never sends email. Review and send saved drafts manually in Gmail.")
    if source == "live":
        st.link_button("Open Gmail Drafts", gmail_drafts_url())
    if rows:
        selected = rows[
            st.selectbox("Preview", range(len(rows)), format_func=lambda i: rows[i]["subject"])
        ]
        st.text_area(
            "Draft body", selected["reply_preview"] or "No preview", height=220, disabled=True
        )


def render_diagnostics(db: Database, source: str, settings: Settings) -> None:
    st.subheader("Configuration health")
    st.write(
        "OAuth client file:",
        "Available" if settings.google_client_secrets_file.is_file() else "Missing",
    )
    st.write(
        "Gemini provider:",
        "Configured" if settings.gemini_api_key else "Not configured — rules fallback active",
    )
    st.write("Policy:", str(settings.policy_file))
    st.write("Database:", str(settings.database_path))
    st.subheader("Sanitized events")
    st.dataframe(db.list_audit(source, 200), hide_index=True, use_container_width=True)


def render_settings(db: Database, source: str, settings: Settings) -> None:
    with st.form("runtime-settings"):
        query = st.text_input("Inbox query", settings.gmail_query)
        interval = st.number_input(
            "Polling interval (seconds)", 15, 3600, settings.poll_interval_seconds
        )
        batch = st.number_input("Batch size", 1, 100, settings.batch_size)
        categories = st.text_area("Categories (one per line)", "\n".join(settings.categories))
        if st.form_submit_button("Apply for this session"):
            try:
                updated = Settings.model_validate(
                    {
                        **settings.model_dump(),
                        "gmail_query": query,
                        "poll_interval_seconds": interval,
                        "batch_size": batch,
                        "categories": tuple(
                            item.strip() for item in categories.splitlines() if item.strip()
                        ),
                    }
                )
                st.session_state.settings = updated
                st.success("Runtime settings applied. Update .env to keep them after restart.")
                st.rerun()
            except ValidationError as error:
                st.error(f"Invalid settings: {sanitize_error(error)}")
    st.caption(
        "Secrets are never displayed here. Edit .env to change credentials or model configuration."
    )
    st.code(f"GEMINI_MODEL={settings.gemini_model}", language="text")
    missing = settings.missing_live_requirements()
    if missing:
        st.warning("Live-mode requirements: " + "; ".join(missing))
    st.subheader("Clear local application data")
    confirm = st.checkbox(f"I understand this clears only local {source} records, never Gmail.")
    if st.button("Clear local data", disabled=not confirm):
        db.clear_local_data(source)
        st.success("Local records cleared. Gmail was not modified.")
        st.rerun()


def main() -> None:
    try:
        loaded_settings = load_settings()
        st.session_state.setdefault("settings", loaded_settings)
        settings = st.session_state.settings
        configure_logging(settings.log_level)
        db, policy = resources(str(settings.database_path), str(settings.policy_file))
    except (ValidationError, ValueError, OSError) as error:
        st.error(f"Startup configuration error: {sanitize_error(error)}")
        st.stop()
    st.session_state.setdefault("gmail", None)
    st.session_state.setdefault("syncing", False)
    st.session_state.setdefault("sync_future", None)
    st.session_state.setdefault("sync_source", None)
    st.title("Support Operations Agent")
    st.caption("Safe Gmail triage, policy-grounded drafts, and human escalation")
    top_left, top_right = st.columns([3, 1])
    with top_left:
        mode = st.radio("Operating mode", ["Demo", "Live"], horizontal=True)
    source = mode.lower()
    with top_right:
        st.markdown(f"<span class='mode'>{mode.upper()} MODE</span>", unsafe_allow_html=True)
    if mode == "Live" and st.session_state.gmail is None:
        if st.button("Connect Gmail", type="primary"):
            try:
                st.session_state.gmail = gmail_for(settings)
                db.audit("live", "gmail_connected", "Gmail OAuth connection verified")
                st.rerun()
            except Exception as error:
                st.error(sanitize_error(error))
        st.caption(
            "OAuth requests gmail.readonly to fetch messages and gmail.compose to save drafts. It does not request gmail.send."
        )
    elif mode == "Live":
        connect_col, disconnect_col = st.columns([1, 3])
        if connect_col.button("Reauthorize Gmail"):
            try:
                st.session_state.gmail = gmail_for(settings, force=True)
                db.audit("live", "gmail_reauthorized", "Gmail OAuth authorization refreshed")
                st.rerun()
            except Exception as error:
                st.error(sanitize_error(error))
        if disconnect_col.button("Disconnect this session"):
            st.session_state.gmail = None
            st.rerun()
    action_col, poll_col = st.columns([1, 3])
    future = st.session_state.sync_future
    if future is not None and future.done():
        completed_source = st.session_state.sync_source or source
        try:
            result = future.result()
            db.audit(completed_source, "sync_completed", json.dumps(result))
            st.toast(f"Sync completed: {result['completed']} processed")
            st.session_state.last_poll = time.time()
        except Exception as error:
            db.audit(completed_source, "sync_failed", sanitize_error(error), "error")
            st.error(f"Sync failed safely: {sanitize_error(error)}")
        finally:
            st.session_state.sync_future = None
            st.session_state.sync_source = None
            st.session_state.syncing = False
        future = None
    with action_col:
        sync_clicked = st.button("Sync Now", type="primary", disabled=future is not None)
    with poll_col:
        auto_poll = st.toggle("Periodic polling while this page is active", value=False)
    poll_due = st.session_state.pop("poll_due", False)
    start_sync = (
        sync_clicked
        or poll_due
        or (
            auto_poll
            and time.time() - st.session_state.get("last_poll", 0) >= settings.poll_interval_seconds
        )
    )
    if start_sync and future is None:
        if mode == "Live" and st.session_state.gmail is None:
            st.warning("Connect Gmail before live synchronization.")
        else:
            task = demo_sync if mode == "Demo" else live_sync
            args = (
                (settings, db, policy)
                if mode == "Demo"
                else (
                    settings,
                    db,
                    policy,
                    st.session_state.gmail,
                )
            )
            st.session_state.sync_future = sync_executor().submit(task, *args)
            st.session_state.sync_source = source
            st.session_state.syncing = True
            future = st.session_state.sync_future
    if future is not None:
        st.info("Synchronization is running in the background. This page remains usable.")

        @st.fragment(run_every="1s")
        def sync_watcher() -> None:
            active = st.session_state.get("sync_future")
            if active is not None and active.done():
                st.rerun()

        sync_watcher()
    if auto_poll:
        st.caption(
            f"Polling is active; use Sync Now for an immediate refresh. Interval: {settings.poll_interval_seconds}s"
        )

        @st.fragment(run_every=f"{settings.poll_interval_seconds}s")
        def polling_tick() -> None:
            if time.time() - st.session_state.get("last_poll", 0) >= settings.poll_interval_seconds:
                st.session_state.poll_due = True
                st.rerun()

        polling_tick()
    pages = st.tabs(
        ["Overview", "Inbox", "Escalations", "Drafts", "Activity & Diagnostics", "Settings"]
    )
    with pages[0]:
        render_overview(db, source, st.session_state.gmail is not None)
    with pages[1]:
        render_inbox(db, source, settings.categories)
    with pages[2]:
        render_escalations(db, source)
    with pages[3]:
        render_drafts(db, source)
    with pages[4]:
        render_diagnostics(db, source, settings)
    with pages[5]:
        render_settings(db, source, settings)


if __name__ == "__main__":
    main()
