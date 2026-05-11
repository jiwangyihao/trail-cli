# Daemon 全命令异步模型实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度。本仓库当前用户明确要求不要创建 worktree，直接在主分支工作；所有新启动子代理必须携带本计划完整路径与规格完整路径，并提供 2000 字以上任务提示词。

**目标：** 将 Trail daemon-backed 业务命令迁移到统一 daemon 端异步 job 模型，CLI 默认等待预算内返回最终业务输出，超时后通过重发同一业务命令查询同一个 job，且不重复执行 UI 动作。

**架构：** 先在协议、存储、renderer 和 executor 上建立可测基础，但生产 server 保持 legacy 路径，直到 cancel 安全点、session atomicity、文档/skill 原子切换全部完成；最后一步才启用默认 async。`RequestExecutor` 负责 job lookup、busy rejection、wait budget、request-result/request-cancel/control-plane bypass；`CommandService` 保留业务 handler，但迁移 legacy duplicate/replay 语义到 executor 管理的 job/call journal。

**技术栈：** Python 3、Typer CLI、ThreadingTCPServer daemon、threading / concurrent.futures、pytest、Trail 输出 renderer、active skills / README / AGENTS 文档契约测试。

---

> 当前用户要求先审查规格与计划，再进入子代理开发；未要求创建 git commit。执行本计划时，每个任务 checkpoint 以“目标测试通过 + diff 自检”替代 commit。若后续用户明确要求提交，再按 Conventional Commits 创建提交。

## 依据文件

- 规格：`docs/superpowers/specs/2026-05-10-daemon-async-command-model-design.md`
- 本计划：`docs/superpowers/plans/2026-05-10-daemon-async-command-model-implementation.md`

## 总体迁移门禁

- **生产 server async gate：** 任务 1-12 期间，`TrailDaemonServer` 默认仍使用 legacy `CommandService.handle()`。`RequestExecutor` 只能通过测试注入或环境/构造参数显式启用。任务 14 才把生产默认切到 executor。
- **默认 async gate：** 全命令默认 async 不得早于以下条件全部满足：session atomic save + job/call journal、request-result、request-cancel soft token、安全点测试、renderer running/busy/status/cancel、文档/skills/routing 测试。
- **legacy journal gate：** executor-managed worker 必须使用稳定 `job_id` 执行业务 handler。`call_id` 只写 CallRecord；legacy `_run_mutation()` 不得因重发 call_id 产生 duplicate/replay 分支。
- **cancel gate：** soft cancel 必须有确定性测试覆盖 worker start 前、RuntimeService wait、image/OCR wait、input helper UI 动作前、CW battle loop、side-effect 后 cancel_unknown。缺任一测试不得启用默认 async。
- **session mutation lease gate：** 默认 async 启用前，必须有测试证明 session/job/call journal 在同一 workspace/session lease 内完成完整 read-modify-write；`session.create`、`guide.fetch.cw --select`、所有写 `last_result`/`last_screenshot` 的业务命令均已分类，不能只锁单次 save。

## 文件结构

### 新建文件

- `trail/daemon/request_executor.py`
  - 统一处理 daemon async job lifecycle、job lookup、busy rejection、wait budget、control-plane bypass、request-result/request-cancel 业务分发。
  - 暴露 `RequestExecutor.handle(request: DaemonRequest) -> dict[str, Any]`。
  - 任务 14 前只能在测试或显式 feature gate 中启用。
- `tests/test_daemon_async_executor.py`
  - 专门覆盖 `RequestExecutor` 的状态机、job key、busy、cancel、result replay、reconcile control-plane bypass。
  - 在文件顶部定义所有复用测试夹具：`_request()`、`SlowBusinessService`、`CountingService`、`FastControlService`、`BlockingBeforeSideEffectService`、`SideEffectThenBlockService`、`BlockingInputRuntime`、`BlockingStartRuntimeService`、`BlockingImageWaitRuntime`、`BlockingOcrRetryRuntime`、`BlockingCwBattleService`。

### 修改文件

- `trail/daemon/models.py`
  - `DaemonRequest` 增加 `call_id`、`job_id`、`control`。
  - 新增 `RequestControl`、`CancellationToken`、`RequestCancelled`。
- `trail/daemon/client.py`
  - `TrailDaemonClient.call()` 支持 `request_id`/`job_id`、`wait_timeout`、`no_wait`，并把 control 字段交给 transport。
  - `send_daemon_request()` 写入 `call_id`、`job_id`、`control`，socket timeout 使用 `wait_timeout + transport_buffer`。
- `trail/daemon/server.py`
  - 支持注入 `RequestExecutor`，但默认 legacy 到任务 14；任务 14 才默认构建并启用 executor。
- `trail/daemon/command_service.py`
  - 新增 `handle_control_plane()` 和 `execute_business_request()`。
  - 新增 executor-managed request 模式：worker request 使用 `request.request_id == job_id`，`request.call_id` 单独保留；legacy `_run_mutation()` 的 duplicate/replay 分支只对非 executor-managed legacy 调用生效。
  - 增加 `daemon.request_result`、`daemon.request_cancel` control-plane handling。
- `trail/daemon/cw_service.py`
  - `handle()` / `cw.battle.run` 调用链接收并传递 cancellation token 到 `run_cw_battle()`。
- `trail/scenes/cw/battle.py`
  - `run_cw_battle()` 接收 `cancellation_token` optional，在每轮 stage 检测、OCR 分类前后、点击开战前、sleep 前检查取消。
- `trail/daemon/session_service.py`
  - 把 `_mutex` 改为 `threading.RLock`，避免 `finish_mutation()` / `reconcile_session()` 持锁调用 session load/save 时死锁。
  - 引入 JobRecord / CallRecord 存储与查询。
  - 所有 session/job/call journal 写入纳入同一锁。
  - request-status 先查 CallRecord，再查 JobRecord，再兼容旧 request record。
- `trail/session/store.py`
  - `save()` 改为 temp file + atomic replace。
- `trail/output/envelope.py`
  - `command_failure()` 增加可选 `data: dict | None = None` 参数，并把 `screenshot` 默认值设为 `None`；默认 `data` 仍为 `{}`，用于 busy/cancel 等 failure 输出携带 machine-readable facts。
- `trail/commands/helpers.py`
  - `call_daemon()` 支持 `request_id`、`wait_timeout`、`no_wait`，并读取全局 daemon control options。
  - 解析优先级固定：`--no-wait` > 命令级或全局 `--wait-timeout` > `TRAIL_WAIT_TIMEOUT` > 默认 100。
- `trail/cli.py`
  - 增加全局 `--wait-timeout`、`--no-wait`、`--request-id`，设置命令 helper 的全局 daemon control options。
- `trail/commands/daemon.py`
  - 新增 `request-result`、`request-cancel` 命令。
  - `request-result` 必须用内层 `render_command/envelope` 原样调用 `print_output(render_command, envelope)`。
  - `request-cancel --force --confirm-taint` 第一版返回 `FORCE_CANCEL_NOT_SUPPORTED`。
- `trail/commands/cw.py`
  - `_rpc_cw()` / `_print_cw()` 可接收命令级 `request_id` 并作为 control 透传，不把 control 混入业务 payload。
  - 至少 `cw battle run --request-id` 必须支持规格示例；若统一 wrapper 可低风险覆盖所有 CW 命令，则优先统一实现。
- `trail/commands/start.py`
  - `trail start --request-id <job_id>` 可查询 job，不改变业务 payload digest。
- `trail/daemon/command_timeouts.py`
  - 新增 `DEFAULT_DAEMON_WAIT_TIMEOUT_SECONDS = 100.0`、transport buffer resolver、`TRAIL_WAIT_TIMEOUT` 解析 helper。
- `trail/output/rendering.py`
  - 增加 async-running renderer 短路、busy failure 特判、request-status 新字段、request-cancel renderer。
- `tests/support/fake_daemon.py`
  - 记录 `call_id`、`job_id`、`control`，并同步更新所有 exact `client.calls` 断言和 `_assert_single_call` helper，避免新增字段破坏旧测试。
- `tests/test_daemon_protocol.py`
- `tests/test_daemon_session_service.py`
- `tests/test_cli_output_protocol.py`
- `tests/test_output_rendering.py`
- `tests/test_atomic_commands.py`
- `tests/test_cw_rpc_contracts.py`
- `tests/test_guide_rpc_contracts.py`
- `tests/test_skill_structure.py`
- `tests/test_skill_routing_contracts.py`
- `tests/test_skill_registry.py`
- `AGENTS.md`
- `README.md`
- active skills 与 references：见任务 12。

### 不修改文件

- `docs/superpowers/specs/*` 和 `docs/superpowers/plans/*` 不作为行为测试扫描源；实现测试只能锁定源码、renderer、README、AGENTS、active skills、registry/evals。
- 不修改 archive skills，不让 archive skill 回流 active topology。

## 任务 1：协议模型、client control 字段与 wait timeout 优先级

**文件：**
- 修改：`trail/daemon/models.py`
- 修改：`trail/daemon/client.py`
- 修改：`trail/daemon/command_timeouts.py`
- 修改：`trail/output/envelope.py`
- 修改：`tests/test_daemon_protocol.py`
- 修改：`tests/support/fake_daemon.py`
- 修改：`tests/test_atomic_commands.py`
- 修改：`tests/test_cw_rpc_contracts.py`
- 修改：`tests/test_guide_rpc_contracts.py`
- 修改：`tests/test_cli_output_protocol.py`

- [ ] **步骤 1：编写 client control 字段失败用例**

在 `tests/test_daemon_protocol.py` 增加 `RequestControl` 导入，并添加：

```python
def test_daemon_request_serializes_call_job_and_control_fields(tmp_path: Path):
    captured: dict[str, Any] = {}

    class Server:
        def handle(self, payload):
            captured.update(payload)
            return build_success_response(request_id=payload["call_id"], data={"ok": True})

    request = DaemonRequest(
        call_id="call-1",
        job_id="job-1",
        request_id="call-1",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id="sess-1",
        verbose=True,
        method="cw.battle.run",
        payload={"timeout": 90},
        control=RequestControl(mode="async_wait", wait_timeout=15.0),
    )

    response = send_daemon_request(request, "token", endpoint="fake", server=Server())

    assert response["ok"] is True
    assert captured["call_id"] == "call-1"
    assert captured["job_id"] == "job-1"
    assert captured["request_id"] == "call-1"
    assert captured["control"] == {"mode": "async_wait", "wait_timeout": 15.0, "side_effect_stage": "none"}
    assert captured["payload"] == {"timeout": 90}
```

在同文件增加 wait timeout 优先级 helper 测试：

```python
def test_response_timeout_uses_wait_timeout_control_buffer():
    assert resolve_response_timeout("cw.battle.run", {"timeout": 90}, wait_timeout=12.0) == 17.0
    assert resolve_response_timeout("cw.battle.run", {"timeout": 90}, wait_timeout=0.0) == 5.0


def test_normalize_daemon_wait_timeout_reads_env_with_cli_precedence(monkeypatch):
    monkeypatch.setenv("TRAIL_WAIT_TIMEOUT", "15")
    assert normalize_daemon_wait_timeout(None, no_wait=False) == 15.0
    assert normalize_daemon_wait_timeout(7, no_wait=False) == 7.0
    assert normalize_daemon_wait_timeout(7, no_wait=True) == 0.0
    monkeypatch.setenv("TRAIL_WAIT_TIMEOUT", "bad")
    assert normalize_daemon_wait_timeout(None, no_wait=False) == 100.0
```

- [ ] **步骤 2：扩展 command_failure 支持 data**

在 `tests/test_output_envelope.py` 增加：

```python
def test_command_failure_accepts_machine_readable_data():
    payload = command_failure(code="DAEMON_BUSY", message="busy", data={"active_request": "job-1"})

    assert payload["ok"] is False
    assert payload["data"] == {"active_request": "job-1"}
    assert payload["error"] == {"code": "DAEMON_BUSY", "message": "busy"}
```

实现后 busy/cancel 测试可以直接使用 `command_failure(..., data={...})`，不会因 TypeError 假红。

- [ ] **步骤 3：更新 FakeDaemon 兼容测试红灯**

在 `tests/test_atomic_commands.py` 增加：

```python
def test_fake_daemon_records_control_without_polluting_payload(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client({"ocr.read": build_success_response(request_id="req", data={"result": [{"text": "x"}]})})

    result = cli_runner.invoke(app, ["--wait-timeout", "3", "--request-id", "job-1", "ocr", "read"])

    assert result.exit_code == 0
    assert client.calls[0]["payload"] == {}
    assert client.calls[0]["job_id"] == "job-1"
    assert client.calls[0]["control"] == {"mode": "async_wait", "wait_timeout": 3.0, "side_effect_stage": "none"}
```

