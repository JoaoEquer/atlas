import type { Override } from './catalog'
import type { Messages } from './en'

const STATUSES: Record<string, string> = {
  error: 'falhou',
  ok: 'respondeu',
  running: 'rodando',
  silent: 'em silêncio',
  unknown: 'desconhecido'
}

const EFFORTS: Record<string, string> = {
  high: 'Alto',
  low: 'Baixo',
  max: 'Máximo',
  medium: 'Médio',
  minimal: 'Mínimo',
  none: 'Desligado',
  xhigh: 'Muito alto'
}

const TOOL_TITLES: Record<string, { done: string; pending: string; plain: string }> = {
  ask_user: { done: 'Perguntou', pending: 'Perguntando', plain: 'Perguntar' },
  bot_create: { done: 'Criou o bot', pending: 'Criando o bot', plain: 'Criar o bot' },
  browser_exec: { done: 'Navegador:', pending: 'Navegador:', plain: 'Navegador:' },
  conversation_search: {
    done: 'Buscou nas conversas',
    pending: 'Buscando nas conversas',
    plain: 'Buscar nas conversas'
  },
  'cronjob_manage.create': { done: 'Agendou', pending: 'Agendando', plain: 'Agendar' },
  'cronjob_manage.list': { done: 'Listou as rotinas', pending: 'Listando as rotinas', plain: 'Listar as rotinas' },
  'cronjob_manage.pause': { done: 'Pausou a rotina', pending: 'Pausando a rotina', plain: 'Pausar a rotina' },
  'cronjob_manage.remove': { done: 'Removeu a rotina', pending: 'Removendo a rotina', plain: 'Remover a rotina' },
  'cronjob_manage.resume': { done: 'Retomou a rotina', pending: 'Retomando a rotina', plain: 'Retomar a rotina' },
  'cronjob_manage.run': { done: 'Rodou a rotina', pending: 'Rodando a rotina', plain: 'Rodar a rotina' },
  'cronjob_manage.update': { done: 'Mudou a rotina', pending: 'Mudando a rotina', plain: 'Mudar a rotina' },
  'delegate.account': { done: 'Levou a tarefa para', pending: 'Levando a tarefa para', plain: 'Levar a tarefa para' },
  'delegate.list': { done: 'Listou as tarefas', pending: 'Listando as tarefas', plain: 'Listar as tarefas' },
  'delegate.resume': { done: 'Retomou', pending: 'Retomando', plain: 'Retomar' },
  'delegate.spawn': { done: 'Delegou:', pending: 'Delegando:', plain: 'Delegar:' },
  'delegate.steer': { done: 'Orientou', pending: 'Orientando', plain: 'Orientar' },
  'delegate.stop': { done: 'Parou', pending: 'Parando', plain: 'Parar' },
  delegation_search: {
    done: 'Buscou nas tarefas delegadas',
    pending: 'Buscando nas tarefas delegadas',
    plain: 'Buscar nas tarefas delegadas'
  },
  'desktop_preview.close': { done: 'Fechou a prévia', pending: 'Fechando a prévia', plain: 'Fechar a prévia' },
  'desktop_preview.open': { done: 'Abriu', pending: 'Abrindo', plain: 'Abrir' },
  edit_file: { done: 'Editou', pending: 'Editando', plain: 'Editar' },
  executor_accounts: { done: 'Leu as contas', pending: 'Lendo as contas', plain: 'Ler as contas' },
  find_files: { done: 'Buscou nos arquivos', pending: 'Buscando nos arquivos', plain: 'Buscar nos arquivos' },
  'memory.memory': {
    done: 'Atualizou as anotações:',
    pending: 'Atualizando as anotações:',
    plain: 'Atualizar as anotações:'
  },
  'memory.user': { done: 'Atualizou seu perfil:', pending: 'Atualizando seu perfil:', plain: 'Atualizar seu perfil:' },
  message_agent: { done: 'Mandou mensagem para', pending: 'Mandando mensagem para', plain: 'Mandar mensagem para' },
  plan: { done: 'Atualizou o plano', pending: 'Atualizando o plano', plain: 'Atualizar o plano' },
  'plan.read': { done: 'Leu o plano', pending: 'Lendo o plano', plain: 'Ler o plano' },
  read_file: { done: 'Leu', pending: 'Lendo', plain: 'Ler' },
  run_command: { done: 'Rodou', pending: 'Rodando', plain: 'Rodar' },
  'run_command.stop': { done: 'Parou', pending: 'Parando', plain: 'Parar' },
  'secrets.list': { done: 'Listou as chaves', pending: 'Listando as chaves', plain: 'Listar as chaves' },
  'secrets.request': { done: 'Pediu a chave', pending: 'Pedindo a chave', plain: 'Pedir a chave' },
  'skill_manage.change': { done: 'Mudou a skill', pending: 'Mudando a skill', plain: 'Mudar a skill' },
  'skill_manage.create': { done: 'Criou a skill', pending: 'Criando a skill', plain: 'Criar a skill' },
  'skill_manage.delete': { done: 'Apagou a skill', pending: 'Apagando a skill', plain: 'Apagar a skill' },
  tool_search: { done: 'Buscou nas ferramentas', pending: 'Buscando nas ferramentas', plain: 'Buscar nas ferramentas' },
  use_skill: { done: 'Carregou a skill', pending: 'Carregando a skill', plain: 'Carregar a skill' },
  web_fetch: { done: 'Abriu a página', pending: 'Abrindo a página', plain: 'Abrir a página' },
  web_search: { done: 'Buscou', pending: 'Buscando', plain: 'Buscar' },
  write_file: { done: 'Escreveu', pending: 'Escrevendo', plain: 'Escrever' }
}

const APPROVALS: Record<string, string> = {
  cronjob_manage: 'Agendar este comando?',
  delegate: 'Agir na sua própria sessão?',
  edit_file: 'Alterar este arquivo?',
  run_command: 'Rodar este comando?',
  write_file: 'Alterar este arquivo?'
}

const EXECUTOR_ACTS: Record<string, string> = {
  Bash: 'rodar um comando',
  Edit: 'alterar um arquivo',
  MultiEdit: 'alterar um arquivo',
  NotebookEdit: 'alterar um notebook',
  Read: 'ler um arquivo',
  WebFetch: 'abrir uma página da web',
  WebSearch: 'pesquisar na web',
  Write: 'escrever um arquivo',
  apply_patch: 'alterar arquivos',
  delete: 'apagar arquivos',
  edit: 'alterar arquivos',
  fetch: 'abrir uma página da web',
  move: 'mover arquivos',
  permissions: 'ter mais permissões',
  read: 'ler arquivos',
  search: 'pesquisar',
  shell: 'rodar um comando'
}

