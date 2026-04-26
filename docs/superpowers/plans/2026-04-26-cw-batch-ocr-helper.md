# CW Batch OCR Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增可组合 batch OCR helper，迁移 `cw.slots.read`，让 slots 顺带刷新 `cw_state.stage.status`，并让 shop 扫描只扫描商店页本身且投影已有 stage status。

**Architecture:** `trail/runtime/batch_ocr.py` 只做切片装箱、atlas OCR、piece 回映射和 finalized debug。`trail/scenes/cw/stage.py` 拥有全局状态区域、解析、status stale/merge-preserve；`trail/scenes/cw/slots.py` 编排角色和 stage status target；`trail/scenes/cw/shop.py` 只持久化 shop facts，并通过投影函数把 stage status 附到响应。

**Tech Stack:** Python 3.12、Pillow、`rectangle-packer` / `rpack`、pytest、现有 `RuntimeOperator` debug recorder、现有 CW scene/session model。

---

## 工作区与约束

- Worktree: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-batch-ocr-helper`
- Spec: `docs/superpowers/specs/2026-04-26-cw-batch-ocr-helper-design.md`
- Plan: `docs/superpowers/plans/2026-04-26-cw-batch-ocr-helper.md`
- 不创建 git commit，除非用户明确要求。
- 不修改 archive skills，不新增默认正文前缀，不改变 YAML allowlist。

## 文件结构

- Create: `trail/runtime/batch_ocr.py`。Batch OCR 数据结构、packing、OCR、piece local-coordinate remap、drop/debug。
- Create: `tests/test_batch_ocr.py`。Helper 单元测试。
- Modify: `pyproject.toml`、`uv.lock`。新增 `rectangle-packer>=2.1.0`。
- Modify: `trail/scenes/cw/stage.py`。全局状态区域/parser、`mark_cw_stage_status_stale()`、stage merge-preserve helpers。
- Modify: `trail/scenes/cw/slots.py`。新增 `CwSlotsReadResult`，slots reader 合并 status targets，`read_cw_slots()` 写 `cw_state.stage.status`。
- Modify: `trail/scenes/cw/shop.py`。shop scanner 只读 `items/coins`，新增 shop response projection。
- Modify: `trail/daemon/cw_service.py`。`cw.shop.scan` 返回投影结果，`cw.slots.read` 仍返回 slots data。
- Modify: tests for slots/shop/rendering/RPC/stage/debug/docs/skills as listed in tasks.

## Task 1: Batch OCR Helper And Dependency

**Files:**
- Create: `trail/runtime/batch_ocr.py`
- Create: `tests/test_batch_ocr.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_batch_ocr.py` with tests for dependency smoke, packing invariants, local-coordinate remap, drop behavior, finalized debug, and exception propagation.

```python
from __future__ import annotations

from PIL import Image
import pytest

from trail.core.errors import TrailError
from trail.runtime.batch_ocr import BatchOcrTarget, pack_batch_ocr_targets, run_batch_ocr


def _image(width: int, height: int) -> Image.Image:
    return Image.new("RGB", (width, height), color="white")


def test_rectangle_packer_import_and_minimal_pack_smoke():
    import rpack

    assert len(rpack.pack([(10, 10), (20, 5)])) == 2


def test_pack_batch_ocr_targets_validates_inputs():
    with pytest.raises(TrailError) as empty:
        pack_batch_ocr_targets([])
    assert empty.value.code == "BATCH_OCR_EMPTY_TARGETS"

    with pytest.raises(TrailError) as duplicate:
        pack_batch_ocr_targets([BatchOcrTarget("x", _image(1, 1)), BatchOcrTarget("x", _image(2, 2))])
    assert duplicate.value.code == "BATCH_OCR_DUPLICATE_KEY"

    with pytest.raises(TrailError) as invalid:
        pack_batch_ocr_targets([BatchOcrTarget("bad", Image.new("RGB", (0, 10)))])
    assert invalid.value.code == "BATCH_OCR_INVALID_TARGET_IMAGE"


