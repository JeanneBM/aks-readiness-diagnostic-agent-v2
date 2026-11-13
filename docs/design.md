# Design: what changed from v1 and why

v1 is careful about safety and honest about its limits. Its weak spot is the experiment:
the rules matched log lines that the lab applications printed for them
(`Readiness route registered: GET /ready`, `readiness_path=`), so 24/24 for the rules
mostly showed that the rules could read their own instrumentation. v2 starts from that.

## The experiment

**Default logs only.** Every pattern in `signals.py` is a line a framework prints with
its default configuration (uvicorn, gunicorn, Werkzeug, ASP.NET Core, Spring Boot).
The lab app logs nothing extra apart from an ordinary warning when a dependency check
fails, and one scenario turns that off. Real captured output for uvicorn, Flask and
gunicorn is in `tests/golden/`.

**Injected and expected are separate.** A label says what was broken and what the
evidence can support. When software logs nothing useful (Go `net/http`, nginx, a silent
app), the expected answer is `insufficient_evidence`. This is where a model could
legitimately beat the rules, or hallucinate; v1's recorded cases had none (only its
synthetic fixtures did).

**Recordings are checked before they are scored.** Each lab scenario declares what its
recording must contain. If a recording lacks it, the case is set aside instead of being
scored against a label it does not deserve.

**Availability and quality are reported separately.** v1's C1 result (9/24, ten provider
errors) mixed both. v2 reports accuracy over all attempts and over answered attempts,
retries 429/5xx a bounded number of times and counts every attempt.

**No p-values on eight cases.** The report lists disagreeing cases instead. Freezing is
kept but small: a manifest of source, prompt, Python and model hashes.

## The code

| v1 | v2 | Why |
| --- | --- | --- |
| The model picks a recommendation from an enum, which is then overwritten; the rules return free text | `Diagnosis.recommendation` is a property derived from the cause | One source of truth; fewer tokens and failure modes for the model |
| Probe config is JSON inside `text`; health is `text == "Ready=True"` | Observations carry typed `data` for status, probe and failures | Rules read values instead of re-parsing strings |
| Rules and regexes interleaved in a triple loop | `signals.extract` runs once; rules are small functions over typed signals; patterns are data | Each rule is testable alone; a new framework is a new pattern |
| `A1 → H → C1` logic duplicated in CLI and evaluation | `strategies.run` | One definition |
| `evaluate()` does loading, running, scoring, aggregation and Markdown | Separate functions for each | Readable and testable |
| CLI prints one generic message for every error | Own errors are printed; provider errors print allowlisted metadata only | Users can fix what went wrong |
| Startup prefix read with a thread and `kill()` | `kubectl logs --limit-bytes` | The kubelet reads forward and stops after N bytes; lab scenario R11 verifies this on a real cluster |
| Recent logs and the startup prefix sent separately, often duplicated | A log that fits in 16 kB is sent once | Smaller bundles |
| Multi-container Pods always abstain | Events attributed through `involvedObject.fieldPath` | The kubelet names the container for probe Events |
| Healthy requires no failure Events at all | Failures before the Ready transition do not count | Slow starters were wrongly treated as unclear |
| Model client tested by patching `urllib` | Injectable transport | Tests exercise the real request and parsing code |
| Email redaction regex is quadratic on long tokens | Bounded and anchored | A 64 kB token no longer takes seconds |

## New cause

`listener_loopback_only` covers a common real failure: uvicorn and `flask run` bind
127.0.0.1 by default, and the kubelet probes the Pod IP, so the probe is refused even
though the port is right. v1 had no way to say this.

## Kept from v1

Zero runtime dependencies, read-only collection, explicit flags for cluster reads and
paid calls, strict output schema with evidence IDs limited to the current bundle, no
redirects, error bodies dropped unread, outputs never overwritten.
