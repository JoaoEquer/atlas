"""A deterministic model server that speaks OpenAI's protocols over real HTTP, for tests.

Tests point a provider's `base_url` here and call it through the real SDK, so nothing stands in for
the adapter under proof (docs/ARCHITECTURE.md, Tests). The server answers from a script, records
every request it gets, and can behave like a particular provider:

- **Chat Completions** (`/v1/chat/completions`), streamed as server-sent events chunk by chunk,
  with usage only when `stream_options.include_usage` asks (OpenAI) or always (OpenRouter).
- **The Responses API** (`/v1/responses`), streamed as typed events, stateless (`store: false`)
  with the rules OpenAI enforces there: no item ids from earlier replies, and encrypted reasoning
  only back to the model that wrote it.
- **Native tool search** on the Responses API, as OpenAI runs it since GPT-5.4: a `tool_search`
  tool executed by the client, refused for a model without it, and a `tool_search_output` refused
  without the `tool_search_call` it answers.
- **Quirks:** a strict schema that refuses fields it does not take (most providers), Gemini's
  thought signatures that must come back on tool calls, Gemini's refusal of a trailing assistant
  message, and OpenRouter's cost in the usage.
- **Failures:** scripted HTTP errors (with `Retry-After` and `x-should-retry`, which the SDK
  reads), errors inside the stream, and streams cut before they finish.
- **A real provider's reply played back** (`FakeRaw`, step 9c): a recorded body sent byte for
  byte, past the checks that only the fake's own replies could pass (its thought signatures).
- **OpenAI's transcription** (`/v1/audio/transcriptions`, step 17a): the multipart form OpenAI
  takes, its fields and its file recorded (`heard`), answered from a script of its own
  (`transcribe`), or a 500 when it runs out; its speech (`/v1/audio/speech`), an Ogg file; and its
  live voice sessions (`/v1/live/sessions`), a WebRTC answer to the offer.
- **Gemini's voice** (`/v1beta`, the voice plugin's other provider): its interactions, which write
  down an audio file from the same script (`transcribe`) or speak a WAV file; its Files API upload,
  in its two requests; its lists of models and voices; and the tokens of its live sessions. Every
  one takes the key in `x-goog-api-key`, and refuses as Gemini does.
- **Driven from outside**, for the desktop app's end-to-end tests: `python tests/fake_model.py`
  serves until stopped and writes `FAKE_MODEL_READY port=<n>`; `POST /_fake/reply` queues replies
  given as JSON (`reply_from_json`), `POST /_fake/transcribe` the transcriptions' texts, and
  `GET /_fake/requests` returns the bodies the model got.
  `--host 0.0.0.0 --port 8000` serves it from a container beside Atlas's (tests/docker).
"""

import argparse
import asyncio
import base64
import dataclasses
import hashlib
import io
import json
import sys
import threading
import time
import wave
from collections import deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any, cast

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

#: The fields a strict Chat Completions schema takes on a message and on a tool call.
STRICT_MESSAGE_FIELDS = frozenset({"role", "content", "tool_calls", "tool_call_id", "name"})
STRICT_CALL_FIELDS = frozenset({"id", "type", "function"})
#: What the fake speech endpoint answers: an Ogg file's first bytes.
SPOKEN = b"OggS\x00\x02" + b"\x00" * 40 + b"OpusHead"
#: What the fake live session answers the WebRTC offer with.
LIVE_ANSWER = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=fake\r\n"