def test_pack_batch_ocr_targets_keeps_key_order_and_content_rects():
    targets = [
        BatchOcrTarget("items", _image(900, 70), padding=4),
        BatchOcrTarget("coins", _image(90, 60), padding=4),
        BatchOcrTarget("level", _image(140, 70), padding=4),
    ]

    atlas, packed = pack_batch_ocr_targets(targets, gap=24)

    assert [item.key for item in packed] == [target.key for target in targets]
    assert atlas.width > 0 and atlas.height > 0
    assert 1.0 < atlas.width / atlas.height < 12.0
    for target, item in zip(targets, packed, strict=True):
        assert item.content_rect["width"] == target.image.width
        assert item.content_rect["height"] == target.image.height
        assert item.rect["width"] == target.image.width + 2 * target.padding
        assert item.rect["height"] == target.image.height + 2 * target.padding


def test_pack_batch_ocr_targets_rects_do_not_overlap_and_atlas_covers_all_rects():
    targets = [BatchOcrTarget(idx, _image(40 + idx * 10, 20 + idx * 5), padding=3) for idx in range(5)]

    atlas, packed = pack_batch_ocr_targets(targets, gap=12)

    for item in packed:
        assert 0 <= item.rect["left"] < item.rect["right"] <= atlas.width
        assert 0 <= item.rect["top"] < item.rect["bottom"] <= atlas.height
    for left_index, left in enumerate(packed):
        for right in packed[left_index + 1 :]:
            assert (
                left.rect["right"] <= right.rect["left"]
                or right.rect["right"] <= left.rect["left"]
                or left.rect["bottom"] <= right.rect["top"]
                or right.rect["bottom"] <= left.rect["top"]
            )


def test_pack_batch_ocr_targets_impossible_constraints_raise_pack_failed():
    with pytest.raises(TrailError) as exc_info:
        pack_batch_ocr_targets([BatchOcrTarget("wide", _image(100, 10))], max_width=50)

    assert exc_info.value.code == "BATCH_OCR_PACK_FAILED"


class RuntimeSpy:
    def __init__(self, pieces=None, error: Exception | None = None):
        self.pieces = pieces or []
        self.error = error
        self.ocr_image_calls = []
        self.trace = []

    def ocr_image(self, image, *, ocr=None):
        self.ocr_image_calls.append({"size": image.size, "ocr": ocr})
        if self.error is not None:
            raise self.error
        return list(self.pieces)

    def _begin_debug_action(self, step: str, **payload):
        event = {"step": step, **payload}
        self.trace.append(event)

        class Action:
            def finish(_, *, ok: bool, **finish_payload):
                event.update(finish_payload)
                event["ok"] = 1 if ok else 0
                event["dur_ms"] = 0
                event["ts"] = "2026-04-26T00:00:00.000Z"

        return Action()

    def _finish_debug_action(self, action, *, ok: bool, **payload):
        action.finish(ok=ok, **payload)


def test_run_batch_ocr_returns_target_local_rapidocr_tuple_coordinates():
    target = BatchOcrTarget("items", _image(100, 40), padding=4)
    _, [packed] = pack_batch_ocr_targets([target])
    x = packed.content_rect["left"]
    y = packed.content_rect["top"]
    runtime = RuntimeSpy([([(x + 10, y + 5), (x + 60, y + 5), (x + 60, y + 20), (x + 10, y + 20)], "黑塔", 0.99)])

    result = run_batch_ocr(runtime, [target])

    assert result.by_key["items"].text == "黑塔"
    assert result.by_key["items"].pieces == [([(10, 5), (60, 5), (60, 20), (10, 20)], "黑塔", 0.99)]
    assert runtime.ocr_image_calls[0]["ocr"].ocr_mode == "high"
    assert runtime.ocr_image_calls[0]["ocr"].retry_high == "never"


def test_run_batch_ocr_drops_gap_missing_box_and_empty_text():
    targets = [BatchOcrTarget("left", _image(60, 20)), BatchOcrTarget("right", _image(60, 20))]
    _, packed = pack_batch_ocr_targets(targets, gap=80)
    left_rect = packed[0].content_rect
    gap_x = packed[0].rect["right"] + 10
    runtime = RuntimeSpy([
        {"text": "左", "box": {"left": left_rect["left"] + 1, "top": left_rect["top"] + 1, "width": 10, "height": 10}},
        {"text": "噪声", "box": {"left": gap_x, "top": left_rect["top"], "width": 10, "height": 10}},
        {"text": "无框"},
        {"text": ""},
    ])

    result = run_batch_ocr(runtime, targets, gap=80)

    assert result.by_key["left"].text == "左"
    assert result.by_key["right"].text is None
    assert [drop["reason"] for drop in result.dropped] == ["outside_target", "missing_box", "empty_text"]
    assert runtime.trace[0]["step"] == "batch_ocr"
    assert runtime.trace[0]["ok"] == 1
    assert runtime.trace[0]["drop_count"] == 3


