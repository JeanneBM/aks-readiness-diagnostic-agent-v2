import json
import subprocess
import unittest

from readiness_triage.collector import LOG_BYTES, collect

POD_UID = "0b8c7d4e-1111-2222-3333-444455556666"


def pod(containers=("app",), probe=None, ready=False):
    probe = probe if probe is not None else {"httpGet": {"path": "/ready", "port": "http"}}
    specs = []
    for name in containers:
        spec = {"name": name, "image": "x", "ports": [{"name": "http", "containerPort": 8000}]}
        if name == "app" and probe:
            spec["readinessProbe"] = probe
        specs.append(spec)
    return {"metadata": {"uid": POD_UID},
            "spec": {"containers": specs},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False",
                                       "lastTransitionTime": "2026-11-13T10:00:05Z"}],
                       "containerStatuses": [{"name": n, "ready": ready} for n in containers]}}


def event(field_path, message="Readiness probe failed: HTTP probe failed with statuscode: 503", count=4,
          last="2026-11-13T10:00:50Z"):
    involved = {"kind": "Pod", "uid": POD_UID}
    if field_path is not None:
        involved["fieldPath"] = field_path
    return {"reason": "Unhealthy", "message": message, "count": count, "lastTimestamp": last,
            "involvedObject": involved}


class FakeKubectl:
    def __init__(self, pod_json, events=(), log=b"INFO:     Uvicorn running on http://0.0.0.0:8000\n",
                 recent=b"recent line\n", fail=None):
        self.pod, self.events, self.log, self.recent, self.fail = pod_json, list(events), log, recent, fail
        self.calls = []

    def __call__(self, cmd, capture_output, timeout, check):
        self.calls.append(cmd)
        verb = cmd[cmd.index("--request-timeout=15s") + 1]
        if self.fail == verb:
            return subprocess.CompletedProcess(cmd, 1, b"", b'Error from server (NotFound): pods "web" not found\n')
        if verb == "get":
            out = json.dumps({"items": self.events} if "events" in cmd else self.pod).encode()
        elif any(a.startswith("--since") for a in cmd):
            out = self.recent
        else:
            limit = int(next(a for a in cmd if a.startswith("--limit-bytes=")).split("=")[1])
            out = self.log[:limit]
        return subprocess.CompletedProcess(cmd, 0, out, b"")


def run(fake, container="app"):
    return collect("kind-lab", "lab", "web", container, run=fake)


class Collector(unittest.TestCase):
    def test_named_port_is_resolved_and_names_stay_out_of_the_bundle(self):
        bundle = run(FakeKubectl(pod(), [event("spec.containers{app}")]))
        probe = next(o for o in bundle.observations if o.id == "probe")
        self.assertEqual(probe.data, {"scheme": "HTTP", "port": 8000, "path": "/ready"})
        dumped = json.dumps(bundle.to_dict())
        self.assertNotIn("web", dumped)
        self.assertNotIn("kind-lab", dumped)

    def test_events_are_attributed_by_field_path(self):
        fake = FakeKubectl(pod(("app", "sidecar")), [event("spec.containers{sidecar}", count=9),
                                                      event("spec.containers{app}", count=4)])
        bundle = run(fake)
        failures = next(o for o in bundle.observations if o.kind == "probe_failures")
        self.assertEqual([e["count"] for e in failures.data["events"]], [4])
        self.assertNotIn("container-scope", bundle.ids)

    def test_unattributed_events_in_multi_container_pods_are_flagged(self):
        bundle = run(FakeKubectl(pod(("app", "sidecar")), [event(None)]))
        self.assertIn("container-scope", bundle.ids)

    def test_unattributed_events_in_single_container_pods_are_kept(self):
        bundle = run(FakeKubectl(pod(), [event(None)]))
        self.assertIn("events", bundle.ids)

    def test_short_log_is_sent_once(self):
        fake = FakeKubectl(pod(), [event("spec.containers{app}")])
        bundle = run(fake)
        self.assertEqual([o.id for o in bundle.of_kind("app_log")], ["logs"])
        self.assertEqual(sum("logs" in c for c in fake.calls), 1)

    def test_long_log_keeps_its_start_and_the_recent_tail(self):
        log = b"INFO:     Uvicorn running on http://0.0.0.0:8000\n" + b"x" * (2 * LOG_BYTES)
        bundle = run(FakeKubectl(pod(), [event("spec.containers{app}")], log=log))
        startup = next(o for o in bundle.observations if o.id == "startup")
        self.assertTrue(startup.text.startswith("INFO:     Uvicorn running"))
        self.assertEqual(len(startup.text.encode()), LOG_BYTES)
        self.assertTrue({"recent", "logs-truncated"} <= bundle.ids)

    def test_secrets_in_logs_are_redacted(self):
        bundle = run(FakeKubectl(pod(), log=b"connecting with password=hunter2 to db\n"))
        self.assertIn("password=[REDACTED]", next(o for o in bundle.observations if o.id == "logs").text)

    def test_unsupported_probe_is_a_limitation(self):
        bundle = run(FakeKubectl(pod(probe={"tcpSocket": {"port": 8000}})))
        self.assertIn("probe-unsupported", bundle.ids)

    def test_kubectl_errors_are_reported(self):
        with self.assertRaisesRegex(ValueError, "NotFound"):
            run(FakeKubectl(pod(), fail="get"))

    def test_unknown_container(self):
        with self.assertRaisesRegex(ValueError, "containers: app"):
            run(FakeKubectl(pod()), container="db")

    def test_target_names_are_validated(self):
        with self.assertRaisesRegex(ValueError, "Invalid Kubernetes pod name"):
            collect("kind-lab", "lab", "--all", "app", run=FakeKubectl(pod()))


if __name__ == "__main__":
    unittest.main()
