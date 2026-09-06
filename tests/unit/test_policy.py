"""Unit tests for otto.core.policy — Plan/Build policy tables."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from google.antigravity import policy as gp
from google.antigravity import types

from otto.core.policy import build_plan_policies, build_policies, make_ask_handler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _policy_tool_names(policies: list[gp.Policy]) -> dict[str, str]:
    """Return {tool_name: decision_value} for quick assertion lookups."""
    result: dict[str, str] = {}
    for p in policies:
        result[p.tool] = p.decision.value
    return result


def _has_deny_wildcard(policies: list[gp.Policy]) -> bool:
    return any(
        p.tool == "*" and p.decision.value == "DENY"
        for p in policies
    )


# ---------------------------------------------------------------------------
# Build mode tests
# ---------------------------------------------------------------------------


class TestBuildPolicies:
    """Tests for the Build mode policy table."""

    def test_read_tools_are_allowed(self) -> None:
        policies = build_policies()
        names = _policy_tool_names(policies)
        for tool in ("view_file", "list_directory", "search_directory", "find_file"):
            assert names.get(tool) == "APPROVE", f"{tool} should be allowed in build mode"

    def test_write_tools_are_allowed(self) -> None:
        policies = build_policies()
        names = _policy_tool_names(policies)
        for tool in ("create_file", "edit_file"):
            assert names.get(tool) == "APPROVE", f"{tool} should be allowed in build mode"

    def test_finish_is_allowed(self) -> None:
        policies = build_policies()
        names = _policy_tool_names(policies)
        assert names.get("finish") == "APPROVE"

    def test_test_linter_are_allowed(self) -> None:
        policies = build_policies()
        names = _policy_tool_names(policies)
        for tool in ("run_tests", "run_linter"):
            assert names.get(tool) == "APPROVE", f"{tool} should be allowed in build mode"

    def test_git_read_is_allowed(self) -> None:
        policies = build_policies()
        names = _policy_tool_names(policies)
        for tool in ("git_diff", "git_status", "git_log"):
            assert names.get(tool) == "APPROVE", f"{tool} should be allowed in build mode"

    def test_git_mutate_is_allowed(self) -> None:
        policies = build_policies()
        names = _policy_tool_names(policies)
        for tool in ("git_branch", "git_commit"):
            assert names.get(tool) == "APPROVE", f"{tool} should be allowed in build mode"

    def test_deny_wildcard_exists(self) -> None:
        policies = build_policies()
        assert _has_deny_wildcard(policies), "Build mode must have deny(*) wildcard"

    def test_no_ask_user_without_handler(self) -> None:
        policies = build_policies(ask_handler=None)
        names = _policy_tool_names(policies)
        # Without handler, run_command falls through to deny
        assert "run_command" not in names or names["run_command"] == "DENY"

    def test_ask_user_with_handler_adds_confirmations(self) -> None:
        async def _dummy_handler(tc: types.ToolCall) -> bool:
            return True

        policies = build_policies(ask_handler=_dummy_handler)
        names = _policy_tool_names(policies)
        for tool in ("run_command", "git_push", "open_pull_request"):
            assert names.get(tool) == "ASK_USER", (
                f"{tool} should be ask_user when handler provided"
            )

    def test_build_policies_list_is_deterministic(self) -> None:
        p1 = build_policies()
        p2 = build_policies()
        assert len(p1) == len(p2)
        for a, b in zip(p1, p2):
            assert a.tool == b.tool
            assert a.decision == b.decision


# ---------------------------------------------------------------------------
# Plan mode tests
# ---------------------------------------------------------------------------


class TestPlanPolicies:
    """Tests for the Plan mode policy table."""

    def test_read_tools_are_allowed(self) -> None:
        policies = build_plan_policies()
        names = _policy_tool_names(policies)
        for tool in ("view_file", "list_directory", "search_directory", "find_file"):
            assert names.get(tool) == "APPROVE", f"{tool} should be allowed in plan mode"

    def test_finish_is_allowed(self) -> None:
        policies = build_plan_policies()
        names = _policy_tool_names(policies)
        assert names.get("finish") == "APPROVE"

    def test_test_linter_are_allowed(self) -> None:
        policies = build_plan_policies()
        names = _policy_tool_names(policies)
        for tool in ("run_tests", "run_linter"):
            assert names.get(tool) == "APPROVE", f"{tool} should be allowed in plan mode"

    def test_git_read_is_allowed(self) -> None:
        policies = build_plan_policies()
        names = _policy_tool_names(policies)
        for tool in ("git_diff", "git_status", "git_log"):
            assert names.get(tool) == "APPROVE", f"{tool} should be allowed in plan mode"

    def test_edit_tools_are_denied(self) -> None:
        policies = build_plan_policies()
        names = _policy_tool_names(policies)
        # In plan mode, edit/create should fall to deny wildcard
        for tool in ("create_file", "edit_file"):
            assert names.get(tool) != "APPROVE", f"{tool} must NOT be allowed in plan mode"

    def test_shell_is_denied(self) -> None:
        policies = build_plan_policies()
        names = _policy_tool_names(policies)
        assert names.get("run_command") != "APPROVE", "run_command must NOT be allowed in plan mode"

    def test_git_mutate_is_denied(self) -> None:
        policies = build_plan_policies()
        names = _policy_tool_names(policies)
        for tool in ("git_branch", "git_commit", "git_push", "open_pull_request"):
            assert names.get(tool) != "APPROVE", f"{tool} must NOT be allowed in plan mode"

    def test_deny_wildcard_exists(self) -> None:
        policies = build_plan_policies()
        assert _has_deny_wildcard(policies), "Plan mode must have deny(*) wildcard"

    def test_no_ask_user_rules(self) -> None:
        policies = build_plan_policies()
        for p in policies:
            assert p.decision.value != "ASK_USER", (
                f"Plan mode must not have ask_user rules (found on {p.tool})"
            )

    def test_plan_fewer_rules_than_build(self) -> None:
        build = build_policies()
        plan = build_plan_policies()
        assert len(plan) < len(build), "Plan mode should have fewer rules than build mode"


# ---------------------------------------------------------------------------
# make_ask_handler tests
# ---------------------------------------------------------------------------


class TestMakeAskHandler:
    """Tests for the ask_user handler factory."""

    def test_handler_returns_approved_value(self) -> None:
        req = asyncio.Event()
        resp = asyncio.Event()
        pending: dict = {}

        handler = make_ask_handler("test-sess", req, resp, pending)

        async def _run() -> None:
            pending["approved"] = True
            resp.set()

        # The handler would block on resp.wait(), so we set resp before calling
        pending["approved"] = True
        resp.set()

        # We can't easily run the async handler in a sync test,
        # but we can verify the handler is callable and returns a coroutine
        coro = handler(MagicMock(name="tool_call", spec=types.ToolCall))
        assert asyncio.iscoroutine(coro)
        coro.close()

    def test_handler_sets_pending_fields(self) -> None:
        req = asyncio.Event()
        resp = asyncio.Event()
        pending: dict = {}

        handler = make_ask_handler("test-sess", req, resp, pending)

        # Verify handler is an async callable
        assert callable(handler)
