"""Linter custom tool for otto."""

from __future__ import annotations

from ..settings import _run_command, load_project_config


def run_linter() -> str:
    """Run the project's linter and report findings.

    Auto-detects the linter (ruff, eslint, flake8, etc.) from project
    files.  Override via .otto/config.json.

    Returns:
        Linter output including any warnings or errors found.
    """
    cfg = load_project_config()
    cmd = cfg.lint_command

    rc, stdout, stderr = _run_command(cmd, timeout=120)
    output = stdout + ("\n--- STDERR ---\n" + stderr if stderr else "")
    status = "CLEAN" if rc == 0 else f"ISSUES FOUND (exit code {rc})"
    return f"[{status}] {cmd}\n\n{output.strip()}"
