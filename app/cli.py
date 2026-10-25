"""CLI: `python -m app.cli {simulate,call,report,verify-audit}`."""

from __future__ import annotations

import argparse
import json
import sys
import time

from app import analytics, budgets
from app.config import Policy, Settings
from app.logging_config import configure_logging
from app.store import Store


def build_llm(settings: Settings, policy: Policy, store: Store):
    from app.providers import BedrockProvider, SimulatedProvider
    from app.telemetry import GovernedLLM

    if settings.llm_provider == "bedrock":
        provider = BedrockProvider(settings.aws_region)
    elif settings.llm_provider == "local":
        provider = SimulatedProvider()
    else:
        raise ValueError(f"unknown LLM_PROVIDER {settings.llm_provider!r}")
    return GovernedLLM(provider, policy, store)


def report(store: Store, policy: Policy, days: int = 30) -> dict:
    now = time.time()
    df = analytics.calls(store, now - days * 86400, now)
    return {
        "window_days": days,
        "kpis": analytics.kpis(df),
        "budgets": [
            {"team": b.team, "budget_usd": b.budget_usd, "spent_usd": b.spent_usd, "level": b.level(policy.alert_thresholds)}
            for b in budgets.statuses(store, policy, now)
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-governance")
    sub = parser.add_subparsers(dest="command", required=True)
    sim = sub.add_parser("simulate", help="generate synthetic governed traffic")
    sim.add_argument("--calls", type=int, default=1500)
    sim.add_argument("--days", type=int, default=30)
    sim.add_argument("--seed", type=int, default=7)
    sim.add_argument("--reset", action="store_true", help="delete existing data first")
    call = sub.add_parser("call", help="make one governed call")
    call.add_argument("--team", required=True)
    call.add_argument("--model", default="anthropic.claude-3-haiku")
    call.add_argument("--prompt", required=True)
    sub.add_parser("report", help="KPIs and budget status as JSON")
    sub.add_parser("verify-audit", help="verify the audit hash chain")
    args = parser.parse_args(argv)

    configure_logging()
    settings = Settings()
    if args.command == "simulate" and args.reset and settings.db_path.exists():
        settings.db_path.unlink()
    store = Store(settings.db_path)
    policy = Policy.load(settings.policy_path)

    if args.command == "simulate":
        from app.simulate import simulate

        print(json.dumps(dict(simulate(store, policy, args.calls, args.days, args.seed))))
    elif args.command == "call":
        try:
            r = build_llm(settings, policy, store).invoke(args.prompt, team=args.team, model=args.model)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"status": r.status, "reason": r.reason, "text": r.text, "cost_usd": r.cost_usd,
                          "violations": [v.rule for v in r.violations], "span_id": r.span_id}))  # fmt: skip
        return 0 if r.status == "ok" else 1
    elif args.command == "report":
        print(json.dumps(report(store, policy), indent=2))
    else:
        from app.audit import AuditLog

        result = AuditLog(store).verify()
        print(json.dumps({"ok": result.ok, "entries": result.entries, "first_broken_seq": result.first_broken_seq}))
        return 0 if result.ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
