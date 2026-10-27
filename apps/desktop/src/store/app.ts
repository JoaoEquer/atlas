/**
 * The app's state, kept in small stores (nanostores), and what the user can do. The server is
 * authoritative for everything here; these are its copy, cached, not owned, refreshed by its events.
 *
 * **One session per open home** (docs/ARCHITECTURE.md, Backup and homes): each has its own client and its own state, and keeps
 * receiving its events while another home is on screen, so a turn there goes on and notifies when
 * it ends. The stores the screen reads (`$conversations`, `$threads`...) mirror the home on
 * screen. At most 3 homes stay open; opening a fourth lets go of the one used longest ago that has
 * no turn running.
 */
import { atom, map, type ReadableAtom, type WritableAtom } from 'nanostores'

import { t } from '@/i18n'
import { emitPluginEvent } from '@/plugins/events'
import { type EventParams, notFound, type ServerRequest } from '@/protocol/channel'
import type { AtlasClient, ConnectionState } from '@/protocol/client'
import type {
  AccountSwitchRow,
  ApprovalParams,
  ApprovalResult,
  AskParams,
  AskResult,
  AttachmentRow,
  BackupInfo,
  BackupWritten,
  BotDefinition,
  BotOptions,
  BotRow,
  CommandOutput,
  CommandRow,
  CommandStopped,
  ConversationModel,
  ConversationRow,
  ConversationsChanged,
  ExecutorAccounts,
  ExecutorApprovalParams,
  ExecutorConnector,
  ExecutorModel,
  ExecutorQuestionParams,
  ExecutorRow,
  ExecutorUsageRow,
  HomeRow,
  InstanceActivityPayload,
  InstanceRow,
  KeyProvider,
  MarketplaceChanged,
  MemoryEdit,
  MemoryEntries,
  MemoryEntriesParams,
  MessageComplete,
  ModelOptions,
  PairingRow,
  PluginRow,
  PluginScreenRow,
  PreviewPayload,
  ProposalApproved,
  ProposalRow,
  ProviderConnected,
  RequestCancel,
  RouteTargetParams,
  RoutineFinished,
  RoutineRow,
  RunRow,
  SearchHit,
  SecretParams,
  SecretResult,
  SessionFolderRow,
  SessionRouteRow,
  SessionRow,
  SettingChange,
  SettingsList,
  SlashCommandRow,
  Soul,
  SubagentRow,
  TitleChanged,
  UserSaid
} from '@/protocol/protocol.generated'

import { applyCommand } from './commands'
import { guarded } from './guarded'
import { EMPTY_MARKET, type Market, type MarketKind, withRow } from './marketplace'
import { closePreview, openPreview, targetOf } from './previews'
import { applyActivity, caughtUp, fromTranscript, type SessionNote, turnEnded, withNotes } from './session'
import { ended, instanceKey, type Mark } from './status'
import { applySubagent } from './subagents'
import { away, named, readTabs, screenPlace, type Tab, type TabPlace, Tabs, writeTabs } from './tabs'
import { type CommandOutcome, leaveExecutorTab, runCommand, showExecutorTab } from './terminals'
import { setPluginThemes } from './theme'
import { applyEvent, EMPTY_THREAD, fromHistory, terminalText, type Thread } from './thread'
import {
  failed as usageFailed,
  type UsageRead,
  reading as usageReading,
  stale as usageStale,
  told as usageTold
} from './usage'

/** A message of the user on its way to the server: `sending` until the server answered the send,
 * then `queued` while it waits to enter the turn that runs (a correction enters once the current
 * step ends). */
export interface Sending {
  attachments: AttachmentRow[]
  key: string
  state: 'queued' | 'sending'
  text: string
}

/** A request of the server waiting for the user. `answered`, the user answered it and the server
 * has not said it has the answer yet (its `request.cancel`): it says so, and takes no other. */
export type Card = { answered?: boolean } & (
  | { conversationId: string; id: string; kind: 'approval'; params: ApprovalParams }
  | { conversationId: string; id: string; kind: 'ask'; params: AskParams }
  | { conversationId: string; id: string; kind: 'executor-approval'; params: ExecutorApprovalParams }
  | { conversationId: string; id: string; kind: 'executor-question'; params: ExecutorQuestionParams }
  | { conversationId: string; id: string; kind: 'secret'; params: SecretParams }
)

/** The workbench's copy: its instances, and the executors the plugins found. */
export interface Workbench {
  executors: ExecutorRow[]
  instances: InstanceRow[]
}

/** A memory proposal the user approved here, kept on screen to say what it overwrote. */
export interface Approved {
  proposal: ProposalRow
  result: ProposalApproved
}

/** How many conversations a page of the list brings (step 9e). */
export const LIST_PAGE = 50

/** The list of conversations past the pages read (step 9e): where the next page starts, empty
 * once the list is whole; whether it is being read; why its last read failed. */
export interface ListMore {
  cursor: string
  error: null | string
  reading: boolean
}

/** How long the list's search waits after a key before it searches (step 9e). */
export const SEARCH_PAUSE_MS = 250
/** The most conversations the list's search shows. */
const SEARCH_LIMIT = 50

/** The list's search (step 9e): what its field holds, the words of the answer shown and what they
 * found; whether a search is under way, and why the last one failed. */
export interface ListSearch {
  error: null | string
  hits: SearchHit[]
  more: boolean
  /** The words `hits` answers. */
  query: string
  reading: boolean
  /** What the field holds, as typed. */
  text: string
}

export const NO_SEARCH: ListSearch = { error: null, hits: [], more: false, query: '', reading: false, text: '' }

/** As a session's turn ends, how often and how many times it is read until its harness kept the
 * answer on screen: three seconds in all. */
const CATCH_UP_MS = 300
const CATCH_UP_TRIES = 10
/** How often showing the executors tab looks for the harnesses again: each look starts them. */
const DETECT_EVERY_MS = 60_000

type Connection = { error: null | string; state: ConnectionState }
/** Which list the sidebar shows: the bots beside the conversations, and the executors since step
 * 16f. */
export type SidebarTab = 'bots' | 'conversations' | 'executors'

/** An executor's connectors as the side panel shows them: being read (the last read kept
 * meanwhile), read, or why they could not be. */
export interface ConnectorsRead {
  connectors: ExecutorConnector[]
  error: null | string
  /** Its plugin lists them; when not, the panel says so. */
  listed: boolean
  reading: boolean
}

/** The side panel's read of an executor's skills in a folder (`slashKey`): running, or why it
 * failed; what it read goes to `$slashCommands`, which the session's box shares. */
export interface CommandsRead {
  error: null | string
  reading: boolean
}

/** An executor's folders as the executors tab lists them, none of their sessions read: being
 * read (the last read kept meanwhile), read, or why they could not be. */
export interface FoldersRead {
  error: null | string
  folders: null | SessionFolderRow[]
  reading: boolean
}

/** One folder's sessions, read the first time it is opened and kept while it is closed: being
 * read (the last read kept meanwhile), read, or why they could not be. `stale` once its instances
 * changed while it was closed: it is read again when it opens. */
export interface SessionsRead {
  error: null | string
  reading: boolean
  sessions: null | SessionRow[]
  stale: boolean
}

/** One folder of one executor, as `$sessions` and `$openFolders` key it. */
export function folderKey(executor: string, folder: string): string {
  return `${executor}\n${folder}`
}

/** The bot dialog on screen: a new bot, or one being edited, with its definition as read. */
export type BotDialog = { definition: BotDefinition | null; mode: 'create' | 'edit' }
type View = 'conversation' | 'marketplace' | 'plugin' | 'proposals' | 'routines' | 'session'

/**
 * What the user chose for a session open in another program (decided with the user on 15/10/2026):
 * read it only, go on in a copy of it
 * (`--fork-session`), or go on here anyway.
 */
export type Elsewhere = 'copy' | 'here' | 'read'

/** An executor's session on screen (step 16f): one of its list, or a new one in a folder. */
export interface OpenSession {
  /** What the user chose, when it is open in another program; `null` before they chose. */
  choice: Elsewhere | null
  /** Where it is open outside Atlas (`claude-vscode`, `cli`), when it is. */
  elsewhere: string
  executor: string
  /** Its harness no longer keeps it: it opens to read only. */
  expired: boolean
  folder: string
  /** The instance working on it here; empty while none does. */
  instanceId: string
  /** Empty for a new session, until its harness says its id. */
  sessionId: string
  title: string
}

/** A session's route as the screen sets it: its stops in order, each with the model and the
 * effort the session goes on with there, whether it is followed, and whether a copy compacts
 * first. */
export interface SessionRouteSet {
  compact: boolean
  enabled: boolean
  targets: RouteTargetParams[]
}

/** What opening a session needs to know of it: its row in the list, its tab, or its instance. */
type SessionShown = Pick<SessionRow, 'elsewhere' | 'executor' | 'folder' | 'instance_id' | 'session_id' | 'title'>

/** The switches under a session's box, before an instance runs on it: applied when one starts. The
 * model and the effort are the executor's ids; `null`, its own default. */
export interface SessionSettings {
  /** The account its next instance starts on (`''`, the main one); `null`, the folder's. */
  account: null | string
  atlasAnswers: boolean
  auto: boolean
  effort: null | string
  model: null | string
}

const NO_SESSION_SETTINGS: SessionSettings = {
  account: null,
  atlasAnswers: false,
  auto: true,
  effort: null,
  model: null
}

/** The two switches under a session's box. */
export type SessionSwitch = 'atlasAnswers' | 'auto'

/** How many homes stay open at once. */
export const MAX_OPEN_HOMES = 3

/**
 * The key of the new conversation not made yet, in the stores keyed by conversation: its model
 * pick and its "Auto" wait here until its first message makes it (no conversation exists until the
 * first message), so a picker or "Auto" touched with nothing sent makes no empty conversation.
 */
export const DRAFT = ''

/** What the window can do besides talking to the server: bring itself forward, and reach the
 * system's file dialogs and the app's own folder (step 10c). */
export interface Shell {
  /** Where a home's backup goes, from the system's save dialog; `null` when cancelled. */
  backupTarget?: (home: string) => Promise<null | string>
  /** A backup to import, from the system's open dialog; `null` when cancelled. */
  backupSource?: () => Promise<null | string>
  /** A part of a remote machine's backup, written on this machine where the dialog said (step 15). */
  backupWrite?: (file: string, offset: number, data: string) => Promise<void>
  /** The last part came (`whole`), or the backup failed: the file put in its place, or gone. */
  backupFinish?: (file: string, whole: boolean) => Promise<void>
  /** A folder of the server's machine, from the system's dialog; left out against a remote one. */
  chooseFolder?: () => Promise<null | string>
  focus: () => void
  notify: (title: string, body: string, onClick: () => void) => void
  /** Open this home first next time (the last home). */
  remember?: (home: string) => void
}

/** A list of the sidebar, read from the server. */
export type Listed = 'bots' | 'conversations' | 'workbench'

/** What the screen reads from the server and shows as a list or a page of its own. */
export type Readable = 'proposals' | 'routines' | Listed