同时更新现有 exact `client.calls == [...]` 断言，给每个期望 dict 增加：

```python
"job_id": None,
"control": {"mode": "async_wait", "wait_timeout": 100.0, "side_effect_stage": "none"},
```

涉及文件至少包括：`tests/test_atomic_commands.py`、`tests/test_cw_rpc_contracts.py`、`tests/test_guide_rpc_contracts.py`、`tests/test_cli_output_protocol.py`、`tests/test_daemon_protocol.py::test_fake_daemon_client_records_request_metadata`。若某文件有 `_assert_single_call` helper，优先更新 helper 而不是散改所有断言。

- [ ] **步骤 4：运行协议红灯**

运行：

```bash
uv run pytest --basetemp .pytest-tmp-task1-red tests/test_daemon_protocol.py::test_daemon_request_serializes_call_job_and_control_fields tests/test_daemon_protocol.py::test_response_timeout_uses_wait_timeout_control_buffer tests/test_daemon_protocol.py::test_normalize_daemon_wait_timeout_reads_env_with_cli_precedence tests/test_output_envelope.py::test_command_failure_accepts_machine_readable_data tests/test_atomic_commands.py::test_fake_daemon_records_control_without_polluting_payload -v
```

预期：FAIL，原因包括 `RequestControl` 不存在、`DaemonRequest` 无 `call_id/job_id/control`、FakeDaemon 不记录 control、`command_failure` 不支持 data。

- [ ] **步骤 5：实现模型、client、command_failure 最小代码**

在 `trail/daemon/models.py` 增加完整签名：

```python
class RequestCancelled(TrailError):
    def __init__(self) -> None:
        super().__init__("REQUEST_CANCELLED", "request cancelled")


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RequestCancelled()


@dataclass(slots=True)
class RequestControl:
    mode: str = "async_wait"
    wait_timeout: float = 100.0
    cancellation_token: CancellationToken = field(default_factory=CancellationToken, repr=False, compare=False)
    side_effect_stage: str = "none"

    @classmethod
    def from_mapping(cls, value: Any) -> "RequestControl":
        mapping = value if isinstance(value, dict) else {}
        return cls(
            mode=str(mapping.get("mode") or "async_wait"),
            wait_timeout=normalize_daemon_wait_timeout(mapping.get("wait_timeout"), no_wait=False),
        )

    def to_dict(self) -> JsonDict:
        return {"mode": self.mode, "wait_timeout": float(self.wait_timeout), "side_effect_stage": self.side_effect_stage}
```

`DaemonRequest.__post_init__()` 保证 `call_id` 默认等于 `request_id`。

在 `trail/daemon/command_timeouts.py` 增加：

```python
DEFAULT_DAEMON_WAIT_TIMEOUT_SECONDS = 100.0
DAEMON_TRANSPORT_TIMEOUT_BUFFER_SECONDS = 5.0


def normalize_daemon_wait_timeout(raw_timeout: Any, *, no_wait: bool = False) -> float:
    if no_wait:
        return 0.0
    value = raw_timeout
    if value is None:
        value = os.environ.get("TRAIL_WAIT_TIMEOUT")
    try:
        parsed = float(value) if value is not None and not isinstance(value, bool) else DEFAULT_DAEMON_WAIT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        parsed = DEFAULT_DAEMON_WAIT_TIMEOUT_SECONDS
    return max(0.0, parsed)
```

`resolve_response_timeout(method, payload, *, wait_timeout=None)` 返回 `normalize_daemon_wait_timeout(wait_timeout) + DAEMON_TRANSPORT_TIMEOUT_BUFFER_SECONDS`。

`trail/output/envelope.py::command_failure()` 增加 `data: dict | None = None` 参数，把 `screenshot` 设为默认 `None`，默认输出 `{}`，保持旧调用兼容。

- [ ] **步骤 6：补全 FakeDaemon call shape 并回跑**

更新 `FakeDaemonClient.call()` 和 `fake_round_trip_transport()`，确保 `call_id/job_id/control` 有默认值且不进入 `payload`。

运行：

```bash
uv run pytest --basetemp .pytest-tmp-task1-green tests/test_daemon_protocol.py::test_daemon_request_serializes_call_job_and_control_fields tests/test_daemon_protocol.py::test_response_timeout_uses_wait_timeout_control_buffer tests/test_daemon_protocol.py::test_normalize_daemon_wait_timeout_reads_env_with_cli_precedence tests/test_output_envelope.py::test_command_failure_accepts_machine_readable_data tests/test_atomic_commands.py::test_fake_daemon_records_control_without_polluting_payload tests/test_guide_rpc_contracts.py -v
```

预期：PASS。同步更新旧 `resolve_response_timeout` 断言：旧 start/battle response timeout 不再等于 execution timeout，而是 wait timeout + 5 秒 transport buffer。

## 任务 2：CLI 全局与命令级 request-id / wait options

**文件：**
- 修改：`trail/cli.py`
- 修改：`trail/commands/helpers.py`
- 修改：`trail/commands/start.py`
- 修改：`trail/commands/cw.py`
- 修改：`tests/test_atomic_commands.py`
- 修改：`tests/test_cw_rpc_contracts.py`

- [ ] **步骤 1：编写全局与命令级 options 失败用例**

在 `tests/test_atomic_commands.py` 增加：

```python
def test_start_forwards_global_request_id_and_wait_timeout_as_control(cli_runner, fake_daemon_client):
    client = fake_daemon_client({"start.run": build_success_response(request_id="call-1", data={"state": "running", "request": "job-1", "waited": 3})})

    result = cli_runner.invoke(app, ["--request-id", "job-1", "--wait-timeout", "3", "start"])

    assert result.exit_code == 0
    assert client.calls[0]["payload"] == {"window_title": "崩坏：星穹铁道", "channel": "official"}
    assert client.calls[0]["job_id"] == "job-1"
    assert client.calls[0]["control"] == {"mode": "async_wait", "wait_timeout": 3.0, "side_effect_stage": "none"}


def test_start_forwards_command_level_request_id(cli_runner, fake_daemon_client):
    client = fake_daemon_client({"start.run": build_success_response(request_id="call-2", data={"state": "running", "request": "job-2", "waited": 100})})

    result = cli_runner.invoke(app, ["start", "--request-id", "job-2"])

    assert result.exit_code == 0
    assert client.calls[0]["job_id"] == "job-2"
    assert client.calls[0]["control"] == {"mode": "async_wait", "wait_timeout": 100.0, "side_effect_stage": "none"}
```

在 `tests/test_cw_rpc_contracts.py` 增加：

```python
def test_cw_battle_run_forwards_command_level_request_id(cli_runner, fake_daemon_client):
    client = fake_daemon_client({"cw.battle.run": build_success_response(request_id="call-cw", data={"state": "running", "request": "job-cw", "waited": 100})})

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", "sess-1", "--request-id", "job-cw"])

    assert result.exit_code == 0
    assert client.calls[0]["method"] == "cw.battle.run"
    assert client.calls[0]["session_id"] == "sess-1"
    assert client.calls[0]["payload"] == {"timeout": 90}
    assert client.calls[0]["job_id"] == "job-cw"


def test_wait_timeout_env_and_no_wait_precedence(cli_runner, fake_daemon_client, monkeypatch):
    client = fake_daemon_client({"ocr.read": build_success_response(request_id="call-ocr", data={"result": [{"text": "x"}]})})
    monkeypatch.setenv("TRAIL_WAIT_TIMEOUT", "15")

    result = cli_runner.invoke(app, ["--no-wait", "ocr", "read"])

    assert result.exit_code == 0
    assert client.calls[0]["control"] == {"mode": "async_wait", "wait_timeout": 0.0, "side_effect_stage": "none"}
```

- [ ] **步骤 2：运行 CLI 红灯**

运行：

```bash
uv run pytest --basetemp .pytest-tmp-task2-red tests/test_atomic_commands.py::test_start_forwards_global_request_id_and_wait_timeout_as_control tests/test_atomic_commands.py::test_start_forwards_command_level_request_id tests/test_cw_rpc_contracts.py::test_cw_battle_run_forwards_command_level_request_id tests/test_cw_rpc_contracts.py::test_wait_timeout_env_and_no_wait_precedence -v
```

预期：FAIL，当前 CLI 不认识这些 options。

- [ ] **步骤 3：实现全局与命令级 options**

在 `trail/commands/helpers.py` 增加：

```python
_DAEMON_CONTROL_OPTIONS = {"job_id": None, "wait_timeout": None, "no_wait": False}


def set_daemon_control_options(*, job_id: str | None, wait_timeout: float | None, no_wait: bool) -> None:
    _DAEMON_CONTROL_OPTIONS.update({"job_id": job_id, "wait_timeout": wait_timeout, "no_wait": bool(no_wait)})


def call_daemon(method: str, payload: dict[str, Any], *, session_id: str | None = None, verbose: bool = False, request_id: str | None = None) -> dict[str, Any]:
    job_id = request_id or _DAEMON_CONTROL_OPTIONS["job_id"]
    wait_timeout = normalize_daemon_wait_timeout(_DAEMON_CONTROL_OPTIONS["wait_timeout"], no_wait=bool(_DAEMON_CONTROL_OPTIONS["no_wait"]))
    response = client.call(method, payload, session_id=session_id, verbose=verbose, job_id=job_id, wait_timeout=wait_timeout)
```

在 `trail/cli.py` callback 增加全局 options。`trail/commands/start.py::start_command()` 增加：

```python
request_id: Annotated[str | None, typer.Option("--request-id")] = None,
```

并调用 `call_daemon("start.run", payload, request_id=request_id)`。

`trail/commands/cw.py`：`_rpc_cw(..., request_id: str | None = None)`，`_print_cw(..., request_id: str | None = None)`；`cw_battle_run()` 增加 `request_id` option 并传给 `_print_cw()`。

- [ ] **步骤 4：运行 CLI 绿灯**

运行同 Step 2 命令，预期 PASS。

## 任务 3：SessionService job/call journal、RLock 与 atomic save

**文件：**
- 修改：`trail/daemon/session_service.py`
- 修改：`trail/session/store.py`
- 修改：`tests/test_daemon_session_service.py`

- [ ] **步骤 1：编写 job/call/status 失败用例**

在 `tests/test_daemon_session_service.py` 增加（若文件尚未导入，先补 `import threading` 与 `from concurrent.futures import ThreadPoolExecutor`）：

```python
def test_job_and_call_records_drive_request_status(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    service.create_job_record(job_id="job-1", job_key="key-1", method="cw.battle.run", payload_digest="digest-1", session_id="sess-1")
    service.create_call_record(call_id="call-1", job_id="job-1", method="cw.battle.run", session_id="sess-1", state="attached", executed=True)

    status_by_call = service.request_status("call-1")
    status_by_job = service.request_status("job-1")

    assert status_by_call["request_id"] == "call-1"
    assert status_by_call["job_id"] == "job-1"
    assert status_by_call["method"] == "cw.battle.run"
    assert status_by_call["session_id"] == "sess-1"
    assert status_by_call["state"] == "accepted"
    assert status_by_call["final"] is False
    assert status_by_call["final_state"] is None
    assert status_by_call["last_visible_stage"] == "accepted"
    assert status_by_call["side_effect_stage"] == "none"
    assert status_by_call["tainted"] is False
    assert status_by_call["next_request_id"] == "job-1"
    assert status_by_job["request_id"] == "job-1"
    assert status_by_job.get("job_id") in {None, "job-1"}
    assert status_by_job["next_request_id"] == "job-1"
```

Rejected call：

```python
def test_rejected_call_status_reports_active_job_without_execution(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    service.create_rejected_call_record(call_id="call-busy", method="daemon.request_submit", rejection_code="DAEMON_BUSY", active_job_id="job-active", active_command="cw.battle.run", active_session="sess-active")

    status = service.request_status("call-busy")

    assert status["state"] == "rejected"
    assert status["final"] is True
    assert status["executed"] is False
    assert status["active_request"] == "job-active"
    assert status["active_command"] == "cw.battle.run"
    assert status["active_session"] == "sess-active"
```

Session mutation lease：

```python
def test_session_mutation_lease_covers_full_read_modify_write_transaction(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    entered = threading.Event()
    release = threading.Event()

    def transaction(label: str):
        with service.session_mutation_lock(session.session_id):
            loaded = service.load_session(session.session_id)
            entered.set()
            if label == "first":
                release.wait(2)
            loaded.scene_state.setdefault("test", {})[label] = True
            service.save_session(loaded)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(transaction, "first")
        assert entered.wait(1)
        second = pool.submit(transaction, "second")
        assert not second.done()
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)

    state = service.load_session(session.session_id).scene_state["test"]
    assert state == {"first": True, "second": True}
```

