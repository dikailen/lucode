import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse

import requests
from mcp.server.fastmcp import FastMCP


DEFAULT_TIMEOUT = int(os.environ.get("WEB_SEARCH_TIMEOUT_SECONDS", "15"))
DEFAULT_MAX_RESULTS = int(os.environ.get("WEB_SEARCH_MAX_RESULTS", "5"))
OFFICIAL_DOC_HOSTS = {
    "platform.openai.com",
    "developers.openai.com",
    "openai.github.io",
    "docs.anthropic.com",
    "docs.github.com",
    "docs.python.org",
    "modelcontextprotocol.io",
}
PACKAGE_REGISTRY_HOSTS = {"pypi.org", "npmjs.com"}
COMMUNITY_HOSTS = {
    "medium.com",
    "dev.to",
    "stackoverflow.com",
    "reddit.com",
    "www.reddit.com",
    "juejin.cn",
    "csdn.net",
    "blog.csdn.net",
    "zhihu.com",
}

mcp = FastMCP("web_search", log_level="ERROR")


@dataclass
class SearchProviderResponse:
    provider_id: str
    provider_label: str
    results: list[dict] = field(default_factory=list)
    error: str = ""


class SearchProvider:
    provider_id = "base"
    provider_label = "Base Search Provider"

    def search(self, query: str, max_results: int, timeout: int) -> SearchProviderResponse:
        raise NotImplementedError


class DuckDuckGoHTMLProvider(SearchProvider):
    provider_id = "duckduckgo_html"
    provider_label = "DuckDuckGo HTML fallback"

    def search(self, query: str, max_results: int, timeout: int) -> SearchProviderResponse:
        response = requests.get(
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            headers=_browser_headers(),
            timeout=timeout,
        )
        response.raise_for_status()

        parser = DuckDuckGoHTMLParser()
        parser.feed(response.text)
        return SearchProviderResponse(
            provider_id=self.provider_id,
            provider_label=self.provider_label,
            results=parser.results[: max(max_results, 1) * 3],
        )


class BraveSearchProvider(SearchProvider):
    provider_id = "brave"
    provider_label = "Brave Search API"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, max_results: int, timeout: int) -> SearchProviderResponse:
        response = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
            }
            for item in payload.get("web", {}).get("results", [])
        ]
        return SearchProviderResponse(self.provider_id, self.provider_label, results=results)


class BingSearchProvider(SearchProvider):
    provider_id = "bing"
    provider_label = "Bing Web Search API"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, max_results: int, timeout: int) -> SearchProviderResponse:
        response = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": max_results},
            headers={"Ocp-Apim-Subscription-Key": self.api_key},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results = [
            {
                "title": item.get("name", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in payload.get("webPages", {}).get("value", [])
        ]
        return SearchProviderResponse(self.provider_id, self.provider_label, results=results)


class SerpApiSearchProvider(SearchProvider):
    provider_id = "serpapi"
    provider_label = "SerpAPI Google Search"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, max_results: int, timeout: int) -> SearchProviderResponse:
        response = requests.get(
            "https://serpapi.com/search.json",
            params={"q": query, "engine": "google", "api_key": self.api_key, "num": max_results},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in payload.get("organic_results", [])
        ]
        return SearchProviderResponse(self.provider_id, self.provider_label, results=results)


def _get_search_provider() -> SearchProvider:
    provider_id = str(os.environ.get("WEB_SEARCH_PROVIDER") or "duckduckgo_html").strip().lower()
    if provider_id in {"", "duckduckgo", "duckduckgo_html", "ddg"}:
        return DuckDuckGoHTMLProvider()

    if provider_id == "brave":
        api_key = _provider_api_key("BRAVE_SEARCH_API_KEY")
        if not api_key:
            raise ValueError("WEB_SEARCH_PROVIDER=brave 需要配置 API Key：WEB_SEARCH_API_KEY 或 BRAVE_SEARCH_API_KEY。")
        return BraveSearchProvider(api_key)

    if provider_id == "bing":
        api_key = _provider_api_key("BING_SEARCH_API_KEY")
        if not api_key:
            raise ValueError("WEB_SEARCH_PROVIDER=bing 需要配置 API Key：WEB_SEARCH_API_KEY 或 BING_SEARCH_API_KEY。")
        return BingSearchProvider(api_key)

    if provider_id == "serpapi":
        api_key = _provider_api_key("SERPAPI_API_KEY")
        if not api_key:
            raise ValueError("WEB_SEARCH_PROVIDER=serpapi 需要配置 API Key：WEB_SEARCH_API_KEY 或 SERPAPI_API_KEY。")
        return SerpApiSearchProvider(api_key)

    raise ValueError(f"未知搜索 Provider：{provider_id}。可用值：duckduckgo_html、brave、bing、serpapi。")


def _provider_api_key(specific_env: str) -> str:
    return str(os.environ.get("WEB_SEARCH_API_KEY") or os.environ.get(specific_env) or "").strip()


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        )
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_result_link = False
        self._current_href = ""
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in classes:
            self._in_result_link = True
            self._current_href = attrs_dict.get("href", "")
            self._current_text = []

    def handle_data(self, data):
        if self._in_result_link:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._in_result_link:
            title = " ".join("".join(self._current_text).split())
            href = _normalize_duckduckgo_url(self._current_href)
            if title and href:
                self.results.append({"title": title, "url": href})
            self._in_result_link = False
            self._current_href = ""
            self._current_text = []


class TextExtractingHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if text:
            self.text_parts.append(text)

    def get_text(self, max_chars: int) -> str:
        text = " ".join(self.text_parts)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]


def _normalize_duckduckgo_url(url: str) -> str:
    if not url:
        return ""

    url = unescape(url)
    if url.startswith("//"):
        url = "https:" + url

    if "uddg=" in url:
        match = re.search(r"[?&]uddg=([^&]+)", url)
        if match:
            from urllib.parse import unquote

            return unquote(match.group(1))

    return url


def _domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    if not allowed_domains:
        return True

    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def _result_score(query: str, url: str, title: str, domains: list[str]) -> int:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    text = f"{query} {title} {url}".lower()
    score = 0

    tier = _source_tier(query, url, title)
    score += _tier_score(tier)

    if domains and _domain_allowed(url, domains):
        score += 100

    if any(word in text for word in ["official", "docs", "documentation", "官方", "文档"]):
        official_hosts = {
            "platform.openai.com",
            "developers.openai.com",
            "openai.github.io",
        }
        if host in official_hosts or any(host.endswith("." + item) for item in official_hosts):
            score += 60
        if host == "github.com" and path.startswith("/openai/"):
            score += 35
        if host in {"pypi.org", "npmjs.com"}:
            score -= 20

    if "docs" in path or "documentation" in title.lower():
        score += 10
    if "mcp" in text:
        score += 10
    if "model context protocol" in title.lower():
        score += 25
    if "openai-agents-python" in url or "agents sdk" in title.lower():
        score += 20

    return score


