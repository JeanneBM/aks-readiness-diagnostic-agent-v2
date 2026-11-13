import json
import unittest

from readiness_triage.errors import ContractError, ModelNoAnswer, ProviderError
from readiness_triage.llm import HttpResponse, ModelClient, TransportFailure, output_schema

from helpers import case_bundle

BUNDLE = case_bundle("D04-uvicorn-port")


def completion(content, finish="stop", refusal=None):
    message = {"role": "assistant", "content": json.dumps(content) if isinstance(content, dict) else content}
    if refusal:
        message["refusal"] = refusal
    return HttpResponse(200, {}, json.dumps({"choices": [{"message": message, "finish_reason": finish}],
                                             "usage": {"total_tokens": 321, "secret": "x"}}).encode())


ANSWER = {"cause": "probe_port_mismatch", "summary": "Probe 8080, server 8000.",
          "evidence_ids": ["probe", "events", "logs"], "uncertainty": "Log may be stale."}


class Script:
    """A transport that replays scripted responses and records requests."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, url, headers, body, timeout):
        self.requests.append((url, headers, json.loads(body)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(transport, **kwargs):
    sleeps = []
    c = ModelClient(endpoint="https://example.openai.azure.com", api_key="sk-test-secret", model="gpt-x",
                    transport=transport, sleep=sleeps.append, **kwargs)
    return c, sleeps


class Requests(unittest.TestCase):
    def test_schema_has_no_recommendation_and_only_bundle_ids(self):
        schema = output_schema(BUNDLE)
        self.assertNotIn("recommendation", schema["properties"])
        self.assertEqual(set(schema["properties"]["evidence_ids"]["items"]["enum"]), set(BUNDLE.ids))

    def test_azure_request_shape(self):
        script = Script(completion(ANSWER))
        c, _ = client(script)
        result = c.diagnose(BUNDLE)
        url, headers, payload = script.requests[0]
        self.assertTrue(url.startswith("https://example.openai.azure.com/openai/deployments/gpt-x/chat/completions?"))
        self.assertEqual(headers["api-key"], "sk-test-secret")
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(result.diagnosis.cause, "probe_port_mismatch")
        self.assertEqual(result.usage, {"total_tokens": 321})
        self.assertIn("operator", result.diagnosis.recommendation)

    def test_endpoint_validation(self):
        for endpoint in ("http://example.openai.azure.com", "https://evil.example.com",
                         "https://example.openai.azure.com/path", "https://u:p@example.openai.azure.com"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ModelClient(endpoint=endpoint, api_key="k", model="m")


class Retries(unittest.TestCase):
    def test_429_is_retried_after_the_requested_delay(self):
        c, sleeps = client(Script(HttpResponse(429, {"Retry-After": "3"}, b""), completion(ANSWER)))
        result = c.diagnose(BUNDLE)
        self.assertEqual((result.attempts, sleeps), (2, [3]))

    def test_client_errors_are_not_retried(self):
        c, sleeps = client(Script(HttpResponse(400, {}, b"")))
        with self.assertRaises(ProviderError) as caught:
            c.diagnose(BUNDLE)
        self.assertEqual(caught.exception.details, {"http_status": 400, "attempts": 1})
        self.assertEqual(sleeps, [])

    def test_redirects_are_errors_not_followed(self):
        c, _ = client(Script(HttpResponse(302, {"Location": "https://elsewhere"}, b"")))
        with self.assertRaises(ProviderError):
            c.diagnose(BUNDLE)

    def test_transport_failures_stop_after_max_attempts(self):
        c, sleeps = client(Script(*[TransportFailure("timeout")] * 3), max_attempts=3)
        with self.assertRaises(ProviderError) as caught:
            c.diagnose(BUNDLE)
        self.assertEqual(caught.exception.details, {"transport_category": "timeout", "attempts": 3})
        self.assertEqual(sleeps, [2, 4])

    def test_long_retry_after_is_not_absorbed(self):
        c, sleeps = client(Script(HttpResponse(429, {"retry-after": "120"}, b"")))
        with self.assertRaises(ProviderError):
            c.diagnose(BUNDLE)
        self.assertEqual(sleeps, [])


class Answers(unittest.TestCase):
    def run_one(self, response):
        c, _ = client(Script(response))
        return c.diagnose(BUNDLE)

    def test_refusal_and_truncation_are_no_answers(self):
        with self.assertRaises(ModelNoAnswer) as caught:
            self.run_one(completion(ANSWER, refusal="no"))
        self.assertEqual(caught.exception.reason, "refusal")
        with self.assertRaises(ModelNoAnswer) as caught:
            self.run_one(completion(ANSWER, finish="length"))
        self.assertEqual(caught.exception.reason, "truncated")

    def test_invented_evidence_breaks_the_contract(self):
        with self.assertRaises(ContractError):
            self.run_one(completion({**ANSWER, "evidence_ids": ["kubectl-output"]}))

    def test_a_supplied_recommendation_breaks_the_contract(self):
        with self.assertRaises(ContractError):
            self.run_one(completion({**ANSWER, "recommendation": "delete the pod"}))

    def test_non_json_content(self):
        with self.assertRaises(ContractError):
            self.run_one(completion("I think it is the port."))


class Environment(unittest.TestCase):
    def test_from_env_reports_bad_numbers(self):
        with self.assertRaisesRegex(ValueError, "READINESS_MAX_ATTEMPTS"):
            ModelClient.from_env({"READINESS_ENDPOINT": "https://x.openai.azure.com", "READINESS_API_KEY": "k",
                                  "READINESS_MODEL": "m", "READINESS_MAX_ATTEMPTS": "many"})

    def test_configuration_holds_no_secrets(self):
        c, _ = client(Script())
        self.assertNotIn("sk-test-secret", json.dumps(c.configuration()))


if __name__ == "__main__":
    unittest.main()
