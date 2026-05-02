# CW Batch Locate And Shared OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development in the existing project-local worktree `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-battle-run-settle-resume` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为货币战争 `cw.stage.detect` / `cw.stage.wait` / `cw.battle.run` 引入单截图多模板匹配与单轮共享 OCR，压缩 settle 页的 wall time，让短 budget 下更早推进 `继续挑战`，同时不改 battle.run 的用户可见协议与 timeout 契约。

**Architecture:** 在 runtime 层新增通用 `batch_locate` helper，冻结“传入共享截图时不得再次截图、helper 内不得回退到 `runtime.locate()`”的语义；在 `trail/scenes/cw/stage.py` 改成单截图 + batch locate + 必要时一次 settle OCR fallback；在 `trail/scenes/cw/battle.py` 新增单轮 observation，把 stage locate 结果、整页 OCR 和 lazy settle detail 收口成同轮共享输入，并让 settle continue/next 在 observation 足够时不再额外整页 locate/OCR。

**Tech Stack:** Python 3.12、Pillow 图像对象、现有 runtime matcher / OCR engine、pytest、现有 CW scene tests、现有 `batch_ocr` 调试/trace 模式。

---

> 未收到用户明确要求前不主动创建 git commit。本计划里的 checkpoint 以“目标测试通过 + diff 自检”代替 commit 步骤。
>
> 本轮是性能/墙钟优化，不改 battle.run 的文本协议、`90s` 默认业务 timeout、response timeout `+30s` buffer，也不改 README / AGENTS / active skill 文档；这些文件只作为现有防回归测试对象。

## 文件结构

### 新建文件

- `trail/runtime/batch_locate.py`
  - 通用单图多模板匹配 helper。
  - 冻结“有 `image` 时不截图、无 `image` 时最多截图一次、禁止内部调用 `runtime.locate()`”。
- `tests/test_runtime_batch_locate.py`
  - `batch_locate` 的 helper 单测。
  - 锁同图多模板结果、trace 形状、截图次数语义。

### 修改文件

- `trail/scenes/cw/stage.py`
  - `build_cw_stage_detector()` 改成“单截图 + batch locate + settle OCR fallback”。
  - settle OCR fallback 改成消费共享 screenshot，而不是重新 `runtime.ocr()` 截图。
- `trail/scenes/cw/battle.py`
  - 引入单轮 `BattleObservation` / shared OCR / lazy settle detail。
  - classifier、settle parser、resume、settle continue/next 动作共享同轮 observation。
- `tests/test_cw_stage.py`
  - 锁 stage detector 的阶段结果不变。
  - 锁模板命中与 OCR fallback 路径的 screenshot / OCR 次数下降。
- `tests/test_cw_battle_run.py`
  - 锁 battle.run 单轮 OCR 复用。
  - 锁 detector miss + OCR settle 命中时能在短 budget 前推进 `continue/next`。
  - 锁 `challenge_end` / `challenge_success` 结算变体、resume hint、`last_battle_round` 等既有行为不回归。

### 只运行、不修改的回归文件

- `tests/test_daemon_protocol.py`
  - 只运行现有 `cw.stage.detect` / `cw.battle.run` / `state.dump` / timeout 契约回归。
  - 这次不新增 daemon 契约测试，也不改 timeout 协议。
- `tests/test_cw_rpc_contracts.py`
  - 只运行现有 `cw.stage.detect` / `cw.battle.run` CLI/RPC 契约防回归。
- `tests/test_output_rendering.py`
  - 只运行现有 renderer / README / AGENTS 契约防回归。
  - 除非实现真的改变用户可见协议，否则不新增文案断言。

## Task 1: 新增通用 `batch_locate` helper

**Files:**
- Create: `trail/runtime/batch_locate.py`
- Create: `tests/test_runtime_batch_locate.py`

- [ ] **Step 1: 先写 helper 的红灯测试**

在 `tests/test_runtime_batch_locate.py` 新建最小 runtime/matcher stub，先锁住两条不可退让的语义：

