from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.core.errors import TrailError
from trail.daemon.command_service import CommandService
from trail.daemon.cw_service import CwService
from trail.daemon.models import DaemonRequest
from trail.daemon.protocol import PROTOCOL_VERSION
from trail.daemon.session_service import SessionServiceRegistry
from trail.runtime.model import Box
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.portal import summarize_portal_cards


def _dict_piece(text: str, left: int, top: int, width: int = 80, height: int = 20) -> dict[str, object]:
    return {
        "text": text,
        "box": {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        },
    }


def _tuple_piece(text: str, left: int, top: int, width: int = 80, height: int = 20) -> list[object]:
    polygon = [
        [left, top],
        [left + width, top],
        [left + width, top + height],
        [left, top + height],
    ]
    return [polygon, text, 0.99]


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box(alias: str, *, left: int, top: int, width: int = 40, height: int = 20) -> Box:
    return Box(left=left, top=top, width=width, height=height, source=_asset(alias))


class StartRuntime:
    def __init__(
        self,
        *,
        locate_results: dict[str, object] | None = None,
        wait_results: dict[str, object] | None = None,
        ocr_result: object | None = None,
    ):
        self._locate_results = locate_results or {}
        self._wait_results = wait_results or {}
        self._ocr_result = [] if ocr_result is None else ocr_result
        self.locate_calls: list[str] = []
        self.wait_calls: list[str] = []
        self.clicks: list[tuple[int, int]] = []
        self.ocr_calls: list[dict[str, object]] = []

    def locate(self, template: str, **kwargs):
        del kwargs
        self.locate_calls.append(template)
        return self._locate_results.get(template)

    def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
        del timeout, interval
        self.wait_calls.append(template)
        return self._wait_results.get(template)

    def click_point(self, x: int, y: int, **kwargs):
        del kwargs
        self.clicks.append((x, y))

    def ocr(self, **kwargs):
        self.ocr_calls.append(dict(kwargs))
        if isinstance(self._ocr_result, list):
            return list(self._ocr_result)
        return self._ocr_result


class PortalRuntime(StartRuntime):
    def __init__(self, *args, click_error: Exception | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.keys: list[tuple[str, int, float]] = []
        self.click_error = click_error

    def click_point(self, x: int, y: int, **kwargs):
        del kwargs
        if self.click_error is not None:
            raise self.click_error
        self.clicks.append((x, y))

    def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
        self.keys.append((key, presses, interval))


def _build_cw_harness(tmp_path: Path, *, runtime):
    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    return registry, service, session, command_service


def _run_cw_start(
    *,
    command_service,
    session,
    workspace_root: Path,
    request_id: str,
    mode: str,
    difficulty: str,
    battle_mode: str,
):
    return command_service.handle(
        DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(workspace_root),
            session_id=session.session_id,
            verbose=False,
            method="cw.start",
            payload={
                "session_id": session.session_id,
                "mode": mode,
                "difficulty": difficulty,
                "battle_mode": battle_mode,
            },
        )
    )


def _portal_cards() -> list[dict[str, object]]:
    return [
        {
            "card_idx": 1,
            "portal_title": "Alpha Portal",
            "portal_description": "Alpha Desc",
            "score": 0.99,
        },
        {
            "card_idx": 2,
            "portal_title": "Beta Portal",
            "portal_description": "Beta Desc",
            "score": 0.88,
        },
        {
            "card_idx": 3,
            "portal_title": "Gamma Portal",
            "portal_description": "Gamma Desc",
            "score": 0.77,
        },
    ]


def _run_cw_portal_mutation(*, command_service, session, workspace_root: Path, request_id: str, method: str, payload: dict):
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


def test_summarize_portal_cards_groups_three_lanes_and_merges_rows():
    portal_list = [
        {"portal_id": "lane1-alt", "title": "Alpha Beta", "description": "Gamma"},
        {"portal_id": "lane1-win", "title": "Beta Alpha", "description": "Gamma"},
        {"portal_id": "lane2", "title": "Delta Epsilon", "description": "Zeta"},
        {"portal_id": "lane3", "title": "Eta Theta", "description": "Iota"},
    ]
    pieces = [
        _dict_piece("Alpha", left=120, top=10),
        _dict_piece("Beta", left=20, top=42),
        _dict_piece("Gamma", left=20, top=90),
        _tuple_piece("Delta", left=760, top=20),
        _tuple_piece("Epsilon", left=860, top=20),
        _tuple_piece("Zeta", left=760, top=70),
        _dict_piece("Eta", left=1500, top=30),
        _dict_piece("Theta", left=1590, top=30),
        _dict_piece("Iota", left=1500, top=80),
    ]

    cards = summarize_portal_cards(pieces, portal_list)

    assert cards == [
        {
            "card_idx": 1,
            "portal_title": "Beta Alpha",
            "portal_description": "Gamma",
            "score": pytest.approx(1.0),
        },
        {
            "card_idx": 2,
            "portal_title": "Delta Epsilon",
            "portal_description": "Zeta",
            "score": pytest.approx(1.0),
        },
        {
            "card_idx": 3,
            "portal_title": "Eta Theta",
            "portal_description": "Iota",
            "score": pytest.approx(1.0),
        },
    ]


def test_summarize_portal_cards_normalizes_case_whitespace_and_punctuation_noise():
    portal_list = [
        {"portal_id": "aaa", "title": "Alpha Bonus", "description": "Great Rewards"},
        {"portal_id": "zzz", "title": "Other Portal", "description": "Other Description"},
    ]
    pieces = [
        _tuple_piece(" ALPHA，", left=40, top=20),
        _tuple_piece("bonus！！  ", left=150, top=20),
        _tuple_piece("Great   Rewards...", left=40, top=70, width=180),
    ]

    cards = summarize_portal_cards(pieces, portal_list)

    assert cards[0]["portal_title"] == "Alpha Bonus"
    assert cards[0]["portal_description"] == "Great Rewards"


def test_summarize_portal_cards_uses_higher_of_title_and_title_plus_description_scores():
    portal_list = [
        {"portal_id": "combo", "title": "Market", "description": "Discounted Upgrades"},
        {"portal_id": "plain", "title": "Market Deals", "description": "Supplies"},
    ]
    pieces = [_dict_piece("Market Discounted Upgrades", left=50, top=20, width=260)]

    cards = summarize_portal_cards(pieces, portal_list)
    expected_combined_score = SequenceMatcher(
        a="market discounted upgrades",
        b="market discounted upgrades",
    ).ratio()

    assert cards[0]["portal_title"] == "Market"
    assert cards[0]["portal_description"] == "Discounted Upgrades"
    assert cards[0]["score"] == pytest.approx(expected_combined_score)


def test_summarize_portal_cards_breaks_ties_by_portal_id_when_scores_are_equal():
    portal_list = [
        {"portal_id": "zzz", "title": "Alpha", "description": "Portal Z"},
        {"portal_id": "aaa", "title": "Alpha", "description": "Portal A"},
        {"portal_id": "bbb", "title": "Beta", "description": "Extra"},
    ]
    pieces = [_dict_piece("Alpha", left=60, top=20, width=160)]

    cards = summarize_portal_cards(pieces, portal_list)

    assert cards[0] == {
        "card_idx": 1,
        "portal_title": "Alpha",
        "portal_description": "Portal A",
        "score": pytest.approx(1.0),
    }


def test_summarize_portal_cards_returns_empty_summary_for_lane_without_text():
    portal_list = [
        {"portal_id": "aaa", "title": "Alpha Bonus", "description": "Portal A"},
        {"portal_id": "bbb", "title": "Beta Bonus", "description": "Portal B"},
    ]
    pieces = [_dict_piece("Alpha Bonus", left=60, top=20, width=160)]

    cards = summarize_portal_cards(pieces, portal_list)

    assert cards[1] == {
        "card_idx": 2,
        "portal_title": "",
        "portal_description": "",
        "score": pytest.approx(0.0),
    }
    assert cards[2] == {
        "card_idx": 3,
        "portal_title": "",
        "portal_description": "",
        "score": pytest.approx(0.0),
    }
    assert all(set(card) == {"card_idx", "portal_title", "portal_description", "score"} for card in cards)


