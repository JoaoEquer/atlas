# SPDX-FileCopyrightText: 2025 Nous Research
# SPDX-FileCopyrightText: 2026 the Atlas contributors
# SPDX-License-Identifier: MIT
"""Shell commands Atlas refuses outright, and those it runs only once the user approves.

Two verdicts come out of this module, and it keeps no state to give them:

- **Hardline.** Refused in every mode, approvals or not. The list is short on purpose: only what
  has no way back (deleting the root, a system folder or the home, writing a raw disk, powering
  the host off, a fork bomb). Anything recoverable is merely dangerous.
- **Dangerous.** Asks the user first. The gate stores an "always" answer under the rule's
  description, so a description is a stored key: its wording never changes.

Matching is plain text, made hard to dodge. A command is first brought to one spelling (terminal
codes, NUL bytes, escapes, `${IFS}`, the user's absolute home), then read in several variants:
command starts marked, Windows paths turned to `/`, command words spelled out, and the code a
command hands to another program (`bash -c`, `rg --pre`, `env -S`). The first rule that matches
any variant decides.

Atlas's own files are covered wherever they are written from: the vault (`~/.atlas/secrets`)
and the home's settings (`config.toml`, `mcp.json`, `models.toml`). The gate first folds an
`ATLAS_HOME` kept elsewhere into the `~/.atlas` spelling (`fold_atlas_home`). Killing Atlas's own
process by name asks first. `sudo -S` is refused: it reads a password from standard input, and
Atlas never supplies one. Nothing here reads a setting (docs/ARCHITECTURE.md, Settings); the
user's home comes from the platform's home lookup.
"""

import functools
import os
import re
import shlex
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from typing import NamedTuple

# ==== Terminal control codes ======================================================================

# Escape sequences a terminal acts on (ECMA-48): CSI, OSC, the string controls DCS/SOS/PM/APC, nF
# and single-character escapes, then their 8-bit C1 forms. They can hide text from the person who
# reads a command while the shell still runs it, so they go before anything is matched.
_CSI_BODY = r"[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
_TERMINAL_CONTROL = re.compile(
    "|".join(
        (
            r"\x1b\[" + _CSI_BODY,
            r"\x1b\][\s\S]*?(?:\x07|\x1b\\)",
            r"\x1b[PX^_][\s\S]*?(?:\x1b\\)",
            r"\x1b[\x20-\x2f]+[\x30-\x7e]",
            r"\x1b[\x30-\x7e]",
            r"\x9b" + _CSI_BODY,
            r"\x9d[\s\S]*?(?:\x07|\x9c)",
            r"[\x80-\x9f]",
        )
    ),
    re.DOTALL,
)
_CONTROL_LEAD = re.compile(r"[\x1b\x80-\x9f]")


def strip_ansi(text: str) -> str:
    """Return *text* without terminal control sequences."""
    if text and _CONTROL_LEAD.search(text):
        return _TERMINAL_CONTROL.sub("", text)
    return text


# ==== Paths the rules protect =====================================================================

# Rules name the user's home as `~`, `$HOME` or `${HOME}` only. Before matching, the resolved home
# is folded into `~` (_fold_user_home) and the gate folds ATLAS_HOME (fold_atlas_home), so no
# absolute path is baked into a pattern. Patterns see lowercased text, hence `$home`.
_HOME = r"(?:~|\$home|\$\{home\})/"
_SSH_DIR = _HOME + r"\.ssh(?:/|$)"
_ATLAS_DIR = r"(?:~/\.atlas/|(?:\$home|\$\{home\})/\.atlas/|(?:\$atlas_home|\$\{atlas_home\})/)"
_ATLAS_VAULT = _ATLAS_DIR + r"secrets\b"
# The settings decide what Atlas runs and where it connects (mcp.json starts programs), so
# writing them from a terminal asks first, as writing them with the file tools does.
_ATLAS_SETTINGS = _ATLAS_DIR + r"(?:config\.toml|mcp\.json|models\.toml)\b"
_LOGIN_SCRIPTS = _HOME + r"\.(?:bashrc|zshrc|profile|bash_profile|zprofile)\b"
_LOGIN_SECRETS = _HOME + r"\.(?:netrc|pgpass|npmrc|pypirc)\b"
# A project's `.env` (any `.env.*` too) and `config.yaml`, at any relative or absolute depth.
_PROJECT_DIRS = r"""(?:/|\.{1,2}/)?(?:[^\s/"'`]+/)*"""
# One optional `.suffix` run covers every `.env.a.b.c`: the run may hold dots itself. Repeating
# the group would match the same names, but in exponentially many ways, so a long dotted name
# that then failed the next check froze the matcher.
_PROJECT_ENV = rf"""(?:{_PROJECT_DIRS}\.env(?:\.[^/\s"'`]+)?)"""
_PROJECT_CONFIG = rf"(?:{_PROJECT_DIRS}config\.yaml)"
_CLOUD_LOGINS = (
    r"\.(?:aws/credentials|git-credentials|config/gh/hosts\.yml|docker/config\.json|kube/config)\b"
)
_KEY_OR_PASSWORD_FILE = (
    rf"(?:{_SSH_DIR}|{_ATLAS_VAULT}|{_LOGIN_SECRETS}|{_PROJECT_ENV}(?![\w-])|{_HOME}{_CLOUD_LOGINS}"
    r"|/etc/(?:shadow|passwd)\b|\bid_(?:rsa|ed25519|ecdsa|dsa)\b)"
)
_SECRET_NAMED_VARIABLE = r"\$\{?\w*(?:key|token|secret|passwd|password|credential)\w*\}?"
# curl and wget options whose value becomes the request body or an uploaded file. The short
# letters keep their case (curl's -D writes headers, -f only fails), so that group is case-exact.
_SENDS_BODY = (
    r"(?<!\S)(?:(?-i:-[a-zA-Z]*[dFT])|--data(?:-binary|-raw|-urlencode|-ascii)?|--form(?:-string)?"
    r"|--upload-file|--json|--post-(?:data|file)|--body-(?:data|file))"
)
_EMOJI = r"\U0001F000-\U0001FAFF\u2600-\u27BF"
# Options before a docker or podman subcommand, each with an optional value. Each option has one
# reading (`-` and a possessive rest), and a value never starts with `-`, so a long run of options
# fails in linear time instead of backtracking for minutes. A separate value follows exactly one
# whitespace character, which is how these rules have always read it.
_CONTAINER_OPTIONS = r"(?:-\S++(?:\s(?!-\S)\S++)?\s++)*"
# On macOS /etc, /var, /tmp and /home are links into /private, so both spellings count.
_SYSTEM_CONFIG = r"(?:/etc/|/private/(?:etc|var|tmp|home)/)"
_PROTECTED_TARGET = (
    rf"(?:{_SYSTEM_CONFIG}|/dev/sd|{_SSH_DIR}|{_ATLAS_VAULT}|{_ATLAS_SETTINGS}|"
    rf"{_LOGIN_SCRIPTS}|{_LOGIN_SECRETS})"
)
_USER_TARGET = rf"(?:{_SSH_DIR}|{_LOGIN_SCRIPTS}|{_LOGIN_SECRETS})"
_PROJECT_TARGET = rf"(?:{_PROJECT_ENV}|{_PROJECT_CONFIG})"
# cp, mv and install write only their last argument, so a protected path counts there and nowhere
# else. Copying config.yaml to a backup only reads it.
_LAST_ARGUMENT = r"(?:\s*(?:&&|\|\||;).*)?$"
# `>`, `>>` and tee always write their target, whatever follows it, so the path only has to end
# as a shell word. A `#` glued to the name is part of it (`.env#old`), not a comment.
_WORD_END = r"""(?=[\s;&|<>"']|$)"""

# ==== Where a command starts ======================================================================

# A rule about a program holds only where the shell would run it: at the start of the text or of
# a line, or just inside `$(` or a backtick, after any sudo, env and runner prefixes. `;`, `&` and
# `|` are left out on purpose: a quote-aware pass turns the real ones into newlines first
# (_with_start_marks), so a `|` inside quoted data never passes for a command start.
_START = r"(?:^|[\n`]|\$\()"
_SUDO_LEAD = r"(?:sudo\s+(?:-\S+\s+)*)?"
_ENV_LEAD = r"(?:env\s+(?:\w+=\S*\s+)*)?"
_RUNNER_LEAD = r"(?:(?:exec|nohup|setsid|time)\s+)*"
_AT_COMMAND = _START + r"\s*" + _SUDO_LEAD + _ENV_LEAD + _RUNNER_LEAD + r"\s*"

# ==== Hardline rules ==============================================================================

_RM = _AT_COMMAND + r"rm\s+(?:-\S*\s+)*"


def _rm_target(paths: str) -> str:
    """A path given to `rm`: wrapped whole in quotes, or bare and followed by a word end."""
    return rf"""(?:["'](?:{paths})["']|(?:{paths})(?:\s|$|[)`;|&]))"""


# Root paths that collapse to `/`: each part between slashes is `.` or `..` and nothing else, so
# `/tmp`, `/.ssh` and even `/...` are real folders, left to the dangerous rules. `/ *` is
# `rm -rf / *`, the root and a glob.
_ROOT_PATHS = r"/(?:(?:\.\.?)?/)*(?:\.\.?)?\**|/ \*"
_SYSTEM_DIRS = "|".join(
    rf"/{name}|/{name}/\*"
    for name in ("home", "root", "etc", "usr", "var", "bin", "sbin", "boot", "lib")
)
_HOME_PATHS = r"(?:~|\$\{?HOME\}?)(?:/?|/\*)?"
_RAW_DISK = r"/dev/(?:sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*"
_FORK_BOMB = r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"

# (description, pattern). Order matters: the first match names the reason. Most rules start at a
# command position, so `git commit -m "never mkfs"` is data. The disk redirect and the fork bomb
# have no program name to anchor on; they read the command with quoted text blanked instead.
_HARDLINE_TABLE = (
    ("recursive delete of root filesystem", _RM + _rm_target(_ROOT_PATHS)),
    ("recursive delete of system directory", _RM + _rm_target(_SYSTEM_DIRS)),
    ("recursive delete of home directory", _RM + _rm_target(_HOME_PATHS)),
    ("format filesystem (mkfs)", _AT_COMMAND + r"mkfs(?:\.[a-z0-9]+)?\b"),
    # Where `of=` sits among dd's operands does not matter.
    ("dd to raw block device", _AT_COMMAND + r"dd\b[^\n]*\bof=" + _RAW_DISK),
    ("redirect to raw block device", r">\s*" + _RAW_DISK + r"\b"),
    ("fork bomb", _FORK_BOMB),
    ("kill all processes", _AT_COMMAND + r"kill\s+(?:-\S+\s+)*-1\b"),
    ("system shutdown/reboot", _AT_COMMAND + r"(?:shutdown|reboot|halt|poweroff)\b"),
    ("init 0/6 (shutdown/reboot)", _AT_COMMAND + r"init\s+[06]\b"),
    ("systemctl poweroff/reboot", _AT_COMMAND + r"systemctl\s+(?:poweroff|reboot|halt|kexec)\b"),
    ("telinit 0/6 (shutdown/reboot)", _AT_COMMAND + r"telinit\s+[06]\b"),
)
_HARDLINE_ON_BLANKED_QUOTES = frozenset({"redirect to raw block device", "fork bomb"})

