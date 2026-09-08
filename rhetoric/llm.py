"""Model access with schema-constrained decoding.

Small open models emit malformed JSON often enough to break a pipeline, so this
never parses-and-retries hopefully: it hands the JSON schema to the runtime and
lets the decoder enforce it token by token. Ollama takes a schema in `format`;
OpenAI-compatible endpoints (OpenRouter et al.) take one in `response_format`.

Configured entirely from the environment so swapping models is a config change:

    RHETORIC_PROVIDER   openrouter | ollama        (default: openrouter)
    RHETORIC_MODEL      provider-specific model id
    RHETORIC_TEMPERATURE                           (default: 0.9)
    RHETORIC_MAX_TOKENS                            (default: 6000)
    RHETORIC_NUM_CTX    ollama context window      (default: 8192)
    RHETORIC_TIMEOUT    seconds                    (default: 1800)
    RHETORIC_THINK      1 to allow reasoning       (default: off)
    OPENROUTER_API_KEY  required for openrouter
    OLLAMA_HOST                                    (default: http://localhost:11434)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

Provider = Literal["openrouter", "ollama"]

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Read KEY=value lines from .env without overriding the real environment.

    Keeps the API key out of shell history and out of this repo -- .env is
    gitignored. Values are never logged.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# Starting points only. Pick the real one by eval -- deadpan register is where
# models differ, and it is not predictable from size or price. `--models` lists
# what the account can actually reach.
DEFAULT_MODEL: dict[Provider, str] = {
    "openrouter": "qwen/qwen3.8-flash",
    "ollama": "qwen3:8b",
}


class ModelError(RuntimeError):
    pass


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a pydantic JSON schema acceptable to strict structured-output APIs.

    Strict mode requires every object to close `additionalProperties` and to list
    every property as required -- pydantic does neither, since it treats fields
    with defaults as optional. Harmless for Ollama, mandatory for OpenAI-shaped
    endpoints.
    """
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema:
            schema["required"] = list(schema["properties"])
            schema["additionalProperties"] = False
        for value in schema.values():
            strictify(value)
    elif isinstance(schema, list):
        for item in schema:
            strictify(item)
    return schema


@dataclass(frozen=True)
class ModelConfig:
    provider: Provider
    model: str
    temperature: float
    max_tokens: int
    num_ctx: int
    timeout: float
    think: bool
    base_url: str
    api_key: str | None

    @classmethod
    def from_env(cls) -> ModelConfig:
        load_env_file()
        provider: Provider = os.environ.get("RHETORIC_PROVIDER", "openrouter")  # type: ignore[assignment]
        if provider not in DEFAULT_MODEL:
            raise ModelError(f"RHETORIC_PROVIDER must be one of {list(DEFAULT_MODEL)}, got {provider!r}")

        if provider == "ollama":
            base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            api_key = None
        else:
            base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise ModelError(
                    "OPENROUTER_API_KEY is not set. Put it in .env at the repo root "
                    "(gitignored) as OPENROUTER_API_KEY=sk-or-..., or export it."
                )

        return cls(
            provider=provider,
            model=os.environ.get("RHETORIC_MODEL", DEFAULT_MODEL[provider]),
            temperature=float(os.environ.get("RHETORIC_TEMPERATURE", "0.9")),
            max_tokens=int(os.environ.get("RHETORIC_MAX_TOKENS", "6000")),
            num_ctx=int(os.environ.get("RHETORIC_NUM_CTX", "8192")),
            timeout=float(os.environ.get("RHETORIC_TIMEOUT", "1800")),
            think=os.environ.get("RHETORIC_THINK", "") not in ("", "0", "false"),
            base_url=base_url.rstrip("/"),
            api_key=api_key,
        )


