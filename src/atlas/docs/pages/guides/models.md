---
title: "Models and keys"
description: "Choose the model Atlas's own agent runs on: OpenAI, Anthropic, Google Gemini or OpenRouter with your key, or a local model through Ollama or vLLM, and see what it spends."
sidebar:
  order: 3
---

Atlas's own agent, the one you talk to in a conversation, runs on a model you choose, with an API
key of yours. Your coding agents are apart: they run on their own plans and logins (see
[Coding agents and accounts](/docs/guides/coding-agents/)).

## The providers

| Provider | Key in the vault | Where to make one |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) |
| Anthropic | `ANTHROPIC_API_KEY` | [platform.claude.com](https://platform.claude.com/settings/keys) |
| Google Gemini | `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| OpenRouter | `OPENROUTER_API_KEY` | [openrouter.ai](https://openrouter.ai/keys) |
| Ollama | none: it runs on your machine | `http://localhost:11434` |
| vLLM | none: it runs on your machine | `http://localhost:8000` |

A plan for a chat app (Claude Pro or Max, ChatGPT Plus) is not an API key: Atlas's agent needs a
key from the provider's developer platform. Those plans still serve your coding agents, which run
on them.

## Add a key

- **The first time the app opens**, with no key yet, its first run asks for one, with a link to each
  provider's page. **Skip** goes on without one, and leaving the first run with no key leaves a band at
  the top that leads back.
- **Later**, open **Settings**, then **Model**: **Provider keys** lists the providers, each with a
  field for its key.
- **In a terminal**, `atlas secrets set OPENAI_API_KEY` reads the key without showing it. Use the
  provider's own name from the table above.
- **In a conversation**, when the agent needs a key it asks for it by name in a card with a masked
  field; the value goes straight to the vault and never reaches the model.

Each key is checked with its provider at no cost before it is kept: a key the provider refuses is
turned away with the provider's answer, and one it cannot check now (no credit, a rate limit, the
provider out of reach) is kept with what the check said. `atlas models check` checks them all again,
without spending a token.

The keys live in the **vault**, `ATLAS_HOME/secrets/vault.json`, each one encrypted. They are never
written in `config.toml`, never in a backup, and the commands the agent runs never see them.

## Choose the model

- **Each conversation** has its model and effort, in the pill under the message box, which says
  both. Its menu lists the models of the conversation's provider, numbered: press 1 to 9 to pick
  one. **Effort** and the other providers with a key open beside it, and with many models a search
  sits on top. A pick is for
  that conversation only, and it is kept with it, across restarts. Switching in the middle of a
  conversation applies from the next turn.
- **The default model**, under **Settings**, then **Model**, is what a new conversation starts on.
  Left as **The first provider with a key**, it is the default model of the first provider with a
  key in the vault, in the order OpenAI, OpenRouter, Google Gemini, Anthropic.
- **The effort** says how hard a reasoning model thinks. Unset, it is `medium`, for everything Atlas
  calls: conversations, bots, routines and subagents. A model that does not reason shows none.

Each provider offers a short list of current models, and the default one is marked **Default**. The same settings are in `config.toml`, under `[model]`
([Settings](/docs/reference/settings/)).

OpenRouter's list ends with free models, the ones whose name ends in `(free)`, and the **Free Models
Router**, which sends each call to whichever free model is up. They cost nothing, but OpenRouter
limits how many calls a day they take, and some of the companies that serve them may keep what is
sent to train their models: leave private data out of those conversations.

## A local model

Ollama and vLLM need no key: start the server on your machine, then tell Atlas which model to use,
in `ATLAS_HOME/models.toml`:

```toml
[[models]]
provider = "ollama"
id = "qwen3:32b"
```

and pick it in the conversation, or make it the default in `config.toml`:

```toml
[model]
provider = "ollama"
name = "qwen3:32b"
```

A local model's calls cost nothing, and Atlas counts them at zero. A provider Atlas does not know,
or a vLLM on another machine, is a `[providers.<id>]` table in the same file, with its
OpenAI-compatible `base_url`. The file is strict: a misspelled field stops Atlas and names the
fields it knows.

## What it spends

```bash
atlas usage
atlas usage --days 7
```

prints what the model calls cost over the last 30 days, or the days you give, by day, by model and
by purpose (a conversation's turns, a compaction, a title, the approval gate's reviewer), and says
where each cost came from: what the provider reported, the price list, or unknown. A price is never
invented.

A few calls are Atlas's own, around your conversations: a title for each new conversation (on a
small, cheap model), the background review that keeps the memory (see
[The background review](/docs/guides/background-review/)), and the reviewer of the approval gate.

## When something is off

- **"No key"** at the first message: no provider has a key in the vault. Add one as above.
- **A key refused** (401 or 403): make a new key on the provider's page and store it again.
- **"Quota used up"** or a long wait: the provider's limit or credit, not Atlas's. On Google
  Gemini's free tier, the daily quota comes back the next day.
- **A local model that does not answer**: check its server runs at the address in the table, and
  that the model's id is the one the server lists.

## Command line

| Command | What it does |
|---|---|
| `atlas secrets set NAME` | Store a key, read from standard input |
| `atlas secrets list` | The keys' names and when each was set, no values |
| `atlas secrets delete NAME` | Remove a key |
| `atlas models check` | Ask each provider whether its key opens it, spending nothing |
| `atlas usage [--days N]` | What the model calls cost |
