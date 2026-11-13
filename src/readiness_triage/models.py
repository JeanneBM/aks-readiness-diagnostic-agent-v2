"""Evidence and diagnosis contracts.

Two changes from v1 shape everything else:

* Observations carry structured ``data`` where the collector already knows the
  structure (Pod status, probe config, probe failure Events). Rules read typed
  values instead of re-parsing strings; logs stay free text.
* A diagnosis has no free-form recommendation. The recommendation is a fixed,
  read-only template derived from the cause, for rules and model alike.

Ground-truth labels never enter a bundle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

CAUSES = (
    "probe_port_mismatch",
    "listener_loopback_only",
    "probe_path_mismatch",
    "dependency_unavailable",
    "healthy",
    "insufficient_evidence",
)

RECOMMENDATIONS = {
    "probe_port_mismatch": (
        "Compare the readiness probe port with the port the server reports listening on; "
        "have an operator review the probe or server configuration before changing either."
    ),
    "listener_loopback_only": (
        "Check the server bind address: the kubelet probes the Pod IP, so a server bound only "
        "to 127.0.0.1 or localhost refuses the probe. Have an operator review it before changing it."
    ),
    "probe_path_mismatch": (
        "Compare the readiness probe path with the route prefix the application reports; "
        "have an operator review the probe path before changing it."
    ),
    "dependency_unavailable": (
        "Check the dependency named in the application logs with authorized read-only checks; "
        "do not assume a network or credential cause."
    ),
    "healthy": "No action from this snapshot; check again if readiness changes.",
    "insufficient_evidence": (
        "Collect a current probe result, the effective probe configuration and application logs "
        "from startup, using authorized read-only access."
    ),
}

KINDS = ("pod_status", "probe_config", "probe_failures", "app_log", "limitation")
READY_VALUES = ("True", "False", "Unknown")

MAX_OBSERVATIONS = 100
MAX_TEXT_CHARS = 24_000
MAX_BUNDLE_TEXT_BYTES = 128_000
MAX_FAILURE_EVENTS = 20
BUNDLE_SCHEMA = 2


def parse_time(value: str | None) -> datetime | None:
    """Parse an RFC 3339 timestamp as Kubernetes writes it; None stays None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Timestamps must be strings or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid timestamp: {value[:40]!r}") from None
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp without a time zone: {value[:40]!r}")
    return parsed


def check_keys(value: Any, required: tuple[str, ...], optional: tuple[str, ...] = (), where: str = "object") -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a JSON object")
    missing = set(required) - set(value)
    extra = set(value) - set(required) - set(optional)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected " + ", ".join(sorted(extra)))
        raise ValueError(f"{where}: " + "; ".join(details))


