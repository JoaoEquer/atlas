"""A real Atlas server on a temporary home, its model the fake model server."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.models import CallLedger
from atlas.secrets import Vault
from fake_model import FakeModelServer
from model_support import Connect
from server_support import AtlasServer, Serve


@pytest.fixture
def work(tmp_path: Path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture
def serve(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, work: Path
) -> Iterator[Serve]:
    """Start a server; it stops when the test ends."""
    started: list[AtlasServer] = []
    database = Database(home.database_file)
    migrate(database, SCHEMAS)

    def serve(
        *,
        providers: tuple[str, ...] = (),
        adjust: Callable[[Settings], Settings] = lambda settings: settings,
        **gateway: Any,
    ) -> AtlasServer:
        """`providers`: more built-in providers pointed at the fake, so a key a test stores for
        one of them (a plugin's) never sends a side call, a title, to the real provider.
        `adjust`: the settings the server starts with, changed."""
        chosen = ModelSettings(provider="openrouter")
        models = connect(
            fake, "openrouter", *providers, settings=chosen, ledger=CallLedger(database)
        )
        settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
        server = AtlasServer(home, adjust(settings), models, vault, **gateway).__enter__()
        started.append(server)
        return server

    yield serve
    for server in started:
        server.__exit__()
