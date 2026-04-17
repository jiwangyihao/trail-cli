# Trail CLI 输出协议重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `trail-cli` 的默认输出从固定 JSON envelope 切到统一首行 + 命令族专门 body 的紧凑文本协议，并提供 `--format yaml` 与开发期 `--verbose` 支持。

**Architecture:** 保持 daemon、journal、capture、内部 envelope 等结构化中间态不动，只在 CLI 最终打印出口新增 `render_output(command, payload, output_format, verbose)` 渲染层。渲染层负责 canonical command 名、`key=value` 文本 grammar、命令族 renderer、YAML 兜底、恢复链路与 verbose/debug block。命令实现继续返回结构化 payload，但所有 `print_json(payload)` 出口统一改为 `print_output(command, payload)`。

**Tech Stack:** Python 3.12, Typer, PyYAML, pytest, golden-style stdout assertions, existing daemon/client/capture/session infrastructure

---

**Spec Source:** `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-17-trail-output-format-design.md`

**Execution Root:** `C:\Users\34404\source\repos\trail-cli\.worktrees\trail-output-format`

**Baseline Verification:** 在该 worktree 中运行 `uv run pytest --basetemp .pytest_tmp` 已通过（`356 passed`）。后续计划中的 pytest 命令都沿用 `--basetemp .pytest_tmp`，避免当前环境的全局临时目录权限问题干扰实现判断。

## 文件结构

### 新增文件

- `trail/output/rendering.py`
  - 默认文本协议、YAML 输出、renderer 注册表、canonical command/field 规则、`print_output` 总入口。
- `trail/output/debug.py`
  - 统一 verbose/debug helper，把 `debug.trace`、`debug.detail`、`debug.request_id` 归一化成稳定事件流。
- `tests/test_output_rendering.py`
- `render_output(command, payload, output_format, verbose)` 的纯单元测试，覆盖 grammar、顺序、request/recover、shot、YAML、must-keep 事实。
- `tests/test_output_debug.py`
  - verbose/debug helper 的单元测试，覆盖事件 schema、默认模式不泄漏、`--verbose` 输出稳定。
- `tests/test_cli_output_protocol.py`
  - 从 `tests/test_daemon_protocol.py` 拆出的 CLI stdout 协议测试，专门覆盖 `daemon.request_status`、`daemon.reconcile_session` 与 control-plane 恢复文本。
- `AGENTS.md`
  - 项目级贡献约束，固定新增命令必须声明 renderer 家族、默认事实、verbose 事件、YAML 适用性、README 示例和测试增量。

### 修改文件

- `pyproject.toml`
  - 显式添加 `pyyaml` 依赖，避免 YAML 输出依赖仅靠间接包提供。
- `trail/cli.py`
  - 增加全局 `--format` 选项，并把输出模式与 verbose 选项传入新的输出配置。
- `trail/commands/helpers.py`
- 把 `print_json` 演进为 `print_output(command, payload)`；保留 `call_daemon(method, payload, session_id, verbose, daemon_client)` 的路径归一化职责。
- `trail/output/capture.py`
  - 保持 envelope 采集链不变，但把 `debug.trace` 交给新 debug helper 归一化。
- `trail/daemon/client.py`
  - 保持 `request_id` 注入时机不变，但让输出层能稳定消费 `debug.request_id`。
- `trail/commands/window.py`
- `trail/commands/session.py`
- `trail/commands/state.py`
- `trail/commands/screen.py`
- `trail/commands/ocr.py`
- `trail/commands/input.py`
- `trail/commands/image.py`
- `trail/commands/guide.py`
- `trail/commands/daemon.py`
- `trail/commands/cw.py`
- 把所有 `print_json(payload)` 调用切到 `print_output(canonical_command, payload)`。
- `tests/test_atomic_commands.py`
  - 从 JSON envelope 断言改成默认文本协议断言，覆盖原子命令与基础截图/错误输出。
- `tests/test_guide_rpc_contracts.py`
  - 改成 `guide.fetch.cw` / `guide.list.cw` 文本输出断言。
- `tests/test_cw_rpc_contracts.py`
  - 改成 `cw.*` 命令的文本输出断言，覆盖 `stage/shop/list` 等 must-keep 字段。
- `tests/test_daemon_bootstrap.py`
  - `daemon install/start/status/stop/logs` 改成文本/YAML 输出断言。
- `tests/test_daemon_protocol.py`
  - 保留 transport/server/journal 的结构化协议断言，把 CLI stdout 部分迁出。
- `README.md`
  - 重写输出约定、恢复链路、`--format yaml`、`--verbose` 说明。

---

### Task 1: 建立输出渲染核心与 CLI 全局开关

**Files:**
- Create: `trail/output/rendering.py`
- Modify: `trail/cli.py`
- Modify: `trail/commands/helpers.py`
- Modify: `pyproject.toml`
- Test: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写渲染核心的失败测试**

