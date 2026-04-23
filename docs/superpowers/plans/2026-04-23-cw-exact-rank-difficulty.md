# CW Exact Rank Difficulty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展 `trail cw start --difficulty`，在保留 `lowest/current/highest` 的同时支持 `AX-X` 精确职级输入，并让 `lowest` 与精确职级都依赖固定区域 OCR 的当前敌人难度真值进行粗调/细调与恢复。

**Architecture:** 继续沿用现有 `CLI -> daemon -> scene` 分层。CLI 只透传公开 token 与更新帮助文本；daemon 统一做公共输入校验并保留公开错误码；`trail.scenes.cw.entry` 负责职级表、固定区域 OCR、精确调级与 `lowest` 状态机；成功态继续把 `difficulty` 以公开 token 形式持久化到 `entry/portal`，失败态复用现有 mutation known-failure 机制返回 `CW_START_DIFFICULTY_RECOVERY_REQUIRED`。

**Tech Stack:** Python 3.12, Typer, daemon RPC, `trail.scenes.cw.entry`, `trail.daemon.cw_service`, `trail.output.rendering`, pytest, `uv run pytest`

---

## File Structure

### Create
- `docs/superpowers/specs/2026-04-23-cw-exact-rank-difficulty-design.md`
  - 已完成；作为本计划的唯一 spec 输入。
- `docs/superpowers/plans/2026-04-23-cw-exact-rank-difficulty.md`
  - 当前实现计划文档。

### Modify
- `trail/commands/cw.py`
  - 把 `cw start --difficulty` 从 `StrEnum` 改成字符串参数，补全 `AX-X` 帮助文本与职级层数列表。
- `trail/scenes/cw/entry.py`
  - 新增职级表、公开 token 解析、固定区域 OCR、`AX-X` 精确调级状态机、`lowest` 粗调/细调状态机，以及 `CW_START_DIFFICULTY_RECOVERY_REQUIRED` 业务错误。
- `trail/daemon/cw_service.py`
  - 扩展 `cw.start` 的公共输入校验，支持 `AX-X`；在 post-side-effect known failure 场景下保留业务错误码与稳定 envelope。
- `tests/test_atomic_commands.py`
  - 锁定 `cw start --help` 与非法 `--difficulty` CLI 契约。
- `tests/test_cw_entry.py`
  - 锁定职级表映射、固定区域 OCR 数字拼接与非法空洞值处理。
- `tests/test_cw_portal.py`
  - 锁定 `cw.start` 在 `entry.new`/`entry.continue`/`stage.boss_preview`/`invest` 下的选级行为、`entry/portal.difficulty` token 持久化、以及 `cw.portal.restart` replay。
- `tests/test_daemon_protocol.py`
  - 锁定 daemon 非法输入校验与 `CW_START_DIFFICULTY_RECOVERY_REQUIRED` 的 `final_state` / `tainted` 契约。
- `tests/test_cw_rpc_contracts.py`
  - 锁定 `A7-3` 透传到 `cw.start` 的 RPC 契约。
- `tests/test_output_rendering.py`
  - 锁定 README/help/skill 口径断言，以及恢复错误的 envelope/文档约束。
- `README.md`
  - 更新 `trail cw start --difficulty` 对外示例与说明，统一为 `lowest/current/highest/AX-X` 与 `A0-1..A8-40` 口径。
- `skills/trail-cw-entry/SKILL.md`
  - 更新难度确认与 `trail cw start --difficulty` 的用户面说明。
- `skills/trail-cw-entry/references/player-language-mapping.md`
  - 同步用户面难度表达，不暴露 `AX-X -> enemy_difficulty` 数值映射。
- `skills/trail-cw-entry/references/confirmation-checklist.md`
  - 同步确认问题与示例输入。

## Task 1: 放宽 CLI `--difficulty` 输入并把公共校验收口到 daemon

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/daemon/cw_service.py`
- Test: `tests/test_atomic_commands.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写失败测试，锁定 `cw start --help` 与非法 `--difficulty` 的 CLI 契约**

```python
def test_cw_start_help_mentions_ax_x_and_rank_ranges(cli_runner):
    result = cli_runner.invoke(app, ["cw", "start", "--help"])

    assert result.exit_code == 0
    assert "lowest/current/highest/AX-X" in result.stdout
    assert "A0-1..A0-3" in result.stdout
    assert "A8-1..A8-40" in result.stdout


def test_cw_start_invalid_difficulty_reaches_daemon_instead_of_typer(cli_runner, monkeypatch):
    seen = {}

    def fake_call(method, payload, *, session_id=None, verbose=None, daemon_client=None):
        seen["method"] = method
        seen["payload"] = payload
        return {
            "ok": False,
            "data": {},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": {
                "code": "CW_START_DIFFICULTY_INVALID",
                "message": "unsupported cw start difficulty: A9-1",
            },
        }

    monkeypatch.setattr("trail.commands.cw.call_daemon", fake_call)

    result = cli_runner.invoke(
        app,
        ["cw", "start", "--session", "session-1", "--mode", "new", "--difficulty", "A9-1", "--battle-mode", "standard"],
    )

    assert result.exit_code == 0
    assert seen == {
        "method": "cw.start",
        "payload": {
            "session_id": "session-1",
            "mode": "new",
            "difficulty": "A9-1",
            "battle_mode": "standard",
        },
    }
    assert "fail cw.start code=CW_START_DIFFICULTY_INVALID" in result.stdout
```

