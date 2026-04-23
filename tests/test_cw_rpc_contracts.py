from __future__ import annotations

import pytest

from trail.cli import app
from tests.support.fake_daemon import build_success_response as _build_success_response


SESSION_ID = "a" * 32


def build_success_response(*, request_id: str, data: dict, screenshot: str | None = None) -> dict:
    response = _build_success_response(request_id=request_id, data=data, screenshot=screenshot)
    if screenshot is not None:
        response["image_guidance"] = {"read_image_first": True}
    return response
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


def _expected_lines(summary: str, *, screenshot: str | None = None, body: list[str] | None = None) -> list[str]:
    lines = [summary]
    if screenshot:
        lines.append(f"shot path={screenshot}")
        lines.append("info read_image_first=1")
    if body:
        lines.extend(body)
    return lines
def test_cw_stage_detect_renders_stage_and_shot(cli_runner, fake_daemon_client, tmp_path):
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
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.stage.detect stage=preparation stale=0",
        screenshot=".trail/shots/req-cw-stage-detect.png",
    )
    _assert_single_call(client, method="cw.stage.detect", payload={}, tmp_path=tmp_path)


def test_cw_enter_renders_home_summary_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.enter": build_success_response(
                request_id="req-cw-enter",
                data={"page": "home"},
                screenshot=".trail/shots/req-cw-enter.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "enter", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.enter page=home",
        screenshot=".trail/shots/req-cw-enter.png",
    )
    _assert_single_call(client, method="cw.enter", payload={}, tmp_path=tmp_path)


def test_cw_enter_renders_already_home_info(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.enter": build_success_response(
                request_id="req-cw-enter-already-home",
                data={"page": "home", "already_home": True},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "enter", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.enter page=home", "info already_home=1"]
    _assert_single_call(client, method="cw.enter", payload={}, tmp_path=tmp_path)


def test_cw_enter_rejects_legacy_start_options(cli_runner):
    result = cli_runner.invoke(app, ["cw", "enter", "--session", SESSION_ID, "--mode", "new"])

    assert result.exit_code == 2
    assert "No such option" in result.output
    assert "--mode" in result.output


def test_cw_start_renders_portal_cards_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.start": build_success_response(
                request_id="req-cw-start",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "new": 1,
                            "guides": [
                                {
                                    "lineup_id": "alpha-guide",
                                    "title": "Alpha攻略",
                                    "version": "3.2",
                                    "labels": [],
                                    "carry_roles": ["希儿"],
                                    "support_hard": True,
                                    "has_change_equip": False,
                                    "has_expert": True,
                                    "like": 123,
                                    "favour": 45,
                                    "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
                                }
                            ],
                        },
                        {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
                    ],
                    "mode": "continue",
                    "difficulty": "current",
                    "battle_mode": "standard",
                    "stale": False,
                },
                screenshot=".trail/shots/req-cw-start.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "cw",
            "start",
            "--session",
            SESSION_ID,
            "--mode",
            "continue",
            "--difficulty",
            "current",
            "--battle-mode",
            "standard",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.start cards=2",
        screenshot=".trail/shots/req-cw-start.png",
        body=[
            'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
            'opt idx=1 说明="Alpha Desc"',
            'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 版本=3.2 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
            'guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3',
            'opt idx=2 投资环境="Beta Portal" score=0.88 待收集=0',
            'opt idx=2 说明="Beta Desc"',
        ],
    )
    _assert_single_call(
        client,
        method="cw.start",
        payload={"mode": "continue", "difficulty": "current", "battle_mode": "standard"},
        tmp_path=tmp_path,
    )


