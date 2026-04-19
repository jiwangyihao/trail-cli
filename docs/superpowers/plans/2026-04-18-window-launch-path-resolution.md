# Window Launch Path Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `trail window launch` 在未显式传 `--game-path` 时，按“历史成功路径 -> 默认路径 -> 直接问用户”自动解析游戏路径，并把成功路径持久化到客户端用户级状态中。

**Architecture:** 把“启动路径状态”和“启动路径解析顺序”从当前 `window.launch` 的显式参数模式里抽出来，增加一层共享路径 helper 与一层用户级路径状态读写模块。CLI 仍保留显式 `--game-path`，但 runtime 在无显式路径时负责解析历史路径与默认路径；daemon 只做透传，不承接策略。成功路径持久化与失败协议会通过现有 success/failure/warn 渲染链路对外暴露。

**Tech Stack:** Python 3.12、Typer、daemon RPC、现有 `TrailError` / rendering 协议、Windows 启动路径、pytest。

---

## File Map

- Create: `trail/daemon/paths.py`
  责任：提供共享的 daemon home 与用户级状态文件路径 helper，避免 runtime 复制 daemon 目录规则。
- Create: `trail/runtime/launch_paths.py`
  责任：封装 `~/.trail-daemon/game-paths.json` 的读写、按 `channel` 分桶、损坏文件退化为“无历史路径”。
- Modify: `trail/daemon/bootstrap.py`
  责任：改为复用新的共享路径 helper，而不是继续独占 `resolve_daemon_home()`。
- Modify: `trail/commands/daemon.py`
  责任：改为从共享 helper 取 daemon home，避免双份实现。
- Modify: `trail/commands/helpers.py`
  责任：改为从共享 helper 取 daemon home。
- Modify: `trail/daemon/server.py`
  责任：改为从共享 helper 取 daemon home。
- Modify: `trail/commands/window.py`
  责任：让 `--game-path` 变为可选，更新 help 文案。
- Modify: `trail/runtime/window.py`
  责任：实现显式路径优先、历史成功路径、默认路径、`GAME_PATH_REQUIRED`、`GAME_PATH_PERSIST_FAILED`、`already_running=1` 不回写历史等主语义。
- Modify: `trail/daemon/command_service.py`
  责任：把 `window.launch` 运行结果里的 `warnings` 提升到 envelope 顶层，确保 `warn code=GAME_PATH_PERSIST_FAILED` 真能进入默认文本输出。
- Modify: `tests/test_daemon_bootstrap.py`
  责任：覆盖共享路径 helper 与 daemon home 相关路径的一致性。
- Modify: `tests/test_runtime_backends.py`
  责任：覆盖 runtime 层解析顺序、显式路径硬约束、按 `channel` 分桶、持久化、退化语义。
- Modify: `tests/test_atomic_commands.py`
  责任：覆盖 CLI 可选 `--game-path`、help、显式参数优先级。
- Modify: `tests/test_daemon_protocol.py`
  责任：覆盖 daemon request 在无 `game_path` 时仍可透传，以及 `GAME_PATH_REQUIRED` / `GAME_PATH_PERSIST_FAILED` 的 envelope 兼容性。
- Modify: `tests/test_output_rendering.py`
  责任：覆盖 `window.launch` 的失败/警告协议，如 `GAME_PATH_REQUIRED` 无 recover、成功但持久化失败时有 `warn`。
- Modify: `README.md`
  责任：补充 `window launch` 的自动路径解析顺序、默认路径、channel 差异与显式路径优先级。
- Modify: `skills/trail-hsr/SKILL.md`
  责任：同步普通用户/Agent 的启动路径新规则。
- Modify: `skills/trail-cw/SKILL.md`
  责任：同步货币战争流程里如何处理 `window launch` 的新规则。

## Baseline Note

- 当前 `trail window launch` 仍强制 `--game-path`；这轮实现后该参数改为可选。
- 当前完整测试基线应统一带 `--basetemp .trail/pytest-tmp/...`。
- 未经用户明确要求，不创建 git commit。

