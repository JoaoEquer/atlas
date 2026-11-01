"""The logins of the programs Atlas reaches GitHub and Google Workspace through, read for the
first run's Connections, and GitHub's started from there."""

import asyncio
import logging
import os
from asyncio.subprocess import Process
from collections.abc import Callable, Mapping

from atlas.logins.errors import LoginsError
from atlas.logins.model import (
    GH_LOGIN,
    GH_STATUS,
    GWS_STATUS,
    LOGIN_SECONDS,
    PROGRAMS,
    STATUS_SECONDS,
    DeviceCode,
    ProgramLogin,
    Service,
    parse_gh_status,
    parse_gws_status,
)
from atlas.logins.store import finish, program, read, start_login, stop

_logger = logging.getLogger(__name__)


class ProgramLogins:
    """Reads `gh`'s and `gws`'s logins, and keeps one `gh` login in the browser at a time,
    calling `changed` when it ends (the server tells its clients)."""

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self.changed: Callable[[], None] = lambda: None
        self._environment = environment
        self._github: tuple[DeviceCode, Process, asyncio.Task[None]] | None = None

    def _env(self) -> Mapping[str, str]:
        return os.environ if self._environment is None else self._environment

    async def read(self) -> list[ProgramLogin]:
        return list(await asyncio.gather(self._read("github"), self._read("google")))

    async def _read(self, service: Service) -> ProgramLogin:
        name = PROGRAMS[service]
        argv = program(name, self._env())
        if argv is None:
            return ProgramLogin(service, name, installed=False, logged_in=False)
        try:
            if service == "github":
                account = parse_gh_status(
                    await read([*argv, *GH_STATUS], self._env(), STATUS_SECONDS)
                )
                return ProgramLogin(service, name, True, account is not None, account or "")
            said = await read([*argv, *GWS_STATUS], self._env(), STATUS_SECONDS)
        except LoginsError:
            return ProgramLogin(service, name, installed=False, logged_in=False)
        logged_in, account = parse_gws_status(said)
        return ProgramLogin(service, name, True, logged_in, account)

    async def log_in_github(self) -> DeviceCode:
        """Start `gh`'s login in the browser and give its code, or the code of the one
        still waiting."""
        if self._github is not None and not self._github[2].done():
            return self._github[0]
        argv = program(PROGRAMS["github"], self._env())
        if argv is None:
            raise LoginsError(
                "GitHub's program, gh, is not on this machine: install it from cli.github.com, "
                "or run Atlas's installer again."
            )
        process, code = await start_login([*argv, *GH_LOGIN], self._env())

        async def wait() -> None:
            ended = await finish(process, LOGIN_SECONDS)
            _logger.info("GitHub's login in the browser ended.", extra={"ok": ended})
            if self._github is not None:  # not stopped by `close`
                self.changed()

        self._github = (code, process, asyncio.create_task(wait()))
        return code

    async def close(self) -> None:
        """Stop a login still waiting for the browser."""
        if self._github is not None:
            # Ended rather than cancelled: the wait reads its output to the end, and the pipes
            # close (a read cancelled on Windows leaves its pipe behind).
            _, process, task = self._github
            self._github = None
            await stop(process)
            await asyncio.gather(task, return_exceptions=True)
