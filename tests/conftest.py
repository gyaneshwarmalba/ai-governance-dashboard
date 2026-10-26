from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Policy
from app.providers import Completion, ProviderError
from app.store import Store
from app.telemetry import GovernedLLM

POLICY = Path(__file__).resolve().parent.parent / "config" / "governance.json"


class FakeProvider:
    name = "fake"

    def __init__(self, text="ok", input_tokens=1000, output_tokens=500, latency_ms=250.0, fail=False):
        self.text, self.input_tokens, self.output_tokens, self.latency_ms, self.fail = (
            text,
            input_tokens,
            output_tokens,
            latency_ms,
            fail,
        )
        self.prompts: list[str] = []

    def complete(self, model, prompt, max_tokens=512):
        self.prompts.append(prompt)
        if self.fail:
            raise ProviderError("ThrottlingException")
        return Completion(self.text, self.input_tokens, self.output_tokens, self.latency_ms)


@pytest.fixture()
def policy() -> Policy:
    return Policy.load(POLICY)


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(tmp_path / "gov.sqlite")


@pytest.fixture()
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture()
def llm(provider, policy, store) -> GovernedLLM:
    return GovernedLLM(provider, policy, store)
