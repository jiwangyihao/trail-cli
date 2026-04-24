from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import local
from time import perf_counter
from typing import Any


_LOCAL_STATE_MISSING = object()


def _utc_now_rfc3339_ms() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class _RecorderBuffer:
    trace: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    open_actions: int = 0


class _RecordedAction:
    def __init__(self, buffer: _RecorderBuffer, step: str, payload: dict[str, Any]) -> None:
        self._buffer = buffer
        self._step = step
        self._payload = dict(payload)
        self._started = perf_counter()
        self._finished = False
        self._buffer.open_actions += 1

    def finish(self, *, ok: bool, **payload: Any) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            event = {
                **self._payload,
                **payload,
                "step": self._step,
                "ts": _utc_now_rfc3339_ms(),
                "ok": 1 if ok else 0,
                "dur_ms": int((perf_counter() - self._started) * 1000),
            }
            self._buffer.trace.append(event)
        except Exception:
            return
        finally:
            self._buffer.open_actions = max(0, self._buffer.open_actions - 1)


class DebugTraceRecorder:
    def __init__(self) -> None:
        self._local = local()
        self._global = _RecorderBuffer()

    def _active_buffer(self) -> _RecorderBuffer:
        if getattr(self._local, "scope_depth", 0):
            buffer = getattr(self._local, "buffer", None)
            if buffer is None:
                buffer = _RecorderBuffer()
                self._local.buffer = buffer
            return buffer
        return self._global

    def _pending_buffers(self) -> list[_RecorderBuffer]:
        pending = getattr(self._local, "pending_buffers", None)
        if pending is None:
            pending = []
            self._local.pending_buffers = pending
        return pending

    def _pending_buffer_for_drain(self) -> _RecorderBuffer | None:
        pending = getattr(self._local, "pending_buffers", None) or []
        draining = getattr(self._local, "draining_buffer", None)
        if draining is not None:
            if any(buffer is draining for buffer in pending) and (draining.trace or draining.context):
                return draining
            if hasattr(self._local, "draining_buffer"):
                del self._local.draining_buffer
        for buffer in pending:
            if buffer.trace or buffer.context:
                self._local.draining_buffer = buffer
                return buffer
        return None

    def _consume_trace_buffer(self) -> _RecorderBuffer:
        if getattr(self._local, "scope_depth", 0):
            return self._active_buffer()
        pending = self._pending_buffer_for_drain()
        if pending is not None:
            return pending
        return self._global

    def _consume_context_buffer(self) -> _RecorderBuffer:
        if getattr(self._local, "scope_depth", 0):
            return self._active_buffer()
        pending = self._pending_buffer_for_drain()
        if pending is not None:
            return pending
        return self._global

    def _clear_pending_buffers_if_drained(self) -> None:
        if getattr(self._local, "scope_depth", 0):
            return
        pending = getattr(self._local, "pending_buffers", None)
        if not pending:
            if hasattr(self._local, "draining_buffer"):
                del self._local.draining_buffer
            return
        self._local.pending_buffers = [
            buffer
            for buffer in pending
            if buffer.trace or buffer.context or buffer.open_actions
        ]
        draining = getattr(self._local, "draining_buffer", None)
        if draining is None:
            return
        if not any(buffer is draining for buffer in self._local.pending_buffers) or not (draining.trace or draining.context):
            del self._local.draining_buffer

    def begin_scope(self) -> None:
        depth = int(getattr(self._local, "scope_depth", 0)) + 1
        self._local.scope_depth = depth
        if depth == 1:
            self._local.buffer = _RecorderBuffer()

    def end_scope(self) -> None:
        depth = int(getattr(self._local, "scope_depth", 0))
        if depth <= 0:
            self._local.scope_depth = 0
            return
        if depth == 1:
            buffer = getattr(self._local, "buffer", None)
            self._local.scope_depth = 0
            if hasattr(self._local, "buffer"):
                del self._local.buffer
            if buffer is not None and (buffer.trace or buffer.context or buffer.open_actions):
                self._pending_buffers().append(buffer)
            return
        self._local.scope_depth = depth - 1

    def begin_action(self, step: str, **payload: Any) -> _RecordedAction:
        return _RecordedAction(self._active_buffer(), step, payload)

    def reset_thread_state(self) -> None:
        try:
            self._local.scope_depth = 0
            for attr in ("buffer", "pending_buffers", "draining_buffer"):
                if hasattr(self._local, attr):
                    delattr(self._local, attr)
        except Exception:
            return

    def append_trace(self, payload: dict[str, Any]) -> None:
        try:
            self._active_buffer().trace.append(dict(payload))
        except Exception:
            return

    def set_context(self, **payload: Any) -> None:
        try:
            self._active_buffer().context.update(payload)
        except Exception:
            return

    def consume_trace(self) -> list[dict[str, Any]]:
        try:
            buffer = self._consume_trace_buffer()
            trace = list(buffer.trace)
            original_trace = buffer.trace
            original_pending = getattr(self._local, "pending_buffers", _LOCAL_STATE_MISSING)
            original_draining = getattr(self._local, "draining_buffer", _LOCAL_STATE_MISSING)
            buffer.trace = []
            self._clear_pending_buffers_if_drained()
            return trace
        except Exception:
            try:
                if "buffer" in locals() and "original_trace" in locals():
                    buffer.trace = original_trace
                if "original_pending" in locals():
                    if original_pending is _LOCAL_STATE_MISSING:
                        if hasattr(self._local, "pending_buffers"):
                            del self._local.pending_buffers
                    else:
                        self._local.pending_buffers = original_pending
                if "original_draining" in locals():
                    if original_draining is _LOCAL_STATE_MISSING:
                        if hasattr(self._local, "draining_buffer"):
                            del self._local.draining_buffer
                    else:
                        self._local.draining_buffer = original_draining
            except Exception:
                pass
            return []

    def consume_context(self) -> dict[str, Any]:
        try:
            buffer = self._consume_context_buffer()
            context = dict(buffer.context)
            original_context = buffer.context
            original_pending = getattr(self._local, "pending_buffers", _LOCAL_STATE_MISSING)
            original_draining = getattr(self._local, "draining_buffer", _LOCAL_STATE_MISSING)
            buffer.context = {}
            self._clear_pending_buffers_if_drained()
            return context
        except Exception:
            try:
                if "buffer" in locals() and "original_context" in locals():
                    buffer.context = original_context
                if "original_pending" in locals():
                    if original_pending is _LOCAL_STATE_MISSING:
                        if hasattr(self._local, "pending_buffers"):
                            del self._local.pending_buffers
                    else:
                        self._local.pending_buffers = original_pending
                if "original_draining" in locals():
                    if original_draining is _LOCAL_STATE_MISSING:
                        if hasattr(self._local, "draining_buffer"):
                            del self._local.draining_buffer
                    else:
                        self._local.draining_buffer = original_draining
            except Exception:
                pass
            return {}
