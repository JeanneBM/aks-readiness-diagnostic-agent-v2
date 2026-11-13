import json
from pathlib import Path

from readiness_triage.models import Bundle

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "cases" / "dev"


def case_bundle(case_id: str) -> Bundle:
    return Bundle.parse(json.loads((DEV / case_id / "evidence.json").read_text(encoding="utf-8")))


def case_label(case_id: str) -> dict:
    return json.loads((DEV / case_id / "label.json").read_text(encoding="utf-8"))


def minimal_bundle(*observations) -> Bundle:
    obs = list(observations) or [{"id": "logs", "kind": "app_log", "source": "test", "text": "hello"}]
    return Bundle.parse({"schema": 2, "target": "t", "observations": obs})
