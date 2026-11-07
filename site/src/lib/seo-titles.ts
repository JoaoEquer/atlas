// What a documentation page is called in a search result. The page's own title is short, for the
// sidebar and for `atlas docs`; a search result has room for what the page answers, in the words
// people search with. Each stays under about 65 characters, where results cut a title.

const TITLES: Record<string, string> = {
  docs: 'Atlas docs: one app for Claude Code, Codex and the Gemini CLI',
  'docs/install': 'Install Atlas on Windows, macOS, Linux or Docker',
  'docs/troubleshooting': 'Troubleshooting Atlas: install, keys, coding agents, updates',
  'docs/faq': 'Atlas FAQ: accounts, usage limits, privacy and models',
  'docs/guides/coding-agents': 'Claude Code and Codex on several accounts, with routes',
  'docs/guides/machines': 'Run coding agents on a home lab or VPS from your laptop',
  'docs/guides/models': 'Models and API keys: OpenAI, Anthropic, Gemini, Ollama, vLLM',
  'docs/guides/memory': 'Memory: what Atlas remembers about you, in files you can edit',
  'docs/guides/background-review': 'The background review: how Atlas learns from a conversation',
  'docs/guides/bots': 'Bots: AI agents with their own mission, memory and routines',
  'docs/guides/routines': 'Routines: schedule prompts and commands in plain words or cron',
  'docs/guides/telegram': 'Telegram: answer Claude Code and Atlas from your phone',
  'docs/guides/voice': 'Voice dictation anywhere on your computer, and live talk',
  'docs/guides/browser': 'Browser automation with Chrome, Edge or Chromium',
  'docs/guides/marketplace': 'Skills, MCP servers and plugins: the Atlas marketplace',
  'docs/guides/approval-gate': 'The approval gate: which agent commands run and which ask you',
  'docs/guides/privacy': 'Privacy: what Atlas keeps on your machine and what leaves it',
  'docs/guides/backups': 'Backups, updates and separate homes for work and personal',
  'docs/reference/commands': 'Every atlas command, with its arguments',
  'docs/reference/settings': 'Every Atlas setting: config.toml and its ATLAS_ variables',
  'docs/reference/tools': 'Every tool the Atlas agent has, and when it has it',
  'docs/extending/plugins': 'Write a plugin: skills, hooks, tools, channels and executors',
  'pt-br/docs': 'Documentação do Atlas: um app para Claude Code, Codex e Gemini CLI',
  'pt-br/docs/install': 'Instalar o Atlas no Windows, macOS, Linux ou Docker'
}

/** The title tag of the page `id`, whose own title is `title`. */
export function searchTitle(id: string, title: string): string {
  const special = TITLES[id]
  if (special === undefined) {
    return id.startsWith('pt-br/') ? `${title} | Documentação do Atlas` : `${title} | Atlas docs`
  }
  // A title that already names Atlas does not name it twice.
  return /\bAtlas\b/.test(special) ? special : `${special} | Atlas`
}
