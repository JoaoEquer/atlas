// The home's words in Portuguese, the same claims as home.ts, said in Brazilian Portuguese; the
// shape is held to the English one by src/lib/homes.ts. The documentation's links lead to its
// Portuguese pages, which fall back to English where there is no translation yet.
import type { CatalogueItem, Chapter, MemoryKind, Question, Tone } from './home'

export const seo = {
  title: 'Atlas: o app de código aberto para Claude Code, Codex e Gemini CLI',
  description:
    'Rode Claude Code, Codex, Gemini CLI e DeepSeek num só app de desktop, em todas as contas e máquinas. Quando um plano bate no limite, o próximo assume. Grátis e MIT.'
}

export const hero = {
  eyebrow: 'Código aberto · Windows, macOS e Linux',
  title: 'Todo agente de código. Toda conta. Uma janela.',
  lede: 'O Atlas é o app de desktop de código aberto que roda Claude Code, Codex, Gemini CLI e DeepSeek em todas as suas contas e máquinas. Quando um plano bate no limite, a tarefa segue no próximo, e o Atlas aprende como você trabalha no caminho.',
  note: 'Grátis, sob a licença MIT. Uma linha instala.'
}

export const proof = [
  { label: 'Grátis e MIT', detail: 'Código aberto, cada linha no GitHub' },
  { label: 'Sem conta, sem telemetria', detail: 'Nada seu chega a um servidor nosso' },
  { label: 'Seus próprios logins', detail: 'O Atlas nunca vê uma credencial' },
  { label: 'Local ou remoto', detail: 'Seu notebook, um homelab, uma VPS' }
]

export const tagline: string =
  'Quando um plano bate no limite, a tarefa segue no próximo. O que o Atlas aprende fica na sua máquina, e nada seu chega a um servidor nosso.'

export const chapters: Chapter[] = [
  {
    id: 'accounts',
    title: 'Pessoal, trabalho, um cliente: todas logadas ao mesmo tempo',
    body: [
      'Claude Code, Codex, Gemini CLI e DeepSeek rodam como você os instalou, com as suas configurações, as instruções do seu projeto e o seu próprio login. O Atlas nunca vê uma credencial.',
      'Cada conta tem a própria pasta, histórico, servidores MCP e plugins. Elas rodam lado a lado, com a janela de cinco horas e a semana de cada plano à vista, lidas sem gastar um token.'
    ]
  },
  {
    id: 'route',
    title: 'O limite de uso não encerra mais a tarefa',
    body: [
      'Dê uma rota a uma sessão: as contas e os agentes de código que ela pode usar, em ordem. Quando um plano acaba, a sessão segue no próximo, sem você sair, entrar de novo ou explicar tudo outra vez.'
    ]
  },
  {
    id: 'machines',
    title: 'Seu homelab, sua VPS, o PC do escritório. Uma janela.',
    body: [
      'Instale o Atlas onde o trabalho está e abra pelo app no seu notebook, pelo Tailscale ou por um túnel SSH. O Tailscale não precisa de porta aberta nem de certificado.',
      'O Atlas de lá faz o trabalho, com os arquivos, as chaves e as aprovações daquela máquina. Ligue o Sempre ligado e ele continua rodando com o app fechado.'
    ]
  },
  {
    id: 'memory',
    title: 'Ele aprende como você trabalha, em arquivos que você pode ler',
    body: [
      'A cada poucas mensagens, uma cópia do agente relê a conversa e guarda o que aprendeu: quem você é vai para o seu perfil, e o jeito de fazer uma tarefa vira uma skill.',
      'É tudo Markdown simples, que você abre e edita.'
    ]
  },
  {
    id: 'close',
    title: 'O que ele sabe fica na sua máquina',
    body: [
      'Sem conta, sem telemetria e sem nuvem nossa. Seu perfil, a memória, as conversas e as chaves cifradas ficam numa pasta no seu computador, e os seus agentes de código mantêm os próprios logins.'
    ]
  }
]

export const route = {
  caption: 'Janela de cinco horas',
  stops: [
    { name: 'Claude Code', account: 'pessoal' },
    { name: 'Claude Code', account: 'empresa' },
    { name: 'Codex', account: 'principal' }
  ],
  handoff:
    'Entre agentes de código diferentes, o próximo começa com uma passagem de bastão: a tarefa, a sua última instrução, os passos que faltam, os arquivos alterados e o que aconteceu até ali. Montada a partir da própria sessão, sem chamar modelo.'
}

export const memory: {
  entries: { kind: MemoryKind; mark: string; text: string }[]
  caption: string
  kinds: Record<MemoryKind, string>
} = {
  entries: [
    { kind: 'profile', mark: '+', text: 'Prefere respostas curtas, em português' },
    { kind: 'notes', mark: '+', text: 'Os testes rodam com uv run pytest' },
    { kind: 'skill', mark: '~', text: 'release-notes: listar primeiro o que quebra' },
    { kind: 'proposal', mark: '?', text: 'Trocar “deploy às sextas” por “deploy às quintas”' }
  ],
  caption: 'O que trocaria ou apagaria uma memória espera a sua aprovação.',
  kinds: { profile: 'perfil', notes: 'notas', skill: 'skill', proposal: 'proposta' }
}