def test_cw_start_forwards_exact_rank_payload_without_rendering_exact_rank_or_numeric_mapping(
    cli_runner,
    fake_daemon_client,
    tmp_path,
):
    client = fake_daemon_client(
        {
            "cw.start": build_success_response(
                request_id="req-cw-start-exact-rank",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "guides": [],
                        }
                    ],
                    "mode": "continue",
                    "difficulty": "A7-3",
                    "requested_difficulty": "A7-3",
                    "target_enemy_difficulty": 51,
                    "current_enemy_difficulty": 54,
                    "page": "invest",
                    "reason": "exact_rank_requested",
                    "battle_mode": "standard",
                    "stale": False,
                },
                screenshot=".trail/shots/req-cw-start-exact-rank.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "cw",
            "start",
            "--session",
            SESSION_ID,
            "--mode",
            "continue",
            "--difficulty",
            "A7-3",
            "--battle-mode",
            "standard",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.start cards=1",
        screenshot=".trail/shots/req-cw-start-exact-rank.png",
        body=[
            'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=0',
            'opt idx=1 说明="Alpha Desc"',
        ],
    )
    assert "A7-3" not in result.stdout
    assert "51" not in result.stdout
    assert "requested_difficulty=" not in result.stdout
    assert "target_enemy_difficulty=" not in result.stdout
    _assert_single_call(
        client,
        method="cw.start",
        payload={"mode": "continue", "difficulty": "A7-3", "battle_mode": "standard"},
        tmp_path=tmp_path,
    )


def test_cw_portal_select_renders_selected_card_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.portal.select": build_success_response(
                request_id="req-cw-portal-select",
                data={"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
                screenshot=".trail/shots/req-cw-portal-select.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "portal", "select", "--session", SESSION_ID, "--card-idx", "2"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        'ok cw.portal.select idx=2 投资环境="Beta Portal"',
        screenshot=".trail/shots/req-cw-portal-select.png",
    )
    _assert_single_call(client, method="cw.portal.select", payload={"card_idx": 2}, tmp_path=tmp_path)


