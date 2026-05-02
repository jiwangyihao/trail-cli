# CW Layer Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: First use superpowers:using-git-worktrees to stay inside the current project-local worktree, then use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前被误归为 `boss_preview` 的“整层结束后点击空白继续进入下一层”的过场页正式拆成新的对外 stage token `layer_transition`，并让 `battle.run` 把它继续视为 battle flow 的一部分完成收口。

**Architecture:** 先在 `stage.py` 与资源/入口耦合层拆清 `boss_preview` 和 `layer_transition` 的语义，再在 `battle.py` 中把 `layer_transition` 纳入 battle flow 推进状态，最后同步对外文档、surface 和结构测试。首版识别保持保守：`click_blank.png` + OCR `点击空白处继续` + OCR `位面` + 非 `本场对局首领`。

**Tech Stack:** Python 3、现有 CW scene 逻辑、Typer CLI、pytest、README/active skill/reference 文档断言。

---

> 按当前仓库协作规则，未收到用户明确要求前不主动创建 git commit。本计划里的 checkpoint 以“目标测试通过 + diff 自检”代替 commit 步骤。
>
> 当前工作在项目内 worktree `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-battle-run-settle-resume` 中进行；本计划默认继续使用 `subagent-driven-development`，并在进入任何 live 游戏操作前先征求用户确认当前画面适合测试。

## 文件结构

### 修改文件

- `trail/scenes/cw/stage.py`
  - 在 stage detector 中分流 `boss_preview` 与 `layer_transition`。
- `trail/scenes/cw/entry.py`
  - 修正所有 `stage.boss_preview` / `page=stage.boss_preview` / `_consume_click_blank_prompt()` 的使用方，避免 detector 与调用方语义失配。
- `trail/scenes/cw/battle.py`
  - 把 `layer_transition` 纳入 battle flow 可推进状态，不再在该页 `CW_BATTLE_STATE_UNKNOWN`。
- `tests/test_cw_stage.py`
  - 锁 stage detector 的新 token 分流与负向保护。
- `tests/test_cw_entry.py`
  - 锁 `entry` / `start` 链路面对 `layer_transition` 与真正 `boss_preview` 的页面语义。
- `tests/test_cw_battle_run.py`
  - 锁 `battle.run` 在 `layer_transition` 上继续收口、不会误收口成稳定阶段、不会再 `unknown` 暴毙。
- `tests/test_output_rendering.py`
  - 锁 `cw.stage.detect` / `cw.stage.wait` 对外输出出现 `stage=layer_transition`，并锁 README / stage-reference / active references 的新 token 文案。
- `tests/test_skill_structure.py`
  - 锁 active prep reference 的 stage boundary 集合已包含 `layer_transition`。
- `README.md`
  - 若直接把该页描述成 `boss_preview` 或未说明它属于 battle flow，需同步修正。
- `docs/cw-stage-reference/README.md`
  - 把当前这一类页从旧 `boss_preview` 语义中拆出。
- `skills/trail-cw-entry/SKILL.md`
  - 若直接提到 battle flow 或相关 stage token，需同步 `layer_transition`。
- `skills/trail-hsr/references/simple-command-surface.md`
  - 若直接提到 battle flow 或相关 stage token，需同步 `layer_transition`。
- `skills/trail-cw-prep/references/stage-boundaries.md`
  - 新增 `layer_transition` 边界，并保留真正 `boss_preview`。

### 只读/只运行回归文件

- `trail/scenes/cw/resources.py`
  - 这次先只读审计当前 `stage.boss_preview -> click_blank.png` 绑定；首版不新增公开 `stage.blank_continue` alias，避免内部候选 key 泄漏到调用方。
- `tests/test_cw_rpc_contracts.py`
- `tests/test_daemon_protocol.py`
- `tests/test_source_boundary.py`
- `tests/test_cw_portal.py`
- `tests/test_cw_slots.py`
- `trail/output/rendering.py`
- `AGENTS.md`