/** The screen in Brazilian Portuguese, over `en`; a key left out falls back to English. */
export const ptBR: Override<Messages> = {
  approval: {
    always: 'Sempre',
    conversation: 'Nesta conversa',
    deny: 'Recusar',
    guardianDenied: 'O guardião julgou isto perigoso. Você ainda pode permitir, uma vez.',
    once: 'Permitir uma vez',
    reasonPlaceholder: 'Por que não? (opcional, o agente lê)',
    title: (tool: string) => APPROVALS[tool] ?? `Permitir ${tool}?`
  },
  bots: {
    advanced: 'Avançado',
    cancel: 'Cancelar',
    confirmDelete: 'Apagar, com a memória e as skills dele?',
    create: 'Criar',
    delete: 'Apagar',
    description: 'Descrição',
    descriptionPlaceholder: 'Com o que este bot deve ajudar?',
    edit: 'Editar',
    editTitle: 'Editar bot',
    empty: 'Nenhum bot ainda. Um bot é um agente com missão, memória e conversa próprias.',
    reading: 'Lendo os bots…',
    emptyChat: (title: string) => `Esta é a sua conversa com ${title}. Ela nunca recomeça.`,
    everything: 'Tudo o que a casa tem',
    intro: 'Um agente com nome, e missão, memória, skills, conversa e rotinas próprias.',
    mcp: 'Servidores MCP',
    mission: 'Missão e regras',
    missionHint: 'Em branco: montada a partir do título e da descrição',
    model: 'Modelo',
    modelOfHome: 'O modelo da casa',
    name: 'Nome',
    new: 'Novo bot',
    newTitle: 'Novo bot',
    nothingToPick: 'A casa não tem nenhum.',
    routines: (count: number) => (count === 1 ? '1 rotina' : `${count} rotinas`),
    save: 'Salvar',
    skills: 'Skills da biblioteca',
    tab: 'Bots',
    title: 'Título',
    tools: 'Ferramentas'
  },
  card: {
    answering: 'Enviando sua resposta…'
  },
  lists: {
    again: 'Ler de novo',
    failed: (error: string) => `Não deu para ler: ${error}`
  },
  ask: {
    other: 'Outro',
    otherPlaceholder: 'Sua resposta',
    pickSome: 'Escolha quantas quiser',
    skip: 'Pular',
    submit: 'Responder',
    title: (count: number) => (count === 1 ? 'O agente pergunta' : `O agente faz ${count} perguntas`)
  },
  secret: {
    byBot: (bot: string) => `Pedida pelo bot ${bot}; todos os bots desta casa leem esta chave.`,
    hide: 'Esconder',
    overwrite: (when: string) => `Já existe uma chave gravada em ${when}. Colar substitui.`,
    placeholder: 'Cole a chave',
    save: 'Salvar',
    show: 'Mostrar',
    skip: 'Pular',
    sharedWithBots: 'Todos os bots desta casa leem esta chave.',
    title: (name: string) => `O Atlas pede a chave ${name}`
  },
  attachments: {
    add: 'Anexar arquivos',
    drop: 'Solte os arquivos para anexar',
    failed: (name: string, why: string) => `${name} não foi anexado: ${why}`,
    open: (name: string) => `Abrir ${name}`,
    remove: (name: string) => `Tirar ${name}`,
    size: (bytes: number) =>
      bytes < 1024
        ? `${String(bytes)} B`
        : Math.round(bytes / 1024) < 1024
          ? `${(bytes / 1024).toFixed(0)} KB`
          : `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} MB`,
    tooHeavy: (name: string, most: string) => `${name} é pesado demais: este tipo de arquivo aceita até ${most}.`,
    uploading: (percent: number) => `Enviando… ${String(percent)}%`,
    waiting: 'Espere os arquivos terminarem de enviar'
  },
  composer: {
    slashCommands: 'Comandos e skills',
    slashGroups: { command: 'Comandos', skill: 'Skills' },
    autoMode: 'Modo automático: pula o guardião e as perguntas. Os pisos continuam barrando.',
    auto: 'Auto',
    autoModeLabel: 'Modo automático',
    defaulted: (label: string) => `${label} · padrão`,
    effort: 'Esforço: o quanto o modelo pensa (nesta conversa)',
    effortLevel: (level: string) => EFFORTS[level] ?? level,
    model: 'Modelo (desta conversa, a partir do próximo turno)',
    noModel: 'Sem modelo: grave uma chave com atlas secrets set',
    placeholder: 'Mensagem para o Atlas',
    placeholderRunning: 'Corrija o rumo: isto entra depois das ferramentas em curso',
    placeholderTo: (name: string) => `Mensagem para ${name}`,
    send: 'Enviar',
    stop: 'Parar'
  },
  connection: {
    closed: 'O Atlas não está respondendo. Tentando de novo…',
    connecting: 'Conectando ao Atlas…',
    failed: (reason: string) => `Não foi possível falar com o Atlas: ${reason}`,
    opening: 'Abrindo o Atlas…'
  },
  conversations: {
    archive: 'Arquivar',
    archived: (count: number) => `Arquivadas (${String(count)})`,
    archivedMark: 'arquivada',
    archiveLater: 'Arquive quando o turno terminar',
    empty: 'Nenhuma conversa ainda.',
    reading: 'Lendo as conversas…',
    readingMore: 'Lendo mais conversas…',
    new: 'Nova conversa',
    menu: (title: string) => `${title}: renomear ou arquivar`,
    nameField: (title: string) => `Novo nome para ${title}`,
    notArchived: (reason: string) => `Não arquivada: ${reason}`,
    notRenamed: (reason: string) => `Não renomeada: ${reason}`,
    notUnarchived: (reason: string) => `Não voltou para a lista: ${reason}`,
    readingArchived: 'Lendo as conversas arquivadas…',
    rename: 'Renomear',
    search: {
      atlas: 'Atlas',
      clear: 'Limpar a busca',
      field: 'Buscar nas conversas',
      found: (count: number, more: boolean) =>
        more
          ? `Mostrando as ${count} primeiras. Mais uma palavra estreita a busca.`
          : count === 1
            ? '1 conversa'
            : `${count} conversas`,
      none: (query: string) => `Nenhuma conversa tem “${query}”.`,
      results: 'Conversas encontradas',
      searching: 'Buscando nas conversas…',
      you: 'Você'
    },
    showMore: 'Mostrar mais',
    tab: 'Conversas',
    unarchive: (title: string) => `Trazer ${title} de volta à lista`,
    untitled: 'Nova conversa',
    via: (source: string) => `pelo ${source}`
  },
  tabs: {
    close: (title: string) => `Fechar ${title}`,
    label: 'Conversas e sessões abertas',
    new: 'Nova aba',
    waiting: (count: number) => (count === 1 ? '1 esperando você' : `${count} esperando você`)
  },
  homes: {
    backedUp: (path: string, size: string) => `Backup salvo em ${path} (${size}). As chaves nunca vão no backup.`,
    backingUp: 'Fazendo o backup…',
    backup: 'Backup',
    backupTitle: 'Salvar um backup desta casa: um zip com as configurações, a memória, as skills e as conversas',
    cancel: 'Cancelar',
    checking: 'Conferindo o backup…',
    clone: 'Copiar de',
    cloneHint:
      'Leva as configurações, os modelos, os servidores MCP, as chaves, o perfil, a memória e as skills. As conversas começam vazias.',
    cloneNone: 'Nenhuma (casa vazia)',
    create: 'Criar',
    creating: 'Criando…',
    dismiss: 'Fechar',
    emptyHint: 'Começa vazia: ao abrir, o Atlas pede uma chave de modelo.',
    import: 'Importar',
    importAction: 'Importar',
    importFrom: (home: string, when: string, atlas: string, conversations: number) =>
      `Backup da casa ${home}, feito em ${when} pelo Atlas ${atlas}, com ${String(conversations)} ${
        conversations === 1 ? 'conversa' : 'conversas'
      }.`,
    importHint:
      'Vira uma casa nova, e a casa aberta agora fica como está. As chaves nunca vão no backup, então a casa nova pede uma ao abrir.',
    importing: 'Importando…',
    importTitle: 'Importar backup',
    importTooltip: 'Criar uma casa nova a partir de um backup',
    inHome: (conversation: string, home: string) => `${conversation} (casa ${home})`,
    intro: 'Cada casa é um Atlas separado: configurações, chaves, memória, skills e conversas próprias.',
    label: 'Casa',
    name: 'Nome',
    nameHint: 'Letras minúsculas, números, - e _',
    newHome: 'Nova casa',
    newName: 'Nome da casa nova',
    newTitle: 'Nova casa',
    remote:
      'Este app fala com um servidor de outra máquina: crie e importe as casas dele no terminal dele (atlas home create, atlas restore).',
    editProfile: 'Editar perfil',
    homes: 'Casas',
    menu: (home: string) => `${home}: trocar de casa ou editar`,
    picture: 'Escolher uma foto',
    pictureRemove: 'Tirar a foto',
    pictureUnreadable: 'Esse arquivo não é uma imagem que o app lê. Escolha um PNG, JPEG, GIF ou WebP.',
    profileTitle: 'Esta casa',
    profileHint: 'A foto e o nome aparecem no pé da barra lateral. O backup leva a foto.',
    switchTo: 'Trocar de casa',
    zipFilter: 'Backup do Atlas'
  },
  keys: {
    band: 'Nenhum provedor de modelo está conectado, então o Atlas ainda não responde.',
    bandAction: 'Conectar um',
    checking: 'Conferindo a chave',
    connect: 'Conectar',
    continue: 'Continuar',
    describe: {
      anthropic: 'Acesso direto aos modelos Claude, com o raciocínio mantido entre as ferramentas.',
      gemini: 'Acesso direto aos modelos Google Gemini.',
      openai: 'Acesso direto aos modelos da OpenAI.',
      openrouter: 'Centenas de modelos com uma chave só. Uma boa primeira escolha.'
    },
    failed: (error: string) => `A chave não foi guardada: ${error}`,
    getKey: 'Pegar uma chave',
    intro:
      'O Atlas conversa com um modelo usando a sua própria chave de API. Cole uma para começar: ela é ' +
      'conferida com o provedor, sem custo, e guardada criptografada nesta máquina.',
    later: 'Escolho depois',
    placeholder: 'Cole a chave de API',
    saved: 'A chave foi guardada.',
    stored: 'conectado',
    title: 'Conecte um provedor de modelo'
  },
  notification: {
    approval: 'O Atlas pede sua aprovação',
    asks: 'O Atlas tem uma pergunta',
    done: 'O Atlas respondeu',
    executor: (name: string) => `${name} espera por você`,
    secret: (name: string) => `O Atlas pede a chave ${name}`
  },
  proposals: {
    adds: 'Acrescenta',
    after: 'Depois',
    applied: 'Aplicada.',
    approve: 'Aprovar',
    back: 'Voltar à conversa',
    before: 'Antes',
    discard: 'Descartar',
    empty: 'Nenhuma proposta de memória.',
    fromConversation: 'Da conversa',
    intro:
      'A revisão em segundo plano mudaria estas entradas da memória. Nada muda até você aprovar; ' +
      'uma mudança só se aplica enquanto as entradas que ela muda estão como estavam.',
    ok: 'OK',
    overwrote: 'Sobrescreveu, inteira:',
    readd: 'Peça ao Atlas para acrescentar de volta o que você ainda quer.',
    removed: 'Tirou:',
    removes: 'Tira',
    replaces: 'Altera',
    review: 'Revisar',
    title: 'Propostas de memória',
    titleBotMemory: (bot: string) => `Mudança na memória do bot ${bot}`,
    titleMemory: 'Mudança na memória',
    titleUser: 'Mudança no seu perfil',
    whole: 'Ver antes e depois inteiros'
  },
  extensions: {
    added: 'Adicionados aqui',
    copied: 'Copiado',
    copy: 'Copiar o comando',
    fromPlugin: 'De um plugin ligado',
    fromPlugins: 'De plugins',
    inMcpFile: 'No mcp.json',
    intro: 'O que esta casa acrescenta ao Atlas, por tipo. Encontre mais na Loja.',
    mcpIntro:
      'Os servidores do mcp.json, na pasta da casa, e os dos plugins ligados. O modelo encontra as ferramentas deles pela busca.',
    mcpState: (state: string, tools: number) =>
      state === 'connected'
        ? tools === 1
          ? 'Conectado · 1 ferramenta'
          : `Conectado · ${tools} ferramentas`
        : state === 'connecting'
          ? 'Conectando…'
          : 'Deixado de fora',
    mcpSummary: (transport: string) =>
      transport === 'stdio'
        ? 'Um programa que o Atlas inicia na máquina da casa.'
        : 'Um servidor que o Atlas alcança pelo endereço dele.',
    modelOnly: 'O modelo carrega quando precisa',
    noExecutors: 'Nenhum executor ainda. O Atlas procura os harnesses que suporta toda vez que abre.',
    noMatch: 'Nada corresponde à busca.',
    noMcp: 'Nenhum servidor MCP. Adicione um no mcp.json, na pasta da casa, ou instale um pela Loja.',
    noSkills: 'Nenhuma skill ainda. O Atlas escreve uma quando aprende como você faz algo; a Loja tem mais.',
    reading: 'Lendo…',
    refresh: 'Ler de novo',
    search: 'Buscar',
    signIn: 'Ele entra com OAuth. Entre por um terminal na máquina da casa:',
    signInAgain: 'Ele entra com OAuth. Para entrar de novo, num terminal na máquina da casa:',
    signInAgainButton: 'Entrar de novo',
    signInButton: 'Entrar',
    signInNeeded: (server: string): string => `${server} espera você entrar.`,
    signInOrTerminal: 'Ou por um terminal na máquina da casa:',
    signInWaiting: 'Autorize o Atlas no navegador que abriu. Ele conecta assim que você autorizar.',
    skillSource: (source: string, plugin: string) =>
      source === 'atlas' ? 'Vem com o Atlas' : source === 'plugin' ? `Do plugin ${plugin}` : 'Desta casa',
    skillsIntro:
      'Procedimentos que o modelo carrega pelo nome quando precisa; os que você pode chamar mostram o /comando.',
    yours: 'Desta casa'
  },
  plugins: {
    approve: 'Deixar entrar',
    pageGone: 'Esta página era de um plugin que está desligado agora.',
    screenFailed: (name: string, why: string) => `A tela do plugin ${name} falhou: ${why}`,
    screenNotLoaded: (why: string) => `A tela dele não carregou: ${why}`,
    approved: 'pode falar',
    cancel: 'Cancelar',
    consentIntro: 'Ligar este plugin deixa ele:',
    defaultValue: (value: string) => `Padrão: ${value}`,
    empty: 'Nenhum plugin. O Atlas traz alguns, e você pode pôr outro como pasta na casa, em plugins/.',
    enable: 'Ligar…',
    enableNow: 'Ligar',
    disable: 'Desligar',
    intro:
      'Plugins acrescentam ao Atlas. Chegam desligados; ligar um mostra o que ele pode fazer, e nada roda antes de você concordar.',
    off: 'desligado',
    on: 'ligado',
    people: 'Pessoas',
    peopleIntro:
      'Quem pode falar com o Atlas por um canal. Quem é novo recebe um código; deixe a pessoa entrar aqui, e ela manda a mensagem de novo.',
    noPeople: 'Ninguém ainda.',
    other: 'Outro…',
    otherHint: 'Digitar um valor que não está na lista',
    list: 'Lista',
    listHint: 'Escolher da lista de novo',
    reload: 'Recarregar',
    reloadHint:
      'Carrega de novo, dos arquivos, os plugins ligados. O Atlas também faz isso quando um arquivo de um deles é salvo.',
    remove: 'Remover',
    reset: 'Padrão',
    running: 'rodando',
    save: 'Guardar',
    secretPlaceholder: 'Cole aqui',
    settings: 'Configurações',
    shipped: 'vem com o Atlas',
    stored: 'guardado',
    notStored: 'não guardado',
    changeSecret: 'Trocar',
    title: 'Plugins',
    guide: 'Guia',
    write: 'Escrever um plugin',
    writeHint: 'Uma conversa nova em que o Atlas escreve um plugin com você, confere e instala.',
    waits: (code: string) => `espera com ${code}`,
    waiting: (count: number) => (count === 1 ? '1 esperando' : `${count} esperando`)
  },
  routines: {
    confirmRemove: 'Apagar?',
    empty: 'Nenhuma rotina ainda. Peça ao Atlas para agendar uma, ou use atlas routines create no terminal.',
    failed: (name: string) => `A rotina ${name} falhou`,
    finished: (name: string) => `A rotina ${name} terminou`,
    hideRuns: 'Esconder execuções',
    intro:
      'Prompts e comandos que o Atlas roda numa agenda. Cada execução de um prompt é uma conversa própria; você é avisado quando uma termina, a não ser que ela não tenha o que dizer.',
    last: (status: string) => `última ${status}`,
    late: 'atrasada',
    next: (time: string) => `próxima ${time}`,
    noRuns: 'Ainda não rodou.',
    open: 'Abrir',
    pause: 'Pausar',
    paused: 'pausada',
    remove: 'Apagar',
    resume: 'Retomar',
    runNow: 'Rodar agora',
    showRuns: 'Execuções',
    status: (status: string) => STATUSES[status] ?? status,
    title: 'Rotinas'
  },
  marketplace: {
    back: 'Todos os itens',
    brought: (names: string) => `Também instalado, porque ele precisa: ${names}.`,
    by: (author: string) => `de ${author}`,
    cancel: 'Cancelar',
    community: 'Comunidade',
    confirmRemove: 'Remover?',
    empty: 'Nada aqui ainda.',
    entry: 'O que entra no seu mcp.json',
    homepage: 'Página',
    install: 'Instalar',
    installed: 'Instalado',
    installing: 'Instalando…',
    intro:
      'Skills, servidores MCP, plugins e executores feitos para o Atlas. Cada um foi revisado, e instala exatamente como foi revisado.',
    kinds: { executor: 'Executor', mcp: 'Servidor MCP', plugin: 'Plugin', skill: 'Skill' },
    license: (license: string) => `Licença ${license}`,
    localChanges: (name: string) =>
      `${name} foi mudado aqui depois que veio. Atualizar mesmo assim? O que ele tem agora fica guardado como uma versão que dá para trazer de volta.`,
    loadingPage: 'Lendo a página…',
    makeHint: 'O Atlas escreve um plugin com você numa conversa nova; o guia diz como publicá-lo aqui.',
    makeYourOwn: 'Faça o seu',
    guide: 'Guia de plugins',
    need: {
      program: (name: string, state: string) =>
        state === 'met' ? `${name}: está neste computador` : `${name}: não está neste computador (instala mesmo assim)`,
      server: (name: string, state: string) =>
        state === 'met'
          ? `Servidor MCP ${name}: já está aqui`
          : state === 'brought'
            ? `Servidor MCP ${name}: vem junto`
            : `Servidor MCP ${name}: falta, e a loja não tem`,
      system: (names: string, state: string) =>
        state === 'met' ? `Funciona aqui (${names})` : `Só funciona em ${names}`
    },
    needs: 'Do que precisa',
    noMatch: 'Nada combina com a busca.',
    official: 'Da equipe Atlas',
    officialBadge: 'Oficial',
    on: 'Ligado',
    paste: 'Cole o valor',
    reading: 'Lendo a loja…',
    refresh: 'Ler de novo',
    remove: 'Remover',
    removing: 'Removendo…',
    retired: 'Retirado',
    save: 'Guardar',
    screenshots: 'Capturas',
    search: 'Buscar',
    secretMissing: 'Não guardado',
    secretStored: 'Guardado',
    secrets: 'Segredos que ele lê',
    shipped: 'Vem com o Atlas',
    source: 'Código-fonte',
    stale: (problem: string, when: string) =>
      `Não deu para ler a loja agora (${problem}). Ela estava assim em ${when}.`,
    tabs: { executor: 'Executores', mcp: 'MCPs', plugin: 'Plugins', skill: 'Skills' },
    title: 'Loja',
    turnOn: 'Ligar',
    turnOnHint: 'Plugins chegam desligados. Ligar mostra o que ele declara, para o seu consentimento.',
    unavailable: 'Indisponível',
    unread: (problem: string) => `Não deu para ler a loja: ${problem}`,
    update: 'Atualizar',
    updateAnyway: 'Atualizar mesmo assim',
    updating: 'Atualizando…',
    version: (version: string) => `Versão ${version}`
  },
  soul: {
    blank: 'Em branco, vale o padrão do Atlas:',
    intro:
      'Quem o Atlas é e como ele fala, em toda conversa: o SOUL.md dele. Escreva com as suas palavras, ou peça ao Atlas para mudar. A mudança vale a partir da próxima conversa.',
    loading: 'Lendo o SOUL.md…',
    missing: 'Ainda não há SOUL.md. Escreva abaixo e salve para criar; até lá, vale o padrão do Atlas.',
    placeholder: 'Quem o Atlas é e como ele fala. Deixe em branco para usar o padrão do Atlas.',
    save: 'Salvar SOUL.md',
    saved: 'Salvo. Vale a partir da próxima conversa.',
    saving: 'Salvando…',
    title: 'Soul',
    unsaved: 'Alterações não salvas'
  },
  settings: {
    about: 'Sobre',
    aboutYou: 'Sobre você',
    aboutYouHint:
      'Quem você é, do que gosta e como trabalha: o USER.md. O Atlas lê em toda conversa e acrescenta conforme aprende.',
    add: 'Acrescentar',
    addPlaceholder: 'Uma entrada nova',
    appliesNext: 'A mudança vale a partir da próxima conversa; a que está aberta fica com o que tinha quando começou.',
    approvalTimeout: 'Espera da aprovação',
    approvalTimeoutHint: 'Segundos que uma aprovação espera por você antes de contar como recusada.',
    ask: 'Sempre perguntar antes',
    askHint: 'Comandos que perguntam mesmo no automático. Um por linha, * como curinga: git push*',
    askSkip: 'Tirados das regras do Atlas',
    askSkipHint: 'Comandos que o Atlas perguntaria por conta própria e que você libera: gh issue comment*',
    automatic: 'O primeiro provedor com chave',
    cancel: 'Cancelar',
    checkUpdate: 'Procurar atualização',
    checking: 'Procurando…',
    choose: 'Escolher…',
    close: 'Fechar',
    defaultModel: 'Modelo padrão',
    defaultModelHint:
      'Com o que responde a conversa que não escolheu modelo, a partir do próximo turno. A escolha da própria conversa fica.',
    deny: 'Comandos bloqueados',
    denyHint: 'Nunca rodam, nem no automático. Um por linha, * como curinga: git push --force*',
    edit: 'Editar',
    effort: 'Esforço',
    effortDefault: 'O padrão do modelo',
    empty: 'Nada ainda.',
    folder: 'Pasta inicial',
    folderHint: 'Onde o agente começa, nas conversas novas. Em branco, a sua pasta pessoal:',
    keys: 'Chaves dos provedores',
    keysHint: 'Cada chave é conferida com o provedor, sem custo, e guardada cifrada na máquina desta casa.',
    language: 'Idioma',
    languageHint: 'Deste computador. A janela recarrega para trocar; nenhuma conversa para.',
    languageSystem: 'O do sistema',
    languages: { en: 'English', 'pt-BR': 'Português' },
    locked: (variable: string) => `Definido por ${variable}, que vale mais que o config.toml.`,
    memory: 'Memória',
    memoryHint: 'O que o Atlas anotou sobre a sua máquina, as suas ferramentas e as manias delas: o MEMORY.md.',
    model: 'Modelo',
    noUpdates: 'Esta instalação não se atualiza pelo app.',
    proposals: (count: number) =>
      count === 1 ? '1 proposta de memória esperando' : `${String(count)} propostas de memória esperando`,
    provider: 'Provedor',
    remove: 'Apagar',
    save: 'Salvar',
    saved: 'Salvo',
    saving: 'Salvando…',
    sections: {
      extensions: 'Extensões',
      general: 'Geral',
      machines: 'Máquinas',
      model: 'Modelo',
      personalization: 'Personalização',
      safety: 'Segurança',
      workspace: 'Área de trabalho'
    },
    theme: 'Aparência',
    themeDark: 'Escuro',
    themeHint: 'Deste computador.',
    themeLight: 'Claro',
    themeSystem: 'A do sistema',
    title: 'Configurações',
    upToDate: 'O Atlas está atualizado.',
    update: 'Atualizar',
    updateAvailable: (count: number) =>
      count === 1 ? 'Há uma atualização: 1 mudança.' : `Há uma atualização: ${String(count)} mudanças.`,
    used: (used: number, limit: number) =>
      `${used.toLocaleString('pt-BR')} de ${limit.toLocaleString('pt-BR')} caracteres`,
    version: (version: string) => `Atlas ${version}`,
    zone: 'Fuso das rotinas',
    zoneHint: 'Um nome IANA, como America/Sao_Paulo. Em branco, o do sistema.'
  },
  executor: {
    allow: 'Permitir',
    answer: 'Responder',
    asks: (name: string) => `${name} pergunta`,
    deny: 'Recusar',
    other: 'Outro',
    reason: 'Por que não? (opcional, ele lê)',
    skip: 'Pular',
    wantsTo: (name: string, tool: string) => {
      const act = EXECUTOR_ACTS[tool]

      return act ? `${name} quer ${act}` : `${name} quer usar ${tool}`
    }
  },
  subagents: {
    button: (agents: number, tasks: number) =>
      [
        agents === 0 ? '' : agents === 1 ? '1 agente' : `${agents} agentes`,
        tasks === 0 ? '' : tasks === 1 ? '1 tarefa' : `${tasks} tarefas`
      ]
        .filter(Boolean)
        .join(' · '),
    close: 'Fechar',
    hint: (count: number) =>
      `${count === 1 ? '1 subagente trabalhando' : `${count} subagentes trabalhando`} · clique em um para ver detalhes`,
    map: 'Mapa de agentes',
    noOutput: 'Ainda sem saída.',
    noTool: 'começando',
    output: 'Saída',
    parent: 'Atlas, esta conversa',
    refused: 'Ele não aceita mais correção: o resultado está a caminho.',
    send: 'Enviar',
    steer: 'Corrigir',
    steerPlaceholder: 'Uma correção de rumo: entra depois das ferramentas em curso',
    stop: 'Parar',
    taskGone: 'Ela já tinha terminado.',
    tasks: (count: number) =>
      `${count === 1 ? '1 tarefa em segundo plano' : `${count} tarefas em segundo plano`} · clique em uma para ver detalhes`,
    taskStatus: (status: string, code: null | number) =>
      ({
        completed: 'Concluída',
        failed: `Falhou · código de saída ${String(code)}`,
        running: 'Rodando',
        stopped: 'Parada'
      })[status] ?? status,
    tools: (count: number) => (count === 1 ? '1 ferramenta' : `${count} ferramentas`)
  },
  executors: {
    account: 'Trocar de conta',
    accountLabel: 'A conta em que ele entra (o e-mail)',
    atlasAnswers: 'Atlas responde',
    atlasAnswersNote: 'O Atlas responde o que tem certeza; o resto continua vindo para você.',
    auto: 'Automático',
    autoNote: 'Ele não pede permissão, então passa por cima do guardião do Atlas.',
    autoWarning: (title: string) =>
      `O ${title} vai aprovar sozinho os próprios pedidos de permissão nesta sessão, sem perguntar a você: ele pode rodar comandos e editar arquivos. O classificador dele ainda revisa cada ação, e as suas configurações do ${title} continuam como estão.`,
    autoWarningCancel: 'Continuar perguntando',
    autoWarningConfirm: 'Aprovar automaticamente nesta sessão',
    autoWarningRules: (title: string) =>
      `Ele segue as próprias regras e as suas configurações do ${title}, então pode fazer commit e push no repositório deste projeto. Para que um comando continue perguntando a você, adicione uma regra de perguntar (ask) às suas configurações do ${title}.`,
    archive: (folder: string) => `Arquivar ${folder}: esconder da lista`,
    archivedFolders: (count: number) => `Pastas arquivadas (${String(count)})`,
    archivedSessions: (count: number) => `Sessões arquivadas (${String(count)})`,
    archiveSession: (title: string) => `Arquivar ${title}: esconder da lista`,
    deleteSession: (title: string) => `Apagar ${title}`,
    deleteSessionNote:
      'Apagar esta sessão? Os arquivos dela saem desta conta para sempre; as outras contas ficam com os delas.',
    deleteSessionYes: 'Apagar',
    unarchiveSession: (title: string) => `Trazer ${title} de volta à lista`,
    byAtlas: 'pelo Atlas',
    cancel: 'Cancelar',
    choose: 'Escolher…',
    commands: 'Comandos dele',
    confirm: 'OK',
    confirmTurnOn: 'Ligar',
    copyNote:
      'Sua próxima mensagem começa uma cópia: uma sessão própria, com tudo até aqui. A original fica como estava.',
    effort: 'Esforço: o quanto o modelo pensa (a partir do próximo pedido)',
    elsewhere: (where: string) => `aberta ${where === 'um terminal' ? 'num terminal' : `no ${where}`}`,
    elsewhereBody:
      'Usar nos dois lugares ao mesmo tempo pode misturar as mensagens: cada um continua de onde estava, e depois o Claude Code segue um e esquece o outro.',
    elsewhereCopy: 'Continuar numa cópia',
    elsewhereHere: 'Continuar aqui mesmo',
    elsewhereRead: 'Só ler',
    elsewhereTitle: (where: string) =>
      `Esta sessão está aberta ${where === 'um terminal' ? 'num terminal' : `no ${where}`}.`,
    empty: 'Nenhuma sessão ainda.',
    emptyFolder: 'Nenhuma sessão nesta pasta.',
    emptySession: (title: string, folder: string) =>
      `Uma sessão nova do ${title} em ${folder}. A sua primeira mensagem a começa.`,
    expired: (title: string) => `O ${title} não guarda mais esta sessão.`,
    failed: (error: string) => `Não deu para ler: ${error}`,
    folder: 'Pasta',
    folderPlaceholder: 'A pasta, a partir da sua pasta de trabalho ou inteira',
    harnesses: 'Harnesses neste computador',
    harnessesIntro:
      'O Atlas passa trabalho para os harnesses de código que você já usa, e mostra as sessões deles aqui. Ligue os que quiser: cada um roda com o seu próprio login.',
    logIn: 'Fazer login',
    lookAgain: 'Procurar de novo: acha um instalado, atualizado ou logado depois',
    looking: 'Procurando…',
    menu: (name: string) => `${name}: conta e lista`,
    model: 'Modelo (desta sessão, a partir do próximo pedido)',
    newSession: 'Nova sessão',
    which: 'Em qual executor',
    newSessionIn: (folder: string) => `Nova sessão em ${folder}`,
    noneOn: 'Nenhum harness ligado ainda.',
    reading: 'Lendo os harnesses…',
    openSettings: 'Ligar em Configurações',
    notFound: 'não encontrado neste computador',
    notLoggedIn: 'sem login',
    on: 'ligado',
    open: 'Abrir',
    openTitle: 'Abrir a sessão dele na aba Executores',
    places: { cli: 'um terminal', 'claude-desktop': 'Claude desktop', 'claude-vscode': 'VS Code' },
    placeholder: (title: string) => `Mensagem para o ${title}`,
    placeholderWorking: 'Ele está trabalhando: isto entra no turno em curso',
    readAgain: 'Ler de novo',
    showArchived: 'Mostrar pastas e sessões arquivadas',
    readOnly: 'Só leitura: nada é enviado daqui.',
    readingFolders: 'Lendo as pastas…',
    readingSessions: 'Lendo as sessões…',
    runsAs: (model: string) => `o último turno rodou como ${model}`,
    start: 'Abrir',
    stop: 'Parar',
    tab: 'Executores',
    turnOn: 'Ligar',
    openInEditor: 'Abrir o projeto no VS Code',
    unarchive: (folder: string) => `Trazer ${folder} de volta à lista`,
    untitled: 'Nova sessão'
  },
  accounts: {
    add: 'Adicionar conta',
    adding: 'Adicionar',
    cancel: 'Cancelar',
    folder: (folder: string) => `A conta em que as sessões novas de ${folder} começam`,
    logIn: 'Fazer login',
    login: 'O e-mail dela (opcional)',
    loginNote: (title: string) => `O ${title} abre o login dele no navegador: entre com esta conta.`,
    main: 'principal',
    mainAccount: 'Conta principal',
    nickname: 'O apelido dela',
    nicknameNote:
      'Ela ganha uma pasta própria ao lado da sua, com login, configurações e conversas próprios: letras, números, - ou _.',
    nicknamePlaceholder: 'empresa-b, pessoal…',
    of: 'O harness de que ela é conta',
    remove: (name: string) => `Remover ${name}`,
    removeNote: (name: string, folder: string) =>
      `Remover ${name}? A pasta dela (${folder}) fica como está, com o login e as conversas; os projetos dela voltam para a conta principal.`,
    session: 'Conta: trocar continua uma cópia desta conversa na outra conta',
    sessionBusy: 'A conta pode ser trocada quando a sessão parar de trabalhar.',
    sessionTitle: 'Conta da sessão',
    switchChecking: 'Olhando a outra conta…',
    switched: (name: string, tokens: null | number) =>
      tokens === null
        ? `Agora em ${name}. A primeira mensagem nesta conta carrega o histórico inteiro; compactar antes (/compact) carrega menos.`
        : `Agora em ${name}. A primeira mensagem nesta conta carrega o histórico inteiro, cerca de ${tokens.toLocaleString('pt-BR')} tokens de uma vez; compactar antes (/compact) carrega menos.`,
    switchCopy: 'A conversa continua numa cópia nessa conta; esta conta fica com ela como está.',
    switchCost: (tokens: null | number) =>
      tokens === null
        ? 'A primeira mensagem relê a conversa inteira, no plano dessa conta.'
        : `A primeira mensagem relê a conversa inteira, cerca de ${tokens.toLocaleString('pt-BR')} tokens de uma vez, no plano dessa conta. Compactar antes (/compact) carrega menos.`,
    switchGo: 'Trocar',
    switchMissing: (names: string) => `Essa conta não tem: ${names}.`,
    switchTitle: (name: string) => `Continuar em ${name}?`,
    switchUnknown: 'Não deu para comparar o que essa conta tem.',
    title: 'Contas'
  },
  route: {
    add: (name: string) => `Pôr ${name} na rota`,
    addStop: 'Adicionar uma parada…',
    atLimit: 'no limite',
    close: 'Fechar',
    compact: 'Compactar antes de seguir',
    compactHint: 'Na cópia em outra conta do mesmo harness, onde ele tiver /compact: a cópia lê menos e custa menos.',
    down: (name: string) => `${name} depois`,
    effort: (name: string) => `Esforço em ${name}`,
    here: 'aqui',
    itsDefault: 'o padrão dele',
    model: (name: string) => `Modelo em ${name}`,
    notLoggedIn: 'sem login',
    ready: 'pronta',
    same: 'como agora',
    used: (percent: number) => `${String(percent)}% usado`,
    empty: 'Nenhum destino ainda: no limite do plano, valem as contas do próprio executor.',
    enabled: 'No limite do plano, seguir esta rota',
    main: 'conta principal',
    note: 'No limite do plano a sessão segue para o próximo destino com login e fora do próprio limite, nesta ordem. Noutra conta do mesmo harness ela continua numa cópia; noutro harness começa lá uma sessão nova com o que esta fez.',
    notInstalled: 'não instalado',
    pill: (count: number) => (count ? `Rota · ${String(count)}` : 'Rota'),
    pillOn: (account: string, count: number) => (count ? `${account} · rota ${String(count)}` : account),
    remove: (name: string) => `Tirar ${name} da rota`,
    title: 'A rota da sessão: para onde ela segue no limite do plano',
    titleOn: 'A conta da sessão, e para onde ela segue no limite do plano',
    up: (name: string) => `${name} antes`,
    wentOn: (name: string) => `O limite do plano chegou: continuou em ${name}.`
  },
  executorPanel: {
    active: (on: number, all: number) => (on === all ? String(all) : `${String(on)} de ${String(all)}`),
    empty: 'Nenhum servidor MCP.',
    failed: (error: string) => `Não deu para ler: ${error}`,
    hideInactive: 'Esconder os fora do ar',
    loginThere: (title: string) => `O login de um servidor MCP se faz no próprio ${title} (/mcp).`,
    mcp: 'Servidores MCP',
    moreSkills: (count: number) => `Mais ${String(count)}`,
    noneActive: 'Nenhum conectado.',
    none: (title: string) => `O ${title} não diz quais servidores MCP tem.`,
    plugins: 'Plugins',
    pluginSkills: (count: number) => (count === 1 ? '1 skill' : `${String(count)} skills`),
    pluginServers: (on: number, all: number) =>
      `${String(on)} de ${String(all)} ${all === 1 ? 'servidor MCP' : 'servidores MCP'}`,
    showInactive: (count: number) =>
      count === 1
        ? 'Mostrar 1 fora do ar (precisa de login, falhou ou está desligado)'
        : `Mostrar ${String(count)} fora do ar (precisam de login, falharam ou estão desligados)`,
    skills: 'Skills',
    skillsEmpty: 'Nenhuma skill.',
    skillsFolder: (folder: string) => `Lidas em ${folder}: as do projeto entram.`,
    skillsReading: 'Lendo as skills…',
    origins: {
      account: 'Da conta',
      local: 'Só desta pasta',
      managed: 'Da organização',
      plugin: 'De plugins',
      project: 'Do projeto',
      user: 'Seus'
    },
    outsideProjects: 'Lidos fora de qualquer projeto: os do .mcp.json de uma pasta não entram.',
    reading: 'Lendo os servidores MCP…',
    refresh: 'Ler de novo',
    statuses: {
      connected: 'conectado',
      disabled: 'desligado',
      failed: 'falhou',
      'needs-auth': 'precisa de login',
      pending: 'iniciando'
    },
    tools: (count: number) => (count === 1 ? '1 ferramenta' : `${String(count)} ferramentas`),
    usage: 'Uso do plano',
    usageExtra:
      'O uso extra está ligado: passando destes limites ele continua e cobra, então não para para a próxima conta ou rota.',
    usageNone: 'Este login não tem limites de plano: quem paga é uma chave de API ou um provedor de nuvem.',
    usageReading: 'Lendo o uso do plano…',
    used: (percent: number) => `${String(percent)}% usado`,
    resets: (when: string) => `Zera em ${when}`,
    windows: {
      days: (count: number) => `Limite de ${String(count)} dias`,
      session: 'Sessão atual',
      week: 'Semana atual (todos os modelos)',
      weekOf: (model: string) => `Semana atual (${model})`
    }
  },
  status: {
    failed: 'parou com erro',
    unread: 'terminou, ainda não vista',
    waiting: 'esperando você',
    working: 'trabalhando'
  },
  layout: {
    hidePanel: 'Esconder o painel',
    hideSidebar: (keys: string) => `Esconder a barra lateral (${keys})`,
    resizePanel: 'Largura do painel: arraste, ou clique duas vezes para voltar ao padrão',
    resizeSidebar: 'Largura da barra lateral: arraste, ou clique duas vezes para voltar ao padrão',
    showPanel: 'Mostrar o painel',
    showSidebar: (keys: string) => `Mostrar a barra lateral (${keys})`
  },
  updates: {
    apply: 'Atualizar e reabrir',
    applyServer: 'Atualizar e reconectar',
    brings: (count: number) =>
      count === 1
        ? 'Uma mudança na main que este Atlas não tem:'
        : `${String(count)} mudanças na main que este Atlas não tem:`,
    button: 'Atualizar o Atlas',
    closing: 'Fechando para atualizar…',
    confirmUnverified: 'Atualizar mesmo assim, sem a verificação da CI',
    dirty:
      'Esta instalação tem mudanças próprias, que a atualização recusa: faça o commit delas ou descarte-as na pasta dela antes.',
    dismiss: 'Dispensar',
    done: (count: number) =>
      count === 1 ? 'O Atlas foi atualizado: uma mudança.' : `O Atlas foi atualizado: ${String(count)} mudanças.`,
    executors: (count: number) =>
      count === 1 ? 'uma sessão de executor trabalhando' : `${String(count)} sessões de executor trabalhando`,
    failed: (error: string) => `A atualização falhou, e o Atlas ficou como estava: ${error}`,
    how: 'O Atlas fecha, atualiza e abre de novo sozinho, em cerca de um minuto.',
    howServer:
      'O servidor prepara a nova versão enquanto continua rodando e depois reinicia por um instante: o app reconecta sozinho e diz como foi. Se algo falhar, o servidor volta à versão de agora, com os dados como estavam.',
    later: 'Agora não',
    log: 'Ver o que ela fez',
    more: (count: number) => `e mais ${String(count)}`,
    ok: 'OK',
    runningNow: 'O Atlas está se atualizando e abre sozinho quando terminar.',
    stops: (what: string) => `Fechar agora para o que está rodando: ${what}.`,
    stopsServer: (what: string) => `O reinício para o que está rodando no servidor agora: ${what}.`,
    subagents: (count: number) =>
      count === 1 ? 'um subagente trabalhando' : `${String(count)} subagentes trabalhando`,
    title: 'Uma atualização do Atlas',
    turns: (count: number) => (count === 1 ? 'uma conversa respondendo' : `${String(count)} conversas respondendo`),
    unverified: (reason: string) => `A CI não verificou esta versão: ${reason}.`,
    updatingServer: 'O servidor está se atualizando…'
  },
  preview: {
    close: 'Fechar esta prévia',
    dismiss: 'Dispensar',
    notWeb: 'Só um endereço da web abre aqui.',
    openBeside: 'Abrir ao lado',
    reading: 'Lendo o arquivo…'
  },
  terminal: {
    add: 'Novo terminal',
    cancel: 'Cancelar',
    close: 'Fechar este terminal',
    dangerous: 'O guardião de confiança acha este comando perigoso. Rodar mesmo assim?',
    empty: 'Nenhum terminal aberto.',
    exit: (code: number) => `saída ${String(code)}`,
    name: 'Terminal',
    panel: 'Painel lateral',
    run: 'Rodar no terminal',
    runAnyway: 'Rodar',
    toggle: 'Mostrar ou esconder o terminal (Ctrl+`)',
    unavailable: 'A máquina do Atlas não abre terminal: falta o pacote do terminal lá (uv sync --all-extras).'
  },
  thread: {
    compacted: 'A conversa foi compactada.',

    empty: 'Pergunte qualquer coisa ao Atlas.',
    reading: 'Lendo a conversa…',
    sending: 'Enviando…',
    queued: 'Entra quando o passo atual terminar',
    changeModel: 'Trocar de modelo',
    connectKey: 'Conectar uma chave',
    putBack: 'Devolver à caixa',
    failed: (error: string) => `O turno falhou: ${error}`,
    halted: 'O turno parou: o agente repetiu a mesma chamada.',
    interrupted: 'Parado.',
    limited: 'O turno parou num limite.',
    fileSent: 'O Atlas mandou um arquivo',
    notice: 'O Atlas foi avisado',
    other: 'desconhecido nesta versão do app',
    skill: 'skill carregada',
    steered: 'enviada durante o turno',
    summary: 'Resumo das mensagens anteriores',
    thinking: 'Pensando…',
    thought: 'Pensou',
    thoughtBriefly: 'Pensou rápido',
    thoughtFor: (duration: string) => `Pensou por ${duration}`,
    toLatest: 'Rolar até o fim'
  },
  tool: {
    cancelled: 'cancelada',
    clamped: (omitted: string) => `… mais ${omitted} caracteres cortados — use Copiar tudo para a saída inteira.`,
    copied: 'Copiado',
    copyAll: 'Copiar tudo',
    denied: 'recusada',
    failed: 'falhou',
    // The same labels in every language.
    input: 'IN',
    output: 'OUT',
    running: 'rodando',
    spilled: (path: string) => `O resultado inteiro está em ${path}`,
    andMore: (target: string, more: number) => `${target} e mais ${String(more)}`,
    titles: TOOL_TITLES,
    unanswered: 'sem resposta a tempo',
    waiting: 'esperando a saída…',
    waitingFor: {
      answer: 'esperando sua resposta',
      approval: 'esperando sua aprovação',
      key: 'esperando a chave'
    }
  },
  machines: {
    onMachine: (conversation: string, machine: string) => `${conversation} (${machine})`,
    add: 'Adicionar máquina',
    adding: 'Adicionando…',
    addTitle: 'Adicionar máquina',
    alwaysOn: 'Sempre ligado',
    alwaysOnHint: 'Roda com o app fechado e liga junto com o computador.',
    busy: 'Um turno está rodando nesta casa; espere ele terminar.',
    code: 'Código de conexão',
    codeCopied: 'Copiado. Ele leva a senha desta casa: cole só no seu próprio app.',
    codeHint:
      'Mostrado por “Abrir para as minhas máquinas” na outra máquina, ou por `atlas server code` no terminal dela.',
    copyCode: 'Copiar o código de conexão',
    cancel: 'Cancelar',
    close: 'Fechar',
    name: 'Nome',
    namePlaceholder: 'Loja',
    newToken: 'Trocar a senha',
    newTokenConfirm: 'Os códigos já dados param de funcionar. Continuar?',
    newTokenDone: 'Senha trocada: leve o código novo às suas máquinas.',
    noTailnet: 'Esperando o Tailscale:',
    open: 'Abrir para as minhas máquinas',
    openHint: 'Alcançável também das suas outras máquinas, pelo Tailscale.',
    others: 'Outras máquinas',
    othersEmpty: 'Nenhuma outra máquina ainda.',
    remove: 'Remover',
    removeConfirm: (name: string) => `Remover ${name}? O Atlas de lá não é tocado: dá para adicionar de novo.`,
    rename: 'Renomear',
    reachable: 'Alcançável',
    save: 'Salvar',
    selector: (machine: string) => `${machine}: trocar de máquina`,
    settings: 'Configurações',
    sshHost: 'Host SSH',
    sshHostPlaceholder: 'usuario@host ou um nome do ~/.ssh/config',
    test: 'Testar',
    testing: 'Testando…',
    thisComputer: 'Este computador',
    thisMachine: (home: string) => `Esta máquina · casa ${home}`,
    title: 'Máquinas',
    transport: 'Caminho',
    viaSsh: 'Túnel SSH',
    viaTailscale: 'Tailscale',
    working: 'Aplicando…'
  },
  duration: (seconds: number) =>
    Math.round(seconds * 1000) < 1000
      ? `${String(Math.max(1, Math.round(seconds * 1000)))}ms`
      : Math.round(seconds * 10) < 100
        ? `${seconds.toFixed(1).replace('.', ',')}s`
        : Math.round(seconds) < 60
          ? `${seconds.toFixed(0)}s`
          : `${String(Math.floor(Math.round(seconds) / 60))}min ${String(Math.round(seconds) % 60)}s`,
  elapsed: (seconds: number) =>
    seconds < 60
      ? `${String(seconds)}s`
      : `${String(Math.floor(seconds / 60))}:${String(seconds % 60).padStart(2, '0')}`
}
