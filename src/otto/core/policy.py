"""Tool-call policy layer for otto.

Defines two static policy tables (Build mode and Plan mode) and provides
an ask_user handler factory that bridges the SDK's policy callback to
the Textual UI via per-session asyncio primitives.

Build mode policy table:
  Read files, search codebase  → allow
  Edit files (working tree)    → allow
  Run shell commands           → ask_user
  Delete files                 → ask_user  (via run_command; no dedicated tool)
  Test runner / linter         → allow  (read-only side effects only)
  Git read-only (diff/status/log) → allow
  Git mutating (branch/commit) → allow  (safe: local only, undoable)
  Git push / open PR           → ask_user  (remote-facing, harder to undo)
  Everything else              → deny (default)

Plan mode policy table:
  Read files, search codebase  → allow
  Test runner / linter         → allow  (read-only verification)
  Git read-only (diff/status/log) → allow
  Edit files                   → deny
  Run shell commands           → deny
  Git mutating (branch/commit/push/PR) → deny
  Delete files                 → deny
  Everything else              → deny (default)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.antigravity import policy, types
from google.antigravity.hooks.policy import AskUserHandler

logger = logging.getLogger("otto.policy")


def build_policies(
    ask_handler: AskUserHandler | None = None,
) -> list[policy.Policy]:
    """Return the static policy list for otto's 'Build mode'.

    The deny("*") wildcard at the end catches anything not explicitly
    allowed or gated, giving us deny-by-default semantics.

    If *ask_handler* is provided, RUN_COMMAND and git-push/PR are gated
    with ASK_USER.  Otherwise they fall through to the deny wildcard.
    """
    policies: list[policy.Policy] = [
        # Read-only tools — always allowed
        policy.allow("view_file", name="allow_read"),
        policy.allow("list_directory", name="allow_read"),
        policy.allow("search_directory", name="allow_read"),
        policy.allow("find_file", name="allow_read"),
        # File editing — always allowed
        policy.allow("create_file", name="allow_write"),
        policy.allow("edit_file", name="allow_write"),
        # Finish tool — always allowed
        policy.allow("finish", name="allow_finish"),
        # Test runner / linter — always allowed (read-only side effects)
        policy.allow("run_tests", name="allow_tests"),
        policy.allow("run_linter", name="allow_linter"),
        # Git read-only — always allowed
        policy.allow("git_diff", name="allow_git_read"),
        policy.allow("git_status", name="allow_git_read"),
        policy.allow("git_log", name="allow_git_read"),
        # Git mutating — allowed in Build mode (local only, undoable)
        policy.allow("git_branch", name="allow_git_mutate"),
        policy.allow("git_commit", name="allow_git_mutate"),
    ]

    if ask_handler is not None:
        # Shell execution — requires confirmation
        policies.append(
            policy.ask_user("run_command", handler=ask_handler, name="confirm_run_command")
        )
        # Git push / PR — always requires confirmation (remote-facing)
        policies.append(
            policy.ask_user("git_push", handler=ask_handler, name="confirm_git_push")
        )
        policies.append(
            policy.ask_user(
                "open_pull_request", handler=ask_handler, name="confirm_open_pr"
            )
        )

    # Deny-by-default wildcard
    policies.append(policy.deny("*", name="deny_default"))
    return policies


def build_plan_policies() -> list[policy.Policy]:
    """Return the static policy list for otto's 'Plan mode'.

    Plan mode is read-only: the agent can read/search files, run tests,
    run linters, and inspect git state — but cannot edit, run shell
    commands, or perform git mutations.  No ask_user handler is needed.
    """
    return [
        # Read-only tools — always allowed
        policy.allow("view_file", name="allow_read"),
        policy.allow("list_directory", name="allow_read"),
        policy.allow("search_directory", name="allow_read"),
        policy.allow("find_file", name="allow_read"),
        # Finish tool — always allowed
        policy.allow("finish", name="allow_finish"),
        # Test runner / linter — allowed in Plan mode (read-only verification)
        policy.allow("run_tests", name="allow_tests"),
        policy.allow("run_linter", name="allow_linter"),
        # Git read-only — allowed in Plan mode
        policy.allow("git_diff", name="allow_git_read"),
        policy.allow("git_status", name="allow_git_read"),
        policy.allow("git_log", name="allow_git_read"),
        # Everything else — hard deny (edit, shell, git mutations, etc.)
        policy.deny("*", name="deny_plan_mode"),
    ]


def make_ask_handler(
    session_id: str,
    confirmation_request: asyncio.Event,
    confirmation_response: asyncio.Event,
    pending_tool_call: dict[str, Any],
) -> AskUserHandler:
    """Create an ask_user handler bound to a specific session.

    The handler communicates with the UI through three shared objects:
      - confirmation_request (Event): set by handler to tell the UI there's
        a pending confirmation.
      - confirmation_response (Event): waited on by handler; set by UI
        after user decides.
      - pending_tool_call (dict): populated by handler with the tool call
        info for the UI to display.

    Returns an async callable compatible with policy.ask_user(handler=...).
    """

    async def _handler(tool_call: types.ToolCall) -> bool:
        args_summary = _format_tool_args(tool_call)
        pending_tool_call["name"] = str(tool_call.name)
        pending_tool_call["args"] = args_summary
        pending_tool_call["canonical_path"] = tool_call.canonical_path or ""

        logger.info(
            "ask_user triggered for session %s: %s(%s)",
            session_id,
            tool_call.name,
            args_summary,
        )

        confirmation_request.set()
        await confirmation_response.wait()

        approved = pending_tool_call.get("approved", False)
        logger.info(
            "ask_user result for session %s: %s (approved=%s)",
            session_id,
            tool_call.name,
            approved,
        )
        return approved

    return _handler


def _format_tool_args(tool_call: types.ToolCall) -> str:
    """Produce a human-readable summary of a tool call's arguments."""
    name = str(tool_call.name)
    args = tool_call.args

    if name == "run_command":
        return args.get("CommandLine", args.get("command_line", str(args)))
    if name in ("view_file", "create_file", "edit_file"):
        path = args.get("Path", args.get("path", ""))
        if name == "edit_file":
            old = args.get("OldText", args.get("old_text", ""))
            new = args.get("NewText", args.get("new_text", ""))
            return f"{path}: {old!r} → {new!r}"
        return path
    if name == "list_directory":
        return args.get("Path", args.get("path", ""))
    if name == "find_file":
        return args.get("Pattern", args.get("pattern", ""))
    if name == "search_directory":
        return args.get("Query", args.get("query", ""))
    # Custom tools
    if name == "run_tests":
        scope = args.get("scope", args.get("Scope"))
        return f"scope={scope}" if scope else "all tests"
    if name == "run_linter":
        return "lint"
    if name == "git_diff":
        path = args.get("path", args.get("Path"))
        return f"path={path}" if path else "full diff"
    if name == "git_status":
        return "status"
    if name == "git_log":
        n = args.get("n", args.get("N", 10))
        return f"last {n} commits"
    if name == "git_branch":
        bname = args.get("name", args.get("Name"))
        return f"create {bname}" if bname else "list branches"
    if name == "git_commit":
        msg = args.get("message", args.get("Message", ""))
        return f"message: {msg}"
    if name == "git_push":
        return "push to origin"
    if name == "open_pull_request":
        title = args.get("title", args.get("Title", ""))
        branch = args.get("branch", args.get("Branch", ""))
        return f"title={title}, branch={branch}"

    return str(args)[:200]
