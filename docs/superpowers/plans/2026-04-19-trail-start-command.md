> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。

# Trail Start Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `trail start` 简化启动入口，自动收口 daemon、游戏、窗口与 session，并把 simple / advanced 原子命令分层同步到 README / SKILL。

**Architecture:** CLI 侧新增 `trail.commands.start`，只负责 local pre-daemon install/start 收口并发起单个 daemon-side `start.run` 请求；daemon 侧新增单请求编排，负责 attach / launch / session 复用或创建。输出继续沿用现有文本协议，固定 canonical command 为 `start.run`。

**Tech Stack:** Python 3.12、Typer、daemon RPC、session store、现有 renderer/debug 协议、pytest。

---

## File Map

- Create: `trail/commands/start.py`
  责任：新增 `trail start` CLI 入口，处理 local pre-daemon install/start 收口与单次 daemon 请求发起。
- Modify: `trail/cli.py`
  责任：注册 `start` 命令组。
- Modify: `trail/commands/daemon.py`
  责任：提取可复用的本地 daemon install/start/ready helper，供 `trail start` 复用而不复制控制平面逻辑。
- Modify: `trail/daemon/command_service.py`
  责任：新增 `start.run` 编排入口，统一 attach / launch / session 复用或创建。
- Modify: `trail/daemon/runtime_service.py`
  责任：提供 daemon-side `start.run` 所需的 attach / launch / session 相关调用入口。
- Modify: `trail/daemon/session_service.py`
  责任：提供按工作区 + `window_binding` 复用 session 的查询能力，以及 tie-breaker。
- Modify: `trail/session/models.py`
  责任：为 session 增加 `updated_at`，支撑复用 tie-breaker。
- Modify: `trail/session/store.py`
  责任：提供 session 枚举与 `updated_at` 持久化，供 daemon-side 复用逻辑扫描候选 session。
- Modify: `trail/output/rendering.py`
  责任：新增 `start.run` renderer，冻结成功首行与 failure 合同。
- Modify: `trail/output/debug.py`
  责任：如有需要，承接 `start.run` 的 verbose 事实，保持走现有 debug 管线。
- Modify: `README.md`
  责任：把 `trail start` 写成默认入口，并把 `daemon/window/session` 移到 advanced 指引。
- Modify: `skills/trail-hsr/SKILL.md`
  责任：基础 skill 改成 simple-first：优先使用 `trail start`、`trail ocr read`、`trail input ...`。
- Create: `skills/trail-hsr-advanced/SKILL.md`
  责任：承接 advanced 原子命令：`trail daemon ...`、`trail window ...`、`trail session create`、`trail screen shot`、`trail image ...`、`trail state dump`。
- Modify: `skills/trail-cw/SKILL.md`
  责任：把前置环境准备改成先用 `trail start` 获得 `session=<id>`，再进入 `cw`。
- Modify: `skills/trail-cw-events/SKILL.md`
  责任：删除旧的 daemon/window 启动前置，统一改成复用已有 `session=<id>`。
- Modify: `skills/trail-cw-guide/SKILL.md`
  责任：删除旧的 daemon/window 启动前置，统一改成复用已有 `session=<id>`。
- Modify: `skills/trail-cw-replenish/SKILL.md`
  责任：删除旧的 daemon/window 启动前置，统一改成复用已有 `session=<id>`。
- Modify: `skills/trail-cw-shop/SKILL.md`
  责任：删除旧的 daemon/window 启动前置，统一改成复用已有 `session=<id>`。
- Modify: `skills/trail-cw-slots/SKILL.md`
  责任：删除旧的 daemon/window 启动前置，统一改成复用已有 `session=<id>`。
- Modify: `tests/test_atomic_commands.py`
  责任：覆盖 CLI help、`trail start` 成功/失败 stdout、simple-first 帮助文案。
- Modify: `tests/test_daemon_protocol.py`
  责任：覆盖 `start.run` daemon request / response 契约。
- Modify: `tests/test_output_rendering.py`
  责任：覆盖 `start.run` 成功首行、failure 顺序、`request id` / `recover` 语义。
- Modify: `tests/test_daemon_bootstrap.py`
  责任：覆盖 daemon install/start 在 `trail start` 本地收口链路里的基线行为。
- Modify: `tests/test_runtime_backends.py`
  责任：覆盖 attach 轮询、session 复用、tainted 排除、`already_running=1` 也进入 attach 轮询。

## Baseline Note

- 当前主分支完整测试基线应统一使用 `--basetemp .trail/pytest-tmp/...`。
- 当前启动链路仍默认文档化为 `daemon -> window -> session` 组合；本计划要把默认入口改成 `trail start`。
- 未经用户明确要求，不创建 git commit。

### Task 1: 新增 `trail start` CLI 入口与本地 pre-daemon 收口