# ==== Dangerous rules =============================================================================

_SHELL_NAMES = ("bash", "sh", "zsh", "ksh", "dash")
_A_SHELL = "(?:" + "|".join(_SHELL_NAMES) + ")"
_INTO_A_SHELL = rf"\|\s*\b{_A_SHELL}\b"
_FLAG_WORDS = r"(?:-\S*\s+)*"
_EXE = r"(?:\.exe)?"
_POWERSHELL = rf"\b(?:powershell|pwsh){_EXE}\b"
_WEB_FETCH = "iwr|invoke-webrequest|invoke-restmethod|irm"
_RUN_TEXT = "iex|invoke-expression"
_OPEN_TO_ALL = r"(?:777|666|o\+[rwx]*w|a\+[rwx]*w)"
_KILLALL = r"\bkillall\s+" + _FLAG_WORDS
_MAYBE_QUOTE = r"""["']?"""
_REST_OF_NAME = r"""[^\s"']*"""
_COPY_TO = r"\b(?:cp|mv|install)\b.*\s"
_SED_I = r"\bsed\s+-\S*i.*"
_SED_IN_PLACE = r"\bsed\s+--in-place\b.*"
_SCRIPT_I = r"\b(?:perl|ruby)\b.*(?:^|\s)-\S*i\b.*"
_ATLAS_FILES = rf"(?:{_ATLAS_SETTINGS}|{_ATLAS_VAULT})"
_DOCKER = r"\bdocker\s+" + _CONTAINER_OPTIONS
_PODMAN = r"\bpodman\s+" + _CONTAINER_OPTIONS
# Package-manager options before the subcommand, each with at most one value that is not a flag
# (`--prefix DIR`, `--cwd DIR`).
_PACKAGE_OPTIONS = r"(?:-\S+(?:\s+[^-\s]\S*)?\s+)*"
_GIT_BRANCH = r"\bgit\s+branch\b"
_ONE_COMMAND = r"[^;|&\n]*?"


# Rules read with quoted text blanked (see _DANGEROUS_ON_BLANKED_QUOTES).
# `find . -{delete,print}`, `find . -del*`
_FIND_GLOB = "find dynamic shell word may expand to destructive flag"
# `rg --pre{,} prog`, `sort --compress-prog* prog`
_TOOL_OPTION_GLOB = "dynamic shell word may expand to arbitrary program execution flag"


def _on_line(program: str, rest: str) -> str:
    """*program* as a word, then *rest* anywhere further on the same line."""
    return rf"\b{program}\b[^\n]*{rest}"


def _kill_by_lookup(opener: str) -> str:
    """`kill` given the output of pgrep or pidof, through the substitution *opener* starts."""
    return r"\bkill\b.*" + opener + r"\s*(?:pgrep|pidof)\b"


def _uninstall(manager: str, verbs: str) -> tuple[str, str]:
    """A package manager at a command position removing software, past any global options."""
    return (
        "package manager uninstall",
        _AT_COMMAND + manager + r"\s+" + _PACKAGE_OPTIONS + verbs + r"\b",
    )


