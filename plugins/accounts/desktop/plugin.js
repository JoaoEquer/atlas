/**
 * The accounts plugin's screen (step 22): the harnesses' other accounts, where the user works with
 * them, as the app showed them before they left its core (asked for by the user on 03/11/2026).
 *
 * - **By a session's box** (`session`): one pill, the account the session runs on and how many
 *   stops its route has. Pressed, a panel above it chooses the account (with a session running,
 *   after reading what changes: it goes on in a copy there) and draws the route as a path: each
 *   stop in order, whether it can take over, how much of its plan is used, and the model and the
 *   effort the session goes on with there.
 * - **Atop an executor's tree** (`executor`): on the harness's own login, "Add an account" by a
 *   nickname, the harness's own login then opening from the account's row; on an account, the
 *   folders whose new sessions start on it, and its removal, which leaves its folder as it is.
 *
 * The accounts, their order and the routes are the plugin's Python's (`ctx.call`); going on in a
 * copy on another account is Atlas's (`host.sessionAccount`).
 *
 * Plain ESM, loaded by the app as it is: only `@atlas/plugin-sdk` and `react` are imported.
 */
import { host, useValue } from "@atlas/plugin-sdk";
import { createElement as h, useEffect, useRef, useState } from "react";

const PORTUGUESE = (globalThis.navigator?.language ?? "")
  .toLowerCase()
  .startsWith("pt");