- [ ] **步骤 2：编写 atomic save 与无死锁失败用例**

```python
def test_session_store_save_uses_atomic_replace(tmp_path: Path, monkeypatch):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    path = service._store._path_for(session.session_id)
    original = path.read_text(encoding="utf-8")

    def fail_replace(self, target):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    session.scene_state.setdefault("daemon", {})["tainted"] = True

    with pytest.raises(OSError):
        service.save_session(session)

    assert path.read_text(encoding="utf-8") == original


def test_finish_mutation_and_reconcile_do_not_deadlock_with_session_lock(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-lock", command_name="input.click")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            service.finish_mutation,
            session_id=session.session_id,
            request_id="req-lock",
            command_name="input.click",
            final_state="completed",
            envelope=_envelope(data={"clicked": True}),
        )
        future.result(timeout=2)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.reconcile_session, session.session_id)
        assert future.result(timeout=2)["tainted"] is False
```

- [ ] **步骤 3：运行 session 红灯**

运行：

```bash
uv run pytest --basetemp .pytest-tmp-task3-red tests/test_daemon_session_service.py::test_job_and_call_records_drive_request_status tests/test_daemon_session_service.py::test_rejected_call_status_reports_active_job_without_execution tests/test_daemon_session_service.py::test_session_store_save_uses_atomic_replace tests/test_daemon_session_service.py::test_finish_mutation_and_reconcile_do_not_deadlock_with_session_lock -v
```

预期：FAIL。

- [ ] **步骤 4：实现 session store 与 job/call API**

`SessionStore.save()` 使用 temp file + `replace()`。

`SessionService`：

```python
self._mutex = RLock()
self._jobs_root = self.workspace_root / ".trail" / "jobs"
self._calls_root = self.workspace_root / ".trail" / "calls"
```

新增 API（这里是接口签名，执行时必须写完整实现，不得保留省略号）：

```python
def session_mutation_lock(self, session_id: str | None):
    """Return a context manager covering a full load -> mutate -> save transaction."""

def create_job_record(self, *, job_id: str, job_key: str, method: str, payload_digest: str, session_id: str | None) -> dict: ...
def create_call_record(self, *, call_id: str, job_id: str | None, method: str, session_id: str | None, state: str, executed: bool) -> dict: ...
def create_rejected_call_record(self, *, call_id: str, method: str, rejection_code: str, active_job_id: str, active_command: str, active_session: str | None) -> dict: ...
def get_job_record(self, job_id: str) -> dict | None: ...
def get_call_record(self, call_id: str) -> dict | None: ...
def find_active_job_by_key(self, job_key: str) -> dict | None: ...
def find_latest_job_for_method(self, method: str) -> dict | None: ...
def update_job_record(self, job_id: str, **updates: Any) -> dict: ...
def finish_job_record(self, job_id: str, *, state: str, final_state: str, last_visible_stage: str, side_effect_stage: str, envelope: dict, tainted: bool) -> dict: ...
def request_status(self, request_id: str) -> dict: ...
def install_test_lease_probe(self, session_id: str | None, *, entered: threading.Event | None = None, release: threading.Event | None = None, fail_if_entered: bool = False) -> None: ...
def install_test_lease_assertion(self, session_id: str | None, *, required_until_call_state: str) -> None: ...
```

实现体使用 `_save_json_atomic(path, record)` 保存，`request_status()` 顺序为 CallRecord -> JobRecord -> legacy request record。`RequestExecutor` 必须对 session-mutating commands（至少 `guide.fetch.cw --select`、`session.create`、所有会改 session last_result/last_screenshot 的 mutation）从业务 handler 开始到 job/session/journal 写入完成全程持有 `session_mutation_lock(session_id)`；不能只锁单次 `save_session()`。
测试探针 API 仅在测试中使用；生产实现可以把它们藏在 private helper 或 monkeypatchable hook 下，但测试必须能证明 lease 覆盖完整业务处理和 journal 持久化窗口。

命令分类边界：
- `session.create` 是 workspace 级 mutation：使用 `session_mutation_lock(None)`，并在创建后补写 call/job journal；不能与同 workspace 其他 session 创建或状态写入交错。
- `guide.fetch.cw --select` / payload `select=True` 是 session 级 mutation：使用目标 `session_id` 的 lease 覆盖攻略读取结果写入与当前攻略快照持久化。
- `state.dump` 只读 session，不持 mutation lease，但若 control-plane response 写 call record，仍通过 workspace journal RLock 原子写。
- 所有执行后写 session `last_result` / `last_screenshot` / `cw_state.*.stale` 的业务命令都按 session-mutating 处理，即使 handler 本身没有显式 mutation。
- 不带 session 的 game operation（如 `start.run`、全局 `ocr.read`）只持 game lease 与 workspace journal RLock；不得伪造 `active_session`。

- [ ] **步骤 5：运行 session 绿灯**

运行同 Step 3 命令，并加旧状态测试：

```bash
uv run pytest --basetemp .pytest-tmp-task3-green tests/test_daemon_session_service.py::test_job_and_call_records_drive_request_status tests/test_daemon_session_service.py::test_rejected_call_status_reports_active_job_without_execution tests/test_daemon_session_service.py::test_session_store_save_uses_atomic_replace tests/test_daemon_session_service.py::test_finish_mutation_and_reconcile_do_not_deadlock_with_session_lock tests/test_daemon_session_service.py::test_request_status_returns_machine_readable_fields -v
```

预期：PASS。

## 任务 4：RequestExecutor 基础，但生产 server 保持 legacy

**文件：**
- 创建：`trail/daemon/request_executor.py`
- 修改：`trail/daemon/command_service.py`
- 修改：`tests/test_daemon_async_executor.py`

- [ ] **步骤 1：建立模块级测试夹具**

在 `tests/test_daemon_async_executor.py` 顶部定义完整夹具：

```python
def _request(tmp_path: Path, *, method: str, call_id: str, payload: dict[str, Any], session_id: str | None = None, job_id: str | None = None, control: RequestControl | None = None, workspace_root: str | None = None) -> DaemonRequest:
    return DaemonRequest(
        request_id=call_id,
        call_id=call_id,
        job_id=job_id,
        protocol_version=PROTOCOL_VERSION,
        workspace_root=workspace_root or str(tmp_path),
        session_id=session_id,
        verbose=False,
        method=method,
        payload=payload,
        control=control or RequestControl(wait_timeout=0.0),
    )


class SlowBusinessService:
    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def handle_control_plane(self, request: DaemonRequest) -> dict[str, Any]:
        raise AssertionError(f"unexpected control-plane call: {request.method}")

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        self.started.set()
        self.release.wait(2)
        return command_success(data={"done": True}, screenshot=None)


class CountingService:
    def __init__(self) -> None:
        self.calls = 0

    def handle_control_plane(self, request: DaemonRequest) -> dict[str, Any]:
        raise AssertionError(f"unexpected control-plane call: {request.method}")

    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        return command_success(data={"call": self.calls}, screenshot=None)


class FastControlService(CountingService):
    def handle_control_plane(self, request: DaemonRequest) -> dict[str, Any]:
        if request.method == "daemon.reconcile_session":
            return command_success(data={"session_id": request.payload["session_id"], "tainted": False}, screenshot=None)
        if request.method == "daemon.request_status":
            return command_success(data={"request_id": request.payload["request_id"]}, screenshot=None)
        raise AssertionError(f"unexpected control-plane call: {request.method}")


class SessionMutatingService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        session_id = request.session_id or request.payload.get("session_id")
        session = request.session_service.load_session(session_id)
        session.scene_state["selected_guide"] = request.payload["guide_id"]
        request.session_service.save_session(session)
        return command_success(data={"selected": request.payload["guide_id"]}, screenshot=None)


class SessionCreateService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        session = request.session_service.create_session(window_binding=request.payload["window_binding"])
        return command_success(data={"session_id": session.session_id}, screenshot=None)


class StateDumpService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        session = request.session_service.load_session(request.payload["session_id"])
        return command_success(data={"session_id": session.session_id, "scene_state": session.scene_state}, screenshot=None)


class SessionResultWritingService(CountingService):
    def execute_business_request(self, request: DaemonRequest) -> dict[str, Any]:
        self.calls += 1
        if request.method.startswith("cw."):
            session = request.session_service.load_session(request.session_id)
            session.scene_state.setdefault("cw", {}).setdefault("shop", {})["stale"] = True
            request.session_service.save_session(session)
        return command_success(data={"method": request.method}, screenshot=".trail/shots/session-result.png")
```

- [ ] **步骤 2：编写 executor running / fast / busy 失败用例**

```python
def test_executor_returns_running_when_wait_budget_expires(tmp_path: Path):
    session_services = SessionServiceRegistry()
    business = SlowBusinessService()
    executor = RequestExecutor(command_service=business, session_service=session_services)
    request = _request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0))

    response = executor.handle(request)

    assert response["ok"] is True
    assert response["data"]["state"] == "running"
    assert response["data"]["request"]
    assert response["data"]["waited"] == 0
    assert "command" not in response["data"]
    assert business.started.wait(1)
    assert business.calls == 1
    business.release.set()


def test_executor_returns_final_business_envelope_within_wait_budget(tmp_path: Path):
    executor = RequestExecutor(command_service=CountingService(), session_service=SessionServiceRegistry())
    response = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-fast", payload={}, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is True
    assert response["data"] == {"call": 1}


def test_executor_rejects_different_game_operation_while_active(tmp_path: Path):
    business = SlowBusinessService()
    executor = RequestExecutor(command_service=business, session_service=SessionServiceRegistry())
    running = executor.handle(_request(tmp_path, method="cw.battle.run", call_id="call-1", session_id="sess-1", payload={"timeout": 90}, control=RequestControl(wait_timeout=0.0)))
    active_job = running["data"]["request"]
    assert business.started.wait(1)

    busy = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-2", payload={}, control=RequestControl(wait_timeout=0.0)))

    assert busy["ok"] is False
    assert busy["error"]["code"] == "DAEMON_BUSY"
    assert busy["data"]["active_request"] == active_job
    assert busy["data"]["active_command"] == "cw.battle.run"
    assert busy["data"]["active_session"] == "sess-1"
    assert busy["data"]["executed"] == 0
    business.release.set()
```

- [ ] **步骤 3：编写 busy no-session producer/status 与 session mutation lease 测试**

```python
def test_executor_busy_for_start_run_omits_active_session_and_status_maps_call(tmp_path: Path):
    business = SlowBusinessService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=business, session_service=registry)
    running = executor.handle(_request(tmp_path, method="start.run", call_id="call-start", payload={"window_title": "崩坏：星穹铁道", "channel": "official"}, control=RequestControl(wait_timeout=0.0)))
    active_job = running["data"]["request"]
    assert business.started.wait(1)

    busy = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-busy", payload={}, control=RequestControl(wait_timeout=0.0)))
    status = registry.for_workspace(str(tmp_path)).request_status("call-busy")

    assert busy["data"]["active_request"] == active_job
    assert busy["data"]["active_command"] == "start.run"
    assert busy["data"].get("active_session") is None
    assert busy["data"]["recover_action"] == "start.run"
    assert status["executed"] is False
    assert status["active_session"] is None
    business.release.set()


def test_executor_holds_session_mutation_lease_through_business_and_journal_writes(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    executor = RequestExecutor(command_service=SessionMutatingService(), session_service=registry)

    response = executor.handle(_request(tmp_path, method="guide.fetch.cw", call_id="call-guide", session_id=session.session_id, payload={"session_id": session.session_id, "guide_id": "g-1", "select": True}, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is True
    loaded = service.load_session(session.session_id)
    assert loaded.scene_state["selected_guide"] == "g-1"
    status = service.request_status("call-guide")
    assert status["state"] == "completed"
    assert status["executed"] is True


def test_executor_session_create_uses_workspace_lease_through_journal(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    executor = RequestExecutor(command_service=SessionCreateService(), session_service=registry)
    entered = threading.Event()
    release = threading.Event()
    service.install_test_lease_probe(session_id=None, entered=entered, release=release)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: executor.handle(_request(tmp_path, method="session.create", call_id="call-create", payload={"window_binding": {"title": "崩坏：星穹铁道", "hwnd": 1}}, control=RequestControl(wait_timeout=1.0))))
        assert entered.wait(1)
        second = pool.submit(lambda: service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 2}))
        assert not second.done()
        release.set()
        response = first.result(timeout=2)
        second.result(timeout=2)

    assert response["ok"] is True
    assert service.request_status("call-create")["state"] == "completed"


def test_executor_state_dump_is_read_only_and_does_not_hold_session_mutation_lease(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    executor = RequestExecutor(command_service=StateDumpService(), session_service=registry)
    service.install_test_lease_probe(session_id=session.session_id, fail_if_entered=True)

    response = executor.handle(_request(tmp_path, method="state.dump", call_id="call-dump", session_id=session.session_id, payload={"session_id": session.session_id}, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is True
    assert service.get_job_record("call-dump") is None
    assert service.load_session(session.session_id).scene_state == session.scene_state


@pytest.mark.parametrize("method,payload", [
    ("start.run", {"window_title": "崩坏：星穹铁道", "channel": "official"}),
    ("input.click", {"x": 1, "y": 2}),
    ("cw.shop.scan", {"session_id": "sess-1"}),
])
def test_session_result_writing_business_commands_hold_lease_until_job_and_call_journal(tmp_path: Path, method: str, payload: dict[str, Any]):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    if "session_id" in payload:
        payload = {**payload, "session_id": session.session_id}
    executor = RequestExecutor(command_service=SessionResultWritingService(), session_service=registry)
    service.install_test_lease_assertion(session.session_id, required_until_call_state="completed")

    response = executor.handle(_request(tmp_path, method=method, call_id=f"call-{method}", session_id=session.session_id, payload=payload, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is True
    assert service.request_status(f"call-{method}")["state"] == "completed"
    loaded = service.load_session(session.session_id)
    assert loaded.last_result is not None
    assert loaded.last_screenshot == ".trail/shots/session-result.png"
    if method.startswith("cw."):
        assert loaded.scene_state["cw"]["shop"]["stale"] is True
```

