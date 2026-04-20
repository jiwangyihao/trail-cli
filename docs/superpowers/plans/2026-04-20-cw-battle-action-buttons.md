# CW Battle Action Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `cw.battle.start`、`cw.battle.continue`、`cw.settle.next` 从纯固定点点击收口为“模板/OCR 优先、固定点兜底”，并把成功后 `shot path=...` 的 daemon/CLI/rendering 契约补成稳定回归测试。

**Architecture:** 场景层只在 `trail/scenes/cw/events.py` 内新增一组本地 helper，统一处理模板匹配、按钮区 OCR 与 fallback 点位，不把识别逻辑散到 daemon 层。截图链路继续复用现有 `cw` mutation auto-capture 与 `_render_cw_stage` renderer；这轮重点是补齐测试矩阵并在必要时做最小修补，而不是重做输出协议。

**Tech Stack:** Python 3.12、scene asset aliases、runtime `wait_img`/`locate`/`ocr`、daemon mutation capture、文本 renderer、pytest。

---

## File Map

- Modify: `trail/scenes/cw/events.py`
  责任：新增按钮解析 helper，冻结三条命令的模板/OCR/fallback 顺序与 OCR 区域/词面白名单。
- Modify: `trail/scenes/cw/resources.py`
  责任：为新模板补 `action.battle_start`、`action.battle_continue`、`action.settle_next_page` alias，并继续复用 `stage.settle`。
- Create: `trail/scenes/cw/assets/battle.png`
  责任：本项目自管的 `cw.battle.start` 模板图。
- Create: `trail/scenes/cw/assets/continue.png`
  责任：本项目自管的 `cw.battle.continue` 模板图。
- Create: `trail/scenes/cw/assets/next_page.png`
  责任：本项目自管的 `cw.settle.next` follow-up 模板图。
- Modify: `trail/scenes/cw/assets/README.md`
  责任：把新增模板纳入版本化清单，并记录来源。
- Modify: `tests/test_cw_events.py`
  责任：锁住 start/continue/settle 的模板命中、OCR 命中、fallback 三分支。
- Modify: `tests/test_daemon_protocol.py`
  责任：锁住三条 `cw` mutation 的 request-scoped capture、非空 screenshot 与 `.trail/shots/...` 归一化。
- Modify: `tests/test_cw_rpc_contracts.py`
  责任：锁住 `cw.battle.start` / `cw.battle.continue` / `cw.settle.next` 的 CLI `shot path=...`。
- Modify: `tests/test_output_rendering.py`
  责任：锁住这三条命令继续走 `_render_cw_stage`，且 `shot path=...` 紧跟首行。
- Modify if red: `trail/daemon/cw_service.py`
  责任：只有在新 capture 测试暴露缺口时，做最小 request-scoped auto-capture 修补。
- Modify if red: `trail/output/rendering.py`
  责任：只有在 renderer 回归测试暴露缺口时，做最小 stage-family 映射修补。

## Baseline Note

- Worktree 固定为 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-battle-action-buttons`。
- Spec 固定为 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-battle-action-buttons\docs\superpowers\specs\2026-04-20-cw-battle-action-buttons-design.md`。
- 当前 worktree 已执行 `python -m pip install -e .`；`rtk pytest ...` 在这个 worktree 上返回 `Pytest: No tests collected`，不要把它当成可信基线。执行阶段统一使用 `uv run pytest --basetemp ...`。
- `README.md` 与 `skills/trail-cw-events/SKILL.md` 默认不改；如果你发现自己要动这两个文件，先回看 spec 的“文档同步”边界，确认不是在无意扩面。
- 本轮不扩面修 action mutation 的真实 `stage.value` 生成链；daemon integration 以 `stale` + `screenshot` 为准，CLI / rendering 的 fake payload 继续只承担 renderer 形状回归。
- 未经用户明确要求，不创建 git commit。

### Task 1: 收口场景层按钮 helper 与本地模板资源

