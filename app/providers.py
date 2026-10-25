"""Model providers behind one interface.

`SimulatedProvider` is deterministic (seeded) and models per-model latency,
token usage, and error rates, so the dashboard and tests run offline.
`BedrockProvider` calls the Bedrock Converse API and reads real token usage
and latency from the response.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float | None = None


class Provider(Protocol):
    name: str

    def complete(self, model: str, prompt: str, max_tokens: int = 512) -> Completion: ...


PROFILES = {
    "anthropic.claude-3-5-sonnet": {"latency": 1900.0, "jitter": 0.35, "error_rate": 0.015, "verbosity": 1.0},
    "anthropic.claude-3-haiku": {"latency": 650.0, "jitter": 0.3, "error_rate": 0.01, "verbosity": 0.7},
    "amazon.titan-text-express": {"latency": 900.0, "jitter": 0.4, "error_rate": 0.03, "verbosity": 0.6},
    "openai.gpt-4o-mini": {"latency": 800.0, "jitter": 0.4, "error_rate": 0.02, "verbosity": 0.8},
}


class SimulatedProvider:
    name = "simulated"

    def __init__(self, seed: int = 7) -> None:
        self._rng = random.Random(seed)

    def complete(self, model: str, prompt: str, max_tokens: int = 512) -> Completion:
        profile = next((p for k, p in PROFILES.items() if model.startswith(k)), PROFILES["anthropic.claude-3-haiku"])
        if self._rng.random() < profile["error_rate"]:
            raise ProviderError("ThrottlingException: rate exceeded")
        input_tokens = max(1, math.ceil(len(prompt) / 4))
        output_tokens = min(max_tokens, int(self._rng.uniform(120, 420) * profile["verbosity"]))
        latency = profile["latency"] * (1 + self._rng.uniform(-profile["jitter"], profile["jitter"])) + output_tokens * 2.5
        text = f"Summary ({model}): the request was processed and {output_tokens} tokens of analysis were produced."
        return Completion(text=text, input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=round(latency, 1))


class BedrockProvider:
    name = "bedrock"

    def __init__(self, region: str) -> None:
        import boto3

        self._client = boto3.client("bedrock-runtime", region_name=region)

    def complete(self, model: str, prompt: str, max_tokens: int = 512) -> Completion:
        try:
            resp = self._client.converse(
                modelId=model,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
            )
        except Exception as exc:
            logger.warning("bedrock call failed", extra={"model": model, "error": str(exc)})
            raise ProviderError(str(exc)) from exc
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        usage = resp.get("usage", {})
        return Completion(
            text=text,
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            latency_ms=float(resp.get("metrics", {}).get("latencyMs", 0)) or None,
        )
