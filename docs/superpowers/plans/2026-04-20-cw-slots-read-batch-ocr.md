# CW Slots Read Batch OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `trail cw slots read` 从“逐槽位即时 OCR”改成“逐槽位截图、批量拼图、单次 OCR”，同时补齐局部读取的 `stale` 语义、名字归一化，以及 `--slot` 的 help / README / skill 约束。

**Architecture:** 先在 runtime 层补两块基础能力：保留原始 region 像素尺寸的截图入口，以及对内存图片执行 OCR 的入口。然后在 `trail/scenes/cw/slots.py` 里把读取流程改成两阶段批量管线，并把局部读取的 merge 矩阵、`SLOTS_READ_EMPTY` 判定和名字归一化都锁进测试。最后再同步 CLI help、README 和 `skills/trail-cw*`，把“Agent 首选 `--slot` 定向读，但默认全量契约保留”写死。

**Tech Stack:** Python 3.12, Typer, Pillow, pytest, RapidOCR, 现有 `RuntimeOperator` / `WindowsWindowController` / `cw` scene 结构

---

## 文件结构

### 修改文件

- `trail/runtime/window.py`
  - 新增保留原始 region 尺寸的截图入口；现有 `capture()` 继续保持 canonical 输出语义不变。
- `trail/runtime/operator.py`
  - 新增对内存图片做 OCR 的入口，并复用现有 warning/debug/ocr config 语义。
- `trail/scenes/cw/slots.py`
  - 把逐槽位即时 OCR 改成批量截图 + 单次 OCR；补齐 strip 映射、局部 merge 矩阵、名字归一化与 stale 规则。
- `trail/commands/cw.py`
  - 给 `cw slots read` 命令和 `--slot` 选项补 help 文案。
- `README.md`
  - 把 `slots read` 的推荐工作流改成“先看 screenshot，再 `--slot` 定向读取”。
- `skills/trail-cw-slots/SKILL.md`
  - 改写槽位识别和 `--slot` 的首选用法。
- `skills/trail-cw/SKILL.md`
  - 收窄上层对通用 `trail ocr read` 的引导，避免和新的 `slots read --slot` 双默认冲突。
- `tests/test_runtime_backends.py`
  - 增加 raw region capture 与 `ocr_image()` 契约测试。
- `tests/test_cw_slots.py`
  - 增加批量 strip、box 回映射、局部 stale 矩阵、名字归一化与边界降级测试。
- `tests/test_atomic_commands.py`
  - 增加 `trail cw slots read --help` 与 `--slot` 选项帮助断言。

### 不新增独立文件

- 本次先不额外拆出新的 `cw slots` helper 文件，优先把变更控制在现有 `trail/scenes/cw/slots.py` 中。
- 本次也不新增本地角色词典文件；名字归一化先只吃 session 内上下文和调用点可传入的本地候选列表，默认允许为空。

---