```python
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from trail.runtime.batch_locate import BatchLocateTarget, run_batch_locate


@dataclass
class MatcherStub:
    hits: dict[str, dict | None]
    calls: list[tuple[str, tuple[int, int]]] | None = None

    def __post_init__(self):
        if self.calls is None:
            self.calls = []

    def locate(self, template: str, image):
        self.calls.append((template, image.size))
        return self.hits.get(template)


class RuntimeStub:
    def __init__(self, *, matcher: MatcherStub):
        self.matcher = matcher
        self.screenshot_calls = 0
        self.debug = []

    def screenshot(self, **kwargs):
        del kwargs
        self.screenshot_calls += 1
        return Image.new("RGB", (1280, 720), "white")

    def _begin_debug_action(self, step: str, **payload):
        self.debug.append(("begin", step, payload))
        return {"step": step}

    def _finish_debug_action(self, action, *, ok: bool, **payload):
        self.debug.append(("finish", action["step"], ok, payload))


def test_run_batch_locate_uses_provided_image_without_screenshot():
    runtime = RuntimeStub(matcher=MatcherStub({"prep.png": {"left": 1, "top": 2, "width": 3, "height": 4}}))
    shared_image = Image.new("RGB", (1920, 1080), "black")

    result = run_batch_locate(
        runtime,
        [BatchLocateTarget("prep", "prep.png"), BatchLocateTarget("shop", "shop.png")],
        image=shared_image,
        trace_prefix="cw_stage_batch_locate",
    )

    assert runtime.screenshot_calls == 0
    assert result.by_key["prep"].found is True
    assert result.by_key["shop"].found is False


def test_run_batch_locate_captures_once_when_image_not_provided():
    runtime = RuntimeStub(matcher=MatcherStub({}))

    run_batch_locate(runtime, [BatchLocateTarget("settle", "settle.png")], trace_prefix="cw_stage_batch_locate")

    assert runtime.screenshot_calls == 1


def test_run_batch_locate_emits_stable_debug_shape():
    runtime = RuntimeStub(matcher=MatcherStub({"prep.png": {"left": 1, "top": 2, "width": 3, "height": 4}}))

    run_batch_locate(
        runtime,
        [BatchLocateTarget("prep", "prep.png"), BatchLocateTarget("shop", "shop.png")],
        image=Image.new("RGB", (1920, 1080), "black"),
        trace_prefix="cw_stage_batch_locate",
    )

    assert runtime.debug[0] == (
        "begin",
        "cw_stage_batch_locate",
        {"target_count": 2, "strategy": "sequential", "screenshot": "provided"},
    )
    assert runtime.debug[1] == (
        "finish",
        "cw_stage_batch_locate",
        True,
        {
            "found_count": 1,
            "miss_count": 1,
            "targets": [
                {"key": "prep", "template": "prep.png", "found": True},
                {"key": "shop", "template": "shop.png", "found": False},
            ],
        },
    )
```

- [ ] **Step 2: 运行红灯测试，确认 helper 还不存在**

Run: `uv run pytest --basetemp .pytest-tmp-task1-red tests/test_runtime_batch_locate.py -v`

Expected: FAIL，报 `ModuleNotFoundError: No module named 'trail.runtime.batch_locate'` 或 `cannot import name 'BatchLocateTarget'`。

- [ ] **Step 3: 用最小实现补齐 `batch_locate` helper**

在 `trail/runtime/batch_locate.py` 先实现最小可执行版本，接口语义要先锁死：

- 第一版不暴露 `capture` 参数，统一要求调用方自行决定是否先截图并传 `image`。
- 这样可以先锁死“共享同一张图”的语义，避免在 helper 内引入 capture offset / 坐标空间歧义。

