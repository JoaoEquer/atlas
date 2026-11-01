// The home's words in each language. Typed as the English module, so a Portuguese home that lacks a
// part, or holds one of another shape, fails the build.
import * as en from './home'
import * as ptBr from './home.pt-br'
import type { Lang } from './i18n'

export type HomeText = typeof en

export const HOMES: Record<Lang, HomeText> = { en, 'pt-br': ptBr }
