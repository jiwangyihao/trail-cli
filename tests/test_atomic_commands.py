from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

from trail.cli import app
from trail.core.errors import TrailError
from trail.daemon.client import TrailDaemonClient
from trail.runtime.ocr_config import OCR_LANG_UNSUPPORTED
from trail.runtime.model import Box
from tests.support.fake_daemon import build_success_response, write_ready_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
            "window.attach": build_success_response(
                request_id="req-window-attach",
                data={"title": "Demo Window", "hwnd": 123},
                screenshot=".trail/shots/req-window-attach.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["window", "attach", "--window-title", "Demo Window"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        'ok window.attach title="Demo Window" hwnd=123',
        "shot path=.trail/shots/req-window-attach.png",
    ]
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


def test_trail_start_help_describes_simple_entry(cli_runner):
    result = cli_runner.invoke(app, ["start", "--help"])

    assert result.exit_code == 0
    assert "自动完成 daemon、游戏、窗口、session 的启动收口" in result.output
    assert "start.run" in result.output
    assert "--window-title" in result.output
    assert "--game-path" in result.output
    assert "--channel" in result.output
    assert "trail daemon status" not in result.output
    assert "trail window attach" not in result.output
    assert "trail session create" not in result.output


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
            "start.run": build_success_response(
                request_id="req-start-dispatch",
                data={"session": "sess-start-1", "reused": 0, "title": "Demo Window", "hwnd": 321},
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
        'ok start.run session=sess-start-1 reused=0 title="Demo Window" hwnd=321'
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


def test_window_launch_help_describes_game_path_option_resolution_contract(cli_runner):
    result = cli_runner.invoke(app, ["window", "launch", "--help"])
    game_path_help = _extract_help_option_block(result.output, "--game-path")

    assert result.exit_code == 0
    assert "--game-path" in game_path_help
    assert "历史成功路径 -> 默认路径 -> 直接问用户" in game_path_help
    assert "默认路径仅覆盖 official" in game_path_help
    assert "GAME_PATH_NOT_FOUND" in result.output
    assert "GAME_LAUNCH_FAILED" in result.output
    assert "GAME_PATH_PERSIST_FAILED" in result.output


def test_guide_list_help_mentions_trait_and_role_filters(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--help"])
    trait_help = _extract_help_option_block(result.output, "--trait")
    role_help = _extract_help_option_block(result.output, "--role")
    portal_help = _extract_help_option_block(result.output, "--portal")
    portal_id_help = _extract_help_option_block(result.output, "--portal-id")

    assert result.exit_code == 0
    assert "--trait" in trait_help
    assert "按羁绊名称筛选" in trait_help
    assert "免查 config" in trait_help
    assert "--role" in role_help
    assert "按角色名称筛选" in role_help
    assert "--portal" in portal_help
    assert "按投资环境筛选" in portal_help
    assert "--portal-id" in portal_id_help
    assert "按投资环境筛选" in portal_id_help


def test_guide_list_help_mentions_repeatable_role_values(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--help"])
    role_help = _extract_help_option_block(result.output, "--role")
    role_id_help = _extract_help_option_block(result.output, "--role-id")

    assert result.exit_code == 0
    assert "可重复传入多个值" in role_help
    assert "可重复传入多个值" in role_id_help


def test_guide_list_help_mentions_exact_id_filters(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--help"])
    trait_id_help = _extract_help_option_block(result.output, "--trait-id")
    role_id_help = _extract_help_option_block(result.output, "--role-id")

    assert result.exit_code == 0
    assert "按羁绊 id 精确筛选" in trait_id_help
    assert "按角色 id 精确筛选" in role_id_help


def test_guide_list_help_mentions_mutually_exclusive_filters(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--help"])
    trait_help = _extract_help_option_block(result.output, "--trait")
    trait_id_help = _extract_help_option_block(result.output, "--trait-id")
    role_help = _extract_help_option_block(result.output, "--role")
    role_id_help = _extract_help_option_block(result.output, "--role-id")
    portal_help = _extract_help_option_block(result.output, "--portal")
    portal_id_help = _extract_help_option_block(result.output, "--portal-id")

    assert result.exit_code == 0
    assert "--trait-id" in trait_help and "互斥" in trait_help
    assert "--trait" in trait_id_help and "互斥" in trait_id_help
    assert "--role-id" in role_help and "互斥" in role_help
    assert "--role" in role_id_help and "互斥" in role_id_help
    assert "--portal-id" in portal_help and "互斥" in portal_help
    assert "--portal" in portal_id_help and "互斥" in portal_id_help


def test_guide_list_help_mentions_boolean_filter_semantics(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--help"])
    match_change_job_help = _extract_help_option_block(result.output, "--match-change-job")
    match_hard_help = _extract_help_option_block(result.output, "--match-hard")

    assert result.exit_code == 0
    assert "保留当前布尔筛选语义" in match_change_job_help
    assert "true/false" in match_change_job_help
    assert "保留当前布尔筛选语义" in match_hard_help
    assert "true/false" in match_hard_help


def test_screen_shot_returns_envelope_and_screenshot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "screen.shot": build_success_response(
                request_id="req-screen-shot",
                data={"captured": True},
                screenshot=".trail/shots/req-screen-shot.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["screen", "shot"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok screen.shot captured=1",
        "shot path=.trail/shots/req-screen-shot.png",
    ]
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
            "ocr.read": build_success_response(
                request_id="req-ocr-read",
                data={"result": [{"text": "银狼"}]},
                screenshot=".trail/shots/req-ocr-read.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok ocr.read hits=1",
        "shot path=.trail/shots/req-ocr-read.png",
        "text value=银狼",
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


def test_ocr_read_rejects_invalid_provider_before_daemon_call(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-invalid-provider",
                data={"result": [{"text": "银狼"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read", "--provider", "gpu"])

    assert result.exit_code == 2
    assert "--provider" in result.output
    assert client.calls == []


def test_ocr_read_rejects_invalid_lang_before_daemon_call(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-invalid-lang",
                data={"result": [{"text": "银狼"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read", "--lang", "en"])

    assert result.exit_code == 2
    assert "--lang" in result.output
    assert OCR_LANG_UNSUPPORTED in result.output
    assert client.calls == []


def test_ocr_read_rejects_invalid_ocr_mode_before_daemon_call(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-invalid-ocr-mode",
                data={"result": [{"text": "银狼"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read", "--ocr-mode", "warp"])

    assert result.exit_code == 2
    assert "--ocr-mode" in result.output
    assert "unsupported ocr mode: warp" in result.output
    assert client.calls == []


def test_ocr_read_rejects_invalid_retry_high_before_daemon_call(cli_runner, fake_daemon_client):
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-invalid-retry-high",
                data={"result": [{"text": "银狼"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read", "--retry-high", "sometimes"])

    assert result.exit_code == 2
    assert "--retry-high" in result.output
    assert "unsupported ocr retry_high: sometimes" in result.output
    assert client.calls == []


def test_ocr_read_help_describes_lang_and_ocr_mode_retry_high(cli_runner):
    result = cli_runner.invoke(app, ["ocr", "read", "--help"])

    assert result.exit_code == 0
    assert "--lang" in result.output
    assert "首版仅支持 ch" in result.output
    assert "--ocr-mode" in result.output
    assert "OCR 模式：fast=1280x720，high=native" in result.output
    assert "fast=1280x720" in result.output
    assert "high=native" in result.output
    assert "--retry-high" in result.output
    assert "高精度重试策略：auto|never|always" in result.output
    assert "auto|never|always" in result.output


def test_ocr_read_rejects_invalid_text_score_env_before_daemon_call(cli_runner, fake_daemon_client, monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_TEXT_SCORE", "not-a-float")
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-invalid-text-score-env",
                data={"result": [{"text": "银狼"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 2
    assert "TRAIL_OCR_TEXT_SCORE" in result.output
    assert client.calls == []


def test_ocr_read_rejects_invalid_use_cls_env_before_daemon_call(cli_runner, fake_daemon_client, monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_USE_CLS", "maybe")
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-invalid-use-cls-env",
                data={"result": [{"text": "银狼"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 2
    assert "Invalid value for TRAIL_OCR_USE_CLS" in result.output
    assert "invalid ocr use_cls from" in result.output
    assert "TRAIL_OCR_USE_CLS: maybe" in result.output
    assert "Invalid value for '--provider'" not in result.output
    assert client.calls == []


def test_ocr_read_rejects_invalid_ocr_mode_env_before_daemon_call(cli_runner, fake_daemon_client, monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_MODE", "warp")
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-invalid-ocr-mode-env",
                data={"result": [{"text": "银狼"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 2
    assert "Invalid value for TRAIL_OCR_MODE" in result.output
    assert "invalid ocr mode from" in result.output
    assert "TRAIL_OCR_MODE:" in result.output
    assert "warp" in result.output
    assert "Invalid value for '--provider'" not in result.output
    assert "Invalid value for TRAIL_OCR_PROVIDER" not in result.output
    assert "Invalid value for '--ocr-mode'" not in result.output
    assert client.calls == []


def test_ocr_read_rejects_invalid_retry_high_env_before_daemon_call(cli_runner, fake_daemon_client, monkeypatch):
    monkeypatch.setenv("TRAIL_OCR_RETRY_HIGH", "sometimes")
    client = fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read-invalid-retry-high-env",
                data={"result": [{"text": "银狼"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 2
    assert "Invalid value for TRAIL_OCR_RETRY_HIGH" in result.output
    assert "invalid ocr retry_high from" in result.output
    assert "TRAIL_OCR_RETRY_HIGH: sometimes" in result.output
    assert "Invalid value for '--provider'" not in result.output
    assert "Invalid value for TRAIL_OCR_PROVIDER" not in result.output
    assert "Invalid value for '--retry-high'" not in result.output
    assert client.calls == []


def test_readme_documents_windows_directml_profile_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "DirectML 目前只作为 Windows 定向的可选加速 profile" in readme
    assert "需要使用项目明确支持的 DirectML 环境 profile" in readme
    assert "默认安装仍以 CPU 基线依赖为准" in readme
    assert "不要在同一环境里模糊共存 `onnxruntime` 与 `onnxruntime-directml`" in readme
    assert "确认当前环境最终只保留预期的 ONNX Runtime 变体" in readme


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
            "image.locate": build_success_response(
                request_id="req-image-locate",
                data={"box": {"left": 1, "top": 2, "width": 3, "height": 4}},
                screenshot=".trail/shots/req-image-locate.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["image", "locate", "demo.png"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok image.locate box=1,2,3,4",
        "shot path=.trail/shots/req-image-locate.png",
    ]
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
    assert result.stdout.splitlines() == [
        "ok image.locate box=1,2,3,4",
        "shot path=.trail/shots/req-image-locate-numpy.png",
    ]


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
            "input.click": build_success_response(
                request_id="req-input-click",
                data={"clicked": [10, 20]},
                screenshot=".trail/shots/req-input-click.png",
            ),
            "input.drag": build_success_response(
                request_id="req-input-drag",
                data={"dragged": [1, 2, 3, 4]},
                screenshot=".trail/shots/req-input-drag.png",
            ),
            "input.key": build_success_response(
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
    assert click_result.stdout.splitlines() == [
        "ok input.click",
        "shot path=.trail/shots/req-input-click.png",
    ]
    assert drag_result.stdout.splitlines() == [
        "ok input.drag",
        "shot path=.trail/shots/req-input-drag.png",
    ]
    assert key_result.stdout.splitlines() == [
        "ok input.key",
        "shot path=.trail/shots/req-input-key.png",
    ]
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
    assert result.stdout.splitlines() == [
        "ok input.click",
        "shot path=.trail/shots/req-input-click.png",
        "warn code=WINDOW_NOT_FOREGROUND msg=输入命令执行后窗口不在前台，本次操作可能失败",
        "ref path=trail/scenes/cw/references/1-1.png sim=0.88",
    ]


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
    assert result.stdout.splitlines() == [
        "ok input.click",
        "shot path=.trail/shots/req-input-click-verbose.png",
        "debug kind=request msg=req-input-click-verbose",
        'debug kind=trace step=click point="[10, 20]"',
    ]
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
            "state.dump": build_success_response(
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
    assert text_result.stdout.splitlines() == [
        "ok state.dump session=session-1 scene=cw last_stage=shop tainted=0",
        "shot path=.trail/shots/req-state-dump.png",
    ]
    assert yaml_result.stdout.splitlines()[0:2] == [
        "ok state.dump session=session-1 scene=cw last_stage=shop tainted=0",
        "shot path=.trail/shots/req-state-dump.png",
    ]
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
    assert result.stdout.splitlines() == [
        "ok input.click",
        "shot path=.trail/shots/req-input-click-backendless.png",
    ]
    assert client.calls == [
        {
            "method": "input.click",
            "payload": {"x": 10, "y": 20},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
        }
    ]


def test_cli_help_exposes_top_level_command_groups(cli_runner):
    result = cli_runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "start" in result.stdout
    assert "session" in result.stdout
    assert "guide" in result.stdout
    assert "window" in result.stdout
    assert "screen" in result.stdout
    assert "ocr" in result.stdout
    assert "image" in result.stdout
    assert "input" in result.stdout
    assert "state" in result.stdout
    assert "cw" in result.stdout
    assert "--verbose" in result.stdout


def test_cw_help_exposes_scene_command_groups(cli_runner):
    result = cli_runner.invoke(app, ["cw", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "货币战争固定流程命令" in normalized
    assert "enter 到首页" in normalized
    assert "start 从首页进入投资环境页" in normalized
    assert "投资环境页的识别/选择/刷新/重开" in normalized
    assert "detect 只重识别当前三张卡" in normalized
    assert "refresh 点击刷新后生成新的三张卡" in normalized
    assert "enter" in _extract_help_command_block(result.output, "enter")
    assert "start" in _extract_help_command_block(result.output, "start")

    expected_blocks = {
        "guide": ("应用", "回顾", "当前对局", "已选攻略"),
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
        block = _extract_help_command_block(result.output, command_name)
        assert command_name in block
        for anchor in anchors:
            assert anchor in block


@pytest.mark.parametrize(
    ("group_name", "expected_anchors"),
    [
        ("portal", ("投资环境页", "detect", "识别", "选择", "refresh", "刷新", "重开")),
        ("invest", ("局内", "invest", "事件")),
        ("stage", ("货币战争内部", "检测", "等待", "登录页", "大世界", "非 CW")),
        ("guide", ("应用", "回顾", "当前对局", "已选攻略")),
        ("shop", ("商店", "购买", "刷新", "关闭")),
        ("event", ("通用", "特殊事件")),
    ],
)
def test_cw_group_help_describes_expected_boundary(cli_runner, group_name, expected_anchors):
    result = cli_runner.invoke(app, ["cw", group_name, "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
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
def test_cw_group_help_avoids_forbidden_phrases(cli_runner, group_name, forbidden_phrase):
    result = cli_runner.invoke(app, ["cw", group_name, "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert forbidden_phrase not in normalized


def test_guide_list_cw_help_uses_chinese_summary_terms(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "攻略ID" in normalized
    assert "攻略标签" in normalized
    assert "最终阵容" in normalized
    assert "has_change_equip" not in normalized
    assert "has_expert" not in normalized
    assert "support_hard" not in normalized
    assert "final_role_cards" not in normalized


def test_cw_portal_help_mentions_invest_portal_terms(cli_runner):
    result = cli_runner.invoke(app, ["cw", "portal", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "投资环境" in normalized
    assert "title=" not in normalized
    assert "desc=" not in normalized
    assert "new=" not in normalized


def test_cw_guide_help_mentions_snapshot_id(cli_runner):
    result = cli_runner.invoke(app, ["cw", "guide", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "攻略快照ID" in normalized
    assert "artifact=" not in normalized


def test_cw_slots_read_help_describes_slot_as_targeted_confirmation(cli_runner):
    result = cli_runner.invoke(app, ["cw", "slots", "read", "--help"])
    normalized = _normalize_help(result.output)
    slot_help = _normalize_help(_extract_help_option_block(result.output, "--slot"))

    assert result.exit_code == 0
    assert "--slot" in result.output
    assert "先看当前截图" in normalized
    assert "有角色但名字不确定" in normalized
    assert "不传 --slot 时仍保留全量读取" in normalized
    assert "定向确认" in slot_help
    assert "全量读取" in slot_help


def test_cw_portal_help_distinguishes_detect_and_refresh(cli_runner):
    result = cli_runner.invoke(app, ["cw", "portal", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "detect" in result.output
    assert "refresh" in result.output
    assert "重新识别并保存当前三张卡" in normalized
    assert "点击刷新后生成新的三张卡" in normalized


def test_cw_portal_detect_help_exposes_snapshot_only_contract(cli_runner):
    result = cli_runner.invoke(app, ["cw", "portal", "detect", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "--session" in result.output
    assert "当前已在投资环境页时重新识别并保存 portal snapshot" in normalized
    assert "只重建当前三张卡识别结果" in normalized
    assert "不点击、不刷新、不重开" in normalized
    assert "推进流程" not in normalized


def test_strategy_group_is_visible_in_cw_help(cli_runner):
    result = cli_runner.invoke(app, ["cw", "--help"])
    block = _extract_help_command_block(result.output, "strategy")

    assert result.exit_code == 0
    assert "strategy" in block
    assert "局内投资策略页" in block
    assert "单卡刷新" in block


def test_strategy_group_help_describes_snapshot_and_single_card_refresh(cli_runner):
    result = cli_runner.invoke(app, ["cw", "strategy", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "detect" in result.output
    assert "select" in result.output
    assert "refresh" in result.output
    assert "只重建当前三张策略卡快照" in normalized
    assert "只刷新指定卡" in normalized
    assert "不做整页刷新" in normalized


def test_strategy_group_refresh_help_requires_card_idx(cli_runner):
    result = cli_runner.invoke(app, ["cw", "strategy", "refresh", "--help"])
    normalized = _normalize_help(result.output)
    card_idx_help = _normalize_help(_extract_help_option_block(result.output, "--card-idx"))

    assert result.exit_code == 0
    assert "--card-idx" in result.output
    assert "只刷新指定卡" in normalized
    assert "不做整页刷新" in normalized
    assert "required" in card_idx_help.lower()


def test_invest_help_marks_compatibility_entry(cli_runner):
    result = cli_runner.invoke(app, ["cw", "invest", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "兼容" in normalized
    assert "粗粒度入口" in normalized
    assert "不用于投资策略页" in normalized


def test_cw_enter_help_exposes_home_only_contract(cli_runner):
    result = cli_runner.invoke(app, ["cw", "enter", "--help"])

    assert result.exit_code == 0
    assert "--session" in result.stdout
    assert "--mode" not in result.stdout
    assert "--difficulty" not in result.stdout
    assert "--battle-mode" not in result.stdout


def test_window_help_exposes_launch_command(cli_runner):
    result = cli_runner.invoke(app, ["window", "--help"])

    assert result.exit_code == 0
    assert "attach" in result.stdout
    assert "launch" in result.stdout
