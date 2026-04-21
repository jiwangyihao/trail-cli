# CW Strategy Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `trail cw strategy detect|select|refresh` 命令族，覆盖局内“请选择投资策略”页面的识别、选择、单卡刷新，并把当前攻略的投资策略推荐映射成稳定文本协议字段。

**Architecture:** 新增独立 `trail.scenes.cw.strategy` scene 模块，专门承载策略页的 OCR 归一、单卡刷新与 snapshot/stale 语义；daemon 层补 `cw.strategy.*` handler 与 capture/mutating 路由；CLI、renderer、README、skills、AGENTS 和测试同步围绕这组 canonical command 收口。`cw.stage.detect` 本轮继续保留 `invest` coarse stage，不改 stage 名，但主 `trail-cw` skill 在 `stage=invest` 时改走 `cw.strategy.*` 作为首选入口。

**Tech Stack:** Python 3.12, Typer, daemon RPC, `trail.scenes.cw.*`, `trail.daemon.*`, `trail.output.rendering`, pytest, `uv run pytest`

---

## File Structure

### Create
- `docs/superpowers/specs/2026-04-21-cw-strategy-commands-design.md`
  - 已完成；作为本计划的唯一 spec 输入。
- `docs/superpowers/plans/2026-04-21-cw-strategy-commands.md`
  - 当前计划文档。
- `trail/scenes/cw/strategy.py`
  - 新的策略页 scene 模块；负责页面识别、OCR 归一、单卡 refresh、select、guide 推荐命中计算、snapshot/stale 维护。
- `tests/test_cw_strategy.py`
  - 新 scene 模块的核心单测，锁定 state、card_idx、refresh 和 guide 推荐字段。

### Modify
- `trail/scenes/cw/models.py`
  - 为 `CwSceneState` 增加 `strategy` 默认 shape。
- `trail/scenes/cw/guide.py`
  - 扩展 `fetch_cw_guide_config()` 的归一化结果，补充策略权威词表；保持 `guide.config.cw` 默认中文摘要不变。
- `trail/scenes/cw/resources.py`
  - 如实现需要模板别名，在这里补策略页按钮/确认类资源别名。
- `trail/daemon/cw_service.py`
  - 接入 `cw.strategy.detect|select|refresh` handler。
- `trail/daemon/command_service.py`
  - 把 `cw.strategy.detect` 纳入 capture methods，把 `cw.strategy.select|refresh` 纳入 mutating methods。
- `trail/commands/cw.py`
  - 新增 `strategy` command group 与三个子命令；同步 `cw` 顶层 help。
- `trail/output/rendering.py`
  - 新增/复用 `cw.strategy.*` renderer 映射，冻结 `opt` / `info` 输出顺序与 YAML reject。
- `tests/test_cw_guide.py`
  - 锁定 guide config 归一化扩展不会破坏现有 scene/摘要语义。
- `tests/test_daemon_protocol.py`
  - 验证 capture、mutating 分类、request envelope 语义。
- `tests/test_guide_rpc_contracts.py`
  - 验证 `guide.config.cw` 在新增策略词表后仍保持默认中文摘要契约。
- `tests/test_cw_rpc_contracts.py`
  - 验证 CLI -> daemon canonical command 与 stdout 文本。
- `tests/test_output_rendering.py`
  - 验证 renderer family、默认文本协议、`info 已加载攻略=0|1` 与 YAML reject。
- `tests/test_atomic_commands.py`
  - 验证 `cw` / `cw strategy` / `cw invest` help 文案边界。
- `README.md`
  - 更新 `货币战争流程`、`命令面概览`、`输出约定` 示例、`Guide 字段语义`。
- `skills/trail-cw/SKILL.md`
  - 更新 `stage=invest` 的主 skill 路由。
- `skills/trail-cw-replenish/SKILL.md`
  - 明确策略页不是本 skill 的首选处理对象。
- `skills/trail-cw-guide/SKILL.md`
  - 明确 `优选投资策略/次选投资策略` 会映射为 `cw.strategy.*` 的 `攻略推荐` 字段。
- `AGENTS.md`
  - 为 `cw.strategy.detect|refresh|select` 补充输出协议冻结说明。

## Task 1: 新建 scene 模块并冻结 `strategy` snapshot/state 语义

