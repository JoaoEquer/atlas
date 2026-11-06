"""The model subject's adapters: the catalog's files, the official OpenAI SDK, the call ledger.

The shipped files live inside the package (`atlas/models/data/`) and are read through
`importlib.resources`, so they are found in a wheel, an editable install and the container alike.

**This is the one module that imports the SDK.** It sends the body a wire built and hands back each
streamed chunk or event as a plain dictionary, so the wires stay pure and the SDK's types stay here.
Retries are the SDK's: connection errors, 408, 409, 429 and 5xx, with backoff, before the reply
starts. The SDK's errors become Atlas's, which say what happened and what fixes it: a rate limit
passes, an empty account does not, a refused key needs the user.

**The call ledger** keeps one row per model call in the home's database, whatever made the call:
the turn, a compaction, a job. Atlas V3 recorded the turn in one table and the background calls in
another, and missed some: its own sum said US$ 0.35 for a period the invoice put near US$ 1.
Recording never breaks the call it records: a failed write costs a log line, not the reply.
"""

import importlib
import importlib.util
import logging
import sqlite3
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any, Protocol, cast

import openai
from openai.types.chat import ChatCompletionMessageParam
from openai.types.responses import ResponseInputParam

from atlas.home import Database, DatabaseError, Migration, Schema, transaction
from atlas.models.catalog import Catalog, parse_catalog
from atlas.models.errors import (
    CatalogError,
    ModelAuthError,
    ModelKeyError,
    ModelOutOfCreditError,
    ModelRateLimitError,
    ModelRequestError,
    ModelsError,
    ModelUnavailableError,
)
from atlas.models.model import (
    CallOutcome,
    CallRecord,
    Cost,
    CostSource,
    KeyCheck,
    KeyStatus,
    Provider,
    Usage,
    Wire,
)

_DATA = resources.files("atlas.models") / "data"
#: Seconds to wait for a connection to the provider.
CONNECT_TIMEOUT = 5.0
#: A placeholder for a server that takes no key: the SDK refuses to start without one.
NO_KEY = "no-key"
#: Error codes that mean the account is empty, whatever the HTTP status says.
#: Error codes or types that mean the account is empty, whatever the HTTP status says. OpenAI
#: answers HTTP 429 with type `insufficient_quota` and code `credit_balance_exhausted` (seen by
#: Atlas V3 in production); a plain 429 is a rate limit.
_OUT_OF_CREDIT_CODES = frozenset(
    {"insufficient_quota", "credit_balance_exhausted", "insufficient_credits", "billing_not_active"}
)

SCHEMA = Schema(
    "models",
    (
        Migration(
            1,
            "call ledger",
            """
CREATE TABLE models_calls (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    scope TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    served_model TEXT,
    outcome TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    input_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    cost_usd TEXT,
    cost_source TEXT NOT NULL
) STRICT;
CREATE INDEX models_calls_by_time ON models_calls (started_at);
""",
        ),
        Migration(
            2,
            "the hour-long cache write",
            # Anthropic bills a write to the one-hour cache at 2x the input and the five-minute
            # one at 1.25x (step 3b); the part of cache_write_tokens that went to the long one.
            "ALTER TABLE models_calls ADD COLUMN cache_write_long_tokens INTEGER;",
        ),
    ),
)

#: The token columns follow `Usage`'s fields, in order.
_COLUMNS = (
    "started_at, purpose, scope, provider, model, served_model, outcome, duration_ms, "
    "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, "
    "cache_write_long_tokens, cost_usd, cost_source"
)

_logger = logging.getLogger("atlas.models")


def load_catalog(models_file: Path) -> Catalog:
    """The shipped catalog with the user's `models.toml` over it, when the file exists."""
    try:
        user_text = models_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        user_text = None
    except (OSError, UnicodeDecodeError) as error:
        raise CatalogError(f"{models_file} cannot be read: {error}.") from error
    return parse_catalog(
        (_DATA / "providers.toml").read_text(encoding="utf-8"),
        (_DATA / "models.json").read_text(encoding="utf-8"),
        user_text,
        str(models_file),
    )


class _MessagesSDK(Protocol):
    """What `atlas.models.messages_store` gives, loaded only when its extra is installed."""

    def __init__(
        self, provider: Provider, key: str | None, *, retries: int, silence: float, connect: float
    ) -> None: ...

    def stream(
        self, body: Mapping[str, Any], betas: Sequence[str]
    ) -> AsyncGenerator[dict[str, Any]]: ...

    async def check_key(self) -> KeyCheck: ...

    async def close(self) -> None: ...