# (description, pattern), tried in this order. The text is lowercased first, except words that
# start with `-`, so the case-exact groups below can tell `-D` from `-d`.
_DANGEROUS_TABLE = (
    # -- Deleting files.
    ("delete in root path", rf"\brm\s+{_FLAG_WORDS}/"),
    ("recursive delete", r"\brm\s+-\S*r"),
    ("recursive delete (long flag)", r"\brm\s+--recursive\b"),
    # rm also takes its options after the files (`rm build/ -rf`). The files may not cross a
    # command separator or a quote, and a lone `--` ends the options, after which `-rf` would be a
    # file name. The flag must follow a blank, so the r of `--registry` does not count.
    (
        "recursive delete (flags after operands)",
        r"\brm\s+(?!--(?:\s|$))(?:(?!\s--(?:\s|$))[^\n\"';|&])*\s(?:-[a-z]*r[a-z]*\b|--recursive\b)",
    ),
    # -- Windows. These programs are reachable from any shell on a Windows host, git-bash too.
    # Each rule needs the destructive verb or switch, so `taskkill /IM app.exe`, `reg query` and
    # `icacls file` pass. cmd's delete verbs count only when cmd runs them, so a file named "del"
    # does not.
    ("Windows cmd destructive delete", rf"\bcmd{_EXE}\s+/[ck]\s+.*\b(?:del|erase|rd|rmdir)\b"),
    # PowerShell runs its first positional word as a command; the verb has to be there, after any
    # switches and an optional -Command, so `-File c:\del-logs\run.ps1` passes.
    (
        "Windows PowerShell destructive delete",
        _POWERSHELL
        + r"(?:\s+-\S+)*\s+(?:-(?:command|c)\s+)?[\"']?(?:remove-item|rmdir|erase|del|rd|ri|rm)\b",
    ),
    ("PowerShell encoded command execution", _POWERSHELL + r".*\s-(?:encodedcommand|enc|e)\b"),
    # Remove-Item on its own, as a PowerShell host or a later command in a chain would run it.
    (
        "PowerShell destructive delete (Remove-Item)",
        r"\bremove-item\b[^\n;|&]*\s-(?:recurse|force)\b",
    ),
    # Bare cmd delete verbs only with /s or /q; a plain `del file.txt` needs the cmd rule above.
    (
        "Windows destructive delete (recursive/quiet switch)",
        r"\b(?:del|erase|rd|rmdir)\s+(?:/[a-z]\s+)*/[sq]\b",
    ),
    (
        "pipe remote content to PowerShell (iwr | iex)",
        _on_line(f"(?:{_WEB_FETCH}|curl|wget)", rf"\|\s*(?:{_RUN_TEXT})\b"),
    ),
    (
        "execute remote content via Invoke-Expression",
        rf"\b(?:{_RUN_TEXT})\s*\(\s*(?:{_WEB_FETCH})\b",
    ),
    ("force kill processes (taskkill /F)", _on_line("taskkill", r"\s/f\b")),
    ("force kill processes (Stop-Process -Force)", _on_line("stop-process", r"\s-force\b")),
    ("format filesystem (Format-Volume)", r"\bformat-volume\b"),
    ("wipe disk (Clear-Disk)", r"\bclear-disk\b"),
    ("disk partitioning (diskpart)", r"\bdiskpart\b"),
    ("format drive (format.com)", r"\bformat(?:\.com)?\s+[a-z]:"),
    ("wipe free space (cipher /w)", r"\bcipher\s+/w\b"),
    # "Everyone" in English, Spanish/Portuguese, German and French, and its well-known SID.
    (
        "grant Everyone access (icacls)",
        _on_line(
            "icacls", r"\s/grant\b[^\n]*\b(?:everyone|todos|jeder|tout\s+le\s+monde|\*s-1-1-0)\b"
        ),
    ),
    ("reset ACLs recursively (icacls /reset)", _on_line("icacls", r"\s/reset\b")),
    # Shadow copies and backups go first when ransomware prepares a host.
    ("delete volume shadow copies (vssadmin)", _on_line("vssadmin", r"\bdelete\s+shadows\b")),
    ("delete backups (wbadmin)", _on_line("wbadmin", r"\bdelete\b")),
    ("modify boot configuration (bcdedit /set)", _on_line("bcdedit", r"\s/set\b")),
    ("registry delete (reg delete)", rf"\breg{_EXE}\s+delete\b"),
    (
        "registry value delete (Remove-ItemProperty -Force)",
        _on_line("remove-itemproperty", r"\s-force\b"),
    ),
    ("force stop service (Stop-Service -Force)", _on_line("stop-service", r"\s-force\b")),
    ("stop/delete service (sc)", rf"\bsc{_EXE}\s+(?:stop|delete)\b"),
    # Drive-letter and backslash spellings, which the `~/.ssh` rules never see.
    ("access to SSH keys (Windows path)", r"\busers[\\/][^\\/\s]+[\\/]\.ssh\b"),
    ("access to Atlas secrets (Windows path)", r"[\\/]\.atlas[\\/]secrets\b"),
    # -- Permissions and owners.
    ("world/other-writable permissions", rf"\bchmod\s+{_FLAG_WORDS}{_OPEN_TO_ALL}\b"),
    ("recursive world/other-writable (long flag)", rf"\bchmod\s+--recursive\b.*{_OPEN_TO_ALL}"),
    ("recursive chown to root", r"\bchown\s+(?:-\S*)?R\s+root"),
    ("recursive chown to root (long flag)", r"\bchown\s+--recur[a-z]*\b.*root"),
    # -- Disks and databases. mkfs and dd start at a command position, like their hardline rules.
    ("format filesystem", _AT_COMMAND + r"mkfs\b"),
    ("disk copy", _AT_COMMAND + r"dd\s+.*if="),
    ("write to block device", r">\s*/dev/sd"),
    ("SQL DROP", r"\bDROP\s+(?:TABLE|DATABASE)\b"),
    # The WHERE must be on the same line: with `.` crossing lines, a WHERE in a later statement
    # would let this one through.
    ("SQL DELETE without WHERE", r"\bDELETE\s+FROM\b(?![^\n]*\bWHERE\b)"),
    ("SQL TRUNCATE", r"\bTRUNCATE\s+(?:TABLE)?\s*\w"),
    ("overwrite system config", r">\s*" + _SYSTEM_CONFIG),
    # -- Services and processes.
    (
        "stop/restart system service",
        r"\bsystemctl\s+(?:-\S+\s+)*(?:stop|restart|disable|mask)\b",
    ),
    ("kill all processes", r"\bkill\s+-9\s+-1\b"),
    ("force kill processes", r"\bpkill\s+-9\b"),
    # killall with SIGKILL in any spelling, and -r, whose pattern can sweep up anything.
    ("force kill processes (killall -KILL)", _KILLALL + r"-(?:9|KILL|SIGKILL)\b"),
    ("force kill processes (killall -s KILL)", _KILLALL + r"-s\s+(?:KILL|SIGKILL|9)\b"),
    ("kill processes by regex (killall -r)", _KILLALL + r"-r\b"),
    ("fork bomb", _FORK_BOMB),
    # -- Running code from somewhere else. `sh -c` itself is read by _program_findings: a pattern
    # looking for a c in a dash word also caught --norc, --rcfile and --restricted.
    (
        "pipe remote content to shell",
        rf"\b(?:curl|wget)\b.*\|\s*(?:[/\w]*/)?{_A_SHELL}(?:\s|$|-c)",
    ),
    (
        "execute remote script via process substitution",
        rf"\b{_A_SHELL}\s+<\s*<?\s*\(\s*(?:curl|wget)\b",
    ),
    (
        "execute remote content via command substitution",
        r"(?:\beval\b|\bsource\b|\.)\s*(?:\$\(\s*|`\s*)(?:curl|wget)\b",
    ),
    # Cloud metadata services hand the machine's credentials to any local process that asks, so a
    # request to them asks first. These addresses mean nothing else, so they count anywhere in the
    # command; the lookarounds keep other link-local addresses and longer dotted names out. On a
    # real cloud machine the use can be legitimate, hence an approval and not a refusal. Covered:
    # the link-local IPv4 address most clouds use, its IPv6 form on AWS, Google's host name and
    # Alibaba's address.
    (
        "cloud metadata endpoint access (instance credentials)",
        r"(?<![\d.])(?:169\.254\.169\.254|100\.100\.100\.200)(?![\d.])"
        r"|(?<![\w.-])metadata\.google\.internal(?![\w.-])"
        r"|fd00:ec2::254",
    ),
    # Decoded text piped into a shell carries no telltale word of its own.
    (
        "pipe decoded content to shell (possible command obfuscation)",
        rf"\b(?:base64|base32|base16)\s+(?:-[dD]|--decode)\b.*{_INTO_A_SHELL}",
    ),
    (
        "pipe xxd-decoded content to shell (possible command obfuscation)",
        rf"\bxxd\s+-r\b.*{_INTO_A_SHELL}",
    ),
    # `echo 'eq -pe v/' | tr 'eqv' 'rmf' | bash` runs `rm -rf /`.
    (
        "pipe tr-transformed output to shell (possible command obfuscation)",
        rf"\becho\b[^|]*\|\s*\btr\b[^|]*{_INTO_A_SHELL}",
    ),
    (
        "pipe openssl-decoded content to shell (possible command obfuscation)",
        rf"\bopenssl\b.*\b(?:base64|enc)\b[^|]*\s+-[dD]\b[^|]*{_INTO_A_SHELL}",
    ),
    # -- Sending secrets out: a secret-named variable or a key file in a request body (a header
    # such as `-H "Authorization: Bearer $KEY"` is ordinary API use), or a key file piped into an
    # uploading curl or wget.
    (
        "upload a secret or credential file via curl/wget (possible exfiltration)",
        rf"""{_SENDS_BODY}(?:[\s=]+|(?=[@"'$]))(?:"[^"]*(?:{_SECRET_NAMED_VARIABLE}|{_KEY_OR_PASSWORD_FILE})"""
        rf"""|'[^']*{_KEY_OR_PASSWORD_FILE}|\$\([^)]*{_KEY_OR_PASSWORD_FILE}"""
        rf"|\S*?(?:{_SECRET_NAMED_VARIABLE}|{_KEY_OR_PASSWORD_FILE}))",
    ),
    (
        "pipe a credential file into a curl/wget upload (possible exfiltration)",
        rf"{_KEY_OR_PASSWORD_FILE}[^|;&\n]*\|[^;&|\n]*\b(?:curl|wget)\b[^;&|\n]*{_SENDS_BODY}",
    ),
    # Invisible and direction-changing characters make the command the user reads differ from
    # the one that runs. A zero-width joiner between emoji is ordinary text; variation selectors
    # are left alone.
    (
        "invisible or bidirectional Unicode control character (possible obfuscation)",
        r"[\u200b\u200c\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u180e\U000e0000-\U000e007f]"
        rf"|(?<![{_EMOJI}\ufe0f])\u200d|\u200d(?![{_EMOJI}])",
    ),
    # -- Writing protected files.
    ("overwrite system file via tee", rf"\btee\b.*{_MAYBE_QUOTE}{_PROTECTED_TARGET}"),
    ("overwrite system file via redirection", rf">>?\s*{_MAYBE_QUOTE}{_PROTECTED_TARGET}"),
    (
        "overwrite project env/config via tee",
        rf"\btee\b.*{_MAYBE_QUOTE}{_PROJECT_TARGET}{_MAYBE_QUOTE}{_WORD_END}",
    ),
    (
        "overwrite project env/config via redirection",
        rf">>?\s*{_MAYBE_QUOTE}{_PROJECT_TARGET}{_MAYBE_QUOTE}{_WORD_END}",
    ),
    ("xargs with rm", r"\bxargs\s+.*\brm\b"),
    # -execdir runs its command like -exec, from each match's folder.
    ("find -exec/-execdir rm", r"\bfind\b.*-exec(?:dir)?\s+(?:/\S*/)?rm\b"),
    # A word the shell expands into one of these flags when it runs (`find . -{delete,print}`,
    # `find . -del*`). Read with quoted text blanked: a quoted glob (`-name 'log-del*'`) is an
    # argument the shell never expands. Words built from `$var` or `$(...)` are not covered.
    (
        _FIND_GLOB,
        _AT_COMMAND
        + r"find\s[^;|&\n]*(?<!\S)-(?:\{[^}\s]*(?:delete|exec(?:dir)?)[^}\s]*\}|(?:del(?:ete?)?|exec(?:dir)?)[*?\[])",
    ),
    ("find -delete", r"\bfind\b.*-delete\b"),
    # The same for the read tools' program options, which _program_findings reads only when they
    # are spelled out.
    (
        _TOOL_OPTION_GLOB,
        r"\b(?:rg|sort|ag|man)\b[^;|&\n]*(?<!\S)--(?:pre|hostname-bin|compress-program|pager|html)(?:\{|[*?\[])",
    ),
    # -- Containers. A global option or variable that points the CLI at another daemon, often a
    # remote one, asks whatever the subcommand: `docker -H ssh://prod stop app` looks local. The
    # option must come before the subcommand and carry a value, so `docker -h` and
    # `docker run -h <hostname>` pass. These come before the lifecycle rules so a redirected stop
    # names the redirect.
    ("docker with remote daemon redirect (-H/--host)", _DOCKER + r"(?:-h|--host)[=\s]+\S+"),
    (
        "docker with daemon redirect (--context: alternate daemon)",
        _DOCKER + r"(?:-c|--context)[=\s]+\S+",
    ),
    (
        "docker context use (switches default daemon for future commands)",
        r"\bdocker\s+context\s+use\b",
    ),
    (
        "podman with remote daemon redirect (--url/--connection/--identity)",
        _PODMAN + r"(?:--url|--connection|--identity)[=\s]+\S+",
    ),
    ("podman remote mode (-r/--remote: remote daemon)", _PODMAN + r"(?:-r\b|--remote\b)"),
    (
        "docker/podman daemon redirect via environment (DOCKER_HOST/CONTAINER_HOST)",
        r"\b(?:docker_host|docker_context|container_host|container_connection)=\S+",
    ),
    # Stopping or killing containers always asks: a mounted docker socket gives a command that
    # power. The legacy docker-compose binary counts too.
    (
        "docker compose restart/stop/kill/down (container lifecycle)",
        r"\bdocker(?:-compose|\s+compose)\s+"
        + _CONTAINER_OPTIONS
        + r"(?:restart|stop|kill|down)\b",
    ),
    ("docker restart/stop/kill (container lifecycle)", _DOCKER + r"(?:restart|stop|kill)\b"),
    # -- Atlas killing itself, by name or through a pgrep/pidof substitution the name rule cannot
    # see into.
    ("kill atlas process (self-termination)", r"\b(?:pkill|killall)\b.*\batlas\b"),
    ("kill process via pgrep/pidof expansion (self-termination)", _kill_by_lookup(r"\$\(")),
    (
        "kill process via backtick pgrep/pidof expansion (self-termination)",
        _kill_by_lookup("`"),
    ),
    # -- Copying over protected files: a planted SSH key, a login script that runs at the next
    # shell, a replaced credential or Atlas setting. Only the destination counts, so copying out
    # of ~/.ssh is fine; the rest of the destination name is part of the target.
    ("copy/move file into system config path", _COPY_TO + _SYSTEM_CONFIG),
    (
        "overwrite project env/config file",
        _COPY_TO + _MAYBE_QUOTE + _PROJECT_TARGET + _MAYBE_QUOTE + _LAST_ARGUMENT,
    ),
    (
        "copy/move file into sensitive credential/SSH/shell-rc path",
        _COPY_TO + _MAYBE_QUOTE + _PROTECTED_TARGET + _REST_OF_NAME + _MAYBE_QUOTE + _LAST_ARGUMENT,
    ),
    # -- Editing in place, which writes the file without a redirection, tee or cp. perl and ruby
    # take -i alone (`-p -i -e`), bundled (`-pi`) or with a backup suffix (`-i.bak`).
    (
        "in-place edit of sensitive credential/SSH/shell-rc path",
        rf"{_SED_I}(?:{_USER_TARGET}){_REST_OF_NAME}",
    ),
    (
        "in-place edit of sensitive credential/SSH/shell-rc path (long flag)",
        rf"{_SED_IN_PLACE}(?:{_USER_TARGET}){_REST_OF_NAME}",
    ),
    (
        "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)",
        rf"{_SCRIPT_I}(?:{_USER_TARGET}){_REST_OF_NAME}",
    ),
    ("in-place edit of system config", _SED_I + r"\s" + _SYSTEM_CONFIG),
    ("in-place edit of system config (long flag)", _SED_IN_PLACE + r"\s" + _SYSTEM_CONFIG),
    ("in-place edit of Atlas settings or vault", _SED_I + _ATLAS_FILES),
    ("in-place edit of Atlas settings or vault (long flag)", _SED_IN_PLACE + _ATLAS_FILES),
    ("in-place edit of Atlas settings or vault (perl/ruby)", _SCRIPT_I + _ATLAS_FILES),
    # A shell reading a heredoc runs it without any -c. Interpreter heredocs are read by
    # _program_findings.
    ("shell execution via heredoc", rf"\b{_A_SHELL}\s+<<"),
    # -- Git. --hard is the only reset mode starting with h, and git answers --help before it
    # looks at the mode, so every prefix of --hard counts.
    ("git reset --hard (destroys uncommitted changes)", r"\bgit\s+reset\s+--h(?:a(?:r(?:d)?)?)?\b"),
    ("git force push (rewrites remote history)", r"\bgit\s+push\b.*--forc[a-z]*\b"),
    ("git force push short flag (rewrites remote history)", r"\bgit\s+push\b.*-f\b"),
    ("git clean with force (deletes untracked files)", r"\bgit\s+clean\s+-\S*f"),
    # Only capital -D forces; -d deletes merged branches only, and git refuses the rest, so it
    # passes. The long spellings are matched in either order within one command.
    ("git branch force delete", r"\bgit\s+branch\s+(?-i:-D)\b"),
    (
        "git branch force delete (long flags)",
        rf"{_GIT_BRANCH}{_ONE_COMMAND}(?:-d\b|--delete\b){_ONE_COMMAND}(?:-f\b|--force\b)",
    ),
    (
        "git branch force delete (long flags, force-first)",
        rf"{_GIT_BRANCH}{_ONE_COMMAND}(?:-f\b|--force\b){_ONE_COMMAND}(?:-d\b|--delete\b)",
    ),
    # A script made executable and run at once: whatever it holds was never read by a rule.
    ("chmod +x followed by immediate execution", r"\bchmod\s+\+x\b.*[;&|]+\s*\./"),
    # -- sudo without a terminal. Atlas has no TTY, so sudo succeeds only by reading a password
    # from stdin (-S) or an askpass helper (-A); a shell (-s) or a privilege listing (-l) is a
    # step in the same chain. Case is folded, so S/s and A/a are one. sudo accepts unambiguous
    # prefixes of long options: --st means --stdin, --a means --askpass.
    (
        "sudo with privilege flag (stdin/askpass/shell/list)",
        r"\bsudo\b[^;|&\n]*?\s+(?:-s\b|--st[a-z]*\b|-a\b|--a[a-z]*\b)",
    ),
    ("sudo with combined-flag privilege escalation", r"\bsudo\b[^;|&\n]*?\s+-[a-z]*[sa][a-z]*\b"),
    # -- Removing installed software, which can reach outside the project (`npm uninstall -g`).
    # Installs and updates pass. Each starts at a command position, so a commit message that
    # mentions an uninstall is data.
    _uninstall("npm", "(?:uninstall|unlink|remove|rm|r|un)"),
    _uninstall("pnpm", "(?:uninstall|remove|rm|un)"),
    _uninstall("yarn", r"(?:global\s+)?(?:uninstall|remove)"),
    _uninstall("pip(?:3)?", "uninstall"),
    _uninstall("brew", "(?:uninstall|remove|rm)"),
)
# Glob and brace characters are plain data inside quotes, so these read quoted text blanked.
_DANGEROUS_ON_BLANKED_QUOTES = frozenset({_FIND_GLOB, _TOOL_OPTION_GLOB})