**Files:**
- Create: `trail/scenes/cw/strategy.py`
- Modify: `trail/scenes/cw/models.py`
- Test: `tests/test_cw_strategy.py`

- [ ] **Step 1: 先写失败测试，锁定 `CwSceneState.strategy` 默认 shape**

```python
def test_cw_scene_state_includes_strategy_defaults():
    state = CwSceneState().model_dump()

    assert state["strategy"] == {
        "cards": [],
        "stale": True,
    }
```

- [ ] **Step 2: 运行单测，确认 `strategy` 默认 state 还不存在**

Run:
`uv run pytest tests/test_cw_strategy.py -k "strategy_defaults" -q --basetemp .trail/pytest-temp-cw-strategy-state -p no:cacheprovider`

Expected:
- FAIL，报 `KeyError: 'strategy'` 或 `AttributeError`。

- [ ] **Step 3: 写最小实现，给 `CwSceneState` 增加 `strategy` 默认值**

```python
@dataclass(slots=True)
class CwSceneState:
    guide: dict | None = None
    constraints: dict = field(
        default_factory=lambda: {
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 7,
            "priority": {},
            "positioning": {},
        }
    )
    slots: dict = field(default_factory=lambda: {"stale": True})
    sell_plan: dict = field(default_factory=dict)
    portal: dict = field(
        default_factory=lambda: {
            "cards": [],
            "mode": None,
            "difficulty": None,
            "battle_mode": None,
            "stale": True,
        }
    )
    strategy: dict = field(default_factory=lambda: {"cards": [], "stale": True})
    shop: dict = field(default_factory=lambda: {"stale": True, "max_team_size": None})
    stage: dict = field(default_factory=lambda: {"stale": True})
    metrics: dict = field(default_factory=dict)

    def model_dump(self) -> dict:
        return {
            "guide": deepcopy(self.guide),
            "constraints": deepcopy(self.constraints),
            "slots": deepcopy(self.slots),
            "sell_plan": deepcopy(self.sell_plan),
            "portal": deepcopy(self.portal),
            "strategy": deepcopy(self.strategy),
            "shop": deepcopy(self.shop),
            "stage": deepcopy(self.stage),
            "metrics": deepcopy(self.metrics),
        }
```

- [ ] **Step 4: 再写失败测试，冻结策略页 detect/select/refresh 的核心 state 语义**

```python
def test_detect_cw_strategy_creates_fresh_snapshot(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.strategy as strategy_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    monkeypatch.setattr(strategy_module, "_detect_strategy_page_state", lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"}, raising=False)
    monkeypatch.setattr(strategy_module, "summarize_strategy_cards", lambda *args, **kwargs: [{"card_idx": 1, "strategy_title": "快攻", "strategy_description": "desc", "refresh_count": 1, "guide_match": "优选"}], raising=False)

    snapshot = strategy_module.detect_cw_strategy(session, runtime=object(), strategy_list=[])

    assert snapshot == {
        "cards": [{"card_idx": 1, "strategy_title": "快攻", "strategy_description": "desc", "refresh_count": 1, "guide_match": "优选"}],
        "stale": False,
    }
    assert session.scene_state["cw"]["strategy"] == snapshot


def test_select_cw_strategy_requires_fresh_snapshot(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.strategy as strategy_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"strategy": {"cards": [{"card_idx": 1, "strategy_title": "快攻"}], "stale": True}}
    monkeypatch.setattr(strategy_module, "_detect_strategy_page_state", lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        strategy_module.select_cw_strategy(session, card_idx=1, runtime=object())

    assert exc_info.value.code == "CW_STRATEGY_SNAPSHOT_REQUIRED"


def test_refresh_cw_strategy_requires_valid_card_idx_and_overwrites_all_cards(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.strategy as strategy_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"strategy": {"cards": [{"card_idx": 1, "strategy_title": "旧卡"}], "stale": False}}
    runtime = StrategyRuntime()
    monkeypatch.setattr(strategy_module, "_detect_strategy_page_state", lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"}, raising=False)
    monkeypatch.setattr(strategy_module, "summarize_strategy_cards", lambda *args, **kwargs: [
        {"card_idx": 1, "strategy_title": "新卡1", "strategy_description": "d1", "refresh_count": 0, "guide_match": "否"},
        {"card_idx": 2, "strategy_title": "新卡2", "strategy_description": "d2", "refresh_count": 1, "guide_match": "次选"},
        {"card_idx": 3, "strategy_title": "新卡3", "strategy_description": "d3", "refresh_count": 2, "guide_match": "优选"},
    ], raising=False)

    snapshot = strategy_module.refresh_cw_strategy(session, card_idx=2, runtime=runtime, strategy_list=[])

    assert [card["strategy_title"] for card in snapshot["cards"]] == ["新卡1", "新卡2", "新卡3"]
    assert session.scene_state["cw"]["strategy"] == snapshot
    assert session.scene_state["cw"]["stage"] == {"stale": True}
```