- [ ] **Step 2: 跑这两个测试，确认当前 `StrEnum` CLI 还不支持 `AX-X`**

Run:
`uv run pytest tests/test_atomic_commands.py -k "cw_start_help_mentions_ax_x_and_rank_ranges or cw_start_invalid_difficulty_reaches_daemon_instead_of_typer" -q --basetemp .trail/pytest-temp-cw-start-cli -p no:cacheprovider`

Expected:
- `test_cw_start_help_mentions_ax_x_and_rank_ranges` FAIL，因为 help 里还没有 `AX-X` 与 `A0-1..A8-40`。
- `test_cw_start_invalid_difficulty_reaches_daemon_instead_of_typer` FAIL，因为 Typer 还会在本地拦截非法值。

- [ ] **Step 3: 写最小实现，改成字符串参数并补统一帮助文案**

```python
CW_START_DIFFICULTY_HELP = (
    "难度支持 lowest/current/highest/AX-X；"
    "AX-X 例如 A7-3。可用范围："
    "A0-1..A0-3，A1-1..A1-3，A2-1..A2-3，A3-1..A3-5，"
    "A4-1..A4-5，A5-1..A5-7，A6-1..A6-7，A7-1..A7-9，A8-1..A8-40"
)


@cw_app.command("start")
def cw_start(
    session: str = typer.Option(..., "--session"),
    mode: EnterMode = typer.Option(..., "--mode"),
    difficulty: str = typer.Option("current", "--difficulty", help=CW_START_DIFFICULTY_HELP),
    battle_mode: BattleMode = typer.Option(BattleMode.STANDARD, "--battle-mode"),
) -> None:
    _print_cw(
        "cw.start",
        session_id=session,
        payload={
            "mode": mode.value,
            "difficulty": difficulty,
            "battle_mode": battle_mode.value,
        },
    )
```

- [ ] **Step 4: 在 daemon 写统一公共校验，接受 `AX-X`，保留 `CW_START_DIFFICULTY_INVALID`**

```python
EXACT_CW_DIFFICULTY_PATTERN = re.compile(r"^A([0-8])-(\d+)$")
EXACT_CW_DIFFICULTY_MAX_LAYER = {
    "A0": 3,
    "A1": 3,
    "A2": 3,
    "A3": 5,
    "A4": 5,
    "A5": 7,
    "A6": 7,
    "A7": 9,
    "A8": 40,
}


def _is_valid_cw_start_difficulty(value: str) -> bool:
    if value in {"lowest", "current", "highest"}:
        return True
    match = EXACT_CW_DIFFICULTY_PATTERN.fullmatch(value)
    if match is None:
        return False
    rank_code = f"A{match.group(1)}"
    layer = int(match.group(2))
    return 1 <= layer <= EXACT_CW_DIFFICULTY_MAX_LAYER[rank_code]


def validated_start_payload() -> tuple[str, str, str]:
    mode = payload.get("mode")
    difficulty = payload.get("difficulty")
    battle_mode = payload.get("battle_mode")
    if not isinstance(mode, str) or not isinstance(difficulty, str) or not isinstance(battle_mode, str):
        raise TrailError("CW_START_ARGS_REQUIRED", "cw start requires mode/difficulty/battle_mode")
    if mode not in {"new", "continue"}:
        raise TrailError("CW_START_MODE_INVALID", f"unsupported cw start mode: {mode}")
    if not _is_valid_cw_start_difficulty(difficulty):
        raise TrailError("CW_START_DIFFICULTY_INVALID", f"unsupported cw start difficulty: {difficulty}")
    if battle_mode not in {"standard", "overclock"}:
        raise TrailError("CW_START_BATTLE_MODE_INVALID", f"unsupported cw start battle_mode: {battle_mode}")
    return mode, difficulty, battle_mode
```

- [ ] **Step 5: 写 daemon 失败测试，锁定非法 `AX-X` 全部归到同一个错误码**

```python
@pytest.mark.parametrize(
    "difficulty",
    ["A3-6", "A8-41", "A9-1", "A7_3", "a7-3"],
)
def test_command_service_handles_cw_start_rejects_invalid_ax_x(tmp_path: Path, difficulty: str):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=CwService(runtime_service=runtime_service))

    request = DaemonRequest(
        request_id=f"req-invalid-{difficulty}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": difficulty,
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "CW_START_DIFFICULTY_INVALID",
        "message": f"unsupported cw start difficulty: {difficulty}",
    }
```

- [ ] **Step 6: 跑本任务测试，确认 CLI 与 daemon 公共校验一起通过**

Run:
`uv run pytest tests/test_atomic_commands.py tests/test_daemon_protocol.py -k "cw_start_help_mentions_ax_x_and_rank_ranges or cw_start_invalid_difficulty_reaches_daemon_instead_of_typer or rejects_invalid_ax_x" -q --basetemp .trail/pytest-temp-cw-start-validation -p no:cacheprovider`