const WORDS = PORTUGUESE
  ? {
      add: "Adicionar conta",
      adding: "Adicionar",
      cancel: "Cancelar",
      nickname: "O apelido dela",
      nicknameNote:
        "Ela ganha uma pasta própria ao lado da sua, com login, configurações e conversas próprios: letras, números, - ou _.",
      nicknamePlaceholder: "empresa-b, pessoal…",
      remove: (name) => `Remover ${name}`,
      removeNote: (name, folder) =>
        `Remover ${name}? A pasta dela (${folder}) fica como está, com o login e as conversas; os projetos dela voltam para a conta principal.`,
      folders: "Projetos que começam nesta conta",
      noFolders: "Nenhum: os projetos começam na conta principal.",
      addFolder: "Começar um projeto nesta conta…",
      takeFolder: (folder) => `Voltar ${folder} para a conta principal`,
      main: "principal",
      mainAccount: "Conta principal",
      sessionTitle: "Conta da sessão",
      session: "Conta: trocar continua uma cópia desta conversa na outra conta",
      sessionBusy:
        "A conta pode ser trocada quando a sessão parar de trabalhar.",
      switchChecking: "Olhando a outra conta…",
      switched: (name, tokens) =>
        tokens === null
          ? `Agora em ${name}. A primeira mensagem nesta conta carrega o histórico inteiro; compactar antes (/compact) carrega menos.`
          : `Agora em ${name}. A primeira mensagem nesta conta carrega o histórico inteiro, cerca de ${tokens.toLocaleString("pt-BR")} tokens de uma vez; compactar antes (/compact) carrega menos.`,
      switchCopy:
        "A conversa continua numa cópia nessa conta; esta conta fica com ela como está.",
      switchCost: (tokens) =>
        tokens === null
          ? "A primeira mensagem relê a conversa inteira, no plano dessa conta."
          : `A primeira mensagem relê a conversa inteira, cerca de ${tokens.toLocaleString("pt-BR")} tokens de uma vez, no plano dessa conta. Compactar antes (/compact) carrega menos.`,
      switchGo: "Trocar",
      switchMissing: (names) => `Essa conta não tem: ${names}.`,
      switchTitle: (name) => `Continuar em ${name}?`,
      switchUnknown: "Não deu para comparar o que essa conta tem.",
      addStop: "Adicionar uma parada…",
      atLimit: "no limite",
      close: "Fechar",
      compact: "Compactar antes de seguir",
      compactHint:
        "Na cópia em outra conta do mesmo harness, onde ele tiver /compact: a cópia lê menos e custa menos.",
      down: (name) => `${name} depois`,
      effort: (name) => `Esforço em ${name}`,
      here: "aqui",
      itsDefault: "o padrão dele",
      model: (name) => `Modelo em ${name}`,
      notLoggedIn: "sem login",
      ready: "pronta",
      same: "como agora",
      used: (percent) => `${percent}% usado`,
      empty:
        "Nenhum destino ainda: no limite do plano, valem as contas do próprio executor.",
      enabled: "No limite do plano, seguir esta rota",
      note: "No limite do plano a sessão segue para o próximo destino com login e fora do próprio limite, nesta ordem. Noutra conta do mesmo harness ela continua numa cópia; noutro harness começa lá uma sessão nova com o que esta fez.",
      notInstalled: "não instalado",
      pill: (count) => (count ? `Rota · ${count}` : "Rota"),
      pillOn: (account, count) =>
        count ? `${account} · rota ${count}` : account,
      removeStop: (name) => `Tirar ${name} da rota`,
      routeTitle: "A rota da sessão: para onde ela segue no limite do plano",
      titleOn: "A conta da sessão, e para onde ela segue no limite do plano",
      up: (name) => `${name} antes`,
      effortLevel: (level) =>
        ({
          low: "baixo",
          medium: "médio",
          high: "alto",
          xhigh: "muito alto",
          max: "máximo",
        })[level] ?? level,
    }
  : {
      add: "Add an account",
      adding: "Add",
      cancel: "Cancel",
      nickname: "Its nickname",
      nicknameNote:
        "It gets a folder of its own beside yours, with its own login, settings and sessions: letters, digits, - or _.",
      nicknamePlaceholder: "company-b, personal…",
      remove: (name) => `Remove ${name}`,
      removeNote: (name, folder) =>
        `Remove ${name}? Its folder (${folder}) stays as it is, with its login and its sessions; its projects start on the main account.`,
      folders: "Projects that start on this account",
      noFolders: "None: projects start on the main account.",
      addFolder: "Start a project on this account…",
      takeFolder: (folder) => `Take ${folder} back to the main account`,
      main: "main",
      mainAccount: "Main account",
      sessionTitle: "The session's account",
      session:
        "Account: switching goes on in a copy of this conversation on the other account",
      sessionBusy:
        "The account can be switched once the session stops working.",
      switchChecking: "Looking at that account…",
      switched: (name, tokens) =>
        tokens === null
          ? `Now on ${name}. The first message on this account loads the whole history; compacting first (/compact) loads less.`
          : `Now on ${name}. The first message on this account loads the whole history, about ${tokens.toLocaleString("en")} tokens at once; compacting first (/compact) loads less.`,
      switchCopy:
        "The conversation goes on in a copy on that account; this account keeps it as it is.",
      switchCost: (tokens) =>
        tokens === null
          ? "The first message reads the whole conversation again, on that account's plan."
          : `The first message reads the whole conversation again, about ${tokens.toLocaleString("en")} tokens at once, on that account's plan. Compacting first (/compact) reads less.`,
      switchGo: "Switch",
      switchMissing: (names) => `That account does not have: ${names}.`,
      switchTitle: (name) => `Go on on ${name}?`,
      switchUnknown: "What that account has could not be compared.",
      addStop: "Add a stop…",
      atLimit: "at its limit",
      close: "Close",
      compact: "Compact before going on",
      compactHint:
        "In the copy on another account of the same harness, where it has /compact: the copy reads less and costs less.",
      down: (name) => `${name} later`,
      effort: (name) => `Effort on ${name}`,
      here: "here",
      itsDefault: "its default",
      model: (name) => `Model on ${name}`,
      notLoggedIn: "not logged in",
      ready: "ready",
      same: "as now",
      used: (percent) => `${percent}% used`,
      empty:
        "No destination yet: at a plan limit, the executor's own accounts take over.",
      enabled: "At a plan limit, follow this route",
      note: "At a plan limit the session goes on to the next destination logged in and not at its own limit, in this order. On another account of the same harness it goes on in a copy; on another harness a new session starts there with what this one did.",
      notInstalled: "not installed",
      pill: (count) => (count ? `Route · ${count}` : "Route"),
      pillOn: (account, count) =>
        count ? `${account} · route ${count}` : account,
      removeStop: (name) => `Take ${name} out of the route`,
      routeTitle: "The session's route: where it goes on at a plan limit",
      titleOn: "The session's account, and where it goes on at a plan limit",
      up: (name) => `${name} sooner`,
      effortLevel: (level) => level,
    };