### Task 1: 提取共享路径 helper 与用户级启动路径状态

**Files:**
- Create: `trail/daemon/paths.py`
- Create: `trail/runtime/launch_paths.py`
- Modify: `trail/daemon/bootstrap.py`
- Modify: `trail/commands/daemon.py`
- Modify: `trail/commands/helpers.py`
- Modify: `trail/daemon/server.py`
- Modify: `tests/test_daemon_bootstrap.py`
- Modify: `tests/test_runtime_backends.py`

- [ ] **Step 1: 先写失败测试，冻结共享路径 helper**

```python
def test_game_paths_path_for_user_is_under_daemon_home(tmp_path):
    from trail.daemon.paths import game_paths_path_for_user

    path = game_paths_path_for_user(tmp_path)

    assert path == tmp_path / "game-paths.json"
```

- [ ] **Step 2: 先写失败测试，冻结损坏状态文件的退化语义**

```python
def test_read_launch_paths_treats_invalid_json_as_empty(tmp_path):
    state_file = tmp_path / "game-paths.json"
    state_file.write_text("{broken", encoding="utf-8")

    paths = read_launch_paths(state_file)

    assert paths == {}
```

- [ ] **Step 3: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/launch-path-red-shared tests/test_daemon_bootstrap.py tests/test_runtime_backends.py -k "game_paths_path_for_user or read_launch_paths" -v`
Expected: FAIL，因为共享 helper 和用户级启动路径状态模块都还不存在。

- [ ] **Step 4: 写最小共享 helper**

```python
# trail/daemon/paths.py
from pathlib import Path


def resolve_daemon_home() -> Path:
    return Path.home() / ".trail-daemon"


def game_paths_path_for_user(daemon_home: Path | None = None) -> Path:
    home = resolve_daemon_home() if daemon_home is None else Path(daemon_home)
    return home / "game-paths.json"
```

- [ ] **Step 5: 写最小启动路径状态模块**

```python
# trail/runtime/launch_paths.py
from __future__ import annotations

import json
from pathlib import Path

from trail.daemon.paths import game_paths_path_for_user, resolve_daemon_home


def read_launch_paths(path: Path | None = None) -> dict[str, dict[str, str]]:
    target = game_paths_path_for_user(resolve_daemon_home()) if path is None else Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_launch_path(channel: str, game_path: str, path: Path | None = None) -> None:
    target = game_paths_path_for_user(resolve_daemon_home()) if path is None else Path(path)
    data = read_launch_paths(target)
    data[channel] = {"last_success_game_path": game_path}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 6: 改现有 daemon 相关模块统一复用路径 helper**

```python
# before
from trail.daemon.bootstrap import resolve_daemon_home

# after
from trail.daemon.paths import resolve_daemon_home
```

- [ ] **Step 7: 跑绿灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/launch-path-green-shared tests/test_daemon_bootstrap.py tests/test_runtime_backends.py -k "game_paths_path_for_user or read_launch_paths" -v`
Expected: PASS，共享路径 helper 与损坏文件退化语义都被锁住。

### Task 2: 让 `window launch` 支持自动解析顺序与显式路径硬约束

**Files:**
- Modify: `trail/commands/window.py`
- Modify: `trail/runtime/window.py`
- Modify: `trail/daemon/runtime_service.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_runtime_backends.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 先写失败测试，冻结 CLI 上 `--game-path` 变为可选**

```python
def test_window_launch_allows_omitting_game_path(cli_runner, fake_daemon_client):
    client = fake_daemon_client({"window.launch": build_success_response(request_id="req-window-launch-1", data={"started": False, "already_running": True, "path": "demo.exe"})})

    result = cli_runner.invoke(app, ["window", "launch"])

    assert result.exit_code == 0
    assert client.calls[-1]["payload"] == {"channel": "official", "launch_args": [], "use_cmd": False}
```

