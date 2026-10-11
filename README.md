# A.L.F.R.E.D.

Modular prompt assets for the A.L.F.R.E.D. system prompt, plus a thin, dependency-free Python runtime that loads the assembled prompt and talks to any OpenAI-compatible endpoint.

The prompt is authored as small, reviewable modules. `ALFRED.system.md` is a **generated artifact** — edit `prompts/`, never the output.

## Layout

```
prompts/
  persona.md                  # 1. Core identity & philosophy
  domains/
    financial_capital_allocation.md   # 2.1
    business_corporate_strategy.md    # 2.2
    data_systems_engineering.md       # 2.3
    hardware_fabrication.md           # 2.4
    governance_intelligence.md        # 2.5
  bluf_protocol.md            # 3. Executive communication protocol
  code_tool_execution.md      # 4. Code & tool execution rules
  tone.md                     # 5. Default tone & character
scripts/build_prompt.py       # deterministic assembler
ALFRED.system.md              # GENERATED — do not hand-edit
alfred/
  runtime.py                  # prompt loader + stdlib LLM client
  cli.py                      # command-line interface
tests/test_alfred.py          # stdlib unittest suite
```

## Requirements

Python 3.10+. No third-party runtime dependencies; nothing to install to use the CLI.

## Usage

Run commands from the repo root, or install the console script:

```bash
pip install -e .      # optional; provides the `alfred` command
```

```bash
python3 -m alfred build          # regenerate ALFRED.system.md from prompts/
python3 -m alfred build --check  # CI guard: fail if the artifact is stale
python3 -m alfred show           # print the assembled system prompt
python3 -m alfred show --json    # same, as JSON
python3 -m alfred stats          # sizes + rough token estimate
python3 -m alfred env            # configuration contract (never prints secrets)
python3 -m alfred ask "question" # one-shot; requires an API key
python3 -m alfred chat           # interactive session; requires an API key
```

`build`, `show`, `stats`, `env`, and `--version` all work offline with no API key.

## Configuration

| Variable | Fallback | Default | Purpose |
|----------|----------|---------|---------|
| `ALFRED_API_KEY` | `OPENAI_API_KEY` | — (required for `ask`/`chat`) | Bearer token |
| `ALFRED_API_BASE` | `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `ALFRED_MODEL` | — | `gpt-4o-mini` | Model name |

Copy `.env.example` to `.env` to keep values out of the shell history. `.env` is gitignored. The API key is never printed by any subcommand, and `Settings` suppresses it from `repr`.

## Testing

```bash
python3 -m unittest discover -s tests -v
```

33 tests, stdlib only. They cover assembler determinism, module coverage, artifact freshness, runtime error paths, upstream failure handling, and the guarantee that secrets never reach stdout.

## Editing the prompt

1. Edit the relevant module under `prompts/`.
2. If you add a module, append it to `MODULES` in `scripts/build_prompt.py` (order is part of the contract).
3. If you add a new top-level section, add its heading text to `REQUIRED_MARKERS` so an emptied module fails the build.
4. `python3 -m alfred build`, then commit the regenerated `ALFRED.system.md` alongside the module change.

## Notes and limits

- Token counts are a `len(text) // 4` heuristic, not a real tokenizer. Directionally useful only.
- The runtime is non-streaming by design: one POST, bounded timeout, small failure surface.
- End-to-end model calls are not covered by the test suite; the network layer is tested with mocked responses.
