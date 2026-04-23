from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from trail.core.errors import TrailError
from trail.scenes.cw.models import CwSceneState
from trail.runtime.model import Box
from trail.session.store import SessionStore


def test_cw_scene_state_includes_strategy_defaults():
    state = CwSceneState().model_dump()

    assert state["strategy"] == {
        "cards": [],
        "stale": True,
    }


def load_cw_strategy_module():
    try:
        return importlib.import_module("trail.scenes.cw.strategy")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.strategy: {exc}")


class StrategyRuntime:
    def __init__(self, *, ocr_results: list[object] | None = None, locate_result: Box | None = None):
        self.clicks: list[tuple[int, int]] = []
        self.ocr_calls = 0
        self.locate_calls: list[tuple[str, dict[str, object]]] = []
        self._ocr_results = list(ocr_results) if ocr_results is not None else []
        self._locate_result = locate_result

    def click_point(self, x: int, y: int, **kwargs):
        del kwargs
        self.clicks.append((x, y))

    def ocr(self, **kwargs):
        del kwargs
        self.ocr_calls += 1
        if not self._ocr_results:
            return []
        index = min(self.ocr_calls - 1, len(self._ocr_results) - 1)
        return self._ocr_results[index]

    def locate(self, template: str, **kwargs):
        self.locate_calls.append((template, dict(kwargs)))
        return self._locate_result


def make_rapidocr_piece(text: str, *, left: int, top: int, width: int = 180, height: int = 48, score: float = 0.99):
    return (
        [
            [left, top],
            [left + width, top],
            [left + width, top + height],
            [left, top + height],
        ],
        text,
        score,
    )


def build_session(tmp_path: Path):
    return SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})


def test_summarize_strategy_cards_marks_primary_secondary_and_loaded_state():
    strategy_module = load_cw_strategy_module()

    cards = strategy_module.summarize_strategy_cards(
        [
            {"text": "快攻", "refresh_count": 1},
            {"text": "回蓝", "refresh_count": 2},
            {"text": "暴击", "refresh_count": 0},
        ],
        [
            {"strategy_id": "rush", "title": "快攻", "description": "d1"},
            {"strategy_id": "mana", "title": "回蓝", "description": "d2"},
            {"strategy_id": "crit", "title": "暴击", "description": "d3"},
        ],
        guide_state={"first_fight_augments": ["快攻"], "second_fight_augments": ["回蓝"]},
    )

    assert cards == [
        {
            "card_idx": 1,
            "strategy_title": "快攻",
            "strategy_description": "d1",
            "refresh_count": 1,
            "guide_match": "优选",
            "guide_loaded": 1,
        },
        {
            "card_idx": 2,
            "strategy_title": "回蓝",
            "strategy_description": "d2",
            "refresh_count": 2,
            "guide_match": "次选",
            "guide_loaded": 1,
        },
        {
            "card_idx": 3,
            "strategy_title": "暴击",
            "strategy_description": "d3",
            "refresh_count": 0,
            "guide_match": "否",
            "guide_loaded": 1,
        },
    ]


def test_summarize_strategy_cards_marks_not_recommended_when_guide_missing():
    strategy_module = load_cw_strategy_module()

    cards = strategy_module.summarize_strategy_cards(
        [{"text": "快攻", "refresh_count": 1}],
        [{"strategy_id": "rush", "title": "快攻", "description": "d1"}],
        guide_state=None,
    )

    assert cards == [
        {
            "card_idx": 1,
            "strategy_title": "快攻",
            "strategy_description": "d1",
            "refresh_count": 1,
            "guide_match": "否",
            "guide_loaded": 0,
        }
    ]


def test_summarize_strategy_cards_does_not_fabricate_cards_from_catalog_when_ocr_is_empty():
    strategy_module = load_cw_strategy_module()

    cards = strategy_module.summarize_strategy_cards(
        [],
        [
            {"strategy_id": "rush", "title": "快攻", "description": "d1"},
            {"strategy_id": "mana", "title": "回蓝", "description": "d2"},
            {"strategy_id": "crit", "title": "暴击", "description": "d3"},
        ],
        guide_state=None,
    )

    assert cards == []


