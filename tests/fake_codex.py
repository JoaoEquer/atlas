"""A stand-in for the `codex` program, speaking its `app-server` JSON-RPC for the tests.

Atlas runs it as it runs Codex (`python tests/fake_codex.py app-server`), so the process, the
pipes and the protocol are real; only the model behind it is a script, a JSON file named by
`FAKE_CODEX_SCRIPT`: a list of steps for every prompt, or `{"turns": [[...], [...]]}`, one list
per prompt in order, the last again for any prompt after, or `{"prompts": {"words": [...]}}`, the
steps of the first entry whose words the prompt holds.

- `{"say": "text"}` and `{"think": "text"}`: an agent message, or a reasoning summary;
- `{"command": "ls"}`: asks to run it (`item/commandExecution/requestApproval`) when the user
  reviews, runs it at once when Codex's own reviewer does (`approvalsReviewer: "auto_review"`);
  "done", exit code 0, when allowed, `declined` when not;
- `{"write": "notes.txt", "content": "..."}`: asks to change a file
  (`item/fileChange/requestApproval`) the same way, and writes it in the thread's folder;
- `{"questions": [...]}`: asks `item/tool/requestUserInput`, and says the answers it got;
- `{"sleep": seconds}`, `{"compact": true}` (a `contextCompaction` item), `{"usage": [primary,
  secondary]}` (`account/rateLimits/updated`), and `{"limit": true}`: the plan's limit, as
  `usageLimitExceeded`, ending the turn failed.

A `turn/steer` enters the turn that runs: it says `Steered: <text>` next. A `turn/interrupt` ends
it as `interrupted`. Every line Atlas writes is appended to `FAKE_CODEX_LOG`, after a first line
with the command line, the credential variables of its environment and its `CODEX_HOME`.

Its sessions are kept in `fake-threads.json` in its `CODEX_HOME` (which must exist, as Codex
requires), so they are listed, read, resumed, forked (from another home's file too, by the
`path` `thread/read` names) and deleted. `thread/list` ignores `cwd` with `useStateDbOnly`, as
0.163.0 does. With `FAKE_CODEX_LIMITED` set, the plan's limit is reached, as a real one is:
every prompt stops at it, and its 5-hour window is used up. `FAKE_CODEX_CREDITS` says whether
credits pay on past the limits: `on`, or `unsaid` for none in its answer; off by default. Its
login is `fake-auth.json` there: `user@example.com` on `plus` by default for the user's own
folder, nobody in another until `codex login`, which logs in `other@example.com`.
"""

import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

VERSION = os.environ.get("FAKE_CODEX_VERSION", "0.163.0")
MODELS = [
    {
        "id": "gpt-6-luna",
        "model": "gpt-6-luna",
        "displayName": "GPT-6 Luna",
        "description": "The default.",
        "hidden": False,
        "isDefault": True,
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low", "description": ""},
            {"reasoningEffort": "medium", "description": ""},
            {"reasoningEffort": "high", "description": ""},
        ],
        "defaultReasoningEffort": "medium",
    },
    {
        "id": "gpt-5.6-terra",
        "model": "gpt-5.6-terra",
        "displayName": "GPT-5.6 Terra",
        "description": "",
        "hidden": False,
        "isDefault": False,
        "supportedReasoningEfforts": [{"reasoningEffort": "ultra", "description": ""}],
        "defaultReasoningEffort": "ultra",
    },
    {"id": "secret", "model": "secret", "displayName": "Hidden", "hidden": True},
]


def home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def own_home() -> bool:
    return home().name == ".codex" or not home().name.startswith(".codex-")


def threads_file(folder: Path | None = None) -> Path:
    return (folder or home()) / "fake-threads.json"


