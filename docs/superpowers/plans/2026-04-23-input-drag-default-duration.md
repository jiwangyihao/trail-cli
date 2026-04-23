# Input Drag Default Duration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把项目实际调用链里未显式传 `duration` 的拖动默认时长统一收敛到 `0.2s`，并保持显式传值优先、CLI/daemon 契约不变。

**Architecture:** 只改 `RuntimeOperator.drag_to()` 这一层：当调用方未传 `duration` 时，由 runtime 归一成固定 `0.2`，再把真实生效值传给 input driver，并把同一个值写入 debug trace。测试集中落在 `tests/test_runtime_backends.py`，同时把受影响的 `input_driver.drag(...)` 测试桩签名升级为可接收 `duration` 关键字参数，避免把实现信号淹没在测试基建噪音里。

**Tech Stack:** Python 3.12、pytest、Typer（仅现有命令表面，不新增参数）、仓库内 worktree、`uv run pytest --basetemp=.pytest-tmp`。

**Execution Note:** 未经用户明确要求，不创建 git commit。

**Spec Source:** `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-23-input-drag-default-duration-design.md`

**Worktree:** `C:\Users\34404\source\repos\trail-cli\.worktrees\input-drag-default-duration`

**Known Baseline:** 这台机器直接跑 `uv run pytest` 会因为系统临时目录 `C:\Users\34404\AppData\Local\Temp\pytest-of-34404` 的权限问题在 setup 阶段报 `PermissionError: [WinError 5]`。本计划里的所有 pytest 命令统一显式加 `--basetemp=.pytest-tmp`，避免把环境噪音误判成代码回归。

---

## 文件边界

- Modify: `.worktrees/input-drag-default-duration/trail/runtime/operator.py`
  只在 `RuntimeOperator.drag_to()` 收敛默认拖动时长，并把真实生效值写入 trace；不改 CLI、daemon 或 driver fallback 默认语义。
- Modify: `.worktrees/input-drag-default-duration/tests/test_runtime_backends.py`
  补 runtime 默认值归一、显式覆盖透传、trace 记录断言，并同步更新 `input_driver.drag(...)` 测试桩签名。
- Modify: `.worktrees/input-drag-default-duration/docs/superpowers/specs/2026-04-23-input-drag-default-duration-design.md`
  仅在实现中若出现与 spec 文案不一致的细节时做最小同步；默认不改。

### Task 1: 先把 runtime 默认值和显式覆盖语义锁进测试

**Files:**
- Modify: `.worktrees/input-drag-default-duration/tests/test_runtime_backends.py`
- Reference: `.worktrees/input-drag-default-duration/trail/runtime/operator.py:592-606`

- [ ] **Step 1: 写失败测试，直接锁住默认 `0.2`、显式覆盖不回落、以及 trace 记录真实时长**

```python
def test_runtime_operator_drag_defaults_duration_to_point_two_seconds():
    import trail.runtime.operator as operator_module

    calls: list[tuple] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input",))

    input_driver = SimpleNamespace(
        click=lambda x, y, **kwargs: calls.append(("click", x, y)),
        drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(
            ("drag", from_x, from_y, to_x, to_y, duration)
        ),
        press=lambda key: calls.append(("press", key)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.drag_to(1, 2, 3, 4)

    assert calls == [
        ("prepare_input",),
        ("drag", 1, 2, 3, 4, 0.2),
    ]
    assert runtime.consume_debug_trace() == [
        {
            "step": "drag_to",
            "from_point": [1, 2],
            "to_point": [3, 4],
            "duration": 0.2,
            "screen_from": [1, 2],
            "screen_to": [3, 4],
        }
    ]


def test_runtime_operator_drag_keeps_explicit_duration():
    import trail.runtime.operator as operator_module

    calls: list[tuple] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input",))

    input_driver = SimpleNamespace(
        click=lambda x, y, **kwargs: calls.append(("click", x, y)),
        drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(
            ("drag", from_x, from_y, to_x, to_y, duration)
        ),
        press=lambda key: calls.append(("press", key)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.drag_to(1, 2, 3, 4, duration=0.35)

    assert calls == [
        ("prepare_input",),
        ("drag", 1, 2, 3, 4, 0.35),
    ]
    assert runtime.consume_debug_trace() == [
        {
            "step": "drag_to",
            "from_point": [1, 2],
            "to_point": [3, 4],
            "duration": 0.35,
            "screen_from": [1, 2],
            "screen_to": [3, 4],
        }
    ]
```

- [ ] **Step 2: 同步更新现有 runtime 输入测试桩签名，避免 `duration=` 先把老测试打爆**

```python
input_driver = SimpleNamespace(
    click=lambda x, y, **kwargs: calls.append(("click", x, y)),
    drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(
        ("drag", from_x, from_y, to_x, to_y, duration)
    ),
    press=lambda key: calls.append(("press", key)),
)

assert calls == [
    ("prepare_input",),
    ("click", 10, 20),
    ("prepare_input",),
    ("drag", 1, 2, 3, 4, 0.2),
    ("prepare_input",),
    ("press", "shift"),
]
```

把同样的 `drag(..., duration=None)` 调整同步到当前文件里另外两处 runtime 坐标转换测试：

```python
input_driver = SimpleNamespace(
    click=lambda x, y, **kwargs: calls.append(("click", x, y)),
    drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(
        ("drag", from_x, from_y, to_x, to_y, duration)
    ),
    press=lambda key: calls.append(("press", key)),
)

assert calls == [
    ("prepare_input",),
    ("click", 110, 220),
    ("prepare_input",),
    ("drag", 101, 202, 130, 240, 0.2),
]
```

