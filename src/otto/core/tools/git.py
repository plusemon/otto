"""Git custom tools for otto."""

from __future__ import annotations

from ..settings import _run_command


def git_diff(path: str | None = None) -> str:
    """Show the current working tree diff.

    Args:
        path: Optional file or directory to diff.  If None, shows the
              full working tree diff.

    Returns:
        The unified diff output.
    """
    cmd = "git diff"
    if path:
        cmd = f"git diff -- {path}"
    rc, stdout, stderr = _run_command(cmd)
    if rc != 0:
        return f"git diff failed: {stderr.strip()}"
    return stdout.strip() or "(no changes)"


def git_status() -> str:
    """Show the working tree status.

    Returns:
        Short git status output.
    """
    rc, stdout, stderr = _run_command("git status --short")
    if rc != 0:
        return f"git status failed: {stderr.strip()}"
    return stdout.strip() or "(clean working tree)"


def git_log(n: int = 10) -> str:
    """Show recent git log.

    Args:
        n: Number of recent commits to show (default 10).

    Returns:
        Formatted git log output.
    """
    rc, stdout, stderr = _run_command(f"git log --oneline -n {n}")
    if rc != 0:
        return f"git log failed: {stderr.strip()}"
    return stdout.strip() or "(no commits)"


def git_branch(name: str | None = None) -> str:
    """List branches or create a new branch.

    Args:
        name: If provided, creates and switches to a new branch with
              this name.  If None, lists all local branches.

    Returns:
        Branch listing or confirmation of branch creation.
    """
    if name:
        rc, stdout, stderr = _run_command(f"git checkout -b {name}")
        if rc != 0:
            return f"git checkout -b {name} failed: {stderr.strip()}"
        return f"Created and switched to branch '{name}'"
    else:
        rc, stdout, stderr = _run_command("git branch")
        if rc != 0:
            return f"git branch failed: {stderr.strip()}"
        return stdout.strip() or "(no branches)"


def git_commit(message: str) -> str:
    """Stage all changes and commit with the given message.

    Args:
        message: The commit message.

    Returns:
        Confirmation with commit hash.
    """
    # Stage all changes
    rc, _, stderr = _run_command("git add -A")
    if rc != 0:
        return f"git add failed: {stderr.strip()}"

    # Commit
    rc, stdout, stderr = _run_command(f"git commit -m {message!r}")
    if rc != 0:
        return f"git commit failed: {stderr.strip()}"
    return stdout.strip()


def git_push() -> str:
    """Push the current branch to the remote origin.

    Returns:
        Push output or error message.
    """
    rc, stdout, stderr = _run_command("git push origin HEAD")
    if rc != 0:
        return f"git push failed: {stderr.strip()}"
    return stdout.strip() or "Push successful"


def open_pull_request(title: str, body: str, branch: str | None = None) -> str:
    """Create a pull request on GitHub using the gh CLI.

    Args:
        title: PR title.
        body: PR description/body.
        branch: Source branch (defaults to current branch).

    Returns:
        The URL of the created pull request, or an error message.
    """
    cmd = f"gh pr create --title {title!r} --body {body!r}"
    if branch:
        cmd += f" --head {branch}"
    rc, stdout, stderr = _run_command(cmd)
    if rc != 0:
        return f"gh pr create failed: {stderr.strip()}"
    return stdout.strip()
