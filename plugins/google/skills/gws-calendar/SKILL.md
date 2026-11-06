---
name: gws-calendar
description: "Google Calendar: calendars and their events, read and changed."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws calendar --help"
---

# Google Calendar through gws (API v3)

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

```bash
gws calendar <resource> <method> [flags]
```

## Shortcut commands

| Command | What it does |
|---------|--------------|
| [`+insert`](../gws-calendar-insert/SKILL.md) | Puts a new event on a calendar |
| [`+agenda`](../gws-calendar-agenda/SKILL.md) | Lists the events coming up, from every calendar |

## What the API exposes

### acl

  - `delete`: removes one access control rule.
  - `get`: gives back one access control rule.
  - `insert`: adds an access control rule.
  - `list`: gives the calendar's access control list, rule by rule.
  - `patch`: changes an access control rule, with patch semantics.
  - `update`: changes an access control rule.
  - `watch`: follows changes to ACL resources.

### calendarList

  - `delete`: takes a calendar off the user's calendar list.
  - `get`: gives back one calendar from the user's calendar list.
  - `insert`: puts a calendar that already exists on the user's calendar list.
  - `list`: gives the calendars on the user's calendar list.
  - `patch`: changes a calendar already on the user's calendar list, with patch semantics.
  - `update`: changes a calendar already on the user's calendar list.
  - `watch`: follows changes to CalendarList resources.

### calendars

  - `clear`: empties a primary calendar: every event of the account's primary calendar is deleted.
  - `delete`: deletes a secondary calendar. To empty a primary calendar of its events, use calendars.clear.
  - `get`: gives a calendar's metadata.
  - `insert`: makes a new secondary calendar.
The user the request is authenticated as becomes the data owner of that calendar.

Note: authenticate as the user meant to own the calendar's data. Domain-wide delegation of authority lets an application act for a given user. A service account should not be used to authenticate: if it is, the service account becomes the data owner, and things may not behave as expected.
  - `patch`: changes a calendar's metadata, with patch semantics.
  - `update`: changes a calendar's metadata.

### channels

  - `stop`: ends the watch on resources through this channel

### colors

  - `get`: gives the color definitions used for calendars and events.

### events

  - `delete`: deletes an event.
  - `get`: gives back an event from its Google Calendar ID. To find an event by its iCalendar ID instead, call events.list with the iCalUID parameter.
  - `import`: imports an event, that is, adds a private copy of an event that already exists to a calendar. Only an event whose eventType is default can be imported.
Deprecated behavior: a non-default event that is imported has its type turned into default, and any properties particular to its event type are dropped.
  - `insert`: makes an event.
  - `instances`: gives the occurrences of a given recurring event.
  - `list`: gives the events on a given calendar.
  - `move`: moves an event to another calendar, which changes its organizer. Only default events can move; birthday, focusTime, fromGmail, outOfOffice and workingLocation events cannot.
  - `patch`: changes an event, with patch semantics.
  - `quickAdd`: makes an event from a short line of text.
  - `update`: changes an event.
  - `watch`: follows changes to Events resources.

### freebusy

  - `query`: gives when a set of calendars is free or busy.

### settings

  - `get`: gives back one of the user's settings.
  - `list`: gives every setting of the authenticated user.
  - `watch`: follows changes to Settings resources.

## Finding the right call

Look a method up before you call it:

```bash
# List the resources and their methods
gws calendar --help

# One method: which params it needs, their types and defaults
gws schema calendar.<resource>.<method>
```

What `gws schema` prints is what you build the `--params` and `--json` flags from.
