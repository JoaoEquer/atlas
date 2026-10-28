# Atlas Architecture

**Status:** decided, 2026-10-07. These are the rules from now on. Planning and decisions live in [ROADMAP.md](../ROADMAP.md) (Portuguese); this file
is for anyone writing code here, human or agent.

## Principles

1. **The harness stays thin; the model does the work.** Atlas should get better every time models do.
   Scaffolding that a better model makes unnecessary is a cost, not an asset.
2. **Memory is text the agent reads and writes.** No AI runs on the memory path unless the model asked
   for it, with one exception: the background review, a copy of the agent that
   rereads a long conversation and keeps what it taught, in the memory and in skills (Memory and
   Skills, below).
3. **Few tools are always on; the rest are found by search.** Every always-on tool is paid on every
   model call and competes for the model's attention.
4. **The core runs anywhere.** One command on a laptop or a VPS, one container, or the desktop app. An
   empty container plus an API key must give a working conversation.
5. **Nothing personal lives in the repository.** User state (profile, memory, conversations,
   secrets, config) lives in `ATLAS_HOME`, outside the repo.
6. **Guarantees are checked by code, and tests exercise the real component.** A test never patches
   the piece it claims to prove.

## Repository layout

```text
src/atlas/
  agent/       the loop: messages, model call, tool execution, guards
  assistant/   the agent put together for a conversation: loop, stored history, tools, gate, review
  models/      provider adapters (OpenAI-compatible protocol in core)
  tools/       the built-in tools and tool search (the documentation's Tools page)
  mcp/         the client for the user's MCP servers, whose tools are deferred
  memory/      the profile and the memory, the instructions that carry them, the background review
  conversations/ stored conversations and their search
  skills/      skill catalog and loading, writing, versions, usage and the curator
  trust/       the approval gate, dangerous commands, the guardian, auto mode, threat scanning
  jobs/        background jobs, tasks, cron
  bots/        the home's bots: their definitions, their memory and skills folders, the roster
  browser/     the browser: Atlas's own Chrome, the code that drives it, the user taking over
  server/      HTTP + WebSocket, and the client protocol (single source)
  instances/   talking to more than one machine
  secrets/     encrypted vault (age); OS keystores as adapters
  home/        ATLAS_HOME and its boundary, settings, database and migrations, logs, UTC time
  hooks/       plugins' hooks at the loop's events, in Claude Code's events and JSON
  plugins/     plugin loader and the public SDK; the plugins Atlas ships in bundled/
  marketplace/ the marketplace: its items read and checked, installed, updated and retired
  installation/ the checkout the user runs every day: its app built and opened, its update
  docs/        the documentation for whoever uses Atlas: its pages, which the site shows and
               `atlas docs` reads (The documentation, below)
apps/desktop/  Electron app (TypeScript)
tests/<subject>/
tests/docker/  the image's tests, run against a built image (the Dockerfile and its compose file
               sit at the root)
docs/
```

### Inside a subject

| File | Holds | May import |
|---|---|---|
| `model.py` | Data and pure rules | Standard library and its own subject only. No I/O |
| `service.py` | Use cases | Its own `model`, its own `store`, other subjects' public API |
| `store.py` | Persistence and adapters (SQLite, files, network) | Anything it adapts |
| `__init__.py` | The subject's public API | Re-exports only |

A subject grows extra modules when it needs them (`agent/guards.py`, `tools/files.py`), but the three
roles stay separate.

### Boundaries, enforced in CI by import-linter

- `model.py` never imports I/O libraries or other subjects.
- A subject reaches another subject only through its package root (`from atlas.memory import ...`),
  never through its internals.
- Nothing imports `server`: the edge depends on the core, not the other way round.
- Plugins import only the SDK: `atlas.plugins.sdk`, and since version 18 its ACP client,
  `atlas.plugins.acp`. A shipped plugin whose folder is no module name (`claude-code`) is out of
  import-linter's sight, so a test reads its imports.

## The agent loop — decided 2026-10-07

**Our own loop, with no agent framework: no LangChain, no LangGraph.** It talks directly to each
provider's official SDK. Atlas V3 lost real
conversations to framework internals:

- A LangGraph hook received `config=None` silently when the parameter was not annotated
  `RunnableConfig`.
- `stream_mode="messages"` leaked hook-only input into the user's chat bubble.
- `RemoveMessage` followed by the same message replaced it in place instead of reordering. A broken
  history then went undetected and returned HTTP 400 on every later turn.
- A trailing assistant message, fine on OpenAI, made Gemini reject every turn.

The loop owns a plain message list, calls one provider adapter, executes tools, and runs guards:
context budget, repeated-call guard, approvals, compaction and history repair. The guards come from
V3, which is where most of its hard-won value is.

Decided 2026-10-07, in step 4 (`atlas.agent`):

- **No cap on a turn's steps**: a cap cuts tasks short in silence. The repeat guard, clearing and compaction hold the rest, the call ledger
  records every call, and the user can stop a turn at any moment.
- **History repair** runs on the copy sent to the model before every call, never on the stored
  history: each call is followed by its result, a missing result says its outcome is unknown.
- **Context is measured as the provider counts it**: the input tokens it reported for the last
  call, plus a local estimate (UTF-8 bytes over four) of only what entered since. Provider fields
  are not estimated: OpenAI does not count earlier turns'
  encrypted reasoning as input.
- **Clearing** replaces old tool results with a note in the copy past 100,000 tokens (or half the
  window); **compaction** summarizes the older stored history past 85% of the input window, keeps
  the user's words verbatim, and leaves the history untouched when it fails. A summary cut short
  is a failure, and its length is asked in its instructions, never set as an output limit that a
  reasoning model spends thinking. A model with no known window is compacted only when
  the provider says the conversation does not fit.
- **A result over 50,000 characters** goes whole to `ATLAS_HOME/spill/` for a day, and the model
  gets its start, its end and the path; a round's results together stay under 200,000.
- **The repeat guard** warns at the 3rd identical call in a row with the same result, and ends the
  turn at the 5th.
- **Read-only tools of a round run together**; a tool that changes something runs alone.
- **A model's failure ends the turn with its reason**; stopping a turn leaves every call with a
  result.

## Models

Each provider adapter wraps that provider's official SDK behind one small interface that the loop
calls. The interface takes messages and tools, and returns text, tool calls and usage.

- The core ships the adapter for the OpenAI SDK. Through OpenAI-compatible endpoints it covers
  OpenAI, OpenRouter (and therefore the future Atlas Cloud credits), Gemini's compatible endpoint
  and local servers such as Ollama and vLLM.
- Anthropic's own SDK is in the core too (decided 2026-10-14, step 3b), as an extra: its API caches only where a request marks it and keeps a model's
  reasoning only while everything before it is unchanged, which no OpenAI-compatible endpoint
  carries (Anthropic, below). Other native SDKs (Google) would come in as model-provider plugins.
- Capability and price data per model live in data files, not in code.

Decided 2026-10-07, in step 3:

- **One adapter, two wires.** Chat Completions for every OpenAI-compatible provider, and the
  Responses API for OpenAI itself: since GPT-5.4, Chat Completions refuses function tools together
  with reasoning. Both always stream, and both are stateless (`store: false` on OpenAI).
- **The interface** is `ModelClient.stream` (text and reasoning deltas, then the reply) and
  `ModelClient.reply`, over Atlas's own message types, so a conversation can move between
  providers.
- **Provider fields** (Gemini's thought signature, OpenRouter's reasoning details, OpenAI's
  encrypted reasoning) stay in the stored history and go back only to the provider and model that
  wrote them. Strict providers refuse fields they do not know.
- **Keys** come from the vault by name (`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`),
  never from the environment.
- **With no model in the settings, the first provider with a key in the vault** is used, with its
  default model, decided on 2026-10-11 for the
  container (Container, below). The order is the catalog's (OpenAI, OpenRouter, Gemini); a
  provider with no key (a local server) is never chosen this way. It is asked at each
  conversation's first turn, so a key stored while Atlas runs serves the next message; with no
  key at all, the turn ends saying how to store one.
- **The catalog** has three files: `atlas/models/data/providers.toml` by hand,
  `atlas/models/data/models.json` cut from models.dev by `scripts/update_model_catalog.py`, and the
  user's `ATLAS_HOME/models.toml`, strict like `config.toml`, which wins field by field. What no
  file says stays unknown (`None`), never a guess.
- **Cost** says where it came from: reported by the provider, the price list, free (local), or
  unknown. A price is never invented.