**Files:**
- Modify: `trail/scenes/cw/events.py`
- Modify: `trail/scenes/cw/resources.py`
- Create: `trail/scenes/cw/assets/battle.png`
- Create: `trail/scenes/cw/assets/continue.png`
- Create: `trail/scenes/cw/assets/next_page.png`
- Modify: `trail/scenes/cw/assets/README.md`
- Modify: `tests/test_cw_events.py`

- [ ] **Step 1: 先写失败测试，锁住 start 的模板优先路径**

```python
def _ocr_piece(text: str, *, left: int = 400, top: int = 500, width: int = 120, height: int = 36) -> dict:
    return {
        "text": text,
        "box": {"left": left, "top": top, "width": width, "height": height},
    }


def test_build_cw_battle_starter_waits_for_template_before_clicking_center(monkeypatch, fake_runtime):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias)
    fake_runtime.wait_result = {"left": 100, "top": 200, "width": 60, "height": 20}

    events_module.build_cw_battle_starter(fake_runtime)()

    assert fake_runtime.wait_calls == ["action.battle_start"]
    assert fake_runtime.locate_calls == []
    assert fake_runtime.clicks == [(130, 210)]
```

- [ ] **Step 2: 先写失败测试，锁住 continue/settle 的 OCR 分支与 fallback 分支**

```python
@pytest.mark.parametrize(
    ("builder_name", "expected_wait_calls", "expected_locate_calls", "ocr_text", "expected_click"),
    [
        (
            "build_cw_battle_starter",
            ["action.battle_start"],
            [],
            "开始挑战",
            (460, 518),
        ),
        (
            "build_cw_battle_continuer",
            [],
            ["action.battle_continue"],
            "继续挑战",
            (460, 518),
        ),
        (
            "build_cw_settle_continuer",
            [],
            ["stage.settle", "action.settle_next_page"],
            "下一页",
            (460, 518),
        ),
    ],
)
def test_cw_action_buttons_use_ocr_box_when_template_misses(
    monkeypatch,
    fake_runtime,
    builder_name,
    expected_wait_calls,
    expected_locate_calls,
    ocr_text,
    expected_click,
):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias)
    ocr_calls: list[dict] = []

    def fake_ocr(**kwargs):
        ocr_calls.append(kwargs)
        return [_ocr_piece(ocr_text)]

    fake_runtime.wait_result = None
    fake_runtime.locate_result = None
    monkeypatch.setattr(fake_runtime, "ocr", fake_ocr)

    getattr(events_module, builder_name)(fake_runtime)()

    assert fake_runtime.wait_calls == expected_wait_calls
    assert fake_runtime.locate_calls == expected_locate_calls
    assert ocr_calls == [{"capture": events_module.CW_ACTION_OCR_REGION}]
    assert fake_runtime.clicks == [expected_click]


@pytest.mark.parametrize(
    ("builder_name", "point_name"),
    [
        ("build_cw_battle_starter", "BATTLE_START_POINT"),
        ("build_cw_battle_continuer", "BATTLE_CONTINUE_POINT"),
        ("build_cw_settle_continuer", "SETTLE_NEXT_POINT"),
    ],
)
def test_cw_action_buttons_fall_back_to_existing_points_when_template_and_ocr_miss(
    monkeypatch,
    fake_runtime,
    builder_name,
    point_name,
):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias)
    fake_runtime.wait_result = None
    fake_runtime.locate_result = None
    fake_runtime.ocr_result = []

    getattr(events_module, builder_name)(fake_runtime)()

    assert fake_runtime.clicks == [getattr(events_module, point_name)]


def test_cw_action_buttons_fall_back_to_existing_points_when_ocr_raises(monkeypatch, fake_runtime):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias)
    fake_runtime.wait_result = None
    fake_runtime.locate_result = None
    monkeypatch.setattr(fake_runtime, "ocr", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("ocr boom")))

    events_module.build_cw_battle_continuer(fake_runtime)()

    assert fake_runtime.clicks == [events_module.BATTLE_CONTINUE_POINT]


def test_cw_action_buttons_accept_tuple_ocr_piece(monkeypatch, fake_runtime):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias)
    fake_runtime.wait_result = None
    fake_runtime.locate_result = None
    fake_runtime.ocr_result = [([(400, 500), (520, 500), (520, 536), (400, 536)], "下一页", 0.99)]

    events_module.build_cw_settle_continuer(fake_runtime)()

    assert fake_runtime.clicks == [(460, 518)]
```

