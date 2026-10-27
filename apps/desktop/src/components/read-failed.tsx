import { t } from '@/i18n'
import { type Readable, readAgain } from '@/store/app'

import { InlineError } from './ui/inline-error'

/** A list or a page whose read failed, in its own place: why, and reading it again; `onAgain`
 * reads a part of it again, such as the list's next page. */
export function ReadFailed({ error, onAgain, what }: { error: string; onAgain?: () => void; what: Readable }) {
  return (
    <InlineError
      action={
        <button
          className="shrink-0 font-medium underline-offset-2 hover:underline"
          data-testid="read-again"
          onClick={() => {
            if (onAgain) {
              onAgain()
            } else {
              readAgain(what)
            }
          }}
          type="button"
        >
          {t.lists.again}
        </button>
      }
      testId="read-failed"
    >
      {t.lists.failed(error)}
    </InlineError>
  )
}
