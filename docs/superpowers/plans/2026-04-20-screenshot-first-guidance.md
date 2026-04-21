> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。

# Screenshot-First Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为所有带截图的 success 结果补上稳定的 `info read_image_first=1` 提示和顶层 `image_guidance.read_image_first` 元数据，并给 7 条 `cw` 读命令补齐自动截图，同时清理 `cw.shop.status` 的旧带图契约。

**Architecture:** 公共 envelope / capture 层只负责生成 `image_guidance` 顶层元数据；`trail/output/rendering.py` 只在 success 文本路径把它渲染为 `shot path=...` 后的 `info read_image_first=1`，failure 路径保持不变。`CommandService` 新增一组 `cw` read-capture 路由，把 7 条命令对齐到 `cw.slots.read` 的 request-scoped selective-capture 语义；README、AGENTS、skills 与活参考文档同步跟进。

**Tech Stack:** Python 3.12、Typer CLI、daemon `CommandService` / `CwService`、`trail.output.envelope` / `capture` / `rendering`、pytest、README / AGENTS / `skills/*/SKILL.md`。

---

## File Map

- Modify: `trail/output/envelope.py`
  责任：新增 `image_guidance.read_image_first` 的公共 payload helper，并让 `command_success(...)` / `command_failure(...)` 共享它。
- Modify: `trail/daemon/command_service.py`
  责任：让 daemon `success(...)` payload 与 envelope helper 词面一致；新增 `CW_CAPTURED_READ_METHODS`，把 7 条 `cw` 读命令路由到 `_run_cw_with_capture(...)`。
- Modify: `trail/output/rendering.py`
  责任：新增 success-only guidance helper；统一 `shot` 后 guidance 行顺序；修 `cw.slots.read` 的插入位置；确保 failure 路径与 YAML / verbose 语义不扩面。
- Modify: `README.md`
  责任：更新输出约定与示例，把带截图 success 的稳定 guidance 写清；去掉 `cw.shop.status` 旧带图示例。
- Modify: `AGENTS.md`
  责任：冻结 `info read_image_first=1`、`image_guidance.read_image_first`、success 行顺序、YAML / verbose 非目标。
- Modify: `skills/trail-hsr/SKILL.md`
  责任：把 simple-first 场景里的“先看图”升级成看到 `shot` + `info read_image_first=1` 时的硬规则。
- Modify: `skills/trail-hsr-advanced/SKILL.md`
  责任：让 advanced 场景也遵守同一张图优先规则。
- Modify: `skills/trail-cw/SKILL.md`
  责任：主循环强制以原始图为第一事实来源。
- Modify: `skills/trail-cw-events/SKILL.md`
  责任：事件 / 战斗 / 结算场景使用新 guidance 词面。
- Modify: `skills/trail-cw-slots/SKILL.md`
  责任：槽位读图规则与 `cw.slots.read` guidance 对齐。
- Modify: `skills/trail-cw-shop/SKILL.md`
  责任：商店相关 guidance 对齐，并消除 `cw.shop.status` 旧带图预期。
- Modify: `skills/trail-cw-replenish/SKILL.md`
  责任：补给 / invest / encounter / fortune 子流程对齐新 guidance。
- Modify: `skills/trail-cw-guide/SKILL.md`
  责任：攻略子流程在看到 guidance 时必须先读图。
- Modify: `docs/superpowers/specs/2026-04-17-trail-output-format-design.md`
  责任：把旧 `shot` 后直接接实体行的示例标注为被新 spec 覆盖，或同步改示例。
- Modify: `docs/superpowers/specs/2026-04-20-cw-battle-run-design.md`
  责任：清理旧顺序说明，避免和新 guidance 冲突。
- Modify: `docs/superpowers/specs/2026-04-20-cw-battle-action-buttons-design.md`
  责任：清理旧的“`shot` 固定第二行且后面直接接实体行”描述。