def _silence() -> bytes:
    """A tenth of a second of silence as Gemini speaks: WAV, 24 kHz, 16-bit mono."""
    made = io.BytesIO()
    with wave.open(made, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(24_000)
        file.writeframes(b"\x00\x00" * 2_400)
    return made.getvalue()


#: What Gemini's fake speech answers: a WAV file.
SPOKEN_WAV = _silence()
#: The token the fake gives a Gemini live session.
LIVE_TOKEN = "auth_tokens/fake-token"


@dataclass
class FakeCall:
    """A tool call the fake model makes."""

    name: str
    arguments: str = "{}"
    id: str | None = None
    #: Extra fields on the call, as a provider sends them (`extra_content`).
    fields: dict[str, Any] = field(default_factory=dict[str, Any])
    #: A native tool search on the Responses API: a `tool_search_call` item, not a function call.
    search: bool = False


@dataclass
class FakeUsage:
    input: int = 0
    output: int = 0
    cached: int = 0
    cache_write: int = 0
    reasoning: int = 0
    #: Reasoning counted only in `total_tokens`, as Gemini counts its thinking.
    hidden_reasoning: int = 0
    #: OpenRouter's `usage.cost`, in dollars.
    cost: float | None = None


@dataclass
class FakeReply:
    """One scripted reply."""

    text: str = ""
    #: Readable reasoning: `reasoning_content` on Chat Completions, a summary on Responses.
    reasoning: str = ""
    calls: list[FakeCall] = field(default_factory=list[FakeCall])
    #: Extra fields on the first delta of a Chat Completions reply (`reasoning_details`).
    fields: dict[str, Any] = field(default_factory=dict[str, Any])
    #: Streamed `reasoning_details` fragments, one per chunk, as OpenRouter sends them.
    detail_fragments: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    usage: FakeUsage | None = None
    #: The stop reason; by default `tool_calls` when there are calls and `stop` otherwise.
    finish: str | None = None
    #: The model the reply says answered; by default the one asked for.
    model: str | None = None
    #: Close the stream after this many chunks or events, without finishing.
    cut_after: int | None = None
    #: Send an error inside the stream after the first chunk or event.
    error_in_stream: dict[str, Any] | None = None
    #: Seconds between chunks or events, so a client can leave while the reply streams.
    pause: float = 0.0
    #: Seconds of silence before the reply starts (before even its HTTP status).
    wait_before: float = 0.0
    #: Taken only by a request whose system prompt holds this text, so conversations that call
    #: at once (a parent and its subagents) each get their own; a reply without it is taken by
    #: the next request, in order.
    when: str | None = None


@dataclass
class FakeError:
    """A scripted HTTP error, with OpenAI's error body, or Google's when `google` names a status."""

    status: int
    message: str
    code: str | None = None
    type: str = "invalid_request_error"
    retry_after_ms: int | None = None
    should_retry: bool | None = None
    #: Google's status name (RESOURCE_EXHAUSTED): the body becomes Gemini's list envelope.
    google: str | None = None
    #: Google's RetryInfo delay, such as "80822s".
    retry_delay: str | None = None
    #: As `FakeReply.when`: taken only by a request whose system prompt holds this text.
    when: str | None = None


@dataclass
class FakeRaw:
    """A provider's reply as it was recorded (`scripts/record_turn.py`), sent byte for byte."""

    body: str
    status: int = 200
    content_type: str = "text/event-stream"
    #: As `FakeReply.when`.
    when: str | None = None


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: dict[str, Any] | None


class FakeModelServer:
    """Start with `with FakeModelServer() as server:`; point a provider at `server.base_url`."""

    def __init__(
        self,
        *,
        key: str | None = "fake-key",
        usage_always: bool = False,
        strict_fields: frozenset[str] | None = None,
        signatures: bool = False,
        refuse_trailing_assistant: bool = False,
        thought_tag: str | None = None,
        chunk_size: int = 4,
        tool_search_models: frozenset[str] = frozenset({"gpt-5.4", "gpt-6-luna"}),
        whole_calls: bool = False,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        #: The key the server takes; `None` takes any, as a local server does.
        self.key = key
        self.usage_always = usage_always
        #: When set, the schema is strict: the only extra fields taken are these.
        self.strict_fields = strict_fields
        self.signatures = signatures
        self.refuse_trailing_assistant = refuse_trailing_assistant
        #: Send readable reasoning inside the text, between these tags, as Gemini does.
        self.thought_tag = thought_tag
        self.chunk_size = chunk_size
        #: Stream each tool call whole in one chunk with no index, as Gemini does.
        self.whole_calls = whole_calls
        #: The models that take a native `tool_search` tool on the Responses API.
        self.tool_search_models = tool_search_models
        self.requests: list[Recorded] = []
        #: Streams the client closed before the last chunk or event was sent.
        self.abandoned = 0
        self._script: deque[FakeReply | FakeError | FakeRaw] = deque()
        self._checks: deque[FakeError] = deque()
        self._signed: dict[str, str] = {}
        #: The cached prefixes of Anthropic's requests, by hash, for the cache a request reads.
        self._cached: set[str] = set()
        self._issued = 0
        #: What the transcriptions answer, in order: a text, or an error.
        self._transcripts: deque[str | FakeError] = deque()
        #: Each transcription asked for: its form's fields, and the file as `file_name`,
        #: `file_type` and `file_bytes`.
        self.heard: list[dict[str, Any]] = []
        #: The model ids OpenAI's `/models` lists.
        self.model_ids: list[str] = ["fake-model"]
        #: What Gemini's `/models` lists, and its `/voices` by language.
        self.gemini_models: list[dict[str, Any]] = []
        self.gemini_voices: dict[str, list[dict[str, Any]]] = {}
        #: The files uploaded through Gemini's Files API, by their URI.
        self.uploaded: dict[str, bytes] = {}
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._host = host
        self.port = port

    # -- the script ------------------------------------------------------------------------------

    def reply(self, *items: FakeReply | FakeError | FakeRaw) -> None:
        """Queue what the next model calls get, in order."""
        self._script.extend(items)

    def transcribe(self, *items: str | FakeError) -> None:
        """Queue what the next transcriptions answer: the text, or an error."""
        self._transcripts.extend(items)

    def refuse_check(self, error: FakeError) -> None:
        """The next key check (`/models`, `/key`) gets this error."""
        self._checks.append(error)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def gemini_url(self) -> str:
        """The address for Gemini's own API, as the voice plugin reaches it."""
        return f"http://127.0.0.1:{self.port}/v1beta"

    @property
    def root_url(self) -> str:
        """The address for Anthropic's SDK, which adds `/v1/messages` itself."""
        return f"http://127.0.0.1:{self.port}"

    def bodies(self, path: str) -> list[dict[str, Any]]:
        """The JSON bodies received on a path, such as `/v1/chat/completions`."""
        return [request.body for request in self.requests if request.path == path and request.body]

    # -- running ---------------------------------------------------------------------------------

    def __enter__(self) -> "FakeModelServer":
        app = Starlette(
            routes=[
                Route("/v1/chat/completions", self._chat, methods=["POST"]),
                Route("/v1/responses", self._responses, methods=["POST"]),
                Route("/v1/messages", self._messages, methods=["POST"]),
                Route("/v1/models", self._check, methods=["GET"]),
                Route("/v1/key", self._check, methods=["GET"]),
                Route("/v1/audio/transcriptions", self._transcription, methods=["POST"]),
                Route("/v1/audio/speech", self._speech, methods=["POST"]),
                Route("/v1/live/sessions", self._live_session, methods=["POST"]),
                Route("/v1beta/interactions", self._interaction, methods=["POST"]),
                Route("/v1beta/models", self._gemini_models, methods=["GET"]),
                Route("/v1beta/voices", self._gemini_voices, methods=["GET"]),
                Route("/v1beta/auth_tokens", self._gemini_token, methods=["POST"]),
                Route("/upload/v1beta/files", self._upload_start, methods=["POST"]),
                Route("/_fake/upload/{name}", self._upload, methods=["POST"]),
                Route("/_fake/reply", self._queue, methods=["POST"]),
                Route("/_fake/transcribe", self._queue_transcripts, methods=["POST"]),
                Route("/_fake/requests", self._recorded, methods=["GET"]),
            ]
        )
        config = uvicorn.Config(
            app,
            host=self._host,
            port=self.port,
            # No log_config and no log_level: uvicorn would otherwise set the level of its loggers
            # for the whole test process, and the log tests read uvicorn's lines.
            log_config=None,
            log_level=None,
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("The fake model server did not start.")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_: object) -> None:
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=10)

    # -- HTTP ------------------------------------------------------------------------------------

    async def _record(self, request: Request) -> dict[str, Any] | None:
        body = await request.json() if request.method == "POST" else None
        self.requests.append(
            Recorded(request.method, request.url.path, dict(request.headers), body)
        )
        return cast("dict[str, Any] | None", body)

    def _refused_key(self, request: Request) -> Response | None:
        if _anthropic(request):
            # Anthropic's SDK sends the key alone, in x-api-key.
            if self.key is None or (
                request.headers.get("x-api-key") == self.key
                and "authorization" not in request.headers
            ):
                return None
            return _anthropic_error(FakeError(401, "invalid x-api-key"))
        if self.key is None or request.headers.get("authorization") == f"Bearer {self.key}":
            return None
        return _error(FakeError(401, "Incorrect API key provided.", code="invalid_api_key"))

    async def _check(self, request: Request) -> Response:
        await self._record(request)
        refused = self._refused_key(request)
        if refused is not None:
            return refused
        if self._checks:
            failure = self._checks.popleft()
            return _anthropic_error(failure) if _anthropic(request) else _error(failure)
        if _anthropic(request):
            model = {
                "id": "claude-fake",
                "type": "model",
                "display_name": "Claude Fake",
                "created_at": "2026-09-01T00:00:00Z",
            }
            return JSONResponse(
                {
                    "data": [model],
                    "has_more": False,
                    "first_id": "claude-fake",
                    "last_id": "claude-fake",
                }
            )
        if request.url.path.endswith("/key"):
            return JSONResponse({"data": {"label": "fake", "limit": None, "usage": 0}})
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"id": name, "object": "model", "owned_by": "x"} for name in self.model_ids
                ],
            }
        )

    async def _transcription(self, request: Request) -> Response:
        """OpenAI's `audio/transcriptions`: a multipart form with the model and the file."""
        form = await request.form()
        heard: dict[str, Any] = {}
        for name, value in form.multi_items():
            if isinstance(value, str):
                heard[name] = value
            else:
                heard["file_name"] = value.filename
                heard["file_type"] = value.content_type
                heard["file_bytes"] = await value.read()
        self.heard.append(heard)
        self.requests.append(
            Recorded(request.method, request.url.path, dict(request.headers), None)
        )
        refused = self._refused_key(request)
        if refused is not None:
            return refused
        if "file_bytes" not in heard or not heard.get("model"):
            return _error(FakeError(400, "A transcription takes a file and a model."))
        if not self._transcripts:
            return _error(
                FakeError(500, "The fake has no scripted transcription left.", type="server_error")
            )
        item = self._transcripts.popleft()
        if isinstance(item, FakeError):
            return _error(item)
        return JSONResponse({"text": item})

    async def _speech(self, request: Request) -> Response:
        """OpenAI's `audio/speech`: the JSON body recorded, and an Ogg file that says nothing."""
        body = await self._record(request) or {}
        refused = self._refused_key(request)
        if refused is not None:
            return refused
        if not body.get("input") or not body.get("model") or not body.get("voice"):
            return _error(FakeError(400, "Speech takes a model, a voice and an input."))
        return Response(SPOKEN, media_type="audio/ogg")

    async def _live_session(self, request: Request) -> Response:
        """OpenAI's `live/sessions`: the session and the WebRTC offer recorded, and an answer."""
        body = await self._record(request) or {}
        refused = self._refused_key(request)
        if refused is not None:
            return refused
        transport = cast("dict[str, Any]", body.get("transport") or {})
        if not str(transport.get("sdp") or "").startswith("v="):
            return _error(FakeError(400, "A live session takes a WebRTC offer."))
        return JSONResponse(
            {"session": {"id": "live_fake"}, "transport": {"type": "webrtc", "sdp": LIVE_ANSWER}}
        )

    def _refused_google(self, request: Request) -> Response | None:
        """Gemini's own API takes its key in `x-goog-api-key`, and nothing else."""
        if self.key is None or (
            request.headers.get("x-goog-api-key") == self.key
            and "authorization" not in request.headers
        ):
            return None
        return _google_error(400, "API key not valid. Please pass a valid API key.")

    async def _interaction(self, request: Request) -> Response:
        """Gemini's `interactions`: speech when the answer is audio, otherwise a transcription of
        the audio in the input, from the script `transcribe` fills."""
        body = await self._record(request) or {}
        refused = self._refused_google(request)
        if refused is not None:
            return refused
        items = cast("list[dict[str, Any]]", body.get("input") or [])
        if not body.get("model") or not items:
            return _google_error(400, "An interaction takes a model and an input.")
        if cast("dict[str, Any]", body.get("response_format") or {}).get("type") == "audio":
            audio = base64.b64encode(SPOKEN_WAV).decode("ascii")
            content = [{"type": "audio", "mime_type": "audio/wav", "data": audio}]
            return JSONResponse({"steps": [{"type": "model_output", "content": content}]})
        audio = next((item for item in items if item.get("type") == "audio"), None)
        if audio is None or not (audio.get("data") or audio.get("uri") in self.uploaded):
            return _google_error(400, "No audio, or a file that was never uploaded.")
        if not self._transcripts:
            return _google_error(500, "The fake has no scripted transcription left.")
        item = self._transcripts.popleft()
        if isinstance(item, FakeError):
            return _google_error(item.status, item.message)
        return JSONResponse(
            {
                "status": "completed",
                "steps": [
                    {"type": "thought", "content": [{"type": "text", "text": "thinking"}]},
                    {"type": "model_output", "content": [{"type": "text", "text": item}]},
                ],
            }
        )

    async def _gemini_models(self, request: Request) -> Response:
        await self._record(request)
        return self._refused_google(request) or JSONResponse({"models": self.gemini_models})

    async def _gemini_voices(self, request: Request) -> Response:
        await self._record(request)
        language = request.query_params.get("language_code", "")
        return self._refused_google(request) or JSONResponse(
            {"voices": self.gemini_voices.get(language, [])}
        )

    async def _gemini_token(self, request: Request) -> Response:
        body = await self._record(request) or {}
        refused = self._refused_google(request)
        if refused is not None:
            return refused
        if body.get("uses") != 1 or not body.get("expireTime"):
            return _google_error(400, "A token takes its uses and when it expires.")
        return JSONResponse({"name": LIVE_TOKEN})

    async def _upload_start(self, request: Request) -> Response:
        """The Files API's first request: where to send the bytes."""
        body = await self._record(request) or {}
        refused = self._refused_google(request)
        if refused is not None:
            return refused
        if request.headers.get("x-goog-upload-command") != "start" or not body.get("file"):
            return _google_error(400, "An upload starts with its command and its file.")
        name = f"f{len(self.uploaded) + 1}"
        place = f"http://127.0.0.1:{self.port}/_fake/upload/{name}"
        return Response(headers={"x-goog-upload-url": place})

    async def _upload(self, request: Request) -> Response:
        """The Files API's second request: the bytes, and the file's URI."""
        if request.headers.get("x-goog-upload-command") != "upload, finalize":
            return _google_error(400, "The bytes go with upload, finalize.")
        uri = f"https://example.com/v1beta/files/{request.path_params['name']}"
        self.uploaded[uri] = await request.body()
        return JSONResponse({"file": {"uri": uri, "state": "ACTIVE"}})

    async def _queue_transcripts(self, request: Request) -> Response:
        """Queue what the next transcriptions answer, for a test outside this process."""
        texts = cast("list[str]", await request.json())
        self.transcribe(*texts)
        return JSONResponse({"queued": len(texts)})

    async def _queue(self, request: Request) -> Response:
        """Queue the replies a test outside this process gives as JSON."""
        items = cast("list[dict[str, Any]]", await request.json())
        self.reply(*(reply_from_json(item) for item in items))
        return JSONResponse({"queued": len(items), "left": len(self._script)})

    async def _recorded(self, _request: Request) -> Response:
        bodies = [
            {
                "path": r.path,
                "body": r.body,
                "authorization": r.headers.get("authorization")
                or (f"Bearer {r.headers['x-api-key']}" if "x-api-key" in r.headers else None),
            }
            for r in self.requests
            if r.path.startswith("/v1/")
        ]
        return JSONResponse({"requests": bodies, "left": len(self._script)})

    def _next(self, body: dict[str, Any]) -> FakeReply | FakeError:
        system = _system_text(body)
        for index, item in enumerate(self._script):
            if item.when is None or item.when in system:
                if isinstance(item, FakeRaw):  # pragma: no cover - `_raw` takes it first
                    raise TypeError("A recorded reply is served by _raw.")
                del self._script[index]
                return item
        return FakeError(500, "The fake model has no scripted reply left.", type="server_error")

    def _raw(self, body: dict[str, Any]) -> Response | None:
        """The recorded reply this request gets, when the next one for it is recorded."""
        system = _system_text(body)
        for index, item in enumerate(self._script):
            if item.when is None or item.when in system:
                if not isinstance(item, FakeRaw):
                    return None
                del self._script[index]
                return Response(item.body, status_code=item.status, media_type=item.content_type)
        return None

    # -- Chat Completions ------------------------------------------------------------------------

    async def _chat(self, request: Request) -> Response:
        body = await self._record(request) or {}
        raw = self._raw(body)
        if raw is not None:
            return raw
        refused = self._refused_key(request) or self._chat_refusal(body)
        if refused is not None:
            return refused
        item = self._next(body)
        if isinstance(item, FakeError):
            return _error(item)
        await asyncio.sleep(item.wait_before)
        model = item.model or str(body.get("model"))
        with_usage = self.usage_always or bool(
            (body.get("stream_options") or {}).get("include_usage")
        )
        chunks = list(self._chat_chunks(item, model, body, with_usage=with_usage))
        return self._sse(
            (f"data: {json.dumps(chunk)}\n\n" for chunk in chunks), item, "data: [DONE]\n\n"
        )

    def _chat_refusal(self, body: dict[str, Any]) -> Response | None:
        google = cast("dict[str, Any]", (body.get("extra_body") or {}).get("google") or {})
        if "reasoning_effort" in body and "thinking_config" in google:
            # Gemini, seen on 10/10/2026.
            return _error(
                FakeError(
                    400,
                    "Expected one of either `reasoning_effort` or custom `thinking_config`; "
                    "found both.",
                )
            )
        messages = cast("list[dict[str, Any]]", body.get("messages") or [])
        if self.refuse_trailing_assistant and messages and messages[-1].get("role") == "assistant":
            return _error(FakeError(400, "Requests ending with a model turn are not supported."))
        for position, message in enumerate(messages):
            if self.strict_fields is not None:
                extra = set(message) - STRICT_MESSAGE_FIELDS - self.strict_fields
                if message.get("role") == "tool" and "name" in message:
                    extra.add("name")
                if extra:
                    return _error(
                        FakeError(
                            400,
                            f"messages[{position}]: Extra inputs are not permitted: "
                            f"{', '.join(sorted(extra))}",
                        )
                    )
            for call in cast("list[dict[str, Any]]", message.get("tool_calls") or []):
                if self.strict_fields is not None:
                    extra = set(call) - STRICT_CALL_FIELDS - self.strict_fields
                    if extra:
                        return _error(
                            FakeError(
                                400,
                                f"messages[{position}].tool_calls: Extra inputs are not "
                                f"permitted: {', '.join(sorted(extra))}",
                            )
                        )
                expected = self._signed.get(str(call.get("id")))
                given = ((call.get("extra_content") or {}).get("google") or {}).get(
                    "thought_signature"
                )
                if expected is not None and given != expected:
                    return _error(
                        FakeError(
                            400,
                            "Function call is missing a thought_signature in "
                            f"messages[{position}].",
                        )
                    )
        return None

    def _chat_chunks(
        self, item: FakeReply, model: str, body: dict[str, Any], *, with_usage: bool
    ) -> Iterator[dict[str, Any]]:
        self._issued += 1
        base = {
            "id": f"chatcmpl-{self._issued}",
            "object": "chat.completion.chunk",
            "created": 1_790_000_000,
            "model": model,
        }

        def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
            return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        yield chunk({"role": "assistant", "content": "", **item.fields})
        for fragment in item.detail_fragments:
            yield chunk({"reasoning_details": [fragment]})
        if self.thought_tag is not None:
            # Gemini: the thought first, then the answer, all in the content; the small chunks
            # cut the tags themselves.
            tag = self.thought_tag
            inline = f"<{tag}>{item.reasoning}</{tag}>" if item.reasoning else ""
            for piece in self._pieces(inline + item.text):
                yield chunk({"content": piece})
        else:
            for piece in self._pieces(item.reasoning):
                yield chunk({"reasoning_content": piece})
            for piece in self._pieces(item.text):
                yield chunk({"content": piece})
        for index, call in enumerate(item.calls):
            call_id = call.id or f"call_{self._issued}_{index}"
            fields = dict(call.fields)
            if self.signatures:
                signature = f"sig-{self._issued}-{index}"
                self._signed[call_id] = signature
                fields["extra_content"] = {"google": {"thought_signature": signature}}
            if self.whole_calls:
                # Gemini (seen on 09/10/2026): each call whole in its own chunk, with no index.
                whole = {"id": call_id, "type": "function", **fields}
                whole["function"] = {"name": call.name, "arguments": call.arguments}
                yield chunk({"tool_calls": [whole]})
                continue
            yield chunk(
                {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": ""},
                            **fields,
                        }
                    ]
                }
            )
            for piece in self._pieces(call.arguments):
                yield chunk({"tool_calls": [{"index": index, "function": {"arguments": piece}}]})
        finish = item.finish or ("tool_calls" if item.calls else "stop")
        yield chunk({}, finish)
        if with_usage:
            yield {**base, "choices": [], "usage": self._chat_usage(item, body)}

    def _chat_usage(self, item: FakeReply, body: dict[str, Any]) -> dict[str, Any]:
        usage = item.usage or FakeUsage(
            input=len(json.dumps(body.get("messages"))) // 4 + 1,
            output=(len(item.text) + len(item.reasoning)) // 4 + 1,
        )
        raw: dict[str, Any] = {
            "prompt_tokens": usage.input,
            "completion_tokens": usage.output,
            "total_tokens": usage.input + usage.output + usage.hidden_reasoning,
            "prompt_tokens_details": {
                "cached_tokens": usage.cached,
                "cache_write_tokens": usage.cache_write,
            },
            "completion_tokens_details": {"reasoning_tokens": usage.reasoning},
        }
        if usage.cost is not None:
            raw["cost"] = usage.cost
        return raw

    # -- the Responses API -----------------------------------------------------------------------

    async def _responses(self, request: Request) -> Response:
        body = await self._record(request) or {}
        raw = self._raw(body)
        if raw is not None:
            return raw
        refused = self._refused_key(request) or self._responses_refusal(body)
        if refused is not None:
            return refused
        item = self._next(body)
        if isinstance(item, FakeError):
            return _error(item)
        model = item.model or str(body.get("model"))
        events = [
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
            for event in self._response_events(item, model, body)
        ]
        return self._sse(iter(events), item, None)

    def _responses_refusal(self, body: dict[str, Any]) -> Response | None:
        if body.get("store") is not False:
            return _error(FakeError(400, "This fake only serves stateless calls (store: false)."))
        model = str(body.get("model"))
        for tool in cast("list[dict[str, Any]]", body.get("tools") or []):
            if tool.get("type") == "tool_search" and model not in self.tool_search_models:
                return _error(FakeError(400, f"tool_search is not supported with {model}."))
        searched: set[str] = set()
        for position, entry in enumerate(cast("list[dict[str, Any]]", body.get("input") or [])):
            if entry.get("type") == "tool_search_call":
                searched.add(str(entry.get("call_id")))
            if entry.get("type") == "tool_search_output" and entry.get("call_id") not in searched:
                return _error(
                    FakeError(
                        400,
                        f"input[{position}]: no tool search call found for call_id "
                        f"{entry.get('call_id')}.",
                    )
                )
            if "id" in entry:
                return _error(
                    FakeError(
                        404,
                        f"Item with id '{entry['id']}' not found. Items are not "
                        "persisted when `store` is set to false.",
                    )
                )
            if entry.get("type") == "reasoning":
                sealed = str(entry.get("encrypted_content") or "")
                if not sealed:
                    return _error(
                        FakeError(
                            400,
                            f"input[{position}]: a reasoning item needs its "
                            "encrypted_content when store is false.",
                        )
                    )
                if not sealed.startswith(f"enc:{model}:"):
                    return _error(
                        FakeError(
                            400,
                            "The encrypted content could not be verified.",
                            code="invalid_encrypted_content",
                        )
                    )
        return None

    def _response_events(
        self, item: FakeReply, model: str, body: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        self._issued += 1
        number = self._issued
        response_id = f"resp_{number}"
        output: list[dict[str, Any]] = []
        sequence = iter(range(1_000_000))

        def event(kind: str, **data: Any) -> dict[str, Any]:
            return {"type": kind, "sequence_number": next(sequence), **data}

        def response(status: str, **data: Any) -> dict[str, Any]:
            return {
                "id": response_id,
                "object": "response",
                "created_at": 1_790_000_000,
                "status": status,
                "model": model,
                "output": list(output),
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
                **data,
            }

        yield event("response.created", response=response("in_progress"))
        if item.reasoning:
            reasoning = {"id": f"rs_{number}", "type": "reasoning", "summary": []}
            yield event("response.output_item.added", output_index=len(output), item=reasoning)
            for piece in self._pieces(item.reasoning):
                yield event(
                    "response.reasoning_summary_text.delta",
                    item_id=reasoning["id"],
                    output_index=len(output),
                    summary_index=0,
                    delta=piece,
                )
            done = {
                **reasoning,
                "summary": [{"type": "summary_text", "text": item.reasoning}],
                "encrypted_content": f"enc:{model}:{number}",
            }
            yield event("response.output_item.done", output_index=len(output), item=done)
            output.append(done)
        if item.text:
            message = {
                "id": f"msg_{number}",
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
                "content": [],
            }
            yield event("response.output_item.added", output_index=len(output), item=message)
            for piece in self._pieces(item.text):
                yield event(
                    "response.output_text.delta",
                    item_id=message["id"],
                    output_index=len(output),
                    content_index=0,
                    delta=piece,
                    logprobs=[],
                )
            done = {
                **message,
                "status": "completed",
                "content": [{"type": "output_text", "text": item.text, "annotations": []}],
            }
            yield event("response.output_item.done", output_index=len(output), item=done)
            output.append(done)
        for index, call in enumerate(item.calls):
            if call.search:
                search_item = {
                    "id": f"tsc_{number}_{index}",
                    "type": "tool_search_call",
                    "execution": "client",
                    "call_id": call.id or f"call_{number}_{index}",
                    "arguments": json.loads(call.arguments or "{}"),
                    "status": "completed",
                }
                yield event(
                    "response.output_item.added", output_index=len(output), item=search_item
                )
                yield event("response.output_item.done", output_index=len(output), item=search_item)
                output.append(search_item)
                continue
            call_item = {
                "id": f"fc_{number}_{index}",
                "type": "function_call",
                "call_id": call.id or f"call_{number}_{index}",
                "name": call.name,
                "arguments": "",
                "status": "in_progress",
            }
            yield event("response.output_item.added", output_index=len(output), item=call_item)
            for piece in self._pieces(call.arguments):
                yield event(
                    "response.function_call_arguments.delta",
                    item_id=call_item["id"],
                    output_index=len(output),
                    delta=piece,
                )
            done = {**call_item, "arguments": call.arguments, "status": "completed"}
            yield event("response.output_item.done", output_index=len(output), item=done)
            output.append(done)
        usage = item.usage or FakeUsage(
            input=len(json.dumps(body.get("input"))) // 4 + 1,
            output=(len(item.text) + len(item.reasoning)) // 4 + 1,
        )
        raw_usage = {
            "input_tokens": usage.input,
            "input_tokens_details": {
                "cached_tokens": usage.cached,
                "cache_write_tokens": usage.cache_write,
            },
            "output_tokens": usage.output,
            "output_tokens_details": {"reasoning_tokens": usage.reasoning},
            "total_tokens": usage.input + usage.output,
        }
        if item.finish == "max_output_tokens":
            yield event(
                "response.incomplete",
                response=response(
                    "incomplete",
                    usage=raw_usage,
                    incomplete_details={"reason": "max_output_tokens"},
                ),
            )
        elif item.finish == "failed":
            yield event(
                "response.failed",
                response=response(
                    "failed", error={"code": "server_error", "message": "The model failed."}
                ),
            )
        else:
            yield event("response.completed", response=response("completed", usage=raw_usage))

    # -- Anthropic's Messages API ----------------------------------------------------------------

    async def _messages(self, request: Request) -> Response:
        body = await self._record(request) or {}
        raw = self._raw(body)
        if raw is not None:
            return raw
        refused = self._refused_key(request) or self._messages_refusal(
            body, request.headers.get("anthropic-beta", "")
        )
        if refused is not None:
            return refused
        item = self._next(body)
        if isinstance(item, FakeError):
            return _anthropic_error(item)
        await asyncio.sleep(item.wait_before)
        model = item.model or str(body.get("model"))
        events = [
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
            for event in self._message_events(item, model, body)
        ]
        if item.error_in_stream is not None:
            # Anthropic's error event, after the first one.
            error = {"type": "error", "error": item.error_in_stream}
            events = [events[0], f"event: error\ndata: {json.dumps(error)}\n\n"]
            item = dataclasses.replace(item, error_in_stream=None)
        return self._sse(iter(events), item, None)

    def _messages_refusal(self, body: dict[str, Any], betas: str) -> Response | None:
        """What Anthropic refuses with a 400, as its documentation and past defects say."""

        def refuse(message: str) -> Response:
            return _anthropic_error(FakeError(400, message))

        messages = cast("list[dict[str, Any]]", body.get("messages") or [])
        tools = cast("list[dict[str, Any]]", body.get("tools") or [])
        if not messages or messages[0].get("role") != "user":
            return refuse('messages: the first message must use the "user" role')
        if messages[-1].get("role") == "assistant":
            return refuse("This model does not support assistant message prefill.")
        for name in ("temperature", "top_p", "top_k"):
            if name in body:
                return refuse(f"{name} is not supported with this model.")
        choice = cast("dict[str, Any]", body.get("tool_choice") or {})
        if choice.get("type") in ("any", "tool"):
            return refuse("tool_choice forcing tool use is not supported with thinking.")
        thinking = cast("dict[str, Any]", body.get("thinking") or {})
        effort = cast("dict[str, Any]", body.get("output_config") or {}).get("effort")
        if thinking.get("type") == "disabled" and effort in ("xhigh", "max"):
            return refuse(f"thinking: disabled is not supported with effort {effort}.")
        if "context_management" in body and "context-management-2025-06-27" not in betas:
            return refuse("context_management: requires the beta context-management-2025-06-27.")
        names: list[str] = []
        for tool in tools:
            names.append(str(tool.get("name")))
            schema = cast("dict[str, Any]", tool.get("input_schema") or {})
            if any(key in schema for key in ("oneOf", "anyOf", "allOf")):
                return refuse(
                    f"tools.{tool.get('name')}.input_schema: unions are not allowed at the "
                    "top level."
                )
            if tool.get("defer_loading") and "cache_control" in tool:
                return refuse("A tool with defer_loading cannot also carry cache_control.")
        if len(set(names)) != len(names):
            return refuse("tools: tool names must be unique.")
        if tools and all(tool.get("defer_loading") for tool in tools):
            return refuse(
                "At least one tool must have defer_loading=false. All tools cannot be deferred."
            )
        deferred = {str(tool.get("name")) for tool in tools if tool.get("defer_loading")}
        markers = sum(1 for tool in tools if "cache_control" in tool)
        system = body.get("system")
        for block in cast("list[dict[str, Any]]", system if isinstance(system, list) else []):
            markers += "cache_control" in block
            if block.get("type") == "text" and not str(block.get("text") or "").strip():
                return refuse("system: text content blocks must contain non-whitespace text")
        for position, message in enumerate(messages):
            content = message.get("content")
            blocks = cast("list[dict[str, Any]]", content if isinstance(content, list) else [])
            if isinstance(content, str) and not content.strip():
                return refuse(
                    f"messages.{position}: text content blocks must contain non-whitespace text"
                )
            for block in blocks:
                markers += "cache_control" in block
                problem = _block_problem(block, deferred)
                if problem is not None:
                    return refuse(f"messages.{position}.content: {problem}")
            if message.get("role") == "assistant":
                calls = {str(b.get("id")) for b in blocks if b.get("type") == "tool_use"}
                following = messages[position + 1] if position + 1 < len(messages) else {}
                answered = {
                    str(b.get("tool_use_id"))
                    for b in cast("list[dict[str, Any]]", following.get("content") or [])
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                }
                if calls - answered:
                    return refuse(
                        "messages: tool_use ids were found without tool_result blocks immediately "
                        f"after: {', '.join(sorted(calls - answered))}"
                    )
                for block in blocks:
                    if block.get("type") != "thinking" or not block.get("signature"):
                        continue
                    if block["signature"] != _anthropic_signature(body, position):
                        return refuse(
                            f"messages.{position}.content: Invalid `signature` in `thinking` "
                            "block. The block is bound to a different conversation."
                        )
        if markers > 4:
            return refuse("A maximum of 4 blocks with cache_control may be provided.")
        return None

    def _message_events(
        self, item: FakeReply, model: str, body: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        self._issued += 1
        start_usage, final_usage, applied = self._anthropic_usage(item, body)
        yield {
            "type": "message_start",
            "message": {
                "id": f"msg_{self._issued}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": start_usage,
            },
        }
        index = 0
        if item.reasoning:
            # Signed against everything before the reply: the whole request.
            signature = _anthropic_signature(body, len(cast("list[Any]", body.get("messages"))))
            yield {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            }
            for piece in self._pieces(item.reasoning):
                yield {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "thinking_delta", "thinking": piece},
                }
            yield {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "signature_delta", "signature": signature},
            }
            yield {"type": "content_block_stop", "index": index}
            index += 1
        if item.text:
            yield {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            }
            for piece in self._pieces(item.text):
                yield {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": piece},
                }
            yield {"type": "content_block_stop", "index": index}
            index += 1
        for number, call in enumerate(item.calls):
            yield {
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": call.id or f"toolu_{self._issued}_{number}",
                    "name": call.name,
                    "input": {},
                },
            }
            for piece in self._pieces(call.arguments):
                yield {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": piece},
                }
            yield {"type": "content_block_stop", "index": index}
            index += 1
        stop = item.finish or ("tool_use" if item.calls else "end_turn")
        delta: dict[str, Any] = {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": final_usage,
        }
        if applied is not None:
            delta["context_management"] = {"applied_edits": [applied]}
        yield delta
        yield {"type": "message_stop"}

    def _anthropic_usage(
        self, item: FakeReply, body: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        """Anthropic's usage: `input_tokens` is only what follows the last cache point; the cache
        read is the longest marked prefix seen before, the write the rest up to the last mark.
        Counted on the prompt after the server's clearing, which it reports as applied."""
        output = (
            len(item.text) + len(item.reasoning) + sum(len(c.arguments) for c in item.calls)
        ) // 4 + 1
        applied: dict[str, Any] | None = None
        if item.usage is not None:
            read, write, rest, long = item.usage.cached, item.usage.cache_write, item.usage.input, 0
            output = item.usage.output
        else:
            body, applied = _server_cleared(body)
            marks = _marked_prefixes(body)
            total = len(json.dumps(_unmarked(body), sort_keys=True)) // 4 + 1
            read = max((size for digest, size, _ in marks if digest in self._cached), default=0)
            last = max((size for _, size, ttl in marks if ttl is not None), default=0)
            write = max(0, last - read)
            long = write if any(ttl == "1h" for _, _, ttl in marks) else 0
            rest = max(0, total - read - write)
            self._cached.update(digest for digest, _, ttl in marks if ttl is not None)
        start = {
            "input_tokens": rest,
            "cache_creation_input_tokens": write,
            "cache_read_input_tokens": read,
            "cache_creation": {
                "ephemeral_5m_input_tokens": write - long,
                "ephemeral_1h_input_tokens": long,
            },
            "output_tokens": 1,
        }
        return start, {**start, "output_tokens": output}, applied

    # -- helpers ---------------------------------------------------------------------------------

    def _sse(self, lines: Iterator[str], item: FakeReply, last: str | None) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            finished = False
            try:
                for count, line in enumerate(lines):
                    if item.cut_after is not None and count >= item.cut_after:
                        finished = True
                        return
                    yield line
                    if count == 0 and item.error_in_stream is not None:
                        yield f"data: {json.dumps({'error': item.error_in_stream})}\n\n"
                        finished = True
                        return
                    await asyncio.sleep(item.pause)
                if last is not None:
                    yield last
                finished = True
            finally:
                if not finished:
                    self.abandoned += 1

        return StreamingResponse(stream(), media_type="text/event-stream")

    def _pieces(self, text: str) -> list[str]:
        size = self.chunk_size
        return [text[start : start + size] for start in range(0, len(text), size)]


def _google_error(status: int, message: str) -> Response:
    """An error as Gemini's own API answers one."""
    return JSONResponse({"error": {"message": message, "code": str(status)}}, status_code=status)


def _error(error: FakeError) -> Response:
    headers: dict[str, str] = {}
    if error.retry_after_ms is not None:
        headers["retry-after-ms"] = str(error.retry_after_ms)
    if error.should_retry is not None:
        headers["x-should-retry"] = "true" if error.should_retry else "false"
    if error.google is not None:
        # Gemini's OpenAI-compatible endpoint wraps its error in a list (seen on 08/10/2026).
        details: list[dict[str, Any]] = []
        if error.retry_delay is not None:
            details.append(
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": error.retry_delay,
                }
            )
        google = {
            "error": {
                "code": error.status,
                "message": error.message,
                "status": error.google,
                "details": details,
            }
        }
        return JSONResponse([google], status_code=error.status, headers=headers)
    body = {
        "error": {"message": error.message, "type": error.type, "code": error.code, "param": None}
    }
    return JSONResponse(body, status_code=error.status, headers=headers)


