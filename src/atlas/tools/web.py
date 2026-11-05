"""web_search and web_fetch.

**web_search** runs on the backend the vault allows (decided on 09/10/2026): Tavily when
`TAVILY_API_KEY` is in the vault, then Brave when `BRAVE_SEARCH_API_KEY` is, and otherwise the
keyless metasearch `ddgs` (`atlas.tools.keyless`, the `ddgs` extra), the last rung. Without a key
and without the extra, web_search says what to add.
Atlas V3 used `ddgs` too and learned that a dead search engine answers "nothing found" with no
error (its old package returned empty lists for days while the server reported healthy), so an
empty answer here says that it may be the engine, and how to add a keyed one.

**web_fetch** fetches the page here and turns it into Markdown with no model in between, so
nothing is summarized away; past 15,000 characters the model gets the first three quarters and the
last one, and the whole text goes to the spill folder with the read_file call for the middle. A
PDF or Office file at a link is read as read_file reads it. Only http and https, a redirect
to another host is returned to the model instead of followed, and an answer is reused for 15
minutes. A page's `<main>` (or
its one `<article>`) is kept when it has one, and scripts, styles, menus and footers are dropped,
since they are most of a page's tokens and none of its content.
"""

import asyncio
import importlib
import importlib.util
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urljoin, urlsplit

import httpx2
from bs4 import BeautifulSoup
from markdownify import markdownify

from atlas.agent import SpillFolder, Tool
from atlas.browser import METADATA_REFUSED, metadata_address, metadata_host
from atlas.tools.documents import DocumentError, bytes_format, bytes_markdown
from atlas.trust import untrusted

DEFAULT_RESULTS = 5
MAX_RESULTS = 10
#: Seconds a search or a fetch may take.
WEB_SECONDS = 30.0
#: The most of a page web_fetch downloads.
PAGE_BYTES = 10_000_000
#: How long a fetched page is reused: 15 minutes.
CACHE_SECONDS = 15 * 60
MAX_REDIRECTS = 5
#: The per-page budget for what the model reads, and its bounds.
PAGE_CHARS = 15_000
MIN_PAGE_CHARS = 2_000
MAX_PAGE_CHARS = 500_000
USER_AGENT = "Mozilla/5.0 (compatible; Atlas personal agent)"
TAVILY_KEY = "TAVILY_API_KEY"
BRAVE_KEY = "BRAVE_SEARCH_API_KEY"
_DROPPED = ("script", "style", "noscript", "template", "svg", "canvas", "iframe", "nav", "footer",
            "aside", "form")  # fmt: skip
_TEXT_TYPES = ("text/plain", "text/markdown", "application/json", "text/csv", "application/xml",
               "text/xml")  # fmt: skip


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchError(Exception):
    """A search engine failed; the message says what to do."""


class SearchBackend(Protocol):
    name: str
    #: The keyless engine, which fails without saying so at times.
    keyless: bool

    async def search(self, query: str, limit: int) -> list[SearchResult]: ...


# -- the backends --------------------------------------------------------------------------------


class TavilySearch:
    name = "Tavily"
    keyless = False

    def __init__(self, key: str, base_url: str = "https://api.tavily.com") -> None:
        self._key, self._base = key, base_url.rstrip("/")

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        body = await _json(
            "POST",
            f"{self._base}/search",
            self.name,
            headers={"Authorization": f"Bearer {self._key}"},
            json={"query": query, "max_results": limit, "search_depth": "basic"},
        )
        return [
            SearchResult(str(r.get("title", "")), str(r.get("url", "")), str(r.get("content", "")))
            for r in _records(body.get("results"))
        ]


class BraveSearch:
    name = "Brave"
    keyless = False

    def __init__(self, key: str, base_url: str = "https://api.search.brave.com") -> None:
        self._key, self._base = key, base_url.rstrip("/")

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        body = await _json(
            "GET",
            f"{self._base}/res/v1/web/search",
            self.name,
            headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
            params={"q": query, "count": limit},
        )
        web = body.get("web")
        found = cast("dict[str, Any]", web).get("results") if isinstance(web, dict) else None
        return [
            SearchResult(
                str(r.get("title", "")), str(r.get("url", "")), str(r.get("description", ""))
            )
            for r in _records(found)
        ]


class NoSearch:
    """Neither a key in the vault nor the keyless engine installed."""

    name = "web search"
    keyless = False

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        raise SearchError(
            f"web_search has no engine here. Ask for a key with the secrets tool "
            f"(action=request, name={TAVILY_KEY} or {BRAVE_KEY}; the user pastes it into the "
            "app's card), or install the keyless one: `uv sync --extra ddgs`, or "
            "`pip install 'atlas-agent[ddgs]'`. Do not ask the user to paste a key into the chat."
        )