```python
input_driver = SimpleNamespace(
    click=lambda x, y, **kwargs: calls.append(("click", x, y)),
    drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(
        ("drag", from_x, from_y, to_x, to_y, duration)
    ),
    press=lambda key: calls.append(("press", key)),
)

assert calls == [
    ("prepare_input",),
    ("click", 600, 325),
    ("prepare_input",),
    ("drag", 200, 300, 900, 500, 0.2),
]
```

- [ ] **Step 3: 只跑这批新旧测试，确认它们先红**

Run: `uv run pytest --basetemp=.pytest-tmp tests/test_runtime_backends.py -k "prepares_window_before_input_actions or click_and_drag_translate_window_relative_pixels or keeps_ratio_support_for_scene_commands or drag_defaults_duration_to_point_two_seconds or drag_keeps_explicit_duration" -q`

Expected: FAIL，至少会因为 `RuntimeOperator.drag_to()` 仍透传 `duration=None` 而让 3 条旧断言与 2 条新断言不成立。

### Task 2: 用最小实现把默认值归一到 runtime 层

**Files:**
- Modify: `.worktrees/input-drag-default-duration/trail/runtime/operator.py`
- Test: `.worktrees/input-drag-default-duration/tests/test_runtime_backends.py`

- [ ] **Step 1: 在 `RuntimeOperator` 内引入默认拖动时长常量，并只在 `drag_to()` 做归一化**

```python
class RuntimeOperator:
    POST_INPUT_CAPTURE_DELAY_SECONDS = 0.2
    DEFAULT_DRAG_DURATION_SECONDS = 0.2

    def drag_to(self, from_x: float, from_y: float, to_x: float, to_y: float, *, duration: float | None = None):
        self._prepare_input_target()
        resolved_duration = self.DEFAULT_DRAG_DURATION_SECONDS if duration is None else duration
        screen_from_x, screen_from_y = self._to_screen_point(from_x, from_y)
        screen_to_x, screen_to_y = self._to_screen_point(to_x, to_y)
        self.input.drag(screen_from_x, screen_from_y, screen_to_x, screen_to_y, duration=resolved_duration)
        self._mark_input_action()
        self._record_trace(
            "drag_to",
            from_point=[from_x, from_y],
            to_point=[to_x, to_y],
            duration=resolved_duration,
            screen_from=[screen_from_x, screen_from_y],
            screen_to=[screen_to_x, screen_to_y],
        )
        self._check_foreground_after_input()
```

- [ ] **Step 2: 跑刚才那组红测，确认转绿**

Run: `uv run pytest --basetemp=.pytest-tmp tests/test_runtime_backends.py -k "prepares_window_before_input_actions or click_and_drag_translate_window_relative_pixels or keeps_ratio_support_for_scene_commands or drag_defaults_duration_to_point_two_seconds or drag_keeps_explicit_duration" -q`

Expected: PASS，5 条测试全部通过。

- [ ] **Step 3: 再跑 driver 侧显式 duration 回归，确认没有把已有分步拖动逻辑改坏**

Run: `uv run pytest --basetemp=.pytest-tmp tests/test_runtime_backends.py -k "pyautogui_input_driver_drag_respects_explicit_duration_on_windows" -q`

Expected: PASS，Windows 显式 `duration=0.2` 的 driver 测试继续通过。

### Task 3: 做收口验证，证明变更只影响 runtime 默认值路径

**Files:**
- Verify: `.worktrees/input-drag-default-duration/tests/test_runtime_backends.py`
- Verify: `.worktrees/input-drag-default-duration/trail/runtime/operator.py`

- [ ] **Step 1: 运行整个 `test_runtime_backends.py`，确保没有遗漏别的 drag stub 或 trace 回归**

Run: `uv run pytest --basetemp=.pytest-tmp tests/test_runtime_backends.py -q`

Expected: PASS，整份 runtime/backend 测试文件通过。

- [ ] **Step 2: 查看 diff，确认只改了 runtime 默认值路径和对应测试，没有碰 CLI、daemon、README 或 driver fallback 默认逻辑**

Run: `rtk git diff -- . "trail/runtime/operator.py" "tests/test_runtime_backends.py"`

Expected: 只看到 `RuntimeOperator.drag_to()` 的默认值归一与 `tests/test_runtime_backends.py` 的断言/测试桩更新；不应出现 `trail/commands/input.py`、`trail/daemon/command_service.py`、`README.md` 的修改。

- [ ] **Step 3: 若实现与 spec 出现细节偏差，再做最小 spec 同步；否则保持 spec 不动**

```md
无偏差时不修改：
- docs/superpowers/specs/2026-04-23-input-drag-default-duration-design.md

只有在实际实现把常量放成 `RuntimeOperator.DEFAULT_DRAG_DURATION_SECONDS`、
而 spec 仍写成 module-level constant 这类“表达不一致但语义一致”的情况下，
才做最小文案同步，不新增范围。
```

- [ ] **Step 4: 完成后按子代理方式执行，不额外询问执行模式**

Run: `继续使用 superpowers:subagent-driven-development，按 Task 1 -> Task 2 -> Task 3 顺序执行。`

Expected: 每个任务完成后先做本地复核，再进入下一个任务；未经用户明确要求，不创建 commit。
