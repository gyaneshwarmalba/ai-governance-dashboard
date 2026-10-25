"""Governance dashboard: `streamlit run app/dashboard.py`."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import analytics, budgets  # noqa: E402
from app.audit import AuditLog  # noqa: E402
from app.cli import build_llm  # noqa: E402
from app.config import Policy, Settings  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.simulate import simulate  # noqa: E402
from app.store import Store  # noqa: E402

configure_logging()
st.set_page_config(page_title="AI Governance Dashboard", layout="wide")


@st.cache_resource
def resources():
    settings = Settings()
    store = Store(settings.db_path)
    policy = Policy.load(settings.policy_path)
    return settings, store, policy, build_llm(settings, policy, store)


settings, store, policy, llm = resources()
st.title("AI Governance Dashboard")
st.caption(f"Policy `{policy.version}` · provider `{llm.provider.name}` · store `{settings.db_path.name}`")

if not store.query("SELECT 1 FROM calls LIMIT 1"):
    st.info("No telemetry yet. Generate 30 days of synthetic, fully governed traffic to explore the dashboard.")
    if st.button("Generate demo data", type="primary"):
        with st.spinner("Simulating governed calls..."):
            simulate(store, policy)
        st.rerun()
    st.stop()

now = time.time()
with st.sidebar:
    st.header("Filters")
    days = st.slider("Window (days)", 1, 30, 30)
    all_calls = analytics.calls(store, now - days * 86400, now)
    teams = st.multiselect("Teams", sorted(all_calls.team.unique()) if not all_calls.empty else [])
    models = st.multiselect("Models", sorted(all_calls.model.unique()) if not all_calls.empty else [])

df = analytics.calls(store, now - days * 86400, now, teams or None, models or None)
k = analytics.kpis(df)
cols = st.columns(6)
cols[0].metric("Calls", f"{k['calls']:,}")
cols[1].metric("Spend", f"${k['cost_usd']:.2f}")
cols[2].metric("Tokens", f"{k['tokens']:,}")
cols[3].metric("p95 latency", f"{k['p95_latency_ms']:.0f} ms")
cols[4].metric("Error rate", f"{k['error_rate']:.1%}")
cols[5].metric("Blocked", f"{k['block_rate']:.1%}")

overview, models_tab, prompts_tab, guard_tab, budget_tab, audit_tab, try_tab = st.tabs(
    ["Cost", "Models", "Prompt templates", "Guardrails", "Budgets", "Audit trail", "Try a governed call"]
)

with overview:
    st.subheader("Daily spend by team (USD)")
    st.bar_chart(analytics.daily_cost(df))
    st.subheader("Calls by status")
    if not df.empty:
        st.bar_chart(
            df.assign(day=df.time.dt.date).pivot_table(
                index="day", columns="status", values="span_id", aggfunc="count", fill_value=0
            )
        )

with models_tab:
    st.dataframe(analytics.model_performance(df), use_container_width=True)
    st.caption("Latency percentiles use successful calls only; cost per 1k calls helps compare model choices for a workload.")

with prompts_tab:
    comparison = analytics.template_comparison(df)
    if comparison.empty:
        st.write("No templated calls in this window.")
    else:
        st.dataframe(comparison, use_container_width=True)
        st.caption("Compare template versions before promoting one: quality comes from the evaluator attached to each call.")

with guard_tab:
    v = analytics.violations(store, now - days * 86400, now)
    if v.empty:
        st.write("No guardrail violations in this window.")
    else:
        if teams:
            v = v[v.team.isin(teams)]
        c1, c2 = st.columns([1, 2])
        c1.dataframe(v.groupby(["rule", "action"]).size().rename("count").reset_index(), use_container_width=True)
        c2.dataframe(v[["time", "team", "model", "rule", "stage", "action", "detail"]].head(200), use_container_width=True)

with budget_tab:
    for b in budgets.statuses(store, policy, now):
        level = b.level(policy.alert_thresholds)
        icon = {"ok": "🟢", "warning": "🟠", "exceeded": "🔴"}[level]
        st.write(f"{icon} **{b.team}**: ${b.spent_usd:.2f} of ${b.budget_usd:.2f} this month ({b.ratio:.0%})")
        st.progress(min(b.ratio, 1.0))
    alerts = store.query("SELECT ts, payload FROM audit WHERE action = 'budget.alert' ORDER BY seq DESC LIMIT 20")
    if alerts:
        st.subheader("Budget alerts")
        rows = [{"time": pd.to_datetime(a["ts"], unit="s", utc=True), **json.loads(a["payload"])} for a in alerts]
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    st.caption(
        "Enforcement is "
        + ("on: calls are blocked once a team's monthly budget is spent." if policy.enforce_budgets else "off (alerts only).")
    )

with audit_tab:
    audit = AuditLog(store)
    if st.button("Verify hash chain"):
        result = audit.verify()
        (st.success if result.ok else st.error)(
            f"Chain intact across {result.entries:,} entries" if result.ok else f"Chain broken at entry {result.first_broken_seq}"
        )
    recent = pd.DataFrame(audit.recent(200))
    if not recent.empty:
        recent["time"] = pd.to_datetime(recent.ts, unit="s", utc=True)
        st.dataframe(recent[["seq", "time", "actor", "action", "span_id", "payload", "hash"]], use_container_width=True)

with try_tab:
    with st.form("try"):
        team = st.selectbox("Team", sorted(policy.budgets) + ["unregistered-team"])
        model = st.selectbox("Model", list(policy.pricing))
        prompt = st.text_area("Prompt", "Summarise the termination rights for jane.doe@example.com's contract.")
        submitted = st.form_submit_button("Send through the governance gateway")
    if submitted:
        r = llm.invoke(prompt, team=team, model=model, project="playground", template="playground", template_version="v1")
        (st.success if r.status == "ok" else st.error)(f"Status: {r.status}" + (f" ({r.reason})" if r.reason else ""))
        if r.text:
            st.write(r.text)
        st.json(
            {
                "span_id": r.span_id,
                "cost_usd": r.cost_usd,
                "latency_ms": r.latency_ms,
                "violations": [vars(x) for x in r.violations],
            }
        )
