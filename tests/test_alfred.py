"""Tests for the A.L.F.R.E.D. prompt assets and thin runtime.

Stdlib only: `python3 -m unittest discover -s tests -v`. No third-party deps.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from alfred import runtime  # noqa: E402
from alfred import cli  # noqa: E402


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "alfred_build_prompt_under_test", REPO_ROOT / "scripts" / "build_prompt.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


class BuildTests(unittest.TestCase):
    """The assembler must be deterministic and complete."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_every_declared_module_exists_and_is_non_empty(self) -> None:
        for rel in builder.MODULES:
            with self.subTest(module=rel):
                path = builder.PROMPTS_DIR / rel
                self.assertTrue(path.is_file(), f"missing: {path}")
                self.assertTrue(path.read_text(encoding="utf-8").strip(), f"empty: {path}")

    def test_domain_modules_cover_all_domains(self) -> None:
        domains = sorted(p.name for p in (builder.PROMPTS_DIR / "domains").glob("*.md"))
        # One module per numbered domain section; adding one requires updating
        # MODULES, REQUIRED_MARKERS, and this set together.
        expected = {
            "business_corporate_strategy.md",
            "data_systems_engineering.md",
            "financial_capital_allocation.md",
            "governance_intelligence.md",
            "hardware_fabrication.md",
            "tools_and_research.md",
        }
        self.assertEqual(set(domains), expected, f"domain set drifted: {domains}")

    def test_every_module_is_registered_in_modules(self) -> None:
        on_disk = {
            str(p.relative_to(builder.PROMPTS_DIR))
            for p in builder.PROMPTS_DIR.rglob("*.md")
        }
        self.assertEqual(on_disk, set(builder.MODULES), "unregistered or missing prompt module")

    def test_assemble_is_deterministic(self) -> None:
        self.assertEqual(builder.assemble(), builder.assemble())

    def test_required_sections_present(self) -> None:
        prompt = builder.assemble()
        for marker in builder.REQUIRED_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, prompt)

    def test_committed_output_is_in_sync(self) -> None:
        out = REPO_ROOT / "ALFRED.system.md"
        self.assertTrue(out.is_file(), "ALFRED.system.md missing; run scripts/build_prompt.py")
        self.assertEqual(out.read_text(encoding="utf-8"), builder.assemble())

    def test_missing_module_raises(self) -> None:
        with mock.patch.object(builder, "MODULES", ("persona.md", "nope.md")):
            with self.assertRaises(builder.BuildError):
                builder.assemble()

    def test_empty_module_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.md"
            empty.write_text("   \n", encoding="utf-8")
            with mock.patch.object(builder, "PROMPTS_DIR", Path(tmp)):
                with mock.patch.object(builder, "MODULES", ("empty.md",)):
                    with self.assertRaises(builder.BuildError):
                        builder.assemble()


