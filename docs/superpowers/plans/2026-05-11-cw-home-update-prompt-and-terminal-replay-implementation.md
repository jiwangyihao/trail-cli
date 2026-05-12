# CW 首页更新提示与 terminal replay 修正实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。本仓库当前用户明确要求不要创建 worktree，直接在主分支工作；所有新启动子代理必须携带本计划完整路径与规格完整路径，并提供 2000 字以上任务提示词。

**目标：** 修复货币战争首页「积分线已更新」提示导致 `cw.enter` 误判的问题，并把 daemon 无显式 request id 的业务命令重发收紧为仅 running job 可复用。

**架构：** 在 CW entry 层把首页更新提示建模为 home variant，使用区域 OCR / 首页锚点做强哨兵，dismiss 后复检再写 session；在 daemon executor 层把 singleton lookup 限定为当前 executor 内 live future / paused job，terminal 和孤儿非终态持久化 job 均不 replay；同步 active skill 文档与契约测试。

**技术栈：** Python 3、Typer CLI、daemon async executor、Trail runtime OCR / template locate、pytest、active skills 文档契约测试。

---

## 依据文件

- 规格：`docs/superpowers/specs/2026-05-11-cw-home-update-prompt-and-terminal-replay-design.md`
- 本计划：`docs/superpowers/plans/2026-05-11-cw-home-update-prompt-and-terminal-replay-implementation.md`

## 文件结构

### 修改文件

- `trail/scenes/cw/entry.py`
  - 增加首页更新提示 OCR 区域、dismiss 坐标、检测 helper、dismiss 后复检 helper。
  - 调整 `_detect_current_enter_page()` 页面优先级，让 update prompt 覆盖首页误判 fallback，但不覆盖未结束进度 / pre-invest / invest / settlement。
  - 调整 `enter_cw()` 和 `_enter_from_start_page()`，在 update prompt 路径执行 dismiss + 复检。

- `trail/daemon/request_executor.py`
  - 修改 `_latest_singleton_job()` / `_singleton_job_response()`，只允许当前 executor 内 live running job 被无 request id 普通业务命令复用。
  - 禁止 terminal job 和无 live worker 的非终态持久化 job replay。

- `skills/trail-hsr/references/async-command-model.md`
  - 更新 Agent 使用规则：普通业务命令重发只用于 running job；terminal 结果必须显式 request id / control-plane 查询。

### 修改测试

- `tests/test_cw_entry.py`
  - 覆盖 `cw.enter` 首页提示成功 dismiss、dismiss 后复检失败、recorded `game_over` 覆盖例外、OCR 区域 / 无锚点负例、负例优先级、`cw.start` 复检路径。

- `tests/test_daemon_async_executor.py`
  - 替换旧 terminal replay 测试。
  - 参数化覆盖 terminal final_state。
  - 保留 running attach。
  - 覆盖 metadata/future 竞态和非终态持久化孤儿记录。

- `tests/test_daemon_protocol.py`
  - 通过真实 async executor 路径覆盖 reconcile 后同 payload 不 replay 旧 `SESSION_RECONCILE_REQUIRED`。
  - 覆盖旧 terminal job 仍可通过显式 request id 查询。

- `tests/test_skill_structure.py`
  - 更新 active skill 文档断言，新增旧 terminal 普通重发 replay 文案的反向断言。

## 执行分组

为降低并发冲突，按文件边界拆分实现任务：

1. **CW entry 任务**：只修改 `trail/scenes/cw/entry.py` 与 `tests/test_cw_entry.py`。
2. **Daemon executor 任务**：只修改 `trail/daemon/request_executor.py` 与 `tests/test_daemon_async_executor.py`。
3. **Protocol/docs 任务**：只修改 `tests/test_daemon_protocol.py`、`skills/trail-hsr/references/async-command-model.md`、`tests/test_skill_structure.py`。

这 3 个任务可并发执行。最终由主会话运行综合验证和处理自动合并冲突。

## 任务 1：CW entry 首页更新提示

**文件：**
- 修改：`trail/scenes/cw/entry.py`
- 测试：`tests/test_cw_entry.py`

