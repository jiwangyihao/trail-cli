# CW Battle Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: First use superpowers:using-git-worktrees to create a project-local worktree, then use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `trail cw battle run` 作为货币战争 battle/settle 默认主入口，自动等待自动战斗结束、提取最关键的结算信息、处理结算翻页并回到下一个稳定阶段，同时把旧 battle/settle 原子命令降级到 advanced fallback。

**Architecture:** 在 scene 层新增 battle orchestrator，统一完成 battle-chain 分类、结算摘要解析与自动 `continue/next`；在 daemon 层只做路由、capture 和 request journal 收口；在 CLI/renderer/docs 层分别补上新命令、动态 transport timeout、battle-run 文本协议以及 README/skills 分层迁移。

**Tech Stack:** Python 3、Typer CLI、现有 Trail daemon RPC、pytest、README/skills 文案断言测试。

---

> 按当前仓库协作规则，未收到用户明确要求前不主动创建 git commit。本计划里的 checkpoint 以“目标测试通过 + diff 自检”代替 commit 步骤。
>
> 本计划的执行前提已由用户预先冻结：**必须先用 `using-git-worktrees` 在项目内创建 worktree，并用 `subagent-driven-development` 执行实现；不要改用 inline `executing-plans`。**

## 文件结构

### 新建文件

- `trail/scenes/cw/battle.py`
  - 负责 `cw.battle.run` 的 scene 层 orchestrator。
  - 提供 battle-chain 分类、结算摘要提取、自动 `continue/next`、稳定阶段判断。
- `tests/test_cw_battle_run.py`
  - battle.run 的 scene loop / parser 专属测试。
- `skills/trail-cw-battle-advanced/SKILL.md`
  - 只在 `battle.run` 报错或需要手工拆链时，教旧的 `battle start` / `battle continue` / `settle next`。

### 修改文件

- `trail/commands/cw.py`
  - 新增 `trail cw battle run --session <id> --timeout <seconds>`。
  - 调整 `CW_BATTLE_HELP`，把新命令定位成默认 battle 入口。
- `trail/daemon/client.py`
  - 为 `cw.battle.run` 引入命令级 response-timeout resolver。
  - 保持非 battle.run 命令继续走 `120s` 默认值。
- `trail/daemon/cw_service.py`
  - 路由 `cw.battle.run`。
  - 为 `cw.battle.start` 增加 `3.0s` capture delay。
- `trail/daemon/command_service.py`
  - 把 `cw.battle.run` 纳入 `CW_MUTATING_METHODS`。
- `trail/scenes/cw/stage.py`
  - 暴露 battle.run 可复用的 stage 失效 helper，统一 `stage.stale` 与 `session.last_stage` 清理语义。
- `trail/scenes/cw/events.py`
  - 把 battle.run 需要复用的 OCR/button helper 收口成 battle 模块可复用的形式。
- `trail/output/rendering.py`
  - 新增 `_render_cw_battle_run`，冻结首行字段顺序与 `info` 行形状。
- `tests/test_cw_rpc_contracts.py`
  - 新增 `cw battle run` CLI/RPC 契约。
- `tests/test_daemon_protocol.py`
  - 新增 battle.run daemon/service + client timeout 契约。
  - 新增 `cw.battle.start` 的 `3s` capture delay 回归。
- `tests/test_output_rendering.py`
  - 新增 `cw.battle.run` renderer goldens。
  - 更新既有 README/skills owner 边界断言。
- `tests/test_atomic_commands.py`
  - 补 `trail state dump --session <id> --format yaml` 的 battle.run 回读断言。
- `README.md`
  - 把 `trail cw battle run` 写成常规 battle 入口。
  - 增补“可能接近 10 分钟、外部 timeout 至少调到 11 分钟”提醒。
  - 说明 stdout 丢失后的 `trail state dump --session <id> --format yaml` 回读路径。
- `skills/trail-cw/SKILL.md`
  - `battle.run` 成为唯一默认 battle owner。
- `skills/trail-cw-events/SKILL.md`
  - 收缩为 `boss-preview confirm` + `event handle`。
- `skills/trail-hsr-advanced/SKILL.md`
  - 明确 stdout 丢失时走 `trail state dump --session <id> --format yaml`，并把 scene-local battle fallback 交给 `trail-cw-battle-advanced`。

## Task 0: 执行前置

**Files:**
- Modify: `docs/superpowers/plans/2026-04-20-cw-battle-run.md`

- [ ] **Step 1: 在实现前先创建项目内 worktree**

执行方式固定为：先用 `using-git-worktrees` 创建项目内 worktree，再开始任何代码任务。

Expected: 后续所有代码、测试、README、skills 修改都发生在新 worktree，而不是当前主工作区。

- [ ] **Step 2: 执行阶段固定使用 subagent-driven-development**

执行方式固定为：每个实现任务都走 `subagent-driven-development`，独立 review/验证任务尽量并行；不要把本计划改成 inline `executing-plans`。

Expected: 实现开始前，执行者已经明确这两个前提，不会在错误工作区或错误执行模式下开工。

## Task 1: 打通 CLI 命令面与 transport timeout

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/daemon/client.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写 `cw battle run` 的 CLI/RPC 契约失败用例**

在 `tests/test_cw_rpc_contracts.py` 追加 battle run 用例，先只冻结命令注册与 payload 透传，不提前绑定最终 renderer 形状：

```python
def test_cw_battle_run_forwards_timeout(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run",
                data={"status": "completed", "stage": "shop", "stale": False, "in_battle": False},
                screenshot=".trail/shots/req-cw-battle-run.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID, "--timeout", "570"])

    assert result.exit_code == 0
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 570}, tmp_path=tmp_path)
```

