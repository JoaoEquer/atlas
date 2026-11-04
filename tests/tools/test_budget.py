"""The always-on tools stay within their token budget on every model call.

The roadmap's target is about 3,000 tokens for the always-on tools (Atlas V3 paid about 10,800).
Measured on 09/10/2026 with `scripts/measure_tools.py`, the eleven of step 5 cost 1,338 tokens on
gpt-6-luna and 1,929 on gemini-3.8-flash; Atlas's own estimate (UTF-8 bytes over four) gave 1,954,
close to the higher of the two. Step 6 added the memory: the memory tool's description in full
(too critical to risk degradation by cutting it), and conversation_search and plan deferred behind
tool search. These eleven and the tool search come to an
estimate of about 2,730, and cost 1,996 on gpt-6-luna.

Step 6b added skill_manage, always on. Measured on 09/10/2026, the twelve cost
2,482 tokens on gpt-6-luna (skill_manage 486) and 3,571 on gemini-3.7-flash;
the estimate is 3,588. The target is counted on gpt-6-luna, as the roadmap has counted it since
step 5: Gemini counts about 44% more for the same definitions. use_skill comes on top, its
listing about 20 tokens a skill since descriptions are cut at 60 characters.

The estimate follows Gemini's count, so the test holds the estimate where it is now: a tool that
grows fails here before it reaches a provider.
"""

import dataclasses
import json
import math
from collections.abc import Sequence
from pathlib import Path

from atlas.agent import Tool, Toolset
from atlas.conversations import ConversationStore
from atlas.home import Database
from atlas.jobs import Task
from atlas.memory import MemoryFiles
from atlas.models import ToolSpec
from atlas.skills import SkillLibrary
from atlas.tools import (
    Answers,
    Commands,
    Outcome,
    Plan,
    Question,
    WebFetch,
    Workspace,
    ask_user,
    conversation_search,
    delegate,
    file_tools,
    find_files,
    install_tool_search,
    memory,
    skill_manage,
    web_search,
)
from atlas.tools.keyless import KeylessSearch

#: The always-on tools, the tool search among them, in Atlas's estimate (bytes over four), as
#: measured on 09/10/2026 with a few tokens to spare, plus run_command's `notify_on_complete`
#: (step 12), plus the delegate tool's description for the subagents (step 12b, about
#: 700 more; decided with the user on 12/10/2026 against a cut of Atlas's own): 3,067 tokens on
#: gpt-6-luna, measured on 12/10/2026, which counts some 30% fewer; the target is about 3,000 there.
#: run_command's `description` (for the agent map; 14/10/2026) took 28 of the
#: spare tokens, leaving 1.
ESTIMATE = 4_350


class _Delegator:
    async def spawn(
        self,
        tasks: tuple[Task, ...],
        executor: str | None = None,
        *,
        auto: bool = False,
        route: Sequence[str] = (),
    ) -> str:
        return ""

    def executors(self) -> list[str]:
        return []

    async def resume(
        self,
        subagent_id: str,
        message: str,
        executor: str | None = None,
        route: Sequence[str] = (),
    ) -> str:
        return ""

    async def account(self, email: str, executor: str | None = None) -> str:
        return ""

    async def accounts(self, executor: str | None = None) -> str:
        return ""

    def list(self) -> str:
        return ""

    async def steer(self, subagent_id: str, message: str) -> str:
        return ""

    async def stop(self, subagent_id: str) -> str:
        return ""


async def _never(questions: tuple[Question, ...]) -> Answers:
    return Answers(Outcome.UNDELIVERED)


async def _nothing(arguments: dict[str, object]) -> str:
    return ""


def _estimate(tool: Tool) -> int:
    spec = ToolSpec(tool.name, tool.description, tool.parameters)
    return math.ceil(len(json.dumps(dataclasses.asdict(spec)).encode()) / 4)


def test_the_always_on_tools_fit_their_budget(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    executor = delegate(_Delegator())
    assert executor is not None

    toolset = Toolset(
        [
            *file_tools(workspace),
            find_files(workspace),
            Commands(workspace, tmp_path / "commands").tool(),
            web_search(KeylessSearch()),
            WebFetch().tool(),
            ask_user(_never),
            Plan().tool(),
            executor,
            memory(MemoryFiles(tmp_path / "memory")),
            skill_manage(SkillLibrary(tmp_path / "skills")),
            conversation_search(ConversationStore(Database(tmp_path / "atlas.db"))),
            Tool("mcp__github__create_issue", "Create an issue.", {}, _nothing, deferred=True,
                 source="mcp:github"),
        ]
    )  # fmt: skip
    install_tool_search(toolset, {"mcp:github": "Issues and pull requests on GitHub."})

    costs = {tool.name: _estimate(tool) for tool in toolset.always}

    assert len(costs) == 12  # conversation_search and plan wait behind tool search
    assert sum(costs.values()) <= ESTIMATE, costs
    assert max(costs.values()) <= 900, costs  # memory, in full


class _WithClaudeCode(_Delegator):
    def executors(self) -> list[str]:
        return ["claude-code"]


#: With an outside executor installed (step 13), delegate says so, and how to switch its account
#: and resume a session: 200 tokens more in the estimate, measured on 12/10/2026, about 137 on
#: gpt-6-luna. Its `approval`, manual or Claude Code's own automatic mode for one delegation, took
#: 56 more (17/10/2026). Picking the account (`name@account`) and the `route` took 71 more
#: (258 to 329, 21/10/2026), and each account an executor has about 5. A task's `workspace: "new"`
#: (step 21) took 35 more (329 to 364, 04/11/2026).
EXECUTOR_EXTRA = 370


def test_an_installed_executor_costs_only_its_own_words() -> None:
    plain, with_executor = delegate(_Delegator()), delegate(_WithClaudeCode())
    assert plain is not None
    assert with_executor is not None

    assert 0 < _estimate(with_executor) - _estimate(plain) <= EXECUTOR_EXTRA
