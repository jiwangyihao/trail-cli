from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from trail.core.errors import TrailError
from trail.scenes.cw.models import CwSceneState
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
    def __init__(self, *, ocr_results: list[object] | None = None):
        self.clicks: list[tuple[int, int]] = []
        self.ocr_calls = 0
        self._ocr_results = list(ocr_results) if ocr_results is not None else []

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