#: Anthropic's error types by HTTP status, for a scripted error that names none of its own.
_ANTHROPIC_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    402: "billing_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    500: "api_error",
    529: "overloaded_error",
}
#: The fields Anthropic takes on each input block: any other is "Extra inputs are not permitted"
#: (the SDK's output-only fields among them).
_BLOCK_FIELDS = {
    "text": {"type", "text", "citations", "cache_control"},
    "thinking": {"type", "thinking", "signature"},
    "redacted_thinking": {"type", "data"},
    "tool_use": {"type", "id", "name", "input", "cache_control"},
    "tool_result": {"type", "tool_use_id", "content", "is_error", "cache_control"},
    "image": {"type", "source", "cache_control"},
    "tool_reference": {"type", "tool_name"},
}


def _anthropic(request: Request) -> bool:
    """A request from Anthropic's SDK, which always sends its API version."""
    return "anthropic-version" in request.headers


def _anthropic_error(error: FakeError) -> Response:
    """A scripted error in Anthropic's envelope, with `retry-after` in seconds."""
    kind = error.type if error.type != "invalid_request_error" else None
    kind = kind or _ANTHROPIC_TYPES.get(error.status, "api_error")
    headers: dict[str, str] = {}
    if error.retry_after_ms is not None:
        headers["retry-after"] = str(error.retry_after_ms / 1000)
    if error.should_retry is not None:
        headers["x-should-retry"] = "true" if error.should_retry else "false"
    body = {"type": "error", "error": {"type": kind, "message": error.message}}
    return JSONResponse(body, status_code=error.status, headers=headers)


