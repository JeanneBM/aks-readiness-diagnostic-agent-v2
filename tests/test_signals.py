import unittest

from readiness_triage.signals import extract, parse_failure

from helpers import case_bundle, minimal_bundle


def log_bundle(text):
    return minimal_bundle({"id": "logs", "kind": "app_log", "source": "s", "text": text})


class Listeners(unittest.TestCase):
    def listeners(self, text):
        return [(l.framework, l.host, l.port, l.loopback) for l in extract(log_bundle(text)).listeners]

    def test_default_framework_lines(self):
        self.assertEqual(self.listeners("INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)"),
                         [("uvicorn", "0.0.0.0", 8000, False)])
        self.assertEqual(self.listeners("[INFO] Listening at: http://127.0.0.1:8000 (1)"),
                         [("gunicorn", "127.0.0.1", 8000, True)])
        self.assertEqual(self.listeners("      Now listening on: http://[::]:8080"),
                         [("aspnetcore", "[::]", 8080, False)])
        self.assertEqual(self.listeners("      Now listening on: http://localhost:5000"),
                         [("aspnetcore", "localhost", 5000, True)])

    def test_both_spring_boot_formats(self):
        for line in ("Tomcat started on port 8080 (http) with context path '/'",
                     "Tomcat started on port(s): 8080 (http) with context path ''"):
            self.assertEqual(self.listeners(line), [("spring-tomcat", None, 8080, False)])

    def test_werkzeug_all_interfaces_is_not_loopback_only(self):
        found = self.listeners(" * Running on http://127.0.0.1:5000\n * Running on http://10.244.0.9:5000")
        self.assertEqual({f[3] for f in found}, {True, False})


class Routes(unittest.TestCase):
    def test_actuator_base_is_relative_to_context_path(self):
        routes = extract(log_bundle("Exposing 1 endpoint beneath base path '/actuator'\n"
                                    "Tomcat started on port 8080 (http) with context path '/api'")).routes
        self.assertEqual({(r.kind, r.prefix) for r in routes}, {("context_path", "/api"), ("actuator_base", "/api/actuator")})

    def test_root_context_path_is_no_prefix(self):
        self.assertEqual(extract(log_bundle("with context path '/'")).routes, ())

    def test_spring_boot_2_plural_wording(self):
        routes = extract(log_bundle("Exposing 2 endpoint(s) beneath base path '/manage'")).routes
        self.assertEqual([r.prefix for r in routes], ["/manage"])


class Failures(unittest.TestCase):
    def test_refused_names_port_and_path(self):
        f = parse_failure('Readiness probe failed: Get "http://10.244.0.7:8080/ready": dial tcp '
                          '10.244.0.7:8080: connect: connection refused', 3, None, "events")
        self.assertEqual((f.kind, f.port, f.path), ("refused", 8080, "/ready"))

    def test_ipv6_pod_ip(self):
        f = parse_failure('Readiness probe failed: Get "http://[fd00::1]:9000/": dial tcp [fd00::1]:9000: '
                          'connect: connection refused', 1, None, "events")
        self.assertEqual(f.port, 9000)

    def test_status_and_timeout(self):
        self.assertEqual(parse_failure("Readiness probe failed: HTTP probe failed with statuscode: 503",
                                       1, None, "e").status, 503)
        self.assertEqual(parse_failure('Readiness probe failed: Get "http://1.2.3.4:80/": context deadline '
                                       'exceeded (Client.Timeout exceeded while awaiting headers)',
                                       1, None, "e").kind, "timeout")


class Timing(unittest.TestCase):
    def test_failures_before_the_ready_transition_are_not_current(self):
        s = extract(case_bundle("D02-uvicorn-healthy-after-startup-failures"))
        self.assertEqual(len(s.failures), 1)
        self.assertEqual(s.current_failures(), ())

    def test_failures_against_an_older_probe_port_are_not_current(self):
        s = extract(case_bundle("D19-stale-failure-other-port"))
        self.assertEqual(s.current_failures(), ())


if __name__ == "__main__":
    unittest.main()
