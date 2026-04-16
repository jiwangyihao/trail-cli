from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.core.errors import TrailError
from trail.session.store import SessionStore


def fake_shop_snapshot():
    return ([{"name": "银狼", "price": 20}], 40, 7, False, 8)


def fake_shop_snapshot_after_purchase():
    return ([{"name": "阮·梅", "price": 30}], 22, 8, False, 8)


def fake_shop_snapshot_target_unchanged_other_changed():
    return ([{"name": "银狼", "price": 20}, {"name": "阮·梅", "price": 30}], 22, 8, False, 8)


def load_cw_shop_module():
    try:
        return importlib.import_module("trail.scenes.cw.shop")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.shop: {exc}")


def test_shop_open_marks_shop_opened_and_stale(tmp_path):
    shop_module = load_cw_shop_module()
    open_cw_shop = getattr(shop_module, "open_cw_shop", None)
    assert open_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    opened: list[str] = []
    refreshed = open_cw_shop(session, opener=lambda: opened.append("open"))

    assert refreshed.scene_state["cw"]["shop"] == {
        "stale": True,
        "opened": True,
    }
    assert opened == ["open"]


def test_shop_scan_refreshes_store_snapshot(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    assert scan_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    refreshed = scan_cw_shop(session, scanner=fake_shop_snapshot)

    assert refreshed.scene_state["cw"]["shop"] == {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 1},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }


def test_shop_scan_without_guide_keeps_opened_and_filters_guide_summary(tmp_path):
    shop_module = load_cw_shop_module()
    open_cw_shop = getattr(shop_module, "open_cw_shop", None)
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    assert open_cw_shop is not None
    assert scan_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = None
    session.scene_state["cw"]["constraints"] = {
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 7,
        "priority": {"银狼": 99},
        "positioning": {"银狼": "on_field"},
    }

    open_cw_shop(session, opener=lambda: None)
    refreshed = scan_cw_shop(session, scanner=fake_shop_snapshot)

    assert refreshed.scene_state["cw"]["shop"] == {
        "opened": True,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }


def test_shop_status_returns_current_snapshot(tmp_path):
    shop_module = load_cw_shop_module()
    shop_cw_status = getattr(shop_module, "shop_cw_status", None)
    assert shop_cw_status is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    scanned = getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)

    assert shop_cw_status(scanned) == scanned.scene_state["cw"]["shop"]


def test_shop_buy_slot_mutates_remaining_purchases(tmp_path):
    shop_module = load_cw_shop_module()
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)
    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    assert refreshed.scene_state["cw"]["guide"]["remaining_purchases"]["银狼"] == 0
    assert refreshed.scene_state["cw"]["shop"] == {
        "items": [{"name": "阮·梅", "price": 30}],
        "coins": 22,
        "level": 8,
        "reserve_full": False,
        "max_team_size": 8,
        "opened": True,
        "guide_summary": {
            "remaining_purchases": {"银狼": 0},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True


def test_shop_buy_slot_without_guide_does_not_crash_or_create_guide(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = None
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)

    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    assert refreshed.scene_state["cw"]["guide"] is None
    assert refreshed.scene_state["cw"]["shop"]["opened"] is True
    assert refreshed.scene_state["cw"]["shop"]["items"] == [{"name": "阮·梅", "price": 30}]
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True


@pytest.mark.parametrize(
    ("slot", "expect", "scanner", "code"),
    [
        (1, "希儿", fake_shop_snapshot_after_purchase, "SHOP_SLOT_MISMATCH"),
        (1, "银狼", fake_shop_snapshot, "SHOP_BUY_NOT_CONFIRMED"),
    ],
)
def test_shop_buy_slot_rejects_invalid_purchase_without_consuming_purchase(tmp_path, slot, expect, scanner, code):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)

    with pytest.raises(TrailError) as exc_info:
        buy_cw_shop_slot(
            session,
            slot=slot,
            expect=expect,
            buyer=fake_buy_success,
            scanner=scanner,
        )

    assert exc_info.value.code == code
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 1}
    assert session.scene_state["cw"]["shop"]["items"] == [{"name": "银狼", "price": 20}]