```python
from trail.output.rendering import render_output


def test_render_output_renders_canonical_stage_text():
    payload = {
        "ok": True,
        "data": {"value": "shop", "stale": False},
        "screenshot": ".trail/shots/req-stage.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.stage.detect", payload).splitlines() == [
        "ok cw.stage.detect stage=shop stale=0",
        "shot path=.trail/shots/req-stage.png",
    ]


def test_render_output_renders_control_plane_recovery_path():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-42", "detail": "timeout"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "daemon unavailable"},
    }

    assert render_output("daemon.start", payload).splitlines() == [
        "fail daemon.start code=DAEMON_UNAVAILABLE",
        "request id=req-42",
        'why msg="daemon unavailable"',
    ]
```

- [ ] **Step 2: 跑红灯确认渲染层尚不存在**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_output_rendering.py -q`
Expected: FAIL，提示 `trail.output.rendering` 或 `render_output` 不存在

- [ ] **Step 3: 写最小渲染实现与全局输出配置**

`trail/output/rendering.py`

```python
from __future__ import annotations

from enum import StrEnum
import json
from typing import Any

import yaml


class OutputFormat(StrEnum):
    TEXT = "text"
    YAML = "yaml"


_OUTPUT_OPTIONS = {"format": OutputFormat.TEXT, "verbose": False}


def set_output_options(*, output_format: str, verbose: bool) -> None:
    _OUTPUT_OPTIONS["format"] = OutputFormat(output_format)
    _OUTPUT_OPTIONS["verbose"] = bool(verbose)


def print_output(command: str, payload: dict[str, Any]) -> None:
    print(render_output(command, payload, output_format=_OUTPUT_OPTIONS["format"], verbose=_OUTPUT_OPTIONS["verbose"]))
```

`trail/cli.py`

```python
@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", help="输出复杂操作的中间流程，便于开发期调试"),
    output_format: str = typer.Option("text", "--format", help="text 或 yaml"),
) -> None:
    set_capture_options(verbose=verbose)
    set_output_options(output_format=output_format, verbose=verbose)
```

`trail/commands/helpers.py`

```python
from trail.output.rendering import print_output
```

`pyproject.toml`

```toml
dependencies = [
  "typer>=0.12",
  "pillow>=10.0",
  "pyautogui>=0.9.54",
  "pygetwindow>=0.0.9",
  "pyscreeze>=1.0.1",
  "pywin32>=306",
  "rapidocr-onnxruntime>=1.3.24",
  "windows-capture>=2.0.0",
  "pyyaml>=6.0",
]
```

- [ ] **Step 4: 跑绿灯验证渲染入口与 CLI 全局格式开关成立**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_output_rendering.py -q`
Expected: PASS

- [ ] **Step 5: 检查当前变更边界**

Run: `git status --short`
Expected: 只出现 `pyproject.toml`、`trail/output/rendering.py`、`trail/cli.py`、`trail/commands/helpers.py` 与新增测试文件

### Task 2: 迁移原子命令与 control-plane 命令到文本协议

**Files:**
- Modify: `trail/commands/window.py`
- Modify: `trail/commands/session.py`
- Modify: `trail/commands/state.py`
- Modify: `trail/commands/screen.py`
- Modify: `trail/commands/ocr.py`
- Modify: `trail/commands/input.py`
- Modify: `trail/commands/image.py`
- Modify: `trail/commands/daemon.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_daemon_bootstrap.py`
- Modify: `tests/test_daemon_protocol.py`
- Create: `tests/test_cli_output_protocol.py`

- [ ] **Step 1: 先把代表性 CLI stdout 测试改成失败断言**

```python
def test_window_attach_renders_text_output(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "window.attach": build_success_response(
                request_id="req-window-attach",
                data={"title": "Demo Window", "hwnd": 123},
                screenshot=".trail/shots/req-window-attach.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["window", "attach", "--window-title", "Demo Window"])

    assert result.stdout.splitlines() == [
        'ok window.attach title="Demo Window" hwnd=123',
        "shot path=.trail/shots/req-window-attach.png",
    ]


def test_daemon_request_status_renders_recovery_state(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "daemon.request_status": build_success_response(
                request_id="req-daemon-request-status",
                data={
                    "request_id": "req-42",
                    "method": "input.click",
                    "final_state": "completed",
                    "last_visible_stage": "responded",
                    "tainted": False,
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["daemon", "request-status", "--request-id", "req-42"])

    assert result.stdout.splitlines() == [
        "ok daemon.request_status request=req-42 final_state=completed last_visible_stage=responded tainted=0",
    ]


def test_daemon_status_renders_summary_output(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-live")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    result = cli_runner.invoke(app, ["daemon", "status"])

    assert result.stdout.splitlines()[0].startswith("ok daemon.status state=ready pid=")
```

这一步不是只改上面三条示例测试；执行时要把 `tests/test_atomic_commands.py` 中所有“默认模式 CLI stdout” JSON 断言，以及 `tests/test_daemon_bootstrap.py` 里所有 control-plane CLI stdout 断言一并迁到文本协议。`test_top_level_verbose_emits_runtime_debug_trace` 和 `test_daemon_control_plane_errors_keep_request_id_in_debug` 这两条专门的 verbose/debug 测试保留到 Task 5 迁移。

