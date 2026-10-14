"""Helpdesk Triage Control Room.   Run: streamlit run dashboard.py

Reads outbox.json + run_trace.json. Can run the agent on new emails. Nothing is ever sent.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from helpdesk import dashboard_data as dd

OUTBOX = Path("outbox.json")
TRACE = Path("run_trace.json")
INBOX = Path("data/inbox")
POLICY = Path("data/policy.md")
PCOLOR = dd.PRIORITY_COLORS

st.set_page_config(page_title="Helpdesk Triage Control Room", page_icon="🎓", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1400px;}
.hero {background: linear-gradient(120deg,#1e3a8a 0%,#2563eb 60%,#0ea5e9 100%); color:#fff;
       border-radius:16px; padding:18px 24px; margin-bottom:14px; display:flex; justify-content:space-between;
       align-items:center; flex-wrap:wrap; gap:12px;}
.hero h1 {font-size:1.6rem; margin:0; color:#fff;}
.hero p {margin:2px 0 0 0; opacity:.9; font-size:.92rem;}
.hero .meta {text-align:right; font-size:.82rem; opacity:.95;}
.pill {display:inline-block; padding:3px 10px; border-radius:999px; font-weight:700; font-size:.75rem;
       letter-spacing:.03em;}
.pill.green {background:#dcfce7; color:#166534;} .pill.amber {background:#fef3c7; color:#92400e;}
.pill.grey {background:#e5e7eb; color:#374151;} .pill.red {background:#fee2e2; color:#991b1b;}
.kpi {background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:14px 16px;
      box-shadow:0 1px 2px rgba(0,0,0,.04);}
.kpi .v {font-size:1.9rem; font-weight:800; line-height:1.1; color:#111827;}
.kpi .l {font-size:.75rem; color:#6b7280; text-transform:uppercase; letter-spacing:.05em;}
.kpi.red {border-top:4px solid #dc2626;} .kpi.amber {border-top:4px solid #d97706;}
.kpi.green {border-top:4px solid #16a34a;} .kpi.blue {border-top:4px solid #2563eb;}
.kpi.grey {border-top:4px solid #9ca3af;}
.card {background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:14px 16px; margin-bottom:10px;}
.card.high {border-left:6px solid #dc2626;} .card.medium {border-left:6px solid #d97706;}
.card.low {border-left:6px solid #16a34a;}
.card h4 {margin:0 0 4px 0; font-size:1rem;}
.muted {color:#6b7280; font-size:.82rem;}
.badge {display:inline-block; padding:2px 9px; border-radius:6px; font-size:.72rem; font-weight:700;
        color:#fff; text-transform:uppercase; margin-right:4px;}
.badge.high {background:#dc2626;} .badge.medium {background:#d97706;} .badge.low {background:#16a34a;}
.chip {display:inline-block; padding:2px 8px; border-radius:999px; font-size:.74rem; margin:2px 4px 2px 0;
       background:#eef2ff; color:#3730a3; border:1px solid #c7d2fe; font-family:monospace;}
.chip.cat {background:#f1f5f9; color:#0f172a; border-color:#cbd5e1; font-family:inherit;}
.route {display:inline-block; padding:2px 8px; border-radius:6px; font-size:.75rem; background:#fff7ed;
        color:#9a3412; border:1px solid #fed7aa; margin-right:4px;}
.mail {background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:14px 16px;
       white-space:pre-wrap; font-size:.9rem; line-height:1.45;}
.mail.reply {background:#f0fdf4; border-color:#bbf7d0;}
.step {border-left:3px solid #cbd5e1; margin-left:10px; padding:4px 0 10px 16px; position:relative;}
.step:before {content:""; position:absolute; left:-8px; top:6px; width:13px; height:13px; border-radius:50%;
              background:#2563eb; border:2px solid #fff; box-shadow:0 0 0 1px #2563eb;}
.step.warn:before {background:#d97706; box-shadow:0 0 0 1px #d97706;}
.step.alert:before {background:#dc2626; box-shadow:0 0 0 1px #dc2626;}
.step b {font-size:.88rem;} .step .d {font-size:.83rem; color:#334155; white-space:pre-wrap;}
.empty {background:#fff; border:2px dashed #cbd5e1; border-radius:16px; padding:36px; text-align:center;}
</style>
""", unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