- [ ] **Step 2: 运行 battle run 的 CLI 契约测试，确认当前实现还不支持**

Run: `pytest tests/test_cw_rpc_contracts.py::test_cw_battle_run_forwards_timeout -v`

Expected: FAIL，报 `No such command 'run'` 或 `_assert_single_call` 未出现 `cw.battle.run`。

- [ ] **Step 3: 写 transport timeout resolver 的失败用例**

在 `tests/test_daemon_protocol.py` 新增 battle-run 专属 timeout 断言，既测纯 resolver，也测真实 `TrailDaemonClient.call()` 通路：

```python
import json
from pathlib import Path

import trail.daemon.client as client_module

from trail.daemon.client import TrailDaemonClient, send_daemon_request
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from tests.support.fake_daemon import build_success_response, write_ready_manifest


def test_resolve_response_timeout_defaults_for_battle_run():
    assert client_module.resolve_response_timeout("cw.battle.run", {"timeout": 570}) == 600.0
    assert client_module.resolve_response_timeout("cw.battle.run", {"timeout": 660}) == 690.0
    assert client_module.resolve_response_timeout("ocr.read", {}) == 120.0


def test_send_daemon_request_uses_battle_run_timeout(monkeypatch, tmp_path: Path):
    settimeouts: list[float] = []

    class Reader:
        def readline(self):
            return json.dumps(build_success_response(request_id="req-timeout", data={"status": "in_progress"}), ensure_ascii=False)
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def settimeout(self, value):
            settimeouts.append(value)
        def sendall(self, payload):
            pass
        def makefile(self, *args, **kwargs):
            return Reader()
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("trail.daemon.client.socket.create_connection", lambda *args, **kwargs: Connection())

    send_daemon_request(
        DaemonRequest(
            request_id="req-timeout",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id="session-1",
            verbose=False,
            method="cw.battle.run",
            payload={"timeout": 570},
        ),
        "token-1",
        endpoint="127.0.0.1:8765",
    )

    assert settimeouts == [600.0]


def test_trail_daemon_client_call_keeps_timeout_mapping_on_real_path(monkeypatch, tmp_path: Path):
    captured: list[float] = []
    write_ready_manifest(tmp_path / "daemon-home", endpoint="127.0.0.1:8765", token_value="token-1")

    class Transport:
        def __call__(self, request, token, *, endpoint):
            captured.append(client_module.resolve_response_timeout(request.method, request.payload))
            return build_success_response(request_id=request.request_id, data={"status": "completed"})

    client = TrailDaemonClient(workspace_root=tmp_path, daemon_home=tmp_path / "daemon-home", transport=Transport())

    client.call("cw.battle.run", {"timeout": 570}, session_id="session-1")
    client.call("ocr.read", {}, session_id="session-1")

    assert captured == [600.0, 120.0]
```

- [ ] **Step 4: 运行 timeout 测试，确认当前 transport 还是固定 120 秒**

Run: `pytest tests/test_daemon_protocol.py::test_resolve_response_timeout_defaults_for_battle_run tests/test_daemon_protocol.py::test_send_daemon_request_uses_battle_run_timeout tests/test_daemon_protocol.py::test_trail_daemon_client_call_keeps_timeout_mapping_on_real_path -v`

Expected: FAIL，当前还没有 `resolve_response_timeout()`，且 `TrailDaemonClient.call()` 也不会产生 `600.0`。

- [ ] **Step 5: 用最小实现补齐 CLI 命令与 timeout plumbing**

先在 `trail/commands/cw.py` 增加默认 timeout 与命令：

```python
DEFAULT_CW_BATTLE_RUN_TIMEOUT = 570
CW_BATTLE_HELP = "默认使用自动战斗处理 battle 到结算返回；旧 battle/settle 原子命令仅作 advanced fallback。"


@battle_app.command("run")
def cw_battle_run(
    session: str = typer.Option(..., "--session"),
    timeout: int = typer.Option(DEFAULT_CW_BATTLE_RUN_TIMEOUT, "--timeout"),
) -> None:
    _print_cw("cw.battle.run", session_id=session, payload={"timeout": timeout})
```

再在 `trail/daemon/client.py` 收口 timeout resolver，但不改 `DaemonTransport` 公共签名，避免把 battle 特性泄漏到所有 fake transport：

```python
SOCKET_RESPONSE_TIMEOUT_SECONDS = 120.0
BATTLE_RUN_TIMEOUT_GRACE_SECONDS = 30.0


def resolve_response_timeout(method: str, payload: dict[str, Any]) -> float:
    if method != "cw.battle.run":
        return SOCKET_RESPONSE_TIMEOUT_SECONDS
    timeout = payload.get("timeout")
    if not isinstance(timeout, int) or timeout <= 0:
        timeout = 570
    return float(timeout + BATTLE_RUN_TIMEOUT_GRACE_SECONDS)


class DaemonTransport(Protocol):
    def __call__(
        self,
        request: DaemonRequest,
        token: str,
        *,
        endpoint: str,
    ) -> dict[str, Any]:
        pass


def send_daemon_request(
    request: DaemonRequest,
    token: str,
    *,
    endpoint: str,
    server: Any = None,
) -> dict[str, Any]:
    body = {
        "request_id": request.request_id,
        "protocol_version": request.protocol_version,
        "workspace_root": request.workspace_root,
        "session_id": request.session_id,
        "verbose": request.verbose,
        "method": request.method,
        "payload": request.payload,
        "token": token,
    }
    if server is not None:
        return server.handle({**body, "endpoint": endpoint})
    host, port_text = endpoint.split(":", 1)
    with socket.create_connection((host, int(port_text)), timeout=5) as sock:
        sock.settimeout(resolve_response_timeout(request.method, request.payload))
        sock.sendall(json.dumps(body, ensure_ascii=False).encode("utf-8") + b"\n")
        with sock.makefile("r", encoding="utf-8") as reader:
            return json.loads(reader.readline())
```