def test_summarize_portal_cards_marks_card_new_when_collection_match_hits_lane():
    portal_list = [
        {"portal_id": "alpha", "title": "盛会之星邀请", "description": "获得一个【盛会之星星徽】。"},
        {"portal_id": "beta", "title": "命运礼物", "description": "立刻获得一个【惊喜盒】。"},
    ]
    pieces = [
        _dict_piece("盛会之星邀请", left=320, top=379, width=180),
        _dict_piece("获得一个【盛会之星星徽】。", left=292, top=423, width=280),
        _dict_piece("命运礼物", left=904, top=379, width=120),
    ]
    collection_matches = [Box(left=623, top=221, width=23, height=24, source="collection.png")]

    cards = summarize_portal_cards(pieces, portal_list, collection_matches=collection_matches)

    assert cards[0]["new"] == 1
    assert "new" not in cards[1]
    assert "new" not in cards[2]


def test_restart_cw_portal_to_settlement_entry_requires_in_game_page(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    restart_to_settlement_entry = getattr(portal_module, "restart_cw_portal_to_settlement_entry", None)
    assert restart_to_settlement_entry is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        restart_to_settlement_entry(session, runtime=runtime)

    assert exc_info.value.code == "CW_PORTAL_RESTART_SETTLEMENT_INVALID"
    assert str(exc_info.value) == "cw portal restart helper only supports in_game, current page: invest"
    assert runtime.keys == []


def test_restart_cw_portal_to_settlement_entry_presses_esc_clicks_abandon_and_waits_for_settlement(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    restart_to_settlement_entry = getattr(portal_module, "restart_cw_portal_to_settlement_entry", None)
    assert restart_to_settlement_entry is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"entry": {"page": "in_game", "mode": "continue", "difficulty": "current", "battle_mode": "standard"}}
    runtime = PortalRuntime()
    states = iter([
        {"page": "in_game", "stage": "shop"},
        {"page": "settlement.entry"},
    ])
    dialog_checks: list[str] = []
    sleep_calls: list[float] = []
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: next(states), raising=False)
    monkeypatch.setattr(portal_module, "_detect_portal_restart_exit_dialog", lambda runtime: dialog_checks.append("dialog") or True, raising=False)
    monkeypatch.setattr(portal_module, "sleep", lambda seconds: sleep_calls.append(seconds), raising=False)

    restart_to_settlement_entry(session, runtime=runtime)

    assert runtime.keys == [("esc", 1, 0.2)]
    assert runtime.clicks == [(750, 750)]
    assert dialog_checks == ["dialog"]
    assert sleep_calls == [portal_module.PORTAL_RESTART_EXIT_DIALOG_SETTLE_SECONDS, portal_module.PORTAL_RESTART_SETTLEMENT_INTERVAL]


def test_detect_cw_portal_updates_snapshot_and_entry_from_invest_page(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    detect_cw_portal = getattr(portal_module, "detect_cw_portal", None)
    assert detect_cw_portal is not None

    cards = [
        {**_portal_cards()[0], "new": 1},
        *_portal_cards()[1:],
    ]
    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime(
        ocr_result=[{"text": "beta"}],
        locate_results={
            _asset("portal.collection"): Box(left=623, top=221, width=23, height=24, source=_asset("portal.collection")),
        },
    )
    observed_summary_inputs: list[dict[str, object]] = []
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(
        portal_module,
        "summarize_portal_cards",
        lambda pieces, portal_list, collection_matches=None: observed_summary_inputs.append({
            "pieces": pieces,
            "portal_list": portal_list,
            "collection_matches": collection_matches,
        }) or cards,
        raising=False,
    )

    snapshot = detect_cw_portal(
        session,
        runtime=runtime,
        portal_list=[{"portal_id": "beta", "title": "Beta Portal", "description": "Beta Desc"}],
    )

    assert runtime.locate_calls == [_asset("portal.collection")]
    assert runtime.ocr_calls == [{}]
    assert observed_summary_inputs == [{
        "pieces": [{"text": "beta"}],
        "portal_list": [{"portal_id": "beta", "title": "Beta Portal", "description": "Beta Desc"}],
        "collection_matches": [Box(left=623, top=221, width=23, height=24, source=_asset("portal.collection"))],
    }]
    assert snapshot == {
        "cards": cards,
        "mode": None,
        "difficulty": None,
        "battle_mode": None,
        "stale": False,
    }
    assert session.scene_state["cw"]["portal"] == snapshot
    assert session.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": None,
        "difficulty": None,
        "battle_mode": None,
    }


def test_detect_cw_portal_preserves_existing_entry_truth_priority(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    detect_cw_portal = getattr(portal_module, "detect_cw_portal", None)
    assert detect_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": None, "difficulty": "current", "battle_mode": None},
        "portal": {"cards": [], "mode": "new", "difficulty": "lowest", "battle_mode": "overclock", "stale": True},
    }
    runtime = PortalRuntime(ocr_result=[{"text": "beta"}])
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: _portal_cards(), raising=False)

    snapshot = detect_cw_portal(session, runtime=runtime, portal_list=[])

    assert snapshot == {
        "cards": _portal_cards(),
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "overclock",
        "stale": False,
    }
    assert session.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "overclock",
    }


