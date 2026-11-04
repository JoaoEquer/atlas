"""What the plugin keeps: each executor's other accounts, the order they take over in at a plan's
limit, the account each folder's new sessions start on, and each session's route. One JSON file in
the plugin's data folder, read once and written whole, under a lock: Atlas asks often and from any
thread, and the file is small."""

import json
import os
import re
import secrets
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

#: The file in the plugin's data folder.
BOOK = "accounts.json"
#: What an update of Atlas leaves in the plugin's data folder (step 22): the accounts Atlas kept
#: itself until then, in this file's shape, adopted when the plugin first starts.
FROM_ATLAS = "from-atlas.json"
#: A nickname, which names the account's folder too (`~/.claude-company-b`): letters, digits, `-`
#: and `_`, at most 40.
NICKNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
#: How many fresh ids an account is tried under before giving up.
ID_TRIES = 20


class BookError(Exception):
    """What the user asked cannot be kept; the message says why."""


@dataclass(frozen=True, slots=True)
class Kept:
    """An account beside the harness's own login."""

    id: str
    nickname: str
    folder: str
    added_at: str


@dataclass(frozen=True, slots=True)
class Order:
    """How an executor's accounts take over at a plan's limit: on, compacting first, in the
    accounts' own order until the user sets one (the main account as `""`)."""

    enabled: bool = True
    compact: bool = True
    order: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Stop:
    """One stop of a route: an executor on one of its accounts (`""`, its own login), and the model
    and effort the session goes on with there; `None`, the ones it ran on."""

    executor: str
    account: str = ""
    model: str | None = field(default=None, compare=False)
    effort: str | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class Route:
    """A session's own stops at a plan's limit, in order, kept by its conversation."""

    stops: tuple[Stop, ...] = ()
    enabled: bool = True
    compact: bool = True


def folder_key(folder: str | Path) -> str:
    """A folder as one key however its path was written (`C:` or `c:`, a slash or the other)."""
    return os.path.normcase(os.path.normpath(str(folder)))