- [ ] **Step 6: 回跑 CLI 与 transport 相关测试，确认新命令面已打通**

Run: `pytest tests/test_cw_rpc_contracts.py::test_cw_battle_run_forwards_timeout tests/test_daemon_protocol.py::test_resolve_response_timeout_defaults_for_battle_run tests/test_daemon_protocol.py::test_send_daemon_request_uses_battle_run_timeout tests/test_daemon_protocol.py::test_trail_daemon_client_call_keeps_timeout_mapping_on_real_path -v`

Expected: PASS。

## Task 2: 新建 scene 层 battle classifier 与结算摘要 parser

**Files:**
- Create: `trail/scenes/cw/battle.py`
- Modify: `trail/scenes/cw/events.py`
- Modify: `trail/scenes/cw/stage.py`
- Test: `tests/test_cw_battle_run.py`

- [ ] **Step 1: 先写 parser/classifier 的失败用例**

先在 `tests/test_cw_battle_run.py` 文件头补最小测试支架，再新增 battle-only 锚点、settle 摘要、次级字段缺失三组最小测试：

```python
from pathlib import Path

from trail.scenes.cw.battle import classify_cw_battle_page, parse_cw_settlement_summary
from trail.session.models import SessionModel


class FakeRuntime:
    def __init__(self, *, ocr_result=None, ocr_map=None):
        self.ocr_result = list(ocr_result or [])
        self.ocr_map = dict(ocr_map or {})

    def ocr(self, capture=None):
        if capture == "headline":
            return self.ocr_map.get("headline", [])
        if capture == "round":
            return self.ocr_map.get("round", [])
        if capture == "stats":
            return self.ocr_map.get("stats", [])
        return self.ocr_result

    def locate(self, template):
        return None


def build_session(*, stage: dict | None = None) -> SessionModel:
    return SessionModel(
        session_id="s" * 32,
        workspace=Path(".trail/sessions"),
        window_binding={"title": "崩坏：星穹铁道", "hwnd": 1},
        created_at="2026-04-20T00:00:00+00:00",
        scene_state={"cw": {"stage": stage or {"stale": True}}},
    )


def test_classify_cw_battle_page_requires_positive_battle_anchor():
    runtime = FakeRuntime(ocr_result=[{"text": "随机页面"}])
    assert classify_cw_battle_page(runtime, session=None) == "unknown"


def test_parse_cw_settlement_summary_reads_stable_fields():
    runtime = FakeRuntime(
        ocr_map={
            "headline": [{"text": "挑战成功"}],
            "round": [{"text": "1-1 奖励"}],
            "stats": [{"text": "小队生命值 82"}, {"text": "获得金币总览 4"}, {"text": "获取经验 +2"}],
        }
    )
    assert parse_cw_settlement_summary(runtime) == {
        "result": "win",
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }


def test_parse_cw_settlement_summary_omits_missing_secondary_fields():
    runtime = FakeRuntime(ocr_map={"headline": [{"text": "挑战失败"}]})
    assert parse_cw_settlement_summary(runtime) == {"result": "lose", "settle_text": "挑战失败"}
```

- [ ] **Step 2: 跑 parser/classifier 测试，确认 battle 模块尚不存在**

Run: `pytest tests/test_cw_battle_run.py -k "classify or settlement" -v`

Expected: FAIL，导入 `trail.scenes.cw.battle` 失败。

- [ ] **Step 3: 创建 `trail/scenes/cw/battle.py`，先补最小 parser/classifier**

先落 battle 模块的骨架，保持 dict 风格，不额外引入 dataclass：

