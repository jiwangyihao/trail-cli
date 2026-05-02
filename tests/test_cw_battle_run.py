from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from trail.core.errors import TrailError
from trail.scenes.cw.models import ensure_cw_state
from trail.session.models import SessionModel


def load_cw_battle_module():
    try:
        return importlib.import_module("trail.scenes.cw.battle")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.battle: {exc}")


def _ocr_piece(text: str) -> dict[str, str]:
    return {"text": text}


def _ocr_button_piece(text: str, *, left: int, top: int, width: int = 120, height: int = 36) -> dict[str, object]:
    return {"text": text, "box": {"left": left, "top": top, "width": width, "height": height}}


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
        settle_headline: str | None = None,
        settle_page_texts: list[str] | None = None,
        settle_detect_stage: str | None = "settle",
        battle_start_text: str = "开始战斗",
        sleep_advances_from: tuple[str, ...] = ("battle_progress",),
    ):
        self.states = list(states)
        self.index = 0
        self.stable_stage = stable_stage
        self.settle_text = settle_text
        self.settle_headline = settle_headline or settle_text
        self.settle_page_texts = settle_page_texts
        self.settle_detect_stage = settle_detect_stage
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
        if self.state == "preparation":
            return {None: [_ocr_piece(self.battle_start_text)] if self.battle_start_text else []}
        if self.state == "battle_progress":
            return {None: [_ocr_piece("自动战斗"), _ocr_piece("暂停")]}
        if self.state == "settle_entry":
            page_texts = self.settle_page_texts or [self.settle_text]
            return {
                None: [_ocr_piece(text) for text in page_texts],
                HEADLINE_CAPTURE_KEY: [_ocr_piece(self.settle_headline)],
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
            return self.settle_detect_stage
        if self.state == "preparation":
            return "preparation"
        return None

    def run_action(self, name: str, *, advance: bool = True) -> None:
        self.actions.append(name)
        self.click_point(640, 360)
        if advance:
            self.advance()


class TeamCountConfirmRuntime(ScriptedBattleRuntime):
    def __init__(self):
        super().__init__(["battle_start"], sleep_advances_from=())
        self.wait_calls: list[str] = []

    def wait_img(self, template: str, timeout: int = 3, interval: float = 0.5):
        del timeout, interval
        self.wait_calls.append(str(template))
        return {"left": 100, "top": 200, "width": 60, "height": 20}

    def ocr(self, **kwargs):
        capture = _capture_key(kwargs.get("capture"))
        if capture is None:
            return [_ocr_piece("开始战斗")]
        return [
            {"text": "提示", "box": {"left": 820, "top": 300, "width": 100, "height": 36}},
            {"text": "可出战角色人数未达上限，是否确认出战？", "box": {"left": 620, "top": 430, "width": 680, "height": 36}},
            {"text": "取消", "box": {"left": 380, "top": 600, "width": 100, "height": 36}},
            {"text": "确认", "box": {"left": 1160, "top": 600, "width": 100, "height": 36}},
        ]


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


class CostlySettleRuntime(ScriptedBattleRuntime):
    def __init__(
        self,
        states: list[str],
        *,
        clock: FakeClock,
        page_ocr_cost: float,
        capture_ocr_cost: float,
        **kwargs,
    ):
        super().__init__(states, **kwargs)
        self.clock = clock
        self.page_ocr_cost = page_ocr_cost
        self.capture_ocr_cost = capture_ocr_cost
        self.page_ocr_calls = 0
        self.capture_ocr_calls: list[object] = []

    def ocr(self, **kwargs):
        capture = _capture_key(kwargs.get("capture"))
        if capture is None:
            self.page_ocr_calls += 1
            self.clock.current += self.page_ocr_cost
        else:
            self.capture_ocr_calls.append(capture)
            self.clock.current += self.capture_ocr_cost
        return super().ocr(**kwargs)

    def _ocr_map_for_state(self) -> dict[object, list[object]]:
        mapping = super()._ocr_map_for_state()
        if self.state == "settle_entry":
            page_texts = self.settle_page_texts or [self.settle_text]
            page_pieces: list[object] = []
            for text in page_texts:
                if text == "继续挑战":
                    page_pieces.append(_ocr_button_piece("继续挑战", left=900, top=884))
                elif text == "下一步":
                    page_pieces.append(_ocr_button_piece("下一步", left=900, top=884))
                else:
                    page_pieces.append(_ocr_piece(text))
            mapping[None] = page_pieces
        if self.state == "settle_followup":
            mapping[None] = [_ocr_button_piece("下一步", left=900, top=884)]
        return mapping

    def click_point(self, x: int, y: int):
        super().click_point(x, y)
        if self.state == "settle_entry":
            self.actions.append("continue")
            self.advance()
        elif self.state == "settle_followup":
            self.actions.append("next")
            self.advance()


class LayerTransitionRuntime(ScriptedBattleRuntime):
    def _ocr_map_for_state(self) -> dict[object, list[object]]:
        mapping = super()._ocr_map_for_state()
        if self.state == "layer_transition":
            mapping[None] = [_ocr_piece("点击空白处继续"), _ocr_piece("位面")]
        return mapping

    def detect_stage(self) -> str | None:
        if self.state == "layer_transition":
            return "layer_transition"
        return super().detect_stage()

    def click_point(self, x: int, y: int):
        super().click_point(x, y)
        if self.state == "layer_transition":
            self.actions.append("blank_continue")
            self.advance()


def _patch_stage_and_clock_only(monkeypatch, battle_scene, runtime: ScriptedBattleRuntime, *, clock: FakeClock):
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: runtime.detect_stage)
    monkeypatch.setattr(battle_scene, "monotonic", clock.monotonic, raising=False)

    def fake_sleep(seconds: float) -> None:
        clock.sleep(seconds)
        runtime.tick()

    monkeypatch.setattr(battle_scene, "sleep", fake_sleep, raising=False)


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
        lambda _runtime, observation=None: runtime.run_action("continue", advance=continue_advances),
        raising=False,
    )
    monkeypatch.setattr(
        battle_scene,
        "_advance_settlement_page",
        lambda _runtime, observation=None: runtime.run_action("next", advance=settle_advances),
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

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path), detected_stage=None)

    assert result == "unknown"


