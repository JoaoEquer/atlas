// The home's words, read by the page, its Markdown twin and its structured data, so the three never
// drift apart. Every claim here is one the documentation makes too: the home sells only what the
// app does.

/** What a search result shows: the title tag and the description. */
export const seo = {
  title: 'Atlas: the open-source app for Claude Code, Codex and the Gemini CLI',
  description:
    'Run Claude Code, Codex, the Gemini CLI and DeepSeek from one desktop app, on every account and machine. When a plan hits its limit, the next one takes over. Free and MIT.'
}

export const hero = {
  eyebrow: 'Open source · Windows, macOS and Linux',
  title: 'Every coding agent. Every account. One window.',
  lede: 'Atlas is the open-source desktop app that runs Claude Code, Codex, the Gemini CLI and DeepSeek on all your accounts and machines. When one plan hits its limit, the task moves on to the next, and Atlas learns how you work as it goes.',
  note: 'Free under the MIT license. One line installs it.'
}

/** The facts beside the install line: what a careful reader checks before running it. */
export const proof = [
  { label: 'Free and MIT', detail: 'Open source, every line on GitHub' },
  { label: 'No account, no telemetry', detail: 'Nothing of yours reaches a server of ours' },
  { label: 'Your own logins', detail: 'Atlas never sees a credential' },
  { label: 'Local or remote', detail: 'Your laptop, a home lab, a VPS' }
]

/** The statement set large between the story and the details; its words light up as it is read. */
export const tagline: string =
  'When one plan hits its limit, the task moves on to the next. What Atlas learns stays on your machine, and nothing of yours reaches a server of ours.'

export interface Chapter {
  id: 'accounts' | 'route' | 'machines' | 'memory' | 'close'
  title: string
  body: string[]
}

/** The chapters the orrery tells, in the order it tells them. */
export const chapters: Chapter[] = [
  {
    id: 'accounts',
    title: 'Personal, work, a client: all signed in at once',
    body: [
      'Claude Code, Codex, the Gemini CLI and DeepSeek run as you installed them, with your settings, your project’s instructions and your own login. Atlas never sees a credential.',
      'Each account keeps its own folder, history, MCP servers and plugins. They run side by side, each plan’s five-hour window and week in view, read without spending a token.'
    ]
  },
  {
    id: 'route',
    title: 'A usage limit no longer ends the task',
    body: [
      'Give a session a route: the accounts and coding agents it may use, in order. When one plan runs out, the session goes on in the next, without you logging out, logging in or explaining it all again.'
    ]
  },
  {
    id: 'machines',
    title: 'Your home lab, your VPS, the office PC. One window.',
    body: [
      'Install Atlas where the work is and open it from the app on your laptop, over Tailscale or an SSH tunnel. Tailscale needs no open port and no certificate.',
      'The Atlas over there does the work, with that machine’s files, keys and approvals. Turn on Always on and it keeps running with the app closed.'
    ]
  },
  {
    id: 'memory',
    title: 'It learns how you work, in files you can read',
    body: [
      'Every few messages, a copy of the agent rereads the conversation and keeps what it learned: who you are goes to your profile, how to do a task becomes a skill.',
      'It is all plain Markdown you can open and edit.'
    ]
  },
  {
    id: 'close',
    title: 'What it knows stays on your machine',
    body: [
      'No account, no telemetry and no cloud of ours. Your profile, memory, conversations and encrypted keys live in one folder on your computer, and your coding agents keep their own logins.'
    ]
  }
]

/** The meters of the route chapter: the five-hour window of each account on the route. */
export const route = {
  caption: 'Five-hour window',
  stops: [
    { name: 'Claude Code', account: 'personal' },
    { name: 'Claude Code', account: 'company' },
    { name: 'Codex', account: 'main' }
  ],
  handoff:
    'Across coding agents, the next one starts with a handoff: the task, your latest instruction, the steps not done, the files changed and what happened so far. Built from the session itself, with no model call.'
}

export type MemoryKind = 'profile' | 'notes' | 'skill' | 'proposal'