# The tables as (pattern, description) pairs, for readers outside this module.
HARDLINE_PATTERNS = [(pattern, description) for description, pattern in _HARDLINE_TABLE]
DANGEROUS_PATTERNS = [(pattern, description) for description, pattern in _DANGEROUS_TABLE]


class _Rule(NamedTuple):
    regex: re.Pattern[str]
    description: str
    on_blanked_quotes: bool


def _compile(
    table: Iterable[tuple[str, str]], on_blanked_quotes: frozenset[str]
) -> tuple[_Rule, ...]:
    # Compiled once at import: the matcher runs on every command and must not depend on re's
    # small cache, which other code evicts.
    return tuple(
        _Rule(
            re.compile(pattern, re.IGNORECASE | re.DOTALL),
            description,
            description in on_blanked_quotes,
        )
        for description, pattern in table
    )


_HARDLINE_RULES = _compile(_HARDLINE_TABLE, _HARDLINE_ON_BLANKED_QUOTES)
_DANGEROUS_RULES = _compile(_DANGEROUS_TABLE, _DANGEROUS_ON_BLANKED_QUOTES)

_PARSER_LIMIT = "command parser limit exceeded"
_MALFORMED = "command parser limit or malformed executable payload"
PARSER_LIMIT_DESCRIPTIONS = frozenset({_PARSER_LIMIT, _MALFORMED})
_SUDO_STDIN = "sudo password guessing via stdin (sudo -S)"
_SUDO_S = re.compile(r"(?:^|[;&|`\n]|&&|\|\||\$\()\s*sudo\s+-S\b", re.IGNORECASE)

# ==== Programs, wrappers and their options ========================================================

# Commands that run the next word as the program, and the options each takes a value for (the
# value is data, not a program). Option spelling keeps its case.
_WRAPPERS = frozenset(
    [
        "sudo",
        "env",
        "exec",
        "nohup",
        "setsid",
        "time",
        "command",
        "builtin",
        "nice",
        "timeout",
        "stdbuf",
        "ionice",
        "chrt",
        "taskset",
        "chroot",
    ]
)
_WRAPPER_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "sudo": frozenset(
        ["-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-c", "--close-from"]
    ),
    "env": frozenset(["-u", "--unset", "-C", "--chdir", "-S", "--split-string", "-a", "--argv0"]),
    "exec": frozenset(["-a"]),
    "nice": frozenset(["-n", "--adjustment"]),
    "time": frozenset(["-o", "--output", "-f", "--format"]),
    "timeout": frozenset(["-s", "--signal", "-k", "--kill-after"]),
    "stdbuf": frozenset(["-i", "--input", "-o", "--output", "-e", "--error"]),
    "ionice": frozenset(["-n", "--classdata", "-c", "--class"]),
    "chroot": frozenset(["--userspec", "--groups"]),
}
# Options that make the wrapper look something up instead of running a program.
_WRAPPER_LOOKUP_OPTIONS: dict[str, frozenset[str]] = {
    "command": frozenset(["-v", "-V"]),
    "taskset": frozenset(["-p", "--pid"]),
    "chrt": frozenset(["-p", "--pid"]),
    "ionice": frozenset(["-p", "--pid", "--pgid", "--uid"]),
}
# Positional operands before the program (timeout's duration, chroot's new root).
_WRAPPER_OPERANDS = {"timeout": 1, "chroot": 1, "chrt": 1, "taskset": 1}
# Words after which the next word is a command again.
_LEADS_TO_COMMAND = frozenset(["!", "if", "then", "elif", "else", "while", "until", "do"])
_REDIRECTION = re.compile(r"(?:[0-9]+)?(?:>>|<<|<>|>&|<&|>\||[<>])")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
# Commands whose quoted argument is code another shell runs.
_RUNS_ITS_ARGUMENT = frozenset(["eval", "source", ".", *_SHELL_NAMES])

# Interpreters, by family; the program's base name must match whole.
_INTERPRETER = re.compile(
    r"(?P<python>py(?:\.exe)?|python[23]?(?:\.\d+)*(?:\.exe)?)"
    r"|(?P<node>node(?:js)?(?:\.exe)?)"
    r"|(?P<perl>perl[0-9]*(?:\.\d+)*(?:\.exe)?)"
    r"|(?P<ruby>ruby[0-9.]*(?:\.exe)?)"
    r"|(?P<php>php(?:\.exe)?)"
    r"|(?P<powershell>powershell(?:\.exe)?|pwsh(?:\.exe)?)"
    r"|(?P<bun>bun(?:\.exe)?)"
    r"|(?P<deno>deno(?:\.exe)?)"
)
# Options that make an interpreter run code given on the command line. Deno's is the bare `eval`
# subcommand, read separately.
_INLINE_CODE_OPTIONS: dict[str, frozenset[str]] = {
    "python": frozenset(["-c"]),
    "node": frozenset(["-p", "--print", "-e", "--eval"]),
    "perl": frozenset(["-e", "--eval"]),
    "ruby": frozenset(["-e"]),
    "php": frozenset(["-r"]),
    "powershell": frozenset(["-c", "-command", "-f", "-file"]),
    "bun": frozenset(["-e", "--eval"]),
    "deno": frozenset(["-e", "--eval", "eval"]),
}
# Interpreter options that take the next word as their value.
_INTERPRETER_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "python": frozenset(["-X", "-W", "--check-hash-based-pycs"]),
    "node": frozenset(
        [
            "--require",
            "--import",
            "--loader",
            "-C",
            "--conditions",
            "--title",
            "--openssl-config",
            "--icu-data-dir",
            "--diagnostic-dir",
            "--cpu-prof-dir",
        ]
    ),
    "perl": frozenset(["-I", "-M", "-m", "-F", "-0", "-x"]),
    "ruby": frozenset(["-I", "-r", "-C", "-E", "-K", "-F"]),
    "php": frozenset(["-d", "-c", "-z"]),
    "powershell": frozenset(
        [
            "-executionpolicy",
            "-version",
            "-workingdirectory",
            "-windowstyle",
            "-inputformat",
            "-outputformat",
            "-configurationname",
            "-custompipename",
            "-settingsfile",
        ]
    ),
    "bun": frozenset(["--cwd", "--config", "--env-file", "--preload", "--require"]),
    # Only -L/--log-level may come before deno's `eval` with a separate value.
    "deno": frozenset(["-L", "--log-level"]),
}
# Read-only tools with an option that runs a program of the caller's choosing.
_PROGRAM_OPTIONS: dict[str, frozenset[str]] = {
    "rg": frozenset(["--pre", "--hostname-bin"]),
    "sort": frozenset(["--compress-program"]),
    "ag": frozenset(["--pager"]),
    "man": frozenset(["-P", "-H", "--pager", "--html"]),
}
# Their other options with a required value, as ripgrep 14, GNU sort, man-db and ag 2.2 define
# them. The value belongs to the option even when it looks like another option.
_TOOL_LONG_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "rg": frozenset(
        [
            "--regexp",
            "--file",
            "--pre",
            "--pre-glob",
            "--replace",
            "--glob",
            "--iglob",
            "--ignore-file",
            "--type",
            "--type-add",
            "--type-clear",
            "--type-not",
            "--sort",
            "--sortr",
            "--max-count",
            "--max-depth",
            "--max-filesize",
            "--max-columns",
            "--after-context",
            "--before-context",
            "--context",
            "--context-separator",
            "--field-context-separator",
            "--field-match-separator",
            "--path-separator",
            "--color",
            "--colors",
            "--encoding",
            "--engine",
            "--threads",
            "--dfa-size-limit",
            "--regex-size-limit",
            "--generate",
            "--hostname-bin",
            "--hyperlink-format",
        ]
    ),
    "sort": frozenset(
        [
            "--key",
            "--field-separator",
            "--output",
            "--sort",
            "--compress-program",
            "--buffer-size",
            "--batch-size",
            "--parallel",
            "--temporary-directory",
            "--random-source",
            "--files0-from",
        ]
    ),
    "man": frozenset(
        [
            "--pager",
            "--preprocessor",
            "--prompt",
            "--sections",
            "--systems",
            "--manpath",
            "--config-file",
            "--extension",
            "--locale",
            "--encoding",
            "--recode",
        ]
    ),
    "ag": frozenset(
        [
            "--pager",
            "--depth",
            "--max-count",
            "--width",
            "--workers",
            "--ignore",
            "--ignore-dir",
            "--path-to-ignore",
            "--filename-pattern",
            "--file-search-regex",
            "--ackmate-dir-filter",
            "--color-line-number",
            "--color-match",
            "--color-path",
        ]
    ),
}
_TOOL_SHORT_VALUE_OPTIONS = {
    "rg": frozenset("efEmjgdtTABCMr"),
    "sort": frozenset("koStT"),
    "man": frozenset("CRLmMSserEPp"),
    "ag": frozenset("gGmpW"),
}
_GREP_LONG_VALUE_OPTIONS = frozenset(
    [
        "--regexp",
        "--file",
        "--include",
        "--exclude",
        "--exclude-dir",
        "--exclude-from",
        "--after-context",
        "--before-context",
        "--context",
        "--max-count",
        "--label",
        "--binary-files",
        "--directories",
        "--devices",
    ]
)
_GREP_SHORT_VALUE_OPTIONS = frozenset("efABCDdm")
_BASH_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})
_BASH_OPTION_LETTERS = frozenset("abcefhiklmnprstuvxBCDEHOPTo")
# GNU env -S escapes, with the character each stands for.
_ENV_S_ESCAPES = {
    "\\": "\\",
    "'": "'",
    '"': '"',
    "$": "$",
    "#": "#",
    "_": " ",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}