def test_classify_cw_battle_page_detects_positive_battle_anchor(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [_ocr_piece("自动战斗"), _ocr_piece("2倍速"), _ocr_piece("暂停")]})

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path), detected_stage=None)

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


def test_classify_cw_battle_page_treats_detected_preparation_as_battle_start_when_ocr_misses(
    tmp_path: Path, monkeypatch
):
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


def test_classify_cw_battle_page_treats_challenge_end_with_continue_as_settlement_entry(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(
        ocr_map={
            None: [_ocr_piece("挑战结束"), _ocr_piece("继续挑战")],
            HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战结束")],
        }
    )

    result = battle_scene.classify_cw_battle_page(
        runtime,
        session=build_session(tmp_path),
        detected_stage=None,
    )

    assert result == "settle_entry"


def test_classify_cw_battle_page_uses_single_page_ocr_snapshot(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()

    class RuntimeSpy(FakeRuntime):
        def __init__(self):
            super().__init__(ocr_map={None: [_ocr_piece("挑战成功"), _ocr_piece("继续挑战")]})
            self.page_ocr_calls = 0

        def ocr(self, **kwargs):
            capture = _capture_key(kwargs.get("capture"))
            if capture is None:
                self.page_ocr_calls += 1
            return super().ocr(**kwargs)

    runtime = RuntimeSpy()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path), detected_stage=None)

    assert result == "settle_entry"
    assert runtime.page_ocr_calls == 1