def test_run_batch_ocr_supports_dict_boxes_half_open_boundaries_and_stable_sorting():
    target = BatchOcrTarget("one", _image(80, 40), padding=4)
    _, [packed] = pack_batch_ocr_targets([target])
    x = packed.content_rect["left"]
    y = packed.content_rect["top"]
    runtime = RuntimeSpy([
        {"text": "儿", "box": {"left": x + 30, "top": y + 10, "width": 10, "height": 10}},
        {"text": "希", "box": {"left": x + 10, "top": y + 10, "width": 10, "height": 10}},
        {"text": "界外", "box": {"left": packed.content_rect["right"], "top": y + 1, "width": 10, "height": 10}},
    ])

    result = run_batch_ocr(runtime, [target])

    assert result.by_key["one"].text == "希儿"
    assert [drop["reason"] for drop in result.dropped] == ["outside_target"]


def test_run_batch_ocr_assigns_missing_box_to_single_target():
    runtime = RuntimeSpy([{"text": "希"}, {"text": "儿"}])

    result = run_batch_ocr(runtime, [BatchOcrTarget("one", _image(80, 40))])

    assert result.by_key["one"].text == "希儿"


def test_run_batch_ocr_drops_piece_crossing_two_targets():
    targets = [BatchOcrTarget("left", _image(30, 20)), BatchOcrTarget("right", _image(30, 20))]
    _, packed = pack_batch_ocr_targets(targets, gap=0)
    left = packed[0].content_rect
    right = packed[1].content_rect
    runtime = RuntimeSpy([
        {"text": "跨界", "box": {"left": left["right"] - 5, "top": left["top"], "width": right["left"] - left["right"] + 10, "height": 10}}
    ])

    result = run_batch_ocr(runtime, targets, gap=0)

    assert result.by_key["left"].text is None
    assert result.by_key["right"].text is None
    assert result.dropped[0]["reason"] == "cross_target_boundary"


def test_pack_batch_ocr_targets_candidate_scoring_avoids_extreme_single_column():
    targets = [BatchOcrTarget(str(index), _image(80, 40), padding=4) for index in range(8)]

    atlas, _ = pack_batch_ocr_targets(targets, gap=12, target_aspect=16 / 9)

    assert atlas.height < 8 * (40 + 2 * 4 + 12)
    assert 0.4 < atlas.width / atlas.height < 6.0


def test_run_batch_ocr_supports_object_style_box_and_out_of_atlas_drop():
    class Box:
        def __init__(self, left: int, top: int, width: int, height: int):
            self.left = left
            self.top = top
            self.width = width
            self.height = height

    class BoxPiece:
        def __init__(self, text: str, left: int, top: int, width: int, height: int):
            self.text = text
            self.box = Box(left, top, width, height)

    target = BatchOcrTarget("box", _image(60, 30), padding=4)
    _, [packed] = pack_batch_ocr_targets([target])
    runtime = RuntimeSpy([
        BoxPiece("内", packed.content_rect["left"] + 5, packed.content_rect["top"] + 5, 10, 10),
        BoxPiece("外", packed.content_rect["right"] + 100, packed.content_rect["bottom"] + 100, 10, 10),
    ])

    result = run_batch_ocr(runtime, [target])

    assert result.by_key["box"].text == "内"
    assert result.by_key["box"].pieces[0].box.left == 5
    assert result.by_key["box"].pieces[0].box.top == 5
    assert result.by_key["box"].pieces[0].box.width == 10
    assert result.by_key["box"].pieces[0].box.height == 10
    assert result.dropped[0]["reason"] == "outside_target"


def test_run_batch_ocr_reraises_ocr_error_and_records_failed_action():
    runtime = RuntimeSpy(error=RuntimeError("ocr boom"))

    with pytest.raises(RuntimeError, match="ocr boom"):
        run_batch_ocr(runtime, [BatchOcrTarget("front", _image(50, 20))], trace_prefix="cw_slots_batch_ocr")

    assert runtime.trace[0]["step"] == "cw_slots_batch_ocr"
    assert runtime.trace[0]["ok"] == 0
