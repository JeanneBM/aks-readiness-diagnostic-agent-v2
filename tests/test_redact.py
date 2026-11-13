import time
import unittest

from readiness_triage.redact import redact


class Redaction(unittest.TestCase):
    def test_common_secret_shapes_disappear(self):
        cases = {  # raw text -> the secret that must not survive
            "Authorization: Bearer abc.def-ghi": "abc.def-ghi",
            "curl -H 'X-Trace: 1' -H 'Bearer tok_live_123'": "tok_live_123",
            "api_key=sk-12345": "sk-12345",
            '"password": "hunter2"': "hunter2",
            "https://acct.blob.core.windows.net/c?sv=2024&sig=abcd%2F&se=1": "abcd%2F",
            "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=Zm9v==;": "Zm9v==",
            "https://app:s3cret@db.internal/x": "s3cret",
            "mail ops@example.com": "ops@example.com",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF": "abcDEF",
        }
        for raw, secret in cases.items():
            with self.subTest(raw=raw):
                self.assertNotIn(secret, redact(raw))

    def test_ordinary_log_lines_are_untouched(self):
        line = 'INFO:     10.244.0.1:40312 - "GET /ready HTTP/1.1" 404 Not Found'
        self.assertEqual(redact(line), line)

    def test_long_tokens_stay_fast(self):
        started = time.perf_counter()
        redact("x" * 64_000)
        self.assertLess(time.perf_counter() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