这些文件这次默认**不修改**。只在最终验证中跑回归，确保 `layer_transition` 拆分没有顺手破坏 battle.run 的现有 timeout / renderer / state.dump / clear-in-progress 契约。

## Task 1: 拆分 `boss_preview` 与 `layer_transition` 的 stage detector 语义

**Files:**
- Modify: `trail/scenes/cw/stage.py`
- Test: `tests/test_cw_stage.py`
- Test: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写 stage detector 红灯测试，锁新 token 分流**

在 `tests/test_cw_stage.py` 追加至少 4 条失败用例：

```python
def test_build_cw_stage_detector_maps_click_blank_with_layer_transition_ocr_to_layer_transition(monkeypatch):
    runtime = RuntimeSpy(
        locate_hits={_asset("stage.boss_preview"): _box("stage.boss_preview", left=440, top=480)},
        ocr_image_result=[_ocr_piece("点击空白处继续"), _ocr_piece("位面")],
    )

    detector = build_cw_stage_detector(runtime)

    assert detector() == "layer_transition"


def test_build_cw_stage_detector_keeps_true_boss_preview_when_ocr_mentions_boss(monkeypatch):
    runtime = RuntimeSpy(
        locate_hits={_asset("stage.boss_preview"): _box("stage.boss_preview", left=440, top=480)},
        ocr_image_result=[_ocr_piece("本场对局首领")],
    )

    detector = build_cw_stage_detector(runtime)

    assert detector() == "boss_preview"


def test_build_cw_stage_detector_does_not_treat_click_blank_without_layer_transition_text_as_layer_transition(monkeypatch):
    runtime = RuntimeSpy(
        locate_hits={_asset("stage.boss_preview"): _box("stage.boss_preview", left=440, top=480)},
        ocr_image_result=[_ocr_piece("点击空白处继续")],
    )

    detector = build_cw_stage_detector(runtime)

    assert detector() != "layer_transition"


@pytest.mark.parametrize("command", ("cw.stage.detect", "cw.stage.wait"))
def test_render_output_stage_commands_can_show_layer_transition(command: str):
    payload = {
        "ok": True,
        "data": {"value": "layer_transition", "stale": False},
        "screenshot": ".trail/shots/req-stage-layer-transition.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output(command, payload).splitlines() == [
        f"ok {command} stage=layer_transition stale=0",
        "shot path=.trail/shots/req-stage-layer-transition.png",
        "info read_image_first=1",
    ]
```

- [ ] **Step 2: 跑红灯测试，确认旧逻辑仍把这类页视为 `boss_preview` 或模糊页**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-stage-red tests/test_cw_stage.py -k "layer_transition or boss_preview" -v`

Expected: FAIL，至少会在 `click_blank + 点击空白处继续 + 位面 -> layer_transition`、`本场对局首领 -> boss_preview`、或负向保护上失败。

- [ ] **Step 3: 做最小实现，先让 detector 能对外返回 `layer_transition`**

在 `trail/scenes/cw/stage.py` 中做最小改动；本轮保持 `resources.py` 只读，不新增公开 `stage.blank_continue` alias，而是在 detector 内部把现有 `stage.boss_preview` 资源当作 blank-continue 候选模板：

```python
# trail/scenes/cw/stage.py
STAGE_RESOURCE_ALIASES = (
    ("stage.preparation", "preparation"),
    ...
    ...
)


def _is_layer_transition_from_ocr(items: list[Any]) -> bool:
    text = "".join(_read_ocr_piece(item).strip() for item in items)
    normalized = re.sub(r"\s+", "", text)
    return (
        "点击空白处继续" in normalized
        and "位面" in normalized
        and "本场对局首领" not in normalized
    )


def _is_true_boss_preview_from_ocr(items: list[Any]) -> bool:
    text = "".join(_read_ocr_piece(item).strip() for item in items)
    normalized = re.sub(r"\s+", "", text)
    return "本场对局首领" in normalized


