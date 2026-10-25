"""GovernedLLM: the single choke point every model call goes through.

Per call it:
  1. checks team attribution and budget (hard stop if enforcement is on),
  2. runs input guardrails (block / redact / flag),
  3. calls the provider,
  4. runs output guardrails,
  5. prices the call from the versioned pricing table,
  6. emits an OpenTelemetry span using GenAI semantic-convention attributes,
  7. appends a hash-chained audit entry (prompt hash + redacted preview only).

Spans go to SQLite for the dashboard and, if OTEL_EXPORTER_OTLP_ENDPOINT is
set, to any OTLP backend (Grafana Tempo, Datadog, Honeycomb, LangSmith, ...).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

from app.audit import AuditLog
from app.budgets import crossed, spent
from app.config import Policy
from app.guardrails import Violation, check_input, check_output, redact
from app.providers import Provider, ProviderError
from app.store import Store

logger = logging.getLogger(__name__)
SPAN_NAME = "gen_ai.chat"
VIOLATION_FIELDS = ("rule", "stage", "action", "severity", "detail")


class SqliteSpanExporter(SpanExporter):
    """Persists governed-call spans into the `calls` and `violations` tables."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            for span in spans:
                if span.name != SPAN_NAME:
                    continue
                a = span.attributes or {}
                span_id = format(span.context.span_id, "016x")
                ts = a.get("governance.start_ts", span.start_time / 1e9)
                row = {
                    "span_id": span_id,
                    "trace_id": format(span.context.trace_id, "032x"),
                    "ts": ts,
                    "team": a.get("governance.team"),
                    "project": a.get("governance.project"),
                    "user_id": a.get("governance.user"),
                    "provider": a.get("gen_ai.system"),
                    "model": a.get("gen_ai.request.model"),
                    "template": a.get("governance.prompt.template"),
                    "template_version": a.get("governance.prompt.version"),
                    "latency_ms": a.get("governance.latency_ms", (span.end_time - span.start_time) / 1e6),
                    "input_tokens": a.get("gen_ai.usage.input_tokens", 0),
                    "output_tokens": a.get("gen_ai.usage.output_tokens", 0),
                    "cost_usd": a.get("governance.cost_usd", 0.0),
                    "status": a.get("governance.status"),
                    "error": a.get("error.type"),
                    "quality": a.get("governance.quality"),
                }
                violations = [
                    {"span_id": span_id, "ts": ts, **{k: (e.attributes or {}).get(k) for k in VIOLATION_FIELDS}}
                    for e in span.events
                    if e.name == "guardrail.violation"
                ]
                self.store.insert_call(row, violations)
        except Exception:
            logger.exception("failed to persist spans")
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS


def build_tracer_provider(store: Store) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": "ai-governance-gateway"}))
    provider.add_span_processor(SimpleSpanProcessor(SqliteSpanExporter(store)))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            logger.info("OTLP export enabled", extra={"endpoint": os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]})
        except ImportError:
            logger.warning("OTEL_EXPORTER_OTLP_ENDPOINT set but opentelemetry-exporter-otlp is not installed")
    return provider


@dataclass
class GovernedResult:
    status: str  # ok | blocked | error
    text: str | None
    span_id: str
    model: str
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    violations: list[Violation] = field(default_factory=list)
    reason: str | None = None


