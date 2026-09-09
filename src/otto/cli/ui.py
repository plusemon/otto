"""Textual TUI for otto.

Layout:
  ┌─ #top-bar (Horizontal, dock:top, height:3) ──────────────────────┐
  │ [ID: abc123] [Model: kilo-auto/free] [BUILD] [Msgs: 5]           │
  ├─ #main-body (Horizontal, 1fr) ───────────────────────────────────┤
  │ #sidebar (Vertical, w:30) │ #chat-timeline (VerticalScroll, 1fr) │
  │  SESSIONS                 │  [UserMessage cards]                  │
  │  [ListView]               │  [AgentMessage cards]                 │
  │                           │  [SystemMessage pills]                │
  ├─ #dock-container (Vertical, dock:bottom, auto) ──────────────────┤
  │ #cmd-suggestions (OptionList, display:none)                       │
  │ #prompt-input (Input)                                             │
  └───────────────────────────────────────────────────────────────────┘

Keybindings:
  Ctrl+B  Toggle sidebar visibility
  Ctrl+C  Quit

Slash commands:
  /plan, /build, /session new|list|switch|resume, /model, /quit
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Collapsible,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    Select,
    Static,
)

from ..core.agent_config import fetch_models
from ..core.session import (
    Session,
    SessionManager,
    SessionMode,
    SessionNotFoundForResume,
    StreamEvent,
)

logger = logging.getLogger("otto.ui")

# ---------------------------------------------------------------------------
# Slash command definitions
# ---------------------------------------------------------------------------

COMMANDS = [
    ("/plan", "Switch active session to Plan mode (read-only)"),
    ("/build", "Switch active session to Build mode (read-write)"),
    ("/session new", "Create a new chat session"),
    ("/session list", "List all sessions"),
    ("/session switch <id>", "Switch to a session by ID prefix"),
    ("/session resume <id>", "Resume a session from disk"),
    ("/model <name>", "Switch model for new sessions"),
    ("/name <id> <name>", "Name a session for easier identification"),
    ("/export [path]", "Export session transcript to Markdown"),
    ("/quit", "Exit otto"),
]

_USER_RE = re.compile(r"^/session(?:\s+(.*))?$", re.IGNORECASE)
_MODEL_RE = re.compile(r"^/model(?:\s+(.*))?$", re.IGNORECASE)
_NAME_RE = re.compile(r"^/name\s+(\S+)\s+(.+)$", re.IGNORECASE)
_PLAN_RE = re.compile(r"^/plan$", re.IGNORECASE)
_BUILD_RE = re.compile(r"^/build$", re.IGNORECASE)
_EXPORT_RE = re.compile(r"^/export(?:\s+(.*))?$", re.IGNORECASE)
_QUIT_CMDS = {"/quit", ":q"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_ts(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")


def _truncate(s: str, limit: int) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "\u2026"


def _compute_edit_diff(args_json: str) -> Text | None:
    """Extract OldText/NewText from edit_file args and compute a colored diff."""
    try:
        args = json.loads(args_json)
    except (json.JSONDecodeError, TypeError):
        return None

    path = args.get("Path", args.get("path", ""))
    old = args.get("OldText", args.get("old_text", ""))
    new = args.get("NewText", args.get("new_text", ""))

    if not old and not new:
        return None
    if old == new:
        return None

    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )

    if not diff_lines:
        return None

    result = Text()
    for line in diff_lines:
        if line.startswith(("+++", "---")):
            result.append(line + "\n", style="bold")
        elif line.startswith("@@"):
            result.append(line + "\n", style="cyan")
        elif line.startswith("+"):
            result.append(line + "\n", style="green")
        elif line.startswith("-"):
            result.append(line + "\n", style="red")
        else:
            result.append(line + "\n")
    return result


def _make_tool_summary(name: str, args_json: str) -> str:
    """Produce a short human-readable summary for a tool call's title."""
    try:
        args = json.loads(args_json)
    except (json.JSONDecodeError, TypeError):
        return f"{name}({args_json[:60]})"

    if name == "run_command":
        cmd = args.get("CommandLine", args.get("command_line", ""))
        return f"{name} {_truncate(cmd, 50)}"
    if name in ("view_file", "create_file", "edit_file"):
        path = args.get("Path", args.get("path", ""))
        return f"{name} {path}"
    if name == "list_directory":
        path = args.get("Path", args.get("path", ""))
        return f"{name} {path}"
    if name == "find_file":
        pattern = args.get("Pattern", args.get("pattern", ""))
        return f"{name} {pattern}"
    if name == "search_directory":
        query = args.get("Query", args.get("query", ""))
        return f"{name} {_truncate(query, 40)}"
    if name == "run_tests":
        scope = args.get("scope", args.get("Scope"))
        return f"{name} (all)" if not scope else f"{name} ({scope})"
    if name == "run_linter":
        return name
    if name == "git_diff":
        path = args.get("path", args.get("Path"))
        return f"{name} {path}" if path else f"{name} (full)"
    if name == "git_status":
        return name
    if name == "git_log":
        n = args.get("n", args.get("N", 10))
        return f"{name} (last {n})"
    if name == "git_commit":
        msg = args.get("message", args.get("Message", ""))
        return f"{name}: {_truncate(msg, 40)}"
    if name == "git_push":
        return name
    if name == "open_pull_request":
        title = args.get("title", args.get("Title", ""))
        return f"{name}: {_truncate(title, 40)}"
    return f"{name}({_truncate(args_json, 60)})"


# ---------------------------------------------------------------------------
# Message Card Widgets
# ---------------------------------------------------------------------------


class UserMessage(Vertical):
    """Card containing a user message with badge, styled border, and timestamp."""

    CSS = """
    UserMessage {
        background: #1c2128;
        border-left: thick #58a6ff;
        border-top: solid #30363d;
        border-right: solid #30363d;
        border-bottom: solid #30363d;
        padding: 1;
        margin-bottom: 1;
        height: auto;
        width: 100%;
    }
    UserMessage .msg-badge {
        text-style: bold;
        margin-bottom: 1;
        height: 1;
    }
    UserMessage .user-badge {
        color: #58a6ff;
    }
    UserMessage .msg-text {
        height: auto;
        width: 100%;
    }
    UserMessage .msg-timestamp {
        color: #6e7681;
        height: 1;
    }
    """

    def __init__(self, text: str, **kwargs) -> None:
        self._text = text
        self._ts = datetime.now(tz=timezone.utc)
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        ts_str = _format_ts(self._ts)
        yield Static(
            Text(f"\U0001f464 You  {ts_str}", style="bold #58a6ff"),
            classes="msg-badge user-badge",
            markup=False,
        )
        yield Static(self._text, classes="msg-text", markup=False)


