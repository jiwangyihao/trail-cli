from __future__ import annotations

import importlib
import json
import math
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.artifacts.store import ArtifactStore
from trail.core.errors import TrailError
from trail.runtime.model import Box
from trail.session.store import SessionStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CW_ASSET_ROOT = (PROJECT_ROOT / "trail" / "scenes" / "cw" / "assets").resolve()


def fake_fetcher(url: str):
    return {"share_code": "##demo##", "on_field": {"希儿": 9}, "off_field": {"佩拉": 3}}


def fake_lineup_url(lineup_id: str = "70472857") -> str:
    return f"https://act.miyoushe.com/sr/event/e20241220rpg-3Tii9M/index.html#/lineup/{lineup_id}"


def fake_guide() -> dict:
    return {
        "artifact_id": "guide-demo",
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "guide-demo",
        "title": "测试攻略",
        "share_code": "##demo##",
        "version": "4.0",
        "operation_guide": "前期按测试运营",
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
        "role_stages": [
            {
                "stage": "Opening",
                "front_roles": [{"name": "希儿"}],
                "back_roles": [{"name": "佩拉"}],
                "traits": ["2量子"],
            }
        ],
        "first_fight_augments": [{"name": "快攻"}],
        "second_fight_augments": [{"name": "回蓝"}],
        "order_basic": [{"name": "钻头"}],
        "order_compose": [{"name": "风暴"}],
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 9,
    }


class FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def fake_lineup_detail_response(share_code: str = "REAL-CODE", lineup_id: str = "70472857") -> dict:
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "lineup": {
                "id": lineup_id,
                "title": "7群攻2银河学者",
                "description": "前期：过渡\n中期：D牌\n后期：补强",
                "nickname": "测试作者",
                "has_change_equip": True,
                "has_expert": True,
                "certification": 3,
                "weight": 999,
                "mongo_json": {"debug": True},
                "forbid_edit": False,
                "draft_info": {"state": "hidden"},
                "uid": "123456",
                "account_uid": "654321",
                "tourn_detail": {
                    "share_code": share_code,
                    "labels": [{"text": "9级搜牌"}, {"text": "银河学者"}],
                    "support_hard": True,
                    "rpg_game_big_version": "3.1",
                    "first_fight_augments": [{"name": "银河大乐透"}, {"name": "超距遥感"}],
                    "second_fight_augments": [{"name": "折射棱镜"}],
                    "portals": [{"title": "购物区"}, {"title": "事件区"}],
                    "order_basic": [{"name": "抢前排输出"}, {"name": "补减防"}],
                    "order_compose": [{"name": "推进器"}],
                    "role_stages": [
                        {
                            "stage": "Opening",
                            "front_roles": [{"name": "黑塔", "star": 1, "rarity": 1, "is_carry": False}],
                            "back_roles": [{"name": "艾丝妲", "star": 1, "rarity": 1, "is_carry": False}],
                            "traits": [{"trait_name": "智识", "current_role_count": 1}],
                        },
                        {
                            "stage": "Final",
                            "front_roles": [
                                {
                                    "name": "希儿",
                                    "star": 3,
                                    "rarity": 3,
                                    "is_carry": True,
                                    "first_equipments": [{"name": "高周波电锯"}, {"name": "战场进化手册"}],
                                    "second_equipments": [{"name": "胜利之旗"}],
                                }
                            ],
                            "back_roles": [{"name": "佩拉", "star": 2, "rarity": 2, "is_carry": False}],
                            "traits": [
                                {"trait_name": "巡猎", "current_role_count": 1},
                                {"trait_name": "量子", "current_role_count": 2},
                            ],
                        },
                    ],
                },
            }
        },
    }


def fake_cw_config_response() -> dict:
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "season_id": 12,
            "sub_season_id": 3,
            "rpg_game_big_version": "3.2",
            "rpg_game_lineup_tourn_filter": "lineup-filter-v2",
            "label_list": [
                {"id": 7, "text": "7级搜牌"},
                {"id": 9, "text": "9级搜牌"},
            ],
            "trait_info_list": [
                {"trait_id": 2001, "trait_name": "巡猎", "trait_type": "path"},
                {"trait_id": 2002, "trait_name": "量子", "trait_type": "element"},
            ],
            "role_list": [
                {
                    "id": 1001,
                    "name": "希儿",
                    "front_back_type": "front",
                    "trait_details": [{"id": 2001}, {"id": 2002}],
                    "role_tags": ["输出", "量子"],
                },
                {
                    "id": 1002,
                    "name": "佩拉",
                    "front_back_type": "back",
                    "trait_details": [{"id": 2002}],
                    "role_tags": ["辅助", "减防"],
                },
                {
                    "id": 1003,
                    "name": "布洛妮娅",
                    "front_back_type": "back",
                    "trait_details": [{"id": 2001}],
                    "role_tags": ["辅助"],
                },
            ],
            "role_tag_list": [
                {"id": 1, "name": "输出"},
                {"id": 2, "name": "辅助"},
                {"id": 3, "name": "减防"},
                {"id": 4, "name": "量子"},
            ],
            "portal_list": [
                {
                    "id": "shop",
                    "name": "购物区",
                    "description": "花金币买角色和升级",
                    "icon": "shop.png",
                },
                {
                    "id": "event",
                    "name": "事件区",
                    "description": "处理事件、补给与遭遇",
                    "icon": "event.png",
                },
            ],
        },
    }


def trait_lookup_config_response() -> dict:
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": 2001, "trait_name": "巡猎", "trait_type": "path"},
        {"trait_id": 2002, "trait_name": "量子", "trait_type": "element"},
        {"trait_id": 2003, "trait_name": "智识", "trait_type": "path"},
        {"trait_id": 2004, "trait_name": "毁灭", "trait_type": "path"},
    ]
    return payload


def fuzzy_role_config_response() -> dict:
    payload = fake_cw_config_response()
    payload["data"]["role_list"] = [
        {
            "id": 1001,
            "name": "黑塔",
            "front_back_type": "front",
            "trait_details": [{"id": 2001}],
            "role_tags": ["输出", "智识"],
        },
        {
            "id": 1002,
            "name": "大黑塔",
            "front_back_type": "front",
            "trait_details": [{"id": 2001}],
            "role_tags": ["输出"],
        },
        {
            "id": 1003,
            "name": "银狼",
            "front_back_type": "back",
            "trait_details": [{"id": 2002}],
            "role_tags": ["减防"],
        },
        {
            "id": 1004,
            "name": "银狼Lv.999",
            "front_back_type": "back",
            "trait_details": [{"id": 2002}],
            "role_tags": ["减防"],
        },
        {
            "id": 1005,
            "name": "花火",
            "front_back_type": "back",
            "trait_details": [{"id": 2002}],
            "role_tags": ["辅助"],
        },
        {
            "id": 1006,
            "name": "火花",
            "front_back_type": "back",
            "trait_details": [{"id": 2002}],
            "role_tags": ["辅助"],
        },
    ]
    return payload


