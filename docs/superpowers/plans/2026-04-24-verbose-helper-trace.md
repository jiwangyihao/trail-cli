# Verbose Helper Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改具体命令文件的前提下，让 `--verbose` 稳定输出 shared helper 的 finalized major action trace、绝对时间戳和失败尝试记录。

**Architecture:** 在 `trail/runtime/` 新增独立 recorder，作为 `RuntimeOperator` 的内部实现细节，继续通过现有 `consume_debug_trace()` / `consume_debug_context()` 暴露给 capture 层。`trail/output/capture.py` 取消 OCR allowlist 特例，统一收集 recorder 快照；`trail/output/debug.py` 保持单一归一化/渲染出口，并在 `trail/daemon/cw_service.py` 的 mutation shared wrapper 上扩展 request scope 生命周期，覆盖“动作先发生、截图后发生”的链路。

**Tech Stack:** Python 3.12、Typer CLI、pytest、uv、现有 Trail envelope / renderer / daemon runtime。

---

**Approved Spec:** `C:\Users\34404\source\repos\trail-cli\trail\docs\superpowers\specs\2026-04-23-verbose-helper-trace-design.md`

**Plan:** `C:\Users\34404\source\repos\trail-cli\.worktrees\verbose-helper-trace\docs\superpowers\plans\2026-04-24-verbose-helper-trace.md`

**Worktree:** `C:\Users\34404\source\repos\trail-cli\.worktrees\verbose-helper-trace`

**Baseline:** 在该 worktree 内执行 `uv run pytest --basetemp .pytest-tmp`，当前基线为 `1218 passed`。

**Repo policy note:** 本仓库执行阶段不主动创建 git commit；只有用户明确要求时才提交。

## File Map

- Create: `trail/runtime/debug_recorder.py`
  责任：封装 request scope / non-scope 双缓冲、finalized action event、best-effort 记录与消费语义。
- Create: `tests/test_debug_recorder.py`
  责任：锁定 recorder 的并发隔离、consume+clear、best-effort 与 finalized event 语义。
- Modify: `trail/runtime/operator.py`
  责任：把现有 `_trace` / `_debug_context` / capture scope 逻辑接到 recorder，并为 shared helper 产出 finalized action trace。
- Modify: `trail/output/capture.py`
  责任：移除 `_OCR_DEBUG_CONTEXT_ALLOWLIST`，继续通过现有 consume 接口统一收集 debug 快照，并保持 safe-collect best-effort。
- Modify: `trail/output/debug.py`
  责任：作为 verbose 单入口，做 trace 归一化、`box` canonical 化和 legacy trace 兼容。
- Modify: `trail/daemon/cw_service.py`
  责任：在 mutation shared wrapper 上提前开启 / 延后关闭 request scope，覆盖 pre-capture action trace。
- Modify: `tests/test_runtime_backends.py`
  责任：锁定 runtime helper 成功/失败路径、`ok=0/1`、`ts`、`dur_ms` 与原始 `box` shape。
- Modify: `tests/test_output_envelope.py`
  责任：锁定 capture debug shape、allowlist 退场、mutation shared scope 与 safe-collect。
- Modify: `tests/test_output_debug.py`
  责任：锁定 `collect_debug_events()` / `render_debug_lines()` 的 normalized output、`box` canonical 化与 OCR trace 迁移。
- Modify: `tests/test_output_rendering.py`
  责任：锁定 README / AGENTS verbose 契约、OCR stdout 形状与“默认模式不泄漏 debug”。
- Modify: `tests/test_daemon_protocol.py`
  责任：证明无需改 command/scene 即可经 shared helper 透传更完整 verbose trace。
- Modify: `tests/test_skill_structure.py`
  责任：锁定 `trail-hsr-advanced` skill/reference 的同步结果。
- Modify: `README.md`
  责任：补 verbose major action trace 语义、示例与读取方式。
- Modify: `AGENTS.md`
  责任：补 verbose major action trace、绝对时间戳、trace/context 边界与 best-effort 原则。
- Modify: `skills/trail-hsr-advanced/SKILL.md`
  责任：同步体现 advanced 诊断链路对 verbose major action trace 的读取边界，避免只改 README / reference。
- Modify: `skills/trail-hsr-advanced/references/advanced-command-surface.md`
  责任：同步 advanced 诊断面上对 verbose trace 的读取说明。

### Task 1: Introduce Recorder Core

**Files:**
- Create: `trail/runtime/debug_recorder.py`
- Test: `tests/test_debug_recorder.py`

- [ ] **Step 1: 写出 recorder 的失败测试**

```python
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


def test_debug_trace_recorder_finalize_action_keeps_ok_and_ts() -> None:
    recorder = DebugTraceRecorder()
    action = recorder.begin_action("click_point", point=[10, 20])
    action.finish(ok=True, screen_point=[110, 120])

    [event] = recorder.consume_trace()
    assert event["step"] == "click_point"
    assert type(event["ok"]) is int
    assert event["ok"] == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event["ts"])
    assert event["screen_point"] == [110, 120]


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
```