def test_summarize_strategy_cards_assigns_card_idx_by_horizontal_position_when_geometry_exists():
    strategy_module = load_cw_strategy_module()

    cards = strategy_module.summarize_strategy_cards(
        [
            {"text": "暴击", "left": 1500, "top": 120, "refresh_count": 3},
            {"text": "快攻", "left": 120, "top": 140, "refresh_count": 1},
            {"text": "回蓝", "left": 860, "top": 130, "refresh_count": 2},
        ],
        [
            {"strategy_id": "rush", "title": "快攻", "description": "d1"},
            {"strategy_id": "mana", "title": "回蓝", "description": "d2"},
            {"strategy_id": "crit", "title": "暴击", "description": "d3"},
        ],
        guide_state={"first_fight_augments": ["快攻"], "second_fight_augments": ["回蓝"]},
    )

    assert cards == [
        {
            "card_idx": 1,
            "strategy_title": "快攻",
            "strategy_description": "d1",
            "refresh_count": 1,
            "guide_match": "优选",
            "guide_loaded": 1,
        },
        {
            "card_idx": 2,
            "strategy_title": "回蓝",
            "strategy_description": "d2",
            "refresh_count": 2,
            "guide_match": "次选",
            "guide_loaded": 1,
        },
        {
            "card_idx": 3,
            "strategy_title": "暴击",
            "strategy_description": "d3",
            "refresh_count": 3,
            "guide_match": "否",
            "guide_loaded": 1,
        },
    ]


def test_summarize_strategy_cards_groups_rapidocr_pieces_by_lane_and_extracts_refresh_counts():
    strategy_module = load_cw_strategy_module()

    cards = strategy_module.summarize_strategy_cards(
        [
            make_rapidocr_piece("刷新次数2", left=820, top=760),
            make_rapidocr_piece("暴击", left=1460, top=150),
            make_rapidocr_piece("技能循环", left=820, top=240),
            make_rapidocr_piece("快攻", left=120, top=140),
            make_rapidocr_piece("刷新次数0", left=1460, top=760),
            make_rapidocr_piece("前期滚雪球", left=120, top=240),
            make_rapidocr_piece("刷新次数1", left=120, top=760),
            make_rapidocr_piece("回蓝", left=820, top=150),
            make_rapidocr_piece("收尾爆发", left=1460, top=240),
        ],
        [
            {"strategy_id": "rush", "title": "快攻", "description": "前期滚雪球"},
            {"strategy_id": "mana", "title": "回蓝", "description": "技能循环"},
            {"strategy_id": "crit", "title": "暴击", "description": "收尾爆发"},
        ],
        guide_state={"first_fight_augments": ["快攻"], "second_fight_augments": ["回蓝"]},
    )

    assert cards == [
        {
            "card_idx": 1,
            "strategy_title": "快攻",
            "strategy_description": "前期滚雪球",
            "refresh_count": 1,
            "guide_match": "优选",
            "guide_loaded": 1,
        },
        {
            "card_idx": 2,
            "strategy_title": "回蓝",
            "strategy_description": "技能循环",
            "refresh_count": 2,
            "guide_match": "次选",
            "guide_loaded": 1,
        },
        {
            "card_idx": 3,
            "strategy_title": "暴击",
            "strategy_description": "收尾爆发",
            "refresh_count": 0,
            "guide_match": "否",
            "guide_loaded": 1,
        },
    ]


