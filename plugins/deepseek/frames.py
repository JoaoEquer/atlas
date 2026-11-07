"""A `dsh` log's Zstandard frames, decoded: `zstandard`, the plugin's extra (`deepseek`), imported
only when a transcript is read, since Python 3.13 reads no Zstandard."""

import contextlib
import io

import zstandard

#: How much of a log is decoded at a time.
CHUNK = 1 << 16


def decoded(raw: bytes) -> str:
    """A log's frames, one after another; a log being written ends in a frame not yet whole, and
    what came before it is read."""
    reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw), read_across_frames=True)
    chunks: list[bytes] = []
    with contextlib.closing(reader), contextlib.suppress(zstandard.ZstdError):
        while chunk := reader.read(CHUNK):
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", "replace")
