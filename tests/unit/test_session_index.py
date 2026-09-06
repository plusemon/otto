"""Unit tests for otto.core.session_index — save/load/update behavior."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from otto.core.session_index import SessionIndex, SessionEntry


class TestSessionIndex:
    """Tests for SessionIndex persistence and in-memory operations."""

    def test_add_and_get(self, tmp_path: Path) -> None:
        """Adding a session and retrieving it by ID works."""
        idx = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx.add("abc123", now, "build", conversation_id="conv-1")

        entry = idx.get("abc123")
        assert entry is not None
        assert entry.id == "abc123"
        assert entry.mode == "build"
        assert entry.conversation_ids == ["conv-1"]

    def test_add_without_conversation_id(self, tmp_path: Path) -> None:
        """Adding without conversation_id leaves list empty."""
        idx = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx.add("abc123", now, "plan")

        entry = idx.get("abc123")
        assert entry is not None
        assert entry.conversation_ids == []

    def test_list_all(self, tmp_path: Path) -> None:
        """list_all returns all entries in insertion order."""
        idx = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx.add("aaa", now, "build")
        idx.add("bbb", now, "plan")

        entries = idx.list_all()
        assert len(entries) == 2
        assert [e.id for e in entries] == ["aaa", "bbb"]

    def test_update_mode(self, tmp_path: Path) -> None:
        """Updating mode persists the change."""
        idx = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx.add("abc123", now, "build")

        idx.update("abc123", mode="plan")
        entry = idx.get("abc123")
        assert entry is not None
        assert entry.mode == "plan"

    def test_update_last_active(self, tmp_path: Path) -> None:
        """Updating last_active changes the timestamp."""
        idx = self._make_index(tmp_path)
        t1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        idx.add("abc123", t1, "build")

        t2 = datetime(2025, 6, 15, tzinfo=timezone.utc)
        idx.update("abc123", last_active=t2)
        entry = idx.get("abc123")
        assert entry is not None
        assert entry.last_active == t2.isoformat()

    def test_update_add_conversation_id(self, tmp_path: Path) -> None:
        """Adding a conversation_id appends to the list (no duplicates)."""
        idx = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx.add("abc123", now, "build", conversation_id="conv-1")

        idx.update("abc123", add_conversation_id="conv-2")
        entry = idx.get("abc123")
        assert entry is not None
        assert entry.conversation_ids == ["conv-1", "conv-2"]

    def test_update_conversation_id_dedup(self, tmp_path: Path) -> None:
        """Duplicate conversation_ids are not added twice."""
        idx = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx.add("abc123", now, "build", conversation_id="conv-1")

        idx.update("abc123", add_conversation_id="conv-1")
        entry = idx.get("abc123")
        assert entry is not None
        assert entry.conversation_ids == ["conv-1"]

    def test_update_unknown_session_is_noop(self, tmp_path: Path) -> None:
        """Updating a nonexistent session doesn't crash."""
        idx = self._make_index(tmp_path)
        # Should not raise
        idx.update("nonexistent", mode="plan")
        assert idx.get("nonexistent") is None

    def test_save_and_reload(self, tmp_path: Path) -> None:
        """Data persists across SessionIndex instances via disk."""
        idx1 = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx1.add("abc123", now, "build", conversation_id="conv-1")
        idx1.add("def456", now, "plan")

        # Create a new index and load from disk
        idx2 = SessionIndex()
        idx2._path = tmp_path / "sessions" / "index.json"
        idx2.load()

        assert idx2.get("abc123") is not None
        assert idx2.get("abc123").mode == "build"
        assert idx2.get("abc123").conversation_ids == ["conv-1"]
        assert idx2.get("def456") is not None
        assert idx2.get("def456").mode == "plan"

    def test_load_missing_file_is_ok(self, tmp_path: Path) -> None:
        """Loading from a nonexistent file returns empty index."""
        idx = SessionIndex()
        idx._path = tmp_path / "nonexistent" / "index.json"
        idx.load()

        assert idx.list_all() == []
        assert idx.get("anything") is None

    def test_load_corrupt_file_starts_fresh(self, tmp_path: Path) -> None:
        """A corrupt JSON file is handled gracefully."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True)
        index_path = sessions_dir / "index.json"
        index_path.write_text("NOT JSON {{{")

        idx = SessionIndex()
        idx._path = index_path
        idx.load()

        assert idx.list_all() == []

    def test_entries_returns_copy(self, tmp_path: Path) -> None:
        """entries() returns a dict copy, not a reference to internal state."""
        idx = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx.add("abc123", now, "build")

        entries = idx.entries()
        entries.pop("abc123")

        # Internal state should be unaffected
        assert idx.get("abc123") is not None

    def test_index_file_is_pretty_printed(self, tmp_path: Path) -> None:
        """The saved JSON is indented (human-readable)."""
        idx = self._make_index(tmp_path)
        now = datetime.now(timezone.utc)
        idx.add("abc123", now, "build")

        index_path = tmp_path / "sessions" / "index.json"
        raw = index_path.read_text()
        # Indented JSON has newlines after opening brace
        assert "\n" in raw
        data = json.loads(raw)
        assert "sessions" in data

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_index(self, tmp_path: Path) -> SessionIndex:
        """Create a SessionIndex backed by a temp directory."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        idx = SessionIndex()
        idx._path = sessions_dir / "index.json"
        return idx
