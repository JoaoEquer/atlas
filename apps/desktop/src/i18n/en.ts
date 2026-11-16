const STATUSES: Record<string, string> = {
  error: 'failed',
  needed: 'needed you',
  ok: 'answered',
  running: 'running',
  silent: 'silent',
  unknown: 'unknown'
}

const EFFORTS: Record<string, string> = {
  high: 'High',
  low: 'Low',
  max: 'Max',
  medium: 'Medium',
  minimal: 'Minimal',
  none: 'Off',
  xhigh: 'Extra high'
}

/** What a call of each of Atlas's own tools does, said three ways: once it ran (`done`), while it
 * runs (`pending`), and what it would do when it waits, was denied or stopped first (`plain`), so a
 * call that never ran never reads as one that did. A tool with several actions has one for each
 * (`tool.action`). */
const TOOL_TITLES: Record<string, { done: string; pending: string; plain: string }> = {
  ask_user: { done: 'Asked', pending: 'Asking', plain: 'Ask' },
  bot_create: { done: 'Created the bot', pending: 'Creating the bot', plain: 'Create the bot' },
  browser_exec: { done: 'Browser:', pending: 'Browser:', plain: 'Browser:' },
  conversation_search: {
    done: 'Searched the conversations for',
    pending: 'Searching the conversations for',
    plain: 'Search the conversations for'
  },
  'cronjob_manage.create': { done: 'Scheduled', pending: 'Scheduling', plain: 'Schedule' },
  'cronjob_manage.list': { done: 'Listed the routines', pending: 'Listing the routines', plain: 'List the routines' },
  'cronjob_manage.pause': { done: 'Paused the routine', pending: 'Pausing the routine', plain: 'Pause the routine' },
  'cronjob_manage.remove': {
    done: 'Removed the routine',
    pending: 'Removing the routine',
    plain: 'Remove the routine'
  },
  'cronjob_manage.resume': {
    done: 'Resumed the routine',
    pending: 'Resuming the routine',
    plain: 'Resume the routine'
  },
  'cronjob_manage.run': { done: 'Ran the routine', pending: 'Running the routine', plain: 'Run the routine' },
  'cronjob_manage.update': {
    done: 'Changed the routine',
    pending: 'Changing the routine',
    plain: 'Change the routine'
  },
  'delegate.account': { done: 'Moved the task to', pending: 'Moving the task to', plain: 'Move the task to' },
  'delegate.list': { done: 'Listed the tasks', pending: 'Listing the tasks', plain: 'List the tasks' },
  'delegate.resume': { done: 'Resumed', pending: 'Resuming', plain: 'Resume' },
  'delegate.spawn': { done: 'Delegated:', pending: 'Delegating:', plain: 'Delegate:' },
  'delegate.steer': { done: 'Steered', pending: 'Steering', plain: 'Steer' },
  'delegate.stop': { done: 'Stopped', pending: 'Stopping', plain: 'Stop' },
  delegation_search: {
    done: 'Searched the delegated tasks for',
    pending: 'Searching the delegated tasks for',
    plain: 'Search the delegated tasks for'
  },
  'desktop_preview.close': { done: 'Closed the preview', pending: 'Closing the preview', plain: 'Close the preview' },
  'desktop_preview.open': { done: 'Opened', pending: 'Opening', plain: 'Open' },
  edit_file: { done: 'Edited', pending: 'Editing', plain: 'Edit' },
  executor_accounts: { done: 'Read the accounts', pending: 'Reading the accounts', plain: 'Read the accounts' },
  find_files: { done: 'Searched the files for', pending: 'Searching the files for', plain: 'Search the files for' },
  'memory.memory': { done: 'Updated its notes:', pending: 'Updating its notes:', plain: 'Update its notes:' },
  'memory.user': { done: 'Updated your profile:', pending: 'Updating your profile:', plain: 'Update your profile:' },
  message_agent: { done: 'Messaged', pending: 'Messaging', plain: 'Message' },
  plan: { done: 'Updated the plan', pending: 'Updating the plan', plain: 'Update the plan' },
  'plan.read': { done: 'Read the plan', pending: 'Reading the plan', plain: 'Read the plan' },
  read_file: { done: 'Read', pending: 'Reading', plain: 'Read' },
  run_command: { done: 'Ran', pending: 'Running', plain: 'Run' },
  'run_command.stop': { done: 'Stopped', pending: 'Stopping', plain: 'Stop' },
  'secrets.list': { done: 'Listed the keys', pending: 'Listing the keys', plain: 'List the keys' },
  'secrets.request': { done: 'Asked for the key', pending: 'Asking for the key', plain: 'Ask for the key' },
  'skill_manage.change': { done: 'Changed the skill', pending: 'Changing the skill', plain: 'Change the skill' },
  'skill_manage.create': { done: 'Created the skill', pending: 'Creating the skill', plain: 'Create the skill' },
  'skill_manage.delete': { done: 'Deleted the skill', pending: 'Deleting the skill', plain: 'Delete the skill' },
  tool_search: { done: 'Searched the tools for', pending: 'Searching the tools for', plain: 'Search the tools for' },
  use_skill: { done: 'Loaded the skill', pending: 'Loading the skill', plain: 'Load the skill' },
  web_fetch: { done: 'Fetched', pending: 'Fetching', plain: 'Fetch' },
  web_search: { done: 'Searched', pending: 'Searching', plain: 'Search' },
  write_file: { done: 'Wrote', pending: 'Writing', plain: 'Write' }
}

/** What Atlas's approval asks, by the tool that asks; another tool is named. */
const APPROVALS: Record<string, string> = {
  cronjob_manage: 'Schedule this command?',
  delegate: 'Act on your own session?',
  edit_file: 'Change this file?',
  run_command: 'Run this command?',
  write_file: 'Change this file?'
}

/** What an executor's tool would do, by its name there; another tool is named. */
const EXECUTOR_ACTS: Record<string, string> = {
  Bash: 'run a command',
  Edit: 'change a file',
  MultiEdit: 'change a file',
  NotebookEdit: 'change a notebook',
  Read: 'read a file',
  WebFetch: 'open a web page',
  WebSearch: 'search the web',
  Write: 'write a file',
  apply_patch: 'change files',
  delete: 'delete files',
  edit: 'change files',
  fetch: 'open a web page',
  move: 'move files',
  permissions: 'get more permissions',
  read: 'read files',
  search: 'search',
  shell: 'run a command'
}