```python
from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BatchLocateTarget:
    key: Hashable
    template: str
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BatchLocateKeyResult:
    key: Hashable
    template: str
    found: bool
    box: Any = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BatchLocateResult:
    image: Any
    by_key: dict[Hashable, BatchLocateKeyResult]


def run_batch_locate(
    runtime: Any,
    targets: Sequence[BatchLocateTarget],
    *,
    image: Any = None,
    trace_prefix: str = "batch_locate",
    strategy: str = "sequential",
) -> BatchLocateResult:
    if strategy != "sequential":
        raise ValueError(f"unsupported batch locate strategy: {strategy}")
    if not targets:
        raise ValueError("batch locate requires at least one target")

    action = getattr(runtime, "_begin_debug_action", lambda *args, **kwargs: None)(
        trace_prefix,
        target_count=len(targets),
        strategy=strategy,
        screenshot="provided" if image is not None else "captured",
    )
    try:
        shared_image = image if image is not None else runtime.screenshot()
        by_key: dict[Hashable, BatchLocateKeyResult] = {}
        found_count = 0
        for target in targets:
            box = runtime.matcher.locate(target.template, shared_image)
            found = box is not None
            found_count += 1 if found else 0
            by_key[target.key] = BatchLocateKeyResult(
                key=target.key,
                template=target.template,
                found=found,
                box=box,
                metadata=target.metadata,
            )
        getattr(runtime, "_finish_debug_action", lambda *args, **kwargs: None)(
            action,
            ok=True,
            found_count=found_count,
            miss_count=len(targets) - found_count,
            targets=[
                {"key": target.key, "template": target.template, "found": by_key[target.key].found}
                for target in targets
            ],
        )
        return BatchLocateResult(image=shared_image, by_key=by_key)
    except Exception as error:
        getattr(runtime, "_finish_debug_action", lambda *args, **kwargs: None)(
            action,
            ok=False,
            error_type=type(error).__name__,
            msg=str(error),
        )
        raise
```

- [ ] **Step 4: 跑 helper 绿灯测试**

Run: `uv run pytest --basetemp .pytest-tmp-task1-green tests/test_runtime_batch_locate.py -v`

Expected: PASS，且 `runtime.screenshot_calls` 语义按测试锁住。

## Task 2: 让 `cw.stage.detect` / `cw.stage.wait` 改走单截图多模板匹配

**Files:**
- Modify: `trail/scenes/cw/stage.py`
- Modify: `tests/test_cw_stage.py`

- [ ] **Step 1: 先写 stage detector 的红灯测试**

在 `tests/test_cw_stage.py` 追加两条 focused 测试，锁住阶段结果不变，同时锁住 screenshot / OCR 次数下降：

```python
from PIL import Image


def test_build_cw_stage_detector_uses_single_screenshot_for_template_hits(monkeypatch):
    class RuntimeSpy:
        def __init__(self):
            self.screenshot_calls = 0
            self.ocr_calls = 0
            self.matcher = SimpleNamespace(
                locate=lambda template, image: {"left": 1, "top": 2, "width": 3, "height": 4}
                if str(template).endswith("fortune_teller.png")
                else None
            )

        def screenshot(self, **kwargs):
            del kwargs
            self.screenshot_calls += 1
            return Image.new("RGB", (1280, 720), "white")

        def ocr_image(self, image, **kwargs):
            del image, kwargs
            self.ocr_calls += 1
            return []

    runtime = RuntimeSpy()
    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "fortune"
    assert runtime.screenshot_calls == 1
    assert runtime.ocr_calls == 0


def test_build_cw_stage_detector_uses_single_screenshot_before_ocr_fallback(monkeypatch):
    class RuntimeSpy:
        def __init__(self):
            self.screenshot_calls = 0
            self.ocr_calls = 0
            self.matcher = SimpleNamespace(locate=lambda template, image: None)

        def screenshot(self, **kwargs):
            del kwargs
            self.screenshot_calls += 1
            return Image.new("RGB", (1280, 720), "white")

        def ocr_image(self, image, **kwargs):
            del image, kwargs
            self.ocr_calls += 1
            return [_rapidocr_piece("挑战成功"), _rapidocr_piece("继续挑战")]

    runtime = RuntimeSpy()
    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "settle"
    assert runtime.screenshot_calls == 1
    assert runtime.ocr_calls == 1
```