- [ ] **Step 2: 跑红灯确认现有 `print_json(payload)` 仍在输出 JSON**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_atomic_commands.py::test_window_attach_renders_text_output tests/test_cli_output_protocol.py::test_daemon_request_status_renders_recovery_state tests/test_cli_output_protocol.py::test_daemon_reconcile_session_cli_output_protocol tests/test_daemon_bootstrap.py::test_daemon_status_renders_summary_output -q`
Expected: FAIL，stdout 仍是 JSON

- [ ] **Step 3: 把命令出口统一切到 `print_output(command, payload)`**

`trail/commands/window.py`

```python
from trail.commands.helpers import call_daemon
from trail.output.rendering import print_output


@window_app.command("attach")
def window_attach(window_title: str = typer.Option("崩坏：星穹铁道", "--window-title")) -> None:
    print_output("window.attach", call_daemon("window.attach", {"window_title": window_title}))
```

`trail/commands/daemon.py`

```python
print_output(
    "daemon.status",
    command_success(
        data={
            "install": asdict(manifest.install),
            "runtime": asdict(manifest.runtime),
        },
        screenshot=None,
    ),
)
print_output("daemon.request_status", call_daemon("daemon.request_status", {"request_id": request_id}))
print_output("daemon.reconcile_session", call_daemon("daemon.reconcile_session", {"session_id": session}))
```

`trail/output/rendering.py`

```python
TEXT_RENDERERS = {
    "window.attach": render_window_attach,
    "window.launch": render_window_launch,
    "session.create": render_session_create,
    "screen.shot": render_screen_shot,
    "ocr.read": render_ocr_read,
    "image.locate": render_image_locate,
    "image.wait": render_image_wait,
    "daemon.install": render_daemon_install,
    "daemon.start": render_daemon_start,
    "daemon.status": render_daemon_status,
    "daemon.stop": render_daemon_stop,
    "daemon.logs": render_daemon_logs,
    "daemon.request_status": render_daemon_request_status,
    "daemon.reconcile_session": render_daemon_reconcile_session,
}


def render_window_attach(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    lines = [f'ok {command} title="{data.get("title")}" hwnd={data.get("hwnd")}']
    if payload.get("screenshot"):
        lines.append(f"shot path={payload['screenshot']}")
    return lines


def render_window_launch(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} started={int(bool(data.get('started')))} already_running={int(bool(data.get('already_running')))} path={json.dumps(data.get('path'))}"]


def render_session_create(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    binding = data.get("window_binding") or {}
    return [f'ok {command} session={data.get("session_id")} title="{binding.get("title")}" hwnd={binding.get("hwnd")}']


def render_screen_shot(command: str, payload: dict[str, Any]) -> list[str]:
    lines = [f"ok {command} captured=1"]
    if payload.get("screenshot"):
        lines.append(f"shot path={payload['screenshot']}")
    return lines


def render_ocr_read(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    result = data.get("result") or []
    lines = [f"ok {command} hits={len(result)}"]
    for index, item in enumerate(result, start=1):
        lines.append(f'text rank={index} value={json.dumps(item.get("text"))}')
    return lines


def render_image_locate(command: str, payload: dict[str, Any]) -> list[str]:
    box = ((payload.get("data") or {}).get("box") or {})
    return [f"ok {command} box={box.get('left')},{box.get('top')},{box.get('width')},{box.get('height')}"]


def render_image_wait(command: str, payload: dict[str, Any]) -> list[str]:
    return render_image_locate(command, payload)


def render_daemon_request_status(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} request={data.get('request_id')} final_state={data.get('final_state')} last_visible_stage={data.get('last_visible_stage')} tainted={int(bool(data.get('tainted')))}"]


def render_daemon_reconcile_session(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} session={data.get('session_id')} tainted={int(bool(data.get('tainted')))}"]


def render_daemon_install(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} manifest_path={json.dumps(data.get('manifest_path'))}"]


def render_daemon_start(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} started={int(bool(data.get('started')))} already_running={int(bool(data.get('already_running')))}"]


def render_daemon_stop(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} stopped={int(bool(data.get('stopped')))}"]


def render_daemon_logs(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    return [f"ok {command} log_dir={json.dumps(data.get('log_dir'))}"]


def render_generic_text(command: str, payload: dict[str, Any]) -> list[str]:
    status = "ok" if payload.get("ok") else "fail"
    lines = [f"{status} {command}"]
    if payload.get("screenshot"):
        lines.append(f"shot path={payload['screenshot']}")
    if payload.get("error"):
        lines.append(f"why msg=\"{payload['error']['message']}\"")
    return lines


def render_output(command: str, payload: dict[str, Any], *, output_format: OutputFormat = OutputFormat.TEXT, verbose: bool = False) -> str:
    if output_format is OutputFormat.YAML:
        return render_yaml_output(command, payload, verbose=verbose)
    return render_text_output(command, payload, verbose=verbose)


def build_text_lines(command: str, payload: dict[str, Any]) -> list[str]:
    handler = TEXT_RENDERERS.get(command, render_generic_text)
    return handler(command, payload)


def render_text_output(command: str, payload: dict[str, Any], *, verbose: bool) -> str:
    lines = build_text_lines(command, payload)
    if verbose:
        lines.extend(render_debug_lines(payload.get("debug")))
    return "\n".join(lines)
```

- [ ] **Step 4: 跑绿灯覆盖原子命令和 control-plane 文本输出**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_atomic_commands.py -k "not test_top_level_verbose_emits_runtime_debug_trace and not test_daemon_control_plane_errors_keep_request_id_in_debug" tests/test_daemon_bootstrap.py tests/test_cli_output_protocol.py tests/test_daemon_protocol.py -q`
Expected: PASS

- [ ] **Step 5: 检查文本出口是否已从原子/daemon 命令收口**

Run: `git diff -- trail/commands/window.py trail/commands/session.py trail/commands/state.py trail/commands/screen.py trail/commands/ocr.py trail/commands/input.py trail/commands/image.py trail/commands/daemon.py tests/test_cli_output_protocol.py tests/test_daemon_protocol.py`
Expected: 这些命令文件都不再直接调用 `print_json(payload)`，且 `tests/test_daemon_protocol.py` 中的 CLI stdout 断言已迁到 `tests/test_cli_output_protocol.py`

### Task 3: 实现 `daemon.status` / `state.dump` / `guide.config.cw` 默认摘要与 YAML allowlist

**Files:**
- Modify: `trail/commands/daemon.py`
- Modify: `trail/commands/state.py`
- Modify: `trail/commands/guide.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_daemon_bootstrap.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_guide_rpc_contracts.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写 allowlist 命令和非 allowlist 命令的失败测试**

```python
def test_daemon_status_renders_summary_and_yaml(cli_runner, monkeypatch, tmp_path: Path):
    daemon_home = tmp_path / "daemon-home"
    write_ready_manifest(daemon_home, endpoint="127.0.0.1:8765", token_value="token-live")
    monkeypatch.setattr("trail.commands.daemon.resolve_daemon_home", lambda: daemon_home)

    text_result = cli_runner.invoke(app, ["daemon", "status"])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "daemon", "status"])

    assert text_result.stdout.splitlines()[0].startswith("ok daemon.status state=ready pid=")
    assert "runtime:" in yaml_result.stdout


def test_state_dump_renders_summary_before_yaml(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "state.dump": build_success_response(
                request_id="req-state-dump",
                data={
                    "session_id": "session-1",
                    "last_stage": {"scene": "cw", "value": "shop"},
                    "scene_state": {"daemon": {"tainted": False}, "cw": {"stage": {"value": "shop", "stale": False}}},
                },
            )
        }
    )

    text_result = cli_runner.invoke(app, ["state", "dump", "--session", "session-1"])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "state", "dump", "--session", "session-1"])

    assert text_result.stdout.splitlines()[0] == "ok state.dump session=session-1 scene=cw last_stage=shop tainted=0"
    assert "scene_state:" in yaml_result.stdout