def _block_problem(block: dict[str, Any], deferred: set[str]) -> str | None:
    """Why Anthropic would refuse this input block, or `None`."""
    kind = block.get("type")
    allowed = _BLOCK_FIELDS.get(str(kind))
    if allowed is None:
        return f"unknown block type {kind!r}"
    extra = set(block) - allowed
    if extra:
        return f"{kind}: Extra inputs are not permitted: {', '.join(sorted(extra))}"
    if kind == "text" and not str(block.get("text") or "").strip():
        return "text content blocks must contain non-whitespace text"
    if kind == "tool_reference" and block.get("tool_name") not in deferred:
        return f"Tool reference '{block.get('tool_name')}' not found in available tools"
    if kind == "tool_result":
        inner = block.get("content")
        if isinstance(inner, str) and not inner.strip():
            return "tool_result: text content blocks must contain non-whitespace text"
        parts = cast("list[dict[str, Any]]", inner if isinstance(inner, list) else [])
        kinds = {part.get("type") for part in parts}
        if "tool_reference" in kinds and kinds != {"tool_reference"}:
            # Seen from Anthropic on 14/10/2026, in the real proof.
            return "Tool definitions/code execution functions cannot be mixed with other content."
        for part in parts:
            problem = _block_problem(part, deferred)
            if problem is not None:
                return f"tool_result: {problem}"
    return None


