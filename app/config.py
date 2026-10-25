"""Settings and the versioned governance policy (pricing, budgets, guardrails)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(default_factory=lambda: Path(_env("GOVERNANCE_DB", str(PROJECT_ROOT / "data" / "governance.sqlite"))))
    policy_path: Path = field(
        default_factory=lambda: Path(_env("GOVERNANCE_POLICY", str(PROJECT_ROOT / "config" / "governance.json")))
    )
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "local"))
    aws_region: str = field(default_factory=lambda: _env("AWS_DEFAULT_REGION", "us-west-2"))


@dataclass(frozen=True)
class Policy:
    version: str
    pricing: dict[str, dict[str, float]]
    budgets: dict[str, float]
    alert_thresholds: list[float]
    enforce_budgets: bool
    guardrails: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> Policy:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for model, price in raw["pricing_per_1k_tokens_usd"].items():
            if not {"input", "output"} <= price.keys():
                raise ValueError(f"pricing for {model} needs input and output rates")
        return cls(
            version=raw["version"],
            pricing=raw["pricing_per_1k_tokens_usd"],
            budgets=raw["budgets_usd_monthly"],
            alert_thresholds=sorted(raw.get("budget_alert_thresholds", [0.8, 1.0])),
            enforce_budgets=bool(raw.get("enforce_budgets", False)),
            guardrails=raw["guardrails"],
        )

    def price_key(self, model: str) -> str:
        """Match versioned model ids (anthropic.claude-3-5-sonnet-20240620-v1:0) to their pricing family."""
        matches = [k for k in self.pricing if model == k or model.startswith(k + "-") or model.startswith(k + ":")]
        if not matches:
            raise KeyError(f"no pricing configured for model {model!r}")
        return max(matches, key=len)

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.pricing[self.price_key(model)]
        return round(input_tokens / 1000 * price["input"] + output_tokens / 1000 * price["output"], 6)
