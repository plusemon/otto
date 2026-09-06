"""Project configuration and auto-detection for otto.

Detects the test runner, linter, and other project-specific commands by
inspecting the working directory.  Override via .otto/config.json.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from dataclasses import dataclass

logger = logging.getLogger("otto.config")

_CONFIG_DIR = pathlib.Path(".otto")
_CONFIG_FILE = _CONFIG_DIR / "config.json"

# Priority-ordered detection: first match wins.
_TEST_DETECTORS: list[tuple[str, str]] = [
    # (marker_file, command)
    ("pytest.ini", "pytest"),
    ("pyproject.toml", "pytest"),  # could be ruff too, but pytest is common
    ("setup.cfg", "pytest"),
    ("tox.ini", "pytest"),
    ("package.json", "npm test"),
    ("Makefile", "make test"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
    ("Gemfile", "bundle exec rspec"),
    ("build.gradle", "gradle test"),
]

_LINT_DETECTORS: list[tuple[str, str]] = [
    (".ruff.toml", "ruff check ."),
    ("ruff.toml", "ruff check ."),
    ("pyproject.toml", "ruff check ."),
    (".eslintrc.js", "npx eslint ."),
    (".eslintrc.json", "npx eslint ."),
    ("eslint.config.js", "npx eslint ."),
    ("eslint.config.mjs", "npx eslint ."),
    (".flake8", "flake8 ."),
    (".pylintrc", "pylint **/*.py"),
    ("Makefile", "make lint"),
    ("package.json", "npm run lint"),
]


@dataclass(frozen=True)
class ProjectConfig:
    """Detected or overridden project commands."""

    test_command: str
    lint_command: str
    source: str  # "auto-detected" or "config.json"


def _find_marker(marker: str) -> bool:
    """Check if a marker file exists in the working directory."""
    return pathlib.Path(marker).exists()


def _detect_test_command() -> str:
    """Auto-detect the test command from project files."""
    for marker, cmd in _TEST_DETECTORS:
        if _find_marker(marker):
            # Special case: pyproject.toml might not have pytest config
            if marker == "pyproject.toml":
                try:
                    content = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
                    if "[tool.pytest" in content or "pytest" in content:
                        return cmd
                    # Check for package.json alongside pyproject.toml
                    if _find_marker("package.json"):
                        return "npm test"
                except OSError:
                    pass
                continue
            logger.debug("Auto-detected test command: %s (from %s)", cmd, marker)
            return cmd
    return "echo 'No test runner detected'"


def _detect_lint_command() -> str:
    """Auto-detect the linter command from project files."""
    for marker, cmd in _LINT_DETECTORS:
        if _find_marker(marker):
            # Special case: pyproject.toml might not have ruff config
            if marker == "pyproject.toml":
                try:
                    content = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
                    if "[tool.ruff" in content:
                        return cmd
                except OSError:
                    pass
                continue
            logger.debug("Auto-detected lint command: %s (from %s)", cmd, marker)
            return cmd
    return "echo 'No linter detected'"


def load_project_config() -> ProjectConfig:
    """Load project config, preferring .otto/config.json overrides.

    .otto/config.json format:
    {
        "test_command": "pytest -x",
        "lint_command": "ruff check . --fix"
    }
    """
    overrides: dict[str, str] = {}
    if _CONFIG_FILE.exists():
        try:
            raw = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            overrides = {k: v for k, v in raw.items() if isinstance(v, str)}
            logger.debug("Loaded config overrides from %s", _CONFIG_FILE)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read %s: %s", _CONFIG_FILE, e)

    test_cmd = overrides.get("test_command", _detect_test_command())
    lint_cmd = overrides.get("lint_command", _detect_lint_command())
    source = "config.json" if overrides else "auto-detected"

    config = ProjectConfig(test_command=test_cmd, lint_command=lint_cmd, source=source)
    logger.info(
        "Project config loaded (source=%s): test=%s, lint=%s",
        source,
        test_cmd,
        lint_cmd,
    )
    return config


def _run_command(cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr).

    This is a synchronous helper used by the custom tools.
    Ensures the current Python venv's bin directory is on PATH so that
    tools installed in the venv (pytest, ruff, etc.) are discoverable.
    """
    import subprocess
    import sys

    env = os.environ.copy()
    # Prepend venv bin to PATH so subprocess shells find venv-installed tools
    venv_bin = pathlib.Path(sys.prefix) / "bin"
    if venv_bin.is_dir():
        env["PATH"] = str(venv_bin) + os.pathsep + env.get("PATH", "")

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.getcwd(),
            env=env,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s"
    except (subprocess.SubprocessError, OSError) as e:
        return 1, "", f"Failed to run command: {e}"


if __name__ == "__main__":
    cfg = load_project_config()
    print(f"test_command: {cfg.test_command}")
    print(f"lint_command: {cfg.lint_command}")
    print(f"source:       {cfg.source}")