class AgentMessage(Vertical):
    """Card containing an agent response with badge, styled border, and Markdown body.

    During streaming the body is a plain Static with a blinking cursor indicator.
    On completion it is swapped to a Markdown widget for syntax-highlighted rendering.
    """

    CSS = """
    AgentMessage {
        background: #161b22;
        border-left: thick #2ea043;
        border-top: solid #30363d;
        border-right: solid #30363d;
        border-bottom: solid #30363d;
        padding: 1;
        margin-bottom: 1;
        height: auto;
        max-height: 80;
        width: 100%;
    }
    AgentMessage .msg-badge {
        text-style: bold;
        margin-bottom: 1;
        height: 1;
    }
    AgentMessage .agent-badge {
        color: #3fb950;
    }
    AgentMessage .msg-body {
        height: auto;
        width: 100%;
    }
    AgentMessage Markdown {
        background: transparent;
        height: auto;
    }
    """

    def __init__(self, markdown_text: str = "", **kwargs) -> None:
        self._text = markdown_text
        self._ts = datetime.now(tz=timezone.utc)
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        ts_str = _format_ts(self._ts)
        yield Static(
            Text(f"\U0001f916 Otto  {ts_str}", style="bold #3fb950"),
            classes="msg-badge agent-badge",
            markup=False,
        )
        if self._text:
            yield Markdown(self._text, classes="msg-body")
        else:
            yield Static(
                Text("\u258c", style="streaming-cursor"),
                classes="msg-body",
                markup=False,
            )

    def append_delta(self, delta: str) -> None:
        """Append a streaming text delta to the live body.

        Shows a blinking cursor while waiting for the first token.
        Once text arrives, renders plain text until finalize() swaps to Markdown.
        """
        self._text += delta
        body = self.query_one(".msg-body")
        if isinstance(body, Static):
            if self._text:
                body.update(Text(self._text))
            else:
                body.update(Text("\u258c", style="streaming-cursor"))

    def finalize(self) -> None:
        """Swap the streaming Static body for a rendered Markdown widget."""
        body = self.query_one(".msg-body")
        if not isinstance(body, Markdown):
            body.remove()
            self.mount(Markdown(self._text, classes="msg-body"))


class SystemMessage(Static):
    """Compact muted info pill for system/status messages with timestamp."""

    CSS = """
    SystemMessage {
        color: #8b949e;
        height: auto;
        width: 100%;
        padding: 0 1;
        margin-bottom: 0;
    }
    """

    def __init__(self, text: str, **kwargs) -> None:
        ts_str = _format_ts(datetime.now(tz=timezone.utc))
        super().__init__(
            Text(f"  {ts_str}  {text}", style="dim #8b949e"), markup=False, **kwargs
        )


# ---------------------------------------------------------------------------
# ToolCard — collapsible tool execution card
# ---------------------------------------------------------------------------


class ToolCard(Vertical):
    """Collapsible tool call card with status badge, timing, and monospace output body.

    Title shows: ⚙️ Tool: <name> [RUNNING] / [DONE 0.3s] / [FAILED 1.2s]
    Body contains args and result inside a monospace Markdown code fence.
    """

    CSS = """
    ToolCard {
        margin: 0 0 1 0;
        width: 100%;
        height: auto;
    }
    ToolCard Collapsible {
        margin: 0;
    }
    ToolCard .tool-output {
        background: #0d1117;
        border: solid #30363d;
        padding: 0 1;
        min-height: 1;
        max-height: 20;
    }
    """

    def __init__(self, name: str, summary: str, args_json: str) -> None:
        self._tool_name = name
        self._summary = summary
        self._args_json = args_json
        self._status = "running"
        self._start_time = time.monotonic()
        self._end_time: float | None = None
        super().__init__(id=f"tool-card-{name}-{id(self)}")

    def compose(self) -> ComposeResult:
        detail = Static(
            Text("waiting\u2026", style="dim #8b949e"),
            classes="tool-output",
            markup=False,
        )
        yield Collapsible(
            detail,
            title=self._title_text(),
            collapsed=True,
        )

    def _title_text(self) -> str:
        emoji = {"running": "\u23f3", "done": "\u2705", "failed": "\u274c"}.get(
            self._status, "\u23f3"
        )
        badge = self._status.upper()
        if self._end_time is not None:
            duration = self._end_time - self._start_time
            if duration >= 1.0:
                time_str = f" {duration:.1f}s"
            else:
                time_str = f" {duration * 1000:.0f}ms"
        else:
            elapsed = time.monotonic() - self._start_time
            if elapsed >= 1.0:
                time_str = f" {elapsed:.1f}s"
            else:
                time_str = ""
        return f"{emoji} Tool: {self._summary}  [{badge}{time_str}]"

    def set_result(self, result_text: str) -> None:
        """Update the card with the tool's result and diff (for edits)."""
        self._end_time = time.monotonic()
        content = Text()
        content.append(
            f"Args: {_truncate(self._args_json, 300)}\n",
            style="dim #8b949e",
        )

        if self._tool_name in ("edit_file", "create_file"):
            diff = _compute_edit_diff(self._args_json)
            if diff:
                content.append_text(diff)
                content.append("\n")

        if result_text:
            content.append(f"Result: {result_text}", style="bold #3fb950")

        detail = self.query_one(Collapsible).query_one(Static)
        detail.update(content)
        self._status = "done"
        self.query_one(Collapsible).title = self._title_text()

    def set_error(self, error_text: str) -> None:
        """Mark the card as failed and auto-expand to show the error."""
        self._end_time = time.monotonic()
        detail = self.query_one(Collapsible).query_one(Static)
        detail.update(Text(f"Error: {error_text}", style="bold #f85149"))
        self._status = "failed"
        collapsible = self.query_one(Collapsible)
        collapsible.title = self._title_text()
        collapsible.expanded = True


# ---------------------------------------------------------------------------
# CommandSuggestionList — dynamic autocomplete for slash commands
# ---------------------------------------------------------------------------