/** Every text the screen shows, in English. Other languages override it (`catalog.ts`). */
export const en = {
  approval: {
    always: 'Always',
    conversation: 'This conversation',
    deny: 'Deny',
    guardianDenied: 'The guardian judged this dangerous. You may still allow it, once.',
    once: 'Allow once',
    reasonPlaceholder: 'Why not? (optional, the agent reads it)',
    title: (tool: string) => APPROVALS[tool] ?? `Allow ${tool}?`
  },
  /** What every card that waits for the user says once answered, until the server has it. */
  card: {
    answering: 'Sending your answer…'
  },
  /** A list or a page whose read failed, in its own place. */
  lists: {
    again: 'Read again',
    failed: (error: string) => `It could not be read: ${error}`
  },
  ask: {
    other: 'Other',
    otherPlaceholder: 'Your answer',
    pickSome: 'Choose any',
    skip: 'Skip',
    submit: 'Answer',
    title: (count: number) => (count === 1 ? 'The agent asks' : `The agent asks ${count} questions`)
  },
  secret: {
    byBot: (bot: string) => `For the bot ${bot}; every bot of this home reads this key.`,
    hide: 'Hide',
    overwrite: (when: string) => `A key is already stored, last written ${when}. Pasting replaces it.`,
    placeholder: 'Paste the key',
    save: 'Save',
    show: 'Show',
    skip: 'Skip',
    sharedWithBots: 'Every bot of this home reads this key.',
    title: (name: string) => `Atlas asks for the key ${name}`
  },
  bots: {
    advanced: 'Advanced',
    cancel: 'Cancel',
    confirmDelete: 'Delete it, with its memory and skills?',
    create: 'Create',
    delete: 'Delete',
    description: 'Description',
    descriptionPlaceholder: 'What should this bot help with?',
    edit: 'Edit',
    editTitle: 'Edit bot',
    empty: 'No bots yet. A bot is an agent with its own mission, memory and conversation.',
    reading: 'Reading the bots…',
    emptyChat: (title: string) => `This is your conversation with ${title}. It never starts over.`,
    everything: 'Everything the home has',
    intro: 'A named agent with its own mission, memory, skills, conversation and routines.',
    mcp: 'MCP servers',
    mission: 'Mission and rules',
    missionHint: 'Blank: composed from the title and the description',
    model: 'Model',
    modelOfHome: "The home's model",
    name: 'Name',
    new: 'New bot',
    newTitle: 'New bot',
    nothingToPick: 'The home has none.',
    routines: (count: number) => (count === 1 ? '1 routine' : `${count} routines`),
    save: 'Save',
    skills: 'Skills of the library',
    tab: 'Bots',
    title: 'Title',
    tools: 'Tools'
  },
  attachments: {
    add: 'Attach files',
    drop: 'Drop files to attach them',
    failed: (name: string, why: string) => `${name} was not attached: ${why}`,
    open: (name: string) => `Open ${name}`,
    remove: (name: string) => `Remove ${name}`,
    size: (bytes: number) =>
      bytes < 1024
        ? `${String(bytes)} B`
        : // By the kilobytes as shown: just under a megabyte reads 1.0 MB, never 1024 KB.
          Math.round(bytes / 1024) < 1024
          ? `${(bytes / 1024).toFixed(0)} KB`
          : `${(bytes / 1024 / 1024).toFixed(1)} MB`,
    tooHeavy: (name: string, most: string) => `${name} is too heavy: this kind of file takes at most ${most}.`,
    uploading: (percent: number) => `Sending… ${String(percent)}%`,
    waiting: 'Wait for the files to finish sending'
  },
  composer: {
    slashCommands: 'Commands and skills',
    slashGroups: { command: 'Commands', skill: 'Skills' } as Record<'command' | 'skill', string>,
    autoMode: 'Auto mode: skip the guardian and the questions. The floors still block.',
    auto: 'Auto',
    autoModeLabel: 'Auto mode',
    defaulted: (label: string) => `${label} · default`,
    effort: 'Effort: how hard the model thinks (for this conversation)',
    effortLevel: (level: string) => EFFORTS[level] ?? level,
    effortTitle: 'Effort',
    defaultBadge: 'Default',
    noModelMatch: 'No model matches',
    searchModels: 'Search models',
    model: 'Model (for this conversation, from its next turn)',
    noModel: 'No model: store a key with atlas secrets set',
    placeholder: 'Message Atlas',
    placeholderRunning: 'Steer the turn: this enters after the tools in course',
    placeholderTo: (name: string) => `Message ${name}`,
    send: 'Send',
    stop: 'Stop'
  },
  connection: {
    closed: 'Atlas is not answering. Trying again…',
    connecting: 'Connecting to Atlas…',
    failed: (reason: string) => `Could not reach Atlas: ${reason}`,
    /** The window before its home's server first answered. */
    opening: 'Opening Atlas…'
  },
  conversations: {
    /** Archiving one, and the Archived section under the list (step 9e). */
    archive: 'Archive',
    archived: (count: number) => `Archived (${String(count)})`,
    archivedMark: 'archived',
    archiveLater: 'Archive it once its turn ends',
    empty: 'No conversations yet.',
    reading: 'Reading the conversations…',
    /** The list's next page, read as it scrolls near its end (step 9e). */
    readingMore: 'Reading more conversations…',
    new: 'New conversation',
    /** A row's menu, and its name given in place (step 9e). */
    menu: (title: string) => `${title}: rename or archive`,
    nameField: (title: string) => `New name for ${title}`,
    notArchived: (reason: string) => `Not archived: ${reason}`,
    notRenamed: (reason: string) => `Not renamed: ${reason}`,
    notUnarchived: (reason: string) => `Not brought back: ${reason}`,
    readingArchived: 'Reading the archived conversations…',
    rename: 'Rename',
    /** The field above the list, and what it found (step 9e). */
    search: {
      atlas: 'Atlas',
      clear: 'Clear the search',
      field: 'Search conversations',
      found: (count: number, more: boolean) =>
        more
          ? `Showing the first ${count}. Add a word to narrow them.`
          : count === 1
            ? '1 conversation'
            : `${count} conversations`,
      none: (query: string) => `No conversation holds “${query}”.`,
      results: 'Conversations found',
      searching: 'Searching the conversations…',
      you: 'You'
    },
    showMore: 'Show more',
    tab: 'Conversations',
    unarchive: (title: string) => `Bring ${title} back to the list`,
    untitled: 'New conversation',
    via: (source: string) => `via ${source}`
  },
  /** The tabs above the central area. */
  /** The start screen a new tab opens on: what each of its cards is for. */
  start: {
    atlas: 'Talk to Atlas: a new conversation, or one you had.',
    bots: 'Agents with their own mission, memory and conversation.',
    executors: 'Coding agents working in your folders, one session at a time.'
  },
  tabs: {
    openFolder: 'Open the folder',
    terminal: 'Terminal (Ctrl+`)',
    close: (title: string) => `Close ${title}`,
    label: 'Open conversations and sessions',
    /** The button after the tabs: the start screen, where a new conversation, bot or session is
     * chosen, in a tab of its own once it is made. */
    new: 'New tab',
    waiting: (count: number) => (count === 1 ? '1 waiting for you' : `${count} waiting for you`)
  },
  homes: {
    backedUp: (path: string, size: string) => `Backed up to ${path} (${size}). Keys are never in a backup.`,
    backingUp: 'Backing up…',
    backup: 'Back up',
    backupTitle: 'Save a backup of this home: one zip with its settings, memory, skills and conversations',
    cancel: 'Cancel',
    checking: 'Checking the backup…',
    clone: 'Copy from',
    cloneHint: 'Takes its settings, models, MCP servers, keys, profile, memory and skills. Conversations start empty.',
    cloneNone: 'None (an empty home)',
    create: 'Create',
    creating: 'Creating…',
    dismiss: 'Close',
    emptyHint: 'Starts empty: Atlas will ask for a model key when it opens.',
    import: 'Import',
    importAction: 'Import',
    importFrom: (home: string, when: string, atlas: string, conversations: number) =>
      `A backup of the home ${home}, taken ${when} by Atlas ${atlas}, with ${String(conversations)} ${
        conversations === 1 ? 'conversation' : 'conversations'
      }.`,
    importHint:
      'It becomes a new home, and the home open now stays as it is. Keys are never in a backup, so the new home asks for one when it opens.',
    importing: 'Importing…',
    importTitle: 'Import a backup',
    importTooltip: 'Make a new home from a backup',
    inHome: (conversation: string, home: string) => `${conversation} (home ${home})`,
    intro: 'Each home is an Atlas of its own: its own settings, keys, memory, skills and conversations.',
    label: 'Home',
    name: 'Name',
    nameHint: 'Lowercase letters, digits, - and _',
    newHome: 'New home',
    newName: 'Name of the new home',
    newTitle: 'New home',
    remote:
      'This app talks to a server on another machine: make and import its homes in its terminal (atlas home create, atlas restore).',
    editProfile: 'Edit home',
    homes: 'Homes',
    menu: (home: string) => `${home}: switch home or edit it`,
    picture: 'Choose a picture',
    pictureRemove: 'Remove the picture',
    pictureUnreadable: 'That file is not an image this app can read. Choose a PNG, JPEG, GIF or WebP.',
    profileTitle: 'This home',
    profileHint: 'Its picture and name show at the foot of the sidebar. A backup carries the picture.',
    switchTo: 'Switch to another home',
    zipFilter: 'Atlas backup'
  },
  firstSteps: {
    count: (done: number, all: number) => `${String(done)} of ${String(all)}`,
    hide: 'Hide',
    routinePrompt: 'Every Monday at 9am, remind me to review what I have for the week.',
    start: 'Start',
    tasks: {
      bot: 'Create a bot',
      delegate: 'Hand a task to an executor',
      routine: 'Schedule a routine',
      store: 'Install something from the store',
      telegram: 'Talk to Atlas on Telegram'
    },
    title: 'First steps'
  },
  connectionOffer: {
    missing: 'This connection is not installed here: the marketplace has it.',
    title: (label: string) => `Connect ${label}`
  },
  firstRun: {
    accounts: 'More than one account?',
    accountsIntro:
      "The Accounts plugin runs your coding agents on your other accounts: when one reaches its plan's limit, the session goes on in the next by itself.",
    accountsPlugin: 'Turn on the Accounts plugin',
    bring: 'Bring my sessions from',
    bringAll: 'Bring everything',
    bringCount: (count: number) => (count === 1 ? 'Bring 1 session' : `Bring ${String(count)} sessions`),
    bringNote: 'The rest goes to Archived, a click away. Nothing changes in the originals.',
    away: (step: string) => `The first run waits on ${step}.`,
    awayBack: 'Back to it',
    botFather: 'In Telegram, talk to @BotFather, send /newbot, choose a name, and copy the token it gives you.',
    botRunning: 'Your bot is on. Send it a message in Telegram: it answers with a code, and you show up here.',
    botStarting: 'Starting your bot…',
    connect: 'Connect',
    continue: 'Continue',
    github: 'GitHub',
    google: 'Gmail and Calendar',
    turnOn: 'Turn on',
    letIn: 'Let in',
    letInDone: (name: string) => `${name} is in: talk to Atlas from Telegram.`,
    letInWaits: (name: string, code: string) => `${name} waits with ${code}`,
    telegramMissing: 'This Atlas has no Telegram plugin.',
    telegramOn: 'Connecting lets the Telegram plugin:',
    tokenLabel: "The bot's token",
    counting: 'Counting…',
    hints: {
      connections: 'GitHub, Gmail, Calendar',
      executors: 'Claude Code, Codex',
      model: 'An API key',
      telegram: 'Atlas on your phone',
      work: 'Last step'
    },
    inProgress: 'In progress',
    intros: {
      connections: 'Atlas works in your accounts through their own programs, each logged in on this machine.',
      executors: 'Atlas works through the coding agents you already use, in your folders, each with your own login.',
      model:
        'Atlas talks to a model with your own API key. Paste one to start: it is checked with the provider, ' +
        'at no cost, and kept encrypted on this machine.',
      telegram: "Talk to Atlas from your phone through a bot of your own, and answer your agents' questions there.",
      work: 'Your sessions stay where they are. Choose what shows in the list; the rest waits in Archived.'
    },
    leave: 'Go to Atlas',
    logIn: 'Log in',
    marks: { done: 'Done', skipped: 'Skipped' },
    noExecutors: 'No executor is on, so there is nothing to bring: turn one on in the step before, or start here.',
    reopen: 'Open this again any time from Settings → General → First run.',
    setup: 'First run',
    skip: 'Skip',
    start: 'Start a conversation',
    stepOf: (step: number, total: number) => `Step ${String(step)} of ${String(total)}`,
    steps: {
      connections: 'Connections',
      executors: 'Executors',
      model: 'Model',
      telegram: 'Telegram',
      work: 'Bring your work'
    },
    titles: {
      connections: { lead: 'Connect your', word: 'accounts' },
      executors: { lead: 'Connect your', word: 'agents' },
      model: { lead: 'Choose the', word: 'model' },
      telegram: { lead: 'Atlas in your', word: 'pocket' },
      work: { lead: 'Bring your', word: 'work' }
    },
    windowAll: 'from any time',
    windowDays: (days: number) => `from the last ${String(days)} days`,
    windowLabel: 'Since when'
  },
  keys: {
    band: 'No model provider is connected, so Atlas cannot answer yet.',
    bandAction: 'Connect one',
    checking: 'Checking the key',
    connect: 'Connect',
    continue: 'Continue',
    describe: {
      anthropic: 'Direct access to Claude models, with their reasoning kept across tools.',
      gemini: 'Direct access to Google Gemini models.',
      openai: 'Direct access to OpenAI models.',
      openrouter: 'Hundreds of models behind a single key. A good first choice.'
    } as Record<string, string>,
    failed: (error: string) => `The key was not saved: ${error}`,
    getKey: 'Get a key',
    intro:
      'Atlas talks to a model with your own API key. Paste one to start: it is checked with the provider, ' +
      'at no cost, and kept encrypted on this machine.',
    later: "I'll choose later",
    placeholder: 'Paste the API key',
    saved: 'The key is saved.',
    stored: 'connected',
    title: 'Connect a model provider'
  },
  notification: {
    approval: 'Atlas asks for your approval',
    asks: 'Atlas has a question',
    done: 'Atlas answered',
    executor: (name: string) => `${name} is waiting for you`,
    secret: (name: string) => `Atlas asks for the key ${name}`
  },
  proposals: {
    adds: 'Adds',
    after: 'After',
    applied: 'Applied.',
    approve: 'Approve',
    back: 'Back to the conversation',
    before: 'Before',
    discard: 'Discard',
    empty: 'No memory proposals.',
    fromConversation: 'From the conversation',
    intro:
      'The background review would change these entries of the memory. Nothing changes until you approve; ' +
      'a change applies only while the entries it changes are as they were.',
    ok: 'OK',
    overwrote: 'Overwrote, whole:',
    readd: 'Ask Atlas to add back anything you still want.',
    removed: 'Removed:',
    removes: 'Removes',
    replaces: 'Changes',
    review: 'Review',
    title: 'Memory proposals',
    titleBotMemory: (bot: string) => `Change to the memory of the bot ${bot}`,
    titleMemory: 'Change to the memory',
    titleUser: 'Change to your profile',
    whole: 'See before and after in full'
  },
  extensions: {
    added: 'Added here',
    copied: 'Copied',
    copy: 'Copy the command',
    fromPlugin: 'From a plugin that is on',
    fromPlugins: 'From plugins',
    inMcpFile: 'In mcp.json',
    intro: 'What this home adds to Atlas, by kind. Find more in the Marketplace.',
    mcpIntro:
      'The servers in mcp.json, in the home folder, and those of the plugins that are on. The model finds their tools by search.',
    mcpState: (state: string, tools: number): string =>
      state === 'connected'
        ? tools === 1
          ? 'Connected · 1 tool'
          : `Connected · ${tools} tools`
        : state === 'connecting'
          ? 'Connecting…'
          : 'Left out',
    mcpSummary: (transport: string): string =>
      transport === 'stdio'
        ? "A program Atlas starts on the home's machine."
        : 'A server Atlas reaches at its address.',
    modelOnly: 'The model loads it when it needs it',
    noExecutors: 'No executors yet. Atlas looks for the harnesses it supports each time it opens.',
    noMatch: 'Nothing matches the search.',
    noMcp: 'No MCP servers. Add one to mcp.json, in the home folder, or install one from the Marketplace.',
    noSkills: 'No skills yet. Atlas writes one when it learns how you do something; the Marketplace has more.',
    reading: 'Reading…',
    refresh: 'Read again',
    search: 'Search',
    signIn: "It signs in with OAuth. Sign in from a terminal on the home's machine:",
    signInAgain: "It signs in with OAuth. To sign in again, in a terminal on the home's machine:",
    signInAgainButton: 'Sign in again',
    signInButton: 'Sign in',
    signInNeeded: (server: string): string => `${server} waits for you to sign in.`,
    signInOrTerminal: "Or from a terminal on the home's machine:",
    signInWaiting: 'Authorize Atlas in the browser that opened. It connects as soon as you do.',
    skillSource: (source: string, plugin: string): string =>
      source === 'atlas' ? 'Ships with Atlas' : source === 'plugin' ? `From the plugin ${plugin}` : "This home's own",
    skillsIntro: 'Procedures the model loads by name when it needs them; the ones you may call show their /command.',
    title: 'Extensions',
    yours: "This home's own"
  },
  plugins: {
    approve: 'Let in',
    pageGone: 'This page belonged to a plugin that is off now.',
    screenFailed: (name: string, why: string) => `The plugin screen ${name} failed: ${why}`,
    screenNotLoaded: (why: string) => `Its screen did not load: ${why}`,
    approved: 'let in',
    cancel: 'Cancel',
    consentAgain: 'An update changed what it may do, so it waits for your consent again: read it and turn it on.',
    consentIntro: 'Turning it on lets it:',
    defaultValue: (value: string) => `Default: ${value}`,
    empty: 'No plugins. Atlas ships some, and you can add one as a folder in the home, in plugins/.',
    enable: 'Turn on…',
    enableNow: 'Turn on',
    disable: 'Turn off',
    intro:
      'Plugins add to Atlas. They arrive turned off; turning one on shows what it may do, and nothing runs before you agree.',
    off: 'off',
    on: 'on',
    people: 'People',
    peopleIntro:
      'Who may talk to Atlas through a channel. Someone new gets a code; let them in here, and they send their message again.',
    noPeople: 'Nobody yet.',
    other: 'Other…',
    otherHint: 'Type a value that is not on the list',
    list: 'List',
    listHint: 'Pick from the list again',
    reload: 'Reload',
    reloadHint: 'Load the plugins that are on again from their files. Atlas also does it when a file of one is saved.',
    remove: 'Remove',
    reset: 'Default',
    running: 'running',
    save: 'Save',
    secretPlaceholder: 'Paste it here',
    settings: 'Settings',
    shipped: 'ships with Atlas',
    stored: 'stored',
    notStored: 'not stored',
    changeSecret: 'Change',
    title: 'Plugins',
    guide: 'Guide',
    write: 'Write a plugin',
    writeHint: 'A new conversation where Atlas writes a plugin with you, checks it and installs it.',
    waits: (code: string) => `waits with ${code}`,
    waiting: (count: number) => (count === 1 ? '1 waits' : `${count} wait`)
  },
  routineForm: {
    auto: 'Run it in its automatic mode, which asks nothing',
    bot: 'Bot',
    cancel: 'Cancel',
    draft: 'Open a draft pull request of what it changed (GitHub plugin)',
    editTitle: 'Change the routine',
    effort: 'Effort',
    effortOfHome: "The model's default",
    efforts: { high: 'High', low: 'Low', medium: 'Medium' },
    executor: 'Executor',
    folder: 'Folder',
    model: 'Model',
    modelOfHome: "The home's model",
    name: 'Name',
    newTitle: 'New routine',
    probe: 'Probe',
    probeHint: 'optional: a command whose new output wakes it',
    prompt: 'What to do',
    runner: 'Who runs it',
    runners: { atlas: 'Atlas', bot: 'A bot', executor: 'A coding agent, in a folder' },
    save: 'Save',
    saveAndRun: 'Save and run now',
    schedule: 'When',
    scheduleHint: 'every day at 9am, every 30m, weekdays at 8am',
    unwatched: 'It runs with nobody watching: its first question stops the run, which then waits for you in Executors.',
    workspace: 'Each run in a new workspace (for a routine that changes code)'
  },
  templates: {
    add: 'Add',
    agentRuns: 'Runs in your coding agent',
    atlasRuns: "Runs on Atlas's model",
    effort: (level: string) => `effort ${level}`,
    groups: { personal: 'Personal', project: 'For a project' },
    install: (plugin: string) => `Install ${plugin} first`,
    intro: 'Routines written by Atlas, ready to adjust: Add opens one in the form.',
    needs: (plugin: string) => `Needs the ${plugin} plugin`,
    tab: 'Templates',
    tiers: { essential: 'Essential', recommended: 'Recommended', useful: 'Useful' },
    words: {
      bills: {
        name: 'bills',
        prompt:
          'Read my email of the last week for bills and payment reminders, and list what is due this week, each with the amount and the date. If nothing is due, answer [SILENT].',
        title: 'Bills due this week'
      },
      dependencyUpdates: {
        name: 'dependencies',
        prompt:
          "Update this project's dependencies that have a new minor or patch version, run the tests after each, and keep only what passes. List what you updated, and what you left and why.",
        title: 'Dependency updates'
      },
      failingChecks: {
        name: 'failing-checks',
        prompt:
          "With gh, list this project's pull requests and main branch runs whose checks failed in the last day: each with the failing check and the first line of its error. If none failed, answer [SILENT].",
        title: 'Checks that failed'
      },
      newsDigest: {
        name: 'news',
        prompt:
          "Search the web for today's news on the subjects my memory says I follow, and give me five headlines, each with one line and its link.",
        title: 'Morning news'
      },
      releaseNotes: {
        name: 'release-notes',
        prompt:
          "Read the commits merged this week and write release notes for this project's users: what is new, what changed and what was fixed, in plain words. Change no file.",
        title: 'Release notes draft'
      },
      weeklyReview: {
        name: 'week',
        prompt:
          "Look back at my week, my calendar and the routines' results, and write me a short review: what got done, what slipped, and what to start Monday with.",
        title: 'Weekly review'
      },
      agenda: {
        name: 'tomorrow',
        prompt:
          'Read my calendar for tomorrow and tell me, in a few lines, what I have and when, what to prepare tonight, and any clash. If tomorrow is empty, answer [SILENT].',
        title: "Tomorrow's agenda"
      },
      backup: {
        name: 'backup',
        prompt: 'Run `atlas backup`. If it worked, answer [SILENT]; if it failed, say why and what to do.',
        title: 'Daily backup'
      },
      mailSummary: {
        name: 'mail',
        prompt:
          'Read my unread email since yesterday and give me the ones that need an answer from me, each in a line with who and what, then the rest in one line. Answer nothing else.',
        title: 'Morning email summary'
      },
      missingTests: {
        name: 'tests',
        prompt:
          'Find the code changed this week that no test covers. Write the tests it lacks, following how this project tests, and run them. Do not change the code under test; list what you could not cover and why.',
        title: 'Missing tests'
      },
      securitySweep: {
        name: 'security',
        prompt:
          "Look for security problems in this project: its dependencies' known vulnerabilities, secrets committed by mistake, and unsafe code (injection, unchecked input). Change nothing: list what you found, the worst first, each with where and how to fix it.",
        title: 'Security sweep'
      },
      stalePullRequests: {
        name: 'stale-prs',
        prompt:
          "With gh, list this project's open pull requests with no activity for 3 days or more: each with its title, its author, how long it waits and what it waits for. If there are none, answer [SILENT].",
        title: 'Pull requests waiting for 3 days'
      }
    }
  },
  routines: {
    confirmRemove: 'Remove it?',
    empty: 'No routines yet. Ask Atlas to schedule one, or use atlas routines create in the terminal.',
    failed: (name: string) => `Routine ${name} failed`,
    finished: (name: string) => `Routine ${name} finished`,
    hideRuns: 'Hide runs',
    intro:
      'Prompts and commands Atlas runs on a schedule. Each run of a prompt is a conversation of its own; you are notified when one ends, unless it had nothing to say.',
    last: (status: string) => `last ${status}`,
    late: 'late',
    next: (time: string) => `next ${time}`,
    noRuns: 'It has not run yet.',
    open: 'Open',
    pause: 'Pause',
    paused: 'paused',
    executor: (name: string, folder: string, workspace: boolean) =>
      `Run by ${name} in ${folder}${workspace ? ', a new workspace each run' : ''}`,
    edit: 'Change',
    newRoutine: 'New routine',
    scoreboard: (alone: number, needed: number, failed: number) =>
      `This week: ${String(alone)} ran alone · ${String(needed)} needed you · ${String(failed)} failed`,
    probe: (command: string) => `Woken by its probe: $ ${command}`,
    probed: 'its probe heard something',
    remove: 'Remove',
    resume: 'Resume',
    runNow: 'Run now',
    showRuns: 'Runs',
    status: (status: string) => STATUSES[status] ?? status,
    title: 'Routines'
  },
  marketplace: {
    back: 'All items',
    brought: (names: string) => `Also installed, since it needs them: ${names}.`,
    mostInstalled: 'Most installed',
    seeAll: (count: number) => `See all ${count.toLocaleString('en')}`,
    unfiled: 'No category',
    provenance: (source: string) => `${source}: read straight from it, not reviewed for Atlas.`,
    allCategories: 'All',
    by: (author: string) => `by ${author}`,
    category: 'Category',
    categories: {
      automation: 'Automation',
      database: 'Databases',
      deployment: 'Deployment',
      design: 'Design',
      development: 'Development',
      learning: 'Learning',
      location: 'Location',
      math: 'Math',
      migration: 'Migration',
      monitoring: 'Monitoring',
      productivity: 'Productivity',
      security: 'Security',
      testing: 'Testing'
    },
    cancel: 'Cancel',
    community: 'Community',
    confirmRemove: 'Remove it?',
    empty: 'Nothing here yet.',
    entry: 'What goes into your mcp.json',
    homepage: 'Home page',
    install: 'Install',
    installed: 'Installed',
    installing: 'Installing…',
    installs: (count: number) => `${count.toLocaleString('en')} installs`,
    intro:
      "Skills, MCP servers, plugins and executors for Atlas: the marketplace's own, each reviewed and installed exactly as reviewed, then what the public catalogs list, read straight from them.",
    kinds: { executor: 'Executor', mcp: 'MCP server', plugin: 'Plugin', skill: 'Skill' },
    license: (license: string) => `License ${license}`,
    localChanges: (name: string) =>
      `${name} was changed here since it came. Update it anyway? What it holds now is kept as a version you can bring back.`,
    loadingPage: 'Reading its page…',
    makeHint: 'Atlas writes a plugin with you in a new conversation; the guide says how to publish it here.',
    makeYourOwn: 'Make your own',
    guide: 'Plugin guide',
    need: {
      program: (name: string, state: string) =>
        state === 'met' ? `${name}: on this computer` : `${name}: not found on this computer (it installs anyway)`,
      server: (name: string, state: string) =>
        state === 'met'
          ? `MCP server ${name}: already here`
          : state === 'brought'
            ? `MCP server ${name}: installed along with it`
            : `MCP server ${name}: missing, and the marketplace does not have it`,
      system: (names: string, state: string) => (state === 'met' ? `Works here (${names})` : `Works only on ${names}`)
    },
    needs: 'What it needs',
    noMatch: 'Nothing matches the search.',
    official: 'By the Atlas team',
    officialBadge: 'Official',
    on: 'On',
    paste: 'Paste the value',
    reading: 'Reading the marketplace…',
    refresh: 'Read again',
    remove: 'Remove',
    removing: 'Removing…',
    retired: 'Retired',
    save: 'Save',
    screenshots: 'Screenshots',
    search: 'Search',
    secretMissing: 'Not stored',
    secretStored: 'Stored',
    secrets: 'Secrets it reads',
    shipped: 'Comes with Atlas',
    source: 'Source code',
    sources: {
      'claude-plugins': "From Claude Code's plugin directory",
      'mcp-registry': 'From the MCP Registry',
      'skills.sh': 'From skills.sh'
    },
    stale: (problem: string, when: string) =>
      `The marketplace could not be read now (${problem}). This is how it was on ${when}.`,
    tabs: { executor: 'Executors', mcp: 'MCPs', plugin: 'Plugins', skill: 'Skills' },
    title: 'Marketplace',
    turnOn: 'Turn on',
    turnOnHint: 'Plugins arrive off. Turning one on shows what it declares, for your consent.',
    unavailable: 'Unavailable',
    unread: (problem: string) => `The marketplace could not be read: ${problem}`,
    update: 'Update',
    updateAnyway: 'Update anyway',
    updated: (date: string) => `updated ${date}`,
    updating: 'Updating…',
    version: (version: string) => `Version ${version}`
  },
  soul: {
    blank: "Blank, so Atlas's default stands in:",
    intro:
      'Who Atlas is and how it speaks, in every conversation: its SOUL.md. Write it in your own words, or ask Atlas to change it. A change applies from the next conversation.',
    loading: 'Reading SOUL.md…',
    missing: 'There is no SOUL.md yet. Write it below and save to make one; until then, Atlas is its default.',
    placeholder: "Who Atlas is and how it speaks. Leave blank to keep Atlas's default.",
    save: 'Save SOUL.md',
    saved: 'Saved. It applies from the next conversation.',
    saving: 'Saving…',
    title: 'Soul',
    unsaved: 'Unsaved changes'
  },
  settings: {
    about: 'About',
    firstRun: 'First run',
    firstRunHint: 'The steps Atlas opened on: the model, the executors, and the sessions the list shows.',
    firstRunOpen: 'Open the first run again',
    aboutYou: 'About you',
    aboutYouHint:
      'Who you are, what you prefer and how you work: USER.md. Atlas reads it in every conversation and adds to it as it learns.',
    add: 'Add',
    addPlaceholder: 'A new entry',
    appliesNext: 'A change applies from the next conversation; an open one keeps what it started with.',
    approvalTimeout: 'Approval wait',
    approvalTimeoutHint: 'Seconds an approval waits for you before it counts as refused.',
    ask: 'Always ask first',
    askHint: 'Commands that ask you even in auto. One per line, * as a wildcard: git push*',
    askSkip: "Left out of Atlas's own rules",
    askSkipHint: 'Commands Atlas would ask about on its own that you allow: gh issue comment*',
    automatic: 'The first provider with a key',
    cancel: 'Cancel',
    checkUpdate: 'Look for an update',
    checking: 'Looking…',
    choose: 'Choose…',
    close: 'Close',
    defaultModel: 'Default model',
    defaultModelHint:
      "What a conversation that picked no model answers with, from its next turn. A conversation's own pick stays.",
    deny: 'Blocked commands',
    denyHint: 'Never run, not even in auto. One per line, * as a wildcard: git push --force*',
    edit: 'Edit',
    effort: 'Effort',
    effortDefault: "The model's default",
    empty: 'Nothing yet.',
    folder: 'Starting folder',
    folderHint: 'Where the agent starts, in new conversations. Blank, your home folder:',
    keys: 'Provider keys',
    keysHint: 'Each key is checked with its provider at no cost and kept encrypted on the machine of this home.',
    language: 'Language',
    languageHint: 'Of this computer. The window reloads to change it; no conversation stops.',
    languageSystem: "The system's",
    languages: { en: 'English', 'pt-BR': 'Português' } as Record<string, string>,
    limit: 'Limit, in characters',
    limitHint:
      'Every model call carries it whole, so a larger limit costs more each time. From 500 to 20,000; lowering it below what is written cuts nothing: Atlas makes room before it adds.',
    locked: (variable: string) => `Set by ${variable}, which wins over config.toml.`,
    memory: 'Memory',
    memoryHint: 'What Atlas noted about your machine, your tools and their quirks: MEMORY.md.',
    model: 'Model',
    noUpdates: 'This installation does not update from the app.',
    proposals: (count: number) =>
      count === 1 ? '1 memory proposal waiting' : `${String(count)} memory proposals waiting`,
    provider: 'Provider',
    remove: 'Remove',
    save: 'Save',
    saved: 'Saved',
    saving: 'Saving…',
    sections: {
      general: 'General',
      machines: 'Machines',
      model: 'Model',
      personalization: 'Personalization',
      safety: 'Safety',
      voice: 'Voice',
      workspace: 'Folder and time zone'
    },
    theme: 'Look',
    themeDark: 'Dark',
    themeHint: 'Of this computer.',
    themeLight: 'Light',
    themeSystem: "The system's",
    title: 'Settings',
    upToDate: 'Atlas is up to date.',
    update: 'Update',
    updateAvailable: (count: number) =>
      count === 1 ? 'An update is available: 1 change.' : `An update is available: ${String(count)} changes.`,
    used: (used: number, limit: number) => `${used.toLocaleString('en')} of ${limit.toLocaleString('en')} characters`,
    version: (version: string) => `Atlas ${version}`,
    zone: 'Time zone of routines',
    zoneHint: "An IANA name, such as America/Sao_Paulo. Blank, the system's."
  },
  executor: {
    allow: 'Allow',
    answer: 'Answer',
    asks: (name: string) => `${name} asks`,
    deny: 'Deny',
    noReset: "The plan's limit came, and it did not say when it resets: go on when you want.",
    other: 'Other',
    reason: 'Why not? (optional, it reads it)',
    resumesAt: (time: string) => `The plan's limit came: it goes on by itself at ${time}.`,
    skip: 'Skip',
    wantsTo: (name: string, tool: string) => {
      const act = EXECUTOR_ACTS[tool]

      return act ? `${name} wants to ${act}` : `${name} wants to use ${tool}`
    }
  },
  dictation: {
    again: 'Write it down again',
    anywhere: 'anywhere',
    cancel: 'Cancel',
    change: 'Change the key',
    choose: 'Choose a key',
    copied: 'This program takes no paste: the text is copied, paste it with Ctrl+V',
    copy: 'Copy',
    copyDone: 'Copied',
    dictate: 'Dictate: click to record, click again to write it in the box',
    failed: (why: string) => `The dictation failed: ${why}`,
    inBox: 'in the box',
    listen: 'Listen',
    none: 'No dictation yet.',
    notWritten: (why: string) => `Not written down: ${why}`,
    nothing: 'Nothing was heard',
    page: 'Dictations',
    pageIntro: (key: string) =>
      key
        ? `Press ${key} in any program to record, and again for the words to land where you are. The dictations are kept here, with their audio.`
        : "Dictation anywhere is off: choose a key, and press it in any program to dictate there. The box's dictations are kept here too, with their audio.",
    pasted: 'Done',
    pressKey: 'Press the key…',
    recording: (key: string) => `Recording: ${key} to stop`,
    recordingBox: 'Recording: click to stop',
    remove: 'Delete',
    shortcut: 'Key',
    shortcutFailed: (why: string) => `The key cannot be used: ${why}`,
    transcribing: 'Writing down what you said…',
    turnOff: 'Turn off',
    writing: 'Writing down what you said…'
  },
  speech: {
    ask: (size: string) =>
      `A recording came. Atlas writes recordings down on this computer, with a model downloaded once (${size} MB).`,
    download: 'Download',
    downloadSize: (size: string) => `Download the model (${size} MB)`,
    downloading: (done: string, total: string) => `Downloading the speech model: ${done} of ${total} MB…`,
    local: 'This computer',
    model: 'The model on this computer',
    modelHint: (credit: string) => `Recordings stay on the computer that runs Atlas. ${credit}.`,
    notNow: 'Not now',
    off: 'Nobody',
    present: 'Downloaded: recordings are written down here.',
    transcription: 'Who writes recordings down',
    transcriptionHint:
      'The audio you send, on any channel, and what you dictate. OpenAI and Gemini use the key stored for them in Model.'
  },
  subagents: {
    button: (agents: number, tasks: number) =>
      [
        agents === 0 ? '' : agents === 1 ? '1 agent' : `${agents} agents`,
        tasks === 0 ? '' : tasks === 1 ? '1 task' : `${tasks} tasks`
      ]
        .filter(Boolean)
        .join(' · '),
    close: 'Close',
    hint: (count: number) =>
      `${count === 1 ? '1 subagent working' : `${count} subagents working`} · click one for details`,
    map: 'Agent map',
    noOutput: 'No output yet.',
    noTool: 'starting',
    output: 'Output',
    parent: 'Atlas, this conversation',
    refused: 'It no longer takes corrections: its result is on the way.',
    send: 'Send',
    steer: 'Steer',
    steerPlaceholder: 'A course correction: it enters after the tools in course',
    stop: 'Stop',
    taskGone: 'It had already ended.',
    tasks: (count: number) =>
      `${count === 1 ? '1 background task' : `${count} background tasks`} · click one for details`,
    taskStatus: (status: string, code: null | number) =>
      ({
        completed: 'Completed',
        failed: `Failed · exit code ${String(code)}`,
        running: 'Running',
        stopped: 'Stopped'
      })[status] ?? status,
    tools: (count: number) => (count === 1 ? '1 tool' : `${count} tools`)
  },
  executors: {
    account: 'Change account',
    accountLabel: 'The account to log it into (its email)',
    atlasAnswers: 'Atlas answers',
    atlasAnswersNote: 'Atlas answers what it is sure of; the rest still comes to you.',
    auto: 'Automatic',
    autoNote: "It asks no permission, so Atlas's guardian is passed over.",
    autoWarning: (title: string) =>
      `${title} will approve its own permission requests in this session, without asking you: it may run commands and edit files. Its classifier still reviews each action, and your ${title} settings stay as they are.`,
    autoWarningCancel: 'Keep asking',
    autoWarningConfirm: 'Approve automatically in this session',
    autoWarningRules: (title: string) =>
      `It follows its own rules and your ${title} settings, so it may commit and push to this project's repository. To keep a command asking you, add an ask rule to your ${title} settings.`,
    archive: (folder: string) => `Archive ${folder}: hide it from the list`,
    archivedFolders: (count: number) => `Archived folders (${String(count)})`,
    archivedSessions: (count: number) => `Archived sessions (${String(count)})`,
    archiveSession: (title: string) => `Archive ${title}: hide it from the list`,
    deleteSession: (title: string) => `Delete ${title}`,
    deleteSessionNote: 'Delete this session? Its files leave this account for good; the other accounts keep theirs.',
    deleteSessionYes: 'Delete',
    unarchiveSession: (title: string) => `Bring ${title} back to the list`,
    byAtlas: 'by Atlas',
    cancel: 'Cancel',
    choose: 'Choose…',
    commands: 'Its commands',
    confirm: 'OK',
    confirmTurnOn: 'Turn it on',
    copyNote:
      'Your next message starts a copy: a session of its own, with everything until now. The original stays as it was.',
    effort: 'Effort: how hard the model thinks (from its next request)',
    elsewhere: (where: string) => `open in ${where}`,
    elsewhereBody:
      'Using it in two places at once can mix up its messages: each goes on from where it was, and later Claude Code follows one and forgets the other.',
    elsewhereCopy: 'Continue in a copy',
    elsewhereHere: 'Continue here anyway',
    elsewhereRead: 'Read only',
    elsewhereTitle: (where: string) => `This session is open in ${where}.`,
    empty: 'No sessions yet.',
    emptyFolder: 'No sessions in this folder.',
    emptySession: (title: string, folder: string) =>
      `A new session of ${title} in ${folder}. Your first message starts it.`,
    expired: (title: string) => `${title} no longer keeps this session.`,
    failed: (error: string) => `They could not be read: ${error}`,
    folder: 'Folder',
    folderPlaceholder: 'The folder, from your working folder or whole',
    harnesses: 'Harnesses on this computer',
    harnessesIntro:
      'Atlas hands work to the coding harnesses you already use, and shows their sessions here. Turn on the ones you want: each runs with your own login.',
    logIn: 'Log in',
    lookAgain: 'Look again: one installed, updated or logged in since is found',
    looking: 'Looking for them…',
    menu: (name: string) => `${name}: account and list`,
    model: 'Model (for this session, from its next request)',
    newSession: 'New session',
    which: 'On which executor',
    newSessionIn: (folder: string) => `New session in ${folder}`,
    noneOn: 'No harness is turned on yet.',
    consentAgain: (title: string) => `${title} waits for your consent again after an update`,
    consentAgainBody: 'Its plugin was updated and declares something new, so it stays off until you agree.',
    reviewConsent: 'Review and turn on',
    notRunning: (title: string) => `${title} is not running`,
    notRunningBody: 'Turn it on, or install it, in Extensions to go on with this session.',
    toExtensions: 'Open Extensions',
    tooOld: (title: string, version: string) => `${title} ${version} is too old for Atlas`,
    update: 'Update',
    updateAvailable: (from: string, to: string) => `Update available: ${from} → ${to}`,
    updateFailed: 'The update did not finish.',
    updateHint: (command: string) => `Runs: ${command}`,
    updated: (version: string) => `Updated to ${version}`,
    updating: (to: string) => `Updating to ${to}…`,
    reading: 'Reading the harnesses…',
    openExtensions: 'Turn one on in Extensions',
    notFound: 'not found on this computer',
    notLoggedIn: 'not logged in',
    on: 'on',
    open: 'Open',
    openTitle: 'Open its session in the executors tab',
    places: { cli: 'a terminal', 'claude-desktop': 'Claude desktop', 'claude-vscode': 'VS Code' } as Record<
      string,
      string
    >,
    placeholder: (title: string) => `Message ${title}`,
    placeholderWorking: 'It is working: this enters the turn running',
    readAgain: 'Read again',
    showArchived: 'Show archived folders and sessions',
    readOnly: 'Read only: nothing is sent from here.',
    readingFolders: 'Reading its folders…',
    readingSessions: 'Reading its sessions…',
    runsAs: (model: string) => `its last turn ran as ${model}`,
    start: 'Open',
    stop: 'Stop',
    tab: 'Executors',
    turnOn: 'Turn on',
    openInEditor: 'Open the project in VS Code',
    unarchive: (folder: string) => `Bring ${folder} back to the list`,
    untitled: 'New session'
  },
  accounts: {
    cancel: 'Cancel',
    logIn: 'Log in',
    login: 'Its email (optional)',
    loginNote: (title: string) => `${title} opens its login on your browser: sign in with this account.`,
    mainAccount: 'Main account'
  },

  route: {
    wentOn: (name: string) => `The plan limit was reached: it went on on ${name}.`
  },

  changes: {
    add: 'Add',
    ahead: (count: number, base: string) =>
      `${count === 1 ? '1 commit' : `${String(count)} commits`} not in ${base} yet`,
    commit: 'Commit',
    commitMessage: 'What these changes do',
    committed: 'Committed on the branch.',
    merge: (base: string) => `Merge into ${base}`,
    mergeNote: (branch: string, base: string) =>
      `${branch} is merged into ${base}, in the project's own folder where ${base} is checked out.`,
    mergeYes: 'Merge',
    merged: (base: string) => `Merged into ${base}.`,
    push: 'Send the branch',
    pushNote: (branch: string) => `${branch} is sent to the project's remote, origin: others with access see it.`,
    pushYes: 'Send',
    pushed: (branch: string) => `${branch} was sent.`,
    binary: 'A binary file.',
    checkpoints: (count: number) => (count === 1 ? '1 checkpoint' : `${String(count)} checkpoints`),
    goBack: 'Go back here',
    goBackNote:
      'The files go back to how they were before this message; how they are now is kept as a checkpoint, to come back to. The session goes on in a copy.',
    goBackYes: 'Go back',
    wentBack: (label: string) =>
      `The files are as they were before “${label}”. Your next message goes on in a copy of the session.`,
    comment: 'Comment on this line',
    commentPlaceholder: 'What should change here? Ctrl+Enter adds it.',
    more: 'More files changed than are shown here.',
    none: 'Nothing changed yet.',
    readAgain: 'Read the changes again',
    reading: 'Reading the changes…',
    removeComment: 'Remove the comment',
    send: (count: number) =>
      count === 1 ? 'Send 1 comment to the agent' : `Send ${String(count)} comments to the agent`,
    statuses: { added: 'new', deleted: 'deleted', renamed: 'renamed' } as Record<string, string>,
    summary: (count: number, base: string) =>
      `${count === 1 ? '1 file' : `${String(count)} files`} changed since ${base}`,
    title: 'Changes',
    truncated: 'The rest of this file is not shown.'
  },
  workspaces: {
    archive: (name: string) => `Archive ${name}: its work kept on its branch, its folder removed`,
    cancel: 'Cancel',
    create: 'Create',
    delete: (name: string) => `Delete ${name}`,
    deleteAnyway: 'Delete anyway',
    deleteNote: (branch: string) => `Its folder and its branch ${branch} are deleted.`,
    deleteYes: 'Delete',
    name: 'Name of the workspace',
    namePlaceholder: 'fix-login',
    new: 'New workspace',
    ports: (ports: string) => `Ports ${ports}`,
    portsSize: 'Ports each',
    portsStart: 'First port',
    projectMenu: (name: string) => `Workspaces of ${name}`,
    save: 'Save',
    script: 'Setup script',
    scriptHint:
      "Runs in each new workspace once it is made, with its ports in ATLAS_WORKSPACE_PORT and ATLAS_WORKSPACE_PORTS. The files listed in the project's .worktreeinclude are copied in first.",
    scriptPlaceholder: 'npm ci',
    setup: 'Prepare new workspaces',
    states: {
      archived: 'archived',
      preparing: 'preparing…',
      setup_failed: 'setup failed'
    } as Record<string, string>,
    title: 'Workspaces',
    unarchive: (name: string) => `Bring ${name} back, its work as it was`,
    undelivered: 'It holds work not delivered, which is lost with it.'
  },
  environment: {
    context: 'Context',
    local: 'Local',
    newWorktree: 'New worktree',
    newWorktreeHint: (branch: string) => `A new session in a workspace of its own, on a branch from ${branch}`,
    title: 'Environment',
    tokens: (count: string) => `${count} tokens`,
    worktree: 'Worktree'
  },
  modes: {
    hints: {
      acceptEdits: 'Takes file edits without asking',
      auto: 'Its own classifier decides what it may do',
      default: 'Always asks before it changes anything',
      plan: 'Makes a plan before it changes anything'
    } as Record<string, string>,
    names: {
      acceptEdits: 'Accept edits',
      auto: 'Automatic',
      default: 'Manual',
      plan: 'Plan'
    } as Record<string, string>,
    title: 'Mode'
  },
  planRing: {
    used: (percent: number, window: string) => `${String(percent)}% used · ${window}`
  },
  executorPanel: {
    active: (on: number, all: number) => (on === all ? String(all) : `${String(on)} of ${String(all)}`),
    empty: 'No MCP servers.',
    failed: (error: string) => `They could not be read: ${error}`,
    hideInactive: 'Hide the ones down',
    loginThere: (title: string) => `An MCP server's login is done in ${title} itself (/mcp).`,
    mcp: 'MCP servers',
    moreSkills: (count: number) => `${String(count)} more`,
    noneActive: 'None connected.',
    none: (title: string) => `${title} does not say which MCP servers it has.`,
    plugins: 'Plugins',
    pluginSkills: (count: number) => (count === 1 ? '1 skill' : `${String(count)} skills`),
    pluginServers: (on: number, all: number) =>
      `${String(on)} of ${String(all)} ${all === 1 ? 'MCP server' : 'MCP servers'}`,
    showInactive: (count: number) =>
      count === 1
        ? 'Show 1 down (needs login, failed or off)'
        : `Show ${String(count)} down (need login, failed or off)`,
    skills: 'Skills',
    skillsEmpty: 'No skills.',
    skillsFolder: (folder: string) => `Read in ${folder}: the project's are here.`,
    skillsReading: 'Reading its skills…',
    origins: {
      account: 'From the account',
      local: 'This folder only',
      managed: 'From the organization',
      plugin: 'From plugins',
      project: 'From the project',
      user: 'Yours'
    } as Record<string, string>,
    outsideProjects: "Read outside any project: a folder's .mcp.json is not here.",
    reading: 'Reading its MCP servers…',
    refresh: 'Read again',
    statuses: {
      connected: 'connected',
      disabled: 'off',
      failed: 'failed',
      'needs-auth': 'needs login',
      pending: 'starting'
    } as Record<string, string>,
    tools: (count: number) => (count === 1 ? '1 tool' : `${String(count)} tools`),
    usage: 'Plan usage',
    usageExtra:
      'Extra usage is on: past these limits it goes on and bills, so it does not stop for the next account or route.',
    usageNone: 'This login has no plan limits: an API key or a cloud provider pays.',
    usageReading: 'Reading the plan usage…',
    used: (percent: number) => `${String(percent)}% used`,
    resets: (when: string) => `Resets in ${when}`,
    windows: {
      days: (count: number) => `${String(count)}-day limit`,
      session: 'Current session',
      week: 'Current week (all models)',
      weekOf: (model: string) => `Current week (${model})`
    }
  },
  status: {
    failed: 'stopped with an error',
    unread: 'done, not seen yet',
    waiting: 'waiting for you',
    working: 'working'
  },
  layout: {
    hidePanel: 'Hide the panel',
    hideSidebar: (keys: string) => `Hide the sidebar (${keys})`,
    resizePanel: "The panel's width: drag, or double-click to reset",
    resizeSidebar: "The sidebar's width: drag, or double-click to reset",
    showPanel: 'Show the panel',
    showSidebar: (keys: string) => `Show the sidebar (${keys})`
  },
  updates: {
    apply: 'Update and reopen',
    applyServer: 'Update and reconnect',
    brings: (count: number) =>
      count === 1
        ? 'One change on main that this Atlas lacks:'
        : `${String(count)} changes on main that this Atlas lacks:`,
    button: 'Update Atlas',
    closing: 'Closing to update…',
    confirmUnverified: "Update anyway, without CI's verification",
    dirty:
      'This installation has changes of its own, which the update refuses: commit or discard them in its folder first.',
    dismiss: 'Dismiss',
    done: (count: number) =>
      count === 1 ? 'Atlas was updated: one change.' : `Atlas was updated: ${String(count)} changes.`,
    executors: (count: number) =>
      count === 1 ? 'an executor session at work' : `${String(count)} executor sessions at work`,
    failed: (error: string) => `The update failed, and Atlas stayed as it was: ${error}`,
    how: 'Atlas closes, updates and opens again by itself, in about a minute.',
    howServer:
      'The server builds the new version while it keeps running, then restarts for a moment: the app reconnects by itself and says how it went. If anything fails, the server goes back to the version it runs now, with its data as it was.',
    later: 'Not now',
    log: 'See what it did',
    more: (count: number) => `and ${String(count)} more`,
    ok: 'OK',
    runningNow: 'Atlas is updating, and opens by itself when it is done.',
    stops: (what: string) => `Closing stops what runs now: ${what}.`,
    stopsServer: (what: string) => `The restart stops what runs on the server now: ${what}.`,
    subagents: (count: number) => (count === 1 ? 'a subagent at work' : `${String(count)} subagents at work`),
    title: 'An update for Atlas',
    turns: (count: number) => (count === 1 ? 'a conversation answering' : `${String(count)} conversations answering`),
    unverified: (reason: string) => `CI did not verify this version: ${reason}.`,
    updatingServer: 'The server is updating…'
  },
  preview: {
    close: 'Close this preview',
    dismiss: 'Dismiss',
    notWeb: 'Only a web address opens here.',
    openBeside: 'Open beside',
    reading: 'Reading the file…'
  },
  terminal: {
    add: 'New terminal',
    cancel: 'Cancel',
    close: 'Close this terminal',
    dangerous: 'The trust gate finds this command dangerous. Run it anyway?',
    empty: 'No terminal open.',
    exit: (code: number) => `exit ${String(code)}`,
    name: 'Terminal',
    panel: 'Side panel',
    run: 'Run in the terminal',
    runAnyway: 'Run it',
    toggle: 'Show or hide the terminal (Ctrl+`)',
    unavailable: "Atlas's machine cannot open a terminal: it lacks the terminal's package (uv sync --all-extras there)."
  },
  thread: {
    compacted: 'The conversation was compacted.',

    empty: 'Ask Atlas anything.',
    reading: 'Reading the conversation…',
    /** Under a message on its way to the server, until the server has it. */
    sending: 'Sending…',
    /** Under a message the server has, waiting to enter the turn that runs. */
    queued: 'Enters once the current step ends',
    // A failed turn's ways out, those that exist: its message back in the box to send again, the
    // model to change, a key to connect.
    changeModel: 'Change the model',
    connectKey: 'Connect a key',
    putBack: 'Put it back in the box',
    failed: (error: string) => `The turn failed: ${error}`,
    halted: 'The turn stopped: the agent repeated the same call.',
    interrupted: 'Stopped.',
    limited: 'The turn stopped at a limit.',
    fileSent: 'Atlas sent a file',
    notice: 'Atlas was notified',
    /** A part for an event this app does not know: a newer server's. */
    other: 'not known to this version of the app',
    /** Under a `/name` that loaded a skill. */
    skill: 'skill loaded',
    steered: 'sent during the turn',
    summary: 'Summary of earlier messages',
    // The reasoning's closed line.
    thinking: 'Thinking…',
    thought: 'Thought',
    thoughtBriefly: 'Thought briefly',
    thoughtFor: (duration: string) => `Thought for ${duration}`,
    /** The button that scrolls a thread back down to its end. */
    toLatest: 'Scroll to the latest'
  },
  tool: {
    cancelled: 'cancelled',
    /** An output cut for display. */
    clamped: (omitted: string) => `… ${omitted} more characters truncated — use Copy all for the full output.`,
    copied: 'Copied',
    copyAll: 'Copy all',
    denied: 'denied',
    failed: 'failed',
    input: 'IN',
    output: 'OUT',
    running: 'running',
    spilled: (path: string) => `The whole result is in ${path}`,
    /** A call's title, what it is about and how many more. */
    andMore: (target: string, more: number) => `${target} and ${String(more)} more`,
    titles: TOOL_TITLES,
    /** A call that never ran: nobody answered its approval in time. */
    unanswered: 'not answered in time',
    waiting: 'waiting for its output…',
    /** A call a card holds: what it waits for. */
    waitingFor: {
      answer: 'waiting for your answer',
      approval: 'waiting for your approval',
      key: 'waiting for the key'
    }
  },
  /** Settings, and their one section so far: the machines (step 15). */
  machines: {
    /** A notification's conversation on another machine than the one on screen. */
    onMachine: (conversation: string, machine: string) => `${conversation} (${machine})`,
    add: 'Add a machine',
    adding: 'Adding…',
    addTitle: 'Add a machine',
    alwaysOn: 'Always on',
    alwaysOnHint: 'Runs with the app closed and starts with the computer.',
    busy: 'A turn is running in this home; wait for it to end.',
    code: 'Connection code',
    codeCopied: 'Copied. It holds this home’s password: paste it only into your own app.',
    codeHint: 'Shown by “Open to my machines” on the other machine, or by `atlas server code` in its terminal.',
    copyCode: 'Copy the connection code',
    cancel: 'Cancel',
    close: 'Close',
    name: 'Name',
    namePlaceholder: 'Shop',
    newToken: 'Change the password',
    newTokenConfirm: 'Every code given before stops working. Continue?',
    newTokenDone: 'Password changed: give the new code to your machines.',
    noTailnet: 'Waiting for Tailscale:',
    open: 'Open to my machines',
    openHint: 'Also reachable from your other machines over Tailscale.',
    others: 'Other machines',
    othersEmpty: 'No other machine yet.',
    remove: 'Remove',
    removeConfirm: (name: string) => `Remove ${name}? Its Atlas is not touched: add it again any time.`,
    rename: 'Rename',
    reachable: 'Reachable',
    save: 'Save',
    selector: (machine: string) => `${machine}: switch machine`,
    settings: 'Settings',
    sshHost: 'SSH host',
    sshHostPlaceholder: 'user@host or a name from ~/.ssh/config',
    test: 'Test',
    testing: 'Testing…',
    thisComputer: 'This computer',
    thisMachine: (home: string) => `This machine · home ${home}`,
    title: 'Machines',
    transport: 'Reached by',
    viaRelay: 'Relay, from anywhere',
    viaSsh: 'SSH tunnel',
    viaTailscale: 'Tailscale',
    working: 'Working…'
  },
  /** A tool's time: 12ms, 4.2s, 25s, 1m 5s. */
  duration: (seconds: number) =>
    // Each step is chosen by the value as it is shown once rounded, so 0.9996 is never `1000ms`,
    // 9.97 never `10.0s` and 59.6 never `60s`.
    Math.round(seconds * 1000) < 1000
      ? `${String(Math.max(1, Math.round(seconds * 1000)))}ms`
      : Math.round(seconds * 10) < 100
        ? `${seconds.toFixed(1)}s`
        : Math.round(seconds) < 60
          ? `${seconds.toFixed(0)}s`
          : `${String(Math.floor(Math.round(seconds) / 60))}m ${String(Math.round(seconds) % 60)}s`,
  /** A running timer, in whole seconds: 4s, 1:05. */
  elapsed: (seconds: number) =>
    seconds < 60
      ? `${String(seconds)}s`
      : `${String(Math.floor(seconds / 60))}:${String(seconds % 60).padStart(2, '0')}`
}

export type Messages = typeof en