# Inputs past these sizes are refused before any parsing (see _too_big_to_parse).
_MAX_CHARS = 128_000
_MAX_CHARS_WITHOUT_SEPARATOR = 4_096
_MAX_SEPARATORS = 25_000
_SEPARATORS = ";&|\n"


def _too_big_to_parse(command: str) -> bool:
    """Whether *command* is past what the parsers may spend time on.

    Separators are counted even inside quotes, which can only refuse more: a refusal is safe, a
    suffix nobody inspected is not. A long command with no separator has no use as a compound
    command and would make every pattern scan one giant word.
    """
    if len(command) > _MAX_CHARS:
        return True
    separators = sum(map(command.count, _SEPARATORS))
    if separators == 0:
        return len(command) > _MAX_CHARS_WITHOUT_SEPARATOR
    return separators >= _MAX_SEPARATORS


# ==== One spelling ================================================================================

_LINE_CONTINUATION = re.compile(r"\\\r?\n")
_BACKSLASHED = re.compile(r"\\([^\n])")
_EMPTY_QUOTES = re.compile(r"''|\"\"")
_IFS = re.compile(r"\$\{IFS\b[^}]*\}|\$IFS\b")


def _plain_spelling(command: str) -> str:
    """Bring *command* to one spelling, so look-alike forms meet the same rules.

    Terminal codes and NUL bytes go and Unicode is NFKC-folded (fullwidth letters become plain
    ones). A backslash-newline is joined first: the escape step below skips newlines, so it
    would leave the backslash between `rm -rf` and `/`. The user's home is folded before escapes
    go, or `C:\\Users\\name` would turn into `C:Usersname`. Then backslash escapes (`r\\m`) and
    empty quotes (`r''m`) go, and `$IFS` or `${IFS...}` becomes a blank, as the shell splits it.
    """
    text = unicodedata.normalize("NFKC", strip_ansi(command).replace("\x00", ""))
    text = _LINE_CONTINUATION.sub("", text)
    text = _fold_user_home(text)
    text = _BACKSLASHED.sub(r"\1", text)
    text = _EMPTY_QUOTES.sub("", text)
    return _IFS.sub(" ", text)


_NON_BLANK = re.compile(r"\S+")


def _lowercase_but_flags(text: str) -> str:
    """Lowercase *text* except the words that start with `-`.

    Every rule ignores case, so a flag's case matters only to a rule that asks for it in a
    case-exact group (`git branch -D`).
    """
    return _NON_BLANK.sub(
        lambda word: word[0] if word[0].startswith("-") else word[0].lower(), text
    )


# Characters that end a path in a command: blanks, quotes and shell punctuation.
_PATH_STOPS = r"""\s'"`;|&<>()"""
_PATH_REST = r"(?P<rest>(?:[/\\][^/\\" + _PATH_STOPS + r"]*)+)"


@functools.lru_cache(maxsize=64)
def _home_matcher(home: str) -> re.Pattern[str] | None:
    """A pattern for *home* as the start of a longer path, or None for a home too short to fold.

    Parts may be joined by either slash, so Windows, forward-slash and mixed spellings all match.
    A path must go on past the home: the home alone is never folded. A home of fewer than two
    parts (`/`, `C:\\`, empty) would rewrite unrelated paths and gives None.
    """
    parts = [part for part in re.split(r"[/\\]+", home) if part]
    if len(parts) < 2:
        return None
    return re.compile(r"[/\\]*" + r"[/\\]+".join(map(re.escape, parts)) + _PATH_REST)


def _fold_homes(text: str, homes: Iterable[str], spelling: str) -> str:
    """Write each of *homes* inside *text* as *spelling*, the rest of the path with `/`.

    The longest home goes first, so a home inside another is not folded halfway.
    """
    for home in dict.fromkeys(sorted(filter(None, homes), key=len, reverse=True)):
        matcher = _home_matcher(home)
        if matcher is not None:
            text = matcher.sub(lambda found: spelling + found["rest"].replace("\\", "/"), text)
    return text


def _fold_user_home(text: str) -> str:
    """Write the user's absolute home, as given and resolved, as `~`."""
    try:
        home = os.path.expanduser("~")
        homes = (home, os.path.realpath(home))
    except (OSError, RuntimeError, ValueError):
        return text
    return _fold_homes(text, homes, "~")


def fold_atlas_home(command: str, home: str) -> str:
    """Write an absolute ATLAS_HOME inside *command* as `~/.atlas`.

    The vault and settings rules then hold for a home kept anywhere, such as `/data` in a
    container.
    """
    return _fold_homes(command, [home, os.path.realpath(home)], "~/.atlas")


# ==== Reading shell text ==========================================================================

# Kinds of step _lex yields.
_PLAIN = "plain"  # one character
_ESCAPED = "escaped"  # a backslash and the character after it
_QUOTE = "quote"  # a quote that opens or closes
_EXPANSION = "expansion"  # a whole `$(...)`, backtick or `${...}`
_COMMENT = "comment"  # a comment, up to its newline

_Step = tuple[str, int, int, str | None]
_CLOSED_BACKTICK = re.compile(r"(?:[^`\\]|\\.)*`", re.DOTALL)
_OPEN_BACKTICK = re.compile(r"(?<!\\)`")


def _lex(
    text: str,
    begin: int = 0,
    limit: int | None = None,
    *,
    bare_expansions: bool = False,
    quoted_expansions: bool = False,
    braces: bool = False,
    stop_at_open: bool = False,
    loose_backticks: bool = False,
    comments: bool = False,
) -> Iterator[_Step]:
    """Walk ``text[begin:limit]`` the way the shell reads quotes, without expanding anything.

    Every reader below is built on this one walk. Each step is ``(kind, at, after, quote)``:
    *quote* is the state it was read in (None, `'` or `"`), and a backslash escapes nothing
    inside single quotes.

    Expansions are taken whole only where asked: outside quotes with *bare_expansions*, inside
    double quotes with *quoted_expansions*; *braces* adds `${...}` outside quotes. One that
    never closes is read as plain characters, unless *stop_at_open*: then the walk yields
    ``(_EXPANSION, at, -1, quote)`` and ends there. *loose_backticks* closes a backtick at the
    next backtick even when escaped. *comments* takes a comment as one step and ignores the
    quotes inside it.
    """
    end = len(text) if limit is None else limit
    quote: str | None = None
    at = begin
    while at < end:
        char = text[at]
        kind, after = _PLAIN, at + 1
        if comments and quote is None and _opens_comment(text, at):
            kind, after = _COMMENT, text.find("\n", at, end)
            if after < 0:
                after = end
        elif char == "\\" and quote != "'" and at + 1 < end:
            kind, after = _ESCAPED, at + 2
        elif char == quote or (quote is None and char in "'\""):
            kind = _QUOTE
        elif quote != "'" and (quoted_expansions if quote else bare_expansions):
            close: int | None
            if char == "`":
                if loose_backticks:
                    close = text.find("`", at + 1) + 1 or None
                else:
                    close = _backtick_close(text, at)
            elif text.startswith("$(", at):
                close = _paren_close(text, at)
            elif braces and quote is None and text.startswith("${", at):
                close = text.find("}", at + 2) + 1 or None
            else:
                close = 0
            if close:
                kind, after = _EXPANSION, close
            elif close is None and stop_at_open:
                yield (_EXPANSION, at, -1, quote)
                return
        yield (kind, at, after, quote)
        if kind == _QUOTE:
            quote = None if quote else char
        at = after


def _paren_close(text: str, opener: int) -> int | None:
    """The offset just past the `)` that closes the `$(` at *opener*, or None.

    One walk answers for every `$(` it passes outside quotes too: a walk from there would read
    the same steps. Asked again for each command of a text, a `$(` that never closes was read to
    the end each time, and a few hundred of them held the server for seconds.
    """
    closes = _paren_closes(text)
    if opener not in closes:
        openers = [opener]
        for kind, at, _, quote in _lex(text, opener + 2):
            if kind != _PLAIN or quote:
                continue
            if text.startswith("$(", at):
                openers.append(at)
            elif text[at] == ")":
                closes[openers.pop()] = at + 1
                if not openers:
                    break
        closes.update(dict.fromkeys(openers))
    return closes[opener]


@functools.lru_cache(maxsize=64)
def _paren_closes(text: str) -> dict[int, int | None]:
    """The closes `_paren_close` has found in *text*, by opener."""
    return {}


def _backtick_close(text: str, opener: int) -> int | None:
    """The offset just past the backtick closing the one at *opener*, or None.

    Inside backticks quotes mean nothing; only a backslash escapes the next character.
    """
    found = _CLOSED_BACKTICK.match(text, opener + 1)
    return found.end() if found else None


def _opens_comment(text: str, at: int) -> bool:
    return text[at] == "#" and (at == 0 or text[at - 1].isspace() or text[at - 1] in ";&|()<>")


