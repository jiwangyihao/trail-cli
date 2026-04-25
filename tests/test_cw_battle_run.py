from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from trail.core.errors import TrailError
from trail.session.models import SessionModel


def load_cw_battle_module():
    try:
        return importlib.import_module("trail.scenes.cw.battle")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.battle: {exc}")


def _ocr_piece(text: str) -> dict[str, str]:
    return {"text": text}


CAPTURE_KEYS = ("from_x", "from_y", "to_x", "to_y")
HEADLINE_CAPTURE_KEY = (0.30, 0.10, 0.70, 0.28)
ROUND_CAPTURE_KEY = (0.22, 0.22, 0.48, 0.42)
STATS_CAPTURE_KEY = (0.20, 0.22, 0.60, 0.52)


def _capture_key(capture: object) -> object:
    if isinstance(capture, dict):
        unexpected_keys = set(capture) - set(CAPTURE_KEYS)
        if unexpected_keys:
            raise TypeError(f"unexpected capture keys: {sorted(unexpected_keys)}")
        return tuple(capture.get(key) for key in CAPTURE_KEYS)
    return capture


class FakeRuntime:
    def __init__(self, *, ocr_map: dict[object, list[object]] | None = None, locate_map: dict[str, object] | None = None):
        self.ocr_map = ocr_map or {}
        self.locate_map = locate_map or {}
        self.ocr_calls: list[object] = []
        self.locate_calls: list[str] = []

    def ocr(self, **kwargs):
        capture = kwargs.get("capture")
        self.ocr_calls.append(_capture_key(capture))
        return list(self.ocr_map.get(_capture_key(capture), []))

    def locate(self, template: str, **kwargs):
        self.locate_calls.append(str(template))
        return self.locate_map.get(str(template))


class ScriptedBattleRuntime:
    def __init__(
        self,
        states: list[str],
        *,
        stable_stage: str = "shop",
        settle_text: str = "挑战成功",
        battle_start_text: str = "开始战斗",
        sleep_advances_from: tuple[str, ...] = ("battle_progress",),
    ):
        self.states = list(states)
        self.index = 0
        self.stable_stage = stable_stage
        self.settle_text = settle_text
        self.battle_start_text = battle_start_text
        self.sleep_advances_from = set(sleep_advances_from)
        self.actions: list[str] = []
        self.clicks: list[tuple[int, int]] = []

    @property
    def state(self) -> str:
        return self.states[min(self.index, len(self.states) - 1)]

    def _settle_result(self) -> str:
        return "lose" if self.settle_text == "挑战失败" else "win"

    def _ocr_map_for_state(self) -> dict[object, list[object]]:
        if self.state == "battle_start":
            return {None: [_ocr_piece(self.battle_start_text)]}
        if self.state == "battle_progress":
            return {None: [_ocr_piece("自动战斗"), _ocr_piece("暂停")]}
        if self.state == "settle_entry":
            return {
                None: [_ocr_piece(self.settle_text)],
                HEADLINE_CAPTURE_KEY: [_ocr_piece(self.settle_text)],
                ROUND_CAPTURE_KEY: [_ocr_piece("第1-1回合")],
                STATS_CAPTURE_KEY: [_ocr_piece("生命 82"), _ocr_piece("金币 4"), _ocr_piece("经验 2")],
            }
        if self.state == "settle_followup":
            return {None: [_ocr_piece("下一步")]}
        if self.state == "game_over":
            return {None: [_ocr_piece("游戏结束")]}
        return {None: []}

    def ocr(self, **kwargs):
        capture = _capture_key(kwargs.get("capture"))
        return list(self._ocr_map_for_state().get(capture, []))

    def locate(self, template: str, **kwargs):
        del template, kwargs
        return None

    def click_point(self, x: int, y: int):
        self.clicks.append((x, y))

    def advance(self) -> None:
        if self.index < len(self.states) - 1:
            self.index += 1

    def tick(self) -> None:
        if self.state in self.sleep_advances_from:
            self.advance()

    def detect_stage(self) -> str | None:
        if self.state == "stable_stage":
            return self.stable_stage
        if self.state == "game_over":
            return "game_over"
        if self.state == "settle_entry":
            return "settle"
        return None

    def run_action(self, name: str, *, advance: bool = True) -> None:
        self.actions.append(name)
        self.click_point(640, 360)
        if advance:
            self.advance()


class FakeClock:
    def __init__(self, *, step: float = 1.0):
        self.current = 0.0
        self.step = step
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        value = self.current
        self.current += self.step
        return value

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.current += seconds