class CommandSuggestions(Static):
    """Display-only list of matching slash commands. Hidden by default.

    Navigation is handled by the Input widget (Up/Down/Enter).
    """

    CSS = """
    CommandSuggestions {
        display: none;
        max-height: 8;
        margin-bottom: 1;
        background: #21262d;
        border: solid #388bfd;
        padding: 0 1;
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="cmd-suggestions")

    def update_matches(self, matches: list[str], selected: int = -1) -> None:
        """Render the list of matching commands with the selected one highlighted."""
        if not matches:
            self.display = False
            return
        lines = Text()
        for i, cmd in enumerate(matches):
            if i == selected:
                lines.append(f" \u25b6 {cmd}\n", style="bold #f0f6fc on #388bfd")
            else:
                lines.append(f"   {cmd}\n", style="#c9d1d9")
        self.update(lines)
        self.display = True

    def hide(self) -> None:
        self.display = False


# ---------------------------------------------------------------------------
# SessionSidebar
# ---------------------------------------------------------------------------


class SessionSidebar(Vertical):
    """Sidebar listing all sessions (active + resumable on disk) with search filter.

    Emits SessionSelected messages on selection for decoupled navigation.
    """

    class SessionSelected(Message):
        """Posted when a session is selected from the sidebar."""

        def __init__(self, session_id: str, is_disk: bool) -> None:
            super().__init__()
            self.session_id = session_id
            self.is_disk = is_disk

    CSS = """
    SessionSidebar {
        width: 30;
        background: #161b22;
        border-right: solid #21262d;
        padding: 1;
    }
    #sidebar-title {
        text-style: bold;
        color: #8b949e;
        margin-bottom: 1;
        height: 1;
    }
    #sidebar-filter {
        height: 3;
        margin-bottom: 1;
    }
    #sidebar-filter Input {
        height: 3;
        background: #0d1117;
        border: solid #30363d;
        color: #c9d1d9;
    }
    #sidebar-filter Input:focus {
        border: solid #58a6ff;
    }
    #session-list {
        height: 1fr;
    }
    #session-list > .list-item {
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("SESSIONS", id="sidebar-title")
        yield Input(placeholder="Filter sessions...", id="sidebar-filter")
        yield ListView(id="session-list")

    def on_mount(self) -> None:
        self._session_ids: list[tuple[str, bool]] = []
        self._filter_text: str = ""
        self._refresh()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "sidebar-filter":
            self._filter_text = event.value.strip().lower()
            self._refresh()

    def refresh_sessions(self) -> None:
        """Rebuild the session list from SessionManager state."""
        self._refresh()

    def _matches_filter(self, label_text: str) -> bool:
        """Check if a session label matches the current filter text."""
        if not self._filter_text:
            return True
        return self._filter_text in label_text.lower()

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        list_view = self.query_one("#session-list", ListView)
        list_view.clear()
        self._session_ids = []

        app: OttoUI = self.app  # type: ignore[assignment]
        active = app.mgr.active
        active_id = active.id if active else None
        sessions = app.mgr.list_sessions()
        disk = app.mgr.disk_sessions()

        if not sessions and not disk:
            list_view.append(
                ListItem(
                    Static(
                        Text("  (no sessions)", style="dim #8b949e"),
                        markup=False,
                    )
                )
            )
            return

        for s in sessions:
            is_current = s.id == active_id
            label = Text()
            marker = " \u25cf " if is_current else "   "
            label.append(
                marker,
                style="bold #58a6ff" if is_current else "",
            )
            label.append(
                f"{s.id}",
                style=(
                    "bold #c9d1d9" if is_current else "dim #8b949e"
                ),
            )
            label.append(" ")
            mode_color = "#3fb950" if s.mode == SessionMode.BUILD else "#d29922"
            label.append(f"{s.mode.value.upper()}", style=f"bold {mode_color}")
            if s.name:
                label.append(f"  \"{s.name}\"", style="italic #c9d1d9")
            label.append(f"  {s.message_count()}m", style="dim #8b949e")

            label_str = str(label.plain)
            if not self._matches_filter(label_str):
                continue

            item = ListItem(Static(label, markup=False))
            list_view.append(item)
            self._session_ids.append((s.id, False))

        if disk:
            sep_label = Text(
                "  \u2500\u2500 on disk \u2500\u2500",
                style="dim #8b949e",
            )
            list_view.append(ListItem(Static(sep_label, markup=False)))
            self._session_ids.append(("", False))

            for d in disk:
                label = Text()
                label.append(f"   {d['id']} ", style="dim #8b949e")
                mode_color = "#3fb950" if d["mode"] == "build" else "#d29922"
                label.append(f"{d['mode'].upper()}", style=f"bold {mode_color}")
                if d.get("name"):
                    label.append(f"  \"{d['name']}\"", style="italic #c9d1d9")
                label.append(
                    f"  {d['conversation_count']}c", style="dim #8b949e"
                )

                label_str = str(label.plain)
                if not self._matches_filter(label_str):
                    continue

                item = ListItem(Static(label, markup=False))
                list_view.append(item)
                self._session_ids.append((d["id"], True))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.index
        if idx >= len(self._session_ids):
            return
        sid, is_disk = self._session_ids[idx]
        if not sid:
            return
        self.post_message(self.SessionSelected(sid, is_disk))


# ---------------------------------------------------------------------------
# ConfirmScreen
# ---------------------------------------------------------------------------


