---
name: gws-calendar-insert
description: "Google Calendar: put a new event on a calendar."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws calendar +insert --help"
---

# calendar +insert

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

Puts a new event on a calendar.

## Usage

```bash
gws calendar +insert --summary <TEXT> --start <TIME> --end <TIME>
```

## Flags

| Flag | Needed | Default | What it does |
|------|--------|---------|--------------|
| `--calendar` | — | primary | The calendar's ID (primary unless set) |
| `--summary` | ✓ | — | The event's title, or summary |
| `--start` | ✓ | — | When it begins (ISO 8601, e.g., 2024-01-01T10:00:00Z) |
| `--end` | ✓ | — | When it ends (ISO 8601) |
| `--location` | — | — | Where it takes place |
| `--description` | — | — | The event's description, its body text |
| `--attendee` | — | — | The email of someone invited (repeat the flag for more) |
| `--meet` | — | — | Adds a Google Meet video call link |

## Examples

```bash
gws calendar +insert --summary 'Standup' --start '2026-06-17T09:00:00-07:00' --end '2026-06-17T09:30:00-07:00'
gws calendar +insert --summary 'Review' --start ... --end ... --attendee alice@example.com
gws calendar +insert --summary 'Meet' --start ... --end ... --meet
```

## Good to know

- Write the times in RFC3339 (e.g. 2026-06-17T09:00:00-07:00).
- --meet puts a Google Meet link on the event by itself.

> [!CAUTION]
> This command **writes**: Atlas asks the user before running it. Say in one line what you are
> going to do and run it; don't ask a second time in chat.

## Related skills

- [gws-shared](../gws-shared/SKILL.md): signing in and the global flags
- [gws-calendar](../gws-calendar/SKILL.md): every Calendar command, for calendars and their events