def build_cw_stage_detector(runtime):
    def detect_stage() -> str | None:
        shared_image = runtime.screenshot()
        ordered_targets = [
            BatchLocateTarget(key=stage, template=str(resolve_scene_asset("cw", alias)))
            for alias, stage in STAGE_RESOURCE_ALIASES
            if stage != "boss_preview"
        ]
        locate_result = run_batch_locate(
            runtime,
            ordered_targets
            + [
                BatchLocateTarget(
                    key="blank_continue_candidate",
                    template=str(resolve_scene_asset("cw", "stage.boss_preview")),
                ),
            ],
            image=shared_image,
            trace_prefix="cw_stage_batch_locate",
        )
        candidate = locate_result.by_key.get("blank_continue_candidate")
        if candidate is not None and candidate.found:
            ocr_items = runtime.ocr_image(shared_image, config=OcrRequestConfig(lang="ch"))
            if _is_true_boss_preview_from_ocr(ocr_items):
                return "boss_preview"
            if _is_layer_transition_from_ocr(ocr_items):
                return "layer_transition"
        ...
    return detect_stage
```

要求：
- 首版实现不需要新建更复杂的图形特征检测器。
- 不要在这里顺手改 `battle.py` 或 `entry.py`。
- `layer_transition` 是新的对外 token，不是内部临时名。
- 普通 stage targets 构建时必须显式跳过旧 `boss_preview` key；`click_blank.png` 只允许经 detector-local `blank_continue_candidate` 路径进入 OCR 分流，不能在 batch locate 早期就直接返回 `boss_preview`。

- [ ] **Step 4: 跑绿灯并补一条 focused 输出回归**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-stage-green tests/test_cw_stage.py tests/test_output_rendering.py -k "layer_transition or boss_preview or stage_commands_can_show_layer_transition" -v`

Expected: PASS。

## Task 2: 修正 `entry` 链和 click-blank 使用方，不再把这类页继续当 `stage.boss_preview`

**Files:**
- Modify: `trail/scenes/cw/entry.py`
- Test: `tests/test_cw_entry.py`

- [ ] **Step 1: 先写 entry 链路的红灯测试**

在 `tests/test_cw_entry.py` 补至少 3 条失败用例，锁住 token 拆分后的使用方边界：

```python
def test_detect_current_enter_page_reports_layer_transition_not_stage_boss_preview(tmp_path, monkeypatch):
    class Runtime:
        def __init__(self):
            self.locate_calls = []
            self.wait_calls = []
            self.clicks = []
            self.matcher = SimpleNamespace(
                locate=lambda template, image: {str(_asset("stage.boss_preview")): _box("stage.boss_preview", left=440, top=480)}.get(str(template))
            )

        def screenshot(self):
            return object()

        def ocr_image(self, image, config=None):
            del image, config
            return [([0, 0], "点击空白处继续", 0.99), ([0, 0], "位面", 0.99)]

    runtime = Runtime()

    page = _detect_current_enter_page(runtime, preferred_mode="continue")

    assert page == {"page": "stage.layer_transition", "stage": "layer_transition"}


def test_start_cw_continue_mode_handles_layer_transition_with_recorded_truth(tmp_path, monkeypatch):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state.setdefault("cw", {})["entry"] = {
        "page": "invest",
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }

    class Runtime:
        def __init__(self):
            self.locate_calls = []
            self.wait_calls = []
            self.clicks = []
            self.matcher = SimpleNamespace(
                locate=lambda template, image: {str(_asset("stage.boss_preview")): _box("stage.boss_preview", left=440, top=480)}.get(str(template))
            )

        def screenshot(self):
            return object()

        def ocr_image(self, image, config=None):
            del image, config
            return [([0, 0], "点击空白处继续", 0.99), ([0, 0], "位面", 0.99)]

    runtime = Runtime()
    _install_template_runtime(
        runtime,
        wait_results={
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=440, top=480),
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=800, top=200),
        },
    )

    result = start_cw(session, mode="continue", difficulty="current", battle_mode="standard", runtime=runtime)

    assert result.scene_state["cw"]["entry"]["page"] == "invest"


def test_enter_cw_rejects_layer_transition_as_already_past_home(tmp_path, monkeypatch):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    class Runtime:
        def __init__(self):
            self.locate_calls = []
            self.wait_calls = []
            self.clicks = []
            self.keys = []

    runtime = Runtime()
    monkeypatch.setattr("trail.scenes.cw.entry.build_cw_stage_detector", lambda _runtime: lambda: "layer_transition")

    with pytest.raises(Exception) as exc_info:
        enter_cw(session, mode="continue", runtime=runtime)

    assert exc_info.value.data == {"page": "stage.layer_transition", "stage": "layer_transition"}


def test_enter_continue_game_consumes_click_blank_prompt(tmp_path, monkeypatch):
    class Runtime:
        def __init__(self):
            self.locate_calls = []
            self.wait_calls = []
            self.clicks = []

    runtime = Runtime()
    box = _box("stage.boss_preview", left=440, top=480)
    _install_template_runtime(runtime, wait_results={_asset("stage.boss_preview"): box})

    _enter_continue_game(runtime)

    assert runtime.wait_calls == [_asset("stage.boss_preview")]
    assert runtime.clicks == [box.center]
```

