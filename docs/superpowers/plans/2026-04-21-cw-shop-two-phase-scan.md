# CW Shop Two-Phase Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `cw.shop.scan` 在一次命令中先退回无弹窗备战页读取队伍容量，再重开商店读取商品与数值，并保持 `cw.shop.status` / `cw.shop.buy_slot` / renderer 协议不被误伤。

**Architecture:** 保留现有“纯商店页 OCR”能力，新增一个只给 `cw.shop.scan` 使用的 scan 专用 orchestrator / snapshot reader，把“点击空白 -> 等待稳定 -> 读队伍容量 -> 点商店 -> 等待稳定 -> 读商品页”的副作用隔离开。daemon 层把 `cw.shop.scan` 提升为 mutation 命令，确保点击后失败也能走 request journal、tainted 与 recover 链路。

**Tech Stack:** Python 3.12、Typer CLI、daemon RPC、RapidOCR、pytest、工作区内 `\.trail\shots` 图像夹具。

---

## 文件边界

- Modify: `trail/scenes/cw/shop.py`
  纯商店页 OCR 逻辑、等级解析、scan 专用 orchestrator、队伍容量解析、点击点与 OCR 区域都收在这里。
- Modify: `trail/daemon/cw_service.py`
  只让 `cw.shop.scan` 接上新的 scan 专用 reader；`cw.shop.buy_slot` 继续复用纯商店页 scanner。
- Modify: `trail/daemon/command_service.py`
  把 `cw.shop.scan` 纳入 mutation 语义，确保副作用失败时产生 unknown-result / tainted / recover。
- Modify: `tests/test_cw_shop.py`
  单测和 orchestration 测试主战场：tuple OCR、level 解析、队伍容量解析、scan 顺序、状态收敛、mutation 失败链路。
- Modify: `tests/test_output_rendering.py`
  守住 `cw.shop.scan` 成功/失败文本协议。
- Modify: `tests/test_cw_rpc_contracts.py`
  守住 CLI/RPC 层的 `cw shop scan` 文本契约。
- Modify: `skills/trail-cw-shop/SKILL.md`
  同步 `scan` 的新前提：命令内部会先退回无弹窗页再重开商店，不再要求调用者先保证商店已开。

> 说明：本 worktree 是从当前 HEAD 创建的，不包含主工作区里尚未提交的本地 shop OCR 修复，因此第一个任务先把“tuple 文本 / level 解析”这部分基础修复补齐到 worktree，再继续做两段式扫描。

### Task 1: 补齐纯商店页 OCR 基线

**Files:**
- Modify: `trail/scenes/cw/shop.py`
- Modify: `tests/test_cw_shop.py`

- [ ] **Step 1: 先写失败测试，锁住 tuple 文本与等级解析**

```python
def test_parse_shop_items_uses_rapidocr_tuple_text_instead_of_confidence():
    raw_items = [
        [[0, 0], "黑塔", 0.9996806085109711],
        [[0, 0], "1", 0.9982701539993286],
        [[0, 0], "阿格莱雅", 0.9880169034004211],
        [[0, 0], "1", 0.9931700229644775],
    ]
    assert _parse_shop_items(raw_items) == (
        [{"name": "黑塔", "price": 1}, {"name": "阿格莱雅", "price": 1}],
        False,
    )


def test_parse_shop_level_prefers_level_text_over_progress_counter():
    raw_items = [
        [[0, 0], "购买经验", 0.9986],
        [[0, 0], "LV.", 0.9367],
        [[0, 0], "3", 0.9983],
        [[0, 0], "0/4", 0.9961],
    ]
    assert _parse_shop_level(raw_items, default=None) == 3


@pytest.mark.parametrize(
    "raw_items",
    [
        [[[0, 0], "LV.", 0.9367], [[0, 0], "0/4", 0.9961]],
        [[[0, 0], "LV.0/4", 0.9961]],
        [[[0, 0], "0", 0.9961]],
    ],
)
def test_parse_shop_level_rejects_progress_only_payloads(raw_items):
    assert _parse_shop_level(raw_items, default=None) is None
```

- [ ] **Step 2: 运行失败测试，确认当前基线确实缺这部分行为**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_cw_shop.py -k "rapidocr or progress_only_payloads or prefers_level_text" -q`

Expected: FAIL，至少会看到 `_read_ocr_text` / `_parse_shop_level` 相关断言不满足。

- [ ] **Step 3: 写最小实现，把纯商店页 scanner 修到当前主工作区已验证的基线**

```python
SHOP_LEVEL_REGION = _region(0.117, 0.815, 0.1825, 0.869)


def _read_ocr_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "value", "name"):
            value = item.get(key)
            if isinstance(value, str | int | float):
                return str(value)
        return ""
    if isinstance(item, (list, tuple)):
        if len(item) >= 2 and isinstance(item[1], str | int | float):
            return str(item[1])
        for value in item:
            if isinstance(value, str | int | float):
                return str(value)
        return ""
    if isinstance(item, str | int | float):
        return str(item)
    return ""


