"""Accounts, the harnesses on the user's other accounts: the plugin's `register` (step 22).

Decided with the user on 03/11/2026 (ROADMAP.md, O futuro dos executores): Atlas runs each harness
on the user's own login, one account; several accounts, and a session moving between them at a
plan's limit, are this plugin's, for whoever wants them.

- **An account is the harness's own folder for it** (Claude Code's `~/.claude-company-b`), with its
  own login, settings and sessions, under a nickname the user gives; Atlas runs it as one executor
  more (`claude-code@<id>`), made by the harness's plugin on that folder. Nothing here keeps a
  credential, and removing an account leaves its folder as it is.
- **At a plan's limit** Atlas asks where the session goes (`Accounts.next_stop`): by the session's
  own route when it has one, else to the next of its executor's accounts in the user's order,
  passing over one not logged in or at its own limit. Carrying it there, in a copy on the same
  harness or in a new session with a handoff packet on another, is Atlas's.
- **A folder may start its new sessions on an account** of its own choosing.
- **The screen** (`desktop/plugin.js`) puts the account's pill on the session and on the executor's
  card, and its calls below keep what the user chooses.

It imports from Atlas only `atlas.plugins.sdk`, as every plugin must; the rest is its own.
"""

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from atlas.plugins.sdk import (
    Executor,
    HandOver,
    NextStop,
    OtherAccount,
    PluginContext,
    WorkbenchHost,
)

from .book import BOOK, FROM_ATLAS, Book, BookError, Order, Route, Stop, entries, folder_key

#: What joins an executor's name and an account's id into the name of its executor on that account.
MARK = "@"
#: What the screen calls the harness's own login.
MAIN = "main"


def account_name(executor: str, account: str) -> str:
    """The name of `executor` on `account`; empty `account`, its own login."""
    return f"{executor}{MARK}{account}" if account else executor


def split_account(name: str) -> tuple[str, str]:
    executor, _, account = name.partition(MARK)
    return executor, account


def in_order(ids: Sequence[str], order: Sequence[str]) -> list[str]:
    """`ids` in the user's `order`, those it does not name after, in their own order."""
    ranked = {account: at for at, account in enumerate(order)}
    return sorted(ids, key=lambda account: ranked.get(account, len(ranked) + ids.index(account)))


class PluginAccounts:
    """What the plugin answers Atlas (`atlas.plugins.sdk.Accounts`)."""

    def __init__(self, book: Book, workbench: Callable[[], WorkbenchHost]) -> None:
        self.book = book
        self._workbench = workbench

    def accounts(self, executor: str) -> Sequence[OtherAccount]:
        return [
            OtherAccount(kept.id, kept.nickname, Path(kept.folder))
            for kept in self.book.accounts(executor)
        ]

    def folder_account(self, executor: str, folder: Path) -> str:
        return self.book.folder_account(executor, folder)

    def hand_over(self, executor: str) -> HandOver:
        order = self.book.order(executor)
        return HandOver(order.enabled, order.compact, order.order)

    async def next_stop(self, name: str, conversation: str) -> NextStop | str | None:
        """By the session's route, when it has one on; else the next account of its executor in
        the user's order, round to the start; `None` when its executor has no other account or
        the user turned the hand-over off."""
        host = self._workbench()
        route = await asyncio.to_thread(self.book.route, conversation)
        if route is not None and route.enabled and route.stops:
            base, current = split_account(name)
            stops = route.stops
            here = Stop(base, current)
            at = stops.index(here) if here in stops else -1
            for stop in (*stops[at + 1 :], *stops[: max(at, 0)]):
                on = account_name(stop.executor, stop.account)
                if on != name and await host.available(on):
                    return NextStop(on, stop.model, stop.effort, compact=route.compact, route=True)
            return "No destination in the session's route can go on now."
        base, current = split_account(name)
        kept = await asyncio.to_thread(self.book.accounts, base)
        order = await asyncio.to_thread(self.book.order, base)
        if not kept or not order.enabled:
            return None
        ids = in_order(["", *(account.id for account in kept)], order.order)
        at = ids.index(current) if current in ids else -1
        for account in (*ids[at + 1 :], *ids[: max(at, 0)]):
            on = account_name(base, account)
            if on != name and await host.available(on):
                return NextStop(on, compact=order.compact)
        return "No other account can go on now."

    def keep_route(self, conversation: str, executors: Sequence[str]) -> None:
        stops = tuple(Stop(*split_account(name)) for name in executors)
        self.book.set_route(conversation, Route(stops))

    def carry_route(self, source: str, conversation: str) -> None:
        self.book.carry_route(source, conversation)


