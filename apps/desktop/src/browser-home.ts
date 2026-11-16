/**
 * How the app browser names a home (docs/GLOSSARY.md, `app browser`): the machine, and the home's
 * name there. The window names the home its tabs are shown for, and the browser host names the home
 * a server serves, with this one function, so the two agree. Shared by the window and the main
 * process, so it imports nothing.
 */

/** The machine of this computer's own homes (a machine of the list has its id). */
export const THIS_MACHINE = 'local'

export function browserHome(machine: null | string, home: string): string {
  return `${machine ?? THIS_MACHINE}/${home}`
}