/** The app's own pill, and its look when pressed. */
const PILL =
  "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border-strong px-2 text-xs text-soft hover:bg-muted disabled:opacity-40";
const PILL_ON = "border-faint bg-selected text-foreground";
const SELECT =
  "w-full rounded-md border border-border-strong bg-raised px-2 py-1 text-xs";
const BUTTON =
  "rounded border border-border-strong px-2 py-0.5 text-xs hover:bg-muted disabled:opacity-50";
const PRIMARY =
  "rounded bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground disabled:opacity-50";
const ICON =
  "inline-flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted disabled:opacity-30";

/** What a call's error says, without the name of what raised it. */
export function said(error) {
  const text = error instanceof Error ? error.message : String(error);
  return text.replace(/^[A-Za-z]+Error: /, "");
}

/** An account as the user knows it: its nickname, or the main one by its login. */
export function named(listed, account) {
  const found = listed.find((row) => row.account_id === account);
  if (found?.account_id) {
    return found.nickname;
  }
  return found?.login || WORDS.mainAccount;
}

/** How much of a plan is used: its most used limit of every model. */
export function usedOf(usage) {
  const windows = (usage?.windows ?? []).filter((window) => !window.model);
  return windows.length
    ? Math.round(Math.max(...windows.map((window) => window.used)))
    : null;
}

/** Each executor on each of its accounts a route may go to: on and installed. */
export function choices(executors) {
  return executors
    .filter((row) => row.installed && row.enabled)
    .map((row) => ({
      key: row.name,
      label: row.base ? row.title : `${row.title} · ${WORDS.mainAccount}`,
      stop: {
        executor: row.base || row.name,
        account: row.account_id,
        model: null,
        effort: null,
      },
    }));
}

// -- by a session's box --------------------------------------------------------------------------

function SessionPart({ ctx, place }) {
  const { executors } = useValue(host.state.workbench);
  const accounts = useValue(host.state.accounts);
  const { state } = useValue(host.state.connection);
  const row = executors.find((found) => found.name === place.executor);
  const base = row?.base || place.executor;
  const listed = accounts[base]?.accounts ?? [];
  const [open, setOpen] = useState(false);
  const [route, setRoute] = useState(null);
  const [picked, setPicked] = useState(null);
  const [switching, setSwitching] = useState(null);
  const conversation = place.conversation;

  useEffect(() => {
    if (state === "open") {
      host.readAccounts(base).catch(() => undefined);
    }
  }, [base, state]);
  useEffect(() => {
    if (state !== "open" || !conversation) {
      setRoute(null);
      return;
    }
    ctx
      .call("route", { conversation })
      .then(setRoute)
      .catch(() => setRoute(null));
  }, [conversation, state]);

  const instance = place.instance;
  const live =
    instance !== null &&
    instance.state !== "ended" &&
    instance.state !== "failed";
  const chosen = live ? instance.account_id : (picked ?? row?.account_id ?? "");
  const several = listed.length >= 2;
  if (!several && !conversation) {
    return null;
  }
  const count = route?.enabled ? route.stops.length : 0;

  const pick = (account) => {
    setOpen(false);
    if (!live) {
      setPicked(account);
      host.sessionAccount.check(account).catch(() => undefined);
      return;
    }
    setSwitching({ account, check: null });
    host.sessionAccount
      .check(account)
      .then((check) => setSwitching(check ? { account, check } : null))
      .catch(() => setSwitching(null));
  };

  return h(
    "span",
    { className: "relative inline-flex" },
    h(
      "button",
      {
        "aria-pressed": open,
        className: `${PILL} ${open ? PILL_ON : ""}`,
        "data-testid": "session-route",
        disabled: state !== "open",
        onClick: () => setOpen(!open),
        title: several ? WORDS.titleOn : WORDS.routeTitle,
        type: "button",
      },
      several ? WORDS.pillOn(named(listed, chosen), count) : WORDS.pill(count),
    ),
    open &&
      h(Panel, {
        chosen,
        conversation,
        ctx,
        instance,
        listed: several ? listed : null,
        locked: state !== "open" || instance?.state === "working",
        onClose: () => setOpen(false),
        onPick: pick,
        route,
        setRoute,
      }),
    switching &&
      h(SwitchNote, {
        name: named(listed, switching.account),
        onCancel: () => setSwitching(null),
        onConfirm: () => {
          const { account, check } = switching;
          setSwitching(null);
          host.sessionAccount
            .switch(
              account,
              WORDS.switched(named(listed, account), check?.tokens ?? null),
            )
            .catch(() => undefined);
        },
        switching,
      }),
  );
}

