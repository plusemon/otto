"""Unit tests for otto.core.settings — config auto-detection logic."""

from __future__ import annotations

import json
from pathlib import Path

from otto.core.settings import (
    _detect_lint_command,
    _detect_test_command,
    load_project_config,
)


class TestDetectTestCommand:
    """Tests for test command auto-detection from marker files."""

    def test_pytest_ini(self, tmp_path: Path) -> None:
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        assert self._detect_in(tmp_path) == "pytest"

    def test_setup_cfg(self, tmp_path: Path) -> None:
        (tmp_path / "setup.cfg").write_text("[tool:pytest]\n")
        assert self._detect_in(tmp_path) == "pytest"

    def test_tox_ini(self, tmp_path: Path) -> None:
        (tmp_path / "tox.ini").write_text("[tox]\n")
        assert self._detect_in(tmp_path) == "pytest"

    def test_package_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
        assert self._detect_in(tmp_path) == "npm test"

    def test_makefile(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("test:\n\techo ok\n")
        assert self._detect_in(tmp_path) == "make test"

    def test_cargo_toml(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        assert self._detect_in(tmp_path) == "cargo test"

    def test_go_mod(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example\n")
        assert self._detect_in(tmp_path) == "go test ./..."

    def test_pyproject_toml_with_pytest(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\ntestpaths = ['tests']\n"
        )
        assert self._detect_in(tmp_path) == "pytest"

    def test_pyproject_toml_without_pytest(self, tmp_path: Path) -> None:
        """pyproject.toml without pytest markers should NOT detect pytest."""
        (tmp_path / "pyproject.toml").write_text(
            "[build-system]\nrequires = ['setuptools']\n"
        )
        # No match — falls through to no-test-runner
        assert self._detect_in(tmp_path) == "echo 'No test runner detected'"

    def test_pyproject_with_package_json_fallback(self, tmp_path: Path) -> None:
        """pyproject.toml without pytest + package.json → npm test."""
        (tmp_path / "pyproject.toml").write_text(
            "[build-system]\nrequires = ['setuptools']\n"
        )
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
        assert self._detect_in(tmp_path) == "npm test"

    def test_no_markers_returns_fallback(self, tmp_path: Path) -> None:
        assert self._detect_in(tmp_path) == "echo 'No test runner detected'"

    def test_priority_order(self, tmp_path: Path) -> None:
        """First match in _TEST_DETECTORS wins."""
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
        # pytest.ini comes before package.json in the list
        assert self._detect_in(tmp_path) == "pytest"

    def _detect_in(self, path: Path) -> str:
        """Run _detect_test_command with cwd set to path."""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(path)
            return _detect_test_command()
        finally:
            os.chdir(old_cwd)


class TestDetectLintCommand:
    """Tests for linter auto-detection from marker files."""

    def test_ruff_toml(self, tmp_path: Path) -> None:
        (tmp_path / "ruff.toml").write_text('line-length = 88\n')
        assert self._detect_in(tmp_path) == "ruff check ."

    def test_dot_ruff_toml(self, tmp_path: Path) -> None:
        (tmp_path / ".ruff.toml").write_text('line-length = 88\n')
        assert self._detect_in(tmp_path) == "ruff check ."

    def test_eslintrc_js(self, tmp_path: Path) -> None:
        (tmp_path / ".eslintrc.js").write_text("module.exports = {}")
        assert self._detect_in(tmp_path) == "npx eslint ."

    def test_eslint_config_js(self, tmp_path: Path) -> None:
        (tmp_path / "eslint.config.js").write_text("export default {}")
        assert self._detect_in(tmp_path) == "npx eslint ."

    def test_flake8(self, tmp_path: Path) -> None:
        (tmp_path / ".flake8").write_text("[flake8]\n")
        assert self._detect_in(tmp_path) == "flake8 ."

    def test_pylint(self, tmp_path: Path) -> None:
        (tmp_path / ".pylintrc").write_text("[MASTER]\n")
        assert self._detect_in(tmp_path) == "pylint **/*.py"

    def test_pyproject_toml_with_ruff(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 88\n"
        )
        assert self._detect_in(tmp_path) == "ruff check ."

    def test_pyproject_toml_without_ruff(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[build-system]\nrequires = ['setuptools']\n"
        )
        assert self._detect_in(tmp_path) == "echo 'No linter detected'"

    def test_no_markers_returns_fallback(self, tmp_path: Path) -> None:
        assert self._detect_in(tmp_path) == "echo 'No linter detected'"

    def _detect_in(self, path: Path) -> str:
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(path)
            return _detect_lint_command()
        finally:
            os.chdir(old_cwd)


class TestLoadProjectConfig:
    """Tests for config override loading."""

    def test_auto_detected_source(self, tmp_path: Path) -> None:
        """Without .otto/config.json, source is 'auto-detected'."""
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            cfg = load_project_config()
            assert cfg.source == "auto-detected"
            assert cfg.test_command == "pytest"
        finally:
            os.chdir(old_cwd)

    def test_config_json_override(self, tmp_path: Path) -> None:
        """.otto/config.json overrides auto-detection."""
        otto_dir = tmp_path / ".otto"
        otto_dir.mkdir()
        (otto_dir / "config.json").write_text(
            json.dumps({"test_command": "custom-test", "lint_command": "custom-lint"})
        )
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            cfg = load_project_config()
            assert cfg.source == "config.json"
            assert cfg.test_command == "custom-test"
            assert cfg.lint_command == "custom-lint"
        finally:
            os.chdir(old_cwd)

    def test_partial_override(self, tmp_path: Path) -> None:
        """Only overridden fields change; others fall through to auto-detect."""
        otto_dir = tmp_path / ".otto"
        otto_dir.mkdir()
        (otto_dir / "config.json").write_text(
            json.dumps({"test_command": "custom-test"})
        )
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            cfg = load_project_config()
            assert cfg.test_command == "custom-test"
            # lint_command should be auto-detected (no lint markers → fallback)
            assert cfg.lint_command == "echo 'No linter detected'"
        finally:
            os.chdir(old_cwd)

    def test_corrupt_config_json_falls_through(self, tmp_path: Path) -> None:
        """Corrupt .otto/config.json is handled gracefully."""
        otto_dir = tmp_path / ".otto"
        otto_dir.mkdir()
        (otto_dir / "config.json").write_text("NOT JSON {{{")
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            cfg = load_project_config()
            assert cfg.source == "auto-detected"
            assert cfg.test_command == "pytest"
        finally:
            os.chdir(old_cwd)