def fake_lineup_index_response() -> dict:
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "list": [
                {
                    "id": "lineup-demo",
                    "title": "7群攻2银河学者",
                    "nickname": "测试作者",
                    "description": "高分稳定上分阵容",
                    "has_change_equip": True,
                    "has_expert": True,
                    "is_like": True,
                    "is_favour": False,
                    "created_at": 1734691200,
                    "last_edit": 1734777600,
                    "certification": {"title": "认证作者"},
                    "game_data": {
                        "interact": {
                            "like_num": 123,
                            "favour_num": 45,
                            "view_num": 6789,
                            "use_num": 321,
                        },
                        "recent_interact": {
                            "like_num": 12,
                            "favour_num": 4,
                            "view_num": 345,
                            "use_num": 22,
                        },
                    },
                    "tourn_detail": {
                        "share_code": "LIST-CODE",
                        "labels": [{"text": "9级搜牌"}, {"text": "银河学者"}],
                        "carry_list": [{"name": "希儿"}, {"name": "布洛妮娅"}],
                        "order_compose": [{"name": "推进器"}],
                        "rpg_game_big_version": "3.1",
                        "support_hard": True,
                        "role_stages": [
                            {
                                "stage": "Opening",
                                "front_roles": [{"name": "黑塔", "star": 4, "rarity": 2, "is_carry": False}],
                                "back_roles": [{"name": "艾丝妲", "star": 4, "rarity": 2, "is_carry": False}],
                                "traits": [{"name": "智识"}],
                            },
                            {
                                "stage": "Final",
                                "front_roles": [
                                    {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                                    {"name": "布洛妮娅", "star": 5, "rarity": 3, "is_carry": False},
                                ],
                                "back_roles": [{"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False}],
                                "traits": [{"name": "巡猎"}, {"name": "量子"}],
                            },
                        ],
                    },
                }
            ],
            "next_page_token": "next-token-demo",
        },
    }


def fake_lineup_index_item(*, lineup_id: str, title: str, summary_portals: list[str] | None = None) -> dict:
    payload = deepcopy(fake_lineup_index_response()["data"]["list"][0])
    payload["id"] = lineup_id
    payload["title"] = title
    payload["tourn_detail"]["portals"] = [{"name": name} for name in (summary_portals or [])]
    return payload


def load_cw_guide_module():
    try:
        return importlib.import_module("trail.scenes.cw.guide")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.guide: {exc}")


def test_apply_guide_populates_cw_scene_state(tmp_path):
    guide_module = load_cw_guide_module()
    apply_cw_guide = getattr(guide_module, "apply_cw_guide", None)
    assert apply_cw_guide is not None

    role_stages = [
        {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
        {"stage": "Final", "front_roles": [{"name": "希儿", "star": 3}], "back_roles": [{"name": "佩拉", "star": 2}]},
    ]
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    refreshed = apply_cw_guide(session, guide_data={**fake_guide(), "role_stages": role_stages})

    cw_state = refreshed.scene_state["cw"]
    assert "guide" in cw_state
    assert "constraints" in cw_state
    assert "slots" in cw_state
    assert "artifact" not in cw_state["guide"]
    assert "artifact_id" not in cw_state["guide"]
    assert cw_state["guide"]["share_code"] == "##demo##"
    assert "on_field" not in cw_state["guide"]
    assert "off_field" not in cw_state["guide"]
    assert "remaining_purchases" not in cw_state["guide"]
    assert cw_state["guide"]["role_stages"] == role_stages
    assert cw_state["guide"]["operation_guide"] == "前期按测试运营"
    assert cw_state["guide"]["first_fight_augments"] == [{"name": "快攻"}]
    assert cw_state["constraints"]["min_coins"] == 40
    assert cw_state["constraints"]["min_level"] == 7
    assert cw_state["constraints"]["mid_level"] == 9
    assert cw_state["slots"]["stale"] is True


def test_apply_guide_preserves_source_metadata_for_later_scene_steps(tmp_path):
    guide_module = load_cw_guide_module()
    apply_cw_guide = getattr(guide_module, "apply_cw_guide", None)
    assert apply_cw_guide is not None

    role_stages = [
        {"stage": "Opening", "front_roles": [{"name": "黑塔", "star": 1}], "back_roles": []},
        {"stage": "Final", "front_roles": [{"name": "希儿", "star": 3}], "back_roles": [{"name": "佩拉", "star": 2}]},
    ]
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    refreshed = apply_cw_guide(
        session,
        guide_data={
            **fake_guide(),
            "role_stages": role_stages,
            "source_url": fake_lineup_url("70472857"),
            "lineup_id": "70472857",
            "title": "货币战争攻略码",
            "author": "测试作者",
            "uploader": "测试作者",
        },
    )

    assert refreshed.scene_state["cw"]["guide"] == {
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "70472857",
        "share_code": "##demo##",
        "source_url": fake_lineup_url("70472857"),
        "title": "货币战争攻略码",
        "author": "测试作者",
        "uploader": "测试作者",
        "version": "4.0",
        "operation_guide": "前期按测试运营",
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 9,
        "priority": {},
        "positioning": {},
        "role_stages": role_stages,
        "first_fight_augments": [{"name": "快攻"}],
        "second_fight_augments": [{"name": "回蓝"}],
        "order_basic": [{"name": "钻头"}],
        "order_compose": [{"name": "风暴"}],
    }


def test_apply_guide_clears_existing_sell_plan(tmp_path):
    guide_module = load_cw_guide_module()
    apply_cw_guide = getattr(guide_module, "apply_cw_guide", None)
    assert apply_cw_guide is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    session.scene_state["cw"] = {
        "guide": None,
        "constraints": {},
        "slots": {"stale": False, "hand": ["银狼"]},
        "sell_plan": {"candidates": [0]},
        "shop": {"stale": False},
        "stage": {"stale": False},
        "metrics": {},
    }

    refreshed = apply_cw_guide(session, guide_data=fake_guide())

    assert refreshed.scene_state["cw"]["sell_plan"] == {}


def test_select_cw_guide_updates_guide_without_resetting_runtime_state(tmp_path: Path):
    guide_module = load_cw_guide_module()
    select_cw_guide = getattr(guide_module, "select_cw_guide", None)
    assert select_cw_guide is not None

    from trail.scenes.cw.models import ensure_cw_state

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session).update(
        {
            "portal": {"cards": [{"card_idx": 1}], "stale": False},
            "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
            "slots": {"stale": False, "hand": ["银狼"]},
            "sell_plan": {"candidates": [0]},
            "stage": {"stale": False, "name": "shop"},
        }
    )

    select_cw_guide(
        session,
        guide_data={
            **fake_guide(),
            "artifact_id": "art-selected",
            "lineup_id": "abc",
        },
    )

    cw_state = ensure_cw_state(session)
    assert cw_state["guide"]["lineup_id"] == "abc"
    assert "artifact" not in cw_state["guide"]
    assert "artifact_id" not in cw_state["guide"]
    assert cw_state["constraints"] == {
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 9,
        "priority": {},
        "positioning": {},
    }
    assert cw_state["portal"] == {"cards": [{"card_idx": 1}], "stale": False}
    assert cw_state["shop"] == {"opened": True, "stale": False, "items": [{"name": "希儿"}]}
    assert cw_state["slots"] == {"stale": False, "hand": ["银狼"]}
    assert cw_state["sell_plan"] == {"candidates": [0]}
    assert cw_state["stage"] == {"stale": False, "name": "shop"}


def test_select_cw_guide_persists_complete_payload_and_operation_guide(tmp_path: Path) -> None:
    guide_module = load_cw_guide_module()
    select_cw_guide = getattr(guide_module, "select_cw_guide", None)
    normalize_cw_guide_payload = getattr(guide_module, "normalize_cw_guide_payload", None)
    assert select_cw_guide is not None
    assert normalize_cw_guide_payload is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    guide = normalize_cw_guide_payload(
        {
            "scene": "cw",
            "kind": "guide",
            "lineup_id": "g1",
            "title": "测试攻略",
            "share_code": "##code##",
            "version": "4.0",
            "min_coins": 40,
            "min_level": 6,
            "mid_level": 9,
            "operation_guide": "前期按测试运营",
            "on_field": {"灵砂": 1},
            "off_field": {"星期日": 1},
            "role_stages": [
                {
                    "stage": "Opening",
                    "front_roles": [{"name": "灵砂"}],
                    "back_roles": [{"name": "星期日"}],
                    "traits": ["2丰饶"],
                }
            ],
            "first_fight_augments": [{"name": "击破概念股"}],
            "second_fight_augments": [{"name": "折射棱镜"}],
            "order_basic": [{"name": "钻头"}],
            "order_compose": [{"name": "风暴"}],
        }
    )

    select_cw_guide(session, guide_data=guide)
    cw_state = session.scene_state["cw"]

    assert cw_state["guide"]["operation_guide"] == "前期按测试运营"
    assert "remaining_purchases" not in cw_state["guide"]
    assert "on_field" not in cw_state["guide"]
    assert "off_field" not in cw_state["guide"]
    assert cw_state["guide"]["first_fight_augments"] == [{"name": "击破概念股"}]
    assert cw_state["guide"]["second_fight_augments"] == [{"name": "折射棱镜"}]
    assert cw_state["guide"]["order_basic"] == [{"name": "钻头"}]
    assert cw_state["guide"]["order_compose"] == [{"name": "风暴"}]
    assert cw_state["guide"]["role_stages"][0]["stage"] == "Opening"
    assert cw_state["guide"]["role_stages"][0]["traits"] == ["2丰饶"]
    assert cw_state["constraints"]["min_coins"] == 40
    assert cw_state["constraints"]["min_level"] == 6
    assert cw_state["constraints"]["mid_level"] == 9
    assert "artifact" not in cw_state["guide"]
    assert "artifact_id" not in cw_state["guide"]


def test_invalidate_cw_guide_runtime_state_resets_runtime_dependent_slices_only(tmp_path: Path):
    guide_module = load_cw_guide_module()
    invalidate_cw_guide_runtime_state = getattr(guide_module, "invalidate_cw_guide_runtime_state", None)
    assert invalidate_cw_guide_runtime_state is not None

    from trail.scenes.cw.models import ensure_cw_state

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session).update(
        {
            "guide": {"lineup_id": "abc", "share_code": "##demo##"},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 9, "priority": {}, "positioning": {}},
            "slots": {"stale": False, "hand": ["银狼"]},
            "sell_plan": {"candidates": [0]},
            "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
            "stage": {"stale": False, "name": "shop"},
        }
    )

    invalidate_cw_guide_runtime_state(session)

    cw_state = ensure_cw_state(session)
    assert cw_state["guide"] == {"lineup_id": "abc", "share_code": "##demo##"}
    assert cw_state["constraints"] == {
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 9,
        "priority": {},
        "positioning": {},
    }
    assert cw_state["slots"] == {"stale": True}
    assert cw_state["sell_plan"] == {}
    assert cw_state["shop"] == {"stale": True}
    assert cw_state["stage"] == {"stale": True, "name": "shop"}


def test_fetch_cw_guide_payload_builds_payload_from_lineup_detail(monkeypatch):
    guide_module = load_cw_guide_module()
    fetch_cw_guide_payload = getattr(guide_module, "fetch_cw_guide_payload", None)
    assert fetch_cw_guide_payload is not None

    monkeypatch.setattr(
        guide_module,
        "urlopen",
        lambda request, timeout=10: FakeHttpResponse(fake_lineup_detail_response(share_code="ABC-123", lineup_id="lineup-demo")),
        raising=False,
    )

    payload = fetch_cw_guide_payload(fake_lineup_url("lineup-demo"))

    assert payload == {
        "scene": "cw",
        "kind": "guide",
        "source_url": fake_lineup_url("lineup-demo"),
        "lineup_id": "lineup-demo",
        "title": "7群攻2银河学者",
        "author": "测试作者",
        "uploader": "测试作者",
        "share_code": "##ABC-123##",
        "labels": ["9级搜牌", "银河学者"],
        "support_hard": True,
        "has_change_equip": True,
        "has_expert": True,
        "operation_guide": "前期：过渡\n中期：D牌\n后期：补强",
        "version": "3.1",
        "min_coins": 40,
        "min_level": 9,
        "mid_level": 9,
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
        "role_stages": [
            {
                "stage": "Opening",
                "front_roles": [{"name": "黑塔", "star": 1, "rarity": 1, "is_carry": False}],
                "back_roles": [{"name": "艾丝妲", "star": 1, "rarity": 1, "is_carry": False}],
                    "traits": ["1智识"],
            },
            {
                "stage": "Final",
                "front_roles": [
                    {
                        "name": "希儿",
                        "star": 3,
                        "rarity": 3,
                        "is_carry": True,
                        "first_equipments": ["高周波电锯", "战场进化手册"],
                        "second_equipments": ["胜利之旗"],
                    }
                ],
                "back_roles": [{"name": "佩拉", "star": 2, "rarity": 2, "is_carry": False}],
                    "traits": ["1巡猎", "2量子"],
            },
        ],
        "first_fight_augments": ["银河大乐透", "超距遥感"],
        "second_fight_augments": ["折射棱镜"],
        "portals": ["购物区", "事件区"],
        "order_basic": ["抢前排输出", "补减防"],
        "order_compose": ["推进器"],
    }


def test_fetch_cw_guide_payload_accepts_raw_lineup_id(monkeypatch):
    guide_module = load_cw_guide_module()
    fetch_cw_guide_payload = getattr(guide_module, "fetch_cw_guide_payload", None)
    assert fetch_cw_guide_payload is not None

    captured_request: dict[str, object] = {}

    def fake_urlopen(request, timeout=10):
        captured_request["url"] = request.full_url
        return FakeHttpResponse(fake_lineup_detail_response(lineup_id="69c9014f24546dfbd2b26227"))

    monkeypatch.setattr(guide_module, "urlopen", fake_urlopen, raising=False)

    payload = fetch_cw_guide_payload("69c9014f24546dfbd2b26227")

    assert captured_request["url"] == "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/lineup/detail?id=69c9014f24546dfbd2b26227&game=hkrpg"
    assert payload["lineup_id"] == "69c9014f24546dfbd2b26227"
    assert payload["source_url"] == fake_lineup_url("69c9014f24546dfbd2b26227")


def test_fetch_cw_guide_config_returns_minimal_catalog(monkeypatch):
    guide_module = load_cw_guide_module()
    fetch_cw_guide_config = getattr(guide_module, "fetch_cw_guide_config", None)
    assert fetch_cw_guide_config is not None

    captured_request: dict[str, object] = {}

    def fake_urlopen(request, timeout=10):
        captured_request["url"] = request.full_url
        captured_request["timeout"] = timeout
        captured_request["headers"] = {key.lower(): value for key, value in request.header_items()}
        return FakeHttpResponse(fake_cw_config_response())

    monkeypatch.setattr(guide_module, "urlopen", fake_urlopen, raising=False)

    payload = fetch_cw_guide_config()

    assert captured_request == {
        "url": "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/config?game=hkrpg",
        "timeout": 10,
        "headers": {
            "accept": "application/json, text/plain, */*",
            "x-rpc-currencywar-tourn": "tourn",
            "x-rpc-platform": "pc",
        },
    }
    assert payload == {
        "meta": {
            "game": "hkrpg",
            "season_id": 12,
            "sub_season_id": 3,
            "big_version": "3.2",
            "lineup_filter_version": "lineup-filter-v2",
        },
        "lineup_levels": [
            {"id": 7, "name": "7级搜牌"},
            {"id": 9, "name": "9级搜牌"},
        ],
        "traits": [
            {"id": 2001, "name": "巡猎", "type": "path"},
            {"id": 2002, "name": "量子", "type": "element"},
        ],
        "roles": [
            {"id": 1001, "name": "希儿", "front_back_type": "front", "trait_ids": [2001, 2002]},
            {"id": 1002, "name": "佩拉", "front_back_type": "back", "trait_ids": [2002]},
            {"id": 1003, "name": "布洛妮娅", "front_back_type": "back", "trait_ids": [2001]},
        ],
        "role_tags": ["输出", "辅助", "减防", "量子"],
        "portal_list": [
            {"portal_id": "shop", "title": "购物区", "description": "花金币买角色和升级"},
            {"portal_id": "event", "title": "事件区", "description": "处理事件、补给与遭遇"},
        ],
        "strategy_list": [],
    }


