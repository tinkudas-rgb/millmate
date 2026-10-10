"""OpenAI-compatible client (Gemini by default). No key -> LLMUnavailable; callers use templates.
Env: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL. 20s timeout, 1 retry."""
import os

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-3.1-flash-lite"


class LLMUnavailable(Exception):
    pass


def available() -> bool:
    return bool(os.environ.get("LLM_API_KEY", "").strip())


def _client(timeout: float):
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        raise LLMUnavailable("no key configured")
    try:
        from openai import OpenAI  # imported late so tracing can instrument it first
    except ImportError as e:
        raise LLMUnavailable("openai package missing") from e
    base = os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE
    from .tracing import wrap
    return wrap(OpenAI(api_key=key, base_url=base, timeout=timeout, max_retries=1))


def chat(messages: list, tools: list | None = None, json: bool = False, timeout: float = 20.0) -> dict:
    """One chat-completions call. Returns {"content": str, "tool_calls": [{"id","name","arguments"}]}."""
    client = _client(min(20.0, max(1.0, timeout)))
    kw = {"model": os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL,
          "messages": messages, "temperature": 0.2, "max_tokens": 1500}
    if tools:
        kw["tools"] = tools
    elif json:
        kw["response_format"] = {"type": "json_object"}
    try:
        msg = client.chat.completions.create(**kw).choices[0].message
    except Exception as e:  # noqa: BLE001
        print(f"LLM call failed: {type(e).__name__} status={getattr(e, 'status_code', None)} {str(e)[:200]}", flush=True)
        raise LLMUnavailable(f"call failed: {type(e).__name__}") from e
    calls = []
    for c in (msg.tool_calls or []):
        # Gemini thinking models attach extra_content (thought_signature) to each call; it must be sent back unchanged.
        extra = getattr(c, "extra_content", None) or (getattr(c, "model_extra", None) or {}).get("extra_content")
        calls.append({"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}",
                      "extra_content": extra})
    return {"content": msg.content or "", "tool_calls": calls}


def complete(system: str, user: str, json: bool = False) -> str:
    return chat([{"role": "system", "content": system}, {"role": "user", "content": user}], json=json)["content"]