- [ ] **Step 5: 跑红灯，确认 scene 模块还不存在或接口未实现**

Run:
`uv run pytest tests/test_cw_strategy.py -q --basetemp .trail/pytest-temp-cw-strategy-scene-red -p no:cacheprovider`

Expected:
- FAIL，报 `ModuleNotFoundError`、`AttributeError` 或新断言失败。

- [ ] **Step 6: 写最小 scene 实现，先把 state/stale/card_idx 契约跑通**

```python
def _require_strategy_page(runtime, *, session) -> dict[str, object]:
    current = _detect_strategy_page_state(runtime, session=session)
    if current.get("page") != "in_game" or current.get("stage") != "invest":
        error = TrailError("CW_STRATEGY_PAGE_INVALID", f"cw strategy action only supports in_game/invest, current page: {current.get('page')}, stage: {current.get('stage')}")
        error.data = {"page": current.get("page"), "stage": current.get("stage")}
        raise error
    return current


def _require_strategy_snapshot(session, *, command_name: str) -> dict[str, object]:
    strategy = ensure_cw_state(session).get("strategy")
    if isinstance(strategy, Mapping) and isinstance(strategy.get("cards"), list) and strategy.get("cards") and strategy.get("stale") is not True:
        return dict(strategy)
    raise TrailError("CW_STRATEGY_SNAPSHOT_REQUIRED", f"{command_name} requires fresh strategy snapshot")


def detect_cw_strategy(session, *, runtime, strategy_list: object) -> dict[str, object]:
    _require_strategy_page(runtime, session=session)
    cards = summarize_strategy_cards(runtime.ocr(), strategy_list, guide_state=ensure_cw_state(session).get("guide"))
    snapshot = {"cards": cards, "stale": False}
    ensure_cw_state(session)["strategy"] = snapshot
    return snapshot


def refresh_cw_strategy(session, *, card_idx: int, runtime, strategy_list: object) -> dict[str, object]:
    _require_strategy_page(runtime, session=session)
    if card_idx not in {1, 2, 3}:
        raise TrailError("CW_STRATEGY_CARD_IDX_INVALID", f"cw strategy.refresh only supports card_idx 1|2|3, got: {card_idx}")
    runtime.click_point(*_strategy_refresh_point(card_idx))
    snapshot = detect_cw_strategy(session, runtime=runtime, strategy_list=strategy_list)
    ensure_cw_state(session)["stage"] = {"stale": True}
    return snapshot


def select_cw_strategy(session, *, card_idx: int, runtime) -> dict[str, object]:
    _require_strategy_page(runtime, session=session)
    snapshot = _require_strategy_snapshot(session, command_name="cw strategy.select")
    selected = dict(snapshot["cards"][card_idx - 1])
    runtime.click_point(*_strategy_card_point(card_idx))
    runtime.click_point(*STRATEGY_CONFIRM_POINT)
    ensure_cw_state(session)["strategy"] = {**snapshot, "stale": True}
    ensure_cw_state(session)["stage"] = {"stale": True}
    return selected
```

- [ ] **Step 7: 跑 scene 测试，确认 state 基线通过**

Run:
`uv run pytest tests/test_cw_strategy.py -q --basetemp .trail/pytest-temp-cw-strategy-scene-green -p no:cacheprovider`

Expected:
- PASS，至少新增的 state/stale/card_idx 用例全部通过。

## Task 2: 归一化策略词表并把当前攻略映射成 `攻略推荐` / `已加载攻略`

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Modify: `trail/scenes/cw/strategy.py`
- Test: `tests/test_cw_guide.py`
- Test: `tests/test_cw_strategy.py`
- Test: `tests/test_guide_rpc_contracts.py`

- [ ] **Step 1: 先写失败测试，锁定 config 词表扩展但不改变 `guide.config.cw` 默认中文摘要**