class RuntimePromptTests(unittest.TestCase):
    """The runtime must load the real assembled prompt."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def test_load_system_prompt_returns_real_prompt(self) -> None:
        prompt = runtime.load_system_prompt()
        self.assertTrue(prompt.strip())
        for marker in builder.REQUIRED_MARKERS:
            self.assertIn(marker, prompt)

    def test_load_from_explicit_path(self) -> None:
        prompt = runtime.load_system_prompt(REPO_ROOT / "ALFRED.system.md")
        self.assertGreater(len(prompt), 1000)

    def test_missing_path_raises_prompt_not_found(self) -> None:
        with self.assertRaises(runtime.PromptNotFoundError):
            runtime.load_system_prompt(REPO_ROOT / "does-not-exist.md")

    def test_estimate_tokens_sane(self) -> None:
        self.assertEqual(runtime.estimate_tokens(""), 1)
        self.assertEqual(runtime.estimate_tokens("a" * 400), 100)


class SettingsTests(unittest.TestCase):
    """Explicit args win, then env, then defaults. Secrets never leak."""

    def test_explicit_args_take_precedence(self) -> None:
        s = runtime.resolve_settings(
            api_key="sk-explicit",
            api_base="https://example.com/v1/",
            model="m1",
            timeout_seconds=5.0,
            environ={"ALFRED_API_KEY": "sk-env", "ALFRED_API_BASE": "https://other/v1", "ALFRED_MODEL": "m2"},
        )
        self.assertEqual(s.api_key, "sk-explicit")
        self.assertEqual(s.api_base, "https://example.com/v1")
        self.assertEqual(s.model, "m1")
        self.assertEqual(s.timeout_seconds, 5.0)

    def test_env_fallbacks_and_defaults(self) -> None:
        s = runtime.resolve_settings(environ={"ALFRED_API_KEY": "sk-env"})
        self.assertEqual(s.api_key, "sk-env")
        self.assertEqual(s.api_base, runtime.DEFAULT_API_BASE)
        self.assertEqual(s.model, runtime.DEFAULT_MODEL)

    def test_openai_fallback_env_vars(self) -> None:
        s = runtime.resolve_settings(environ={"OPENAI_API_KEY": "sk-oa", "OPENAI_BASE_URL": "https://oa/v1"})
        self.assertEqual(s.api_key, "sk-oa")
        self.assertEqual(s.api_base, "https://oa/v1")

    def test_missing_key_raises(self) -> None:
        with self.assertRaises(runtime.ConfigError):
            runtime.resolve_settings(environ={})

    def test_bad_base_raises(self) -> None:
        with self.assertRaises(runtime.ConfigError):
            runtime.resolve_settings(api_key="k", api_base="ftp://x", environ={})

    def test_non_positive_timeout_raises(self) -> None:
        with self.assertRaises(runtime.ConfigError):
            runtime.resolve_settings(api_key="k", timeout_seconds=0, environ={})

    def test_settings_repr_does_not_leak_key(self) -> None:
        s = runtime.resolve_settings(api_key="sk-super-secret-value", environ={})
        self.assertNotIn("sk-super-secret-value", repr(s))
        self.assertNotIn("sk-super-secret-value", str(s))


class ChatTests(unittest.TestCase):
    """chat() must be thin, bounded, and loud about upstream failures."""

    def _settings(self) -> runtime.Settings:
        return runtime.Settings(
            api_key="sk-test", api_base="https://api.example.com/v1", model="m", timeout_seconds=5.0
        )

    def test_empty_user_text_rejected(self) -> None:
        with self.assertRaises(runtime.ConfigError):
            runtime.chat("   ", "sys", self._settings())

    def test_build_messages_shape(self) -> None:
        msgs = runtime.build_messages("sys", "hi")
        self.assertEqual(msgs, [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ])

    def test_successful_reply(self) -> None:
        body = json.dumps({"choices": [{"message": {"content": "  answer  "}}]}).encode()
        fake = mock.MagicMock()
        fake.__enter__.return_value.read.return_value = body
        with mock.patch.object(runtime.urllib.request, "urlopen", return_value=fake):
            self.assertEqual(runtime.chat("q", "sys", self._settings()), "answer")

    def test_http_error_becomes_upstream_error(self) -> None:
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b"bad key"))
        with mock.patch.object(runtime.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(runtime.UpstreamError) as ctx:
                runtime.chat("q", "sys", self._settings())
        self.assertIn("401", str(ctx.exception))

    def test_url_error_becomes_upstream_error(self) -> None:
        with mock.patch.object(
            runtime.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")
        ):
            with self.assertRaises(runtime.UpstreamError):
                runtime.chat("q", "sys", self._settings())

    def test_empty_choices_becomes_upstream_error(self) -> None:
        body = json.dumps({"choices": []}).encode()
        fake = mock.MagicMock()
        fake.__enter__.return_value.read.return_value = body
        with mock.patch.object(runtime.urllib.request, "urlopen", return_value=fake):
            with self.assertRaises(runtime.UpstreamError):
                runtime.chat("q", "sys", self._settings())

    def test_non_json_becomes_upstream_error(self) -> None:
        fake = mock.MagicMock()
        fake.__enter__.return_value.read.return_value = b"<html>oops</html>"
        with mock.patch.object(runtime.urllib.request, "urlopen", return_value=fake):
            with self.assertRaises(runtime.UpstreamError):
                runtime.chat("q", "sys", self._settings())

    def test_authorization_header_carries_bearer_token(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            fake = mock.MagicMock()
            fake.__enter__.return_value.read.return_value = json.dumps(
                {"choices": [{"message": {"content": "ok"}}]}
            ).encode()
            return fake

        with mock.patch.object(runtime.urllib.request, "urlopen", side_effect=fake_urlopen):
            runtime.chat("q", "sys", self._settings())
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(headers["authorization"], "Bearer sk-test")
        self.assertEqual(captured["timeout"], 5.0)


class CliTests(unittest.TestCase):
    """Offline CLI paths must work without any API key."""

    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_show_prints_prompt(self) -> None:
        code, out, _ = self._run(["show"])
        self.assertEqual(code, 0)
        self.assertIn("A.L.F.R.E.D.", out)

    def test_show_json_is_valid(self) -> None:
        code, out, _ = self._run(["show", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("A.L.F.R.E.D.", data["system_prompt"])
        self.assertGreater(data["bytes"], 1000)

    def test_stats_json_reports_modules(self) -> None:
        code, out, _ = self._run(["stats", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["source_modules"], len(builder.MODULES))
        self.assertEqual(len(data["modules"]), len(builder.MODULES))
        self.assertGreater(data["estimated_tokens"], 0)

    def test_build_check_passes(self) -> None:
        code, out, _ = self._run(["build", "--check"])
        self.assertEqual(code, 0)
        self.assertIn("in sync", out)

    def test_env_reports_contract_without_secret(self) -> None:
        with mock.patch.dict(os.environ, {"ALFRED_API_KEY": "sk-secret-xyz"}, clear=False):
            code, out, _ = self._run(["env"])
        self.assertEqual(code, 0)
        self.assertNotIn("sk-secret-xyz", out)
        self.assertIn("(hidden)", out)

    def test_ask_without_key_exits_nonzero(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("ALFRED_API_KEY", "OPENAI_API_KEY")}
        with mock.patch.dict(os.environ, env, clear=True):
            code, _, err = self._run(["ask", "hello"])
        self.assertEqual(code, 1)
        self.assertIn("ALFRED_API_KEY", err)

    def test_version_flag(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
