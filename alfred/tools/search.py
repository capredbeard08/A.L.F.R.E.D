"""Web search tooling for A.L.F.R.E.D.

Provider resolution, in order:

1. ``tavily``      - used when an API key is configured. Preferred: purpose-built
                     for LLM grounding, returns scored, ranked results.
2. ``duckduckgo``  - used when the optional ``duckduckgo_search`` / ``ddgs``
                     package is installed. No key required.
3. ``urllib``      - standard-library fallback against DuckDuckGo's HTML
                     endpoint. Always available, no key, no install.

Only the first *available* provider is consulted; there is no silent cascade on
failure, because a partial result set presented as complete is worse than a
loud error. Callers wanting redundancy can pass ``provider=`` explicitly.
"""
from __future__ import annotations

import html as html_module
import importlib.util
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable

from ..runtime import AlfredError

TAVILY_ENDPOINT = "https://api.tavily.com/search"
DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

# Phrases DuckDuckGo uses when it has decided the caller is a bot. Matched
# only AFTER a page yields zero results, so a real result mentioning one of
# these words can never be misclassified as a block.
BOT_BLOCK_MARKERS: tuple[str, ...] = (
    "anomaly",
    "bot",
    "watermark",
    "captcha",
    "automated",
)

PROVIDER_TAVILY = "tavily"
PROVIDER_DDG = "duckduckgo"
PROVIDER_URLLIB = "urllib"
PROVIDERS = (PROVIDER_TAVILY, PROVIDER_DDG, PROVIDER_URLLIB)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 alfred-runtime/1.0"
)
DEFAULT_MAX_RESULTS = 5
DEFAULT_TIMEOUT_SECONDS = 20.0


class SearchError(AlfredError):
    """A search provider failed, returned nothing usable, or is unreachable."""


@dataclass(frozen=True)
class SearchResult:
    """One search hit. Carries enough to cite it and nothing sensitive."""

    title: str
    url: str
    snippet: str
    provider: str
    score: float | None = None
    published: str | None = None

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"result url must be absolute http(s): {self.url!r}")