```python
def test_fetch_cw_guide_config_includes_strategy_list_without_changing_summary_shape(monkeypatch):
    import trail.scenes.cw.guide as guide_module

    monkeypatch.setattr(
        guide_module,
        "_request_json",
        lambda *args, **kwargs: {
            "data": {
                "meta": {"season_id": 12, "sub_season_id": 3, "big_version": "3.2"},
                "lineup_level_list": [],
                "trait_list": [],
                "role_list": [],
                "role_tag_list": [],
                "portal_list": [],
                "fight_augment_list": [
                    {"id": "rush", "name": "快攻", "description": "desc1"},
                    {"id": "mana", "name": "回蓝", "description": "desc2"},
                ],
            }
        },
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_config()

    assert payload["strategy_list"] == [
        {"strategy_id": "rush", "title": "快攻", "description": "desc1"},
        {"strategy_id": "mana", "title": "回蓝", "description": "desc2"},
    ]
    assert "portal_list" in payload
```

- [ ] **Step 2: 写失败测试，锁定 `攻略推荐` 与 `已加载攻略` 的计算语义**

```python
def test_summarize_strategy_cards_marks_primary_secondary_and_loaded_state():
    cards = summarize_strategy_cards(
        [
            {"text": "快攻"},
            {"text": "回蓝"},
            {"text": "暴击"},
        ],
        [
            {"strategy_id": "rush", "title": "快攻", "description": "d1"},
            {"strategy_id": "mana", "title": "回蓝", "description": "d2"},
            {"strategy_id": "crit", "title": "暴击", "description": "d3"},
        ],
        guide_state={"first_fight_augments": ["快攻"], "second_fight_augments": ["回蓝"]},
    )

    assert cards[0]["guide_match"] == "优选"
    assert cards[1]["guide_match"] == "次选"
    assert cards[2]["guide_match"] == "否"
    assert cards[0]["guide_loaded"] == 1


def test_summarize_strategy_cards_marks_not_recommended_when_guide_missing():
    cards = summarize_strategy_cards(
        [{"text": "快攻"}],
        [{"strategy_id": "rush", "title": "快攻", "description": "d1"}],
        guide_state=None,
    )

    assert cards[0]["guide_match"] == "否"
    assert cards[0]["guide_loaded"] == 0
```

- [ ] **Step 3: 跑红灯，确认 guide config / `guide_match` 逻辑尚未实现**

Run:
`uv run pytest tests/test_cw_strategy.py tests/test_guide_rpc_contracts.py -k "strategy_list or guide_match or guide_loaded" -q --basetemp .trail/pytest-temp-cw-strategy-guide-red -p no:cacheprovider`

Expected:
- FAIL，缺少 `strategy_list`、`guide_match` 或 `guide_loaded` 字段。

- [ ] **Step 4: 写最小实现，扩展 guide config 并把当前 guide 映射成稳定字段**

```python
def _normalize_strategy_list(values: object) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(values, list):
        return normalized
    for item in values:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("name") or item.get("title") or "").strip()
        if not title:
            continue
        normalized.append(
            {
                "strategy_id": str(item.get("id") or item.get("strategy_id") or title),
                "title": title,
                "description": str(item.get("description") or item.get("desc") or "").strip(),
            }
        )
    return normalized


def fetch_cw_guide_config(timeout: int = 10) -> dict:
    payload = _request_json(CW_GUIDE_CONFIG_API, headers=CW_GUIDE_HEADERS, timeout=timeout)
    data = payload.get("data") if isinstance(payload, Mapping) else {}
    return {
        "meta": _normalize_meta(data.get("meta")),
        "lineup_levels": _normalize_lineup_levels(data.get("lineup_level_list")),
        "traits": _normalize_traits(data.get("trait_list")),
        "roles": _normalize_roles(data.get("role_list")),
        "role_tags": _normalize_role_tags(data.get("role_tag_list")),
        "portal_list": _normalize_portal_list(data.get("portal_list")),
        "strategy_list": _normalize_strategy_list(data.get("fight_augment_list")),
    }


def _guide_match_for_strategy(title: str, guide_state: Mapping[str, object] | None) -> tuple[str, int]:
    if not isinstance(guide_state, Mapping):
        return "否", 0
    primary = {str(item).strip() for item in guide_state.get("first_fight_augments", []) if str(item).strip()}
    secondary = {str(item).strip() for item in guide_state.get("second_fight_augments", []) if str(item).strip()}
    if title in primary:
        return "优选", 1
    if title in secondary:
        return "次选", 1
    return "否", 1
```