- [ ] **Step 2: 跑红灯测试，确认旧逻辑仍返回 `stage.boss_preview`**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-entry-red tests/test_cw_entry.py -k "layer_transition or stage_boss_preview" -v`

Expected: FAIL，至少会在 `page=stage.boss_preview` 与 `stage=layer_transition` 的差异上失败。

- [ ] **Step 3: 在 `entry.py` 中最小修正 click-blank 使用方**

按现有调用边界改 `trail/scenes/cw/entry.py`：

```python
def _consume_click_blank_prompt(runtime) -> None:
    _click_box_center(runtime, _wait(runtime, "stage.boss_preview"))


def _detect_current_enter_page(runtime, *, preferred_mode: str | None = None):
    detected_stage = build_cw_stage_detector(runtime)()
    ...
    if detected_stage == "layer_transition":
        return {"page": "stage.layer_transition", "stage": "layer_transition"}
    if detected_stage == "boss_preview":
        return {"page": "stage.boss_preview", "stage": "boss_preview"}
    ...


def _run_start_chain(...):
    ...
    if page == "stage.layer_transition":
        _consume_click_blank_prompt(runtime)
        _handle_invest_environment_flow(runtime)
        return {
            "mode": recorded_mode,
            "difficulty": recorded_difficulty,
            "battle_mode": recorded_battle_mode,
        }


def _enter_continue_game(runtime) -> None:
    _consume_click_blank_prompt(runtime)
```

要求：
- 不要顺手改 `events.py`。
- `boss_preview` 保留为真正首领预览页的既有 page token。
- `layer_transition` 与 `stage.boss_preview` 两类页要分开返回。
- `entry.py` 中至少要审并覆盖这 4 个耦合点：`_consume_click_blank_prompt()`、`_enter_continue_game()`、`_detect_current_enter_page()`、`_run_start_chain()`。
- `_detect_current_enter_page()` 必须复用 `build_cw_stage_detector(runtime)()` 的 public stage 结果，不允许在 `entry.py` 自己复制 `layer_transition` OCR 规则或重新发明 detector-local alias。

- [ ] **Step 4: 跑绿灯并确认未改坏既有 `boss_preview` 路径**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-entry-green tests/test_cw_entry.py -k "layer_transition or boss_preview" -v`

Expected: PASS。

## Task 3: 让 `battle.run` 在 `layer_transition` 上继续收口，而不是 `unknown` 暴毙

**Files:**
- Modify: `trail/scenes/cw/battle.py`
- Test: `tests/test_cw_battle_run.py`