class ConfirmScreen(ModalScreen[bool]):
    """Modal dialog asking the user to approve or deny a tool call."""

    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #confirm-dialog {
        width: 70;
        max-height: 30;
        border: solid #d29922;
        padding: 1 2;
        background: #161b22;
    }
    #confirm-title {
        text-style: bold;
        color: #d29922;
        margin-bottom: 1;
    }
    #confirm-body {
        margin-bottom: 1;
        color: #c9d1d9;
    }
    #confirm-buttons {
        layout: horizontal;
        height: 3;
        align: center middle;
    }
    #confirm-buttons Button {
        margin: 0 2;
    }
    """

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="confirm-dialog"):
            yield Static(self._title, id="confirm-title")
            yield Static(self._body, id="confirm-body")
            with Static(id="confirm-buttons"):
                yield Button("Approve [Y]", id="approve", variant="success")
                yield Button("Deny [N]", id="deny", variant="error")

    def on_mount(self) -> None:
        self.query_one("#approve", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.dismiss(True)
        elif event.button.id == "deny":
            self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key == "y":
            event.prevent_default()
            self.dismiss(True)
        elif event.key == "n" or event.key == "escape":
            event.prevent_default()
            self.dismiss(False)


# ---------------------------------------------------------------------------
# ModelPickerScreen
# ---------------------------------------------------------------------------


class ModelPickerScreen(ModalScreen[str]):
    """Full-screen modal with a Select dropdown to pick a model."""

    CSS = """
    ModelPickerScreen {
        align: center middle;
    }
    #model-picker {
        width: 80;
        max-height: 40;
        border: solid #3fb950;
        padding: 1 2;
        background: #161b22;
    }
    #model-select {
        height: 1fr;
    }
    """

    def __init__(self, models: list[tuple[str, str]], current_model: str) -> None:
        super().__init__()
        self._models = models
        self._current_model = current_model

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="model-picker"):
            yield Select(
                self._models,
                prompt="Select a model\u2026",
                id="model-select",
                type_to_search=True,
            )

    def on_mount(self) -> None:
        sel = self.query_one("#model-select", Select)
        for label, model_id in self._models:
            if model_id == self._current_model:
                sel.value = model_id
                break
        sel.focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value is not Select.NULL:
            self.dismiss(str(event.value))


# ---------------------------------------------------------------------------
# OttoUI — Main Application
# ---------------------------------------------------------------------------


class OttoUI(App):
    """Main TUI application for otto.

    Composes:
      #top-bar (status badges)
      #main-body = #sidebar + #chat-timeline
      #dock-container = #cmd-suggestions + #prompt-input
    """

    CSS = """
    Screen {
        background: #0f141c;
        color: #c9d1d9;
    }

    /* ── Scrollbar styling ── */
    VerticalScroll VerticalScroll {
        scrollbar-color: #58a6ff #21262d;
        scrollbar-color-hover: #79c0ff #30363d;
        scrollbar-color-active: #a5d6ff #388bfd;
    }

    /* ── Focus indicators ── */
    ListView:focus {
        border: solid #58a6ff;
    }
    Input:focus {
        border: solid #79c0ff;
    }
    Button:focus {
        outline: solid #58a6ff;
    }

    /* ── Top status bar ── */
    #top-bar {
        dock: top;
        height: 3;
        background: #161b22;
        border-bottom: solid #21262d;
        padding: 0 1;
        align-vertical: middle;
    }
    .pill {
        margin-right: 2;
        text-style: bold;
    }
    .pill-accent { color: #58a6ff; }
    .pill-success { color: #3fb950; }
    .pill-muted { color: #8b949e; }

    /* ── Main body (sidebar + timeline) ── */
    #main-body {
        height: 1fr;
    }

    /* ── Sidebar ── */
    #sidebar {
        width: 30;
        background: #161b22;
        border-right: solid #21262d;
        padding: 1;
    }
    #sidebar-title {
        text-style: bold;
        color: #8b949e;
        margin-bottom: 1;
    }
    #sidebar-filter {
        margin-bottom: 1;
        height: 3;
    }
    #session-list {
        height: 1fr;
    }

    /* ── Chat timeline ── */
    #chat-timeline {
        padding: 1 2;
        height: 1fr;
        background: #0f141c;
    }

    /* ── Scroll buttons ── */
    #scroll-buttons {
        dock: right;
        width: 3;
        height: auto;
        padding: 0;
        margin: 0;
    }
    #scroll-buttons Button {
        width: 3;
        height: 1;
        margin: 0 0 0 0;
        background: #21262d;
        border: solid #30363d;
        color: #8b949e;
        min-width: 3;
    }
    #scroll-buttons Button:hover {
        background: #30363d;
        color: #c9d1d9;
    }

    /* ── Streaming cursor ── */
    .streaming-cursor {
        color: #3fb950;
        text-style: bold;
    }

    /* ── Bottom dock ── */
    #dock-container {
        dock: bottom;
        background: #161b22;
        border-top: solid #21262d;
        padding: 1;
        height: auto;
    }
    #cmd-suggestions {
        display: none;
        max-height: 8;
        margin-bottom: 1;
        background: #21262d;
        border: solid #388bfd;
    }
    #prompt-input {
        background: #0d1117;
        border: solid #388bfd;
        color: #f0f6fc;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar", show=True),
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+left", "shrink_sidebar", "Shrink Sidebar", show=True),
        Binding("ctrl+right", "grow_sidebar", "Grow Sidebar", show=True),
    ]

    _MIN_SIDEBAR_WIDTH = 15
    _MAX_SIDEBAR_WIDTH = 60
    _SIDEBAR_STEP = 5

    def __init__(self, manager: SessionManager) -> None:
        super().__init__()
        self.mgr = manager
        self.output: ChatTimeline
        self.sidebar: SessionSidebar
        self.input_bar: Input
        self.cmd_suggestions: CommandSuggestions
        self._busy = False
        self._cmd_matches: list[str] = []
        self._suggestion_index: int = -1
        self._cmd_history: list[str] = []
        self._history_index: int = -1
        self._total_tokens_in: int = 0
        self._total_tokens_out: int = 0

    def compose(self) -> ComposeResult:
        # ── Top status bar ──
        with Horizontal(id="top-bar"):
            yield Label("[ID: ---]", classes="pill pill-accent", id="top-id")
            yield Label("[---]", classes="pill pill-success", id="top-mode")
            yield Label("[Msgs: 0]", classes="pill pill-muted", id="top-msgs")
            yield Label("[Tokens: 0]", classes="pill pill-muted", id="top-tokens")
            yield Label("[Model: ---]", classes="pill pill-muted", id="top-model")

        # ── Main body: sidebar + chat timeline ──
        with Horizontal(id="main-body"):
            with SessionSidebar(id="sidebar"):
                pass
            with ChatTimeline(id="chat-timeline"):
                pass

        # ── Bottom dock: suggestions + input ──
        with Vertical(id="dock-container"):
            yield CommandSuggestions()
            yield Input(
                placeholder="Type a message or / for commands \u00b7 Ctrl+B sidebar \u00b7 Ctrl+C quit",
                id="prompt-input",
            )

    async def on_mount(self) -> None:
        self.input_bar = self.query_one("#prompt-input", Input)
        self.output = self.query_one(ChatTimeline)
        self.sidebar = self.query_one(SessionSidebar)
        self.cmd_suggestions = self.query_one(CommandSuggestions)
        self.input_bar.focus()

        sess = await self.mgr.ensure_active()
        self._refresh_status()
        self._refresh_sidebar()
        self.output.write_system(
            f"active session {sess.id} \u00b7 "
            f"model {self.mgr.config.model} \u00b7 "
            f"base {self.mgr.config.base_url}"
        )
        self.output.write_system("type / for commands. /quit to exit.")

    # ------------------------------------------------------------------
    # Status bar refresh
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        active = self.mgr.active
        id_label = self.query_one("#top-id", Label)
        model_label = self.query_one("#top-model", Label)
        mode_label = self.query_one("#top-mode", Label)
        msgs_label = self.query_one("#top-msgs", Label)
        tokens_label = self.query_one("#top-tokens", Label)

        if active is None:
            id_label.update("[ID: ---]")
            model_label.update("[Model: ---]")
            mode_label.update("[---]")
            msgs_label.update("[Msgs: 0]")
            tokens_label.update("[Tokens: 0]")
            return

        id_display = active.id
        if active.name:
            id_display = f"{active.id} \"{active.name}\""
        id_label.update(f"[ID: {id_display}]")
        model_label.update(f"[Model: {self.mgr.model}]")
        mode_color = "#3fb950" if active.mode == SessionMode.BUILD else "#d29922"
        mode_label.update(f"[{active.mode.value.upper()}]")
        mode_label.styles.color = mode_color
        msgs_label.update(f"[Msgs: {active.message_count()}]")

        if self._total_tokens_in or self._total_tokens_out:
            tokens_label.update(
                f"[Tokens: {self._total_tokens_in} in / {self._total_tokens_out} out]"
            )
        else:
            tokens_label.update("[Tokens: 0]")

    def _refresh_sidebar(self) -> None:
        if hasattr(self, "sidebar") and self.sidebar is not None:
            self.sidebar.refresh_sessions()

    def _reset_for_new_active_session(
        self, banner: str, replay_messages: list | None = None
    ) -> None:
        """Reset the chat timeline when the active session changes.

        Finalizes any in-progress streaming agent card (so its body is
        rendered as Markdown), clears the timeline, and writes a banner
        so the user can see the new session is loaded. If replay_messages
        is provided, renders the persisted history after the banner.
        Then re-enables sticky-bottom scrolling for the fresh viewport.
        """
        if self._busy:
            # An in-flight turn is being abandoned; mark its UI state as
            # finished so the next session starts with a clean slate.
            self._busy = False
        if self.output is not None:
            self.output.end_assistant()
            self.output.clear_timeline()
            self.output.write_system(banner)
            if replay_messages:
                self.output.replay_history(replay_messages)
            # A new viewport is being shown — assume the user wants to
            # see the bottom of the freshly cleared timeline.
            self.output.reset_sticky_bottom()
            self.output.scroll_end(animate=False)
        self._refocus_input()

    # ------------------------------------------------------------------
    # Sidebar message handling
    # ------------------------------------------------------------------

    def on_session_sidebar_session_selected(
        self, event: SessionSidebar.SessionSelected
    ) -> None:
        sid = event.session_id
        is_disk = event.is_disk
        if is_disk:
            self.run_worker(self._resume_session_from_sidebar(sid))
        else:
            if self.mgr.active and self.mgr.active.id == sid:
                self.output.write_system(f"already on {sid}")
                return
            try:
                self.mgr.switch(sid)
            except Exception as e:  # noqa: BLE001
                self.output.write_error(str(e))
                return
            self._reset_for_new_active_session(f"[switched to {sid}]")
            self._refresh_status()
            self._refresh_sidebar()

    # ------------------------------------------------------------------
    # Actions (keybindings)
    # ------------------------------------------------------------------

    def action_toggle_sidebar(self) -> None:
        """Toggle sidebar visibility with Ctrl+B."""
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display

    def action_shrink_sidebar(self) -> None:
        """Shrink sidebar width with Ctrl+Left."""
        sidebar = self.query_one("#sidebar")
        if sidebar.display:
            current = sidebar.styles.width
            if current is not None:
                new_w = max(self._MIN_SIDEBAR_WIDTH, int(current.value) - self._SIDEBAR_STEP)
                sidebar.styles.width = new_w

    def action_grow_sidebar(self) -> None:
        """Grow sidebar width with Ctrl+Right."""
        sidebar = self.query_one("#sidebar")
        if sidebar.display:
            current = sidebar.styles.width
            if current is not None:
                new_w = min(self._MAX_SIDEBAR_WIDTH, int(current.value) + self._SIDEBAR_STEP)
                sidebar.styles.width = new_w

    def action_scroll_to_bottom(self) -> None:
        """Scroll the chat timeline to the bottom."""
        self.output.scroll_end(animate=False)
        self.output.reset_sticky_bottom()

    def action_scroll_to_top(self) -> None:
        """Scroll the chat timeline to the top."""
        self.output.scroll_home(animate=False)
        self.output._sticky_bottom = False

    def _refocus_input(self) -> None:
        """Re-focus the input bar after any action."""
        if hasattr(self, "input_bar") and self.input_bar is not None:
            self.input_bar.focus()

    # ------------------------------------------------------------------
    # Dynamic autocomplete for slash commands
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        val = event.value.strip()
        if val.startswith("/"):
            self._cmd_matches = [
                f"{cmd}  -  {desc}"
                for cmd, desc in COMMANDS
                if cmd.startswith(val)
            ]
            self._suggestion_index = -1
            self.cmd_suggestions.update_matches(self._cmd_matches)
        else:
            self._cmd_matches = []
            self._suggestion_index = -1
            self.cmd_suggestions.hide()

    def _accept_suggestion(self) -> None:
        """Insert the currently highlighted suggestion into the input."""
        if 0 <= self._suggestion_index < len(self._cmd_matches):
            cmd_text = self._cmd_matches[self._suggestion_index].split("  -  ")[0]
            self.input_bar.value = cmd_text
        self._cmd_matches = []
        self._suggestion_index = -1
        self.cmd_suggestions.hide()

    # ------------------------------------------------------------------
    # Input handling & key events
    # ------------------------------------------------------------------

    def on_key(self, event) -> None:
        # Tab completion for commands
        if event.key == "tab" and self._cmd_matches:
            event.prevent_default()
            if self._suggestion_index < len(self._cmd_matches) - 1:
                self._suggestion_index += 1
            else:
                self._suggestion_index = 0
            self.cmd_suggestions.update_matches(
                self._cmd_matches, self._suggestion_index
            )
            cmd_text = self._cmd_matches[self._suggestion_index].split("  -  ")[0]
            self.input_bar.value = cmd_text
            return

        if self._cmd_matches:
            if event.key == "down":
                self._suggestion_index = min(
                    self._suggestion_index + 1, len(self._cmd_matches) - 1
                )
                self.cmd_suggestions.update_matches(
                    self._cmd_matches, self._suggestion_index
                )
            elif event.key == "up":
                self._suggestion_index = max(self._suggestion_index - 1, -1)
                self.cmd_suggestions.update_matches(
                    self._cmd_matches, self._suggestion_index
                )
            elif event.key == "enter" and self._suggestion_index >= 0:
                event.prevent_default()
                self._accept_suggestion()
            elif event.key == "escape":
                self._cmd_matches = []
                self._suggestion_index = -1
                self.cmd_suggestions.hide()
            return

        # Command history navigation when no suggestions shown
        if self._cmd_history:
            if event.key == "up":
                if self._history_index < len(self._cmd_history) - 1:
                    self._history_index += 1
                    self.input_bar.value = self._cmd_history[
                        -(self._history_index + 1)
                    ]
            elif event.key == "down":
                if self._history_index > 0:
                    self._history_index -= 1
                    self.input_bar.value = self._cmd_history[
                        -(self._history_index + 1)
                    ]
                elif self._history_index == 0:
                    self._history_index = -1
                    self.input_bar.value = ""

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.input_bar.value = ""
        self._cmd_matches = []
        self._suggestion_index = -1
        self.cmd_suggestions.hide()
        self._history_index = -1
        if not text:
            return
        if text:
            self._cmd_history.append(text)
        if text.lower() in _QUIT_CMDS:
            await self._shutdown_and_exit()
            return
        if text.startswith("/"):
            await self._handle_command(text)
            return
        await self._submit_user_message(text)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    async def _handle_command(self, text: str) -> None:
        if _PLAN_RE.match(text):
            await self._switch_mode(SessionMode.PLAN)
            return
        if _BUILD_RE.match(text):
            await self._switch_mode(SessionMode.BUILD)
            return
        mm = _MODEL_RE.match(text)
        if mm:
            arg = (mm.group(1) or "").strip()
            if not arg:
                await self._show_model_picker()
                return
            self.mgr.set_model(arg)
            self.output.write_system(
                f"[model set to {arg} for new sessions]"
            )
            return
        nm = _NAME_RE.match(text)
        if nm:
            id_prefix = nm.group(1).strip()
            name = nm.group(2).strip()
            try:
                sess = self.mgr.switch(id_prefix)
            except Exception as e:  # noqa: BLE001
                self.output.write_error(str(e))
                return
            sess.name = name
            self.output.write_system(f"[session {sess.id} named \"{name}\"]")
            self._refresh_sidebar()
            self._refresh_status()
            return
        em = _EXPORT_RE.match(text)
        if em:
            self._export_session((em.group(1) or "").strip())
            return
        m = _USER_RE.match(text)
        if not m:
            logger.warning("Unknown command: %s", text.split()[0])
            self.output.write_error(
                f"unknown command: {text.split()[0]}. "
                "try /session ... or /quit"
            )
            return
        arg = (m.group(1) or "").strip()
        if not arg:
            self.output.write_error(
                "usage: /session <new|list|switch|resume <id>>"
            )
            return
        parts = arg.split(maxsplit=1)
        verb = parts[0].lower()
        if verb == "new":
            sess = await self.mgr.new_session()
            self._reset_for_new_active_session(f"[session {sess.id} created]")
            self._refresh_status()
            self._refresh_sidebar()
            return
        if verb == "list":
            self._render_session_list()
            return
        if verb == "switch":
            if len(parts) < 2 or not parts[1].strip():
                self.output.write_error("usage: /session switch <id-prefix>")
                return
            if (
                self.mgr.active is not None
                and self.mgr.active.id.startswith(parts[1])
            ):
                self.output.write_system(
                    f"already on {self.mgr.active.id}"
                )
                return
            try:
                sess = self.mgr.switch(parts[1])
            except Exception as e:  # noqa: BLE001
                logger.error("Error switching session: %s", e)
                self.output.write_error(str(e))
                return
            self._reset_for_new_active_session(f"[switched to {sess.id}]")
            self._refresh_status()
            self._refresh_sidebar()
            return
        if verb == "resume":
            if len(parts) < 2 or not parts[1].strip():
                self.output.write_error("usage: /session resume <id-prefix>")
                return
            self.output.write_system(f"resuming session {parts[1]}...")
            try:
                sess = await self.mgr.resume_session(parts[1])
            except SessionNotFoundForResume as e:
                self.output.write_error(str(e))
                return
            except Exception as e:  # noqa: BLE001
                logger.error("Error resuming session: %s", e)
                self.output.write_error(f"failed to resume: {e}")
                return
            self._reset_for_new_active_session(
                f"[resumed {sess.id} \u00b7 mode {sess.mode.value} \u00b7 "
                f"{sess.message_count()} msgs]",
                replay_messages=sess.messages if sess.messages else None,
            )
            self._refresh_status()
            self._refresh_sidebar()
            return
        self.output.write_error(
            f"unknown /session subcommand: {verb}. "
            "try: new | list | switch <id> | resume <id>"
        )

    async def _resume_session_from_sidebar(self, sid: str) -> None:
        """Resume a session triggered from sidebar click."""
        self.output.write_system(f"resuming session {sid}...")
        try:
            sess = await self.mgr.resume_session(sid)
        except SessionNotFoundForResume as e:
            self.output.write_error(str(e))
            return
        except Exception as e:  # noqa: BLE001
            logger.error("Error resuming session from sidebar: %s", e)
            self.output.write_error(f"failed to resume: {e}")
            return
        self._reset_for_new_active_session(
            f"[resumed {sess.id} \u00b7 mode {sess.mode.value} \u00b7 "
            f"{sess.message_count()} msgs]",
            replay_messages=sess.messages if sess.messages else None,
        )
        self._refresh_status()
        self._refresh_sidebar()

    async def _show_model_picker(self) -> None:
        models = fetch_models()
        if not models:
            self.output.write_error("failed to fetch models from gateway")
            return
        options = [
            (f"{'[free] ' if m.is_free else ''}{m.name}", m.id)
            for m in models
        ]
        picker = ModelPickerScreen(options, self.mgr.model)
        self.push_screen(picker, self._on_model_picked)

    async def _switch_mode(self, new_mode: SessionMode) -> None:
        sess = await self.mgr.ensure_active()
        if sess.mode == new_mode:
            self.output.write_system(f"already in {new_mode.value} mode")
            return
        self.output.write_system(f"switching to {new_mode.value} mode...")
        try:
            await sess.switch_mode(new_mode)
        except Exception as e:  # noqa: BLE001
            logger.error("Error switching mode: %s", e)
            self.output.write_error(f"failed to switch mode: {e}")
            return
        self.output.write_system(f"[mode \u2192 {new_mode.value}]")
        if new_mode == SessionMode.PLAN:
            self.output.write_system(
                "plan output will be saved to .otto/plans/"
            )
        self._refresh_status()
        self._refresh_sidebar()
        self._refocus_input()

    def _on_model_picked(self, result: str | None) -> None:
        if result is not None:
            self.mgr.set_model(result)
            self.output.write_system(
                f"[model set to {result} for new sessions]"
            )

    # ------------------------------------------------------------------
    # Session list (text-based, for /session list command)
    # ------------------------------------------------------------------

    def _render_session_list(self) -> None:
        sessions = self.mgr.list_sessions()
        disk_only = self.mgr.disk_sessions()
        if not sessions and not disk_only:
            self.output.write_system("(no sessions)")
            return
        active = self.mgr.active
        active_id = active.id if active else None
        if sessions:
            self.output.write_system("\u2500\u2500 active \u2500\u2500")
            self.output.write_system(
                f"{'id':<10} {'mode':<6} {'name':<20} {'created':<10} {'#msgs':>5}  last active"
            )
            for s in sessions:
                marker = " *" if s.id == active_id else "  "
                name_display = f'"{s.name}"' if s.name else ""
                self.output.write_system(
                    f"{s.id}{marker}  "
                    f"{s.mode.value:<6} "
                    f"{name_display:<20} "
                    f"{_format_ts(s.created_at)}   "
                    f"{s.message_count():>5}  "
                    f"{_format_ts(s.last_activity)}"
                )
        if disk_only:
            self.output.write_system(
                "\u2500\u2500 resumable (on disk) \u2500\u2500"
            )
            self.output.write_system(
                f"{'id':<10} {'mode':<6} {'name':<20} {'created':<10} {'#conv':>5}  last active"
            )
            for d in disk_only:
                created = datetime.fromisoformat(d["created_at"])
                last = datetime.fromisoformat(d["last_active"])
                name_display = f'"{d["name"]}"' if d.get("name") else ""
                self.output.write_system(
                    f"{d['id']}  "
                    f"{d['mode']:<6} "
                    f"{name_display:<20} "
                    f"{_format_ts(created)}   "
                    f"{d['conversation_count']:>5}  "
                    f"{_format_ts(last)}"
                )

    # ------------------------------------------------------------------
    # Export session transcript
    # ------------------------------------------------------------------

    def _export_session(self, path: str) -> None:
        """Export the current session's messages to a Markdown file."""
        import pathlib

        active = self.mgr.active
        if active is None:
            self.output.write_error("no active session to export")
            return

        if not path:
            safe_name = active.name.replace(" ", "_") if active.name else active.id
            path = f"otto-export-{safe_name}.md"

        out = pathlib.Path(path)
        lines = [
            "# Otto Session Export",
            "",
            f"- **Session ID**: {active.id}",
            f"- **Mode**: {active.mode.value}",
            f"- **Model**: {self.mgr.model}",
            f"- **Created**: {active.created_at.isoformat()}",
            f"- **Messages**: {active.message_count()}",
            "",
            "---",
            "",
        ]

        for msg in active.messages:
            ts_str = msg.ts.strftime("%Y-%m-%d %H:%M:%S")
            if msg.role == "user":
                lines.append(f"## You ({ts_str})")
                lines.append("")
                lines.append(msg.content)
                lines.append("")
            elif msg.role == "assistant":
                lines.append(f"## Otto ({ts_str})")
                lines.append("")
                lines.append(msg.content)
                lines.append("")
            lines.append("---")
            lines.append("")

        try:
            out.write_text("\n".join(lines), encoding="utf-8")
            self.output.write_system(f"[exported session to {out}]")
        except OSError as e:
            self.output.write_error(f"failed to export: {e}")

    # ------------------------------------------------------------------
    # Message submission & turn draining
    # ------------------------------------------------------------------

    async def _submit_user_message(self, text: str) -> None:
        if self._busy:
            logger.debug("Busy with current turn, rejecting input")
            self.output.write_error("busy with current turn; please wait")
            return
        sess = await self.mgr.ensure_active()
        self.output.write_user(text)
        self.output.start_assistant()
        self._busy = True
        self._refresh_status()
        await sess.enqueue(text)
        self.run_worker(
            self._drain_turn(sess),
            exclusive=False,
            name=f"turn-{sess.id}",
        )

    async def _drain_turn(self, sess: Session) -> None:
        try:
            while True:
                ev: StreamEvent = await sess.event_queue.get()
                if ev.session_id != sess.id:
                    continue
                try:
                    if ev.kind == "delta":
                        self.output.append_assistant_delta(ev.text)
                    elif ev.kind == "thinking":
                        self.output.write_thinking(ev.text)
                    elif ev.kind == "tool_call":
                        self.output.add_tool_call(ev.tool_name, ev.tool_args)
                    elif ev.kind == "tool_result":
                        self.output.add_tool_result(
                            ev.tool_name, ev.tool_result_text
                        )
                    elif ev.kind == "usage":
                        self._total_tokens_in += ev.tokens_in
                        self._total_tokens_out += ev.tokens_out
                        self.output.write_usage(
                            ev.tokens_in,
                            ev.tokens_out,
                            ev.tokens_thoughts,
                            ev.tokens_total,
                        )
                        self._refresh_status()
                    elif ev.kind == "confirmation_request":
                        await self._handle_confirmation(sess, ev)
                    elif ev.kind == "done":
                        self.output.end_assistant()
                        return
                    elif ev.kind == "error":
                        logger.error(
                            "Stream error from session %s: %s",
                            sess.id,
                            ev.error,
                        )
                        self.output.end_assistant()
                        self.output.write_error(ev.error)
                        return
                except Exception as handler_err:
                    # A bug in a single event handler must not corrupt
                    # the rest of the turn. Log, surface to the user,
                    # and continue draining so the session can finish.
                    logger.exception(
                        "Error handling %s event in session %s",
                        ev.kind,
                        sess.id,
                    )
                    self.output.write_error(
                        f"internal error handling {ev.kind} event: "
                        f"{handler_err}"
                    )
        finally:
            # Always leave the timeline in a clean state: if the turn
            # ended via an exception (e.g. session was abandoned on
            # switch), finalize any in-progress streaming card so the
            # next session does not append to a stale widget.
            self.output.end_assistant()
            self._busy = False
            try:
                self.mgr.update_activity(sess.id)
            except Exception:
                logger.exception("Failed to update activity for %s", sess.id)
            self._refresh_status()
            self._refresh_sidebar()
            self._refocus_input()

    # ------------------------------------------------------------------
    # Confirmation modal (ask_user flow)
    # ------------------------------------------------------------------

    async def _handle_confirmation(
        self, sess: Session, ev: StreamEvent
    ) -> None:
        """Show confirmation modal and resolve the session's pending ask_user."""
        tool_name = sess.pending_tool_call.get("name", ev.confirm_tool_name)
        tool_args = sess.pending_tool_call.get("args", ev.confirm_tool_args)
        path = sess.pending_tool_call.get(
            "canonical_path", ev.confirm_canonical_path
        )

        title = f"Approve tool call: {tool_name}"
        body_parts = [f"Tool: {tool_name}", f"Args: {tool_args}"]
        if path:
            body_parts.append(f"Path: {path}")
        body = "\n".join(body_parts)

        self.output.write_confirm(
            f"confirmation requested: {tool_name}({tool_args})"
        )

        result = await self.push_screen_wait(ConfirmScreen(title, body))
        approved = result if result is not None else False

        sess.pending_tool_call["approved"] = approved
        sess.confirmation_response.set()

        if approved:
            self.output.write_system(f"[approved {tool_name}]")
        else:
            self.output.write_system(f"[denied {tool_name}]")

    def on_unmount(self) -> None:
        # Textual calls `on_unmount` synchronously and does NOT await
        # `async def` versions of it, so any async cleanup must happen
        # elsewhere (see `_shutdown_and_exit`). This hook is a last-resort
        # sync fallback for abnormal exits: we flush the session index so
        # recently-completed sessions are durable even if the user closed
        # the terminal abruptly.
        try:
            self.mgr.flush_index()
        except Exception:
            logger.exception("Failed to flush session index on unmount")

    async def _shutdown_and_exit(self) -> None:
        """Async shutdown used by `/quit` and `:q`.

        Awaits `mgr.aclose()` so any in-flight resources are released and
        the session index is persisted, then exits the Textual app.
        """
        try:
            await self.mgr.aclose()
        except Exception:
            logger.exception("Failed to close session manager on /quit")
        self.exit()