def test_detect_cw_portal_rejects_world_even_when_cached_entry_is_invest(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    detect_cw_portal = getattr(portal_module, "detect_cw_portal", None)
    assert detect_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "world"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        detect_cw_portal(session, runtime=runtime, portal_list=[])

    assert exc_info.value.code == "CW_PORTAL_PAGE_INVALID"
    assert str(exc_info.value) == "cw portal action only supports invest, current page: world"


def test_select_cw_portal_rejects_non_invest_page(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    select_cw_portal = getattr(portal_module, "select_cw_portal", None)
    assert select_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"portal": {"cards": _portal_cards(), "stale": False}}
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "home"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        select_cw_portal(session, card_idx=1, runtime=runtime)

    assert exc_info.value.code == "CW_PORTAL_PAGE_INVALID"
    assert str(exc_info.value) == "cw portal action only supports invest, current page: home"
    assert runtime.clicks == []


def test_select_cw_portal_does_not_fallback_to_cached_invest_when_live_page_is_world(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    select_cw_portal = getattr(portal_module, "select_cw_portal", None)
    assert select_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "world"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        select_cw_portal(session, card_idx=1, runtime=runtime)

    assert exc_info.value.code == "CW_PORTAL_PAGE_INVALID"
    assert str(exc_info.value) == "cw portal action only supports invest, current page: world"
    assert runtime.clicks == []


def test_select_cw_portal_rejects_invalid_card_idx(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    select_cw_portal = getattr(portal_module, "select_cw_portal", None)
    assert select_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {"portal": {"cards": _portal_cards(), "stale": False}}
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        select_cw_portal(session, card_idx=4, runtime=runtime)

    assert exc_info.value.code == "CW_PORTAL_CARD_IDX_INVALID"
    assert str(exc_info.value) == "cw portal.select only supports card_idx 1|2|3, got: 4"
    assert runtime.clicks == []


def test_select_cw_portal_requires_cached_portal_snapshot(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    select_cw_portal = getattr(portal_module, "select_cw_portal", None)
    assert select_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        select_cw_portal(session, card_idx=2, runtime=runtime)

    assert exc_info.value.code == "CW_PORTAL_SNAPSHOT_REQUIRED"
    assert str(exc_info.value) == "cw portal.select requires cached portal snapshot"
    assert runtime.clicks == []


def test_select_cw_portal_rejects_stale_snapshot(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    select_cw_portal = getattr(portal_module, "select_cw_portal", None)
    assert select_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": True},
    }
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        select_cw_portal(session, card_idx=1, runtime=runtime)

    assert exc_info.value.code == "CW_PORTAL_SNAPSHOT_REQUIRED"
    assert str(exc_info.value) == "cw portal.select requires fresh portal snapshot"


def test_select_cw_portal_marks_snapshot_stale_after_confirm(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    select_cw_portal = getattr(portal_module, "select_cw_portal", None)
    assert select_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)

    selected = select_cw_portal(session, card_idx=2, runtime=runtime)

    assert selected == _portal_cards()[1]
    assert runtime.clicks == [(960, 540), (1084, 992)]
    assert session.scene_state["cw"]["portal"]["stale"] is True
    assert session.scene_state["cw"]["entry"] == {
        "page": "in_game",
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }


def test_cw_portal_select_requires_selected_guide_before_click(tmp_path: Path):
    runtime = PortalRuntime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    called: list[int] = []
    select_patch = pytest.MonkeyPatch()
    select_patch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: called.append(card_idx) or (_ for _ in ()).throw(AssertionError("select should not run without guide")),
    )

    try:
        envelope = _run_cw_portal_mutation(
            command_service=command_service,
            session=session,
            workspace_root=tmp_path,
            request_id="req-cw-portal-select-no-guide",
            method="cw.portal.select",
            payload={"card_idx": 2},
        )
        status = service.request_status("req-cw-portal-select-no-guide")

        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "CW_GUIDE_SELECTION_REQUIRED"
        assert "guide.fetch.cw --select" in envelope["error"]["message"]
        assert called == []
        assert runtime.clicks == []
        assert status["final_state"] == "failed_before_side_effect"
    finally:
        select_patch.undo()


@pytest.mark.parametrize(
    "guide_state",
    [
        {"artifact": "selected-artifact", "lineup_id": "selected-lineup"},
        {"artifact": "selected-artifact", "lineup_id": "selected-lineup", "share_code": ""},
        {"artifact": "selected-artifact", "lineup_id": "selected-lineup", "share_code": "demo"},
        {"artifact": "selected-artifact", "lineup_id": "selected-lineup", "share_code": "###"},
    ],
    ids=["missing-share-code", "empty-share-code", "plain-text-share-code", "broken-marker-share-code"],
)
def test_cw_portal_select_rejects_invalid_selected_guide_before_click(
    tmp_path: Path,
    guide_state: dict[str, object],
):
    runtime = PortalRuntime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": dict(guide_state),
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    called: list[int] = []
    select_patch = pytest.MonkeyPatch()
    select_patch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: called.append(card_idx) or (_ for _ in ()).throw(AssertionError("select should not run with invalid guide")),
    )

    try:
        envelope = _run_cw_portal_mutation(
            command_service=command_service,
            session=session,
            workspace_root=tmp_path,
            request_id=f"req-cw-portal-select-invalid-guide-{guide_state.get('share_code', 'missing')}",
            method="cw.portal.select",
            payload={"card_idx": 2},
        )
        status = service.request_status(f"req-cw-portal-select-invalid-guide-{guide_state.get('share_code', 'missing')}")

        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "CW_GUIDE_STATE_INVALID"
        assert called == []
        assert runtime.clicks == []
        assert status["final_state"] == "failed_before_side_effect"
    finally:
        select_patch.undo()


def test_cw_portal_select_auto_applies_selected_guide_and_invalidates_runtime_state(tmp_path: Path, monkeypatch):
    runtime = PortalRuntime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": {"artifact": "selected-artifact", "lineup_id": "selected-lineup", "share_code": "##demo##"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
        "slots": {"stale": False, "hand": ["银狼"]},
        "sell_plan": {"candidates": [0]},
        "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
        "stage": {"value": "shop", "stale": False},
    }
    service.save_session(session)
    applied_share_codes: list[str] = []
    selected_card_idxs: list[int] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: selected_card_idxs.append(card_idx)
        or session.scene_state["cw"]["portal"].__setitem__("stale", True)
        or dict(_portal_cards()[card_idx - 1]),
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_portal_preparation",
        lambda session, runtime: None,
        raising=False,
    )

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-select-auto-apply",
        method="cw.portal.select",
        payload={"card_idx": 2},
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == _portal_cards()[1]
    assert selected_card_idxs == [2]
    assert applied_share_codes == ["##demo##"]
    assert runtime.clicks == []
    assert persisted.scene_state["cw"]["portal"]["stale"] is True
    assert persisted.scene_state["cw"]["guide"]["lineup_id"] == "selected-lineup"
    assert persisted.scene_state["cw"]["sell_plan"] == {}
    assert persisted.scene_state["cw"]["slots"]["stale"] is True
    assert persisted.scene_state["cw"]["shop"]["stale"] is True
    assert persisted.scene_state["cw"]["stage"]["stale"] is True


def test_cw_portal_select_waits_for_preparation_before_auto_apply(tmp_path: Path, monkeypatch):
    runtime = PortalRuntime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "guide": {"artifact": "selected-artifact", "lineup_id": "selected-lineup", "share_code": "##demo##"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    events: list[str] = []

    monkeypatch.setattr(
        "trail.daemon.cw_service.select_cw_portal",
        lambda session, card_idx, runtime: events.append("select") or dict(_portal_cards()[card_idx - 1]),
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_portal_preparation",
        lambda session, runtime: events.append("wait") or None,
        raising=False,
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: events.append("apply"),
    )

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-select-wait-preparation",
        method="cw.portal.select",
        payload={"card_idx": 2},
    )

    assert envelope["ok"] is True
    assert events == ["select", "wait", "apply"]


