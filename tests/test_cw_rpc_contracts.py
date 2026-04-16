from __future__ import annotations

import json

import pytest

from trail.cli import app
from tests.support.fake_daemon import build_success_response


SESSION_ID = "a" * 32


@pytest.fixture(autouse=True)
def block_local_cw_execution(monkeypatch):
    def fail_local_cw(*args, **kwargs):
        raise AssertionError("local cw execution path used")

    monkeypatch.setattr("trail.commands.cw._runtime_for_session", fail_local_cw, raising=False)
    monkeypatch.setattr("trail.commands.cw._run", fail_local_cw, raising=False)
    monkeypatch.setattr("trail.commands.cw.session_store_factory", lambda: fail_local_cw(), raising=False)
    monkeypatch.setattr("trail.commands.cw.with_auto_capture", fail_local_cw, raising=False)


def _assert_single_call(client, *, method: str, payload: dict, tmp_path) -> None:
    assert client.calls == [
        {
            "method": method,
            "payload": {"session_id": SESSION_ID, **payload},
            "workspace_root": str(tmp_path),
            "session_id": SESSION_ID,
            "verbose": False,
        }
    ]


def test_cw_stage_detect_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.stage.detect": build_success_response(
                request_id="req-cw-stage-detect",
                data={"value": "preparation", "stale": False},
                screenshot=".trail/shots/req-cw-stage-detect.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "stage", "detect", "--session", SESSION_ID])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"value": "preparation", "stale": False}
    _assert_single_call(client, method="cw.stage.detect", payload={}, tmp_path=tmp_path)


def test_cw_enter_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.enter": build_success_response(
                request_id="req-cw-enter",
                data={"mode": "new", "difficulty": "current", "battle_mode": "standard"},
                screenshot=".trail/shots/req-cw-enter.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "cw",
            "enter",
            "--session",
            SESSION_ID,
            "--mode",
            "new",
            "--difficulty",
            "current",
            "--battle-mode",
            "standard",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["mode"] == "new"
    _assert_single_call(
        client,
        method="cw.enter",
        payload={"mode": "new", "difficulty": "current", "battle_mode": "standard"},
        tmp_path=tmp_path,
    )


def test_cw_shop_buy_slot_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.buy_slot": build_success_response(
                request_id="req-cw-shop-buy-slot",
                data={"slot": 2, "expect": "希儿"},
                screenshot=".trail/shots/req-cw-shop-buy-slot.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "shop", "buy-slot", "--session", SESSION_ID, "--slot", "2", "--expect", "希儿"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"slot": 2, "expect": "希儿"}
    _assert_single_call(client, method="cw.shop.buy_slot", payload={"slot": 2, "expect": "希儿"}, tmp_path=tmp_path)


def test_cw_guide_current_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.guide.current": build_success_response(
                request_id="req-cw-guide-current",
                data={"lineup_id": "abc", "artifact_id": "art-1"},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "guide", "current", "--session", SESSION_ID])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"lineup_id": "abc", "artifact_id": "art-1"}
    _assert_single_call(client, method="cw.guide.current", payload={}, tmp_path=tmp_path)


@pytest.mark.parametrize("guide_flag", ["--lineup-id", "--guide"])
def test_cw_guide_apply_rpc_contract(cli_runner, fake_daemon_client, tmp_path, guide_flag: str):
    client = fake_daemon_client(
        {
            "cw.guide.apply": build_success_response(
                request_id=f"req-cw-guide-apply-{guide_flag.lstrip('-')}",
                data={"lineup_id": "abc", "artifact_id": "art-1"},
                screenshot=".trail/shots/req-cw-guide-apply.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", SESSION_ID, guide_flag, "abc"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"lineup_id": "abc", "artifact_id": "art-1"}
    _assert_single_call(client, method="cw.guide.apply", payload={"lineup_id": "abc"}, tmp_path=tmp_path)


def test_cw_slots_place_one_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.slots.place_one": build_success_response(
                request_id="req-cw-slots-place-one",
                data={"front": ["希儿"], "back": [], "hand": [], "stale": False},
                screenshot=".trail/shots/req-cw-slots-place-one.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "slots", "place-one", "--session", SESSION_ID, "--source", "hand:0", "--target", "front:0"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["front"] == ["希儿"]
    _assert_single_call(
        client,
        method="cw.slots.place_one",
        payload={"source": "hand:0", "target": "front:0"},
        tmp_path=tmp_path,
    )


def test_cw_invest_choose_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.invest.choose": build_success_response(
                request_id="req-cw-invest-choose",
                data={"value": "battle", "stale": False},
                screenshot=".trail/shots/req-cw-invest-choose.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "invest", "choose", "--session", SESSION_ID, "--option", "2"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"value": "battle", "stale": False}
    _assert_single_call(client, method="cw.invest.choose", payload={"option": 2}, tmp_path=tmp_path)


def test_cw_battle_continue_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.continue": build_success_response(
                request_id="req-cw-battle-continue",
                data={"value": "settle", "stale": False},
                screenshot=".trail/shots/req-cw-battle-continue.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "continue", "--session", SESSION_ID])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"value": "settle", "stale": False}
    _assert_single_call(client, method="cw.battle.continue", payload={}, tmp_path=tmp_path)


def test_cw_stage_wait_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.stage.wait": build_success_response(
                request_id="req-cw-stage-wait",
                data={"value": "settle", "stale": False},
                screenshot=".trail/shots/req-cw-stage-wait.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "stage", "wait", "--session", SESSION_ID, "--timeout", "120"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"value": "settle", "stale": False}
    _assert_single_call(client, method="cw.stage.wait", payload={"timeout": 120}, tmp_path=tmp_path)


def test_cw_shop_status_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.status": build_success_response(
                request_id="req-cw-shop-status",
                data={"items": [{"slot": 1, "name": "希儿"}]},
                screenshot=".trail/shots/req-cw-shop-status.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", SESSION_ID])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"items": [{"slot": 1, "name": "希儿"}]}
    _assert_single_call(client, method="cw.shop.status", payload={}, tmp_path=tmp_path)