- [ ] **Step 2: 运行新测试并确认失败**

Run: `uv run pytest tests/test_debug_recorder.py --basetemp .pytest-tmp -v`

Expected: FAIL，报 `ModuleNotFoundError: No module named 'trail.runtime.debug_recorder'` 或缺少 `DebugTraceRecorder` / `begin_action`。

- [ ] **Step 3: 实现 recorder 核心模块**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import local
from time import perf_counter
from typing import Any


def _utc_now_rfc3339_ms() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class _RecorderBuffer:
    trace: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    scope_depth: int = 0


class _RecordedAction:
    def __init__(self, recorder: "DebugTraceRecorder", step: str, payload: dict[str, Any]):
        self._recorder = recorder
        self._step = step
        self._payload = dict(payload)
        self._started = perf_counter()

    def finish(self, *, ok: bool, **payload: Any) -> None:
        event = {
            "step": self._step,
            "ts": _utc_now_rfc3339_ms(),
            "ok": 1 if ok else 0,
            "dur_ms": int((perf_counter() - self._started) * 1000),
            **self._payload,
            **payload,
        }
        self._recorder.append_trace(event)


class DebugTraceRecorder:
    def __init__(self) -> None:
        self._local = local()
        self._global = _RecorderBuffer()

    def _buffer(self) -> _RecorderBuffer:
        if getattr(self._local, "scope_depth", 0):
            if not hasattr(self._local, "buffer"):
                self._local.buffer = _RecorderBuffer(scope_depth=self._local.scope_depth)
            return self._local.buffer
        return self._global

    def begin_scope(self) -> None:
        depth = int(getattr(self._local, "scope_depth", 0)) + 1
        self._local.scope_depth = depth
        if depth == 1:
            self._local.buffer = _RecorderBuffer(scope_depth=depth)

    def end_scope(self) -> None:
        depth = int(getattr(self._local, "scope_depth", 0))
        self._local.scope_depth = max(0, depth - 1)

    def begin_action(self, step: str, **payload: Any) -> _RecordedAction:
        return _RecordedAction(self, step, payload)

    def append_trace(self, payload: dict[str, Any]) -> None:
        self._buffer().trace.append(dict(payload))

    def set_context(self, **payload: Any) -> None:
        self._buffer().context.update(payload)

    def consume_trace(self) -> list[dict[str, Any]]:
        buffer = self._buffer()
        trace = list(buffer.trace)
        buffer.trace = []
        return trace

    def consume_context(self) -> dict[str, Any]:
        buffer = self._buffer()
        context = dict(buffer.context)
        buffer.context = {}
        return context
```

- [ ] **Step 4: 重新运行 recorder 测试**

Run: `uv run pytest tests/test_debug_recorder.py --basetemp .pytest-tmp -v`

Expected: PASS，至少通过 4 个 recorder 基础用例，并严格锁住 `ok=0/1` 与 RFC3339 毫秒时间戳格式。

### Task 2: Harden Recorder Safety and Runtime Integration

**Files:**
- Modify: `trail/runtime/debug_recorder.py`
- Modify: `trail/runtime/operator.py:95-256`
- Test: `tests/test_debug_recorder.py`
- Test: `tests/test_runtime_backends.py`

- [ ] **Step 1: 先写真正会变红的 safety / integration 测试**

```python
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_runtime_operator_constructs_debug_trace_recorder() -> None:
    import trail.runtime.operator as operator_module
    from trail.runtime.debug_recorder import DebugTraceRecorder

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None, hotkey=lambda *keys: None, type_text=lambda text: None),
    )

    assert isinstance(runtime._debug_recorder, DebugTraceRecorder)


def test_runtime_operator_recorder_failure_does_not_override_original_error() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(prepare_input=lambda: None, is_foreground=lambda: False, to_screen_point=lambda x, y: (x, y), capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(ensure_available=lambda: None, click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None, hotkey=lambda *keys: None, type_text=lambda text: None),
    )
    runtime._debug_recorder.append_trace = lambda payload: (_ for _ in ()).throw(RuntimeError("recorder boom"))

    with pytest.raises(operator_module.TrailError, match="窗口不在前台"):
        runtime.click_point(10, 20)
```

- [ ] **Step 2: 运行 safety / integration 测试并确认失败**

Run: `uv run pytest tests/test_debug_recorder.py tests/test_runtime_backends.py -k "constructs_debug_trace_recorder or recorder_failure_does_not_override_original_error" --basetemp .pytest-tmp -v`

Expected: FAIL，当前还没有 `_debug_recorder` 接线，也没有把 recorder 写失败包成 best-effort。

- [ ] **Step 3: 把 recorder 的 best-effort 与 `RuntimeOperator` 接线一起补齐**

```python
from trail.runtime.debug_recorder import DebugTraceRecorder