- [ ] **Step 3: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/cw-action-red tests/test_cw_events.py -k "battle_starter_waits_for_template or action_buttons_use_ocr_box or action_buttons_fall_back or action_buttons_fall_back_to_existing_points_when_ocr_raises or action_buttons_accept_tuple_ocr_piece" -v`
Expected: FAIL，因为 `events.py` 里这三条 builder 仍然只是裸 `click_point(...)`，还没有 `_asset()` / box center / OCR helper。

- [ ] **Step 4: 在 `events.py` 实现模板/OCR/fallback helper，并把三条 builder 接到统一路径**

```python
from collections.abc import Callable, Mapping

from trail.runtime.resources import resolve_scene_asset

CW_ACTION_OCR_REGION = {
    "from_x": 0.30,
    "from_y": 0.72,
    "to_x": 0.70,
    "to_y": 0.92,
}


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box_center(box: object) -> tuple[int, int]:
    if hasattr(box, "center"):
        center = getattr(box, "center")
        if isinstance(center, tuple) and len(center) == 2:
            return int(center[0]), int(center[1])
    if isinstance(box, Mapping):
        return (
            int(box["left"]) + int(box["width"]) // 2,
            int(box["top"]) + int(box["height"]) // 2,
        )
    raise TrailError("CW_ACTION_BOX_INVALID", "currency wars action returned invalid box")


def _normalized_action_text(text: object) -> str:
    return "".join(str(text or "").split())


def _extract_box_from_mapping(piece: Mapping[object, object]) -> tuple[float, float, float, float] | None:
    box = piece.get("box")
    if isinstance(box, Mapping):
        return _extract_box_values(box)
    polygon = piece.get("polygon") or piece.get("points")
    if polygon is not None:
        return _extract_box_from_polygon(polygon)
    center = piece.get("center")
    if isinstance(center, Mapping):
        return float(center["x"]), float(center["y"]), 0.0, 0.0
    if isinstance(center, (list, tuple)) and len(center) == 2:
        return float(center[0]), float(center[1]), 0.0, 0.0
    return _extract_box_values(piece)


def _extract_box_values(box: Mapping[object, object]) -> tuple[float, float, float, float] | None:
    try:
        return float(box["left"]), float(box["top"]), float(box["width"]), float(box["height"])
    except (KeyError, TypeError, ValueError):
        return None


def _extract_box_from_polygon(polygon: object) -> tuple[float, float, float, float] | None:
    if not isinstance(polygon, (list, tuple)):
        return None
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (IndexError, TypeError, ValueError):
        return None
    if not xs or not ys:
        return None
    left = min(xs)
    top = min(ys)
    return left, top, max(xs) - left, max(ys) - top


def _normalize_ocr_piece(piece: object) -> dict[str, float | str] | None:
    if isinstance(piece, Mapping):
        text = str(piece.get("text") or piece.get("ocr_text") or "").strip()
        box = _extract_box_from_mapping(piece)
    elif isinstance(piece, (list, tuple)) and len(piece) >= 2:
        text = str(piece[1] or "").strip()
        box = _extract_box_from_polygon(piece[0])
    else:
        return None
    if not text or box is None:
        return None
    left, top, width, height = box
    return {"text": text, "left": left, "top": top, "width": width, "height": height}


def _find_ocr_button_box(runtime, *, allowed_texts: set[str]):
    try:
        pieces = runtime.ocr(capture=CW_ACTION_OCR_REGION)
    except Exception:
        return None
    for piece in pieces or []:
        normalized = _normalize_ocr_piece(piece)
        if normalized is None:
            continue
        if _normalized_action_text(normalized["text"]) not in allowed_texts:
            continue
        return normalized
    return None