def test_refresh_cw_portal_rejects_non_invest_page(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    refresh_cw_portal = getattr(portal_module, "refresh_cw_portal", None)
    assert refresh_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime()
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "home"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        refresh_cw_portal(session, runtime=runtime, portal_list=[])

    assert exc_info.value.code == "CW_PORTAL_PAGE_INVALID"
    assert str(exc_info.value) == "cw portal action only supports invest, current page: home"


def test_refresh_cw_portal_rejects_when_refresh_unavailable(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    refresh_cw_portal = getattr(portal_module, "refresh_cw_portal", None)
    assert refresh_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    runtime = PortalRuntime(click_error=TrailError("BUTTON_MISSING", "button missing"))
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)

    with pytest.raises(TrailError) as exc_info:
        refresh_cw_portal(session, runtime=runtime, portal_list=[])

    assert exc_info.value.code == "CW_PORTAL_REFRESH_UNAVAILABLE"
    assert str(exc_info.value) == "cw portal.refresh unavailable"


def test_refresh_cw_portal_clicks_refresh_and_updates_snapshot(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    refresh_cw_portal = getattr(portal_module, "refresh_cw_portal", None)
    assert refresh_cw_portal is not None

    cards = [
        {**_portal_cards()[0], "new": 1},
        *_portal_cards()[1:],
    ]
    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [{"card_idx": 1, "portal_title": "old", "portal_description": "old", "score": 0.1}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    runtime = PortalRuntime(
        ocr_result=[{"text": "beta"}],
        locate_results={
            _asset("portal.refresh"): Box(left=650, top=975, width=36, height=34, source=_asset("portal.refresh")),
            _asset("portal.collection"): Box(left=623, top=221, width=23, height=24, source=_asset("portal.collection")),
        },
    )
    observed_summary_inputs: list[dict[str, object]] = []
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(
        portal_module,
        "summarize_portal_cards",
        lambda pieces, portal_list, collection_matches=None: observed_summary_inputs.append({
            "pieces": pieces,
            "portal_list": portal_list,
            "collection_matches": collection_matches,
        }) or cards,
        raising=False,
    )

    snapshot = refresh_cw_portal(
        session,
        runtime=runtime,
        portal_list=[{"portal_id": "beta", "title": "Beta Portal", "description": "Beta Desc"}],
    )

    assert runtime.locate_calls == [_asset("portal.refresh"), _asset("portal.collection")]
    assert runtime.clicks == [(668, 992)]
    assert runtime.ocr_calls == [{}]
    assert observed_summary_inputs == [{
        "pieces": [{"text": "beta"}],
        "portal_list": [{"portal_id": "beta", "title": "Beta Portal", "description": "Beta Desc"}],
        "collection_matches": [Box(left=623, top=221, width=23, height=24, source=_asset("portal.collection"))],
    }]
    assert snapshot == {
        "cards": cards,
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
        "stale": False,
    }
    assert session.scene_state["cw"]["portal"] == snapshot
    assert session.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }


def test_refresh_cw_portal_waits_for_invest_to_stabilize_before_ocr(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    refresh_cw_portal = getattr(portal_module, "refresh_cw_portal", None)
    assert refresh_cw_portal is not None

    cards = [
        {**_portal_cards()[0], "new": 1},
        *_portal_cards()[1:],
    ]
    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [{"card_idx": 1, "portal_title": "old", "portal_description": "old", "score": 0.1}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    runtime = PortalRuntime(ocr_result=[{"text": "beta"}])
    states = iter([
        {"page": "invest"},
        {"page": "world"},
        {"page": "invest"},
    ])
    detect_calls: list[str] = []
    monkeypatch.setattr(
        portal_module,
        "_detect_current_enter_page",
        lambda runtime, session=None, preferred_mode=None: detect_calls.append("detect") or next(states),
        raising=False,
    )
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards, raising=False)
    monkeypatch.setattr(portal_module, "sleep", lambda seconds: None, raising=False)

    snapshot = refresh_cw_portal(session, runtime=runtime, portal_list=[])

    assert snapshot["cards"] == cards
    assert len(detect_calls) == 3
    assert runtime.ocr_calls == [{}]


def test_refresh_cw_portal_waits_one_settle_cycle_even_if_page_stays_invest(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    refresh_cw_portal = getattr(portal_module, "refresh_cw_portal", None)
    assert refresh_cw_portal is not None

    cards = _portal_cards()
    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": [{"card_idx": 1, "portal_title": "old", "portal_description": "old", "score": 0.1}], "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    runtime = PortalRuntime(ocr_result=[{"text": "beta"}])
    states = iter([
        {"page": "invest"},
        {"page": "invest"},
    ])
    detect_calls: list[str] = []
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        portal_module,
        "_detect_current_enter_page",
        lambda runtime, session=None, preferred_mode=None: detect_calls.append("detect") or next(states),
        raising=False,
    )
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards, raising=False)
    monkeypatch.setattr(portal_module, "sleep", lambda seconds: sleep_calls.append(seconds), raising=False)

    snapshot = refresh_cw_portal(session, runtime=runtime, portal_list=[])

    assert snapshot["cards"] == cards
    assert detect_calls == ["detect", "detect"]
    assert sleep_calls == [portal_module.PORTAL_SETTLE_INTERVAL]
    assert runtime.ocr_calls == [{}]


def test_refresh_cw_portal_rejects_when_page_does_not_settle_back_to_invest(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    refresh_cw_portal = getattr(portal_module, "refresh_cw_portal", None)
    assert refresh_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    runtime = PortalRuntime(ocr_result=[{"text": "stale"}])
    states = iter([
        {"page": "invest"},
        {"page": "world"},
        {"page": "world"},
        {"page": "world"},
    ])
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: next(states), raising=False)
    monkeypatch.setattr(portal_module, "sleep", lambda seconds: None, raising=False)

    with pytest.raises(TrailError) as exc_info:
        refresh_cw_portal(session, runtime=runtime, portal_list=[])

    assert exc_info.value.code == "CW_PORTAL_REFRESH_UNAVAILABLE"
    assert str(exc_info.value) == "cw portal.refresh did not settle back to invest"
    assert runtime.ocr_calls == []


def test_restart_cw_portal_requires_recorded_entry_truth(tmp_path: Path, monkeypatch):
    import trail.daemon.cw_service as cw_service_module

    runtime = PortalRuntime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest"},
        "portal": {"cards": _portal_cards(), "stale": False},
    }
    service.save_session(session)

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-restart-missing-entry",
        method="cw.portal.restart",
        payload={},
    )

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "CW_PORTAL_ENTRY_TRUTH_REQUIRED",
        "message": "cw portal.restart requires recorded mode/difficulty/battle_mode",
    }


def test_detect_snapshot_does_not_make_restart_valid_without_entry_truth(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    detect_cw_portal = getattr(portal_module, "detect_cw_portal", None)
    assert detect_cw_portal is not None

    runtime = PortalRuntime(ocr_result=[{"text": "beta"}])
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: _portal_cards(), raising=False)

    detect_cw_portal(session, runtime=runtime, portal_list=[])
    service.save_session(session)

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-restart-after-detect",
        method="cw.portal.restart",
        payload={},
    )

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "CW_PORTAL_ENTRY_TRUTH_REQUIRED",
        "message": "cw portal.restart requires recorded mode/difficulty/battle_mode",
    }
    assert session.scene_state["cw"]["portal"]["stale"] is False
    assert session.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": None,
        "difficulty": None,
        "battle_mode": None,
    }


def test_restart_cw_portal_rejects_non_invest_page(tmp_path: Path, monkeypatch):
    import trail.daemon.cw_service as cw_service_module

    runtime = PortalRuntime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home", "mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "continue", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-restart-home",
        method="cw.portal.restart",
        payload={},
    )

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "CW_PORTAL_PAGE_INVALID",
        "message": "cw portal action only supports invest, current page: home",
    }


def test_restart_cw_portal_selects_first_card_returns_home_and_restarts_into_invest(tmp_path: Path, monkeypatch):
    import trail.daemon.cw_service as cw_service_module

    cards = _portal_cards()
    restarted_cards = [
        {"card_idx": 1, "portal_title": "New Alpha", "portal_description": "New Desc", "score": 0.91},
        {"card_idx": 2, "portal_title": "New Beta", "portal_description": "New Desc", "score": 0.82},
        {"card_idx": 3, "portal_title": "New Gamma", "portal_description": "New Desc", "score": 0.73},
    ]
    runtime = PortalRuntime(ocr_result=[{"text": "restart"}])
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "new", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": cards, "mode": "new", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    select_calls: list[int] = []
    wait_calls: list[str] = []
    restart_calls: list[str] = []
    start_calls: list[dict[str, object]] = []
    monkeypatch.setattr(cw_service_module, "select_cw_portal", lambda session, card_idx, runtime: select_calls.append(card_idx) or cards[0], raising=False)
    monkeypatch.setattr(
        cw_service_module,
        "wait_cw_portal_in_game",
        lambda session, runtime: wait_calls.append("waited") or session.scene_state.setdefault("cw", {}).update({"entry": {"page": "in_game", "mode": "continue", "difficulty": "current", "battle_mode": "standard"}}),
        raising=False,
    )
    monkeypatch.setattr(cw_service_module, "restart_cw_portal_to_settlement_entry", lambda session, runtime: restart_calls.append("settlement") or session, raising=False)

    def fake_start_cw(session, *, mode: str, difficulty: str, battle_mode: str, runtime):
        start_calls.append({"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode})
        resolved_mode = "new" if mode == "continue" else mode
        session.scene_state.setdefault("cw", {})["entry"] = {
            "page": "invest",
            "mode": resolved_mode,
            "difficulty": difficulty,
            "battle_mode": battle_mode,
        }
        return session

    monkeypatch.setattr(cw_service_module, "start_cw", fake_start_cw, raising=False)
    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {"portal_list": []}, raising=False)
    monkeypatch.setattr(cw_service_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: restarted_cards, raising=False)

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-restart-happy",
        method="cw.portal.restart",
        payload={},
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == {
        "cards": restarted_cards,
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "standard",
        "stale": False,
    }
    assert select_calls == [1]
    assert wait_calls == ["waited"]
    assert restart_calls == ["settlement"]
    assert start_calls == [{"mode": "continue", "difficulty": "current", "battle_mode": "standard"}]
    assert persisted.scene_state["cw"]["portal"] == envelope["data"]


def test_detect_snapshot_can_be_consumed_by_select(tmp_path: Path, monkeypatch):
    import trail.scenes.cw.portal as portal_module

    detect_cw_portal = getattr(portal_module, "detect_cw_portal", None)
    select_cw_portal = getattr(portal_module, "select_cw_portal", None)
    assert detect_cw_portal is not None
    assert select_cw_portal is not None

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = PortalRuntime(ocr_result=[{"text": "beta"}])
    monkeypatch.setattr(portal_module, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"}, raising=False)
    monkeypatch.setattr(portal_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: _portal_cards(), raising=False)

    detect_cw_portal(session, runtime=runtime, portal_list=[])
    selected = select_cw_portal(session, card_idx=2, runtime=runtime)

    assert selected == _portal_cards()[1]
    assert session.scene_state["cw"]["portal"]["stale"] is True
    assert session.scene_state["cw"]["entry"]["page"] == "in_game"


def test_restart_cw_portal_waits_for_in_game_before_returning_home(tmp_path: Path, monkeypatch):
    import trail.daemon.cw_service as cw_service_module

    runtime = PortalRuntime(ocr_result=[{"text": "restart"}])
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest", "mode": "new", "difficulty": "current", "battle_mode": "standard"},
        "portal": {"cards": _portal_cards(), "mode": "new", "difficulty": "current", "battle_mode": "standard", "stale": False},
    }
    service.save_session(session)
    wait_calls: list[str] = []
    restart_calls: list[str] = []

    monkeypatch.setattr(cw_service_module, "select_cw_portal", lambda session, card_idx, runtime: _portal_cards()[0], raising=False)
    monkeypatch.setattr(
        cw_service_module,
        "wait_cw_portal_in_game",
        lambda session, runtime: wait_calls.append("waited") or session.scene_state.setdefault("cw", {}).update({"entry": {"page": "in_game", "mode": "continue", "difficulty": "current", "battle_mode": "standard"}}),
        raising=False,
    )
    monkeypatch.setattr(
        cw_service_module,
        "restart_cw_portal_to_settlement_entry",
        lambda session, runtime: restart_calls.append(session.scene_state["cw"]["entry"]["page"]) or session.scene_state.setdefault("cw", {}).update({"entry": {"page": "settlement.entry", "mode": "continue", "difficulty": "current", "battle_mode": "standard"}}),
        raising=False,
    )
    monkeypatch.setattr(
        cw_service_module,
        "start_cw",
        lambda session, mode, difficulty, battle_mode, runtime: session.scene_state.setdefault("cw", {}).update({"entry": {"page": "invest", "mode": "new" if mode == "continue" else mode, "difficulty": difficulty, "battle_mode": battle_mode}}) or session,
        raising=False,
    )
    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {"portal_list": []}, raising=False)
    monkeypatch.setattr(cw_service_module, "summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: _portal_cards(), raising=False)

    envelope = _run_cw_portal_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-portal-restart-wait-in-game",
        method="cw.portal.restart",
        payload={},
    )

    assert envelope["ok"] is True
    assert wait_calls == ["waited"]
    assert restart_calls == ["in_game"]