class RuntimeOperator:
    def __init__(self, window, matcher, ocr_engine, input_driver, *, reference_root=None):
        self.window = window
        self.matcher = matcher
        self.ocr_engine = ocr_engine
        self.input = input_driver
        self.reference_root = Path.cwd() if reference_root is None else Path(reference_root)
        self._warnings: list[dict[str, Any]] = []
        self._debug_recorder = DebugTraceRecorder()
        self._request_local = threading.local()
        self._last_input_at: float | None = None
        self.raise_post_input_foreground_error = False

    def begin_capture_scope(self) -> None:
        depth = int(getattr(self._request_local, "capture_scope_depth", 0)) + 1
        self._request_local.capture_scope_depth = depth
        if depth == 1:
            self._request_local.warnings = []
        self._debug_recorder.begin_scope()

    def end_capture_scope(self) -> None:
        depth = int(getattr(self._request_local, "capture_scope_depth", 0))
        self._request_local.capture_scope_depth = max(0, depth - 1)
        self._debug_recorder.end_scope()

    def _record_trace(self, step: str, **payload: Any) -> None:
        try:
            self._debug_recorder.append_trace({"step": step, **payload})
        except Exception:
            return

    def _append_trace(self, payload: dict[str, Any]) -> None:
        try:
            self._debug_recorder.append_trace(payload)
        except Exception:
            return

    def _set_debug_context(self, **payload: Any) -> None:
        try:
            self._debug_recorder.set_context(**payload)
        except Exception:
            return

    def consume_debug_trace(self) -> list[dict[str, Any]]:
        return self._debug_recorder.consume_trace()

    def consume_debug_context(self) -> dict[str, Any]:
        return self._debug_recorder.consume_context()
```

```python
class DebugTraceRecorder:
    def append_trace(self, payload: dict[str, Any]) -> None:
        try:
            self._buffer().trace.append(dict(payload))
        except Exception:
            return

    def set_context(self, **payload: Any) -> None:
        try:
            self._buffer().context.update(payload)
        except Exception:
            return

    def consume_trace(self) -> list[dict[str, Any]]:
        try:
            buffer = self._buffer()
            trace = list(buffer.trace)
            buffer.trace = []
            return trace
        except Exception:
            return []

    def consume_context(self) -> dict[str, Any]:
        try:
            buffer = self._buffer()
            context = dict(buffer.context)
            buffer.context = {}
            return context
        except Exception:
            return {}
```

- [ ] **Step 4: 重新运行 safety / integration 测试**

Run: `uv run pytest tests/test_debug_recorder.py tests/test_runtime_backends.py -k "recorder" --basetemp .pytest-tmp -v`

Expected: PASS，recorder 已接到 `RuntimeOperator`，且 recorder 故障不再覆盖原始 helper 异常。

### Task 3: Finalize Helper Trace in RuntimeOperator

**Files:**
- Modify: `trail/runtime/operator.py:179-648,840-883`
- Test: `tests/test_runtime_backends.py`

- [ ] **Step 1: 先写 helper finalized trace 的失败测试**

```python
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


@pytest.mark.parametrize(
    ("runner", "expected_step"),
    [
        (lambda runtime: runtime.click_point(10, 20), "click_point"),
        (lambda runtime: runtime.drag_to(10, 20, 30, 40), "drag_to"),
        (lambda runtime: runtime.press_key("f", presses=1), "press_key"),
        (lambda runtime: runtime.hotkey("ctrl", "l"), "hotkey"),
        (lambda runtime: runtime.type_text("abc"), "type_text"),
    ],
)
def test_runtime_operator_input_helpers_emit_finalized_trace_with_ok_ts_and_duration(runner, expected_step) -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(prepare_input=lambda: None, is_foreground=lambda: True, to_screen_point=lambda x, y: (x, y), capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(ensure_available=lambda: None, click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None, hotkey=lambda *keys: None, type_text=lambda text: None),
    )

    runner(runtime)

    trace = runtime.consume_debug_trace()
    event = trace[-1]
    assert event["step"] == expected_step
    assert type(event["ok"]) is int
    assert event["ok"] == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event["ts"])
    assert "dur_ms" in event


def test_runtime_operator_capture_after_action_optional_failure_keeps_failed_trace() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: (_ for _ in ()).throw(RuntimeError("capture failed"))),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None, hotkey=lambda *keys: None, type_text=lambda text: None),
    )

    assert runtime.capture_after_action(optional=True) is None
    [event] = runtime.consume_debug_trace()
    assert event["step"] == "capture_after_action"
    assert event["ok"] == 0
    assert event["optional"] == 1


