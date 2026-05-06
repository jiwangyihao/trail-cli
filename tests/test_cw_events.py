from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from trail.core.errors import TrailError
from trail.scenes.cw.models import ensure_cw_state
from trail.session.store import SessionStore


def result_text(result) -> str:
    return result.stdout + getattr(result, "stderr", "")


def load_cw_events_module():
    try:
        return importlib.import_module("trail.scenes.cw.events")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.events: {exc}")


def _ocr_piece(text: str, *, left: int = 400, top: int = 500, width: int = 120, height: int = 36) -> dict[str, object]:
    return {
        "text": text,
        "box": {"left": left, "top": top, "width": width, "height": height},
    }


class EventRouterRuntime:
    def __init__(self, pieces: list[dict[str, object]] | None = None):
        self.pieces: list[dict[str, object]] = pieces or []
        self.clicks: list[tuple[int, int]] = []
        self.locate_calls: list[str] = []
        self.wait_calls: list[str] = []
        self.locate_result: dict[str, int] | None = None
        self.wait_result: dict[str, int] | None = None

    def ocr(self, **_kwargs: object) -> list[dict[str, object]]:
        return self.pieces

    def locate(self, path: str) -> dict[str, int] | None:
        self.locate_calls.append(path)
        return self.locate_result

    def wait_img(self, path: str, **_kwargs: object) -> dict[str, int] | None:
        self.wait_calls.append(path)
        return self.wait_result

    def click_point(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


EVENT_NEXT_ACTIONS = {
    "replenish": "cw.replenish.choose",
    "invest": "cw.invest.choose",
    "encounter": "cw.encounter.choose",
    "fortune": "cw.fortune.choose",
}


@pytest.mark.parametrize(
    ("method_name", "expected_options"),
    [
        ("read_cw_replenish", [1, 2, 3]),
        ("read_cw_invest", [1, 2, 3]),
        ("read_cw_encounter", [1, 2]),
        ("read_cw_fortune", [1, 2]),
    ],
)
def test_event_reads_return_expected_options(tmp_path, method_name, expected_options):
    events_module = load_cw_events_module()
    method = getattr(events_module, method_name, None)
    assert method is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)

    assert method(session) == {"options": expected_options}


@pytest.mark.parametrize(
    ("method_name", "option"),
    [
        ("choose_cw_replenish", 1),
        ("choose_cw_invest", 2),
        ("choose_cw_encounter", 1),
        ("choose_cw_fortune", 2),
    ],
)
def test_event_chooses_call_chooser_and_invalidate_stage_snapshot(tmp_path, method_name, option):
    events_module = load_cw_events_module()
    method = getattr(events_module, method_name, None)
    assert method is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["stage"] = {"value": "shop", "stale": False}
    session.last_stage = {"scene": "cw", "value": "shop"}
    chosen: list[int] = []

    refreshed = method(session, option=option, chooser=lambda selected: chosen.append(selected))

    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.last_stage is None
    assert chosen == [option]


def test_event_handle_returns_type_and_action(tmp_path):
    events_module = load_cw_events_module()
    handle_cw_event = getattr(events_module, "handle_cw_event", None)
    assert handle_cw_event is not None

    from tests.conftest import build_fake_cw_session, fake_event_handler

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["stage"] = {"value": "event", "stale": False}
    session.last_stage = {"scene": "cw", "value": "event"}
    result = handle_cw_event(session, handler=fake_event_handler)

    assert result == {"event_type": "special", "handled": True, "handled_action": "confirm"}
    assert session.scene_state["cw"]["stage"] == {"stale": True}
    assert session.last_stage is None


def test_detect_cw_event_unknown_does_not_click_runtime():
    events_module = load_cw_events_module()
    runtime = EventRouterRuntime([_ocr_piece("无法识别的新事件")])

    result = events_module.build_cw_event_router(runtime)(None)

    assert result["event_type"] == "unknown"
    assert result["handled"] is False
    assert result["next_action"] == "manual"
    assert runtime.clicks == []


def test_detect_cw_event_unknown_ignores_stage_event_template_hit(monkeypatch):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias, raising=False)
    runtime = EventRouterRuntime([_ocr_piece("无法识别的新事件")])
    runtime.locate_result = {"left": 100, "top": 100, "width": 20, "height": 20}

    result = events_module.build_cw_event_router(runtime)(None)

    assert result["event_type"] == "unknown"
    assert result["handled"] is False
    assert result["next_action"] == "manual"
    assert runtime.locate_calls == []
    assert runtime.clicks == []


@pytest.mark.parametrize(
    ("text", "event_type", "next_action"),
    [
        ("补给", "replenish", "cw.replenish.choose"),
        ("投资事件", "invest", "cw.invest.choose"),
        ("遭遇事件", "encounter", "cw.encounter.choose"),
        ("命运卜者", "fortune", "cw.fortune.choose"),
    ],
)
def test_detect_cw_event_recognized_unhandled_is_no_click(text: str, event_type: str, next_action: str):
    events_module = load_cw_events_module()
    runtime = EventRouterRuntime([_ocr_piece(text)])

    result = events_module.build_cw_event_router(runtime)(None)

    assert result["event_type"] == event_type
    assert result["handled"] is False
    assert result["next_action"] == next_action
    assert runtime.clicks == []


def _fill_fresh_event_session_facts(session) -> dict[str, object]:
    cw_state = ensure_cw_state(session)
    cw_state["stage"] = {
        "value": "event",
        "stale": False,
        "status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
    }
    cw_state["slots"] = {"stale": False, "front": [{"name": "希儿"}], "back": [], "hand": [{"name": "佩拉"}]}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼", "cost": 3}], "coins": 40}
    cw_state["equipment"] = {"stale": False, "items": [{"name": "风暴"}], "uncertain": 0}
    cw_state["strategy"] = {"stale": False, "cards": [{"strategy_title": "快攻"}]}
    cw_state["sell_plan"] = {"stale": False, "items": [{"name": "佩拉", "priority": 10}]}
    cw_state["metrics"] = {"crystals": 2}
    session.last_stage = {"scene": "cw", "value": "event"}
    return deepcopy(cw_state)


