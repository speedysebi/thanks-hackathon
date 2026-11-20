"""The only file that talks to Claude. With no ANTHROPIC_API_KEY the app still runs (keyword extractor + template report)."""
import json
import os

from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("MODEL", "claude-opus-5-5")
EFFORT = os.getenv("EFFORT", "low")  # field notes are easy: low effort = fast + cheap

_client = None
if os.getenv("ANTHROPIC_API_KEY"):
    import anthropic

    _client = anthropic.Anthropic()

# Running token totals so /api/stats can show real cost per note.
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def available() -> bool:
    return _client is not None


def _create(prompt: str, system: str, max_tokens: int, **extra):
    resp = _client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if the model declines (e.g. a note about a shooting), retry on a fallback model
        messages=[{"role": "user", "content": prompt}],
        **extra,
    )
    USAGE["calls"] += 1
    USAGE["input_tokens"] += resp.usage.input_tokens
    USAGE["output_tokens"] += resp.usage.output_tokens
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined")
    return "".join(b.text for b in resp.content if b.type == "text")


def json_call(prompt: str, system: str, schema: dict) -> dict:
    """Structured output: the response is guaranteed to match `schema`."""
    text = _create(
        prompt, system, 2000,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
    )
    return json.loads(text)


def text_call(prompt: str, system: str, max_tokens: int = 4000) -> str:
    return _create(prompt, system, max_tokens, output_config={"effort": EFFORT})