def search_backend(secret: Callable[[str], str | None]) -> SearchBackend:
    """The backend the vault allows: Tavily, then Brave, then the keyless search."""
    if key := secret(TAVILY_KEY):
        return TavilySearch(key)
    if key := secret(BRAVE_KEY):
        return BraveSearch(key)
    if importlib.util.find_spec("ddgs") is None:
        return NoSearch()
    keyless = importlib.import_module("atlas.tools.keyless")
    return cast("SearchBackend", keyless.KeylessSearch())


async def _json(method: str, url: str, name: str, **request: Any) -> dict[str, Any]:
    try:
        async with httpx2.AsyncClient(timeout=WEB_SECONDS) as client:
            response = await client.request(method, url, **request)
    except httpx2.HTTPError as error:
        raise SearchError(f"{name} could not be reached: {error}") from error
    if response.status_code in (401, 403):
        raise SearchError(f"{name} refused the key in the vault (HTTP {response.status_code}).")
    if response.status_code == 429:
        raise SearchError(f"{name} is limiting requests (HTTP 429); try again later.")
    if response.status_code >= 400:
        raise SearchError(f"{name} answered HTTP {response.status_code}: {response.text[:300]}")
    try:
        body: object = response.json()
    except ValueError as error:
        raise SearchError(f"{name} answered something that is not JSON.") from error
    return cast("dict[str, Any]", body) if isinstance(body, dict) else {}


def _records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        cast("dict[str, Any]", item) for item in cast("list[Any]", value) if isinstance(item, dict)
    ]


# -- web_search ----------------------------------------------------------------------------------