def test_cw_start_from_home_advances_to_invest_and_persists_portal_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    start_box = _box("entry.start", left=100, top=200)
    entry_new_box = _box("entry.new", left=200, top=300)
    start_game_box = _box("entry.start_game", left=240, top=340)
    settle_box = _box("stage.settle", left=320, top=420)
    boss_preview_box = _box("stage.boss_preview", left=300, top=400)
    invest_box = _box("entry.invest_environment", left=400, top=500)
    runtime = StartRuntime(
        locate_results={
            _asset("entry.start"): start_box,
            _asset("portal.collection"): Box(left=623, top=221, width=23, height=24, source=_asset("portal.collection")),
        },
        wait_results={
            _asset("entry.new"): entry_new_box,
            _asset("entry.start_game"): start_game_box,
            _asset("stage.settle"): settle_box,
            _asset("stage.boss_preview"): boss_preview_box,
            _asset("entry.invest_environment"): invest_box,
        },
        ocr_result=[{"text": "alpha"}],
    )
    cards = _portal_cards()
    observed_summary_inputs: list[dict[str, object]] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide_config",
        lambda **kwargs: {"portal_list": [{"portal_id": "alpha", "title": "Alpha Portal", "description": "Alpha Desc"}]},
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.summarize_portal_cards",
        lambda pieces, portal_list, collection_matches=None: observed_summary_inputs.append({
            "pieces": pieces,
            "portal_list": portal_list,
            "collection_matches": collection_matches,
        }) or cards,
    )
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [1]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-home",
        mode="new",
        difficulty="current",
        battle_mode="standard",
    )
    status = registry.for_workspace(str(tmp_path)).request_status("req-cw-start-home")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == {
        "cards": cards,
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "standard",
        "stale": False,
    }
    assert persisted.scene_state["cw"]["portal"] == envelope["data"]
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert persisted.scene_state["cw"]["slots"] == {"stale": True, "hand": ["希儿"]}
    assert persisted.scene_state["cw"]["shop"] == {"stale": True, "opened": True}
    assert persisted.scene_state["cw"]["sell_plan"] == {"stale": True}
    assert status["final_state"] == "completed"
    assert runtime.ocr_calls == [{}, {}, {}, {}, {}]
    assert observed_summary_inputs == [
        {
            "pieces": [{"text": "alpha"}],
            "portal_list": [{"portal_id": "alpha", "title": "Alpha Portal", "description": "Alpha Desc"}],
            "collection_matches": [Box(left=623, top=221, width=23, height=24, source=_asset("portal.collection"))],
        }
    ]
    assert runtime.clicks == [
        start_box.center,
        (300, 250),
        entry_new_box.center,
        start_game_box.center,
        settle_box.center,
        boss_preview_box.center,
    ]
    assert runtime.wait_calls == [
        _asset("entry.new"),
        _asset("entry.start_game"),
        _asset("stage.settle"),
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]


@pytest.mark.parametrize("requested_mode", ["new", "continue"])
def test_cw_start_rejects_home_with_unfinished_progress_before_side_effects(
    tmp_path: Path,
    monkeypatch,
    requested_mode: str,
):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    continue_box = _box("entry.continue", left=200, top=300)
    runtime = StartRuntime(
        locate_results={
            _asset("entry.continue"): continue_box,
        },
        ocr_result=[
            _dict_piece("继续进度", left=100, top=100),
            _dict_piece("结束并结算", left=260, top=100),
        ],
    )
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home"},
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [1]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=f"req-cw-start-home-progress-{requested_mode}",
        mode=requested_mode,
        difficulty="current",
        battle_mode="standard",
    )
    status = registry.for_workspace(str(tmp_path)).request_status(f"req-cw-start-home-progress-{requested_mode}")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is False
    assert envelope["data"] == {"page": "home"}
    assert envelope["error"] == {
        "code": "CW_START_PROGRESS_PENDING",
        "message": "cw start found unfinished home progress; ask whether to continue progress or end and settle before starting a new run",
    }
    assert status["final_state"] == "failed_before_side_effect"
    assert runtime.clicks == []
    assert runtime.wait_calls == []
    assert runtime.ocr_calls == [{}]
    assert persisted.scene_state["cw"]["entry"] == {"page": "home"}
    assert persisted.scene_state["cw"]["slots"] == {"stale": False, "hand": ["希儿"]}


def test_cw_start_rejects_continue_mode_on_clean_home_before_side_effects(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    start_box = _box("entry.start", left=100, top=200)
    runtime = StartRuntime(
        locate_results={
            _asset("entry.start"): start_box,
        },
        ocr_result=[_dict_piece("货币战争", left=100, top=100)],
    )
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {"entry": {"page": "home"}}
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-home-continue-invalid",
        mode="continue",
        difficulty="current",
        battle_mode="standard",
    )

    assert envelope["ok"] is False
    assert envelope["data"] == {"page": "home"}
    assert envelope["error"] == {
        "code": "CW_START_CONTINUE_PAGE_INVALID",
        "message": "cw start --mode continue only supports whole-run settlement pages, current page: home",
    }
    assert registry.for_workspace(str(tmp_path)).request_status("req-cw-start-home-continue-invalid")["final_state"] == "failed_before_side_effect"
    assert runtime.clicks == []
    assert runtime.wait_calls == []
    assert runtime.ocr_calls == [{}, {}]