@pytest.mark.parametrize(
    ("event_type", "next_action"),
    [(event_type, next_action) for event_type, next_action in EVENT_NEXT_ACTIONS.items()],
)
def test_handle_cw_event_recognized_unhandled_does_not_mark_stale_or_click(tmp_path, event_type: str, next_action: str):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    before_cw_state = _fill_fresh_event_session_facts(session)
    before_last_stage = deepcopy(session.last_stage)
    runtime = EventRouterRuntime()

    result = events_module.handle_cw_event(
        session,
        router=lambda _choice: {"event_type": event_type, "handled": False, "next_action": next_action},
    )

    assert result["event_type"] == event_type
    assert result["handled"] is False
    assert result["next_action"] == next_action
    assert session.scene_state["cw"] == before_cw_state
    assert session.last_stage == before_last_stage
    assert ensure_cw_state(session)["metrics"] == before_cw_state["metrics"]
    assert runtime.clicks == []


def test_handle_cw_event_lv999_without_choice_does_not_mark_stale(tmp_path):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    before_cw_state = _fill_fresh_event_session_facts(session)
    before_last_stage = deepcopy(session.last_stage)
    runtime = EventRouterRuntime([_ocr_piece("银狼 LV.999 选择强化方向")])

    result = events_module.handle_cw_event(session, router=events_module.build_cw_event_router(runtime))

    assert result["event_type"] == "lv999_choice"
    assert result["handled"] is False
    assert result["next_action"] == "manual"
    assert session.scene_state["cw"] == before_cw_state
    assert session.last_stage == before_last_stage
    assert runtime.clicks == []


def test_handle_cw_event_unhandled_with_variable_cost_choice_does_not_mutate_session(tmp_path):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    before_cw_state = _fill_fresh_event_session_facts(session)
    cw_state = ensure_cw_state(session)
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}
    before_cw_state = deepcopy(cw_state)
    before_last_stage = deepcopy(session.last_stage)
    runtime = EventRouterRuntime()

    with pytest.raises(ValueError, match="unhandled cw event cannot apply variable cost choice"):
        events_module.handle_cw_event(
            session,
            router=lambda _choice: {"event_type": "replenish", "handled": False, "next_action": "cw.replenish.choose"},
            variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
        )

    assert session.scene_state["cw"] == before_cw_state
    assert session.last_stage == before_last_stage
    assert ensure_cw_state(session)["shop"] == before_cw_state["shop"]
    assert runtime.clicks == []


def test_handle_cw_event_unknown_marks_readable_facts_stale_without_touching_metrics(tmp_path):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    _fill_fresh_event_session_facts(session)
    cw_state = ensure_cw_state(session)
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 4, "star": 1}}
    before_metrics = deepcopy(cw_state["metrics"])
    before_last_result = deepcopy(session.last_result)
    before_last_screenshot = deepcopy(session.last_screenshot)

    result = events_module.handle_cw_event(
        session,
        router=lambda _choice: {"event_type": "unknown", "handled": False, "next_action": "manual"},
    )

    assert result == {"event_type": "unknown", "handled": False, "next_action": "manual"}
    assert cw_state["stage"]["stale"] is True
    assert cw_state["stage"]["status"]["stale"] is True
    assert cw_state["slots"]["stale"] is True
    assert cw_state["shop"]["stale"] is True
    assert cw_state["equipment"]["stale"] is True
    assert cw_state["sell_plan"]["stale"] is True
    assert cw_state["strategy"]["stale"] is True
    assert cw_state["variable_cost_roles_stale"] is True
    assert "stale" not in cw_state["variable_cost_roles"]
    assert "stale" not in cw_state["variable_cost_roles"]["银狼LV.999"]
    assert cw_state["metrics"] == before_metrics
    assert session.last_result == before_last_result
    assert session.last_screenshot == before_last_screenshot


def test_handle_cw_event_unknown_creates_missing_readable_subtrees(tmp_path):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    cw_state = ensure_cw_state(session)
    for key in ("slots", "shop", "equipment", "sell_plan", "strategy"):
        cw_state.pop(key, None)

    events_module.handle_cw_event(
        session,
        router=lambda _choice: {"event_type": "unknown", "handled": False, "next_action": "manual"},
    )

    assert cw_state["slots"] == {"stale": True}
    assert cw_state["shop"] == {"stale": True}
    assert cw_state["equipment"] == {"stale": True}
    assert cw_state["sell_plan"] == {"stale": True}
    assert cw_state["strategy"] == {"cards": [], "stale": True}
    assert cw_state["variable_cost_roles_stale"] is True


def test_handle_cw_event_rejects_variable_choice_on_non_lv999_detection_without_click(tmp_path):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    runtime = EventRouterRuntime([_ocr_piece("特殊事件确认")])

    with pytest.raises(TrailError) as exc_info:
        events_module.handle_cw_event(
            session,
            router=events_module.build_cw_event_router(runtime),
            variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
        )

    assert exc_info.value.code == "CW_EVENT_CHOICE_MISMATCH"
    assert runtime.clicks == []


def test_handle_cw_event_rejects_lv999_missing_option_center_without_click(tmp_path):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    runtime = EventRouterRuntime([_ocr_piece("银狼 LV.999 选择强化方向")])

    with pytest.raises(TrailError) as exc_info:
        events_module.handle_cw_event(
            session,
            router=events_module.build_cw_event_router(runtime),
            variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
        )

    assert exc_info.value.code == "CW_EVENT_CHOICE_TARGET_MISSING"
    assert runtime.clicks == []


def test_handle_cw_event_rejects_lv999_stale_state_before_click_or_apply(tmp_path):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    cw_state = ensure_cw_state(session)
    cw_state["variable_cost_roles_stale"] = True
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}
    before_cw_state = deepcopy(cw_state)
    runtime = EventRouterRuntime([_ocr_piece("银狼 LV.999 选择强化方向"), _ocr_piece("提升费用")])

    with pytest.raises(TrailError) as exc_info:
        events_module.handle_cw_event(
            session,
            router=events_module.build_cw_event_router(runtime),
            variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
        )

    assert exc_info.value.code == "CW_EVENT_LV999_STATE_STALE"
    assert str(exc_info.value) == "银狼LV.999 状态已过期，请先运行 cw.event.reconcile"
    assert cw_state == before_cw_state
    assert runtime.clicks == []


