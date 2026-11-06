import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { SpeechState } from '@/protocol/protocol.generated'
import { $speech, declineSpeech, downloadSpeech } from '@/store/app'

/**
 * Writing down recordings (step 26, docs/ARCHITECTURE.md, Speech): who does it is chosen in
 * Settings, Voice, and the model this computer uses is downloaded once. The band above the
 * conversation asks once, the first time a recording waited for the model, with its size said;
 * a no is kept for the machine, and Settings still offers it.
 */

/** Megabytes, as the question and the progress say them. */
function megabytes(bytes: number): string {
  return String(Math.round(bytes / 1_000_000))
}

/** Whether the band asks: a recording waited for the model, which is not here nor on its way,
 * and the user never said no. */
export function speechAsks(speech: null | SpeechState): boolean {
  return (
    speech !== null &&
    speech.transcription === 'local' &&
    speech.wanted &&
    !speech.present &&
    !speech.declined &&
    speech.downloading === null
  )
}

/** The band above the conversation: the question, then the download's progress. */
export function SpeechBand() {
  const speech = useStore($speech)
  const [failed, setFailed] = useState<null | string>(null)
  const act = (action: () => Promise<void>) => {
    setFailed(null)
    action().catch((failure: unknown) => {
      setFailed(failure instanceof Error ? failure.message : String(failure))
    })
  }

  if (speech?.downloading != null) {
    return (
      <div
        className="flex items-center gap-3 border-b border-border bg-subtle px-4 py-2 text-sm"
        data-testid="speech-band"
        role="status"
      >
        <span>{t.speech.downloading(megabytes(speech.downloading), megabytes(speech.size))}</span>
      </div>
    )
  }

  if (!speechAsks(speech) || speech === null) {
    return null
  }

  return (
    <div
      className="flex flex-wrap items-center gap-3 border-b border-border bg-subtle px-4 py-2 text-sm"
      data-testid="speech-band"
      role="status"
    >
      <span>{t.speech.ask(megabytes(speech.size))}</span>
      <button
        className="font-medium underline"
        data-testid="speech-band-download"
        onClick={() => {
          act(downloadSpeech)
        }}
        type="button"
      >
        {t.speech.download}
      </button>
      <button
        className="text-muted-foreground underline"
        data-testid="speech-band-decline"
        onClick={() => {
          act(declineSpeech)
        }}
        type="button"
      >
        {t.speech.notNow}
      </button>
      {failed && (
        <span className="text-failed-text" role="alert">
          {failed}
        </span>
      )}
    </div>
  )
}

/** The model on this computer, in Settings, Voice: there, on its way, or to download. */
export function SpeechModel({ speech }: { speech: SpeechState }) {
  const [failed, setFailed] = useState<null | string>(null)
  const error = failed ?? (speech.error || null)

  return (
    <section className="space-y-2 rounded-lg border border-border p-3" data-testid="speech-model">
      <p className="text-sm font-medium">{t.speech.model}</p>
      <p className="text-sm text-muted-foreground">{t.speech.modelHint(speech.credit)}</p>
      {speech.present ? (
        <p className="text-sm" data-testid="speech-model-present">
          {t.speech.present}
        </p>
      ) : speech.downloading !== null ? (
        <p className="text-sm" data-testid="speech-model-downloading" role="status">
          {t.speech.downloading(megabytes(speech.downloading), megabytes(speech.size))}
        </p>
      ) : (
        <button
          className="rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-selected"
          data-testid="speech-model-download"
          onClick={() => {
            setFailed(null)
            downloadSpeech().catch((failure: unknown) => {
              setFailed(failure instanceof Error ? failure.message : String(failure))
            })
          }}
          type="button"
        >
          {t.speech.downloadSize(megabytes(speech.size))}
        </button>
      )}
      {error && (
        <p className="text-xs text-failed-text" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
