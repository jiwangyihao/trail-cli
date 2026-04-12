from __future__ import annotations

import json

from trail.cli import app
from trail.session.store import SessionStore


def test_window_attach_returns_envelope_and_binding(cli_runner, fake_runtime):
    result = cli_runner.invoke(app, ["window", "attach", "--window-title", "Demo Window"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {"title": "Demo Window", "hwnd": 123}
    assert payload["screenshot"]


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
    assert click_payload["data"] == {"clicked": [10.0, 20.0]}
    assert drag_payload["data"] == {"dragged": [1.0, 2.0, 3.0, 4.0]}
    assert key_payload["data"] == {"key": "space", "presses": 2}
    assert fake_runtime.clicks == [(10.0, 20.0)]
    assert fake_runtime.drags == [(1.0, 2.0, 3.0, 4.0)]
    assert fake_runtime.keys == [("space", 2, 0.2)]


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

    monkeypatch.setattr(state_cmd, "session_store", SessionStore(tmp_path / ".trail" / "sessions"))

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