def test_handle_cw_event_rejects_lv999_cost_up_at_max_cost_before_click_or_mutation(tmp_path):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    cw_state = ensure_cw_state(session)
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 5, "star": 2, "choice_available": True}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 5}]}
    before_cw_state = deepcopy(cw_state)
    runtime = EventRouterRuntime([_ocr_piece("银狼 LV.999 选择强化方向"), _ocr_piece("提升费用")])

    with pytest.raises(TrailError) as exc_info:
        events_module.handle_cw_event(
            session,
            router=events_module.build_cw_event_router(runtime),
            variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
        )

    assert exc_info.value.code == "CW_EVENT_CHOICE_MISMATCH"
    assert cw_state == before_cw_state
    assert runtime.clicks == []


@pytest.mark.parametrize(
    "role_state",
    [
        {"cost": 3, "star": 2, "choice_available": False},
        {"cost": 3, "star": 1, "choice_available": True},
        {"star": 2, "choice_available": True},
        {"cost": 3, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {3: "equipment"}},
    ],
)
def test_handle_cw_event_rejects_invalid_lv999_session_state_before_click_or_mutation(tmp_path, role_state):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    cw_state = ensure_cw_state(session)
    cw_state["variable_cost_roles"] = {"银狼LV.999": dict(role_state)}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}
    before_cw_state = deepcopy(cw_state)
    runtime = EventRouterRuntime([_ocr_piece("银狼 LV.999 选择强化方向"), _ocr_piece("提升费用")])

    with pytest.raises(TrailError) as exc_info:
        events_module.handle_cw_event(
            session,
            router=events_module.build_cw_event_router(runtime),
            variable_cost_choice={"role_name": "银狼LV.999", "choice": "cost_up"},
        )

    assert exc_info.value.code == "CW_EVENT_CHOICE_MISMATCH"
    assert cw_state == before_cw_state
    assert runtime.clicks == []


@pytest.mark.parametrize(
    "payload",
    [
        {"role_name": "银狼", "choice": "cost_up"},
        {"role_name": "银狼LV.999", "choice": "reroll"},
        {"role_name": "银狼LV.999"},
    ],
)
def test_handle_cw_event_rejects_invalid_lv999_choice_payload_before_click(tmp_path, payload):
    events_module = load_cw_events_module()
    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    runtime = EventRouterRuntime([_ocr_piece("银狼 LV.999 选择强化方向"), _ocr_piece("提升费用")])

    with pytest.raises(TrailError) as exc_info:
        events_module.handle_cw_event(session, router=events_module.build_cw_event_router(runtime), variable_cost_choice=payload)

    assert exc_info.value.code == "CW_EVENT_CHOICE_MISMATCH"
    assert runtime.clicks == []


def test_special_confirm_router_uses_legacy_confirm_flow(monkeypatch):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias, raising=False)
    runtime = EventRouterRuntime([_ocr_piece("特殊事件确认")])

    result = events_module.build_cw_event_router(runtime)(None)

    assert result["event_type"] == "special_confirm"
    assert result["handled"] is True
    assert result["handled_action"] == "confirm"
    assert runtime.locate_calls == []
    assert runtime.clicks == [events_module.SPECIAL_EVENT_OPTION_POINT, events_module.SPECIAL_EVENT_CONFIRM_POINT]


def test_lv999_choice_router_reports_confirm_action_with_explicit_choice():
    events_module = load_cw_events_module()
    runtime = EventRouterRuntime([
        _ocr_piece("银狼 LV.999 选择强化方向"),
        _ocr_piece("提升费用", left=700, top=520, width=160, height=40),
    ])

    result = events_module.build_cw_event_router(runtime)({"role_name": "银狼LV.999", "choice": "cost_up"})

    assert result == {"event_type": "lv999_choice", "handled": True, "handled_action": "confirm"}
    assert runtime.clicks == [(780, 540)]


@pytest.mark.parametrize(
    ("method_name", "callback_name"),
    [
        ("confirm_cw_boss_preview", "confirmer"),
        ("settle_cw_next", "continuer"),
        ("start_cw_battle", "starter"),
        ("continue_cw_battle", "continuer"),
    ],
)
def test_boss_preview_settle_and_battle_actions_invalidate_stage_snapshot(tmp_path, method_name, callback_name):
    events_module = load_cw_events_module()
    method = getattr(events_module, method_name, None)
    assert method is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    ensure_cw_state(session)["stage"] = {"value": "battle", "stale": False}
    session.last_stage = {"scene": "cw", "value": "battle"}
    calls: list[str] = []

    refreshed = method(session, **{callback_name: lambda: calls.append("called")})

    assert refreshed.scene_state["cw"]["stage"] == {"stale": True}
    assert refreshed.last_stage is None
    assert calls == ["called"]


def test_build_cw_battle_starter_waits_for_template_before_clicking_center(monkeypatch, fake_runtime):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias, raising=False)
    fake_runtime.wait_result = {"left": 100, "top": 200, "width": 60, "height": 20}

    events_module.build_cw_battle_starter(fake_runtime)()

    assert fake_runtime.wait_calls == ["action.battle_start"]
    assert fake_runtime.locate_calls == []
    assert fake_runtime.clicks == [(130, 210)]


def test_build_cw_battle_starter_cancels_team_count_confirm_dialog(monkeypatch, fake_runtime):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias, raising=False)
    fake_runtime.wait_result = {"left": 100, "top": 200, "width": 60, "height": 20}
    ocr_calls: list[dict[str, object]] = []

    def fake_ocr(**kwargs):
        ocr_calls.append(kwargs)
        return [
            _ocr_piece("提示", left=820, top=300, width=100, height=36),
            _ocr_piece("可出战角色人数未达上限，是否确认出战？", left=620, top=430, width=680, height=36),
            _ocr_piece("取消", left=380, top=600, width=100, height=36),
            _ocr_piece("确认", left=1160, top=600, width=100, height=36),
        ]

    monkeypatch.setattr(fake_runtime, "ocr", fake_ocr)

    with pytest.raises(TrailError) as exc_info:
        events_module.build_cw_battle_starter(fake_runtime)()

    assert exc_info.value.code == "CW_BATTLE_TEAM_COUNT_INSUFFICIENT"
    assert "可出战角色人数未达上限" in str(exc_info.value)
    assert "检查场上人数" in str(exc_info.value)
    assert fake_runtime.clicks == [(130, 210), (430, 618)]
    assert ocr_calls


