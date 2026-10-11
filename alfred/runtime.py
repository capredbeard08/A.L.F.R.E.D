"""Thin runtime for A.L.F.R.E.D.

Loads the assembled system prompt from disk and talks to any OpenAI-compatible
chat-completions endpoint using only the standard library. No third-party
dependencies, so the repo installs nothing.

Network behaviour is deliberately minimal: one POST, non-streaming, bounded
timeout. This keeps the failure surface small and every error legible.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PROMPT_FILENAME = "ALFRED.system.md"
DEFAULT_API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_SECONDS = 60.0

ENV_API_KEY = "ALFRED_API_KEY"
ENV_API_BASE = "ALFRED_API_BASE"
ENV_MODEL = "ALFRED_MODEL"


class AlfredError(Exception):
    """Base class for runtime failures with a user-actionable message."""


class PromptNotFoundError(AlfredError):
    """The assembled system prompt is missing from the workspace."""


class ConfigError(AlfredError):
    """A required setting is absent or malformed."""


class UpstreamError(AlfredError):
    """The model endpoint returned an error or an unusable response."""


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration.

    `api_key` uses `field(repr=False)` so the secret can never surface in a log
    line, traceback, or interactive inspection of this object.
    """

    api_key: str = field(repr=False)
    api_base: str
    model: str
    timeout_seconds: float


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upward from `start` looking for the directory that holds the prompt."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / DEFAULT_PROMPT_FILENAME).is_file():
            return candidate
    raise PromptNotFoundError(
        f"{DEFAULT_PROMPT_FILENAME} not found in {here} or any parent directory. "
        "Run `python3 scripts/build_prompt.py` first."
    )


def load_system_prompt(path: Path | str | None = None) -> str:
    """Read the assembled system prompt, raising PromptNotFoundError if absent."""
    target = Path(path).resolve() if path else find_repo_root() / DEFAULT_PROMPT_FILENAME
    if not target.is_file():
        raise PromptNotFoundError(
            f"{target} does not exist. Run `python3 scripts/build_prompt.py` first."
        )
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PromptNotFoundError(f"{target} is not valid UTF-8.") from exc
    if not text.strip():
        raise PromptNotFoundError(f"{target} is empty. Re-run `python3 scripts/build_prompt.py`.")
    return text


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars/4). Cheap and directionally right, not exact."""
    return max(1, len(text) // 4)


def resolve_settings(
    *,
    api_key: str | None = None,
    api_base: str | None = None,
    model: str | None = None,
    timeout_seconds: float | None = None,
    environ: dict[str, str] | None = None,
) -> Settings:
    """Resolve settings from explicit arguments, then environment. Never logs secrets."""
    env = os.environ if environ is None else environ

    key = (api_key or env.get(ENV_API_KEY) or env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise ConfigError(
            f"No API key. Set {ENV_API_KEY} (or OPENAI_API_KEY) in your environment."
        )

    base = (api_base or env.get(ENV_API_BASE) or env.get("OPENAI_BASE_URL") or DEFAULT_API_BASE).strip()
    if not base.startswith(("http://", "https://")):
        raise ConfigError(f"{ENV_API_BASE} must be an http(s) URL, got: {base!r}")
    base = base.rstrip("/")

    resolved_model = (model or env.get(ENV_MODEL) or DEFAULT_MODEL).strip()
    if not resolved_model:
        raise ConfigError(f"{ENV_MODEL} resolved to an empty model name.")

    timeout = timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS
    if timeout <= 0:
        raise ConfigError("timeout must be positive.")

    return Settings(api_key=key, api_base=base, model=resolved_model, timeout_seconds=timeout)


def build_messages(system_prompt: str, user_text: str) -> list[dict[str, str]]:
    """Assemble the chat messages payload."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]


def chat(user_text: str, system_prompt: str, settings: Settings) -> str:
    """Send one turn and return the assistant reply. Raises UpstreamError on failure."""
    if not user_text.strip():
        raise ConfigError("Cannot send an empty user message.")

    payload = json.dumps(
        {
            "model": settings.model,
            "messages": build_messages(system_prompt, user_text),
            "temperature": 0.2,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{settings.api_base}/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key}",
            "User-Agent": "alfred-runtime/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise UpstreamError(f"Endpoint returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise UpstreamError(f"Could not reach {settings.api_base}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise UpstreamError(
            f"Request to {settings.api_base} timed out after {settings.timeout_seconds}s."
        ) from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UpstreamError("Endpoint returned a non-JSON response.") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise UpstreamError("Response JSON is missing choices[0].message.content.") from exc

    if not isinstance(content, str) or not content.strip():
        raise UpstreamError("Model returned an empty response.")
    return content.strip()