def load(path: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads((path or threads_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _credits() -> dict[str, Any]:
    """What `rateLimits` says of the account's credits (`FAKE_CODEX_CREDITS`)."""
    given = os.environ.get("FAKE_CODEX_CREDITS", "")
    if given == "unsaid":
        return {}
    on = given == "on"
    return {"credits": {"hasCredits": on, "unlimited": False, "balance": "10" if on else "0"}}


def save(threads: dict[str, Any]) -> None:
    """Written whole and then swapped in, so another fake Codex reading it at that moment (a
    resume in the next process) never finds it half written and the thread missing."""
    path = threads_file()
    part = path.with_name(f"{path.name}.{os.getpid()}")
    part.write_text(json.dumps(threads), encoding="utf-8")
    for _ in range(50):
        try:
            part.replace(path)
            return
        except PermissionError:  # Windows, while another process has it open to read
            time.sleep(0.02)
    part.replace(path)


def auth() -> dict[str, Any] | None:
    try:
        return json.loads((home() / "fake-auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if own_home() and not os.environ.get("FAKE_CODEX_LOGGED_OUT"):
            return {"email": "user@example.com", "planType": "plus"}
        return None


class Server:
    def __init__(self) -> None:
        self.out = threading.Lock()
        self.next_id = 1000
        self.replies: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.threads = load()
        self.thread: str | None = None
        self.reviewer = "user"
        self.cwd = str(Path.cwd())
        self.turn_id: str | None = None
        self.steers: queue.Queue[str] = queue.Queue()
        self.interrupted = threading.Event()
        self.prompts = 0
        self.log_file = os.environ.get("FAKE_CODEX_LOG")

    # -- the wire ---------------------------------------------------------------------------------

    def log(self, entry: dict[str, Any]) -> None:
        if self.log_file:
            with Path(self.log_file).open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry) + "\n")

    def send(self, message: dict[str, Any]) -> None:
        with self.out:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"method": method, "params": params})

    def ask(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        waiting: queue.Queue[dict[str, Any]] = queue.Queue()
        self.replies[request_id] = waiting
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            try:
                reply = waiting.get(timeout=0.05)
            except queue.Empty:
                if self.interrupted.is_set():
                    self.notify("serverRequest/resolved", {"requestId": request_id})
                    return {}
                continue
            return reply.get("result") or {}

    # -- requests ---------------------------------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> None:
        if "method" not in message:
            waiting = self.replies.pop(int(message.get("id", -1)), None)
            if waiting is not None:
                waiting.put(message)
            return
        method = message["method"]
        params = message.get("params") or {}
        if "id" not in message:
            return
        try:
            snake = re.sub(r"(?<=[a-z])([A-Z])", r"_\1", method).lower()
            result = getattr(self, "on_" + snake.replace("/", "_"))(params)
        except AttributeError:
            self.send({"id": message["id"], "error": {"code": -32601, "message": f"no {method}"}})
            return
        except LookupError as error:
            self.send({"id": message["id"], "error": {"code": -32600, "message": str(error)}})
            return
        self.send({"id": message["id"], "result": result})

    def on_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"userAgent": "fake", "codexHome": str(home())}

    def on_account_read(self, params: dict[str, Any]) -> dict[str, Any]:
        found = auth()
        account = None if found is None else {"type": "chatgpt", **found}
        return {"account": account, "requiresOpenaiAuth": True}

    def on_model_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"data": MODELS, "nextCursor": None}

    def on_mcp_server_status_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "data": [
                {"name": "docs", "pluginId": None, "authStatus": "bearerToken", "toolsError": None},
                {"name": "github", "pluginId": "gh@1", "authStatus": "notLoggedIn"},
            ],
            "nextCursor": None,
        }

    def on_account_rate_limits_read(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "rateLimits": {
                "planType": "plus",
                "primary": {
                    "usedPercent": 100 if os.environ.get("FAKE_CODEX_LIMITED") else 12,
                    "windowDurationMins": 300,
                    "resetsAt": 1790000000,
                },
                "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": None},
                **_credits(),
            }
        }

    def on_thread_start(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = str(uuid.uuid4())
        self.cwd = params.get("cwd") or self.cwd
        now = int(time.time())
        self.threads[thread] = {
            "cwd": self.cwd,
            "preview": "",
            "name": "",
            "createdAt": now,
            "updatedAt": now,
            "source": "appServer",
            "turns": [],
        }
        save(self.threads)
        return self.opened(thread, params)

    def on_thread_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = params["threadId"]
        if thread not in self.threads:
            self.threads = load()
        if thread not in self.threads:
            raise LookupError(f"no rollout found for thread id {thread}")
        return self.opened(thread, params)

    def on_thread_fork(self, params: dict[str, Any]) -> dict[str, Any]:
        path = params.get("path")
        source = params["threadId"]
        if path:
            file, _, source = str(path).partition("#")
            original = load(Path(file)).get(source)
        else:
            original = self.threads.get(source) or load().get(source)
        if original is None:
            raise LookupError(f"no rollout found for thread id {source}")
        thread = str(uuid.uuid4())
        self.threads[thread] = json.loads(json.dumps(original))
        self.threads[thread]["source"] = "appServer"
        save(self.threads)
        return self.opened(thread, params)

    def opened(self, thread: str, params: dict[str, Any]) -> dict[str, Any]:
        self.thread = thread
        self.reviewer = params.get("approvalsReviewer") or "user"
        self.cwd = params.get("cwd") or self.threads[thread]["cwd"]
        model = params.get("model") or "gpt-6-luna"
        self.notify("thread/started", {"thread": {"id": thread}})
        return {"thread": {"id": thread, "model": model}, "model": model}

    def on_thread_read(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = params["threadId"]
        if thread not in self.threads:
            self.threads = load()
        if thread not in self.threads:
            raise LookupError(f"thread not found: {thread}")
        return {"thread": {"id": thread, "path": f"{threads_file()}#{thread}"}}

    def on_thread_list(self, params: dict[str, Any]) -> dict[str, Any]:
        found = [
            {"id": key, **{k: v for k, v in value.items() if k != "turns"}}
            for key, value in self.threads.items()
            if value.get("source") in (params.get("sourceKinds") or ["cli", "vscode"])
        ]
        if params.get("cwd") and not params.get("useStateDbOnly"):
            found = [thread for thread in found if thread["cwd"] == params["cwd"]]
        found.sort(key=lambda thread: thread["updatedAt"], reverse=True)
        return {"data": found, "nextCursor": None}

    def on_thread_turns_list(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = self.threads.get(params["threadId"])
        if thread is None:
            raise LookupError("thread not found")
        return {"data": thread["turns"], "nextCursor": None}

    def on_thread_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.threads.pop(params["threadId"], None) is None:
            raise LookupError("thread not found")
        save(self.threads)
        return {}

    def on_turn_start(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("threadId") != self.thread:
            raise LookupError("thread not loaded")
        self.reviewer = params.get("approvalsReviewer") or self.reviewer
        turn = self.turn_id = str(uuid.uuid4())
        self.interrupted.clear()
        said = " ".join(part.get("text", "") for part in params.get("input") or [])
        steps = self.steps(said)
        threading.Thread(target=self.run, args=(turn, params, steps), daemon=True).start()
        return {"turn": {"id": turn, "status": "inProgress", "items": []}}

    def on_turn_steer(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("expectedTurnId") != self.turn_id:
            raise LookupError("no active turn to steer")
        self.steers.put(" ".join(part.get("text", "") for part in params["input"]))
        return {"turnId": self.turn_id}

    def on_turn_interrupt(self, params: dict[str, Any]) -> dict[str, Any]:
        self.interrupted.set()
        return {}

    # -- a turn -----------------------------------------------------------------------------------

    def steps(self, prompt: str) -> list[dict[str, Any]]:
        script = os.environ.get("FAKE_CODEX_SCRIPT")
        found: Any = json.loads(Path(script).read_text(encoding="utf-8")) if script else []
        if isinstance(found, dict) and "prompts" in found:
            found = next((v for k, v in found["prompts"].items() if k in prompt), [])
        elif isinstance(found, dict):
            turns = found["turns"]
            found = turns[min(self.prompts, len(turns) - 1)]
        self.prompts += 1
        if os.environ.get("FAKE_CODEX_LIMITED"):
            return [{"limit": True}]
        return list(found)

    def run(self, turn: str, params: dict[str, Any], steps: list[dict[str, Any]]) -> None:
        thread = self.thread or ""
        items: list[dict[str, Any]] = [
            {"type": "userMessage", "id": str(uuid.uuid4()), "content": params["input"]}
        ]
        record = self.threads[thread]
        if not record["preview"]:
            record["preview"] = next(
                (part["text"] for part in params["input"] if part.get("type") == "text"), ""
            )
        self.notify("turn/started", {"threadId": thread, "turn": {"id": turn}})
        status = "completed"
        error: dict[str, Any] | None = None
        for step in steps:
            while not self.steers.empty():
                items.append(
                    self.item(
                        turn, {"type": "agentMessage", "text": f"Steered: {self.steers.get()}"}
                    )
                )
            if self.interrupted.is_set():
                status = "interrupted"
                break
            if "say" in step:
                items.append(self.item(turn, {"type": "agentMessage", "text": step["say"]}))
            elif "think" in step:
                self.item(turn, {"type": "reasoning", "summary": [step["think"]], "content": []})
            elif "command" in step:
                items.append(self.command(turn, step["command"]))
            elif "write" in step:
                items.append(self.write(turn, step["write"], step.get("content", "")))
            elif "questions" in step:
                said = self.ask(
                    "item/tool/requestUserInput",
                    {
                        "threadId": thread,
                        "turnId": turn,
                        "itemId": "q",
                        "questions": step["questions"],
                    },
                )
                answers = json.dumps(said.get("answers", {}), sort_keys=True)
                said_back = {"type": "agentMessage", "text": f"Answers: {answers}"}
                items.append(self.item(turn, said_back))
            elif "sleep" in step:
                end = time.monotonic() + step["sleep"]
                while (
                    time.monotonic() < end and not self.interrupted.is_set() and self.steers.empty()
                ):
                    time.sleep(0.02)
            elif "compact" in step:
                self.notify(
                    "thread/tokenUsage/updated",
                    {
                        "threadId": thread,
                        "turnId": turn,
                        "tokenUsage": {"last": {"inputTokens": 90000}},
                    },
                )
                items.append(self.item(turn, {"type": "contextCompaction"}))
            elif "usage" in step:
                primary, secondary = step["usage"]
                self.notify(
                    "account/rateLimits/updated",
                    {
                        "rateLimits": {
                            "primary": {"usedPercent": primary, "windowDurationMins": 300},
                            "secondary": {"usedPercent": secondary, "windowDurationMins": 10080},
                        }
                    },
                )
            elif "limit" in step:
                error = {
                    "message": "You've hit your usage limit.",
                    "codexErrorInfo": "usageLimitExceeded",
                }
                self.notify(
                    "error",
                    {"error": error, "willRetry": False, "threadId": thread, "turnId": turn},
                )
                status = "failed"
                break
        while status == "completed" and not self.steers.empty():
            items.append(
                self.item(turn, {"type": "agentMessage", "text": f"Steered: {self.steers.get()}"})
            )
        if self.interrupted.is_set():
            status = "interrupted"
        record["turns"].append(
            {"id": turn, "items": items, "status": status, "startedAt": int(time.time())}
        )
        record["updatedAt"] = int(time.time())
        save(self.threads)
        self.turn_id = None
        completed = {"id": turn, "status": status, "error": error, "items": []}
        self.notify("turn/completed", {"threadId": thread, "turn": completed})

    def item(self, turn: str, item: dict[str, Any]) -> dict[str, Any]:
        item = {"id": str(uuid.uuid4()), **item}
        thread = self.thread or ""
        self.notify("item/started", {"threadId": thread, "turnId": turn, "item": item})
        self.notify("item/completed", {"threadId": thread, "turnId": turn, "item": item})
        return item

    def allowed(self, method: str, params: dict[str, Any]) -> bool:
        if self.reviewer == "auto_review":
            return True
        said = self.ask(method, params)
        return said.get("decision") == "accept"

    def command(self, turn: str, command: str) -> dict[str, Any]:
        thread = self.thread or ""
        item_id = str(uuid.uuid4())
        started = {
            "type": "commandExecution",
            "id": item_id,
            "command": command,
            "cwd": self.cwd,
            "status": "inProgress",
        }
        self.notify("item/started", {"threadId": thread, "turnId": turn, "item": started})
        ok = self.allowed(
            "item/commandExecution/requestApproval",
            {
                "threadId": thread,
                "turnId": turn,
                "itemId": item_id,
                "command": command,
                "cwd": self.cwd,
                "reason": "needs the network",
            },
        )
        done = {
            **started,
            "status": "completed" if ok else "declined",
            "aggregatedOutput": "done" if ok else "",
            "exitCode": 0 if ok else None,
        }
        self.notify("item/completed", {"threadId": thread, "turnId": turn, "item": done})
        return done

    def write(self, turn: str, name: str, content: str) -> dict[str, Any]:
        thread = self.thread or ""
        item_id = str(uuid.uuid4())
        path = str(Path(self.cwd) / name)
        started = {
            "type": "fileChange",
            "id": item_id,
            "changes": [{"path": path, "kind": {"type": "add"}}],
            "status": "inProgress",
        }
        self.notify("item/started", {"threadId": thread, "turnId": turn, "item": started})
        ok = self.allowed(
            "item/fileChange/requestApproval",
            {"threadId": thread, "turnId": turn, "itemId": item_id, "reason": None},
        )
        if ok:
            Path(path).write_text(content, encoding="utf-8")
        done = {**started, "status": "completed" if ok else "declined"}
        self.notify("item/completed", {"threadId": thread, "turnId": turn, "item": done})
        return done


def main() -> None:
    arguments = sys.argv[1:]
    if arguments == ["--version"]:
        sys.stdout.write(f"codex-cli {VERSION}" + "\n")
        return
    if not home().is_dir():
        sys.stderr.write(f"CODEX_HOME points to {home()}, but that path does not exist" + "\n")
        sys.exit(1)
    if arguments == ["login"]:
        (home() / "fake-auth.json").write_text(
            json.dumps({"email": "other@example.com", "planType": "pro"}), encoding="utf-8"
        )
        sys.stdout.write("Successfully logged in" + "\n")
        return
    if arguments[:1] != ["app-server"]:
        sys.stderr.write(f"unknown command {arguments}" + "\n")
        sys.exit(2)
    server = Server()
    credentials = {
        name: value
        for name, value in os.environ.items()
        if name.upper().startswith("OPENAI_") or name.upper() in ("CODEX_API_KEY",)
    }
    server.log({"argv": arguments, "credentials": credentials, "home": str(home())})
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        server.log(message)
        server.handle(message)


if __name__ == "__main__":
    main()