class GovernedLLM:
    def __init__(self, provider: Provider, policy: Policy, store: Store, tracer_provider: TracerProvider | None = None) -> None:
        self.provider = provider
        self.policy = policy
        self.store = store
        self.audit = AuditLog(store)
        self._tracer_provider = tracer_provider or build_tracer_provider(store)
        self._tracer = self._tracer_provider.get_tracer("ai-governance-dashboard")

    def invoke(
        self,
        prompt: str,
        *,
        team: str,
        model: str,
        project: str | None = None,
        user: str | None = None,
        template: str | None = None,
        template_version: str | None = None,
        max_tokens: int = 512,
        evaluator: Callable[[str, str], float] | None = None,
        now: float | None = None,
    ) -> GovernedResult:
        start = time.time() if now is None else now
        span = self._tracer.start_span(SPAN_NAME, start_time=int(start * 1e9))
        span_id = format(span.get_span_context().span_id, "016x")
        result = GovernedResult(status="ok", text=None, span_id=span_id, model=model)
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": self.provider.name,
            "gen_ai.request.model": model,
            "gen_ai.request.max_tokens": max_tokens,
            "governance.team": team,
            "governance.project": project or "",
            "governance.user": user or "",
            "governance.prompt.template": template or "",
            "governance.prompt.version": template_version or "",
            "governance.policy_version": self.policy.version,
        }
        span.set_attributes(attrs)
        latency_ms = 0.0
        before = spent(self.store, team, start) if team in self.policy.budgets else 0.0
        try:
            self._precheck(team, model, before, result)
            if result.status == "ok":
                clean_prompt, violations = check_input(prompt, self.policy.guardrails)
                result.violations += violations
                blocking = [v for v in violations if v.action == "block"]
                if blocking:
                    result.status, result.reason = "blocked", blocking[0].rule
                else:
                    started = time.perf_counter()
                    completion = self.provider.complete(model, clean_prompt, max_tokens)
                    latency_ms = completion.latency_ms or (time.perf_counter() - started) * 1000
                    text, out_violations = check_output(completion.text, self.policy.guardrails)
                    result.violations += out_violations
                    result.text = text
                    result.cost_usd = self.policy.cost(model, completion.input_tokens, completion.output_tokens)
                    span.set_attributes(
                        {
                            "gen_ai.usage.input_tokens": completion.input_tokens,
                            "gen_ai.usage.output_tokens": completion.output_tokens,
                            "governance.cost_usd": result.cost_usd,
                        }
                    )
                    if evaluator is not None:
                        span.set_attribute("governance.quality", float(evaluator(clean_prompt, text)))
        except ProviderError as exc:
            result.status, result.reason = "error", str(exc)
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            logger.warning("governed call failed", extra={"team": team, "model": model, "error": str(exc)})
        finally:
            for v in result.violations:
                span.add_event("guardrail.violation", {k: getattr(v, k) for k in VIOLATION_FIELDS})
            result.latency_ms = round(latency_ms, 1)
            span.set_attributes(
                {"governance.status": result.status, "governance.start_ts": start, "governance.latency_ms": result.latency_ms}
            )
            span.end(end_time=int((start + latency_ms / 1000) * 1e9))  # fmt: skip

        self._audit(prompt, team, model, result, start)
        self._budget_alerts(team, before, start, latency_ms)
        return result

    def _precheck(self, team: str, model: str, month_spend: float, result: GovernedResult) -> None:
        try:
            self.policy.price_key(model)
        except KeyError:
            result.violations.append(Violation("unapproved_model", "input", "block", "high", model))
            result.status, result.reason = "blocked", "unapproved_model"
            return
        if team not in self.policy.budgets:
            result.violations.append(Violation("unregistered_team", "input", "block", "high", team))
            result.status, result.reason = "blocked", "unregistered_team"
            return
        if self.policy.enforce_budgets and month_spend >= self.policy.budgets[team]:
            detail = f"${month_spend:.2f} of ${self.policy.budgets[team]:.2f}"
            result.violations.append(Violation("budget_exceeded", "input", "block", "high", detail))
            result.status, result.reason = "blocked", "budget_exceeded"

    def _audit(self, prompt: str, team: str, model: str, result: GovernedResult, ts: float) -> None:
        types = self.policy.guardrails.get("pii", {}).get("types", [])
        self.audit.append(
            actor=team,
            action=f"llm.call.{result.status}",
            span_id=result.span_id,
            ts=ts,
            payload={
                "model": model,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "prompt_preview": redact(prompt[:120], types),
                "cost_usd": result.cost_usd,
                "violations": [v.rule for v in result.violations],
                "reason": result.reason,
                "policy_version": self.policy.version,
            },
        )

    def _budget_alerts(self, team: str, before: float, start: float, latency_ms: float) -> None:
        budget = self.policy.budgets.get(team)
        if not budget:
            return
        after = spent(self.store, team, start + latency_ms / 1000)
        for threshold in crossed(before, after, budget, self.policy.alert_thresholds):
            logger.warning("budget threshold crossed", extra={"team": team, "threshold": threshold, "spent_usd": round(after, 4)})
            self.audit.append(
                actor="governance",
                action="budget.alert",
                ts=start,
                payload={"team": team, "threshold": threshold, "spent_usd": round(after, 4), "budget_usd": budget},
            )
