"""Unit tests for otto.core.tools — custom tool functions with mocked subprocess."""

from __future__ import annotations

from unittest.mock import patch

from otto.core.tools import ALL_TOOLS
from otto.core.tools.git import (
    git_branch,
    git_commit,
    git_diff,
    git_log,
    git_push,
    git_status,
    open_pull_request,
)
from otto.core.tools.lint import run_linter
from otto.core.tools.tests import run_tests


class TestALLTools:
    """Verify the ALL_TOOLS export list is complete."""

    def test_all_tools_count(self) -> None:
        assert len(ALL_TOOLS) == 9

    def test_all_tools_are_callable(self) -> None:
        for tool in ALL_TOOLS:
            assert callable(tool), f"{tool.__name__} is not callable"

    def test_all_tools_names(self) -> None:
        names = {t.__name__ for t in ALL_TOOLS}
        expected = {
            "run_tests", "run_linter",
            "git_diff", "git_status", "git_log",
            "git_branch", "git_commit", "git_push",
            "open_pull_request",
        }
        assert names == expected


class TestRunTests:
    """Tests for the run_tests tool function."""

    @patch("otto.core.tools.tests._run_command")
    def test_passing_tests(self, mock_run) -> None:
        mock_run.return_value = (0, "5 passed in 0.1s", "")
        result = run_tests()
        assert "[PASSED]" in result
        assert "5 passed" in result

    @patch("otto.core.tools.tests._run_command")
    def test_failing_tests(self, mock_run) -> None:
        mock_run.return_value = (1, "1 failed, 4 passed", "assertion error")
        result = run_tests()
        assert "[FAILED (exit code 1)]" in result

    @patch("otto.core.tools.tests._run_command")
    def test_with_scope(self, mock_run) -> None:
        mock_run.return_value = (0, "1 passed", "")
        run_tests(scope="tests/test_foo.py")
        # Should include the scope in the command
        call_args = mock_run.call_args[0][0]
        assert "tests/test_foo.py" in call_args

    @patch("otto.core.tools.tests._run_command")
    def test_stderr_included(self, mock_run) -> None:
        mock_run.return_value = (0, "stdout output", "stderr output")
        result = run_tests()
        assert "stderr output" in result
        assert "STDERR" in result


class TestRunLinter:
    """Tests for the run_linter tool function."""

    @patch("otto.core.tools.lint._run_command")
    def test_clean_lint(self, mock_run) -> None:
        mock_run.return_value = (0, "", "")
        result = run_linter()
        assert "[CLEAN]" in result

    @patch("otto.core.tools.lint._run_command")
    def test_issues_found(self, mock_run) -> None:
        mock_run.return_value = (1, "foo.py:1:1: E100 error", "")
        result = run_linter()
        assert "[ISSUES FOUND" in result
        assert "E100" in result


class TestGitDiff:
    """Tests for the git_diff tool function."""

    @patch("otto.core.tools.git._run_command")
    def test_full_diff(self, mock_run) -> None:
        mock_run.return_value = (0, "diff --git a/foo b/foo\n...", "")
        result = git_diff()
        assert "diff --git" in result

    @patch("otto.core.tools.git._run_command")
    def test_path_diff(self, mock_run) -> None:
        mock_run.return_value = (0, "diff --git a/foo b/foo", "")
        git_diff(path="foo")
        call_args = mock_run.call_args[0][0]
        assert "foo" in call_args

    @patch("otto.core.tools.git._run_command")
    def test_no_changes(self, mock_run) -> None:
        mock_run.return_value = (0, "", "")
        result = git_diff()
        assert result == "(no changes)"

    @patch("otto.core.tools.git._run_command")
    def test_error(self, mock_run) -> None:
        mock_run.return_value = (128, "", "fatal: not a git repo")
        result = git_diff()
        assert "failed" in result.lower()


class TestGitStatus:
    """Tests for the git_status tool function."""

    @patch("otto.core.tools.git._run_command")
    def test_clean_tree(self, mock_run) -> None:
        mock_run.return_value = (0, "", "")
        result = git_status()
        assert result == "(clean working tree)"

    @patch("otto.core.tools.git._run_command")
    def test_changes_present(self, mock_run) -> None:
        mock_run.return_value = (0, "M foo.py\nA bar.py", "")
        result = git_status()
        assert "M foo.py" in result


class TestGitLog:
    """Tests for the git_log tool function."""

    @patch("otto.core.tools.git._run_command")
    def test_log_output(self, mock_run) -> None:
        mock_run.return_value = (0, "abc1234 initial commit", "")
        result = git_log()
        assert "abc1234" in result

    @patch("otto.core.tools.git._run_command")
    def test_log_with_n(self, mock_run) -> None:
        mock_run.return_value = (0, "", "")
        git_log(n=5)
        call_args = mock_run.call_args[0][0]
        assert "-n 5" in call_args

    @patch("otto.core.tools.git._run_command")
    def test_no_commits(self, mock_run) -> None:
        mock_run.return_value = (0, "", "")
        result = git_log()
        assert result == "(no commits)"


class TestGitBranch:
    """Tests for the git_branch tool function."""

    @patch("otto.core.tools.git._run_command")
    def test_list_branches(self, mock_run) -> None:
        mock_run.return_value = (0, "* main\n  dev", "")
        result = git_branch()
        assert "main" in result

    @patch("otto.core.tools.git._run_command")
    def test_create_branch(self, mock_run) -> None:
        mock_run.return_value = (0, "Switched to a new branch 'feature'", "")
        result = git_branch(name="feature")
        assert "Created and switched" in result
        assert "feature" in result


class TestGitCommit:
    """Tests for the git_commit tool function."""

    @patch("otto.core.tools.git._run_command")
    def test_successful_commit(self, mock_run) -> None:
        mock_run.return_value = (0, "[main abc1234] test commit", "")
        result = git_commit("test commit")
        assert "abc1234" in result

    @patch("otto.core.tools.git._run_command")
    def test_add_fails(self, mock_run) -> None:
        mock_run.return_value = (1, "", "git add error")
        result = git_commit("test")
        assert "git add failed" in result


class TestGitPush:
    """Tests for the git_push tool function."""

    @patch("otto.core.tools.git._run_command")
    def test_successful_push(self, mock_run) -> None:
        mock_run.return_value = (0, "To github.com:user/repo", "")
        result = git_push()
        assert "To github.com" in result

    @patch("otto.core.tools.git._run_command")
    def test_push_failure(self, mock_run) -> None:
        mock_run.return_value = (1, "", "fatal: auth failed")
        result = git_push()
        assert "failed" in result.lower()


class TestOpenPullRequest:
    """Tests for the open_pull_request tool function."""

    @patch("otto.core.tools.git._run_command")
    def test_successful_pr(self, mock_run) -> None:
        mock_run.return_value = (0, "https://github.com/user/repo/pull/1", "")
        result = open_pull_request("My PR", "Description here")
        assert "https://github.com" in result

    @patch("otto.core.tools.git._run_command")
    def test_pr_with_branch(self, mock_run) -> None:
        mock_run.return_value = (0, "https://github.com/user/repo/pull/1", "")
        open_pull_request("My PR", "Desc", branch="feature")
        call_args = mock_run.call_args[0][0]
        assert "feature" in call_args

    @patch("otto.core.tools.git._run_command")
    def test_pr_failure(self, mock_run) -> None:
        mock_run.return_value = (1, "", "gh: not logged in")
        result = open_pull_request("My PR", "Desc")
        assert "failed" in result.lower()