**Files:**
- Create: `trail/commands/start.py`
- Modify: `trail/cli.py`
- Modify: `trail/commands/daemon.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_daemon_bootstrap.py`

- [ ] **Step 1: 先写失败测试，冻结 `trail start --help` 的最小合同**

```python
def test_trail_start_help_describes_simple_entry(cli_runner):
    result = cli_runner.invoke(app, ["start", "--help"])

    assert result.exit_code == 0
    assert "自动完成 daemon、游戏、窗口、session 的启动收口" in result.output
    assert "--window-title" in result.output
    assert "--game-path" in result.output
    assert "--channel" in result.output
```

- [ ] **Step 2: 先写失败测试，冻结本地 pre-daemon install/start 行为**

```python
def test_trail_start_installs_daemon_when_missing_before_dispatch(monkeypatch, cli_runner, fake_daemon_client):
    installed = []
    ready = []

    monkeypatch.setattr("trail.commands.start._ensure_local_daemon_installed", lambda: installed.append(True) or Path("C:/Users/demo/.trail-daemon"))
    monkeypatch.setattr("trail.commands.start._ensure_local_daemon_ready", lambda daemon_home: ready.append(daemon_home))

    result = cli_runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert installed == [True]
    assert ready == [Path("C:/Users/demo/.trail-daemon")]


def test_trail_start_local_install_failure_renders_fail_start_run(cli_runner, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.start._ensure_local_daemon_installed",
        lambda: (_ for _ in ()).throw(TrailError("DAEMON_INSTALL_FAILED", "daemon install failed")),
    )

    result = cli_runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail start.run code=DAEMON_INSTALL_FAILED",
        'why msg="daemon install failed"',
    ]


def test_trail_start_local_ready_failure_renders_fail_start_run(cli_runner, monkeypatch):
    monkeypatch.setattr("trail.commands.start._ensure_local_daemon_installed", lambda: Path("C:/Users/demo/.trail-daemon"))
    monkeypatch.setattr(
        "trail.commands.start._ensure_local_daemon_ready",
        lambda daemon_home: (_ for _ in ()).throw(TrailError("DAEMON_UNAVAILABLE", "daemon did not become ready in time")),
    )

    result = cli_runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail start.run code=DAEMON_UNAVAILABLE",
        'why msg="daemon did not become ready in time"',
    ]


def test_trail_start_waits_for_daemon_state_ready_not_degraded(monkeypatch, cli_runner, fake_daemon_client):
    poll_states = iter(["degraded", "ready"])
    seen_states = []
    monkeypatch.setattr("trail.commands.start._ensure_local_daemon_installed", lambda: Path("C:/Users/demo/.trail-daemon"))
    manifest = SimpleNamespace(runtime=SimpleNamespace(state="degraded"))
    monkeypatch.setattr("trail.commands.daemon._load_manifest_for_command", lambda daemon_home, request_id: (manifest, None))
    monkeypatch.setattr("trail.commands.daemon.runtime_manifest_is_live", lambda manifest: True)
    monkeypatch.setattr("trail.commands.daemon.start_bootstrap", lambda daemon_home: True)
    monkeypatch.setattr(
        "trail.commands.daemon._poll_daemon_runtime_state",
        lambda daemon_home: seen_states.append(next(poll_states)) or seen_states[-1],
    )
    fake_daemon_client(
        {
            "start.run": build_success_response(
                request_id="req-start-ready",
                data={"session": "sess-ready", "reused": 1, "title": "崩坏：星穹铁道", "hwnd": 123},
            )
        }
    )

    result = cli_runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert seen_states == ["degraded", "ready"]
```

- [ ] **Step 3: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/start-red-cli tests/test_atomic_commands.py tests/test_daemon_bootstrap.py -k "trail_start or local_install_failure or local_ready_failure or daemon_state_ready_not_degraded" -v`
Expected: FAIL，因为 `trail.commands.start` 还不存在，CLI 也未注册 `start`。

- [ ] **Step 4: 提取可复用的本地 daemon helper**

```python
# trail/commands/daemon.py
def ensure_bootstrap_installed(*, daemon_home: Path) -> Path:
    manifest_path = manifest_path_for_user(daemon_home)
    if manifest_path.exists():
        return manifest_path
    try:
        return install_bootstrap(daemon_home)
    except Exception as error:
        raise TrailError("DAEMON_INSTALL_FAILED", "daemon install failed") from error
def _poll_daemon_runtime_state(daemon_home: Path) -> str:
    manifest = load_manifest(manifest_path_for_user(daemon_home))
    return str(manifest.runtime.state)