/** What the pill opens above the box: the session's account, then its route once it runs. It
 * closes as a popover does: a click outside it, Escape, or its own button. */
function Panel({
  chosen,
  conversation,
  ctx,
  instance,
  listed,
  locked,
  onClose,
  onPick,
  route,
  setRoute,
}) {
  const panel = useRef(null);
  useEffect(() => {
    const outside = (event) => {
      const target = event.target;
      if (
        target instanceof Node &&
        !panel.current?.contains(target) &&
        !(
          target instanceof Element &&
          target.closest('[data-testid="session-route"]')
        )
      ) {
        onClose();
      }
    };
    const escape = (event) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      window.removeEventListener("keydown", escape);
    };
  }, [onClose]);

  return h(
    "div",
    {
      className:
        "absolute bottom-full left-0 z-20 mb-2 w-96 max-w-[80vw] space-y-3 rounded-lg border border-border bg-raised p-3 pr-8 shadow-lg",
      "data-testid": "session-route-panel",
      ref: panel,
    },
    h(
      "button",
      {
        "aria-label": WORDS.close,
        className: `${ICON} absolute top-2 right-2`,
        "data-testid": "session-route-close",
        onClick: onClose,
        type: "button",
      },
      "×",
    ),
    listed &&
      h(
        "fieldset",
        { className: "space-y-1 text-xs", "data-testid": "session-account" },
        h("legend", { className: "pb-1 font-medium" }, WORDS.sessionTitle),
        ...listed.map((found) =>
          h(
            "label",
            {
              className: "flex items-center gap-2",
              key: found.account_id,
            },
            h("input", {
              checked: found.account_id === chosen,
              "data-account": found.account_id,
              "data-testid": "session-account-choice",
              disabled: locked,
              name: "session-account",
              onChange: () => onPick(found.account_id),
              type: "radio",
            }),
            found.account_id
              ? found.nickname
              : found.login
                ? `${found.login} · ${WORDS.main}`
                : WORDS.mainAccount,
          ),
        ),
        h(
          "p",
          { className: "text-muted-foreground" },
          locked ? WORDS.sessionBusy : WORDS.session,
        ),
      ),
    conversation &&
      instance &&
      h(RouteEditor, { conversation, ctx, instance, route, setRoute }),
  );
}