def _click_cw_action_button(
    runtime,
    *,
    wait_alias: str | None,
    locate_aliases: tuple[str, ...],
    allowed_texts: set[str],
    fallback_point: tuple[int, int],
    wait_timeout: int = 3,
):
    if wait_alias is not None:
        box = runtime.wait_img(_asset(wait_alias), timeout=wait_timeout, interval=0.5)
        if box is not None:
            runtime.click_point(*_box_center(box))
            return
    for alias in locate_aliases:
        box = runtime.locate(_asset(alias))
        if box is not None:
            runtime.click_point(*_box_center(box))
            return
    box = _find_ocr_button_box(runtime, allowed_texts=allowed_texts)
    if box is not None:
        runtime.click_point(*_box_center(box))
        return
    runtime.click_point(*fallback_point)


def build_cw_battle_starter(runtime) -> SceneAction:
    return lambda: _click_cw_action_button(
        runtime,
        wait_alias="action.battle_start",
        locate_aliases=(),
        allowed_texts={"开始战斗", "开始挑战"},
        fallback_point=BATTLE_START_POINT,
    )


def build_cw_battle_continuer(runtime) -> SceneAction:
    return lambda: _click_cw_action_button(
        runtime,
        wait_alias=None,
        locate_aliases=("action.battle_continue",),
        allowed_texts={"继续挑战", "继续"},
        fallback_point=BATTLE_CONTINUE_POINT,
    )


def build_cw_settle_continuer(runtime) -> SceneAction:
    return lambda: _click_cw_action_button(
        runtime,
        wait_alias=None,
        locate_aliases=("stage.settle", "action.settle_next_page"),
        allowed_texts={"下一步", "下一页"},
        fallback_point=SETTLE_NEXT_POINT,
    )
```

- [ ] **Step 5: 补 alias、拷贝模板图，并更新 assets README**

```python
# trail/scenes/cw/resources.py
CW_RESOURCE_ALIASES = {
    # ...existing aliases...
    "action.battle_start": "battle.png",
    "action.battle_continue": "continue.png",
    "action.settle_next_page": "next_page.png",
}
```

```powershell
Copy-Item "C:\Users\34404\source\repos\StarRailAssistant\resources\img\currency_wars\battle.png" "trail/scenes/cw/assets/battle.png"
Copy-Item "C:\Users\34404\source\repos\StarRailAssistant\resources\img\currency_wars\continue.png" "trail/scenes/cw/assets/continue.png"
Copy-Item "C:\Users\34404\source\repos\StarRailAssistant\resources\img\currency_wars\next_page.png" "trail/scenes/cw/assets/next_page.png"
```

```markdown
# Currency Wars Assets

此目录存放 `trail-cli` 自己版本化管理的货币战争模板图。

第一阶段至少迁移：
- `battle.png`
- `continue.png`
- `fold.png`
- `replenish_stage.png`
- `click_blank.png`
- `select_invest_strategy.png`
- `encounter_node.png`
- `fortune_teller.png`
- `collect.png`
- `next_step.png`
- `next_page.png`
- `start_currency_wars.png`
```

- [ ] **Step 6: 跑绿灯，顺带确认 alias 都能 resolve 到本地模板**

Run: `uv run pytest --basetemp .trail/pytest-tmp/cw-action-green tests/test_cw_events.py tests/test_source_boundary.py -v`
Expected: PASS，新的 action alias 都能 resolve 到 package 内本地模板，`start` / `continue` / `settle` 的模板/OCR/fallback 分支都被锁住。

### Task 2: 锁住 daemon mutation 的 request-scoped capture 契约

**Files:**
- Modify: `tests/test_daemon_protocol.py`
- Modify if red: `trail/daemon/cw_service.py`
- Modify if red: `trail/daemon/command_service.py`

- [ ] **Step 1: 先写 capture 契约测试，覆盖 start/continue/settle 三条 mutation**

```python
@pytest.mark.parametrize(
    ("method", "factory_name", "action_name", "request_id"),
    [
        ("cw.battle.start", "battle_starter_factory", "start_cw_battle", "req-cw-battle-start"),
        ("cw.battle.continue", "battle_continuer_factory", "continue_cw_battle", "req-cw-battle-continue"),
        ("cw.settle.next", "settle_continuer_factory", "settle_cw_next", "req-cw-settle-next"),
    ],
)
def test_command_service_cw_stage_mutations_use_request_scoped_capture(
    tmp_path: Path,
    monkeypatch,
    method: str,
    factory_name: str,
    action_name: str,
    request_id: str,
):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

    def apply_stage(session, **kwargs):
        del kwargs
        session.scene_state.setdefault("cw", {})["stage"] = {"stale": True}
        session.last_stage = None
        return session

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(f"trail.daemon.cw_service.{factory_name}", lambda runtime: object())
    monkeypatch.setattr(f"trail.daemon.cw_service.{action_name}", apply_stage)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id=request_id,
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method=method,
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"] == {"stale": True}
    assert payload["screenshot"] == f".trail/shots/{request_id}.png"
    assert runtime.capture_requests == [(False, request_id)]