def test_cw_event_handle_flows_through_command_service_journal(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    monkeypatch.setattr("trail.daemon.cw_service.event_router_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.handle_cw_event",
        lambda session, router, variable_cost_choice=None: _set_event_result(
            session, {"event_type": "special", "handled": True, "handled_action": "confirm"}
        ),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-handle-1",
        method="cw.event.handle",
        payload={},
    )
    status = service.request_status("req-event-handle-1")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert envelope["data"] == {"event_type": "special", "handled": True, "handled_action": "confirm"}
    assert status["final_state"] == "completed"
    assert persisted.scene_state["cw"]["stage"] == {"stale": True}


def test_cw_event_handle_unknown_daemon_response_includes_recovery_facts(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    before_cw_state = _fill_fresh_event_session_facts(session)
    cw_state = ensure_cw_state(session)
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    cw_state["variable_cost_roles_stale"] = False
    before_cw_state = deepcopy(cw_state)
    service.save_session(session)
    monkeypatch.setattr(
        "trail.daemon.cw_service.event_router_factory",
        lambda runtime: lambda choice: {"event_type": "unknown", "handled": False, "next_action": "manual"},
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-handle-unknown-real",
        method="cw.event.handle",
        payload={},
    )
    persisted_cw_state = ensure_cw_state(service.load_session(session.session_id))
    data = cast(dict[str, object], envelope.get("data"))

    assert envelope["ok"] is True
    assert data["event_type"] == "unknown"
    assert data["handled"] is False
    assert data["next_action"] == "manual"
    assert data["stale"] is True
    assert data["stale_facts"] == "stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles"
    assert data["crystals_stale"] is False
    assert data["reconcile_action"] == "cw.event.reconcile"
    assert cast(dict[str, object], persisted_cw_state["stage"])["stale"] is True
    assert cast(dict[str, object], persisted_cw_state["slots"])["stale"] is True
    assert cast(dict[str, object], persisted_cw_state["shop"])["stale"] is True
    assert cast(dict[str, object], persisted_cw_state["equipment"])["stale"] is True
    assert cast(dict[str, object], persisted_cw_state["strategy"])["stale"] is True
    assert cast(dict[str, object], persisted_cw_state["sell_plan"])["stale"] is True
    assert persisted_cw_state["variable_cost_roles_stale"] is True
    assert persisted_cw_state["metrics"] == before_cw_state["metrics"]


def test_cw_event_handle_payload_confirmed_lv999_choice_updates_persisted_shop_snapshot(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    cw_state = ensure_cw_state(session)
    cw_state["variable_cost_roles"] = {
        "银狼LV.999": {"cost": 3, "star": 2, "choice_available": True, "confirmed_choices_by_cost": {}}
    }
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}
    service.save_session(session)
    monkeypatch.setattr(
        "trail.daemon.cw_service.event_router_factory",
        lambda runtime: lambda choice: {"event_type": "lv999_choice", "handled": True, "handled_action": "confirm"},
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-handle-choice",
        method="cw.event.handle",
        payload={"variable_cost_choice": {"role_name": "银狼LV.999", "choice": "cost_up"}},
    )
    persisted = service.load_session(session.session_id)

    data = envelope.get("data")
    assert isinstance(data, dict)
    assert data["handled_action"] == "confirm"
    assert data["variable_cost_choice"] == {"role_name": "银狼LV.999", "choice": "cost_up"}
    persisted_cw_state = cast(dict[str, object], persisted.scene_state["cw"])
    persisted_roles = cast(dict[str, object], persisted_cw_state["variable_cost_roles"])
    persisted_role_state = cast(dict[str, object], persisted_roles["银狼LV.999"])
    persisted_shop = cast(dict[str, object], persisted_cw_state["shop"])
    persisted_items = cast(list[dict[str, object]], persisted_shop["items"])
    assert persisted_role_state["cost"] == 4
    assert persisted_role_state["star"] == 1
    assert persisted_shop["stale"] is False
    assert persisted_items[0]["cost"] == 4


def test_cw_event_handle_payload_without_variable_choice_keeps_legacy_behavior(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    cw_state = ensure_cw_state(session)
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2, "choice_available": True}}
    cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "银狼LV.999", "cost": 3}]}
    service.save_session(session)
    monkeypatch.setattr(
        "trail.daemon.cw_service.event_router_factory",
        lambda runtime: lambda choice: {"event_type": "special_confirm", "handled": True, "handled_action": "confirm"},
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-handle-no-choice",
        method="cw.event.handle",
        payload={},
    )
    persisted = service.load_session(session.session_id)

    assert envelope.get("data") == {"event_type": "special_confirm", "handled": True, "handled_action": "confirm"}
    persisted_cw_state = cast(dict[str, object], persisted.scene_state["cw"])
    persisted_roles = cast(dict[str, object], persisted_cw_state["variable_cost_roles"])
    persisted_role_state = cast(dict[str, object], persisted_roles["银狼LV.999"])
    persisted_shop = cast(dict[str, object], persisted_cw_state["shop"])
    persisted_items = cast(list[dict[str, object]], persisted_shop["items"])
    assert persisted_role_state["cost"] == 3
    assert persisted_items[0]["cost"] == 3


@pytest.mark.parametrize(
    ("builder_name", "expected_wait_calls", "expected_locate_calls", "ocr_text", "expected_click"),
    [
        (
            "build_cw_battle_starter",
            ["action.battle_start"],
            [],
            "开始挑战",
            (460, 518),
        ),
        (
            "build_cw_battle_starter",
            ["action.battle_start"],
            [],
            "出战",
            (460, 518),
        ),
        (
            "build_cw_battle_continuer",
            [],
            ["action.battle_continue"],
            "继续挑战",
            (460, 518),
        ),
        (
            "build_cw_settle_continuer",
            [],
            ["stage.settle", "action.settle_next_page"],
            "下一页",
            (460, 518),
        ),
    ],
)
def test_cw_action_buttons_use_ocr_box_when_template_misses(
    monkeypatch,
    fake_runtime,
    builder_name,
    expected_wait_calls,
    expected_locate_calls,
    ocr_text,
    expected_click,
):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias, raising=False)
    ocr_calls: list[dict[str, object]] = []

    def fake_ocr(**kwargs):
        ocr_calls.append(kwargs)
        return [_ocr_piece(ocr_text)]

    fake_runtime.wait_result = None
    fake_runtime.locate_result = None
    monkeypatch.setattr(fake_runtime, "ocr", fake_ocr)

    getattr(events_module, builder_name)(fake_runtime)()

    assert fake_runtime.wait_calls == expected_wait_calls
    assert fake_runtime.locate_calls == expected_locate_calls
    expected_ocr_calls = [{"capture": events_module.CW_ACTION_OCR_REGION}]
    if builder_name == "build_cw_battle_starter":
        expected_ocr_calls.append({"capture": events_module.CW_BATTLE_TEAM_COUNT_CONFIRM_REGION})
    assert ocr_calls == expected_ocr_calls
    assert fake_runtime.clicks == [expected_click]


