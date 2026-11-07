"""Code run in a fresh interpreter under a time limit, for the tests that time the trust gate.

A pattern stuck in C holds the GIL, so only a separate process can be stopped. And the time is
measured where neither coverage tracing nor this process's state slows it down.
"""

import os
import subprocess
import sys
from pathlib import Path

import atlas

# The interpreter imports the same Atlas as this test, whether that is the editable install, a
# worktree or a tree put first on PYTHONPATH.
_ATLAS_SOURCE = str(Path(atlas.__file__).resolve().parent.parent)


def in_a_fresh_interpreter(code: str, *, limit: float = 30) -> None:
    """Run *code*, failing past *limit* seconds. A stuck pattern never ends, so the limit only
    has to outlast an interpreter's start on a machine busy with the other test processes."""
    search = os.pathsep.join(p for p in (_ATLAS_SOURCE, os.environ.get("PYTHONPATH")) if p)
    subprocess.run(  # noqa: S603 - the code is the test's own
        [sys.executable, "-c", code],
        check=True,
        timeout=limit,
        env={**os.environ, "PYTHONPATH": search},
    )