def test_shop_buy_slot_rejects_other_slot_change_without_target_change(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    before_shop = deepcopy(session.scene_state["cw"]["shop"])

    with pytest.raises(TrailError) as exc_info:
        buy_cw_shop_slot(
            session,
            slot=1,
            expect="银狼",
            buyer=fake_buy_success,
            scanner=fake_shop_snapshot_target_unchanged_other_changed,
        )

    assert exc_info.value.code == "SHOP_BUY_NOT_CONFIRMED"
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 1}
    assert session.scene_state["cw"]["shop"] == before_shop


def test_shop_buy_slot_noop_failure_keeps_shop_snapshot_unchanged(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    before_shop = deepcopy(session.scene_state["cw"]["shop"])

    with pytest.raises(TrailError) as exc_info:
        buy_cw_shop_slot(
            session,
            slot=1,
            expect="银狼",
            buyer=fake_buy_success,
            scanner=fake_shop_snapshot,
        )

    assert exc_info.value.code == "SHOP_BUY_NOT_CONFIRMED"
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 1}
    assert session.scene_state["cw"]["shop"] == before_shop


def test_shop_refresh_invalidates_snapshot(tmp_path):
    shop_module = load_cw_shop_module()
    refresh_cw_shop = getattr(shop_module, "refresh_cw_shop", None)
    assert refresh_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    scanned = getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)
    refreshed_calls: list[str] = []
    refreshed = refresh_cw_shop(scanned, refresher=lambda: refreshed_calls.append("refresh"))

    assert refreshed.scene_state["cw"]["shop"]["stale"] is True
    assert refreshed_calls == ["refresh"]


def test_shop_close_marks_shop_closed_and_stale(tmp_path):
    shop_module = load_cw_shop_module()
    close_cw_shop = getattr(shop_module, "close_cw_shop", None)
    assert close_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    closed: list[str] = []
    refreshed = close_cw_shop(session, closer=lambda: closed.append("close"))

    assert refreshed.scene_state["cw"]["shop"] == {
        "stale": True,
        "opened": False,
    }
    assert closed == ["close"]


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


def _set_shop(session, shop: dict):
    session.scene_state.setdefault("cw", {})["shop"] = deepcopy(shop)
    return session


@pytest.mark.parametrize(
    ("method", "request_id", "payload", "setup_patches", "expected_key", "expected_value"),
    [
        (
            "cw.shop.open",
            "req-shop-open-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_opener_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.open_cw_shop", lambda session, opener: _set_shop(session, {"opened": True, "stale": True})),
            ),
            "opened",
            True,
        ),
        (
            "cw.shop.buy_slot",
            "req-shop-buy-1",
            {"slot": 2, "expect": "希儿"},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_buyer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.buy_cw_shop_slot",
                    lambda session, slot, expect, buyer, scanner: _set_shop(session, {"slot": slot, "expect": expect, "opened": True, "stale": False}),
                ),
            ),
            "expect",
            "希儿",
        ),
        (
            "cw.shop.refresh",
            "req-shop-refresh-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_refresher_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.refresh_cw_shop", lambda session, refresher: _set_shop(session, {"refreshed": True, "opened": True, "stale": False})),
            ),
            "refreshed",
            True,
        ),
        (
            "cw.shop.close",
            "req-shop-close-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_closer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.close_cw_shop", lambda session, closer: _set_shop(session, {"opened": False, "stale": True})),
            ),
            "opened",
            False,
        ),
    ],
)
def test_cw_shop_mutating_commands_flow_through_command_service_journal(
    tmp_path: Path,
    monkeypatch,
    method: str,
    request_id: str,
    payload: dict,
    setup_patches,
    expected_key: str,
    expected_value,
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    setup_patches(monkeypatch)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=request_id,
        method=method,
        payload=payload,
    )
    status = service.request_status(request_id)

    assert envelope["ok"] is True
    assert envelope["data"][expected_key] == expected_value
    assert status["final_state"] == "completed"
    assert service.load_session(session.session_id).scene_state["cw"]["shop"][expected_key] == expected_value


def test_cw_shop_read_commands_persist_shop_snapshot(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    service.save_session(loaded)
    monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: fake_shop_snapshot)

    scanned = cw_service.handle(
        method="cw.shop.scan",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )
    status = cw_service.handle(
        method="cw.shop.status",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert scanned["items"] == [{"name": "银狼", "price": 20}]
    assert status["items"] == [{"name": "银狼", "price": 20}]
    assert service.load_session(session.session_id).scene_state["cw"]["shop"]["coins"] == 40
