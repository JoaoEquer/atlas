---
title: "Troubleshooting"
description: "What to do when Atlas does not install, start, answer, find a coding agent, update or reach another machine, and where its log is."
sidebar:
  order: 3
---

Most problems say what they are, in the app or in the terminal. This page gathers the common ones,
what causes each and the way out. Each guide also has a section of its own for its subject.

## Start here

- **The log** is `logs/atlas.log` in your home (`~/.atlas/logs/atlas.log` by default), with every
  secret masked. `ATLAS_LOG_LEVEL=DEBUG` makes it say more.
- **`atlas version`** prints the version you run, and **`atlas models check`** checks every key
  with its provider, spending nothing.
- **The documentation of your version** is on your machine too: `atlas docs --search WORDS`.

## Installing

- **The install folder is taken** by something that is not an installation of Atlas: the installer
  never writes over a folder it did not make. Move that folder away and run the line again.
- **The path is too long** (Windows): an install folder longer than 120 characters is refused before
  anything is downloaded, since Git for Windows cannot unpack past that.
- **"musl" or "libatomic"** (Linux): the Node Atlas pins does not run on that system, and the
  installer stops before downloading anything.
- **The app does not open on Linux after installing**: Electron's sandbox helper needs to be owned
  by root. The installer does it with `sudo` when it can, and otherwise prints the two commands to
  run.
- **A download failed** or a hash did not match: nothing was installed with it. Run the line again;
  it repairs and goes on from where it stopped.

## The first start

- **The app opens on its first run, on "Choose the model"**: Atlas has no key yet. Paste one, or choose
  **Skip** or **Go to Atlas** and come back from the band at the top
  ([Models and keys](/docs/guides/models/)).
- **A key is refused**: the provider answered 401 or 403. Make a new key on its page.
- **Atlas answers that its quota is used up**: the provider's limit or credit, not Atlas's. Add
  credit, wait for the quota to come back, or switch the conversation to another provider.

## Coding agents

- **A harness shows "not found on this computer"**: install it as its card on the **Executors** tab of
  **Extensions** says, then **Look again**.
- **"Not logged in"**: choose **Log in**, which opens the harness's own login in your browser.
- **"This session is open in a terminal"**: the same session is still open in another program.
  **Continue in a copy** is the safe choice; two programs writing one session mix up its messages.
- **The session stopped at its plan's limit**: give it a route, or switch its account
  ([Coding agents and accounts](/docs/guides/coding-agents/#a-route-for-each-session)).

## Updating

- **It refuses while the app is open**: `atlas update` from a terminal needs the app closed, since
  the app's servers run from the installation. The app's own **Update and reopen** closes it for
  you.
- **It refuses an installation with changes of its own**: someone edited the installation's files.
  Atlas never overwrites them: undo them, or keep them and update with Git yourself.
- **The update failed**: Atlas went back to the version it was on, and the app said why. Your data is
  untouched, and a database is backed up before each migration.

## Other machines

- **"Waiting for Tailscale"**: Tailscale is not running, or this machine is not on your tailnet. Open
  Tailscale and sign in with the same account on both machines.
- **The code is refused**: the other machine's password changed since the code was made. Copy a new
  code there (**Open to my machines**, or `atlas server code`).
- **An SSH tunnel does not connect**: try `ssh user@host` in a terminal first. Atlas uses the same
  keys and `~/.ssh/config`.

## Something else

- **A second server is refused for a home**: one Atlas serves a home at a time, so two agents
  never write the same memory. Close the other app or the other `atlas serve`, or open another
  home.
- **A key that worked stops working after a restore**: keys never travel in a backup. Store them
  again on the new machine.
- **The vault does not open**: Atlas says why once, at start (no key, or the key of another
  vault), and runs without its secrets. A vault copied by hand from another machine needs that
  machine's key, given as `ATLAS_VAULT_KEY`.

Still stuck? Search the [issues on GitHub](https://github.com/marqueskaio/atlas/issues), or open one
with what you did, what happened and the lines of the log around it. Remove anything private from
them first.
