"""Helpdesk Triage Control Room.   Run: streamlit run dashboard.py

Reads outbox.json + run_trace.json (and results/edge_* for the Edge Case Lab). Can run the agent on new
emails. Nothing is ever sent. Paths can be overridden with HELPDESK_* environment variables (used by tests).
"""
from __future__ import annotations

import html
import os
import shlex
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from helpdesk import dashboard_data as dd
from helpdesk.pipeline import trace_path_for

ROOT = Path(__file__).resolve().parent
INBOX = Path(os.getenv("HELPDESK_INBOX", ROOT / "data" / "inbox"))
OUTBOX = Path(os.getenv("HELPDESK_OUTBOX", ROOT / "outbox.json"))
TRACE = trace_path_for(OUTBOX)
POLICY = Path(os.getenv("HELPDESK_POLICY", ROOT / "data" / "policy.md"))
EDGE_FIXTURES = Path(os.getenv("HELPDESK_EDGE_FIXTURES", ROOT / "tests" / "fixtures" / "edge"))
EDGE_OUTBOX = Path(os.getenv("HELPDESK_EDGE_OUTBOX", ROOT / "results" / "edge_outbox.json"))
EXTRA_ARGS = shlex.split(os.getenv("HELPDESK_AGENT_ARGS", ""), posix=os.name != "nt")  # e.g. "--offline" in tests
LOG = os.getenv("HELPDESK_LOG")  # separate variable: paths with spaces must not go through shlex
PCOLOR = dd.PRIORITY_COLORS
VIEWS = {"overview": "📊 Overview", "escalations": "🚨 Escalations", "inbox": "📥 Inbox & Details",
         "live": "⚡ Live Run", "edge": "🧪 Edge Case Lab", "history": "🕘 Run History"}