```

- [ ] **Step 2: 跑红灯或现状灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/cw-daemon-capture tests/test_daemon_protocol.py -k "cw_stage_mutations_use_request_scoped_capture" -v`
Expected: 理想情况是 PASS，说明 production 链路已经具备 request-scoped capture，只是之前没被测试锁住；如果 FAIL，失败点应当集中在 `screenshot` 为空、路径未归一化、`request_id` 未下传，或集成层没有保留 `stale` 更新。

- [ ] **Step 3: 只有在 Step 2 变红时，才做最小 capture 修补**

```python
# trail/daemon/cw_service.py
extra_delay_seconds = PORTAL_SELECT_EXTRA_CAPTURE_DELAY_SECONDS if method == "cw.portal.select" else 0.0
capture_runtime = _RequestScopedCaptureRuntime(runtime(), request_id, extra_delay_seconds=extra_delay_seconds)
return with_auto_capture(capture_runtime, lambda: result, verbose=verbose)

# trail/daemon/command_service.py
def _normalize_capture_payload(response: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    normalized = deepcopy(response)
    normalized["screenshot"] = _normalize_daemon_screenshot_path(
        normalized.get("screenshot"),
        workspace_root=workspace_root,
    )
    return _bind_references_to_screenshot(normalized)
```

实现要求：

- 不新增这三条命令的专用 capture 分支。
- 不改 `optional=False` 语义。
- 不把 screenshot 路径从 workspace-relative `.trail/shots/...` 改成绝对路径。
- `request_id` 下传问题只修 `trail/daemon/cw_service.py`；路径归一化问题只修 `trail/daemon/command_service.py`。

- [ ] **Step 4: 重新跑 Task 2 测试，确认 screenshot 非空且 request-scoped**

Run: `uv run pytest --basetemp .trail/pytest-tmp/cw-daemon-capture-green tests/test_daemon_protocol.py -k "cw_stage_mutations_use_request_scoped_capture" -v`
Expected: PASS，三条 mutation 都返回非空 `screenshot`，并且 `capture_after_action` 记录 `[(False, <request_id>)]`。

### Task 3: 锁住 CLI / renderer 的 `shot path=...` 协议并做最终验证

**Files:**
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_output_rendering.py`
- Modify if red: `trail/output/rendering.py`

- [ ] **Step 1: 先写失败测试，补齐 `cw.battle.start` / `cw.settle.next` 的 CLI `shot` 契约，并保持 `cw.battle.continue` 显式覆盖**

```python
(
    ["cw", "battle", "start", "--session", SESSION_ID],
    "cw.battle.start",
    {},
    {"value": "battle", "stale": False},
    ".trail/shots/req-cw-battle-start.png",
    _expected_lines(
        "ok cw.battle.start stage=battle stale=0",
        screenshot=".trail/shots/req-cw-battle-start.png",
    ),
),
(
    ["cw", "settle", "next", "--session", SESSION_ID],
    "cw.settle.next",
    {},
    {"value": "shop", "stale": False},
    ".trail/shots/req-cw-settle-next.png",
    _expected_lines(
        "ok cw.settle.next stage=shop stale=0",
        screenshot=".trail/shots/req-cw-settle-next.png",
    ),
),
```

```python
@pytest.mark.parametrize(
    "command",
    ["cw.battle.start", "cw.battle.continue", "cw.settle.next"],
)
def test_render_output_cw_action_stage_commands_keep_stage_renderer(command: str):
    payload = _stage_payload()

    assert render_output(command, payload).splitlines() == [
        f"ok {command} stage=shop stale=0",
        "shot path=.trail/shots/req-stage.png",
    ]


