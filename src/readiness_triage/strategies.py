"""The three strategies under comparison, defined once for the CLI and the evaluation.

rules    explicit rules only; never calls a model
llm      one model request per diagnosis
cascade  rules first; the model is asked only when the rules abstain
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import rules
from .models import Bundle, Diagnosis

STRATEGIES = ("rules", "llm", "cascade")


@dataclass(frozen=True)
class Outcome:
    diagnosis: Diagnosis
    model_calls: int = 0
    provider_attempts: int = 0
    usage: dict = field(default_factory=dict)


def needs_model(strategies) -> bool:
    return any(s != "rules" for s in strategies)


def run(strategy: str, bundle: Bundle, client=None) -> Outcome:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
    if strategy in ("rules", "cascade"):
        diagnosis = rules.diagnose(bundle)
        if strategy == "rules" or not diagnosis.abstained:
            return Outcome(diagnosis)
    if client is None:
        raise ValueError(f"Strategy {strategy!r} needs a configured model client")
    result = client.diagnose(bundle)
    return Outcome(result.diagnosis.check(bundle), 1, result.attempts, result.usage)