def _source_tier(query: str, url: str, title: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    text = f"{query} {title} {url}".lower()

    if host in OFFICIAL_DOC_HOSTS:
        return "official_docs"
    if host.startswith(("docs.", "developer.", "developers.", "platform.")):
        return "official_docs"
    if "official" in text and ("docs" in path or "documentation" in text):
        return "official_docs"

    if host == "github.com":
        parts = [part for part in path.split("/") if part]
        if parts:
            owner = parts[0]
            if owner in text or owner in {"openai", "anthropics", "vercel", "modelcontextprotocol"}:
                return "official_github"
        return "github"

    if host in PACKAGE_REGISTRY_HOSTS:
        return "package_registry"
    if host in COMMUNITY_HOSTS or any(host.endswith("." + item) for item in COMMUNITY_HOSTS):
        return "community"
    if "docs" in path or "documentation" in text:
        return "documentation"
    return "general"


def _tier_score(tier: str) -> int:
    scores = {
        "official_docs": 90,
        "official_github": 70,
        "documentation": 45,
        "package_registry": 30,
        "github": 20,
        "general": 0,
        "community": -20,
    }
    return scores.get(tier, 0)


@mcp.tool(
    name="web_search",
    description=(
        "Search the web for current external information. "
        "Returns title and URL results. Prefer official sources when the query asks for docs, APIs, or latest behavior. "
        "For URL-only tasks, call this once and do not call web_fetch."
    ),
)
def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS, domains: list[str] | None = None) -> str:
    if not query.strip():
        raise ValueError("query must not be empty")

    max_results = max(1, min(int(max_results or DEFAULT_MAX_RESULTS), 10))
    domains = domains or []
    domain_query = " ".join(f"site:{domain}" for domain in domains)
    full_query = f"{query} {domain_query}".strip()
    retrieved_at = _now_iso()

    try:
        provider = _get_search_provider()
        provider_response = provider.search(full_query, max_results=max_results, timeout=DEFAULT_TIMEOUT)
    except (ValueError, requests.RequestException) as exc:
        provider_id = str(os.environ.get("WEB_SEARCH_PROVIDER") or "duckduckgo_html").strip().lower()
        return json.dumps(
            {
                "query": query,
                "domains": domains,
                "source_provider": provider_id or "duckduckgo_html",
                "source_provider_label": provider_id or "DuckDuckGo HTML fallback",
                "retrieved_at": retrieved_at,
                "results": [],
                "error": f"web_search request failed: {exc}",
            },
            ensure_ascii=False,
            indent=2,
        )

    seen = set()
    candidates = []
    for item in provider_response.results:
        url = item.get("url", "")
        if url in seen or not _domain_allowed(url, domains):
            continue
        seen.add(url)
        title = item.get("title", "")
        tier = _source_tier(query, url, title)
        candidates.append(
            {
                **item,
                "url": url,
                "title": title,
                "source_tier": tier,
                "source_score": _tier_score(tier),
                "source_provider": provider_response.provider_id,
                "retrieved_at": retrieved_at,
            }
        )

    candidates.sort(
        key=lambda item: _result_score(query, item["url"], item["title"], domains),
        reverse=True,
    )
    results = candidates[:max_results]

    return json.dumps(
        {
            "query": query,
            "domains": domains,
            "source_provider": provider_response.provider_id,
            "source_provider_label": provider_response.provider_label,
            "retrieved_at": retrieved_at,
            "results": results,
            "source_priority": "official_docs > official_github > documentation > package_registry > github > general > community",
            "note": "Search results should be verified by opening primary sources when precision matters.",
            "error": provider_response.error,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="web_fetch",
    description=(
        "Fetch a web page and return cleaned text for verification. "
        "Use after web_search when the answer needs source details."
    ),
)
def web_fetch(url: str, max_chars: int = 6000) -> str:
    if not url.strip():
        raise ValueError("url must not be empty")

    max_chars = max(500, min(int(max_chars or 6000), 20000))
    try:
        response = requests.get(
            url,
            headers=_browser_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return json.dumps(
            {
                "url": url,
                "text": "",
                "error": f"web_fetch request failed: {exc}",
            },
            ensure_ascii=False,
            indent=2,
        )

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        return json.dumps(
            {
                "url": url,
                "content_type": content_type,
                "text": response.text[:max_chars],
                "note": "Non-HTML content returned raw truncated text.",
            },
            ensure_ascii=False,
            indent=2,
        )

    parser = TextExtractingHTMLParser()
    parser.feed(response.text)
    return json.dumps(
        {
            "url": url,
            "content_type": content_type,
            "text": parser.get_text(max_chars),
            "note": "Text is extracted and truncated; verify details against the source URL.",
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run("stdio")