def test_state_dump_surfaces_stage_error_summary(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "state.dump": build_success_response(
                request_id="req-state-dump-stale",
                data={
                    "session_id": "session-2",
                    "last_stage": None,
                    "scene_state": {"daemon": {"tainted": True}, "cw": {"stage": {"stale": True, "error": {"code": "STAGE_AMBIGUOUS"}}}},
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["state", "dump", "--session", "session-2"])

    assert result.stdout.splitlines()[0] == "ok state.dump session=session-2 scene=cw stage_stale=1 stage_error=STAGE_AMBIGUOUS tainted=1"


def test_guide_config_renders_summary_and_yaml(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "guide.config.cw": build_success_response(
                request_id="req-guide-config",
                data={
                    "meta": {"season_id": 12, "sub_season_id": 3, "big_version": "3.2"},
                    "lineup_levels": [{"id": 1}],
                    "traits": [{"id": 1001}, {"id": 1002}],
                    "roles": [{"id": 1}, {"id": 2}, {"id": 3}],
                    "role_tags": [{"id": 11}],
                },
            )
        }
    )

    text_result = cli_runner.invoke(app, ["guide", "config", "cw"])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "guide", "config", "cw"])

    assert text_result.stdout.splitlines()[0] == "ok guide.config.cw season=12 sub_season=3 big_version=3.2"
    assert "meta:" in yaml_result.stdout


def test_ocr_read_rejects_yaml_output(cli_runner, fake_daemon_client):
    fake_daemon_client({"ocr.read": build_success_response(request_id="req-ocr-read", data={"result": [{"text": "点击进入"}]})})

    result = cli_runner.invoke(app, ["--format", "yaml", "ocr", "read"])

    assert result.stdout.splitlines() == [
        "fail ocr.read code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for ocr.read"',
    ]
```

- [ ] **Step 2: 跑红灯确认 snapshot/config/allowlist 行为尚未落地**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_daemon_bootstrap.py::test_daemon_status_renders_summary_and_yaml tests/test_atomic_commands.py::test_state_dump_renders_summary_before_yaml tests/test_atomic_commands.py::test_state_dump_surfaces_stage_error_summary tests/test_guide_rpc_contracts.py::test_guide_config_renders_summary_and_yaml tests/test_output_rendering.py::test_ocr_read_rejects_yaml_output -q`
Expected: FAIL

- [ ] **Step 3: 在渲染层实现 YAML allowlist 和默认摘要冻结**

