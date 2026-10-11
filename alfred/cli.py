"""Command-line interface for A.L.F.R.E.D.

Subcommands:
    show    print the assembled system prompt
    stats   report size and a rough token estimate
    build   (re)assemble the prompt from prompts/
    env     show the configuration contract (never prints secrets)
    ask     one-shot question against the model
    chat    interactive multi-turn session
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from . import __version__, runtime
from .runtime import AlfredError, DEFAULT_MODEL

PROMPT_FILENAME = runtime.DEFAULT_PROMPT_FILENAME


def _load_builder_module():
    """Import scripts/build_prompt.py without requiring scripts/ to be a package."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "build_prompt.py"
    if not script.is_file():
        raise AlfredError(f"builder not found at {script}")
    spec = importlib.util.spec_from_file_location("alfred_build_prompt", script)
    if spec is None or spec.loader is None:
        raise AlfredError(f"could not load builder from {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cmd_build(args: argparse.Namespace) -> int:
    builder = _load_builder_module()
    forwarded: list[str] = []
    if args.check:
        forwarded.append("--check")
    elif args.stdout:
        forwarded.append("--stdout")
    return builder.main(forwarded)


def _cmd_show(args: argparse.Namespace) -> int:
    prompt = runtime.load_system_prompt()
    if args.json:
        json.dump(
            {
                "path": str(runtime.find_repo_root() / PROMPT_FILENAME),
                "bytes": len(prompt.encode("utf-8")),
                "system_prompt": prompt,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        sys.stdout.write(prompt)
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    prompt = runtime.load_system_prompt()
    modules = sorted(
        str(p.relative_to(runtime.find_repo_root() / "prompts"))
        for p in (runtime.find_repo_root() / "prompts").rglob("*.md")
    )
    report = {
        "path": str(runtime.find_repo_root() / PROMPT_FILENAME),
        "bytes": len(prompt.encode("utf-8")),
        "characters": len(prompt),
        "lines": prompt.count("\n") + 1,
        "estimated_tokens": runtime.estimate_tokens(prompt),
        "source_modules": len(modules),
        "modules": modules,
    }
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for key, value in report.items():
            if key == "modules":
                continue
            print(f"{key:>16}: {value}")
        print(f"{'modules':>16}:")
        for name in modules:
            print(f"                 - {name}")
    return 0


def _cmd_env(args: argparse.Namespace) -> int:
    env = os.environ
    key_present = bool(
        (env.get(runtime.ENV_API_KEY) or env.get("OPENAI_API_KEY") or "").strip()
    )
    report = {
        "api_key": {
            "env_var": runtime.ENV_API_KEY,
            "fallback_env_var": "OPENAI_API_KEY",
            "set": key_present,
            "value": "(hidden)" if key_present else "(unset)",
        },
        "api_base": {
            "env_var": runtime.ENV_API_BASE,
            "fallback_env_var": "OPENAI_BASE_URL",
            "value": env.get(runtime.ENV_API_BASE)
            or env.get("OPENAI_BASE_URL")
            or runtime.DEFAULT_API_BASE,
        },
        "model": {
            "env_var": runtime.ENV_MODEL,
            "fallback_env_var": None,
            "value": env.get(runtime.ENV_MODEL) or DEFAULT_MODEL,
        },
    }
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _prepare() -> tuple[str, runtime.Settings]:
    prompt = runtime.load_system_prompt()
    return prompt, runtime.resolve_settings()


def _cmd_ask(args: argparse.Namespace) -> int:
    prompt, settings = _prepare()
    reply = runtime.chat(args.prompt, prompt, settings)
    sys.stdout.write(reply + "\n")
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    prompt, settings = _prepare()
    print(f"A.L.F.R.E.D. ready | model={settings.model} | Ctrl-D to exit.", file=sys.stderr)
    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[session ended]", file=sys.stderr)
            return 0
        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            return 0
        try:
            reply = runtime.chat(user_text, prompt, settings)
        except AlfredError as exc:
            print(f"[error] {exc}", file=sys.stderr)
            continue
        print(f"\nalfred> {reply}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alfred",
        description="A.L.F.R.E.D. prompt assembler and thin LLM runtime.",
    )
    parser.add_argument("--version", action="version", version=f"alfred {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_show = sub.add_parser("show", help="print the assembled system prompt")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=_cmd_show)

    p_stats = sub.add_parser("stats", help="report prompt size and token estimate")
    p_stats.add_argument("--json", action="store_true")
    p_stats.set_defaults(func=_cmd_stats)

    p_build = sub.add_parser("build", help="assemble prompts/ into " + PROMPT_FILENAME)
    group = p_build.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="fail if output is stale")
    group.add_argument("--stdout", action="store_true", help="print instead of writing")
    p_build.set_defaults(func=_cmd_build)

    p_env = sub.add_parser("env", help="show the configuration contract")
    p_env.set_defaults(func=_cmd_env)

    p_ask = sub.add_parser("ask", help="one-shot question (requires an API key)")
    p_ask.add_argument("prompt", help="the question to send")
    p_ask.set_defaults(func=_cmd_ask)

    p_chat = sub.add_parser("chat", help="interactive session (requires an API key)")
    p_chat.set_defaults(func=_cmd_chat)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AlfredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