def _skip_blanks(text: str, at: int) -> int:
    while at < len(text) and text[at].isspace():
        at += 1
    return at


def _read_word(text: str, at: int) -> tuple[int, int, str]:
    """The shell word at or after *at*, as ``(start, end, word)``; expansions are kept whole."""
    first = last = _skip_blanks(text, at)
    for kind, i, j, quote in _lex(text, first, bare_expansions=True, braces=True):
        if kind == _PLAIN and quote is None and (text[i].isspace() or text[i] in ";&|<>()"):
            break
        last = j
    return first, last, text[first:last]


def _top_level_segments(text: str) -> Iterator[str]:
    """The parts of *text* between unquoted separators and comments, in order."""
    begin = 0
    for kind, at, after, quote in _lex(text, comments=True):
        if kind == _COMMENT or (kind == _PLAIN and quote is None and text[at] in _SEPARATORS):
            if begin < at:
                yield text[begin:at]
            begin = after
    if begin < len(text):
        yield text[begin:]


def _command_segment(text: str, start: int) -> str:
    """The simple command beginning at *start*, quoted bytes intact, trimmed."""
    for kind, at, _, quote in _lex(
        text, start, bare_expansions=True, quoted_expansions=True, braces=True, comments=True
    ):
        if kind == _COMMENT or (kind == _PLAIN and quote is None and text[at] in ";&|\n)`"):
            return text[start:at].strip()
    return text[start:].strip()


def _splice(text: str, edits: Iterable[tuple[int, int, str]]) -> str:
    """Replace ``text[start:end]`` with each edit's text, in one pass over sorted edits."""
    pieces: list[str] = []
    done = 0
    for start, end, insert in edits:
        pieces += (text[done:start], insert)
        done = end
    pieces.append(text[done:])
    return "".join(pieces)


def _gather_starts(
    text: str, begin: int, end: int, found: list[int], read: set[tuple[int, int]] | None = None
) -> None:
    """Add to *found* every offset in ``text[begin:end]`` where the shell starts a command.

    *read* holds the stretches already gathered: a `$(` inside a backtick can close past the
    backtick's end, so stretches overlap, and read again each time they made a program's bytes
    take minutes. A stretch read once has added its starts already.
    """
    read = set() if read is None else read
    if (begin, end) in read:
        return
    read.add((begin, end))
    paired = -1  # the second character of a `&&` or `||` just seen
    for kind, at, after, quote in _lex(
        text,
        begin,
        end,
        bare_expansions=True,
        quoted_expansions=True,
        stop_at_open=True,
        comments=True,
    ):
        if kind == _EXPANSION:
            # A substitution is a command of its own; an unclosed one runs to the end.
            body = at + (1 if text[at] == "`" else 2)
            found.append(body)
            _gather_starts(text, body, end if after < 0 else after - 1, found, read)
            continue
        if kind != _PLAIN or quote is not None or at == paired:
            continue
        char = text[at]
        # `{` opens a group only as a word of its own: inside `${IFS}` or `-{delete,print}` a
        # start would split the word the patterns need whole.
        if char in "(;\n" or (
            char == "{" and (at == 0 or text[at - 1].isspace() or text[at - 1] in "(;&|)")
        ):
            found.append(at + 1)
        elif char in "&|":
            if at + 1 < end and text[at + 1] == char:
                paired = at + 1
                found.append(at + 2)
            else:
                found.append(at + 1)


def _command_starts(text: str) -> Iterator[int]:
    """Each offset where a command starts, quote-aware, once each."""
    queue = [0]
    _gather_starts(text, 0, len(text), queue)
    visited: set[int] = set()
    k = 0
    while k < len(queue):
        at = _skip_blanks(text, queue[k])
        k += 1
        if at >= len(text) or at in visited or _opens_comment(text, at):
            continue
        visited.add(at)
        yield at
        _, after, word = _read_word(text, at)
        if word in _LEADS_TO_COMMAND:
            queue.append(after)


def _program_words_from(text: str, at: int) -> Iterator[tuple[int, int, str]]:
    """The words from *at* that may name a program: the first, and the one after each wrapper."""
    wrapper: str | None = None
    operands = 0
    reading_options = True
    skip_next = False
    while at < len(text):
        blanks_end = _skip_blanks(text, at)
        if "\n" in text[at:blanks_end]:
            # An unquoted newline ends the command, and the next line is a start of its own.
            # Read on past it, a script of `x=...` lines was read to its end from every line.
            return
        redirect = _REDIRECTION.match(text, blanks_end)
        if redirect:
            at = _read_word(text, redirect.end())[1]
            continue
        first, last, word = _read_word(text, at)
        if first == last:
            return
        at = last
        plain = _spelled_out(word)
        if skip_next:
            skip_next = False
            continue
        if wrapper and reading_options and plain == "--":
            reading_options = False
            continue
        if wrapper and reading_options and plain.startswith("-"):
            option = plain.split("=", 1)[0]
            if wrapper == "env" and (option == "--split-string" or plain.startswith("-S")):
                # The split string and what follows are one command, read by _env_s_command.
                return
            looks_up = option in _WRAPPER_LOOKUP_OPTIONS.get(wrapper, ())
            if wrapper == "command" and not option.startswith("--"):
                # `command -v`, `-V` and any bundle holding them print where a name resolves.
                looks_up = looks_up or not {"v", "V"}.isdisjoint(option[1:])
            if looks_up:
                return
            skip_next = "=" not in plain and option in _WRAPPER_VALUE_OPTIONS.get(wrapper, ())
            continue
        if operands:
            operands -= 1
            continue
        if _ASSIGNMENT.fullmatch(word):
            continue
        yield first, last, word
        name = os.path.basename(plain).lower()
        if name not in _WRAPPERS:
            return
        wrapper, reading_options, operands = name, True, _WRAPPER_OPERANDS.get(name, 0)


def _program_words(text: str) -> Iterator[tuple[int, int, str]]:
    """``(start, end, word)`` for every word of *text* in a program's place."""
    for at in _command_starts(text):
        yield from _program_words_from(text, at)


def _with_start_marks(text: str, mark: str = "\n") -> str:
    """Put *mark* before every command start the quote-aware reader finds.

    A newline is a command position for the rules, so this shows them commands opened by `(`,
    `{`, `;`, `&` or `|` without treating those characters as starts inside quotes, where they
    are data: `--title "block (reboot)"` is left as it is.
    """
    cuts = sorted(at for at in _command_starts(text) if at > 0)
    if not cuts:
        return text
    return _splice(text, [(at, at, mark) for at in cuts])


def _unwrap_quoted_newlines(text: str) -> str:
    """Turn the newlines inside quotes into blanks.

    A quoted newline is data, yet a rule treats every newline as a command start, so a
    multi-line commit message with a line starting `sudo reboot` would be refused. Newlines
    outside quotes stay. A quote that never closes swallows the rest, as it does in the shell.
    """
    if "\n" not in text:
        return text
    return _unwrap_newlines_in(text, 0, len(text))


def _unwrap_newlines_in(text: str, begin: int, end: int) -> str:
    # A `$(...)` or backtick inside double quotes runs: its body is read again with fresh quotes,
    # so a newline between two of its commands stays a separator.
    pieces: list[str] = []
    for kind, at, after, quote in _lex(text, begin, end, quoted_expansions=True):
        if kind == _EXPANSION:
            body = at + (2 if text.startswith("$(", at) else 1)
            pieces += (
                text[at:body],
                _unwrap_newlines_in(text, body, after - 1),
                text[after - 1 : after],
            )
        elif quote and kind == _PLAIN and text[at] == "\n":
            pieces.append(" ")
        else:
            pieces.append(text[at:after])
    return "".join(pieces)


def _blank_quoted_text(text: str) -> str:
    """Replace what is inside quotes with blanks, keeping the quotes themselves.

    A `$(...)` or backtick inside double quotes is kept: the shell runs it. A quote that never
    closes blanks the rest, which hides nothing the shell would run.
    """
    return "".join(
        text[at:after] if quote is None or kind in (_QUOTE, _EXPANSION) else " " * (after - at)
        for kind, at, after, quote in _lex(text, quoted_expansions=True, loose_backticks=True)
    )


def _hands_quotes_to_a_shell(text: str) -> bool:
    """Whether a program word of *text* is a shell or eval, whose quoted argument is code."""
    return any(
        os.path.basename(_spelled_out(word)).lower() in _RUNS_ITS_ARGUMENT
        for _, _, word in _program_words(text)
    )


# ==== Spelling out a command word =================================================================

_ECHO_SWITCHES = re.compile(r"-[nEe]+")
_BARE_LITERAL = re.compile(r"[A-Za-z0-9_./:@%+=,-]+")
_PARAMETER_SUBSTITUTE = re.compile(r"\$\{[^}/\s]+/[^}/]*/([^}]*)\}")
_PARAMETER_FALLBACK = re.compile(r"\$\{[^}:\s]+:-([^}]*)\}")


def _echoed_literal(script: str) -> str | None:
    """What `echo WORD` or `printf [%s] WORD` would print, for a plain WORD; None otherwise."""
    try:
        words = shlex.split(script, posix=True)
    except ValueError:
        return None
    if not words:
        return None
    program, args = words[0].lower(), words[1:]
    if program == "echo":
        while args and _ECHO_SWITCHES.fullmatch(args[0]):
            args = args[1:]
    elif program != "printf":
        return None
    if len(args) == 1 and _BARE_LITERAL.fullmatch(args[0]):
        return args[0]
    if (
        program == "printf"
        and len(args) == 2
        and args[0] == "%s"
        and _BARE_LITERAL.fullmatch(args[1])
    ):
        return args[1]
    return None


def _inline_substitutions(word: str) -> str:
    """Replace each `$(echo x)` or backtick form in *word* with the literal it prints."""
    out: list[str] = []
    at = 0
    while at < len(word):
        if word.startswith("$(", at):
            width, close = 2, _paren_close(word, at)
        elif word[at] == "`":
            width, close = 1, _backtick_close(word, at)
        else:
            width, close = 0, None
        printed = None if close is None else _echoed_literal(word[at + width : close - 1])
        if printed is None or close is None:
            out.append(word[at])
            at += 1
        else:
            out.append(printed)
            at = close
    return "".join(out)


def _drop_quoting(word: str) -> str:
    return "".join(
        word[at + 1] if kind == _ESCAPED else word[at]
        for kind, at, _, _ in _lex(word)
        if kind != _QUOTE
    )