class Calls:
    """What the plugin's screen calls (`atlas.calls`): the accounts, their order, a folder's
    account and a session's route, each answered as JSON."""

    def __init__(self, accounts: PluginAccounts, workbench: Callable[[], WorkbenchHost]) -> None:
        self._accounts = accounts
        self._book = accounts.book
        self._workbench = workbench

    def _executor(self, name: str) -> Executor:
        found = self._workbench().executor(name)
        if found is None:
            raise BookError(f"{name} is not installed on this computer.")
        return found

    async def list(self, params: dict[str, Any]) -> dict[str, Any]:
        """An executor's accounts, its own login first then the user's order, each with who it is
        logged in as; and whether and how they take over at a plan's limit."""
        executor = str(params.get("executor", ""))
        found = self._executor(executor)
        kept = await asyncio.to_thread(self._book.accounts, executor)
        order = await asyncio.to_thread(self._book.order, executor)
        folders = await asyncio.to_thread(self._book.folders, executor)
        rows: list[dict[str, Any]] = []
        for account_id, nickname, folder in (
            ("", "", str(found.account_folder())),
            *((k.id, k.nickname, k.folder) for k in kept),
        ):
            name = account_name(executor, account_id)
            on = self._workbench().executor(name)
            login, logged = await _logged(on)
            rows.append(
                {
                    "account_id": account_id,
                    "name": name,
                    "nickname": nickname,
                    "folder": folder,
                    "login": login,
                    "logged_in": logged,
                    "limited": self._workbench().limited(name),
                    "folders": [f for f, a in folders.items() if a == account_id and account_id],
                }
            )
        listed = in_order([row["account_id"] for row in rows], order.order)
        rows.sort(key=lambda row: listed.index(row["account_id"]))
        return {
            "executor": executor,
            "title": found.title,
            "accounts": rows,
            "hand_over": {"enabled": order.enabled, "compact": order.compact},
        }

    async def add(self, params: dict[str, Any]) -> dict[str, Any]:
        """Keep another account under a nickname, in the harness's folder for it beside its own,
        made empty or taken as it is (the user may have used it in a terminal). Not logged in yet:
        the harness's own login does that, from the account's row."""
        executor = str(params.get("executor", ""))
        nickname = str(params.get("nickname", "")).strip()
        found = self._executor(executor)
        if not found.capabilities().account_folders:
            raise BookError(f"{found.title} runs on no account but its own.")
        kept = await asyncio.to_thread(self._book.accounts, executor)
        try:
            folder = found.new_account_folder(nickname)
        except Exception as error:  # a plugin's own code: its refusal is told
            raise BookError(f"{found.title} has no folder for {nickname!r}: {error}") from error
        taken = {folder_key(found.account_folder()), *(folder_key(k.folder) for k in kept)}
        if folder_key(folder) in taken:
            raise BookError(f"The folder {folder} is another account's already.")
        added = await asyncio.to_thread(self._book.add, executor, nickname, str(folder))
        try:
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        except OSError as error:
            await asyncio.to_thread(self._book.remove, executor, added.id)
            raise BookError(f"The folder {folder} could not be made: {error}") from error
        self._workbench().accounts_changed(executor)
        return {"account_id": added.id, "name": account_name(executor, added.id)}

    async def remove(self, params: dict[str, Any]) -> dict[str, Any]:
        """Forget an account: its nickname, and the folders that started on it, which start on
        the harness's own login again. Its folder stays, with its login and sessions. Never while
        a session runs on it."""
        executor = str(params.get("executor", ""))
        account_id = str(params.get("account", ""))
        found = await asyncio.to_thread(self._book.account, executor, account_id)
        if found is None:
            raise BookError(f"{executor} has no account {account_id!r}.")
        if self._workbench().running(account_name(executor, account_id)):
            raise BookError(
                f"A session runs on {found.nickname}. Stop it, or switch it to another account, "
                "then remove the account."
            )
        await asyncio.to_thread(self._book.remove, executor, account_id)
        self._workbench().accounts_changed(executor)
        return {}

    async def set_hand_over(self, params: dict[str, Any]) -> dict[str, Any]:
        """The order an executor's accounts take over in (its own login as `""`), whether they
        do, and whether a session compacts in its copy first."""
        executor = str(params.get("executor", ""))
        order = [str(a) for a in params.get("order", []) if isinstance(a, str)]
        known = {"", *(k.id for k in await asyncio.to_thread(self._book.accounts, executor))}
        unknown = [a for a in order if a not in known]
        if unknown:
            raise BookError(f"{executor} has no account {unknown[0]!r}.")
        chosen = Order(
            enabled=bool(params.get("enabled", True)),
            compact=bool(params.get("compact", True)),
            order=tuple(order),
        )
        await asyncio.to_thread(self._book.set_order, executor, chosen)
        return await self.list({"executor": executor})

    async def set_folder(self, params: dict[str, Any]) -> dict[str, Any]:
        """The account a folder's new sessions start on; empty, the harness's own login."""
        executor = str(params.get("executor", ""))
        folder = str(params.get("folder", ""))
        account_id = str(params.get("account", ""))
        if account_id and await asyncio.to_thread(self._book.account, executor, account_id) is None:
            raise BookError(f"{executor} has no account {account_id!r}.")
        await asyncio.to_thread(self._book.set_folder, executor, folder, account_id)
        self._workbench().accounts_changed(executor)
        return {}

    async def route(self, params: dict[str, Any]) -> dict[str, Any]:
        """A session's route, each stop as the screen draws it; empty until the user sets one."""
        conversation = str(params.get("conversation", ""))
        found = await asyncio.to_thread(self._book.route, conversation)
        if found is None:
            return {"stops": [], "enabled": True, "compact": True}
        return {
            "stops": [self._stop(stop) for stop in found.stops],
            "enabled": found.enabled,
            "compact": found.compact,
        }

    async def set_route(self, params: dict[str, Any]) -> dict[str, Any]:
        """A session's stops at a plan's limit, in order, each an executor installed here and one
        of its accounts, with the model and effort it goes on with there."""
        conversation = str(params.get("conversation", ""))
        stops: list[Stop] = []
        for given in entries(params.get("stops")):
            executor = str(given.get("executor", ""))
            account = str(given.get("account", ""))
            if split_account(executor)[1]:
                raise BookError(f"{executor!r} names an account: give it apart.")
            found = self._executor(executor)
            if account and await asyncio.to_thread(self._book.account, executor, account) is None:
                raise BookError(f"{found.title} has no account {account!r}.")
            model, effort = given.get("model"), given.get("effort")
            stops.append(
                Stop(
                    executor,
                    account,
                    model=model if isinstance(model, str) and model else None,
                    effort=effort if isinstance(effort, str) and effort else None,
                )
            )
        route = Route(
            tuple(stops),
            enabled=bool(params.get("enabled", True)),
            compact=bool(params.get("compact", True)),
        )
        await asyncio.to_thread(self._book.set_route, conversation, route)
        return await self.route({"conversation": conversation})

    def _stop(self, stop: Stop) -> dict[str, Any]:
        name = account_name(stop.executor, stop.account)
        host = self._workbench()
        found = host.executor(name)
        kept = self._book.account(stop.executor, stop.account) if stop.account else None
        return {
            "executor": stop.executor,
            "account": stop.account,
            "name": name,
            "title": stop.executor if found is None else found.title,
            "nickname": "" if kept is None else kept.nickname,
            "installed": found is not None,
            "model": stop.model,
            "effort": stop.effort,
            "limited": host.limited(name),
        }


async def _logged(executor: Executor | None) -> tuple[str, bool]:
    """Who `executor` is logged in as, read with no effect on it; nobody when it cannot say."""
    if executor is None:
        return "", False
    try:
        found = await executor.detect()
    except Exception:  # noqa: BLE001 - a plugin's own code: an account that cannot say is not logged in
        return "", False
    account = found.account
    return ("", False) if account is None else (account.login, account.logged_in)


def register(ctx: PluginContext) -> None:
    book = Book(ctx.data_folder / BOOK)
    if book.adopt(ctx.data_folder / FROM_ATLAS):
        ctx.logger.info("The accounts Atlas kept before this plugin were adopted.")

    def workbench() -> WorkbenchHost:
        return ctx.workbench

    accounts = PluginAccounts(book, workbench)
    ctx.register_accounts(accounts)
    calls = Calls(accounts, workbench)
    for name in ("list", "add", "remove", "set_hand_over", "set_folder", "route", "set_route"):
        ctx.register_call(name, getattr(calls, name))
