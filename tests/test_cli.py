import contextlib
import io
from pathlib import Path
import shutil
import tempfile
import unittest

from readiness_triage.cli import main

from helpers import DEV


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Cli(unittest.TestCase):
    def test_diagnose_with_rules(self):
        code, out, _ = call("diagnose", str(DEV / "D03-uvicorn-loopback" / "evidence.json"))
        self.assertEqual(code, 0)
        self.assertIn('"listener_loopback_only"', out)
        self.assertIn('"recommendation"', out)

    def test_model_calls_need_explicit_permission(self):
        code, _, err = call("diagnose", str(DEV / "D03-uvicorn-loopback" / "evidence.json"), "--strategy", "llm")
        self.assertEqual(code, 2)
        self.assertIn("--allow-model-calls", err)

    def test_cluster_reads_need_explicit_permission(self):
        code, _, err = call("collect", "--context", "kind-x", "--namespace", "n", "--pod", "p",
                            "--container", "c", "--output", "out.json")
        self.assertEqual(code, 2)
        self.assertIn("--allow-cluster-read", err)

    def test_errors_say_what_went_wrong(self):
        temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp)
        bad = temp / "bundle.json"
        bad.write_text('{"schema": 1, "target": "t", "observations": []}')
        code, _, err = call("diagnose", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("schema must be 2", err)

    def test_evaluate_prints_the_report(self):
        temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp)
        code, out, _ = call("evaluate", "--cases", str(DEV), "--output", str(temp / "run"))
        self.assertEqual(code, 0)
        self.assertIn("| rules | 19 |", out)


if __name__ == "__main__":
    unittest.main()
