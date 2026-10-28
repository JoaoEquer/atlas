---
title: "Tools"
description: "Every tool the agent has, when it has it, and what it takes."
sidebar:
  order: 3
---

What each of the agent's tools does, when the agent has it, and
what it takes. Why each tool is shaped the way it is lives in [ARCHITECTURE.md](https://github.com/marqueskaio/atlas/blob/main/docs/ARCHITECTURE.md)
(Tools, and the section each entry below names). The exact description and schema the model
receives live in the code: `src/atlas/tools/`, and `src/atlas/browser/` for `browser_exec`.

## How tools reach the model

- **Always on or deferred.** An always-on tool is in every model call, and paid for in tokens on
  every call. A deferred tool waits behind `tool_search`, which lists it by name and loads it into
  the conversation when the model searches for it. From then on it is a real tool, called with its
  own schema, for as long as the search's result stays in the history.
- **The order never changes**, so the provider's cache holds (`atlas.assistant`, `_Shared.tools`).
- **Every call passes the approval gate** (`atlas.trust`; ARCHITECTURE, Trust), a loaded tool like
  any other. What the gate checks is noted under each tool.
- **Flags a tool carries** (`atlas.agent.Tool`):
  - *read-only*: it runs alongside the round's other read-only calls;
  - *failure-tolerant*: failing again with other arguments is diagnosis, so the loop guards warn
    and never halt it;
  - *carried*: what it holds (the plan, the skills in use) comes back after a compaction;
  - an oversized result goes whole to a spill file, except from the tools that page their own
    output (`read_file`, `web_fetch`).
- **Measuring the cost:** `uv run python scripts/measure_tools.py` estimates the always-on tools;
  with a provider (`openai [model] --each`) it asks the real model, which spends credit.

## Who has which tool

| Tool | Conversation | Bot | Routine run | Subagent |
|---|---|---|---|---|
| `read_file`, `write_file`, `edit_file`, `find_files` | yes | if listed | yes | yes |
| `run_command`, `web_search`, `web_fetch` | yes | if listed | yes | yes |
| `browser_exec` (deferred) | with a browser | if listed | with a browser | with a browser |
| `memory` | yes | its own memory | yes | no |
| `ask_user` | yes | if listed | no | no |
| `delegate` | yes | if listed | yes, waits for the results | no |
| `skill_manage`, `use_skill` | yes | its own, and the library's it lists | yes | yes |
| `conversation_search` (deferred) | every agent's | its own conversations | yes | yes |
| `delegation_search` (deferred) | yes | if listed | yes | no |
| `executor_accounts` (deferred) | with an executor | if listed | with an executor | no |
| `plan` (deferred) | yes | if listed | yes | yes |
| `cronjob_manage` (deferred) | yes | its own routines | no | no |
| `message_agent` | when the home has a bot | if listed | no | no |
| `bot_create` (deferred) | yes | no | no | no |
| `tool_search` | when something is deferred | same | same | same |
| MCP tools | every server | the servers it lists | every server | every server |

- **A bot** gets only the tools its definition lists (`tools`) and the MCP servers it names; with
  no list it gets them all. It writes its own memory and skills, and the home's profile is shared.
- **"With a browser"** means a Chrome, Edge or Chromium is installed, or `[browser] chrome` names
  one.
- **The background review** (ARCHITECTURE, Memory) runs on the conversation's tools, but its gate
  lets through only `use_skill`, `read_file`, `find_files`, `skill_manage` and, when the memory is
  due, `memory`. There a replace or a remove becomes a proposal the user approves.
- `use_skill` exists only when there is a skill to load, and `tool_search` only when there is a
  deferred tool to find.

## Files

### `read_file` — always on, read-only

Reads a text file with its lines numbered as `cat -n`, 2,000 lines at a time within about 100,000
characters; a longer file says where to go on. A document (PDF, Word, PowerPoint, Excel,
OpenDocument, RTF, EPUB) is read as Markdown. An image (PNG, JPEG, GIF, WebP, up to 5 MB) is shown
to a model that sees images; one that does not gets a note.

- `path` (required): absolute, or relative to the working folder.
- `offset`: the first line, from 1.
- `limit`: how many lines, up to 2,000.

### `write_file` — always on

Creates a file, or replaces all of it, creating folders as needed. An existing file must have been
read in this conversation and be unchanged since. The write is atomic.

- `path` (required), `content` (required): the whole text.

### `edit_file` — always on

Replaces exact text and answers with the diff. `old_string` must appear once, unless
`replace_all`; only CRLF against LF is tolerated.

- `path`, `old_string`, `new_string` (all required); `replace_all` (default false).

**What the three file tools are kept from:** none of them reaches the vault's folder. `write_file`
and `edit_file` also refuse system paths, the home's settings (`config.toml`, `mcp.json`,
`models.toml`) and its `plugins/` and `plugin-data/`, and they ask before changing the SSH
configuration.

