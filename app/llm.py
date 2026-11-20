"""The only file that talks to Claude. With no ANTHROPIC_API_KEY the app still runs (keyword extractor + template report)."""
import json
import os
import time

from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("MODEL", "claude-opus-5-5")
EFFORT = os.getenv("EFFORT", "low")  # field notes are easy: low effort = fast + cheap

_client = None
if os.getenv("ANTHROPIC_API_KEY"):
    import anthropic

    _client = anthropic.Anthropic()

# Running token totals so /api/stats and the eval can show real cost per note.
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0, "failures": 0, "last_error": None}

# Server-side refusal fallback exists for these families; Haiku has none and would reject the parameter.
_FALLBACK_OK = ("claude-opus-", "claude-sonnet-", "claude-fable-")


def available() -> bool:
    return _client is not None


def _create(prompt: str, system: str, max_tokens: int, model: str | None = None, **extra):
    model = model or MODEL
    if model.startswith(_FALLBACK_OK):
        # if the model declines (e.g. a note about a shooting), the API retries on a fallback model
        extra |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    t = time.perf_counter()
    resp = _client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,  # includes thinking tokens, so leave headroom
        system=system,
        messages=[{"role": "user", "content": prompt}],
        **extra,
    )
    USAGE["calls"] += 1
    USAGE["input_tokens"] += resp.usage.input_tokens
    USAGE["output_tokens"] += resp.usage.output_tokens
    USAGE["seconds"] += time.perf_counter() - t
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("response cut off at max_tokens")
    return "".join(b.text for b in resp.content if b.type == "text")


def json_call(prompt: str, system: str, schema: dict, model: str | None = None, effort: str | None = None) -> dict:
    """Structured output: the response is guaranteed to match `schema`."""
    text = _create(
        prompt, system, 16000, model=model,
        output_config={"effort": effort or EFFORT, "format": {"type": "json_schema", "schema": schema}},
    )
    return json.loads(text)


def text_call(prompt: str, system: str, max_tokens: int = 16000) -> str:
    return _create(prompt, system, max_tokens, output_config={"effort": EFFORT})
