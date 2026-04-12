from __future__ import annotations

import importlib
import json

import pytest

from trail.cli import app
from trail.session.store import SessionStore


def fake_shop_snapshot():
    return ([{"name": "银狼", "price": 20}], 40, 7, False, 8)


def fake_shop_snapshot_after_purchase():
    return ([{"name": "阮·梅", "price": 30}], 22, 8, False, 8)


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
    refreshed = buy_cw_shop_slot(
        session,
        slot=3,
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
        "guide_summary": {
            "remaining_purchases": {"银狼": 0},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True


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


def test_cw_shop_open_cli_persists_snapshot(cli_runner, fake_runtime, fake_session, tmp_path):
    result = cli_runner.invoke(app, ["cw", "shop", "open", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["stale"] is True
    assert payload["data"]["opened"] is True
    assert fake_runtime.clicks == [(0.8438, 0.8481)]

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["shop"] == payload["data"]


def test_cw_shop_scan_cli_uses_runtime_scanner(cli_runner, fake_runtime, fake_session, tmp_path):
    calls: list[dict] = []

    def fake_ocr(**kwargs):
        calls.append(kwargs)
        if kwargs == {"from_x": 0.19, "from_y": 0.26, "to_x": 0.88, "to_y": 0.31}:
            return [(None, "银狼"), (None, "20"), (None, "希儿"), (None, "30")]
        if kwargs == {"from_x": 0.84, "from_y": 0.81, "to_x": 0.89, "to_y": 0.89}:
            return [(None, "40")]
        if kwargs == {"from_x": 0.05, "from_y": 0.815, "to_x": 0.3, "to_y": 0.87}:
            return [(None, "等级.7")]
        if kwargs == {"from_x": 0.505, "from_y": 0.18, "to_x": 0.608, "to_y": 0.27}:
            return [(None, "8")]
        return []

    fake_runtime.ocr = fake_ocr

    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    session.scene_state["cw"] = {
        "guide": {"remaining_purchases": {"银狼": 2}},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        "slots": {"stale": False, "hand": ["银狼"]},
        "shop": {"stale": True, "opened": True},
        "stage": {"stale": True},
        "metrics": {},
    }
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "shop", "scan", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "items": [{"name": "银狼", "price": 20}, {"name": "希儿", "price": 30}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    assert calls == [
        {"from_x": 0.19, "from_y": 0.26, "to_x": 0.88, "to_y": 0.31},
        {"from_x": 0.84, "from_y": 0.81, "to_x": 0.89, "to_y": 0.89},
        {"from_x": 0.05, "from_y": 0.815, "to_x": 0.3, "to_y": 0.87},
        {"from_x": 0.505, "from_y": 0.18, "to_x": 0.608, "to_y": 0.27},
    ]


def test_cw_shop_scan_cli_refreshes_snapshot(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd

    monkeypatch.setattr(cw_cmd, "shop_scanner_factory", lambda runtime: fake_shop_snapshot, raising=False)

    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    session.scene_state["cw"] = {
        "guide": {"remaining_purchases": {"银狼": 2}},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        "slots": {"stale": False, "hand": ["银狼"]},
        "shop": {"stale": True, "opened": True},
        "stage": {"stale": True},
        "metrics": {},
    }
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "shop", "scan", "--session", fake_session])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }

    session = store.load(fake_session)
    assert session.scene_state["cw"]["shop"] == payload["data"]


def test_cw_shop_buy_slot_cli_mutates_remaining_purchases(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd

    from tests.conftest import fake_buy_success

    monkeypatch.setattr(cw_cmd, "shop_buyer_factory", lambda runtime: fake_buy_success, raising=False)
    monkeypatch.setattr(cw_cmd, "shop_scanner_factory", lambda runtime: fake_shop_snapshot_after_purchase, raising=False)

    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    session.scene_state["cw"] = {
        "guide": {"remaining_purchases": {"银狼": 1}},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        "slots": {"stale": False, "hand": ["银狼"]},
        "shop": {"stale": False, "opened": True},
        "stage": {"stale": True},
        "metrics": {},
    }
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "shop", "buy-slot", "--session", fake_session, "--slot", "3", "--expect", "银狼"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "items": [{"name": "阮·梅", "price": 30}],
        "coins": 22,
        "level": 8,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 0},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }

    session = store.load(fake_session)
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 0}
    assert session.scene_state["cw"]["shop"] == payload["data"]
    assert session.scene_state["cw"]["slots"]["stale"] is True


def test_cw_shop_buy_slot_cli_clicks_requested_slot(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    session.scene_state["cw"] = {
        "guide": {"remaining_purchases": {"银狼": 1}},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        "slots": {"stale": False, "hand": ["银狼"]},
        "shop": {"stale": False, "opened": True},
        "stage": {"stale": True},
        "metrics": {},
    }
    store.save(session)

    result = cli_runner.invoke(app, ["cw", "shop", "buy-slot", "--session", fake_session, "--slot", "3", "--expect", "银狼"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert fake_runtime.clicks == [(0.55, 0.18)]

    session = store.load(fake_session)
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 0}
    assert session.scene_state["cw"]["shop"] == payload["data"]


def test_cw_shop_refresh_close_and_status_cli_follow_state_contract(cli_runner, fake_runtime, fake_session, tmp_path):
    store = SessionStore(tmp_path / ".trail" / "sessions")
    session = store.load(fake_session)
    session.scene_state["cw"] = {
        "guide": {"remaining_purchases": {"银狼": 1}},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        "slots": {"stale": False, "hand": ["银狼"]},
        "shop": {"stale": False, "opened": True, "items": [{"name": "银狼", "price": 20}]},
        "stage": {"stale": True},
        "metrics": {},
    }
    store.save(session)

    refresh_result = cli_runner.invoke(app, ["cw", "shop", "refresh", "--session", fake_session])
    assert refresh_result.exit_code == 0
    refresh_payload = json.loads(refresh_result.stdout)
    assert refresh_payload["ok"] is True
    assert refresh_payload["data"] == {"stale": True, "opened": True, "items": [{"name": "银狼", "price": 20}]}
    assert fake_runtime.keys == [("d", 1, 0.2)]

    close_result = cli_runner.invoke(app, ["cw", "shop", "close", "--session", fake_session])
    assert close_result.exit_code == 0
    close_payload = json.loads(close_result.stdout)
    assert close_payload["ok"] is True
    assert close_payload["data"] == {"stale": True, "opened": False, "items": [{"name": "银狼", "price": 20}]}
    assert fake_runtime.clicks == [(0.5, 0.55)]

    status_result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", fake_session])
    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["ok"] is True
    assert status_payload["data"] == {"stale": True, "opened": False, "items": [{"name": "银狼", "price": 20}]}