- Modify: `tests/test_output_envelope.py`
  责任：锁住 payload-level `image_guidance` 出现性。
- Modify: `tests/test_output_rendering.py`
  责任：锁住 success guidance 行、failure 不扩面、`cw.slots.read` 插入顺序、`cw.shop.status` negative regression、README / skill 文案断言。
- Modify: `tests/test_daemon_protocol.py`
  责任：锁住 `CommandService -> CwService.handle_with_capture` 的 7 条真实 read-capture 路由。
- Modify: `tests/test_cw_rpc_contracts.py`
  责任：锁住 CLI wrapper 的 `shot` + `info` 输出；删除 `cw.shop.status` 旧带图预期；给 `encounter.read` / `fortune.read` 补截图期望。

## Baseline Note

- Worktree 固定为 `C:\Users\34404\source\repos\trail-cli\.worktrees\spec-screenshot-first-guidance`。
- 已批准的 spec 当前位于主工作区：`C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-20-screenshot-first-guidance-design.md`。因为未创建 commit，执行阶段统一从这个绝对路径读取 spec。
- 该 worktree 已执行 `uv sync --all-groups`。
- `uv run pytest` 在未改代码前已存在大量 `PermissionError` / daemon-runtime 相关基线失败，不把全量 suite 当作可信 baseline。本轮只把计划中列出的定向 pytest 命令当作验收门槛。
- 未经用户明确要求，不创建 git commit。

### Task 1: 锁住 envelope / payload 的 `image_guidance` 契约

**Files:**
- Modify: `trail/output/envelope.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_output_envelope.py`

- [ ] **Step 1: 先补 payload-level 红灯测试，锁住有图/无图 success/failure 的 guidance 出现性**

```python
from trail.daemon.command_service import success as daemon_success


def test_command_success_includes_image_guidance_when_screenshot_present(tmp_path):
    result = command_success(data={"done": True}, screenshot=tmp_path / "ok.png")

    assert result["image_guidance"] == {"read_image_first": True}


def test_command_failure_includes_image_guidance_when_screenshot_present(tmp_path):
    result = command_failure(code="WINDOW_NOT_FOUND", message="window missing", screenshot=tmp_path / "fail.png")

    assert result["image_guidance"] == {"read_image_first": True}


def test_command_success_omits_image_guidance_without_screenshot():
    result = command_success(data={"done": True}, screenshot=None)

    assert "image_guidance" not in result


def test_daemon_success_matches_envelope_guidance_shape(tmp_path):
    result = daemon_success({"done": True}, request_id="req-guidance", screenshot=str(tmp_path / "daemon.png"))

    assert result["image_guidance"] == {"read_image_first": True}
```

- [ ] **Step 2: 跑红灯，确认当前 payload 还没有 guidance 字段**

Run: `uv run pytest tests/test_output_envelope.py -k "image_guidance or daemon_success_matches_envelope_guidance_shape" -v`
Expected: FAIL，现有 `command_success(...)` / `command_failure(...)` / daemon `success(...)` 都不会返回 `image_guidance`。

- [ ] **Step 3: 在公共 envelope helper 收口 guidance 生成，并让 daemon `success(...)` 复用同一词面**