```

- [ ] **Step 2: Run failing tests**

Run: `uv run pytest tests/test_batch_ocr.py -q`

Expected: FAIL because `trail.runtime.batch_ocr` or `rpack` is missing.

- [ ] **Step 3: Add dependency**

Run: `uv add "rectangle-packer>=2.1.0"`

Expected: `pyproject.toml` and `uv.lock` both change; `uv.lock` contains `rectangle-packer`.

- [ ] **Step 4: Implement helper**

Create `trail/runtime/batch_ocr.py` with the dataclasses and functions named in the spec. Implementation requirements:

```python
@dataclass(frozen=True)
class BatchOcrTarget:
    key: Hashable
    image: Image.Image
    padding: int = 4
    metadata: Mapping[str, Any] | None = None
```

Implement `pack_batch_ocr_targets()` so it validates empty/duplicate/invalid targets, builds padded sizes, calls `rpack.pack()`, uses `rpack.bbox_size()`, pastes images into a white atlas, and returns `PackedOcrTarget` objects in input order. Do not accept the first `rpack.pack()` result blindly: implement the spec's finite candidate width set, score candidates with blank ratio / aspect penalty / long-side penalty, pass explicit `max_height` as a hard constraint, discard candidates that raise `rpack.PackingImpossibleError`, and raise `BATCH_OCR_PACK_FAILED` if all candidates fail. Gap is applied to packing size only; `rect` remains the padded content frame and `content_rect` remains original image size.

Implement `run_batch_ocr()` so it calls `runtime.ocr_image(atlas, ocr=OcrRequestConfig(ocr_mode="high", retry_high="never"))`, maps pieces by center point into `content_rect`, translates piece geometry back to target-local coordinates, preserves every input key in `by_key`, records finalized debug action if runtime supports it, and re-raises OCR exceptions.

- [ ] **Step 5: Verify helper**

Run: `uv run pytest tests/test_batch_ocr.py -q`

Expected: PASS.

## Task 2: Stage Status Unit And Preserve Semantics

**Files:**
- Modify: `trail/scenes/cw/stage.py`
- Modify: stage-related tests, preferably `tests/test_cw_stage.py` or existing stage sections in `tests/test_cw_shop.py` / `tests/test_cw_slots.py`

- [ ] **Step 1: Write stage status tests**

Add tests for parser, merge-preserve, and stale helper. Use existing test file conventions.

```python
def test_stage_status_parser_preserves_zero_role_counts():
    stage = load_cw_stage_module()
    by_key = {
        ("stage_status", "level"): SimpleNamespace(pieces=[_rapidocr_piece("LV.7")]),
        ("stage_status", "exp"): SimpleNamespace(pieces=[_rapidocr_piece("4/52")]),
        ("stage_status", "team_size"): SimpleNamespace(pieces=[_rapidocr_piece("3/3")]),
    }

    status = stage.parse_cw_stage_status(by_key, role_count={"front": 0, "back": 0, "hand": 0, "field": 0, "total": 0})

    assert status == {
        "stale": False,
        "level": 7,
        "exp": "4/52",
        "team_size": "3/3",
        "role_count": {"front": 0, "back": 0, "hand": 0, "field": 0, "total": 0},
    }


def test_detect_cw_stage_preserves_existing_stage_status(tmp_path):
    stage = load_cw_stage_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7}}

    refreshed = stage.detect_cw_stage(session, detector=lambda: "shop")

    assert refreshed.scene_state["cw"]["stage"]["value"] == "shop"
    assert refreshed.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}


def test_mark_cw_stage_status_stale_keeps_values(tmp_path):
    stage = load_cw_stage_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"value": "shop", "stale": False, "status": {"stale": False, "team_size": "3/3"}}

    stage.mark_cw_stage_status_stale(session)

    assert session.scene_state["cw"]["stage"]["value"] == "shop"
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": True, "team_size": "3/3"}