- **Gemini's effort goes inside its `thinking_config`**, as `thinking_level`, beside the
  request for readable reasoning: Gemini refuses `reasoning_effort` next to a `thinking_config`
  (HTTP 400, found by step 7's proof on 10/10/2026).
- **A silent provider is given up** after 120 seconds without a byte, before the reply starts or
  in the middle of it: Gemini once held a reply open for 10 minutes and sent nothing.
- **A limit that lasts is not a rate limit.** When the provider says the wait is longer than a
  minute (Gemini's daily free-tier quota: "retry in 22h"), the call fails as a used-up quota, which
  the caller must not retry in seconds.
- Only `atlas.models.store` imports the SDK, and the wires (`chat`, `responses`) only build and
  parse; import-linter holds both.

### Anthropic — decided 2026-10-14

Step 3b, decided with the user to run Claude Opus 5.5 in Atlas on a paid API key (the subscription
does not serve: Outside executors, below), from Anthropic's documentation on 13 and 14/10/2026.
Through OpenRouter or
Anthropic's OpenAI-compatible endpoint nothing is cached unless the request marks it, and the
latter is "not considered a long-term or production-ready solution".

- **A third wire, `messages`**, beside `chat` and `responses`, under the same `ModelClient`:
  `atlas.models.messages` builds and reads, pure; `atlas.models.messages_store` is the only module
  that imports Anthropic's SDK, an extra (`anthropic`) loaded only for a provider on this wire, so
  a bare install starts without it and says how to add it. The `anthropic` provider comes after
  Gemini, so the first provider with a key stays what it was; its ids are Anthropic's
  (`claude-opus-5-5`), and models.dev's `anthropic` catalog prices them.
- **The reasoning goes back sealed.** A reply's blocks are kept in the order they came,
  whitelisted field by field, and go back only to the model that wrote them. A block holds only
  while everything before it is unchanged, byte for byte; on an account made after 31/08/2026 an
  edited past is a 400. Atlas edits its past when it compacts and when the instructions change, so
  **each reply keeps the fingerprint of the request it answered** (`prefix`), and from the first
  reply whose fingerprint no longer matches, the reasoning goes, to the end: removing reasoning is
  allowed.
- **Old results are cleared by Anthropic's server**, `clear_tool_uses_20250919` with the
  `context-management-2025-06-27` beta, never by the loop (decided with the user): the server's
  clearing never invalidates the reasoning on the current models, the loop's would. Same rule
  (100,000 tokens or half a smaller window, the 3 latest kept), the tool search's results never
  cleared, and **at least 20,000 tokens a time**, since a clearing rewrites the cache from there on
  (Anthropic's advice; Atlas's figure).
- **Four cache points**: the system prompt, the last tool that is not deferred, the ends of the last
  two completed rounds. An hour when a person paces the turns (`ModelRequest.long_cache`: a
  conversation's turns, its bots' included), five minutes for a routine, a subagent, a review, the
  guardian or a compaction (a person often comes back after 5 to 60 minutes idle, which a
  five-minute cache would make a rewrite). The hour-long write costs 2x the input, which models.dev
  does not give: the provider's `cache_write_long` factor prices it, and `Usage` and the call
  ledger keep its share.
- **Deferred tools by reference**: the loop gives every deferred tool (`ModelRequest.
  deferred_tools`), they go on every call with `defer_loading`, and the tool search's result names
  what it loaded in `tool_reference` blocks; the tool list never changes, so neither the cache nor
  the reasoning breaks.
- **Thinking** is adaptive, its readable summary asked for (`display: summarized`); the effort goes
  in `output_config`. **`max_tokens` is always sent, and at most 32,000** (the provider's
  `max_output`, raised in `models.toml` up to the model's own limit): on 14/10/2026 one Opus 5.5
  call of
  the real proof reasoned in a loop to the model's 128,000 for 13.5 minutes and cost US$ 2.68
  alone, the silence limit never firing since the reasoning kept streaming. A request's own ceiling (a compaction, the guardian) stays. Errors: 402, a
  `billing_error`, a 400 "credit balance is too low" and the spending cap's 429 are an account that
  cannot pay; 529 is an overloaded API. The key alone goes, in `x-api-key`; the SDK reads no
  credential from the environment when given one, so two are never sent.
- **Proved** by `tests/models/test_messages.py` and `tests/agent/test_anthropic_loop.py` through
  the real SDK against `tests/fake_model.py`, which speaks Messages and refuses as Anthropic does
  (a thinking block whose past changed, a blank block, an output-only field, an unknown tool
  reference, a cache point on a deferred tool, more than four points, a call without its result
  right after, clearing without its beta, prefill, tool references beside other content) and
  caches and clears as Anthropic does; each rule was checked to fail the tests when switched off.
  And against the real API on 14/10/2026: `prove_models`, `prove_agent` and `prove_tools` on Haiku
  5.5 (about US$ 0.005; the tools proof found that Anthropic refuses tool references beside other
  content in a result, a 400 on every later call of the conversation), and `prove_anthropic` on
  Opus 5.5 (US$ 4.18 instead of the estimated 1 to 1.5, the call above): twelve chapters read and
  answered with no request refused, 75% of the input read from the cache (US$ 1.50 for thirteen
  calls that would cost 2.83 without it), and the server clearing once from about 105,000 tokens
  to 40,000, the cache read again after it.
- **Left out** (Atlas's call, told to the user on 14/10/2026): the hour-long cache only on the
  turn's last cache point.
  Anthropic requires the longer TTL's points before the shorter ones, so the turn's end cannot be
  the one long point, and the rounds of tools inside a turn keep the hour.

### Conversation titles — decided 2026-10-14

In `atlas.assistant.titles`.

- **Two stages.** The instant title, the first line the user wrote, is stored with the first
  message. As the first turn starts, a side call to a title model runs in the background and
  replaces it; the server tells the app (`title.changed`), which renames the row without waiting
  for the turn to end. When the call fails, the instant title stays.
- **The title models, decided by the user:** Claude Haiku 5.5 with `thinking: disabled`, the
  cheapest it takes; when the vault has no Anthropic key or the
  call fails (a used-up credit above all), GPT-6 Luna with the effort `none`, at the same price;
  with neither, the instant title. Never the conversation's own model. A
  request turns reasoning off with `ModelRequest.reasoning = False`.
- **The request** (3 to 7 words, the user's language, `{"title": ...}`, 1000
  characters of the message, 512 tokens of output at most), read loosely, with its guards: a reply
  over 12 words is an answer and is refused, an echo of the prompt's examples is refused, and a
  greeting's placeholder keeps the instant title, so turns 2 and 3 try again. One rule more: the
  placeholder is asked in English, since a translated one would not be recognized.
- **Provenance** (`TitleSource`, the conversations' migration 4): `derived` < `llm` < `user`, the
  check and the write in one transaction. A bot's, a routine's and a subagent's conversations are
  named when made (`user`), and only the user's own conversations are titled.
- **Left out:** a response format forced by the provider (each wire spells it its own
  way, and the guards catch what it prevents), and the wait for a model on the user's machine,
  which shares its slot with the turn (both title models are hosted). A conversation the user
  renames in the list takes `user` (step 9e, The list of conversations).
- **The quarantine** (asked for by the user on 15/10, when each title paid a refused Haiku call
  first, the Anthropic account having no credit): a title provider that says the account cannot
  pay, or refuses its key, is skipped by every title for 10 minutes, and asked again after.
  In the process only: a restart tries again. A passing failure (an overload, a rate
  limit, a dropped connection) skips nothing: a title, one per new conversation, does not need
  to wait it out. Nothing is kept on disk; a title is no main call.
- **Proved** by `tests/assistant/test_titles.py` and the server's and app's tests against
  `tests/fake_model.py`, and against the real models on 14/10/2026 (`prove_titles`): both took the
  request, reasoned nothing and named a Portuguese message in Portuguese, for US$ 0.0001
  together.

## Tools

What each tool does, who has it and what it takes: [Tools](../src/atlas/docs/pages/reference/tools.md). Why they are so:

Decided 2026-10-09, in step 5 (`atlas.tools`):

- **Thirteen tools are always on** (ROADMAP, Ferramentas); everything else is a **deferred tool**
  found by **tool search**: MCP and plugin tools, and Atlas's own rarely used ones. V3's 52 tools
  cost about 10,800 tokens per call; the target is about 3,000.
- **A found tool becomes a real tool of the conversation**, called with its own schema. On the
  Responses API (GPT-5.4 and later, `tool_search` in the provider
  catalog) the search is the provider's own, executed by Atlas, and the found tools travel inside
  its `tool_search_output`, at the end of the context, so the cache holds. On Chat Completions they
  join the tool list, and the cache breaks once per load. One history serves both: a result
  records the names it loaded (`ToolResult.loaded`), and they stay loaded while it is there.
- **Ranking**: BM25 over name, source, description and parameter names, stemmed; the
  rarest query word admits a tool; long queries need half of their known words; exact names first.
- **The tool search exists only when there is something to find**, and its description lists the
  deferred tools by source, names and first sentences: within a budget (5% of the window, at most
  4,000 tokens), falling to names
  only and then to a line per source, the largest first; sorted, so the cache holds.
- **Every call passes the tool gate** (`AgentLoop(gate=...)`) before it runs, a loaded tool like
  any other: in V3, a tool moved behind `mcp_call` silently lost its approval. Approvals (step 7)
  plug in there.
- **The file tools are native** (`atlas.tools.files`), so the core needs no Node. `read_file`
  numbers lines as `cat -n` and pages 2,000 lines within about 100,000 characters, documents
  (PDF, Office, OpenDocument, RTF, EPUB) read as Markdown, and an image
  (up to 5 MB) handed to the model: inside the result on the Responses
  API, in a user message after the round's results on Chat Completions, a
  note for a model that does not see images; clearing drops old images; `write_file`
  replaces a file atomically, and only one read in this conversation and unchanged since;
  `edit_file` replaces exact text, unique unless `replace_all`, tolerating only CRLF. The vault's
  folder is out of their reach.
- **`find_files` runs on ripgrep** on every system, for names and content (ripgrep ships with
  Atlas, no second engine). It skips a list of folders by name, plus `AppData`, and stops at 60
  seconds, returning results in path order. It also
  searches inside documents (PDF, Office, OpenDocument, RTF, EPUB): each is
  converted once by read_file's converter, its text kept in the home's `documents/` until the
  file changes, and ripgrep searches the text (Memory, below).
- **`run_command` runs the system's shell**, PowerShell on Windows (`pwsh` when installed) and
  bash elsewhere, and its description names it. Output and errors come together with the exit
  code; a timeout (2 minutes, at most 10) stops the whole process tree, and so does stopping the
  turn. A background command writes to `ATLAS_HOME/commands/`, read with `read_file`, whose last
  line says how it ended; it lives while Atlas runs. Standard input is closed, no window opens,
  and pagers and colors are off. The model names a background command in a
  few words (`description`, 28 tokens), and its start and end reach the
  screen (Desktop app, the agent map).
- **`plan`** is deferred behind tool search, and **`ask_user`** is always on (ROADMAP, decided
  2026-10-09). A tool may carry text across a compaction (`Tool.carried`): the plan's open items
  follow the summary, and a resumed conversation rebuilds the plan from its last result.
  `ask_user` waits on an `Asker` the server gives (step 8); a turn with nobody to answer is not
  given the tool.
- **Skills are Claude Code's folders** (`atlas.skills`): `ATLAS_HOME/skills/<name>/SKILL.md` with
  YAML frontmatter, read when asked. `use_skill` lists them in its description (names always,
  descriptions within 1% of the window, each cut at 60 characters,
  decided 2026-10-09 to fit `skill_manage` in the budget), answers with the instructions, the
  folder and its files, and carries the skills in use across a compaction (5,000 tokens each,
  25,000 in all). `skill_manage` writes them (Skills, below).
- **`web_search` runs on the engine the vault allows**: Tavily
  (`TAVILY_API_KEY`), then Brave (`BRAVE_SEARCH_API_KEY`), then the keyless `ddgs`, so an empty
  home still searches; an empty keyless answer says the engine may be failing. **`web_fetch`**
  fetches locally: Markdown of the main content with no model in
  between; past 15,000 characters (`char_limit`, 2,000 to 500,000) the first three quarters and
  the last one, the whole text in the spill folder with the read_file offset of the middle; a PDF
  or Office link read as read_file reads it. Http and https only, a redirect to
  another site returned rather than followed, and a page reused for 15 minutes.
- **`delegate` hands tasks to subagents** (step 12b; Subagents, below), always on. Step 5's
  interface, an executor streaming its work into the turn
  through `report_progress` (any tool may still report), was written for a delegate that waits; a
  subagent runs in the background, so it left, and the outside harnesses of step 13 join the tool
  as an optional `executor`, their events reaching the user then.
- **MCP** (`atlas.mcp`): the user's servers in `ATLAS_HOME/mcp.json`, Claude Code's `.mcp.json`
  format (stdio, `http`, `sse`; `timeout`, `headersHelper`, `alwaysLoad`; unknown fields ignored;
  `oauth` servers signed in to with OAuth 2.1, below), `user` in the home's boundary; none ships
  connected. Secrets are `${NAME}` or `${NAME:-default}` from the vault. One
  long-lived session per server, opened again when it died; a call waits for the server's
  `timeout`, or until 5 (network) or 30 (stdio) minutes of silence. MCP tools
  are deferred unless `alwaysLoad` says otherwise, named `mcp__<server>__<tool>`, their
  descriptions cut at 2,048 characters, and a tool whose schema no provider takes is left out and
  said. Only `atlas.mcp.store` imports the official SDK.
- **A remote MCP server signs in with OAuth 2.1** (`atlas.mcp.oauth`), decided 2026-10-28, as
  the MCP authorization spec asks: the server's resource metadata (RFC 9728, from its 401's
  `WWW-Authenticate` or the well-known address), its authorization server's (RFC 8414), Atlas
  registered as a public client for each sign-in (RFC 7591), the code grant with PKCE S256, a
  `state`, the `iss` when sent (RFC 9207) and `resource` (RFC 8707). The SDK has a client of its
  own, but it signs in from inside a request, where a connection has 30 seconds; the sign-in
  is the user's, at a page only a person may approve, so it is a command, `atlas mcp login
  <server>`, never a server process: the browser comes back to a loopback address (RFC 8252), or
  the user pastes the address it landed on. The tokens are kept by server name in the vault's
  second file, `secrets/mcp-sign-ins.json` (same key, same lock, `machine`), apart from the
  secrets the user stored. Every request carries the bearer token, renewed a minute before it
  ends and once on a 401, the request then sent again once. A server without a sign-in, or whose
  renewal is refused, is left out naming the command, and the server, which watches the file,
  connects it once the user signs in. An entry without `oauth` uses a sign-in when one is kept.
  Left out: Claude Code's `oauth` fields (a fixed client id, a callback port) and a sign-in from
  the app.
- **The toolset changes while Atlas runs**: a source's tools are replaced together, or not at all
  when a name clashes, and the tool search is installed again after.

## Memory — decided 2026-10-09

The memory of a personal assistant (ROADMAP, Memória). A notebook, an index always loaded plus a
note per subject, was chosen on 07/10 and left on 09/10: it works when each project has its own,
and one notebook for all of a user's subjects would carry lines about other subjects into every
call.

- **The profile and the memory** (`atlas.memory`): `memory/USER.md` (1,375 characters) and
  `memory/MEMORY.md` (2,200), entries separated by a `§` line. The
  `memory` tool, its description in full (too critical to risk degrading), adds, replaces and removes entries, several in one atomic call
  checked against the limit. An entry is found by equality first, then by a piece of it, then with
  quotes and dashes folded. A single change over the limit, or finding nothing, shows the entries
  so the agent consolidates in the same turn; a failed batch does not repeat them
  and names the closest ones; what a replace or remove overwrote is said back. A file that exists
  but cannot be read is never written over, and one holding an entry longer than the whole limit
  (text appended outside the tool) is copied aside and left alone.
- **The instructions** carry the memory snapshot: read when a conversation starts and again after
  a compaction, so a write mid-conversation
  does not break the provider's cache. Their guidance is short, and does not
  repeat what the tool's description teaches. The loop asks for them (`AgentLoop(instructions=...)`) and
  keeps them as the conversation's first message.
- **The background review**: after a turn, when 10 user messages went by in the
  conversation without a memory write, a copy of the agent gets the conversation plus a
  review prompt, which routes each fact to one of the two files; the same review keeps skills
  (Skills, below), with a combined prompt when both are due. Same model and the same tools
  (so the provider's cache holds), the gate refusing all but the memory and skill tools, at most
  16 calls and input tokens up to three quarters of the model's window, never past 600,000
  and 120,000 for an unknown window. **It never
  replaces or removes an entry on its own** (a review could delete standing
  rules after the "consolidate now" hint): such a call becomes a memory proposal the user approves
  or discards (Memory proposals, below), and a review due for skills only is not given `memory` at
  all. Its calls are `background_review` in the call ledger.
- **Conversations** (`atlas.conversations`): every message stored raw, for
  good, in `atlas.db`. A compaction archives the replaced messages instead of deleting them, and
  they stay searchable. A conversation keeps its id for life.
- **Conversation search** has no model in between: words (FTS5,
  accents ignored, all words required, the query cleaned), the best conversation in full
  (the match's surroundings and its first and last messages) and the others compact; scrolling
  around a message; reading one conversation; and the recent ones. A time frame and the
  conversations already read narrow it, and an exact title finds a conversation first. The
  teaching sits in the responses, so the description stays short, and each message is cut to what a shape needs. The user's and the agent's
  messages are searched, tool output when asked, and the current conversation only where a
  compaction archived it. It is a deferred tool, found through tool search.
- **No library.** The user's files are found where they are. Measured
  on 09/10: ripgrep alone found a word in none of 31 real documents the converter read, and a
  Python process with the converter takes 0.8 s to start, so documents are converted inside Atlas
  and not through ripgrep's `--pre`, which starts one process per file on every search.
- **No vectors yet.** On LongMemEval plain word search already finds 98–99% of what it should;
  vectors help on paraphrase, multi-hop and temporal questions (LoCoMo). They may come later as an
  extra, when real use shows searches failing for want of them.

### Memory proposals — decided 2026-10-11

A change kept for the user to approve later.

- **What becomes a proposal.** A background review's `memory` call that replaces or removes an
  entry is not applied: the whole call is kept, its adds included, since they may fit the limit
  only beside the removal. It is checked first as if applied now, so
  an entry not found, an ambiguous one, a result over the limit or a threat pattern is refused at
  once, as the direct write would be; and each replace or remove records the
  whole entry it selects. The review's model is told the change waits for the
  user. A conversation's own writes still apply directly.
- **Kept in the database** (`memory_proposals`): the operations with their entries, the target, the
  conversation the review read, and when. Proposals do not expire and have no cap;
  V3's M-42 queue, whose tickets expired, is left out.
- **Approval applies exactly the recorded entries** or nothing (replaying the
  search at approval could delete a newer entry nobody had seen). A refused approval keeps the
  proposal. What an approval overwrote or removed is said back in full, so the user can ask for
  what they still want.
- **A proposal that can no longer apply is marked when listed** (Atlas's, decided on 11/10):
  listing tries each one against the files as they are now, and says why one cannot apply (an
  entry changed, the result no longer fits), so the screen offers only to discard it.
- **Reaching the user** (decided on 11/10, both ways): the review's summary says a change waits; the
  proposal shows as a card in the conversation the review read; and the screen counts the open
  proposals and lists them all (with nowhere to review them, they would pile up unseen). The
  terminal has `atlas memory proposals`, `approve` and `discard`. There is no approve-all: each
  card has its own button.

### The soul — decided 2026-10-15

Asked for by the user on 15/10.

- **The file** is `ATLAS_HOME/SOUL.md`, a `user` entry: a backup carries it, and `atlas home create
  --clone` copies it with the settings. A bot's soul stays the body of
  its definition (Bots, below).
- **Seeded** with Atlas's default identity when missing, as the assistant starts, and never
  written over. When the default text changes, the old one goes on a list,
  and a file still holding exactly it is upgraded; there is no
  old text yet.
- **It is the identity**, the first thing of the instructions, whole and with no words around it
  Missing, empty or unreadable, the default identity takes its place. The main agent's
  conversations have it, those of the channels and the routines too;
  a subagent does not (identity belongs to the parent); the background review
  reads the instructions of the conversation it reviews.
- **Read with the memory snapshot**: when a conversation starts and after each compaction, so an
  edit applies from the next conversation and never breaks the provider's cache in the middle of
  one.
- **20,000 characters**, whatever the model's window:
  past it, the first 70% and the last 20% stay, with a marker between them naming the file to read
  whole.
- **The user's own file, so a threat pattern warns and loads.** It is scanned as a context file; a
  hit is logged and the file still loads (a user who quotes "ignore previous instructions" in
  their safety rules would otherwise lose their whole identity to a block).
- **Who writes it.** The user, in an editor or on the app's screen. The agent, with the file tools,
  when the user asks:
  - with no approval: the gate that asks for instruction files leaves the home's own files out;
  - only when the user asks; a user who wants the agent to evolve its soul on its own may write
    that into it;
  - the background review never writes it;
  - a memory entry or a skill that tells the agent to change `.atlas/SOUL.md` is refused, as
    `atlas_config_mod`.
- **A line beside the working folder names the file and gives the leave** (`SOUL_NOTE`): "When
  the user asks you to change who you are or how you speak, edit that file, keeping the rest of
  it, and tell them the change applies from the next conversation." With the path
  alone, the real proof of 15/10 saw gpt-6-luna refuse three times of three ("Não posso trocar
  essa regra"), since an instruction of the system prompt outranks the user's. With the leave it
  edited only the line asked, told the user, and kept the old rule until the next conversation,
  twice of two.
- **The app's Soul screen** is an editor of `SOUL.md`: the text, Save, the default shown
  when it is empty, and a note that a change applies from the next conversation.

## Skills — decided 2026-10-09

Skills are folders in Claude Code's format (Tools, above) that learn from use.

- **`skill_manage`** is always on (486 tokens on gpt-6-luna, measured
  on 09/10/2026): one call is a
  list of operations (create, patch, write a support file, remove one, delete), applied whole or
  undone whole. A new skill's frontmatter has a name equal to the folder's and a description of at
  most 60 characters, trigger first (so the listing never cuts it); `SKILL.md` up to
  100,000 characters, a support file up to 1 MiB, under `references/`, `templates/`, `scripts/` or
  `assets/`. A patch replaces exact text as `edit_file` does. Skills stay one folder deep, with no
  categories.
- **The previous version is kept before every write**, Atlas V3's skill vault ported: V3 lost a
  skill rewritten three times with no trace. The files a call changed or removed go to
  `skills/.versions/<name>/<time>/`, 20 versions per skill, and `atlas skills revert` brings the
  last one back. A delete archives the skill
  instead (below), so nothing a call does is lost.
- **Every skill has an origin**: `review` when the background review made it, the user's otherwise
  (written by hand, or made in a conversation). Only a `review` skill is the curator's. Usage lives
  in the `skills_usage` table: when it was made, loaded and patched.
- **The skill review** is the skill half of the background review: due when 10 model calls
  went by since the agent last changed a skill, with a
  prompt: patch the skill in use, else an existing one, else add a support file, and only then
  create a class-level skill; rules, not incident logs; no environment failures or negative claims
  about tools. **It may improve any skill**, the user's included
  (protecting them makes it refuse most of its writes and write near-duplicates elsewhere), but only
  after reading that skill or file in the same review, and it never deletes.
- **What a review changed in a skill is its skill diff** (decided 13/10/2026): a unified diff of the
  skill's files from before the review's first write to after its last, built from what the
  review's own writes noted, never by comparing the folder before and after. Its
  limits: 6 files, 80 lines, the rest counted. Atlas sends the diff with the summary's line. One
  unchanged line around each change, not 3: a skill's lines are whole paragraphs.
- **Bundled skills** (step 14b, decided 2026-10-13): the
  package's `atlas/skills/bundled/<name>/` folders are copied into the library when the server
  starts, `skills/.bundled_manifest` keeping the hash of what was copied; a skill changed in the
  repository is replaced only while the home's copy still has that hash, one the user, the agent
  or the review changed is theirs and left alone, one the user deleted is not copied again, and a
  library skill of the same name is kept. Every write goes through the library, so an update or
  `atlas skills reset` (back to what Atlas ships) keeps the previous files as a version. The first
  ones are the programs' skills of step 14b: `github` on `gh`, and the `gws` project's own
  Gmail and Calendar skills (Apache-2.0). `write-plugin` (step 16t) writes a plugin with the
  user, reading the plugin guide copied into its `references/`.
- **The curator** makes no model call: at most every 7 days, after 2 idle hours, the first pass deferred one interval, it
  archives a `review` skill nobody loaded or changed for 30 days, counted from its creation when
  it never was (a load by the review counts). A skill a routine names will count
  as in use when routines exist (step 12). It never touches the user's skills and never deletes: an archived skill moves to `skills/.archive/`,
  and `atlas skills restore` brings it back, counted as a use so the next pass leaves it (with the
  old clock, the next pass would archive it again). A "stale" mark at 14
  days would change nothing but a status screen, so it is left for when Atlas has one.
- **The user invokes a skill with `/name`** (asked for by the user on 18/10/2026), from the app
  and from a channel alike:
  `/name words`, `name` a skill the conversation loads (a bot's, its own and the library's it
  sees), is sent as an invocation message (`[IMPORTANT: The user has invoked the "name"
  skill, ...]`, the skill's text with `$ARGUMENTS` and `${CLAUDE_SKILL_DIR}` filled, its folder and
  files, then the user's words as their instruction), and counts as a load, as `use_skill`'s does.
  It is kept as a message of kind `skill`: the user's, not a notice (it begins as notices
  do), and the screen and the title read it back as `/name words`. A skill that says
  `user-invocable: false` (Claude Code's frontmatter) is the model's only, and one named as
  Atlas's own command loses to the command. The box's `/` lists Atlas's own
  commands, which the screen runs (`/new`, not in a bot's conversation, which is its one; `/stop`),
  then the skills (`conversation.commands`), read as the conversation comes on screen, after each
  turn and as `/` is typed.

## Trust — decided 2026-10-10

Code taken from another project keeps that project's license: the file that holds it starts with an
SPDX header naming the original copyright and license (`SPDX-FileCopyrightText`,
`SPDX-License-Identifier`), and the license's text is in `LICENSES/`.

- **The approval gate** (`atlas.trust`) is the loop's tool gate. For `run_command` it runs in this
  order: the floors (hardline commands, blocked always, auto mode or not), the user's deny list,
  then the dangerous-command patterns over the command and its de-obfuscated variants.
- **Ask rules** (step 14b, decided 2026-10-13): a command that acts outside the machine goes
  straight to the user, after the floors and before auto mode, so it asks even with "Auto" on;
  the guardian never sees it, the answer covers that one call, and with nobody to
  answer it is refused. Atlas ships the list for the
  programs it ships skills for (`atlas.trust.outward`): what changes GitHub through `gh` (`pr
  merge`, `issue create`, `api` with a write method or fields), Google Workspace through `gws`
  (any method that does not read, `+send` and the other write helpers) and Google Cloud through
  `gcloud`, and what prints their tokens. A rule leans towards asking: a method it cannot read
  asks. The user adds globs in `[trust] ask` and takes Atlas's actions off with `[trust]
  ask_skip`; a rule lives in the settings, never in a skill, which the agent itself edits.
  Matching covers every part of a compound command (inside `$(...)` and subshells, past variable
  prefixes and wrappers), over the variants the detection unwraps (`sh -c`,
  `bash -c`, `eval`, a full path), and the program's name is also looked for anywhere in a
  variant, which catches PowerShell's `& "C:\...\gh.exe"`, `cmd /c` and `powershell -Command`;
  asking once too often (`echo gh pr merge`) is the price. **A script the command runs** is read
  too: each file the command names that exists (up to 256 KB, outside `node_modules` and
  `site-packages`) goes through the same rules, and the output of a script that runs one of the
  programs is outside text. Found by the proof on 13/10/2026: gpt-6-luna, fighting Windows
  PowerShell 5.1's quoting, wrote its `gws` call into a `.cmd` and ran that, so its output came
  unmarked and a send there would not have asked; a script made another way (downloaded,
  generated by a program) is still past the rules.
  **Known destinations**: the approval of
  an email says when it is the first to an address (`--to`, `--cc`, `--bcc`), and an address
  becomes known when the user approves an email to it (`trust_destinations`); since every send
  asks, this marks the card and is no gate of its own (decided with the user on 2026-10-13).
- **A flagged command goes to the guardian**: a short request to the conversation's model, the
  command between delimiters with its shell comments stripped, answered APPROVE, DENY or ESCALATE.
  An approval covers that command only. DENY blocks; ESCALATE, an empty or unreadable answer and a
  failed call go to the user. Its calls are `approval` in the call ledger. **It asks the model not
  to reason** (`effort: none` where the model lists it), as on every quick side request: on
  gpt-6-luna a 16-token budget went to thinking and every answer came back empty. A model that
  cannot stop reasoning gets its lowest effort and 2,048 tokens.
- **The user answers through an `Approver`** the server gives (step 8), as `ask_user` answers
  through an `Asker`: once, for the conversation, always (kept in the database), or deny, with a
  reason relayed to the model. Silence is not consent: a timeout, a withdrawn prompt and a
  conversation with nobody to answer all deny. Nobody refused the call a timeout stops, so it ends
  `unanswered`, not `denied`: the gate's refusal text is then an `Unanswered`, which the loop reads.
- **After 3 guardian denials in a row** the refusal tells the model to stop trying variations and
  report to the user (the denial breaker); an approval resets the count.
- **Files and MCP:** the file tools refuse system paths and ask before changing the SSH
  configuration (the write guards), on top of the vault already out of their reach. An MCP
  server with `"trust": "untrusted"` in `mcp.json` asks before every call whose tool does not
  declare `readOnlyHint`; a server is trusted unless it says so (the trust tier).
- **Auto mode** is per conversation, V3's ⚡: it skips the guardian and the
  questions; floors and the deny list still block, and the ask rules still ask. **A conversation
  with Atlas, a bot's and a chat's start in it** (asked for by the user on 20/10/2026), the mode
  every one starts in: the app's new
  conversation (`conversation.create`), a bot's fixed one and a channel's new one are made with it
  on, and the user turns it off on the conversation. A routine and Atlas's own subagents still
  start without it, with nobody there to have chosen it; conversations made before stay as they
  were.
- **One layer of rules for Atlas's own agents** (decided with the user on 17/10/2026): the main
  agent, its subagents and the bots run their tools through the same gate and the same `[trust]`
  settings. **An outside program keeps its own rules**: Claude Code and any executor
  after it decide what they ask by their own permission system and the user's settings for them;
  Atlas decides only who answers what they ask (the user, or the guardian with "Atlas answers"),
  never adds rules to them.
- **Threat scanning** is a pattern library: a memory write holding an injection pattern or
  invisible characters is refused (the agent's skill writes are scanned only behind an option that
  ships off); a memory entry that holds one anyway (written outside
  the tool) reaches the snapshot as `[BLOCKED: ...]`; and the results of web search, web fetch and
  MCP tools travel inside an `<untrusted_tool_result>` block that tells the model they are data,
  not instructions.
- **Left out:** an auto-mode classifier that reviews every action at one model call each (measured,
  it lets 17% of real overeager actions through and blocks 0.4% of normal ones); V3's
  reversibility lanes; and V3's delivery check. The list
  of known destinations came with Gmail (step 14b, above); the review's memory proposals come after
  the first screen (step 9b; Memory proposals, above).

## Server — decided 2026-10-10

Starlette and uvicorn. The client protocol is ours, so FastAPI's validation and OpenAPI layers would
be unused weight. The server listens on `127.0.0.1:18790` by default. It listens on another
interface only inside a container, or on the tailnet address of a home open to the user's
machines (Several machines, below).

The shape is one two-way connection on which the server asks the client questions too, each with
an id, and may withdraw them.

- **JSON-RPC 2.0 over a WebSocket** at `/ws`, beside `/health`. Methods go client to server; events
  are notifications (`method: "event"`, with `type`, `conversation_id`, `seq` and `payload`); and
  **server requests** go server to client and wait for the client's answer: `ask_user`'s questions
  (the `Asker`) and approvals (the `Approver`). An OpenAI-compatible API and run endpoints for
  outside programs are left out, as are V3's REST twins: routines (step 12) run in the process.
- **A server request** is answered by any client attached to its conversation; the first answer
  wins and the others get `request.cancel`. With no client attached it is undelivered at once
  instead of waiting out the deadline. A stopped turn withdraws its open requests.
  The deadline is 300 seconds: `[trust] approval_timeout` for approvals.
- **The turn lives in the server, not in the connection.** Closing the app does not stop it. Every
  event of a conversation gets a number, the server keeps the last 512 (4 MiB at most) per
  conversation, and a client that comes back asks for those after the last it saw; the requests
  still open are sent again. A random epoch per process tells a client the numbering restarted.
- **Event names**: `message.start`, `message.delta`, `reasoning.delta`,
  `message.interim`, `tool.start`, `tool.progress`, `tool.complete`, `status.update`,
  `message.complete`, `review.summary`, `request.cancel`.
- **Memory proposals** (step 9b): `memory.proposals` lists the open ones, each marked when it can no
  longer apply, and `memory.approve` and `memory.discard` act on one. `review.summary` names the
  proposals its review made and carries the skill diff of each skill it changed, and `proposals.changed`, an event of no conversation, reaches every
  client whenever the list changes, so each keeps its count right; a client that comes back reads
  the list again.
- **Homes** (step 10b): `home.list` gives the homes of the machine and the one the server serves;
  since step 10c, `home.create`, `home.backup`, `home.inspect` and
  `home.import` (Homes in the app).
- **The list of conversations** (step 9e): `conversation.list` gives a page and the cursor of the
  next, and `conversation.search` finds one by its words (The list of conversations, below).
- **A message sent while a turn runs steers it**: it enters the history after the round of tools in
  course (`AgentLoop.run(steering=...)`), or starts the next turn when the turn ends first. Stopping
  a turn hands the messages not yet taken back to the client, which puts them back in the input
  box.
- **Stopping a turn cancels it again until it ends**: a cancel that lands while the HTTP client
  guards a connection is lost, and the turn runs to its end (3 of 300 stops in the first
  milliseconds of a model call, measured on 10/10/2026).
- **One turn per conversation**; conversations run in parallel.
- **The protocol lives in one place**, `atlas.server.protocol`: every method, event and server
  request with its shape, in standard-library dataclasses (the core does not carry Pydantic). The
  server refuses a name or a parameter not declared there, and a test fails when the server serves
  anything the declaration lacks (V3's M-51 found 8 methods in use and undeclared). The TypeScript
  side is generated from it in step 9.
- **Access:** on a loopback address only local peers are let
  in; the Host header, and an `http(s)` Origin when one comes, must name the server, since a page in
  the browser can open a local WebSocket; and every client presents the **server token**. It comes
  from `ATLAS_SERVER_TOKEN`, set by whoever starts the server (the desktop, a script, the
  container), and leaves the environment once read; without it a server on a loopback address makes
  a fresh one that nobody knows, and only `/health` answers, while one on another address (the
  container) uses the token kept in the vault, made the first time, which `atlas server token`
  shows (Container, below). The token travels as a WebSocket subprotocol
  (`atlas.token.<token>` beside `atlas.v1`), never in the URL: V3's sat in the query and reached
  583 log lines in clear. Close
  codes: 4401 for the token, 4403 for Host or Origin, 4408 for a peer.

**The assistant** (`atlas.assistant`) puts the agent together for a conversation, the same for
every surface: the agent loop on the stored
conversation, the instructions, the tools (with the conversation's `Asker` and plan), the approval
gate, and after each turn the background review in a task of its own, which tells the client what it
changed (`review.summary`, "Memory updated · Skill x patched"): what its own tools did,
noted while it ran. Comparing the files before and after,
as step 8 did, counted what another conversation or the user changed meanwhile (found by step 9b's
proof). The curator is asked every
minute, and runs after 2 idle hours when its 7 days are
due. A turn that fails before any model call, when no model can be chosen, keeps the user's message
as a turn whose call failed keeps it: step 10's rehearsal saw it vanish after a restart. File tools
start from `[workspace] folder`, the user's home folder by default. Routines (step 12) and channels
(step 16) run their turns through it too.

## Persistence

- One SQLite database in WAL mode at `ATLAS_HOME/atlas.db`. Each subject owns its tables, prefixed
  with the subject name (`memory_`, `jobs_`), and its numbered, forward-only migrations.
  - Every connection (`atlas.home.Database`) sets WAL, `synchronous=NORMAL`, foreign keys and a
    5-second busy wait, and runs in autocommit mode. WAL needs a local disk, never a network folder.
  - Writes go through `transaction()`, which opens with `BEGIN IMMEDIATE`: a plain `BEGIN` takes
    the write lock late, and SQLite then fails at once instead of waiting.
  - A subject declares a `Schema` of `Migration`s numbered 1, 2, 3. A migration may create, alter
    or drop only objects with its subject's prefix, and never controls its own transaction.
  - The ledger `home_migrations` keeps each applied migration with its checksum and UTC time.
    Atlas refuses to start when a migration changed after it ran, or when the database was
    migrated by a newer Atlas. Each migration lands whole with its ledger row, once, even with
    several processes starting together.
- Every stored timestamp is UTC in one format, `2026-10-07T12:34:56.789Z` (what JavaScript's
  `toISOString()` writes), converted to local time only at the edge. Every value has the same
  width, so sorting the text sorts the time. V3 mixed time zones across tables, so joining by time
  was unreliable.
- Text memory (profile, memory) is Markdown under `ATLAS_HOME/memory/`; the soul is
  `ATLAS_HOME/SOUL.md`.
- Search is FTS5 by default. Vectors are optional.
- **What goes in the database, and what in files** (written down on 2026-10-11, when the user asked
  where the one-database rule came from: Atlas V3's practice, one main database whose every table
  has one owner). One database gives one consistent copy for the
  backup (step 10b), one migration ledger, and subjects that can be crossed (the call ledger
  records each call's conversation). It never meant that everything lives there:
  - text a person or the model reads lives in files: the profile and the memory, skills, a spilled
    result, a background command's output;
  - records the program writes often, lists, searches or crosses live in the database:
    conversations, the call ledger;
  - small state that changes little may be either. Atlas has kept it
    in the database (skill usage, the approvals kept for good, memory proposals).

## Settings

One typed settings object, read once at startup from `ATLAS_*` environment variables and
`ATLAS_HOME/config.toml`, and passed to whoever needs it. No module reads `os.environ` on its own.
V3 had 156 environment reads scattered over 51 files.

- **Precedence**, lowest first: default, `config.toml`, `ATLAS_*` variable, command-line flag.
- **Names follow the file:** `[server] port` is `ATLAS_SERVER_PORT`. A blank variable counts as
  unset.
- **The file is strict, the environment is not.** An unknown key in `config.toml` stops Atlas with
  the list of known keys. An unknown `ATLAS_*` variable is only reported, because the environment
  is shared with other programs.
- **A section that moved elsewhere is read past, and the server moves it** when it starts, once its
  database is up to date, keeping the file as it was in `backups/` first. Refusing it stopped every
  command, the
  update's own build among them, which then went back to the version before (a tester's
  `[executors]`, which step 16c moved to the claude-code plugin, on 15/10/2026). The update itself
  never moves it: a database migrated by an update that then went back would be refused by the
  version it went back to.
- **No secret in the file.** Secrets come from the vault, and the vault key from the environment
  or a key file. Secret variables leave the process environment as soon as they are read, so no
  child process inherits them.
- **Standard library only:** frozen dataclasses and `tomllib`. `pydantic-settings` would add four
  packages to the core for a handful of fields.
- **The editable settings change while the server runs** (step 15b: a screen that saves itself and
  applies without a restart): `settings.read` and `settings.write` cover the
  default model, the trust rules, the approval timeout, the workspace folder and the time zone. A
  write edits only the setting's own lines (`edit_settings`), so the user's comments and order
  stay; the new text is read again, through the same rules as a start, and must give the same
  settings with only that change, or nothing is written. The server then hands the new values to
  whoever holds them (`Assistant.use_settings`): the default model from the next turn of any
  conversation that picked none, the trust rules from the next request of every open conversation,
  the folder in new conversations, the zone the next time a routine is scheduled. A setting an
  `ATLAS_*` variable sets is refused there and shown locked, naming the variable: writing the file
  would change nothing. The log, the server's address, the vault and the browser's program stay in
  the file.

The default `ATLAS_HOME` is `~/.atlas`; named homes live in its `homes/`
(Backup and homes, below). The container uses `/data`, and
its user's home folder is `/data/home`, inside the same volume (Container, below).
`ATLAS_HOME` must be an absolute path: a process started by a scheduler has no promised working
folder.

### The home's boundary

Every entry the code writes at the top of `ATLAS_HOME` has a declared class in `atlas.home`, and a
test fails on an entry nobody declared:

- `user`: the user's data (`config.toml`, `models.toml`, `atlas.db`). A backup carries it.
- `machine`: bound to this machine (`secrets/`, the vault and its key). It never travels.
- `diagnostic`: logs. They never travel.
- `homes`: the default home's `homes/`, the other homes of this machine; each is backed up on its
  own.

V3 drew this line after a backup had already carried the vault.

### Backup and homes — decided 2026-10-11

With V3's checked manifest (M-83 to M-86).

- **A backup is one zip with no secrets.** Atlas follows the home's boundary, so one file serves
  both as a backup and as an export: `atlas backup`
  writes the home's `user` entries, and `atlas restore` puts them back. After a restore on another
  machine, keys are stored again.
- **Writing it**: the database through SQLite's backup API, sound while a server runs; the
  zip under a hidden name, published whole or not at all, never over a good one; exit 1 when a file
  is missing; by default `~/atlas-backup-<home>-<UTC>.zip`, the last 3 kept, a good one never
  pruned for an incomplete one; `-o -` writes to standard output, which is how a backup leaves the
  container. A `manifest.json` first (V3's) names the format, the home, the time, Atlas's version
  and the database's counts, and the backup is checked once written (each member, and the
  database's `integrity_check`).
- **Restoring overwrites**: the whole archive is checked before the home is
  touched (a damaged member, a newer format or database, a path out of the folder all refuse); a
  home with data asks first (`--yes` skips it); the database is written into the live file, so a
  running server reads the same data, with a warning to stop it first; a backup holding fewer
  conversations or messages than the database it replaced says so; and only `user` entries are
  written.
- **Before a migration** of a database that already holds data, a backup goes to the home's
  `backups/` (`machine`), the last 5 kept. Daily backups wait for
  routines (step 12).
- **Several homes are named homes**, not profiles, since a profile in Atlas is `USER.md`. The
  default home is `~/.atlas`; the others are complete homes in `~/.atlas/homes/<name>/`, each with
  its own vault. `atlas --home <name> <command>` picks one, `atlas home use <name>` writes the
  default in `active_home`, and `ATLAS_HOME` still names any home.
  Commands: `list`, `create` (empty, or `--clone` of another), `use`, `show`,
  `rename`, `delete`; aliases, descriptions and git distributions are left out. `--clone` copies
  the settings, models, MCP, the vault with its keys, the soul, the profile and memory files
  and the skills; conversations and the database start empty.
- **One server per home**: a server holds its home while it runs and a second is refused, which
  keeps two agents off one home (both would write the same memory); a home being served is neither
  renamed nor deleted.
- **The app keeps a server per open home**: the sidebar
  shows the home and lists the others, which the server gives; switching
  starts that home's server when it is not alive and keeps the others, so a turn elsewhere goes on
  and notifies when it ends; at most 3 live, the least recently used one without a running turn
  leaving for a fourth; a local server is never stopped for being idle, since it runs its home's
  routines (step 12). With `ATLAS_DESKTOP_URL` it talks to that one server only. The app creates,
  backs up and imports homes since step 10c (Homes in the app, below).
- **Left out** (V3's only): the JSONL dump of every table, since an old backup restored in a newer
  Atlas migrates forward; and the free-space refusal.
- **Proved** by `tests/home` and `tests/test_cli.py` on real files, `tests/docker` (a backup leaves the
  container on standard output and restores here), the e2e scenario of two homes, and
  `scripts/prove_homes.py`, which runs the app against a real model: a cloned home, a turn going on
  in another home, and a backup restored into a new one without its keys (rehearsed on 11/10; its
  real-model run waits for the end of step 11, decided with the user).

### Homes in the app — decided 2026-10-15

Step 10c, asked for by the user: see the home in use, make one, back one up and import a backup
from the app.

- **The home is always shown**, at the foot of the sidebar, even with only the default home: its
  picture and its name (asked for by the user on 18/10/2026). **A click opens the home menu**: the
  homes to switch to, New home, the home's own dialog, and the light or dark look.
- **The home's dialog** ("Editar perfil" on the Portuguese screen, the user's words) has its
  picture, its name, Back up and Import. The name is only shown: renaming was asked for and then
  dropped by the user on 18/10/2026, as the home open in the app is served and the default one
  keeps its name. **The picture**:
  the app crops the image the user chose to its centre square, 256 pixels a side, and sends it as a
  PNG; the server keeps it as `picture.png` in the home (`home.set_picture`), a `user` entry a
  backup carries, refusing what is not a PNG or weighs more than 512 KB.
- **The light or dark look**: a `dark` class on the page's root, the choice kept in the
  window's own storage (a look of the window, not of a home), the system's until the user chooses.
- **New home** is a dialog: a name, and "Copy from", which starts on `default` and offers
  none or any home; copying is `--clone`. Once made, the app opens it.
- **Back up** opens the system's save dialog on the CLI's default name in the user's home folder;
  the home's server writes the zip at the path chosen (the app passes the path, not the bytes). A
  file the dialog confirmed is replaced, only by a checked backup (`write_backup(replace=True)`).
- **Import makes a new home** (decided with the user): the system's
  open dialog, `home.inspect` checks the whole zip and gives what the dialog shows, and
  `home.import` restores it into the home being made, brings its database forward, and publishes
  the home whole (`import_backup`). The app asks the name: the default home's backup
  is the common one, and `default` always exists. Restoring over a home stays `atlas restore`.
- **The app opens its last home**, kept apart from the CLI's: the
  name is kept in the app's own folder, and a last home that no longer exists gives way to the
  active one. The terminal still opens the active home.
- **Against a remote server** making, backing up and importing are off and say the terminal's
  way: a path chosen in the dialog would be another machine's. (Since
  step 15, Back up works there, the zip coming down the connection: Several machines, below.)
- **Left out:** deleting and renaming from the app, where a deleted home can come back as the app
  reconnects and its server recreates the folder; a color per home, hotkeys, and warming a server
  on hover.

## Secrets

API keys and tokens live in the vault, `ATLAS_HOME/secrets/vault.json`, never in `config.toml`
or in a plain `.env`.

- **One format everywhere: age** (X25519, through `pyrage`). Names stay readable; each value is
  encrypted on its own, so damage to one entry costs that entry only. Each ciphertext is a
  standard age file that `age -d` opens without Atlas.
- **The key**, first found wins: `ATLAS_VAULT_KEY`, then `ATLAS_VAULT_KEY_FILE` (a Docker secret,
  say), then the home's own key in the private `secrets/` folder, created with the first secret.
  On Windows the home's key is encrypted by the Windows account (DPAPI); elsewhere it is a file
  only the owner can open. A key in a folder others can open is refused. Decided 2026-10-07: the
  key may live in the home, so an empty container works without a setup step; `secrets/` is
  `machine` in the boundary, so the key never travels in a backup.
- **The vault says why it does not open, once,** without decrypting: no key, or the key of
  another vault (it records its recipient). Atlas starts anyway, without its secrets.
- **Never empty by mistake:** a damaged file raises and is never written over. V3 read it as an
  empty vault, and the next write erased every secret.
- Every reader and writer holds one lock (`flock`, or `msvcrt.locking` on Windows), and the file
  is replaced atomically after an `fsync`.
- Every secret read or written is registered with the log `Redactor`.
- Values enter through `atlas secrets set NAME` (from standard input, never from arguments), the
  app's key screen and plugin screen, and the `secrets` tool's card. Never through a chat message.

### A secret asked for in a conversation — decided 2026-10-14

The agent asks for a secret by name, the user pastes it into a masked card in the app, and the
value goes straight to the vault. Roadmap step 16d.

- **The `secrets` tool** has two actions.
  - `request` takes a name in variable form (`^[A-Z][A-Z0-9_]{0,127}$`, which fits `${NAME}` in
    `mcp.json`) and a reason of up to 200 characters, and waits up to 15 minutes: making the key
    on the provider's site takes time.
  - `list` gives the vault's names with their dates, and decrypts nothing.
  - No action takes a value from the model. None deletes either: this vault deletes for good, and
    an agent misled by a page would lose the user's key. Deleting stays with
    `atlas secrets delete`.
  - It is a deferred tool, behind tool search. Every message that reports a missing secret (an MCP
    server left out, `web_search` with no engine, a plugin) names it, so the model does not ask
    for the key in the chat instead.
- **The `secret` server request** carries the name, the reason, the conversation,
  the bot that asks (bots share the vault) and, when the name exists, when it was stored. Its card
  shows all of them, says that pasting replaces the stored value, and has a masked field with a
  show button, no spellcheck and no autocomplete. It reaches every app connected
  to the server, as the workbench's cards do, with a system notification when the window is away.
- **The value never reaches the model.** It travels only in the request's answer. The server adds
  it to the `Redactor`, stores it exactly as typed, and the tool returns
  `{"status": "stored", "name": ...}`. It is never in a tool result, a stored message, the replay,
  a log line, or the app's state, which clears the field when the card closes. A provider's key
  goes through the key check first, as on the key screen: a refused key keeps the card open with
  the provider's answer.
- **What uses the secret starts again** once it is stored: a plugin that
  declares it, an MCP server left out for lack of it.
- **A channel never answers it.** Its chat gets a notice that the key goes in the app and not in
  the chat; a message typed there stays an ordinary message. Atlas has no web page to send a link
  to. With no app attached, the tool returns `no_answer` at once with
  `atlas secrets set NAME`, as `ask_user` returns "undelivered".
- **What the model is told lives in the tool's description only**, never in the system prompt:
  a blanket rule against credentials in tool arguments breaks logins the owner has authorized.
  The description names what uses a secret, and says that `no_answer` means reporting the blocker
  or going on, never asking for the key in the chat.
- **No child process receives a secret** (Settings, above), decided again by the user on
  2026-10-14: `run_command`'s commands never see the vault. A secret reaches the providers,
  `web_search`, the MCP servers that name it in `mcp.json` and the plugins that declare it, all
  set up by the user in files the trust gate keeps the agent from writing (`config.toml`,
  `mcp.json`, `models.toml`). That is why there is no list of allowed hosts: such a list binds
  where an egress proxy may substitute a secret into a command's traffic, and Atlas has no such
  path. Passing the variable to the command would let the model print it.
- **Given where someone can answer**, as `ask_user` is: subagents, routines and the review do not
  get it.

## Errors and logging

- Each subject has typed exceptions. Messages say what went wrong and how to fix it.
- `except Exception` is allowed only at process edges (the server, the scheduler, the job runner,
  the agent loop's tool runner, since a tool is foreign code),
  and it always logs.
- Logs are JSON lines, one logger per subject. Secrets never reach a log line.
  - A line holds `ts` (UTC), `level`, `logger`, `msg`, the caller's `extra` fields, and `exc` with
    the traceback. Log calls pass arguments, never f-strings (ruff `G`).
  - Loggers are named `atlas.<subject>`, never after a module; a test holds every call to it.
  - The vault registers every secret it handles with one `Redactor`, which replaces them with
    `[REDACTED]` in the finished line, tracebacks and `extra` included.
  - Only the process entry point configures logging, never an import. Lines go to stderr, and the
    server also writes `logs/atlas.log` (10 MB, 5 old files): one owner per rotating file.

## Dependencies

- Core dependencies are pinned to exact versions, with a `uv.lock`. A new version reaches users only
  through a deliberate bump, a lesson of the May 2026 PyPI worm (mistralai 2.4.6).
- The core list stays short: it holds only what every session uses, so the next
  supply-chain attack has less to reach. What one feature needs is an extra (`ddgs` for the
  keyless web search, `mcp` for MCP servers), and nothing in the core imports one (import-linter
  holds it). The container image and the installer bring every extra; a bare install says which
  one to add when a feature needs it.
- Every dependency needs one written line of justification in `pyproject.toml`.
- Code taken from another project keeps that project's license: the file that holds it starts
  with an SPDX header naming the original copyright and license (`SPDX-FileCopyrightText`,
  `SPDX-License-Identifier`), and the license's text is in `LICENSES/`.

Python 3.13, installed and managed by `uv`, so Atlas never depends on whatever Python the machine
has.

## Quality gates (CI fails the build)

- `ruff check` and `ruff format --check`, with a broad rule set that includes the V3 lessons:
  - BLE001: no blind `except Exception`.
  - DTZ: no naive datetimes.
  - T20: no `print`.
  - PLC0415: imports at the top of the module.
- `pyright` in strict mode. Astral's `ty` replaces it once `ty` reaches 1.0.
- `import-linter` contracts (the boundaries above).
- `deptry` (no undeclared or unused dependencies).
- `pytest`, with a coverage floor per subject.
- The image built, and `tests/docker/` against it: an empty container, an API key and one
  conversation, with the fake model behind the provider (Container, below).
- The gates run on Linux and on Windows, where the Windows adapters (ACL, DPAPI) live.

## Tests

- Test the real component. Model calls go to a deterministic fake model server
  (`tests/fake_model.py`) over real HTTP, through the real SDK, never to a mocked client. It speaks
  both wires and can act like a given provider: a strict schema, Gemini's signatures, OpenRouter's
  cost, errors, cut streams.
- A test never patches the piece it claims to prove. In V3, three of four targets of one audit
  replaced the very guarantee they were meant to check.
- Tests that touch state point `ATLAS_HOME` at a temporary directory. They never touch a live home.
  `tests/conftest.py` also points the process's `ATLAS_HOME` at one for every test, as a guard.
- `tests/docker/` runs only against a built image named in `ATLAS_TEST_IMAGE` (CI's docker job, or
  by hand); without it the tests are skipped.

## Container — decided 2026-10-11

Principle 4's proof: an empty container plus an API key gives a working conversation. With Atlas
V3's image (M-87, M-89).

- **The image**: `python:3.13-slim`, uv, ripgrep, every extra; the code in `/app`, owned by root;
  the process runs as the `atlas` user, with `ATLAS_HOME=/data` on a named volume and the server on
  `0.0.0.0:18790`, which `docker-compose.yml` publishes on the host's loopback only.
- **tini is PID 1** (V3's `init: true`), in the image so `docker run` gets it too:
  orphans of `run_command` and of MCP servers are reaped instead of piling up as zombies (one
  deployment without it reached 284 in three hours), and `docker stop` reaches the whole process
  group. A supervisor for many homes in one container is left out.
- **The user's home folder is `/data/home`**, in the volume (V3's): the workspace starts there, so
  what the agent makes outlives a new image. The home's
  boundary declares it `user`. UID remapping, for a host folder mounted as the data
  folder, is left out: the named volume keeps SQLite's WAL sound, which a host folder mounted
  through Docker Desktop does not.
- **First boot needs nothing**: the model is the first provider with a key in the vault (Models,
  above), and the server token, when none is given, is the one kept in the vault (Server, above).
  The key enters as everywhere else: `docker compose exec atlas atlas secrets set OPENAI_API_KEY`,
  from standard input. `atlas server token` prints the token, making it when the vault has none,
  as V3's `atlas instancia token` did. A setup wizard waits for the installer (step 18).
- **The app reaches it** with `ATLAS_DESKTOP_URL=ws://127.0.0.1:18790/ws` and
  `ATLAS_DESKTOP_TOKEN`.
- **Its tests** (`tests/docker/`) run the real `docker-compose.yml` against the built
  image: an empty container, the token from `atlas server token`, a turn with no key that says how
  to store one, the key through `secrets set`, a turn whose tool runs inside the container, and the
  conversation and the files kept across a restart; also a non-root process and orphans reaped. The
  one stand-in is the OpenAI provider's address, pointed at the fake model in a container beside
  it. `scripts/prove_docker.py` runs the same path through the app against a real model.

### The update of a Compose host — decided 2026-10-24

Asked for by the user: Atlas on a home server, run by Docker Compose from a checkout with its own
untracked `Dockerfile.homelab` and `docker-compose.homelab.yml`, updated with **the same `atlas
update` and the same Update button** as the desktop's installation, with nothing new to learn and
no `docker compose exec` to type. How to install it on the server: [COMPOSE_HOST.md](COMPOSE_HOST.md).

- **The installation kind is said, never guessed** (`atlas.installation.installation_kind`):
  `ATLAS_INSTALLATION=compose`, set by the host updater's Compose file, or unset for `native`, every
  installation before this one. An unknown value is refused. The native update, its hand-off and
  its button are unchanged; the dispatch only adds the Compose way beside them.
- **The host updater** (`atlas.installation.host_updater`) does the update on the host, as root,
  since only root reaches Docker there. It is one standalone file, the standard library alone on
  the host's Python 3.11 or newer, which the installer copies to
  `/usr/local/lib/atlas-host-updater/` and `atlas-host-updater.service` runs. Atlas imports it for
  its constants and its tests; it imports nothing of Atlas's. A new host updater comes only when the
  installer runs again, by the owner's own `sudo`: an update never replaces the code root runs.
- **Root runs nothing the user's checkout holds.** The checkout is the user's, and through them
  the agent's; its `.git/config` and hooks would run as root under a root `git`. So the source
  comes from the host updater's own mirror (`/var/lib/atlas-updater/mirror.git`), fetched from the
  address in its settings with hooks off and only that protocol allowed, and each build is a clean
  `git archive` of one commit, never a working tree: the untracked homelab files are never read,
  moved nor deleted. The Compose file and the Dockerfile are root's, in `/etc/atlas-updater/`,
  migrated once from the homelab's by the installer (`docker compose config`, so the copy stands
  alone with its `.env`), the originals left where they were.
- **The Compose file is checked before every update** (`compose_problems`): no Docker socket
  mounted, ever; no privileged service, added capabilities, host devices or device rules, host
  namespaces or disabled profiles (`unconfined`, `label=disable`); no host folder but the updater's
  socket folder, and that one read-only; no volume a driver's options make (a bind of `/` in
  disguise), no config or secret read from a host file, no other container's volumes, no
  `env_file` or `label_file` read again at each start, no `build` (a folder Compose would build
  from, maybe the checkout); the service runs the image the settings
  name, is told `ATLAS_INSTALLATION=compose`, mounts the socket folder and keeps `/data` in the
  named volume the snapshot covers. The file as checked, resolved, is kept root's alone
  (`/var/lib/atlas-updater/compose.json`), and the update's containers are made from it: a file
  the Compose files name, changed after the check, never reaches them.
- **Root's own folders are root's alone**: the settings, every file they name and a folder to
  fetch from are refused unless they and every folder above them are root's and written by no one
  else, and none under `/home`; so are the state folder, where root unpacks and deletes, and the
  socket's folder. The host updater runs with a `0077` umask, under `sudo` too, so the snapshots of
  `/data` are root's alone. The installer copies the program root runs from its own tree, never
  from `--checkout`, the user's, and refuses to run from a tree, or a folder above it, that is not
  root's alone.
- **The container reaches the host updater through one Unix socket**,
  `/run/atlas-updater/updater.sock`, its folder bind-mounted read-only at the same path, the
  socket owned by the container's user (10001) and no one else. A `tmpfiles.d` entry makes the
  folder at boot, so the container Docker restarts finds its bind mount before the service starts. Its peer must be that user's
  **main process** in the container: the kernel names it (`SO_PEERCRED`), and it must be the
  oldest child of the container's init. A command the agent runs is the server's child, and one
  that left it for the init started later, so neither passes (`PeerCheck`). A request is one JSON
  line of four shapes, `check`, `status`, `log` and `apply` with the commit the user was shown and
  whether they confirmed it unverified; its exact keys, nothing more: no path, no command, no
  Compose override. Thirty requests a minute at most; a look is reused for a minute (GitHub's 60
  unauthenticated calls an hour); an update at a time under a file lock, and none within two
  minutes of the last.
- **Who may ask**, in Atlas: the server's `update.check`, `update.apply`, `update.status` and
  `update.log` answer only a client that presented the server token, never a channel's bridge
  (a bot's chat): `FORBIDDEN` (4003). A plugin's screen is refused in the app (`host.request`).
  No tool, bot, routine, hook or plugin imports `atlas.installation` (an import contract), and
  `atlas update` typed in the container refuses, pointing to the host's: a command typed there could
  be the agent's. The guards are defense in depth, as everywhere in Atlas: whoever holds the server
  token could also ask the server, which is why the update itself is bounded (below).
- **What an update may do**: only move forward, to the commit the user was shown (one `main` moved
  past since the look is refused), verified by CI. The verification reads GitHub Actions' runs for
  the commit: green when it has some and every one ended green; **failed**, and not offered, when a
  job ran and failed; **unverified** when it has none, they still run, GitHub could not be reached
  or every failed job never started a step (a billing or quota block stops jobs before their first
  step, which says nothing of the code). An unverified commit is offered with its reason, and
  updates only on the user's explicit yes: a box ticked in the dialog, or `update anyway` typed on
  the host. Never a silent pull of an unverified `main`.
- **The steps**: export and build the image, tagged by its commit and labelled with it
  (`org.atlas.revision`), **while the old service keeps serving**: a failed build changes nothing.
  Then stop the service, snapshot `/data` whole (a tar from a container of the old image, no
  network, the newest three kept), tag the new image as the service's, recreate the container
  (`--no-build --pull never --force-recreate`), and wait for it to call itself healthy, or answer
  `/health` when its image has no health check, then check it runs the new image. Any failure
  after the stop goes back: the old image (the one the old container ran, not whatever the tag
  names by then) tagged again, `/data` emptied and filled from the
  snapshot (the new version may have migrated it), the old container recreated and checked. A way
  back that fails says so and where the snapshot is. Never `down -v`, never `git clean`.
- **The same button, a server's** (`apps/desktop/src/store/updates.ts`): with another machine's
  server or a container on screen, the button and its dialog show that server's look, through
  `update.check`, every hour and when the server comes on screen, and "Update and reconnect"
  asks `update.apply`. **The one difference**, which a server cannot avoid: it restarts where the
  desktop's app closes. The dialog may close meanwhile; the app asks `update.status` every
  3 seconds and again when its connection comes back, and when the host's last update is no longer
  the one before, says how it ended in the same strip as the desktop's, once; its log comes down
  through `update.log`. A server that started moments after an update ended (10 minutes) tells
  each client as it connects (`update.ended`). What the restart stops is that server's turns,
  subagents and executor sessions. A server from before these methods offers nothing.
- **`atlas update` on the host** stays those words: `/usr/local/bin/atlas`, the installer's shim,
  runs the host updater as root through `sudo` (which asks for the password as it always does):
  it looks, says what the update brings and whether CI verified it, and updates on a yes, the
  same update the button asks for, under the same lock. Every other `atlas` command runs in the
  container (`docker exec`).
- **Left out**: updates pulled from an image registry (the image is built on the host from the
  verified commit), signed tags (CI's verification is the check, until releases are cut), a
  downgrade from the app, and more than one Compose service per host.
- **Proved** by `tests/installation/test_host_updater.py` (real git, Docker played, `/data` a real
  folder snapshotted and restored: a successful update, a failed build with no downtime, an
  unhealthy container rolled back with its data, the refusals, the request shapes, the peer check,
  the host's command; the socket and `SO_PEERCRED` on Linux), `tests/installation/test_compose_update.py`
  (the dispatch, `atlas update` in the container), `tests/server/test_update.py` (the methods over
  the real server, owner only, the restarted server's news),
  `tests/installation/test_compose_host_scripts.py` (the shim and the installer, staged) and
  `apps/desktop/src/components/update-bar.server.test.tsx` (the button following the server, the
  restart and the reconnection, the unverified yes, plugin screens refused).

## Desktop app — decided 2026-10-11

Electron, with TypeScript, React and Vite. The app talks to a
local or remote Atlas server through the protocol defined in `atlas.server`. It has no PWA. It lives
in `apps/desktop/`: `electron/` (the main process and the preload), `src/` (the window), `e2e/`.

Cut to what a first screen needs: about 6 runtime packages.

- **The app starts the server**: `atlas serve --host 127.0.0.1 --port 0
  --parent-pid <app>` with a fresh `ATLAS_SERVER_TOKEN` in its environment, and one more per home
  the window opens, with `--home <name>` (Backup and homes). The server writes
  `ATLAS_SERVER_READY port=<n>` on standard output once it listens, and the app opens the window
  only after `/health` and the WebSocket
  answered with the token. Port 0 never clashes with a hand-started server or the container.
- **The server leaves with its app** (a parent-death watchdog): with `--parent-pid` it checks
  every 2 seconds, and stops when that process is gone. Comparing the process's start
  time would need `psutil`; Atlas keeps the PID, and on Windows holds
  the process open from startup, so a reused PID cannot pass for it. **On quit the app stops it at
  once**: the process group with SIGTERM on POSIX, the tree with
  `taskkill /T /F` on Windows. Waiting for the watchdog there is a deadlock: Electron does not exit
  while its child's pipes are open, and the child waits for Electron to exit (one quit in three, seen
  on 11/10/2026). A server that dies unasked is started again, at most 3 times a minute; the
  window's next dial finds its new port and token, and its new epoch makes the window read the
  conversations again.
- **An already running server** is reached with `ATLAS_DESKTOP_URL` and `ATLAS_DESKTOP_TOKEN`;
  the app then starts nothing.
- **The client**: request ids and deadlines; server requests answered once, a crashed card
  answering an error at once so the turn does not wait out its deadline; a `ping` every 15 seconds,
  the socket dropped when one goes unanswered for 45 (judged by the unanswered ping, since a hidden
  window spaces timers a minute apart; any frame counts as life);
  after a reconnect, the events after each conversation's last number, live frames held back
  meanwhile, a truncated window never painted, a new epoch read as a restarted server
  (the conversation is read again); and a jittered backoff from 300 ms to 15 seconds, a socket that
  dies within 5 seconds of opening counted as a failure. `conversation.open` returns the
  messages and the conversation's last event number together, so the race between reading the
  history and replaying the gap cannot happen.
- **The TypeScript side of the protocol is generated** from `atlas.server.protocol` by
  `scripts/gen_protocol.py` into `apps/desktop/src/protocol/generated.ts`, and a test fails when the
  committed file is stale; it runs in Python, so both systems check it without Node.
- **Packages are pinned exactly**: React, Vite, TypeScript,
  Tailwind, `nanostores` for state and `streamdown` for Markdown that arrives in pieces. The
  `.npmrc` asks for exact versions, and no package younger than 14 days. Electron is
  43.7.5, since 40 is out of support (Electron keeps the last three) and carries a high
  advisory (GHSA-9f4c-93c8-jc8g); and ESLint is 10.11.0, 10.12.0 being younger than the 14
  days. A library that owns the thread's state is left out: here that state is the
  server's event stream. Since 18/10/2026 two more: `@tabler/icons-react`
  for the icons, outlined and minimal (asked for by the user), named by their use in one file
  (`lib/icons.ts`); and `radix-ui` for the tooltips, the home menu, its dialog and
  its switch. A control that is only its icon says what it does in a tooltip, and that is its name
  for a screen reader.
- **The window is closed**: context isolation, sandbox, no Node in the page,
  every new window denied, navigation away sent to the system browser, and a preload that hands
  over only the connection (address and token).
- **UI text** comes from a typed catalog: `en` whole, other languages as typed partial
  overrides (an unknown key fails `tsc`), functions for plurals. No i18n library.
- **The screen**: conversations on the left, the conversation on the right;
  the turn as a trail of parts (The turn on screen, below); "Auto" beside send (a ⚡ until the user
  asked for the word on 18/10/2026); below the box
  (above it until the user asked on 22/10/2026), the model, the effort and the terminal as one
  kind of pill; the box grows with the prompt up to two fifths of the window; while a turn runs, Enter steers, and
  send is the stop while the box is empty, and a stop puts the
  unsent messages back in the box; `ask_user`'s card and the
  approval card (once, this conversation, always, deny with a reason), answered within the server's
  300 seconds; the review's summary as a quiet row; a band while the connection comes
  back; and a system notification when a turn ends in a conversation not on screen.
- **What the screen says while it reads, sends and switches** (step 9d; the UI review of
  25/10/2026 found the window saying "No conversations yet" to someone with conversations while
  the server started, and a new conversation out of the list while its first turn waited for an
  approval). A list says it is being read until it was (`$read`), with a skeleton after 400 ms, so a
  quick read never flashes one; the window says Atlas opens until its home first answered, and the
  band is left for a connection lost or a failure; a conversation opened for the first time shows a
  skeleton, never the invitation to start. The server writes a new conversation's title, its first
  message's, as its first turn starts (`ConversationStore.name_untitled`): the list shows only
  titled conversations, and the screens read it as a turn starts. A message shows at once, lighter,
  until the server's own `message.start` or `message.steered` takes its place (`$sending`); one the
  server has, in a turn that runs, says it enters once the current step ends, and goes when the
  turn ends (its words back in the box when it never entered). The words typed in a box stay with
  their conversation (`store/drafts.ts`), and so does where the user read to in it.
- **An error has a way out, in its own place** (step 9d; the review found a failed turn written
  as quietly as "Stopped.", and lists and pages that failed to read looking empty or old). A
  failed turn is an error in the server's words and, as the conversation's last word, offers the
  ways out that exist: its message back in the box, the model to change, and a key to connect when
  a key is what failed (`ModelKeyError`, `ModelAuthError`, `ModelOutOfCreditError`); nothing is
  sent again by itself, which would store the message twice. A list or a page whose read failed
  says why with "Read again" (`$readError`), never "nothing here". A card answered says the answer
  is on its way until the server's `request.cancel` takes it down, never gone first as if it had
  arrived; a reconnect drops it, and the request, delivered again while it waits, takes an answer
  again.
- **Every card that waits for the user has one frame** (step 9d; the review found five cards in
  three looks, an approval titled by the tool's own name, `Allow run_command?`, and the key's card
  taking the keyboard from the message box). `CardFrame` gives Atlas's cards and an executor's the
  same look: amber, the question as the title in words (`Run this command?`, `Claude Code wants to
  run a command`; a tool with no words is named), exactly what would run or change (`Target`), and
  the choices in one order, the one that goes ahead first and the one that refuses last, with the
  reason field under them. While the keyboard is in a card, a number key picks the choice it
  labels, in the row that holds the keyboard or else the card's first. A click on the card gives
  it the keyboard; no card takes it by itself, and the key's field takes it only when nothing else
  has it.
- **Tabs above the central area** (step 9d, decided with the user on 25/10/2026): the
  conversations, the bots' and the executors' sessions open in one home (`Tabs`, store/tabs.ts, one
  per `HomeSession`). A click on a row opens it in the passing tab, in italics, which the next click
  reuses; it stays once used (a message sent there) or double-clicked, and what a first message
  makes opens in a tab of its own. The tab on screen is the one holding what the screen shows
  (`screenPlace`: the conversation open, or the session once it has an id), never kept apart, so
  whatever opens a conversation or a session, a notification's click included, shows its tab; a
  session's tab follows its ids as they come and its account as it changes. Ctrl+1 to 8 show a tab
  and Ctrl+9 the last, Ctrl+Tab the next, Ctrl+W closes the one on screen (to the next, else the
  one before, else a new conversation), all left to the shell inside the terminal; a tab is
  dragged to another place, and a middle click closes it. On macOS ⌘1 to ⌘9 do the same, and ⌘W,
  which Electron's default menu would answer by closing the window, is taken by the main process
  first (`electron/keys.ts`) and closes the tab, or the window when no tab is on screen; the
  sidebar's Ctrl+B takes ⌘B there too, and its buttons say so. Beside them, how many cards wait away
  from the screen, a click going to the first. The window keeps each home's tabs by machine and
  name (`atlas.tabs`), and the home reopens on them and on the one it showed, read as it opens: a
  restored tab is never blank, a closed one never comes back, and one whose conversation was
  deleted closes as it opens (`NOT_FOUND`). An executor's session shown before shows its trail at
  once while it is read anew.
- **A page of its own has one head** (step 9d, `PageHeader`): the routines, the memory proposals
  and the marketplace each say their title and what they are for, with their own controls on the
  right and the way back to the conversation always in the same place; an empty page says so in the
  intro's size. The key screen lists first, open, the provider it calls a good first choice
  (OpenRouter: one key, hundreds of models), where it said so second under another one open.
- **Colors with a name and one meaning each, and short movement** (step 9d; the review found green
  meaning "at work" in the list and "done" on the trail, and the Markdown's code blocks drawn in
  black: they paint with color names the app had never defined). The app paints with the colors
  `src/styles.css` names for the light and the dark theme alike: grounds (`background`, `subtle`,
  `raised`, `muted`, `hover`, `selected`), words (`foreground`, `soft`, `muted-foreground`,
  `faint`), edges (`border`, `border-strong`, `ring`), `primary`, and one color per state: blue and
  pulsing at work (`working`), amber waiting for the user (`waiting`), green done (`done`), red
  failed (`failed`), with `danger` for a button that deletes and `added` and `removed` for a
  change's lines. A screen never writes a palette's own color, nor a color for one theme alone
  (`src/colors.test.ts`), so the user's own colors, later, change these values alone; a harness
  maker's mark, a file's kind on its chip and a bot's face keep their own, being someone's colors
  and not the theme's. A part that looks the same in both themes takes that theme's class: a
  terminal is `dark`, a page's preview `light`. What is normal has no color: a server connected, a
  key stored, "Auto" on (a full dot, no longer amber). A row not seen yet says so with its title in
  bold; the dot is for what gathers rows. Movement is short, one curve and three lengths (120, 180
  and 260 ms): a control's colors move instead of jumping, a `details` slides open, and what comes
  onto a thread on screen, a card or a message on its way rises 4 px into place (`.enter`), while a
  conversation that opens is there at once (`data-settled`); with the system's "reduce motion",
  nothing moves.
- **Memory proposals** (step 9b): the review's row offers to review the change; the proposal is a
  card in the conversation the review read, beside the other cards, until it is decided; and the
  sidebar counts the open proposals, only when there are some, and opens the list of all. A card
  shows a replace as the entry once, with the words that go struck and the words that come marked
  (asked for by the user on 24/10/2026), and both texts whole a click away; what is added or removed
  shows whole. Then approve and discard; one that can no longer apply says why and offers only
  discard; an approved one says what it overwrote or removed.
- **The agent map** (decided with the user on 12/10 and 14/10/2026): a pill in the
  box while the conversation's subagents or background commands run, counting each; a click opens
  the subagents as a tree (Subagents, below) and the background commands below it, each named by
  its description, or its command's first line when the model gave none, with its id (the one its
  notice names) and its time. A click on a command shows the command, the end of its output (the
  last 4,000 characters, read again every 1.5 seconds while it runs and the row is open) and Stop,
  which hands the output back. The server says when one starts and ends (`command.update`), so the
  app never polls for exits; `command.list` gives the running
  ones when a conversation opens. One that ends while the map is open stays on it, saying how it
  ended. A subagent's own background commands stay in its conversation.
- **Gates**, on Linux and Windows beside Python's five (the app is
  used on Windows, and starting and stopping the server is what differs): `tsc` strict everywhere,
  the Electron code included, `eslint` with types, `prettier`, `vitest`, and
  Playwright end to end with the real app, the real `atlas serve` and the fake model server.
  The fake model runs as its own process there (`python tests/fake_model.py`)
  and takes its script over HTTP. Under `--atlas-e2e` only, the window may drop its own connection
  and the tests may read the server's PID; nothing else changes. `scripts/prove_desktop.py` runs the
  same window against a real model, and `scripts/prove_proposals.py` its memory proposals.

### The turn on screen — decided 2026-10-15

A turn shows as a trail: a vertical line with one dot per part (reasoning, text, a tool call), in
the order they happened. All text stays on screen, the interim text lighter and the final answer at
full weight; a click on a tool opens what went in and what came out. Roadmap step 9c.

- **Parts**: reasoning, text, tool, and other, for what the app
  does not know. Reasoning is a part of its own, apart from the text.
- **The trail.** One dot per part, linked from the turn's first part to its last; the user's
  message stays off the line. The dot's color is the state: hollow for reasoning, muted for interim
  text, solid for the final answer, green for a completed tool, red for a failed one, muted for a
  denied, cancelled or unanswered one, and blue and pulsing for one that runs, as a sidebar row at work, which
  also shows an elapsed timer. Success carries no mark. `tool.complete` carries `duration_s`.
- **A call a card holds waits; it does not run** (step 9d, found by the UI review on 25/10/2026:
  a command waiting for its approval read "Running"). Its dot is amber and still, as the sidebar's
  own waiting dot, and its line says what it waits for: the approval, the answers, the key. The
  approval request names the call it holds (`ApprovalParams.call_id`, read by the server from the
  loop's own `current_call_id`, since the gate runs inside the call it judges); a question is its
  `ask_user` call, a key its `secrets` call, an executor's permission or question the call of its
  session that asked (`store/waiting.ts`), nothing of it stored. The three working dots show only
  where nothing on the trail shows the turn at work, between a call's end and the model's next
  words; and an executor's session read while its instance works leaves its last turn going on
  (its call waiting for a permission once read "cancelled").
- **Every text is part of the answer**, decided by the user on 2026-10-15. The text of a model call
  that ends asking for tools is interim text: the server says so with `message.interim`,
  at the end of the call and before its first `tool.start`, so the app never guesses.
  `message.complete` still carries only the final answer.
- **Reasoning** is one closed line: "Thinking…" with a timer while it streams, "Thought for 4 s"
  once done, and "Thought" when reopened (the time is not stored). The line also says
  what the reasoning is about, its latest bold title or its first words: ours, approved by the user
  on a prototype. Open, it is Markdown. Reasoning with no readable text, encrypted or only
  counted, is not shown.
- **A tool**, closed, is its name and a summary of its arguments. Each of Atlas's own tools has a
  title of its own instead (every one since 26/10/2026, the UI review having found raw JSON on the
  trail), named from its arguments: `run_command` its command, `read_file` its path, `ask_user`
  its first question, `delegate` its first goal, `cronjob_manage` and the others with several
  actions a verb for each, words (a question, a goal, a browser step's own comment) drawn as
  words and the rest as code. Its verb says what the call did as it stands: "Ran" once it ran,
  "Running" while it runs, and "Run" while it waits or when it was denied or stopped first, so a
  call that never ran never reads as one that did. MCP and plugin tools, and a call whose
  arguments name nothing, keep the generic summary. Open, it is a box with IN and OUT: one
  argument bare, several each under its name; the output while it runs and the result once done,
  red when it failed, cut at 20,000 characters with a copy-all button; a spilled result
  names its file. `tool.complete`'s `failed` becomes `status`: `completed`, `failed` (it raised,
  or its result says it failed), `denied` (refused at
  approval), `cancelled` (the turn stopped, or
  the loop guard halted it before the call ran) or `unanswered` (nobody answered its approval in
  time); an app older than its server shows a status it does not know with no word beside the
  call. A stop closes the loop's stream, so the
  assistant tells the calls it cut, from the cancelled results the loop leaves. Removing a field,
  the protocol becomes `atlas.v2`: an app and a server of different versions refuse each other
  until both are updated (decided with the user on 2026-10-15; the installed app and its server
  update together).
- **What the app does not know is shown** (the raw payload kept, displayed generically).
  An event of an unknown type becomes an "other" part: a muted dot, the event's name and its payload
  as JSON, folded. It happens when the server is newer than the app, as in an updated container.
  Unknown fields are ignored. A TypeScript test fails when a protocol event has no drawing, so the
  generic part never hides one we forgot.
- **Providers differ only in their adapter**; the screen knows Atlas's events alone. Reasoning tags
  inside a model's text leave in the `chat` adapter through `think_scrubber.py`: nine tags
  (`think`, `thinking`, `reasoning`, `thought`, `REASONING_SCRATCHPAD` and four Chinese ones), in any
  case; a tag split across two chunks is held until whole; an opening tag starts a block only at a
  line's start, so prose that mentions one survives; a lone closing tag is removed. What leaves
  becomes reasoning, and the stored message is clean. A block that never closes keeps everything
  after its tag out of the text.
- **Reopened is live**. The stored body already holds the reasoning, each tool call's
  arguments and the model (`codec.py`): `MessageRow` carries the reasoning and the calls (id, name,
  arguments), and a tool's row its call id and status. A tool result stores its status, so failure
  is never worked out of the result's text again: the status is in the event and costs one
  field of the body, with no migration. Only the times are left out.
- **A channel gets the interim text** (decided by the user on 2026-10-15): each `message.interim`
  goes as a message of its
  own when it comes, the typing notice holds until the final answer, and neither tool progress nor
  reasoning goes.
- **Left out**: a one-line summary of consecutive calls (the user chose a dot per step); the
  per-tool views (a diff, a terminal's stdout and stderr apart, search hits), which come one at a
  time when use asks; a tool `kind`, for icons; stored times.
- **Tests**: the prototype's six model behaviors as fake-model scripts, checked in Vitest;
  Playwright end to end, where the live trail and the same conversation reopened match but for the
  times; an invented event shown as a generic part; the `think_scrubber`'s own tests;
  a channel getting the interim texts in order, before the answer. Real recordings of one short task
  from Anthropic, OpenAI, Gemini, DeepSeek through OpenRouter and an Ollama model that thinks in
  `<think>` are replayed through each adapter and the trail: no generic part, no tag in the text,
  reopened equal. A new model is recorded and run.
- **How the tests do it** (step 9c): the six behaviors and each recording run as a turn on the
  real server, and what the app received and the conversation reopened are kept in the app
  (`apps/desktop/src/store/turns/`, `tests/turn_support.py`), where Vitest replays them through the
  screen's reducer; the server's test fails when what it says changes. `scripts/record_turn.py`
  records through a proxy that keeps only what the provider answered to the turn's calls, with the
  folders as markers and the recording refused if the user's name is left in it; the fake model
  plays it back byte for byte (`FakeRaw`). Found while recording, on 2026-10-15: Ollama 0.33
  already sends a thinking model's reasoning apart (`reasoning`), so its recording does not test
  the scrubber, which the `<think>` script does; asked for no reasoning (`effort: none`), it sends
  the thinking in the text, closed by a `</think>` whose opening its template wrote, and the
  scrubber takes out only the tag (left as it is, decided with the user). DeepSeek
  waits for an OpenRouter key, which the user's vault does not hold (decided with the user).

### The list of conversations — decided 2026-10-27

Step 9e: the sidebar's list holds every conversation of the home, not only the latest 50, finds
one by its words, renames one in place and archives one out of the way.

- **A page at a time** (`conversation.list`, `ConversationStore.page`): the latest activity first,
  the id breaking a tie, 50 rows a page. A page ends at a cursor, its last row's activity and id
  as text (`Cursor`), and the next starts strictly after it, so a conversation whose activity moves
  it to the top meanwhile is neither read twice nor skipped: it leaves the part not read yet for
  the part already read. Each change to the list (`conversations.changed`) reads it again, as many
  rows as the app holds (1,000 at most, `LIST_MOST`; past them, the rest is read again as the list
  scrolls), and that read gives the cursor the next page starts from. The app reads the next page
  as the list scrolls to 240 px of its end, or at once while the list is shorter than its height.
  A page and a read of the whole list never cross (`HomeSession.listRead`): a page read from the
  old end would skip the row the other read just pushed past it. A page that fails says so at the
  list's end, with "Read again", and nothing reads it again by itself. A cursor the server never
  gave is refused; an app that sends none gets the first page, as before this step, and a server
  that sends none ends the list at its one page.
- **The search** (`conversation.search`, `ConversationStore.find`): the field above the list finds
  the list's conversations whose title, or one of whose messages, holds every word typed, each word
  also matching the words it begins (`typed_query`). It has no index of its own: the messages are
  read through the conversations' search index (`conversations_search`), as `conversation_search`
  reads them, and the titles as they are, compared word by word without case or accents, as the
  index's tokenizer compares (`remove_diacritics 2`); a title model's title is in no message, and
  is found all the same. A query with an operator (`AND`, `OR`, `NOT`) is left to the index alone.
  Those found by their title come first, the latest activity first, then those found by a message,
  the best match first, 50 at most, with whether more matched. A result says where it matched:
  its title, or the words around the match in its best message and who said them, each cut into
  pieces by the server, the odd ones what matched, between marks no one types (`MATCH_MARKS`), so
  no character of a message can pass for one. The app searches 250 ms after the typing pauses and
  shows only the answer to the latest words; the results take the list's place, which stays where
  it was, hidden, until Esc ends the search; Enter opens the first conversation found.
- **Renaming in place** (`conversation.rename`, `ConversationStore.rename`): F2 on a row, or
  Rename in its menu (the dots over its time on hover, or a right click), puts the title in a field
  in the row's place; Enter, or leaving the field, keeps the new name, Esc the old one, and a name
  that did not change, or holds no words, changes nothing. The server keeps its words on one line,
  cut to a title's length (`given_title`), as `user`, through the same check and write as every
  title, so a title model's answer arriving after it replaces nothing. A name is not a title
  event (`title.changed` goes to the clients on the conversation): every client hears it in
  `conversations.changed`, which carries it, so each one's list and tabs take it, a tab whose
  conversation its list does not hold as well; a conversation read again (`conversation.open`)
  gives its tab the title the server holds, for a tab kept from before the name. A refusal keeps
  the field open, saying why. A double click on a row still keeps its tab (decided with the user).
- **Archiving** (`conversation.archive`, `conversation.unarchive`, `ConversationStore.archive`):
  Archive in a row's menu takes the conversation out of the list for the Archived section under
  it, which says how many it holds (`ConversationList.archived`) and, opened, lists them a page at
  a time, the latest activity first, each brought back by the button over its time. The stamp is
  a column of the conversation (`archived_at`, migration 5), so a restart and a backup keep it;
  nothing else of the conversation changes, and an archived conversation is not its older messages
  a compaction archived. A tab holding it stays open. A new message brings it back to the list as
  its turn starts, from wherever it was written (`OpenConversation` brings it back before
  `TurnStarted`). One whose turn runs, or waits for the user, is not archived: the server refuses
  and the menu's item is off, so an approval card never hides in the section (decided with the
  user). The search still finds the archived ones, and says so, as the agent's recent
  conversations still hold them. The last one brought back closes the section.
- **Proved** by the store's and the server's tests (each conversation once, in order, with
  activity in the middle and a tie in time; a title found without its case or accents, a message
  by a word begun, the titles first, only the list's conversations, a mark a message held), the
  app's (the page waiting for the read of the whole list, the read of as many rows as it holds,
  the failed page; the search after the pause, an older answer never shown over a newer one) and
  the end-to-end tests: 60 conversations and a new one begun before the rest was read, all 61
  listed once, in order; and the search finding a word begun in a message past the first page and
  a title no message holds, opening what it found, and Esc bringing the list back. The name given:
  the server's tests (its words on one line, cut, never empty; every client told; a title model
  answering after the name replaces nothing), the app's (the list and the tab, one the list lacks,
  a refusal leaving both; F2, the menu and a right click, Enter and Esc, the focus back on the row)
  and the end-to-end test: F2 and the menu, the row and its tab, Esc, and the names read again.
  The archive: the store's and the server's tests (out of every client's list, counted, a page
  at a time, found by the search and marked; brought back where its activity puts it; a new
  message bringing it back on top; refused while its turn runs; kept across a restart and in a
  backup imported as a new home; the migration leaving every conversation listed), the app's
  (out of the list at once, a refusal saying why under the row, the item off while its turn
  runs; the section read as it opens and as the list changes, the count with it closed, its
  pages, a failed read, the last one brought back closing it) and the end-to-end test: archived
  from its menu with its tab open, brought back, archived again and still archived when the app
  opens again, then opened from the section, written to, and on top of the list.

## Attachments — decided 2026-10-16

Files dropped or pasted into the message box, of any format, each reaching the model the cheapest
way the conversation's model allows. Asked for by the user on 2026-10-16. Roadmap step 16g.

- **The file always lands in the home**, in `attachments/<id>/<name>`, and the message points at
  it: a path on the user's
  computer does not exist in the container or on another machine. It starts sending as it is
  dropped, before the message leaves, always as its bytes, in parts of
  4 MiB (`attachment.add`), since a WebSocket frame holds 16 MiB. Sending the path when the
  server shares the disk would spare reading a large file twice; one way for every server is ours.
  What a file is comes from its first bytes where they say (PNG, JPEG, GIF, WebP, PDF), else its
  name. The folder is `user` in the home's boundary, so a restored conversation still opens its
  files.
- **The protocol** (step 16g): `attachment.add` takes a part and answers the attachment with the
  part that completes it; `attachment.limits` gives the limits, for the app to refuse first;
  `conversation.send` takes the ids, its text may then be empty; `message.start` and
  `message.steered` carry them in `UserSaid`, and a stored user message in `MessageRow`, each with
  its kind and its path on the server's machine. The pieces of a reply (`Said`) carry none.
- **One piece on the server decides**, per attachment and per model call, how it reaches the model;
  the app only shows and sends, and a channel's files take the same piece (one
  choke point for every adapter). What a model takes is the model catalog's `input_modalities`.
- **An image** goes to a model that sees images as an image, in the user message, on every wire
  (both; Atlas already sends `read_file`'s images so). To a model that does not, a description
  model describes it before the turn, and the description goes in its place with the file's path
  (decided by the user on 2026-10-16). A fixed description request, under a one-line system prompt
  that keeps the reply a description. The description models are the title models (Claude Haiku
  5.5, else GPT-6 Luna; both see images), side calls (`image_description` in the call ledger)
  with the title quarantine. The
  description is kept with the attachment, so it is made once, as the message is sent; a message
  that steers a running turn is not described. With none, or when it fails, only the note with
  the path, saying the image could not be seen. The app shrinks a large image before it
  leaves, to 2048 px on the longer side, under Anthropic's 5 MB; a PNG stays a PNG
  when it fits, anything else becomes a JPEG; a GIF goes as it is.
- **A document** is taken with any model (decided by the user on 2026-10-16). Text up
  to 100 KB, by a list of text extensions, goes whole into the message, wrapped as untrusted
  (`atlas.trust.untrusted`). Anything else (PDF, Office, OpenDocument, RTF,
  EPUB, larger text) gets the document note: its name, type and path, and an order to read it
  before answering instead of asking the user to paste it. The model reads it with `read_file`,
  which converts and pages it. A PDF never goes to the model whole.
- **A video** goes to a model whose catalog entry takes video, else the note with its
  path. **Audio** gets the note, or what a plugin's reader wrote down of it (Voice, below).
- **What failed is told to the model**: a type not taken, a file that did not open, a
  document with no text. At most five such markers a message, the rest folded into one
  "N more attachments skipped".
- **The stored message keeps the user's text and its attachments apart**, and the note is built
  when the model is called. A note written into the stored prompt would have to be stripped back
  out with regular expressions to match the turn painted. Built per call, a switch to a model that
  does not see images turns earlier images into notes, as a tool's images already are.
- **Limits**: an image up to 5 MB once shrunk (`read_file`'s, Anthropic's); a document or a video
  up to 50 MB and audio up to 20 MB. Over them, the app refuses the drop and
  says why.
- **The screen**: a file dropped on the box, pasted into it or picked with "+" (a 📎
  until 22/10/2026) becomes a chip above it, in the order it came, wrapping onto more rows: its
  name, its size or how far it got, an × on hover, and a tile with its format. Each format is
  marked its own way, as the user asked: the tile shows the format in a few letters (PDF, DOCX,
  XLSX) on the color of its family (PDF red, word processing blue, data green, slides orange,
  e-book teal, text grey, code violet, video fuchsia, audio pink, archive amber), and an image
  shows its thumbnail with the format in a corner. Send waits for every chip to arrive. In the
  conversation the chips sit above the user's message, the same when reopened, and a click opens
  the file in the side panel (step 16f) when the server is on this machine.
- **Left out**: a whole PDF to the model and scanned pages seen as images, and attachments by
  URL. Transcription came with the voice plugin (step 17a).
- **A channel's files** (SDK version 7, decided with the user on 16/10/2026): `Inbound.files`
  carries each file a person sent as its name and bytes, nothing of the platform's own; the
  server keeps them in `attachments/` and sends the message with them, and one it cannot keep
  is said in the chat. The Telegram plugin downloads a photo (its largest size), a document, a
  video, an audio file and a voice message through `getFile` (20 MB at most for a bot, said in
  the chat past it), the caption as the text, and keeps its manifest at `api: 1`, so the
  consent already given holds (it ships with Atlas, as the Claude Code plugin keeps `api: 2`).
  A voice message reaches the model as a note, or written down when the voice plugin is on.
- **An executor's files** (SDK version 13, asked for by the user on 19/10/2026, step 16i): the
  session's box takes files as Atlas's does, the same chips and the same `attachment.add`, when
  its executor says it does (`Capabilities.files`, `ExecutorRow.files`); `workbench.prompt` takes
  the ids. What the harness reads of them is its plugin's, as the harness's own channel takes a
  file: Claude Code gets an image as an `image` block of the user message, in base64, as the Agent
  SDK's streaming input sends one, and every file, the image too, by its path in a note of the
  plugin's (`<atlas-attachments>`, Atlas's attachment note in Claude Code's words), read with its
  own Read tool, which takes a PDF, an image or a notebook itself. The home's `attachments/` is
  one of its working directories from its start (`--add-dir`, the SDK's `additionalDirectories`,
  `ExecutorSpec.directories`), so reading one asks no permission, as a file in the project asks
  none. No description model and no inlined text: Claude Code sees images and reads files itself.
  The harness's transcript is the truth (step 16f), so the files are told back from it: the plugin
  reads its own note as the entry's `files` (`TranscriptEntry.files`), never as the user's words,
  and the server turns each path back into its attachment (`TranscriptRow.attachments`); live, the
  `prompted` and `steered` activity carries them. Unlike a note stripped from a stored
  prompt (above), the note is written and read by the same plugin, in the harness's own record.
- **Tests**: the server's piece with real files, each kind with a model that sees images and one
  that does not (`tests/conversations/test_attachments.py`); a message with attachments stored and
  read back; an image in the user message on the three wires; the description model through the
  real Anthropic adapter for a text-only conversation; the protocol, a file in parts to the model
  and back as chips (`tests/server/test_attachments.py`); the formats and their colors in Vitest;
  Playwright end to end with the fake model (drop a PDF and an image, pick a spreadsheet, take one
  off, send, check what the model got, reopen, open beside; a file too heavy refused at the drop).
  Every server gets the same bytes, so the container has no test of its own. With real models, a
  few calls, asked first: a PDF and an image to a model that sees images, and the same image to a
  text-only model, answered from its description.

## Daily use — decided 2026-10-11

Step 11, the first switch: the user moves to Atlas for every day.

- **An installation apart from development**: a clone of the repository that follows `main`, in a
  folder of its own. The app runs `atlas serve` from the checkout it sits
  in, so running it from the development tree would bring half-written code, and an open step's
  migrations, to everyday use.
- **`atlas desktop`**: under a build lock that `atlas update` shares, it
  installs the app's packages when `package-lock.json` changed, builds the app when its sources
  changed (a content stamp), and starts it detached from the
  terminal. Each run writes the shortcut: a Start Menu link on Windows,
  a `.desktop` entry on Linux, opening the built app directly. Both show the app's icon, which the
  window, the taskbar and the Dock show too: `apps/desktop/resources/`, drawn from `icon.svg` by
  `npm run icons`, with bolder lines at the small sizes. The packaged app is step 18b.
- **`atlas update`**: `git pull --ff-only` of `main`, `uv sync` of the
  dependencies only (reinstalling Atlas would replace the `atlas.exe` the update runs from, which
  Windows refuses; Atlas is installed editable, and the app's next server reinstalls it), never
  removing Atlas itself (`--inexact`: an exact sync without the project uninstalled it, found on
  12/10/2026) and with every extra, as the installer brings them (without it an update never
  brought a new one, found by step 3b on 14/10/2026), and the app built by `python -m atlas desktop --build-only` in a new process,
  through the environment's Python, never a bare `atlas` from the PATH (which found Atlas V3's
  that day), on the code that just arrived (the
  update's own process still runs the code from before the pull: found on 11/10/2026, when the
  first update built without the fix it had pulled); when one of these fails, the checkout goes
  back to the commit it was on. It refuses while a
  home is being served (the app is open): here the app's servers are all there is. It refuses a
  checkout with local changes, never stashing them: an installation is not edited. The database is
  already backed up before a migration (Backup and homes), so there is no pre-update snapshot.
- **The update from the app** (asked for by the user on 18/10/2026: closing the app, running
  `atlas update` in a terminal and opening it again was three steps by hand), lean:
  - **Python looks, the app carries**: `python -m atlas.installation
    check` fetches the branch's upstream and says how many commits it lacks and their subjects,
    each with its `Notes-pt-BR` trailer, the subject in Portuguese that the app in Portuguese
    shows (AGENTS.md, Commits), with nothing to type and no window (`GIT_TERMINAL_PROMPT=0`,
    `GCM_INTERACTIVE=never`). It writes them as UTF-8, which the app reads, whatever the
    console's code page: Windows writes a pipe in its ANSI one, which garbled the Portuguese
    subjects' accents and has no ⌘, on which a look failed on 26/10/2026. Only
    an installation, with its toolchain beside it, is offered one; a development checkout updates
    with git. The app looks 10 seconds after it opens, every hour and when the window comes back
    after an hour, since Atlas's `main` moves several times a day.
  - **A button shows only when there is one**, above the home at the foot of the sidebar, its
    count on it, and on the hidden sidebar's strip. It opens what the commits are, what closing
    now stops (turns that answer, subagents and executor sessions at work) and "Update and
    reopen"; an installation with changes of its own
    is refused there, as `atlas update` refuses it.
  - **The update is handed off as the app closes** (`python -m atlas.installation handoff`, which
    lives in the checkout, so
    each update brings the code of the next). The app starts it and waits, at most 20 seconds,
    until its marker names it, then closes as the user closes it, its terminals
    and servers stopped; the hand-off waits for the app's process to be gone (the server's own
    watchdog, `atlas.home.Parent`, its handle held so a recycled PID cannot pass for it) and for
    the homes to be let go, runs `update` with its way back, writes how it went, removes its
    marker and opens the app again, which says how it went once. The result is written before the
    app opens: nothing claims an outcome the files do not hold yet. A hand-off
    whose app does not close in two minutes touches nothing. The app opens again as it was opened:
    the flags it was given go along, but Chromium's and a debugger's; `--user-data-dir` and
    `--lang` go too, since
    here they are how the app was opened (found by the real proof, `scripts/prove_update.py`: an
    app opened on a folder of its own came back on the user's).
  - **It loads only the standard library** and Atlas's own installation and home: on Windows
    `uv sync` cannot replace a package a running process loaded, and the hand-off runs while it
    syncs (`atlas update`, from a terminal, imports every package).
  - **On Windows it starts through `cmd /d /s /c start "" /b`**, not detached and hidden: a
    console program started detached gets
    no console, and each program it then starts would open a window of its own; so started, it
    keeps a hidden console its programs share, and outlives the app (libuv's job lets a
    grandchild leave it). A path holding `%` or a quote is refused, as cmd would read it.
  - **An app opened while an update runs** says so and starts no server: the update replaces what
    the app runs from. When the update ends first, it opens again on what it brought; the update
    opens the app too, and the single-instance lock keeps one.
  - **On Linux the app reopens only when its sandbox lets it**: a new
    install of the app's packages gives `chrome-sandbox` back to the user, so it is set again with
    `sudo -n` when that needs no password, as the installer does; otherwise, and unless the kernel
    lets users make namespaces, the result says the two commands and the app waits for the user.
  - **Left out**: a progress window during the update (a page in the system's
    browser, its own "decoration"), update channels (`stable`, `canary`), the update of a packaged
    or store app (step 18b), and a bridge of markers between several desktops.
  - **A server's update**, a Compose host's, goes through the same button and the same `atlas
    update`: The update of a Compose host (Container, above).
- **A conversation's model** is chosen in the app, in the model and effort pills of the
  composer, and kept with the conversation, so it survives a restart:
  - the picker lists the providers with a key in the vault, each with its
    short list of models in `providers.toml` (`models`) plus those the user's `models.toml`
    declares; crossing OpenAI's list with what the key may reach, a network call per opening, is
    left out; prices are hidden;
  - the effort lists the levels the catalog says the model takes, and is gone for a model that does
    not reason. Atlas's catalog already refuses a level the model does not take;
  - **unset, the effort is medium** (`DEFAULT_EFFORT`, decided by the user on 17/10/2026), for
    whatever Atlas calls: conversations, bots, routines, subagents and the executors it starts.
    `[model] effort` in `config.toml` and a pick stand above it. A model without `medium` gets the
    nearest weaker level it takes (`clamp_effort`: never stronger, never `none`); a model
    that does not reason, or whose levels the catalog does not know, gets none. An effort left
    unset would let the screen say medium while the provider runs its own default (Anthropic's is
    high). Atlas sends what it shows. The picker never says "Default": it shows the level that runs,
    and its lists mark the default model and level with "· default";
  - a pick is for that conversation only; a new
    conversation starts from `[model]` in `config.toml`. A pick that follows every
    new chat runs forgotten expensive models in silence, and that is left out;
  - a switch mid-conversation adds a note to the history, a user message (strict providers refuse a
    system message after the first), and only the newest one stays; while a
    turn runs it applies from the next turn. Provider fields already go back
    only to the provider and model that wrote them (Models).
- **`atlas usage [--days N]`**: the call ledger over the last 30 days by
  default, by day, by model and by purpose, saying where the costs came from (a free tier's calls
  carry the price list's figure).
- **The workspace stays the user's home folder**, and the agent reaches the whole machine but the
  vault and the system's folders: the guards are defense in depth, not a security
  boundary. Coding projects, which would want a folder per session, go to
  the executors (step 13).
- **A deferred tool called before it is loaded is refused**, the answer saying to load it with the
  tool search.
  Atlas used to run it, and Gemini guessed its arguments wrong that way (step 5).
- **Proved** by 7 days of everyday use, the step's own rule: real conversations covering the core,
  each defect found becoming a test and a fix, listed in
  the roadmap; and by `scripts/prove_homes.py` against real models (Backup and homes).

### The settings screen — decided 2026-10-20

One large
screen, its sections on the left, a change that saves itself and applies without a restart (the
editable settings, in Settings above), and what the environment set shown locked. The sections, in
order:

1. **General:** the language (Portuguese, English or the system's) and the look (light, dark or the
   system's), both kept by the app on this computer; and "About": Atlas's version,
   looking for an update and updating, through the update button the app already has. The look left
   the home menu. A new language reloads the window, whose texts are read once; the server is not
   touched.
2. **Model:** the default model, from the conversation picker's lists, or "the first provider with a
   key", and the providers' keys, the key screen's list.
3. **Personalization:** the soul, "About you" (`USER.md`) and the memory (`MEMORY.md`), each entry
   read, edited, removed or added (`memory.entries`, `memory.edit`), with the limit in characters
   in view. An edit goes through the memory tool's own
   door (`MemoryFiles.change`: its lock, its limit, its threat scan and its refusal of a file
   changed outside it), names the exact entry it changes, and applies from the next conversation,
   the snapshot frozen as one starts. The memory proposals stay where they are, with a shortcut.
4. **Safety:** `[trust] deny`, `ask` and `ask_skip`, one per line, and `approval_timeout`.
5. **Workspace:** `[workspace] folder` and `[time] zone`.
6. **Plugins:** the screen that was in the sidebar. Someone
   waiting with a code still shows in the sidebar, as the proposals do, and opens it.
7. **Executors:** the harnesses on this computer, each installed one turned on here after its
   consent, each one missing greyed out with the command that installs it (the key screen's list).
   Out of the sidebar since 22/10/2026: the sidebar shows only the executors turned on, and with
   none on, a link here.
8. **Machines** (Several machines, below).

The sidebar keeps Conversations, Bots, Executors and Routines, and the proposals when there are
some. A search over the settings waits for use to ask for it: there are some fifteen settings.

## The installer — decided 2026-10-13

Step 18: one line in a terminal gives a machine with nothing on it the installation of Daily use,
the app built and opened, with the defects such installers are known to meet as our tests. The
packaged, signed app is step 18b.

- **Two scripts, `scripts/install.ps1` and `scripts/install.sh`**, run as `irm <url> | iex` and
  `curl -fsSL <url> | bash`. The site serves them, `atlasv4.com/install.ps1` and
  `atlasv4.com/install.sh` (decided with the user on 28/10/2026; a gist served them before, and now holds two lines that
  run the site's, so an old link still installs the latest and is counted): its
  build copies them byte for byte (`site/scripts/check-dist.mjs` compares), so each deploy of
  `main` serves `main`'s, and its Worker answers them as UTF-8 text, never cached. A file handed
  over instead would
  need `-ExecutionPolicy Bypass` (Windows refuses an unsigned script downloaded from the internet),
  and an `.exe` or `.cmd` the SmartScreen warning, which only signing (18b) removes.
- **Each download is counted** (asked for by the user on 28/10/2026: how many installs began). The
  Worker writes one data point to Cloudflare's Analytics Engine (`atlas_installs`,
  `site/worker/installs.ts`): the system (`windows` for the `.ps1`, `unix` for the `.sh`), the
  client (PowerShell, curl, or `other` for a browser or a crawler) and the country Cloudflare
  reads, never the address. `npm run installs` in `site/` reads the last days back. It counts
  installs begun, not finished ones, and a run again to repair counts again; the app's own update
  fetches from GitHub and is not counted. The Privacy page says so. Left out: a call from the
  installer when it finishes, and anything from the app itself.
- **Where it installs**: `%LOCALAPPDATA%\atlas\` on Windows (where the installation already lives),
  `~/.local/share/atlas/` elsewhere, holding `atlas/` (the checkout), `tools/` (the pinned tools)
  and `bin/` (the `atlas` command); `~/.local/bin/atlas` on Linux and macOS. `ATLAS_HOME` is not
  touched: the installation is a program, the home is the user's.
- **Its own tools, pinned and checked by hash**, never what the machine has (a PATH `uv` 0.6, a
  Chocolatey shim and a system Git can each break an install): uv, Python 3.13
  through uv, Node 24, `gh`, and on Windows the PortableGit (its self-extracting `.7z.exe`, which
  needs no `tar` with bzip2; Linux and macOS use the system's Git). The pins are a
  generated block in both scripts, written by `scripts/gen_install_pins.py` from
  `scripts/install_pins.toml`, whose `--refresh` reads each SHA-256 where its project publishes it
  (GitHub's release assets, Node's `SHASUMS256.txt`), and a test fails when the block is stale. A
  version is at least 14 days old when pinned, the `.npmrc` rule. Each tool is unpacked in a short
  folder of TEMP and moved in whole: PortableGit's self-extractor fails past 260 characters, its
  deepest file 86 under its folder, so an install folder longer than 120 characters is refused
  before any download (found by the first real install, 13/10/2026). A digest that does not match deletes the file and stops; a
  TLS failure never falls back. A content-addressed mirror is left out: Atlas has no CDN.
- **The tools stay off the user's PATH.** Only `bin/` goes there (with the environment on the
  PATH, every terminal's `python` would become the installation's). The app and `atlas update`
  find them through the installation's **toolchain** (`atlas.installation`), `tools/` beside the
  checkout, and use the PATH only in a development checkout, which has none. The app no longer runs
  the `uv` of the PATH.
- **The installation's Git asks no window which credential helper to use.** The app puts the
  toolchain first on the server's PATH, so the agent's commands run the PortableGit, whose own
  config names Git for Windows' helper selector (`portable/release.sh` in its `build-extra`): a
  window asking which helper to use, the first time Git needs a login. A `git fetch` the agent ran
  waited on it until the user clicked (14/10/2026). The installer sets `credential.helper manager`
  in that file on every run, the Git Credential Manager that Git for Windows' installer sets, so the
  agent's Git finds the login the user's own Git saved; running the installer again repairs an
  installation, and `atlas update` leaves the file alone. A helper is kept rather than dropped,
  because Atlas's agent fetches the
  user's private repositories. With no login saved, Git Credential Manager still opens its own
  sign-in window.
- **No GitHub sign-in**: the repository is public, so the clone and every later `atlas update`
  fetch with no login. The pinned `gh` is still installed, for the agent's `github` skill.
  An installation made while the repository was private keeps `gh` as its checkout's credential
  helper; for a public repository it answers nothing and changes nothing.
- **The order**: refuse an occupied folder that is not a checkout, a file or a
  link, **before** anything is downloaded; the tools; the clone into a sibling staging
  folder (`--filter=blob:none`, `core.longpaths` and `core.autocrlf=false` from the first command,
  three tries), moved into place only whole; `uv sync --locked --all-extras` (the installer brings
  every extra: Dependencies); `python -m atlas desktop --build-only`, which builds the app and
  writes the shortcut; `bin/` on the user's PATH; the app opened.
- **Run again, it repairs and updates**, as `atlas update` does and with its refusals: not while
  the app is open, not over local changes (an installation is not edited). A
  `.git` with no commit (an interrupted clone) is moved aside, never deleted; the fetch names its
  refspec; a checkout that cannot fast-forward keeps its commits in a rescue ref and is reset to
  `main`.
- **Windows, from known defects** (the commit names the test that holds each): the script is
  ASCII with no BOM (PowerShell 5.1 reads it as the ANSI code page); a failure under `iex` throws
  and never `exit`s (it would close the user's window); nothing is dot-sourced from disk (the
  Restricted policy refuses it, while `iex` text is never checked); every native command runs
  through one function that tolerates its stderr and judges its exit code; profile paths are
  expanded out of their 8.3 form (`FIRST~1.LAS`) before use; captured output of UTF-8 programs is
  decoded as UTF-8 (a CJK profile); children get `PYTHONUTF8=1`; inherited `UV_PYTHON`,
  `VIRTUAL_ENV`, `CONDA_*` and `PYTHONHOME` are removed; a process tree is stopped with
  `taskkill /T /F`; the PATH entry is compared as a whole entry, never with `-like` (a `[` in a
  user name), and written back keeping its `REG_EXPAND_SZ` type;
  the current window gets the PATH too, so `atlas` answers at once under `iex`. An empty argument
  to a program is passed as `'""'` before PowerShell 7.3, which drops a `''` (found by the
  script's tests: git got one argument too few). The command is `bin\atlas.cmd`, which runs the
  environment's `atlas.exe` in place, outside the checkout.
- **Linux and macOS, likewise**: `set -u` safe under `curl | bash` (an empty `BASH_SOURCE`);
  questions read `/dev/tty`, never the piped script; the PATH line added once, guarded, to the
  shell's own files (`.bashrc` and `.profile`, `.zshrc` and `.zprofile`, fish's `config.fish`),
  never creating a `.bash_profile`, and a file that already sets it (Ubuntu's `.profile`) left
  alone; no `timeout` or `realpath` assumed on macOS, and its bash 3.2 (no hash tables, so the
  pins are a `case`). A musl system and one without `libatomic` are refused before any download,
  since the pinned Node cannot run there. Electron's
  `chrome-sandbox` is made root's with the setuid bit through `sudo`, asked on the terminal only;
  without one the two commands are said. The command is
  `~/.local/bin/atlas`, removed before it is written so an old link is never written through.
- **The key screen**: when no provider has a key in the vault the app
  opens on it, OpenRouter, OpenAI and Gemini, each with its "get a key" link and a field for the
  key (the catalog's `key_page`), and "choose later", which leaves a band leading back, so nobody
  is left without a way forward. `provider.list` gives the providers that take
  a key; the key goes through `provider.connect`, is trimmed, and is never sent back to the window.
  The field takes any text that is not blank. **It is checked where it costs nothing and blocks
  only a refused key**: the server runs the key check against the key given, on a connection of its
  own, never retried and given up after 20 seconds, and stores it unless the provider refuses it
  (401, 403); a used-up credit, a rate limit or a provider out of reach store it with
  the check's words. A probe that refuses on any failure turns good keys away (proxies, regional
  blocks and flaky providers), and a model call costs money. The model is
  then the first provider with a key, as it already is. OpenRouter's OAuth login is left out
  (decided with the user).
- **Proved** by the scripts' tests under Windows PowerShell 5.1, PowerShell 7 and bash
  (`tests/installation/test_install_ps1.py`, `test_install_sh.py`, `test_install_pins.py`); by
  the real install in CI (`.github/workflows/install.yml`) on Windows and Ubuntu, and macOS when
  asked, from the commit under test, run twice
  (`irm | iex` under 5.1 then a file under 7; `curl | bash` under the system's bash), checking the
  command, the PATH a new shell reads, the built app and a server that answers, only when the
  installer or what it installs changes, or when asked; by the app's e2e of the key
  screen against the fake model; and by an install in Windows Sandbox (a clean machine under the
  Restricted policy) and one by a tester.

## Routines and loop guards — decided 2026-10-11

Step 12. Atlas V3's M-106 had reached the catch-up rule below.

- **Routines live in the database** (`jobs_` tables: the routines and their runs), by the
  Persistence rule.
- **Schedules**: `every 30m`, `in 2h` (once), `every monday 9am` and
  `weekdays at 9am`, a 5-field cron expression, an ISO time. Cron runs on `croniter` 6.0.0, in the
  core; corrected on 12/10, since the decision of 11/10 had left natural language
  out for needing it, and cron needs it too. Times are in the system's zone, or `[time] zone` in
  `config.toml`, with `tzdata` on Windows, which ships no zone database.
- **The server fires them**, every 60 seconds; one server per home already
  keeps a routine from firing twice. A run missed while the machine was off fires late within a
  grace of half its period (2 minutes to 2 hours); past it, the missed runs collapse into one,
  and the list says it ran late. A routine still running does not start again.
  No generic retry.
- **A run is a new conversation** of source `routine`, demoted in conversation search, with the
  memory snapshot: no `ask_user`; an approval that would
  need someone is denied; no background review (~30K tokens a run); the
  skills it loads count as used for the curator; it may not create or change a routine (a
  recursion guard); it stops after 10 minutes without activity (an inactivity watchdog), with
  no wall-clock cap. Its model is `[model]`, or the one the user pinned on the routine.
- **Its result is the conversation and a system notification** (decided with the user): the run is
  listed beside the other conversations, and the system
  says the routine finished. A `[SILENT]` reply notifies nobody and is kept; a failure always
  notifies. V3's inbox is left out; channels come in step 16.
- **`cronjob_manage`** is the tool (create, list, update, pause, resume, remove, run now),
  deferred behind tool search, the routine's prompt scanned for threat patterns,
  with no confirmation step.
- **A script-only routine** runs a command and delivers its output; empty
  output notifies nobody. The daily backup is one, `atlas backup`, made in the user's home.
  The agent makes one too, through `cronjob_manage` (the user's own until 2026-10-25, asked for
  by the user): its command passes the approval gate as a `run_command` would, when it is made
  and when it is changed, since the routine runs it later with nobody to approve. A bot makes
  only prompt routines.
- **`atlas routines`** `list|create|pause|resume|run|remove|runs`; and **a lean
  screen** (decided with the user): the list, run now, pause, remove, and
  each routine's runs, which open as conversations. Creating stays with the conversation and the
  terminal.
- **A background command notifies when it ends**: its result enters the
  conversation between turns.
- **Loop guards**: a cycle (A, B, A, B, periods 2 to 4); a byte-identical result of 512
  characters or more replaced by a pointer to the first; the same failure again (warned at the 2nd);
  the same tool failing (warned at the 3rd); a successful changing call resets the failure counts.
  **The posture** (decided with the user): attended work only warns the
  model, since the user can stop it; a routine, which nobody stops, is halted. Step 4's stop at
  the 5th identical call becomes a warning in a
  conversation.
- **A known limit** (found by the real proof on 12/10/2026): with nobody to approve, a
  flagged command is refused, but only what the patterns know is; Gemini 3.7, refused three times,
  deleted the folder another way. Kept as it is, decided with the user.
- **Proved** by `tests/jobs`, `tests/assistant/test_routines.py`, the server's and the CLI's tests, an
  e2e scenario, and `scripts/prove_routines.py` against real models (OpenAI and Gemini, 12/10/2026).
- **Left out** until use asks: URL monitors, wake scripts, the per-routine notepad, retrying an
  unreachable model, and durable tasks (a kanban, goals, V3's task registry).

## Subagents — decided 2026-10-11

Step 12b. The shape: a fresh context, in the background by default, only the summary returned.

- **`delegate` keeps its name** and takes `tasks` (each a `goal` and its
  `context`), `action` (`spawn`, `list`, `steer`, `stop`), `subagent_id` and `message`. Its
  description leaves out what names a feature Atlas lacks
  (`execute_code`, tracked work, `output_schema`, `images`, `group`, pinning a model in its config,
  live transcript files) and uses Atlas's tool names: 833 tokens in Atlas's estimate,
  the always-on tools 3,067 on gpt-6-luna, measured on 12/10 (decided with the user, against a cut
  of our own). It stays always on. The outside executors (step 13) join it as an
  optional `executor`; step 5's synchronous executor interface leaves until then.
- **The model picks neither tools nor model**: a child inherits the parent's model and tools, minus
  `delegate`, `ask_user`, `memory` and `cronjob_manage`. It gets the
  identity and the subagent prompt (the context, then "summarize what you did, found, changed
  and hit"), and the goal as its first message; no memory snapshot.
- **A child is a conversation** of source `subagent` with its parent's id, run in the server's
  process as a routine's run is, in the **subagent role**: approvals denied, no background review,
  the loop guards only warning (a subagent counts as attended: its parent watches), at most 250
  model calls. It is left out of the
  sidebar and of search; its calls are `subagent` in the call ledger, so `atlas usage` shows what
  delegation cost.
- **Always in the background**, in a conversation: the call returns at once, saying the results
  come when the parent ends its turn, and one **batch notice** brings every summary of the call when
  the last child ends (a notice per child wakes the parent once per task, and almost every such
  notice is answered "already incorporated"). It enters as the background
  command's notice does: it starts a turn, or waits for the one running. An early notice of a
  failed child is left out, since it is a second notice. **In a routine** nobody would receive it,
  so the call waits for its children and returns their results.
- **The parent lists, steers and stops** its own children: steering enters the child after its
  current tool calls, as an out-of-band user message, and one that arrives too late is said in
  its summary; stopping returns the child's last words with status `interrupted`; stopping the
  parent's turn stops its running children.
- **The summary comes back** whole up to 24,000 characters, or half the parent's remaining context
  split over the batch (at least 2,000); past it, three quarters of the head and a quarter of the
  tail, and the whole text in the spill folder with the `read_file` offset of the middle (a
  footer).
- **Limits**: one level; at most 10 tasks a call, and 10 delegations in the background at
  once in the process, past which a call waits for its children, run synchronously
  (never queued); a batch's goals checked (no placeholder, no template
  marker, 10 characters at least); 50 subagents a turn, a loop cap that blocks whoever watches
  (`max_subagents`); a frozen child is stopped, 450 seconds without activity or 1,200 inside one
  tool, as `stalled`.
- **Kept in the database** (`jobs_subagents`): each child's goal, status, summary, calls and cost,
  and whether its batch was delivered. A server that starts finds the children an earlier process
  left running and marks them `unknown`, never `failed`, and delivers their batch
  with what was recorded, within 48 hours (the replay window).
- **The screen** (decided with the user): the **subagent dock** above the composer while children
  run: each child's goal, its last tool and its time,
  with Steer and Stop. A live transcript pane is left out.
- V3's resume fuse (10 resumes without a human message) is left out: one notice per batch
  and the guards do its work.

## Outside executors — decided 2026-10-12

Step 13. The user's goal, in their words: nothing they would answer Claude Code in a terminal is
lost; it reaches them through Atlas. Researched on 12/10/2026. Domain agents moved to step 13b.

- **Claude Code first**, the one the user runs (since step 16c the plugin Atlas ships,
  `atlas/plugins/bundled/claude-code`: Plugins, below); Codex second, since step 16o (Codex,
  below); the Gemini CLI third, since step 16p, over ACP (Gemini CLI, below); DeepSeek's
  harness fourth, since step 16q, over the same client (DeepSeek, below); and the rest come when
  use asks.
- **The user's own Claude Code, over its stdio control protocol**, the one the Claude Agent SDK
  speaks, spoken by Atlas itself rather than through the SDK:
  the SDK's every wheel carries a 244 MB `claude` of its own and ships almost weekly (decided with
  the user). Atlas runs the `claude` on the PATH (`claude.exe` on Windows; the SDK refuses the npm
  `.cmd`), or its plugin's `program` setting, with Claude Code's own system prompt, the
  user's settings and permission rules and the project's CLAUDE.md (`--setting-sources
  user,project,local`), in the default permission mode: it asks here what it would ask in a
  terminal.
- **Its login is never touched**: Anthropic forbids a third party to route requests through a
  plan's credentials or to intermediate them, and an API key in Atlas's environment would be
  billed in place of the user's subscription, so `ANTHROPIC_*` variables are left out of its
  environment. Its first message says which credential it uses. Since step 16h other accounts,
  each in its own Claude Code folder and logged in by Claude Code itself, may run beside it
  (Several accounts at once, below).
- **`delegate` takes it as `executor`**, only where it is installed (its words then cost 138 tokens
  more in Atlas's estimate), each task with its project folder; a routine cannot hand it work,
  since nobody would answer it. It runs as a subagent of step 12b: in the background, in the same
  batch notice, its record in `jobs_subagents` with its folder and its session.
- **What it does is kept** as the subagent's conversation: its words, its
  tool calls and their results; the screen opens it.
- **What it asks waits with no deadline**, for the defect most reported among products that
  delegate in the background is the lost permission request. They are server requests of their
  own, `executor.approval` and
  `executor.question`, held when no app is attached and sent with the conversation's open
  requests to the app that opens it; a delegation made from a channel's chat is asked there too
  (Channels). A refusal does not end the task: Claude Code reads why.
- **"I answer" or "Atlas decides"**, per running executor (`subagent.decides`), **Atlas's own
  option**, asked for by the user: a permission goes to the guardian (Trust), and what it is unsure
  of still reaches the
  user; a question goes to the conversation's model, with the conversation's last words.
- **Stopping** interrupts it and stops its whole process tree (stopping the process alone leaves
  the tools it started behind), with no console window on Windows; what it asked is
  withdrawn. A watchdog stops one that does nothing for 450 seconds, or 1,200 in a tool, and leaves
  alone one waiting for the user.
- **The screen** (decided with the user): its presence on the right of the conversation, apart from
  the subagents' dock: a badge with its name while it works (Anthropic's logo needs its approval
  first), what it did when opened, who answers what it asks, open its conversation, stop; and its
  own cards, which come out of the badge, styled apart from Atlas's.
- **Another account, when the plan's limit stops it** (decided with the user on 12/10/2026): its
  `rate_limit_event` ends the task, and the notice names the account and when it resets. Only when
  the user asks, `delegate(action='account')` runs Claude Code's own `claude auth login --email`,
  which opens the user's browser on Anthropic's page (Atlas never sees the credentials; one
  installation holds one login, so each switch logs in again, never while it works), and
  `delegate(action='resume')` goes on with the same session (`--resume`), in the same folder:
  Claude Code keeps its history by folder, not by account, so nothing is copied. The badge names
  the account (`claude auth status`). With Claude Code installed, `delegate` costs 200 tokens
  more in Atlas's estimate, about 137 on gpt-6-luna. Since step 16h each account has its own
  folder, so `action='account'` logs in the account `executor` names, and never an email another
  account is logged in as already: a delegation reaches that one by its name (A delegation's
  account and route, below).
- **Proved** by `tests/plugins/test_claude_code.py` (the channel, against `tests/fake_claude.py`,
  which speaks it over
  real pipes), `tests/assistant/test_executors.py`, the server's held request, an e2e scenario, and
  `scripts/prove_executors.py` against the user's own Claude Code and a real model (12/10/2026), the
  switch between the user's two accounts included (13/10/2026).

### Codex — decided 2026-10-20

Step 16o, asked for by the user on 20/10/2026 with the Gemini CLI and DeepSeek after it (ROADMAP,
Codex, Gemini CLI e DeepSeek). Studied on Codex 0.163.0, in the schema its own `codex app-server
generate-json-schema` writes. The plugin is
`atlas/plugins/bundled/codex`, the SDK's executor as Claude Code's is.

- **Over `codex app-server`**, the JSON-RPC 2.0 on stdio that Codex's own app and its VS Code
  extension speak: one process per instance, holding one thread; a prompt is a
  `turn/start`, a steer a `turn/steer` naming the turn it enters. A line may be long (one
  account's connectors passed 64 KB), so the reader takes 16 MB.
- **Found** on the PATH (on Windows a native `codex.exe` only, npm's `codex.cmd` replaced by the
  native program its package carries), in the Codex app of the Microsoft Store through the
  registry (its folder changes with each version; its `codex.exe` runs from outside the app), and
  in Homebrew's place, or as its `program` setting names it. It needs 0.163.0, the version proved;
  the Store app's `-alpha` counts as its numbers.
- **Its login is never touched**: `OPENAI_*` and `CODEX_API_KEY` are left out of its environment,
  and `CODEX_HOME` is kept, the user's own folder. Atlas names itself `atlas_workbench`, not
  "atlas", which Codex gives another product.
- **It asks as its terminal does**: sandbox `workspace-write`, approval policy `on-request`. A
  command, a file change or more permissions is a `Permission`, answered `accept` or `decline`.
  Its `decline` carries no reason, so a refusal that says why is steered into the turn once
  written, for Codex to read (Atlas's own choice). An MCP server's elicitation is declined. Its
  questions (`item/tool/requestUserInput`) exist only in
  its Plan mode, so it says it has none (`questions=False`), and one that comes is asked all the
  same.
- **Its automatic mode is its reviewer** (`approvalsReviewer: "auto_review"`), its subagent that
  approves or denies a request by its risk, in the same sandbox: `delegate` runs it in it by
  default, as Claude Code's `auto` (Subagents' `AUTO_EXECUTORS`). The reviewer, the model and the
  effort go with each `turn/start`, so a switch counts from the next turn.
- **What it has is read with no effect and no model call**, from a Codex started in the home
  folder and closed at once: its account, models (the hidden left out), connectors, plan limits
  (`primary`, `secondary`), sessions (`thread/list`, always `useStateDbOnly`: without it Codex
  repairs its database from its files, which is writing; its `cwd` filter finds nothing there, so a
  folder's sessions are picked by the plugin) and their turns (`thread/turns/list`, the
  protocol's shape rather than its files'). Its slash commands are its screen's, so it offers none.
- **Its other accounts** are Codex's own folders (`CODEX_HOME`, `~/.codex-<nickname>`), logged in
  by `codex login` on the user's browser; another account's session goes on here in a copy,
  `thread/fork` taking the path of its file, which that account's Codex names.
- **Not known:** whether a session is open in the Codex app (Atlas's `app-server` is another
  process); going on with one there fails with Codex's own error, on the card.
- **Proved** by `tests/plugins/test_codex.py` against `tests/fake_codex.py`, which speaks the
  same JSON-RPC over real pipes, and its reads against the user's own Codex (20/10/2026, no
  prompt); a turn with a real model waits for the user (`scripts/prove_codex.py`).

### Gemini CLI — decided 2026-10-21

Step 16p, after Codex (ROADMAP, Codex, Gemini CLI e DeepSeek). Studied on the Gemini CLI 0.65.0,
in its source (`packages/cli/src/acp/`, its session files and its Code Assist client; Apache-2.0)
and in ACP's schema (`agent-client-protocol` 0.9.0, schema 0.11.2). The plugin is
`atlas/plugins/bundled/gemini-cli`.

- **Over ACP**, through the SDK's client (`atlas.plugins.acp`, version 18): `gemini --acp`, one
  process per instance holding one session; a prompt is a `session/prompt`, which answers when the
  turn ends. Atlas's own client, not ACP's Python package, which brings pydantic and changes its
  models faster than the harnesses: plain dictionaries. Atlas offers
  no file system and no terminal, so the harness reads, writes and runs commands itself, in its
  own sandbox, as in its terminal. A permission is answered with the option of the verdict's kind
  (`allow_once`, `reject_once`), never by its id.
- **A steer is a second prompt**: the Gemini CLI ends the turn that runs at its next step and goes
  on with the new one, so the plugin waits for the second as the same turn. ACP's answer to a
  permission carries no reason, so a refusal that says why is followed by such a prompt with it
  (Atlas's own choice, as for Codex). It asks no questions over ACP (its `ask_user` is left out
  there), and its mode changes come as a message of its own (`[MODE_UPDATE]`), not shown.
- **Its automatic mode is its own `yolo`**, which approves every tool with no review, chosen by the
  user on 21/10/2026 over Atlas's guardian; `delegate` runs it in it, as Claude Code's and Codex's
  (`AUTO_EXECUTORS`). A folder it does not trust (its folder trust, on by default) refuses `yolo`:
  the session says so and keeps asking, the user's choice of leaving its trust as it is.
- **Found** on the PATH, or where npm and Homebrew put it. On Windows npm's `gemini.cmd` starts a
  Node script `CreateProcess` cannot run, so the script is run with the Node beside it, else the
  PATH's. It needs 0.65.0, the version studied and proved. Its environment is the user's own (an
  API key may be its login), without `NO_BROWSER`, which would make it write a login prompt over
  the channel.
- **What it has is read from its files, without running it**: started, it renews its login and
  may write. Its account (its settings' chosen login, Google's account it names, whether it keeps
  a token), its sessions (`.gemini/tmp/<project>/chats/session-*.jsonl`, read as its own reader
  folds its `$set`, `$patch` and `$rewindTo`; a project's folder from its `.project_root`, in its
  real case on Windows, where the marker lowers it) and its connectors (its settings' MCP servers
  and its extensions', on or off). Its models come from a session it opens (`session/new` writes a
  file with no message, which its lists and Atlas's leave out), its slash commands from its
  `available_commands_update`.
- **Its plan's limits are Google's Code Assist quota** (`loadCodeAssist`, `retrieveUserQuota`),
  read with the token its login keeps, as its `/stats` reads them, and no model request: one
  window a model, the share used, when it starts again. Only while the token is good: Atlas never
  renews it, so a token past its hour waits for the next turn, after which they are told
  (`UsageChanged`). An API key has none to read. Google's 429 for a quota spent is the plan's
  limit (`LimitReached`); its 429 for a minute's pause is not.
- **A personal account logs in with an API key**, the plugin's secret (`GEMINI_API_KEY`, in the
  vault, the same name Atlas's own Gemini provider reads), handed to the sessions it opens: Google
  stopped taking a personal account's login to the Gemini CLI (found in the real proof, 21/10/2026;
  chosen with the user). Its login with Google is a company account's, and so is the quota read
  from Code Assist; Google's refusal of a personal login becomes the way to the key on the card.
- **Its other accounts** are homes of their own (`GEMINI_CLI_HOME`, `~/.gemini-<nickname>`, whose
  `.gemini` it runs on), logged in through ACP's `authenticate` on the user's browser. A session
  goes on in another account, or in a fork, as a copy of its file with an id of its own, which it
  then loads; deleting one takes its file and what it kept beside it, as its `--delete-session`.
- **Proved** by `tests/plugins/test_gemini_cli.py` and `tests/plugins/test_acp.py` against
  `tests/fake_gemini.py`, which speaks ACP over real pipes and writes its sessions as it does, its
  reads against the user's own Gemini CLI (21/10/2026, no prompt), and the real proof
  (`scripts/prove_gemini.py --vault`), whose seven steps passed on 21/10/2026 with the vault's key.
  It found what the fake hid: a tool call's `content` is a list, and a listener that fails on a
  notification no longer ends the channel, it is logged.

### DeepSeek — decided 2026-10-21

Step 16q, after the Gemini CLI (ROADMAP, Codex, Gemini CLI e DeepSeek). Studied in DeepSeek's
harness (`dsh`, `@deepseek-ai/dsh` 0.2.1-rc.2, MIT): its ACP package (`packages/acp/acp`), its
session format and its configuration, and in the `dsh` installed here, with no prompt. The plugin
is `atlas/plugins/bundled/deepseek`, over the SDK's ACP client.

- **Over `dsh --profile acp`**, its automation-only ACP: no login over the channel, `session/new`,
  `session/resume`, `session/list` and `session/close`, the model and the effort as configuration
  options (`session/set_config_option`), and no `session/load`, fork, deletion, modes, commands,
  additional directories or images. A permission offers only `allow-once` and `reject-once`.
- **A steer is a cancel and a new prompt**: it takes one prompt at a time and refuses a second, so
  the turn is cancelled (its work kept) and the steer goes in next, one turn for Atlas. A
  refusal's reason goes in the same way, since ACP's answer carries none. A turn that fails
  rejects the prompt ("turn failed: …"); DeepSeek's 402 for a spent balance is the account's
  limit (`LimitReached`).
- **Its automatic mode is its preset `danger-full-access`** (no review), chosen by the user on
  21/10/2026 as the Gemini CLI's `yolo`, so `delegate` runs it in it (`AUTO_EXECUTORS`). The
  preset is the process's (`DSH_PERMISSION_MODE`), so a switch starts `dsh` again on the same
  session (`session/resume`); during a turn, from the next one. Off, `workspace-write`, which
  writes only in the session's folder and asks.
- **Its session logs are off in the processes Atlas starts.** `dsh` sends DeepSeek, with each
  request and by default, the session's messages, tool arguments and results, paths and feedback
  (`session-log-deepseek`); Atlas hands it a `--patch` that turns that off, unless the plugin's
  `session_logs` setting says `on` (decided with the user on 21/10/2026). The user's own `dsh` is
  left as it is.
- **It runs on a DeepSeek API key**: the plugin's secret (`DEEPSEEK_API_KEY`), else the key in its
  environment or in its own credentials file (`.credentials.yaml` in its home). Its browser login
  is its own screens', and its ACP offers only the key's provider. The balance is DeepSeek's own
  `/user/balance` with that key, shown as the plan: DeepSeek bills by use, with no windows.
- **What it has is read from its home, without running it**: its sessions from the projection it
  keeps beside each one (`storages/session_projcache/sessions/<id>.json`: folder, title, prompts),
  and a session's transcript from its log (`sessions/<folder>/<id>/session.v4.jsonl.zstd`), whose
  Zstandard frames Python 3.13 does not read: `zstandard`, the `deepseek` extra, does, loaded only
  then. Its models and efforts come from a session it opens and closes, which has no prompt and
  so is listed nowhere.
- **Its other accounts** are homes of their own (`DSH_HOME`, `~/.dsh-<nickname>`), each with its
  key; with no fork or copy in its ACP, a session goes on only in the account that has it.
- **Found** on the PATH, else in npm's global folder: on Windows npm's `dsh.cmd` starts a Node
  script, run with the Node beside it, else the PATH's. It needs 0.2.1, the version studied.
- **Proved** by `tests/plugins/test_deepseek.py` against `tests/fake_dsh.py`, which speaks ACP over
  real pipes and writes its sessions in Zstandard frames as it does, and by the `dsh` installed
  here (the handshake, a session's options, the `--patch` and the preset, no prompt); a turn with
  the real model waits for a key with balance (`scripts/prove_deepseek.py`).

## Browser — decided 2026-10-12

Step 14 (`atlas.browser`). Decided with the user on 12/10/2026.

- **One code tool, `browser_exec`**, deferred behind tool search: the model writes Python against
  pre-imported helpers
  (`new_tab`, `page_info`, `js`, `fill_input`, `click_at_xy`, `capture_screenshot`, raw `cdp`),
  batching a sub-procedure in one call; the browser and a workspace folder last across calls,
  Python variables do not. Its description is a header and a digest of the helpers; the
  code starts with a one-line comment naming the step for the user. The engine is Browser Use's
  **browser-harness** (0.1.13, MIT), run on Atlas's own interpreter as `python -m
  browser_harness.run` with the code on its standard input, its daemon holding the browser between
  calls (about 2 seconds for the first call, 0.2 after). Its telemetry, which sends the code to
  PostHog, and its update check, which tells the agent to upgrade itself, are switched off.
- **Atlas's own Chrome profile** (`ATLAS_HOME/browser/profile`), which the user signs into once per
  site, opened by Atlas itself (`--user-data-dir`, `--remote-debugging-port=0`): Chrome 136 and
  later refuse remote debugging of the user's default profile, and copying
  the real profile fails on Windows while Chrome is open (the cookie database is locked). The user
  chose this over an extension, which needs a store listing and a channel
  of its own. Two more modes, **clean** (a temporary profile, a window) and **headless** (a
  temporary profile, no window), are thrown away when Atlas closes them. Chrome, Edge or Chromium,
  found where they install or named in `[browser] chrome` (the program and its first arguments); the
  tool is offered only when one is found. One Chrome per mode, each conversation in its own tab.
  Edge's launcher on Windows can end with code 0 and leave the browser running in another process,
  which writes `DevToolsActivePort` a moment later (found on 25/10/2026, where Edge was the only one
  installed): a launcher that ends with 0 gets 5 seconds for the file before it counts as a browser
  that did not start, and closing waits for the endpoint to stop listening before the profile goes
  (a browser still listening keeps its `DevToolsActivePort`, so the next start uses it).
- **The agent stops at logins, CAPTCHAs and payments** and asks the user to do that part in the
  window, then reads the page again once they say it is done; it never guesses credentials. A
  password manager (1Password) is left for later, by the user's
  decision.
- **No takeover, no presence on the screen** (decided with the user on 20/10/2026). Step 14 had
  a lease: a panel on the right of the
  conversation with the last step's label and "Take over", which refused every call while the
  user held the window. In use the panel stayed after the browser closed, with no way to close
  it, and the user found the button unneeded: the window is a real one, and the user types in it
  when the agent asks. The panel, the lease and their protocol (`browser.state`,
  `browser.take_over`, `browser.hand_back`, `browser.update`) were removed.
- **Who gets it, and what it may reach**: the code is host Python, so the tool is given
  only where `run_command` is, and it is not approved call by call (the dangerous-command
  patterns read shell, not Python). URLs written in the code are checked first: a cloud metadata
  address (169.254.0.0/16, `metadata.google.internal`) is always refused, and so is a URL carrying
  a secret Atlas knows; a private address is allowed, the browser being local. The output is
  redacted, wrapped as untrusted, and spilled to a file past the limit; a
  screenshot it prints reaches the model as an image when the model sees images.
- **Proved** by `tests/browser` (the rules; the service against `tests/fake_chrome.py` and
  `tests/fake_engine.py` over real processes; and the installed Chrome with the real engine), the
  assistant's tests, and `scripts/prove_browser.py` against the installed Chrome and a real model
  (13/10/2026).

## Bots — decided 2026-10-13

Step 13b, decided with the user, who asked for bots. Researched on 13/10/2026, with Atlas V3's
domain agents.

- **A bot lives inside the home** (`atlas.bots`), beside the main agent. Making each bot a whole
  home was refused by the user: three homes of three bots each would be twelve
  homes and twelve servers, and each copy of `USER.md` would age on its own. One server per home
  runs the home's bots (Backup and homes is unchanged), and a home's backup carries them: `bots/`
  is `user` in the home's boundary.
- **A bot's own**, in `bots/<name>/`: its definition, `BOT.md`, in the format of Claude Code's
  agent file (a frontmatter with the name, the title, the description, the model, the tools, the
  MCP servers and the skills it may see; the body is its mission and rules);
  its `MEMORY.md`; its `skills/`; and, in the database, its fixed conversation and its routines.
- **The home's, shared**: the vault, the MCP servers, the database, the skill library, and
  `USER.md`. The main agent keeps the home's `USER.md` and `MEMORY.md`.
- **One `USER.md`, read and written by the main agent and every bot — Atlas's own option**, asked
  for by the user: the user is the same for every agent, and each agent is its own identity. The
  memory's lock already re-reads the file inside it (Memory).
- **A bot's memory is its `MEMORY.md`**, written by that bot alone (one writer per
  memory). The main agent reads it as a file when it needs to; it does not write it, and it does
  not carry it in its instructions.
- **Skills** (`atlas.skills.SkillShelves`): the home's library, the bot's own
  folder, and the bot's list of the library's skills it may see (none listed: all of them); its
  own are always its to see, and on a same name its own folder wins. Writing: a new skill is
  created in the agent's own
  folder, in a turn as in the background review, and an existing one is changed where it is
  found; one call changes one folder, and a new skill may not hide one of the library's the bot
  sees. The library does not fill with skills only one bot uses.
  - **Every skill records who created it** (`skills_usage.creator`: the main agent, a bot by
    name, or nobody for the user's own), beside its origin and whose folder it is in
    (`owner`), and `atlas skills list` shows it: asked for by the user, to see who fills the
    library. `atlas skills share NAME --bot BOT` moves a bot's skill to the library, and who
    created it stays.
  - The curator's pass covers each bot's folder, and a deleted bot's rows are dropped.
- **The fixed conversation**: one per bot, made the first time it is asked
  for (`bot.open`), and a click on the bot always opens that one. It lives in the bots' list, not
  among the conversations. Atlas's screen has no
  way to start another in a bot's conversation, and it compacts when it fills, as any does.
- **Instructions that last forever are read again when what they show changes** (a capability
  epoch), made for the fixed conversations: at the start of a turn in a fixed
  conversation the instructions are built again, and replace the stored ones only when they
  differ. The text itself is compared, not a fingerprint of what the prompt shows (the definition,
  the skills, the roster): the same check with nothing
  to keep in step, and so what the text carries counts too: `USER.md` and the bot's
  `MEMORY.md`. Without it a fact told to one bot would reach another only when its conversation
  next compacts. Unchanged, the stored instructions are reused byte for byte, and the
  provider's cache holds. The start date is left out of a fixed conversation's instructions. A
  bot's definition is read from disk each time, so an edit to its mission
  reaches its next turn; its tools and its model are taken when the conversation is opened.
- **A bot's subagent is the bot's**: its conversation carries the bot, and it is given no tool
  the bot is not.
- **A memory proposal says whose memory it is for** (`memory_proposals.bot`): a bot's review
  proposes over the bot's own `MEMORY.md`, and approving applies there; one for the memory of
  a bot since deleted can only be discarded. A proposal for the profile is the home's,
  whoever made it.
- **A deleted bot's conversations are kept**, as every conversation is, and let go of the
  name: a bot made later under it starts its own.
- **A bot's routine** is one of the home's routines, marked with the bot (`jobs_routines.bot`): its
  run is a conversation of the bot's, as the bot, and what it answered enters the bot's fixed
  conversation as a message the bot reads and answers; a silent run and a failed one deliver
  nothing. A bot's `cronjob_manage`
  makes, lists and changes only its own routines, and the main agent's reaches every one. A
  command routine is nobody's. A deleted bot's routines go with it.
- **`message_agent`** (`atlas.tools.agents`, `atlas.assistant.messages`): fire and
  forget, always on in a home that has a bot, in the conversations the user holds with the main
  agent and in each bot's fixed conversation (a routine's run and a subagent are not given it; a
  bot whose definition lists its tools has it only when listed). The roster (handle, title,
  description) is in the instructions with a protocol section; a conversation of the
  main agent's carries the roster of when it started, and the tool checks the target against
  the live one, a name first and then a title nobody else has.
  - **A message enters the receiver's conversation as its next turn**, with who sent it in front
    (`Message from 🤖 <title> (@<handle>):`); messages to one bot run in order, one turn at a
    time. What that turn answers goes back to the sender's conversation as a notice that starts
    a turn there, as a subagent's summary does; silence goes back as nothing to add, and a
    failed or stopped turn as what it was, never as the agent's words. The receiver does not
    also write back with the tool: there is one path.
  - **The main agent has it in every conversation — Atlas's own option**, asked for by the user,
    from V3's use (61 consultations by the main agent, 2 turns typed by the user into an agent's
    conversation). So a bot that writes
    to the main agent reaches the conversation the main agent last wrote to it from; with none,
    it is told to say it in its own conversation, where the user reads it.
  - **A ceiling on messages between agents with no word from the user — Atlas's own**
    (`MAX_AGENT_HOPS`, 6): a rule in the prompt and the silent reply alone do not hold the
    ping-pong. Each message carries how many came before it; past the ceiling the tool
    refuses and tells the agent to stop and say what is pending, for the user to decide.
  - **A message from another agent is never the user's**: it is marked as a notice, counts for
    no review as the user's word, and the instructions say it approves nothing.
  - **Known limit**: a message waiting and an answer on its way live in the server's process,
    with no receipts on disk. A message whose turn already ran is in the receiver's
    conversation, and its answer is not delivered again after a restart.
- **Conversation search stays inside the bot**: a bot finds only its own conversations; the main
  agent finds everything, the bots'
  conversations included (decided with the user).
- **A bot's cost** is read from the call ledger through the conversations that belong to it:
  `atlas usage` sums, by bot, its turns, its reviews and its subagents.
- **Who creates one**: the user, on the screen and on the command line (`atlas bot`); and the
  main agent when asked, with `bot_create`, a deferred tool of the conversations the user holds
  with it, told in the tool's description to ask first and to keep the roster small. A bot made
  with no mission is born with one composed from its title and description.
- **The screen** (studied on 13/10/2026): a Bots tab beside the conversations, each row a
  face, the title, when the
  conversation last changed, the handle and the last thing said; a dialog of three fields under
  the face (name, title, what the bot helps with), and under "Advanced" its model, its mission
  in the user's words, and the tools, MCP servers and library skills it may use; above a bot's
  conversation, whose it is, its routines one click away, and the way into its definition. A
  face is drawn from the name alone, so nothing of it is stored (same name, same face). A routine
  says the bot it runs as, and a memory proposal whose memory it is for.
  The server tells every screen when the roster changes (`bots.changed`).
- **Left out** until use asks: group chats (studied on 13/10/2026, off the roadmap since
  18/10/2026; the roadmap keeps the study), the roster's sections, pins and hiding, a look the user picks and generated faces, a
  voice per bot, a bot introducing itself when made (it costs a model call), bots of
  other machines, shared templates and team bots.
- **Proved** by `tests/bots` (the definition and the folder), `tests/skills/test_shelves.py`,
  `tests/assistant/test_bots.py` and `test_messages.py` (the real assistant against the fake
  model: who a conversation runs as, the profile one bot saves reaching another's open
  conversation, the messages and their ceiling), the bots' cases in `tests/memory`,
  `tests/conversations`, `tests/tools`, `tests/server` and `tests/test_cli.py`, the app's
  `bots.test.tsx` and its e2e scenario `e2e/bots.spec.ts`.

## Plugins — decided 2026-10-14

Step 16, in `atlas.plugins`; the first plugin is Telegram, the proof the user chose. Studied on
14/10/2026 with Claude Code's plugin reference.

- **The format is Claude Code's**, which other harnesses read too: a folder with
  `.claude-plugin/plugin.json`, whose `name` (kebab-case, the folder's name) is
  the only field required. What only Atlas reads goes under `atlas` in the same file: `api` (the
  SDK version), `channels`,
  `secrets` (names in the vault, with where to get them), `settings` (Claude Code's `userConfig`,
  with defaults) and `network` (shown, not enforced). Version 1 loads Python channels; a plugin
  with skills, commands, agents, hooks or MCP servers is listed and refused until Atlas loads them.
- **Where plugins come from:** the ones Atlas ships, in `atlas/plugins/bundled/` (inside the
  package, so the image and the wheel carry them), then the home's `plugins/`, one folder each. A
  user's plugin named as a shipped one is refused (an impostor). Each plugin has a data
  folder in the home's `plugin-data/` (Claude Code's `${CLAUDE_PLUGIN_DATA}`); both travel in a
  backup.
- **Python only through `register(ctx)`** (`atlas.plugins.sdk`, the one module of Atlas a plugin
  imports with `atlas.plugins.acp`, the SDK's ACP client since version 18; import-linter and a
  test hold the shipped ones to them). The context grants only what the manifest
  declares: an undeclared secret, setting or channel raises. **The contract only grows**: nothing
  removed or renamed, new parameters optional and keyword-only, a breaking change
  kept working with a warning for two versions; a plugin written for a newer SDK is refused.
- **Off until the user consents**: turning a plugin on shows what it declares
  (its Python, with no sandbox; its channels; the secrets it reads; the hosts it reaches), and the
  consent is kept as the declaration's fingerprint. An update that declares anything else, not
  only a new capability, turns it off until the user consents again.
- **The agent cannot consent for the user** (found on 14/10/2026, before the step closed): the
  gate treats `atlas plugins enable|disable|secret|set|new`, `atlas plugins
  install|update|remove` (step 16n), `atlas pairing approve|forget`,
  `atlas marketplace install|update|remove` (step 16s) and any
  command that touches the home's `plugins/`, `plugin-data/` or `atlas.db` as an outward action,
  asked of the user every time, past the guardian and "Auto", in every spelling and inside the
  scripts a command runs; the file tools refuse to write in those folders. Without it, a model
  talked into it by a page could write a plugin and run `atlas plugins enable --yes`.
- **Loading has a deadline**: the import and `register` get 10 seconds in a
  thread; past them, or on any exception, the plugin is marked failed with why, and what it
  registers later is ignored. A plugin's code that fails while running stops only that plugin.
- **A channel is one more client of the gateway**, as the app is (`atlas.server.channels`): its
  bridge is a `Connection` attached to its chats' conversations, so it gets their events, questions
  and approvals and answers them as the app does; a conversation goes on in the app and in the
  channel alike, the first answer to a question taking it. What is Atlas's, the same for every
  channel: pairing before anything is read, the
  commands (`/start`, `/new`, `/stop`), which conversation a chat continues (listed in the app with
  the channel as its source), questions one part at a time by button or typed, and who may press a
  button (someone let in; a press on what no longer waits is told so).
- **What an outside executor asks reaches the chat that delegated** (decided with the user on
  17/10/2026): `executor.approval` and `executor.question` go to every client, as the workbench is
  every conversation's, and a bridge shows those whose conversation is one of its chats, with Allow
  and Deny or the executor's own choices, the first answer, there or on the app's card, taking it,
  the other dropped. Who may
  answer is who may answer Atlas's own approvals; the Telegram plugin reads private chats only, so
  nobody else is in the chat (a group would need the owner's rule first: without it, anyone in the
  chat could approve an executor's requests).
- **What a session the user started in the app asks reaches everyone let in** (asked for by the
  user on 18/10/2026: Claude Code's questions in a session of the app never reached Telegram, as
  decided the day before). It has no conversation, so no chat, and goes to the chat of every person
  the channel lets in, each chat with a prompt of its own that names the
  session's folder; the first answer, in a chat or in the app, takes it, and the rest are settled.
  Anyone let in may answer; the user decides who is let in. Each person's chat is the one
  they last wrote from, kept with their pairing (the plugins' migration 2); someone let in before
  is known by a chat named as they are, which Telegram gives a person's private chat. A delegation
  made from a conversation of the app still asks in the app only, as Atlas's own approvals there.
- **A request still open reaches a channel that starts** (it was off, or started again): an
  outside executor's waits with no deadline, so one asked meanwhile used to wait for the app alone.
- **Pairing**: someone new gets an 8-character code from an
  alphabet without look-alikes and their message is dropped; the user approves it on the Plugins
  screen or with `atlas pairing approve`, and they send again. Codes last an hour, at most 3 wait
  per channel.
- **Telegram** (`atlas/plugins/bundled/telegram`): long polling, the
  backlog dropped at start, private chats and text only, each interim text as it comes and the
  reply at the end, with the typing notice meanwhile (The turn on screen). Two choices of Atlas's own: the Bot API over `httpx2` instead of
  python-telegram-bot, since that library's retries have to be fought (the 409 loop,
  a stop hung on a dead socket), and HTML instead of MarkdownV2 (three characters escaped instead
  of eighteen), plain text again when Telegram refuses it. From
  known defects: replies cut at paragraphs and never inside code, counted in UTF-16; a 409
  waited out 10 to 50 seconds then named; a flood wait over 30 seconds fails the send instead of
  freezing the chat; a 401 says to get the token again.
- **Version 2 registers executors** (step 16c, decided on 14/10/2026; the roadmap's harness
  workbench): `register_executor`, with the channel's shape, the plugin only carrying. An
  `Executor` detects its harness with no effect on it (installed, version, account), says what it
  can do (`Capabilities`), starts an `ExecutorInstance` (one account, one folder, one session, many
  turns: `prompt`, `steer`, `set_auto`, `stop`) with Atlas as its `ExecutorHost` (`event`, and
  `ask` and `approve`, which wait with no deadline; the plugin never learns who answered), and
  reads a session back as five kinds of entry every harness shares. `executors` in the manifest
  need `api` 2 and enter the consent's fingerprint only where declared, so a plugin of version 1
  keeps the consent it was given. `ExecutorInstance`, not `Instance`: an instance is one running
  Atlas (decided with the user). The server finds the executors of the plugins that are on at
  every start (waiting 5 seconds, so the first conversation sees them) and whenever one is turned
  on or off, and offers only those installed in a version their plugin drives. Claude Code is the
  first (`atlas/plugins/bundled/claude-code`), moved out of the core with its behavior of step 13
  kept: its `program` setting replaced `[executors] claude_code`, which `config.toml` now refuses
  with the way there. Codex is the second (`atlas/plugins/bundled/codex`, step 16o: Codex,
  above), the Gemini CLI the third (`atlas/plugins/bundled/gemini-cli`, step 16p: Gemini CLI,
  above), over the SDK's ACP client (`atlas.plugins.acp`, version 18), which came with it (decided
  with the user on 20/10/2026, when Hermes, its first user until then, moved after Codex, the
  Gemini CLI and DeepSeek); DeepSeek's harness the fourth (`atlas/plugins/bundled/deepseek`,
  step 16q: DeepSeek, above), over the same client.
- **The workbench** (step 16c; `atlas.assistant.workbench`, the app's `workbench.tsx`): one panel on
  the right, the same in every conversation, collapsed while no instance works or waits. One card
  per executor instance (`ExecutorRun`: executor, account, folder, session, its turns), drawn from
  what its plugin says it can do. The card's prompt starts a turn, or steers the one running; what
  the harness answers stays on the card, never in a conversation; what it asks waits there with no
  deadline, a server request every screen gets (`executor.approval`, `executor.question`, read
  again from `workbench.list`). Two switches, apart: the harness's own automatic mode, which asks
  nothing and so passes over the guardian (the card says so), and "Atlas answers", off by default,
  step 13's "Atlas decides" as a mode: on an instance of the user's, which works for no
  conversation, the guardian and the chosen model answer, reading the user's own prompts to it.
  The menu: duplicate in another folder, change folder (a new session there), change account (the
  harness's own login, then the same session goes on; never while one of its instances works),
  open its conversation (kept hidden, source `workbench`), stop. A task Atlas delegates is an
  instance of Atlas's, on the workbench while it works, its cards there instead of step 13's badge.
  Since step 16f the panel is gone and its cards with it: each instance is a session in the
  executors tab (The executors tab and the side panel, below), and the workbench runs underneath.
- **The delegation record** (step 16c; `jobs_instances`, `jobs_turns`): every instance, the user's
  and Atlas's, and each of its turns, lean, a short summary and a pointer: the prompt and its steers, what it asked and who answered (`user`,
  `atlas`, `nobody`), the final text and the files its tool calls wrote. It outlives the harness's
  own transcript; nothing is copied to a `.md`. A new Atlas finds what an earlier one left running
  ended. `delegation_search`, deferred, is `conversation_search`'s four shapes over it: browse
  (filters by executor, folder, dates), discover (FTS5 over the record only, decided with the
  user), read (`instance_id`: the record and the start and end of the harness's session, read back
  normalized by its plugin, with a word when the harness deleted it) and scroll (`around`); a
  tool's input and output only when asked. Its id is `instance_id`, not the roadmap's
  `delegation_id`: a delegation is one `delegate` call's batch (GLOSSARY.md). `delegate`'s `list`
  shows the workbench and whose each instance is; steering or stopping the user's own asks them
  every time, past the guardian and "Auto" (`ApprovalGate.ask_first`), as the agent cannot consent
  for the user in step 16.
- **Detection** (step 16c): at every start, and whenever a plugin is turned on, off or set, Atlas
  looks for the harness of each executor plugin it ships, on or off (their code is Atlas's own,
  shipped with it; a plugin of the user's runs only once consented to). The onboarding (the key
  screen) and an empty workbench show every harness supported: one found to turn on, its consent
  given right there after reading what its plugin declares; one not found, or too old, greyed out
  with why. The workbench offers to start only those on and found.
- **Detection, reviewed** (asked for by the user on 18/10/2026, every defect found fixed):
  - **The screen asks too** (`executor.detect`): when the executors tab shows, at most once a
    minute, and on the offers' "Look again". Before, only a start or a plugin turned on or off
    looked, so a Claude Code installed, updated or logged in while Atlas ran was found only after
    a restart. A setting changed from the terminal is looked at again too. A look tells the
    screens only what changed, keeps an executor found as before (with the models it read), and
    the app reads the executors alone when the plugins change: the whole workbench read so could
    answer after an instance's own event and put a working session back to idle (found by the CI
    on 18/10/2026, once the tab looked as it showed).
  - **Claude Code is found as the Agent SDK finds it** (`_find_cli`, 0.2.165): the PATH, on
    Windows a native `claude.exe` only (a `.cmd` early on the PATH no longer hides one later on
    it), then where its installers put it, since the PATH the app started with can be older than
    the install. Atlas's own additions to the SDK's places: WinGet's (the user's Claude Code comes
    from there) and Homebrew's on a Mac. npm's `claude.cmd` on Windows is refused as the SDK
    refuses it, saying so and how to install the native one, where it used to say "not on the
    PATH".
  - **What is not usable says why and what to do**: not installed, the install command of Claude
    Code's documentation; too old, how its install updates (`claude update`, or WinGet's
    `winget upgrade Anthropic.ClaudeCode`); the screen shows it in full, where a harness not
    installed used to say only "not found".
  - **A failed look is a finding, never a plugin turned off**: a detection that took too long or
    raised, and a setting the plugin refuses, are a row saying why, and the plugin stays on; before,
    it was marked failed and its harness left the screen, the onboarding's included, until the
    plugin was turned on again.
  - **Read as Claude Code writes it** (2.1.296, 18/10/2026): `claude auth status --json` exits 1
    when nobody is logged in and still says so, so it is read whatever the exit code; the version
    is found on the line that names Claude Code and compared by its numbers (`2.1.300-beta.1` is no
    longer "older"); both are asked at once, so a slow first start costs one wait, under the
    look's deadline. A harness nobody is logged in to says so (`ExecutorRow.logged_in`), and its
    account's switch logs in.
  - **One Claude Code folder**: `CLAUDE_CONFIG_DIR` reaches the program Atlas runs, as the Agent
    SDK passes it on. It was left out with the credentials, while the executors tab read the
    sessions from it, so a user who keeps Claude Code there had their runs in one folder, logged
    in elsewhere, and their sessions listed from the other.
- **Left out of version 1:** groups, photos, voice and files, the reply streaming as it is written,
  the questions an outside executor asks (they stay with the app), a plugin's skills, MCP servers
  and hooks, a plugin's own screen, and installing a plugin from a URL.

### What a plugin brings beside channels and executors — decided 2026-10-20

Step 16j, asked by the user for "the definitive plugin SDK". Its shape comes from the two plugins
the user is writing, both kept in their home until a plugin store exists: a client's ERP (its MCP
server and skills, no Python) and video made with Remotion (skills, a tool, Claude Code when it is
installed through `delegate`, the Remotion Studio in the side panel through `desktop_preview`).
Studied on 20/10/2026 with Claude Code's manifest reference of that day. The guide for whoever writes a plugin is [Writing a plugin](../src/atlas/docs/pages/extending/plugins.md).

**How people learn to write one** (step 16t, decided by the user on 25/10/2026):

- **The guide names the whole SDK:** its sections, channels and executors included, and a last
  one listing every name of `atlas.plugins.sdk` and `@atlas/plugin-sdk`. A test of each side
  fails when the SDK grows a name the guide does not say, so the guide cannot fall behind.
- **Atlas writes one with the user:** the bundled skill `write-plugin` picks the smallest kind
  (a skill, an MCP server, a plugin), makes the plugin in the conversation's folder with `atlas
  plugins new --folder`, checks it with `validate` and installs it from there; `new`, `install`
  and `update` are asked of the user by the gate, and turning it on is theirs on the Plugins
  screen. It publishes in the marketplace only when asked. Its `references/guide.md` is the
  guide, a test keeping the copy equal.
- **The app points there:** "Make your own" in the marketplace and "Write a plugin" on the
  Plugins screen open a new conversation with `/write-plugin ` in its box, beside a link to the
  guide.

- **Skills, Claude Code's:** the folders of a plugin's `skills/` that hold a `SKILL.md`, read
  where they are while the plugin is on, listed after the user's and named after the plugin
  (`video:render`), so they never hide one of the user's. `skill_manage` never writes them, and
  the curator never archives them (their use makes no row). A bot that lists its skills sees a
  plugin's only when it lists it by that name.
- **MCP servers, Claude Code's:** a plugin's `.mcp.json`, and its manifest's `mcpServers`, the
  manifest's winning a name both hold. Each is named `plugin_<plugin>_<server>` (Claude Code's
  `plugin:<plugin>:<server>`, `_` for the `:` a server's name cannot hold; its tools
  `mcp__plugin_<plugin>_<server>__<tool>`, as in Claude Code), connected when the plugin is turned
  on and stopped when it is turned off, the user's own `mcp.json` untouched (`McpClient.
  reload_plugins`); a name the user's file holds too is the user's. `${CLAUDE_PLUGIN_ROOT}` and
  `${CLAUDE_PLUGIN_DATA}` become the plugin's folder and its data folder, written with `/`.
  **Found while designing it:** the MCP client fills any `${NAME}` from the whole vault, so a
  plugin's server could have read `${ANTHROPIC_API_KEY}` without declaring it. A secret a plugin's
  server names is declared by being named: shown at consent and counted in its fingerprint, as one
  of `atlas.secrets` (decided with the user on 20/10/2026, after step 16k found Claude Code's
  GitHub and Terraform plugins refused for it; before, the manifest was refused).
- **Tools, `register_tool`** (SDK version 14): name, description, a JSON Schema object
  and `run(arguments, host)`, the manifest naming each under `atlas.tools`. Behind the tool search
  as an MCP server's (its source `plugin:<name>`), through the same gate and approvals as Atlas's
  own; one named as one of Atlas's is left out and logged (a plugin cannot override one).
  `host.progress(text)` goes under the call on the screen (`tool progress`). A tool that raises
  fails the call; the plugin stays on.
- **Stops, `on_unload`** (version 14): run when the plugin is turned off, when what
  registered them is loaded again, and when the server closes; one that raises is logged and the
  others run.
- **The declaration grows with them,** and so does the consent's fingerprint, only where declared
  (a plugin of an earlier version keeps its consent): the tools, each MCP server with its command
  or its address (a server that runs another command asks again), and the skills.
- **For whoever writes one:** `atlas plugins new <name>`,
  a folder in the home's `plugins/` with a manifest, a tool and a skill; `atlas plugins validate
  <folder>`, the folder read by the real
  loader and its Python loaded in a home thrown away after, saying what it registers and what is
  wrong. The gate treats `atlas plugins new` as the user's decision, as `enable`; `validate`
  touches no home of the user's.
- **Left out of step 16j,** and brought by step 16k (below): hooks, agents, `bin/`, `userConfig`,
  commands and monitors.

### The whole of Claude Code's plugin folder — decided 2026-10-20

Step 16k, asked by the user after 16j: "not the moment to write plugins, but to complete the SDK,
ready for any plugin I want to write". Everything Claude Code's plugin folder holds that Atlas did
not load, from Claude Code's plugin and hooks references of 20/10/2026, so a plugin written for Claude Code runs in Atlas unchanged.

- **Hooks** (`atlas.hooks`), the event system Atlas did not have: a plugin's `hooks/hooks.json`
  (and its manifest's `hooks`), Claude Code's file, events and JSON. Atlas fires
  `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure` and
  `Stop`; a hook on another of Claude Code's events is read, shown at consent, and never runs. A
  command hook gets the event on its stdin, in the conversation's folder, with Claude Code's
  variables; its answer is Claude Code's (exit 2 blocks, JSON with `decision` and
  `hookSpecificOutput`). The hooks of an event run together, each with its timeout; one that fails
  lets the action go on unless it says `onFailure: "block"`. Python hooks
  (`PluginContext.register_hook`, SDK version 15, the events named under `atlas.hooks`) get the
  same JSON and answer the same way. **Atlas's own choices, marked:** a matcher is tried on
  Atlas's tool name and on Claude Code's name for the same tool (`Bash` for `run_command`), so a
  hook written for Claude Code's tools reaches Atlas's; and `allow` lowers nothing (`deny` blocks,
  `ask` asks, the user's gate decides the rest), so a plugin never lowers the
  user's approvals. A hook's `if` (Claude Code's permission rule, `Bash(git *)`) is matched on the
  call too; `async` runs it in the background, and `asyncRewake` brings what it says on exit 2 to
  the model as a notice.
- **Read as Claude Code reads it,** found by loading all 54 plugins of Claude Code's official
  marketplace through `validate` on 20/10/2026 (42 load; the 12 LSP plugins do not): the secret
  an MCP server names is declared by being named (above, 16j); the manifest is optional, the
  plugin then named after its folder (`receipts`), when the folder brings anything a plugin
  brings; and a `.mcp.json` may hold the servers alone, without `mcpServers` around them
  (GitHub's plugin, which was read as having none).
- **Commands,** Claude Code's `commands/<name>.md`, read as skills as Claude Code reads them now:
  named by the file and the plugin (`/video:review`), the frontmatter optional, a skill of the
  plugin winning a name both have. Read only, like the plugin's skills.
- **Programs,** Claude Code's `bin/`: while the plugin is on, its folder comes first on the `PATH`
  of the commands the model and the routines run (`Commands.path`), as Claude Code puts it on its
  Bash tool's.
- **Options,** Claude Code's `userConfig`, as strict as Claude Code reads it: an option is the
  plugin's setting, and a `sensitive` one a secret named `<PLUGIN>_<KEY>` (`ERP_API_TOKEN`), so two
  plugins never share one. An MCP server reads one as `${user_config.KEY}`: a secret stays a
  `${NAME}` for the MCP client to fill, and a setting's value has any `${` taken out, so a value
  the plugin defaults or the user types never names another secret. A hook gets each as
  `CLAUDE_PLUGIN_OPTION_<KEY>`, and an exec-form hook as `${user_config.KEY}` in its arguments; a
  monitor's command takes none and a default that names a value is refused, as in Claude Code.
- **Agents,** Claude Code's `agents/<name>.md`, as **predefined subagents** of `delegate` (decided
  by the user on 20/10/2026: what Claude Code's are, a helper for one task with no memory; a
  plugin's bot, with a memory and a conversation of its own, waits for a plugin that asks).
  `delegate` lists them (`video:editor` and when to use it) and a task's `agent` runs one: its
  instructions before the task's context, Atlas's own tools kept to those its `tools` names
  (Claude Code's names read as Atlas's, as a bot's `tools` keeps them, its MCP tools untouched),
  and its `model` when it is `provider/model` (Claude Code's aliases name no provider of Atlas's,
  so they run on the parent's). On an outside executor an agent is refused.
- **Monitors,** Claude Code's `monitors/monitors.json` (or the manifest's `experimental.monitors`):
  a command run in the background of each of the user's own conversations, from its first turn
  (`always`) or from the first use of a skill (`on-skill-invoke:<skill>`), through the shell a
  hook's command takes; each line reaches the model as a notice. Subagents, routines and the
  review run none, as Claude Code runs them only in interactive sessions. It stops with the
  conversation, and when the plugins change; one that ends by itself is said once. **Atlas's own
  choice, marked:** in Claude Code each line is a notification; in Atlas a notice is a turn, which
  costs a model call, so the lines within a second come as one notice, cut at 50 lines and 4,000
  characters.
- **The declaration grows with each,** and the consent's fingerprint only where declared: each
  hook and the events the Python hooks into, the commands, the programs, the subagents and each
  monitor with its command.
- **Still left out:** LSP servers (code intelligence for an editor, which Atlas is not), output
  styles (Atlas has `SOUL.md`) and workflows (themes came in SDK version 24: colors as
  data in `themes/<name>.json`, offered in the settings beside light and dark while the plugin is on); and, from the user's list of 20/10, a
  plugin's own screen (16m) and, for whoever writes and installs (16n), reloading as files change,
  installing from a URL and dependencies.

### The rest of a plugin's Python — decided 2026-10-20

Step 16l, SDK version 16: what else a plugin's Python may do, each declared under `atlas` and shown
at consent.

- **Slash commands, `register_command`:** a command the manifest names under
  `atlas.commands`, typed `/<plugin>:<name>` (Claude Code's naming for a plugin's commands, so it
  never takes one of Atlas's or the user's skills), in the app and in a channel. Its code gets what
  follows the name and the conversation, and what it returns is the reply, with no model call; one
  that raises ends the turn as failed with its message. The box's `/` lists them among the
  commands. **Atlas's own choice, marked:** the exchange is kept in the conversation, so the model
  reads what the user saw at its next turn, rather than printed aside.
- **Terminal commands, `register_cli_command`:** `atlas <name> ...`, the manifest naming
  each under `atlas.cli`; a name of Atlas's own commands never reaches a plugin. Its code gets the
  arguments as typed and returns the exit code; Atlas hands the arguments, not an argparse
  subparser, so argparse is not in the contract. The stops it registered run before Atlas exits.
- **The user's model, `ctx.llm`** (`atlas.assistant.AssistantPluginHost`): one completion
  on the model a conversation that picked none uses, no tools, no reasoning asked for, in the call
  ledger as `plugin:<name>`. With `json_schema`, the schema goes in the instructions and the reply
  is parsed, inside a code fence too, and checked when `jsonschema` is installed;
  one that does not fit is `parsed = None`, the text kept. **Marked:** a call
  costs the user's credit on the plugin's own initiative, so the manifest says `"llm": true` and the
  consent says so. Provider and model overrides are left out
  until a plugin asks.
- **A message to a conversation:** `ctx.notify(conversation, text)`
  brings the text as a notice, which starts a turn or waits for the one running; `"notify": true`
  and the consent. A tool learns the conversation it
  runs in from `ToolHost.conversation` (a render that ends after the turn says so there).
- **Model providers:** `atlas.providers`, each as `models.toml`'s
  `[providers.<id>]` writes one (an OpenAI-compatible address at least), added to the catalog while
  the plugin is on and taken away when it is turned off. A plugin adds providers and never changes
  one: an id Atlas or the user has is refused, and marks the plugin. Its `key` is a secret,
  declared by being named, as an MCP server's.
- **Without a server** (a terminal command), `llm` and `notify` say Atlas is not serving.

### A plugin's own screen in the app — decided 2026-10-21

Step 16m. **Decided with the user:** a screen runs inside the app with all the app's access, no
sandbox; the consent says so. Isolating it in a frame of its own was offered and left for when the
store opens to other people's plugins.

- **The file:** a plugin's `desktop/plugin.js`, plain ESM loaded
  uncompiled. The server hands its text (`plugin.screens`, read again on `plugins.changed`), so a
  home on another machine shows its plugins' screens too, never read from the app's own disk.
- **The loader** (`apps/desktop/src/plugins/loader.ts`): the imports allowed are
  `@atlas/plugin-sdk`, `react` and `react/jsx-runtime`, rewritten to shim blobs of the app's own
  modules, and anything else refuses the screen (a screen cannot pull code from an address); a
  blob `import()` with a deadline of 10 seconds; a default export whose `id` is the plugin's name
  and whose `register(ctx)` adds the contributions. The page's policy takes `blob:` scripts for it.
- **What a screen adds, of the areas the app has:** a tab of the side panel (`panel`) and a page
  of the main area with its entry in the sidebar (`page`). A status bar, a palette and keybinds are
  surfaces the
  app does not have, and are left out until it has them.
- **The context, cut to what the app has:** `register`, `onEvent` (the home's events),
  `onDispose`, `setTimeout`/`setInterval` and `storage`, each taken away with the plugin, and
  `call`, the plugin's Python (`register_call`, SDK version 17) over
  Atlas's own connection instead of a REST namespace. `@atlas/plugin-sdk` gives `host.state` (the
  connection, the conversation and the home on screen), `host.request` (the server's methods, as
  the app calls them), `host.openPage`, `host.togglePanel` and `useValue`.
- **Errors stay with the plugin:** each contribution renders inside an error boundary of its own,
  and a screen that fails to load is said on the Plugins screen.
  **Found while testing it:** the first boundary held the component that called `render()`, so a
  throw there escaped it; the call moved inside.
- **Proved** by the app's tests (the imports rewritten and refused, the contributions sorted and
  taken away, a page that throws shown in its place) and end to end in the real app: a plugin's
  page in the sidebar whose button calls its Python, its panel following the app's state, a screen
  that imports `node:fs` failing alone, and both going when the plugin is turned off.

### For whoever writes and installs plugins — decided 2026-10-21

Step 16n. Researched on 21/10/2026 with Claude Code's plugin docs (`/reload-plugins`,
`--plugin-dir`, marketplace sources, `dependencies`).

- **Reloading on save, Atlas's own, marked.** Atlas's server looks at the user's plugins every
  second (each
  file's path, size and time, skipping `.git`, `node_modules`, `__pycache__` and virtual
  environments, at most 5,000 files a plugin) and at their state, and loads again a plugin whose
  stamp changed once a look finds nothing newer: its Python imported afresh, its channels started
  again, a failure cleared for another try, and the app told, which loads a changed screen again
  in place, its page kept on screen. The same stamp sees a plugin turned on, off or set from the
  terminal, which before waited for a restart. `plugin.reload` (the Plugins screen's Reload) is
  Claude Code's `/reload-plugins`. A changed declaration still waits for consent, its fingerprint
  changed. What a plugin writes belongs in its data folder: one writing in its own folder would load
  itself again and again.
- **Installing by address** (`atlas plugins install`, `update`, `remove`, in
  `atlas.plugins.install`): a git URL, GitHub's `owner/repo`, with `#folder` for a plugin inside a
  repository, GitHub's `/tree/<ref>/<folder>` pages, and a folder on this computer. `--ref` takes a
  branch, a tag or a commit, Claude Code's `ref` and `sha`. The plugin lands in the home's
  `plugins/<name>` without `.git`, where
  it came from in `plugins/.install-metadata.json`; it is checked as Atlas loads it before
  it takes its place, arrives off, and never replaces a plugin Atlas ships or one the user wrote in
  the folder. Removing takes away only a plugin installed by address, and keeps its data folder
  (Claude Code's `--keep-data` as the default, Atlas's own). Claude Code's marketplaces wait for
  the catalog the roadmap opens later.
- **Dependencies are Claude Code's** (`dependencies` in `plugin.json`: `"name"`, `"name@market"`
  or `{name, version, marketplace}`, the version an npm semver range, read in `atlas.plugins.model`
  as npm reads it). A plugin runs only while what it needs runs, in the versions asked; one turned
  off or failed stops those that need it, which say why, as Claude Code keeps them disabled.
  Turning off one that another on needs is refused, as in Claude Code. **Atlas's own, marked:**
  Claude Code turns the dependencies on with the plugin, but each needs its own consent here, so
  the user is told which to turn on first; and a dependency missing is said, not installed, since
  there is no catalog to find it in. They load first (each after what it needs,
  ties by name; plugins needing each other in a circle keep their order).
- **Left for later:** a plugin's Python packages (installed with consent, through uv),
  Claude Code's marketplaces and auto-update.
- **Proved** by tests on real folders (the ranges, the shapes, a chain stopped by one failure, the
  load order), installs cloning real git repositories made in the test (at a commit, from a folder
  of one, updated with and without a change, a declaration that grows asking again, removed with
  its data kept, nothing left by a failed one), a real server loading a saved plugin's Python and
  screen, a broken save and its fix, and a plugin turned off and on from the terminal; and end to
  end in the real app, a page that changes in place when its author saves both halves.
  **Found while testing it:** loading a screen again took its page off the screen.

### The marketplace — decided 2026-10-25

Step 16s, asked for by the user on 24/10/2026: a place in the app to find and install what other
people made for Atlas, a tab per kind, the Atlas team's items marked, a page per item. In the code,
`atlas.marketplace`; `store` already names the plugins' state and the app's screen state.

- **No server of Atlas's** (decided by the user): the marketplace is a public repository on GitHub,
  `marqueskaio/atlas-marketplace`, with no download counts and no ratings yet. **The user reviews
  every item** that enters, by its pull request; more reviewers and more automation come later.
- **The repository:** `marketplace.yaml` says its format (a newer one is refused whole, with the
  advice to update Atlas); `items/<name>.yaml`, one file per item, so two pull requests open at once
  never change the same line; and `removed.yaml`, the items retired for safety.
- **Four kinds, apart** (decided by the user: whoever wants a skill gets the skill, not a plugin
  that brings skills):
  - a **skill**: one folder with a `SKILL.md`. Into the home's library through `SkillLibrary.put`,
    which keeps a version before every write. It says what it needs under `metadata.requires`
    (`Needs`, in `atlas.skills`): programs (`bins`), checked and said, the skill installing anyway;
    MCP servers (`mcp`), installed along with it, each the item it is; systems (`os`), outside which
    it is refused. A script's libraries travel in the script (Python's inline metadata, run with
    `uv run`; npm's `npx` at an exact version), never onto the machine. A skill brings no secret:
    the commands the model runs never see the vault (Secrets), so what needs a key is an MCP server,
    which receives it, or a plugin;
  - an **MCP server**: its `mcp.json` entry, written into the home's file with the rest left as it
    was; a server of the same name there refuses it, and a file that cannot be read is never
    written over. What it runs is pinned: a package at one exact version (`npx name@1.2.3`, `uvx
    name==1.2.3`, which the registries never let change), an image by its digest, or an `https`
    address; `oauth`, `headersHelper` and `cwd` are refused, and each `${NAME}` it reads is declared,
    and stored from its page;
  - a **plugin**: the bundle (skills with its own MCP server, hooks, a screen, Python). Checked as
    Atlas reads a plugin and put in place by `atlas.plugins.install.place`, off: turning it on is the
    consent of the Plugins screen. Recorded as `marketplace:<name>`, so `atlas plugins update`
    sends the user to the marketplace;
  - an **executor**: a plugin that registers one, listed apart.
- **Pinned to what was reviewed:** a skill, a plugin or an executor names a GitHub repository, its
  folder and a whole commit; a new version is a new pull request moving the commit, and the reviewer
  reads what changed. Atlas fetches GitHub's archive of that commit over HTTPS, unpacked with the
  standard library's data filter, so no git is needed (the container has none) and no file lands
  outside its folder. Compiled files are refused (by their first bytes and their names), and so is
  an item whose code is not the kind it says. Its pictures are files of its own repository at its
  commit, so the page shows what was reviewed too.
- **Reading it:** the repository's archive at its branch, one request; the index read from it is
  kept in the home's `marketplace/index.json` and stands in when GitHub cannot be reached, which
  the listing says. A read older than an hour is read again when the listing is asked for. The
  addresses are `[marketplace]` settings, for a mirror and for the tests.
- **What came from it** is `marketplace/installed.json`: each item's revision and, for a skill or
  an MCP entry, a digest of what the home held right after. An **update** takes the new revision;
  one changed in the home since (by the user, or a skill by the review, which improves it as it
  improves the user's) waits for the user's word (`force`), the skill's versions keeping what it
  was. The curator never archives a skill from the marketplace. **Removing** archives a skill,
  drops an MCP entry (refused while a skill that brought it is installed) and takes a plugin away,
  its data kept.
- **Retiring:** when the marketplace lists an installed item in `removed.yaml`, by name or by
  repository, it stops as removing stops it, and stays listed as retired, with the reason.
- **Official:** an item by the Atlas team, marked by the maintainer alone. What Atlas ships is
  listed as official (its executors, its plugins and its skills), turned on rather than installed;
  a shipped skill the user deleted installs back as Atlas ships it.
- **The server installs,** never the app: the home may be on another machine. It reads
  `mcp.json` again once it changes (`McpClient.reload_file`), by the marketplace or by hand: new
  servers connect, those gone or changed stop, the others stay. Every client hears
  `marketplace.changed`. The terminal has the same: `atlas marketplace list|show|install|update|
  remove`. From the agent, `install`, `update` and `remove` are the user's decision, asked every
  time as `atlas plugins enable` is: an MCP server connects as it is installed, and a skill changes
  what the model reads.
- **The marketplace's own CI** runs `atlas marketplace check --load` on each pull request: every
  item read by these rules, its code fetched at its commit and checked for its kind, and a plugin's
  Python loaded in a home thrown away after (only there: `check` without `--load` runs nothing).
- **The screen:** a Marketplace entry in the sidebar opens it in the main area: a search, a tab per
  kind with its count, the Atlas team's items before the community's, a card each, and a page with
  the README at the commit (a skill's own `SKILL.md`, which is what the model reads; an MCP server's
  entry as JSON), drawn with its raw HTML skipped and its addresses kept to the item's tree or
  `https`; what the item needs here; and its secrets, stored from the page and never shown again.
  "Turn on" opens the Plugins screen over it, and a plugin turned on or off there reads the list
  again, so its card follows.
- **Left for later:** download counts and ratings, on a service of the user's own; MCP servers that
  sign in with OAuth; repositories outside GitHub; other people's marketplaces; updating on its
  own; and isolating a plugin's screen, studied again if review alone is not enough.
- **Proved** by tests against a fake GitHub over real HTTP serving real git repositories made in
  the test (each kind installed, a skill bringing its server, updates kept and forced, removal,
  retiring by name and by repository, the last read standing in), the MCP client reading a changed
  file against real servers, the server's methods with two clients, the app's screens, and end to
  end in the real app, as far as the model receiving the marketplace's skill and a plugin's. The
  end-to-end MCP item asks for two secrets and gets one, so `npx` never fetches a package there.

### Voice — decided 2026-10-24

Step 17a, asked for by the user on 24/10/2026: sending audio from Telegram and the app, and talking
to Atlas by voice with the microphone open. One plugin Atlas ships, `voice`, off as the others:
both halves hear and speak through the same provider. **No voice in the core** (decided with the
user): the core gains openings in the plugin SDK that any plugin may use, and the plugin brings the
rest. Everything goes through OpenAI, on the `OPENAI_API_KEY` the vault already holds (decided by
the user: the voice conversation is to be the best there is, paid).

- **Attachment readers** (SDK version 21, `PluginContext.register_reader`, `atlas.readers` in the
  manifest, shown at consent and counted in its fingerprint): a plugin's code that turns one kind of
  attachment into text before the turn, `audio` for now. Atlas hands it the file's path and media
  type and keeps what it returns as the attachment's `description`, the field an image's
  description already uses, so a file is read once, the stored message keeps it, and no protocol
  or codec changes. The attachments' one piece (Attachments, above) gives it to the model in the
  note's place, saying a speech model wrote it down and may have misheard a word. One reader a kind,
  the first loaded, the others logged; a reader gets 2 minutes, and one that fails, says nothing or
  runs out leaves the note, logged. **Atlas's own, marked:** an image is described only as a
  turn starts, but a voice note sent while a turn runs is common on a phone, so a steer with a file
  to read is read first and delivered then, steering the turn or starting the next one.
- **The voice plugin's reader** (`atlas/plugins/bundled/voice`): OpenAI's
  `audio/transcriptions` with `gpt-transcribe` (released 05/08/2026, US$ 0.0045 a minute), the
  file sent as it is (Telegram's OGG voice notes, MP3, M4A, WAV and WebM up to 25 MB, past
  Atlas's 20 MB for audio). The model and the address are settings.
- **Atlas speaks only when asked** (decided by the user): receiving audio never makes Atlas
  answer in audio. Speaking is the plugin's tool, `voice_speak`, which the model calls when the
  user asks to hear the answer: OpenAI's `audio/speech` with `gpt-4o-mini-tts` and the voice
  setting, as Opus in an Ogg file, a text past 4,096 characters cut at a sentence.
- **Files a tool sends the user** (SDK version 22, `ToolHost.send_file`): Atlas copies the file
  into the home's attachments (a backup carries it), tells the conversation's listeners
  (`FileSent`), and returns where it is kept. The server sends it as `message.file`, an
  `AttachmentRow`: the app shows it as a part of the turn, a chip that opens beside; a channel's
  bridge hands it to a channel that sends files (`FileChannel.send_file`, a method of its own, so
  a channel written before keeps working), and gives one that does not a line naming it. Telegram
  sends an Ogg voice note as its voice message (`sendVoice`, which plays in the chat), other audio
  as audio and anything else as a document, as a multipart upload. **Left out:** a file sent is a
  live event, not stored in the history, so a conversation reopened shows the tool's result naming
  where it is kept, not the chip.
- **The plugins' screens, version 2** (`SCREEN_API`): a third area, `box`, a button beside the
  words of the conversation's box (Atlas's and a bot's, not an executor session's), each in its
  own error boundary; `host.insertText`, words put after what the box holds, for the user to read
  and send; and `host.send`, a message sent as the box sends one, which makes the conversation
  when none is open.
- **Dictation** (decided by the user: the box's microphone dictates, it does not send the audio):
  the screen records with the browser's recorder, its Python has OpenAI write it down (the
  `dictate` call, base64, 10 MB at most), and the words land in the box.
- **The live conversation**: OpenAI's `gpt-live-1` talks with the user over WebRTC from the
  app, listens while it speaks, takes being interrupted, and has no tools. Its persona, Atlas's own
  words, tells it to hand over everything that needs work, facts or memory, and to say it is
  checking meanwhile, never a result it does not have.
  - **The key stays on the server:** the screen makes the WebRTC offer, and the plugin's Python
    exchanges it for OpenAI's answer (`POST /live/sessions`, the `live_session` call), with the
    conversation's last 24 text messages as the voice's history. Only the answer reaches the app.
  - **Each request handed over (a delegation) is a turn of the conversation** on screen, sent as
    the box sends one: the user's last words are the message; the spoken exchange goes to the
    plugin's Python first (`live_turn`), whose `UserPromptSubmit` hook adds Atlas's note after the
    words: it is speech, it will be spoken, answer briefly with no Markdown, never claim what is
    not done. The note is the hooks' `additionalContext`, so no core change carries it, and a
    typed message gets none. A tool starting is told to the voice as quiet context
    (`session.thinking.append`); the answer, stripped of Markdown, code and addresses, is given
    to it to say (`session.commentary.append`), in parts the voice model takes.
  - **Which model answers** (decided by the user): the plugin's `voice_turns_model`, GPT-6 Luna
    with no reasoning by default, or `conversation` for the conversation's own. The screen picks
    it for the conversation as the voice starts (`conversation.model`) and puts the conversation's
    back as it ends, also at the next start when the window closed meanwhile. **Atlas's own,
    marked:** the conversation's model is switched rather than one turn's, since the protocol picks
    a model per conversation; the model picker shows it while the voice runs.
  - **It ends** on its button, on a stop phrase said alone ("stop", "pare", "tchau"), or after
    the plugin's `quiet_close_seconds` of silence (120 by default; 0 never), since OpenAI bills
    the session's time, silence included, at US$ 0.05 a minute. **Atlas's own, marked:** a
    session forgotten open would bill on. A request Atlas still works on is never cut for silence.
  - **Left out:** the conversation chained from transcription, a turn and speech, which only a
    home without OpenAI's key would need; a request handed over while a turn runs steers it, as a
    typed one does, rather than stopping it.
- **Proved** against the fake model server, which takes OpenAI's multipart transcription, its
  speech and its live sessions: the manifest and its refusals, a reader not declared, one reader a
  kind; an audio file written down once, kept with it and read again at the next call; no key and a
  refusal leaving the note while the turn goes on; a voice note sent while a turn runs reaching the
  model written down; a Telegram voice message through a real server and the Telegram plugin; the
  spoken answer reaching the app as `message.file` and Telegram as `sendVoice`; the dictation; the
  live session opened with the key and the history, refused without an offer; and a spoken turn
  carrying its note once. The screen's engine in Vitest, with a fake link and a fake conversation:
  what is handed over and with which note, the tools and the answer told to the voice, a failed
  turn, the silence that closes and the one that does not, the stop phrase. End to end in the real
  app, with Chromium's fake microphone: the two buttons in the box, a dictation landing in it unsent,
  and the live conversation's session reaching OpenAI's endpoint with the app's offer and the key
  from the server; the fake answers no real WebRTC, so the voice itself, spoken and heard, waits for
  the user's proof with OpenAI.

### Dictation anywhere — decided 2026-10-25

Step 17b, asked for by the user on 24/10/2026: dictating into any field of the computer, not only
the conversation's box. Researched on 24/10/2026: dictation apps hold a key to talk, tap it twice
to talk hands-free, show a small bar that never takes the focus, and paste the words through the
clipboard with a paste key pressed for the user, putting back what the clipboard held. Still the
voice plugin's, and still no voice in the core: screens gain what they may do outside the window.

- **Desktop powers** (SDK version 23, `atlas.desktop` in the manifest, `shortcut` and `paste`,
  needing a screen, shown at consent and counted in its fingerprint). The server hands them with
  the screen (`PluginScreenRow.desktop`), and the app gives a screen only the ones it declares.
- **The plugins' screens, version 3** (`SCREEN_API`): `ctx.shortcut(id, accelerator, run)`, a key
  heard in every program while the app is open, as Electron names keys, resolving `''` or why it
  cannot be held (another program's, another plugin's, no key at all); `ctx.paste(text)`; and
  `ctx.bar(state)`, the floating bar. All three are let go with the plugin.
- **The shortcut** is Electron's `globalShortcut` (`electron/desktop-powers.ts`), which hears a key
  go down and never come up: a press records and the next stops (decided by the user: press to
  talk, press to stop), and a key held, repeating, is heard once. Holding the key to talk, the
  double tap and a shortcut of modifiers alone need a native key hook, and wait for it.
  **Found while testing it:** Caps Lock as the shortcut still flips the capitals as the system reads
  it, so the app presses it once more for the user, the shortcut let go meanwhile so that press
  is not heard as the user's.
- **The paste**: with the app's window in focus, the words are typed where its cursor is
  (`webContents.insertText`), the conversation's box or an executor's terminal alike, the clipboard
  untouched. Else they go on the clipboard, a paste key is pressed, and after 600 ms the clipboard
  gets back its text, HTML, rich text and image, unless something else was copied meanwhile. The
  keys are the system's own: on Windows a PowerShell kept running with a few system calls compiled
  in (`SendInput`), on a Mac `osascript`, on Linux `xdotool` or `wtype`; the app ships no native
  library. On Windows a program running as administrator refuses an ordinary program's keys without
  a word, so the helper reads the rights of the window in focus first, and above the app's (or
  unreadable) the words stay on the clipboard and the bar says to paste them.
- **The bar**: a frameless window above the taskbar of the screen the mouse is on, never focusable,
  ignoring the mouse, its page a fixed text and dot. It closes with the app's window, which would
  otherwise keep the app from quitting.
- **The voice plugin's half** (`desktop/plugin.js`, `dictations.py`): the `dictation_shortcut`
  setting, `Capslock` by default (decided by the user); the words written down raw, as the box's
  microphone writes them (decided by the user: no model cleans them yet). **Atlas's own,
  marked:** a recording under half a second is dropped unsent, one stops on its own at 10 minutes
  (the `dictate` call's 10 MB), and a press while the words are being written down starts nothing.
- **Dictations kept** (decided by the user: in the home, until deleted): each recording and a JSON
  beside it in the plugin's data folder, which a backup carries. **Atlas's own, marked:** the
  box's dictations are kept too; the recording is kept before it is written down, so a failed
  transcription keeps it with why, to write down again (`dictation_again`). The page "Dictations"
  lists them, plays, copies and deletes them, and changes the key, letting the held one go while the
  new one is pressed.
- **Proved** by the Python tests (the manifest and its refusals, the consent's lines, the screen
  row's powers, dictations kept, listed, failed and written down again, an id outside the folder
  refused), the app's tests (shortcuts held, taken, repeated and Caps Lock put back; the paste typed
  in the app, pasted with the clipboard put back, left copied, and leaving what was copied
  meanwhile; the screen's engine with a fake microphone), end to end in the real app with
  Chromium's fake microphone and the powers' end-to-end double (the shortcut held, two
  dictations, one pasted elsewhere and one typed in the box unsent, both on the page, the key
  changed and a dictation deleted), and on Windows with the real pieces: the shortcut heard on a
  Caps Lock pressed through the system with the capitals left as they were, the bar shown without
  taking the focus, and the words, accents included, landing in another program's field with the
  clipboard put back.

### Voice through Gemini, and settings picked from lists — decided 2026-10-28

Asked for by the user on 28/10/2026: the OpenAI credit ran out, and every voice feature stopped
with it, while the Gemini key Atlas already holds reaches models that write down, speak and talk;
and a model had to be typed by name into the plugin's settings.

- **One provider for the whole voice**, the plugin's `provider` setting: `openai`, as before, or
  `gemini`, each on the key Atlas's provider of that name reads (`OPENAI_API_KEY`,
  `GEMINI_API_KEY`). A model or voice left empty is the provider's own, a dated choice in the
  plugin (`endpoint.py`), as the catalog's defaults are.
- **Gemini's half** (`gemini.py`), on its own API rather than its OpenAI-compatible one, which has
  no transcription, speech or live sessions. Writing down and speaking are interactions
  (`POST /interactions`): `gemini-3.5-transcribe` takes the audio alone, any other model is asked
  for the words only beside it, and a recording past 14 MB (Gemini takes 20 MB a request, base64
  grows by a third) goes up through the Files API first. Speech comes as WAV; Telegram plays only
  Opus as a voice message, and Opus needs a native encoder no package brings alone, so the plugin
  uses ffmpeg when the computer has it and sends the WAV as an audio file otherwise (`audio.py`).
- **Gemini's live conversation** runs over a WebSocket from the app (Gemini's Live API takes no
  WebRTC). The Python makes a token for one session (`POST /auth_tokens`), so the key stays here,
  and the messages that open it: the persona, the voice, the conversation so far, and one function,
  `ask_atlas`, Gemini's way of handing a request over. The app's link (`GeminiLink`) reads Gemini's
  messages into the events the engine already speaks (OpenAI's), sends the microphone as 16 kHz
  PCM, plays the 24 kHz answer and drops it when the user speaks over it; Atlas's answer goes back
  as the function's response, or as a new turn when Gemini cancelled the call. Gemini ends a
  connection after some minutes, warning first: the link goes on in a new one with the handle it
  gave (`sessionResumption`), and the window slides (`contextWindowCompression`) so a long call
  does not end. An answer with nothing to say is now said, not thought, since a call waits for one.
- **A setting's choices** (SDK version 25, `choices` on a manifest setting): fixed values,
  `models` (the picker's, as `provider/model`) or `plugin`, the plugin's call `setting_choices`,
  asked when the Plugins screen shows the setting and again when another of its settings or keys
  changes. The voice asks the provider: Gemini says what each model does
  (`supportedGenerationMethods`) and lists its voices by language (the app's first, then the ones
  that speak every language); OpenAI says only names, which carry the job (`transcribe`, `tts`,
  `gpt-live`). Nothing is a list in the code, so a model the provider adds shows up by itself; a
  list is kept ten minutes. **Other…** types a value no list holds, and a list that could not be
  asked says why under it.
- **Proved** on 28/10/2026 against Gemini itself, with the user's key and a few seconds of audio:
  speech with a tone and a Brazilian voice, the WAV turned into Opus by ffmpeg, that audio written
  down inline and through the Files API, a live session opened with a token that handed a request
  over with `ask_atlas` and spoke the answer, with a classic voice and a Brazilian one, and the
  lists of models and voices of both providers. Then by the Python tests against the fake model
  server's Gemini (the requests each makes, the upload, the token and the setup, the lists and
  their refusals, the speech with and without ffmpeg) and the app's tests (the link with a fake
  WebSocket: the setup, the history, the microphone, the answer in one response, a cancelled call,
  a connection gone on in a new one, a setup refused). The live conversation spoken and heard in
  the app waits for the user's microphone.

## The executors tab and the side panel — decided 2026-10-15

Step 16f, asked for by the user on 15/10/2026: the workbench's card played a conversation squeezed
into a narrow panel, and no old session could be seen or continued. What step 16c built underneath
stays: the contract, the instances, the requests with no deadline, the two switches, the record,
`delegate`. Researched on 15/10/2026: Claude Code's `/resume` and its VS Code extension, which list
every session of a folder, and the Agent SDK's `listSessions`, which reads them; ACP's
`session/list` and `session/load`. The order, a commit each: the tab with
Claude Code, the terminal with ▶ and `!`, the preview.

**The executors tab** (part 1):

- **A third tab beside Conversations and Bots**, one card per executor on and found, as the bots'
  rows ("Agents" would be taken for bots and subagents). **A click opens it in place**, a tree
  under its row (asked for by the user on 18/10/2026, where it
  used to open a screen of its own with a way back): its account and the switch to another, "New
  session", and its folders, the most recent first; a folder opened lists its sessions. One
  executor is open at a time, its tab in the side panel. "New session" takes
  a folder typed or chosen in the system's dialog (only when the app started its server: a folder
  of this machine is no remote server's). Since 26/10/2026 it is also the tab's first button, as
  "New conversation" and "New bot" are in theirs, choosing the executor when several are on;
  "Add an account" comes under it, above the list as the user asked on 19/10/2026.
- **A folder archived leaves the list** (asked for by the user on 18/10/2026, so the list stays
  short), without touching it, kept per home and
  executor. An archived folder stays listed
  under the executor's "Archived", where it comes back.
- **The account switches from the executor**, not from a session's header (asked for by the user
  on 18/10/2026): `executor.account` logs the harness in on the user's browser, as the session's
  switch did, and each of the user's instances of it here goes on with its session on the new
  account; refused while one of them works. A session stops from the box, in send's place while it
  works.
- **A folder's sessions are read only once it opens** (17/10/2026): before, the tab read every
  session of every folder each time it showed, two 64 KB ends of each file. Version 8 of the SDK
  adds `Capabilities.folders`, `SessionFolder`, `Executor.folders` and the keyword
  `Executor.sessions(folder=)`, passed only where the plugin says it lists folders; a plugin
  before it has every session listed and their folders taken, as before. One folder's sessions are
  the Agent SDK's `list_sessions(directory=...)` (its `_find_project_dir`, a long path's hash
  matched by its first 200 characters; its worktrees left out, each a folder of its own here). The
  folders apart are **Atlas's own**, since the SDK lists sessions only and a project folder's name
  does not turn back into its path: of each project, its files by their time alone, read lite from
  the newest until one is a session the list shows, whose `cwd` names the folder. The server's
  `executor.folders` lists them, an instance's folder not kept yet first; `executor.sessions`
  takes the folder. On screen a folder starts closed, and the folder of the session on screen
  opens with it; a closed folder keeps what it read,
  and says when one of its instances waits for the user or works. Showing the executor again reads
  its folders and its open folders' sessions again, the last read shown meanwhile; an instance
  that changes does the same, and a closed folder is read again only when it opens. One read of a
  list at a time: a second ask waits for it, and a change made while it runs gets one read more
  after it. Being read, failed with why and a way to read again, and empty are said as the side
  panel's connectors say them. A plugin's failure to list is told now, as its models' and
  connectors' are, where before it listed nothing.
- **Every session of the folder, wherever it began** (decided by the user): the terminal, an
  editor, Atlas. Each plugin lists its harness's: version 3 of the SDK (`API_VERSION`) adds
  `Executor.sessions`, declared in `Capabilities.sessions` and never called on a plugin that does
  not say so, and `SessionInfo`; a bundled plugin keeps the `api` its consent was given for, since
  the fingerprint holds it and turning the user's plugin off for no new permission helps nobody.
  Claude Code's plugin reads them as the SDK's `list_sessions` does (0.2.165): the `.jsonl` files under `projects/`, read lite (size, time, the first
  and last 64 KB), a side chain or a file of metadata only left out, the folder taken from the `cwd`
  the session recorded, since a project folder's name does not turn back into its path. The title
  is what Claude Code kept (`customTitle`, then `aiTitle`, then an old `summary`) or the start of the
  first prompt: no model is asked. Codex's `thread/list` and ACP's `session/list` come with their
  plugins.
- **The middle is the session's conversation, with the trail of Atlas's own** (The turn on
  screen): built from the transcript its plugin reads back (`executor.session`) and from what its
  instance does live (`workbench.activity`, which now names the call and the tool). Nothing is
  copied into `atlas.db`. A session reads as Claude Code's VS Code extension shows it: the chain
  from the latest leaf back to the root (`parentUuid`, the SDK's `get_session_messages`), so a
  rewound reply and a side chain are not in it; before step 16f every line was read in file order.
  In each turn the harness's last words are its answer, the others its interim text; when a turn
  ends the session is read again, and the transcript replaces what the live events drew once it
  holds their last words: Claude Code writes its file a moment after it says them, so a read as
  the turn ends can come before its answer (found on 16/10/2026: the answer showed only once the
  session was opened again), and the read is tried again for up to three seconds. After a
  reconnect the session on screen is read again too: the workbench's events are not replayed.
- **A harness's own compaction is shown, as Atlas's is** (decided on 16/10/2026): the harness
  compacts its context by itself (Claude Code near its window, as in a terminal), and Atlas only
  says so. Version 6 of the SDK adds the event `Compacted` (Claude Code's `compact_boundary`, about
  how many tokens before and after) and the transcript kind `compacted`; the screen draws both as
  the status line of a compaction of Atlas's own. Read back, the session starts at its latest
  boundary, as the chain does not cross it, and the summary after it is left out: it is the model's
  text, not the user's prompt it would look like. Before, nothing said that the harness compacted.
  A `/compact` the user typed reads back with its boundary below the command, in the place of its
  bare "Compacted" output (asked for by the user on 22/10/2026): read back, the boundary roots the
  chain, so it was drawn at the top, out of sight, and only "Compacted" showed where the user looked.
- **A switch of account says what it costs** (asked for by the user on 22/10/2026): version 20 of
  the SDK adds `Transcript.context`, the tokens of context a session holds (Claude Code's: the
  usage of its last reply, or a compaction's tokens after), and `workbench.account_switch` answers
  it (`tokens`). The note before the switch and a status line in the session after it say that the
  first message on the other account loads the whole history, about that many tokens at once. The
  line is the screen's own, kept with its place for the card, so a read of the session again
  shows it where it was said.
- **Continuing a session starts an instance on it** (`workbench.start` with `session`): `--resume`
  for Claude Code. One process per session in Atlas: a start on a session an instance already
  works on answers that instance. A session its harness no longer keeps opens to read only.
- **The card comes apart into the usual places:** its prompt is the box, which steers while the
  harness works; what it asks shows inside the conversation, as Atlas's questions and approvals
  do; switching account and stopping are at the top. Duplicating and moving became "+ New
  session" in another folder.
- **One box for every conversation** (asked for by the user on 16/10/2026): Atlas's, a bot's and an executor's session
  use the same `MessageBox`, the screen's controls above it (model, effort, terminal) and "Auto"
  beside send. An executor's automatic mode is that "Auto" (kept for the next instance when none
  runs). "Atlas answers" left the screen with it, at the user's word; the server still takes
  `workbench.atlas_answers`, off by default. The executor's box takes files since step 16i
  (Attachments, "An executor's files").
- **Claude Code's automatic mode, the default** (asked for by the user on 17/10/2026; the default
  since 20/10/2026, at the user's word): every delegation, its resume, and every session the user
  starts runs in Claude Code's `auto` (its own classifier reviews each action; never
  `bypassPermissions`, which checks nothing), with no question first. `delegate`'s
  `approval='manual'` and the session's "Auto" turned off start it in its `default` mode, which
  asks; "Auto" turned on again comes on after its warning is confirmed. An executor with no
  automatic mode Atlas may turn on starts in its own default, asked for `auto` or not. **Atlas adds no rule of its own to it** (decided with the user
  on 17/10/2026): it runs on its own classifier,
  its own rules and the user's settings, as Anthropic ships it, so by default it commits and
  pushes to the project's own repository (code.claude.com/docs, auto mode, 17/10/2026). A user who wants
  a push to ask adds an `ask` rule to their Claude Code settings, which explicit `ask` rules
  prompt for in `auto` too, and which it reads with `--setting-sources`. Since 21/10/2026 the task
  is no longer told to stay in its folder (asked for by the user). What it still asks goes to the user, never
  the guardian, "Atlas answers" or not: an explicit ask rule is the user's word that they want to
  be asked. No other executor's automatic mode is offered.
- **The model and the effort** (asked for by the user on 16/10/2026) sit below the box, as
  Atlas's conversation has its own: the models the
  harness's own picker lists for the account and the efforts of each (its executor models), the
  effort gone for a model that takes none. Version 4 of the SDK adds `Capabilities.models`,
  `ModelChoice`, `Executor.models`, `ExecutorSpec.model` and `effort`, and
  `ExecutorInstance.set_model`; the bundled plugin keeps its `api`, as for version 3. A pick
  applies from the instance's next request, or to the instance the next prompt starts, and goes
  on with it to another account or folder. Claude Code's are what its `initialize` answers
  (`models`, `supportedEffortLevels`), before any instance from one started in the home folder
  and closed at once, with no prompt; `--model` and `--effort` start it on them, and mid-session
  `set_model` (the SDK's) and `apply_flag_settings` with `effortLevel` (what its own `/effort`
  sends) switch them, both proved without a prompt on 2.1.296. It takes an effort it does not know
  and quietly drops it, so the plugin refuses one the model's list lacks. Beside the pick, what
  the last turn ran as when that differs (a model in the user's own settings). **Unset, the
  effort is Atlas's medium** (above, A conversation's model), for a session the user starts, a
  delegation and its resume alike, over the harness's own settings; the harness's default model
  goes by the name of the model it runs as (Claude Code's "Default (recommended)", which resolves
  to `claude-opus-5-5`, shows as "Opus 5.5 · default", its own "Opus 5.5" not listed twice).
- **Slash commands** are those the harness says its channel takes: Claude Code answers them to
  `initialize` (`commands`) and says them again in `init` (`slash_commands`), now `Started.commands`
  and the instance's `commands`; the box offers them as `/` is typed. ACP's
  `available_commands_update` comes with its plugin.
- **The slash commands, described and before a session runs** (asked for by the user on
  18/10/2026: the `/` offered nothing until an instance had run, only names, and a command's
  output was lost on reading the session back). Version 10 of the SDK adds `SlashCommand` (name,
  description, hint, `builtin`: Claude Code's `SlashCommand`, ACP's `AvailableCommand`), the event
  `CommandsChanged`, `Capabilities.commands` and `Executor.commands(folder=)`. Claude Code's plugin
  reads them described from its `initialize` (2.1.296 answers 101 for the user, its own marked
  `builtin`, a name shared by two rows run as the marked one), says them again on its
  `commands_changed` (a skill found in a folder it worked in), and, before a session runs there,
  asks a Claude Code started in the folder and closed at once, with no prompt, so a project's
  skills are in the list. The server's `executor.commands` answers what the instance there said,
  or that read; the card's names changing says to read them again. **They run as they do in a
  terminal**: proved on 2.1.296 with no model call, a command Claude Code runs itself (`/context`,
  `/usage`, `/mcp`) answers on the channel with a synthetic message and a result, or
  `local_command_output`, now its words; a skill's command is a prompt, and runs as one. **Read
  back as typed**: Claude Code keeps `/name args` as a user line of tags (`<command-name>`,
  `<command-args>`) and a command's output on a `system` line of subtype `local_command` below it,
  which the SDK's chain stops before; the plugin reads the command as typed and its output as the
  harness's words, the chain going on through those lines, where the tags used to show and the
  output of a session's last command was lost.
- **One `/` list for every box** (`use-slash-completions.ts`):
  each row the command, its hint and its description, in two groups, the commands first, then the
  skills (Claude Code's own are the commands; a skill or a command a user, project or plugin wrote
  is a skill); matched in tiers (the name, its start, inside it, a word of its
  description; after a plugin's `:` too), at most 30 of each kind; the arrows
  move, Enter or Tab take the row, Escape hides the list until the text changes.
- **The state is on the list**: working, waiting for you. The tab carries a badge
  while something waits, and the system's notification still comes; its click opens the session.
- **Atlas's delegation** is a session marked "by Atlas" in the list (the delegation record knows
  which), and in Atlas's conversation the `delegate` line opens it (`subagent.session`). This
  replaces the workbench's cards.
- **The same session open in two places** (studied in the step and decided by the user on
  15/10/2026). Two programs writing one session fork it into two branches, and Claude Code later
  follows one and forgets the other (its issue #83719, reproduced with two VS Code windows; #80427
  compacted a branch 15 hours old). Claude Code keeps a registry of its running processes,
  `~/.claude/sessions/<pid>.json`, which its VS Code extension reads to warn; its terminal
  `--resume` warns of nothing. The file's time
  would not do: a session left open in VS Code does not touch its file. So the plugin reads the
  registry (the process alive and the same one, its `procStart` being its
  creation time on Windows; Atlas's own processes left out), `SessionInfo.elsewhere` names the
  program, and the session opens with a warning and three ways out: read
  only, continue in a copy (`ExecutorSpec.fork`, Claude Code's
  `--fork-session`), or continue here anyway. The registry is not documented; read wrong, it only warns
  less.

**The side panel** (parts 2 and 3, decided by the user): the panel on
the right keeps its place with another use, the terminal and the previews in tabs.

- **The terminal** (xterm.js; in step 16f `node-pty` in the app's main process,
  since step 15 the server's own pseudo-terminal, `atlas.terminals`: Several machines, below): its
  choice of shell (PowerShell 7, then Windows PowerShell 5.1, then cmd on Windows; `$SHELL`, then
  zsh, bash, sh elsewhere; `ATLAS_TERMINAL_SHELL` names one), the environment it cleans, the output
  held until the view attaches, a tab per terminal, Ctrl+` to show it. It opens in the folder of
  the session or the conversation on screen (`conversation.open` says the conversation's folder),
  on the server's machine, wherever the app is.
- **The ▶ on a shell block of an answer** (`powershell`, `bash`, `sh`, `cmd`, `zsh`) is Run: it
  types the command into the conversation's terminal and presses Enter; off while
  the answer still comes; a block of several lines runs each as a command. **The `!` in the box**
  runs a command: `!npm test` runs there with no model call first.
- **What both do:** the selected terminal tab when it is free, else a new one
  (a server left running in a tab stays up); the command
  waits for the terminal to be ready, never lost; the output enters the conversation as a terminal
  row, mid-turn too, cleaned as
  `run_command`'s; the agent answers it on its own, one model call, and in an executor's session
  the output goes to the harness; the conversation is not
  left "running". The row travels in Claude Code's own
  bash-mode tags (`<bash-input>`, `<bash-stdout>`) with the exit code, a user
  message the model reads and the screen reads back (`conversation.terminal`; `message.start` and
  `message.steered` say `kind: terminal`; the stored kind is `terminal`), so no new event was
  needed; the redaction is the vault's, the cut past 50,000 characters the tools'.
- **How a command's end is known** (studied in the step, 15/10/2026): OSC
  133 marks with a random nonce are injected into PowerShell (wrapping `prompt` and
  `PSConsoleHostReadLine`; the
  exit is 0 when `$?`, 1 when a new `$Error` came, else `$LASTEXITCODE`) and bash; ready is the first
  prompt mark; the output is what lies between the start and end marks. A command that does
  not end (`npm run dev`) gets 30 seconds: its output so far enters as "still
  running" and the agent answers once. **When it ends later, the agent is told and answers again**
  (decided by the user on 15/10/2026, as the model is told a background command ended). A
  command must start within 2 seconds of being typed, else it is interrupted and said so;
  the output keeps its last 102,400 characters, its escapes taken
  out, and a line Windows' pseudo-terminal drew by moving the cursor read back as a line break
  (found by the terminal's own test on 15/10/2026: cmd's output and its next prompt came as one
  line). A shell with no marks (cmd) ends a command when it goes quiet a second with a prompt at
  the end, with no exit code.
- **A dangerous ▶ asks first** (Atlas's own, decided by the user): the app asks the server, which
  uses the trust gate's detection; the whole command shows in a confirmation; refused, it does not
  run. The `!` never asks: the user wrote the command.
- **The preview** shows a file, a local address or an artifact (HTML, mermaid, Markdown), a tab
  each (`PreviewTarget`), each tab the conversation's that opened it.
  HTML runs in an isolated frame, `sandbox="allow-scripts"` with no `allow-same-origin`, from an
  allow list (which refuses an upper-case `ALLOW-SAME-ORIGIN`): the page may have come from a site
  the agent read. **It never opens by itself**: "Open beside" on a block;
  a file written or a site up becomes a small link above the box (a stack, 4 a
  conversation); only the agent's `desktop_preview` opens one, behind the tool search as
  `browser_exec`, and never takes the focus from a conversation in the background.
  - **How the page is isolated** (found by the e2e test on 15/10/2026): a `srcdoc` frame inherits
    the window's policy, which forbids inline scripts, so the page's script never ran. The
    window's policy stays: the page is served from a protocol of its
    own (`atlas-preview:`, Electron's main process) with a policy of its own, its scripts running
    and every connection refused, in the frame sandboxed with no `allow-same-origin`; the window's
    policy gains only `frame-src` for that protocol and for a local site. A file is read by the main
    process, only with the server on this machine; an SVG shows as an image, so its scripts never
    run. Mermaid draws in the panel through Streamdown, in mermaid's strict mode, loaded when a
    diagram shows.
  - **`desktop_preview`**, `open` and `close`, for the user's
    own conversations; `preview.open` and `preview.close` reach the app. A `read` action is left
    out: read_file, web_fetch and the browser read. A URL's normalizing alone would take a
    relative `page.html` for a bare domain (`https://page.html`); a name that is a file of
    the folder is the file.
- **The executor's tab** (asked for by the user on 16/10/2026): choosing an executor in the
  sidebar gives the panel a tab with what it has, now its connectors, as its own `/mcp` lists
  them: each MCP server with its state (connected with how many tools, needs login, failed with
  why, starting, off), under where it comes from (the user's, a project's, a plugin's, the
  organization's, the account's hosted connectors). **Only shown** (the user's choice): a
  connector's login is the harness's own, and the tab says to do it there. Version 5 of the SDK
  adds `Capabilities.connectors`, `Connector` and `Executor.connectors`, read with
  `executor.connectors`; the bundled plugin keeps its `api`, as for versions 3 and 4. Claude Code
  answers the control request `mcp_status` (the SDK's `mcpServerStatus()`) without a prompt, asked
  of one started in the home folder and closed at once, so a folder's `.mcp.json` is not in it, and
  the tab says so. The account's claude.ai connectors come a moment after the rest (found on
  2.1.296, 16/10/2026: none in the first answer, all within a second, some `pending` until 2.5
  seconds), so the plugin asks again every half second until none is pending and, for a login on
  a claude.ai plan (`initialize`'s `account.subscriptionType`), the account's have come, for up to
  10 seconds; what is still pending then is given as pending. The tab leaves with the executor:
  back to the list of executors, or to another sidebar tab.
- **The plan's usage** (asked for by the user on 18/10/2026) heads the executor's tab, above its
  connectors: a bar per window of the plan the harness's account is on, how much is used and when
  it starts again, as Claude Code's own `/usage` shows them (its five-hour session, its week, and
  its weeks of one model), always shown; amber from 80% used, red from 95%. Version 9 of the SDK
  adds `Capabilities.usage`, `UsageWindow`, `PlanUsage`, `Executor.usage` and the event
  `UsageChanged`; the bundled plugin keeps its `api`, as for versions 3 to 8. Version 19
  (21/10/2026) adds `PlanUsage.extra_usage`: past its limits the plan goes on and bills (Claude's
  extra usage, `get_usage`'s `extra_usage.is_enabled`; Codex's credits, `rateLimits.credits`)
  rather than stopping, which the panel says in amber, since such an account never stops for the
  next account or a route. The shape is
  one for every provider: a
  window says how long it lasts, so the screen names it in its own language, and Codex's primary
  and secondary windows fit it. **Read the harness's own way, never with its login:** reading
  the plan's usage with the user's OAuth token, straight from Anthropic's API (`api/oauth/usage`),
  is what Atlas's rule for Claude Code's login forbids (Outside executors, above). Claude Code
  answers the control request `get_usage` (its structured `/usage`) without a prompt, asked with
  `skip_behaviors` of one started in the home folder and closed at once, as its connectors are;
  and after each request of a turn it writes a `rate_limit_event` that names the windows
  (`unifiedWindows`, 2.1.296), which Atlas used to drop unless the limit was reached. Its SDK
  names `get_usage` `usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET`, so the plugin
  alone knows its shape, a Claude Code that refuses it says the usage shows once a turn runs, and
  the stream's event still feeds the bars. The workbench keeps each executor's last usage and
  merges what an instance tells (a window it names replaces the one with its id; the others stay
  as read), forgets it when the account switches, and tells every screen (`executor.usage`, a
  method and an event). The screen reads it when the executor shows and the last read is older
  than two minutes, on its ↻, and after an account switch;
  no timer polls it. Proved by the plugin's tests (the answer of 2.1.296, its code-named
  windows left out, an API key's login with none, a refusal, the event in both shapes), the
  workbench's (read, merged, forgotten on another account), the gateway's and Vitest on the bars.
- **Both sides of the window hide** (asked for by the user on 16/10/2026): a button on each hides
  it to a narrow strip whose button brings it back; Ctrl+B does it
  for the sidebar, Ctrl+` still for the terminal. The sidebar's strip still says
  when something waits. **The panel is the user's to open or hide** (asked for by the user on
  17/10/2026): choosing an executor gives it the executor's tab without opening it, leaving the
  executor does not close it, and open or hidden it stays so from screen to screen; hidden, its
  strip is on every screen where the panel has something to offer (the terminal, a preview, an
  executor's tab). A command run and a preview opened still open it, as they did.
- **Both sides open at the same width and drag** (asked for by the user on 18/10/2026, so the two
  rails match): 288px each, wide enough that a terminal keeps its columns. The edge toward the
  conversation drags each between 224 and 640px, the arrows move it, a double click puts it back;
  the conversation keeps its 24rem, and a narrower window narrows both rails before it (below 960px
  the panel lies over it, as before). A width dragged is kept in the window's storage, as the theme
  is (`store/layout.ts`).
- **An executor's session opens its folder in VS Code** (asked for by the user on 18/10/2026), a
  button in its header: by VS Code's own link, `vscode://file/<folder>`, which its installer
  registers, so no shell runs and `code` need not be on the PATH (`electron/editor.ts`). Only with
  the server on this machine, whose folders are this machine's.

**Proved** (part 1) by `tests/plugins/test_claude_code.py` on Claude Code's own format
(`tests/plugins/data/claude-session.jsonl`, the keys of each kind of line as Claude Code 2.1.140
writes them, neutral words): the listing, the chain, a session begun in a terminal listed and
continued with `--resume` in the fake `claude`, which now keeps its sessions as Claude Code does;
`tests/assistant/test_workbench.py` (the list crossed with the instances, one process per session,
the copy, the delegated mark, the registry); the gateway's tests; Vitest on the session's trail;
and `e2e/executors.spec.ts`. No test reaches the user's own sessions: the tests and the e2e point
`CLAUDE_CONFIG_DIR` at a temporary folder. The folders read apart (17/10/2026) are proved by the
plugin's tests (one file read a folder, a side chain passed over, a folder's sessions alone, a long
path under Claude Code's own hash), the workbench's (an instance's folder first, a plugin before
version 8, a plugin that fails), the gateway's, Vitest on the list (no session read before a
folder opens, one read however often asked, kept while closed, the failure and the read again, the
mark of a closed folder) and on the store (a change while a read runs reads once more), and the
e2e opening the folder before its sessions show.

**Proved** (part 2) by the terminal's own tests on the real shell and `node-pty`
(`electron/terminal.test.ts`: a command clicked before the prompt, a program's exit code, one that
does not end given as running and its end told later, a busy terminal refused, cmd with no marks),
the marks' reader (`electron/shell-marks.test.ts`), the gateway's tests (the terminal row, its
redaction and cut, the title by the command, `trust.check`), Vitest on the row read back, and
`e2e/terminal.spec.ts`: the ▶ off while the answer comes and absent on Python, a ▶ before the
terminal is ready, the row and Atlas's answer with the conversation not left running, a dangerous
command asked and refused, the `!` with no question, and a command still running whose later end
Atlas answers.

**Proved** (part 3) by the tool's tests (`tests/tools/test_preview.py`) and the gateway's
(`preview.open` and `preview.close` through the tool search), Vitest on the sandbox's allow list
(an upper-case `ALLOW-SAME-ORIGIN` dropped), the links and the tabs of each conversation, the
main process's file reading and page policy, and `e2e/preview.spec.ts`: an HTML block opens beside
only on its click, and its script runs and reaches neither the window, nor the bridge, nor storage;
the agent's page opens beside its conversation; a file it wrote waits as a link and opens on it;
another conversation shows none of its tabs; a mermaid block opens as its drawing.

## Several accounts at once — decided 2026-10-18, redone 2026-10-19

Step 16h, asked for by the user in three parts on 18/10: the same executor running on two accounts
at once, each project on its account, and a session that switches accounts and goes on in its
project. On 19/10 the user added a fourth, which changed the design: **each account keeps its own
settings and its own history, untouched**. Their case: accounts of their own and of different
companies, one for personal projects, one for project A, one for project B; each stays in its own
world, and now and then, finishing a personal project with its plan spent, they move that
conversation to project B's account and go on. The switch is the exception.

- **The design of 18/10 is gone** (`153d3e8`: one `~/.claude` for every account, an extra account a
  `claude setup-token` token kept in the vault). With one folder, a company's account read the
  user's personal `CLAUDE.md`, MCP servers and plugins, and two companies' conversations shared one
  list; and keeping a plan's token in Atlas sits close to Anthropic's "developers may not collect,
  store, or intermediate Claude.ai credentials or session tokens" (Claude Code's Legal and
  compliance page). Its proof still stands for what it showed: a conversation goes on under another
  account and remembers what it was told.
- **A folder per account** (Claude Code's documented way, "Log in with multiple accounts"; Codex's
  `CODEX_HOME` is the same). The main account is the user's own folder, `~/.claude` or their
  `CLAUDE_CONFIG_DIR`, which Atlas never changes; every other account is a folder beside it,
  `~/.claude-<nickname>`, with its own settings, plugins, MCP servers, `CLAUDE.md` and sessions.
  They live outside `ATLAS_HOME` because they are Claude Code's: they outlive Atlas, serve every
  home, and the user can run one from a terminal. Atlas hands the harness one absolute, normalized
  path per account (Claude Code's #92252: a trailing slash or an unexpanded `~` picks another,
  empty, store).
- **The login is always the harness's own.** "Add an account" takes a nickname, makes the empty
  folder (or takes a `~/.claude-*` that exists) and runs `claude auth login --claudeai` with its
  `CLAUDE_CONFIG_DIR`, which opens Anthropic's page on the user's browser. Atlas never sees, keeps
  nor passes a credential, which is the case Anthropic allows ("an end user ... signing in to the
  unmodified Claude Code binary with their own Claude subscription"). With a whole login each
  account has claude.ai's connectors, Remote Control and `get_usage`.
- **On screen, each account is an executor of its own** ("duplicate the executor", 18/10):
  "Claude Code · personal", "Claude Code · company B", each with the folders and sessions read from
  its folder, its plan usage, models, connectors and commands. The `folder account` stays, and the
  rule for which account an instance runs on with it: the one asked for, else the folder's, else
  the main one; a delegation from Atlas follows it.
- **Switching inside a conversation opens a copy on the other account** (the user's choice on
  19/10, as the community does it). Atlas ends the idle instance and starts the other account on
  `--resume <absolute path of the transcript> --fork-session`: Claude Code itself reads the first
  account's file and writes a new session, with a new id, into the second account's folder; both
  flags are documented, and it is Claude Code's own `/branch`, which leaves the original as it was.
  Going back does the same the other way. Atlas's card stays one, showing the conversation
  through; each turn keeps the session it ran on, which is its account's. If the proof shows the fork
  written anywhere else, Atlas copies the whole file into the other account's project folder and
  resumes it there, never reading nor changing its contents.
- **Before a switch the app says what changes:** the account that gets the conversation, the tools
  the other account lacks (its connectors and MCP servers against this one's), and that the first
  message reads the whole conversation again on the other plan (the prompt cache is per account).
- **The copy stays on the other account,** and the user decides per session. **Archive** takes a
  session out of the list and touches nothing, as an archived folder does; it comes back under "Archived". **Delete** asks first and removes,
  on that account only, the files of that session that Claude Code's `claude purge` removes for a
  whole project: the transcript (`projects/<project>/<id>.jsonl` and its `<id>/` folder), its
  `file-history/`, `tasks/` and `debug/` entries; never while it runs. A card on it closes; the
  other accounts do not change. **New:** Claude Code deletes only whole projects (`claude purge`);
  deleting one Claude Code session is ours, with `purge`'s list.
- **The plugin SDK grows to version 12** and drops 18/10's credential (`Capabilities.credentials`,
  `ExecutorSpec.credential`, `credential_recipe`, the one break of its promise to only grow: it
  lasted a day and only the plugin Atlas ships used it). It gains `Capabilities.account_folders`:
  `Executor.account_folder()` names its folder, `new_account_folder(nickname)` where another goes,
  and `for_account(folder)` makes the same harness on that folder, handed to each of its processes
  in the harness's own variable (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`); `ExecutorSpec.resume_from`
  names the other account's folder a session is copied from; and `Capabilities.delete_sessions`
  with `Executor.delete_session`. **In the core, an account's executor is one executor more**,
  named `<executor>@<account id>` (`claude-code@1a2b3c4d`): the assistant makes it from the plugin's
  on the account's folder, so the workbench, the executors tab, the plan's usage, the delegation
  record and the screens need nothing new to keep each account apart. The core keeps the accounts (nickname, folder) in `jobs_executor_accounts` and each
  folder's account in `jobs_folder_accounts`; the vault keeps nothing of an account. An extra
  account of 18/10 goes in the migration, and the app asks for it to be added again.
- **The next account, at a plan's limit** (asked for by the user on 21/10/2026, in place of
  "only when the user asks"): a session its account's limit stops goes on by itself in a copy on
  the next account logged in, as a switch does, in the order the user set under the main
  account's row (the main one first until then), and compacts there before it goes on unless the
  user turned that off (on by default since 25/10/2026); its conversation says which account took
  over or why none could. Since 22/10/2026
  that order is no longer on screen (asked for by the user: it crowded the sidebar, and each
  session's route does it per session); a session with no route keeps the one saved. Turned off
  (`executor.failover`, kept in `jobs_account_failover`), a delegation stops
  at the limit as before, `delegate(action='resume')` going on later. Using several accounts to get past restrictions is what Anthropic's terms
  warn about, so the user is the one who adds each account.

**Risks, each with the step of the proof that catches it:** the other account lacks a tool the
conversation used (the warning; a switch to an account without one of the first's MCP servers);
Claude Code still reads the main `~/.claude`'s `CLAUDE.md` and rules under another folder (#80580)
and may install a `/plugin` there (#74393) (looked at on the current version, what still leaks
written down); rewinding edits made before the switch does not work on the other account, since
`file-history/` and tasks stay behind (what it can rewind, recorded); the first message after a
switch is dear (the plan's usage before and after); the transcript format is Claude Code's own
(Atlas passes a path or copies a whole file, never parses it to switch); a company's account gets
a personal project's content (the warning names the account); two processes refreshing one login
at once may lose it (#103325; two accounts at once, neither logged out).

**Proved** by the plugin's tests (an account's folder reaches its process as `CLAUDE_CONFIG_DIR`,
an inherited token never does), the workbench's with the fake `claude` keeping each account in
its folder (two accounts at once and a folder's account; the switch going on in a copy kept on the
other account, the first account's file the same byte for byte, and the connectors the other
lacks; a nickname refused or taken, an account removed with its folder left; a session deleted on
its account alone, its idle card closed first), the delegation's (a project on another account with
the main one logged out), the gateway's over the socket (the account's row, its login, the start,
the switch back, the delete refused for a session it no longer keeps), Vitest (each account a row,
the nickname form, the removal asked first, a session archived and back, a delete asked first), and
an e2e scenario with the real app: an account added by its nickname and logged in in its folder,
and a session switched to it after the warning, its copy there and the original untouched. The
tests' own home is a temporary folder, so none makes an account beside the user's.

**To prove** with the user's accounts, a few prompts on each, asked first: two accounts logged in
at once, each with its usage; a folder starting on its account; the switch in a conversation
remembering a word, the first account's file the same byte for byte and a new session on the
other (that `--fork-session` writes the copy in the other account's folder is what the fake
assumes, and the real one must show); the way back; the missing tool's warning; the leaks above;
archive and delete on the other account, the first untouched.

### A session's route across executors — decided 2026-10-21

Asked for by the user on 21/10/2026, beside the `account failover` of the same day, which stays
as it is: a session of its own may go on, at a plan's limit, to destinations the user orders,
accounts of its executor and of others alike (Claude Code · main → Claude Code · company B →
Codex · main). The route is the session's, not the executor's.

- **Kept for the session's card** (`jobs_session_routes`, migration 10, by the conversation
  Atlas keeps of the card): every instance that takes the card's place follows it, and a session
  opened again from the list takes the route its last card had (`InstanceStore.conversation_of`).
  Each `route target` is an executor and one of its accounts together, checked when it is set.
  With a route on, the executor's own order is not used; with none, or with it off, it holds.
- **The next destination** comes after the one the session runs on, round to the start: one not
  installed, not logged in, or at its own limit (reached here, or as its usage says) is passed
  over, as an account's hand-over does. With none left, the session's conversation says so, and
  the session waits where it stopped.
- **Within one executor**, a `session copy` on the other account, compacted first unless the
  route says not to (on by default, decided with the user on 25/10/2026: at the switch, never
  before the limit), then told to continue: what an account switch does. It compacts only where
  the harness has `/compact` among its commands (Claude Code's; Codex's and DeepSeek's take no
  slash command, and the Gemini CLI's is another), and a compaction that fails is said (a `note`
  activity) and the copy goes on as it was, never left stopped.
- **The model and the effort are kept at every switch** (decided with the user on 25/10/2026): a
  stop may name the model and the effort the session goes on with there (`RouteTarget.model` and
  `effort`, kept in the stop's JSON, never part of what tells two stops apart); with none, the
  session keeps the model the user chose, or, on the harness's default, the one it ran as, since
  another account's default may be another model, and the effort it ran on, onto the levels the
  model takes there (`clamp_effort`). Across executors the effort goes on the same way and the
  model is the destination's default unless the stop names one. A model the destination does not
  offer gives way to its default, and the session says so (`note`). A delegated task's switch
  does the same.
- **Across executors, a new session.** A Claude Code session cannot be resumed as a Codex one, nor
  the other way round, so the destination starts a session of its own in the same folder, on the
  same card, its model the stop's or its own default, its effort kept onto its levels, its
  automatic mode and "Atlas answers" kept. Its first
  prompt is the `handoff packet` (`assistant/handoff.py`): the normal call that goes on with the
  work, with no model call before it to summarize (Principle 1: the destination's model reads and
  does the work). The packet is put together deterministically, from the source harness's own
  steps of its session (`Executor.transcript`, the same kinds for every harness), or, when it
  cannot read them, from Atlas's own record of the card; with the files its turns changed (the
  delegation record) and the user attached, and the reason. Within 40,000 characters (about
  10,000 tokens) the newest steps are kept first, each step cut to its start and end, and what was
  left out is said, so the destination looks at the folder itself. A packet that hands on a
  packet keeps the first task, never the packet pasted in again.
- **On screen** (`workbench.route`, `workbench.set_route`): one pill below the session's box for
  its account and its route (one pill since 22/10/2026, the route's first stop being the account):
  the account it runs on, once its executor has another, and how many destinations the route has.
  It opens the account's choice and the route drawn as a path (25/10/2026): its stops in order,
  joined, the one it runs on marked, each saying whether it can take over (installed, logged in,
  not at its own limit, which the server tells as `limited`) and how much of its plan is used, as
  last read, with the model and the effort it goes on with there ("as now" unless chosen; on
  another harness, "its default"); a stop is added, moved and taken out there, and two switches say
  whether the route is followed and whether a copy compacts first. Each change is saved at once.
  The session's conversation says where it went on (`route` and `account` activities) and what
  changed on the way (`note`).
- **Not yet:** a session not started yet has no card, so its route is set from its first prompt on; the session's screen,
  after it crosses executors, reads the new session's own steps, which begin from the packet
  (the card's conversation in Atlas keeps both); a compaction of the packet by a model, should a
  harness's window ever be too small for it, waits until use shows it is needed.

**The real proof** is `scripts/prove_route.py`, rehearsed on the fakes (`tests/test_prove_route.py`,
which `FAKE_CLAUDE_LIMITED` and `FAKE_CODEX_LIMITED` stand an account at its limit for). It drives
no account to its limit on purpose: it waits for one of the user's to be there, so the turn sent
to it stops at once; it reads every account first with no prompt (who, usage, extra usage, at its
limit), shows where a route would go from that usage alone, and prompts only with `--yes`,
refusing an account that bills past its limits or does not say so. Its steps: a route inside
Claude Code with step 16h's switch (the first account's file the same byte for byte), the
account's own hand-over, and a route to Codex, each a word kept on one account and written after
the session went on elsewhere. Its rehearsal found that `Workbench.limited` could not read Codex's
reset (ISO 8601 with no fraction of a second), which broke the hand-over just as an account was
full; it reads any ISO 8601 now, and one it cannot read counts as still at its limit. Run on
21/10/2026 with the user's accounts, steps 1 to 3 passed (the main account at its weekly limit,
the turn sent there stopping at once, the session going on in a copy on an account with room by
the route and by the account's own order, the word kept, the first file the same byte for byte);
step 4 waits for Codex's 30-day limit, full that day.

**Proved** by the workbench's tests with the fake `claude` and the fake `codex` over real pipes (a
route inside one executor going to the account it names rather than the executor's order, in a
copy; a route across executors starting a new Codex thread whose first prompt is the packet, with
no resume nor fork and Claude Code not prompted again; an account not logged in passed over, Codex
at its own limit, and nothing left said; the route checked, kept for the session opened again,
and off leaving the executor's order), the packet's own (the same handoff the same text, its
sections, what is pending, the size kept, a packet handed on), the gateway's over the socket, and
Vitest (the pill, the editor adding Codex, ordering and turning it off; the conversation's lines).

### A delegation's account and route — decided 2026-10-21

Asked for by the user on 21/10/2026, after asking whether Atlas could delegate to any account of
any harness, switch accounts and make a route of them: until then a delegation ran on its folder's
account or the main one, followed only its executor's order at a limit, and its `account` action
logged the harness in again. Atlas now picks, as the user does on the workbench.

- **The account is in the executor's name.** `delegate`'s `executor` takes a `target name`: the
  executor alone runs on the folder's account, else the main one, as before; `claude-code@work`
  on the account the user nicknamed `work` (or its id), `claude-code@main` on the main one. The
  tool's list names every account of an executor that has more than one, about 5 tokens each; one
  added after the tool was made is still taken, and checked when the call runs.
- **A task's route** is `delegate`'s `route`: target names in order, an executor alone being its
  main account. It is kept as a card's is (`jobs_session_routes`, by the task's own stored
  conversation), so a resumed task follows it too; with none, the executor's own order of its
  accounts holds, as before. At a plan's limit the next destination goes on: within one executor
  in a `session copy`; in another executor a session of its own whose first prompt is the
  `handoff packet`, the workbench's own (`Workbench.handoff`), with "by the task's route" as its
  reason. The task stays one, its record taking the executor, the account and the harness's name
  it ends on; the conversation hears a `route` or an `account` activity, as a card's does.
- **Resuming elsewhere is the switch.** `action='resume'` with `executor` naming another account
  of the task's executor goes on in a copy there; naming another executor, in a new session from
  the packet, the task's own words after it when there are any. Its own executor named alone
  stays on its account; another, on the folder's.
- **`executor_accounts`**, deferred and read-only, shows what Atlas needs to pick: each account by
  its target name, its login, its plan usage per window, whether it is at its limit or bills past
  it, the folders that start on it, and the accounts' order. It reads the harnesses, never a model.
- **The login stays the user's call**: `action='account'` logs in the account `executor` names, by
  the harness's own login, only when the user asks; with several executors that switch accounts it
  asks which. Using several accounts to get past limits is what Anthropic's terms warn about, so
  Atlas only moves among the accounts the user added and ordered, as their hand-over does.
- **The cost:** with an executor installed, `delegate` costs 329 tokens more than without in
  Atlas's estimate (258 before, 21/10/2026).

**Proved** by the tool's tests (target names and the route checked, an account added later passed
on, the accounts tool deferred) and by the delegation's with the fake `claude` and the fake `codex`
over real pipes: a task on the account Atlas names over its folder's, an unknown account refused
with the accounts it has; a route crossing to Codex at Claude Code's limit, whose first and only
prompt is the packet, Claude Code not prompted again, the task completed as Codex's; a task
stopped at its limit resumed on another account in a copy and then on Codex from the packet, one
stored conversation through; and the accounts tool's logins, usage, limit and folders, with a login
refused for an email another account has. **To prove** with the user's accounts, beside step 4 of
`scripts/prove_route.py`: a real delegation on a named account, and one crossing to Codex.

## Several machines — decided 2026-10-19

Step 15. The user's cases: Atlas on a shop's PC run from home; this PC's Atlas opened from another
PC, with the same conversations; Atlas on a home lab or a company VPS run from the app. All three
are one shape: **the app on one machine opens the Atlas of another, and the Atlas there does the
work**, with that machine's files, keys, trust and approvals. Studied on 19/10/2026. The agent's
server stays on its own machine and is reached through an SSH tunnel, Tailscale or a relay;
whatever listens off the machine demands a credential, and TLS comes from a proxy or the tunnel,
never the server itself.

- **One agent acting on another machine is left out** (the user's decision): crossing a server's
  boundary changes filesystem, credentials, tools and trust.
- **Transports** (`atlas.instances`): `tailscale` for all three cases, `ssh` for a server, and
  `relay` later, with Atlas Cloud (step 20) and the Android app.
  - **`tailscale`**: an open home's server listens on the machine's tailnet address too. The
    address and the machine's MagicDNS name come from
    the `tailscale` command (`tailscale ip`, `tailscale status --json`); WireGuard encrypts, so no
    certificate is involved. A server whose tailnet is not up yet (Windows starts Tailscale after the
    logon too) keeps listening on loopback and tries the tailnet address every 30 seconds.
  - **`ssh`**: the server stays on loopback, and the app opens `ssh -N -L 127.0.0.1:<free
    port>:127.0.0.1:<port> <host>` with the system's client, so the user's `~/.ssh/config`, agent
    and jump hosts apply. With these guards: `BatchMode=yes` (an ssh never waits on a prompt),
    `StrictHostKeyChecking=accept-new`
    with a changed host key failing closed on OpenSSH's own words, `ExitOnForwardFailure=yes`,
    `ServerAliveInterval` (an idle tunnel would drop), a host refused when it starts
    with a dash or holds whitespace, a colon or a control character, the tunnel restarted 5 times
    a second apart before the connection is called dead (a blip must not tear down a healthy
    server), and on Windows the system's OpenSSH, then Git's, then the PATH's.
  - **The app never installs nor starts Atlas over SSH**: that path is mostly fixes (remote
    orphans, fish and zsh login shells, shared `ControlMaster`s, the Windows client). On a server
    Atlas comes by the installer (step 18), and `atlas always-on on` runs there.
- **Always on, per home** (`atlas always-on on|off|status`, and the switch in Settings): the
  home's server runs as the user's service, outliving the app and starting with the computer; the
  app attaches to it instead of starting one, so the local and the
  remote windows see the same conversations and routines run with the app closed. Off by default.
  Per home because each home has its own server (Backup and homes). What it writes per system:
  - Windows: a Scheduled Task at the user's logon, `/RL LIMITED` (no elevation), restarted on
    failure, written and checked as XML, never by reading `schtasks`'s localized text.
    `schtasks /End` stops the whole tree, the interpreter a virtual
    environment's `pythonw.exe` starts included (seen on 19/10/2026). A Startup-folder
    fallback, for a refused task, is left out: a server started from there could not be stopped by
    its task, so the switch says why it failed;
  - macOS: a `launchd` agent with `RunAtLoad` and `KeepAlive`;
  - Linux: a `systemd --user` unit with `Restart=always`, and the user's linger
    (`loginctl enable-linger`) so a VPS starts it with nobody logged in, with no `sudo` and no
    system unit.
  - The service runs `atlas --home <home> serve --always-on`, which reads the home's
    `always_on.json` (a `machine` entry: the port, fixed once so codes stay valid, and whether the
    home is open) and takes the token from the home's vault, as the container does. The app finds
    the always-on server by that file and its `/health`, and reads the token through `atlas server
    token`. Turning it on stops the app's own server for the home and starts the service; turning
    it off does the reverse; neither while a turn runs. An update of the installation (`atlas
    update` and the app's hand-off) stops every always-on home first and starts them again after,
    rolled back too (`AlwaysOnServices`): closing the app leaves them serving, so the update would
    wait for them and refuse, and on Windows `uv sync` cannot replace what their Python loaded.
- **Open to my machines**, inside always on: the server listens on the tailnet address too. On that
  socket only tailnet peers enter (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`); the Host guard also
  takes the tailnet address and the MagicDNS name; the token and the Origin guard are unchanged.
  With no Tailscale installed the switch says how to get it.
- **The connection code**: `atlas1.` and the base64url of `{transport, tailnet host, port, home,
  token}`, versioned so `relay` comes as one more transport. The app shows it masked with Copy;
  `atlas server code` prints it (the VPS's and the container's way). Adding a machine takes the code
  and a name, and for `ssh` the host as `~/.ssh/config` names it. One string carries the address
  and the token together. It is a secret: never
  logged. `atlas server token --new` replaces a home's token, and every code given before stops
  working.
- **The machine list** (`apps/desktop/electron/machines.ts`): a
  name, a transport and a home per machine, "This computer" being the app's own; `machines.json`
  in the app's folder holds names and addresses only, the tokens apart, encrypted by Electron's
  `safeStorage` where the system offers it (DPAPI on Windows), else owner-only. A selector at the
  foot of the sidebar, above the home, only with two machines or more:
  machine → home → conversations. One machine on screen, the
  others kept connected, so a turn elsewhere goes on and notifies; a connection opens the first
  time its machine is opened (no surprise tunnel). Test probes `/health` and the WebSocket with the
  token. A server whose protocol differs is refused, naming the side to update. A roster merged
  across machines and "update all instances" are left out; the machine on screen, when it is a
  Compose host, updates from the same button (The update of a Compose host).
- **Settings begin here**, with one section, Machines: this machine's always on, open, code and
  new token; the other machines with Add, Test, Rename and Remove.
  It opens from a gear at the foot of the sidebar and from the machine selector. Step 15b gave it
  the rest (The settings screen, in Daily use).
- **The server terminal** (`terminal.available`, `terminal.open`, `terminal.input`,
  `terminal.resize`, `terminal.close`, the `terminal.output` and `terminal.exit` events, only to the
  connection that opened it; `atlas.terminals`): the server opens the shell on its machine, its
  choice, its environment and its marks' launch moving there from the app (the nonce is the app's,
  checked as hex since it is written into the start-up script); the app keeps the rest of step 16f
  (the marks read, "still running", ▶ and `!`, `src/terminal/`) over a `Pty` that rides the
  connection. **One path, local too**: `node-pty` left the app, and the side panel's terminal
  always runs through the server, so it works the same over Tailscale, SSH and in the container. A
  terminal is its connection's and closes with it; the answer that names it leaves before any of
  its output. On Windows `winpty.PTY` itself (`pywinpty`),
  without the loopback socket `PtyProcess` adds; on POSIX the standard library's pty instead of
  `ptyprocess` (Atlas's own option: one dependency fewer), the shell started by `subprocess` through
  a short Python that takes the pty as its controlling terminal, never `pty.fork` in a threaded
  server. `pywinpty` is the `terminal` extra: without it the terminal is said to be unavailable.
  **Found moving it**: the server runs under `uv run`, so the shell would get Atlas's environment
  first on its PATH (every terminal's `python` Atlas's), and a
  container's server its token: both are taken out. The shell gets its arguments alone, since
  pywinpty puts the program in front of them: named twice, PowerShell 7 took its own path for a
  script and stopped, and Windows PowerShell ran a second shell inside the first, whose exit code
  read as 1 (found on GitHub's Windows runners, 24/10/2026). Whoever holds the token already runs
  commands through the agent, so the terminal opens nothing new.
- **The preview**: files and artifacts come from the server (`preview.read`, the app's step-16f
  reader moved there with its rules: an absolute path, 5 MB at most, by what the file is; one path,
  local too, and attached images' thumbnails with it). A site up on the other machine
  (`localhost:3000`) opens through one more `-L` on an `ssh` machine
  (a tunnel per port, closed with the machine); over Tailscale the tab says the address is that
  machine's, since a dev server listens on its loopback.
- **Homes on a remote machine**: Back up works, the checked zip coming down the connection in 4 MiB
  parts as attachments go up (`home.backup_download`, then `home.backup_part`; the backup is its
  connection's, and goes with its last part or with the connection), written where this machine's
  save dialog chose: the main process writes only a file a dialog gave, to `<file>.part`, renamed
  once whole. New home and Import stay off and say the terminal's way, since a new home would have
  no server to reach.
- **Bots** are the open machine's and home's, as now; one runs where its home lives, all day when
  always on.
- **Unchanged**: the token in the WebSocket's subprotocol, never the URL; the Host and Origin
  guards; the vault bound to its machine; one server per home.
- **Left out**: a folder chosen in the system's dialog for a remote machine (typed, as now); taking
  over a remote machine's browser; syncing a home across machines (a backup moves one).
- **Proved** by `tests/instances` (the code; the guards: a peer off the tailnet refused, one on it
  let in, no token refused; the service definitions of the three systems), the real service on
  Windows and Linux in CI (on, the server up by itself, off, gone), the e2e with the real app, two
  real `atlas serve` and the fake model (a machine added by code, a turn there, the other's turn
  notifying, the terminal and ▶ through the server, a backup coming down), the SSH tunnel against a
  container running `sshd` and Atlas in Linux CI, and `scripts/prove_machines.py` with the user:
  this PC always on and open, reached from another of the user's machines over Tailscale.

## The documentation — decided 2026-10-25

Step 19, asked for by the user on 25/10/2026: one documentation for whoever uses Atlas, shown by the
website and read by Atlas itself to answer a question about Atlas, which no model knows from its
training.

- **The pages live in the package**, `atlas/docs/pages/`, not in `site/`: a checkout, the
  container's image (which copies `src/`) and the packaged app carry the pages of the version they
  run, offline. The site builds its documentation from that folder. `docs/` at the repository's
  root stays for whoever works on the code.
- **A page** is Markdown with a header the site and `atlas.docs` both read: `title` and
  `description`, and `sidebar.order`, its place in its group. A value YAML would refuse bare
  (a `: ` inside, a reserved character first) is refused by `atlas.docs` too, so the two readers
  never differ. A page's name is its path, `guides/memory`; the groups are the folders
  (`atlas.docs.GROUPS`, which the site's sidebar lists in the same order).
- **`atlas docs`** reads them: the list of pages, one page (by its name, the end of it or its
  title), a search, or every page. A search returns the sections holding every word, a word in a
  heading counting more; with none, the sections holding the most words, since a reader asks in
  words of their own. The command opens no home, so it answers on any installation; the links
  between pages print as the command that shows the page, and a pipe gets UTF-8.
- **The bundled skill `atlas`** is the one line the agent sees: "Questions about Atlas itself:
  answer from `atlas docs`." It holds no documentation of its own: the review may improve any skill
  (Skills, above), and what it edits there is a pointer, never the pages. The agent runs
  `atlas docs` with `run_command`: the app starts the server with `uv run`, whose environment
  leads the PATH, the container has `atlas` on its PATH, and the commands Atlas runs write UTF-8.
- **The generated reference**, by `scripts/gen_docs.py`: the commands page from `build_parser`,
  and the settings page from the settings' dataclasses and the `#:` comment above each field, which
  every setting must have (the generator refuses one without). `tests/docs/test_reference.py`
  fails when a committed page differs from what the code renders.
- **What proves it:** every page reads, sits in a group and links only to pages that are there
  (`tests/docs`); the site's build checks every internal link and every `#` section of the built
  pages (`site/scripts/check-dist.mjs`); every command the skill gives runs, and a page reaches the
  agent whole through `run_command` (`tests/docs/test_skill.py`).

## Language

- Code, identifiers, comments, logs and contributor docs are in English.
- UI text comes from a message catalog: `en` whole and `pt-BR` as a typed partial override, the
  language taken from the system (step 11).
- Concept names are fixed in [GLOSSARY.md](GLOSSARY.md) before they become identifiers.

## Porting code from Atlas V3

Nothing is copied as it was. Every subject passes through this gate:

1. **Read** the V3 code and its tests, and list what the code guarantees.
2. **Check** it against this document: layout, boundaries, typing, errors, UTC, settings and tests.
3. **Port** it in English, fixing what fails the check. Write down what was fixed in the commit
   message.
4. **Bring the tests along.** Rewrite the ones that mock the guarantee they claim to prove.
5. **Leave behind** what the V4 design removed (the knowledge graph, extracted events, the
   heartbeat, the always-on retrieval block).