```python
# trail/output/envelope.py
def build_image_guidance(screenshot: Path | str | None) -> dict[str, bool] | None:
    if screenshot is None:
        return None
    return {"read_image_first": True}


def _attach_image_guidance(payload: dict[str, Any], *, screenshot: Path | str | None) -> dict[str, Any]:
    guidance = build_image_guidance(screenshot)
    if guidance is None:
        return payload
    return {**payload, "image_guidance": guidance}


def command_success(
    *,
    data: dict[str, Any],
    screenshot: Path | None,
    timing: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _attach_image_guidance(
        {
            "ok": True,
            "data": deepcopy(data),
            "screenshot": None if screenshot is None else str(screenshot),
            "timing": deepcopy(timing or {}),
            "warnings": deepcopy(warnings or []),
            "references": deepcopy(references or []),
            "debug": deepcopy(debug),
            "error": None,
        },
        screenshot=screenshot,
    )


def command_failure(
    *,
    code: str,
    message: str,
    screenshot: Path | None,
    timing: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _attach_image_guidance(
        {
            "ok": False,
            "data": {},
            "screenshot": None if screenshot is None else str(screenshot),
            "timing": deepcopy(timing or {}),
            "warnings": deepcopy(warnings or []),
            "references": deepcopy(references or []),
            "debug": deepcopy(debug),
            "error": {"code": code, "message": message},
        },
        screenshot=screenshot,
    )
```

```python
# trail/daemon/command_service.py
from trail.output.envelope import build_image_guidance


def success(
    data: dict[str, Any],
    *,
    request_id: str | None = None,
    screenshot: str | None = None,
    references: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "request_id": request_id,
        "ok": True,
        "data": deepcopy(data),
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": deepcopy(references or []),
        "debug": deepcopy(debug),
        "error": None,
    }
    guidance = build_image_guidance(screenshot)
    if guidance is not None:
        payload["image_guidance"] = guidance
    return _bind_references_to_screenshot(payload)
```

- [ ] **Step 4: 重跑 payload 契约测试，确认词面收口**

Run: `uv run pytest tests/test_output_envelope.py -k "image_guidance or daemon_success_matches_envelope_guidance_shape" -v`
Expected: PASS，且 `image_guidance` 只在 screenshot 存在时出现。

### Task 2: 锁住 success renderer guidance 与 `cw.shop.status` / `cw.slots.read` 回归

**Files:**
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先补 renderer 红灯测试，覆盖 success guidance、failure 不扩面、slots 顺序与 `cw.shop.status` negative regression**

```python
def test_render_output_adds_read_image_first_after_shot_for_success():
    payload = {
        "ok": True,
        "data": {"value": "shop", "stale": False},
        "screenshot": ".trail/shots/req-stage.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.stage.detect", payload).splitlines() == [
        "ok cw.stage.detect stage=shop stale=0",
        "shot path=.trail/shots/req-stage.png",
        "info read_image_first=1",
    ]


def test_render_output_failure_does_not_render_read_image_first_line():
    payload = {
        "request_id": "req-fail",
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-fail.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": {"code": "WINDOW_NOT_FOUND", "message": "window missing"},
    }

    assert "info read_image_first=1" not in render_output("ocr.read", payload)
```

```python
def test_render_output_renders_cw_slots_summary_text():
    payload = {
        "ok": True,
        "data": {"front": [{"name": "希儿", "star": 4}, None], "back": [{"name": "佩拉", "rarity": 2}], "hand": [{"name": "停云", "cost": 2, "is_carry": True}, None], "stale": True},
        "screenshot": ".trail/shots/req-slots.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.read", payload).splitlines() == [
        "ok cw.slots.read front=1 back=1 hand=1 stale=1",
        "shot path=.trail/shots/req-slots.png",
        "info read_image_first=1",
        "slot pos=front:0 name=希儿 star=4",
        "slot pos=front:1 empty=1",
        "slot pos=back:0 name=佩拉 rarity=2",
        "slot pos=hand:0 name=停云 carry=1 cost=2",
        "slot pos=hand:1 empty=1",
    ]


def test_render_output_renders_cw_shop_status_without_shot_or_guidance():
    payload = {
        "ok": True,
        "data": {"items": [{"slot": 1, "name": "希儿", "price": 2}]},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.status", payload).splitlines() == [
        "ok cw.shop.status count=1",
        "item idx=1 slot=1 name=希儿 cost=2",
    ]
```

- [ ] **Step 2: 跑 renderer 红灯**

