"""The Telegram plugin against a fake Bot API over real HTTP: what it reads, what it sends, how it
cuts and writes a reply, its buttons, and Telegram's errors (401, 409, 429, an HTML refusal).

The host is a recorder: Atlas's side is proven in tests/server/test_channels.py.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from atlas.plugins import Inbound, InboundFile, Prompt
from fake_telegram import JPEG, TOKEN, FakeTelegram
from official_support import official_module

# The Telegram plugin Atlas makes (plugins/telegram), imported as Atlas imports it.
_bot, _channel, _text = (official_module("telegram", name) for name in ("bot", "channel", "text"))
Fatal = _bot.Fatal
NOT_READ_TEXT, TOO_BIG_TEXT, TelegramChannel = (
    _channel.NOT_READ_TEXT,
    _channel.TOO_BIG_TEXT,
    _channel.TelegramChannel,
)
MESSAGE_LIMIT, pieces, to_html, utf16_len = (
    _text.MESSAGE_LIMIT,
    _text.pieces,
    _text.to_html,
    _text.utf16_len,
)


class Host:
    def __init__(self) -> None:
        self.received: list[Inbound] = []
        self.chosen: list[tuple[str, int, str]] = []
        self.refusal: str | None = None
        self.failures: list[str] = []

    async def receive(self, message: Inbound) -> None:
        self.received.append(message)

    async def choose(self, prompt: str, choice: int, user: str) -> str | None:
        self.chosen.append((prompt, choice, user))
        return self.refusal

    def failed(self, reason: str) -> None:
        self.failures.append(reason)


@pytest.fixture
def telegram() -> Any:
    with FakeTelegram() as server:
        yield server


async def _until(check: Callable[[], bool], seconds: float = 5) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("It did not happen in time.")
        await asyncio.sleep(0.02)


def _running(
    telegram: FakeTelegram, host: Host, scenario: Callable[[Any], Awaitable[None]]
) -> None:
    async def main() -> None:
        channel = TelegramChannel(TOKEN, telegram.url, sleep=0.001)
        task = asyncio.create_task(channel.run(host))
        try:
            await scenario(channel)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())


# -- the text --------------------------------------------------------------------------------------


def test_markdown_becomes_telegram_s_html_and_the_rest_is_escaped() -> None:
    text = (
        "# Plan\n**Bold** and *italic* and ~~gone~~, `x < 1 & y`, [docs](https://e.com/a?b=1&c=2)\n"
        "- one\n- two\n```python\nif a < b:\n    print('<ok>')\n```\n2 * 3 * 4 and snake_case_name"
    )
    assert to_html(text) == (
        "<b>Plan</b>\n<b>Bold</b> and <i>italic</i> and <s>gone</s>, "
        "<code>x &lt; 1 &amp; y</code>, "
        '<a href="https://e.com/a?b=1&amp;c=2">docs</a>\n'
        "• one\n• two\n<pre>if a &lt; b:\n    print(&#x27;&lt;ok&gt;&#x27;)</pre>\n"
        "2 * 3 * 4 and snake_case_name"
    )


def test_a_long_reply_is_cut_at_paragraphs_never_inside_code_and_counted_in_utf16() -> None:
    assert pieces("short") == ["short"]
    paragraph = "word " * 300  # 1,500 characters
    found = pieces("\n\n".join([paragraph] * 6))
    assert len(found) == 3
    assert [piece.splitlines()[-1] for piece in found] == ["(1/3)", "(2/3)", "(3/3)"]
    assert all(utf16_len(piece) <= MESSAGE_LIMIT for piece in found)

    code = "```python\n" + "\n".join(f"line_{n} = {n}" for n in range(600)) + "\n```"
    cut = pieces(f"Before.\n\n{code}\n\nAfter.")
    assert all(utf16_len(piece) <= MESSAGE_LIMIT for piece in cut)
    for piece in cut:
        body = piece.rsplit("\n(", 1)[0]
        assert body.count("```") % 2 == 0  # each piece's code block closed
    assert cut[0].startswith("Before.")
    assert "After." in cut[-1]

    emoji = "🙂" * 2500  # 2,500 characters, 5,000 UTF-16 units
    parts = pieces(emoji)
    assert len(parts) == 2
    assert all(utf16_len(part) <= MESSAGE_LIMIT for part in parts)
    assert "".join(part.rsplit("\n(", 1)[0] for part in parts) == emoji


# -- the channel -----------------------------------------------------------------------------------


def test_it_starts_by_dropping_what_waited_and_hands_on_private_texts_only(
    telegram: FakeTelegram,
) -> None:
    host = Host()

    async def scenario(channel: Any) -> None:
        await _until(lambda: bool(telegram.called("getUpdates")))
        telegram.say(100, "hello there", name="Ana")
        telegram.say(200, "in a group", chat_type="group")
        telegram.sticker(100)
        await _until(lambda: len(host.received) == 1 and len(telegram.sent(100)) == 1)

    _running(telegram, host, scenario)
    assert telegram.called("deleteWebhook") == [{"drop_pending_updates": True}]
    assert host.received == [Inbound("100", "100", "Ana", "hello there")]
    assert telegram.sent(100) == [NOT_READ_TEXT]
    assert telegram.sent(200) == []


def test_files_are_downloaded_and_handed_on_with_their_caption(telegram: FakeTelegram) -> None:
    """Step 16g over SDK version 7: a photo (its largest size), a document with its name, a voice
    message; Atlas decides what the model reads of them."""
    host = Host()
    pdf = b"%PDF-1.7\n" + b"x" * 300

    async def scenario(channel: Any) -> None:
        await _until(lambda: bool(telegram.called("getUpdates")))
        telegram.send_photo(100, JPEG + b"the large one", caption="What is this?")
        telegram.send_file(100, "document", pdf, name="contrato.pdf")
        telegram.send_file(100, "voice", b"OggS voice", caption="")
        await _until(lambda: len(host.received) == 3)

    _running(telegram, host, scenario)
    photo, document, voice = host.received
    assert (photo.text, photo.files) == (
        "What is this?",
        (InboundFile("photo.jpg", JPEG + b"the large one"),),
    )
    assert [call["file_id"] for call in telegram.called("getFile")] == ["p-large", "f3", "f4"]
    assert (document.text, document.files) == ("", (InboundFile("contrato.pdf", pdf),))
    assert voice.files == (InboundFile("voice.ogg", b"OggS voice"),)
    assert telegram.sent(100) == []


def test_a_file_past_what_telegram_lets_a_bot_download_is_said_so(telegram: FakeTelegram) -> None:
    host = Host()

    async def scenario(channel: Any) -> None:
        await _until(lambda: bool(telegram.called("getUpdates")))
        telegram.send_file(100, "video", b"small", name="aula.mp4", size=30 * 1024 * 1024)
        telegram.send_file(100, "document", b"x", name="notas.pdf", caption="Read it", size=1)
        telegram.fail("getFile", 400, "Bad Request: file is too big")
        # The caption reaches Atlas after the notice is sent: wait for both.
        await _until(lambda: len(telegram.sent(100)) == 2 and bool(host.received))

    _running(telegram, host, scenario)
    assert telegram.sent(100) == [
        TOO_BIG_TEXT.format(name="aula.mp4"),
        TOO_BIG_TEXT.format(name="notas.pdf"),
    ]
    # Its caption still reaches Atlas, without the file.
    assert host.received == [Inbound("100", "100", "Ana", "Read it")]


def test_a_reply_goes_as_html_and_again_plain_when_telegram_cannot_read_it(
    telegram: FakeTelegram,
) -> None:
    host = Host()
    telegram.refuse_html = 1

    async def scenario(channel: Any) -> None:
        await channel.send("100", "**Done**: 2 < 3")
        await channel.send("100", "**Again**")

    _running(telegram, host, scenario)
    sent = telegram.called("sendMessage")
    assert [(s["text"], s.get("parse_mode")) for s in sent] == [
        ("<b>Done</b>: 2 &lt; 3", "HTML"),
        ("**Done**: 2 < 3", None),
        ("<b>Again</b>", "HTML"),
    ]


def test_a_flood_wait_is_waited_and_a_long_one_fails_the_send(telegram: FakeTelegram) -> None:
    host = Host()
    telegram.fail("sendMessage", 429, "Too Many Requests: retry after 2", retry_after=2)

    async def scenario(channel: Any) -> None:
        await channel.send("100", "first")
        telegram.fail("sendMessage", 429, "Too Many Requests: retry after 5000", retry_after=5000)
        with pytest.raises(Exception, match="429"):
            await channel.send("100", "second")

    _running(telegram, host, scenario)
    # The calls Telegram got: the first refused then waited and sent; the second refused, once.
    assert telegram.sent(100) == ["first", "first", "second"]


def test_buttons_carry_the_prompt_and_a_press_goes_to_atlas_which_may_refuse_it(
    telegram: FakeTelegram,
) -> None:
    host = Host()

    async def scenario(channel: Any) -> None:
        prompt = Prompt("srq-abc123", "Run `git reset`?", ("Allow once", "Deny"))
        await channel.ask("100", prompt)
        assert telegram.buttons(100) == [["p:srq-abc123:0", "p:srq-abc123:1"]]
        telegram.press(100, "p:srq-abc123:1")
        await _until(lambda: len(telegram.called("answerCallbackQuery")) == 1)
        host.refusal = "This no longer waits for an answer."
        telegram.press(300, "p:srq-abc123:0")
        telegram.press(100, "garbage")
        await _until(lambda: len(telegram.called("answerCallbackQuery")) == 3)
        await channel.settle("100", "srq-abc123", "You chose: Deny.")
        await channel.settle("100", "srq-unknown", "Nothing.")

    _running(telegram, host, scenario)
    assert host.chosen == [("srq-abc123", 1, "100"), ("srq-abc123", 0, "300")]
    assert [a["text"] for a in telegram.called("answerCallbackQuery")] == [
        "",
        "This no longer waits for an answer.",
        "This button is not Atlas's.",
    ]
    [edit] = telegram.called("editMessageText")
    assert edit["text"] == "Run <code>git reset</code>?\n\nYou chose: Deny."
    assert "reply_markup" not in edit


def test_a_free_question_says_a_typed_answer_works(telegram: FakeTelegram) -> None:
    host = Host()

    async def scenario(channel: Any) -> None:
        await channel.ask("100", Prompt("srq-q.0", "Which city?", ("Lisbon",), typed=True))

    _running(telegram, host, scenario)
    assert telegram.sent(100) == ["Which city?\n\nOr write your answer."]


def test_working_shows_typing_until_the_turn_ends(telegram: FakeTelegram) -> None:
    host = Host()

    async def scenario(channel: Any) -> None:
        await channel.working("100", True)
        await _until(lambda: bool(telegram.called("sendChatAction")))
        await channel.working("100", False)

    _running(telegram, host, scenario)
    assert telegram.called("sendChatAction")[0] == {"chat_id": "100", "action": "typing"}


def test_a_wrong_or_missing_token_ends_the_channel_saying_what_to_do(
    telegram: FakeTelegram,
) -> None:
    async def main() -> list[str]:
        said: list[str] = []
        for token in ("999:wrong", None):
            with pytest.raises(Fatal) as raised:
                await TelegramChannel(token, telegram.url).run(Host())
            said.append(str(raised.value))
        return said

    wrong, missing = asyncio.run(main())
    assert "refused the bot's token" in wrong
    assert "@BotFather" in missing


def test_another_poller_is_waited_out_then_named(telegram: FakeTelegram) -> None:
    for _ in range(6):
        telegram.fail("getUpdates", 409, "Conflict: terminated by other getUpdates request")

    async def main() -> str:
        with pytest.raises(Fatal) as raised:
            await TelegramChannel(TOKEN, telegram.url, sleep=0.001).run(Host())
        return str(raised.value)

    assert "Another program is reading this bot's messages" in asyncio.run(main())
    assert len(telegram.called("getUpdates")) == 6


def test_a_conflict_that_passes_and_a_flood_on_polling_are_ridden_out(
    telegram: FakeTelegram,
) -> None:
    host = Host()
    telegram.fail("getUpdates", 409, "Conflict")
    telegram.fail("getUpdates", 429, "Too Many Requests", retry_after=1)

    async def scenario(channel: Any) -> None:
        telegram.say(100, "still here")
        await _until(lambda: len(host.received) == 1)

    _running(telegram, host, scenario)
    assert host.received[0].text == "still here"
