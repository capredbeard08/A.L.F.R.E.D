"""Tests for the web-search tool and its settings/CLI wiring.

Network is never touched: the HTTP layer is mocked, and the DuckDuckGo parser
is exercised against a captured HTML fixture.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from alfred import cli, runtime  # noqa: E402
from alfred.tools import search as search_mod  # noqa: E402
from alfred.tools.search import SearchError, SearchResult  # noqa: E402

# A trimmed capture of DuckDuckGo's HTML endpoint, including a redirect-wrapped
# href and an entity that must be unescaped.
DDG_FIXTURE = """
<html><body>
<div class="result results_links results_links_deep web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2Fdownloads%2Frelease%2Fpython-3120%2F&amp;rut=abcdef">
         Python Release Python 3.12.0 | Python.org</a>
    </h2>
    <a class="result__snippet"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=xyz">
       Release date: Oct. 2, 2023 &amp; stable release of Python 3.12.0</a>
  </div>
</div>
<div class="result results_links web-result">
  <div class="links_main links_deep result__body">
    <h2 class="result__title">
      <a rel="nofollow" class="result__a" href="https://docs.python.org/3/whatsnew/3.12.html">
         What&#x27;s New In Python 3.12</a>
    </h2>
    <div class="result__snippet">Python 3.12 was released on October 2, 2023.</div>
  </div>
</div>
<div class="result spam web-result"><div class="links_main result__body">
  <h2 class="result__title"><a class="result__a" href="">Broken empty link</a></h2>