def test_cw_portal_detect_renders_portal_cards_family(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.portal.detect": build_success_response(
                request_id="req-cw-portal-detect",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "guides": [
                                {
                                    "lineup_id": "alpha-guide",
                                    "title": "Alpha攻略",
                                    "carry_roles": ["希儿"],
                                    "support_hard": True,
                                    "has_change_equip": False,
                                    "has_expert": True,
                                    "like": 123,
                                    "favour": 45,
                                }
                            ],
                        }
                    ],
                    "mode": None,
                    "difficulty": None,
                    "battle_mode": None,
                    "stale": False,
                },
                screenshot=".trail/shots/req-cw-portal-detect.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "portal", "detect", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.portal.detect cards=1",
        screenshot=".trail/shots/req-cw-portal-detect.png",
        body=[
            'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=0',
            'opt idx=1 说明="Alpha Desc"',
            'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
        ],
    )
    _assert_single_call(client, method="cw.portal.detect", payload={}, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("args", "method", "payload", "response_data", "screenshot"),
    [
        (
            ["cw", "strategy", "detect", "--session", SESSION_ID],
            "cw.strategy.detect",
            {},
            {
                "cards": [
                    {
                        "card_idx": 1,
                        "strategy_title": "快攻",
                        "strategy_description": "desc",
                        "refresh_count": 1,
                        "guide_match": "优选",
                        "guide_loaded": 1,
                    }
                ],
                "stale": False,
            },
            ".trail/shots/req-cw-strategy-detect.png",
        ),
        (
            ["cw", "strategy", "select", "--session", SESSION_ID, "--card-idx", "2"],
            "cw.strategy.select",
            {"card_idx": 2},
            {
                "card_idx": 2,
                "strategy_title": "回蓝",
                "strategy_description": "desc",
                "refresh_count": 1,
                "guide_match": "次选",
                "guide_loaded": 1,
            },
            ".trail/shots/req-cw-strategy-select.png",
        ),
        (
            ["cw", "strategy", "refresh", "--session", SESSION_ID, "--card-idx", "3"],
            "cw.strategy.refresh",
            {"card_idx": 3},
            {
                "cards": [
                    {
                        "card_idx": 3,
                        "strategy_title": "暴击",
                        "strategy_description": "desc",
                        "refresh_count": 2,
                        "guide_match": "否",
                        "guide_loaded": 1,
                    }
                ],
                "stale": False,
            },
            ".trail/shots/req-cw-strategy-refresh.png",
        ),
    ],
)
def test_cw_strategy_rpc_contracts(cli_runner, fake_daemon_client, tmp_path, args, method: str, payload: dict, response_data: dict, screenshot: str):
    client = fake_daemon_client(
        {
            method: build_success_response(
                request_id=f"req-{method}",
                data=response_data,
                screenshot=screenshot,
            )
        }
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    _assert_single_call(client, method=method, payload=payload, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("args", "method", "screenshot"),
    [
        (["cw", "portal", "refresh", "--session", SESSION_ID], "cw.portal.refresh", ".trail/shots/req-cw-portal-refresh.png"),
        (["cw", "portal", "restart", "--session", SESSION_ID], "cw.portal.restart", ".trail/shots/req-cw-portal-restart.png"),
    ],
)
def test_cw_portal_refresh_and_restart_render_portal_cards(cli_runner, fake_daemon_client, tmp_path, args, method: str, screenshot: str):
    client = fake_daemon_client(
        {
            method: build_success_response(
                request_id=f"req-{method}",
                data={
                    "cards": [
                        {
                            "card_idx": 1,
                            "portal_title": "Alpha Portal",
                            "portal_description": "Alpha Desc",
                            "score": 0.99,
                            "new": 1,
                            "guides": [
                                {
                                    "lineup_id": "alpha-guide",
                                    "title": "Alpha攻略",
                                    "version": "3.2",
                                    "carry_roles": ["希儿"],
                                    "support_hard": True,
                                    "has_change_equip": False,
                                    "has_expert": True,
                                    "like": 123,
                                    "favour": 45,
                                    "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
                                }
                            ],
                        },
                        {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
                    ],
                    "mode": "continue",
                    "difficulty": "current",
                    "battle_mode": "standard",
                    "stale": False,
                },
                screenshot=screenshot,
            )
        }
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        f"ok {method} cards=2",
        screenshot=screenshot,
        body=[
            'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
            'opt idx=1 说明="Alpha Desc"',
            'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 版本=3.2 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
            'guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3',
            'opt idx=2 投资环境="Beta Portal" score=0.88 待收集=0',
            'opt idx=2 说明="Beta Desc"',
        ],
    )
    _assert_single_call(client, method=method, payload={}, tmp_path=tmp_path)


def test_cw_shop_buy_slot_renders_purchase_summary_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.buy_slot": build_success_response(
                request_id="req-cw-shop-buy-slot",
                data={
                    "items": [
                        {"slot": 2, "name": "停云", "price": 10},
                        {"slot": 1, "name": "银狼", "price": 20},
                    ],
                    "opened": True,
                    "stale": False,
                    "guide_summary": {"remaining_purchases": {"银狼": 0}},
                },
                screenshot=".trail/shots/req-cw-shop-buy-slot.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "shop", "buy-slot", "--session", SESSION_ID, "--slot", "2", "--expect", "希儿"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.shop.buy_slot opened=1 stale=0 count=2",
        screenshot=".trail/shots/req-cw-shop-buy-slot.png",
        body=[
            "item idx=1 slot=1 name=银狼 cost=20",
            "item idx=2 slot=2 name=停云 cost=10",
        ],
    )
    _assert_single_call(client, method="cw.shop.buy_slot", payload={"slot": 2, "expect": "希儿"}, tmp_path=tmp_path)


def test_cw_shop_scan_renders_unknown_result_failure_contract(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.scan": {
                "request_id": "req-cw-shop-scan-unknown",
                "ok": False,
                "data": {},
                "screenshot": ".trail/shots/req-cw-shop-scan-unknown.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {
                    "request_id": "req-cw-shop-scan-unknown",
                    "last_known_stage": "side_effect_applied",
                    "detail": "flush failed",
                },
                "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
            }
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "scan", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-scan-unknown",
        "shot path=.trail/shots/req-cw-shop-scan-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-scan-unknown",
    ]
    _assert_single_call(client, method="cw.shop.scan", payload={}, tmp_path=tmp_path)


def test_cw_guide_current_renders_guide_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.guide.current": build_success_response(
                request_id="req-cw-guide-current",
                data={
                    "lineup_id": "abc",
                    "title": "7群攻2银河学者",
                    "share_code": "##demo##",
                    "version": "3.2",
                    "labels": ["7级搜牌"],
                    "support_hard": True,
                    "artifact_id": "art-1",
                },
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "guide", "current", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.guide.current 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2",
        "guide 攻略标签=#7级搜牌|#适用超频博弈",
        "info 攻略快照ID=art-1",
    ]
    _assert_single_call(client, method="cw.guide.current", payload={}, tmp_path=tmp_path)


