"""Trust: the approval gate of every tool call, dangerous commands, the guardian, auto mode, and
the threat patterns that keep outside text from giving orders."""

from atlas.trust.detection import (
    detect_dangerous_command,
    detect_hardline_command,
    fold_atlas_home,
)
from atlas.trust.model import (
    DENIAL_BREAKER,
    GUARDIAN_SYSTEM,
    NOBODY_TO_APPROVE,
    UNTRUSTED_MIN_CHARS,
    ApprovalAnswer,
    ApprovalRequest,
    Approver,
    Choice,
    Outcome,
    Verdict,
    guardian_prompt,
    parse_verdict,
    strip_shell_comments,
    untrusted,
)
from atlas.trust.outward import outside_program, outward_actions
from atlas.trust.service import GUARDIAN_PURPOSE, ApprovalGate, guardian_effort, script_texts
from atlas.trust.store import SCHEMA, TrustStore
from atlas.trust.threats import Scope, first_threat_message, scan_for_threats

__all__ = [
    "DENIAL_BREAKER",
    "GUARDIAN_PURPOSE",
    "GUARDIAN_SYSTEM",
    "NOBODY_TO_APPROVE",
    "SCHEMA",
    "UNTRUSTED_MIN_CHARS",
    "ApprovalAnswer",
    "ApprovalGate",
    "ApprovalRequest",
    "Approver",
    "Choice",
    "Outcome",
    "Scope",
    "TrustStore",
    "Verdict",
    "detect_dangerous_command",
    "detect_hardline_command",
    "first_threat_message",
    "fold_atlas_home",
    "guardian_effort",
    "guardian_prompt",
    "outside_program",
    "outward_actions",
    "parse_verdict",
    "scan_for_threats",
    "script_texts",
    "strip_shell_comments",
    "untrusted",
]
