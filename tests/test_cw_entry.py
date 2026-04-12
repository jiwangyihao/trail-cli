from __future__ import annotations

import json

from trail.cli import app
from trail.scenes.cw.entry import enter_cw
from trail.session.store import SessionStore


def test_enter_cw_records_entry_snapshot_and_invalidates_stage(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    refreshed = enter_cw(session, mode="continue", difficulty="highest", battle_mode="overclock")

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "continue",
        "difficulty": "highest",
        "battle_mode": "overclock",
    }
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}


def test_cw_enter_cli_persists_entry_snapshot(cli_runner, fake_runtime, fake_session, tmp_path):
    result = cli_runner.invoke(app, ["cw", "enter", "--session", fake_session, "--mode", "new"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert payload["screenshot"]

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["entry"] == payload["data"]
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_result == {
        "command": "cw.enter",
        "ok": True,
        "data": payload["data"],
        "error": None,
    }