```python
from __future__ import annotations

import re

from trail.core.errors import TrailError
from trail.scenes.cw.stage import build_cw_stage_detector, mark_cw_stage_stale

DEFAULT_CW_BATTLE_RUN_TIMEOUT = 570
SETTLEMENT_STAGE_VALUE = "settle"
STABLE_BATTLE_RETURN_STAGES = {"shop", "replenish", "invest", "encounter", "fortune", "event", "preparation"}


def _joined_ocr_text(runtime, *, capture=None) -> str:
    return "".join(str(piece.get("text") if isinstance(piece, dict) else piece) for piece in (runtime.ocr(capture=capture) or []))


def _has_battle_start(runtime) -> bool:
    return any(keyword in _joined_ocr_text(runtime) for keyword in ("开始战斗", "开始挑战"))


def _has_settlement_entry(runtime) -> bool:
    return any(keyword in _joined_ocr_text(runtime, capture="headline") for keyword in ("挑战成功", "挑战失败", "继续挑战"))


def _has_settlement_followup(runtime) -> bool:
    return any(keyword in _joined_ocr_text(runtime) for keyword in ("下一步", "下一页"))


def _has_game_over(runtime) -> bool:
    return any(keyword in _joined_ocr_text(runtime) for keyword in ("游戏结束", "本局结束"))


def _has_positive_battle_anchor(runtime) -> bool:
    return any(keyword in _joined_ocr_text(runtime) for keyword in ("自动战斗", "倍速", "暂停"))


def _read_settle_headline(runtime) -> str | None:
    text = _joined_ocr_text(runtime, capture="headline")
    if "挑战成功" in text:
        return "挑战成功"
    if "挑战失败" in text:
        return "挑战失败"
    return None


def _read_optional_settlement_metrics(runtime) -> dict[str, object]:
    joined = _joined_ocr_text(runtime, capture="stats")
    summary: dict[str, object] = {}
    if round_match := re.search(r"(\d+-\d+)", _joined_ocr_text(runtime, capture="round")):
        summary["round"] = round_match.group(1)
    if hp_match := re.search(r"小队生命值\s*(\d+)", joined):
        summary["hp"] = int(hp_match.group(1))
    if coins_match := re.search(r"获得金币总览\s*(\d+)", joined):
        summary["coins"] = int(coins_match.group(1))
    if exp_match := re.search(r"获取经验\s*\+?(\d+)", joined):
        summary["exp"] = int(exp_match.group(1))
    return summary


def classify_cw_battle_page(runtime, *, session) -> str:
    if _has_battle_start(runtime):
        return "battle_start"
    if _has_settlement_entry(runtime):
        return "settle_entry"
    if _has_settlement_followup(runtime):
        return "settle_followup"
    if _has_game_over(runtime):
        return "game_over"
    stage = build_cw_stage_detector(runtime)()
    if stage is not None:
        return "stable_stage"
    if _has_positive_battle_anchor(runtime):
        return "battle_progress"
    return "unknown"


def parse_cw_settlement_summary(runtime) -> dict[str, object]:
    settle_text = _read_settle_headline(runtime)
    if settle_text not in {"挑战成功", "挑战失败"}:
        raise TrailError("CW_SETTLEMENT_UNREADABLE", "unable to read battle settlement result")
    summary = {
        "result": "win" if settle_text == "挑战成功" else "lose",
        "settle_text": settle_text,
    }
    summary.update(_read_optional_settlement_metrics(runtime))
    return summary
```

在 `trail/scenes/cw/stage.py` 先补一个 battle 可复用的失效 helper，再在 `trail/scenes/cw/events.py` 把 battle 模块需要复用的 OCR/button helper 整理成 battle.py 可直接 import 的函数。

```python
def mark_cw_stage_stale(session: SessionModel) -> None:
    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = None
```

- [ ] **Step 4: 回跑 parser/classifier 测试，确认 battle 摘要的最小语义已经成立**

Run: `pytest tests/test_cw_battle_run.py -k "classify or settlement" -v`

Expected: PASS。

## Task 3: 实现 battle loop、daemon 集成与旧命令 3 秒截图延迟

**Files:**
- Modify: `trail/scenes/cw/battle.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_atomic_commands.py`
- Test: `tests/test_cw_battle_run.py`

- [ ] **Step 1: 先写 battle loop 的失败用例**

在 `tests/test_cw_battle_run.py` 追加 loop 行为测试，先把脚本化 runtime 支架写完整，再覆盖 start、settle timeout、battle timeout、stable stage 与 game_over：

```python
from trail.scenes.cw.battle import run_cw_battle


class ScriptedBattleRuntime:
    def __init__(self, *, states, settlement=None, stable_stage=None):
        self.states = list(states)
        self.settlement = settlement or {}
        self.stable_stage = stable_stage
        self.clicked: list[str] = []
        self.index = 0

    def current_state(self):
        return self.states[min(self.index, len(self.states) - 1)]

    def advance(self):
        if self.index < len(self.states) - 1:
            self.index += 1

    def tick(self):
        self.advance()

    def click_point(self, x, y):
        self.clicked.append(f"{x},{y}")
        self.advance()

    def ocr(self, capture=None):
        state = self.current_state()
        if capture == "headline" and state.startswith("settle"):
            return [{"text": self.settlement.get("settle_text", "挑战成功")}]
        if capture == "round" and state.startswith("settle") and self.settlement.get("round"):
            return [{"text": f'{self.settlement["round"]} 奖励'}]
        if capture == "stats" and state.startswith("settle"):
            pieces = []
            if "hp" in self.settlement:
                pieces.append({"text": f'小队生命值 {self.settlement["hp"]}'})
            if "coins" in self.settlement:
                pieces.append({"text": f'获得金币总览 {self.settlement["coins"]}'})
            if "exp" in self.settlement:
                pieces.append({"text": f'获取经验 +{self.settlement["exp"]}'})
            return pieces
        if state == "battle_progress":
            return [{"text": "自动战斗"}]
        if state == "battle_start":
            return [{"text": "开始战斗"}]
        if state == "settle_followup":
            return [{"text": "下一步"}]
        if state == "game_over":
            return [{"text": "游戏结束"}]
        return []

    def locate(self, template):
        return None


def test_run_cw_battle_clicks_start_then_returns_completed_summary(monkeypatch):
    runtime = ScriptedBattleRuntime(
        states=["battle_start", "battle_progress", "settle_entry", "stable_stage"],
        settlement={"result": "win", "round": "1-1", "hp": 82, "coins": 4, "exp": 2, "settle_text": "挑战成功"},
        stable_stage="shop",
    )
    session = build_session(stage={"stale": True})
    monkeypatch.setattr("trail.scenes.cw.battle.build_cw_stage_detector", lambda runtime: (lambda: runtime.stable_stage))
    monkeypatch.setattr("trail.scenes.cw.battle.sleep", lambda _seconds: runtime.tick())

    result = run_cw_battle(session, runtime=runtime, timeout=570)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "shop",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }


def test_run_cw_battle_returns_settle_timeout_summary_when_budget_exhausted():
    runtime = ScriptedBattleRuntime(
        states=["settle_entry", "settle_followup", "settle_followup"],
        settlement={"result": "win", "round": "1-1", "hp": 82, "coins": 4, "exp": 2, "settle_text": "挑战成功"},
    )
    session = build_session(stage={"stale": True})

    result = run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["stage"] == "settle"
    assert result["in_battle"] is False


def test_run_cw_battle_finishes_when_starting_from_settle_followup(monkeypatch):
    runtime = ScriptedBattleRuntime(states=["settle_followup", "stable_stage"], settlement={"result": "win", "settle_text": "挑战成功"}, stable_stage="shop")
    session = build_session(stage={"stale": True})
    monkeypatch.setattr("trail.scenes.cw.battle.build_cw_stage_detector", lambda runtime: (lambda: runtime.stable_stage))

    result = run_cw_battle(session, runtime=runtime, timeout=570)

    assert result["status"] == "completed"
    assert result["stage"] == "shop"


def test_run_cw_battle_returns_in_battle_timeout_when_only_progress_seen():
    runtime = ScriptedBattleRuntime(states=["battle_progress", "battle_progress", "battle_progress"])
    session = build_session(stage={"stale": True})

    result = run_cw_battle(session, runtime=runtime, timeout=1)

    assert result == {"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": 1}


def test_run_cw_battle_returns_game_over_when_chain_ends():
    runtime = ScriptedBattleRuntime(states=["settle_entry", "game_over"], settlement={"result": "lose", "settle_text": "挑战失败"})
    session = build_session(stage={"stale": True})

    result = run_cw_battle(session, runtime=runtime, timeout=570)

    assert result["status"] == "completed"
    assert result["stage"] == "game_over"
```

