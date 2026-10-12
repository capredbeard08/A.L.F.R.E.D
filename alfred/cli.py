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
from .tools import search as search_tool
from .tools.search import PROVIDERS, SearchError
from . import roles as roles_mod

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
        "search_api_key": {
            "env_var": runtime.ENV_TAVILY_API_KEY,
            "fallback_env_var": runtime.ENV_SEARCH_API_KEY,
            "set": bool((env.get(runtime.ENV_TAVILY_API_KEY) or env.get(runtime.ENV_SEARCH_API_KEY) or "").strip()),
            "value": "(hidden)" if (env.get(runtime.ENV_TAVILY_API_KEY) or env.get(runtime.ENV_SEARCH_API_KEY)) else "(unset)",
        },
        "search_provider": {
            "resolved": search_tool.available_provider(
                (env.get(runtime.ENV_TAVILY_API_KEY) or env.get(runtime.ENV_SEARCH_API_KEY) or "").strip() or None
            ),
            "available": list(PROVIDERS),
        },
    }
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _prepare(role: str | None = None) -> tuple[str, runtime.Settings]:
    """Load the system prompt, adopting a professional role if one was given."""
    prompt = runtime.load_system_prompt()
    if role:
        prompt = roles_mod.apply_role(prompt, role)
    return prompt, runtime.resolve_settings()


def _cmd_roles(args: argparse.Namespace) -> int:
    if args.show:
        sys.stdout.write(roles_mod.get_role(args.show) + "\n")
        return 0

    grouped = roles_mod.categories()
    if args.category:
        wanted = args.category.strip().lower()
        grouped = {k: v for k, v in grouped.items() if k.lower() == wanted}
        if not grouped:
            known = ", ".join(sorted(roles_mod.categories()))
            print(f"error: unknown category {args.category!r}. Known: {known}", file=sys.stderr)
            return 1

    total = sum(len(v) for v in grouped.values())
    if args.json:
        json.dump({"total": total, "categories": grouped}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    for category in sorted(grouped):
        print(f"{category} ({len(grouped[category])})")
        for role_id in grouped[category]:
            print(f"  {role_id:<32} {roles_mod.role_title(role_id)}")
    print(f"\n{total} roles. Use --role <id>, or: alfred roles --show <id>")
    return 0


def _grounded_system_prompt(prompt: str, question: str, settings: runtime.Settings,
                            max_results: int) -> tuple[str, list[search_tool.SearchResult], str]:
    """Run a live search and fold the results into the system prompt."""
    provider = search_tool.available_provider(settings.search_api_key)
    results = search_tool.search(
        question,
        api_key=settings.search_api_key,
        provider=provider,
        max_results=max_results,
    )
    block = search_tool.build_grounding_block(results, question, provider)
    return prompt + "\n\n" + block, results, provider


def _cmd_search(args: argparse.Namespace) -> int:
    settings = runtime.resolve_settings(require_api_key=False)
    provider = args.provider or search_tool.available_provider(settings.search_api_key)
    results = search_tool.search(
        args.query,
        api_key=settings.search_api_key,
        provider=provider,
        max_results=args.max_results,
    )
    if args.json:
        json.dump(
            {
                "query": args.query,
                "provider": provider,
                "results": [
                    {
                        "title": r.title,
                        "url": r.url,
                        "snippet": r.snippet,
                        "score": r.score,
                        "published": r.published,
                    }
                    for r in results
                ],
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        sys.stdout.write(search_tool.format_brief(results, args.query) + "\n")
    return 0


def _cmd_research(args: argparse.Namespace) -> int:
    if args.ask:
        prompt, settings = _prepare(args.role)
    else:
        # Retrieval-only mode needs no LLM key: only the optional search key matters.
        prompt, settings = "", runtime.resolve_settings(require_api_key=False)

    provider = search_tool.available_provider(settings.search_api_key)
    results = search_tool.search(
        args.query,
        api_key=settings.search_api_key,
        provider=provider,
        max_results=args.max_results,
    )

    if not args.ask:
        # Retrieval only: hand back the cited sources without spending a model call.
        sys.stdout.write(search_tool.format_brief(results, args.query) + f"\n(provider: {provider})\n")
        return 0

    grounded = prompt + "\n\n" + search_tool.build_grounding_block(results, args.query, provider)
    messages = [
        {"role": "system", "content": grounded},
        {"role": "user", "content": (
            f"Research and brief me on: {args.query}\n\n"
            "Open with BLUF. Cite sources inline as [1], [2]. Close with a short "
            "source list using the exact URLs you were given."
        )},
    ]
    reply = runtime.chat_messages(messages, settings)
    sys.stdout.write(reply + "\n")
    if args.json:
        json.dump(
            {"query": args.query, "provider": provider,
             "sources": [{"title": r.title, "url": r.url} for r in results]},
            sys.stderr, indent=2,
        )
        sys.stderr.write("\n")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    prompt, settings = _prepare(args.role)
    if args.web:
        prompt, _results, _provider = _grounded_system_prompt(prompt, args.prompt, settings, args.max_results)
    reply = runtime.chat(args.prompt, prompt, settings)
    sys.stdout.write(reply + "\n")
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    prompt, settings = _prepare(args.role)
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

    p_search = sub.add_parser("search", help="run a live web query and print cited results")
    p_search.add_argument("query")
    p_search.add_argument("--max-results", type=int, default=search_tool.DEFAULT_MAX_RESULTS)
    p_search.add_argument("--provider", choices=PROVIDERS, default=None)
    p_search.add_argument("--json", action="store_true")
    p_search.set_defaults(func=_cmd_search)

    p_research = sub.add_parser("research", help="search, then optionally brief via the model")
    p_research.add_argument("query")
    p_research.add_argument("--ask", action="store_true", help="synthesize a cited brief (needs an API key)")
    p_research.add_argument("--max-results", type=int, default=search_tool.DEFAULT_MAX_RESULTS)
    p_research.add_argument("--json", action="store_true", help="also emit sources as JSON on stderr")
    p_research.add_argument("--role", default=None, help="adopt a professional role (see: alfred roles)")
    p_research.set_defaults(func=_cmd_research)

    p_ask = sub.add_parser("ask", help="one-shot question (requires an API key)")
    p_ask.add_argument("prompt", help="the question to send")
    p_ask.add_argument("--web", action="store_true", help="ground the answer in a live web search first")
    p_ask.add_argument("--role", default=None, help="adopt a professional role (see: alfred roles)")
    p_ask.add_argument("--max-results", type=int, default=search_tool.DEFAULT_MAX_RESULTS)
    p_ask.set_defaults(func=_cmd_ask)

    p_chat = sub.add_parser("chat", help="interactive session (requires an API key)")
    p_chat.add_argument("--role", default=None, help="adopt a professional role (see: alfred roles)")
    p_chat.set_defaults(func=_cmd_chat)

    p_roles = sub.add_parser("roles", help="list or inspect the professional role taxonomy")
    p_roles.add_argument("--category", default=None, help="filter by category, e.g. Advisor")
    p_roles.add_argument("--show", default=None, metavar="ROLE_ID", help="print one role block")
    p_roles.add_argument("--json", action="store_true")
    p_roles.set_defaults(func=_cmd_roles)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SearchError as exc:
        print(f"search error: {exc}", file=sys.stderr)
        return 2
    except AlfredError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