/** One open home: its client, and the copy of its server's state. */
export class HomeSession {
  readonly $connection = atom<Connection>({ error: null, state: 'idle' })
  /** The lists read since the home opened: until one is, it says it is being read, never that it
   * is empty (found by the UI review on 25/10/2026: "No conversations yet" while the server
   * started, to someone with conversations). */
  readonly $read = map<Record<Listed, boolean>>({ bots: false, conversations: false, workbench: false })
  /** Why each list or page could not be read the last time, until a read works: it says so in
   * its place, with a way to read it again, never looking empty or old (the UI review,
   * 25/10/2026, which found these reads failing in silence). */
  readonly $readError = map<Partial<Record<Readable, string>>>({})
  /** The list of conversations as far as its pages were read, the latest activity first. */
  readonly $conversations = atom<ConversationRow[]>([])
  readonly $listMore = atom<ListMore>({ cursor: '', error: null, reading: false })
  readonly $search = atom<ListSearch>(NO_SEARCH)
  readonly $selected = atom<null | string>(null)
  readonly $threads = map<Record<string, Thread>>({})
  readonly $autoMode = map<Record<string, boolean>>({})
  /** Each conversation's model: its pick, or the default. */
  readonly $models = map<Record<string, ConversationModel | undefined>>({})
  /** What the picker offers, with the default; read when the home connects. */
  readonly $modelOptions = atom<ModelOptions | null>(null)
  /** The providers that take a key, for the key screen; read when the home connects. */
  readonly $keyProviders = atom<KeyProvider[] | null>(null)
  /** The user chose to connect a provider later: the key screen gives way to a band. */
  readonly $keyLater = atom(false)
  /** A key stored with something to say (no credit, a limit, a provider out of reach), kept on the
   * key screen until the user has read it. */
  readonly $keyNotice = atom<null | ProviderConnected>(null)
  /** Text for a conversation's box: the messages a stop handed back. The box takes it once. */
  readonly $restored = map<Record<string, string | undefined>>({})
  /** The user's messages on their way, by conversation (`DRAFT` for a new one): drawn at once,
   * marked as such, until the server's own `message.start` or `message.steered` has them (the
   * UI review, 25/10/2026: the words showed only once the server answered, a moment later on
   * another machine). */
  readonly $sending = map<Record<string, Sending[] | undefined>>({})
  readonly $cards = atom<Card[]>([])
  /**
   * The memory proposals waiting for the user, as the server listed them: each tried against the
   * memory when listed, so one that can no longer apply says why. Read when the home connects,
   * when they change, and when the screen shows them.
   */
  readonly $proposals = atom<ProposalRow[]>([])
  readonly $approved = map<Record<string, Approved | undefined>>({})
  /** What the main area shows: the selected conversation, the memory proposals, or the routines. */
  readonly $view = atom<View>('conversation')
  /** The home's routines, read when the home connects and when they change. */
  readonly $routines = atom<RoutineRow[]>([])
  /** The marketplace's screen (step 16s): read when it opens and when an item changes. */
  readonly $market = atom<Market>(EMPTY_MARKET)
  /** The home's plugins and the people their channels let in or who wait with a code. */
  readonly $plugins = atom<PluginRow[]>([])
  /** The screens of its plugins that are on (step 16m), which the app loads while it is on screen. */
  readonly $screens = atom<PluginScreenRow[]>([])
  readonly $pairings = atom<PairingRow[]>([])
  /** What the server refused on the Plugins screen, said there. */
  readonly $pluginError = atom<null | string>(null)
  /** Each routine's runs, read when its row opens. */
  readonly $runs = map<Record<string, RunRow[] | undefined>>({})
  /** Each conversation's running subagents, for its agent map: read when it opens, kept by its events. */
  readonly $subagents = map<Record<string, SubagentRow[] | undefined>>({})
  /** Each conversation's background commands, for its agent map: read when it opens, kept by its
   * events, an ended one kept to show how it ended. */
  readonly $commands = map<Record<string, CommandRow[] | undefined>>({})
  /** The workbench (step 16c): its instances and the executors the plugins found, the same in
   * every conversation; read when the home connects, kept by its events. Since step 16f its cards
   * live in the executors tab. */
  readonly $workbench = atom<Workbench>({ executors: [], instances: [] })
  /** What the server refused on a card (by instance), or on the new instance's form (`''`). */
  readonly $workbenchError = map<Record<string, string | undefined>>({})
  /** The server looks for the harnesses again (`executor.detect`) right now. */
  readonly $detecting = atom(false)
  /** The home's bots (step 13b), read when the home connects and when they change. */
  readonly $bots = atom<BotRow[]>([])
  /** The home's picture as a data URL, read when the home connects; `null` when it has none. */
  readonly $picture = atom<null | string>(null)
  /** What the server refused of a picture, said where it was chosen. */
  readonly $pictureError = atom<null | string>(null)
  /** The executors tab (step 16f): each executor's folders, as its plugin listed them; read when
   * its list shows, and again as its instances change. */
  readonly $sessionFolders = map<Record<string, FoldersRead | undefined>>({})
  /** Each folder's sessions (`folderKey`): read only once it is opened, and again as its
   * instances change while it is open. */
  readonly $sessions = map<Record<string, SessionsRead | undefined>>({})
  /** The folders open in the list (`folderKey`); a folder starts closed. */
  readonly $openFolders = map<Record<string, true | undefined>>({})
  /** The executor whose sessions the sidebar lists and whose tab the side panel has, the last
   * opened; `null`, none. */
  readonly $openExecutor = atom<null | string>(null)
  /** The executors open in the list, several at once (asked for by the user on 20/10/2026). */
  readonly $openExecutors = map<Record<string, true | undefined>>({})
  /** The conversations a turn runs in now (status.ts): their rows' word as the list is read, until
   * `conversations.changed` tells of one, which a row read just as its turn ends cannot undo. */
  readonly $live = map<Record<string, true | undefined>>({})
  /** What a row not on screen keeps from an end the user did not see: a conversation by its id,
   * an instance by `instanceKey`. */
  readonly $marks = map<Record<string, Mark | undefined>>({})
  /** When this app saw each conversation, executor, folder (`folderKey`) and instance
   * (`instanceKey`) start to work, so its list puts it first at once. */
  readonly $used = map<Record<string, number | undefined>>({})
  /** The session on screen, its trail, the switches for its next instance, and what the server
   * refused. */
  readonly $openSession = atom<OpenSession | null>(null)
  readonly $sessionThread = atom<Thread>(EMPTY_THREAD)
  readonly $sessionSettings = atom<SessionSettings>(NO_SESSION_SETTINGS)
  readonly $sessionError = atom<null | string>(null)
  /** The route of the session on screen (asked for by the user on 21/10/2026), read when its
   * instance opens; `null` before it is read or with no instance. */
  readonly $sessionRoute = atom<null | SessionRouteRow>(null)
  /** The tabs of the central area (store/tabs.ts). */
  readonly tabs = new Tabs()
  /** Where the window keeps them, its machine and name, once the home said its name. */
  private tabsHome: null | string = null
  /** The trail of each session shown before, by `sessionOwner`: shown again at once as its tab
   * comes back, while it is read anew. */
  private readonly sessionThreads = new Map<string, Thread>()
  /** Each executor's models and their efforts, as its plugin read them: read when one of its
   * sessions opens, and again after its account changes. */
  readonly $executorModels = map<Record<string, ExecutorModel[] | undefined>>({})
  /** Each executor's connectors, for the side panel: read when it is chosen in the sidebar, and
   * again when asked. */
  readonly $executorConnectors = map<Record<string, ConnectorsRead | undefined>>({})
  /** Each executor's plan usage, for the side panel (one per account, step 16h): read when it is
   * chosen in the sidebar and the last read is old, and told by its instances as they work. */
  readonly $executorUsage = map<Record<string, undefined | UsageRead>>({})
  /** Each executor's accounts (step 16h): read when it is chosen in the sidebar, and again when
   * the server says they changed. */
  readonly $executorAccounts = map<Record<string, ExecutorAccounts | undefined>>({})
  /** The slash commands of each executor in each folder, as its harness describes them, for the
   * session's box (`slashKey`): read when a session opens there, and again when its instance's
   * commands change. */
  readonly $slashCommands = map<Record<string, SlashCommandRow[] | undefined>>({})
  /** The side panel's reads of `$slashCommands`, by `slashKey`. */
  readonly $commandReads = map<Record<string, CommandsRead | undefined>>({})
  /** The read of each `slashKey` running or done, with the instance's names it was read for. */
  private slashReads = new Map<string, string>()
  /** What the box of Atlas's conversation or a bot's offers as `/` is typed, by conversation
   * (`DRAFT` for a new one): Atlas's own commands and the skills the user invokes there. */
  readonly $atlasCommands = map<Record<string, SlashCommandRow[] | undefined>>({})
  /** The home's name, once its server said it; `null` until then for the home that opened first. */
  name: null | string
  lastUsed = Date.now()
  private readonly requests = new Map<string, ServerRequest>()
  private stops: (() => void)[] = []
  /** The folder its conversations' file tools start from, where their terminal opens; known
   * once a conversation opened (step 16f). */
  workspace = ''
  /** Numbers the live steps of the session on screen, for their keys. */
  private activitySeq = 0
  /** Numbers the messages sent from here, for their bubbles' keys. */
  private sendSeq = 0
  /** What the screen said in each card's session that its harness keeps nowhere, as a switch of
   * account (asked for by the user on 22/10/2026): kept with the place it was said, so a read of
   * the session again shows it there too. */
  private readonly sessionNotes = new Map<string, SessionNote[]>()
  /** When the harnesses were last looked for at the screen's ask. */
  private detectedAt = 0
  /** The executors whose list is read again soon, as their instances change. */
  private readonly sessionsDue = new Set<string>()
  /** The conversations `conversations.changed` told of since the socket opened. */
  private readonly heard = new Set<string>()
  /** The lists being read, by what they list: a second ask waits for the same read. */
  private readonly reads = new Map<string, Promise<void>>()
  /** The reads asked for again while one ran: it is taken once that one ends, as what it read may
   * be older than what changed. */
  private readonly readsAgain = new Map<string, Promise<void>>()
  /** The list of conversations' reads, one at a time (step 9e), so a page never crosses a refresh:
   * a page goes on from where the list ends, and a refresh reads again as many rows as it holds.
   * One asked for while another of its kind waits its turn joins that one. */
  private listing: Promise<void> = Promise.resolve()
  private readonly listWaiting = new Map<'more' | 'refresh', Promise<void>>()
  /** The list's searches asked so far: only the latest one's answer shows (step 9e). */
  private searches = 0
  private searchTimer: ReturnType<typeof setTimeout> | undefined

  constructor(
    readonly client: AtlasClient,
    /** What its server was asked for: a home's name, or `null` for the one that opens first. */
    readonly key: null | string
  ) {
    this.name = key
  }

  start(): void {
    const atlas = this.client
    this.stops = [
      atlas.onState(state => {
        this.$connection.set({ error: state === 'open' ? null : this.$connection.get().error, state })

        if (state === 'open') {
          // What was told before the socket fell may be over: the rows say it again.
          this.heard.clear()
          // An answer sent before the socket fell: its request comes again if it still waits.
          this.$cards.set(this.$cards.get().filter(card => !card.answered))
          // A read that fails says so in its place ($readError).
          void this.refreshList().catch(() => undefined)
          // They belong to no conversation, so nothing replays them: read them again.
          void this.refreshProposals().catch(() => undefined)
          void readHomes(this).catch(() => undefined)
          void this.refreshModelOptions().catch(() => undefined)
          void this.refreshKeyProviders().catch(() => undefined)
          void this.refreshRoutines().catch(() => undefined)
          void this.refreshBots().catch(() => undefined)
          void this.refreshPicture().catch(() => undefined)
          void this.refreshPlugins().catch(() => undefined)
          // The session on screen missed what its instance did while the socket was gone.
          void this.refreshWorkbench()
            .then(() => this.readSession())
            .catch(() => undefined)
        }
      }),
      atlas.onError(error => {
        this.$connection.set({ ...this.$connection.get(), error: error.message })
      }),
      atlas.onEvent(event => {
        guarded(() => {
          this.handleEvent(event)
        })
      }),
      atlas.onResync(conversationId => {
        void this.readConversation(conversationId)
      }),
      atlas.onRequest(request => this.handleRequest(request))
    ]
    // The window keeps the tabs as they change, and which one is on screen; their titles follow
    // the lists.
    const save = () => {
      this.saveTabs()
    }
    const retitle = () => {
      this.tabs.retitle(place => this.titleOf(place))
    }
    this.stops.push(
      this.tabs.$tabs.listen(save),
      this.$selected.listen(save),
      this.$view.listen(save),
      this.$openSession.listen(save),
      this.$conversations.listen(retitle),
      this.$bots.listen(retitle)
    )
    atlas.start()
  }

  stop(): void {
    for (const stop of this.stops) {
      stop()
    }

    clearTimeout(this.searchTimer)
    this.client.stop()
  }

  /** A turn runs in one of its conversations. */
  running(): boolean {
    return Object.values(this.$threads.get()).some(thread => thread.running)
  }

  /** Read `what` with `read`, keeping why it failed until a read of it works; the failure still
   * reaches the caller. */
  async reading(what: Readable, read: () => Promise<void>): Promise<void> {
    try {
      await read()
      this.$readError.setKey(what, undefined)
    } catch (error) {
      this.$readError.setKey(what, error instanceof Error ? error.message : String(error))
      throw error
    }
  }

  /** Read the list of conversations again, as far as its pages were read: what changed anywhere in
   * it shows, and the next page goes on from its new end, so none is read twice or skipped. */
  async refreshList(): Promise<void> {
    await this.reading('conversations', () =>
      this.listRead('refresh', async () => {
        const held = this.$conversations.get().length
        const { conversations, next_cursor } = await this.client.call('conversation.list', {
          limit: Math.max(LIST_PAGE, held)
        })
        this.$conversations.set(conversations)
        // A server older than the pages sends no cursor: its one page is the whole list.
        this.$listMore.set({ cursor: next_cursor || '', error: null, reading: false })
        this.$read.setKey('conversations', true)
        this.readLive(conversations.map(row => [row.id, row.running] as const))
      })
    )
    // A home made in the terminal meanwhile shows up too.
    void readHomes(this).catch(() => undefined)
  }

  /** The next page of the list of conversations, as the sidebar nears its end; nothing once the
   * list is whole. A failure says so at the list's end, and only `again`, its button, reads it
   * again: scrolling does not. */
  moreConversations(again = false): Promise<void> {
    const { cursor, error, reading } = this.$listMore.get()

    // A page being read already is the one the scroll asks for.
    if (!cursor || reading || (error !== null && !again)) {
      return Promise.resolve()
    }

    return this.listRead('more', async () => {
      const { cursor } = this.$listMore.get()

      if (!cursor) {
        return
      }

      this.$listMore.set({ cursor, error: null, reading: true })

      try {
        const page = await this.client.call('conversation.list', { cursor, limit: LIST_PAGE })
        const held = this.$conversations.get()
        const known = new Set(held.map(row => row.id))
        this.$conversations.set([...held, ...page.conversations.filter(row => !known.has(row.id))])
        this.$listMore.set({ cursor: page.next_cursor || '', error: null, reading: false })
        this.readLive(page.conversations.map(row => [row.id, row.running] as const))
      } catch (error) {
        this.$listMore.set({ cursor, error: error instanceof Error ? error.message : String(error), reading: false })
      }
    })
  }

  /** One read of the list at a time, in the order asked; one asked for while another of its kind
   * waits joins it. */
  private listRead(kind: 'more' | 'refresh', read: () => Promise<void>): Promise<void> {
    const waiting = this.listWaiting.get(kind)

    if (waiting) {
      return waiting
    }

    const turn = this.listing.then(() => {
      this.listWaiting.delete(kind)

      return read()
    })
    this.listWaiting.set(kind, turn)
    this.listing = turn.catch(() => undefined)

    return turn
  }

  /** What the list's search field holds (step 9e), searched once the typing pauses; blank words
   * search nothing. */
  typeSearch(text: string): void {
    clearTimeout(this.searchTimer)
    const words = text.trim()

    if (!words) {
      // An answer still on its way is for words no longer there.
      this.searches++
      this.$search.set({ ...NO_SEARCH, text })

      return
    }

    this.$search.set({ ...this.$search.get(), text })
    this.searchTimer = setTimeout(() => {
      void this.search(words)
    }, SEARCH_PAUSE_MS)
  }

  /** The same search again, after it failed. */
  searchAgain(): void {
    const words = this.$search.get().text.trim()

    if (words) {
      void this.search(words)
    }
  }

  /** End the search: the field empties at once, and an answer on its way is dropped. */
  clearSearch(): void {
    clearTimeout(this.searchTimer)
    this.searches++
    this.$search.set(NO_SEARCH)
  }

  private async search(words: string): Promise<void> {
    const asked = ++this.searches
    this.$search.set({ ...this.$search.get(), error: null, reading: true })

    try {
      const found = await this.client.call('conversation.search', { limit: SEARCH_LIMIT, query: words })

      if (asked === this.searches) {
        this.$search.set({ ...this.$search.get(), hits: found.hits, more: found.more, query: words, reading: false })
      }
    } catch (error) {
      if (asked === this.searches) {
        this.$search.set({
          ...this.$search.get(),
          error: error instanceof Error ? error.message : String(error),
          reading: false
        })
      }
    }
  }

  /** Show a conversation, in its tab or the passing one: its stored messages, then its events from
   * the number they were read at. */
  async selectConversation(conversationId: string): Promise<void> {
    const place: TabPlace = { conversationId, kind: 'conversation' }
    this.tabs.show(place, this.titleOf(place) ?? '')
    this.$selected.set(conversationId)
    this.$marks.setKey(conversationId, undefined)
    this.$view.set('conversation')
    // Its proposals' cards are checked again as it opens.
    void this.refreshProposals().catch(() => undefined)
    await this.readConversation(conversationId)
  }

  async readConversation(conversationId: string): Promise<void> {
    const opened = await this.client.call('conversation.open', { conversation_id: conversationId })
    this.workspace = opened.folder || this.workspace
    this.client.watch(conversationId, opened.seq)
    this.$threads.setKey(conversationId, fromHistory(opened.messages, opened.running))
    this.$autoMode.setKey(conversationId, opened.auto_mode)
    void this.readModel(conversationId).catch(() => undefined)
    void this.readSubagents(conversationId).catch(() => undefined)
    void this.readCommands(conversationId).catch(() => undefined)
  }

  // -- tabs ---------------------------------------------------------------------------------------

  /** What the screen shows that a tab holds (store/tabs.ts). */
  place(): null | TabPlace {
    return screenPlace(this.$view.get(), this.$selected.get(), this.$openSession.get())
  }

  /** The tab on screen; `null` for a new conversation, a new session or a page. */
  activeTab(): null | Tab {
    return this.tabs.find(this.place())
  }

  /** What the lists call a tab's conversation, a bot's, or its session now. */
  titleOf(place: TabPlace): string | undefined {
    if (place.kind === 'session') {
      return place.session.title || undefined
    }

    const { conversationId } = place

    return this.botOf(conversationId)?.title ?? this.$conversations.get().find(row => row.id === conversationId)?.title
  }

  /** Show what a tab holds; a tab whose conversation was deleted since closes. A session's tab
   * takes the side panel to its executor, so its plan usage is its account's (asked for by the
   * user on 27/10/2026, where the panel kept the one chosen in the sidebar). */
  async showTab(id: string): Promise<void> {
    const tab = this.tabs.$tabs.get().find(open => open.id === id)

    if (!tab) {
      return
    }

    try {
      if (tab.place.kind === 'conversation') {
        await this.selectConversation(tab.place.conversationId)
      } else {
        const { session } = tab.place
        this.followExecutor(session.executor)
        await this.openSession({
          elsewhere: session.elsewhere,
          executor: session.executor,
          folder: session.folder,
          instance_id: session.instanceId,
          session_id: session.sessionId,
          title: session.title
        })
      }
    } catch (error) {
      if (!notFound(error)) {
        throw error
      }

      this.closeTab(id)
    }
  }

  /** Close a tab: the one on screen gives way to the next tab, else the one before, else a new
   * conversation. */
  closeTab(id: string): void {
    const shown = this.activeTab()
    const next = this.tabs.close(id)

    if (shown?.id === id) {
      if (next) {
        void this.showTab(next.id).catch(() => undefined)
      } else {
        this.newConversation()
      }
    }
  }

  /** The passing tab stays: a double click on it. */
  keepTab(id: string): void {
    const tab = this.tabs.$tabs.get().find(open => open.id === id)

    if (tab) {
      this.tabs.keep(tab.place)
    }
  }

  /** Show where the first card away from the screen waits. */
  async openWaiting(): Promise<void> {
    const [card] = away(this.$cards.get(), this.$view.get(), this.$selected.get(), this.$openSession.get())

    if (card?.kind === 'executor-approval' || card?.kind === 'executor-question') {
      this.openInstance(card.params.instance_id)
    } else if (card) {
      await this.selectConversation(card.conversationId)
    }
  }