def ensure_runtime_ready(*, daemon_home: Path) -> None:
    manifest, failure = _load_manifest_for_command(daemon_home=daemon_home, request_id="local-start-run")
    if failure is not None:
        raise TrailError(failure["error"]["code"], failure["error"]["message"])
    if runtime_manifest_is_live(manifest) and _poll_daemon_runtime_state(daemon_home) == "ready":
        return
    if not start_bootstrap(daemon_home):
        raise TrailError("DAEMON_START_FAILED", "daemon start failed")
    deadline = monotonic() + 30.0
    while monotonic() < deadline:
        manifest, failure = _load_manifest_for_command(daemon_home=daemon_home, request_id="local-start-run")
        if failure is None and runtime_manifest_is_live(manifest) and _poll_daemon_runtime_state(daemon_home) == "ready":
            return
        sleep(1.0)
    raise TrailError("DAEMON_UNAVAILABLE", "daemon did not become ready in time")
```

- [ ] **Step 5: 最小新增 CLI 入口**

```python
# trail/commands/start.py
start_app = typer.Typer(no_args_is_help=False)


def _local_start_failure(code: str, message: str) -> dict[str, Any]:
    return command_failure(code=code, message=message, screenshot=None, timing={}, warnings=[], references=[], debug=None)


def _ensure_local_daemon_installed() -> Path:
    daemon_home = resolve_daemon_home()
    ensure_bootstrap_installed(daemon_home=daemon_home)
    return daemon_home


def _ensure_local_daemon_ready(daemon_home: Path) -> None:
    ensure_runtime_ready(daemon_home=daemon_home)


@start_app.callback()
def start_command(
    window_title: str = typer.Option("崩坏：星穹铁道", "--window-title"),
    game_path: Path | None = typer.Option(None, "--game-path"),
    channel: str = typer.Option("official", "--channel"),
):
    try:
        daemon_home = _ensure_local_daemon_installed()
        _ensure_local_daemon_ready(daemon_home)
    except TrailError as error:
        print_output("start.run", _local_start_failure(error.code, str(error)))
        return
    payload = {"window_title": window_title, "channel": channel}
    if game_path is not None:
        payload["game_path"] = str(game_path)
    print_output("start.run", call_daemon("start.run", payload))
```

- [ ] **Step 6: 注册命令并跑绿灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/start-green-cli tests/test_atomic_commands.py tests/test_daemon_bootstrap.py -k "trail_start or local_install_failure or local_ready_failure or daemon_state_ready_not_degraded" -v`
Expected: PASS，`trail start` 已可见，help、本地 install/start 失败的 `fail start.run` 合同，以及 `daemon ready` 必须是 `state=ready` 都被锁住。

### Task 2: 实现 daemon-side `start.run` 编排与 session 复用

**Files:**
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/runtime_service.py`
- Modify: `trail/daemon/session_service.py`
- Modify: `trail/session/models.py`
- Modify: `trail/session/store.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_runtime_backends.py`

- [ ] **Step 1: 先在测试里定义 `start.run` 所需的最小 stubs**

```python
class StartRuntimeServiceStub:
    def __init__(self, *, attach_binding=None, launch_result=None, attach_failures_before_success=0):
        self.attach_binding = attach_binding or {"title": "崩坏：星穹铁道", "hwnd": 123}
        self.launch_result = launch_result or {"started": True, "already_running": False, "path": "demo.exe", "channel": "official", "args": []}
        self.attach_failures_before_success = attach_failures_before_success
        self.attach_attempts = 0
        self.launch_calls = []

    def attach_window(self, *, window_title: str):
        self.attach_attempts += 1
        if self.attach_attempts <= self.attach_failures_before_success:
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        return dict(self.attach_binding)

    def launch_game(self, **payload):
        self.launch_calls.append(dict(payload))
        return dict(self.launch_result)