- [ ] **Step 5: 跑测试，确认 `strategy_list` 扩展不会破坏现有 `guide.config.cw` 默认输出语义**

Run:
`uv run pytest tests/test_cw_guide.py tests/test_guide_rpc_contracts.py tests/test_cw_strategy.py -k "guide_config or strategy_list or guide_match or guide_loaded" -q --basetemp .trail/pytest-temp-cw-strategy-guide-green -p no:cacheprovider`

Expected:
- PASS。
- 现有 `guide.config.cw` 默认中文摘要测试保持绿色。

## Task 3: 接入 daemon/CLI，冻结 `cw.strategy.*` canonical command 与 capture 路由

**Files:**
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/commands/cw.py`
- Test: `tests/test_daemon_protocol.py`
- Test: `tests/test_cw_rpc_contracts.py`
- Test: `tests/test_atomic_commands.py`

- [ ] **Step 1: 写失败测试，锁定 CLI -> daemon method 与 help 文案边界**

```python
def test_cw_help_exposes_strategy_group(cli_runner):
    result = cli_runner.invoke(app, ["cw", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "strategy" in result.output
    assert "局内投资策略页" in normalized
    assert "单卡刷新" in normalized


def test_cw_strategy_refresh_help_requires_card_idx(cli_runner):
    result = cli_runner.invoke(app, ["cw", "strategy", "refresh", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "--card-idx" in result.output
    assert "只刷新该卡" in normalized
    assert "整页刷新" not in normalized


def test_cw_invest_help_marks_it_as_compatibility_entry(cli_runner):
    result = cli_runner.invoke(app, ["cw", "invest", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "兼容" in normalized
    assert "不用于投资策略页" in normalized
```

- [ ] **Step 2: 写失败测试，锁定 RPC 载荷与 mutating/capture 分类**

```python
def test_cw_strategy_detect_rpc_contract(cli_runner, fake_daemon_client, tmp_path: Path):
    client = fake_daemon_client({
        "cw.strategy.detect": build_success_response(
            request_id="req-cw-strategy-detect",
            data={"cards": [{"card_idx": 1, "strategy_title": "快攻", "strategy_description": "desc", "refresh_count": 1, "guide_match": "优选", "guide_loaded": 1}], "stale": False},
            screenshot=".trail/shots/req-cw-strategy-detect.png",
        )
    })

    result = cli_runner.invoke(app, ["cw", "strategy", "detect", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert client.calls == [{"method": "cw.strategy.detect", "payload": {}, "workspace_root": str(tmp_path), "session_id": SESSION_ID, "verbose": False}]


def test_command_service_marks_strategy_methods_capture_and_mutation():
    assert "cw.strategy.detect" in CW_CAPTURE_METHODS
    assert "cw.strategy.detect" not in CW_MUTATING_METHODS
    assert "cw.strategy.select" in CW_MUTATING_METHODS
    assert "cw.strategy.refresh" in CW_MUTATING_METHODS
```

- [ ] **Step 3: 跑红灯，确认 `cw.strategy.*` 命令面和 daemon 路由尚未实现**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_atomic_commands.py -k "cw_strategy or strategy_group or strategy_methods or invest_help_marks" -q --basetemp .trail/pytest-temp-cw-strategy-cli-red -p no:cacheprovider`

Expected:
- FAIL，报未知命令、缺少 help 文案、集合断言失败或 RPC contract 不匹配。

- [ ] **Step 4: 写最小实现，接入 command group、daemon handler 和 capture/mutation 集合**

```python
strategy_app = typer.Typer(no_args_is_help=True, help="局内投资策略页的识别/选择/单卡刷新动作。detect 只重建当前三张策略卡快照；refresh 只刷新指定卡片。")
cw_app.add_typer(strategy_app, name="strategy")


@strategy_app.command("detect")
def cw_strategy_detect(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.strategy.detect", session_id=session)


@strategy_app.command("select")
def cw_strategy_select(session: str = typer.Option(..., "--session"), card_idx: int = typer.Option(..., "--card-idx")) -> None:
    _print_cw("cw.strategy.select", session_id=session, payload={"card_idx": card_idx})


@strategy_app.command("refresh")
def cw_strategy_refresh(session: str = typer.Option(..., "--session"), card_idx: int = typer.Option(..., "--card-idx")) -> None:
    _print_cw("cw.strategy.refresh", session_id=session, payload={"card_idx": card_idx})


CW_CAPTURE_METHODS = {"cw.slots.read", "cw.portal.detect", "cw.strategy.detect"}
CW_MUTATING_METHODS = {
    "cw.enter",
    "cw.start",
    "cw.portal.select",
    "cw.portal.refresh",
    "cw.portal.restart",
    "cw.guide.apply",
    "cw.strategy.select",
    "cw.strategy.refresh",
    "cw.slots.swap",
    "cw.slots.place_one",
    "cw.shop.open",
    "cw.shop.buy_slot",
    "cw.shop.refresh",
    "cw.shop.close",
    "cw.crystals.collect",
    "cw.hand.sell_one",
    "cw.hand.sell_plan",
    "cw.replenish.choose",
    "cw.invest.choose",
    "cw.encounter.choose",
    "cw.fortune.choose",
    "cw.boss_preview.confirm",
    "cw.battle.start",
    "cw.battle.continue",
    "cw.settle.next",
    "cw.event.handle",
}


handlers = {
    "cw.enter": lambda: validated_enter_payload() and enter_cw(session, runtime=runtime()).scene_state["cw"]["entry"],
    "cw.start": run_start,
    "cw.portal.select": lambda: select_cw_portal(session, card_idx=payload["card_idx"], runtime=runtime()),
    "cw.portal.detect": lambda: _attach_guides_to_portal_snapshot(
        session,
        detect_cw_portal(session, runtime=runtime(), portal_list=fetch_cw_guide_config().get("portal_list", [])),
    ),
    "cw.strategy.detect": lambda: detect_cw_strategy(session, runtime=runtime(), strategy_list=fetch_cw_guide_config().get("strategy_list", [])),
    "cw.strategy.select": lambda: select_cw_strategy(session, card_idx=payload["card_idx"], runtime=runtime()),
    "cw.strategy.refresh": lambda: refresh_cw_strategy(session, card_idx=payload["card_idx"], runtime=runtime(), strategy_list=fetch_cw_guide_config().get("strategy_list", [])),
}
```

- [ ] **Step 5: 跑测试，确认 canonical command、help 与 capture/mutation 分类通过**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_atomic_commands.py -k "cw_strategy or strategy_group or strategy_methods or invest_help_marks" -q --basetemp .trail/pytest-temp-cw-strategy-cli-green -p no:cacheprovider`

Expected:
- PASS。

## Task 4: renderer 收口 `opt`/`info` 文本协议与 YAML reject

**Files:**
- Modify: `trail/output/rendering.py`
- Test: `tests/test_output_rendering.py`

- [ ] **Step 1: 写失败测试，冻结 `cw.strategy.detect|refresh|select` 文本协议**

```python
def test_render_output_renders_cw_strategy_detect_text():
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {"card_idx": 1, "strategy_title": "快攻", "strategy_description": "前中期更强势", "refresh_count": 1, "guide_match": "优选", "guide_loaded": 1},
                {"card_idx": 2, "strategy_title": "回蓝", "strategy_description": "依赖技能循环", "refresh_count": 0, "guide_match": "次选", "guide_loaded": 1},
            ],
            "stale": False,
        },
        "screenshot": ".trail/shots/req-cw-strategy-detect.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.strategy.detect", payload).splitlines() == [
        "ok cw.strategy.detect cards=2",
        "shot path=.trail/shots/req-cw-strategy-detect.png",
        'opt idx=1 投资策略=快攻 攻略推荐=优选 刷新次数=1',
        'opt idx=1 说明="前中期更强势"',
        'opt idx=2 投资策略=回蓝 攻略推荐=次选 刷新次数=0',
        'opt idx=2 说明="依赖技能循环"',
        "info 已加载攻略=1",
    ]


def test_render_output_renders_cw_strategy_select_summary_text():
    payload = {
        "ok": True,
        "data": {"card_idx": 2, "strategy_title": "回蓝"},
        "screenshot": ".trail/shots/req-cw-strategy-select.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.strategy.select", payload).splitlines() == [
        "ok cw.strategy.select idx=2 投资策略=回蓝",
        "shot path=.trail/shots/req-cw-strategy-select.png",
    ]


def test_render_output_rejects_yaml_for_cw_strategy_detect():
    payload = {
        "ok": True,
        "data": {"cards": [], "stale": False},
        "screenshot": ".trail/shots/req-cw-strategy-detect-yaml.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.strategy.detect", payload, output_format="yaml").splitlines() == [
        "fail cw.strategy.detect code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-cw-strategy-detect-yaml.png",
        'why msg="yaml not supported for cw.strategy.detect"',
    ]
```

- [ ] **Step 2: 跑红灯，确认 renderer 映射与输出尚未存在**

Run:
`uv run pytest tests/test_output_rendering.py -k "cw_strategy_detect or cw_strategy_select or strategy_detect_rejects_yaml" -q --basetemp .trail/pytest-temp-cw-strategy-render-red -p no:cacheprovider`

Expected:
- FAIL，报 `KeyError`、文本不匹配或 YAML reject 失败。

- [ ] **Step 3: 写最小 renderer，实现 cards family + summary family + `info 已加载攻略` 聚合**

```python
def _render_cw_strategy_cards(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    cards = _as_list(data.get("cards"))
    lines = _render_success_summary(command, payload, ("cards", len(cards)))
    guide_loaded = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        guide_loaded = max(guide_loaded, int(card.get("guide_loaded") or 0))
        _append_fact_line(
            lines,
            "opt",
            ("idx", card.get("card_idx")),
            ("投资策略", card.get("strategy_title")),
            ("攻略推荐", card.get("guide_match")),
            ("刷新次数", card.get("refresh_count")),
        )
        _append_fact_line(lines, "opt", ("idx", card.get("card_idx")), ("说明", card.get("strategy_description")))
    _append_fact_line(lines, "info", ("已加载攻略", guide_loaded))
    return lines


def _render_cw_strategy_select(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(command, payload, ("idx", data.get("card_idx")), ("投资策略", data.get("strategy_title")))


TEXT_RENDERERS.update(
    {
        "cw.strategy.detect": _render_cw_strategy_cards,
        "cw.strategy.refresh": _render_cw_strategy_cards,
        "cw.strategy.select": _render_cw_strategy_select,
    }
)
```

- [ ] **Step 4: 跑 renderer 测试，确认文本协议冻结**

Run:
`uv run pytest tests/test_output_rendering.py -k "cw_strategy_detect or cw_strategy_select or strategy_detect_rejects_yaml" -q --basetemp .trail/pytest-temp-cw-strategy-render-green -p no:cacheprovider`

Expected:
- PASS。

## Task 5: README / SKILL / AGENTS / help 文案同步收口

**Files:**
- Modify: `README.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `skills/trail-cw-replenish/SKILL.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `AGENTS.md`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_atomic_commands.py`

- [ ] **Step 1: 先写失败断言，锁定 README / SKILL / AGENTS 必须出现的新边界句**

```python
def test_readme_mentions_strategy_page_boundary(readme: str):
    assert "局内投资策略页使用 trail cw strategy.*" in readme
    assert "普通局内 invest 事件的兼容/粗粒度入口才是 trail cw invest.*" in readme
    assert "info 已加载攻略=0|1" in readme


def test_trail_cw_skill_routes_stage_invest_to_strategy(cw_skill: str):
    assert "stage=invest" in cw_skill
    assert "优先使用 trail cw strategy detect|refresh|select" in cw_skill


def test_replenish_skill_says_strategy_page_returns_to_main_skill(replenish_skill: str):
    assert "请选择投资策略" in replenish_skill
    assert "交回主 skill" in replenish_skill


def test_agents_mentions_cw_strategy_protocol(agents: str):
    assert "cw.strategy.detect|refresh|select" in agents
    assert "投资策略" in agents
    assert "攻略推荐" in agents
```

- [ ] **Step 2: 跑红灯，确认文档/技能/AGENTS 还未同步**

Run:
`uv run pytest tests/test_output_rendering.py tests/test_atomic_commands.py -k "strategy_page_boundary or routes_stage_invest_to_strategy or strategy_protocol" -q --basetemp .trail/pytest-temp-cw-strategy-docs-red -p no:cacheprovider`

Expected:
- FAIL，缺少 README/skill/AGENTS 新句子或 help 锚点。

- [ ] **Step 3: 最小更新 README、skills、AGENTS 与 help 文案**

```text
README 追加/改写要点：
- 货币战争流程：说明 `stage=invest` 时先判断是否为“请选择投资策略”页面，并改用 `cw.strategy.*`
- 命令面概览：新增 `strategy` 分组，明确它与 `portal` / `invest` 的区别
- 输出约定：新增 `cw.strategy.detect` / `refresh` / `select` 示例块
- Guide 字段语义：说明 `优选投资策略/次选投资策略` 是 `攻略推荐` 的来源

skills/trail-cw/SKILL.md：
- 在 stage 分发处把 `invest` 改成主 skill 直接走 `cw.strategy.*`

skills/trail-cw-replenish/SKILL.md：
- 明确 `cw.invest.*` 是兼容/粗粒度入口，不处理“请选择投资策略”页

skills/trail-cw-guide/SKILL.md：
- 明确 guide 的投资策略字段会在 `cw.strategy.*` 被消费

AGENTS.md：
- 新增 `cw.strategy.detect|refresh|select` 的 must-keep facts 与正文字段约束
```

- [ ] **Step 4: 跑文档/skill/help 断言，确认防误用文案全部落地**

Run:
`uv run pytest tests/test_output_rendering.py tests/test_atomic_commands.py -k "strategy_page_boundary or routes_stage_invest_to_strategy or strategy_protocol or cw_strategy or invest_help_marks" -q --basetemp .trail/pytest-temp-cw-strategy-docs-green -p no:cacheprovider`

Expected:
- PASS。

## Task 6: 组合验证与最小提交面检查

**Files:**
- Modify: 已在前面任务列出
- Test: `tests/test_cw_guide.py`
- Test: `tests/test_cw_strategy.py`
- Test: `tests/test_daemon_protocol.py`
- Test: `tests/test_guide_rpc_contracts.py`
- Test: `tests/test_cw_rpc_contracts.py`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_atomic_commands.py`

- [ ] **Step 1: 运行 scene/guide/renderer/CLI/daemon 相关目标测试组合**

Run:
`uv run pytest tests/test_cw_guide.py tests/test_cw_strategy.py tests/test_daemon_protocol.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py -q --basetemp .trail/pytest-temp-cw-strategy-targeted -p no:cacheprovider`

Expected:
- 新增和受影响测试全部 PASS。
- 不再出现 `C:\Users\34404\AppData\Local\Temp\pytest-of-34404` 权限错误，因为本计划所有 pytest 命令都显式使用了 `--basetemp`。

- [ ] **Step 2: 运行针对 README / skill / AGENTS 原文断言的补充筛选**

Run:
`uv run pytest tests/test_output_rendering.py tests/test_atomic_commands.py -k "readme or skill or agents or help" -q --basetemp .trail/pytest-temp-cw-strategy-doclocks -p no:cacheprovider`

Expected:
- PASS。

- [ ] **Step 3: 检查 worktree 改动面，只保留本计划相关文件**

Run:
`rtk git status --short`

Expected:
- 只出现本计划列出的 scene/daemon/CLI/renderer/tests/README/skills/AGENTS/doc 文件。

- [ ] **Step 4: 记录 baseline 测试环境问题，避免误判为功能回归**

```text
如果需要再次跑更大范围 pytest，统一使用：
--basetemp .trail/pytest-temp-<task-name> -p no:cacheprovider

原因：当前 worktree 直接跑 `pytest -q` 会因为 `C:\Users\34404\AppData\Local\Temp\pytest-of-34404` 权限拒绝而产生大量环境级错误，这不是本功能回归信号。
```

- [ ] **Step 5: 提交**

```bash
git add trail/scenes/cw/models.py trail/scenes/cw/guide.py trail/scenes/cw/strategy.py trail/scenes/cw/resources.py trail/daemon/cw_service.py trail/daemon/command_service.py trail/commands/cw.py trail/output/rendering.py tests/test_cw_guide.py tests/test_cw_strategy.py tests/test_daemon_protocol.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py README.md skills/trail-cw/SKILL.md skills/trail-cw-replenish/SKILL.md skills/trail-cw-guide/SKILL.md AGENTS.md docs/superpowers/specs/2026-04-21-cw-strategy-commands-design.md docs/superpowers/plans/2026-04-21-cw-strategy-commands.md
git commit -m "feat(cw): 新增投资策略页命令族"
```
