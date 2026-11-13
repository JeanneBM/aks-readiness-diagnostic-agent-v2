"""Replay evaluation in small steps: load cases, run attempts, score, summarize, report.

Reporting choices:
* Accuracy is reported twice: over all attempts (errors count as misses) and over
  answered attempts. The first mixes diagnosis quality with provider availability;
  the second isolates quality. Both are needed to read a result honestly.
* Each label records what was injected and, separately, what the evidence can
  support. A port fault behind a server that logs nothing is labelled
  ``insufficient_evidence``: abstaining is the right answer there.
* No significance test. With a handful of cases a p-value suggests more than the
  data holds; the report lists which cases the strategies disagree on instead.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time

from . import llm, strategies
from .errors import ContractError, ModelNoAnswer, ProviderError
from .models import CAUSES, Bundle, Diagnosis, check_keys

SPLITS = ("dev", "held-out")
ORIGINS = ("synthetic", "recorded")


@dataclass(frozen=True)
class Label:
    split: str
    expected: str
    injected: str
    framework: str
    origin: str
    basis: str
    citations: tuple[tuple[str, ...], ...]   # a conclusion must cite every id of at least one group

    @classmethod
    def parse(cls, value, bundle: Bundle, where: str) -> "Label":
        check_keys(value, ("split", "expected", "injected", "framework", "origin", "basis", "citations"), where=where)
        if value["split"] not in SPLITS:
            raise ValueError(f"{where}.split must be one of {SPLITS}")
        for key in ("expected", "injected"):
            if value[key] not in CAUSES:
                raise ValueError(f"{where}.{key} must be one of {CAUSES}")
        if value["origin"] not in ORIGINS:
            raise ValueError(f"{where}.origin must be one of {ORIGINS}")
        groups = value["citations"]
        if not isinstance(groups, list) or any(not isinstance(g, list) or not g for g in groups):
            raise ValueError(f"{where}.citations must be a list of nonempty lists")
        unknown = {i for g in groups for i in g} - bundle.ids
        if unknown:
            raise ValueError(f"{where}.citations reference unknown evidence: {', '.join(sorted(unknown))}")
        return cls(value["split"], value["expected"], value["injected"], str(value["framework"]),
                   value["origin"], str(value["basis"]), tuple(tuple(g) for g in groups))


@dataclass(frozen=True)
class Case:
    id: str
    bundle: Bundle
    label: Label


def load_cases(directory) -> list[Case]:
    """Each case is a directory holding evidence.json and label.json."""
    directory = Path(directory)
    folders = sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith(("_", ".")))
    if not folders:
        raise ValueError(f"No case directories in {directory}")
    cases = []
    for folder in folders:
        evidence, label = folder / "evidence.json", folder / "label.json"
        if not evidence.is_file() or not label.is_file():
            raise ValueError(f"{folder.name}: needs evidence.json and label.json")
        try:
            bundle = Bundle.parse(json.loads(evidence.read_text(encoding="utf-8")))
            parsed = Label.parse(json.loads(label.read_text(encoding="utf-8")), bundle, f"{folder.name}/label")
        except ValueError as error:
            raise ValueError(f"{folder.name}: {error}") from None
        cases.append(Case(folder.name, bundle, parsed))
    if len({c.label.split for c in cases}) != 1:
        raise ValueError("Do not mix development and held-out cases in one run")
    return cases


def score(diagnosis: Diagnosis, label: Label) -> dict:
    correct = diagnosis.cause == label.expected
    cited = set(diagnosis.evidence_ids)
    citations_ok = None
    if label.citations and not diagnosis.abstained:
        citations_ok = any(set(group) <= cited for group in label.citations)
    return {"correct": correct, "abstained": diagnosis.abstained,
            "wrong_conclusion": not correct and not diagnosis.abstained,
            "missed_abstention": label.expected == "insufficient_evidence" and not diagnosis.abstained,
            "citations_ok": citations_ok}


def attempt(case: Case, strategy: str, repetition: int, client) -> dict:
    row = {"case": case.id, "strategy": strategy, "repetition": repetition, "expected": case.label.expected,
           "injected": case.label.injected, "framework": case.label.framework, "status": "error",
           "model_calls": 0, "provider_attempts": 0, "usage": {}}
    started = time.perf_counter()
    try:
        outcome = strategies.run(strategy, case.bundle, client)
    except ModelNoAnswer as error:
        row.update(error="no_answer", detail=error.reason, model_calls=1)
    except ContractError:
        row.update(error="contract", model_calls=1)
    except ProviderError as error:
        row.update(error="provider", detail=error.details, model_calls=1,
                   provider_attempts=error.details.get("attempts", 1))
    except Exception as error:  # recorded, not raised: one broken case must not hide the rest
        row.update(error="execution", detail=type(error).__name__)
    else:
        row.update(status="ok", diagnosis=outcome.diagnosis.to_dict(), model_calls=outcome.model_calls,
                   provider_attempts=outcome.provider_attempts, usage=outcome.usage,
                   **score(outcome.diagnosis, case.label))
    row["seconds"] = round(time.perf_counter() - started, 4)
    return row


def majority(rows: list[dict]) -> str | None:
    """The cause given by an absolute majority of all attempts; errors stay in the denominator."""
    counts = Counter(r["diagnosis"]["cause"] for r in rows if r["status"] == "ok")
    if not counts:
        return None
    cause, count = counts.most_common(1)[0]
    return cause if count > len(rows) / 2 else None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(rows: list[dict]) -> dict:
    by_strategy = defaultdict(list)
    for row in rows:
        by_strategy[row["strategy"]].append(row)
    summary = {"strategies": {}, "disagreements": {}}
    majority_correct = {}
    for strategy, data in by_strategy.items():
        answered = [r for r in data if r["status"] == "ok"]
        correct = sum(r["correct"] for r in answered)
        per_case = defaultdict(list)
        for r in data:
            per_case[r["case"]].append(r)
        majority_correct[strategy] = {case: majority(rs) == rs[0]["expected"] for case, rs in per_case.items()}
        frameworks = defaultdict(lambda: [0, 0])
        for r in data:
            frameworks[r["framework"]][0] += 1
            frameworks[r["framework"]][1] += bool(r.get("correct"))
        summary["strategies"][strategy] = {
            "cases": len(per_case),
            "cases_majority_correct": sum(majority_correct[strategy].values()),
            "attempts": len(data),
            "answered": len(answered),
            "correct": correct,
            "accuracy_all_attempts": _rate(correct, len(data)),
            "accuracy_answered": _rate(correct, len(answered)),
            "wrong_conclusions": sum(r["wrong_conclusion"] for r in answered),
            "missed_abstentions": sum(r["missed_abstention"] for r in answered),
            "abstentions": sum(r["abstained"] for r in answered),
            "citation_gaps": sum(r["citations_ok"] is False for r in answered),
            "errors": dict(Counter(r["error"] for r in data if r["status"] == "error")),
            "model_calls": sum(r["model_calls"] for r in data),
            "provider_attempts": sum(r["provider_attempts"] for r in data),
            "total_tokens": sum(r["usage"].get("total_tokens", 0) for r in data),
            "median_seconds": round(statistics.median(r["seconds"] for r in data), 4),
            "by_framework": {k: {"attempts": v[0], "correct": v[1]} for k, v in sorted(frameworks.items())},
        }
    if "rules" in majority_correct:
        for strategy, cases in majority_correct.items():
            if strategy != "rules":
                summary["disagreements"][strategy] = {
                    "only_rules_correct": sorted(c for c, ok in cases.items() if majority_correct["rules"][c] and not ok),
                    f"only_{strategy}_correct": sorted(c for c, ok in cases.items() if ok and not majority_correct["rules"][c]),
                }
    return summary


def render_markdown(summary: dict, run: dict) -> str:
    lines = ["# Replay results", "",
             f"{run['cases']} cases ({run['split']}, {', '.join(run['origins'])}), "
             f"{run['repetitions']} repetition(s). Errors count as misses in the first accuracy column.", "",
             "| Strategy | Attempts | Answered | Accuracy (all) | Accuracy (answered) | Wrong conclusions "
             "| Abstentions | Errors | Model calls |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    pct = lambda v: "n/a" if v is None else f"{100 * v:.0f}%"
    for name, s in summary["strategies"].items():
        errors = sum(s["errors"].values())
        lines.append(f"| {name} | {s['attempts']} | {s['answered']} | {pct(s['accuracy_all_attempts'])} "
                     f"| {pct(s['accuracy_answered'])} | {s['wrong_conclusions']} | {s['abstentions']} "
                     f"| {errors} | {s['model_calls']} |")
    for strategy, diff in summary["disagreements"].items():
        lines += ["", f"Cases where rules and {strategy} differ (majority of attempts):"]
        for key, cases in diff.items():
            lines.append(f"- {key.replace('_', ' ')}: {', '.join(cases) or 'none'}")
    return "\n".join(lines) + "\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest(cases_dir, client=None) -> dict:
    """What a frozen comparison must hold constant. Source hashes ignore CRLF/LF differences."""
    package = Path(__file__).parent
    cases_dir = Path(cases_dir)
    return {
        "schema": 1,
        "python": platform.python_version(),
        "code": {p.name: _sha(p.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
                 for p in sorted(package.glob("*.py"))},
        "prompt_sha256": _sha(llm.PROMPT.encode()),
        "model": client.configuration() if client is not None else None,
        "cases": {p.relative_to(cases_dir).as_posix(): _sha(p.read_bytes())
                  for p in sorted(cases_dir.rglob("*.json"))},
    }


def check_frozen(frozen: dict, current: dict) -> None:
    changed = [k for k in ("schema", "python", "code", "prompt_sha256", "model") if frozen.get(k) != current.get(k)]
    if changed:
        raise ValueError("The frozen manifest differs in: " + ", ".join(changed))


def evaluate(cases_dir, output, strategy_names, client=None, repetitions: int = 1, frozen_manifest=None) -> dict:
    if not strategy_names or len(set(strategy_names)) != len(strategy_names) \
            or not set(strategy_names) <= set(strategies.STRATEGIES):
        raise ValueError(f"Choose distinct strategies from {', '.join(strategies.STRATEGIES)}")
    if type(repetitions) is not int or not 1 <= repetitions <= 10:
        raise ValueError("repetitions must be between 1 and 10")
    if strategies.needs_model(strategy_names) and client is None:
        raise ValueError("The llm and cascade strategies need a configured model client")
    cases = load_cases(cases_dir)
    current = manifest(cases_dir, client)
    if cases[0].label.split == "held-out" and frozen_manifest is None:
        raise ValueError("Held-out runs require --frozen with a manifest from the development run")
    if frozen_manifest is not None:
        check_frozen(json.loads(Path(frozen_manifest).read_text(encoding="utf-8")), current)

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    attempts_dir = output / "attempts"
    attempts_dir.mkdir()

    def save(path: Path, value) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    run = {"strategies": list(strategy_names), "repetitions": repetitions, "cases": len(cases),
           "split": cases[0].label.split, "origins": sorted({c.label.origin for c in cases})}
    save(output / "manifest.json", current)
    save(output / "run.json", run)
    rows = []
    for case in cases:
        for repetition in range(1, repetitions + 1):
            for strategy in strategy_names:
                row = attempt(case, strategy, repetition, client)
                rows.append(row)
                save(attempts_dir / f"{case.id}--{strategy}--{repetition:02d}.json", row)
    summary = summarize(rows)
    save(output / "summary.json", summary)
    (output / "summary.md").write_text(render_markdown(summary, run), encoding="utf-8")
    return summary
