"""The models' Markdown as Telegram shows it: cut to its limit, then written as Telegram's HTML.

**HTML, not MarkdownV2**: HTML escapes three characters, where MarkdownV2 escapes eighteen and
refuses a message for one it missed. When Telegram still refuses the HTML, the piece goes again as
plain text.

**Cut before it is written**, so no tag is split: the Markdown is cut at paragraphs, never inside a
code block (a block longer than a message is closed and opened again), with Telegram's 4096
counted in UTF-16 units as Telegram counts them (an emoji counts two). The pieces are kept under
3,800 so the counter appended, ` (1/2)`, has room.
"""

import html
import re

#: Telegram's limit for a message's text.
MESSAGE_LIMIT = 4096
#: A piece's most, leaving room for the counter.
PIECE_LIMIT = 3800

_FENCE = re.compile(r"^\s*```")
_CODE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_STRIKE = re.compile(r"~~(.+?)~~")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*]\s+", re.MULTILINE)


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def pieces(text: str, limit: int = PIECE_LIMIT) -> list[str]:
    """`text` cut into pieces of at most `limit` UTF-16 units, at blank lines when it can, never
    inside a code block; each piece after the first of several gets its counter."""
    blocks = _blocks(text.strip())
    found: list[str] = []
    current = ""
    for block in blocks:
        joined = f"{current}\n\n{block}" if current else block
        if utf16_len(joined) <= limit:
            current = joined
            continue
        if current:
            found.append(current)
        *whole, current = _hard(block, limit)
        found += whole
    if current:
        found.append(current)
    if len(found) > 1:
        found = [f"{piece}\n({index}/{len(found)})" for index, piece in enumerate(found, start=1)]
    return found or [""]


def _blocks(text: str) -> list[str]:
    """Paragraphs, a code block counting as one."""
    blocks: list[str] = []
    lines: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
        if not fenced and not line.strip():
            if lines:
                blocks.append("\n".join(lines))
                lines = []
            continue
        lines.append(line)
    if lines:
        blocks.append("\n".join(lines))
    return blocks


def _hard(block: str, limit: int) -> list[str]:
    """A block longer than a piece, cut at lines (at characters for a line that long); a code
    block's pieces each closed and opened again."""
    fence = block.split("\n", 1)[0].strip() if _FENCE.match(block) else None
    room = limit - (0 if fence is None else utf16_len(fence) + 5)
    parts: list[str] = []
    current = ""
    lines = block.split("\n")
    if fence is not None:
        lines = lines[1:-1] if len(lines) > 1 and _FENCE.match(lines[-1]) else lines[1:]
    for line in lines:
        while utf16_len(line) > room:
            cut = _fit(line, room)
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:cut])
            line = line[cut:]
        joined = f"{current}\n{line}" if current else line
        if utf16_len(joined) > room:
            parts.append(current)
            current = line
        else:
            current = joined
    if current:
        parts.append(current)
    if fence is None:
        return parts
    return [f"{fence}\n{part}\n```" for part in parts]


def _fit(line: str, room: int) -> int:
    """How many characters of `line` fit in `room` UTF-16 units."""
    used = 0
    for index, char in enumerate(line):
        used += 2 if ord(char) > 0xFFFF else 1
        if used > room:
            return max(index, 1)
    return len(line)


def to_html(text: str) -> str:
    """A piece of Markdown as Telegram's HTML: code, bold, italic, strikethrough, links, headings
    as bold and bullets as •. Everything else is escaped."""
    kept: list[str] = []

    def keep(fragment: str) -> str:
        kept.append(fragment)
        return f"\x00{len(kept) - 1}\x00"

    text = _CODE_BLOCK.sub(lambda m: keep(f"<pre>{html.escape(m.group(1).rstrip())}</pre>"), text)
    text = _INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = _LINK.sub(
        lambda m: keep(f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>'), text
    )
    text = html.escape(text, quote=False)
    text = _HEADING.sub(r"<b>\1</b>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    text = _BULLET.sub(r"\1• ", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)