```

- [ ] **Step 2: 先写失败测试，补齐 session 复用数据模型与 tie-breaker**

```python
def test_session_service_reuses_latest_non_tainted_matching_session(tmp_path):
    service = SessionService(workspace_root=tmp_path)

    old = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    old_path = service._store._path_for(old.session_id)
    old_payload = json.loads(old_path.read_text(encoding="utf-8"))
    old_payload["updated_at"] = "2026-04-19T00:00:00+00:00"
    old_path.write_text(json.dumps(old_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    newest = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    newest_path = service._store._path_for(newest.session_id)
    newest_payload = json.loads(newest_path.read_text(encoding="utf-8"))
    newest_payload["updated_at"] = "2026-04-19T01:00:00+00:00"
    newest_path.write_text(json.dumps(newest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    tainted = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    tainted_path = service._store._path_for(tainted.session_id)
    tainted_payload = json.loads(tainted_path.read_text(encoding="utf-8"))
    tainted_payload.setdefault("scene_state", {}).setdefault("daemon", {})["tainted"] = True
    tainted_payload["updated_at"] = "2026-04-19T02:00:00+00:00"
    tainted_path.write_text(json.dumps(tainted_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    reusable = service.find_reusable_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})

    assert reusable.session_id == newest.session_id


def test_session_service_does_not_reuse_same_title_with_different_hwnd(tmp_path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    service.save_session(session)

    reusable = service.find_reusable_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 999})

    assert reusable is None


def test_session_service_falls_back_to_created_at_when_updated_at_missing(tmp_path):
    service = SessionService(workspace_root=tmp_path)
    older = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    older_path = service._store._path_for(older.session_id)
    older_payload = json.loads(older_path.read_text(encoding="utf-8"))
    older_payload["updated_at"] = None
    older_payload["created_at"] = "2026-04-19T00:00:00+00:00"
    older_path.write_text(json.dumps(older_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    newer = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    newer_path = service._store._path_for(newer.session_id)
    newer_payload = json.loads(newer_path.read_text(encoding="utf-8"))
    newer_payload["updated_at"] = None
    newer_payload["created_at"] = "2026-04-19T01:00:00+00:00"
    newer_path.write_text(json.dumps(newer_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    reusable = service.find_reusable_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})

    assert reusable.session_id == newer.session_id
```

- [ ] **Step 3: 先写失败测试，冻结 daemon-side `start.run` 编排顺序**

```python
def test_command_service_start_run_returns_session_and_window_facts(tmp_path):
    runtime_service = StartRuntimeServiceStub()
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime_service, session_service=session_services)
    payload = service.handle(
        SimpleNamespace(
            request_id="req-start-1",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert payload["ok"] is True
    assert payload["data"]["session"]
    assert payload["data"]["reused"] in {0, 1}
    assert payload["data"]["title"] == "崩坏：星穹铁道"
    assert payload["data"]["hwnd"] == 123


def test_start_run_waits_for_attach_after_window_launch_started_or_already_running():
    tmp_path = Path(".")
    runtime = StartRuntimeServiceStub(
        launch_result={"started": False, "already_running": True, "path": "demo.exe", "channel": "official", "args": []},
        attach_failures_before_success=2,
    )
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime, session_service=session_services)
    request = SimpleNamespace(
        request_id="req-start-wait",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=None,
        verbose=False,
        method="start.run",
        payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
    )

    result = service.handle(request)

    assert result["ok"] is True
    assert runtime.attach_attempts == 3


def test_start_run_unknown_result_keeps_request_and_recover_and_taints_created_session(tmp_path):
    runtime = StartRuntimeServiceStub()
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime, session_service=session_services)
    original = service._start_run

    def exploding(request, payload, session_service):
        result = original(request, payload, session_service)
        envelope = command_failure(code="DAEMON_UNAVAILABLE", message="mutation result unknown", screenshot=None, debug={"last_known_stage": "state_persisted", "tainted": True})
        envelope["data"] = result
        raise PersistedButResponseUnknown(envelope)

    service._start_run = exploding
    response = service.handle(
        SimpleNamespace(
            request_id="req-start-unknown",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="start.run",
            payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
        )
    )

    assert response["ok"] is False
    assert response["request_id"] == "req-start-unknown"
    assert response["error"]["code"] == "DAEMON_UNAVAILABLE"
    assert response["debug"]["last_known_stage"] == "state_persisted"

    status = session_services.for_workspace(tmp_path).request_status("req-start-unknown")
    assert status["tainted"] is True
    created_session_id = status["session_id"]
    assert session_services.for_workspace(tmp_path).is_session_tainted(created_session_id) is True


def test_start_run_duplicate_request_id_after_session_created_returns_duplicate_terminal(tmp_path):
    runtime = StartRuntimeServiceStub()
    session_services = SessionServiceRegistry()
    service = CommandService(runtime_service=runtime, session_service=session_services)
    request = SimpleNamespace(
        request_id="req-start-dup",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(tmp_path),
        session_id=None,
        verbose=False,
        method="start.run",
        payload={"window_title": "崩坏：星穹铁道", "channel": "official"},
    )

    first = service.handle(request)
    second = service.handle(request)

    assert first == second
```

- [ ] **Step 4: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/start-red-daemon tests/test_daemon_protocol.py tests/test_runtime_backends.py -k "start_run or latest_non_tainted or waits_for_attach or unknown_result_keeps_request" -v`
Expected: FAIL，因为 `start.run` 还不存在，session 复用 tie-breaker、attach 轮询与 request/recover 语义也未落地。

- [ ] **Step 5: 最小实现 session 复用基础设施**

```python
# trail/session/models.py
@dataclass(slots=True)
class SessionModel:
    session_id: str
    workspace: Path
    window_binding: JsonDict
    created_at: str
    updated_at: str | None = None
    scene_state: dict[str, JsonDict] = field(default_factory=dict)
    last_result: JsonDict | None = None
    last_screenshot: str | None = None
    last_stage: JsonDict | None = None

    def to_dict(self, *, workspace_root: Path | None = None) -> JsonDict:
        return {
            "session_id": self.session_id,
            "workspace": _to_workspace_relative(self.workspace, workspace_root=workspace_root),
            "window_binding": self.window_binding,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "scene_state": self.scene_state,
            "last_result": self.last_result,
            "last_screenshot": _to_workspace_relative(self.last_screenshot, workspace_root=workspace_root),
            "last_stage": self.last_stage,
        }

    @classmethod
    def from_dict(cls, payload: JsonDict, *, workspace_root: Path | None = None) -> SessionModel:
        return cls(
            session_id=str(payload["session_id"]),
            workspace=_from_workspace_relative(payload["workspace"], workspace_root=workspace_root),
            window_binding=dict(payload["window_binding"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]) if payload.get("updated_at") is not None else None,
            scene_state=dict(payload.get("scene_state") or {}),
            last_result=payload.get("last_result"),
            last_screenshot=_to_workspace_relative(payload.get("last_screenshot"), workspace_root=workspace_root),
            last_stage=payload.get("last_stage"),
        )


# trail/session/store.py
def create(self, *, window_binding: dict) -> SessionModel:
    now = datetime.now(timezone.utc).isoformat()
    session = SessionModel(
        session_id=uuid4().hex,
        workspace=self.workspace,
        window_binding=deepcopy(window_binding),
        created_at=now,
        updated_at=now,
    )
    return self.save(session)

def list(self) -> list[SessionModel]:
    return [self.load(path.stem) for path in sorted(self.workspace.glob("*.json"))]

def save(self, session: SessionModel) -> SessionModel:
    session.updated_at = datetime.now(timezone.utc).isoformat()
    path = self.workspace / f"{self._validate_session_id(session.session_id)}.json"
    path.write_text(
        json.dumps(session.to_dict(workspace_root=self._workspace_root()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return session


# trail/daemon/session_service.py
def find_reusable_session(self, *, window_binding: dict):
    candidates = []
    for session in self._store.list():
        if session.window_binding != window_binding:
            continue
        if self.is_session_tainted(session.session_id):
            continue
        candidates.append(session)
    candidates.sort(key=lambda item: (item.updated_at or item.created_at, item.created_at), reverse=True)
    return candidates[0] if candidates else None


def finish_mutation(self, *, session_id: str | None, request_id: str, command_name: str, final_state: str, envelope: dict) -> None:
    with self._mutex:
        record = self._require_record(request_id=request_id)
        record["session_id"] = session_id
        risky_final_state = final_state in RISKY_FINAL_STATES
        record = self._apply_terminal_record(
            record=record,
            command_name=command_name,
            final_state=final_state,
            envelope=envelope,
            tainted=risky_final_state,
        )
        if risky_final_state:
            self._save_record(record)
        if session_id is not None:
            session = self.load_session(session_id)
            if risky_final_state:
                session.scene_state.setdefault("daemon", {})["tainted"] = True
            session.last_result = deepcopy(record["last_result"])
            self.save_session(session)
        if not risky_final_state:
            self._save_record(record)


def finalize_journal_record(self, *, request_id: str, command_name: str, final_state: str, envelope: dict, session_id: str | None = None) -> None:
    with self._mutex:
        record = self._require_record(request_id=request_id)
        record["session_id"] = session_id
        record = self._apply_terminal_record(
            record=record,
            command_name=command_name,
            final_state=final_state,
            envelope=envelope,
            tainted=final_state in RISKY_FINAL_STATES,
        )
        self._save_record(record)
```

- [ ] **Step 6: 最小实现 daemon-side `start.run` 编排与动态 session 绑定**

```python
def _wait_attach_ready(self, *, window_title: str, timeout_seconds: int = 30, interval_seconds: int = 1):
    last_error = None
    for _ in range(timeout_seconds):
        try:
            return to_jsonable(self.runtime_service.attach_window(window_title=window_title))
        except TrailError as error:
            last_error = error
            if error.code != "WINDOW_NOT_FOUND":
                raise
            sleep(interval_seconds)
    if isinstance(last_error, TrailError):
        raise last_error
    raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")


def _persist_terminal_envelope(
    self,
    *,
    service,
    request,
    command_name: str,
    final_state: str,
    envelope: dict[str, Any],
    session_id: str | None = None,
):
    effective_session_id = request.session_id if session_id is None else session_id
    try:
        service.finish_mutation(
            session_id=effective_session_id,
            request_id=request.request_id,
            command_name=command_name,
            final_state=final_state,
            envelope=envelope,
        )
    except Exception as recovery_error:
        try:
            service.finalize_journal_record(
                request_id=request.request_id,
                command_name=command_name,
                final_state=final_state,
                envelope=envelope,
                session_id=effective_session_id,
            )
        except Exception as finalize_error:
            payload = deepcopy(envelope)
            debug = deepcopy(payload.get("debug") or {})
            debug["recovery_detail"] = f"{type(recovery_error).__name__}: {recovery_error}"
            debug["finalize_detail"] = f"{type(finalize_error).__name__}: {finalize_error}"
            payload["debug"] = debug
            return payload
        payload = deepcopy(envelope)
        debug = deepcopy(payload.get("debug") or {})
        debug["recovery_detail"] = f"{type(recovery_error).__name__}: {recovery_error}"
        payload["debug"] = debug
        return payload
    return envelope


def _start_run(self, request, payload, service):
    window_title = payload.get("window_title", "崩坏：星穹铁道")
    try:
        binding = to_jsonable(self.runtime_service.attach_window(window_title=window_title))
    except TrailError as error:
        if error.code != "WINDOW_NOT_FOUND":
            raise
        launch_result = self.runtime_service.launch_game(game_path=payload.get("game_path"), channel=payload.get("channel", "official"))
        if not launch_result.get("started") and not launch_result.get("already_running"):
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")
        binding = self._wait_attach_ready(window_title=window_title, timeout_seconds=30, interval_seconds=1)
    session = service.find_reusable_session(window_binding=binding)
    if session is not None:
        return {"session": session.session_id, "reused": 1, "title": binding["title"], "hwnd": binding["hwnd"]}
    created = service.create_session(window_binding=binding)
    return {"session": created.session_id, "reused": 0, "title": binding["title"], "hwnd": binding["hwnd"]}


def _resolve_start_session_id(result: dict[str, Any]) -> str | None:
    return result.get("session") if isinstance(result, dict) else None


def _run_mutation(
    self,
    request,
    command_name,
    handler,
    *,
    handler_persisted_state=False,
    response_builder=None,
    enforce_cw_tainted=False,
    tainted_session_id=None,
    session_id_resolver=None,
):
    effective_session_id = request.session_id

    # 在现有 `try` 成功路径里，`handler_result = handler(service)` 之后替换为：
    handler_result = handler(service)
    if session_id_resolver is not None:
        effective_session_id = session_id_resolver(handler_result) or request.session_id
    response = response_builder(handler_result)
    result = self._response_with_request_id(request.request_id, response)

    # 在现有 `except SideEffectAppliedButStateNotPersisted as error` 分支里，保留当前分支主体不变；
    # 只在 envelope 构造完成后补 3 行：
    data_payload = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if session_id_resolver is not None:
        effective_session_id = session_id_resolver(data_payload) or request.session_id
    return self._persist_terminal_envelope(service=service, request=request, command_name=command_name, final_state="applied_but_not_persisted", envelope=envelope, session_id=effective_session_id)

    # 在现有 `except PersistedButResponseUnknown as error` 分支里，同样保留当前分支主体不变；
    # 只在 envelope 构造完成后补 3 行：
    data_payload = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if session_id_resolver is not None:
        effective_session_id = session_id_resolver(data_payload) or request.session_id
    return self._persist_terminal_envelope(service=service, request=request, command_name=command_name, final_state="persisted_but_response_unknown", envelope=envelope, session_id=effective_session_id)

    # 在现有 success 尾部的 late-failure `except Exception as error` 分支里，把：
    last_known_stage = envelope.get("debug", {}).get("last_known_stage") if isinstance(envelope.get("debug"), dict) else None
    final_state = "persisted_but_response_unknown" if last_known_stage == "state_persisted" else "applied_but_not_persisted"
    return self._persist_terminal_envelope(service=service, request=request, command_name=command_name, final_state=final_state, envelope=envelope, session_id=effective_session_id)

    # 并把现有 `_run_mutation()` 里这些调用改成：
    service.mark_side_effect_applied(request_id=request.request_id, session_id=effective_session_id, command_name=command_name)
    service.mark_state_persisted(request_id=request.request_id, session_id=effective_session_id, command_name=command_name)
    service.finish_mutation(session_id=effective_session_id, request_id=request.request_id, command_name=command_name, final_state="completed", envelope=result)


def begin_mutation(self, *, session_id: str | None, request_id: str, command_name: str, enforce_cw_tainted: bool = False):
    try:
        existing_record = self._load_record(request_id)
    except FileNotFoundError:
        existing_record = None
    if existing_record and existing_record.get("method") == "start.run" and command_name == "start.run" and session_id is None and existing_record.get("session_id") is not None:
        return {"status": "duplicate_terminal", "record": existing_record}


if request.method == "start.run":
    return self._run_mutation(
        request,
        "start.run",
        lambda service: self._start_run(request, request.payload, service),
        response_builder=success,
        session_id_resolver=_resolve_start_session_id,
    )
```

- [ ] **Step 6: 跑绿灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/start-green-daemon tests/test_daemon_protocol.py tests/test_runtime_backends.py -k "start_run or latest_non_tainted or waits_for_attach or unknown_result_keeps_request" -v`
Expected: PASS，`start.run` 的单请求编排、session 复用 tie-breaker、attach 轮询，以及 request/recover 语义都被锁住。

### Task 3: 固化 `start.run` 文本协议与 failure contract

**Files:**
- Modify: `trail/output/rendering.py`
- Modify: `trail/output/debug.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写失败测试，冻结成功首行字段顺序**

```python
def test_render_output_start_run_success_preserves_must_keep_facts():
    payload = build_success_response(
        request_id="req-start-1",
        data={"session": "sess-1", "reused": 1, "title": "崩坏：星穹铁道", "hwnd": 123},
    )

    assert render_output("start.run", payload).splitlines() == [
        "ok start.run session=sess-1 reused=1 title=崩坏：星穹铁道 hwnd=123",
    ]
```

- [ ] **Step 2: 先写失败测试，冻结 `GAME_PATH_REQUIRED` 的 request/recover 合同**

```python
def test_render_output_start_run_game_path_required_keeps_request_id_without_recover():
    payload = command_failure(code="GAME_PATH_REQUIRED", message="请提供游戏路径", screenshot=None)
    payload["debug"] = {"request_id": "req-start-path"}

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=GAME_PATH_REQUIRED",
        "request id=req-start-path",
        "why msg=请提供游戏路径",
    ]


def test_render_output_start_run_unknown_result_keeps_request_and_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-start-unknown", "last_known_stage": "side_effect_applied", "session_id": "sess-1", "tainted": 1},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-start-unknown",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-start-unknown",
    ]
```

- [ ] **Step 3: 先写失败测试，冻结 local pre-daemon failure 也必须渲染成 `start.run`**

```python
def test_trail_start_local_install_failure_renders_fail_start_run(cli_runner, monkeypatch):
    monkeypatch.setattr(
        "trail.commands.start._ensure_local_daemon_installed",
        lambda: (_ for _ in ()).throw(TrailError("DAEMON_INSTALL_FAILED", "daemon install failed")),
    )

    result = cli_runner.invoke(app, ["start"])

    assert result.stdout.splitlines() == [
        "fail start.run code=DAEMON_INSTALL_FAILED",
        'why msg="daemon install failed"',
    ]
```

- [ ] **Step 4: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/start-red-rendering tests/test_atomic_commands.py tests/test_output_rendering.py tests/test_daemon_protocol.py -k "start.run or GAME_PATH_REQUIRED or local_install_failure or unknown_result_keeps_request" -v`
Expected: FAIL，因为 `start.run` renderer 还不存在。

- [ ] **Step 5: 最小实现 `start.run` renderer 与现有 renderer 映射**

```python
def _render_start_run(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [
        f"ok {command} session={_encode_value(data.get('session'))} reused={_encode_value(data.get('reused'))} title={_encode_value(data.get('title'))} hwnd={_encode_value(data.get('hwnd'))}"
    ]


TEXT_RENDERERS["start.run"] = _render_start_run
```

- [ ] **Step 6: 跑绿灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/start-green-rendering tests/test_atomic_commands.py tests/test_output_rendering.py tests/test_daemon_protocol.py -k "start.run or GAME_PATH_REQUIRED or local_install_failure or unknown_result_keeps_request" -v`
Expected: PASS，`start.run` 成功首行、local failure、request/recover、tainted unknown-result 合同都被锁住。

### Task 4: README / SKILL 分层与 `trail start` 默认入口

**Files:**
- Modify: `README.md`
- Modify: `skills/trail-hsr/SKILL.md`
- Create: `skills/trail-hsr-advanced/SKILL.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `skills/trail-cw-events/SKILL.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `skills/trail-cw-replenish/SKILL.md`
- Modify: `skills/trail-cw-shop/SKILL.md`
- Modify: `skills/trail-cw-slots/SKILL.md`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写失败测试，锁住 `trail start --help` 与 simple-first 文档合同**

```python
def test_readme_documents_trail_start_as_default_entry():
    readme = Path("README.md").read_text(encoding="utf-8")

    quick_start = readme.split("## Quick Start", 1)[1].split("## ", 1)[0]

    assert "trail start" in readme
    assert "推荐入口" in readme
    assert "trail daemon install" not in quick_start
    assert "trail daemon status" not in quick_start
    assert "trail daemon start" not in quick_start
    assert "trail daemon request-status" not in quick_start
    assert "trail daemon reconcile-session" not in quick_start
    assert "trail window launch" not in quick_start
    assert "trail window attach" not in quick_start
    assert "trail session create" not in quick_start
    assert "trail screen shot" not in quick_start
    assert "trail image" not in quick_start
    assert "trail state dump" not in quick_start


def test_skill_docs_split_simple_and_advanced_commands():
    basic = Path("skills/trail-hsr/SKILL.md").read_text(encoding="utf-8")
    advanced = Path("skills/trail-hsr-advanced/SKILL.md").read_text(encoding="utf-8")
    cw = Path("skills/trail-cw/SKILL.md").read_text(encoding="utf-8")

    assert "trail start" in basic
    assert "trail daemon install" not in basic
    assert "trail daemon status" not in basic
    assert "trail daemon start" not in basic
    assert "trail daemon request-status" not in basic
    assert "trail daemon reconcile-session" not in basic
    assert "trail window launch" not in basic
    assert "trail window attach" not in basic
    assert "trail session create" not in basic
    assert "trail screen shot" not in basic
    assert "trail image" not in basic
    assert "trail state dump" not in basic
    assert "trail daemon install" in advanced
    assert "trail daemon status" in advanced
    assert "trail daemon start" in advanced
    assert "trail daemon request-status" in advanced
    assert "trail daemon reconcile-session" in advanced
    assert "trail window launch" in advanced
    assert "trail window attach" in advanced
    assert "trail session create" in advanced
    assert "trail screen shot" in advanced
    assert "trail image" in advanced
    assert "trail state dump" in advanced
    assert "trail start" in cw
    assert "加载 advanced skill" in basic


def test_cw_skill_family_uses_existing_session_instead_of_old_bootstrap_chain():
    for path in [
        Path("skills/trail-cw/SKILL.md"),
        Path("skills/trail-cw-events/SKILL.md"),
        Path("skills/trail-cw-guide/SKILL.md"),
        Path("skills/trail-cw-replenish/SKILL.md"),
        Path("skills/trail-cw-shop/SKILL.md"),
        Path("skills/trail-cw-slots/SKILL.md"),
    ]:
        content = path.read_text(encoding="utf-8")
        assert "trail daemon install" not in content
        assert "trail daemon status" not in content
        assert "trail daemon start" not in content
        assert "trail daemon request-status" not in content
        assert "trail daemon reconcile-session" not in content
        assert "trail window launch" not in content
        assert "trail window attach" not in content
        assert "trail session create" not in content
        assert "trail screen shot" not in content
        assert "trail image" not in content
        assert "trail state dump" not in content
        assert "使用已有 session" in content or "trail start" in content
```

- [ ] **Step 2: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/start-red-docs tests/test_atomic_commands.py tests/test_output_rendering.py -k "trail_start or advanced skill or simple" -v`
Expected: FAIL，因为 README / SKILL / help 还没切换到 simple-first，且 `trail-cw*` 仍残留旧的 daemon/window 启动前置。

- [ ] **Step 3: 最小更新文档与 skill**

```markdown
- README：把 `trail start` 写成默认入口，把 `daemon/window/session` 放到 advanced 调试流程
- skills/trail-hsr/SKILL.md：默认只教 `trail start`、`trail ocr read`、`trail input ...`
- skills/trail-hsr-advanced/SKILL.md：收录 `trail daemon install/status/start/request-status/reconcile-session`、`trail window launch/attach`、`trail session create`、`trail screen shot`、`trail image ...`、`trail state dump`
- skills/trail-cw/SKILL.md：前置说明改成先用 `trail start` 获得 session
- skills/trail-cw-events/guide/replenish/shop/slots：删除旧 daemon/window 启动前置，统一假设已有 `session=<id>` 或先回到 `trail-hsr` 使用 `trail start`
```

- [ ] **Step 4: 跑整体验收**

Run: `uv run pytest --basetemp .trail/pytest-tmp/start-final tests/test_atomic_commands.py tests/test_daemon_bootstrap.py tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_runtime_backends.py -v`
Expected: PASS，`trail start` 的 CLI / daemon / renderer / doc layering 全部收口。

## Self-Review Checklist

- Spec coverage:
  - `trail start` 自动 install daemon、daemon ready、游戏/窗口/session ready：Task 1-2 覆盖。
  - `start.run` canonical command、成功首行与 failure 合同：Task 3 覆盖。
  - simple/advanced 文档分层、README / SKILL / help：Task 4 覆盖。
- Placeholder scan: 无 `TBD` / `TODO` / 未定义 helper / 不一致命名。
- Type consistency: 统一使用 `start.run`、`reused`、`window_binding`、`trail-hsr-advanced` 这些名字，不在后续任务里改名。

## Execution Mode

按用户当前约束，后续继续使用 **Subagent-Driven**：

- 使用项目内 worktree 执行
- 每个任务 fresh implementer 子代理
- 每个任务后做 spec review + code quality review
- review 通过后自动进入下一任务