def _spelled_out(word: str) -> str:
    """How the shell may spell a program word, without running anything.

    Quotes, escapes, literal `$(echo ...)` and `${x/a/b}` or `${x:-b}` forms are undone, at most
    twice. Kept narrow on purpose: it is applied to program words only.
    """
    for _ in range(2):
        plain = _inline_substitutions(word)
        plain = _PARAMETER_SUBSTITUTE.sub(lambda found: found[1], plain)
        plain = _drop_quoting(_PARAMETER_FALLBACK.sub(lambda found: found[1], plain))
        if plain == word:
            break
        word = plain
    return word


# ==== grep patterns ===============================================================================


def _simple_command_end(segment: str, start: int) -> int:
    """Where the simple command at *start* ends: an unquoted `;`, `|`, `&` or newline, or the `)`
    or backtick that closes the substitution it sits in."""
    nesting = 0  # `$(` opened after start
    inner_backtick = False  # a backtick opened after start, like `grep -e `cmd` f`
    for kind, at, _, quote in _lex(segment, start):
        if kind != _PLAIN or quote:
            continue
        char = segment[at]
        if segment.startswith("$(", at):
            nesting += 1
        elif char == "`":
            if inner_backtick:
                inner_backtick = False
            elif nesting == 0 and not _OPEN_BACKTICK.search(segment, at + 1):
                return at
            else:
                inner_backtick = True
        elif char == ")":
            if nesting == 0:
                return at
            nesting -= 1
        elif char in _SEPARATORS:
            return at
    return len(segment)


def _shell_tokens_with_spans(segment: str, start: int) -> list[tuple[str, int, int, bool]] | None:
    """The words of the simple command at *start*, as ``(value, start, end, single_quoted)``.

    None when its quoting is broken: a quote left open or a trailing backslash. A small reader
    that keeps each word's place in the text, which shlex does not, so a quoted grep pattern can
    be found and blanked. The command ends where _simple_command_end says, so a grep inside
    `"$(grep ... | cut ...)"` is not read together with the outer command's closing quote.
    """
    stop = _simple_command_end(segment, start)
    words: list[tuple[str, int, int, bool]] = []
    chars: list[str] = []
    word_at: int | None = None
    quote: str | None = None
    for kind, at, _, _ in _lex(segment, start, stop):
        char = segment[at]
        if kind == _PLAIN and not quote and char.isspace():
            if word_at is not None:
                words.append(_finished_word(segment, chars, word_at, at))
                chars, word_at = [], None
            continue
        if word_at is None:
            word_at = at
        if kind == _QUOTE:
            quote = None if quote else char
        elif kind == _ESCAPED:
            chars.append(segment[at + 1])
        elif char == "\\" and not quote:
            return None
        else:
            chars.append(char)
    if quote:
        return None
    if word_at is not None:
        words.append(_finished_word(segment, chars, word_at, stop))
    return words


def _finished_word(
    segment: str, chars: list[str], start: int, end: int
) -> tuple[str, int, int, bool]:
    # Only a word in single quotes is inert: double quotes still run `$(...)` and backticks.
    raw = segment[start:end]
    return ("".join(chars), start, end, raw.endswith("'") and (raw.startswith("'") or "='" in raw))


_Spans = list[tuple[int, int]]


def _grep_pattern_words(args: list[tuple[str, int, int, bool]]) -> tuple[bool, list[int] | None]:
    """Read grep's arguments: ``(perl_regexp, indexes of the pattern words)``.

    The indexes are None when the parse gives up, and the first value then says whether to fail
    closed: an option missing its value, or a -P grep with no pattern at all.
    """
    perl = explicit = False
    picks: list[int] = []
    first_operand: int | None = None
    in_options = True
    k = 0
    while k < len(args):
        token = args[k][0]
        has_next = k + 1 < len(args)
        if not in_options or token == "-" or not token.startswith("-"):
            if first_operand is None:
                first_operand = k
        elif token == "--":
            in_options = False
        elif token.startswith("--"):
            name, equals, _ = token.partition("=")
            perl = perl or name == "--perl-regexp"
            explicit = explicit or name in ("--regexp", "--file")
            needs_value = not equals and name in _GREP_LONG_VALUE_OPTIONS
            if needs_value and not has_next:
                return True, None
            if name == "--regexp":
                picks.append(k + 1 if needs_value else k)
            if needs_value:
                k += 1
        else:
            bundle = token[1:]
            for n, letter in enumerate(bundle):
                perl = perl or letter == "P"
                explicit = explicit or letter in ("e", "f")
                if letter in _GREP_SHORT_VALUE_OPTIONS:
                    # This option takes the rest of the bundle, or the next word when it is last.
                    glued = n + 1 < len(bundle)
                    if not glued and not has_next:
                        return True, None
                    if letter == "e":
                        picks.append(k if glued else k + 1)
                    if not glued:
                        k += 1
                    break
        k += 1
    if not explicit:
        if first_operand is None:
            return perl, None
        picks.append(first_operand)
    return perl, picks


def _quoted_grep_pattern_spans(text: str) -> tuple[_Spans, bool]:
    """Where the single-quoted patterns of `grep -P` are, and whether the parse failed.

    A Perl pattern is full of shell-looking text (`(?=...)`, `|`, `;`), so it is blanked before
    the rules read the command. When any grep cannot be read for sure, nothing is blanked and
    the caller decides on the command as written.
    """
    spans: _Spans = []
    cursor = 0
    for piece in _top_level_segments(text):
        base = text.find(piece, cursor)
        cursor = base + len(piece)
        for at, _, word in _program_words(piece):
            if os.path.basename(_spelled_out(word)).lower() not in ("grep", "egrep"):
                continue
            words = _shell_tokens_with_spans(piece, at)
            if words is None:
                return [], True
            perl, picks = _grep_pattern_words(words[1:])
            if picks is None:
                return [], perl
            if perl:
                for pick in picks:
                    _, begin, end, single_quoted = words[1 + pick]
                    if single_quoted:
                        spans.append((base + begin, base + end))
    return spans, False


def _hide_grep_patterns(command: str) -> tuple[str, bool]:
    """*command* with its quoted grep -P patterns blanked, and whether grep parsing failed."""
    spans, failed = _quoted_grep_pattern_spans(command)
    if failed or not spans:
        return command, failed
    return _splice(command, [(begin, end, " " * (end - begin)) for begin, end in spans]), False


# ==== Code handed to another program ==============================================================


def _argv(segment: str, start: int) -> list[str] | None:
    """shlex words of ``segment[start:]``, or None when its quoting is broken."""
    try:
        words = shlex.shlex(instream=segment[start:], punctuation_chars="<>", posix=True)
        words.commenters, words.whitespace_split = "", True
        return list(words)
    except ValueError:
        return None


def _interpreter_of(program: str) -> str | None:
    found = _INTERPRETER.fullmatch(os.path.basename(program).lower())
    return found.lastgroup if found else None


def _runs_inline_code(family: str, args: list[str]) -> bool:
    """Whether an interpreter's options make it run code given on the command line."""
    code_options = _INLINE_CODE_OPTIONS[family]
    value_options = _INTERPRETER_VALUE_OPTIONS[family]
    # PowerShell's options are case-blind, and its long options take one dash, so a word is never
    # a bundle of letters there. Elsewhere options come first and end at the first plain word.
    windows = family == "powershell"
    glued_value_options = [o for o in value_options if o.startswith("-") and not o.startswith("--")]
    skip_value = False
    for token in args:
        if skip_value:
            skip_value = False
            continue
        if token == "--" or (not windows and not token.startswith("-")):
            # Deno runs code through a bare `eval` as its first plain word; a later `eval` is
            # data (`deno run eval.ts`).
            return family == "deno" and token.lower() == "eval"
        name, equals, _ = token.partition("=")
        key = name.lower() if windows else name
        if key in code_options:
            return True
        # `-Wonce` or `-rjson` is an option with its value attached, not a bundle of letters.
        if (
            not windows
            and not name.startswith("--")
            and len(name) > 2
            and not any(name.startswith(o) and len(name) > len(o) for o in glued_value_options)
            and any(f"-{letter}" in code_options for letter in name[1:])
        ):
            return True
        skip_value = key in value_options and not equals
    return False


def _bash_payload(args: list[str]) -> tuple[bool, str | None]:
    """Whether a shell gets `-c`, and the command string it then runs.

    -o and -O take the next word even inside the bundle that holds `c` or before it, and the two
    startup-file options take theirs, so neither hides a payload nor fakes one.
    """
    k = 0
    while k < len(args):
        token = args[k]
        if token == "--" or not token.startswith(("-", "+")):
            break
        if token in _BASH_VALUE_OPTIONS:
            k += 2
            continue
        letters = token[1:]
        # Letters keep their case, and only the shell's own letters make a bundle (`-Wc` is not).
        if token.startswith("--") or not set(letters) <= _BASH_OPTION_LETTERS:
            k += 1
            continue
        taken = 1 if {"o", "O"} & set(letters) else 0
        if "c" in letters:
            where = k + 1 + taken
            return True, args[where] if where < len(args) else None
        k += 1 + taken
    return False, None


def _tool_program(tool: str, args: list[str]) -> tuple[str, str] | None:
    """``(option, program)`` when a read-only tool is told to run a program."""
    run_options = _PROGRAM_OPTIONS[tool]
    k = 0
    while k < len(args):
        token = args[k]
        if token == "--":
            return None
        name, equals, value = token.partition("=")
        program: str | None = value if equals else None
        option = name if name in run_options else None
        if tool == "man" and len(token) > 2 and token[:2] in ("-P", "-H"):
            option, program = token[:2], token[2:]
        if option:
            if program is None and k + 1 < len(args):
                program = args[k + 1]
            # The option owns its value however it is spelled; the tools run a value starting
            # with `-` rather than read it as an option.
            if program:
                return option, program
            k += 2 if program is not None and "=" not in token else 1
        elif program is None and name in _TOOL_LONG_VALUE_OPTIONS[tool]:
            k += 2
        elif len(token) > 1 and token.startswith("-") and not token.startswith("--"):
            # In a bundle the first option taking a value takes the rest of the word, or the next
            # word when it comes last.
            takes = _TOOL_SHORT_VALUE_OPTIONS[tool]
            owner = next((n for n, letter in enumerate(token[1:], 1) if letter in takes), None)
            k += 2 if owner == len(token) - 1 else 1
        else:
            k += 1
    return None


# What _program_findings reports, besides a read-only tool's program option.
_SHELL_DASH_C = "shell command via -c/-lc flag"
_INLINE_SCRIPT = "script execution via -e/-c flag"
_INTERPRETER_HEREDOC = "script execution via heredoc"