def _unmarked(value: Any) -> Any:
    """A request's part without its cache points, which change nothing for a signature."""
    if isinstance(value, dict):
        return {
            key: _unmarked(item)
            for key, item in cast("dict[str, Any]", value).items()
            if key != "cache_control"
        }
    if isinstance(value, list):
        return [_unmarked(item) for item in cast("list[Any]", value)]
    return value


def _anthropic_signature(body: dict[str, Any], position: int) -> str:
    """The fake's thinking signature: bound, as Anthropic's are, to the model, the system prompt,
    the loaded tools and every message before the reply, byte for byte. Cache points, deferred
    tools and reasoning blocks themselves are left out: moving the first, adding the second and
    removing the third are allowed."""
    system = body.get("system")
    messages = cast("list[dict[str, Any]]", body.get("messages") or [])[:position]
    material = {
        "model": body.get("model"),
        "system": _unmarked(system),
        "tools": [
            _unmarked(tool)
            for tool in cast("list[dict[str, Any]]", body.get("tools") or [])
            if not tool.get("defer_loading")
        ],
        "messages": [
            {
                **message,
                "content": [
                    _unmarked(block)
                    for block in cast("list[Any]", message.get("content") or [])
                    if not (
                        isinstance(block, dict)
                        and block.get("type") in ("thinking", "redacted_thinking")
                    )
                ]
                if isinstance(message.get("content"), list)
                else message.get("content"),
            }
            for message in messages
        ],
    }
    text = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return "sig-" + hashlib.sha256(text.encode()).hexdigest()[:32]