def test_classify_cw_battle_page_uses_observation_headline_when_page_ocr_misses(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [], HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战成功")]})

    result = battle_scene.classify_cw_battle_page(runtime, session=build_session(tmp_path), detected_stage=None)

    assert result == "settle_entry"
    assert runtime.ocr_calls == [None, HEADLINE_CAPTURE_KEY]


def test_continue_after_settlement_observation_uses_observed_button_without_fallback(monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = CostlySettleRuntime(
        ["settle_entry"],
        clock=FakeClock(step=0.0),
        page_ocr_cost=0.0,
        capture_ocr_cost=0.0,
        settle_text="挑战成功",
        settle_page_texts=["挑战成功", "继续挑战"],
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    observation = battle_scene.observe_cw_battle_page(runtime, detected_stage=None)

    monkeypatch.setattr(
        battle_scene,
        "build_cw_battle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback battle continuer used"),
    )

    battle_scene._continue_after_settlement(runtime, observation=observation)

    assert runtime.clicks[-1] == (960, 902)
    assert runtime.page_ocr_calls == 1


def test_continue_after_settlement_observation_ignores_off_region_button_and_uses_fallback(monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = CostlySettleRuntime(
        ["settle_entry"],
        clock=FakeClock(step=0.0),
        page_ocr_cost=0.0,
        capture_ocr_cost=0.0,
        settle_text="挑战成功",
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    monkeypatch.setattr(
        runtime,
        "_ocr_map_for_state",
        lambda: {None: [_ocr_button_piece("继续挑战", left=100, top=100)]},
    )
    observation = battle_scene.observe_cw_battle_page(runtime, detected_stage=None)
    monkeypatch.setattr(
        battle_scene,
        "build_cw_battle_continuer",
        lambda _runtime: lambda: runtime.actions.append("fallback"),
    )

    battle_scene._continue_after_settlement(runtime, observation=observation)

    assert runtime.clicks == []
    assert runtime.actions == ["fallback"]


def test_advance_settlement_page_observation_uses_observed_button_without_fallback(monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = CostlySettleRuntime(
        ["settle_followup"],
        clock=FakeClock(step=0.0),
        page_ocr_cost=0.0,
        capture_ocr_cost=0.0,
        sleep_advances_from=(),
    )
    observation = battle_scene.observe_cw_battle_page(runtime, detected_stage=None)

    monkeypatch.setattr(
        battle_scene,
        "build_cw_settle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback settle continuer used"),
    )

    battle_scene._advance_settlement_page(runtime, observation=observation)

    assert runtime.clicks[-1] == (960, 902)
    assert runtime.page_ocr_calls == 1


def test_continue_after_settlement_observation_accepts_tuple_ocr_piece_without_fallback(monkeypatch):
    battle_scene = load_cw_battle_module()
    runtime = CostlySettleRuntime(
        ["settle_entry"],
        clock=FakeClock(step=0.0),
        page_ocr_cost=0.0,
        capture_ocr_cost=0.0,
        settle_text="挑战成功",
        settle_page_texts=["挑战成功"],
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    monkeypatch.setattr(
        runtime,
        "_ocr_map_for_state",
        lambda: {
            None: [([[900, 884], [1020, 884], [1020, 920], [900, 920]], "继续挑战", 0.99)],
            HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战成功")],
        },
    )
    observation = battle_scene.observe_cw_battle_page(runtime, detected_stage=None)

    monkeypatch.setattr(
        battle_scene,
        "build_cw_battle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback battle continuer used"),
    )

    battle_scene._continue_after_settlement(runtime, observation=observation)

    assert runtime.clicks[-1] == (960, 902)
    assert runtime.page_ocr_calls == 1


def test_classify_cw_battle_page_does_not_treat_challenge_end_without_continue_as_settlement_entry(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(
        ocr_map={
            None: [_ocr_piece("挑战结束")],
            HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战结束")],
        }
    )

    result = battle_scene.classify_cw_battle_page(
        runtime,
        session=build_session(tmp_path),
        detected_stage=None,
    )

    assert result == "unknown"


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


def test_classify_cw_battle_page_treats_layer_transition_as_battle_flow_state(
    tmp_path: Path, monkeypatch
):
    battle_scene = load_cw_battle_module()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: "layer_transition")

    result = battle_scene.classify_cw_battle_page(FakeRuntime(), session=build_session(tmp_path))

    assert result == "layer_transition"


def test_classify_cw_battle_page_trusts_detected_layer_transition_without_ocr(tmp_path: Path):
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(ocr_map={None: [], HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战成功")]})

    result = battle_scene.classify_cw_battle_page(
        runtime,
        session=build_session(tmp_path),
        detected_stage="layer_transition",
    )

    assert result == "layer_transition"
    assert runtime.ocr_calls == []


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


def test_parse_cw_settlement_summary_treats_challenge_end_with_continue_as_win():
    battle_scene = load_cw_battle_module()
    runtime = FakeRuntime(
        ocr_map={
            None: [_ocr_piece("挑战结束"), _ocr_piece("继续挑战")],
            HEADLINE_CAPTURE_KEY: [_ocr_piece("挑战结束")],
            ROUND_CAPTURE_KEY: [_ocr_piece("1-4 X战斗")],
            STATS_CAPTURE_KEY: [_ocr_piece("小队生命值82"), _ocr_piece("获得金币总览")],
        }
    )

    result = battle_scene.parse_cw_settlement_summary(runtime)

    assert result == {
        "result": "win",
        "round": "1-4",
        "hp": 82,
        "settle_text": "挑战结束",
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
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False, "status": {"stale": False, "level": 7}}
    session.last_stage = {"scene": "cw", "value": "shop"}

    mark_cw_stage_stale(session)

    assert session.scene_state["cw"]["stage"] == {"stale": True, "status": {"stale": False, "level": 7}}
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


def test_run_cw_battle_stops_on_next_round_preparation_after_settle(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(
        ["battle_start", "battle_progress", "settle_entry", "settle_followup", "preparation"],
        battle_start_text="出战",
    )
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=30)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "preparation",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }
    assert runtime.actions == ["start", "continue", "next"]
    assert session.scene_state["cw"]["stage"] == {"value": "preparation", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "preparation"}


def test_run_cw_battle_stops_on_detected_next_round_preparation_when_ocr_misses_start_text(
    tmp_path: Path, monkeypatch
):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(
        ["preparation", "battle_progress", "settle_entry", "settle_followup", "preparation"],
        battle_start_text="",
    )
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=30)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "preparation",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }
    assert runtime.actions == ["start", "continue", "next"]
    assert session.scene_state["cw"]["stage"] == {"value": "preparation", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "preparation"}


def test_run_cw_battle_keeps_first_preparation_detector_hit_when_second_read_misses(
    tmp_path: Path, monkeypatch
):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(
        ["settle_entry", "settle_followup", "preparation"],
        battle_start_text="",
    )
    _patch_run_loop(monkeypatch, battle_scene, runtime)
    preparation_reads = 0

    def build_racy_stage_detector(_runtime):
        def detect_stage():
            nonlocal preparation_reads
            if runtime.state != "preparation":
                return runtime.detect_stage()
            preparation_reads += 1
            return "preparation" if preparation_reads == 1 else None

        return detect_stage

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", build_racy_stage_detector)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=30)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "preparation",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }
    assert runtime.actions == ["continue", "next"]
    assert preparation_reads == 1
    assert session.scene_state["cw"]["stage"] == {"value": "preparation", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "preparation"}


def test_run_cw_battle_does_not_recheck_detector_when_first_preparation_read_misses(
    tmp_path: Path, monkeypatch
):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(
        ["settle_entry", "settle_followup", "preparation"],
        battle_start_text="",
    )
    _patch_run_loop(monkeypatch, battle_scene, runtime)
    preparation_reads = 0

    def build_racy_stage_detector(_runtime):
        def detect_stage():
            nonlocal preparation_reads
            if runtime.state != "preparation":
                return runtime.detect_stage()
            preparation_reads += 1
            return None if preparation_reads == 1 else "preparation"

        return detect_stage

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", build_racy_stage_detector)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=30)

    assert result == {
        "status": "completed",
        "result": "win",
        "stage": "preparation",
        "stale": False,
        "in_battle": False,
        "round": "1-1",
        "hp": 82,
        "coins": 4,
        "exp": 2,
        "settle_text": "挑战成功",
    }
    assert runtime.actions == ["continue", "next"]
    assert preparation_reads == 2
    assert session.scene_state["cw"]["stage"] == {"value": "preparation", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "preparation"}


def test_run_cw_battle_raises_when_start_shows_team_count_confirm_dialog(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = TeamCountConfirmRuntime()
    clock = FakeClock()
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: runtime.detect_stage)
    monkeypatch.setattr(battle_scene, "monotonic", clock.monotonic, raising=False)

    with pytest.raises(TrailError) as exc_info:
        battle_scene.run_cw_battle(session, runtime=runtime, timeout=2)

    assert exc_info.value.code == "CW_BATTLE_TEAM_COUNT_INSUFFICIENT"
    assert "可出战角色人数未达上限" in str(exc_info.value)
    assert "检查场上人数" in str(exc_info.value)
    assert runtime.clicks == [(130, 210), (430, 618)]


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


def test_run_cw_battle_persists_last_battle_round(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "settle_followup", "stable_stage"])
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=10)

    assert result["round"] == "1-1"
    assert ensure_cw_state(session)["metrics"]["last_battle_round"] == "1-1"


def test_run_cw_battle_persists_last_battle_round_for_settle_timeout(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "settle_followup", "unknown"], sleep_advances_from=("unknown",))

    _patch_run_loop(monkeypatch, battle_scene, runtime, clock=FakeClock(step=0.1))
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["stage"] == "settle"
    assert ensure_cw_state(session)["metrics"]["last_battle_round"] == "1-1"
    assert ensure_cw_state(session)["stage"] == {"stale": True}
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


