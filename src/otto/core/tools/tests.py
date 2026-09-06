"""Test runner custom tool for otto."""

from __future__ import annotations

from ..settings import _run_command, load_project_config


def run_tests(scope: str | None = None) -> str:
    """Run the project's test suite and report results.

    Auto-detects the test command (pytest, npm test, cargo test, etc.)
    from project files.  Override via .otto/config.json.

    Args:
        scope: Optional test scope — a module path, test file, or test
               name pattern passed to the test runner.  For pytest this
               becomes ``pytest <scope>``, for npm test it's ignored.

    Returns:
        A summary of test output including pass/fail counts.
    """
    cfg = load_project_config()
    cmd = cfg.test_command

    if scope:
        if cmd.startswith("pytest"):
            cmd = f"pytest {scope}"
        elif cmd.startswith("cargo test"):
            cmd = f"cargo test {scope}"
        elif cmd.startswith("go test"):
            cmd = f"go test {scope}"
        # npm test doesn't support scope args cleanly — ignore

    rc, stdout, stderr = _run_command(cmd, timeout=300)
    output = stdout + ("\n--- STDERR ---\n" + stderr if stderr else "")
    status = "PASSED" if rc == 0 else f"FAILED (exit code {rc})"
    return f"[{status}] {cmd}\n\n{output.strip()}"