### Task 1: Runtime 原始截图与内存 OCR 基础能力

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\trail\runtime\window.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\trail\runtime\operator.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\tests\test_runtime_backends.py`

- [ ] **Step 1: 先写 raw region capture 与内存 OCR 的失败测试**

```python
def test_window_capture_image_preserves_region_size_when_normalize_false(tmp_path: Path, monkeypatch):
    controller = WindowsWindowController(workspace=tmp_path, window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    class FakeWindow:
        _hWnd = 1
        title = "崩坏：星穹铁道"

    monkeypatch.setattr(controller, "_resolve_window", lambda: FakeWindow())
    monkeypatch.setattr(controller, "_resolve_region", lambda window=None: Region(left=0, top=0, width=1920, height=1080))
    monkeypatch.setattr(
        "trail.runtime.window._capture_with_windows_capture",
        lambda hwnd, client_region: Image.new("RGB", (client_region.width, client_region.height), color="white"),
    )

    image = controller.capture_image(from_x=100, from_y=200, to_x=301, to_y=261, normalize=False)

    assert image.size == (201, 61)


def test_runtime_operator_ocr_image_reuses_ocr_context_and_warnings(tmp_path: Path):
    class FakeWindow:
        def capture(self, **kwargs):
            buffer = BytesIO()
            Image.new("RGB", (20, 20), color="white").save(buffer, format="PNG")
            return buffer.getvalue()

        def capture_image(self, **kwargs):
            return Image.new("RGB", (201, 61), color="white")

        def capture_to_workspace(self, request_id: str | None = None):
            return tmp_path / "shot.png"

        def prepare_input(self):
            return None

        def is_foreground(self):
            return True

        def client_region(self):
            return Region(left=0, top=0, width=1920, height=1080)

    class FakeMatcher:
        def locate(self, template: str, image):
            return None

    class FakeInput:
        def ensure_available(self):
            return None
        def click(self, x, y, **kwargs):
            return None
        def drag(self, from_x, from_y, to_x, to_y):
            return None
        def press(self, key: str):
            return None
        def hotkey(self, *keys: str):
            return None
        def type_text(self, text: str):
            return None

    class FakeOcrEngine:
        def run(self, image, *, ocr=None):
            return OcrRunResult(pieces=[{"text": "希儿", "score": 0.99, "box": {"left": 1, "top": 1, "width": 10, "height": 10}}], warnings=[], trace=[])

    runtime = RuntimeOperator(FakeWindow(), FakeMatcher(), FakeOcrEngine(), FakeInput())

    runtime.ocr_image(Image.new("RGB", (201, 61), color="white"), ocr=OcrRequestConfig(provider="cpu", ocr_mode="high", retry_high="never"))

    context = runtime.consume_debug_context()
    assert context["ocr_mode_effective"] == "high"
    assert context["ocr_retry_high"] == 0
```

- [ ] **Step 2: 跑红灯，确认基础能力还不存在**

Run: `rtk uv run pytest tests/test_runtime_backends.py -k "capture_image_preserves_region_size or ocr_image_reuses_ocr_context" -q --basetemp .pytest-tmp-runtime`
Expected: FAIL，报 `capture_image` / `ocr_image` 未定义，或行为与断言不一致。

- [ ] **Step 3: 在 `window.py` 加原始 region 图片接口，并让旧 `capture()` 继续走 canonical 语义**

`trail/runtime/window.py`

```python
def capture_image(self, *, from_x=None, from_y=None, to_x=None, to_y=None, normalize: bool = True):
    window = self._resolve_window()
    region = self._resolve_region(window)
    if all(value is not None for value in (from_x, from_y, to_x, to_y)):
        region = region.sub_region(
            _scale_canonical_capture_value(from_x, target_size=region.width, canonical_size=CANONICAL_CLIENT_WIDTH),
            _scale_canonical_capture_value(from_y, target_size=region.height, canonical_size=CANONICAL_CLIENT_HEIGHT),
            _scale_canonical_capture_value(to_x, target_size=region.width, canonical_size=CANONICAL_CLIENT_WIDTH),
            _scale_canonical_capture_value(to_y, target_size=region.height, canonical_size=CANONICAL_CLIENT_HEIGHT),
        )

    hwnd = getattr(window, "_hWnd", None)
    capture_hwnd = int(hwnd) if hwnd is not None else None
    capture_region = region
    if capture_hwnd is not None and all(value is None for value in (from_x, from_y, to_x, to_y)):
        overlay_target = _find_owned_overlay_target(capture_hwnd, region)
        if overlay_target is not None:
            capture_hwnd, capture_region = overlay_target

    if sys.platform == "win32" and capture_hwnd is not None:
        try:
            image = _capture_with_windows_capture(capture_hwnd, capture_region)
        except Exception:
            scaled_region = _scale_region_for_screen_capture(capture_region, capture_hwnd)
            image = _grab_region_with_imagegrab(scaled_region)
    else:
        image = _grab_region_with_imagegrab(capture_region)

    if normalize:
        image = self._normalize_captured_image(image, target_size=_target_capture_size(region, getattr(window, "_hWnd", None)))
    return image


def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None) -> bytes:
    image = self.capture_image(from_x=from_x, from_y=from_y, to_x=to_x, to_y=to_y, normalize=True)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
```

- [ ] **Step 4: 在 `operator.py` 抽共享 OCR 执行路径，并补 `ocr_image()`**

`trail/runtime/operator.py`

```python
def _ocr_from_image(self, image, *, ocr: OcrRequestConfig | None = None):
    ocr_config = normalize_runtime_ocr_request_config(ocr)
    self._set_debug_context(
        ocr_mode_requested=ocr_config.ocr_mode,
        ocr_mode_effective=ocr_config.ocr_mode,
        ocr_scale_applied="native",
        ocr_retry_high=0,
        ocr_retry_reason="none",
    )
    fast_attempt = self._run_ocr_attempt(image, ocr_config=ocr_config)
    retry_high, retry_reason = self._should_retry_high(ocr_config=ocr_config, attempt=fast_attempt)
    final_attempt = fast_attempt
    if retry_high:
        final_attempt = self._run_ocr_attempt(image, ocr_config=self._to_high_ocr_config(ocr_config))
    self._set_debug_context(
        ocr_mode_requested=ocr_config.ocr_mode,
        ocr_mode_effective=final_attempt.mode,
        ocr_scale_applied=final_attempt.scale_applied,
        ocr_retry_high=1 if retry_high else 0,
        ocr_retry_reason=retry_reason,
    )
    for warning in final_attempt.warnings:
        self._append_warning(warning)
    for trace in final_attempt.trace:
        self._append_trace(trace)
    return list(final_attempt.pieces)