def test_cw_event_handle_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.event.handle": build_success_response(
                request_id="req-cw-event-handle",
                data={"value": "settle", "stale": False},
                screenshot=".trail/shots/req-cw-event-handle.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "event", "handle", "--session", SESSION_ID])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"value": "settle", "stale": False}
    _assert_single_call(client, method="cw.event.handle", payload={}, tmp_path=tmp_path)


def test_cw_invest_read_rpc_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.invest.read": build_success_response(
                request_id="req-cw-invest-read",
                data={"options": [{"id": 1, "name": "量子力学"}]},
                screenshot=".trail/shots/req-cw-invest-read.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "invest", "read", "--session", SESSION_ID])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"] == {"options": [{"id": 1, "name": "量子力学"}]}
    _assert_single_call(client, method="cw.invest.read", payload={}, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("args", "method", "payload"),
    [
        (["cw", "slots", "read", "--session", SESSION_ID, "--slot", "front:0", "--slot", "back:1"], "cw.slots.read", {"slot": ["front:0", "back:1"]}),
        (["cw", "slots", "swap", "--session", SESSION_ID, "--source", "hand:0", "--target", "front:0"], "cw.slots.swap", {"source": "hand:0", "target": "front:0"}),
        (["cw", "replenish", "read", "--session", SESSION_ID], "cw.replenish.read", {}),
        (["cw", "replenish", "choose", "--session", SESSION_ID, "--option", "2"], "cw.replenish.choose", {"option": 2}),
        (["cw", "crystals", "collect", "--session", SESSION_ID], "cw.crystals.collect", {}),
        (["cw", "hand", "sell-one", "--session", SESSION_ID, "--slot", "1"], "cw.hand.sell_one", {"slot": 1}),
        (["cw", "hand", "sell-plan", "--session", SESSION_ID], "cw.hand.sell_plan", {}),
        (["cw", "shop", "open", "--session", SESSION_ID], "cw.shop.open", {}),
        (["cw", "shop", "scan", "--session", SESSION_ID], "cw.shop.scan", {}),
        (["cw", "shop", "refresh", "--session", SESSION_ID], "cw.shop.refresh", {}),
        (["cw", "shop", "close", "--session", SESSION_ID], "cw.shop.close", {}),
        (["cw", "encounter", "read", "--session", SESSION_ID], "cw.encounter.read", {}),
        (["cw", "encounter", "choose", "--session", SESSION_ID, "--option", "1"], "cw.encounter.choose", {"option": 1}),
        (["cw", "fortune", "read", "--session", SESSION_ID], "cw.fortune.read", {}),
        (["cw", "fortune", "choose", "--session", SESSION_ID, "--option", "1"], "cw.fortune.choose", {"option": 1}),
        (["cw", "boss-preview", "confirm", "--session", SESSION_ID], "cw.boss_preview.confirm", {}),
        (["cw", "battle", "start", "--session", SESSION_ID], "cw.battle.start", {}),
        (["cw", "settle", "next", "--session", SESSION_ID], "cw.settle.next", {}),
    ],
)
def test_cw_rpc_wrapper_matrix(cli_runner, fake_daemon_client, tmp_path, args, method: str, payload: dict):
    client = fake_daemon_client(
        {
            method: build_success_response(
                request_id=f"req-{method}",
                data={"ok": True},
            )
        }
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    response_payload = json.loads(result.stdout)
    assert response_payload["ok"] is True
    _assert_single_call(client, method=method, payload=payload, tmp_path=tmp_path)