def _has_module(name: str) -> bool:
    """True if an optional module is importable, without importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def available_provider(api_key: str | None = None) -> str:
    """Pick the best provider this environment can actually run."""
    if api_key:
        return PROVIDER_TAVILY
    if _has_module("ddgs") or _has_module("duckduckgo_search"):
        return PROVIDER_DDG
    return PROVIDER_URLLIB


def _http_post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    """POST JSON and decode the response, normalising transport failures."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise SearchError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SearchError(f"could not reach {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SearchError(f"timed out after {timeout}s contacting {url}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SearchError(f"{url} returned a non-JSON response") from exc


def _http_get(url: str, timeout: float) -> str:
    """GET a document as text, normalising transport failures."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise SearchError(f"HTTP {exc.code} fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise SearchError(f"could not reach {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SearchError(f"timed out after {timeout}s fetching {url}") from exc


def _search_tavily(query: str, api_key: str, max_results: int, timeout: float) -> list[SearchResult]:
    """Tavily Search API. Bearer auth, JSON body, ``results[]`` response."""
    data = _http_post_json(
        TAVILY_ENDPOINT,
        payload={
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
        },
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
        },
        timeout=timeout,
    )

    results: list[SearchResult] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or ""
        if not url:
            continue
        results.append(
            SearchResult(
                title=str(item.get("title") or url),
                url=str(url),
                snippet=str(item.get("content") or "").strip(),
                provider=PROVIDER_TAVILY,
                score=float(item["score"]) if isinstance(item.get("score"), (int, float)) else None,
                published=str(item["published_date"]) if item.get("published_date") else None,
            )
        )
    if not results:
        raise SearchError(f"Tavily returned no results for {query!r}")
    return results[:max_results]


def _search_ddgs(query: str, max_results: int, timeout: float) -> list[SearchResult]:
    """Optional ``ddgs`` / ``duckduckgo_search`` client. Imported lazily."""
    module = None
    for name in ("ddgs", "duckduckgo_search"):
        try:
            module = __import__(name)
            break
        except ImportError:
            continue
    if module is None:
        raise SearchError("no DuckDuckGo client installed")

    try:
        raw: Iterable[dict] = module.DDGS().text(query, max_results=max_results)
    except Exception as exc:  # the optional client raises library-specific types
        raise SearchError(f"DuckDuckGo client failed: {exc}") from exc

    results: list[SearchResult] = []
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("href"):
            continue
        results.append(
            SearchResult(
                title=str(item.get("title") or item["href"]),
                url=str(item["href"]),
                snippet=str(item.get("body") or "").strip(),
                provider=PROVIDER_DDG,
            )
        )
    if not results:
        raise SearchError(f"DuckDuckGo returned no results for {query!r}")
    return results


class _DDGHTMLParser(HTMLParser):
    """Extract ``result__a`` / ``result__snippet`` blocks from DDG's HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, list[str]] | None = None
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {k: (v or "") for k, v in attrs}
        classes = attributes.get("class", "")

        if tag == "a" and "result__a" in classes:
            self._current = {"title": [], "url": [attributes.get("href", "")], "snippet": []}
            self.results.append(self._current)  # type: ignore[arg-type]
            self._capture = "title"
        elif "result__snippet" in classes and self._current is not None:
            self._capture = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if self._capture in ("title", "snippet") and tag in ("a", "div", "span"):
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._capture and self._current is not None:
            self._current[self._capture].append(data)  # type: ignore[index]


def _decode_ddg_redirect(href: str) -> str:
    """DDG wraps destinations as ``//duckduckgo.com/l/?uddg=<urlencoded>``."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        query_string = urllib.parse.urlsplit(href).query
        decoded = urllib.parse.parse_qs(query_string).get("uddg", [""])[0]
        if decoded:
            return decoded
    if href.startswith(("http://", "https://")):
        return href
    return ""


def _search_urllib(query: str, max_results: int, timeout: float) -> list[SearchResult]:
    """Standard-library fallback: parse DuckDuckGo's HTML endpoint."""
    page = _http_get(
        f"{DDG_HTML_ENDPOINT}?q={urllib.parse.quote_plus(query)}",
        timeout=timeout,
    )

    if not page.strip():
        raise SearchError("DuckDuckGo returned an empty page")

    parser = _DDGHTMLParser()
    parser.feed(page)

    results: list[SearchResult] = []
    for item in parser.results:
        url = _decode_ddg_redirect((item.get("url") or [""])[0] if item.get("url") else "")
        title = html_module.unescape("".join(item.get("title") or [])).strip()
        snippet = html_module.unescape("".join(item.get("snippet") or [])).strip()
        if not url or not title:
            continue
        results.append(
            SearchResult(title=title, url=url, snippet=snippet, provider=PROVIDER_URLLIB)
        )
        if len(results) >= max_results:
            break

    if not results:
        # Classify only now: zero parseable results plus bot-block phrasing means
        # we were rate-limited, not that the query had no matches.
        lowered = page.lower()
        if any(marker in lowered for marker in BOT_BLOCK_MARKERS):
            raise SearchError(
                "DuckDuckGo served an anti-bot/anomaly page instead of results "
                "(rate limiting). Configure TAVILY_API_KEY for reliable access."
            )
        raise SearchError(
            f"no parseable results for {query!r} (endpoint may have changed). "
            "Configure TAVILY_API_KEY for a stable provider."
        )
    return results


def search(
    query: str,
    *,
    api_key: str | None = None,
    provider: str | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[SearchResult]:
    """Run a live web query and return ranked, citable results.

    Raises SearchError on any provider failure; never returns a silently
    truncated or empty list.
    """
    if not query or not query.strip():
        raise SearchError("query must be non-empty")
    if max_results < 1:
        raise SearchError("max_results must be >= 1")
    if timeout <= 0:
        raise SearchError("timeout must be positive")

    chosen = provider or available_provider(api_key)
    if chosen not in PROVIDERS:
        raise SearchError(f"unknown provider {chosen!r}; expected one of {PROVIDERS}")

    if chosen == PROVIDER_TAVILY:
        if not api_key:
            raise SearchError("provider 'tavily' requires an API key")
        return _search_tavily(query.strip(), api_key, max_results, timeout)
    if chosen == PROVIDER_DDG:
        return _search_ddgs(query.strip(), max_results, timeout)
    return _search_urllib(query.strip(), max_results, timeout)


def format_brief(results: list[SearchResult], query: str = "") -> str:
    """Render results as a numbered, citable briefing block."""
    if not results:
        return "(no results)"
    lines = []
    if query:
        lines.append(f"Query: {query}")
    lines.append(f"Retrieved {len(results)} result(s). Cite as [1], [2], ...")
    lines.append("")
    for index, item in enumerate(results, start=1):
        lines.append(f"[{index}] {item.title}")
        lines.append(f"     {item.url}")
        if item.snippet:
            lines.append(f"     {item.snippet}")
        if item.published:
            lines.append(f"     published: {item.published}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_grounding_block(results: list[SearchResult], query: str, provider: str) -> str:
    """Build the system-prompt addendum that makes answers cite-able."""
    if not results:
        return ""
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "## LIVE WEB CONTEXT\n\n"
        f"Source: {provider} | Retrieved: {retrieved_at} | Query: {query!r}\n\n"
        "The results below were retrieved live and are the only web evidence you have.\n"
        "Rules:\n"
        "- Cite inline using the bracketed index, e.g. [1], [2].\n"
        "- Do not invent URLs or citations not present below.\n"
        "- If a claim is not supported by these results, say so explicitly.\n"
        "- For time-sensitive figures (prices, rates, releases), state the retrieved timestamp.\n\n"
        + format_brief(results, query)
    )