def _patch_run_loop(
    monkeypatch,
    battle_scene,
    runtime: ScriptedBattleRuntime,
    *,
    clock: FakeClock | None = None,
    start_advances: bool = True,
    continue_advances: bool = True,
    settle_advances: bool = True,
):
    active_clock = clock or FakeClock()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: runtime.detect_stage)
    monkeypatch.setattr(
        battle_scene,
        "_start_battle",
        lambda _runtime: runtime.run_action("start", advance=start_advances),
        raising=False,
    )
    monkeypatch.setattr(
        battle_scene,
        "_continue_after_settlement",
        lambda _runtime: runtime.run_action("continue", advance=continue_advances),
        raising=False,
    )
    monkeypatch.setattr(
        battle_scene,
        "_advance_settlement_page",
        lambda _runtime: runtime.run_action("next", advance=settle_advances),
        raising=False,
    )

    def fake_sleep(seconds: float) -> None:
        active_clock.sleep(seconds)
        runtime.tick()

    monkeypatch.setattr(battle_scene, "sleep", fake_sleep, raising=False)
    monkeypatch.setattr(battle_scene, "monotonic", active_clock.monotonic, raising=False)
    return active_clock


def build_session(tmp_path: Path) -> SessionModel:
    return SessionModel(
        session_id="session-cw-battle-run",
        workspace=tmp_path,
        window_binding={"title": "崩坏：星穹铁道", "hwnd": 1},
        created_at="2026-04-21T00:00:00Z",
    )


def test_classify_cw_battle_page_requires_positive_battle_anchor(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [_ocr_piece("随机 OCR 文本")], HEADLINE_CAPTURE_KEY: []})

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "unknown"


def test_classify_cw_battle_page_detects_positive_battle_anchor(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [_ocr_piece("自动战斗"), _ocr_piece("2倍速"), _ocr_piece("暂停")]})

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "battle_progress"


def test_classify_cw_battle_page_detects_battle_start(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [_ocr_piece("开始战斗")]})

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "battle_start"


def test_classify_cw_battle_page_treats_chuzhan_preparation_page_as_battle_start(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [_ocr_piece("出战")]})
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda runtime: lambda: "preparation")

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "battle_start"


def test_classify_cw_battle_page_treats_detected_preparation_as_battle_start_without_ocr(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [], HEADLINE_CAPTURE_KEY: []})
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda runtime: lambda: "preparation")

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "battle_start"


def test_classify_cw_battle_page_prefers_settlement_entry_over_battle_hud(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(
        ocr_map={
            None: [_ocr_piece("自动战斗"), _ocr_piece("挑战成功")],
            HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战成功")],
        }
    )

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "settle_entry"


def test_classify_cw_battle_page_treats_continue_challenge_as_settlement_entry(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [_ocr_piece("继续挑战")], HEADLINE_CAPTURE_KEY: [_ocr_piece("继续挑战")]})

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "settle_entry"


def test_classify_cw_battle_page_detects_settlement_followup(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [_ocr_piece("下一步")]})

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "settle_followup"


def test_classify_cw_battle_page_detects_game_over(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [_ocr_piece("游戏结束")], HEADLINE_CAPTURE_KEY: []})

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path))

    assert result == "game_over"


def test_classify_cw_battle_page_maps_stable_stage_detector_hits(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda runtime: lambda: "shop")

    result = battle_scene.classify_cw_battle_page(FakeRuntime(), session=build_session(tmp_path))

    assert result == "stable_stage"


def test_classify_cw_battle_page_does_not_treat_boss_preview_as_stable_stage(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda runtime: lambda: "boss_preview")

    result = battle_scene.classify_cw_battle_page(FakeRuntime(), session=build_session(tmp_path))

    assert result != "stable_stage"


def test_classify_cw_battle_page_maps_detector_settle_to_settlement_entry(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda runtime: lambda: "settle")

    result = battle_scene.classify_cw_battle_page(FakeRuntime(), session=build_session(tmp_path))

    assert result == "settle_entry"


def test_parse_cw_settlement_summary_reads_stable_fields():
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(
        ocr_map={
            HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战成功")],
            ROUND_CAPTURE_KEY: [_ocr_piece("第1-1回合")],
            STATS_CAPTURE_KEY: [_ocr_piece("生命 82"), _ocr_piece("金币 4"), _ocr_piece("经验 2")],
        }
    )

    result = battle_scene.parse_cw_settlement_summary(runtime)

    assert result == {
        "result": "win",
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }


def test_parse_cw_settlement_summary_omits_missing_secondary_fields():
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战失败")]})

    result = battle_scene.parse_cw_settlement_summary(runtime)

    assert result == {"result": "lose", "settle_text": "挑战失败"}