### `find_files` — always on, read-only

Searches with ripgrep, which ships with Atlas, on every system. It skips hidden and `.gitignore`d
files and folders such as `node_modules` and `AppData`, unless `path` is inside one, and stops at
60 seconds. Results come in path order. It searches inside documents too: each is converted once,
and its text is kept in the home's `documents/` until the file changes.

- `pattern` (required): a regex for content, a glob for names.
- `target`: `content` (default) or `files`.
- `path`: the folder or file to search; defaults to the working folder.
- `file_glob`: only files whose name matches (content).
- `output_mode`: `content` (default), `files_only` or `count` (content).
- `context`: lines around each match, up to 10. `ignore_case`.
- `limit` (default 50) and `offset`, to page.

## Commands

### `run_command` — always on, failure-tolerant

Runs the system's shell: PowerShell on Windows (`pwsh` when installed), bash elsewhere. Output and
errors come back together with the exit code. A timeout stops the whole process tree, and so does
stopping the turn. Standard input is closed, no window opens, and pagers and colors are off.

- `command`.
- `cwd`: defaults to the working folder.
- `timeout`: seconds, default 120, at most 600.
- `background`: returns at once; the output goes to a file in the home's `commands/`, read with
  `read_file`, whose last line says how the command ended. It lives while Atlas runs.
- `notify_on_complete`: with `background`, be told when it ends.
- `stop`: the id of a background command to stop, instead of running one.

**What the gate checks** (ARCHITECTURE, Trust), in order:

1. hardline commands are blocked, always;
2. the user's deny list blocks;
3. commands that act outside the machine ask the user every time, even in auto mode: writes
   through `gh`, `gws` and `gcloud`, printing their tokens, `atlas plugins enable` and the like,
   and anything touching the home's `plugins/`, `plugin-data/` or `atlas.db`;
4. dangerous patterns go to the guardian, which approves, denies or asks the user.

Auto mode skips step 4, the guardian and its questions, never steps 1 to 3.

The scripts a command runs are read through the same rules.

## Web

### `web_search` — always on, read-only

Returns the title, address and an excerpt of each result. The engine is the first the vault
allows: Tavily (`TAVILY_API_KEY`), then Brave (`BRAVE_SEARCH_API_KEY`), then the keyless `ddgs`, so
an empty home still searches.

- `query` (required): keywords; `limit`: up to 10.

### `web_fetch` — always on, read-only, failure-tolerant

Reads a page as Markdown of its main content, with no model in between; a PDF or Office link is
read as `read_file` reads it. Past `char_limit` it returns the first three quarters and the last
quarter, and keeps the whole text in a spill file with the `read_file` offset of the middle. Only
http and https; a redirect to another site is returned, not followed; a page is reused for 15
minutes.

- `url` (required); `char_limit`: 2,000 to 500,000, default 15,000.

The results of both tools reach the model inside an `<untrusted_tool_result>` block: data, not
instructions.

### `browser_exec` — deferred, failure-tolerant

Drives Atlas's own Chrome (ARCHITECTURE, Browser). `code` is Python with the browser helpers
imported: `new_tab`, `goto_url`, `wait_for_load`, `page_info`, `js`, `fill_input`, `click_at_xy`,
`capture_screenshot`, raw `cdp`, `ensure_real_tab`. The browser and the workspace folder
(`$BH_AGENT_WORKSPACE`) last across calls; Python variables do not. A screenshot path printed in
the output brings the image to a model that sees images. At a login, a CAPTCHA or a payment the
agent stops and asks the user to do it in the browser window.

