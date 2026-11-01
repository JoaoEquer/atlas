---
title: 'Instalar'
description: 'Instale o Atlas com uma linha no Windows, no macOS ou no Linux, ou rode num container, depois coloque uma chave e ligue os seus agentes de código.'
sidebar:
  order: 2
---

O Atlas instala com uma linha. O instalador traz tudo de que precisa, o próprio Python, Node e ferramentas, cada um conferido contra um hash fixo, depois constrói o app de desktop e o abre.

## Windows

No PowerShell:

```powershell
irm https://atlasv4.com/install.ps1 | iex
```

## macOS e Linux

Num terminal:

```bash
curl -fsSL https://atlasv4.com/install.sh | bash
```

O site conta cada download do instalador por sistema e país, para saber quantas instalações começaram; ele não guarda nada que diga quem você é (veja [Privacy](/pt-br/docs/guides/privacy/), em inglês).

## Sem terminal

O Atlas Setup faz o mesmo que a linha: baixe o arquivo do seu sistema, abra e clique em **Instalar** (a janela do Setup fala português quando o sistema está em português). Ele mostra cada etapa e abre o Atlas quando termina. Aberto de novo onde o Atlas já está instalado, ele abre o Atlas, ou conserta e atualiza.

- **Windows:** [Atlas-Setup.exe](https://github.com/marqueskaio/atlas/releases/download/setup/Atlas-Setup.exe). Ele ainda não é assinado, então na primeira vez o Windows avisa de um editor desconhecido: clique em **Mais informações** e depois em **Executar assim mesmo**.
- **Mac:** [Apple Silicon](https://github.com/marqueskaio/atlas/releases/download/setup/Atlas-Setup-arm64.dmg) ou [Intel](https://github.com/marqueskaio/atlas/releases/download/setup/Atlas-Setup-x64.dmg). Abra a imagem de disco e o Setup que está nela; na primeira vez o macOS recusa, então abra **Ajustes do Sistema**, **Privacidade e Segurança**, e clique em **Abrir Mesmo Assim** ao lado do Atlas Setup.
- **Linux:** [Atlas-Setup-x86_64.AppImage](https://github.com/marqueskaio/atlas/releases/download/setup/Atlas-Setup-x86_64.AppImage). Torne o arquivo executável (`chmod +x`) e abra. Sem um terminal onde perguntar, o Setup não consegue rodar `sudo`; quando a sandbox do app precisa dele, o Setup termina mostrando os dois comandos para rodar.

O aviso aparece só uma vez, para o Setup: o app que ele instala é construído no seu computador.

## A primeira abertura

O app abre na primeira abertura: cinco etapas, numeradas à esquerda. Cada uma pode ser pulada, e **Ir para o Atlas** sai a qualquer momento.

1. **Modelo**: uma chave de API de um provedor de modelo, OpenAI, Anthropic, Google Gemini ou OpenRouter. O Atlas confere a chave sem gastar nada e a guarda no cofre cifrado.
2. **Executores**: os agentes de código achados neste computador, Claude Code, Codex, Gemini CLI e o da DeepSeek. Ligue os que você usa; um que não está instalado mostra o comando que o instala. Com mais de uma conta num harness, acrescente as outras aqui: quando uma chega ao limite do plano, o Atlas segue na próxima.
3. **Telegram**: fale com o Atlas pelo celular. Crie um bot com o @BotFather, cole o token dele e mande uma mensagem ao bot: você aparece na etapa, e **Deixar entrar** libera a conversa com o Atlas pelo chat. O guia do Telegram, em inglês, tem o resto.
4. **Conexões**: o GitHub e o Gmail com a Agenda, que o Atlas alcança pelos programas deles, o `gh` e o `gws`, cada um com login neste computador. O **Conectar** do GitHub abre a página do GitHub no navegador e mostra o código a digitar lá. O **Configurar com o Atlas** do Google abre uma conversa em que o Atlas guia a configuração do `gws`; a etapa espera atrás de uma faixa no alto, e **Voltar a ela** mostra se está conectado.
5. **Trazer seu trabalho**: escolha os harnesses e até quando voltar, os últimos 7, 30 ou 90 dias, ou tudo. As sessões deles desde então aparecem na aba **Executores** da barra lateral; as mais antigas esperam em **Arquivadas**. Nada é copiado, e os arquivos dos próprios harnesses não são tocados.

Depois é só conversar: cada conversa pode trocar de modelo. Se você usava o Atlas antes de existir a primeira abertura e os seus agentes de código têm pastas paradas há um mês, o Atlas oferece **Trazer seu trabalho** uma vez, sozinho. **Configurações**, depois **Geral**, depois **Refazer a configuração** traz as etapas de volta.

No pé da barra lateral, **Primeiros passos** lista cinco coisas para experimentar: entregar uma tarefa a um agente de código, adicionar uma segunda conta, falar com o Atlas pelo Telegram, agendar uma rotina e criar um bot. O que você já fez aparece marcado, **Começar** leva à próxima, e **Esconder** guarda o cartão de vez.

## Atualizar

O app mostra um botão de atualizar quando há algo novo. Ou rode a mesma linha de novo, ou o Setup, ou digite:

```bash
atlas update
```

Se uma etapa da atualização falhar, o Atlas volta para onde estava.

## Num container

O Atlas também roda com o Docker Compose. A partir de um clone do repositório:

```bash
git clone https://github.com/marqueskaio/atlas.git && cd atlas
docker compose up -d --build
docker compose exec atlas atlas secrets set OPENAI_API_KEY
docker compose exec atlas atlas server token
```

A terceira linha pede a chave sem mostrá-la; use `GEMINI_API_KEY` ou o nome de outro provedor para outro provedor. A última linha mostra o token de que o app de desktop precisa: abra o app com `ATLAS_DESKTOP_URL=ws://127.0.0.1:18790/ws` e `ATLAS_DESKTOP_TOKEN` com esse token.

O container escuta só no loopback do host, e os seus dados ficam num volume com nome. Um backup sai pela saída padrão, nunca com as chaves:

```bash
docker compose exec -T atlas atlas backup -o - > atlas.zip
```

O `atlas restore` põe um backup em qualquer casa.