def test_stage_error_and_stale_paths_preserve_existing_stage_status(tmp_path):
    stage = load_cw_stage_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7}}

    stage.mark_cw_stage_stale(session)
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}

    session.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7}}
    with pytest.raises(TrailError):
        stage.detect_cw_stage(session, detector=lambda: (_ for _ in ()).throw(TrailError("STAGE_AMBIGUOUS", "ambiguous")))
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}
```

Add regression tests for every known whole-stage write site. Use focused direct function tests when available, or grep-backed tests that enforce helper usage. Known files to cover: `trail/scenes/cw/battle.py`, `trail/scenes/cw/portal.py`, `trail/scenes/cw/strategy.py`, `trail/scenes/cw/entry.py`, `trail/scenes/cw/guide.py`. Each test must start with `cw_state.stage.status={"stale": False, "level": 7}` and assert the status survives the stage update path.

- [ ] **Step 2: Run failing stage tests**

Run: `uv run pytest tests/test_cw_stage.py -q` or the exact file containing the new tests.

Expected: FAIL because parser/stale helpers do not exist or stage overwrites status.

- [ ] **Step 3: Implement stage status helpers**

In `trail/scenes/cw/stage.py` add constants and helpers:

```python
CW_STATUS_LEVEL_REGION = {"from_x": 220, "from_y": 880, "to_x": 360, "to_y": 950}
CW_STATUS_EXP_REGION = {"from_x": 256, "from_y": 943, "to_x": 325, "to_y": 973}
CW_STATUS_TEAM_SIZE_REGION = {"from_x": 835, "from_y": 188, "to_x": 1095, "to_y": 281}
```

Move or duplicate the small pure parsing functions for level/exp/team size from `shop.py` into `stage.py`, then have `shop.py` import the shared versions if still needed.

Add merge helpers:

```python
def _stage_state(session: SessionModel) -> dict:
    return ensure_cw_state(session).setdefault("stage", {"stale": True})


def _replace_stage_fields(session: SessionModel, **fields) -> None:
    current = dict(_stage_state(session))
    status = current.get("status")
    current.update(fields)
    if isinstance(status, dict):
        current["status"] = status
    ensure_cw_state(session)["stage"] = current


def mark_cw_stage_status_stale(session: SessionModel) -> None:
    stage = _stage_state(session)
    status = dict(stage.get("status") if isinstance(stage.get("status"), dict) else {})
    status["stale"] = True
    stage["status"] = status
```

Update existing `mark_cw_stage_stale()`, `_invalidate_cw_stage()`, and `detect_cw_stage()` to use `_replace_stage_fields()`. Then search and update all direct whole-stage assignments:

Run: `rg 'cw_state\["stage"\]\s*=|ensure_cw_state\(session\)\["stage"\]\s*=' trail/scenes/cw`

Expected matches to migrate include `stage.py`, `battle.py`, `portal.py`, `strategy.py`, `entry.py`, and `guide.py`. Replace direct assignment with a shared merge helper that preserves `stage.status`.

- [ ] **Step 4: Verify stage tests**

Run: `uv run pytest tests/test_cw_stage.py -q`

Expected: PASS.

## Task 3: Migrate Slots Read And Persist Stage Status

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Modify: `tests/test_cw_slots.py`

- [ ] **Step 1: Write slots reader contract tests**

Update slots tests so production reader returns `CwSlotsReadResult` and `read_cw_slots()` persists stage status.

```python
def test_read_cw_slots_persists_stage_status_without_overwriting_stage_value(tmp_path):
    slots = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"value": "shop", "stale": False}
    result = slots.CwSlotsReadResult(
        front=[{"name": "希儿", "star": 4}, None, None, None],
        back=[None, None, None, None, None, None],
        hand=[None, None, None, None, None, None, None, None, None],
        stage_status={"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
    )

    refreshed = slots.read_cw_slots(session, reader=lambda: result)

    assert refreshed.scene_state["cw"]["stage"]["value"] == "shop"
    assert refreshed.scene_state["cw"]["stage"]["status"]["level"] == 7
    assert refreshed.scene_state["cw"]["stage"]["status"]["role_count"]["front"] == 1
    assert refreshed.scene_state["cw"]["stage"]["status"]["role_count"]["back"] == 0
```

Add a reader orchestration test that records event order: center dismiss click, wait, status captures, then first slot click, and one `ocr_image()` total.

Use a production `build_cw_slots_reader()` fake runtime whose `ocr_image()` returns both stage status and slot name pieces from the same atlas. Assert `type(result).__name__ == "CwSlotsReadResult"`, `result.stage_status == {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}`, slot names are parsed, and `len(runtime.ocr_image_calls) == 1`. The event list must show `capture_image(CW_STATUS_*)` before the first slot coordinate click.

- [ ] **Step 2: Run failing slots tests**

Run: `uv run pytest tests/test_cw_slots.py -q`

Expected: FAIL because `CwSlotsReadResult` and status capture are missing.

- [ ] **Step 3: Implement slots result and status target orchestration**

In `slots.py` add:

```python
@dataclass(frozen=True)
class CwSlotsReadResult:
    front: list[Any]
    back: list[Any]
    hand: list[Any]
    stage_status: dict[str, Any] | None = None
```

Change `build_cw_slots_reader()` to return `CwSlotsReadResult`. After `runtime.click_point(*INFO_DISMISS_POINT)` and sleep, capture `stage.CW_STATUS_*` images with `normalize=False`, build `BatchOcrTarget(("stage_status", field), image)`, then append slot name targets as `("slot", area, index)`. Run one batch OCR for both groups.

Change `read_cw_slots()` to accept tuple legacy only for tests, but production path uses `CwSlotsReadResult`. Compute role counts from merged final slots:

```python
def _slot_role_count(front, back, hand):
    return {
        "front": sum(1 for item in front if item),
        "back": sum(1 for item in back if item),
        "hand": sum(1 for item in hand if item),
        "field": sum(1 for item in front if item) + sum(1 for item in back if item),
        "total": sum(1 for item in front if item) + sum(1 for item in back if item) + sum(1 for item in hand if item),
    }
```

Merge `result.stage_status` with role count and write `cw_state["stage"]["status"]`.

- [ ] **Step 4: Verify slots tests**

Run: `uv run pytest tests/test_batch_ocr.py tests/test_cw_stage.py tests/test_cw_slots.py -q`

Expected: PASS.

## Task 4: Shop Scanner Without Global Status And Response Projection

**Files:**
- Modify: `trail/scenes/cw/shop.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_cw_shop.py`

- [ ] **Step 1: Write shop projection tests**

Add tests asserting shop scan does not capture global regions and persisted shop does not contain stage fields.

```python
def test_shop_scan_projects_stage_status_without_rescanning_global_regions(tmp_path, monkeypatch):
    shop = load_cw_shop_module()
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}}

    scanner = lambda: {"opened": True, "stale": False, "items": [{"slot": 1, "name": "黑塔", "price": 1}], "coins": 62, "reserve_full": False}
    refreshed = shop.scan_cw_shop(session, scanner=scanner)
    projected = shop.project_cw_shop_snapshot(refreshed)

    assert "level" not in refreshed.scene_state["cw"]["shop"]
    assert projected["stage_status"]["level"] == 7
    assert projected["stage_status_stale"] is False
