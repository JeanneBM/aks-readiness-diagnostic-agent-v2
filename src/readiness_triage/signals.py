"""Turn a bundle into typed signals, once, before any rule runs.

The log patterns are the lines frameworks print with their *default* logging
configuration. None of them come from instrumentation written for the lab, so
the rules see what an operator sees on a real cluster. Adding a framework means
adding a pattern here, not editing a rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from .models import Bundle, parse_time

_HOST = r"(?P<host>\[[^\]]+\]|[^\s:/]+)"
_PORT = r"(?P<port>\d{1,5})"

# (framework, pattern). A pattern without a host group means "all interfaces".
LISTENER_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
    ("uvicorn", re.compile(rf"Uvicorn running on https?://{_HOST}:{_PORT}")),
    # [2026-11-13 10:00:01 +0000] [1] [INFO] Listening at: http://0.0.0.0:8000 (1)
    ("gunicorn", re.compile(rf"Listening at: https?://{_HOST}:{_PORT}")),
    #  * Running on http://127.0.0.1:5000
    ("werkzeug", re.compile(rf"\* Running on https?://{_HOST}:{_PORT}")),
    # info: Microsoft.Hosting.Lifetime[14] / Now listening on: http://[::]:8080
    ("aspnetcore", re.compile(rf"Now listening on: https?://{_HOST}:{_PORT}")),
    # Tomcat started on port 8080 (http) with context path '/'      (Spring Boot 3.2+)
    # Tomcat started on port(s): 8080 (http) with context path ''   (Spring Boot 2.x)
    ("spring-tomcat", re.compile(rf"Tomcat started on port(?:\(s\))?:? {_PORT}")),
)

# Route prefixes an application reports by default.
CONTEXT_PATH = re.compile(r"with context path '(?P<prefix>[^']*)'")
ACTUATOR_BASE = re.compile(r"Exposing \d+ endpoints?(?:\(s\))? beneath base path '(?P<prefix>[^']+)'")

# Outbound connection failures as the OS, runtime or HTTP client words them.
DEPENDENCY_ERROR = re.compile(
    r"(?i)connection refused|name or service not known|no such host|temporary failure in name resolution"
    r"|i/o timeout|connection timed out|read timed out|connection reset|could not connect"
    r"|ECONNREFUSED|ENOTFOUND|ETIMEDOUT|EAI_AGAIN"
)

# Kubelet readiness failure messages.
_PROBE_URL = re.compile(r'Get "(?P<scheme>https?)://[^"/]*?:(?P<port>\d{1,5})(?P<path>/[^"]*)?"')
_STATUS = re.compile(r"statuscode: (?P<code>\d{3})")
_REFUSED = re.compile(r"connect: connection refused")
_TIMEOUT = re.compile(r"(?i)context deadline exceeded|Client\.Timeout|i/o timeout")

LOOPBACK_HOSTS = {"localhost", "[::1]", "::1"}


def is_loopback(host: str | None) -> bool:
    return host is not None and (host in LOOPBACK_HOSTS or host.startswith("127."))


@dataclass(frozen=True)
class Listener:
    framework: str
    port: int
    host: str | None
    evidence_id: str

    @property
    def loopback(self) -> bool:
        return is_loopback(self.host)


@dataclass(frozen=True)
class Route:
    kind: str          # "context_path" or "actuator_base"
    prefix: str
    evidence_id: str


@dataclass(frozen=True)
class Failure:
    kind: str          # "refused", "status", "timeout" or "other"
    status: int | None
    port: int | None   # port named in the kubelet message, when it names one
    path: str | None
    count: int
    last_seen: datetime | None
    evidence_id: str


@dataclass(frozen=True)
class Signals:
    status_id: str | None
    container_ready: bool | None
    ready_since: datetime | None   # only set when the Pod condition matches the container
    probe_id: str | None
    probe_port: int | None         # None when absent or an unresolved named port
    probe_path: str | None
    failures: tuple[Failure, ...]
    listeners: tuple[Listener, ...]
    routes: tuple[Route, ...]
    dependency_log_ids: tuple[str, ...]
    limitation_ids: frozenset[str]

    def current_failures(self) -> tuple[Failure, ...]:
        """Failures that bear on the present state.

        A failure naming another port than the current probe was recorded against an
        older configuration. When the container is Ready, failures from before the
        Ready transition belong to startup and do not contradict it. A failure
        without a timestamp is always treated as current.
        """
        result = []
        for failure in self.failures:
            if failure.port is not None and self.probe_port is not None and failure.port != self.probe_port:
                continue
            if (self.container_ready is True and self.ready_since is not None
                    and failure.last_seen is not None and failure.last_seen < self.ready_since):
                continue
            result.append(failure)
        return tuple(result)


def parse_failure(message: str, count: int, last_seen: str | None, evidence_id: str) -> Failure:
    url = _PROBE_URL.search(message)
    status = _STATUS.search(message)
    if _REFUSED.search(message):
        kind = "refused"
    elif status:
        kind = "status"
    elif _TIMEOUT.search(message):
        kind = "timeout"
    else:
        kind = "other"
    return Failure(kind, int(status["code"]) if status else None,
                   int(url["port"]) if url else None, url["path"] if url and url["path"] else None,
                   count, parse_time(last_seen), evidence_id)


def extract(bundle: Bundle) -> Signals:
    status = next(iter(bundle.of_kind("pod_status")), None)
    container_ready = ready_since = None
    if status is not None:
        container_ready = status.data["container_ready"]
        pod_ready = status.data["pod_ready"]
        # A Pod-level transition time describes this container only if they agree.
        if container_ready is not None and pod_ready == ("True" if container_ready else "False"):
            ready_since = parse_time(status.data["pod_ready_since"])

    probe = next(iter(bundle.of_kind("probe_config")), None)
    probe_port = probe_path = None
    if probe is not None:
        probe_port = probe.data["port"] if type(probe.data["port"]) is int else None
        probe_path = probe.data["path"]

    failures = tuple(parse_failure(e["message"], e["count"], e["last_seen"], o.id)
                     for o in bundle.of_kind("probe_failures") for e in o.data["events"])

    listeners, routes, dependency_logs = [], [], []
    for log in bundle.of_kind("app_log"):
        for framework, pattern in LISTENER_PATTERNS:
            for match in pattern.finditer(log.text):
                host = match.groupdict().get("host")
                listeners.append(Listener(framework, int(match["port"]), host, log.id))
        context = None
        for match in CONTEXT_PATH.finditer(log.text):
            context = match["prefix"].rstrip("/")
            if context:
                routes.append(Route("context_path", context, log.id))
        for match in ACTUATOR_BASE.finditer(log.text):
            # Actuator's base path is relative to the servlet context path.
            routes.append(Route("actuator_base", (context or "") + match["prefix"], log.id))
        if DEPENDENCY_ERROR.search(log.text):
            dependency_logs.append(log.id)

    return Signals(
        status_id=status.id if status else None,
        container_ready=container_ready,
        ready_since=ready_since,
        probe_id=probe.id if probe else None,
        probe_port=probe_port,
        probe_path=probe_path,
        failures=failures,
        listeners=tuple(listeners),
        routes=tuple(routes),
        dependency_log_ids=tuple(dict.fromkeys(dependency_logs)),
        limitation_ids=frozenset(o.id for o in bundle.of_kind("limitation")),
    )