@st.cache_data(show_spinner=False)
def load_all(stamp: tuple):
    outbox = dd.read_json(OUTBOX, [])
    trace = dd.read_json(TRACE, {"emails": {}, "runs": []})
    policy = dd.load_policy_safe(POLICY)
    rows = dd.build_rows(outbox if isinstance(outbox, list) else [], trace, dd.inbox_emails(INBOX), policy)
    return rows, trace, policy


def file_stamp() -> tuple:
    def m(p):
        return p.stat().st_mtime if p.exists() else 0
    return m(OUTBOX), m(TRACE), m(POLICY)


rows, trace, policy = load_all(file_stamp())
runs = trace.get("runs", [])
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

top = st.columns([6, 1])
if top[1].button("↻ Refresh", use_container_width=True):
    st.cache_data.clear()
    st.rerun()


def kpi(col, value, label, tone):
    col.markdown(f'<div class="kpi {tone}"><div class="v">{esc(value)}</div><div class="l">{esc(label)}</div></div>',
                 unsafe_allow_html=True)


def badge(p):
    return f'<span class="badge {esc(p)}">{esc(p)}</span>'


def chips(codes):
    return "".join(f'<span class="chip">{esc(c)}</span>' for c in codes) or '<span class="muted">no policy code</span>'


tabs = st.tabs(["📊 Overview", "🚨 Escalations", "📥 Inbox & Details", "⚡ Live Run", "🕘 Run History"])

if not rows:
    with tabs[0]:
        st.markdown("""<div class="empty"><h3>No results yet</h3>
        <p>Run the agent first, then press Refresh:</p><code>python agent.py</code>
        <p class="muted">or use the ⚡ Live Run tab.</p></div>""", unsafe_allow_html=True)

# ---------------- Overview ----------------
with tabs[0]:
    if rows:
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
            tooltip=["category", "emails"]).properties(height=260), use_container_width=True)
        pr = df.groupby("priority").size().reindex(["high", "medium", "low"]).fillna(0).reset_index(name="emails")
        b.markdown("**Priority**")
        b.altair_chart(alt.Chart(pr).mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
            x=alt.X("priority:N", sort=["high", "medium", "low"], title=None),
            y=alt.Y("emails:Q", title=None),
            color=alt.Color("priority:N", scale=alt.Scale(domain=list(PCOLOR), range=list(PCOLOR.values())),
                            legend=None), tooltip=["priority", "emails"]).properties(height=260),
            use_container_width=True)
        esc_counts = dd.escalation_code_counts(rows, policy)
        cc.markdown("**Escalations by rule**")
        if esc_counts:
            ec = pd.DataFrame({"code": list(esc_counts), "emails": list(esc_counts.values())})
            cc.altair_chart(alt.Chart(ec).mark_bar(color="#dc2626", cornerRadiusEnd=6).encode(
                y=alt.Y("code:N", sort="-x", title=None), x=alt.X("emails:Q", title=None),
                tooltip=["code", "emails"]).properties(height=260), use_container_width=True)
        else:
            cc.info("No escalations.")
        st.markdown("**Latest decisions**")
        for r in rows[-5:][::-1]:
            st.markdown(f'<div class="card {esc(r["priority"])}">{badge(r["priority"])}'
                        f'<span class="chip cat">{esc(r["category"])}</span> <b>{esc(r["email_id"])}</b> · '
                        f'{esc(r["subject"])[:90]} <span class="muted">— {esc(r["from_name"])}</span></div>',
                        unsafe_allow_html=True)