```

Update scanner runtime tests so `runtime.ocr()` raises and `capture_image()` raises if called with `CW_STATUS_LEVEL_REGION`, `CW_STATUS_EXP_REGION`, or `CW_STATUS_TEAM_SIZE_REGION`.

Add service-level tests through the existing `CwService` / `CommandService` harness: executing `cw.shop.scan` returns response data with `stage_status` and `stage_status_stale`, while the saved session's `cw_state.shop` lacks `level/exp/team_size/role_count`. Add the same projection assertion for `cw.shop.status`.

- [ ] **Step 2: Run failing shop tests**

Run: `uv run pytest tests/test_cw_shop.py -q`

Expected: FAIL because shop still reads global status and has no projection function.

- [ ] **Step 3: Implement shop page batch scan and projection**

In `shop.py`, import `BatchOcrTarget`, `run_batch_ocr`, and stage status constants for negative tests only if needed. Replace `_read_shop_page_snapshot()` so it captures only `SHOP_SCAN_REGION` and `SHOP_COINS_REGION`, calls one `run_batch_ocr(trace_prefix="cw_shop_batch_ocr")`, parses `items/coins/reserve_full`, and returns shop facts only.

Add projection helper:

```python
def project_cw_shop_snapshot(session: SessionModel) -> dict[str, Any]:
    cw_state = ensure_cw_state(session)
    payload = deepcopy(cw_state.get("shop", {"stale": True}))
    status = cw_state.get("stage", {}).get("status") if isinstance(cw_state.get("stage"), dict) else None
    if isinstance(status, dict):
        payload["stage_status"] = deepcopy(status)
        payload["stage_status_stale"] = bool(status.get("stale", True))
    else:
        payload["stage_status_stale"] = True
    return payload
```

Have `shop_cw_status(session)` call `project_cw_shop_snapshot(session)` before attaching guide summary.

In `cw_service.py`, change `cw.shop.scan` handler to run `scan_cw_shop(...)` and return `project_cw_shop_snapshot(session)` instead of `.scene_state["cw"]["shop"]`.

- [ ] **Step 4: Verify shop tests**

Run: `uv run pytest tests/test_cw_shop.py -q`

Expected: PASS.

## Task 5: Stale Lifecycle, Renderer, Docs, Skills

**Files:**
- Modify: `trail/scenes/cw/slots.py`, `trail/scenes/cw/shop.py`, and other scene files that mark slots/stage stale
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_output_rendering.py`, `tests/test_cw_rpc_contracts.py`, `tests/test_output_debug.py`
- Modify: `README.md`
- Modify active skills only when they mention scan behavior