  /** Once the home said its name, its tabs come back as the window kept them, and the one that was
   * on screen shows again, unless the user already chose what to see. */
  restoreTabs(): void {
    if (this.tabsHome !== null || this.name === null) {
      return
    }

    this.tabsHome = `${machineOf(this.key) ?? 'here'}/${this.name}`
    const shown = this.tabs.restore(readTabs(this.tabsHome))

    if (shown && this.$view.get() === 'conversation' && this.$selected.get() === null) {
      void this.showTab(shown.id).catch(() => undefined)
    }

    this.saveTabs()
  }

  private saveTabs(): void {
    if (this.tabsHome !== null) {
      writeTabs(this.tabsHome, this.tabs.saved(this.place()))
    }
  }

  async readCommands(conversationId: string): Promise<void> {
    const { commands } = await this.client.call('command.list', { conversation_id: conversationId })
    this.$commands.setKey(conversationId, commands)
  }

  /** The end of a background command's output, for its row on the agent map. */
  async commandOutput(commandId: string): Promise<CommandOutput> {
    const conversationId = this.$selected.get()

    if (!conversationId) {
      return { found: false, text: '' }
    }

    return await this.client.call('command.output', { command_id: commandId, conversation_id: conversationId })
  }

  /** Stop a background command with everything it started; the end of its output comes back. */
  async stopCommand(commandId: string): Promise<CommandStopped> {
    const conversationId = this.$selected.get()

    if (!conversationId) {
      return { stopped: false, text: '' }
    }

    return await this.client.call('command.stop', { command_id: commandId, conversation_id: conversationId })
  }

  async readSubagents(conversationId: string): Promise<void> {
    const { subagents } = await this.client.call('subagent.list', { conversation_id: conversationId })
    this.$subagents.setKey(conversationId, subagents)
  }

  /** Correct a running subagent's course; it enters after its current tools. */
  async steerSubagent(subagentId: string, message: string): Promise<boolean> {
    const conversationId = this.$selected.get()

    if (!conversationId) {
      return false
    }

    const result = await this.client.call('subagent.steer', {
      conversation_id: conversationId,
      message,
      subagent_id: subagentId
    })

    return result.accepted
  }

  async refreshWorkbench(): Promise<void> {
    await this.reading('workbench', async () => {
      // Its open requests come back as cards through the channel, as a conversation's do.
      const { executors, instances } = await this.client.call('workbench.list', {})
      this.$workbench.set({ executors, instances: instances.filter(row => row.state !== 'ended') })
      this.$read.setKey('workbench', true)
    })
  }

  /** The executors alone, read again when the plugins changed: the instances are the events'. A
   * list read while an instance starts or switches can answer after its event, and putting that
   * list back made a working session read as idle (found by the CI on 18/10/2026, once the tab
   * began to look for the harnesses as it shows). */
  async refreshExecutors(): Promise<void> {
    const { executors } = await this.client.call('workbench.list', {})
    this.$workbench.set({ ...this.$workbench.get(), executors })
  }

  /** The server looks for the harnesses again: a Claude Code installed, updated or logged in
   * while Atlas runs is found. When the executors tab shows, at most once a minute; on "Look
   * again", always. */
  async detectExecutors(force: boolean): Promise<void> {
    if ((!force && Date.now() - this.detectedAt < DETECT_EVERY_MS) || this.$detecting.get()) {
      return
    }

    this.detectedAt = Date.now()
    this.$detecting.set(true)

    try {
      const { executors } = await this.client.call('executor.detect', {})
      this.$workbench.set({ ...this.$workbench.get(), executors })
    } finally {
      this.$detecting.set(false)
    }
  }

  /** Something asked of the workbench; what the server refuses is said on the card (`key`). */
  async onWorkbench<T>(key: string, act: (client: AtlasClient) => Promise<T>): Promise<T | undefined> {
    this.$workbenchError.setKey(key, undefined)

    try {
      return await act(this.client)
    } catch (error) {
      this.$workbenchError.setKey(key, error instanceof Error ? error.message : String(error))

      return undefined
    }
  }

  private applyInstance(row: InstanceRow): void {
    const { executors, instances } = this.$workbench.get()
    const others = instances.filter(found => found.instance_id !== row.instance_id)
    const kept = instances.some(found => found.instance_id === row.instance_id)
      ? instances.map(found => (found.instance_id === row.instance_id ? row : found))
      : [...others, row]
    this.$workbench.set({
      executors,
      instances: row.state === 'ended' ? others : kept
    })
  }

  // -- the executors tab (step 16f) --------------------------------------------------------------

  /** An executor's folders, read by its plugin without their sessions. `again`, something changed
   * since a read running now began: one more read follows it. A folder something works in keeps
   * the time it had, so its tools' writes do not move it in the list. */
  refreshFolders(executor: string, again = false): Promise<void> {
    return this.readOnce(`folders\n${executor}`, again, async () => {
      const before = this.$sessionFolders.get()[executor]?.folders ?? null
      this.$sessionFolders.setKey(executor, { error: null, folders: before, reading: true })

      try {
        const { folders } = await this.client.call('executor.folders', { executor })
        const held = folders.map(row => {
          const kept = before?.find(found => found.folder === row.folder)

          return kept && this.works(instance => instance.executor === executor && instance.folder === row.folder)
            ? { ...row, updated_at: kept.updated_at }
            : row
        })
        this.$sessionFolders.setKey(executor, { error: null, folders: held, reading: false })
      } catch (error) {
        this.$sessionFolders.setKey(executor, {
          error: error instanceof Error ? error.message : String(error),
          folders: before,
          reading: false
        })
      }
    })
  }

  /** One folder's sessions, wherever they began, read by its plugin. `again`, as for the
   * folders; a session that works keeps its time as they do. */
  refreshSessions(executor: string, folder: string, again = false): Promise<void> {
    const key = folderKey(executor, folder)

    return this.readOnce(`sessions\n${key}`, again, async () => {
      const before = this.$sessions.get()[key]
      const kept = before?.sessions ?? null
      this.$sessions.setKey(key, { error: null, reading: true, sessions: kept, stale: false })

      try {
        const { sessions: read } = await this.client.call('executor.sessions', { executor, folder })
        const sessions = read.map(row => {
          const held = kept?.find(found => found.session_id !== '' && found.session_id === row.session_id)

          return held && row.instance_id && this.works(instance => instance.instance_id === row.instance_id)
            ? { ...row, updated_at: held.updated_at }
            : row
        })
        this.$sessions.setKey(key, { error: null, reading: false, sessions, stale: false })
        this.followListed(executor, sessions)
      } catch (error) {
        const stale = before?.stale ?? false
        this.$sessions.setKey(key, {
          error: error instanceof Error ? error.message : String(error),
          reading: false,
          sessions: kept,
          stale
        })
      }
    })
  }

  /** A folder's sessions as last read, or read now: the first time it opens, after it went stale,
   * or after a read failed. */
  loadSessions(executor: string, folder: string): Promise<void> {
    const read = this.$sessions.get()[folderKey(executor, folder)]

    return read?.sessions && !read.stale && !read.error ? Promise.resolve() : this.refreshSessions(executor, folder)
  }

  /** Open a folder in the list, its sessions read if they were not; or close it, its sessions
   * kept. */
  toggleFolder(executor: string, folder: string): void {
    const key = folderKey(executor, folder)

    if (this.$openFolders.get()[key]) {
      this.$openFolders.setKey(key, undefined)
    } else {
      this.openFolder(executor, folder)
    }
  }

  openFolder(executor: string, folder: string): void {
    this.$openFolders.setKey(folderKey(executor, folder), true)
    void this.loadSessions(executor, folder)
  }

  /** An executor's list on screen: its folders read again, and the sessions of those open, the
   * last read shown meanwhile. */
  listExecutor(executor: string): void {
    void this.refreshFolders(executor)

    for (const folder of this.foldersOf(executor, this.$openFolders.get())) {
      void this.refreshSessions(executor, folder)
    }
  }

  /** The folders of `executor` among `keyed`'s keys. */
  private foldersOf(executor: string, keyed: Record<string, unknown>): string[] {
    const prefix = folderKey(executor, '')

    return Object.keys(keyed)
      .filter(key => key.startsWith(prefix))
      .map(key => key.slice(prefix.length))
  }

  /** One read of `key` at a time: a second ask waits for the one running, or, `again`, for one
   * more read after it. */
  private readOnce(key: string, again: boolean, read: () => Promise<void>): Promise<void> {
    const running = this.reads.get(key)

    if (running && !again) {
      return running
    }

    if (running) {
      const queued =
        this.readsAgain.get(key) ??
        running.then(() => {
          this.readsAgain.delete(key)

          return this.readOnce(key, false, read)
        })
      this.readsAgain.set(key, queued)

      return queued
    }

    const started = read().finally(() => {
      this.reads.delete(key)
    })
    this.reads.set(key, started)

    return started
  }

  /** The session on screen learns where else it is open, and its title once its harness kept it. */
  private followListed(executor: string, sessions: SessionRow[]): void {
    const open = this.$openSession.get()

    if (open?.executor === executor && open.sessionId) {
      const found = sessions.find(row => row.session_id === open.sessionId)

      if (found) {
        this.followOpen({
          ...open,
          elsewhere: open.instanceId ? '' : found.elsewhere,
          instanceId: open.instanceId || found.instance_id,
          title: found.title || open.title
        })
      }
    }
  }

  /** The session on screen learned more of itself (its ids as they come, its title, another
   * account): its tab follows it. */
  private followOpen(next: OpenSession): void {
    const before = this.$openSession.get()

    if (before) {
      this.tabs.follow(before, next)
    }

    this.$openSession.set(next)

    // Gone on to another harness by its route: the box offers that one's models.
    if (next.executor !== before?.executor && this.$executorModels.get()[next.executor] === undefined) {
      void this.refreshExecutorModels(next.executor).catch(() => undefined)
    }
  }

  /** Show a session, in its tab or the passing one: what its harness kept, read by its plugin;
   * nothing is copied into Atlas. One shown before shows its trail at once, while it is read anew. */
  async openSession(row: SessionShown): Promise<void> {
    const live = row.instance_id && this.instance(row.instance_id) ? row.instance_id : ''

    if (row.instance_id) {
      this.$marks.setKey(instanceKey(row.instance_id), undefined)
    }

    const before = this.$openSession.get()
    const next: OpenSession = {
      choice: null,
      elsewhere: live ? '' : row.elsewhere,
      executor: row.executor,
      expired: false,
      folder: row.folder,
      instanceId: live,
      sessionId: row.session_id,
      title: row.title
    }

    if (before && named(before)) {
      this.sessionThreads.set(sessionOwner(before), this.$sessionThread.get())
    }

    if (named(next)) {
      this.tabs.show({ kind: 'session', session: next }, next.title)
    }

    this.$openSession.set(next)
    this.$sessionError.set(null)
    this.$sessionSettings.set(NO_SESSION_SETTINGS)
    this.$sessionThread.set((named(next) ? this.sessionThreads.get(sessionOwner(next)) : undefined) ?? EMPTY_THREAD)
    this.$view.set('session')
    // Its folder opens in the list, so the session on screen shows where it belongs.
    this.openFolder(row.executor, row.folder)

    if (this.$executorModels.get()[row.executor] === undefined) {
      void this.refreshExecutorModels(row.executor).catch(() => undefined)
    }

    if (row.session_id) {
      await this.readSession()
    }
  }

  /** The models an executor offers its account now, and the efforts of each. */
  async refreshExecutorModels(executor: string): Promise<void> {
    const { models } = await this.client.call('executor.models', { executor })
    this.$executorModels.setKey(executor, models)
  }

  /** The MCP servers an executor has for its account and the state of each: a Claude Code started
   * and closed with no prompt, which takes a few seconds. */
  async refreshExecutorConnectors(executor: string): Promise<void> {
    const before = this.$executorConnectors.get()[executor]
    this.$executorConnectors.setKey(executor, {
      connectors: before?.connectors ?? [],
      error: null,
      listed: before?.listed ?? true,
      reading: true
    })

    try {
      const { connectors, listed } = await this.client.call('executor.connectors', { executor })
      this.$executorConnectors.setKey(executor, { connectors, error: null, listed, reading: false })
    } catch (error) {
      this.$executorConnectors.setKey(executor, {
        connectors: before?.connectors ?? [],
        error: error instanceof Error ? error.message : String(error),
        listed: before?.listed ?? true,
        reading: false
      })
    }
  }

  /** The slash commands an executor takes in `folder`, described: what its instance there said,
   * or a harness started there with no prompt. Read once per folder, and again when the names its
   * instance says change (`names`). */
  async readExecutorCommands(executor: string, folder: string, instanceId: string, names: string): Promise<void> {
    const key = slashKey(executor, folder)

    if (this.slashReads.get(key) === names) {
      return
    }

    this.slashReads.set(key, names)

    try {
      const { commands } = await this.client.call('executor.commands', {
        executor,
        folder,
        instance_id: instanceId
      })
      this.$slashCommands.setKey(key, commands)
    } catch {
      // The box offers the names its card says meanwhile; the next change reads again.
      this.slashReads.delete(key)
    }
  }

  /** The skills and commands an executor takes in `folder`, for the side panel: read with no
   * session (a harness started there and closed at once), and again when asked. */
  async refreshExecutorCommands(executor: string, folder: string): Promise<void> {
    const key = slashKey(executor, folder)
    this.$commandReads.setKey(key, { error: null, reading: true })

    try {
      const { commands } = await this.client.call('executor.commands', { executor, folder, instance_id: '' })
      this.$slashCommands.setKey(key, commands)
      this.$commandReads.setKey(key, { error: null, reading: false })
    } catch (error) {
      this.$commandReads.setKey(key, { error: error instanceof Error ? error.message : String(error), reading: false })
    }
  }

  /** What the box of a conversation of Atlas's or a bot's offers as `/` is typed
   * (`conversation.commands`). Read as the conversation comes on screen and after each turn, which
   * may have made a skill. */
  async readAtlasCommands(conversationId: null | string): Promise<void> {
    try {
      const { commands } = await this.client.call('conversation.commands', {
        conversation_id: conversationId ?? ''
      })
      this.$atlasCommands.setKey(conversationId ?? DRAFT, commands)
    } catch {
      // The box offers what it had; the next read tries again.
    }
  }

  /** How much of its plan's limits the account an executor runs on has used: a Claude Code
   * started and closed with no prompt, which takes a few seconds. The answer is told to every
   * screen too, as `executor.usage`. */
  async refreshExecutorUsage(executor: string): Promise<void> {
    this.$executorUsage.setKey(executor, usageReading(this.$executorUsage.get()[executor]))

    try {
      const usage = await this.client.call('executor.usage', { executor })
      this.$executorUsage.setKey(executor, { ...usageTold(undefined, usage), reading: false })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.$executorUsage.setKey(executor, usageFailed(this.$executorUsage.get()[executor], message))
    }
  }

  /** An executor's accounts, in the order they take over in at a plan's limit (the main one first
   * until the user orders them, step 16h), each with who it is logged in as.
   * What is refused is said under `accounts:<executor>`. */
  async refreshAccounts(executor: string): Promise<void> {
    const listed = await this.onWorkbench(`accounts:${executor}`, client =>
      client.call('executor.accounts', { executor })
    )

    if (listed) {
      this.$executorAccounts.setKey(executor, listed)
    }
  }

  /** Keep another account under a nickname, in a folder of its own; its row then logs it in. */
  async addAccount(executor: string, nickname: string): Promise<boolean> {
    const row = await this.onWorkbench(`accounts:${executor}`, client =>
      client.call('executor.add_account', { executor, nickname })
    )

    if (row) {
      await Promise.all([this.refreshAccounts(executor), this.refreshExecutors()])
    }

    return row !== undefined
  }

  /** Forget another account; its folder stays, with its login and its sessions. */
  async removeAccount(executor: string, account: string): Promise<boolean> {
    const done = await this.onWorkbench(`accounts:${executor}`, client =>
      client.call('executor.remove_account', { account_id: account, executor })
    )

    if (done) {
      await Promise.all([this.refreshAccounts(executor), this.refreshExecutors()])
      void this.refreshFolders(executor)
    }

    return done !== undefined
  }

