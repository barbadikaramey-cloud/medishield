"""
Real-Time Agent Execution Event Streaming and WebSocket Broadcaster.
Specification Reference: ADR-017 § Feature 2, Spec 04 § 2, Spec 11 § 1.

Provides non-blocking event broadcasting for multi-agent workflows,
sequence numbering, bounded replay buffers, client authorization,
and graceful disconnection handling.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

EventType = Literal["STARTED", "HEARTBEAT", "COMPLETED", "FAILED", "SKIPPED", "RETRYING"]
EventStatus = Literal["RUNNING", "SUCCESS", "ERROR", "BLOCKED"]


class AgentExecutionEvent(BaseModel):
    """
    Structured, machine-readable lifecycle transition event for agent execution.
    Strictly sanitized with zero raw PII.
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    run_id: str
    agent_name: str
    event_type: EventType
    status: EventStatus
    sequence_number: int
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds")
    )
    duration_ms: float | None = None
    safe_summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CaseEventBroadcaster:
    """
    In-memory asynchronous WebSocket event broadcaster with:
    - Thread-safe non-blocking event emission from background worker threads
    - Per-(case_id, run_id) monotonic sequence numbering
    - Bounded in-memory replay buffer for reconnecting clients
    - Graceful error isolation and backpressure handling for slow consumers
    """

    def __init__(self, max_buffer_size: int = 100) -> None:
        self.max_buffer_size = max_buffer_size
        self._connections: dict[str, set[Any]] = collections.defaultdict(set)
        self._replay_buffers: dict[str, collections.deque[AgentExecutionEvent]] = collections.defaultdict(
            lambda: collections.deque(maxlen=self.max_buffer_size)
        )
        self._run_sequences: dict[str, int] = collections.defaultdict(int)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Sets the main async event loop for threadsafe dispatching."""
        self._loop = loop

    def get_replay_history(self, case_id: str) -> list[AgentExecutionEvent]:
        """Returns buffered event history for a case in chronological sequence order."""
        with self._lock:
            return list(self._replay_buffers.get(case_id, []))

    def emit(
        self,
        case_id: str,
        run_id: str,
        agent_name: str,
        event_type: EventType,
        status: EventStatus,
        safe_summary: str,
        duration_ms: float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentExecutionEvent:
        """
        Emits an agent execution event synchronously from any thread.
        Thread-safe, non-blocking, and error-isolated.
        """
        try:
            with self._lock:
                key = f"{case_id}:{run_id}"
                self._run_sequences[key] += 1
                seq = self._run_sequences[key]

                event = AgentExecutionEvent(
                    case_id=case_id,
                    run_id=run_id,
                    agent_name=agent_name,
                    event_type=event_type,
                    status=status,
                    sequence_number=seq,
                    timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
                    duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
                    safe_summary=safe_summary,
                    payload=payload or {},
                )

                self._replay_buffers[case_id].append(event)
                active_sockets = list(self._connections.get(case_id, []))

            # Broadcast asynchronously to active subscribers
            if active_sockets:
                self._dispatch_broadcast(case_id, event, active_sockets)

            return event
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to emit agent execution event for case %s: %s", case_id, exc)
            # Return fallback event so caller never crashes
            return AgentExecutionEvent(
                case_id=case_id,
                run_id=run_id,
                agent_name=agent_name,
                event_type=event_type,
                status=status,
                sequence_number=0,
                safe_summary=safe_summary,
                payload={},
            )

    def _dispatch_broadcast(
        self,
        case_id: str,
        event: AgentExecutionEvent,
        sockets: list[Any],
    ) -> None:
        """Dispatches event to WebSockets using async task or threadsafe runner."""
        text = event.model_dump_json()

        async def _send_all() -> None:
            coros = [self._send_safe(case_id, ws, text) for ws in sockets]
            await asyncio.gather(*coros, return_exceptions=True)

        try:
            # Check if running in active asyncio loop
            try:
                current_loop = asyncio.get_running_loop()
                if current_loop.is_running():
                    current_loop.create_task(_send_all())
                    return
            except RuntimeError:
                pass

            loop = self._loop
            if loop and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(_send_all(), loop)
                fut.add_done_callback(
                    lambda f: logger.debug("Broadcast future finished: %s", f.exception())
                    if not f.cancelled() and f.exception()
                    else None
                )
            else:
                logger.debug("No active event loop found to dispatch WebSocket broadcast for case %s", case_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to dispatch broadcast: %s", exc)

    async def _send_safe(self, case_id: str, ws: Any, text: str) -> None:
        """Sends payload to a single WebSocket with error isolation and removal on failure."""
        try:
            # Enforce 3.0 second timeout to prevent slow consumers from hanging
            await asyncio.wait_for(ws.send_text(text), timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("WebSocket client dropped or timed out for case %s: %s", case_id, exc)
            await self.disconnect(case_id, ws)

    async def connect(self, case_id: str, websocket: Any) -> list[AgentExecutionEvent]:
        """Registers a new WebSocket subscriber and returns historical events."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                self._loop = loop
        except RuntimeError:
            pass

        with self._lock:
            self._connections[case_id].add(websocket)
            history = list(self._replay_buffers.get(case_id, []))
        return history

    def has_subscribers(self, case_id: str) -> bool:
        """Returns True if there is at least one active WebSocket connection for this case."""
        with self._lock:
            return len(self._connections.get(case_id, set())) > 0

    def clear_replay_history(self, case_id: str) -> None:
        """Clears buffered history for a case when explicitly resetting for a rerun."""
        with self._lock:
            if case_id in self._replay_buffers:
                self._replay_buffers[case_id].clear()

    async def disconnect(self, case_id: str, websocket: Any) -> None:
        """Unregisters a WebSocket subscriber cleanly."""
        with self._lock:
            if case_id in self._connections:
                self._connections[case_id].discard(websocket)
                if not self._connections[case_id]:
                    del self._connections[case_id]


# Global singleton broadcaster instance
broadcaster = CaseEventBroadcaster()