def _parse_shop_level(items: list[Any], *, default: int | None) -> int | None:
    normalized_texts = [re.sub(r"\s+", "", _read_ocr_text(item)) for item in items if _read_ocr_text(item).strip()]
    for normalized in normalized_texts:
        match = re.fullmatch(r"lv\.?([0-9]+)", normalized, re.IGNORECASE)
        if match is not None:
            return int(match.group(1))
    for index, normalized in enumerate(normalized_texts[:-1]):
        if re.fullmatch(r"lv\.?", normalized, re.IGNORECASE):
            next_text = normalized_texts[index + 1]
            if re.fullmatch(r"\d+", next_text):
                return int(next_text)
    digit_texts = [text for text in normalized_texts if re.fullmatch(r"\d+", text)]
    if len(digit_texts) == 1 and int(digit_texts[0]) > 0 and not any("/" in text for text in normalized_texts):
        return int(digit_texts[0])
    return default


def build_cw_shop_scanner(runtime) -> ShopScanner:
    def scanner() -> tuple[list[dict[str, Any]], int | None, int | None, bool, int | None]:
        items, reserve_full = _parse_shop_items(runtime.ocr(capture=SHOP_SCAN_REGION))
        coins = _parse_first_int(runtime.ocr(capture=SHOP_COINS_REGION) or [], default=0)
        level = _parse_shop_level(runtime.ocr(capture=SHOP_LEVEL_REGION) or [], default=None)
        max_team_size = _parse_last_int(runtime.ocr(capture=SHOP_MAX_TEAM_SIZE_REGION) or [], default=None)
        return items, coins, level, reserve_full, max_team_size
```

- [ ] **Step 4: 跑纯商店页回归，确认 worktree 已达到当前已验证基线**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_cw_shop.py -q`

Expected: PASS，`tests/test_cw_shop.py` 全绿。

### Task 2: 新增 `cw.shop.scan` 专用两段式 orchestrator

**Files:**
- Modify: `trail/scenes/cw/shop.py`
- Modify: `tests/test_cw_shop.py`

- [ ] **Step 1: 先写失败测试，锁定 scan 顺序、状态收敛和不污染纯 scanner**

```python
def test_build_cw_shop_scan_snapshot_reader_closes_then_reopens_before_scanning():
    runtime = RuntimeStub()
    reader = build_cw_shop_scan_snapshot_reader(runtime)
    snapshot = reader()
    assert runtime.clicks == [SHOP_SCAN_RESET_POINT, SHOP_OPEN_POINT]
    assert runtime.ocr_calls == [SHOP_TEAM_SIZE_REGION, SHOP_SCAN_REGION, SHOP_COINS_REGION, SHOP_LEVEL_REGION]
    assert snapshot["opened"] is True
    assert snapshot["stale"] is False
    assert snapshot["max_team_size"] == 3


def test_build_cw_shop_scanner_remains_shop_page_only_for_buy_confirmation():
    runtime = RuntimeStub()
    scanner = build_cw_shop_scanner(runtime)
    scanner()
    assert runtime.clicks == []


def test_scan_cw_shop_converges_to_same_snapshot_from_opened_or_closed_start(tmp_path):
    from tests.conftest import build_fake_cw_session
    session = build_fake_cw_session(tmp_path)
    payload = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 2,
        "level": 3,
        "reserve_full": False,
        "max_team_size": 3,
    }
    opened = scan_cw_shop(session, scanner=lambda: dict(payload))
    session.scene_state["cw"]["shop"] = {"opened": False, "stale": True}
    closed = scan_cw_shop(session, scanner=lambda: dict(payload))
    assert opened.scene_state["cw"]["shop"] == closed.scene_state["cw"]["shop"]
    assert opened.scene_state["cw"]["shop"]["opened"] is True
```

- [ ] **Step 2: 跑失败测试，确认当前还没有专用 orchestrator**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_cw_shop.py -k "scan_snapshot_reader or converges_to_same_snapshot or remains_shop_page_only" -q`

Expected: FAIL，缺少 `build_cw_shop_scan_snapshot_reader()` 或顺序/状态断言不满足。

- [ ] **Step 3: 写最小实现，新增 scan 专用 reader，但保留纯商店页 scanner 给其它命令复用**

```python
SHOP_SCAN_RESET_POINT = _point(0.5, 0.55)
SHOP_TEAM_SIZE_REGION = _region(0.4479, 0.1760, 0.5625, 0.3056)
SHOP_SCAN_SETTLE_SECONDS = 0.35


