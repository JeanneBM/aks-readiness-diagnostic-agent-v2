import sys
import unittest

from helpers import ROOT, case_bundle

sys.path.insert(0, str(ROOT / "lab"))
from scenarios import SCENARIOS, check, pod_manifest  # noqa: E402

COLLECTOR_IDS = {"status", "probe", "events", "logs", "startup", "recent"}


class Scenarios(unittest.TestCase):
    def test_labels_cite_only_what_the_collector_produces(self):
        for s in SCENARIOS:
            with self.subTest(s.id):
                self.assertTrue({i for g in s.citations for i in g} <= COLLECTOR_IDS)
                if s.expected == "insufficient_evidence":
                    self.assertEqual(s.citations, [])

    def test_ids_and_pod_names_are_unique(self):
        self.assertEqual(len({s.pod_name for s in SCENARIOS}), len(SCENARIOS))

    def test_manifest_shape(self):
        m = pod_manifest(next(s for s in SCENARIOS if s.id == "R10-uvicorn-dependency-silent"))
        container = m["spec"]["containers"][0]
        self.assertEqual(container["name"], "app")
        self.assertEqual(container["readinessProbe"]["timeoutSeconds"], 5)
        self.assertIn({"name": "LOG_DEPENDENCY_ERRORS", "value": "0"}, container["env"])

    def test_checks_accept_matching_evidence_and_reject_mismatches(self):
        r07 = next(s for s in SCENARIOS if s.id == "R07-uvicorn-port")
        self.assertEqual(check(r07, case_bundle("D04-uvicorn-port")), [])
        problems = check(r07, case_bundle("D03-uvicorn-loopback"))
        self.assertTrue(any("missing" in p for p in problems))
        r12 = next(s for s in SCENARIOS if s.id == "R12-go-silent-port")
        self.assertEqual(check(r12, case_bundle("D14-go-silent-port")), [])
        r05 = next(s for s in SCENARIOS if s.id == "R05-uvicorn-healthy")
        self.assertTrue(any("ready=False" in p for p in check(r05, case_bundle("D04-uvicorn-port"))))


if __name__ == "__main__":
    unittest.main()
