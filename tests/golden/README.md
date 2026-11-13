# Golden logs

Real output, not hand-written, used to check that the log patterns match what the
software actually prints.

| File | Produced by |
| --- | --- |
| `uvicorn-0.32.1-lab-app.log` | `lab/apps/uvicorn/app.py` under uvicorn 0.32.1, Python 3.12.3, run locally with `--host 127.0.0.1 --port 8765` and an unresolvable `DEPENDENCY_URL`, then one request each to `/ready` and `/healthz`. Captured 2026-11-13. |
| `flask-3.1.0-default.log` | `flask --app app run --port 5055` (Flask 3.1.0, Werkzeug default bind address). Captured 2026-11-13. |
| `flask-3.1.0-all-addresses.log` | `flask --app app run --host 0.0.0.0 --port 5056`; Werkzeug then prints a loopback line and a non-loopback line. Captured 2026-11-13. |
| `gunicorn-23.0.0.log` | `gunicorn -b 0.0.0.0:8077 app:app` (gunicorn 23.0.0), first four lines. Captured 2026-11-13. |

Not yet captured from real software: ASP.NET Core and Spring Boot lines are written from their
documented formats in `cases/dev` and are first checked against real output by the kind lab
(`R03`, `R04` for ASP.NET Core).
