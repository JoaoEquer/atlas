"""Turning a WAV file into an Ogg Opus voice note, the format Telegram plays as a voice message.

Gemini speaks in WAV; Opus needs an encoder no Python package brings without a native library, so
this uses ffmpeg when the computer has it. Without it, the WAV is sent as it is: an audio file,
which plays in the app and arrives on Telegram as a file rather than a voice message.
"""

import asyncio
import shutil
import sys

#: How long ffmpeg may take on one answer.
ENCODE_SECONDS = 60.0

# No console window flashing on Windows. Imported only there: the flag exists only there, and a
# check made on Linux would read a module-wide import as unused.
if sys.platform == "win32":
    from subprocess import CREATE_NO_WINDOW as NO_WINDOW
else:
    NO_WINDOW = 0


async def voice_note(wav: bytes) -> tuple[bytes, str]:
    """The audio and its file's suffix: `.ogg` when ffmpeg made it, `.wav` otherwise."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return wav, ".wav"
    try:
        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            *("-hide_banner", "-loglevel", "error", "-i", "pipe:0"),
            *("-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    except OSError:
        return wav, ".wav"
    try:
        made, _ = await asyncio.wait_for(process.communicate(wav), ENCODE_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return wav, ".wav"
    if process.returncode != 0 or not made.startswith(b"OggS"):
        return wav, ".wav"
    return made, ".ogg"
