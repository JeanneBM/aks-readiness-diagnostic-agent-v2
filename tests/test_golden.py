import unittest

from readiness_triage.signals import extract

from helpers import ROOT, minimal_bundle


def signals(name):
    text = (ROOT / "tests" / "golden" / name).read_text(encoding="utf-8")
    return extract(minimal_bundle({"id": "logs", "kind": "app_log", "source": "golden", "text": text}))


def listeners(s):
    return [(l.framework, l.host, l.port, l.loopback) for l in s.listeners]


class GoldenLogs(unittest.TestCase):
    def test_uvicorn(self):
        s = signals("uvicorn-0.32.1-lab-app.log")
        self.assertEqual(listeners(s), [("uvicorn", "127.0.0.1", 8765, True)])
        self.assertEqual(s.dependency_log_ids, ("logs",))

    def test_flask_default_is_loopback_only(self):
        self.assertEqual(listeners(signals("flask-3.1.0-default.log")), [("werkzeug", "127.0.0.1", 5055, True)])

    def test_flask_all_addresses_is_not_loopback_only(self):
        found = listeners(signals("flask-3.1.0-all-addresses.log"))
        self.assertEqual({(port, loopback) for _, _, port, loopback in found}, {(5056, True), (5056, False)})

    def test_gunicorn(self):
        self.assertEqual(listeners(signals("gunicorn-23.0.0.log")), [("gunicorn", "0.0.0.0", 8077, False)])


if __name__ == "__main__":
    unittest.main()