Run: `uv run pytest tests/test_output_rendering.py -k "read_image_first or cw_slots_summary_text or cw_shop_status" -v`
Expected: FAIL，因为当前 success path 没有 guidance，`cw.slots.read` 也没有额外插槽位置逻辑，`cw.shop.status` 旧断言仍带 shot。

- [ ] **Step 3: 在 success-only 路径引入 guidance helper，并修 `cw.slots.read` 的插入索引**

```python
def _has_success_image_guidance(payload: dict[str, Any]) -> bool:
    guidance = payload.get("image_guidance")
    return bool(payload.get("ok")) and bool(payload.get("screenshot")) and isinstance(guidance, dict) and guidance.get("read_image_first") is True


def _append_success_image_guidance(lines: list[str], payload: dict[str, Any]) -> None:
    if _has_success_image_guidance(payload):
        lines.append("info read_image_first=1")


def _append_success_capture_block(lines: list[str], payload: dict[str, Any]) -> None:
    _append_shot(lines, payload)
    _append_success_image_guidance(lines, payload)


def _append_common_success_lines(lines: list[str], payload: dict[str, Any]) -> list[str]:
    _append_success_capture_block(lines, payload)
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines
```

```python
def _render_cw_slots_read(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    lines = _render_cw_slots(command, payload)
    insert_at = 1
    if payload.get("screenshot"):
        insert_at += 1
    if _has_success_image_guidance(payload):
        insert_at += 1
    slot_lines: list[str] = []
    _append_cw_slot_lines(slot_lines, data)
    lines[insert_at:insert_at] = slot_lines
    return lines
```

```python
# 把 success renderer 里裸 `_append_shot(lines, payload)` 统一替换成 `_append_success_capture_block(lines, payload)`
# 这轮至少覆盖：_render_cw_entry、_render_cw_portal_cards、_render_cw_guide_current、
# _render_cw_option_list、_render_cw_shop_status、_render_cw_shop_scan、_render_ocr_read。
```

- [ ] **Step 4: 重跑 renderer 测试，确认 success/failure 边界与排序稳定**

Run: `uv run pytest tests/test_output_rendering.py -k "read_image_first or cw_slots_summary_text or cw_shop_status" -v`
Expected: PASS，且 failure 输出仍不含 guidance。

### Task 3: 给 7 条 `cw` 读命令补 read-capture 路由，并更新 CLI wrapper 预期

**Files:**
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 先写 route-level 红灯测试，锁住 7 条命令都走 `_run_cw_with_capture(...)`，且不是 mutation journal**

```python
@pytest.mark.parametrize(
    ("method", "payload", "response_data"),
    [
        ("cw.stage.detect", {}, {"value": "shop", "stale": False}),
        ("cw.stage.wait", {"timeout": 120}, {"value": "settle", "stale": False}),
        ("cw.shop.scan", {}, {"items": [{"slot": 1, "name": "银狼", "price": 20}], "opened": True, "stale": False}),
        ("cw.replenish.read", {}, {"options": [1, 2, 3]}),
        ("cw.invest.read", {}, {"options": [1, 2]}),
        ("cw.encounter.read", {}, {"options": [1, 2]}),
        ("cw.fortune.read", {}, {"options": [1, 2]}),
    ],
)
def test_command_service_routes_cw_captured_reads_through_handle_with_capture(tmp_path, monkeypatch, method, payload, response_data):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            assert optional is False
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(cw_service, "handle_with_capture", lambda **kwargs: {"ok": True, "data": response_data, "screenshot": tmp_path / ".trail" / "shots" / f"{kwargs['request_id']}.png", "timing": {}, "warnings": [], "references": [], "debug": None, "error": None})
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    request = DaemonRequest(
        request_id=f"req-{method}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method=method,
        payload={"session_id": session.session_id, **payload},
    )

    response = command_service.handle(request)

    assert response["screenshot"] == f".trail/shots/{request.request_id}.png"
    with pytest.raises(TrailError) as exc_info:
        service.request_status(request.request_id)
    assert exc_info.value.code == "REQUEST_NOT_FOUND"


def test_command_service_handles_cw_stage_detect_with_request_scoped_capture(tmp_path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            assert optional is False
            return tmp_path / ".trail" / "shots" / f"{request_id}.png"

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.detect_cw_stage",
        lambda session, detector: _set_stage(session, {"value": "preparation", "stale": False}),
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    request = DaemonRequest(
        request_id="req-cw-stage-detect",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.stage.detect",
        payload={"session_id": session.session_id},
    )

    response = command_service.handle(request)

    assert response["screenshot"] == ".trail/shots/req-cw-stage-detect.png"
```