def test_cw_start_from_clean_home_reveals_unfinished_progress_after_start_click(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    start_box = _box("entry.start", left=100, top=200)

    class Runtime(StartRuntime):
        def __init__(self):
            super().__init__()
            self._phase = "home"

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == _asset("entry.start"):
                return start_box if self._phase == "home" else None
            if template == _asset("entry.continue"):
                return _box("entry.continue", left=200, top=300) if self._phase == "after_start" else None
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            if (x, y) == start_box.center:
                self._phase = "after_start"

        def ocr(self, **kwargs):
            self.ocr_calls.append(dict(kwargs))
            if self._phase == "home":
                return [{"text": "货币战争"}]
            return [
                {"text": "继续进度"},
                {"text": "结束并结算"},
                {"text": "当前进度1-1M奖励"},
            ]

    runtime = Runtime()
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home"},
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [1]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-home-late-progress-new",
        mode="new",
        difficulty="current",
        battle_mode="standard",
    )
    status = registry.for_workspace(str(tmp_path)).request_status("req-cw-start-home-late-progress-new")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is False
    assert envelope["data"] == {}
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert runtime.clicks == [start_box.center]
    assert runtime.wait_calls == []
    assert runtime.ocr_calls == [{}, {}, {}]
    assert persisted.scene_state["cw"]["entry"] == {"page": "home"}
    assert persisted.scene_state["cw"]["slots"] == {"stale": False, "hand": ["希儿"]}
    assert persisted.scene_state["daemon"]["tainted"] is True


def test_cw_start_continue_from_whole_run_settlement_chain_reaches_invest_and_persists_new_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    cards = _portal_cards()
    start_box = _box("entry.start", left=100, top=200)
    entry_new_box = _box("entry.new", left=200, top=300)
    start_game_box = _box("entry.start_game", left=300, top=400)
    settle_box = _box("stage.settle", left=400, top=500)
    boss_preview_box = _box("stage.boss_preview", left=500, top=600)
    invest_box = _box("entry.invest_environment", left=600, top=700)

    class Runtime(StartRuntime):
        def __init__(self):
            super().__init__()
            self._phase = "settlement.entry"

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == _asset("entry.start"):
                return start_box if self._phase == "home" else None
            if template == _asset("entry.new"):
                return entry_new_box if self._phase == "after_home_start" else None
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            del timeout, interval
            self.wait_calls.append(template)
            if template == _asset("entry.start"):
                return start_box if self._phase == "home" else None
            if template == _asset("entry.new"):
                return entry_new_box if self._phase == "after_home_start" else None
            if template == _asset("entry.start_game"):
                return start_game_box
            if template == _asset("stage.settle"):
                return settle_box
            if template == _asset("stage.boss_preview"):
                return boss_preview_box
            if template == _asset("entry.invest_environment"):
                return invest_box
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            if (x, y) == (960, 908):
                if self._phase == "settlement.entry":
                    self._phase = "settlement.followup"
                elif self._phase == "settlement.followup":
                    self._phase = "settlement.return"
                elif self._phase == "settlement.return":
                    self._phase = "home"
            elif (x, y) == start_box.center:
                self._phase = "after_home_start"

        def ocr(self, **kwargs):
            self.ocr_calls.append(dict(kwargs))
            if self._phase == "settlement.entry":
                return [{"text": "挑战失败"}, {"text": "对局评价"}, {"text": "下一步"}]
            if self._phase == "settlement.followup":
                return [{"text": "1-1M奖励"}, {"text": "标准博弈"}, {"text": "下一页"}]
            if self._phase == "settlement.return":
                return [{"text": "小队生命值"}, {"text": "总经济"}, {"text": "返回货币战争"}]
            return [{"text": "货币战争"}]

    runtime = Runtime()
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home"},
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [1]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-whole-run-continue",
        mode="continue",
        difficulty="current",
        battle_mode="standard",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"]["cards"] == cards
    assert envelope["data"]["mode"] == "new"
    assert envelope["data"]["difficulty"] == "current"
    assert envelope["data"]["battle_mode"] == "standard"
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert runtime.clicks == [
        (960, 908),
        (960, 908),
        (960, 908),
        start_box.center,
        (300, 250),
        entry_new_box.center,
        start_game_box.center,
        settle_box.center,
        boss_preview_box.center,
    ]
    assert runtime.wait_calls == [
        _asset("entry.start"),
        _asset("entry.new"),
        _asset("entry.start_game"),
        _asset("stage.settle"),
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]


@pytest.mark.parametrize("requested_mode", ["new", "continue"])
def test_start_cw_consumes_unfinished_progress_flag_before_run_start_chain(
    tmp_path: Path,
    monkeypatch,
    requested_mode: str,
):
    import trail.scenes.cw.entry as entry_module

    session = SessionServiceRegistry().for_workspace(str(tmp_path)).create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    session.scene_state["cw"] = {
        "entry": {"page": "home"},
        "slots": {"stale": False, "hand": ["希儿"]},
    }
    runtime = StartRuntime()

    monkeypatch.setattr(
        entry_module,
        "_detect_current_enter_page",
        lambda runtime, session=None, preferred_mode=None: {"page": "home", "unfinished_progress": "1"},
        raising=False,
    )
    monkeypatch.setattr(
        entry_module,
        "_run_start_chain",
        lambda *args, **kwargs: pytest.fail("unfinished_progress should short-circuit before _run_start_chain"),
        raising=False,
    )

    with pytest.raises(TrailError) as exc_info:
        entry_module.start_cw(
            session,
            mode=requested_mode,
            difficulty="current",
            battle_mode="standard",
            runtime=runtime,
        )

    assert exc_info.value.code == "CW_START_PROGRESS_PENDING"
    assert str(exc_info.value) == "cw start found unfinished home progress; ask whether to continue progress or end and settle before starting a new run"
    assert runtime.clicks == []
    assert session.scene_state["cw"]["entry"] == {"page": "home"}


def test_cw_start_entry_continue_still_advances_when_only_continue_progress_text_present(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    cards = _portal_cards()
    runtime = StartRuntime(
        locate_results={
            _asset("entry.continue"): _box("entry.continue", left=200, top=300),
        },
        wait_results={
            _asset("entry.continue"): _box("entry.continue", left=200, top=300),
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
        },
        ocr_result=[
            _dict_piece("继续进度", left=100, top=100),
        ],
    )
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home", "mode": "continue", "difficulty": "lowest", "battle_mode": "standard"},
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [1]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-entry-continue-only-continue-text",
        mode="new",
        difficulty="current",
        battle_mode="overclock",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"]["cards"] == cards
    assert envelope["data"]["mode"] == "continue"
    assert envelope["data"]["difficulty"] == "lowest"
    assert envelope["data"]["battle_mode"] == "overclock"
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "continue",
        "difficulty": "lowest",
        "battle_mode": "overclock",
    }
    assert runtime.clicks == [(300, 450), (220, 310), (320, 410)]
    assert runtime.wait_calls == [
        _asset("entry.continue"),
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]
    assert runtime.ocr_calls == [{}, {}]


def test_cw_start_entry_continue_keeps_recorded_exact_difficulty(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    cards = _portal_cards()
    runtime = StartRuntime(
        locate_results={
            _asset("entry.continue"): _box("entry.continue", left=200, top=300),
        },
        wait_results={
            _asset("entry.continue"): _box("entry.continue", left=200, top=300),
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
        },
        ocr_result=[
            _dict_piece("继续进度", left=100, top=100),
        ],
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide_config",
        lambda workspace_root=None, timeout=10: {"portal_list": []},
    )
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home", "mode": "continue", "difficulty": "A7-3", "battle_mode": "standard"},
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [1]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-entry-continue-exact",
        mode="new",
        difficulty="current",
        battle_mode="overclock",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"]["cards"] == cards
    assert envelope["data"]["mode"] == "continue"
    assert envelope["data"]["difficulty"] == "A7-3"
    assert envelope["data"]["battle_mode"] == "overclock"
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "continue",
        "difficulty": "A7-3",
        "battle_mode": "overclock",
    }
    assert runtime.clicks == [(300, 450), (220, 310), (320, 410)]
    assert runtime.wait_calls == [
        _asset("entry.continue"),
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]
    assert runtime.ocr_calls == [{}, {}]