def _program_findings(text: str) -> Iterator[tuple[str, str | None]]:
    """``(description, payload)`` for each command in *text* that runs code it was handed.

    The payload is the code itself when it can be read (`bash -c '...'`, `rg --pre prog`), so the
    rules can look at it too.
    """
    for piece in _top_level_segments(text):
        for at, _, word in _program_words(piece):
            program = _spelled_out(word)
            name = os.path.basename(program).lower()
            family = _interpreter_of(program)
            argv = _argv(piece, at)
            if argv is None:
                # Broken quoting around a program that can run code: refuse to guess.
                if family or name in _PROGRAM_OPTIONS:
                    yield (_MALFORMED, None)
                continue
            if not argv:
                continue
            args = argv[1:]
            if family and _runs_inline_code(family, args):
                yield (_INLINE_SCRIPT, None)
            elif family:
                if any(arg.startswith("<<") for arg in args):
                    yield (_INTERPRETER_HEREDOC, None)
            elif name in _SHELL_NAMES:
                has_c, payload = _bash_payload(args)
                if has_c:
                    yield (_SHELL_DASH_C, payload)
            elif name in _PROGRAM_OPTIONS and (chosen := _tool_program(name, args)):
                yield (f"arbitrary program execution via {name} {chosen[0]}", chosen[1])


def _env_split_words(text: str) -> list[str] | None:
    """The argv GNU `env -S` makes of *text*, or None for what it would reject or we can't know.

    These are env's rules, not the shell's. A `$` outside single quotes gives None: env would
    expand it from the environment it runs in, which is not this process's.
    """
    words: list[str] = []
    current: list[str] = []
    quote: str | None = None
    open_word = False
    k = 0
    while k < len(text):
        char = text[k]
        k += 1
        if char == "\\":
            if k == len(text):
                return None
            escaped = text[k]
            if quote == "'" and escaped not in ("'", "\\"):
                current.append(char)
                open_word = True
                continue
            k += 1
            if escaped == "_" and quote is None:
                # Outside quotes `\_` separates words, like a blank.
                if open_word:
                    words.append("".join(current))
                current, open_word = [], False
                continue
            if escaped == "c":
                # `\c` ends the string; inside quotes env refuses it.
                if quote:
                    return None
                break
            if escaped not in _ENV_S_ESCAPES:
                return None
            current.append(_ENV_S_ESCAPES[escaped])
            open_word = True
            continue
        if char in ("'", '"') and quote in (None, char):
            quote = None if quote else char
            open_word = True
            continue
        if quote is None and char in " \t\n\r\v\f":
            if open_word:
                words.append("".join(current))
            current, open_word = [], False
            continue
        if quote is None and char == "#" and not open_word:
            break
        if char == "$" and quote != "'":
            return None
        current.append(char)
        open_word = True
    if quote:
        return None
    if open_word:
        words.append("".join(current))
    return words


def _env_s_command(argv: list[str]) -> str | None:
    """The command `env -S STRING ...` runs, quoted back into one line, or None."""
    k = 1
    while k < len(argv):
        token = argv[k]
        if token == "--" or not token.startswith("-"):
            return None
        name, equals, value = token.partition("=")
        long_form = name == "--split-string"
        if long_form or token.startswith("-S"):
            if bool(equals) if long_form else len(token) > 2:
                string = value if long_form else token[2:]
            else:
                k += 1
                string = argv[k] if k < len(argv) else ""
            words = _env_split_words(string)
            # Quoted back so its separators stay data; only a real shell -c turns them into code.
            return None if words is None else shlex.join(words + argv[k + 1 :])
        k += 2 if not equals and name in _WRAPPER_VALUE_OPTIONS["env"] else 1
    return None


# ==== Variants and verdicts =======================================================================

# A raw command with a drive-letter path (`C:\`) or three backslashes in a row.
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\|\\{3}")


def _variants(command: str) -> Iterator[str]:
    """The spellings of *command* the rules read, each once, the plain spelling first."""
    plain = _plain_spelling(_unwrap_quoted_newlines(command))
    first, _ = _hide_grep_patterns(plain)
    yield first
    given = {first}

    def unseen(variant: str) -> bool:
        if not variant or variant in given:
            return False
        given.add(variant)
        return True

    # The plain spelling drops backslashes as escapes, so `del C:\Users\me\.ssh\id_rsa` reaches
    # the rules as `del C:Usersme.sshid_rsa`. With a Windows path in sight, also read the
    # command with every backslash turned to `/` first.
    if _WINDOWS_PATH.search(command):
        slashed = _plain_spelling(_unwrap_quoted_newlines(command.replace("\\", "/")))
        if unseen(slashed):
            yield slashed
    # Code handed to another program, read in the context of the command that owns it, so the
    # hardline rules see what will run without promoting look-alike options or quoted prose.
    # A payload may itself start with an option-like word and hold a command after a separator.
    stack = [plain]
    while stack:
        for _, payload in _program_findings(stack.pop()):
            if payload is None or not unseen(payload):
                continue
            yield payload
            marked = _with_start_marks(payload)
            if marked != payload and unseen(marked):
                yield marked
            stack.append(payload)
    # Commands opened by `(` or `{` are at a command position the rules' start class leaves out.
    marked = _with_start_marks(first)
    if marked != first and unseen(marked):
        yield marked
    # Quotes are read above on the plain spelling, where `\"` is already `"`: that flips which
    # side of a quote a `;` sits on. Here the starts are marked on the command as written, then
    # it is brought to the plain spelling. The blank before each newline keeps a trailing
    # backslash from joining it as a line continuation.
    faithful = _plain_spelling(_with_start_marks(_unwrap_quoted_newlines(command), " \n"))
    if unseen(faithful):
        yield faithful
    yield from _with_program_words_spelled_out(plain, unseen)


def _with_program_words_spelled_out(plain: str, unseen: Callable[[str], bool]) -> Iterator[str]:
    """*plain* with every program word spelled out (`r\\m`, `r''m`), arguments left as they are.

    One variant holds every word, not one variant per word: a heredoc can hold hundreds of quoted
    words, and a variant each would make matching quadratic. Words can nest (a backtick word and
    the command inside it), so overlapping ones wait for a later variant.
    """
    edits = sorted(
        (
            (start, end, spelled)
            for start, end, word in _program_words(plain)
            if (spelled := _spelled_out(word)) and spelled != word
        ),
        key=lambda edit: edit[:2],
    )
    while edits:
        layer: list[tuple[int, int, str]] = []
        later: list[tuple[int, int, str]] = []
        reach = 0
        for edit in edits:
            if edit[0] < reach:
                later.append(edit)
            else:
                layer.append(edit)
                reach = edit[1]
        variant = _splice(plain, layer)
        if unseen(variant):
            yield variant
        edits = later


def deny_command_variants(command: str) -> Iterator[str]:
    """Spellings of *command* for the user's deny list to match, the detection variants first.

    Then, for every program word, the program as written and its base name followed by the rest
    of its command, with blanks outside quotes squeezed, each also in the plain spelling. Quotes
    are read once, on the original text; only the program word is folded, never an argument's
    path. Code handed through `env -S` or to another program is read the same way.
    """
    yield from _variants(command)
    queue = [command]
    done: set[str] = set()
    while queue:
        source = queue.pop()
        if source in done:
            continue
        done.add(source)
        for start, end, word in _program_words(source):
            segment = _command_segment(source, start)
            program = _spelled_out(word)
            rest = _squeeze_bare_blanks(segment[end - start :])
            for name in dict.fromkeys((program, os.path.basename(program))):
                yield name + rest
                yield _plain_spelling(name + rest)
            if os.path.basename(program) == "env":
                argv = _argv(segment, 0)
                payload = _env_s_command(argv) if argv else None
                if payload:
                    queue.append(payload)
        queue.extend(payload for _, payload in _program_findings(source) if payload)


def _squeeze_bare_blanks(text: str) -> str:
    """Collapse each run of blanks outside quotes to one space; quoted text is data."""
    pieces: list[str] = []
    for kind, at, after, quote in _lex(text):
        if kind == _PLAIN and quote is None and text[at].isspace():
            if pieces and pieces[-1] == " ":
                continue
            pieces.append(" ")
        else:
            pieces.append(text[at:after])
    return "".join(pieces)


def _first_match(rules: tuple[_Rule, ...], lowered: str, blanked: Callable[[], str]) -> str | None:
    """The first rule matching a variant: *lowered* is it lowercased, *blanked* gives it with quoted
    text blanked, made only when a rule asks."""
    masked: str | None = None
    for rule in rules:
        subject = lowered
        if rule.on_blanked_quotes:
            if masked is None:
                masked = blanked()
            subject = masked
        if rule.regex.search(subject):
            return rule.description
    return None


def check_sudo_stdin_guard(command: str) -> tuple[bool, str | None]:
    """``(True, reason)`` when *command* runs `sudo -S`, which reads a password from stdin.

    Atlas never feeds sudo a password, so `-S` can only mean a guessed one piped in. Refused.
    """
    if _SUDO_S.search(_plain_spelling(command).lower()):
        return (True, _SUDO_STDIN)
    return (False, None)


def detect_hardline_command(command: str) -> tuple[bool, str | None]:
    """``(True, reason)`` when *command* matches a hardline rule; no mode lets it run."""
    if _too_big_to_parse(command):
        return (True, _PARSER_LIMIT)
    # Broken grep quoting is judged on the quotes as written: the plain spelling turns `\"` into
    # `"`, and `grep -o "[^\"]*"` would look unterminated. Only quoted newlines are unwrapped,
    # which keeps every quote in place.
    if _hide_grep_patterns(_unwrap_quoted_newlines(command))[1]:
        return (True, _MALFORMED)
    for variant in _variants(command):
        lowered = variant.lower()

        def blanked(variant: str = variant, lowered: str = lowered) -> str:
            # Quoted text is data, except what a shell or eval is handed: that runs.
            if _hands_quotes_to_a_shell(variant):
                return lowered
            return _blank_quoted_text(variant).lower()

        reason = _first_match(_HARDLINE_RULES, lowered, blanked)
        if reason is not None:
            return (True, reason)
    return (False, None)


def detect_dangerous_command(command: str) -> tuple[bool, str | None, str | None]:
    """``(True, key, reason)`` when *command* needs the user's approval; key and reason are equal."""
    if _too_big_to_parse(command):
        return (True, _PARSER_LIMIT, _PARSER_LIMIT)
    for variant in _variants(command):
        reason = _first_match(
            _DANGEROUS_RULES,
            _lowercase_but_flags(variant),
            lambda variant=variant: _lowercase_but_flags(_blank_quoted_text(variant)),
        )
        if reason is not None:
            return (True, reason, reason)
    for reason, _ in _program_findings(_plain_spelling(command)):
        return (True, reason, reason)
    return (False, None, None)