- [ ] **Step 1: 先写 battle 层红灯测试**

在 `tests/test_cw_battle_run.py` 先扩一层最小 runtime 支架，再增加失败用例，锁住 3 件事：

```python
class LayerTransitionRuntime(ScriptedBattleRuntime):
    def _ocr_map_for_state(self) -> dict[object, list[object]]:
        mapping = super()._ocr_map_for_state()
        if self.state == "layer_transition":
            mapping[None] = [_ocr_piece("点击空白处继续"), _ocr_piece("位面")]
        return mapping

    def detect_stage(self) -> str | None:
        if self.state == "layer_transition":
            return "layer_transition"
        return super().detect_stage()

    def click_point(self, x: int, y: int):
        super().click_point(x, y)
        if self.state == "layer_transition":
            self.actions.append("blank_continue")
            self.advance()


def test_classify_cw_battle_page_treats_layer_transition_as_battle_flow_state(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: "layer_transition")

    state = battle_scene.classify_cw_battle_page(FakeRuntime(), session=build_session(tmp_path))

    assert state == "layer_transition"


def test_run_cw_battle_resumes_from_settle_into_layer_transition_then_shop(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = LayerTransitionRuntime(["settle_entry", "layer_transition", "stable_stage"])
    _patch_run_loop(monkeypatch, battle_scene, runtime)
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: runtime.detect_stage)
    monkeypatch.setattr(
        battle_scene,
        "_continue_after_settlement",
        lambda _runtime, observation=None: runtime.run_action("continue"),
    )

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=15)

    assert result["status"] == "completed"
    assert result["stage"] == "shop"
    assert runtime.actions == ["continue", "blank_continue"]


def test_run_cw_battle_timeout_on_layer_transition_keeps_in_progress(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = LayerTransitionRuntime(["layer_transition", "layer_transition"], sleep_advances_from=())
    _patch_run_loop(monkeypatch, battle_scene, runtime)
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: runtime.detect_stage)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["in_battle"] is False
    assert result["stage"] == "layer_transition"
```

支架要求必须在同一个 Step 里补齐，不允许把这些能力留给后续实现者猜：
- `LayerTransitionRuntime.detect_stage()` 必须在 `state == "layer_transition"` 时返回 `"layer_transition"`。
- `LayerTransitionRuntime.click_point()` 必须模拟真实 UI 点击后的状态推进，记录 `blank_continue` 并 `advance()`；否则 run-loop 测试会先被 stub 卡住。
- 若测试继续使用 `_patch_run_loop()`，只能 monkeypatch `continue` 动作，不能 monkeypatch `_advance_layer_transition()`；layer-transition 点击必须走 `battle.py` 的真实 `_advance_layer_transition()`。
- 如果 observation 命中按钮，测试中必须把 `build_cw_battle_continuer` / `build_cw_settle_continuer` monkeypatch 成 `pytest.fail(...)`，锁住“不能偷偷回退到旧 fallback helper”这条边界。

- [ ] **Step 2: 跑红灯测试，确认旧逻辑仍把这类页当 `boss_preview` 或 `unknown`**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-battle-red tests/test_cw_battle_run.py -k "layer_transition" -v`

Expected: FAIL。

- [ ] **Step 3: 在 `battle.py` 中把 `layer_transition` 纳入 battle flow**

按最小路径实现，不改 timeout/user-visible contract：

```python
def classify_cw_battle_page(...):
    ...
    if detected_stage == "layer_transition":
        return "layer_transition"
    ...


def _advance_layer_transition(runtime) -> None:
    runtime.click_point(*LAYER_TRANSITION_CONTINUE_POINT)


def run_cw_battle(...):
    ...
    if state == "layer_transition" and remaining > 0:
        _advance_layer_transition(runtime)
        continue
    ...
    if remaining <= 0:
        if state == "layer_transition":
            return {
                **summary,
                "status": "in_progress",
                "stage": "layer_transition",
                "stale": True,
                "in_battle": False,
                "timeout_seconds": timeout,
            }
        ...