- [ ] **步骤 1：编写 `cw.enter` 首页提示成功路径失败测试**

在 `tests/test_cw_entry.py` 增加测试，构造：

```python
def test_enter_cw_home_update_prompt_dismisses_and_returns_home(tmp_path, monkeypatch):
    import trail.scenes.cw.entry as entry_module

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {"stage": {"value": "game_over", "stale": False}}

    start_box = _box("entry.start", left=1500, top=900, width=120, height=60)

    class Runtime:
        def __init__(self):
            self.locate_calls = []
            self.wait_calls = []
            self.clicks = []
            self.ocr_calls = []
            self.dismissed = False

        def locate(self, template: str, **kwargs):
            self.locate_calls.append(template)
            if template == _asset("entry.start"):
                return start_box
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            self.wait_calls.append(template)
            if template == _asset("entry.start"):
                return start_box
            return None

        def click_point(self, x: int, y: int, **kwargs):
            self.clicks.append((x, y))
            if (x, y) == entry_module.HOME_UPDATE_PROMPT_DISMISS_POINT:
                self.dismissed = True

        def ocr(self, **kwargs):
            self.ocr_calls.append(kwargs)
            if kwargs.get("capture") == entry_module.HOME_UPDATE_PROMPT_REGION and not self.dismissed:
                return [{"text": "积分线已更新"}]
            return [{"text": "货币战争"}]

    runtime = Runtime()

    refreshed = enter_cw(session, runtime=runtime)

    assert runtime.clicks == [entry_module.HOME_UPDATE_PROMPT_DISMISS_POINT]
    assert any(call.get("capture") == entry_module.HOME_UPDATE_PROMPT_REGION for call in runtime.ocr_calls)
    assert refreshed.scene_state["cw"]["entry"]["page"] == "home"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
uv run pytest tests/test_cw_entry.py -k "home_update_prompt_dismisses" -q
```

预期：FAIL，原因是当前 `cw.enter` 仍会误判或没有复检 / 区域 OCR。

- [ ] **步骤 3：编写 dismiss 后复检失败测试**

新增：

```python
def test_enter_cw_home_update_prompt_reconfirm_failure_does_not_record_home(tmp_path, monkeypatch):
    import trail.scenes.cw.entry as entry_module

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    class Runtime:
        def __init__(self):
            self.clicks = []
            self.ocr_calls = []
            self.dismissed = False

        def locate(self, template: str, **kwargs):
            if template == _asset("entry.start") and not self.dismissed:
                return _box("entry.start", left=1500, top=900, width=120, height=60)
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            return None

        def click_point(self, x: int, y: int, **kwargs):
            self.clicks.append((x, y))
            self.dismissed = True

        def ocr(self, **kwargs):
            self.ocr_calls.append(kwargs)
            if kwargs.get("capture") == entry_module.HOME_UPDATE_PROMPT_REGION and not self.dismissed:
                return [{"text": "积分线已更新"}]
            return []

    runtime = Runtime()

    with pytest.raises(TrailError) as exc_info:
        enter_cw(session, runtime=runtime)

    assert exc_info.value.code == "CW_HOME_UPDATE_PROMPT_DISMISS_UNCONFIRMED"
    assert session.scene_state.get("cw", {}).get("entry") is None
    assert runtime.clicks == [entry_module.HOME_UPDATE_PROMPT_DISMISS_POINT]
```

- [ ] **步骤 4：运行测试验证失败**

运行：

```bash
uv run pytest tests/test_cw_entry.py -k "home_update_prompt_reconfirm_failure" -q
```

预期：FAIL，错误码尚不存在。

- [ ] **步骤 5：编写检测优先级与负例测试**

新增或调整测试：