  /** The account a folder's new sessions start on (`''`, the main one). */
  async setFolderAccount(executor: string, folder: string, account: string): Promise<void> {
    const done = await this.onWorkbench(`folder-account:${executor}`, client =>
      client.call('executor.folder_account', { account_id: account, executor, folder })
    )

    if (done) {
      await this.refreshFolders(executor)
    }
  }

  /** What changes if the session on screen goes on on `account` (`''`, the main one), for the user
   * to read before switching; `undefined` with no instance running on it, whose next one simply
   * starts on that account. */
  async checkSessionAccount(account: string): Promise<AccountSwitchRow | undefined> {
    const open = this.$openSession.get()
    const instance = open ? this.instance(open.instanceId) : undefined

    if (!instance || instance.state === 'ended' || instance.state === 'failed') {
      this.$sessionSettings.set({ ...this.$sessionSettings.get(), account })

      return undefined
    }

    return this.onSession(client =>
      client.call('workbench.account_switch', { account_id: account, instance_id: instance.instance_id })
    )
  }

  /** The session on screen goes on on `account`, in a copy there (decided with the user on
   * 19/10/2026): its card stays, and the screen follows it to that account's executor. */
  async switchSessionAccount(account: string, note = ''): Promise<void> {
    const open = this.$openSession.get()
    const instance = open ? this.instance(open.instanceId) : undefined

    if (!instance) {
      return
    }

    const row = await this.onSession(client =>
      client.call('workbench.use_account', { account_id: account, instance_id: instance.instance_id })
    )

    if (row) {
      if (note) {
        // What its first message there reads again, said in the session where it switched.
        const thread = this.$sessionThread.get()
        const notes = [...(this.sessionNotes.get(row.instance_id) ?? []), { at: thread.items.length, text: note }]
        this.sessionNotes.set(row.instance_id, notes)
        const said = { key: `n${String(notes.length - 1)}`, kind: 'status', text: note } as const
        this.$sessionThread.set({ ...thread, items: [...thread.items, said] })
      }

      this.applyInstance(row)
      this.followInstance(row, instance)
    }
  }

  /** The route of the session on the instance `instanceId`: where it goes on at a plan's limit. */
  async readSessionRoute(instanceId: string): Promise<void> {
    if (this.$sessionRoute.get()?.instance_id !== instanceId) {
      this.$sessionRoute.set(null)
    }

    const row = await this.onSession(client => client.call('workbench.route', { instance_id: instanceId }))

    if (row) {
      this.$sessionRoute.set(row)
    }
  }

  /** The session's route, in order, each stop with the model and the effort it goes on with,
   * whether it is followed and whether a copy compacts first: kept for its card, so the instances
   * that take its place follow it too. */
  async setSessionRoute(instanceId: string, route: SessionRouteSet): Promise<void> {
    const row = await this.onSession(client =>
      client.call('workbench.set_route', { ...route, instance_id: instanceId })
    )

    if (row) {
      this.$sessionRoute.set(row)
    }
  }

  /** Delete one session on its account, after the user said yes (asked for by the user on
   * 19/10/2026); the list is read again, and a session on screen leaves it. */
  async deleteSession(row: SessionRow): Promise<boolean> {
    const done = await this.onWorkbench(`delete:${row.executor}`, client =>
      client.call('executor.delete_session', {
        executor: row.executor,
        folder: row.folder,
        session_id: row.session_id
      })
    )

    if (done) {
      const tab = this.tabs.$tabs
        .get()
        .find(
          ({ place }) =>
            place.kind === 'session' &&
            place.session.executor === row.executor &&
            place.session.sessionId === row.session_id
        )

      // Its tab closes, the next one showing; a session on screen with no tab leaves it.
      if (tab) {
        this.closeTab(tab.id)
      }

      if (this.$view.get() === 'session' && this.$openSession.get()?.sessionId === row.session_id) {
        this.newSession(row.executor, row.folder)
      }

      await this.refreshSessions(row.executor, row.folder, true)
    }

    return done !== undefined
  }

  /** Read an executor's plan usage when the panel shows it and the last read is old. */
  readUsageIfStale(executor: string): void {
    if (usageStale(this.$executorUsage.get()[executor], Date.now())) {
      void this.refreshExecutorUsage(executor)
    }
  }

  /** The side panel's executor becomes `executor`, its tab left as it is: what it has is read the
   * first time, its plan usage when the last read is old. */
  followExecutor(executor: string): void {
    this.$openExecutor.set(executor)
    // An account's row is its executor's on that account: the accounts are its executor's.
    const row = this.$workbench.get().executors.find(found => found.name === executor)
    void this.refreshAccounts(row?.base || executor)

    if (this.$executorConnectors.get()[executor] === undefined) {
      void this.refreshExecutorConnectors(executor)
    }

    this.readUsageIfStale(executor)
  }

  /** A new session of `executor` in `folder`, not started yet: its first prompt starts it. */
  newSession(executor: string, folder: string): void {
    void this.openSession({ elsewhere: '', executor, folder, instance_id: '', session_id: '', title: '' })
  }

  /** Read the session on screen again, as its harness keeps it now. As its turn ends (`ended`), the
   * read is taken only once the harness kept the last words on screen, tried again a few times. */
  async readSession(ended = false): Promise<void> {
    const open = this.$openSession.get()

    if (!open?.sessionId) {
      return
    }

    for (let attempt = 1; ; attempt += 1) {
      const read = await this.client.call('executor.session', {
        executor: open.executor,
        folder: open.folder,
        session_id: open.sessionId
      })
      const now = this.$openSession.get()

      // Another session opened meanwhile: this one's text is not its.
      if (now?.sessionId !== open.sessionId) {
        return
      }

      if (ended) {
        const thread = this.$sessionThread.get()

        // A new turn began meanwhile: its live events are newer than this read.
        if (thread.running) {
          return
        }

        if (attempt < CATCH_UP_TRIES && !caughtUp(read.entries, thread)) {
          await new Promise(resolve => setTimeout(resolve, CATCH_UP_MS))
          continue
        }
      }

      const working = this.instance(now.instanceId)?.state === 'working'
      const notes = this.sessionNotes.get(now.instanceId) ?? []
      this.$sessionThread.set(withNotes(fromTranscript(read.entries, working), notes))

      if (read.expired) {
        this.followOpen({ ...now, expired: true })
      }

      return
    }
  }

  /** What the user chose for a session open in another program. */
  chooseElsewhere(choice: Elsewhere): void {
    const open = this.$openSession.get()

    if (open) {
      this.followOpen({ ...open, choice })
    }
  }

  /** A prompt from the session's box: it starts an instance on the session when none runs (in a
   * copy, when the user chose one), then a turn, or a steer into the one running. */
  async sendToSession(text: string, attachments: AttachmentRow[] = []): Promise<boolean> {
    const open = this.$openSession.get()
    const trimmed = text.trim()

    if (!open || (!trimmed && !attachments.length)) {
      return false
    }

    this.$sessionError.set(null)

    try {
      let instanceId = this.instance(open.instanceId) ? open.instanceId : ''

      if (!instanceId) {
        const settings = this.$sessionSettings.get()
        const started = await this.client.call('workbench.start', {
          account_id: settings.account,
          auto: settings.auto,
          effort: settings.effort,
          executor: open.executor,
          folder: open.folder,
          fork: open.choice === 'copy',
          model: settings.model,
          session: open.sessionId || null
        })
        instanceId = started.instance_id
        this.applyInstance(started)
        const now = this.$openSession.get() ?? open
        this.followOpen({ ...now, elsewhere: '', instanceId })

        if (started.problem) {
          this.$sessionError.set(started.problem)

          return false
        }

        if (settings.atlasAnswers) {
          await this.client.call('workbench.atlas_answers', { enabled: true, instance_id: instanceId })
        }
      }

      await this.client.call('workbench.prompt', {
        attachments: attachments.map(attachment => attachment.id),
        instance_id: instanceId,
        text: trimmed
      })
      this.used(open.executor, open.folder, instanceId)
      // Used, its tab stays.
      const shown = this.$openSession.get()

      if (shown) {
        this.tabs.keep({ kind: 'session', session: shown })
      }

      return true
    } catch (error) {
      this.$sessionError.set(error instanceof Error ? error.message : String(error))

      return false
    }
  }

  /** Stop the instance on the session on screen, for good; the session stays, to go on later. */
  async stopSession(): Promise<void> {
    const open = this.$openSession.get()

    if (open?.instanceId) {
      await this.onSession(client => client.call('workbench.stop', { instance_id: open.instanceId }))
    }
  }

  /** A switch under the session's box: on its instance when one runs, kept for the next one when
   * none does. */
  async setSessionSwitch(which: SessionSwitch, enabled: boolean): Promise<void> {
    const open = this.$openSession.get()
    const instance = open ? this.instance(open.instanceId) : undefined

    if (!instance) {
      this.$sessionSettings.set({ ...this.$sessionSettings.get(), [which]: enabled })

      return
    }

    const method = which === 'auto' ? 'workbench.auto' : 'workbench.atlas_answers'
    const row = await this.onSession(client => client.call(method, { enabled, instance_id: instance.instance_id }))

    if (row) {
      this.applyInstance(row)
    }
  }

  /** The model and the effort under the session's box (`null`, the executor's default): on its
   * instance from its next request when one runs, kept for the next one when none does. */
  async setSessionModel(model: null | string, effort: null | string): Promise<void> {
    const open = this.$openSession.get()
    const instance = open ? this.instance(open.instanceId) : undefined

    if (!instance) {
      this.$sessionSettings.set({ ...this.$sessionSettings.get(), effort, model })

      return
    }

    const row = await this.onSession(client =>
      client.call('workbench.model', { effort, instance_id: instance.instance_id, model })
    )

    if (row) {
      this.applyInstance(row)
    }
  }

  /** Log an executor's harness into another account, from its place in the sidebar (asked for by
   * the user on 18/10/2026); its sessions open here go on with it. What is refused is said there,
   * under `account:<executor>`. */
  async switchExecutorAccount(executor: string, login: string): Promise<boolean> {
    const row = await this.onWorkbench(`account:${executor}`, client =>
      client.call('executor.account', { executor, login: login.trim() })
    )

    if (row) {
      const { executors, instances } = this.$workbench.get()
      this.$workbench.set({ executors: executors.map(found => (found.name === executor ? row : found)), instances })
      // Another account may be offered other models, and has a plan of its own.
      void this.refreshExecutorModels(executor).catch(() => undefined)
      void this.refreshExecutorUsage(executor)
    }

    return row !== undefined
  }

  /** The executors tab on the session an instance works on (a notification's click). */
  openInstance(instanceId: string): void {
    const found = this.instance(instanceId)

    if (!found) {
      return
    }

    $sidebarTab.set('executors')
    this.$openExecutor.set(found.executor)
    this.$openExecutors.setKey(found.executor, true)
    void this.refreshFolders(found.executor)
    void this.refreshSessions(found.executor, found.folder)
    void this.openSession(sessionOf(found))
  }

  /** The session an outside executor's task of this conversation works in (its delegate line). */
  async openDelegated(conversationId: string, subagentId: string): Promise<boolean> {
    const found = await this.client.call('subagent.session', {
      conversation_id: conversationId,
      subagent_id: subagentId
    })

    if (!found.found) {
      return false
    }

    $sidebarTab.set('executors')
    this.$openExecutor.set(found.executor)
    this.$openExecutors.setKey(found.executor, true)
    void this.refreshFolders(found.executor)
    await this.refreshSessions(found.executor, found.folder)
    const read = this.$sessions.get()[folderKey(found.executor, found.folder)]
    const listed = read?.sessions?.find(row => row.session_id === found.session_id)
    await this.openSession(
      listed ?? {
        by_atlas: true,
        elsewhere: '',
        executor: found.executor,
        folder: found.folder,
        instance_id: '',
        session_id: found.session_id,
        started_at: '',
        state: '',
        title: '',
        updated_at: '',
        waiting: 0
      }
    )

    return true
  }

  /** Whether an instance on the workbench works, of those `which` takes. */
  private works(which: (instance: InstanceRow) => boolean): boolean {
    return this.$workbench.get().instances.some(instance => instance.state === 'working' && which(instance))
  }

  /** A session goes first in its folder, and its folder in its executor's list: the user sent it a
   * message, or its answer came (asked for by the user on 22/10/2026, where any of its work moved
   * it; what it asks moves nothing). */
  private used(executor: string, folder: string, instanceId: string): void {
    const now = Date.now()
    this.$used.setKey(folderKey(executor, folder), now)
    this.$used.setKey(instanceKey(instanceId), now)
  }

  /** An instance on the workbench now, not ended. */
  instance(instanceId: string): InstanceRow | undefined {
    return instanceId
      ? this.$workbench.get().instances.find(row => row.instance_id === instanceId && row.state !== 'ended')
      : undefined
  }

  private async onSession<T>(act: (client: AtlasClient) => Promise<T>): Promise<T | undefined> {
    this.$sessionError.set(null)

    try {
      return await act(this.client)
    } catch (error) {
      this.$sessionError.set(error instanceof Error ? error.message : String(error))

      return undefined
    }
  }

  /** An instance changed: the session on screen follows it (its id once its harness says it, a
   * turn that ended read again as its harness kept it), and its executor's list is read again. */
  private followInstance(row: InstanceRow, before: InstanceRow | undefined): void {
    const open = this.$openSession.get()
    const mine =
      open !== null &&
      (open.instanceId === row.instance_id ||
        (!open.instanceId && open.sessionId !== '' && open.sessionId === row.session_id && row.state !== 'ended'))

    if (open && mine) {
      // A switch of account moves it to that account's executor (step 16h).
      const sessionId = row.session_id || (row.executor === open.executor ? open.sessionId : '')
      this.followOpen({ ...open, elsewhere: '', executor: row.executor, instanceId: row.instance_id, sessionId })
      const ended = before?.state === 'working' && row.state !== 'working'

      if (row.state === 'working' && !this.$sessionThread.get().running) {
        this.$sessionThread.set({ ...this.$sessionThread.get(), running: true })
      } else if (ended || row.state === 'ended' || row.state === 'failed') {
        this.$sessionThread.set(turnEnded(this.$sessionThread.get()))

        if (ended && sessionId) {
          void this.readSession(true).catch(() => undefined)
        }
      }
    }

    if (this.$sessionFolders.get()[row.executor] !== undefined && !this.sessionsDue.has(row.executor)) {
      this.sessionsDue.add(row.executor)
      setTimeout(() => {
        this.sessionsDue.delete(row.executor)
        this.followFolders(row.executor)
      }, 300)
    }
  }

  /** An executor's instances changed: its folders are read again, and so are the sessions of the
   * folders open; a closed one's are read again only when it opens. */
  private followFolders(executor: string): void {
    void this.refreshFolders(executor, true)
    const reads = this.$sessions.get()
    const open = this.$openFolders.get()

    for (const folder of this.foldersOf(executor, reads)) {
      const key = folderKey(executor, folder)
      const read = reads[key]

      if (open[key] || read?.reading) {
        void this.refreshSessions(executor, folder, true)
      } else if (read) {
        this.$sessions.setKey(key, { ...read, stale: true })
      }
    }
  }

  /** Stop a running subagent; what it had still reaches the conversation with its batch. */
  async stopSubagent(subagentId: string): Promise<void> {
    const conversationId = this.$selected.get()

    if (conversationId) {
      await this.client.call('subagent.interrupt', { conversation_id: conversationId, subagent_id: subagentId })
    }
  }

  async refreshBots(): Promise<void> {
    await this.reading('bots', async () => {
      const { bots } = await this.client.call('bot.list', {})
      this.$bots.set(bots)
      this.$read.setKey('bots', true)
      this.readLive(bots.flatMap(bot => (bot.conversation_id ? [[bot.conversation_id, bot.running] as const] : [])))
    })
  }

  /** Which conversations run, as their rows were read: one told of since is left as told. */
  private readLive(rows: (readonly [string, boolean])[]): void {
    for (const [conversationId, running] of rows) {
      if (!this.heard.has(conversationId)) {
        this.$live.setKey(conversationId, running ? true : undefined)
      }
    }
  }