# ---------------- Escalations ----------------
with tabs[1]:
    queue = [r for r in rows if r["escalate"]]
    queue.sort(key=lambda r: ({"high": 0, "medium": 1, "low": 2}.get(r["priority"], 3), r["email_id"]))
    st.markdown(f"### Needs a human · {len(queue)}")
    if not queue:
        st.success("Nothing needs a human right now.")
    cols = st.columns(2)
    for i, r in enumerate(queue):
        routes = "".join(f'<span class="route">→ {esc(x)}</span>' for x in r["routes"])
        cols[i % 2].markdown(f"""
<div class="card {esc(r['priority'])}">
  <div>{badge(r['priority'])}<span class="chip cat">{esc(r['category'])}</span>
  <span class="muted">{esc(r['email_id'])}</span></div>
  <h4>{esc(r['subject']) or '(no subject)'}</h4>
  <div class="muted">from {esc(r['from_name']) or 'unknown sender'}</div>
  <p style="margin:8px 0">{esc(r['escalation_reason'])}</p>
  <div>{chips(r['policy_refs'])}</div><div style="margin-top:6px">{routes}</div>
</div>""", unsafe_allow_html=True)

# ---------------- Inbox & details ----------------
with tabs[2]:
    if rows:
        f1, f2, f3, f4, f5 = st.columns([3, 2, 2, 2, 2])
        query = f1.text_input("Search", placeholder="name, subject, text…")
        cats = f2.multiselect("Category", sorted({r["category"] for r in rows}))
        pris = f3.multiselect("Priority", ["high", "medium", "low"])
        langs = f4.multiselect("Language", sorted({r["language"] for r in rows}))
        with f5:
            only_esc = st.checkbox("Escalated only")
            only_fb = st.checkbox("Fallback only")
        view = dd.filter_rows(rows, query, cats, pris, only_esc, only_fb, langs)
        table = pd.DataFrame([{
            "id": r["email_id"], "from": r["from_name"], "subject": r["subject"], "category": r["category"],
            "priority": r["priority"], "escalate": "🚨" if r["escalate"] else "", "confidence": r["confidence"],
            "language": r["language"], "source": "cache" if r["cache_hit"] else ("fallback" if r["fallback"] else "LLM"),
        } for r in view])
        st.caption(f"{len(view)} of {len(rows)} emails")
        if not table.empty:
            event = st.dataframe(
                table, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row",
                column_config={"confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1,
                                                                               format="%.2f")})
            picked = event.selection.rows[0] if event and event.selection.rows else None
            ids = [r["email_id"] for r in view]
            default = st.session_state.get("focus_id")
            if picked is not None:
                default = ids[picked]
            index = ids.index(default) if default in ids else 0
            sel = st.selectbox("Open email", ids, index=index)
            r = next(x for x in view if x["email_id"] == sel)

            st.markdown("---")
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
                st.markdown(f'{badge(r["priority"])}<span class="chip cat">{esc(r["category"])}</span> '
                            f'confidence <b>{r["confidence"]:.2f}</b> · {"🚨 escalate" if r["escalate"] else "no escalation"}',
                            unsafe_allow_html=True)
                st.markdown(f'<div class="mail reply">{esc(r["reply_draft"])}</div>', unsafe_allow_html=True)
                if r["escalate"]:
                    st.error(r["escalation_reason"])
                st.markdown(chips(r["policy_refs"]), unsafe_allow_html=True)
                for code in r["policy_refs"]:
                    with st.expander(f"{code}: rule text from policy.md"):
                        st.write(dd.rule_text(policy, code))
            st.markdown("#### 🧠 How the agent decided")
            meta = (f"model **{r['model'] or ('rule-based fallback' if r['fallback'] else '-')}** · attempts "
                    f"**{r['attempts']}** · cache hit **{'yes' if r['cache_hit'] else 'no'}** · "
                    f"duration **{r['duration_ms'] if r['duration_ms'] is not None else '-'} ms**")
            st.markdown(meta)
            if r["steps"]:
                timeline = "".join(
                    f'<div class="step {esc(s.get("status", "ok"))}"><b>{esc(s.get("n", ""))}. {esc(s.get("name", ""))}</b>'
                    f'<div class="d">{esc(s.get("detail", ""))}</div></div>' for s in r["steps"])
                st.markdown(timeline, unsafe_allow_html=True)
            else:
                st.info("No trace for this email yet (run the agent to record one).")
            st.markdown(f"**Agent notes:** {esc(r['agent_notes'])}", unsafe_allow_html=True)
            if r["llm_errors"]:
                with st.expander("LLM retries / errors"):
                    st.write(r["llm_errors"])
        else:
            st.info("No emails match the filters.")

