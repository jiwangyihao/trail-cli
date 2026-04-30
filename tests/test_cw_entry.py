from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.core.errors import TrailError
from trail.runtime.model import Box
from trail.runtime.ocr_config import OcrRequestConfig
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.entry import (
    ENTRY_ENEMY_DIFFICULTY_REGION,
    enter_cw,
    is_cw_exact_difficulty_token,
    parse_cw_start_difficulty_token,
    read_entry_enemy_difficulty,
    resolve_entry_rank_from_enemy_difficulty,
)
from trail.session.store import SessionStore


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box(alias: str, *, left: int, top: int, width: int = 40, height: int = 20) -> Box:
    return Box(left=left, top=top, width=width, height=height, source=_asset(alias))


def _install_template_runtime(
    runtime,
    *,
    locate_results: dict[str, object] | None = None,
    wait_results: dict[str, object] | None = None,
    ocr_results: object | None = None,
) -> None:
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

    def ocr(**kwargs):
        del kwargs
        return ocr_results or []

    runtime.locate = locate
    runtime.wait_img = wait_img
    runtime.ocr = ocr


@pytest.mark.parametrize("value", ["A0-1", "A3-5", "A7-3", "A8-40"])
def test_is_cw_exact_difficulty_token_accepts_public_ax_x_range(value: str):
    assert is_cw_exact_difficulty_token(value) is True


@pytest.mark.parametrize("value", ["current", "A3-6", "A8-41", "A9-1", "A7_3", "a7-3", "A7-03", "A8-040", "A0-00"])
def test_is_cw_exact_difficulty_token_rejects_invalid_tokens(value: str):
    assert is_cw_exact_difficulty_token(value) is False


def test_parse_cw_start_difficulty_token_accepts_exact_rank():
    assert parse_cw_start_difficulty_token("A7-3") == {
        "kind": "exact",
        "token": "A7-3",
        "rank_code": "A7",
        "rank_name": "资本帝王",
        "layer": 3,
        "target_enemy_difficulty": 51,
        "global_layer_ordinal": 36,
    }


@pytest.mark.parametrize(
    ("token", "target_enemy_difficulty", "global_layer_ordinal"),
    [
        ("A8-1", 61, 43),
        ("A8-40", 108, 82),
    ],
)
def test_parse_cw_start_difficulty_token_maps_a8_authoritative_values(
    token: str,
    target_enemy_difficulty: int,
    global_layer_ordinal: int,
):
    parsed = parse_cw_start_difficulty_token(token)

    assert parsed is not None
    assert parsed["rank_code"] == "A8"
    assert parsed["target_enemy_difficulty"] == target_enemy_difficulty
    assert parsed["global_layer_ordinal"] == global_layer_ordinal


def test_parse_cw_start_difficulty_token_accepts_preset_token():
    assert parse_cw_start_difficulty_token("current") == {"kind": "preset", "token": "current"}


@pytest.mark.parametrize("raw", ["AX-X", "A3-6", "A8-41", "A9-1", "A7_3", "a7-3", "A7-03", "A8-040", "A0-00"])
def test_parse_cw_start_difficulty_token_rejects_invalid_exact_rank(raw: str):
    assert parse_cw_start_difficulty_token(raw) is None


def test_resolve_entry_rank_from_enemy_difficulty_maps_exact_band():
    assert resolve_entry_rank_from_enemy_difficulty(39) == {
        "token": "A6-1",
        "rank_code": "A6",
        "rank_name": "投资大师",
        "layer": 1,
        "enemy_difficulty": 39,
        "global_layer_ordinal": 27,
    }
    assert resolve_entry_rank_from_enemy_difficulty(51) == {
        "token": "A7-3",
        "rank_code": "A7",
        "rank_name": "资本帝王",
        "layer": 3,
        "enemy_difficulty": 51,
        "global_layer_ordinal": 36,
    }
    assert resolve_entry_rank_from_enemy_difficulty(61) == {
        "token": "A8-1",
        "rank_code": "A8",
        "rank_name": "财富造物主",
        "layer": 1,
        "enemy_difficulty": 61,
        "global_layer_ordinal": 43,
    }
    assert resolve_entry_rank_from_enemy_difficulty(99) == {
        "token": "A8-31",
        "rank_code": "A8",
        "rank_name": "财富造物主",
        "layer": 31,
        "enemy_difficulty": 99,
        "global_layer_ordinal": 73,
    }
    assert resolve_entry_rank_from_enemy_difficulty(108) == {
        "token": "A8-40",
        "rank_code": "A8",
        "rank_name": "财富造物主",
        "layer": 40,
        "enemy_difficulty": 108,
        "global_layer_ordinal": 82,
    }


