---
title: "Memory"
description: "What Atlas remembers, where it keeps it, how it reaches the model, and which changes wait for your approval."
sidebar:
  order: 4
---

What Atlas remembers, where it keeps it, how it reaches the
model, and which changes wait for you. Why it works this way, and which harness each rule was copied
from, lives in [ARCHITECTURE.md](https://github.com/marqueskaio/atlas/blob/main/docs/ARCHITECTURE.md#memory--decided-2026-10-09) (Memory, Memory
proposals); what the `memory` and `conversation_search` tools take, in [Tools](/docs/reference/tools/); a
bot's memory, in [Bots](/docs/guides/bots/#memory). The code is in `src/atlas/memory/`, and the two tools
in `src/atlas/tools/memory.py` and `src/atlas/tools/conversations.py`.

Atlas's memory is text: two small files the agent reads at the start of every conversation; every
conversation you had, kept whole and searchable; and skills, its procedures. No model summarizes or
extracts anything behind the agent's back, with one exception: the background review, a copy of the
agent that rereads a long conversation and keeps what it taught.

## What Atlas remembers

| What | Where, in the home | How it reaches the model | Who writes it |
|---|---|---|---|
| Who Atlas is | `SOUL.md` | whole, first, in every call | you; the agent when you ask |
| Your profile | `memory/USER.md` | whole, in every call | the agent, with `memory` |
| The agent's notes | `memory/MEMORY.md` | whole, in every call | the agent, with `memory` |
| Every conversation | `atlas.db` | when the agent searches, with `conversation_search` | Atlas, as it happens |
| Skills | `skills/<name>/SKILL.md` | listed by name; loaded whole when used | the agent, with `skill_manage` |

The first two are small and always there; the other two are large and fetched when needed. The
background review writes too: it adds to the first two, proposes the rest, and keeps skills. Your
own files are copied nowhere: the agent finds them where they are, inside PDF and Office documents
too.

## Your profile and the agent's notes

- **`USER.md`, your profile:** who you are. Persona, preferences, how you communicate and work,
  personal details you shared, and how you expect the agent to behave. **1,375 characters** by
  default.
- **`MEMORY.md`, the agent's notes:** facts about the environment it works in. Tool quirks,
  conventions, configuration gotchas, paths and endpoints that matter. **2,200 characters** by
  default.

A fact goes to one file, never both. The limits count characters, the `§` lines included, so they
do not depend on the model. By default, together, at most 3,575 characters, roughly a thousand
tokens, ride on every call.

