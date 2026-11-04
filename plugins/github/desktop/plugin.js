/**
 * The GitHub plugin's screen (step 26): its part of the first run's Connections, where the core
 * had it until then. It says whether `gh` is on the computer and who it is logged in as, and
 * starts `gh`'s own login in the browser, showing the one-time code to type there; while that
 * login waits, it reads the status again every few seconds.
 *
 * Since step 21 (screen version 6), beside a workspace's changes, the pull request of its branch
 * and how its checks stand, or one opened at the user's click once Atlas sent the branch; and,
 * under a project among the workspaces, its open issues, each starting a workspace whose session
 * begins with the issue.
 *
 * Plain ESM, loaded by the app as it is: only `@atlas/plugin-sdk` and `react` are imported.
 */
import { useValue, host } from "@atlas/plugin-sdk";
import { createElement as h, useEffect, useState } from "react";

const PORTUGUESE = (globalThis.navigator?.language ?? "")
  .toLowerCase()
  .startsWith("pt");

const WORDS = PORTUGUESE
  ? {
      title: "GitHub",
      intro:
        "Issues, pull requests e repositórios, pelo gh, com o login da sua conta.",
      connect: "Conectar",
      connected: (account) => `Conectado como ${account}`,
      missing:
        "O programa do GitHub, gh, não está neste computador: instale-o em cli.github.com e volte aqui.",
      code: "Digite este código na página que abriu no navegador:",
      page: "Abrir a página de novo",
      openPr: "Abrir um pull request",
      prTitle: "Título do pull request",
      prNote: (branch, base) =>
        `Envia ${branch} ao origin e abre um pull request para ${base}.`,
      draft: "Em rascunho",
      open: "Abrir",
      cancel: "Cancelar",
      checks: (count) => (count === 1 ? "1 check" : `${count} checks`),
      issues: "Issues no GitHub",
      noIssues: "Nenhuma issue aberta.",
      start: "Começar",
      startHint: "Uma workspace nova, com a issue como primeira mensagem",
      issuePrompt: (issue) =>
        `Resolva a issue #${issue.number}: ${issue.title}\n\n${issue.body}\n\n${issue.url}`.trim(),
    }
  : {
      title: "GitHub",
      intro:
        "Issues, pull requests and repositories, through gh, logged in with your account.",
      connect: "Connect",
      connected: (account) => `Connected as ${account}`,
      missing:
        "GitHub's program, gh, is not on this computer: install it from cli.github.com and come back.",
      code: "Type this code on the page that opened in your browser:",
      page: "Open the page again",
      openPr: "Open a pull request",
      prTitle: "Title of the pull request",
      prNote: (branch, base) =>
        `Sends ${branch} to origin and opens a pull request into ${base}.`,
      draft: "As a draft",
      open: "Open",
      cancel: "Cancel",
      checks: (count) => (count === 1 ? "1 check" : `${count} checks`),
      issues: "Issues on GitHub",
      noIssues: "No open issues.",
      start: "Start",
      startHint: "A new workspace, the issue as its first message",
      issuePrompt: (issue) =>
        `Resolve issue #${issue.number}: ${issue.title}\n\n${issue.body}\n\n${issue.url}`.trim(),
    };

const PRIMARY =
  "rounded bg-primary px-2.5 py-1 text-sm font-medium text-primary-foreground disabled:opacity-50";

/** What a call's error says, without the name of what raised it. */
export function said(error) {
  const text = error instanceof Error ? error.message : String(error);
  return text.replace(/^[A-Za-z]+Error: /, "");
}