def test_cw_start_continue_from_whole_run_settlement_chain_reaches_invest_and_persists_new_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    cards = _portal_cards()
    start_box = _box("entry.start", left=100, top=200)
    entry_new_box = _box("entry.new", left=140, top=180)
    start_game_box = _box("entry.start_game", left=240, top=280)
    settle_box = _box("stage.settle", left=340, top=380)
    boss_preview_box = _box("stage.boss_preview", left=440, top=480)
    invest_box = _box("entry.invest_environment", left=540, top=580)

    class Runtime(StartRuntime):
        def __init__(self):
            super().__init__()
            self._phase = "settlement.entry"

        def locate(self, template: str, **kwargs):
            del kwargs
            self.locate_calls.append(template)
            if template == _asset("entry.start"):
                return start_box if self._phase == "home" else None
            if template == _asset("entry.new"):
                return entry_new_box if self._phase == "after_home_start" else None
            return None

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            del timeout, interval
            self.wait_calls.append(template)
            if template == _asset("entry.start"):
                return start_box if self._phase == "home" else None
            if template == _asset("entry.new"):
                return entry_new_box if self._phase == "after_home_start" else None
            if template == _asset("entry.start_game"):
                return start_game_box
            if template == _asset("stage.settle"):
                return settle_box
            if template == _asset("stage.boss_preview"):
                return boss_preview_box
            if template == _asset("entry.invest_environment"):
                return invest_box
            return None

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            if (x, y) == (960, 908):
                if self._phase == "settlement.entry":
                    self._phase = "settlement.followup"
                elif self._phase == "settlement.followup":
                    self._phase = "settlement.return"
                elif self._phase == "settlement.return":
                    self._phase = "home"
            elif (x, y) == start_box.center:
                self._phase = "after_home_start"

        def ocr(self, **kwargs):
            self.ocr_calls.append(dict(kwargs))
            if self._phase == "settlement.entry":
                return [{"text": "挑战失败"}, {"text": "对局评价"}, {"text": "下一步"}]
            if self._phase == "settlement.followup":
                return [{"text": "1-1M奖励"}, {"text": "标准博弈"}, {"text": "下一页"}]
            if self._phase == "settlement.return":
                return [{"text": "小队生命值"}, {"text": "总经济"}, {"text": "返回货币战争"}]
            return [{"text": "货币战争"}]

    runtime = Runtime()
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home"},
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [1]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-whole-run-continue",
        mode="continue",
        difficulty="current",
        battle_mode="standard",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"]["cards"] == cards
    assert envelope["data"]["mode"] == "new"
    assert envelope["data"]["difficulty"] == "current"
    assert envelope["data"]["battle_mode"] == "standard"
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "new",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert runtime.clicks == [
        (960, 908),
        (960, 908),
        (960, 908),
        start_box.center,
        (300, 250),
        entry_new_box.center,
        start_game_box.center,
        settle_box.center,
        boss_preview_box.center,
    ]
    assert runtime.wait_calls == [
        _asset("entry.start"),
        _asset("entry.new"),
        _asset("entry.start_game"),
        _asset("stage.settle"),
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]


