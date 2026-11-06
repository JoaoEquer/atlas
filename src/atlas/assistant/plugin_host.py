"""What Atlas lends a plugin's Python (SDK version 16, step 16l): the user's model and the
conversations.

- **The model, `PluginContext.llm`:** one completion on the model a conversation that picked
  none uses, its key from the vault and never seen by the plugin, no tools, no reasoning asked
  for, in the call ledger as the plugin's (`plugin:<name>`), so `atlas usage` shows what each
  plugin cost. A plugin cannot pin a provider or a model; that is left out until a plugin asks.
- **Structured answers:** the schema goes in the instructions, the reply is read as JSON (inside
  a code fence too) and checked against it when `jsonschema` is installed (the mcp extra brings
  it); one that does not fit is `parsed = None` with the text kept.
- **A notice, `PluginContext.notify`:** the text reaches the conversation as a background
  command's notice does, starting a turn there or waiting for the one running. The user allows
  it at consent (`atlas.notify`).
- **The memory's limits, `PluginContext.memory_limits`** (version 26): read from the home's
  stores at each call, never set by a plugin.
- **The workbench, `PluginContext.workbench`** (version 29), for the plugin that brings the
  harnesses' other accounts: the executors installed, whether one may take a session over, and
  the word that its accounts changed, which the server tells every screen.
"""

import importlib
import importlib.util
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from atlas.conversations import ConversationNotFoundError
from atlas.memory import Target
from atlas.models import AssistantMessage, ModelRequest, ModelsError, SystemMessage, UserMessage
from atlas.plugins import Executor, LlmError, LlmMessage, LlmResult, MemoryLimits, WorkbenchHost

if TYPE_CHECKING:
    from atlas.assistant.service import Assistant

#: The ledger's purpose of a plugin's calls: `plugin:<name>`.
PURPOSE = "plugin:"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_logger = logging.getLogger("atlas.assistant")


class AssistantPluginHost:
    """`atlas.plugins.PluginHost` over one Atlas's assistant."""

    def __init__(
        self,
        assistant: "Assistant",
        accounts_changed: Callable[[str], None] = lambda _executor: None,
    ) -> None:
        self._assistant = assistant
        self._accounts_changed = accounts_changed

    async def complete(
        self,
        plugin: str,
        prompt: str | Sequence[LlmMessage],
        *,
        system: str,
        max_tokens: int | None,
        json_schema: Mapping[str, Any] | None,
    ) -> LlmResult:
        instructions = system.strip()
        if json_schema is not None:
            asked = (
                "Answer with one JSON value that fits this JSON Schema, and nothing else:\n"
                + json.dumps(json_schema, ensure_ascii=False)
            )
            instructions = f"{instructions}\n\n{asked}" if instructions else asked
        said = [LlmMessage("user", prompt)] if isinstance(prompt, str) else list(prompt)
        if not said or said[-1].role != "user":
            raise LlmError("A completion ends with the user's message.")
        messages = [SystemMessage(instructions)] if instructions else []
        messages += [
            UserMessage(m.text) if m.role == "user" else AssistantMessage(m.text) for m in said
        ]
        request = ModelRequest(
            tuple(messages),
            purpose=f"{PURPOSE}{plugin}",
            scope=plugin,
            max_output_tokens=max_tokens,
            reasoning=False,
        )
        try:
            client = self._assistant.models.client()
            reply = await client.reply(request)
        except ModelsError as error:
            raise LlmError(str(error)) from error
        model = f"{client.provider.id}/{client.model}"
        if json_schema is None:
            return LlmResult(reply.text, None, model)
        return LlmResult(reply.text, _parsed(reply.text, json_schema), model)

    async def notify(self, plugin: str, conversation: str, text: str) -> bool:
        try:
            opened = self._assistant.conversation(conversation)
        except ConversationNotFoundError:
            return False
        opened.notice(f"[From the plugin {plugin}]\n{text.strip()}")
        _logger.info(
            "A plugin wrote to a conversation.",
            extra={"plugin": plugin, "conversation": conversation},
        )
        return True

    def memory_limits(self) -> MemoryLimits:
        limits = self._assistant.memory.limits
        return MemoryLimits(user=limits[Target.USER], memory=limits[Target.MEMORY])

    def workbench(self) -> WorkbenchHost:
        return _Workbench(self._assistant, self._accounts_changed)


class _Workbench:
    """`atlas.plugins.WorkbenchHost` over one Atlas's workbench."""

    def __init__(self, assistant: "Assistant", accounts_changed: Callable[[str], None]) -> None:
        self._assistant = assistant
        self._changed = accounts_changed

    def executor(self, name: str) -> Executor | None:
        return self._assistant.executor(name)

    def executors(self) -> Sequence[str]:
        return self._assistant.executors()

    async def available(self, name: str) -> bool:
        return await self._assistant.workbench.available(name)

    def limited(self, name: str) -> bool:
        return self._assistant.workbench.limited(name)

    def running(self, name: str) -> bool:
        return self._assistant.workbench.running(name)

    def accounts_changed(self, executor: str) -> None:
        self._changed(executor)


def _parsed(text: str, schema: Mapping[str, Any]) -> object:
    """The reply's JSON when it fits the schema; `None` otherwise (`LlmResult.parsed`)."""
    fenced = _FENCE.search(text)
    raw = fenced.group(1) if fenced else text
    try:
        value: Any = json.loads(raw.strip())
    except ValueError:
        return None
    if importlib.util.find_spec("jsonschema") is None:
        # Checked only when jsonschema is installed (it comes with the mcp extra).
        return value
    validator: Any = importlib.import_module("jsonschema").Draft202012Validator
    try:
        fits = bool(validator(dict(schema)).is_valid(value))
    except Exception:  # a plugin's schema: one that cannot be used checks nothing
        _logger.warning("A plugin's JSON Schema could not be used.", exc_info=True)
        return None
    return value if fits else None
