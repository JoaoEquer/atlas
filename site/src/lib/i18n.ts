// The site's languages: English first, as the rest of Atlas, and Portuguese for the home and the
// documentation's start (ROADMAP, step 19). A page in Portuguese lives under /pt-br/; the rest of
// the documentation falls back to English there, saying so.

export const LANGS = ['en', 'pt-br'] as const
export type Lang = (typeof LANGS)[number]

/** The `lang` of a page's `<html>`, and the locale its share card names. */
export const HTML_LANG: Record<Lang, string> = { en: 'en', 'pt-br': 'pt-BR' }
export const OG_LOCALE: Record<Lang, string> = { en: 'en_US', 'pt-br': 'pt_BR' }

/** A path of the site in a language: `/docs/` is `/pt-br/docs/` in Portuguese. */
export function localized(path: string, lang: Lang): string {
  return lang === 'en' || !path.startsWith('/') ? path : `/pt-br${path}`
}

/** The words of the site's frame and of the home's markup, beside the home's own text (home.ts). */
export interface Words {
  /** The other language, as the switch names it in its own words. */
  switchLabel: string
  switchTitle: string
  skip: string
  /** What the share image shows, for whoever cannot see it. */
  imageAlt: string
  home: string
  menu: string
  mainNav: string
  theme: string
  nav: { docs: string; news: string; changelog: string }
  footer: {
    line: string
    blurb: string
    installAtlas: string
    label: string
    atlas: string
    install: string
    documentation: string
    guides: string
    codingAgents: string
    machines: string
    models: string
    troubleshooting: string
    openSource: string
    issues: string
    writePlugin: string
    license: string
    forAgents: string
    markdown: string
    changelogFeed: string
    affiliation: string
    fonts: string
  }
  install: {
    system: string
    copy: string
    copied: string
    copiedStatus: string
    noTerminal: string
  }
  homePage: {
    readDocs: string
    inShort: string
    running: string
    limit: string
    installAtlas: string
    promise: string
    when: string
    readMore: string
    installGuide: string
    installGuideRest: string
    notAnswered: string
    documentation: string
    coversAll: string
    issues: string
    issuesRest: string
    readMoreDocs: string
    keepReading: string
    everyCommand: string
    newsEmpty: string
    newsFeed: string
  }
}