st.set_page_config(page_title="Helpdesk Triage Control Room", page_icon="🎓", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px;}
.hero {background: linear-gradient(120deg,#1e3a8a 0%,#2563eb 60%,#0ea5e9 100%); color:#fff;
       border-radius:16px; padding:16px 22px; margin-bottom:12px; display:flex; justify-content:space-between;
       align-items:center; flex-wrap:wrap; gap:12px;}
.hero h1 {font-size:1.5rem; margin:0; color:#fff;}
.hero p {margin:2px 0 0 0; opacity:.92; font-size:.9rem;}
.hero .meta {text-align:right; font-size:.8rem; opacity:.95;}
.pill {display:inline-block; padding:3px 10px; border-radius:999px; font-weight:700; font-size:.74rem;}
.pill.green {background:#dcfce7; color:#166534;} .pill.amber {background:#fef3c7; color:#92400e;}
.pill.grey {background:#e5e7eb; color:#374151;} .pill.red {background:#fee2e2; color:#991b1b;}
.kpi {background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:12px 14px; box-shadow:0 1px 2px rgba(0,0,0,.04);}
.kpi .v {font-size:1.7rem; font-weight:800; line-height:1.1; color:#111827;}
.kpi .l {font-size:.72rem; color:#6b7280; text-transform:uppercase; letter-spacing:.05em;}
.kpi.red {border-top:4px solid #dc2626;} .kpi.amber {border-top:4px solid #d97706;}
.kpi.green {border-top:4px solid #16a34a;} .kpi.blue {border-top:4px solid #2563eb;} .kpi.grey {border-top:4px solid #9ca3af;}
.card {background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:12px 14px; margin-bottom:10px;}
.card.high {border-left:6px solid #dc2626;} .card.medium {border-left:6px solid #d97706;} .card.low {border-left:6px solid #16a34a;}
.card h4 {margin:2px 0 4px 0; font-size:1rem; word-break:break-word;}
.muted {color:#6b7280; font-size:.82rem;}
.badge {display:inline-block; padding:2px 9px; border-radius:6px; font-size:.72rem; font-weight:700; color:#fff;
        text-transform:uppercase; margin-right:4px;}
.badge.high {background:#dc2626;} .badge.medium {background:#d97706;} .badge.low {background:#16a34a;}
.badge.pass {background:#16a34a;} .badge.check {background:#d97706;}
.chip {display:inline-block; padding:2px 8px; border-radius:999px; font-size:.74rem; margin:2px 4px 2px 0;
       background:#eef2ff; color:#3730a3; border:1px solid #c7d2fe; font-family:monospace;}
.chip.cat {background:#f1f5f9; color:#0f172a; border-color:#cbd5e1; font-family:inherit;}
.route {display:inline-block; padding:2px 8px; border-radius:6px; font-size:.75rem; background:#fff7ed; color:#9a3412;
        border:1px solid #fed7aa; margin:2px 4px 2px 0;}
.mail {background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:12px 14px; white-space:pre-wrap;
       font-size:.9rem; line-height:1.45; word-break:break-word; max-height:420px; overflow-y:auto;}
.mail.reply {background:#f0fdf4; border-color:#bbf7d0;}
.step {border-left:3px solid #cbd5e1; margin-left:10px; padding:4px 0 10px 16px; position:relative;}
.step:before {content:""; position:absolute; left:-8px; top:6px; width:13px; height:13px; border-radius:50%;
              background:#2563eb; border:2px solid #fff; box-shadow:0 0 0 1px #2563eb;}
.step.warn:before {background:#d97706; box-shadow:0 0 0 1px #d97706;}
.step.alert:before {background:#dc2626; box-shadow:0 0 0 1px #dc2626;}
.step b {font-size:.88rem;} .step .d {font-size:.83rem; color:#334155; white-space:pre-wrap; word-break:break-word;}
.empty {background:#fff; border:2px dashed #cbd5e1; border-radius:16px; padding:32px; text-align:center;}
</style>
""", unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def mtime(p: Path) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


@st.cache_data(show_spinner=False)
def load_dataset(outbox_path: str, inbox_path: str, policy_path: str, stamp: tuple):
    """Rows for one dataset (main inbox or edge fixtures). Never raises on missing/corrupt files."""
    outbox = dd.read_json(Path(outbox_path), [])
    trace = dd.read_json(trace_path_for(Path(outbox_path)), {"emails": {}, "runs": []})
    if not isinstance(trace, dict):
        trace = {"emails": {}, "runs": []}
    policy = dd.load_policy_safe(Path(policy_path))
    rows = dd.build_rows(outbox if isinstance(outbox, list) else [], trace, dd.inbox_emails(Path(inbox_path)), policy)
    corrupt = Path(outbox_path).exists() and not isinstance(outbox, list)
    return rows, trace, policy, corrupt


def stamp_for(outbox: Path, inbox: Path) -> tuple:
    files = sorted((p.name, p.stat().st_mtime) for p in inbox.iterdir()) if inbox.is_dir() else []
    return mtime(outbox), mtime(trace_path_for(outbox)), mtime(POLICY), tuple(files)


rows, trace, policy, corrupt = load_dataset(str(OUTBOX), str(INBOX), str(POLICY), stamp_for(OUTBOX, INBOX))
runs = trace.get("runs", []) if isinstance(trace, dict) else []
last = runs[-1] if runs else {}
pill, color = dd.status_pill(trace)
models = ", ".join(last.get("models", [])) or ("rule-based" if last.get("offline") else "-")
st.markdown(f"""
<div class="hero">
  <div><h1>🎓 Helpdesk Triage Control Room</h1>
  <p>AI agent: reads, classifies, applies policy, drafts, escalates. <b>Nothing is sent.</b></p></div>
  <div class="meta"><span class="pill {color}">{esc(pill)}</span><br>
  Last run: {esc(last.get('ended', '-'))} · mode: {esc(last.get('mode', '-'))}<br>Model: {esc(models)}</div>
</div>""", unsafe_allow_html=True)
if corrupt:
    st.error("outbox.json could not be read (corrupt or partial). Run `python agent.py` to rebuild it.")


def kpi(col, value, label, tone):
    col.markdown(f'<div class="kpi {tone}"><div class="v">{esc(value)}</div><div class="l">{esc(label)}</div></div>',
                 unsafe_allow_html=True)


def badge(p):
    return f'<span class="badge {esc(p)}">{esc(p)}</span>'


def chips(codes):
    return "".join(f'<span class="chip">{esc(c)}</span>' for c in codes) or '<span class="muted">no policy code</span>'


def empty_state():
    st.markdown("""<div class="empty"><h3>No results yet</h3><p>Run the agent first, then press Refresh:</p>
    <code>python agent.py</code><p class="muted">or use the ⚡ Live Run tab.</p></div>""", unsafe_allow_html=True)


def show_messages(msgs):
    for level, text in msgs:
        {"success": st.success, "info": st.info, "warning": st.warning, "error": st.error}[level](text)


def jump_to(email_id: str):
    st.query_params["view"] = "inbox"
    st.query_params["email"] = email_id
    st.cache_data.clear()
    st.rerun()


# ---------------- views ----------------
def view_overview():
    if not rows:
        return empty_state()
    k = dd.kpis(rows)
    c = st.columns(7)
    kpi(c[0], k["total"], "Processed", "blue")
    kpi(c[1], k["escalated"], "Escalated", "red")
    kpi(c[2], k["high"], "High priority", "red")
    kpi(c[3], f'{k["avg_conf"]:.2f}', "Avg confidence", "green")
    kpi(c[4], k["fallback"], "Fallback used", "amber")
    kpi(c[5], k["cache_hits"], "Cache hits", "grey")
    kpi(c[6], f'{k["avg_ms"]/1000:.1f}s' if k["avg_ms"] else "-", "Avg time / email", "grey")
    st.write("")
    df = pd.DataFrame(rows)
    a, b, cc = st.columns(3)
    cat = df.groupby("category").size().reset_index(name="emails")
    a.markdown("**Categories**")
    a.altair_chart(alt.Chart(cat).mark_arc(innerRadius=55).encode(
        theta="emails:Q", color=alt.Color("category:N", legend=alt.Legend(orient="bottom", columns=3)),
        tooltip=["category", "emails"]).properties(height=260), width="stretch")
    pr = df.groupby("priority").size().reindex(["high", "medium", "low"]).fillna(0).reset_index(name="emails")
    b.markdown("**Priority**")
    b.altair_chart(alt.Chart(pr).mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
        x=alt.X("priority:N", sort=["high", "medium", "low"], title=None), y=alt.Y("emails:Q", title=None),
        color=alt.Color("priority:N", scale=alt.Scale(domain=list(PCOLOR), range=list(PCOLOR.values())), legend=None),
        tooltip=["priority", "emails"]).properties(height=260), width="stretch")
    counts = dd.escalation_code_counts(rows, policy)
    cc.markdown("**Escalations by rule**")
    if counts:
        ec = pd.DataFrame({"code": list(counts), "emails": list(counts.values())})
        cc.altair_chart(alt.Chart(ec).mark_bar(color="#dc2626", cornerRadiusEnd=6).encode(
            y=alt.Y("code:N", sort="-x", title=None), x=alt.X("emails:Q", title=None),
            tooltip=["code", "emails"]).properties(height=260), width="stretch")
    else:
        cc.info("No escalations.")


def view_escalations():
    queue = sorted((r for r in rows if r["escalate"]),
                   key=lambda r: ({"high": 0, "medium": 1, "low": 2}.get(r["priority"], 3), r["email_id"]))
    st.markdown(f"### Needs a human · {len(queue)}")
    if not rows:
        return empty_state()
    if not queue:
        st.success("Nothing needs a human right now.")
    cols = st.columns(2)
    for i, r in enumerate(queue):
        routes = "".join(f'<span class="route">→ {esc(x)}</span>' for x in r["routes"])
        cols[i % 2].markdown(f"""<div class="card {esc(r['priority'])}">
  <div>{badge(r['priority'])}<span class="chip cat">{esc(r['category'])}</span> <span class="muted">{esc(r['email_id'])}</span></div>
  <h4>{esc(r['subject']) or '(no subject)'}</h4><div class="muted">from {esc(r['from_name']) or 'unknown sender'}</div>
  <p style="margin:8px 0">{esc(r['escalation_reason'])}</p><div>{chips(r['policy_refs'])}</div>
  <div style="margin-top:6px">{routes}</div></div>""", unsafe_allow_html=True)


def render_detail(r: dict, pol):
    left, right = st.columns(2)
    with left:
        st.markdown("#### 📨 Original email")
        st.markdown(f'<div class="muted"><b>From:</b> {esc(r["from_name"])} &lt;{esc(r["from_email"])}&gt;<br>'
                    f'<b>Subject:</b> {esc(r["subject"])}<br><b>Received:</b> {esc(r["received_at"])}</div>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="mail">{esc(r["body"]) or "(empty body)"}</div>', unsafe_allow_html=True)
        if r["load_notes"]:
            st.caption("Input notes: " + "; ".join(r["load_notes"]))
    with right:
        st.markdown("#### ✉️ Reply draft (not sent)")
        st.markdown(f'{badge(r["priority"])}<span class="chip cat">{esc(r["category"])}</span> confidence '
                    f'<b>{r["confidence"]:.2f}</b> · {"🚨 escalate" if r["escalate"] else "no escalation"}',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="mail reply">{esc(r["reply_draft"])}</div>', unsafe_allow_html=True)
        if r["escalate"]:
            st.error(r["escalation_reason"])
            if r["routes"]:
                st.markdown("".join(f'<span class="route">→ {esc(x)}</span>' for x in r["routes"]),
                            unsafe_allow_html=True)
        st.markdown(chips(r["policy_refs"]), unsafe_allow_html=True)
        for code in r["policy_refs"]:
            with st.expander(f"{code}: rule text from policy.md"):
                st.write(dd.rule_text(pol, code))
    with st.expander("🧠 How the agent decided", expanded=True):
        st.markdown(f"model **{r['model'] or ('rule-based fallback' if r['fallback'] else '-')}** · attempts "
                    f"**{r['attempts']}** · cache hit **{'yes' if r['cache_hit'] else 'no'}** · duration "
                    f"**{r['duration_ms'] if r['duration_ms'] is not None else '-'} ms**")
        if r["steps"]:
            st.markdown("".join(
                f'<div class="step {esc(s.get("status", "ok"))}"><b>{esc(s.get("n", ""))}. {esc(s.get("name", ""))}</b>'
                f'<div class="d">{esc(s.get("detail", ""))}</div></div>' for s in r["steps"]), unsafe_allow_html=True)
        else:
            st.info("No trace for this email yet (run the agent to record one).")
        st.markdown(f"**Agent notes:** {esc(r['agent_notes'])}", unsafe_allow_html=True)
        if r["llm_errors"]:
            st.caption("LLM retries / errors: " + "; ".join(map(str, r["llm_errors"]))[:600])


def view_inbox(data_rows=None, pol=None, key="main", extra_cols=None):
    data_rows = rows if data_rows is None else data_rows
    pol = policy if pol is None else pol
    if not data_rows:
        return empty_state()
    f1, f2, f3, f4, f5 = st.columns([3, 2, 2, 2, 2])
    query = f1.text_input("Search", placeholder="name, subject, text…", key=f"{key}_q")
    cats = f2.multiselect("Category", sorted({r["category"] for r in data_rows}), key=f"{key}_cat")
    pris = f3.multiselect("Priority", ["high", "medium", "low"], key=f"{key}_pri")
    langs = f4.multiselect("Language", sorted({r["language"] for r in data_rows}), key=f"{key}_lang")
    with f5:
        only_esc = st.checkbox("Escalated only", key=f"{key}_esc")
        only_fb = st.checkbox("Fallback only", key=f"{key}_fb")
    view = dd.filter_rows(data_rows, query, cats, pris, only_esc, only_fb, langs)
    st.caption(f"{len(view)} of {len(data_rows)} emails")
    if not view:
        return st.info("No emails match the filters.")
    table = pd.DataFrame([{
        "id": r["email_id"], "from": r["from_name"], "subject": r["subject"], "category": r["category"],
        "priority": r["priority"], "escalate": "🚨" if r["escalate"] else "", "confidence": r["confidence"],
        "language": r["language"], "source": "cache" if r["cache_hit"] else ("fallback" if r["fallback"] else "LLM"),
        **(extra_cols(r) if extra_cols else {}),
    } for r in view])
    event = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun",
                         selection_mode="single-row", key=f"{key}_table",
                         column_config={"confidence": st.column_config.ProgressColumn(
                             "confidence", min_value=0, max_value=1, format="%.2f")})
    ids = [r["email_id"] for r in view]
    focus = st.query_params.get("email") if key == "main" else None
    picked = event.selection.rows[0] if event is not None and getattr(event, "selection", None) and event.selection.rows else None
    default = ids[picked] if picked is not None else focus
    sel = st.selectbox("Open email", ids, index=ids.index(default) if default in ids else 0, key=f"{key}_open")
    st.markdown("---")
    render_detail(next(x for x in view if x["email_id"] == sel), pol)


def view_live():
    st.markdown("### ⚡ Live run")
    st.caption(f"Runs `agent.py` on this machine against `{INBOX.name}/`. Drafts only; nothing is sent. "
               "Test emails get LIVE-xxx ids.")
    base = ["--inbox", str(INBOX), "--out", str(OUTBOX), "--policy", str(POLICY), *EXTRA_ARGS,
            *(["--log", LOG] if LOG else [])]

    def stream(args, label, timeout=300):
        box, lines = st.empty(), []
        with st.status(label, expanded=True) as status:
            for line in dd.run_agent_stream(base + args, timeout=timeout):
                lines.append(line)
                box.code("\n".join(lines[-30:]), language=None)
            msgs = dd.summarize_run(lines)
            failed = any(level == "error" for level, _ in msgs)
            status.update(label=label + (" — finished with problems" if failed else " — done"),
                          state="error" if failed else "complete")
        show_messages(msgs)
        st.cache_data.clear()
        return not failed

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Process new emails**")
        st.caption("Only new or changed files in the inbox; cached decisions are reused.")
        offline = st.toggle("Offline mode (no LLM)", value=False, key="live_offline")
        if st.button("▶ Process new emails", type="primary", key="live_process"):
            stream(["--only-new"] + (["--offline"] if offline else []), "Processing new emails")
        st.markdown("**Reprocess everything**")
        sure = st.checkbox("I understand this calls the LLM for every email (uses quota)", key="live_sure")
        if st.button("⟳ Reprocess all", disabled=not sure, key="live_reprocess"):
            stream(["--fresh"] + (["--offline"] if offline else []), "Reprocessing all emails", timeout=900)
        st.markdown("**Clear live test emails**")
        live_files = sorted(p.name for p in INBOX.glob(f"{dd.LIVE_PREFIX}*")) if INBOX.is_dir() else []
        st.caption(f"{len(live_files)} test email(s): {', '.join(live_files[:6]) or 'none'}")
        confirm = st.checkbox("Yes, delete the LIVE-* test emails and their results", key="live_clear_ok")
        if st.button("🗑 Clear live test emails", disabled=not (confirm and live_files), key="live_clear"):
            res = dd.clear_live_emails(INBOX, OUTBOX, TRACE)
            st.success(f"Deleted {res['files']} file(s) and {res['outbox_removed']} outbox entr(y/ies).")
            st.cache_data.clear()
    with c2:
        st.markdown("**Test a new email**")
        with st.form("new_email", clear_on_submit=False):
            fn = st.text_input("From name", "", placeholder="e.g. Ayesha Khan", key="live_name")
            fe = st.text_input("From email", "", placeholder="e.g. ayesha.k@example.com", key="live_email")
            sj = st.text_input("Subject", key="live_subject")
            bd = st.text_area("Body", height=150, key="live_body", max_chars=dd.LIMITS["body"])
            ra = st.text_input("received_at (optional, ISO)", "", key="live_received")
            go = st.form_submit_button("💾 Save to inbox & process", key="live_submit")
        if go:
            path, errors = dd.save_new_email(INBOX, {"from_name": fn, "from_email": fe, "subject": sj,
                                                     "body": bd, "received_at": ra})
            if errors:
                for e in errors:
                    st.warning(e)
            else:
                st.success(f"Saved {path.name}")
                if stream(["--file", str(path)] + (["--offline"] if offline else []), f"Processing {path.name}"):
                    jump_to(path.stem)
        up = st.file_uploader("…or upload an email file (.json, .txt, .eml)", type=["json", "txt", "eml"],
                              key="live_upload")
        if up is not None and st.button("⬆ Save upload & process", key="live_upload_go"):
            path, errors, notes = dd.save_upload(INBOX, up.name, up.getvalue())
            for n in notes:
                st.caption(n)
            if errors:
                for e in errors:
                    st.error(f"Upload rejected: {e}")
            else:
                st.success(f"Saved {path.name}")
                if stream(["--file", str(path)] + (["--offline"] if offline else []), f"Processing {path.name}"):
                    jump_to(path.stem)


def view_edge():
    st.markdown("### 🧪 Edge Case Lab")
    st.caption("Unseen and deliberately unusual emails from `tests/fixtures/edge/`, checked against `_expected.json`. "
               "Kept separate from the main inbox KPIs.")
    from scripts.edge_report import evaluate

    erows, _, epol, ecorrupt = load_dataset(str(EDGE_OUTBOX), str(EDGE_FIXTURES), str(POLICY),
                                            stamp_for(EDGE_OUTBOX, EDGE_FIXTURES))
    if ecorrupt or not erows:
        st.info("No edge results yet. Run: `python agent.py --inbox tests/fixtures/edge --out results/edge_outbox.json` "
                "then `python scripts/edge_report.py`.")
        return
    ev = {r["email_id"]: r for r in evaluate(EDGE_FIXTURES, EDGE_OUTBOX)}
    passed = sum(r["status"] == "PASS" for r in ev.values())
    c = st.columns(4)
    kpi(c[0], len(ev), "Edge cases", "blue")
    kpi(c[1], passed, "PASS", "green")
    kpi(c[2], len(ev) - passed, "CHECK", "amber")
    kpi(c[3], sum(r["fallback"] for r in erows), "Fallback used", "grey")
    status = st.radio("Show", ["all", "PASS", "CHECK"], horizontal=True, key="edge_status")
    shown = [r for r in erows if status == "all" or ev.get(r["email_id"], {}).get("status") == status]
    view_inbox(shown, epol, key="edge", extra_cols=lambda r: {
        "result": ev.get(r["email_id"], {}).get("status", "-"),
        "what it tests": ev.get(r["email_id"], {}).get("case", ""),
    })


def view_history():
    st.markdown("### 🕘 Run history")
    if not runs:
        return st.info("No runs recorded yet. Run `python agent.py`.")
    hist = pd.DataFrame([{
        "run": r.get("run_id"), "mode": r.get("mode"), "started": r.get("started"), "ended": r.get("ended"),
        "processed": r.get("processed"), "escalated": r.get("escalated"), "fallback": r.get("fallback"),
        "invalid": r.get("invalid"), "cache hits": r.get("cache_hits"), "LLM calls": r.get("llm_calls"),
        "models": ", ".join(r.get("models", [])) or ("offline" if r.get("offline") else "-"), "avg ms": r.get("avg_ms"),
    } for r in runs[::-1]])
    st.dataframe(hist, hide_index=True, width="stretch")


RENDER = {"overview": view_overview, "escalations": view_escalations, "inbox": view_inbox, "live": view_live,
          "edge": view_edge, "history": view_history}

top = st.columns([5, 1, 1])
if top[2].button("↻ Refresh", width="stretch", key="refresh"):
    st.cache_data.clear()
    st.rerun()
focus_view = st.query_params.get("view")
if focus_view in RENDER:  # deep link (used to jump to a new email's details)
    if top[1].button("◀ All tabs", width="stretch", key="all_tabs"):
        st.query_params.clear()
        st.rerun()
    st.markdown(f"#### {VIEWS[focus_view]}")
    RENDER[focus_view]()
else:
    for tab, name in zip(st.tabs(list(VIEWS.values())), VIEWS, strict=True):
        with tab:
            RENDER[name]()
