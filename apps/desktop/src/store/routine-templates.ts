/**
 * The routine templates (step 25): routines written by Atlas, ready to adjust, since no public
 * catalog has any. Two groups: a project's, run by a coding agent in a folder; and personal ones,
 * run by Atlas. Each has a tier (essential, recommended, useful), how often it runs, its effort,
 * and the plugin it needs, if any; its words are in the language files (`t.templates`), so a
 * template reads in the app's language. Add opens the routine's form filled in.
 */
import type { RoutineSave } from '@/protocol/protocol.generated'

export type TemplateGroup = 'personal' | 'project'
export type TemplateTier = 'essential' | 'recommended' | 'useful'

export interface RoutineTemplate {
  id: TemplateId
  group: TemplateGroup
  tier: TemplateTier
  /** As `atlas routines create --schedule` takes it. */
  schedule: string
  effort: 'high' | 'low' | 'medium'
  /** A project's runs in a new workspace each time: it changes code. */
  workspace?: boolean
  /** The plugin it needs on: its card says so, and Add offers to install it first. */
  plugin?: string
}

export type TemplateId =
  | 'agenda'
  | 'backup'
  | 'bills'
  | 'dependencyUpdates'
  | 'failingChecks'
  | 'mailSummary'
  | 'missingTests'
  | 'newsDigest'
  | 'releaseNotes'
  | 'securitySweep'
  | 'stalePullRequests'
  | 'weeklyReview'

export const TEMPLATES: RoutineTemplate[] = [
  { effort: 'high', group: 'project', id: 'securitySweep', schedule: 'every monday at 9am', tier: 'essential' },
  {
    effort: 'low',
    group: 'project',
    id: 'stalePullRequests',
    plugin: 'github',
    schedule: 'weekdays at 9am',
    tier: 'recommended'
  },
  {
    effort: 'medium',
    group: 'project',
    id: 'missingTests',
    schedule: 'every friday at 4pm',
    tier: 'useful',
    workspace: true
  },
  {
    effort: 'low',
    group: 'personal',
    id: 'mailSummary',
    plugin: 'google',
    schedule: 'every day at 8am',
    tier: 'essential'
  },
  {
    effort: 'low',
    group: 'personal',
    id: 'agenda',
    plugin: 'google',
    schedule: 'every day at 8pm',
    tier: 'recommended'
  },
  { effort: 'low', group: 'personal', id: 'backup', schedule: 'every day at 3am', tier: 'useful' },
  {
    effort: 'low',
    group: 'project',
    id: 'failingChecks',
    plugin: 'github',
    schedule: 'weekdays at 9am',
    tier: 'essential'
  },
  {
    effort: 'medium',
    group: 'project',
    id: 'dependencyUpdates',
    schedule: 'every monday at 10am',
    tier: 'recommended',
    workspace: true
  },
  { effort: 'low', group: 'project', id: 'releaseNotes', schedule: 'every friday at 5pm', tier: 'useful' },
  {
    effort: 'medium',
    group: 'personal',
    id: 'weeklyReview',
    plugin: 'google',
    schedule: 'every friday at 6pm',
    tier: 'recommended'
  },
  { effort: 'low', group: 'personal', id: 'bills', plugin: 'google', schedule: 'every monday at 9am', tier: 'useful' },
  { effort: 'low', group: 'personal', id: 'newsDigest', schedule: 'every day at 7am', tier: 'useful' }
]

const TIERS: TemplateTier[] = ['essential', 'recommended', 'useful']

/** A group's templates, the essential first. */
export function templatesOf(group: TemplateGroup): RoutineTemplate[] {
  return TEMPLATES.filter(found => found.group === group).sort((a, b) => TIERS.indexOf(a.tier) - TIERS.indexOf(b.tier))
}

/** The form's start for a template: its words, and a project's on the first coding agent found. */
export function templateDraft(
  template: RoutineTemplate,
  words: { name: string; prompt: string },
  executor: string
): RoutineSave {
  const project = template.group === 'project'

  return {
    effort: project ? '' : template.effort,
    executor: project
      ? { auto: false, draft: false, folder: '', name: executor, workspace: template.workspace ?? false }
      : null,
    name: words.name,
    prompt: words.prompt,
    schedule: template.schedule
  }
}
