---
title: "Routines"
description: "Have Atlas run a prompt or a command on a schedule, in plain words or cron: a morning summary, a daily backup, a check every hour."
sidebar:
  order: 7
---

A **routine** is a prompt or a command that Atlas runs on a schedule: a summary of your inbox every
weekday at 9, a check of a site every hour, a backup every night. Each run of a prompt is a
conversation of its own, and you are notified when it ends.

## Make one

The simplest way is to ask, in a conversation:

> Every weekday at 9am, read my unread emails and give me the five that need an answer.

Atlas makes the routine and tells you its schedule. From a terminal:

```bash
atlas routines create inbox --schedule "weekdays at 9am" --prompt "Read my unread emails and list the five that need an answer."
atlas routines create backup --schedule "every day at 3am" --command "atlas backup"
```

A **prompt** routine runs the agent on your words; write the prompt so it stands alone, since the
run starts with no conversation before it. A **command** routine runs a command in the system's
shell and delivers its output. `--model openai/gpt-6-sol` pins a model to a routine; unset, it runs
on the default model. `--bot NAME` runs it as one of your [bots](/docs/guides/bots/), its result
entering the bot's conversation.

## Schedules

| Written | Runs |
|---|---|
| `every 30m`, `every 2h`, `every 1d` | again and again, that long apart |
| `in 2h` | once, two hours from now |
| `every monday 9am`, `monday, wednesday at 18:30` | on those days, at that time |
| `weekdays at 9am`, `weekends at noon`, `every day at 3am` | every weekday, weekend day or day |
| `0 9 * * 1-5` | a 5-field cron expression |
| `2026-12-01T14:00` | once, at that time |

Times are in your system's time zone, or the one under **Settings**, then **Folder and time zone**
(**Time zone of routines**), an IANA name such as `America/Sao_Paulo`.

## The Routines screen

**Routines**, in the sidebar, lists each routine with its schedule, its next run and how its last
one went. From there you can **Run now**, **Pause**, **Resume** and **Remove** a routine, and open
its **Runs**: each run of a prompt opens as the conversation it was. Creating and changing a
routine stays with the conversation and the terminal.

## When it runs

- **Atlas's server fires them**, so a routine runs while the app is open, or always when the home
  runs as a service: **Always on**, under **Settings**, then **Machines** (see
  [Several machines](/docs/guides/machines/#always-on)).
- **A run missed** while the computer was off still runs when it comes back, within half the
  routine's period (between 2 minutes and 2 hours). Past that, the missed runs collapse into one,
  and the list marks it late. A routine still running does not start again.
- **You are notified** when a run ends. A run with nothing to say is kept and notifies nobody: a
  prompt whose answer is `[SILENT]`, or a command with no output. A failure always notifies.

## What a run may do

A run has nobody watching it, so it works with less:

- it cannot ask you a question, and a command that would need your approval is refused;
- it cannot make or change a routine;
- it stops after 10 minutes without activity, with no limit on the whole run;
- it has no background review, and it reads the memory as it was when it started;
- a command routine made by the agent passes the [approval gate](/docs/guides/approval-gate/) when
  it is made and when it is changed, since later it runs with nobody to approve it.

When a run repeats itself (the same failing call, a cycle of the same steps), Atlas stops it, where
in a conversation it would only warn the agent.

## Command line

| Command | What it does |
|---|---|
| `atlas routines list` | The routines, their schedules and their last run |
| `atlas routines create NAME --schedule ... --prompt ...` | Make a prompt routine (or `--command`) |
| `atlas routines run NAME` | Run it at the server's next minute |
| `atlas routines pause NAME` | Stop it firing until resumed |
| `atlas routines resume NAME` | Fire it again; a run that came due meanwhile runs once |
| `atlas routines runs NAME` | Its runs, the newest first |
| `atlas routines remove NAME` | Delete it and its runs |

Every option is in [Commands](/docs/reference/commands/#atlas-routines).