```python
def test_detect_current_enter_page_prioritizes_home_update_prompt_over_recorded_game_over(tmp_path):
    import trail.scenes.cw.entry as entry_module

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {"stage": {"value": "game_over", "stale": False}}

    class Runtime:
        def __init__(self):
            self.locate_calls = []
            self.ocr_calls = []

        def locate(self, template: str, **kwargs):
            self.locate_calls.append(template)
            if template == _asset("entry.start"):
                return _box("entry.start", left=1500, top=900)
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            return None

        def ocr(self, **kwargs):
            self.ocr_calls.append(kwargs)
            if kwargs.get("capture") == entry_module.HOME_UPDATE_PROMPT_REGION:
                return [{"text": "积分线已更新"}]
            return []

    assert entry_module._detect_current_enter_page(Runtime(), session=session) == {"page": "home", "update_prompt": "1"}
```

并新增无首页锚点负例：

```python
def test_detect_current_enter_page_does_not_treat_update_prompt_as_home_without_anchor(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks = []
            self.ocr_calls = []

        def locate(self, template: str, **kwargs):
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            return None

        def click_point(self, x: int, y: int, **kwargs):
            self.clicks.append((x, y))

        def ocr(self, **kwargs):
            self.ocr_calls.append(kwargs)
            return [{"text": "积分线已更新"}]

    monkeypatch.setattr(entry_module, "build_cw_stage_detector", lambda runtime: lambda: None)
    runtime = Runtime()

    assert entry_module._detect_current_enter_page(runtime) == {"page": "world"}
    assert runtime.clicks == []
```

- [ ] **步骤 6：运行检测测试验证失败**

运行：

```bash
uv run pytest tests/test_cw_entry.py -k "update_prompt_over_recorded_game_over or update_prompt_as_home_without_anchor" -q
```

预期：FAIL，当前优先级未实现。

- [ ] **步骤 7：编写 `cw.start` 复检测试**

新增或扩展已有 `test_enter_from_start_page_dismisses_home_update_prompt_before_start_click`，要求 dismiss 后再定位 / 确认首页，然后才点击 start；复检失败时不点击 start。

关键断言：

```python
assert events[:2] == [("click", *entry_module.HOME_UPDATE_PROMPT_DISMISS_POINT), ("sleep", 0.8)]
assert ("click", *start_box.center) in events
```

复检失败测试断言：

```python
assert start_box.center not in runtime.clicks
```

- [ ] **步骤 8：实现最少生产代码**

在 `trail/scenes/cw/entry.py` 增加：

```python
HOME_UPDATE_PROMPT_DISMISS_POINT = (1450, 580)
HOME_UPDATE_PROMPT_DISMISS_SETTLE_SECONDS = 0.8
HOME_UPDATE_PROMPT_REGION = {"from_x": 520, "from_y": 420, "to_x": 1160, "to_y": 600}
HOME_UPDATE_PROMPT_KEYWORDS = ("积分线已更新",)
HOME_REWARD_PROMPT_KEYWORDS = ("积分奖励",)
HOME_PROMPT_DISMISS_MAX_CLICKS = 2
```

新增错误：

```python
class CwHomeUpdatePromptDismissUnconfirmedError(TrailError):
    def __init__(self):
        super().__init__(
            "CW_HOME_UPDATE_PROMPT_DISMISS_UNCONFIRMED",
            "cw enter dismissed home update prompt but could not confirm currency wars home",
        )
```

新增 helper：

```python
def _read_home_update_prompt_text(runtime) -> str | None:
    try:
        texts = _extract_ocr_texts(runtime.ocr(capture=HOME_UPDATE_PROMPT_REGION))
    except TypeError:
        try:
            texts = _extract_ocr_texts(runtime.ocr())
        except Exception:
            return None
    except Exception:
        return None
    return "".join(texts)


def _home_text_has_update_prompt(joined: str | None) -> bool:
    return bool(joined) and any(keyword in joined for keyword in HOME_UPDATE_PROMPT_KEYWORDS)


def _home_has_update_prompt(runtime, *, has_home_anchor: bool) -> bool:
    if not has_home_anchor:
        return False
    return _home_text_has_update_prompt(_read_home_update_prompt_text(runtime))


def _confirm_home_after_update_prompt(runtime) -> bool:
    return _locate(runtime, "entry.start") is not None and not _home_has_update_prompt(runtime, has_home_anchor=True)


def _dismiss_home_update_prompt(runtime) -> None:
    runtime.click_point(*HOME_UPDATE_PROMPT_DISMISS_POINT)
    _transition_sleep(HOME_UPDATE_PROMPT_DISMISS_SETTLE_SECONDS)
    if not _confirm_home_after_update_prompt(runtime):
        raise CwHomeUpdatePromptDismissUnconfirmedError()
```