def test_summarize_strategy_cards_ignores_page_chrome_and_matches_real_strategy_page_layout():
    strategy_module = load_cw_strategy_module()

    cards = strategy_module.summarize_strategy_cards(
        [
            {"text": "攻略", "box": {"left": 1519.5, "top": 42.0, "width": 61.5, "height": 34.5}},
            {"text": "返回备战界面", "box": {"left": 1722.0, "top": 42.0, "width": 154.5, "height": 34.5}},
            {"text": "图例", "box": {"left": 180.0, "top": 85.5, "width": 57.0, "height": 31.5}},
            {"text": "请选择投资策略", "box": {"left": 856.5, "top": 81.0, "width": 205.5, "height": 37.5}},
            {"text": "装备党", "box": {"left": 411.0, "top": 477.0, "width": 88.5, "height": 36.0}},
            {"text": "四费晋升", "box": {"left": 904.5, "top": 478.5, "width": 111.0, "height": 34.5}},
            {"text": "梦境大舞台", "box": {"left": 1392.0, "top": 474.0, "width": 141.0, "height": 43.5}},
            {"text": "激活狼狩羁绊时，狼狩角色们", "box": {"left": 282.0, "top": 522.0, "width": 327.0, "height": 34.5}},
            {"text": "你购买的下1个4费角色将立刻", "box": {"left": 789.0, "top": 525.0, "width": 325.0, "height": 30.0}},
            {"text": "盛会之星羁绊激活时，战斗中", "box": {"left": 1291.0, "top": 520.0, "width": 326.0, "height": 35.0}},
            {"text": "每穿戴一件装备，就获得4%伤", "box": {"left": 286.0, "top": 555.0, "width": 330.0, "height": 28.0}},
            {"text": "升到2星。获得12金币。", "box": {"left": 787.0, "top": 553.0, "width": 261.0, "height": 33.0}},
            {"text": "首次激活3/7/10个不同的非独", "box": {"left": 1294.0, "top": 555.0, "width": 329.0, "height": 28.0}},
            {"text": "害增幅和2%速度增幅。获得【", "box": {"left": 286.0, "top": 583.0, "width": 329.0, "height": 30.0}},
            {"text": "立羁绊后，获得【盛会之星星", "box": {"left": 1291.0, "top": 579.0, "width": 323.0, "height": 36.0}},
            {"text": "椒丘】和【飞雪】。", "box": {"left": 282.0, "top": 609.0, "width": 220.0, "height": 37.0}},
            {"text": "徽】。获得【花火】。", "box": {"left": 1291.0, "top": 612.0, "width": 242.0, "height": 33.0}},
            {"text": "刷新次数1", "box": {"left": 414.0, "top": 847.0, "width": 115.0, "height": 32.0}},
            {"text": "刷新次数1", "box": {"left": 921.0, "top": 847.0, "width": 114.0, "height": 32.0}},
            {"text": "刷新次数1", "box": {"left": 1423.0, "top": 847.0, "width": 116.0, "height": 32.0}},
            {"text": "）确认", "box": {"left": 939.0, "top": 978.0, "width": 69.0, "height": 31.0}},
            {"text": "UID:111373161", "box": {"left": 27.0, "top": 1050.0, "width": 123.0, "height": 25.0}},
        ],
        [
            {"strategy_id": "equip", "title": "装备党", "description": "激活狼狩羁绊时，狼狩角色们每穿戴一件装备，就获得4%伤害增幅和2%速度增幅。获得<color=#b4b4b4>【椒丘】</color>和<color=#6cce9f>【飞霄】</color>。"},
            {"strategy_id": "promote", "title": "四费晋升", "description": "你购买的下1个<color=#927fe7>4费角色</color>将立刻升到2星。获得12金币。"},
            {"strategy_id": "dream", "title": "梦境大舞台", "description": "盛会之星羁绊激活时，战斗中首次激活3/7/10个不同的非独立羁绊后，获得【盛会之星星徽】。获得<color=#6cce9f>【花火】</color>。"},
        ],
        guide_state=None,
    )

    assert cards == [
        {
            "card_idx": 1,
            "strategy_title": "装备党",
            "strategy_description": "激活狼狩羁绊时，狼狩角色们每穿戴一件装备，就获得4%伤害增幅和2%速度增幅。获得<color=#b4b4b4>【椒丘】</color>和<color=#6cce9f>【飞霄】</color>。",
            "refresh_count": 1,
            "guide_match": "否",
            "guide_loaded": 0,
        },
        {
            "card_idx": 2,
            "strategy_title": "四费晋升",
            "strategy_description": "你购买的下1个<color=#927fe7>4费角色</color>将立刻升到2星。获得12金币。",
            "refresh_count": 1,
            "guide_match": "否",
            "guide_loaded": 0,
        },
        {
            "card_idx": 3,
            "strategy_title": "梦境大舞台",
            "strategy_description": "盛会之星羁绊激活时，战斗中首次激活3/7/10个不同的非独立羁绊后，获得【盛会之星星徽】。获得<color=#6cce9f>【花火】</color>。",
            "refresh_count": 1,
            "guide_match": "否",
            "guide_loaded": 0,
        },
    ]