```

要求：
- `layer_transition` 不能归成 `stable_stage`。
- 也不能只落到普通 `unknown` 睡眠兜底。
- `battle.run` 在该页上默认继续点空白收口。
- 如果 budget 耗尽，也保持 battle flow 的 `in_progress` 语义。
- 测试里不能只断言“最终回到 shop”；还要锁 `blank_continue` 动作确实发生。
- `layer_transition` 相关测试要显式覆盖 3 条负向保护：不是 `stable_stage`、不是 `unknown`、不是“只靠 sleep 自动推进到下一页”。
- `LAYER_TRANSITION_CONTINUE_POINT` 必须在 `battle.py` 本地显式定义，首版冻结成 `(960, 903)`；给足 budget 的测试要断言 `runtime.clicks[-1] == (960, 903)`。
- timeout 与点击推进要拆成两条测试：一条给足 budget 锁真实 `_advance_layer_transition()` 的点击推进；另一条让 timeout 落在 `layer_transition` 时只锁 `status=in_progress stage=layer_transition in_battle=0`，不要求先点击。

- [ ] **Step 4: 跑绿灯并确认 battle flow 不再在这里暴毙**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-battle-green tests/test_cw_battle_run.py -k "layer_transition" -v`

Expected: PASS。

## Task 4: 同步对外文档、surface 与结构断言

**Files:**
- Modify: `README.md`
- Modify: `docs/cw-stage-reference/README.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-hsr/references/simple-command-surface.md`
- Modify: `skills/trail-cw-prep/references/stage-boundaries.md`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_skill_structure.py`

- [ ] **Step 1: 先写文档/结构红灯测试**

至少补下面两类断言：

```python
def test_render_output_stage_commands_can_show_layer_transition():
    ...


def test_docs_and_active_surfaces_split_layer_transition_from_boss_preview() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    stage_reference = (PROJECT_ROOT / "docs" / "cw-stage-reference" / "README.md").read_text(encoding="utf-8")
    cw_entry = (PROJECT_ROOT / "skills" / "trail-cw-entry" / "SKILL.md").read_text(encoding="utf-8")
    simple_surface = (PROJECT_ROOT / "skills" / "trail-hsr" / "references" / "simple-command-surface.md").read_text(encoding="utf-8")
    stage_boundaries = (PROJECT_ROOT / "skills" / "trail-cw-prep" / "references" / "stage-boundaries.md").read_text(encoding="utf-8")

    assert "layer_transition" in readme
    assert "layer_transition" in stage_reference
    assert "layer_transition" in cw_entry
    assert "layer_transition" in simple_surface
    assert "layer_transition" in stage_boundaries
    assert "boss_preview" in stage_boundaries
    assert "trail cw battle run --session <id>" in readme
    assert "trail cw battle run --session <id>" in cw_entry
    assert "trail cw battle run --session <id>" in simple_surface
    assert "本场对局首领" in stage_reference
    assert "layer_transition" in _markdown_section(stage_reference, "08-cw-round-settle-success.jpg")
    assert "trail cw battle run --session <id>" in _markdown_section(stage_reference, "08-cw-round-settle-success.jpg")
    assert "boss_preview" in _markdown_section(stage_reference, "05-cw-boss-preview-page.jpg")
    assert "本场对局首领" in _markdown_section(stage_reference, "05-cw-boss-preview-page.jpg")
```

- [ ] **Step 2: 跑红灯测试，确认当前对外文档仍停留在旧 `boss_preview` 语义**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-docs-red tests/test_output_rendering.py tests/test_skill_structure.py -k "layer_transition or boss_preview" -v`

Expected: FAIL。

- [ ] **Step 3: 同步对外文档与 active surface**

