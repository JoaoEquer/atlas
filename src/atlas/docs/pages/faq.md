---
title: "Questions and answers"
description: "Short answers to what people ask about Atlas: cost, accounts, usage limits, privacy, models, safety, phones and other machines, each with the page that says more."
sidebar:
  order: 4
---

## Is Atlas free?

Yes. Atlas is open source under the MIT license, with no paid tier and no account. Atlas's own agent
runs on an API key you bring, and you pay that provider for what you use, or nothing with a local
model. Your coding agents run on the plans you already have. See [What Atlas is](/docs/).

## What is the difference between Atlas's agent and my coding agents?

Atlas's own agent is the assistant you talk to in a conversation, on the model and key you choose:
it keeps a memory, runs routines, uses the browser and hands work out. Your coding agents (Claude
Code, Codex, the Gemini CLI, DeepSeek's harness) are the programs you installed, which Atlas runs as
**executors**, each on its own plan and login. You can work with either directly, or ask Atlas to
hand a task to one of them.

## Does Atlas replace Claude Code, Codex or the Gemini CLI?

No. Atlas runs the copy you installed and speaks the protocol its makers use for their own apps:
Claude Code's control protocol, `codex app-server`, and the Agent Client Protocol for the Gemini CLI
and DeepSeek. Your settings, permission rules and project instructions stay as they are, and the
sessions you start in a terminal show up in Atlas too. See
[Coding agents and accounts](/docs/guides/coding-agents/).

## Can I use several Claude Code accounts at the same time?

Yes. Each account is its own executor, such as **Claude Code · personal** and
**Claude Code · company**, with its own folder, login, settings, MCP servers, plugins and history.
They run side by side. Add one with **Add an account** in the Executors tab, and sign in through
Claude Code's own login in your browser. The same holds for Codex, the Gemini CLI and DeepSeek.

## What happens when a coding agent hits its usage limit?

With a **route**, the session goes on by itself, on the model and the effort it ran on unless a
stop names others: within one coding agent it continues in a copy on the next account, compacted
first where the agent has `/compact`; across coding agents a new session starts with a handoff of
the task, your latest instruction, the steps not done and the files changed. Without a route, it
goes on with the coding agent's other accounts. You can also switch a session's account yourself
at any time. See
[A route for each session](/docs/guides/coding-agents/#a-route-for-each-session).

## Does my code or data go to Atlas's servers?

There are none. Atlas has no account, no telemetry and no cloud of its own. Your memory,
conversations and encrypted keys stay in a folder on your machine, and what leaves it goes to the
services you set up, such as your model provider. See [Privacy and your data](/docs/guides/privacy/).

## Which models can Atlas's own agent use?

OpenAI, Anthropic, Google Gemini or OpenRouter with your key, or a local model through Ollama or
vLLM, and any other server that speaks OpenAI's API. Each conversation can switch models and
effort. See [Models and keys](/docs/guides/models/).

## Can I use my Claude or ChatGPT subscription for Atlas's own agent?

No: Atlas's agent calls the provider's API, which needs an API key. Your subscription keeps serving
the coding agent it belongs to, which Atlas runs on your own login.

## Is it safe to let it run commands?

Every command passes the [approval gate](/docs/guides/approval-gate/): a few never run in any mode,
dangerous patterns go to a reviewer model or to you, and anything that acts outside your machine,
such as merging a pull request or sending an email, asks you every time. The gate guards; it is not
a sandbox, so keep auto mode for work you trust.

## Can I use it from my phone?

Yes, through a Telegram bot of your own: talk to Atlas, send files and voice notes, and answer your
coding agents' questions and permission requests. See [Telegram](/docs/guides/telegram/).

## Can it run on a server and be used from my laptop?

Yes. Install Atlas on a home lab, a VPS or an office PC, and open it from the app on your laptop
over Tailscale or an SSH tunnel. It can run as a service, with the app closed. It also runs in a
container with Docker Compose. See [Several machines](/docs/guides/machines/) and
[Install](/docs/install/#in-a-container).

## Which systems does it run on?

Windows, macOS and Linux, installed with one line, and anywhere Docker runs. On Linux, a system on
musl (such as Alpine) is not supported outside the container.

## Does it remember things between conversations?

Yes. Every few messages a copy of the agent rereads the conversation and keeps what it learned: who
you are in your profile, facts about your setup in its notes, how to do a task in a skill. It is all
plain text you can read and edit, and anything that would replace or remove what it knows waits for
your approval. See [Memory](/docs/guides/memory/).

## Can I keep several conversations open?

Yes. Each conversation, bot and coding agent session you open has a tab above it, one row of tabs
per home. A click in the list opens it in the passing tab, in italics, which your next click reuses;
it stays once you send a message there or double-click it. **Ctrl+1** to **Ctrl+8** show a tab and
**Ctrl+9** the last, **Ctrl+Tab** the next, and **Ctrl+W** closes the one on screen; on a Mac,
**⌘1** to **⌘9** and **⌘W** do the same. When a question or an approval waits somewhere else, the
tabs say how many, and a click takes you there. The app reopens on the tabs you left, and a closed
tab stays closed.

## How do I find an older conversation?

Scroll down the list of conversations. It shows the fifty with the latest activity first, and reads
the next ones as you near its end, all the way back to your first conversation.

Or type in **Search conversations**, above the list. It finds the conversations whose title, or one
of whose messages, holds every word you typed, also as the start of a longer word, accents and
capitals aside. Each result shows where it matched, the words in bold: in its title, or in a message,
with who said it. A click opens the conversation, **Enter** opens the first, and **Esc** clears the
search.

## How do I rename a conversation?

Select it in the list and press **F2**, or choose **Rename** in its menu: the dots that show over its
time as you point at it, or a right click. Type the name and press **Enter**; **Esc** keeps the one
it had. The name is kept in your home, shows in every app connected to it, and Atlas never replaces
it with a title of its own.

## Can I add my own tools?

Yes: an MCP server in `mcp.json`, a skill in your home, or a plugin, which can add skills, commands,
hooks, tools, channels and new coding agents. The [marketplace](/docs/guides/marketplace/) has
reviewed ones, and [Writing a plugin](/docs/extending/plugins/) shows how to make one.

## How do I update it?

Press the update button that appears in the app when a new version is out, or run `atlas update`
with the app closed. If a step fails, Atlas goes back to where it was. See
[Backups, updates and homes](/docs/guides/backups/#update-atlas).
