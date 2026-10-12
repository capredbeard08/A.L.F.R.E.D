# A.L.F.R.E.D.

A.L.F.R.E.D. (Autonomous Logic, Financial, Research, &amp; Engineering Director) is a private, agentic intelligence architecture designed to act as an elite Chief of Staff, Lead Systems Engineer, and Principal Analyst.

Modular prompt assets for the A.L.F.R.E.D. system prompt, plus a thin, dependency-free Python runtime that loads the assembled prompt, talks to any OpenAI-compatible endpoint, and can execute live web research with citations.

The prompt is authored as small, reviewable modules. `ALFRED.system.md` is a **generated artifact** — edit `prompts/`, never the output.

## Layout

```
prompts/
  persona.md                          # 1. Core identity & philosophy
  domains/
    financial_capital_allocation.md   # 2.1
    business_corporate_strategy.md    # 2.2
    data_systems_engineering.md       # 2.3
    hardware_fabrication.md           # 2.4
    governance_intelligence.md        # 2.5
    tools_and_research.md             # 2.6 web tools vs offline reasoning, citation rules
    07_roles.md                       # 2.7 professional role taxonomy (69 roles)
  bluf_protocol.md                    # 3. Executive communication protocol
  code_tool_execution.md              # 4. Code & tool execution rules
  tone.md                             # 5. Default tone & character
scripts/build_prompt.py               # deterministic assembler
ALFRED.system.md                      # GENERATED — do not hand-edit
alfred/
  runtime.py                          # prompt loader + stdlib LLM client + Settings
  cli.py                              # command-line interface
  roles.py                            # role registry parser and prompt compiler
  tools/
    search.py                         # multi-provider web search tool
tests/
  test_alfred.py                      # prompt + runtime tests
  test_search.py                      # tool, settings, and CLI tests
  test_roles.py                       # role taxonomy loading, switching, compilation
```

## Requirements

Python 3.10+. No third-party runtime dependencies; nothing to install to use the CLI.

Optional, used automatically **only if installed**: `duckduckgo_search` (or its newer `ddgs` fork) for a keyless search provider.

## Usage

Run commands from the repo root, or install the console script:

```bash
pip install -e .      # optional; provides the `alfred` command
```

```bash
python3 -m alfred build            # regenerate ALFRED.system.md from prompts/
python3 -m alfred build --check    # CI guard: fail if the artifact is stale
python3 -m alfred show             # print the assembled system prompt
python3 -m alfred show --json      # same, as JSON
python3 -m alfred stats            # sizes + rough token estimate
python3 -m alfred env              # configuration contract (never prints secrets)
python3 -m alfred ask "question"   # one-shot; requires an API key
python3 -m alfred chat             # interactive session; requires an API key
python3 -m alfred roles            # list the professional role taxonomy
python3 -m alfred roles --category Advisor
python3 -m alfred roles --show advisor-tax
```

### Tool execution

```bash
python3 -m alfred search "NVDA stock price today" --max-results 5
python3 -m alfred search "rust 1.80 release" --json
python3 -m alfred research "semiconductor export controls"          # retrieve cited sources only
python3 -m alfred research "semiconductor export controls" --ask    # + model brief, inline citations
python3 -m alfred ask "what changed in Python 3.12?" --web           # ground a normal ask in search
```

`build`, `show`, `stats`, `env`, `search`, `research` (without `--ask`), and `--version` all work offline with no LLM API key.

### Professional roles

A.L.F.R.E.D. can adopt one of 69 professional roles across ten categories —
Strategist, Advisor, Consultant, Specialist, Associate, Planner, Officer,
Researcher, Architect, and Auditor/Controller. A role changes the lens,
methodology, and output format; it never relaxes BLUF, citation, or honesty rules.

```bash
python3 -m alfred ask "review this cap table" --role associate-investment-banking
python3 -m alfred chat --role architect-security
python3 -m alfred research "semiconductor export controls" --ask --role advisor-policy
```

Roles are defined in `prompts/domains/07_roles.md`, one `### role: <id>` block per
role, and compiled into the built prompt. `--role` inserts an activation note
rather than repeating the definition, so switching roles stays cheap. Unknown ids
fail with the nearest valid suggestions.

## Configuration

| Variable | Fallback | Default | Purpose |
|----------|----------|---------|---------|
| `ALFRED_API_KEY` | `OPENAI_API_KEY` | — (required for `ask`/`chat`/`research --ask`) | LLM bearer token |
| `ALFRED_API_BASE` | `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `ALFRED_MODEL` | — | `gpt-4o-mini` | Model name |
| `TAVILY_API_KEY` | `SEARCH_API_KEY` | — (optional) | Keyed web search provider |

Copy `.env.example` to `.env` to keep values out of the shell history. `.env` is gitignored. Neither secret is printed by any subcommand, and `Settings` suppresses both from `repr`.

### Search provider resolution

The first available provider wins; there is **no silent cascade on failure**, because a partial result set presented as complete is worse than a loud error.

| Order | Provider | Requirement |
|-------|----------|-------------|
| 1 | `tavily` | `TAVILY_API_KEY` or `SEARCH_API_KEY` |
| 2 | `duckduckgo` | `duckduckgo_search` / `ddgs` package installed |
| 3 | `urllib` | always available; parses `html.duckduckgo.com` |

Override per call with `--provider {tavily,duckduckgo,urllib}`.

> **Reliability warning.** The `urllib` fallback is best-effort. DuckDuckGo rate-limits aggressively and will serve an anti-bot page after a handful of requests from one IP; A.L.F.R.E.D. detects this and fails loudly rather than returning nothing. For anything you depend on, set `TAVILY_API_KEY`.

## Testing

```bash
python3 -m unittest discover -s tests -v
```

126 tests, stdlib only, no network. They cover assembler determinism, module coverage, artifact freshness, prompt-vs-tool routing, DuckDuckGo HTML parsing against a captured fixture, Tavily request/response shape, every error path, and the guarantee that both secrets never reach stdout, and that all 69 specified
roles exist with complete fields and switch cleanly.

## Editing the prompt

1. Edit the relevant module under `prompts/`.
2. If you add a module, append it to `MODULES` in `scripts/build_prompt.py` (order is part of the contract).
3. If you add a new top-level section, add its heading text to `REQUIRED_MARKERS` so an emptied module fails the build.
4. `python3 -m alfred build`, then commit the regenerated `ALFRED.system.md` alongside the module change.

`tests/test_alfred.py` asserts the set of domain modules matches the set registered in `MODULES`, so adding one without registering it fails the build.

## Adding a tool

Create `alfred/tools/<name>.py` exposing a `search()`-style entry point that raises `AlfredError` on failure and never returns a silently empty result. Keep it stdlib-only at rest and probe any optional dependency at call time, as `search.py` does.

## Notes and limits

- Token counts are a `len(text) // 4` heuristic, not a real tokenizer. Directionally useful only.
- The LLM transport is non-streaming by design: one POST, bounded timeout, small failure surface.
- End-to-end model calls are not covered by the test suite; the network layer is tested with mocked responses.
- Live web search was verified working against DuckDuckGo from this environment before rate limiting engaged; subsequent calls correctly returned a block error.
