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

## Without a terminal

The Atlas Setup does the same as the line: download the file for your system, open it and click **Install**. It shows each step, and opens Atlas when it ends. Opened again where Atlas is installed, it opens Atlas, or repairs and updates it.

- **Windows:** [Atlas-Setup.exe](https://github.com/marqueskaio/atlas/releases/download/setup/Atlas-Setup.exe). It is not signed yet, so the first time Windows warns about an unknown publisher: click **More info**, then **Run anyway**.
- **Mac:** [Apple Silicon](https://github.com/marqueskaio/atlas/releases/download/setup/Atlas-Setup-arm64.dmg) or [Intel](https://github.com/marqueskaio/atlas/releases/download/setup/Atlas-Setup-x64.dmg). Open the disk image and the Setup in it; the first time, macOS refuses it, so open **System Settings**, **Privacy & Security**, and click **Open Anyway** beside Atlas Setup.
- **Linux:** [Atlas-Setup-x86_64.AppImage](https://github.com/marqueskaio/atlas/releases/download/setup/Atlas-Setup-x86_64.AppImage). Make it executable (`chmod +x`) and open it. With no terminal to ask in, the Setup cannot run `sudo`; when the app's sandbox needs it, the Setup ends with the two commands to run.

The warning comes only once, for the Setup: the app it installs is built on your computer.

## The first time it opens

1. The app asks for an API key from a model provider: OpenAI, Anthropic, Google Gemini or OpenRouter. Atlas checks the key without spending anything and keeps it in its encrypted vault.
2. Start talking. Each conversation can switch models.
3. To use your coding agents, open **Settings**, then **Extensions**, on its **Executors** tab: it lists the harnesses on this computer, Claude Code, Codex, the Gemini CLI and DeepSeek's. Turn on the ones you use; each one shows up in the **Executors** tab of the sidebar, and one not installed shows the command that installs it.

## Updating

The app shows an update button when there is something new. Or run the same line again, or the Setup, or type:

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