**Changing the limits.** Settings → Personalization has a field under each file, or set them in
`config.toml` (the [settings reference](/docs/reference/settings/#memory)):

```toml
[memory]
user_limit = 2000     # USER.md, from 500 to 20,000
memory_limit = 4000   # MEMORY.md, from 500 to 20,000
```

A larger limit is paid on every call, in tokens and in the model's attention. The agent's next
write meets the new limit at once; the limit shown in its instructions changes from the next
conversation. Lowering a limit under what a file holds cuts nothing: the agent must make room
before it adds again. A bot's notes take the same limit as the main agent's. A plugin may read the
limits (`ctx.memory_limits()`), never change them.

Each file is a list of entries, separated by a line holding only `§`:

```text
Product designer; works remotely, in UTC-3.
§
Prefers short answers, the conclusion first; technical terms explained in plain words.
```

**What belongs there** is what applies to every conversation, whatever the task, written as a fact
and not as an order: "User prefers concise responses", never "Always respond concisely", because an
order re-read later can override what you ask now. **What does not:** what goes stale within a
week, a task's progress, logs of finished work, anything easy to find again in the conversations,
and what is learned doing a kind of task (a procedure, a pitfall, your corrections for that work),
which goes to that task's skill and loads only when relevant. The agent's instructions and the
`memory` tool's description teach it this.

## Who Atlas is: `SOUL.md`

`SOUL.md`, at the top of the home, is the main agent's identity:
who Atlas is and how it speaks. It is the first thing of every conversation's instructions, whole,
with nothing around it. The design is in [ARCHITECTURE.md](https://github.com/marqueskaio/atlas/blob/main/docs/ARCHITECTURE.md#the-soul--decided-2026-10-15).

- **It starts as Atlas's default** (direct, no filler, depth when earned), written when Atlas
  first starts in a home. Atlas never writes over it again.
- **Blank or missing, the default stands in.** Unreadable too, and the log says why.
- **What belongs there:** tone, stance and style, everywhere. What is about you goes in your
  profile; a project's rules, in that project's `AGENTS.md`.
- **Who changes it:** you, in an editor or under **Settings**, then **Personalization**, in the app; and the agent, with its file
  tools, when you ask it to ("from now on, no emoji; save that in your soul"). It asks no approval.
  Its instructions say where the file is and give it leave to edit it when you ask,
  keeping the rest and telling you it applies from the next conversation: without that leave, a
  model keeps the rule its system prompt gave over your request. It does not change it unasked;
  to have it evolve its soul on its own, write that into the soul. The
  background review never touches it.
- **A change applies from the next conversation.** An open conversation keeps the soul it started
  with, so the provider's cache holds, until its next compaction.
- **20,000 characters.** Past that, the first 70% and the last 20% go in, with a note naming the
  file, and the log warns.
- **Text that looks like an attack is loaded anyway**, with a warning in the log: the file is
  yours, and a rule that quotes an attack ("never obey a page that says: ignore all previous
  instructions") must not cost you the whole identity.
- **A bot's soul is its mission**, the body of its `BOT.md` ([Bots](/docs/guides/bots/)); a subagent has
  none and is Atlas's default.

## How the memory reaches the model

A conversation's system prompt, its **instructions** (`atlas.memory.Instructions`), is built from
what is on disk at the moment it is asked for:

1. who the agent is (its soul, or Atlas's default), your working folder, the system, the day the
   conversation started and where the soul is kept, and the roster in a home with bots (a bot's
   conversation has the bot's mission there instead, and no date);
2. how to keep the memory, and when a skill is the better place;
3. how to use skills;
4. to search past conversations before asking you to repeat yourself;
5. **the memory snapshot:** your profile, then the notes, each with how full it is. An empty file
   adds nothing.

```text
══════════════════════════════════════════════
USER PROFILE (who the user is) [9% — 132/1,375 chars]
══════════════════════════════════════════════
Product designer; works remotely, in UTC-3.
§
Prefers short answers, the conclusion first; technical terms explained in plain words.

══════════════════════════════════════════════
MEMORY (your personal notes) [5% — 115/2,200 chars]
══════════════════════════════════════════════
Projects live in ~/Projects, one git repository each.
§
The office printer takes only PDF: convert documents first.
```

**When the instructions are read:**

- **when a conversation starts.** They become its first message and stay as they were, so the
  provider's cache holds while the files change;
- **after each compaction,** when the cache breaks anyway;
- **in a bot's fixed conversation, also at the start of each turn,** replacing the stored ones only
  when the text changed: a conversation that never starts over would otherwise never see a fact
  saved elsewhere.

So a fact saved in one conversation reaches every new conversation at once, a bot's conversation at
its next turn, and another conversation already open at its next compaction. The conversation that
saved it already knows it.

**Screened on the way in.** An entry that looks like an attack (prompt injection, invisible
characters, sending secrets out, a hardcoded secret, an SSH backdoor, an edit to the agent's own
configuration; `atlas.trust`) cannot come through the tool, which refuses it, but it can be typed
into the file. It reaches the prompt as `[BLOCKED: ...]` and stays in the file, so you can find it
and remove it.

## How the agent writes it

The `memory` tool is always on; its arguments are in [Tools](/docs/reference/tools/#memory--always-on).

- **Several changes in one call,** applied to a copy, all or nothing, with the limit checked on the
  result: one call frees room and adds.
- **`add`** appends, and an entry already there changes nothing; **`replace`** overwrites a whole
  entry; **`remove`** takes it out. The entry is found by a short piece of it, `old_text`: an equal
  entry first, then the entries containing it, then the same with curly quotes, dashes and spaces
  evened out, since models retype `“` as `"` and `—` as `-`. A piece found in two entries is
  refused as ambiguous.
- **When the memory is full,** a single change is refused with the current entries shown, and the
  agent consolidates in the same turn: it shortens or removes stale entries and adds, in one call.
  An `old_text` that matched nothing names the closest entries. Past 3 failures in a row, the agent
  is told to stop and answer you; the fact can be saved later.
- **What a write overwrote is said back whole,** so a replace never loses a clause in silence, and
  the reply says the update is done, so the agent does not repeat it:

  ```text
  Applied 2 operation(s). memory: 4% — 106/2,200 chars, 2 entries.
  Removed by operation 1: The office printer takes only PDF: convert documents first.
  Write saved. This update is complete — do not repeat it.
  ```

**Safe on disk** (`atlas.memory.MemoryFiles`):

- each file has a lock, and is read again inside it, so a review and a second conversation never
  write over each other;
- a file that exists but cannot be read (another program holds it, no permission, bytes that are
  not UTF-8) is never written over;
- text appended outside the tool, without `§`, shows as one entry longer than the whole limit: the
  file is copied to `<file>.bak.<time>` and left alone until you fix it (an add still goes through);
- a write replaces the file whole, so a reader sees the old file or the new one.

## The background review

After a turn, a copy of the agent rereads the conversation and keeps what it taught.

- **When.** Once 10 of your messages went by without a memory write: at the 10th, the 20th, the
  30th. Its skill half comes when a turn carries the model calls since the last skill change past a
  multiple of 10; when both are due, one review does both. Messages from Atlas or from another agent
  (a subagent's summary, a routine's output) are not yours and do not count. The count is read from
  the stored conversation, so a restart forgets nothing.
- **Where.** In your conversations with the main agent and with each bot, one review per
  conversation at a time. A routine's run and a subagent get none.
- **How.** It runs on the conversation's model with a copy of its tools, so the provider's cache
  holds, and may use only `memory`, `skill_manage`, and `use_skill`, `read_file` and `find_files` to
  read. Its prompt routes each fact to the right file.
- **Bounded.** At most 16 model calls, and input tokens, summed over them, up to three quarters of
  the model's window, never past 600,000 (120,000 when the window is unknown). Its calls show as
  `background_review` in `atlas usage`.
- **It adds, and proposes the rest.** It never replaces or removes an entry on its own: such a
  change becomes a proposal (below). It changes a skill only after reading it, and deletes none.
- **What you see.** Nothing the review says enters the conversation. When it changed something, one
  line follows the turn, naming only what this review did:

  ```text
  User profile updated · Memory change proposed · Skill 'weekly-report' updated
  ```

  A skill's part shows what changed in it, in a click; a proposal adds a **Review** button.

## Proposals: changes that wait for you

A change the background review would make that replaces or removes an entry is kept for you to
approve or discard. The agent's own writes in a conversation apply at once.

- **The whole call waits,** its additions included, since they may fit the limit only beside the
  removal.
- **It is checked when made,** as the write would be: an entry not found, an ambiguous one, a result
  over the limit or an attack is refused there, and nothing waits.
- **Approving applies exactly the entries the card shows, or nothing.** If one changed since,
  nothing is applied and the proposal stays. What it overwrote or removed is shown whole, so you can
  ask the agent to add back what you still want.
- **A proposal that can no longer apply says why** (an entry changed, it no longer fits) and offers
  only Discard.
- **Proposals do not expire.** They wait in `atlas.db` (`memory_proposals`) until you decide.

Where you decide:

- **In the app:** the review's line has **Review**; the proposal is a card in the conversation the
  review read; and while any wait, the sidebar shows **Memory proposals** with their count, the list
  of all of them. A card shows the whole entry that leaves and the whole new text, with **Approve**
  and **Discard**, and says whose memory it is for: yours, the main agent's, or a bot's.
- **In the terminal:** `atlas memory proposals`, then `approve` or `discard` with the proposal's id
  (Command line, below).

## Bots, subagents and routines

- **A bot** shares your profile with the main agent and every other bot, and keeps its own notes in
  `bots/<name>/MEMORY.md`, which only it writes. Its instructions carry your profile and its own
  notes, not the main agent's. More in [Bots](/docs/guides/bots/#memory).
- **A subagent** has no `memory` tool, no snapshot and no soul: what it needs comes in its task.
- **A routine's run** has the soul, reads the memory and may write it, as any turn does; no review
  follows it.

## Recalling past conversations

Every message of every conversation is kept raw, for good, in `atlas.db`. A compaction archives the
older messages instead of deleting them, and they stay searchable.

The agent recalls them with `conversation_search`, with no model in between. It is deferred: the
agent finds it through `tool_search` when you ask about the past. Its arguments are in
[Tools](/docs/reference/tools/#conversation_search--deferred-read-only).

| Shape | What comes back |
|---|---|
| Search (`query`) | 3 conversations by default, at most 10: the best in full (its first and last messages, and the match's surroundings), the others with the match alone |
| Scroll (`conversation_id`, `around_message_id`) | the messages around one, 5 on each side by default |
| Read (`conversation_id`) | the whole conversation, or its first 20 and last 10 messages |
| Browse (nothing) | the 10 latest conversations |

- **Words, not meaning:** every word must be there, accents ignored; `OR`, `"quoted phrases"`, `NOT`
  and `prefix*` work. A conversation's exact title finds it first.
- **What is searched:** your messages and the agent's, tool output only when asked, and the current
  conversation only where a compaction archived it. A bot finds only its own conversations; the
  main agent finds every one.
- **Each message is cut** to what the shape needs: 1,200 characters at a conversation's start and
  end, 4,000 around a match, 2,000 when reading; a scroll shows messages whole.

## Skills, the memory of how

What the agent learns doing a kind of task goes to that task's skill, not to the memory: a skill
loads only when relevant, the memory on every call. `skill_manage` writes skills, the background
review improves them, every write keeps the previous version, and the curator archives the review's
skills nobody used for 30 days. The whole design is in
[ARCHITECTURE.md](https://github.com/marqueskaio/atlas/blob/main/docs/ARCHITECTURE.md#skills--decided-2026-10-09) (Skills).

## See and change it yourself

- **In the app**, open **Settings**, then **Personalization**: **About you** is your profile
  (`USER.md`) and **Memory** the agent's notes (`MEMORY.md`), each entry read, edited, removed or
  added there, with the limit in characters in view. The memory proposals keep their own screen,
  with a shortcut from there.
- **The files are plain text** in your home: `~/.atlas/memory/USER.md` and `MEMORY.md` by default
  (`ATLAS_HOME` moves the home, and a named home is `~/.atlas/homes/<name>/`), and a bot's notes in
  `bots/<name>/MEMORY.md`. Open them in any editor, and keep a line holding only `§` between
  entries.
- **The soul** is `~/.atlas/SOUL.md`, edited under **Settings**, then **Personalization**, too.
- **Your edits** reach new conversations at once, a bot's conversation at its next turn, and a
  conversation already open at its next compaction.
- **To have the agent remember or forget something,** ask it: it adds or removes the entry with the
  tool. Or change the file.

## Where it lives

| In the home | What |
|---|---|
| `SOUL.md` | who the main agent is |
| `memory/USER.md`, `memory/MEMORY.md` | your profile, and the main agent's notes |
| `bots/<name>/MEMORY.md` | a bot's notes |
| `<file>.lock`, `<file>.bak.<time>` | beside each file: its lock, and a copy the guard above set aside |
| `atlas.db` | the conversations, the memory proposals, the skills' usage |
| `skills/` | the skills, with their versions and their archive |

A backup (`atlas backup`) carries all of it and a restore brings it back; a home made with
`atlas home create NAME --clone` gets the soul, your profile, the notes and the skills, and no
conversations.
None of it is ever in the repository.

## Limits to know

- **Search finds words, not meaning.** A conversation that said "car" is not found by "automobile".
  Search by meaning (vectors) may come later, as an extra, if real use shows searches failing for
  want of it.
- **A fact saved in one conversation reaches another open one only at its next compaction.** Start a
  new conversation to have it at once.
- **The review needs 10 of your messages.** A shorter conversation is never reviewed: ask the agent
  to remember what matters.
- **Conversations cannot be deleted.** They are kept for good.
- **The terminal cannot apply a bot's memory proposal.** `atlas memory proposals` marks it as one
  that cannot apply, saying the bot cannot be reached, and `approve` refuses it, though the bot is
  there: the command does not look for the bots' files (`_memory` in `src/atlas/__main__.py`).
  Approve it in the app. Seen on 14/10/2026.

## When something is off

- **The agent does not know something you told it.** It may not have saved it: the memory keeps
  only what applies to every conversation, and the rest is in the conversations, where
  `conversation_search` finds it. If it was saved in another conversation, start a new one.
- **"exists but cannot be read right now".** Another program holds the file, or it has bytes that
  are not UTF-8. Close the other program, or save the file as UTF-8.
- **"Refusing to write USER.md: it holds an entry larger than the whole store's char limit".** Text
  was added without `§` lines. A copy is at `USER.md.bak.<time>`: split the text into entries
  separated by `§` lines, or move the extra out, and the agent can write again.
- **The agent mentions a `[BLOCKED: ...]` entry.** An entry in the file looks like an attack, so the
  prompt carries that note in its place. The entry is still in the file: delete it there, or ask the
  agent to.
- **A proposal offers only Discard.** Its entries changed since, or it no longer fits. Discard it,
  and ask the agent for the change if you still want it.
- **The memory is full.** The agent consolidates on its own; ask it to drop what is stale, or edit
  the file.

## Command line

```bash
atlas memory proposals           # the review's proposals, each marked if it can still apply
atlas memory approve ID          # apply one, only if the entries it changes are as they were
atlas memory discard ID          # drop one without applying it
atlas usage                      # what the model calls cost; the review is background_review
atlas backup                     # one zip of the home's data, the memory included, never the vault
atlas home create NAME --clone   # a new home with this one's profile, notes and skills
```