@pytest.mark.parametrize(
    ("builder_name", "point_name"),
    [
        ("build_cw_battle_starter", "BATTLE_START_POINT"),
        ("build_cw_battle_continuer", "BATTLE_CONTINUE_POINT"),
        ("build_cw_settle_continuer", "SETTLE_NEXT_POINT"),
    ],
)
def test_cw_action_buttons_fall_back_to_existing_points_when_template_and_ocr_miss(
    monkeypatch,
    fake_runtime,
    builder_name,
    point_name,
):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias, raising=False)
    fake_runtime.wait_result = None
    fake_runtime.locate_result = None
    fake_runtime.ocr_result = []

    getattr(events_module, builder_name)(fake_runtime)()

    assert fake_runtime.clicks == [getattr(events_module, point_name)]


def test_cw_action_buttons_fall_back_to_existing_points_when_ocr_raises(monkeypatch, fake_runtime):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias, raising=False)
    fake_runtime.wait_result = None
    fake_runtime.locate_result = None
    monkeypatch.setattr(fake_runtime, "ocr", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("ocr boom")))

    events_module.build_cw_battle_continuer(fake_runtime)()

    assert fake_runtime.clicks == [events_module.BATTLE_CONTINUE_POINT]


def test_cw_action_buttons_accept_tuple_ocr_piece(monkeypatch, fake_runtime):
    events_module = load_cw_events_module()
    monkeypatch.setattr(events_module, "_asset", lambda alias: alias, raising=False)
    fake_runtime.wait_result = None
    fake_runtime.locate_result = None
    fake_runtime.ocr_result = [([(400, 500), (520, 500), (520, 536), (400, 536)], "下一页", 0.99)]

    events_module.build_cw_settle_continuer(fake_runtime)()

    assert fake_runtime.clicks == [(460, 518)]


def _build_cw_harness(tmp_path: Path):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: SimpleNamespace())
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    return registry, service, session, cw_service, command_service


def _run_cw_mutation(*, command_service, session, workspace_root: Path, request_id: str, method: str, payload: dict[str, object]) -> dict[str, Any]:
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION

    return cast(
        dict[str, Any],
        command_service.handle(
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
    )



def _fact_set(value: object) -> set[str]:
    if not isinstance(value, str) or value == "none":
        return set()
    return {part for part in value.split("|") if part}


def _seed_reconcile_stale_state(session) -> dict[str, object]:
    cw_state = ensure_cw_state(session)
    cw_state["stage"] = {"value": "event", "stale": True, "status": {"stale": True, "level": 7}}
    cw_state["slots"] = {"stale": True, "front": [{"name": "希儿"}], "back": [], "hand": []}
    cw_state["shop"] = {"opened": True, "stale": True, "items": [{"name": "银狼LV.999", "cost": 3}], "coins": 40}
    cw_state["equipment"] = {"stale": True, "items": [{"name": "风暴"}]}
    cw_state["strategy"] = {"stale": True, "cards": [{"strategy_title": "快攻"}]}
    cw_state["sell_plan"] = {"stale": True, "items": [{"name": "佩拉", "priority": 10}]}
    cw_state["metrics"] = {"crystals": 7, "bonus": 2}
    cw_state["variable_cost_roles"] = {"银狼LV.999": {"cost": 3, "star": 2}}
    cw_state["variable_cost_roles_stale"] = True
    session.last_stage = {"scene": "cw", "value": "event"}
    return deepcopy(cw_state)


def _patch_reconcile_stage(monkeypatch, stage_value: str | None) -> None:
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: lambda: stage_value)


def test_cw_event_reconcile_unknown_stage_keeps_stale_and_metrics(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    before_cw_state = _seed_reconcile_stale_state(session)
    cw_state = ensure_cw_state(session)
    cw_state["stage"] = {"value": "event", "stale": True, "status": {"stale": False, "level": 7}}
    before_cw_state = deepcopy(cw_state)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, None)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-unknown",
        method="cw.event.reconcile",
        payload={},
    )
    persisted = service.load_session(session.session_id)
    persisted_cw_state = cast(dict[str, object], persisted.scene_state["cw"])
    data = cast(dict[str, object], envelope.get("data"))

    assert envelope["ok"] is True
    assert data["stage"] == "unknown"
    assert data["stale"] is True
    assert data["reconciled"] == "none"
    assert data["next_action"] == "manual"
    assert {"stage/status", "slots", "shop", "equipment", "strategy", "sell_plan", "variable_cost_roles"}.issubset(
        _fact_set(data["stale_facts"])
    )
    persisted_stage = cast(dict[str, object], persisted_cw_state["stage"])
    persisted_status = cast(dict[str, object], persisted_stage["status"])
    assert persisted_status["stale"] is True
    assert "crystals" not in data
    assert persisted_cw_state["metrics"] == before_cw_state["metrics"]
    assert persisted.last_stage is None


def test_collect_cw_preparation_facts_include_crystals_false_omits_crystals_key(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module

    _registry, service, session, _cw_service, _command_service = _build_cw_harness(tmp_path)
    cw_state = ensure_cw_state(session)
    cw_state["metrics"] = {"crystals": 5}
    before_metrics = deepcopy(cw_state["metrics"])
    monkeypatch.setattr(
        cw_service_module,
        "collect_cw_crystals",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("crystals collector must not run")),
    )

    data = cw_service_module._collect_cw_preparation_facts(
        session,
        runtime=SimpleNamespace(),
        workspace_root=str(tmp_path),
        include_crystals=False,
        order=(),
    )

    assert "crystals" not in data
    assert ensure_cw_state(session)["metrics"] == before_metrics


