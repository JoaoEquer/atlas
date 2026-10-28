---
title: "Install"
description: "Install Atlas with one line on Windows, macOS or Linux, or run it in a container, then add a key and turn on your coding agents."
sidebar:
  order: 2
---

Atlas installs with one line. The installer brings everything it needs, its own Python, Node and tools, each checked against a pinned hash, then builds the desktop app and opens it.

## Windows

In PowerShell:

```powershell
irm https://atlasv4.com/install.ps1 | iex
```

## macOS and Linux

In a terminal:

```bash
curl -fsSL https://atlasv4.com/install.sh | bash
```

The site counts each download of the installer by system and country, to know how many installs began; it keeps nothing that tells who you are (see [Privacy](/docs/guides/privacy/)).

## The first time it opens

1. The app asks for an API key from a model provider: OpenAI, Anthropic, Google Gemini or OpenRouter. Atlas checks the key without spending anything and keeps it in its encrypted vault.
2. Start talking. Each conversation can switch models.
3. To use your coding agents, open **Settings**, then **Executors**: it lists the harnesses on this computer, Claude Code, Codex, the Gemini CLI and DeepSeek's. Turn on the ones you use; each one shows up in the **Executors** tab of the sidebar, and one not installed shows the command that installs it.

## Updating

Run the same line again, or type:

```bash
atlas update
```

If a step of the update fails, Atlas goes back to where it was.

## In a container

Atlas also runs with Docker Compose. From a clone of the repository:

```bash
git clone https://github.com/marqueskaio/atlas.git && cd atlas
docker compose up -d --build
docker compose exec atlas atlas secrets set OPENAI_API_KEY
docker compose exec atlas atlas server token
```

The third line asks for the key without showing it; use `GEMINI_API_KEY` or another provider's name for another provider. The last line prints the token the desktop app needs: open the app with `ATLAS_DESKTOP_URL=ws://127.0.0.1:18790/ws` and `ATLAS_DESKTOP_TOKEN` set to that token.

The container listens only on the host's loopback, and your data lives in a named volume. A backup leaves on standard output, never with the keys:

```bash
docker compose exec -T atlas atlas backup -o - > atlas.zip
```

`atlas restore` puts a backup into any home.