  /** A turn began or ended in a conversation, attached here or not: its row works, or keeps what
   * it ended with while it is not on screen. */
  private turnChanged(conversationId: string, status: string): void {
    this.heard.add(conversationId)

    if (status === 'started') {
      this.$live.setKey(conversationId, true)
      this.$marks.setKey(conversationId, undefined)
      this.$used.setKey(conversationId, Date.now())
    } else {
      this.$live.setKey(conversationId, undefined)
      const mark = ended(status)
      const seen =
        $session.get() === this && this.$view.get() === 'conversation' && this.$selected.get() === conversationId

      if (mark && !seen) {
        this.$marks.setKey(conversationId, mark)
      }
    }

    if (this.botOf(conversationId)) {
      void this.refreshBots().catch(() => undefined)
    }
  }

  /** An instance began to work, or stopped: its answer puts its folder and session first, and a
   * session not on screen that stopped keeps that until it opens. */
  private instanceChanged(row: InstanceRow, before: InstanceRow | undefined): void {
    const key = instanceKey(row.instance_id)

    if (row.state === 'working' && before?.state !== 'working') {
      this.$marks.setKey(key, undefined)
    } else if (row.state === 'ended') {
      this.$marks.setKey(key, undefined)
    } else if (before?.state === 'working' && row.state === 'idle') {
      this.used(row.executor, row.folder, row.instance_id)
      const seen =
        $session.get() === this &&
        this.$view.get() === 'session' &&
        this.$openSession.get()?.instanceId === row.instance_id

      if (!seen) {
        this.$marks.setKey(key, 'unread')
      }
    }
  }

  async refreshPicture(): Promise<void> {
    const { png } = await this.client.call('home.picture', {})
    this.$picture.set(png === null ? null : `data:image/png;base64,${png}`)
  }

  /** Keep a square PNG (base64) as the home's picture; `null` takes it away. */
  async setPicture(png: null | string): Promise<void> {
    this.$pictureError.set(null)

    try {
      const kept = await this.client.call('home.set_picture', { png })
      this.$picture.set(kept.png === null ? null : `data:image/png;base64,${kept.png}`)
    } catch (error) {
      this.$pictureError.set(error instanceof Error ? error.message : String(error))
    }
  }

  /** Open a bot's fixed conversation, which the server makes the first time. */
  async openBot(name: string): Promise<void> {
    const { conversation_id: conversationId } = await this.client.call('bot.open', { name })
    await this.selectConversation(conversationId)
    await this.refreshBots()
  }

  /** Make a bot, or write its definition anew; a new one opens. */
  async saveBot(definition: BotDefinition, creating: boolean): Promise<void> {
    await this.client.call(creating ? 'bot.create' : 'bot.update', definition)
    await this.refreshBots()

    if (creating) {
      await this.openBot(definition.name)
    }
  }

  /** Delete a bot; its conversation leaves the screen with it, and its tab closes. */
  async deleteBot(name: string): Promise<void> {
    const fixed = this.$bots.get().find(bot => bot.name === name)?.conversation_id
    await this.client.call('bot.delete', { name })
    const tab = fixed ? this.tabs.find({ conversationId: fixed, kind: 'conversation' }) : null

    if (tab) {
      this.closeTab(tab.id)
    }

    if (fixed && this.$selected.get() === fixed) {
      this.newConversation()
    }

    await this.refreshBots()
    await this.refreshRoutines()
  }

  /** The bot whose fixed conversation this is, if it is one. */
  botOf(conversationId: null | string): BotRow | undefined {
    return conversationId ? this.$bots.get().find(bot => bot.conversation_id === conversationId) : undefined
  }

  async refreshRoutines(): Promise<void> {
    await this.reading('routines', async () => {
      const { routines } = await this.client.call('routine.list', {})
      this.$routines.set(routines)
    })
  }

  /** Change the marketplace's screen state. */
  market(change: Partial<Market>): void {
    this.$market.set({ ...this.$market.get(), ...change })
  }

  /** Read the marketplace (step 16s); `refresh` reads it from GitHub now, not only once the last
   * read is an hour old. What was read stays on screen meanwhile. */
  async refreshMarketplace(refresh = false): Promise<void> {
    this.market({ error: null, reading: true })

    try {
      const list = await this.client.call('marketplace.list', { refresh })
      this.market({ list, page: withRow(this.$market.get().page, list), reading: false })
    } catch (error) {
      this.market({ error: error instanceof Error ? error.message : String(error), reading: false })
    }
  }

  /** Open an item's page: its README at its commit, and what it needs here. */
  async openMarketItem(name: string): Promise<void> {
    this.market({ brought: [], confirming: null, failed: null, open: name, page: null, pageError: null })
    await this.readMarketPage(name)
  }

  async readMarketPage(name: string): Promise<void> {
    try {
      const page = await this.client.call('marketplace.page', { name })

      if (this.$market.get().open === name) {
        this.market({ page, pageError: null })
      }
    } catch (error) {
      if (this.$market.get().open === name) {
        this.market({ pageError: error instanceof Error ? error.message : String(error) })
      }
    }
  }

  /** Install, update or remove an item; the list and its page are read again after. An update the
   * home changed since waits for the user's word (`confirming`), then comes again with `force`. */
  async actOnMarket(action: 'install' | 'remove' | 'update', name: string, force = false): Promise<void> {
    this.market({ brought: [], busy: name, confirming: null, failed: null })

    try {
      if (action === 'remove') {
        await this.client.call('marketplace.remove', { name })
      } else {
        const done =
          action === 'install'
            ? await this.client.call('marketplace.install', { name })
            : await this.client.call('marketplace.update', { force, name })
        this.market({
          brought: done.changed.filter(changed => changed !== name),
          confirming: done.local_changes ? name : null
        })
      }
    } catch (error) {
      this.market({ failed: error instanceof Error ? error.message : String(error) })
    } finally {
      this.market({ busy: null })
    }

    await this.refreshMarketplace()

    if (this.$market.get().open === name) {
      await this.readMarketPage(name)
    }
  }

  /** Store a secret an MCP server of the marketplace reads; its value is never read back. */
  async storeMarketSecret(name: string, secret: string, value: string): Promise<boolean> {
    try {
      const page = await this.client.call('marketplace.secret', { name, secret, value })
      this.market({ failed: null, page })
    } catch (error) {
      this.market({ failed: error instanceof Error ? error.message : String(error) })

      return false
    }

    await this.refreshMarketplace()

    return true
  }

  async refreshPlugins(): Promise<void> {
    const [{ plugins }, { pairings }] = await Promise.all([
      this.client.call('plugin.list', {}),
      this.client.call('pairing.list', {})
    ])
    this.$plugins.set(plugins)
    this.$pairings.set(pairings)
    // An older server has no screens to give.
    const { screens } = await this.client.call('plugin.screens', {}).catch(() => ({ screens: [] }))
    this.$screens.set(screens)
    // Likewise for themes: the look stays as it was.
    const themes = await this.client.call('plugin.themes', {}).catch(() => undefined)
    if (themes) {
      setPluginThemes(themes.themes)
    }
  }

  /** A change on the Plugins screen; what the server refuses is said there, and the screen is read
   * again either way. */
  async actOnPlugins(act: (client: AtlasClient) => Promise<unknown>): Promise<boolean> {
    this.$pluginError.set(null)
    let done = true

    try {
      await act(this.client)
    } catch (error) {
      this.$pluginError.set(error instanceof Error ? error.message : String(error))
      done = false
    }

    await this.refreshPlugins().catch(() => undefined)

    return done
  }

  async readRuns(routineId: string): Promise<void> {
    const { runs } = await this.client.call('routine.runs', { routine_id: routineId })
    this.$runs.setKey(routineId, runs)
  }

  /** Run now, pause, resume or remove a routine; the list is read again after. */
  async actOnRoutine(action: 'pause' | 'remove' | 'resume' | 'run', routineId: string): Promise<void> {
    await this.client.call(`routine.${action}`, { routine_id: routineId })
    await this.refreshRoutines()
  }

  async refreshModelOptions(): Promise<void> {
    this.$modelOptions.set(await this.client.call('model.options', {}))
  }

  async refreshKeyProviders(): Promise<void> {
    const { providers } = await this.client.call('provider.list', {})
    this.$keyProviders.set(providers)
  }

  /** Check a provider's key and store it unless the provider refuses it (docs/ARCHITECTURE.md, The
   * installer); a stored key makes the picker offer the provider at once. */
  async connectProvider(provider: string, key: string): Promise<ProviderConnected> {
    const found = await this.client.call('provider.connect', { key, provider })

    if (found.stored) {
      this.$keyNotice.set(found.status === 'valid' ? null : found)
      await Promise.all([this.refreshModelOptions(), this.refreshKeyProviders()])
    }

    return found
  }

  async readModel(conversationId: string): Promise<void> {
    this.$models.setKey(
      conversationId,
      await this.client.call('conversation.model', { conversation_id: conversationId })
    )
  }

  /** Pick the conversation's model and effort; it applies from its next turn (a
   * switch made while a turn runs waits). A pick with no conversation yet makes the conversation
   * first. The pick shows at once and goes back if the server refuses it, so a second pick made before the answer builds on the first. */
  async setModel(provider: string, model: string, effort: null | string): Promise<void> {
    const conversationId = this.$selected.get()
    this.$models.setKey(conversationId ?? DRAFT, { effort, model, picked: true, provider })

    if (conversationId === null) {
      return
    }

    try {
      const result = await this.client.call('conversation.model', {
        conversation_id: conversationId,
        effort,
        model,
        provider
      })
      this.$models.setKey(conversationId, result)
    } catch (error) {
      await this.readModel(conversationId).catch(() => undefined)
      throw error
    }
  }

  /** A new conversation on screen, not made yet: its first message makes it. */
  newConversation(): void {
    this.$selected.set(null)
    this.$view.set('conversation')
    this.$models.setKey(DRAFT, undefined)
    this.$autoMode.setKey(DRAFT, true)
  }

  /** Make the conversation on screen, with the model and "Auto" picked while it was a draft. */
  private async makeConversation(): Promise<string> {
    const { conversation_id: conversationId, folder } = await this.client.call('conversation.create', {})
    this.workspace = folder || this.workspace
    this.client.watch(conversationId, 0)
    this.$threads.setKey(conversationId, EMPTY_THREAD)
    const picked = this.$models.get()[DRAFT]
    const auto = this.$autoMode.get()[DRAFT] ?? true
    this.$models.setKey(DRAFT, undefined)
    this.$autoMode.setKey(DRAFT, true)

    // The server makes it in "Auto": a draft that turned it off turns it off.
    if (!auto) {
      await this.client.call('conversation.auto_mode', { conversation_id: conversationId, enabled: false })
    }

    this.$autoMode.setKey(conversationId, auto)

    if (picked?.picked && picked.provider && picked.model) {
      this.$models.setKey(
        conversationId,
        await this.client.call('conversation.model', {
          conversation_id: conversationId,
          effort: picked.effort,
          model: picked.model,
          provider: picked.provider
        })
      )
    } else {
      const fallback = this.$modelOptions.get()?.default

      if (fallback) {
        this.$models.setKey(conversationId, fallback)
      }
    }

    // A tab of its own, on screen unless the user went to another meanwhile.
    this.tabs.made({ conversationId, kind: 'conversation' }, '')

    if (this.$view.get() === 'conversation' && this.$selected.get() === null) {
      this.$selected.set(conversationId)
    }

    return conversationId
  }

  async refreshProposals(): Promise<void> {
    await this.reading('proposals', async () => {
      const { proposals } = await this.client.call('memory.proposals', {})
      this.$proposals.set(proposals)
    })
  }

  /**
   * Apply a proposal. Applied, its card stays to say what it overwrote or removed;
   * refused, the list read again says why it cannot apply.
   */
  async approveProposal(proposal: ProposalRow): Promise<void> {
    const result = await this.client.call('memory.approve', { proposal_id: proposal.id })

    if (result.applied) {
      this.$approved.setKey(proposal.id, { proposal, result })
    }

    await this.refreshProposals()

    if (!result.applied && result.error) {
      // The list may not say it yet (another client approved it meanwhile): the card shows this.
      this.$proposals.set(
        this.$proposals
          .get()
          .map(row => (row.id === proposal.id ? { ...row, problem: row.problem ?? result.error } : row))
      )
    }
  }

  async discardProposal(proposalId: string): Promise<void> {
    await this.client.call('memory.discard', { proposal_id: proposalId })
    await this.refreshProposals()
  }

  /** Start a turn, or steer the one running, with the files attached (step 16g). A first message
   * makes the conversation first. Its bubble shows at once, marked as on its way (`$sending`). */
  async send(text: string, attachments: AttachmentRow[] = []): Promise<void> {
    const selected = this.$selected.get()
    this.sendSeq += 1
    const sending: Sending = { attachments, key: `sending-${String(this.sendSeq)}`, state: 'sending', text }
    let owner = selected ?? DRAFT
    this.holdSending(owner, sending)

    try {
      const conversationId = selected ?? (await this.makeConversation())
      // Used, its tab stays.
      this.tabs.keep({ conversationId, kind: 'conversation' })

      if (conversationId !== owner) {
        this.dropSending(owner, sending.key)
        owner = conversationId
        this.holdSending(owner, sending)
      }

      await this.client.call('conversation.send', {
        attachments: attachments.map(row => row.id),
        conversation_id: conversationId,
        text
      })
    } catch (error) {
      this.dropSending(owner, sending.key)
      throw error
    }

    // The server has it: in a turn that runs, it waits to enter once the current step ends.
    if (this.$threads.get()[owner]?.running) {
      this.$sending.setKey(
        owner,
        this.$sending.get()[owner]?.map(row => (row.key === sending.key ? { ...row, state: 'queued' as const } : row))
      )
    }
  }

  private holdSending(owner: string, sending: Sending): void {
    this.$sending.setKey(owner, [...(this.$sending.get()[owner] ?? []), sending])
  }

  private dropSending(owner: string, key: string): void {
    const left = (this.$sending.get()[owner] ?? []).filter(row => row.key !== key)
    this.$sending.setKey(owner, left.length ? left : undefined)
  }

  /** The server has the user's words in the conversation: their bubble on its way gives way to the
   * conversation's own (the first with those words, else the oldest). */
  private arrived(conversationId: string, text: string): void {
    const held = this.$sending.get()[conversationId] ?? []
    const found = held.find(row => row.text === text) ?? held[0]

    if (found) {
      this.dropSending(conversationId, found.key)
    }
  }

  /**
   * A user command (step 16f): the ▶ on a shell block, or a message starting with `!`. It runs in
   * the side panel's terminal, in the folder of what is on screen, and what came of it goes back
   * there: to Atlas's conversation as a terminal row, which the agent answers; or to the executor's
   * session, whose harness answers it.
   */
  async runUserCommand(command: string): Promise<void> {
    const open = this.$view.get() === 'session' ? this.$openSession.get() : null

    if (open) {
      await runCommand(command, open.folder, async outcome => {
        await this.sendToSession(terminalText(outcome))
      })

      return
    }

    const conversationId = this.$selected.get() ?? (await this.makeConversation())

    if (!this.workspace) {
      await this.readConversation(conversationId)
    }

    await runCommand(command, this.workspace, async (outcome: CommandOutcome) => {
      await this.client.call('conversation.terminal', {
        command: outcome.command,
        conversation_id: conversationId,
        exit_code: outcome.exitCode,
        output: outcome.output,
        status: outcome.status
      })
    })
  }

  /** Whether the trust gate finds a command dangerous, before a ▶ runs it. */
  async checkCommand(command: string): Promise<{ dangerous: boolean; reason: string }> {
    return await this.client.call('trust.check', { command })
  }

  async interrupt(): Promise<void> {
    const conversationId = this.$selected.get()

    if (conversationId) {
      await this.client.call('conversation.interrupt', { conversation_id: conversationId })
    }
  }

  async setAutoMode(enabled: boolean): Promise<void> {
    const conversationId = this.$selected.get()

    if (conversationId === null) {
      this.$autoMode.setKey(DRAFT, enabled)

      return
    }

    const result = await this.client.call('conversation.auto_mode', { conversation_id: conversationId, enabled })
    this.$autoMode.setKey(conversationId, result.enabled)
  }