- [ ] **Step 2: 运行 loop 测试，确认 orchestrator 还没完成**

Run: `pytest tests/test_cw_battle_run.py -k "run_cw_battle" -v`

Expected: FAIL，当前 `run_cw_battle()` 未实现或返回体不完整。

- [ ] **Step 3: 在 battle 模块里补齐 loop，并把结果写回 session stage**

先把最小 loop 与 stage 写回落好：

```python
from time import monotonic, sleep

from trail.core.errors import TrailError
from trail.scenes.cw.events import build_cw_battle_continuer, build_cw_battle_starter, build_cw_settle_continuer
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import build_cw_stage_detector


def _start_battle(runtime) -> None:
    build_cw_battle_starter(runtime)()


def _continue_after_settlement(runtime) -> None:
    build_cw_battle_continuer(runtime)()


def _advance_settlement_page(runtime) -> None:
    build_cw_settle_continuer(runtime)()


def run_cw_battle(session, *, runtime, timeout: int) -> dict[str, object]:
    STABLE_BATTLE_RETURN_STAGES = {"shop", "replenish", "invest", "encounter", "fortune", "event", "preparation"}

    def mark_stale() -> None:
        ensure_cw_state(session)["stage"] = {"stale": True}
        session.last_stage = None

    started_chain = False
    summary: dict[str, object] = {}
    deadline = monotonic() + timeout

    while True:
        state = classify_cw_battle_page(runtime, session=session)
        if state == "unknown" and not started_chain:
            raise TrailError("CW_BATTLE_STATE_UNKNOWN", "unable to confirm battle chain state")
        if state == "battle_start":
            _start_battle(runtime)
            started_chain = True
            continue
        if state == "battle_progress":
            started_chain = True
        elif state == "settle_entry":
            started_chain = True
            summary = parse_cw_settlement_summary(runtime)
            _continue_after_settlement(runtime)
            continue
        elif state == "settle_followup":
            started_chain = True
            _advance_settlement_page(runtime)
            continue
        elif state == "game_over":
            ensure_cw_state(session)["stage"] = {"value": "game_over", "stale": False}
            session.last_stage = {"scene": "cw", "value": "game_over"}
            return {**summary, "status": "completed", "stage": "game_over", "stale": False, "in_battle": False}
        elif state == "stable_stage":
            stage = build_cw_stage_detector(runtime)()
            if stage not in STABLE_BATTLE_RETURN_STAGES:
                raise TrailError("CW_BATTLE_STATE_UNKNOWN", f"unsupported battle return stage: {stage}")
            ensure_cw_state(session)["stage"] = {"value": stage, "stale": False}
            session.last_stage = {"scene": "cw", "value": stage}
            return {**summary, "status": "completed", "stage": stage, "stale": False, "in_battle": False}

        if monotonic() >= deadline:
            mark_stale()
            if summary:
                return {**summary, "status": "in_progress", "stage": "settle", "stale": True, "in_battle": False, "timeout_seconds": timeout}
            return {"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": timeout}

        sleep(0.5)
```

- [ ] **Step 4: 写 daemon/service 级失败用例，冻结 battle.run 路由与旧 start 延迟**

在 `tests/test_daemon_protocol.py` 追加 battle.run 路由和 start capture delay 回归：