@pytest.mark.parametrize("guide_flag", ["--lineup-id", "--guide"])
def test_cw_guide_apply_renders_guide_summary(cli_runner, fake_daemon_client, tmp_path, guide_flag: str):
    client = fake_daemon_client(
        {
            "cw.guide.apply": build_success_response(
                request_id=f"req-cw-guide-apply-{guide_flag.lstrip('-')}",
                data={
                    "lineup_id": "abc",
                    "title": "7群攻2银河学者",
                    "share_code": "##demo##",
                    "version": "3.2",
                    "labels": ["7级搜牌"],
                    "support_hard": True,
                    "artifact_id": "art-1",
                },
                screenshot=".trail/shots/req-cw-guide-apply.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", SESSION_ID, guide_flag, "abc"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.guide.apply 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2",
        screenshot=".trail/shots/req-cw-guide-apply.png",
        body=["guide 攻略标签=#7级搜牌|#适用超频博弈", "info 攻略快照ID=art-1"],
    )
    _assert_single_call(client, method="cw.guide.apply", payload={"lineup_id": "abc"}, tmp_path=tmp_path)


def test_cw_slots_place_renders_slot_counts_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.slots.place": build_success_response(
                request_id="req-cw-slots-place",
                data={"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": True},
                screenshot=".trail/shots/req-cw-slots-place.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        [
            "cw",
            "slots",
            "place",
            "--session",
            SESSION_ID,
            "--action",
            "hand:0,front:0",
            "--action",
            "hand:1,back:2",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.slots.place front=1 back=1 hand=0 stale=1",
        screenshot=".trail/shots/req-cw-slots-place.png",
    )
    _assert_single_call(
        client,
        method="cw.slots.place",
        payload={
            "actions": [
                {"source": "hand:0", "target": "front:0"},
                {"source": "hand:1", "target": "back:2"},
            ]
        },
        tmp_path=tmp_path,
    )


def test_cw_hand_sell_renders_slot_counts_and_shot(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.hand.sell": build_success_response(
                request_id="req-cw-hand-sell",
                data={"front": ["希儿"], "back": ["佩拉"], "hand": [None, "银狼", None], "stale": True},
                screenshot=".trail/shots/req-cw-hand-sell.png",
            )
        }
    )

    result = cli_runner.invoke(
        app,
        ["cw", "hand", "sell", "--session", SESSION_ID, "--slot", "0", "--slot", "2"],
    )

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.hand.sell front=1 back=1 hand=1 stale=1",
        screenshot=".trail/shots/req-cw-hand-sell.png",
    )
    _assert_single_call(client, method="cw.hand.sell", payload={"slots": [0, 2]}, tmp_path=tmp_path)


def test_cw_slots_place_one_is_removed(cli_runner):
    result = cli_runner.invoke(app, ["cw", "slots", "place-one", "--help"])

    assert result.exit_code == 2
    assert "No such command 'place-one'" in result.output


def test_cw_slots_place_requires_at_least_one_action(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})

    result = cli_runner.invoke(app, ["cw", "slots", "place", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.slots.place code=CW_OPTION_INVALID",
        'why msg="cw slots place requires at least one --action"',
    ]
    assert client.calls == []


def test_cw_slots_place_rejects_malformed_action_without_rpc(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})

    result = cli_runner.invoke(app, ["cw", "slots", "place", "--session", SESSION_ID, "--action", "hand:0-front:0"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.slots.place code=CW_OPTION_INVALID",
        'why msg="cw slots place action invalid: hand:0-front:0"',
    ]
    assert client.calls == []


def test_cw_hand_sell_one_is_removed(cli_runner):
    result = cli_runner.invoke(app, ["cw", "hand", "sell-one", "--help"])

    assert result.exit_code == 2
    assert "No such command 'sell-one'" in result.output


def test_cw_hand_sell_requires_at_least_one_slot(cli_runner, fake_daemon_client):
    client = fake_daemon_client({})

    result = cli_runner.invoke(app, ["cw", "hand", "sell", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail cw.hand.sell code=CW_OPTION_INVALID",
        'why msg="cw hand sell requires at least one --slot"',
    ]
    assert client.calls == []


def test_cw_invest_choose_renders_stage_summary(cli_runner, fake_daemon_client, tmp_path):
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
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.invest.choose stage=battle stale=0",
        screenshot=".trail/shots/req-cw-invest-choose.png",
    )
    _assert_single_call(client, method="cw.invest.choose", payload={"option": 2}, tmp_path=tmp_path)


def test_cw_battle_continue_renders_stage_summary(cli_runner, fake_daemon_client, tmp_path):
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
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.battle.continue stage=settle stale=0",
        screenshot=".trail/shots/req-cw-battle-continue.png",
    )
    _assert_single_call(client, method="cw.battle.continue", payload={}, tmp_path=tmp_path)