  /** Answer a card: it says the answer is on its way until the server says it has it
   * (`request.cancel`), never gone first as if it had arrived; with no connection, the server
   * delivers the request again once one opens, and the card takes an answer again (the UI review,
   * 25/10/2026). */
  answer(id: string, result: Record<string, unknown>): void {
    this.requests.get(id)?.respond(result)
    this.requests.delete(id)
    this.$cards.set(this.$cards.get().map(card => (card.id === id ? { ...card, answered: true } : card)))
  }

  private handleRequest(request: ServerRequest): boolean {
    const conversationId = typeof request.params.conversation_id === 'string' ? request.params.conversation_id : ''

    let card: Card

    if (request.method === 'ask_user') {
      card = { conversationId, id: request.id, kind: 'ask', params: request.params as unknown as AskParams }
    } else if (request.method === 'approval') {
      card = { conversationId, id: request.id, kind: 'approval', params: request.params as unknown as ApprovalParams }
    } else if (request.method === 'executor.approval') {
      const params = request.params as unknown as ExecutorApprovalParams
      card = { conversationId, id: request.id, kind: 'executor-approval', params }
    } else if (request.method === 'executor.question') {
      const params = request.params as unknown as ExecutorQuestionParams
      card = { conversationId, id: request.id, kind: 'executor-question', params }
    } else if (request.method === 'secret') {
      card = { conversationId, id: request.id, kind: 'secret', params: request.params as unknown as SecretParams }
    } else {
      return false
    }

    // A request delivered again after a reconnect keeps its id: the card answers the new socket.
    this.requests.set(request.id, request)
    this.$cards.set([...this.$cards.get().filter(open => open.id !== request.id), card])

    if (card.kind === 'executor-approval' || card.kind === 'executor-question') {
      // The workbench's card: on every screen, so only a window not in front is away.
      if (!request.replayed && ($session.get() !== this || !document.hasFocus())) {
        const title = t.notification.executor(card.params.executor_title)
        const instanceId = card.params.instance_id
        shell.notify(title, card.params.executor_title, () => {
          shell.focus()
          show(this)
          this.openInstance(instanceId)
        })
      }
    } else if (card.kind === 'secret') {
      // The key card: the window not in front says so, as an approval does (step 16d).
      if (!request.replayed && this.away(conversationId)) {
        notifyFor(this, conversationId, t.notification.secret(card.params.name))
      }
    } else if (!request.replayed && this.away(conversationId)) {
      notifyFor(this, conversationId, card.kind === 'ask' ? t.notification.asks : t.notification.approval)
    }

    return true
  }

  private handleEvent(event: EventParams): void {
    const conversationId = event.conversation_id

    // The plugins' screens hear the home on screen's events (step 16m).
    if ($session.get() === this) {
      emitPluginEvent(event)
    }

    if (event.type === 'request.cancel') {
      const cancel = event.payload as RequestCancel
      this.requests.delete(cancel.id)
      this.$cards.set(this.$cards.get().filter(card => card.id !== cancel.id))

      return
    }

    if (event.type === 'proposals.changed') {
      void this.refreshProposals().catch(() => undefined)

      return
    }

    if (event.type === 'bots.changed') {
      void this.refreshBots().catch(() => undefined)

      return
    }

    if (event.type === 'conversations.changed') {
      const changed = event.payload as ConversationsChanged

      if (changed.status) {
        this.turnChanged(changed.conversation_id, changed.status)
      }

      void this.refreshList().catch(() => undefined)

      return
    }

    if (event.type === 'plugins.changed') {
      void this.refreshPlugins().catch(() => undefined)
      // A plugin turned on or off changes which executors the workbench and the onboarding show;
      // the instances are kept by their own events.
      void this.refreshExecutors().catch(() => undefined)

      // And what the marketplace's card says of it, when the Plugins screen opened over that card.
      if (this.$view.get() === 'marketplace') {
        void this.refreshMarketplace()
      }

      return
    }

    if (event.type === 'routines.changed') {
      void this.refreshRoutines().catch(() => undefined)

      return
    }

    if (event.type === 'marketplace.changed') {
      // Read again only by a screen that read it once; another client or the terminal acted.
      const changed = event.payload as MarketplaceChanged
      const { list, open } = this.$market.get()

      if (list !== null) {
        void this.refreshMarketplace()
      }

      if (open !== null && (changed.name === '' || changed.name === open)) {
        void this.readMarketPage(open)
      }

      return
    }

    if (event.type === 'workbench.update') {
      const row = event.payload as InstanceRow
      const before = this.$workbench.get().instances.find(found => found.instance_id === row.instance_id)
      this.applyInstance(row)
      this.instanceChanged(row, before)
      this.followInstance(row, before)

      return
    }

    if (event.type === 'executor.usage') {
      const usage = event.payload as ExecutorUsageRow
      this.$executorUsage.setKey(usage.executor, usageTold(this.$executorUsage.get()[usage.executor], usage))

      return
    }

    if (event.type === 'executor.accounts') {
      const { executor } = event.payload as { executor: string }
      void this.refreshAccounts(executor)
      void this.refreshExecutors().catch(() => undefined)
      void this.refreshFolders(executor).catch(() => undefined)

      return
    }

    if (event.type === 'workbench.activity') {
      const activity = event.payload as InstanceActivityPayload

      if (activity.instance_id === this.$openSession.get()?.instanceId) {
        this.activitySeq += 1
        this.$sessionThread.set(applyActivity(this.$sessionThread.get(), activity, this.activitySeq))
      }

      return
    }

    if (event.type === 'routine.finished') {
      this.routineFinished(event.payload as RoutineFinished, Boolean(event.replayed))

      return
    }

    if (!conversationId) {
      return
    }

    if (event.type === 'subagent.update') {
      const rows = this.$subagents.get()[conversationId] ?? []
      this.$subagents.setKey(conversationId, applySubagent(rows, event.payload as SubagentRow))

      return
    }

    if (event.type === 'command.update') {
      const rows = this.$commands.get()[conversationId] ?? []
      this.$commands.setKey(conversationId, applyCommand(rows, event.payload as CommandRow))

      return
    }

    if (event.type === 'preview.open' || event.type === 'preview.close') {
      // The agent's desktop_preview: a tab of its conversation, shown at once only when the user
      // is on that conversation: it never takes the focus from one in the background.
      const asked = event.payload as PreviewPayload

      if (event.type === 'preview.close') {
        closePreview(conversationId, asked.target ? { target: asked.target } : undefined)
      } else if (!event.replayed) {
        const here =
          $session.get() === this && this.$view.get() === 'conversation' && this.$selected.get() === conversationId
        openPreview(conversationId, targetOf(asked.target, asked.label), here)
      }

      return
    }

    if (event.type === 'title.changed') {
      const { title } = event.payload as TitleChanged
      const rows = this.$conversations.get()

      if (rows.some(row => row.id === conversationId)) {
        this.$conversations.set(rows.map(row => (row.id === conversationId ? { ...row, title } : row)))
      } else {
        void this.refreshList().catch(() => undefined)
      }

      return
    }

    const before = this.$threads.get()[conversationId] ?? EMPTY_THREAD
    const after = applyEvent(before, event)

    if (after !== before) {
      this.$threads.setKey(conversationId, after)
    }

    // The user's words reached the conversation: their bubble on its way gives way to it.
    if (event.type === 'message.start' || event.type === 'message.steered') {
      const said = event.payload as UserSaid

      if (said.kind !== 'notice') {
        this.arrived(conversationId, said.text)
      }
    }

    const ofBot = this.botOf(conversationId) !== undefined

    if (event.type === 'message.start' && !ofBot && !this.$conversations.get().some(row => row.id === conversationId)) {
      void this.refreshList()
    }

    if (event.type === 'message.complete') {
      const complete = event.payload as MessageComplete

      if (complete.unsent.length) {
        const kept = this.$restored.get()[conversationId] ?? ''
        this.$restored.setKey(conversationId, [kept, ...complete.unsent].filter(Boolean).join('\n\n'))
      }

      // What waited to enter the turn either entered it or came back to the box with the unsent.
      const waiting = (this.$sending.get()[conversationId] ?? []).filter(row => row.state === 'queued')

      for (const row of waiting) {
        this.dropSending(conversationId, row.key)
      }

      const notify = !event.replayed && complete.status !== 'interrupted' && this.away(conversationId)
      // A bot's row shows the last thing said in its conversation.
      void this.refreshBots().catch(() => undefined)
      // The list first: the server titles a conversation as its first turn is stored.
      void this.refreshList()
        .catch(() => undefined)
        .then(() => {
          if (notify) {
            notifyFor(this, conversationId, t.notification.done)
          }
        })
    }
  }

  /** A routine's run ended: its runs are read again, and the system says so, unless the run asked
   * for silence ([SILENT]); a click opens the run's conversation. */
  private routineFinished(finished: RoutineFinished, replayed: boolean): void {
    void this.refreshRoutines().catch(() => undefined)

    if (this.$runs.get()[finished.routine_id]) {
      void this.readRuns(finished.routine_id).catch(() => undefined)
    }

    if (finished.conversation_id) {
      void this.refreshList().catch(() => undefined)
    }

    if (replayed || finished.status === 'silent') {
      return
    }

    const title =
      finished.status === 'error' || finished.status === 'unknown'
        ? t.routines.failed(finished.name)
        : t.routines.finished(finished.name)
    shell.notify(title, finished.text || finished.name, () => {
      shell.focus()
      show(this)

      if (finished.conversation_id) {
        void this.selectConversation(finished.conversation_id)
      } else {
        this.$view.set('routines')
      }
    })
  }

  /** The user is not looking at this conversation: another home or conversation is on screen, or
   * the window is not in front. */
  private away(conversationId: string): boolean {
    return $session.get() !== this || this.$selected.get() !== conversationId || !document.hasFocus()
  }
}

// -- the home on screen ---------------------------------------------------------------------------

export const $connection = atom<Connection>({ error: null, state: 'idle' })
export const $read = map<Record<Listed, boolean>>({ bots: false, conversations: false, workbench: false })
export const $readError = map<Partial<Record<Readable, string>>>({})
export const $conversations = atom<ConversationRow[]>([])
export const $listMore = atom<ListMore>({ cursor: '', error: null, reading: false })
export const $search = atom<ListSearch>(NO_SEARCH)
export const $selected = atom<null | string>(null)
export const $threads = map<Record<string, Thread>>({})
export const $autoMode = map<Record<string, boolean>>({})
export const $models = map<Record<string, ConversationModel | undefined>>({})
export const $modelOptions = atom<ModelOptions | null>(null)
export const $keyProviders = atom<KeyProvider[] | null>(null)
export const $keyLater = atom(false)
export const $keyNotice = atom<null | ProviderConnected>(null)
export const $restored = map<Record<string, string | undefined>>({})
export const $sending = map<Record<string, Sending[] | undefined>>({})
export const $cards = atom<Card[]>([])
export const $proposals = atom<ProposalRow[]>([])
export const $approved = map<Record<string, Approved | undefined>>({})
export const $view = atom<View>('conversation')
export const $routines = atom<RoutineRow[]>([])
export const $market = atom<Market>(EMPTY_MARKET)
export const $plugins = atom<PluginRow[]>([])
/** The screens of the home on screen's plugins that are on (step 16m). */
export const $screens = atom<PluginScreenRow[]>([])
export const $pairings = atom<PairingRow[]>([])
export const $pluginError = atom<null | string>(null)
export const $runs = map<Record<string, RunRow[] | undefined>>({})
export const $subagents = map<Record<string, SubagentRow[] | undefined>>({})
export const $commands = map<Record<string, CommandRow[] | undefined>>({})
export const $workbench = atom<Workbench>({ executors: [], instances: [] })
export const $workbenchError = map<Record<string, string | undefined>>({})
export const $detecting = atom(false)
export const $bots = atom<BotRow[]>([])
export const $picture = atom<null | string>(null)
export const $pictureError = atom<null | string>(null)
export const $sessionFolders = map<Record<string, FoldersRead | undefined>>({})
export const $sessions = map<Record<string, SessionsRead | undefined>>({})
export const $openFolders = map<Record<string, true | undefined>>({})
export const $openExecutor = atom<null | string>(null)
export const $openExecutors = map<Record<string, true | undefined>>({})
export const $live = map<Record<string, true | undefined>>({})
export const $marks = map<Record<string, Mark | undefined>>({})
export const $used = map<Record<string, number | undefined>>({})
export const $openSession = atom<OpenSession | null>(null)
export const $sessionThread = atom<Thread>(EMPTY_THREAD)
export const $sessionSettings = atom<SessionSettings>(NO_SESSION_SETTINGS)
export const $sessionError = atom<null | string>(null)
export const $sessionRoute = atom<null | SessionRouteRow>(null)
export const $executorModels = map<Record<string, ExecutorModel[] | undefined>>({})
export const $executorConnectors = map<Record<string, ConnectorsRead | undefined>>({})
export const $executorUsage = map<Record<string, undefined | UsageRead>>({})
export const $executorAccounts = map<Record<string, ExecutorAccounts | undefined>>({})
export const $slashCommands = map<Record<string, SlashCommandRow[] | undefined>>({})
export const $commandReads = map<Record<string, CommandsRead | undefined>>({})
export const $atlasCommands = map<Record<string, SlashCommandRow[] | undefined>>({})
export const $tabs = atom<Tab[]>([])
/** The sidebar's list on screen, and the bot dialog when one is open. */
export const $sidebarTab = atom<SidebarTab>('conversations')
export const $botDialog = atom<BotDialog | null>(null)
/** The homes of this machine, as the server on screen listed them; the name of the one on screen. */
export const $homes = atom<HomeRow[]>([])
export const $home = atom<null | string>(null)
/** The open home on screen. */
export const $session = atom<HomeSession | null>(null)
/** The machine on screen (step 15): `null` for this computer, else its id in the machine list. */
export const $machine = atom<null | string>(null)
/** The home on screen is another machine's: its folders and files are not this one's, and the
 * system's dialogs, which choose this machine's paths, are off. */
export const $remote = atom<boolean>(false)

/** How the window names another machine's home to the main process (electron/main.ts). */
const MACHINE = 'machine:'

function machineOf(key: null | string): null | string {
  return key?.startsWith(MACHINE) ? key.slice(MACHINE.length) : null
}

type Mirror = (session: HomeSession) => () => void

/** A store of the screen that follows one store of the session on screen. */
function mirror<T>(target: WritableAtom<T>, pick: (session: HomeSession) => ReadableAtom<T>): Mirror {
  return session =>
    pick(session).subscribe(value => {
      target.set(value)
    })
}

/** The screen's stores follow the session on screen. */
const MIRRORS: Mirror[] = [
  mirror($connection, s => s.$connection),
  mirror($read, s => s.$read),
  mirror($readError, s => s.$readError),
  mirror($conversations, s => s.$conversations),
  mirror($listMore, s => s.$listMore),
  mirror($search, s => s.$search),
  mirror($selected, s => s.$selected),
  mirror($threads, s => s.$threads),
  mirror($autoMode, s => s.$autoMode),
  mirror($models, s => s.$models),
  mirror($modelOptions, s => s.$modelOptions),
  mirror($keyProviders, s => s.$keyProviders),
  mirror($keyLater, s => s.$keyLater),
  mirror($keyNotice, s => s.$keyNotice),
  mirror($restored, s => s.$restored),
  mirror($sending, s => s.$sending),
  mirror($cards, s => s.$cards),
  mirror($proposals, s => s.$proposals),
  mirror($approved, s => s.$approved),
  mirror($view, s => s.$view),
  mirror($routines, s => s.$routines),
  mirror($market, s => s.$market),
  mirror($plugins, s => s.$plugins),
  mirror($screens, s => s.$screens),
  mirror($pairings, s => s.$pairings),
  mirror($pluginError, s => s.$pluginError),
  mirror($runs, s => s.$runs),
  mirror($subagents, s => s.$subagents),
  mirror($commands, s => s.$commands),
  mirror($workbench, s => s.$workbench),
  mirror($workbenchError, s => s.$workbenchError),
  mirror($detecting, s => s.$detecting),
  mirror($bots, s => s.$bots),
  mirror($picture, s => s.$picture),
  mirror($pictureError, s => s.$pictureError),
  mirror($sessionFolders, s => s.$sessionFolders),
  mirror($sessions, s => s.$sessions),
  mirror($openFolders, s => s.$openFolders),
  mirror($openExecutor, s => s.$openExecutor),
  mirror($openExecutors, s => s.$openExecutors),
  mirror($live, s => s.$live),
  mirror($marks, s => s.$marks),
  mirror($used, s => s.$used),
  mirror($openSession, s => s.$openSession),
  mirror($sessionThread, s => s.$sessionThread),
  mirror($sessionSettings, s => s.$sessionSettings),
  mirror($sessionError, s => s.$sessionError),
  mirror($sessionRoute, s => s.$sessionRoute),
  mirror($executorModels, s => s.$executorModels),
  mirror($executorConnectors, s => s.$executorConnectors),
  mirror($executorUsage, s => s.$executorUsage),
  mirror($executorAccounts, s => s.$executorAccounts),
  mirror($slashCommands, s => s.$slashCommands),
  mirror($commandReads, s => s.$commandReads),
  mirror($atlasCommands, s => s.$atlasCommands),
  mirror($tabs, s => s.tabs.$tabs)
]