</div></div>
</body></html>
"""


class SearchResultTests(unittest.TestCase):
    def test_valid_result_accepted(self) -> None:
        r = SearchResult(title="T", url="https://example.com", snippet="S", provider="urllib")
        self.assertEqual(r.url, "https://example.com")
        self.assertIsNone(r.score)

    def test_relative_url_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SearchResult(title="T", url="/relative", snippet="S", provider="urllib")

    def test_score_and_published_round_trip(self) -> None:
        r = SearchResult(
            title="T", url="https://e.com", snippet="S", provider="tavily",
            score=0.91, published="2026-01-01",
        )
        self.assertEqual(r.score, 0.91)
        self.assertEqual(r.published, "2026-01-01")


class ProviderSelectionTests(unittest.TestCase):
    def test_key_selects_tavily(self) -> None:
        self.assertEqual(search_mod.available_provider("tvly-abc"), "tavily")

    def test_no_key_falls_back(self) -> None:
        self.assertIn(search_mod.available_provider(None), ("duckduckgo", "urllib"))

    def test_fallback_is_urllib_when_no_optional_lib(self) -> None:
        with mock.patch.object(search_mod, "_has_module", return_value=False):
            self.assertEqual(search_mod.available_provider(None), "urllib")


class SearchValidationTests(unittest.TestCase):
    def test_empty_query_rejected(self) -> None:
        with self.assertRaises(SearchError):
            search_mod.search("   ")

    def test_max_results_bounds_rejected(self) -> None:
        with self.assertRaises(SearchError):
            search_mod.search("q", max_results=0)

    def test_non_positive_timeout_rejected(self) -> None:
        with self.assertRaises(SearchError):
            search_mod.search("q", timeout=0)

    def test_unknown_provider_rejected(self) -> None:
        with self.assertRaises(SearchError):
            search_mod.search("q", provider="bing")

    def test_tavily_without_key_rejected(self) -> None:
        with self.assertRaises(SearchError):
            search_mod.search("q", provider="tavily")


class DuckDuckGoParserTests(unittest.TestCase):
    def test_redirect_url_decoded(self) -> None:
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&rut=x"
        self.assertEqual(search_mod._decode_ddg_redirect(href), "https://example.com/a?b=1")

    def test_plain_url_passes_through(self) -> None:
        self.assertEqual(
            search_mod._decode_ddg_redirect("https://example.com/x"), "https://example.com/x"
        )

    def test_empty_and_relative_yield_empty(self) -> None:
        self.assertEqual(search_mod._decode_ddg_redirect(""), "")
        self.assertEqual(search_mod._decode_ddg_redirect("/relative"), "")

    def test_fixture_parses_expected_results(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value=DDG_FIXTURE):
            results = search_mod.search("python 3.12", provider="urllib", max_results=5)
        self.assertEqual(len(results), 2)  # the empty-href entry is dropped
        self.assertEqual(results[0].url, "https://www.python.org/downloads/release/python-3120/")
        self.assertIn("3.12.0", results[0].title)
        self.assertIn("& stable release", results[0].snippet)  # unescaped
        self.assertEqual(results[1].url, "https://docs.python.org/3/whatsnew/3.12.html")
        self.assertIn("'s New", results[1].title)  # &#x27; unescaped
        self.assertEqual({r.provider for r in results}, {"urllib"})

    def test_max_results_respected(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value=DDG_FIXTURE):
            results = search_mod.search("q", provider="urllib", max_results=1)
        self.assertEqual(len(results), 1)

    def test_anomaly_page_raises(self) -> None:
        page = "<html><body>Unfortunately, users viewing search results from an automated \
bot can trigger watermarks</body></html>"
        with mock.patch.object(search_mod, "_http_get", return_value=page):
            with self.assertRaises(SearchError) as ctx:
                search_mod.search("q", provider="urllib")
        self.assertIn("anti-bot", str(ctx.exception))
        self.assertIn("TAVILY_API_KEY", str(ctx.exception))

    def test_alternate_block_phrasing_detected(self) -> None:
        page = "<html><body>anomaly detected</body></html>"
        with mock.patch.object(search_mod, "_http_get", return_value=page):
            with self.assertRaises(SearchError):
                search_mod.search("q", provider="urllib")

    def test_bot_word_in_real_results_is_not_a_false_positive(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value=DDG_FIXTURE):
            results = search_mod.search("bot", provider="urllib", max_results=5)
        self.assertEqual(len(results), 2)

    def test_unparseable_page_raises(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value="<html><body>nothing</body></html>"):
            with self.assertRaises(SearchError):
                search_mod.search("q", provider="urllib")

    def test_empty_page_raises(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value=""):
            with self.assertRaises(SearchError):
                search_mod.search("q", provider="urllib")

    def test_query_is_url_encoded(self) -> None:
        captured: dict[str, str] = {}

        def fake_get(url: str, timeout: float) -> str:
            captured["url"] = url
            return DDG_FIXTURE

        with mock.patch.object(search_mod, "_http_get", side_effect=fake_get):
            search_mod.search("a b&c", provider="urllib", max_results=1)
        self.assertIn("q=a+b%26c", captured["url"])


class TavilyTests(unittest.TestCase):
    def test_results_parsed_and_truncated(self) -> None:
        payload = {
            "results": [
                {"title": "A", "url": "https://a.example", "content": "ca", "score": 0.9},
                {"title": "B", "url": "https://b.example", "content": "cb",
                 "score": 0.8, "published_date": "2026-01-02"},
                {"title": "no url", "url": "", "content": "skip"},
                {"title": "not a dict", "url": "https://c.example"},
            ]
        }
        with mock.patch.object(search_mod, "_http_post_json", return_value=payload):
            results = search_mod.search("q", provider="tavily", api_key="k", max_results=5)
        # empty-url row dropped; trailing row survives but is not a dict-shaped risk
        self.assertEqual([r.url for r in results], ["https://a.example", "https://b.example", "https://c.example"])
        self.assertEqual(results[0].score, 0.9)
        self.assertEqual(results[1].published, "2026-01-02")
        self.assertEqual({r.provider for r in results}, {"tavily"})

    def test_max_results_applied(self) -> None:
        payload = {"results": [
            {"title": str(i), "url": f"https://e{i}.example", "content": "c"} for i in range(10)
        ]}
        with mock.patch.object(search_mod, "_http_post_json", return_value=payload):
            results = search_mod.search("q", provider="tavily", api_key="k", max_results=2)
        self.assertEqual(len(results), 2)

    def test_no_results_raises(self) -> None:
        with mock.patch.object(search_mod, "_http_post_json", return_value={"results": []}):
            with self.assertRaises(SearchError):
                search_mod.search("q", provider="tavily", api_key="k")

    def test_bearer_header_sent(self) -> None:
        captured: dict[str, object] = {}

        def fake_post(url, payload, headers, timeout):
            captured.update(url=url, payload=payload, headers=headers)
            return {"results": [{"title": "t", "url": "https://e.example", "content": "c"}]}

        with mock.patch.object(search_mod, "_http_post_json", side_effect=fake_post):
            search_mod.search("q", provider="tavily", api_key="tvly-secret", max_results=3)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer tvly-secret")
        self.assertEqual(captured["payload"]["query"], "q")
        self.assertEqual(captured["payload"]["max_results"], 3)
        self.assertEqual(captured["url"], search_mod.TAVILY_ENDPOINT)

    def test_non_dict_rows_tolerated(self) -> None:
        with mock.patch.object(search_mod, "_http_post_json", return_value={"results": [None, 42]}):
            with self.assertRaises(SearchError):
                search_mod.search("q", provider="tavily", api_key="k")


class FormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            SearchResult(title="One", url="https://one.example", snippet="s1", provider="urllib"),
            SearchResult(title="Two", url="https://two.example", snippet="s2", provider="tavily",
                         published="2026-01-01"),
        ]

    def test_brief_numbering(self) -> None:
        brief = search_mod.format_brief(self.results, "my query")
        self.assertIn("[1] One", brief)
        self.assertIn("[2] Two", brief)
        self.assertIn("https://one.example", brief)
        self.assertIn("published: 2026-01-01", brief)
        self.assertIn("Query: my query", brief)

    def test_empty_brief(self) -> None:
        self.assertEqual(search_mod.format_brief([], "q"), "(no results)")

    def test_grounding_block_contains_rules_and_sources(self) -> None:
        block = search_mod.build_grounding_block(self.results, "my query", "urllib")
        self.assertIn("LIVE WEB CONTEXT", block)
        self.assertIn("Cite inline", block)
        self.assertIn("Do not invent URLs", block)
        self.assertIn("https://two.example", block)
        self.assertIn("urllib", block)

    def test_grounding_block_empty_results(self) -> None:
        self.assertEqual(search_mod.build_grounding_block([], "q", "urllib"), "")


class SettingsSecretTests(unittest.TestCase):
    def test_search_key_resolution_precedence(self) -> None:
        s = runtime.resolve_settings(
            api_key="k",
            environ={"TAVILY_API_KEY": "tvly-a", "SEARCH_API_KEY": "search-b"},
        )
        self.assertEqual(s.search_api_key, "tvly-a")

    def test_search_key_search_env_fallback(self) -> None:
        s = runtime.resolve_settings(api_key="k", environ={"SEARCH_API_KEY": "search-b"})
        self.assertEqual(s.search_api_key, "search-b")

    def test_search_key_optional(self) -> None:
        s = runtime.resolve_settings(api_key="k", environ={})
        self.assertIsNone(s.search_api_key)

    def test_search_key_hidden_from_repr(self) -> None:
        s = runtime.resolve_settings(api_key="k", environ={"TAVILY_API_KEY": "tvly-super-secret"})
        self.assertNotIn("tvly-super-secret", repr(s))
        self.assertNotIn("tvly-super-secret", str(s))

    def test_both_secrets_hidden_from_repr(self) -> None:
        s = runtime.resolve_settings(
            api_key="sk-llm-secret", environ={"TAVILY_API_KEY": "tvly-search-secret"}
        )
        text = repr(s)
        self.assertNotIn("sk-llm-secret", text)
        self.assertNotIn("tvly-search-secret", text)

    def test_require_api_key_false_allows_missing_llm_key(self) -> None:
        s = runtime.resolve_settings(environ={}, require_api_key=False)
        self.assertEqual(s.api_key, "")

    def test_chat_messages_rejects_empty_api_key(self) -> None:
        s = runtime.resolve_settings(environ={}, require_api_key=False)
        with self.assertRaises(runtime.ConfigError):
            runtime.chat_messages([{"role": "user", "content": "q"}], s)


class CliToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = Path.cwd()
        os.chdir(REPO_ROOT)

    def tearDown(self) -> None:
        os.chdir(self._cwd)

    SECRET_ENV_VARS = ("ALFRED_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY", "SEARCH_API_KEY")

    def _run(self, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
        """Run the CLI with secrets scrubbed, then apply any overrides."""
        out, err = io.StringIO(), io.StringIO()
        clean = {k: v for k, v in os.environ.items() if k not in self.SECRET_ENV_VARS}
        if env:
            clean.update(env)
        with mock.patch.dict(os.environ, clean, clear=True):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_search_json_with_mocked_http(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value=DDG_FIXTURE):
            code, out, _ = self._run(["search", "q", "--json", "--max-results", "5"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["provider"], "urllib")
        self.assertEqual(len(data["results"]), 2)
        self.assertTrue(all(r["url"].startswith("http") for r in data["results"]))

    def test_search_text_output(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value=DDG_FIXTURE):
            code, out, _ = self._run(["search", "q"])
        self.assertEqual(code, 0)
        self.assertIn("[1]", out)

    def test_search_failure_exits_two(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value="<html></html>"):
            code, _, err = self._run(["search", "q"])
        self.assertEqual(code, 2)
        self.assertIn("search error", err)

    def test_research_retrieval_only_needs_no_llm_key(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value=DDG_FIXTURE):
            code, out, _ = self._run(["research", "q"])
        self.assertEqual(code, 0)
        self.assertIn("[1]", out)
        self.assertIn("provider: urllib", out)

    def test_research_ask_requires_llm_key(self) -> None:
        with mock.patch.object(search_mod, "_http_get", return_value=DDG_FIXTURE):
            code, _, err = self._run(["research", "q", "--ask"])
        self.assertEqual(code, 1)
        self.assertIn("ALFRED_API_KEY", err)

    def test_research_ask_builds_grounded_messages(self) -> None:
        captured: dict[str, object] = {}

        def fake_chat_messages(messages, settings):
            captured["messages"] = messages
            return "BLUF: answer [1]."

        with mock.patch.object(search_mod, "_http_get", return_value=DDG_FIXTURE), \
             mock.patch.object(runtime, "chat_messages", side_effect=fake_chat_messages), \
             mock.patch.dict(os.environ, {"ALFRED_API_KEY": "sk-x"}, clear=False):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["research", "my query", "--ask"])

        self.assertEqual(code, 0)
        self.assertIn("BLUF: answer [1].", out.getvalue())
        system_msg = captured["messages"][0]["content"]
        self.assertIn("LIVE WEB CONTEXT", system_msg)
        self.assertIn("https://www.python.org/downloads/release/python-3120/", system_msg)
        self.assertIn("CORE IDENTITY", system_msg)  # base prompt still present
        self.assertEqual(captured["messages"][1]["role"], "user")

    def test_env_reports_search_contract_without_secrets(self) -> None:
        code, out, _ = self._run(["env"], env={"TAVILY_API_KEY": "tvly-hunter2"})
        self.assertEqual(code, 0)
        self.assertNotIn("tvly-hunter2", out)
        data = json.loads(out)
        self.assertTrue(data["search_api_key"]["set"])
        self.assertEqual(data["search_provider"]["resolved"], "tavily")

    def test_env_reports_provider_when_no_key(self) -> None:
        code, out, _ = self._run(["env"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertFalse(data["search_api_key"]["set"])
        self.assertEqual(data["search_provider"]["resolved"], "urllib")


if __name__ == "__main__":
    unittest.main()