def test_runtime_operator_ocr_failure_emits_finalized_ocr_trace_and_keeps_provider_trace(tmp_path: Path) -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: Image.new("RGB", (32, 32), color="white"), capture_to_workspace=lambda request_id=None: tmp_path / "shot.png"),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: (_ for _ in ()).throw(operator_module.OcrRunFailure("OCR_BACKEND_UNAVAILABLE", "ocr backend unavailable", trace=[{"step": "ocr_provider", "requested_provider": "cpu"}]))),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None, hotkey=lambda *keys: None, type_text=lambda text: None),
    )

    with pytest.raises(operator_module.OcrRunFailure):
        runtime.ocr(capture={})

    trace = runtime.consume_debug_trace()
    assert any(item.get("step") == "ocr_provider" for item in trace)
    assert any(item.get("step") == "ocr" and item.get("ok") == 0 for item in trace)


def test_runtime_operator_wait_img_and_locate_keep_raw_box_until_debug_layer() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: operator_module.Box(left=10, top=20, width=30, height=40, source="template")),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None, hotkey=lambda *keys: None, type_text=lambda text: None),
    )

    runtime.wait_img("entry.start", timeout=1, interval=0.1)

    locate_event = next(item for item in runtime.consume_debug_trace() if item.get("step") == "locate")
    assert isinstance(locate_event["box"], dict)
    assert locate_event["box"] == {"left": 10, "top": 20, "width": 30, "height": 40, "source": "template"}


def test_runtime_operator_screenshot_and_ocr_image_emit_finalized_trace() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[{"text": "进入"}], warnings=[], trace=[])),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None, hotkey=lambda *keys: None, type_text=lambda text: None),
    )

    runtime.screenshot()
    runtime.ocr_image(b"demo")

    steps = [item["step"] for item in runtime.consume_debug_trace()]
    assert "screenshot" in steps
    assert "ocr_image" in steps


def test_runtime_operator_foreground_checks_emit_finalized_trace_on_success_and_failure() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(prepare_input=lambda: None, is_foreground=lambda: False, to_screen_point=lambda x, y: (x, y), capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(ensure_available=lambda: None, click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None, hotkey=lambda *keys: None, type_text=lambda text: None),
    )

    with pytest.raises(operator_module.TrailError):
        runtime.click_point(10, 20)

    steps = [item["step"] for item in runtime.consume_debug_trace()]
    assert "prepare_input" in steps
    assert "foreground_prepare_check" in steps
```

- [ ] **Step 2: 运行 helper 测试并确认失败**

Run: `uv run pytest tests/test_runtime_backends.py -k "input_helpers_emit_finalized_trace or optional_failure_keeps_failed_trace or emits_finalized_ocr_trace or wait_img_and_locate_keep_raw_box or screenshot_and_ocr_image_emit_finalized_trace or foreground_checks_emit_finalized_trace" --basetemp .pytest-tmp -v`

Expected: FAIL，当前 trace 还没有统一 `ts` / `ok` / `dur_ms`，OCR 仍依赖 context 特例。

- [ ] **Step 3: 为 shared helper 补 finalized action 记录**

```python
def _begin_debug_action(self, step: str, **payload: Any):
    return self._debug_recorder.begin_action(step, **payload)


def click_point(self, x: float, y: float, **kwargs):
    self._prepare_input_target()
    screen_x, screen_y = self._to_screen_point(x, y)
    action = self._begin_debug_action("click_point", point=[x, y], screen_point=[screen_x, screen_y])
    try:
        self.input.click(screen_x, screen_y, **kwargs)
        self._mark_input_action()
        self._check_foreground_after_input()
    except Exception as error:
        action.finish(ok=False, error_type=type(error).__name__, msg=str(error))
        raise
    action.finish(ok=True)


def capture_after_action(self, optional: bool = False, request_id: str | None = None):
    action = self._begin_debug_action("capture_after_action", optional=1 if optional else 0)
    try:
        self._wait_for_post_input_settle()
        path = self.window.capture_to_workspace(request_id=request_id) if request_id is not None else self.window.capture_to_workspace()
    except Exception as error:
        if optional:
            action.finish(ok=False, error_type=type(error).__name__, msg=str(error))
            return None
        action.finish(ok=False, error_type=type(error).__name__, msg=str(error))
        raise
    action.finish(ok=True, screenshot=str(path))
    return path


def _finalize_ocr_trace(self, *, step: str, ok: bool, pieces: int, mode_requested: str, mode_effective: str, retry_high: bool, retry_reason: str, **payload: Any) -> None:
    self._debug_recorder.begin_action(step).finish(
        ok=ok,
        pieces=pieces,
        mode_requested=mode_requested,
        mode_effective=mode_effective,
        retry_high=1 if retry_high else 0,
        retry_reason=retry_reason,
        **payload,
    )