调整 `_detect_current_enter_page()` 顺序：

- 获取 `start_box` / `continue_box`。
- 若首页锚点存在，先读 `_read_home_text()` 判断 unfinished progress。
- `entry.new` / `entry.continue` / `invest` / settlement 原逻辑在 update prompt 前。
- 在 stage detector / recorded game_over fallback 前加入：

```python
if start_box is not None and _home_has_update_prompt(runtime, has_home_anchor=True):
    return {"page": "home", "update_prompt": "1"}
```

调整 `enter_cw()`：

```python
if current["page"] == "home":
    if current.get("update_prompt") == "1":
        _dismiss_home_update_prompt(runtime)
        entry = {"page": "home", "already_home": True, "dismissed_update_prompt": True}
    else:
        entry = {"page": "home", "already_home": True}
```

调整 `_enter_from_start_page()`：若 start page 上存在 update prompt，先 `_dismiss_home_update_prompt(runtime)`，再重新 `_wait(runtime, "entry.start")` 或 `_locate`，然后点击 start；复检失败由 helper 抛错。

- [ ] **步骤 9：运行 CW entry 全文件测试**

运行：

```bash
uv run pytest tests/test_cw_entry.py
```

预期：全部通过。

## 任务 2：Daemon executor terminal replay 收紧

**文件：**
- 修改：`trail/daemon/request_executor.py`
- 测试：`tests/test_daemon_async_executor.py`

- [ ] **步骤 1：替换 terminal replay 旧测试**

在 `tests/test_daemon_async_executor.py` 中找到 `test_without_request_id_terminal_same_payload_replays_latest_singleton_job`，改为：

```python
def test_without_request_id_terminal_same_payload_creates_new_job(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)

    first = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-1", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0))
    )
    first_job = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("input.click")["job_id"]

    second = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-2", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0))
    )
    second_job = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("input.click")["job_id"]

    assert first["ok"] is True
    assert second["ok"] is True
    assert service.calls == 2
    assert second_job != first_job
    assert registry.for_workspace(str(tmp_path)).request_status("call-2")["job_id"] == second_job
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
uv run pytest tests/test_daemon_async_executor.py -k "terminal_same_payload" -q
```

预期：FAIL，当前仍 replay terminal。

- [ ] **步骤 3：新增 terminal final_state 参数化测试**

新增 helper 可直接写 job record 到 service，然后提交相同 payload：

```python
@pytest.mark.parametrize(
    "final_state",
    [
        "completed",
        "failed_before_side_effect",
        "cancelled",
        "applied_but_not_persisted",
        "persisted_but_response_unknown",
        "cancel_unknown",
    ],
)
def test_without_request_id_does_not_replay_any_terminal_job(tmp_path: Path, final_state: str):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    session_service = registry.for_workspace(str(tmp_path))

    first = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-terminal-1", payload={"x": 3, "y": 4}, control=RequestControl(wait_timeout=1.0))
    )
    first_job = session_service.find_latest_job_for_method("input.click")["job_id"]
    session_service.update_job_record(
        first_job,
        state="completed" if final_state == "completed" else "failed",
        final=True,
        final_state=final_state,
        last_envelope=first,
    )

    second = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-terminal-2", payload={"x": 3, "y": 4}, control=RequestControl(wait_timeout=1.0))
    )
    second_job = session_service.find_latest_job_for_method("input.click")["job_id"]

    assert second["ok"] is True
    assert second_job != first_job
    assert service.calls == 2
```

- [ ] **步骤 4：新增 metadata/future 竞态测试**

新增：