@pytest.mark.parametrize(
    (
        "page",
        "requested_mode",
        "battle_mode",
        "existing_entry",
        "locate_results",
        "wait_results",
        "expected_clicks",
        "expected_waits",
        "expected_entry",
    ),
    [
        (
            "entry.new",
            "new",
            "overclock",
            {},
            {_asset("entry.new"): _box("entry.new", left=120, top=220)},
            {
                _asset("entry.new"): _box("entry.new", left=120, top=220),
                _asset("entry.start_game"): _box("entry.start_game", left=220, top=320),
                _asset("stage.settle"): _box("stage.settle", left=320, top=420),
                _asset("stage.boss_preview"): _box("stage.boss_preview", left=420, top=520),
                _asset("entry.invest_environment"): _box("entry.invest_environment", left=520, top=620),
            },
            [(300, 450), (140, 230), (240, 330), (340, 430), (440, 530)],
            [
                _asset("entry.new"),
                _asset("entry.start_game"),
                _asset("stage.settle"),
                _asset("stage.boss_preview"),
                _asset("entry.invest_environment"),
            ],
            {
                "page": "invest",
                "mode": "new",
                "difficulty": "current",
                "battle_mode": "overclock",
            },
        ),
        (
            "entry.continue",
            "new",
            "overclock",
            {
                "page": "home",
                "mode": "continue",
                "difficulty": "lowest",
                "battle_mode": "standard",
            },
            {
                _asset("entry.continue"): _box("entry.continue", left=200, top=300),
            },
            {
                _asset("entry.continue"): _box("entry.continue", left=200, top=300),
                _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
                _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
            },
            [(300, 450), (220, 310), (320, 410)],
            [
                _asset("entry.continue"),
                _asset("stage.boss_preview"),
                _asset("entry.invest_environment"),
            ],
                {
                    "page": "invest",
                    "mode": "continue",
                    "difficulty": "lowest",
                    "battle_mode": "overclock",
                },
            ),
    ],
)
def test_cw_start_continues_pages_between_home_and_invest(
    tmp_path: Path,
    monkeypatch,
    page: str,
    requested_mode: str,
    battle_mode: str,
    existing_entry: dict[str, object],
    locate_results: dict[str, object],
    wait_results: dict[str, object],
    expected_clicks: list[tuple[int, int]],
    expected_waits: list[str],
    expected_entry: dict[str, object],
):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    cards = _portal_cards()
    runtime = StartRuntime(locate_results=locate_results, wait_results=wait_results, ocr_result=[{"text": page}])
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": dict(existing_entry),
        "slots": {"stale": False, "hand": ["希儿"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [1]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=f"req-cw-start-{page.replace('.', '-')}",
        mode=requested_mode,
        difficulty="current",
        battle_mode=battle_mode,
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"]["cards"] == cards
    assert envelope["data"]["mode"] == expected_entry["mode"]
    assert envelope["data"]["difficulty"] == expected_entry["difficulty"]
    assert envelope["data"]["battle_mode"] == expected_entry["battle_mode"]
    assert persisted.scene_state["cw"]["entry"] == expected_entry
    assert persisted.scene_state["cw"]["slots"] == {"stale": True, "hand": ["希儿"]}
    assert persisted.scene_state["cw"]["shop"] == {"stale": True, "opened": True}
    assert persisted.scene_state["cw"]["sell_plan"] == {"stale": True}
    assert runtime.clicks == expected_clicks
    assert runtime.wait_calls == expected_waits


def test_cw_start_entry_new_allows_highest_when_already_selected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    cards = _portal_cards()
    entry_new_box = _box("entry.new", left=140, top=180)
    start_game_box = _box("entry.start_game", left=240, top=280)
    settle_box = _box("stage.settle", left=340, top=380)
    boss_preview_box = _box("stage.boss_preview", left=440, top=480)
    invest_box = _box("entry.invest_environment", left=540, top=580)
    runtime = StartRuntime(
        locate_results={
            _asset("entry.new"): entry_new_box,
        },
        wait_results={
            _asset("entry.new"): entry_new_box,
            _asset("entry.start_game"): start_game_box,
            _asset("stage.settle"): settle_box,
            _asset("stage.boss_preview"): boss_preview_box,
            _asset("entry.invest_environment"): invest_box,
        },
        ocr_result=[{"text": "货币战争"}],
    )
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home"},
        "slots": {"stale": False, "hand": ["银狼"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [2]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-entry-new-highest-already",
        mode="new",
        difficulty="highest",
        battle_mode="standard",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == {
        "cards": cards,
        "mode": "new",
        "difficulty": "highest",
        "battle_mode": "standard",
        "stale": False,
    }
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "new",
        "difficulty": "highest",
        "battle_mode": "standard",
    }
    assert persisted.scene_state["cw"]["slots"] == {"stale": True, "hand": ["银狼"]}
    assert persisted.scene_state["cw"]["shop"] == {"stale": True, "opened": True}
    assert persisted.scene_state["cw"]["sell_plan"] == {"stale": True}
    assert runtime.clicks == [
        (300, 250),
        entry_new_box.center,
        start_game_box.center,
        settle_box.center,
        boss_preview_box.center,
    ]
    assert runtime.wait_calls == [
        _asset("entry.new"),
        _asset("entry.start_game"),
        _asset("stage.settle"),
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]


def test_cw_start_entry_new_clicks_highest_when_button_visible(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    cards = _portal_cards()
    entry_new_box = _box("entry.new", left=140, top=180)
    highest_box = _box("entry.difficulty.highest", left=190, top=220)
    start_game_box = _box("entry.start_game", left=240, top=280)
    settle_box = _box("stage.settle", left=340, top=380)
    boss_preview_box = _box("stage.boss_preview", left=440, top=480)
    invest_box = _box("entry.invest_environment", left=540, top=580)
    runtime = StartRuntime(
        locate_results={
            _asset("entry.new"): entry_new_box,
            _asset("entry.difficulty.highest"): highest_box,
        },
        wait_results={
            _asset("entry.new"): entry_new_box,
            _asset("entry.start_game"): start_game_box,
            _asset("stage.settle"): settle_box,
            _asset("stage.boss_preview"): boss_preview_box,
            _asset("entry.invest_environment"): invest_box,
        },
        ocr_result=[{"text": "货币战争"}],
    )
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home"},
        "slots": {"stale": False, "hand": ["银狼"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [2]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-entry-new-highest-visible",
        mode="new",
        difficulty="highest",
        battle_mode="standard",
    )

    assert envelope["ok"] is True
    assert runtime.clicks == [
        (300, 250),
        entry_new_box.center,
        highest_box.center,
        start_game_box.center,
        settle_box.center,
        boss_preview_box.center,
    ]


def test_cw_start_boss_preview_preserves_known_entry_truth_source(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    cards = _portal_cards()
    runtime = StartRuntime(
        locate_results={
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
        },
        wait_results={
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
        },
        ocr_result=[{"text": "boss preview"}],
    )
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {
            "page": "home",
            "mode": "continue",
            "difficulty": "lowest",
            "battle_mode": "overclock",
        },
        "slots": {"stale": False, "hand": ["阮梅"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [3]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-boss-preview",
        mode="new",
        difficulty="current",
        battle_mode="standard",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == {
        "cards": cards,
        "mode": "continue",
        "difficulty": "lowest",
        "battle_mode": "overclock",
        "stale": False,
    }
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "continue",
        "difficulty": "lowest",
        "battle_mode": "overclock",
    }
    assert persisted.scene_state["cw"]["slots"] == {"stale": True, "hand": ["阮梅"]}
    assert persisted.scene_state["cw"]["shop"] == {"stale": True, "opened": True}
    assert persisted.scene_state["cw"]["sell_plan"] == {"stale": True}
    assert runtime.clicks == [(320, 410)]
    assert runtime.wait_calls == [
        _asset("stage.boss_preview"),
        _asset("entry.invest_environment"),
    ]


def test_cw_start_entry_continue_requires_recorded_difficulty_before_side_effects(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    runtime = StartRuntime(
        locate_results={
            _asset("entry.continue"): _box("entry.continue", left=200, top=300),
        },
        wait_results={
            _asset("entry.continue"): _box("entry.continue", left=200, top=300),
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
        },
    )
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home", "mode": "continue", "battle_mode": "standard"},
        "slots": {"stale": False, "hand": ["姬子"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [4]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-continue-missing-difficulty",
        mode="new",
        difficulty="current",
        battle_mode="overclock",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "CW_START_ENTRY_TRUTH_REQUIRED",
        "message": "cw start requires recorded difficulty before continuing from entry.continue",
    }
    assert runtime.clicks == []
    assert persisted.scene_state["cw"]["entry"] == {"page": "home", "mode": "continue", "battle_mode": "standard"}
    assert persisted.scene_state["cw"]["slots"] == {"stale": False, "hand": ["姬子"]}


def test_cw_start_boss_preview_requires_complete_recorded_entry_before_side_effects(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    runtime = StartRuntime(
        locate_results={
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
        },
        wait_results={
            _asset("stage.boss_preview"): _box("stage.boss_preview", left=300, top=400),
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
        },
    )
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "home", "mode": "continue", "difficulty": "lowest"},
        "slots": {"stale": False, "hand": ["卡芙卡"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [5]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-boss-preview-missing-entry",
        mode="new",
        difficulty="current",
        battle_mode="standard",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "CW_START_ENTRY_TRUTH_REQUIRED",
        "message": "cw start requires recorded mode/difficulty/battle_mode before continuing from stage.boss_preview",
    }
    assert runtime.clicks == []
    assert persisted.scene_state["cw"]["entry"] == {"page": "home", "mode": "continue", "difficulty": "lowest"}
    assert persisted.scene_state["cw"]["slots"] == {"stale": False, "hand": ["卡芙卡"]}


def test_cw_start_noops_on_invest_and_backfills_entry_params(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    runtime = StartRuntime(
        locate_results={
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
        },
        ocr_result=[{"text": "invest"}],
    )
    cards = _portal_cards()
    monkeypatch.setattr("trail.daemon.cw_service.fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr("trail.daemon.cw_service.summarize_portal_cards", lambda pieces, portal_list, collection_matches=None: cards)
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {"page": "invest"},
        "slots": {"stale": False, "hand": ["银狼"]},
        "shop": {"stale": False, "opened": True},
        "sell_plan": {"stale": False, "steps": [2]},
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-invest",
        mode="new",
        difficulty="highest",
        battle_mode="overclock",
    )
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == {
        "cards": cards,
        "mode": "new",
        "difficulty": "highest",
        "battle_mode": "overclock",
        "stale": False,
    }
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "new",
        "difficulty": "highest",
        "battle_mode": "overclock",
    }
    assert persisted.scene_state["cw"]["slots"] == {"stale": True, "hand": ["银狼"]}
    assert persisted.scene_state["cw"]["shop"] == {"stale": True, "opened": True}
    assert persisted.scene_state["cw"]["sell_plan"] == {"stale": True}
    assert runtime.clicks == []
    assert runtime.wait_calls == []


def test_cw_start_noop_rejects_conflicting_entry_params(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    runtime = StartRuntime(
        locate_results={
            _asset("entry.invest_environment"): _box("entry.invest_environment", left=400, top=500),
        },
    )
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    session.scene_state["cw"] = {
        "entry": {
            "page": "invest",
            "mode": "continue",
            "difficulty": "current",
            "battle_mode": "standard",
        }
    }
    service.save_session(session)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-invest-conflict",
        mode="new",
        difficulty="current",
        battle_mode="standard",
    )
    status = registry.for_workspace(str(tmp_path)).request_status("req-cw-start-invest-conflict")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "CW_START_ENTRY_CONFLICT",
        "message": "cw start conflicts with recorded mode: continue != new",
    }
    assert status["final_state"] == "failed_before_side_effect"
    assert persisted.scene_state["cw"]["entry"] == {
        "page": "invest",
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    assert runtime.clicks == []


def test_cw_start_rejects_in_game_page(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("trail.scenes.cw.entry._detect_cw_stage_from_ocr", lambda runtime: None)
    runtime = StartRuntime(
        locate_results={
            _asset("stage.shop"): _box("stage.shop", left=500, top=600),
        }
    )
    registry, service, session, command_service = _build_cw_harness(tmp_path, runtime=runtime)

    envelope = _run_cw_start(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-start-in-game",
        mode="continue",
        difficulty="current",
        battle_mode="standard",
    )
    status = registry.for_workspace(str(tmp_path)).request_status("req-cw-start-in-game")

    assert envelope["ok"] is False
    assert envelope["data"] == {"page": "in_game", "stage": "shop"}
    assert envelope["error"] == {
        "code": "CW_START_PAGE_INVALID",
        "message": "cw start only supports home, pre-invest pages, or invest, current page: in_game, stage: shop",
    }
    assert status["final_state"] == "failed_before_side_effect"
