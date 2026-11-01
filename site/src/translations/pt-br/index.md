---
title: 'O que é o Atlas'
description: 'O Atlas é um agente pessoal de código aberto com um app de desktop, e o lugar onde os seus agentes de código rodam em todas as contas e em todas as máquinas.'
sidebar:
  order: 1
---

O Atlas é um agente pessoal de código aberto com um app de desktop. Coloque uma chave de API e você tem um assistente que guarda uma memória que você pode ler, escreve as próprias skills à medida que aprende como você trabalha, roda rotinas agendadas e conversa com você pelo Telegram.

Ele também é onde os seus agentes de código moram. Claude Code, Codex, Gemini CLI e o harness da DeepSeek rodam dentro do Atlas como **executores**: a cópia que você instalou, com as suas configurações, as instruções do seu projeto e o seu próprio login. O Atlas mostra todas as sessões deles, onde quer que tenham começado.

## O que dá para fazer com ele

- **Rodar todos os agentes de código numa só janela.** Cada um que você tem instalado aparece na aba Executores, com as pastas, as sessões e o uso do plano.
- **Usar todas as suas contas ao mesmo tempo.** Pessoal, empresa A, empresa B: cada conta tem a própria pasta, configurações e histórico, e elas rodam lado a lado. Quando uma chega ao limite do plano, a sessão segue na próxima. Veja [Agentes de código e contas](/pt-br/docs/guides/coding-agents/).
- **Alcançar as suas outras máquinas.** Instale o Atlas num homelab, numa VPS ou no PC do escritório e abra pelo app no seu notebook, pelo Tailscale ou por SSH. Veja [Várias máquinas](/pt-br/docs/guides/machines/).
- **Passar trabalho para o Atlas.** Peça em palavras comuns, e o Atlas delega ao agente de código certo, na conta que você disser, acompanha e traz o resultado.
- **Deixar que ele aprenda.** A cada poucas mensagens, uma cópia do agente relê a conversa e guarda o que aprendeu: quem você é no seu perfil, fatos sobre o seu ambiente nas notas dele, o jeito de fazer uma tarefa numa skill.

## Como as peças se encaixam

O Atlas é um servidor, `atlas serve`, e os clientes que falam com ele: o app de desktop, o Telegram e o app nas suas outras máquinas. O servidor roda o laço do agente, cada chamada de ferramenta passa pelo portão de aprovação, e ele guarda a sua memória, as skills e as conversas.

Tudo o que é seu fica numa pasta, `ATLAS_HOME` (`~/.atlas`, a não ser que você escolha outra): o seu perfil, a memória, as conversas, as configurações e o cofre cifrado que guarda as suas chaves de API. O Atlas não tem conta, nem telemetria, nem nuvem própria; o que sai da sua máquina é o que você pede, como as chamadas ao seu provedor de modelo. Os seus agentes de código mantêm os próprios logins nas próprias pastas, e o Atlas nunca mexe neles.

## Quanto custa

O Atlas é grátis e de código aberto, sob a licença MIT. Você traz uma chave de API de um provedor de modelo (OpenAI, Anthropic, Google Gemini ou OpenRouter) ou um modelo local pelo Ollama ou pelo vLLM, e paga a esse provedor pelo que usar. Os seus agentes de código rodam nos planos que você já tem.

## Por onde seguir

**Primeira vez no Atlas?** [Instale](/pt-br/docs/install/) com uma linha no Windows, no macOS ou no
Linux, ou num container, depois [escolha um modelo](/pt-br/docs/guides/models/) e ligue os seus agentes de código.

As páginas abaixo ainda estão em inglês.

| Para...                                                                                               | Leia                                                                                                                            |
| ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Rodar Claude Code, Codex, Gemini CLI ou DeepSeek em várias contas, com uma rota no limite de um plano | [Coding agents and accounts](/pt-br/docs/guides/coding-agents/)                                                                 |
| Abrir o Atlas de um homelab, de uma VPS ou do PC do escritório pelo seu notebook                      | [Several machines](/pt-br/docs/guides/machines/)                                                                                |
| Escolher o modelo, colocar chaves, usar um modelo local, ver quanto ele gasta                         | [Models and keys](/pt-br/docs/guides/models/)                                                                                   |
| Ver e mudar o que o Atlas lembra                                                                      | [Memory](/pt-br/docs/guides/memory/) e [The background review](/pt-br/docs/guides/background-review/)                           |
| Criar agentes com uma missão própria                                                                  | [Bots](/pt-br/docs/guides/bots/)                                                                                                |
| Rodar um prompt ou um comando num horário                                                             | [Routines](/pt-br/docs/guides/routines/)                                                                                        |
| Falar com o Atlas pelo celular                                                                        | [Telegram](/pt-br/docs/guides/telegram/)                                                                                        |
| Ditar em qualquer lugar, mandar áudio, conversar ao vivo                                              | [Voice and dictation](/pt-br/docs/guides/voice/)                                                                                |
| Deixar o Atlas usar um navegador                                                                      | [The browser](/pt-br/docs/guides/browser/)                                                                                      |
| Instalar skills, servidores MCP e plugins feitos por outros                                           | [Skills and the marketplace](/pt-br/docs/guides/marketplace/)                                                                   |
| Saber quais comandos rodam e quais perguntam a você                                                   | [The approval gate](/pt-br/docs/guides/approval-gate/)                                                                          |
| Saber o que fica na sua máquina e o que sai dela                                                      | [Privacy and your data](/pt-br/docs/guides/privacy/)                                                                            |
| Fazer backup, restaurar, manter casas separadas, atualizar                                            | [Backups, updates and homes](/pt-br/docs/guides/backups/)                                                                       |
| Procurar um comando, uma configuração ou uma ferramenta                                               | [Commands](/pt-br/docs/reference/commands/), [Settings](/pt-br/docs/reference/settings/), [Tools](/pt-br/docs/reference/tools/) |
| Acrescentar skills, ferramentas, canais ou um agente de código seu                                    | [Writing a plugin](/pt-br/docs/extending/plugins/)                                                                              |

Algo não funciona? [Troubleshooting](/pt-br/docs/troubleshooting/) reúne os problemas comuns, e
[Questions and answers](/pt-br/docs/faq/) as perguntas comuns.

Depois de instalado, esta documentação também fica na sua máquina, em inglês e na versão que você tem: `atlas docs` lista as páginas, `atlas docs install` mostra uma, e `atlas docs --search PALAVRAS` acha um assunto. O Atlas também lê lá antes de responder a uma pergunta sobre ele mesmo.
