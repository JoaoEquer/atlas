/**
 * The Google Workspace plugin's screen (step 26): its part of the first run's Connections, where
 * the core had it until then. It says whether `gws` is logged in and as whom; setting `gws` up,
 * with its Google Cloud project, is led by Atlas in a conversation, which "Set up" starts, the
 * first run waiting a band away (`place.away`) and reading the status again when it shows.
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
      title: "Gmail e Agenda",
      intro:
        "O Atlas configura o gws com você numa conversa: o projeto no Google, o login e a conferência.",
      setUp: "Configurar com o Atlas",
      connected: (account) =>
        account ? `Conectado como ${account}` : "Conectado",
      prompt:
        "Conecte o meu Gmail e o meu Google Agenda ao Atlas pelo gws. Comece pelo `gws auth status`, instale o gws se faltar, e me guie na configuração um passo de cada vez.",
    }
  : {
      title: "Gmail and Calendar",
      intro:
        "Atlas sets up gws with you in a conversation: the Google project, its sign-in, and a check.",
      setUp: "Set up with Atlas",
      connected: (account) =>
        account ? `Connected as ${account}` : "Connected",
      prompt:
        "Connect my Gmail and Google Calendar to Atlas with gws. Start with `gws auth status`, install gws if it is missing, and walk me through the setup one step at a time.",
    };

const PRIMARY =
  "rounded bg-primary px-2.5 py-1 text-sm font-medium text-primary-foreground disabled:opacity-50";

function Connection({ ctx, place }) {
  const { state } = useValue(host.state.connection);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    if (state === "open") {
      ctx
        .call("status", {})
        .then(setStatus)
        .catch(() => setStatus(null));
    }
  }, [state]);

  return h(
    "section",
    {
      className: "space-y-3 rounded-lg border border-border p-4",
      "data-testid": "first-run-google",
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
              "data-testid": "first-run-google-connected",
            },
            `✓ ${WORDS.connected(status.account)}`,
          )
        : h(
            "button",
            {
              className: PRIMARY,
              "data-testid": "first-run-google-set-up",
              onClick: () => {
                // The first run waits on its step, a band away, while Atlas leads the setup.
                place?.away?.();
                host.send(WORDS.prompt).catch(() => undefined);
              },
              type: "button",
            },
            WORDS.setUp,
          ),
    ),
  );
}

export default {
  id: "google",
  register(ctx) {
    ctx.register({
      area: "connection",
      id: "google",
      order: 20,
      title: WORDS.title,
      render: (place) => h(Connection, { ctx, place }),
    });
  },
};