Expected:
- PASS，help 文案出现 `AX-X` 与全量层级范围。
- PASS，非法值不再由 Typer 本地拦截，而是统一返回 `CW_START_DIFFICULTY_INVALID`。

- [ ] **Step 7: Commit**

```bash
git add trail/commands/cw.py trail/daemon/cw_service.py tests/test_atomic_commands.py tests/test_daemon_protocol.py
git commit -m "feat(cw): 扩展 start 难度输入契约"
```

## Task 2: 在 `entry.py` 增加职级表、固定区域 OCR 与数值反解 helper

**Files:**
- Modify: `trail/scenes/cw/entry.py`
- Test: `tests/test_cw_entry.py`

- [ ] **Step 1: 先写失败测试，锁定 `AX-X` 解析、难度反解与 OCR 数字拼接**

```python
from trail.scenes.cw.entry import (
    parse_cw_start_difficulty_token,
    resolve_entry_rank_from_enemy_difficulty,
    read_entry_enemy_difficulty,
)


def test_parse_cw_start_difficulty_token_accepts_exact_rank():
    parsed = parse_cw_start_difficulty_token("A7-3")

    assert parsed == {
        "kind": "exact",
        "token": "A7-3",
        "rank_code": "A7",
        "rank_name": "资本帝王",
        "layer": 3,
        "target_enemy_difficulty": 51,
        "global_layer_ordinal": 36,
    }


@pytest.mark.parametrize("raw", ["A3-6", "A8-41", "A9-1", "A7_3", "a7-3"])
def test_parse_cw_start_difficulty_token_rejects_invalid_exact_rank(raw):
    assert parse_cw_start_difficulty_token(raw) is None


def test_resolve_entry_rank_from_enemy_difficulty_maps_exact_band():
    assert resolve_entry_rank_from_enemy_difficulty(39)["token"] == "A6-1"
    assert resolve_entry_rank_from_enemy_difficulty(51)["token"] == "A7-3"
    assert resolve_entry_rank_from_enemy_difficulty(108)["token"] == "A8-40"


@pytest.mark.parametrize("value", [4, 5, 9, 10, 58, 59, 60, 71, 84, 97])
def test_resolve_entry_rank_from_enemy_difficulty_rejects_unmapped_gap(value):
    assert resolve_entry_rank_from_enemy_difficulty(value) is None


def test_read_entry_enemy_difficulty_joins_split_digits_left_to_right():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "9", "box": {"left": 62, "top": 10, "width": 12, "height": 20}},
            {"text": "3", "box": {"left": 40, "top": 12, "width": 12, "height": 20}},
        ]
    )

    assert read_entry_enemy_difficulty(runtime) == 39


def test_read_entry_enemy_difficulty_ignores_geometryless_noise_and_fails_on_gap_value():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "4", "box": {"left": 40, "top": 12, "width": 12, "height": 20}},
            {"text": "噪声"},
        ]
    )

    with pytest.raises(TrailError) as exc_info:
        read_entry_enemy_difficulty(runtime)

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "ocr_unmapped"
```

- [ ] **Step 2: 跑这些测试，确认 helper 还不存在或行为未实现**

Run:
`uv run pytest tests/test_cw_entry.py -k "parse_cw_start_difficulty_token or resolve_entry_rank_from_enemy_difficulty or read_entry_enemy_difficulty" -q --basetemp .trail/pytest-temp-cw-entry-helpers -p no:cacheprovider`

Expected:
- FAIL，报 `ImportError`、`AttributeError` 或断言失败。

- [ ] **Step 3: 写最小实现，补齐职级表和公开 token 解析**