- [ ] **Step 2: 先写失败测试，冻结显式路径硬约束**

```python
def test_launch_game_explicit_path_does_not_fallback_to_history_or_default(tmp_path, monkeypatch):
    history_file = tmp_path / "game-paths.json"
    write_launch_path("official", str(tmp_path / "history.exe"), history_file)

    with pytest.raises(TrailError, match="未找到游戏启动路径") as exc:
        launch_game(game_path=tmp_path / "missing.exe", channel="official")

    assert exc.value.code == "GAME_PATH_NOT_FOUND"


def test_launch_game_falls_back_to_default_when_history_path_start_fails(tmp_path, monkeypatch):
    history = tmp_path / "history.exe"
    history.write_text("", encoding="utf-8")
    default = tmp_path / "default.exe"
    default.write_text("", encoding="utf-8")
    monkeypatch.setattr("trail.runtime.window.DEFAULT_GAME_PATHS", {"official": default})
    monkeypatch.setattr("trail.runtime.window.read_launch_paths", lambda path=None: {"official": {"last_success_game_path": str(history)}})
    monkeypatch.setattr("trail.runtime.window.is_process_running", lambda process_name: False)

    def fake_change_game_config(path, *, channel, sub_channel):
        if Path(path) == history:
            raise TrailError("GAME_CHANNEL_INVALID", "history path launch failed")

    popen_calls = []
    monkeypatch.setattr("trail.runtime.window.change_game_config", fake_change_game_config)
    monkeypatch.setattr("trail.runtime.window.subprocess.Popen", lambda args, cwd=None: popen_calls.append((args, cwd)))

    payload = launch_game(game_path=None, channel="official")

    assert payload["path"] == str(default)
```

- [ ] **Step 3: 先写失败测试，冻结自动顺序为历史 -> 默认 -> GAME_PATH_REQUIRED**

```python
def test_launch_game_uses_history_before_default(tmp_path, monkeypatch):
    history = tmp_path / "history.exe"
    history.write_text("", encoding="utf-8")
    default = tmp_path / "default.exe"
    default.write_text("", encoding="utf-8")

    monkeypatch.setattr("trail.runtime.window.DEFAULT_GAME_PATHS", {"official": default})
    monkeypatch.setattr("trail.runtime.window.read_launch_paths", lambda path=None: {"official": {"last_success_game_path": str(history)}})
    monkeypatch.setattr("trail.runtime.window.is_process_running", lambda process_name: False)
    popen_calls = []
    monkeypatch.setattr("trail.runtime.window.subprocess.Popen", lambda args, cwd=None: popen_calls.append((args, cwd)))

    payload = launch_game(game_path=None, channel="official")

    assert payload["path"] == str(history)


def test_launch_game_falls_back_to_default_when_history_path_is_missing(tmp_path, monkeypatch):
    default = tmp_path / "default.exe"
    default.write_text("", encoding="utf-8")
    monkeypatch.setattr("trail.runtime.window.DEFAULT_GAME_PATHS", {"official": default})
    monkeypatch.setattr("trail.runtime.window.read_launch_paths", lambda path=None: {"official": {"last_success_game_path": str(tmp_path / 'missing.exe')}})
    monkeypatch.setattr("trail.runtime.window.is_process_running", lambda process_name: False)
    popen_calls = []
    monkeypatch.setattr("trail.runtime.window.subprocess.Popen", lambda args, cwd=None: popen_calls.append((args, cwd)))

    payload = launch_game(game_path=None, channel="official")

    assert payload["path"] == str(default)


def test_launch_game_global_without_history_raises_game_path_required(monkeypatch):
    monkeypatch.setattr("trail.runtime.window.read_launch_paths", lambda path=None: {})

    with pytest.raises(TrailError, match="请提供游戏路径") as exc:
        launch_game(game_path=None, channel="global")

    assert exc.value.code == "GAME_PATH_REQUIRED"


def test_launch_game_bilibili_without_history_raises_game_path_required(monkeypatch):
    monkeypatch.setattr("trail.runtime.window.read_launch_paths", lambda path=None: {})

    with pytest.raises(TrailError, match="请提供游戏路径") as exc:
        launch_game(game_path=None, channel="bilibili")

    assert exc.value.code == "GAME_PATH_REQUIRED"
```

