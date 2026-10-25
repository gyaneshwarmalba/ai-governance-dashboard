"""Input and output guardrails.

Each check returns violations with an action from policy: `block` stops the
call, `redact` masks the content and continues, and `flag` records only.
Detectors are deliberately simple and transparent; they are a first line of
defence, not a replacement for a dedicated DLP or safety service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)"),
    "ssn": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "credit_card": re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)"),
}
INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(previous|prior|above) (instructions|prompts|rules)",
        r"disregard (the |your )?(system|previous) (prompt|instructions)",
        r"reveal (the |your )?(system prompt|hidden instructions)",
        r"you are now (in )?(developer|dan|jailbreak) mode",
        r"pretend (that )?you have no (rules|restrictions|guidelines)",
    )
]
SEVERITY = {"block": "high", "redact": "medium", "flag": "low"}


@dataclass(frozen=True)
class Violation:
    rule: str
    stage: str  # "input" | "output"
    action: str
    severity: str
    detail: str


def _luhn(digits: str) -> bool:
    nums = [int(d) for d in digits][::-1]
    total = sum(n if i % 2 == 0 else (n * 2 - 9 if n * 2 > 9 else n * 2) for i, n in enumerate(nums))
    return total % 10 == 0


def find_pii(text: str, types: list[str]) -> list[tuple[str, re.Match[str]]]:
    hits = []
    for kind in types:
        for m in PII_PATTERNS[kind].finditer(text):
            if kind == "credit_card" and not _luhn(re.sub(r"\D", "", m.group(0))):
                continue
            hits.append((kind, m))
    return hits


def redact(text: str, types: list[str]) -> str:
    for kind, m in sorted(find_pii(text, types), key=lambda h: -h[1].start()):
        text = text[: m.start()] + f"[{kind.upper()}]" + text[m.end() :]
    return text


def check_input(prompt: str, policy: dict[str, Any]) -> tuple[str, list[Violation]]:
    """Return (possibly redacted prompt, violations)."""
    violations: list[Violation] = []
    if len(prompt) > policy.get("max_prompt_chars", 1_000_000):
        violations.append(Violation("max_prompt_chars", "input", "block", "high", f"{len(prompt)} chars"))

    for pattern in INJECTION_PATTERNS:
        if m := pattern.search(prompt):
            action = policy["prompt_injection"]["action"]
            violations.append(Violation("prompt_injection", "input", action, SEVERITY[action], m.group(0)))
            break

    topics = policy.get("blocked_topics", {})
    for term in topics.get("terms", []):
        if term.lower() in prompt.lower():
            action = topics["action"]
            violations.append(Violation("blocked_topic", "input", action, SEVERITY[action], term))

    pii = policy.get("pii", {})
    hits = find_pii(prompt, pii.get("types", []))
    if hits:
        action = pii["action"]
        kinds = sorted({k for k, _ in hits})
        violations.append(Violation("pii", "input", action, SEVERITY[action], f"{len(hits)} match(es): {', '.join(kinds)}"))
        if action == "redact":
            prompt = redact(prompt, pii["types"])
    return prompt, violations


def check_output(text: str, policy: dict[str, Any]) -> tuple[str, list[Violation]]:
    rule = policy.get("output_pii")
    if not rule:
        return text, []
    types = policy.get("pii", {}).get("types", list(PII_PATTERNS))
    hits = find_pii(text, types)
    if not hits:
        return text, []
    action = rule["action"]
    v = Violation("output_pii", "output", action, SEVERITY[action], f"{len(hits)} match(es)")
    return (redact(text, types) if action == "redact" else text), [v]