def test_summarize_strategy_cards_keeps_sparse_lane_card_idx_for_right_column_only():
    strategy_module = load_cw_strategy_module()

    cards = strategy_module.summarize_strategy_cards(
        [
            make_rapidocr_piece("暴击", left=1460, top=150),
            make_rapidocr_piece("收尾爆发", left=1460, top=240),
            make_rapidocr_piece("刷新次数2", left=1460, top=760),
        ],
        [
            {"strategy_id": "rush", "title": "快攻", "description": "前期滚雪球"},
            {"strategy_id": "mana", "title": "回蓝", "description": "技能循环"},
            {"strategy_id": "crit", "title": "暴击", "description": "收尾爆发"},
        ],
        guide_state=None,
    )

    assert cards == [
        {
            "card_idx": 3,
            "strategy_title": "暴击",
            "strategy_description": "收尾爆发",
            "refresh_count": 2,
            "guide_match": "否",
            "guide_loaded": 0,
        }
    ]


def test_summarize_strategy_cards_does_not_fabricate_cards_from_unmatched_rapidocr_pieces():
    strategy_module = load_cw_strategy_module()

    cards = strategy_module.summarize_strategy_cards(
        [
            make_rapidocr_piece("请选择投资策略", left=760, top=60, width=360),
            make_rapidocr_piece("普通投资事件", left=860, top=220),
            make_rapidocr_piece("刷新次数1", left=860, top=760),
        ],
        [
            {"strategy_id": "rush", "title": "快攻", "description": "前期滚雪球"},
            {"strategy_id": "mana", "title": "回蓝", "description": "技能循环"},
        ],
        guide_state=None,
    )

    assert cards == []


def test_detect_cw_strategy_creates_fresh_snapshot_and_writes_session(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime()
    cards = [
        {
            "card_idx": 1,
            "strategy_title": "快攻",
            "strategy_description": "desc",
            "refresh_count": 1,
            "guide_match": "优选",
        }
    ]

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )
    monkeypatch.setattr(strategy_module, "summarize_strategy_cards", lambda *args, **kwargs: cards)

    snapshot = strategy_module.detect_cw_strategy(session, runtime=runtime, strategy_list=[])

    assert snapshot == {
        "cards": cards,
        "stale": False,
    }
    assert session.scene_state["cw"]["strategy"] == snapshot


