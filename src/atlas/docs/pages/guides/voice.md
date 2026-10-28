---
title: "Voice and dictation"
description: "Dictate into any program on your computer with one key, send Atlas audio, hear its answers, and talk to it live, through OpenAI or Gemini on your own key."
sidebar:
  order: 9
---

The Voice plugin, which ships with Atlas, lets you speak to Atlas and to the rest of your computer.
Everything goes through one provider, OpenAI or Gemini, on the key Atlas's provider of that name
already uses: `OPENAI_API_KEY` or `GEMINI_API_KEY`.

## Turn it on

Open **Settings**, then **Extensions**, and on the **Plugins** tab choose **Turn on…** on Voice's card. Atlas shows what it may
do (reach `api.openai.com` and `generativelanguage.googleapis.com`, read the two keys, hold a key on
your keyboard and paste where you are) and turns it on once you agree. With no key yet, add one
first ([Models and keys](/docs/guides/models/)).

## OpenAI or Gemini

The `provider` setting picks who writes down, speaks and talks: `openai`, as it comes, or `gemini`.
Change it in the plugin's settings, and everything after goes through the other provider, with its
own models and voices.

The models and the voices are picked from lists, which Atlas asks of the provider when you open the
settings: a model the provider adds shows up without a new Atlas, and only the ones that do the
job are listed (a speech model for `speech_model`, a live one for `live_model`). Gemini lists the
voices of the app's language first, then the ones that speak every language. OpenAI lists no
voices, so its voice is typed. **Other…**, at the end of a list, types a value it does not hold.

## Dictate anywhere

Press **Caps Lock** in any program to start recording, and press it again: the words land where your
cursor is. A small bar above the taskbar shows that Atlas is listening, without taking the focus.

- **In Atlas's own window**, the words are typed into the message box or an executor's terminal,
  and the clipboard is not touched.
- **In another program**, the words go on the clipboard, Atlas presses the paste key for you, and a
  moment later the clipboard gets back what it held.
- **A program running as administrator** on Windows refuses keys from an ordinary program, so there
  the words stay on the clipboard and the bar says to paste them.
- A recording shorter than half a second is dropped, and one stops by itself at 10 minutes.

The key is yours to change: the **Dictations** page, in the sidebar, has **Change the key**
(a key or a combination such as `F9` or `Control+Shift+Space`) and **Turn off**. Caps Lock keeps
its capitals as they were: Atlas presses it once more for you. Pasting uses the system's own keys:
on a Mac through `osascript`, on Linux through `xdotool` or `wtype`, which must be installed there.

## The microphone in the message box

The microphone button beside the message box dictates too: the words land in the box, unsent, for
you to read and send.

## Your dictations

Every dictation is kept in your home, with its audio, until you delete it: the **Dictations** page
lists them, plays, copies and deletes them. A recording that could not be written down is kept
with why, and **Write it down again** tries once more. A backup carries them.

## Send audio

An audio file you attach in the app, or a voice note you send on [Telegram](/docs/guides/telegram/),
is written down before Atlas reads it: the model receives the words, with a note that a speech model
wrote them and may have misheard one. OGG, MP3, M4A, WAV and WebM work, up to Atlas's 20 MB.

## Hear the answer

Atlas answers in writing, even to audio. Ask to hear an answer ("say it", "send me that as audio")
and it sends a spoken one: in the app as a file you play, on Telegram as a voice message.

Gemini speaks in WAV, and Telegram plays only Opus as a voice message: Atlas turns it into Opus
with [ffmpeg](https://ffmpeg.org/) when your computer has it. Without ffmpeg the answer still
arrives, as an audio file rather than a voice message.

## Talk live

**Talk by voice**, the button beside the message box, opens a live voice conversation: you speak, the voice
answers, and you can interrupt it. Anything that needs work, facts or memory goes to Atlas as a
turn of the conversation, which you see on screen, and the voice says the answer when it comes.

It ends with its button, when you say "stop" or "bye" alone, or after two minutes of silence. A
request Atlas is still working on is never cut for silence. Gemini closes its connection after some
minutes; the conversation goes on in a new one by itself.

## What it costs

You pay the provider on your key, at its prices (these were checked in October 2026):

| | OpenAI | Gemini |
|---|---|---|
| Writing speech down | `gpt-transcribe`, about US$ 0.0045 a minute | `gemini-3.5-transcribe`, about US$ 0.005 a minute |
| Speaking an answer | `gpt-4o-mini-tts`, OpenAI's speech price | `gemini-3.8-flash-tts`, about US$ 0.00225 for 10 seconds |
| Talking live | `gpt-live-1`, about US$ 0.05 a minute, silence included | `gemini-3.8-live`, about US$ 0.005 a minute heard and US$ 0.018 a minute spoken |

Gemini's three models have a free tier too, with a daily limit on calls. What the live
conversation hands over to Atlas runs on the plugin's own model (GPT-6 Luna through OpenAI, Gemini
3.8 Flash through Gemini, both with no reasoning, by default) and is priced as any turn.

## Its settings

Under **Settings**, then **Extensions**, on Voice's card on the **Plugins** tab:

| Setting | Default | What it does |
|---|---|---|
| `provider` | `openai` | Who writes down, speaks and talks: `openai` or `gemini` |
| `dictation_shortcut` | `Capslock` | The key that dictates anywhere; empty turns it off |
| `transcription_model` | the provider's | The model that writes down what you say: `gpt-transcribe` or `gemini-3.5-transcribe` |
| `speech_model` | the provider's | The model that speaks Atlas's answers: `gpt-4o-mini-tts` or `gemini-3.8-flash-tts` |
| `live_model` | the provider's | The model of the live conversation: `gpt-live-1` or `gemini-3.8-live` |
| `voice` | the provider's | The voice that speaks: `marin` or `Kore` |
| `voice_turns_model` | the provider's | The model that answers in the live conversation: one of Atlas's models, or `conversation` for the conversation's own |
| `quiet_close_seconds` | `120` | Seconds of silence before the live conversation closes; `0` never |

Left empty, a model or a voice is the provider's own, the one named above.
