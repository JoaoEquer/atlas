/**
 * The GitHub plugin's screen (step 26): its part of the first run's Connections, where the core
 * had it until then. It says whether `gh` is on the computer and who it is logged in as, and
 * starts `gh`'s own login in the browser, showing the one-time code to type there; while that
 * login waits, it reads the status again every few seconds.
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
  },
};