- `code` (required): start it with a one-line comment naming the step.
- `mode`: `profile` (default: the user's signed-in profile), `clean` or `headless`.
- `timeout_s`: default 300, at most 1,800.

## Memory, skills and the past

### `memory` — always on

Adds, replaces and removes entries in two files the agent reads at the start of every
conversation: `user` (`memory/USER.md`, 1,375 characters: who the user is) and `memory`
(`memory/MEMORY.md`, 2,200: the agent's notes). Several changes go in one atomic call, checked
against the limit at the end. ARCHITECTURE, Memory.

- `target` (required): `user` or `memory`.
- `operations`: a list of `{action, content, old_text}`, the preferred shape.
- Or a single change: `action` (`add`, `replace`, `remove`), `content` (`new_text` is an alias),
  `old_text` (a piece of the entry to change or remove).

A write holding an injection pattern or invisible characters is refused.

### `skill_manage` — always on

Creates, patches and deletes skills, the agent's procedures, kept in the home's `skills/<name>/`
(a bot's in its own folder). One call is a list of operations, applied atomically; the previous
version is kept. ARCHITECTURE, Skills.

- `operations` (required), each naming its `name` and `action`:
  - `create`: `content`, the whole `SKILL.md`, its frontmatter `name` the skill's;
  - `patch`: `old_string` and `new_string` (`replace_all`, `file_path` optional), or `content` to
    replace the whole file;
  - `write_file` and `remove_file`: `file_path` under `references/`, `templates/`, `scripts/` or
    `assets/`, and `file_content` to write;
  - `delete`: alone in its call; it archives the skill.

### `use_skill` — always on, carried

Loads a skill and answers with its instructions, its folder and its files. Its description lists
the skills: every name, and descriptions cut at 60 characters within 1% of the window. The skills
in use come back after a compaction, 5,000 tokens each and 25,000 in all.

- `name` (required); `arguments`: what the skill works on.

### `conversation_search` — deferred, read-only

Finds and reads past conversations, with no model in between (FTS5, all words required, accents
ignored). The shape depends on the arguments:

- `query`: **discovery**, the best conversation in full and the others by their match. With
  `limit` (default 3, at most 10), `sort` (`newest`, `oldest`), `detail` (`adaptive`, `full`),
  `after` and `before` (an ISO date or `7d`, `24h`, `2w`), `exclude_conversation_ids`.
- `conversation_id` with `around_message_id`: **scroll**, `window` messages each side (default 5,
  at most 20).
- `conversation_id` alone: **read** the whole conversation.
- nothing: **browse** the latest conversations.
- `role_filter`: `user,assistant` by default; add `tool` to search tool output.

### `delegation_search` — deferred, read-only

Recalls what the executor instances on the workbench did (Claude Code and the other harnesses),
Atlas's and the user's, from the delegation record: each turn's prompt and steers, what it asked
and who answered, its final text and the files it changed (ARCHITECTURE, Plugins). The same four
shapes as `conversation_search`, with no model in between:

- `query`: **discovery**, FTS5 over the record only; the best delegation with every turn, the
  others with the matching turn. `limit` (default 3, at most 10).
- `instance_id` alone: **read** the record, then the start and the end of the harness's own
  session, read back by its plugin; when the harness deleted it (Claude Code does after 30 days),
  the record says so and still answers.
- `instance_id` with `around`: **scroll** the session, `window` entries each side (default 5, at
  most 20).
- nothing: **browse** the latest delegations.
- Filters for browse and discovery: `executor`, `folder` (part of the path), `after` and `before`
  (an ISO date or `7d`, `24h`, `2w`). `include_tools`: the tools' input and output too, in a read
  or a scroll.

### `executor_accounts` — deferred, read-only

Shows the outside executors' accounts before Atlas picks one, read from the harnesses with no
model call (ARCHITECTURE, Several accounts at once): each by the target name `delegate` takes, who
it is logged in as, its plan usage per limit window and when each resets, whether it is at its
limit now (reached here, or by its usage) and whether it bills past its limits (`extra_usage`),
the folders that start on it, and whether and in what order the accounts take over (`hand_over`).
Given wherever `delegate` has an executor.

- `executor`: only this executor's accounts.

## Work

### `plan` — deferred, carried

The task list of multi-step work. With no arguments it reads the list; with `todos` it writes the
whole list, or merges by id. One item in progress at a time; an item is completed only once
verified. The open items come back after a compaction, and a resumed conversation rebuilds the plan
from its last result.

- `todos`: items of `id`, `content`, `status` (`pending`, `in_progress`, `completed`,
  `cancelled`) and an optional `parent`.
- `merge`: true updates by id and adds; false (default) replaces the list.

### `ask_user` — always on

Asks the user 1 to 5 questions and waits for the answer, from the app or a channel. Each question
takes up to 4 choices, the recommended one first; the screen adds "Other". The result says how the
wait ended: submitted, cancelled, timed out or undelivered. It does not approve dangerous actions:
that is the gate's job.

- `questions` (required): each `question`, optional `choices` and `multi_select`.

### `delegate` — always on

Hands tasks to subagents, each with a clean context, the parent's model and tools minus the ones in
the table above. In a conversation they run in the background: the call returns at once, and the
summaries come back in one message when the last one ends. In a routine's run the call waits for
them. ARCHITECTURE, Subagents.

- `tasks`: up to 10, each a `goal` and the `context` it needs; it knows nothing of the
  conversation.
- `action`: `spawn` (default), `list`, `steer` (with `subagent_id` and `message`) or `stop` (with
  `subagent_id`).

**With an executor turned on and installed** (Claude Code, through the plugin Atlas ships;
ARCHITECTURE, Outside executors and Plugins), the tool also takes:

- `executor`: hands the tasks to it instead of subagents, each task with its `folder`; each task
  runs as an instance of Atlas's on the workbench while it works. A `target name`: the executor
  alone runs on the folder's account, else the main one; `claude-code@work` on the account the
  user nicknamed `work`, `claude-code@main` on the main one. The list names every account of an
  executor that has more than one;
- `route`: target names, in order, where each task goes on when its account reaches its plan's
  limit, in place of the executor's own order of its accounts (`["claude-code@work", "codex"]`;
  an executor alone is its main account here). Another account of the same executor goes on in a
  copy of the session; another executor starts a session of its own from the `handoff packet`;
- `action: resume` with `subagent_id` and `message`, to continue a stopped session; with
  `executor` naming another account or executor, it goes on there (a copy, or a new session from
  the packet), and with `route`, that is its route from then on;
- `action: account`, with `account` (an email, optional) and `executor` (which account), to log
  an account in by the harness's own login on the user's browser, only when the user asks. An
  email another account of the executor is logged in as already is refused: delegate to it by its
  name instead.

What the executor asks reaches the user, on its card on the workbench.

**The workbench in `list`:** besides the conversation's children, `action: list` shows every
instance on the workbench, with its folder, account, state and whose it is (yours, the user's,
another conversation's). `steer` and `stop` take an instance's id as `subagent_id`; on one of the
user's own, the user approves each time, past the guardian and auto mode.

### `cronjob_manage` — deferred

Makes and manages routines: a prompt run on a schedule, each run its own conversation and a
notification. A routine's prompt must stand alone. A reply of `[SILENT]` notifies nobody. The
prompt is scanned for threats before it is kept. ARCHITECTURE, Routines and loop guards.

- `action` (required): `create`, `list`, `update`, `pause`, `resume`, `remove` or `run`.
- `job_id`: the routine's id or name, for all but `create` and `list`.
- `name`, `prompt` and `schedule` for `create`. `schedule` takes `every 30m`, `in 30m` (once),
  `weekdays at 9am`, `every day at 18:30` (the user's time), cron syntax or an ISO time (once).

## Other agents

### `message_agent` — always on, when the home has a bot

Sends a message to another agent of the home, into its own conversation, and returns at once with
a delivery id. The reply, or the failure, arrives later as a notice. ARCHITECTURE, Bots.

- `target` (required): a name from the roster; `message` (required): at most 16,000 characters.

### `bot_create` — deferred, the main agent only

Makes a bot: a named agent with its own mission, memory, skills, conversation and routines. The
agent must ask the user first.

- `name` (required): lowercase letters, digits, `-` and `_`.
- `title` (required), `description` (required): one line, its role.
- `mission`: its standing rules; composed from the title and description when left out.

## Finding the rest

### `tool_search` — always on, read-only

Finds deferred tools and loads them, up to `limit` (1 to 10, default 5). It ranks with BM25 over
name, source, description and parameter names, exact names first. Its description lists
the deferred tools by source, within 5% of the window and at most 4,000 tokens. On the Responses
API the search is the provider's own, run by Atlas.

- `query` (required): keywords (`github create issue`) or a tool's exact name.

### MCP tools

The user's servers in the home's `mcp.json`, in Claude Code's `.mcp.json` format; none ships
connected. Each tool is named `mcp__<server>__<tool>` and is deferred unless the server says
`alwaysLoad`. A server marked `"trust": "untrusted"` asks before every call to a tool that does not
declare itself read-only. Their results reach the model as untrusted data. ARCHITECTURE, Tools.

A remote server that signs in with OAuth (`"oauth": {}` in its entry) waits for you to sign in
from a terminal, once:

```bash
atlas mcp login erp        # opens the server's page; authorize Atlas there
atlas mcp logout erp       # forget the sign-in
```

The browser comes back to an address Atlas listens on, on this machine, for that sign-in only.
When the browser is on another machine (Atlas in a container, or over SSH), the page it lands on
does not load: copy its address from the address bar and paste it into the terminal, or pass
`--no-browser` to only print the page. Atlas keeps the sign-in encrypted beside the vault, never
among your secrets, renews it on its own and connects the server within a few seconds, without a
restart. A server whose sign-in has ended says so and names the command again.

Plugins add no tools yet: the SDK registers channels (version 1) and executors (version 2), not
tools (ARCHITECTURE, Plugins).