```python
from types import SimpleNamespace

from trail.daemon.command_service import CommandService
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.daemon.session_service import SessionServiceRegistry


def test_command_service_handles_cw_battle_run_and_persists_last_result(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-battle-run.png"
        def collect_warnings(self):
            return []
        def match_references(self, screenshot_path, limit: int = 3):
            return []

    monkeypatch.setattr(
        "trail.daemon.cw_service.run_cw_battle",
        lambda session, runtime, timeout: {"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": timeout},
    )
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-battle-run",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.battle.run",
        payload={"session_id": session.session_id, "timeout": 570},
    )
    payload = command_service.handle(request)
    assert payload["data"]["status"] == "in_progress"
    assert service.request_status("req-cw-battle-run")["final_state"] == "completed"
    assert service.load_session(session.session_id).last_result["data"]["status"] == "in_progress"
    assert payload["screenshot"] == ".trail/shots/req-cw-battle-run.png"
    assert service.load_session(session.session_id).last_screenshot == ".trail/shots/req-cw-battle-run.png"


def test_command_service_handles_cw_battle_start_waits_extra_before_capture(tmp_path: Path, monkeypatch):
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_requests: list[tuple[bool, str | None]] = []
        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_requests.append((optional, request_id))
            return tmp_path / ".trail" / "shots" / "req-cw-battle-start-delay.png"
        def collect_warnings(self):
            return []
        def match_references(self, screenshot_path, limit: int = 3):
            return []

    sleeps: list[float] = []
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.save_session(session)
    runtime = Runtime()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.start_cw_battle", lambda session, starter: session)
    monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: sleeps.append(seconds))
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-battle-start-delay",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.battle.start",
        payload={"session_id": session.session_id},
    )
    payload = command_service.handle(request)
    assert payload["ok"] is True
    assert sleeps == [3.0]


def test_command_service_handles_state_dump_returns_latest_battle_run_snapshot(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.last_result = {"command": "cw.battle.run", "ok": True, "data": {"status": "in_progress", "in_battle": True}}
    session.last_screenshot = ".trail/shots/req-cw-battle-run.png"
    service.save_session(session)
    runtime_service = SimpleNamespace(attach_window=lambda window_title: {"title": window_title, "hwnd": 1})
    command_service = CommandService(runtime_service=runtime_service, session_service=registry)
    request = DaemonRequest(
        request_id="req-state-dump-battle-run",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="state.dump",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["data"]["last_result"]["command"] == "cw.battle.run"
    assert payload["data"]["last_result"]["data"]["status"] == "in_progress"
    assert payload["data"]["last_result"]["data"]["in_battle"] is True
    assert payload["data"]["last_screenshot"] == ".trail/shots/req-cw-battle-run.png"
```

- [ ] **Step 5: 实现 daemon 集成与 capture delay 映射**

在 `trail/daemon/command_service.py` 先把新命令纳入 mutation 集：

```python
CW_MUTATING_METHODS = {
    "cw.boss_preview.confirm",
    "cw.battle.start",
    "cw.battle.continue",
    "cw.battle.run",
    "cw.settle.next",
    "cw.event.handle",
}
```

先在 `trail/daemon/cw_service.py` 增加默认 timeout 常量与 `cw.battle.run` 路由，再把 capture delay 映射接到 `handle_mutation()` 现有返回路径：

```python
from trail.scenes.cw.battle import DEFAULT_CW_BATTLE_RUN_TIMEOUT, run_cw_battle


def _capture_delay_seconds_for_method(method: str) -> float:
    if method == "cw.portal.select":
        return PORTAL_SELECT_EXTRA_CAPTURE_DELAY_SECONDS
    if method == "cw.battle.start":
        return 3.0
    return 0.0


extra_delay_seconds = _capture_delay_seconds_for_method(method)
capture_runtime = _RequestScopedCaptureRuntime(runtime(), request_id, extra_delay_seconds=extra_delay_seconds)

return with_auto_capture(capture_runtime, lambda: result, verbose=verbose)

handlers = {
    "cw.boss_preview.confirm": lambda: confirm_cw_boss_preview(
        session,
        confirmer=boss_preview_confirmer_factory(runtime()),
    ).scene_state["cw"]["stage"],
    "cw.battle.start": lambda: start_cw_battle(
        session,
        starter=battle_starter_factory(runtime()),
    ).scene_state["cw"]["stage"],
    "cw.battle.continue": lambda: continue_cw_battle(
        session,
        continuer=battle_continuer_factory(runtime()),
    ).scene_state["cw"]["stage"],
    "cw.battle.run": lambda: run_cw_battle(
        session,
        runtime=runtime(),
        timeout=payload.get("timeout", DEFAULT_CW_BATTLE_RUN_TIMEOUT),
    ),
    "cw.settle.next": lambda: settle_cw_next(
        session,
        continuer=settle_continuer_factory(runtime()),
    ).scene_state["cw"]["stage"],
    "cw.event.handle": lambda: handle_cw_event(session, handler=event_handler_factory(runtime())),
}
```

- [ ] **Step 6: 为 `trail state dump --session <id> --format yaml` 补 battle.run 回读用例**

在 `tests/test_atomic_commands.py` 新增 battle-run 回读断言，冻结 YAML 路径而不是默认文本：

```python
def test_state_dump_yaml_includes_latest_battle_run_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "state.dump": build_success_response(
                request_id="req-state-dump-battle-run",
                data={
                    "session_id": "session-1",
                    "scene_state": {"cw": {"stage": {"stale": True}}},
                    "last_result": {"command": "cw.battle.run", "ok": True, "data": {"status": "in_progress", "in_battle": True}},
                    "last_screenshot": ".trail/shots/req-cw-battle-run.png",
                },
                screenshot=".trail/shots/req-state-dump-battle-run.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "state", "dump", "--session", "session-1"])

    assert result.exit_code == 0
    assert "last_result:" in result.stdout
    assert "command: cw.battle.run" in result.stdout
    assert "status: in_progress" in result.stdout
    assert "last_screenshot: .trail/shots/req-cw-battle-run.png" in result.stdout
```

- [ ] **Step 7: 回跑 scene + daemon + state.dump 的 battle 相关测试**

Run: `pytest tests/test_cw_battle_run.py tests/test_daemon_protocol.py tests/test_atomic_commands.py -k "run_cw_battle or cw_battle_run or battle_start_waits_extra_before_capture or state_dump or state_dump_yaml_includes_latest_battle_run_summary" -v`

Expected: PASS。