同时把现有 `test_build_cw_stage_detector_maps_*` 与 settle OCR fallback 相关 `RuntimeSpy` 一并迁到新接口：

1. 旧的 `runtime.locate()` stub 改成 `runtime.matcher = SimpleNamespace(locate=...)`。
2. 所有 detector 测试都补 `screenshot()`。
3. settle OCR fallback 相关测试改成提供 `ocr_image()`，不再依赖 `ocr()`。

- [ ] **Step 2: 跑红灯测试，确认当前还是多次 `runtime.locate()` / 重复截图**

Run: `uv run pytest --basetemp .pytest-tmp-task2-red tests/test_cw_stage.py -k "single_screenshot_for_template_hits or single_screenshot_before_ocr_fallback" -v`

Expected: FAIL，至少 `screenshot_calls` 会大于 `1`。

- [ ] **Step 3: 改造 `build_cw_stage_detector()`**

在 `trail/scenes/cw/stage.py` 用 `run_batch_locate()` 收口模板探测，并把 settle OCR fallback 改成直接消费共享 screenshot：

```python
from trail.runtime.batch_locate import BatchLocateTarget, run_batch_locate
from trail.runtime.ocr_config import OcrRequestConfig


def _detect_cw_stage_from_ocr_image(runtime, image) -> str | None:
    ocr_image = getattr(runtime, "ocr_image", None)
    if not callable(ocr_image):
        return None
    pieces = ocr_image(image, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="auto")) or []
    text = "".join(_read_ocr_piece(piece).strip() for piece in pieces)
    if any(keyword in text for keyword in SETTLE_OCR_KEYWORDS):
        return "settle"
    return None


def build_cw_stage_detector(runtime):
    grouped_templates: dict[str, list[str]] = {}
    for alias, value in STAGE_RESOURCE_ALIASES:
        template = str(resolve_scene_asset("cw", alias))
        grouped_templates.setdefault(template, []).append(value)

    ordered_targets = [
        BatchLocateTarget(key=tuple(values), template=template)
        for template, values in grouped_templates.items()
    ]

    def detector() -> str | None:
        shared_image = runtime.screenshot()
        locate_result = run_batch_locate(runtime, ordered_targets, image=shared_image, trace_prefix="cw_stage_batch_locate")
        for target in ordered_targets:
            result = locate_result.by_key[target.key]
            if not result.found:
                continue
            values = tuple(target.key)
            if len(values) == 1:
                return values[0]
            raise TrailError("STAGE_AMBIGUOUS", f"当前资源无法区分阶段: {', '.join(values)}")
        return _detect_cw_stage_from_ocr_image(runtime, shared_image)

    return detector
```

- [ ] **Step 4: 跑 stage 绿灯 + 相邻既有回归**

Run: `uv run pytest --basetemp .pytest-tmp-task2-green tests/test_cw_stage.py -k "build_cw_stage_detector or cw_stage_detect_service" -v`

Expected: PASS，且既有 stage 结果语义不回归。

## Task 3: 为 `battle.run` 引入单轮 observation 与共享 OCR

**Files:**
- Modify: `trail/scenes/cw/battle.py`
- Modify: `tests/test_cw_battle_run.py`

- [ ] **Step 1: 先写 battle observation 的红灯测试**

先在 `tests/test_cw_battle_run.py` 扩测试支架，保证短 budget 与 observation 按钮路径都能直接执行，而不是先被 fixture 自己绊住。这里直接建立在当前 worktree 里已经存在的 `ScriptedBattleRuntime(..., settle_page_texts=..., settle_detect_stage=...)` 基础之上；如果本地分支还没同步到这版 fixture，先把它更新到当前 worktree 的形态再继续：

