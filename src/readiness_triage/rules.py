"""Explicit rules. Each rule looks at typed signals and either proposes one cause
with the evidence that supports it, or stays silent. ``diagnose`` concludes only
when exactly one cause is proposed; otherwise it abstains and says why."""
from __future__ import annotations

from dataclasses import dataclass

from .models import Bundle, Diagnosis
from .signals import Signals, extract

HEALTH_SEGMENTS = {"health", "healthz", "ready", "readyz", "readiness", "live", "livez", "liveness", "status"}

UNCERTAINTY = {
    "probe_port_mismatch": "Inferred from the startup log line; the listener may have changed after it was written.",
    "listener_loopback_only": "Inferred from the startup log line; the bind address may have changed after it was written.",
    "probe_path_mismatch": "The logged prefix shows where routes live, not that a readiness route exists beneath it.",
    "dependency_unavailable": "Log lines are not timestamp-matched to probe failures; the dependency error may be older.",
    "healthy": "Readiness is a point-in-time signal; it does not prove the application behaves correctly.",
}


@dataclass(frozen=True)
class Candidate:
    cause: str
    evidence_ids: tuple[str, ...]
    summary: str


def _ids(*values) -> tuple[str, ...]:
    flat = []
    for value in values:
        if value is None:
            continue
        flat.extend(value if isinstance(value, (list, tuple)) else [value])
    return tuple(dict.fromkeys(flat))


def _under(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/")
    return not prefix or path == prefix or path.startswith(prefix + "/")


def port_mismatch(s: Signals) -> Candidate | None:
    refused = [f for f in s.current_failures() if f.kind == "refused"]
    if not refused or s.probe_port is None:
        return None
    reachable = sorted({l.port for l in s.listeners if not l.loopback})
    if not reachable or s.probe_port in {l.port for l in s.listeners}:
        return None
    evidence = [l.evidence_id for l in s.listeners if not l.loopback]
    return Candidate("probe_port_mismatch", _ids(s.probe_id, refused[0].evidence_id, evidence),
                     f"The readiness probe targets port {s.probe_port} and the connection is refused; "
                     f"the server logs that it listens on port {', '.join(map(str, reachable))}.")


def loopback_only(s: Signals) -> Candidate | None:
    refused = [f for f in s.current_failures() if f.kind == "refused"]
    on_port = [l for l in s.listeners if l.port == s.probe_port]
    if not refused or not on_port or not all(l.loopback for l in on_port):
        return None
    hosts = sorted({l.host for l in on_port})
    return Candidate("listener_loopback_only",
                     _ids(s.probe_id, refused[0].evidence_id, [l.evidence_id for l in on_port]),
                     f"The server listens on port {s.probe_port} only on {', '.join(hosts)}; "
                     f"the kubelet probes the Pod IP and the connection is refused.")


def path_mismatch(s: Signals) -> Candidate | None:
    not_found = [f for f in s.current_failures() if f.kind == "status" and f.status == 404]
    if not not_found or s.probe_path is None:
        return None
    looks_like_health = s.probe_path.rstrip("/").rsplit("/", 1)[-1].lower() in HEALTH_SEGMENTS
    for route in s.routes:
        if _under(s.probe_path, route.prefix):
            continue
        if route.kind == "context_path" or (route.kind == "actuator_base" and looks_like_health):
            return Candidate("probe_path_mismatch", _ids(s.probe_id, not_found[0].evidence_id, route.evidence_id),
                             f"The probe path {s.probe_path} returns 404 and lies outside the prefix "
                             f"{route.prefix} that the application reports ({route.kind.replace('_', ' ')}).")
    return None


def dependency_unavailable(s: Signals) -> Candidate | None:
    unavailable = [f for f in s.current_failures() if f.kind == "status" and f.status == 503]
    if not unavailable or not s.dependency_log_ids:
        return None
    return Candidate("dependency_unavailable", _ids(unavailable[0].evidence_id, s.dependency_log_ids),
                     "The readiness endpoint returns 503 and the application logs an outbound connection failure.")


RULES = (port_mismatch, loopback_only, path_mismatch, dependency_unavailable)


def _abstain(reason: str, s: Signals) -> Diagnosis:
    evidence = _ids([f.evidence_id for f in s.current_failures()], sorted(s.limitation_ids))
    return Diagnosis("insufficient_evidence", reason, evidence,
                     "The supported causes may still apply; the snapshot does not separate them.")


def diagnose(bundle: Bundle) -> Diagnosis:
    s = extract(bundle)
    if "container-scope" in s.limitation_ids:
        return _abstain("Probe failures could not be attributed to the selected container.", s)

    candidates = [c for rule in RULES if (c := rule(s)) is not None]
    causes = sorted({c.cause for c in candidates})
    current = s.current_failures()

    if s.container_ready is True:
        if current:
            return _abstain("The container reports Ready, but probe failures were recorded after it became Ready.", s)
        earlier = len(s.failures)
        summary = "The container reports Ready" + (
            f"; {earlier} earlier probe failure record(s) predate the Ready transition." if earlier else
            " and no probe failures are recorded for it.")
        return Diagnosis("healthy", summary, _ids(s.status_id), UNCERTAINTY["healthy"])

    if len(causes) == 1:
        c = candidates[0]
        return Diagnosis(c.cause, c.summary, c.evidence_ids, UNCERTAINTY[c.cause]).check(bundle)
    if len(causes) > 1:
        return _abstain("The evidence supports more than one cause: " + ", ".join(causes) + ".", s)
    if not current:
        return _abstain("The container is not Ready, but no current probe failure is recorded.", s)
    kinds = sorted({f.kind for f in current})
    return _abstain("Probe failures (" + ", ".join(kinds) + ") are recorded, but the logs name no listener, "
                    "route prefix or dependency error that would identify a supported cause.", s)