- [ ] **步骤 4：运行 executor 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task4-red tests/test_daemon_async_executor.py::test_executor_returns_running_when_wait_budget_expires tests/test_daemon_async_executor.py::test_executor_returns_final_business_envelope_within_wait_budget tests/test_daemon_async_executor.py::test_executor_rejects_different_game_operation_while_active tests/test_daemon_async_executor.py::test_executor_busy_for_start_run_omits_active_session_and_status_maps_call tests/test_daemon_async_executor.py::test_executor_holds_session_mutation_lease_through_business_and_journal_writes tests/test_daemon_async_executor.py::test_executor_session_create_uses_workspace_lease_through_journal tests/test_daemon_async_executor.py::test_executor_state_dump_is_read_only_and_does_not_hold_session_mutation_lease tests/test_daemon_async_executor.py::test_session_result_writing_business_commands_hold_lease_until_job_and_call_journal -v
```

预期：FAIL，`RequestExecutor` 不存在。

- [ ] **步骤 5：实现 RequestExecutor 基础，不接生产 server**

`trail/daemon/request_executor.py` 实现：

- `CONTROL_PLANE_METHODS`：`daemon.status`、`daemon.request_status`、`daemon.request_result`、`daemon.request_cancel`、`daemon.logs`、`daemon.stop`、`daemon.restart`、`daemon.reconcile_session`、`daemon.ping`。
- `is_game_operation(method)`：`method in {"start.run", "ocr.read", "screen.shot"}` 或 startswith `cw.` / `input.` / `window.` / `image.`。
- `canonical_payload_digest(payload)`：`json.dumps(to_jsonable(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"))` 后 hash。
- `job_key`：`workspace_root + session_id + method + digest`。
- 新 job：生成 `job_id=uuid4().hex`，用 `job_id` 构造 worker request（`request_id=job_id`、`call_id` 保留原 call id），并把 workspace `SessionService` 注入 worker request，写 JobRecord 和 CallRecord。
- Future done callback：在同一个 session mutation lease 内保存 last_envelope、job final record、call completed state，并释放 game lease；有 session 的 business mutation 从 handler 入口到所有 session/job/call 持久化完成全程持锁。
- timeout：返回 `command_success(data={"state": "running", "request": job_id, "waited": int(wait_timeout)}, screenshot=None)`。
- busy：写 rejected CallRecord 并返回 `command_failure(code="DAEMON_BUSY", data={...}, debug={"request_id": call_id})`。

- [ ] **步骤 6：CommandService 增加双入口但不改行为**

```python
def handle_control_plane(self, request):
    if request.method in {"daemon.ping", "daemon.request_status", "daemon.reconcile_session"}:
        return self.handle(request)
    raise TrailError("DAEMON_METHOD_NOT_CONTROL_PLANE", f"not a control-plane method: {request.method}")


def execute_business_request(self, request):
    return self.handle(request)
```

后续任务再补 request-result/cancel。

- [ ] **步骤 7：运行 executor 绿灯**

运行同 Step 4 命令，预期 PASS。

## 任务 5：job_key 重发、terminal replay 与 legacy journal 边界

**文件：**
- 修改：`trail/daemon/request_executor.py`
- 修改：`trail/daemon/session_service.py`
- 修改：`trail/daemon/command_service.py`
- 修改：`tests/test_daemon_async_executor.py`
- 修改：`tests/test_daemon_protocol.py`

- [ ] **步骤 1：编写重发不重复执行失败用例**

```python
def test_resending_same_business_command_attaches_to_running_job_without_duplicate_execution(tmp_path: Path):
    business = SlowBusinessService()
    executor = RequestExecutor(command_service=business, session_service=SessionServiceRegistry())
    first = executor.handle(_request(tmp_path, method="cw.battle.run", call_id="call-1", session_id="sess-1", payload={"timeout": 90}, control=RequestControl(wait_timeout=0.0)))
    assert business.started.wait(1)

    second = executor.handle(_request(tmp_path, method="cw.battle.run", call_id="call-2", session_id="sess-1", payload={"timeout": 90}, control=RequestControl(wait_timeout=0.0)))

    assert second["data"]["request"] == first["data"]["request"]
    assert business.calls == 1
    business.release.set()
```

Terminal 不 replay 与显式 replay：

```python
def test_without_request_id_terminal_same_payload_creates_new_job(tmp_path: Path):
    service = CountingService()
    executor = RequestExecutor(command_service=service, session_service=SessionServiceRegistry())

    first = executor.handle(_request(tmp_path, method="input.click", call_id="call-1", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0)))
    second = executor.handle(_request(tmp_path, method="input.click", call_id="call-2", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0)))

    assert first["data"] == {"call": 1}
    assert second["data"] == {"call": 2}


def test_explicit_request_id_replays_terminal_job_without_handler_call(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    first = executor.handle(_request(tmp_path, method="input.click", call_id="call-1", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0)))
    job_id = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("input.click")["job_id"]

    replay = executor.handle(_request(tmp_path, method="input.click", call_id="call-2", job_id=job_id, payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0)))

    assert replay["data"] == first["data"]
    assert service.calls == 1
```

- [ ] **步骤 2：编写真实 CommandService legacy journal 边界失败用例**

在 `tests/test_daemon_protocol.py` 增加：

```python
def test_executor_managed_mutation_uses_job_id_for_legacy_journal_and_call_status_maps_to_job(tmp_path: Path):
    runtime = ProtocolRuntime(tmp_path / "shot.png")
    runtime_service = ProtocolRuntimeService(runtime)
    session_services = SessionServiceRegistry()
    command_service = CommandService(runtime_service=runtime_service, session_service=session_services)
    executor = RequestExecutor(command_service=command_service, session_service=session_services)

    response = executor.handle(_request(tmp_path, method="input.click", call_id="call-click", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0)))
    service = session_services.for_workspace(str(tmp_path))
    job = service.find_latest_job_for_method("input.click")
    legacy_status = service.request_status(job["job_id"])
    call_status = service.request_status("call-click")

    assert response["ok"] is True
    assert job["job_id"] != "call-click"
    assert legacy_status["request_id"] == job["job_id"]
    assert call_status["request_id"] == "call-click"
    assert call_status["job_id"] == job["job_id"]
    assert call_status["method"] == "input.click"
    assert service.get_call_record("call-click") is not None
    assert not (Path(tmp_path) / ".trail" / "requests" / "call-click.json").exists()
```

- [ ] **步骤 3：编写 mismatch 和 call-id/job-id 混淆失败用例**

```python
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"method": "ocr.read"}, "REQUEST_JOB_METHOD_MISMATCH"),
        ({"session_id": "other-session"}, "REQUEST_JOB_SESSION_MISMATCH"),
        ({"workspace_root": "other"}, "REQUEST_JOB_WORKSPACE_MISMATCH"),
    ],
)
def test_explicit_job_id_mismatch_does_not_execute_handler(tmp_path: Path, change: dict[str, str], code: str):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    executor.handle(_request(tmp_path, method="cw.battle.run", call_id="call-1", session_id="sess-1", payload={"timeout": 90}, control=RequestControl(wait_timeout=1.0)))
    job_id = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("cw.battle.run")["job_id"]
    workspace_root = str(tmp_path / change["workspace_root"]) if "workspace_root" in change else str(tmp_path)

    response = executor.handle(_request(tmp_path, method=change.get("method", "cw.battle.run"), call_id="call-2", job_id=job_id, session_id=change.get("session_id", "sess-1"), payload={"timeout": 90}, workspace_root=workspace_root, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is False
    assert response["error"]["code"] == code
    assert service.calls == 1


def test_explicit_request_id_that_matches_call_record_is_rejected_without_execution(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    executor.handle(_request(tmp_path, method="ocr.read", call_id="call-original", payload={}, control=RequestControl(wait_timeout=1.0)))

    response = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-2", job_id="call-original", payload={}, control=RequestControl(wait_timeout=1.0)))

    assert response["ok"] is False
    assert response["error"]["code"] == "REQUEST_ID_IS_CALL_ID"
    assert service.calls == 1
```

- [ ] **步骤 4：运行重发红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task5-red tests/test_daemon_async_executor.py::test_resending_same_business_command_attaches_to_running_job_without_duplicate_execution tests/test_daemon_async_executor.py::test_without_request_id_terminal_same_payload_creates_new_job tests/test_daemon_async_executor.py::test_explicit_request_id_replays_terminal_job_without_handler_call tests/test_daemon_async_executor.py::test_explicit_job_id_mismatch_does_not_execute_handler tests/test_daemon_async_executor.py::test_explicit_request_id_that_matches_call_record_is_rejected_without_execution tests/test_daemon_protocol.py::test_executor_managed_mutation_uses_job_id_for_legacy_journal_and_call_status_maps_to_job -v
```

预期：FAIL。

- [ ] **步骤 5：实现 job lookup 与 legacy journal 边界**

- `RequestExecutor` worker request 必须用 `job_id` 作为 `request_id`。
- `CommandService.execute_business_request()` 不新增默认 debug 事实；快速完成最终业务 envelope 不暴露 job id。
- `_run_mutation()` duplicate/replay 分支仍可处理 legacy same `request_id`，但 executor 重发不会再用新 call_id 执行业务，因此不会触发 legacy duplicate。
- `SessionService.find_latest_job_for_method(method)` 仅用于测试，返回最近 job record；不要在用户输出依赖它。
- `request_status(call_id)` 必须通过 CallRecord 映射到 JobRecord；不得要求 call_id 查不到。
- `request_status(call_id)` 返回的 dict 必须保留 `request_id=<call_id>` 和 `job_id=<job_id>`，并提供 `next_request_id=<job_id>` 给 renderer/control-plane recovery 使用。
- 若显式 `job_id` 命中 CallRecord 而非 JobRecord，返回 `REQUEST_ID_IS_CALL_ID`，handler 不执行。

- [ ] **步骤 6：运行重发绿灯**

运行同 Step 4 命令，预期 PASS。

## 任务 6：renderer async running、busy、status、cancel

**文件：**
- 修改：`trail/output/rendering.py`
- 修改：`tests/test_output_rendering.py`
- 修改：`tests/test_cli_output_protocol.py`

- [ ] **步骤 1：补充测试 imports**

在 `tests/test_output_rendering.py` 确保导入：

```python
from trail.output.envelope import command_failure, command_success
```

- [ ] **步骤 2：编写 renderer 失败用例**

在 `tests/test_output_rendering.py` 增加：

```python
@pytest.mark.parametrize("command", ["start.run", "cw.battle.run", "cw.enter"])
def test_render_output_async_running_short_circuits_handoff(command: str):
    payload = command_success(data={"state": "running", "request": "job-1", "waited": 100}, screenshot=None)

    assert render_output(command, payload).splitlines() == [
        f"ok {command} state=running request=job-1 waited=100",
        f"info next_action={command} request=job-1",
    ]
```

Busy with session and without session：

```python
def test_render_output_daemon_busy_uses_request_submit_recover_action_with_session():
    payload = command_failure(code="DAEMON_BUSY", message="another game operation is running", data={"active_request": "job-active", "active_command": "cw.battle.run", "active_session": "sess-1", "executed": 0, "recover_action": "cw.battle.run"}, debug={"request_id": "call-busy"})

    assert render_output("ocr.read", payload).splitlines() == [
        "fail daemon.request_submit code=DAEMON_BUSY active_request=job-active active_command=cw.battle.run active_session=sess-1",
        "request id=call-busy",
        'why msg="another game operation is running"',
        "recover action=cw.battle.run request=job-active session=sess-1",
    ]


def test_render_output_daemon_busy_omits_null_active_session_for_start_run():
    payload = command_failure(code="DAEMON_BUSY", message="another game operation is running", data={"active_request": "job-start", "active_command": "start.run", "active_session": None, "executed": 0, "recover_action": "start.run"}, debug={"request_id": "call-busy"})

    assert render_output("ocr.read", payload).splitlines() == [
        "fail daemon.request_submit code=DAEMON_BUSY active_request=job-start active_command=start.run",
        "request id=call-busy",
        'why msg="another game operation is running"',
        "recover action=start.run request=job-start",
    ]
```

request-status 与 cancel：

```python
def test_render_output_request_status_includes_async_fields_and_next_action():
    payload = command_success(data={"request_id": "job-1", "method": "cw.battle.run", "session_id": "sess-1", "state": "running", "final": False, "final_state": None, "last_visible_stage": "executing", "side_effect_stage": "none", "tainted": False}, screenshot=None)

    assert render_output("daemon.request_status", payload).splitlines() == [
        "ok daemon.request_status request=job-1 command=cw.battle.run session=sess-1 state=running final=0 final_state=null last_visible_stage=executing side_effect_stage=none tainted=0",
        "info next_action=cw.battle.run request=job-1 session=sess-1",
    ]


def test_render_output_request_status_for_call_record_uses_job_id_in_next_action():
    payload = command_success(data={"request_id": "call-1", "job_id": "job-1", "next_request_id": "job-1", "method": "cw.battle.run", "session_id": "sess-1", "state": "running", "final": False, "final_state": None, "last_visible_stage": "executing", "side_effect_stage": "none", "tainted": False}, screenshot=None)

    assert render_output("daemon.request_status", payload).splitlines() == [
        "ok daemon.request_status request=call-1 job=job-1 command=cw.battle.run session=sess-1 state=running final=0 final_state=null last_visible_stage=executing side_effect_stage=none tainted=0",
        "info next_action=cw.battle.run request=job-1 session=sess-1",
    ]


def test_render_output_request_status_for_rejected_call_uses_active_request_in_next_action():
    payload = command_success(data={"request_id": "call-busy", "method": "daemon.request_submit", "state": "rejected", "final": True, "final_state": "failed_before_side_effect", "last_visible_stage": "rejected", "side_effect_stage": "none", "tainted": False, "executed": False, "active_request": "job-active", "active_command": "cw.battle.run", "active_session": "sess-1"}, screenshot=None)

    assert render_output("daemon.request_status", payload).splitlines() == [
        "ok daemon.request_status request=call-busy command=daemon.request_submit state=rejected final=1 final_state=failed_before_side_effect last_visible_stage=rejected side_effect_stage=none tainted=0 executed=0 active_request=job-active active_command=cw.battle.run active_session=sess-1",
        "info next_action=cw.battle.run request=job-active session=sess-1",
    ]


def test_render_output_request_status_cancel_unknown_emits_reconcile_recover():
    payload = command_success(data={"request_id": "job-1", "method": "input.click", "session_id": "sess-1", "state": "cancel_unknown", "final": True, "final_state": "applied_but_not_persisted", "last_visible_stage": "side_effect_applied", "side_effect_stage": "applied", "tainted": True}, screenshot=None)

    assert render_output("daemon.request_status", payload).splitlines() == [
        "ok daemon.request_status request=job-1 command=input.click session=sess-1 state=cancel_unknown final=1 final_state=applied_but_not_persisted last_visible_stage=side_effect_applied side_effect_stage=applied tainted=1",
        "recover action=daemon.reconcile_session session=sess-1",
    ]


def test_render_output_request_cancel():
    payload = command_success(data={"request_id": "job-1", "state": "cancel_requested", "command": "start.run"}, screenshot=None)

    assert render_output("daemon.request_cancel", payload).splitlines() == [
        "ok daemon.request_cancel request=job-1 state=cancel_requested command=start.run",
        "info next_action=start.run request=job-1",
    ]
```

- [ ] **步骤 3：运行 renderer 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task6-red tests/test_output_rendering.py::test_render_output_async_running_short_circuits_handoff tests/test_output_rendering.py::test_render_output_daemon_busy_uses_request_submit_recover_action_with_session tests/test_output_rendering.py::test_render_output_daemon_busy_omits_null_active_session_for_start_run tests/test_output_rendering.py::test_render_output_request_status_includes_async_fields_and_next_action tests/test_output_rendering.py::test_render_output_request_status_for_call_record_uses_job_id_in_next_action tests/test_output_rendering.py::test_render_output_request_status_for_rejected_call_uses_active_request_in_next_action tests/test_output_rendering.py::test_render_output_request_status_cancel_unknown_emits_reconcile_recover tests/test_output_rendering.py::test_render_output_request_cancel -v
```

预期：FAIL。

- [ ] **步骤 4：实现 renderer**

- `_render_text_lines()` 成功分支最前面检测 `data.state == "running"` 并返回 `_render_async_running()`，不得调用 `_finalize_success_lines()`。
- `_render_failure_lines()` 最前面检测 `error.code == "DAEMON_BUSY"` 并返回 `_render_daemon_busy_failure()`。
- `_render_daemon_request_status()` 输出 `request/job/command/session/state/final/final_state/last_visible_stage/side_effect_stage/tainted/executed/active_request/active_command/active_session`。对 `daemon.request_status`，`final_state is None` 必须编码为 `final_state=null`，因为它是恢复 must-keep 字段；其他 None 继续按普通 omit 规则处理。若 status 来自 CallRecord 且包含 `job_id`，首行使用 `request=<call_id> job=<job_id>`，但 `info next_action` 必须使用 `next_request_id`（没有时退回 `job_id`，再退回 `request_id`），不得把 call_id 当 job id 提示给 Agent。若 `state=rejected` 且有 `active_request/active_command`，`info next_action` 必须使用 `active_command` 与 `active_request`，并在 `active_session` 非空时带 `session=<active_session>`；不得回退到 rejected call_id。若 `state=cancel_unknown` 且有 `session_id`，不得输出业务 `next_action`，必须输出 `recover action=daemon.reconcile_session session=<session_id>`。
- 新增 `daemon.request_cancel` renderer。

- [ ] **步骤 5：运行 renderer 绿灯**

运行同 Step 3 命令，预期 PASS。

## 任务 7：request-result、request-cancel 与 force unsupported

**文件：**
- 修改：`trail/daemon/command_service.py`
- 修改：`trail/daemon/request_executor.py`
- 修改：`trail/commands/daemon.py`
- 修改：`tests/test_daemon_async_executor.py`
- 修改：`tests/test_cli_output_protocol.py`

- [ ] **步骤 1：补充 CLI 输出测试 imports**

在 `tests/test_cli_output_protocol.py` 增加：

```python
import pytest
from trail.output.envelope import command_failure
from trail.output.rendering import render_output
```

- [ ] **步骤 2：编写 request-result exact dispatch 失败用例**

在 `tests/test_cli_output_protocol.py` 增加参数化测试：

```python
@pytest.mark.parametrize(
    ("render_command", "inner"),
    [
        ("start.run", build_success_response(request_id="job-start", data={"status": "attached", "session": "sess-1", "reused": 1, "title": "崩坏：星穹铁道", "hwnd": 123}, screenshot=".trail/shots/job-start.png")),
        ("cw.battle.run", build_success_response(request_id="job-battle", data={"status": "completed", "result": "win", "stage": "shop", "stale": False, "in_battle": False}, screenshot=".trail/shots/job-battle.png")),
        ("cw.portal.select", build_success_response(request_id="job-portal", data={"card_idx": 1, "portal_title": "传送门", "stage": "preparation", "stale": False}, screenshot=".trail/shots/job-portal.png")),
        ("input.click", command_failure(code="REQUEST_CANCELLED", message="request cancelled", screenshot=None, debug={"request_id": "job-click"})),
    ],
)
def test_daemon_request_result_renders_inner_business_envelope_exactly(cli_runner, fake_daemon_client, render_command, inner):
    if inner.get("screenshot"):
        inner["image_guidance"] = {"read_image_first": 1}
    inner["warnings"] = [{"code": "SOFT", "message": "kept"}]
    inner["references"] = [{"label": "ref", "path": "skills/trail-hsr/SKILL.md"}]
    inner["timing"] = {"elapsed_ms": 123}
    inner_debug = dict(inner.get("debug") or {})
    inner_debug.setdefault("request_id", inner.get("request_id") or "job-click")
    inner_debug["trace"] = [{"step": "demo", "ok": 1}]
    inner["debug"] = inner_debug
    fake_daemon_client({"daemon.request_result": build_success_response(request_id="call-result", data={"render_command": render_command, "envelope": inner})})

    result = cli_runner.invoke(app, ["daemon", "request-result", "--request-id", inner_debug["request_id"]])

    assert result.exit_code == 0
    assert result.stdout.strip() == render_output(render_command, inner).strip()
```

结构性透传 spy 测试：

```python
def test_daemon_request_result_passes_inner_envelope_object_to_print_output(monkeypatch, cli_runner, fake_daemon_client):
    inner = build_success_response(request_id="job-struct", data={"captured": True}, screenshot=".trail/shots/job-struct.png")
    inner["image_guidance"] = {"read_image_first": 1}
    inner["debug"] = {"trace": [{"step": "demo", "ok": 1}], "request_id": "job-struct"}
    inner["timing"] = {"elapsed_ms": 123}
    captured = {}

    def spy_print_output(command, payload):
        captured["command"] = command
        captured["payload"] = payload

    monkeypatch.setattr("trail.commands.daemon.print_output", spy_print_output)
    fake_daemon_client({"daemon.request_result": build_success_response(request_id="call-result", data={"render_command": "screen.shot", "envelope": inner})})

    result = cli_runner.invoke(app, ["daemon", "request-result", "--request-id", "job-struct"])

    assert result.exit_code == 0
    assert captured == {"command": "screen.shot", "payload": inner}
```

- [ ] **步骤 3：编写 cancel / force 失败用例**

```python
def test_request_cancel_marks_running_job_cancel_requested(tmp_path: Path):
    business = SlowBusinessService()
    executor = RequestExecutor(command_service=business, session_service=SessionServiceRegistry())
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]

    cancel = executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))

    assert cancel["ok"] is True
    assert cancel["data"] == {"request_id": job_id, "state": "cancel_requested", "command": "ocr.read"}
    assert executor.session_service.for_workspace(str(tmp_path)).request_status(job_id)["state"] == "cancel_requested"
    business.release.set()


def test_request_cancel_force_is_unsupported_and_does_not_change_running_job(tmp_path: Path):
    business = SlowBusinessService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=business, session_service=registry)
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    status_before = registry.for_workspace(str(tmp_path)).request_status(job_id)

    response = executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-force", payload={"request_id": job_id, "force": True, "confirm_taint": True}, control=RequestControl(wait_timeout=0.0)))
    status_after = registry.for_workspace(str(tmp_path)).request_status(job_id)

    assert response["ok"] is False
    assert response["error"]["code"] == "FORCE_CANCEL_NOT_SUPPORTED"
    assert status_after["state"] == status_before["state"]
    business.release.set()


def test_daemon_request_cancel_force_cli_payload(cli_runner, fake_daemon_client):
    client = fake_daemon_client({"daemon.request_cancel": command_failure(code="FORCE_CANCEL_NOT_SUPPORTED", message="force cancel requires process-isolated workers", screenshot=None)})

    result = cli_runner.invoke(app, ["daemon", "request-cancel", "--request-id", "job-1", "--force", "--confirm-taint"])

    assert result.exit_code == 0
    assert client.calls[0]["payload"] == {"request_id": "job-1", "force": True, "confirm_taint": True}
    assert result.stdout.splitlines() == [
        "fail daemon.request_cancel code=FORCE_CANCEL_NOT_SUPPORTED",
        'why msg="force cancel requires process-isolated workers"',
    ]
```

- [ ] **步骤 4：运行 request-result/cancel 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task7-red tests/test_cli_output_protocol.py::test_daemon_request_result_renders_inner_business_envelope_exactly tests/test_cli_output_protocol.py::test_daemon_request_result_passes_inner_envelope_object_to_print_output tests/test_daemon_async_executor.py::test_request_cancel_marks_running_job_cancel_requested tests/test_daemon_async_executor.py::test_request_cancel_force_is_unsupported_and_does_not_change_running_job tests/test_cli_output_protocol.py::test_daemon_request_cancel_force_cli_payload -v
```

预期：FAIL。

- [ ] **步骤 5：实现 daemon commands 与 executor control**

`daemon request-result`：提取 control envelope 中的 `data.render_command` 和 `data.envelope`，原样传 `print_output(render_command, envelope)`。

`daemon request-cancel`：支持 options：

```python
request_id: Annotated[str, typer.Option("--request-id")]
force: Annotated[bool, typer.Option("--force")] = False
confirm_taint: Annotated[bool, typer.Option("--confirm-taint")] = False
```

force 为真时 payload 包含 `force` / `confirm_taint`，executor 第一版返回 `FORCE_CANCEL_NOT_SUPPORTED`，且不改变 token/job state。

`RequestExecutor` control-plane：

- `daemon.request_result`：查 job last_envelope，返回 `command_success(data={"render_command": job["method"], "envelope": last_envelope})`。
- `daemon.request_cancel`：running job token.cancel()，状态更新 `cancel_requested`；completed job 返回 `state=completed_already`；force 返回 unsupported。

- [ ] **步骤 6：运行 request-result/cancel 绿灯**

运行同 Step 4 命令，预期 PASS。

## 任务 8：确定性 soft cancel 安全点

**文件：**
- 修改：`trail/daemon/models.py`
- 修改：`trail/daemon/request_executor.py`
- 修改：`trail/daemon/command_service.py`
- 修改：`trail/daemon/runtime_service.py`
- 修改：`trail/daemon/cw_service.py`
- 修改：`trail/scenes/cw/battle.py`
- 修改：`tests/test_daemon_async_executor.py`
- 修改：`tests/test_daemon_protocol.py`
- 修改：`tests/test_daemon_session_service.py`

- [ ] **步骤 1：在测试文件补齐 blocking fake class**

在 `tests/test_daemon_async_executor.py` 模块级增加：

```python
class BlockingBeforeSideEffectService:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
    def handle_control_plane(self, request):
        raise AssertionError
    def execute_business_request(self, request):
        self.entered.set()
        self.release.wait(2)
        request.control.cancellation_token.throw_if_cancelled()
        return command_success(data={"unreachable": True}, screenshot=None)


class SideEffectThenBlockService:
    def __init__(self) -> None:
        self.side_effect_applied = threading.Event()
        self.release = threading.Event()
    def handle_control_plane(self, request):
        raise AssertionError
    def execute_business_request(self, request):
        request.control.side_effect_stage = "applied"
        self.side_effect_applied.set()
        self.release.wait(2)
        request.control.cancellation_token.throw_if_cancelled()
        return command_success(data={"unreachable": True}, screenshot=None)


class BlockingInputRuntime:
    def __init__(self) -> None:
        self.before_click_entered = threading.Event()
        self.release_before_click = threading.Event()
        self.clicks: list[tuple[int, int]] = []
        self.warnings = []
        self.references = []
    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        return None
    def collect_warnings(self):
        return []
    def match_references(self, screenshot_path, limit: int = 3):
        return []
    def consume_debug_trace(self):
        return []
    def click_point(self, x: int, y: int):
        self.before_click_entered.set()
        self.release_before_click.wait(2)
        self.clicks.append((x, y))
```

在 `tests/test_daemon_protocol.py` 模块级增加 `BlockingStartRuntimeService`、`BlockingImageWaitRuntime`、`BlockingOcrRetryRuntime`、`BlockingCwBattleService`，每个都有 `entered` / `release` Event 和最小方法；若测试需要 runtime wrapper，新增本文件内 `BlockingRuntimeService`，不要引用不存在的 `StubRuntimeService`。

- [ ] **步骤 2：编写 worker start 前与安全等待 cancel 测试**

```python
def test_cancel_before_worker_starts_finishes_with_request_cancelled_envelope(tmp_path: Path):
    service = BlockingBeforeSideEffectService()
    executor = RequestExecutor(command_service=service, session_service=SessionServiceRegistry(), start_workers_paused=True)
    submitted = executor.submit_paused_for_testing(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": submitted.job_id}, control=RequestControl(wait_timeout=0.0)))
    executor.release_paused_job_for_testing(submitted.job_id)
    final = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-replay", job_id=submitted.job_id, payload={}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCELLED"
    assert service.entered.is_set() is False


def test_cancel_before_side_effect_finishes_with_request_cancelled_envelope(tmp_path: Path):
    service = BlockingBeforeSideEffectService()
    executor = RequestExecutor(command_service=service, session_service=SessionServiceRegistry())
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert service.entered.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    service.release.set()
    final = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-replay", job_id=job_id, payload={}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCELLED"
    status = executor.session_service.for_workspace(str(tmp_path)).request_status(job_id)
    assert status["state"] == "cancelled"
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False
```

- [ ] **步骤 3：编写 input UI 动作前 cancel 与 side-effect 后 cancel_unknown 测试**

```python
def test_input_click_cancel_before_action_does_not_call_runtime(tmp_path: Path):
    runtime = BlockingInputRuntime()
    command_service = CommandService(runtime_service=BlockingRuntimeService(runtime), session_service=SessionServiceRegistry())
    executor = RequestExecutor(command_service=command_service, session_service=command_service.session_service)
    running = executor.handle(_request(tmp_path, method="input.click", call_id="call-click", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert runtime.before_click_entered.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    runtime.release_before_click.set()
    final = executor.handle(_request(tmp_path, method="input.click", call_id="call-replay", job_id=job_id, payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0)))

    assert final["error"]["code"] == "REQUEST_CANCELLED"
    assert runtime.clicks == []


def test_cancel_after_side_effect_maps_to_cancel_unknown_and_taints(tmp_path: Path):
    service = SideEffectThenBlockService()
    executor = RequestExecutor(command_service=service, session_service=SessionServiceRegistry())
    running = executor.handle(_request(tmp_path, method="input.click", call_id="call-click", payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert service.side_effect_applied.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    service.release.set()
    final = executor.handle(_request(tmp_path, method="input.click", call_id="call-replay", job_id=job_id, payload={"x": 1, "y": 2}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCEL_UNKNOWN"
    status = executor.session_service.for_workspace(str(tmp_path)).request_status(job_id)
    assert status["state"] == "cancel_unknown"
    assert status["tainted"] is True
    assert status["final_state"] == "applied_but_not_persisted"
```

- [ ] **步骤 4：编写 RuntimeService / image wait / OCR retry / CW battle loop cancel 测试**

在 `tests/test_daemon_protocol.py` 增加具名测试，使用 Event 驱动，不使用真实 sleep/timeout：

```python
def test_start_run_runtime_wait_checks_cancel_token(tmp_path: Path):
    runtime_service = BlockingStartRuntimeService()
    command_service = CommandService(runtime_service=runtime_service, session_service=SessionServiceRegistry())
    executor = RequestExecutor(command_service=command_service, session_service=command_service.session_service)
    running = executor.handle(_request(tmp_path, method="start.run", call_id="call-start", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert runtime_service.entered.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    runtime_service.release.set()
    final = executor.handle(_request(tmp_path, method="start.run", call_id="call-replay", job_id=job_id, payload={}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCELLED"


def test_image_wait_checks_cancel_token(tmp_path: Path):
    runtime = BlockingImageWaitRuntime()
    command_service = CommandService(runtime_service=BlockingRuntimeService(runtime), session_service=SessionServiceRegistry())
    executor = RequestExecutor(command_service=command_service, session_service=command_service.session_service)
    running = executor.handle(_request(tmp_path, method="image.wait", call_id="call-image", payload={"template": "demo.png", "timeout": 30}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert runtime.entered.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    runtime.release.set()
    final = executor.handle(_request(tmp_path, method="image.wait", call_id="call-replay", job_id=job_id, payload={"template": "demo.png", "timeout": 30}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCELLED"


def test_ocr_retry_loop_checks_cancel_token(tmp_path: Path):
    runtime = BlockingOcrRetryRuntime()
    command_service = CommandService(runtime_service=BlockingRuntimeService(runtime), session_service=SessionServiceRegistry())
    executor = RequestExecutor(command_service=command_service, session_service=command_service.session_service)
    running = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-ocr", payload={}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert runtime.entered.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    runtime.release.set()
    final = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-replay", job_id=job_id, payload={}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] == "REQUEST_CANCELLED"


def test_cw_battle_run_loop_checks_cancel_token(tmp_path: Path):
    battle_service = BlockingCwBattleService()
    command_service = CommandService(runtime_service=BlockingRuntimeService(battle_service.runtime), session_service=SessionServiceRegistry(), cw_service=battle_service)
    executor = RequestExecutor(command_service=command_service, session_service=command_service.session_service)
    running = executor.handle(_request(tmp_path, method="cw.battle.run", call_id="call-battle", session_id="sess-1", payload={"session_id": "sess-1"}, control=RequestControl(wait_timeout=0.0)))
    job_id = running["data"]["request"]
    assert battle_service.entered.wait(1)

    executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    battle_service.release.set()
    final = executor.handle(_request(tmp_path, method="cw.battle.run", call_id="call-replay", job_id=job_id, session_id="sess-1", payload={"session_id": "sess-1"}, control=RequestControl(wait_timeout=1.0)))

    assert final["ok"] is False
    assert final["error"]["code"] in {"REQUEST_CANCELLED", "REQUEST_CANCEL_UNKNOWN"}
```

每个 fake 必须暴露 `entered` / `release` Event，并在目标循环内调用 `request.control.cancellation_token.throw_if_cancelled()`；若目标循环已经执行 UI side effect，再取消必须落到 `REQUEST_CANCEL_UNKNOWN` 且 `tainted=1`。

- [ ] **步骤 5：运行 cancel 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task8-red tests/test_daemon_async_executor.py::test_cancel_before_worker_starts_finishes_with_request_cancelled_envelope tests/test_daemon_async_executor.py::test_cancel_before_side_effect_finishes_with_request_cancelled_envelope tests/test_daemon_async_executor.py::test_input_click_cancel_before_action_does_not_call_runtime tests/test_daemon_async_executor.py::test_cancel_after_side_effect_maps_to_cancel_unknown_and_taints tests/test_daemon_protocol.py::test_start_run_runtime_wait_checks_cancel_token tests/test_daemon_protocol.py::test_image_wait_checks_cancel_token tests/test_daemon_protocol.py::test_ocr_retry_loop_checks_cancel_token tests/test_daemon_protocol.py::test_cw_battle_run_loop_checks_cancel_token -v
```

预期：FAIL。

- [ ] **步骤 6：实现 cancel token 与安全点**

- Executor 创建 job 时生成 token，并把同一 token 注入 worker request control。
- worker 启动前检查 token。若已取消，不调用 business service。
- worker 捕获 `RequestCancelled`：若 `side_effect_stage == none`，写 `cancelled/final_state=failed_before_side_effect/tainted=0/last_envelope=REQUEST_CANCELLED`；若 side_effect 已 applied/persisted，写 `cancel_unknown` 与 `REQUEST_CANCEL_UNKNOWN`。
- `CommandService.execute_business_request()` 开始前检查 token，并把 `request.control.cancellation_token` 传入所有长等待/循环调用点。
- `_start_run()` 调用 `self.runtime_service.start_run(..., cancellation_token=request.control.cancellation_token)`；`RuntimeService.start_run()` 接收 `cancellation_token` optional 并在 attach/launch/wait loop 每轮检查。
- `_run_mutation()` 在 mark_executing 前、handler 前、UI action helper 前检查 token；`_capture_mutation_with_runtime()` action 前检查 token；input click/drag/key 执行前检查。
- `_read_ocr()` / OCR retry 或 wait 外层接收 `cancellation_token`，在 capture 前、retry 前后和空结果重试前检查；`_wait_image()` / `image.wait` 调用 runtime wait 之前、轮询每轮、返回前检查。`test_image_wait_checks_cancel_token` 必须走 `method="image.wait"`，不得用 `screen.shot` 代替。
- `CwService.handle()` / `cw.battle.run` 把 token 传给 `run_cw_battle(session, runtime=..., timeout=..., cancellation_token=request.control.cancellation_token)`；`trail/scenes/cw/battle.py::run_cw_battle()` 接 optional token，并在 `while True` 顶部、stage detection 后、OCR classify 前后、`_start_battle()` 前、`sleep()` 前、settle/game_over action 前检查。
  - `CommandService._run_cw()`、`_run_cw_with_capture()`、`_run_cw_mutation()` 调用 cw service 时统一传 `cancellation_token=request.control.cancellation_token`，`CwService.handle()` / `handle_with_capture()` / `handle_mutation()` 保持签名兼容并向内部动作继续传递。
- 无法直接传到底层第三方阻塞调用时，外层轮询每轮检查；不要只在 executor 层标记 cancel。

- [ ] **步骤 7：late cancel 测试与绿灯**

增加：

```python
def test_late_cancel_completed_already_does_not_change_last_envelope(tmp_path: Path):
    service = CountingService()
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=service, session_service=registry)
    first = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-1", payload={}, control=RequestControl(wait_timeout=1.0)))
    job_id = registry.for_workspace(str(tmp_path)).find_latest_job_for_method("ocr.read")["job_id"]

    cancel = executor.handle(_request(tmp_path, method="daemon.request_cancel", call_id="call-cancel", payload={"request_id": job_id}, control=RequestControl(wait_timeout=0.0)))
    replay = executor.handle(_request(tmp_path, method="ocr.read", call_id="call-replay", job_id=job_id, payload={}, control=RequestControl(wait_timeout=1.0)))

    assert cancel["data"]["state"] == "completed_already"
    assert replay["data"] == first["data"]
```

运行：

```bash
uv run pytest --basetemp .pytest-tmp-task8-green tests/test_daemon_async_executor.py::test_cancel_before_worker_starts_finishes_with_request_cancelled_envelope tests/test_daemon_async_executor.py::test_cancel_before_side_effect_finishes_with_request_cancelled_envelope tests/test_daemon_async_executor.py::test_input_click_cancel_before_action_does_not_call_runtime tests/test_daemon_async_executor.py::test_cancel_after_side_effect_maps_to_cancel_unknown_and_taints tests/test_daemon_async_executor.py::test_late_cancel_completed_already_does_not_change_last_envelope tests/test_daemon_protocol.py::test_start_run_runtime_wait_checks_cancel_token tests/test_daemon_protocol.py::test_image_wait_checks_cancel_token tests/test_daemon_protocol.py::test_ocr_retry_loop_checks_cancel_token tests/test_daemon_protocol.py::test_cw_battle_run_loop_checks_cancel_token -v
```

预期：PASS。

## 任务 9：request-status / reconcile_session control-plane 与锁契约

**文件：**
- 修改：`trail/daemon/request_executor.py`
- 修改：`trail/daemon/command_service.py`
- 修改：`trail/daemon/session_service.py`
- 修改：`tests/test_daemon_async_executor.py`
- 修改：`tests/test_daemon_session_service.py`

- [ ] **步骤 1：编写 reconcile control-plane 失败用例**

```python
def test_reconcile_session_runs_sync_while_game_operation_busy(tmp_path: Path):
    business = SlowBusinessService()
    executor = RequestExecutor(command_service=business, session_service=SessionServiceRegistry())
    executor.handle(_request(tmp_path, method="cw.battle.run", call_id="call-1", session_id="sess-1", payload={"timeout": 90}, control=RequestControl(wait_timeout=0.0)))

    response = executor.handle(_request(tmp_path, method="daemon.reconcile_session", call_id="call-reconcile", payload={"session_id": "sess-1"}, control=RequestControl(wait_timeout=0.0)))

    assert response["ok"] is True
    assert response["data"]["session_id"] == "sess-1"
    assert response["data"].get("state") != "running"
    business.release.set()


def test_reconcile_session_does_not_create_job_record(tmp_path: Path):
    registry = SessionServiceRegistry()
    executor = RequestExecutor(command_service=FastControlService(), session_service=registry)
    executor.handle(_request(tmp_path, method="daemon.reconcile_session", call_id="call-reconcile", payload={"session_id": "sess-1"}, control=RequestControl(wait_timeout=0.0)))

    service = registry.for_workspace(str(tmp_path))
    assert service.get_job_record("call-reconcile") is None


def test_executor_reconcile_session_waits_for_target_session_mutation_lease(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    command_service = CommandService(runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "shot.png")), session_service=registry)
    executor = RequestExecutor(command_service=command_service, session_service=registry)
    entered = threading.Event()
    release = threading.Event()

    def hold_lease():
        with service.session_mutation_lock(session.session_id):
            entered.set()
            release.wait(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(hold_lease)
        assert entered.wait(1)
        second = pool.submit(lambda: executor.handle(_request(tmp_path, method="daemon.reconcile_session", call_id="call-reconcile", session_id=session.session_id, payload={"session_id": session.session_id}, control=RequestControl(wait_timeout=0.0))))
        assert not second.done()
        release.set()
        first.result(timeout=2)
        response = second.result(timeout=2)

    assert response["ok"] is True
    assert service.get_job_record("call-reconcile") is None
```

- [ ] **步骤 2：编写同 session mutation 串行测试**

```python
def test_reconcile_session_waits_for_session_mutation_lock(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    entered = threading.Event()
    release = threading.Event()
    original_save = service._store.save

    def blocking_save(model):
        entered.set()
        release.wait(2)
        return original_save(model)

    service._store.save = blocking_save
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("daemon", {})["tainted"] = True

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.save_session, loaded)
        assert entered.wait(1)
        second = pool.submit(service.reconcile_session, session.session_id)
        assert not second.done()
        release.set()
        first.result(timeout=2)
        assert second.result(timeout=2)["tainted"] is False
```

- [ ] **步骤 3：运行 reconcile 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task9-red tests/test_daemon_async_executor.py::test_reconcile_session_runs_sync_while_game_operation_busy tests/test_daemon_async_executor.py::test_reconcile_session_does_not_create_job_record tests/test_daemon_async_executor.py::test_executor_reconcile_session_waits_for_target_session_mutation_lease tests/test_daemon_session_service.py::test_reconcile_session_waits_for_session_mutation_lock -v
```

预期：FAIL 或未覆盖。

- [ ] **步骤 4：实现 control-plane bypass 与 lock 边界**

`RequestExecutor` 对 control-plane 不检查 game-operation lease、不创建 JobRecord；`daemon.reconcile_session` 是例外：它仍不占 game-operation lease、不创建 JobRecord，但必须在调用 `CommandService.handle_control_plane()` 前持有目标 `session_mutation_lock(session_id)`，因此与同 session business mutation 串行。`CommandService.handle_control_plane()` 处理 `daemon.request_status`、`daemon.reconcile_session`、`daemon.request_result`、`daemon.request_cancel`。`SessionService` 使用 RLock 保证同一 service 内串行。

- [ ] **步骤 5：运行 reconcile 绿灯**

运行同 Step 3 命令，预期 PASS。

## 任务 10：server integration feature gate

**文件：**
- 修改：`trail/daemon/server.py`
- 修改：`tests/test_daemon_protocol.py`
- 修改：`tests/test_daemon_bootstrap.py`

- [ ] **步骤 1：编写 server 注入 executor 失败用例**

在 `tests/test_daemon_protocol.py` 增加：

```python
def test_server_parses_control_and_can_use_injected_executor(tmp_path: Path):
    class Executor:
        def __init__(self):
            self.request = None
        def handle(self, request):
            self.request = request
            return build_success_response(request_id=request.call_id, data={"ok": True})

    executor = Executor()
    server = TrailDaemonServer(command_service=CommandService(runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "shot.png"))), request_executor=executor, async_enabled=True)
    payload = _server_payload(tmp_path, token="token", request_id="call-1", method="ocr.read", payload={}, job_id="job-1", control={"mode": "async_wait", "wait_timeout": 7.0, "side_effect_stage": "none"})

    response = server.handle_payload(payload)

    assert response["ok"] is True
    assert executor.request.call_id == "call-1"
    assert executor.request.job_id == "job-1"
    assert executor.request.control.wait_timeout == 7.0

若 `tests/test_daemon_protocol.py::_server_payload()` 尚不支持 `job_id` / `control`，在同一任务先扩展该 helper：保留既有参数，新增可选 `job_id: str | None = None`、`control: dict | None = None`，仅在非 None 时写入 payload。不得新增 `_authorized_payload`。


def test_server_defaults_to_legacy_until_async_enabled(tmp_path: Path):
    class Executor:
        def handle(self, request):
            raise AssertionError("executor should not be used before default async switch")

    runtime = ProtocolRuntime(tmp_path / "shot.png")
    command_service = CommandService(runtime_service=ProtocolRuntimeService(runtime), session_service=SessionServiceRegistry())
    server = TrailDaemonServer(command_service=command_service, request_executor=Executor(), async_enabled=False)
    payload = _server_payload(tmp_path, token="token", request_id="call-legacy", method="ocr.read", payload={}, control={"mode": "async_wait", "wait_timeout": 0.0, "side_effect_stage": "none"})

    response = server.handle_payload(payload)

    assert response["ok"] is True
    assert response["request_id"] == "call-legacy"
```

- [ ] **步骤 2：运行 server 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task10-red tests/test_daemon_protocol.py::test_server_parses_control_and_can_use_injected_executor tests/test_daemon_protocol.py::test_server_defaults_to_legacy_until_async_enabled -v
```

预期：FAIL。

- [ ] **步骤 3：实现 server feature gate**

`TrailDaemonServer.__init__(..., request_executor=None, async_enabled=False)`。

`handle_payload()` 解析 `call_id/job_id/control` 后：

```python
if self.async_enabled:
    response = self.request_executor.handle(request)
else:
    response = self.command_service.handle(request)
```

默认 `async_enabled=False`，任务 14 改默认。

- [ ] **步骤 4：运行 server 绿灯与 bootstrap 局部**

```bash
uv run pytest --basetemp .pytest-tmp-task10-green tests/test_daemon_protocol.py::test_server_parses_control_and_can_use_injected_executor tests/test_daemon_protocol.py::test_server_defaults_to_legacy_until_async_enabled tests/test_daemon_bootstrap.py -v
```

预期：PASS。

## 任务 11：真实 CommandService control-plane 与 request-status/result/cancel 输出闭环

**文件：**
- 修改：`trail/daemon/command_service.py`
- 修改：`trail/daemon/request_executor.py`
- 修改：`trail/commands/daemon.py`
- 修改：`tests/test_daemon_protocol.py`
- 修改：`tests/test_cli_output_protocol.py`

- [ ] **步骤 1：编写真实 request-status/result/cancel 协议测试**

在 `tests/test_daemon_protocol.py` 增加：

```python
def test_command_service_request_result_returns_render_command_and_inner_envelope(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    envelope = build_success_response(request_id="job-1", data={"captured": True}, screenshot=".trail/shots/job-1.png")
    service.create_job_record(job_id="job-1", job_key="key", method="screen.shot", payload_digest="digest", session_id=None)
    service.finish_job_record(job_id="job-1", state="completed", final_state="completed", last_visible_stage="responded", side_effect_stage="none", envelope=envelope, tainted=False)
    command_service = CommandService(runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "shot.png")), session_service=registry)

    response = command_service.handle_control_plane(_request(tmp_path, method="daemon.request_result", call_id="call-result", payload={"request_id": "job-1"}, control=RequestControl(wait_timeout=0.0)))

    assert response["ok"] is True
    assert response["data"]["render_command"] == "screen.shot"
    assert response["data"]["envelope"] == envelope


def test_command_service_request_status_for_call_record_returns_job_next_request(tmp_path: Path):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    service.create_job_record(job_id="job-1", job_key="key", method="cw.battle.run", payload_digest="digest", session_id="sess-1")
    service.create_call_record(call_id="call-1", job_id="job-1", method="cw.battle.run", session_id="sess-1", state="attached", executed=True)
    command_service = CommandService(runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "shot.png")), session_service=registry)

    response = command_service.handle_control_plane(_request(tmp_path, method="daemon.request_status", call_id="call-status", payload={"request_id": "call-1"}, control=RequestControl(wait_timeout=0.0)))

    assert response["ok"] is True
    assert response["data"]["request_id"] == "call-1"
    assert response["data"]["job_id"] == "job-1"
    assert response["data"]["next_request_id"] == "job-1"
```

- [ ] **步骤 2：运行 control-plane 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task11-red tests/test_daemon_protocol.py::test_command_service_request_result_returns_render_command_and_inner_envelope tests/test_daemon_protocol.py::test_command_service_request_status_for_call_record_returns_job_next_request tests/test_cli_output_protocol.py::test_daemon_request_result_renders_inner_business_envelope_exactly -v
```

预期：FAIL。

- [ ] **步骤 3：实现 control-plane 闭环**

`CommandService.handle_control_plane()` 处理：

- `daemon.ping`
- `daemon.request_status`
  - 调用 `SessionService.request_status(payload["request_id"])`，返回的 `data` 原样保留 `request_id/job_id/next_request_id`，不要把 call_id 规范化成 job_id。renderer 负责首行与 next_action 的不同字段选择。
- `daemon.reconcile_session`
- `daemon.request_result`
- `daemon.request_cancel`：若由 executor 处理 token 则 CommandService 只负责 force unsupported/record lookup fallback。

- [ ] **步骤 4：运行 control-plane 绿灯**

运行同 Step 2 命令，预期 PASS。

## 任务 12：文档与 skills 原子切换

**文件：**
- 修改：`AGENTS.md`
- 修改：`README.md`
- 修改：`skills/trail-hsr/SKILL.md`
- 修改：`skills/trail-hsr/references/simple-command-surface.md`
- 修改：`skills/trail-hsr/references/start-run-status-handling.md`
- 修改：`skills/trail-hsr/references/ocr-and-screenshot.md`
- 修改：`skills/trail-hsr-advanced/SKILL.md`
- 修改：`skills/trail-hsr-advanced/references/advanced-command-surface.md`
- 修改：`skills/trail-hsr-advanced/references/request-status-and-taint.md`
- 修改：`skills/trail-hsr-advanced/references/recovery-ladder.md`
- 修改：`skills/trail-cw-entry/SKILL.md`
- 修改：`skills/trail-cw-prep/SKILL.md`
- 修改：`skills/trail-cw-prep/references/command-surface.md`
- 修改：`skills/trail-cw-guide/SKILL.md`
- 修改：`tests/test_skill_structure.py`
- 修改：`tests/test_skill_routing_contracts.py`

- [ ] **步骤 1：编写文档契约失败用例**

在 `tests/test_skill_structure.py` 增加：

```python
def test_docs_describe_daemon_async_business_resend_contract():
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    hsr = (PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md").read_text(encoding="utf-8")
    entry = (PROJECT_ROOT / "skills" / "trail-cw-entry" / "SKILL.md").read_text(encoding="utf-8")
    prep = (PROJECT_ROOT / "skills" / "trail-cw-prep" / "SKILL.md").read_text(encoding="utf-8")
    prep_surface = (PROJECT_ROOT / "skills" / "trail-cw-prep" / "references" / "command-surface.md").read_text(encoding="utf-8")
    guide = (PROJECT_ROOT / "skills" / "trail-cw-guide" / "SKILL.md").read_text(encoding="utf-8")
    advanced = (PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "references" / "request-status-and-taint.md").read_text(encoding="utf-8")

    for needle in ["ok <command> state=running request=<job_id> waited=<n>", "重发同一条业务命令", "daemon.request_status request=<call_id> job=<job_id>", "next_action 使用 job_id", "daemon.request_result", "daemon.request_cancel", "DAEMON_BUSY", "active_request", "active_session"]:
        assert needle in agents
    assert "--wait-timeout" in readme
    assert "--no-wait" in readme
    assert "不要把 state=running 当成 cw.battle.run status=in_progress" in hsr
    assert "state=running 不触发 handoff" in entry
    assert "state=running 不触发 trail-cw-prep" in prep
    assert "daemon.request_result" in prep_surface
    assert "daemon.request_cancel" in guide
    assert "final_state" in advanced and "last_visible_stage" in advanced and "tainted" in advanced
```

- [ ] **步骤 2：编写 routing 契约测试，不使用伪 helper**

先读取 `tests/test_skill_routing_contracts.py` 中现有 `_trigger_rows()` / trigger fixture 结构。不要使用不存在的 runtime routing helper，也不要把 `routing-competition.json` 当成带 `.get("positive")` 的 dict；该文件当前是 list。计划要求的测试代码形态：

```python
ASYNC_CONTROL_NEGATIVE_NEEDLES = [
    "state=running request=<job_id>",
    "DAEMON_BUSY active_request",
    "daemon.request_status request=<call_id> job=<job_id>",
    "daemon.request_cancel",
    "daemon.request_result --request-id",
]


def test_daemon_async_control_outputs_are_negative_samples_for_scene_skills():
    for path in [CW_GUIDE_TRIGGERS, CW_PORTAL_TRIGGERS, CW_EVENT_UNKNOWN_TRIGGERS, CW_PREP_TRIGGERS]:
        rows = _trigger_rows(path, "should-not-trigger")
        prompts = [row["prompt"] for row in rows]
        assert all(row["expected_winner"] == "none" for row in rows)
        for needle in ASYNC_CONTROL_NEGATIVE_NEEDLES:
            assert any(needle in prompt for prompt in prompts), f"missing {needle} in {path}"


def test_daemon_async_control_outputs_are_not_scene_entry_competition_winners():
    rows = _load_json(COMPETITION)
    async_rows = [row for row in rows if row["sample_type"] == "async-control-negative"]
    assert {needle for row in async_rows for needle in ASYNC_CONTROL_NEGATIVE_NEEDLES if needle in row["prompt"]} == set(ASYNC_CONTROL_NEGATIVE_NEEDLES)
    for row in async_rows:
        assert row["expected_winner"] == "trail-hsr-advanced"
        assert set(row["candidates"]) == {"trail-hsr", "trail-hsr-advanced"}
        assert row["expected_winner"] not in {"trail-cw-entry", "trail-cw-prep", "trail-cw-guide", "trail-cw-portal"}
```

同步更新 `tests/test_skill_routing_contracts.py` 增加 `CW_PREP_TRIGGERS = PROJECT_ROOT / "skills" / "trail-cw-prep" / "evals" / "triggers.json"`，并更新 `skills/*/evals/triggers.json` 的 `should-not-trigger` 样本和 `skills/registry/routing-competition.json` 的 `async-control-negative` 样本，覆盖 async running、busy rejected、request-status call_id/job_id、request-result、request-cancel。若现有 fixture 要求 `expected_winner in candidates`，则 `async-control-negative` 的 candidates 使用 `['trail-hsr', 'trail-hsr-advanced']` 且 expected 为 `trail-hsr-advanced`；不得让 scene entry / prep / guide / portal 成为 winner。

- [ ] **步骤 3：运行文档红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task12-red tests/test_skill_structure.py::test_docs_describe_daemon_async_business_resend_contract tests/test_skill_routing_contracts.py::test_daemon_async_control_outputs_are_negative_samples_for_scene_skills tests/test_skill_routing_contracts.py::test_daemon_async_control_outputs_are_not_scene_entry_competition_winners -v
```

预期：FAIL。

- [ ] **步骤 4：更新文档与 skills**

同步以下事实：

- 默认业务命令 async wait 100 秒。
- running 输出只表示 daemon job 仍在跑，不表示业务阶段完成。
- Agent 普通查询：重发同业务命令 + `--request-id`，session-bound 命令保留 `--session`。
- `daemon.request_status`、`daemon.request_result`、`daemon.request_cancel` 是 advanced/fallback。
- busy recover 例外：recover 指向 active business command 与 job id，session-bound 时带 session。
- `daemon.request_status` 若查询的是 call_id，首行可显示 `request=<call_id> job=<job_id>`，但 `info next_action` 和 Agent 应复制的 `--request-id` 必须使用 job_id。
- 带截图最终结果仍必须先读原图；running 不产截图。
- 禁止 eager load 下游 skills：只有业务最终结果/handoff 才切 skill，`state=running` 不触发 handoff。

- [ ] **步骤 5：运行文档绿灯**

运行同 Step 3 命令，预期 PASS。

## 任务 13：默认 wait 验证但保持 production async 关闭

**文件：**
- 修改：`trail/commands/helpers.py`
- 修改：`tests/test_atomic_commands.py`

- [ ] **步骤 1：确认默认 wait_timeout=100 失败用例**

```python
def test_daemon_business_command_defaults_to_async_wait_100(cli_runner, fake_daemon_client):
    client = fake_daemon_client({"ocr.read": build_success_response(request_id="call-1", data={"result": [{"text": "x"}]})})

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 0
    assert client.calls[0]["control"] == {"mode": "async_wait", "wait_timeout": 100.0, "side_effect_stage": "none"}
```

- [ ] **步骤 2：运行默认 wait 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task13-red tests/test_atomic_commands.py::test_daemon_business_command_defaults_to_async_wait_100 -v
```

预期：FAIL 若默认未接通。

- [ ] **步骤 3：实现默认 wait control**

确保 `call_daemon()` 默认 wait_timeout 为 100，control 随所有 daemon-backed command 发送；但 server production async 仍保持 feature gate 关闭。

- [ ] **步骤 4：运行默认 wait 绿灯**

运行同 Step 2 命令，预期 PASS。

## 任务 14：生产默认 async 启用

**文件：**
- 修改：`trail/daemon/server.py`
- 修改：`tests/test_daemon_protocol.py`

- [ ] **步骤 1：确认 server async 默认启用失败用例**

```python
def test_server_defaults_to_async_after_all_gates(tmp_path: Path):
    class Executor:
        def __init__(self):
            self.called = False
        def handle(self, request):
            self.called = True
            return build_success_response(request_id=request.call_id, data={"state": "running", "request": "job", "waited": 0})

    executor = Executor()
    server = TrailDaemonServer(command_service=CommandService(runtime_service=ProtocolRuntimeService(ProtocolRuntime(tmp_path / "shot.png"))), request_executor=executor)
    response = server.handle_payload(_server_payload(tmp_path, token="token", request_id="call", method="ocr.read", payload={}, control={"mode": "async_wait", "wait_timeout": 0.0, "side_effect_stage": "none"}))

    assert executor.called is True
    assert response["data"]["state"] == "running"
```

- [ ] **步骤 2：运行 gate 前置回归**

必须先运行任务 1-13 的目标集合：

```bash
uv run pytest --basetemp .pytest-tmp-pre-async-enable tests/test_daemon_async_executor.py tests/test_daemon_protocol.py tests/test_daemon_session_service.py tests/test_cli_output_protocol.py tests/test_output_rendering.py tests/test_atomic_commands.py tests/test_cw_rpc_contracts.py tests/test_guide_rpc_contracts.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py -v
```

预期：PASS。若不 PASS，不得执行 Step 3。

- [ ] **步骤 3：运行默认 async 红灯**

```bash
uv run pytest --basetemp .pytest-tmp-task14-red tests/test_daemon_protocol.py::test_server_defaults_to_async_after_all_gates -v
```

预期：FAIL，server 默认仍 legacy。

- [ ] **步骤 4：启用生产默认 async**

把 `TrailDaemonServer.__init__(..., async_enabled=True)` 设为默认，`main()` 默认构建 `RequestExecutor`。此步骤只能在 Step 2 PASS 后执行。

- [ ] **步骤 5：运行目标回归**

```bash
uv run pytest --basetemp .pytest-tmp-task14-green tests/test_daemon_async_executor.py tests/test_daemon_protocol.py tests/test_daemon_session_service.py tests/test_cli_output_protocol.py tests/test_output_rendering.py tests/test_atomic_commands.py tests/test_cw_rpc_contracts.py tests/test_guide_rpc_contracts.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py -v
```

预期：PASS。

## 任务 15：最终验证与代码审查

**文件：**
- 不预期修改；若审查发现缺陷，回到对应任务补测试和修复。

- [ ] **步骤 1：运行目标回归**

```bash
uv run pytest --basetemp .pytest-tmp-final tests/test_daemon_async_executor.py tests/test_daemon_protocol.py tests/test_daemon_session_service.py tests/test_cli_output_protocol.py tests/test_output_rendering.py tests/test_atomic_commands.py tests/test_cw_rpc_contracts.py tests/test_guide_rpc_contracts.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py -v
```

预期：PASS。

- [ ] **步骤 2：运行快速回归**

```bash
uv run pytest --basetemp .pytest-tmp-final-full
```

预期：PASS。

- [ ] **步骤 3：运行并行快速回归**

```bash
uv run pytest -n 8 --basetemp .pytest-tmp-final-xdist
```

预期：PASS。若 Windows xdist 抖动，改跑：

```bash
uv run pytest -n 8 --dist worksteal --basetemp .pytest-tmp-final-xdist
```

- [ ] **步骤 4：手工 CLI smoke**

```bash
uv run trail version
uv run trail --no-wait ocr read
uv run trail daemon request-status --request-id nonexistent
```

预期：

- `uv run trail version` 输出当前版本。
- `--no-wait ocr read` 在 daemon 可用时要么返回 `ok ocr.read state=running ...`，要么在极快完成时返回正常 OCR 结果；不得返回 socket timeout 型 `DAEMON_UNAVAILABLE`。
- nonexistent request-status 返回明确 `REQUEST_NOT_FOUND` failure。

- [ ] **步骤 5：请求代码审查子代理**

按用户要求启动至少 3 个只读 reviewer 子代理：架构、输出契约、测试/文档。每个子代理提示词必须包含本计划完整路径、规格完整路径、修改范围、禁止修改、验收标准，并不少于 2000 字。

- [ ] **步骤 6：处理审查反馈**

若任一 reviewer 返回 Critical/Important，使用 receiving-code-review 技能处理：先核验证据，补失败测试，再修复，回跑相关测试，重复审查。全部 PASS 后才交付。

- [ ] **步骤 7：最终状态检查**

```bash
git status --short
```

记录实际修改文件；不得声明未运行的测试。最终回复只说明已完成的事实、关键测试命令和结果、剩余风险（若有）。