function Connection({ ctx }) {
  const { state } = useValue(host.state.connection);
  const [status, setStatus] = useState(null);
  const [code, setCode] = useState(null);
  const [error, setError] = useState("");
  const read = () =>
    ctx
      .call("status", {})
      .then(setStatus)
      .catch((failed) => setError(said(failed)));

  useEffect(() => {
    if (state === "open") {
      read();
    }
  }, [state]);
  // While a login waits for the browser, its end shows by itself.
  useEffect(() => {
    if (!code || status?.logged_in) {
      return undefined;
    }
    const timer = setInterval(read, 3000);
    return () => clearInterval(timer);
  }, [code, status?.logged_in]);

  const connect = () => {
    setError("");
    ctx
      .call("log_in", {})
      .then((started) => {
        setCode(started);
        window.open(started.url);
      })
      .catch((failed) => setError(said(failed)));
  };

  return h(
    "section",
    {
      className: "space-y-3 rounded-lg border border-border p-4",
      "data-testid": "first-run-github",
    },
    h(
      "div",
      { className: "flex items-center gap-3" },
      h(
        "div",
        { className: "min-w-0 flex-1" },
        h("p", { className: "text-sm font-medium" }, WORDS.title),
        h("p", { className: "text-xs text-muted-foreground" }, WORDS.intro),
      ),
      status?.logged_in
        ? h(
            "span",
            {
              className: "flex shrink-0 items-center gap-1 text-sm",
              "data-testid": "first-run-github-connected",
            },
            `✓ ${WORDS.connected(status.account)}`,
          )
        : status?.installed &&
            h(
              "button",
              {
                className: PRIMARY,
                "data-testid": "first-run-github-connect",
                onClick: connect,
                type: "button",
              },
              WORDS.connect,
            ),
    ),
    status &&
      !status.installed &&
      h(
        "p",
        {
          className: "text-sm text-muted-foreground",
          "data-testid": "first-run-github-missing",
        },
        WORDS.missing,
      ),
    code &&
      !status?.logged_in &&
      h(
        "div",
        { className: "space-y-1 rounded-md bg-subtle p-3 text-sm" },
        h("p", null, WORDS.code),
        h(
          "p",
          {
            className: "font-mono text-lg tracking-widest select-all",
            "data-testid": "first-run-github-code",
          },
          code.code,
        ),
        h(
          "button",
          {
            className: "text-xs underline underline-offset-4",
            onClick: () => window.open(code.url),
            type: "button",
          },
          WORDS.page,
        ),
      ),
    error &&
      h("p", { className: "text-sm text-failed-text", role: "alert" }, error),
  );
}

/** How a check stands, as a mark. */
function mark(state) {
  if (["success", "skipped", "neutral"].includes(state)) {
    return "✓";
  }
  if (
    ["failure", "error", "cancelled", "timed_out", "action_required"].includes(
      state,
    )
  ) {
    return "✗";
  }
  return "…";
}

/** The pull request of a workspace's branch, beside its changes (step 21, A4). */
function PullRequest({ ctx, place }) {
  const [pr, setPr] = useState(null);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);
  const [title, setTitle] = useState("");
  const [draft, setDraft] = useState(false);
  const [busy, setBusy] = useState(false);
  const where = { folder: place.folder, branch: place.branch };
  const read = () =>
    ctx
      .call("pull_request", where)
      .then(setPr)
      .catch((failed) => setError(said(failed)));

  useEffect(() => {
    void read();
  }, [place.branch]);

  const open = () => {
    setBusy(true);
    setError("");
    host
      .request("workspace.push", { id: place.id })
      .then(() =>
        ctx.call("open_pull_request", {
          ...where,
          base: place.base,
          title,
          draft,
        }),
      )
      .then((made) => {
        setPr(made);
        setOpening(false);
      })
      .catch((failed) => setError(said(failed)))
      .finally(() => setBusy(false));
  };

  return h(
    "div",
    {
      className: "space-y-1 border-b border-border p-2 text-xs",
      "data-testid": "github-pull-request",
    },
    pr?.exists &&
      h(
        "div",
        { className: "space-y-1" },
        h(
          "button",
          {
            className: "link block truncate text-left",
            "data-testid": "github-pr-link",
            onClick: () => window.open(pr.url),
            title: pr.url,
            type: "button",
          },
          `#${pr.number} ${pr.title} · ${pr.draft ? "draft" : pr.state}`,
        ),
        pr.checks.length > 0 &&
          h(
            "ul",
            { "aria-label": WORDS.checks(pr.checks.length) },
            ...pr.checks.map((check) =>
              h(
                "li",
                {
                  className: "flex gap-1.5",
                  "data-state": check.state,
                  "data-testid": "github-check",
                  key: check.name,
                },
                h("span", { className: "w-3 shrink-0" }, mark(check.state)),
                h("span", { className: "truncate" }, check.name),
              ),
            ),
          ),
      ),
    pr &&
      !pr.exists &&
      !opening &&
      h(
        "button",
        {
          className: "link",
          "data-testid": "github-open-pr",
          onClick: () => setOpening(true),
          type: "button",
        },
        WORDS.openPr,
      ),
    opening &&
      h(
        "div",
        { className: "space-y-1", "data-testid": "github-open-pr-form" },
        h(
          "p",
          { className: "text-soft" },
          WORDS.prNote(place.branch, place.base),
        ),
        h("input", {
          "aria-label": WORDS.prTitle,
          className:
            "w-full rounded border border-border-strong bg-raised px-2 py-1",
          "data-testid": "github-pr-title",
          onChange: (event) => setTitle(event.target.value),
          placeholder: WORDS.prTitle,
          value: title,
        }),
        h(
          "label",
          { className: "flex items-center gap-1.5" },
          h("input", {
            checked: draft,
            onChange: (event) => setDraft(event.target.checked),
            type: "checkbox",
          }),
          WORDS.draft,
        ),
        h(
          "div",
          { className: "flex gap-2" },
          h(
            "button",
            {
              className: PRIMARY,
              "data-testid": "github-pr-open",
              disabled: busy || !title.trim(),
              onClick: open,
              type: "button",
            },
            WORDS.open,
          ),
          h(
            "button",
            {
              className: "rounded px-2 py-1 text-muted-foreground",
              onClick: () => setOpening(false),
              type: "button",
            },
            WORDS.cancel,
          ),
        ),
      ),
    error && h("p", { className: "text-failed-text", role: "alert" }, error),
  );
}