def test_fetch_cw_guide_config_includes_strategy_list_without_changing_existing_shape(monkeypatch):
    guide_module = load_cw_guide_module()

    def fake_urlopen(request, timeout=10):
        del request, timeout
        payload = fake_cw_config_response()
        payload["data"]["fight_augment_list"] = [
            {"id": "rush", "name": "快攻", "description": "desc1"},
            {"id": "mana", "name": "回蓝", "description": "desc2"},
        ]
        return FakeHttpResponse(payload)

    monkeypatch.setattr(guide_module, "urlopen", fake_urlopen, raising=False)

    payload = guide_module.fetch_cw_guide_config()

    assert payload["strategy_list"] == [
        {"strategy_id": "rush", "title": "快攻", "description": "desc1"},
        {"strategy_id": "mana", "title": "回蓝", "description": "desc2"},
    ]
    assert payload["portal_list"] == [
        {"portal_id": "shop", "title": "购物区", "description": "花金币买角色和升级"},
        {"portal_id": "event", "title": "事件区", "description": "处理事件、补给与遭遇"},
    ]


def test_fetch_cw_guide_config_writes_and_reuses_workspace_cache(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()

    calls = {"count": 0}

    def fake_urlopen(request, timeout=10):
        del request, timeout
        calls["count"] += 1
        return FakeHttpResponse(fake_cw_config_response())

    monkeypatch.setattr(guide_module, "urlopen", fake_urlopen, raising=False)

    first = guide_module.fetch_cw_guide_config(workspace_root=tmp_path)
    second = guide_module.fetch_cw_guide_config(workspace_root=tmp_path)

    assert calls["count"] == 1
    assert first == second
    cache_path = tmp_path / ".trail" / "cache" / "cw-guide-config.json"
    assert cache_path.is_file()


def test_fetch_cw_guide_config_enriches_trait_layers_from_raw_lineup_list(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": "1004", "trait_name": "公司", "layers": []},
        {"trait_id": "1007", "trait_name": "仙舟", "layers": []},
        {"trait_id": "2002", "trait_name": "击破", "layers": []},
    ]
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: payload["data"])
    calls = []

    def fake_list(**kwargs):
        calls.append(kwargs.get("trait_id"))
        return {
            "list": [
                {
                    "tourn_detail": {
                        "role_stages": [
                            {
                                "stage": "Early",
                                "traits": [
                                    {
                                        "trait_id": "1004",
                                        "trait_name": "公司",
                                        "layers": [{"layer": 2}, {"layer": 3}],
                                        "current_role_count": 2,
                                    }
                                ],
                            },
                            {
                                "stage": "Middle",
                                "traits": [
                                    {
                                        "trait_id": "1007",
                                        "trait_name": "仙舟",
                                        "layers": [{"layer": 3}, {"layer": 5}, {"layer": 7}, {"layer": 10}],
                                        "current_role_count": 3,
                                    }
                                ],
                            },
                            {
                                "stage": "Final",
                                "traits": [
                                    {
                                        "trait_id": "2002",
                                        "trait_name": "击破",
                                        "layers": [{"layer": 2}, {"layer": 4}, {"layer": 6}, {"layer": 8}, {"layer": 10}],
                                        "current_role_count": 4,
                                    }
                                ],
                            },
                        ]
                    }
                }
            ],
            "next_page_token": None,
        }

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_list)

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    by_name = {item["name"]: item for item in config["traits"]}
    assert [layer["layer"] for layer in by_name["公司"]["layers"]] == [2, 3]
    assert [layer["layer"] for layer in by_name["仙舟"]["layers"]] == [3, 5, 7, 10]
    assert [layer["layer"] for layer in by_name["击破"]["layers"]] == [2, 4, 6, 8, 10]
    assert calls == [None]


def test_fetch_cw_guide_config_default_does_not_request_guide_list(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: fake_cw_config_response()["data"])
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not fetch list")),
    )

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path)

    assert config["traits"]


def test_fetch_cw_guide_config_enriched_cache_hit_avoids_guide_list(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    cache_path = tmp_path / ".trail" / "cache" / "cw-guide-config-enriched.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cached_traits = [
        {"id": "2001", "name": "巡猎", "type": "path", "layers": [{"layer": 2}, {"layer": 4}]},
        {"id": "2002", "name": "量子", "type": "element", "layers": [{"layer": 3}]},
    ]
    cache_path.write_text(
        json.dumps(
            {
                "complete": True,
                "meta": {
                    "season_id": 12,
                    "sub_season_id": 3,
                    "rpg_game_big_version": "3.2",
                    "rpg_game_lineup_tourn_filter": "lineup-filter-v2",
                },
                "traits": cached_traits,
                "missing_trait_ids": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: fake_cw_config_response()["data"])
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("cache hit should avoid list fetch")),
    )

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    assert config["traits"] == cached_traits


def test_fetch_cw_guide_config_enrichment_failure_without_cache_returns_base_traits(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": "1004", "trait_name": "公司", "layers": []},
    ]
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: payload["data"])
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: (_ for _ in ()).throw(TrailError("GUIDE_FETCH_FAILED", "guide list failed")),
    )

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    assert config["traits"] == [{"id": "1004", "name": "公司", "type": None}]
    assert not (tmp_path / ".trail" / "cache" / "cw-guide-config-enriched.json").exists()


def test_fetch_cw_guide_config_writes_complete_cache_with_missing_trait_ids(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": "1004", "trait_name": "公司", "layers": []},
        {"trait_id": "9999", "trait_name": "未知羁绊", "layers": []},
    ]
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: payload["data"])
    calls = []

    def fake_list(**kwargs):
        calls.append(kwargs.get("trait_id"))
        if kwargs.get("trait_id") is None:
            return {
                "list": [
                    {
                        "tourn_detail": {
                            "role_stages": [
                                {
                                    "stage": "Final",
                                    "traits": [
                                        {"trait_id": "1004", "trait_name": "公司", "layers": [{"layer": 2}, {"layer": 3}]}
                                    ],
                                }
                            ]
                        }
                    }
                ],
                "next_page_token": None,
            }
        return {"list": [], "next_page_token": None}

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_list)

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    by_name = {item["name"]: item for item in config["traits"]}
    assert [layer["layer"] for layer in by_name["公司"]["layers"]] == [2, 3]
    assert "layers" not in by_name["未知羁绊"]
    assert calls == [None, 9999]
    cache_payload = json.loads((tmp_path / ".trail" / "cache" / "cw-guide-config-enriched.json").read_text(encoding="utf-8"))
    assert cache_payload["complete"] is True
    assert cache_payload["missing_trait_ids"] == ["9999"]
    assert cache_payload["meta"]["rpg_game_big_version"] == "3.2"
    assert cache_payload["meta"]["rpg_game_lineup_tourn_filter"] == "lineup-filter-v2"


def test_fetch_cw_guide_config_enrichment_reads_beyond_thirty_pages(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": "1004", "trait_name": "公司", "layers": []},
    ]
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: payload["data"])
    calls = []

    def fake_list(**kwargs):
        token = kwargs.get("next_page_token")
        calls.append(token)
        page_number = 1 if token is None else int(str(token).removeprefix("page-"))
        if page_number == 31:
            return {
                "list": [
                    {
                        "tourn_detail": {
                            "role_stages": [
                                {
                                    "stage": "Final",
                                    "traits": [{"trait_id": "1004", "trait_name": "公司", "layers": [{"layer": 2}]}],
                                }
                            ]
                        }
                    }
                ],
                "next_page_token": None,
            }
        return {"list": [], "next_page_token": f"page-{page_number + 1}"}

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_list)

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    by_name = {item["name"]: item for item in config["traits"]}
    assert [layer["layer"] for layer in by_name["公司"]["layers"]] == [2]
    assert len(calls) == 31
    cache_payload = json.loads((tmp_path / ".trail" / "cache" / "cw-guide-config-enriched.json").read_text(encoding="utf-8"))
    assert cache_payload["complete"] is True


def test_fetch_cw_guide_config_enrichment_stops_on_repeated_next_page_token(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": "1004", "trait_name": "公司", "layers": []},
    ]
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: payload["data"])
    calls = []

    def fake_list(**kwargs):
        token = kwargs.get("next_page_token")
        calls.append(token)
        traits = []
        if token == "repeat-token":
            traits = [{"trait_id": "1004", "trait_name": "公司", "layers": [{"layer": 2}]}]
        return {
            "list": [{"tourn_detail": {"role_stages": [{"stage": "Final", "traits": traits}]}}],
            "next_page_token": "repeat-token",
        }

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_list)

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    by_name = {item["name"]: item for item in config["traits"]}
    assert [layer["layer"] for layer in by_name["公司"]["layers"]] == [2]
    assert calls == [None, "repeat-token"]


def test_fetch_cw_guide_config_enrichment_merges_name_only_raw_trait_with_base_id(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": "1004", "trait_name": "公司", "layers": []},
    ]
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: payload["data"])
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: {
            "list": [
                {
                    "tourn_detail": {
                        "role_stages": [
                            {
                                "stage": "Final",
                                "traits": [{"trait_name": "公司", "layers": [{"layer": 2}]}],
                            }
                        ]
                    }
                }
            ],
            "next_page_token": None,
        },
    )

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    assert config["traits"] == [{"id": "1004", "name": "公司", "layers": [{"layer": 2}]}]


def test_fetch_cw_guide_config_enrichment_keeps_result_when_cache_write_fails(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": "1004", "trait_name": "公司", "layers": []},
    ]
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: payload["data"])
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: {
            "list": [
                {
                    "tourn_detail": {
                        "role_stages": [
                            {
                                "stage": "Final",
                                "traits": [{"trait_id": "1004", "trait_name": "公司", "layers": [{"layer": 2}]}],
                            }
                        ]
                    }
                }
            ],
            "next_page_token": None,
        },
    )
    monkeypatch.setattr(
        guide_module,
        "_write_enriched_traits_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cache write failed")),
    )

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    by_name = {item["name"]: item for item in config["traits"]}
    assert [layer["layer"] for layer in by_name["公司"]["layers"]] == [2]