def test_cw_event_reconcile_preparation_restores_stale_sell_plan_after_reader_side_effect(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module

    _registry, service, session, _cw_service, command_service = _build_cw_harness(tmp_path)
    before_cw_state = _seed_reconcile_stale_state(session)
    previous_sell_plan = cast(dict[str, object], deepcopy(before_cw_state["sell_plan"]))
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "preparation")

    def fake_collect(session_arg, **kwargs):
        assert kwargs["include_crystals"] is False
        assert kwargs["order"] == ("slots", "shop", "equipment")
        cw_state = ensure_cw_state(session_arg)
        cw_state["slots"] = {"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False}
        cw_state["sell_plan"] = {}
        return {"slots": deepcopy(cw_state["slots"])}

    monkeypatch.setattr(cw_service_module, "_collect_cw_preparation_facts", fake_collect)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-prep-sell-plan",
        method="cw.event.reconcile",
        payload={},
    )
    persisted = service.load_session(session.session_id)
    persisted_cw_state = cast(dict[str, object], persisted.scene_state["cw"])
    data = cast(dict[str, object], envelope.get("data"))

    assert envelope["ok"] is True
    assert "crystals" not in data
    assert persisted_cw_state["sell_plan"] == {**previous_sell_plan, "stale": True}
    assert cast(dict[str, object], persisted_cw_state["strategy"])["stale"] is True
    assert {"strategy", "sell_plan"}.issubset(_fact_set(data["stale_facts"]))
    assert {"strategy", "sell_plan"}.issubset(_fact_set(data["todo"]))


def test_cw_event_reconcile_preparation_collects_no_crystals_and_only_fresh_reconciled(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module

    _registry, service, session, _cw_service, command_service = _build_cw_harness(tmp_path)
    before_cw_state = _seed_reconcile_stale_state(session)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "preparation")

    def fake_collect(session_arg, **kwargs):
        assert kwargs["include_crystals"] is False
        cw_state = ensure_cw_state(session_arg)
        cw_state["slots"] = {"front": [{"name": "希儿"}], "back": [], "hand": [], "stale": False}
        cw_state["shop"] = {"opened": False, "stale": True, "items": []}
        cw_state["equipment"] = {"stale": False, "items": [{"name": "风暴"}]}
        cw_state["sell_plan"] = {}
        return {
            "slots": deepcopy(cw_state["slots"]),
            "shop": deepcopy(cw_state["shop"]),
            "equipment": deepcopy(cw_state["equipment"]),
        }

    monkeypatch.setattr(cw_service_module, "_collect_cw_preparation_facts", fake_collect)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-prep-fresh",
        method="cw.event.reconcile",
        payload={},
    )
    persisted = service.load_session(session.session_id)
    data = cast(dict[str, object], envelope.get("data"))

    assert envelope["ok"] is True
    assert "crystals" not in data
    assert ensure_cw_state(persisted)["metrics"] == before_cw_state["metrics"]
    assert data["reconciled"] == "stage|slots|equipment"
    assert "shop" not in _fact_set(data["reconciled"])
    assert {"shop", "strategy", "sell_plan"}.issubset(_fact_set(data["stale_facts"]))


def test_cw_event_reconcile_shop_uses_current_page_reader_without_reset_or_open(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.scenes.cw import shop as shop_module

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int) -> None:
            point = (x, y)
            self.clicks.append(point)
            if point in {shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT}:
                raise AssertionError(f"shop reconcile must not reset/open already-open shop: {point}")

    runtime = Runtime()
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service.runtime_service = runtime_service
    command_service.runtime_service = runtime_service
    _seed_reconcile_stale_state(session)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "shop")
    calls: list[str] = []

    def current_page_reader_factory(runtime_arg):
        del runtime_arg

        def reader():
            calls.append("page-reader")
            return {
                "opened": True,
                "stale": False,
                "items": [{"name": "希儿", "price": 3}],
                "coins": 40,
                "reserve_full": False,
            }

        return reader

    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {})
    monkeypatch.setattr(cw_service_module, "shop_page_snapshot_reader_factory", current_page_reader_factory)
    monkeypatch.setattr(cw_service_module, "shop_closer_factory", lambda runtime_arg: lambda: calls.append("close"))

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-current-page-shop",
        method="cw.event.reconcile",
        payload={},
    )
    data = cast(dict[str, object], envelope.get("data"))
    persisted_shop = cast(dict[str, object], ensure_cw_state(service.load_session(session.session_id))["shop"])

    assert envelope["ok"] is True
    assert calls == ["page-reader", "close"]
    assert runtime.clicks == []
    assert persisted_shop["opened"] is False
    assert persisted_shop["stale"] is False
    assert "shop" in _fact_set(data["reconciled"])
    assert "shop" not in _fact_set(data["stale_facts"])
    assert "shop" not in _fact_set(data.get("todo"))
    assert "shop" not in _fact_set(data["stale_facts"])


def test_cw_event_reconcile_shop_scans_current_shop_before_close_without_open_shop(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module

    _registry, service, session, _cw_service, command_service = _build_cw_harness(tmp_path)
    _seed_reconcile_stale_state(session)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "shop")
    calls: list[str] = []

    monkeypatch.setattr(
        cw_service_module,
        "open_cw_shop",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reconcile must not open an already-open shop")),
    )
    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {})
    monkeypatch.setattr(cw_service_module, "shop_scan_snapshot_reader_factory", lambda *args, **kwargs: object())
    monkeypatch.setattr(cw_service_module, "shop_closer_factory", lambda runtime: object())

    def fake_scan(session_arg, *, scanner, guide_config=None):
        del scanner, guide_config
        calls.append("scan")
        cw_state = ensure_cw_state(session_arg)
        cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "希儿", "cost": 3}], "coins": 40}
        return SimpleNamespace(session=session_arg, response_snapshot=deepcopy(cw_state["shop"]))

    def fake_close(session_arg, *, closer=None):
        del closer
        calls.append("close")
        shop = dict(ensure_cw_state(session_arg).get("shop") or {})
        shop["opened"] = False
        shop["stale"] = True
        ensure_cw_state(session_arg)["shop"] = shop
        return session_arg

    monkeypatch.setattr(cw_service_module, "scan_cw_shop", fake_scan)
    monkeypatch.setattr(cw_service_module, "close_cw_shop", fake_close)
    monkeypatch.setattr(cw_service_module, "project_cw_shop_snapshot", lambda session_arg, **kwargs: deepcopy(ensure_cw_state(session_arg)["shop"]))

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-shop",
        method="cw.event.reconcile",
        payload={},
    )
    persisted_shop = cast(dict[str, object], ensure_cw_state(service.load_session(session.session_id))["shop"])
    data = cast(dict[str, object], envelope.get("data"))

    assert envelope["ok"] is True
    assert calls == ["scan", "close"]
    assert persisted_shop["opened"] is False
    assert persisted_shop["stale"] is False
    assert "shop" in _fact_set(data["reconciled"])