def ocr(self, *, capture: dict[str, Any] | None = None, ocr: OcrRequestConfig | None = None):
    image = self.screenshot(**dict(capture or {}))
    pieces = self._ocr_from_image(image, ocr=ocr)
    self._record_trace("ocr", kwargs=dict(capture or {}), pieces=len(pieces))
    return pieces


def capture_image(self, **kwargs):
    capture_image = getattr(self.window, "capture_image", None)
    if not callable(capture_image):
        raise TrailError("SCREENSHOT_FAILED", "window controller does not support capture_image")
    return capture_image(**kwargs)


def ocr_image(self, image, *, ocr: OcrRequestConfig | None = None):
    pieces = self._ocr_from_image(image, ocr=ocr)
    self._record_trace("ocr_image", pieces=len(pieces))
    return pieces
```

- [ ] **Step 5: 回归这组 runtime 测试；不要 commit**

Run: `rtk uv run pytest tests/test_runtime_backends.py -k "capture_image_preserves_region_size or ocr_image_reuses_ocr_context" -q --basetemp .pytest-tmp-runtime`
Expected: PASS

Note: 当前仓库规则要求只有用户明确要求时才创建 commit，这个任务结束后不要提交。

### Task 2: 把 `cw slots read` 改成批量截图 + 单次 OCR

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\trail\scenes\cw\slots.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\tests\test_cw_slots.py`

- [ ] **Step 1: 先写“批量截图只做一次 OCR”的失败测试**

```python
def test_build_cw_slots_reader_batches_target_captures_into_single_ocr_call(monkeypatch):
    slots_module = load_cw_slots_module()
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)

    class RuntimeSpy:
        def __init__(self):
            self.clicks = []
            self.capture_calls = []
            self.ocr_image_calls = []

        def locate(self, template: str, **kwargs):
            return None

        def click_point(self, x: int, y: int, **kwargs):
            self.clicks.append((x, y))

        def capture_image(self, **kwargs):
            self.capture_calls.append(kwargs)
            return Image.new("RGB", (201, 61), color="white")

        def ocr_image(self, image, *, ocr=None):
            self.ocr_image_calls.append({"size": image.size, "ocr": ocr})
            return [
                {"text": "希儿", "box": {"left": 10, "top": 10, "width": 60, "height": 20}},
                {"text": "银狼", "box": {"left": 10, "top": 95, "width": 60, "height": 20}},
            ]

    runtime = RuntimeSpy()
    front, back, hand = slots_module.build_cw_slots_reader(runtime, targets=["front:0", "hand:0"])()

    assert front[0] == "希儿"
    assert hand[0] == "银狼"
    assert len(runtime.capture_calls) == 2
    assert len(runtime.ocr_image_calls) == 1
```

- [ ] **Step 2: 再写边界失败测试：无几何 piece、多 target 忽略、单 target 接受**

```python
def test_batch_slot_mapping_ignores_geometryless_piece_when_multiple_targets(monkeypatch):
    slots_module = load_cw_slots_module()
    monkeypatch.setattr(slots_module, "sleep", lambda seconds: None, raising=False)

    class RuntimeSpy:
        def locate(self, template: str, **kwargs):
            return None
        def click_point(self, x: int, y: int, **kwargs):
            pass
        def capture_image(self, **kwargs):
            return Image.new("RGB", (201, 61), color="white")
        def ocr_image(self, image, *, ocr=None):
            return [{"text": "符玄"}]

    runtime = RuntimeSpy()
    front, back, hand = slots_module.build_cw_slots_reader(runtime, targets=["front:0", "front:1"])()

    assert front == [None, None, None, None]
```

- [ ] **Step 3: 跑红灯确认 `slots.py` 还在逐槽位即时 OCR**

