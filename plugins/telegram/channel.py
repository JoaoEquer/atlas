"""The Telegram channel: a bot's private chats, long-polled.

What arrives goes to Atlas as it is (`ChannelHost.receive`): who may talk, the commands and the
conversations are Atlas's. Only private chats with people are read; groups and other bots are left
for later. A photo, a document, a video, an audio file or a voice message is downloaded (`getFile`,
20 MB at most for a bot) and handed over with its caption (SDK version 7): what the model reads of
it is Atlas's, as for a file dropped in the app. A sticker, a location or a poll is told it is not
read. A file Atlas sends (SDK version 22) goes as a voice message when it is an Ogg voice note,
as audio or as a document otherwise.

**Buttons** carry `p:<prompt>:<choice>` (Telegram allows 64 bytes).
Atlas decides whether the person who pressed may answer and whether the question still waits; a
press it refuses gets Atlas's words back in Telegram's small notice, and a question settled
elsewhere has its buttons replaced by what happened.

**Starting** reads the token, checks it with `getMe` and drops what was sent while Atlas was off:
an old message answered hours late reads as a mistake.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import ChannelHost, Inbound, InboundFile, Prompt

from .bot import DOWNLOAD_BYTES, Bot, Fatal, TelegramError
from .text import pieces, to_html

#: What Telegram plays as a voice message: Opus in an Ogg file.
VOICE_TYPES = frozenset({"audio/ogg", "audio/opus"})
#: Telegram's typing notice lasts 5 seconds; it is sent again before it ends.
TYPING_SECONDS = 4.5
#: A message with nothing Atlas reads: a sticker, a location, a contact, a poll.
NOT_READ_TEXT = "Atlas reads text, photos, documents, audio and video here."
#: Telegram lets a bot download 20 MB at most.
TOO_BIG_TEXT = "{name} is over the 20 MB Telegram lets a bot download. Send a smaller file."
NOT_FETCHED_TEXT = "{name} could not be downloaded from Telegram. Send it again."
#: What each kind of Telegram message carries a file in, and the name one sent without a name
#: gets. A photo comes in several sizes, the largest last; Telegram sends it as a JPEG.
_FILES = (
    ("document", "file"),
    ("video", "video.mp4"),
    ("audio", "audio.mp3"),
    ("voice", "voice.ogg"),
    ("video_note", "video-note.mp4"),
    ("animation", "animation.mp4"),
)
#: A choice's label on its button, cut.
BUTTON_CHARS = 60


@dataclass(frozen=True, slots=True)
class _Shown:
    chat: str
    message_id: int
    text: str


class TelegramChannel:
    """One bot, talking to the people Atlas lets in."""

    def __init__(
        self,
        token: str | None,
        api_url: str,
        *,
        logger: logging.Logger | None = None,
        sleep: float = 1.0,
    ) -> None:
        """`logger` is the plugin's own (`PluginContext.logger`)."""
        self._token = token
        self._logger = logger or logging.getLogger(__name__)
        self._api_url = api_url
        self._sleep = sleep
        self._bot: Bot | None = None
        self._ready = asyncio.Event()
        self._typing: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._prompts: dict[str, _Shown] = {}

    async def run(self, host: ChannelHost) -> None:
        if not self._token:
            raise Fatal(
                "The bot's token is not stored. In Telegram, talk to @BotFather, send /newbot, "
                "and store the token it gives on the plugin's screen."
            )
        bot = Bot(self._api_url, self._token, sleep=self._sleep)
        self._bot = bot
        try:
            try:
                me = await bot.call("getMe")
                await bot.call("deleteWebhook", drop_pending_updates=True)
            except TelegramError as error:
                if error.status in (401, 404):
                    raise Fatal(
                        "Telegram refused the bot's token: it is wrong or was revoked. Get it "
                        "again from @BotFather and store it on the plugin's screen."
                    ) from error
                raise
            self._logger.info("The Telegram bot is listening.", extra={"bot": me.get("username")})
            self._ready.set()
            offset: int | None = None
            while True:
                for update in await bot.updates(offset):
                    offset = int(update["update_id"]) + 1
                    await self._handle(host, update)
        finally:
            self._ready.clear()
            for task in self._typing.values():
                task.cancel()
            await bot.close()

    async def _handle(self, host: ChannelHost, update: dict[str, Any]) -> None:
        try:
            if "callback_query" in update:
                await self._pressed(host, cast("dict[str, Any]", update["callback_query"]))
            elif "message" in update:
                await self._message(host, cast("dict[str, Any]", update["message"]))
        except (Fatal, asyncio.CancelledError):
            raise
        except Exception:  # one update's failure must not stop the bot, logged
            self._logger.exception("A Telegram update could not be handled.")

    async def _message(self, host: ChannelHost, message: dict[str, Any]) -> None:
        chat = cast("dict[str, Any]", message.get("chat") or {})
        sender = cast("dict[str, Any]", message.get("from") or {})
        if chat.get("type") != "private" or sender.get("is_bot"):
            return
        chat_id = str(chat["id"])
        said = message.get("text") or message.get("caption")
        text = said if isinstance(said, str) else ""
        sent = _sent_file(message)
        if not text.strip() and sent is None:
            await self.send(chat_id, NOT_READ_TEXT)
            return
        files: tuple[InboundFile, ...] = ()
        if sent is not None:
            file = await self._fetched(chat_id, *sent)
            if file is None and not text.strip():
                return
            files = () if file is None else (file,)
        name = " ".join(
            str(part) for part in (sender.get("first_name"), sender.get("last_name")) if part
        ) or str(sender.get("username") or sender.get("id"))
        await host.receive(Inbound(chat_id, str(sender["id"]), name, text, files))

    async def _fetched(self, chat: str, file_id: str, name: str, size: int) -> InboundFile | None:
        """A file's bytes, downloaded; `None`, said in the chat, when Telegram will not give it."""
        bot = self._bot
        if bot is None:  # pragma: no cover - only `run` handles updates
            return None
        if size > DOWNLOAD_BYTES:
            await self.send(chat, TOO_BIG_TEXT.format(name=name))
            return None
        try:
            data = await bot.download(file_id)
        except TelegramError as error:
            if "too big" in error.description.lower():
                await self.send(chat, TOO_BIG_TEXT.format(name=name))
            else:
                self._logger.warning("A file could not be downloaded: %s", error)
                await self.send(chat, NOT_FETCHED_TEXT.format(name=name))
            return None
        return InboundFile(name, data)

    async def _pressed(self, host: ChannelHost, query: dict[str, Any]) -> None:
        bot = self._bot
        if bot is None:  # pragma: no cover - only `run` handles updates
            return
        data = str(query.get("data") or "")
        sender = cast("dict[str, Any]", query.get("from") or {})
        said: str | None = "This button is not Atlas's."
        kind, _, rest = data.partition(":")
        prompt, _, choice = rest.rpartition(":")
        if kind == "p" and prompt and choice.isdigit():
            said = await host.choose(prompt, int(choice), str(sender.get("id")))
        with contextlib.suppress(TelegramError):
            await bot.send("answerCallbackQuery", callback_query_id=query["id"], text=said or "")

    # -- what Atlas says --------------------------------------------------------------------------

    async def send(self, chat: str, text: str) -> None:
        bot = await self._ready_bot()
        async with self._locks.setdefault(chat, asyncio.Lock()):
            for piece in pieces(text):
                if piece:
                    await self._say(bot, chat, piece)

    async def send_file(self, chat: str, path: Path, media_type: str) -> None:
        """A file Atlas sends (SDK version 22): an Ogg voice note as Telegram's voice message,
        which plays in the chat, other audio as audio, anything else as a document."""
        bot = await self._ready_bot()
        data = await asyncio.to_thread(path.read_bytes)
        if media_type in VOICE_TYPES:
            method, part = "sendVoice", "voice"
        elif media_type.startswith("audio/"):
            method, part = "sendAudio", "audio"
        else:
            method, part = "sendDocument", "document"
        async with self._locks.setdefault(chat, asyncio.Lock()):
            await bot.upload(method, part, path.name, data, media_type, chat_id=chat)

    async def _say(self, bot: Bot, chat: str, piece: str, **extra: Any) -> dict[str, Any]:
        try:
            sent = await bot.send(
                "sendMessage", chat_id=chat, text=to_html(piece), parse_mode="HTML", **extra
            )
        except TelegramError as error:
            if error.status != 400 or "parse" not in error.description.lower():
                raise
            # Telegram could not read the HTML: the same words, plain.
            sent = await bot.send("sendMessage", chat_id=chat, text=piece, **extra)
        return cast("dict[str, Any]", sent)

    async def working(self, chat: str, busy: bool) -> None:
        running = self._typing.pop(chat, None)
        if running is not None:
            running.cancel()
        if busy:
            self._typing[chat] = asyncio.create_task(self._type(chat))

    async def _type(self, chat: str) -> None:
        bot = await self._ready_bot()
        while True:
            with contextlib.suppress(TelegramError):
                await bot.call("sendChatAction", chat_id=chat, action="typing")
            await asyncio.sleep(TYPING_SECONDS)

    async def ask(self, chat: str, prompt: Prompt) -> None:
        bot = await self._ready_bot()
        buttons = [
            [{"text": label[:BUTTON_CHARS], "callback_data": f"p:{prompt.id}:{index}"}]
            for index, label in enumerate(prompt.choices)
        ]
        text = prompt.text + ("\n\nOr write your answer." if prompt.typed else "")
        async with self._locks.setdefault(chat, asyncio.Lock()):
            sent = await self._say(
                bot, chat, text, reply_markup={"inline_keyboard": buttons} if buttons else None
            )
        self._prompts[prompt.id] = _Shown(chat, int(sent["message_id"]), text)

    async def settle(self, chat: str, prompt: str, said: str) -> None:
        shown = self._prompts.pop(prompt, None)
        bot = self._bot
        if shown is None or bot is None:
            return
        with contextlib.suppress(TelegramError):
            await bot.send(
                "editMessageText",
                chat_id=shown.chat,
                message_id=shown.message_id,
                text=to_html(f"{shown.text}\n\n{said}"),
                parse_mode="HTML",
            )

    async def _ready_bot(self) -> Bot:
        await self._ready.wait()
        bot = self._bot
        if bot is None:  # pragma: no cover - ready only while running
            raise Fatal("The Telegram channel is not running.")
        return bot


def _sent_file(message: dict[str, Any]) -> tuple[str, str, int] | None:
    """The file a message carries, as `(file_id, name, size)`; `None` for one with none. A
    document keeps its name; a photo, the largest of its sizes, is a JPEG Telegram names nothing."""
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        largest = cast("dict[str, Any]", photos[-1])
        return str(largest["file_id"]), "photo.jpg", int(largest.get("file_size") or 0)
    for kind, default in _FILES:
        found = message.get(kind)
        if isinstance(found, dict):
            file = cast("dict[str, Any]", found)
            name = str(file.get("file_name") or default)
            return str(file["file_id"]), name, int(file.get("file_size") or 0)
    return None
