"""DeepSeek's harness (`dsh`), an executor for Atlas's workbench: its `register` (step 16q).

It imports from Atlas only the SDK, as every plugin must: `atlas.plugins.sdk`, and the ACP client
the SDK has since version 18 (`atlas.plugins.acp`); the rest is its own.
"""

import json
from typing import cast

from atlas.plugins.sdk import ExecutorError, PluginContext

from .executor import DeepSeek, installed_cli
from .protocol import DEEPSEEK, KEY_SECRET


def register(ctx: PluginContext) -> None:
    def make() -> DeepSeek:
        named = ctx.setting("program")
        return DeepSeek(
            program(named),
            found=not named.strip(),
            logger=ctx.logger,
            data=ctx.data_folder,
            api_key=ctx.secret(KEY_SECRET),
            session_logs=ctx.setting("session_logs").strip().lower() == "on",
        )

    ctx.register_executor(DEEPSEEK, make)


def program(setting: str) -> list[str] | None:
    """The program the `program` setting names, a JSON list; empty, the `dsh` this computer
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
