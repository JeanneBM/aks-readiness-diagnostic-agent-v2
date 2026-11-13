# Lab

The lab records real evidence on a local kind cluster. Scenarios and their labels live in
[lab/scenarios.py](../lab/scenarios.py) and are fixed before recording.

| ID | Software | Injected | Expected |
| --- | --- | --- | --- |
| R01 | nginx (stock) | healthy | healthy |
| R02 | nginx (stock) | port mismatch | insufficient evidence: nginx does not log its port |
| R03 | ASP.NET Core sample | healthy | healthy |
| R04 | ASP.NET Core sample | probe on 80, .NET 8+ listens on 8080 | port mismatch |
| R05 | uvicorn | healthy | healthy |
| R06 | uvicorn `--host 127.0.0.1` | loopback bind | loopback only |
| R07 | uvicorn | port mismatch | port mismatch |
| R08 | uvicorn | path mismatch | insufficient evidence: only a 404 is logged |
| R09 | uvicorn | dependency down, error logged | dependency unavailable |
| R10 | uvicorn | dependency down, nothing logged | insufficient evidence |
| R11 | uvicorn, 40 kB of log | port mismatch | port mismatch, from the log prefix |
| R12 | Go net/http | port mismatch | insufficient evidence: nothing is logged |
| R13 | Go net/http | healthy | healthy |

## Run it

Locally (Docker, kind and kubectl installed):

```bash
kind create cluster --name readiness-lab
python tools/record_lab.py --context kind-readiness-lab --output cases/recorded/$(date +%F)-kind
readiness-triage evaluate --cases cases/recorded/$(date +%F)-kind --output .local/recorded-rules
kind delete cluster --name readiness-lab
```

Or run the manual **kind lab recording** workflow in GitHub Actions and download the artifact.

The recorder refuses contexts not named `kind-*` and namespaces it does not own. Recordings
that miss their intended evidence go to `_invalid/` with a `problems.json`; they are kept
for inspection and excluded from evaluation.

Not verified yet: the ASP.NET Core sample image's log format and port (R03/R04), and the
`--limit-bytes` prefix behaviour (R11). The first recording settles both.
