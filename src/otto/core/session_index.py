"""Persistent session index for otto.

Maintains .otto/sessions/index.json — a lightweight metadata file that
tracks per-session info (id, timestamps, mode, conversation_ids) separate
from the Go harness's trajectory storage. This is what makes `/session list`
and cross-process resume possible without parsing harness-internal state.
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("otto.session_index")

_SESSIONS_DIR = pathlib.Path(".otto") / "sessions"
_INDEX_PATH = _SESSIONS_DIR / "index.json"


@dataclass
class SessionEntry:
    """Metadata for one session, persisted in the index."""

    id: str
    created_at: str  # ISO-8601
    last_active: str  # ISO-8601
    mode: str  # "plan" | "build"
    conversation_ids: list[str] = field(default_factory=list)

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def from_timestamps(
        session_id: str,
        created_at: datetime,
        last_active: datetime,
        mode: str,
        conversation_ids: list[str] | None = None,
    ) -> SessionEntry:
        return SessionEntry(
            id=session_id,
            created_at=created_at.isoformat(),
            last_active=last_active.isoformat(),
            mode=mode,
            conversation_ids=conversation_ids or [],
        )


class SessionIndex:
    """Read/write wrapper around .otto/sessions/index.json."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionEntry] = {}
        self._path = _INDEX_PATH
        self._loaded = False

    def load(self) -> None:
        """Load index from disk. Missing file is OK (empty index)."""
        if self._loaded:
            return
        if not self._path.exists():
            logger.debug("No index file at %s, starting fresh", self._path)
            self._loaded = True
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for sid, entry_dict in raw.get("sessions", {}).items():
                self._sessions[sid] = SessionEntry(**entry_dict)
            logger.debug("Loaded %d sessions from index", len(self._sessions))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load session index, starting fresh: %s", e)
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def save(self) -> None:
        """Persist index to disk. Called defensively on every update."""
        self._ensure_loaded()
        _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        data = {"sessions": {sid: asdict(e) for sid, e in self._sessions.items()}}
        tmp_path = self._path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(self._path)
            logger.debug("Session index saved (%d entries)", len(self._sessions))
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to save session index: %s", e)

    def add(
        self,
        session_id: str,
        created_at: datetime,
        mode: str,
        conversation_id: str | None = None,
    ) -> None:
        """Add a new session entry and save."""
        self._ensure_loaded()
        now = SessionEntry.now_iso()
        self._sessions[session_id] = SessionEntry(
            id=session_id,
            created_at=created_at.isoformat(),
            last_active=now,
            mode=mode,
            conversation_ids=[conversation_id] if conversation_id else [],
        )
        self.save()

    def update(
        self,
        session_id: str,
        *,
        last_active: datetime | None = None,
        mode: str | None = None,
        add_conversation_id: str | None = None,
    ) -> None:
        """Update fields on an existing entry and save."""
        self._ensure_loaded()
        entry = self._sessions.get(session_id)
        if entry is None:
            logger.warning("update called for unknown session %s", session_id)
            return
        if last_active is not None:
            entry.last_active = last_active.isoformat()
        if mode is not None:
            entry.mode = mode
        if add_conversation_id is not None and add_conversation_id not in entry.conversation_ids:
            entry.conversation_ids.append(add_conversation_id)
        self.save()

    def get(self, session_id: str) -> SessionEntry | None:
        self._ensure_loaded()
        return self._sessions.get(session_id)

    def list_all(self) -> list[SessionEntry]:
        self._ensure_loaded()
        return list(self._sessions.values())

    def entries(self) -> dict[str, SessionEntry]:
        self._ensure_loaded()
        return dict(self._sessions)