def load_sdks() -> None:
    """Import the Messages wire's SDK, when it is installed, for a thread to run as the server
    starts. Its first import takes a second or two, more on a busy machine; met by the first turn
    on that wire, it held the event loop, and every conversation of the server, until it ended."""
    if importlib.util.find_spec("anthropic") is not None:
        importlib.import_module("atlas.models.messages_store")


class Connection:
    """One provider's address and key, through its official SDK: OpenAI's for the chat and
    responses wires, Anthropic's for the messages wire (step 3b)."""

    def __init__(
        self, provider: Provider, key: str | None, *, retries: int, silence: float
    ) -> None:
        self.provider = provider
        self._silence = silence
        self._client = openai.AsyncOpenAI(
            api_key=key or NO_KEY,
            base_url=provider.base_url,
            max_retries=retries,
            # The wait for a connection stays short; the read timeout is the longest silence.
            timeout=openai.Timeout(silence, connect=CONNECT_TIMEOUT),
        )
        #: The Messages wire's own SDK, an extra loaded only for a provider that speaks it.
        self._messages: _MessagesSDK | None = None
        if provider.wire is Wire.MESSAGES:
            if importlib.util.find_spec("anthropic") is None:
                raise ModelKeyError(
                    f"{provider.name} needs Anthropic's SDK, an extra: run the installer again, "
                    "or `uv sync --extra anthropic` in a checkout."
                )
            module = importlib.import_module("atlas.models.messages_store")
            self._messages = cast("type[_MessagesSDK]", module.MessagesConnection)(
                provider, key, retries=retries, silence=silence, connect=CONNECT_TIMEOUT
            )

    async def messages(
        self, body: Mapping[str, Any], betas: Sequence[str] = ()
    ) -> AsyncGenerator[dict[str, Any]]:
        """Send a Messages API body built by `messages_request`; yield each event."""
        if self._messages is None:  # pragma: no cover - the service picks the wire by provider
            raise ModelRequestError(f"{self.provider.name} does not speak the Messages API.")
        async for event in self._messages.stream(body, betas):
            yield event

    async def chat(self, body: Mapping[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        """Send a Chat Completions body built by `chat_request`; yield each chunk."""
        rest = {key: value for key, value in body.items() if key not in ("model", "messages")}
        rest.pop("stream", None)
        try:
            stream = await self._client.chat.completions.create(
                model=body["model"],
                messages=cast("list[ChatCompletionMessageParam]", body["messages"]),
                stream=True,
                extra_body=rest,
            )
            async with stream:
                async for chunk in stream:
                    yield chunk.model_dump(mode="json", warnings=False)
        except openai.APIError as error:
            raise model_error(self.provider, error, self._silence) from error

    async def responses(self, body: Mapping[str, Any]) -> AsyncGenerator[dict[str, Any]]:
        """Send a Responses API body built by `responses_request`; yield each event."""
        rest = {key: value for key, value in body.items() if key not in ("model", "input")}
        rest.pop("stream", None)
        try:
            stream = await self._client.responses.create(
                model=body["model"],
                input=cast("ResponseInputParam", body["input"]),
                stream=True,
                extra_body=rest,
            )
            async with stream:
                async for event in stream:
                    yield event.model_dump(mode="json", warnings=False)
        except openai.APIError as error:
            raise model_error(self.provider, error, self._silence) from error

    async def check_key(self) -> KeyCheck:
        """Ask the provider, where it costs nothing, whether the key opens it. Never retried."""
        if self._messages is not None:
            return await self._messages.check_key()
        provider = self.provider
        name = provider.name
        try:
            await self._client.with_options(max_retries=0).get(provider.key_check, cast_to=object)
        except openai.APIStatusError as error:
            failure = _failure(error)
            status, detail = failure.status, failure.detail
            if status in (401, 403):
                found = KeyStatus.REFUSED, f"{name} refused the key (HTTP {status}): {detail}."
            elif failure.out_of_credit or failure.quota_used_up:
                found = KeyStatus.NO_CREDIT, f"The key is valid; its credit or quota: {detail}."
            elif failure.limited:
                found = KeyStatus.LIMITED, f"The key is valid; {name} is limiting requests for now."
            else:
                found = KeyStatus.UNEXPECTED, f"{name} answered HTTP {status}: {detail}."
            return KeyCheck(provider.id, *found)
        except openai.APIConnectionError as error:
            return KeyCheck(
                provider.id,
                KeyStatus.UNREACHABLE,
                f"{name} could not be reached at {provider.base_url} ({error.message}). "
                "This says nothing about the key.",
            )
        if provider.key is None:
            return KeyCheck(provider.id, KeyStatus.VALID, f"{name} answers at {provider.base_url}.")
        return KeyCheck(provider.id, KeyStatus.VALID, f"The key opens {name}.")

    async def close(self) -> None:
        await self._client.close()
        if self._messages is not None:
            await self._messages.close()


def model_error(provider: Provider, error: openai.APIError, silence: float) -> ModelsError:
    """The SDK's error as Atlas's, saying what happened and what fixes it."""
    name = provider.name
    # A timeout is a connection error to the SDK; only the wait for the connection itself is one.
    if (
        isinstance(error, openai.APITimeoutError)
        and "Connect" not in type(error.__cause__).__name__
    ):
        return ModelUnavailableError(
            f"{name} sent nothing for {silence:g} seconds, so the call was given up. Try again, "
            "or choose another model or provider."
        )
    if isinstance(error, openai.APIConnectionError):
        return ModelUnavailableError(
            f"{name} could not be reached at {provider.base_url} ({error.message}). Check the "
            "address and the network, or try again."
        )
    failure = _failure(error)
    status, detail = failure.status, failure.detail
    where = f"HTTP {status}" if status is not None else "during the reply"
    if status in (401, 403):
        fix = f"Store a valid one with `atlas secrets set {provider.key}`." if provider.key else ""
        return ModelAuthError(f"{name} refused the key ({where}): {detail}. {fix}".rstrip())
    if failure.quota_used_up:
        return ModelOutOfCreditError(
            f"{name} says the quota is used up ({where}): {detail}. It resets in about "
            f"{_duration(failure.wait or 0)}; until then, add credit or raise the plan at the "
            "provider, or choose another provider."
        )
    if failure.out_of_credit:
        return ModelOutOfCreditError(
            f"{name} says the account has no credit left ({where}): {detail}. Add credit at the "
            "provider, or choose another provider; waiting does not help."
        )
    if failure.limited:
        return ModelRateLimitError(
            f"{name} is limiting requests ({where}): {detail}. It passes; try again shortly."
        )
    if status is not None and 400 <= status < 500:
        return ModelRequestError(f"{name} refused the request ({where}): {detail}.")
    return ModelUnavailableError(f"{name} failed ({where}): {detail}. Try again.")


#: A limit the provider says lifts within this many seconds passes; a longer one is a used-up quota.
PASSING_WAIT = 60.0


@dataclass(frozen=True, slots=True)
class _Failure:
    """A provider's error answer, read the same way whatever its envelope."""

    status: int | None
    detail: str
    code: str | None
    #: OpenAI's error type, such as insufficient_quota.
    error_type: str | None
    #: Google's status name, such as RESOURCE_EXHAUSTED.
    kind: str | None
    #: Seconds the provider says to wait before trying again, when it says.
    wait: float | None

    @property
    def limited(self) -> bool:
        return (
            self.status == 429
            or self.code == "rate_limit_exceeded"
            or self.kind == "RESOURCE_EXHAUSTED"
        )

    @property
    def quota_used_up(self) -> bool:
        """A limit that does not lift in seconds, such as Gemini's daily free-tier quota.

        Gemini answers HTTP 429 RESOURCE_EXHAUSTED with the same message for its per-minute limit
        and its daily quota; only the wait it states tells them apart (seen on 08/10/2026: "retry
        in 22h27m").
        """
        return self.limited and self.wait is not None and self.wait > PASSING_WAIT

    @property
    def out_of_credit(self) -> bool:
        return (
            self.status == 402
            or self.code in _OUT_OF_CREDIT_CODES
            or self.error_type in _OUT_OF_CREDIT_CODES
        )


def _failure(error: openai.APIError) -> _Failure:
    fields = _error_fields(error.body)
    message = fields.get("message")
    code, kind, error_type = fields.get("code"), fields.get("status"), fields.get("type")
    status = error.status_code if isinstance(error, openai.APIStatusError) else None
    wait = _wait_in_details(fields.get("details"))
    if wait is None and isinstance(error, openai.APIStatusError):
        wait = _wait_in_headers(error.response.headers)
    text = message if isinstance(message, str) and message.strip() else error.message
    return _Failure(
        status=status,
        # One line, without its final period: it goes inside Atlas's own sentence.
        detail=" ".join(text.split()).rstrip("."),
        code=code if isinstance(code, str) else None,
        error_type=error_type if isinstance(error_type, str) else None,
        kind=kind if isinstance(kind, str) else None,
        wait=wait,
    )


def _error_fields(body: object) -> Mapping[str, Any]:
    """The error object inside any envelope: `{"error": {...}}`, Gemini's `[{"error": {...}}]`,
    or the object itself."""
    if isinstance(body, list) and body:
        body = cast("list[Any]", body)[0]
    if not isinstance(body, Mapping):
        return {}
    body = cast("Mapping[str, Any]", body)
    nested = body.get("error")
    return cast("Mapping[str, Any]", nested) if isinstance(nested, Mapping) else body


def _wait_in_details(details: object) -> float | None:
    """Google's `RetryInfo` detail, such as `{"retryDelay": "80822s"}`."""
    for detail in cast("list[Any]", details) if isinstance(details, list) else []:
        if not isinstance(detail, Mapping):
            continue
        detail = cast("Mapping[str, Any]", detail)
        delay = detail.get("retryDelay")
        if str(detail.get("@type", "")).endswith("RetryInfo") and isinstance(delay, str):
            try:
                return float(delay.removesuffix("s"))
            except ValueError:
                return None
    return None


def _wait_in_headers(headers: Mapping[str, str]) -> float | None:
    """`retry-after-ms`, or `retry-after` in seconds. A date in `retry-after` is not read."""
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            return float(value) / scale
        except ValueError:
            continue
    return None


def _duration(seconds: float) -> str:
    if seconds >= 5400:
        return f"{round(seconds / 3600)} hours"
    return f"{max(1, round(seconds / 60))} minutes"


class CallLedger:
    """Every model call, one row each, in the home's database.

    A connection is opened for each write and closed after it: a write per model call is little
    next to the call itself, and a connection belongs to the thread that opened it.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def record(self, call: CallRecord) -> bool:
        """Write one call. A failure is logged and returns `False`; it never reaches the caller."""
        usage = call.usage
        counts = (
            (None,) * 6
            if usage is None
            else (
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
                usage.reasoning_tokens,
                usage.cache_write_long_tokens,
            )
        )
        values = (
            call.started_at,
            call.purpose,
            call.scope,
            call.provider,
            call.model,
            call.served_model,
            call.outcome.value,
            call.duration_ms,
            *counts,
            None if call.cost.usd is None else str(call.cost.usd),
            call.cost.source.value,
        )
        try:
            connection = self._database.connect()
        except DatabaseError as error:
            _logger.error("The call ledger could not record a call: %s", error)
            return False
        try:
            with transaction(connection):
                connection.execute(
                    f"INSERT INTO models_calls ({_COLUMNS}) VALUES ({', '.join('?' * 16)})",  # noqa: S608
                    values,
                )
        except sqlite3.Error as error:
            _logger.error("The call ledger could not record a call: %s", error)
            return False
        finally:
            connection.close()
        return True

    def since(self, started_at: str) -> list[CallRecord]:
        """The calls started at `started_at` (UTC text) or later, oldest first."""
        connection = self._database.connect()
        try:
            rows: list[tuple[object, ...]] = connection.execute(
                f"SELECT {_COLUMNS} FROM models_calls WHERE started_at >= ? ORDER BY id",  # noqa: S608
                (started_at,),
            ).fetchall()
        finally:
            connection.close()
        return [_record(row) for row in rows]

    def recent(self, limit: int = 50) -> list[CallRecord]:
        """The latest calls, newest first."""
        connection = self._database.connect()
        try:
            rows: list[tuple[object, ...]] = connection.execute(
                f"SELECT {_COLUMNS} FROM models_calls ORDER BY id DESC LIMIT ?",  # noqa: S608
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [_record(row) for row in rows]


def _record(row: tuple[object, ...]) -> CallRecord:
    started_at, purpose, scope, provider, model, served, outcome, duration = row[:8]
    counts, cost_usd, cost_source = row[8:14], row[14], row[15]
    # A row from before migration 2 has no hour-long write: none was made then.
    usage = (
        None
        if counts[0] is None
        else Usage(*(0 if count is None else int(cast("int", count)) for count in counts))
    )
    return CallRecord(
        started_at=str(started_at),
        purpose=str(purpose),
        scope=None if scope is None else str(scope),
        provider=str(provider),
        model=str(model),
        served_model=None if served is None else str(served),
        outcome=CallOutcome(str(outcome)),
        duration_ms=int(cast("int", duration)),
        usage=usage,
        cost=Cost(
            None if cost_usd is None else Decimal(str(cost_usd)), CostSource(str(cost_source))
        ),
    )