def test_detect_cw_strategy_reads_heading_from_runtime_ocr_when_entry_state_has_no_title(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime(
        ocr_results=[
            [make_rapidocr_piece("请选择投资策略", left=760, top=60, width=360)],
            [make_rapidocr_piece("快攻", left=120, top=140)],
        ]
    )
    cards = [
        {
            "card_idx": 1,
            "strategy_title": "快攻",
            "strategy_description": "desc",
            "refresh_count": 1,
            "guide_match": "优选",
            "guide_loaded": 1,
        }
    ]

    monkeypatch.setattr(
        strategy_module,
        "_detect_current_enter_page",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest"},
    )
    monkeypatch.setattr(strategy_module, "summarize_strategy_cards", lambda *args, **kwargs: cards)

    snapshot = strategy_module.detect_cw_strategy(session, runtime=runtime, strategy_list=[])

    assert snapshot == {
        "cards": cards,
        "stale": False,
    }
    assert runtime.ocr_calls == 2


def test_select_cw_strategy_requires_fresh_snapshot(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime()
    session.scene_state["cw"] = {
        "strategy": {
            "cards": [{"card_idx": 1, "strategy_title": "快攻"}],
            "stale": True,
        }
    }

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )

    with pytest.raises(TrailError) as exc_info:
        strategy_module.select_cw_strategy(session, card_idx=1, runtime=runtime)

    assert exc_info.value.code == "CW_STRATEGY_SNAPSHOT_REQUIRED"
    assert runtime.clicks == []


def test_select_cw_strategy_marks_strategy_and_stage_stale_on_success(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime()
    session.last_stage = {"scene": "cw", "value": "invest"}
    session.scene_state["cw"] = {
        "strategy": {
            "cards": [
                {"card_idx": 1, "strategy_title": "旧卡1"},
                {"card_idx": 2, "strategy_title": "旧卡2"},
            ],
            "stale": False,
        },
        "stage": {"value": "invest", "stale": False},
    }

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )

    selected = strategy_module.select_cw_strategy(session, card_idx=2, runtime=runtime)

    assert selected == {"card_idx": 2, "strategy_title": "旧卡2"}
    assert session.scene_state["cw"]["strategy"]["stale"] is True
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None
    assert runtime.clicks == [
        strategy_module._strategy_card_point(2),
        strategy_module.STRATEGY_CONFIRM_POINT,
    ]


def test_select_cw_strategy_supports_sparse_snapshot_card_idx_roundtrip(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime()
    session.last_stage = {"scene": "cw", "value": "invest"}
    session.scene_state["cw"] = {
        "strategy": {
            "cards": [
                {"card_idx": 3, "strategy_title": "右列卡"},
            ],
            "stale": False,
        },
        "stage": {"value": "invest", "stale": False},
    }

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )

    selected = strategy_module.select_cw_strategy(session, card_idx=3, runtime=runtime)

    assert selected == {"card_idx": 3, "strategy_title": "右列卡"}
    assert session.scene_state["cw"]["strategy"]["stale"] is True
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None
    assert runtime.clicks == [
        strategy_module._strategy_card_point(3),
        strategy_module.STRATEGY_CONFIRM_POINT,
    ]


def test_select_cw_strategy_rejects_missing_card_idx_in_sparse_snapshot(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime()
    session.scene_state["cw"] = {
        "strategy": {
            "cards": [
                {"card_idx": 3, "strategy_title": "右列卡"},
            ],
            "stale": False,
        }
    }

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )

    with pytest.raises(TrailError) as exc_info:
        strategy_module.select_cw_strategy(session, card_idx=2, runtime=runtime)

    assert exc_info.value.code == "CW_STRATEGY_SNAPSHOT_REQUIRED"
    assert runtime.clicks == []


def test_detect_cw_strategy_rejects_entry_invest_page_truth(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "invest", "stage": "invest", "title": "请选择投资策略"},
    )

    with pytest.raises(TrailError) as exc_info:
        strategy_module.detect_cw_strategy(session, runtime=StrategyRuntime(), strategy_list=[])

    assert exc_info.value.code == "CW_STRATEGY_PAGE_INVALID"


def test_detect_cw_strategy_rejects_non_strategy_in_game_invest_page(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "普通投资事件"},
    )

    with pytest.raises(TrailError) as exc_info:
        strategy_module.detect_cw_strategy(session, runtime=StrategyRuntime(), strategy_list=[])

    assert exc_info.value.code == "CW_STRATEGY_PAGE_INVALID"