```python
def test_without_request_id_does_not_replay_finished_future_metadata(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    session_service = registry.for_workspace(str(tmp_path))

    first = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-meta-1", payload={"x": 5, "y": 6}, control=RequestControl(wait_timeout=1.0))
    )
    first_job = session_service.find_latest_job_for_method("input.click")["job_id"]

    # 模拟 release callback 尚未清理 metadata，但持久化 job 已 terminal。
    executor._job_metadata[first_job] = {
        **session_service.get_job_record(first_job),
        "final": False,
        "final_state": None,
    }
    executor._futures.pop(first_job, None)

    second = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-meta-2", payload={"x": 5, "y": 6}, control=RequestControl(wait_timeout=1.0))
    )
    second_job = session_service.find_latest_job_for_method("input.click")["job_id"]

    assert second["ok"] is True
    assert second_job != first_job
    assert service.calls == 2
```

并新增孤儿非终态持久化记录测试：

```python
def test_without_request_id_does_not_replay_orphan_non_terminal_persisted_job(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    session_service = registry.for_workspace(str(tmp_path))

    orphan = session_service.create_job_record(
        job_id="orphan-job",
        job_key="\n".join([str(tmp_path).replace("\\", "/").casefold(), "", "input.click", canonical_payload_digest({"x": 7, "y": 8})]),
        method="input.click",
        payload_digest=canonical_payload_digest({"x": 7, "y": 8}),
        session_id=None,
    )
    assert orphan["final"] is False

    response = executor.handle(
        _request(tmp_path, method="input.click", call_id="call-orphan", payload={"x": 7, "y": 8}, control=RequestControl(wait_timeout=1.0))
    )

    latest = session_service.find_latest_job_for_method("input.click")
    assert response["ok"] is True
    assert latest["job_id"] != "orphan-job"
    assert service.calls == 1
```

- [ ] **步骤 5：运行新增 daemon async tests 验证失败**

运行：

```bash
uv run pytest tests/test_daemon_async_executor.py -k "terminal or metadata or orphan" -q
```

预期：FAIL，当前逻辑会 replay terminal / orphan。

- [ ] **步骤 6：实现 live running 限定**

在 `trail/daemon/request_executor.py` 中修改 `_latest_singleton_job()`。

推荐实现：

```python
def _replayable_running_metadata(self, job_id: str | None, metadata: dict | None) -> dict | None:
    if job_id is None or metadata is None or metadata.get("final"):
        return None
    future = self._futures.get(job_id)
    if future is not None and not future.done():
        return deepcopy(metadata)
    if job_id in self._paused_jobs:
        return deepcopy(metadata)
    return None
```

修改 `_latest_singleton_job()`：

```python
def _latest_singleton_job(self, request: DaemonRequest, service, *, job_key: str) -> dict | None:
    with self._mutex:
        job_id = self._job_key_to_job_id.get(job_key)
        metadata = deepcopy(self._job_metadata.get(job_id)) if job_id is not None else None
        replayable = self._replayable_running_metadata(job_id, metadata)
    if replayable is not None:
        return replayable
    return None
```

删除或停用 `service.find_latest_job_for_method()` 的无 request id singleton replay 分支。

- [ ] **步骤 7：运行 daemon async 全文件测试**

运行：

```bash
uv run pytest tests/test_daemon_async_executor.py
```

预期：全部通过。

## 任务 3：Protocol 回归与 active skill 文档同步

**文件：**
- 修改：`tests/test_daemon_protocol.py`
- 修改：`skills/trail-hsr/references/async-command-model.md`
- 修改：`tests/test_skill_structure.py`

- [ ] **步骤 1：新增真实 async executor reconcile 回归测试**

在 `tests/test_daemon_protocol.py` 新增测试。可用 `RequestExecutor.handle()` 或 `TrailDaemonServer(async_enabled=True)`；关键是不能只调 `CommandService`。

伪代码结构：

