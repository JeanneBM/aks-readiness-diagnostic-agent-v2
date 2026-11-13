"""Command line. Cluster reads and chargeable model requests each need an explicit flag.

Errors raised by this package carry messages written here, so they are printed.
Provider errors print only allowlisted metadata, never response bodies.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import strategies
from .collector import collect
from .errors import ContractError, ProviderError
from .evaluation import evaluate
from .llm import ModelClient
from .models import load_bundle


def write_new(path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:  # never overwrite earlier evidence
        json.dump(value, stream, indent=2)
        stream.write("\n")


def _client(names, allowed: bool):
    if not strategies.needs_model(names):
        return None
    if not allowed:
        raise ValueError("The llm and cascade strategies may send requests to a paid provider; "
                         "add --allow-model-calls to permit that")
    return ModelClient.from_env()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="readiness-triage",
                                     description="Read-only Kubernetes readiness triage and offline replay")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="read one container's readiness evidence into a bundle")
    for name in ("context", "namespace", "pod", "container", "output"):
        c.add_argument("--" + name, required=True)
    c.add_argument("--allow-cluster-read", action="store_true",
                   help="confirm reading from the named kubeconfig context")

    d = sub.add_parser("diagnose", help="diagnose one bundle")
    d.add_argument("bundle")
    d.add_argument("--strategy", choices=strategies.STRATEGIES, default="rules")
    d.add_argument("--allow-model-calls", action="store_true")

    e = sub.add_parser("evaluate", help="replay labelled cases and write a report")
    e.add_argument("--cases", default="cases/dev")
    e.add_argument("--output", required=True, help="a directory that does not exist yet")
    e.add_argument("--strategies", default="rules", help="comma-separated: rules,llm,cascade")
    e.add_argument("--repetitions", type=int, default=1)
    e.add_argument("--frozen", help="manifest.json of an earlier run that this run must match")
    e.add_argument("--allow-model-calls", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "collect":
            if not args.allow_cluster_read:
                raise ValueError(f"Add --allow-cluster-read to read from context {args.context!r}")
            bundle = collect(args.context, args.namespace, args.pod, args.container)
            write_new(args.output, bundle.to_dict())
            print(f"Wrote {len(bundle.observations)} observations to {args.output}")
        elif args.command == "diagnose":
            client = _client([args.strategy], args.allow_model_calls)
            outcome = strategies.run(args.strategy, load_bundle(args.bundle), client)
            print(json.dumps({**outcome.diagnosis.to_dict(), "model_calls": outcome.model_calls}, indent=2))
        else:
            names = [n.strip() for n in args.strategies.split(",") if n.strip()]
            client = _client(names, args.allow_model_calls)
            summary = evaluate(args.cases, args.output, names, client, args.repetitions, args.frozen)
            print((Path(args.output) / "summary.md").read_text(encoding="utf-8"))
            return 1 if any(s["errors"] for s in summary["strategies"].values()) else 0
        return 0
    except ProviderError as error:
        print(f"error: {error} {json.dumps(error.details)}", file=sys.stderr)
    except ContractError as error:
        print(f"error: {error}", file=sys.stderr)
    except FileExistsError as error:
        print(f"error: {error.filename} already exists; earlier outputs are never overwritten", file=sys.stderr)
    except FileNotFoundError as error:
        print(f"error: {error.filename} not found", file=sys.stderr)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