def build_request(
    cfg: ModelConfig, system: str, user: str, schema: dict[str, Any], name: str
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Return (url, headers, body) without sending anything. Also used by --dry-run."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    schema = strictify(json.loads(json.dumps(schema)))  # deep copy; do not mutate the caller's

    if cfg.provider == "ollama":
        return (
            f"{cfg.base_url}/api/chat",
            {"Content-Type": "application/json"},
            {
                "model": cfg.model,
                "messages": messages,
                "format": schema,
                "stream": False,
                # Reasoning models otherwise spend the entire token budget
                # thinking and return empty content -- measured on qwen3:8b,
                # which burned 200/200 tokens on a 990-character think block.
                # The grammar does not constrain the reasoning channel.
                "think": cfg.think,
                "options": {
                    # A plan runs long; the default 2k context silently truncates
                    # it. Do not overshoot either -- the KV cache is allocated up
                    # front, and on a small GPU that is the difference between
                    # fitting and crawling.
                    "num_ctx": cfg.num_ctx,
                    # Bounded, because a small model under a grammar will happily
                    # generate until something gives up rather than close the JSON.
                    "num_predict": cfg.max_tokens,
                    "temperature": cfg.temperature,
                },
            },
        )

    return (
        f"{cfg.base_url}/chat/completions",
        {"Content-Type": "application/json", "Authorization": f"Bearer {cfg.api_key}"},
        {
            "model": cfg.model,
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            # Same trap as ollama's `think`, different provider: reasoning is
            # default_enabled on many models and its tokens count against
            # max_tokens, so the budget is spent before the JSON is closed.
            # Harmless on models without a reasoning channel.
            "reasoning": {"enabled": cfg.think},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        },
    )


def _extract(cfg: ModelConfig, payload: dict[str, Any]) -> tuple[str, str | None]:
    """Return (content, finish_reason)."""
    # OpenRouter reports upstream failures as HTTP 200 with an error body, so
    # the status check upstream of here does not catch them. Without this the
    # next line is a bare KeyError on 'choices'.
    if error := payload.get("error"):
        message = error.get("message", error) if isinstance(error, dict) else error
        raw = (error.get("metadata") or {}).get("raw", "") if isinstance(error, dict) else ""
        raise ModelError(f"{cfg.model}: {message}{f' -- {raw}' if raw else ''}"[:500])

    if cfg.provider == "ollama":
        return payload["message"]["content"], payload.get("done_reason")
    choice = payload["choices"][0]
    return choice["message"]["content"], choice.get("finish_reason")


def complete_json(
    cfg: ModelConfig,
    system: str,
    user: str,
    schema: dict[str, Any],
    name: str = "response",
) -> dict[str, Any]:
    """One schema-constrained completion. Returns parsed JSON."""
    url, headers, body = build_request(cfg, system, user, schema, name)
    timeout = cfg.timeout

    try:
        response = httpx.post(url, headers=headers, json=body, timeout=timeout)
        if response.status_code == 400 and "think" in response.text.lower():
            # Model has no reasoning channel to turn off; ollama rejects the flag.
            body.pop("think", None)
            response = httpx.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.ConnectError as exc:
        hint = " Is `ollama serve` running?" if cfg.provider == "ollama" else ""
        raise ModelError(f"could not reach {url}.{hint}") from exc
    except httpx.ReadTimeout as exc:
        raise ModelError(
            f"{cfg.model} did not respond within {timeout:.0f}s. Local models are slow "
            "under a large grammar -- lower RHETORIC_MAX_TOKENS, or raise "
            "RHETORIC_TIMEOUT."
        ) from exc

    if response.status_code != 200:
        raise ModelError(f"{cfg.provider} returned {response.status_code}: {response.text[:400]}")

    content, finish = _extract(cfg, response.json())

    # Worth naming precisely: a truncated response is still schema-shaped up to
    # the cut, so it surfaces as a JSON parse error and sends you hunting for a
    # decoding bug that is not there.
    if finish in ("length", "max_tokens"):
        raise ModelError(
            f"{cfg.model} hit the {cfg.max_tokens}-token cap before closing the JSON. "
            "Raise RHETORIC_MAX_TOKENS."
        )

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        # Should be unreachable with a working constrained decode; if it fires,
        # the provider ignored the schema rather than the model misbehaving.
        raise ModelError(
            f"{cfg.model} returned non-JSON despite a schema constraint: {content[:400]}"
        ) from exc


def list_models(cfg: ModelConfig, limit: int = 25) -> list[dict[str, Any]]:
    """Models this design can actually use, cheapest first. OpenRouter only.

    Filtered to models advertising `structured_outputs`: the whole pipeline
    rests on the decoder enforcing a JSON schema, and a model that quietly
    ignores `response_format` returns prose where a plan should be.
    """
    if cfg.provider != "openrouter":
        raise ModelError("--models is only meaningful for openrouter")

    response = httpx.get(f"{cfg.base_url}/models", timeout=60.0)
    if response.status_code != 200:
        raise ModelError(f"could not list models: {response.status_code}")

    rows = []
    for m in response.json().get("data", []):
        if "structured_outputs" not in (m.get("supported_parameters") or []):
            continue
        try:
            out = float((m.get("pricing") or {}).get("completion", "0"))
        except (TypeError, ValueError):
            continue
        # Negative values are OpenRouter's dynamic-pricing sentinel, not a
        # discount; sorting on them puts routers at the top of a "cheapest" list.
        if out < 0:
            continue
        rows.append({"id": m.get("id", ""), "out_per_m": out * 1_000_000,
                     "context": m.get("context_length") or 0})
    rows.sort(key=lambda r: (r["out_per_m"], r["id"]))
    return rows[:limit]
