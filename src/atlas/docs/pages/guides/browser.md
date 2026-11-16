---
title: "The browser"
description: "Atlas uses a browser to act on websites: the one beside the conversation in the app, or a Chrome of its own with a clean or headless mode, and the steps it leaves for you."
sidebar:
  order: 10
---

Atlas can use a web browser the way you do: open a page, read it, fill a form, click, take a
screenshot. With the app open on the computer Atlas runs on, it works in **the browser beside the
conversation**, in the side panel, where you watch the page as it goes. Otherwise it drives a
Chrome, Edge or Chromium already on your computer, in a window you can see and type in.

## Ask for it

Ask in plain words, and say when you want it to use the browser:

> Open the dashboard at status.example.com in the browser and tell me which services are degraded.

To search the web or read a page, Atlas usually fetches it without a browser, which is quicker. The
browser is for what needs one: a page drawn by JavaScript, a login, a form, a screenshot, a site you
are building.

## The Browser tab

The side panel has a **Browser** tab: a browser you use by hand, beside the conversation. Open it
from the globe at the panel's edge or with **Ctrl+Shift+B** (**⇧⌘B** on a Mac). Its tabs are on top,
with an address bar, back, forward and reload, and a button that expands it over the conversation.
Drag its edge to make it wider than the panel's other tabs; the conversation keeps its minimum
width. A new tab can look for the local servers running on this computer, such as a site you are
building.

It opens only web pages (`http` and `https`), refuses downloads, and says no to a site that asks
for the camera, the microphone, your location or notifications. On macOS, ⌘W in a page closes its
tab.

A tab belongs to the conversation that was on screen when it opened, and to the home. The tabs on
top are the ones of the conversation and home you are looking at; the others stay open, hidden,
until you come back to them. A tab Atlas opened has a small mark.

## When Atlas uses it

In its default mode Atlas works in that browser whenever the app is open on the computer its server
runs on. The first page it opens in the conversation on screen brings the panel up by itself; the
pages after that open in the background, and a conversation you are not looking at never takes the
panel.

Atlas sees the tabs you opened in the conversation too, the one on your screen first, so after you
sign in to a site you can tell it to go on from there. It never navigates over a page of yours: it
opens its own tab.

With the app closed, or with a server on another machine or in a container, Atlas uses its own
Chrome window instead (below). The two keep their own sign-ins: a site you signed into in the app's
browser is not signed in in the Chrome window, nor the other way round. Without Chrome, Edge or
Chromium installed, Atlas still has a browser while the app is open.

## Its own profile

When the app's browser is not there, Atlas opens **its own Chrome profile**, kept in your home
(`ATLAS_HOME/browser/profile`), not your everyday one. The first time a site needs a login, sign in
there yourself, once: the profile keeps the session, as your own browser does, and the next visit is
already signed in. The app's browser keeps the sign-ins of each home apart: a login in your work
home does not show in your personal one.

Two more modes are thrown away when Atlas closes them, for when no history should stay. Both always
run in Chrome, Edge or Chromium:

- **clean**: a new, empty profile, in a window;
- **headless**: a new, empty profile, with no window at all, only when you ask for no window.

There is one browser per mode, and each conversation works in a tab of its own.

## What it leaves to you

Atlas stops at **logins, CAPTCHAs and payments**. It asks you to do that part in the browser, beside
the conversation or in its window, then reads the page again once you say it is done. It never
guesses a password, and the browser is a real one: you can type in it, or close it, at any moment.

## What it may reach

- The browser is given wherever the agent may run commands, since what drives it is code on your
  machine.
- An address of a cloud's metadata service is always refused, and so is an address that carries a
  secret from your vault. In the app's browser, a request to a metadata address is refused whoever
  makes it, a page or Atlas.
- Addresses on your own network are allowed, since the browser runs on your machine.
- In the app's browser Atlas drives only the pages of its tabs, never the app's own window.
- What a page says reaches the model marked as data from outside, never as instructions to follow.
- A screenshot reaches the model as an image when the model can see images.

## Which browser

Atlas looks for Chrome, Edge or Chromium where they install, and offers the browser when it finds
one or when the app lends its own. To use another program, or to give it arguments, name it in
`config.toml`:

```toml
[browser]
chrome = ["/usr/bin/chromium", "--lang=en"]
```

## When something is off

- **Atlas says it has no browser.** None of Chrome, Edge or Chromium was found and the app is not
  open on this computer: open the app, install one, or name it under `[browser] chrome`.
- **A site asks to sign in every time.** It runs in the clean or headless mode, which keep nothing.
  Ask Atlas to use its own profile.
- **A site asks to sign in again.** The call ran in the other browser, which keeps its own
  sign-ins: in the Chrome window because the app was closed, or in the app's browser after it
  opened.
- **A site shows its phone layout in the panel.** The panel is narrow: expand it or drag it wider.
  Atlas can ask a page for its desktop layout when it needs one.
- **You want to use your everyday profile.** Chrome refuses to be driven on it, so Atlas keeps its
  own: sign in there once.