def _server_cleared(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The prompt as Anthropic's server clears it (`clear_tool_uses_20250919`): past the trigger,
    the oldest results become a placeholder, the latest `keep` and the excluded tools' stay, and
    nothing is cleared unless at least `clear_at_least` tokens would go. The request itself is
    stateless, so the same history clears the same way each time, and the cache holds after."""
    edits = cast("dict[str, Any]", body.get("context_management") or {}).get("edits") or []
    rule = next(
        (cast("dict[str, Any]", e) for e in edits if e.get("type") == "clear_tool_uses_20250919"),
        None,
    )
    if rule is None:
        return body, None
    total = len(json.dumps(_unmarked(body), sort_keys=True)) // 4 + 1
    trigger = int(cast("dict[str, Any]", rule.get("trigger") or {}).get("value", 100_000))
    if total <= trigger:
        return body, None
    keep = int(cast("dict[str, Any]", rule.get("keep") or {}).get("value", 3))
    least = int(cast("dict[str, Any]", rule.get("clear_at_least") or {}).get("value", 0))
    excluded = set(cast("list[str]", rule.get("exclude_tools") or []))
    cleared = json.loads(json.dumps(body))
    messages = cast("list[dict[str, Any]]", cleared["messages"])
    names = {
        str(block.get("id")): str(block.get("name"))
        for message in messages
        for block in cast("list[dict[str, Any]]", message.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    }
    results = [
        block
        for message in messages
        for block in cast("list[dict[str, Any]]", message.get("content") or [])
        if isinstance(block, dict)
        and block.get("type") == "tool_result"
        and names.get(str(block.get("tool_use_id"))) not in excluded
    ]
    oldest = results[: max(0, len(results) - keep)]
    tokens = sum(len(json.dumps(block.get("content"))) // 4 for block in oldest)
    if not oldest or tokens < least:
        return body, None
    for block in oldest:
        block["content"] = [{"type": "text", "text": "[cleared to save context]"}]
    applied = {
        "type": "clear_tool_uses_20250919",
        "cleared_tool_uses": len(oldest),
        "cleared_input_tokens": tokens,
    }
    return cleared, applied


def _marked_prefixes(body: dict[str, Any]) -> list[tuple[str, int, str | None]]:
    """Every block boundary's prefix (tools, then system, then messages, deferred tools left out,
    as Anthropic keeps them out of the prefix): its hash, its size in tokens, and the TTL of the
    cache point there, `None` where there is none. Anthropic looks for a cached prefix at the
    boundaries before each point too, so a point that moved still reads what an earlier one
    wrote."""
    walked: list[Any] = []
    found: list[tuple[str, int, str | None]] = []

    def visit(item: dict[str, Any]) -> None:
        walked.append(_unmarked(item))
        marker = item.get("cache_control")
        ttl = (
            str(cast("dict[str, Any]", marker).get("ttl") or "5m")
            if isinstance(marker, dict)
            else None
        )
        text = json.dumps(walked, sort_keys=True)
        found.append((hashlib.sha256(text.encode()).hexdigest(), len(text) // 4 + 1, ttl))

    for tool in cast("list[dict[str, Any]]", body.get("tools") or []):
        if not tool.get("defer_loading"):
            visit(tool)
    system = body.get("system")
    for block in cast("list[dict[str, Any]]", system if isinstance(system, list) else []):
        visit(block)
    for message in cast("list[dict[str, Any]]", body.get("messages") or []):
        content = message.get("content")
        walked.append(message.get("role"))
        for block in cast("list[dict[str, Any]]", content if isinstance(content, list) else []):
            visit(block)
    return found


def _system_text(body: dict[str, Any]) -> str:
    """A request's system prompt: Chat Completions' first system message, or the Responses API's
    instructions or first system input, or Anthropic's system blocks."""
    system = body.get("system")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        blocks = cast("list[dict[str, Any]]", system)
        return "".join(str(block.get("text", "")) for block in blocks)
    if isinstance(body.get("instructions"), str):
        return cast("str", body["instructions"])
    messages = cast("list[dict[str, Any]]", body.get("messages") or body.get("input") or [])
    first = messages[0] if messages else {}
    if first.get("role") not in ("system", "developer"):
        return ""
    content = first.get("content")
    if isinstance(content, list):
        parts = cast("list[dict[str, Any]]", content)
        return "".join(str(part.get("text", "")) for part in parts)
    return str(content or "")


def reply_from_json(item: dict[str, Any]) -> FakeReply | FakeError:
    """A reply as a test outside Python writes it: `{"error": {...}}` is a `FakeError`; otherwise
    the fields of `FakeReply`, with `calls` as `{"name", "arguments"}` objects."""
    if "error" in item:
        return FakeError(**cast("dict[str, Any]", item["error"]))
    fields = dict(item)
    calls = cast("list[dict[str, Any]]", fields.pop("calls", []))
    return FakeReply(**fields, calls=[FakeCall(**call) for call in calls])


def main() -> None:
    """Serve until stopped, for a test in another process (the desktop's end-to-end tests)."""
    parser = argparse.ArgumentParser(description="The fake model server, until stopped.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--key", default=None, help="the only API key it takes; any, without it")
    args = parser.parse_args()
    server = FakeModelServer(key=args.key, host=args.host, port=args.port)
    with server:
        sys.stdout.write(f"FAKE_MODEL_READY port={server.port}\n")
        sys.stdout.flush()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