def _parse_shop_team_size(items: list[Any], *, default: int | None) -> int | None:
    texts = [_read_ocr_text(item).strip() for item in items if _read_ocr_text(item).strip()]
    for text in texts:
        match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
        if match is not None:
            return int(match.group(2))
    return default


def build_cw_shop_scan_snapshot_reader(runtime):
    pure_shop_scanner = build_cw_shop_scanner(runtime)

    def reader() -> dict[str, Any]:
        runtime.click_point(*SHOP_SCAN_RESET_POINT)
        sleep(SHOP_SCAN_SETTLE_SECONDS)
        max_team_size = _parse_shop_team_size(runtime.ocr(capture=SHOP_TEAM_SIZE_REGION) or [], default=None)
        runtime.click_point(*SHOP_OPEN_POINT)
        sleep(SHOP_SCAN_SETTLE_SECONDS)
        items, coins, level, reserve_full, _ = pure_shop_scanner()
        return {
            "opened": True,
            "items": items,
            "coins": coins,
            "level": level,
            "reserve_full": reserve_full,
            "max_team_size": max_team_size,
        }

    return reader


def scan_cw_shop(session: SessionModel, *, scanner) -> SessionModel:
    cw_state = ensure_cw_state(session)
    scanned = scanner()
    snapshot = _build_shop_snapshot(
        cw_state,
        items=scanned["items"],
        coins=scanned["coins"],
        level=scanned["level"],
        reserve_full=scanned["reserve_full"],
        max_team_size=scanned.get("max_team_size"),
    )
    snapshot["opened"] = bool(scanned.get("opened", True))
    cw_state["shop"] = snapshot
    return session
```

- [ ] **Step 4: 用用户给的两张图做 image-backed 最小复现**

Run: `uv run python -c "from pathlib import Path; from PIL import Image; from trail.runtime.operator import RapidOcrAdapter; from trail.runtime.ocr_config import OcrRequestConfig; from trail.scenes.cw.shop import SHOP_SCAN_REGION, SHOP_COINS_REGION, SHOP_LEVEL_REGION, SHOP_TEAM_SIZE_REGION, _parse_shop_items, _parse_first_int, _parse_shop_level, _parse_shop_team_size; adapter=RapidOcrAdapter(); cfg=OcrRequestConfig(provider='cpu', ocr_mode='high', retry_high='never'); open_img=Image.open(Path(r'C:\Users\34404\source\repos\trail-cli\.trail\shots\2033ad2b66ff4947b0b6644f94f3a6c1-f6e3c2d5f4.jpg')).convert('RGB'); closed_img=Image.open(Path(r'C:\Users\34404\source\repos\trail-cli\.trail\shots\3e2fae9b5be74f99bbfce0953d9d1ec7-7ed1d69ed6.jpg')).convert('RGB'); print('items', _parse_shop_items(adapter.run(open_img.crop((SHOP_SCAN_REGION['from_x'], SHOP_SCAN_REGION['from_y'], SHOP_SCAN_REGION['to_x'], SHOP_SCAN_REGION['to_y'])), ocr=cfg).pieces)); print('coins', _parse_first_int(adapter.run(open_img.crop((SHOP_COINS_REGION['from_x'], SHOP_COINS_REGION['from_y'], SHOP_COINS_REGION['to_x'], SHOP_COINS_REGION['to_y'])), ocr=cfg).pieces, default=0)); print('level', _parse_shop_level(adapter.run(open_img.crop((SHOP_LEVEL_REGION['from_x'], SHOP_LEVEL_REGION['from_y'], SHOP_LEVEL_REGION['to_x'], SHOP_LEVEL_REGION['to_y'])), ocr=cfg).pieces, default=None)); print('team', _parse_shop_team_size(adapter.run(closed_img.crop((SHOP_TEAM_SIZE_REGION['from_x'], SHOP_TEAM_SIZE_REGION['from_y'], SHOP_TEAM_SIZE_REGION['to_x'], SHOP_TEAM_SIZE_REGION['to_y'])), ocr=cfg).pieces, default=None))"`

Required assertions:
- `C:\Users\34404\source\repos\trail-cli\.trail\shots\3e2fae9b5be74f99bbfce0953d9d1ec7-7ed1d69ed6.jpg` 能读出 `3/3 -> max_team_size=3`
- `C:\Users\34404\source\repos\trail-cli\.trail\shots\2033ad2b66ff4947b0b6644f94f3a6c1-f6e3c2d5f4.jpg` 能读出商品、价格、金币、等级
- 不会把 `1-1`、`80`、`Lv.3`、`0/4`、`1/2`、`1/3` 误判成 `max_team_size`

- [ ] **Step 5: 跑 shop 场景测试，确认两段式 reader 和纯 scanner 同时稳定**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_cw_shop.py -q`

Expected: PASS。

### Task 3: 把 `cw.shop.scan` 升级为 mutation 命令并补 failure 契约