def test_run_cw_battle_resumes_challenge_end_settlement_entry_when_detector_misses(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(
        ["settle_entry", "settle_followup", "stable_stage"],
        settle_headline="挑战结束",
        settle_page_texts=["挑战结束", "继续挑战"],
        settle_detect_stage=None,
    )
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=10)

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
        "settle_text": "挑战结束",
    }
    assert runtime.actions == ["continue", "next"]
    assert session.scene_state["cw"]["stage"] == {"value": "shop", "stale": False}
    assert session.last_stage == {"scene": "cw", "value": "shop"}


def test_run_cw_battle_resumes_from_settle_into_layer_transition_then_shop(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = LayerTransitionRuntime(["settle_entry", "layer_transition", "stable_stage"])
    _patch_run_loop(monkeypatch, battle_scene, runtime)
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: runtime.detect_stage)
    monkeypatch.setattr(
        battle_scene,
        "_continue_after_settlement",
        lambda _runtime, observation=None: runtime.run_action("continue"),
    )
    monkeypatch.setattr(
        battle_scene,
        "build_cw_battle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback battle continuer used"),
    )
    monkeypatch.setattr(
        battle_scene,
        "build_cw_settle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback settle continuer used"),
    )

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=15)

    assert result["status"] == "completed"
    assert result["stage"] == "shop"
    assert runtime.actions == ["continue", "blank_continue"]
    assert runtime.clicks[-1] == (960, 903)