- [ ] **Step 4: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/launch-path-red-runtime tests/test_atomic_commands.py tests/test_runtime_backends.py tests/test_daemon_protocol.py -k "window_launch_allows_omitting_game_path or explicit_path_does_not_fallback or uses_history_before_default or falls_back_to_default_when_history_path_is_missing or falls_back_to_default_when_history_path_start_fails or global_without_history_raises_game_path_required or bilibili_without_history_raises_game_path_required or GAME_PATH_REQUIRED" -v`
Expected: FAIL，因为当前 CLI 还强制 `--game-path`，runtime 也没有自动解析顺序。

- [ ] **Step 5: 最小改 CLI 入口**

```python
@window_app.command("launch")
def window_launch(
    game_path: Path | None = typer.Option(None, "--game-path", help="可选；未提供时按历史成功路径 -> 默认路径 -> 直接问用户解析"),
    channel: LaunchChannel = typer.Option(LaunchChannel.OFFICIAL, "--channel"),
    arg: list[str] | None = typer.Option(None, "--arg"),
    use_cmd: bool = typer.Option(False, "--use-cmd"),
) -> None:
    payload = {"channel": channel.value, "launch_args": list(arg or []), "use_cmd": use_cmd}
    if game_path is not None:
        payload["game_path"] = str(game_path)
    print_output("window.launch", call_daemon("window.launch", payload))
