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

The app opens on its first run: five steps, numbered on the left. Each one can be skipped, and **Go to Atlas** leaves at any point.

1. **Model**: an API key from a model provider, OpenAI, Anthropic, Google Gemini or OpenRouter. Atlas checks the key without spending anything and keeps it in its encrypted vault.
2. **Executors**: the coding agents found on this computer, Claude Code, Codex, the Gemini CLI and DeepSeek's. Turn on the ones you use; one not installed shows the command that installs it. With more than one account of a harness, turn on the Accounts plugin here and add the others: when one reaches its plan's limit, the session goes on in the next.
3. **Telegram**: talk to Atlas from your phone. Make a bot with @BotFather, paste its token, and send the bot a message: you show up on the step, and **Let in** lets you talk to Atlas from the chat. The [Telegram](/docs/guides/telegram/) guide has the rest.
4. **Connections**: GitHub and Gmail with Calendar, which Atlas reaches through their own programs, `gh` and `gws`, each logged in on this computer. GitHub's **Connect** opens GitHub's page in your browser and shows the code to type there. Google's **Set up with Atlas** opens a conversation where Atlas walks you through `gws`'s setup; the step waits behind a band at the top, and **Back to it** shows whether it is connected.
5. **Bring your work**: choose the harnesses and how far back, the last 7, 30 or 90 days, or any time. Their sessions from then show in the **Executors** tab of the sidebar; the older ones wait under **Archived**. Nothing is copied, and the harnesses' own files are not touched.

Then start talking: each conversation can switch models. If you used Atlas before the first run existed and your coding agents have folders untouched for a month, Atlas offers **Bring your work** once, alone. **Settings**, then **General**, then **Open the first run again** brings the steps back.

At the foot of the sidebar, **First steps** lists five things to try: hand a task to a coding agent, add a second account, talk to Atlas on Telegram, schedule a routine and create a bot. What you already did is ticked, **Start** takes you to the next one, and **Hide** puts the card away for good.

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