/** What the memory chapter shows the review keeping. */
export const memory: {
  entries: { kind: MemoryKind; mark: string; text: string }[]
  caption: string
  kinds: Record<MemoryKind, string>
} = {
  entries: [
    { kind: 'profile', mark: '+', text: 'Prefers short answers, in Portuguese' },
    { kind: 'notes', mark: '+', text: 'The tests run with uv run pytest' },
    { kind: 'skill', mark: '~', text: 'release-notes: list breaking changes first' },
    { kind: 'proposal', mark: '?', text: 'Replace “deploys on Fridays” with “deploys on Thursdays”' }
  ],
  caption: 'Anything that would replace or remove a memory waits for your approval.',
  /** What each kind is called on screen; the kind itself is the page's key. */
  kinds: { profile: 'profile', notes: 'notes', skill: 'skill', proposal: 'proposal' }
}

/** The comparison: the same day of work, with the coding agents on their own and inside Atlas. */
export const compare = {
  title: 'The same agents, without the friction',
  body: 'Atlas does not replace Claude Code, Codex or the Gemini CLI. It runs the copy you installed, through the protocol its makers use for their own apps, and takes away what stops you between tasks.',
  alone: 'On their own',
  atlas: 'In Atlas',
  rows: [
    {
      moment: 'A plan hits its limit mid-task',
      alone: 'The task stops. You switch accounts by hand and explain it all again.',
      atlas: 'The session goes on in the next account or coding agent on its route.'
    },
    {
      moment: 'You have several accounts',
      alone: 'One login at a time, or config folders you juggle in the terminal.',
      atlas: 'Every account signed in at once, each in its own folder, side by side.'
    },
    {
      moment: 'The work lives on another machine',
      alone: 'An SSH session and a terminal multiplexer per machine.',
      atlas: 'Every machine in the same window, over Tailscale or an SSH tunnel.'
    },
    {
      moment: 'An agent asks for permission',
      alone: 'It waits in a terminal you may not be looking at.',
      atlas: 'It reaches the app, or your phone on Telegram, and waits with no deadline.'
    },
    {
      moment: 'You want to know what is left',
      alone: '/usage in each terminal, one account at a time.',
      atlas: 'Each plan’s five-hour window and week, for every account, in one tab.'
    }
  ]
}

/** How the gate treats a command: the page colors each verdict by it. */
export type Tone = 'runs' | 'reviewed' | 'asks-you' | 'never-runs'

export const gate: {
  title: string
  body: string
  link: { href: string; label: string }
  commands: { command: string; tone: Tone; verdict: string; detail: string }[]
} = {
  title: 'Every command passes the approval gate',
  body: 'Atlas runs commands on your machine, so each one is checked before it runs. Some never run, whatever the mode, and anything that acts outside your machine on your behalf asks you first.',
  link: { href: '/docs/guides/approval-gate/', label: 'How the approval gate decides' },
  commands: [
    { command: 'npm test', tone: 'runs', verdict: 'Runs', detail: 'An ordinary command goes straight through.' },
    {
      command: 'git push --force',
      tone: 'reviewed',
      verdict: 'Reviewed',
      detail:
        'A dangerous pattern. Unless auto mode is on, a reviewer model reads it first, and what it is unsure of comes to you.'
    },
    {
      command: 'gh pr merge 42',
      tone: 'asks-you',
      verdict: 'Asks you',
      detail: 'It acts outside your machine, so it asks every time, even in auto mode.'
    },
    {
      command: 'rm -rf ~',
      tone: 'never-runs',
      verdict: 'Never runs',
      detail: 'Blocked in every mode, whatever was approved before.'
    }
  ]
}

export interface CatalogueItem {
  name: string
  text: string
  /** The documentation page that covers it. */
  href: string
}

export const catalogue: { title: string; body: string; items: CatalogueItem[] } = {
  title: 'And a personal agent of its own',
  body: 'Beyond your coding agents, Atlas is an assistant on the model you choose, with the tools to do the rest of the work.',
  items: [
    {
      name: 'Routines',
      text: 'Scheduled prompts and commands, written in plain words (“every weekday at 9am”) or cron.',
      href: '/docs/guides/routines/'
    },
    {
      name: 'Bots',
      text: 'Named agents with their own mission, memory, skills and routines. They write to each other.',
      href: '/docs/guides/bots/'
    },
    {
      name: 'Telegram',
      text: 'Talk to Atlas from your phone, and answer your coding agents’ questions from it.',
      href: '/docs/guides/telegram/'
    },
    {
      name: 'Voice',
      text: 'Dictate into any program on your computer, send audio, or talk live, on your OpenAI key.',
      href: '/docs/guides/voice/'
    },
    {
      name: 'A browser of its own',
      text: 'Atlas drives its own Chrome profile, and stops at logins and payments for you to do them.',
      href: '/docs/guides/browser/'
    },
    {
      name: 'Your choice of model',
      text: 'OpenAI, Anthropic, Google Gemini or OpenRouter with your key, or local models through Ollama and vLLM.',
      href: '/docs/guides/models/'
    },
    {
      name: 'Marketplace',
      text: 'Skills, MCP servers, plugins and executors, each reviewed and installed exactly as reviewed.',
      href: '/docs/guides/marketplace/'
    },
    {
      name: 'Plugins',
      text: 'Add skills, commands, hooks, MCP servers, tools, channels and new coding agents.',
      href: '/docs/extending/plugins/'
    },
    {
      name: 'Backups and homes',
      text: 'One zip of your data, never the keys, and separate homes for work and personal life.',
      href: '/docs/guides/backups/'
    }
  ]
}

