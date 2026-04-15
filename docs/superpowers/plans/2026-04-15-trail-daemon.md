# Trail Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `trail-cli` 重构成“非管理员薄 CLI + 单管理员全量 daemon”，在保留现有命令面的同时解决管理员窗口输入、模型重复加载与跨命令状态复用问题。

**Architecture:** 新增常驻 `traild` 进程，承载 runtime、截图、OCR、图像匹配、输入、session 热状态与 `cw` 场景执行；`trail` CLI 改为 RPC 薄壳，负责 workspace 解析、daemon 发现/自动启动、认证与 envelope 渲染。Windows 启动链通过显式 `trail daemon install` 安装 bootstrap，后续普通命令自动连接/自动启动已安装 daemon。

**Tech Stack:** Python 3.12, Typer, Pillow, pywin32, pyautogui, RapidOCR, JSON manifest/session store, Windows 计划任务或等价 bootstrap, 本机 IPC/RPC

---

## 文件结构

### 新增文件

- `trail/daemon/models.py`
  - daemon manifest、runtime record、request journal、request status 的数据模型。
- `trail/daemon/manifest.py`
  - manifest 读写、ACL/路径解析、instance/token generation 更新。
- `trail/daemon/protocol.py`
  - RPC request/response schema、错误码、token 校验辅助。
- `trail/daemon/server.py`
  - daemon 启动入口、服务注册、请求路由、健康状态更新。
- `trail/daemon/bootstrap.py`
  - Windows `trail daemon install/start/stop` 所需 bootstrap 安装与调用封装。
- `trail/daemon/session_service.py`
  - 内存热状态、磁盘副本、request journal、tainted/reconcile 语义。
- `trail/daemon/runtime_service.py`
  - runtime 资源持有与 workspace/session 绑定。
- `trail/daemon/command_service.py`
  - daemon 侧命令应用服务，负责把现有 `window/screen/ocr/image/input/guide/cw` 命令面映射到服务调用。
- `trail/daemon/client.py`
  - CLI 侧 transport、manifest 发现、自动启动、request_id 生成、失败 envelope 合成。
- `trail/commands/daemon.py`
  - `trail daemon install|start|stop|status|logs|request-status|reconcile-session`。
- `tests/test_daemon_manifest.py`
  - manifest/runtime schema、token generation、workspace 归属测试。
- `tests/test_daemon_protocol.py`
  - request/response schema 与 CLI envelope 兼容测试。
- `tests/test_daemon_session_service.py`
  - request journal、tainted、失败持久化矩阵、cutover 约束测试。
- `tests/test_daemon_bootstrap.py`
  - install/start/status 控制面测试。
- `tests/support/fake_daemon.py`
  - fake daemon client/server 与 request 记录辅助。

### 修改文件

- `pyproject.toml`
  - 增加 `traild` 或等价 daemon 入口 script。
- `trail/cli.py`
  - 注册 `daemon` 命令组；普通命令切到 RPC 薄壳实现。
- `trail/commands/helpers.py`
  - 抽离本地 runtime/session store 默认构造，改成以 workspace 为核心的 CLI client helper。
- `trail/commands/window.py`
  - 改为 RPC 调用 daemon。
- `trail/commands/screen.py`
  - 改为 RPC 调用 daemon。
- `trail/commands/ocr.py`
  - 改为 RPC 调用 daemon。
- `trail/commands/image.py`
  - 改为 RPC 调用 daemon。
- `trail/commands/input.py`
  - 改为 RPC 调用 daemon。
- `trail/commands/cw.py`
  - 改为 RPC 调用 daemon 中的 `cw` 执行面。
- `trail/commands/guide.py`
  - `guide fetch|config|list` 改为 RPC 调用 daemon，同时保留当前命令面。
- `trail/commands/session.py`
  - `session create` 改为 RPC 调用 daemon。
- `trail/commands/state.py`
  - `state dump` 改为 RPC 调用 daemon。
- `tests/conftest.py`
  - 从 fake runtime 扩展为同时支持 fake daemon client/server 注入。
- `trail/output/envelope.py`
  - 增加 transport/bootstrap 失败 envelope 构造辅助，但保持对外字段集合不变。
- `README.md`
  - 说明 daemon 生命周期、bootstrap 与 RPC 薄壳模式。
- `skills/trail-hsr/SKILL.md`
  - 更新 daemon install/start/status 使用方式。
- `skills/trail-cw/SKILL.md`
  - 更新 daemon 常驻后的调试与运行前提。
- `skills/trail-cw-guide/SKILL.md`
  - 更新 guide 命令 daemon 化后的调用前提与调试方式。
- `skills/trail-cw-shop/SKILL.md`
  - 更新 shop 命令 daemon 化后的调用前提与调试方式。
- `skills/trail-cw-slots/SKILL.md`
  - 更新 slots 命令 daemon 化后的调用前提与调试方式。
- `skills/trail-cw-events/SKILL.md`
  - 更新 events 命令 daemon 化后的调用前提与调试方式。
- `skills/trail-cw-replenish/SKILL.md`
  - 更新 replenish 命令 daemon 化后的调用前提与调试方式。

---

### Task 1: 建立实现隔离工作树

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.git`（通过 git worktree 元数据）

- [ ] **Step 1: 创建项目内 worktree 目录**

Run: `mkdir .worktrees`
Expected: `.worktrees/` 目录存在

- [ ] **Step 2: 新建实现分支 worktree**

Run: `git worktree add .worktrees/trail-daemon -b feat/trail-daemon`
Expected: 新工作树创建成功，分支为 `feat/trail-daemon`

- [ ] **Step 3: 在新 worktree 验证当前基线**

Run: `git status --short`
Expected: 工作树干净

- [ ] **Step 4: 记录执行根目录**

后续所有实现、测试、review 都在：

```text
C:\Users\34404\source\repos\trail-cli\.worktrees\trail-daemon
```

- [ ] **Step 5: Commit**

不需要 commit；这是执行环境准备步骤。

### Task 2: 固化全局 manifest、协议模型与测试脚手架

**Files:**
- Create: `trail/daemon/models.py`
- Create: `trail/daemon/manifest.py`
- Create: `trail/daemon/protocol.py`
- Create: `tests/support/fake_daemon.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_daemon_manifest.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 写“manifest 不绑定 workspace”失败测试**

```python
def test_manifest_path_for_user_is_global_not_workspace_scoped(tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    workspace_root = tmp_path / "workspace-root"

    path = manifest_path_for_user(daemon_home)

    assert path == daemon_home / "traild" / "manifest.json"
    assert str(workspace_root) not in str(path)
```

- [ ] **Step 2: 写协议模型与 fake daemon harness 的失败测试**

```python
def test_fake_daemon_client_records_request_metadata():
    client = FakeDaemonClient(
        responses={
            "ocr.read": {
                "ok": True,
                "data": {"result": [{"text": "点击进入"}]},
                "screenshot": ".trail/shots/req-1.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )

    payload = client.call(
        method="ocr.read",
        payload={},
        workspace_root="C:/repo",
        session_id=None,
        verbose=False,
    )

    assert payload["ok"] is True
    assert client.calls == [
        {
            "method": "ocr.read",
            "payload": {},
            "workspace_root": "C:/repo",
            "session_id": None,
            "verbose": False,
        }
    ]
```

- [ ] **Step 3: 跑红灯验证基础设施尚不存在**

Run: `pytest tests/test_daemon_manifest.py::test_manifest_path_for_user_is_global_not_workspace_scoped tests/test_daemon_protocol.py::test_fake_daemon_client_records_request_metadata -v`
Expected: FAIL，提示 `manifest_path_for_user` 或 `FakeDaemonClient` 未定义

- [ ] **Step 4: 写最小实现**

`trail/daemon/models.py`

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InstallRecord:
    bootstrap_type: str
    bootstrap_id: str
    daemon_entrypoint: str
    manifest_version: int
    protocol_version: int
    token_file: str
    log_dir: str
    workspace_strategy: str


@dataclass
class RuntimeRecord:
    instance_id: str | None
    pid: int | None
    state: str
    endpoint: str | None
    token_generation: int | None
    updated_at: str | None
    last_transition_at: str | None
    last_start_error: str | None


@dataclass
class TrailDaemonManifest:
    install: InstallRecord
    runtime: RuntimeRecord


@dataclass
class DaemonRequest:
    request_id: str
    protocol_version: int
    workspace_root: str
    session_id: str | None
    verbose: bool
    method: str
    payload: dict
```

`trail/daemon/manifest.py`

```python
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from trail.daemon.models import InstallRecord, RuntimeRecord, TrailDaemonManifest


def manifest_path_for_user(daemon_home: Path) -> Path:
    return Path(daemon_home) / "traild" / "manifest.json"


def load_manifest(path: Path) -> TrailDaemonManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return TrailDaemonManifest(
        install=InstallRecord(**payload["install"]),
        runtime=RuntimeRecord(**payload["runtime"]),
    )


