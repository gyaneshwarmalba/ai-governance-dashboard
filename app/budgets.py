"""Month-to-date spend per team against budget."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import Policy
from app.store import Store


def month_start(ts: float) -> float:
    dt = datetime.fromtimestamp(ts, tz=UTC)
    return datetime(dt.year, dt.month, 1, tzinfo=UTC).timestamp()


def spent(store: Store, team: str, now: float) -> float:
    row = store.query(
        "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM calls WHERE team = ? AND ts >= ? AND ts <= ?",
        (team, month_start(now), now),
    )
    return float(row[0]["s"])


@dataclass(frozen=True)
class BudgetStatus:
    team: str
    budget_usd: float
    spent_usd: float

    @property
    def ratio(self) -> float:
        return self.spent_usd / self.budget_usd if self.budget_usd else 0.0

    def level(self, thresholds: list[float]) -> str:
        if self.ratio >= thresholds[-1]:
            return "exceeded"
        if self.ratio >= thresholds[0]:
            return "warning"
        return "ok"


def statuses(store: Store, policy: Policy, now: float) -> list[BudgetStatus]:
    return [BudgetStatus(team, budget, round(spent(store, team, now), 4)) for team, budget in sorted(policy.budgets.items())]


def crossed(before: float, after: float, budget: float, thresholds: list[float]) -> list[float]:
    if budget <= 0:
        return []
    return [t for t in thresholds if before / budget < t <= after / budget]