- [ ] **Step 2: 先写 CLI wrapper 红灯，更新 screenshot helper 并修正 `cw.shop.status` / `encounter.read` / `fortune.read` 期望**

```python
def _expected_lines(summary: str, *, screenshot: str | None = None, body: list[str] | None = None) -> list[str]:
    lines = [summary]
    if screenshot:
        lines.extend([f"shot path={screenshot}", "info read_image_first=1"])
    if body:
        lines.extend(body)
    return lines
```

```python
def test_cw_shop_status_renders_items_in_slot_order(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {"cw.shop.status": build_success_response(request_id="req-cw-shop-status", data={"items": [{"slot": 2, "name": "停云", "price": 1}, {"slot": 1, "name": "希儿", "price": 2}]}, screenshot=None)}
    )

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.shop.status count=2",
        body=["item idx=1 slot=1 name=希儿 cost=2", "item idx=2 slot=2 name=停云 cost=1"],
    )
```

```python
# 在 cw_rpc matrix 里把这三条改成带图
(
    ["cw", "encounter", "read", "--session", SESSION_ID],
    "cw.encounter.read",
    {},
    {"options": [1, 2]},
    ".trail/shots/req-cw-encounter-read.png",
    _expected_lines(
        "ok cw.encounter.read count=2",
        screenshot=".trail/shots/req-cw-encounter-read.png",
        body=["opt idx=1 value=1", "opt idx=2 value=2"],
    ),
)
```

- [ ] **Step 3: 跑红灯，确认当前 production 路由和 CLI 期望仍未收口**

Run: `uv run pytest tests/test_daemon_protocol.py -k "captured_reads_through_handle_with_capture" -v && uv run pytest tests/test_cw_rpc_contracts.py -k "cw_stage_detect or cw_stage_wait or cw_shop_status or cw_invest_read or cw_encounter_read or cw_fortune_read" -v`
Expected: FAIL，当前只有 `cw.slots.read` 走 `_run_cw_with_capture(...)`，`encounter.read` / `fortune.read` 也没有 screenshot 预期。

- [ ] **Step 4: 在 `CommandService` 引入 read-capture 集合并切换分发分支**

```python
CW_CAPTURED_READ_METHODS = {
    "cw.stage.detect",
    "cw.stage.wait",
    "cw.shop.scan",
    "cw.replenish.read",
    "cw.invest.read",
    "cw.encounter.read",
    "cw.fortune.read",
}


if request.method.startswith("cw."):
    service = self._session_service(request)
    if request.method == "cw.slots.read" or request.method in CW_CAPTURED_READ_METHODS:
        return self._run_cw_with_capture(request, service=service)
    if request.method in CW_MUTATING_METHODS:
        session_id = request.session_id or request.payload.get("session_id")
        return self._run_mutation(
            request,
            request.method,
            lambda session_service: self._run_cw_mutation(request, service=session_service),
            handler_persisted_state=True,
            response_builder=lambda payload: payload,
            enforce_cw_tainted=bool(isinstance(session_id, str) and session_id),
            tainted_session_id=session_id if isinstance(session_id, str) and session_id else None,
        )
    return success(self._run_cw(request, service=service), request_id=request.request_id)
```