```python
ENTRY_ENEMY_DIFFICULTY_REGION = {"from_x": 480, "from_y": 940, "to_x": 590, "to_y": 1005}
ENTRY_EXACT_DIFFICULTY_PATTERN = re.compile(r"^A(?P<rank>[0-8])-(?P<layer>\d+)$")
ENTRY_RANK_BANDS = [
    {"rank_code": "A0", "rank_name": "黑铁", "max_layer": 3, "start_difficulty": 1, "ordinal_start": 1},
    {"rank_code": "A1", "rank_name": "青铜", "max_layer": 3, "start_difficulty": 6, "ordinal_start": 4},
    {"rank_code": "A2", "rank_name": "翠钢", "max_layer": 3, "start_difficulty": 11, "ordinal_start": 7},
    {"rank_code": "A3", "rank_name": "钴银", "max_layer": 5, "start_difficulty": 16, "ordinal_start": 10},
    {"rank_code": "A4", "rank_name": "冰钛", "max_layer": 5, "start_difficulty": 23, "ordinal_start": 15},
    {"rank_code": "A5", "rank_name": "紫金", "max_layer": 7, "start_difficulty": 30, "ordinal_start": 20},
    {"rank_code": "A6", "rank_name": "投资大师", "max_layer": 7, "start_difficulty": 39, "ordinal_start": 27},
    {"rank_code": "A7", "rank_name": "资本帝王", "max_layer": 9, "start_difficulty": 49, "ordinal_start": 34},
    {
        "rank_code": "A8",
        "rank_name": "财富造物主",
        "max_layer": 40,
        "start_difficulty": 61,
        "ordinal_start": 43,
        "difficulty_values": tuple(range(61, 71)) + tuple(range(74, 84)) + tuple(range(87, 97)) + tuple(range(99, 109)),
    },
]


def parse_cw_start_difficulty_token(value: str) -> dict[str, object] | None:
    if value in {"lowest", "current", "highest"}:
        return {"kind": "preset", "token": value}
    match = ENTRY_EXACT_DIFFICULTY_PATTERN.fullmatch(value)
    if match is None:
        return None
    rank_code = f"A{match.group('rank')}"
    layer = int(match.group("layer"))
    for band in ENTRY_RANK_BANDS:
        if band["rank_code"] != rank_code:
            continue
        if not 1 <= layer <= band["max_layer"]:
            return None
        difficulty_values = band.get("difficulty_values") or tuple(
            range(band["start_difficulty"], band["start_difficulty"] + band["max_layer"])
        )
        return {
            "kind": "exact",
            "token": value,
            "rank_code": rank_code,
            "rank_name": band["rank_name"],
            "layer": layer,
            "target_enemy_difficulty": difficulty_values[layer - 1],
            "global_layer_ordinal": band["ordinal_start"] + layer - 1,
        }
    return None


def resolve_entry_rank_from_enemy_difficulty(value: int) -> dict[str, object] | None:
    for band in ENTRY_RANK_BANDS:
        difficulty_values = band.get("difficulty_values") or tuple(
            range(band["start_difficulty"], band["start_difficulty"] + band["max_layer"])
        )
        if value not in difficulty_values:
            continue
        layer = difficulty_values.index(value) + 1
        return {
            "token": f"{band['rank_code']}-{layer}",
            "rank_code": band["rank_code"],
            "rank_name": band["rank_name"],
            "layer": layer,
            "enemy_difficulty": value,
            "global_layer_ordinal": band["ordinal_start"] + layer - 1,
        }
    return None
```

- [ ] **Step 4: 实现固定区域 OCR 数字拼接，并统一抛 `CW_START_DIFFICULTY_RECOVERY_REQUIRED`**

```python
def _extract_box(piece: object) -> tuple[int, int, int, int] | None:
    if isinstance(piece, dict) and isinstance(piece.get("box"), dict):
        box = piece["box"]
        return int(box["left"]), int(box["top"]), int(box["width"]), int(box["height"])
    return None


def _build_difficulty_recovery_error(*, requested_difficulty: str | None, reason: str, target_enemy_difficulty: int | None = None, current_enemy_difficulty: int | None = None, after_input: bool = False) -> TrailError:
    error = TrailError(
        "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
        "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
    )
    error.data = {
        "requested_difficulty": requested_difficulty,
        "target_enemy_difficulty": target_enemy_difficulty,
        "current_enemy_difficulty": current_enemy_difficulty,
        "reason": reason,
        "page": "entry.new",
    }
    error.known_failure_after_save = bool(after_input)
    error.completed_after_side_effect = bool(after_input)
    return error


def read_entry_enemy_difficulty(runtime, *, requested_difficulty: str | None = None, target_enemy_difficulty: int | None = None, after_input: bool = False) -> int:
    pieces = runtime.ocr(capture=ENTRY_ENEMY_DIFFICULTY_REGION, ocr=OcrRequestConfig(ocr_mode="high", retry_high="never")) or []
    digit_runs: list[tuple[int, int, int, str]] = []
    for order, piece in enumerate(pieces):
        box = _extract_box(piece)
        if box is None:
            continue
        text = _read_ocr_piece(piece)
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            continue
        digit_runs.append((box[0], box[1], order, digits))
    if not digit_runs:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            reason="ocr_missing",
            after_input=after_input,
        )
    digits = "".join(item[3] for item in sorted(digit_runs))
    value = int(digits)
    if resolve_entry_rank_from_enemy_difficulty(value) is None:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            current_enemy_difficulty=value,
            reason="ocr_unmapped",
            after_input=after_input,
        )
    return value
```

- [ ] **Step 5: 跑 helper 测试，确认映射与 OCR 读取通过**

Run:
`uv run pytest tests/test_cw_entry.py -k "parse_cw_start_difficulty_token or resolve_entry_rank_from_enemy_difficulty or read_entry_enemy_difficulty" -q --basetemp .trail/pytest-temp-cw-entry-helpers-green -p no:cacheprovider`

Expected:
- PASS，`A7-3 -> 51`、`39 -> A6-1`、分裂数字拼接与空洞值 recovery 都通过。

- [ ] **Step 6: Commit**

```bash
git add trail/scenes/cw/entry.py tests/test_cw_entry.py
git commit -m "feat(cw): 新增精确职级映射与难度读取 helper"
```

## Task 3: 在 `entry.py` 落地 `AX-X` 精确调级状态机

**Files:**
- Modify: `trail/scenes/cw/entry.py`
- Test: `tests/test_cw_entry.py`
- Test: `tests/test_cw_portal.py`

- [ ] **Step 1: 先写失败测试，锁定 `AX-X` 的 `返回最高职级 -> 粗调 -> 细调` 动作链**

