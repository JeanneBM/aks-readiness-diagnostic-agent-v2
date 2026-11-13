"""Minimal ASGI app for the lab.

Logging stays at uvicorn's defaults plus Python's default basicConfig. The only line
the app itself writes is an ordinary warning when its dependency check fails, and
LOG_DEPENDENCY_ERRORS=0 turns even that off. Nothing here exists to help the rules.
"""
import logging
import os
import threading
import time
import urllib.request

READY_PATH = os.environ.get("READY_PATH", "/ready")
DEPENDENCY_URL = os.environ.get("DEPENDENCY_URL", "")
LOG_DEPENDENCY_ERRORS = os.environ.get("LOG_DEPENDENCY_ERRORS", "1") == "1"
FLOOD_BYTES = int(os.environ.get("FLOOD_BYTES", "0"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


def dependency_ok() -> bool:
    if not DEPENDENCY_URL:
        return True
    try:
        with urllib.request.urlopen(DEPENDENCY_URL, timeout=1) as response:
            return response.status == 200
    except Exception as error:  # any failure means "not ready"
        if LOG_DEPENDENCY_ERRORS:
            log.warning("readiness check failed: %s", error)
        return False


def flood() -> None:
    """Write filler after startup so the log outgrows the collector's 16 kB prefix."""
    time.sleep(2)
    line = "filler " + "x" * 120
    for _ in range(FLOOD_BYTES // (len(line) + 1) + 1):
        print(line, flush=True)


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                if FLOOD_BYTES:
                    threading.Thread(target=flood, daemon=True).start()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    if scope["path"] == READY_PATH:
        status = 200 if dependency_ok() else 503
    elif scope["path"] == "/":
        status = 200
    else:
        status = 404
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok\n" if status == 200 else b"not ready\n"})
