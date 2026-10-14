"""Status dashboard for outbox.json.  Run: streamlit run dashboard.py"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

OUTBOX = Path("outbox.json")
LOG = Path("logs/agent_run.log")

st.set_page_config(page_title="Helpdesk Triage", layout="wide")
st.title("Student Helpdesk Triage")
st.caption("Drafts only. Nothing is sent. Source: outbox.json")

if not OUTBOX.exists():
    st.warning("outbox.json not found. Run `python agent.py` first.")
    st.stop()

df = pd.DataFrame(json.loads(OUTBOX.read_text(encoding="utf-8")))
df["fallback"] = df["agent_notes"].str.contains("FALLBACK", na=False)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Emails", len(df))
c2.metric("Escalated", int(df["escalate"].sum()))
c3.metric("High priority", int((df["priority"] == "high").sum()))
c4.metric("Avg confidence", f"{df['confidence'].mean():.2f}")
c5.metric("Fallback used", int(df["fallback"].sum()))

left, right = st.columns(2)
left.bar_chart(df["category"].value_counts())
right.bar_chart(df["priority"].value_counts().reindex(["high", "medium", "low"]).fillna(0))

with st.sidebar:
    st.header("Filter")
    cats = st.multiselect("Category", sorted(df["category"].unique()))
    pris = st.multiselect("Priority", ["high", "medium", "low"])
    esc = st.selectbox("Escalated", ["all", "yes", "no"])
    query = st.text_input("Search text")

view = df
if cats:
    view = view[view["category"].isin(cats)]
if pris:
    view = view[view["priority"].isin(pris)]
if esc != "all":
    view = view[view["escalate"] == (esc == "yes")]
if query:
    mask = view.apply(lambda r: query.lower() in json.dumps(r.to_dict(), default=str).lower(), axis=1)
    view = view[mask]

order = {"high": 0, "medium": 1, "low": 2}
view = view.sort_values(by=["escalate", "priority", "email_id"],
                        key=lambda s: s.map(order) if s.name == "priority" else s,
                        ascending=[False, True, True])

st.subheader(f"Emails ({len(view)})")
st.dataframe(
    view[["email_id", "category", "priority", "escalate", "policy_refs", "confidence", "escalation_reason"]],
    width="stretch", hide_index=True,
)

for _, row in view.iterrows():
    flag = "[ESCALATE]" if row["escalate"] else "[ok]"
    with st.expander(f"{flag} {row['email_id']} | {row['category']} | {row['priority']} | conf {row['confidence']:.2f}"):
        if row["escalate"]:
            st.error(f"Escalate: {row['escalation_reason']}")
        st.markdown(f"**Policy refs:** {', '.join(row['policy_refs']) or 'none'}")
        st.markdown("**Reply draft**")
        st.code(row["reply_draft"], language=None)
        st.markdown(f"**Agent notes:** {row['agent_notes']}")

if LOG.exists():
    with st.expander("Run log (logs/agent_run.log)"):
        st.code(LOG.read_text(encoding="utf-8")[-20000:], language=None)