/** A project's open issues, under it among the workspaces, each starting one (step 21, A5). */
function Issues({ ctx, place }) {
  const [open, setOpen] = useState(false);
  const [issues, setIssues] = useState(null);
  const [error, setError] = useState("");

  const toggle = () => {
    setOpen(!open);
    if (!open && issues === null) {
      ctx
        .call("issues", { folder: place.root })
        .then(setIssues)
        .catch((failed) => setError(said(failed)));
    }
  };

  return h(
    "div",
    { className: "px-2 py-0.5 text-xs", "data-testid": "github-issues" },
    h(
      "button",
      {
        "aria-expanded": open,
        className: "text-muted-foreground hover:text-foreground",
        "data-testid": "github-issues-toggle",
        onClick: toggle,
        type: "button",
      },
      `${open ? "▾" : "▸"} ${WORDS.issues}`,
    ),
    open &&
      issues?.length === 0 &&
      h("p", { className: "py-1 text-muted-foreground" }, WORDS.noIssues),
    open &&
      issues &&
      h(
        "ul",
        { className: "space-y-0.5 py-1" },
        ...issues.map((issue) =>
          h(
            "li",
            {
              className: "flex items-center gap-2",
              "data-testid": "github-issue",
              key: issue.number,
            },
            h(
              "span",
              { className: "min-w-0 flex-1 truncate", title: issue.title },
              `#${issue.number} ${issue.title}`,
            ),
            h(
              "button",
              {
                className: "link shrink-0",
                "data-testid": "github-issue-start",
                onClick: () =>
                  place
                    .start(`issue-${issue.number}`, WORDS.issuePrompt(issue))
                    .catch((failed) => setError(said(failed))),
                title: WORDS.startHint,
                type: "button",
              },
              WORDS.start,
            ),
          ),
        ),
      ),
    error && h("p", { className: "text-failed-text", role: "alert" }, error),
  );
}

export default {
  id: "github",
  register(ctx) {
    ctx.register({
      area: "connection",
      id: "github",
      order: 10,
      title: WORDS.title,
      render: () => h(Connection, { ctx }),
    });
    ctx.register({
      area: "workspace",
      id: "pull-request",
      title: WORDS.title,
      render: (place) => h(PullRequest, { ctx, place }),
    });
    ctx.register({
      area: "project",
      id: "issues",
      title: WORDS.issues,
      render: (place) => h(Issues, { ctx, place }),
    });
  },
};
