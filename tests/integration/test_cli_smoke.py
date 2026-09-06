"""Integration smoke test — CLI starts without crashing.

Mocks the model API call so no real KILO_API_KEY or network access is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestCLISmoke:
    """Verify the CLI module can be imported and App instantiated."""

    def test_import_cli_main(self) -> None:
        """otto.cli.main can be imported without error."""
        from otto.cli.main import main
        assert callable(main)

    def test_import_app_class(self) -> None:
        """otto.cli.ui.AgentCliApp can be imported."""
        from otto.cli.ui import AgentCliApp
        assert AgentCliApp is not None

    def test_app_instantiation(self) -> None:
        """AgentCliApp can be constructed with a mock manager."""
        from otto.cli.ui import AgentCliApp
        from otto.core.session import SessionManager

        # Mock the SessionManager so we don't hit the real API
        with patch.object(SessionManager, "__init__", lambda self: None):
            mgr = SessionManager()
            mgr._base_config = MagicMock()
            mgr._base_config.model = "test-model"
            mgr._base_config.base_url = "http://localhost"
            mgr._sessions = {}
            mgr._order = []
            mgr._active_id = None
            mgr._index = MagicMock()

            app = AgentCliApp(mgr)
            assert app.mgr is mgr
            assert app._busy is False

    def testSlashCommandsDefined(self) -> None:
        """All expected slash commands are registered."""
        from otto.cli.ui import COMMANDS
        cmd_names = {c[0] for c in COMMANDS}
        assert "/plan" in cmd_names
        assert "/build" in cmd_names
        assert "/session new" in cmd_names
        assert "/session list" in cmd_names
        assert "/session switch <id>" in cmd_names
        assert "/session resume <id>" in cmd_names
        assert "/model <name>" in cmd_names
        assert "/quit" in cmd_names

    def test_core_modules_importable(self) -> None:
        """All core modules can be imported."""
        from otto.core.tools import git_diff, run_linter, run_tests

        assert callable(run_tests)
        assert callable(run_linter)
        assert callable(git_diff)

    def test_session_mode_enum(self) -> None:
        """SessionMode has expected values."""
        from otto.core.session import SessionMode
        assert SessionMode.PLAN.value == "plan"
        assert SessionMode.BUILD.value == "build"

    def test_stream_event_fields(self) -> None:
        """StreamEvent dataclass has expected fields."""
        from otto.core.session import StreamEvent
        ev = StreamEvent(session_id="abc", kind="delta", text="hello")
        assert ev.session_id == "abc"
        assert ev.kind == "delta"
        assert ev.text == "hello"
        assert ev.tool_name == ""

    def test_policy_build_returns_list(self) -> None:
        """build_policies returns a non-empty list."""
        from otto.core.policy import build_policies
        policies = build_policies()
        assert len(policies) > 0

    def test_session_index_instantiation(self, tmp_path) -> None:
        """SessionIndex can be instantiated."""
        from otto.core.session_index import SessionIndex
        idx = SessionIndex()
        idx._path = tmp_path / "sessions" / "index.json"
        idx.load()
        assert idx.list_all() == []