`trail/output/rendering.py`

```python
YAML_ALLOWLIST = {"daemon.status", "state.dump", "guide.config.cw"}


def render_failure_text(command: str, payload: dict[str, Any], *, verbose: bool) -> str:
    error = payload.get("error") or {}
    lines = [f"fail {command} code={error.get('code')}"]
    if payload.get("screenshot"):
        lines.append(f"shot path={payload['screenshot']}")
    lines.append(f'why msg="{error.get("message")}"')
    return "\n".join(lines)


def render_output(command: str, payload: dict[str, Any], *, output_format: OutputFormat = OutputFormat.TEXT, verbose: bool = False) -> str:
    if output_format is OutputFormat.YAML:
        if command not in YAML_ALLOWLIST:
            return render_failure_text(
                command,
                {
                    "ok": False,
                    "data": {},
                    "screenshot": payload.get("screenshot"),
                    "timing": {},
                    "warnings": [],
                    "references": [],
                    "debug": payload.get("debug"),
                    "error": {"code": "OUTPUT_FORMAT_NOT_SUPPORTED", "message": f"yaml not supported for {command}"},
                },
                verbose=verbose,
            )
        return render_yaml_output(command, payload, verbose=verbose)
    if payload.get("ok") is False:
        return render_failure_text(command, payload, verbose=verbose)
    return render_text_output(command, payload, verbose=verbose)
```

并为以下命令补专门摘要函数：

```python
def render_daemon_status(command: str, payload: dict[str, Any]) -> list[str]:
    runtime = (payload.get("data") or {}).get("runtime") or {}
    install = (payload.get("data") or {}).get("install") or {}
    summary = f"ok {command} state={runtime.get('state')} pid={runtime.get('pid')} endpoint={runtime.get('endpoint')} protocol={install.get('protocol_version')}"
    lines = [summary]
    if runtime.get("last_start_error"):
        lines.append(f'why msg="{runtime["last_start_error"]}"')
    return lines


def render_state_dump(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    tainted = int(bool((((data.get("scene_state") or {}).get("daemon") or {}).get("tainted", False))))
    scene_state = data.get("scene_state") or {}
    scene = next((name for name in scene_state.keys() if name != "daemon"), "unknown")
    stage_state = ((scene_state.get(scene) or {}).get("stage") or {})
    last_stage = (data.get("last_stage") or {}).get("value")
    summary = f"ok {command} session={data.get('session_id')} scene={scene}"
    if last_stage:
        summary += f" last_stage={last_stage}"
    if stage_state.get("stale") is True:
        summary += " stage_stale=1"
    if isinstance(stage_state.get("error"), dict) and stage_state["error"].get("code"):
        summary += f" stage_error={stage_state['error']['code']}"
    summary += f" tainted={tainted}"
    return [summary]


def render_guide_config(command: str, payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") or {}
    meta = data.get("meta") or {}
    return [
        f"ok {command} season={meta.get('season_id')} sub_season={meta.get('sub_season_id')} big_version={meta.get('big_version')}",
        f"info lineup_levels={len(data.get('lineup_levels') or [])} traits={len(data.get('traits') or [])} roles={len(data.get('roles') or [])} role_tags={len(data.get('role_tags') or [])}",
    ]
```

- [ ] **Step 4: 跑绿灯验证 allowlist 与默认摘要冻结**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_daemon_bootstrap.py tests/test_atomic_commands.py::test_state_dump_renders_summary_before_yaml tests/test_atomic_commands.py::test_state_dump_surfaces_stage_error_summary tests/test_guide_rpc_contracts.py tests/test_output_rendering.py::test_ocr_read_rejects_yaml_output -q`
Expected: PASS

- [ ] **Step 5: 检查 spec 中三类冻结命令是否全部进入计划覆盖**

Run: `git diff -- trail/commands/daemon.py trail/commands/state.py trail/commands/guide.py trail/output/rendering.py tests/test_daemon_bootstrap.py tests/test_atomic_commands.py tests/test_guide_rpc_contracts.py tests/test_output_rendering.py`
Expected: `daemon.status`、`state.dump`、`guide.config.cw` 三类命令都已有默认文本摘要断言、YAML allowlist 断言和实现变更

### Task 4: 实现 guide / cw 命令族 renderer 与 must-keep 字段

**Files:**
- Modify: `trail/commands/guide.py`
- Modify: `trail/commands/cw.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_guide_rpc_contracts.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 先写三类高价值失败测试**