/** What a switch of account changes, for the user to read before it happens. */
function SwitchNote({ name, onCancel, onConfirm, switching }) {
  const { check } = switching;
  const who = check?.login ? `${name} (${check.login})` : name;
  return h(
    "div",
    {
      className:
        "absolute bottom-full left-0 z-20 mb-2 w-96 max-w-[80vw] space-y-1 rounded-lg border border-border bg-raised p-3 text-xs shadow-lg",
      "data-testid": "account-switch",
      role: "alertdialog",
    },
    h("p", { className: "font-medium" }, WORDS.switchTitle(who)),
    check === null
      ? h(
          "p",
          { className: "text-muted-foreground", role: "status" },
          WORDS.switchChecking,
        )
      : [
          h("p", { className: "text-soft", key: "copy" }, WORDS.switchCopy),
          check.missing.length > 0 &&
            h(
              "p",
              {
                className: "text-waiting-text",
                "data-testid": "account-switch-missing",
                key: "missing",
              },
              WORDS.switchMissing(check.missing.join(", ")),
            ),
          !check.compared &&
            h(
              "p",
              { className: "text-muted-foreground", key: "unknown" },
              WORDS.switchUnknown,
            ),
          h(
            "p",
            { className: "text-soft", key: "cost" },
            WORDS.switchCost(check.tokens ?? null),
          ),
        ],
    h(
      "div",
      { className: "flex gap-2" },
      h(
        "button",
        {
          className: PRIMARY,
          "data-testid": "account-switch-confirm",
          disabled: check === null,
          onClick: onConfirm,
          type: "button",
        },
        WORDS.switchGo,
      ),
      h(
        "button",
        { className: BUTTON, onClick: onCancel, type: "button" },
        WORDS.cancel,
      ),
    ),
  );
}

/** The session's route as a path: the stops it goes on to at a plan's limit, in order, the one
 * it runs on marked; each change saved at once, for the session's card. */
function RouteEditor({ conversation, ctx, instance, route, setRoute }) {
  const { executors } = useValue(host.state.workbench);
  const [failed, setFailed] = useState("");
  if (!route) {
    return h(
      "p",
      { className: "text-xs text-muted-foreground", role: "status" },
      "…",
    );
  }
  const stops = route.stops;
  const given = stops.map((stop) => ({
    executor: stop.executor,
    account: stop.account,
    model: stop.model ?? null,
    effort: stop.effort ?? null,
  }));
  const save = (changed, switches = {}) => {
    setFailed("");
    ctx
      .call("set_route", {
        conversation,
        stops: changed,
        enabled: switches.enabled ?? route.enabled,
        compact: switches.compact ?? route.compact,
      })
      .then(setRoute)
      .catch((error) => setFailed(said(error)));
  };
  const move = (index, by) => {
    const changed = [...given];
    const [row] = changed.splice(index, 1);
    changed.splice(index + by, 0, row);
    save(changed);
  };
  const others = choices(executors).filter(
    (choice) => !stops.some((stop) => stop.name === choice.key),
  );
  const harness = instance.executor.split("@")[0];

  return h(
    "div",
    { className: "space-y-2 text-xs", "data-testid": "session-route-editor" },
    h("p", { className: "font-medium" }, WORDS.routeTitle),
    h(
      "p",
      { className: "text-muted-foreground" },
      stops.length ? WORDS.note : WORDS.empty,
    ),
    h(
      "ol",
      { className: "space-y-1.5", "data-testid": "session-route-stops" },
      ...stops.map((stop, index) =>
        h(PathStop, {
          here: stop.name === instance.executor,
          index,
          key: stop.name,
          last: index === stops.length - 1,
          onChange: (model, effort) =>
            save(
              given.map((row, at) =>
                at === index ? { ...row, model, effort } : row,
              ),
            ),
          onMove: (by) => move(index, by),
          onRemove: () => save(given.filter((_row, at) => at !== index)),
          row: executors.find((found) => found.name === stop.name),
          sameHarness: stop.executor === harness,
          stop,
        }),
      ),
    ),
    others.length > 0 &&
      h(
        "select",
        {
          "aria-label": WORDS.addStop,
          className: SELECT,
          "data-testid": "session-route-others",
          onChange: (event) => {
            const choice = others.find(
              (found) => found.key === event.target.value,
            );
            if (choice) {
              save([...given, choice.stop]);
            }
          },
          value: "",
        },
        h("option", { value: "" }, WORDS.addStop),
        ...others.map((choice) =>
          h("option", { key: choice.key, value: choice.key }, choice.label),
        ),
      ),
    h(
      "label",
      { className: "flex items-center gap-2" },
      h("input", {
        checked: route.enabled,
        "data-testid": "session-route-enabled",
        onChange: (event) => save(given, { enabled: event.target.checked }),
        type: "checkbox",
      }),
      WORDS.enabled,
    ),
    h(
      "label",
      { className: "flex items-start gap-2" },
      h("input", {
        checked: route.compact,
        className: "mt-0.5",
        "data-testid": "session-route-compact",
        onChange: (event) => save(given, { compact: event.target.checked }),
        type: "checkbox",
      }),
      h(
        "span",
        null,
        WORDS.compact,
        h(
          "span",
          { className: "block text-muted-foreground" },
          WORDS.compactHint,
        ),
      ),
    ),
    failed && h("p", { className: "text-failed-text", role: "alert" }, failed),
  );
}

