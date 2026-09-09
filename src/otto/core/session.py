"""Session and SessionManager for otto.

Each Session owns:
  - a unique id (first 8 hex chars of uuid4)
  - a creation timestamp
  - a shared provider config reference
  - a running google.antigravity.Agent (entered once, lives for the session)
  - a per-session asyncio.Queue of incoming user messages
  - a per-session consumer task that drains the queue, calls the SDK, and
    emits StreamEvents into a per-session event queue
  - an in-memory list[Message] of the conversation transcript for UI rendering
  - per-session confirmation primitives for the ask_user policy flow
  - a per-session mode (Plan or Build) that controls tool permissions

Mode switching: changing the mode tears down the current Agent and creates
a new one with the appropriate policy set.  Conversation history is preserved
via the SDK's session resumption mechanism (conversation_id + save_dir).

Concurrent-input policy: the consumer task pulls one message at a time and
runs a full turn before the next. Messages submitted while a turn is active
are queued in FIFO order. No turn cancellation in MVP.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import os
import pathlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from google import antigravity
from google.antigravity.types import (
    SessionContinuationMode,
)
from google.antigravity.types import (
    Text as _Text,
)
from google.antigravity.types import (
    Thought as _Thought,
)
from google.antigravity.types import (
    ToolCall as _ToolCall,
)
from google.antigravity.types import (
    ToolResult as _ToolResult,
)

from . import agent_config as provider
from .policy import build_plan_policies, build_policies, make_ask_handler
from .session_index import SessionIndex

logger = logging.getLogger("otto.session")

_PLANS_DIR = pathlib.Path(".otto") / "plans"
_SESSIONS_DIR = pathlib.Path(".otto") / "sessions"
_MESSAGES_VERSION = 1


def save_messages(session_id: str, messages: list[Message]) -> None:
    """Persist conversation messages to disk for later resume."""
    save_dir = _SESSIONS_DIR / session_id
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / "messages.json"
    data = {
        "version": _MESSAGES_VERSION,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "ts": m.ts.isoformat(),
            }
            for m in messages
        ],
    }
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.debug("Saved %d messages for session %s", len(messages), session_id)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to save messages for session %s: %s", session_id, e)


def load_messages(session_id: str) -> list[Message]:
    """Load persisted conversation messages from disk. Returns empty list on error."""
    path = _SESSIONS_DIR / session_id / "messages.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        messages = []
        for m in raw.get("messages", []):
            ts = datetime.fromisoformat(m["ts"]) if "ts" in m else datetime.now(timezone.utc)
            messages.append(Message(role=m["role"], content=m["content"], ts=ts))
        logger.debug("Loaded %d messages for session %s", len(messages), session_id)
        return messages
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning("Failed to load messages for session %s: %s", session_id, e)
        return []


class SessionMode(str, enum.Enum):
    """Per-session operating mode."""

    PLAN = "plan"
    BUILD = "build"


def _truncate(s: str, limit: int) -> str:
    """Truncate a string to *limit* chars, appending '…' if clipped."""
    s = s.replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionNotFound(LookupError):
    """Raised by SessionManager.switch when the prefix is empty/ambiguous/missing."""


@dataclass
class StreamEvent:
    """A single streaming event emitted by a session's consumer task."""

    session_id: str
    kind: str  # "delta" | "done" | "error" | "thinking" | "tool_call" | "tool_result" | "usage" | "confirmation_request"
    text: str = ""
    error: str = ""  # human-readable, for kind=="error"
    # Tool call/result fields
    tool_name: str = ""
    tool_args: str = ""  # JSON string of args
    tool_result_text: str = ""  # truncated result summary
    # Confirmation request fields
    confirm_tool_name: str = ""
    confirm_tool_args: str = ""
    confirm_canonical_path: str = ""
    # Usage metadata fields
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thoughts: int = 0
    tokens_total: int = 0


class _AgentHandle:
    """Internal: a running Agent started via async context manager."""

    def __init__(self, agent: antigravity.Agent):
        self._agent = agent

    @classmethod
    async def start(cls, config: antigravity.LocalOpenAIAgentConfig) -> _AgentHandle:
        try:
            agent = antigravity.Agent(config)
            await agent.__aenter__()
            logger.debug("Agent started successfully")
            return cls(agent)
        except Exception as e:
            logger.error("Failed to start agent: %s", e)
            raise

    @property
    def agent(self) -> antigravity.Agent:
        return self._agent

    @property
    def conversation_id(self) -> str | None:
        """Return the runtime conversation id, or None if not yet available."""
        return self._agent.conversation_id

    async def aclose(self) -> None:
        try:
            await self._agent.__aexit__(None, None, None)
            logger.debug("Agent closed successfully")
        except Exception as e:
            logger.error("Error closing agent: %s", e)
            raise