def test_run_cw_battle_timeout_on_layer_transition_keeps_in_progress(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = LayerTransitionRuntime(["layer_transition", "layer_transition"], sleep_advances_from=())
    _patch_run_loop(monkeypatch, battle_scene, runtime)
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: runtime.detect_stage)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["stage"] == "layer_transition"
    assert result["stale"] is True
    assert result["in_battle"] is False


def test_run_cw_battle_advances_detected_layer_transition_without_ocr(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)

    class NoOcrLayerTransitionRuntime(LayerTransitionRuntime):
        def __init__(self, states: list[str], *, sleep_advances_from: tuple[str, ...]):
            super().__init__(states, sleep_advances_from=sleep_advances_from)
            self.ocr_calls = 0

        def ocr(self, **kwargs):
            del kwargs
            self.ocr_calls += 1
            return []

    runtime = NoOcrLayerTransitionRuntime(["layer_transition", "stable_stage"], sleep_advances_from=())
    _patch_stage_and_clock_only(monkeypatch, battle_scene, runtime=runtime, clock=FakeClock(step=0.0))

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=30)

    assert result == {"status": "completed", "stage": "shop", "stale": False, "in_battle": False}
    assert runtime.actions == ["blank_continue"]
    assert runtime.ocr_calls == 0


def test_run_cw_battle_resumes_settle_entry_before_short_deadline(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    clock = FakeClock(step=0.0)
    runtime = CostlySettleRuntime(
        ["settle_entry", "settle_followup", "stable_stage"],
        clock=clock,
        page_ocr_cost=3.0,
        capture_ocr_cost=1.0,
        settle_text="挑战成功",
        settle_page_texts=["挑战成功", "继续挑战"],
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    _patch_stage_and_clock_only(monkeypatch, battle_scene, runtime, clock=clock)
    monkeypatch.setattr(
        battle_scene,
        "build_cw_battle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback battle continuer used"),
    )
    monkeypatch.setattr(
        battle_scene,
        "build_cw_settle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback settle continuer used"),
    )

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=15)

    assert result["status"] == "completed"
    assert result["stage"] == "shop"
    assert runtime.actions == ["continue", "next"]
    assert clock.current < 15


def test_run_cw_battle_short_deadline_resume_hint_then_settle_still_advances_continue_and_next(
    tmp_path: Path, monkeypatch
):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    clock = FakeClock(step=0.0)
    runtime = CostlySettleRuntime(
        ["settle_entry", "settle_followup", "stable_stage"],
        clock=clock,
        page_ocr_cost=3.0,
        capture_ocr_cost=1.0,
        settle_text="挑战成功",
        settle_page_texts=["挑战成功", "继续挑战"],
        settle_detect_stage=None,
        sleep_advances_from=(),
    )
    _patch_stage_and_clock_only(monkeypatch, battle_scene, runtime, clock=clock)
    monkeypatch.setattr(
        battle_scene,
        "build_cw_battle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback battle continuer used"),
    )
    monkeypatch.setattr(
        battle_scene,
        "build_cw_settle_continuer",
        lambda _runtime: lambda: pytest.fail("fallback settle continuer used"),
    )

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=15)

    assert result["status"] == "completed"
    assert result["stage"] == "shop"
    assert runtime.actions == ["continue", "next"]
    assert ensure_cw_state(session)["battle_resume"] == {}


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


