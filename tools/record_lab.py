"""Record the lab scenarios on a local kind cluster into a case directory.

Safety: only contexts named kind-* are accepted, everything runs in one namespace
labelled as owned by the lab, and an existing namespace without that label is
refused. Pods are deleted after each scenario.

Usage:
    kind create cluster --name readiness-lab
    python tools/record_lab.py --context kind-readiness-lab --output cases/recorded/$(date +%F)-kind
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "lab")]
from readiness_triage.collector import collect  # noqa: E402
from scenarios import NAMESPACE, OWNER, SCENARIOS, check, pod_manifest  # noqa: E402


def sh(args: list[str], *, input: str | None = None, timeout: int = 300) -> str:
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    if result.returncode:
        first = (result.stderr.strip().splitlines() or ["no error output"])[0][:300]
        raise RuntimeError(f"{' '.join(args[:4])} failed: {first}")
    return result.stdout


class Lab:
    def __init__(self, context: str):
        if not context.startswith("kind-"):
            raise SystemExit("Refusing a context not named kind-*: the lab only runs on local kind clusters")
        self.context, self.cluster = context, context[len("kind-"):]

    def kubectl(self, *args: str, input: str | None = None) -> str:
        return sh(["kubectl", "--context", self.context, *args], input=input)

    def ensure_namespace(self) -> None:
        raw = self.kubectl("get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json")
        if raw.strip():
            labels = json.loads(raw).get("metadata", {}).get("labels", {})
            if any(labels.get(k) != v for k, v in OWNER.items()):
                raise SystemExit(f"Namespace {NAMESPACE} exists but is not owned by this lab")
            return
        self.kubectl("create", "-f", "-", input=json.dumps(
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE, "labels": OWNER}}))

    def build_images(self) -> None:
        for name in ("uvicorn", "go"):
            tag = f"readiness-lab/{name}:dev"
            sh(["docker", "build", "-t", tag, str(ROOT / "lab" / "apps" / name)], timeout=900)
            sh(["kind", "load", "docker-image", tag, "--name", self.cluster], timeout=600)

    def delete(self, name: str, wait: bool) -> None:
        self.kubectl("-n", NAMESPACE, "delete", "pod", name, "--ignore-not-found", f"--wait={str(wait).lower()}")

    def wait(self, scenario, timeout: int = 240) -> None:
        name = scenario.pod_name
        if scenario.becomes_ready:
            self.kubectl("-n", NAMESPACE, "wait", "--for=condition=Ready", f"pod/{name}", f"--timeout={timeout}s")
            time.sleep(5)
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self.kubectl("-n", NAMESPACE, "get", "events", "--field-selector",
                               f"involvedObject.name={name},reason=Unhealthy", "-o", "json")
            if any((i.get("message") or "").startswith("Readiness probe failed")
                   for i in json.loads(raw).get("items", [])):
                time.sleep(15)  # let failures repeat and logs accumulate
                return
            time.sleep(3)
        raise RuntimeError(f"no readiness failure Event within {timeout}s")


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--context", required=True)
    parser.add_argument("--output", required=True, help="a directory that does not exist yet")
    parser.add_argument("--scenarios", help="comma-separated scenario IDs (default: all)")
    parser.add_argument("--skip-build", action="store_true", help="images are already loaded into kind")
    args = parser.parse_args()

    chosen = SCENARIOS
    if args.scenarios:
        wanted = set(args.scenarios.split(","))
        chosen = tuple(s for s in SCENARIOS if s.id in wanted)
        if len(chosen) != len(wanted):
            raise SystemExit("Unknown scenario IDs: " + ", ".join(sorted(wanted - {s.id for s in chosen})))
    lab = Lab(args.context)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)

    lab.kubectl("get", "nodes")
    lab.ensure_namespace()
    if not args.skip_build:
        lab.build_images()

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    for scenario in chosen:
        print(f"{scenario.id}: recording", flush=True)
        lab.delete(scenario.pod_name, wait=True)
        bundle, problems = None, []
        try:
            lab.kubectl("apply", "-f", "-", input=json.dumps(pod_manifest(scenario)))
            lab.wait(scenario)
            bundle = collect(args.context, NAMESPACE, scenario.pod_name, "app")
            problems = check(scenario, bundle)
        except (RuntimeError, ValueError) as error:
            problems = [str(error)]
        finally:
            lab.delete(scenario.pod_name, wait=False)
        folder = output / ("_invalid" if problems else "") / scenario.id
        if bundle is not None:
            write(folder / "evidence.json", bundle.to_dict())
        write(folder / "label.json", {
            "split": "dev", "expected": scenario.expected, "injected": scenario.injected,
            "framework": scenario.framework, "origin": "recorded",
            "basis": f"{scenario.basis} Recorded on kind, {started[:10]}.", "citations": scenario.citations})
        if problems:
            write(folder / "problems.json", problems)
        results.append({"id": scenario.id, "valid": not problems, "problems": problems})
        print(f"{scenario.id}: {'ok' if not problems else 'INVALID: ' + '; '.join(problems)}", flush=True)

    write(output / "recording.json", {
        "started": started, "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "context": args.context, "kubectl": json.loads(lab.kubectl("version", "-o", "json")),
        "scenarios": results})
    invalid = [r["id"] for r in results if not r["valid"]]
    print(f"{len(results) - len(invalid)} valid, {len(invalid)} set aside" + (f": {', '.join(invalid)}" if invalid else ""))
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