```

- [ ] **Step 6: 最小改 runtime 启动解析器**

```python
DEFAULT_GAME_PATHS = {
    "official": Path(r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe"),
}


def iter_launch_game_candidates(*, game_path: Path | None, channel: str):
    if game_path is not None:
        yield Path(game_path), "explicit"
        return

    raw_history = read_launch_paths().get(channel)
    history = raw_history.get("last_success_game_path") if isinstance(raw_history, dict) else None
    if history:
        history_path = Path(history)
        if history_path.exists():
            yield history_path, "history"

    default = DEFAULT_GAME_PATHS.get(channel)
    if default is not None and Path(default).exists():
        yield Path(default), "default"


def launch_game(*, game_path: Path | None = None, channel: str = "official", launch_args=None, use_cmd: bool = False):
    last_error = None
    for path, source in iter_launch_game_candidates(game_path=game_path, channel=channel):
        try:
            args = list(launch_args or [])
            if not path.exists():
                raise TrailError("GAME_PATH_NOT_FOUND", f"未找到游戏启动路径 {path}")
            if is_process_running("StarRail.exe"):
                return {"started": False, "already_running": True, "path": str(path), "channel": channel, "args": args}
            channel_config = GAME_CHANNEL_CONFIG.get(channel)
            if channel_config is not None:
                change_game_config(path, channel=channel_config[0], sub_channel=channel_config[1])
            if use_cmd:
                subprocess.Popen(["cmd", "/c", "start", "", str(path), *args], cwd=str(path.parent))
            else:
                subprocess.Popen([str(path)] + args, cwd=str(path.parent))
            return {"started": True, "already_running": False, "path": str(path), "channel": channel, "args": args}
        except TrailError as error:
            if source == "explicit":
                raise
            last_error = error
            continue
    if last_error is not None and game_path is None:
        raise TrailError("GAME_PATH_REQUIRED", "请提供游戏路径")
    raise TrailError("GAME_PATH_REQUIRED", "请提供游戏路径")


def launch_game(self, **payload):
    from trail.runtime.window import launch_game

    resolved = dict(payload)
    game_path = resolved.get("game_path")
    resolved["game_path"] = None if game_path is None else Path(game_path)
    return launch_game(**resolved)
```

- [ ] **Step 7: 跑绿灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/launch-path-green-runtime tests/test_atomic_commands.py tests/test_runtime_backends.py tests/test_daemon_protocol.py -k "window_launch_allows_omitting_game_path or explicit_path_does_not_fallback or uses_history_before_default or falls_back_to_default_when_history_path_is_missing or falls_back_to_default_when_history_path_start_fails or global_without_history_raises_game_path_required or bilibili_without_history_raises_game_path_required or GAME_PATH_REQUIRED" -v`
Expected: PASS，CLI 可省略 `--game-path`，显式路径仍是硬约束，自动路径顺序被锁住。

### Task 3: 成功回写、`already_running` 语义与 warning/failure 协议

**Files:**
- Modify: `trail/runtime/window.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_runtime_backends.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写失败测试，冻结成功回写与 `already_running=1` 不回写**

```python
def test_launch_game_writes_history_only_after_real_start(tmp_path, monkeypatch):
    executable = tmp_path / "StarRail.exe"
    executable.write_text("", encoding="utf-8")
    history_file = tmp_path / "game-paths.json"
    popen_calls = []

    monkeypatch.setattr("trail.runtime.window.write_launch_path", lambda channel, game_path, path=None: history_file.write_text(game_path, encoding="utf-8"))
    monkeypatch.setattr("trail.runtime.window.subprocess.Popen", lambda args, cwd=None: popen_calls.append((args, cwd)))
    monkeypatch.setattr("trail.runtime.window.is_process_running", lambda process_name: False)

    payload = launch_game(game_path=executable, channel="official")

    assert payload["started"] is True
    assert history_file.read_text(encoding="utf-8") == str(executable)


def test_launch_game_already_running_does_not_write_history(tmp_path, monkeypatch):
    executable = tmp_path / "StarRail.exe"
    executable.write_text("", encoding="utf-8")
    writes = []

    monkeypatch.setattr("trail.runtime.window.write_launch_path", lambda *args, **kwargs: writes.append((args, kwargs)))
    monkeypatch.setattr("trail.runtime.window.is_process_running", lambda process_name: True)

    payload = launch_game(game_path=executable, channel="official")

    assert payload["already_running"] is True
    assert writes == []
```

- [ ] **Step 2: 先写失败测试，冻结“启动成功但持久化失败”仍为 success + warn**

```python
def test_launch_game_success_with_persist_failure_returns_warning(tmp_path, monkeypatch):
    executable = tmp_path / "StarRail.exe"
    executable.write_text("", encoding="utf-8")

    monkeypatch.setattr("trail.runtime.window.is_process_running", lambda process_name: False)
    monkeypatch.setattr("trail.runtime.window.subprocess.Popen", lambda args, cwd=None: None)
    monkeypatch.setattr("trail.runtime.window.write_launch_path", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    payload = launch_game(game_path=executable, channel="official")

    assert payload["started"] is True
    assert payload["warnings"] == [{"code": "GAME_PATH_PERSIST_FAILED", "message": "disk full"}]


def test_command_service_promotes_window_launch_warnings_to_envelope(tmp_path):
    class RuntimeServiceStub:
        def launch_game(self, **payload):
            return {
                "started": True,
                "already_running": False,
                "path": "demo.exe",
                "warnings": [{"code": "GAME_PATH_PERSIST_FAILED", "message": "disk full"}],
            }

    service = CommandService(runtime_service=RuntimeServiceStub())

    payload = service.handle(_command_request(workspace_root=tmp_path, method="window.launch", payload={"channel": "official", "launch_args": [], "use_cmd": False}))

    assert payload["ok"] is True
    assert payload["warnings"] == [{"code": "GAME_PATH_PERSIST_FAILED", "message": "disk full"}]
```

- [ ] **Step 3: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/launch-path-red-persist tests/test_runtime_backends.py tests/test_daemon_protocol.py tests/test_output_rendering.py -k "write_history_only_after_real_start or already_running_does_not_write_history or GAME_PATH_PERSIST_FAILED or promotes_window_launch_warnings_to_envelope or GAME_PATH_REQUIRED" -v`
Expected: FAIL，因为当前 runtime 还没有历史回写与 persist-failure warning 语义。

- [ ] **Step 4: 最小实现成功回写与 warning 收口**

```python
def launch_game(...):
    last_error = None
    for path, source in iter_launch_game_candidates(game_path=game_path, channel=channel):
        warnings = []
        try:
            if is_process_running("StarRail.exe"):
                return {"started": False, "already_running": True, "path": str(path), "channel": channel, "args": args, "warnings": warnings}
            ...
            subprocess.Popen(...)
            try:
                write_launch_path(channel, str(path))
            except OSError as error:
                warnings.append({"code": "GAME_PATH_PERSIST_FAILED", "message": str(error)})
            return {"started": True, "already_running": False, "path": str(path), "channel": channel, "args": args, "warnings": warnings}
        except TrailError as error:
            if source == "explicit":
                raise
            last_error = error
            continue
    raise TrailError("GAME_PATH_REQUIRED", "请提供游戏路径") if game_path is None else last_error


def _capture_window_launch(self, request):
    warning_holder = {"warnings": []}

    def action():
        result = to_jsonable(self.runtime_service.launch_game(**request.payload))
        warning_holder["warnings"] = list(result.pop("warnings", []))
        return result

    envelope = self._capture_response(request, None, action)
    envelope["warnings"].extend(warning_holder["warnings"])
    return envelope


def handle(self, request):
    ...
    if request.method == "window.launch":
        return self._capture_window_launch(request)
```

- [ ] **Step 5: 跑绿灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/launch-path-green-persist tests/test_runtime_backends.py tests/test_daemon_protocol.py tests/test_output_rendering.py -k "write_history_only_after_real_start or already_running_does_not_write_history or GAME_PATH_PERSIST_FAILED or promotes_window_launch_warnings_to_envelope or GAME_PATH_REQUIRED" -v`
Expected: PASS，历史写回、`already_running` 与 `GAME_PATH_PERSIST_FAILED` 都被锁住。

### Task 4: README、SKILL 与协议回归

**Files:**
- Modify: `README.md`
- Modify: `skills/trail-hsr/SKILL.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写失败测试，锁住 README / help / 协议**

```python
def test_readme_documents_window_launch_path_resolution_contract():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "历史成功路径 -> 默认路径 -> 直接问用户" in readme
    assert "C:\\Program Files\\miHoYo Launcher\\games\\Star Rail Game\\StarRail.exe" in readme
    assert "GAME_PATH_REQUIRED" in readme
    assert "自动搜索常见目录" not in readme
    assert "trail window launch --game-path <StarRail.exe>" not in readme
    assert "注册表" not in readme
    assert "全盘搜索" not in readme


def test_skills_document_window_launch_path_resolution_contract():
    trail_hsr = Path("skills/trail-hsr/SKILL.md").read_text(encoding="utf-8")
    trail_cw = Path("skills/trail-cw/SKILL.md").read_text(encoding="utf-8")

    assert "历史成功路径 -> 默认路径 -> 直接问用户" in trail_hsr
    assert "历史成功路径 -> 默认路径 -> 直接问用户" in trail_cw
    assert "不要默认乱搜路径" in trail_hsr
    assert "不要默认乱搜路径" in trail_cw
    assert "trail window launch --game-path <StarRail.exe>" not in trail_hsr
    assert "trail window launch --game-path <StarRail.exe>" not in trail_cw
    assert "自动搜索常见目录" not in trail_hsr
    assert "自动搜索常见目录" not in trail_cw
    assert "注册表" not in trail_hsr
    assert "注册表" not in trail_cw
    assert "全盘搜索" not in trail_hsr
    assert "全盘搜索" not in trail_cw


def test_render_output_window_launch_game_path_required_omits_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-launch-path"},
        "error": {"code": "GAME_PATH_REQUIRED", "message": "请提供游戏路径"},
    }

    assert render_output("window.launch", payload).splitlines() == [
        "fail window.launch code=GAME_PATH_REQUIRED",
        "request id=req-launch-path",
        'why msg="请提供游戏路径"',
    ]


def test_render_output_window_launch_success_keeps_persist_failure_warning():
    payload = {
        "ok": True,
        "data": {"started": True, "already_running": False, "path": "demo.exe"},
        "screenshot": None,
        "timing": {},
        "warnings": [{"code": "GAME_PATH_PERSIST_FAILED", "message": "disk full"}],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("window.launch", payload).splitlines() == [
        "ok window.launch started=1 already_running=0 path=demo.exe",
        'warn code=GAME_PATH_PERSIST_FAILED msg="disk full"',
    ]


def test_window_launch_help_describes_auto_path_resolution(cli_runner):
    result = cli_runner.invoke(app, ["window", "launch", "--help"])

    assert result.exit_code == 0
    assert "历史成功路径" in result.output
    assert "默认路径" in result.output
    assert "--game-path" in result.output
```

- [ ] **Step 2: 跑红灯**

Run: `uv run pytest --basetemp .trail/pytest-tmp/launch-path-red-docs tests/test_atomic_commands.py tests/test_output_rendering.py -k "window_launch_path_resolution_contract or skills_document_window_launch_path_resolution_contract or GAME_PATH_REQUIRED or GAME_PATH_PERSIST_FAILED or window_launch_help_describes_auto_path_resolution" -v`
Expected: FAIL，因为 README/help/协议回归还未收口。

- [ ] **Step 3: 更新 README 与两个 SKILL**

```markdown
- `trail window launch` 现在在未显式传 `--game-path` 时，按“历史成功路径 -> 默认路径 -> 直接问用户”解析
- 当前冻结默认路径：`C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe`
- 当前默认路径仅覆盖 `official`；`bilibili` / `global` 无历史成功路径时通常仍需显式 `--game-path`
- 显式 `--game-path` 优先级最高，失败时不回退
- Agent 不应默认乱搜路径；收到 `GAME_PATH_REQUIRED` 后直接问用户
- 不要把“自动搜索常见目录/注册表/全盘搜索”写进 README 或 SKILL 的默认行为描述
```

- [ ] **Step 4: 跑综合回归**

Run: `uv run pytest --basetemp .trail/pytest-tmp/launch-path-final tests/test_daemon_bootstrap.py tests/test_runtime_backends.py tests/test_atomic_commands.py tests/test_daemon_protocol.py tests/test_output_rendering.py -v`
Expected: PASS，共享路径 helper、自动顺序、显式路径硬约束、成功回写、`GAME_PATH_REQUIRED`、`GAME_PATH_PERSIST_FAILED`、README/help/SKILL 合同都收口。

## Self-Review Checklist

- Spec coverage:
  - 历史成功路径 -> 默认路径 -> 直接问用户：Task 2 覆盖。
  - `~/.trail-daemon/game-paths.json` 与按 channel 分桶：Task 1 覆盖。
  - `GAME_PATH_REQUIRED` / `GAME_PATH_PERSIST_FAILED`：Task 2-4 覆盖。
  - `already_running=1` 不回写历史：Task 3 覆盖。
  - README / `skills/trail-hsr` / `skills/trail-cw` 同步：Task 4 覆盖。
- Placeholder scan: 无 `TBD` / `TODO` / 未定义 helper 占位。
- Type consistency: 统一使用 `GAME_PATH_REQUIRED`、`GAME_PATH_PERSIST_FAILED`、`game-paths.json`、`last_success_game_path` 这些名字，不再改名。

## Execution Mode

按用户当前要求，后续继续使用 **Subagent-Driven**：

- 使用项目内 worktree 执行
- 每个任务 fresh implementer 子代理
- 每个任务后做 spec review + code quality review
- review 通过后自动进入下一任务