class Session:
    """A single conversation session backed by one running Agent."""

    def __init__(
        self,
        *,
        session_id: str,
        created_at: datetime,
        handle: _AgentHandle,
        mode: SessionMode,
        save_dir: str,
        confirmation_request: asyncio.Event,
        confirmation_response: asyncio.Event,
        pending_tool_call: dict[str, Any],
        base_config: antigravity.LocalOpenAIAgentConfig,
        index: SessionIndex | None = None,
    ):
        self.id = session_id
        self.created_at = created_at
        self._handle = handle
        self._mode = mode
        self._save_dir = save_dir
        self.messages: list[Message] = []
        self.input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.event_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        self._consumer: asyncio.Task[None] | None = None
        self.last_activity: datetime = created_at
        # Confirmation primitives for ask_user policy flow
        self.confirmation_request = confirmation_request
        self.confirmation_response = confirmation_response
        self.pending_tool_call = pending_tool_call
        # Retained for mode-switch rebuilds
        self._base_config = base_config
        # Reference to session index for persistence
        self._index_ref = index
        # Optional user-assigned name
        self._name: str = ""

    @property
    def agent(self) -> antigravity.Agent:
        return self._handle.agent

    @property
    def mode(self) -> SessionMode:
        return self._mode

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value
        # Persist name to index
        if self._index_ref:
            self._index_ref.update(self.id, name=value)

    def message_count(self) -> int:
        return len(self.messages)

    async def enqueue(self, user_text: str) -> None:
        await self.input_queue.put(user_text)

    async def start_consumer(self) -> None:
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(
                self._consume_loop(), name=f"sess-{self.id}"
            )
            logger.debug("Consumer started for session %s", self.id)

    async def stop_consumer(self) -> None:
        if self._consumer is not None and not self._consumer.done():
            self._consumer.cancel()
            try:
                await self._consumer
            except asyncio.CancelledError:
                logger.debug("Consumer cancelled for session %s", self.id)
        self._consumer = None
        logger.debug("Consumer stopped for session %s", self.id)

    async def aclose(self) -> None:
        await self.stop_consumer()
        await self._handle.aclose()

    async def switch_mode(self, new_mode: SessionMode) -> None:
        """Switch between Plan and Build mode.

        Tears down the current Agent, creates a new one with the
        appropriate policy set, and resumes the conversation via
        conversation_id + save_dir.  Consumer is stopped and restarted.
        """
        if new_mode == self._mode:
            return

        old_conversation_id = self._handle.conversation_id

        # Tear down current agent
        await self.stop_consumer()
        await self._handle.aclose()

        # Build new config with updated mode
        new_config = _build_session_config(
            base_config=self._base_config,
            session_id=self.id,
            mode=new_mode,
            save_dir=self._save_dir,
            confirmation_request=self.confirmation_request,
            confirmation_response=self.confirmation_response,
            pending_tool_call=self.pending_tool_call,
            conversation_id=old_conversation_id,
        )

        # Start new agent with session resumption
        self._handle = await _AgentHandle.start(new_config)
        self._mode = new_mode
        await self.start_consumer()

        # Update index with new mode and conversation_id
        if self._index_ref:
            self._index_ref.update(
                self.id,
                mode=new_mode.value,
                last_active=datetime.now(timezone.utc),
                add_conversation_id=old_conversation_id,
            )

        logger.info(
            "Session %s switched to %s mode (conversation_id=%s)",
            self.id,
            new_mode.value,
            old_conversation_id,
        )

    def append_plan_output(self, text: str) -> None:
        """Append assistant text to the plan file for this session."""
        if self._mode != SessionMode.PLAN:
            return
        _PLANS_DIR.mkdir(parents=True, exist_ok=True)
        plan_file = _PLANS_DIR / f"{self.id}.md"
        with open(plan_file, "a", encoding="utf-8") as f:
            f.write(text)

    async def _consume_loop(self) -> None:
        while True:
            try:
                user_text = await self.input_queue.get()
            except asyncio.CancelledError:
                logger.debug("Input queue cancelled for session %s", self.id)
                return
            try:
                self.messages.append(Message(role="user", content=user_text))
                self.last_activity = datetime.now(timezone.utc)
                resp = await self.agent.chat(user_text)
                buf: list[str] = []
                async for chunk in resp.chunks:
                    if isinstance(chunk, _Text):
                        buf.append(chunk.text)
                        await self.event_queue.put(
                            StreamEvent(session_id=self.id, kind="delta", text=chunk.text)
                        )
                    elif isinstance(chunk, _Thought):
                        await self.event_queue.put(
                            StreamEvent(session_id=self.id, kind="thinking", text=chunk.text)
                        )
                    elif isinstance(chunk, _ToolCall):
                        args_str = json.dumps(chunk.args, default=str) if chunk.args else "{}"
                        await self.event_queue.put(
                            StreamEvent(
                                session_id=self.id,
                                kind="tool_call",
                                tool_name=str(chunk.name),
                                tool_args=args_str,
                            )
                        )
                    elif isinstance(chunk, _ToolResult):
                        result_summary = _truncate(str(chunk.result), 120) if chunk.result else ""
                        if chunk.error:
                            result_summary = f"error: {_truncate(chunk.error, 100)}"
                        await self.event_queue.put(
                            StreamEvent(
                                session_id=self.id,
                                kind="tool_result",
                                tool_name=str(chunk.name),
                                tool_result_text=result_summary,
                            )
                        )
                full = "".join(buf)
                self.messages.append(Message(role="assistant", content=full))
                self.last_activity = datetime.now(timezone.utc)
                # Persist plan output
                self.append_plan_output(full)
                # Emit usage metadata
                usage = resp.usage_metadata
                if usage:
                    await self.event_queue.put(
                        StreamEvent(
                            session_id=self.id,
                            kind="usage",
                            tokens_in=usage.prompt_token_count or 0,
                            tokens_out=usage.candidates_token_count or 0,
                            tokens_thoughts=usage.thoughts_token_count or 0,
                            tokens_total=usage.total_token_count or 0,
                        )
                    )
                await self.event_queue.put(
                    StreamEvent(session_id=self.id, kind="done", text=full)
                )
                # Persist messages for resume
                save_messages(self.id, self.messages)
            except asyncio.CancelledError:
                logger.debug("Consumer loop cancelled for session %s", self.id)
                return
            except Exception as e:  # noqa: BLE001 — surface any error to the UI
                logger.error("Error in consumer loop for session %s: %s", self.id, e)
                await self.event_queue.put(
                    StreamEvent(
                        session_id=self.id,
                        kind="error",
                        error=f"{type(e).__name__}: {e}",
                    )
                )