/** One stop of the path: its place, whether it can take over, how much of its plan is used, and
 * the model and the effort the session goes on with there. */
function PathStop({
  here,
  index,
  last,
  onChange,
  onMove,
  onRemove,
  row,
  sameHarness,
  stop,
}) {
  const [offered, setOffered] = useState([]);
  const [used, setUsed] = useState(null);
  useEffect(() => {
    if (!stop.installed) {
      return;
    }
    host
      .request("executor.models", { executor: stop.name })
      .then((found) => setOffered(found.models))
      .catch(() => setOffered([]));
    host
      .request("executor.usage", { executor: stop.name })
      .then((found) => setUsed(usedOf(found)))
      .catch(() => setUsed(null));
  }, [stop.name, stop.installed]);

  const label = stop.nickname
    ? `${stop.title} · ${stop.nickname}`
    : `${stop.title} · ${WORDS.mainAccount}`;
  const state = here
    ? WORDS.here
    : !stop.installed || !row
      ? WORDS.notInstalled
      : !row.logged_in
        ? WORDS.notLoggedIn
        : stop.limited
          ? WORDS.atLimit
          : WORDS.ready;
  const ready = here || state === WORDS.ready;
  const model = offered.find((found) => found.id === stop.model);
  const efforts = (model ?? offered[0])?.efforts ?? [];
  const dot = here
    ? "border-foreground bg-foreground"
    : ready
      ? "border-foreground bg-background"
      : "border-faint bg-background";

  return h(
    "li",
    {
      className: `relative space-y-1 pl-5 ${ready ? "" : "text-muted-foreground"}`,
      "data-here": here || undefined,
      "data-testid": "session-route-stop",
    },
    !last &&
      h("span", {
        "aria-hidden": true,
        className:
          "absolute top-3 bottom-[-0.5rem] left-[0.3rem] w-px bg-border-strong",
      }),
    h("span", {
      "aria-hidden": true,
      className: `absolute top-1 left-0 h-2.5 w-2.5 rounded-full border-2 ${dot}`,
    }),
    h(
      "div",
      { className: "flex items-center gap-1" },
      h(
        "span",
        {
          className: "min-w-0 flex-1 truncate font-medium",
          "data-testid": "session-route-name",
        },
        `${index + 1}. ${label}`,
      ),
      h(
        "span",
        { className: "shrink-0", "data-testid": "session-route-ready" },
        used === null ? state : `${state} · ${WORDS.used(used)}`,
      ),
      h(
        "button",
        {
          "aria-label": WORDS.up(label),
          className: ICON,
          disabled: index === 0,
          onClick: () => onMove(-1),
          type: "button",
        },
        "↑",
      ),
      h(
        "button",
        {
          "aria-label": WORDS.down(label),
          className: ICON,
          disabled: last,
          onClick: () => onMove(1),
          type: "button",
        },
        "↓",
      ),
      h(
        "button",
        {
          "aria-label": WORDS.removeStop(label),
          className: ICON,
          onClick: onRemove,
          type: "button",
        },
        "×",
      ),
    ),
    offered.length > 0 &&
      h(
        "div",
        { className: "flex flex-wrap gap-1" },
        h(
          "select",
          {
            "aria-label": WORDS.model(label),
            className: `${PILL} max-w-56 bg-transparent`,
            "data-testid": "session-route-model",
            onChange: (event) => {
              const next = offered.find(
                (found) => found.id === event.target.value,
              );
              // An effort the model does not take goes back to "as now".
              const kept =
                stop.effort && next?.efforts.includes(stop.effort)
                  ? stop.effort
                  : null;
              onChange(event.target.value || null, kept);
            },
            value: stop.model ?? "",
          },
          h(
            "option",
            { value: "" },
            sameHarness ? WORDS.same : WORDS.itsDefault,
          ),
          ...offered.map((found) =>
            h(
              "option",
              { key: found.id, title: found.description, value: found.id },
              found.name,
            ),
          ),
        ),
        efforts.length > 0 &&
          h(
            "select",
            {
              "aria-label": WORDS.effort(label),
              className: `${PILL} bg-transparent`,
              "data-testid": "session-route-effort",
              onChange: (event) =>
                onChange(stop.model ?? null, event.target.value || null),
              value: stop.effort ?? "",
            },
            h("option", { value: "" }, WORDS.same),
            ...efforts.map((level) =>
              h(
                "option",
                { key: level, value: level },
                WORDS.effortLevel(level),
              ),
            ),
          ),
      ),
  );
}