def test_cw_battle_run_forwards_timeout(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run",
                data={},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID])

    assert result.exit_code == 0
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 570}, tmp_path=tmp_path)


def test_cw_battle_run_renders_completed_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run-completed",
                data={
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
                },
                screenshot=".trail/shots/req-cw-battle-run-completed.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID, "--timeout", "570"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.battle.run status=completed result=win stage=shop stale=0 in_battle=0",
        "shot path=.trail/shots/req-cw-battle-run-completed.png",
        "info read_image_first=1",
        "info round=1-1 hp=82 coins=4 exp=2",
        "info settle_text=挑战成功",
    ]
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 570}, tmp_path=tmp_path)


def test_cw_battle_run_renders_timeout_summary(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.battle.run": build_success_response(
                request_id="req-cw-battle-run-timeout",
                data={"status": "in_progress", "stale": True, "in_battle": True, "timeout_seconds": 570},
                screenshot=".trail/shots/req-cw-battle-run-timeout.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "battle", "run", "--session", SESSION_ID, "--timeout", "570"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "ok cw.battle.run status=in_progress stale=1 in_battle=1",
        "shot path=.trail/shots/req-cw-battle-run-timeout.png",
        "info read_image_first=1",
        "info timeout_seconds=570",
    ]
    _assert_single_call(client, method="cw.battle.run", payload={"timeout": 570}, tmp_path=tmp_path)


def test_cw_stage_wait_renders_stage_and_shot(cli_runner, fake_daemon_client, tmp_path):
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
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.stage.wait stage=settle stale=0",
        screenshot=".trail/shots/req-cw-stage-wait.png",
    )
    _assert_single_call(client, method="cw.stage.wait", payload={"timeout": 120}, tmp_path=tmp_path)


def test_cw_shop_status_renders_items_in_slot_order(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.shop.status": build_success_response(
                request_id="req-cw-shop-status",
                data={
                    "items": [
                        {"slot": 2, "name": "停云", "price": 1},
                        {"slot": 1, "name": "希儿", "price": 2},
                    ]
                },
                screenshot=None,
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "shop", "status", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.shop.status count=2",
        body=[
            "item idx=1 slot=1 name=希儿 cost=2",
            "item idx=2 slot=2 name=停云 cost=1",
        ],
    )
    _assert_single_call(client, method="cw.shop.status", payload={}, tmp_path=tmp_path)


