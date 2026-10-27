# Atlas — ROADMAP

O planejamento é em português e o código é em inglês. A arquitetura mora em
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), e os nomes de conceito em [docs/GLOSSARY.md](docs/GLOSSARY.md).

Este repositório nasce pronto para ser público, por isso **nada pessoal entra aqui**: nem nome de
cliente, nem pasta de usuário, nem dado de conversa. O que é do usuário fica no `ATLAS_HOME`.

---

## O que é o Atlas

**Um agente pessoal open source e gratuito, e uma plataforma para orquestrar agentes e harnesses.**
Um agente de código trabalha dentro de um projeto. O Atlas é onde o usuário consolida tudo:
novidades, documentos, transcrições de reunião e a direção do trabalho. E ele passa adiante o que
outro executor faz melhor.

**A tese: o Atlas cresce junto com os modelos.** Harness leve melhora sozinho a cada modelo novo.
Estrutura pesada vira peso morto.

**Como se instala:** um site com quatro caminhos, todos rodando o mesmo script por baixo.
- **App Android:** em Kotlin, num repositório próprio.
- **App desktop:** em Electron, com navegador embutido e instalador assinado.
- **Servidor:** `curl -fsSL …/install.sh | sh` ou `docker compose up`.
- **Computador:** o mesmo comando, ou `irm …/install.ps1 | iex` no Windows.

O app para iPhone fica para depois. Não existe PWA.

**A prova de pronto:** um container vazio, uma chave de API e uma conversa que funciona.

**Como ganha dinheiro:** o lançamento é grátis, com a chave de API do próprio usuário. Depois vem o
Atlas Cloud, que vende crédito em reais e usa uma chave da OpenRouter por cliente. Não existe plano
"ilimitado", porque uma fala do agente manda dezenas de milhares de tokens.

---

## Desenho decidido

### Núcleo e plugins

- **Núcleo** (vem em toda instalação):
  - conversa e modelos;
  - ferramentas, MCP e skills;
  - memória;
  - jobs, tarefas, cron e subagentes;
  - agentes de domínio;
  - delegação a outros harnesses: o contrato, a aba Executores e o SDK de executores; cada harness
    é um plugin (decidido em 14/10: Bancada de harnesses, abaixo; a tela redesenhada em 15/10: Aba
    Executores e painel lateral, abaixo);
  - navegador com painel no app;
  - várias máquinas;
  - confiança: aprovações, modo automático, contrato de destino, plano registrado e registro do
    turno;
  - casa: servidor, protocolo, cofre, perfis, backup e push.
- **Plugins oficiais** (vêm desligados): executores (Claude Code, Codex, Hermes, Pi, DeepSeek;
  ligados no onboarding quando detectados), canais (Telegram, WhatsApp), voz, juiz e aprendizado,
  linha do tempo, notificação inteligente e laboratório (replay, bancada de modelos, visualização).
- **Ficam no V3 e não vêm:**
  - grafo de conhecimento;
  - acontecimentos extraídos por IA;
  - heartbeat (o resgate de cron atrasado vira regra do cron);
  - o bloco de memória colado em toda fala.

### Memória (decidido em 09/10)

Em 07/10 o formato escolhido foi o do Claude Code: um índice sempre no prompt e uma nota por
assunto. Em 09/10 ele saiu, porque o caderno do Claude Code funciona por ser um por projeto. Um
caderno só, para todos os assuntos do usuário, poria em toda fala linhas de outro assunto: é o
defeito do bloco colado do V3 em tamanho menor. O Atlas usa o formato de um assistente pessoal,
que é o que ele é.

Quatro peças, todas texto:
1. **Perfil** (`memory/USER.md`). Quem é o usuário e como trabalhar com ele, com teto de 1.375
   caracteres.
2. **Memória** (`memory/MEMORY.md`). Os fatos duráveis que o agente aprendeu, com teto de 2.200
   caracteres.
   - As duas vão inteiras no prompt. São lidas no começo da conversa e de novo depois de uma
     compactação, para não quebrar o cache no meio da conversa.
   - O agente escreve com a ferramenta `memory`: acrescenta, troca e tira entradas, e
     várias numa chamada só. Acima do teto ela recusa e mostra as entradas, e o agente consolida
     na hora.
   - Não entra o que muda em uma semana, o andamento de uma tarefa nem o que se acha nas
     conversas. Procedimento vira skill.
   - **Revisão em segundo plano**:
     - a cada 10 falas do usuário numa conversa sem o agente ter salvo memória, uma cópia do agente
       relê a conversa e salva o que vale;
     - usa o modelo da conversa (o cache barateia), a `memory` só para acrescentar, e as
       ferramentas de skill (item 4);
     - para em 16 chamadas, ou quando os tokens de entrada passam de 75% da janela do modelo (no
       máximo 600 mil; 120 mil quando a janela é desconhecida);
     - entra no livro de chamadas como `background_review` (no passo 6 era
       `memory_review`; com as skills, a mesma revisão cuida das duas).
3. **Conversas.** Guardadas cruas e para sempre no `atlas.db`.
   - A compactação arquiva as mensagens antigas, que continuam na busca.
   - A `conversation_search` faz busca por palavra, rolagem dentro de
     uma conversa, leitura de uma conversa e lista das recentes, sem IA no meio. Filtra por
     período e deixa de fora as conversas já lidas. Fica atrás da `tool_search`.

**Textos refeitos para gastar menos contexto** (decidido em 09/10). A primeira versão foi refeita:
a descrição inteira da `memory`, que não se enxuga por ser crítica demais; a orientação curta no
prompt, que não repete a descrição; o pedido da revisão com os dois arquivos; e a busca de conversas
enxuta, com o ensino nas respostas.
4. **Skills.** A memória de procedimento, no formato do Claude Code. Que aprendem
   (passo 6b, decidido em 09/10):
   - **A ferramenta `skill_manage`** cria, corrige, ganha arquivos de apoio e apaga, numa
     lista de operações aplicada inteira ou desfeita inteira.
   - **A revisão em segundo plano** também cuida de skills: a cada 10 chamadas ao modelo sem mexer em
     skill, com um pedido (corrigir a skill em uso, depois ampliar uma, depois um arquivo de
     apoio, só por último criar; regras e não diário; nada de falha de ambiente nem frase negativa
     sobre ferramenta). Quando vence junto com a de memória, vai um pedido só.
   - **A revisão pode melhorar qualquer skill**, inclusive as do usuário: protegê-las
     faz a revisão recusar lições e criar skills quase repetidas.
     Ela lê a skill antes de mudá-la e nunca apaga.
   - **A versão anterior é guardada antes de toda escrita** (do V3, que perdeu uma skill reescrita
     três vezes sem rastro): 20 por skill, e `atlas skills revert` volta a anterior.
   - **O dono de cada skill:** só a que a revisão criou é do agente. As outras são do usuário.
   - **O curador**, sem IA: a cada 7 dias, arquiva a skill do agente que ficou 30 dias
     sem ser carregada nem mudada (contando da criação), nunca uma do usuário, e nunca apaga:
     `atlas skills restore` traz de volta. A marca de "velha" aos 14 dias fica de fora:
     ela não mudaria nada, só apareceria numa tela de status, e sai da data do último uso quando houver
     tela. Juntar skills por IA fica de fora.
   - **A revisão não troca nem tira entrada sozinha**: uma
     revisão sem ninguém olhando pode apagar regras do usuário. A troca ou a remoção que ela quer vira
     proposta que o usuário aprova ou descarta (Propostas da revisão, abaixo; passo 9b). A revisão
     só de skills nem recebe a `memory`.

**Sem biblioteca.** Os arquivos do usuário são achados onde estão,
pela `find_files`.
- Ela também lê dentro de PDF, Office, OpenDocument, RTF e EPUB, como o ripgrep-all (`rga`): o
  Atlas converte o documento uma vez, guarda o texto, e o ripgrep busca nele.
- Medido em 09/10: o ripgrep sozinho não achou a palavra em nenhum de 31 documentos reais que o
  conversor leu.
- Abrir um processo Python com o conversor custa 0,8 s. Por isso a conversão roda dentro do Atlas,
  e não pelo `--pre` do ripgrep, que abriria um processo por documento a cada busca.

**A busca é por palavra** (FTS5, sem acento). O vetor fica para depois, como extra,
se o uso mostrar buscas que falham por sinônimo.

**Depois:**
- cadernos por frente, no formato do Claude Code, quando existirem frentes ou agentes de domínio
  (passo 13);
- a proteção contra texto malicioso na memória, no passo 7.

### Ferramentas

**13 sempre ligadas.** Em 09/10, a memória nova trouxe a `memory`, e a `memory_search` virou a
`conversation_search`. No passo 6b entra a `skill_manage`, sempre ligada, que a deixa
no conjunto de trabalho. Medido em 09/10: as 12 custam 2.482 tokens no gpt-6-luna (a `skill_manage`,
486) e 3.571 no gemini-3.7-flash. Para caber na meta, a lista da `use_skill` corta cada descrição em
60 caracteres: uns 20 tokens por skill. A meta é medida no
gpt-6-luna (decidido em 09/10): o Gemini conta uns 44% a mais para as mesmas definições. A `conversation_search` e o `plan` ficam atrás da `tool_search`: o
modelo as acha e usa como quando estavam sempre visíveis, e as tarefas de vários passos gastam menos
tokens. A `ask_user` continua sempre ligada, porque escondida ela quase deixa de ser usada:
- `read_file`, `write_file`, `edit_file` e `find_files`;
- `run_command`;
- `web_search` e `web_fetch`;
- `memory`;
- `ask_user`;
- `delegate`;
- `use_skill` e `skill_manage`;
- `tool_search`.

O resto chega pela `tool_search`, inclusive as ferramentas de MCP e de plugin. A meta é cair de cerca
de 10,8 mil para cerca de 3 mil tokens fixos por chamada ao modelo. As ferramentas de arquivo são
nativas, então o núcleo não depende de Node.

### `plan`, `ask_user` e `delegate` (decidido em 09/10)

- **`plan`** é a lista de tarefas do turno. Uma ferramenta só: sem argumentos, ela lê a lista; com
  argumentos, grava a lista inteira ou mescla por id.
  - Cada item tem id, texto, estado (pendente, em andamento, concluído, cancelado) e um item-pai
    opcional.
  - Só um item fica em andamento por vez, e um item só é concluído depois de verificado.
  - A lista volta para o contexto depois de cada compactação.
- **`ask_user`** pergunta ao usuário no meio do turno.
  - Cada chamada traz de 1 a 5 perguntas, cada uma com até 4 opções (a recomendada primeiro) e
    "Outro" acrescentado pela tela. Também aceita múltipla escolha e texto livre.
  - O turno espera a resposta, venha ela do app, do celular ou de um canal.
  - A resposta diz se foi respondida, pulada, cancelada, se o tempo acabou ou se não foi entregue.
  - Não serve para aprovar ação perigosa: isso é da confiança (passo 7).
- **`delegate`** é uma ferramenta só, com duas formas por trás.
  - **Subagentes do Atlas** (passo 12):
    - contexto zerado, com objetivo e contexto autossuficientes;
    - várias tarefas em paralelo, em fundo;
    - listar, corrigir o rumo ou parar cada um no meio;
    - o pai recebe só o resumo;
    - o subagente não pergunta ao usuário, não grava memória e não delega de novo.
  - **Executores externos** (Claude Code, Codex, Hermes), integrados no laço do harness chamado
    (passo 13):
    - os eventos do executor aparecem no Atlas como os do próprio turno: texto, raciocínio,
      ferramentas e plano;
    - as perguntas e aprovações que ele faz no meio do turno chegam ao usuário no Atlas e voltam
      respondidas;
    - o Atlas pode cancelar.
  - Os caminhos dos executores externos:
    - ACP para os harnesses que o falam (Claude Code por adaptador, Gemini CLI, Hermes, OpenCode);
    - o `app-server` nativo do Codex;
    - o Agent SDK, se for o melhor caminho para o Claude Code.
  - No passo 13, conferir a regra da Anthropic sobre usar a assinatura dentro de programas de
    terceiros.
- No passo 5, `plan` e `ask_user` saem completos. O `delegate` sai com a interface pronta para
  transmitir eventos e repassar perguntas, e é implementado de verdade nos passos 12 e 13.

### Confiança (decidida em 10/10)

- **O portão** (modo `smart`, o padrão):
  - o comando do `run_command` passa pelos pisos, que barram sempre (`rm -rf /`, apagar a casa,
    formatar disco, bomba de processos), e depois pela lista de padrões perigosos;
  - o comando marcado vai a um guardião: um pedido curto ao modelo da conversa, que aprova, nega
    ou devolve ao usuário. O texto do comando é tratado como não confiável (comentário tirado,
    comando entre marcas);
  - devolvido ao usuário, ele escolhe uma vez, nesta conversa, sempre, ou recusa. Sem ninguém para
    responder (rotina, celular fora), recusa;
  - depois de 3 recusas seguidas do guardião, a resposta manda parar e contar ao usuário.
- **Arquivos e MCP:** escrever em pasta do sistema é recusado, e mudar a
  configuração do SSH pergunta. O servidor MCP marcado como não confiável no `mcp.json` pergunta
  antes de cada chamada que pode escrever; por padrão todo servidor é confiável.
- **Modo automático:** o botão ⚡ por conversa, desligado por padrão, como o ⚡ do V3.
  Ligado, pula o guardião e as perguntas; os pisos continuam barrando.
- **Texto de fora não manda:** uma varredura recusa gravar na memória um texto com padrão
  de injeção, troca por `[BLOCKED]` a entrada suspeita na fotografia da memória, e embrulha o
  resultado da busca, da página e do MCP num bloco que diz ao modelo que aquilo é dado, não
  instrução. A gravação de skill pelo agente só é varrida com uma opção que vem desligada.
- **Ficam de fora:** um classificador que revisa toda ação e custaria uma chamada de
  modelo por comando; as faixas por reversibilidade do V3; e a conferência da entrega do V3.
- **Depois:** a lista de destinos conhecidos (entregar para destinatário novo pergunta, como o
  M-76 do V3) vem no passo 14b, com o Gmail (decidido em 13/10); as propostas
  da revisão (trocar ou tirar entrada de memória) vêm no passo 9b, depois da tela (Propostas da
  revisão, abaixo).

### Servidor (decidido em 10/10)

O desenho é uma conexão de mão dupla em que o servidor também pergunta ao app, com id, e pode
retirar a pergunta.

- **JSON-RPC 2.0 sobre WebSocket**, em `/ws`, mais o `/health`. Fica de fora a API compatível com a
  OpenAI e uma rota `/v1/runs`, que servem a programas de terceiros, e as rotas REST gêmeas do V3:
  o cron do passo 12 roda dentro do processo.
- **As perguntas do agente são pedidos do servidor ao app**: a
  `ask_user` e as aprovações. Qualquer app ligado à conversa responde, a primeira resposta vale, e
  os outros apps recebem `request.cancel`. Sem app nenhum ligado, a resposta é "não entregue" na
  hora, sem esperar o prazo. Parar o turno retira as perguntas abertas. O prazo é
  de 300 s.
- **O turno vive no servidor, não na conexão:** fechar o app não para o turno. Cada evento ganha um
  número por conversa, o servidor guarda os últimos 512 (até 4 MiB), e o app que volta pede os que
  perdeu; as perguntas ainda abertas chegam de novo. Uma marca por processo avisa que o servidor
  reiniciou.
- **Os nomes dos eventos:** `message.delta`, `reasoning.delta`, `tool.start`,
  `tool.progress`, `tool.complete`, `status.update`, `message.complete`, `review.summary` e
  `request.cancel`.
- **Mensagem enviada com o turno rodando entra entre ferramentas**: ela entra no histórico depois da rodada de
  ferramentas em curso; se o turno acabar antes, vira o turno seguinte. Parar o turno devolve ao app
  as que ainda não entraram.
- **Um turno por conversa**, e conversas em paralelo.
- **A montagem do agente é um assunto do núcleo**, `atlas.assistant`,
  que serve o terminal, o gateway, o desktop e o cron: o laço, a conversa guardada, as instruções,
  as ferramentas, o portão e a revisão. O cron (passo 12) e os canais (passo 16) usam a mesma.
- **A revisão em segundo plano** roda depois do turno, numa tarefa à parte, sem segurar o turno
  seguinte, e avisa o app com `review.summary` ("Memória atualizada · Skill x corrigida").
  O resumo diz só o que as ferramentas da própria revisão fizeram: até o
  passo 9b ele comparava o antes e o depois, e contava o que outra conversa mudou no meio.
  **Clicar na linha de uma skill abre o diff dela** (pedido no uso, 13/10): o que a revisão mudou
  naquela skill, do antes da primeira escrita ao depois da última, montado das próprias escritas da
  revisão, com limites de 6
  arquivos e 80 linhas; a revisão roda calada, e a linha é tudo o que o usuário vê. O diff tem
  1 linha de contexto em vez de 3, e a marca só nas palavras que mudaram, porque a linha de uma
  skill é um parágrafo inteiro. A linha só existe ao vivo: reabrir a conversa não a traz de volta.
  **O curador** é conferido a cada minuto e roda com 2
  horas sem turno e 7 dias desde a última passada.
- **O protocolo mora num lugar só** (`atlas.server.protocol`): cada método, evento e pedido é
  declarado com o seu formato, o servidor recusa nome e parâmetro que não estão lá, e um teste falha
  se o servidor atender algo fora da lista (o M-51 do V3 achou 8
  métodos em uso sem declaração). Dataclasses da biblioteca padrão, no lugar do Pydantic,
  pela regra do núcleo curto; o TypeScript gerado vem no passo 9, com o app.
- **Segurança no 127.0.0.1:** só aceita conexão do próprio computador, confere o
  Host e o Origin (uma página aberta no navegador consegue abrir um WebSocket local) e exige uma
  senha. A senha vem de `ATLAS_SERVER_TOKEN`, passada por quem abre o servidor (o desktop, o script
  de prova, o Docker); sem ela, o servidor sobe com uma senha nova que ninguém conhece. Ela viaja no
  cabeçalho do WebSocket, nunca na URL: o V3 gravou a dele em claro em 583 linhas de log por ela
  estar na URL.
- **A pasta de trabalho** é `[workspace] folder`, por padrão a pasta do usuário.

### App desktop (decidido em 11/10)

Um app enxuto: umas 6 dependências de execução. Mora em `apps/desktop/`.

- **O app sobe o servidor e passa a senha:** roda `atlas serve --port 0` com uma
  `ATLAS_SERVER_TOKEN` nova a cada subida; o servidor escreve `ATLAS_SERVER_READY port=N` na saída,
  e o app confere o `/health` e o WebSocket antes de abrir a tela. A porta 0 não briga com um
  `atlas serve` à mão nem com o Docker na 18790.
  - **O servidor sai quando o app morre** (o vigia): o app passa o seu PID, e o servidor
    confere a cada 2 s. Conferir também a hora de início do processo pediria o
    `psutil`; fica só o PID. No Windows o servidor segura o processo do
    app desde a subida, então um PID reaproveitado não o engana.
  - **Ao fechar, o app derruba o servidor na hora**: o grupo
    de processos no Linux e no Mac, a árvore com `taskkill /T /F` no Windows. Esperar o vigia no
    Windows é um impasse: o Electron não sai enquanto o filho tem os canais abertos, e o filho
    espera o Electron sair (achado pelos testes em 11/10, uma saída em três). Servidor que morre
    sozinho é subido de novo, até 3 vezes por minuto.
  - **Servidor já rodando:** `ATLAS_DESKTOP_URL` e `ATLAS_DESKTOP_TOKEN`.
  - No passo 9 o app roda o Atlas do repositório (`uv run`); o empacotado vem no passo 18.
- **O cliente do protocolo:**
  - pedidos do servidor respondidos uma vez só, e um cartão que quebra responde erro na hora, para
    o turno não esperar o prazo;
  - um `ping` a cada 15 s, e a conexão cai sem resposta em 45 s; julgada só pelo `ping` sem
    resposta, porque uma janela escondida atrasa o relógio, e qualquer
    mensagem recebida conta como vida;
  - a volta pede os eventos pelo número e segura os que chegam ao vivo enquanto isso; uma lista
    cortada não é mostrada, e marca nova quer dizer que o servidor reiniciou: o app
    relê a conversa;
  - reconexão com espera crescente e sorteada, de 300 ms a 15 s; uma conexão que cai em menos de
    5 s conta como falha;
  - o `conversation.open` devolve as mensagens e o número do último evento juntos, então a disputa
    entre ler a conversa e receber o perdido não existe;
  - ficam de fora os vários perfis e conexões, os tickets OAuth e o `client.capabilities`.
- **O protocolo em TypeScript é gerado** do `atlas.server.protocol` por `scripts/gen_protocol.py`,
  e um teste do pytest falha quando o arquivo gerado
  está velho. Fica de fora um arquivo OpenRPC.
- **A pilha, nas versões de 10/10:** React, Vite, TypeScript, Tailwind,
  `nanostores` para o estado e `streamdown` para o Markdown que chega aos pedaços, com um `.npmrc`
  de versão exata e nenhum pacote com menos de 14 dias. O Electron é o 43,
  porque o 40 saiu do suporte e tem um alerta alto de segurança; e o ESLint é o 10.11,
  porque o 10.12 tem menos de 14 dias. Fica de fora o `assistant-ui`, que seria
  dono do estado da conversa: aqui o estado é o fluxo de eventos do servidor. A janela é fechada: página isolada sem Node, janela nova
  negada, link de fora no navegador do sistema, e o preload só entrega a conexão.
- **Os textos vêm de um catálogo tipado**: `en` inteiro, e o `pt-BR` depois só com o
  que muda, e uma chave desconhecida falha no `tsc`. Sem biblioteca.
- **A tela:**
  - as conversas à esquerda, com "Nova conversa", e a conversa à direita;
  - a resposta aos pedaços em Markdown, o raciocínio recolhido, cada ferramenta numa linha que gira
    enquanto roda e abre o resultado num clique;
  - o ⚡ ao lado do enviar; com o turno rodando, Enter manda a
    mensagem no meio do turno e o botão vira parar, e parar devolve à caixa o que não entrou;
  - o cartão da `ask_user` (de 1 a 5 perguntas, opções, "Outro", múltipla escolha, pular) e o de
    aprovação (uma vez, nesta conversa, sempre, ou recusar com motivo); o app espera 300 s, o prazo
    do servidor;
  - o resumo da revisão numa linha discreta, e uma faixa enquanto a conexão volta;
  - o aviso do sistema quando termina um turno numa conversa que não está na tela.
- **Os fiscais de TypeScript rodam no Windows e no Linux**, ao lado dos cinco do Python (o app
  é usado no Windows, e subir e fechar o servidor é o que muda entre
  os dois): `tsc` estrito em tudo, inclusive o código do Electron, `eslint`
  com tipos, `prettier`, `vitest`, e o Playwright de ponta a ponta, com o app real, o `atlas serve`
  real e o servidor de modelo falso.

### Propostas da revisão (decididas em 11/10)

A troca e a remoção que a revisão quer ficam guardadas para o usuário aprovar depois: uma
consolidação em segundo plano que aplica direto apaga o que contradiz.

- **O que vira proposta.** A revisão em segundo plano que tenta trocar ou tirar uma entrada não
  aplica nada: a chamada inteira vira proposta, inclusive os acréscimos que vieram junto, que só
  cabem no teto com a remoção.
  - Antes de guardar, a chamada é conferida como se fosse aplicada agora: entrada não achada,
    ambígua, acima do teto ou com injeção é recusada na hora, como a escrita direta.
  - Cada troca ou remoção guarda o texto inteiro da entrada que pega.
  - A conversa continua gravando direto. Um portão que faz toda escrita esperar aprovação
    fica de fora.
- **Onde fica:** uma tabela `memory_proposals` no `atlas.db`, com o registro (as operações
  com a entrada inteira, o alvo, a conversa da revisão e a hora), como o uso das skills foi para
  tabela no lugar de um JSON. O critério de banco ou arquivo foi escrito junto, na arquitetura
  (Persistence).
- **Como chega ao app**, pelos dois caminhos:
  - a linha do resumo da revisão diz que há uma mudança esperando, com "Revisar";
  - a proposta aparece como cartão na conversa em que a revisão rodou, até ser decidida;
  - a barra lateral mostra quantas propostas esperam, só quando há alguma, e abre a lista de todas;
    um app sem onde revisar propostas as deixa acumular em silêncio;
  - cada cartão mostra a entrada inteira que sai e o texto novo inteiro, com Aprovar e Descartar.
    Aprovada, mostra o que foi sobrescrito ou tirado, para o usuário pedir de volta o que ainda
    quer.
- **Aprovar aplica só se a entrada guardada ainda está igual** (aprovar uma proposta velha apagaria
  uma entrada mais nova que ninguém viu). Se mudou, nada se aplica e a proposta fica.
  - **Opção nova**: a lista confere ao abrir e marca a proposta que não pode mais ser aplicada, com
    o motivo e só o Descartar. O cartão não oferece um
    Aprovar que só pode falhar.
- **Não vencem.** A fila de aprovação do M-42 do V3, que vencia, fica de fora.
- **Métodos:** `memory.proposals`, `memory.approve` e `memory.discard`, e um evento para todos os
  apps quando a lista muda, para o contador ficar certo. No terminal, `atlas memory proposals`,
  `approve` e `discard`. Um `all` fica de fora:
  cada cartão tem o seu botão.

### Container (decidido em 11/10)

Vem do container do V3 (M-87 e M-89), com usuário comum e o estado num volume. Para a prova de
pronto faltavam três peças: ninguém sabia a senha do servidor, nenhum modelo estava escolhido, e o
app nunca tinha falado com um servidor de fora.

- **Sem modelo nas configurações, vale o primeiro provedor com chave no cofre**.
  A ordem é a do catálogo (OpenAI, OpenRouter, Gemini), com o modelo
  padrão de cada um.
  - A escolha é feita no primeiro turno de cada conversa, então uma chave gravada com o servidor
    rodando vale na mensagem seguinte.
  - Provedor local (Ollama, vLLM) nunca é escolhido sozinho.
  - Sem chave nenhuma, o turno termina dizendo como gravar uma.
  - Um assistente de configuração fica para o
    instalador (passo 18).
- **A senha do servidor no container** é uma senha guardada:
  - a `ATLAS_SERVER_TOKEN` passada continua valendo;
  - sem ela, um servidor que escuta fora do 127.0.0.1 usa a senha guardada no cofre, criada na
    primeira vez;
  - `atlas server token` mostra a senha (e a cria, se ainda não existe), como o
    `atlas instancia token` do V3, e o app a recebe por `ATLAS_DESKTOP_TOKEN`;
  - no 127.0.0.1 nada muda: sem senha passada, o servidor cria uma que ninguém conhece, como no
    passo 8.
- **O tini é o processo 1** (o `init: true` do V3).
  - Ele recolhe os processos órfãos do `run_command` e dos servidores MCP, que sem ele viram
    zumbis.
  - Fica na imagem, então vale também para `docker run`.
  - O s6 fica de fora: ele supervisiona vários perfis num container só.
- **A pasta do usuário fica no volume** (`HOME=/data/home`, como no V3). A pasta de trabalho do agente começa nela, e o que ele cria sobrevive à troca da
  imagem.
  - Fica de fora a troca de UID, que serve para pasta do computador montada no container.
  - O Atlas usa um volume nomeado, onde o SQLite em WAL funciona. Pasta montada
    no Docker Desktop corrompe o WAL.
- **Como se conversa com o container:** pelo app, com `ATLAS_DESKTOP_URL=ws://127.0.0.1:18790/ws`
  e `ATLAS_DESKTOP_TOKEN`. A chave entra por `docker compose exec atlas atlas secrets set
  OPENAI_API_KEY`, pela entrada padrão.
- **O CI:** o job de Docker monta a imagem e roda contra ela, com
  o `docker-compose.yml` de verdade, o caminho da prova:
  - o container sobe vazio, e a senha sai do `atlas server token`;
  - um turno sem chave diz como gravar uma, e a chave entra pelo `secrets set`;
  - um turno roda uma ferramenta dentro do container e responde;
  - a conversa e os arquivos sobrevivem ao restart;
  - o processo não roda como root, e os zumbis são recolhidos.

  O único dublê é o endereço da OpenAI, apontado para o modelo falso num container ao lado. O app
  ligado a um servidor de fora ganha um cenário no e2e, contra um `atlas serve` comum, no Windows e
  no Linux.

### Backup e várias casas (decidido em 11/10)

Do V3 (M-83 a M-86) vêm o manifesto conferido e a lição da fronteira.

- **O backup é um zip só, sem chaves.** O Atlas segue a fronteira da casa (decidida em 07/10, do incidente do V3). Por
  isso backup e exportação viram uma coisa só: `atlas backup` e `atlas restore`.
  - **Entra:** o que é `user` na fronteira (`config.toml`, `models.toml`, o banco, `mcp.json`,
    `skills/`, `memory/` e, no container, `home/`).
  - **Fica de fora:** o cofre, os logs, os textos de documento e os restos de um dia.
  - **Depois de restaurar noutra máquina,** a chave é gravada de novo.
- **Como sai:**
  - o banco é copiado pela API de backup do SQLite, segura com o servidor rodando;
  - o zip nasce com outro nome e só ganha o nome final inteiro, e um zip incompleto nunca é
    publicado por cima do bom;
  - `atlas backup` termina com erro quando falta um arquivo;
  - por padrão vai para `~/atlas-backup-<casa>-<hora UTC>.zip` e guarda os 3 últimos, sem nunca
    apagar um bom por causa de um incompleto;
  - `-o -` escreve na saída padrão, o caminho do container:
    `docker compose exec -T atlas atlas backup -o - > atlas.zip`.
- **O manifesto, do V3:** um `manifest.json` na frente do zip diz o formato, a casa, a hora, a
  versão do Atlas e as contagens do banco. O backup é conferido depois de escrito (cada membro do
  zip e o `integrity_check` do banco), porque "backup não conferido não é backup".
- **Restaurar sobrescreve:**
  - antes de tocar na casa, confere o zip inteiro, recusa um formato ou um banco de um Atlas mais
    novo, e recusa caminho para fora da pasta;
  - pede confirmação quando a casa já tem dados (`--yes` pula);
  - escreve o banco por dentro do arquivo vivo, para um servidor rodando ver o mesmo dado, e avisa
    que o certo é parar o servidor antes;
  - avisa quando o backup tinha menos conversas ou mensagens que o banco que ele substituiu;
  - só escreve o que é `user`: o cofre e o resto da máquina ficam como estão.
- **Backup automático antes de migrar o banco**: quando um Atlas novo
  vai rodar uma migração num banco que já tem dados, faz um backup em `backups/` na casa e guarda
  os 5 últimos. O backup diário fica para o cron (passo 12).
- **Várias casas**, com o nome de casa porque no Atlas "perfil" é o
  `USER.md`.
  - A casa de hoje (`~/.atlas`) é a `default`, e as outras moram em `~/.atlas/homes/<nome>/`, cada
    uma inteira: configurações, banco, memória, skills, logs e cofre próprio.
  - `atlas --home <nome> <comando>` escolhe uma casa. `atlas home use <nome>` grava a casa padrão
    num arquivo `active_home`. O `ATLAS_HOME` continua valendo.
  - **Comandos:** `list`, `create` (vazia, ou `--clone` de outra), `use`,
    `show`, `rename` e `delete`. Ficam de fora o `alias` (cada casa virar um comando), um
    `describe` e as distribuições por git.
  - **O `--clone`** copia configurações, modelos, MCP, o cofre com as chaves, o
    perfil e a memória e as skills; conversas e banco começam vazios. Sem `--clone`, a casa nasce
    vazia.
  - **Um servidor por casa:** o servidor segura a casa enquanto roda, e um segundo é recusado,
    porque dois processos gravando a memória da mesma casa se atropelam. A casa com
    servidor rodando não é renomeada nem apagada.
  - Cada backup é de uma casa.
- **O app tem um servidor por casa aberta.**
  - A barra lateral mostra a casa e a lista das outras, que o servidor dá.
  - Trocar sobe o servidor daquela casa, se ainda não está vivo, e mantém os outros: um turno de
    outra casa continua rodando e avisa quando termina.
  - No máximo 3 servidores vivos; ao abrir um quarto, sai o usado há mais tempo que não tem turno
    rodando. Servidor local não cai por ficar parado, porque roda as rotinas da casa (passo 12).
  - O app abre a casa do `atlas home use`. Criar casa continua no terminal. (Mudado em 15/10, no
    passo 10c: o app abre na última casa usada, e cria, faz backup e importa; Casas no app, abaixo.)
  - Com `ATLAS_DESKTOP_URL`, o app fala só com aquele servidor.
- **Fica de fora, do V3:** o despejo das tabelas em JSONL (um backup velho restaurado num Atlas novo
  só migra para a frente) e a recusa por falta de espaço.

### Primeira troca (decidida em 11/10)

O usuário passa a usar o V4 no dia a dia.

- **O que conta como prova** (regra do Atlas: a etapa fecha pelo uso):
  - 7 dias seguidos em que o V4 é o único Atlas de conversa; o V3 só roda as rotinas dele;
  - uma lista mínima de conversas reais, sem roteiro, cada item ao menos uma vez: um arquivo do
    computador achado e lido pela `find_files`; uma busca na web; uma memória gravada e usada numa
    conversa nova; uma skill criada ou corrigida; um cartão de pergunta ou de aprovação; uma conversa
    que compacta; uma troca de modelo no meio da conversa; e um backup restaurado numa casa de teste;
  - cada defeito achado vira teste e conserto, e entra na lista "Achados do uso" da linha 11, sem
    nada pessoal;
  - quem confere é o agente que desenvolve, lendo o livro de chamadas e o banco da casa real com a
    licença do usuário; o usuário só usa, sem rotular nada à mão;
  - fecha com a prova real do passo 10b (`scripts/prove_homes.py`).
- **Uma cópia só para uso** (separada de onde se desenvolve): um clone do repositório que acompanha o `main`. O app roda o
  `atlas serve` da pasta em que está, então rodar da pasta de desenvolvimento levaria código pela
  metade, e a migração de um passo aberto, ao uso do dia a dia.
  - **`atlas desktop`** roda o app do código: sob uma trava que o
    `atlas update` também usa, instala as dependências do app quando o `package-lock.json` mudou,
    constrói o app quando o código dele mudou (uma marca do conteúdo), e o abre solto do terminal. O app empacotado continua no
    passo 18.
  - **O atalho no Menu Iniciar** é gravado pelo
    `atlas desktop` a cada vez; no
    Linux, uma entrada `.desktop`. Abre o app construído direto.
  - **`atlas update`** traz o `main` (`git pull --ff-only`), sincroniza o
    Python e reconstrói o app num processo novo (`atlas desktop --build-only`), com o código que
    acabou de chegar; se um desses passos falha, volta o código ao commit de antes
    (`git reset --hard`).
    - Recusa com o app aberto (uma casa sendo servida): só existe o servidor do app.
    - Recusa uma cópia com mudanças locais: a cópia de uso não é
      editada.
    - O banco já está protegido pelo backup antes de migrar (passo 10b); uma fotografia antes de
      atualizar fica de fora.
  - O código chega ao uso quando um passo fecha, ou quando um conserto achado no uso passa nos
    fiscais.
- **Modelo do dia a dia:** fica o primeiro provedor com chave (OpenAI, gpt-6-luna, US$ 0,10 por milhão
  de tokens de entrada; um dia de uns 30 turnos sai por uns US$ 0,02), com o esforço padrão do
  provedor: o Atlas não manda esforço quando ninguém escolheu.
- **O seletor de modelo e de esforço no app**, na caixa de texto:
  - lista só os provedores com chave no cofre, agrupados, cada um com a
    sua lista curta de modelos no `providers.toml`, mais os que o `models.toml` do usuário declara;
    cruzar a lista da OpenAI com o que a chave alcança pediria uma chamada à rede a cada abertura, e
    fica de fora; o preço fica escondido;
  - o esforço mostra os níveis que o catálogo diz que o modelo aceita, e some num modelo que não
    raciocina. O catálogo do Atlas já recusa nível que o
    modelo não aceita;
  - **a troca vale só para aquela conversa** e fica guardada nela;
    conversa nova usa o padrão do `config.toml`. Fica de fora a escolha que gruda na tela, que
    faria conversas novas rodarem em silêncio num modelo caro esquecido;
  - trocar no meio da conversa deixa uma marca no histórico, como mensagem do usuário, só a mais nova;
    com o turno rodando, vale a partir do turno seguinte.
- **O custo:** `atlas usage [--days N]` lê o livro de chamadas e mostra
  o gasto por dia, por modelo e por finalidade, nos últimos 30 dias por padrão. O preço do plano
  gratuito do Gemini aparece como preço de tabela, e o relatório diz isso.
- **A pasta de trabalho continua a pasta do usuário**, e o agente alcança o computador todo, menos o
  cofre e as pastas do sistema ("defesa em profundidade, não fronteira de
  segurança"). Uma pasta por conversa serviria a projeto de código, que no Atlas vai
  para os executores (passo 13).
- **A tela em português:** o `pt-BR` entra agora, com só o que muda sobre o `en`, e o idioma vem do
  sistema.
- **Ferramenta adiada chamada sem ser carregada é recusada**, mandando carregá-la pela
  `tool_search`.
  Até aqui o Atlas a executava, e o Gemini errou o argumento assim (passo 5).
- **O `npm audit`:** o `source-map-js` 1.2.2 e o `streamdown` 2.7.0 (que leva um katex consertado)
  saíram em 30/09 e passam dos 14 dias em 14/10; sobem nesse dia.
- **Fica de fora:** o backup agendado (o agendador do sistema serve).
  O backup diário vem com o cron (passo 12).

#### Achados do uso

O uso começa quando a cópia de uso abre a casa real pela primeira vez. Cada defeito entra aqui com a
data, o que aconteceu (sem nada pessoal), o teste que o prende e o commit do conserto.

- **11/10, ao montar a cópia de uso:** o `atlas desktop` construiu o app e não achou o Electron. O
  Electron 43 não baixa mais o programa na instalação do pacote; baixa no primeiro
  `require('electron')`, que o e2e e o `npm run dev` fazem e o `atlas desktop` não. Agora ele roda o
  instalador do próprio Electron quando o programa falta. Teste:
  `test_the_app_is_built_once_and_again_only_when_its_code_changes`.
- **11/10, na primeira atualização da cópia de uso:** o `atlas update` trouxe o conserto acima e
  construiu o app sem ele, porque o processo da atualização ainda rodava o código de antes do
  `git pull`; e o atalho, que é melhor esforço, deixou escapar o erro do Electron ausente. Agora a
  construção roda num processo novo (`atlas desktop --build-only`), com o código que acabou de
  chegar, e o atalho só avisa. Testes: `test_an_update_brings_main_syncs_python_and_rebuilds_the_app`
  e `test_a_failed_step_after_the_pull_goes_back_to_the_commit_before`.
- **12/10, ao rodar a prova das rotinas:** os scripts de prova migravam a casa real só com a tabela
  do livro de chamadas, e desde que a cópia de uso migrou a casa inteira a conferência recusava a casa
  ("um Atlas mais novo escreveu este banco"). Migrar com o código de uma branch seria pior: deixaria
  na casa tabelas que a cópia de uso não conhece, e ela recusaria a casa ao abrir. Agora as provas
  não migram a casa real; só criam o livro quando a casa ainda não tem nenhum
  (`scripts/proof_ledger.py`). Nada foi gasto nem mudado na casa.
- **12/10, na atualização para o passo 12:** o `uv run atlas update` puxou o código e o `uv sync`
  falhou, porque precisava substituir o `atlas.exe`, que a própria atualização usava, e o Windows
  trava um programa em uso. A volta atrás funcionou e a cópia ficou inteira. Agora a atualização só
  instala as dependências (`uv sync --no-install-project`): o Atlas está instalado em modo editável,
  então o código novo vale sem reinstalar, e o próximo `uv run atlas serve` do app reinstala tudo; um
  lançador fora do ambiente seria mais pesado. Testes:
  `test_an_update_brings_main_syncs_python_and_rebuilds_the_app` e o da volta atrás.
- **12/10, na atualização seguinte:** o conserto acima desinstalou o próprio Atlas da cópia de uso
  (um `uv sync` exato sem o projeto tira o projeto), e a construção do app, que chamava `atlas`
  pelo PATH, achou o `atlas.exe` do V3, que falhou na hora sem rodar nada. Nada se perdeu: o código
  não tinha mudado e a casa não foi tocada, e o app voltaria a instalar o Atlas ao subir o servidor.
  Agora a sincronização mantém o que está instalado (`--inexact`), e a construção roda pelo Python
  do próprio ambiente (`python -m atlas desktop --build-only`). Os testes antigos trocavam o `uv`
  por um gravador de comandos, então provavam o texto do comando e não o efeito; o novo roda o `uv`
  de verdade num projeto mínimo instalado em modo editável, e falha com o comando antigo. Teste:
  `test_the_updates_sync_keeps_the_project_installed_and_its_build_runs_the_environments_python`.
- **12/10, conferindo o banco da casa real:** 2 das 5 conversas estavam vazias, sem mensagem e sem
  título, e por isso invisíveis na barra lateral (o usuário pediu para consertar). O "Nova conversa",
  o seletor de modelo e o ⚡ criavam a conversa no servidor antes da primeira mensagem. Agora
  a conversa não existe até o primeiro envio: a conversa nova é um rascunho no
  app: a escolha de modelo e o ⚡ ficam guardados nele, e a primeira mensagem cria a conversa e os
  aplica. As duas vazias que já existem ficam no banco, invisíveis e sem conteúdo. Teste:
  `a new conversation is made by its first message, not by a pick or the ⚡ before it` (e2e).
- **15/10, na atualização de um testador:**
  - O que aconteceu: o `config.toml` dele tinha a seção `[executors]`, posta à mão para apontar o
    `claude`. O passo 16c levou essa configuração para o plugin do Claude Code, e a versão nova
    recusava a seção em todo comando, inclusive na construção do app dentro do `atlas update`. A
    construção falhou e a atualização voltou para a versão de antes. O comando que a mensagem
    mandava usar (`atlas plugins set`) só existe na versão nova, então o testador ficou preso.
  - O conserto migra a configuração depois de atualizar: a seção que mudou
    de lugar é lida e ignorada, sem recusar. O servidor, ao subir e depois de migrar o banco, leva
    o valor para a configuração `program` do plugin e tira a seção do arquivo. Antes, guarda o
    arquivo como era em `backups/config-<hora>.toml`.
  - A migração fica no servidor, e não no update, para o update nunca mexer no banco. Se o update
    migrasse o banco e depois voltasse para a versão de antes, essa versão recusaria a casa.
  - Testes: `test_a_section_an_older_atlas_kept_is_read_past_and_the_server_moves_it`, que falha
    sem o conserto com a mensagem que o testador viu, e os de `drop_section`.

- **15/10, no ensaio da prova do 10b (`scripts/prove_homes.py --rehearse`), que passava em 11/10:**
  - **Defeito do app:** um turno numa casa restaurada, que não tem a chave do modelo escolhido,
    mostrava só "The turn failed: ModelKeyError". O servidor mandava o motivo e o que fazer ("grave
    com `atlas secrets set OPENAI_API_KEY`"), mas desde o 9c a tela só mostrava o texto de um turno
    respondido. Agora o fim de um turno que falhou mostra as palavras do servidor, como o app fazia
    antes do 9c. Testes: `a turn on a model whose key is
    not stored says how to store it` (e2e, que falha sem o conserto com o texto visto), e os do
    Vitest para o estado e para a tela.
  - **Defeito da prova, não do Atlas:** desde 14/10 o título usa o GPT-6 Luna quando a OpenAI tem
    chave, e no ensaio a OpenAI é o modelo falso; o título, que começa junto com o turno, pegava a
    resposta roteirizada do turno, e o turno ficava sem nenhuma. Agora as respostas da prova só
    servem a pedidos de turno (`when`, do modelo falso).

#### Conferência da lista mínima (15/10)

Feita pelo agente com a licença do usuário, lendo só os metadados do banco da casa real (nome da
ferramenta, tipo e data das mensagens, o livro de chamadas), e as respostas de quatro conversas para
o item da memória. Nada do conteúdo entra aqui.

- **Dias de uso:** o usuário pediu para contar da primeira mensagem, em 11/10. Houve conversa em
  todos os dias de 11 a 15/10.
- **Feitos no uso:**
  - arquivo achado pela `find_files` e lido na mesma conversa, em 12/10, e em mais 7 conversas;
  - busca na web, em 12/10;
  - skill corrigida pela própria conversa (`skill_manage`), em 14/10; a revisão em segundo plano
    cria e corrige skills desde 12/10;
  - cartão de pergunta (`ask_user`), em 12/10, 6 no total; o guardião, em 12 e 14/10;
  - troca de modelo no meio da conversa, em 14 e 15/10.
- **Dispensados pelo usuário em 15/10**, sem registro no uso:
  - memória usada numa conversa nova: a memória foi gravada em 13 e 14/10, mas nas respostas das
    conversas abertas depois só aparece o nome do usuário, que um canal também conhece;
  - conversa que compacta: nenhuma compactou. A compactação começa em 85% da janela, e no gpt-6-luna
    isso dá uns 780 mil tokens; a conversa mais longa chegou a 289 mil. A compactação com modelo real
    foi provada pelo `scripts/prove_agent.py`;
  - backup restaurado numa casa de teste: o restaurar foi provado pelo 10b e pelo 10c, e a prova
    real do 10b, que fecha este passo, restaura um backup numa casa nova.
- **Nenhum defeito novo.** Pareciam e não eram: o título caindo do modelo de título sem crédito
  para o modelo da conversa (a reserva funcionou); 3 conversas vazias, todas criadas antes de o
  conserto do rascunho chegar à cópia de uso; e 503 dos provedores. As chamadas do livro sem
  conversa na casa são das provas, que copiam as suas chamadas para o livro real.

### Rotinas e guardas (decididas em 11/10)

Do V3 (M-106) vem a confirmação da regra do atraso.

- **Passo próprio, com os subagentes no 12b** (decidido em 11/10), como o 6b e o 9b: a rotina
  chega antes ao uso, e cada um fecha com a sua prova.
- **Onde ficam:** uma tabela no `atlas.db`, com o histórico das execuções. O critério da arquitetura (Persistence) põe no banco o que
  o programa grava e lista.
- **A agenda:** `every 30m`, `in 2h` (uma vez), `every monday 9am` e
  `weekdays at 9am`, cron de 5 campos e data ISO. Corrigido em 12/10: a decisão de 11/10 deixava a
  linguagem natural de fora por pedir o `croniter`, mas o cron de 5 campos também pede
  (6.0.0, com `python-dateutil` e `pytz`, no núcleo); com ele a linguagem natural
  sai de graça. O fuso é o do sistema, ou `[time] zone` no `config.toml`,
  com o `tzdata` no Windows, que não traz a base de fusos.
- **Quem dispara é o servidor**, a cada 60 s; um servidor por casa já
  impede o disparo duplo.
  - **Rotina perdida com o computador desligado:** dentro de uma folga de metade do intervalo
    (entre 2 min e 2 h) roda atrasada; passada a folga, várias perdidas viram uma execução só
    (o M-106 do V3 chegou à mesma regra). A lista mostra "atrasada".
  - Uma rotina que ainda roda não começa de novo.
  - Sem nova tentativa genérica.
- **Cada execução:**
  - é uma conversa nova, de origem `routine`, que a `conversation_search` põe depois das outras, com o perfil e a memória carregados;
  - não tem `ask_user`, e a aprovação que dependeria de alguém é negada (`cron_mode: deny`);
  - não roda a revisão em segundo plano (decidido em 09/10) e conta como em uso as skills que
    carrega (decidido em 09/10);
  - não cria nem muda rotina (a guarda de recursão);
  - para depois de 10 min sem atividade (o vigia de inatividade),
    sem teto de tempo total;
  - o modelo é o do `config.toml`, ou o que a rotina fixou ao ser criada pelo usuário.
- **O resultado vira conversa e aviso** (decidido em 11/10): a execução aparece na barra lateral,
  como uma conversa, e o sistema avisa "Rotina x terminou".
  Uma resposta `[SILENT]` não avisa e fica guardada; uma falha sempre avisa. A caixa de
  entrada do V3 fica de fora; os canais vêm no passo 16.
- **A ferramenta do agente** é a `cronjob_manage` (criar, listar, mudar, pausar, retomar,
  apagar, rodar agora), atrás da `tool_search`, com a varredura de ameaças no texto da
  rotina e sem pedir confirmação ao usuário.
- **Rotina sem modelo** (`no_agent`): roda um comando e entrega a saída; saída vazia não
  avisa. O backup diário entra assim, criado na casa do usuário com `atlas backup`.
  - **O agente também cria** (pedido do usuário em 25/10): até então só o usuário criava, pelo
    terminal, porque o comando roda depois sem ninguém para aprovar e sem o modelo para recusar.
    Agora a `cronjob_manage` aceita `command`, e o comando passa pelo portão como um
    `run_command` passaria, ao ser criado e ao ser mudado: o piso bloqueia, a ação para fora e
    o comando marcado vão para o usuário (ou o guardião), e o modo automático deixa passar o
    resto. O bot continua só com rotina de prompt.
- **No terminal**, `atlas routines list|create|pause|resume|run|remove|runs`.
- **No app, uma tela enxuta** (decidido em 11/10): a lista,
  rodar agora, pausar, apagar, e as execuções de cada rotina, que abrem como conversa. Criar continua
  pela conversa ou pelo terminal.
- **O comando em segundo plano avisa quando termina** (`notify`): o resultado entra na
  conversa entre os turnos. Hoje o modelo só sabe se for ler o arquivo.
- **As guardas de laço:** o ciclo (A, B, A, B, de período 2 a 4), o resultado igual de 512
  caracteres ou mais trocado por uma referência ao primeiro, o mesmo erro de novo (avisa no 2º) e a
  mesma ferramenta falhando (avisa no 3º); uma chamada que muda algo com sucesso zera as contagens
  de falha.
  - **A postura** (decidido em 11/10): com alguém olhando, só avisa o modelo; na
    rotina, que ninguém para, interrompe. A parada na
    5ª chamada igual, decidida no passo 4, vira aviso na conversa.
- **Limite conhecido** (achado pela prova real em 12/10): sem ninguém para
  aprovar, o comando marcado é recusado, mas a recusa só barra o que os padrões conhecem. Numa rotina
  que pedia para apagar uma pasta, o Gemini 3.7 levou três recusas e apagou a pasta por outro
  caminho, em uns 37 pedidos; o texto da recusa ("ache outro caminho que evite este comando") até
  convida a isso. Decidido em 12/10 não mudar nada. Uma rotina só deve receber tarefas que o usuário aceitaria ver feitas sem aprovação.
- **Ficam de fora**, até fazerem falta: o monitor de URL, o script que decide se acorda o agente, o
  bloco de notas por rotina, a nova tentativa quando o modelo não responde, e as tarefas duráveis (o
  registro de tarefas do V3).

### Subagentes (decididos em 11/10, passo 12b)

Contexto zerado, em segundo plano por padrão, só o resumo volta.

- Uma lista de tarefas, cada uma com objetivo e contexto; o modelo não escolhe ferramentas nem
  modelo: o filho herda os do pai, menos os bloqueados.
- **Sempre em segundo plano**, e o resultado entra entre os turnos, um aviso só para o lote (um
  aviso por tarefa acordaria o pai a cada uma, para responder "já incorporado"). O pai lista, corrige o rumo e para cada um; parar devolve o que o filho tinha.
- **Volta o resumo**, até 24 mil caracteres ou metade do contexto que sobra, o resto num arquivo.
- **Limites:** um nível só (o filho não delega); o filho não pergunta, não grava memória e
  não cria rotina; as aprovações dele são negadas; 250 iterações; o filho travado é parado (450 s
  parado, 1.200 s numa ferramenta só); até 10 ao mesmo tempo.
- A conversa do filho é guardada e fica fora da busca; o custo soma no do pai; um filho cujo
  processo morreu fica "desconhecido", nunca "falhou".
- As guardas de laço, no subagente, só avisam: o pai vê.
- O fusível de retomadas do V3 ("10 retomadas sem fala humana") fica de fora: o aviso
  único por lote e as guardas fazem o papel dele.

**Decidido em 12/10, ao começar o passo:**
- **A ferramenta continua `delegate`**, com os argumentos (`tasks` com `goal` e
  `context`, `action` list/steer/stop, `subagent_id`, `message`). O `executor` do passo 13 entra
  depois, como campo opcional; a interface síncrona do passo 5 saiu, porque o filho roda em segundo
  plano.
- **O texto da descrição** fala só das peças que o Atlas tem, com os nomes das ferramentas do
  Atlas. O usuário recusou um corte maior, que não dava para garantir. Medido em 12/10: as sempre ligadas custam 3.067 tokens no
  gpt-6-luna (o `delegate`, 833 pela estimativa local).
- **Rotina não espera aviso:** numa execução de rotina ninguém recebe o aviso depois, então a
  chamada espera os filhos e devolve o resultado nela mesma. O mesmo acontece
  com 10 lotes já em segundo plano no processo.
- **O filho** é uma conversa de origem `subagent` com o id do pai, no papel `subagent`: sem
  `memory`, `delegate`, `ask_user` e `cronjob_manage`, sem fotografia da memória,
  aprovações negadas, sem revisão, guardas que só avisam, até 250 chamadas.
- **O custo** fica no livro de chamadas como `subagent`, e o `atlas usage` mostra quanto a
  delegação custou. O Atlas não mostra custo por
  conversa, então a soma vira essa linha.
- **Um aviso antecipado de falha** (um filho falhou com irmãos rodando) fica de fora: seria
  um segundo aviso, e a decisão de 11/10 é um aviso por lote.
- **A tela** (decidido em 12/10): uma doca "Subagentes" enxuta, acima da caixa
  de texto, só enquanto há filhos rodando: o objetivo, a última ferramenta e o tempo, com Corrigir e
  Parar. A transcrição ao vivo fica de fora.
- **O critério de pronto:** os fiscais nos dois sistemas e a prova real
  (`scripts/prove_subagents.py`, ensaiada antes no modelo falso) na OpenAI e no Gemini, em seis
  passos: um lote em paralelo que volta num aviso só; corrigir o rumo de um filho; parar um filho;
  um comando marcado negado no filho sem perguntar a ninguém; o custo dos filhos no livro; e o
  processo morto no meio de um filho, que volta "desconhecido" no servidor seguinte.

### Executores externos (decididos em 12/10, passo 13)

O objetivo, nas palavras do usuário: nada das aprovações e perguntas que ele daria direto ao Claude
Code some; elas passam a chegar a ele pelo Atlas. Pesquisado em 12/10: o `codex app-server`
repassa as aprovações e não as perguntas.

- **Começa só pelo Claude Code**, o que o usuário usa, pelo Agent SDK em Python (o
  `claude-agent-sdk`, que roda o `claude` instalado): o mesmo caminho que a ponte ACP do Zed usa
  por dentro. O Codex (`codex app-server`) e os outros (ACP) entram quando
  fizerem falta. O SDK é um extra, como o `mcp`.
- **A ferramenta é o mesmo `delegate`**, com o campo `executor` opcional: sem ele, quem trabalha é
  o subagente do passo 12b.
- **Em segundo plano** (esperando, duas buscas que podiam rodar juntas rodariam uma depois da
  outra): a conversa segue, e o resultado volta num aviso só, como o dos subagentes.
- **Uma presença própria na tela**, separada da doca dos subagentes e da conversa: um selo "Claude
  Code" no lado direito enquanto ele roda; clicado, mostra o que ele está fazendo. O logo da Anthropic
  fica de fora: as regras de marca dela pedem aprovação prévia.
- **Cartões próprios, que saem do selo**, com outro visual ("Claude Code pergunta", "Claude Code
  quer rodar…"). Contra o defeito mais relatado em orquestradores, a aprovação perdida (negada
  em silêncio, falhando sem avisar, desligada pela ponte, ou a tarefa morta sem resposta):
  - nenhum cartão vence: o Claude Code espera o tempo que for;
  - uma recusa não mata a tarefa;
  - fora da tela, chega o aviso do sistema.
- **"Eu respondo" ou "o Atlas decide"**, por delegação. **Opção nova** (pedido do usuário: o pai
  responde pelo filho): a aprovação vai ao guardião do Atlas, e o que ele não tiver certeza vira
  cartão; a pergunta é respondida pelo Atlas da conversa, que conhece o contexto.
- **Parar** pelo selo, explícito e definitivo. A conversa do Claude Code fica guardada e abre pelo
  selo.
- **O login é o do usuário**, feito no próprio Claude Code: o Atlas nunca toca nele (a regra da
  Anthropic proíbe oferecer "entrar com Claude" ou guardar o login de alguém; rodar o Claude Code
  sem modificação, com o login do usuário, pode).

**Decidido em 12/10, no código:**
- **O canal, não o pacote** (decidido com o usuário): o Atlas fala com o `claude` instalado pelo
  mesmo canal que o Agent SDK usa por dentro (mensagens JSON pela entrada e saída padrão). O pacote do
  SDK traz um Claude Code próprio de 244 MB em toda versão e sai quase toda semana. Testado contra o
  `claude` 2.1.140 do usuário antes de escrever o código.
- **O Claude Code roda como o usuário o roda:** o `claude` do PATH (ou `[executors] claude_code`
  no `config.toml`), com as configurações, as regras de permissão e o `CLAUDE.md` do usuário, no
  modo padrão. Uma chave de API no ambiente do Atlas é tirada do dele, para nunca cobrar numa chave
  em vez da assinatura.
- **O pedido sem prazo** é um pedido do servidor próprio (`executor.approval`,
  `executor.question`): sem app ligado, ele espera, e chega ao app que abrir a conversa.
- **O custo nas sempre ligadas:** com o Claude Code instalado, o `delegate` diz que ele existe, 138
  tokens a mais pela estimativa (uns 95 no gpt-6-luna); sem ele, nada muda.
- **O ensaio achou** que um texto com acento numa pergunta chegava embaralhado a quem lê na
  codificação do Windows; agora o JSON sai escapado, como o SDK escreve.

**Trocar de conta quando o limite acaba** (decidido em 12/10, complemento do passo 13; provado em 13/10
com as duas contas do usuário, ao lado da prova do passo 14): o Claude
Code para, e o aviso diz qual conta chegou ao limite e quando volta. Só quando o usuário pede, o
Atlas roda o login do próprio Claude Code (`claude auth login --email`), que abre o navegador na
página da Anthropic, e depois continua a mesma sessão (`--resume`) na mesma pasta: o Claude Code
guarda o histórico por pasta, não por conta, então nada é copiado (o usuário recusou copiar
configurações entre contas e separar contas por pasta). Uma instalação guarda um login por vez,
então cada troca pede o login de novo; nunca troca com o Claude Code trabalhando. O selo mostra a
conta. Com o Claude Code instalado, o `delegate` custa uns 137 tokens a mais no gpt-6-luna.

### Bancada de harnesses (decidida em 14/10, passo 16c)

Redesenho do passo 13, pedido pelo usuário em 14/10. No passo 13 o Atlas é o intermediário
obrigatório: o harness só é chamado por pedido ao Atlas, o prompt é sempre o do Atlas, e a única
saída para as perguntas é o Atlas responder. Isso vai contra a tese (o Atlas passa adiante o que
outro faz melhor) e contra o uso do usuário, que sabe escrever o próprio prompt. O que o passo 13
fez continua valendo: o `delegate` com `executor`, os pedidos sem prazo, o selo, a troca de conta.

- **Uma bancada global,** o painel da direita, sempre presente e colapsado quando ninguém trabalha
  (do selo do passo 13). É a mesma em todas as conversas.
- **Um cartão por instância** (harness + conta + pasta), não por harness: o mesmo harness pode
  rodar duas vezes em pastas diferentes. O cartão mostra a conta, a pasta e o estado, tem o campo de
  prompt (que vira steer com ele rodando), as perguntas e permissões, o que ele fez (expandido, como
  no passo 13) e o menu ⋮: duplicar, trocar de pasta, trocar de conta, abrir a conversa, parar.
- **A resposta do harness fica no cartão,** nunca na conversa. O `@claude` na caixa da conversa foi
  descartado pelo usuário: não dá a configuração que o cartão dá.
- **Duas entradas para a mesma instância:** o usuário pelo cartão, ou o Atlas pelo `delegate`.
- **Duas chaves por instância, independentes:**
  - **o automático do próprio harness** (o `auto` do Claude Code, a política de aprovação do
    Codex), para ele não pedir permissão;
  - **"Atlas responde"**, desligado por padrão (opção nova, a do passo 13, que vira modo em vez de
    escolha por pergunta): o Atlas responde perguntas e permissões das quais tem certeza, e o resto
    vem para o usuário.
  - O automático não cobre perguntas: elas vão ao usuário, ou ao Atlas se a segunda chave estiver
    ligada. Com as duas desligadas, tudo vem ao usuário.
  - O automático passa por cima do guardião do Atlas; o cartão mostra isso.
- **Detecção na instalação e em toda abertura** do Atlas. O onboarding (o da chave de API) mostra
  todos os harnesses suportados: os detectados para ligar, com a confirmação do usuário ali mesmo,
  e os não detectados apagados. Na bancada, o não detectado não aparece.
- **O Atlas consulta as delegações sob demanda, nunca em tempo real** (o pai recebe um resumo curto
  e um ponteiro, e lê o detalhe quando precisa):
  - o Atlas guarda no SQLite um registro enxuto de cada instância (harness, conta, pasta, id da
    sessão, estado) e de cada turno dela: o prompt, as perguntas e respostas com quem respondeu, o
    texto final do turno e os arquivos alterados, tirados dos eventos de ferramenta;
  - a transcrição completa fica onde o harness a guarda, e o plugin dele a lê quando o Atlas pede;
  - o registro sobrevive à transcrição (o Claude Code apaga sessões depois de 30 dias), e responde
    a pergunta mais comum, "o que ele mudou?", mesmo depois dela;
  - nada é copiado para `.md`: seria duplicar.
- **As ferramentas do Atlas para isso:**
  - **o estado ao vivo é o `delegate` que já existe:** o `list` mostra também as instâncias da
    bancada, com estado, pasta, conta, pergunta esperando e de quem é a instância;
  - **o histórico é a `delegation_search`, adiada**, cópia da `conversation_search`, com as mesmas
    quatro formas: navegar (filtros por harness, pasta e data), descobrir (`query`), ler
    (`delegation_id`: o registro e o início e o fim da transcrição
    normalizada) e rolar (`delegation_id` e `around`). Corta as mensagens como ela, e a saída de
    ferramenta só vem quando pedida;
  - **a busca de texto olha só o registro** (decidido pelo usuário em 14/10): o texto final de cada
    turno costuma citar o assunto, e o que a busca não acha o Atlas acha navegando e lendo. A busca
    dentro das transcrições originais, cinco formatos, um por plugin, fica para quando fizer falta;
  - **transcrição expirada:** o `transcript()` diz que ela não existe mais, e a ferramenta devolve
    o registro com o aviso de que a original foi apagada pelo harness. O Atlas responde com o que
    tem.
- **O Atlas só lê as instâncias do usuário** (decidido pelo usuário em 14/10): steer e parar numa
  instância começada pelo cartão só quando o usuário pede, e o `delegate` recusa o resto, como o
  agente que não consente pelo usuário no passo 16.
- **O núcleo guarda o contrato; cada harness é um plugin** que vem com o Atlas, como o Telegram.
  A delegação inteira como plugin foi descartada: o contrato encosta no protocolo do servidor, no
  guardião, no `delegate` (que também chama os subagentes) e na tela.
  - **No núcleo:** o contrato (instância, eventos, pergunta, permissão, steer, modos), a bancada e um
    cartão genérico desenhado pelos dados do plugin (nenhum plugin traz tela própria), os pedidos
    sem prazo e o "Atlas responde", o registro e a ferramenta do Atlas para consultá-lo, o
    `executor` do `delegate`, e o cliente ACP como biblioteca (`atlas.plugins.acp`, fora do
    `sdk.py`, que é puro), sem ativação: só roda quando um plugin o usa. O cliente ACP chega com o
    plugin do Hermes, o primeiro que o usa, e não no 16c (decidido com o usuário em 14/10: o
    Hermes prova mais do cliente que o DeepSeek, com steer e permissões completas, e já está
    instalado; o DeepSeek vem depois e reaproveita o cliente).
  - **No plugin:** o canal, a detecção e a versão mínima, a conta e a troca de conta, o automático
    próprio e a leitura da transcrição.
- **SDK v2:** `register_executor`, com o mesmo desenho do canal da versão 1 (o plugin só transporta;
  quem responde o quê é do Atlas):
  - `Executor`: `detect()` (instalado, versão, conta), `capabilities()` (perguntas, steer,
    automático), `start(spec, host)` e `transcript(session)`;
  - `ExecutorInstance` (não `Instance`, que no glossário é um Atlas rodando numa máquina;
    decidido com o usuário em 14/10): `prompt`, `steer`, `set_auto`, `stop`;
  - `ExecutorHost`, o Atlas visto pelo plugin: `event`, `ask` e `approve`, que esperam o tempo que
    for. O plugin nunca sabe quem respondeu.
  - A `transcript` devolve entradas normalizadas (texto, ferramenta, pergunta, resposta, resultado),
    iguais para todos os harnesses: o Atlas lê um formato só, gasta menos, e a mudança de formato
    de um harness fica no plugin dele.
  - O manifesto é o do Claude Code, com `executors` na seção `atlas` (entra na impressão digital
    do consentimento). Um plugin pode trazer executor e skill juntos.
- **O canal de cada um, o nativo, não um protocolo único:**
  - Claude Code continua no stream-json do passo 13 (o tradutor ACP `claude-agent-acp` saiu 24
    versões em dois meses e traria o Node);
  - Codex pelo `codex app-server`;
  - Pi pelo RPC próprio (`pi --mode rpc`);
  - Hermes e DeepSeek pelo cliente ACP do núcleo, que serve também aos que vierem (Gemini CLI,
    OpenCode).
  - Quando o harness não tem ferramenta de pergunta no canal, a pergunta chega como texto no fim
    do turno e o usuário responde pelo prompt do cartão.
- **A ordem:** o SDK v2 e o Claude Code saindo do núcleo como primeiro plugin, que é a prova; depois
  Codex, Pi, Hermes e DeepSeek, um de cada vez.

**Os cinco harnesses, estudados em 14/10 no código de cada um:**

| | Canal | Perguntas | Permissões | Steer | Automático próprio | Conta, lida sem efeito |
|---|---|---|---|---|---|---|
| Claude Code | stream-json | sim (`AskUserQuestion` como `can_use_tool`) | sim, "sempre" com escopo | sim | sim, `set_permission_mode` no meio | `claude auth status --json` |
| Codex | `app-server` | só no modo Plan, experimental (`item/tool/requestUserInput`) | sim | `turn/steer` | política de aprovação por turno | `account/read` |
| Hermes | ACP (`hermes acp`) | não pelo ACP (o `clarify` não está ligado); só no `tui_gateway`, interno | sim | sim | só `--yolo`, para o processo inteiro | lendo `config.yaml` |
| Pi | RPC | não tem ferramenta; só por extensão (`extension_ui_request`) | não pede nunca | steer e follow-up | sempre automático | `pi auth check` diz se está pronto, não qual conta |
| DeepSeek (`dsh`) | ACP (`dsh --profile acp`) | não (a ferramenta só existe na versão web) | só "uma vez" ou "recusar" | não pelo ACP | `DSH_PERMISSION_MODE` na partida | não há |

- **As sessões:**
  - Claude Code: JSONL por pasta, apagado em 30 dias;
  - Codex: JSONL (às vezes zstd) e SQLite;
  - Hermes: SQLite (`state.db`);
  - Pi: JSONL em árvore;
  - DeepSeek: JSONL com zstd.
  - Só o Claude Code apaga sozinho.
- **Riscos achados:**
  - **O `dsh` manda os logs da sessão à DeepSeek por padrão** (`session-log-deepseek`); o plugin
    mostra isso ou desliga.
  - **A Anthropic proíbe terceiros de oferecer o login do claude.ai.** Rodar o `claude` do usuário
    é zona cinzenta, e a troca de conta mais ainda; conferir antes de abrir o repositório.
  - **O `app-server` do Codex é declarado não suportado para produção,** e as perguntas dependem de
    partes experimentais.
  - **O Codex já usa "atlas" como origem de sessão:** não usar esse rótulo.
  - **Os CLIs mudam toda semana:** cada plugin exige uma versão mínima, e a detecção a mostra (o
    `claude` do usuário, instalado pelo WinGet, não se atualiza sozinho).
- **Para depois:** o daemon compartilhado do Codex expõe as sessões abertas no terminal do usuário,
  que a bancada poderia mostrar, mas sem isolamento entre clientes.

### Navegador (decidido em 12/10, passo 14)

Decidido com o usuário em 12/10.

- **Uma ferramenta de código, o `browser_exec`**, atrás da busca: o modelo escreve
  Python com funções prontas (abrir aba, ler a página, rodar JS, preencher, clicar, tirar print) e
  junta vários passos numa chamada, com menos tokens que uma ferramenta por ação. O motor é o
  **browser-harness** do Browser Use (MIT), rodando no Python do próprio Atlas. A telemetria dele, que
  manda o código para fora, e o aviso de atualização, que manda o agente se atualizar, ficam
  desligados.
- **Um perfil do Chrome só do Atlas**, em que o usuário entra uma vez em cada site, aberto pelo
  próprio Atlas. O Chrome 136 em diante não deixa controlar o perfil padrão, e copiar o perfil real
  falha no Windows com o Chrome aberto. O usuário escolheu isso em vez de uma extensão. Mais dois
  modos:
  **limpo** (perfil temporário, com janela) e **invisível** (perfil temporário, sem janela). Vale o
  Chrome, o Edge ou o Chromium; sem
  nenhum, a ferramenta não aparece. Cada conversa usa a própria aba.
- **O usuário assume na janela de verdade** (a "posse", sem tela virtual, porque Windows e Mac já
  têm tela): "Assumir" no Atlas
  traz a janela para a frente, e enquanto o usuário estiver com ela o agente não age nem lê a
  página, que pode estar mostrando a senha; o que estava rodando quando ele assumiu é descartado.
  Ao devolver, o agente é avisado de que a página pode ter mudado.
- **O agente para em login, CAPTCHA e pagamento** e pede para o usuário assumir. Gerenciador de
  senhas (1Password) fica para depois, por decisão do usuário.
- **Quem recebe e até onde vai**: o código é Python na máquina, então a ferramenta
  só existe onde o `run_command` existe, e não pede aprovação a cada chamada. Antes de rodar, os
  endereços escritos no código são conferidos: endereço de metadados de nuvem é sempre recusado, e
  endereço com um segredo que o Atlas conhece também. A saída sai sem segredos, marcada como
  conteúdo de fora, e vai para arquivo quando passa do limite; um print vai ao modelo como imagem
  quando ele enxerga imagem.
- **A tela**: a presença do navegador à direita da conversa: qual
  navegador, o rótulo do último passo, assumir e devolver. **Retirada em 20/10**, com a posse do
  navegador, por decisão do usuário: o painel ficava aberto depois de o navegador fechar, sem como
  fechar, e o botão de assumir não era preciso (a janela é de verdade, o usuário digita nela
  quando o agente pede).

### Bots (decididos em 13/10, passo 13b)

Decidido com o usuário em 13/10, partindo dos agentes de domínio do V3 (M-81). O agente de
domínio do passo vira **bot**.

- **O bot mora dentro da casa.** O bot como uma casa inteira foi recusado pelo usuário porque 3
  casas com 3 bots cada viram 12 casas e 12 servidores, e porque cada cópia do `USER.md` envelhece
  sozinha (conte ao bot de finanças que mudou de
  emprego, e o de produto continua achando o emprego antigo). A casa tem o Atlas principal e os
  bots.
  - **Um servidor por casa roda todos os bots dela**: as rotinas e as mensagens. A
    decisão do passo 10b não muda.
  - **O backup da casa leva os bots.**
- **O que é do bot:**
  - **a definição**, um arquivo no formato do agente do Claude Code (o mesmo `agents/` dos
    plugins): nome, título, descrição, modelo, ferramentas, servidores MCP e skills permitidas no
    cabeçalho, e o texto é a missão e as regras dele;
  - **o próprio `MEMORY.md`**, com o que ele aprendeu do trabalho dele. Só o bot grava nele (um
    escritor por memória). O Atlas principal lê o arquivo quando precisa, sem carregá-lo nas
    instruções, e o que ele quer
    que o bot saiba vai pelo `message_agent`;
  - **a própria pasta de skills**;
  - **a conversa fixa**, as rotinas e a cara na lista (título, descrição e cor).
- **O que é da casa:** o cofre, os servidores MCP, o banco, a biblioteca de skills, e o `USER.md`.
  O Atlas principal continua com o `USER.md` e o `MEMORY.md` da casa.
  - **O `USER.md` é um só, e todos os bots leem e gravam nele.** **Opção nova**, pedida pelo
    usuário: o usuário é um só para todos, e cada agente é uma identidade própria. O Atlas faz
    isso com os próprios arquivos. A trava já
    existe: o `USER.md` é relido e gravado dentro dela.
- **As skills:** a biblioteca da casa, a pasta do bot, e uma lista de permitidas por
  bot que filtra o que ele vê (sem lista, vê tudo).
  - **A revisão da conversa de um bot cria a skill na pasta dele**, não na biblioteca, para a
    biblioteca não encher de skills que só servem a um bot.
  - **Toda skill guarda quem a criou** (o Atlas principal, um bot pelo nome, ou o usuário), ao
    lado da origem que o `skills_usage` já guarda, e a lista de skills mostra isso, para o
    usuário ver quem está enchendo a biblioteca. Pedido do usuário.
- **A conversa fixa:** uma por bot, criada com ele. Clicar no bot abre
  sempre ela, e `/new` nela compacta em vez de abrir outra. Ela fica na lista de bots, fora da
  lista de conversas.
- **As rotinas do bot:** a rotina é das rotinas da casa, marcada com o bot, roda como o
  bot, e o resultado entra na conversa fixa dele como mensagem, que ele lê e responde.
- **Mensagem entre agentes, o `message_agent`:** vai e não espera, e a resposta volta
  depois num aviso, como a dos subagentes. A lista dos bots (nome, título e descrição) entra nas
  instruções, só quando a casa tem bot.
  - **O Atlas principal manda de qualquer conversa**, não só da conversa fixa.
    Pedido do usuário, pelo uso do V3: 61 consultas do Atlas aos agentes, contra 2 vezes em que o
    usuário abriu a conversa de um agente.
  - Um bot manda ao Atlas principal e aos outros bots. Ele escreve a mensagem dele, nunca repassa
    as palavras do usuário, e não responde só para agradecer.
  - **Mensagem de outro agente nunca vale como fala do usuário**: uma aprovação que chega por
    outro agente não aprova nada.
- **Os grupos ficam de fora** (o passo 13c saiu da linha em 18/10, pelo usuário; decidido em
  13/10, depois do estudo: Grupos de bots, abaixo). São a peça mais cara dos bots, nada do resto
  depende deles, e o uso do V3 foi o Atlas consultando um agente por vez, o que o `message_agent`
  cobre: "pergunte ao bot de finanças e ao de
  produto" são duas mensagens, e o Atlas principal junta as respostas.
- **Quem cria:** o usuário, pela tela (nome, título e descrição, mais o avançado)
  e pelo terminal; e o Atlas, quando o usuário pede, perguntando antes e mantendo a lista pequena.
- **A tela:** uma aba Bots ao lado das conversas, com a lista (cara, última mensagem e
  hora) e as rotinas do bot ao lado da conversa fixa.
- **Fica de fora**, até fazer falta: as seções da lista, esconder bot, cara gerada por IA, voz por
  bot, bots de outras máquinas (passo 15), modelos de bot para compartilhar, bot de equipe, e a
  tela própria de cada bot.

**O que é novo, e o que pode dar errado** (estudado em 13/10, a pedido do usuário: o que ainda não
foi provado fica à vista, com o defeito possível e o que o segura). Do que está acima, é novo: o
bot dentro da casa, o `USER.md` único, o
`message_agent` em qualquer conversa do Atlas principal, e a skill na pasta do bot com a marca de
quem criou.

- **O `USER.md` único.** Dois agentes gravando a mesma memória arriscam que cada um carregue o
  que o outro escreveu, e o estado deixe de ser o que o usuário configurou.
  - **O fato novo não chega ao outro bot.** As instruções de uma conversa são montadas no começo e
    depois de compactar, e a conversa fixa de um bot não recomeça nunca: o bot de produto só
    saberia do emprego novo dias depois. *Defesa:* as instruções são remontadas uma vez quando o
    que elas mostram muda, e o `USER.md` entra nessa conta. Custa uma quebra de cache por bot a cada
    mudança do perfil, que muda pouco.
  - **O teto enche mais rápido.** O `USER.md` tem 1.375 caracteres, e passa a ter vários
    escritores. *Defesa:* a regra do que vai para onde entra nas instruções do bot (o que é
    sobre o usuário vai para o `USER.md`; o que é do trabalho do bot vai para o `MEMORY.md`
    dele), e trocar ou tirar entrada continua virando proposta (passo 9b).
  - **Entradas quase repetidas**, cada revisão acrescentando a sua versão do mesmo fato. *Defesa:*
    a revisão já lê o arquivo antes de gravar; a prova conta as repetições.
  - **O que um bot sabe aparece em todos.** Um fato de finanças no perfil entra nas instruções do
    bot que fala com um cliente. *Limite aceito:* o perfil é "quem é o usuário e como trabalhar
    com ele", e vale para todos; o que é reservado de um assunto fica no `MEMORY.md` do bot.
- **O bot dentro da casa.** Banco, cofre e skills não são separados por bot: é tudo da casa.
  - **A busca nas conversas atravessa bots:** o bot de um cliente acharia a conversa de finanças.
    *Defesa:* cada bot busca só nas próprias
    conversas, e o que é de outro se pede pelo `message_agent`. O Atlas principal busca em tudo,
    inclusive nas conversas dos bots (decidido pelo usuário em 13/10).
  - **O cofre e os servidores MCP são de todos.** *Defesa:* as listas de ferramentas e de
    servidores MCP na definição do bot (os campos `tools` e `mcpServers`).
  - **O "sempre permitir" de uma aprovação vale para a casa inteira**, e um bot herda o que foi
    liberado para outro. *Limite aceito* por enquanto; fica anotado para o uso dizer.
  - **O custo some no total.** *Defesa:* o livro de chamadas marca o bot, como já marca o
    subagente.
- **O `message_agent` em qualquer conversa**, e não só na conversa fixa.
  - **A lista de bots nas instruções fica velha** numa conversa antiga, depois de criar ou
    renomear um bot. *Defesa:* a ferramenta confere o destino na lista viva e, no
    erro, devolve a lista.
  - **Várias conversas escrevem ao mesmo bot.** *Defesa:* as mensagens para o mesmo
    bot entram em ordem, um turno por vez.
  - **A conversa fixa do bot enche de consultas** (no V3, 61 consultas contra 2 falas do
    usuário), e o usuário acha ruído quando abre. *Limite aceito:* a tela
    mostra de quem veio cada mensagem.
  - **Vai e volta sem fim** entre o Atlas e um bot. A regra no texto ("nunca fique trocando
    agradecimentos") e a resposta em silêncio não bastam. *Defesa:* um teto de mensagens entre agentes
    por fala do usuário, que ao estourar avisa o usuário em vez de calar.
  - **A resposta chega numa conversa que já mudou de assunto.** É o aviso dos subagentes, já
    provado no passo 12b.
- **A skill na pasta do bot.**
  - **Uma skill útil presa num bot, e a mesma skill aprendida por três bots.** *Defesa:* a marca
    de quem criou mostra as duas coisas, e o usuário passa a skill para a biblioteca pelo
    terminal.
  - **Dois nomes iguais.** *Defesa:* a pasta do bot ganha da biblioteca.
- **O critério de pronto:** os fiscais nos dois sistemas e a prova real (`scripts/prove_bots.py`,
  ensaiada antes no modelo falso), com um passo para cada defeito acima que dá para provocar:
  1. criar um bot pela tela e conversar na conversa fixa dele;
  2. contar um fato a um bot, e o outro, numa conversa fixa já aberta, saber dele na fala
     seguinte;
  3. a revisão da conversa de um bot cria a skill na pasta dele, marcada com o nome dele;
  4. um bot não acha, na busca, a conversa de outro;
  5. a rotina do bot cai na conversa fixa dele;
  6. de uma conversa qualquer, o Atlas principal pergunta a um bot e repassa a resposta dizendo
     de quem veio.

### Grupos de bots (estudados em 13/10; o passo 13c saiu da linha em 18/10, pelo usuário)

Saíram do passo 13b por decisão do usuário em 13/10 e entram quando fizerem falta. O estudo fica
aqui para não ser refeito: o desenho, e os defeitos que ele precisa evitar.

- **Os grupos:** salas de 2 a 6 membros, com o Atlas principal podendo entrar. A sala é hospedada
  no servidor, que é a que roda sem o app.
  - **A sala é um registro de eventos, e quem fala a seguir é calculado relendo o registro
    inteiro**, sem estado guardado à parte: depois de uma queda do servidor, a mesma conta dá o
    mesmo turno, e ninguém fala duas vezes. A resposta de um membro é gravada antes do fim do
    turno dele, e uma resposta sem esse fim não conta.
  - **Um membro por vez, nunca ao mesmo tempo.** Com os membros de uma rodada juntos, quem fala
    depois deixaria de ver quem falou antes, e os bots responderiam ao usuário cada um por si em vez de
    construir um sobre o outro.
  - **Até 3 rodadas por mensagem do usuário, e 10 respostas no máximo.** Na primeira rodada falam
    os mencionados, ou todos se ninguém foi mencionado (`@all` e `@everyone` valem). Nas
    seguintes só fala quem um bot mencionou e ainda não respondeu. Quem não tem o que somar
    responde "(pass)", e a sala assenta numa rodada sem resposta.
  - **Cada membro tem a própria conversa da sala** e recebe só o que ainda não viu (uma marca por
    membro), até 24 mensagens, com o corte avisado.
  - **Uma mensagem nova do usuário passa na frente:** a resposta atrasada de um turno anterior é
    cancelada em vez de publicada.
  - **O servidor da casa conduz a sala**, então fechar o app não para a discussão.
  - **As aprovações e perguntas de um membro chegam na sala** e se respondem no clique.
  - **As proteções que entram junto, cada uma contra um defeito:**
    - um membro nunca recebe a própria resposta como novidade (o bot responderia a si
      mesmo, turno após turno, até o teto);
    - um turno que falhou vira aviso de falha, nunca a fala do bot (um erro 401 do provedor seria
      publicado como resposta, e o membro chamado de novo até o teto);
    - o membro que o usuário chamou pelo nome e respondeu "(pass)" é cobrado uma vez (num `@everyone`, o
      segundo bot passaria porque o primeiro já tinha respondido); um
      segundo "(pass)" aparece como recusa, não como silêncio;
    - a fala de um membro que imita um aviso do sistema é remarcada antes de ir aos outros: a resposta
      de um bot entra no pedido dos outros, e sem isso os outros a leriam como ordem do sistema;
    - o pedido tem teto em bytes, não só em mensagens;
    - um turno que falhou de jeito duvidoso não é reenviado sozinho;
    - o membro que está trabalhando segura a vez por até 3 horas, e o que parou de dar sinal a
      perde em 3 minutos (com um teto curto, os turnos seriam cortados no meio do trabalho, e o
      seguinte falaria sobre um trabalho que ainda não existia).
  - **Fica de fora:** os fios dentro da sala ("responder no fio"); as palavras de parar na
    mensagem (`stop @bot`; um `halt` em alemão calaria o bot), ficando só o botão Parar; e as salas
    entre máquinas.
- **Falta ler antes de decidir o passo** (os subagentes da pesquisa caíram no limite de gasto, e
  o estudo foi feito direto, mais estreito):
  - quem executa os turnos: o que está acima sobre a queda do servidor vem da política, sem o
    executor estudado;
  - como a aprovação e a pergunta de um membro chegam na sala.
- **A prova, quando o passo vier:** um grupo de 3 em que cada um fala uma vez, um vê o que o
  outro disse, e a sala assenta; o membro chamado pelo nome que passa é cobrado; e o servidor
  morto no meio de uma rodada volta e ninguém fala duas vezes.

### Títulos das conversas (decididos em 13/10, feitos em 14/10 ✅)

Pedido do usuário em 13/10: a lista de conversas mostra a primeira linha do que ele escreveu,
cortada em 80 caracteres, e ele quer um título gerado. Entra depois do adaptador próprio da
Anthropic, que ainda não está na linha.

- **Duas etapas:**
  - o título instantâneo, que é o de hoje (a primeira linha, sem modelo, nunca falha);
  - o título gerado, por uma chamada lateral em segundo plano no começo do primeiro turno, que
    substitui o instantâneo quando chega. Se a chamada falha, fica o instantâneo.
- **O pedido:** 3 a 7 palavras, só a primeira em maiúscula, na língua do usuário, resposta em JSON
  `{"title"}`. O modelo lê a primeira mensagem, até 1000 caracteres.
- **Quem paga, decidido pelo usuário em 13/10: um modelo barato fixo, o Haiku 5.5** (US$ 0,10 e
  US$ 0,50 por milhão de tokens em 13/10; uns US$ 0,00004 por título), e não o modelo da conversa.
  - Não o Haiku 4.5, que custa dez vezes mais.
  - O raciocínio vai desligado na chamada: o Haiku 5.5 raciocina por padrão, e raciocínio é
    cobrado como saída.
  - Pede chave da API da Anthropic, paga por token; a assinatura do Claude não serve (a regra do
    passo 13).
  - **Decidido pelo usuário em 14/10, no lugar da proposta de 13/10:** o Haiku 5.5 na
    configuração mais barata que ele aceitar; se não houver chave da Anthropic ou a chamada
    falhar por falta de crédito, o gpt-6-luna da OpenAI, com o raciocínio desligado (esforço
    `none`), que custa o mesmo (US$ 0,10 e US$ 0,50 por milhão em 14/10); sem nenhum dos dois,
    fica o título instantâneo, sem cair no modelo da conversa. O gpt-5-nano, mais barato
    (US$ 0,05 e US$ 0,40), ficou de fora: é de 08/2025 e não desliga o raciocínio.
- **A procedência do título:** uma coluna diz se ele é `derived`, `llm` ou `user`. O
  gerado só troca o instantâneo, e nenhum dos dois troca um nome que o usuário digitou.
- **As travas, cada uma contra um defeito:**
  - resposta com mais de 12 palavras é recusada, não cortada: o modelo respondeu a mensagem em vez
    de dar o título;
  - título igual a um exemplo do pedido é recusado;
  - a conversa aberta com um cumprimento fica com o título instantâneo e tem nova chance nos
    turnos 2 e 3 (o Atlas guarda as palavras do usuário, não um provisório como "Friendly
    greeting", e pede o provisório sempre em inglês, porque um traduzido não seria reconhecido);
  - subagente e rotina não gastam chamada.
- **A tela:** o servidor avisa o app quando o título muda; hoje a lista só é relida no fim do
  turno.
- **Feito em 14/10** (docs/ARCHITECTURE.md, Conversation titles). Ficaram de fora a resposta em
  JSON forçada pelo provedor (as travas cobrem) e a espera por um modelo local (os dois modelos de
  título são da nuvem). Renomear a conversa no app fica para depois; a origem `user` já existe.
  - **Prova real (`prove_titles`), 14/10:** o Haiku 5.5 aceitou `thinking: disabled` e o
    gpt-6-luna o esforço `none`; nenhum raciocinou, os dois deram "Configurar Nginx com HTTPS e
    Certbot" para uma mensagem em português, US$ 0,0001 juntos.

### Título sem crédito (pedido e feito em 15/10, sem passo)

Pedido do usuário em 15/10: a chave da Anthropic da casa dele está sem crédito, e cada conversa nova
pedia o título ao Haiku, levava a recusa e só então ia ao GPT-6 Luna.

- **Uma quarentena:** um provedor que diz que a conta não pode pagar, ou que recusa a chave, fica
  10 minutos fora de todos os títulos e depois é tentado de novo. Fica só na memória do processo:
  reiniciar o Atlas tenta de novo.
- **Uma falha passageira não tira o provedor** (sobrecarga, limite por minuto, conexão caída): num
  título, um por conversa nova, não faz falta.
- **Ficou de fora** um castigo de 1 hora, gravado em disco: seria o das chamadas principais, não
  das auxiliares.
- **A prova:** os testes do título com o modelo falso, que falham com a quarentena desligada (o
  provedor sem crédito pulado por 10 minutos e pedido de novo depois; a chave recusada também; a
  sobrecarga não; e todas as conversas dividindo a mesma quarentena).

### Comandos em segundo plano no mapa de agentes (pedido e feito em 14/10, sem passo)

Pedido do usuário em 14/10: chegaram avisos "[IMPORTANT: Background command cmd-2 …]" de um
build do Unity e o mapa de agentes não mostrou nada, porque só lista subagentes. Ele quer os
comandos em segundo plano lá, numa lista separada.

- **A tela:** abaixo da árvore vem "N background tasks", cada uma com o nome, o
  id (`cmd-2`, o mesmo do aviso) e o tempo. O clique mostra o comando, o fim da saída e Parar. A
  pílula conta agentes e tarefas. Uma tarefa que termina com o mapa aberto fica nele e diz como
  terminou; ao reabrir, aparecem só as que rodam.
- **O nome é a descrição que o modelo dá**, o campo `description` do `run_command` (decidido pelo
  usuário). Sem descrição, aparece a primeira linha do comando.
  O campo custa 28 tokens na estimativa do Atlas, e o orçamento das ferramentas fixas fica com 1
  token de folga no teto de 4.350.
- **O protocolo:**
  - `command.list` ao abrir a conversa e `command.stop`, que devolve a saída;
  - os últimos 4.000 caracteres da saída, relidos a cada 1,5 s só com o detalhe aberto.
- **Sem consulta periódica** para saber quando um processo terminou. O
  começo e o fim chegam por evento (`command.update`), porque o Atlas já espera o fim de todo
  comando.
- **Fica de fora:** os comandos de um subagente não aparecem no mapa do pai, porque ficam na
  conversa do filho.
- **Prova real (`prove_tasks`), 14/10:** pedido um build em segundo plano, o gpt-6-luna deu sozinho
  a descrição "Start game build" (3 palavras). A tela ouviu o começo, leu a saída e ouviu o fim
  como `stopped` depois do Parar. Foram 2 chamadas, US$ 0,0003. O ensaio no modelo falso
  mostrou que o roteiro acusa a chamada sem descrição.

### Detecção dos harnesses, perguntas no Telegram e atualização pelo app (pedidos e feitos em 18/10, sem passo)

Três pedidos do usuário em 18/10: revisar a detecção automática dos harnesses e consertar todos os
problemas; um botão que aparece no app quando há atualização, com o fechar, atualizar e abrir
melhorado; e as perguntas do Claude Code, que não chegavam ao Telegram.

- **Detecção, revisada.** Cada defeito virou um teste:
  - a tela pede de novo (`executor.detect`): ao mostrar a aba Executores, no máximo uma vez por
    minuto, e no "Procurar de novo". Antes só uma abertura do Atlas ou um plugin ligado ou desligado
    procuravam, e um Claude Code instalado, atualizado ou logado com o Atlas aberto pedia reiniciar;
  - o Claude Code é achado como o Agent SDK 0.2.165 acha (`_find_cli`): no Windows, só o
    `claude.exe` nativo, e depois os lugares onde os instaladores dele o põem, porque o PATH do app
    pode ser mais velho que a instalação. Novos do Atlas: os lugares do WinGet (de onde vem o do
    usuário) e o do Homebrew. O `claude.cmd` do npm é recusado como o SDK recusa, dizendo como
    instalar o nativo, onde antes dizia "não está no PATH";
  - o que não pode ser usado diz o porquê e o que fazer (o comando de instalar, ou `claude update`
    e `winget upgrade Anthropic.ClaudeCode` para um velho), inteiro na tela;
  - uma procura que falha vira uma linha com o porquê, e nunca desliga o plugin: antes, o plugin
    ficava "falhou" e o harness sumia da tela e da recepção até ser ligado de novo;
  - lida como o Claude Code 2.1.296 escreve: o `auth status` sai com código 1 sem login e ainda
    diz o estado; a versão é lida na linha do Claude Code e comparada pelos números; versão e conta
    são pedidas juntas. Sem login, a tela diz "sem login" e a troca de conta faz o login;
  - o `CLAUDE_CONFIG_DIR` chega ao Claude Code que o Atlas roda, como o SDK passa: a aba lia as
    sessões dessa pasta, e as rodadas iam sem ela.
- **Perguntas no Telegram.** A sessão aberta pelo app não tem conversa, então o que o Claude Code
  perguntava ficava só no app (decidido em 17/10). Agora vai ao chat de cada pessoa pareada, cada
  chat com o seu aviso e o nome da pasta da sessão; vale a primeira resposta, num chat ou no app. O
  chat de cada pessoa fica guardado no pareamento (migração 2 dos plugins, que já preenche os
  pareados de antes). Um pedido aberto chega também ao canal que (re)começa.
- **Atualização pelo app**, para um checkout, enxuta:
  - o Python procura (`python -m atlas.installation check`): 10 s depois de abrir, a cada hora e
    quando a janela volta depois de uma hora (a `main` do Atlas muda várias vezes por dia). Só
    numa instalação;
  - o botão "Atualizar o Atlas" aparece acima da casa, com a contagem, e no trilho da barra
    escondida; abre o que mudou, o que fechar agora para, e "Atualizar e reabrir";
  - a troca (`python -m atlas.installation handoff`, que mora no checkout): o app a começa, espera o
    marcador dela, e fecha; ela espera o app sumir e as casas serem soltas, roda o `atlas update`
    com a volta, grava como foi, e abre o app de novo, que diz
    como foi. Carrega só a biblioteca padrão: no Windows o `uv sync` não troca um pacote em uso.
    No Windows começa pelo `cmd /d /s /c start "" /b`;
  - um app aberto durante a atualização avisa e não sobe servidor; no Linux, o app só reabre
    quando o sandbox deixa, senão o resultado diz os dois comandos.
- **Fica de fora:** a janela de progresso durante a atualização, os canais
  (`stable`, `canary`) e a atualização do app empacotado (18b).
- **A prova:** os testes (a detecção pelo servidor de verdade; o Telegram falso com duas pessoas
  pareadas; a troca com git de verdade, um processo de verdade no lugar do app e um app falso que
  reabre; o Vitest da troca pelo `cmd start` de verdade; o e2e do botão até o app reaberto dizer
  "atualizado"). E a **prova real** (`scripts/prove_update.py`, sem crédito), em 18/10 no Windows:
  uma instalação temporária clonada de um repositório local, com as ferramentas da instalação do
  usuário, o Python sincronizado e o app construído; a `main` ganhou um commit que muda os pacotes
  e o código do app; o app de verdade, aberto nela, procurou sozinho, mostrou o botão e o diálogo,
  e o clique em "Atualizar e reabrir" fechou o app. A troca de verdade puxou o commit, reinstalou
  os pacotes do app (o próprio Electron, que o app fechado usava, entre eles), reconstruiu e
  reabriu o app, que leu como foi; nenhum marcador sobrou (os 7 itens da prova, 1,5 min da tela).
  **A primeira rodada achou um defeito:** o Electron não lê a variável `APPDATA`, então o app da
  prova, sem pasta própria, bateu no bloqueio de instância única do Atlas aberto do usuário e
  fechou. Daí a troca reabrir o app com as opções com que foi aberto (mantendo `--user-data-dir` e
  `--lang`), e a prova usar pasta própria.
- **Fica para o usuário:** a cópia de uso ainda está no código de antes do botão, então esta
  primeira atualização é a última pelo terminal (fechar o Atlas, `atlas update`, abrir); as
  próximas vêm pelo botão.

### Cota do plano, comandos de barra e o `/` do Atlas (pedidos e feitos em 18/10, sem passo)

Três pedidos do usuário em 18/10: ver no painel lateral direito o quanto a conta do executor já
usou das cotas da assinatura; usar pelo Atlas os comandos de barra do Claude Code; e o `/` listar
direito as skills do harness, do bot e do Atlas principal. Com a regra dele: o que é do adaptador
no plugin, o que é do núcleo no núcleo, porque o Claude Code é o primeiro de vários executores.

- **O contrato dos plugins cresceu duas versões** (só cresce): a 9 traz a cota (`UsageWindow`,
  `PlanUsage`, o evento `UsageChanged`, `Executor.usage()`), a 10 traz os comandos descritos
  (`SlashCommand`, o evento `CommandsChanged`, `Executor.commands(folder=)`). O núcleo não sabe
  nada do Claude Code: guarda, junta e mostra o que o plugin diz.
- **Cota no painel**, sempre visível: uma barra por janela (sessão atual de 5 h, semana, semana
  por modelo), com quanto foi usado e quando renova; âmbar a partir de 80%, vermelha a partir de
  95%. O plugin pergunta ao Claude Code pelo `get_usage` do protocolo de controle (oficial,
  experimental) e ouve o `rate_limit_event` de cada rodada (oficial). O app relê ao mostrar o
  executor quando a leitura tem mais de 2 minutos, sem consultar por relógio. Fica de fora ler o token
  OAuth da conta para perguntar à API direto.
- **Comandos de barra do Claude Code**, descritos e antes de qualquer sessão rodar: o plugin lê a
  lista do `initialize` (no 2.1.296, 101 para o usuário, 54 do próprio Claude Code), ouve o
  `commands_changed` e, sem sessão na pasta, abre um Claude Code ali e fecha na hora, sem prompt,
  para as skills do projeto entrarem. Os comandos que o Claude Code roda sozinho (`/context`,
  `/usage`, `/mcp`) respondem sem chamar o modelo; a sessão relida mostra `/nome args` como foi
  digitado e a saída do comando, que antes se perdia.
- **Uma lista `/` para todas as caixas:** dois grupos (comandos, depois skills), com descrição e
  dica, busca em níveis, no máximo 30 de cada, setas, Enter ou Tab, Esc.
- **O `/` nas conversas do Atlas e dos bots**: os comandos do próprio Atlas (`/new`, que não
  aparece na conversa de um bot, e `/stop`), que a tela roda, e as skills que o usuário pode
  chamar ali (as do bot, as suas e as da biblioteca que ele vê). `/nome palavras` enviado carrega
  a skill (uma mensagem de invocação, com a pasta, os arquivos e as palavras do usuário como
  instrução), do app e do Telegram (inclusive `/nome@bot` de grupo), e
  conta como uso para o curador. A mensagem fica guardada como `skill`: é do usuário, não um aviso,
  e a tela e o título mostram `/nome palavras`. `user-invocable: false` tira a skill do menu;
  uma skill com nome de comando perde para o comando.
- **A prova:** os testes (o Claude Code falso com cota, comandos e saída de comando; o servidor de
  verdade; o e2e da cota, do `/co`, do `/context` e do `/` do Atlas com uma skill escrita com o app
  aberto). E a **prova real**, sem crédito, pelo plugin na conta do usuário em 18/10: cota de 5 h
  em 71% e semanal em 28%, plano team; 101 comandos listados numa pasta.

### Adaptador da Anthropic (estudado em 13/10, decidido em 14/10, passo 3b)

Pedido do usuário em 13/10: usar o Opus 5.5 como modelo do próprio Atlas, com chave da API paga
numa conta pessoal do Console (a assinatura do Claude não serve: a regra do passo 13). Decidido
pelo usuário em 13/10: o adaptador próprio, sem passar pelo OpenRouter nem testar antes por ele.
Decidido pelo usuário em 14/10: vem agora, antes dos títulos com o Haiku 5.5 e do 16.

- **Decidido em 14/10: no núcleo,** com o pacote `anthropic` como extra, importado só pelo
  `atlas.models.store`. A linha da arquitetura que mandava SDK nativo para plugin muda junto.
  O pacote é o 1.11.0 (30/09), não o 1.13.0: a regra dos 14 dias de idade.
- **Decidido em 14/10: a limpeza de resultados antigos fica com a Anthropic (opção 1, abaixo).**
  A documentação de context editing confirma, em 14/10, que nos quatro modelos a limpeza do lado
  do servidor nunca invalida o raciocínio guardado. Ela avisa também que cada limpeza invalida o
  cache dali em diante, e recomenda `clear_at_least`: a regra do Atlas ganha um mínimo de 20 mil
  tokens por limpeza, para cada reescrita do cache valer o que limpou, a medir na prova real.
- **Achados ao começar (14/10), que o estudo não cobria:**
  - **A compactação e as instruções novas também editam o passado** do lado do Atlas. Para saber
    de onde tirar o raciocínio, cada resposta guarda uma impressão digital de tudo o que veio
    antes dela no pedido; quando a impressão não bate mais, o raciocínio sai dali até o fim (tirar
    do fim é permitido), em vez de tirar tudo depois de comprimir.
  - **A busca de ferramentas pelo `tool_reference`** pede que o laço entregue ao adaptador todas
    as ferramentas adiadas, e não só as carregadas: acrescentar uma ferramenta à lista também
    quebra o raciocínio.
  - **O cache de 1 hora** custa 2× a entrada na escrita (o de 5 minutos, 1,25×), e o models.dev
    só traz o de 5 minutos: o consumo ganha a parte de 1 hora, o livro de chamadas uma coluna, e
    o catálogo um fator por provedor, com a fonte citada.

- **Por que não o OpenRouter, que já está no catálogo com o Opus 5.5:** o adaptador de Chat
  Completions não marca pontos de cache (`cache_control`), e a Anthropic só faz cache onde o pedido
  marca. No laço do agente, que reenvia a conversa inteira a cada chamada de ferramenta, a leitura
  do cache custa 0,05× a entrada no Opus 5.5. O OpenRouter cobra ainda uma taxa sobre o crédito. O
  endpoint compatível com a OpenAI da própria Anthropic não faz cache e é, nas palavras dela, para
  teste, "not considered a long-term or production-ready solution".
- **Onde entra:** um terceiro wire, `messages`, ao lado de `chat` e `responses`, com o mesmo
  `ModelClient`; `[providers.anthropic]` no `providers.toml` com `ANTHROPIC_API_KEY`, e o catálogo
  `anthropic` do models.dev (hoje só há os ids do OpenRouter, `anthropic/claude-opus-5.5`; os da
  Anthropic usam hífen, `claude-opus-5-5`, e converter ponto em hífen é um erro fácil).
  - **Decidido pelo usuário em 14/10 (era proposta):** no núcleo, com o pacote `anthropic` como extra (como
    `ddgs` e `mcp`), importado só pelo `atlas.models.store`. A arquitetura diz hoje que SDK nativo
    entra como plugin; os plugins são o passo 16. A linha da arquitetura muda junto.
  - O SDK é o `anthropic` 1.x (1.13.0 em 09/10), que já usa o `httpx2` do `openai` 3.x.

**Os fatos da API em 13/10** (platform.claude.com, docs de prompt caching, thinking, preserved
thinking, streaming, errors, tool search):

- **Raciocínio:** os quatro modelos (Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5) só têm o
  adaptativo e raciocinam por padrão; `budget_tokens` é HTTP 400. O esforço vai em
  `output_config.effort` (`low` a `max`); o padrão é `medium` no Opus 5.5 e no Haiku 5.5, `high` no
  Fable 5.1 e no Sonnet 5.5.
  - Desligar: impossível no Opus 5.5 e no Fable 5.1 (`disabled` é 400); no Sonnet 5.5 o mais perto
    é `between_tools`; no Haiku 5.5 `disabled` vale até o esforço `high`. O guardião, que pede
    `effort: none` (passo 7), usa `low` onde não dá para desligar.
  - O texto do raciocínio vem vazio por padrão (`display: "omitted"`): para mostrá-lo, pedir
    `"summarized"`. Ele é cobrado inteiro de qualquer jeito.
  - Temperatura, `top_p` e `top_k` fora do padrão são 400 nos quatro.
- **Raciocínio guardado ("preserved thinking"):** os blocos `thinking` e `redacted_thinking` voltam
  sem mudança nenhuma, inclusive os vazios, na ordem em que vieram. Um bloco só vale enquanto o
  `system`, as `tools` e todas as mensagens antes dele estão idênticos byte a byte. Em conta criada
  a partir de 31/08/2026, como a que o usuário vai abrir, editar o passado dá 400 ("Invalid
  `signature` in `thinking` block. The block is bound to a different conversation"); em conta mais
  velha, o bloco é só descartado.
  - Contam como edição: trocar ou encurtar resultado antigo de ferramenta, resumir mensagens no
    cliente, trocar o prompt de sistema, mexer na lista de ferramentas (exceto acrescentar uma
    `defer_loading` nunca citada).
  - Não contam: acrescentar no fim, mudar esforço ou `max_tokens`, mover marcas de cache, limpar ou
    compactar do lado do servidor, e tirar blocos de raciocínio do começo, do fim, ou todos.
  - O beta `thinking-binding-controls-2026-08-01` com `drop_block` evita o 400 descartando os
    blocos, ao preço de refazer o raciocínio: a Anthropic mediu +2,5% a +20,1% de saída descartando
    uma vez por sessão, e +4,6% a +67,1% descartando a cada turno.
  - Trocar de modelo no meio: o bloco que o modelo novo não lê é descartado em silêncio, sem
    cobrança.
- **Cache:** até 4 pontos, na ordem `tools` → `system` → `messages`; mínimo de 512 tokens nos
  quatro (abaixo disso não cacheia, sem erro). Escrita de 5 min custa 1,25× a entrada, de 1 h custa
  2×; leitura 0,1× (0,05× no Opus 5.5 e no Sonnet 5.5, 0,025× no Fable 5.1). O consumo vem em três
  partes: `input_tokens` (só o que vem depois da última marca), `cache_creation_input_tokens` (com a
  divisão 5 min/1 h) e `cache_read_input_tokens`. Leitura de cache não conta no limite por minuto.
  - O models.dev tem um preço só de escrita (o de 5 min): a escrita de 1 h pede um campo novo no
    preço, ou a conta sai errada.
- **Busca de ferramentas sem beta e sem quebrar o cache:** a nossa `tool_search` devolve blocos
  `tool_reference` no resultado, e todas as adiadas vão em todo pedido com `defer_loading: true`. A
  definição entra no ponto da referência, no fim do contexto, como a busca nativa da OpenAI que já
  usamos. Pelo menos uma ferramenta não pode ser adiada, e uma adiada não leva marca de cache.
- **Paradas:** `end_turn`, `tool_use`, `max_tokens`, `stop_sequence`, `pause_turn` (só ferramentas
  do servidor), `refusal` (com HTTP 200) e `model_context_window_exceeded`. O uso do
  `message_delta` é cumulativo.
- **Erros:** 402 `billing_error`; crédito acabado chega também como 400 "Your credit balance is too
  low" (não está na página oficial, só em relatos); o teto de gasto mensal é 429 sem `retry-after`
  (`enforced_spend_limit_reached`) e não é limite passageiro; 529 é sobrecarga; "prompt is too long"
  é 400. `tool_choice` `any` ou `tool` é 400 no Opus 5.5, no Sonnet 5.5 e no Fable 5.1. Pedido
  terminando em assistente (prefill) é 400 nos quatro.
- **A chave se confere de graça** em `GET /v1/models`, que diz também os limites e os esforços de
  cada modelo.
- 1M de contexto e 128 mil de saída nos quatro, sem cabeçalho beta.

**As regras do adaptador:**

- O SDK oficial com as novas tentativas dele desligadas (`max_retries=0`), porque as do SDK
  disparam em dobro e ignoram o `Retry-After`. Só a chave da Anthropic vai no cabeçalho: o SDK lê
  `ANTHROPIC_AUTH_TOKEN` do ambiente e mandaria dois.
- **Os 4 pontos de cache na API direta da Anthropic:** o fim da parte fixa do sistema, a última
  ferramenta, e o fim das duas últimas rodadas completas (uma rodada aberta não leva marca).
  A decoração tira as marcas antigas antes, senão passa de 4. O sistema e todo o histórico, menos
  o fim, ficam idênticos byte a byte: um prompt de sistema com a hora derruba o acerto de cache
  para zero.
- **1 h de cache para conversa do usuário, 5 min para subagente e rotina** (`ttl: auto`): 63% da
  escrita de cache das conversas era reescrita depois de 5 a 60 min parado, e 1 h corta 42% dela;
  em subagente custaria 49% a mais.
- **A ordem dos blocos de cada resposta guardada** e devolvida como veio (pensamento, texto,
  chamada, pensamento...): refeita como [pensamentos][texto][chamadas], a assinatura quebra.
  Os campos que o SDK só devolve (`parsed_output`, `caller`, `citations: null`) não voltam, ou dão
  400.
- **A conversão do histórico:** resultados seguidos viram uma mensagem do usuário com vários
  `tool_result`, e a mensagem que o usuário mandou no meio do turno entra depois deles, na mesma
  mensagem; mensagens seguidas do mesmo papel se juntam por blocos, nunca juntando texto, que
  mudaria o prefixo; bloco de texto vazio ou só com espaço nunca vai (400 em qualquer
  posição); um `tool_use` precisa do resultado na mensagem logo seguinte; histórico que começa pelo
  assistente ganha uma mensagem do usuário antes; `oneOf`/`anyOf` no topo do esquema e nome de
  ferramenta repetido são 400; `image/jpg` vira `image/jpeg`, e formato de imagem que a Anthropic
  não lê vira nota (senão todo pedido seguinte dá 400).
- **O fluxo:** sem `message_stop` a resposta não terminou e não vale; uma chamada de ferramenta
  sem motivo de parada caiu no meio e não roda com `{}`; `refusal` com conteúdo vazio não se
  repete, ou repete para sempre; o `stop_details` da recusa se perde no SDK e é lido à mão.
- **O que fica de fora:** o login da assinatura (`sk-ant-oat`, pego do Claude Code e disfarçado de
  Claude Code para passar no classificador da Anthropic: proibido, a regra do passo 13); o beta de 1M
  (padrão nesses modelos, e dava 400 em contas sem ele); o modo rápido; as ferramentas do servidor.
- Ao chegar a vez: cada regra acima vira teste.

**A limpeza de resultados antigos numa conversa longa (decidida pelo usuário em 14/10: a opção 1).** Hoje, passados
100 mil tokens, a cada chamada o Atlas troca por uma nota o resultado que deixa de estar entre os 3
mais recentes, na cópia que vai ao modelo (a regra veio da própria Anthropic). Exemplo: o Opus
revisa 30 arquivos; no 25º a conversa passa de 100 mil, e a leitura do 22º vira nota. Para a
Anthropic, isso edita o passado: o raciocínio das rodadas seguintes deixa de valer, e na conta nova
o pedido volta 400. As duas saídas:

1. **(Recomendada) A limpeza com o servidor da Anthropic:** as mesmas regras (100 mil, guardar 3)
   vão no pedido (`clear_tool_uses_20250919`, beta `context-management-2025-06-27`, de 2025), e a
   Anthropic limpa sem invalidar o raciocínio. É o original da nossa regra. A compactação (a 85% de
   1M, rara) e a troca do prompt de sistema quando a memória muda continuam do nosso lado: nessas
   horas o Atlas tira de uma vez todo o raciocínio antigo, o que é permitido e custa uma vez só. A
   Anthropic tem também compactação do lado dela (beta `compact-2026-09-04`), mas ela não guarda as
   palavras do usuário como a nossa.
2. **Tudo do nosso lado, sem beta,** como nos outros provedores, tirando o raciocínio a partir do
   ponto editado. Para não pagar isso a cada chamada, a limpeza teria de ser em blocos, e essa
   regra seria nova, nossa.

**A prova real, ao chegar a vez** (perguntar o custo antes): as provas de modelo, agente e
ferramentas no Haiku 5.5 (centavos), e uma conversa longa no Opus 5.5 que passe de 100 mil tokens,
mostrando leitura de cache no livro e nenhuma recusa (estimado em US$ 0,50 a 1 em 13/10). O
servidor falso dos testes passa a falar o protocolo da Anthropic e a recusar como ela: raciocínio
editado, `tool_choice` forçado, bloco vazio, prefill e temperatura.

### Google e GitHub pelo terminal (decididos em 13/10, passo 14b)

Pedido do usuário em 13/10: Gmail, Calendário e GitHub são mais úteis agora, na fase de uso do
passo 11, do que um canal de mensagem. Pela escada de integração (Regras de trabalho, abaixo),
entram como programa de terminal com skill, sem MCP e sem plugin.

- **Os programas:** `gh` (oficial do GitHub), `gcloud` (oficial do Google) e `gws` para Gmail e
  Calendário (`googleworkspace/cli`, que o próprio README diz não ser produto oficial do Google). O
  Atlas os instala pelo `run_command`, passando pelo portão.
- **As skills de partida** (decididas pelo usuário em 13/10); depois o uso as ajusta:
  - **GitHub: uma skill `github`**, toda em cima do `gh`.
  - **Google: as skills do próprio `gws`** (`googleworkspace/cli`, Apache-2.0, geradas da
    ferramenta): `gws-shared` (login e regras), `gws-gmail` e as de enviar, ler, responder,
    encaminhar e triar, `gws-calendar` e as de agenda e de marcar. Um script Python próprio
    ficou de fora: guardaria o login do Google num JSON aberto na casa (o Atlas guarda segredo só
    no cofre) e traria as bibliotecas Python do Google como dependência. O `gws` também manda pedir
    só o acesso ao e-mail e ao calendário (`--services`).
  - **O login do Google fica com o `gws`**, criptografado no chaveiro do sistema, fora da casa. O
    `gws auth setup` usa o `gcloud` para criar o projeto no Google Cloud e ligar as APIs sozinho;
    sem navegador (container, servidor), o login é feito no PC e levado com `gws auth export`, o
    fluxo documentado do `gws`.
- **Vêm embutidas no Atlas** (decidido pelo usuário em 13/10). É o primeiro jeito de o Atlas trazer
  skill própria:
  - o repositório traz a pasta das embutidas, e o servidor, ao subir, as copia para o `skills/`
    da casa (a subida do servidor cobre a instalação e o `atlas update`, que reabre o app), guardando a
    impressão (hash) de cada uma num manifesto;
  - a skill nova é copiada; a que mudou no repositório só é trocada se a cópia da casa continua
    igual à original; a que o usuário ou o agente editou fica como está, e uma ordem
    a devolve à original; a que o usuário apagou não volta;
  - o curador nunca arquiva uma embutida; a revisão em segundo plano pode melhorá-la, como melhora
    qualquer skill, e o agente numa conversa também (corrigido em 13/10: a guarda barra a revisão
    só de apagar ou arquivar uma embutida, nunca de escrever nela). Depois de mudada, a embutida vira do
    usuário e para de receber atualização; `atlas skills reset` a devolve à original, e a mudança
    fica
    guardada como versão.
  - O usuário que começa do zero (regra de 09/10) continua começando sem nada dele: as embutidas
    são do Atlas, não do V3.
- **O login é do usuário, uma vez por serviço:** o `gh` e o `gcloud` abrem o navegador. Para o
  `gws`, o `gws auth setup` cria o acesso no Google Cloud pelo `gcloud`; sem o `gcloud`, o usuário
  o cria no console (projeto, APIs, cliente "Desktop app", ele mesmo como usuário de teste), uns 15
  minutos. No modo de teste o acesso do Google cai a cada 7 dias; publicado como app não
  verificado só para ele, fica.
- **As três travas:**
  - **Ação para fora pergunta ao usuário.** O portão de hoje reconhece comando perigoso (apagar,
    formatar), mas não sabe que `gws gmail send` manda e-mail nem que `gh pr merge` junta código.
    Regras de pergunta (`ask`): cada envio pede aprovação. Decidido pelo usuário em 13/10:
    - **Uma lista para o Atlas inteiro, no portão, nunca uma instrução na skill.** Instrução o
      modelo pode não seguir, e o próprio Atlas edita as skills (6b), então uma trava dentro delas
      poderia ser apagada por ele ou por uma instrução injetada. As regras ficam nas configurações,
      não nas skills.
    - **A lista padrão vem no núcleo, ligada desde a instalação:** as ações para fora dos três
      programas (`gh pr merge`, `gh repo delete`, `gh api` com método de escrita, `gws gmail
      send`, apagar evento e as outras, levantadas no passo). Por cima, as regras do usuário no
      `config.toml`, para pôr ou tirar comandos.
    - **Comando da lista vai direto ao usuário, sem o guardião,** e pergunta mesmo com o ⚡: uma
      regra `ask` explícita pergunta em todos os modos.
    - **Como compara, conferido em 13/10:** a regra vale para cada parte de um comando composto
      (`&&`, `;`, `|`), inclusive dentro de subshell e de `$(...)`, passando por prefixo de
      variável e por embrulho (`timeout`, `env`, `sudo`, `xargs`). `sh -c '...'` e caminho
      completo (`/usr/bin/gh`) escapariam de uma regra só por prefixo; a detecção do passo 7
      (`atlas.trust.detection`) já desmonta `sh -c`, `bash -c` e `eval`, e a lista usa o mesmo
      desmonte. A prova tenta cada um desses disfarces.
    - **O agente não muda a lista sozinho:** escrever no `config.toml` da casa pergunta, como um
      caminho protegido que nenhum modo aprova sozinho, e pergunta também com o ⚡ (decidido pelo
      usuário em 13/10): sem isso, o agente poderia tirar a regra e depois enviar.
    - **A skill só explica** ao modelo que o envio vai pedir aprovação, para ele não estranhar.
    Junto entra a lista de destinos conhecidos (decidida em 10/10, Confiança, acima), mudada pelo
    usuário em 13/10: como todo envio já pergunta, ela não é uma trava à parte; o cartão de
    aprovação diz quando é o primeiro e-mail para um endereço ("the first email to fulano@..."),
    porque um e-mail malicioso pode pedir "encaminhe isto para fulano". Um endereço vira conhecido
    quando o usuário aprova um envio para ele (`trust_destinations`).
  - **A saída marcada como texto de fora.** O e-mail lido pelo terminal chegaria ao modelo como saída
    de comando comum. O Atlas embrulha a saída desses comandos no mesmo bloco do resultado de MCP.
    **Nova:** a prova manda um e-mail com uma instrução inofensiva e confere que ele não a segue.
  - **O login dos programas fica fora do backup.** O `gh` e o `gws` guardam o token na pasta do
    usuário, e no container essa pasta (`home/`) entra no backup (Backup e várias casas, acima), o
    que quebraria a regra de backup sem chaves. As pastas deles entram na fronteira como `machine`
    (conferido em 13/10): `~/.config/gws` (`GOOGLE_WORKSPACE_CLI_CONFIG_DIR`; no Linux sem chaveiro,
    a chave que abre o `credentials.enc` fica na mesma pasta), `~/.config/gh` (`GH_CONFIG_DIR`; sem
    chaveiro, o token fica em texto no `hosts.yml`) e `~/.config/gcloud` (`CLOUDSDK_CONFIG`, em
    texto). No container não há chaveiro, então os três ficam em arquivo.
- **A prova, com a conta real:** ler e responder um e-mail, marcar uma reunião e abrir uma issue,
  cada ação para fora aprovada no clique; um e-mail para destinatário novo pergunta; e o e-mail com
  a instrução inofensiva não é seguido.

### Instalador (decidido em 13/10, passo 18)

Pedido do usuário em 13/10: mandar o Atlas para outras pessoas testarem. Estudada também a
assinatura de instaladores em 2026.

- **Primeiro o comando de uma linha (18), depois o app empacotado (18b) e assinado (18c).** O comando
  não depende de certificado, então os testadores não esbarram no aviso do Windows nem no
  bloqueio do Mac.
- **O comando:** traz o próprio `uv`, o Python 3.13, o Git (o PortableGit fixo, no
  Windows) e o Node, cada um numa versão fixa e conferida pelo hash, sem usar o que a máquina já
  tem; clona o Atlas numa pasta temporária e só depois o move para o lugar, constrói o app, põe o
  atalho e o caminho no PATH; rodado de novo, conserta e atualiza. É o que a cópia de uso já faz
  (`atlas desktop`, `atlas update`), sem o usuário ter instalado nada antes.
  - **Os defeitos conhecidos de instaladores viram testes**, os do Windows primeiro: o caminho
    curto do perfil (`FIRST~1`) que quebra a construção; um caractere não-ASCII no script, que o
    PowerShell 5.1 lê errado; uma falha que fechava a janela do usuário no
    `iex`; a política de execução Restrita barrando o programa instalado; o Git baixado antes de
    recusar uma pasta ocupada.
- **Enquanto o repositório for privado,** só baixa quem foi convidado como colaborador e está
  logado no GitHub na máquina: o instalador guia o `gh auth login` e clona com ele. **Os dois
  scripts ficam num gist público** (decidido pelo usuário em 13/10), cópia dos do `main`, porque o
  endereço cru de um repositório privado responde 404 a quem não está logado: continua uma linha
  só, `irm <gist> | iex`. Mandar o arquivo pediria `-ExecutionPolicy Bypass`, e um `.exe` ou
  `.cmd` esbarra no aviso do Windows, que só a assinatura do 18c tira.
- **O desenho detalhado** está em `docs/ARCHITECTURE.md` (The installer), revisto em 13/10. Achados
  que mudam o código de hoje:
  - as ferramentas da instalação ficam fora do PATH do usuário, e o app deixa de rodar o `uv` do
    PATH (nesta máquina, ele achava o de outro programa);
  - o login do `gh` serve o clone só pela configuração do próprio clone, sem mexer no
    `~/.gitconfig`.
  - **Cerca de 75 defeitos conhecidos de Windows e 30 de Linux e Mac** em instaladores foram
    lidos um a um; uns 65 dos de Windows valem para qualquer instalador como o nosso. Dois a
    evitar: a comparação do PATH com `-like`, que falha com `[` no nome do usuário, e o PATH
    regravado como `REG_SZ`, que congela as variáveis `%...%`.
- **A primeira abertura pede a chave pela tela,** sem terminal: escolher o provedor e colar a
  chave, que vai para o cofre. Um provedor hospedado ou uma camada grátis não têm par no Atlas até
  o Atlas Cloud. Decidido pelo usuário em 13/10:
  - **a chave é conferida onde não custa nada** (a conferência de chave que já existe), e só a
    chave recusada (401, 403) é barrada. Sem crédito, no limite por minuto ou com o provedor fora
    do ar, a chave é guardada com o aviso, porque proxy de empresa, bloqueio
    regional e provedor lento recusariam chave boa; nenhuma chamada ao modelo é gasta;
  - **o login OAuth da OpenRouter fica de fora.**
- **O app empacotado vem em duas partes** (decidido pelo usuário em 15/10). A assinatura não é
  necessária para empacotar, só para tirar o aviso de programa desconhecido:
  - **18b, sem assinatura e grátis:** o instalador, o atalho na área de trabalho e a atualização
    pelo próprio app. No Windows, o `Setup.exe` do NSIS (electron-builder) abre a tela do
    SmartScreen na primeira vez ("Mais informações", "Executar assim mesmo"), e o electron-updater
    atualiza sem assinatura; o Smart App Control, ligado só em Windows 11 instalado do zero,
    bloqueia sem opção, e antivírus acusam falso positivo com mais facilidade. No Mac, o DMG leva
    a assinatura ad-hoc, automática e grátis, e a primeira abertura pede Ajustes do Sistema →
    Privacidade e Segurança → "Abrir mesmo assim" (o clique direito → Abrir deixou de servir no
    macOS 15). A atualização do Electron no Mac (Squirrel.Mac) exige app assinado, então o 18b
    traz um atualizador nosso: o app baixa a versão nova e troca a pasta, e o arquivo baixado pelo
    próprio app não ganha a marca de quarentena. O comando do 18 continua, sem aviso nenhum,
    porque constrói o app na máquina.
  - **18c, a assinatura,** quando o repositório abrir: tira os avisos e devolve o Mac à
    atualização do Electron.
- **A assinatura (18c), quando vier:** no Windows, a Microsoft Store (MSIX assinado pela
  própria Microsoft, conta grátis) ou a SignPath Foundation (grátis para projeto OSI com build no
  CI), e o Certum Open Source (€49 por ano) como reserva; o Azure Artifact Signing não atende
  pessoa nem empresa do Brasil. No Mac, a conta de desenvolvedor da Apple (US$ 99
  por ano) é inevitável, e o Homebrew recusa app sem notarização desde 01/09/2026. No Linux,
  AppImage, sem assinatura.
- **Antes de abrir o repositório:** o nome novo (o usuário decidiu trocar "Atlas"), a revisão do
  histórico atrás de dado pessoal, e a marca pesquisada no INPI.

### Plugins

- **O formato é uma pasta compatível com a do Claude Code:** `plugin.json` (ou `.claude-plugin/`),
  `skills/`, `.mcp.json`, `hooks/` e `agents/`.
- **Python só quando arquivo não basta,** com `register(ctx)`: canal, tela, provedor ou ouvinte de
  evento.
- **Vêm desligados.** As ferramentas deles chegam por busca e passam pelas mesmas aprovações que as
  do núcleo.
- **Cada plugin traz a própria tela,** e ela só aparece com o plugin ligado.
- **O contrato só cresce:** nada é removido nem renomeado.

#### O estudo para o passo 16 (13/10)

O estudo fica aqui para não ser refeito.

- **MCP e plugin não são a mesma coisa.** O MCP é uma fonte de ferramentas, a skill é o texto que
  ensina a tarefa, e o plugin é a caixa que junta skills, MCPs, ganchos e agentes. O conector
  (claude.ai, ChatGPT) é um MCP hospedado com o login guardado pela empresa; no Atlas, o lugar dele
  é o Atlas Cloud (passo 20).
- **O formato do Claude Code é lido também pelo Codex** e pelo formato comum `agent-plugins.org`. A
  decisão de 10/10 se mantém.
- **Plugin de produto costuma ser só um apontador:** o do GitHub no Claude Code são dois arquivos,
  `plugin.json` e um `.mcp.json` para o MCP oficial do GitHub; os de Gmail, Calendário e GitHub no
  Codex apontam para os MCPs oficiais e para o conector da OpenAI.
- **A promessa do SDK** (só cresce, um aviso dura duas versões, `ctx` nunca perde método):
  declarar tudo experimental quebraria plugins.
- **Plugin novo pede consentimento:** antes de ligar, o Atlas mostra o que ele declara (canais,
  ferramentas, ganchos, MCPs), e uma atualização que declara algo novo pergunta de novo.
- **Os canais além do Telegram** (o passo 16b saiu da linha em 18/10, pelo usuário) são plugin
  em Python: o servidor do Atlas fica de pé, então o canal é plugin.
  - Quem fala com o bot entra por código: o desconhecido recebe um código, o usuário aprova, e até
    lá a mensagem dele não é processada.
  - Canais e autorização são onde mais aparecem defeitos. Exemplos a testar: um membro de grupo
    fora da lista trocando o modelo do dono pelo botão; a aprovação chegando sem notificação no
    celular e vencendo em 5 min; uma resposta repetida aprovando um pedido diferente.
  - **WhatsApp:** a API oficial da Meta proíbe assistente de IA de uso geral desde 15/01/2026 (no
    Brasil o CADE suspendeu a regra, e a Meta cobra por mensagem em números +55), e pede endereço
    público. O caminho dos agentes pessoais é o Baileys como aparelho conectado (por uma ponte em
    Node), com risco pequeno de banimento, num chip separado; desde 30/06 o
    WhatsApp pede passkey para conectar alguns aparelhos, e o defeito no Baileys segue aberto.
  - **Telegram:** o mais simples (sem endereço público, com botões de aprovação), mas o usuário não
    tem interesse nele agora (13/10).

### Chave pedida na conversa (decidida em 14/10, passo 16d)

O agente pede uma chave pelo nome, o usuário cola num cartão mascarado do app, e a chave vai
direto para o cofre: o modelo recebe só "guardada". Pedido do usuário em 14/10. A especificação do
MCP proíbe pedir chave por formulário (elicitation) e manda usar um link externo: o mesmo princípio
de a chave não passar pela conversa.

- **A ferramenta `secrets`**, com duas ações:
  - `request`: o nome no formato de variável (`^[A-Z][A-Z0-9_]{0,127}$`, que cabe no `${NOME}` do
    `mcp.json`) e um motivo de até 200 caracteres. O turno espera até 15 minutos, porque criar a chave
    no site do provedor leva tempo;
  - `list`: os nomes do cofre com a data de cada um, sem valor (o cofre lista sem decifrar);
  - nenhuma ação recebe um valor do modelo;
  - **sem `delete`:** o cofre do Atlas apaga de vez, sem lixeira. Um agente enganado por uma página
    apagaria a chave sem volta. Apagar fica no `atlas secrets delete`;
  - fica atrás da `tool_search`, como o `plan` (Ferramentas, acima). As mensagens que dizem que
    falta uma chave (servidor MCP de fora, `web_search` sem motor, plugin) passam a citar a
    ferramenta, para o modelo não pedir a chave no chat (sem a ferramenta, o modelo pede para
    colar o token na conversa).
- **O pedido `secret` do servidor ao app**, ao lado do `ask_user` e da aprovação. O cartão mostra:
  - o nome, o motivo, a conversa e o bot que pediu. Os bots dividem o cofre, então a chave pedida
    por um vale para todos;
  - "já existe uma chave com esse nome, gravada em 12/10; colar substitui", quando existe;
  - o campo mascarado com botão de olho, sem corretor nem preenchimento automático, e Pular;
  - aparece em toda tela, como os cartões da bancada, e avisa pelo sistema quando a janela não
    está na frente.
- **A chave não passa pelo modelo.** Ao receber a resposta, o servidor registra o valor no
  `Redactor`, grava no cofre exatamente como colado e devolve à ferramenta só
  `{"status": "stored", "name": ...}`. O valor fica fora do resultado da ferramenta, da conversa no
  banco, da volta do app sem perder evento, dos logs e do estado do app, que limpa o campo ao
  fechar o cartão.
  - A chave de um provedor passa antes pela conferência de chave, como na tela de chave: se o
    provedor recusa, o cartão continua aberto com a resposta dele.
- **Quem usa a chave é reiniciado depois de gravar** (a chave nova só vale depois de reiniciar):
  o plugin que a declara (o `plugins.secret` já faz isso) e o servidor MCP
  que não subiu por falta dela.
- **Pelo Telegram, nunca.** O canal não responde o pedido. O chat recebe: "O Atlas pediu a chave
  X para <motivo>. Cole no app do Atlas, no cartão que apareceu. Não cole aqui." O Atlas não tem
  página web, então o cartão fica no app.
  - Uma chave colada mesmo assim no chat vira mensagem comum e entra na conversa. A defesa é o
    aviso.
- **Sem app conectado, a resposta é `no_answer` na hora**, com o caminho `atlas secrets set NOME`.
  É a regra do `ask_user`.
- **O que o modelo sabe fica só na descrição da ferramenta**, sem regra no prompt do sistema. Uma
  regra geral contra credencial em argumento de ferramenta quebraria logins que o dono autorizou. A
  descrição diz:
  - quem usa a chave (para o agente não pedir, por exemplo, senha de sudo, que os comandos não
    usam): os provedores, a `web_search`, os servidores MCP que a citam no `mcp.json` e os
    plugins que a declaram;
  - que `no_answer` é para contar o bloqueio ou seguir, nunca para pedir a chave no chat.
- **Quem recebe a ferramenta:** as conversas com alguém para responder, como no `ask_user`.
  Subagentes, rotinas e a revisão não recebem.
- **Decidido pelo usuário em 14/10: os comandos do `run_command` não recebem a chave.** Fica a
  regra da arquitetura: nenhum processo filho herda segredo. Exemplo: "lista minhas cobranças do
  Stripe com curl" não funciona; funciona um MCP do Stripe com `${STRIPE_API_KEY}` no `mcp.json`.
  As alternativas que ficaram de fora:
  - a variável passada ao comando: o modelo pode rodar `echo $STRIPE_API_KEY` e ver
    a chave, e uma página maliciosa pode mandar ele fazer isso;
  - um proxy de saída que troca um marcador falso pela chave real só nos sites
    liberados: funciona, mas é uma peça grande, com certificados próprios. Só volta se a regra se
    mostrar curta no uso.
  - Por isso também não entra uma lista de sites liberados. No Atlas a chave só chega a
    quem o usuário configurou, e o portão não deixa o agente escrever `config.toml`, `mcp.json`
    nem `models.toml` (Confiança).
- **Ficam de fora:**
  - a chave pedida por uma skill que a declara: as skills do Atlas não declaram chave;
  - achar uma chave colada numa mensagem comum.
- **A prova** põe um teste em cada defeito possível:
  - de ponta a ponta, com o modelo falso: o turno chama a ferramenta e o Playwright cola uma chave
    falsa. A chave está no cofre e falta no banco, na volta do app, nos logs, na página depois de o
    cartão fechar e no resultado da ferramenta. O servidor MCP que precisava dela sobe;
  - o cartão aparece (senão a ferramenta espera calada até o prazo);
  - o pedido vai para a conversa certa, com dois apps;
  - uma gravação recusada, com o cofre trancado, diz que falhou, e não que guardou;
  - o `/stop` fecha o cartão (senão o pedido fica pendurado);
  - o chat do Telegram recebe o aviso, e o texto colado lá não responde o pedido;
  - um modelo de verdade, pedido para ligar um MCP sem chave, chama a ferramenta com nome e motivo
    certos e não pede a chave no chat. Custa poucas chamadas; perguntar antes de rodar.

### Casas no app (decididas em 15/10, passo 10c)

Pedido do usuário em 14/10: ver no app a casa em uso, criar uma casa, fazer backup e importar um
backup, sem o terminal: uma faixa de casas no pé da barra lateral, o diálogo de casa nova, a
exportação e a importação pelos diálogos do sistema e as rotas do servidor.

- **A casa aparece sempre**, no pé da barra lateral, à vista mesmo só com a `default`. Ao lado do
  nome, um "+" para Nova casa; embaixo, Backup e Importar. O seletor do passo
  10b, que só aparecia com duas casas, vai para lá.
- **Nova casa**, um diálogo:
  - o nome, com a regra de nome do servidor;
  - "Copiar de", que começa na `default` e oferece "Nenhuma (vazia)" e cada casa. Copiar é o
    `--clone`: configurações, modelos, MCP, as chaves, o perfil, a memória e as skills, com as
    conversas vazias;
  - criada, o app abre nela;
  - fica de fora um campo do `SOUL.md`, que o Atlas não tinha. (O Atlas ganhou o `SOUL.md` no
    passo 16e, também em 15/10: uma casa copiada leva o soul, e uma vazia nasce com o padrão. O
    campo no diálogo pode entrar depois.)
- **Backup:**
  - abre o diálogo de salvar do sistema, com o nome do `atlas backup`
    (`atlas-backup-<casa>-<hora UTC>.zip`) na pasta do usuário;
  - o servidor da casa escreve o zip no caminho escolhido: o app passa o caminho, não os bytes;
  - é o mesmo zip do terminal, sem as chaves, e o app diz onde ele ficou;
  - um arquivo que já existe só é substituído porque o usuário confirmou no diálogo, e sempre por
    um backup já conferido, porque o zip só ganha o nome no fim;
  - exportar a casa e fazer backup são uma coisa só desde o 10b.
- **Importar cria uma casa nova** (decidido pelo usuário em 15/10):
  - o diálogo de abrir do sistema, e o zip conferido inteiro antes de tudo;
  - depois, um diálogo que mostra a casa de origem, a data, a versão do Atlas e quantas conversas
    o backup tem, e pede o nome da casa nova;
  - o app abre na casa nova. As chaves não viajam, então ela abre na tela de chave;
  - **o nome é perguntado:** tirado só do arquivo, um nome que já existe daria erro. O backup mais
    comum é o da `default`, que sempre existe, então o app pergunta o nome. Sugere o da casa de
    origem, ou `<casa>-<data do backup>` quando ela já existe;
  - a casa nova é montada em `homes/<nome>.creating/` e só ganha o nome inteira, com o banco já
    trazido para esta versão. Uma importação que falha não deixa casa pela metade;
  - escrever por cima de uma casa continua só no terminal (`atlas restore`).
- **O app abre na última casa usada** (decidido pelo usuário em 15/10):
  - o app guarda o nome na pasta dele, e o terminal continua indo para a casa do `atlas home use`;
  - uma casa lembrada que não existe mais, apagada no terminal, dá lugar à do `atlas home use`;
  - muda a decisão de 11/10, em que o app abria a casa do `atlas home use`.
- **Num servidor remoto** (`ATLAS_DESKTOP_URL`, o container), os três botões ficam desligados e
  dizem o caminho do terminal, porque o caminho escolhido no diálogo seria de outra máquina
  (opção nossa, pequena). (Mudado em 19/10, no passo 15: o Backup
  funciona no remoto, com o zip descendo pela conexão; Várias máquinas, abaixo.)
- **Os métodos novos do servidor:**
  - `home.create`;
  - `home.backup`;
  - `home.inspect`, que confere o zip e dá o que o diálogo mostra;
  - `home.import`.
- **Ficam de fora:**
  - apagar e renomear casa pelo app, onde é fácil errar: uma casa apagada voltaria se o app
    reconectasse e o servidor recriasse a pasta;
  - a cor e o ícone de cada perfil, os atalhos de teclado e o aquecimento ao passar o mouse.
- **A prova:**
  - de ponta a ponta no Playwright, com o app real, o servidor real e o modelo falso, e o teste
    respondendo os diálogos do sistema;
  - os passos: a casa aparece com uma casa só; uma casa copiada é criada e conversa; o backup é
    salvo e conferido; o backup é importado como casa nova e mostra a conversa; o app fecha, abre
    de novo e volta na última casa;
  - depois, o usuário usa no app de verdade.

### SOUL.md (decidido em 15/10, passo 16e)

O Atlas principal ganha um `SOUL.md`, a identidade dele num arquivo do usuário. Pedido do usuário
em 15/10.

- **O arquivo** é `~/.atlas/SOUL.md`, na raiz da casa. Vai no backup e no `--clone`. O soul de um
  bot continua sendo o texto do `BOT.md` dele.
- **Nasce com o texto padrão** quando falta, e nunca é sobrescrito. O padrão é o `IDENTITY` de hoje.
  Quando o padrão mudar, um arquivo que ainda tiver o texto velho exato é atualizado (uma lista de
  textos velhos).
- **É a identidade.** Entra inteiro, no lugar do texto fixo e sem nada em volta. Vazio ou ausente,
  volta o padrão. Vale nas conversas, nos canais e nas rotinas; o subagente não recebe (a
  identidade é do pai).
- **É lido junto com a memória**, no começo da conversa e depois de cada compactação. A mudança vale
  a partir da próxima conversa e não quebra o cache no meio de uma.
- **Até 20 mil caracteres.** Acima disso, ficam os primeiros 70% e os últimos 20%, com um aviso no
  meio dizendo qual arquivo ler inteiro.
- **Texto suspeito avisa e carrega**, porque o arquivo é do usuário (quem citasse "ignore previous
  instructions" nas regras de segurança perderia a identidade inteira).
- **Quem escreve:**
  - o usuário, num editor ou na tela do app;
  - o agente, com as ferramentas de arquivo, quando o usuário pede, e sem pedir aprovação;
  - **só quando o usuário pede** (recomendado em 15/10; o usuário mandou seguir). Ficou de fora o
    arquivo dizer ao agente "This file is yours to evolve", e ele mudar sozinho e avisar. O usuário
    consegue esse jeito escrevendo essa frase no próprio `SOUL.md`;
  - a revisão em segundo plano nunca escreve nele;
  - uma memória ou skill que manda mudar o `.atlas/SOUL.md` é recusada.
- **Uma linha junto da pasta de trabalho diz onde fica o arquivo e dá a permissão**: "quando o
  usuário pedir para mudar quem você é ou como fala, edite esse arquivo, mantendo o resto, e diga
  que a mudança vale a partir da próxima conversa". Opção nossa, achada na prova real de 15/10:
  - só com o caminho, o gpt-6-luna recusou três vezes em três ("Não posso trocar essa regra"):
    uma instrução do prompt do sistema vale mais que o pedido do usuário. Com a permissão, editou
    só a linha pedida, avisou e manteve a regra antiga até a conversa seguinte, duas vezes em duas;
  - a permissão fica no prompt do sistema, com o "avise o usuário".
- **A tela no app** é um editor de `SOUL.md`: o texto, Salvar, o padrão à mostra
  quando o arquivo está vazio, e o aviso de que a mudança vale a partir da próxima conversa.
- **A prova:**
  - testes em arquivos de verdade: o arquivo nasce, não é sobrescrito, vazio volta ao padrão, é
    cortado, avisa de texto suspeito, vai no clone e no backup;
  - de ponta a ponta no Playwright, com o modelo falso: editar na tela e ver o texto novo no prompt
    da conversa seguinte, e não na que já estava aberta;
  - com modelo real (poucas chamadas no gpt-6-luna; perguntar antes): um traço escrito no soul
    aparece na conversa nova; e o agente, quando pedem para ele mudar o próprio jeito, edita o
    arquivo, e a conversa seguinte segue o texto novo.

### Aba Executores e painel lateral (decididos em 15/10, passo 16f)

Redesenho da tela do passo 16c, pedido pelo usuário em 15/10: o cartão da bancada fazia papel de
conversa, com a resposta espremida num painel estreito, e não deixava ver nem retomar uma sessão
antiga. O que o 16c fez por baixo continua valendo: o contrato, as instâncias, os pedidos sem prazo,
as duas chaves, o registro e o `delegate`.

Pesquisado em 15/10:
- o `/resume` e a extensão do VS Code do Claude Code, que mostram todas as sessões da pasta, e o
  `listSessions` do Agent SDK, que as lê;
- o app do Codex: os projetos na barra lateral, com as conversas de cada um embaixo;
- o `session/list` e o `session/load` do ACP, que o adaptador do Hermes já implementa.

**A aba Executores**
- **Uma terceira aba, ao lado de Conversations e Bots**, com um cartão por executor ligado e
  detectado, como os dos bots. O nome é o do glossário. "Agents" se confundiria com bots e
  subagentes.
- **Clicar num executor** mostra na lista da esquerda as pastas dele e as sessões de cada uma, a
  mais recente em cima (o app do Codex). "+ Nova sessão" fica no lugar de "New conversation".
- **Todas as sessões da pasta, de onde quer que tenham começado** (decidido pelo usuário em 15/10):
  no terminal, no VS Code ou pelo Atlas, como o `/resume` e a extensão do VS Code mostram. Cada
  plugin lista as do seu harness:
  - Claude Code: os JSONL de `~/.claude/projects/`, o que o `listSessions` do Agent SDK lê. A pasta
    real vem do `cwd` gravado na sessão, porque o nome da pasta do Claude Code não volta ao caminho;
  - Codex: o `thread/list` do `app-server`;
  - Hermes e DeepSeek: o `session/list` do ACP, onde o agente o oferece;
  - Pi: a conferir no plugin dele;
  - o título é o que o harness guardou, ou o começo do primeiro prompt, sem gastar crédito (Título
    sem crédito, acima).
- **O meio é a conversa da sessão**, com o visual da conversa do Atlas. Ela é montada da transcrição
  do harness (a `transcript` normalizada do 16c) e dos eventos ao vivo. Nada é copiado para o
  `atlas.db`, como no 16c.
- **Continuar uma sessão é começar uma instância sobre ela:** `--resume` no Claude Code,
  `thread/resume` no Codex, `session/load` no ACP. Onde o harness não retoma, a sessão abre só para
  leitura, e a caixa oferece uma sessão nova.
- **O que estava no cartão vai para os lugares de sempre:**
  - o campo de prompt vira a caixa de mensagem, que manda steer enquanto ele trabalha;
  - a conta, o automático do harness e o "Atlas responde" ficam embaixo, onde a conversa do Atlas
    tem modelo e esforço;
  - perguntas e permissões aparecem dentro da conversa, como as do Atlas;
  - o topo tem trocar de conta e parar. Duplicar e trocar de pasta viram "+ Nova sessão" em outra
    pasta.
- **Os comandos de barra** são os que o harness diz que o canal aceita: o Claude Code manda a lista
  no `init` (`slash_commands`), e o ACP no `available_commands_update`. A caixa os oferece ao
  digitar `/`. Os da tela do terminal (`/config`, `/login`) não passam pelo canal.
- **O estado fica na lista** (o app do Codex): rodando, esperando resposta. A aba ganha um aviso
  quando uma sessão espera resposta, e o aviso do sistema continua.
- **A delegação do Atlas** aparece na lista como sessão marcada "pelo Atlas", como as conversas
  "via telegram". Na conversa do Atlas, ela é a linha do `delegate`, com "abrir" levando à sessão (a
  linha do subagente no Claude Code). Isso substitui os cartões da bancada.
- **A mesma sessão aberta em dois lugares, a estudar no passo.** Exemplo: uma sessão aberta no VS
  Code e continuada pelo Atlas ao mesmo tempo; os dois escreveriam no mesmo arquivo. Ainda não foi
  visto como os outros tratam isso. A proposta, **opção nova**: avisar quando a sessão mudou há
  poucos minutos e oferecer continuar numa cópia (o `--fork-session` do Claude Code).

**O painel lateral**
- **O painel da direita fica, com outra função** (decidido pelo usuário em 15/10): o terminal e a
  prévia, em abas. A presença do navegador do passo 14 continua
  nele.
- **O terminal** abre na pasta da sessão ou da conversa aberta. É feito com o xterm.js e o
  `node-pty`. O `node-pty` é peça nativa, compilada para cada sistema, e pesa no 18b.
  - **Ele roda na máquina do servidor**, onde estão os arquivos do agente: na própria máquina, roda
    local; numa máquina remota, por SSH. No 16f, o terminal só aparece com o servidor na mesma
    máquina do app. O remoto e o container vêm com o passo 15.
- **O ▶ num bloco de shell da resposta** (pedido pelo usuário em 15/10, que escolheu rodar direto)
  abre o terminal na pasta da conversa, digita o comando e aperta Enter.
  - **Roda direto, sem esperar o Enter** (estudado em 15/10).
  - O usuário vê o comando inteiro no bloco antes de clicar. Um comando como `atlas secrets set`
    pede a chave escondida, o que só um terminal de verdade aceita: a ferramenta de terminal do
    agente não tem teclado.
  - O bloco de código já perdeu o "baixar" em 15/10: só copia.
  - O ▶ aparece só em bloco de shell (`powershell`, `bash`, `sh`, `cmd`, `zsh`).
  - Ele fica desligado enquanto a resposta ainda chega, porque um bloco pela metade rodaria cortado.
  - Num bloco de várias linhas, cada linha roda como um comando, como num colar.
- **O `!` na caixa de mensagem segue o mesmo caminho:** `!npm test` roda no terminal da conversa,
  sem passar pelo modelo.
- **O que o ▶ e o `!` têm em comum:**
  - **o comando usa a aba de terminal selecionada, se ela estiver livre**;
  - **o comando espera o terminal ficar pronto**, em vez de se perder. O comando fica guardado até o
    terminal abrir;
  - **a saída entra na conversa como uma linha de terminal**, também no meio de um turno, como a
    mensagem no meio do turno do passo 9. Ela passa pelo mesmo tratamento da saída do
    `run_command`: sem segredos, e guardada em arquivo quando é grande;
  - **o Atlas responde sozinho à saída** (decidido pelo usuário em 15/10). Cada ▶ ou `!` gasta uma
    chamada de modelo. Numa sessão de executor, a saída vai ao harness, e é ele quem responde;
  - **a conversa não fica marcada como "rodando"** depois que a saída chega;
  - **a estudar no passo:** como saber que o comando terminou, e o que acontece com
    um que não termina (`npm run dev`).
- **Comando perigoso pede confirmação antes do ▶** (decidido pelo usuário em 15/10; **opção nova**):
  - o app pergunta ao servidor, que usa a detecção de comando perigoso do passo 7;
  - um comando marcado aparece inteiro numa confirmação;
  - recusado, não roda;
  - o `!` não pergunta, porque o comando foi o usuário quem escreveu.
- **A prévia** mostra um arquivo, um endereço local (`localhost:3000`) ou um artefato (HTML,
  mermaid, Markdown), cada um numa aba:
  - **cada aba é da conversa que a abriu:** trocar de conversa troca as abas;
  - **o HTML roda num quadro isolado:** os scripts da página rodam, mas ela não alcança o app, o
    cofre nem o servidor. É o `sandbox="allow-scripts"` sem `allow-same-origin`, com uma lista de
    permitidos que barra também um `ALLOW-SAME-ORIGIN` em maiúsculas. Isso importa porque o
    HTML pode ter vindo de uma página que o agente leu.
- **Não abre sozinho** (decidido pelo usuário em 15/10):
  - um bloco de HTML, mermaid ou Markdown na resposta ganha "Abrir ao lado";
  - um arquivo gravado ou um site no ar vira um link pequeno acima da caixa de mensagem (uma pilha,
    até 4 por conversa);
  - só abre sozinho quando o agente pede para mostrar: o `desktop_preview`, que só existe com o app
    ligado. Fica atrás da busca, como o `browser_exec`, e nunca tira o foco de uma conversa em
    segundo plano.

**A ordem**, cada parte com um commit:
1. a aba com o Claude Code;
2. o terminal, com o ▶ e o `!` (vem antes da prévia, que não depende dele);
3. a prévia.

Os outros harnesses chegam com os plugins deles.

**A prova:**
- testes do plugin do Claude Code em JSONL de verdade: listar, inclusive uma sessão começada fora do
  Atlas, e retomar com `--resume` no Claude Code falso;
- de ponta a ponta no Playwright:
  - abrir a aba, escolher a pasta e abrir uma sessão antiga;
  - mandar um prompt e responder uma pergunta dentro da conversa;
  - um bloco de HTML só abre ao lado no clique;
  - um script da página não alcança o app;
  - o ▶ num bloco de PowerShell roda no terminal do painel, um bloco de Python não tem ▶, e um bloco
    ainda chegando não roda;
  - um ▶ clicado antes de o terminal ficar pronto roda assim mesmo;
  - a saída entra na conversa e o Atlas responde a ela, com o modelo falso;
  - a conversa não fica "rodando" depois disso;
  - um comando perigoso pede confirmação, e recusado não roda;
  - o `!` roda sem confirmação;
  - tudo isso no Windows, onde o terminal pode deixar de rodar;
- prova real com o Claude Code do usuário: uma sessão começada no terminal aparece na lista e
  continua pelo Atlas. Gasta só a assinatura, e o crédito do Atlas só com o "Atlas responde" ligado;
- prova real do ▶: um teste que falha e a resposta do Atlas dizendo por quê (poucas chamadas no
  gpt-6-luna; perguntar antes).

### O turno na tela (decidido em 15/10, passo 9c)

O turno aparece como uma trilha: uma linha vertical com um ponto para cada parte (raciocínio,
texto, ferramenta), na ordem em que aconteceu. Todo texto fica na tela, o do meio mais leve e a
resposta final com o peso cheio. Um clique numa ferramenta abre o que entrou (IN) e o que saiu
(OUT). Pedido do usuário em 15/10 e desenhado com ele num protótipo com seis jeitos de modelo. O nome
"trilha" deixa a "linha do tempo" para o plugin oficial que já tem esse nome
(acima).

- **O turno é uma lista de partes com tipo**, como no ACP. A ferramenta tem estado (pendente,
  rodando, terminou, falhou, recusada, cancelada) e guarda a entrada e a saída cruas ao lado do que
  se mostra. O que o app não conhece é guardado cru e mostrado de forma genérica, como o ACP v2
  manda, em vez de descartado calado ou de quebrar.
- **As partes do turno:** raciocínio, texto, ferramenta, e outra,
  para o que o app não conhece. O raciocínio vira parte própria; hoje ele divide a peça com o
  texto.
- **A trilha:**
  - um ponto por parte, ligados do primeiro ao último passo do turno. A mensagem do usuário fica
    fora da linha;
  - a cor do ponto é o estado: círculo vazio no raciocínio, cinza no texto do meio, cheio na
    resposta final, verde na ferramenta que terminou, vermelho na que falhou, cinza na recusada e
    na cancelada, e azul pulsando na que roda;
  - a ferramenta que termina bem não ganha marca, e a que roda mostra "rodando" com um cronômetro.
    No fim aparece o tempo que levou: o `tool.complete` passa a levar `duration_s`.
- **Todo texto é parte da resposta** (decidido pelo usuário em 15/10):
  - o texto de uma chamada que termina pedindo ferramenta é o texto do meio. O servidor avisa com
    o `message.interim`, no fim da chamada e antes do `tool.start`; o app não adivinha;
  - o texto do meio fica mais leve, e o último, a resposta final, fica com o peso cheio. Nada muda
    de lugar nem some;
  - o `message.complete` continua levando só a resposta final.
- **O raciocínio:**
  - fechado, numa linha: "Pensando…" com cronômetro enquanto chega, e "Pensou por 4 s" no fim. Ao
    reabrir, só "Pensou", porque o tempo não é guardado;
  - a mesma linha diz do que ele trata: o último título em negrito, ou as primeiras palavras.
    **Nova:** o usuário aprovou no protótipo;
  - aberto, o texto é lido como Markdown. Hoje os títulos em negrito do Gemini e do GPT
    aparecem com os asteriscos;
  - raciocínio sem texto legível (cifrado, ou só a contagem de tokens) não aparece.
- **A ferramenta:**
  - fechada, numa linha: o nome e os argumentos resumidos. As ferramentas do Atlas ganham título
    próprio aos poucos (`run_command` mostra o comando, `read_file` o caminho,
    `web_search` a busca). As de MCP e de plugin ficam no resumo de hoje: os valores, sem as
    chaves;
  - aberta, uma caixa com IN e OUT. No IN, um argumento só aparece puro; vários
    aparecem com o nome em cima de cada valor. No OUT, a saída enquanto roda (`tool.progress`) e o
    resultado no fim, em vermelho quando falhou;
  - a saída longa é cortada em 20 mil caracteres, com um botão de copiar tudo. O
    resultado que foi para o disco mostra o caminho, como hoje;
  - o `failed` do `tool.complete` vira `status`: `completed`, `failed`, `denied` (recusada na
    aprovação) e `cancelled` (o turno parou). Hoje uma recusa e um turno parado aparecem como falha.
- **O que o app não conhece aparece** (ACP v2):
  - um evento de tipo desconhecido vira uma parte "outra": ponto cinza, o nome do evento e o
    conteúdo em JSON, fechado. Acontece quando o servidor é mais novo que o app, como num
    container atualizado. Um campo desconhecido é ignorado;
  - um teste do TypeScript falha quando um evento do protocolo não tem desenho, para a parte
    genérica não esconder um esquecimento nosso.
- **A diferença entre provedores fica no adaptador** (o que é de cada provedor fica num campo à
  parte):
  - a tela só conhece os eventos do Atlas. Provedor novo, ou saída nova de um provedor, muda o
    adaptador;
  - o `<think>` no meio do texto sai no adaptador `chat`, por um limpador de `<think>`: nove
    marcações (`think`, `thinking`, `reasoning`, `thought`, `REASONING_SCRATCHPAD` e quatro
    chinesas), sem diferença de maiúscula. Uma marcação partida entre dois pedaços fica retida até
    completar, e só abre bloco no começo de uma linha, para uma frase que cita `<think>` passar
    intacta. A de fechar sem a de abrir é tirada. O que sai vira raciocínio, e a conversa é gravada
    limpa;
  - a decidir: o que fazer com uma marcação que nunca fecha (descartar o texto retido no fim da
    resposta, por exemplo).
- **Reabrir mostra o mesmo que ao vivo:**
  - o banco já guarda o raciocínio, os argumentos de cada ferramenta e o modelo (`codec.py`). O
    `MessageRow` passa a levar o raciocínio e as chamadas (id, nome e argumentos), e a linha da
    ferramenta, o id da chamada e o estado;
  - o resultado da ferramenta passa a gravar o estado, em vez de deduzir a falha do texto: o
    estado já vem no evento, e gravar custa um campo no corpo da mensagem, sem migração;
  - só os tempos ficam de fora ao reabrir.
- **O Telegram recebe os textos do meio** (decidido pelo usuário em 15/10):
  - cada `message.interim` vai como uma mensagem, na hora, e o "digitando" continua até a resposta
    final;
  - o progresso das ferramentas não vai, nem o raciocínio;
  - sem configuração por canal: desligar os textos do meio só faria sentido em canais mais
    pobres, que o Atlas não tem.
- **Ficam de fora:**
  - o resumo das chamadas seguidas numa frase: o usuário escolheu a linha com um ponto por passo;
  - as vistas próprias de cada ferramenta (o diff do `edit_file`, a saída e o erro do terminal
    separados, a lista de resultados da busca). Entram uma por vez, quando o uso pedir;
  - o tipo de ferramenta do ACP (`read`, `edit`, `execute`…), que serve para escolher ícone;
  - guardar os tempos.
- **A prova:**
  - os seis jeitos de modelo do protótipo (narra, calado, repete, conteúdo no meio, pensa em voz
    alta, vaza `<think>`) viram roteiros do modelo falso. O Vitest confere as partes, os estados e
    o texto do meio de cada um;
  - de ponta a ponta no Playwright, com o modelo falso: um turno com raciocínio, texto do meio, uma
    ferramenta que falha e uma recusada. A trilha ao vivo e a mesma conversa reaberta saem iguais,
    fora os tempos;
  - um evento inventado aparece como parte genérica;
  - o limpador de `<think>` tem os testes dele;
  - o Telegram recebe os textos do meio na ordem, antes da resposta final, e nada do raciocínio;
  - **gravações de verdade:** a mesma tarefa curta, de duas ou três ferramentas, roda na
    Anthropic, na OpenAI, no Gemini, no DeepSeek pelo OpenRouter e num modelo local que pensa em
    `<think>` pelo Ollama, e o que cada provedor mandou é gravado. Nos testes, cada gravação passa
    pelo adaptador e pela trilha: nenhuma parte genérica, nenhum `<think>` no texto, e reabrir
    igual. Com modelo novo, basta gravar e rodar. Custa poucos centavos por provedor (o Gemini
    gratuito e o Ollama, nada); perguntar antes;
  - o usuário usa no app e no Telegram de verdade.

### Anexos na conversa (decididos em 16/10, passo 16g)

Arrastar e soltar (ou colar) arquivos de qualquer formato na caixa de mensagem, e o agente
entender cada um do jeito mais barato que o modelo da conversa permite. Pedido do usuário em 16/10.
Hoje o Atlas não recebe arquivo nenhum pela conversa: o `send` só
leva texto, e a mensagem do usuário só guarda texto.

- **O arquivo sempre vai para a casa.** Fica em `attachments/`, e a mensagem aponta para
  ele. Um caminho do computador do usuário não existe no container nem em outra máquina.
  - **Como chega ao servidor:** começa a ir assim que é solto, antes de a mensagem sair,
    sempre pelo conteúdo, em partes de 4 MiB (`attachment.add`), porque
    uma mensagem do WebSocket leva no máximo 16 MiB. Opção nossa, mudada ao construir: mandar
    o caminho quando o servidor enxerga o disco pouparia ler duas vezes um arquivo grande, mas um
    jeito só para todo servidor (local, container, outra máquina) é mais simples de provar.
  - **O tipo** vem dos primeiros bytes quando eles dizem (PNG, JPEG, GIF, WebP, PDF), senão do nome.
  - **Vai no backup** (`user`): uma conversa restaurada ainda abre os arquivos dela. Opção nossa,
    em vez de um `cache/` da casa.
- **Imagem, decidida pelo usuário em 16/10:**
  - o modelo vê imagem: vai direto ao modelo, como imagem. O Atlas já faz isso com a
    imagem do `read_file`;
  - o modelo não vê imagem: um modelo de apoio descreve a imagem antes do turno, e a descrição
    entra no lugar, com o caminho do arquivo. O pedido de descrição tem uma linha de sistema que
    mantém a resposta uma descrição. O modelo de apoio é o dos títulos (Claude Haiku 5.5, senão
    GPT-6 Luna, os dois veem imagem), com a quarentena deles. Opção nossa, em vez de um modelo de
    visão na configuração;
  - a descrição fica guardada com o anexo: é feita uma vez, quando a mensagem é enviada. Uma
    mensagem que corrige o rumo de um turno em curso não é descrita;
  - sem modelo de apoio, ou quando ele falha: só a nota com o caminho, dizendo que a imagem não
    pôde ser vista;
  - imagem grande é reduzida no app antes de sair (2048 px no lado maior).
    Fica abaixo dos 5 MB que a Anthropic aceita. PNG continua PNG quando cabe; o resto vira JPEG;
    GIF vai como está.
- **Documento, decidido pelo usuário em 16/10: aceito com qualquer modelo.**
  - Texto de até 100 KB (`.txt`, `.md`, `.csv`, `.json` e os outros de uma lista) entra
    inteiro na mensagem, marcado como conteúdo de fora (o `untrusted` do Atlas).
  - O resto (PDF, Office, OpenDocument, RTF, EPUB, texto grande) entra como uma nota:
    nome, tipo e caminho, e "leia você mesmo antes de responder, em vez de pedir ao usuário que
    cole o conteúdo". O modelo lê com o `read_file`, que já converte para Markdown e pagina:
    o Atlas tem o conversor, sem extrair pelo terminal.
  - PDF nunca vai inteiro ao modelo neste passo.
- **Vídeo:** vai direto quando o modelo declara vídeo no catálogo; senão, a nota com o
  caminho. **Áudio:** a nota com o caminho. Transcrever fica para depois.
- **O que deu errado vira aviso para o modelo**, nunca silêncio: tipo não aceito,
  arquivo que não abriu, documento sem texto. No máximo cinco avisos por mensagem; o resto vira
  "mais N anexos pulados".
- **Desacoplado:**
  - o que o modelo aceita vem do catálogo (`input_modalities`, que já existe);
  - uma peça só no servidor decide, para cada anexo, se ele vai direto, como descrição ou como
    nota. O app só mostra e envia; os arquivos de um canal usam a mesma peça, um ponto
    só para todos os canais;
  - **a mensagem guarda o texto do usuário e os anexos separados.** Com a nota escrita dentro do
    texto guardado, o app precisaria de expressões regulares para tirá-la de volta. Aqui a nota é
    montada na hora de chamar o modelo;
  - trocar para um modelo sem visão no meio da conversa transforma as imagens antigas em nota,
    como já acontece com a imagem de uma ferramenta.
- **Limites:** imagem de até 5 MB depois de reduzida (o limite do `read_file`, o da Anthropic);
  documento e vídeo de até 50 MB, áudio de até 20 MB. Acima disso, o app recusa
  ao soltar e diz por quê.
- **A tela:** o arquivo solto na caixa, colado nela ou escolhido no 📎 vira uma
  ficha acima dela, na ordem em que chegou, quebrando em mais
  linhas: nome, tamanho ou quanto já foi, um × ao passar o mouse.
  - **Cada formato com a sua marca e a sua cor** (pedido do usuário em 16/10): o quadrado da ficha
    mostra o formato em poucas letras (PDF, DOCX, XLSX) na cor da família: PDF vermelho, texto de
    escritório azul, dados verde, apresentação laranja, e-book verde-azulado, texto cinza, código
    violeta, vídeo fúcsia, áudio rosa, compactado âmbar. A imagem mostra a miniatura, com o
    formato num canto.
  - Enviar espera todas as fichas chegarem. Na conversa, as fichas ficam acima da mensagem do
    usuário, e reabrir a conversa mostra as mesmas. Clicar abre o arquivo no painel da direita
    (16f), quando o servidor está nesta máquina.
- **Fica de fora:** o PDF inteiro ao modelo e a página escaneada vista como imagem;
  transcrever áudio (vai ser um plugin de voz próprio); arquivo por URL.
- **Arquivos pelo Telegram** (pedido do usuário em 16/10, feito no mesmo dia): o SDK de plugins
  vai para a versão 7, só acrescentando a lista de arquivos da mensagem (`Inbound.files`: nome e
  conteúdo, nada do Telegram), e o servidor guarda cada um em `attachments/`, como os do app. O
  plugin do Telegram baixa foto, documento, vídeo, áudio e voz pelo `getFile`, com a legenda como
  texto; acima dos 20 MB que o Telegram deixa um bot baixar, avisa na conversa. O manifesto dele
  fica em `api: 1`, para o consentimento já dado continuar valendo. A voz chega como nota até o
  plugin de voz existir.
- **A prova:**
  - testes da peça do servidor com arquivos de verdade: cada tipo (imagem, PDF, DOCX, texto
    pequeno, texto grande, vídeo, áudio, desconhecido) com um modelo que vê imagem e com um que
    não vê;
  - a mensagem com anexos guardada e lida de volta; a imagem na mensagem do usuário nos três
    formatos de pedido (Chat Completions, Responses, Messages);
  - de ponta a ponta no Playwright, com o modelo falso: soltar uma imagem e um PDF, ver as fichas,
    mandar, conferir o que o modelo recebeu, reabrir a conversa. Todo servidor recebe o mesmo
    conteúdo, então o container não tem teste próprio;
  - com modelo real (poucas chamadas; perguntar antes): um PDF e uma imagem num modelo que vê
    imagem, e a mesma imagem num modelo só de texto, respondendo pela descrição.

---

### Várias contas ao mesmo tempo (decidido em 18/10, refeito em 19/10, passo 16h)

O que o usuário pediu em 18/10, em três partes: o mesmo executor rodando em duas contas ao mesmo
tempo, cada projeto na sua conta, e a sessão que troca de conta e segue no mesmo projeto. Em 19/10
ele acrescentou a quarta, que mudou o desenho: **cada conta com a sua configuração e o seu
histórico intactos**.

- **O caso do usuário** (19/10): contas dele e contas de empresas diferentes, uma para os projetos
  pessoais, outra para o projeto A, outra para o projeto B. No normal, cada conta fica no seu mundo.
  Às vezes, terminando o projeto pessoal, o crédito da conta pessoal acaba e ele troca para a conta
  do projeto B para continuar a mesma conversa. A troca é a exceção.
- **Por que o desenho de 18/10 saiu.** Nele, todas as contas usavam o `~/.claude` do usuário: a
  conta da empresa B leria o `CLAUDE.md`, os MCPs e os plugins pessoais, e as conversas dos projetos
  A e B ficariam numa lista só. E o token de cada conta extra ficava no cofre do Atlas, perto do
  texto da Anthropic que proíbe terceiros de "coletar, guardar ou intermediar credenciais ou tokens
  de sessão do claude.ai" (página Legal and compliance do Claude Code). A prova de 18/10 continua
  valendo para o que provou: uma conversa segue em outra conta e lembra o que ouviu.
- **Uma pasta por conta** (o jeito da documentação do Claude Code, "Log in with multiple accounts";
  o `CODEX_HOME` do Codex é o mesmo). A conta principal é a pasta de hoje, o `~/.claude` do usuário
  (ou o `CLAUDE_CONFIG_DIR` dele), que o Atlas nunca muda. Cada outra conta é uma pasta ao lado,
  `~/.claude-<apelido>` (ex.: `C:\Users\<usuário>\.claude-empresa-b`), com as suas configurações,
  plugins, MCPs, `CLAUDE.md` e conversas. Ficam fora do `~/.atlas` porque são do Claude Code:
  sobrevivem a desinstalar o Atlas, valem para todas as casas e o usuário pode usar no terminal
  (`CLAUDE_CONFIG_DIR=... claude`). O Atlas passa sempre o caminho absoluto escrito de um jeito só
  (#92252 do Claude Code: uma barra a mais ou um `~` escolhem outra pasta e parece que o login
  sumiu).
- **O login é sempre o do próprio Claude Code.** "Adicionar conta" pede o apelido, cria a pasta
  vazia (ou aceita uma `~/.claude-*` que já exista) e roda `claude auth login --claudeai` com o
  `CLAUDE_CONFIG_DIR` dela, que abre a página da Anthropic no navegador. O Atlas nunca vê, guarda ou
  passa credencial: é a exceção que a Anthropic escreve ("o binário do Claude Code sem modificação,
  com a assinatura do próprio usuário"). Como o login é completo, cada conta tem os conectores do
  claude.ai, o Remote Control e o uso do plano (`get_usage`), que o token de 18/10 não tinha.
- **Na tela, cada conta é um executor** (o "duplicar o executor" de 18/10): "Claude Code ·
  pessoal", "Claude Code · empresa B", cada um com as pastas e as sessões lidas da pasta dele, o seu
  uso do plano, modelos, conectores e comandos. A conta da pasta continua (a conta em que as sessões
  novas de um projeto começam), e a delegação do Atlas segue a mesma regra: a pedida, senão a da
  pasta, senão a principal.
- **Trocar no meio da conversa copia a conversa** (escolha do usuário em 19/10, como a comunidade
  faz). Exemplo: "terminar o app", na conta pessoal; o crédito acaba; o usuário troca para a
  empresa B. O Atlas para a instância e abre a conversa na conta B com
  `claude --resume <caminho do arquivo na conta pessoal> --fork-session`: o próprio Claude Code lê o
  arquivo da conta pessoal e grava uma sessão nova, com número novo, na pasta da conta B (as duas
  opções são documentadas; é o mesmo `/branch` do Claude Code, que deixa o original intacto). O
  arquivo da conta pessoal não muda. Voltar para a pessoal faz o mesmo no outro sentido. No Atlas o
  card é um só e mostra a conversa seguida; cada turno guarda a sessão em que rodou, que é da conta. Se a
  prova mostrar que o `--fork-session` grava na pasta errada, o Atlas copia o arquivo ele mesmo para
  a pasta da conta B e retoma de lá, sem mexer no conteúdo.
- **Antes de trocar, o app diz o que muda:** a conta que vai receber a conversa; as ferramentas que
  a conta de destino não tem (ex.: "a conta empresa B não tem: Figma"), comparando os conectores e
  os MCPs das duas; e que a primeira mensagem relê a conversa inteira no plano da outra conta (o
  cache é por conta).
- **A cópia fica na conta de destino.** Quem decide é o usuário, por sessão: **arquivar** tira a
  sessão da lista sem tocar em nada, como a pasta arquivada de hoje, e ela volta pelo "Arquivadas";
  **apagar** pergunta antes e apaga, só naquela conta, os arquivos da sessão que o `claude purge` do
  Claude Code apaga de um projeto
  inteiro: a conversa (`projects/<projeto>/<id>.jsonl` e a pasta `<id>/`), o `file-history/<id>`,
  o `tasks/<id>` e o `debug` dela. Nunca com ela rodando. Se um card do Atlas estiver nela, ele
  fecha. As outras contas não mudam. **Peça nova:** o Claude Code apaga só o projeto inteiro
  (`claude purge`); apagar uma sessão do Claude Code é nosso, com a lista do `purge`.
- **O SDK dos plugins vai para a versão 12:** sai a credencial de 18/10 (`Capabilities.credentials`,
  `ExecutorSpec.credential`, `credential_recipe`) e entra a pasta da conta: o plugin diz a pasta
  dele, onde fica a de uma conta nova e faz o mesmo harness em outra pasta (`for_account`), que ele
  entrega na variável dele (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`); mais retomar de outra conta
  (`ExecutorSpec.resume_from`) e apagar uma sessão. No núcleo, **a conta é um executor a mais**,
  `claude-code@<id>`: a aba, a bancada, o uso do plano e a delegação já separam uma conta da outra
  sem peça nova. O núcleo guarda as contas (apelido, pasta) em `jobs_executor_accounts` e a conta de
  cada pasta em `jobs_folder_accounts`; o cofre não guarda mais nada de conta. Uma conta extra de
  18/10 que exista sai na migração, e o app pede para adicioná-la de novo.

**Riscos, cada um com o passo da prova que o pega:**

1. **A conversa troca de conta, as ferramentas não:** na conta B ela vê chamadas a um MCP que a B
   não tem. O aviso antes da troca resolve; a prova troca para uma conta sem um MCP da outra.
2. **A separação não é completa no Claude Code de hoje:** ele ainda lê o `CLAUDE.md` e as regras do
   `~/.claude` principal com outra pasta (#80580), e um `/plugin install` dentro da conta B pode
   instalar na principal (#74393). A prova olha os dois na versão atual e o que ainda vazar fica
   escrito.
3. **Na conta de destino, desfazer edições de antes da troca não funciona:** o `file-history` e as
   tarefas não vão junto. A prova registra o que a conta B consegue desfazer.
4. **A primeira mensagem depois da troca é cara:** relê a conversa inteira no plano da outra conta.
   O aviso diz; a prova mede o uso do plano antes e depois.
5. **O formato dos arquivos é interno do Claude Code** e muda entre versões: o Atlas só passa o
   caminho (ou copia o arquivo inteiro) e nunca lê nem muda o conteúdo para trocar.
6. **Conta de empresa:** o conteúdo do projeto pessoal passa a ir pela conta da empresa (planos Team
   e Enterprise podem ter regras e visibilidade sobre isso). O aviso nomeia a conta.
7. **Trocar de conta quando o limite acaba** é o padrão que os termos da Anthropic tratam como
   arriscado ("várias contas para contornar restrições"). O Atlas troca só quando o usuário pede,
   nunca sozinho.
8. **Duas instâncias na mesma conta renovando o login ao mesmo tempo** podem derrubá-lo (#103325 do
   Claude Code). Uma pasta por conta diminui; a prova roda duas contas ao mesmo tempo e confere que
   nenhuma caiu.

**A prova real** (com as contas do usuário, alguns prompts de cada; pedir antes): duas contas
logadas ao mesmo tempo, cada uma com o seu uso; uma pasta começando na sua conta; a troca no meio
da conversa lembrando uma palavra, com o arquivo da conta de origem igual byte a byte e uma sessão
nova na de destino; a volta; o aviso de ferramenta que falta; os vazamentos do item 2; arquivar e
apagar na conta de destino, com a de origem intacta.

---

### Rota da sessão entre harnesses (pedida e feita em 21/10, sem passo)

Pedida pelo usuário em 21/10, depois da troca automática de conta do mesmo dia (cada executor com
a ordem das suas contas, que continua valendo): **uma rota por sessão**, em que o usuário escolhe
quais destinos participam e em que ordem, misturando contas de harnesses diferentes. Exemplo:
Claude Code · principal → Claude Code · segunda conta → Codex · principal.

- **Decidido com o usuário em 21/10:** a rota é da sessão, não global por executor; cada destino é
  harness + conta; uma sessão do Claude Code não se retoma como a mesma sessão no Codex, nem o
  contrário, então ao atravessar de harness começa uma sessão nova no destino; a passagem leva um
  pacote de transferência montado sem chamada de modelo, e a primeira chamada ao destino é a
  normal, que continua o trabalho. Compactar o pacote com um modelo fica para depois, se algum
  limite de tamanho pedir.
- **Feito em 21/10** (docs/ARCHITECTURE.md, A session's route across executors): a rota fica
  guardada para o card da sessão (`jobs_session_routes`, migração 10) e vale também para a sessão
  reaberta pela lista; no limite do plano, o próximo destino com login e fora do próprio limite
  assume. Na mesma conta do mesmo harness não muda nada; noutra conta do mesmo harness, a cópia de
  sempre; noutro harness, uma sessão nova no mesmo card, cujo primeiro prompt é o pacote: o
  motivo, a pasta, a tarefa e a última instrução, o plano por fazer e as perguntas sem resposta,
  os arquivos mudados e anexados, e os passos da sessão (os mais novos primeiro, até 40 mil
  caracteres), lidos da transcrição do próprio harness ou, sem ela, do registro do Atlas. No app,
  o botão "Rota" acima da caixa da sessão abre o editor: quem participa, a ordem e se vale.
- **Provado** nos testes com o Claude Code e o Codex falsos por pipes de verdade (rota dentro do
  mesmo harness, rota entre harnesses, conta sem login pulada, Codex no limite e nenhum destino
  sobrando), nos do pacote, no do servidor pelo socket e no Vitest. **Falta** a prova real com as
  contas do usuário e o uso dele no app.
- **A prova real** (`scripts/prove_route.py`, ensaiada nos falsos em `tests/test_prove_route.py`)
  junta a da rota, a da troca automática de conta e a troca do 16h. Decidido com o usuário em
  21/10: **não esgota limite de propósito**. Espera uma conta do Claude Code que já esteja no
  limite, onde o turno para na hora sem gastar nada, e usa uma com folga para dois turnos curtos
  por passo. O passo 1 só lê, sem prompt: quem está logado, o uso, o uso extra e quem está no
  limite, e para onde uma rota iria com esse uso. Os passos 2 a 4 (rota dentro do Claude Code
  com a troca do 16h, troca automática, rota para o Codex) só rodam com `--yes`, e recusam conta
  com uso extra ligado ou que não diga. Pior caso em dinheiro: zero de crédito do Atlas (nenhuma
  chamada de modelo); nas assinaturas, uns 4 a 6 turnos curtos na conta com folga e um turno curto
  do Codex (2 a 4% do limite de 30 dias do plano `free`). O passo 4 espera o Codex ter folga (em
  21/10 ele estava sem crédito no dia, o que o passo 1 prova pulando-o).
- **Prova real em 21/10, passos 1 a 3 ✅** (o 4 espera o limite de 30 dias do Codex voltar). Com as
  contas do usuário: a conta principal no limite semanal (100%) e cinco contas logadas, todas com
  o uso extra desligado. O passo 1, só lendo, viu o Codex em 100% do limite de 30 dias e mandou a
  rota da principal para a primeira conta com folga, pulando o Codex. Nos passos 2 e 3, a sessão
  aprendeu uma palavra na conta com folga e foi trocada para a principal, numa cópia. O prompt
  seguinte parou no limite na hora, sem gastar nada da principal. A sessão continuou numa cópia
  na conta com folga, pela rota e pela troca automática, e gravou a palavra num arquivo. O arquivo
  da sessão original ficou igual byte a byte. Gasto: uns 6 turnos curtos na conta com folga
  (a sessão de 5 h dela foi de 64% a 65%) e nenhum crédito do Atlas. **Achado na prova:** o
  próprio roteiro media o arquivo antes de o Claude Code terminar de gravá-lo (ele grava um
  instante depois de falar); agora mede depois da troca, com o processo parado. As sessões da
  prova foram apagadas; a pasta de projeto vazia que o Claude Code deixa para a pasta temporária
  foi apagada à mão.
- **No Linux** (o CI parado; rodados daqui na `atlas-gates:node`, sobre o `main` publicado, em
  21/10): os cinco fiscais do Python limpos, 2.385 testes passando com 90,96% de cobertura, e os
  323 do Vitest. Falham só os 2 testes do `install.sh` de sempre: a imagem não tem `git`, que o
  runner do GitHub tem. O Playwright no Linux não rodou desta vez; no Windows passaram os 62.
- **Mais da 16h na mesma prova (21/10):** o passo 2 apaga a cópia na conta no limite e confere
  que a original ficou; o passo 5, sem prompt, abre uma pasta na conta dela e lê o aviso da troca
  (os conectores que a outra conta não tem); o passo 6, num turno curto, pergunta a uma conta sem
  `CLAUDE.md` próprio a memória de usuário que ela recebeu e compara com a do `~/.claude`
  principal, sem imprimir nada dela. Passaram os três. Nesta rodada a sessão de 5 h da conta com
  folga foi de 65% a 97%: os três turnos curtos da prova não explicam isso sozinhos, então a conta
  provavelmente estava em uso por outro lado ao mesmo tempo.
- **Uso extra no painel** (SDK versão 19, `PlanUsage.extra_usage`): o painel avisa em âmbar
  quando, passando dos limites, a conta continua e cobra (o uso extra do Claude, os créditos do
  Codex), porque ela nunca para para a próxima conta nem para a rota.
- **Achado pelo ensaio e consertado:** a troca automática de conta (`b748cef`) quebrava ao ler o
  reset de uma janela do Codex (ISO 8601 sem fração de segundo), justamente quando a conta estava
  cheia.
- **Ficou de fora, por ora:** a sessão ainda não começada não tem card, então a rota se escolhe a partir do primeiro prompt; e a
  tela da sessão, depois de atravessar, mostra a sessão nova do destino, que começa pelo pacote (a
  conversa do card no Atlas guarda as duas).

### Conta e rota na delegação do Atlas (pedidas e feitas em 21/10, sem passo)

O usuário perguntou em 21/10 se o Atlas conseguia delegar para qualquer conta de qualquer harness,
trocar de conta e criar rota de contas. Não conseguia: a delegação rodava na conta da pasta ou na
principal, no limite seguia só a ordem das contas do executor, e o `action='account'` refazia o
login do harness (o desenho de 12/10, de antes de uma pasta por conta). Pedido: fazer tudo.

- **A conta vai no nome do executor** (docs/ARCHITECTURE.md, A delegation's account and route): o
  `executor` do `delegate` aceita `claude-code@trabalho` (o apelido, ou o id) e `claude-code@main`;
  sozinho, segue a conta da pasta, senão a principal, como antes. A lista do `delegate` nomeia
  cada conta de um executor que tem mais de uma.
- **A rota da tarefa** é o `route` do `delegate`, os mesmos nomes em ordem, misturando harnesses
  (`["claude-code@trabalho", "codex"]`). Fica guardada como a do card (`jobs_session_routes`, pela
  conversa da tarefa), e vale também para a tarefa retomada. No limite: outra conta do mesmo
  harness segue numa cópia; outro harness começa uma sessão nova com o pacote de transferência do
  workbench. Sem rota, a ordem das contas do executor continua valendo.
- **Trocar de conta é retomar em outro lugar:** `action='resume'` com `executor` noutra conta
  segue numa cópia; noutro harness, numa sessão nova a partir do pacote.
- **`executor_accounts`**, ferramenta nova, adiada e só de leitura: cada conta pelo nome que o
  `delegate` aceita, quem está logado, o uso do plano por janela e quando volta, se está no limite
  ou se cobra uso extra, as pastas que começam nela e a ordem da troca automática. Sem chamada de
  modelo.
- **O login continua sendo pedido do usuário:** o `action='account'` faz o login da conta que o
  `executor` nomeia, e recusa um e-mail que outra conta já tem (aí basta delegar pelo nome dela).
- **Custo:** com um executor instalado, o `delegate` passou de 258 para 329 tokens a mais pela
  estimativa local.
- **Provado** nos testes da ferramenta e nos da delegação com o Claude Code e o Codex falsos por
  pipes de verdade: a conta nomeada vencendo a da pasta, conta desconhecida recusada, a rota
  atravessando para o Codex com o pacote, a tarefa parada retomada noutra conta (cópia) e depois
  no Codex, e o painel das contas. **Falta** a prova real com as contas do usuário: uma delegação
  numa conta nomeada e uma atravessando para o Codex (junto do passo 4 do `prove_route.py`, que
  espera o limite do Codex voltar).

### Arquivos na sessão do executor (pedido em 19/10, passo 16i)

Pedido do usuário em 19/10: a caixa da sessão de um executor não aceitava foto nem arquivo, como a
do Atlas e a dos bots aceitam. Feito de forma limpa, reaproveitando o que já existe e usando o que
o SDK oficial dá, sem inventar (docs/ARCHITECTURE.md, Attachments, "An executor's files"):

- **O mesmo envio do 16g:** as mesmas fichas da caixa, o mesmo `attachment.add` em partes, o mesmo
  `attachments/` da casa. A caixa da sessão só aceita arquivo quando o executor diz que aceita
  (`Capabilities.files`, `ExecutorRow.files`); o `workbench.prompt` leva os ids.
- **Como o Claude Code recebe, pelo SDK oficial:** a imagem vai como bloco `image` em base64 na
  mensagem do usuário, exatamente como o exemplo de entrada em streaming do Agent SDK; todo
  arquivo, a imagem também, vai pelo caminho numa nota do plugin (`<atlas-attachments>`), e o
  Claude Code lê com o próprio `Read`, que já lê PDF, imagem e notebook. A pasta `attachments/`
  entra como pasta de trabalho dele desde o início (`--add-dir`, o `additionalDirectories` do
  SDK), então ler um anexo não pede permissão. Sem modelo de descrição e sem texto embutido: o
  Claude Code vê imagem e lê arquivo sozinho.
- **Reaberta, a sessão mostra as fichas:** a transcrição do Claude Code é a verdade (16f), então o
  plugin lê a própria nota de volta como os arquivos da fala (`TranscriptEntry.files`), nunca como
  texto do usuário, e o servidor transforma cada caminho no anexo (`TranscriptRow.attachments`);
  ao vivo, o `prompted` e o `steered` levam os anexos.
- **SDK de plugins versão 13**, só acrescentando: `ExecutorFile`, `prompt(files=)` e
  `steer(files=)` opcionais, `ExecutorSpec.directories` e `TranscriptEntry.files`. Um plugin
  antigo continua funcionando e simplesmente não recebe arquivo.
- **A prova:** o plugin contra o Claude Code falso (o bloco de imagem igual ao do SDK, o PDF pelo
  caminho, o `--add-dir`, a sessão lida de volta com os arquivos), a bancada (prompt e steer com
  arquivo, a conversa guardada com eles, o executor sem `files` recusando), o protocolo pelo
  socket (o arquivo enviado em partes, ouvido no `prompted` e lido de volta como ficha) e o
  Vitest da tela da sessão. Falta o usuário colar um print e um PDF numa sessão de verdade.

### O SDK de plugins completo (pedido em 20/10, passo 16j)

Pedido do usuário em 20/10: "a versão definitiva do SDK de plugins, para criar os mais diversos
plugins". Até a versão 13 um plugin só registra canal (Telegram) e executor (Claude Code); o passo
16 deixou de fora skills e MCP dentro do plugin, ganchos, tela própria, instalar por endereço e o
guia de quem escreve. Estudada em 20/10 a referência de manifesto do Claude Code (skills, commands,
agents, hooks, `.mcp.json`, `.lsp.json`, `bin/`, `userConfig`, monitors).

**O desenho sai dos plugins que o usuário vai escrever**, dois, os dois só dele (na casa,
`~/.atlas/plugins/`, nunca no repositório) até existir a loja de plugins:

- **O ERP de um cliente:** um `.mcp.json` para o MCP do ERP, que o usuário está fazendo, com o
  token no cofre, e skills de como usar o ERP. Só arquivo, sem Python. As ferramentas do MCP
  passam pelas aprovações de sempre.
- **Vídeo com o Remotion:** as skills oficiais do Remotion (`remotion-dev/skills`; a licença delas
  é conferida antes de copiar), o Claude Code quando instalado (a `delegate` já oferece o executor:
  a skill manda entregar a composição a ele, ou escrever direto quando não há), o Remotion Studio
  no painel lateral pelo `desktop_preview`, que já abre `localhost`, e uma ferramenta em Python para
  renderizar e mostrar o vídeo. O Remotion é grátis para pessoa e empresa de até 3 pessoas; um app
  que gera vídeo por pedido paga por renderização. Por isso o plugin é do usuário e não oficial: a
  licença volta a ser assunto quando a loja abrir.

**O que o Atlas passa a carregar de um plugin** (o formato do Claude Code, que o Atlas já usa no
`plugin.json`, nas skills e no `mcp.json`):

- `skills/`, com o nome do plugin na frente (`video:render`), como o Claude Code; o
  `skill_manage` não escreve nelas e o curador não as arquiva;
- `.mcp.json` (e o `mcpServers` do manifesto), cada servidor com o nome
  `plugin_<plugin>_<servidor>` (o do Claude Code), com `${CLAUDE_PLUGIN_ROOT}` e
  `${CLAUDE_PLUGIN_DATA}` (os nomes do Claude Code, para um plugin dele rodar aqui) e o segredo
  pelo `${NOME}` que o `mcp.json` do Atlas já entende. **Achado no desenho:** o cliente MCP enche
  qualquer `${NOME}` com o cofre inteiro, então um plugin poderia ler a chave da Anthropic sem
  declarar; o servidor de um plugin só podia citar os segredos do `atlas.secrets` dele. **Mudado
  com o usuário em 20/10, no 16k** (a regra recusava os plugins oficiais do GitHub e do Terraform):
  o segredo citado conta como declarado, aparece no consentimento e entra na impressão digital, e
  o plugin nunca lê uma chave que o usuário não viu.

Tudo isso entra no consentimento: a tela mostra as ferramentas, os servidores MCP (com o comando
ou o endereço) e as skills, e uma atualização que declara algo novo desliga o plugin até o usuário
consentir de novo, como hoje. **Saíram durante o trabalho, para depois:** `agents/` (os bots do
Atlas não são os subagentes do Claude Code, e nenhum dos dois plugins usa), `bin/`, o `userConfig`
e o `atlas plugins dev` (recarregar ao salvar); desligar e ligar de novo já carrega o código novo.

**O que o Python ganha (versão 14 do SDK, só acrescentando):**

- `register_tool`: nome, descrição, esquema e a função; a ferramenta chega pela
  `tool_search` (`source` `plugin:<nome>`), passa pelo portão e pelas aprovações como qualquer
  outra, e nunca toma o nome de uma do Atlas (substituir uma ferramenta fica de fora);
- `on_unload`: o que o plugin abriu (o Studio, uma renderização) para quando ele é
  desligado ou o Atlas fecha.

**Para quem escreve:**

- `atlas plugins new <nome>`, o esqueleto com o manifesto, uma skill e um `__init__.py`;
- `atlas plugins validate <pasta>`, como o `claude plugin validate`: carrega pelo carregador de
  verdade, numa casa temporária, sem rede, e diz o que está errado;
- o guia (`src/atlas/docs/pages/extending/plugins.md`), em inglês, com os dois plugins como exemplo, sem o ERP de verdade.

O portão trata `atlas plugins new` como decisão do usuário, como o `enable`: o agente não cria
plugin na casa sem perguntar.

**Fica para depois, até um plugin pedir:** ganchos (nenhum dos dois usa; quando entrarem, primeiro
o `hooks.json` do Claude Code, para os plugins dele rodarem aqui, e depois a função em Python), a
tela própria no app, comandos de barra de plugin, `ctx.llm`,
provedores de modelo, `.lsp.json`, monitors, instalar por endereço e a loja.

**A prova:** os testes de cada peça contra o carregador de verdade
(`tests/plugins/test_plugin_parts.py`): o manifesto e o consentimento; a ferramenta carregada,
rodando com progresso e parando com o plugin; uma ferramenta não declarada marcando o plugin; as
skills listadas com o nome do plugin e recusadas pelo `skill_manage`; o servidor MCP de teste de
verdade subindo da pasta do plugin quando ele liga e saindo quando desliga; uma conversa em que o
modelo falso acha a ferramenta pela `tool_search`, chama e recebe o resultado; o `new` que passa
no `validate`, e o `validate` dizendo o que está errado. Falta a prova com os dois plugins do
usuário de verdade: o do ERP contra o MCP dele, quando estiver pronto, e o de vídeo gerando,
editando no Studio e renderizando um vídeo curto, com e sem o Claude Code.

#### O SDK completo, para qualquer plugin (pedido e decidido em 20/10, passos 16k a 16n)

Depois do 16j o usuário pediu: "não é o momento de criar plugins e sim de completar o SDK, deixar
ele preparado para qualquer plugin que eu queira criar". A lista é tudo o que um plugin precisa
fazer e o Atlas ainda não deixa, na ordem aprovada:

- **16k, o formato de arquivos do Claude Code inteiro:**
  - **ganchos** (`hooks/hooks.json`): um comando roda num evento e pode barrar, mudar ou
    acrescentar contexto. Os nomes dos eventos e o JSON de entrada e de saída são os do Claude Code,
    para os ganchos dele rodarem aqui sem mudança. É a base do sistema de eventos do Atlas, que não
    existia;
  - **comandos** (`commands/*.md`), lidos como skills, como o próprio Claude Code faz;
  - **programas** (`bin/`), no `PATH` dos comandos que o modelo roda;
  - **configurações** (`userConfig`): as sensíveis vão para o cofre;
  - **agentes** (`agents/*.md`) viram **subagentes pré-definidos** que a `delegate` oferece, cada
    um com nome, instruções, ferramentas e modelo próprios (decidido pelo usuário em 20/10: é o que
    o `agents/` do Claude Code é, um ajudante de uma tarefa e sem memória; um bot de plugin, com
    memória e conversa própria, fica para quando um plugin pedir);
  - **monitores**: um processo em segundo plano cujas linhas viram avisos na conversa.
- **16l, o resto do Python:** ganchos em Python nos mesmos eventos; comando de barra com código
  próprio e comando de terminal (`atlas <plugin> ...`); usar o modelo do usuário (`ctx.llm`); mandar
  mensagem a uma conversa; provedor de modelo.
- **16m, a tela própria no app:** painel, página, item na barra lateral e comando na paleta, e os
  temas.
- **16n, para quem escreve e instala:** recarregar ao salvar, instalar por endereço (git) e
  dependência entre plugins.
- **Ficam de fora, com motivo:** servidores de linguagem (`.lsp.json`, inteligência de código de
  editor, que o Atlas não tem) e estilos de saída (o Atlas tem o `SOUL.md`).

**Escolha própria nos monitores, marcada:** no Claude Code cada linha do monitor é um aviso; no
Atlas um aviso começa um turno, que gasta uma chamada de modelo, então as linhas que chegam em um
segundo viram um aviso só (até 50 linhas e 4.000 caracteres). **Nos agentes:** o `tools` deles
limita só as ferramentas do próprio Atlas (os nomes do Claude Code valem: `Bash` é o
`run_command`), como o `tools` de um bot, e o `model` só vale como `provedor/modelo`; os apelidos
do Claude Code (`sonnet`, `inherit`) rodam no modelo de quem delegou.

**Escolha própria nos ganchos, marcada:** no Claude Code um gancho que responde `allow` dispensa a
pergunta de permissão. No Atlas, `deny` barra e `ask` obriga a pergunta, mas `allow` não passa por
cima do portão: um plugin não diminui as aprovações do usuário: o `approve` de plugin cai no
portão humano e falha fechado.

#### A prova do SDK com os plugins do usuário (decidido em 21/10, passo 16r)

Com o 16n o SDK planejado ficou completo, mas a prova dele ainda não foi feita: são os dois plugins
do usuário, que ficam na casa dele (`~/.atlas/plugins/`) e nunca no repositório (o do ERP é dado
de um cliente), até a loja abrir. Na ordem:

1. **O do ERP, primeiro**, porque já cabe no SDK de hoje: o `.mcp.json` apontando para o MCP que o
   usuário está fazendo, o token no cofre, skills com o fluxo do cliente, um gancho que pede
   aprovação antes de gravar no ERP, um monitor de pedidos novos, uma página com o painel do dia
   pelo `register_call` e um `/erp:status` sem modelo.
2. **O de vídeo, depois**: Remotion no `bin/`, uma skill de roteiro, um subagente editor, o
   trabalho pesado no Claude Code pela `delegate` quando ele estiver instalado, e uma página com os
   renders.
3. **O que faltar no SDK entra só se um dos dois precisar**, cada coisa estudada antes. As lacunas
   já vistas pensando no de vídeo: a tela mostrar arquivos do plugin (a prévia do vídeo, por rotas
   HTTP do plugin), o Python avisar a tela sozinho (o
   progresso de um render) e instalar os pacotes do Node do plugin (o Claude Code roda `npm ci`
   pelo lockfile). Os pacotes Python, os marketplaces, a atualização automática e o isolamento da
   tela num quadro próprio continuam para quando a loja abrir.

Cada coisa que o SDK ganhar vem com teste no repositório, com um plugin de exemplo sem nada do
usuário; os dois plugins de verdade são a prova, não o teste.

### Várias máquinas (decidido em 19/10, passo 15)

Os cenários do usuário, de 19/10:
1. o Atlas instalado no PC de uma loja e operado de casa (rotinas, comandos, arquivos de lá);
2. o Atlas deste PC aberto de outro PC, com as mesmas conversas;
3. o Atlas num home lab ou no VPS da empresa, operado pelo app do PC.

Os três têm a mesma forma: **o app de uma máquina abre o Atlas de outra, e quem trabalha é o Atlas
de lá**, com os arquivos, as chaves, a confiança e as aprovações de lá.

**A regra, pesquisada em 19/10:** o servidor do agente escuta só na própria máquina, e de fora se
chega por túnel SSH, pelo Tailscale ou por um relé. Quem escuta fora da máquina exige senha, e o
TLS fica na frente, num proxy ou no Tailscale, nunca dentro do próprio servidor.

**As decisões, todas do usuário em 19/10:**

- **Fica de fora: um agente agir em outra máquina** (decisão 10). Exemplo: o Atlas deste PC rodar
  um comando na loja sem a Loja aberta no app. Cruzar de máquina troca arquivos, chaves e
  confiança, e deve ser uma ponte explícita. O modelo, se um dia fizer falta, são máquinas pareadas
  que executam ferramentas para um servidor principal.
- **O caminho: Tailscale para os três cenários, e túnel SSH como alternativa para servidor**
  (decisão 2), preparando o relé para o futuro.
  - O Tailscale é grátis para uso pessoal, atravessa o roteador sem abrir porta e cifra a conexão:
    serve a "um servidor sempre ligado, alcançado do notebook".
  - **Pelo Tailscale**, o servidor escuta também no endereço da máquina na rede do Tailscale (o
    `100.x`) e só aceita quem vem dessa rede. Não há certificado para configurar: o Tailscale já cifra.
  - **Pelo SSH**, o servidor continua só na própria máquina, e o app abre o túnel com o `ssh` do
    sistema (`ssh -N -L`), com o `~/.ssh/config` e as chaves que o usuário já tem. Os
    cuidados do túnel: nunca pedir senha (`BatchMode`), aceitar a chave do servidor só na primeira
    vez e falhar com o erro do OpenSSH quando ela muda, manter vivo depois de o notebook dormir,
    subir de novo poucas vezes antes de dar a conexão por morta e achar o `ssh` do Windows, ou o
    do Git, sem caminho fixo.
  - **O app não sobe nem instala o Atlas na outra máquina por SSH.** É a peça que mais quebra
    (processo órfão no remoto, shell fish e zsh, o `ControlMaster` dividido, o cliente SSH do
    Windows). No servidor, o Atlas entra pelo instalador de uma linha
    (passo 18), e "Sempre ligado" é ligado pelo terminal.
  - **O relé fica para o Atlas Cloud** (passo 20), com o app Android: o código de conexão diz o
    tipo do caminho, e `relay` entra depois como mais um tipo, sem quebrar os códigos existentes.
- **"Sempre ligado", por casa** (decisões 3, 4 e 8). Uma chave, desligada por padrão:
  - ligada, o servidor daquela casa vira um serviço do usuário. Ele continua rodando com o app
    fechado, volta sozinho quando o computador liga, e o app daquela máquina passa a usar esse
    mesmo servidor em vez de subir o seu. As conversas locais e as remotas são as mesmas, e as
    rotinas rodam com o app fechado;
  - desligada, nada muda em relação a hoje: o servidor nasce e morre com o app;
  - a chave vem desligada por padrão. O nome é do usuário, que pediu um que dissesse a mudança de
    "morre com o app" para "roda enquanto a máquina está ligada";
  - o mecanismo de cada sistema:
    - no Windows, uma tarefa agendada que roda ao entrar no usuário, sem pedir administrador
      (`/RL LIMITED`), que sobe de novo quando cai. Uma reserva na pasta Inicializar, para
      quando a tarefa é recusada, ficou de fora (19/10): o servidor que sobe por ali não seria
      parado pela tarefa, então a chave diz por que falhou. A saída do `schtasks` muda com o idioma
      do Windows (o deste usuário é português), então a tarefa é escrita e conferida por XML, nunca
      lendo o texto;
    - no Mac, um `launchd` do usuário (`RunAtLoad`, `KeepAlive`);
    - no Linux, uma unidade `systemd --user` que sobe de novo quando cai (`Restart=always`). Para
      um VPS ligar o Atlas sem ninguém entrar, o Atlas liga o `linger` do usuário (`loginctl
      enable-linger`), sem pedir `sudo` nem uma unidade do sistema;
  - "volta sozinho ao ligar" quer dizer ao entrar no usuário, no Windows e no Mac. Rodar antes de
    qualquer login pediria um serviço do Windows, fora do usuário, sem as pastas e o Chrome dele;
  - **por casa** porque cada casa tem o seu servidor (decidido em 11/10). Dentro de "Sempre
    ligado", cada casa abre ou não. Um servidor para a máquina toda mudaria a regra de 11/10 e o
    servidor por inteiro.
- **"Abrir para as minhas máquinas"**, dentro de "Sempre ligado" e só com ela ligada (decisão 4):
  - o servidor passa a escutar também no endereço do Tailscale e mostra o **código de conexão**;
  - sem o Tailscale instalado ou ligado, a chave diz como instalar, e o servidor espera o Tailscale
    subir (no Windows, ele sobe depois do usuário entrar);
  - separada de "Sempre ligado" porque uma coisa não pede a outra. Exemplo: a rotina das 7h deste PC
    rodando com o app fechado, sem este PC aberto a ninguém: o servidor sempre vivo, e o acesso de fora
    só quando ele escuta fora da máquina, com senha.
- **O código de conexão** (decisão 5): um texto só, com a versão, o tipo do caminho, o endereço do
  Tailscale, a porta, a casa e a senha do servidor. Na máquina de lá, o app mostra o código para
  copiar, e `atlas server code` o imprime no terminal (o caminho do VPS e do container). No outro
  PC, "Adicionar máquina" pede só o código e um nome ("Loja"); com o caminho SSH, pede também o
  host do `~/.ssh/config`.
  - Um código único, em vez de colar o endereço e a senha separados.
  - O código leva a senha, então é segredo: aparece escondido, com copiar, e nunca vai para o log.
  - "Trocar a senha" na máquina de lá invalida os códigos dados (`atlas server token --new`).
- **A lista de máquinas no app** (decisão 5):
  - cada máquina tem um nome, o caminho e a casa. "Este computador" é a do próprio app;
  - a lista fica na pasta do app, nunca na casa nem no repositório. As senhas ficam cifradas pelo
    sistema (`safeStorage` do Electron: no Windows, a proteção da conta do usuário), num arquivo à
    parte que só o usuário lê. A lista guarda nomes e endereços, nunca senha. A cifra vale onde o
    sistema a oferece (o chaveiro do Mac pode faltar);
  - **trocar de máquina:** um seletor no pé da barra lateral, acima da casa, só com duas ou mais
    máquinas. A hierarquia é máquina → casa → conversas.
    Uma máquina remota com duas casas abertas aparece como "Loja · default" e "Loja · testes";
  - **uma máquina por vez na tela, as outras continuam ligadas:** um turno da Loja segue rodando e
    avisa quando termina, como as casas já fazem. A conexão com uma máquina só abre quando ela é
    aberta pela primeira vez (sem túnel surpresa);
  - **Testar** confere o `/health` e o WebSocket com a senha, as duas pernas: um Testar que passa
    quer dizer que a conversa vai funcionar;
  - um Atlas do outro lado com outro protocolo é recusado com a mensagem de qual lado atualizar;
  - **fica de fora:** juntar numa tela só os agentes de todas as máquinas, e "atualizar
    todas as máquinas" de uma vez.
- **A tela de Configurações começa aqui, só com a seção "Máquinas"** (decisão 7). Abre por uma
  engrenagem no pé da barra lateral e pelo seletor de máquina.
  - **Esta máquina:** "Sempre ligado" e "Abrir para as minhas máquinas" da casa aberta, o código e
    "Trocar a senha".
  - **Outras máquinas:** a lista, com Adicionar, Testar, Renomear e Remover.
  - Os outros ajustes do app só vão para lá num passo próprio, quando o usuário quiser.
- **O terminal pelo próprio servidor** (decisão 6):
  - o servidor abre o shell na máquina dele, e o texto vai e volta pela conexão do app, com a mesma
    senha. Funciona igual pelo Tailscale, pelo SSH e no container, e o ▶ e o `!` passam a valer no
    remoto;
  - **um caminho só, também no local:** o terminal deixa de rodar no app (`node-pty`) e passa a
    rodar sempre pelo servidor. O que o 16f fez em cima continua no app (as marcas de começo e fim
    do comando, o "ainda rodando", o ▶ e o `!`), e o servidor passa a escolher o shell e preparar as
    marcas. Sai do app uma peça nativa que precisava ser compilada para cada sistema e pesava no
    18b. Entram no servidor o `ptyprocess` e o `pywinpty`, que já vêm prontos para cada sistema;
    sem eles, o terminal aparece indisponível, como hoje sem o `node-pty`;
  - quem tem a senha já roda comandos pelo agente, então o terminal não abre nada além do que já
    existe.
- **A prévia:** arquivos e artefatos vêm pelo servidor. Um site no ar na outra máquina
  (`localhost:3000` da Loja) abre pelo túnel SSH quando a máquina foi adicionada por SSH, e nos outros
  casos a aba diz que o endereço é da outra máquina. Fazer o servidor do Atlas repassar o site seria
  **opção nova**, e espera o uso pedir.
- **As casas no remoto** (decisão 8):
  - **o Backup funciona:** o zip conferido desce pela conexão em partes de 4 MiB, como os anexos
    sobem, e é gravado no caminho que o diálogo de salvar escolheu aqui;
  - **Nova casa e Importar continuam desligados** e apontam o terminal: a casa nova não teria
    servidor ligado para o app alcançar.
- **Os bots** (decisão 9): a aba Bots mostra os da máquina e da casa abertas, como hoje. O bot roda
  onde mora a casa dele, e com "Sempre ligado" responde 24h. Juntar os bots de todas as máquinas fica
  para depois, com os agentes de todas as máquinas.
- **Continua como está:** a senha no cabeçalho, nunca na URL; as guardas de Host e Origin, que
  passam a aceitar também o endereço e o nome da máquina no Tailscale; o cofre preso à máquina (a
  senha do servidor de cada casa mora no cofre dela); e um servidor por casa.
- **Fica de fora também:** pastas escolhidas pelo diálogo do sistema numa máquina remota (a "Nova
  sessão" do executor recebe a pasta digitada, como já é); assumir o navegador da outra máquina (a
  janela do Chrome abre na tela de lá); e sincronizar uma casa entre máquinas (o backup já move uma
  casa).

**A ordem**, cada parte com um commit:
1. o glossário, a arquitetura e este desenho;
2. "Sempre ligado" e "Abrir para as minhas máquinas" no servidor e no terminal (`atlas always-on`,
   `atlas server code`), com as guardas novas;
3. a lista de máquinas, o código, o Tailscale e o túnel SSH no app, e a tela de Configurações;
4. o terminal pelo servidor;
5. o backup que desce pela conexão, e a prévia pelo túnel.

**A prova:**
- testes do código de conexão, das guardas (de fora do Tailscale é recusado, com o endereço dele é
  aceito, sem a senha é recusado) e das definições de serviço dos três sistemas;
- o serviço de verdade no Windows e no Linux do CI: ligar, ver o servidor subir sozinho, desligar e
  ver sair;
- de ponta a ponta no Playwright, com o app real, dois `atlas serve` reais e o modelo falso:
  adicionar uma máquina por código, conversar nela, trocar e ver o turno da outra avisar, o terminal
  e o ▶ rodando pelo servidor, o backup descendo;
- o túnel SSH contra um container com `sshd` e o Atlas no CI do Linux;
- prova real com o usuário: o Atlas deste PC "Sempre ligado" e aberto, alcançado de outra máquina
  pelo Tailscale, com uma conversa, o terminal e uma rotina rodando com o app fechado. Pede uma
  segunda máquina do usuário com o Tailscale. O custo de modelo é de poucas chamadas, perguntado
  antes.

**O código, feito em 19/10 (partes 2 a 5, um commit cada uma das três primeiras e um para a 4 e a 5):**
- **"Sempre ligado" e "Abrir"** (`atlas.instances`, `atlas always-on`, `atlas server token` e
  `code`): a tarefa agendada do Windows, o agente do `launchd` e o serviço do `systemd --user`; o
  serviço de verdade foi ligado, visto subir sozinho, parado, religado e desligado no Windows daqui.
  **Achado:** o servidor segura a casa antes de migrar o banco e escutar, então "pronto" é o
  `/health` responder, não a casa estar presa.
- **O app** (parte 3): a lista de máquinas com as regras do registro, as senhas cifradas
  pelo `safeStorage`, o túnel com os cuidados acima, a tela de Configurações e o seletor de
  máquina. **Achado e consertado:** um "Sempre ligado" sobrevive ao app, então o `atlas update` e a
  atualização pelo app esperariam por ele para sempre e recusariam ("o app está aberto"), e no
  Windows o `uv sync` não troca o que o Python dele carregou; a atualização agora para os serviços
  antes e religa depois, também quando volta atrás.
- **O terminal pelo servidor** (`atlas.terminals`, `terminal.*`): o `node-pty` saiu do app; no
  Windows o `winpty.PTY` do `pywinpty` (o extra `terminal`), sem o soquete que o `PtyProcess`
  acrescenta; no Linux e no Mac o `pty` da biblioteca padrão no lugar do `ptyprocess`
  (**opção nova**, justificada: o `ptyprocess` embrulha as mesmas chamadas, e é uma dependência a
  menos), com o shell iniciado pelo `subprocess` por um Python curto que toma o pty como terminal
  de controle, nunca `pty.fork` num servidor com threads. **Achados:** o servidor roda sob `uv run`,
  e o shell herdaria o ambiente do Atlas no começo do PATH (o `python` de todo terminal viraria o do
  Atlas) e, no container, a senha do servidor: os dois saem; o código de
  saída do próprio Windows PowerShell sob o ConPTY é sempre 1 (o do cmd vem certo), o que não muda
  nada, porque o de cada comando vem das marcas.
- **A prévia e o backup** (`preview.read`, `home.backup_download` e `home.backup_part`): o arquivo
  da prévia é lido onde o servidor está, também no local (um caminho só), e as miniaturas dos
  anexos com ele; um site no ar na outra máquina abre por mais um `-L` numa máquina SSH, e pelo
  Tailscale a aba diz que o endereço é de lá; o backup desce em partes e o processo principal só
  grava no arquivo que o diálogo deu, em `<arquivo>.part`, renomeado quando chega inteiro.
- **Provado:** os testes do Python (o código, as guardas, os serviços, o terminal de verdade no
  ConPTY e no pty, o backup em partes, a prévia), os do app (a lista, o túnel, a leitura das marcas
  sobre a conexão), o Playwright com o app real e dois `atlas serve` reais (uma máquina adicionada
  por código, uma conversa lá, o `!` rodando no terminal de lá, o backup descendo, o turno de lá
  avisando com a outra máquina na tela, removida; **achado e consertado:** o aviso do turno de outra
  máquina dizia "casa default", o nome da casa de lá, e agora diz a máquina; e o primeiro `!`, mandado
  antes de o servidor dizer que abre terminal, ia para o modelo como mensagem), tudo no Windows e no
  Linux, e o túnel SSH de
  verdade contra um container com `sshd` e o Atlas (`tests/docker/ssh-tunnel.sh`, rodado daqui e no
  CI): o Atlas de lá alcançado com a senha, e um site de lá aberto pela prévia.
- **Falta:** a prova real com o usuário (`scripts/prove_machines.py`, ensaiado contra um servidor
  daqui com o modelo falso), que pede o Tailscale entrado nas duas máquinas.

#### Prova em VMs do Azure (21/10, com os créditos que venciam no dia seguinte)

Cinco máquinas limpas, instaladas pelo instalador de uma linha com o `main` em pacote (`git
bundle`, o repositório é privado): Ubuntu 24.04 (o VPS), Debian 12, Ubuntu com Docker (o home lab,
pelo `docker compose`), Windows Server 2025 (o PC da loja, com login automático) e um segundo
Windows Server (o cliente). O modelo dos turnos foi um Azure OpenAI do mesmo crédito
(`gpt-5.4-mini`, por um `[providers.azure]` no `models.toml`: o endpoint `/openai/v1` aceita o SDK
da OpenAI como está).

**Provado:**
- `scripts/prove_machines.py` inteiro, as 8 etapas com um turno real, pelo túnel SSH nas quatro
  máquinas que servem (Ubuntu, Debian, container e Windows): `/health`, as guardas da senha, as
  conversas, o terminal de lá (bash e PowerShell), a prévia, o backup descendo e o turno;
- **o caminho do Tailscale**, a prova que faltava: as cinco VMs entraram no tailnet do usuário por
  uma chave reutilizável e efêmera (no Windows, o MSI com `TS_UNATTENDEDMODE=always`), as casas do
  Ubuntu, do Debian e do Windows foram abertas (`atlas always-on open`), e o
  `scripts/prove_machines.py` passou as 8 etapas, com o turno, contra as três, pelo nome
  `*.ts.net`, rodado do Windows cliente e do PC do usuário. No Windows que serve, a interface do Tailscale vem como rede privada
  e a porta abre sem regra de firewall;
- "Sempre ligado" num VPS: depois de reiniciar, o servidor subiu 10 s após o boot, antes de
  qualquer login (o `linger`); no Windows com login automático, subiu junto com o login;
- queda de energia: a VM desalocada e religada voltou com o servidor, as rotinas e a prova inteira;
- trocar a senha (`atlas server token --new`): o código antigo é recusado com a mensagem certa, o
  novo entra;
- rotinas com o app fechado nas quatro: um comando a cada 15 min e um prompt por hora em que o
  agente usa o terminal e escreve um arquivo; a primeira rodada passou nas quatro, e elas ficaram
  rodando a noite toda.

**Achados:**
1. **Consertado:** no Windows, um servidor que morria ficava fora até o próximo login. O
   `RestartOnFailure` do Agendador só vale para a tarefa que não conseguiu começar, não para o
   processo que terminou depois. A tarefa ganhou um gatilho de tempo, do registro em diante, que
   olha a cada minuto (`WATCH_INTERVAL`), com o `IgnoreNew` que já havia; na VM, o servidor morto à
   força voltou em 50 s. Uma repetição no gatilho de logon não serve: só conta do próximo logon.
2. **Consertado:** `atlas always-on on` por SSH num Windows fora de domínio falhava ("No mapping
   between account names and security IDs"): o OpenSSH do Windows põe `USERDOMAIN=WORKGROUP`, e o
   usuário da tarefa vinha dele. Agora vem do próprio Windows (`GetUserNameExW`).
3. **Consertado:** a mensagem de "Sempre ligado" sem servidor de pé culpava o app aberto ou uma
   falha; no Windows, o motivo comum é ninguém entrado na máquina, e ela agora diz isso.
   - **Consertado, no teste:** `test_an_open_home_with_no_tailnet_still_serves_this_machine`
     falhava num PC entrado num tailnet: fora do PATH, o Tailscale é achado onde o instalador o
     põe, e o Windows impõe o `ProgramFiles` a cada processo. Numa máquina num tailnet ele agora
     pula, dizendo por quê; no CI, sem tailnet, continua rodando.
4. **Aberto, decisão do usuário:** num Windows limpo (Server, e Windows recém-formatado), o
   instalador para ao montar o app: o binário nativo do `@electron-internal/extract-zip` do
   Electron precisa do runtime do Visual C++ (`vcruntime140.dll`), que o Windows não traz. O PC do
   usuário e o do testador já o tinham de outro programa. Com o `vc_redist.x64` instalado, o mesmo
   instalador, rodado de novo, terminou. Caminhos: o instalador instalar pelo `winget`
   (`Microsoft.VCRedist.2015+.x64`, que pede administrador uma vez), ou conferir antes e dizer o
   comando.
5. **Aberto, pequeno:** num Ubuntu mínimo, o instalador para porque o Node pede a `libatomic1`; a
   mensagem já diz o comando (`sudo apt install libatomic1`). Um VPS novo tropeça nisso na primeira
   linha.
6. **Aberto, a conferir:** a prévia de um Windows remoto recebeu um caminho no formato Unix e
   respondeu "não é caminho absoluto": vale ver se o app monta o caminho no formato da máquina de
   lá quando os sistemas diferem.
7. **Não repetido:** uma vez, a tarefa do Windows sumiu do Agendador e o servidor parou, sem nada
   no log da casa; refeita, não aconteceu de novo. A noite de rotinas fica de olho nisso.

### Configurações (decididas em 20/10, passo 15b)

O passo 15 abriu a tela de Configurações só com "Máquinas" e deixou os outros ajustes "para um passo
próprio, quando o usuário quiser". Em 20/10 o usuário pediu o menu definitivo. Antes dele, o tema
ficava no menu da casa, as chaves na tela de chave, o Soul e os Plugins na barra lateral, e o modelo
padrão, as regras de aprovação, a pasta inicial e o fuso só mudavam editando o `config.toml` à mão,
que o servidor lia uma vez, ao ligar.

**O desenho:** uma tela grande com as seções à esquerda, cada uma com as suas partes; a mudança
salva sozinha, sem botão de salvar, e vale sem reiniciar; e o que veio do ambiente aparece travado.
**Decidido com o usuário em 20/10:**

- **As seções, na ordem**, cada uma dizendo de onde vem:
  1. **Geral:** o idioma (Português, English ou o do sistema) e o tema (claro, escuro ou o do
     sistema), guardados neste computador, no app; e o "Sobre": a versão do Atlas, procurar
     atualização e atualizar, pelo botão de atualização que já existe. O tema sai do menu da casa.
  2. **Modelo:** o modelo padrão (provedor, modelo e esforço, das mesmas listas do seletor da
     conversa, ou "o primeiro provedor com chave") e as chaves dos provedores, a lista da tela de
     chave.
  3. **Personalização:** o Soul, o "Sobre você" (o `USER.md`) e a Memória (o `MEMORY.md`), cada
     entrada para ler, editar e apagar, mais "acrescentar", com o limite em caracteres à vista
     (1.375 e 2.200): o conteúdo à mostra, e não só o tamanho dos arquivos. A mudança vale a partir
     da próxima conversa, como o Soul já dizia: a conversa aberta guarda o que tinha quando começou
     (um retrato congelado, para o cache do provedor). A edição passa pela mesma porta da ferramenta
     `memory` (a trava, o limite, a busca de ameaças e a recusa quando o arquivo mudou por fora), e
     as
     propostas de memória continuam onde estão, com um atalho "N propostas esperando". O Soul sai
     da barra lateral.
  4. **Segurança:** os comandos bloqueados, os que sempre perguntam e os tirados das regras do Atlas
     (`[trust] deny`, `ask` e `ask_skip`, um por linha), e quanto tempo uma aprovação espera
     (`approval_timeout`).
  5. **Área de trabalho:** a pasta onde o agente começa (`[workspace] folder`) e o fuso das rotinas
     (`[time] zone`).
  6. **Plugins:** a tela que já existe, que sai da barra lateral. Quando alguém
     espera com um código, a barra mostra uma linha "Plugins · N esperando", como já faz com as
     propostas, que abre ali.
  7. **Máquinas:** como o passo 15 fez.
- **A barra lateral fica com** Conversas, Bots, Executores e Rotinas, e as Propostas quando há.
- **Salva sozinho e vale no próximo turno:** o servidor grava o `config.toml` e passa a
  usar o valor novo sem reiniciar. O modelo padrão vale no próximo turno de toda conversa que não
  escolheu o seu (a troca de modelo do passo 9 já segue o padrão); as regras de aprovação, no
  próximo pedido de toda conversa aberta; a pasta inicial, nas conversas novas; o fuso, nas próximas
  vezes que uma rotina for marcada. Um subagente que já roda termina com as regras com que começou.
- **A gravação mexe só na linha do ajuste**, como o `drop_section` já faz: os comentários e a ordem
  do resto ficam, e o arquivo novo é lido de novo antes de valer: se não der as mesmas configurações
  com só aquela mudança, nada é gravado. O valor passa pelas mesmas regras de quando o servidor liga
  (o fuso que não existe e o caminho relativo são recusados com a mesma mensagem). A gravação
  compara com o que leu antes para nunca regravar um campo que o usuário não tocou.
- **O que vem do ambiente aparece travado** (`ATLAS_MODEL_PROVIDER` e os outros), dizendo a
  variável: o ambiente vence o arquivo (Configurações, na arquitetura), e gravar no arquivo não
  mudaria nada.
- **Ficam de fora**, só no `config.toml` e no terminal: o nível de log,
  o endereço e a porta do servidor, o cofre e o programa do navegador. **E a busca nas
  Configurações**, que serviria a dezenas de ajustes: aqui são uns 15, e ela espera o uso
  pedir.
- **O idioma muda recarregando a janela**: os textos são lidos uma vez, quando ela abre. O servidor
  não é tocado, então nenhuma conversa para.

**A prova:**
- testes da gravação (o comentário e a ordem ficam, a lista que ocupa várias linhas sai inteira, o
  valor recusado deixa o arquivo como estava, o ajuste do ambiente é recusado) e do valor novo
  valendo sem reiniciar (o modelo padrão no próximo turno, a regra de aprovação no próximo pedido, a
  pasta na conversa nova);
- testes da memória pela tela (editar, apagar e acrescentar uma entrada, o limite, a ameaça recusada);
- de ponta a ponta no Playwright, com o app real, o `atlas serve` e o modelo falso: trocar o modelo
  padrão e ver o próximo turno sair nele; pôr um comando em "bloqueados" e ver o agente ser
  recusado; editar uma entrada do "Sobre você" e ver a conversa nova recebê-la; o Soul e os Plugins
  pelas Configurações; o tema e o idioma;
- sem prova com modelo real: nada aqui depende de um modelo de verdade, e o modelo falso prova o
  caminho todo.

**O código, feito em 20/10:**
- **No servidor:** `settings.read` e `settings.write` (`edit_settings`, que mexe só na linha do
  ajuste e confere o arquivo novo antes de gravar) e `Assistant.use_settings`, que entrega o
  valor novo ao modelo padrão, às regras de aprovação das conversas abertas, à pasta e ao fuso
  das rotinas; `memory.entries` e `memory.edit`, pela mesma porta da ferramenta `memory`. O
  servidor recebe as variáveis `ATLAS_*` com que leu as configurações (sem as secretas), para
  travar o que elas definem e reler o arquivo como na partida.
- **No app:** a tela de Configurações com as sete seções à esquerda; o Soul e os Plugins saíram
  da barra lateral (os Plugins voltam a ela só quando alguém espera com um código); o tema saiu
  do menu da casa e ganhou "o do sistema"; o idioma escolhido fica neste computador.
- **Os fiscais:** os cinco do Python passam no Windows, 2.146 testes com 91,9% de cobertura (15
  novos da gravação e 6 do servidor: o modelo padrão trocando no turno seguinte, a regra de
  bloqueio valendo na conversa aberta, a pasta e o fuso sem reiniciar, o "Sobre você" entrada
  por entrada, o limite com a mensagem da tela); os do app também (310 testes do Vitest e os 60
  cenários do Playwright, 2 novos: a regra, a entrada e a pasta salvas pela tela valendo sem
  reiniciar; as seções, a variável travando o fuso e o idioma recarregando a janela).
- **No Linux** (o CI parado por cobrança do GitHub; rodados daqui na `atlas-gates:node`): os cinco
  fiscais do Python, com 2.121 testes passando, os do app e os 60 cenários do Playwright. Os 2
  testes do `install.sh` que falharam lá pedem `git` e `curl`, que a imagem não tem e o runner do
  GitHub tem.
- **Falta:** o usuário usar a tela.

### Controle de programas (estudado e decidido em 20/10)

Pedido do usuário em 20/10: o Atlas abrir um programa do computador e usá-lo, para automação
profissional. Hoje ele opera o navegador (passo 14), manda em programas por script e COM pelo
`run_command` e fala com o que tem MCP; programa desktop sem API (sistema legado, SAP GUI) fica de
fora.

- **A escada da automação**, do mais firme para o mais frágil: API ou MCP, script/CLI/COM,
  navegador, árvore de acessibilidade da janela (UI Automation no Windows, AX no Mac, AT-SPI no
  Linux) e, por último, print com clique por coordenada. Os dois últimos degraus são a lacuna, e
  ninguém os separa: a captura manda o print com os elementos da janela numerados, e o modelo
  clica pelo número; a coordenada fica de reserva.
- **O que foi estudado em 20/10:**
  - **o motor:** o **cua-driver** da trycua, binário à parte, falando MCP por stdio, no Mac,
    Windows e Linux, sem roubar o cursor; a telemetria dele vem ligada. A captura tem três modos:
    `som` (print com elementos numerados), `ax` (só a árvore, sem imagem, o mais barato) e
    `vision` (só o print). Escrever o próprio motor não vale: é a peça cara, quase tudo permissão
    do macOS, árvore lenta, janela travada e tela bloqueada;
  - **a ferramenta:** uma só com `action` (capturar, clicar, arrastar, rolar, digitar, tecla,
    `set_value`), adiada atrás da busca, com aprovação por tipo de ação; o que derruba a sessão
    (logout) é recusado mesmo aprovado. Ela é arriscada e de último recurso, quando não há
    conector nem ferramenta própria; no Windows, um motor pode tomar o mouse e o teclado.
- **Proposta, pela escada de integração** (Regras de trabalho, abaixo):
  1. **Primeiro degrau, sem ferramenta nova:** o cua-driver como servidor MCP comum, com uma skill
     embutida que ensina a escada da automação e a parar em login e senha.
     **Falta uma peça no núcleo:** o MCP do Atlas hoje troca a imagem por um texto
     (`src/atlas/mcp/store.py`, "[an image, …]"), então o modelo não veria o print. A imagem do MCP
     chegando ao modelo, como o print do navegador chega, serve a todo MCP, não só a este.
  2. **Ferramenta no núcleo só se o primeiro degrau não bastar,** medido no uso: o `computer_use`,
     adiado, com o motor fora do núcleo (o cua-driver instalado sob pedido, trocável),
     a aprovação por tipo de ação e as ações sempre recusadas. Sem motor, a ferramenta não
     aparece, como o navegador sem Chrome. No container não há tela, então ela não existe lá.
- **Conferido em 20/10:**
  - **Licença:** o cua-driver é MIT, como quase todo o repositório da trycua. Não são MIT: o
    `cua-som` (AGPL-3.0), a extensão `cua-perception` (traz o OmniParser, AGPL-3.0), e o Cua
    Spaces e o Cua Driver Bench (FSL-1.1-MIT; o Bench era MIT até 11/10). O Atlas usa só o driver
    e nunca instala esses extras.
  - **Telemetria:** vem ligada e vai para o PostHog na Europa: versão, sistema, nome da ferramenta,
    duração e resultado em faixas; nunca texto, print, árvore, título de janela, caminho nem
    endereço. A conferência de atualização no GitHub é à parte. O Atlas desliga as duas no
    processo dele (`CUA_DRIVER_RS_TELEMETRY_ENABLED=false`, `CUA_DRIVER_RS_UPDATE_CHECK=false`).
    Rodado direto, o driver desta máquina estava com a telemetria ligada.
  - **Foco no Windows** (cua-driver 0.21.0, já instalado aqui), com o usuário usando o
    computador ao mesmo tempo: a Calculadora abriu sem vir para a frente; a árvore dela (39
    elementos, com os nomes dos botões) foi lida; `7 + 5 =` foi clicado pelos elementos, por
    acessibilidade e em segundo plano, e o visor deu 12; o print da janela saiu certo com ela atrás
    de outras. Em tudo isso, a janela da frente e o cursor do usuário não mudaram. **Mas:** digitar
    (`type_text`) na Calculadora falhou em segundo plano, e o driver pediu para vir para a frente
    (o caminho é o `set_value` no campo). O clique pede o `element_token` da última captura, não o
    número solto. O Bloco de Notas, aberto pelo driver, não veio para a frente: quem o trouxe e o
    fechou foi o usuário. Ele reabriu sozinho uma aba que já estava aberta antes: o programa que
    restaura a sessão mostra ao agente arquivos do usuário.
  - **Modo automático:** desde 20/10 (`6df0a98`) toda conversa nova começa em "Auto", que pula o
    guardião e as perguntas; só os pisos e as ações para fora (`atlas.trust.outward`) perguntam em
    todo modo. Um MCP é confiável por padrão.
- **Decidido pelo usuário em 20/10: "modo auto é modo auto".** No Auto, a ação na tela roda sem
  perguntar, em qualquer programa, sem lista de programas liberados nem bloqueio novo além dos
  perigosos: os pisos de sempre e o que derruba a sessão (sair, desligar, bloquear a tela), recusados
  mesmo aprovados. **Apagar, pagar, enviar ou confirmar algo para fora pergunta
  sempre,** em todo modo, como o `gh pr merge` (`atlas.trust.outward`). Como reconhecer essas ações
  na tela (o nome do botão e do menu na árvore, o que o modelo declara que vai fazer, ou os dois) é
  decidido no passo, com teste dos casos que escapam.

---

### Codex, Gemini CLI e DeepSeek (pedidos e decididos em 20/10, passos 16o a 16q)

Pedido do usuário em 20/10: "podemos considerar que nosso adaptador do Claude Code está completo e
criar os demais adaptadores dos outros harness? codex, deepseek, gemini". Respostas dele no mesmo
dia: o Hermes e o Pi ficam para depois desses três; a ordem é Codex, Gemini CLI, DeepSeek. O
cliente ACP do núcleo, que a Bancada de harnesses (acima) punha no plugin do Hermes, chega com o
Gemini CLI, o primeiro dos três que fala ACP, e o DeepSeek o reaproveita. O Gemini CLI não estava
entre os cinco harnesses estudados em 14/10: ele é estudado antes do código do 16p.

**O adaptador do Claude Code está completo** (passos 13, 16c, 16f a 16i fechados). Ficam para
quando o usuário quiser as provas reais adiadas (o prompt pela janela, o `auto` na 2.1.296, as
contas reais do 16h, o 16i no app). Uma costura do núcleo aparece com o segundo harness: a lista
dos executores cujo automático o Atlas aceita (`AUTO_EXECUTORS`, em `subagents.py`) e o texto da
`delegate`, que só citava o Claude Code.

#### O Codex (passo 16o)

Estudado em 20/10 no `codex` 0.163.0 (o do npm e o do app da Microsoft Store, 0.163.0-alpha.10.2,
que o usuário tem instalado e logado), no esquema que ele mesmo gera (`codex app-server
generate-json-schema` e `generate-ts`). As leituras sem efeito (conta, limites, modelos, conectores,
sessões) foram provadas na conta do usuário, sem chamada de modelo.

- **O canal é o `codex app-server`**, JSON-RPC 2.0 em linhas pelo stdio, o que a extensão do VS
  Code e o app do próprio Codex usam: `initialize`, depois `thread/start` (ou
  `thread/resume`, `thread/fork`) e um `turn/start` por prompt, com as notificações `item/*` até o
  `turn/completed`. Um processo por instância, como o do Claude Code. Uma linha pode passar de
  64 KB (a lista de conectores do usuário passou): o leitor aceita até 16 MB, como o do Claude Code.
- **Onde ele é achado:** o `codex` do PATH (no Windows, só o programa nativo; o `codex.cmd` do npm
  não roda pelo `CreateProcess`, então o Atlas usa o `codex.exe` que o pacote do npm traz, como faz
  com o do Claude Code), o app da Microsoft Store pelo registro do Windows (a pasta dele muda a cada
  versão, e o `codex.exe` de dentro roda de fora do app; provado em 20/10), o Homebrew no Mac, e a
  configuração `program` do plugin, como a do Claude Code.
- **A versão mínima é 0.163.0**, a provada. A comparação lê os três números e ignora o
  `-alpha` do app da loja.
- **O login nunca é tocado:** o Codex roda com a conta que o usuário já tem nele, e as variáveis
  que trocariam a conta (`OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL` e as `OPENAI_*`)
  saem do ambiente dele, como as `ANTHROPIC_*` saem do Claude Code. O nome do cliente no
  `initialize` não é "atlas": o Codex já usa esse rótulo para outro produto.
- **As permissões,** na política do terminal do Codex: o `sandbox` `workspace-write` (escreve só
  na pasta, lê o resto) e a `approvalPolicy` `on-request` (ele pede quando precisa sair dela). Os
  pedidos `item/commandExecution/requestApproval`, `item/fileChange/requestApproval` e
  `item/permissions/requestApproval` viram a `Permission` de sempre, e o `Verdict` volta como
  `accept` ou `decline`. **Escolha própria, marcada:** o `decline` do Codex não leva motivo; quando
  o usuário (ou o guardião) diz por que recusou, o motivo entra no turno por um `turn/steer`, logo
  depois da recusa escrita, para o Codex ler, como o Claude Code lê o dele. **Achado na
  implementação:** o Codex pode começar o turno e pedir nele antes de a resposta do `turn/start`
  ser lida; o plugin pega o id do turno do que chegar primeiro (a resposta, o `turn/started` ou o
  próprio pedido), senão o motivo da recusa não tinha turno para entrar. O pedido de um servidor
  MCP (`mcpServer/elicitation`) é recusado.
- **O automático é o revisor do próprio Codex** (`approvalsReviewer: "auto_review"`, um subagente
  dele que aprova ou nega cada pedido pelo risco), na mesma caixa de areia. É o equivalente do
  `auto` do Claude Code, e por isso o Codex entra na lista dos executores cujo automático o Atlas
  aceita, com a caixa de areia segurando a pasta. A troca no meio da sessão vale a partir do
  próximo turno: o Codex só muda a política no `turn/start`.
- **As perguntas:** o `item/tool/requestUserInput` do Codex só existe no modo Plan, e no modo de
  sempre ainda está "em desenvolvimento" (`default_mode_request_user_input`). Então
  `questions=False`: a pergunta chega como texto no fim do turno, e o usuário responde pelo prompt
  do cartão. Se o pedido chegar mesmo assim, ele vai ao usuário como pergunta.
- **O steer** é o `turn/steer`, com o id do turno que roda.
- **Os modelos e os esforços** vêm do `model/list` (os que não são `hidden`, com os
  `supportedReasoningEfforts`). A troca vale do próximo turno em diante (`turn/start` leva `model`
  e `effort`).
- **As sessões** vêm do `thread/list`, sempre com `useStateDbOnly`: sem isso o Codex "repara" o
  banco dele lendo os arquivos, o que é escrever. **Achado na implementação:** com
  `useStateDbOnly`, o filtro `cwd` do Codex não acha nada (0.163.0), então o plugin lê a lista e
  separa a pasta ele mesmo. A lista pede as origens `cli`, `vscode`, `exec` e `appServer`: sem
  isso o Codex só devolve as do terminal e do editor, e as sessões que o próprio Atlas abriu
  (`appServer`) sumiriam. As pastas saem das sessões (o Codex não as lista à parte). A transcrição vem do `thread/turns/list` com os itens completos, e não do arquivo: o
  formato do arquivo muda, o do protocolo é versionado. A cópia é o `thread/fork`; apagar é o
  `thread/delete`.
- **O que ele faz na tela:** a mensagem dele é `Said`, o resumo do raciocínio é `Thought`, o
  comando, a mudança de arquivos, a ferramenta MCP e a busca na web são `Called` e `Returned`, a
  compactação (`contextCompaction`) é `Compacted`, o fim do turno é
  `Finished`, e o limite do plano (`usageLimitExceeded`) é `LimitReached`.
- **Os conectores** vêm do `mcpServerStatus/list`; **os limites do plano** vêm do
  `account/rateLimits/read` (as janelas `primary` e `secondary`) e mudam com o
  `account/rateLimits/updated`.
- **As outras contas** são o jeito do próprio Codex: uma pasta por conta, `~/.codex-<apelido>` ao
  lado do `~/.codex`, passada como `CODEX_HOME`, e o login é o `codex login` dele, no navegador do
  usuário. Uma sessão de outra conta continua aqui numa cópia (`thread/fork` pelo caminho do
  arquivo dela).
- **Os arquivos anexados:** a imagem vai como `localImage`, pelo caminho; os outros vão nomeados no
  texto, como no Claude Code.
- **O que não tem:** comandos de barra pelo canal (os do Codex são da tela dele; o `app-server`
  só tem skills, chamadas por `$nome`), e saber se a sessão está aberta no app do Codex (o
  `app-server` do Atlas é outro processo). Continuar uma sessão aberta lá falha com o erro do
  Codex, que aparece no cartão.
- **Risco já conhecido** (Bancada de harnesses, acima): o `app-server` se diz experimental e muda
  toda semana. A versão mínima e os testes contra o falso seguram o que foi provado.
- **No núcleo:** o texto da `delegate` passa a nomear os executores instalados, sem citar o
  Claude Code (custa até 260 tokens a mais, o mesmo teto de antes); o `action='account'` leva o
  `executor` escolhido, porque com dois instalados ele pegava sempre o primeiro; o Codex entra
  em `AUTO_EXECUTORS`; e a tela da sessão apagada diz o nome do harness (antes dizia sempre que o
  Claude Code apaga em 30 dias).
- **A prova:** um Codex falso (`tests/fake_codex.py`) que fala o mesmo canal por pipes de verdade,
  com os testes do plugin contra ele; e a prova real (`scripts/prove_codex.py`, ensaiada contra o
  falso em `tests/test_prove_codex.py`) com o Codex do usuário: permissão, recusa, o revisor dele,
  steer, parar, e a sessão listada, lida e continuada. Ela gasta da cota do plano dele (a conta
  do usuário está no plano `free`, com um limite de 30 dias) e só roda depois de perguntar.

#### O Gemini CLI (passo 16p)

Estudado em 21/10 no código do Gemini CLI 0.65.0 (o lado ACP em `packages/cli/src/acp/`, os
arquivos de sessão, o cliente do Code Assist), no esquema do ACP (`agent-client-protocol` 0.9.0),
com três estudos em paralelo antes do código. Instalado pelo npm nesta máquina com o sim do usuário
(21/10), para as leituras reais.

**Decidido com o usuário em 21/10** (perguntas feitas depois do estudo):

- **O automático é o `yolo` do próprio Gemini**, que aprova tudo sem revisor, e não o guardião do
  Atlas (que era a recomendação). O Gemini entra em `AUTO_EXECUTORS`: a `delegate` também o roda
  em `yolo`. É a primeira exceção à regra de que um automático que não revisa nada não entra lá.
- **A confiança de pasta do Gemini é respeitada:** numa pasta que ele não confia, ele recusa o
  `yolo`; a sessão diz isso e segue pedindo antes de cada ferramenta.
- **O uso do plano é lido no Google** (`loadCodeAssist` e `retrieveUserQuota`, as chamadas do
  `/stats` dele, sem gastar requisição de modelo), com o token que o login dele guarda, só
  enquanto o token vale: o Atlas nunca renova. Com o token vencido o painel diz que os limites
  aparecem depois do próximo turno, e eles chegam ao fim de cada turno (`UsageChanged`).
- **Instalar agora**, para as leituras reais durante o trabalho.

**O que ficou, com o que o estudo achou:**

- **O cliente ACP do núcleo** (`atlas.plugins.acp`, o SDK versão 18): o canal do Codex
  (`CodexServer`) generalizado, o código de erro guardado (`-32000` pede login) e o `session/prompt`
  sem prazo, porque só responde no fim do turno. **Escolha própria, marcada:** não usar o pacote
  Python do ACP, que traz o pydantic e muda os modelos mais rápido que os harnesses; dicionários
  simples. O Atlas não oferece sistema de arquivos nem terminal: o Gemini
  lê, escreve e roda comandos sozinho, na caixa de areia dele. A permissão é respondida pela
  espécie da opção (`allow_once`, `reject_once`), nunca pelo id, que cada harness nomeia do seu
  jeito.
- **Achados do estudo** que mudaram o desenho: o `ask_user` dele fica de fora no ACP (sem
  perguntas); um segundo `session/prompt` encerra o turno que roda e segue com o novo, então o
  steer é um segundo prompt e o Atlas espera o segundo como o mesmo turno; a resposta a uma
  permissão não leva motivo, então o motivo de uma recusa entra como um prompt desses (como no
  Codex); a cota estourada chega como erro JSON-RPC 429 com a mensagem do Google, e o 429 de pausa
  de um minuto não é o limite do plano; a troca de modo chega como mensagem dele (`[MODE_UPDATE]`),
  que não aparece; o `--acp` sobe dois processos (pai e filho), e parar mata a árvore toda; o
  `NO_BROWSER` faria ele escrever um pedido de login no canal, e sai do ambiente.
- **Lido dos arquivos, sem rodar o Gemini** (rodar renova o login e pode escrever): a conta (o
  login escolhido nas configurações, a conta do Google que ele nomeia, se guarda token), as sessões
  (`.gemini/tmp/<projeto>/chats/session-*.jsonl`, dobradas como o leitor dele dobra os registros
  `$set`, `$patch` e `$rewindTo`) e os conectores (os servidores MCP das configurações e das
  extensões, ligados ou não). **Achado na implementação:** o marcador `.project_root` guarda a pasta
  em minúsculas no Windows; o plugin devolve a caixa real. Os modelos vêm de uma sessão que ele
  abre (o `session/new` grava um arquivo sem mensagem, que as listas dele e as do Atlas pulam); os
  comandos de barra, do `available_commands_update`.
- **As outras contas** são casas próprias (`GEMINI_CLI_HOME`, `~/.gemini-<apelido>`, com o
  `.gemini` dentro), logadas pelo `authenticate` do ACP no navegador. Continuar uma sessão em
  outra conta, ou num fork, é uma cópia do arquivo com id próprio, que ele carrega; apagar leva o
  arquivo e o que ele guardou ao lado, como o `--delete-session` dele.
- **Achado no fim:** o Atlas leva a própria cópia do Node (`AppData\Local\atlas\tools\node-*`),
  e o npm global desta máquina instala lá; a procura parte do `gemini` do PATH, qualquer pasta,
  e roda o script com o Node ao lado dele.
- **Sobre o que o Gemini manda ao Google:** as estatísticas de uso dele ficam ligadas a não ser que
  as configurações dele as desliguem; o Atlas não mexe nelas e a declaração do plugin diz isso.
- **No núcleo:** a regra "plugins importam só o SDK" passa a contar o `atlas.plugins.acp`; o e2e dá
  a cada teste um `GEMINI_CLI_HOME` próprio, como já dava `CLAUDE_CONFIG_DIR` e `CODEX_HOME`.
- **Achado na prova real (21/10): o Google não aceita mais o login de conta pessoal no Gemini CLI**
  ("This client is no longer supported for Gemini Code Assist for individuals", mandando migrar
  para o Antigravity). Para conta pessoal só resta a chave de API. **Decidido com o usuário no
  mesmo dia:** a chave é um segredo do plugin (`GEMINI_API_KEY`, no cofre do Atlas, como o token
  do Telegram), entregue só às sessões que ele abre; o Gemini do terminal fica como está. É o mesmo
  nome que o provedor Gemini do próprio Atlas usa, então a mesma chave serve aos dois. O login com
  Google fica para contas de empresa, e a leitura da cota no Google também: com chave de API, o
  painel diz que não há limites de plano para ler. A recusa do Google vira, no cartão, o caminho
  para criar a chave no AI Studio.
- **Achado na prova real: um bug que o falso escondia.** No `tool_call` real, o `content` é uma lista
  (o diff da escrita); o plugin lia como um bloco só, e a exceção dentro do ouvinte derrubava a
  leitura do canal, que dizia que o Gemini tinha terminado. Corrigido, o falso passou a mandar a
  lista como o Gemini manda, e o cliente ACP agora registra no log a notificação que não consegue
  ler e segue lendo, em vez de perder o turno.
- **Outros achados da prova real:** a chave gratuita esgotou a cota diária do modelo que o `auto`
  dele escolhe (cada turno manda uns 30 mil tokens de entrada, o prompt de sistema dele), e cada
  turno parou com `LimitReached` e a mensagem do Google, o caminho do limite provado no real; a cota
  é por modelo, e a prova seguiu no `gemini-3.5-flash-lite` (`--model`). Um comando longo ele manda
  sozinho para segundo plano ("Command moved to background"); ao parar, nada ficou segurando a
  pasta.
- **A prova:** um Gemini falso (`tests/fake_gemini.py`) que fala ACP por pipes de verdade e grava as
  sessões como ele, com os testes do plugin e do cliente ACP contra ele; as leituras reais na
  máquina do usuário (21/10: a versão, 38 sessões em 10 pastas, uma transcrição, sem prompt); e a
  prova real (`scripts/prove_gemini.py --vault`, ensaiada contra o falso em
  `tests/test_prove_gemini.py`), que passou nos 7 passos em 21/10 com a chave do cofre no
  `gemini-3.5-flash-lite`: permissão, recusa, o `yolo` sem pedir nada, steer (a resposta terminou
  em BANANA), parar, a sessão listada, lida e continuada (ele lembrou do `allowed.txt`) e o painel
  dizendo que chave de API não tem limites de plano.

#### O DeepSeek (passo 16q)

Estudado em 21/10 no código do DeepSeek Harness (`dsh`, `@deepseek-ai/dsh` 0.2.1-rc.2, MIT): o
pacote ACP dele (`packages/acp/acp`), o formato das sessões e a configuração. Instalado pelo npm
nesta máquina com o sim do usuário, para as leituras reais (o handshake, as opções de uma sessão, o
`--patch` e o modo de permissão, sem prompt). Os dois estudos em paralelo caíram três vezes no
limite de gasto da organização; o estudo foi feito direto no código.

**Decidido com o usuário em 21/10** (perguntas feitas depois do estudo):

- **Os logs da sessão que o `dsh` manda à DeepSeek** (`session-log-deepseek`: as mensagens, os
  argumentos e resultados das ferramentas, os caminhos e o feedback, junto de cada pedido, ligados
  por padrão desde a decisão dele de 14/09) **ficam desligados nas sessões que o Atlas abre**, por um
  `--patch` próprio, e a configuração `session_logs` do plugin liga de volta. O `dsh` do terminal
  fica como o usuário o tiver.
- **O automático é o `danger-full-access` dele**, que aprova tudo sem revisor, como o `yolo` do
  Gemini, e o DeepSeek entra em `AUTO_EXECUTORS`. O modo é do processo (`DSH_PERMISSION_MODE`, lido
  na partida): trocar entre turnos reinicia o `dsh` na mesma sessão (`session/resume`); durante um
  turno, vale do próximo.
- **A transcrição de uma sessão antiga é lida dos arquivos**, com uma dependência nova e fixada, o
  `zstandard` 0.25.0, no extra `deepseek` (o Python 3.13 não lê Zstandard; o 3.14 lê).
- **Instalar agora**, para as leituras reais.

**O que ficou, com o que o estudo achou:**

- **O ACP dele é só para automação:** sem login pelo canal, com `session/new`, `session/resume`,
  `session/list` e `session/close`, o modelo e o esforço como opções de configuração, e sem
  `session/load`, fork, apagar, modos, comandos, pastas extras nem imagens. A permissão só oferece
  `allow-once` e `reject-once`.
- **O steer é cancelar e mandar um prompt novo:** ele aceita um prompt por vez e recusa o segundo,
  então o turno é cancelado (o trabalho fica na sessão) e o steer entra em seguida, um turno só para
  o Atlas. O motivo de uma recusa entra do mesmo jeito. **Achado no estudo:** um turno que termina
  em erro responde `end_turn` no ACP, mas o prompt é rejeitado ("turn failed: …"); o 402 da DeepSeek
  para saldo acabado vira o limite da conta (`LimitReached`).
- **A conta é a chave de API da DeepSeek:** o segredo do plugin (`DEEPSEEK_API_KEY`), senão a do
  ambiente ou a que o próprio `dsh` guarda (`.credentials.yaml` na casa dele). O login pelo
  navegador é das telas dele, e o ACP só oferece o provedor da chave. O painel mostra o saldo pelo
  `/user/balance` da DeepSeek, com a mesma chave: ela cobra por uso, sem janelas de plano.
- **Lido da casa dele, sem rodá-lo:** a lista de sessões vem da projeção que ele guarda ao lado de
  cada uma (`storages/session_projcache/sessions/<id>.json`: a pasta, o título, quantos prompts), e
  a transcrição, do log (`sessions/<pasta>/<id>/session.v4.jsonl.zstd`). Os modelos e esforços vêm
  de uma sessão que ele abre e fecha, sem prompt, que por isso não aparece em lista nenhuma.
- **As outras contas** são casas próprias (`DSH_HOME`, `~/.dsh-<apelido>`), cada uma com a sua chave.
  Sem fork nem cópia no ACP dele, uma sessão só continua na conta que a tem.
- **No núcleo:** o DeepSeek entra em `AUTO_EXECUTORS`, o e2e dá a cada teste um `DSH_HOME` próprio,
  e a barra lateral dá a ele uma marca (o azul da DeepSeek e um telescópio, nunca a baleia dele).
- **A prova:** um `dsh` falso (`tests/fake_dsh.py`) que fala ACP por pipes de verdade e grava as
  sessões em quadros Zstandard como ele, com os testes do plugin contra ele; e a prova real
  (`scripts/prove_deepseek.py`, ensaiada contra o falso em `tests/test_prove_deepseek.py`):
  permissão, recusa, o automático, steer, parar, a sessão listada, lida e continuada, e o saldo.
  Ela precisa de uma chave da DeepSeek com saldo (o cofre não tem uma) e só roda depois de
  perguntar.

### Voz (decidida em 24/10, passo 17a)

Pedido do usuário em 24/10: mandar áudio pelo Telegram e pelo app, e conversar por voz com o Atlas
como se fala com uma pessoa, de microfone aberto. Hoje o áudio chega ao modelo só como nota (16g),
e o canal só manda texto de volta.

- **Um plugin oficial, `voice` (a voz), no repositório, desligado como os outros.** Decidido pelo
  usuário em 24/10; o nome em inglês, como o dos outros plugins. As duas partes usam a mesma base (ouvir e falar), por isso um plugin só. **O núcleo não
  ganha nenhuma linha de voz:** só três aberturas no SDK, que servem a qualquer plugin:
  - transformar um anexo antes do turno (o áudio vira texto no mesmo ponto que decide imagem,
    documento e nota, e assim o Telegram e o app ganham juntos);
  - o canal mandar um arquivo de volta (o áudio como balão de voz no Telegram);
  - o plugin pôr um botão na caixa de mensagem e na conversa.
- **Tudo pela OpenAI, com o `OPENAI_API_KEY` que já está no cofre** (decidido pelo usuário em
  24/10: a conversa por voz tem de ser a melhor possível, paga). Sem a chave, a tela de Plugins
  mostra que ela falta e o áudio chega como a nota de hoje. A transcrição local e a voz grátis
  ficam de fora.
- **Áudio que chega** (Telegram, ou solto na caixa do app): transcrito uma vez, quando a mensagem
  chega, e guardado com o anexo, como a descrição de imagem do 16g; o modelo recebe o texto marcado
  como transcrição, com o caminho do arquivo. Menos de US$ 0,01 por minuto. Falhou: a nota de hoje,
  dizendo que não deu para transcrever.
- **O 🎤 da caixa dita** (decidido pelo usuário em 24/10): grava, e o texto aparece na caixa para
  corrigir antes de enviar. Não manda o áudio.
- **O Atlas só fala quando pedem** (decidido pelo usuário em 24/10): uma ferramenta de fala
  (`voice_speak`) que o modelo chama quando o usuário pede ("me manda isso em áudio"); sai como balão de voz no Telegram
  e como arquivo na conversa do app. Receber áudio não faz o Atlas responder em áudio.
- **A conversa por voz:** um botão na conversa abre o modo voz, e quem fala com o usuário é o
  `gpt-live-1` da OpenAI, que ouve enquanto fala, aceita ser interrompido e lida com barulho.
  - Ele não tem ferramentas: o que pede trabalho de verdade ele passa ao Atlas (a delegação do
    `gpt-live-1`), que responde com as ferramentas, a memória e as aprovações de sempre, e a voz
    fala a resposta. Enquanto o Atlas trabalha, ela diz que está vendo; o que é só conversa ela
    responde sozinha.
  - Cada pedido passado vira um turno normal na conversa aberta, com o que o usuário disse e o que
    o Atlas respondeu na tela. O turno leva uma nota (não o prompt de sistema) dizendo que é fala
    transcrita e que a resposta vai ser falada (curta, sem Markdown, sem lista), e as últimas falas,
    porque um "não, quinta" sozinho não diz nada. O que o Atlas está fazendo chega à voz como
    contexto, para ela contar se perguntarem.
  - **Quem responde os turnos de voz** (decidido pelo usuário em 24/10): o usuário escolhe entre o
    modelo da conversa e um modelo fixo só para voz, que vem como GPT-6 Luna, com o raciocínio
    desligado nos turnos de voz.
  - A chave fica no servidor: ele só abre a sessão (troca a oferta WebRTC do app pela resposta da
    OpenAI) e o áudio vai do app direto para a OpenAI.
  - **Custo:** US$ 0,05 por minuto de sessão, contando o silêncio, mais os turnos do Atlas. Por
    isso, **escolha própria, marcada:** a sessão fecha sozinha depois de 2 minutos calada, além de
    fechar pela frase de parar e pelo botão.
- **Fica de fora:** o telefone (ligar e receber ligação; seria outro plugin, `telefone`, que
  depende deste, e o nó é o endereço público para a operadora alcançar o Atlas); a resposta em
  áudio automática; a transcrição local e as vozes grátis; e a conversa por voz encadeada
  (transcrever, turno, falar), que só faria falta sem a chave da OpenAI.
- **A prova:**
  - testes contra uma OpenAI falsa: o anexo de áudio transcrito uma vez e lido de volta, a falha
    virando nota, o ditado na caixa, a ferramenta de fala chegando como arquivo ao Telegram falso, a
    sessão aberta sem a chave sair do servidor, e uma delegação virando turno com a nota e o modelo
    escolhido;
  - de ponta a ponta no app, com o modelo falso e a OpenAI falsa: o botão abre o modo voz, uma
    delegação aparece como turno e a resposta volta;
  - com a OpenAI de verdade (perguntar antes): um áudio pelo Telegram, um ditado, um áudio pedido, e
    uns 5 minutos de conversa por voz (cerca de US$ 0,25 mais os turnos no GPT-6 Luna). O áudio de
    verdade, falado e ouvido, só o usuário prova.

### Loja (decidida em 24/10, revista em 25/10, passo 16s)

Pedido do usuário em 24/10: um lugar no app para achar e instalar o que outras pessoas fizeram para
o Atlas, separado em skills, MCPs, plugins e executores, com os itens da equipe do Atlas marcados e
uma página para cada item. Hoje só se instala por endereço (16n), sabendo o endereço.

- **Sem servidor nosso por enquanto** (decidido pelo usuário em 24/10): a loja é um repositório
  público no GitHub, sem contagem de downloads e sem estrelas. Os dois vêm depois, com um serviço
  pequeno no servidor de casa do usuário; a tela nasce sem espaço vazio esperando por eles.
- **Quem entra, o usuário decide** (decidido pelo usuário em 24/10): cada item entra por um PR que
  ele revisa e aceita. Mais revisores e mais automação vêm depois.
- **Quatro tipos, separados** (decidido pelo usuário em 24/10: quem quer uma skill baixa só a
  skill, não um plugin que traz skills; revisto com ele em 25/10 para a skill que precisa de uma
  biblioteca ou de um MCP):
  - **Skill:** uma pasta com `SKILL.md` e os arquivos dela. Vai para a biblioteca de skills da
    casa, como as do usuário. Um autor com várias skills tem um item para cada uma. No próprio
    `SKILL.md` (`metadata.requires`, a forma que as skills do Gmail e da Agenda que o Atlas traz já
    usam) ela diz do que precisa:
    - **programas** na máquina (`bins`: `ffmpeg`, `gh`): a loja confere e mostra o que falta; a
      skill instala mesmo assim e continua à vista do modelo, que pode pedir para instalar o
      programa;
    - **MCPs da loja** (`mcp`): instalados junto, cada um como o item que é, com os segredos dele;
    - **sistemas** (`os`: `windows`, `macos`, `linux`): fora deles, a loja não instala.
  - **A biblioteca de um script vai no próprio script, não na máquina:** o Python declara as
    dependências no cabeçalho do arquivo (o bloco `# /// script`, padrão do Python) e roda com
    `uv run`, que monta um ambiente isolado na primeira vez; o Node usa `npx` com a versão exata.
    Nada é instalado no sistema do usuário.
  - **Skill não traz segredo.** Os comandos que o modelo roda nunca veem o cofre (Segredos, na
    arquitetura), então o que precisa de uma chave passa por um MCP, que a recebe, ou vira plugin.
  - **MCP:** um servidor MCP. Vai para o `mcp.json` da casa, com os segredos que ele pede guardados
    no cofre.
  - **Executor:** um plugin que registra um executor. A página diz que programa precisa estar
    instalado na máquina.
  - **Plugin:** o pacote: o que junta mais de um tipo no mesmo item (skills com o seu próprio MCP,
    um gancho, uma tela) ou traz Python. Chega desligado, com o consentimento de sempre ao ligar.
  - O CI da loja confere que o tipo declarado é o que o item traz de fato: uma "skill" com um gancho
    ou com um `.mcp.json` é recusada.
- **O repositório da loja,** `marqueskaio/atlas-marketplace`: um arquivo por item em `items/`, com
  nome, tipo, título, uma frase, autor (o usuário do GitHub, nunca nome real), licença, o
  repositório no GitHub e a pasta, e o **commit fixo**, os 40 caracteres. Um MCP traz a entrada do
  `mcp.json` e os segredos que ela lê; de pacote, com a versão exata (`pacote@1.2.3`), que o
  registro de pacotes não deixa trocar; remoto, com o endereço. Imagem e capturas, só do próprio
  repositório, no mesmo commit. Um arquivo por item, e não uma lista única, para dois PRs abertos ao
  mesmo tempo não brigarem pela mesma linha.
- **O Atlas lê a loja inteira de uma vez,** o repositório num arquivo compactado só, por HTTPS, e
  guarda a última que leu bem. Os itens vêm do GitHub do mesmo jeito, no commit fixado: sem precisar
  de git, e assim também no container. Os endereços ficam em `[marketplace]` no `config.toml`, para
  um espelho (o servidor de casa, depois) e para os testes.
- **O que a revisão garante:**
  - o que o revisor leu é o que instala: o Atlas baixa exatamente o commit fixado. Versão nova é um
    PR novo trocando o commit, e o revisor lê a diferença entre os dois;
  - um item não se atualiza sozinho (baixar código novo por fora da loja) nem traz binário
    compilado, que ninguém consegue ler;
  - segredos só pelo cofre do Atlas; o que ele faz na rede, em segundo plano ou fora da pasta dele
    está escrito no README;
  - **a lista de retirados:** um item tirado por segurança vai para uma lista no repositório, com o
    motivo, e não instala mais. Quem já instalou é avisado, e o item para: o plugin é tirado (os
    dados dele ficam), a skill vai para o arquivo das skills, o MCP sai do `mcp.json`. Vale também
    pelo repositório: tudo o que veio dele para.
- **Oficial:** o selo diz que é da equipe do Atlas, e só o usuário o dá. O que já vem com o Atlas
  aparece na loja com o selo: os quatro executores, o Telegram e a voz (com Ligar no lugar de
  Instalar), e as skills do GitHub, do Gmail e da Agenda. Assim a loja já abre com o que mostrar.
- **No app:** uma entrada "Loja" na barra lateral, junto de Rotinas, que abre na área principal:
  - a busca e as abas Plugins · Skills · MCPs · Executores; em cada aba, primeiro os oficiais,
    depois os da comunidade;
  - o cartão: título, autor, selo, a frase, e o botão no estado certo (Instalar, Ligar, Atualizar,
    Instalado, Retirado);
  - a página do item: o README no commit fixado (a skill mostra o próprio `SKILL.md`, que é o que o
    modelo lê), sem HTML solto, com links e imagens resolvidos no mesmo commit; as capturas, a
    licença, a versão, o repositório, e o que ele pede (programas, MCPs, segredos) antes de
    instalar. Ligar um plugin leva ao consentimento de sempre, na tela de Plugins.
- **Quem busca e instala é o servidor, não o app:** a casa pode estar em outra máquina (passo 15), e
  o item vai para a casa. Sem loja lida nenhuma vez e sem rede, a tela diz que não conseguiu abrir.
  Tudo também pelo terminal (`atlas marketplace`).
- **Atualizar é um botão, nunca sozinho:** aparece quando a loja tem outro commit do que está
  instalado.
  - Plugin e executor: pedem consentimento de novo se o que declaram cresceu.
  - Skill: troca só se a cópia da casa está como veio. Se o usuário ou a revisão mudou a skill, a
    loja avisa e oferece atualizar mesmo assim, com a versão da casa guardada (a biblioteca já
    guarda as versões). A revisão melhora uma skill da loja como melhora as do usuário, e o curador
    nunca a arquiva.
  - MCP: a entrada do `mcp.json` é trocada; uma que o usuário mudou pede a mesma confirmação.
- **Compatibilidade:** um plugin feito para um SDK mais novo (`api`) é recusado ao instalar, com o
  aviso de atualizar o Atlas.
- **No código, `marketplace`** (`atlas.marketplace`): `store` já é o estado dos plugins no servidor
  e o estado das telas no app.
- **Fica de fora:** downloads e estrelas (depois, no servidor de casa); MCP que entra com OAuth (o
  cliente MCP do Atlas ainda não faz OAuth); repositório fora do GitHub; lojas de outras pessoas;
  atualização automática; e o isolamento da tela de plugin num quadro próprio (16m), estudado de
  novo se a revisão humana não bastar.
- **A prova:**
  - testes com uma loja servida por um GitHub falso em HTTP de verdade, os itens tirados de
    repositórios git feitos no teste: a loja lida e guardada, cada tipo instalado no seu lugar, o
    commit fixo conferido, a skill trazendo o MCP de que precisa, um commit novo virando Atualizar,
    a skill mudada na casa sem ser sobrescrita, o item retirado parando, e a rede caída usando a
    cópia guardada;
  - o `atlas marketplace check` do CI da loja recusando um tipo que não bate, um item sem commit
    fixo e um MCP de versão solta;
  - de ponta a ponta no app: abrir a Loja, achar um item em cada aba, abrir a página, instalar,
    ligar e ver funcionar.

### Ditado em qualquer lugar (decidido em 25/10, passo 17b)

Pedido do usuário em 24/10: ditar em qualquer campo do computador, como num programa de ditado, e
não só na caixa do Atlas e do bot. A aba Executores nem tem o 🎤. Pesquisado em 24/10: os programas
de ditado seguram uma tecla para falar, usam dois toques para o modo mãos-livres, mostram uma
barrinha da gravação sem roubar o foco, colam pela área de transferência com um Ctrl+V apertado
por eles e devolvem depois o que estava copiado, e guardam o histórico; há os que fazem isso em
Electron, como o app. Os harnesses de agente não ditam fora da própria janela.

- **Continua no plugin `voice`, e o núcleo não ganha voz.** As telas de plugin ganham três aberturas
  novas, que o consentimento mostra: um atalho global, colar no programa em foco e uma barrinha
  flutuante.
- **Clicar para falar, clicar para parar** (decidido pelo usuário em 25/10): o primeiro toque no
  atalho grava e o segundo para, transcreve e cola. Usa o atalho global do próprio Electron. Segurar
  e soltar, e os dois toques do mãos-livres, ficam para depois: o Electron só sabe quando a tecla
  desce, e saber quando ela sobe pediria uma biblioteca nativa (`uiohook-napi`). Sem essa biblioteca,
  um atalho feito só de modificadores (Ctrl+Win) também não dá.
- **O atalho de fábrica é o Caps Lock** (decidido pelo usuário em 25/10), trocado na tela do plugin.
  O Electron aceita a tecla sozinha (`Capslock`, testado em 25/10). Se outro programa já tiver
  ocupado o atalho, a tela diz isso, em vez de falhar calada.
- **O texto é cru** (decidido pelo usuário em 25/10): sai da transcrição como sai hoje no 🎤, sem
  passar por um modelo que tire os "é…" e as correções. Essa limpeza fica para depois, como opção.
- **Onde o texto cai:** no programa em foco, pela área de transferência e um Ctrl+V simulado, como
  nos programas de ditado. Depois, a área de transferência volta a ter o que tinha. Se o foco estiver no próprio
  Atlas, o texto é digitado onde está o cursor dele, sem passar pela área de transferência (também
  no terminal de um executor).
- **Os ditados ficam na casa** (decidido pelo usuário em 25/10): o áudio e o texto na pasta de dados
  do plugin, que o backup leva. O plugin ganha a página "Ditados", com cada um para ouvir, copiar ou
  apagar, e com a tecla para trocar. Ficam guardados até o usuário apagar. **Escolhas próprias,
  marcadas:** os ditados do 🎤 da caixa também ficam guardados ali; a gravação é guardada antes da
  transcrição, e uma que a OpenAI não escreveu fica com o motivo e um botão "Escrever de novo", para
  a fala nunca se perder.
- **Funciona enquanto o app está aberto**, inclusive minimizado. Fechar a janela fecha o app, e o
  ícone na bandeja fica de fora até fazer falta. Com a casa em outra máquina, a transcrição roda no
  Python do plugin dessa casa, como o 🎤.
- **Escolhas próprias, marcadas:**
  - uma gravação de menos de meio segundo é descartada sem ir à OpenAI (um toque sem querer);
  - uma gravação para sozinha em 10 minutos, porque o `dictate` aceita até 10 MB;
  - o atalho apertado enquanto o texto ainda é transcrito não abre uma gravação nova.
- **Defeitos esperados, cada um com o seu passo na prova:**
  - um programa aberto como administrador não aceita a colagem de um programa comum; nesse caso o
    texto fica copiado e a barrinha avisa;
  - o Caps Lock pode mudar entre maiúsculas e minúsculas além de gravar, o que a prova confere
    (**confirmado em 25/10**: o toque que grava também trocava as maiúsculas; agora o app aperta o
    Caps Lock mais uma vez, com o atalho solto nesse meio tempo, e as maiúsculas ficam como estavam);
  - uma colagem que acontece antes de a área de transferência mudar cola o texto velho, então a
    prova cola em um campo de verdade e lê o que caiu ali.
- **A prova:**
  - testes do app com o atalho, a colagem e a barrinha falsos: o ciclo de gravar, parar, transcrever
    e colar, o toque curto descartado e o atalho ocupado;
  - testes do Python contra a OpenAI falsa: o ditado guardado com o áudio, listado e apagado;
  - de ponta a ponta no app, com o microfone falso do Chromium: o atalho grava, o texto cai num campo
    de outra janela e o ditado aparece na página "Ditados";
  - com a OpenAI de verdade (perguntar antes, menos de US$ 0,01): um ditado no Bloco de Notas, outro
    no terminal de um executor e outro numa janela do navegador.

### Site e documentação pública (decididos em 25/10, passo 19)

Pedido do usuário em 25/10: um site com uma home bonita, uma página de novidades e uma documentação
muito boa, que sirva também de fonte para o próprio Atlas responder sobre si. Hoje o `docs/` é
documentação de decisão, escrita para quem contribui ("por que decidimos X em 13/10"). Quem usa o
Atlas precisa de outra: "como faço X".

- **Três partes:** a home, as novidades e a documentação. O site também é a página de download do
  passo 18: os quatro caminhos com o comando para copiar, servindo o `install.sh` e o
  `install.ps1`. O `download.html` sai.
- **Astro com Starlight, numa pasta `site/` deste repositório.** O Starlight traz a documentação
  (busca local com Pagefind, idiomas e tema escuro), e o Astro deixa a home e as novidades livres.
  O Node já é do app, então não entra outra linguagem. No mesmo repositório, o commit que muda um
  comportamento muda também a página que o explica.
- **Hospedado na Cloudflare** (Workers com arquivos estáticos), com deploy a cada push na `main`.
  O GitHub Pages não deixa pôr cabeçalhos nem responder em Markdown, e os dois contam para os
  agentes (abaixo). O domínio ainda está em aberto.
- **A documentação é escrita do zero, para quem usa**, organizada pelo que a pessoa quer fazer:
  - Começar: instalar, a chave e a primeira conversa;
  - Guias: executores e contas, várias máquinas, ditado, rotinas, bots, plugins, memória,
    navegador e atualizações;
  - Como funciona: o laço, a memória, a aprovação e as skills, na língua de quem usa;
  - Referência: a CLI `atlas`, as configurações, as variáveis de ambiente, as ferramentas e o
    glossário;
  - Contribuir: links para o `docs/` de hoje, que fica como está.
- **A documentação não envelhece:**
  - a referência é gerada do código, como o `gen_protocol.py`, e o CI falha se ela não bater com o
    código;
  - as imagens do app saem do e2e, que já abre o app de verdade com o modelo falso;
  - link quebrado ou comando de exemplo que não roda falha o CI;
  - uma página que explica um comportamento muda no mesmo commit que muda o comportamento.
- **O Atlas lê a própria documentação.** O modelo não conhece o Atlas, que é mais novo que o
  treino dele, e "como faço X aqui?" é das perguntas mais comuns. Por isso as páginas vão, em
  Markdown, dentro de cada versão instalada: o Atlas responde sobre a versão que está rodando, sem
  internet. Ele as lê por uma skill embutida, que custa uma linha até ser usada (a escada de
  integração). Ele não lê o `docs/ARCHITECTURE.md`, que é longo, conta a história das decisões e
  confundiria a resposta. Uma resposta ruim sobre o próprio Atlas aponta a página que falta.
- **Pronto para agentes**, medido pelo Is It Agent Ready da Cloudflare (`isitagentready.com`):
  - `robots.txt`, `sitemap.xml` e cabeçalhos `Link`;
  - cada página também em Markdown, entregue quando o pedido manda `Accept: text/markdown`;
  - `Content-Signal` e as regras dos robôs de IA no `robots.txt`, decididos pelo usuário antes de
    publicar;
  - a mesma skill da documentação publicada em `/.well-known/agent-skills/`, e o `llms.txt`;
  - fica de fora o que não é do Atlas (OAuth, catálogo de API, comércio), mesmo que baixe a nota.
- **Duas listas de novidades:**
  - **Versões:** o que muda no Atlas, gerado dos assuntos dos commits e do `Notes-pt-BR`, o mesmo
    dado da janela de atualização;
  - **News:** notícias de fora (modelos, agentes de código, MCP) num card com título, fonte, data,
    um resumo e o link para a notícia original, como fazem os sites de notícia de agentes. O resumo
    é escrito com palavras nossas e traz uma linha sobre o que a notícia muda no Atlas. Uma rotina do
    próprio Atlas lê as fontes e abre um PR com o rascunho, e o usuário aprova antes de publicar.
  - As duas listas têm RSS.
- **A home precisa ser linda:** interativa, respondendo ao mouse e à rolagem, e boa no celular. Ela
  sai como HTML estático, e só as partes animadas levam JavaScript. Regras:
  - o texto fica no HTML, não dentro do canvas, para agentes e buscadores lerem;
  - no celular, a cena responde à rolagem e ao toque;
  - com `prefers-reduced-motion`, a página fica parada;
  - a cena carrega depois do texto, e a página passa no Lighthouse.
  Antes de construir, três direções em protótipo, e o usuário escolhe uma.
- **Idiomas:** inglês primeiro, como o resto do projeto, e o português depois, começando pela home
  e pelo Começar.
- **A ordem:**
  1. os protótipos da home;
  2. o esqueleto: o site na Cloudflare, a home, a instalação, e o `robots`, o `sitemap` e o
     Markdown para agentes;
  3. a documentação essencial e a referência gerada;
  4. a skill da documentação no Atlas e em `/.well-known/`;
  5. as Versões e as News;
  6. o português.
- **A prova:** o site no ar com o deploy pela `main`, a nota do Is It Agent Ready e do Lighthouse
  anotadas aqui, a referência conferida pelo CI, e o Atlas respondendo "como ligo uma segunda conta
  do Claude Code?" só pela documentação da versão instalada.
- **A direção da home, escolhida pelo usuário em 25/10** entre os protótipos: as linhas que reagem ao
  mouse e as órbitas da primeira direção, com as conexões da segunda. A terceira, clara e com
  serifa, ficou de fora: parecia o site de outra empresa.
- **O conceito: um planetário mecânico** (o instrumento de latão que segura os planetas em braços).
  Tudo o que a cena mostra é verdade sobre o Atlas:
  - o Atlas é o centro;
  - os executores são planetas nas órbitas, ligados ao centro por braços;
  - as contas são luas do seu executor;
  - a rota é um arco de transferência entre órbitas, com um cometa que passa de uma conta à outra;
  - as outras máquinas são outros sistemas, ligados por sinal;
  - o que o Atlas aprende entra no centro como partículas.
  O logo é o planetário fechado: a página abre a esfera em órbitas ao rolar e a fecha de novo no
  fim, onde o texto diz que tudo o que ele sabe fica com você.
- **O desenho:**
  - **cores:** Noite `#05070F`, Tinta `#0D1322`, Luz `#E8ECF4`, Névoa `#8D96AB`, Latão `#E7B36A`
    (o Atlas e as rotas) e Sinal `#86B6FF` (só as outras máquinas);
  - **letra:** uma família só, a Mona Sans, com o eixo de largura dando o papel: larga nos títulos,
    normal no texto, estreita nos rótulos do planetário; a Martian Mono só nos comandos; as duas sob
    a SIL Open Font License, servidas pelo próprio site;
  - **movimento:** um momento só ao abrir (as linhas crescem do meio e o título se alarga), e o resto
    responde ao mouse e à rolagem;
  - **o ícone do app** (pedido do usuário em 25/10): a marca do logo como o site a desenha, em Luz,
    num quadrado de cantos redondos com a Noite e o Latão do Atlas brilhando atrás dela. Nos tamanhos
    pequenos as linhas engrossam, porque abaixo de um pixel sumiriam. A fonte é
    `apps/desktop/resources/icon.svg`, e o `npm run icons` desenha com o Electron o `.ico` (de 16 a
    256 px) e o `.png`. A janela, a barra de tarefas, o Dock, o atalho do Menu Iniciar e a entrada do
    Linux usam ele no lugar do ícone do Electron;
  - fora: rótulo em caixa-alta acima de título, palavra colorida no título, seta nos botões, grade de
    cards e numeração que não seja sequência de verdade.
- **Como vai ao ar:** a Cloudflare constrói o site a cada push na `main` (Workers Builds, ligado ao
  repositório pelo painel), na pasta `site`, com `npm ci && npm run build` e `npx wrangler deploy`.
  O `site/wrangler.jsonc` já diz o resto. `SITE_URL` recebe o domínio quando ele existir.
- **O Atlas lendo a própria documentação** (pedido do usuário em 25/10, depois da parte 1: "a doc
  já está acessível para o Atlas da melhor forma?"; não estava: o Atlas não sabia que ela existia,
  o container nem a tinha, e eram 4 páginas). Decidido e feito em 25/10, na escada de integração
  (programa de terminal com skill):
  - **as páginas moram no pacote**, `src/atlas/docs/pages/`, e o site as lê de lá: o checkout, a
    imagem do container e o app empacotado levam as da versão que rodam;
  - **`atlas docs`** lista as páginas, mostra uma, busca por seção (todas as palavras; sem
    nenhuma seção com todas, as que têm mais) ou mostra tudo, sem abrir casa;
  - **a skill embutida `atlas`**, uma linha no prompt: para pergunta sobre o próprio Atlas, ler
    `atlas docs` antes de responder, nunca de memória;
  - **a referência gerada do código** (`scripts/gen_docs.py`): os comandos, do parser da linha de
    comando, e as configurações, das dataclasses e do comentário `#:` de cada campo; um teste
    falha quando a página não bate com o código;
  - **mais páginas:** `MEMORY.md`, `TOOLS.md`, `BOTS.md`, `PLUGINS.md` e `BACKGROUND_REVIEW.md`
    saíram de `docs/` e viraram páginas (os READMEs apontam para elas), e entraram duas novas: o
    portão de aprovação, e de onde vem cada dado do Claude Code (o pedido de 18/10).
  - **Uma promessa que mudou:** foi dito ao usuário que a skill não seria editável pela revisão em
    segundo plano. A arquitetura decidiu em 09/10 que a revisão pode melhorar qualquer skill
    (proteger as skills a faz recusar escritas e criar quase-duplicatas), então a skill ficou só
    um ponteiro: a documentação fica no pacote, onde a revisão não escreve.

### Revisão de UI/UX do app (decidida em 25/10, passo 9d)

Pedida pelo usuário em 25/10, a partir de um roteiro de auditoria: o app inteiro revisto como
produto, para parecer mais rápido, ser mais fácil de entender, pedir menos esforço e passar mais
confiança. **Sem redesenho:** polimento, fluidez, percepção de velocidade e intuição.

- **O diagnóstico** (o app de verdade com o modelo falso, 87 prints em claro e escuro, em inglês e
  em português e numa janela estreita; o código das telas; a linha de base com o `npm run check` e
  o e2e inteiro verdes):
  - nenhum P0: nem falha de segurança nem perda de dados;
  - P1, a tela dizia o que não aconteceu: "rodando" numa ferramenta parada à espera de aprovação,
    resposta ou chave; na sessão de um executor, "cancelled" no comando que esperava permissão;
    "Ran" para um comando parado ou recusado; "nenhuma conversa ainda" enquanto o servidor subia;
    a conversa nova fora da lista durante a primeira resposta (o servidor só lista conversa com
    título, e o título era gravado depois da leitura da lista); um erro que pede ação em cinza
    miúdo, igual a "Parado."; o texto digitado numa conversa seguindo para a outra na troca;
  - P2 e P3: cores com sentidos trocados e nenhuma cor com nome (por isso as bordas pretas do
    Markdown, que pinta com cores de nome que o app nunca definiu); cinco desenhos de cartão para
    a mesma ideia; JSON cru nas ferramentas do Atlas; a sessão do executor relida a cada troca; o
    "compactar antes de seguir" fora da tela desde 22/10; o modelo e o esforço que não atravessam
    de harness; foco do teclado, contraste e "reduzir movimento".
- **Decidido pelo usuário em 25/10:**
  - direção A: o app de hoje, em preto e branco (claro e escuro), sem as cores do site; as cores
    ganham nome num arquivo só, para a personalização de cores que ele quer pôr depois;
  - uma cor por estado: azul pulsando é trabalhando, na trilha e na lista; verde, só terminou;
    âmbar, só espera o usuário; vermelho, falhou; não lido vira negrito; o "Auto" ligado sai do
    âmbar;
  - conversas além das 50 últimas, busca, renomear e arquivar ficam para o passo seguinte;
  - abas no topo da área central para as conversas do Atlas, dos bots e as sessões de executor:
    aba de passagem, como nos editores de código (o clique simples reaproveita uma aba em itálico,
    que fica quando a conversa é usada), e uma faixa por casa;
  - a conta e a rota da sessão refeitas como um caminho, com o "compactar antes de seguir" na
    tela, ligado por padrão, e o modelo e o esforço garantidos em cada troca; sem compactar com
    antecedência (antes de a conta chegar ao limite): a compactação acontece no salto.
- **Os princípios de cada mudança** (dele): o que espera o usuário aparece primeiro e o resto fica
  quieto; um clique a menos no que se repete; movimento curto, coeso e com função; nada muda de
  lugar sem motivo; a tela só diz o que aconteceu.
- **As entregas,** cada uma num commit com os fiscais verdes e prints de antes e depois: 1, o
  estado real do agente; 2, abrir, reconectar e trocar de conversa; 3, erros com saída; 4, base
  visual e movimento; 5, os cartões que pedem o usuário; 6, abas; 7, conta e rota da sessão; 8,
  telas e navegação; 9, acessibilidade, janela estreita e Mac; 10, fechamento. Sobem para a `main`
  em grupos (1 a 3, 4 e 5, 6 e 7, 8 a 10), com o OK do usuário. Nenhuma gasta crédito: tudo roda
  no modelo e nos executores falsos.
- **Pesquisado em 25/10:** o ACP, que a trilha segue, chama de `pending` a ferramenta que espera
  aprovação e liga o pedido de permissão à chamada pelo id; em outros apps de agente, usuários
  relataram que, sem um sinal de "esperando aprovação", o agente parecia trabalhando ou travado; os
  estudos de usabilidade recomendam nenhum indicador abaixo de 1 s e esqueleto em cargas de página
  abaixo de 10 s; nos editores de código, a aba de passagem, Ctrl+1 a 9 e Ctrl+W; e, nos apps de
  agente com abas, os defeitos de abas em branco ao reabrir e de abas fechadas que voltam, que
  viram testes aqui.
- **Ficam de fora:** trocar a pilha (React, Tailwind, Radix) ou a disposição da tela; uma contagem
  regressiva no cartão de aprovação; os motivos do portão em português, que vêm do servidor.
- **Entrega 1, o estado real do agente (25/10):** a chamada presa num cartão aparece esperando o
  usuário, com o ponto âmbar parado e o que ela espera, nunca rodando. O pedido de aprovação passou
  a levar o id da chamada (`call_id`, campo novo e compatível), que o servidor lê do próprio laço,
  porque o portão roda dentro da chamada que julga; a pergunta é a chamada `ask_user`, a chave a
  `secrets`, e o pedido de um executor a chamada da sessão que pediu. O título das ferramentas do
  Atlas diz o que a chamada fez: "Rodou", "Rodando", e "Rodar" quando ela espera ou foi recusada ou
  parada antes. Os três pontinhos só aparecem quando nada mais mostra trabalho, e a sessão de um
  executor lida enquanto ele trabalha deixa o turno aberto. Provado no pytest (o gateway manda o id
  da chamada; cada chamada do laço tem o próprio id, mesmo rodando juntas), no Vitest e no e2e (a
  aprovação esperando e depois "Rodou"; a recusada como "Rodar … recusada"; a pergunta esperando; o
  comando do executor esperando a permissão). Fiscais: 2.688 testes do Python com 91,46% de
  cobertura, o `npm run check` e os 67 cenários do e2e.
- **Entrega 2, abrir, reconectar e trocar de conversa (26/10):** uma lista diz que está lendo até
  ser lida, com linhas cinzas depois de 400 ms (uma leitura rápida não pisca nada), e nunca "nenhuma
  conversa" antes disso; enquanto o servidor sobe, o meio diz "Abrindo o Atlas…" e a faixa fica só
  para a conexão perdida ou uma falha; a conversa aberta pela primeira vez mostra linhas cinzas, não
  o convite. O servidor grava o título da conversa nova, as primeiras palavras dela, quando a
  primeira vez começa (`name_untitled`): a lista só mostra conversa com título, e a conversa ficava
  fora dela enquanto a primeira vez esperava uma aprovação. A mensagem enviada aparece na hora, mais
  clara, até o servidor mostrar a dele; num turno em andamento, diz que entra quando o passo atual
  terminar. O texto digitado fica na própria conversa (um Enter mandava para a próxima o que foi
  escrito para outra), e o ponto onde se estava lendo também. Provado no pytest (a lista lida no
  começo do turno traz a conversa, e vinha vazia sem a correção), no Vitest e no e2e (reabrir nunca
  diz "nenhuma conversa" e diz "Abrindo o Atlas…"; a conversa nova na lista esperando a aprovação;
  o texto de cada conversa), que falham no app de antes. Achado nos fiscais: um teste dos títulos
  esperava as palavras do título como sinal de que o modelo tinha respondido, e o título provisório
  já tem as mesmas palavras; agora espera o título do modelo. Fiscais: 2.690 testes do Python com
  91,46% de cobertura, o `npm run check` (411 do Vitest) e o e2e.
- **Entrega 3, erros com saída (26/10):** o turno que falhou é um erro com as palavras do servidor
  e, sendo a última fala da conversa, oferece as saídas que existem: devolver a mensagem à caixa,
  trocar de modelo e, quando a chave é o que falhou (nenhuma, recusada ou sem crédito), conectar
  uma; nada é reenviado sozinho, o que gravaria a mensagem duas vezes. As listas da barra, as
  propostas e as rotinas liam em silêncio e, falhando, pareciam vazias ou velhas: agora dizem por
  quê, no lugar, com "Ler de novo" (`$readError`). O cartão respondido sumia na hora, até sem
  conexão para levar a resposta, e voltava depois: agora diz que a resposta está a caminho até o
  `request.cancel` do servidor; a reconexão o tira, e o pedido que chega de novo aceita resposta.
  Provado no Vitest (as saídas do turno, o erro das telas, o cartão respondido e a reconexão) e no
  e2e (no turno sem chave, "Devolver à caixa" traz a mensagem e "Conectar uma chave" abre a seção
  Modelo). Fiscais: 2.690 testes do Python com 91,45% de cobertura, o `npm run check` (417 do
  Vitest) e os 70 do e2e.
- **Entrega 4, base visual e movimento (26/10):** as cores ganharam nome num arquivo só
  (`src/styles.css`), para o claro e o escuro: fundos, textos, bordas, o preto e branco dos botões
  e uma cor por estado (azul pulsando, trabalhando; âmbar, esperando o usuário; verde, terminou;
  vermelho, falhou), mais `danger` para o botão que apaga e `added` e `removed` para as linhas de
  uma mudança. As cerca de 1.400 cores escritas direto em 51 arquivos viraram esses nomes, por uma
  tabela de conversão revista à mão; um teste barra cor de paleta, cor escrita e cor só do escuro
  numa tela (`colors.test.ts`), e só guardam as próprias cores a marca do fabricante de cada
  executor, o tipo do arquivo no chip e o rosto do bot. Os blocos de código e as tabelas das
  respostas, que pintam com esses nomes e saíam com borda preta, agora seguem o tema. O roxo, o
  laranja e o azul-céu dos botões e links viraram preto e branco (o link ganhou sublinhado); o ponto
  de trabalhando da lista ficou azul como o da trilha; a conversa não vista ganha o título em
  negrito, e o ponto fica para o que junta conversas (as abas, uma pasta fechada); o "Auto" ligado
  deixou o âmbar (um ponto cheio); a faixa da conexão ficou neutra, com o ponto azul enquanto
  reconecta e vermelho quando cai; os cartões que pedem o usuário ficaram âmbar; o normal (servidor
  conectado, chave guardada) não tem cor. O foco do teclado é um anel cinza igual em todo controle,
  no lugar da cor de destaque do sistema. Movimento: uma curva e três durações (120, 180 e 260 ms);
  as cores de um controle mudam suave, o que abre e fecha desliza, e o que chega na conversa à
  vista, um cartão e a mensagem a caminho sobem 4 px no lugar, enquanto a conversa que se abre já
  aparece pronta (`data-settled`); com "reduzir movimento" no sistema, nada se mexe. Provado no
  Vitest (as cores com nome, o não visto em negrito, a conversa que abre inteira) e no e2e (o que
  chega sobe e a conversa aberta não, teste que falha sem a regra; com "reduzir movimento", nada se
  mexe; uma resposta longa não segura quadro além de 250 ms, e aqui nenhum passou de 50 ms).
  Fiscais: 2.690 testes do Python com 91,46% de cobertura, o `npm run check` (422 do Vitest) e os
  73 do e2e; um deles falhou uma vez só ao apagar a pasta temporária, que o Gemini CLI desta
  máquina ainda escrevia, e passou nas três vezes seguintes.
- **Entrega 5, os cartões que pedem o usuário (26/10):** os cinco cartões que esperam o usuário
  (a aprovação, a pergunta e a chave do Atlas, e a permissão e a pergunta de um executor) tinham
  três desenhos; agora têm uma moldura só (`card-frame.tsx`): âmbar, a pergunta em palavras no
  título ("Rodar este comando?", "Claude Code quer rodar um comando"; uma ferramenta sem palavras é
  nomeada), exatamente o que vai rodar ou mudar, e as escolhas numa ordem só, a que segue primeiro e
  a que recusa por último, com o campo do motivo embaixo. Com o teclado no cartão, os números
  escolhem, e cada escolha mostra o seu, claro; um clique no cartão dá o teclado a ele, e nenhum
  cartão o toma sozinho: o campo da chave só pega o foco quando nada mais o tem, e o que se
  digitava para uma mensagem não cai mais na chave. A pergunta de um executor ficou como as do
  Atlas ("Outro" abre o campo), e "Dispensar" virou "Pular". A aprovação que ninguém responde no
  `[trust] approval_timeout` termina num estado novo da chamada, `unanswered`: a trilha diz "sem
  resposta a tempo", não "recusada", e o modelo lê o mesmo texto; o portão marca essa recusa
  (`Unanswered`) e o laço lê a marca. Provado no pytest (só o silêncio é marcado, e o laço termina
  essa chamada sem resposta), no Vitest (os títulos, a ordem e as teclas, o teclado deixado onde o
  usuário digita) e no e2e (o 1 permite uma vez; a aprovação deixada sozinha some depois dos seus
  10 segundos, e a chamada diz "sem resposta a tempo", com a pasta ainda lá). Fiscais: 2.692 testes
  do Python com 91,46% de cobertura, o `npm run check` (430 do Vitest) e os 74 do e2e.
- **Entrega 6, abas (26/10):** as conversas, as dos bots e as sessões dos executores abertas numa
  casa ganharam abas no topo da área central (`store/tabs.ts`, uma faixa por casa). O clique numa
  linha abre na aba de passagem, em itálico, que o próximo clique reaproveita; ela fica quando é
  usada (uma mensagem enviada) ou com um clique duplo, na aba ou na linha, e o que uma primeira
  mensagem cria abre numa aba própria. A aba na tela é a que guarda o que a tela mostra, nunca um
  estado à parte: o clique numa notificação ou na linha de uma tarefa delegada mostra a aba dela, e
  a aba de uma sessão acompanha os ids que o harness dá e a troca de conta. Cada aba tem o ponto do
  estado e o título em negrito enquanto o fim não foi visto; a conversa nova ainda não criada
  aparece como aba; à direita, quantos cartões esperam longe da tela, e o clique leva ao primeiro.
  Ctrl+1 a 8 mostram uma aba e Ctrl+9 a última, Ctrl+Tab a próxima, Ctrl+W fecha a da tela e mostra
  a seguinte, tudo deixado para o shell dentro do terminal; arrastar muda a ordem, e o clique do
  meio fecha. A janela guarda as abas de cada casa por máquina e nome, e a casa reabre nelas e na
  que estava na tela, já lida; a aba fechada não volta, e a de uma conversa apagada fecha ao abrir.
  A sessão de executor já vista mostra o rastro na hora enquanto é relida, e a primeira mensagem
  enviada enquanto o usuário foi para outro lugar não o puxa mais de volta. Achado nos fiscais: o
  "+" das abas e o botão da barra lateral tinham o mesmo nome, "Nova conversa", o que confunde o
  leitor de tela e quebrava os testes que procuram esse botão; o "+" virou "Nova aba". No Mac, o
  Cmd+W ainda fecha a janela, pelo menu padrão do Electron: fica para a entrega 9. Provado no Vitest
  (a aba de passagem, fechar para a seguinte, a sessão seguida ao ganhar id, as abas guardadas por
  casa e lidas com tolerância, as teclas, a conta do que espera) e no e2e (a linha na aba de
  passagem e a seguinte tomando o lugar, a mensagem que a fixa, Ctrl+1, Ctrl+Tab, Ctrl+W e o
  arrasto; e, reaberto, as mesmas abas, a da tela lida, a fechada fora). Fiscais: 2.692 testes do
  Python com 91,49% de cobertura, o `npm run check` (444 do Vitest), o do site e os 76 do e2e.
- **Entrega 7, conta e rota da sessão (26/10):** a rota da sessão virou um caminho, acima da caixa:
  as paradas em ordem, ligadas, a de agora marcada, cada uma dizendo se pode assumir (instalada, com
  login, fora do próprio limite, que o servidor agora diz como `limited`) e quanto do plano já foi
  usado, com o modelo e o esforço que a sessão leva para lá ("como agora" até escolher; noutro
  harness, "o padrão dele"), e duas chaves: seguir o caminho no limite, e "compactar antes de
  seguir", ligada por padrão (migração 11). O modelo e o esforço atravessam toda troca: a parada
  pode nomear os seus; sem isso, a sessão fica no modelo escolhido ou, no padrão do harness, no
  modelo em que rodava, porque o padrão de outra conta pode ser outro, e no esforço em que rodava,
  encaixado nos níveis do modelo de lá; noutro harness, o esforço segue do mesmo jeito e o modelo é
  o padrão de lá. Um modelo que o destino não oferece cede ao padrão, e a sessão diz isso. A
  compactação roda só onde o harness tem `/compact` (o Claude Code; o Codex e o DeepSeek não recebem
  comando de barra, e o do Gemini CLI é outro), na troca e nunca antes do limite; se falhar, a
  sessão diz e segue como estava, em vez de parar. A troca de uma tarefa delegada faz o mesmo, e
  depois de atravessar de harness a caixa já oferece os modelos do novo. Provado no pytest (a parada
  dando o modelo e o esforço, o esforço encaixado, o modelo não oferecido cedendo ao padrão com o
  aviso, o padrão mantido; a compactação só onde há `/compact` e a falha que não para; a ida ao
  Codex levando o esforço; a rota guardada com modelo, esforço e compactar), no Vitest (o caminho
  com prontidão, uso, modelo, esforço e compactar) e no e2e (a parada adicionada dizendo que está
  pronta, com compactar ligado). Fiscais: 2.700 testes do Python com 91,45% de cobertura, o `npm run
  check` (445 do Vitest), o do site e os 76 do e2e.
- **Entrega 8, telas e navegação (26/10):** cada uma das ferramentas do Atlas ganhou título no
  rastro, no lugar do JSON cru que a revisão achou: "Perguntou" e a pergunta, "Delegou:" e o
  objetivo, "Escreveu" e o arquivo, "Agendou" e a rotina, "Pediu a chave" e o nome, "Buscou nas
  ferramentas" e a busca, o passo do navegador pelo comentário que o próprio código traz, e um verbo
  por ação nas que têm várias (rotinas, tarefas delegadas, skills, memória); o que é texto (uma
  pergunta, um objetivo) sai em letra de texto, o resto em letra de código, e o verbo segue o estado
  da chamada, como já faziam o comando, a leitura e a busca. As de MCP e de plugins, e uma chamada
  cujos argumentos não nomeiam nada, ficam com o resumo de antes. As páginas próprias (rotinas,
  propostas, loja) ganharam um cabeçalho só (`PageHeader`): título, para que servem, os controles
  delas à direita e a volta à conversa sempre no mesmo lugar. Na aba de executores, "Nova sessão"
  virou o primeiro botão, como "Nova conversa" e "Novo bot" nas delas, com a escolha do executor
  quando há mais de um, e "Adicionar uma conta" ficou embaixo dele, mais discreto, ainda acima da
  lista, como o usuário pediu em 19/10. A tela da chave abre primeiro o OpenRouter, que ela mesma
  chamava de "uma boa primeira escolha" em segundo lugar, debaixo de outro aberto. Provado no Vitest
  (os títulos de cada ferramenta e o resumo genérico mantido, a nova sessão pelo primeiro botão, o
  provedor primeiro e aberto) e no e2e (a pergunta e a tarefa delegada achadas pelo título).
  Fiscais: 2.700 testes do Python com 91,44% de cobertura, o `npm run check` (447 do Vitest), o do
  site e os 76 do e2e.
- **Entrega 9, Mac, janela estreita e acessibilidade (26/10):** no Mac, o Cmd+W fecha a aba da tela,
  e não a janela: o processo principal pega a tecla antes do menu padrão do Electron, que fecharia a
  janela (`electron/keys.ts`), e avisa a janela; sem aba na tela, a janela fecha, como num
  navegador. O Cmd+1 a 9 já valiam desde a entrega 6, e o Cmd+Tab é do sistema, então a próxima aba
  segue no Ctrl+Tab. O atalho da barra lateral aceita Cmd+B no Mac, e o rótulo dos botões diz ⌘B lá
  e Ctrl+B nos outros sistemas; o do terminal segue igual em todos. Na janela estreita, a dica longa
  da caixa ("Corrija o rumo: isto entra depois das ferramentas em curso") ficava cortada ao meio
  numa segunda linha: agora fica numa linha só, com reticências. Conferido na acessibilidade: nenhum
  texto usa a cor `faint`, só enfeites; o foco do teclado, o "reduzir movimento", os números dos
  cartões com `aria-keyshortcuts` e as abas com o foco que anda pelas setas vieram das entregas
  anteriores. Provado no Vitest (o Cmd+W só no Mac, sem outro modificador; a aba da tela fechada
  pelo aviso do processo principal, e a janela quando não há aba) e no print da janela estreita.
  Fiscais: 2.700 testes do Python com 91,44% de cobertura, o `npm run check` (450 do Vitest), o do
  site e os 76 do e2e.
- **Entrega 10, fechamento (26/10):** a revisão termina nas nove entregas acima, cada uma num commit
  com os fiscais verdes, sem gastar crédito. Cada achado do diagnóstico teve a sua: a ferramenta
  parada à espera do usuário, o "cancelled" e o "Ran" do que não rodou (1); o "nenhuma conversa" ao
  abrir, a conversa nova fora da lista e o texto que seguia para outra conversa (2); o erro que pede
  ação em cinza miúdo (3); as cores sem nome e de sentido trocado, as bordas pretas do Markdown, o
  foco do teclado e o "reduzir movimento" (4); os cinco desenhos de cartão (5); a sessão do executor
  relida a cada troca, que agora mostra o rastro na hora (6); o "compactar antes de seguir" fora da
  tela e o modelo e o esforço que não atravessavam de harness (7); o JSON cru nas ferramentas do
  Atlas (8); e o contraste (9). Fica para depois: conferir num Mac de verdade o Cmd+W, o Cmd+B e os
  rótulos com ⌘, que o Vitest prova, mas nenhum Mac rodou nesta revisão; o uso do usuário no app; e
  o passo 9e, decidido em 25/10, com as conversas além das 50 últimas, a busca, renomear e arquivar.
  Continuam de fora a contagem regressiva no cartão de aprovação e os motivos do portão em
  português. Nos fiscais, sem relação com a revisão, três falhas de uma vez só: o teste das outras
  contas de um executor pelo socket passou do tempo; o que apaga a sessão de um executor achou o
  arquivo dela ainda preso pelo Windows (`WinError 32`); e uma rodada caiu inteira porque o Windows
  deixou de abrir processos com a máquina carregada (`0xC0000142`). Os dois testes passaram sozinhos
  três vezes, e cada rodada refeita passou. Fiscais: 2.700 testes do Python com 91,43% de cobertura,
  o `npm run check` (450 do Vitest), o do site e os 76 do e2e.
- **Achado ao atualizar (26/10):** o botão de atualizar do app falhou na busca, com o erro do Python
  nas Configurações: o título da entrega 9 tem um ⌘, e o `python -m atlas.installation check`
  escrevia a resposta na página de código ANSI do Windows (cp1252), que não tem esse caractere. O
  mesmo defeito estragava, desde o começo, os acentos das notas em português na janela de
  atualização, que o app lê como UTF-8. Agora a busca escreve em UTF-8, qualquer que seja o console.
  A instalação de antes da entrega 9 não passa desse título pelo botão, porque quem olha é o código
  dela: atualiza uma vez pelo `atlas update` num terminal, onde o console aceita qualquer caractere,
  e o botão volta. Provado no pytest: a busca rodada num pipe em cp1252, com um título com ⌘ e um em
  português, lida inteira como UTF-8; o teste falha sem a correção. Fiscais: 2.701 testes do Python
  com 91,44% de cobertura, o `npm run check` (450 do Vitest), o do site e os 76 do e2e.
- **Entrega 1 do 9e, mais de 50 conversas (27/10):** a lista mostrava só as 50 conversas de
  atividade mais recente, e as outras não tinham como aparecer. Agora o `conversation.list` vem em
  páginas de 50, cada uma terminando num cursor (a hora da atividade e o id da última linha), e a
  seguinte começa logo depois dele; a barra lateral lê a próxima página quando a rolagem chega
  perto do fim, ou na hora, se a lista não enche a altura. Uma conversa que ganha atividade no meio
  sai da parte ainda não lida e entra na já lida, então nenhuma se repete nem fica de fora: a
  releitura da lista, a cada mudança, pede tantas linhas quantas o app já tem (até 1.000), e uma
  página nunca cruza uma releitura, uma espera a outra. A página que falha diz por quê no fim da
  lista, com "Ler de novo", e não tenta sozinha. O app de antes, que não manda cursor, recebe a
  primeira página, como sempre; um servidor de antes, que não manda cursor, encerra a lista nessa
  página. Provado no pytest (cada conversa uma vez e em ordem, com atividade no meio e empate de
  hora; o cursor que o servidor não deu é recusado), no Vitest (a página que espera a releitura, a
  releitura do tamanho da lista, a falha e o botão, a rolagem perto do fim) e no e2e (60 conversas
  e uma nova começada antes de ler o resto: as 61 na lista, uma vez cada, em ordem). Prints de
  antes e depois no claro e no escuro, em inglês e em português. Na primeira rodada do e2e, com o
  pytest rodando junto, três testes de outros assuntos (duas sessões de executor e o movimento ao
  abrir uma conversa) falharam pela carga; passaram sozinhos duas vezes, e a rodada inteira refeita
  passou. Fiscais: 2.707 testes do Python com 91,48% de cobertura, o `npm run check` (456 do
  Vitest) e os 77 do e2e.
- **Entrega 2 do 9e, a busca (27/10):** acima da lista, um campo "Buscar nas conversas" acha as
  conversas da lista cujo título, ou uma das mensagens, tem todas as palavras digitadas, também
  como começo de uma palavra maior ("quok" acha "quokka"), sem diferença de maiúsculas nem de
  acentos (`conversation.search`). Não há índice novo: as mensagens vêm pelo FTS5 que a busca do
  agente já usa, e os títulos são comparados como estão, palavra por palavra, do jeito que o
  tokenizador do índice compara; assim acha também o título que o modelo de título escreveu e que
  nenhuma mensagem tem. Uma busca com operador (`AND`, `OR`, `NOT`) fica só com o índice. Os achados
  pelo título vêm primeiro, da atividade mais recente para trás, depois os achados por uma mensagem,
  a melhor primeiro, até 50, e a linha de cima diz quantos são, ou que há mais e que outra palavra
  estreita a busca. Cada resultado mostra onde bateu, as palavras em negrito: no título, ou no
  trecho da mensagem, com quem disse ("Atlas:", "Você:"); o servidor já manda o texto em pedaços,
  com marcas que ninguém digita, para nenhum caractere de uma mensagem passar por marca. O app busca
  250 ms depois da última tecla e só mostra a resposta das palavras mais recentes; os resultados
  ocupam o lugar da lista, que fica escondida onde estava; o clique abre a conversa, Enter abre a
  primeira, e Esc limpa a busca e devolve a lista. Provado no pytest (o título sem maiúsculas nem
  acentos, a mensagem por uma palavra começada, todas as palavras no título ou numa mensagem, os
  títulos primeiro, só as conversas da lista, o "há mais", a marca que uma mensagem tinha), no
  Vitest (a busca depois da pausa, a resposta velha que chega depois da nova e é descartada, o Esc
  que larga a resposta a caminho, a falha com "Ler de novo"; o campo, os resultados no lugar da
  lista, o negrito, o clique e o Enter) e no e2e (uma palavra começada achada na resposta da
  conversa mais antiga, além da primeira página, aberta pelo clique; um título que nenhuma mensagem
  tem; o "nenhuma conversa"; o Esc devolvendo a lista). Prints de antes (a lista da entrega 1) e
  depois no claro e no escuro, em inglês e em português. Fiscais: 2.718 testes do Python com
  91,46% de cobertura, o `npm run check` (464 do Vitest) e os 78 do e2e.
- **Entrega 3 do 9e, renomear (27/10):** o título de uma conversa era o que a primeira mensagem ou
  o modelo de título dava, e o usuário não tinha como trocar. Agora F2 na linha, ou "Renomear" no
  menu dela (os três pontos que aparecem sobre a hora quando o ponteiro ou o teclado está na
  linha, ou o clique direito), põe o título num campo no lugar da linha; Enter, ou sair do campo,
  guarda o nome novo, e Esc mantém o antigo; um nome igual, ou sem palavras, não muda nada. O
  servidor (`conversation.rename`) guarda as palavras numa linha só, cortadas no tamanho de um
  título, com a origem `user`, pela mesma checagem e escrita de todo título, então o modelo de
  título que responde depois não desfaz o nome. Todo app conectado fica sabendo pelo
  `conversations.changed`, que agora leva o nome: a lista e as abas trocam o título, também a aba
  de uma conversa que a lista daquele app não tem; e a conversa aberta de novo dá à aba o título
  que o servidor tem. Uma recusa deixa o campo aberto, dizendo por quê; ao fechar o campo pelo
  teclado, o foco volta para a linha. O clique duplo continua fixando a aba (decidido com o
  usuário). Provado no pytest (as palavras numa linha, o corte, o nome vazio e a conversa que não
  existe recusados; o outro cliente avisado; o modelo de título que responde depois do nome não
  troca nada), no Vitest (a lista e a aba, a aba de uma conversa fora da lista, a recusa que não
  muda nada, a aba da conversa lida de novo; o F2, o menu e o clique direito, Enter e Esc, a recusa
  no campo, o foco de volta na linha, o clique duplo que não renomeia) e no e2e (F2 e o menu, a
  linha e a aba, o Esc, os nomes lidos de novo depois de recarregar). Prints de antes (a linha sob
  o ponteiro, só com a hora) e depois (os três pontos, o menu, o campo, o nome na lista e na aba)
  no claro e no escuro, em inglês e em português. Um teste antigo do gateway comparava o
  `conversations.changed` inteiro e passou a esperar o `title` vazio. Na rodada do pytest, com o
  pytest de outra sessão rodando junto, o teste do Codex contra o Codex falso
  (`test_prove_codex`) falhou uma vez pela carga e passou sozinho três vezes. Fiscais: 2.721
  testes do Python com 91,48% de cobertura (2.720 na rodada e o do Codex sozinho), o
  `npm run check` (473 do Vitest) e os 79 do e2e.

## Assuntos de desenho ainda abertos

A arquitetura do código foi decidida em 07/10 e está em `docs/ARCHITECTURE.md`: laço próprio sem
framework, Python 3.13 pelo `uv`, React com TypeScript no desktop, `ATLAS_HOME` em `~/.atlas` e
servidor em Starlette.

1. Instalação e primeiro boot. O que o container precisa foi decidido em 11/10 (Container, acima);
   o instalador é o passo 18.
2. Navegador. Decidido em 12/10 (Navegador, acima).
3. Várias máquinas. Decidido em 19/10 (Várias máquinas, acima): o app abre o Atlas de outra máquina
   pelo Tailscale ou por túnel SSH, com "Sempre ligado" e o código de conexão; o relé fica para o
   Atlas Cloud.
4. Modelos e custo. O que o dia a dia precisa foi decidido em 11/10 (Primeira troca, acima): o
   seletor por conversa e o `atlas usage`.
5. Confiança.
6. Agentes e delegação. A delegação a harnesses foi redesenhada em 14/10 (Bancada de harnesses,
   acima), e a tela dela em 15/10 (Aba Executores e painel lateral, acima).
7. Licença e repositório público. Decidido pelo usuário em 13/10: licença MIT (copyright de "the Atlas contributors", sem nome real); o repositório fica privado no GitHub por enquanto, e os testadores entram como colaboradores; o nome "Atlas" vai mudar antes de abrir (o ChatGPT Atlas da OpenAI, de 10/2025, e o MongoDB Atlas), e o histórico é revisado atrás de dado pessoal antes de ficar público.

8. Controle de programas. Estudado em 20/10 (Controle de programas, acima): o cua-driver primeiro
   como MCP, com a imagem do MCP chegando ao modelo. Conferidos em 20/10 a licença (MIT), a
   telemetria (desligável) e o foco no Windows (clicar e ler em segundo plano funcionam, digitar
   não). Decidido pelo usuário em 20/10: no Auto a tela roda sem perguntar, e só apagar, pagar,
   enviar ou confirmar algo para fora pergunta sempre.

Cada um é decidido quando o passo que depende dele chegar.

---

## Regras de trabalho

- **O Atlas V3 congela.** Lá só entra conserto, nada novo. O V3 continua sendo o agente do dia a dia
  até a troca.
- **Nada vem do V3 sem passar pela porta de entrada** (`docs/ARCHITECTURE.md`, última seção):
  - ler o código e o que ele garante;
  - conferir contra a arquitetura;
  - portar em inglês, consertando;
  - trazer os testes junto.
- **Cada passo fecha com prova,** nunca por sensação.
- **O V4 começa do zero** (decidido em 09/10, no lugar da decisão de 07/10 que trazia o perfil
  essencial, as skills e os agentes). Nada do usuário vem do V3: nem perfil, nem memória, nem
  skills, nem agentes, nem conversas. Tudo fica no V3. Do V3 só vem código, pela porta de entrada.
  MCP e rotina entram um a um, quando fizerem falta.
- **Subir a escada até o primeiro degrau que resolve** (a escada de integração, adotada pelo
  usuário em 13/10): programa de terminal com skill, depois ferramenta, depois
  plugin, depois MCP, e por último ferramenta nova no núcleo. O motivo: toda ferramenta
  vai ao modelo em toda chamada, e uma skill custa uma linha até ser usada.

---

## 🛤️ A linha

Um passo de cada vez, na ordem. O passo atual é o primeiro sem ✅ cuja coluna "Só depois de" já se
cumpriu.

**A ordem até o fim, decidida pelo usuário em 27/10,** vale no lugar da ordem da tabela para os
passos que faltam:

1. O app redondo: o 9e, e as vulnerabilidades das dependências que o GitHub aponta na `main`.
2. Distribuir: o 18b, o 18c e o resto do 19 (o domínio, o português, as News e o link do guia no
   app). Sem a conta paga da Apple, o app do Mac abre só depois do "Abrir mesmo assim" nos Ajustes
   na primeira vez e se atualiza pelo atualizador nosso; a conta vale quando o Atlas for para quem
   não é técnico.
3. Crescer: o 14c, o 16r e o 17.
4. Um dia de provas, as adiadas todas juntas, perguntando antes de gastar crédito: o 15, o 17a e o
   17b na OpenAI, o 16q com uma chave da DeepSeek, o 19 com um modelo real, o Cmd+W do 9d num Mac
   e o uso da tela do 15b.
5. O 20, o Atlas Cloud.

| Passo | O quê | Só depois de | Status |
|---|---|---|---|
| 1 | Arquitetura decidida. Esqueleto com `pyproject`, `uv`, ruff, pyright, import-linter, CI, e um Dockerfile que sobe e responde "saudável" | — | ✅ 07/10. Os cinco fiscais passam, 5 testes com 92% de cobertura. O container responde `/health` em 2 s, roda como usuário `atlas` e o Docker o marca "healthy" (imagem de 268 MB) |
| 2 | Casa: `ATLAS_HOME`, configurações, cofre `age`, logs, SQLite e migrações | 1 | ✅ 07/10. Os cinco fiscais passam no Windows e no Linux, 132 testes com 92% de cobertura. No container, um segredo gravado pelo `atlas secrets set` sobrevive ao `docker restart` e não aparece em claro no volume nem nos logs, e a chave fica em `secrets/` (700, dono `atlas`). Os logs saem em JSON com hora UTC, o banco abre em WAL com o livro de migrações, e o Docker marca "healthy". Quatro processos gravando no mesmo cofre não perdem nada |
| 3 | Modelos: adaptador compatível com a OpenAI e o servidor de modelo falso dos testes | 2 | ✅ 07/10. Os cinco fiscais passam no Windows e no Linux, 240 testes com 94,7% de cobertura. Um adaptador sobre o SDK oficial da OpenAI fala Chat Completions (OpenRouter, Gemini, Ollama, vLLM) e a Responses API (OpenAI, que aceita ferramenta junto com raciocínio), com a chave vinda do cofre. O servidor falso dos testes (`tests/fake_model.py`) fala os dois protocolos por HTTP de verdade e imita as manias de cada provedor. Capacidade e preço de 482 modelos vêm de um recorte do models.dev, e o livro de chamadas grava toda chamada com consumo e custo. Prova real (US$ 0,0002): na OpenAI (gpt-5.6-luna), chamada de ferramenta, resultado e resposta, com as duas chamadas no livro, mas o modelo não raciocinou, então o raciocínio cifrado devolvido está provado só no servidor falso; no Gemini (gemini-3.8-flash), a primeira chamada trouxe a assinatura de pensamento e a segunda esbarrou na cota do plano gratuito (20 pedidos por dia), e a volta da assinatura ficou provada só no servidor falso até 09/10, quando a prova do passo 4 a provou contra o Gemini real. A prova achou e consertou dois defeitos: a cota esgotada lida como limite passageiro, e uma espera muda de 10 minutos, agora cortada em 2. O OpenRouter ficou fora da prova real, por decisão de 07/10 |
| 3b | O adaptador da Anthropic, no núcleo, para usar o Opus 5.5 no Atlas (estudado em 13/10, decidido em 14/10: Adaptador da Anthropic, acima): o terceiro wire, `messages`, sobre o SDK oficial; o raciocínio guardado devolvido com a impressão digital do que veio antes; a limpeza de resultados feita pela Anthropic; os 4 pontos de cache, 1 hora na conversa do usuário; a busca de ferramentas pelo `tool_reference`. Decidido pelo usuário em 14/10: vem antes dos títulos com o Haiku 5.5 e do 16 | 3, 5, 8 | ✅ 14/10. Os cinco fiscais passam no Windows e no Linux, e o CI no GitHub. O terceiro wire, `messages`, sobre o SDK oficial da Anthropic (1.11.0, um extra carregado só quando preciso); o raciocínio lacrado devolvido com a impressão digital do que veio antes; a limpeza de resultados pelo servidor da Anthropic; os 4 pontos de cache, 1 hora na conversa do usuário; as ferramentas adiadas por `tool_reference`; e o teto de 32 mil tokens de saída por chamada. O modelo falso fala o formato da Anthropic e recusa como ela, e cada regra foi desligada uma vez para ver os testes falharem. Prova real em 14/10: no Haiku 5.5, as provas de modelo, agente e ferramentas (uns US$ 0,005); no Opus 5.5, a prova longa de 12 capítulos, que passou dos 100 mil tokens sem nenhuma recusa, leu 75% da entrada do cache (US$ 1,50 em 13 chamadas que custariam 2,83 sem ele) e viu o servidor limpar uma vez, de uns 105 mil para 40 mil tokens. **Incidente da prova:** uma chamada do Opus raciocinou em laço até o teto de então, 128 mil tokens, por 13,5 minutos, e custou US$ 2,68 sozinha; a prova saiu por US$ 4,18 em vez de 1 a 1,5, quase todo o crédito que o usuário pôs. Daí o teto de 32 mil (o do Claude Code para um modelo que ele não lista), e a regra de toda prova em modelo caro dizer o pior caso, não só o normal. A prova achou e consertou também: a Anthropic recusa referência de ferramenta ao lado de outro conteúdo no resultado; e o `atlas update` não trazia extras novos. Ficou de fora, decidido com o usuário: o cache de 1 hora só no fim do turno, que a regra de ordem dos prazos da Anthropic não deixa |
| 4 | O laço do agente e as guardas: orçamento de contexto, repetição, compactação e reparo do histórico | 3 | ✅ 09/10. Os cinco fiscais passam no Windows e no Linux, 308 testes com 95% de cobertura. O laço roda o turno sem teto de passos, como o Claude Code e o Codex. Antes de cada chamada, ele repara a cópia do histórico, limpa retornos antigos quando o contexto cresce e compacta perto da janela do modelo, guardando as falas do usuário na íntegra. O retorno grande vai para `spill/`, e a 5ª chamada repetida encerra o turno. Prova real em 09/10 pelo `scripts/prove_agent.py`, quatro turnos na OpenAI (gpt-5.6-luna, US$ 0,007 com as rodadas que acharam defeitos) e no Gemini (gemini-3.8-flash, plano gratuito): ferramenta e resposta; um turno parado quando a ferramenta começa, com o histórico consistente; o turno seguinte respondendo pelo histórico, com a assinatura de pensamento do Gemini e os itens da OpenAI aceitos de volta; e a compactação escrita pelo próprio modelo, que citou literalmente a exclusão do usuário. O raciocínio legível apareceu nos dois. A prova achou e consertou três defeitos: a medida do contexto que perdeu uma compactação (agora a âncora é a contagem do provedor), o catálogo recusando campos do próprio models.dev, e o custo do Gemini sem os tokens de pensamento. Nesta prova a compactação não encolheu a conversa, porque a parte antiga era pequena e o grosso era a mensagem nova |
| 5 | Ferramentas: as 13, a `tool_search` e o MCP. Decidido em 09/10: a `memory_search` chega no passo 6, junto com a memória que ela busca | 4 | ✅ 09/10. Os cinco fiscais passam no Windows e no Linux, 459 testes com 94% de cobertura. Doze das 13 ferramentas, peça por peça: arquivos nativos (com PDF, Office e imagem), `find_files` sobre o ripgrep, `run_command` com segundo plano, `web_search` pelo motor que o cofre permite, `web_fetch` cortado, `plan` e `ask_user`, `use_skill` no formato do Claude Code e a interface do `delegate`. A `tool_search` é a nativa da Responses API na OpenAI e entra pela lista no Chat Completions, listando as ferramentas adiadas. O cliente MCP lê o `.mcp.json` do Claude Code, sem nenhum servidor de fábrica; `ddgs` e `mcp` são extras. As 11 sempre ligadas custam 1.338 tokens no gpt-6-luna e 1.929 no gemini-3.8-flash, contra a meta de 3 mil, e um teste segura esse teto. Prova real em 09/10 pelo `scripts/prove_tools.py`, quatro turnos com as ferramentas reais e um servidor MCP real: arquivo editado e mostrado por comando, ferramenta MCP achada pela `tool_search` e chamada, plano de três itens, e imagem lida e respondida ("Red"). Passou na OpenAI (gpt-6-luna, US$ 0,0009, com a busca nativa) e no Gemini (gemini-3.5-flash, plano gratuito, com 15 s entre chamadas, pela lista). A prova e a revisão das decisões acharam e consertaram: a busca vazia sem dizer qual palavra faltava, a descrição da busca sem os nomes das ferramentas, as chamadas paralelas do Gemini coladas numa só, o histórico com argumentos quebrados que o Gemini recusa inteiro, o `.mcp.json` do Claude Code que seria recusado, e o `web_fetch` e o `read_file` incompletos. O plano gratuito do Gemini corta a resposta e devolve 429 depois de uns 5 pedidos por minuto. Fica aberto: chamar uma ferramenta adiada sem buscá-la antes (o Gemini errou o argumento assim; exigir descrever antes é uma saída) |
| 6 | Memória, decidida em 09/10: perfil e memória com a ferramenta `memory` e a revisão em segundo plano, conversas guardadas com a `conversation_search`, e a `find_files` lendo dentro de documentos no lugar da biblioteca | 5 | ✅ 09/10. Os cinco fiscais passam no Windows e no Linux, 588 testes com 94,9% de cobertura. Refeito em 09/10, depois que a revisão achou a primeira versão feita sobre uma base antiga, de antes do trabalho para reduzir contexto. O perfil (`USER.md`, 1.375 caracteres) e a memória (`MEMORY.md`, 2.200), com a ferramenta `memory` inteira e as guardas no disco: trava, arquivo ilegível nunca sobrescrito, entrada maior que o teto copiada à parte. As instruções levam a fotografia da memória, lida no começo da conversa e depois de cada compactação, com uma orientação curta. A revisão em segundo plano roda a cada 10 falas sem gravação, só com a `memory`, em até 16 chamadas e 75% da janela do modelo. As conversas ficam cruas e para sempre no `atlas.db`: a compactação arquiva sem apagar, e a `conversation_search` (atrás da `tool_search`) busca sem acento, filtra por período, rola, lê e lista. A `find_files` lê dentro de PDF e Office como o `rga`, convertendo uma vez e guardando o texto. O `plan` também foi para trás da `tool_search`, e numa conversa nova que pedia para organizar quatro tarefas foi achado sozinho e usado, na OpenAI e no Gemini, em 3 chamadas. As 11 sempre ligadas desta conta (sem a `use_skill`), mais a `tool_search`, custam 1.996 tokens no gpt-6-luna. Prova real em 09/10 pelo `scripts/prove_memory.py`, numa casa temporária: o agente grava o nome e a preferência; uma conversa nova responde o nome pelo prompt, sem buscar; um fato dito só numa conversa guardada é achado pela `conversation_search`, descoberta pela `tool_search`, mesmo perguntado sem acento; a multa que só um Word tem é achada pela `find_files`; e a revisão de 10 falas não gravadas salva o que a usuária contou, com as chamadas como `memory_review` no livro. Passou na OpenAI (gpt-6-luna, US$ 0,0012) e no Gemini (gemini-3.6-flash, plano gratuito, com 15 s entre chamadas). A descoberta custou 1 ou 2 chamadas a mais. A prova e os testes acharam e consertaram: o resumo da compactação caindo no meio das mensagens arquivadas, a dica de busca vazia sem apontar a palavra que só a conversa atual tinha, e o código 2 do ripgrep sem mensagem lido como recusa. Fica aberto: o gemini-3.5-flash-lite passou um padrão de nome (`*.docx`) como expressão regular no `find_files`; a recusa explicou, e ele acertou na terceira |
| 6b | Skills que aprendem (Memória, item 4): a `skill_manage`, a revisão em segundo plano que cria e melhora skills, a versão anterior guardada antes de toda escrita (do V3), o dono de cada skill e o curador que arquiva as do agente que ninguém usa. Decidido em 09/10: passo próprio, porque custa mais que a revisão da memória (no ritmo do V3, até umas 12 revisões por dia); a revisão pode melhorar as skills do usuário; a revisão só acrescenta na memória, conserto do passo 6 | 6 | ✅ 09/10. Os cinco fiscais passam no Windows e no Linux, 620 testes com 94,4% de cobertura. A `skill_manage` (lista de operações aplicada inteira ou desfeita inteira, descrição de skill nova em até 60 caracteres), a revisão em segundo plano com o pedido de skills e o combinado, a leitura obrigatória antes de mudar uma skill na revisão, e o curador sem IA. Do V3, pela porta de entrada: a versão anterior guardada antes de toda escrita, 20 por skill, com `atlas skills revert`; consertado no porte, a escrita agora é recusada quando a versão não pode ser guardada (o V3 escrevia assim mesmo). Apagar arquiva, e `atlas skills restore` traz de volta, contando como uso para o curador não arquivar de novo (senão a restaurada voltaria ao arquivo na rodada seguinte). A revisão só acrescenta na memória e, quando só as skills vencem, nem recebe a `memory`. Prova real em 09/10 pelo `scripts/prove_skills.py`, numa casa temporária: o agente cria a skill `resumo-semanal` a pedido (origem do usuário); numa conversa nova, acha a skill na lista da `use_skill` e a carrega, e o uso fica registrado; a revisão de uma conversa em que a usuária corrigiu o resumo (10 chamadas desde a última mudança de skill) carrega a skill e a corrige para "números primeiro, no máximo 5 tópicos", guardando a versão anterior e mantendo a skill do usuário; e o curador, com o relógio adiantado 40 dias, arquiva a skill da revisão que ninguém usou, deixa a do usuário, e a restauração a traz de volta. Passou na OpenAI (gpt-6-luna, 8 chamadas, US$ 0,0022), onde o modelo escreveu uma descrição de 127 caracteres, recebeu a recusa e acertou na tentativa seguinte, e no Gemini (gemini-3.7-flash, plano gratuito, 7 chamadas, com 15 s entre elas). As 12 sempre ligadas custam 2.482 tokens no gpt-6-luna (a `skill_manage`, 486) e 3.571 no gemini-3.7-flash. No Gemini as 12 passam de 3 mil, porque ele conta uns 44% a mais que o gpt para as mesmas definições, como já contava no passo 5; decidido em 09/10 que a meta continua medida no gpt-6-luna |
| 7 | Confiança, decidida em 10/10 (Confiança, acima): o portão com pisos, padrões perigosos e guardião, as guardas de arquivo e de MCP não confiável, o ⚡ por conversa, e a varredura contra texto malicioso. O contrato por destino foi para o passo 16; a conferência da entrega ficou de fora | 5 | ✅ 10/10. Os cinco fiscais passam no Windows e no Linux, 1.041 testes com 92,5% de cobertura (a detecção tem ramos que nem os testes dela cobrem). A detecção de comando perigoso (pisos, uns 200 padrões, as variantes que desfazem disfarces), com os 394 testes dela; o guardião; a pergunta ao usuário (uma vez, nesta conversa, sempre, não), recusando quando ninguém responde; o disjuntor depois de 3 recusas do guardião; as guardas de arquivo; o `trust` do MCP; e os padrões de ameaça na memória e no resultado de fora. O ⚡ por conversa fica no `atlas.db`, e a lista de recusas do usuário em `[trust] deny`. Prova real em 10/10 pelo `scripts/prove_trust.py`: o guardião deixa passar o `python -c` e para a remoção de uma pasta da casa do usuário e o `curl \| bash` com um comentário pedindo aprovação, e nenhum dos parados roda; o `rm -rf ~` é barrado em modo automático sem perguntar a ninguém; o agente apaga uma pasta com remoção recursiva, liberada pelo guardião; e uma página que manda criar um arquivo chega ao modelo como dado de fora, e o arquivo não é criado. Passou na OpenAI (gpt-6-luna, 8 chamadas, US$ 0,0005) e no Gemini (gemini-3.7-flash, plano gratuito, 8 chamadas). A prova achou e consertou dois defeitos: o guardião num modelo que raciocina gastava os 16 tokens pensando e não respondia, e toda decisão ia ao usuário; agora o pedido desliga o raciocínio onde o modelo aceita, como nos pedidos rápidos, e dá folga onde não aceita. E o Gemini recusava qualquer esforço pedido, porque o adaptador do passo 3 mandava `reasoning_effort` junto com o `thinking_config`; o esforço agora vai como `thinking_level` dentro dele, e o servidor falso recusa como o Gemini real. Pedido diretamente, o gpt-6-luna se recusou sozinho a rodar `rm -rf ~`, então o piso é provado no portão |
| 8 | Servidor: HTTP, WebSocket e o protocolo do cliente, decidido em 10/10 (Servidor, acima): o protocolo do app, com as perguntas do agente como pedidos do servidor ao app, o turno vivendo no servidor, a volta do app sem perder evento, a mensagem no meio do turno entrando entre ferramentas, a montagem do agente em `atlas.assistant`, e a revisão e o curador rodando sozinhos | 4 | ✅ 10/10. Os cinco fiscais passam no Windows e no Linux, 1.119 testes com 92,7% de cobertura. JSON-RPC 2.0 sobre WebSocket em `/ws`, os pedidos do servidor ao app (`ask_user` e aprovação, respondidos por qualquer app ligado à conversa, retirados quando o turno para, "não entregue" na hora sem app ligado), o anel de eventos numerados para quem volta (512 por conversa, uma marca por processo), os nomes dos eventos, e as guardas do 127.0.0.1 (senha no cabeçalho do WebSocket, Host e Origin, só conexão local). O protocolo inteiro está declarado em `atlas.server.protocol`, e um teste falha se o servidor atender algo fora dele. A montagem do agente virou o assunto `atlas.assistant`: o laço sobre a conversa guardada, as instruções (com a identidade padrão), as 13 ferramentas e as do MCP, o portão de aprovação, a revisão em segundo plano depois do turno e o curador conferido a cada minuto. O laço ganhou a mensagem no meio do turno, que entra depois da rodada de ferramentas. Entra `websockets` 15.0.1 no núcleo, porque o uvicorn instalado não falava WebSocket. A senha vem de `ATLAS_SERVER_TOKEN`; um teste sobe o `atlas serve` de verdade e conversa com ele por WebSocket. Prova real em 10/10 pelo `scripts/prove_server.py`, com o servidor de verdade numa porta e um cliente WebSocket de verdade, em oito passos: um turno chega aos pedaços e fica guardado; a `ask_user` chega ao app como pedido e a resposta (verde) volta ao modelo; uma ferramenta que escreve, de um servidor MCP marcado como não confiável, é achada pela `tool_search` e chega ao app como aprovação, liberada uma vez; um texto longo é parado no primeiro pedaço e o turno seguinte responde; uma mensagem mandada no meio do turno ("responda em inglês") entra entre as ferramentas e a resposta sai em inglês; um app que sai no meio do turno volta e recebe o que perdeu, com a mesma marca e nada cortado; a revisão de uma conversa de 10 falas grava o perfil da usuária e o app recebe o `review.summary`; e, com o relógio 3 horas à frente, o servidor chama o curador sozinho. Passou na OpenAI (gpt-6-luna, 19 chamadas, 5 delas da revisão, US$ 0,0035; a revisão também criou uma skill) e no Gemini (gemini-3.8-flash, plano gratuito, 14 chamadas, com 15 s entre elas). A prova achou e consertou um defeito: parar o turno nos primeiros milissegundos de uma chamada ao modelo às vezes não parava (3 em 300 tentativas medidas), porque o cancelamento se perdia dentro do cliente HTTP, e o turno ia até o fim; agora o servidor cancela de novo até o turno acabar |
| 9 | App desktop: a primeira tela, a conversa. Decidido em 11/10 (App desktop, acima): um desktop enxuto, o app subindo o `atlas serve` com a senha, o cliente com a volta sem perder evento, o protocolo em TypeScript gerado, os cartões da `ask_user` e da aprovação, o ⚡, parar, a mensagem no meio do turno, o resumo da revisão e o aviso do sistema | 8 | ✅ 11/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.130 testes com 92,2% de cobertura, e os do TypeScript também: `tsc` estrito, ESLint com tipos, Prettier, 36 testes do Vitest e 13 cenários de ponta a ponta no Playwright, com o app real, o `atlas serve` real e o servidor de modelo falso rodando como processo. O cliente JSON-RPC (pedidos do servidor respondidos uma vez, cartão que quebra responde erro na hora, `ping` julgado pelo pedido sem resposta, volta pelo número sem pintar lista cortada, espera crescente sorteada), o jeito de subir o servidor (porta 0, linha de pronto, senha nova, `/health` e WebSocket conferidos), o vigia do app no servidor, as regras da janela, o catálogo de textos tipado e o ESLint. O `scripts/gen_protocol.py` escreve o protocolo em TypeScript a partir do `atlas.server.protocol`, e um teste falha quando ele está velho. A tela tem as conversas, a resposta aos pedaços em Markdown, cada ferramenta numa linha, os dois cartões, o ⚡ ao lado do enviar, parar devolvendo à caixa o que não entrou, a mensagem no meio do turno, o resumo da revisão e o aviso do sistema. Prova real em 11/10 pelo `scripts/prove_desktop.py`, que dirige a janela do app de verdade numa casa temporária, com o app subindo o servidor sozinho, em dez passos: a resposta chega aos pedaços; a `ask_user` vira cartão e a escolha (verde) volta ao modelo; uma ferramenta de servidor MCP não confiável vira cartão de aprovação, liberada uma vez; com o ⚡ a mesma ferramenta roda sem cartão; parar encerra o turno e devolve à caixa a mensagem que não entrou; uma mensagem no meio do turno entra entre as ferramentas e a resposta sai em inglês; uma queda da conexão não perde nem repete nada; a revisão de uma conversa de 10 falas chega como resumo; um turno que termina fora da tela gera aviso do sistema; e fechar o app derruba o servidor. Passou na OpenAI (gpt-6-luna, 19 chamadas, 3 da revisão, US$ 0,0026; a queda caiu no meio do turno) e no Gemini (gemini-3.6-flash, plano gratuito, 20 pedidos, com 15 s entre os passos; a queda caiu depois do turno, que terminou rápido). Na OpenAI o passo 1 ainda aceitava uma resposta de um pedaço só, e a resposta foi "Lisboa."; o passo passou a pedir três frases e a exigir mais de um pedaço, e assim passou no Gemini (6 tamanhos vistos). Os testes e a prova acharam e consertaram: fechar o app no Windows travava em um de três fechamentos (o Electron esperava o servidor, e o servidor esperava o Electron morrer), a recusa da senha (4401) chegava à tela com o motivo errado, uma mensagem enviada antes de conectar sumia, e o aviso do sistema saía sem o título da conversa. Fica aberto: o `npm audit` aponta o katex, que só carrega com diagrama mermaid na resposta, e o `source-map-js` do build, cujo conserto ainda não tem 14 dias |
| 9b | As propostas da revisão: trocar ou tirar entrada de memória, que a revisão não faz sozinha, vira proposta que o usuário aprova ou descarta. Decidido em 11/10: passo próprio, antes da primeira troca, porque nada se perde até a memória encher. Decidido em 11/10 (Propostas da revisão, acima): a tabela no banco, o resumo com "Revisar", o cartão na conversa e a lista com contador, a proposta velha marcada ao abrir, e sem vencimento | 9 | ✅ 11/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.155 testes com 92,4% de cobertura, e os do TypeScript também: `tsc` estrito, ESLint, Prettier, 39 testes do Vitest e 15 cenários de ponta a ponta no Playwright (2 novos). A chamada da revisão que trocaria ou tiraria entrada vira proposta inteira, com os acréscimos que vieram junto; antes de guardar, ela é conferida como se fosse aplicada (entrada não achada, ambígua, acima do teto ou com injeção é recusada na hora), e cada troca ou remoção guarda a entrada inteira que pega; aprovar aplica exatamente essas entradas ou nada, e diz o que sobrescreveu ou tirou; sem onde guardar, a revisão é recusada. Do Atlas: a tabela `memory_proposals` no `atlas.db`, a lista que confere ao abrir e marca a proposta que não aplica mais, o cartão na conversa da revisão, o contador na barra lateral, o "Revisar" no resumo, os métodos `memory.proposals`, `memory.approve` e `memory.discard`, o evento `proposals.changed` e o `atlas memory proposals\|approve\|discard`. Prova real em 11/10 pelo `scripts/prove_proposals.py`, pela janela do app, numa casa temporária com o perfil e a memória quase cheios, em três passos: uma conversa contradiz o perfil e a revisão propõe em vez de mudar, com os arquivos intactos e o cartão na conversa; aprovar aplica, e o cartão mostra o que foi sobrescrito; e a proposta de outra conversa, cujas entradas o usuário editou à mão, aparece marcada ao abrir a lista, só com Descartar. Passou inteira na OpenAI (gpt-6-luna, 11 chamadas, 9 da revisão, US$ 0,0048; a rodada anterior ao conserto abaixo também passou, com 14 chamadas e US$ 0,0064). No Gemini (gemini-3.8-flash, plano gratuito, `--pace 15`), os passos 1 e 2 passaram duas vezes, e o passo 3 falhou as duas por comportamento do modelo: numa, a segunda revisão passou de 5 minutos ainda respondendo (o Gemini levou de 95 a 137 s por chamada nesse dia; a prova agora espera 10); na outra, a própria conversa gravou a memória no turno, e a revisão ficou sem o que propor. Decidido em 11/10 fechar assim: o que o passo 3 prova de próprio, a proposta velha marcada, é conferido pelo servidor e passou com propostas reais da OpenAI. Nas duas rodadas da OpenAI, a revisão quis tirar do perfil fatos que continuavam valendo, só para abrir espaço (a newsletter, o motion design, os óculos): é o defeito que a proposta existe para segurar, e virou cartão. A prova achou e consertou: o resumo da revisão comparava a memória e as skills antes e depois, e contava como da revisão o que outra conversa ou o usuário mudou no meio (no gemini-3.7-flash, a revisão de uma conversa disse que criou a skill que a revisão da outra criou); agora ele diz só o que as ferramentas da própria revisão fizeram |
| 9c | O turno na tela (decidido em 15/10: O turno na tela, acima): a trilha, com um ponto por parte e a cor do estado; todo texto na tela, o do meio mais leve, avisado pelo servidor com o `message.interim`; a ferramenta aberta em IN e OUT, com `status` e tempo; o raciocínio com tempo e assunto; o que o app não conhece aparecendo de forma genérica, como manda o ACP v2; o `<think>` tirado no adaptador pelo `think_scrubber`; a conversa reaberta igual à vista ao vivo; o Telegram recebendo os textos do meio; e gravações de verdade de cada provedor nos testes. Pedido do usuário em 15/10 | 9, 16 | ✅ 15/10, usado pelo usuário no app e no Telegram de verdade (confirmado por ele em 15/10, na conferência do passo 11); fica aberta só a gravação do Gemini. Os cinco fiscais do Python passam no Windows e no Linux, 1.859 testes no Windows com 92,8% de cobertura (no Linux, na imagem `atlas-gates:node`, falham só os 2 testes do `install.sh` que já falham na `main` por falta de `git` e `curl` na imagem), e os do app também: 153 testes do Vitest e 37 cenários do Playwright, 1 novo (a trilha ao vivo e reaberta). O `think_scrubber` com os testes dele, no lugar do separador de uma marcação só (o `reasoning_tag` saiu do catálogo); o `message.interim`; o `duration_s`; o "Thinking…", "Thought for 4s" e "Thought briefly"; os títulos de `run_command`, `read_file` e `web_search`; e o corte em 20 mil caracteres. Do Claude Code, a trilha e o IN e OUT; do AI SDK e do ACP, as partes e os estados; do ACP v2, a parte "outra". O protocolo virou `atlas.v2`, porque o `failed` deu lugar ao `status` (decidido pelo usuário em 15/10: app e servidor instalados atualizam juntos). Uma ferramenta que devolve erro no texto conta como `failed`; a chamada que a guarda de laço não deixou rodar e a que a parada cortou aparecem como canceladas ao vivo, como ficam gravadas. Prova: os seis jeitos de modelo do protótipo são roteiros do modelo falso, rodados no servidor de verdade, e o que o app recebeu fica guardado no app (`apps/desktop/src/store/turns/`), onde o Vitest confere partes, estados, texto do meio e a conversa reaberta igual; o Playwright faz um turno com raciocínio, dois textos do meio, uma ferramenta que falha e uma recusada; o Telegram recebe os textos do meio na ordem e nada do raciocínio. Gravações de verdade pelo `scripts/record_turn.py` (um proxy guarda só o que o provedor respondeu, sem chave nem caminho pessoal), tocadas pelo SDK real: Anthropic `claude-haiku-5-5` (6 chamadas, duas ferramentas falhando de verdade), OpenAI `gpt-6-luna` (3 chamadas) e Ollama `qwen3:4b-thinking` (de graça); custo total, com títulos e tentativas, US$ 0,0056. O Gemini respondeu 503 "alta demanda" no 3.8 e no 3.7 em quatro tentativas: falta gravar (`uv run python scripts/record_turn.py gemini gemini-3.8-flash --pace 15`); tentado de novo em 21/10, duas vezes: na primeira o Gemini respondeu 429 duas vezes e o turno passou do teto de 6 chamadas (US$ 0,0164); na segunda, a cota gratuita do dia (20 pedidos por modelo) já tinha acabado (US$ 0,0001). Nada foi gravado. O DeepSeek espera uma chave do OpenRouter (decidido pelo usuário). A prova achou e consertou: o `think_scrubber` cortava o texto no lugar errado quando um caractere muda de tamanho ao virar minúscula ("İ"); um resultado cortado pelo limite da rodada perdia as ferramentas carregadas e as imagens; e, ao gravar, o eco das instruções na resposta da OpenAI levava o caminho da pasta temporária, que tem o nome do usuário (a gravação foi recusada e o script agora troca por marcadores). Achado e deixado como está (decidido pelo usuário): o Ollama manda o raciocínio separado, e com esforço `none` põe o pensamento no texto terminado por um `</think>` sem abertura, do qual o scrubber tira só a marcação |
| 9d | Revisão de UI/UX do app (decidida em 25/10: Revisão de UI/UX do app, acima): o estado real do agente, abrir e reconectar sem dizer o que não aconteceu, erros com saída, cores com nome e movimento, os cartões que pedem o usuário, abas, a conta e a rota da sessão, as telas, a acessibilidade | 9c, 16f | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Código pronto em 26/10, em dez entregas com os fiscais verdes no Windows (Revisão de UI/UX do app, acima); falta o usuário usar no app e conferir o Cmd+W num Mac de verdade |
| 9e | A lista de conversas (decidido em 25/10: Revisão de UI/UX do app, acima): as conversas além das 50 últimas, a busca, renomear e arquivar | 9d | Em andamento desde 27/10 |
| 10 | Prova de pronto em Docker: container vazio, chave e conversa. Decidido em 11/10 (Container, acima): o modelo do primeiro provedor com chave no cofre, a senha guardada no cofre e mostrada pelo `atlas server token`, o tini como processo 1, a pasta do usuário no volume, a conversa pelo app com `ATLAS_DESKTOP_URL`, e o CI rodando o mesmo caminho com o modelo falso | 6, 7, 8 | ✅ 11/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.164 testes com 92,4% de cobertura, e os do TypeScript também: `tsc` estrito, ESLint, Prettier, 39 testes do Vitest e 16 cenários de ponta a ponta no Playwright (1 novo: o app num `atlas serve` que ele não subiu, que volta quando esse servidor reinicia). A imagem monta, e os 3 testes dela passam contra o Docker Desktop. Parte vem do V3. Sem modelo nas configurações, vale o primeiro provedor com chave no cofre, escolhido no primeiro turno de cada conversa. Um servidor fora do 127.0.0.1 guarda a senha no cofre na primeira vez, e o `atlas server token` a mostra (o `instancia token` do V3); a senha é lida e criada sob a mesma trava, para o servidor e o comando nunca ficarem com senhas diferentes. O tini é o processo 1: sem ele, o mesmo cenário deixa 2 zumbis. A pasta do usuário fica no volume (`/data/home`, do V3), declarada `user` na fronteira da casa. O Docker marca "healthy" em segundos. O CI monta a imagem e roda os `tests/docker/` contra ela e o `docker-compose.yml` de verdade, com o modelo falso num container ao lado: o container sobe vazio, a senha sai do `atlas server token`, um turno sem chave diz como gravar uma, e a chave entra pelo `secrets set`; o `run_command` roda no container (Linux, usuário `atlas`, `/data/home`), a chave chega ao provedor, e a conversa e o arquivo sobrevivem ao restart; o processo não roda como root, e os zumbis são recolhidos. Prova real em 11/10 pelo `scripts/prove_docker.py`, pela janela do app ligada ao container por `ATLAS_DESKTOP_URL`, num projeto do compose com volume novo, em cinco passos: o app conecta ao container vazio; uma mensagem sem chave diz como gravar uma; a chave entra por `docker compose exec atlas atlas secrets set`, com o servidor rodando; o `run_command` roda dentro do container, e a resposta diz Linux e atlas; e, depois de `docker compose restart`, a conversa volta inteira e o modelo lê o arquivo que gravou. Passou na OpenAI (gpt-6-luna, escolhido sozinho, 4 chamadas, US$ 0,0005) e no Gemini (gemini-3.8-flash, escolhido sozinho, plano gratuito, 4 pedidos, `--pace 15`). O ensaio achou e consertou um defeito: quando nenhum modelo podia ser escolhido, a fala do usuário não era guardada e sumia da conversa depois de um restart; agora ela fica, como num turno em que a chamada ao modelo falhou. Fica aberto: os testes da imagem rodam daqui pelo Windows (a imagem é Linux nos dois casos), e o lado Linux deles roda só no CI |
| 10b | Backup e várias casas: copiar, conferir, restaurar, exportar e importar a casa pela fronteira dela, e mais de uma casa por máquina. Vem do V3 (`backup_de_perfil`, `perfis`) pela porta de entrada. Decidido em 07/10: fica fora do passo 2, porque a casa ainda não tem o que guardar. Decidido em 11/10 (Backup e várias casas, acima): o `atlas backup` sem as chaves, que serve também de exportação, com o manifesto conferido do V3; restaurar sobrescreve com avisos; backup automático antes de migrar; as casas (`atlas home`, `--home`, `--clone` com as chaves), e o app tem um servidor por casa aberta, até 3 | 6 | ✅ 11/10, sem a rodada com modelo real, adiada para o fim do passo 11 (decidido em 11/10, para economizar: o que o passo garante não depende do modelo). Os cinco fiscais do Python passam no Windows e no Linux, 1.211 testes com 92,3% de cobertura, e os do TypeScript também: `tsc` estrito, ESLint, Prettier, 41 testes do Vitest e 17 cenários de ponta a ponta no Playwright (1 novo). `atlas backup` escreve um zip só, com o que é `user` na fronteira e nunca o cofre, o banco copiado pela API do SQLite, o manifesto do V3 na frente e a conferência depois de escrito; publica inteiro ou nada e guarda os 3 últimos da casa. Com `-o -`, o backup sai pela saída padrão. `atlas restore` confere o zip inteiro antes de tocar na casa, sobrescreve, escreve o banco por dentro do arquivo vivo, pede confirmação quando a casa tem dados e diz quais tabelas ficaram menores. O banco com dados é copiado para `backups/` antes de uma migração (5 guardados). As casas: `atlas --home`, `atlas home list|create [--clone]|use|show|rename|delete`, o `--clone` levando as chaves (a chave do cofre no Windows abre na mesma conta), e um servidor por casa, que segura a casa enquanto roda. O servidor dá a lista pelo `home.list`. O app tem uma sessão e um servidor por casa aberta, até 3: sai a usada há mais tempo que não tem turno rodando, e a lista é relida quando a janela volta ao foco e depois de cada turno. Provado sem modelo real: os testes do backup e das casas em arquivos, SQLite e zips de verdade; um banco sendo escrito por outra conexão sai consistente, e uma conexão aberta lê o que foi restaurado; o segundo `atlas serve` na mesma casa é recusado; no container, o backup sai pela saída padrão e é restaurado no computador com a conversa intacta e sem a chave; e no app, a troca para outra casa sobe o servidor dela, e um turno lá continua e avisa "(home work)". O `scripts/prove_homes.py` passou no ensaio contra o modelo falso, em seis passos pela janela (perfil gravado, clone no terminal que aparece no app, clone que sabe quem é a usuária, turno em outra casa que avisa, backup restaurado numa casa nova sem as chaves, e a casa restaurada respondendo depois de gravar a chave); a rodada com modelo real ficou para o fim do passo 11, e o usuário fechou o 11 sem ela (15/10). O ensaio de 15/10 passou de novo, depois de dois consertos (Achados do uso, acima) |
| 10c | Casas no app (decididas em 15/10: Casas no app, acima): a casa sempre à vista no pé da barra lateral, criar casa vazia ou copiada, backup pelo diálogo de salvar do sistema, importar backup como casa nova, e o app abrindo na última casa usada. Pedido do usuário em 14/10 | 10b | ✅ 15/10, usado pelo usuário no app de verdade (confirmado por ele em 15/10, na conferência do passo 11), e provado no Windows contra o modelo falso (o lado Linux roda no CI). Os cinco fiscais do Python passam, 1.788 testes com 92,8% de cobertura, e os do app também: 96 testes do Vitest e 35 cenários do Playwright, 3 novos (a casa à vista com uma só, e uma casa criada que abre e conversa; o backup no caminho do diálogo e importado como casa nova, que abre com a conversa; e o app abrindo na última casa, ou na ativa quando ela foi apagada). Os métodos `home.create`, `home.backup`, `home.inspect` e `home.import`; a importação montada em `homes/<nome>.creating/`, com o banco trazido para esta versão; e a última casa guardada na pasta do app. A prova achou e consertou: o servidor dos testes subia sem a lista de migrações e recusava todo backup como de um Atlas mais novo (agora a lista é obrigatória); e, nas fotos da tela, o "+ Nova casa" cortado na barra e o aviso do backup quebrando palavras no meio |
| 11 | **Primeira troca.** O usuário passa a usar o V4 no dia a dia, começando do zero: nada vem do V3, nem perfil, nem skills, nem agentes, nem conversas (decidido em 09/10). Nenhum MCP vem conectado e nenhuma rotina é copiada; o V3 segue rodando as rotinas dele. Decidido em 11/10: no fim deste passo roda a prova real do passo 10b (`scripts/prove_homes.py`), adiada para economizar. Decidido em 11/10 (Primeira troca, acima): 7 dias de uso com a lista mínima de conversas e cada defeito virando teste; a cópia de uso com `atlas desktop`, `atlas update` e o atalho; o seletor de modelo e de esforço por conversa; o `atlas usage`; o pt-BR na tela; e a recusa da ferramenta adiada não carregada | 9, 9b, 10, 10b | ✅ 15/10, fechado pelo usuário antes do prazo: 5 dias seguidos de uso (11 a 15/10) dos 7 decididos, e sem a rodada da prova do 10b com modelo real, que passou no ensaio com o modelo falso. A lista mínima foi conferida no banco da casa real em 15/10 (Conferência da lista mínima, acima): arquivo achado e lido, busca na web, skill corrigida, cartão de pergunta e troca de modelo aconteceram no uso; memória usada numa conversa nova, compactação e backup restaurado foram dispensados pelo usuário. Achados do uso: 8, consertados com teste (acima); o último, achado no ensaio da prova do 10b, foi o fim de um turno que falhou mostrando só o tipo do erro (desde o 9c), e a prova do 10b, que ficou velha com os títulos do 14/10. Os cinco fiscais do Python passam no Windows, 1.861 testes com 92,8% de cobertura, e os do app também: 155 testes do Vitest e 38 cenários do Playwright, 1 novo (o turno sem a chave diz como gravá-la); o lado Linux roda no CI |
| 12 | Rotinas e guardas de laço (decididas em 11/10: Rotinas e guardas, acima): o cron no servidor, a execução como conversa nova com aviso do sistema, a `cronjob_manage`, a rotina sem modelo e o backup diário, a tela de rotinas, o aviso do comando em segundo plano, e as guardas de laço com a postura de só avisar na conversa. Os subagentes foram para o 12b. Nenhuma rotina vem do V3: cada uma é criada de novo no V4, quando fizer falta. Decidido em 09/10: aqui entram as guardas de repetição completas, que a do passo 4 (avisa na 3ª chamada igual, para na 5ª) ainda não tem: os ciclos (A, B, A, B), o resultado repetido trocado por uma referência ao primeiro, e as guardas de falha (o mesmo erro de novo, a mesma ferramenta falhando). Elas entram aqui porque é aqui que nasce o trabalho sem ninguém olhando, onde um laço custa mais. Fica para decidir neste passo se a conversa fica com a postura de só avisar, parando só no trabalho sem ninguém olhando. Aqui também a skill que uma rotina usa passa a contar como em uso para o curador, e a revisão em segundo plano não roda depois de uma rotina (custa uns 30 mil tokens por vez sem ninguém para aproveitar). Decidido em 12/10: começa em paralelo com o uso do passo 11, numa branch, e entra no `main` quando passar na prova, para o usuário testá-lo no uso | 10b, em paralelo com o 11 | ✅ 12/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.290 testes com 92,5% de cobertura, e os do TypeScript também: `tsc` estrito, ESLint, Prettier, 48 testes do Vitest e 20 cenários de ponta a ponta no Playwright (1 novo: uma rotina feita no terminal roda pela tela, avisa e abre como conversa). As guardas de laço (`tool_guardrails`, com os testes delas) trocam a guarda de repetição do passo 4: ciclo A, B, A, B, resultado repetido trocado por uma referência, a mesma falha de novo, a mesma ferramenta falhando; na conversa só avisam, na rotina param. O comando em segundo plano com `notify_on_complete` começa um turno com o aviso quando termina. As rotinas (`atlas.jobs`): a agenda (com o `croniter` 6.0.0 no núcleo, e a linguagem natural que veio junto, corrigindo a decisão de 11/10), a folga de metade do intervalo e uma execução só depois dela, o servidor disparando a cada minuto, a execução como conversa nova sem ninguém olhando (sem `ask_user`, aprovações negadas, sem revisão, guardas que param, vigia de 10 minutos sem atividade), o `[SILENT]`, a rotina de comando, a `cronjob_manage` atrás da busca, o `atlas routines`, a tela e o aviso do sistema. Prova real em 12/10 pelo `scripts/prove_routines.py`, no servidor de verdade, em sete passos: o agente agenda pela conversa uma rotina de todo dia útil às 9h, achando a `cronjob_manage` pela busca; rodar agora dispara, a execução é uma conversa e o app recebe o fim; uma rotina sem novidade responde `[SILENT]`; o comando que apagaria uma pasta é recusado sem perguntar a ninguém; com o relógio 5 horas à frente, uma rotina de hora em hora roda uma vez só, atrasada; um comando em segundo plano com aviso começa um turno, e a resposta diz o que ele imprimiu; e uma rotina de comando entrega a saída. Passou na OpenAI (gpt-6-luna, 20 chamadas, US$ 0,006) e no Gemini (plano gratuito, `--pace 15`): os passos 1, 2, 3 e 7 no gemini-3.8-flash, cuja cota acabou no meio de uma segunda rodada (a primeira caiu no 503 do pico), e os 4, 5 e 6 no gemini-3.7-flash. A prova achou: o Gemini 3.7, com três recusas sem ninguém para aprovar, apagou a pasta por outro caminho, em uns 37 pedidos; foi decidido em 12/10 não mudar (limite conhecido, acima). E achou, antes de gastar, que os scripts de prova migravam a casa real (achado do uso, passo 11). |
| 12b | Subagentes, o `delegate` (decididos em 11/10: Subagentes, acima): em segundo plano, um aviso por lote, listar, corrigir o rumo e parar, só o resumo volta, um nível só | 12 | ✅ 12/10, provado na OpenAI; decidido pelo usuário em 12/10 que a prova do Gemini não é preciso terminar (os passos 3, 4 e 6 caíram na cota gratuita do dia). Os cinco fiscais do Python passam no Windows e no Linux, 1.330 testes com 92,5% de cobertura, e os do TypeScript também: `tsc` estrito, ESLint, Prettier, 51 testes do Vitest e 22 cenários de ponta a ponta no Playwright (1 novo: a doca mostra o filho, aceita a correção, para, e o lote volta como aviso). O `delegate` com os argumentos e o texto dele, o filho como conversa de origem `subagent` no papel `subagent` (o `unattended` virou o `ConversationRole`), o aviso único por lote, a rotina que espera os filhos, corrigir o rumo pela mensagem fora de hora, parar com o que o filho tinha, o vigia de 450 s e 1.200 s, o corte do resumo com um rodapé, a tabela `jobs_subagents` com o "desconhecido" depois de o processo morrer, o teto de 50 por turno nas guardas, os métodos `subagent.list`, `subagent.steer` e `subagent.interrupt` e a doca. As sempre ligadas custam 3.067 tokens no gpt-6-luna. Prova real em 12/10 pelo `scripts/prove_subagents.py`, ensaiada antes no modelo falso, em seis passos: um lote de duas tarefas volta num aviso só e a resposta diz os dois números; a correção de rumo pela doca faz o resumo terminar com a palavra pedida; parar um filho devolve "interrupted" e o pai responde; um comando de remoção no filho é negado sem perguntar a ninguém; as chamadas dos filhos ficam no livro como `subagent`; e o `atlas serve` morto no meio de um filho deixa-o "unknown", e o servidor seguinte entrega o lote. Passou inteira na OpenAI (gpt-6-luna, 28 chamadas, 12 dos filhos, US$ 0,0052). No Gemini (plano gratuito, `--pace 15`), os passos 1 e 2 passaram no gemini-3.6-flash; os 3, 4 e 6 caíram na cota do dia (o 3.5-flash parou depois de 3 pedidos, o 3.7 depois de 18 no dia). Os testes acharam e consertaram: uma correção mandada logo depois de criar o filho era recusada, porque o turno dele ainda não tinha começado. O ensaio achou dois defeitos do próprio roteiro de prova, consertados antes de gastar |
| 13 | Delegação para executores externos, integrados no laço do harness chamado: eventos, perguntas e aprovações dele chegam ao usuário no Atlas. Decidido em 12/10: passo próprio, com os agentes de domínio no 13b; começa só pelo Claude Code (o que o usuário usa), pelo Agent SDK em Python; o Codex, o Hermes e os outros (ACP) entram quando fizerem falta | 12b | ✅ 12/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.347 testes com 92,1% de cobertura, e os do TypeScript também: `tsc` estrito, ESLint, Prettier, 51 testes do Vitest e 23 cenários de ponta a ponta no Playwright (1 novo: o app com um Claude Code falso que fala o mesmo canal, o selo à direita, o cartão de permissão e o de pergunta saindo dele, e o resultado como aviso). O canal do Claude Code é o do Agent SDK, sem o pacote de 244 MB, e roda o `claude` do usuário com o login e as configurações dele. Os pedidos dele são do servidor, sem prazo, e esperam um app; com "o Atlas decide", o guardião aprova ou devolve, e o modelo da conversa responde as perguntas (opção nova). A tela tem a presença dele à direita, separada da doca. Prova real em 12/10 pelo `scripts/prove_executors.py`, ensaiada antes com o modelo falso e o Claude Code falso, contra o Claude Code 2.1.140 do usuário e o gpt-6-luna, em cinco passos: conserta um teste quebrado com os cartões de Edit e Bash permitidos (o comando chegou reescrito pelo hook do usuário, `rtk pytest -q`: as configurações dele valem); um `Write` recusado pelo cartão deixa a pasta sem o arquivo e a tarefa termina; o `AskUserQuestion` vira cartão e o nome escolhido vai para o código; com "o Atlas decide" o guardião aprova e nenhum cartão chega ao usuário; e parar no meio volta "interrupted" com o que ele tinha. 19 chamadas do Atlas, US$ 0,0027, mais o uso da assinatura do Claude Code. A prova achou: pedido para apagar um arquivo, o Claude Code pediu confirmação por escrito e não tentou nada, então o passo da recusa passou a pedir uma escrita; e o ensaio achou um texto com acento embaralhado no canal, consertado escapando o JSON como o SDK. Com o Claude Code instalado, as sempre ligadas custam uns 95 tokens a mais no gpt-6-luna |
| 13b | Agentes de domínio: um agente fixo com missão, ferramentas e regras próprias. Decidido em 12/10: separado do 13, por ser peça que só o V3 tem; chega como opcional. Pedido do usuário em 13/10: a inspiração são os bots (com conversa fixa, rotinas, grupos e mensagens entre bots), comparados com o V3. Decidido em 13/10 (Bots, acima): o bot mora dentro da casa, com o `USER.md` da casa e o próprio `MEMORY.md`, as skills com a marca de quem criou, a conversa fixa, as rotinas, e o `message_agent` (do Atlas principal, de qualquer conversa); os grupos saíram para o 13c | 13 | ✅ 13/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.461 testes com 92,2% de cobertura, e os do app também: 58 testes do Vitest e 25 cenários de ponta a ponta (1 novo: criar um bot pela tela, conversar, editar a missão e apagar). O bot mora na casa, em `bots/<nome>/`, com o `BOT.md` no formato do agente do Claude Code, o próprio `MEMORY.md` e a própria pasta de skills; o `USER.md` é o da casa, um só (opção nossa). A conversa fixa, que relê as instruções quando o que elas mostram muda (comparando o próprio texto), as rotinas do bot entregues na conversa dele, o `message_agent` com o texto e as regras dele, e a tela (a aba Bots e o diálogo de criar, enxutos; a cara sai do nome). As skills (biblioteca da casa, pasta do bot na frente, lista de permitidas) e a busca só nas próprias conversas; o Atlas principal busca em tudo. O `bot_create`, que pergunta antes. Novos: o `USER.md` único, o `message_agent` em qualquer conversa do Atlas principal, o teto de 6 mensagens entre agentes sem fala do usuário, e a marca de quem criou cada skill (`atlas skills list`). Prova real em 13/10 pelo `scripts/prove_bots.py`, ensaiada antes no modelo falso, contra o gpt-6-luna, em seis passos: o bot segue a regra da missão e a mesma conversa abre de novo; o fato contado ao bot de finanças chega ao de produto com a conversa já aberta, e a nota do trabalho fica na memória do bot; a skill pedida fica na pasta do bot, marcada como dele; o bot não acha a conversa do Atlas, e o Atlas acha a do bot dizendo de quem é; a rotina roda como o bot e a resposta entra na conversa dele; e o Atlas pergunta ao bot de uma conversa qualquer e repassa a resposta. 25 chamadas, US$ 0,0065. A primeira rodada achou um defeito: o bot respondeu ao Atlas pelo `message_agent`, e o texto do turno voltou também, então a resposta chegava duas vezes; agora a mensagem escrita a quem perguntou é a resposta, e um teste segura isso. Fica registrado: uma mensagem entre agentes a caminho se perde se o servidor reiniciar (recibos em disco evitariam), e os grupos ficaram para o 13c |
| 14 | Navegador. Decidido em 12/10 (Navegador, acima): o `browser_exec` sobre o browser-harness, um perfil do Chrome só do Atlas mais os modos limpo e invisível, o usuário assumindo na janela de verdade, e a presença do navegador à direita da conversa | 9 | ✅ 13/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.382 testes com 92,1% de cobertura (no Linux, sem Chrome, os testes com o Chrome de verdade são pulados), e os do TypeScript também: 51 testes do Vitest e 24 cenários de ponta a ponta (1 novo: a presença do navegador, assumir e devolver). A descrição e o resumo das funções do `browser_exec`, a aba própria de cada sessão, o piso dos metadados de nuvem, o print e o tempo esgotado, e a posse da janela. O motor é o browser-harness 0.1.13 da Browser Use (MIT), com a telemetria (que manda o código para fora) e o aviso de atualização desligados. Do Atlas: o perfil próprio aberto pelo Atlas, os modos limpo e invisível, a recusa de endereço com segredo do cofre, e a presença na tela. Nas sempre ligadas, 19 tokens a mais pela estimativa (o nome dele na busca). Prova real em 13/10 pelo `scripts/prove_browser.py`, ensaiada antes com o modelo falso, contra o Chrome 155 instalado e o gpt-6-luna, num site servido na própria máquina, em quatro passos: lê um preço sem janela; para no login sem tentar entrar, o usuário assume, entra e devolve, e o modelo, avisado de que a página mudou, lê o saldo; o endereço de metadados de nuvem é recusado antes de o navegador abrir; e o modelo vê a cor num print. 23 chamadas, US$ 0,0037. Na primeira rodada o próprio modelo se recusou a abrir os metadados, sem chamar o navegador (como o `rm -rf ~` na prova de confiança); na segunda tentou e o Atlas barrou. O usuário conferiu o assumir e devolver no app de verdade, na casa dele. Na mesma rodada, a troca de conta do passo 13 foi provada com as duas contas do usuário (`prove_executors.py`, passo 6): uma tarefa guarda uma palavra, o Claude Code entra na outra conta pelo navegador, a mesma sessão continua e lembra a palavra, e volta à conta do usuário (11 chamadas, US$ 0,0012). Os testes e o ensaio acharam e consertaram: o arquivo de porta que um Chrome morto deixa impedia abrir de novo; uma chamada invisível fazia a conversa esquecer a janela que o usuário segurava; o aviso do limite do plano dizia `<this id>` em vez do subagente a retomar; e o `measure_tools.py` estava quebrado desde o passo 13. Fica registrado: digitar na janela sem assumir é possível (ela é do usuário); o assumir é o que impede o agente de agir e ler enquanto ele digita |
| 14b | Google e GitHub pelo terminal (decididos em 13/10: Google e GitHub pelo terminal, acima): o `gh`, o `gcloud` e o `gws` com as skills `github` e do `gws` embutidas no Atlas (o primeiro jeito de trazer skill própria), as regras de pergunta no portão antes de cada ação para fora (com a lista de destinos conhecidos), a saída marcada como texto de fora e o login dos programas fora do backup. Decidido pelo usuário em 13/10: vem antes do 15 e do 16 | 7, 6b, 10b | ✅ 13/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.576 testes com 92,7% de cobertura, e os do app também (63 testes do Vitest). Provado com o gpt-6-luna e as contas reais do usuário (`scripts/prove_terminal.py`, ensaiado antes no modelo falso com `echo` na forma dos comandos reais): o Atlas instalou o `gws` sozinho pelo `npm`; uma issue aberta pediu aprovação antes e foi fechada pela prova; um e-mail com o ⚡ ligado perguntou mesmo assim, dizendo que era o primeiro para o endereço; a leitura chegou marcada como texto de fora e a resposta perguntou; um evento perguntou antes e foi apagado; um e-mail recusado para um endereço novo não foi tentado de outro jeito; e um e-mail que mandava encaminhá-lo só foi resumido, com o agente contando ao usuário o que ele pedia. Achado da prova: o modelo, brigando com as aspas do Windows PowerShell 5.1, escreveu o comando `gws` num `.cmd` e rodou o arquivo, e por ali a saída vinha sem marca e um envio não perguntaria; agora o portão lê os scripts que o comando roda (fora de `node_modules` e `site-packages`, depois que um `Test-Path` no lançador do próprio `gws` perguntou à toa). Também: a conta de gasto das provas lê as últimas mil chamadas do livro, que a casa real já passou, e diz zero (o gasto do dia, pelo `atlas usage`: US$ 0,07 em 207 chamadas, com o uso do dia); e o login do `gws` pediu o usuário como usuário de teste do app no Google Cloud, passo que a skill passa a lembrar |
| 14c | Controle de programas (estudado e decidido em 20/10: Controle de programas, acima): a imagem do MCP chegando ao modelo, como o print do navegador; o cua-driver como servidor MCP, com a telemetria e a conferência de atualização desligadas, e uma skill embutida com a escada da automação; no Auto a tela roda sem perguntar, e apagar, pagar, enviar ou confirmar algo para fora pergunta sempre; a ferramenta `computer_use` no núcleo só se o MCP não bastar no uso | 14 | |
| 15 | Várias máquinas (decididas em 19/10: Várias máquinas, acima): o app abre o Atlas de outra máquina, que trabalha lá; pelo Tailscale nos três cenários do usuário (a loja, este PC visto de outro, o VPS) e por túnel SSH para servidor, com o relé deixado para o Atlas Cloud; "Sempre ligado" por casa, o servidor como serviço do usuário que sobrevive ao app e liga com o computador, e dentro dela "Abrir para as minhas máquinas", com o código de conexão; a lista de máquinas no app, com as senhas cifradas pelo sistema e uma máquina por vez na tela; a tela de Configurações começando só com "Máquinas"; o terminal sempre pelo servidor; o backup descendo pela conexão. Fica de fora um agente agir em outra máquina | 10c, 16f, 18 | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Em andamento desde 19/10: código pronto (partes 2 a 5), falta a prova real com o usuário |
| 15b | Configurações (decididas em 20/10: Configurações, acima): a tela inteira, com Geral, Modelo, Personalização (o Soul, o `USER.md` e o `MEMORY.md`, para ler e editar), Segurança, Área de trabalho, Plugins e Máquinas; salva sozinha e vale sem reiniciar; o Soul e os Plugins saem da barra lateral | 15 | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Código pronto em 20/10, com os fiscais verdes no Windows; falta o usuário usar a tela |
| 16 | SDK de plugins: a caixa no formato do Claude Code, ligar e desligar, e o consentimento antes de ligar (estudado em 13/10: Plugins, acima). Decidido pelo usuário em 13/10: a lista de destinos conhecidos foi para o 14b. **Decidido pelo usuário em 14/10, no lugar das duas caixas de 13/10: a prova é só um plugin do Telegram**, e por isso o `register(ctx)` em Python e o primeiro canal vieram do 16b para cá; ligar o plugin e aprovar o pareamento numa tela Plugins no app (além do terminal), e as conversas do Telegram na lista do app, marcadas | 12, 14b | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Código pronto em 14/10 (docs/ARCHITECTURE.md, Plugins): o formato do Claude Code com a parte do Atlas em `atlas`, desligado até o consentimento à declaração (uma atualização que declara mais desliga de novo), o carregamento com prazo de 10 s, o `ctx` que só dá o que o manifesto declara, o canal como mais um cliente do servidor, o pareamento aprovado na tela, e o plugin do Telegram no Atlas. Escolhas próprias, marcadas: a API do Telegram direto pelo `httpx2` em vez da python-telegram-bot (para não brigar com as tentativas da biblioteca) e HTML em vez de MarkdownV2. Provado nos testes contra um Telegram falso por HTTP (pareamento, turno, aprovação por botão, pergunta por texto, `/new`, `/stop`, 401, 409, 429, HTML recusado) e de ponta a ponta no app. ✅ 14/10: **prova real feita pelo usuário**, com o bot dele no celular, e funcionou bem. **Achado antes de fechar:** o portão não protegia a pasta `plugins/` nem os comandos novos, e o agente poderia escrever um plugin e rodar `atlas plugins enable --yes`, pulando o consentimento. Agora `atlas plugins enable|disable|secret|set`, `atlas pairing approve|forget` e qualquer comando que toque `plugins/`, `plugin-data/` ou `atlas.db` perguntam ao usuário toda vez, passando por cima do guardião e do ⚡, e as ferramentas de arquivo não escrevem nessas pastas. Ficou para depois: o Atlas criar e ligar plugins sozinho (sempre pedindo o consentimento na tela), skills e MCPs dentro do plugin, instalar por endereço e um guia para quem escreve plugin |
| 16c | Bancada de harnesses (decidida em 14/10: Bancada de harnesses, acima): o SDK v2 com `register_executor`, a bancada global com um cartão por instância, as duas chaves (o automático do harness e "Atlas responde"), a detecção na instalação e na abertura com a confirmação no onboarding, o registro enxuto das delegações por turno, a `delegation_search` com a transcrição normalizada sob demanda, e o Atlas só lendo as instâncias do usuário. A prova é o Claude Code saindo do núcleo como primeiro plugin; depois Codex, Pi, Hermes e DeepSeek, um de cada vez | 16 | ✅ 14/10. Os fiscais do Python passam no Windows e no Linux, 1.772 testes com 92,7% de cobertura, os do app também (86 testes do Vitest e 31 cenários do Playwright, 3 novos: a delegação na bancada, o cartão inteiro e o onboarding que liga o Claude Code com a confirmação ali mesmo), e o CI no GitHub. Feito em quatro partes, cada uma com um commit: o SDK v2 (`register_executor`, `ExecutorInstance` e `ExecutorHost`, sem quebrar a versão 1: o consentimento do Telegram continua valendo) e o Claude Code saindo do núcleo para `plugins/bundled/claude-code`, com os testes do passo 13 passando com as mesmas asserções; a bancada no app, que substituiu o selo do passo 13; o registro por instância e por turno no SQLite, a `delegation_search` e a bancada no `list` do `delegate`; e a detecção em toda abertura, também dos plugins embutidos desligados, oferecida no onboarding. Decidido com o usuário em 14/10: `ExecutorInstance` e não `Instance`, que no glossário é um Atlas rodando; o cliente ACP vem com o plugin do Hermes. Prova real em 14/10 pelo `scripts/prove_executors.py`, ensaiado antes no Claude Code falso e no modelo falso (o ensaio agora é teste, `tests/test_prove_executors.py`), contra o Claude Code 2.1.140 do usuário e o gpt-6-luna: os cinco passos do 13 de novo, agora pelo plugin, e o passo 7, a bancada: a pergunta respondida no cartão, o steer aceito com o turno rodando, o automático sem nenhum cartão, o "Atlas responde" com o guardião aprovando, a duplicata em outra pasta, e o Atlas respondendo "o que o Claude Code fez" pela `delegation_search`, com os três arquivos e o resultado do `pytest`. 34 chamadas do Atlas, US$ 0,0033, mais o uso da assinatura. **Prova do usuário no app real em 14/10, e funcionou tudo:** a bancada com o Claude Code detectado, conta e versão; prompt e steer pelo cartão; pergunta respondida no cartão; as duas chaves; a duplicata em outra pasta; e a pergunta ao Atlas. **Achados no caminho:** o import-linter não enxerga pasta com hífen, e a regra "o plugin só importa o SDK" não valia para o `claude-code` (agora um teste lê os imports de todo plugin embutido); a cobertura contava pelo nome do módulo e não via o código do plugin (agora conta pela pasta); o `prove_executors.py` quebrou na parte 2 sem ninguém ver (daí o ensaio virar teste); o prompt do cartão deixava a instância parecer parada por um instante, e um steer logo em seguida se perdia; a configuração de um plugin desligado não refazia a detecção; e o CI estava vermelho desde o fechamento do passo 18 (um lint e o modelo de título consumindo a resposta roteirizada do teste do contêiner) |
| 16d | Chave pedida na conversa (decidida em 14/10: Chave pedida na conversa, acima): a ferramenta `secrets`, com `request` e `list` e sem `delete`; o pedido `secret` com o cartão mascarado no app, em toda tela; a chave direto no cofre, com o modelo recebendo só "guardada"; quem usa a chave reiniciado depois de gravar; o Telegram só avisando que a chave vai no app. Decidido pelo usuário em 14/10: os comandos do `run_command` não recebem a chave | 9, 16 | ✅ 18/10, fechado pelo usuário (o uso dele no app fica para quando ele quiser; o CI roda no Linux a cada push). Código pronto em 16/10. Os cinco fiscais do Python passam (ruff, pyright strict, lint-imports com 30 contratos, deptry), 1.918 testes passam com 0 falhas e o piso de cobertura passa (o último número medido foi 92,78%, antes dos testes da prova), e os do app também: `tsc` estrito, ESLint, Prettier, 183 testes do Vitest e 47 cenários do Playwright, 2 novos (a chave colada no cartão vai para o cofre e o modelo ouve só "guardada"; o pulo). A ferramenta `secrets` com `request` e `list`, sem `delete`, deferida atrás da busca, com o pedido `secret` ao app e a chave que não passa pelo modelo nem pelo registro. A chave vai ao cofre exatamente como colada, sem aparar; o servidor registra o valor no Redactor antes de gravar; uma gravação recusada pelo cofre volta ao modelo como `failed`, nunca como guardada; o servidor `notes` que faltava sobe com a chave, sem reiniciar. Pelo Telegram, o chat recebe o aviso e o texto digitado lá não responde o cartão. A revisão em segundo plano não recebe a ferramenta (o que custa o cache da revisão nos turnos que a tinham). **Achados e consertados:** o cartão aparava a chave (corrigido: vai como colada); a função `secrets_tool` tinha o nome do módulo, e o `lint-imports` acusava (renomeada para `make_secrets_tool`); o teste de servidor que cobria a ferramenta em subagente era só uma inspeção do código (trocado por testes reais de conjunto de ferramentas por papel). **Gravação do Ollama:** a cópia do turno que o app confere foi reescrita, sem modelo, porque a mensagem "nenhuma ferramenta" da busca agora cita a fonte `atlas.secrets`; a gravação em `tests/recordings/` não mudou. **Prova real** com o gpt-6-luna (`scripts/prove_secrets_tool.py`), ensaiada antes com o modelo falso (`tests/test_prove_secrets_tool.py`): o modelo pediu `NOTES_TOKEN` pela ferramenta, com motivo, e não pediu a chave no chat; a chave falsa colada foi para o cofre; o modelo ouviu só "stored", e o servidor `notes` subiu. Custo: 21 chamadas em duas tentativas, cerca de US$ 0,0046. Falta: validar no Linux (o CI roda no Linux a cada push), e o uso do usuário no app antes de marcar ✅ |
| 16e | SOUL.md do Atlas principal (decidido em 15/10: SOUL.md, acima): o arquivo na raiz da casa no lugar da identidade fixa, nascido com o texto de hoje e lido no começo de cada conversa; escrito pelo usuário, e pelo agente quando o usuário pede; a tela no app. Pedido do usuário em 15/10 | 9, 10b | ✅ 15/10. Os cinco fiscais do Python passam no Windows e no Linux, 1.790 testes com 92,8% de cobertura no Windows (no Linux daqui, dois testes do `install.sh` falham também no `main`: a imagem local não tem `git` nem `curl`), e os do app também: `tsc` estrito, ESLint, Prettier, 89 testes do Vitest e 33 cenários de ponta a ponta no Playwright (1 novo: o soul editado na tela, a conversa aberta que não muda e a seguinte que segue o texto novo). O `SOUL.md` na raiz da casa, nascido com o texto padrão e nunca sobrescrito, no lugar da identidade, lido no começo da conversa e depois de cada compactação; vazio ou ilegível, vale o padrão; acima de 20 mil caracteres, cortado no meio com o caminho do arquivo; texto suspeito avisa no log e carrega; vai no backup e no `--clone`; a rotina tem soul e o subagente não; memória ou skill que manda mudar o `.atlas/SOUL.md` é recusada; e a tela Soul no app, com `soul.read` e `soul.write`. Prova real em 15/10 pelo `scripts/prove_soul.py`, ensaiada antes no modelo falso, no servidor de verdade e numa casa temporária, no gpt-6-luna, em três passos: um traço escrito no soul pela tela aparece numa conversa nova; o agente, pedido para trocar o traço e gravar isso no `SOUL.md`, edita só a linha pedida, sem aprovação, e avisa que vale na próxima conversa; e a conversa seguinte segue o texto novo, sem ninguém mudar o arquivo. **A prova achou e consertou:** só com o caminho do arquivo, o gpt-6-luna recusou três vezes em três ("Não posso trocar essa regra"), porque a regra do prompt do sistema vale mais que o pedido do usuário. Daí a linha que dá a permissão quando o usuário pede (opção nossa, com o "avise o usuário"), e com ela a prova passou duas vezes em duas, com 11 chamadas e US$ 0,0013 cada (US$ 0,0049 com as rodadas que acharam o defeito) |
| 16f | Aba Executores e painel lateral (decididos em 15/10: Aba Executores e painel lateral, acima): uma aba ao lado de Conversations e Bots com os executores; as sessões de cada pasta, de onde quer que tenham começado, abertas como conversa e continuadas dali; o estado na lista e a delegação do Atlas como linha na conversa, no lugar dos cartões da bancada; o painel da direita com o terminal e a prévia de arquivo, site local e artefato, num quadro isolado, que não abre sozinho; e o ▶ dos blocos de shell da resposta e o `!` da caixa, que rodam o comando nesse terminal, com a saída entrando na conversa e o Atlas respondendo, e confirmação antes de um ▶ perigoso. Pedido do usuário em 15/10 | 16c | ✅ 16/10, fechado pelo usuário (código pronto em 15/10, as provas reais e o uso no app ficam para quando ele quiser). Os cinco fiscais do Python passam no Windows e no Linux, 1.874 testes com 92,8% de cobertura (no Linux daqui, os dois testes do `install.sh` que já falham na `main`), e os do app também: `tsc` estrito, ESLint, Prettier, 175 testes do Vitest e 45 cenários do Playwright no Windows (11 novos: a aba, a sessão aberta em outro lugar, a delegação aberta pela linha, o ▶, o `!`, o comando perigoso, o comando que termina depois, o HTML isolado, a página do agente, o link de arquivo gravado, o mermaid); no Linux, numa imagem com compilador, o `node-pty` compila e o terminal, a prévia e o navegador passam. Feito em três partes, cada uma com um commit. **A aba Executores:** o SDK dos plugins cresce para a versão 3 só acrescentando (`Executor.sessions`, `SessionInfo`, `Capabilities.sessions` e `fork`), e o plugin embutido continua com `api: 2`, a versão do consentimento que o usuário deu (a impressão digital guarda o número); a listagem é a do `list_sessions` do Agent SDK 0.2.165, o título é o que o Claude Code guardou ou o primeiro prompt, sem gastar crédito; a sessão abre com a trilha do 9c, lida da transcrição e dos eventos ao vivo, nada copiado para o `atlas.db`; continuar é `--resume`, um processo por sessão no Atlas; os comandos de barra vêm do `initialize`; a linha do `delegate` abre a sessão, e os cartões da bancada saíram. **Decidido pelo usuário em 15/10, depois da pesquisa:** a sessão aberta em outro lugar é vista no registro de processos do próprio Claude Code (`~/.claude/sessions`, o que a extensão do VS Code e o claude-agent-acp leem), e abre com o aviso do VS Code e as saídas do Codex: só ler, continuar numa cópia (`--fork-session`) ou continuar aqui mesmo; e o comando que termina depois dos 30 s avisa o Atlas, que responde de novo (o Claude Code faz assim com comando em segundo plano). **O terminal** é feito com o xterm.js e o `node-pty` (o `node-pty`, opcional: sem compilador no Linux o app funciona sem terminal), e o fim de um comando vem de marcas OSC 133 com um nonce no PowerShell, no bash e no zsh, com uma regra própria de código de saída no PowerShell; o cmd termina quando fica quieto, sem código. O ▶ só em bloco de shell, desligado enquanto a resposta chega; o perigoso pergunta ao `trust.check`; a saída entra como linha de terminal nas tags do modo bash do Claude Code, limpa como a de uma ferramenta, e o Atlas responde num turno que termina. **A prévia** (`PreviewTarget`, cada aba da sua conversa, a pilha de até 4 links, o `desktop_preview` atrás da busca), num quadro com uma lista de permitidos. **Achados e consertados:** a pasta com acento não era achada (o Claude Code troca por hífen tudo que não é letra ou número ASCII); a sessão era lida na ordem do arquivo, com ramos rebobinados e cadeias laterais (agora a cadeia, como o SDK); o Claude Code falso travava quando a entrada fechava no meio do turno; o pseudoterminal do Windows desenha a quebra de linha movendo o cursor, e a saída do cmd vinha colada ao prompt; o quadro `srcdoc` herda a política do app, que proíbe script, e o script da página nunca rodava (a página agora vem de um protocolo próprio, `atlas-preview:`, com política própria, scripts rodando e nenhuma conexão); e a normalização tomava `page.html` por domínio. **Provas reais** prontas e ensaiadas nos falsos, passando (`scripts/prove_tab.py`: uma sessão começada no terminal com `claude -p`, listada e continuada pelo Atlas; e o ▶ num teste que falha, com o Atlas dizendo por quê); não rodadas, por decisão do usuário em 15/10 (custo estimado: 2 turnos da assinatura e ~US$ 0,002 no gpt-6-luna, pior caso US$ 0,26). **Provas reais feitas em 21/10** (`scripts/prove_tab.py openai gpt-6-luna`, com o Claude Code na segunda conta, porque a principal estava no limite semanal): a sessão começada no terminal com `claude -p` foi listada, aberta e continuada pelo Atlas, e disse a palavra de volta; o ▶ rodou o teste que falha no terminal do painel, e o Atlas disse por quê. 3 chamadas do Atlas, US$ 0,0007, e dois turnos curtos da assinatura. O roteiro passou a apagar a sessão da prova na pasta da conta em que ela rodou (`CLAUDE_CONFIG_DIR`), não só no `~/.claude`. Falta o uso do usuário no app |
| 16g | Anexos na conversa (decididos em 16/10: Anexos na conversa, acima): arrastar, soltar ou colar arquivos de qualquer formato na caixa, guardados na casa; a imagem vai direto ao modelo que vê imagem e, ao que não vê, como a descrição do modelo de apoio; o documento é aceito com qualquer modelo, texto pequeno inteiro e o resto por uma nota, lido com o `read_file`; vídeo direto quando o modelo aceita, senão nota, e áudio como nota. Pedido do usuário em 16/10 | 16f | ✅ 18/10, fechado pelo usuário (o uso dele no app, o lado Linux e a prova com modelo real ficam para quando ele quiser). Código pronto em 16/10. Os cinco fiscais do Python passam no Windows, 1.967 testes com 92,7% de cobertura; os do app também: `tsc` estrito, ESLint, Prettier, 205 testes do Vitest e 52 cenários do Playwright (2 novos: soltar, colar e escolher arquivos, fichas por formato e cor, o que o modelo recebeu, a conversa reaberta, o painel ao lado; o arquivo pesado demais recusado ao soltar). Os testes rodaram só no Windows; o lado Linux ficou por fazer. **Prova real em 21/10** (`scripts/prove_attachments.py openai gpt-6-luna openai o3-mini`): pelo servidor de verdade, com os arquivos enviados como o app envia, o gpt-6-luna leu o código escrito num PDF e a cor de uma imagem na mesma mensagem; o o3-mini, que não vê imagem, disse a cor pela descrição que o claude-haiku-5-5 fez antes do turno. 6 chamadas, US$ 0,0056 |
| 16h | Várias contas ao mesmo tempo num executor (pedido do usuário em 18/10; decidido em 18/10 e refeito em 19/10: Várias contas ao mesmo tempo, acima): **duplicar o executor do Claude Code e pôr uma conta em cada um**, **pôr cada conta num projeto** (substitui a recusa de 12/10 de separar contas por pasta), **continuar trocando de conta e seguindo no mesmo projeto**, na mesma sessão, e (19/10) **cada conta com a sua configuração e o seu histórico intactos**. Uma pasta do Claude Code por conta (`~/.claude` e `~/.claude-<apelido>`), com o login do próprio Claude Code; a troca no meio da conversa abre uma cópia na outra conta (`--resume <arquivo> --fork-session`); arquivar e apagar uma sessão | 16f | ✅ 19/10, fechado pelo usuário (a prova real com as contas dele fica para quando ele quiser). O desenho de 18/10 (o token no cofre, uma pasta só) foi implementado em 18/10 (`153d3e8`) e provado com as contas do usuário; em 19/10 o usuário trocou pelo de uma pasta por conta. Código refeito em 19/10: os cinco fiscais do Python passam no Windows, 2.040 testes com 92,6% de cobertura, e os do app também (`tsc`, ESLint, Prettier, 267 testes do Vitest e 57 cenários do Playwright, 1 novo: a conta adicionada pelo apelido, o login dela na pasta própria e a sessão trocada para ela depois do aviso, com a cópia lá e o original intacto). O SDK dos plugins vai para a versão 12. **Prova real em 21/10, com as contas do usuário** (`scripts/prove_route.py`, passos 1, 2, 5 e 6): cinco contas logadas ao mesmo tempo, cada uma com o seu uso; a troca no meio da conversa lembrando uma palavra, com uma sessão nova na conta de destino e o arquivo da de origem igual byte a byte; a volta; apagar a cópia numa conta deixando a outra intacta; a pasta começando na conta dela; o aviso da troca com os 14 conectores do claude.ai que a principal não tem; e o vazamento #80580 não apareceu (uma conta sem `CLAUDE.md` próprio não recebeu a memória do `~/.claude` principal). Falta só arquivar, que é do app (o uso do usuário), e olhar o #74393 (`/plugin install` noutra conta), que pede instalar um plugin |
| 16i | Arquivos na sessão do executor (pedido do usuário em 19/10: Arquivos na sessão do executor, acima): colar, soltar ou escolher imagem e documento na caixa da sessão, como na do Atlas e nos bots, pelo caminho que o SDK oficial do Claude Code dá: a imagem como bloco `image` da mensagem, o resto pelo caminho, lido pelo `Read` dele | 16g, 16f | ✅ 19/10, fechado pelo usuário (a prova dele no app real fica para quando ele quiser) |
| 16j | O SDK de plugins completo (pedido do usuário em 20/10: O SDK de plugins completo, acima): skills e servidores MCP de dentro do plugin, no formato do Claude Code; `register_tool` e `on_unload` (SDK versão 14); `atlas plugins new` e `validate`, e o guia (`src/atlas/docs/pages/extending/plugins.md`). A prova real são os dois plugins do usuário, o do ERP e o de vídeo com o Remotion, que ficam na casa dele | 16, 16c | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Código pronto em 20/10, provado nos testes (o carregador e um servidor MCP de verdade, o modelo falso); falta a prova com os dois plugins do usuário |
| 16k | O formato de arquivos do Claude Code inteiro no plugin (O SDK completo, acima): ganchos com os eventos e o JSON do Claude Code, comandos, `bin/`, `userConfig`, agentes como subagentes pré-definidos da `delegate`, e monitores | 16j | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Código pronto em 20/10 (docs/ARCHITECTURE.md, The whole of Claude Code's plugin folder), com os ganchos em Python que estavam no 16l (SDK versão 15). Provado nos testes: comandos de verdade como ganchos dentro de turnos reais, a conversa em que o modelo falso delega a um agente de plugin e o subagente recebe as instruções e só as ferramentas dele, um programa do `bin/` rodado pelo nome, as opções do `userConfig` chegando ao MCP e ao gancho, e um monitor de verdade mandando avisos. Os cinco fiscais passam, 2.228 testes com 91,9% de cobertura. **Prova com plugins de verdade, sem crédito:** os 54 plugins da loja oficial do Claude Code instalada nesta máquina passaram pelo `atlas plugins validate`; 42 carregam, todos menos os 12 de LSP (de fora, decidido). A prova achou e consertou quatro diferenças: o manifesto é opcional no Claude Code, o `.mcp.json` pode vir sem o `mcpServers` (o do GitHub era lido como sem servidor nenhum), os ganchos `async` e o `if` (antes ignorado, o que faria um gancho rodar em chamadas que não eram dele), e o segredo que um servidor MCP cita sem declarar, que recusava o GitHub e o Terraform (o usuário escolheu, em 20/10, contá-lo como declarado e mostrá-lo no consentimento). Falta ligar um deles no app e usar |
| 16l | O resto do Python no plugin: ganchos em Python, comando de barra e de terminal, `ctx.llm`, mensagem a uma conversa, provedor de modelo | 16k | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Código pronto em 20/10 (docs/ARCHITECTURE.md; os ganchos em Python vieram no 16k): SDK versão 16 com `register_command` (`/plugin:nome`, responde sem chamar o modelo), `register_cli_command` (`atlas <nome>`), `ctx.llm` (o modelo do usuário, contado como `plugin:<nome>` no `atlas usage`), `ctx.notify` (mensagem que começa um turno) e `atlas.providers` (provedor de modelo como o `models.toml` escreve). **Mais rígido, marcado:** `llm` e `notify` gastam crédito por iniciativa do plugin, então são declarados e aparecem no consentimento. Provado nos testes contra o modelo falso: o comando que responde sem chamada de modelo e fica na conversa, a chamada do plugin com o propósito dele no registro, a resposta em JSON conferida, a mensagem que começa um turno, o comando de terminal com o código de saída dele, e o provedor que entra no catálogo sem trocar um que já existe |
| 16m | A tela própria do plugin no app, e os temas | 16l | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Código pronto em 21/10 (docs/ARCHITECTURE.md, A plugin's own screen in the app). **Decidido pelo usuário em 21/10: a tela roda dentro do app com todo o acesso dele, sem caixa de areia**, dito no consentimento; isolar num quadro próprio fica para quando a loja abrir. O `desktop/plugin.js` vem do servidor (funciona com casa em outra máquina), o carregador aceita só `@atlas/plugin-sdk`, `react` e `react/jsx-runtime`, com importação por blob e prazo de 10 s, e a tela acrescenta página com entrada na barra lateral e aba do painel lateral; o Python dela atende pelo `register_call` (SDK versão 17). Barra de status, paleta e temas ficaram de fora: o app não tem essas superfícies. **Achado no teste:** a barreira de erro não pegava o `render()` do plugin, e uma tela com erro derrubaria o app; consertado. Provado no vitest e de ponta a ponta no app de verdade (página cujo botão chama o Python, painel seguindo o app, tela que importa `node:fs` falhando sozinha, tudo saindo ao desligar) |
| 16n | Recarregar o plugin ao salvar, instalar por endereço e dependência entre plugins | 16m | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). Código pronto em 21/10 (docs/ARCHITECTURE.md, For whoever writes and installs plugins). **Recarregar ao salvar, escolha própria marcada:** o Claude Code só recarrega no `/reload-plugins`; o servidor do Atlas olha os plugins do usuário a cada segundo e recarrega o que mudou, Python e tela, no app aberto, sem tirar a página da tela; uma gravação que quebra o plugin diz o porquê, e a que conserta volta a rodar. O mesmo olhar pega um plugin ligado ou desligado pelo terminal, que antes esperava reiniciar. Botão Recarregar na tela de Plugins (`plugin.reload`). **Instalar por endereço:** `atlas plugins install` (git, `owner/repo`, `#pasta`, pasta local), `--ref` com ramo, tag ou commit, `update` e `remove`; chega desligado, nunca substitui um plugin do Atlas nem uma pasta escrita pelo usuário, e o `remove` guarda os dados dele. **Dependências, do Claude Code** (`dependencies` com faixa semver do npm): só roda com as dependências rodando, e não se desliga uma de que outro ligado precisa; **escolha própria marcada:** o Claude Code liga as dependências junto, aqui cada uma pede seu consentimento, então o Atlas diz qual ligar antes; e uma que falta é dita, não instalada, porque ainda não há catálogo. Carregam antes. Ficaram para depois: pacotes Python do plugin, marketplaces e atualização automática. **Achado no teste:** recarregar a tela tirava a página dela da tela; consertado. Provado com repositórios git de verdade, um servidor de verdade recarregando ao salvar, e de ponta a ponta no app |
| 16o | O Codex como executor (decidido em 20/10: Codex, Gemini CLI e DeepSeek, acima): o plugin `codex` pelo `codex app-server`, achado no PATH, no app da Microsoft Store e no Homebrew; as permissões na caixa de areia dele, o automático pelo revisor dele (`auto_review`), o steer, os modelos, as sessões, a transcrição, os conectores, os limites do plano, as outras contas e os anexos; o núcleo sem o Claude Code fixo na `delegate` e na lista dos automáticos | 16c, 16i | ✅ 21/10: a prova real (`scripts/prove_codex.py`) passou nos 6 passos com o Codex do usuário, gastando 11% do limite de 30 dias do plano `free` dele; o painel passou a dizer "Limite de 30 dias" onde dizia semana |
| 16p | O Gemini CLI como executor, com o cliente ACP do núcleo (`atlas.plugins.acp`), estudado antes do código | 16o | ✅ 21/10: a prova real (`scripts/prove_gemini.py --vault`) passou nos 7 passos com a chave de API do cofre; o Google não aceita mais login de conta pessoal no Gemini CLI, e a chave virou o segredo do plugin |
| 16q | O DeepSeek (`dsh`) como executor, pelo cliente ACP do 16p, com os logs que ele manda à DeepSeek mostrados ou desligados | 16p | ✅ 21/10 no código e nas leituras reais sem prompt (o handshake, as opções, o `--patch` dos logs e o modo de permissão); a prova real com o modelo (`scripts/prove_deepseek.py --vault`) fica para quando houver uma chave da DeepSeek com saldo no cofre |
| 16r | A prova do SDK com os dois plugins do usuário (decidida em 21/10: A prova do SDK com os plugins do usuário, acima): o do ERP contra o MCP dele, depois o de vídeo com o Remotion; o que faltar no SDK entra só quando um deles precisar (a tela mostrar arquivos, o Python avisar a tela, os pacotes do Node) | 16n | |
| 16s | A loja (decidida em 24/10 e revista em 25/10: Loja, acima): o repositório público `atlas-marketplace` no GitHub, onde cada item entra por PR revisado pelo usuário, fixado num commit; quatro tipos separados, a skill dizendo os programas e os MCPs de que precisa; no app, a entrada Loja com as abas Plugins, Skills, MCPs e Executores, os oficiais marcados, uma página por item, instalar e atualizar pelo servidor da casa; sem downloads nem estrelas por enquanto. Pedido do usuário em 24/10 | 16n | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). **Código pronto em 25/10 (docs/ARCHITECTURE.md, The marketplace):** o `atlas.marketplace` lê a loja inteira num arquivo compactado do GitHub, por HTTPS e sem git, guarda a última leitura boa e o registro do que instalou (`marketplace/` na casa, que viaja com ela), e põe cada tipo no seu lugar: a skill na biblioteca, como do usuário (o curador não a arquiva); o MCP no `mcp.json`, que o servidor relê quando muda; o plugin e o executor desligados, pelo caminho de sempre dos plugins. A tela Loja no app, e `atlas marketplace list`, `show`, `install`, `update`, `remove` e `check` no terminal. O repositório `marqueskaio/atlas-marketplace` nasce sem itens, com as regras no CONTRIBUTING, o usuário como revisor de tudo (CODEOWNERS) e o CI rodando o `atlas marketplace check --load` da main do Atlas nos itens de cada PR. **A prova:** 52 testes da loja contra um GitHub falso que serve repositórios git feitos no teste; o servidor com dois clientes ouvindo a mesma instalação; o `mcp.json` relido com servidores MCP de verdade; e o e2e no app real: as abas, a página da skill com o programa que falta e o MCP que vem junto, a instalação dos dois, o segredo guardado, o plugin ligado pelo consentimento e o modelo recebendo a skill da loja e a do plugin. Contra o GitHub de verdade, numa casa temporária: o `check --load` e a instalação de uma skill pública fixada num commit. **Achados:** a pasta de um plugin precisa ter o nome do manifesto, então o item é copiado com o nome dele, e um manifesto de outro nome é recusado com o motivo; o cartão da loja não mudava quando o plugin era ligado pela tela de Plugins, aberta por cima dele (agora a loja relê); o MCP do e2e fica esperando um segundo segredo, para o `npx` nunca baixar e rodar um pacote de verdade no CI; e o agente instalaria pela loja sem perguntar: `atlas marketplace install`, `update` e `remove` viraram decisão do usuário, como o `enable`, porque um MCP instalado conecta na hora |
| 16t | Como as pessoas aprendem a escrever um plugin (decidido em 25/10): o guia (`src/atlas/docs/pages/extending/plugins.md`) com os canais, os executores e a lista de tudo que o SDK oferece, um teste de cada lado (Python e tela) falhando quando o SDK ganha um nome que o guia não diz; a skill `write-plugin`, que vem com o Atlas e escreve o plugin com o usuário (na pasta da conversa, `validate`, instalar perguntando, ligar fica com ele, publicar na loja só quando pedido); e no app, "Faça o seu" na loja e "Escrever um plugin" na tela de Plugins, abrindo uma conversa nova com `/write-plugin ` na caixa, ao lado do link do guia. Pedido do usuário em 25/10 | 16s | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). **Código pronto em 25/10 (docs/ARCHITECTURE.md, How people learn to write one).** **A prova:** o e2e da loja clica em "Faça o seu", a caixa recebe `/write-plugin `, e o modelo recebe os passos da skill; o dos plugins abre a mesma conversa pela tela de Plugins. **Achados:** `atlas plugins install`, `update` e `remove` não eram perguntados quando o agente rodava, e agora são, como o `enable`; e a caixa de uma conversa nova não recebia texto de fora, só a de uma conversa aberta |
| 17 | Plugins oficiais, um de cada vez, cada um com a sua tela | 16 | |
| 17a | O plugin `voice` (decidido em 24/10: Voz, acima): o áudio do Telegram e do app transcrito pela OpenAI, o 🎤 da caixa que dita, a fala só quando o usuário pede, e a conversa por voz de microfone aberto com o `gpt-live-1` passando o trabalho ao Atlas, no modelo da conversa ou num fixo só para voz (GPT-6 Luna por padrão); o núcleo só ganha três aberturas no SDK. Pedido do usuário em 24/10 | 16g, 16m | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). **Parte 1, código pronto em 24/10 (docs/ARCHITECTURE.md, Voice):** o leitor de anexo no SDK (versão 21, `register_reader`, declarado em `atlas.readers` e mostrado no consentimento) e o plugin `voice` com o `gpt-transcribe` da OpenAI (US$ 0,0045 por minuto). O áudio é transcrito uma vez e guardado com o anexo, no mesmo ponto da descrição de imagem; sem chave, ou com a OpenAI recusando, fica a nota de hoje e o turno segue. **Escolha própria, marcada:** um áudio mandado no meio de um turno é transcrito antes de entrar (a imagem nesse caso não é descrita), porque mandar áudio no meio é comum no celular. Provado contra o servidor falso, que agora também faz a transcrição da OpenAI, e pelo Telegram num servidor de verdade. **Parte 2, código pronto em 24/10:** o resto do 17a. A fala pedida é a ferramenta `voice_speak` (o `gpt-4o-mini-tts`, em Ogg Opus); o SDK vai para a versão 22 com `ToolHost.send_file`: o arquivo fica nos anexos da casa, aparece no turno do app (`message.file`) e vai ao Telegram como balão de voz (`sendVoice`), por um `send_file` do canal. As telas de plugin vão para a versão 2: botões na caixa (`box`), `host.insertText` e `host.send`. O 🎤 dita na caixa, sem mandar. A conversa por voz é o `gpt-live-1` em WebRTC no app; o Python do plugin abre a sessão com a chave, que nunca vai ao app; cada pedido vira um turno da conversa com a nota de fala (um gancho `UserPromptSubmit`, sem mudar o núcleo), no GPT-6 Luna sem raciocínio, a não ser que a configuração diga `conversation`. Fecha no botão, numa frase de parar dita sozinha ou depois de 120 s de silêncio. **Escolhas próprias, marcadas:** o modelo de voz é trocado na conversa enquanto a voz roda (o protocolo escolhe modelo por conversa) e devolvido no fim; um pedido feito com um turno rodando corrige o turno em vez de pará-lo; o arquivo mandado não fica no histórico, e a conversa reaberta mostra o resultado da ferramenta no lugar da ficha. Provado contra a OpenAI falsa (transcrição, fala e sessão ao vivo), pelo Telegram num servidor de verdade, no Vitest (o motor da conversa com uma ligação falsa) e de ponta a ponta no app com o microfone falso do Chromium: os dois botões, o ditado chegando à caixa sem ser mandado, e a sessão ao vivo chegando à OpenAI com a oferta do app e a chave do servidor. A voz falada e ouvida de verdade fica para a prova do usuário com a OpenAI (perguntar antes: cerca de US$ 0,25 por 5 minutos) |
| 17b | O ditado em qualquer lugar do plugin `voice` (decidido em 25/10: Ditado em qualquer lugar, acima): um atalho global (Caps Lock de fábrica) grava e, no segundo toque, cola o texto cru no programa em foco; os ditados ficam na casa com o áudio, na página "Ditados". As telas de plugin ganham o atalho global, a colagem no programa em foco e a barrinha. Pedido do usuário em 24/10 | 17a | ✅ 27/10, fechado pelo usuário (a prova fica para quando ele quiser). **Código pronto em 25/10 (docs/ARCHITECTURE.md, Dictation anywhere):** o SDK vai para a versão 23 (`atlas.desktop`: `shortcut` e `paste`, mostrados no consentimento) e as telas para a versão 3 (`ctx.shortcut`, `ctx.paste`, `ctx.bar`). O atalho é o `globalShortcut` do Electron; a colagem usa a área de transferência e um Ctrl+V apertado por um PowerShell que fica aberto no Windows (`osascript` no Mac, `xdotool` ou `wtype` no Linux), sem biblioteca nativa, e devolve o que estava copiado. Com o foco no próprio Atlas, o texto é digitado onde está o cursor. Um programa aberto como administrador não aceita a colagem: o texto fica copiado e a barrinha avisa. **Achado na prova:** o Caps Lock como atalho também trocava as maiúsculas; o app aperta a tecla mais uma vez, com o atalho solto. Os ditados ficam na pasta de dados do plugin com o áudio, na página "Ditados" (ouvir, copiar, apagar, trocar a tecla). **Escolhas próprias, marcadas:** os ditados da caixa também ficam guardados; a gravação é guardada antes da transcrição, e uma que falhou pode ser escrita de novo. Provado nos testes do Python e do app, de ponta a ponta no app com o microfone falso e, no Windows, com as peças de verdade: o Caps Lock apertado pelo sistema ouvido sem trocar as maiúsculas, a barrinha sem roubar o foco e o texto com acentos colado na janela de outro programa, com a área de transferência devolvida. Falta a prova do usuário com a OpenAI de verdade (menos de US$ 0,01) e com o microfone dele, e a de um programa aberto como administrador |
| 18 | Instalador de uma linha, e a chave pedida pela tela na primeira abertura (decidido em 13/10: Instalador, acima). Decidido pelo usuário em 13/10: vem antes do 15, para mandar o Atlas a testadores convidados no repositório privado; os scripts num gist público; a chave conferida sem custo e barrada só quando recusada; sem o login OAuth da OpenRouter. Desenho fechado em 13/10 | 10 | ✅. Os dois testadores convidados instalaram com a linha do gist e usaram sem problema: um num MacBook e outro no Windows, além do Windows do usuário e do CI no Windows e no Ubuntu. Achado do testador no Windows: com a chave gratuita do Gemini, o Gemini 3.6 Flash respondeu HTTP 503 ("high demand", o Google recusando quem não paga quando está cheio); com outra chave funcionou. Não é defeito do instalador nem da tela de chave |
| 18b | O app empacotado sem assinatura (decidido em 15/10: Instalador, acima): o `Setup.exe` do NSIS no Windows, o DMG com a assinatura ad-hoc no Mac, o AppImage no Linux; instala, põe o atalho na área de trabalho e se atualiza pelo próprio app (o electron-updater no Windows e no Linux, um atualizador nosso no Mac). Grátis, com o aviso do Windows e do Mac na primeira abertura. Dividido pelo usuário em 15/10 | 18 | |
| 18c | A assinatura do app empacotado: MSIX pela Microsoft Store ou SignPath no Windows, DMG notarizado no Mac (estudados em 13/10: Instalador, acima). Tira os avisos do 18b e troca o atualizador nosso do Mac pelo do Electron. Vem quando o repositório abrir | 18b | |
| 19 | Site e documentação pública (decididos em 25/10: Site e documentação pública, acima): Astro com Starlight em `site/`, na Cloudflare; a home, as Versões e as News, e a documentação de quem usa, que vai também dentro de cada versão para o Atlas responder sobre si; pronto para agentes. Pedido do usuário em 25/10 | 18 | **Parte 1, código pronto em 25/10:** o site em `site/`, com Astro 7 e Starlight 0.42. A home é o planetário em WebGL2 próprio, sem biblioteca: as linhas são calculadas no shader, e a esfera, as órbitas e o caminho entre as duas não custam CPU por quadro. Ela tem seis capítulos (as contas, a rota com o cometa e os medidores da janela de cinco horas, as máquinas, a memória e o fecho), e depois o portão de aprovação com comandos e vereditos de verdade, o resto do Atlas, a instalação com as abas do sistema de quem visita, e as últimas mudanças. Sem WebGL fica o primeiro quadro desenhado em SVG no build, pela mesma fórmula; com movimento reduzido, a câmera troca de plano sem voar. No celular a cena é um painel no alto e o texto passa por baixo dele. A documentação tem quatro páginas (O que é o Atlas, Instalar, Executores e contas, Várias máquinas), conferidas contra o código e as telas do app. As News saem vazias, com o formato do item escrito, e o Changelog é o `git log` da `main` no formato da atualização; os dois têm RSS. Para agentes: cada página tem o gêmeo em Markdown, `llms.txt` e `llms-full.txt` na ordem da barra lateral, `robots.txt` com `Content-Signal` e os robôs de IA, a skill `atlas` em `/.well-known/agent-skills/` com o digest dos bytes, e um Worker que responde Markdown a `Accept: text/markdown` e põe os cabeçalhos `Link`. O CI ganha o job `site`. **Escolhas próprias, marcadas:** o `Content-Signal` libera busca, resposta e treino (decidir antes de publicar); a skill publicada e o gêmeo em Markdown entram já, antes do item 4; a instalação ainda aponta para o gist; o domínio é um marcador (`SITE_URL`). Provado: `npm run check` (astro check, tipos do Worker, eslint, prettier e 32 testes); o build com a conferência do `dist` (toda página com gêmeo, o digest da skill, nenhum link interno quebrado); o Worker no runtime da Cloudflare (`wrangler dev`), respondendo Markdown com `Vary` e `Content-Signal` e pondo os `Link` na home; a home vista no Edge em 1920, 1440, 1024 e 390 px, capítulo por capítulo; e o Lighthouse com renderização por software: 98 de desempenho e 100 de acessibilidade e de boas práticas na home, e SEO 92, porque o Lighthouse não conhece a linha `Content-Signal` que o Is It Agent Ready pede. **Parte 2, código pronto em 25/10 (docs/ARCHITECTURE.md, The documentation):** as páginas no pacote (`atlas.docs`, assunto novo com o seu contrato no import-linter), lidas pelo site e pelo `atlas docs`; a skill embutida `atlas`; a referência gerada dos comandos e das configurações (três campos sem comentário ganharam o seu); as cinco referências de `docs/` viradas páginas, mais o portão de aprovação e a origem dos dados do Claude Code; 12 páginas ao todo. O guia de plugins, que a 16t aumentou no mesmo dia, veio inteiro para a página, e a cópia da skill `write-plugin`, os testes dos dois lados e o link do app foram junto com ele. O plugin de validação de links do site saiu (ele calcula o id da página pela pasta `src/content/docs` e dava todos os links por quebrados) e o `check-dist.mjs` passou a conferir também cada `#` de seção, provado com uma âncora quebrada de propósito. Provado: os testes de `tests/docs` (o cabeçalho, a ordem, achar, buscar, o comando, a referência atual, cada comando que a skill dá rodando, e a página chegando inteira pelo `run_command` do agente, com acentos), ruff, pyright, import-linter, deptry e a suíte inteira; no site, `npm run check` e o build. **O ícone do app, código pronto em 25/10 (pedido do usuário):** a marca do Atlas no lugar do ícone do Electron, na janela, na barra de tarefas, no Dock, no atalho do Menu Iniciar e na entrada do Linux. Provado: o `.ico` lido pelo carregador do próprio Windows em cada tamanho, de 16 a 256 px, com os cantos transparentes no fundo claro e no escuro; o teste do atalho lendo o ícone que ele guarda; e o e2e do app inteiro. **Parte 3, o site e a documentação revistos, código pronto em 25/10 (pedido do usuário: mais bonito, animações melhores, texto estratégico, documentação impecável, rápido e bem posicionado nas buscas):** a home ganhou um texto novo, conferido contra o código e as telas do app (o título "Every coding agent. Every account. One window.", quatro fatos sob a instalação, a comparação "sozinhos × no Atlas", cartões que levam ao guia de cada recurso e oito perguntas frequentes); animações sem JavaScript (a entrada do hero em sequência, as seções subindo com a rolagem por `animation-timeline: view()`, a transição entre páginas por `@view-transition`, as respostas que deslizam) e uma luz que segue o ponteiro nos cartões, com um só ouvinte, tudo parado com movimento reduzido; para as buscas, JSON-LD (Organization, WebSite, SoftwareApplication, FAQPage, e TechArticle com BreadcrumbList em cada página da documentação), o título de busca de cada página com as palavras que se procuram (`site/src/lib/seo-titles.ts`, pelo `routeMiddleware` do Starlight), a imagem de compartilhamento nova e em toda página, `noindex` na 404 e um rodapé com os guias; o logo de 17 KB passou a 7,7 KB, os shaders do planetário compilam em paralelo onde o navegador deixa, e as fontes da documentação vêm antes do primeiro quadro (o CLS de 0,187 foi a 0). A documentação ganhou dez páginas (Modelos e chaves, Rotinas, Telegram, Voz e ditado, O navegador, Skills e o marketplace, Privacidade, Backups/atualizações/homes, Solução de problemas e Perguntas e respostas) e um mapa na página inicial, e três trechos que o app já tinha deixado para trás foram corrigidos (os executores ligam em Configurações → Executores; a memória e a alma se editam em Configurações → Personalização). Provado: `npm run check` (44 testes, os de SEO novos), o build com a conferência de cada link e âncora, `tests/docs`, as telas em 1440, 1280 e 390 px, e o Lighthouse com renderização por software: home 93 de desempenho e 100 de acessibilidade e boas práticas, documentação 99 de desempenho; SEO 92 nas duas pelo mesmo `Content-Signal`. **Falta:** a prova com um modelo de verdade (perguntar antes: centavos), o domínio e o projeto na Cloudflare (o usuário), a nota do Is It Agent Ready no ar, a rotina das News, o português e o link do guia de plugins no app, que passa para o site quando houver domínio |
| 20 | Atlas Cloud | 19 | |
