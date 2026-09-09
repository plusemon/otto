"""Unit tests for otto.core.session — message persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from otto.core.session import Message, load_messages, save_messages


class TestMessagePersistence:
    """Tests for save_messages / load_messages round-trip."""

    def test_save_and_load_empty(self, tmp_path: Path, monkeypatch) -> None:
        """Saving and loading an empty list works."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".otto" / "sessions" / "test1").mkdir(parents=True)

        save_messages("test1", [])
        messages = load_messages("test1")
        assert messages == []

    def test_save_and_load_round_trip(self, tmp_path: Path, monkeypatch) -> None:
        """Messages survive save/load cycle."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".otto" / "sessions" / "test1").mkdir(parents=True)

        original = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi there"),
            Message(role="user", content="how are you?"),
        ]
        save_messages("test1", original)
        loaded = load_messages("test1")

        assert len(loaded) == 3
        assert loaded[0].role == "user"
        assert loaded[0].content == "hello"
        assert loaded[1].role == "assistant"
        assert loaded[1].content == "hi there"
        assert loaded[2].role == "user"
        assert loaded[2].content == "how are you?"

    def test_load_missing_file_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        """Loading from nonexistent path returns empty list."""
        monkeypatch.chdir(tmp_path)
        messages = load_messages("nonexistent")
        assert messages == []

    def test_load_corrupt_file_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        """Loading corrupt JSON returns empty list."""
        monkeypatch.chdir(tmp_path)
        sessions_dir = tmp_path / ".otto" / "sessions" / "test1"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "messages.json").write_text("NOT JSON {{{")

        messages = load_messages("test1")
        assert messages == []

    def test_messages_file_is_pretty_printed(self, tmp_path: Path, monkeypatch) -> None:
        """The saved JSON is indented for readability."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".otto" / "sessions" / "test1").mkdir(parents=True)

        save_messages("test1", [Message(role="user", content="test")])
        raw = (tmp_path / ".otto" / "sessions" / "test1" / "messages.json").read_text()
        assert "\n" in raw
        data = json.loads(raw)
        assert data["version"] == 1

    def test_timestamps_preserved(self, tmp_path: Path, monkeypatch) -> None:
        """Message timestamps are preserved through save/load."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".otto" / "sessions" / "test1").mkdir(parents=True)

        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        msg = Message(role="user", content="test", ts=ts)
        save_messages("test1", [msg])

        loaded = load_messages("test1")
        assert len(loaded) == 1
        assert loaded[0].ts.year == 2025
        assert loaded[0].ts.month == 6
        assert loaded[0].ts.day == 15

    def test_version_field_present(self, tmp_path: Path, monkeypatch) -> None:
        """Saved JSON includes version field for future compatibility."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".otto" / "sessions" / "test1").mkdir(parents=True)

        save_messages("test1", [Message(role="user", content="x")])
        raw = json.loads(
            (tmp_path / ".otto" / "sessions" / "test1" / "messages.json").read_text()
        )
        assert "version" in raw
        assert raw["version"] == 1