```

Implementation notes for this step:
- `screenshot`、`locate`、`wait_img`、`drag_to`、`press_key`、`hotkey`、`type_text`、`prepare_input`、`foreground_prepare_check`、`foreground_check` 都按同一 finalized event 模式补齐。
- OCR 要保留现有 `ocr_provider` trace，同时把 `ocr_mode_requested` / `ocr_mode_effective` / `ocr_scale_applied` / `ocr_retry_high` / `ocr_retry_reason` 迁入 `ocr` / `ocr_image` 的 finalized trace。
- runtime 原始 `box` 保持 dict/可归一化 shape，不在这一层提前压成字符串。
- 这一 task 内要同步改掉 `tests/test_runtime_backends.py` 里已存在的旧断言：`prepare_input`、`drag_to`、`press_key`、`hotkey`、`type_text`、`capture_after_action`、`ocr_image` 不能继续只断言旧 raw trace；要么迁到 finalized trace 断言，要么明确列为 legacy compatibility case。
- 如果 `foreground_check` / `foreground_prepare_check` / `screenshot` 当前没有单独测试名，就先在 `tests/test_runtime_backends.py` 增补对应测试名，再和上面的 helper matrix 一起转绿。

- [ ] **Step 4: 跑 runtime helper 聚焦测试**

Run: `uv run pytest tests/test_runtime_backends.py -k "click_point or drag_to or press_key or hotkey or type_text or prepare_input or foreground_check or capture_after_action or screenshot or locate or wait_img or ocr or ocr_image" --basetemp .pytest-tmp -v`

Expected: PASS，新增用例通过，旧有 OCR/provider/runtime 用例未回退。

### Task 4: Generalize Capture Collection and Mutation Scope

**Files:**
- Modify: `trail/output/capture.py:9-192`
- Modify: `trail/daemon/cw_service.py:193-305`
- Test: `tests/test_output_envelope.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写 capture / mutation scope 的失败测试**

```python
import threading
from pathlib import Path
from types import SimpleNamespace

def test_with_auto_capture_keeps_non_ocr_debug_context_without_allowlist(tmp_path):
    runtime = SimpleNamespace(
        capture_after_action=lambda optional=False: tmp_path / "shot.png",
        collect_warnings=lambda: [],
        match_references=lambda screenshot_path, limit=3: [],
        consume_debug_trace=lambda: [{"step": "click_point", "ts": "2026-04-24T08:15:30.123Z", "ok": 1}],
        consume_debug_context=lambda: {"last_known_stage": "handler_completed"},
    )

    result = with_auto_capture(runtime, lambda: {"done": True}, verbose=True)

    assert result["debug"] == {
        "trace": [{"step": "click_point", "ts": "2026-04-24T08:15:30.123Z", "ok": 1}],
        "last_known_stage": "handler_completed",
    }


def test_cw_mutation_scope_collects_trace_emitted_before_auto_capture(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry
    from tests.test_daemon_protocol import ProtocolRuntimeService

    class ScopeAwareRuntime:
        def __init__(self):
            self._local = threading.local()

        def begin_capture_scope(self):
            depth = int(getattr(self._local, "depth", 0)) + 1
            self._local.depth = depth
            if depth == 1:
                self._local.trace = []
                self._local.context = {}

        def end_capture_scope(self):
            depth = int(getattr(self._local, "depth", 0))
            self._local.depth = max(0, depth - 1)

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            if getattr(self._local, "depth", 0):
                self._local.trace.append({"step": "click_point", "ts": "2026-04-24T08:15:30.123Z", "ok": 1, "point": [x, y]})

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / "mutation.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

        def consume_debug_trace(self):
            trace = list(getattr(self._local, "trace", []))
            self._local.trace = []
            return trace

        def consume_debug_context(self):
            context = dict(getattr(self._local, "context", {}))
            self._local.context = {}
            return context

    runtime = ScopeAwareRuntime()
    runtime_service = ProtocolRuntimeService(runtime)
    service = CwService(runtime_service=runtime_service)
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    def fake_start_cw(session, *, runtime, **kwargs):
        del session, kwargs
        runtime.click_point(10, 20)
        return {"cards": 1}

    monkeypatch.setattr("trail.daemon.cw_service.start_cw", fake_start_cw)

    payload = service.handle_mutation(
        method="cw.start",
        payload={"session_id": session.session_id, "mode": "new", "difficulty": "lowest", "battle_mode": "standard"},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-1",
        verbose=True,
    )

    assert any(item.get("step") == "click_point" for item in payload["debug"]["trace"])


def test_with_auto_capture_collect_failure_only_drops_debug(tmp_path):
    runtime = SimpleNamespace(
        capture_after_action=lambda optional=False: tmp_path / "shot.png",
        collect_warnings=lambda: [],
        match_references=lambda screenshot_path, limit=3: [],
        consume_debug_trace=lambda: (_ for _ in ()).throw(RuntimeError("trace boom")),
        consume_debug_context=lambda: {"last_known_stage": "handler_completed"},
    )

    payload = with_auto_capture(runtime, lambda: {"done": True}, verbose=True)

    assert payload["ok"] is True
    assert payload["data"] == {"done": True}
    assert payload["debug"] is None


def test_cw_mutation_scope_keeps_failure_path_trace_until_known_failure_capture(tmp_path: Path, monkeypatch):
    from trail.core.errors import TrailError
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry
    from tests.test_daemon_protocol import ProtocolRuntimeService

    class ScopeAwareRuntime:
        def __init__(self):
            self._local = threading.local()

        def begin_capture_scope(self):
            depth = int(getattr(self._local, "depth", 0)) + 1
            self._local.depth = depth
            if depth == 1:
                self._local.trace = []
                self._local.context = {}

        def end_capture_scope(self):
            depth = int(getattr(self._local, "depth", 0))
            self._local.depth = max(0, depth - 1)

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self._local.trace.append({"step": "click_point", "ts": "2026-04-24T08:15:30.123Z", "ok": 1, "point": [x, y]})

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            del optional, request_id
            return tmp_path / "known-failure.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

        def consume_debug_trace(self):
            trace = list(getattr(self._local, "trace", []))
            self._local.trace = []
            return trace

        def consume_debug_context(self):
            return {}

    runtime = ScopeAwareRuntime()
    runtime_service = ProtocolRuntimeService(runtime)
    service = CwService(runtime_service=runtime_service)
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    def fake_start_cw(session, *, runtime, **kwargs):
        del session, kwargs
        runtime.click_point(10, 20)
        error = TrailError("CW_START_DIFFICULTY_RECOVERY_REQUIRED", "recovery required")
        error.known_failure_after_save = True
        error.completed_after_side_effect = True
        raise error

    monkeypatch.setattr("trail.daemon.cw_service.start_cw", fake_start_cw)

    payload = service.handle_mutation(
        method="cw.start",
        payload={"session_id": session.session_id, "mode": "new", "difficulty": "lowest", "battle_mode": "standard"},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="req-known-failure",
        verbose=True,
    )

    assert payload["ok"] is False
    assert any(item.get("step") == "click_point" for item in payload["debug"]["trace"])
```

