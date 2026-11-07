"""Prove the Gemini CLI plugin (step 16p) against the user's own Gemini CLI and its real model.
Spends some of the account's Gemini quota, and no Atlas credit: Atlas's model takes no part.

    uv run python scripts/prove_gemini.py [--vault | --login]

The plugin Atlas ships drives the `gemini` this computer has (or `--program`, a JSON list), with
its own login, in a temporary project; this script plays Atlas, the host: it keeps what the Gemini
CLI says and answers what it asks. `--vault` hands it the plugin's secret, the `GEMINI_API_KEY` in
Atlas's vault, as Atlas does (a personal Google account's only way in); `--login` logs it in with
Google first, on the browser, through the plugin (ACP's `authenticate`), which Google allows a
company's account only.

1. A permission: it is asked to write a file in the project, which it asks for in the mode that
   asks; the request reaches the host, which allows it, and the file is there.
2. A refusal: the same in a second file, refused with a reason; the file is not there, the reason
   goes in as a prompt, and the turn still ends once.
3. Its automatic mode: the same in a third file with `auto` on: `yolo`, and nothing reaches the
   host; or, in a folder it does not trust, it says so and keeps asking (said which).
4. A steer: a slow command, and a steer sent while it runs, which it takes: a later message says
   the word the steer asked for.
5. Stop: a long command, stopped while it runs; the process ends.
6. The session: the first step's session is listed in the project's folder, read back with its
   prompt and its tool, and goes on: it still knows the file it wrote.
7. Its plan's limits, read from Google with its login, after the turns refreshed its token.

Exit code 0 only when every step did what it should.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from atlas.home import Redactor, load_settings, open_home
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
from atlas.secrets import Vault

#: The longest a step may take: it works with a real model.
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
        say(f"    asked: {permission.summary[:140]}")
        return Verdict(True) if self._approve is None else await self._approve(permission)

    def failed(self, reason: str) -> None:
        self.failures.append(reason)

    def said(self) -> str:
        return "\n".join(event.text for event in self.events if isinstance(event, Said))

    def session(self) -> str:
        return next((e.session for e in self.events if isinstance(e, Started)), "")

    def finished(self) -> list[Finished]:
        return [e for e in self.events if isinstance(e, Finished)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the Gemini CLI plugin end to end.")
    parser.add_argument("--program", default="", help="the gemini program, a JSON list")
    parser.add_argument("--steps", default="1,2,3,4,5,6,7", help="the steps to run, such as 1,6")
    parser.add_argument("--login", action="store_true", help="log in with Google first")
    parser.add_argument("--vault", action="store_true", help="the API key in Atlas's vault")
    parser.add_argument("--model", default=None, help="a model of its own, such as a lighter one")
    arguments = parser.parse_args()
    steps = {int(step) for step in arguments.steps.split(",") if step.strip()}
    key = vault_key() if arguments.vault else None
    return asyncio.run(
        prove(arguments.program, steps, login=arguments.login, api_key=key, model=arguments.model)
    )


def vault_key() -> str | None:
    """The plugin's secret as Atlas reads it: `GEMINI_API_KEY` in the active home's vault."""
    settings = load_settings(os.environ).settings
    home = open_home(settings.home)
    return Vault(home.secrets_folder, settings.vault, Redactor()).get("GEMINI_API_KEY")


def held(folder: Path) -> list[str]:
    """The processes still running in `folder`, which a stop should have ended with the rest."""
    probe = folder / "probe"
    try:
        probe.mkdir()
        probe.rmdir()
        folder.rename(folder.with_name(folder.name + "-moved"))
        folder.with_name(folder.name + "-moved").rename(folder)
    except OSError as error:
        return [str(error)]
    return []


def executor(program: str, api_key: str | None = None) -> Executor:
    """The plugin's executor, as Atlas makes it."""
    package = import_plugin(OFFICIAL_FOLDER / "gemini-cli", "gemini-cli")
    made: Executor = package.GeminiCli(
        package.program(program), logger=logging.getLogger("prove_gemini"), api_key=api_key
    )
    return made


def write_task(name: str) -> str:
    return (
        f"Create the file {name} in the current folder containing the single word proof, with "
        "your file writing tool. Then say only: written."
    )


