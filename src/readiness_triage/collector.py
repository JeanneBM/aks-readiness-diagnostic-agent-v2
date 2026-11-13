"""Bounded, read-only kubectl reads for one container.

Differences from v1:
* Probe failure Events are attributed to the container through
  ``involvedObject.fieldPath`` (``spec.containers{name}``), which the kubelet sets,
  so multi-container Pods no longer force an abstention.
* Event counts and last-seen times and the Ready transition time are kept, so rules
  can tell startup failures from current ones.
* The start of the log is read with ``--limit-bytes`` (the kubelet reads forward and
  stops after N bytes). If the whole log fits, it is sent once instead of twice.
"""
from __future__ import annotations

import json
import re
import subprocess

from .models import Bundle, BUNDLE_SCHEMA
from .redact import redact

NAME = re.compile(r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?\Z")
LOG_BYTES = 16_000
MAX_RESPONSE_BYTES = 2_000_000
MAX_EVENTS = 10


def _event_time(item: dict) -> str | None:
    return ((item.get("series") or {}).get("lastObservedTime") or item.get("lastTimestamp")
            or item.get("eventTime") or (item.get("metadata") or {}).get("creationTimestamp"))


def _count(item: dict) -> int:
    value = (item.get("series") or {}).get("count") or item.get("count") or 1
    return value if type(value) is int and value >= 1 else 1


class Kubectl:
    def __init__(self, context: str, namespace: str, run=subprocess.run):
        self.base = ["kubectl", "--context", context, "--namespace", namespace, "--request-timeout=15s"]
        self._run = run

    def read(self, args: list[str], *, optional: bool = False) -> bytes | None:
        try:
            result = self._run([*self.base, *args], capture_output=True, timeout=25, check=False)
        except (subprocess.TimeoutExpired, OSError) as error:
            if optional:
                return None
            raise ValueError(f"kubectl {args[0]} did not complete: {type(error).__name__}") from None
        if result.returncode != 0 or len(result.stdout) > MAX_RESPONSE_BYTES:
            if optional:
                return None
            first = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[:1]
            detail = redact(first[0])[:200] if first else f"exit code {result.returncode}"
            raise ValueError(f"kubectl {args[0]} failed: {detail}")
        return result.stdout


def _validate_target(context: str, namespace: str, pod: str, container: str) -> None:
    for label, value in (("namespace", namespace), ("pod", pod), ("container", container)):
        if not NAME.fullmatch(value) or len(value) > 253:
            raise ValueError(f"Invalid Kubernetes {label} name: {value[:60]!r}")
    if not context or context.startswith("-") or len(context) > 300:
        raise ValueError("An explicit kubeconfig context is required")


def collect(context: str, namespace: str, pod: str, container: str, *, run=subprocess.run) -> Bundle:
    _validate_target(context, namespace, pod, container)
    kubectl = Kubectl(context, namespace, run)
    observations: list[dict] = []

    def add(id, kind, source, text, data=None):
        item = {"id": id, "kind": kind, "source": source, "text": text}
        if data is not None:
            item["data"] = data
        observations.append(item)

    def limitation(id, text):
        add(id, "limitation", "collector", text)

    snapshot = json.loads(kubectl.read(["get", "pod", pod, "-o", "json"]))
    spec, status = snapshot.get("spec") or {}, snapshot.get("status") or {}
    containers = spec.get("containers") or []
    selected = next((c for c in containers if c.get("name") == container), None)
    if selected is None:
        names = ", ".join(c.get("name", "?") for c in containers) or "none"
        raise ValueError(f"Container {container!r} is not in Pod {pod!r} (containers: {names})")

    container_status = next((c for c in status.get("containerStatuses") or [] if c.get("name") == container), {})
    container_ready = container_status.get("ready") if isinstance(container_status.get("ready"), bool) else None
    condition = next((c for c in status.get("conditions") or [] if c.get("type") == "Ready"), {})
    pod_ready = condition.get("status") if condition.get("status") in ("True", "False") else "Unknown"
    since = condition.get("lastTransitionTime")
    add("status", "pod_status", "pod/status",
        f"selected container ready={container_ready}; Pod Ready={pod_ready} since {since or 'unknown'}",
        {"container_ready": container_ready, "pod_ready": pod_ready, "pod_ready_since": since})

    http = (selected.get("readinessProbe") or {}).get("httpGet")
    if http:
        port = http.get("port")
        if isinstance(port, str):  # named port: resolve against the container's ports
            port = next((p.get("containerPort") for p in selected.get("ports") or [] if p.get("name") == port), port)
        probe = {"scheme": http.get("scheme", "HTTP"), "port": port, "path": http.get("path") or "/"}
        add("probe", "probe_config", "pod/spec/readinessProbe/httpGet", json.dumps(probe), probe)
    else:
        limitation("probe-unsupported", "No HTTP readiness probe; exec, TCP and gRPC probes are out of scope.")

    uid = (snapshot.get("metadata") or {}).get("uid")
    raw = kubectl.read(["get", "events", "--field-selector", f"involvedObject.uid={uid}", "-o", "json"],
                       optional=True) if uid else None
    if raw is None:
        limitation("events-unavailable", "Pod Events could not be read.")
    else:
        mine, unattributed = [], []
        for item in json.loads(raw).get("items") or []:
            message = item.get("message") or item.get("note") or ""
            if not message.startswith("Readiness probe failed"):
                continue
            field_path = (item.get("involvedObject") or item.get("regarding") or {}).get("fieldPath") or ""
            if field_path == f"spec.containers{{{container}}}":
                mine.append((item, message))
            elif not field_path:
                unattributed.append((item, message))
        if unattributed and len(containers) == 1:
            mine.extend(unattributed)
        elif unattributed:
            limitation("container-scope", f"{len(unattributed)} readiness failure Event(s) name no container.")
        mine.sort(key=lambda pair: _event_time(pair[0]) or "")
        events = [{"message": redact(message)[:2000], "count": _count(item), "last_seen": _event_time(item)}
                  for item, message in mine[-MAX_EVENTS:]]
        if events:
            add("events", "probe_failures", "pod/events/readiness",
                "\n".join(f"{e['last_seen'] or 'unknown time'} x{e['count']}: {e['message']}" for e in events),
                {"events": events})
        else:
            limitation("events-none", "No readiness failure Events for this container. Events expire "
                                      "(one hour by default), so absence is not proof of health.")

    log_args = ["logs", pod, "--container", container, "--timestamps=true"]
    head = kubectl.read([*log_args, f"--limit-bytes={LOG_BYTES + 1}"], optional=True)
    if head is None:
        limitation("logs-unavailable", "Container logs could not be read.")
    elif not head.strip():
        limitation("logs-empty", "The container has written no log lines.")
    elif len(head) <= LOG_BYTES:
        add("logs", "app_log", "container/logs/all", redact(head.decode("utf-8", "replace")))
    else:
        add("startup", "app_log", "container/logs/first-16kB",
            redact(head[:LOG_BYTES].decode("utf-8", "replace")))
        recent = kubectl.read([*log_args, "--since=10m", "--tail=200", f"--limit-bytes={LOG_BYTES}"], optional=True)
        if recent and recent.strip():
            add("recent", "app_log", "container/logs/last-10m", redact(recent.decode("utf-8", "replace")))
        limitation("logs-truncated", "The log exceeds 16 kB; only its start and the last ten minutes are included.")

    limitation("snapshot", "Reads are sequential, not atomic. Logs cover the current container instance only.")
    # Names stay out of the bundle; it may be sent to a model provider.
    return Bundle.parse({"schema": BUNDLE_SCHEMA, "target": "selected-pod/selected-container",
                         "observations": observations})