- [ ] **Step 2: 运行 envelope / daemon 聚焦测试并确认失败**

Run: `uv run pytest tests/test_output_envelope.py tests/test_daemon_protocol.py -k "non_ocr_debug_context_without_allowlist or mutation_scope_collects_trace or collect_failure_only_drops_debug or known_failure_capture" --basetemp .pytest-tmp -v`

Expected: FAIL，当前 capture 仍受 `_OCR_DEBUG_CONTEXT_ALLOWLIST` 限制，mutation path 也没有在 handler 前开 scope。

- [ ] **Step 3: 实现通用 capture 收集与 mutation scope 生命周期**

```python
def _collect_capture_metadata(runtime, *, screenshot, verbose: bool) -> dict:
    runtime = _resolve_runtime(runtime)
    warnings: list[dict] = []
    references: list[dict] = []
    debug = None

    if runtime is not None:
        collect_warnings = getattr(runtime, "collect_warnings", None)
        if callable(collect_warnings):
            warnings = collect_warnings() or []

        match_references = getattr(runtime, "match_references", None)
        if screenshot is not None and callable(match_references):
            references = match_references(screenshot) or []

        if verbose:
            trace = getattr(runtime, "consume_debug_trace", lambda: [])() or []
            debug_context = getattr(runtime, "consume_debug_context", lambda: {})() or {}
            if trace or debug_context:
                debug = {}
                if trace:
                    debug["trace"] = trace
                if isinstance(debug_context, dict):
                    for key, value in debug_context.items():
                        if key not in {"trace", "request_id", "detail"}:
                            debug[key] = value

    return {"warnings": warnings, "references": references, "debug": debug}


def _safe_collect_capture_metadata(runtime, *, screenshot, verbose: bool) -> dict:
    try:
        return _collect_capture_metadata(runtime, screenshot=screenshot, verbose=verbose)
    except Exception:
        return {"warnings": [], "references": [], "debug": None}


def _begin_runtime_scope(runtime) -> None:
    begin_capture_scope = getattr(runtime, "begin_capture_scope", None)
    if callable(begin_capture_scope):
        begin_capture_scope()


def _end_runtime_scope(runtime) -> None:
    end_capture_scope = getattr(runtime, "end_capture_scope", None)
    if callable(end_capture_scope):
        end_capture_scope()
```

```python
resolved_runtime = runtime()
_begin_runtime_scope(resolved_runtime)
try:
    try:
        result = handlers[method]()
    except TrailError as error:
        # 保留现有 known_failure_after_save / side_effect / unknown-result 分支，只把 shared scope 包在外层。
        ...
    session_service.save_session(session)
    capture_runtime = _RequestScopedCaptureRuntime(resolved_runtime, request_id, extra_delay_seconds=extra_delay_seconds)
    return with_auto_capture(capture_runtime, lambda: result, verbose=verbose)
finally:
    _end_runtime_scope(resolved_runtime)
```