```python
def _ocr_button_piece(text: str, *, left: int, top: int, width: int = 120, height: int = 36) -> dict[str, object]:
    return {"text": text, "box": {"left": left, "top": top, "width": width, "height": height}}


class CostlySettleRuntime(ScriptedBattleRuntime):
    def __init__(self, states: list[str], *, clock: FakeClock, page_ocr_cost: float, capture_ocr_cost: float, **kwargs):
        super().__init__(states, **kwargs)
        self.clock = clock
        self.page_ocr_cost = page_ocr_cost
        self.capture_ocr_cost = capture_ocr_cost
        self.page_ocr_calls = 0
        self.capture_ocr_calls: list[object] = []

    def ocr(self, **kwargs):
        capture = _capture_key(kwargs.get("capture"))
        if capture is None:
            self.page_ocr_calls += 1
            self.clock.current += self.page_ocr_cost
        else:
            self.capture_ocr_calls.append(capture)
            self.clock.current += self.capture_ocr_cost
        return super().ocr(**kwargs)

    def _ocr_map_for_state(self) -> dict[object, list[object]]:
        mapping = super()._ocr_map_for_state()
        if self.state == "settle_entry":
            page_texts = self.settle_page_texts or [self.settle_text]
            page_pieces: list[object] = []
            for text in page_texts:
                if text == "继续挑战":
                    page_pieces.append(_ocr_button_piece("继续挑战", left=900, top=884))
                elif text == "下一步":
                    page_pieces.append(_ocr_button_piece("下一步", left=900, top=884))
                else:
                    page_pieces.append(_ocr_piece(text))
            mapping[None] = page_pieces
        if self.state == "settle_followup":
            mapping[None] = [_ocr_button_piece("下一步", left=900, top=884)]
        return mapping

    def click_point(self, x: int, y: int):
        super().click_point(x, y)
        if self.state == "settle_entry":
            self.actions.append("continue")
            self.advance()
        elif self.state == "settle_followup":
            self.actions.append("next")
            self.advance()


def _patch_stage_and_clock_only(monkeypatch, battle_scene, runtime, *, clock: FakeClock):
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: runtime.detect_stage)
    monkeypatch.setattr(battle_scene, "monotonic", clock.monotonic, raising=False)

    def fake_sleep(seconds: float) -> None:
        clock.sleep(seconds)
        runtime.tick()

    monkeypatch.setattr(battle_scene, "sleep", fake_sleep, raising=False)
```

然后再追加下面这 4 条 focused 测试：