export const WORDS: Record<Lang, Words> = {
  en: {
    switchLabel: 'Português',
    switchTitle: 'Ler em português',
    skip: 'Skip to content',
    imageAlt: 'Atlas: every coding agent, every account, one window.',
    home: 'Atlas, home',
    menu: 'Menu',
    mainNav: 'Main',
    theme: 'Switch between the light and the dark page',
    nav: { docs: 'Docs', news: 'News', changelog: 'Changelog' },
    footer: {
      line: 'Every coding agent. Every account. One window.',
      blurb:
        'Free and open source under the MIT license. No account, no telemetry: what Atlas learns stays on your machine.',
      installAtlas: 'Install Atlas',
      label: 'Footer',
      atlas: 'Atlas',
      install: 'Install',
      documentation: 'Documentation',
      guides: 'Guides',
      codingAgents: 'Coding agents and accounts',
      machines: 'Several machines',
      models: 'Models and keys',
      troubleshooting: 'Troubleshooting',
      openSource: 'Open source',
      issues: 'Issues',
      writePlugin: 'Write a plugin',
      license: 'MIT license',
      forAgents: 'For agents',
      markdown: 'This site in Markdown',
      changelogFeed: 'Changelog feed',
      affiliation:
        'Atlas is not affiliated with Anthropic, OpenAI, Google or DeepSeek. Their products’ names belong to them.',
      fonts: 'Set in Mona Sans and Martian Mono, under the SIL Open Font License.'
    },
    install: {
      system: 'Your system',
      copy: 'Copy',
      copied: 'Copied',
      copiedStatus: 'Copied to the clipboard.',
      noTerminal: 'No terminal? Download the Setup:'
    },
    homePage: {
      readDocs: 'Read the docs',
      inShort: 'In short',
      running: 'running',
      limit: 'limit reached',
      installAtlas: 'Install Atlas',
      promise: 'What Atlas promises',
      when: 'When',
      readMore: 'Read more',
      installGuide: 'The install guide',
      installGuideRest: ', with the container and what the first start asks.',
      notAnswered: 'Not answered here? The',
      documentation: 'documentation',
      coversAll: 'covers every part of Atlas, and the',
      issues: 'issues on GitHub',
      issuesRest: 'are open.',
      readMoreDocs: 'Read more in the docs',
      keepReading: 'Keep reading',
      everyCommand: 'Every command',
      newsEmpty:
        'Short summaries of what changes in the models and tools Atlas works with, each linking to the full story. The first ones are on the way:',
      newsFeed: 'follow the feed'
    }
  },
  'pt-br': {
    switchLabel: 'English',
    switchTitle: 'Read in English',
    skip: 'Pular para o conteúdo',
    imageAlt: 'Atlas: todo agente de código, toda conta, uma janela.',
    home: 'Atlas, início',
    menu: 'Menu',
    mainNav: 'Principal',
    theme: 'Trocar entre a página clara e a escura',
    nav: { docs: 'Documentação', news: 'Notícias', changelog: 'Mudanças' },
    footer: {
      line: 'Todo agente de código. Toda conta. Uma janela.',
      blurb:
        'Grátis e de código aberto, sob a licença MIT. Sem conta, sem telemetria: o que o Atlas aprende fica na sua máquina.',
      installAtlas: 'Instalar o Atlas',
      label: 'Rodapé',
      atlas: 'Atlas',
      install: 'Instalar',
      documentation: 'Documentação',
      guides: 'Guias',
      codingAgents: 'Agentes de código e contas',
      machines: 'Várias máquinas',
      models: 'Modelos e chaves',
      troubleshooting: 'Solução de problemas',
      openSource: 'Código aberto',
      issues: 'Issues',
      writePlugin: 'Escrever um plugin',
      license: 'Licença MIT',
      forAgents: 'Para agentes',
      markdown: 'Este site em Markdown',
      changelogFeed: 'Feed das mudanças',
      affiliation:
        'O Atlas não tem ligação com a Anthropic, a OpenAI, o Google ou a DeepSeek. Os nomes dos produtos deles pertencem a eles.',
      fonts: 'Composto em Mona Sans e Martian Mono, sob a SIL Open Font License.'
    },
    install: {
      system: 'Seu sistema',
      copy: 'Copiar',
      copied: 'Copiado',
      copiedStatus: 'Copiado para a área de transferência.',
      noTerminal: 'Sem terminal? Baixe o Setup:'
    },
    homePage: {
      readDocs: 'Ler a documentação',
      inShort: 'Em resumo',
      running: 'rodando',
      limit: 'limite atingido',
      installAtlas: 'Instalar o Atlas',
      promise: 'O que o Atlas promete',
      when: 'Quando',
      readMore: 'Saiba mais',
      installGuide: 'O guia de instalação',
      installGuideRest: ', com o container e o que a primeira abertura pede.',
      notAnswered: 'Não achou aqui? A',
      documentation: 'documentação',
      coversAll: 'cobre cada parte do Atlas, e as',
      issues: 'issues no GitHub',
      issuesRest: 'estão abertas.',
      readMoreDocs: 'Mais na documentação',
      keepReading: 'Para continuar lendo',
      everyCommand: 'Todos os comandos',
      newsEmpty:
        'Resumos curtos do que muda nos modelos e nas ferramentas com que o Atlas trabalha, cada um com o link para a notícia inteira. Os primeiros estão a caminho:',
      newsFeed: 'siga o feed'
    }
  }
}