def test_write_enriched_traits_cache_uses_unique_temp_file(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    cache_path = guide_module._cw_guide_enriched_config_cache_path(workspace_root=tmp_path)
    path_type = type(cache_path)
    written_paths = []
    replaced_paths = []

    def fake_write_text(self, *args, **kwargs):
        written_paths.append(self.name)
        return 1

    def fake_replace(self, target):
        replaced_paths.append((self.name, target.name))
        return target

    monkeypatch.setattr(path_type, "write_text", fake_write_text)
    monkeypatch.setattr(path_type, "replace", fake_replace)

    guide_module._write_enriched_traits_cache(
        {"season_id": 12},
        [{"id": "1004", "name": "公司", "layers": [{"layer": 2}]}],
        [],
        workspace_root=tmp_path,
    )

    assert written_paths
    assert written_paths[0] != "cw-guide-config-enriched.json.tmp"
    assert written_paths[0].startswith("cw-guide-config-enriched.json.")
    assert replaced_paths == [(written_paths[0], "cw-guide-config-enriched.json")]


def test_guide_config_yaml_uses_enriched_config(monkeypatch, tmp_path: Path):
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION
    from trail.output.rendering import render_output

    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, service, session, cw_service
    captured_kwargs = {}

    def fake_fetch_config(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "meta": {"season_id": 12, "sub_season_id": 3, "big_version": "3.2"},
            "lineup_levels": [],
            "traits": [{"id": "1004", "name": "公司", "layers": [{"layer": 2}, {"layer": 3}]}],
            "roles": [],
            "role_tags": [],
            "portal_list": [],
            "strategy_list": [],
        }

    monkeypatch.setattr("trail.scenes.cw.guide.fetch_cw_guide_config", fake_fetch_config)

    envelope = command_service.handle(
        DaemonRequest(
            request_id="req-guide-config-yaml",
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(tmp_path),
            session_id=None,
            verbose=False,
            method="guide.config.cw",
            payload={},
        )
    )
    rendered = render_output("guide.config.cw", envelope, output_format="yaml")

    assert captured_kwargs["enrich_traits"] is True
    assert rendered.splitlines()[:2] == [
        "ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2",
        "info 搜牌档位=0 羁绊=1 角色=0 角色标签=0 投资环境=0",
    ]
    assert "traits:" in rendered
    assert "layers:" in rendered
    assert "- layer: 2" in rendered


def test_fetch_cw_raw_guide_config_returns_cached_raw_data(monkeypatch, tmp_path):
    from trail.scenes.cw import guide

    monkeypatch.setattr(
        guide,
        "_get_cw_config_data",
        lambda timeout=10, workspace_root=None: {"rpg_game_big_version": "3.2", "equipment_list": []},
    )

    assert guide.fetch_cw_raw_guide_config(workspace_root=tmp_path) == {
        "rpg_game_big_version": "3.2",
        "equipment_list": [],
    }


def test_fetch_cw_guide_list_uses_cached_workspace_config_for_trait_resolution(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()

    cache_path = tmp_path / ".trail" / "cache" / "cw-guide-config.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(fake_cw_config_response()["data"], ensure_ascii=False), encoding="utf-8")
    captured_trait_id: dict[str, object] = {}

    def fake_fetch_guide_list_data(**kwargs):
        captured_trait_id["value"] = kwargs["trait_id"]
        return {"list": [], "next_page_token": None}

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_fetch_guide_list_data, raising=False)
    monkeypatch.setattr(
        guide_module,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("cached config path should avoid refetching guide config"),
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(page=1, limit=10, trait="巡猎", workspace_root=tmp_path)

    assert payload["list"] == []
    assert captured_trait_id["value"] == 2001


def test_build_guide_list_request_payload_includes_role_ids():
    guide_module = load_cw_guide_module()

    payload = json.loads(
        guide_module._build_guide_list_request_payload(
            page=1,
            limit=10,
            trait_id=321,
            role_ids=["1001", "1002"],
            order=None,
            next_page_token=None,
            match_change_job=None,
            match_hard=None,
        ).decode("utf-8")
    )

    assert payload["trait_ids"] == ["321", ""]
    assert payload["role_ids"] == ["1001", "1002"]


def test_fetch_cw_guide_list_posts_filters_and_normalizes_response(monkeypatch):
    guide_module = load_cw_guide_module()
    fetch_cw_guide_list = getattr(guide_module, "fetch_cw_guide_list", None)
    assert fetch_cw_guide_list is not None

    captured_request: dict[str, object] = {}

    def fake_urlopen(request, timeout=10):
        captured_request["url"] = request.full_url
        captured_request["timeout"] = timeout
        captured_request["method"] = request.get_method()
        captured_request["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured_request["body"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse(fake_lineup_index_response())

    monkeypatch.setattr(guide_module, "urlopen", fake_urlopen, raising=False)

    payload = fetch_cw_guide_list(
        page=2,
        limit=10,
        trait_id=321,
        order="hot",
        next_page_token="cursor-demo",
        match_change_job=True,
        match_hard=False,
    )

    assert captured_request == {
        "url": "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/lineup/index",
        "timeout": 10,
        "method": "POST",
        "headers": {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "x-rpc-currencywar-tourn": "tourn",
            "x-rpc-platform": "pc",
        },
        "body": {
            "game": "hkrpg",
            "page": "2",
            "limit": "10",
            "lineup_type": "Tourn",
            "role_ids": [],
            "trait_ids": ["321", ""],
            "order": "Hot",
            "next_page_token": "cursor-demo",
            "match_change_job": True,
            "match_hard": False,
        },
    }
    assert payload == {
        "list": [
            {
                "id": "lineup-demo",
                "title": "7群攻2银河学者",
                "nickname": "测试作者",
                "description": "高分稳定上分阵容",
                "labels": ["9级搜牌", "银河学者"],
                "final_traits": ["巡猎", "量子"],
                "final_role_cards": [
                    {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                    {"name": "布洛妮娅", "star": 5, "rarity": 3, "is_carry": False},
                    {"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False},
                ],
                "has_change_equip": True,
                "has_expert": True,
                "version": "3.1",
            "created_at": 1734691200,
            "last_edit": 1734777600,
            "carry_roles": ["希儿", "布洛妮娅"],
            "like": 123,
            "favour": 45,
            "interact": {"like": 123, "favour": 45, "view": 6789, "use": 321},
            "recent_interact": {"like": 12, "favour": 4, "view": 345, "use": 22},
            "support_hard": True,
            }
        ],
        "next_page_token": "next-token-demo",
    }


def test_fetch_cw_guide_list_resolves_trait_name_to_single_trait_id(monkeypatch):
    guide_module = load_cw_guide_module()
    captured_list_request: dict[str, object] = {}

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fake_cw_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": [], "next_page_token": None},
        raising=False,
    )

    guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait="巡猎",
        trait_id=None,
        role=None,
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )

    assert captured_list_request["trait_id"] == 2001


def test_fetch_cw_guide_list_trait_name_miss_raises_GuideTraitLookupError(monkeypatch):
    guide_module = load_cw_guide_module()

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: trait_lookup_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: pytest.fail("trait lookup miss should fail before requesting guide list"),
        raising=False,
    )

    with pytest.raises(guide_module.GuideTraitLookupError) as exc_info:
        guide_module.fetch_cw_guide_list(
            page=1,
            limit=10,
            trait="巡猎者",
            trait_id=None,
            role=None,
            role_id=None,
            order=None,
            next_page_token=None,
            match_change_job=None,
            match_hard=None,
        )

    assert exc_info.value.code == "GUIDE_TRAIT_INVALID"
    assert [candidate["trait"] for candidate in exc_info.value.candidates] == ["巡猎", "智识", "毁灭"]
    assert [candidate["trait_id"] for candidate in exc_info.value.candidates] == ["2001", "2003", "2004"]


def test_fetch_cw_guide_list_role_name_miss_returns_role_candidates_and_warning(monkeypatch):
    guide_module = load_cw_guide_module()
    captured_list_request: dict[str, object] = {}

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fuzzy_role_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": [fake_lineup_index_item(lineup_id="lineup-herta", title="黑塔阵容")], "next_page_token": None},
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait=None,
        trait_id=None,
        role="黑搭",
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )

    assert captured_list_request["role_ids"] == ["1001"]
    assert payload["role_candidates"] == [
        {
            "query": "黑搭",
            "role_resolution": "fuzzy",
            "resolved": "黑塔",
            "candidates": [
                {
                    "query": "黑搭",
                    "role": "黑塔",
                    "id": "1001",
                    "selected": True,
                    "score": 0.5,
                    "front_back": "front",
                    "traits": ["巡猎"],
                    "role_tags": ["输出", "智识"],
                },
                {
                    "query": "黑搭",
                    "role": "大黑塔",
                    "id": "1002",
                    "selected": False,
                    "score": 0.4,
                    "front_back": "front",
                    "traits": ["巡猎"],
                    "role_tags": ["输出"],
                },
                {
                    "query": "黑搭",
                    "role": "火花",
                    "id": "1006",
                    "selected": False,
                    "score": 0.0,
                    "front_back": "back",
                    "traits": ["量子"],
                    "role_tags": ["辅助"],
                },
            ],
        }
    ]
    assert payload["role_warnings"] == [
        {
            "code": "GUIDE_ROLE_FUZZY_MATCH",
            "query": "黑搭",
            "resolved": "黑塔",
            "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
        }
    ]


def test_fetch_cw_guide_list_role_id_values_pass_through_to_role_ids(monkeypatch):
    guide_module = load_cw_guide_module()
    captured_list_request: dict[str, object] = {}

    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_config_data",
        lambda timeout=10: pytest.fail("role-id passthrough should not require config lookup"),
        raising=False,
    )
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": [], "next_page_token": None},
        raising=False,
    )

    guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait=None,
        trait_id=None,
        role=None,
        role_id=["1003", "1002", "1003"],
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )

    assert captured_list_request["role_ids"] == ["1003", "1002", "1003"]


def test_fetch_cw_guide_list_role_exact_match_keeps_ambiguous_candidates(monkeypatch):
    guide_module = load_cw_guide_module()
    captured_list_request: dict[str, object] = {}

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fuzzy_role_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": [fake_lineup_index_item(lineup_id="lineup-huohuo", title="花火阵容")], "next_page_token": None},
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait=None,
        trait_id=None,
        role="花火",
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )

    assert captured_list_request["role_ids"] == ["1005"]
    assert payload["role_candidates"] == [
        {
            "query": "花火",
            "role_resolution": "exact_ambiguous",
            "resolved": "花火",
            "candidates": [
                {
                    "query": "花火",
                    "role": "花火",
                    "id": "1005",
                    "selected": True,
                    "score": 1.0,
                    "front_back": "back",
                    "traits": ["量子"],
                    "role_tags": ["辅助"],
                },
                {
                    "query": "花火",
                    "role": "火花",
                    "id": "1006",
                    "selected": False,
                    "score": 0.5,
                    "front_back": "back",
                    "traits": ["量子"],
                    "role_tags": ["辅助"],
                },
            ],
        }
    ]
    assert payload["role_warnings"] == [
        {
            "code": "GUIDE_ROLE_SIMILAR_CANDIDATES",
            "query": "花火",
            "resolved": "花火",
            "message": "角色名虽已精确命中，但存在高相似候选，请确认目标角色是否正确",
        }
    ]


def test_fetch_cw_guide_list_multi_role_queries_keep_blocks_when_resolved_role_repeats(monkeypatch):
    guide_module = load_cw_guide_module()
    captured_list_request: dict[str, object] = {}

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fuzzy_role_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": [fake_lineup_index_item(lineup_id="lineup-herta", title="黑塔阵容")], "next_page_token": None},
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait=None,
        trait_id=None,
        role=["黑搭", "塔黑"],
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )

    assert captured_list_request["role_ids"] == ["1001"]
    assert [block["query"] for block in payload["role_candidates"]] == ["黑搭", "塔黑"]
    assert [block["resolved"] for block in payload["role_candidates"]] == ["黑塔", "黑塔"]
    assert [warning["query"] for warning in payload["role_warnings"]] == ["黑搭", "塔黑"]


def test_fetch_cw_guide_list_role_fuzzy_resolution_prefers_highest_similarity_over_contains_priority(monkeypatch):
    guide_module = load_cw_guide_module()
    captured_list_request: dict[str, object] = {}

    def regression_role_config_response() -> dict:
        payload = fake_cw_config_response()
        payload["data"]["role_list"] = [
            {
                "id": 3001,
                "name": "ab",
                "front_back_type": "front",
                "trait_details": [{"id": 2001}],
                "role_tags": ["contains"],
            },
            {
                "id": 3002,
                "name": "axef",
                "front_back_type": "back",
                "trait_details": [{"id": 2001}],
                "role_tags": ["best-score"],
            },
            {
                "id": 3003,
                "name": "feab",
                "front_back_type": "back",
                "trait_details": [{"id": 2001}],
                "role_tags": ["rearranged"],
            },
        ]
        return payload

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: regression_role_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": [fake_lineup_index_item(lineup_id="lineup-regression", title="回归阵容")], "next_page_token": None},
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait=None,
        trait_id=None,
        role="abef",
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )

    assert captured_list_request["role_ids"] == ["3002"]
    assert payload["role_candidates"] == [
        {
            "query": "abef",
            "role_resolution": "fuzzy",
            "resolved": "axef",
            "candidates": [
                {
                    "query": "abef",
                    "role": "axef",
                    "id": "3002",
                    "selected": True,
                    "score": 0.75,
                    "front_back": "back",
                    "traits": ["巡猎"],
                    "role_tags": ["best-score"],
                },
                {
                    "query": "abef",
                    "role": "feab",
                    "id": "3003",
                    "selected": False,
                    "score": 0.5,
                    "front_back": "back",
                    "traits": ["巡猎"],
                    "role_tags": ["rearranged"],
                },
                {
                    "query": "abef",
                    "role": "ab",
                    "id": "3001",
                    "selected": False,
                    "score": 0.67,
                    "front_back": "front",
                    "traits": ["巡猎"],
                    "role_tags": ["contains"],
                },
            ],
        }
    ]