def test_detect_cw_strategy_rejects_in_game_invest_without_strategy_heading_from_runtime_ocr(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime(
        ocr_results=[
            [make_rapidocr_piece("普通投资事件", left=760, top=60, width=360)],
        ]
    )

    monkeypatch.setattr(
        strategy_module,
        "_detect_current_enter_page",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest"},
    )

    with pytest.raises(TrailError) as exc_info:
        strategy_module.detect_cw_strategy(session, runtime=runtime, strategy_list=[])

    assert exc_info.value.code == "CW_STRATEGY_PAGE_INVALID"
    assert runtime.ocr_calls == 1


def test_refresh_cw_strategy_rejects_invalid_card_idx(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )

    with pytest.raises(TrailError) as exc_info:
        strategy_module.refresh_cw_strategy(session, card_idx=0, runtime=StrategyRuntime(), strategy_list=[])

    assert exc_info.value.code == "CW_STRATEGY_CARD_IDX_INVALID"


def test_refresh_cw_strategy_overwrites_all_cards_and_invalidates_stage(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime()
    session.last_stage = {"scene": "cw", "value": "invest"}
    session.scene_state["cw"] = {
        "strategy": {
            "cards": [{"card_idx": 1, "strategy_title": "旧卡"}],
            "stale": False,
        },
        "stage": {"value": "invest", "stale": False},
    }
    cards = [
        {
            "card_idx": 1,
            "strategy_title": "新卡1",
            "strategy_description": "d1",
            "refresh_count": 0,
            "guide_match": "否",
        },
        {
            "card_idx": 2,
            "strategy_title": "新卡2",
            "strategy_description": "d2",
            "refresh_count": 1,
            "guide_match": "次选",
        },
        {
            "card_idx": 3,
            "strategy_title": "新卡3",
            "strategy_description": "d3",
            "refresh_count": 2,
            "guide_match": "优选",
        },
    ]

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )
    monkeypatch.setattr(strategy_module, "summarize_strategy_cards", lambda *args, **kwargs: cards)

    snapshot = strategy_module.refresh_cw_strategy(session, card_idx=2, runtime=runtime, strategy_list=[])

    assert snapshot == {
        "cards": cards,
        "stale": False,
    }
    assert runtime.clicks == [strategy_module._strategy_refresh_point(2)]
    assert session.scene_state["cw"]["strategy"] == snapshot
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_refresh_cw_strategy_prefers_refresh_label_center_over_static_lane_point(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime(
        ocr_results=[
            [
                make_rapidocr_piece("刷新次数1", left=414, top=847, width=115, height=32),
                make_rapidocr_piece("刷新次数1", left=921, top=847, width=114, height=32),
                make_rapidocr_piece("刷新次数1", left=1423, top=847, width=116, height=32),
            ],
            [],
        ]
    )
    session.scene_state["cw"] = {
        "strategy": {"cards": [{"card_idx": 2, "strategy_title": "旧卡2"}], "stale": False},
        "stage": {"value": "invest", "stale": False},
    }
    cards = [
        {
            "card_idx": 2,
            "strategy_title": "新卡2",
            "strategy_description": "d2",
            "refresh_count": 1,
            "guide_match": "次选",
        }
    ]

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )
    monkeypatch.setattr(strategy_module, "summarize_strategy_cards", lambda *args, **kwargs: cards)

    strategy_module.refresh_cw_strategy(session, card_idx=2, runtime=runtime, strategy_list=[])

    assert runtime.clicks == [(978, 863)]


def test_refresh_cw_strategy_clicks_located_refresh_icon_within_target_lane(tmp_path: Path, monkeypatch):
    strategy_module = load_cw_strategy_module()
    session = build_session(tmp_path)
    runtime = StrategyRuntime(locate_result=Box(left=902, top=844, width=64, height=38))
    session.scene_state["cw"] = {
        "strategy": {"cards": [{"card_idx": 2, "strategy_title": "旧卡2"}], "stale": False},
        "stage": {"value": "invest", "stale": False},
    }
    cards = [
        {
            "card_idx": 2,
            "strategy_title": "新卡2",
            "strategy_description": "d2",
            "refresh_count": 1,
            "guide_match": "次选",
        }
    ]

    monkeypatch.setattr(
        strategy_module,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )
    monkeypatch.setattr(strategy_module, "summarize_strategy_cards", lambda *args, **kwargs: cards)

    strategy_module.refresh_cw_strategy(session, card_idx=2, runtime=runtime, strategy_list=[])

    assert runtime.clicks == [(934, 863)]
    assert runtime.locate_calls == [
        (
            str(strategy_module._asset("strategy.refresh")),
            {"from_x": 1 / 3, "from_y": 0.72, "to_x": 2 / 3, "to_y": 0.92},
        )
    ]