```python
def test_classify_cw_battle_page_uses_single_page_ocr_snapshot(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()

    class RuntimeSpy(FakeRuntime):
        def __init__(self):
            super().__init__(ocr_map={None: [_ocr_piece("挑战成功"), _ocr_piece("继续挑战")]})
            self.page_ocr_calls = 0

        def ocr(self, **kwargs):
            capture = _capture_key(kwargs.get("capture"))
            if capture is None:
                self.page_ocr_calls += 1
            return super().ocr(**kwargs)

    runtime = RuntimeSpy()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)

    assert battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path), detected_stage=None) == "settle_entry"
    assert runtime.page_ocr_calls == 1


def test_continue_after_settlement_uses_observed_button_without_fallback(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = CostlySettleRuntime(
        ["settle_entry"],
        clock=FakeClock(step=0.0),
        page_ocr_cost=0.0,
        capture_ocr_cost=0.0,
        settle_text="挑战成功",
        settle_page_texts=["挑战成功", "继续挑战"],
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    observation = battle_scene.observe_cw_battle_page(runtime, detected_stage=None)

    monkeypatch.setattr(
        battle_scene,
        "build_cw_battle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback battle continuer used"),
    )

    battle_scene._continue_after_settlement(runtime, observation=observation)

    assert runtime.clicks[-1] == (960, 902)
    assert runtime.page_ocr_calls == 1


def test_advance_settlement_page_uses_observed_button_without_fallback(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = CostlySettleRuntime(
        ["settle_followup"],
        clock=FakeClock(step=0.0),
        page_ocr_cost=0.0,
        capture_ocr_cost=0.0,
        sleep_advances_from=(),
    )
    observation = battle_scene.observe_cw_battle_page(runtime, detected_stage=None)

    monkeypatch.setattr(
        battle_scene,
        "build_cw_settle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback settle continuer used"),
    )

    battle_scene._advance_settlement_page(runtime, observation=observation)

    assert runtime.clicks[-1] == (960, 902)


def test_continue_after_settlement_accepts_tuple_ocr_piece_without_fallback(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = CostlySettleRuntime(
        ["settle_entry"],
        clock=FakeClock(step=0.0),
        page_ocr_cost=0.0,
        capture_ocr_cost=0.0,
        settle_text="挑战成功",
        settle_page_texts=["挑战成功"],
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    monkeypatch.setattr(
        runtime,
        "_ocr_map_for_state",
        lambda: {
            None: [([[900, 884], [1020, 884], [1020, 920], [900, 920]], "继续挑战", 0.99)],
            HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战成功")],
        },
    )
    observation = battle_scene.observe_cw_battle_page(runtime, detected_stage=None)

    monkeypatch.setattr(
        battle_scene,
        "build_cw_battle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback battle continuer used"),
    )

    battle_scene._continue_after_settlement(runtime, observation=observation)

    assert runtime.clicks[-1] == (960, 902)


def test_run_cw_battle_resumes_settle_entry_before_short_deadline(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    clock = FakeClock(step=0.0)
    runtime = CostlySettleRuntime(
        ["settle_entry", "settle_followup", "stable_stage"],
        clock=clock,
        page_ocr_cost=3.0,
        capture_ocr_cost=1.0,
        settle_text="挑战成功",
        settle_page_texts=["挑战成功", "继续挑战"],
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    _patch_stage_and_clock_only(monkeypatch, battle_scene, runtime, clock=clock)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=15)

    assert result["status"] == "completed"
    assert result["stage"] == "shop"
    assert runtime.actions == ["continue", "next"]
    assert clock.current < 15


def test_run_cw_battle_resume_hint_then_settle_still_advances_continue_and_next(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    clock = FakeClock(step=0.0)
    runtime = CostlySettleRuntime(
        ["settle_entry", "settle_followup", "stable_stage"],
        clock=clock,
        page_ocr_cost=3.0,
        capture_ocr_cost=1.0,
        settle_text="挑战成功",
        settle_page_texts=["挑战成功", "继续挑战"],
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    _patch_stage_and_clock_only(monkeypatch, battle_scene, runtime, clock=clock)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=15)

    assert result["status"] == "completed"
    assert result["stage"] == "shop"
    assert runtime.actions == ["continue", "next"]
    assert ensure_cw_state(session)["battle_resume"] == {}
```

- [ ] **Step 2: 跑红灯测试，确认当前 battle.run 仍会重复整页 OCR / 在短 budget settle 页上来不及推进**

Run: `uv run pytest --basetemp .pytest-tmp-task3-red tests/test_cw_battle_run.py -k "single_page_ocr_snapshot or short_deadline or observation" -v`

Expected: FAIL，至少会在整页 OCR 次数、fallback helper 被错误调用，`runtime.actions == ["continue", "next"]`，或 `clock.current < 15` 上失败。

- [ ] **Step 3: 在 `battle.py` 实现 observation + lazy settle detail**

在 `trail/scenes/cw/battle.py` 增加最小 observation 层，保持外部协议不变。这里明确选择：**所有 observation 按钮解析 helper 都本地放在 `battle.py`，不要从 `events.py` 导入私有 helper，也不要为这次优化新增 `events.py` 改动。**