def test_fetch_cw_guide_list_filters_by_portal_id_using_list_item_detail(monkeypatch):
    guide_module = load_cw_guide_module()
    fetch_cw_guide_list = getattr(guide_module, "fetch_cw_guide_list", None)
    assert fetch_cw_guide_list is not None

    raw_items = [
        fake_lineup_index_item(lineup_id="lineup-shop", title="购物阵容", summary_portals=["购物区"]),
        fake_lineup_index_item(lineup_id="lineup-event", title="事件阵容", summary_portals=["事件区"]),
    ]
    captured_list_request: dict[str, object] = {}

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fake_cw_config_response()["data"], raising=False)

    def fake_fetch_list_data(**kwargs):
        captured_list_request.update(kwargs)
        return {"list": raw_items, "next_page_token": "raw-next-token"}

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_fetch_list_data, raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_lineup_detail",
        lambda *args, **kwargs: pytest.fail("portal filtering should use list item detail"),
        raising=False,
    )

    payload = fetch_cw_guide_list(
        page=1,
        limit=1,
        trait_id=321,
        order="Recent",
        next_page_token=None,
        match_change_job=True,
        match_hard=False,
        portal_id="shop",
    )

    assert captured_list_request == {
        "page": 1,
        "limit": 10,
        "trait_id": 321,
        "role_ids": [],
        "order": "Recent",
        "next_page_token": None,
        "match_change_job": True,
        "match_hard": False,
        "timeout": 10,
    }
    assert payload["list"] == [
        {
            "id": "lineup-shop",
            "title": "购物阵容",
            "nickname": "测试作者",
            "description": "高分稳定上分阵容",
            "labels": ["9级搜牌", "银河学者"],
            "final_traits": ["巡猎", "量子"],
            "final_role_cards": [
                {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                {"name": "布洛妮娅", "star": 5, "rarity": 3, "is_carry": False},
                {"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False},
            ],
            "has_change_equip": True,
            "has_expert": True,
            "version": "3.1",
            "created_at": 1734691200,
            "last_edit": 1734777600,
            "carry_roles": ["希儿", "布洛妮娅"],
            "like": 123,
            "favour": 45,
            "interact": {"like": 123, "favour": 45, "view": 6789, "use": 321},
            "recent_interact": {"like": 12, "favour": 4, "view": 345, "use": 22},
            "support_hard": True,
        }
    ]
    assert payload["next_page_token"] == "raw-next-token"


def test_fetch_cw_guide_list_portal_filter_reuses_resolved_role_ids(monkeypatch):
    guide_module = load_cw_guide_module()
    raw_items = [fake_lineup_index_item(lineup_id="lineup-shop", title="购物阵容", summary_portals=["购物区"])]
    captured_list_request: dict[str, object] = {}

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fuzzy_role_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": raw_items, "next_page_token": None},
        raising=False,
    )
    monkeypatch.setattr(
        guide_module,
        "_fetch_lineup_detail",
        lambda *args, **kwargs: pytest.fail("portal filtering should use list item detail"),
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=1,
        trait=None,
        trait_id=None,
        role="黑搭",
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
        portal_id="shop",
    )

    assert captured_list_request["role_ids"] == ["1001"]
    assert payload["role_candidates"][0]["resolved"] == "黑塔"
    assert payload["list"][0]["id"] == "lineup-shop"


def test_fetch_cw_guide_list_portal_filter_reuses_resolved_trait_id(monkeypatch):
    guide_module = load_cw_guide_module()
    raw_items = [fake_lineup_index_item(lineup_id="lineup-shop", title="购物阵容", summary_portals=["购物区"])]
    captured_list_request: dict[str, object] = {}

    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fake_cw_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": raw_items, "next_page_token": None},
        raising=False,
    )
    monkeypatch.setattr(
        guide_module,
        "_fetch_lineup_detail",
        lambda *args, **kwargs: pytest.fail("portal filtering should use list item detail"),
        raising=False,
    )

    guide_module.fetch_cw_guide_list(
        page=1,
        limit=1,
        trait="巡猎",
        trait_id=None,
        role=None,
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
        portal_id="shop",
    )

    assert captured_list_request["trait_id"] == 2001


def test_normalize_lineup_summary_keeps_version_in_list_payload(monkeypatch):
    guide_module = load_cw_guide_module()

    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: fake_lineup_index_response()["data"],
        raising=False,
    )

    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait=None,
        trait_id=None,
        role=None,
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )

    assert payload["list"][0]["version"] == "3.1"


def test_fetch_cw_guide_payload_rejects_article_url(monkeypatch):
    guide_module = load_cw_guide_module()
    fetch_cw_guide_payload = getattr(guide_module, "fetch_cw_guide_payload", None)
    assert fetch_cw_guide_payload is not None

    monkeypatch.setattr(
        guide_module,
        "urlopen",
        lambda request, timeout=10: pytest.fail("lineup parser should reject article url before requesting api"),
        raising=False,
    )

    with pytest.raises(TrailError) as exc_info:
        fetch_cw_guide_payload("https://www.miyoushe.com/sr/article/70472857")

    assert exc_info.value.code == "GUIDE_URL_INVALID"


def test_fetch_cw_guide_payload_classifies_lineup_api_error_shape(monkeypatch):
    guide_module = load_cw_guide_module()
    fetch_cw_guide_payload = getattr(guide_module, "fetch_cw_guide_payload", None)
    assert fetch_cw_guide_payload is not None

    monkeypatch.setattr(
        guide_module,
        "urlopen",
        lambda request, timeout=10: FakeHttpResponse({"retcode": 1, "message": "lineup not found", "data": None}),
        raising=False,
    )

    with pytest.raises(TrailError) as exc_info:
        fetch_cw_guide_payload(fake_lineup_url())

    assert exc_info.value.code == "GUIDE_FETCH_FAILED"
    assert str(exc_info.value) == "guide fetch failed: lineup not found"


def test_fetch_cw_guide_returns_payload_without_artifact_store():
    guide_module = load_cw_guide_module()
    fetch_cw_guide = getattr(guide_module, "fetch_cw_guide", None)
    assert fetch_cw_guide is not None

    payload = fetch_cw_guide("https://example.invalid/cw", fetcher=fake_fetcher)

    assert payload == {
        "scene": "cw",
        "kind": "guide",
        "share_code": "##demo##",
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
        "priority": {},
        "positioning": {},
    }


def test_resolve_guide_input_supports_id_path_and_inline_artifact(tmp_path):
    guide_module = load_cw_guide_module()
    resolve_guide_input = getattr(guide_module, "resolve_guide_input", None)
    assert resolve_guide_input is not None

    store = ArtifactStore(tmp_path)
    meta = store.create(scene="cw", kind="guide", payload={"share_code": "##abc##"})

    assert resolve_guide_input(meta.artifact_id, artifact_store=store)["share_code"] == "##abc##"
    assert resolve_guide_input(str(meta.path), artifact_store=store)["share_code"] == "##abc##"


@pytest.mark.parametrize(
    ("scene", "kind", "code"),
    [
        ("ocr", "guide", "GUIDE_SCENE_MISMATCH"),
        ("cw", "dump", "GUIDE_KIND_MISMATCH"),
    ],
)
def test_resolve_guide_input_rejects_non_cw_guide_artifact(tmp_path, scene, kind, code):
    guide_module = load_cw_guide_module()
    resolve_guide_input = getattr(guide_module, "resolve_guide_input", None)
    assert resolve_guide_input is not None

    store = ArtifactStore(tmp_path)
    meta = store.create(scene=scene, kind=kind, payload={"share_code": "##abc##"})

    with pytest.raises(TrailError) as exc_info:
        resolve_guide_input(meta.artifact_id, artifact_store=store)

    assert exc_info.value.code == code


@pytest.mark.parametrize(
    ("scene", "kind", "code"),
    [
        ("ocr", "guide", "GUIDE_SCENE_MISMATCH"),
        ("cw", "dump", "GUIDE_KIND_MISMATCH"),
    ],
)
def test_apply_cw_guide_rejects_non_cw_guide_payload(tmp_path, scene, kind, code):
    guide_module = load_cw_guide_module()
    apply_cw_guide = getattr(guide_module, "apply_cw_guide", None)
    assert apply_cw_guide is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    with pytest.raises(TrailError) as exc_info:
        apply_cw_guide(session, guide_data={**fake_guide(), "scene": scene, "kind": kind})

    assert exc_info.value.code == code


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


def _complete_selected_guide(**overrides: object) -> dict[str, object]:
    guide: dict[str, object] = {
        "scene": "cw",
        "kind": "guide",
        "lineup_id": "selected-lineup",
        "title": "7群攻2银河学者",
        "share_code": "##demo##",
        "version": "4.0",
        "operation_guide": "前期按测试运营",
        "role_stages": [
            {
                "stage": "Opening",
                "front_roles": [{"name": "希儿"}],
                "back_roles": [{"name": "佩拉"}],
                "traits": [],
            }
        ],
        "first_fight_augments": [{"name": "快攻"}],
        "second_fight_augments": [{"name": "回蓝"}],
        "order_basic": [{"name": "钻头"}],
        "order_compose": [{"name": "风暴"}],
    }
    guide.update(overrides)
    return guide


def _complete_selected_constraints() -> dict[str, object]:
    return {"min_coins": 40, "min_level": 7, "mid_level": 9, "priority": {}, "positioning": {}}


def test_cw_guide_apply_mutation_uses_selected_guide_and_invalidates_runtime_state(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": _complete_selected_guide(),
        "constraints": _complete_selected_constraints(),
        "slots": {"stale": False, "hand": ["银狼"]},
        "sell_plan": {"candidates": [0]},
        "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
        "stage": {"value": "shop", "stale": False},
    }
    service.save_session(loaded)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-guide-apply-1",
        method="cw.guide.apply",
        payload={},
    )
    status = service.request_status("req-cw-guide-apply-1")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is True
    assert applied_share_codes == ["##demo##"]
    assert status["final_state"] == "completed"
    assert envelope["data"]["lineup_id"] == "selected-lineup"
    assert "artifact" not in persisted.scene_state["cw"]["guide"]
    assert "artifact_id" not in persisted.scene_state["cw"]["guide"]
    assert persisted.scene_state["cw"]["sell_plan"] == {}
    assert persisted.scene_state["cw"]["slots"]["stale"] is True
    assert persisted.scene_state["cw"]["shop"]["stale"] is True
    assert persisted.scene_state["cw"]["stage"]["stale"] is True


def test_cw_guide_apply_service_requires_selected_guide_in_session(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.apply",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
    )

    assert exc_info.value.code == "CW_GUIDE_STATE_INVALID"
    assert "guide.fetch.cw --select" in str(exc_info.value)
    assert "guide.fetch.cw --select" in str(exc_info.value)


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
def test_cw_guide_apply_service_rejects_invalid_selected_guide_share_code(tmp_path: Path, guide_state: dict[str, object]):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = dict(guide_state)
    service.save_session(loaded)

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.apply",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    assert exc_info.value.code == "CW_GUIDE_STATE_INVALID"