## Task 4: 落地 battle-run renderer 与 CLI stdout 契约

**Files:**
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 先写 renderer 的失败用例**

在 `tests/test_output_rendering.py` 增加 completed / timeout 两条 goldens：

```python
def test_render_output_cw_battle_run_completed_summary():
    payload = {
        "ok": True,
        "data": {
            "status": "completed",
            "result": "win",
            "stage": "shop",
            "stale": False,
            "in_battle": False,
            "round": "1-1",
            "hp": 82,
            "coins": 4,
            "exp": 2,
            "settle_text": "挑战成功",
        },
        "screenshot": ".trail/shots/req-cw-battle-run.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=completed result=win stage=shop stale=0 in_battle=0",
        "shot path=.trail/shots/req-cw-battle-run.png",
        "info round=1-1 hp=82 coins=4 exp=2",
        "info settle_text=挑战成功",
    ]


def test_render_output_cw_battle_run_timeout_in_settle_chain():
    payload = {
        "ok": True,
        "data": {
            "status": "in_progress",
            "result": "win",
            "stage": "settle",
            "stale": True,
            "in_battle": False,
            "round": "1-1",
            "hp": 82,
            "coins": 4,
            "exp": 2,
            "settle_text": "挑战成功",
            "timeout_seconds": 570,
        },
        "screenshot": ".trail/shots/req-cw-battle-run-timeout.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=in_progress result=win stage=settle stale=1 in_battle=0",
        "shot path=.trail/shots/req-cw-battle-run-timeout.png",
        "info round=1-1 hp=82 coins=4 exp=2",
        "info settle_text=挑战成功",
        "info timeout_seconds=570",
    ]
```

- [ ] **Step 2: 运行 renderer 失败用例，确认 battle.run 尚未接入 `TEXT_RENDERERS`**

Run: `pytest tests/test_output_rendering.py -k "cw_battle_run" -v`

Expected: FAIL，当前会回退到 generic success 或缺少 battle-run 专用形状。

- [ ] **Step 3: 实现 `_render_cw_battle_run` 并接入 `TEXT_RENDERERS`**

在 `trail/output/rendering.py` 增加 battle-run renderer：

```python
def _render_cw_battle_run(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    lines = [
        "ok "
        + command
        + " "
        + _format_fact_sequence(
            ("status", data.get("status")),
            ("result", data.get("result")),
            ("stage", data.get("stage")),
            ("stale", bool(data.get("stale")) if "stale" in data else None),
            ("in_battle", bool(data.get("in_battle")) if "in_battle" in data else None),
        )
    ]
    _append_shot(lines, payload)
    _append_fact_line(lines, "info", ("round", data.get("round")), ("hp", data.get("hp")), ("coins", data.get("coins")), ("exp", data.get("exp")))
    _append_fact_line(lines, "info", ("settle_text", data.get("settle_text")))
    _append_fact_line(lines, "info", ("timeout_seconds", data.get("timeout_seconds")))
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines


TEXT_RENDERERS = {
    "cw.boss_preview.confirm": _render_cw_stage,
    "cw.battle.start": _render_cw_stage,
    "cw.battle.continue": _render_cw_stage,
    "cw.battle.run": _render_cw_battle_run,
    "cw.settle.next": _render_cw_stage,
    "cw.event.handle": _render_cw_event_result,
}
```

- [ ] **Step 4: 把 CLI 契约矩阵补上 battle.run 的完成态与 timeout 态**

在 `tests/test_cw_rpc_contracts.py` 新增两个 CLI 样例：

```python
def test_cw_battle_run_renders_completed_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run-completed",
                data={
                    "status": "completed",
                    "result": "win",
                    "stage": "shop",
                    "stale": False,
                    "in_battle": False,
                    "round": "1-1",
                    "hp": 82,
                    "coins": 4,
                    "exp": 2,
                    "settle_text": "挑战成功",
                },
                screenshot=".trail/shots/req-cw-battle-run-completed.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID, "--timeout", "570"])

    assert result.stdout.splitlines() == [
        "ok cw.battle.run status=completed result=win stage=shop stale=0 in_battle=0",
        "shot path=.trail/shots/req-cw-battle-run-completed.png",
        "info round=1-1 hp=82 coins=4 exp=2",
        "info settle_text=挑战成功",
    ]
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 570}, tmp_path=tmp_path)


def test_cw_battle_run_renders_timeout_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run-timeout",
                data={"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": 570},
                screenshot=".trail/shots/req-cw-battle-run-timeout.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID, "--timeout", "570"])

    assert result.stdout.splitlines() == [
        "ok cw.battle.run status=in_progress stale=1 in_battle=1",
        "shot path=.trail/shots/req-cw-battle-run-timeout.png",
        "info timeout_seconds=570",
    ]
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 570}, tmp_path=tmp_path)
```

- [ ] **Step 5: 回跑 renderer + CLI 契约测试**

Run: `pytest tests/test_output_rendering.py -k "cw_battle_run" tests/test_cw_rpc_contracts.py -k "cw_battle_run" -v`

Expected: PASS。

## Task 5: 同步 README、CW skills 与文案断言

**Files:**
- Modify: `README.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `skills/trail-cw-events/SKILL.md`
- Create: `skills/trail-cw-battle-advanced/SKILL.md`
- Modify: `skills/trail-hsr-advanced/SKILL.md`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_atomic_commands.py`

- [ ] **Step 1: 先写文档断言失败用例**

先更新现有 README/skill owner 边界断言，再补 battle.run 迁移要求：