@pytest.mark.parametrize("value", [4, 5, 9, 10, 58, 59, 60, 71, 84, 97])
def test_resolve_entry_rank_from_enemy_difficulty_rejects_unmapped_gap(value: int):
    assert resolve_entry_rank_from_enemy_difficulty(value) is None


def test_read_entry_enemy_difficulty_joins_split_digits_left_to_right():
    seen: list[dict[str, object]] = []

    def ocr(**kwargs):
        seen.append(kwargs)
        return [
            {"text": "9", "box": {"left": 62, "top": 10, "width": 12, "height": 20}},
            {"text": "3", "box": {"left": 40, "top": 12, "width": 12, "height": 20}},
        ]

    runtime = SimpleNamespace(ocr=ocr)

    assert read_entry_enemy_difficulty(runtime) == 39
    assert seen == [
        {
            "capture": ENTRY_ENEMY_DIFFICULTY_REGION,
            "ocr": OcrRequestConfig(ocr_mode="high", retry_high="never"),
        }
    ]


def test_read_entry_enemy_difficulty_accepts_points_mapping_geometry():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "39", "points": ((40, 10), (64, 10), (64, 30), (40, 30))},
        ]
    )

    assert read_entry_enemy_difficulty(runtime) == 39


@pytest.mark.parametrize(
    "piece",
    [
        {"text": "39", "center": {"x": 52, "y": 20}},
        {"text": "39", "center": (52, 20)},
        {"text": "39", "left": 40, "top": 10, "width": 24, "height": 20},
    ],
)
def test_read_entry_enemy_difficulty_accepts_center_and_direct_geometry(piece: dict[str, object]):
    runtime = SimpleNamespace(ocr=lambda **kwargs: [piece, {"text": "噪声"}])

    assert read_entry_enemy_difficulty(runtime) == 39


def test_read_entry_enemy_difficulty_ignores_geometryless_noise_and_fails_on_gap_value():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "4", "box": {"left": 40, "top": 12, "width": 12, "height": 20}},
            {"text": "噪声"},
        ]
    )

    with pytest.raises(TrailError) as exc_info:
        read_entry_enemy_difficulty(
            runtime,
            requested_difficulty="A0-1",
            target_enemy_difficulty=1,
            after_input=True,
        )

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert str(exc_info.value) == "cw start cannot safely continue difficulty selection; ask agent to enter error recovery"
    assert exc_info.value.data == {
        "requested_difficulty": "A0-1",
        "target_enemy_difficulty": 1,
        "current_enemy_difficulty": 4,
        "reason": "ocr_unmapped",
        "page": "entry.new",
    }
    assert exc_info.value.known_failure_after_save is True
    assert exc_info.value.completed_after_side_effect is True


def test_read_entry_enemy_difficulty_prefers_longest_piece_when_overlapping_with_single_digits():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "39", "box": {"left": 40, "top": 10, "width": 24, "height": 20}},
            {"text": "3", "box": {"left": 40, "top": 10, "width": 12, "height": 20}},
            {"text": "9", "box": {"left": 52, "top": 10, "width": 12, "height": 20}},
        ]
    )

    assert read_entry_enemy_difficulty(runtime, requested_difficulty="A6-1", target_enemy_difficulty=39) == 39



def test_read_entry_enemy_difficulty_prefers_longest_piece_when_overlapping_with_single_digit_fragment():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "3", "box": {"left": 43, "top": 14, "width": 43, "height": 61}},
            {"text": "36", "box": {"left": 72, "top": 12, "width": 56, "height": 68}},
        ]
    )

    assert read_entry_enemy_difficulty(runtime, requested_difficulty="A5-7", target_enemy_difficulty=36) == 36


