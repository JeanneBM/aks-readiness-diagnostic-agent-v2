import unittest

from readiness_triage import rules, strategies
from readiness_triage.models import Bundle

from helpers import DEV, case_bundle, case_label


class FixtureRegression(unittest.TestCase):
    """Guards rule behaviour on the synthetic fixtures. It shows consistency with
    cases written alongside the rules, not accuracy on unseen evidence."""

    def test_every_dev_fixture(self):
        for folder in sorted(p for p in DEV.iterdir() if p.is_dir()):
            with self.subTest(case=folder.name):
                diagnosis = rules.diagnose(case_bundle(folder.name))
                label = case_label(folder.name)
                self.assertEqual(diagnosis.cause, label["expected"])
                if label["citations"]:
                    self.assertTrue(any(set(g) <= set(diagnosis.evidence_ids) for g in label["citations"]))


class Behaviour(unittest.TestCase):
    def test_injected_instructions_do_not_change_the_rules(self):
        self.assertEqual(rules.diagnose(case_bundle("D18-prompt-injection-in-logs")).cause, "probe_port_mismatch")

    def test_flapping_ready_container_is_not_called_healthy(self):
        bundle = case_bundle("D02-uvicorn-healthy-after-startup-failures").to_dict()
        bundle["observations"][2]["data"]["events"][0]["last_seen"] = "2026-11-13T10:00:59Z"
        self.assertEqual(rules.diagnose(Bundle.parse(bundle)).cause, "insufficient_evidence")

    def test_actuator_prefix_ignored_for_non_health_paths(self):
        bundle = case_bundle("D10-spring-actuator-base").to_dict()
        bundle["observations"][1]["data"]["path"] = "/orders"
        self.assertEqual(rules.diagnose(Bundle.parse(bundle)).cause, "insufficient_evidence")


class Strategies(unittest.TestCase):
    class Exploding:
        def diagnose(self, bundle):
            raise AssertionError("the model must not be called")

    def test_cascade_skips_the_model_when_rules_conclude(self):
        outcome = strategies.run("cascade", case_bundle("D04-uvicorn-port"), self.Exploding())
        self.assertEqual((outcome.diagnosis.cause, outcome.model_calls), ("probe_port_mismatch", 0))

    def test_cascade_without_a_client_fails_only_when_the_model_is_needed(self):
        with self.assertRaisesRegex(ValueError, "needs a configured model client"):
            strategies.run("cascade", case_bundle("D14-go-silent-port"))

    def test_unknown_strategy(self):
        with self.assertRaisesRegex(ValueError, "Unknown strategy"):
            strategies.run("A1", case_bundle("D04-uvicorn-port"))


if __name__ == "__main__":
    unittest.main()