```python
def test_readme_and_cw_skills_document_battle_run_as_default_entry() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    cw_skill = (PROJECT_ROOT / "skills" / "trail-cw" / "SKILL.md").read_text(encoding="utf-8")
    events_skill = (PROJECT_ROOT / "skills" / "trail-cw-events" / "SKILL.md").read_text(encoding="utf-8")
    battle_advanced_skill = (PROJECT_ROOT / "skills" / "trail-cw-battle-advanced" / "SKILL.md").read_text(encoding="utf-8")
    hsr_advanced_skill = (PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md").read_text(encoding="utf-8")

    assert "trail cw battle run --session <id>" in readme
    assert "命令行工具的外部 timeout 提到至少 11 分钟" in readme
    assert "trail state dump --session <id> --format yaml" in readme
    assert "trail cw battle run --session <id>" in cw_skill
    assert "trail cw battle start --session <id>" not in events_skill
    assert "trail cw battle continue --session <id>" not in events_skill
    assert "trail cw settle next --session <id>" not in events_skill
    assert "不负责 battle 主流程" in events_skill
    assert "trail cw battle start --session <id>" in battle_advanced_skill
    assert "只在 battle.run 报错" in battle_advanced_skill
    assert "trail state dump --session <id> --format yaml" in hsr_advanced_skill
    assert "trail-cw-battle-advanced" in hsr_advanced_skill
```

- [ ] **Step 2: 运行文档断言，确认 README/skills 还没迁移到新主流程**

Run: `pytest tests/test_output_rendering.py -k "battle_run_as_default_entry" -v`

Expected: FAIL，README 与 skills 还没有 `battle.run` / `battle-advanced` 的新边界。

- [ ] **Step 3: 先按章节更新 README**

README 里至少补这几段明确文案：

```md
- 常规 battle/settle 流程默认使用：`trail cw battle run --session <id> --timeout 570`
- `trail cw battle run` 可能耗时接近 10 分钟；文档里要明确写出“命令行工具的外部 timeout 提到至少 11 分钟”
- 如果 `battle.run` 已在 daemon 内完成但 stdout 丢失，立刻运行：`trail state dump --session <id> --format yaml`
- `battle start` / `battle continue` / `settle next` 仍保留为 CLI 兼容命令，但只建议在 `trail-cw-battle-advanced` fallback 流程中使用
```

- [ ] **Step 4: 再更新 `trail-cw` 与 `trail-cw-events` 的 owner 边界**

`skills/trail-cw/SKILL.md` 里把 battle owner 改成主 skill：

```md
- 主循环里遇到 battle/settle 链时，默认执行 `trail cw battle run --session <id> --timeout 570`
- 只有 `battle.run` 报错、结果与截图矛盾，或用户明确要求手工拆链，才切到 `trail-cw-battle-advanced`
```

`skills/trail-cw-events/SKILL.md` 收缩成：

```md
- 处理 Boss 预览与特殊事件
- 不负责 battle 主流程；battle/settle 默认回到 `trail-cw` 主 skill 的 `trail cw battle run`
```

- [ ] **Step 5: 新建 `trail-cw-battle-advanced`，并补 `trail-hsr-advanced` 的恢复分流说明**

新建 `skills/trail-cw-battle-advanced/SKILL.md`：

```md
---
name: trail-cw-battle-advanced
description: Use when `trail cw battle run` fails or when an agent must manually拆解 Currency Wars battle/settle fallback commands.
---

# Skill: trail-cw-battle-advanced

## 何时使用

- 只在 `battle.run` 报错、结果与截图矛盾或用户明确要求手工拆链时进入
- `trail cw battle run` 报错
- `battle.run` 结果与截图明显矛盾
- 用户明确要求手工拆解 battle/settle 链路

## fallback 命令

- `trail cw battle start --session <id>`
- `trail cw battle continue --session <id>`
- `trail cw settle next --session <id>`

## 边界

- 只负责 CW battle/settle 的 scene-local fallback
- daemon / request-status / reconcile-session / state dump 继续走 `trail-hsr-advanced`
```

同时在 `skills/trail-hsr-advanced/SKILL.md` 明确补这两句：

```md
- 如果 `trail cw battle run` 的 stdout 丢失，但你仍持有 `session_id`，立刻执行：`trail state dump --session <id> --format yaml`
- 如果问题是 CW 场景内的 battle/settle 手工拆链，而不是 daemon/session/control-plane 恢复，切到 `trail-cw-battle-advanced`
```

- [ ] **Step 6: 回跑文档断言与相关 README/skill 测试**

Run: `pytest tests/test_output_rendering.py -k "battle_run_as_default_entry or help_boundaries or split_simple_and_advanced" -v`

Expected: PASS。

## 最终验证

- [ ] **Step 1: 跑 battle.run 相关测试全集**

Run: `pytest tests/test_cw_battle_run.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py -k "run_cw_battle or cw_battle_run or battle_start_waits_extra_before_capture or battle_run_as_default_entry or state_dump or state_dump_yaml_includes_latest_battle_run_summary" -v`

Expected: PASS，且 battle.run 场景、renderer、timeout、README/skills 断言全部通过。

- [ ] **Step 2: 跑一轮更宽的 CW 回归**

Run: `pytest tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_atomic_commands.py -k "cw_battle or cw_settle or cw_stage or help_boundaries or split_simple_and_advanced or state_dump" -v`

Expected: PASS，确认旧 battle/settle 命令兼容面、state.dump、README/skill 断言没有被顺手改坏。

- [ ] **Step 3: 自检 worktree 中的差异边界**

Run: `git status --short`

Expected: 只看到 battle.run 相关代码、测试、README、skills 和 plan/spec 文档变更；没有无关文件被带入。
