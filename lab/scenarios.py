"""Lab scenarios for a local kind cluster.

Labels are fixed here, before anything is recorded. ``expected`` is what the evidence
the scenario is designed to produce can support; it differs from ``injected`` when the
software logs too little to identify the fault (nginx, Go net/http, a silent app).

Each scenario also states checks on the recorded bundle. If a check fails, the
scenario did not produce the evidence its label assumes, and the recording is set
aside as invalid instead of being scored against a wrong label.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

NAMESPACE = "readiness-lab"
OWNER = {"app.kubernetes.io/managed-by": "readiness-triage-lab"}
UVICORN = "readiness-lab/uvicorn:dev"
GO = "readiness-lab/go:dev"
NGINX = "nginx:1.27-alpine"
ASPNET = "mcr.microsoft.com/dotnet/samples:aspnetapp"
MISSING_DEPENDENCY = f"http://inventory.{NAMESPACE}.svc.cluster.local:8080/health"
PORT_CITATIONS = [["probe", "events", "logs"]]


@dataclass(frozen=True)
class Check:
    observation: str            # an observation id, or "*" for any app_log
    pattern: str | None = None  # None: the observation must merely exist
    present: bool = True


@dataclass(frozen=True)
class Scenario:
    id: str
    framework: str
    injected: str
    expected: str
    basis: str
    image: str
    probe_port: int
    probe_path: str = "/ready"
    args: tuple[str, ...] | None = None
    env: dict = field(default_factory=dict)
    citations: list = field(default_factory=list)
    checks: tuple[Check, ...] = ()

    @property
    def becomes_ready(self) -> bool:
        return self.expected == "healthy"

    @property
    def pod_name(self) -> str:
        return self.id.lower()


SCENARIOS = (
    Scenario("R01-nginx-healthy", "nginx", "healthy", "healthy", "Stock nginx, probe on 80.",
             NGINX, 80, "/", citations=[["status"]]),
    Scenario("R02-nginx-silent-port", "nginx", "probe_port_mismatch", "insufficient_evidence",
             "Stock nginx listens on 80 and does not log it; probe on 8080.", NGINX, 8080, "/",
             checks=(Check("*", r"Uvicorn running|Now listening on|Listening at:", present=False),)),
    Scenario("R03-aspnet-healthy", "aspnetcore", "healthy", "healthy",
             "Microsoft's aspnetapp sample on its default port 8080.", ASPNET, 8080, "/",
             citations=[["status"]], checks=(Check("*", r"Now listening on: http://\[::\]:8080"),)),
    Scenario("R04-aspnet-port-dotnet8", "aspnetcore", "probe_port_mismatch", "probe_port_mismatch",
             ".NET 8+ listens on 8080 by default; probe left on the old default 80.", ASPNET, 80, "/",
             citations=PORT_CITATIONS, checks=(Check("*", r"Now listening on: http://\[::\]:8080"),)),
    Scenario("R05-uvicorn-healthy", "uvicorn", "healthy", "healthy", "Lab app, default uvicorn logging.",
             UVICORN, 8000, citations=[["status"]]),
    Scenario("R06-uvicorn-loopback", "uvicorn", "listener_loopback_only", "listener_loopback_only",
             "uvicorn started with --host 127.0.0.1.", UVICORN, 8000,
             args=("uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000"), citations=PORT_CITATIONS,
             checks=(Check("*", r"Uvicorn running on http://127\.0\.0\.1:8000"),)),
    Scenario("R07-uvicorn-port", "uvicorn", "probe_port_mismatch", "probe_port_mismatch",
             "Server on 0.0.0.0:8000, probe on 8080.", UVICORN, 8080, citations=PORT_CITATIONS,
             checks=(Check("*", r"Uvicorn running on http://0\.0\.0\.0:8000"),)),
    Scenario("R08-uvicorn-path-404-only", "uvicorn", "probe_path_mismatch", "insufficient_evidence",
             "Probe on /healthz, app serves /ready; default logs show only the 404.", UVICORN, 8000, "/healthz",
             checks=(Check("*", r'"GET /healthz HTTP/1\.1" 404'),)),
    Scenario("R09-uvicorn-dependency-logged", "uvicorn", "dependency_unavailable", "dependency_unavailable",
             "Dependency host does not resolve; the app logs the urllib error.", UVICORN, 8000,
             env={"DEPENDENCY_URL": MISSING_DEPENDENCY}, citations=[["events", "logs"]],
             checks=(Check("*", r"readiness check failed"), Check("events", r"statuscode: 503"))),
    Scenario("R10-uvicorn-dependency-silent", "uvicorn", "dependency_unavailable", "insufficient_evidence",
             "Dependency host does not resolve; the app does not log why.", UVICORN, 8000,
             env={"DEPENDENCY_URL": MISSING_DEPENDENCY, "LOG_DEPENDENCY_ERRORS": "0"},
             checks=(Check("*", r"readiness check failed", present=False), Check("events", r"statuscode: 503"))),
    Scenario("R11-uvicorn-long-log-port", "uvicorn", "probe_port_mismatch", "probe_port_mismatch",
             "R07 with 40 kB of log after startup: the listener line must survive in the log prefix. "
             "This checks the collector's --limit-bytes reading on a real kubelet.",
             UVICORN, 8080, env={"FLOOD_BYTES": "40000"}, citations=[["probe", "events", "startup"]],
             checks=(Check("startup", r"Uvicorn running on http://0\.0\.0\.0:8000"), Check("logs-truncated"))),
    Scenario("R12-go-silent-port", "go-net-http", "probe_port_mismatch", "insufficient_evidence",
             "net/http on :8080 logs nothing; probe on 9090.", GO, 9090,
             checks=(Check("logs-empty"),)),
    Scenario("R13-go-healthy", "go-net-http", "healthy", "healthy", "net/http on :8080, probe on 8080.",
             GO, 8080, citations=[["status"]]),
)


def pod_manifest(s: Scenario) -> dict:
    container = {"name": "app", "image": s.image, "imagePullPolicy": "IfNotPresent",
                 "readinessProbe": {"httpGet": {"path": s.probe_path, "port": s.probe_port},
                                    "periodSeconds": 3, "timeoutSeconds": 5, "failureThreshold": 1}}
    if s.args:
        container["args"] = list(s.args)
    if s.env:
        container["env"] = [{"name": k, "value": v} for k, v in s.env.items()]
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": s.pod_name, "namespace": NAMESPACE, "labels": dict(OWNER)},
            "spec": {"containers": [container], "terminationGracePeriodSeconds": 1}}


def check(s: Scenario, bundle) -> list[str]:
    """Problems that make the recording unfit for its label; empty means valid."""
    problems = []
    status = next((o for o in bundle.observations if o.kind == "pod_status"), None)
    ready = status.data["container_ready"] if status else None
    if ready is not s.becomes_ready:
        problems.append(f"container ready={ready}, scenario expects ready={s.becomes_ready}")
    if not s.becomes_ready and "events" not in bundle.ids:
        problems.append("no readiness failure Events were collected")
    for c in s.checks:
        texts = [o.text for o in bundle.observations
                 if o.id == c.observation or (c.observation == "*" and o.kind == "app_log")]
        if c.pattern is None:
            found = bool(texts)
        else:
            found = any(re.search(c.pattern, t) for t in texts)
        if found is not c.present:
            what = c.pattern or "the observation"
            problems.append(f"{c.observation}: {what!r} {'missing' if c.present else 'unexpectedly present'}")
    return problems
