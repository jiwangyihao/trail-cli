from __future__ import annotations

import re
import threading

from trail.runtime.debug_recorder import DebugTraceRecorder


def test_debug_trace_recorder_scope_isolated_per_thread() -> None:
    recorder = DebugTraceRecorder()
    payloads: dict[str, list[dict[str, object]]] = {}
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        recorder.begin_scope()
        try:
            recorder.append_trace({"step": "capture_after_action", "thread": name})
            barrier.wait(timeout=1)
            payloads[name] = recorder.consume_trace()
        finally:
            recorder.end_scope()

    left = threading.Thread(target=worker, args=("left",))
    right = threading.Thread(target=worker, args=("right",))
    left.start()
    right.start()
    left.join(timeout=2)
    right.join(timeout=2)

    assert payloads["left"] == [{"step": "capture_after_action", "thread": "left"}]
    assert payloads["right"] == [{"step": "capture_after_action", "thread": "right"}]


def test_debug_trace_recorder_consume_trace_is_one_shot() -> None:
    recorder = DebugTraceRecorder()
    recorder.append_trace({"step": "click_point"})

    assert recorder.consume_trace() == [{"step": "click_point"}]
    assert recorder.consume_trace() == []


def test_debug_trace_recorder_consume_context_is_one_shot_without_scope() -> None:
    recorder = DebugTraceRecorder()
    recorder.set_context(mode_requested="fast", retry_high=1)

    assert recorder.consume_context() == {"mode_requested": "fast", "retry_high": 1}
    assert recorder.consume_context() == {}


def test_debug_trace_recorder_write_methods_are_best_effort_on_internal_failure(monkeypatch) -> None:
    recorder = DebugTraceRecorder()
    monkeypatch.setattr(recorder, "_active_buffer", lambda: (_ for _ in ()).throw(RuntimeError("buffer boom")))

    recorder.append_trace({"step": "click_point"})
    recorder.set_context(mode_requested="fast")

    assert recorder.consume_trace() == []
    assert recorder.consume_context() == {}


def test_debug_trace_recorder_write_methods_preserve_existing_state_on_internal_failure() -> None:
    recorder = DebugTraceRecorder()
    recorder.append_trace({"step": "existing"})
    recorder.set_context(existing="value")

    class BrokenTrace(list[dict[str, object]]):
        def append(self, item: dict[str, object]) -> None:
            del item
            raise RuntimeError("trace boom")

    class BrokenContext(dict[str, object]):
        def update(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("context boom")

    recorder._global.trace = BrokenTrace(recorder._global.trace)
    recorder._global.context = BrokenContext(recorder._global.context)

    recorder.append_trace({"step": "new"})
    recorder.set_context(new="value")

    assert recorder.consume_trace() == [{"step": "existing"}]
    assert recorder.consume_context() == {"existing": "value"}


def test_debug_trace_recorder_consume_methods_are_best_effort_on_internal_failure(monkeypatch) -> None:
    recorder = DebugTraceRecorder()
    monkeypatch.setattr(recorder, "_consume_trace_buffer", lambda: (_ for _ in ()).throw(RuntimeError("trace boom")))
    monkeypatch.setattr(recorder, "_consume_context_buffer", lambda: (_ for _ in ()).throw(RuntimeError("context boom")))

    assert recorder.consume_trace() == []
    assert recorder.consume_context() == {}


def test_debug_trace_recorder_consume_trace_restores_existing_payload_after_cleanup_failure(monkeypatch) -> None:
    recorder = DebugTraceRecorder()
    recorder.append_trace({"step": "existing"})

    state = {"calls": 0}

    def flaky_cleanup() -> None:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("cleanup boom")

    monkeypatch.setattr(recorder, "_clear_pending_buffers_if_drained", flaky_cleanup)

    assert recorder.consume_trace() == []
    assert recorder.consume_trace() == [{"step": "existing"}]
    assert recorder.consume_trace() == []


def test_debug_trace_recorder_consume_context_restores_existing_payload_after_cleanup_failure(monkeypatch) -> None:
    recorder = DebugTraceRecorder()
    recorder.set_context(existing="value")

    state = {"calls": 0}

    def flaky_cleanup() -> None:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("cleanup boom")

    monkeypatch.setattr(recorder, "_clear_pending_buffers_if_drained", flaky_cleanup)

    assert recorder.consume_context() == {}
    assert recorder.consume_context() == {"existing": "value"}
    assert recorder.consume_context() == {}


def test_debug_trace_recorder_scope_context_does_not_pollute_global_fallback() -> None:
    recorder = DebugTraceRecorder()
    recorder.set_context(global_key="global")

    recorder.begin_scope()
    try:
        recorder.set_context(scope_key="scope")
        assert recorder.consume_context() == {"scope_key": "scope"}
        assert recorder.consume_context() == {}
    finally:
        recorder.end_scope()

    assert recorder.consume_context() == {"global_key": "global"}
    assert recorder.consume_context() == {}


def test_debug_trace_recorder_finalize_action_keeps_ok_and_ts() -> None:
    recorder = DebugTraceRecorder()
    action = recorder.begin_action("click_point", point=[10, 20])
    action.finish(ok=True, screen_point=[110, 120])

    [event] = recorder.consume_trace()
    assert event["step"] == "click_point"
    assert type(event["ok"]) is int
    assert event["ok"] == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event["ts"])
    assert event["point"] == [10, 20]
    assert event["screen_point"] == [110, 120]
    assert "dur_ms" in event
    assert type(event["dur_ms"]) is int