```python
def test_cw_stage_detect_renders_stage_and_shot(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "cw.stage.detect": build_success_response(
                request_id="req-cw-stage-detect",
                data={"value": "preparation", "stale": False},
                screenshot=".trail/shots/req-cw-stage-detect.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "stage", "detect", "--session", SESSION_ID])

    assert result.stdout.splitlines() == [
        "ok cw.stage.detect stage=preparation stale=0",
        "shot path=.trail/shots/req-cw-stage-detect.png",
    ]


def test_cw_shop_status_renders_items_in_slot_order(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "cw.shop.status": build_success_response(
                request_id="req-cw-shop-status",
                data={"items": [{"slot": 1, "name": "希儿"}, {"slot": 2, "name": "停云"}]},
                screenshot=".trail/shots/req-cw-shop-status.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", SESSION_ID])

    assert result.stdout.splitlines() == [
        "ok cw.shop.status count=2",
        "shot path=.trail/shots/req-cw-shop-status.png",
        "item idx=1 slot=1 name=希儿",
        "item idx=2 slot=2 name=停云",
    ]


def test_guide_list_renders_paging_and_facts(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "guide.list.cw": build_success_response(
                request_id="req-guide-list",
                data={
                    "list": [{"lineup_id": "abc", "has_change_equip": False, "has_expert": True, "support_hard": True}],
                    "next_page_token": "next-token",
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["guide", "list", "cw"])

    assert result.stdout.splitlines() == [
        "ok guide.list.cw count=1 more=1 next=next-token",
        "guide id=abc idx=1 hard=1 change_equip=0 expert=1",
    ]
```

- [ ] **Step 2: 跑红灯确认高信息命令仍是 JSON 输出**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_cw_rpc_contracts.py::test_cw_stage_detect_renders_stage_and_shot tests/test_cw_rpc_contracts.py::test_cw_shop_status_renders_items_in_slot_order tests/test_guide_rpc_contracts.py::test_guide_list_renders_paging_and_facts -q`
Expected: FAIL

- [ ] **Step 3: 实现 guide / cw 专门 renderer**

`trail/output/rendering.py`

```python
def render_text_output(command: str, payload: dict[str, Any], *, verbose: bool) -> str:
    handlers = {
        "cw.stage.detect": render_cw_stage,
        "cw.stage.wait": render_cw_stage,
        "cw.shop.status": render_cw_shop_status,
        "guide.list.cw": render_guide_list,
        "ocr.read": render_ocr_read,
    }
    handler = handlers.get(command, render_generic_text)
    return "\n".join(handler(command, payload, verbose=verbose))


def render_guide_list(command: str, payload: dict[str, Any], *, verbose: bool) -> list[str]:
    data = payload["data"]
    items = data.get("list") or []
    summary = f"ok {command} count={len(items)} more={int(bool(data.get('next_page_token')))}"
    if data.get("next_page_token"):
        summary += f" next={data['next_page_token']}"
    lines = [summary]
    if payload.get("screenshot"):
        lines.append(f"shot path={payload['screenshot']}")
    for index, item in enumerate(items, start=1):
        guide_line = f"guide id={item.get('lineup_id')} idx={index} hard={int(bool(item.get('support_hard')))} change_equip={int(bool(item.get('has_change_equip')))} expert={int(bool(item.get('has_expert')))}"
        carry_roles = item.get("carry_roles") or []
        if carry_roles:
            guide_line += f" carry={carry_roles[0]}"
        lines.append(guide_line)
    return lines
```

`trail/commands/cw.py`

```python
print_output("cw.stage.detect", _rpc_cw("cw.stage.detect", session_id=session))
print_output("cw.shop.status", _rpc_cw("cw.shop.status", session_id=session))
print_output("cw.shop.buy_slot", _rpc_cw("cw.shop.buy_slot", session_id=session, payload={"slot": slot, "expect": expect}))
```

- [ ] **Step 4: 跑绿灯覆盖 must-keep 字段、分页和顺序**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py -q`
Expected: PASS

- [ ] **Step 5: 补一轮渲染单元测试，锁死排序与字段名**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_output_rendering.py -q`
Expected: PASS，新增断言已覆盖 `guide.list.cw`、`cw.shop.status`、`ocr.read` 的排序与字段冻结

### Task 5: 实现 `--verbose` 调试 helper 与内部契约回归保护

**Files:**
- Create: `trail/output/debug.py`
- Modify: `trail/output/rendering.py`
- Modify: `trail/output/capture.py`
- Modify: `trail/daemon/client.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_output_envelope.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_output_debug.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写 verbose/debug 与内部契约的失败测试**

```python
def test_verbose_output_keeps_extra_debug_fields():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-42",
            "trace": [{"step": "click", "point": [10, 20]}],
            "detail": "backend missing",
            "last_known_stage": "handler_completed",
            "stage_detail": "state persisted marker failed",
            "session_id": "session-1",
            "pid": 4321,
        },
        "error": {"code": "INPUT_BACKEND_MISSING", "message": "input backend missing"},
    }

    output = render_output("input.click", payload, verbose=True)

    assert "debug kind=request msg=req-42" in output
    assert "debug kind=trace step=click" in output
    assert "debug kind=context key=last_known_stage value=handler_completed" in output
    assert "debug kind=context key=stage_detail value=state persisted marker failed" in output
    assert "debug kind=context key=session_id value=session-1" in output
    assert "debug kind=context key=pid value=4321" in output


def test_render_output_preserves_daemon_unknown_result_contract():
    registry = SessionServiceRegistry()
    service = registry.for_workspace("C:/repo")
    accepted = service.begin_mutation(session_id="session-1", request_id="req-unknown", command_name="cw.shop.buy_slot")
    assert accepted["status"] == "accepted"
```