def test_parse_cw_settlement_summary_raises_when_headline_unreadable():
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={HEADLINE_CAPTURE_KEY: [_ocr_piece("结算奖励")], None: [_ocr_piece("下一步")]})

    with pytest.raises(TrailError) as exc_info:
        battle_scene.parse_cw_settlement_summary(runtime)

    assert exc_info.value.code == "CW_SETTLEMENT_UNREADABLE"


def test_mark_cw_stage_stale_clears_last_stage(tmp_path: Path):
    from trail.scenes.cw.models import ensure_cw_state
    from trail.scenes.cw.stage import mark_cw_stage_stale

    session = build_session(tmp_path)
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False}
    session.last_stage = {"scene": "cw", "value": "shop"}

    mark_cw_stage_stale(session)

    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_run_cw_battle_clicks_start_then_returns_completed_summary(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["battle_start", "battle_progress", "settle_entry", "settle_followup", "stable_stage"])
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=30)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "shop",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }
    assert runtime.actions == ["start", "continue", "next"]
    assert session.scene_state["cw"]["stage"] == {"value": "shop", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "shop"}


def test_run_cw_battle_clicks_chuzhan_start_then_returns_completed_summary(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(
        ["battle_start", "battle_progress", "settle_entry", "settle_followup", "stable_stage"],
        battle_start_text="出战",
    )
    _patch_run_loop(monkeypatch, battle_scene, runtime)
    monkeypatch.setattr(
        battle_scene,
        "build_cw_stage_detector",
        lambda _runtime: lambda: "preparation" if runtime.state == "battle_start" else runtime.detect_stage(),
    )

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=30)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "shop",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }
    assert runtime.actions == ["start", "continue", "next"]
    assert session.scene_state["cw"]["stage"] == {"value": "shop", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "shop"}


def test_run_cw_battle_starts_from_detected_preparation_when_ocr_misses_start_text(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(
        ["battle_start", "battle_progress", "settle_entry", "settle_followup", "stable_stage"],
        battle_start_text="",
    )
    _patch_run_loop(monkeypatch, battle_scene, runtime)
    monkeypatch.setattr(
        battle_scene,
        "build_cw_stage_detector",
        lambda _runtime: lambda: "preparation" if runtime.state == "battle_start" else runtime.detect_stage(),
    )

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=30)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "shop",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }
    assert runtime.actions == ["start", "continue", "next"]
    assert session.scene_state["cw"]["stage"] == {"value": "shop", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "shop"}


def test_run_cw_battle_returns_settle_timeout_summary_when_budget_exhausted(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "settle_followup"], sleep_advances_from=())
    _patch_run_loop(monkeypatch, battle_scene, runtime, settle_advances=False)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=2)

    assert result == {
        "status": "in_progress",
        "result": "win",
        "stage": "settle",
        "stale": True,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
        "timeout_seconds": 2,
    }
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_run_cw_battle_finishes_when_starting_from_settle_followup(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_followup", "stable_stage"])
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=10)

    assert result == {
        "status": "completed",
        "stage": "shop",
        "stale": False,
        "in_battle": False,
    }
    assert runtime.actions == ["next"]
    assert session.scene_state["cw"]["stage"] == {"value": "shop", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "shop"}


def test_run_cw_battle_finishes_from_continue_only_settle_entry_without_summary(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "stable_stage"], settle_text="继续挑战")
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=10)

    assert result == {
        "status": "completed",
        "stage": "shop",
        "stale": False,
        "in_battle": False,
    }
    assert runtime.actions == ["continue"]
    assert session.scene_state["cw"]["stage"] == {"value": "shop", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "shop"}


def test_run_cw_battle_returns_settle_timeout_when_starting_from_settle_followup(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_followup"], sleep_advances_from=())
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result == {
        "status": "in_progress",
        "stage": "settle",
        "stale": True,
        "in_battle": False,
        "timeout_seconds": 1,
    }
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_run_cw_battle_returns_settle_timeout_for_continue_only_settle_entry_without_summary(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry"], settle_text="继续挑战", sleep_advances_from=())
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result == {
        "status": "in_progress",
        "stage": "settle",
        "stale": True,
        "in_battle": False,
        "timeout_seconds": 1,
    }
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_run_cw_battle_returns_in_battle_timeout_when_only_progress_seen(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["battle_progress"], sleep_advances_from=())
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=2)

    assert result == {
        "status": "in_progress",
        "stale": True,
        "in_battle": True,
        "timeout_seconds": 2,
    }
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_run_cw_battle_returns_game_over_when_chain_ends(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "game_over"])
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=10)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "game_over",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }
    assert session.scene_state["cw"]["stage"] == {"value": "game_over", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "game_over"}