@pytest.mark.parametrize(
    "command",
    ["cw.battle.start", "cw.battle.continue", "cw.settle.next"],
)
def test_render_output_cw_action_stage_commands_reject_yaml(command: str):
    payload = _stage_payload()

    assert render_output(command, payload, output_format="yaml").splitlines() == [
        f"fail {command} code=OUTPUT_FORMAT_NOT_SUPPORTED",
        f'why msg="yaml not supported for {command}"',
    ]


@pytest.mark.parametrize(
    "command",
    ["cw.battle.start", "cw.battle.continue", "cw.settle.next"],
)
def test_render_output_cw_action_stage_commands_preserve_stale_only_success(command: str):
    payload = {
        "ok": True,
        "data": {"stale": True},
        "screenshot": ".trail/shots/req-stage-stale.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output(command, payload).splitlines() == [
        f"ok {command} stale=1",
        "shot path=.trail/shots/req-stage-stale.png",
    ]
```

- [ ] **Step 2: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/cw-output-red tests/test_cw_rpc_contracts.py tests/test_output_rendering.py -k "cw_battle_continue or cw_action_stage_commands or cw_rpc_wrapper_matrix" -v`
Expected: FAIL，因为 `tests/test_cw_rpc_contracts.py` 里 `cw.battle.start` / `cw.settle.next` 仍然是 `screenshot=None`，renderer 回归测试也还没新增。

- [ ] **Step 3: 如果 renderer 回归暴露映射缺口，只做最小 stage-family 修补**

```python
# trail/output/rendering.py
TEXT_RENDERERS = {
    # ...existing mappings...
    "cw.battle.start": _render_cw_stage,
    "cw.battle.continue": _render_cw_stage,
    "cw.settle.next": _render_cw_stage,
}
```

要求：

- 成功首行继续沿用 `_render_cw_stage` 摘要：`stale=<0|1>` 必须保留；如果 payload 带 `value`，则继续输出 `stage=<value>`。
- `shot path=...` 继续由 `_append_shot(...)` 作为第二行输出。
- 不新增 renderer 家族、prefix 或 YAML allowlist。

- [ ] **Step 4: 跑最终目标套件**

Run: `uv run pytest --basetemp .trail/pytest-tmp/cw-buttons-green tests/test_source_boundary.py tests/test_cw_events.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py -v`
Expected: PASS，按钮资源、场景点击策略、request-scoped capture、CLI `shot path=...`、renderer 顺序全部闭环。

- [ ] **Step 5: 做收尾核对，确认没有无意扩面**

Run: `git status --short -- trail/scenes/cw/events.py trail/scenes/cw/resources.py trail/scenes/cw/assets/README.md trail/scenes/cw/assets/battle.png trail/scenes/cw/assets/continue.png trail/scenes/cw/assets/next_page.png trail/daemon/cw_service.py trail/daemon/command_service.py trail/output/rendering.py tests/test_cw_events.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py && git diff -- trail/scenes/cw/events.py trail/scenes/cw/resources.py trail/scenes/cw/assets/README.md trail/daemon/cw_service.py trail/daemon/command_service.py trail/output/rendering.py tests/test_cw_events.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py`
Expected: status/diff 只落在 allowlist；三张新模板能在 `git status --short` 里出现，`README.md` 与 `skills/trail-cw-events/SKILL.md` 保持不变。