- [ ] **Step 5: 重跑 route-level 与 CLI wrapper 测试，确认 7 条命令统一带图，`cw.shop.status` 继续无图**

Run: `uv run pytest tests/test_daemon_protocol.py -k "captured_reads_through_handle_with_capture" -v && uv run pytest tests/test_cw_rpc_contracts.py -k "cw_stage_detect or cw_stage_wait or cw_shop_status or cw_invest_read or cw_encounter_read or cw_fortune_read" -v`
Expected: PASS。

### Task 4: 同步 README、AGENTS、skills 与活参考文档

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `skills/trail-hsr/SKILL.md`
- Modify: `skills/trail-hsr-advanced/SKILL.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `skills/trail-cw-events/SKILL.md`
- Modify: `skills/trail-cw-slots/SKILL.md`
- Modify: `skills/trail-cw-shop/SKILL.md`
- Modify: `skills/trail-cw-replenish/SKILL.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `docs/superpowers/specs/2026-04-17-trail-output-format-design.md`
- Modify: `docs/superpowers/specs/2026-04-20-cw-battle-run-design.md`
- Modify: `docs/superpowers/specs/2026-04-20-cw-battle-action-buttons-design.md`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先改 README 与项目级 AGENTS，把 guidance 词面、顺序和 `cw.shop.status` 边界冻结下来**

```markdown
<!-- README.md -->
- `shot path=...` 表示当前命令结果对应的截图路径；只要当前命令有截图，就会输出 `shot path=...`，且位于实体行之前
+ `shot path=...` 表示当前命令结果对应的截图路径；只要当前命令有截图，就会先输出 `shot path=...`，再输出 `info read_image_first=1`，然后才是实体行
+ `info read_image_first=1` 表示 Agent 必须先阅读本次命令返回的原始截图，再参考后续压缩文本

- ok cw.shop.status count=2
- shot path=.trail/shots/req-shop.png
- item idx=1 slot=1 name=希儿 cost=2
- item idx=2 slot=2 name=停云 cost=1
+ ok cw.shop.scan opened=1 stale=0 count=2
+ shot path=.trail/shots/req-shop.png
+ info read_image_first=1
+ item idx=1 slot=1 name=希儿 cost=2
+ item idx=2 slot=2 name=停云 cost=1
```

```markdown
<!-- AGENTS.md -->
- success 路径必须先输出首行，再按需要输出 `shot`，然后才是 `item`、`guide`、`text`、`slot`、`opt`、`info` 这类实体行
+ success 路径必须先输出首行，再按需要输出 `shot`；若当前结果带截图，再紧跟 `info read_image_first=1`；然后才是 `item`、`guide`、`text`、`slot`、`opt`、其余 `info` 这类实体行
+ envelope 顶层若带 `screenshot`，同步生成 `image_guidance.read_image_first=1`；该字段不进入 YAML body，也不新增独立 verbose 输出
```

- [ ] **Step 2: 批量改 skill 文案，把“先看图”升级成看到 guidance 时的硬规则**

```markdown
- 每次命令后优先阅读返回的 `screenshot` 与 `data`
+ 每次命令后，如果返回了 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考 `data` / `detect` / `read` / `status` 文本
```

对以下文件执行同一语义替换：

```text
skills/trail-hsr/SKILL.md
skills/trail-hsr-advanced/SKILL.md
skills/trail-cw/SKILL.md
skills/trail-cw-events/SKILL.md
skills/trail-cw-slots/SKILL.md
skills/trail-cw-shop/SKILL.md
skills/trail-cw-replenish/SKILL.md
skills/trail-cw-guide/SKILL.md
```

- [ ] **Step 3: 清理活参考文档里的旧顺序说明，并让 README / skill 断言同步**