要求：
- `docs/cw-stage-reference/README.md` 必须把当前这类页从旧 `boss_preview` 中拆出，并说明默认由 `battle.run` 继续收口。
- `README.md`、`skills/trail-cw-entry/SKILL.md`、`skills/trail-hsr/references/simple-command-surface.md` 如果直接提到该页或 battle flow，也要同步使用 `layer_transition`。
- `skills/trail-cw-prep/references/stage-boundaries.md` 必须同时保留真正 `boss_preview` 与新的 `layer_transition`，不能简单全局替换。
- `README.md`、`skills/trail-cw-entry/SKILL.md`、`skills/trail-hsr/references/simple-command-surface.md` 还要明确：`layer_transition` 仍属于 battle flow，默认继续 `trail cw battle run --session <id>`。
- `docs/cw-stage-reference/README.md` 的真正首领预览 section 仍要保留 `boss_preview` 与 `本场对局首领`，不能把真正首领预览页一锅端。
- `tests/test_output_rendering.py` 必须对 `README.md`、`docs/cw-stage-reference/README.md`、`skills/trail-cw-entry/SKILL.md`、`skills/trail-hsr/references/simple-command-surface.md` 做逐文件断言，不允许用 `cw_entry or simple_surface` 这类放松条件。

- [ ] **Step 4: 跑绿灯并确认真正 `boss_preview` 文案没有被一锅端**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-docs-green tests/test_output_rendering.py tests/test_skill_structure.py -k "layer_transition or boss_preview" -v`

Expected: PASS。

## 最终验证

- [ ] **Step 1: 跑本轮 focused 回归**

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-final-stage tests/test_cw_stage.py tests/test_output_rendering.py -k "layer_transition or boss_preview or stage_commands_can_show_layer_transition" -v`

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-final-entry-battle tests/test_cw_entry.py tests/test_cw_battle_run.py -k "layer_transition or boss_preview or timeout" -v`

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-final-docs tests/test_skill_structure.py tests/test_output_rendering.py -k "layer_transition or boss_preview" -v`

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-final-regression tests/test_cw_rpc_contracts.py::test_cw_rpc_wrapper_matrix[cw_boss_preview_confirm] tests/test_daemon_protocol.py::test_start_run_and_battle_run_timeouts_share_unified_command_policy tests/test_source_boundary.py::test_cw_stage_aliases_cover_minimum_stage_set tests/test_cw_slots.py -k "boss_preview or sell_plan" -v`

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-final-rendering tests/test_output_rendering.py -k "cw_battle_run_timeout or in_progress or clear_in_progress or layer_transition or boss_preview" -v`

Run: `uv run pytest --basetemp .pytest-tmp-layer-transition-final-portal tests/test_cw_portal.py -v`

Expected: PASS。

- [ ] **Step 2: 做一轮 live 验证前先征求用户确认**

执行 live 命令前，必须先让用户确认当前游戏画面适合测试。一次授权只允许执行一条会点击/推进游戏画面的 live 命令；拿到结果后必须先停住汇报，不自动连跑第二条。

Expected: 用户明确授权后，才进入 live。

- [ ] **Step 3: live 验证当前页族行为**

在用户确认后，优先验证两类 live 场景：

1. 当前这种“整层结束 -> 点击空白继续 -> 下一层”的页，应被 `cw.stage.detect` 识别为 `layer_transition`。
2. `battle.run --timeout 15` 续跑撞到该页时，不再 `CW_BATTLE_STATE_UNKNOWN`，而是继续点空白收口。

Expected: 每次 live 点击型命令执行后，都先停住向用户汇报当前截图和结果；除非用户再次明确授权，否则不自动继续下一次点击。

- [ ] **Step 4: 自检 diff 边界**

Run: `rtk git status --short`

Expected: 只看到本 plan 相关的 `stage.py`、`entry.py`、`battle.py`、相关 tests 与文档/reference 改动；默认不应出现 `resources.py`、timeout / renderer / daemon 契约的无关变更。若最终确需改 `resources.py`，必须先回到 plan 审查解释为什么 public alias 变更不可避免。