```python
def test_select_exact_difficulty_resets_to_highest_then_coarse_then_step(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []
            self._values = iter([49, 108, 63, 51])

        def locate(self, template: str, **kwargs):
            del kwargs
            if template == _asset("entry.difficulty.highest"):
                return _box("entry.difficulty.highest", left=1300, top=960)
            if template == _asset("entry.difficulty.lowest"):
                return _box("entry.difficulty.lowest", left=1600, top=960)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            value = next(self._values)
            return [{"text": str(value), "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    entry_module._select_difficulty(runtime, difficulty="A7-3")

    assert runtime.clicks[0] == (1320, 970)
    assert runtime.drags == [(960, 810, 960, 0)]
    assert runtime.clicks[-1] == (1620, 970)


def test_select_exact_difficulty_recovery_when_highest_button_missing(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def locate(self, template: str, **kwargs):
            del kwargs
            return None

        def ocr(self, **kwargs):
            return [{"text": "49", "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(Runtime(), difficulty="A8-1")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "highest_reset_unavailable"
```

- [ ] **Step 2: 跑红灯，确认 `AX-X` 还没真正驱动动作状态机**

Run:
`uv run pytest tests/test_cw_entry.py -k "select_exact_difficulty" -q --basetemp .trail/pytest-temp-cw-entry-exact-red -p no:cacheprovider`

Expected:
- FAIL，当前 `_select_difficulty()` 仍只识别 `current/highest/lowest`。

- [ ] **Step 3: 写最小实现，把 `_select_difficulty()` 分流到精确状态机**

```python
ENTRY_DIFFICULTY_SETTLE_SECONDS = 0.5
ENTRY_DIFFICULTY_COARSE_MAX_STEPS = 20
ENTRY_DIFFICULTY_FINE_MAX_STEPS = 12
ENTRY_DIFFICULTY_COARSE_START = (CW_WIDTH // 2, int(CW_HEIGHT * 0.75))
ENTRY_DIFFICULTY_COARSE_END = (CW_WIDTH // 2, 0)


def _read_current_entry_rank(runtime, *, requested_difficulty: str, target_enemy_difficulty: int | None, after_input: bool) -> dict[str, object]:
    value = read_entry_enemy_difficulty(
        runtime,
        requested_difficulty=requested_difficulty,
        target_enemy_difficulty=target_enemy_difficulty,
        after_input=after_input,
    )
    rank = resolve_entry_rank_from_enemy_difficulty(value)
    if rank is None:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            current_enemy_difficulty=value,
            reason="ocr_unmapped",
            after_input=after_input,
        )
    return rank


def _reset_entry_to_highest(runtime, *, requested_difficulty: str, target_enemy_difficulty: int) -> dict[str, object]:
    box = _locate(runtime, "entry.difficulty.highest")
    if box is None:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            reason="highest_reset_unavailable",
            after_input=False,
        )
    _click_box_center(runtime, box)
    _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)
    return _read_current_entry_rank(runtime, requested_difficulty=requested_difficulty, target_enemy_difficulty=target_enemy_difficulty, after_input=True)


def _coarse_reduce_entry_difficulty(runtime):
    runtime.drag_to(*ENTRY_DIFFICULTY_COARSE_START, *ENTRY_DIFFICULTY_COARSE_END)
    _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)


def _step_reduce_entry_difficulty(runtime):
    box = _locate(runtime, "entry.difficulty.lowest")
    if box is None:
        raise _build_difficulty_recovery_error(
            requested_difficulty=None,
            reason="step_arrow_missing",
            after_input=False,
        )
    _click_box_center(runtime, box)
    _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)


def _select_exact_difficulty(runtime, *, difficulty: str, parsed: dict[str, object]) -> None:
    current = _read_current_entry_rank(runtime, requested_difficulty=difficulty, target_enemy_difficulty=parsed["target_enemy_difficulty"], after_input=False)
    for _ in range(ENTRY_DIFFICULTY_COARSE_MAX_STEPS + ENTRY_DIFFICULTY_FINE_MAX_STEPS):
        if current["global_layer_ordinal"] == parsed["global_layer_ordinal"]:
            return
        if current["global_layer_ordinal"] < parsed["global_layer_ordinal"]:
            current = _reset_entry_to_highest(runtime, requested_difficulty=difficulty, target_enemy_difficulty=parsed["target_enemy_difficulty"])
            continue
        if current["global_layer_ordinal"] - parsed["global_layer_ordinal"] > 10:
            before = current["enemy_difficulty"]
            _coarse_reduce_entry_difficulty(runtime)
            current = _read_current_entry_rank(runtime, requested_difficulty=difficulty, target_enemy_difficulty=parsed["target_enemy_difficulty"], after_input=True)
            if current["enemy_difficulty"] == before:
                raise _build_difficulty_recovery_error(
                    requested_difficulty=difficulty,
                    target_enemy_difficulty=parsed["target_enemy_difficulty"],
                    current_enemy_difficulty=current["enemy_difficulty"],
                    reason="coarse_no_progress",
                    after_input=True,
                )
            continue
        before = current["enemy_difficulty"]
        _step_reduce_entry_difficulty(runtime)
        current = _read_current_entry_rank(runtime, requested_difficulty=difficulty, target_enemy_difficulty=parsed["target_enemy_difficulty"], after_input=True)
        if current["enemy_difficulty"] == before:
            raise _build_difficulty_recovery_error(
                requested_difficulty=difficulty,
                target_enemy_difficulty=parsed["target_enemy_difficulty"],
                current_enemy_difficulty=current["enemy_difficulty"],
                reason="step_no_progress",
                after_input=True,
            )
    raise _build_difficulty_recovery_error(
        requested_difficulty=difficulty,
        target_enemy_difficulty=parsed["target_enemy_difficulty"],
        current_enemy_difficulty=current["enemy_difficulty"],
        reason="iteration_budget_exhausted",
        after_input=True,
    )
```