def _nonempty_str(value: Any, where: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{where} must be a nonempty string of at most {limit} characters")
    return value


def _validate_data(kind: str, data: Any, where: str) -> None:
    if kind in ("app_log", "limitation"):
        if data is not None:
            raise ValueError(f"{where}: {kind} observations carry no structured data")
        return
    if kind == "pod_status":
        check_keys(data, ("container_ready", "pod_ready", "pod_ready_since"), where=where + ".data")
        if data["container_ready"] not in (True, False, None):
            raise ValueError(f"{where}.data.container_ready must be true, false or null")
        if data["pod_ready"] not in READY_VALUES:
            raise ValueError(f"{where}.data.pod_ready must be one of {READY_VALUES}")
        parse_time(data["pod_ready_since"])
    elif kind == "probe_config":
        check_keys(data, ("scheme", "port", "path"), where=where + ".data")
        if data["scheme"] not in ("HTTP", "HTTPS"):
            raise ValueError(f"{where}.data.scheme must be HTTP or HTTPS")
        port = data["port"]
        if not ((type(port) is int and 1 <= port <= 65535) or (isinstance(port, str) and 0 < len(port) <= 15)):
            raise ValueError(f"{where}.data.port must be a port number or an unresolved port name")
        if not isinstance(data["path"], str) or not data["path"].startswith("/") or len(data["path"]) > 2000:
            raise ValueError(f"{where}.data.path must be an absolute path")
    elif kind == "probe_failures":
        check_keys(data, ("events",), where=where + ".data")
        events = data["events"]
        if not isinstance(events, list) or not 1 <= len(events) <= MAX_FAILURE_EVENTS:
            raise ValueError(f"{where}.data.events must hold 1 to {MAX_FAILURE_EVENTS} events")
        for index, event in enumerate(events):
            at = f"{where}.data.events[{index}]"
            check_keys(event, ("message", "count", "last_seen"), where=at)
            _nonempty_str(event["message"], at + ".message", 2000)
            if type(event["count"]) is not int or event["count"] < 1:
                raise ValueError(f"{at}.count must be a positive integer")
            parse_time(event["last_seen"])
    try:
        json.dumps(data)
    except (TypeError, ValueError):
        raise ValueError(f"{where}.data must be JSON-serializable") from None


@dataclass(frozen=True)
class Observation:
    id: str
    kind: str
    source: str
    text: str
    data: dict | None = None

    @classmethod
    def parse(cls, value: Any, where: str = "observation") -> "Observation":
        check_keys(value, ("id", "kind", "source", "text"), ("data",), where=where)
        _nonempty_str(value["id"], where + ".id", 64)
        _nonempty_str(value["source"], where + ".source", 200)
        _nonempty_str(value["text"], where + ".text", MAX_TEXT_CHARS)
        if value["kind"] not in KINDS:
            raise ValueError(f"{where}.kind must be one of {KINDS}")
        data = value.get("data")
        _validate_data(value["kind"], data, where)
        return cls(value["id"], value["kind"], value["source"], value["text"], data)

    def to_dict(self) -> dict:
        value = {"id": self.id, "kind": self.kind, "source": self.source, "text": self.text}
        if self.data is not None:
            value["data"] = self.data
        return value


@dataclass(frozen=True)
class Bundle:
    target: str
    observations: tuple[Observation, ...]

    @classmethod
    def parse(cls, value: Any) -> "Bundle":
        check_keys(value, ("schema", "target", "observations"), where="bundle")
        if type(value["schema"]) is not int or value["schema"] != BUNDLE_SCHEMA:
            raise ValueError(f"bundle.schema must be {BUNDLE_SCHEMA}")
        _nonempty_str(value["target"], "bundle.target", 300)
        items = value["observations"]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_OBSERVATIONS:
            raise ValueError(f"bundle.observations must hold 1 to {MAX_OBSERVATIONS} items")
        observations = tuple(Observation.parse(item, f"observations[{i}]") for i, item in enumerate(items))
        ids = [o.id for o in observations]
        if len(ids) != len(set(ids)):
            raise ValueError("bundle.observations contain duplicate IDs")
        if sum(len(o.text.encode()) for o in observations) > MAX_BUNDLE_TEXT_BYTES:
            raise ValueError(f"bundle text exceeds {MAX_BUNDLE_TEXT_BYTES} bytes")
        return cls(value["target"], observations)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(o.id for o in self.observations)

    def of_kind(self, kind: str) -> list[Observation]:
        return [o for o in self.observations if o.kind == kind]

    def to_dict(self) -> dict:
        return {"schema": BUNDLE_SCHEMA, "target": self.target,
                "observations": [o.to_dict() for o in self.observations]}


@dataclass(frozen=True)
class Diagnosis:
    cause: str
    summary: str
    evidence_ids: tuple[str, ...]
    uncertainty: str

    @property
    def recommendation(self) -> str:
        return RECOMMENDATIONS[self.cause]

    @property
    def abstained(self) -> bool:
        return self.cause == "insufficient_evidence"

    def check(self, bundle: Bundle) -> "Diagnosis":
        """Validate against the bundle it claims to describe; returns self."""
        if self.cause not in CAUSES:
            raise ValueError(f"Unsupported cause: {str(self.cause)[:60]!r}")
        for name in ("summary", "uncertainty"):
            text = getattr(self, name)
            if not isinstance(text, str) or not 1 <= len(text) <= 4000:
                raise ValueError(f"Diagnosis {name} must be 1 to 4000 characters")
        ids = self.evidence_ids
        if not isinstance(ids, tuple) or any(not isinstance(x, str) for x in ids):
            raise ValueError("Diagnosis evidence_ids must be strings")
        if len(ids) != len(set(ids)):
            raise ValueError("Diagnosis cites the same evidence twice")
        unknown = set(ids) - bundle.ids
        if unknown:
            raise ValueError("Diagnosis cites unknown evidence: " + ", ".join(sorted(unknown)[:5]))
        if not self.abstained and not ids:
            raise ValueError("A conclusion other than insufficient_evidence must cite evidence")
        return self

    @classmethod
    def parse(cls, value: Any, bundle: Bundle) -> "Diagnosis":
        check_keys(value, ("cause", "summary", "evidence_ids", "uncertainty"), where="diagnosis")
        if not isinstance(value["evidence_ids"], list):
            raise ValueError("diagnosis.evidence_ids must be a list")
        return cls(value["cause"], value["summary"], tuple(value["evidence_ids"]), value["uncertainty"]).check(bundle)

    def to_dict(self) -> dict:
        return {"cause": self.cause, "summary": self.summary, "evidence_ids": list(self.evidence_ids),
                "recommendation": self.recommendation, "uncertainty": self.uncertainty}


def load_bundle(path) -> Bundle:
    with open(path, encoding="utf-8") as stream:
        return Bundle.parse(json.load(stream))