Run: `rtk uv run pytest tests/test_cw_slots.py -k "batches_target_captures_into_single_ocr_call or geometryless_piece" -q --basetemp .pytest-tmp-slots-batch`
Expected: FAIL，表现为 `ocr_image` 未调用、调用次数错误，或结果没有映射到目标槽位。

- [ ] **Step 4: 在 `slots.py` 写最小批量流水线**

`trail/scenes/cw/slots.py`

```python
SLOT_BATCH_OCR_CONFIG = OcrRequestConfig(ocr_mode="high", retry_high="never")
SLOT_STRIP_GAP = 24


def _capture_slot_name_panel_image(runtime, *, point: tuple[int, int]):
    runtime.click_point(*point)
    sleep(SLOT_PANEL_SETTLE_SECONDS)
    try:
        return runtime.capture_image(**SLOT_NAME_REGION, normalize=False)
    finally:
        runtime.click_point(*INFO_DISMISS_POINT)
        sleep(SLOT_PANEL_SETTLE_SECONDS)


def _compose_slot_name_strip_image(captures: list[dict[str, Any]]) -> tuple[Image.Image, list[dict[str, int]]]:
    width = max(image["image"].size[0] for image in captures)
    total_height = sum(item["image"].size[1] for item in captures) + SLOT_STRIP_GAP * (len(captures) - 1)
    canvas = Image.new("RGB", (width, total_height), color="white")
    layout = []
    cursor_y = 0
    for item in captures:
        image = item["image"]
        canvas.paste(image, (0, cursor_y))
        layout.append({"top": cursor_y, "bottom": cursor_y + image.size[1]})
        cursor_y += image.size[1] + SLOT_STRIP_GAP
    return canvas, layout


def _read_batch_slot_names(runtime, captures: list[dict[str, Any]]) -> dict[tuple[str, int], str | None]:
    stitched, layout = _compose_slot_name_strip_image(captures)
    pieces = runtime.ocr_image(stitched, ocr=SLOT_BATCH_OCR_CONFIG)
    return _map_ocr_pieces_to_slot_names(pieces, captures=captures, layout=layout)
```

- [ ] **Step 5: 回归批量槽位测试；不要 commit**

Run: `rtk uv run pytest tests/test_cw_slots.py -k "batches_target_captures_into_single_ocr_call or geometryless_piece" -q --basetemp .pytest-tmp-slots-batch`
Expected: PASS

Note: 当前仓库规则要求只有用户明确要求时才创建 commit，这个任务结束后不要提交。

### Task 3: 锁定局部读取 merge 矩阵、`SLOTS_READ_EMPTY` 与名字归一化

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\trail\scenes\cw\slots.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\tests\test_cw_slots.py`

- [ ] **Step 1: 先写 stale/fresh 基线矩阵的失败测试**

```python
def test_slots_read_partial_refresh_from_stale_base_keeps_snapshot_stale(tmp_path):
    slots_module = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"]["stale"] = True

    refreshed = slots_module.read_cw_slots(
        session,
        reader=lambda: (["希儿", None, None, None], [None] * 6, [None] * 9),
        targets=["front:0"],
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "希儿"
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True


def test_slots_read_partial_refresh_from_fresh_base_keeps_snapshot_fresh(tmp_path):
    slots_module = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)

    refreshed = slots_module.read_cw_slots(
        session,
        reader=lambda: ([None, "布洛妮娅", None, None], [None] * 6, [None] * 9),
        targets=["front:1"],
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][1] == "布洛妮娅"
    assert refreshed.scene_state["cw"]["slots"]["stale"] is False