def _build_session_config(
    base_config: antigravity.LocalOpenAIAgentConfig,
    session_id: str,
    mode: SessionMode,
    save_dir: str,
    confirmation_request: asyncio.Event,
    confirmation_response: asyncio.Event,
    pending_tool_call: dict[str, Any],
    conversation_id: str | None = None,
) -> antigravity.LocalOpenAIAgentConfig:
    """Build a per-session config for the given mode.

    Each session gets its own config so the ask_user handler closure can
    capture that session's confirmation primitives without cross-talk.
    """
    if mode == SessionMode.PLAN:
        policies = build_plan_policies()
    else:
        ask_handler = make_ask_handler(
            session_id=session_id,
            confirmation_request=confirmation_request,
            confirmation_response=confirmation_response,
            pending_tool_call=pending_tool_call,
        )
        policies = build_policies(ask_handler=ask_handler)

    kwargs: dict[str, Any] = {
        "model": base_config.model,
        "base_url": base_config.base_url,
        "capabilities": base_config.capabilities,
        "policies": policies,
        "env": {"KILO_API_KEY": base_config.env.get("KILO_API_KEY", "")},
        "save_dir": save_dir,
    }
    if conversation_id is not None:
        kwargs["conversation_id"] = conversation_id
        kwargs["session_continuation_mode"] = SessionContinuationMode.RESUME

    return antigravity.LocalOpenAIAgentConfig(**kwargs)


class SessionNotFoundForResume(LookupError):
    """Raised when a session cannot be resumed (missing/corrupt state)."""


