"""Prove the Codex plugin (step 16o) against the user's own Codex and its real model. Spends some
of the Codex plan's usage, and no Atlas credit: Atlas's model takes no part.

    uv run python scripts/prove_codex.py

The plugin Atlas ships drives the `codex` this computer has (or `--program`, a JSON list), with its
own login, in a temporary project; this script plays Atlas, the host: it keeps what Codex says and
answers what it asks.

1. A permission: Codex is asked to write a file outside the project, which its sandbox refuses
   until the user allows it; the request reaches the host, which allows it, and the file is there.
2. A refusal: the same in a second file, refused with a reason; the file is not there, and the
   turn still ends.
3. Its own reviewer: the same in a third file with `auto` on; nothing reaches the host.
4. A steer: a slow command, and a steer sent while it runs, which Codex takes: a later message
   says the word the steer asked for.
5. Stop: a long command, stopped while it runs; the process ends.
6. The session: the first step's session is listed in the project's folder, read back with its
   prompt and its tool, and goes on: Codex still knows the file it wrote.

Exit code 0 only when every step did what it should.
"""

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from atlas.plugins import (
    OFFICIAL_FOLDER,
    Answers,
    Called,
    Executor,
    ExecutorEvent,
    ExecutorSpec,
    Finished,
    Permission,
    Question,
    Said,
    Started,
    Verdict,
)
from atlas.plugins.store import import_plugin

#: The longest a step may take: Codex works with a real model.
STEP_SECONDS = 300.0
#: The word step 4's steer asks for.
WORD = "BANANA"


def say(text: str) -> None:
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


class Host:
    """Atlas, as the instance sees it."""

    def __init__(self, approve: Callable[[Permission], Awaitable[Verdict]] | None = None) -> None:
        self.events: list[ExecutorEvent] = []
        self.permissions: list[Permission] = []
        self.failures: list[str] = []
        self._approve = approve

    def event(self, event: ExecutorEvent) -> None:
        self.events.append(event)
        say(f"    {type(event).__name__}: {str(event)[:160]}")

    async def ask(self, questions: Sequence[Question]) -> Answers:
        return None

    async def approve(self, permission: Permission) -> Verdict:
        self.permissions.append(permission)
        say(f"    asked: {permission.title}: {permission.summary[:120]}")
        return Verdict(True) if self._approve is None else await self._approve(permission)

    def failed(self, reason: str) -> None:
        self.failures.append(reason)

    def said(self) -> str:
        return "\n".join(event.text for event in self.events if isinstance(event, Said))

    def session(self) -> str:
        return next((e.session for e in self.events if isinstance(e, Started)), "")

    def finished(self) -> Finished | None:
        return next((e for e in reversed(self.events) if isinstance(e, Finished)), None)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the Codex plugin end to end.")
    parser.add_argument("--program", default="", help="the codex program, a JSON list")
    parser.add_argument("--steps", default="1,2,3,4,5,6", help="the steps to run, such as 1,6")
    arguments = parser.parse_args()
    steps = {int(step) for step in arguments.steps.split(",") if step.strip()}
    return asyncio.run(prove(arguments.program, steps))


def executor(program: str) -> Executor:
    """The plugin's executor, as Atlas makes it."""
    package = import_plugin(OFFICIAL_FOLDER / "codex", "codex")
    cli = package.program(program)
    made: Executor = package.Codex(cli, logger=logging.getLogger("prove_codex"))
    return made


def write_task(path: Path) -> str:
    return (
        f"Create the file {path} containing the single word proof. It is outside this project, "
        "so ask for the permission you need. Then say only: written."
    )


async def prove(program: str, steps: set[int]) -> int:
    codex = executor(program)
    found = await codex.detect()
    say(f"Codex {found.version}, supported {found.supported}, account {found.account}")
    if not found.supported:
        say(f"Cannot prove: {found.problem}")
        return 1
    results: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="atlas-prove-codex-") as temporary:
        work = Path(temporary)
        project = work / "project"
        project.mkdir()
        outside = work / "outside"
        outside.mkdir()
        first = ""

        async def turn(host: Host, prompt: str, spec: ExecutorSpec | None = None) -> Host:
            instance = await codex.start(spec or ExecutorSpec(project), host)
            try:
                await asyncio.wait_for(instance.prompt(prompt), STEP_SECONDS)
            finally:
                await instance.stop()
            return host

        if 1 in steps:
            say("1. A permission")
            target = outside / "allowed.txt"
            host = await turn(Host(), write_task(target))
            first = host.session()
            results["permission"] = bool(host.permissions) and target.is_file()
        if 2 in steps:
            say("2. A refusal")
            target = outside / "refused.txt"

            async def refuse(permission: Permission) -> Verdict:
                return Verdict(False, "Do not write outside the project.")

            host = await turn(Host(refuse), write_task(target))
            finished = host.finished()
            results["refusal"] = (
                bool(host.permissions) and not target.exists() and finished is not None
            )
        if 3 in steps:
            say("3. Its own reviewer")
            target = outside / "reviewed.txt"
            host = await turn(Host(), write_task(target), ExecutorSpec(project, auto=True))
            results["reviewer"] = not host.permissions and host.finished() is not None
        if 4 in steps:
            say("4. A steer")
            host = Host()
            instance = await codex.start(ExecutorSpec(project), host)
            try:
                prompt = asyncio.create_task(
                    instance.prompt(
                        'Run the command `python -c "import time; time.sleep(15)"` and wait for '
                        "it to finish, then say: done."
                    )
                )
                for _ in range(600):
                    if any(isinstance(event, Called) for event in host.events):
                        break
                    await asyncio.sleep(0.1)
                await instance.steer(f"Also end your answer with the word {WORD}.")
                await asyncio.wait_for(prompt, STEP_SECONDS)
            finally:
                await instance.stop()
            results["steer"] = WORD in host.said().upper()
        if 5 in steps:
            say("5. Stop")
            host = Host()
            instance = await codex.start(ExecutorSpec(project), host)
            prompt = asyncio.create_task(
                instance.prompt('Run the command `python -c "import time; time.sleep(120)"`.')
            )
            for _ in range(600):
                if any(isinstance(event, Called) for event in host.events):
                    break
                await asyncio.sleep(0.1)
            await asyncio.wait_for(instance.stop(), 20)
            prompt.cancel()
            await asyncio.gather(prompt, return_exceptions=True)
            server: Any = getattr(instance, "server", None)
            results["stop"] = server is not None and server.ended.is_set()
        if 6 in steps and first:
            say("6. The session")
            listed = await codex.sessions(folder=project)
            transcript = await codex.transcript(first, project)
            kinds = {entry.kind for entry in transcript.entries}
            host = await turn(
                Host(),
                "What is the name of the file you created earlier? Answer with the name only.",
                ExecutorSpec(project, resume=first),
            )
            results["session"] = (
                first in [info.id for info in listed]
                and {"text", "tool"} <= kinds
                and host.session() == first
                and "allowed.txt" in host.said()
            )
    say(json.dumps(results, indent=2))
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