- [ ] **Step 2: 跑红灯确认 verbose helper 与内部契约保护尚未落地**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_output_debug.py::test_verbose_output_keeps_extra_debug_fields tests/test_atomic_commands.py::test_top_level_verbose_emits_runtime_debug_trace tests/test_atomic_commands.py::test_daemon_control_plane_errors_keep_request_id_in_debug tests/test_output_envelope.py tests/test_daemon_protocol.py -q`
Expected: FAIL

- [ ] **Step 3: 实现不会丢字段的 debug helper，并保持内部 envelope 契约不变**

`trail/output/debug.py`

```python
from __future__ import annotations

from typing import Any


def collect_debug_events(debug: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(debug, dict):
        return []

    events: list[dict[str, Any]] = []
    if debug.get("request_id"):
        events.append({"kind": "request", "msg": debug["request_id"]})
    for trace in debug.get("trace") or []:
        events.append({"kind": "trace", "step": trace.get("step", "unknown"), "context": trace})
    if debug.get("detail"):
        events.append({"kind": "detail", "msg": str(debug["detail"])})
    for key, value in debug.items():
        if key in {"request_id", "trace", "detail"}:
            continue
        events.append({"kind": "context", "key": key, "value": value})
    return events
```

`trail/output/rendering.py`

```python
def render_debug_lines(debug: dict[str, Any] | None) -> list[str]:
    lines: list[str] = []
    for event in collect_debug_events(debug):
        if event["kind"] == "request":
            lines.append(f"debug kind=request msg={event['msg']}")
        elif event["kind"] == "detail":
            lines.append(f'debug kind=detail msg="{event["msg"]}"')
        elif event["kind"] == "trace":
            lines.append(f"debug kind=trace step={event['step']}")
        else:
            lines.append(f"debug kind=context key={event['key']} value={event['value']}")
    return lines


def render_yaml_output(command: str, payload: dict[str, Any], *, verbose: bool) -> str:
    lines = render_failure_text(command, payload, verbose=False).splitlines() if payload.get("ok") is False else build_text_lines(command, payload)
    lines.append(yaml.safe_dump(payload.get("data") or {}, allow_unicode=True, sort_keys=False).rstrip())
    if verbose:
        lines.extend(render_debug_lines(payload.get("debug")))
    return "\n".join(lines)
```

`trail/output/capture.py`

```python
if verbose and callable(consume_debug_trace):
    trace = consume_debug_trace() or []
    debug = {"trace": trace}
```

保持 `capture.py` 的事实来源不变，只在输出层统一消费。

- [ ] **Step 4: 跑绿灯验证 verbose 输出和内部 envelope/journal 契约都未回归**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_output_debug.py tests/test_atomic_commands.py::test_top_level_verbose_emits_runtime_debug_trace tests/test_atomic_commands.py::test_daemon_control_plane_errors_keep_request_id_in_debug tests/test_output_envelope.py tests/test_daemon_protocol.py -q`
Expected: PASS

- [ ] **Step 5: 用 end-to-end 命令验证 verbose 追加层不会吞掉控制面字段**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_atomic_commands.py::test_top_level_verbose_emits_runtime_debug_trace tests/test_atomic_commands.py::test_daemon_control_plane_errors_keep_request_id_in_debug -q`
Expected: PASS，verbose 输出已包含 request/trace/detail/context 事件，且不影响默认文本模式

### Task 6: 锁死 must-keep 回归面与结果未知恢复协议

**Files:**
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_guide_rpc_contracts.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写 must-keep 与 tainted 恢复协议的失败测试**

```python
@pytest.mark.parametrize(
    ("command", "payload", "expected_tokens"),
    [
        (
            "cw.stage.detect",
            {"ok": True, "data": {"value": "shop", "stale": False}, "screenshot": ".trail/shots/req-stage.png", "timing": {}, "warnings": [], "references": [], "debug": None, "error": None},
            ["stage=shop", "stale=0", "shot path=.trail/shots/req-stage.png"],
        ),
        (
            "guide.list.cw",
            {"ok": True, "data": {"list": [{"lineup_id": "abc", "has_change_equip": False, "has_expert": True, "support_hard": True}], "next_page_token": "next-token"}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None},
            ["count=1", "more=1", "next=next-token", "change_equip=0", "expert=1"],
        ),
        (
            "ocr.read",
            {"ok": True, "data": {"result": [{"text": "点击进入", "score": 0.98, "box": {"left": 122, "top": 88, "width": 74, "height": 20}}]}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None},
            ["hits=1", "rank=1", "score=0.98", "box=122,88,74,20"],
        ),
    ],
)
def test_render_output_preserves_must_keep_facts(command, payload, expected_tokens):
    output = render_output(command, payload)
    for token in expected_tokens:
        assert token in output


def test_render_output_preserves_result_unknown_recovery_contract():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-unknown", "tainted": True, "last_known_stage": "side_effect_applied"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "daemon unavailable"},
    }

    output = render_output("cw.shop.buy_slot", payload)

    assert output.splitlines() == [
        "fail cw.shop.buy_slot code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-unknown",
        "shot path=.trail/shots/req-unknown.png",
        'why msg="daemon unavailable"',
        "recover action=daemon.request_status request=req-unknown",
    ]