def test_read_entry_enemy_difficulty_rejects_conflicting_overlapping_multi_digit_pieces():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "39", "box": {"left": 40, "top": 10, "width": 24, "height": 20}},
            {"text": "36", "box": {"left": 44, "top": 10, "width": 24, "height": 20}},
        ]
    )

    with pytest.raises(TrailError) as exc_info:
        read_entry_enemy_difficulty(runtime, requested_difficulty="A6-1", target_enemy_difficulty=39)

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert str(exc_info.value) == "cw start cannot safely continue difficulty selection; ask agent to enter error recovery"
    assert exc_info.value.data == {
        "requested_difficulty": "A6-1",
        "target_enemy_difficulty": 39,
        "current_enemy_difficulty": None,
        "reason": "ocr_conflict",
        "page": "entry.new",
    }
    assert not hasattr(exc_info.value, "known_failure_after_save")
    assert not hasattr(exc_info.value, "completed_after_side_effect")


def test_read_entry_enemy_difficulty_merges_overlapping_single_digit_pieces():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "5", "box": {"left": 40, "top": 10, "width": 18, "height": 20}},
            {"text": "1", "box": {"left": 54, "top": 10, "width": 12, "height": 20}},
        ]
    )

    assert read_entry_enemy_difficulty(runtime, requested_difficulty="A5-1", target_enemy_difficulty=30) == 51


def test_read_entry_enemy_difficulty_raises_missing_when_no_boxed_digits():
    runtime = SimpleNamespace(
        ocr=lambda **kwargs: [
            {"text": "噪声"},
            {"text": "abc", "box": {"left": 40, "top": 12, "width": 12, "height": 20}},
        ]
    )

    with pytest.raises(TrailError) as exc_info:
        read_entry_enemy_difficulty(runtime, requested_difficulty="lowest", target_enemy_difficulty=1)

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data == {
        "requested_difficulty": "lowest",
        "target_enemy_difficulty": 1,
        "current_enemy_difficulty": None,
        "reason": "ocr_missing",
        "page": "entry.new",
    }
    assert not hasattr(exc_info.value, "known_failure_after_save")
    assert not hasattr(exc_info.value, "completed_after_side_effect")