def save_manifest(path: Path, manifest: TrailDaemonManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
```

`trail/daemon/protocol.py`

```python
from __future__ import annotations

PROTOCOL_VERSION = 1
```

`tests/support/fake_daemon.py`

```python
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from threading import Lock
from threading import Thread
from time import monotonic, sleep

from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest


class FakeDaemonClient:
    def __init__(self, responses: dict[str, dict]):
        self._responses = responses
        self.calls: list[dict] = []

    def call(self, method: str, payload: dict, *, workspace_root: str | None = None, session_id: str | None = None, verbose: bool = False):
        self.calls.append(
            {
                "method": method,
                "payload": payload,
                "workspace_root": workspace_root,
                "session_id": session_id,
                "verbose": verbose,
            }
        )
        return self._responses[method]


class FakeDaemonServer:
    def __init__(self, responses: dict[str, dict]):
        self._responses = responses
        self.requests: list[dict] = []

    def handle(self, payload: dict) -> dict:
        self.requests.append(payload)
        return self._responses[payload["method"]]


def build_success_response(*, request_id: str, data: dict, screenshot: str | None = None) -> dict:
    return {
        "request_id": request_id,
        "ok": True,
        "data": data,
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }


def write_installed_manifest(daemon_home: Path, *, runtime_state: str) -> Path:
    path = manifest_path_for_user(daemon_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    token_file = daemon_home / "daemon-token.txt"
    token_file.write_text("token-1", encoding="utf-8")
    payload = {
        "install": {
            "bootstrap_type": "scheduled_task",
            "bootstrap_id": "traild-user",
            "daemon_entrypoint": "trail.daemon.server:main",
            "manifest_version": 1,
            "protocol_version": 1,
            "token_file": str(token_file),
            "log_dir": str(daemon_home / "logs"),
            "workspace_strategy": "per-request",
        },
        "runtime": {
            "instance_id": None,
            "pid": None,
            "state": runtime_state,
            "endpoint": None,
            "token_generation": None,
            "updated_at": None,
            "last_transition_at": None,
            "last_start_error": None,
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_ready_manifest(daemon_home: Path, *, endpoint: str, token_value: str) -> Path:
    path = write_installed_manifest(daemon_home, runtime_state="ready")
    token_file = daemon_home / "daemon-token.txt"
    token_file.write_text(token_value, encoding="utf-8")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["runtime"].update(
        {
            "instance_id": "inst-1",
            "pid": 1234,
            "endpoint": endpoint,
            "token_generation": 1,
            "updated_at": "2026-04-15T00:00:00+00:00",
            "last_transition_at": "2026-04-15T00:00:00+00:00",
        }
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def start_fake_daemon_server(responses: dict[str, dict]):
    server = FakeDaemonServer(responses)
    return server


def fake_round_trip_transport(server: FakeDaemonServer):
    def transport(request, token, endpoint):
        return server.handle(
            {
                "request_id": request.request_id,
                "protocol_version": request.protocol_version,
                "workspace_root": request.workspace_root,
                "session_id": request.session_id,
                "verbose": request.verbose,
                "method": request.method,
                "payload": request.payload,
                "token": token,
                "endpoint": endpoint,
            }
        )

    return transport


def start_server_in_thread(server, *, daemon_home: Path, monkeypatch) -> str:
    monkeypatch.setattr("trail.daemon.server.resolve_daemon_home", lambda: daemon_home)
    write_installed_manifest(daemon_home, runtime_state="starting")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manifest_path = manifest_path_for_user(daemon_home)
    deadline = monotonic() + 5
    while monotonic() < deadline:
        manifest = load_manifest(manifest_path)
        if manifest.runtime.endpoint:
            return str(manifest.runtime.endpoint)
        sleep(0.1)
    raise RuntimeError("fake daemon server did not publish endpoint")
```

`tests/conftest.py`

```python
from tests.support.fake_daemon import FakeDaemonClient
```

- [ ] **Step 5: 跑基础模型与 fake daemon 测试到绿**

Run: `pytest tests/test_daemon_manifest.py tests/test_daemon_protocol.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add trail/daemon/models.py trail/daemon/manifest.py trail/daemon/protocol.py tests/support/fake_daemon.py tests/conftest.py tests/test_daemon_manifest.py tests/test_daemon_protocol.py
git commit -m "feat(daemon): 建立全局 manifest 与测试脚手架"
```

### Task 3: 建立 daemon client transport 与本地失败 envelope

**Files:**
- Create: `trail/daemon/client.py`
- Modify: `trail/output/envelope.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 写“manifest 缺失时本地包 `DAEMON_BOOTSTRAP_REQUIRED`”失败测试**

```python
def test_client_returns_bootstrap_required_envelope_when_manifest_missing(tmp_path: Path):
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, endpoint: None,
    )

    payload = client.call("ocr.read", {})

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "DAEMON_BOOTSTRAP_REQUIRED",
        "message": "daemon bootstrap not installed",
    }
    assert payload["debug"]["request_id"]
```

- [ ] **Step 2: 写“请求会自动注入 request_id/workspace/version”失败测试**

```python
def test_client_call_builds_protocol_request(monkeypatch, tmp_path: Path):
    requests: list[DaemonRequest] = []

    def fake_transport(request: DaemonRequest, token: str, endpoint: str):
        requests.append(request)
        return {
            "request_id": request.request_id,
            "ok": True,
            "data": {"captured": True},
            "screenshot": ".trail/shots/req-1.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        }

    write_ready_manifest(tmp_path / "daemon-home", endpoint="127.0.0.1:8765", token_value="token-1")
    client = TrailDaemonClient(workspace_root=tmp_path, daemon_home=tmp_path / "daemon-home", transport=fake_transport)

    payload = client.call("screen.shot", {})

    assert payload["ok"] is True
    assert len(requests) == 1
    assert requests[0].workspace_root == str(tmp_path)
    assert requests[0].method == "screen.shot"
    assert requests[0].protocol_version == PROTOCOL_VERSION
    assert requests[0].request_id
```

- [ ] **Step 3: 写“成功响应会在 verbose 模式下把 `request_id` 暴露到 `debug`”失败测试**

```python
def test_client_moves_transport_request_id_into_debug_when_verbose(tmp_path: Path):
    write_ready_manifest(tmp_path / "daemon-home", endpoint="127.0.0.1:8765", token_value="token-1")

    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, endpoint: {
            "request_id": request.request_id,
            "ok": True,
            "data": {"clicked": [10, 20]},
            "screenshot": ".trail/shots/req-1.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        },
    )

    payload = client.call("input.click", {"x": 10, "y": 20}, verbose=True)

    assert payload["debug"]["request_id"]
    assert "request_id" not in payload
```

- [ ] **Step 4: 写 fake server round-trip 失败测试**

```python
def test_send_daemon_request_round_trips_with_fake_server(tmp_path: Path):
    response = build_success_response(request_id="req-1", data={"captured": True}, screenshot=".trail/shots/req-1.png")
    fake_server = start_fake_daemon_server({"screen.shot": response})

    transport = fake_round_trip_transport(fake_server)
    payload = transport(
        DaemonRequest(
            request_id="req-1",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="screen.shot",
            payload={},
        ),
        "token-1",
        "127.0.0.1:8765",
    )

    assert payload["ok"] is True
    assert fake_server.requests[0]["method"] == "screen.shot"
```

- [ ] **Step 5: 跑红灯验证 client transport 还不存在**

Run: `pytest tests/test_daemon_protocol.py::test_client_returns_bootstrap_required_envelope_when_manifest_missing tests/test_daemon_protocol.py::test_client_call_builds_protocol_request tests/test_daemon_protocol.py::test_send_daemon_request_round_trips_with_fake_server -v`
Expected: FAIL，提示 `TrailDaemonClient` 或 manifest/token 读取辅助不存在

- [ ] **Step 6: 写最小实现**

`trail/daemon/client.py`

```python
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
import socket
from uuid import uuid4

from trail.daemon.manifest import load_manifest, manifest_path_for_user
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.output.envelope import command_failure


def daemon_transport_failure(*, request_id: str, code: str, message: str, debug: dict | None = None) -> dict:
    merged_debug = {"request_id": request_id}
    if debug:
        merged_debug.update(debug)
    return command_failure(code=code, message=message, screenshot=None, timing={}, warnings=[], references=[], debug=merged_debug)


def send_daemon_request(request: DaemonRequest, token: str, *, endpoint: str, server=None) -> dict:
    if server is not None:
        return server.handle(
            {
                "request_id": request.request_id,
                "protocol_version": request.protocol_version,
                "workspace_root": request.workspace_root,
                "session_id": request.session_id,
                "verbose": request.verbose,
                "method": request.method,
                "payload": request.payload,
                "token": token,
            }
        )
    host, port_text = endpoint.split(":", 1)
    with socket.create_connection((host, int(port_text)), timeout=5) as sock:
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
        sock.sendall(json.dumps(body, ensure_ascii=False).encode("utf-8") + b"\n")
        return json.loads(sock.makefile("r", encoding="utf-8").readline())


class TrailDaemonClient:
    def __init__(self, *, workspace_root: Path, daemon_home: Path, transport):
        self.workspace_root = Path(workspace_root)
        self.daemon_home = Path(daemon_home)
        self.transport = transport

    def call(self, method: str, payload: dict, *, session_id: str | None = None, verbose: bool = False) -> dict:
        request_id = uuid4().hex
        manifest_path = manifest_path_for_user(self.daemon_home)
        if not manifest_path.exists():
            return daemon_transport_failure(request_id=request_id, code="DAEMON_BOOTSTRAP_REQUIRED", message="daemon bootstrap not installed")
        manifest = load_manifest(manifest_path)
        token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
        request = DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(self.workspace_root),
            session_id=session_id,
            verbose=verbose,
            method=method,
            payload=payload,
        )
        response = self.transport(request, token, endpoint=str(manifest.runtime.endpoint))
        returned_request_id = response.pop("request_id", request_id)
        if verbose:
            response["debug"] = {**(response.get("debug") or {}), "request_id": returned_request_id}
        return response
```

- [ ] **Step 7: 跑 client 协议测试到绿**

Run: `pytest tests/test_daemon_protocol.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add trail/daemon/client.py trail/output/envelope.py tests/test_daemon_protocol.py
git commit -m "feat(daemon): 建立 CLI transport 与失败 envelope"
```

### Task 4: 建立 bootstrap、daemon lifecycle 与 `traild` 入口

**Files:**
- Create: `trail/daemon/bootstrap.py`
- Create: `trail/daemon/server.py`
- Create: `trail/daemon/runtime_service.py`
- Create: `trail/daemon/command_service.py`
- Create: `trail/commands/daemon.py`
- Modify: `trail/daemon/client.py`
- Modify: `trail/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_daemon_bootstrap.py`

- [ ] **Step 1: 写 install/start/status 三条控制面失败测试**

```python
def test_daemon_status_reports_installed_runtime_state(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="installed")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", "status"])
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["data"]["runtime"]["state"] == "installed"


def test_daemon_install_writes_manifest(cli_runner, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: tmp_path / "daemon-home")

    result = cli_runner.invoke(app, ["daemon", "install"])
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["data"]["manifest_path"].endswith("manifest.json")


def test_daemon_start_returns_bootstrap_required_when_not_installed(cli_runner, monkeypatch, tmp_path: Path):
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: tmp_path / "daemon-home")

    result = cli_runner.invoke(app, ["daemon", "start"])
    payload = json.loads(result.stdout)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "DAEMON_BOOTSTRAP_REQUIRED"


def test_client_attempts_bootstrap_when_runtime_not_ready(tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="installed")

    started = []

    def fake_start(home: Path) -> bool:
        started.append(home)
        write_ready_manifest(home, endpoint="127.0.0.1:8765", token_value="token-1")
        return True

    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=daemon_home,
        transport=lambda request, token, endpoint: {"request_id": request.request_id, "ok": True, "data": {}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None},
        starter=fake_start,
    )

    payload = client.call("screen.shot", {})

    assert payload["ok"] is True
    assert started == [daemon_home]


def test_daemon_logs_returns_log_dir(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="ready")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", "logs"])
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["data"]["log_dir"] == str(daemon_home / "logs")


def test_daemon_stop_returns_stopped(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_installed_manifest(daemon_home, runtime_state="ready")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", "stop"])
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["data"] == {"stopped": True}


def test_traild_server_accepts_socket_requests(tmp_path: Path, monkeypatch):
    command_service = SimpleNamespace(handle=lambda request: build_success_response(request_id=request.request_id, data={"captured": True}))
    server = TrailDaemonServer(command_service=command_service)

    endpoint = start_server_in_thread(server, daemon_home=tmp_path / "daemon-home", monkeypatch=monkeypatch)
    payload = send_daemon_request(
        DaemonRequest(
            request_id="req-server-1",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="screen.shot",
            payload={},
        ),
        token="token-1",
        endpoint=endpoint,
    )

    assert payload["ok"] is True
```

- [ ] **Step 2: 跑红灯验证 lifecycle 命令与 `traild` 入口缺失**

Run: `pytest tests/test_daemon_bootstrap.py -q`
Expected: FAIL，提示 bootstrap helper、`daemon` 命令组或 `traild` 入口不存在

- [ ] **Step 3: 写最小控制面实现**

`trail/daemon/bootstrap.py`

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
from time import monotonic, sleep

from trail.daemon.models import InstallRecord, RuntimeRecord, TrailDaemonManifest

from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest


def resolve_daemon_home() -> Path:
    return Path.home() / ".trail-daemon"


def install_bootstrap(daemon_home: Path) -> Path:
    daemon_home.mkdir(parents=True, exist_ok=True)
    token_file = daemon_home / "daemon-token.txt"
    token_file.write_text("bootstrap-token", encoding="utf-8")
    manifest = TrailDaemonManifest(
        install=InstallRecord(
            bootstrap_type="scheduled_task",
            bootstrap_id="traild-user",
            daemon_entrypoint="trail.daemon.server:main",
            manifest_version=1,
            protocol_version=1,
            token_file=str(token_file),
            log_dir=str(daemon_home / "logs"),
            workspace_strategy="per-request",
        ),
        runtime=RuntimeRecord(
            instance_id=None,
            pid=None,
            state="installed",
            endpoint=None,
            token_generation=None,
            updated_at=None,
            last_transition_at=None,
            last_start_error=None,
        ),
    )
    path = manifest_path_for_user(daemon_home)
    save_manifest(path, manifest)
    return path


def start_bootstrap(daemon_home: Path) -> bool:
    manifest_path = manifest_path_for_user(daemon_home)
    if not manifest_path.exists():
        return False
    manifest = load_manifest(manifest_path)
    manifest.runtime.state = "starting"
    manifest.runtime.last_transition_at = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest_path, manifest)
    subprocess.Popen(["traild"], cwd=str(Path.cwd()))
    return True


def wait_until_runtime_ready(daemon_home: Path) -> dict:
    manifest_path = manifest_path_for_user(daemon_home)
    deadline = monotonic() + 10
    while monotonic() < deadline:
        manifest = load_manifest(manifest_path)
        if manifest.runtime.state in {"ready", "degraded"}:
            return manifest.runtime.__dict__
        sleep(0.2)
    raise RuntimeError("daemon did not become ready in time")
```

`trail/daemon/server.py`

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
from datetime import datetime, timezone

from trail.core.errors import TrailError
from trail.daemon.models import DaemonRequest
from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest

from trail.daemon.bootstrap import resolve_daemon_home
from trail.daemon.client import daemon_transport_failure
from trail.daemon.command_service import CommandService
from trail.daemon.runtime_service import RuntimeService


class TrailDaemonServer:
    def __init__(self, *, command_service: CommandService):
        self.command_service = command_service

    def serve_forever(self) -> None:
        server = self

        class Handler(StreamRequestHandler):
            def handle(self) -> None:
                payload = json.loads(self.rfile.readline().decode("utf-8"))
                manifest = load_manifest(manifest_path_for_user(resolve_daemon_home()))
                expected_token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
                if payload.get("protocol_version") != manifest.install.protocol_version:
                    response = daemon_transport_failure(request_id=payload.get("request_id", "unknown"), code="DAEMON_VERSION_MISMATCH", message="protocol version mismatch")
                    self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
                    return
                if payload.get("token") != expected_token:
                    response = daemon_transport_failure(request_id=payload.get("request_id", "unknown"), code="DAEMON_AUTH_FAILED", message="daemon token mismatch")
                    self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
                    return
                request = DaemonRequest(
                    request_id=payload["request_id"],
                    protocol_version=payload["protocol_version"],
                    workspace_root=payload["workspace_root"],
                    session_id=payload.get("session_id"),
                    verbose=payload.get("verbose", False),
                    method=payload["method"],
                    payload=payload["payload"],
                )
                try:
                    response = server.command_service.handle(request)
                except TrailError as exc:
                    response = daemon_transport_failure(request_id=request.request_id, code=exc.code, message=str(exc))
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")

        with ThreadingTCPServer(("127.0.0.1", 0), Handler) as listener:
            manifest_path = manifest_path_for_user(resolve_daemon_home())
            manifest = load_manifest(manifest_path)
            host, port = listener.server_address
            manifest.runtime.instance_id = f"traild-{os.getpid()}"
            manifest.runtime.pid = os.getpid()
            manifest.runtime.endpoint = f"{host}:{port}"
            manifest.runtime.state = "ready"
            manifest.runtime.token_generation = 1
            manifest.runtime.updated_at = datetime.now(timezone.utc).isoformat()
            manifest.runtime.last_transition_at = manifest.runtime.updated_at
            save_manifest(manifest_path, manifest)
            listener.serve_forever()

def main() -> None:
    runtime_service = RuntimeService()
    command_service = CommandService(runtime_service=runtime_service)
    TrailDaemonServer(command_service=command_service).serve_forever()
```

`trail/daemon/runtime_service.py`

```python
from __future__ import annotations

from pathlib import Path

from trail.runtime.window import attach_window, launch_game
from trail.runtime.operator import build_runtime


class RuntimeService:
    def __init__(self):
        self._runtimes: dict[tuple[str, str | None], object] = {}

    def get_runtime(self, *, workspace_root: str, window_binding: dict | None):
        key = (workspace_root, None if window_binding is None else str(window_binding))
        if key not in self._runtimes:
            self._runtimes[key] = build_runtime(
                workspace=Path(workspace_root) / ".trail" / "shots",
                window_binding=window_binding,
            )
        return self._runtimes[key]

    def attach_window(self, *, window_title: str):
        return attach_window(window_title)

    def launch_game(self, **payload):
        return launch_game(**payload)
```

`trail/daemon/session_service.py`

Task 4 不提前落 `session_service.py` 的正式实现；这个文件在 Task 6 一次性创建并收口 request journal、`request-status`、`reconcile-session` 与 `request_id` 语义，避免同一文件出现两套实现。

`trail/daemon/command_service.py`

```python
from __future__ import annotations

from trail.core.errors import TrailError
from trail.daemon.client import daemon_transport_failure


def success(
    data: dict,
    *,
    request_id: str | None = None,
    screenshot: str | None = None,
    references: list[dict] | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "ok": True,
        "data": data,
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": references or [],
        "debug": None,
        "error": None,
    }


class CommandService:
    def __init__(self, *, runtime_service, session_service=None, cw_service=None):
        self.runtime_service = runtime_service
        self.session_service = session_service
        self.cw_service = cw_service

    def _runtime(self, request):
        return self.runtime_service.get_runtime(workspace_root=request.workspace_root, window_binding=None)

    def handle(self, request):
        if request.method == "ocr.read":
            runtime = self._runtime(request)
            return {
                "request_id": request.request_id,
                "ok": True,
                "data": {"result": runtime.ocr()},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        raise ValueError(f"unsupported method: {request.method}")
```

`trail/commands/daemon.py`

```python
from __future__ import annotations

from datetime import datetime, timezone
import os
import typer

from trail.commands.helpers import print_json
from trail.commands.helpers import build_default_daemon_client
from trail.daemon.bootstrap import install_bootstrap, resolve_daemon_home, start_bootstrap
from trail.daemon.client import daemon_transport_failure
from trail.daemon.manifest import load_manifest, manifest_path_for_user, save_manifest


def terminate_daemon_process(pid: int) -> None:
    os.kill(pid, 15)

daemon_app = typer.Typer(no_args_is_help=True)
daemon_client_factory = build_default_daemon_client


@daemon_app.command("install")
def daemon_install() -> None:
    path = install_bootstrap(resolve_daemon_home())
    print_json({"ok": True, "data": {"manifest_path": str(path)}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None})


@daemon_app.command("start")
def daemon_start() -> None:
    daemon_home = resolve_daemon_home()
    if not manifest_path_for_user(daemon_home).exists():
        print_json(daemon_transport_failure(request_id="local-start", code="DAEMON_BOOTSTRAP_REQUIRED", message="daemon bootstrap not installed"))
        return
    started = start_bootstrap(daemon_home)
    print_json({"ok": True, "data": {"started": started}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None})


@daemon_app.command("status")
def daemon_status() -> None:
    daemon_home = resolve_daemon_home()
    manifest_path = manifest_path_for_user(daemon_home)
    if not manifest_path.exists():
        print_json(daemon_transport_failure(request_id="local-status", code="DAEMON_BOOTSTRAP_REQUIRED", message="daemon bootstrap not installed"))
        return
    manifest = load_manifest(manifest_path)
    print_json({"ok": True, "data": {"install": manifest.install.__dict__, "runtime": manifest.runtime.__dict__}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None})


@daemon_app.command("stop")
def daemon_stop() -> None:
    daemon_home = resolve_daemon_home()
    manifest_path = manifest_path_for_user(daemon_home)
    manifest = load_manifest(manifest_path)
    if manifest.runtime.pid:
        terminate_daemon_process(manifest.runtime.pid)
    manifest.runtime.state = "stopped"
    manifest.runtime.endpoint = None
    manifest.runtime.updated_at = datetime.now(timezone.utc).isoformat()
    save_manifest(manifest_path, manifest)
    print_json({"ok": True, "data": {"stopped": True}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None})


@daemon_app.command("logs")
def daemon_logs() -> None:
    print_json({"ok": True, "data": {"log_dir": str(resolve_daemon_home() / "logs")}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None})
```

`trail/cli.py`

```python
from trail.commands.daemon import daemon_app
app.add_typer(daemon_app, name="daemon")
```

`pyproject.toml`

```toml
[project.scripts]
trail = "trail.cli:app"
traild = "trail.daemon.server:main"
```

`trail/daemon/client.py`

```python
from trail.daemon.bootstrap import start_bootstrap, wait_until_runtime_ready


class TrailDaemonClient:
    def __init__(self, *, workspace_root: Path, daemon_home: Path, transport, starter=start_bootstrap):
        self.workspace_root = Path(workspace_root)
        self.daemon_home = Path(daemon_home)
        self.transport = transport
        self.starter = starter

    def call(self, method: str, payload: dict, *, session_id: str | None = None, verbose: bool = False) -> dict:
        request_id = uuid4().hex
        manifest_path = manifest_path_for_user(self.daemon_home)
        if not manifest_path.exists():
            return daemon_transport_failure(request_id=request_id, code="DAEMON_BOOTSTRAP_REQUIRED", message="daemon bootstrap not installed")
        manifest = load_manifest(manifest_path)
        if manifest.runtime.state not in {"ready", "degraded"}:
            if not self.starter(self.daemon_home):
                return daemon_transport_failure(request_id=request_id, code="DAEMON_START_FAILED", message="daemon start failed")
            wait_until_runtime_ready(self.daemon_home)
            manifest = load_manifest(manifest_path)
        token = Path(manifest.install.token_file).read_text(encoding="utf-8").strip()
        request = DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(self.workspace_root),
            session_id=session_id,
            verbose=verbose,
            method=method,
            payload=payload,
        )
        response = self.transport(request, token, endpoint=str(manifest.runtime.endpoint))
        returned_request_id = response.pop("request_id", request_id)
        if verbose:
            response["debug"] = {**(response.get("debug") or {}), "request_id": returned_request_id}
        return response
```

- [ ] **Step 4: 跑 lifecycle 测试到绿**

Run: `pytest tests/test_daemon_bootstrap.py tests/test_daemon_protocol.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trail/daemon/bootstrap.py trail/daemon/server.py trail/daemon/runtime_service.py trail/daemon/command_service.py trail/commands/daemon.py trail/cli.py pyproject.toml tests/test_daemon_bootstrap.py tests/test_daemon_protocol.py
git commit -m "feat(daemon): 增加 bootstrap 与 lifecycle 控制面"
```

### Task 5: 迁移基础运行时命令到 RPC 并接入统一 fake daemon 夹具

**Files:**
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/commands/helpers.py`
- Modify: `trail/commands/window.py`
- Modify: `trail/commands/screen.py`
- Modify: `trail/commands/ocr.py`
- Modify: `trail/commands/image.py`
- Modify: `trail/commands/input.py`
- Modify: `trail/output/capture.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_atomic_commands.py`
- Test: `tests/test_runtime_backends.py`

- [ ] **Step 1: 写 `screen/ocr/image/window/input` 全部走 fake daemon client 的失败测试**

```python
def test_screen_shot_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.screen as screen_cmd
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "screen.shot": {
                "ok": True,
                "data": {"captured": True},
                "screenshot": ".trail/shots/req-screen.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["screen", "shot"])
    payload = json.loads(result.stdout)

    assert payload["screenshot"] == ".trail/shots/req-screen.png"
    assert fake_client.calls[0]["method"] == "screen.shot"


def test_ocr_read_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.ocr as ocr_cmd
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "ocr.read": {
                "ok": True,
                "data": {"result": [{"text": "点击进入"}]},
                "screenshot": ".trail/shots/req-ocr.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["ocr", "read"])
    payload = json.loads(result.stdout)

    assert payload["data"]["result"] == [{"text": "点击进入"}]
    assert fake_client.calls[0]["method"] == "ocr.read"


def test_image_wait_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.image as image_cmd
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "image.wait": {
                "ok": True,
                "data": {"box": {"left": 1, "top": 2, "width": 3, "height": 4}},
                "screenshot": ".trail/shots/req-image.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["image", "wait", "demo.png"])
    payload = json.loads(result.stdout)

    assert payload["data"]["box"]["left"] == 1
    assert fake_client.calls[0]["method"] == "image.wait"
```

- [ ] **Step 2: 跑红灯确认这些命令仍在走本地 runtime**

Run: `pytest tests/test_atomic_commands.py::test_screen_shot_uses_daemon_client tests/test_atomic_commands.py::test_ocr_read_uses_daemon_client tests/test_atomic_commands.py::test_image_wait_uses_daemon_client -v`
Expected: FAIL

- [ ] **Step 3: 写 CLI 侧统一 RPC helper 与命令薄壳**

`trail/commands/helpers.py`

```python
from trail.daemon.bootstrap import resolve_daemon_home
from trail.daemon.client import TrailDaemonClient
from trail.output.capture import get_capture_options


def build_default_daemon_client(*, workspace_root: Path | None = None):
    resolved_workspace = Path.cwd() if workspace_root is None else Path(workspace_root)
    return TrailDaemonClient(
        workspace_root=resolved_workspace,
        daemon_home=resolve_daemon_home(),
        transport=send_daemon_request,
    )


def call_daemon(method: str, payload: dict, *, session_id: str | None = None, verbose: bool = False) -> dict:
    client = build_default_daemon_client()
    current_verbose = bool(get_capture_options()["verbose"])
    return client.call(method, payload, session_id=session_id, verbose=(verbose or current_verbose))
```

`trail/output/capture.py`

```python
def get_capture_options() -> dict:
    return dict(_CAPTURE_OPTIONS)
```

`trail/commands/screen.py`

```python
daemon_client_factory = build_default_daemon_client


@screen_app.command("shot")
def screen_shot() -> None:
    print_json(call_daemon("screen.shot", {}))
```

`trail/commands/ocr.py`

```python
daemon_client_factory = build_default_daemon_client


@ocr_app.command("read")
def ocr_read() -> None:
    print_json(call_daemon("ocr.read", {}))
```

`trail/commands/image.py`

```python
daemon_client_factory = build_default_daemon_client


@image_app.command("locate")
def image_locate(template: str) -> None:
    print_json(call_daemon("image.locate", {"template": template}))


@image_app.command("wait")
def image_wait(template: str) -> None:
    print_json(call_daemon("image.wait", {"template": template}))
```

`trail/commands/window.py`

```python
daemon_client_factory = build_default_daemon_client


@window_app.command("attach")
def window_attach(window_title: str = typer.Option("崩坏：星穹铁道", "--window-title")) -> None:
    print_json(call_daemon("window.attach", {"window_title": window_title}))


@window_app.command("launch")
def window_launch(
    game_path: Annotated[Path, typer.Option("--game-path")],
    channel: LaunchChannel = typer.Option(LaunchChannel.OFFICIAL, "--channel"),
    arg: list[str] | None = typer.Option(None, "--arg"),
    use_cmd: bool = typer.Option(False, "--use-cmd"),
) -> None:
    print_json(call_daemon("window.launch", {"game_path": str(game_path), "channel": channel.value, "launch_args": list(arg or []), "use_cmd": use_cmd}))
```

`trail/commands/input.py`

```python
daemon_client_factory = build_default_daemon_client


@input_app.command("click")
def input_click(x: int, y: int) -> None:
    print_json(call_daemon("input.click", {"x": x, "y": y}))


@input_app.command("drag")
def input_drag(from_x: int, from_y: int, to_x: int, to_y: int) -> None:
    print_json(call_daemon("input.drag", {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}))


@input_app.command("key")
def input_key(key: str, presses: int = typer.Option(1, "--presses")) -> None:
    print_json(call_daemon("input.key", {"key": key, "presses": presses}))
```

本任务必须同时把以下 RPC 方法切通并纳入测试：`window.attach`、`window.launch`、`screen.shot`、`ocr.read`、`image.locate`、`image.wait`、`input.click`、`input.drag`、`input.key`。

`trail/daemon/command_service.py`

```python
if request.method == "window.attach":
    return success(self.runtime_service.attach_window(window_title=request.payload["window_title"]), request_id=request.request_id)
if request.method == "window.launch":
    return success(self.runtime_service.launch_game(**request.payload), request_id=request.request_id)
if request.method == "screen.shot":
    screenshot = str(self._runtime(request).capture_after_action(optional=False, request_id=request.request_id))
    return success({"captured": True}, screenshot=screenshot, request_id=request.request_id)
if request.method == "ocr.read":
    return success({"result": self._runtime(request).ocr()}, request_id=request.request_id)
if request.method == "image.locate":
    return success({"box": self._runtime(request).locate(**request.payload)}, request_id=request.request_id)
if request.method == "image.wait":
    return success({"box": self._runtime(request).wait_img(**request.payload)}, request_id=request.request_id)
if request.method == "input.click":
    self._runtime(request).click_point(**request.payload)
    return success({"clicked": [request.payload["x"], request.payload["y"]]}, request_id=request.request_id)
if request.method == "input.drag":
    self._runtime(request).drag_to(**request.payload)
    return success({"dragged": [request.payload["from_x"], request.payload["from_y"], request.payload["to_x"], request.payload["to_y"]]}, request_id=request.request_id)
if request.method == "input.key":
    self._runtime(request).press_key(**request.payload)
    return success({"key": request.payload["key"], "presses": request.payload["presses"]}, request_id=request.request_id)
```

- [ ] **Step 4: 扩充 `tests/conftest.py` 的 fake daemon 注入能力**

```python
@pytest.fixture
def fake_daemon_client():
    return FakeDaemonClient(responses={})


@pytest.fixture
def fake_daemon_server():
    return start_fake_daemon_server({})


@pytest.fixture
def patch_daemon_client_factories(monkeypatch, fake_daemon_client):
    import trail.commands.helpers as helper_mod

    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_daemon_client)
    return fake_daemon_client
```

- [ ] **Step 5: 跑基础运行时命令契约测试到绿**

Run: `pytest tests/test_atomic_commands.py tests/test_runtime_backends.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add trail/daemon/command_service.py trail/commands/helpers.py trail/commands/window.py trail/commands/screen.py trail/commands/ocr.py trail/commands/image.py trail/commands/input.py tests/conftest.py tests/test_atomic_commands.py tests/test_runtime_backends.py
git commit -m "feat(daemon): 让基础运行时命令走 RPC"
```

### Task 6: 实现 request journal、`request-status` 与 `reconcile-session`

**Files:**
- Create: `trail/daemon/session_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/server.py`
- Modify: `trail/commands/daemon.py`
- Test: `tests/test_daemon_session_service.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 写 request journal 与管理命令的失败测试**

```python
def test_request_status_returns_machine_readable_fields(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-2", command_name="cw.stage.detect")

    status = service.request_status("req-2")

    assert status["request_id"] == "req-2"
    assert status["method"] == "cw.stage.detect"
    assert status["session_id"] == session.session_id
    assert status["last_visible_stage"] == "accepted"


def test_reconcile_session_clears_tainted_state(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("daemon", {})["tainted"] = True
    service.save_session(loaded)

    result = service.reconcile_session(session.session_id)

    assert result == {"session_id": session.session_id, "tainted": False}
    assert service.load_session(session.session_id).scene_state["daemon"]["tainted"] is False


def test_duplicate_request_id_returns_duplicate_terminal(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-3", command_name="input.click")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-3",
        command_name="input.click",
        final_state="completed",
        envelope={"ok": True, "data": {"clicked": [10, 20]}, "screenshot": ".trail/shots/req-3.png", "timing": {}, "warnings": [], "references": [], "debug": None, "error": None},
    )

    result = service.begin_mutation(session_id=session.session_id, request_id="req-3", command_name="input.click")

    assert result["status"] == "duplicate_terminal"
    assert result["record"]["last_envelope"]["data"]["clicked"] == [10, 20]


def test_duplicate_request_id_returns_duplicate_in_progress(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-3b", command_name="input.click")

    result = service.begin_mutation(session_id=session.session_id, request_id="req-3b", command_name="input.click")

    assert result["status"] == "duplicate_in_progress"


def test_duplicate_request_id_is_workspace_global(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    first = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    second = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 2})
    service.begin_mutation(session_id=first.session_id, request_id="req-3c", command_name="cw.enter")

    result = service.begin_mutation(session_id=second.session_id, request_id="req-3c", command_name="cw.shop.buy-slot")

    assert result["status"] == "request_id_conflict"


def test_finish_mutation_marks_tainted_for_unknown_result(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-4", command_name="cw.stage.detect")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-4",
        command_name="cw.stage.detect",
        final_state="persisted_but_response_unknown",
        envelope={"ok": False, "data": {}, "screenshot": ".trail/shots/req-4.png", "timing": {}, "warnings": [], "references": [], "debug": None, "error": {"code": "DAEMON_UNAVAILABLE", "message": "lost response"}},
    )

    assert service.load_session(session.session_id).scene_state["daemon"]["tainted"] is True


def test_run_mutation_records_stage_progression(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-5", command_name="input.click")
    service.mark_executing(request_id="req-5", session_id=session.session_id, command_name="input.click")
    service.mark_side_effect_applied(request_id="req-5", session_id=session.session_id, command_name="input.click")
    service.mark_state_persisted(request_id="req-5", session_id=session.session_id, command_name="input.click")

    assert service.request_status("req-5")["last_visible_stage"] == "state_persisted"


def test_run_mutation_marks_unknown_result_terminal_states(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    command_service.session_service._services[str(tmp_path)] = service

    with pytest.raises(PersistedButResponseUnknown):
        command_service._run_mutation(
            SimpleNamespace(session_id=session.session_id, request_id="req-6", workspace_root=str(tmp_path)),
            "input.click",
            lambda svc: (_ for _ in ()).throw(PersistedButResponseUnknown(envelope={"ok": True, "data": {}, "screenshot": ".trail/shots/req-6.png", "timing": {}, "warnings": [], "references": [], "debug": None, "error": None})),
        )

    assert service.request_status("req-6")["final_state"] == "persisted_but_response_unknown"


def test_run_mutation_marks_failed_before_side_effect(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})

    command_service = CommandService(runtime_service=SimpleNamespace(), session_service=SessionServiceRegistry())
    command_service.session_service._services[str(tmp_path)] = service

    with pytest.raises(RuntimeError):
        command_service._run_mutation(
            SimpleNamespace(session_id=session.session_id, request_id="req-6b", workspace_root=str(tmp_path)),
            "input.click",
            lambda svc: (_ for _ in ()).throw(RuntimeError("boom")),
        )

    assert service.request_status("req-6b")["final_state"] == "failed_before_side_effect"
    assert service.load_session(session.session_id).last_screenshot is None


def test_request_status_survives_service_restart(tmp_path: Path):
    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service.begin_mutation(session_id=session.session_id, request_id="req-7", command_name="cw.shop.buy-slot")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-7",
        command_name="cw.shop.buy-slot",
        final_state="completed",
        envelope={"ok": True, "data": {"slot": 2}, "screenshot": ".trail/shots/req-7.png", "timing": {}, "warnings": [], "references": [], "debug": None, "error": None},
    )

    reloaded = SessionService(workspace_root=tmp_path)

    assert reloaded.request_status("req-7")["final_state"] == "completed"
```

- [ ] **Step 2: 跑红灯验证管理恢复链路尚不存在**

Run: `pytest tests/test_daemon_session_service.py::test_request_status_returns_machine_readable_fields tests/test_daemon_session_service.py::test_reconcile_session_clears_tainted_state tests/test_daemon_session_service.py::test_duplicate_request_id_returns_duplicate_terminal tests/test_daemon_session_service.py::test_duplicate_request_id_returns_duplicate_in_progress tests/test_daemon_session_service.py::test_duplicate_request_id_is_workspace_global tests/test_daemon_session_service.py::test_finish_mutation_marks_tainted_for_unknown_result tests/test_daemon_session_service.py::test_run_mutation_records_stage_progression tests/test_daemon_session_service.py::test_run_mutation_marks_unknown_result_terminal_states tests/test_daemon_session_service.py::test_run_mutation_marks_failed_before_side_effect tests/test_daemon_session_service.py::test_request_status_survives_service_restart -v`
Expected: FAIL

- [ ] **Step 3: 写最小 SessionService 与 daemon 命令面实现**

```python
import json
from pathlib import Path
from threading import Lock

from trail.core.errors import TrailError
from trail.session.store import SessionStore


class SessionService:
    def __init__(self, *, workspace_root: Path):
        self.workspace_root = Path(workspace_root)
        self._store = SessionStore(self.workspace_root / ".trail" / "sessions")
        self._journal_root = self.workspace_root / ".trail" / "requests"
        self._journal_root.mkdir(parents=True, exist_ok=True)
        self._mutex = Lock()

    def _journal_path(self, request_id: str) -> Path:
        return self._journal_root / f"{request_id}.json"

    def _load_record(self, request_id: str) -> dict | None:
        path = self._journal_path(request_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_record(self, record: dict) -> None:
        self._journal_path(record["request_id"]).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    def create_session(self, *, window_binding: dict):
        return self._store.create(window_binding=window_binding)

    def load_session(self, session_id: str):
        return self._store.load(session_id)

    def save_session(self, session) -> None:
        self._store.save(session)

    def dump_state(self, *, session_id: str) -> dict:
        return self.load_session(session_id).to_dict()

    def begin_mutation(self, *, session_id: str, request_id: str, command_name: str) -> dict:
        # 第一版把 request_id 视为 workspace 内全局唯一的 journal lookup key；跨 session/command 复用视为冲突。
        with self._mutex:
            record = self._load_record(request_id)
            if record is not None and record.get("final_state"):
                return {"status": "duplicate_terminal", "record": record}
            if record is not None:
                if record["session_id"] != session_id or record["command_name"] != command_name:
                    return {"status": "request_id_conflict", "record": record}
                return {"status": "duplicate_in_progress", "record": record}
            fresh = {
                "request_id": request_id,
                "command_name": command_name,
                "workspace_root": str(self.workspace_root),
                "session_id": session_id,
                "stage": "accepted",
                "final_state": None,
            }
            self._save_record(fresh)
            return {"status": "accepted", "record": fresh}

    def mark_executing(self, *, request_id: str, session_id: str, command_name: str) -> None:
        record = self._load_record(request_id)
        record["stage"] = "executing"
        self._save_record(record)

    def mark_side_effect_applied(self, *, request_id: str, session_id: str, command_name: str) -> None:
        record = self._load_record(request_id)
        record["stage"] = "side_effect_applied"
        self._save_record(record)

    def mark_state_persisted(self, *, request_id: str, session_id: str, command_name: str) -> None:
        record = self._load_record(request_id)
        record["stage"] = "state_persisted"
        self._save_record(record)

    def finish_mutation(self, *, session_id: str, request_id: str, command_name: str, final_state: str, envelope: dict) -> None:
        session = self.load_session(session_id)
        record = self._load_record(request_id)
        record["final_state"] = final_state
        record["stage"] = "responded"
        record["last_envelope"] = envelope
        if final_state in {"applied_but_not_persisted", "persisted_but_response_unknown"}:
            session.scene_state.setdefault("daemon", {})["tainted"] = True
        session.last_result = {
            "command": record["command_name"],
            "ok": envelope["ok"],
            "data": envelope["data"],
            "error": envelope["error"],
        }
        if envelope["screenshot"] is not None:
            session.last_screenshot = envelope["screenshot"]
        self.save_session(session)
        self._save_record(record)

    def request_status(self, request_id: str) -> dict:
        record = self._load_record(request_id)
        session = self.load_session(record["session_id"])
        return {
            "request_id": request_id,
            "method": record["command_name"],
            "workspace_root": record["workspace_root"],
            "session_id": record["session_id"],
            "final_state": record["final_state"],
            "last_visible_stage": record["stage"],
            "tainted": bool(session.scene_state.get("daemon", {}).get("tainted", False)),
            "started_at": record.setdefault("started_at", "2026-04-15T00:00:00+00:00"),
            "updated_at": record.setdefault("updated_at", "2026-04-15T00:00:00+00:00"),
        }

    def reconcile_session(self, session_id: str) -> dict:
        session = self.load_session(session_id)
        session.scene_state.setdefault("daemon", {})["tainted"] = False
        self.save_session(session)
        return {"session_id": session_id, "tainted": False}


class SessionServiceRegistry:
    def __init__(self):
        self._services: dict[str, SessionService] = {}

    def for_workspace(self, workspace_root: str) -> SessionService:
        if workspace_root not in self._services:
            self._services[workspace_root] = SessionService(workspace_root=Path(workspace_root))
        return self._services[workspace_root]
```

```python
@daemon_app.command("request-status")
def daemon_request_status(request_id: Annotated[str, typer.Option("--request-id")]) -> None:
    client = daemon_client_factory()
    print_json(client.call("daemon.request_status", {"request_id": request_id}))


@daemon_app.command("reconcile-session")
def daemon_reconcile_session(session: Annotated[str, typer.Option("--session")]) -> None:
    client = daemon_client_factory()
    print_json(client.call("daemon.reconcile_session", {"session_id": session}))
```

- [ ] **Step 4: 在 daemon `CommandService` 中注册 `daemon.request_status` 与 `daemon.reconcile_session`**

```python
class SideEffectAppliedButStateNotPersisted(Exception):
    def __init__(self, envelope: dict):
        self.envelope = envelope


class PersistedButResponseUnknown(Exception):
    def __init__(self, envelope: dict):
        self.envelope = envelope


def _run_mutation(self, request, command_name: str, handler):
    service = self.session_service.for_workspace(request.workspace_root)
    accepted = service.begin_mutation(session_id=request.session_id, request_id=request.request_id, command_name=command_name)
    if accepted["status"] == "duplicate_terminal":
        return success(accepted["record"], request_id=request.request_id)
    if accepted["status"] == "duplicate_in_progress":
        return daemon_transport_failure(request_id=request.request_id, code="REQUEST_IN_PROGRESS", message="matching request is still executing", debug={"record": accepted["record"]})
    if accepted["status"] == "request_id_conflict":
        raise TrailError("REQUEST_ID_CONFLICT", "request_id reused across a different session or command")
    service.mark_executing(request_id=request.request_id, session_id=request.session_id, command_name=command_name)
    try:
        result = handler(service)
        service.mark_side_effect_applied(request_id=request.request_id, session_id=request.session_id, command_name=command_name)
        service.mark_state_persisted(request_id=request.request_id, session_id=request.session_id, command_name=command_name)
        service.finish_mutation(session_id=request.session_id, request_id=request.request_id, command_name=command_name, final_state="completed", envelope=result)
        return result
    except SideEffectAppliedButStateNotPersisted as exc:
        service.mark_side_effect_applied(request_id=request.request_id, session_id=request.session_id, command_name=command_name)
        service.finish_mutation(session_id=request.session_id, request_id=request.request_id, command_name=command_name, final_state="applied_but_not_persisted", envelope=exc.envelope)
        raise
    except PersistedButResponseUnknown as exc:
        service.mark_side_effect_applied(request_id=request.request_id, session_id=request.session_id, command_name=command_name)
        service.mark_state_persisted(request_id=request.request_id, session_id=request.session_id, command_name=command_name)
        service.finish_mutation(session_id=request.session_id, request_id=request.request_id, command_name=command_name, final_state="persisted_but_response_unknown", envelope=exc.envelope)
        raise
    except Exception as exc:
        service.finish_mutation(
            session_id=request.session_id,
            request_id=request.request_id,
            command_name=command_name,
            final_state="failed_before_side_effect",
            envelope={"ok": False, "data": {}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": {"code": type(exc).__name__, "message": str(exc)}},
        )
        raise


if request.method == "daemon.request_status":
    service = self.session_service.for_workspace(request.workspace_root)
    return success(service.request_status(request.payload["request_id"]), request_id=request.request_id)
if request.method == "daemon.reconcile_session":
    service = self.session_service.for_workspace(request.workspace_root)
    return success(service.reconcile_session(request.payload["session_id"]), request_id=request.request_id)
```

`trail/daemon/server.py`

```python
def main() -> None:
    runtime_service = RuntimeService()
    session_service = SessionServiceRegistry()
    command_service = CommandService(runtime_service=runtime_service, session_service=session_service)
    TrailDaemonServer(command_service=command_service).serve_forever()
```

同一步里，把 `Task 5` 里已经接到 daemon 的 mutating handler 统一改成走 `_run_mutation()`：

- `input.click`
- `input.drag`
- `input.key`

- [ ] **Step 5: 跑 journal 与 daemon 管理命令测试到绿**

Run: `pytest tests/test_daemon_session_service.py tests/test_daemon_protocol.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add trail/daemon/session_service.py trail/daemon/command_service.py trail/daemon/server.py trail/commands/daemon.py tests/test_daemon_session_service.py tests/test_daemon_protocol.py
git commit -m "feat(daemon): 增加 request journal 与恢复命令"
```

### Task 7: 落地 workspace 相对路径与按 `request_id` 命名的截图产物

**Files:**
- Modify: `trail/session/models.py`
- Modify: `trail/session/store.py`
- Modify: `trail/artifacts/store.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/runtime/window.py`
- Modify: `trail/runtime/operator.py`
- Test: `tests/test_output_envelope.py`
- Test: `tests/test_runtime_backends.py`

- [ ] **Step 1: 写路径相对化与唯一截图命名的失败测试**

```python
def test_capture_to_workspace_uses_request_id_filename(tmp_path: Path):
    controller = WindowsWindowController(workspace=tmp_path, window_binding={"title": "Demo"})
    controller.capture = lambda **kwargs: b"demo-bytes"

    path = controller.capture_to_workspace(request_id="req-123")

    assert path.name == "req-123.png"


def test_session_store_persists_relative_last_screenshot(tmp_path: Path):
    session = SessionModel(
        session_id="a" * 32,
        workspace=tmp_path / ".trail" / "sessions",
        window_binding={"title": "崩坏：星穹铁道", "hwnd": 1},
        created_at="2026-04-15T00:00:00+00:00",
    )
    session.last_screenshot = ".trail/shots/req-123.png"
    SessionStore(tmp_path / ".trail" / "sessions").save(session)

    payload = json.loads((tmp_path / ".trail" / "sessions" / f"{session.session_id}.json").read_text(encoding="utf-8"))
    assert payload["last_screenshot"] == ".trail/shots/req-123.png"


def test_artifact_store_persists_relative_artifact_path(tmp_path: Path):
    store = ArtifactStore(tmp_path / ".trail" / "artifacts")

    artifact = store.create(scene="cw", kind="guide", payload={"path": ".trail/artifacts/guide.json"})

    payload = json.loads(artifact.path.read_text(encoding="utf-8"))
    assert payload["path"] == ".trail/artifacts/guide.json"


def test_references_bind_current_request_screenshot(tmp_path: Path):
    screenshot = tmp_path / ".trail" / "shots" / "req-123.png"
    payload = {
        "screenshot": str(screenshot),
        "references": [{"path": "trail/scenes/cw/references/1.png", "screenshot": str(screenshot)}],
    }

    assert payload["references"][0]["screenshot"] == payload["screenshot"]
```

- [ ] **Step 2: 跑红灯验证截图与路径协议仍未收口**

Run: `pytest tests/test_runtime_backends.py::test_capture_to_workspace_uses_request_id_filename tests/test_output_envelope.py::test_session_store_persists_relative_last_screenshot tests/test_output_envelope.py::test_artifact_store_persists_relative_artifact_path tests/test_output_envelope.py::test_references_bind_current_request_screenshot -v`
Expected: FAIL

- [ ] **Step 3: 实现相对路径与唯一截图命名**

```python
def to_workspace_relative(path: Path, workspace_root: Path) -> str:
    try:
        return str(Path(path).relative_to(workspace_root))
    except ValueError:
        return str(path)


def capture_to_workspace(self, request_id: str) -> Path:
    path = self.workspace / f"{request_id}.png"
    path.write_bytes(self.capture())
    return path


def create(self, *, scene: str, kind: str, payload: dict) -> ArtifactMeta:
    artifact_id = uuid4().hex
    path = self._path_for(artifact_id)
    data = deepcopy(payload)
    data["path"] = to_workspace_relative(Path(data["path"]), self.workspace.parent.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return ArtifactMeta(artifact_id=artifact_id, scene=scene, kind=kind, path=path)


def bind_references_to_screenshot(payload: dict) -> dict:
    if payload.get("screenshot"):
        for item in payload.get("references", []):
            item["screenshot"] = payload["screenshot"]
    return payload


def success(data: dict, *, request_id: str | None = None, screenshot: str | None = None, references: list[dict] | None = None) -> dict:
    payload = {
        "request_id": request_id,
        "ok": True,
        "data": data,
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": references or [],
        "debug": None,
        "error": None,
    }
    return bind_references_to_screenshot(payload)


class RuntimeOperator:
    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        try:
            path = self.window.capture_to_workspace(request_id=request_id or "last-action")
            self._record_trace("capture_after_action", optional=optional, screenshot=str(path))
            return path
        except Exception:
            if optional:
                self._record_trace("capture_after_action", optional=optional, screenshot=None)
                return None
            raise
```

本任务必须同步完成：

- `session`、`artifact`、`last_screenshot` 默认保存相对 `workspace_root` 的路径；
- `references` 与当前 envelope 的 `screenshot` 绑定；
- 停止把 `last-action.png` 当成唯一截图真相源。

- [ ] **Step 4: 跑路径与截图协议测试到绿**

Run: `pytest tests/test_output_envelope.py tests/test_runtime_backends.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trail/session/models.py trail/session/store.py trail/artifacts/store.py trail/daemon/command_service.py trail/runtime/window.py trail/runtime/operator.py tests/test_output_envelope.py tests/test_runtime_backends.py
git commit -m "fix(daemon): 收紧路径与截图产物契约"
```

### Task 8: 迁移 `guide fetch|config|list` 到 daemon RPC

**Files:**
- Modify: `trail/commands/guide.py`
- Modify: `trail/daemon/command_service.py`
- Create: `tests/test_guide_rpc_contracts.py`
- Test: `tests/test_atomic_commands.py`

- [ ] **Step 1: 写 `guide fetch|config|list` 走 daemon 的失败测试**

```python
def test_guide_fetch_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.guide as guide_cmd
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "guide.fetch.cw": {
                "ok": True,
                "data": {"lineup_id": "abc"},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["guide", "fetch", "cw", "abc"])
    payload = json.loads(result.stdout)

    assert payload["data"]["lineup_id"] == "abc"
    assert fake_client.calls[0]["method"] == "guide.fetch.cw"


def test_guide_config_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "guide.config.cw": {
                "ok": True,
                "data": {"season_id": 12},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["guide", "config", "cw"])
    payload = json.loads(result.stdout)

    assert payload["data"]["season_id"] == 12
    assert fake_client.calls[0]["method"] == "guide.config.cw"


def test_guide_list_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "guide.list.cw": {
                "ok": True,
                "data": {"items": [{"lineup_id": "abc"}]},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["guide", "list", "cw", "--page", "2", "--limit", "10", "--trait-id", "1005"])
    payload = json.loads(result.stdout)

    assert payload["data"]["items"][0]["lineup_id"] == "abc"
    assert fake_client.calls[0]["method"] == "guide.list.cw"
    assert fake_client.calls[0]["payload"]["page"] == 2
```

- [ ] **Step 2: 跑红灯确认 `guide` 仍是本地路径**

Run: `pytest tests/test_guide_rpc_contracts.py -v`
Expected: FAIL

- [ ] **Step 3: 改 `guide.py` 为 RPC 薄壳，并在 daemon 命令服务中注册 `guide.fetch.cw` / `guide.config.cw` / `guide.list.cw`**

```python
@guide_app.command("fetch")
def guide_fetch(scene: str, url: str) -> None:
    print_json(call_daemon(f"guide.fetch.{scene}", {"url": url}))


@guide_app.command("config")
def guide_config_cw(scene: str) -> None:
    print_json(call_daemon(f"guide.config.{scene}", {}))


@guide_app.command("list")
def guide_list_cw(
    scene: str,
    page: int = typer.Option(1, "--page"),
    limit: int = typer.Option(20, "--limit"),
    trait_id: int | None = typer.Option(None, "--trait-id"),
    order: str | None = typer.Option(None, "--order"),
    next_page_token: str | None = typer.Option(None, "--next-page-token"),
    match_change_job: str | None = typer.Option(None, "--match-change-job"),
    match_hard: str | None = typer.Option(None, "--match-hard"),
) -> None:
    print_json(
        call_daemon(
            f"guide.list.{scene}",
            {
                "page": page,
                "limit": limit,
                "trait_id": trait_id,
                "order": order,
                "next_page_token": next_page_token,
                "match_change_job": match_change_job,
                "match_hard": match_hard,
            },
        )
    )
```

- [ ] **Step 4: 跑 `guide` CLI RPC 契约测试到绿**

Run: `pytest tests/test_guide_rpc_contracts.py tests/test_atomic_commands.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trail/commands/guide.py trail/daemon/command_service.py tests/test_guide_rpc_contracts.py tests/test_atomic_commands.py
git commit -m "feat(daemon): 迁移 guide 命令到 RPC"
```

### Task 9: 为 `session/state` cutover 准备 daemon 侧适配器

**Execution note:** Task 9 和 Task 10 必须作为同一批次连续执行；Task 9 完成后不要单独停下做 review 或合并，因为 spec 明确禁止 `session/state` 已经切到 daemon 而 `cw` 仍走本地真相源的中间态。

**Files:**
- Create: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/server.py`
- Test: `tests/test_daemon_protocol.py`
- Test: `tests/test_daemon_session_service.py`

- [ ] **Step 1: 写 daemon 侧 `session.create` 与 `state.dump` 处理器失败测试**

```python
def test_command_service_handles_session_create(tmp_path: Path):
    service = SessionServiceRegistry()
    runtime_service = SimpleNamespace(attach_window=lambda window_title: {"title": window_title, "hwnd": 1})
    command_service = CommandService(runtime_service=runtime_service, session_service=service)
    request = DaemonRequest(
        request_id="req-1",
        protocol_version=1,
        workspace_root=str(tmp_path),
        session_id=None,
        verbose=False,
        method="session.create",
        payload={"window_title": "崩坏：星穹铁道"},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["session_id"]


def test_command_service_handles_state_dump(tmp_path: Path):
    service = SessionServiceRegistry()
    session = service.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(attach_window=lambda window_title: {"title": window_title, "hwnd": 1})
    command_service = CommandService(runtime_service=runtime_service, session_service=service)
    request = DaemonRequest(
        request_id="req-2",
        protocol_version=1,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="state.dump",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert payload["data"]["session_id"] == session.session_id
```

- [ ] **Step 2: 跑红灯确认 daemon 侧适配器仍未接入**

Run: `pytest tests/test_daemon_protocol.py tests/test_daemon_session_service.py -q`
Expected: 至少新增适配器测试 FAIL

- [ ] **Step 3: 在 daemon 命令服务中补齐 `session.create` 与 `state.dump` 方法映射**

- `session.create`
- `state.dump`

- [ ] **Step 4: 在 daemon 侧补齐 `session.create` 与 `state.dump` 处理器，但先不切 CLI 命令入口**

`trail/daemon/command_service.py`

```python
if request.method == "session.create":
    binding = self.runtime_service.attach_window(window_title=request.payload["window_title"])
    service = self.session_service.for_workspace(request.workspace_root)
    session = service.create_session(window_binding=binding)
    return success(session.to_dict(), request_id=request.request_id)

if request.method == "state.dump":
    service = self.session_service.for_workspace(request.workspace_root)
    return success(service.dump_state(session_id=request.payload["session_id"]), request_id=request.request_id)
```


- [ ] **Step 5: 跑 daemon 侧 `session/state` 适配器测试到绿**

Run: `pytest tests/test_daemon_protocol.py tests/test_daemon_session_service.py -q`
Expected: PASS

- [ ] **Step 6: 不提交，直接进入 Task 10**

Task 9 只建立 daemon 侧能力，不单独提交；真正的 cutover commit 统一在 Task 10 完成后创建。

### Task 10: 在 daemon 侧补齐全部 `cw` 方法映射

**Files:**
- Create: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/daemon/server.py`
- Test: `tests/test_daemon_protocol.py`
- Modify: `tests/test_cw_entry.py`
- Modify: `tests/test_cw_stage.py`
- Modify: `tests/test_cw_slots.py`
- Modify: `tests/test_cw_shop.py`
- Modify: `tests/test_cw_events.py`
- Modify: `tests/test_cw_guide.py`

- [ ] **Step 1: 把现有 `tests/test_cw_*` 迁到 daemon 侧断言，并补 daemon 侧 `cw` 处理器失败测试**

```python
def test_command_service_handles_cw_stage_detect(tmp_path: Path, monkeypatch):
    service = SessionServiceRegistry()
    session = service.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.detect_cw_stage",
        lambda session, detector: _set_stage(session, {"value": "preparation", "stale": False}),
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=service, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-1",
        protocol_version=1,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.stage.detect",
        payload={"session_id": session.session_id},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert service.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["stage"]["value"] == "preparation"


def test_command_service_routes_cw_shop_buy_slot_through_mutation_journal(tmp_path: Path, monkeypatch):
    service = SessionServiceRegistry()
    session = service.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.shop_buyer_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.buy_cw_shop_slot",
        lambda session, slot, expect, buyer, scanner: _set_shop(session, {"slot": slot, "expect": expect}),
    )
    command_service = CommandService(runtime_service=runtime_service, session_service=service, cw_service=cw_service)
    request = DaemonRequest(
        request_id="req-cw-2",
        protocol_version=1,
        workspace_root=str(tmp_path),
        session_id=session.session_id,
        verbose=False,
        method="cw.shop.buy_slot",
        payload={"session_id": session.session_id, "slot": 2, "expect": "希儿"},
    )

    payload = command_service.handle(request)

    assert payload["ok"] is True
    assert service.for_workspace(str(tmp_path)).request_status("req-cw-2")["final_state"] == "completed"
    assert service.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["shop"]["slot"] == 2


def test_cw_entry_service_updates_entry_and_marks_stage_stale(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(
        "trail.daemon.cw_service.enter_cw",
        lambda session, mode, difficulty, battle_mode, runtime: _set_entry(session, {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode}),
    )

    payload = cw_service.handle(
        method="cw.enter",
        payload={"session_id": session.session_id, "mode": "new", "difficulty": "current", "battle_mode": "standard"},
        workspace_root=str(tmp_path),
        session_service=registry.for_workspace(str(tmp_path)),
    )

    assert payload["mode"] == "new"
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["entry"]["mode"] == "new"
```

- [ ] **Step 2: 跑红灯确认 daemon 侧 `cw` handler 仍未接齐**

Run: `pytest tests/test_daemon_protocol.py tests/test_cw_entry.py tests/test_cw_stage.py tests/test_cw_slots.py tests/test_cw_shop.py tests/test_cw_events.py tests/test_cw_guide.py -q`
Expected: 至少新增 daemon-side `cw` 测试 FAIL

- [ ] **Step 3: 在 daemon 命令服务中补齐全部 `cw` 方法映射**

RPC 方法名在 daemon 内统一使用 `snake_case`，用于承接现有 CLI 中带连字符的命令；例如：

- `cw slots place-one` -> `cw.slots.place_one`
- `cw shop buy-slot` -> `cw.shop.buy_slot`
- `cw hand sell-one` -> `cw.hand.sell_one`

必须覆盖的 RPC 方法清单：

- `cw.enter`
- `cw.stage.detect`
- `cw.stage.wait`
- `cw.guide.apply`
- `cw.guide.current`
- `cw.slots.read`
- `cw.slots.swap`
- `cw.slots.place_one`
- `cw.shop.open`
- `cw.shop.scan`
- `cw.shop.buy_slot`
- `cw.shop.refresh`
- `cw.shop.close`
- `cw.shop.status`
- `cw.crystals.collect`
- `cw.hand.sell_one`
- `cw.hand.sell_plan`
- `cw.replenish.read`
- `cw.replenish.choose`
- `cw.invest.read`
- `cw.invest.choose`
- `cw.encounter.read`
- `cw.encounter.choose`
- `cw.fortune.read`
- `cw.fortune.choose`
- `cw.boss_preview.confirm`
- `cw.battle.start`
- `cw.battle.continue`
- `cw.settle.next`
- `cw.event.handle`

其中所有 mutating `cw` 方法必须统一走 `_run_mutation()`，至少包括：

- `cw.enter`
- `cw.guide.apply`
- `cw.slots.swap`
- `cw.slots.place_one`
- `cw.shop.open`
- `cw.shop.buy_slot`
- `cw.shop.refresh`
- `cw.shop.close`
- `cw.crystals.collect`
- `cw.hand.sell_one`
- `cw.hand.sell_plan`
- `cw.replenish.choose`
- `cw.invest.choose`
- `cw.encounter.choose`
- `cw.fortune.choose`
- `cw.boss_preview.confirm`
- `cw.battle.start`
- `cw.battle.continue`
- `cw.settle.next`
- `cw.event.handle`

`trail/daemon/command_service.py`

```python
from trail.daemon.cw_service import CwService


CW_MUTATING_METHODS = {
    "cw.enter",
    "cw.guide.apply",
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


def _run_cw(self, request):
    service = self.session_service.for_workspace(request.workspace_root)
    return self.cw_service.handle(method=request.method, payload=request.payload, workspace_root=request.workspace_root, session_service=service)


if request.method.startswith("cw."):
    if request.method in CW_MUTATING_METHODS:
        return self._run_mutation(request, request.method, lambda service: success(self._run_cw(request), request_id=request.request_id))
    return success(self._run_cw(request), request_id=request.request_id)
```

`trail/daemon/cw_service.py`

```python
from pathlib import Path

from trail.artifacts.store import ArtifactStore
from trail.scenes.cw.entry import enter_cw
from trail.scenes.cw.events import (
    build_cw_battle_continuer,
    build_cw_battle_starter,
    build_cw_boss_preview_confirmer,
    build_cw_encounter_chooser,
    build_cw_event_handler,
    build_cw_fortune_chooser,
    build_cw_invest_chooser,
    build_cw_replenish_chooser,
    build_cw_settle_continuer,
    choose_cw_encounter,
    choose_cw_fortune,
    choose_cw_invest,
    choose_cw_replenish,
    confirm_cw_boss_preview,
    continue_cw_battle,
    handle_cw_event,
    read_cw_encounter,
    read_cw_fortune,
    read_cw_invest,
    read_cw_replenish,
    settle_cw_next,
    start_cw_battle,
)
from trail.scenes.cw.guide import apply_cw_guide, apply_cw_guide_via_ui, fetch_cw_guide, fetch_cw_guide_payload
from trail.scenes.cw.shop import (
    build_cw_shop_buyer,
    build_cw_shop_closer,
    build_cw_shop_opener,
    build_cw_shop_refresher,
    build_cw_shop_scanner,
    buy_cw_shop_slot,
    close_cw_shop,
    open_cw_shop,
    refresh_cw_shop,
    scan_cw_shop,
    shop_cw_status,
)
from trail.scenes.cw.slots import (
    build_cw_crystal_collector,
    build_cw_hand_seller,
    build_cw_slot_swapper,
    build_cw_slots_reader,
    collect_cw_crystals,
    place_one_cw_slot,
    plan_cw_hand_sell,
    read_cw_slots,
    sell_one_cw_hand,
    swap_cw_slots,
)
from trail.scenes.cw.stage import build_cw_stage_detector, detect_cw_stage, wait_cw_stage

stage_detector_factory = build_cw_stage_detector
slots_reader_factory = build_cw_slots_reader
slot_swapper_factory = build_cw_slot_swapper
slot_placer_factory = build_cw_slot_swapper
hand_seller_factory = build_cw_hand_seller
crystal_collector_factory = build_cw_crystal_collector
shop_scanner_factory = build_cw_shop_scanner
shop_buyer_factory = build_cw_shop_buyer
shop_opener_factory = build_cw_shop_opener
shop_refresher_factory = build_cw_shop_refresher
shop_closer_factory = build_cw_shop_closer
replenish_chooser_factory = build_cw_replenish_chooser
invest_chooser_factory = build_cw_invest_chooser
encounter_chooser_factory = build_cw_encounter_chooser
fortune_chooser_factory = build_cw_fortune_chooser
event_handler_factory = build_cw_event_handler
boss_preview_confirmer_factory = build_cw_boss_preview_confirmer
battle_starter_factory = build_cw_battle_starter
battle_continuer_factory = build_cw_battle_continuer
settle_continuer_factory = build_cw_settle_continuer


class CwService:
    def __init__(self, *, runtime_service):
        self.runtime_service = runtime_service

    def handle(self, *, method: str, payload: dict, workspace_root: str, session_service) -> dict:
        session = session_service.load_session(payload["session_id"])
        runtime = self.runtime_service.get_runtime(workspace_root=workspace_root, window_binding=session.window_binding)
        artifact_store = ArtifactStore(Path(workspace_root) / ".trail" / "artifacts")
        readers = {
            "cw.stage.detect": lambda: detect_cw_stage(session, detector=stage_detector_factory(runtime)).scene_state["cw"]["stage"],
            "cw.stage.wait": lambda: wait_cw_stage(session, detector=stage_detector_factory(runtime), timeout=payload.get("timeout", 120)).scene_state["cw"]["stage"],
            "cw.guide.current": lambda: session.scene_state.get("cw", {}).get("guide"),
            "cw.slots.read": lambda: read_cw_slots(session, reader=slots_reader_factory(runtime, targets=payload.get("slot")), targets=payload.get("slot")).scene_state["cw"]["slots"],
            "cw.shop.open": lambda: open_cw_shop(session, opener=shop_opener_factory(runtime)).scene_state["cw"]["shop"],
            "cw.shop.scan": lambda: scan_cw_shop(session, scanner=shop_scanner_factory(runtime)).scene_state["cw"]["shop"],
            "cw.shop.refresh": lambda: refresh_cw_shop(session, refresher=shop_refresher_factory(runtime)).scene_state["cw"]["shop"],
            "cw.shop.close": lambda: close_cw_shop(session, closer=shop_closer_factory(runtime)).scene_state["cw"]["shop"],
            "cw.shop.status": lambda: shop_cw_status(session),
            "cw.replenish.read": lambda: read_cw_replenish(session),
            "cw.invest.read": lambda: read_cw_invest(session),
            "cw.encounter.read": lambda: read_cw_encounter(session),
            "cw.fortune.read": lambda: read_cw_fortune(session),
        }
        writers = {
            "cw.enter": lambda: enter_cw(session, mode=payload["mode"], difficulty=payload.get("difficulty"), battle_mode=payload.get("battle_mode"), runtime=runtime).scene_state["cw"]["entry"],
            "cw.guide.apply": lambda: _apply_guide(session, runtime, artifact_store, payload["lineup_id"]),
            "cw.slots.swap": lambda: swap_cw_slots(session, source=payload["source"], target=payload["target"], swapper=slot_swapper_factory(runtime)).scene_state["cw"]["slots"],
            "cw.slots.place_one": lambda: place_one_cw_slot(session, source=payload["source"], target=payload["target"], placer=slot_placer_factory(runtime)).scene_state["cw"]["slots"],
            "cw.shop.buy_slot": lambda: buy_cw_shop_slot(session, slot=payload["slot"], expect=payload["expect"], buyer=shop_buyer_factory(runtime), scanner=shop_scanner_factory(runtime)).scene_state["cw"]["shop"],
            "cw.crystals.collect": lambda: collect_cw_crystals(session, collector=crystal_collector_factory(runtime)).scene_state["cw"]["metrics"],
            "cw.hand.sell_one": lambda: sell_one_cw_hand(session, slot=payload["slot"], seller=hand_seller_factory(runtime)).scene_state["cw"]["slots"],
            "cw.hand.sell_plan": lambda: plan_cw_hand_sell(session),
            "cw.replenish.choose": lambda: choose_cw_replenish(session, option=payload["option"], chooser=replenish_chooser_factory(runtime)).scene_state["cw"]["stage"],
            "cw.invest.choose": lambda: choose_cw_invest(session, option=payload["option"], chooser=invest_chooser_factory(runtime)).scene_state["cw"]["stage"],
            "cw.encounter.choose": lambda: choose_cw_encounter(session, option=payload["option"], chooser=encounter_chooser_factory(runtime)).scene_state["cw"]["stage"],
            "cw.fortune.choose": lambda: choose_cw_fortune(session, option=payload["option"], chooser=fortune_chooser_factory(runtime)).scene_state["cw"]["stage"],
            "cw.boss_preview.confirm": lambda: confirm_cw_boss_preview(session, confirmer=boss_preview_confirmer_factory(runtime)).scene_state["cw"]["stage"],
            "cw.battle.start": lambda: start_cw_battle(session, starter=battle_starter_factory(runtime)).scene_state["cw"]["stage"],
            "cw.battle.continue": lambda: continue_cw_battle(session, continuer=battle_continuer_factory(runtime)).scene_state["cw"]["stage"],
            "cw.settle.next": lambda: settle_cw_next(session, continuer=settle_continuer_factory(runtime)).scene_state["cw"]["stage"],
            "cw.event.handle": lambda: handle_cw_event(session, handler=event_handler_factory(runtime)),
        }
        if method in readers:
            result = readers[method]()
            session_service.save_session(session)
            return result
        if method in writers:
            result = writers[method]()
            session_service.save_session(session)
            return result
        raise NotImplementedError(method)


def _apply_guide(session, runtime, artifact_store, lineup_id: str):
    guide_data = fetch_cw_guide(lineup_id, fetcher=fetch_cw_guide_payload)
    apply_cw_guide_via_ui(runtime, share_code=guide_data["share_code"])
    artifact = artifact_store.create(scene="cw", kind="guide", payload=guide_data)
    return apply_cw_guide(session, guide_data={**guide_data, "artifact_id": artifact.artifact_id}).scene_state["cw"]["guide"]


def _set_stage(session, stage: dict):
    session.scene_state.setdefault("cw", {})["stage"] = stage
    return session


def _set_shop(session, shop: dict):
    session.scene_state.setdefault("cw", {})["shop"] = shop
    return session


def _set_entry(session, entry: dict):
    session.scene_state.setdefault("cw", {})["entry"] = entry
    session.scene_state["cw"]["stage"] = {"stale": True}
    return session


def _set_guide(session, guide: dict):
    session.scene_state.setdefault("cw", {})["guide"] = guide
    return session


def _set_slots(session, slots: dict):
    session.scene_state.setdefault("cw", {})["slots"] = slots
    return session


def _set_metrics(session, metrics: dict):
    session.scene_state.setdefault("cw", {})["metrics"] = metrics
    return session


def _set_sell_plan(session, plan: dict):
    session.scene_state.setdefault("cw", {})["sell_plan"] = plan
    return session


def _build_cw_harness(tmp_path: Path):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    return registry, session, runtime_service, cw_service, command_service


def _run_cw_mutation(*, command_service, session, workspace_root: Path, request_id: str, method: str, payload: dict):
    return command_service.handle(
        DaemonRequest(
            request_id=request_id,
            protocol_version=1,
            workspace_root=str(workspace_root),
            session_id=session.session_id,
            verbose=False,
            method=method,
            payload={"session_id": session.session_id, **payload},
        )
    )
```

这一步里，`tests/test_cw_entry.py`、`tests/test_cw_stage.py`、`tests/test_cw_slots.py`、`tests/test_cw_shop.py`、`tests/test_cw_events.py`、`tests/test_cw_guide.py` 都必须改成断言 daemon 侧 service / journal / session 持久化结果，不再保留旧的本地 `fake_runtime` 副作用断言。

执行要求：直接重写这 6 个测试文件中对应的旧用例，不要在原文件里保留“双轨断言”或临时兼容层；Task 10 完成后，这些文件应只服务于 daemon 侧行为验证。对于每个文件，至少把当前文件里所有 CLI 用例替换成等价的 daemon-side service 断言；不要把剩余旧 CLI 用例拖到 Task 11/12。

导入口径也要一起收口：这 6 个文件里凡是 daemon 侧断言段，统一补上 `from pathlib import Path`、`from types import SimpleNamespace`、`from trail.daemon.cw_service import CwService`、`from trail.daemon.session_service import SessionServiceRegistry`。文件前半段如果保留纯 scene/unit 测试可以继续沿用原导入；但原先所有 `cli_runner.invoke(app, ...)` 的 CLI 用例都要替换成下面这种 daemon 侧断言。

最小迁移口径：

- `tests/test_cw_entry.py`：改成断言 `CwService.handle("cw.enter", ...)` 后 `scene_state["cw"]["entry"]` 与 `stage.stale` 的持久化结果
- `tests/test_cw_stage.py`：改成断言 `cw.stage.detect` / `cw.stage.wait` 的 service 返回值与 session 落盘结果
- `tests/test_cw_slots.py`：改成断言 `cw.slots.read|swap|place_one` 的 slots 快照持久化
- `tests/test_cw_shop.py`：改成断言 `cw.shop.*` 的 shop 快照持久化与 `request_status()` 终态
- `tests/test_cw_events.py`：改成断言 `cw.event.handle` / `cw.battle.*` / `cw.settle.next` 的 stage 变化与 journal 终态
- `tests/test_cw_guide.py`：改成断言 `cw.guide.apply/current` 的 guide 状态、artifact id 与 session 持久化

每个旧测试文件至少要覆盖下面这些原有命令面，不允许只迁一个 happy-path 示例就结束：

- `tests/test_cw_entry.py`：`cw.enter`
- `tests/test_cw_stage.py`：`cw.stage.detect`、`cw.stage.wait`
- `tests/test_cw_slots.py`：`cw.slots.read`、`cw.slots.swap`、`cw.slots.place-one`、`cw.crystals.collect`、`cw.hand.sell-one`、`cw.hand.sell-plan`
- `tests/test_cw_shop.py`：`cw.shop.open`、`cw.shop.scan`、`cw.shop.buy-slot`、`cw.shop.refresh`、`cw.shop.close`、`cw.shop.status`
- `tests/test_cw_events.py`：`cw.replenish.read|choose`、`cw.invest.read|choose`、`cw.encounter.read|choose`、`cw.fortune.read|choose`、`cw.boss-preview.confirm`、`cw.battle.start|continue`、`cw.settle.next`、`cw.event.handle`
- `tests/test_cw_guide.py`：`cw.guide.apply`、`cw.guide.current`

直接按下面这些完整改写块重写；如果原文件里还有同类 CLI 用例没有被这些代码块覆盖，继续用同一结构把它们一条条改掉，不要留到 Task 11/12：

```python
# tests/test_cw_entry.py
def test_cw_enter_mutation_flows_through_command_service_journal(tmp_path: Path, monkeypatch):
    registry, session, runtime_service, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.enter_cw", lambda session, mode, difficulty, battle_mode, runtime: _set_entry(session, {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode}))

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-1",
        method="cw.enter",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
    )
    status = registry.for_workspace(str(tmp_path)).request_status("req-cw-enter-1")
    persisted = registry.for_workspace(str(tmp_path)).load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"]["mode"] == "continue"
    assert status["final_state"] == "completed"
    assert persisted.scene_state["cw"]["entry"]["battle_mode"] == "standard"
    assert persisted.scene_state["cw"]["stage"] == {"stale": True}


# tests/test_cw_stage.py
def test_cw_stage_detect_service_persists_stage_state(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.detect_cw_stage", lambda session, detector: _set_stage(session, {"value": "preparation", "stale": False}))

    result = cw_service.handle(method="cw.stage.detect", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))

    assert result["value"] == "preparation"
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["stage"]["value"] == "preparation"


def test_cw_stage_wait_service_persists_waited_stage_state(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.wait_cw_stage", lambda session, detector, timeout: _set_stage(session, {"value": "settle", "stale": False}))

    result = cw_service.handle(method="cw.stage.wait", payload={"session_id": session.session_id, "timeout": 120}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))

    assert result["value"] == "settle"
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["stage"] == {"value": "settle", "stale": False}


def test_cw_stage_detect_service_preserves_last_stage_snapshot(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.detect_cw_stage", lambda session, detector: _set_stage(session, {"value": "event", "stale": False}))

    result = cw_service.handle(method="cw.stage.detect", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=service)
    persisted = service.load_session(session.session_id)

    assert result["value"] == "event"
    assert persisted.scene_state["cw"]["stage"]["value"] == "event"


# tests/test_cw_slots.py
def test_cw_slots_read_service_persists_incremental_snapshot(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", lambda runtime, targets=None: lambda: (["希儿", None, None, None], [None] * 6, [None] * 9))

    result = cw_service.handle(method="cw.slots.read", payload={"session_id": session.session_id, "slot": ["front:0"]}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))

    assert result["front"][0] == "希儿"
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["slots"]["front"][0] == "希儿"


@pytest.mark.parametrize(
    ("method", "request_id", "payload", "setup_patches", "assertion_key", "assertion_value", "state_path"),
    [
        (
            "cw.slots.swap",
            "req-slots-swap-1",
            {"source": "hand:0", "target": "front:0"},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.slot_swapper_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.swap_cw_slots", lambda session, source, target, swapper: _set_slots(session, {"front": ["希儿"], "back": [], "hand": [], "stale": False})),
            ),
            "front",
            ["希儿"],
            ("slots", "front"),
        ),
        (
            "cw.slots.place_one",
            "req-slots-place-1",
            {"source": "hand:0", "target": "front:0"},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.slot_placer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.place_one_cw_slot", lambda session, source, target, placer: _set_slots(session, {"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": False})),
            ),
            "back",
            ["佩拉"],
            ("slots", "back"),
        ),
        (
            "cw.crystals.collect",
            "req-crystals-collect-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.crystal_collector_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.collect_cw_crystals", lambda session, collector: _set_metrics(session, {"last_crystal_collection": "done"})),
            ),
            "last_crystal_collection",
            "done",
            ("metrics", "last_crystal_collection"),
        ),
        (
            "cw.hand.sell_one",
            "req-hand-sell-one-1",
            {"slot": 0},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.hand_seller_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.sell_one_cw_hand", lambda session, slot, seller: _set_slots(session, {"front": [], "back": [], "hand": [None], "stale": True})),
            ),
            "stale",
            True,
            ("slots", "stale"),
        ),
        (
            "cw.hand.sell_plan",
            "req-hand-sell-plan-1",
            {},
            lambda monkeypatch: monkeypatch.setattr("trail.daemon.cw_service.plan_cw_hand_sell", lambda session: (_set_sell_plan(session, {"candidates": [0, 2]}), {"candidates": [0, 2]})[1]),
            "candidates",
            [0, 2],
            ("sell_plan", "candidates"),
        ),
    ],
)
def test_cw_slots_and_hand_mutations_flow_through_command_service_journal(tmp_path: Path, monkeypatch, method: str, request_id: str, payload: dict, setup_patches, assertion_key: str, assertion_value, state_path: tuple[str, str]):
    registry, session, runtime_service, cw_service, command_service = _build_cw_harness(tmp_path)
    setup_patches(monkeypatch)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=request_id,
        method=method,
        payload=payload,
    )
    status = registry.for_workspace(str(tmp_path)).request_status(request_id)
    persisted = registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]

    assert envelope["ok"] is True
    assert envelope["data"][assertion_key] == assertion_value
    assert status["final_state"] == "completed"
    assert persisted[state_path[0]][state_path[1]] == assertion_value


def test_cw_slots_read_service_preserves_existing_snapshot_shape(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.slots_reader_factory", lambda runtime, targets=None: lambda: ([None, "布洛妮娅", None, None], [None] * 6, [None] * 9))

    result = cw_service.handle(method="cw.slots.read", payload={"session_id": session.session_id, "slot": ["front:1"]}, workspace_root=str(tmp_path), session_service=service)

    assert result["front"][1] == "布洛妮娅"
    assert service.load_session(session.session_id).scene_state["cw"]["slots"]["front"][1] == "布洛妮娅"


# tests/test_cw_shop.py
@pytest.mark.parametrize(
    ("method", "request_id", "payload", "setup_patches", "expected_key", "expected_value"),
    [
        (
            "cw.shop.open",
            "req-shop-open-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_opener_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.open_cw_shop", lambda session, opener: _set_shop(session, {"opened": True, "stale": True})),
            ),
            "opened",
            True,
        ),
        (
            "cw.shop.buy_slot",
            "req-shop-buy-1",
            {"slot": 2, "expect": "希儿"},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_buyer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.buy_cw_shop_slot", lambda session, slot, expect, buyer, scanner: _set_shop(session, {"slot": slot, "expect": expect, "opened": True, "stale": False})),
            ),
            "expect",
            "希儿",
        ),
        (
            "cw.shop.refresh",
            "req-shop-refresh-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_refresher_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.refresh_cw_shop", lambda session, refresher: _set_shop(session, {"refreshed": True, "opened": True, "stale": False})),
            ),
            "refreshed",
            True,
        ),
        (
            "cw.shop.close",
            "req-shop-close-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_closer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.close_cw_shop", lambda session, closer: _set_shop(session, {"opened": False, "stale": True})),
            ),
            "opened",
            False,
        ),
    ],
)
def test_cw_shop_mutating_commands_flow_through_command_service_journal(tmp_path: Path, monkeypatch, method: str, request_id: str, payload: dict, setup_patches, expected_key: str, expected_value):
    registry, session, runtime_service, cw_service, command_service = _build_cw_harness(tmp_path)
    setup_patches(monkeypatch)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=request_id,
        method=method,
        payload=payload,
    )
    status = registry.for_workspace(str(tmp_path)).request_status(request_id)

    assert envelope["ok"] is True
    assert envelope["data"][expected_key] == expected_value
    assert status["final_state"] == "completed"
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["shop"][expected_key] == expected_value


def test_cw_shop_read_commands_persist_shop_snapshot(tmp_path: Path, monkeypatch):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = registry.for_workspace(str(tmp_path)).load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    registry.for_workspace(str(tmp_path)).save_session(loaded)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr("trail.daemon.cw_service.shop_opener_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.open_cw_shop", lambda session, opener: _set_shop(session, {"opened": True, "stale": True}))
    monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: lambda: ([{"name": "银狼", "price": 20}], 40, 7, False, 8))
    monkeypatch.setattr("trail.daemon.cw_service.shop_refresher_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.refresh_cw_shop", lambda session, refresher: _set_shop(session, {"refreshed": True, "opened": True, "stale": False}))
    monkeypatch.setattr("trail.daemon.cw_service.shop_closer_factory", lambda runtime: object())
    monkeypatch.setattr("trail.daemon.cw_service.close_cw_shop", lambda session, closer: _set_shop(session, {"opened": False, "stale": True}))

    opened = cw_service.handle(method="cw.shop.open", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))
    scanned = cw_service.handle(method="cw.shop.scan", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))
    refreshed = cw_service.handle(method="cw.shop.refresh", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))
    closed = cw_service.handle(method="cw.shop.close", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))
    status = cw_service.handle(method="cw.shop.status", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))

    assert opened["opened"] is True
    assert scanned["guide_summary"]["remaining_purchases"] == {"银狼": 2}
    assert refreshed["refreshed"] is True
    assert closed["opened"] is False
    assert status["opened"] is False


# tests/test_cw_events.py
@pytest.mark.parametrize(
    ("method", "patch_name", "expected_options"),
    [
        ("cw.replenish.read", "read_cw_replenish", [1, 2, 3]),
        ("cw.invest.read", "read_cw_invest", [1, 2, 3]),
        ("cw.encounter.read", "read_cw_encounter", [1, 2]),
        ("cw.fortune.read", "read_cw_fortune", [1, 2]),
    ],
)
def test_cw_event_read_services_return_options(tmp_path: Path, monkeypatch, method: str, patch_name: str, expected_options: list[int]):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    monkeypatch.setattr(f"trail.daemon.cw_service.{patch_name}", lambda session: {"options": expected_options})

    result = cw_service.handle(method=method, payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))

    assert result == {"options": expected_options}


@pytest.mark.parametrize(
    ("method", "request_id", "payload", "setup_patches", "expected_data"),
    [
        (
            "cw.event.handle",
            "req-event-handle-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.event_handler_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.handle_cw_event", lambda session, handler: {"event_type": "special", "handled_action": "confirm"}),
            ),
            {"event_type": "special", "handled_action": "confirm"},
        ),
        (
            "cw.replenish.choose",
            "req-replenish-choose-1",
            {"option": 2},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.replenish_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_replenish", lambda session, option, chooser: _set_stage(session, {"value": "invest", "stale": False})),
            ),
            {"value": "invest", "stale": False},
        ),
        (
            "cw.invest.choose",
            "req-invest-choose-1",
            {"option": 1},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.invest_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_invest", lambda session, option, chooser: _set_stage(session, {"value": "battle", "stale": False})),
            ),
            {"value": "battle", "stale": False},
        ),
        (
            "cw.encounter.choose",
            "req-encounter-choose-1",
            {"option": 1},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.encounter_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_encounter", lambda session, option, chooser: _set_stage(session, {"value": "battle", "stale": False})),
            ),
            {"value": "battle", "stale": False},
        ),
        (
            "cw.fortune.choose",
            "req-fortune-choose-1",
            {"option": 2},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.fortune_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_fortune", lambda session, option, chooser: _set_stage(session, {"value": "battle", "stale": False})),
            ),
            {"value": "battle", "stale": False},
        ),
        (
            "cw.boss_preview.confirm",
            "req-boss-confirm-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.boss_preview_confirmer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.confirm_cw_boss_preview", lambda session, confirmer: _set_stage(session, {"value": "battle", "stale": False})),
            ),
            {"value": "battle", "stale": False},
        ),
        (
            "cw.battle.start",
            "req-battle-start-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.battle_starter_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.start_cw_battle", lambda session, starter: _set_stage(session, {"value": "battle", "stale": False})),
            ),
            {"value": "battle", "stale": False},
        ),
        (
            "cw.battle.continue",
            "req-battle-continue-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.battle_continuer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.continue_cw_battle", lambda session, continuer: _set_stage(session, {"value": "settle", "stale": False})),
            ),
            {"value": "settle", "stale": False},
        ),
        (
            "cw.settle.next",
            "req-settle-next-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.settle_continuer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.settle_cw_next", lambda session, continuer: _set_stage(session, {"value": "preparation", "stale": False})),
            ),
            {"value": "preparation", "stale": False},
        ),
    ],
)
def test_cw_event_mutating_commands_flow_through_command_service_journal(tmp_path: Path, monkeypatch, method: str, request_id: str, payload: dict, setup_patches, expected_data: dict):
    registry, session, runtime_service, cw_service, command_service = _build_cw_harness(tmp_path)
    setup_patches(monkeypatch)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=request_id,
        method=method,
        payload=payload,
    )
    status = registry.for_workspace(str(tmp_path)).request_status(request_id)

    assert envelope["ok"] is True
    assert envelope["data"] == expected_data
    assert status["final_state"] == "completed"




# tests/test_cw_guide.py
def test_cw_guide_current_service_reads_persisted_guide_state(tmp_path: Path):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    loaded = registry.for_workspace(str(tmp_path)).load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"lineup_id": "abc", "artifact_id": "art-1"}
    registry.for_workspace(str(tmp_path)).save_session(loaded)
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)

    result = cw_service.handle(method="cw.guide.current", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))

    assert result["lineup_id"] == "abc"


def test_cw_guide_apply_mutation_flows_through_command_service_journal(tmp_path: Path, monkeypatch):
    registry, session, runtime_service, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service._apply_guide", lambda session, runtime, artifact_store, lineup_id: _set_guide(session, {"lineup_id": lineup_id, "artifact_id": "art-1", "share_code": "share-1"}).scene_state["cw"]["guide"])

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-guide-apply-1",
        method="cw.guide.apply",
        payload={"lineup_id": "abc"},
    )
    status = registry.for_workspace(str(tmp_path)).request_status("req-guide-apply-1")

    assert envelope["ok"] is True
    assert envelope["data"]["lineup_id"] == "abc"
    assert status["final_state"] == "completed"
    assert registry.for_workspace(str(tmp_path)).load_session(session.session_id).scene_state["cw"]["guide"]["artifact_id"] == "art-1"


def test_cw_guide_current_service_returns_none_when_not_applied(tmp_path: Path):
    registry = SessionServiceRegistry()
    session = registry.for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)

    result = cw_service.handle(method="cw.guide.current", payload={"session_id": session.session_id}, workspace_root=str(tmp_path), session_service=registry.for_workspace(str(tmp_path)))

    assert result is None
```

这些示例只负责把迁移口径锁死，不代表你可以省掉同文件其余旧 CLI 用例。执行时要用同样模式，把每个文件里剩余的 CLI 断言继续改写成 daemon-side `CwService.handle(...)` 断言，直到该文件只剩 scene/unit 测试 + daemon service 测试两类内容。

`trail/daemon/server.py`

```python
def main() -> None:
    runtime_service = RuntimeService()
    session_service = SessionServiceRegistry()
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=session_service, cw_service=cw_service)
    TrailDaemonServer(command_service=command_service).serve_forever()
```

- [ ] **Step 4: 跑 daemon 侧 `cw` 测试到绿**

Run: `pytest tests/test_daemon_protocol.py tests/test_cw_entry.py tests/test_cw_stage.py tests/test_cw_slots.py tests/test_cw_shop.py tests/test_cw_events.py tests/test_cw_guide.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trail/daemon/cw_service.py trail/daemon/command_service.py trail/daemon/server.py tests/test_daemon_protocol.py tests/test_daemon_session_service.py tests/test_cw_entry.py tests/test_cw_stage.py tests/test_cw_slots.py tests/test_cw_shop.py tests/test_cw_events.py tests/test_cw_guide.py
git commit -m "feat(daemon): 补齐 cw daemon 侧方法映射"
```

### Task 11: 原子 cutover `session/state/cw` 的 CLI 入口到 RPC

**Execution note:** 现有 `tests/test_cw_entry.py`、`tests/test_cw_stage.py`、`tests/test_cw_slots.py`、`tests/test_cw_shop.py`、`tests/test_cw_events.py`、`tests/test_cw_guide.py` 已在 Task 10 被迁成 daemon 侧断言；Task 11 不再重复修改它们，只新增 CLI 专用的 RPC 契约测试 `tests/test_cw_rpc_contracts.py`。

**Files:**
- Modify: `trail/commands/session.py`
- Modify: `trail/commands/state.py`
- Modify: `trail/commands/cw.py`
- Test: `tests/test_atomic_commands.py`
- Create: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 写 `session create`、`state dump`、`cw stage detect` 都经由 daemon 的失败测试**

```python
def test_session_create_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.session as session_cmd
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "session.create": {
                "ok": True,
                "data": {"session_id": "abc"},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["session", "create", "--window-title", "崩坏：星穹铁道"])
    payload = json.loads(result.stdout)

    assert payload["data"]["session_id"] == "abc"
    assert fake_client.calls[0]["method"] == "session.create"


def test_state_dump_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.state as state_cmd
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "state.dump": {
                "ok": True,
                "data": {"scene_state": {"cw": {"stage": "preparation"}}},
                "screenshot": ".trail/shots/req-state.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["state", "dump", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["scene_state"]["cw"]["stage"] == "preparation"
    assert fake_client.calls[0]["method"] == "state.dump"


def test_state_dump_missing_session_rpc_error(cli_runner, monkeypatch):
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "state.dump": {
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": {"code": "SESSION_NOT_FOUND", "message": "session missing"},
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["state", "dump", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["error"]["code"] == "SESSION_NOT_FOUND"


def test_state_dump_rpc_contract_replaces_local_runtime_binding_assertions(cli_runner, monkeypatch):
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "state.dump": {
                "ok": True,
                "data": {"window_binding": {"title": "崩坏：星穹铁道", "hwnd": 1}},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["state", "dump", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["window_binding"]["hwnd"] == 1
    assert fake_client.calls[0]["method"] == "state.dump"


def test_state_dump_returns_session_snapshot(cli_runner, monkeypatch):
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "state.dump": {
                "ok": True,
                "data": {"scene_state": {"cw": {"stage": "preparation"}}, "window_binding": {"title": "崩坏：星穹铁道", "hwnd": 1}},
                "screenshot": ".trail/shots/req-state-snapshot.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["state", "dump", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["window_binding"]["title"] == "崩坏：星穹铁道"
    assert fake_client.calls[0]["method"] == "state.dump"


def test_state_dump_returns_structured_error_for_missing_session(cli_runner, monkeypatch):
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "state.dump": {
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": {"code": "SESSION_NOT_FOUND", "message": "session missing"},
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["state", "dump", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["error"]["code"] == "SESSION_NOT_FOUND"


def test_state_dump_uses_session_window_binding_for_runtime(cli_runner, monkeypatch):
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "state.dump": {
                "ok": True,
                "data": {"window_binding": {"title": "崩坏：星穹铁道", "hwnd": 2}},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["state", "dump", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["window_binding"]["hwnd"] == 2
    assert fake_client.calls[0]["payload"]["session_id"] == "a" * 32


def test_cw_stage_detect_uses_daemon_client(cli_runner, monkeypatch):
    import trail.commands.cw as cw_cmd
    import trail.commands.helpers as helper_mod

    fake_client = FakeDaemonClient(
        responses={
            "cw.stage.detect": {
                "ok": True,
                "data": {"value": "preparation", "stale": False},
                "screenshot": ".trail/shots/req-cw.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            }
        }
    )
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: fake_client)

    result = cli_runner.invoke(app, ["cw", "stage", "detect", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["value"] == "preparation"
    assert fake_client.calls[0]["method"] == "cw.stage.detect"
```

执行要求补充：`tests/test_atomic_commands.py` 里现有的 `state dump` 旧断言必须直接替换成这里给出的 RPC 版本，不再保留任何基于 `session_store_factory` / `runtime_factory` 的本地路径断言。Task 11 完成后，`tests/test_atomic_commands.py` 对 `state dump` 只验证 RPC 调用、payload 透传与结构化错误输出。

- [ ] **Step 2: 跑红灯确认 `session/state/cw` CLI 入口仍是本地执行**

Run: `pytest tests/test_atomic_commands.py tests/test_cw_rpc_contracts.py -q`
Expected: 至少新增 cutover 测试 FAIL

- [ ] **Step 3: 把 `session.py`、`state.py`、`cw.py` 全部切到 RPC 薄壳，并新增局部 RPC 契约测试文件**

`tests/test_cw_rpc_contracts.py`

```python
@pytest.fixture
def fake_daemon_client(monkeypatch):
    import trail.commands.helpers as helper_mod

    client = FakeDaemonClient(responses={})
    monkeypatch.setattr(helper_mod, "build_default_daemon_client", lambda *args, **kwargs: client)
    return client


def test_cw_stage_detect_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.stage.detect"] = {
        "ok": True,
        "data": {"value": "preparation", "stale": False},
        "screenshot": ".trail/shots/req-cw-stage.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "stage", "detect", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["value"] == "preparation"
    assert fake_daemon_client.calls[0]["method"] == "cw.stage.detect"


def test_cw_enter_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.enter"] = {
        "ok": True,
        "data": {"mode": "new", "difficulty": "current", "battle_mode": "standard"},
        "screenshot": ".trail/shots/req-cw-enter.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "enter", "--session", "a" * 32, "--mode", "new", "--difficulty", "current", "--battle-mode", "standard"])
    payload = json.loads(result.stdout)

    assert payload["data"]["mode"] == "new"
    assert fake_daemon_client.calls[0]["payload"]["difficulty"] == "current"


def test_cw_shop_buy_slot_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.shop.buy_slot"] = {
        "ok": True,
        "data": {"slot": 2, "expect": "希儿"},
        "screenshot": ".trail/shots/req-cw-shop.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "shop", "buy-slot", "--session", "a" * 32, "--slot", "2", "--expect", "希儿"])
    payload = json.loads(result.stdout)

    assert payload["data"]["slot"] == 2
    assert fake_daemon_client.calls[0]["method"] == "cw.shop.buy_slot"


def test_cw_guide_current_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.guide.current"] = {
        "ok": True,
        "data": {"lineup_id": "abc", "artifact_id": "art-1"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "guide", "current", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["artifact_id"] == "art-1"
    assert fake_daemon_client.calls[0]["method"] == "cw.guide.current"


def test_cw_slots_place_one_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.slots.place_one"] = {
        "ok": True,
        "data": {"front": ["希儿"], "back": [], "hand": [], "stale": False},
        "screenshot": ".trail/shots/req-cw-slots.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "slots", "place-one", "--session", "a" * 32, "--source", "hand:0", "--target", "front:0"])
    payload = json.loads(result.stdout)

    assert payload["data"]["front"][0] == "希儿"
    assert fake_daemon_client.calls[0]["method"] == "cw.slots.place_one"


def test_cw_invest_choose_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.invest.choose"] = {
        "ok": True,
        "data": {"value": "battle", "stale": False},
        "screenshot": ".trail/shots/req-cw-invest.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "invest", "choose", "--session", "a" * 32, "--option", "2"])
    payload = json.loads(result.stdout)

    assert payload["data"]["value"] == "battle"
    assert fake_daemon_client.calls[0]["payload"]["option"] == 2


def test_cw_battle_continue_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.battle.continue"] = {
        "ok": True,
        "data": {"value": "settle", "stale": False},
        "screenshot": ".trail/shots/req-cw-battle.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "battle", "continue", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["value"] == "settle"
    assert fake_daemon_client.calls[0]["method"] == "cw.battle.continue"


def test_cw_invest_read_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.invest.read"] = {
        "ok": True,
        "data": {"options": [{"id": 1, "name": "量子力学"}]},
        "screenshot": ".trail/shots/req-cw-invest-read.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "invest", "read", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["options"][0]["name"] == "量子力学"
    assert fake_daemon_client.calls[0]["method"] == "cw.invest.read"


def test_cw_stage_wait_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.stage.wait"] = {
        "ok": True,
        "data": {"value": "settle", "stale": False},
        "screenshot": ".trail/shots/req-cw-stage-wait.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "stage", "wait", "--session", "a" * 32, "--timeout", "120"])
    payload = json.loads(result.stdout)

    assert payload["data"]["value"] == "settle"
    assert fake_daemon_client.calls[0]["payload"]["timeout"] == 120


def test_cw_guide_apply_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.guide.apply"] = {
        "ok": True,
        "data": {"lineup_id": "abc", "artifact_id": "art-1"},
        "screenshot": ".trail/shots/req-cw-guide-apply.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", "a" * 32, "--lineup-id", "abc"])
    payload = json.loads(result.stdout)

    assert payload["data"]["artifact_id"] == "art-1"
    assert fake_daemon_client.calls[0]["method"] == "cw.guide.apply"


def test_cw_shop_status_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.shop.status"] = {
        "ok": True,
        "data": {"items": [{"slot": 1, "name": "希儿"}]},
        "screenshot": ".trail/shots/req-cw-shop-status.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["items"][0]["name"] == "希儿"
    assert fake_daemon_client.calls[0]["method"] == "cw.shop.status"


def test_cw_event_handle_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.event.handle"] = {
        "ok": True,
        "data": {"value": "settle", "stale": False},
        "screenshot": ".trail/shots/req-cw-event.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "event", "handle", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["value"] == "settle"
    assert fake_daemon_client.calls[0]["method"] == "cw.event.handle"


def test_cw_replenish_read_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.replenish.read"] = {
        "ok": True,
        "data": {"options": [1, 2, 3]},
        "screenshot": ".trail/shots/req-cw-replenish-read.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "replenish", "read", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["options"] == [1, 2, 3]
    assert fake_daemon_client.calls[0]["method"] == "cw.replenish.read"


def test_cw_encounter_read_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.encounter.read"] = {
        "ok": True,
        "data": {"options": [1, 2]},
        "screenshot": ".trail/shots/req-cw-encounter-read.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "encounter", "read", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["options"] == [1, 2]
    assert fake_daemon_client.calls[0]["method"] == "cw.encounter.read"


def test_cw_fortune_read_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.fortune.read"] = {
        "ok": True,
        "data": {"options": [1, 2]},
        "screenshot": ".trail/shots/req-cw-fortune-read.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "fortune", "read", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["options"] == [1, 2]
    assert fake_daemon_client.calls[0]["method"] == "cw.fortune.read"


def test_cw_crystals_collect_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.crystals.collect"] = {
        "ok": True,
        "data": {"last_crystal_collection": "done"},
        "screenshot": ".trail/shots/req-cw-crystals.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "crystals", "collect", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["last_crystal_collection"] == "done"
    assert fake_daemon_client.calls[0]["method"] == "cw.crystals.collect"


def test_cw_settle_next_rpc_contract(cli_runner, fake_daemon_client):
    fake_daemon_client._responses["cw.settle.next"] = {
        "ok": True,
        "data": {"value": "preparation", "stale": False},
        "screenshot": ".trail/shots/req-cw-settle-next.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, ["cw", "settle", "next", "--session", "a" * 32])
    payload = json.loads(result.stdout)

    assert payload["data"]["value"] == "preparation"
    assert fake_daemon_client.calls[0]["method"] == "cw.settle.next"


@pytest.mark.parametrize(
    ("args", "method", "payload_subset"),
    [
        (["cw", "slots", "read", "--session", "a" * 32, "--slot", "front:0"], "cw.slots.read", {"slot": ["front:0"]}),
        (["cw", "slots", "swap", "--session", "a" * 32, "--source", "hand:0", "--target", "front:0"], "cw.slots.swap", {"source": "hand:0", "target": "front:0"}),
        (["cw", "replenish", "choose", "--session", "a" * 32, "--option", "2"], "cw.replenish.choose", {"option": 2}),
        (["cw", "hand", "sell-one", "--session", "a" * 32, "--slot", "1"], "cw.hand.sell_one", {"slot": 1}),
        (["cw", "hand", "sell-plan", "--session", "a" * 32], "cw.hand.sell_plan", {}),
        (["cw", "shop", "open", "--session", "a" * 32], "cw.shop.open", {}),
        (["cw", "shop", "scan", "--session", "a" * 32], "cw.shop.scan", {}),
        (["cw", "shop", "refresh", "--session", "a" * 32], "cw.shop.refresh", {}),
        (["cw", "shop", "close", "--session", "a" * 32], "cw.shop.close", {}),
        (["cw", "encounter", "choose", "--session", "a" * 32, "--option", "1"], "cw.encounter.choose", {"option": 1}),
        (["cw", "fortune", "choose", "--session", "a" * 32, "--option", "1"], "cw.fortune.choose", {"option": 1}),
        (["cw", "boss-preview", "confirm", "--session", "a" * 32], "cw.boss_preview.confirm", {}),
        (["cw", "battle", "start", "--session", "a" * 32], "cw.battle.start", {}),
    ],
)
def test_cw_rpc_wrapper_matrix(cli_runner, fake_daemon_client, args, method, payload_subset):
    fake_daemon_client._responses[method] = {
        "ok": True,
        "data": {"ok": True},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    result = cli_runner.invoke(app, args)
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert fake_daemon_client.calls[0]["method"] == method
    for key, value in payload_subset.items():
        assert fake_daemon_client.calls[0]["payload"][key] == value
```

`trail/commands/session.py`

```python
daemon_client_factory = build_default_daemon_client


@session_app.command("create")
def session_create(window_title: str = typer.Option("崩坏：星穹铁道", "--window-title")) -> None:
    print_json(call_daemon("session.create", {"window_title": window_title}))
```

`trail/commands/state.py`

```python
daemon_client_factory = build_default_daemon_client


@state_app.command("dump")
def state_dump(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(call_daemon("state.dump", {"session_id": session}, session_id=session))
```

`trail/commands/cw.py`

```python
daemon_client_factory = build_default_daemon_client


def _rpc_cw(method: str, *, session_id: str, payload: dict | None = None) -> dict:
    return call_daemon(method, {"session_id": session_id, **(payload or {})}, session_id=session_id)


@stage_app.command("detect")
def cw_stage_detect(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.stage.detect", session_id=session))


@cw_app.command("enter")
def cw_enter(
    session: Annotated[str, typer.Option("--session")],
    mode: EnterMode = typer.Option(..., "--mode"),
    difficulty: EnterDifficulty = typer.Option(EnterDifficulty.CURRENT, "--difficulty"),
    battle_mode: BattleMode = typer.Option(BattleMode.STANDARD, "--battle-mode"),
) -> None:
    print_json(_rpc_cw("cw.enter", session_id=session, payload={"mode": mode.value, "difficulty": difficulty.value, "battle_mode": battle_mode.value}))


@slots_app.command("read")
def cw_slots_read(
    session: Annotated[str, typer.Option("--session")],
    slot: list[str] | None = typer.Option(None, "--slot"),
) -> None:
    print_json(_rpc_cw("cw.slots.read", session_id=session, payload={"slot": list(slot or []) or None}))


@stage_app.command("wait")
def cw_stage_wait(
    session: Annotated[str, typer.Option("--session")],
    timeout: int = typer.Option(120, "--timeout"),
) -> None:
    print_json(_rpc_cw("cw.stage.wait", session_id=session, payload={"timeout": timeout}))


@slots_app.command("swap")
def cw_slots_swap(
    session: Annotated[str, typer.Option("--session")],
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
) -> None:
    print_json(_rpc_cw("cw.slots.swap", session_id=session, payload={"source": source, "target": target}))


@slots_app.command("place-one")
def cw_slots_place_one(
    session: Annotated[str, typer.Option("--session")],
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
) -> None:
    print_json(_rpc_cw("cw.slots.place_one", session_id=session, payload={"source": source, "target": target}))


@cw_guide_app.command("apply")
def cw_guide_apply(
    session: Annotated[str, typer.Option("--session")],
    lineup_id: str = typer.Option(..., "--lineup-id", "--guide"),
) -> None:
    print_json(_rpc_cw("cw.guide.apply", session_id=session, payload={"lineup_id": lineup_id}))


@replenish_app.command("choose")
def cw_replenish_choose(
    session: Annotated[str, typer.Option("--session")],
    option: int = typer.Option(..., "--option"),
) -> None:
    print_json(_rpc_cw("cw.replenish.choose", session_id=session, payload={"option": option}))


@replenish_app.command("read")
def cw_replenish_read(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.replenish.read", session_id=session))


@crystals_app.command("collect")
def cw_crystals_collect(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.crystals.collect", session_id=session))


@hand_app.command("sell-one")
def cw_hand_sell_one(
    session: Annotated[str, typer.Option("--session")],
    slot: int = typer.Option(..., "--slot"),
) -> None:
    print_json(_rpc_cw("cw.hand.sell_one", session_id=session, payload={"slot": slot}))


@hand_app.command("sell-plan")
def cw_hand_sell_plan(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.hand.sell_plan", session_id=session))


@shop_app.command("open")
def cw_shop_open(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.shop.open", session_id=session))


@shop_app.command("scan")
def cw_shop_scan(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.shop.scan", session_id=session))


@shop_app.command("refresh")
def cw_shop_refresh(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.shop.refresh", session_id=session))


@shop_app.command("close")
def cw_shop_close(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.shop.close", session_id=session))


@shop_app.command("status")
def cw_shop_status(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.shop.status", session_id=session))


@invest_app.command("read")
def cw_invest_read(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.invest.read", session_id=session))


@invest_app.command("choose")
def cw_invest_choose(
    session: Annotated[str, typer.Option("--session")],
    option: int = typer.Option(..., "--option"),
) -> None:
    print_json(_rpc_cw("cw.invest.choose", session_id=session, payload={"option": option}))


@encounter_app.command("read")
def cw_encounter_read(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.encounter.read", session_id=session))


@encounter_app.command("choose")
def cw_encounter_choose(
    session: Annotated[str, typer.Option("--session")],
    option: int = typer.Option(..., "--option"),
) -> None:
    print_json(_rpc_cw("cw.encounter.choose", session_id=session, payload={"option": option}))


@fortune_app.command("read")
def cw_fortune_read(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.fortune.read", session_id=session))


@fortune_app.command("choose")
def cw_fortune_choose(
    session: Annotated[str, typer.Option("--session")],
    option: int = typer.Option(..., "--option"),
) -> None:
    print_json(_rpc_cw("cw.fortune.choose", session_id=session, payload={"option": option}))


@boss_preview_app.command("confirm")
def cw_boss_preview_confirm(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.boss_preview.confirm", session_id=session))


@battle_app.command("start")
def cw_battle_start(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.battle.start", session_id=session))


@battle_app.command("continue")
def cw_battle_continue(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.battle.continue", session_id=session))


@settle_app.command("next")
def cw_settle_next(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.settle.next", session_id=session))


@event_app.command("handle")
def cw_event_handle(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.event.handle", session_id=session))


@cw_guide_app.command("current")
def cw_guide_current(session: Annotated[str, typer.Option("--session")]) -> None:
    print_json(_rpc_cw("cw.guide.current", session_id=session))


@shop_app.command("buy-slot")
def cw_shop_buy_slot(
    session: Annotated[str, typer.Option("--session")],
    slot: int = typer.Option(..., "--slot"),
    expect: str = typer.Option(..., "--expect"),
) -> None:
    print_json(_rpc_cw("cw.shop.buy_slot", session_id=session, payload={"slot": slot, "expect": expect}))
```

- [ ] **Step 4: 跑全量 `session/state/cw` 契约测试到绿**

Run: `pytest tests/test_atomic_commands.py tests/test_cw_rpc_contracts.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trail/commands/session.py trail/commands/state.py trail/commands/cw.py tests/test_atomic_commands.py tests/test_cw_rpc_contracts.py
git commit -m "feat(daemon): 完成 session state cw RPC cutover"
```

### Task 12: 文档、帮助与实机验收

**Files:**
- Modify: `README.md`
- Modify: `skills/trail-hsr/SKILL.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `skills/trail-cw-shop/SKILL.md`
- Modify: `skills/trail-cw-slots/SKILL.md`
- Modify: `skills/trail-cw-events/SKILL.md`
- Modify: `skills/trail-cw-replenish/SKILL.md`

- [ ] **Step 1: 更新 README 与全部相关 skills，明确 daemon install/start/status/request-status/reconcile-session 的用法**

```markdown
- 首次使用：`trail daemon install`
- 查看状态：`trail daemon status`
- 排查未知结果：`trail daemon request-status --request-id <id>`
- 清理 tainted：`trail daemon reconcile-session --session <id>`
```

- [ ] **Step 2: 跑完整自动化回归**

Run: `pytest -q --basetemp .trail/pytest-temp -p no:cacheprovider`
Expected: PASS

- [ ] **Step 3: 执行冷启动 smoke**

Run:

```bash
trail daemon install
trail daemon status
trail window launch --game-path "C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe" --use-cmd
```

Expected:

- `install` 成功
- `status` 显示 `installed` 或 `ready`
- 游戏成功启动

- [ ] **Step 4: 执行 OCR 预热 smoke**

Run:

```bash
trail daemon start
$first = trail --verbose ocr read | ConvertFrom-Json
$second = trail --verbose ocr read | ConvertFrom-Json
$third = trail --verbose ocr read | ConvertFrom-Json
$timings = @($first.timing.elapsed_ms, $second.timing.elapsed_ms, $third.timing.elapsed_ms)
$timings
```

Expected:

- daemon 已 ready
- `$timings[1]` 与 `$timings[2]` 的中位数小于 `$timings[0]`
- `debug` 可用于排障

- [ ] **Step 5: 执行截图与输入 smoke**

Run:

```bash
$shot1 = trail --verbose screen shot | ConvertFrom-Json
$shot2 = trail --verbose screen shot | ConvertFrom-Json
$shot1.screenshot
$shot2.screenshot
$shot = $shot2
$image = [System.Drawing.Image]::FromFile($shot.screenshot)
$image.Width; $image.Height
$payload = trail --verbose input click 960 540 | ConvertFrom-Json
trail daemon request-status --request-id $payload.debug.request_id
```

Expected:

- 图片尺寸输出必须是 `1920` 与 `1080`
- `$shot1.screenshot` 与 `$shot2.screenshot` 必须不同
- 已提权游戏窗口输入真实生效
- `request-status` 返回机器可消费终态

- [ ] **Step 6: Commit**

```bash
git add README.md skills/trail-hsr/SKILL.md skills/trail-cw/SKILL.md skills/trail-cw-guide/SKILL.md skills/trail-cw-shop/SKILL.md skills/trail-cw-slots/SKILL.md skills/trail-cw-events/SKILL.md skills/trail-cw-replenish/SKILL.md
git commit -m "docs(daemon): 更新常驻服务运行与验收说明"
```
