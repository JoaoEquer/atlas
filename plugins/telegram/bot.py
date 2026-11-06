"""Telegram's Bot API over HTTP.

A client library's own retries fight the caller's (a 409 that loops, a stop that hangs on a dead
socket). Here each call is one HTTP request with a deadline, and what to do about an error is
decided once, below:

- **401**: the token is wrong or was revoked; nothing will fix it but a new token.
- **409** on `getUpdates`: another program polls with the same token (or a webhook is set). Atlas
  waits 10, 20, 30, 40 and 50 seconds, then gives up saying so.
- **429**: Telegram's flood control says how long to wait (`retry_after`). A wait of up to 30
  seconds is waited; a longer one fails the send instead of freezing the chat (a wait can be
  97 minutes).
- A network error: the call is tried again after 1, 2, 4… up to 30 seconds, for as long as it
  takes; the user's computer sleeps and wakes.
"""

import asyncio
import json
from typing import Any, cast

import httpx2

#: How long Telegram holds a `getUpdates` open when nothing arrives.
POLL_SECONDS = 25
#: A request's own deadline, past the poll's.
REQUEST_SECONDS = POLL_SECONDS + 15
#: The longest flood wait waited inline.
MOST_FLOOD_WAIT = 30.0
#: The 409 rhythm: 10 + 10n seconds, five times.
CONFLICT_WAITS = (10.0, 20.0, 30.0, 40.0, 50.0)
MOST_NETWORK_WAIT = 30.0
#: The largest file a bot may download through the Bot API (`getFile`'s limit).
DOWNLOAD_BYTES = 20 * 1024 * 1024


class TelegramError(Exception):
    """Telegram refused a call."""

    def __init__(self, method: str, status: int, description: str, retry_after: float = 0) -> None:
        super().__init__(f"Telegram refused {method} (HTTP {status}): {description}")
        self.status = status
        self.description = description
        self.retry_after = retry_after


class Fatal(Exception):  # noqa: N818 - the channel ends: said as it is
    """The channel cannot go on by itself; the message says why and what to do."""


class Bot:
    """One bot's calls."""

    def __init__(self, api_url: str, token: str, *, sleep: float = 1.0) -> None:
        self._base = f"{api_url.rstrip('/')}/bot{token}"
        #: Where `getFile`'s paths are downloaded from (the Bot API's `file/bot<token>/<path>`).
        self._files = f"{api_url.rstrip('/')}/file/bot{token}"
        self._client = httpx2.AsyncClient(timeout=REQUEST_SECONDS)
        #: The unit of every wait, 1 second; tests make it small.
        self._sleep = sleep

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        """One call, its result; network errors are tried again, Telegram's refusals raise."""
        wait = 1.0
        while True:
            try:
                response = await self._client.post(
                    f"{self._base}/{method}",
                    content=json.dumps(params),
                    headers={"Content-Type": "application/json"},
                )
            except httpx2.HTTPError:
                await asyncio.sleep(wait * self._sleep)
                wait = min(wait * 2, MOST_NETWORK_WAIT)
                continue
            return _result(method, response)

    async def upload(
        self, method: str, part: str, name: str, data: bytes, media_type: str, **params: Any
    ) -> Any:
        """A call that sends a file, as a multipart form, its file in `part` (`voice`,
        `document`): one try, with `send`'s flood wait; Telegram's refusals raise."""
        for attempt in range(3):
            try:
                response = await self._client.post(
                    f"{self._base}/{method}",
                    data={key: str(value) for key, value in params.items()},
                    files={part: (name, data, media_type or "application/octet-stream")},
                )
            except httpx2.HTTPError as error:
                raise TelegramError(method, 0, str(error)) from error
            try:
                return _result(method, response)
            except TelegramError as error:
                if error.status == 401:
                    raise Fatal(_REFUSED) from error
                if error.status != 429 or error.retry_after > MOST_FLOOD_WAIT or attempt == 2:
                    raise
                await asyncio.sleep(error.retry_after * self._sleep)
        raise AssertionError  # pragma: no cover - the loop returns or raises

    async def download(self, file_id: str) -> bytes:
        """A file someone sent, by its id: `getFile` gives its path, then its bytes. Raises
        `TelegramError`; a bot downloads at most `DOWNLOAD_BYTES` (Telegram's "file is too big")."""
        found = cast("dict[str, Any]", await self.call("getFile", file_id=file_id))
        path = str(found.get("file_path") or "")
        if not path:
            raise TelegramError("getFile", 400, "Bad Request: the file has no path")
        try:
            response = await self._client.get(f"{self._files}/{path}")
        except httpx2.HTTPError as error:
            raise TelegramError("file", 0, str(error)) from error
        if response.status_code != 200:
            raise TelegramError("file", response.status_code, response.reason_phrase)
        return response.content

    async def send(self, method: str, **params: Any) -> Any:
        """A call that says something: a flood wait of up to `MOST_FLOOD_WAIT` is waited, three
        times at most."""
        for attempt in range(3):
            try:
                return await self.call(method, **params)
            except TelegramError as error:
                if error.status == 401:
                    raise Fatal(_REFUSED) from error
                if error.status != 429 or error.retry_after > MOST_FLOOD_WAIT or attempt == 2:
                    raise
                await asyncio.sleep(error.retry_after * self._sleep)
        raise AssertionError  # pragma: no cover - the loop returns or raises

    async def updates(self, offset: int | None) -> list[dict[str, Any]]:
        """The next updates, waiting up to `POLL_SECONDS` for them; a 409 is waited out
        (`CONFLICT_WAITS`) before it becomes `Fatal`."""
        for wait in (*CONFLICT_WAITS, None):
            try:
                found = await self.call(
                    "getUpdates",
                    offset=offset,
                    timeout=POLL_SECONDS,
                    allowed_updates=["message", "callback_query"],
                )
                return cast("list[dict[str, Any]]", found or [])
            except TelegramError as error:
                if error.status == 401:
                    raise Fatal(_REFUSED) from error
                if error.status == 429:
                    await asyncio.sleep(min(error.retry_after, MOST_FLOOD_WAIT) * self._sleep)
                    continue
                if error.status != 409:
                    raise
                if wait is None:
                    raise Fatal(
                        "Another program is reading this bot's messages (Telegram says 409 "
                        "Conflict): another Atlas, or another bot program, with the same token. "
                        "Stop it, or give this Atlas a bot of its own, and turn the plugin on "
                        "again."
                    ) from error
                await asyncio.sleep(wait * self._sleep)
        raise AssertionError  # pragma: no cover - the loop returns or raises


def _result(method: str, response: httpx2.Response) -> Any:
    """A call's result; Telegram's refusal raises, with its flood wait."""
    try:
        body = cast("dict[str, Any]", response.json())
    except ValueError:
        body = {}
    if response.status_code == 200 and body.get("ok"):
        return body.get("result")
    parameters = cast("dict[str, Any]", body.get("parameters") or {})
    raise TelegramError(
        method,
        response.status_code,
        str(body.get("description") or response.reason_phrase),
        float(parameters.get("retry_after") or 0),
    )


_REFUSED = (
    "Telegram refused the bot's token (HTTP 401): it is wrong or was revoked. Get it again "
    "from @BotFather, store it on the plugin's screen, and turn the plugin on again."
)