- [ ] **Step 4: 补高层开始链测试，锁定 `entry.continue` / `boss_preview` / `invest` 仍不重选难度**

```python
def test_cw_start_entry_continue_keeps_recorded_exact_difficulty(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    runtime = StartRuntime(
        locate_results={_asset("entry.continue"): _box("entry.continue", left=200, top=300)},
        wait_results={
            _asset("entry.continue"): _box("entry.continue", left=200, top=300),
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
        },
        ocr_result=[{"text": "继续进度"}],
    )
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home", "mode": "continue", "difficulty": "A7-3", "battle_mode": "standard"},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-entry-continue-exact",
        mode="new",
        difficulty="current",
        battle_mode="overclock",
    )

    assert envelope["ok"] is True
    assert envelope["data"]["difficulty"] == "A7-3"
    assert runtime.clicks == [(300, 450), (220, 310), (320, 410)]
```

- [ ] **Step 5: 跑精确职级相关测试，确认动作链和 recorded truth 同时通过**

Run:
`uv run pytest tests/test_cw_entry.py tests/test_cw_portal.py -k "select_exact_difficulty or recorded_exact_difficulty" -q --basetemp .trail/pytest-temp-cw-entry-exact-green -p no:cacheprovider`

Expected:
- PASS，`AX-X` 的 reset/coarse/fine 动作链稳定。
- PASS，`entry.continue` / `boss_preview` / `invest` 不会误做重选。

- [ ] **Step 6: Commit**

```bash
git add trail/scenes/cw/entry.py tests/test_cw_entry.py tests/test_cw_portal.py
git commit -m "feat(cw): 支持精确职级调级状态机"
```

## Task 4: 把 `lowest` 纳入同一真值链，并保留稳定的已知恢复失败协议

**Files:**
- Modify: `trail/scenes/cw/entry.py`
- Modify: `trail/daemon/cw_service.py`
- Test: `tests/test_cw_entry.py`
- Test: `tests/test_cw_portal.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写失败测试，锁定 `lowest` 状态机与 request-status 语义**

```python
def test_select_lowest_recovers_immediately_when_arrow_present_but_ocr_missing(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []

        def locate(self, template: str, **kwargs):
            del kwargs
            if template == _asset("entry.difficulty.lowest"):
                return _box("entry.difficulty.lowest", left=1600, top=960)
            return None

        def ocr(self, **kwargs):
            return []

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(Runtime(), difficulty="lowest")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "ocr_missing"


def test_command_service_keeps_known_recovery_failure_completed_after_side_effect(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    def fake_start_cw(session, *, mode: str, difficulty: str, battle_mode: str, runtime):
        error = TrailError(
            "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
            "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
        )
        error.data = {
            "requested_difficulty": difficulty,
            "target_enemy_difficulty": 51,
            "current_enemy_difficulty": 49,
            "reason": "coarse_no_progress",
            "page": "entry.new",
        }
        error.known_failure_after_save = True
        error.completed_after_side_effect = True
        raise error

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.start_cw", fake_start_cw)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    request = DaemonRequest(
        request_id="req-cw-start-recovery-known-failure",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "A7-3",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)
    status = service.request_status("req-cw-start-recovery-known-failure")

    assert payload["ok"] is False
    assert payload["error"]["code"] == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert status["final_state"] == "completed"
    assert status["tainted"] is False
```

- [ ] **Step 2: 跑红灯，确认 `lowest` 还没接入当前真值链，known failure 协议也未锁定**

Run:
`uv run pytest tests/test_cw_entry.py tests/test_daemon_protocol.py -k "select_lowest_recovers_immediately_when_arrow_present_but_ocr_missing or keeps_known_recovery_failure_completed_after_side_effect" -q --basetemp .trail/pytest-temp-cw-lowest-red -p no:cacheprovider`

Expected:
- FAIL，`lowest` 仍会尝试旧逻辑。
- FAIL，daemon known failure 语义尚未被这类错误覆盖。

- [ ] **Step 3: 写最小实现，把 `lowest` 收口到与 `AX-X` 同一真值状态机**

```python
def _select_lowest_difficulty(runtime) -> None:
    for _ in range(ENTRY_DIFFICULTY_COARSE_MAX_STEPS + ENTRY_DIFFICULTY_FINE_MAX_STEPS):
        arrow_box = _locate(runtime, "entry.difficulty.lowest")
        if arrow_box is not None:
            current = _read_current_entry_rank(runtime, requested_difficulty="lowest", target_enemy_difficulty=None, after_input=False)
            if current["global_layer_ordinal"] - parse_cw_start_difficulty_token("A1-1")["global_layer_ordinal"] > 10:
                _coarse_reduce_entry_difficulty(runtime)
                continue
            _click_box_center(runtime, arrow_box)
            _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)
            try:
                current = _read_current_entry_rank(runtime, requested_difficulty="lowest", target_enemy_difficulty=None, after_input=True)
            except TrailError as error:
                raise error
            if _locate(runtime, "entry.difficulty.lowest") is None and current["token"] == "A0-1":
                return
            continue

        try:
            current = _read_current_entry_rank(runtime, requested_difficulty="lowest", target_enemy_difficulty=None, after_input=False)
        except TrailError:
            _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)
            current = _read_current_entry_rank(runtime, requested_difficulty="lowest", target_enemy_difficulty=None, after_input=False)
        if current["token"] == "A0-1":
            return
        if current["global_layer_ordinal"] - parse_cw_start_difficulty_token("A1-1")["global_layer_ordinal"] > 10:
            _coarse_reduce_entry_difficulty(runtime)
            continue
        raise _build_difficulty_recovery_error(
            requested_difficulty="lowest",
            current_enemy_difficulty=current["enemy_difficulty"],
            reason="lowest_arrow_missing_non_bottom",
            after_input=False,
        )

    raise _build_difficulty_recovery_error(
        requested_difficulty="lowest",
        reason="iteration_budget_exhausted",
        after_input=True,
    )
