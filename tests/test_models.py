import unittest

from readiness_triage.models import RECOMMENDATIONS, Bundle, Diagnosis

from helpers import case_bundle, minimal_bundle

LOG = {"id": "logs", "kind": "app_log", "source": "s", "text": "x"}


class BundleContract(unittest.TestCase):
    def test_rejects_unexpected_fields_with_a_useful_message(self):
        with self.assertRaisesRegex(ValueError, "unexpected extra"):
            Bundle.parse({"schema": 2, "target": "t", "observations": [{**LOG, "extra": 1}]})

    def test_rejects_v1_bundles(self):
        with self.assertRaisesRegex(ValueError, "schema must be 2"):
            Bundle.parse({"schema": 1, "target": "t", "observations": [LOG]})

    def test_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            minimal_bundle(LOG, dict(LOG))

    def test_logs_carry_no_structured_data(self):
        with self.assertRaisesRegex(ValueError, "no structured data"):
            minimal_bundle({**LOG, "data": {"a": 1}})

    def test_pod_status_data_is_typed(self):
        status = {"id": "status", "kind": "pod_status", "source": "s", "text": "x",
                  "data": {"container_ready": "yes", "pod_ready": "True", "pod_ready_since": None}}
        with self.assertRaisesRegex(ValueError, "container_ready"):
            minimal_bundle(status)

    def test_timestamps_need_a_zone(self):
        status = {"id": "status", "kind": "pod_status", "source": "s", "text": "x",
                  "data": {"container_ready": True, "pod_ready": "True", "pod_ready_since": "2026-11-13T10:00:00"}}
        with self.assertRaisesRegex(ValueError, "time zone"):
            minimal_bundle(status)

    def test_round_trip(self):
        bundle = case_bundle("D04-uvicorn-port")
        self.assertEqual(Bundle.parse(bundle.to_dict()), bundle)


class DiagnosisContract(unittest.TestCase):
    def setUp(self):
        self.bundle = minimal_bundle(LOG)

    def test_recommendation_is_derived_from_the_cause(self):
        d = Diagnosis("healthy", "s", ("logs",), "u")
        self.assertEqual(d.recommendation, RECOMMENDATIONS["healthy"])
        self.assertEqual(d.to_dict()["recommendation"], RECOMMENDATIONS["healthy"])

    def test_a_recommendation_cannot_be_supplied(self):
        with self.assertRaisesRegex(ValueError, "unexpected recommendation"):
            Diagnosis.parse({"cause": "healthy", "summary": "s", "evidence_ids": ["logs"],
                             "uncertainty": "u", "recommendation": "kubectl delete pod"}, self.bundle)

    def test_conclusions_need_known_evidence(self):
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            Diagnosis.parse({"cause": "healthy", "summary": "s", "evidence_ids": ["nope"], "uncertainty": "u"},
                            self.bundle)
        with self.assertRaisesRegex(ValueError, "must cite evidence"):
            Diagnosis.parse({"cause": "healthy", "summary": "s", "evidence_ids": [], "uncertainty": "u"},
                            self.bundle)

    def test_abstention_may_cite_nothing(self):
        d = Diagnosis.parse({"cause": "insufficient_evidence", "summary": "s", "evidence_ids": [],
                             "uncertainty": "u"}, self.bundle)
        self.assertTrue(d.abstained)


if __name__ == "__main__":
    unittest.main()
