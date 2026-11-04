import { useStore } from '@nanostores/react'
import { useCallback, useEffect } from 'react'

import { t } from '@/i18n'
import { BoxActions } from '@/plugins/view'
import {
  $atlasCommands,
  $autoMode,
  $restored,
  $selected,
  DRAFT,
  interrupt,
  newConversation,
  readAtlasCommands,
  runUserCommand,
  send,
  setAutoMode,
  takeRestored
} from '@/store/app'
import { $terminalAvailable } from '@/store/terminals'

import { AgentsButton } from './agent-map'
import { DictateButton } from './dictation'
import { MessageBox } from './message-box'
import { ModelPicker } from './model-picker'
import { TerminalButton } from './side-panel'

/**
 * The box of Atlas's conversation and a bot's (the one `MessageBox`). With a turn running, what it
 * sends steers the turn (the correction is read as soon as the current action completes) and the
 * button beside it stops the turn; a stop puts what never entered back here. "Auto" sits beside
 * send. A message starting with `!` is a command for the conversation's terminal (step 16f): it
 * runs with no model call first, and the user wrote it, so it never asks. `/` lists Atlas's own
 * commands, which run here (`/new`, not in a bot's conversation, which is its one; `/stop`), and the
 * skills the user invokes there; `/name words` sent loads the skill.
 */
/** `to` is who the message is for: a bot's title in its conversation; unset, Atlas. */
export function Composer({ connected, running, to }: { connected: boolean; running: boolean; to?: string }) {
  const label = to ? t.composer.placeholderTo(to) : t.composer.placeholder
  const selected = useStore($selected)
  const autoMode = useStore($autoMode)
  const restored = useStore($restored)
  const terminal = useStore($terminalAvailable)
  const owner = selected ?? DRAFT
  // What was not sent, or what a link put in a new conversation's box (`writePlugin`).
  const waiting = restored[owner]
  const restore = useCallback(() => (waiting ? takeRestored(owner) : ''), [owner, waiting])
  const commands = useStore($atlasCommands)[owner]

  // Read as the conversation comes on screen, and after each turn, which may have made a skill.
  useEffect(() => {
    if (connected && !running) {
      readAtlasCommands(selected)
    }
  }, [connected, running, selected])

  // `/new` runs where the server offered it: not in a bot's conversation.
  const offersNew = commands?.some(row => row.group === 'command' && row.name === 'new') ?? !to

  // And as `/` is typed, so a skill written meanwhile is there.
  const reread = useCallback(() => {
    readAtlasCommands(selected)
  }, [selected])

  const slash = (name: string) => {
    if (name === 'new' && offersNew) {
      newConversation()

      return true
    }

    if (name === 'stop') {
      if (running) {
        void interrupt()
      }

      return true
    }

    return false
  }

  return (
    <MessageBox
      actions={
        <>
          <DictateButton />
          <BoxActions />
        </>
      }
      auto={{
        label: t.composer.autoModeLabel,
        on: autoMode[owner] ?? owner === DRAFT,
        set: on => void setAutoMode(on),
        title: t.composer.autoMode
      }}
      commands={commands}
      connected={connected}
      draftKey={owner}
      label={label}
      // Off only once the server said it opens no terminal: asked as the connection opens, it may not
      // have answered yet when the first `!` is sent (found by the e2e, 19/10/2026: the command went
      // to the model as a message).
      onCommand={terminal === false ? undefined : command => void runUserCommand(command)}
      onSend={(text, attachments) =>
        send(text, attachments).then(
          () => true,
          () => false
        )
      }
      onSlash={slash}
      onSlashOpen={reread}
      onStop={running ? () => void interrupt() : undefined}
      owner={owner}
      placeholder={running ? t.composer.placeholderRunning : label}
      restore={restore}
      toolbar={
        <>
          <ModelPicker connected={connected} />
          <AgentsButton />
          <TerminalButton />
        </>
      }
    />
  )
}
