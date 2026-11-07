"""The Gemini CLI, an executor for Atlas's workbench: the plugin's `register` (step 16p).

It imports from Atlas only the SDK, as every plugin must: `atlas.plugins.sdk`, and the ACP client
the SDK has since version 18 (`atlas.plugins.acp`); the rest is its own.
"""

import json
from typing import cast

from atlas.plugins.sdk import ExecutorError, PluginContext

from .executor import GeminiCli, installed_cli
from .protocol import GEMINI, KEY_SECRET


def register(ctx: PluginContext) -> None:
    def make() -> GeminiCli:
        named = ctx.setting("program")
        return GeminiCli(
            program(named),
            logger=ctx.logger,
            api_key=ctx.secret(KEY_SECRET),
            found=not named.strip(),
        )

    ctx.register_executor(GEMINI, make)


def program(setting: str) -> list[str] | None:
    """The program the `program` setting names, a JSON list; empty, the `gemini` this computer
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