@pytest.mark.parametrize(
    ("guide_state", "expected_code"),
    [
        (None, "CW_GUIDE_STATE_INVALID"),
        ({"artifact": "selected-artifact", "lineup_id": "selected-lineup", "share_code": "demo"}, "CW_GUIDE_STATE_INVALID"),
    ],
    ids=["missing-selected-guide", "invalid-selected-guide"],
)
def test_cw_guide_apply_service_does_not_recover_selected_guide_from_latest_fetch_artifact(
    tmp_path: Path,
    monkeypatch,
    guide_state: dict[str, object] | None,
    expected_code: str,
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, command_service
    loaded = service.load_session(session.session_id)
    if guide_state is not None:
        loaded.scene_state.setdefault("cw", {})["guide"] = dict(guide_state)
    service.save_session(loaded)
    artifact = ArtifactStore(tmp_path / ".trail" / "artifacts").create(
        scene="cw",
        kind="guide",
        payload={
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": "preview-only",
            "share_code": "##preview##",
            "recovery_origin": "guide.fetch.cw",
        },
    )
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.apply",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    persisted = service.load_session(session.session_id)
    assert artifact.artifact_id
    assert exc_info.value.code == expected_code
    assert applied_share_codes == []
    if guide_state is None:
        assert persisted.scene_state.get("cw", {}).get("guide") is None
    else:
        assert persisted.scene_state["cw"]["guide"] == guide_state


def test_cw_guide_apply_keeps_pre_side_effect_failure_as_normal_failure(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": _complete_selected_guide(),
        "constraints": _complete_selected_constraints(),
        "slots": {"stale": False, "hand": ["银狼"]},
        "sell_plan": {"candidates": [0]},
        "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
        "stage": {"value": "shop", "stale": False},
    }
    service.save_session(loaded)
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: (_ for _ in ()).throw(TrailError("GUIDE_UI_NOT_READY", "guide ui not ready")),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-guide-apply-pre-side-effect-fail",
        method="cw.guide.apply",
        payload={},
    )
    status = service.request_status("req-cw-guide-apply-pre-side-effect-fail")
    persisted = service.load_session(session.session_id)

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "GUIDE_UI_NOT_READY",
        "message": "guide ui not ready",
    }
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False
    assert persisted.scene_state["cw"]["shop"]["stale"] is False


@pytest.mark.parametrize("legacy_field", ["lineup_id", "guide", "artifact", "artifact_id"])
def test_cw_guide_apply_direct_rpc_rejects_legacy_payload(tmp_path: Path, monkeypatch, legacy_field: str):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=f"req-cw-guide-apply-legacy-{legacy_field}",
        method="cw.guide.apply",
        payload={legacy_field: "abc"},
    )
    status = service.request_status(f"req-cw-guide-apply-legacy-{legacy_field}")

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "CW_GUIDE_APPLY_ARGS_NOT_SUPPORTED",
        "message": "cw guide.apply no longer accepts lineup_id/guide; use guide.fetch.cw --select",
    }
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False
    assert applied_share_codes == []


def test_cw_guide_apply_marks_applied_but_not_persisted_when_save_fails_after_ui_side_effect(
    tmp_path: Path,
    monkeypatch,
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": _complete_selected_guide(),
        "constraints": _complete_selected_constraints(),
        "slots": {"stale": False, "hand": ["银狼"]},
        "sell_plan": {"candidates": [0]},
        "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
        "stage": {"value": "shop", "stale": False},
    }
    service.save_session(loaded)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )
    original_save_session = service.save_session

    def fail_save_session(model):
        raise OSError("disk full")

    monkeypatch.setattr(service, "save_session", fail_save_session)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-guide-apply-save-fail",
        method="cw.guide.apply",
        payload={},
    )

    monkeypatch.setattr(service, "save_session", original_save_session)

    status = service.request_status("req-cw-guide-apply-save-fail")
    persisted = service.load_session(session.session_id)
    artifacts = list((tmp_path / ".trail" / "artifacts").glob("*.json"))

    assert applied_share_codes == ["##demo##"]
    assert artifacts == []
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert persisted.scene_state["cw"]["guide"]["lineup_id"] == "selected-lineup"
    assert persisted.scene_state["cw"]["shop"]["stale"] is False


def test_cw_guide_apply_marks_persisted_but_response_unknown_when_response_build_fails(
    tmp_path: Path,
    monkeypatch,
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": _complete_selected_guide(),
        "constraints": _complete_selected_constraints(),
        "slots": {"stale": False, "hand": ["银狼"]},
        "sell_plan": {"candidates": [0]},
        "shop": {"opened": True, "stale": False, "items": [{"name": "希儿"}]},
        "stage": {"value": "shop", "stale": False},
    }
    service.save_session(loaded)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )
    monkeypatch.setattr(
        command_service,
        "_response_with_request_id",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("response build failed")),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-guide-apply-response-fail",
        method="cw.guide.apply",
        payload={},
    )
    status = service.request_status("req-cw-guide-apply-response-fail")
    persisted = service.load_session(session.session_id)

    assert applied_share_codes == ["##demo##"]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert envelope["debug"]["last_known_stage"] == "state_persisted"
    assert status["final_state"] == "persisted_but_response_unknown"
    assert status["tainted"] is True
    assert persisted.scene_state["cw"]["guide"]["lineup_id"] == "selected-lineup"
    assert persisted.scene_state["cw"]["shop"]["stale"] is True


