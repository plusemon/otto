"""Custom tools for otto — test runner, linter, and git operations.

These are plain Python callables registered via the tools= config param.
The SDK auto-generates JSON Schema from type annotations and docstrings,
serializes them to protobuf, and the Go harness dispatches calls back to
ToolRunner which invokes these functions.

Policy gating is handled by the same policy system used for built-in tools
(policies match on tool name strings).  No special registration needed.
"""

from .git import (
    git_branch,
    git_commit,
    git_diff,
    git_log,
    git_push,
    git_status,
    open_pull_request,
)
from .lint import run_linter
from .tests import run_tests

ALL_TOOLS = [
    run_tests,
    run_linter,
    git_diff,
    git_status,
    git_log,
    git_branch,
    git_commit,
    git_push,
    open_pull_request,
]