**Files:**
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_cw_shop.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 先写失败测试，锁定 scan 的 mutation 语义和文本契约**

```python
def test_cw_shop_scan_marks_applied_but_not_persisted_when_click_side_effect_happened_before_failure(tmp_path, monkeypatch):
    assert envelope["ok"] is False
    assert envelope["error"] == {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"}
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True


def test_render_output_renders_cw_shop_scan_mutation_unknown_result():
    assert render_output("cw.shop.scan", payload).splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-scan-unknown",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-scan-unknown",
    ]


def test_cw_shop_scan_rpc_contract_keeps_success_shape():
    assert result.stdout.splitlines() == [
        "ok cw.shop.scan opened=1 stale=0 count=1",
        "shot path=.trail/shots/req-cw-shop-scan.png",
        "item idx=1 slot=1 name=银狼 cost=20",
    ]
```

- [ ] **Step 2: 跑失败测试，确认当前 `cw.shop.scan` 还停留在只读路径**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_cw_shop.py -k "shop_scan.*applied_but_not_persisted" -q`

Expected: FAIL，`cw.shop.scan` 尚未进入 mutation journal。

- [ ] **Step 3: 写最小实现，只改变 `cw.shop.scan` 的接线与语义**

```python
# trail/daemon/cw_service.py
"cw.shop.scan": lambda: scan_cw_shop(
    session,
    scanner=shop_scan_snapshot_reader_factory(runtime()),
).scene_state["cw"]["shop"],


# trail/daemon/command_service.py
CW_MUTATING_METHODS = {
    "cw.enter",
    "cw.start",
    "cw.portal.select",
    "cw.portal.refresh",
    "cw.portal.restart",
    "cw.guide.apply",
    "cw.slots.swap",
    "cw.slots.place_one",
    "cw.shop.open",
    "cw.shop.scan",
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


if request.method.startswith("cw."):
    service = self._session_service(request)
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
```

- [ ] **Step 4: 验证 success / failure 双契约**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_cw_shop.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -k "cw_shop_scan or cw.shop.scan" -q`

Expected: PASS，既守成功输出，也守带副作用失败输出。

### Task 4: 同步技能文档并做最终验收

**Files:**
- Modify: `skills/trail-cw-shop/SKILL.md`
- Modify: `docs/superpowers/specs/2026-04-21-cw-shop-two-phase-scan-design.md`（如实现细节需要回填）

- [ ] **Step 1: 更新 shop skill，说明 `scan` 内部会自动退回无弹窗页再重开商店**

```md
## 标准流程

1. 默认假设当前工作区已经通过 `trail start` 获得可用 `session_id`
2. 扫描当前商店：`trail cw shop scan --session <id>`
   - 该命令会先退回无弹窗备战页读取队伍容量，再自动重新打开商店读取商品内容
3. 如需购买，使用显式槽位和角色名：`trail cw shop buy-slot --session <id> --slot <n> --expect <角色>`
```

- [ ] **Step 2: 如实现和 spec 有偏差，立即回填 spec；如 README 不改，写清“不需要改 README”的原因**

```md
- README 无需改动：`cw.shop.scan` 的默认输出字段不变，只是内部扫描路径变成两段式。
```

- [ ] **Step 3: 跑最终验证集**

Run: `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_cw_shop.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -q`

Expected: PASS。

- [ ] **Step 4: 做一次真实截图回归记录**

Use:
- `C:\Users\34404\source\repos\trail-cli\.trail\shots\2033ad2b66ff4947b0b6644f94f3a6c1-f6e3c2d5f4.jpg`
- `C:\Users\34404\source\repos\trail-cli\.trail\shots\3e2fae9b5be74f99bbfce0953d9d1ec7-7ed1d69ed6.jpg`

Checklist:
- 商品页图：能读到角色、价格、金币、等级
- 关闭商店图：能读到 `3/3`
- 合并后的最终 snapshot：`opened=True`、`stale=False`
- 失败路径：若点击后故意注入异常，CLI 文本仍带 `request id=` 与 `recover action=daemon.request_status request=<id>`

## 自检

- spec 覆盖：所有 spec 条款都已映射到 Task 1-4。
- 占位符扫描：计划内没有 `TODO/TBD/类似 Task N` 这类占位描述。
- 类型一致性：`build_cw_shop_scanner()` 保持纯 shop-page scanner；scan 专用 reader 单独命名，避免和 `cw.shop.buy_slot` 的确认扫描复用混淆。

## 执行说明

用户已经明确选择：**Subagent-Driven（推荐）**。

下一步直接在当前 worktree `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-shop-scan-two-phase` 中，使用 `superpowers:subagent-driven-development` 按 Task 1 -> Task 4 顺序执行；每个任务完成后先做代码审查，再进入下一任务。未经用户明确要求，不创建 git commit。
