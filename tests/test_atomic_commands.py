from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import typer

from trail.cli import app
from trail.commands.cw import (
    CW_APP_HELP,
    cw_app,
    cw_enter,
    cw_equipment_compose,
    cw_guide_app,
    cw_guide_apply,
    cw_portal_detect,
    cw_portal_select,
    cw_slots_read,
    cw_start,
    cw_strategy_refresh,
    equipment_app,
    portal_app,
    slots_app,
    strategy_app,
)
from trail.commands.guide import guide_fetch, guide_list
from trail.commands.ocr import ocr_read
from trail.commands.start import start_app
from trail.commands.window import window_app, window_launch
from trail.core.errors import TrailError
from trail.daemon.client import TrailDaemonClient
from trail.runtime.ocr_config import OCR_LANG_UNSUPPORTED
from trail.runtime.model import Box
from tests.support.fake_daemon import build_success_response, write_ready_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _expected_success_lines(summary: str, *, screenshot: str | None = None, body: list[str] | None = None) -> list[str]:
    lines = [summary]
    if screenshot:
        lines.append(f"shot path={screenshot}")
        lines.append("info read_image_first=1")
    if body:
        lines.extend(body)
    return lines


CW_HELP_COMMANDS = {
    "enter",
    "start",
    "guide",
    "portal",
    "strategy",
    "stage",
    "slots",
    "shop",
    "crystals",
    "hand",
    "replenish",
    "invest",
    "encounter",
    "fortune",
    "boss-preview",
    "battle",
    "settle",
    "event",
}


def _build_success_response_with_guidance(*, request_id: str, data: dict, screenshot: str | None = None) -> dict:
    response = build_success_response(request_id=request_id, data=data, screenshot=screenshot)
    if screenshot is not None:
        response["image_guidance"] = {"read_image_first": 1}
    return response


def _normalize_help(output: str) -> str:
    return " ".join(output.split())


def _maybe_import_start_module():
    try:
        return importlib.import_module("trail.commands.start")
    except ModuleNotFoundError:
        return None


def _extract_help_option_block(output: str, option_name: str) -> str:
    lines = output.splitlines()
    block: list[str] = []
    capture = False
    in_options = False

    for raw_line in lines:
        line = raw_line.strip(" │")
        if "Options" in line:
            in_options = True
            continue
        if not line:
            if capture and block:
                break
            continue

        if not in_options:
            continue

        if option_name in line:
            capture = True
            block.append(line)
            continue

        if capture:
            if line.startswith("--"):
                break
            block.append(line)

    return " ".join(block)


def _extract_help_command_block(output: str, command_name: str) -> str:
    lines = output.splitlines()
    block: list[str] = []
    capture = False
    in_commands = False

    for raw_line in lines:
        line = raw_line.strip(" │")
        if "Commands" in line:
            in_commands = True
            continue
        if not line:
            if capture and block:
                break
            continue

        if not in_commands:
            continue

        token = line.split()[0]
        if token == command_name:
            capture = True
            block.append(line)
            continue

        if capture:
            if token in CW_HELP_COMMANDS:
                break
            block.append(line)

    return _normalize_help(" ".join(block))


def _registered_command_names(typer_app) -> set[str]:
    return {command.name for command in typer_app.registered_commands}


def _registered_command_help(typer_app, command_name: str) -> str:
    for command in typer_app.registered_commands:
        if command.name == command_name:
            return command.help or command.callback.__doc__ or ""
    raise AssertionError(f"missing command: {command_name}")


def _registered_group_help(typer_app) -> dict[str, str]:
    return {group.name: group.typer_instance.info.help or "" for group in typer_app.registered_groups}


def _registered_group_names(typer_app) -> set[str]:
    return {group.name for group in typer_app.registered_groups}


def _callback_option_decls(typer_app) -> set[str]:
    signature = inspect.signature(typer_app.registered_callback.callback)
    return {
        option_decl
        for parameter in signature.parameters.values()
        for option_decl in getattr(parameter.default, "param_decls", ())
    }


def _function_option_decls(callback) -> set[str]:
    signature = inspect.signature(callback)
    return {
        option_decl
        for parameter in signature.parameters.values()
        for option_decl in getattr(parameter.default, "param_decls", ())
    }


def _function_option_help(callback, option_decl: str) -> str:
    signature = inspect.signature(callback)
    for parameter in signature.parameters.values():
        default = parameter.default
        if option_decl in getattr(default, "param_decls", ()):
            return default.help or ""
    raise AssertionError(f"missing option: {option_decl}")


def _call_ocr_read_direct(**overrides) -> None:
    kwargs = {
        "provider": None,
        "lang": None,
        "use_cls": None,
        "text_score": None,
        "ocr_mode": None,
        "retry_high": None,
    }
    kwargs.update(overrides)
    ocr_read(**kwargs)


@pytest.fixture(autouse=True)
def _clear_ocr_env(monkeypatch):
    for name in (
        "TRAIL_OCR_PROVIDER",
        "TRAIL_OCR_LANG",
        "TRAIL_OCR_USE_CLS",
        "TRAIL_OCR_TEXT_SCORE",
        "TRAIL_OCR_MODE",
        "TRAIL_OCR_RETRY_HIGH",
    ):
        monkeypatch.delenv(name, raising=False)


def _install_real_daemon_client(monkeypatch, tmp_path, transport) -> TrailDaemonClient:
    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=transport,
    )

    import trail.commands.helpers as helpers

    monkeypatch.setattr(helpers, "build_default_daemon_client", lambda: client)
    return client