```markdown
> Note: 默认 success 路径自截图优先 guidance 变更起，在 `shot path=...` 后固定新增 `info read_image_first=1`；若本文旧示例与当前协议冲突，以 `2026-04-20-screenshot-first-guidance-design.md` 为准。
```

```python
# tests/test_output_rendering.py
assert "info read_image_first=1" in readme
assert "cw.shop.status" in readme  # 仍可保留命令说明
assert "```text\nok cw.shop.status count=2\nshot path=" not in readme
assert "info read_image_first=1" in skill_doc
```

- [ ] **Step 4: 跑文档与规则断言，确认 README / AGENTS / skills 词面已经统一**

Run: `uv run pytest tests/test_output_rendering.py -k "readme or skill or shop_status" -v`
Expected: PASS，且不再保留 `cw.shop.status` 旧带图示例。

### Task 5: 跑最小验证集并记录残余风险

**Files:**
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_output_envelope.py`
- Test: `tests/test_daemon_protocol.py`
- Test: `tests/test_cw_rpc_contracts.py`
- Test: `tests/test_runtime_backends.py`
- Test: `tests/test_atomic_commands.py`

- [ ] **Step 1: 先按文件逐个跑最小验证集，不要直接跑全量 suite**

Run: `uv run pytest tests/test_output_envelope.py -k "image_guidance or daemon_success_matches_envelope_guidance_shape" -v`
Expected: PASS

Run: `uv run pytest tests/test_output_rendering.py -k "read_image_first or cw_slots_summary_text or cw_shop_status or readme or skill" -v`
Expected: PASS

Run: `uv run pytest tests/test_daemon_protocol.py -k "captured_reads_through_handle_with_capture or handles_cw_stage_detect_with_request_scoped_capture" -v`
Expected: PASS，新增的 7 条 read-capture route-level 断言通过

Run: `uv run pytest tests/test_cw_rpc_contracts.py -k "cw_stage_detect or cw_stage_wait or cw_shop_status or cw_replenish_read or cw_invest_read or cw_encounter_read or cw_fortune_read" -v`
Expected: PASS，CLI wrapper 中带图命令统一出现 `shot` + `info read_image_first=1`

Run: `uv run pytest tests/test_runtime_backends.py -k "capture_after_action_passes_request_id_to_window or command_service_binds_references_to_current_screenshot or command_service_capture_chain_keeps_request_scoped_screenshot_inside_workspace" -v`
Expected: PASS，已有 request-scoped screenshot / envelope 行为未被 guidance 回归打坏

Run: `uv run pytest tests/test_atomic_commands.py -k "window_attach_renders_text_output or screen_shot_returns_envelope_and_screenshot or ocr_read_returns_runtime_payload_with_ocr_mode_and_retry_high_defaults or image_locate_returns_box_payload or image_wait_returns_error_when_template_missing or input_click_drag_and_key_return_envelopes or state_dump_renders_summary_before_yaml" -v`
Expected: PASS，CLI 原子命令的 `shot` / renderer 行为未受负面影响

- [ ] **Step 2: 若 `tests/test_runtime_backends.py` 或 `tests/test_atomic_commands.py` 暴露 guidance 相关回归，只做最小修补并重跑该文件**

```python
# 允许的最小修补范围
- `trail/output/envelope.py`
- `trail/output/rendering.py`
- `trail/daemon/command_service.py`
- 对应失败的测试文件
```

- [ ] **Step 3: 记录仍未自动化兜底的残余风险，不在本轮扩面修复**

```text
1. 全量 `uv run pytest` 仍有既有 PermissionError / daemon-runtime 基线失败，不作为本轮阻塞项
2. `docs/superpowers/**` 的旧示例同步仍主要依赖人工核查；只要 plan 中点名的活参考文档完成同步，就不要继续扩面
3. 四条 `read_*` 命令新增 runtime 依赖后，窗口不可附着时仍按现有无图错误语义失败，这符合 spec，不要继续改 helper 语义
```
