"""Aggregations for the dashboard and the `report` command."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.store import Store


def calls(
    store: Store, since: float, until: float, teams: list[str] | None = None, models: list[str] | None = None
) -> pd.DataFrame:
    df = pd.DataFrame(store.query("SELECT * FROM calls WHERE ts >= ? AND ts <= ? ORDER BY ts", (since, until)))
    if df.empty:
        return df
    if teams:
        df = df[df.team.isin(teams)]
    if models:
        df = df[df.model.isin(models)]
    df["time"] = pd.to_datetime(df.ts, unit="s", utc=True)
    return df


def violations(store: Store, since: float, until: float) -> pd.DataFrame:
    sql = (
        "SELECT v.*, c.team, c.model FROM violations v JOIN calls c USING (span_id) "
        "WHERE v.ts >= ? AND v.ts <= ? ORDER BY v.ts DESC"
    )
    df = pd.DataFrame(store.query(sql, (since, until)))
    if not df.empty:
        df["time"] = pd.to_datetime(df.ts, unit="s", utc=True)
    return df


def kpis(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"calls": 0, "cost_usd": 0.0, "p95_latency_ms": 0.0, "error_rate": 0.0, "block_rate": 0.0, "tokens": 0}
    ok = df[df.status == "ok"]
    return {
        "calls": int(len(df)),
        "cost_usd": round(float(df.cost_usd.sum()), 4),
        "p95_latency_ms": round(float(ok.latency_ms.quantile(0.95)), 1) if not ok.empty else 0.0,
        "error_rate": round(float((df.status == "error").mean()), 4),
        "block_rate": round(float((df.status == "blocked").mean()), 4),
        "tokens": int(df.input_tokens.sum() + df.output_tokens.sum()),
    }


def daily_cost(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.assign(day=df.time.dt.date).pivot_table(
        index="day", columns="team", values="cost_usd", aggfunc="sum", fill_value=0.0
    )


def model_performance(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    ok = df[df.status == "ok"]
    grouped = df.groupby("model")
    out = pd.DataFrame(
        {
            "calls": grouped.size(),
            "cost_usd": grouped.cost_usd.sum().round(4),
            "error_rate": grouped.status.apply(lambda s: (s == "error").mean()).round(4),
            "p50_latency_ms": ok.groupby("model").latency_ms.median().round(0),
            "p95_latency_ms": ok.groupby("model").latency_ms.quantile(0.95).round(0),
            "avg_output_tokens": ok.groupby("model").output_tokens.mean().round(0),
        }
    )
    out["cost_per_1k_calls"] = (out.cost_usd / out.calls * 1000).round(2)
    return out.sort_values("cost_usd", ascending=False)


def template_comparison(df: pd.DataFrame) -> pd.DataFrame:
    """Prompt-template versions side by side: quality, latency, cost, and guardrail outcomes."""
    if df.empty or df.template.fillna("").eq("").all():
        return pd.DataFrame()
    scoped = df[df.template.fillna("") != ""]
    g = scoped.groupby(["template", "template_version"])
    return pd.DataFrame(
        {
            "calls": g.size(),
            "avg_quality": g.quality.mean().round(3),
            "p95_latency_ms": g.latency_ms.quantile(0.95).round(0),
            "avg_cost_usd": g.cost_usd.mean().round(5),
            "block_rate": g.status.apply(lambda s: (s == "blocked").mean()).round(4),
            "error_rate": g.status.apply(lambda s: (s == "error").mean()).round(4),
        }
    ).reset_index()