- [ ] **Step 1: Write stale lifecycle tests**

Add tests that after stage status is fresh, `cw.shop.buy_slot`, `cw.hand.sell`, and `cw.slots.swap/place` mark `cw_state.stage.status.stale` true. Use existing session helpers and avoid UI when pure functions exist.

- [ ] **Step 2: Implement stale marks**

Call `mark_cw_stage_status_stale(session)` anywhere `_mark_slots_stale(session)` is called or slots are changed by sell/place/swap/buy. Preserve existing slots stale behavior.

- [ ] **Step 3: Write renderer tests for stage projection**

In `tests/test_output_rendering.py`, add cases:

```python
def test_render_output_renders_shop_stage_status_projection():
    payload = {
        "ok": True,
        "data": {
            "items": [],
            "opened": True,
            "stale": False,
            "coins": 62,
            "reserve_full": False,
            "stage_status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
            "stage_status_stale": False,
        },
        "screenshot": ".trail/shots/req-shop.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.scan", payload).splitlines()[-1] == "info stage_level=7 stage_exp=4/52 stage_team_size=3/3 stage_status_stale=0"


def test_render_output_keeps_stage_status_stale_when_missing():
    payload = {"ok": True, "data": {"items": [], "opened": True, "stale": False, "stage_status_stale": True}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}

    rendered = render_output("cw.shop.scan", payload)

    assert "info stage_status_stale=1" in rendered


def test_render_output_hides_stale_stage_values_but_keeps_stale_fact():
    payload = {"ok": True, "data": {"items": [], "opened": True, "stale": False, "stage_status": {"stale": True, "level": 7, "exp": "4/52"}, "stage_status_stale": True}, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}

    rendered = render_output("cw.shop.scan", payload)

    assert "stage_status_stale=1" in rendered
    assert "stage_level" not in rendered
    assert "stage_exp" not in rendered
```

- [ ] **Step 4: Implement renderer projection lines**

Update the shop action renderer to include a separate `info stage_*` line for `cw.shop.scan` and `cw.shop.status` when data includes `stage_status_stale`. Keep `stage_status_stale=0|1` even when no level/exp/team size is present. Only output `stage_level/stage_exp/stage_team_size` when `stage_status_stale=0`; stale values remain in session for diagnosis but are not rendered as valid facts.

- [ ] **Step 4b: Add protocol no-leak, YAML, and failure recover tests**

Add or extend tests with these exact assertions:

```python
def test_render_output_does_not_leak_batch_ocr_debug_in_default_mode():
    payload = {"ok": True, "data": {"items": [], "opened": True, "stale": False, "stage_status_stale": True}, "debug": {"trace": [{"step": "cw_shop_batch_ocr", "ok": 1, "ts": "2026-04-26T00:00:00.000Z", "dur_ms": 1, "rect": {"left": 1, "top": 2, "width": 3, "height": 4}, "atlas": "debug.png"}]}, "timing": {}, "warnings": [], "references": [], "error": None}

    rendered = render_output("cw.shop.scan", payload)

    assert "debug" not in rendered
    assert "atlas" not in rendered
    assert "rect" not in rendered


def test_render_output_verbose_renders_batch_ocr_trace():
    payload = {"ok": True, "data": {"items": [], "opened": True, "stale": False, "stage_status_stale": True}, "debug": {"trace": [{"step": "cw_shop_batch_ocr", "ok": 1, "ts": "2026-04-26T00:00:00.000Z", "dur_ms": 1}]}, "timing": {}, "warnings": [], "references": [], "error": None}

    assert "debug kind=trace step=cw_shop_batch_ocr" in render_output("cw.shop.scan", payload, verbose=True)
```

Extend YAML rejection tests so `cw.shop.scan` and `cw.slots.read` still return `OUTPUT_FORMAT_NOT_SUPPORTED`. Extend screenshot success tests so `shot path=...` is immediately followed by `info read_image_first=1`. Add a CommandService/RPC test where shop page `ocr_image()` raises after the open click, and assert rendered failure contains `request id=...`, `tainted=1` or equivalent final-state taint data, and `recover action=daemon.request_status` in the existing failure order.

