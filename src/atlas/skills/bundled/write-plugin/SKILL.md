---
name: write-plugin
description: "Write an Atlas plugin with the user: a tool, a skill, a channel, an executor or a screen, checked, installed and, when asked, published in the marketplace."
version: 1.0.0
license: MIT
---

# Writing a plugin

A plugin is a folder that adds to Atlas. `references/guide.md` is the whole guide: what a plugin
may hold, the manifest, and every part of the SDK. ALWAYS open it before you write a plugin's
first file, and read the section of each thing you add.

## First, the smallest kind

Ask what the user wants it to do, then pick the least that does it:

| What it needs | What to make |
|---|---|
| Instructions and scripts, no key | A skill: one folder with a `SKILL.md`, no plugin |
| One MCP server that already exists | An MCP server in the user's `mcp.json`, no plugin |
| Its own Python tool, a secret, a hook, a screen, a channel or an executor | A plugin |

Say which you picked and why before you write anything.

## The steps

1. **Make it in the conversation's folder**, never in the home:
   `atlas plugins new <name> --folder <folder>`, which writes `<folder>/<name>/` with a manifest,
   a tool and a skill. The name is kebab-case. Atlas asks the user first.
2. **Write it** with the file tools: the manifest (`.claude-plugin/plugin.json`), `__init__.py`,
   its skills. Name in `atlas.tools`, `atlas.channels` and `atlas.executors` everything the
   Python registers, and set `atlas.api` to the version the guide gives for what you use.
3. **Check it**: `atlas plugins validate <folder>/<name>`. It loads the plugin as Atlas does, in a
   home thrown away after, and says what it registers and what is wrong. Fix and check again
   until it is clean.
4. **Install it**: `atlas plugins install <folder>/<name>`, which copies it into the home, off.
   Atlas asks the user first. Never write in the home's `plugins/` yourself: the file tools refuse
   it.
5. **The user turns it on**, in Settings → Extensions → Plugins, where Atlas shows what it declares. Never run
   `atlas plugins enable` yourself: the consent is theirs to give. Tell them where to go.
6. **A change** is made in `<folder>/<name>`, checked again, and brought in with
   `atlas plugins update <name>`, asked too. A folder that is a git repository is installed from
   its last commit: commit the change first. When it declares something new, it waits for
   consent again.

## Publishing it, only when asked

The marketplace takes one file, `items/<name>.yaml`, by a pull request the maintainer reviews
(the guide's "Publishing it in the marketplace"). With the `gh` CLI:

1. The plugin's own public repository: `gh repo create <name> --public --source <folder>/<name>
   --push`, with a README that says what it does on the network, in the background and outside
   its folder.
2. Its whole commit: `git -C <folder>/<name> rev-parse HEAD`, 40 characters.
3. A fork of `marqueskaio/atlas-marketplace`, with `items/<name>.yaml` holding that `sha`.
4. `atlas marketplace check items/<name>.yaml --load` in the fork, until it is clean.
5. `gh pr create`, filling the template.

## Rules

- Import only `atlas.plugins.sdk` from Atlas. It only grows, so what the guide shows keeps
  working.
- `register(ctx)` only registers; the work starts when it is called.
- What the plugin writes while it runs goes in `ctx.data_folder`, never in its own folder.
- A secret is read with `ctx.secret`, declared under `atlas.secrets`. Never write a key into the
  plugin, its README or the conversation.
- A plugin runs inside Atlas with the user's permissions. Say so when it reaches the network or
  runs programs.