def web_search(backend: SearchBackend) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return "[Atlas: web_search needs a query.]"
        limit = _count(arguments.get("limit"))
        try:
            results = await backend.search(query.strip(), limit)
        except SearchError as error:
            return f"[Atlas: {error}{_keyed_hint(backend)}]"
        if not results:
            return (
                f'{backend.name} found nothing for "{query.strip()}".{_keyed_hint(backend)} '
                "Try other words."
            )
        lines = [
            f"{n}. {r.title}\n   {r.url}\n   {' '.join(r.snippet.split())}"
            for n, r in enumerate(results[:limit], start=1)
        ]
        return untrusted("web_search", "\n".join(lines))

    return Tool(
        name="web_search",
        description=(
            "Searches the web and returns the title, address and an excerpt of each result. Use it "
            "for what changes over time or is not in your memory. Read a result in full with "
            "web_fetch."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, as typed in a search box."},
                "limit": {"type": "integer", "description": f"Results, up to {MAX_RESULTS}."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        run=run,
        read_only=True,
    )


def _keyed_hint(backend: SearchBackend) -> str:
    if not backend.keyless:
        return ""
    return (
        " The keyless search fails without saying so at times; a Tavily or Brave key in the vault "
        f"({TAVILY_KEY} or {BRAVE_KEY}) gives a steadier one."
    )


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return DEFAULT_RESULTS
    return max(1, min(MAX_RESULTS, int(value)))


# -- web_fetch -----------------------------------------------------------------------------------


class WebFetch:
    """The web_fetch tool, with its 15-minute cache. `spill` keeps a long page whole."""

    def __init__(self, spill: SpillFolder | None = None, cache_seconds: float = CACHE_SECONDS):
        self._spill = spill
        self._cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, str]] = {}
        self._count = 0

    def tool(self) -> Tool:
        return Tool(
            name="web_fetch",
            description=(
                "Reads a web page, or a PDF or Office file at a link, as Markdown, the page's main "
                f"content only. Past `char_limit` characters (default {PAGE_CHARS:,}) you get its "
                "start and end, and the whole text is kept in a file for read_file. A redirect to "
                "another site is returned to you, not followed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "An http(s) address."},
                    "char_limit": {
                        "type": "integer",
                        "description": f"Characters to return, {MIN_PAGE_CHARS:,} to "
                        f"{MAX_PAGE_CHARS:,}.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            run=self._run,
            read_only=True,
            spills=False,
            failure_tolerant=True,
        )

    async def _run(self, arguments: dict[str, Any]) -> str:
        url = arguments.get("url")
        if not isinstance(url, str) or urlsplit(url.strip()).scheme not in ("http", "https"):
            return "[Atlas: web_fetch reads http and https addresses only.]"
        url = url.strip()
        # A cloud computer's credentials sit at its metadata address (step 20): never read there,
        # by its name, its address, or a name that resolves to it.
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
        if metadata_host(host) or any(metadata_address(a) for a in await _addresses(host)):
            return f"[Atlas: {METADATA_REFUSED}.]"
        limit = _char_limit(arguments.get("char_limit"))
        cached = self._cache.get(url)
        if cached is not None and time.monotonic() - cached[0] < self._cache_seconds:
            page = cached[1]
        else:
            page = await _fetch(url)
            if page.startswith("[Atlas:"):
                return page
            self._cache[url] = (time.monotonic(), page)
        return untrusted("web_fetch", self._window(page, url, limit))

    def _window(self, page: str, url: str, limit: int) -> str:
        """The window: a page past `limit` shows its first three quarters and its last one,
        cut at lines, and a footer says where the whole text is and how to read the middle."""
        if len(page) <= limit:
            return page
        head_budget = int(limit * 0.75)
        tail_budget = limit - head_budget
        head, tail = page[:head_budget], page[-tail_budget:]
        if (cut := head.rfind("\n")) > head_budget * 0.5:
            head = head[:cut]
        if 0 <= (cut := tail.find("\n")) < tail_budget * 0.5:
            tail = tail[cut + 1 :]
        said = (
            f"[Atlas: {url} has {len(page):,} characters; showing {len(head):,} from the start "
            f"and {len(tail):,} from the end."
        )
        self._count += 1
        saved = None if self._spill is None else self._spill.save(f"web-{self._count}", page)
        if saved is not None:
            middle = head.count("\n") + 2
            said += f" The whole text is in {saved}; read the middle with read_file, "
            said += f"offset={middle}.]"
        else:
            said += " Fetch it again with a larger char_limit to read more.]"
        return f"{head}\n\n[... the middle is left out ...]\n\n{tail}\n{said}"


def _char_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return PAGE_CHARS
    return max(MIN_PAGE_CHARS, min(MAX_PAGE_CHARS, int(value)))


async def _addresses(host: str) -> list[str]:
    """The addresses `host` resolves to; none when it does not resolve, which the fetch says."""
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return []
    return [str(entry[4][0]) for entry in found]


async def _fetch(url: str) -> str:
    current = url
    try:
        async with httpx2.AsyncClient(
            timeout=WEB_SECONDS, headers={"User-Agent": USER_AGENT}
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        target = urljoin(current, response.headers.get("location", ""))
                        if urlsplit(target).netloc != urlsplit(current).netloc:
                            return (
                                f"[Atlas: {current} redirects to another site, {target}. Fetch "
                                "that address if you trust it.]"
                            )
                        current = target
                        continue
                    if response.status_code >= 400:
                        return f"[Atlas: {current} answered HTTP {response.status_code}.]"
                    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data += chunk
                        if len(data) > PAGE_BYTES:
                            return f"[Atlas: {current} is larger than {PAGE_BYTES:,} bytes.]"
                    return await _readable(current, kind, bytes(data), response.encoding)
    except httpx2.HTTPError as error:
        return f"[Atlas: {current} could not be read: {type(error).__name__}: {error}]"
    return f"[Atlas: {url} redirected more than {MAX_REDIRECTS} times.]"


async def _readable(url: str, kind: str, data: bytes, encoding: str | None) -> str:
    document = bytes_format(data)
    if document is not None:
        try:
            text = await asyncio.to_thread(bytes_markdown, data)
        except DocumentError as error:
            return f"[Atlas: {url} is a {document.upper()} file that could not be read: {error}.]"
        return f"URL: {url}\n\n{text.strip()}"
    text = data.decode(encoding or "utf-8", "replace")
    if kind in _TEXT_TYPES or (kind.startswith("text/") and kind != "text/html"):
        return f"URL: {url}\n\n{text.strip()}"
    if kind not in ("text/html", "application/xhtml+xml", ""):
        return f"[Atlas: {url} is {kind}, not a page web_fetch can read as text.]"
    soup = BeautifulSoup(text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for name in _DROPPED:
        for element in soup.find_all(name):
            element.decompose()
    articles = soup.find_all("article")
    main = soup.find("main") or (articles[0] if len(articles) == 1 else None) or soup.body or soup
    body = markdownify(str(main), heading_style="ATX")
    lines = [line.rstrip() for line in body.splitlines()]
    compact = "\n".join(lines)
    while "\n\n\n" in compact:
        compact = compact.replace("\n\n\n", "\n\n")
    head = f"# {title}\n" if title else ""
    return f"{head}URL: {url}\n\n{compact.strip()}"
