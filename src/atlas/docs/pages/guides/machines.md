---
title: "Several machines"
description: "Install Atlas on a home lab, a VPS or the office PC and open it from the app on your laptop, over Tailscale or an SSH tunnel."
sidebar:
  order: 2
---

Install Atlas on any machine you have, a home lab, a company VPS or the office PC, and open it from the app on your laptop. The Atlas over there does the work, with that machine's files, keys and approvals. The app is only the window.

## Over Tailscale

Tailscale needs no open port and no certificate.

1. On the other machine, open the app's **Settings**, then **Machines**, and choose **Open to my machines**. It shows a connection code. Without the app there, `atlas server code` prints it in a terminal.
2. On your laptop, choose **Add a machine** in **Settings**, then **Machines**, pick **Tailscale** and paste the code.

The code holds that home's password, so paste it only into your own app. **Change the password** makes every code given before stop working.

## Over SSH

If you reach the machine with SSH already, Atlas uses the keys you have. Choose **Add a machine**, pick **SSH tunnel**, and give the host as `user@host` or a name from your `~/.ssh/config`.

## Through a relay

A relay lets the app reach your home from anywhere, with no port open and no Tailscale: the home
and the app both connect out to it, and it joins them. Only the app and the home read what crosses:
the relay carries bytes it cannot read, sealed with a key that never leaves your connection code.

Set the relay's address in your home's `config.toml` and print the code:

```toml
[relay]
url = "wss://relay.example.com"
```

```bash
atlas server code --relay
```

In the app on the other machine, **Settings → Machines → Add**, paste the code and choose **Relay,
from anywhere**. The home waits at the relay as long as its server runs; keep it always on (below)
so it is there when you are away.

## Always on

A home can run as a service: turn on **Always on** and it runs with the app closed and starts with the computer. Routines then run on time, and every window you open sees the same conversations.

## A terminal on each machine

The side panel holds a terminal on the machine the Atlas you are looking at runs on, wherever the app is. Previews of files, local pages and what the agent builds come from that machine too.

## Separate homes

On one machine you can keep several homes, for example one for work and one for personal life. Each has its own memory, keys and conversations, and the app shows which one you are in.