export const compare = {
  title: 'Os mesmos agentes, sem o atrito',
  body: 'O Atlas não substitui Claude Code, Codex nem Gemini CLI. Ele roda a cópia que você instalou, pelo protocolo que os próprios fabricantes usam nos apps deles, e tira o que te trava entre uma tarefa e outra.',
  alone: 'Sozinhos',
  atlas: 'No Atlas',
  rows: [
    {
      moment: 'Um plano bate no limite no meio da tarefa',
      alone: 'A tarefa para. Você troca de conta na mão e explica tudo de novo.',
      atlas: 'A sessão segue na próxima conta ou no próximo agente de código da rota.'
    },
    {
      moment: 'Você tem várias contas',
      alone: 'Um login por vez, ou pastas de configuração que você alterna no terminal.',
      atlas: 'Todas as contas logadas ao mesmo tempo, cada uma na sua pasta, lado a lado.'
    },
    {
      moment: 'O trabalho está em outra máquina',
      alone: 'Uma sessão SSH e um multiplexador de terminal por máquina.',
      atlas: 'Todas as máquinas na mesma janela, pelo Tailscale ou por um túnel SSH.'
    },
    {
      moment: 'Um agente pede permissão',
      alone: 'Ele espera num terminal que você talvez nem esteja olhando.',
      atlas: 'O pedido chega ao app, ou ao seu celular pelo Telegram, e espera sem prazo.'
    },
    {
      moment: 'Você quer saber quanto sobra',
      alone: '/usage em cada terminal, uma conta por vez.',
      atlas: 'A janela de cinco horas e a semana de cada plano, de todas as contas, numa aba.'
    }
  ]
}

export const gate: {
  title: string
  body: string
  link: { href: string; label: string }
  commands: { command: string; tone: Tone; verdict: string; detail: string }[]
} = {
  title: 'Todo comando passa pelo portão de aprovação',
  body: 'O Atlas roda comandos na sua máquina, então cada um é conferido antes de rodar. Alguns nunca rodam, em modo nenhum, e tudo o que age fora da sua máquina em seu nome pergunta antes.',
  link: { href: '/pt-br/docs/guides/approval-gate/', label: 'Como o portão de aprovação decide' },
  commands: [
    { command: 'npm test', tone: 'runs', verdict: 'Roda', detail: 'Um comando comum passa direto.' },
    {
      command: 'git push --force',
      tone: 'reviewed',
      verdict: 'Revisado',
      detail:
        'Um padrão perigoso. A não ser no modo automático, um modelo revisor lê antes, e o que o deixa em dúvida chega a você.'
    },
    {
      command: 'gh pr merge 42',
      tone: 'asks-you',
      verdict: 'Pergunta a você',
      detail: 'Ele age fora da sua máquina, então pergunta toda vez, até no modo automático.'
    },
    {
      command: 'rm -rf ~',
      tone: 'never-runs',
      verdict: 'Nunca roda',
      detail: 'Bloqueado em todos os modos, seja o que for que você aprovou antes.'
    }
  ]
}

export const catalogue: { title: string; body: string; items: CatalogueItem[] } = {
  title: 'E um agente pessoal só dele',
  body: 'Além dos seus agentes de código, o Atlas é um assistente no modelo que você escolher, com as ferramentas para fazer o resto do trabalho.',
  items: [
    {
      name: 'Rotinas',
      text: 'Prompts e comandos agendados, escritos em palavras comuns (“todo dia útil às 9h”) ou em cron.',
      href: '/pt-br/docs/guides/routines/'
    },
    {
      name: 'Bots',
      text: 'Agentes com nome, missão, memória, skills e rotinas próprias. Eles escrevem uns para os outros.',
      href: '/pt-br/docs/guides/bots/'
    },
    {
      name: 'Telegram',
      text: 'Fale com o Atlas pelo celular, e responda de lá as perguntas dos seus agentes de código.',
      href: '/pt-br/docs/guides/telegram/'
    },
    {
      name: 'Voz',
      text: 'Dite em qualquer programa do computador, mande áudio ou converse ao vivo, com a sua chave da OpenAI.',
      href: '/pt-br/docs/guides/voice/'
    },
    {
      name: 'Um navegador próprio',
      text: 'O Atlas usa um perfil do Chrome só dele, e para nos logins e pagamentos para você fazer.',
      href: '/pt-br/docs/guides/browser/'
    },
    {
      name: 'O modelo que você quiser',
      text: 'OpenAI, Anthropic, Google Gemini ou OpenRouter com a sua chave, ou modelos locais pelo Ollama e pelo vLLM.',
      href: '/pt-br/docs/guides/models/'
    },
    {
      name: 'Loja',
      text: 'Skills, servidores MCP, plugins e executores, cada um revisado e instalado exatamente como foi revisado.',
      href: '/pt-br/docs/guides/marketplace/'
    },
    {
      name: 'Plugins',
      text: 'Acrescente skills, comandos, ganchos, servidores MCP, ferramentas, canais e novos agentes de código.',
      href: '/pt-br/docs/extending/plugins/'
    },
    {
      name: 'Backups e casas',
      text: 'Um zip dos seus dados, nunca com as chaves, e casas separadas para o trabalho e a vida pessoal.',
      href: '/pt-br/docs/guides/backups/'
    }
  ]
}