Add explicit `tests/test_cw_rpc_contracts.py` stdout assertions through the existing CLI/fake daemon path:

```python
def test_cw_shop_scan_rpc_stdout_includes_stage_status_fresh_fact(fake_daemon_client, cli_runner):
    fake_daemon_client.enqueue_response(
        "cw.shop.scan",
        ok=True,
        data={
            "items": [],
            "opened": True,
            "stale": False,
            "stage_status": {"stale": False, "level": 7},
            "stage_status_stale": False,
        },
        screenshot=".trail/shots/req-shop.png",
        image_guidance={"read_image_first": True},
    )

    result = cli_runner.invoke_cli(["cw", "shop", "scan", "--session", "s1"])

    assert result.exit_code == 0
    assert "shot path=.trail/shots/req-shop.png" in result.output
    assert "shot path=.trail/shots/req-shop.png\ninfo read_image_first=1" in result.output
    assert "info stage_level=7 stage_status_stale=0" in result.output


def test_cw_shop_status_rpc_stdout_includes_stage_status_stale_fact(fake_daemon_client, cli_runner):
    fake_daemon_client.enqueue_response(
        "cw.shop.status",
        ok=True,
        data={
            "items": [],
            "opened": True,
            "stale": False,
            "stage_status": {"stale": True, "level": 7},
            "stage_status_stale": True,
        },
    )

    result = cli_runner.invoke_cli(["cw", "shop", "status", "--session", "s1"])

    assert result.exit_code == 0
    assert "info stage_status_stale=1" in result.output
    assert "stage_level=7" not in result.output


def test_cw_shop_status_rpc_stdout_includes_missing_stage_status_stale_fact(fake_daemon_client, cli_runner):
    fake_daemon_client.enqueue_response(
        "cw.shop.status",
        ok=True,
        data={"items": [], "opened": True, "stale": False, "stage_status_stale": True},
    )

    result = cli_runner.invoke_cli(["cw", "shop", "status", "--session", "s1"])

    assert result.exit_code == 0
    assert "info stage_status_stale=1" in result.output
    assert "stage_level" not in result.output
    assert "stage_exp" not in result.output
    assert "stage_team_size" not in result.output
```

Adapt helper names to the repository's actual CLI fixtures, but keep these assertions and cover both `cw.shop.scan` and `cw.shop.status` stdout.

- [ ] **Step 5: Update README and active skills**

Update README examples/description for `cw.slots.read` and `cw.shop.scan`. Search active skills with:

Run: `rg "cw.shop.scan|cw.slots.read|shop scan|slots read" skills/trail-hsr skills/trail-cw-entry skills/trail-cw-portal skills/trail-cw-guide`

Only edit active skill files that describe scan flow. State that `cw.slots.read` refreshes `stage.status`, while `cw.shop.scan` only projects existing stage status.

- [ ] **Step 6: Verify contracts**

Run: `uv run pytest tests/test_output_rendering.py tests/test_output_debug.py tests/test_cw_rpc_contracts.py tests/test_cw_shop.py tests/test_cw_slots.py -q`

Expected: PASS.

## Task 6: Full Verification

**Files:** all modified files

- [ ] **Step 1: Run targeted suite**

Run: `uv run pytest tests/test_batch_ocr.py tests/test_cw_stage.py tests/test_cw_slots.py tests/test_cw_shop.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_cw_rpc_contracts.py -q`

Expected: PASS.

- [ ] **Step 2: Run dependency smoke**

Run: `uv run python -c "import rpack; print(len(rpack.pack([(1, 1), (2, 2)])))"`

Expected: `2`.

- [ ] **Step 3: Check status**

Run: `git status --short`

Expected: only files listed in this plan are modified; no `.trail/`, `.pytest_cache/`, debug screenshots, archive skills, or unrelated files.

---

## Plan Self-Review

- Spec coverage: helper, dependency/lockfile, stage status unit, slots read result contract, shop projection, stale lifecycle, renderer/docs/skills, and verification are all covered.
- Red-flag scan: no unfinished marker text remains; all pseudo-tests include concrete assertions and expected commands.
- Type consistency: names match spec: `BatchOcrTarget`, `CwSlotsReadResult`, `mark_cw_stage_status_stale`, `project_cw_shop_snapshot`, `stage_status_stale`.
