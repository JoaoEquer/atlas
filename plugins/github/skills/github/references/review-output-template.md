# How to lay out a review

The summary comment of a PR review follows this layout. Copy it and fill each section in.

## The summary comment on the PR

```markdown
## Code Review Summary

**Verdict: [Approved ✅ | Changes Requested 🔴 | Reviewed 💬]** ([N] issues, [N] suggestions)

**PR:** #[number] — [title]
**Author:** @[username]
**Files changed:** [N] (+[additions] -[deletions])

### 🔴 Critical
<!-- MUST be fixed before the merge -->
- **file.py:line** — [description]. Suggestion: [fix].

### ⚠️ Warnings
<!-- SHOULD be fixed, though they don't strictly block -->
- **file.py:line** — [description].

### 💡 Suggestions
<!-- Optional: style, refactors, things to keep in mind later -->
- **file.py:line** — [description].

### ✅ Looks Good
<!-- What was done well, said out loud -->
- [aspect that was done well]

---
*Reviewed by Atlas*
```

## How serious is it

| Level | Icon | For | Blocks the merge? |
|-------|------|-------------|---------------|
| Critical | 🔴 | A security hole, a chance of losing data, a crash, core features broken | Yes |
| Warning | ⚠️ | A bug off the critical path, an error left unhandled, new code with no tests | Usually yes |
| Suggestion | 💡 | Style, ideas for a refactor, a hint about performance, docs that are missing | No |
| Looks Good | ✅ | Clean patterns, tests that cover well, clear names, sound design choices | N/A |

## Picking the verdict

- **Approved ✅** — not one Critical or Warning item; suggestions at most, or nothing at all.
- **Changes Requested 🔴** — there is at least one Critical or Warning item.
- **Reviewed 💬** — only remarks (a draft PR, findings you are unsure of, information).

## Comments on lines

Open each comment on a line with the icon of its level, so they can be told apart at a glance:

```
🔴 **Critical:** The user's input goes straight into the SQL query; use a parameterized query so it cannot be injected.
```

```
⚠️ **Warning:** The error is caught and dropped. Log it, at the very least.
```

```
💡 **Suggestion:** A dict comprehension says this more simply:
`{k: v for k, v in items if v is not None}`
```

```
✅ **Nice:** With the context manager, cleanup happens on the error path too.
```

## A local review, before the push

Reviewing on this machine before a push, keep the same layout, but give it to the user as a
message rather than a comment on a PR. Leave out the header with the PR's details and begin at
the severity sections.