const sessions: HomeSession[] = []
let mirrored: (() => void)[] = []
let shell: Shell = { focus: () => undefined, notify: () => undefined }
let makeClient: (home: null | string) => AtlasClient = () => {
  throw new Error('The app has not started.')
}
let releaseHome: (home: null | string) => Promise<void> = () => Promise.resolve()
/** The app reaches one server it did not start, by its address (`ATLAS_DESKTOP_URL`). */
let remoteServer = false

export interface AppOptions {
  /** A client for a home's server; `null` is the home that opens first. */
  client: (home: null | string) => AtlasClient
  /** Let a home's server go. */
  release?: (home: null | string) => Promise<void>
  /** The app reaches one server by its address, on another machine or in a container. */
  remote?: boolean
  shell: Shell
}

/** Open the home that opens first. Returns what stops every home's connection. */
export function startApp(options: AppOptions): () => void {
  makeClient = options.client
  releaseHome = options.release ?? (() => Promise.resolve())
  remoteServer = options.remote ?? false
  shell = options.shell
  show(open(null))
  // Coming back from the terminal, where a home may have been made, reads the homes again.
  const onFocus = () => {
    void refreshHomes()
  }
  window.addEventListener('focus', onFocus)
  // The home on screen opens first next time.
  const forget = $home.subscribe(name => {
    if (name) {
      shell.remember?.(name)
    }
  })

  return () => {
    forget()
    window.removeEventListener('focus', onFocus)
    for (const session of sessions.splice(0)) {
      session.stop()
    }
  }
}

function open(key: null | string): HomeSession {
  const session = new HomeSession(makeClient(key), key)
  sessions.push(session)
  session.start()

  return session
}

function show(session: HomeSession): void {
  for (const stop of mirrored) {
    stop()
  }

  session.lastUsed = Date.now()
  $session.set(session)
  $home.set(session.name)
  $machine.set(machineOf(session.key))
  $remote.set(remoteServer || machineOf(session.key) !== null)
  mirrored = MIRRORS.map(follow => follow(session))
}

/** The session on screen. */
export function current(): HomeSession {
  const session = $session.get()

  if (!session) {
    throw new Error('The app has not started.')
  }

  return session
}

/** Switch the screen to a home, opening it (and starting its server) when it is not open yet. On
 * another machine, the home is that machine's one, and switching means another machine. */
export async function openHome(name: string): Promise<void> {
  if ($machine.get() !== null) {
    return
  }

  const found = sessions.find(session => machineOf(session.key) === null && session.name === name)

  if (found) {
    show(found)

    return
  }

  show(open(name))
  await letGo()
}

/** Switch the screen to a machine (step 15): another machine's home, connected the first time it is
 * opened, or back to this computer's last home on screen. */
export async function openMachine(id: null | string): Promise<void> {
  if (id === null) {
    const local = sessions.filter(session => machineOf(session.key) === null).sort((a, b) => b.lastUsed - a.lastUsed)[0]
    show(local ?? open(null))
    await letGo()

    return
  }

  const key = `${MACHINE}${id}`
  show(sessions.find(session => session.key === key) ?? open(key))
  await letGo()
}

/** Let go of a machine's connection, as it leaves the list. */
export async function forgetMachine(id: string): Promise<void> {
  const key = `${MACHINE}${id}`
  const found = sessions.find(session => session.key === key)

  if (!found) {
    return
  }

  if ($session.get() === found) {
    await openMachine(null)
  }

  sessions.splice(sessions.indexOf(found), 1)
  found.stop()
  await releaseHome(key).catch(() => undefined)
}

/** Read again the open sessions of this machine's home, after its server changed (always on, or
 * not): the window's next dial reaches the right one. */
export function redialHome(name: null | string): void {
  for (const session of sessions) {
    if (machineOf(session.key) === null && (name === null || session.name === name)) {
      session.client.reconnect()
    }
  }
}

/** What would stop if the app closed now, in every open home: the turns that run, Atlas's
 * subagents at work, and the executor sessions that work or wait for an answer. The update asks
 * before it closes the app on them. Given a server's session, what its restart would stop there:
 * a server's update restarts it. */
export function workUnderway(only?: HomeSession): { executors: number; subagents: number; turns: number } {
  const underway = { executors: 0, subagents: 0, turns: 0 }
  // Another machine's turns go on there when this app closes.
  const counted = only ? [only] : sessions.filter(each => machineOf(each.key) === null)

  for (const session of counted) {
    underway.turns += Object.values(session.$threads.get()).filter(thread => thread.running).length
    underway.subagents += Object.values(session.$subagents.get())
      .flatMap(rows => rows ?? [])
      .filter(row => row.status === 'running').length
    underway.executors += session.$workbench
      .get()
      .instances.filter(row => row.state === 'working' || row.waiting > 0).length
  }

  return underway
}

/** Beyond the limit, close the home used longest ago that has no turn running. */
async function letGo(): Promise<void> {
  while (sessions.length > MAX_OPEN_HOMES) {
    const idle = sessions
      .filter(session => session !== $session.get() && !session.running())
      .sort((a, b) => a.lastUsed - b.lastUsed)[0]

    if (!idle) {
      return
    }

    sessions.splice(sessions.indexOf(idle), 1)
    idle.stop()
    await releaseHome(idle.key).catch(() => undefined)
  }
}

/** Read the homes of this machine again from the server on screen. */
export function refreshHomes(): Promise<void> {
  const session = $session.get()

  return session ? readHomes(session).catch(() => undefined) : Promise.resolve()
}

async function readHomes(session: HomeSession): Promise<void> {
  const listed = await session.client.call('home.list', {})
  session.name = listed.current
  // Named, the home's tabs come back.
  session.restoreTabs()

  if ($session.get() === session) {
    $home.set(listed.current)
    // Another machine's other homes are reached as machines of their own, by their own codes.
    $homes.set(machineOf(session.key) === null ? listed.homes : listed.homes.filter(row => row.name === listed.current))
  }
}

/** A machine's name by its id, as the machine list says (store/machines.ts gives it). */
let machineLabel: (id: string) => string = id => id

export function nameMachinesBy(resolve: (id: string) => string): void {
  machineLabel = resolve
}

/** A turn ended off screen: the notification names the conversation, and the home or the machine
 * it is on when another is on screen (found by the e2e, 19/10/2026: another machine's said "home
 * default", its home's name, as this computer's default home would). */
function notifyFor(session: HomeSession, conversationId: string, title: string): void {
  const row = session.$conversations.get().find(conversation => conversation.id === conversationId)
  const named = session.botOf(conversationId)?.title ?? (row?.title || t.conversations.untitled)
  const machine = machineOf(session.key)
  const body =
    $session.get() === session
      ? named
      : machine !== null
        ? t.machines.onMachine(named, machineLabel(machine))
        : session.name
          ? t.homes.inHome(named, session.name)
          : named
  shell.notify(title, body, () => {
    shell.focus()
    show(session)
    void session.selectConversation(conversationId)
  })
}

// -- what the screen does, on the home on screen ---------------------------------------------------

export function refreshList(): Promise<void> {
  return current().refreshList()
}

/** The next page of the list of conversations, as the sidebar nears its end (step 9e); `again`,
 * after a read of it failed. */
export function moreConversations(again = false): Promise<void> {
  return current().moreConversations(again)
}

/** What the list's search field holds, searched once the typing pauses (step 9e). */
export function typeSearch(text: string): void {
  current().typeSearch(text)
}

/** The list's search again, after it failed. */
export function searchAgain(): void {
  current().searchAgain()
}

/** End the list's search, at once. */
export function clearSearch(): void {
  current().clearSearch()
}

/** Read again a list or a page whose last read failed; a failure again says so in its place. */
export function readAgain(what: Readable): void {
  const session = current()
  const read = {
    bots: () => session.refreshBots(),
    conversations: () => session.refreshList(),
    proposals: () => session.refreshProposals(),
    routines: () => session.refreshRoutines(),
    workbench: () => session.refreshWorkbench()
  }[what]

  void read().catch(() => undefined)
}

export function selectConversation(conversationId: string): Promise<void> {
  return current().selectConversation(conversationId)
}

export function newConversation(): void {
  current().newConversation()
}

/** The plugin guide, on GitHub: what a plugin may hold and every part of the SDK. */
export const PLUGIN_GUIDE = 'https://github.com/marqueskaio/atlas/blob/main/src/atlas/docs/pages/extending/plugins.md'

/** A new conversation with `/write-plugin ` in its box, for the user to say what the plugin should
 *  do: the skill Atlas ships writes it with them. */
export function writePlugin(): void {
  const session = current()
  session.newConversation()
  session.$restored.setKey(DRAFT, '/write-plugin ')
}

export function refreshProposals(): Promise<void> {
  return current().refreshProposals()
}

/** Open the list of every memory proposal, each checked again against the memory as it is now. */
export function showProposals(): void {
  const session = current()
  session.$view.set('proposals')
  void session.refreshProposals().catch(() => undefined)
}

/** Open the routines, read again. */
export function showRoutines(): void {
  const session = current()
  session.$view.set('routines')
  void session.refreshRoutines().catch(() => undefined)
}

/** Open the marketplace (step 16s) on its list, read again. */
export function showMarketplace(): void {
  const session = current()
  session.$view.set('marketplace')
  session.market({ confirming: null, failed: null, open: null, page: null })
  void session.refreshMarketplace()
}

/** Read the marketplace from GitHub now. */
export function refreshMarketplace(): void {
  void current().refreshMarketplace(true)
}

export function setMarketKind(kind: MarketKind): void {
  current().market({ kind })
}

export function setMarketQuery(query: string): void {
  current().market({ query })
}

export function openMarketItem(name: string): void {
  void current().openMarketItem(name)
}

/** Back from an item's page to the list. */
export function closeMarketItem(): void {
  current().market({ brought: [], confirming: null, failed: null, open: null, page: null, pageError: null })
}

export function actOnMarket(action: 'install' | 'remove' | 'update', name: string, force = false): void {
  void current().actOnMarket(action, name, force)
}

export function cancelMarketUpdate(): void {
  current().market({ confirming: null })
}

export function storeMarketSecret(name: string, secret: string, value: string): Promise<boolean> {
  return current().storeMarketSecret(name, secret, value)
}

/** The main agent's soul, `SOUL.md`, as the open home's server reads it. */
export function readSoul(): Promise<Soul> {
  return current().client.call('soul.read', {})
}

/** Write the soul whole; it applies from the next conversation. */
export function writeSoul(text: string): Promise<Soul> {
  return current().client.call('soul.write', { text })
}

/** Read the plugins again, for the settings screen's Plugins. */
export function refreshPlugins(): void {
  const session = current()
  session.$pluginError.set(null)
  void session.refreshPlugins().catch(() => undefined)
}

/** The Atlas version the open home's server runs; `null` before it said. */
export function serverVersion(): null | string {
  return current().client.version
}

/** The editable settings of the open home's `config.toml` (step 15b). */
export function readSettings(): Promise<SettingsList> {
  return current().client.call('settings.read', {})
}

/** Write settings, applied by the server without a restart; the default model the picker shows is
 * read again, since it may be what changed. */
export async function writeSettings(changes: SettingChange[]): Promise<SettingsList> {
  const session = current()
  const written = await session.client.call('settings.write', { changes })
  void session.refreshModelOptions().catch(() => undefined)

  return written
}

/** "About you" (`USER.md`) or the memory (`MEMORY.md`), entry by entry. */
export function readMemory(target: MemoryEntriesParams['target']): Promise<MemoryEntries> {
  return current().client.call('memory.entries', { target })
}

/** Add, replace or remove one entry; it applies from the next conversation. */
export function editMemory(edit: MemoryEdit): Promise<MemoryEntries> {
  return current().client.call('memory.edit', edit)
}

/** Turn a plugin on with the consent to what the user saw it declare (its row's `consent`). */
export function enablePlugin(plugin: PluginRow): Promise<boolean> {
  return current().actOnPlugins(client => client.call('plugin.enable', { consent: plugin.consent, name: plugin.name }))
}

/** Load every plugin that is on again from its files (step 16n). */
export function reloadPlugins(): Promise<boolean> {
  return current().actOnPlugins(client => client.call('plugin.reload', {}))
}

export function disablePlugin(name: string): Promise<boolean> {
  return current().actOnPlugins(client => client.call('plugin.disable', { name }))
}

export function storePluginSecret(name: string, secret: string, value: string): Promise<boolean> {
  return current().actOnPlugins(client => client.call('plugin.secret', { name, secret, value }))
}

/** Change a plugin's setting; `null` goes back to its default. */
export function setPluginSetting(name: string, setting: string, value: null | string): Promise<boolean> {
  return current().actOnPlugins(client => client.call('plugin.setting', { name, setting, value }))
}

export function approvePairing(code: string): Promise<boolean> {
  return current().actOnPlugins(client => client.call('pairing.approve', { code }))
}

export function forgetPairing(channel: string, user: string): Promise<boolean> {
  return current().actOnPlugins(client => client.call('pairing.forget', { channel, user }))
}

export function readRuns(routineId: string): Promise<void> {
  return current().readRuns(routineId)
}

export function actOnRoutine(action: 'pause' | 'remove' | 'resume' | 'run', routineId: string): Promise<void> {
  return current().actOnRoutine(action, routineId)
}

export function showSidebarTab(tab: SidebarTab): void {
  $sidebarTab.set(tab)

  if (tab === 'bots') {
    void current()
      .refreshBots()
      .catch(() => undefined)
  }

  if (tab === 'executors') {
    const session = current()
    // What was found shows at once; a harness installed or updated since is looked for after.
    void session
      .refreshWorkbench()
      .then(() => session.detectExecutors(false))
      .catch(() => undefined)
  } else {
    // The executor's tab is the executors tab's: the panel leaves it with the list.
    leaveExecutorTab()
  }
}

// -- the executors tab (step 16f) ---------------------------------------------------------------

/** Open an executor in the list, or close it; the others open stay so. Either way the panel's tab
 * goes to the one clicked (asked for by the user on 22/10/2026, where closing one showed another's
 * plan). */
export function toggleExecutor(executor: string): void {
  const open = current().$openExecutors

  if (open.get()[executor]) {
    open.setKey(executor, undefined)
    focusExecutor(executor)

    return
  }

  showExecutor(executor)
}

/** Look for the harnesses again now (the offers' "Look again"). */
export function detectExecutors(): Promise<void> {
  return current()
    .detectExecutors(true)
    .catch(() => undefined)
}

/** Open an executor's sessions in the sidebar, with the others open, and its tab in the side panel
 * (its connectors, read the first time); `null` closes them all, and the panel leaves its tab. */
export function showExecutor(executor: null | string): void {
  const session = current()
  session.$openExecutor.set(executor)

  if (!executor) {
    session.$openExecutors.set({})
    leaveExecutorTab()

    return
  }

  session.$openExecutors.setKey(executor, true)
  session.listExecutor(executor)
  focusExecutor(executor)
}

/** The side panel's tab goes to `executor`, its list left as it is: any click on its rows (itself,
 * a folder, a session) shows its plan usage there (asked for by the user on 22/10/2026). */