```python
def test_async_executor_reconcile_allows_same_payload_after_session_tainted_terminal(tmp_path: Path, monkeypatch):
    from trail.daemon.request_executor import RequestExecutor
    from trail.daemon.cw_service import CwService

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state.setdefault("daemon", {})["tainted"] = True
    service.save_session(session)

    runtime = SimpleNamespace()
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    executor = RequestExecutor(command_service=command_service, session_service=registry)

    first = executor.handle(DaemonRequest(
        request_id="call-tainted-1",
        call_id="call-tainted-1",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        method="cw.enter",
        payload={"session_id": session.session_id},
    ))
    assert first["error"]["code"] == "SESSION_RECONCILE_REQUIRED"

    reconcile = executor.handle(DaemonRequest(
        request_id="call-reconcile",
        call_id="call-reconcile",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        method="daemon.reconcile_session",
        payload={"session_id": session.session_id},
    ))
    assert reconcile["ok"] is True

    monkeypatch.setattr("trail.scenes.cw.entry.enter_cw", lambda session, **kwargs: session)
    second = executor.handle(DaemonRequest(
        request_id="call-tainted-2",
        call_id="call-tainted-2",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        method="cw.enter",
        payload={"session_id": session.session_id},
    ))

    assert second["ok"] is True
    assert second.get("error") is None
```

根据现有 `DaemonRequest` 构造签名补齐 `verbose` / `control` 字段；遵循文件中已有 helper 风格。

- [ ] **步骤 2：新增显式 request id 可追溯测试**

在同一测试中或单独测试中，拿第一次 terminal job id：

```python
first_job = service.find_latest_job_for_method("cw.enter")["job_id"]
status = executor.handle(DaemonRequest(... method="daemon.request_status", payload={"request_id": first_job}))
result = executor.handle(DaemonRequest(... method="daemon.request_result", payload={"request_id": first_job}))
assert status["ok"] is True
assert result["ok"] is False
assert result["error"]["code"] == "SESSION_RECONCILE_REQUIRED"
```

- [ ] **步骤 3：运行 protocol 新测试验证失败**

运行：

```bash
uv run pytest tests/test_daemon_protocol.py -k "reconcile_allows_same_payload or request_id" -q
```

预期：FAIL，当前 terminal singleton replay 或测试未实现。

- [ ] **步骤 4：更新 active skill 文档**

修改 `skills/trail-hsr/references/async-command-model.md` 第 14 条，将旧规则：

```text
没有显式 `--request-id` 时，重发同 payload 会附着该业务命令的最新 singleton job；旧 job 已终态时也回放终态业务输出，不重新执行同 payload mutation。
```

改为：

```text
没有显式 `--request-id` 时，重发同 payload 只会附着当前仍在运行的 singleton job；如果匹配 job 已终态，普通业务命令不会 replay 旧结果，而是按一次新的业务命令提交。需要查看旧终态结果时，使用 `trail daemon request-status --request-id <job_id>` 或 `trail daemon request-result --request-id <job_id>`。
```

- [ ] **步骤 5：更新 skill structure 测试**

在 `tests/test_skill_structure.py` 找到 async command model 文档测试，增加正反向断言：

```python
text = path.read_text(encoding="utf-8")
assert "只会附着当前仍在运行的 singleton job" in text
assert "旧 job 已终态时也回放终态业务输出" not in text
assert "daemon request-result --request-id" in text
```

- [ ] **步骤 6：运行文档契约测试**

运行：

```bash
uv run pytest tests/test_skill_structure.py
```

预期：通过。

- [ ] **步骤 7：运行 protocol 全文件测试**

运行：

```bash
uv run pytest tests/test_daemon_protocol.py
```

预期：全部通过。

## 最终验证

所有实现任务完成后，主会话运行：

```bash
uv run pytest tests/test_cw_entry.py
uv run pytest tests/test_daemon_async_executor.py
uv run pytest tests/test_daemon_protocol.py
uv run pytest tests/test_skill_structure.py
```

然后运行实机验证：

```bash
uv run trail daemon restart
uv run trail session create
uv run trail cw enter --session <new_session>
```

预期：在「积分线已更新」首页变种上，`cw.enter` 点击 `HOME_UPDATE_PROMPT_DISMISS_POINT` 关闭提示并返回 `ok cw.enter ...`。如果页面已经是普通首页，则 no-op 成功返回且不额外点击。
