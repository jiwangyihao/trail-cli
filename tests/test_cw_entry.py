from __future__ import annotations

import json

from trail.cli import app
from trail.runtime.model import Box
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.entry import enter_cw
from trail.session.store import SessionStore


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box(alias: str, *, left: int, top: int, width: int = 40, height: int = 20) -> Box:
    return Box(left=left, top=top, width=width, height=height, source=_asset(alias))


def _install_template_runtime(runtime, *, locate_results: dict[str, object] | None = None, wait_results: dict[str, object] | None = None) -> None:
    locate_results = locate_results or {}
    wait_results = wait_results or {}

    def locate(template: str, **kwargs):
        del kwargs
        runtime.locate_calls.append(template)
        return locate_results.get(template)

    def wait_img(template: str, timeout: int = 10, interval: float = 0.5):
        del timeout, interval
        runtime.wait_calls.append(template)
        return wait_results.get(template)

    runtime.locate = locate
    runtime.wait_img = wait_img


def test_enter_cw_records_entry_snapshot_and_invalidates_stage(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"steps": [1], "stale": False},
        "stage": {"stale": False, "value": "shop"},
    }

    refreshed = enter_cw(session, mode="continue", difficulty="highest", battle_mode="overclock")

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "continue",
        "difficulty": "highest",
        "battle_mode": "overclock",
    }
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.scene_state["cw"]["slots"] == {"stale": True, "hand": ["希儿"]}
    assert refreshed.scene_state["cw"]["shop"] == {"stale": True, "opened": True}
    assert refreshed.scene_state["cw"]["sell_plan"] == {"stale": True}


def test_enter_cw_runs_new_mode_ui_flow_from_start_related_pages(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.last_stage = {"scene": "cw", "value": "shop"}

    start_box = _box("entry.start", left=10, top=20)
    enter_box = _box("entry.new", left=80, top=90)
    highest_box = _box("entry.difficulty.highest", left=140, top=160)
    start_game_box = _box("entry.start_game", left=220, top=260)
    next_step_box = _box("stage.settle", left=320, top=360)
    blank_box = _box("stage.boss_preview", left=360, top=400)
    invest_box = _box("entry.invest_environment", left=420, top=460)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = Runtime()
    _install_template_runtime(
        runtime,
        locate_results={
            _asset("entry.start"): start_box,
            _asset("stage.preparation"): None,
            _asset("stage.settle"): next_step_box,
            _asset("stage.invest"): None,
        },
        wait_results={
            _asset("entry.new"): enter_box,
            _asset("entry.difficulty.highest"): highest_box,
            _asset("entry.start_game"): start_game_box,
            _asset("stage.settle"): next_step_box,
            _asset("stage.boss_preview"): blank_box,
            _asset("entry.invest_environment"): invest_box,
        },
    )

    refreshed = enter_cw(session, mode="new", difficulty="highest", battle_mode="overclock", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "new",
        "difficulty": "highest",
        "battle_mode": "overclock",
    }
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.last_stage is None
    assert runtime.clicks == [
        start_box.center,
        (300, 450),
        enter_box.center,
        highest_box.center,
        start_game_box.center,
        next_step_box.center,
        blank_box.center,
    ]
    assert runtime.wait_calls == [
        _asset("entry.new"),
        _asset("entry.difficulty.highest"),
        _asset("entry.start_game"),
        _asset("stage.settle"),
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]


def test_enter_cw_runs_world_to_currency_wars_entry_chain_before_continue_flow(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    menu_box = _box("entry.menu", left=12, top=24)
    cosmic_box = _box("entry.cosmic_strife", left=36, top=48)
    start_box = _box("entry.start", left=84, top=96)
    continue_box = _box("entry.continue", left=120, top=132)
    blank_box = _box("stage.boss_preview", left=168, top=180)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.keys: list[tuple[str, int, float]] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            self.keys.append((key, presses, interval))

    runtime = Runtime()
    _install_template_runtime(
        runtime,
        locate_results={
            _asset("stage.preparation"): None,
            _asset("entry.start"): None,
            _asset("entry.new"): None,
            _asset("entry.continue"): None,
            _asset("stage.settle"): None,
            _asset("stage.invest"): None,
        },
        wait_results={
            _asset("entry.menu"): menu_box,
            _asset("entry.cosmic_strife"): cosmic_box,
            _asset("entry.start"): start_box,
            _asset("entry.continue"): continue_box,
            _asset("stage.boss_preview"): blank_box,
        },
    )

    refreshed = enter_cw(session, mode="continue", difficulty="current", battle_mode="standard", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert runtime.keys == [("f4", 1, 0.2)]
    assert runtime.clicks == [
        cosmic_box.center,
        (464, 324),
        (1494, 884),
        start_box.center,
        (300, 250),
        continue_box.center,
        blank_box.center,
    ]
    assert runtime.wait_calls == [
        _asset("entry.menu"),
        _asset("entry.cosmic_strife"),
        _asset("entry.start"),
        _asset("entry.continue"),
        _asset("stage.boss_preview"),
    ]


def test_enter_cw_does_not_treat_generic_settle_template_as_top_level_entry_recovery(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    menu_box = _box("entry.menu", left=20, top=32)
    cosmic_box = _box("entry.cosmic_strife", left=44, top=56)
    start_box = _box("entry.start", left=92, top=104)
    continue_box = _box("entry.continue", left=128, top=140)
    blank_box = _box("stage.boss_preview", left=176, top=188)
    next_step_box = _box("stage.settle", left=224, top=236)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []
            self.clicks: list[tuple[int, int]] = []
            self.keys: list[tuple[str, int, float]] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            self.keys.append((key, presses, interval))

    runtime = Runtime()
    _install_template_runtime(
        runtime,
        locate_results={
            _asset("stage.preparation"): None,
            _asset("entry.start"): None,
            _asset("entry.new"): None,
            _asset("entry.continue"): None,
            _asset("stage.settle"): next_step_box,
            _asset("stage.invest"): None,
        },
        wait_results={
            _asset("entry.menu"): menu_box,
            _asset("entry.cosmic_strife"): cosmic_box,
            _asset("entry.start"): start_box,
            _asset("entry.continue"): continue_box,
            _asset("stage.boss_preview"): blank_box,
        },
    )

    refreshed = enter_cw(session, mode="continue", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert runtime.keys == [("f4", 1, 0.2)]
    assert next_step_box.center not in runtime.clicks
    assert runtime.clicks == [
        cosmic_box.center,
        (464, 324),
        (1494, 884),
        start_box.center,
        (300, 250),
        continue_box.center,
        blank_box.center,
    ]


def test_cw_enter_cli_persists_entry_snapshot(cli_runner, fake_runtime, fake_session, tmp_path):
    continue_box = _box("entry.continue", left=60, top=80)
    blank_box = _box("stage.boss_preview", left=160, top=180)
    _install_template_runtime(
        fake_runtime,
        locate_results={
            _asset("stage.preparation"): None,
            _asset("entry.continue"): continue_box,
        },
        wait_results={
            _asset("entry.continue"): continue_box,
            _asset("stage.boss_preview"): blank_box,
        },
    )

    result = cli_runner.invoke(app, ["cw", "enter", "--session", fake_session, "--mode", "continue"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert payload["screenshot"]
    assert fake_runtime.clicks == [continue_box.center, blank_box.center]

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["entry"] == payload["data"]
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_result == {
        "command": "cw.enter",
        "ok": True,
        "data": payload["data"],
        "error": None,
    }
