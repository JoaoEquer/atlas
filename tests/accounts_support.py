"""The accounts plugin Atlas ships (step 22), on a temporary data folder, put on an assistant as
the server puts it: the tests of the workbench and the delegations reach the other accounts
through it, its calls being what the plugin's screen calls."""

import importlib
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from atlas.assistant import Assistant
from atlas.assistant.model import AccountView
from atlas.assistant.plugin_host import AssistantPluginHost
from atlas.plugins import ExecutorError
from atlas.plugins.store import OFFICIAL_FOLDER, import_plugin

FOLDER = OFFICIAL_FOLDER / "accounts"


def accounts_module(name: str = "") -> ModuleType:
    """The plugin's package, or one of its modules (`book`), imported once."""
    package = "atlas_plugin_accounts"
    if package not in sys.modules:
        import_plugin(FOLDER, "accounts")
    return importlib.import_module(f"{package}.{name}" if name else package)


def Stop(  # noqa: N802 - it stands for the plugin's `Stop`, named as a class
    executor: str, account: str = "", *, model: str | None = None, effort: str | None = None
) -> dict[str, Any]:
    """A stop of a route, as the plugin's screen sends it."""
    return {"executor": executor, "account": account, "model": model, "effort": effort}


class AccountsPlugin:
    """The plugin on `assistant`, keeping its file in `folder`; each method as the old workbench
    one was, an `ExecutorError` for what the plugin refuses."""

    def __init__(
        self, assistant: Assistant, folder: Path, changed: list[str] | None = None
    ) -> None:
        module = accounts_module()
        self.folder = folder
        self.book = module.Book(folder / "accounts.json")
        told = changed if changed is not None else []
        host = AssistantPluginHost(assistant, told.append).workbench()
        self.accounts = module.PluginAccounts(self.book, lambda: host)
        self.calls = module.Calls(self.accounts, lambda: host)
        self._assistant = assistant
        assistant.set_accounts(self.accounts)

    async def _call(self, name: str, params: dict[str, Any]) -> Any:
        try:
            return await getattr(self.calls, name)(params)
        except accounts_module("book").BookError as error:
            raise ExecutorError(str(error)) from error

    async def add_account(self, executor: str, nickname: str) -> AccountView:
        made = await self._call("add", {"executor": executor, "nickname": nickname})
        listed = await self._assistant.workbench.accounts(executor)
        return next(a for a in listed.accounts if a.account_id == made["account_id"])

    async def remove_account(self, executor: str, account: str) -> None:
        await self._call("remove", {"executor": executor, "account": account})

    async def set_folder_account(self, executor: str, folder: str, account: str) -> None:
        await self._call("set_folder", {"executor": executor, "folder": folder, "account": account})

    async def set_failover(
        self, executor: str, order: Sequence[str], *, enabled: bool, compact: bool
    ) -> Any:
        await self._call(
            "set_hand_over",
            {"executor": executor, "order": list(order), "enabled": enabled, "compact": compact},
        )
        return await self._assistant.workbench.accounts(executor)

    async def set_route(
        self,
        instance_id: str,
        stops: Sequence[dict[str, Any]],
        *,
        enabled: bool,
        compact: bool = True,
    ) -> SimpleNamespace:
        conversation = self._assistant.workbench.get(instance_id).transcript.id
        found = await self._call(
            "set_route",
            {
                "conversation": conversation,
                "stops": list(stops),
                "enabled": enabled,
                "compact": compact,
            },
        )
        return _route(found)

    async def route(self, instance_id: str) -> SimpleNamespace:
        conversation = self._assistant.workbench.get(instance_id).transcript.id
        return _route(await self._call("route", {"conversation": conversation}))


def _route(found: dict[str, Any]) -> SimpleNamespace:
    stops = tuple(
        SimpleNamespace(**{**stop, "account_id": stop["account"]}) for stop in found["stops"]
    )
    return SimpleNamespace(stops=stops, enabled=found["enabled"], compact=found["compact"])