async def prove(
    program: str,
    steps: set[int],
    *,
    login: bool = False,
    api_key: str | None = None,
    model: str | None = None,
) -> int:
    gemini = executor(program, api_key)
    if login:
        say("0. Logging in with Google, on the browser")
        account = await gemini.log_in("")
        say(f"   {account}")
    found = await gemini.detect()
    say(f"Gemini CLI {found.version}, supported {found.supported}, account {found.account}")
    if not found.supported or found.account is None or not found.account.logged_in:
        say(f"Cannot prove: {found.problem or 'not logged in (run with --vault or --login)'}")
        return 1
    results: dict[str, bool] = {}
    # A command it started that outlives it would hold the folder: step 5 says so, not the cleanup.
    with tempfile.TemporaryDirectory(
        prefix="atlas-prove-gemini-", ignore_cleanup_errors=True
    ) as temporary:
        project = Path(temporary) / "project"
        project.mkdir()
        first = ""

        async def turn(host: Host, prompt: str, spec: ExecutorSpec | None = None) -> Host:
            instance = await gemini.start(spec or ExecutorSpec(project, model=model), host)
            try:
                await asyncio.wait_for(instance.prompt(prompt), STEP_SECONDS)
            finally:
                await instance.stop()
            return host

        async def until_called(host: Host) -> None:
            for _ in range(1200):
                if any(isinstance(event, Called) for event in host.events):
                    return
                await asyncio.sleep(0.1)

        if 1 in steps:
            say("1. A permission")
            host = await turn(Host(), write_task("allowed.txt"))
            first = host.session()
            results["permission"] = bool(host.permissions) and (project / "allowed.txt").is_file()
        if 2 in steps:
            say("2. A refusal")

            async def refuse(permission: Permission) -> Verdict:
                return Verdict(False, "Do not create that file.")

            host = await turn(Host(refuse), write_task("refused.txt"))
            results["refusal"] = (
                bool(host.permissions)
                and not (project / "refused.txt").exists()
                and len(host.finished()) == 1
            )
        if 3 in steps:
            say("3. Its automatic mode")
            host = await turn(
                Host(), write_task("auto.txt"), ExecutorSpec(project, auto=True, model=model)
            )
            untrusted = "does not trust this folder" in host.said()
            yolo = not host.permissions and (project / "auto.txt").is_file()
            outcome = "yolo: nothing asked" if yolo else "untrusted: it kept asking"
            say(f"   {outcome if yolo or untrusted else 'neither'}")
            results["auto"] = yolo or (untrusted and bool(host.permissions))
        if 4 in steps:
            say("4. A steer")
            host = Host()
            instance = await gemini.start(ExecutorSpec(project, auto=True, model=model), host)
            try:
                running = asyncio.create_task(
                    instance.prompt(
                        'Run the command `python -c "import time; time.sleep(20)"` and wait for '
                        "it to finish, then say: done."
                    )
                )
                await until_called(host)
                if not running.done():
                    await instance.steer(f"Also end your answer with the word {WORD}.")
                await asyncio.wait_for(running, STEP_SECONDS)
            finally:
                await instance.stop()
            results["steer"] = WORD in host.said().upper() and len(host.finished()) == 1
        if 5 in steps:
            say("5. Stop")
            host = Host()
            instance = await gemini.start(ExecutorSpec(project, auto=True, model=model), host)
            running = asyncio.create_task(
                instance.prompt('Run the command `python -c "import time; time.sleep(120)"`.')
            )
            await until_called(host)
            await asyncio.wait_for(instance.stop(), 30)
            await asyncio.gather(running, return_exceptions=True)
            agent: Any = getattr(instance, "agent", None)
            await asyncio.sleep(2)
            left = held(project)
            say(f"   the process ended: {agent is not None and agent.ended.is_set()}; left: {left}")
            results["stop"] = agent is not None and agent.ended.is_set() and not left
        if 6 in steps and first:
            say("6. The session")
            listed = await gemini.sessions(folder=project)
            transcript = await gemini.transcript(first, project)
            kinds = {entry.kind for entry in transcript.entries}
            host = await turn(
                Host(),
                "What is the name of the file you created earlier? Answer with the name only.",
                ExecutorSpec(project, resume=first, model=model),
            )
            results["session"] = (
                first in [info.id for info in listed]
                and {"text", "tool"} <= kinds
                and host.session() == first
                and "allowed.txt" in host.said()
            )
        if 7 in steps:
            say("7. The plan's limits")
            usage = await gemini.usage()
            say(f"   plan {usage.plan!r}, {len(usage.windows)} windows, {usage.unavailable!r}")
            for window in usage.windows:
                say(f"   {window.title}: {window.used}% used, resets {window.resets_at}")
            # An API key has no plan limits to read: the panel says so.
            results["usage"] = bool(usage.windows) or "API key" in usage.unavailable
    say(json.dumps(results, indent=2))
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