```

- [ ] **Step 4: 在 daemon known-failure 路径补测试并确认 `failed_before_side_effect` / `completed` 两种 request-status**

```python
def test_command_service_keeps_recovery_failure_failed_before_side_effect_without_input(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    def fake_start_cw(session, *, mode: str, difficulty: str, battle_mode: str, runtime):
        error = TrailError(
            "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
            "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
        )
        error.data = {
            "requested_difficulty": difficulty,
            "target_enemy_difficulty": 51,
            "current_enemy_difficulty": None,
            "reason": "ocr_missing",
            "page": "entry.new",
        }
        raise error

    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.start_cw", fake_start_cw)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)

    request = DaemonRequest(
        request_id="req-cw-start-recovery-pre-input",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.start",
        payload={
            "session_id": session.session_id,
            "mode": "new",
            "difficulty": "A7-3",
            "battle_mode": "standard",
        },
    )

    payload = command_service.handle(request)
    status = service.request_status("req-cw-start-recovery-pre-input")

    assert payload["ok"] is False
    assert payload["error"]["code"] == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False
```

- [ ] **Step 5: 跑 `lowest` 与 known failure 测试，确认恢复协议稳定**

Run:
`uv run pytest tests/test_cw_entry.py tests/test_cw_portal.py tests/test_daemon_protocol.py -k "lowest or recovery_failure" -q --basetemp .trail/pytest-temp-cw-lowest-green -p no:cacheprovider`

Expected:
- PASS，`lowest` 在有箭头、无箭头、近底部、真值缺失时都走唯一状态机。
- PASS，request-status 在 pre-input 与 post-input 两类已知恢复失败场景下分别稳定为 `failed_before_side_effect` 和 `completed`，且 `tainted=0`。

- [ ] **Step 6: Commit**

```bash
git add trail/scenes/cw/entry.py trail/daemon/cw_service.py tests/test_cw_entry.py tests/test_cw_portal.py tests/test_daemon_protocol.py
git commit -m "feat(cw): 统一 lowest 与精确职级恢复语义"
```

## Task 5: 同步 RPC、README、skill 与断言文档面

**Files:**
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `README.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-cw-entry/references/player-language-mapping.md`
- Modify: `skills/trail-cw-entry/references/confirmation-checklist.md`

- [ ] **Step 1: 先写失败测试，锁定成功输出仍保存公开 token，README/help/skill 不泄漏内部数值映射**

```python
def test_cw_start_renders_exact_rank_token_without_numeric_mapping(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "cw.start": build_success_response(
                request_id="req-cw-start-exact-rank",
                data={
                    "cards": [{"card_idx": 1, "portal_title": "Alpha", "portal_description": "Desc", "score": 0.99}],
                    "mode": "new",
                    "difficulty": "A7-3",
                    "battle_mode": "standard",
                    "stale": False,
                },
                screenshot=".trail/shots/req-cw-start-exact-rank.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "start", "--session", SESSION_ID, "--mode", "new", "--difficulty", "A7-3", "--battle-mode", "standard"])

    assert result.exit_code == 0
    assert "A7-3" in result.stdout
    assert "51" not in result.stdout


def test_readme_and_trail_cw_entry_skill_document_ax_x_without_numeric_mapping():
    readme = Path("README.md").read_text(encoding="utf-8")
    skill = Path("skills/trail-cw-entry/SKILL.md").read_text(encoding="utf-8")

    assert "lowest/current/highest/AX-X" in readme
    assert "A0-1..A0-3" in readme
    assert "A8-1..A8-40" in readme
    assert "A7-3 对应难度 51" not in readme
    assert "lowest/current/highest/AX-X" in skill
    assert "A7-3 对应难度 51" not in skill
```

- [ ] **Step 2: 跑红灯，确认 README、skill、RPC 断言还没同步到 `AX-X` 语义**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_skill_structure.py -k "exact_rank_token or document_ax_x" -q --basetemp .trail/pytest-temp-cw-docs-red -p no:cacheprovider`

Expected:
- FAIL，当前 README/skill/help 仍只有 `lowest/current/highest`。

- [ ] **Step 3: 更新 README 与 `trail-cw-entry` skill 的用户面说明**

```md
- `trail cw start --session <id> --mode new|continue --difficulty lowest|current|highest|AX-X --battle-mode standard|overclock`
- `AX-X` 例如 `A7-3`
- 可用范围：`A0-1..A0-3`、`A1-1..A1-3`、`A2-1..A2-3`、`A3-1..A3-5`、`A4-1..A4-5`、`A5-1..A5-7`、`A6-1..A6-7`、`A7-1..A7-9`、`A8-1..A8-40`
- 不在 README / skill 中展开 `AX-X -> enemy_difficulty` 数值映射
```

- [ ] **Step 4: 补 RPC / 输出断言，锁定成功响应与恢复错误的文档面**

```python
def test_cw_start_rpc_contract_forwards_exact_rank_difficulty(cli_runner, fake_daemon_client):
    client = fake_daemon_client({"cw.start": build_success_response(request_id="req-cw-start", data={"cards": [], "mode": "new", "difficulty": "A7-3", "battle_mode": "standard", "stale": False})})

    result = cli_runner.invoke(app, ["cw", "start", "--session", SESSION_ID, "--mode", "new", "--difficulty", "A7-3", "--battle-mode", "standard"])

    assert result.exit_code == 0
    assert client.calls[0]["method"] == "cw.start"
    assert client.calls[0]["payload"]["difficulty"] == "A7-3"


def test_render_output_cw_start_recovery_failure_keeps_context_only_in_envelope():
    payload = {
        "ok": False,
        "data": {
            "requested_difficulty": "A7-3",
            "target_enemy_difficulty": 51,
            "current_enemy_difficulty": 49,
            "reason": "coarse_no_progress",
            "page": "entry.new",
        },
        "request_id": "req-cw-start-recovery",
        "error": {
            "code": "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
            "message": "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
        },
        "screenshot": ".trail/shots/req-cw-start-recovery.png",
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-cw-start-recovery"},
    }

    lines = render_output("cw.start", payload).splitlines()

    assert lines[0] == "fail cw.start code=CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert "request id=req-cw-start-recovery" in lines[1]
    assert all("requested_difficulty" not in line for line in lines)
    assert all("coarse_no_progress" not in line for line in lines)
```

- [ ] **Step 5: 跑文档与契约测试，确认对外口径完全统一**

Run:
`uv run pytest tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_skill_structure.py tests/test_atomic_commands.py -k "cw_start or exact_rank or document_ax_x" -q --basetemp .trail/pytest-temp-cw-docs-green -p no:cacheprovider`

Expected:
- PASS，CLI/RPC/README/skill 全部只展示 `lowest/current/highest/AX-X` 与 `A0-1..A8-40`。
- PASS，成功输出保存公开 token，失败输出不泄漏内部 `reason` 或数值映射。

- [ ] **Step 6: Commit**

```bash
git add tests/test_cw_rpc_contracts.py tests/test_output_rendering.py README.md skills/trail-cw-entry/SKILL.md skills/trail-cw-entry/references/player-language-mapping.md skills/trail-cw-entry/references/confirmation-checklist.md
git commit -m "docs(cw): 同步精确职级难度文档与契约"
```

## Self-Review

### Spec coverage
- `--difficulty` 支持 `AX-X`：Task 1。
- 固定区域 OCR `480,940,590,1005`：Task 2。
- `enemy_difficulty -> AX-X -> global_layer_ordinal`：Task 2。
- 精确职级 `返回最高职级 / 粗调 / 细调`：Task 3。
- `lowest` 的粗调/细调与箭头缺失恢复：Task 4。
- `CW_START_DIFFICULTY_RECOVERY_REQUIRED` 的 request-status / tainted 契约：Task 4。
- README/help/skill/test 同步：Task 1 与 Task 5。

### Placeholder scan
- 本计划没有 `TODO` / `TBD` / “实现细节后补” 之类占位内容。
- 每个任务都给了明确文件路径、测试代码、实现代码骨架、运行命令与预期结果。

### Type consistency
- 对外 `difficulty` 一直是公开 token `lowest/current/highest/AX-X`。
- 内部解析对象统一使用 `rank_code`、`rank_name`、`layer`、`target_enemy_difficulty`、`global_layer_ordinal`。
- 运行时恢复错误统一使用 `CW_START_DIFFICULTY_RECOVERY_REQUIRED`。

## Execution Handoff

用户已经预先选择：使用项目内 worktree 的 Subagent-Driven 方式执行本计划。

下一步直接进入：

1. 使用 `superpowers:subagent-driven-development`
2. 在 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-exact-rank-difficulty` 中按 Task 1 开始实现
3. 每个任务完成后都走一次并发 review，再进入下一个任务