def test_select_exact_difficulty_resets_to_highest_then_coarse_then_step(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []
            self._values = iter([49, 108, 63, 51])

        def locate(self, template: str, **kwargs):
            del kwargs
            if template == _asset("entry.difficulty.highest"):
                return _box("entry.difficulty.highest", left=1300, top=960)
            if template == _asset("entry.difficulty.lowest"):
                return _box("entry.difficulty.lowest", left=1600, top=960)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            value = next(self._values)
            return [{"text": str(value), "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    entry_module._select_difficulty(runtime, difficulty="A7-3")

    assert runtime.clicks == [(1320, 970), (1620, 970)]
    assert runtime.drags == [(960, 810, 960, 0)]


def test_select_exact_difficulty_noops_when_current_rank_already_matches_target(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            return [{"text": "51", "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    entry_module._select_difficulty(runtime, difficulty="A7-3")

    assert runtime.clicks == []
    assert runtime.drags == []


def test_select_battle_mode_does_not_wait_after_click(monkeypatch: pytest.MonkeyPatch):
    import trail.scenes.cw.entry as entry_module

    sleep_calls: list[float] = []

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: sleep_calls.append(seconds))
    runtime = Runtime()

    entry_module._select_battle_mode(runtime, battle_mode="standard")

    assert runtime.clicks == [entry_module.STANDARD_BATTLE_MODE_POINT]
    assert sleep_calls == []


def test_enter_new_game_waits_before_first_difficulty_ocr(monkeypatch: pytest.MonkeyPatch):
    import trail.scenes.cw.entry as entry_module

    sleep_calls: list[float] = []
    selected: list[str] = []

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = Runtime()

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: sleep_calls.append(seconds))
    monkeypatch.setattr(entry_module, "_handle_boss_info_flow", lambda runtime: None)
    monkeypatch.setattr(entry_module, "_consume_click_blank_prompt", lambda runtime: None)
    monkeypatch.setattr(entry_module, "_handle_invest_environment_flow", lambda runtime: None)
    monkeypatch.setattr(
        entry_module,
        "_wait",
        lambda runtime, alias: {
            "entry.new": {"left": 1570, "top": 955, "width": 48, "height": 32},
            "entry.start_game": {"left": 1648, "top": 958, "width": 104, "height": 34},
        }[alias],
    )
    monkeypatch.setattr(
        entry_module,
        "_select_difficulty",
        lambda runtime, *, difficulty, after_input=False: selected.append(f"{difficulty}:{after_input}"),
    )

    entry_module._enter_new_game(runtime, difficulty="A5-1")

    assert runtime.clicks == [(1594, 971), (1700, 975)]
    assert sleep_calls == [2.0]
    assert selected == ["A5-1:True"]


def test_select_exact_difficulty_recovery_when_highest_button_missing(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def ocr(self, **kwargs):
            del kwargs
            return [{"text": "49", "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(Runtime(), difficulty="A8-1")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "highest_reset_unavailable"


def test_select_exact_difficulty_initial_failure_marks_known_failure_when_prior_input_done(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def ocr(self, **kwargs):
            del kwargs
            return []

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(Runtime(), difficulty="A5-1", after_input=True)

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.known_failure_after_save is True
    assert exc_info.value.completed_after_side_effect is True


def test_select_exact_difficulty_recovery_when_coarse_drag_makes_no_progress(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.drags: list[tuple[int, int, int, int]] = []
            self._values = iter([49, 108, 108])

        def locate(self, template: str, **kwargs):
            del kwargs
            if template == _asset("entry.difficulty.highest"):
                return _box("entry.difficulty.highest", left=1300, top=960)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del x, y, kwargs

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            value = next(self._values)
            return [{"text": str(value), "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(Runtime(), difficulty="A7-3")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "coarse_no_progress"


def test_select_exact_difficulty_recovery_when_step_click_makes_no_progress(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []
            self._values = iter([57, 57])

        def locate(self, template: str, **kwargs):
            del kwargs
            if template == _asset("entry.difficulty.lowest"):
                return _box("entry.difficulty.lowest", left=1600, top=960)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            value = next(self._values)
            return [{"text": str(value), "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(runtime, difficulty="A7-1")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "step_no_progress"
    assert runtime.clicks == [(1620, 970)]
    assert runtime.drags == []


def test_select_exact_difficulty_recovery_when_coarse_budget_exhausted(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.drags: list[tuple[int, int, int, int]] = []
            self._values = iter([
                108,
                *range(107, 98, -1),
                *range(96, 86, -1),
                *range(83, 73, -1),
                *range(70, 67, -1),
            ])

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del x, y, kwargs

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            value = next(self._values)
            return [{"text": str(value), "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(runtime, difficulty="A0-1")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "iteration_budget_exhausted"
    assert runtime.drags == [(960, 810, 960, 0)] * 20


def test_select_lowest_recovers_immediately_when_arrow_present_but_ocr_missing(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []

        def locate(self, template: str, **kwargs):
            del kwargs
            if template == _asset("entry.difficulty.lowest"):
                return _box("entry.difficulty.lowest", left=1600, top=960)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            return []

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(runtime, difficulty="lowest")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "ocr_missing"
    assert runtime.clicks == []
    assert runtime.drags == []


def test_select_lowest_noops_when_arrow_missing_and_current_rank_is_a0_1(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            return [{"text": "1", "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    entry_module._select_difficulty(runtime, difficulty="lowest")

    assert runtime.clicks == []
    assert runtime.drags == []


def test_select_lowest_recovers_when_arrow_missing_near_bottom_but_not_at_a0_1(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []

        def locate(self, template: str, **kwargs):
            del template, kwargs
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            return [{"text": "8", "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    sleep_calls: list[float] = []
    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: sleep_calls.append(seconds))
    runtime = Runtime()

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(runtime, difficulty="lowest")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "lowest_arrow_missing_non_bottom"
    assert sleep_calls == [entry_module.ENTRY_DIFFICULTY_SETTLE_SECONDS]
    assert runtime.clicks == []
    assert runtime.drags == []


def test_select_lowest_waits_once_then_steps_when_arrow_reappears_after_wait(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    runtime = SimpleNamespace()
    arrow_box = _box("entry.difficulty.lowest", left=1600, top=960)
    locate_results = iter([None, arrow_box, None])
    step_calls: list[dict[str, object]] = []
    read_calls = {"count": 0}
    stepped = {"value": False}
    sleep_calls: list[float] = []

    def fake_locate(actual_runtime, alias: str):
        assert actual_runtime is runtime
        assert alias == "entry.difficulty.lowest"
        return next(locate_results)

    def fake_read_current_entry_rank(actual_runtime, *, requested_difficulty: str, target_enemy_difficulty, after_input: bool):
        assert actual_runtime is runtime
        assert requested_difficulty == "lowest"
        assert target_enemy_difficulty is None
        read_calls["count"] += 1
        if stepped["value"]:
            return {"token": "A0-1", "enemy_difficulty": 1, "global_layer_ordinal": 1}
        return {"token": "A1-3", "enemy_difficulty": 8, "global_layer_ordinal": 6}

    def fake_step(actual_runtime, *, requested_difficulty: str, target_enemy_difficulty, after_input: bool):
        assert actual_runtime is runtime
        step_calls.append(
            {
                "requested_difficulty": requested_difficulty,
                "target_enemy_difficulty": target_enemy_difficulty,
                "after_input": after_input,
            }
        )
        stepped["value"] = True

    def fake_sleep(seconds: float):
        sleep_calls.append(seconds)
        if len(sleep_calls) > 1:
            raise AssertionError("lowest wait path should not repeat")

    monkeypatch.setattr(entry_module, "_locate", fake_locate)
    monkeypatch.setattr(entry_module, "_read_current_entry_rank", fake_read_current_entry_rank)
    monkeypatch.setattr(entry_module, "_step_reduce_entry_difficulty", fake_step)
    monkeypatch.setattr(
        entry_module,
        "_coarse_reduce_entry_difficulty",
        lambda actual_runtime: (_ for _ in ()).throw(AssertionError("coarse should not run")),
    )
    monkeypatch.setattr(entry_module, "_transition_sleep", fake_sleep)

    entry_module._select_difficulty(runtime, difficulty="lowest")

    assert sleep_calls == [entry_module.ENTRY_DIFFICULTY_SETTLE_SECONDS]
    assert step_calls == [
        {
            "requested_difficulty": "lowest",
            "target_enemy_difficulty": None,
            "after_input": False,
        }
    ]
    assert read_calls["count"] == 2


def test_select_lowest_coarse_then_step_to_a0_1(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []
            self.locate_calls = 0
            self._values = iter([108, 8, 8, 1])

        def locate(self, template: str, **kwargs):
            del kwargs
            if template != _asset("entry.difficulty.lowest"):
                return None
            self.locate_calls += 1
            if self.locate_calls in {1, 2, 3}:
                return _box("entry.difficulty.lowest", left=1600, top=960)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            value = next(self._values)
            return [{"text": str(value), "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    entry_module._select_difficulty(runtime, difficulty="lowest")

    assert runtime.drags == [(960, 810, 960, 0)]
    assert runtime.clicks == [(1620, 970)]


def test_select_lowest_recovery_when_step_click_makes_no_progress(monkeypatch):
    import trail.scenes.cw.entry as entry_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.drags: list[tuple[int, int, int, int]] = []
            self._values = iter([8, 8])

        def locate(self, template: str, **kwargs):
            del kwargs
            if template == _asset("entry.difficulty.lowest"):
                return _box("entry.difficulty.lowest", left=1600, top=960)
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int, *, duration=None):
            del duration
            self.drags.append((from_x, from_y, to_x, to_y))

        def ocr(self, **kwargs):
            del kwargs
            value = next(self._values)
            return [{"text": str(value), "box": {"left": 10, "top": 10, "width": 20, "height": 20}}]

    monkeypatch.setattr(entry_module, "_transition_sleep", lambda seconds: None)
    runtime = Runtime()

    with pytest.raises(TrailError) as exc_info:
        entry_module._select_difficulty(runtime, difficulty="lowest")

    assert exc_info.value.code == "CW_START_DIFFICULTY_RECOVERY_REQUIRED"
    assert exc_info.value.data["reason"] == "step_no_progress"
    assert runtime.clicks == [(1620, 970)]
    assert runtime.drags == []


def test_enter_cw_records_entry_snapshot_and_invalidates_stage(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"steps": [1], "stale": False},
        "stage": {"stale": False, "value": "shop"},
    }

    refreshed = enter_cw(session, mode="continue", difficulty="highest", battle_mode="overclock")

    assert refreshed.scene_state["cw"]["entry"] == {"page": "home"}
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True, "value": "shop"}
    assert refreshed.scene_state["cw"]["slots"] == {"stale": True, "hand": ["希儿"]}
    assert refreshed.scene_state["cw"]["shop"] == {"stale": True, "opened": True}
    assert refreshed.scene_state["cw"]["sell_plan"] == {"stale": True}


def test_enter_cw_returns_home_noop_when_already_on_start_page(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.last_stage = {"scene": "cw", "value": "shop"}

    start_box = _box("entry.start", left=10, top=20)

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
        },
    )

    refreshed = enter_cw(session, mode="new", difficulty="highest", battle_mode="overclock", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {"page": "home", "already_home": True}
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.last_stage is None
    assert runtime.clicks == []
    assert runtime.wait_calls == []


def test_enter_cw_returns_home_when_start_page_visible_and_ocr_backend_errors(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    start_box = _box("entry.start", left=10, top=20)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

    runtime = Runtime()
    _install_template_runtime(runtime, locate_results={_asset("entry.start"): start_box})
    runtime.ocr = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("ocr backend missing"))

    refreshed = enter_cw(session, mode="new", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {"page": "home", "already_home": True}


def test_enter_cw_rejects_game_over_state_recorded_in_session(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {"stage": {"value": "game_over", "stale": False}}

    start_box = _box("entry.start", left=10, top=20)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

    runtime = Runtime()
    _install_template_runtime(runtime, locate_results={_asset("entry.start"): start_box})

    with pytest.raises(Exception) as exc_info:
        enter_cw(session, mode="continue", runtime=runtime)

    assert getattr(exc_info.value, "code", None) == "CW_ENTER_ALREADY_PAST_HOME"
    assert getattr(exc_info.value, "data", None) == {"page": "in_game", "stage": "game_over"}


def test_enter_cw_prefers_recorded_game_over_over_home_on_shared_start_resource(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {
        "entry": {"page": "home"},
        "stage": {"value": "game_over", "stale": False},
    }

    start_box = _box("entry.start", left=10, top=20)

    class Runtime:
        def __init__(self):
            self.locate_calls: list[str] = []
            self.wait_calls: list[str] = []

        def capture_after_action(self, optional: bool = False):
            del optional
            return None

    runtime = Runtime()
    _install_template_runtime(runtime, locate_results={_asset("entry.start"): start_box})

    with pytest.raises(Exception) as exc_info:
        enter_cw(session, mode="continue", runtime=runtime)

    assert getattr(exc_info.value, "code", None) == "CW_ENTER_ALREADY_PAST_HOME"
    assert getattr(exc_info.value, "data", None) == {"page": "in_game", "stage": "game_over"}


def test_enter_cw_ignores_recorded_game_over_when_runtime_is_still_world(tmp_path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._transition_sleep", lambda seconds: None)
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {"stage": {"value": "game_over", "stale": False}}

    menu_box = _box("entry.menu", left=12, top=24)
    cosmic_box = _box("entry.cosmic_strife", left=36, top=48)
    start_box = _box("entry.start", left=84, top=96)

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
            _asset("entry.new"): None,
            _asset("entry.continue"): None,
            _asset("entry.invest_environment"): None,
            _asset("stage.preparation"): None,
            _asset("stage.shop"): None,
            _asset("stage.replenish"): None,
            _asset("stage.encounter"): None,
            _asset("stage.fortune"): None,
            _asset("stage.event"): None,
            _asset("stage.boss_preview"): None,
            _asset("entry.start"): None,
        },
        wait_results={
            _asset("entry.menu"): menu_box,
            _asset("entry.cosmic_strife"): cosmic_box,
            _asset("entry.start"): start_box,
        },
        ocr_results=[],
    )

    refreshed = enter_cw(session, mode="continue", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {"page": "home"}
    assert runtime.keys == [("f4", 1, 0.2)]


def test_enter_cw_runs_world_to_currency_wars_entry_chain_until_home(tmp_path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._transition_sleep", lambda seconds: None)
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    menu_box = _box("entry.menu", left=12, top=24)
    cosmic_box = _box("entry.cosmic_strife", left=36, top=48)
    start_box = _box("entry.start", left=84, top=96)

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
            _asset("entry.start"): None,
            _asset("entry.new"): None,
            _asset("entry.continue"): None,
            _asset("entry.invest_environment"): None,
            _asset("stage.preparation"): None,
            _asset("stage.invest"): None,
            _asset("stage.boss_preview"): None,
            _asset("stage.shop"): None,
            _asset("stage.replenish"): None,
            _asset("stage.encounter"): None,
            _asset("stage.fortune"): None,
            _asset("stage.event"): None,
            _asset("stage.settle"): None,
            _asset("stage.game_over"): None,
        },
        wait_results={
            _asset("entry.menu"): menu_box,
            _asset("entry.cosmic_strife"): cosmic_box,
            _asset("entry.start"): start_box,
        },
    )

    refreshed = enter_cw(session, mode="continue", difficulty="current", battle_mode="standard", runtime=runtime)

    assert refreshed.scene_state["cw"]["entry"] == {"page": "home"}
    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert runtime.keys == [("f4", 1, 0.2)]
    assert runtime.clicks == [
        cosmic_box.center,
        (464, 324),
        (1494, 884),
    ]
    assert runtime.wait_calls == [
        _asset("entry.menu"),
        _asset("entry.cosmic_strife"),
        _asset("entry.start"),
    ]


def test_enter_cw_world_entry_flow_waits_between_guide_transitions(tmp_path, monkeypatch):
    import trail.scenes.cw.entry as entry_module

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    menu_box = _box("entry.menu", left=12, top=24)
    cosmic_box = _box("entry.cosmic_strife", left=36, top=48)
    start_box = _box("entry.start", left=84, top=96)
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
            _asset("entry.start"): None,
            _asset("entry.new"): None,
            _asset("entry.continue"): None,
            _asset("entry.invest_environment"): None,
            _asset("stage.preparation"): None,
            _asset("stage.invest"): None,
            _asset("stage.boss_preview"): None,
            _asset("stage.shop"): None,
            _asset("stage.replenish"): None,
            _asset("stage.encounter"): None,
            _asset("stage.fortune"): None,
            _asset("stage.event"): None,
            _asset("stage.settle"): None,
            _asset("stage.game_over"): None,
        },
        wait_results={
            _asset("entry.menu"): menu_box,
            _asset("entry.cosmic_strife"): cosmic_box,
            _asset("entry.start"): start_box,
        },
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr(entry_module, "sleep", lambda seconds: sleep_calls.append(seconds), raising=False)

    entry_module.enter_cw(session, mode="continue", runtime=runtime)

    assert sleep_calls == [2.0, 1.0, 0.8, 1.0]


@pytest.mark.parametrize(
    ("locate_results", "ocr_results", "expected_data", "expected_message"),
    [
        ({_asset("entry.new"): _box("entry.new", left=10, top=20)}, None, {"page": "entry.new"}, "cw enter only supports world or home, current page: entry.new"),
        ({_asset("entry.continue"): _box("entry.continue", left=10, top=20)}, None, {"page": "entry.continue"}, "cw enter only supports world or home, current page: entry.continue"),
        ({_asset("stage.boss_preview"): _box("stage.boss_preview", left=10, top=20)}, None, {"page": "stage.boss_preview", "stage": "boss_preview"}, "cw enter only supports world or home, current page: stage.boss_preview, stage: boss_preview"),
        ({_asset("entry.invest_environment"): _box("entry.invest_environment", left=10, top=20)}, None, {"page": "invest"}, "cw enter only supports world or home, current page: invest"),
        ({_asset("stage.preparation"): _box("stage.preparation", left=10, top=20)}, None, {"page": "in_game", "stage": "preparation"}, "cw enter only supports world or home, current page: in_game, stage: preparation"),
        ({_asset("entry.start"): _box("entry.start", left=10, top=20)}, [([0, 0], "挑战失败", 0.99), ([0, 0], "继续挑战", 0.99)], {"page": "in_game", "stage": "settle"}, "cw enter only supports world or home, current page: in_game, stage: settle"),
    ],
)
def test_enter_cw_rejects_pages_beyond_home(tmp_path, locate_results, ocr_results, expected_data, expected_message):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

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
    _install_template_runtime(runtime, locate_results=locate_results, ocr_results=ocr_results)

    with pytest.raises(Exception) as exc_info:
        enter_cw(session, mode="continue", runtime=runtime)

    assert isinstance(exc_info.value, Exception)
    assert getattr(exc_info.value, "code", None) == "CW_ENTER_ALREADY_PAST_HOME"
    assert str(exc_info.value) == expected_message
    assert getattr(exc_info.value, "data", None) == expected_data
    assert runtime.keys == []
    assert runtime.clicks == []
    assert runtime.wait_calls == []


def _build_cw_harness(tmp_path: Path, *, runtime=None):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = SimpleNamespace() if runtime is None else runtime
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    return registry, service, session, cw_service, command_service


def _run_cw_mutation(*, command_service, session, workspace_root: Path, request_id: str, method: str, payload: dict):
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION

    return command_service.handle(
        DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(workspace_root),
            session_id=session.session_id,
            verbose=False,
            method=method,
            payload={"session_id": session.session_id, **payload},
        )
    )


def _set_entry(session, entry: dict):
    session.scene_state.setdefault("cw", {})["entry"] = dict(entry)
    session.scene_state["cw"]["stage"] = {"stale": True}
    return session


def test_cw_enter_mutation_flows_through_command_service_journal(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr(
        "trail.daemon.cw_service.enter_cw",
        lambda session, runtime: _set_entry(
            session,
            {"page": "home"},
        ),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-1",
        method="cw.enter",
        payload={},
    )
    status = registry.for_workspace(str(tmp_path)).request_status("req-cw-enter-1")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == {"page": "home"}
    assert status["final_state"] == "completed"
    assert persisted.scene_state["cw"]["entry"] == {"page": "home"}
    assert persisted.scene_state["cw"]["stage"] == {"stale": True}


def test_cw_enter_mutation_rejects_tainted_session_until_reconciled(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    service.begin_mutation(session_id=session.session_id, request_id="req-cw-tainted", command_name="cw.guide.apply")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-cw-tainted",
        command_name="cw.guide.apply",
        final_state="persisted_but_response_unknown",
        envelope={
            "ok": False,
            "data": {},
            "screenshot": ".trail/shots/req-cw-tainted.png",
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": {"detail": "mutation result unknown"},
            "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
        },
    )
    enter_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.enter_cw",
        lambda session, runtime: enter_calls.append(("called", "called", "called")) or _set_entry(session, {"page": "home"}),
    )

    blocked = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-blocked",
        method="cw.enter",
        payload={},
    )

    assert service.request_status("req-cw-tainted")["tainted"] is True
    assert blocked["ok"] is False
    assert blocked["error"] == {
        "code": "SESSION_RECONCILE_REQUIRED",
        "message": "session is tainted; reconcile before mutating cw commands",
    }
    assert enter_calls == []

    service.reconcile_session(session.session_id)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-reconciled",
        method="cw.enter",
        payload={},
    )

    assert envelope["ok"] is True
    assert enter_calls == [("called", "called", "called")]
    assert service.request_status("req-cw-enter-reconciled")["final_state"] == "completed"


def test_cw_enter_duplicate_terminal_replay_precedes_tainted_gate(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    enter_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.enter_cw",
        lambda session, runtime: enter_calls.append(("called", "called", "called")) or _set_entry(session, {"page": "home"}),
    )
    risky_envelope = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-cw-enter-tainted.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"detail": "mutation result unknown"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }
    service.begin_mutation(session_id=session.session_id, request_id="req-cw-enter-tainted", command_name="cw.enter")
    service.finish_mutation(
        session_id=session.session_id,
        request_id="req-cw-enter-tainted",
        command_name="cw.enter",
        final_state="applied_but_not_persisted",
        envelope=risky_envelope,
    )

    replay = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-tainted",
        method="cw.enter",
        payload={},
    )
    blocked = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-blocked",
        method="cw.enter",
        payload={},
    )

    assert replay["request_id"] == "req-cw-enter-tainted"
    assert replay["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert replay["screenshot"] == ".trail/shots/req-cw-enter-tainted.png"
    assert blocked["ok"] is False
    assert blocked["error"] == {
        "code": "SESSION_RECONCILE_REQUIRED",
        "message": "session is tainted; reconcile before mutating cw commands",
    }
    assert enter_calls == []


def test_cw_enter_marks_applied_but_not_persisted_when_ui_side_effect_fails_late(tmp_path: Path, monkeypatch):
    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = Runtime()
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)

    def late_failure(session, runtime):
        del session
        runtime.click_point(640, 360)
        raise RuntimeError("cw enter late failure")

    monkeypatch.setattr("trail.daemon.cw_service.enter_cw", late_failure)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-enter-late-fail",
        method="cw.enter",
        payload={},
    )
    status = service.request_status("req-cw-enter-late-fail")
    persisted = service.load_session(session.session_id)

    assert runtime.clicks == [(640, 360)]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert "cw enter late failure" in envelope["debug"]["detail"]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert persisted.scene_state.get("cw", {}).get("entry") is None
