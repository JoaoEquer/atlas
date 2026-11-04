import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

import { t } from '@/i18n'
import type {
  CheckpointRow,
  DiffFileRow,
  DiffLineRow,
  WorkspaceDiff,
  WorkspaceRow
} from '@/protocol/protocol.generated'
import { $workspacesSeen } from '@/store/app'
import {
  $reviewComments,
  addComment,
  commentsOf,
  goBack,
  readCheckpoints,
  readDiff,
  removeComment,
  type ReviewComment,
  sendReview
} from '@/store/workspaces'

import { ChevronDown, ChevronRight, Close, Refresh } from './ui/icons'
import { IconButton } from './ui/tip'
import { when } from './when'

/**
 * What a workspace changed, in the side panel's Changes tab (step 21, A2, "the heart"): each file
 * its agent touched since the workspace left its branch, committed or not, line by line with the
 * numbers the file has. A click on a line opens a comment on it; the comments gather below, kept
 * while the user reads on, and go to the session as one message, each with its file, its line and
 * the line itself, so the agent finds the place and the user never has to describe it.
 */
export function ChangesView({ workspace }: { workspace: WorkspaceRow }) {
  const seen = useStore($workspacesSeen)
  const comments = commentsOf(useStore($reviewComments), workspace.id)
  const [diff, setDiff] = useState<null | WorkspaceDiff>(null)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)

  const read = useCallback(() => {
    readDiff(workspace.id).then(
      found => {
        setDiff(found)
        setError('')
      },
      (failed: unknown) => {
        setError(failed instanceof Error ? failed.message : String(failed))
      }
    )
  }, [workspace.id])

  useEffect(read, [read, seen])

  return (
    <div className="flex h-full flex-col text-xs" data-testid="changes">
      <div className="flex items-center gap-2 border-b border-border px-2 py-1.5">
        <span className="min-w-0 flex-1 truncate text-muted-foreground" title={workspace.folder}>
          {diff ? t.changes.summary(diff.files.length, workspace.base) : t.changes.reading}
        </span>
        <IconButton icon={Refresh} label={t.changes.readAgain} onClick={read} testId="changes-refresh" />
      </div>
      {error && (
        <p className="px-2 py-1 text-failed-text" role="alert">
          {error}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {diff?.files.length === 0 && <p className="p-2 text-muted-foreground">{t.changes.none}</p>}
        {diff?.files.map(file => (
          <FileChanges file={file} key={file.path} workspaceId={workspace.id} />
        ))}
        {diff?.more && <p className="p-2 text-muted-foreground">{t.changes.more}</p>}
        <Checkpoints seen={seen} workspace={workspace} />
      </div>
      {comments.length > 0 && (
        <div className="space-y-1.5 border-t border-border p-2" data-testid="review-comments">
          <ul className="max-h-40 space-y-1 overflow-y-auto">
            {comments.map((comment, index) => (
              <li
                className="flex items-start gap-1"
                data-testid="review-comment"
                key={`${comment.path}:${String(index)}`}
              >
                <span className="min-w-0 flex-1">
                  <span className="font-mono text-muted-foreground">
                    {comment.path}:{comment.line}
                  </span>{' '}
                  {comment.text}
                </span>
                <IconButton
                  icon={Close}
                  label={t.changes.removeComment}
                  onClick={() => {
                    removeComment(workspace.id, index)
                  }}
                  testId="review-comment-remove"
                />
              </li>
            ))}
          </ul>
          <button
            className="w-full rounded bg-primary px-2 py-1 font-medium text-primary-foreground disabled:opacity-60"
            data-testid="review-send"
            disabled={sending}
            onClick={() => {
              setSending(true)
              void sendReview(workspace.id).finally(() => {
                setSending(false)
              })
            }}
            type="button"
          >
            {t.changes.send(comments.length)}
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * The workspace's checkpoints (step 21, A3), the newest first: its files as they were before each
 * prompt. Going back to one asks first, keeps what is there now as a checkpoint of its own (so it
 * can be undone), and the session goes on in a copy, its original left with what it said since.
 */
function Checkpoints({ seen, workspace }: { seen: number; workspace: WorkspaceRow }) {
  const [rows, setRows] = useState<CheckpointRow[]>([])
  const [open, setOpen] = useState(false)
  const [asking, setAsking] = useState('')
  const [back, setBack] = useState<CheckpointRow | null>(null)
  const [error, setError] = useState('')
  const Chevron = open ? ChevronDown : ChevronRight

  useEffect(() => {
    let live = true
    readCheckpoints(workspace.id).then(
      found => {
        if (live) {
          setRows(found)
        }
      },
      () => undefined
    )

    return () => {
      live = false
    }
  }, [seen, workspace.id])

  if (rows.length === 0) {
    return null
  }

  return (
    <section className="p-2" data-testid="checkpoints">
      <button
        aria-expanded={open}
        className="flex items-center gap-1 font-semibold tracking-wide text-muted-foreground uppercase hover:text-foreground"
        data-testid="checkpoints-toggle"
        onClick={() => {
          setOpen(!open)
        }}
        type="button"
      >
        <Chevron aria-hidden size={12} stroke={2} />
        {t.changes.checkpoints(rows.length)}
      </button>
      {back && (
        <p className="pt-1 text-soft" data-testid="checkpoint-back" role="status">
          {t.changes.wentBack(back.label)}
        </p>
      )}
      {error && (
        <p className="pt-1 text-failed-text" role="alert">
          {error}
        </p>
      )}
      {open && (
        <ul className="space-y-1 pt-1">
          {rows.map(row => (
            <li className="group" data-testid="checkpoint" key={row.commit}>
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate" title={row.label}>
                  {row.label}
                </span>
                <span className="shrink-0 text-muted-foreground">{when(row.created_at)}</span>
                <button
                  className="link shrink-0"
                  data-testid="checkpoint-go-back"
                  onClick={() => {
                    setAsking(asking === row.commit ? '' : row.commit)
                  }}
                  type="button"
                >
                  {t.changes.goBack}
                </button>
              </div>
              {asking === row.commit && (
                <div className="space-y-1 py-1" data-testid="checkpoint-confirm">
                  <p className="text-soft">{t.changes.goBackNote}</p>
                  <div className="flex gap-2">
                    <button
                      className="rounded bg-primary px-2 py-0.5 font-medium text-primary-foreground"
                      data-testid="checkpoint-go-back-yes"
                      onClick={() => {
                        setError('')
                        goBack(workspace.id, row.commit).then(
                          () => {
                            setAsking('')
                            setBack(row)
                          },
                          (failed: unknown) => {
                            setError(failed instanceof Error ? failed.message : String(failed))
                          }
                        )
                      }}
                      type="button"
                    >
                      {t.changes.goBackYes}
                    </button>
                    <button
                      className="rounded px-2 py-0.5 text-muted-foreground"
                      onClick={() => {
                        setAsking('')
                      }}
                      type="button"
                    >
                      {t.workspaces.cancel}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function FileChanges({ file, workspaceId }: { file: DiffFileRow; workspaceId: string }) {
  const [open, setOpen] = useState(true)
  const [commenting, setCommenting] = useState<null | number>(null)
  const Chevron = open ? ChevronDown : ChevronRight

  return (
    <section className="border-b border-border" data-path={file.path} data-testid="changed-file">
      <button
        aria-expanded={open}
        className="sticky top-0 flex w-full items-center gap-1.5 bg-subtle px-2 py-1 text-left hover:bg-muted"
        onClick={() => {
          setOpen(!open)
        }}
        type="button"
      >
        <Chevron aria-hidden className="shrink-0 text-muted-foreground" size={12} stroke={2} />
        <span className="min-w-0 flex-1 truncate font-mono" title={file.path}>
          {file.path}
        </span>
        {file.status !== 'modified' && (
          <span className="shrink-0 text-muted-foreground">{t.changes.statuses[file.status] ?? file.status}</span>
        )}
        <span className="shrink-0 text-added">+{file.added}</span>
        <span className="shrink-0 text-removed">−{file.removed}</span>
      </button>
      {open && file.binary && <p className="px-2 py-1 text-muted-foreground">{t.changes.binary}</p>}
      {open && !file.binary && (
        <div className="overflow-x-auto font-mono leading-5">
          {file.lines.map((line, index) => (
            <div key={index}>
              <Line
                line={line}
                onComment={() => {
                  setCommenting(commenting === index ? null : index)
                }}
              />
              {commenting === index && (
                <CommentBox
                  line={line}
                  onDone={() => {
                    setCommenting(null)
                  }}
                  path={file.path}
                  workspaceId={workspaceId}
                />
              )}
            </div>
          ))}
          {file.truncated && <p className="px-2 py-1 font-sans text-muted-foreground">{t.changes.truncated}</p>}
        </div>
      )}
    </section>
  )
}

const KINDS: Record<string, string> = {
  minus: 'bg-removed/10',
  plus: 'bg-added/10',
  same: ''
}

function Line({ line, onComment }: { line: DiffLineRow; onComment: () => void }) {
  if (line.kind === 'gap') {
    return <div className="px-2 text-center text-muted-foreground select-none">⋯</div>
  }

  const number = line.kind === 'minus' ? line.old : line.new

  return (
    <button
      className={`group flex w-full min-w-max text-left hover:brightness-95 ${KINDS[line.kind] ?? ''}`}
      data-kind={line.kind}
      data-line={number}
      data-testid="diff-line"
      onClick={onComment}
      title={t.changes.comment}
      type="button"
    >
      <span className="w-10 shrink-0 pr-2 text-right text-muted-foreground select-none">{number || ''}</span>
      <span className="w-3 shrink-0 select-none">{line.kind === 'plus' ? '+' : line.kind === 'minus' ? '−' : ' '}</span>
      <span className="pr-2 whitespace-pre">{line.text}</span>
    </button>
  )
}

function CommentBox({
  line,
  onDone,
  path,
  workspaceId
}: {
  line: DiffLineRow
  onDone: () => void
  path: string
  workspaceId: string
}) {
  const [text, setText] = useState('')
  const add = () => {
    if (!text.trim()) {
      return
    }

    const removed = line.kind === 'minus'
    const comment: ReviewComment = {
      line: removed ? line.old : line.new,
      path,
      quoted: line.text,
      removed,
      text: text.trim()
    }
    addComment(workspaceId, comment)
    onDone()
  }

  return (
    <div className="space-y-1 border-y border-border bg-background p-2 font-sans" data-testid="comment-box">
      <textarea
        aria-label={t.changes.comment}
        autoFocus
        className="w-full rounded border border-border-strong bg-raised px-2 py-1"
        data-testid="comment-text"
        onChange={event => {
          setText(event.target.value)
        }}
        onKeyDown={event => {
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault()
            add()
          } else if (event.key === 'Escape') {
            onDone()
          }
        }}
        placeholder={t.changes.commentPlaceholder}
        rows={2}
        value={text}
      />
      <div className="flex gap-2">
        <button
          className="rounded bg-primary px-2 py-0.5 font-medium text-primary-foreground"
          data-testid="comment-add"
          onClick={add}
          type="button"
        >
          {t.changes.add}
        </button>
        <button className="rounded px-2 py-0.5 text-muted-foreground" onClick={onDone} type="button">
          {t.workspaces.cancel}
        </button>
      </div>
    </div>
  )
}