```

- [ ] **Step 2: 跑红灯确认 must-keep 与 tainted 恢复协议尚未完全锁死**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_output_rendering.py::test_render_output_preserves_must_keep_facts tests/test_output_rendering.py::test_render_output_preserves_result_unknown_recovery_contract -q`
Expected: FAIL

- [ ] **Step 3: 扩展已有 `render_failure_text(...)`，把 must-keep 矩阵与 tainted 首行规则落到 renderer 与 CLI 测试**

`trail/output/rendering.py`

```python
def render_failure_text(command: str, payload: dict[str, Any], *, verbose: bool) -> str:
    error = payload.get("error") or {}
    debug = payload.get("debug") or {}
    tainted = int(bool(debug.get("tainted") or (payload.get("data") or {}).get("tainted")))
    lines = [f"fail {command} code={error.get('code')}" + (" tainted=1" if tainted else "")]
    if debug.get("request_id"):
        lines.append(f"request id={debug['request_id']}")
    if payload.get("screenshot"):
        lines.append(f"shot path={payload['screenshot']}")
    lines.append(f'why msg="{error.get("message")}"')
    if debug.get("request_id") and debug.get("last_known_stage"):
        lines.append(f"recover action=daemon.request_status request={debug['request_id']}")
    return "\n".join(lines)
```

`tests/test_output_rendering.py`

```python
def test_render_output_preserves_result_unknown_recovery_contract():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-unknown", "tainted": True, "last_known_stage": "side_effect_applied"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "daemon unavailable"},
    }

    assert render_output("cw.shop.buy_slot", payload).splitlines()[0] == "fail cw.shop.buy_slot code=DAEMON_UNAVAILABLE tainted=1"
    assert "request id=req-unknown" in render_output("cw.shop.buy_slot", payload)
    assert "recover action=daemon.request_status request=req-unknown" in render_output("cw.shop.buy_slot", payload)
```

- [ ] **Step 4: 跑绿灯覆盖文本渲染、must-keep 字段和结果未知恢复协议**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_atomic_commands.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_cli_output_protocol.py -q`
Expected: PASS

- [ ] **Step 5: 检查 tainted 恢复契约与 must-keep 字段是否都已有显式断言**

Run: `git diff -- tests/test_atomic_commands.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_cli_output_protocol.py`
Expected: `tainted=1`、`request id=<id>`、`recover action=daemon.request_status ...` 与第一批 must-keep 字段都已进入显式断言

### Task 7: 更新 README、项目级 AGENTS 与最终验证

**Files:**
- Modify: `README.md`
- Create: `AGENTS.md`
- Test: `tests/test_output_rendering.py`
- Test: `tests/test_output_debug.py`
- Test: `tests/test_atomic_commands.py`
- Test: `tests/test_guide_rpc_contracts.py`
- Test: `tests/test_cw_rpc_contracts.py`
- Test: `tests/test_daemon_bootstrap.py`
- Test: `tests/test_cli_output_protocol.py`

- [ ] **Step 1: 先写 README/AGENTS 对应的失败检查**

```python
def test_readme_mentions_text_output_protocol():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "默认输出已改为紧凑文本协议" in readme
    assert "--format yaml" in readme
    assert "trail daemon request-status --request-id" in readme


def test_project_agents_declares_renderer_contracts():
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "renderer 家族" in agents
    assert "默认模式必出事实" in agents
    assert "verbose 事件" in agents
```

- [ ] **Step 2: 跑红灯确认文档尚未同步**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_output_rendering.py::test_readme_mentions_text_output_protocol tests/test_output_debug.py::test_project_agents_declares_renderer_contracts -q`
Expected: FAIL

- [ ] **Step 3: 更新 README 与新增项目级 `AGENTS.md`**

`README.md`

```markdown
## 输出约定

- 默认输出是紧凑文本协议：`<ok|fail> <command> <核心事实...>`
- 只要当前命令有截图，就输出 `shot path=.trail/shots/req-demo.png`
- 结果未知或当前失败显式提供恢复链路时，默认输出会包含：
  - `request id=<request_id>`
  - `recover action=daemon.request_status request=<request_id>`（仅在当前失败可恢复时出现）
- `--format yaml` 提供近乎无损的结构化视图
- `--verbose` 仅用于开发/排障
```

`AGENTS.md`

```markdown
# 项目输出协议约束

1. 新命令必须声明所属 renderer 家族。
2. 新命令必须声明默认模式必出事实。
3. 新命令的 debug 只能通过统一 verbose helper 写入。
4. 只有 allowlist 命令允许 `--format yaml`。
5. 新命令必须补 README 示例与测试增量。
```

- [ ] **Step 4: 运行最终验收集**

Run: `uv run pytest --basetemp .pytest_tmp tests/test_output_rendering.py tests/test_output_debug.py tests/test_atomic_commands.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_daemon_bootstrap.py tests/test_cli_output_protocol.py -q`
Expected: PASS

- [ ] **Step 5: 运行全量回归**

Run: `uv run pytest --basetemp .pytest_tmp`
Expected: PASS（`356` 级别用例仍全部通过，或因新增测试而增加，但不允许回归）
