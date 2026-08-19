"""Python's terminal run statuses must all be terminal to the Rust kernel too.

MAN-306: a run that finished normally was archived with `status="completed"`,
the kernel's `archive_run` guard did not recognise that as terminal, and every
ordinary task assignment tripped `archive_non_terminal_run_requires_review`
for weeks with no error anywhere saying why. The fix widened the kernel's
`TERMINAL_RUN_STATUSES` -- BY HAND, from a comment quoting Python's list --
and in doing so dropped `waiting_for_input`, which is a real status a real run
really reaches (`worker_dispatch_service.py:703`). So the same bug survived in
the one status the fix forgot to copy, and nothing anywhere noticed.

That is this codebase's own documented failure mode twice over: a hand-copied
list goes stale silently, and "a check that derives its own expectations from
the thing it checks is blind". So this test reads the two lists from TWO
DIFFERENT FILES -- Python's from `server_modules/shared.py`, the kernel's from
`empyralis-runtime-kernel/src/runtime_state_store.rs` -- and compares them.
Neither can be edited into agreement with itself.

DIRECTION MATTERS. The requirement is Python ⊆ Rust, not equality:

    Python says terminal, kernel does not  ─▶ FAILURE. This is MAN-306: an
                                              ordinary finished run needs
                                              human review to archive.
    kernel says terminal, Python does not  ─▶ fine. The kernel is permissive
                                              about a status Python never
                                              produces; nothing is blocked.

The kernel's list deliberately carries extras (`succeeded`, `terminated`,
`expired`, `archived`) kept in case another caller relies on them, so
asserting equality would fail on purpose-built slack.

This is a SOURCE-TEXT test, not a behavioural one, and that is deliberate:
the kernel is a compiled binary invoked over subprocess, so a behavioural test
would exercise whatever `cargo build` last produced rather than what the
source says. The staleness half of that problem is `preflight._check_kernel`'s
job (it refuses to boot when a `.rs` file is newer than the binary); this
test's job is the drift between two source files.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTHON_SOURCE = _REPO_ROOT / "server_modules" / "shared.py"
_KERNEL_SOURCE = _REPO_ROOT / "empyralis-runtime-kernel" / "src" / "runtime_state_store.rs"


def _python_terminal_statuses() -> set[str]:
    """Parsed from shared.py's AST, never imported.

    Importing `server_modules.shared` drags in the whole runtime; parsing the
    literal keeps this test cheap and, more importantly, keeps it readable
    when the module fails to import for an unrelated reason.
    """
    tree = ast.parse(_PYTHON_SOURCE.read_text())
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        for target in targets:
            if target.id != "TERMINAL_RUN_STATUSES":
                continue
            value = node.value
            if isinstance(value, (ast.Set, ast.List, ast.Tuple)):
                return {
                    element.value
                    for element in value.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
    raise AssertionError(
        f"TERMINAL_RUN_STATUSES not found as a literal in {_PYTHON_SOURCE}. "
        "If it moved or became computed, this test needs to follow it — do not "
        "delete the test, or the MAN-306 drift becomes invisible again."
    )


def _kernel_terminal_statuses() -> set[str]:
    text = _KERNEL_SOURCE.read_text()
    match = re.search(
        r"const\s+TERMINAL_RUN_STATUSES\s*:\s*&\[&str\]\s*=\s*&\[(.*?)\];",
        text,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(
            f"TERMINAL_RUN_STATUSES not found in {_KERNEL_SOURCE}. "
            "If the constant moved, point this test at its new home."
        )
    # Comments inside the literal quote Python status names verbatim, so they
    # must be stripped before scanning for string literals — otherwise the
    # test reads the names out of the COMMENT and passes while the actual
    # array is missing them, which is precisely the failure it exists to
    # catch.
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return set(re.findall(r'"([^"]+)"', body))


class KernelTerminalStatusDriftTests(unittest.TestCase):
    def test_every_python_terminal_status_is_terminal_to_the_kernel(self) -> None:
        python_statuses = _python_terminal_statuses()
        kernel_statuses = _kernel_terminal_statuses()
        missing = python_statuses - kernel_statuses
        self.assertEqual(
            missing,
            set(),
            "server_modules/shared.py treats these run statuses as TERMINAL, but "
            "empyralis-runtime-kernel/src/runtime_state_store.rs does not: "
            f"{sorted(missing)}. Archiving a run in any of them trips "
            "archive_non_terminal_run_requires_review and flips the task to "
            "Blocked — MAN-306, again. Add them to the Rust const AND rebuild "
            "the kernel (the binary is not rebuilt by pulling code; see "
            "docs/DEPLOY-RUNBOOK.md step 3a).",
        )

    def test_both_lists_were_actually_read(self) -> None:
        """Guards the guard.

        Every assertion above is `missing == set()`, which an empty
        Python-side parse satisfies trivially. If `shared.py` were ever
        refactored so the literal is no longer found the way this test looks
        for it, the drift check would silently start passing against nothing.
        """
        self.assertGreaterEqual(len(_python_terminal_statuses()), 5)
        self.assertGreaterEqual(len(_kernel_terminal_statuses()), 5)

    def test_the_status_that_regressed_is_covered(self) -> None:
        """`waiting_for_input` specifically — the one the MAN-306 fix dropped.

        Pinned by name because it is the only member of Python's set that a
        reader would plausibly think is NOT terminal (it sounds like a pause,
        not an end), which is exactly why it was the one left out.
        """
        self.assertIn("waiting_for_input", _python_terminal_statuses())
        self.assertIn("waiting_for_input", _kernel_terminal_statuses())


if __name__ == "__main__":
    unittest.main()