export const install = {
  title: 'Instale o Atlas',
  steps: [
    'Rode a linha. O instalador traz o próprio Python, Node e ferramentas, conferidos contra hashes fixos, depois constrói o app e o abre.',
    'Cole uma chave de API da OpenAI, Anthropic, Google Gemini ou OpenRouter quando o app pedir, e comece a conversar.',
    'Ligue Claude Code, Codex, Gemini CLI ou DeepSeek nas Configurações. Cada um que você tem instalado aparece na aba Executores.'
  ],
  update:
    'Rode a mesma linha de novo para atualizar, ou digite atlas update. Se uma etapa falhar, ele volta para onde estava.',
  needs:
    'Nada mais para instalar antes: Git, curl e tar no macOS e no Linux, que quase todo mundo já tem. O Atlas deixa as ferramentas dele fora do seu PATH, então não muda nenhuma das suas, e os seus dados ficam à parte, em ~/.atlas.'
}

export const faq: { title: string; items: Question[] } = {
  title: 'Perguntas antes de instalar',
  items: [
    {
      question: 'O Atlas é grátis?',
      answer:
        'Sim. O Atlas é código aberto sob a licença MIT, sem plano pago e sem conta. O agente do próprio Atlas roda numa chave de API que você traz, e você paga a esse provedor pelo que usar; os seus agentes de código rodam nos planos que você já tem.',
      href: '/pt-br/docs/'
    },
    {
      question: 'Posso usar várias contas do Claude Code ao mesmo tempo?',
      answer:
        'Sim. Cada conta é um executor próprio, como Claude Code · pessoal e Claude Code · empresa, com a sua pasta, login, configurações, servidores MCP, plugins e histórico, e elas rodam lado a lado. Você entra pelo login do próprio Claude Code no navegador; o Atlas nunca vê, guarda ou repassa uma credencial.',
      href: '/pt-br/docs/guides/coding-agents/'
    },
    {
      question: 'O que acontece quando o Claude Code bate no limite de uso?',
      answer:
        'Com uma rota, a sessão segue sozinha. Dentro do mesmo agente de código, ela continua numa cópia na próxima conta; entre agentes diferentes, uma sessão nova começa com uma passagem de bastão: a tarefa, a sua última instrução, os passos que faltam e os arquivos alterados. Você também pode trocar a conta de uma sessão na mão.',
      href: '/pt-br/docs/guides/coding-agents/'
    },
    {
      question: 'O Atlas substitui o Claude Code, o Codex ou o Gemini CLI?',
      answer:
        'Não. O Atlas roda a cópia que você instalou e fala o protocolo que os fabricantes usam nos próprios apps: o protocolo de controle do Claude Code, o codex app-server e o Agent Client Protocol para o Gemini CLI e o DeepSeek. As suas configurações, regras de permissão e instruções de projeto ficam como estão.',
      href: '/pt-br/docs/guides/coding-agents/'
    },
    {
      question: 'Meu código ou meus dados vão para servidores do Atlas?',
      answer:
        'Não existem servidores. O Atlas não tem conta, nem telemetria, nem nuvem própria. A sua memória, as conversas e as chaves cifradas ficam numa pasta na sua máquina, e o que sai dela é o que você pede, como as chamadas ao seu provedor de modelo.',
      href: '/pt-br/docs/guides/privacy/'
    },
    {
      question: 'Quais modelos o agente do próprio Atlas pode usar?',
      answer:
        'OpenAI, Anthropic, Google Gemini ou OpenRouter com a sua chave, ou um modelo local pelo Ollama ou pelo vLLM. Cada conversa pode trocar de modelo e de esforço.',
      href: '/pt-br/docs/guides/models/'
    },
    {
      question: 'Como o Atlas decide quais comandos rodam?',
      answer:
        'Todo comando passa pelo portão de aprovação. Alguns nunca rodam em modo nenhum, padrões perigosos vão a um modelo revisor ou a você, e tudo o que age fora da sua máquina, como fazer merge de um pull request ou mandar um e-mail, pergunta toda vez.',
      href: '/pt-br/docs/guides/approval-gate/'
    },
    {
      question: 'Dá para usar pelo celular?',
      answer:
        'Sim, por um bot do Telegram só seu. Você fala com o Atlas por lá, recebe as respostas e responde às perguntas e aos pedidos de permissão dos seus agentes de código; vale a primeira resposta, no chat ou no app.',
      href: '/pt-br/docs/guides/telegram/'
    }
  ]
}