function focusExecutor(executor: string): void {
  current().followExecutor(executor)
  showExecutorTab()
}

/** Where an executor's slash commands are kept: a project adds its own. */
export function slashKey(executor: string, folder: string): string {
  return `${executor}\u0000${folder}`
}

/** Read the slash commands of the session's box (see `readExecutorCommands`). */
export function readExecutorCommands(executor: string, folder: string, instanceId: string, names: string): void {
  void current().readExecutorCommands(executor, folder, instanceId, names)
}

/** Read an executor's skills in `folder` for the side panel: once, unless `again` (its ↻). */
export function readPanelCommands(executor: string, folder: string, again = false): void {
  const session = current()
  const key = slashKey(executor, folder)

  if (again || (session.$slashCommands.get()[key] === undefined && session.$commandReads.get()[key] === undefined)) {
    void session.refreshExecutorCommands(executor, folder)
  }
}

/** Read what the box of Atlas's conversation or a bot's offers (see `readAtlasCommands`). */
export function readAtlasCommands(conversationId: null | string): void {
  void current().readAtlasCommands(conversationId)
}

/** Read an executor's plan usage again, on the panel's ↻. */
export function refreshExecutorUsage(executor: string): Promise<void> {
  return current().refreshExecutorUsage(executor)
}

/** Read an executor's plan usage when the last read is old: a route's stops as it opens. */
export function readUsageIfStale(executor: string): void {
  current().readUsageIfStale(executor)
}

/** Read the models an executor offers, once: a route's stops as it opens. */
export function readExecutorModels(executor: string): void {
  const session = current()

  if (session.$executorModels.get()[executor] === undefined) {
    void session.refreshExecutorModels(executor).catch(() => undefined)
  }
}

export function addAccount(executor: string, nickname: string): Promise<boolean> {
  return current().addAccount(executor, nickname)
}

export function removeAccount(executor: string, account: string): Promise<boolean> {
  return current().removeAccount(executor, account)
}

export function setFolderAccount(executor: string, folder: string, account: string): Promise<void> {
  return current().setFolderAccount(executor, folder, account)
}

export function checkSessionAccount(account: string): Promise<AccountSwitchRow | undefined> {
  return current().checkSessionAccount(account)
}

export function switchSessionAccount(account: string, note?: string): Promise<void> {
  return current().switchSessionAccount(account, note)
}

export function readSessionRoute(instanceId: string): Promise<void> {
  return current().readSessionRoute(instanceId)
}

export function setSessionRoute(instanceId: string, route: SessionRouteSet): Promise<void> {
  return current().setSessionRoute(instanceId, route)
}

export function deleteSession(row: SessionRow): Promise<boolean> {
  return current().deleteSession(row)
}

/** Read an executor's connectors again: the user logged into one, or added one. */
export function refreshExecutorConnectors(executor: string): Promise<void> {
  return current().refreshExecutorConnectors(executor)
}

export function openSession(row: SessionRow): Promise<void> {
  focusExecutor(row.executor)

  return current().openSession(row)
}

/** Open or close a folder of the executor on screen; opened, its sessions are read the first
 * time. */
export function toggleFolder(executor: string, folder: string): void {
  focusExecutor(executor)
  current().toggleFolder(executor, folder)
}

/** Read an executor's folders again, after they could not be read. */
export function refreshFolders(executor: string): Promise<void> {
  return current().refreshFolders(executor)
}

/** Read a folder's sessions again, after they could not be read. */
export function refreshSessions(executor: string, folder: string): Promise<void> {
  return current().refreshSessions(executor, folder)
}

export function newSession(executor: string, folder: string): void {
  focusExecutor(executor)
  current().newSession(executor, folder)
}

/** A folder from the system's dialog, when the app started its server; `null` otherwise. */
export async function chooseFolder(): Promise<null | string> {
  return (await shell.chooseFolder?.()) ?? null
}

export function canChooseFolder(): boolean {
  return shell.chooseFolder !== undefined && !$remote.get()
}

export function chooseElsewhere(choice: Elsewhere): void {
  current().chooseElsewhere(choice)
}

export function sendToSession(text: string, attachments: AttachmentRow[] = []): Promise<boolean> {
  return current().sendToSession(text, attachments)
}

export function stopSession(): Promise<void> {
  return current().stopSession()
}

export function setSessionSwitch(which: SessionSwitch, enabled: boolean): Promise<void> {
  return current().setSessionSwitch(which, enabled)
}

export function setSessionModel(model: null | string, effort: null | string): Promise<void> {
  return current().setSessionModel(model, effort)
}

export function setPicture(png: null | string): Promise<void> {
  return current().setPicture(png)
}

export function switchExecutorAccount(executor: string, login: string): Promise<boolean> {
  return current().switchExecutorAccount(executor, login)
}

export function openDelegated(conversationId: string, subagentId: string): Promise<boolean> {
  return current().openDelegated(conversationId, subagentId)
}

/** A session's row for an instance, from what the workbench says of it. */
function sessionOf(row: InstanceRow): SessionRow {
  return {
    by_atlas: row.owner === 'atlas',
    elsewhere: '',
    executor: row.executor,
    folder: row.folder,
    instance_id: row.instance_id,
    session_id: row.session_id,
    started_at: '',
    state: row.state,
    title: '',
    updated_at: '',
    waiting: row.waiting
  }
}

export function openBot(name: string): Promise<void> {
  return current().openBot(name)
}

/** Open the dialog for a new bot. */
export function newBot(): void {
  $botDialog.set({ definition: null, mode: 'create' })
}

/** Open the dialog on a bot's definition, read from the server. */
export async function editBot(name: string): Promise<void> {
  const definition = await current().client.call('bot.describe', { name })
  $botDialog.set({ definition, mode: 'edit' })
}

export function closeBotDialog(): void {
  $botDialog.set(null)
}

/** What a bot's definition may name: the home's tools, MCP servers and skills. */
export function botOptions(): Promise<BotOptions> {
  return current().client.call('bot.options', {})
}

/** Save the dialog's definition; the dialog closes when the server took it. */
export async function saveBot(definition: BotDefinition, creating: boolean): Promise<void> {
  await current().saveBot(definition, creating)
  $botDialog.set(null)

  if (creating) {
    $sidebarTab.set('bots')
  }
}

export async function deleteBot(name: string): Promise<void> {
  await current().deleteBot(name)
  $botDialog.set(null)
}

/**
 * Whether the key screen shows (docs/ARCHITECTURE.md, The installer): the server said what the
 * picker offers, it offers nothing and chooses no model, and the user did not choose later; or a key
 * was just stored with something the user should read.
 */
export function keyScreenShown(
  options: ModelOptions | null,
  later: boolean,
  notice: null | ProviderConnected
): boolean {
  if (notice !== null) {
    return true
  }

  return options !== null && options.providers.length === 0 && options.default.provider === null && !later
}

export function connectProvider(provider: string, key: string): Promise<ProviderConnected> {
  return current().connectProvider(provider, key)
}

/** "Choose later" on the key screen, or the band's way back to it. */
export function chooseKeyLater(later: boolean): void {
  current().$keyLater.set(later)
}

/** The user read what the stored key's check said. */
export function dismissKeyNotice(): void {
  current().$keyNotice.set(null)
}

export function showConversation(): void {
  current().$view.set('conversation')
}

/** Show what a tab holds (store/tabs.ts). */
export function showTab(id: string): Promise<void> {
  return current().showTab(id)
}

export function closeTab(id: string): void {
  current().closeTab(id)
}

/** The passing tab stays (a double click). */
export function keepTab(id: string): void {
  current().keepTab(id)
}

/** A conversation's passing tab stays (a double click on its row). */
export function keepConversation(conversationId: string): void {
  current().tabs.keep({ conversationId, kind: 'conversation' })
}

export function moveTab(id: string, to: number): void {
  current().tabs.move(id, to)
}

/** The tab on screen; `null` for a new conversation, a new session or a page. */
export function activeTab(): null | Tab {
  return current().activeTab()
}

/** Show where the first card away from the screen waits. */
export function openWaiting(): Promise<void> {
  return current().openWaiting()
}

export function approveProposal(proposal: ProposalRow): Promise<void> {
  return current().approveProposal(proposal)
}

export function discardProposal(proposalId: string): Promise<void> {
  return current().discardProposal(proposalId)
}

/** Take down the card of a proposal approved here. */
export function putAwayApproved(proposalId: string): void {
  current().$approved.setKey(proposalId, undefined)
}

/** Start a turn, or steer the one running, with the ids of the files attached. A first message
 * makes the conversation first. */
export async function send(text: string, attachments: AttachmentRow[] = []): Promise<void> {
  const trimmed = text.trim()

  if (trimmed || attachments.length) {
    await current().send(trimmed, attachments)
  }
}

export function interrupt(): Promise<void> {
  return current().interrupt()
}

export function runUserCommand(command: string): Promise<void> {
  return current().runUserCommand(command)
}

export function checkCommand(command: string): Promise<{ dangerous: boolean; reason: string }> {
  return current().checkCommand(command)
}

/** Whose previews and box chips an executor's session has. */
export function sessionOwner(open: OpenSession): string {
  return `session:${open.sessionId || open.instanceId}`
}

/** Whose previews the panel shows: the executor's session on screen, or the conversation (a new
 * one not made yet is the draft's). */
export function currentOwner(): string {
  const session = $session.get()

  if (!session) {
    return DRAFT
  }

  const open = session.$view.get() === 'session' ? session.$openSession.get() : null

  if (open) {
    return sessionOwner(open)
  }

  return session.$selected.get() ?? DRAFT
}

/** The folder of what is on screen: an executor's session's, or the conversations' workspace. */
export function currentFolder(): string {
  const session = current()
  const open = session.$view.get() === 'session' ? session.$openSession.get() : null

  return open?.folder ?? session.workspace
}

export function steerSubagent(subagentId: string, message: string): Promise<boolean> {
  return current().steerSubagent(subagentId, message)
}

export function answerExecutor(id: string, result: Record<string, unknown>): void {
  current().answer(id, result)
}

// -- the workbench (step 16c) ---------------------------------------------------------------------

/** Turn an executor's plugin on from the onboarding, with the consent to what the user just
 * read it declare; the server looks for it again, and the workbench offers it to start. */
export async function turnOnExecutor(row: ExecutorRow): Promise<void> {
  const session = current()
  await session.onWorkbench('offer', client => client.call('plugin.enable', { consent: row.consent, name: row.plugin }))
  await session.refreshWorkbench().catch(() => undefined)
}

export function stopSubagent(subagentId: string): Promise<void> {
  return current().stopSubagent(subagentId)
}

export function commandOutput(commandId: string): Promise<CommandOutput> {
  return current().commandOutput(commandId)
}

export function stopCommand(commandId: string): Promise<CommandStopped> {
  return current().stopCommand(commandId)
}

export function setAutoMode(enabled: boolean): Promise<void> {
  return current().setAutoMode(enabled)
}

export function setModel(provider: string, model: string, effort: null | string): Promise<void> {
  return current().setModel(provider, model, effort)
}

export function takeRestored(conversationId: string): string {
  const session = current()
  const text = session.$restored.get()[conversationId] ?? ''

  if (text) {
    session.$restored.setKey(conversationId, undefined)
  }

  return text
}

export function answerAsk(id: string, result: AskResult): void {
  current().answer(id, result as Record<string, unknown>)
}

export function answerApproval(id: string, result: ApprovalResult): void {
  current().answer(id, result as unknown as Record<string, unknown>)
}

/** The key the user pasted (or a skip). The card's state goes with the answer: nothing of the
 * value is kept in a store (step 16d). */
export function answerSecret(id: string, result: Partial<SecretResult>): void {
  current().answer(id, result)
}

// -- the homes from the app (step 10c) -----------------------------------------------------------

/** The homes' dialog on screen: a new home, or a backup checked and waiting for its new name. */
export type HomeDialog = { info: BackupInfo; mode: 'import'; path: string } | { mode: 'create' }
export const $homeDialog = atom<HomeDialog | null>(null)
/** What a backup or a check of one said, under the homes, until closed. */
export type HomeNotice =
  | { kind: 'backed-up'; written: BackupWritten }
  | { kind: 'backing-up' }
  | { kind: 'checking' }
  | { kind: 'failed'; message: string }
export const $homeNotice = atom<HomeNotice | null>(null)
/** A backup, its check and an import wait for the server however long the zip takes: each ends
 * with its answer, or with the connection. */
const NO_DEADLINE = 0

const messageOf = (error: unknown) => (error instanceof Error ? error.message : String(error))

export function newHome(): void {
  $homeDialog.set({ mode: 'create' })
}

export function closeHomeDialog(): void {
  $homeDialog.set(null)
}

export function dismissHomeNotice(): void {
  $homeNotice.set(null)
}

/** Make a home, empty or a copy of another, and open it. */
export async function createHome(name: string, cloneFrom: null | string): Promise<void> {
  const listed = await current().client.call('home.create', { clone_from: cloneFrom, name })
  $homes.set(listed.homes)
  $homeDialog.set(null)
  await openHome(name)
}

/** Back up the home on screen where the system's save dialog says. */
export async function backUpHome(): Promise<void> {
  const session = current()
  const target = await shell.backupTarget?.($home.get() ?? 'default')

  if (!target) {
    return
  }

  $homeNotice.set({ kind: 'backing-up' })

  try {
    // The dialog asked before it gave a file that exists.
    const written = $remote.get()
      ? await downloadBackup(session.client, target)
      : await session.client.call('home.backup', { path: target, replace: true }, NO_DEADLINE)
    $homeNotice.set({ kind: 'backed-up', written })
  } catch (error) {
    $homeNotice.set({ kind: 'failed', message: messageOf(error) })
  }
}

/** Another machine's backup (step 15): made there, it comes down the connection in parts, as
 * attachments go up, and is written here where the save dialog said, whole or not at all. */
async function downloadBackup(client: AtlasClient, target: string): Promise<BackupWritten> {
  const write = shell.backupWrite
  const finish = shell.backupFinish

  if (!write || !finish) {
    throw new Error('This window cannot write a backup.')
  }

  const made = await client.call('home.backup_download', {}, NO_DEADLINE)
  let offset = 0

  try {
    for (;;) {
      const part = await client.call('home.backup_part', { id: made.id, offset }, NO_DEADLINE)
      await write(target, offset, part.data)
      offset += decodedLength(part.data)

      if (part.done) {
        break
      }
    }
  } catch (error) {
    await finish(target, false).catch(() => undefined)
    throw error
  }

  await finish(target, true)

  return { files: made.files, path: target, size: made.size }
}

/** A file for the side panel's preview, read where the server is (step 15): this computer's, another
 * machine's or the container's, by the same call. */
export async function readPreview(path: string): Promise<PreviewFile> {
  try {
    const read = await current().client.call('preview.read', { path })

    switch (read.kind) {
      case 'error':
        return { error: read.content, kind: 'error' }
      case 'image':
        return { dataUrl: read.content, kind: 'image' }
      default:
        return { kind: read.kind, text: read.content }
    }
  } catch (error) {
    return { error: messageOf(error), kind: 'error' }
  }
}

/** How many bytes a base64 text holds. */
function decodedLength(base64: string): number {
  return (base64.length / 4) * 3 - (base64.endsWith('==') ? 2 : base64.endsWith('=') ? 1 : 0)
}

/** Pick a backup in the system's open dialog and have it checked whole; the dialog then asks the
 * new home's name. */
export async function chooseBackup(): Promise<void> {
  const path = await shell.backupSource?.()

  if (!path) {
    return
  }

  $homeNotice.set({ kind: 'checking' })

  try {
    const info = await current().client.call('home.inspect', { path }, NO_DEADLINE)
    $homeNotice.set(null)
    $homeDialog.set({ info, mode: 'import', path })
  } catch (error) {
    $homeNotice.set({ kind: 'failed', message: messageOf(error) })
  }
}

/** Import the checked backup as a new home, and open it. */
export async function importHome(path: string, name: string): Promise<void> {
  const listed = await current().client.call('home.import', { name, path }, NO_DEADLINE)
  $homes.set(listed.homes)
  $homeDialog.set(null)
  await openHome(name)
}
