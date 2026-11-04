---
name: gws-calendar-agenda
description: "Google Calendar: the events coming up, from every calendar."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws calendar +agenda --help"
---

# calendar +agenda

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

Lists the events coming up, taken from every calendar.

## Usage

```bash
gws calendar +agenda
```

## Flags

| Flag | Needed | Default | What it does |
|------|--------|---------|--------------|
| `--today` | — | — | Only the events of today |
| `--tomorrow` | — | — | Only the events of tomorrow |
| `--week` | — | — | The events of this week |
| `--days` | — | — | How many days ahead to list |
| `--calendar` | — | — | Keeps one calendar only, given by its name or ID |
| `--timezone` | — | — | An IANA time zone to use instead (e.g. America/Denver). The Google account's time zone otherwise. |

## Examples

```bash
gws calendar +agenda
gws calendar +agenda --today
gws calendar +agenda --week --format table
gws calendar +agenda --days 3 --calendar 'Work'
gws calendar +agenda --today --timezone America/New_York
```

## Good to know

- It only reads: no event is ever changed.
- Every calendar is searched unless --calendar narrows it to one.
- Times are in the Google account's time zone unless --timezone gives another.

## Related skills

- [gws-shared](../gws-shared/SKILL.md): signing in and the global flags
- [gws-calendar](../gws-calendar/SKILL.md): every Calendar command, for calendars and their events