def test_cw_event_handle_renders_event_result(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.event.handle": build_success_response(
                request_id="req-cw-event-handle",
                data={"event_type": "special", "handled_action": "confirm"},
            )
        }
    )

    result = cli_runner.invoke(app, ["cw", "event", "handle", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.event.handle event_type=special handled_action=confirm"]
    _assert_single_call(client, method="cw.event.handle", payload={}, tmp_path=tmp_path)


def test_cw_invest_read_renders_options(cli_runner, fake_daemon_client, tmp_path):
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
    assert result.stdout.splitlines() == _expected_lines(
        "ok cw.invest.read count=1",
        screenshot=".trail/shots/req-cw-invest-read.png",
        body=["opt idx=1 id=1 name=量子力学"],
    )
    _assert_single_call(client, method="cw.invest.read", payload={}, tmp_path=tmp_path)


@pytest.mark.parametrize(
    ("args", "method", "payload", "response_data", "screenshot", "expected_lines"),
    [
        (
            ["cw", "slots", "read", "--session", SESSION_ID, "--slot", "front:0", "--slot", "back:1"],
            "cw.slots.read",
            {"slot": ["front:0", "back:1"]},
            {"front": ["希儿", None], "back": [None], "hand": ["停云", None], "stale": False},
            ".trail/shots/req-cw-slots-read.png",
            _expected_lines(
                "ok cw.slots.read front=1 back=0 hand=1 stale=0",
                screenshot=".trail/shots/req-cw-slots-read.png",
                body=[
                    "slot pos=front:0 name=希儿",
                    "slot pos=front:1 empty=1",
                    "slot pos=back:0 empty=1",
                    "slot pos=hand:0 name=停云",
                    "slot pos=hand:1 empty=1",
                ],
            ),
        ),
        (
            ["cw", "slots", "swap", "--session", SESSION_ID, "--source", "hand:0", "--target", "front:0"],
            "cw.slots.swap",
            {"source": "hand:0", "target": "front:0"},
            {"front": ["希儿"], "back": ["佩拉"], "hand": [], "stale": True},
            ".trail/shots/req-cw-slots-swap.png",
            _expected_lines(
                "ok cw.slots.swap front=1 back=1 hand=0 stale=1",
                screenshot=".trail/shots/req-cw-slots-swap.png",
            ),
        ),
        (
            ["cw", "replenish", "read", "--session", SESSION_ID],
            "cw.replenish.read",
            {},
            {"options": [1, 2, 3]},
            ".trail/shots/req-cw-replenish-read.png",
            _expected_lines(
                "ok cw.replenish.read count=3",
                screenshot=".trail/shots/req-cw-replenish-read.png",
                body=["opt idx=1 value=1", "opt idx=2 value=2", "opt idx=3 value=3"],
            ),
        ),
        (
            ["cw", "replenish", "choose", "--session", SESSION_ID, "--option", "2"],
            "cw.replenish.choose",
            {"option": 2},
            {"value": "shop", "stale": False},
            ".trail/shots/req-cw-replenish-choose.png",
            _expected_lines(
                "ok cw.replenish.choose stage=shop stale=0",
                screenshot=".trail/shots/req-cw-replenish-choose.png",
            ),
        ),
        (
            ["cw", "crystals", "collect", "--session", SESSION_ID],
            "cw.crystals.collect",
            {},
            {"last_crystal_collection": "done"},
            ".trail/shots/req-cw-crystals-collect.png",
            _expected_lines(
                "ok cw.crystals.collect status=done",
                screenshot=".trail/shots/req-cw-crystals-collect.png",
            ),
        ),
        (
            ["cw", "hand", "sell-plan", "--session", SESSION_ID],
            "cw.hand.sell_plan",
            {},
            {"candidates": [0, 2]},
            None,
            ["ok cw.hand.sell_plan count=2"],
        ),
        (
            ["cw", "shop", "open", "--session", SESSION_ID],
            "cw.shop.open",
            {},
            {
                "items": [{"slot": 2, "name": "停云", "price": 10}, {"slot": 1, "name": "银狼", "price": 20}],
                "opened": True,
                "stale": True,
            },
            ".trail/shots/req-cw-shop-open.png",
            _expected_lines(
                "ok cw.shop.open opened=1 stale=1 count=2",
                screenshot=".trail/shots/req-cw-shop-open.png",
                body=["item idx=1 slot=1 name=银狼 cost=20", "item idx=2 slot=2 name=停云 cost=10"],
            ),
        ),
        (
            ["cw", "shop", "scan", "--session", SESSION_ID],
            "cw.shop.scan",
            {},
            {"items": [{"slot": 1, "name": "银狼", "price": 20}], "opened": True, "stale": False},
            ".trail/shots/req-cw-shop-scan.png",
            _expected_lines(
                "ok cw.shop.scan opened=1 stale=0 count=1",
                screenshot=".trail/shots/req-cw-shop-scan.png",
                body=["item idx=1 slot=1 name=银狼 cost=20"],
            ),
        ),
        (
            ["cw", "shop", "refresh", "--session", SESSION_ID],
            "cw.shop.refresh",
            {},
            {"items": [{"slot": 1, "name": "阮·梅", "price": 30}], "opened": False, "stale": True},
            ".trail/shots/req-cw-shop-refresh.png",
            _expected_lines(
                "ok cw.shop.refresh opened=0 stale=1 count=1",
                screenshot=".trail/shots/req-cw-shop-refresh.png",
                body=["item idx=1 slot=1 name=阮·梅 cost=30"],
            ),
        ),
        (
            ["cw", "shop", "close", "--session", SESSION_ID],
            "cw.shop.close",
            {},
            {"items": [], "opened": False, "stale": True},
            ".trail/shots/req-cw-shop-close.png",
            _expected_lines(
                "ok cw.shop.close opened=0 stale=1 count=0",
                screenshot=".trail/shots/req-cw-shop-close.png",
            ),
        ),
        (
            ["cw", "encounter", "read", "--session", SESSION_ID],
            "cw.encounter.read",
            {},
            {"options": [1, 2]},
            ".trail/shots/req-cw-encounter-read.png",
            _expected_lines(
                "ok cw.encounter.read count=2",
                screenshot=".trail/shots/req-cw-encounter-read.png",
                body=["opt idx=1 value=1", "opt idx=2 value=2"],
            ),
        ),
        (
            ["cw", "encounter", "choose", "--session", SESSION_ID, "--option", "1"],
            "cw.encounter.choose",
            {"option": 1},
            {"value": "event", "stale": False},
            None,
            ["ok cw.encounter.choose stage=event stale=0"],
        ),
        (
            ["cw", "fortune", "read", "--session", SESSION_ID],
            "cw.fortune.read",
            {},
            {"options": [1, 2]},
            ".trail/shots/req-cw-fortune-read.png",
            _expected_lines(
                "ok cw.fortune.read count=2",
                screenshot=".trail/shots/req-cw-fortune-read.png",
                body=["opt idx=1 value=1", "opt idx=2 value=2"],
            ),
        ),
        (
            ["cw", "fortune", "choose", "--session", SESSION_ID, "--option", "1"],
            "cw.fortune.choose",
            {"option": 1},
            {"value": "battle", "stale": False},
            None,
            ["ok cw.fortune.choose stage=battle stale=0"],
        ),
        (
            ["cw", "boss-preview", "confirm", "--session", SESSION_ID],
            "cw.boss_preview.confirm",
            {},
            {"value": "battle", "stale": False},
            None,
            ["ok cw.boss_preview.confirm stage=battle stale=0"],
        ),
        (
            ["cw", "battle", "start", "--session", SESSION_ID],
            "cw.battle.start",
            {},
            {"value": "battle", "stale": False},
            ".trail/shots/req-cw-battle-start.png",
            _expected_lines(
                "ok cw.battle.start stage=battle stale=0",
                screenshot=".trail/shots/req-cw-battle-start.png",
            ),
        ),
        (
            ["cw", "settle", "next", "--session", SESSION_ID],
            "cw.settle.next",
            {},
            {"value": "shop", "stale": False},
            ".trail/shots/req-cw-settle-next.png",
            _expected_lines(
                "ok cw.settle.next stage=shop stale=0",
                screenshot=".trail/shots/req-cw-settle-next.png",
            ),
        ),
    ],
    ids=[
        "cw_slots_read",
        "cw_slots_swap",
        "cw_replenish_read",
        "cw_replenish_choose",
        "cw_crystals_collect",
        "cw_hand_sell_plan",
        "cw_shop_open",
        "cw_shop_scan",
        "cw_shop_refresh",
        "cw_shop_close",
        "cw_encounter_read",
        "cw_encounter_choose",
        "cw_fortune_read",
        "cw_fortune_choose",
        "cw_boss_preview_confirm",
        "cw_battle_start",
        "cw_settle_next",
    ],
)
def test_cw_rpc_wrapper_matrix(
    cli_runner,
    fake_daemon_client,
    tmp_path,
    args,
    method: str,
    payload: dict,
    response_data: dict,
    screenshot: str | None,
    expected_lines: list[str],
):
    client = fake_daemon_client(
        {
            method: build_success_response(
                request_id=f"req-{method}",
                data=response_data,
                screenshot=screenshot,
            )
        }
    )

    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert result.stdout.splitlines() == expected_lines
    _assert_single_call(client, method=method, payload=payload, tmp_path=tmp_path)
