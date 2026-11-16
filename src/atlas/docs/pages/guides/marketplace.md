---
title: "Skills and the marketplace"
description: "Find and install skills, MCP servers, plugins and executors other people made for Atlas, each reviewed and pinned to the code that was reviewed, and use skills with a slash."
sidebar:
  order: 11
---

The **marketplace** is where you find what other people made for Atlas: skills, MCP servers,
plugins and new executors. It is a public repository on GitHub,
[atlas-marketplace](https://github.com/marqueskaio/atlas-marketplace), with one page per item.

## The four kinds

| Kind | What it is | Where it goes |
|---|---|---|
| **Skill** | A folder with a `SKILL.md`: how to do one kind of task | your home's skills |
| **MCP server** | A server that gives the agent new tools | your home's `mcp.json` |
| **Plugin** | A bundle: skills, an MCP server, hooks, a screen, Python | your home's plugins, turned off |
| **Executor** | A plugin that adds a coding agent | your home's plugins, turned off |

## Install one

Open **Marketplace** in the sidebar. Each kind has a tab, with a search, the Atlas team's items
marked **Official** and listed first. An item's page shows its README at the reviewed version, what
it needs on your computer, and the secrets it reads, which you store from that page and never see
again. Then **Install**.

Atlas's own plugins, the coding agents, Voice, Telegram and Accounts, are among the Official items:
installing one takes it from your Atlas, at the same version, with no download, and it updates
when Atlas does.

- A **skill** is ready at once. A skill that needs an MCP server installs it along with it.
- An **MCP server** connects as soon as it is installed: no restart.
- A **plugin** or an **executor** arrives turned off. **Turn on** shows what it declares, its
  Python, the hosts it reaches and the secrets it reads, and runs nothing before you agree.

From a terminal, `atlas marketplace list`, `show NAME` and `install NAME` do the same.

## Why you can trust what you install

- **Every item is reviewed** before it enters, by its pull request on GitHub.
- **It installs exactly as reviewed.** A skill, a plugin or an executor names a commit, and Atlas
  fetches that commit, never a branch that may have moved. An MCP server runs a package at one exact
  version, or an image by its digest. A new version is a new review.
- **Compiled files are refused**, and so is an item whose code is not the kind it says.
- **An item retired for safety** stops on every Atlas that installed it the next time the
  marketplace is read, and stays listed as retired, with the reason.

When GitHub cannot be reached, the marketplace shows the last list it read, and says so.

## The public catalogs

After the marketplace's own items, each tab lists what a public catalog offers, read straight from
it: nothing there was reviewed for Atlas, and each card says where it comes from. A card's picture
is the icon its source publishes, or else the GitHub avatar of who published it, which shows the
publisher rather than the item; with neither, the kind's own icon.

- **MCP servers, from the MCP Registry**, the official catalog of MCP servers. With no search, it
  lists the servers updated in the last month: the ones the vendor itself published first (a name
  verified by the vendor's domain, such as `com.stripe/mcp`), then the newest. Type in the search to
  ask the registry for more.
- Atlas runs a server from the registry only as it runs the marketplace's own: at an address over
  `https`, or a package at one exact version. One it cannot run that way says why, and does not
  install.
- What a server asks for (a token, a key) is a secret: its page asks for it, and your vault keeps
  it. A server that signs you in with your account shows **Sign in** once installed.
- It takes a short name in your `mcp.json` (`stripe`, `notes-mcp`); when you have a server by that
  name already, it does not install.
- **Plugins, from Claude Code's plugin directory**, many of them by the service's own maker, the
  most installed first, with a filter by category. The category and the installs are the
  directory's own, counted in Claude Code rather than in Atlas, and one with no count shows none.
  Where the directory says nothing, the plugin's own manifest gives its display name, its author
  and its icon. With no icon, the picture is the GitHub avatar of who published it, or of the GitHub
  organization its author's name is, once GitHub confirms that name; with none of these it is the
  kind's own icon, rather than a guess. A plugin with no display name reads as words
  (`api-security`: API Security), and its page shows the identifier as published. The first time
  the tab opens, reading the manifests takes some seconds; after that they are kept. A plugin
  arrives turned off, as every plugin does; one the marketplace found does not work
  in Atlas says so, and does not install.
- **Skills, from skills.sh**, with how many times each was installed, the most installed first. A
  skill installs from its GitHub repository as it is at that moment, and shows **Update** once its
  repository changed. Before it lands, Atlas reads its text for instructions meant to take over the
  agent, and refuses it when it finds any.

`[marketplace] outside = false` in your `config.toml` lists only the marketplace's own items.

## Updates and removal

**Update** takes the newly reviewed version. When you changed the item since it came (or the
background review improved a skill you installed), Atlas asks first, and keeps what it holds now as
a version you can bring back. **Remove** archives a skill, drops an MCP server's entry, and takes a
plugin away, keeping its data.

## Use a skill

A skill loads by itself when the agent's task matches what it says it is for. You can also call one
by name in the message box: type `/` to see the list, then

```
/release-notes for the changes since last Friday
```

The skill's instructions reach the agent with your words after them. Skills you write yourself, the
ones the agent writes as it learns ([Memory](/docs/guides/memory/#skills-the-memory-of-how)) and the
ones Atlas ships work the same way.

## Make your own

**Make your own**, on the Marketplace screen, opens a conversation where Atlas writes a plugin with
you. [Writing a plugin](/docs/extending/plugins/) explains every part of one, and
[how to publish it](/docs/extending/plugins/#publishing-it-in-the-marketplace) here.

## Command line

| Command | What it does |
|---|---|
| `atlas marketplace list` | The items, by kind (`--search TEXT` asks the public catalogs too) |
| `atlas marketplace show NAME` | One item's page |
| `atlas marketplace install NAME` | Install it |
| `atlas marketplace update NAME` | Take its newly reviewed version |
| `atlas marketplace remove NAME` | Remove it |
| `atlas skills list` | The skills in your home and who made each |
