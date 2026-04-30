import json
import os
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse

import requests
from mcp.server.fastmcp import FastMCP


DEFAULT_TIMEOUT = int(os.environ.get("WEB_SEARCH_TIMEOUT_SECONDS", "15"))
DEFAULT_MAX_RESULTS = int(os.environ.get("WEB_SEARCH_MAX_RESULTS", "5"))

mcp = FastMCP("web_search", log_level="ERROR")


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

    response = requests.get(
        f"https://html.duckduckgo.com/html/?q={quote_plus(full_query)}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            )
        },
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

    parser = DuckDuckGoHTMLParser()
    parser.feed(response.text)

    seen = set()
    candidates = []
    for item in parser.results:
        url = item["url"]
        if url in seen or not _domain_allowed(url, domains):
            continue
        seen.add(url)
        candidates.append(item)

    candidates.sort(
        key=lambda item: _result_score(query, item["url"], item["title"], domains),
        reverse=True,
    )
    results = candidates[:max_results]

    return json.dumps(
        {
            "query": query,
            "domains": domains,
            "results": results,
            "note": "Search results should be verified by opening primary sources when precision matters.",
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
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            )
        },
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

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