Implementation notes for this step:
- 不要按 happy path 重写 `handle_mutation()`；要在现有 success、`known_failure_after_save`、capture error、`PersistedButResponseUnknown`、`SideEffectAppliedButStateNotPersisted` 分支外侧只加 shared scope owner。
- 这一 task 的核心是：移除 `_OCR_DEBUG_CONTEXT_ALLOWLIST`，让非 OCR request-level context 原样透传；OCR mode/retry 从这里开始不再做 top-level 提升，统一留给后面的 trace 迁移任务收口。
- `with_auto_capture()` 也要切到 `_safe_collect_capture_metadata()`，不能只让 `with_selective_capture()` 独占 safe-collect。

- [ ] **Step 4: 重新运行 capture / daemon 聚焦测试**

Run: `uv run pytest tests/test_output_envelope.py tests/test_daemon_protocol.py -k "non_ocr_debug_context_without_allowlist or mutation_scope_collects_trace or collect_failure_only_drops_debug or known_failure_capture or safe_collect" --basetemp .pytest-tmp -v`

Expected: PASS，allowlist 特例退场，mutation 路径能拿到 pre-capture action trace。

### Task 5: Normalize Debug Output in One Place

**Files:**
- Modify: `trail/output/debug.py:6-65`
- Test: `tests/test_output_debug.py`
- Test: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写 debug 归一化失败测试**

Before writing new tests in this task, first rewrite the existing OCR context assertions in `tests/test_output_debug.py` and `tests/test_output_rendering.py` that currently expect `debug kind=context key=ocr_mode_requested ...` / `ocr_retry_high` / `ocr_retry_reason` top-level lines. Those old assertions must become red before the new trace-based output can turn green.

```python
def test_collect_debug_events_canonicalizes_box_only_in_debug_layer():
    debug = {
        "trace": [
            {
                "step": "locate",
                "ts": "2026-04-24T08:15:30.123Z",
                "ok": 1,
                "box": {"left": 10, "top": 20, "width": 30, "height": 40},
            }
        ]
    }

    assert collect_debug_events(debug) == [
        {
            "kind": "trace",
            "step": "locate",
            "ts": "2026-04-24T08:15:30.123Z",
            "ok": 1,
            "box": "10,20,30,40",
        }
    ]


def test_render_output_verbose_renders_ocr_facts_from_trace_not_context(tmp_path):
    payload = {
        "ok": True,
        "data": {"result": [{"text": "点击进入"}]},
        "screenshot": str(tmp_path / "ocr.png"),
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "trace": [
                {
                    "step": "ocr",
                    "ts": "2026-04-24T08:15:30.123Z",
                    "ok": 1,
                    "dur_ms": 35,
                    "pieces": 1,
                    "mode_requested": "fast",
                    "mode_effective": "high",
                    "retry_high": 1,
                    "retry_reason": "low_confidence",
                }
            ]
        },
        "error": None,
    }

    lines = render_output("ocr.read", payload, verbose=True).splitlines()
    assert "debug kind=trace step=ocr ts=2026-04-24T08:15:30.123Z ok=1 dur_ms=35 pieces=1 mode_requested=fast mode_effective=high retry_high=1 retry_reason=low_confidence" in lines
    assert not any("debug kind=context key=ocr_mode_requested" in line for line in lines)
    assert not any("debug kind=context key=ocr_retry_high" in line for line in lines)
```

- [ ] **Step 2: 运行 debug / rendering 聚焦测试并确认失败**

Run: `uv run pytest tests/test_output_debug.py tests/test_output_rendering.py -k "canonicalizes_box or renders_ocr_facts_from_trace or ocr_mode_retry_context" --basetemp .pytest-tmp -v`

Expected: FAIL，当前 `collect_debug_events()` 还不会 canonicalize `box`，而且旧的 OCR context 型断言也还没有被清理成 trace 型断言。

- [ ] **Step 3: 只在 `trail/output/debug.py` 做 trace 归一化**

```python
def _format_box(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    left = value.get("left")
    top = value.get("top")
    width = value.get("width")
    height = value.get("height")
    if None in {left, top, width, height}:
        return None
    return f"{left},{top},{width},{height}"


def _normalize_trace_event(trace: dict[str, Any]) -> dict[str, Any]:
    event: dict[str, Any] = {"kind": "trace", "step": trace.get("step") or "unknown"}
    for key, value in trace.items():
        if key == "step":
            continue
        if key == "box":
            event[key] = _format_box(value) or value
            continue
        event[key] = value
    return event


def collect_debug_events(debug: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(debug, dict):
        return []

    events: list[dict[str, Any]] = []
    request_id = debug.get("request_id")
    if request_id is not None:
        events.append({"kind": "request", "msg": request_id})

    for trace in debug.get("trace") or []:
        if isinstance(trace, dict):
            events.append(_normalize_trace_event(trace))
        else:
            events.append({"kind": "trace", "step": "unknown", "value": trace})
```