// -- atop an executor's tree ---------------------------------------------------------------------

function ExecutorPart({ ctx, place }) {
  const { executors } = useValue(host.state.workbench);
  const row = executors.find((found) => found.name === place.executor);
  const own = executors.find((found) => found.name === place.base);
  if (!row || !own?.account_folders) {
    return null;
  }
  return place.account
    ? h(AccountPart, { ctx, place, row })
    : h(AddAccount, { ctx, place });
}

/** "Add an account", by a nickname alone: its folder is made beside the harness's own, and its
 * row, once listed, logs it in. */
function AddAccount({ ctx, place }) {
  const [open, setOpen] = useState(false);
  const [nickname, setNickname] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");
  if (!open) {
    return h(
      "div",
      { className: "px-2 pb-1 pl-8" },
      h(
        "button",
        {
          className: "link text-xs",
          "data-testid": "add-account",
          onClick: () => setOpen(true),
          type: "button",
        },
        `+ ${WORDS.add}`,
      ),
    );
  }
  return h(
    "form",
    {
      className: "space-y-1.5 px-2 pb-2 pl-8 text-xs",
      "data-testid": "add-account-form",
      onSubmit: (event) => {
        event.preventDefault();
        setBusy(true);
        setFailed("");
        ctx
          .call("add", { executor: place.base, nickname: nickname.trim() })
          .then(() => {
            setOpen(false);
            setNickname("");
          })
          .catch((error) => setFailed(said(error)))
          .finally(() => setBusy(false));
      },
    },
    h(
      "label",
      { className: "block space-y-1" },
      h("span", { className: "font-medium" }, WORDS.nickname),
      h("input", {
        autoFocus: true,
        className: SELECT,
        "data-testid": "account-nickname",
        onChange: (event) => setNickname(event.target.value),
        placeholder: WORDS.nicknamePlaceholder,
        value: nickname,
      }),
    ),
    h("p", { className: "text-muted-foreground" }, WORDS.nicknameNote),
    failed && h("p", { className: "text-failed-text", role: "alert" }, failed),
    h(
      "div",
      { className: "flex gap-2" },
      h(
        "button",
        {
          className: PRIMARY,
          disabled: busy || !nickname.trim(),
          type: "submit",
        },
        WORDS.adding,
      ),
      h(
        "button",
        { className: BUTTON, onClick: () => setOpen(false), type: "button" },
        WORDS.cancel,
      ),
    ),
  );
}

/** An account's own part: the projects whose new sessions start on it, and its removal, asked
 * first, which leaves its folder as it is. */