# ---------------- Live run ----------------
with tabs[3]:
    st.markdown("### ⚡ Live run")
    st.caption("Runs `agent.py` on this machine. Drafts only; nothing is sent.")

    def stream(args, label):
        box = st.empty()
        lines = []
        with st.status(label, expanded=True) as status:
            for line in dd.run_agent_stream(args, timeout=300):
                lines.append(line)
                box.code("\n".join(lines[-40:]), language=None)
            ok = lines and lines[-1].endswith("code 0]")
            status.update(label=label + (" — done" if ok else " — finished with problems"),
                          state="complete" if ok else "error")
        st.cache_data.clear()
        return ok

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Process new emails**")
        st.caption("Only new or changed files in data/inbox (cached decisions are reused).")
        offline = st.toggle("Offline mode (no LLM)", value=False)
        if st.button("▶ Process new emails", type="primary"):
            stream(["--only-new"] + (["--offline"] if offline else []), "Processing new emails")
        st.markdown("**Reprocess everything**")
        sure = st.checkbox("I understand this calls the LLM for every email (uses quota)")
        if st.button("⟳ Reprocess all", disabled=not sure):
            stream(["--fresh"] + (["--offline"] if offline else []), "Reprocessing all emails")
    with c2:
        st.markdown("**Test a new email**")
        with st.form("new_email", clear_on_submit=False):
            fn = st.text_input("From name", "Test Student")
            fe = st.text_input("From email", "test.student@example.com")
            sj = st.text_input("Subject")
            bd = st.text_area("Body", height=150)
            ra = st.text_input("received_at (optional, ISO)", "")
            go = st.form_submit_button("💾 Save to inbox & process")
        if go:
            if not (sj.strip() or bd.strip()):
                st.warning("Write a subject or a body first.")
            else:
                path = dd.save_new_email(INBOX, fn, fe, sj, bd, ra)
                st.success(f"Saved {path.name}")
                if stream(["--file", str(path)] + (["--offline"] if offline else []), f"Processing {path.name}"):
                    st.session_state["focus_id"] = path.stem
                    st.info(f"Open the 📥 Inbox & Details tab: {path.stem} is selected.")
        up = st.file_uploader("…or upload an email file (.json, .txt, .eml)", type=["json", "txt", "eml"])
        if up is not None and st.button("⬆ Save upload & process"):
            path = dd.save_uploaded(INBOX, up.name, up.getvalue())
            st.success(f"Saved {path.name}")
            if stream(["--file", str(path)] + (["--offline"] if offline else []), f"Processing {path.name}"):
                st.session_state["focus_id"] = json.loads(path.read_text(encoding="utf-8-sig")).get("id", path.stem) \
                    if path.suffix == ".json" else path.stem

# ---------------- Run history ----------------
with tabs[4]:
    st.markdown("### 🕘 Run history")
    if runs:
        hist = pd.DataFrame([{
            "run": r.get("run_id"), "mode": r.get("mode"), "started": r.get("started"), "ended": r.get("ended"),
            "processed": r.get("processed"), "escalated": r.get("escalated"), "fallback": r.get("fallback"),
            "invalid": r.get("invalid"), "cache hits": r.get("cache_hits"), "LLM calls": r.get("llm_calls"),
            "models": ", ".join(r.get("models", [])) or ("offline" if r.get("offline") else "-"),
            "avg ms": r.get("avg_ms"),
        } for r in runs[::-1]])
        st.dataframe(hist, hide_index=True, use_container_width=True)
        skipped = sorted({f for r in runs for f in r.get("skipped_files", [])})
        if skipped:
            st.caption("Skipped non-email files: " + ", ".join(skipped))
    else:
        st.info("No runs recorded yet. Run `python agent.py`.")