def test_run_cw_battle_tolerates_unknown_opening_when_resume_hint_is_set(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    runtime = ScriptedBattleRuntime(["unknown", "battle_progress"], sleep_advances_from=("unknown",))

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=5)

    assert result["status"] == "in_progress"
    assert result["in_battle"] is True


def test_run_cw_battle_resume_hint_completes_when_opening_on_preparation(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    runtime = ScriptedBattleRuntime(["preparation"], battle_start_text="出战")
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=5)

    assert result == {
        "status": "completed",
        "stage": "preparation",
        "stale": False,
        "in_battle": False,
    }
    assert runtime.actions == []
    assert ensure_cw_state(session).get("battle_resume") == {}


def test_run_cw_battle_resume_hint_ocr_only_preparation_completes_when_detector_misses(
    tmp_path: Path, monkeypatch
):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    runtime = ScriptedBattleRuntime(["preparation"], battle_start_text="出战")

    _patch_run_loop(monkeypatch, battle_scene, runtime)
    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=5)

    assert result == {
        "status": "completed",
        "stage": "preparation",
        "stale": False,
        "in_battle": False,
    }
    assert runtime.actions == []
    assert ensure_cw_state(session).get("battle_resume") == {}


def test_run_cw_battle_completes_after_layer_transition_when_ocr_sees_preparation(
    tmp_path: Path, monkeypatch
):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(
        ["layer_transition", "preparation"],
        battle_start_text="备战阶段",
        sleep_advances_from=(),
    )

    _patch_run_loop(monkeypatch, battle_scene, runtime)
    monkeypatch.setattr(
        battle_scene,
        "build_cw_stage_detector",
        lambda _runtime: lambda: "layer_transition" if runtime.state == "layer_transition" else None,
    )
    monkeypatch.setattr(battle_scene, "_advance_layer_transition", lambda _runtime: runtime.run_action("blank_continue"))

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=5)

    assert result == {
        "status": "completed",
        "stage": "preparation",
        "stale": False,
        "in_battle": False,
    }
    assert runtime.actions == ["blank_continue"]
    assert ensure_cw_state(session).get("battle_resume") == {}


def test_run_cw_battle_sets_resume_hint_after_in_battle_timeout(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["battle_progress"])

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["in_battle"] is True
    assert ensure_cw_state(session)["battle_resume"] == {"in_battle_hint": True}


def test_run_cw_battle_does_not_set_resume_hint_for_settle_timeout(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    runtime = ScriptedBattleRuntime(["settle_entry", "settle_followup", "unknown"], sleep_advances_from=("unknown",))

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: None)
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=1)

    assert result["status"] == "in_progress"
    assert result["stage"] == "settle"
    assert result["in_battle"] is False
    assert ensure_cw_state(session).get("battle_resume") == {}


def test_run_cw_battle_clears_resume_hint_after_completed_result(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    runtime = ScriptedBattleRuntime(["battle_progress", "stable_stage"])

    monkeypatch.setattr(battle_scene, "build_cw_stage_detector", lambda _runtime: lambda: "shop")
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    result = battle_scene.run_cw_battle(session, runtime=runtime, timeout=5)

    assert result["status"] == "completed"
    assert ensure_cw_state(session).get("battle_resume") == {}


def test_run_cw_battle_clears_resume_hint_when_settlement_parse_failure_raises(tmp_path: Path, monkeypatch):
    battle_scene = load_cw_battle_module()
    session = build_session(tmp_path)
    ensure_cw_state(session)["battle_resume"] = {"in_battle_hint": True}
    runtime = ScriptedBattleRuntime(["settle_entry"])
    _patch_run_loop(monkeypatch, battle_scene, runtime)

    def fail_settlement_summary(_runtime, observation=None):
        del observation
        raise TrailError("CW_SETTLEMENT_UNREADABLE", "无法识别货币战争结算结果")

    monkeypatch.setattr(battle_scene, "parse_cw_settlement_summary", fail_settlement_summary)

    with pytest.raises(TrailError) as exc_info:
        battle_scene.run_cw_battle(session, runtime=runtime, timeout=5)

    assert exc_info.value.code == "CW_SETTLEMENT_UNREADABLE"
    assert ensure_cw_state(session).get("battle_resume") == {}


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