def test_cw_guide_current_service_reads_applied_guide_from_session(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_selected_guide(lineup_id="69c9014f24546dfbd2b26227")
    loaded.scene_state["cw"]["constraints"] = _complete_selected_constraints()
    service.save_session(loaded)

    payload = cw_service.handle(
        method="cw.guide.current",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert payload["lineup_id"] == "69c9014f24546dfbd2b26227"
    assert payload["share_code"] == "##demo##"
    assert payload["operation_guide"] == "前期按测试运营"
    assert "artifact" not in payload
    assert "artifact_id" not in payload


def test_cw_guide_current_service_requires_selected_guide_in_session(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.current",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    assert exc_info.value.code == "CW_GUIDE_STATE_INVALID"
    assert "guide.fetch.cw --select" in str(exc_info.value)


def test_cw_guide_current_service_handles_non_mapping_cw_state_with_stable_error(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = "corrupted"
    service.save_session(loaded)

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.current",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    assert exc_info.value.code == "CW_GUIDE_STATE_INVALID"
    assert "guide.fetch.cw --select" in str(exc_info.value)


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
def test_cw_guide_current_service_rejects_invalid_selected_guide_share_code_without_artifact_fallback(
    tmp_path: Path,
    guide_state: dict[str, object],
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = dict(guide_state)
    service.save_session(loaded)
    artifact = ArtifactStore(tmp_path / ".trail" / "artifacts").create(
        scene="cw",
        kind="guide",
        payload={
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": "preview-only",
            "share_code": "##preview##",
            "recovery_origin": "guide.fetch.cw",
        },
    )

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.current",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    persisted = service.load_session(session.session_id)
    assert artifact.artifact_id
    assert exc_info.value.code == "CW_GUIDE_STATE_INVALID"
    assert "guide.fetch.cw --select" in str(exc_info.value)
    assert persisted.scene_state["cw"]["guide"] == guide_state


def test_cw_guide_current_service_does_not_recover_guide_from_latest_fetch_artifact(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    artifact = store.create(
        scene="cw",
        kind="guide",
        payload={
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": "69c9014f24546dfbd2b26227",
            "title": "7群攻2银河学者",
            "recovery_origin": "guide.fetch.cw",
        },
    )

    persisted = service.load_session(session.session_id)

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.current",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
    )

    assert artifact.artifact_id
    assert exc_info.value.code == "CW_GUIDE_STATE_INVALID"
    assert not isinstance(persisted.scene_state.get("cw"), dict) or persisted.scene_state["cw"].get("guide") is None


def test_cw_shop_status_does_not_recover_guide_summary_from_plain_fetch_artifact_when_session_guide_missing(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": None,
        "constraints": {},
        "slots": {"stale": False, "hand": ["银狼"]},
        "sell_plan": {"candidates": [0]},
        "shop": {
            "opened": True,
            "stale": False,
            "items": [{"name": "希儿", "price": 3}],
            "coins": 15,
            "level": 4,
            "exp": "4/52",
            "reserve_full": False,
            "team_size": "6/6",
        },
        "stage": {"stale": False, "name": "shop"},
        "metrics": {},
    }
    service.save_session(loaded)
    store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    artifact = store.create(
        scene="cw",
        kind="guide",
        payload={
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": "69c9014f24546dfbd2b26227",
            "recovery_origin": "guide.fetch.cw",
        },
    )

    payload = cw_service.handle(
        method="cw.shop.status",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )
    persisted = service.load_session(session.session_id)

    assert payload == {
        "opened": True,
        "stale": False,
        "items": [{"name": "希儿", "price": 3}],
        "coins": 15,
        "reserve_full": False,
        "stage_status_stale": True,
    }
    assert artifact.artifact_id
    assert persisted.scene_state["cw"]["guide"] is None
    assert persisted.scene_state["cw"]["shop"] == {
        "opened": True,
        "stale": False,
        "items": [{"name": "希儿", "price": 3}],
        "coins": 15,
        "reserve_full": False,
    }
    assert persisted.scene_state["cw"]["slots"] == {"stale": False, "hand": ["银狼"]}
    assert persisted.scene_state["cw"]["stage"] == {"stale": False, "name": "shop"}
    assert persisted.scene_state["cw"]["sell_plan"] == {"candidates": [0]}


def test_cw_shop_status_hides_stale_shop_guide_summary_when_selected_guide_missing(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": None,
        "constraints": {},
        "slots": {"stale": False, "hand": ["银狼"]},
        "sell_plan": {"candidates": [0]},
        "shop": {
            "opened": True,
            "stale": False,
            "items": [{"name": "希儿", "price": 3}],
            "coins": 15,
            "level": 4,
            "exp": "4/52",
            "reserve_full": False,
            "team_size": "6/6",
            "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 9}},
        },
        "stage": {"stale": False, "name": "shop"},
        "metrics": {},
    }
    service.save_session(loaded)

    payload = cw_service.handle(
        method="cw.shop.status",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )
    persisted = service.load_session(session.session_id)

    assert payload == {
        "opened": True,
        "stale": False,
        "items": [{"name": "希儿", "price": 3}],
        "coins": 15,
        "reserve_full": False,
        "stage_status_stale": True,
    }
    assert persisted.scene_state["cw"]["shop"]["guide_summary"] == {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 9}}


def test_cw_shop_status_handles_non_mapping_cw_state_without_leaking_guide_summary(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = "corrupted"
    service.save_session(loaded)

    payload = cw_service.handle(
        method="cw.shop.status",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )
    persisted = service.load_session(session.session_id)

    assert payload == {"stale": True, "stage_status_stale": True}
    assert "guide_summary" not in payload
    assert persisted.scene_state["cw"]["shop"] == {"stale": True}


def test_cw_guide_current_service_does_not_recover_from_any_artifact_origin(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    older_fetch = store.create(
        scene="cw",
        kind="guide",
        payload={
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": "fetch-old",
            "share_code": "##fetch-old##",
            "title": "旧 fetch 攻略",
            "created_at": "2026-04-20T10:00:00+00:00",
            "recovery_origin": "guide.fetch.cw",
        },
    )
    latest_fetch = store.create(
        scene="cw",
        kind="guide",
        payload={
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": "fetch-new",
            "share_code": "##fetch-new##",
            "title": "新 fetch 攻略",
            "created_at": "2026-04-20T11:00:00+00:00",
            "recovery_origin": "guide.fetch.cw",
        },
    )
    store.create(
        scene="cw",
        kind="guide",
        payload={
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": "preview-newest",
            "share_code": "##preview-newest##",
            "title": "最新但不可恢复",
            "created_at": "2026-04-20T12:00:00+00:00",
        },
    )

    persisted = service.load_session(session.session_id)

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.current",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    assert older_fetch.artifact_id != latest_fetch.artifact_id
    assert exc_info.value.code == "CW_GUIDE_STATE_INVALID"
    assert not isinstance(persisted.scene_state.get("cw"), dict) or persisted.scene_state["cw"].get("guide") is None


def test_cw_guide_current_service_does_not_recover_from_shop_summary_without_selection(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = {
        "guide": None,
        "constraints": {},
        "slots": {"stale": False},
        "sell_plan": {},
        "shop": {
            "opened": True,
            "stale": False,
            "items": [{"name": "希儿", "price": 3}],
            "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 9}},
        },
        "stage": {"stale": False, "name": "shop"},
        "metrics": {},
    }
    service.save_session(loaded)
    store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    artifact = store.create(
        scene="cw",
        kind="guide",
        payload={
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": "69c9014f24546dfbd2b26227",
            "recovery_origin": "guide.fetch.cw",
        },
    )

    persisted = service.load_session(session.session_id)

    with pytest.raises(TrailError) as exc_info:
        cw_service.handle(
            method="cw.guide.current",
            payload={"session_id": session.session_id},
            workspace_root=str(tmp_path),
            session_service=service,
        )

    assert artifact.artifact_id
    assert exc_info.value.code == "CW_GUIDE_STATE_INVALID"
    assert persisted.scene_state["cw"]["guide"] is None
    assert persisted.scene_state["cw"]["shop"]["guide_summary"] == {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 9}}


def test_apply_cw_guide_via_ui_waits_for_apply_button_to_settle_before_exit(monkeypatch):
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None
    monkeypatch.setattr(guide_module, "sleep", lambda seconds: None)

    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    confirm_template = str((CW_ASSET_ROOT / "ensure2.png").resolve())
    wait_calls: list[str] = []
    locate_calls: list[str] = []
    texts: list[str] = []
    keys: list[tuple[str, int, float]] = []
    ocr_calls: list[dict[str, object]] = []

    def apply_locate_results() -> list[Box | None]:
        stable_checks = max(1, math.ceil(guide_module.GUIDE_APPLY_READY_DELAY / guide_module.GUIDE_APPLY_SETTLE_INTERVAL))
        apply_box = Box(left=10, top=20, width=40, height=20, source=apply_template)
        return [apply_box] * stable_checks + [None]

    class RuntimeStub:
        def __init__(self):
            self._locate_results = apply_locate_results()

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            wait_calls.append(template)
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            return None

        def type_text(self, text: str):
            texts.append(text)

        def ocr(self, **kwargs):
            ocr_calls.append(dict(kwargs))
            return [{"text": "输入攻略码", "box": {"left": 100, "top": 200, "width": 60, "height": 20}}]

        def locate(self, template: str, **kwargs):
            locate_calls.append(template)
            if template != apply_template:
                return None
            if self._locate_results:
                return self._locate_results.pop(0)
            return None

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            keys.append((key, presses, interval))

    apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    assert confirm_template in wait_calls
    assert texts == ["##demo##"]
    assert locate_calls == [apply_template] * (len(apply_locate_results()))
    assert keys == [("esc", 3, 1)]
    assert ocr_calls


def _exercise_apply_cw_guide_confirm_settle(guide_module, monkeypatch) -> dict[str, object]:
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None
    assert guide_module.GUIDE_CONFIRM_SETTLE_DELAY > 0, "GUIDE_CONFIRM_SETTLE_DELAY must stay > 0"

    confirm_template = str((CW_ASSET_ROOT / "ensure2.png").resolve())
    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    sleep_calls: list[float] = []
    events: list[object] = []
    state = {"confirm_clicked": False, "confirm_settled": False}

    def fake_sleep(seconds: float):
        sleep_calls.append(seconds)
        events.append(("sleep", seconds))
        if state["confirm_clicked"] and seconds == guide_module.GUIDE_CONFIRM_SETTLE_DELAY:
            state["confirm_settled"] = True

    monkeypatch.setattr(guide_module, "sleep", fake_sleep)

    confirm_box = Box(left=100, top=20, width=40, height=20, source=confirm_template)
    apply_box = Box(left=200, top=20, width=40, height=20, source=apply_template)

    class RuntimeStub:
        def __init__(self):
            self._apply_locate_results = _guide_apply_ready_locate_results(guide_module, apply_box)

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            if template == confirm_template:
                return confirm_box
            if template == apply_template:
                events.append("wait_apply")
                assert state["confirm_settled"], "guide.apply queried before confirm settle"
                return apply_box
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            if (x, y) == confirm_box.center:
                state["confirm_clicked"] = True
                events.append("confirm_click")

        def type_text(self, text: str):
            return None

        def ocr(self, **kwargs):
            return [{"text": "输入攻略码", "box": {"left": 100, "top": 200, "width": 60, "height": 20}}]

        def locate(self, template: str, **kwargs):
            if template == apply_template:
                if self._apply_locate_results:
                    return self._apply_locate_results.pop(0)
                return None
            return None

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            return None

    apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    return {"sleep_calls": sleep_calls, "events": events}


def _guide_apply_ready_locate_results(guide_module, apply_box: Box) -> list[Box | None]:
    stable_checks = max(1, math.ceil(guide_module.GUIDE_APPLY_READY_DELAY / guide_module.GUIDE_APPLY_SETTLE_INTERVAL))
    return [apply_box] * stable_checks + [None]


def test_apply_cw_guide_via_ui_waits_for_confirm_result_to_settle_before_clicking_apply(monkeypatch):
    guide_module = load_cw_guide_module()
    result = _exercise_apply_cw_guide_confirm_settle(guide_module, monkeypatch)

    assert result["sleep_calls"][:3] == [
        guide_module.GUIDE_INPUT_FOCUS_DELAY,
        guide_module.GUIDE_TEXT_SETTLE_DELAY,
        guide_module.GUIDE_CONFIRM_SETTLE_DELAY,
    ]
    confirm_click_index = result["events"].index("confirm_click")
    assert result["events"][confirm_click_index : confirm_click_index + 3] == [
        "confirm_click",
        ("sleep", guide_module.GUIDE_CONFIRM_SETTLE_DELAY),
        "wait_apply",
    ]


def test_apply_cw_guide_via_ui_confirm_settle_test_rejects_zero_delay(monkeypatch):
    guide_module = load_cw_guide_module()
    monkeypatch.setattr(guide_module, "GUIDE_CONFIRM_SETTLE_DELAY", 0.0)

    with pytest.raises(AssertionError, match="GUIDE_CONFIRM_SETTLE_DELAY"):
        _exercise_apply_cw_guide_confirm_settle(guide_module, monkeypatch)


def _exercise_apply_cw_guide_apply_ready_settle(
    guide_module,
    monkeypatch,
    *,
    shift_apply_box: bool = False,
    drop_apply_once: bool = False,
    drop_apply_after_checks: int = 0,
) -> dict[str, object]:
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None
    assert guide_module.GUIDE_APPLY_READY_DELAY > 0, "GUIDE_APPLY_READY_DELAY must stay > 0"
    expected_stable_checks = max(
        1,
        math.ceil(guide_module.GUIDE_APPLY_READY_DELAY / guide_module.GUIDE_APPLY_SETTLE_INTERVAL),
    )

    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    sleep_calls: list[float] = []
    events: list[object] = []
    state = {"apply_visible": False, "stable_checks": 0, "apply_clicked": False}

    def fake_sleep(seconds: float):
        sleep_calls.append(seconds)
        events.append(("sleep", seconds))

    monkeypatch.setattr(guide_module, "sleep", fake_sleep)

    initial_apply_box = Box(left=200, top=20, width=40, height=20, source=apply_template)
    shifted_apply_box = Box(left=260, top=20, width=40, height=20, source=apply_template)

    class RuntimeStub:
        def __init__(self):
            self._current_apply_box = initial_apply_box
            self._stable_checks_remaining = expected_stable_checks
            self._apply_dropped = False

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            if template == apply_template:
                state["apply_visible"] = True
                events.append("wait_apply")
                return self._current_apply_box
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            if (x, y) == self._current_apply_box.center:
                assert state["stable_checks"] >= expected_stable_checks, "guide.apply clicked before guide load settle"
                state["apply_clicked"] = True
                events.append("apply_click")

        def type_text(self, text: str):
            return None

        def ocr(self, **kwargs):
            return [{"text": "输入攻略码", "box": {"left": 100, "top": 200, "width": 60, "height": 20}}]

        def locate(self, template: str, **kwargs):
            if template == apply_template:
                if drop_apply_once and not self._apply_dropped and state["stable_checks"] >= drop_apply_after_checks:
                    self._apply_dropped = True
                    state["stable_checks"] = 0
                    self._stable_checks_remaining = expected_stable_checks + 1
                    events.append("apply_missing")
                    return None
                if shift_apply_box and self._current_apply_box is initial_apply_box:
                    self._current_apply_box = shifted_apply_box
                    state["stable_checks"] = 0
                    self._stable_checks_remaining = expected_stable_checks
                    events.append("apply_shift")
                    return self._current_apply_box
                if self._stable_checks_remaining > 0:
                    self._stable_checks_remaining -= 1
                    state["stable_checks"] += 1
                    events.append(("apply_locate", self._current_apply_box.center))
                    return self._current_apply_box
                return None
            return None

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            return None

    apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    return {
        "expected_stable_checks": expected_stable_checks,
        "sleep_calls": sleep_calls,
        "events": events,
        "final_apply_center": shifted_apply_box.center if shift_apply_box else initial_apply_box.center,
    }


def test_apply_cw_guide_via_ui_waits_for_guide_load_settle_before_clicking_apply(monkeypatch):
    guide_module = load_cw_guide_module()
    result = _exercise_apply_cw_guide_apply_ready_settle(guide_module, monkeypatch)

    locate_events_before_click = []
    post_wait_sleep_events = []
    wait_apply_seen = False
    for event in result["events"]:
        if event == "wait_apply":
            wait_apply_seen = True
            continue
        if event == "apply_click":
            break
        if isinstance(event, tuple) and event[:1] == ("apply_locate",):
            locate_events_before_click.append(event)
        if wait_apply_seen and event == ("sleep", guide_module.GUIDE_APPLY_SETTLE_INTERVAL):
            post_wait_sleep_events.append(event)

    assert len(locate_events_before_click) == result["expected_stable_checks"]
    assert len(post_wait_sleep_events) == result["expected_stable_checks"]


def test_apply_cw_guide_via_ui_relocates_apply_button_after_settle_shift(monkeypatch):
    guide_module = load_cw_guide_module()
    result = _exercise_apply_cw_guide_apply_ready_settle(guide_module, monkeypatch, shift_apply_box=True)

    assert "apply_shift" in result["events"]
    assert ("apply_locate", result["final_apply_center"]) in result["events"]
    assert result["events"].index("apply_shift") < result["events"].index("apply_click")


def test_apply_cw_guide_via_ui_tolerates_transient_apply_miss_during_settle(monkeypatch):
    guide_module = load_cw_guide_module()
    result = _exercise_apply_cw_guide_apply_ready_settle(guide_module, monkeypatch, drop_apply_once=True)

    assert "apply_missing" in result["events"]
    assert result["events"].index("apply_missing") < result["events"].index("apply_click")


def test_apply_cw_guide_via_ui_restarts_stability_window_after_apply_miss(monkeypatch):
    guide_module = load_cw_guide_module()
    result = _exercise_apply_cw_guide_apply_ready_settle(
        guide_module,
        monkeypatch,
        drop_apply_once=True,
        drop_apply_after_checks=2,
    )

    locate_events_after_missing = []
    missing_seen = False
    for event in result["events"]:
        if event == "apply_missing":
            missing_seen = True
            continue
        if event == "apply_click":
            break
        if missing_seen and isinstance(event, tuple) and event[:1] == ("apply_locate",):
            locate_events_after_missing.append(event)

    assert len(locate_events_after_missing) == result["expected_stable_checks"] + 1


def test_apply_cw_guide_via_ui_fails_when_apply_button_never_stabilizes(monkeypatch):
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None
    monkeypatch.setattr(guide_module, "GUIDE_UI_WAIT_TIMEOUT", 0.02)
    monkeypatch.setattr(guide_module, "sleep", lambda seconds: None)

    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    first_box = Box(left=200, top=20, width=40, height=20, source=apply_template)
    second_box = Box(left=260, top=20, width=40, height=20, source=apply_template)

    class RuntimeStub:
        def __init__(self):
            self._toggle = False

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            if template == apply_template:
                return first_box
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            return None

        def type_text(self, text: str):
            return None

        def ocr(self, **kwargs):
            return [{"text": "输入攻略码", "box": {"left": 100, "top": 200, "width": 60, "height": 20}}]

        def locate(self, template: str, **kwargs):
            if template == apply_template:
                self._toggle = not self._toggle
                return first_box if self._toggle else second_box
            return Box(left=10, top=20, width=40, height=20, source=template)

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            return None

    with pytest.raises(TrailError) as exc_info:
        apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    assert exc_info.value.code == "GUIDE_UI_NOT_FOUND"


def test_apply_cw_guide_via_ui_apply_ready_test_rejects_zero_delay(monkeypatch):
    guide_module = load_cw_guide_module()
    monkeypatch.setattr(guide_module, "GUIDE_APPLY_READY_DELAY", 0.0)

    with pytest.raises(AssertionError, match="GUIDE_APPLY_READY_DELAY"):
        _exercise_apply_cw_guide_apply_ready_settle(guide_module, monkeypatch)


def _exercise_apply_cw_guide_post_apply_settle(guide_module, monkeypatch) -> dict[str, object]:
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None
    assert guide_module.GUIDE_POST_APPLY_SETTLE_DELAY > 0, "GUIDE_POST_APPLY_SETTLE_DELAY must stay > 0"

    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    sleep_calls: list[float] = []
    events: list[object] = []
    state = {"apply_cleared": False, "post_apply_settled": False}

    def fake_sleep(seconds: float):
        sleep_calls.append(seconds)
        events.append(("sleep", seconds))
        if state["apply_cleared"] and seconds == guide_module.GUIDE_POST_APPLY_SETTLE_DELAY:
            state["post_apply_settled"] = True

    monkeypatch.setattr(guide_module, "sleep", fake_sleep)

    class RuntimeStub:
        def __init__(self):
            apply_box = Box(left=10, top=20, width=40, height=20, source=apply_template)
            self._apply_locate_results = _guide_apply_ready_locate_results(guide_module, apply_box)

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            return None

        def type_text(self, text: str):
            return None

        def ocr(self, **kwargs):
            return [{"text": "输入攻略码", "box": {"left": 100, "top": 200, "width": 60, "height": 20}}]

        def locate(self, template: str, **kwargs):
            if template == apply_template:
                if self._apply_locate_results:
                    result = self._apply_locate_results.pop(0)
                    if result is None:
                        state["apply_cleared"] = True
                        events.append("apply_cleared")
                    return result
                state["apply_cleared"] = True
                events.append("apply_cleared")
                return None
            return None

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            events.append(("press_key", key, presses, interval))
            assert state["post_apply_settled"], "guide exited before post-apply settle"

    apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    return {"sleep_calls": sleep_calls, "events": events}


def test_apply_cw_guide_via_ui_waits_for_post_apply_settle_before_exit(monkeypatch):
    guide_module = load_cw_guide_module()
    result = _exercise_apply_cw_guide_post_apply_settle(guide_module, monkeypatch)

    assert result["sleep_calls"][-1] == guide_module.GUIDE_POST_APPLY_SETTLE_DELAY
    apply_cleared_index = result["events"].index("apply_cleared")
    assert result["events"][apply_cleared_index : apply_cleared_index + 3] == [
        "apply_cleared",
        ("sleep", guide_module.GUIDE_POST_APPLY_SETTLE_DELAY),
        ("press_key", "esc", guide_module.GUIDE_ESC_PRESSES, guide_module.GUIDE_ESC_INTERVAL),
    ]


def test_apply_cw_guide_via_ui_post_apply_settle_test_rejects_zero_delay(monkeypatch):
    guide_module = load_cw_guide_module()
    monkeypatch.setattr(guide_module, "GUIDE_POST_APPLY_SETTLE_DELAY", 0.0)

    with pytest.raises(AssertionError, match="GUIDE_POST_APPLY_SETTLE_DELAY"):
        _exercise_apply_cw_guide_post_apply_settle(guide_module, monkeypatch)


def test_apply_cw_guide_via_ui_fails_when_apply_button_does_not_clear(monkeypatch):
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None
    monkeypatch.setattr(guide_module, "GUIDE_APPLY_SETTLE_TIMEOUT", 0.02)
    monkeypatch.setattr(guide_module, "sleep", lambda seconds: None)

    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    keys: list[tuple[str, int, float]] = []
    texts: list[str] = []

    class RuntimeStub:
        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            return None

        def type_text(self, text: str):
            texts.append(text)

        def ocr(self, **kwargs):
            return [{"text": "输入攻略码", "box": {"left": 100, "top": 200, "width": 60, "height": 20}}]

        def locate(self, template: str, **kwargs):
            if template == apply_template:
                return Box(left=10, top=20, width=40, height=20, source=template)
            return None

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            keys.append((key, presses, interval))

    with pytest.raises(TrailError) as exc_info:
        apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    assert exc_info.value.code == "GUIDE_APPLY_NOT_CONFIRMED"
    assert texts == ["##demo##"]
    assert keys == []


def test_apply_cw_guide_via_ui_skips_strategy_click_when_guide_page_already_open(monkeypatch):
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None
    monkeypatch.setattr(guide_module, "sleep", lambda seconds: None)

    strategy_template = str((CW_ASSET_ROOT / "strategy.png").resolve())
    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    wait_calls: list[str] = []
    locate_calls: list[str] = []
    clicks: list[tuple[float, float]] = []
    ocr_calls: list[dict[str, object]] = []

    class RuntimeStub:
        def __init__(self):
            apply_box = Box(left=10, top=20, width=40, height=20, source=apply_template)
            self._apply_locate_results = _guide_apply_ready_locate_results(guide_module, apply_box)

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            wait_calls.append(template)
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            clicks.append((x, y))

        def type_text(self, text: str):
            return None

        def ocr(self, **kwargs):
            ocr_calls.append(dict(kwargs))
            return [{"text": "输入攻略码", "box": {"left": 100, "top": 200, "width": 60, "height": 20}}]

        def locate(self, template: str, **kwargs):
            locate_calls.append(template)
            if template == apply_template:
                if self._apply_locate_results:
                    return self._apply_locate_results.pop(0)
                return None
            if template == strategy_template:
                return None
            return None

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            return None

    apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    assert strategy_template not in locate_calls
    assert strategy_template not in wait_calls
    assert ocr_calls
    assert clicks[0] != (30, 30)


def test_apply_cw_guide_via_ui_uses_ocr_for_enter_code_button_when_template_missing(monkeypatch):
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None

    strategy_template = str((CW_ASSET_ROOT / "strategy.png").resolve())
    confirm_template = str((CW_ASSET_ROOT / "ensure2.png").resolve())
    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    apply_box = Box(left=200, top=20, width=40, height=20, source=apply_template)
    clicks: list[tuple[float, float]] = []
    texts: list[str] = []
    keys: list[tuple[str, int, float]] = []
    ocr_calls: list[dict[str, object]] = []

    monkeypatch.setattr(guide_module, "sleep", lambda seconds: None)

    class RuntimeStub:
        def __init__(self):
            self._apply_locate_results = _guide_apply_ready_locate_results(guide_module, apply_box)
            self._ocr_round = 0

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            if template == strategy_template:
                return Box(left=10, top=20, width=40, height=20, source=template)
            if template == confirm_template:
                return Box(left=60, top=20, width=40, height=20, source=template)
            if template == apply_template:
                return apply_box
            return Box(left=10, top=20, width=40, height=20, source=template)

        def locate(self, template: str, **kwargs):
            if template == apply_template:
                if self._apply_locate_results:
                    return self._apply_locate_results.pop(0)
                return None
            return None

        def ocr(self, **kwargs):
            ocr_calls.append(dict(kwargs))
            self._ocr_round += 1
            if self._ocr_round == 1:
                return []
            return [
                {
                    "text": "输入攻略码",
                    "box": {"left": 100, "top": 200, "width": 60, "height": 20},
                }
            ]

        def click_point(self, x: float, y: float, **kwargs):
            clicks.append((x, y))

        def type_text(self, text: str):
            texts.append(text)

        def ocr(self, **kwargs):
            ocr_calls.append(dict(kwargs))
            return [{"text": "输入攻略码", "box": {"left": 100, "top": 200, "width": 60, "height": 20}}]

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            keys.append((key, presses, interval))

    apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    assert len(clicks) >= 2
    assert texts == ["##demo##"]
    assert keys == [("esc", 3, 1)]
    assert ocr_calls, "expected OCR fallback for enter strategy code button"


def test_apply_cw_guide_via_ui_uses_ocr_tuple_piece_for_enter_code_button(monkeypatch):
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None

    strategy_template = str((CW_ASSET_ROOT / "strategy.png").resolve())
    confirm_template = str((CW_ASSET_ROOT / "ensure2.png").resolve())
    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    apply_box = Box(left=200, top=20, width=40, height=20, source=apply_template)
    clicks: list[tuple[float, float]] = []

    monkeypatch.setattr(guide_module, "sleep", lambda seconds: None)

    class RuntimeStub:
        def __init__(self):
            self._apply_locate_results = _guide_apply_ready_locate_results(guide_module, apply_box)

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            if template == strategy_template:
                return Box(left=10, top=20, width=40, height=20, source=template)
            if template == confirm_template:
                return Box(left=60, top=20, width=40, height=20, source=template)
            if template == apply_template:
                return apply_box
            return Box(left=10, top=20, width=40, height=20, source=template)

        def locate(self, template: str, **kwargs):
            if template == apply_template:
                if self._apply_locate_results:
                    return self._apply_locate_results.pop(0)
                return None
            return None

        def ocr(self, **kwargs):
            return [[[[100, 200], [160, 200], [160, 220], [100, 220]], "输入攻略码", 0.99]]

        def click_point(self, x: float, y: float, **kwargs):
            clicks.append((x, y))

        def type_text(self, text: str):
            return None

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            return None

    apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    assert len(clicks) >= 2


@pytest.mark.parametrize("field", ["on_field", "off_field", "priority", "positioning"])
def test_apply_cw_guide_rejects_non_mapping_payload_fields(tmp_path, field):
    guide_module = load_cw_guide_module()
    apply_cw_guide = getattr(guide_module, "apply_cw_guide", None)
    assert apply_cw_guide is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    with pytest.raises(TrailError) as exc_info:
        apply_cw_guide(session, guide_data={**fake_guide(), field: []})

    assert exc_info.value.code == "GUIDE_PAYLOAD_INVALID"
    assert field in str(exc_info.value)