# ---------------------------------------------------------------------------
# ChatTimeline — scrollable message area (separate from App for encapsulation)
# ---------------------------------------------------------------------------


class ChatTimeline(VerticalScroll):
    """Scrollable timeline of chat messages and tool cards.

    Mounted inside the App via compose(). Provides write_* and streaming
    methods that the App delegates to.
    """

    CSS = """
    ChatTimeline {
        height: 1fr;
        padding: 1 2;
        background: #0f141c;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._live_agent: AgentMessage | None = None
        # FIFO of ToolCard widgets awaiting a `tool_result` event. This
        # supports providers that may issue multiple tool calls before
        # returning their results, instead of overwriting a single slot.
        self._pending_tools: list[ToolCard] = []
        # Auto-scroll only when the user is already near the bottom. We
        # detect this by tracking whether the last `scroll_end` call found
        # the viewport at the bottom of the scrollable area. While the
        # user is reading older content (scrolled up), new content still
        # renders but the viewport does not snap to the bottom on every
        # streaming token.
        self._sticky_bottom: bool = True

    def _maybe_scroll_end(self) -> None:
        """Scroll to the end only if the user is still at the bottom."""
        if not self._sticky_bottom:
            return
        self.scroll_end(animate=False)

    def on_scroll(self, event) -> None:  # type: ignore[no-untyped-def]
        """Track whether the viewport is at the bottom of the scrollable area.

        Textual's VerticalScroll fires ``Scroll`` events on the *content*
        widget when the scroll position changes. We treat the viewport as
        "at the bottom" when ``scroll_y + viewport.height >= content_size``.
        """
        try:
            content_height = self.content_size.height
            viewport_height = self.size.height
        except Exception:  # noqa: BLE001
            return
        if content_height <= viewport_height:
            self._sticky_bottom = True
            return
        # scroll_y may not exist on all event types; fall back to attribute.
        scroll_y = getattr(event, "scroll_y", None)
        if scroll_y is None:
            return
        distance_from_bottom = content_height - viewport_height - scroll_y
        if distance_from_bottom <= 1:
            self._sticky_bottom = True
        else:
            self._sticky_bottom = False

    def reset_sticky_bottom(self) -> None:
        """Mark the viewport as at-bottom after a programmatic scroll.

        Called by the App when it forces a `scroll_end` (for example,
        after switching to a new session) so the next stream of events
        continues to auto-scroll.
        """
        self._sticky_bottom = True

    # ── message writers ──

    def write_user(self, text: str) -> None:
        self.mount(UserMessage(text))
        self._maybe_scroll_end()

    def start_assistant(self) -> None:
        msg = AgentMessage()
        self._live_agent = msg
        self.mount(msg)
        self._maybe_scroll_end()

    def append_assistant_delta(self, delta: str) -> None:
        if self._live_agent is None:
            self.start_assistant()
        if self._live_agent is None:
            return
        self._live_agent.append_delta(delta)
        self._maybe_scroll_end()

    def end_assistant(self) -> None:
        if self._live_agent is not None:
            self._live_agent.finalize()
        self._live_agent = None

    def write_system(self, text: str) -> None:
        self.mount(SystemMessage(text))
        self._maybe_scroll_end()

    def write_error(self, text: str) -> None:
        err = Static(
            Text(f"  \u274c {text}", style="bold #f85149"),
            markup=False,
        )
        self.mount(err)
        self._maybe_scroll_end()

    def write_thinking(self, text: str) -> None:
        inner = Static(
            Text(f"  \u2039{text}\u203a", style="dim italic #d29922"),
            markup=False,
        )
        wrapped = Collapsible(
            inner,
            title="  \U0001f4ad Thinking\u2026",
            collapsed=True,
            id=f"thinking-{id(inner)}",
        )
        self.mount(wrapped)
        self._maybe_scroll_end()

    def write_usage(
        self,
        tokens_in: int,
        tokens_out: int,
        tokens_thoughts: int,
        tokens_total: int,
    ) -> None:
        parts = []
        if tokens_in:
            parts.append(f"in:{tokens_in}")
        if tokens_out:
            parts.append(f"out:{tokens_out}")
        if tokens_thoughts:
            parts.append(f"think:{tokens_thoughts}")
        if tokens_total:
            parts.append(f"total:{tokens_total}")
        summary = "  ".join(parts) if parts else "no usage data"
        self.mount(SystemMessage(summary))
        self._maybe_scroll_end()

    def write_confirm(self, text: str) -> None:
        warn = Static(
            Text(f"  \u26a0 {text}", style="bold #d29922"),
            markup=False,
        )
        self.mount(warn)
        self._maybe_scroll_end()

    # ── tool cards ──

    def add_tool_call(self, name: str, args_json: str) -> None:
        summary = _make_tool_summary(name, args_json)
        card = ToolCard(name, summary, args_json)
        self.mount(card)
        self._maybe_scroll_end()
        self._pending_tools.append(card)

    def add_tool_result(self, name: str, result_text: str) -> None:
        if self._pending_tools:
            card = self._pending_tools.pop(0)
            card.set_result(result_text)

    # ── history replay ──

    def replay_history(self, messages: list) -> None:
        """Render persisted messages into the timeline on resume."""
        for msg in messages:
            if msg.role == "user":
                self.write_user(msg.content)
            elif msg.role == "assistant":
                # Render completed assistant messages as finalized cards
                agent = AgentMessage()
                agent.append_delta(msg.content)
                agent.finalize()
                self.mount(agent)
            # Skip system messages from history (they're transient)
        self._maybe_scroll_end()

    # ── clear ──

    def clear_timeline(self) -> None:
        """Remove all messages and tool cards."""
        self.remove_children()
        self._live_agent = None
        self._pending_tools.clear()
        # After clearing, the viewport is empty — treat as at-bottom so
        # the next writer scrolls naturally.
        self._sticky_bottom = True


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------

AgentCliApp = OttoUI
