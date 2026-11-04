/**
 * `@atlas/plugin-sdk`, the one module a plugin's screen imports from Atlas (step 16m): the app's live state to read, the server's
 * methods to call, and the pages and panels to open. A screen also imports `react` and
 * `react/jsx-runtime`, the app's own, and nothing else.
 *
 * It only grows, as the Python SDK: nothing is taken away or renamed.
 */
import { useStore } from '@nanostores/react'

import type { RpcMethod, RpcMethods } from '@/protocol/protocol.generated'
import {
  $connection,
  $executorAccounts,
  $home,
  $selected,
  $workbench,
  checkSessionAccount,
  current,
  refreshAccounts,
  send,
  switchSessionAccount
} from '@/store/app'
import { togglePanel } from '@/store/terminals'

import { $pluginPage, insertIntoBox } from './registry'

/** What this app offers screens: 1, panels and pages, the app's state, the server's methods; 2,
 *  buttons in the conversation's box, words put into it, and a message sent as the box sends
 *  one (step 17a); 3, a shortcut heard in every program, a text pasted where the user is, and the
 *  floating bar (`ctx.shortcut`, `ctx.paste`, `ctx.bar`, step 17b); 4, a part of an executor's
 *  session and of its place in the sidebar (`session`, `executor`), the executors and their
 *  accounts as the app knows them, and the session on screen going on on another account (step
 *  22, the accounts plugin); 5, a part of the first run's Connections (`connection`, step 26: the GitHub and
 *  Google Workspace plugins). */
export const SCREEN_API = 5

/** Read a store and follow it, in a component: `useValue(host.state.conversation)`. */
export const useValue = useStore

export const host = {
  /** The app's live state, each a store to read with `useValue` or `.get()`. */
  state: {
    /** The connection to the home on screen: `{ state, error }`. */
    connection: $connection,
    /** The conversation on screen, or `null`. */
    conversation: $selected,
    /** The home on screen, by its name; `null` before the first answer. */
    home: $home,
    /** The executors and their instances, as `workbench.list` lists them (version 4). */
    workbench: $workbench,
    /** Each executor's accounts, by its harness's name, as `executor.accounts` lists them, once
     *  read (`host.readAccounts`, version 4). */
    accounts: $executorAccounts
  },
  /** Read an executor's accounts again into `host.state.accounts` (version 4). */
  readAccounts(executor: string): Promise<void> {
    return refreshAccounts(executor)
  },
  /** The session on screen and another of its executor's accounts (version 4): `check` reads
   *  what changes if it goes on there (`workbench.account_switch`), or, with no instance running,
   *  makes it the one its next start runs on and answers `undefined`; `switch` goes on there, in a
   *  copy, the screen following it, with `note` said in the session. */
  sessionAccount: {
    check: checkSessionAccount,
    switch: switchSessionAccount
  },
  /** Call one of the server's methods, as the app does (docs: the protocol). Never the update's:
   *  only the owner, from the update button, updates Atlas (docs/ARCHITECTURE.md, The update of a
   *  Compose host). */
  request<M extends RpcMethod>(method: M, params: RpcMethods[M]['params']): Promise<RpcMethods[M]['result']> {
    if (method.startsWith('update.')) {
      return Promise.reject(new Error("A plugin's screen cannot update Atlas."))
    }

    return current().client.call(method, params)
  },
  /** Show a plugin's page in the main area, by its key (`<plugin>:<id>`). */
  openPage(key: string): void {
    current().$view.set('plugin')
    $pluginPage.set(key)
  },
  /** Send a message in the conversation on screen as the box sends one (version 2): it starts a
   *  turn, steers the one running, or makes the conversation when none is open. */
  send(text: string): Promise<void> {
    return send(text)
  },
  /** Put words into the conversation's box, after what it holds, for the user to read and send
   *  (version 2: a dictation). */
  insertText(text: string): void {
    insertIntoBox(text)
  },
  /** Show or hide the side panel. */
  togglePanel(open?: boolean): void {
    togglePanel(open)
  }
}