function AccountPart({ ctx, place, row }) {
  const { state } = useValue(host.state.connection);
  const [listed, setListed] = useState(null);
  const [folders, setFolders] = useState([]);
  const [asking, setAsking] = useState(false);
  const [failed, setFailed] = useState("");
  const read = () => {
    ctx
      .call("list", { executor: place.base })
      .then(setListed)
      .catch((error) => setFailed(said(error)));
    host
      .request("executor.folders", { executor: place.base })
      .then((found) => setFolders(found.folders.map((folder) => folder.folder)))
      .catch(() => setFolders([]));
  };
  useEffect(() => {
    if (state === "open") {
      read();
    }
  }, [place.base, state]);

  const mine = listed?.accounts.find(
    (found) => found.account_id === place.account,
  );
  const starting = mine?.folders ?? [];
  const setFolder = (folder, account) => {
    setFailed("");
    ctx
      .call("set_folder", { executor: place.base, folder, account })
      .then(read)
      .catch((error) => setFailed(said(error)));
  };

  return h(
    "div",
    {
      className: "space-y-1 px-2 pb-2 pl-8 text-xs",
      "data-testid": "account-part",
    },
    h("p", { className: "font-medium" }, WORDS.folders),
    starting.length === 0
      ? h("p", { className: "text-muted-foreground" }, WORDS.noFolders)
      : h(
          "ul",
          { className: "space-y-0.5", "data-testid": "account-folders" },
          ...starting.map((folder) =>
            h(
              "li",
              { className: "flex items-center gap-1", key: folder },
              h(
                "span",
                { className: "min-w-0 flex-1 truncate", title: folder },
                folder,
              ),
              h(
                "button",
                {
                  "aria-label": WORDS.takeFolder(folder),
                  className: ICON,
                  onClick: () => setFolder(folder, ""),
                  type: "button",
                },
                "×",
              ),
            ),
          ),
        ),
    folders.some((folder) => !starting.includes(folder)) &&
      h(
        "select",
        {
          "aria-label": WORDS.addFolder,
          className: SELECT,
          "data-testid": "account-folder-add",
          onChange: (event) => {
            if (event.target.value) {
              setFolder(event.target.value, place.account);
            }
          },
          value: "",
        },
        h("option", { value: "" }, WORDS.addFolder),
        ...folders
          .filter((folder) => !starting.includes(folder))
          .map((folder) => h("option", { key: folder, value: folder }, folder)),
      ),
    asking
      ? h(
          "div",
          {
            className: "space-y-1 rounded border border-border p-2",
            "data-testid": "remove-account-confirm",
            role: "alertdialog",
          },
          h("p", null, WORDS.removeNote(row.nickname, mine?.folder ?? "")),
          h(
            "div",
            { className: "flex gap-2" },
            h(
              "button",
              {
                className: `${BUTTON} text-failed-text`,
                onClick: () => {
                  setAsking(false);
                  ctx
                    .call("remove", {
                      executor: place.base,
                      account: place.account,
                    })
                    .catch((error) => setFailed(said(error)));
                },
                type: "button",
              },
              WORDS.remove(row.nickname),
            ),
            h(
              "button",
              {
                className: BUTTON,
                onClick: () => setAsking(false),
                type: "button",
              },
              WORDS.cancel,
            ),
          ),
        )
      : h(
          "button",
          {
            className: "link text-xs text-failed-text",
            "data-testid": "remove-account",
            onClick: () => setAsking(true),
            type: "button",
          },
          WORDS.remove(row.nickname),
        ),
    failed && h("p", { className: "text-failed-text", role: "alert" }, failed),
  );
}

export default {
  id: "accounts",
  register(ctx) {
    ctx.register({
      area: "session",
      id: "account",
      title: WORDS.sessionTitle,
      render: (place) => h(SessionPart, { ctx, place }),
    });
    ctx.register({
      area: "executor",
      id: "accounts",
      title: WORDS.mainAccount,
      render: (place) => h(ExecutorPart, { ctx, place }),
    });
  },
};