export const install = {
  title: 'Install Atlas',
  steps: [
    'Run the line. The installer brings its own Python, Node and tools, checked against pinned hashes, then builds the app and opens it.',
    'Paste an API key from OpenAI, Anthropic, Google Gemini or OpenRouter when the app asks for one, and start talking.',
    'Turn on Claude Code, Codex, the Gemini CLI or DeepSeek in Settings. Each one you have installed shows up in the Executors tab.'
  ],
  update: 'Run the same line again to update, or type atlas update. If a step fails, it goes back to where it was.',
  needs:
    'Nothing else to install first: Git, curl and tar on macOS and Linux, which most already have. Atlas keeps its tools off your PATH, so it changes none of your own, and your data lives apart, in ~/.atlas.'
}

export interface Question {
  question: string
  answer: string
  /** Where the documentation says more. */
  href?: string
}

/** The questions people ask before they install, answered as the documentation answers them. */
export const faq: { title: string; items: Question[] } = {
  title: 'Questions before you install',
  items: [
    {
      question: 'Is Atlas free?',
      answer:
        'Yes. Atlas is open source under the MIT license, with no paid tier and no account. Atlas’s own agent runs on an API key you bring, and you pay that provider for what you use; your coding agents run on the plans you already have.',
      href: '/docs/'
    },
    {
      question: 'Can I use several Claude Code accounts at the same time?',
      answer:
        'Yes. Each account is its own executor, such as Claude Code · personal and Claude Code · company, with its own folder, login, settings, MCP servers, plugins and history, and they run side by side. You sign in through Claude Code’s own login in your browser; Atlas never sees, keeps or passes a credential.',
      href: '/docs/guides/coding-agents/'
    },
    {
      question: 'What happens when Claude Code hits its usage limit?',
      answer:
        'With a route, the session goes on by itself. Within one coding agent it continues in a copy on the next account; across coding agents, a new session starts with a handoff: the task, your latest instruction, the steps not done and the files changed. You can also switch a session’s account by hand.',
      href: '/docs/guides/coding-agents/'
    },
    {
      question: 'Does Atlas replace Claude Code, Codex or the Gemini CLI?',
      answer:
        'No. Atlas runs the copy you installed and speaks the protocol its makers use for their own apps: Claude Code’s control protocol, codex app-server and the Agent Client Protocol for the Gemini CLI and DeepSeek. Your settings, permission rules and project instructions stay as they are.',
      href: '/docs/guides/coding-agents/'
    },
    {
      question: 'Does my code or data go to Atlas’s servers?',
      answer:
        'There are none. Atlas has no account, no telemetry and no cloud of its own. Your memory, conversations and encrypted keys stay in a folder on your machine, and what leaves it is what you ask for, such as the calls to your model provider.',
      href: '/docs/guides/privacy/'
    },
    {
      question: 'Which models can Atlas’s own agent use?',
      answer:
        'OpenAI, Anthropic, Google Gemini or OpenRouter with your own key, or a local model through Ollama or vLLM. Each conversation can switch models and effort.',
      href: '/docs/guides/models/'
    },
    {
      question: 'How does Atlas decide which commands run?',
      answer:
        'Every command passes the approval gate. A few never run in any mode, dangerous patterns go to a reviewer model or to you, and anything that acts outside your machine, such as merging a pull request or sending an email, asks you every time.',
      href: '/docs/guides/approval-gate/'
    },
    {
      question: 'Can I use it from my phone?',
      answer:
        'Yes, through a Telegram bot of your own. You talk to Atlas there, get its replies, and answer the questions and permission requests of your coding agents, the first answer, in the chat or in the app, taking it.',
      href: '/docs/guides/telegram/'
    }
  ]
}