```

- [ ] **Step 2: 再写名字归一化与 stale 候选隔离的失败测试**

```python
def test_slots_read_normalizes_name_from_fresh_session_candidates_only(tmp_path):
    slots_module = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "on_field": {"符玄": 1},
        "off_field": {"椒丘": 1},
    }
    session.scene_state["cw"]["slots"]["front"] = ["符玄", None, None, None]
    session.scene_state["cw"]["slots"]["stale"] = False

    refreshed = slots_module.read_cw_slots(
        session,
        reader=lambda: (["用非", None, None, None], [None] * 6, [None] * 9),
        targets=["front:0"],
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "符玄"


def test_slots_read_does_not_use_stale_slot_names_as_normalization_candidates(tmp_path):
    slots_module = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"]["front"] = ["布洛妮娅", None, None, None]
    session.scene_state["cw"]["slots"]["stale"] = True

    refreshed = slots_module.read_cw_slots(
        session,
        reader=lambda: (["布落妮亚", None, None, None], [None] * 6, [None] * 9),
        targets=["front:0"],
    )

    assert refreshed.scene_state["cw"]["slots"]["front"][0] == "布落妮亚"
```

- [ ] **Step 3: 跑红灯，确认 merge/stale/normalize 契约当前未实现**

Run: `rtk uv run pytest tests/test_cw_slots.py -k "partial_refresh_from_stale_base or normalizes_name_from_fresh_session_candidates_only or does_not_use_stale_slot_names_as_normalization_candidates" -q --basetemp .pytest-tmp-slots-state`
Expected: FAIL，表现为 `stale` 被错误清掉，或脏字没有被纠正/被错误纠正。

- [ ] **Step 4: 在 `slots.py` 写最小 merge 与归一化逻辑**

`trail/scenes/cw/slots.py`

```python
def _score_slot_name_candidates(text: str, *, candidates: list[str]) -> list[tuple[str, float]]:
    scored = [(candidate, SequenceMatcher(a=text, b=candidate).ratio()) for candidate in candidates]
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def _session_slot_name_candidates(cw_state: dict) -> list[str]:
    candidates: list[str] = []
    guide = cw_state.get("guide") if isinstance(cw_state.get("guide"), dict) else {}
    for group in (guide.get("on_field", {}), guide.get("off_field", {})):
        if isinstance(group, dict):
            candidates.extend(str(name) for name in group.keys() if str(name).strip())
    slots = cw_state.get("slots") if isinstance(cw_state.get("slots"), dict) else {}
    if slots.get("stale") is False:
        for area in ("front", "back", "hand"):
            for value in slots.get(area, []) or []:
                if isinstance(value, str) and value.strip():
                    candidates.append(value)
    return list(dict.fromkeys(candidates))


def _normalize_slot_name(raw: str | None, *, candidates: list[str]) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text in candidates:
        return text
    scored = _score_slot_name_candidates(text, candidates=candidates)
    if not scored:
        return text
    best, score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0
    if best is not None and score >= 0.72 and (score - second_score) >= 0.12:
        return best
    return text


def _next_slots_stale(previous: dict[str, Any], *, parsed_targets: dict[str, set[int]] | None) -> bool:
    if parsed_targets is None:
        return False
    return previous.get("stale", True) is not False
```

- [ ] **Step 5: 回归状态矩阵与名字归一化测试；不要 commit**

Run: `rtk uv run pytest tests/test_cw_slots.py -k "partial_refresh_from_stale_base or normalizes_name_from_fresh_session_candidates_only or does_not_use_stale_slot_names_as_normalization_candidates" -q --basetemp .pytest-tmp-slots-state`
Expected: PASS

Note: 当前仓库规则要求只有用户明确要求时才创建 commit，这个任务结束后不要提交。

### Task 4: 同步 `cw slots read --help`、README 与 skills

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\trail\commands\cw.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\tests\test_atomic_commands.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\README.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\skills\trail-cw-slots\SKILL.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\skills\trail-cw\SKILL.md`

- [ ] **Step 1: 先写叶子命令 help 的失败测试**

```python
def test_cw_slots_read_help_describes_slot_as_targeted_confirmation(cli_runner):
    result = cli_runner.invoke(app, ["cw", "slots", "read", "--help"])

    assert result.exit_code == 0
    normalized = " ".join(result.stdout.split())
    assert "--slot" in normalized
    assert "优先读取截图中看见有角色" in normalized
    assert "不传 --slot 时仍保留全量读取" in normalized
```

- [ ] **Step 2: 跑红灯确认当前帮助页缺少这条边界**

Run: `rtk uv run pytest tests/test_atomic_commands.py -k "cw_slots_read_help_describes_slot_as_targeted_confirmation" -q --basetemp .pytest-tmp-help`
Expected: FAIL，说明 `--slot` 选项或 help 文案还没表达“推荐定向读 + 默认全量契约保留”。

- [ ] **Step 3: 在 `cw.py` 补命令和 `--slot` 选项帮助文案**

`trail/commands/cw.py`

```python
@slots_app.command("read", help="读取编队槽位；优先对截图中需要确认名字的槽位显式传 --slot 做定向读取。")
def cw_slots_read(
    session: str = typer.Option(..., "--session"),
    slot: list[str] | None = typer.Option(
        None,
        "--slot",
        help="只读取指定槽位；适合截图里看见有角色但名字不确定的场景。不传 --slot 时仍保留当前全量读取语义。",
    ),
) -> None:
    _print_cw("cw.slots.read", session_id=session, payload={"slot": list(slot or []) or None})
```

- [ ] **Step 4: 同步 README 与 skills，写死两层边界**

`README.md`

```markdown
- `trail cw slots read --session <id> --slot front:0 --slot hand:3`：优先用于确认截图里已看见角色、但名字不确定的槽位。
- 不传 `--slot` 时，`cw slots read` 仍保留全量读取契约，只作为完整快照兜底。
```

`skills/trail-cw-slots/SKILL.md`

```markdown
- 运行 `slots read` 前，先看当前阶段已有 screenshot。
- 若只需确认个别槽位名字，显式传 `--slot`；不要默认全量扫。
- 不传 `--slot` 时仍是完整快照兜底，不是新的强制废弃路径。
```

`skills/trail-cw/SKILL.md`

```markdown
- 槽位名字确认优先使用 `trail cw slots read --slot ...`。
- `trail ocr read` 保留给非槽位特定文本或通用 OCR 读取，不再作为槽位名确认的默认入口。
```

- [ ] **Step 5: 回归 help 测试并人工检查文档 diff；不要 commit**

Run: `rtk uv run pytest tests/test_atomic_commands.py -k "cw_slots_read_help_describes_slot_as_targeted_confirmation" -q --basetemp .pytest-tmp-help`
Expected: PASS

Manual check: `rtk git diff -- README.md skills/trail-cw-slots/SKILL.md skills/trail-cw/SKILL.md trail/commands/cw.py tests/test_atomic_commands.py`
Expected: 只出现本任务预期的 help / README / skill 变更。

Note: 当前仓库规则要求只有用户明确要求时才创建 commit，这个任务结束后不要提交。

### Task 5: 做一次面向实现验收的总回归

**Files:**
- Test: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\tests\test_runtime_backends.py`
- Test: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\tests\test_cw_slots.py`
- Test: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr\tests\test_atomic_commands.py`

- [ ] **Step 1: 跑 runtime 与 slots 核心测试集**

Run: `rtk uv run pytest tests/test_runtime_backends.py tests/test_cw_slots.py -q --basetemp .pytest-tmp-final-core`
Expected: PASS

- [ ] **Step 2: 跑 `cw slots read` 相关 CLI/help 测试**

Run: `rtk uv run pytest tests/test_atomic_commands.py -k "cw_slots_read_help_describes_slot_as_targeted_confirmation or slots and read" -q --basetemp .pytest-tmp-final-cli`
Expected: PASS（如果 `-k "slots and read"` 没选中任何测试，至少保留前一个显式 help 测试为 PASS）

- [ ] **Step 3: 人工核对 worktree 状态，确认只动了计划内文件**

Run: `rtk git status --short`
Expected: 只看到本计划涉及的 `trail/runtime/*`、`trail/scenes/cw/slots.py`、`trail/commands/cw.py`、`README.md`、`skills/trail-cw*.md`、相关 tests 文件修改。

- [ ] **Step 4: 记录当前 worktree 与下一步执行根目录**

```text
C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-batch-ocr
```

后续所有子代理实现、验证、review 都在这个 worktree 下继续。

- [ ] **Step 5: 不提交，等待上层子代理调度继续执行**

```text
不要创建 commit；把变更留在 worktree，交给上层子代理调度继续推进。
```

---

## 自检

### Spec 覆盖

- 批量 OCR：Task 1 + Task 2 覆盖 raw capture、in-memory OCR、strip 拼图与单次 OCR。
- `high + never`：Task 2 与 Task 3 在 `slots.py` 中落地并通过测试锁定。
- stale / 局部 merge / `SLOTS_READ_EMPTY`：Task 3 覆盖。
- 名字归一化：Task 3 覆盖，并明确不新增在线依赖。
- `--slot` 首选工作流与文档同步：Task 4 覆盖。
- 总回归与 worktree 约束：Task 5 覆盖。

### Placeholder 扫描

- 没有 `TODO` / `TBD` / “自行实现” 这类占位语句。
- 所有代码改动步骤都给了实际函数名、测试名和命令。

### 命名一致性

- 原始截图入口统一叫 `capture_image(..., normalize=False)`。
- 内存 OCR 入口统一叫 `ocr_image(...)`。
- 批量槽位路径统一围绕 `SLOT_BATCH_OCR_CONFIG`、`_capture_slot_name_panel_image()`、`_compose_slot_name_strip_image()` 组织。
