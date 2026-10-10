"""Neatlogs tracing. Silent no-op unless NEATLOGS_API_KEY is set. Never raises."""
import functools
import logging
import os

log = logging.getLogger(__name__)
ENDPOINT = "https://ingest.neatlogs.com"
_enabled = False
_started = False


def init() -> bool:
    """Call once at startup, before the LLM client is created. Safe to call again."""
    global _enabled, _started
    if _started:
        return _enabled
    _started = True
    key = os.environ.get("NEATLOGS_API_KEY", "").strip()
    if not key:
        return False
    try:
        import neatlogs
        neatlogs.init(api_key=key, endpoint=ENDPOINT, workflow_name="rice-mill-advisor")
        _enabled = True
    except Exception:  # noqa: BLE001  tracing must never break the app
        log.warning("tracing could not start; continuing without it")
    return _enabled


def wrap(client):
    """Wrap an LLM client so its calls show up in the trace. Returns the client unchanged when tracing is off."""
    if not _enabled:
        return client
    try:
        import neatlogs
        return neatlogs.wrap(client)
    except Exception:  # noqa: BLE001
        return client


def enabled() -> bool:
    return _enabled


def traced(kind: str, name: str):
    """Decorator: wraps the call in a Neatlogs span when tracing is on, else calls straight through.
    kind is one of WORKFLOW, AGENT, CHAIN, TOOL (the first argument of neatlogs.span)."""
    def deco(fn):
        wrapped = {}

        @functools.wraps(fn)
        def inner(*a, **k):
            if not _enabled:
                return fn(*a, **k)
            if "f" not in wrapped:
                try:
                    from neatlogs import span
                    wrapped["f"] = span(kind, name)(fn)
                except Exception:  # noqa: BLE001
                    wrapped["f"] = fn
            return wrapped["f"](*a, **k)
        return inner
    return deco
