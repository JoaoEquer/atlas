"""Codex, an executor for Atlas's workbench: the plugin's `register` (step 16o).

It imports from Atlas only `atlas.plugins.sdk`, as every plugin must; the rest is its own.
"""

import json
from typing import cast

from atlas.plugins.sdk import ExecutorError, PluginContext

from .executor import Codex, installed_cli
from .protocol import CODEX


def register(ctx: PluginContext) -> None:
    def make() -> Codex:
        named = ctx.setting("program")
        return Codex(program(named), logger=ctx.logger, found=not named.strip())

    ctx.register_executor(CODEX, make)


def program(setting: str) -> list[str] | None:
    """The program the `program` setting names, a JSON list; empty, the `codex` this computer
    has."""
    if not setting.strip():
        return installed_cli()
    try:
        named: object = json.loads(setting)
    except ValueError as error:
        raise ExecutorError(f"The program setting is not a JSON list: {error}") from error
    if not isinstance(named, list) or not named:
        raise ExecutorError("The program setting is a JSON list of the program and its arguments.")
    return [str(part) for part in cast("list[object]", named)]
