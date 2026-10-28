---
title: "Telegram"
description: "Talk to Atlas from your phone through a Telegram bot of your own, send it files and voice notes, and answer your coding agents' questions and permission requests from the chat."
sidebar:
  order: 8
---

With the Telegram plugin, which ships with Atlas, you talk to Atlas from your phone through a bot
of your own. A conversation there is an Atlas conversation like any other: it shows up in the app
too, and goes on in either place.

## Set it up

1. **Make a bot.** In Telegram, talk to [@BotFather](https://t.me/BotFather), send `/newbot`,
   choose a name and a username, and copy the token it gives you.
2. **Turn the plugin on.** In the app, open **Settings**, then **Extensions**, and on the **Plugins**
   tab choose **Turn on…** on Telegram's card. Atlas shows what the plugin may do (reach `api.telegram.org`,
   read the secret `TELEGRAM_BOT_TOKEN`) and turns it on once you agree.
3. **Paste the token** in the plugin's `TELEGRAM_BOT_TOKEN` field. It goes to the vault, encrypted.
   From a terminal, `atlas secrets set TELEGRAM_BOT_TOKEN` does the same.
4. **Let yourself in.** Send your bot a message. Since you are new to it, it answers with an
   8-character code instead. In the app, the code shows under **People** on the Plugins tab:
   choose **Let in**, then send your message again. From a terminal: `atlas pairing approve CODE`.

A code lasts an hour, and at most 3 wait at a time. Nobody gets an answer from your bot until you
let them in, and the bot reads private chats only: a group is never read.

## In the chat

- **Write as you would in the app.** Atlas answers with its interim messages as they come and the
  reply at the end, showing that it is typing meanwhile. A long reply is split at paragraphs, never
  inside a block of code.
- **Send files.** A photo, a document, a video or an audio file reaches the model as a file dropped
  in the app does, the caption as your words, up to the 20 MB a Telegram bot may download.
- **Send voice notes.** With the [Voice](/docs/guides/voice/) plugin on, a voice note is written
  down before Atlas reads it, and Atlas sends a spoken answer when you ask to hear one.
- **Answer questions** by pressing a button or typing the answer. An approval offers what the app
  offers. A question asked in both places is taken by the first answer, in the chat or in the app.

| Command | What it does |
|---|---|
| `/start` | A word of welcome |
| `/new` | Start a new conversation from your next message |
| `/stop` | Stop the turn that is running |

## Your coding agents, from your phone

When Claude Code, Codex, the Gemini CLI or DeepSeek asks a question or asks for permission, the
request reaches Telegram with **Allow** and **Deny**, or the coding agent's own choices:

- a request from work you handed out in a Telegram conversation goes to that chat;
- a request from a session you started in the app goes to everyone you let in, each prompt naming
  the session's folder.

These requests wait with no deadline, and a channel that starts later still receives the ones
open. The first answer, on the phone or in the app, takes it, and the others are settled.

## Who you let in

Everyone you let in may answer the requests above, so let in only yourself and people you trust
with your machine. **People**, on the Plugins tab of Settings, Extensions, lists them; `atlas pairing list` too, and
`atlas pairing forget telegram USER` removes someone.

## When something is off

- **The bot does not answer.** Check the plugin is on and its token stored, and that only one Atlas
  uses this bot: Telegram lets one program read a bot at a time, so a second one waits, then says
  so.
- **"Get the token again."** Telegram refused the token: ask @BotFather for it (`/token`) and paste
  it again.
- **Formatting looks plain.** When Telegram refuses a message's formatting, Atlas sends it again as
  plain text rather than lose it.