def _discover_conversation_ids(save_dir: str) -> list[str]:
    """Scan a save_dir for .db files and return their conversation_ids (stems)."""
    d = pathlib.Path(save_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.db"))


class SessionManager:
    """Owns Sessions, creates them lazily, switches the active one."""

    def __init__(self) -> None:
        self._base_config = provider.build_provider_config()
        self._sessions: dict[str, Session] = {}
        self._order: list[str] = []
        self._active_id: str | None = None
        self._index = SessionIndex()
        self._index.load()

    @property
    def config(self) -> antigravity.LocalOpenAIAgentConfig:
        return self._base_config

    @property
    def model(self) -> str:
        return self._base_config.model

    def set_model(self, model: str) -> None:
        """Rebuild the provider config with a new model for future sessions."""
        self._base_config = provider.build_provider_config(model_override=model)
        logger.debug("Model changed to %s", model)

    def list_sessions(self) -> list[Session]:
        return [self._sessions[i] for i in self._order]

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    @property
    def active(self) -> Session | None:
        if self._active_id is None:
            return None
        return self._sessions.get(self._active_id)

    async def ensure_active(self) -> Session:
        if self._active_id is None or self._active_id not in self._sessions:
            return await self.new_session()
        return self._sessions[self._active_id]

    async def new_session(self, mode: SessionMode = SessionMode.BUILD) -> Session:
        sid = uuid.uuid4().hex[:8]
        created = datetime.now(timezone.utc)

        # Stable save_dir for session resumption across mode switches
        save_dir = str(_SESSIONS_DIR / sid)
        os.makedirs(save_dir, exist_ok=True)

        # Per-session confirmation primitives
        confirmation_request = asyncio.Event()
        confirmation_response = asyncio.Event()
        pending_tool_call: dict[str, Any] = {}

        session_config = _build_session_config(
            base_config=self._base_config,
            session_id=sid,
            mode=mode,
            save_dir=save_dir,
            confirmation_request=confirmation_request,
            confirmation_response=confirmation_response,
            pending_tool_call=pending_tool_call,
        )

        try:
            handle = await _AgentHandle.start(session_config)
        except Exception as e:
            logger.error("Failed to create new session: %s", e)
            raise

        sess = Session(
            session_id=sid,
            created_at=created,
            handle=handle,
            mode=mode,
            save_dir=save_dir,
            confirmation_request=confirmation_request,
            confirmation_response=confirmation_response,
            pending_tool_call=pending_tool_call,
            base_config=self._base_config,
            index=self._index,
        )
        self._sessions[sid] = sess
        self._order.append(sid)
        self._active_id = sid
        # Record in persistent index
        self._index.add(sid, created, mode.value)
        await sess.start_consumer()
        logger.debug("New session created: %s (mode=%s)", sid, mode.value)
        return sess

    def switch(self, id_prefix: str) -> Session:
        prefix = (id_prefix or "").strip().lower()
        if not prefix:
            logger.warning("Switch called with empty prefix")
            raise SessionNotFound("switch requires a non-empty id prefix")
        matches = [s for s in self.list_sessions() if s.id.lower().startswith(prefix)]
        if not matches:
            available = ", ".join(s.id for s in self.list_sessions()) or "<none>"
            logger.warning("No session matches prefix '%s'. Available: %s", id_prefix, available)
            raise SessionNotFound(
                f"no session matches '{id_prefix}'. available: {available}"
            )
        if len(matches) > 1:
            ids = ", ".join(s.id for s in matches)
            logger.warning("Prefix '%s' is ambiguous: %s", id_prefix, ids)
            raise SessionNotFound(
                f"prefix '{id_prefix}' is ambiguous ({ids}); provide a longer prefix"
            )
        self._active_id = matches[0].id
        logger.debug("Switched to session %s", matches[0].id)
        return matches[0]

    async def resume_session(self, id_prefix: str) -> Session:
        """Resume a session from disk by ID prefix.

        Finds the session in the index, discovers its conversation_ids from
        the on-disk .db files, and creates a new Agent with RESUME mode.

        Raises SessionNotFoundForResume if:
          - prefix matches nothing or is ambiguous
          - save_dir is missing or has no .db files
          - Agent fails to start with RESUME
        """
        prefix = (id_prefix or "").strip().lower()
        if not prefix:
            raise SessionNotFoundForResume("resume requires a non-empty id prefix")

        # Search across ALL index entries (not just in-memory)
        all_entries = self._index.entries()
        matches = [e for e in all_entries.values() if e.id.lower().startswith(prefix)]

        if not matches:
            available = ", ".join(e.id for e in all_entries.values()) or "<none>"
            raise SessionNotFoundForResume(
                f"no session matches '{id_prefix}'. available: {available}"
            )
        if len(matches) > 1:
            ids = ", ".join(e.id for e in matches)
            raise SessionNotFoundForResume(
                f"prefix '{id_prefix}' is ambiguous ({ids}); provide a longer prefix"
            )

        entry = matches[0]
        sid = entry.id

        # Already loaded in-memory?
        if sid in self._sessions:
            self._active_id = sid
            return self._sessions[sid]

        # Discover conversation_ids from disk
        save_dir = str(_SESSIONS_DIR / sid)
        conv_ids = _discover_conversation_ids(save_dir)
        if not conv_ids:
            raise SessionNotFoundForResume(
                f"session '{sid}' has no on-disk state at {save_dir}"
            )

        # Use the most recent conversation_id (last modified .db file)
        save_dir_path = pathlib.Path(save_dir)
        db_files = sorted(save_dir_path.glob("*.db"), key=lambda p: p.stat().st_mtime)
        conversation_id = db_files[-1].stem

        # Parse mode from index
        mode = SessionMode(entry.mode) if entry.mode in ("plan", "build") else SessionMode.BUILD

        # Per-session confirmation primitives
        confirmation_request = asyncio.Event()
        confirmation_response = asyncio.Event()
        pending_tool_call: dict[str, Any] = {}

        session_config = _build_session_config(
            base_config=self._base_config,
            session_id=sid,
            mode=mode,
            save_dir=save_dir,
            confirmation_request=confirmation_request,
            confirmation_response=confirmation_response,
            pending_tool_call=pending_tool_call,
            conversation_id=conversation_id,
        )

        try:
            handle = await _AgentHandle.start(session_config)
        except Exception as e:
            raise SessionNotFoundForResume(
                f"failed to resume session '{sid}': {e}"
            ) from e

        sess = Session(
            session_id=sid,
            created_at=datetime.fromisoformat(entry.created_at),
            handle=handle,
            mode=mode,
            save_dir=save_dir,
            confirmation_request=confirmation_request,
            confirmation_response=confirmation_response,
            pending_tool_call=pending_tool_call,
            base_config=self._base_config,
            index=self._index,
        )
        # Restore saved message history
        sess.messages = load_messages(sid)
        # Restore session name
        if entry.name:
            sess._name = entry.name
        self._sessions[sid] = sess
        self._order.append(sid)
        self._active_id = sid
        # Update index with resume activity
        self._index.update(sid, last_active=datetime.now(timezone.utc))
        await sess.start_consumer()
        logger.info(
            "Session %s resumed from disk (conversation_id=%s, mode=%s)",
            sid, conversation_id, mode.value,
        )
        return sess

    def disk_sessions(self) -> list[dict[str, Any]]:
        """Return metadata for sessions on disk that are NOT loaded in memory."""
        self._index._ensure_loaded()
        result = []
        for sid, entry in self._index.entries().items():
            if sid not in self._sessions:
                save_dir = str(_SESSIONS_DIR / sid)
                conv_ids = _discover_conversation_ids(save_dir)
                result.append({
                    "id": sid,
                    "created_at": entry.created_at,
                    "last_active": entry.last_active,
                    "mode": entry.mode,
                    "conversation_count": len(conv_ids),
                    "name": entry.name,
                })
        return result

    def update_activity(self, session_id: str) -> None:
        """Update last_active timestamp in the index for a session."""
        self._index.update(session_id, last_active=datetime.now(timezone.utc))

    def flush_index(self) -> None:
        """Force-save the index. Called on graceful shutdown."""
        self._index.save()

    async def aclose(self) -> None:
        for sid in list(self._order):
            try:
                await self._sessions[sid].aclose()
            except Exception as e:  # noqa: BLE001
                logger.error("Error closing session %s: %s", sid, e)
        self._sessions.clear()
        self._order.clear()
        self._active_id = None
        self.flush_index()
        logger.debug("SessionManager closed")


if __name__ == "__main__":
    import os
    if "KILO_API_KEY" not in os.environ:
        os.environ["KILO_API_KEY"] = "sk-dummy-smoke-test"

    async def run() -> None:
        mgr = SessionManager()
        s1 = await mgr.new_session()
        s2 = await mgr.new_session()
        assert s1.id != s2.id, "session ids must be unique"
        assert mgr.list_sessions() == [s1, s2], "creation order preserved"
        mgr.switch(s1.id)
        assert mgr.active and mgr.active.id == s1.id
        mgr.switch(s2.id)
        assert mgr.active and mgr.active.id == s2.id
        # Test mode switching
        assert s1.mode == SessionMode.BUILD
        await s1.switch_mode(SessionMode.PLAN)
        assert s1.mode == SessionMode.PLAN
        assert s2.mode == SessionMode.BUILD  # other session unaffected
        await s1.switch_mode(SessionMode.BUILD)
        assert s1.mode == SessionMode.BUILD
        print("session smoke test OK")
        print("  s1.id =", s1.id)
        print("  s2.id =", s2.id)
        await mgr.aclose()

    asyncio.run(run())