def test_cw_event_reconcile_shop_scan_failure_keeps_command_ok_and_shop_stale(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module

    _registry, service, session, _cw_service, command_service = _build_cw_harness(tmp_path)
    _seed_reconcile_stale_state(session)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "shop")

    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {})
    monkeypatch.setattr(cw_service_module, "shop_scan_snapshot_reader_factory", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        cw_service_module,
        "scan_cw_shop",
        lambda *args, **kwargs: (_ for _ in ()).throw(TrailError("SHOP_SCAN_FAILED", "shop scan failed")),
    )
    monkeypatch.setattr(
        cw_service_module,
        "close_cw_shop",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("close must not run after scan failure")),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-shop-scan-failure",
        method="cw.event.reconcile",
        payload={},
    )
    persisted_shop = cast(dict[str, object], ensure_cw_state(service.load_session(session.session_id))["shop"])
    data = cast(dict[str, object], envelope.get("data"))

    assert envelope["ok"] is True
    assert "shop" in _fact_set(data["attempted"])
    assert "shop" not in _fact_set(data["reconciled"])
    assert "shop" in _fact_set(data["todo"])
    assert "shop" in _fact_set(data["stale_facts"])
    assert persisted_shop.get("stale") is not False


def test_cw_event_reconcile_shop_close_failure_keeps_command_ok_and_shop_stale(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module

    _registry, service, session, _cw_service, command_service = _build_cw_harness(tmp_path)
    _seed_reconcile_stale_state(session)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "shop")

    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {})
    monkeypatch.setattr(cw_service_module, "shop_scan_snapshot_reader_factory", lambda *args, **kwargs: object())
    monkeypatch.setattr(cw_service_module, "shop_closer_factory", lambda runtime: object())

    def fake_scan(session_arg, *, scanner, guide_config=None):
        del scanner, guide_config
        cw_state = ensure_cw_state(session_arg)
        cw_state["shop"] = {"opened": True, "stale": False, "items": [{"name": "希儿", "cost": 3}], "coins": 40}
        return SimpleNamespace(session=session_arg, response_snapshot=deepcopy(cw_state["shop"]))

    monkeypatch.setattr(cw_service_module, "scan_cw_shop", fake_scan)
    monkeypatch.setattr(
        cw_service_module,
        "close_cw_shop",
        lambda *args, **kwargs: (_ for _ in ()).throw(TrailError("SHOP_CLOSE_FAILED", "shop close failed")),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-shop-close-failure",
        method="cw.event.reconcile",
        payload={},
    )
    persisted_shop = cast(dict[str, object], ensure_cw_state(service.load_session(session.session_id))["shop"])
    data = cast(dict[str, object], envelope.get("data"))

    assert envelope["ok"] is True
    assert "shop" in _fact_set(data["attempted"])
    assert "shop" not in _fact_set(data["reconciled"])
    assert "shop" in _fact_set(data["todo"])
    assert "shop" in _fact_set(data["stale_facts"])
    assert persisted_shop.get("stale") is not False


def test_cw_event_reconcile_reader_failure_goes_to_attempted_todo_not_reconciled(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module

    _registry, service, session, _cw_service, command_service = _build_cw_harness(tmp_path)
    _seed_reconcile_stale_state(session)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "preparation")

    def fail_collect(*args, **kwargs):
        raise TrailError("SLOTS_READ_EMPTY", "slots unavailable")

    monkeypatch.setattr(cw_service_module, "_collect_cw_preparation_facts", fail_collect)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-reader-failure",
        method="cw.event.reconcile",
        payload={},
    )
    data = cast(dict[str, object], envelope.get("data"))

    assert envelope["ok"] is True
    assert "slots" in _fact_set(data["attempted"])
    assert "slots" not in _fact_set(data["reconciled"])
    assert "slots" in _fact_set(data["todo"])
    assert "slots" in _fact_set(data["stale_facts"])
    assert data["why"] == "stale_after_reconcile"


def test_cw_event_reconcile_keeps_fresh_equipment_after_mutation_wrapper(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module

    _registry, service, session, _cw_service, command_service = _build_cw_harness(tmp_path)
    _seed_reconcile_stale_state(session)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "preparation")

    def fake_collect(session_arg, **kwargs):
        cw_state = ensure_cw_state(session_arg)
        cw_state["equipment"] = {"stale": False, "items": [{"name": "风暴"}]}
        return {"equipment": deepcopy(cw_state["equipment"])}

    monkeypatch.setattr(cw_service_module, "_collect_cw_preparation_facts", fake_collect)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-equipment-fresh",
        method="cw.event.reconcile",
        payload={},
    )
    persisted_equipment = cast(dict[str, object], ensure_cw_state(service.load_session(session.session_id))["equipment"])

    assert envelope["ok"] is True
    assert persisted_equipment["stale"] is False


