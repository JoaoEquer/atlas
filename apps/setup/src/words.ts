/** The Setup's words, in English, or in Portuguese when the system is. The installer's own lines
 * stay as it writes them. */

export type Language = 'en' | 'pt-BR'

export interface Words {
  close: string
  details: string
  doneText: string
  doneTitle: string
  failedText: string
  failedTitle: string
  install: string
  installedText: string
  installedTitle: string
  introText: string
  introTitle: string
  log: string
  open: string
  opening: string
  repair: string
  retry: string
  runningTitle: string
  starting: string
  warnings: string
}

export const WORDS: Record<Language, Words> = {
  en: {
    close: 'Close',
    details: 'Details',
    doneText: 'Atlas is opening. You can close this window.',
    doneTitle: 'Atlas is installed',
    failedText: 'Running it again repairs what was left half done. The details below say why it stopped.',
    failedTitle: 'The installation stopped',
    install: 'Install',
    installedText: 'Atlas is already installed here. It can be opened, or repaired and updated.',
    installedTitle: 'Atlas is installed',
    introText:
      'It downloads its own tools and builds the app on this computer, which takes a few minutes. The tools stay in this folder; only the atlas command joins your PATH.',
    introTitle: 'Install Atlas',
    log: 'Open the log',
    open: 'Open Atlas',
    opening: 'Opening Atlas…',
    repair: 'Repair and update',
    retry: 'Try again',
    runningTitle: 'Installing Atlas',
    starting: 'Starting',
    warnings: 'Worth a look'
  },
  'pt-BR': {
    close: 'Fechar',
    details: 'Detalhes',
    doneText: 'O Atlas está abrindo. Pode fechar esta janela.',
    doneTitle: 'O Atlas está instalado',
    failedText: 'Rodar de novo conserta o que ficou pela metade. Os detalhes abaixo dizem por que parou.',
    failedTitle: 'A instalação parou',
    install: 'Instalar',
    installedText: 'O Atlas já está instalado aqui. Dá para abrir, ou consertar e atualizar.',
    installedTitle: 'O Atlas está instalado',
    introText:
      'Ele baixa as próprias ferramentas e constrói o app neste computador, o que leva alguns minutos. As ferramentas ficam nesta pasta; só o comando atlas entra no seu PATH.',
    introTitle: 'Instalar o Atlas',
    log: 'Abrir o log',
    open: 'Abrir o Atlas',
    opening: 'Abrindo o Atlas…',
    repair: 'Consertar e atualizar',
    retry: 'Tentar de novo',
    runningTitle: 'Instalando o Atlas',
    starting: 'Começando',
    warnings: 'Vale olhar'
  }
}

/** Portuguese when the first language the system prefers is, English otherwise. */
export function languageOf(preferred: readonly string[]): Language {
  return preferred[0]?.toLowerCase().startsWith('pt') ? 'pt-BR' : 'en'
}
