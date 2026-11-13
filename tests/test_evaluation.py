import json
from pathlib import Path
import shutil
import tempfile
import unittest

from readiness_triage.errors import ProviderError
from readiness_triage.evaluation import check_frozen, evaluate, load_cases, manifest, majority
from readiness_triage.llm import ModelResult
from readiness_triage.models import Diagnosis

from helpers import DEV


class Flaky:
    """Answers correctly every other call and fails with a provider error otherwise."""

    def __init__(self):
        self.calls = 0

    def configuration(self):
        return {"provider": "fake"}

    def diagnose(self, bundle):
        self.calls += 1
        if self.calls % 2:
            raise ProviderError("Provider returned HTTP 429", http_status=429, attempts=3)
        return ModelResult(Diagnosis("insufficient_evidence", "s", (), "u"), {"total_tokens": 10}, 1)


class Evaluation(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp)

    def test_rules_replay_writes_a_complete_report(self):
        summary = evaluate(DEV, self.temp / "run", ["rules"])
        rules = summary["strategies"]["rules"]
        self.assertEqual((rules["attempts"], rules["answered"], rules["model_calls"]), (19, 19, 0))
        for name in ("manifest.json", "run.json", "summary.json", "summary.md"):
            self.assertTrue((self.temp / "run" / name).is_file())
        self.assertEqual(len(list((self.temp / "run" / "attempts").iterdir())), 19)

    def test_existing_output_is_never_reused(self):
        (self.temp / "run").mkdir()
        with self.assertRaises(FileExistsError):
            evaluate(DEV, self.temp / "run", ["rules"])

    def test_errors_count_as_misses_only_in_the_all_attempts_column(self):
        summary = evaluate(DEV, self.temp / "run", ["rules", "cascade"], Flaky())
        cascade = summary["strategies"]["cascade"]
        self.assertEqual(cascade["errors"], {"provider": 4})
        self.assertEqual(cascade["provider_attempts"], 4 * 3 + 3)
        self.assertLess(cascade["accuracy_all_attempts"], cascade["accuracy_answered"])
        self.assertIn("only_rules_correct", summary["disagreements"]["cascade"])

    def test_model_strategies_need_a_client(self):
        with self.assertRaisesRegex(ValueError, "need a configured model client"):
            evaluate(DEV, self.temp / "run", ["llm"])

    def test_labels_must_cite_known_evidence(self):
        cases = self.temp / "cases"
        shutil.copytree(DEV / "D04-uvicorn-port", cases / "D04")
        label = json.loads((cases / "D04" / "label.json").read_text())
        label["citations"] = [["probe", "kubectl-describe"]]
        (cases / "D04" / "label.json").write_text(json.dumps(label))
        with self.assertRaisesRegex(ValueError, "D04: .*kubectl-describe"):
            load_cases(cases)

    def test_held_out_requires_a_frozen_manifest(self):
        cases = self.temp / "held"
        shutil.copytree(DEV / "D04-uvicorn-port", cases / "H01")
        label = json.loads((cases / "H01" / "label.json").read_text())
        (cases / "H01" / "label.json").write_text(json.dumps({**label, "split": "held-out"}))
        with self.assertRaisesRegex(ValueError, "--frozen"):
            evaluate(cases, self.temp / "run", ["rules"])

    def test_frozen_manifest_detects_code_or_prompt_changes(self):
        current = manifest(DEV)
        check_frozen(current, current)
        with self.assertRaisesRegex(ValueError, "prompt_sha256"):
            check_frozen({**current, "prompt_sha256": "0" * 64}, current)

    def test_majority_needs_more_than_half_of_all_attempts(self):
        ok = lambda cause: {"status": "ok", "diagnosis": {"cause": cause}}
        self.assertEqual(majority([ok("healthy"), ok("healthy"), {"status": "error"}]), "healthy")
        self.assertIsNone(majority([ok("healthy"), {"status": "error"}]))


if __name__ == "__main__":
    unittest.main()