class Book:
    """The plugin's file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None
        #: When the file was last read or written here: one written by another load of the
        #: plugin (its screen's calls run in one, what Atlas asks in another) is read again.
        self._stamp: tuple[int, int] | None = None

    # -- reading ----------------------------------------------------------------------------------

    def accounts(self, executor: str) -> list[Kept]:
        """`executor`'s other accounts, the oldest first."""
        with self._lock:
            return [_kept(entry) for entry in entries(self._executor(executor).get("accounts"))]

    def account(self, executor: str, account_id: str) -> Kept | None:
        return next((kept for kept in self.accounts(executor) if kept.id == account_id), None)

    def order(self, executor: str) -> Order:
        with self._lock:
            found = self._executor(executor).get("hand_over")
        if not isinstance(found, dict):
            return Order()
        entry = cast("dict[str, Any]", found)
        given = entry.get("order", [])
        order = cast("list[object]", given) if isinstance(given, list) else []
        return Order(
            enabled=bool(entry.get("enabled", True)),
            compact=bool(entry.get("compact", True)),
            order=tuple(account for account in order if isinstance(account, str)),
        )

    def folders(self, executor: str) -> dict[str, str]:
        """Each folder of `executor` that starts on another account, and that account's id."""
        with self._lock:
            found = mapping(self._executor(executor).get("folders"))
        return {key: str(value) for key, value in found.items()}

    def folder_account(self, executor: str, folder: str | Path) -> str:
        wanted = folder_key(folder)
        ids = {kept.id for kept in self.accounts(executor)}
        return next(
            (found for kept, found in self.folders(executor).items()
             if folder_key(kept) == wanted and found in ids),
            "",
        )  # fmt: skip

    def route(self, conversation: str) -> Route | None:
        with self._lock:
            routes = mapping(self._load().get("routes"))
        if not isinstance(routes.get(conversation), dict):
            return None
        entry = mapping(routes[conversation])
        stops = [_stop(stop) for stop in entries(entry.get("stops"))]
        return Route(
            tuple(stop for stop in stops if stop.executor),
            enabled=bool(entry.get("enabled", True)),
            compact=bool(entry.get("compact", True)),
        )

    # -- writing ----------------------------------------------------------------------------------

    def add(self, executor: str, nickname: str, folder: str) -> Kept:
        """Keep a new account under a fresh id (8 hexadecimal characters); its nickname must be
        new for the executor, whatever its case."""
        if not NICKNAME.fullmatch(nickname):
            raise BookError(
                "Give the account a nickname of letters, digits, - or _, at most 40, such as "
                "company-b: it names its folder too."
            )
        with self._lock:
            entry = self._executor(executor, make=True)
            listed: list[dict[str, Any]] = entry.setdefault("accounts", [])
            if any(str(a.get("nickname", "")).lower() == nickname.lower() for a in listed):
                raise BookError(f"An account is named {nickname!r} already. Choose another name.")
            taken = {str(a.get("id")) for a in listed}
            for _ in range(ID_TRIES):
                made = secrets.token_hex(4)
                if made not in taken:
                    break
            else:  # pragma: no cover - 4 billion ids
                raise BookError(f"No free id for an account after {ID_TRIES} tries.")
            added = Kept(made, nickname, folder, datetime.now(UTC).isoformat(timespec="seconds"))
            listed.append(_entry(added))
            self._save()
        return added

    def put(self, executor: str, account: Kept) -> None:
        """Keep `account` as it is, under its own id: what the update copies from Atlas's
        tables."""
        with self._lock:
            listed: list[dict[str, Any]] = self._executor(executor, make=True).setdefault(
                "accounts", []
            )
            listed[:] = [a for a in listed if a.get("id") != account.id]
            listed.append(_entry(account))
            self._save()

    def remove(self, executor: str, account_id: str) -> bool:
        """Forget an account, and the folders that started on it."""
        with self._lock:
            entry = self._executor(executor, make=True)
            listed: list[dict[str, Any]] = entry.setdefault("accounts", [])
            kept = [a for a in listed if a.get("id") != account_id]
            if len(kept) == len(listed):
                return False
            entry["accounts"] = kept
            folders: dict[str, str] = entry.get("folders", {})
            entry["folders"] = {k: v for k, v in folders.items() if v != account_id}
            self._save()
        return True

    def set_order(self, executor: str, order: Order) -> None:
        with self._lock:
            self._executor(executor, make=True)["hand_over"] = {
                "enabled": order.enabled,
                "compact": order.compact,
                "order": list(dict.fromkeys(order.order)),
            }
            self._save()

    def set_folder(self, executor: str, folder: str, account_id: str) -> None:
        """The account `folder`'s new sessions start on; empty, the main one. One entry per
        folder, however its path was written."""
        with self._lock:
            entry = self._executor(executor, make=True)
            folders: dict[str, str] = entry.setdefault("folders", {})
            for kept in [k for k in folders if folder_key(k) == folder_key(folder)]:
                del folders[kept]
            if account_id:
                folders[folder] = account_id
            self._save()

    def set_route(self, conversation: str, route: Route) -> None:
        with self._lock:
            routes: dict[str, Any] = self._load().setdefault("routes", {})
            routes[conversation] = {
                "enabled": route.enabled,
                "compact": route.compact,
                "stops": [
                    {
                        "executor": stop.executor,
                        "account": stop.account,
                        **({"model": stop.model} if stop.model else {}),
                        **({"effort": stop.effort} if stop.effort else {}),
                    }
                    for stop in dict.fromkeys(route.stops)
                ],
            }
            self._save()

    def carry_route(self, source: str, conversation: str) -> None:
        found = self.route(source)
        if found is not None and source != conversation:
            self.set_route(conversation, replace(found))

    # -- the file ---------------------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if self._data is not None and self._stamp != self._mtime():
            self._data = None
        if self._data is None:
            try:
                read: object = json.loads(self._path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                read = {}
            except (OSError, ValueError) as error:
                # Never written over: a file that cannot be read is the user's to look at.
                raise BookError(f"{self._path} cannot be read: {error}") from error
            self._data = mapping(read)
            self._stamp = self._mtime()
        return self._data

    def _mtime(self) -> tuple[int, int] | None:
        """When the file was written and its size: two writes within one tick of a coarse
        clock still differ in what they hold."""
        try:
            found = self._path.stat()
        except OSError:
            return None
        return found.st_mtime_ns, found.st_size

    def _executor(self, executor: str, *, make: bool = False) -> dict[str, Any]:
        executors: dict[str, Any] = self._load().setdefault("executors", {})
        found = executors.get(executor)
        if isinstance(found, dict):
            return cast("dict[str, Any]", found)
        if not make:
            return {}
        made: dict[str, Any] = {}
        executors[executor] = made
        return made

    def _save(self) -> None:
        """Write what was changed in memory just now, read a moment before under the lock."""
        data = self._data if self._data is not None else {}
        data["version"] = 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        part = self._path.with_suffix(".json.part")
        part.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        part.replace(self._path)
        self._data, self._stamp = data, self._mtime()

    @property
    def exists(self) -> bool:
        return self._path.exists()

    def adopt(self, source: Path) -> bool:
        """Start from what Atlas kept before this plugin (`FROM_ATLAS`), once: only while this
        plugin has no file of its own. The source stays, for the user to look at."""
        with self._lock:
            if self._path.exists() or not source.exists():
                return False
            try:
                read = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            self._data = mapping(read)
            self._save()
        return True


def mapping(value: object) -> dict[str, Any]:
    """`value` when it is a JSON object, else an empty one."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def entries(value: object) -> list[dict[str, Any]]:
    """The JSON objects of `value` when it is a list, else none."""
    listed = cast("list[object]", value) if isinstance(value, list) else []
    return [mapping(entry) for entry in listed if isinstance(entry, dict)]  # pyright: ignore[reportUnknownArgumentType]


def _kept(entry: dict[str, Any]) -> Kept:
    return Kept(
        str(entry.get("id", "")),
        str(entry.get("nickname", "")),
        str(entry.get("folder", "")),
        str(entry.get("added_at", "")),
    )


def _entry(kept: Kept) -> dict[str, Any]:
    return {
        "id": kept.id,
        "nickname": kept.nickname,
        "folder": kept.folder,
        "added_at": kept.added_at,
    }


def _stop(entry: dict[str, Any]) -> Stop:
    model, effort = entry.get("model"), entry.get("effort")
    return Stop(
        str(entry.get("executor", "")),
        str(entry.get("account", "")),
        model=model if isinstance(model, str) and model else None,
        effort=effort if isinstance(effort, str) and effort else None,
    )