def test_cw_event_reconcile_variable_cost_roles_stale_rules(tmp_path: Path, monkeypatch):
    from trail.daemon import cw_service as cw_service_module
    from trail.scenes.cw.slots import CwSlotsReadResult

    _registry, service, session, _cw_service, command_service = _build_cw_harness(tmp_path)
    _seed_reconcile_stale_state(session)
    service.save_session(session)
    _patch_reconcile_stage(monkeypatch, "preparation")
    snapshots = [
        CwSlotsReadResult(front=[{"name": "希儿"}], back=[], hand=[], stage="preparation", stage_status={"stale": False, "level": 7}),
        CwSlotsReadResult(
            front=[{"name": "银狼LV.999", "cost": 4, "star": 2}],
            back=[],
            hand=[],
            stage="preparation",
            stage_status={"stale": False, "level": 7},
        ),
    ]

    def fake_slots_reader_factory(runtime, targets=None, request_id=None, dismiss_initial_overlay=False):
        del runtime, targets, request_id, dismiss_initial_overlay
        return lambda: snapshots.pop(0)

    def fake_scan(session_arg, *, scanner, guide_config=None):
        del scanner, guide_config
        cw_state = ensure_cw_state(session_arg)
        cw_state["shop"] = {"opened": True, "stale": False, "items": []}
        return SimpleNamespace(session=session_arg, response_snapshot=deepcopy(cw_state["shop"]))

    def fake_close(session_arg, *, closer=None):
        del closer
        shop = dict(ensure_cw_state(session_arg).get("shop") or {})
        shop["opened"] = False
        shop["stale"] = False
        ensure_cw_state(session_arg)["shop"] = shop
        return session_arg

    monkeypatch.setattr(cw_service_module, "dismiss_cw_slots_overlay", lambda runtime: None)
    monkeypatch.setattr(cw_service_module, "slots_reader_factory", fake_slots_reader_factory)
    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {})
    monkeypatch.setattr(cw_service_module, "open_cw_shop", lambda session_arg, opener=None: session_arg)
    monkeypatch.setattr(cw_service_module, "scan_cw_shop", fake_scan)
    monkeypatch.setattr(cw_service_module, "project_cw_shop_snapshot", lambda session_arg, **kwargs: deepcopy(ensure_cw_state(session_arg)["shop"]))
    monkeypatch.setattr(cw_service_module, "close_cw_shop", fake_close)
    monkeypatch.setattr(cw_service_module, "shop_opener_factory", lambda runtime: object())
    monkeypatch.setattr(cw_service_module, "shop_page_snapshot_reader_factory", lambda runtime: object())
    monkeypatch.setattr(cw_service_module, "shop_closer_factory", lambda runtime: object())
    monkeypatch.setattr(cw_service_module, "_apply_cw_equipment_read_with_resources", lambda *args, **kwargs: {"stale": False, "items": []})
    monkeypatch.setattr(cw_service_module, "sleep", lambda seconds: None)

    first = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-lv999-absent",
        method="cw.event.reconcile",
        payload={},
    )
    first_data = cast(dict[str, object], first.get("data"))
    first_cw_state = ensure_cw_state(service.load_session(session.session_id))
    assert first_cw_state["variable_cost_roles_stale"] is True
    assert "variable_cost_roles" in _fact_set(first_data["stale_facts"])

    second = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-event-reconcile-lv999-seen",
        method="cw.event.reconcile",
        payload={},
    )
    second_data = cast(dict[str, object], second.get("data"))
    second_cw_state = ensure_cw_state(service.load_session(session.session_id))
    assert second_cw_state["variable_cost_roles_stale"] is False
    assert second_cw_state["variable_cost_roles"]["银狼LV.999"]["cost"] == 4
    assert "variable_cost_roles" in _fact_set(second_data["reconciled"])
    assert "variable_cost_roles" not in _fact_set(second_data["stale_facts"])


def _set_stage(session, stage: dict[str, object]):
    session.scene_state.setdefault("cw", {})["stage"] = dict(stage)
    session.last_stage = None
    return session


def _set_event_result(session, result: dict[str, object]):
    session.scene_state.setdefault("cw", {})["stage"] = {"stale": True}
    session.last_stage = None
    return result


@pytest.mark.parametrize(
    ("method", "patch_name", "expected_options"),
    [
        ("cw.replenish.read", "read_cw_replenish", [1, 2, 3]),
        ("cw.invest.read", "read_cw_invest", [1, 2, 3]),
        ("cw.encounter.read", "read_cw_encounter", [1, 2]),
        ("cw.fortune.read", "read_cw_fortune", [1, 2]),
    ],
)
def test_cw_event_read_services_return_options(tmp_path: Path, monkeypatch, method: str, patch_name: str, expected_options: list[int]):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr(f"trail.daemon.cw_service.{patch_name}", lambda session: {"options": expected_options})

    result = cw_service.handle(
        method=method,
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert result == {"options": expected_options}


@pytest.mark.parametrize(
    ("method", "request_id", "payload", "setup_patches", "expected_data"),
    [
        (
            "cw.event.handle",
            "req-event-handle-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.event_router_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.handle_cw_event",
                    lambda session, router, variable_cost_choice=None: _set_event_result(
                        session, {"event_type": "special", "handled": True, "handled_action": "confirm"}
                    ),
                ),
            ),
            {"event_type": "special", "handled": True, "handled_action": "confirm"},
        ),
        (
            "cw.replenish.choose",
            "req-replenish-choose-1",
            {"option": 2},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.replenish_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_replenish", lambda session, option, chooser: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.invest.choose",
            "req-invest-choose-1",
            {"option": 1},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.invest_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_invest", lambda session, option, chooser: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.encounter.choose",
            "req-encounter-choose-1",
            {"option": 1},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.encounter_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_encounter", lambda session, option, chooser: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.fortune.choose",
            "req-fortune-choose-1",
            {"option": 2},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.fortune_chooser_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.choose_cw_fortune", lambda session, option, chooser: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.boss_preview.confirm",
            "req-boss-confirm-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.boss_preview_confirmer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.confirm_cw_boss_preview", lambda session, confirmer: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.battle.start",
            "req-battle-start-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.battle_starter_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.start_cw_battle", lambda session, starter: _set_stage(session, {"stale": True})),
                monkeypatch.setattr("trail.daemon.cw_service.sleep", lambda seconds: None),
            ),
            {"stale": True},
        ),
        (
            "cw.battle.continue",
            "req-battle-continue-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.battle_continuer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.continue_cw_battle", lambda session, continuer: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
        (
            "cw.settle.next",
            "req-settle-next-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.settle_continuer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.settle_cw_next", lambda session, continuer: _set_stage(session, {"stale": True})),
            ),
            {"stale": True},
        ),
    ],
)
def test_cw_event_mutation_services_return_expected_data(
    tmp_path: Path,
    monkeypatch,
    method: str,
    request_id: str,
    payload: dict[str, object],
    setup_patches,
    expected_data: dict[str, object],
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, command_service, request_id
    setup_patches(monkeypatch)

    result = cw_service.handle(
        method=method,
        payload={"session_id": session.session_id, **payload},
        workspace_root=str(tmp_path),
        session_service=service,
    )
    persisted = service.load_session(session.session_id)

    assert result == expected_data
    assert persisted.scene_state["cw"]["stage"] == {"stale": True}
