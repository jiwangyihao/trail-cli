from __future__ import annotations

import importlib
import json
import sys

import numpy as np
import pytest

from trail.cli import app
from trail.runtime.model import Box
from trail.session.store import SessionStore


def test_window_attach_returns_envelope_and_binding(cli_runner, fake_runtime):
    result = cli_runner.invoke(app, ["window", "attach", "--window-title", "Demo Window"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"title": "Demo Window", "hwnd": 123}
    assert payload["screenshot"]


def test_window_launch_returns_structured_payload(cli_runner, fake_runtime, tmp_path, monkeypatch):
    import trail.commands.window as window_cmd

    executable = tmp_path / "StarRail.exe"
    captured: dict[str, object] = {}

    def fake_launch_game(*, game_path, channel, launch_args, use_cmd):
        captured.update(
            {
                "game_path": game_path,
                "channel": channel,
                "launch_args": launch_args,
                "use_cmd": use_cmd,
            }
        )
        return {
            "started": True,
            "already_running": False,
            "path": str(game_path),
            "channel": channel,
            "args": list(launch_args),
        }

    monkeypatch.setattr(window_cmd, "launch_game", fake_launch_game, raising=False)

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
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "started": True,
        "already_running": False,
        "path": str(executable),
        "channel": "bilibili",
        "args": ["-popupwindow"],
    }
    assert captured == {
        "game_path": executable,
        "channel": "bilibili",
        "launch_args": ["-popupwindow"],
        "use_cmd": True,
    }


def test_screen_shot_returns_envelope_and_screenshot(cli_runner, fake_runtime):
    result = cli_runner.invoke(app, ["screen", "shot"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"captured": True}
    assert payload["screenshot"]


def test_ocr_read_returns_runtime_payload(cli_runner, fake_runtime):
    fake_runtime.ocr_result = [{"text": "银狼"}]

    result = cli_runner.invoke(app, ["ocr", "read"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["result"] == [{"text": "银狼"}]


def test_image_locate_returns_box_payload(cli_runner, fake_runtime):
    fake_runtime.locate_result = {"left": 1, "top": 2, "width": 3, "height": 4}

    result = cli_runner.invoke(app, ["image", "locate", "demo.png"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["box"] == {"left": 1, "top": 2, "width": 3, "height": 4}


def test_image_locate_serializes_numpy_box_values(cli_runner, fake_runtime):
    fake_runtime.locate_result = Box(np.int64(1), np.int64(2), np.int64(3), np.int64(4), source="demo.png")

    result = cli_runner.invoke(app, ["image", "locate", "demo.png"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["box"] == {
        "left": 1,
        "top": 2,
        "width": 3,
        "height": 4,
        "source": "demo.png",
    }


def test_image_wait_returns_error_when_template_missing(cli_runner, fake_runtime):
    result = cli_runner.invoke(app, ["image", "wait", "missing.png"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == {"code": "IMAGE_NOT_FOUND", "message": "未找到 missing.png"}
    assert payload["screenshot"]


def test_input_click_drag_and_key_return_envelopes(cli_runner, fake_runtime):
    click_result = cli_runner.invoke(app, ["input", "click", "10", "20"])
    drag_result = cli_runner.invoke(app, ["input", "drag", "1", "2", "3", "4"])
    key_result = cli_runner.invoke(app, ["input", "key", "space", "--presses", "2"])

    click_payload = json.loads(click_result.stdout)
    drag_payload = json.loads(drag_result.stdout)
    key_payload = json.loads(key_result.stdout)

    assert click_result.exit_code == 0
    assert drag_result.exit_code == 0
    assert key_result.exit_code == 0
    assert click_payload["data"] == {"clicked": [10, 20]}
    assert drag_payload["data"] == {"dragged": [1, 2, 3, 4]}
    assert key_payload["data"] == {"key": "space", "presses": 2}
    assert fake_runtime.clicks == [(10, 20)]
    assert fake_runtime.drags == [(1, 2, 3, 4)]
    assert fake_runtime.keys == [("space", 2, 0.2)]


def test_input_click_returns_runtime_warnings_and_reference_matches(cli_runner, fake_runtime):
    fake_runtime.warnings = [
        {
            "code": "WINDOW_NOT_FOREGROUND",
            "message": "输入命令执行后窗口不在前台，本次操作可能失败",
        }
    ]
    fake_runtime.references = [{"path": "trail/scenes/cw/references/1-1.png", "similarity": 0.88}]

    result = cli_runner.invoke(app, ["input", "click", "10", "20"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["warnings"] == [
        {
            "code": "WINDOW_NOT_FOREGROUND",
            "message": "输入命令执行后窗口不在前台，本次操作可能失败",
        }
    ]
    assert payload["references"] == [{"path": "trail/scenes/cw/references/1-1.png", "similarity": 0.88}]
    assert payload["debug"] is None


def test_top_level_verbose_emits_runtime_debug_trace(cli_runner, fake_runtime):
    fake_runtime.trace = [{"step": "click", "point": [10, 20]}]

    result = cli_runner.invoke(app, ["--verbose", "input", "click", "10", "20"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["debug"] == {"trace": [{"step": "click", "point": [10, 20]}]}


def test_state_dump_returns_session_snapshot(cli_runner, fake_runtime, fake_session):
    result = cli_runner.invoke(app, ["state", "dump", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["session_id"] == fake_session
    assert payload["data"]["scene_state"] == {}
    assert payload["data"]["last_result"] is None


def test_state_dump_returns_structured_error_for_missing_session(cli_runner, fake_runtime, tmp_path, monkeypatch):
    import trail.commands.state as state_cmd

    monkeypatch.setattr(
        state_cmd,
        "session_store_factory",
        lambda: SessionStore(tmp_path / ".trail" / "sessions"),
        raising=False,
    )

    result = cli_runner.invoke(app, ["state", "dump", "--session", "deadbeefdeadbeefdeadbeefdeadbeef"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["data"] == {}
    assert payload["error"] == {
        "code": "SESSION_NOT_FOUND",
        "message": "session not found: deadbeefdeadbeefdeadbeefdeadbeef",
    }
    assert payload["screenshot"]
    assert "Traceback" not in result.stdout


def test_state_dump_uses_session_window_binding_for_runtime(cli_runner, tmp_path, monkeypatch):
    import trail.commands.state as state_cmd

    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.create(window_binding={"title": "自定义窗口", "hwnd": 456})
    built_titles: list[str] = []

    class RuntimeSpy:
        def __init__(self, shot_path):
            self._shot = shot_path

        def capture_after_action(self, optional: bool = False):
            return self._shot

    monkeypatch.setattr(state_cmd, "session_store_factory", lambda: store, raising=False)
    monkeypatch.setattr(
        state_cmd,
        "runtime_factory",
        lambda **kwargs: built_titles.append(kwargs["window_title"]) or RuntimeSpy(tmp_path / "state-after.png"),
        raising=False,
    )

    result = cli_runner.invoke(app, ["state", "dump", "--session", session.session_id])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert built_titles == ["自定义窗口"]


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


def test_input_click_returns_structured_error_when_backend_missing(cli_runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "pyautogui", None)

    result = cli_runner.invoke(app, ["input", "click", "10", "20"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "INPUT_BACKEND_UNAVAILABLE",
        "message": "pyautogui backend unavailable",
    }


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