def test_window_attach_renders_text_output(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "window.attach": _build_success_response_with_guidance(
                request_id="req-window-attach",
                data={"title": "Demo Window", "hwnd": 123},
                screenshot=".trail/shots/req-window-attach.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["window", "attach", "--window-title", "Demo Window"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_success_lines(
        'ok window.attach title="Demo Window" hwnd=123',
        screenshot=".trail/shots/req-window-attach.png",
    )
    assert client.calls == [
        {
            "method": "window.attach",
            "payload": {"window_title": "Demo Window"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_session_create_uses_daemon_client(cli_runner, fake_daemon_client, tmp_path, monkeypatch):
    def fail_local_session_create(*args, **kwargs):
        raise AssertionError("local session create path used")

    monkeypatch.setattr("trail.commands.session.attach_window", fail_local_session_create, raising=False)
    client = fake_daemon_client(
        {
            "session.create": build_success_response(
                request_id="req-session-create",
                data={
                    "session_id": "session-1",
                    "window_binding": {"title": "Demo Window", "hwnd": 321},
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["session", "create", "--window-title", "Demo Window"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        'ok session.create session=session-1 title="Demo Window" hwnd=321'
    ]
    assert client.calls == [
        {
            "method": "session.create",
            "payload": {"window_title": "Demo Window"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_trail_start_help_describes_simple_entry():
    help_text = start_app.info.help or ""
    option_decls = _callback_option_decls(start_app)

    assert "自动完成 daemon、游戏、窗口、session 的启动收口" in help_text
    assert "start.run" in help_text
    assert "--window-title" in option_decls
    assert "--game-path" in option_decls
    assert "--channel" in option_decls
    assert "trail daemon status" not in help_text
    assert "trail window attach" not in help_text
    assert "trail session create" not in help_text


def test_trail_start_dispatches_single_start_run_after_local_ready(
    cli_runner, fake_daemon_client, tmp_path, monkeypatch
):
    daemon_home = Path("C:/Users/demo/.trail-daemon")
    installed: list[bool] = []
    ready: list[Path] = []
    start_module = _maybe_import_start_module()
    if start_module is not None:
        monkeypatch.setattr(start_module, "_ensure_local_daemon_installed", lambda: installed.append(True) or daemon_home)
        monkeypatch.setattr(start_module, "_ensure_local_daemon_ready", lambda resolved_home: ready.append(resolved_home))
    client = fake_daemon_client(
        {
            "start.run": _build_success_response_with_guidance(
                request_id="req-start-dispatch",
                data={
                    "status": "attached",
                    "session": "sess-start-1",
                    "reused": 0,
                    "title": "Demo Window",
                    "hwnd": 321,
                },
                screenshot=".trail/shots/req-start-dispatch.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "start",
            "--window-title",
            "Demo Window",
            "--game-path",
            str(tmp_path / "StarRail.exe"),
            "--channel",
            "bilibili",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        'ok start.run status=attached session=sess-start-1 reused=0 title="Demo Window" hwnd=321',
        "shot path=.trail/shots/req-start-dispatch.png",
        "info read_image_first=1",
    ]
    assert installed == [True]
    assert ready == [daemon_home]
    assert client.calls == [
        {
            "method": "start.run",
            "payload": {
                "window_title": "Demo Window",
                "game_path": str(tmp_path / "StarRail.exe"),
                "channel": "bilibili",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_trail_start_local_install_failure_renders_fail_start_run(cli_runner, monkeypatch):
    start_module = _maybe_import_start_module()
    if start_module is not None:
        monkeypatch.setattr(
            start_module,
            "_ensure_local_daemon_installed",
            lambda: (_ for _ in ()).throw(TrailError("DAEMON_INSTALL_FAILED", "daemon install failed")),
        )

    result = cli_runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail start.run code=DAEMON_INSTALL_FAILED",
        'why msg="daemon install failed"',
    ]


def test_trail_start_local_ready_failure_renders_fail_start_run(cli_runner, monkeypatch):
    start_module = _maybe_import_start_module()
    if start_module is not None:
        monkeypatch.setattr(start_module, "_ensure_local_daemon_installed", lambda: Path("C:/Users/demo/.trail-daemon"))
        monkeypatch.setattr(
            start_module,
            "_ensure_local_daemon_ready",
            lambda daemon_home: (_ for _ in ()).throw(TrailError("DAEMON_UNAVAILABLE", "daemon did not become ready in time")),
        )

    result = cli_runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail start.run code=DAEMON_UNAVAILABLE",
        'why msg="daemon did not become ready in time"',
    ]


def test_window_launch_returns_structured_payload(cli_runner, fake_daemon_client, tmp_path):
    executable = tmp_path / "StarRail.exe"
    client = fake_daemon_client(
        {
            "window.launch": build_success_response(
                request_id="req-window-launch",
                data={
                    "started": True,
                    "already_running": False,
                    "path": str(executable),
                    "channel": "bilibili",
                    "args": ["-popupwindow"],
                },
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "window",
            "launch",
            "--game-path",
            str(executable),
            "--channel",
            "bilibili",
            "--arg",
            "-popupwindow",
            "--use-cmd",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        f"ok window.launch started=1 already_running=0 path={json.dumps(str(executable), ensure_ascii=False)}"
    ]
    assert client.calls == [
        {
            "method": "window.launch",
            "payload": {
                "game_path": str(executable),
                "channel": "bilibili",
                "launch_args": ["-popupwindow"],
                "use_cmd": True,
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_window_launch_allows_omitted_game_path_and_preserves_channel_payload(cli_runner, fake_daemon_client, tmp_path):
    resolved = tmp_path / "resolved.exe"
    client = fake_daemon_client(
        {
            "window.launch": build_success_response(
                request_id="req-window-launch-omitted",
                data={
                    "started": True,
                    "already_running": False,
                    "path": str(resolved),
                    "channel": "bilibili",
                    "args": ["-popupwindow"],
                },
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "window",
            "launch",
            "--channel",
            "bilibili",
            "--arg",
            "-popupwindow",
            "--use-cmd",
        ],
    )

    assert result.exit_code == 0
    assert client.calls == [
        {
            "method": "window.launch",
            "payload": {
                "channel": "bilibili",
                "launch_args": ["-popupwindow"],
                "use_cmd": True,
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_window_launch_help_describes_game_path_option_resolution_contract():
    game_path_help = _function_option_help(window_launch, "--game-path")
    command_help = window_launch.__doc__ or ""

    assert "历史成功路径 -> 默认路径 -> 直接问用户" in game_path_help
    assert "默认路径仅覆盖 official" in game_path_help
    assert "GAME_PATH_NOT_FOUND" in command_help
    assert "GAME_LAUNCH_FAILED" in command_help
    assert "GAME_PATH_PERSIST_FAILED" in command_help


def test_guide_list_help_mentions_trait_and_role_filters():
    trait_help = _function_option_help(guide_list, "--trait")
    role_help = _function_option_help(guide_list, "--role")
    portal_help = _function_option_help(guide_list, "--portal")
    portal_id_help = _function_option_help(guide_list, "--portal-id")

    assert "按羁绊名称筛选" in trait_help
    assert "免查 config" in trait_help
    assert "按角色名称筛选" in role_help
    assert "按投资环境筛选" in portal_help
    assert "按投资环境筛选" in portal_id_help


def test_guide_list_help_mentions_repeatable_role_values():
    role_help = _function_option_help(guide_list, "--role")
    role_id_help = _function_option_help(guide_list, "--role-id")

    assert "可重复传入多个值" in role_help
    assert "可重复传入多个值" in role_id_help


def test_guide_list_help_mentions_exact_id_filters():
    trait_id_help = _function_option_help(guide_list, "--trait-id")
    role_id_help = _function_option_help(guide_list, "--role-id")

    assert "按羁绊 id 精确筛选" in trait_id_help
    assert "按角色 id 精确筛选" in role_id_help


def test_guide_list_help_mentions_mutually_exclusive_filters():
    trait_help = _function_option_help(guide_list, "--trait")
    trait_id_help = _function_option_help(guide_list, "--trait-id")
    role_help = _function_option_help(guide_list, "--role")
    role_id_help = _function_option_help(guide_list, "--role-id")
    portal_help = _function_option_help(guide_list, "--portal")
    portal_id_help = _function_option_help(guide_list, "--portal-id")

    assert "--trait-id" in trait_help and "互斥" in trait_help
    assert "--trait" in trait_id_help and "互斥" in trait_id_help
    assert "--role-id" in role_help and "互斥" in role_help
    assert "--role" in role_id_help and "互斥" in role_id_help
    assert "--portal-id" in portal_help and "互斥" in portal_help
    assert "--portal" in portal_id_help and "互斥" in portal_id_help


def test_guide_list_help_mentions_boolean_filter_semantics():
    match_change_job_help = _function_option_help(guide_list, "--match-change-job")
    match_hard_help = _function_option_help(guide_list, "--match-hard")

    assert "保留当前布尔筛选语义" in match_change_job_help
    assert "true/false" in match_change_job_help
    assert "保留当前布尔筛选语义" in match_hard_help
    assert "true/false" in match_hard_help


def test_screen_shot_returns_envelope_and_screenshot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "screen.shot": _build_success_response_with_guidance(
                request_id="req-screen-shot",
                data={"captured": True},
                screenshot=".trail/shots/req-screen-shot.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["screen", "shot"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_success_lines(
        "ok screen.shot captured=1",
        screenshot=".trail/shots/req-screen-shot.png",
    )
    assert client.calls == [
        {
            "method": "screen.shot",
            "payload": {},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_ocr_read_returns_runtime_payload_with_ocr_mode_and_retry_high_defaults(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "ocr.read": _build_success_response_with_guidance(
                request_id="req-ocr-read",
                data={"result": [{"text": "银狼"}]},
                screenshot=".trail/shots/req-ocr-read.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_success_lines(
        "ok ocr.read hits=1",
        screenshot=".trail/shots/req-ocr-read.png",
        body=["text value=银狼"],
    )
    assert client.calls == [
        {
            "method": "ocr.read",
            "payload": {
                "provider": "auto",
                "lang": "ch",
                "use_cls": False,
                "text_score": 0.5,
                "ocr_mode": "fast",
                "retry_high": "auto",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_ocr_read_includes_provider_lang_use_cls_ocr_mode_and_retry_high(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-provider",
                data={"result": [{"text": "银狼"}]},
                screenshot=".trail/shots/req-ocr-read-provider.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "ocr",
            "read",
            "--provider",
            "dml",
            "--lang",
            "ch",
            "--use-cls",
            "--text-score",
            "0.8",
            "--ocr-mode",
            "high",
            "--retry-high",
            "never",
        ],
    )

    assert result.exit_code == 0
    assert client.calls == [
        {
            "method": "ocr.read",
            "payload": {
                "provider": "dml",
                "lang": "ch",
                "use_cls": True,
                "text_score": 0.8,
                "ocr_mode": "high",
                "retry_high": "never",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_ocr_read_uses_env_defaults_in_payload_for_ocr_mode_and_retry_high(
    cli_runner, fake_daemon_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("TRAIL_OCR_PROVIDER", "cpu")
    monkeypatch.setenv("TRAIL_OCR_LANG", "ch")
    monkeypatch.setenv("TRAIL_OCR_USE_CLS", "1")
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "0.7")
    monkeypatch.setenv("TRAIL_OCR_MODE", "high")
    monkeypatch.setenv("TRAIL_OCR_RETRY_HIGH", "always")
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-env",
                data={"result": [{"text": "银狼"}]},
                screenshot=".trail/shots/req-ocr-read-env.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 0
    assert client.calls == [
        {
            "method": "ocr.read",
            "payload": {
                "provider": "cpu",
                "lang": "ch",
                "use_cls": True,
                "text_score": 0.7,
                "ocr_mode": "high",
                "retry_high": "always",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_ocr_read_explicit_ocr_mode_and_retry_high_override_conflicting_env(
    cli_runner, fake_daemon_client, monkeypatch, tmp_path
):
    monkeypatch.setenv("TRAIL_OCR_PROVIDER", "gpu")
    monkeypatch.setenv("TRAIL_OCR_LANG", "en")
    monkeypatch.setenv("TRAIL_OCR_USE_CLS", "0")
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "not-a-float")
    monkeypatch.setenv("TRAIL_OCR_MODE", "warp")
    monkeypatch.setenv("TRAIL_OCR_RETRY_HIGH", "sometimes")
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-explicit-overrides-env",
                data={"result": [{"text": "银狼"}]},
                screenshot=".trail/shots/req-ocr-read-explicit-overrides-env.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "ocr",
            "read",
            "--provider",
            "dml",
            "--lang",
            "ch",
            "--use-cls",
            "--text-score",
            "0.8",
            "--ocr-mode",
            "high",
            "--retry-high",
            "never",
        ],
    )

    assert result.exit_code == 0
    assert client.calls == [
        {
            "method": "ocr.read",
            "payload": {
                "provider": "dml",
                "lang": "ch",
                "use_cls": True,
                "text_score": 0.8,
                "ocr_mode": "high",
                "retry_high": "never",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_ocr_read_explicit_no_use_cls_overrides_env_true(cli_runner, fake_daemon_client, monkeypatch, tmp_path):
    monkeypatch.setenv("TRAIL_OCR_PROVIDER", "auto")
    monkeypatch.setenv("TRAIL_OCR_LANG", "ch")
    monkeypatch.setenv("TRAIL_OCR_USE_CLS", "1")
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "0.6")
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-no-cls",
                data={"result": [{"text": "银狼"}]},
                screenshot=".trail/shots/req-ocr-read-no-cls.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read", "--no-use-cls"])

    assert result.exit_code == 0
    assert client.calls == [
        {
            "method": "ocr.read",
            "payload": {
                "provider": "auto",
                "lang": "ch",
                "use_cls": False,
                "text_score": 0.6,
                "ocr_mode": "fast",
                "retry_high": "auto",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_ocr_read_rejects_invalid_provider_before_daemon_call():
    with pytest.raises(typer.BadParameter) as exc_info:
        _call_ocr_read_direct(provider="gpu")

    assert exc_info.value.param_hint == "--provider"
    assert "unsupported ocr provider: gpu" in str(exc_info.value)


def test_ocr_read_rejects_invalid_lang_before_daemon_call():
    with pytest.raises(typer.BadParameter) as exc_info:
        _call_ocr_read_direct(lang="en")

    assert exc_info.value.param_hint == "--lang"
    assert OCR_LANG_UNSUPPORTED in str(exc_info.value)


def test_ocr_read_rejects_invalid_ocr_mode_before_daemon_call():
    with pytest.raises(typer.BadParameter) as exc_info:
        _call_ocr_read_direct(ocr_mode="warp")

    assert exc_info.value.param_hint == "--ocr-mode"
    assert "unsupported ocr mode: warp" in str(exc_info.value)


def test_ocr_read_rejects_invalid_retry_high_before_daemon_call():
    with pytest.raises(typer.BadParameter) as exc_info:
        _call_ocr_read_direct(retry_high="sometimes")

    assert exc_info.value.param_hint == "--retry-high"
    assert "unsupported ocr retry_high: sometimes" in str(exc_info.value)


def test_ocr_read_help_describes_lang_and_ocr_mode_retry_high():
    assert "首版仅支持 ch" in _function_option_help(ocr_read, "--lang")
    assert "OCR 模式：fast=1280x720，high=native" in _function_option_help(ocr_read, "--ocr-mode")
    assert "高精度重试策略：auto|never|always" in _function_option_help(ocr_read, "--retry-high")


def test_ocr_read_rejects_invalid_text_score_env_before_daemon_call(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "not-a-float")

    with pytest.raises(typer.BadParameter) as exc_info:
        _call_ocr_read_direct()

    assert exc_info.value.param_hint == "TRAIL_OCR_TEXT_SCORE"
    assert "invalid ocr text score from TRAIL_OCR_TEXT_SCORE" in str(exc_info.value)


def test_ocr_read_rejects_invalid_use_cls_env_before_daemon_call(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_USE_CLS", "maybe")

    with pytest.raises(typer.BadParameter) as exc_info:
        _call_ocr_read_direct()

    assert exc_info.value.param_hint == "TRAIL_OCR_USE_CLS"
    assert "invalid ocr use_cls from" in str(exc_info.value)
    assert "TRAIL_OCR_USE_CLS: maybe" in str(exc_info.value)


def test_ocr_read_rejects_invalid_ocr_mode_env_before_daemon_call(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_MODE", "warp")

    with pytest.raises(typer.BadParameter) as exc_info:
        _call_ocr_read_direct()

    assert exc_info.value.param_hint == "TRAIL_OCR_MODE"
    assert "invalid ocr mode from" in str(exc_info.value)
    assert "TRAIL_OCR_MODE:" in str(exc_info.value)
    assert "warp" in str(exc_info.value)


def test_ocr_read_rejects_invalid_retry_high_env_before_daemon_call(monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_RETRY_HIGH", "sometimes")

    with pytest.raises(typer.BadParameter) as exc_info:
        _call_ocr_read_direct()

    assert exc_info.value.param_hint == "TRAIL_OCR_RETRY_HIGH"
    assert "invalid ocr retry_high from" in str(exc_info.value)
    assert "TRAIL_OCR_RETRY_HIGH: sometimes" in str(exc_info.value)


def test_pyproject_declares_windows_directml_runtime_dependency() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"onnxruntime-directml' in pyproject


def test_ocr_read_renders_OCR_PROVIDER_UNAVAILABLE_failure(cli_runner, monkeypatch, tmp_path):
    requests = []

    _install_real_daemon_client(
        monkeypatch,
        tmp_path,
        lambda request, token, *, endpoint: requests.append(request)
        or {
            "request_id": "req-ocr-dml-unavailable",
            "ok": False,
            "data": {},
            "screenshot": ".trail/shots/req-ocr-dml-unavailable.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {
                "trace": [
                    {
                        "step": "ocr_provider",
                        "requested_provider": "dml",
                        "effective_provider": "dml",
                        "lang": "ch",
                    }
                ]
            },
            "error": {
                "code": "OCR_PROVIDER_UNAVAILABLE",
                "message": "requested dml provider unavailable",
            },
        },
    )

    result = cli_runner.invoke(app, ["ocr", "read", "--provider", "dml"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail ocr.read code=OCR_PROVIDER_UNAVAILABLE",
        "request id=req-ocr-dml-unavailable",
        "shot path=.trail/shots/req-ocr-dml-unavailable.png",
        'why msg="requested dml provider unavailable"',
    ]
    assert len(requests) == 1
    assert requests[0].method == "ocr.read"
    assert requests[0].payload == {
        "provider": "dml",
        "lang": "ch",
        "use_cls": False,
        "text_score": 0.5,
        "ocr_mode": "fast",
        "retry_high": "auto",
    }
    assert requests[0].workspace_root == str(tmp_path)
    assert requests[0].session_id is None
    assert requests[0].verbose is False


def test_ocr_read_renders_OCR_LANG_UNSUPPORTED_failure_without_recover(cli_runner, monkeypatch, tmp_path):
    requests = []

    _install_real_daemon_client(
        monkeypatch,
        tmp_path,
        lambda request, token, *, endpoint: requests.append(request)
        or {
            "request_id": "req-ocr-lang-unsupported",
            "ok": False,
            "data": {},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": {
                "code": "OCR_LANG_UNSUPPORTED",
                "message": "unsupported ocr lang: en",
            },
        },
    )

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail ocr.read code=OCR_LANG_UNSUPPORTED",
        "request id=req-ocr-lang-unsupported",
        'why msg="unsupported ocr lang: en"',
    ]
    assert len(requests) == 1
    assert requests[0].method == "ocr.read"
    assert requests[0].payload == {
        "provider": "auto",
        "lang": "ch",
        "use_cls": False,
        "text_score": 0.5,
        "ocr_mode": "fast",
        "retry_high": "auto",
    }
    assert requests[0].workspace_root == str(tmp_path)
    assert requests[0].session_id is None
    assert requests[0].verbose is False


def test_daemon_client_preserves_request_id_for_non_control_plane_failures(tmp_path):
    request_ids: list[str] = []

    write_ready_manifest(
        tmp_path / "daemon-home",
        endpoint="127.0.0.1:8765",
        token_value="token-1",
    )
    client = TrailDaemonClient(
        workspace_root=tmp_path,
        daemon_home=tmp_path / "daemon-home",
        transport=lambda request, token, *, endpoint: request_ids.append(request.request_id)
        or {
            "request_id": request.request_id,
            "ok": False,
            "data": {},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {"source": "runtime"},
            "error": {
                "code": "OCR_PROVIDER_UNAVAILABLE",
                "message": "requested dml provider unavailable",
            },
        },
    )

    payload = client.call("ocr.read", {"provider": "dml"})

    assert payload["ok"] is False
    assert request_ids == [payload["debug"]["request_id"]]
    assert payload["debug"] == {
        "source": "runtime",
        "request_id": request_ids[0],
    }
    assert "request_id" not in payload


def test_ocr_read_verbose_preserves_provider_trace_debug_pipeline(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "ocr.read": {
                "request_id": "req-ocr-dml-verbose",
                "ok": False,
                "data": {},
                "screenshot": ".trail/shots/req-ocr-dml-verbose.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {
                    "trace": [
                        {
                            "step": "ocr_provider",
                            "requested_provider": "dml",
                            "effective_provider": "dml",
                            "lang": "ch",
                            "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
                            "reason": "RuntimeError: explicit dml run failed",
                        }
                    ]
                },
                "error": {
                    "code": "OCR_PROVIDER_UNAVAILABLE",
                    "message": "requested dml provider unavailable",
                },
            }
        }
    )

    result = cli_runner.invoke(app, ["--verbose", "ocr", "read"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail ocr.read code=OCR_PROVIDER_UNAVAILABLE",
        "request id=req-ocr-dml-verbose",
        "shot path=.trail/shots/req-ocr-dml-verbose.png",
        'why msg="requested dml provider unavailable"',
        "debug kind=request msg=req-ocr-dml-verbose",
        'debug kind=trace step=ocr_provider requested_provider=dml effective_provider=dml lang=ch available_providers="[\'DmlExecutionProvider\', \'CPUExecutionProvider\']" reason="RuntimeError: explicit dml run failed"',
    ]
    assert client.calls == [
        {
            "method": "ocr.read",
            "payload": {
                "provider": "auto",
                "lang": "ch",
                "use_cls": False,
                "text_score": 0.5,
                "ocr_mode": "fast",
                "retry_high": "auto",
            },
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": True,
        }
    ]


def test_image_locate_returns_box_payload(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "image.locate": _build_success_response_with_guidance(
                request_id="req-image-locate",
                data={"box": {"left": 1, "top": 2, "width": 3, "height": 4}},
                screenshot=".trail/shots/req-image-locate.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["image", "locate", "demo.png"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_success_lines(
        "ok image.locate box=1,2,3,4",
        screenshot=".trail/shots/req-image-locate.png",
    )
    assert client.calls == [
        {
            "method": "image.locate",
            "payload": {"template": "demo.png"},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_image_locate_serializes_numpy_box_values(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "image.locate": build_success_response(
                request_id="req-image-locate-numpy",
                data={"box": Box(np.int64(1), np.int64(2), np.int64(3), np.int64(4), source="demo.png")},
                screenshot=".trail/shots/req-image-locate-numpy.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["image", "locate", "demo.png"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_success_lines(
        "ok image.locate box=1,2,3,4",
        screenshot=".trail/shots/req-image-locate-numpy.png",
    )


def test_image_wait_returns_error_when_template_missing(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "image.wait": {
                "ok": False,
                "data": {},
                "screenshot": ".trail/shots/req-image-wait.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": {"code": "IMAGE_NOT_FOUND", "message": "未找到 missing.png"},
            }
        }
    )

    result = cli_runner.invoke(app, ["image", "wait", "missing.png"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail image.wait code=IMAGE_NOT_FOUND",
        "shot path=.trail/shots/req-image-wait.png",
        'why msg="未找到 missing.png"',
    ]
    assert client.calls == [
        {
            "method": "image.wait",
            "payload": {"template": "missing.png", "timeout": 10},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_input_click_drag_and_key_return_envelopes(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "input.click": _build_success_response_with_guidance(
                request_id="req-input-click",
                data={"clicked": [10, 20]},
                screenshot=".trail/shots/req-input-click.png",
            ),
            "input.drag": _build_success_response_with_guidance(
                request_id="req-input-drag",
                data={"dragged": [1, 2, 3, 4]},
                screenshot=".trail/shots/req-input-drag.png",
            ),
            "input.key": _build_success_response_with_guidance(
                request_id="req-input-key",
                data={"key": "space", "presses": 2},
                screenshot=".trail/shots/req-input-key.png",
            ),
        }
    )

    click_result = cli_runner.invoke(app, ["input", "click", "10", "20"])
    drag_result = cli_runner.invoke(app, ["input", "drag", "1", "2", "3", "4"])
    key_result = cli_runner.invoke(app, ["input", "key", "space", "--presses", "2"])

    assert click_result.exit_code == 0
    assert drag_result.exit_code == 0
    assert key_result.exit_code == 0
    assert click_result.stdout.splitlines() == _expected_success_lines(
        "ok input.click",
        screenshot=".trail/shots/req-input-click.png",
    )
    assert drag_result.stdout.splitlines() == _expected_success_lines(
        "ok input.drag",
        screenshot=".trail/shots/req-input-drag.png",
    )
    assert key_result.stdout.splitlines() == _expected_success_lines(
        "ok input.key",
        screenshot=".trail/shots/req-input-key.png",
    )
    assert client.calls == [
        {
            "method": "input.click",
            "payload": {"x": 10, "y": 20},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        },
        {
            "method": "input.drag",
            "payload": {"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        },
        {
            "method": "input.key",
            "payload": {"key": "space", "presses": 2},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        },
    ]


def test_input_click_returns_runtime_warnings_and_reference_matches(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "input.click": {
                "ok": True,
                "data": {"clicked": [10, 20]},
                "screenshot": ".trail/shots/req-input-click.png",
                "timing": {},
                "warnings": [
                    {
                        "code": "WINDOW_NOT_FOREGROUND",
                        "message": "输入命令执行后窗口不在前台，本次操作可能失败",
                    }
                ],
                "references": [{"path": "trail/scenes/cw/references/1-1.png", "similarity": 0.88}],
                "debug": None,
                "error": None,
            }
        }
    )

    result = cli_runner.invoke(app, ["input", "click", "10", "20"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_success_lines(
        "ok input.click",
        screenshot=".trail/shots/req-input-click.png",
        body=[
            "warn code=WINDOW_NOT_FOREGROUND msg=输入命令执行后窗口不在前台，本次操作可能失败",
            "ref path=trail/scenes/cw/references/1-1.png sim=0.88",
        ],
    )


def test_top_level_verbose_emits_runtime_debug_trace(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "input.click": {
                "request_id": "req-input-click-verbose",
                "ok": True,
                "data": {"clicked": [10, 20]},
                "screenshot": ".trail/shots/req-input-click-verbose.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {"trace": [{"step": "click", "point": [10, 20]}]},
                "error": None,
            }
        }
    )

    result = cli_runner.invoke(app, ["--verbose", "input", "click", "10", "20"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_success_lines(
        "ok input.click",
        screenshot=".trail/shots/req-input-click-verbose.png",
        body=[
            "debug kind=request msg=req-input-click-verbose",
            'debug kind=trace step=click point="[10, 20]"',
        ],
    )
    assert client.calls == [
        {
            "method": "input.click",
            "payload": {"x": 10, "y": 20},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": True,
        }
    ]


def test_daemon_control_plane_errors_keep_request_id_in_debug(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "screen.shot": {
                "request_id": "req-screen-shot-daemon-error",
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {"detail": "bootstrap missing"},
                "error": {
                    "code": "DAEMON_BOOTSTRAP_REQUIRED",
                    "message": "daemon bootstrap not installed",
                },
            }
        }
    )

    result = cli_runner.invoke(app, ["screen", "shot"])
    verbose_result = cli_runner.invoke(app, ["--verbose", "screen", "shot"])

    assert result.exit_code == 0
    assert verbose_result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail screen.shot code=DAEMON_BOOTSTRAP_REQUIRED",
        "request id=req-screen-shot-daemon-error",
        'why msg="daemon bootstrap not installed"',
    ]
    assert verbose_result.stdout.splitlines() == [
        "fail screen.shot code=DAEMON_BOOTSTRAP_REQUIRED",
        "request id=req-screen-shot-daemon-error",
        'why msg="daemon bootstrap not installed"',
        "debug kind=request msg=req-screen-shot-daemon-error",
        'debug kind=detail msg="bootstrap missing"',
    ]
    assert client.calls == [
        {
            "method": "screen.shot",
            "payload": {},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        },
        {
            "method": "screen.shot",
            "payload": {},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": True,
        },
    ]


def test_state_dump_renders_summary_before_yaml(cli_runner, fake_daemon_client, tmp_path, monkeypatch):
    session_id = "session-1"

    def fail_local_state_dump(*args, **kwargs):
        raise AssertionError("local state dump path used")

    monkeypatch.setattr("trail.commands.state.run_session_command", fail_local_state_dump, raising=False)
    client = fake_daemon_client(
        {
            "state.dump": _build_success_response_with_guidance(
                request_id="req-state-dump",
                data={
                    "session_id": session_id,
                    "last_stage": {"scene": "cw", "value": "shop"},
                    "scene_state": {
                        "daemon": {"tainted": False},
                        "cw": {"stage": {"value": "shop", "stale": False}},
                    },
                },
                screenshot=".trail/shots/req-state-dump.png",
            )
        }
    )

    text_result = cli_runner.invoke(app, ["state", "dump", "--session", session_id])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "state", "dump", "--session", session_id])

    assert text_result.exit_code == 0
    assert yaml_result.exit_code == 0
    assert text_result.stdout.splitlines() == _expected_success_lines(
        "ok state.dump session=session-1 scene=cw last_stage=shop tainted=0",
        screenshot=".trail/shots/req-state-dump.png",
    )
    assert yaml_result.stdout.splitlines()[0:3] == _expected_success_lines(
        "ok state.dump session=session-1 scene=cw last_stage=shop tainted=0",
        screenshot=".trail/shots/req-state-dump.png",
    )
    assert "scene_state:" in yaml_result.stdout
    assert client.calls == [
        {
            "method": "state.dump",
            "payload": {"session_id": session_id},
            "workspace_root": str(tmp_path),
            "session_id": session_id,
            "verbose": False,
        },
        {
            "method": "state.dump",
            "payload": {"session_id": session_id},
            "workspace_root": str(tmp_path),
            "session_id": session_id,
            "verbose": False,
        },
    ]


def test_state_dump_yaml_includes_cw_equipment_snapshot(cli_runner, fake_daemon_client, tmp_path, monkeypatch):
    session_id = "session-equipment"

    def fail_local_state_dump(*args, **kwargs):
        raise AssertionError("local state dump path used")

    monkeypatch.setattr("trail.commands.state.run_session_command", fail_local_state_dump, raising=False)
    client = fake_daemon_client(
        {
            "state.dump": build_success_response(
                request_id="req-state-dump-equipment",
                data={
                    "session_id": session_id,
                    "scene_state": {
                        "cw": {
                            "equipment": {
                                "items": [
                                    {
                                        "pos": "equipment:1",
                                        "center": {"x": 1855, "y": 275},
                                        "row": 1,
                                        "col": 1,
                                        "name": "生命之花",
                                        "gap": 0.01,
                                        "alt": "光能电池",
                                        "alt_score": 0.69,
                                        "candidates": [{"name": "生命之花", "score": 0.7}],
                                    }
                                ],
                                "stale": False,
                            }
                        }
                    },
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "state", "dump", "--session", session_id])

    assert result.exit_code == 0
    assert "equipment:" in result.stdout
    assert "pos: equipment:1" in result.stdout
    assert "center:" in result.stdout
    assert "row: 1" in result.stdout
    assert "col: 1" in result.stdout
    assert "gap: 0.01" in result.stdout
    assert "alt: 光能电池" in result.stdout
    assert "alt_score: 0.69" in result.stdout
    assert "candidates:" in result.stdout
    assert "box:" not in result.stdout
    assert client.calls == [
        {
            "method": "state.dump",
            "payload": {"session_id": session_id},
            "workspace_root": str(tmp_path),
            "session_id": session_id,
            "verbose": False,
        }
    ]


def test_state_dump_surfaces_stage_error_summary(cli_runner, fake_daemon_client, tmp_path):
    session_id = "session-2"
    client = fake_daemon_client(
        {
            "state.dump": build_success_response(
                request_id="req-state-dump-stale",
                data={
                    "session_id": session_id,
                    "last_stage": None,
                    "scene_state": {
                        "daemon": {"tainted": True},
                        "cw": {"stage": {"stale": True, "error": {"code": "STAGE_AMBIGUOUS"}}},
                    },
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["state", "dump", "--session", session_id])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok state.dump session=session-2 scene=cw stage_stale=1 stage_error=STAGE_AMBIGUOUS tainted=1"
    ]
    assert client.calls == [
        {
            "method": "state.dump",
            "payload": {"session_id": session_id},
            "workspace_root": str(tmp_path),
            "session_id": session_id,
            "verbose": False,
        }
    ]


def test_state_dump_yaml_includes_latest_battle_run_summary(cli_runner, fake_daemon_client, tmp_path):
    session_id = "session-battle-run"
    client = fake_daemon_client(
        {
            "state.dump": build_success_response(
                request_id="req-state-dump-battle-run",
                data={
                    "session_id": session_id,
                    "last_stage": None,
                    "last_result": {
                        "command": "cw.battle.run",
                        "ok": True,
                        "data": {
                            "status": "in_progress",
                            "stale": True,
                            "in_battle": True,
                            "timeout_seconds": 570,
                        },
                        "error": None,
                    },
                    "last_screenshot": ".trail/shots/req-cw-battle-run.png",
                    "scene_state": {
                        "daemon": {"tainted": False},
                        "cw": {"stage": {"stale": True}},
                    },
                },
                screenshot=".trail/shots/req-state-dump-battle-run.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "state", "dump", "--session", session_id])

    assert result.exit_code == 0
    assert "last_result:" in result.stdout
    assert "command: cw.battle.run" in result.stdout
    assert "status: in_progress" in result.stdout
    assert "last_screenshot: .trail/shots/req-cw-battle-run.png" in result.stdout
    assert client.calls == [
        {
            "method": "state.dump",
            "payload": {"session_id": session_id},
            "workspace_root": str(tmp_path),
            "session_id": session_id,
            "verbose": False,
        }
    ]


def test_state_dump_yaml_includes_last_battle_round(cli_runner, fake_daemon_client, tmp_path):
    session_id = "session-1"
    client = fake_daemon_client(
        {
            "state.dump": build_success_response(
                request_id="req-state-dump-round",
                data={
                    "session_id": session_id,
                    "scene_state": {"cw": {"metrics": {"last_battle_round": "1-1"}}},
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "state", "dump", "--session", session_id])

    assert result.exit_code == 0
    assert "last_battle_round: 1-1" in result.stdout
    assert client.calls == [
        {
            "method": "state.dump",
            "payload": {"session_id": session_id},
            "workspace_root": str(tmp_path),
            "session_id": session_id,
            "verbose": False,
        }
    ]


def test_state_dump_returns_structured_error_for_missing_session(cli_runner, fake_daemon_client, tmp_path, monkeypatch):
    session_id = "deadbeefdeadbeefdeadbeefdeadbeef"

    def fail_local_state_dump(*args, **kwargs):
        raise AssertionError("local state dump path used")

    monkeypatch.setattr("trail.commands.state.run_session_command", fail_local_state_dump, raising=False)
    client = fake_daemon_client(
        {
            "state.dump": {
                "request_id": "req-state-dump-missing",
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": {
                    "code": "SESSION_NOT_FOUND",
                    "message": "session missing",
                },
            }
        }
    )

    result = cli_runner.invoke(app, ["state", "dump", "--session", session_id])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail state.dump code=SESSION_NOT_FOUND",
        'why msg="session missing"',
    ]
    assert "Traceback" not in result.stdout
    assert client.calls == [
        {
            "method": "state.dump",
            "payload": {"session_id": session_id},
            "workspace_root": str(tmp_path),
            "session_id": session_id,
            "verbose": False,
        }
    ]


@pytest.mark.parametrize(
    "module_name",
    [
        "trail.commands.session",
        "trail.commands.guide",
        "trail.commands.cw",
        "trail.commands.state",
        "trail.commands.input",
    ],
)
def test_command_module_import_has_no_trail_workspace_side_effect(module_name, tmp_path, monkeypatch):
    workdir = tmp_path / module_name.rsplit(".", 1)[-1]
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    module = importlib.import_module(module_name)
    importlib.reload(module)

    assert not (workdir / ".trail").exists()


def test_input_click_returns_structured_error_when_backend_missing(cli_runner, tmp_path, monkeypatch, fake_daemon_client):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    client = fake_daemon_client(
        {
            "input.click": build_success_response(
                request_id="req-input-click-backendless",
                data={"clicked": [10, 20]},
                screenshot=".trail/shots/req-input-click-backendless.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["input", "click", "10", "20"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_success_lines(
        "ok input.click",
        screenshot=".trail/shots/req-input-click-backendless.png",
    )
    assert client.calls == [
        {
            "method": "input.click",
            "payload": {"x": 10, "y": 20},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_cli_help_exposes_top_level_command_groups():
    assert {
        "start",
        "session",
        "guide",
        "window",
        "screen",
        "ocr",
        "image",
        "input",
        "state",
        "cw",
    }.issubset(_registered_group_names(app))
    assert "--verbose" in _callback_option_decls(app)


def test_cw_help_exposes_scene_command_groups():
    group_help = _registered_group_help(cw_app)
    normalized = _normalize_help(" ".join([CW_APP_HELP, *group_help.values()]))

    assert "货币战争固定流程命令" in normalized
    assert "enter 到首页" in normalized
    assert "start 从首页进入投资环境页" in normalized
    assert "投资环境页的识别/选择/刷新/重开" in normalized
    assert "detect 只重识别当前三张卡" in normalized
    assert "refresh 点击刷新后生成新的三张卡" in normalized
    assert {"enter", "start"}.issubset(_registered_command_names(cw_app))

    expected_blocks = {
        "guide": ("查看", "应用", "当前对局", "已选攻略", "guide.fetch.cw --select", "自动应用"),
        "portal": ("投资环境页", "识别", "选择", "刷新", "重开"),
        "stage": ("检测", "等待", "阶段"),
        "slots": ("编队",),
        "shop": ("商店",),
        "crystals": ("结晶",),
        "hand": ("手牌",),
        "replenish": ("补给事件",),
        "invest": ("局内", "invest", "事件"),
        "encounter": ("遭遇事件",),
        "fortune": ("命运卜者事件",),
        "boss-preview": ("首领预览",),
        "battle": ("战斗",),
        "settle": ("结算",),
        "event": ("通用", "特殊事件"),
    }

    for command_name, anchors in expected_blocks.items():
        block = group_help[command_name]
        for anchor in anchors:
            assert anchor in block


@pytest.mark.parametrize(
    ("group_name", "expected_anchors"),
    [
        ("portal", ("投资环境页", "detect", "识别", "选择", "refresh", "刷新", "重开")),
        ("invest", ("局内", "invest", "事件")),
        ("stage", ("货币战争内部", "检测", "等待", "登录页", "大世界", "非 CW")),
        ("guide", ("查看", "应用", "当前对局", "已选攻略", "guide.fetch.cw --select", "自动应用")),
        ("shop", ("商店", "购买", "刷新", "关闭")),
        ("event", ("通用", "特殊事件")),
    ],
)
def test_cw_group_help_describes_expected_boundary(group_name, expected_anchors):
    normalized = _normalize_help(_registered_group_help(cw_app)[group_name])

    for anchor in expected_anchors:
        assert anchor in normalized


@pytest.mark.parametrize(
    ("group_name", "forbidden_phrase"),
    [
        ("portal", "进入投资环境页"),
        ("guide", "攻略列表"),
        ("stage", "推进流程"),
        ("stage", "通用场景"),
        ("event", "处理所有事件"),
    ],
)
def test_cw_group_help_avoids_forbidden_phrases(group_name, forbidden_phrase):
    normalized = _normalize_help(_registered_group_help(cw_app)[group_name])

    assert forbidden_phrase not in normalized


def test_guide_list_cw_help_uses_chinese_summary_terms():
    normalized = _normalize_help(guide_list.__doc__ or "")

    assert "攻略ID" in normalized
    assert "攻略标签" in normalized
    assert "最终阵容" in normalized
    assert "has_change_equip" not in normalized
    assert "has_expert" not in normalized
    assert "support_hard" not in normalized
    assert "final_role_cards" not in normalized


def test_cw_portal_help_mentions_invest_portal_terms():
    normalized = _normalize_help(_registered_group_help(cw_app)["portal"])

    assert "投资环境" in normalized
    assert "默认不自动附加动态攻略摘要" in normalized
    assert "显式 guide.list.cw / guide.fetch.cw" in normalized
    assert "下挂攻略摘要" not in normalized
    assert "title=" not in normalized
    assert "desc=" not in normalized
    assert "new=" not in normalized


def test_cw_equipment_help_describes_bundle_prepare_contract():
    normalized = _normalize_help(_registered_group_help(cw_app)["equipment"])

    assert "prepare 默认只汇总 bundle 装备资源" in normalized
    assert "--refresh 写 workspace override" in normalized
    assert "read 默认使用 bundle recognizer" in normalized
    assert "prepare 只准备资源缓存" not in normalized


def test_cw_guide_help_describes_selected_guide_boundary():
    normalized = _normalize_help(_registered_group_help(cw_app)["guide"])

    assert "当前已选攻略" in normalized
    assert "guide.fetch.cw --select" in normalized
    assert "cw.portal.select" in normalized
    assert "cw.guide.apply 只作为手动兜底" in normalized
    assert normalized.index("guide.fetch.cw --select") < normalized.index("cw.portal.select")
    assert normalized.index("cw.portal.select") < normalized.index("cw.guide.apply 只作为手动兜底")
    assert "攻略快照ID" not in normalized
    assert "artifact=" not in normalized
    assert "当前已应用攻略" not in normalized
    assert "已经 apply 过后" not in normalized
    assert "--lineup-id" not in normalized


def test_guide_fetch_cw_help_distinguishes_preview_and_select_flow():
    summary_block = _normalize_help(guide_fetch.__doc__ or "")
    select_block = _function_option_help(guide_fetch, "--select")
    session_block = _function_option_help(guide_fetch, "--session")
    option_decls = _function_option_decls(guide_fetch)

    assert "拉取攻略内容并直接返回给 Agent" in summary_block
    assert "默认只做预览/查看" in summary_block
    assert "支持 lineup_url 或 lineup_id" in summary_block
    assert "写入当前 session" not in summary_block
    assert "--session" not in summary_block
    assert "--select" in option_decls
    assert "把攻略写入当前 session" in select_block
    assert "建立当前已选攻略" in select_block
    assert "不执行 UI 应用" in select_block
    assert "--session" in option_decls
    assert "只在 --select 时必填" in session_block
    assert "--select" in session_block
    assert "--lineup-id" not in option_decls


def test_cw_portal_select_help_mentions_selected_guide_auto_apply():
    normalized = _normalize_help(_registered_command_help(portal_app, "select"))
    option_decls = _function_option_decls(cw_portal_select)

    assert "--session" in option_decls
    assert "--card-idx" in option_decls
    assert "guide.fetch.cw --select" in normalized
    assert "未记录则会在点击前失败" in normalized
    assert "成功后会自动应用当前已选攻略" in normalized
    assert "自动收集" in normalized
    assert "stage/slots/equipment/shop" in normalized
    assert "装备失败" in normalized
    assert "soft warning" in normalized
    assert "handoff" in normalized
    assert "手动兜底" not in normalized


def test_cw_guide_apply_help_marks_manual_fallback_only():
    normalized = _normalize_help(_registered_command_help(cw_guide_app, "apply"))
    option_decls = _function_option_decls(cw_guide_apply)

    assert "--session" in option_decls
    assert "手动兜底" in normalized
    assert "当前已选攻略" in normalized
    assert "常规第一步" not in normalized


def test_cw_slots_read_help_describes_slot_as_targeted_confirmation():
    normalized = _normalize_help(_registered_command_help(slots_app, "read"))
    slot_help = _normalize_help(_function_option_help(cw_slots_read, "--slot"))
    option_decls = _function_option_decls(cw_slots_read)

    assert "--slot" in option_decls
    assert "先看当前截图" in normalized
    assert "有角色但名字不确定" in normalized
    assert "不传 --slot 时仍保留全量读取" in normalized
    assert "定向确认" in slot_help
    assert "全量读取" in slot_help


def test_cw_equipment_help_mentions_compose_real_action():
    help_text = _registered_group_help(cw_app)["equipment"]

    assert "compose" in _registered_command_names(equipment_app)
    assert "真实合成" in help_text or "执行真实合成" in help_text
    assert "只写 session" not in help_text
    assert "不执行真实 UI 合成" not in help_text


def test_cw_equipment_compose_help_lists_required_options():
    command_help = _registered_command_help(equipment_app, "compose")
    option_decls = _function_option_decls(cw_equipment_compose)
    slot_help = _function_option_help(cw_equipment_compose, "--slot")

    assert "--name" in option_decls
    assert "--slot" in option_decls
    assert "--role" in option_decls
    assert "1-based" in command_help or "从 1 开始" in slot_help


def test_cw_portal_help_distinguishes_detect_and_refresh():
    normalized = _normalize_help(_registered_group_help(cw_app)["portal"])
    command_names = _registered_command_names(portal_app)

    assert "detect" in command_names
    assert "refresh" in command_names
    assert "重新识别并保存当前三张卡" in normalized
    assert "点击刷新后生成新的三张卡" in normalized


def test_cw_portal_detect_help_exposes_snapshot_only_contract():
    normalized = _normalize_help(_registered_command_help(portal_app, "detect"))
    option_decls = _function_option_decls(cw_portal_detect)

    assert "--session" in option_decls
    assert "当前已在投资环境页时重新识别并保存 portal snapshot" in normalized
    assert "只重建当前三张卡识别结果" in normalized
    assert "不点击、不刷新、不重开" in normalized
    assert "推进流程" not in normalized


def test_strategy_group_is_visible_in_cw_help():
    block = _registered_group_help(cw_app)["strategy"]

    assert "strategy" in _registered_group_help(cw_app)
    assert "局内投资策略页" in block
    assert "单卡刷新" in block


def test_strategy_group_help_describes_snapshot_and_single_card_refresh():
    normalized = _normalize_help(_registered_group_help(cw_app)["strategy"])
    command_names = _registered_command_names(strategy_app)

    assert "detect" in command_names
    assert "select" in command_names
    assert "refresh" in command_names
    assert "只重建当前三张策略卡快照" in normalized
    assert "只刷新指定卡" in normalized
    assert "不做整页刷新" in normalized


def test_strategy_group_refresh_help_requires_card_idx():
    normalized = _normalize_help(_registered_command_help(strategy_app, "refresh"))
    option_decls = _function_option_decls(cw_strategy_refresh)
    card_idx_option = inspect.signature(cw_strategy_refresh).parameters["card_idx"].default

    assert "--card-idx" in option_decls
    assert "只刷新指定卡" in normalized
    assert "不做整页刷新" in normalized
    assert card_idx_option.default is ...


def test_invest_help_marks_compatibility_entry():
    normalized = _normalize_help(_registered_group_help(cw_app)["invest"])

    assert "兼容" in normalized
    assert "粗粒度入口" in normalized
    assert "不用于投资策略页" in normalized


def test_cw_enter_help_exposes_home_only_contract():
    option_decls = _function_option_decls(cw_enter)

    assert "--session" in option_decls
    assert "--mode" not in option_decls
    assert "--difficulty" not in option_decls
    assert "--battle-mode" not in option_decls


def test_cw_start_help_mentions_ax_x_and_rank_ranges():
    difficulty_help = _normalize_help(_function_option_help(cw_start, "--difficulty"))

    assert "lowest/current/highest/AX-X" in difficulty_help
    assert "A0-1..A8-40" in difficulty_help


def test_cw_start_invalid_difficulty_reaches_daemon_instead_of_typer(cli_runner, monkeypatch):
    seen: dict[str, object] = {}

    def fake_call(method, payload, *, session_id=None, verbose=None, daemon_client=None):
        del session_id, verbose, daemon_client
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
        [
            "cw",
            "start",
            "--session",
            "session-1",
            "--mode",
            "new",
            "--difficulty",
            "A9-1",
            "--battle-mode",
            "standard",
        ],
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


def test_window_help_exposes_launch_command():
    assert {"attach", "launch"}.issubset(_registered_command_names(window_app))
