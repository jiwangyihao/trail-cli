from __future__ import annotations

import importlib
import json
import sys

import numpy as np
import pytest

from trail.cli import app
from trail.runtime.model import Box
from tests.support.fake_daemon import build_success_response


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


def test_ocr_read_returns_runtime_payload(cli_runner, fake_daemon_client, tmp_path):
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
            "payload": {},
            "workspace_root": str(tmp_path),
            "session_id": None,
            "verbose": False,
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

    assert result.exit_code == 0
    assert "enter" in result.stdout
    assert "guide" in result.stdout
    assert "stage" in result.stdout
    assert "slots" in result.stdout
    assert "shop" in result.stdout
    assert "crystals" in result.stdout
    assert "hand" in result.stdout
    assert "replenish" in result.stdout
    assert "invest" in result.stdout
    assert "encounter" in result.stdout
    assert "fortune" in result.stdout
    assert "boss-preview" in result.stdout
    assert "battle" in result.stdout
    assert "settle" in result.stdout
    assert "event" in result.stdout


def test_cw_enter_help_exposes_enum_contract(cli_runner):
    result = cli_runner.invoke(app, ["cw", "enter", "--help"])

    assert result.exit_code == 0
    assert "--mode" in result.stdout
    assert "[new|continue]" in result.stdout
    assert "--difficulty" in result.stdout
    assert "[lowest|current|highest]" in result.stdout
    assert "--battle-mode" in result.stdout
    assert "[standard|overclock]" in result.stdout


def test_window_help_exposes_launch_command(cli_runner):
    result = cli_runner.invoke(app, ["window", "--help"])

    assert result.exit_code == 0
    assert "attach" in result.stdout
    assert "launch" in result.stdout