```python
from dataclasses import dataclass


@dataclass
class BattleObservation:
    runtime: object
    detected_stage: object
    page_ocr_pieces: list[object]
    page_text: str
    _headline_text: str | None = None
    _round_text: str | None = None
    _stats_text: str | None = None

    def headline_text(self) -> str:
        if self._headline_text is None:
            self._headline_text = _joined_ocr_text(self.runtime, capture=SETTLEMENT_HEADLINE_CAPTURE)
        return self._headline_text

    def round_text(self) -> str:
        if self._round_text is None:
            self._round_text = _joined_ocr_text(self.runtime, capture=SETTLEMENT_ROUND_CAPTURE)
        return self._round_text

    def stats_text(self) -> str:
        if self._stats_text is None:
            self._stats_text = _joined_ocr_text(self.runtime, capture=SETTLEMENT_STATS_CAPTURE)
        return self._stats_text


def observe_cw_battle_page(runtime, *, detected_stage: object) -> BattleObservation:
    page_ocr_pieces = runtime.ocr() or []
    page_text = "".join(_read_ocr_piece(piece).strip() for piece in page_ocr_pieces)
    return BattleObservation(
        runtime=runtime,
        detected_stage=detected_stage,
        page_ocr_pieces=list(page_ocr_pieces),
        page_text=page_text,
    )
```

然后把下面这些 call-site 全部改成优先消费 `BattleObservation`，不再各自重复整页 OCR：

- `_has_battle_start(observation)`
- `_has_settlement_entry(observation)`
- `_has_settlement_followup(observation)`
- `_has_game_over(observation)`
- `_has_positive_battle_anchor(observation)`
- `_read_settle_headline(runtime, observation=observation)`
- `_read_optional_settlement_metrics(runtime, observation=observation)`
- `_is_continue_only_settlement_entry(runtime, observation=observation)`

`classify_cw_battle_page()` 保留对外签名，但内部新增 `observation: BattleObservation | None = None`；`run_cw_battle()` 每轮先得到一次 `detected_stage` 和一次 `observation`，然后把同一份 observation 传给 classifier、settle parser、continue-only fallback 和 action wrapper。

- [ ] **Step 4: 用 observation 结果更早推进 settle action**

在 `battle.py` 内新增基于同轮 OCR 结果的按钮候选解析，优先直接点击 observation 里的 `继续挑战/继续` 或 `下一步/下一页`，只有 observation 没给出按钮候选时才 fallback 到现有 helper。这里不复用 `events.py` 的私有 `_normalized_action_text` / `_normalize_ocr_piece` / `_box_center`，而是在 `battle.py` 本地实现最小等价 helper：

```python
def _normalized_action_text(text: object) -> str:
    return "".join(str(text or "").split())


def _normalize_observation_piece(piece: object) -> dict[str, float | str] | None:
    if isinstance(piece, dict):
        text = str(piece.get("text") or "").strip()
        box = piece.get("box")
        if not text or not isinstance(box, dict):
            return None
        return {
            "text": text,
            "left": float(box["left"]),
            "top": float(box["top"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
        }
    if isinstance(piece, (list, tuple)) and len(piece) >= 2:
        polygon = piece[0]
        text = str(piece[1] or "").strip()
        if not text or not isinstance(polygon, (list, tuple)):
            return None
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
        left = min(xs)
        top = min(ys)
        return {
            "text": text,
            "left": left,
            "top": top,
            "width": max(xs) - left,
            "height": max(ys) - top,
        }
    return None


def _box_center(box: dict[str, float | str]) -> tuple[int, int]:
    return (
        int(float(box["left"]) + float(box["width"]) / 2.0),
        int(float(box["top"]) + float(box["height"]) / 2.0),
    )


def _find_action_button_box_from_observation(observation: BattleObservation, *, allowed_texts: set[str]):
    normalized_allowed = {_normalized_action_text(text) for text in allowed_texts}
    for piece in observation.page_ocr_pieces:
        normalized_piece = _normalize_observation_piece(piece)
        if normalized_piece is None:
            continue
        if _normalized_action_text(normalized_piece["text"]) in normalized_allowed:
            return normalized_piece
    return None


def _continue_after_settlement(runtime, observation: BattleObservation | None = None) -> None:
    if observation is not None:
        button = _find_action_button_box_from_observation(observation, allowed_texts={"继续挑战", "继续"})
        if button is not None:
            runtime.click_point(*_box_center(button))
            return
    build_cw_battle_continuer(runtime)()


def _advance_settlement_page(runtime, observation: BattleObservation | None = None) -> None:
    if observation is not None:
        button = _find_action_button_box_from_observation(observation, allowed_texts={"下一步", "下一页"})
        if button is not None:
            runtime.click_point(*_box_center(button))
            return
    build_cw_settle_continuer(runtime)()
```