def test_debug_trace_recorder_finalize_action_failure_keeps_ok_zero_and_ts() -> None:
    recorder = DebugTraceRecorder()
    action = recorder.begin_action("ocr", capture="shot.png")
    action.finish(ok=False, error_code="OCR_BACKEND_UNAVAILABLE", msg="ocr backend unavailable")

    [event] = recorder.consume_trace()
    assert event["step"] == "ocr"
    assert type(event["ok"]) is int
    assert event["ok"] == 0
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event["ts"])
    assert event["capture"] == "shot.png"
    assert event["error_code"] == "OCR_BACKEND_UNAVAILABLE"
    assert event["msg"] == "ocr backend unavailable"
    assert "dur_ms" in event
    assert type(event["dur_ms"]) is int


def test_debug_trace_recorder_finish_is_best_effort_and_drains_pending_after_append_failure() -> None:
    recorder = DebugTraceRecorder()

    class BrokenTrace(list[dict[str, object]]):
        def append(self, item: dict[str, object]) -> None:
            del item
            raise RuntimeError("trace boom")

    recorder.begin_scope()
    action = recorder.begin_action("click_point", point=[1, 2])
    recorder.end_scope()
    action._buffer.trace = BrokenTrace()

    recorder.append_trace({"step": "global"})
    recorder.set_context(global_key="global")

    action.finish(ok=True, screen_point=[3, 4])

    assert recorder.consume_trace() == [{"step": "global"}]
    assert recorder.consume_context() == {"global_key": "global"}
    assert getattr(recorder._local, "pending_buffers", []) == []


def test_debug_trace_recorder_repeated_finish_is_no_op_after_first_event() -> None:
    recorder = DebugTraceRecorder()
    action = recorder.begin_action("click_point", point=[10, 20])

    action.finish(ok=True, screen_point=[110, 120])
    action.finish(ok=False, error_code="SHOULD_NOT_APPEAR")

    events = recorder.consume_trace()
    assert len(events) == 1
    assert events[0]["step"] == "click_point"
    assert events[0]["point"] == [10, 20]
    assert events[0]["screen_point"] == [110, 120]
    assert events[0]["ok"] == 1
    assert "error_code" not in events[0]


def test_debug_trace_recorder_action_keeps_creation_scope_buffer_after_scope_end() -> None:
    recorder = DebugTraceRecorder()

    recorder.begin_scope()
    action = recorder.begin_action("click_point", point=[1, 2])
    recorder.end_scope()

    recorder.append_trace({"step": "global"})
    action.finish(ok=True, screen_point=[3, 4])

    first = recorder.consume_trace()
    assert len(first) == 1
    assert first[0]["step"] == "click_point"
    assert first[0]["point"] == [1, 2]
    assert first[0]["screen_point"] == [3, 4]
    assert first[0]["ok"] == 1

    assert recorder.consume_trace() == [{"step": "global"}]
    assert recorder.consume_trace() == []


def test_debug_trace_recorder_hanging_action_does_not_block_global_trace() -> None:
    recorder = DebugTraceRecorder()

    recorder.begin_scope()
    action = recorder.begin_action("click_point", point=[1, 2])
    recorder.end_scope()

    recorder.append_trace({"step": "global"})

    assert recorder.consume_trace() == [{"step": "global"}]

    action.finish(ok=True, screen_point=[3, 4])

    late = recorder.consume_trace()
    assert len(late) == 1
    assert late[0]["step"] == "click_point"
    assert late[0]["point"] == [1, 2]
    assert late[0]["screen_point"] == [3, 4]
    assert late[0]["ok"] == 1
    assert recorder.consume_trace() == []


def test_debug_trace_recorder_hanging_action_does_not_block_global_context() -> None:
    recorder = DebugTraceRecorder()

    recorder.begin_scope()
    recorder.begin_action("click_point", point=[1, 2])
    recorder.end_scope()

    recorder.set_context(global_key="global")

    assert recorder.consume_context() == {"global_key": "global"}
    assert recorder.consume_context() == {}


def test_debug_trace_recorder_pending_buffers_do_not_mix_trace_and_context_across_requests() -> None:
    recorder = DebugTraceRecorder()

    recorder.begin_scope()
    recorder.set_context(request="A")
    recorder.end_scope()

    recorder.begin_scope()
    recorder.append_trace({"step": "request-b"})
    recorder.end_scope()

    assert recorder.consume_trace() == []
    assert recorder.consume_context() == {"request": "A"}
    assert recorder.consume_trace() == [{"step": "request-b"}]
    assert recorder.consume_context() == {}


def test_debug_trace_recorder_nested_scope_shares_same_request_buffer() -> None:
    recorder = DebugTraceRecorder()
    recorder.begin_scope()
    try:
        recorder.append_trace({"step": "outer"})
        recorder.begin_scope()
        try:
            recorder.append_trace({"step": "inner"})
        finally:
            recorder.end_scope()

        assert recorder.consume_trace() == [{"step": "outer"}, {"step": "inner"}]
        assert recorder.consume_trace() == []
    finally:
        recorder.end_scope()
