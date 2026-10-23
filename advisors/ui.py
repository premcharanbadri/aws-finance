"""Streamlit chat for the advisor agents.

  make advisors        # or: streamlit run advisors/ui.py

Pick an advisor in the sidebar, ask a question; the agent writes SQL, runs it on
Redshift as the read-only advisor_ro user, and answers. Each answer shows the SQL it ran.
Separate entrypoint so it doesn't collide with the team's root app.py; the team can
import build_advisor / PERSONAS from the advisors package instead.
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from advisors import PERSONAS, build_advisor  # noqa: E402

st.set_page_config(page_title="aws-finance advisors", page_icon="📊", layout="wide")

# ---- sidebar: persona picker -------------------------------------------------
with st.sidebar:
    st.header("Advisor")
    persona = st.radio("Choose an advisor", list(PERSONAS), format_func=lambda k: PERSONAS[k]["title"])
    st.caption(f"Reads Redshift schemas: {', '.join(PERSONAS[persona]['schemas'])} (read-only)")
    if st.button("New conversation", width="stretch"):
        st.session_state.pop(f"agent_{persona}", None)
        st.session_state.pop(f"history_{persona}", None)
        st.rerun()
    st.divider()
    st.caption("Synthetic data for a fictional fintech, Jan 2025 - Jun 2026. "
               "Analysis only - not tax, legal or investment advice.")

# One agent (with its own conversation memory and query log) per persona per session.
akey, hkey, lkey = f"agent_{persona}", f"history_{persona}", f"log_{persona}"
if akey not in st.session_state:
    st.session_state[lkey] = []
    st.session_state[akey] = build_advisor(persona, query_log=st.session_state[lkey])
    st.session_state[hkey] = []
agent, history, qlog = st.session_state[akey], st.session_state[hkey], st.session_state[lkey]

st.title(PERSONAS[persona]["title"])


def show_queries(queries):
    if not queries:
        return
    with st.expander(f"SQL run on Redshift ({len(queries)} quer{'y' if len(queries) == 1 else 'ies'})"):
        for i, q in enumerate(queries, 1):
            st.markdown(f"**Query {i}** - " + (f"error: {q.error}" if q.error
                                                else f"{q.total_rows} rows in {q.seconds}s"))
            st.code(q.sql, language="sql")
            if q.rows:
                st.dataframe([dict(zip(q.columns, r)) for r in q.rows[:50]],
                             width="stretch", hide_index=True)


# ---- conversation ------------------------------------------------------------
for turn in history:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
        show_queries(turn.get("queries"))

question = None
if not history:
    st.write("Try one of these:")
    cols = st.columns(2)
    for i, ex in enumerate(PERSONAS[persona]["examples"]):
        if cols[i % 2].button(ex, key=f"ex_{persona}_{i}", width="stretch"):
            question = ex

question = st.chat_input(f"Ask the {persona} advisor a question") or question
if question:
    history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        start = len(qlog)
        with st.spinner("Querying Redshift..."):
            try:
                answer = str(agent(question)).strip()
            except Exception as e:  # noqa: BLE001 - show model/AWS errors in the UI
                answer = f"Sorry, something went wrong: `{e}`"
        queries = qlog[start:]
        st.markdown(answer)
        show_queries(queries)
    history.append({"role": "assistant", "content": answer, "queries": queries})