- [ ] **Step 4: 重新运行 debug / rendering 聚焦测试**

Run: `uv run pytest tests/test_output_debug.py tests/test_output_rendering.py -k "trace or canonicalizes_box or ocr" --basetemp .pytest-tmp -v`

Expected: PASS，`box` canonical 化只发生在 debug 单入口，旧 OCR context 行消失，OCR stdout 改为 trace 新形状输出。

### Task 6: Sync Docs and Run Full Verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `skills/trail-hsr-advanced/SKILL.md`
- Modify: `skills/trail-hsr-advanced/references/advanced-command-surface.md`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_skill_structure.py`

- [ ] **Step 1: 先写文档同步断言的失败测试**

```python
def test_readme_documents_verbose_major_action_trace_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "major action trace" in readme
    assert "UTC RFC3339" in readme


def test_project_agents_declares_verbose_major_action_trace_contract() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "major action trace 固定输出" in agents
    assert "UTC RFC3339" in agents
    assert "trace/context" in agents


def test_trail_hsr_advanced_docs_describe_verbose_trace_guidance() -> None:
    skill_text = TRAIL_HSR_ADVANCED_SKILL.read_text(encoding="utf-8")
    reference_text = ADVANCED_COMMAND_SURFACE.read_text(encoding="utf-8")

    assert "verbose" in skill_text
    assert "major action trace" in reference_text
    assert "--verbose" in reference_text
```

- [ ] **Step 2: 运行文档断言测试并确认失败**

Run: `uv run pytest tests/test_output_rendering.py tests/test_skill_structure.py -k "readme_documents_verbose_major_action_trace_contract or verbose_major_action_trace_contract or describe_verbose_trace_guidance" --basetemp .pytest-tmp -v`

Expected: FAIL，当前文档还没有 major action trace / absolute timestamp / advanced skill 同步说明，这些新断言会先变红。

- [ ] **Step 3: 更新 README / AGENTS / advanced skill 文档**

```md
- `--verbose` 仍然只追加 `debug kind=...` 行，不改变默认文本协议顺序。
- shared helper 的 major action trace 现在固定带 `ts=<UTC RFC3339 毫秒时间戳>` 与 `ok=0|1`。
- `trace` 只承载 finalized helper 动作事件；`context` 只承载跨动作请求级事实，不能再补动作结果。
- OCR mode/retry 事实改由 `debug kind=trace step=ocr ...` 输出；legacy trace 仍兼容渲染。
```

```md
- `trail-hsr-advanced` 在使用 `screen` / `image` / `state` 做高级排障时，可以把 `--verbose` 中的 major action trace 当作 shared helper 执行证据；若没有显式开启 `--verbose`，不要假设 stdout 会出现这些调试行。
- `references/advanced-command-surface.md` 需要补充：`major action trace`、`UTC RFC3339` 与 `trace/context` 边界。
```

- [ ] **Step 4: 重新运行文档断言测试**

Run: `uv run pytest tests/test_output_rendering.py tests/test_skill_structure.py -k "verbose or advanced" --basetemp .pytest-tmp -v`

Expected: PASS，README / AGENTS / `trail-hsr-advanced` skill/reference 的具体 verbose 文案全部同步到位。

- [ ] **Step 5: 跑最终回归**

Run: `uv run pytest --basetemp .pytest-tmp`

Expected: PASS，完整套件继续保持全绿。

## Self-Review

- Spec coverage：
  - recorder 内部实现、best-effort、request scope owner、mutation shared path、OCR trace 迁移、debug 单入口、README/AGENTS/skill 同步、完整测试矩阵都已有对应任务。
- Placeholder scan：
  - 计划中没有 `TODO` / `TBD` / “实现细节自行处理” 这类占位语句。
- Type consistency：
  - 统一使用 `DebugTraceRecorder`、`ts`、`ok`、`dur_ms`、`consume_debug_trace()` / `consume_debug_context()`、`with_auto_capture()` / `with_selective_capture()` 这组命名。

## Execution Handoff

用户已经明确选择：**Subagent-Driven**。

下一阶段按 `superpowers:subagent-driven-development` 执行本计划；每个 task 用 fresh subagent 落地，在 task 之间做 review，并继续在 worktree `C:\Users\34404\source\repos\trail-cli\.worktrees\verbose-helper-trace` 内完成实现与验证。

子代理最小上下文必须固定携带：

- Spec: `C:\Users\34404\source\repos\trail-cli\trail\docs\superpowers\specs\2026-04-23-verbose-helper-trace-design.md`
- Plan: `C:\Users\34404\source\repos\trail-cli\.worktrees\verbose-helper-trace\docs\superpowers\plans\2026-04-24-verbose-helper-trace.md`
- Worktree: `C:\Users\34404\source\repos\trail-cli\.worktrees\verbose-helper-trace`