然后在 `run_cw_battle()` 的 `settle_entry` / `settle_followup` 分支把当前 `observation` 传进去。这里要明确：

1. observation 命中动作按钮时，`_continue_after_settlement()` / `_advance_settlement_page()` 不得再调用额外整页 OCR。
2. observation 命中动作按钮时，fallback helper 不得被调用。
3. 只有 observation 没给出按钮候选时，才允许走现有 `build_cw_battle_continuer()` / `build_cw_settle_continuer()`。

- [ ] **Step 5: 跑 battle 绿灯 + 既有 focused 防回归**

Run: `uv run pytest --basetemp .pytest-tmp-task3-green tests/test_cw_battle_run.py -v`

Expected: PASS，并且既有 `resume_hint` / `clear_in_progress` / `last_battle_round` / `challenge_end` 系列测试不回归。

## Task 4: 做现有 focused 回归与 live 对照验证

**Files:**
- Modify: 无
- Run/read only: `tests/test_daemon_protocol.py`
- Run/read only: `tests/test_cw_rpc_contracts.py`
- Run/read only: `tests/test_output_rendering.py`
- Run/read only: `README.md`
- Run/read only: `AGENTS.md`
- Run/read only: `docs/cw-stage-reference/README.md`
- Run/read only: `skills/trail-cw-entry/SKILL.md`
- Run/read only: `skills/trail-hsr/references/simple-command-surface.md`

- [ ] **Step 1: 跑现有协议/daemon focused 回归**

Run: `uv run pytest --basetemp .pytest-tmp-task4-regression tests/test_cw_stage.py tests/test_cw_battle_run.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py -k "cw_stage_detect or cw_battle_run or state_dump or clear_in_progress or battle_run_as_default_entry or unified_command_policy" -v`

Expected: PASS，确保这次性能优化没有顺手改 battle.run 的协议、resume/clear、state.dump、README/AGENTS 既有契约。

- [ ] **Step 2: 跑完整 battle / stage focused 套件，确认调用次数与结果路径一起成立**

Run: `uv run pytest --basetemp .pytest-tmp-task4-runtime tests/test_runtime_batch_locate.py tests/test_cw_stage.py tests/test_cw_battle_run.py -v`

Expected: PASS，且新的 runtime helper、stage 调用次数、battle observation 共享逻辑都被同一轮回归锁住。

- [ ] **Step 3: 在 live 上复跑 `--timeout 15` 对照**

顺序执行：

```bash
uv run trail daemon restart
uv run trail window attach --window-title "崩坏：星穹铁道"
uv run trail session create --window-title "崩坏：星穹铁道"
uv run trail --verbose cw battle run --session <id> --timeout 15
uv run trail --verbose cw battle run --session <id> --timeout 15
uv run trail --verbose cw battle run --session <id> --timeout 15
```

Expected:

1. 首次仍可能是 `in_battle=1`，但 wall time 更接近 `15s`，trace 明显减少重复 screenshot / OCR。
2. 后续进入 `stage=settle` 后，应更早出现 `continue/next` 的点击，而不是多次纯 `in_progress stage=settle` 且 trace 无点击。
3. 若 CLI 仍偶发 `TimeoutError`，应优先对照 trace / `daemon.request-status` 判断是否 wall time 仍超 budget，而不是立即改 timeout 契约。

- [ ] **Step 4: 自检 diff 边界**

Run: `rtk git status --short`

Expected: 只看到 `trail/runtime/batch_locate.py`、`trail/scenes/cw/stage.py`、`trail/scenes/cw/battle.py`、对应测试文件，以及本轮新 plan/spec / `.pytest-tmp-*` 临时目录；没有 `README.md`、`AGENTS.md`、`skills/*`、`trail/output/*` 的无关改动。
