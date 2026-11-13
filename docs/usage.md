# Usage

## Collect

```bash
readiness-triage collect --allow-cluster-read --context CONTEXT --namespace NS \
  --pod POD --container CONTAINER --output bundle.json
```

Needs `get` on pods and pods/log and `list` on events in the namespace
([k8s/collector-role.yaml](../k8s/collector-role.yaml)). The output file must not exist.
Review the bundle before sending it to a model provider; redaction is pattern-based.

## Diagnose

```bash
readiness-triage diagnose bundle.json                                   # rules
readiness-triage diagnose bundle.json --strategy cascade --allow-model-calls
```

## Evaluate

```bash
readiness-triage evaluate --cases cases/dev --output .local/run-1 \
  --strategies rules,llm,cascade --repetitions 3 --allow-model-calls
```

Writes `manifest.json`, `run.json`, one file per attempt, `summary.json` and `summary.md`.
For held-out cases, pass `--frozen` with the `manifest.json` of the development run; the
run refuses to start if code, prompt, Python version or model settings differ.

## Model configuration

| Variable | Meaning | Default |
| --- | --- | --- |
| `READINESS_ENDPOINT` | `https://<resource>.openai.azure.com` or `https://api.openai.com` | required |
| `READINESS_API_KEY` | API key | required |
| `READINESS_MODEL` | Azure deployment name or OpenAI model | required |
| `READINESS_PROVIDER` | `azure` or `openai` | `azure` |
| `READINESS_API_VERSION` | Azure API version | `2024-10-21` |
| `READINESS_TOKEN_PARAMETER` | `max_tokens` or `max_completion_tokens` | `max_tokens` |
| `READINESS_TEMPERATURE` | `0`, or `omit` for models that reject it | `0` |
| `READINESS_MAX_ATTEMPTS` | 1 to 5; retries 408, 429, 5xx and transport failures | `3` |
| `READINESS_MIN_INTERVAL_SECONDS` | pause between requests | `0` |
