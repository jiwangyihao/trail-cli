from __future__ import annotations

import importlib
import json
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
        "share_code": "##demo##",
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
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
                "description": "9级搜牌",
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
                    "portals": [{"name": "购物区"}, {"name": "事件区"}],
                    "order_basic": [{"name": "抢前排输出"}, {"name": "补减防"}],
                    "order_compose": [{"name": "推进器"}],
                    "role_stages": [
                        {
                            "stage": "Opening",
                            "front_roles": [{"name": "黑塔", "star": 1, "rarity": 1, "is_carry": False}],
                            "back_roles": [{"name": "艾丝妲", "star": 1, "rarity": 1, "is_carry": False}],
                            "traits": [{"name": "智识"}],
                        },
                        {
                            "stage": "Final",
                            "front_roles": [{"name": "希儿", "star": 3, "rarity": 3, "is_carry": True}],
                            "back_roles": [{"name": "佩拉", "star": 2, "rarity": 2, "is_carry": False}],
                            "traits": [{"name": "巡猎"}, {"name": "量子"}],
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
        },
    }


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


def load_cw_guide_module():
    try:
        return importlib.import_module("trail.scenes.cw.guide")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.guide: {exc}")


def test_apply_guide_populates_cw_scene_state(tmp_path):
    guide_module = load_cw_guide_module()
    apply_cw_guide = getattr(guide_module, "apply_cw_guide", None)
    assert apply_cw_guide is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    refreshed = apply_cw_guide(session, guide_data=fake_guide())

    cw_state = refreshed.scene_state["cw"]
    assert "guide" in cw_state
    assert "constraints" in cw_state
    assert "slots" in cw_state
    assert cw_state["guide"]["artifact"] == "guide-demo"
    assert cw_state["guide"]["share_code"] == "##demo##"
    assert cw_state["guide"]["on_field"] == {"希儿": 9}
    assert cw_state["guide"]["off_field"] == {"佩拉": 3}
    assert cw_state["guide"]["remaining_purchases"] == {"希儿": 9, "佩拉": 3}
    assert cw_state["constraints"]["min_coins"] == 40
    assert cw_state["constraints"]["min_level"] == 7
    assert cw_state["constraints"]["mid_level"] == 9
    assert cw_state["slots"]["stale"] is True


def test_apply_guide_preserves_source_metadata_for_later_scene_steps(tmp_path):
    guide_module = load_cw_guide_module()
    apply_cw_guide = getattr(guide_module, "apply_cw_guide", None)
    assert apply_cw_guide is not None

    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    refreshed = apply_cw_guide(
        session,
        guide_data={
            **fake_guide(),
            "source_url": fake_lineup_url("70472857"),
            "lineup_id": "70472857",
            "title": "货币战争攻略码",
            "author": "测试作者",
            "uploader": "测试作者",
        },
    )

    assert refreshed.scene_state["cw"]["guide"] == {
        "artifact": "guide-demo",
        "lineup_id": "70472857",
        "share_code": "##demo##",
        "source_url": fake_lineup_url("70472857"),
        "title": "货币战争攻略码",
        "author": "测试作者",
        "uploader": "测试作者",
        "labels": [],
        "support_hard": False,
        "has_change_equip": False,
        "has_expert": False,
        "version": None,
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
        "role_stages": [],
        "first_fight_augments": [],
        "second_fight_augments": [],
        "portals": [],
        "order_basic": [],
        "order_compose": [],
        "remaining_purchases": {"希儿": 9, "佩拉": 3},
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
                "traits": ["智识"],
            },
            {
                "stage": "Final",
                "front_roles": [{"name": "希儿", "star": 3, "rarity": 3, "is_carry": True}],
                "back_roles": [{"name": "佩拉", "star": 2, "rarity": 2, "is_carry": False}],
                "traits": ["巡猎", "量子"],
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
    }


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
                "interact": {"like": 123, "favour": 45, "view": 6789, "use": 321},
                "recent_interact": {"like": 12, "favour": 4, "view": 345, "use": 22},
                "support_hard": True,
            }
        ],
        "next_page_token": "next-token-demo",
    }


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


def test_cw_guide_apply_mutation_persists_artifact_and_session_state(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide",
        lambda lineup_id, fetcher: {
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": lineup_id,
        },
    )
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
        payload={"lineup_id": "69c9014f24546dfbd2b26227"},
    )
    status = service.request_status("req-cw-guide-apply-1")
    persisted = service.load_session(session.session_id)
    artifact_id = envelope["data"]["artifact"]
    artifact_payload = json.loads((tmp_path / ".trail" / "artifacts" / f"{artifact_id}.json").read_text(encoding="utf-8"))

    assert envelope["ok"] is True
    assert applied_share_codes == ["##demo##"]
    assert status["final_state"] == "completed"
    assert artifact_payload["lineup_id"] == "69c9014f24546dfbd2b26227"
    assert persisted.scene_state["cw"]["guide"]["artifact"] == artifact_id
    assert persisted.scene_state["cw"]["shop"]["stale"] is True


def test_cw_guide_apply_marks_applied_but_not_persisted_when_save_fails_after_ui_side_effect(
    tmp_path: Path,
    monkeypatch,
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide",
        lambda lineup_id, fetcher: {
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": lineup_id,
        },
    )
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
        payload={"lineup_id": "69c9014f24546dfbd2b26227"},
    )

    monkeypatch.setattr(service, "save_session", original_save_session)

    status = service.request_status("req-cw-guide-apply-save-fail")
    persisted = service.load_session(session.session_id)
    artifacts = list((tmp_path / ".trail" / "artifacts").glob("*.json"))

    assert applied_share_codes == ["##demo##"]
    assert len(artifacts) == 1
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert persisted.scene_state.get("cw", {}).get("guide") is None


def test_cw_guide_apply_marks_persisted_but_response_unknown_when_response_build_fails(
    tmp_path: Path,
    monkeypatch,
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    applied_share_codes: list[str] = []
    monkeypatch.setattr(
        "trail.daemon.cw_service.fetch_cw_guide",
        lambda lineup_id, fetcher: {
            **fake_guide(),
            "artifact_id": None,
            "lineup_id": lineup_id,
        },
    )
    monkeypatch.setattr(
        "trail.daemon.cw_service.apply_cw_guide_via_ui",
        lambda runtime, share_code: applied_share_codes.append(share_code),
    )
    monkeypatch.setattr(
        "trail.daemon.command_service.success",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("response build failed")),
    )

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-guide-apply-response-fail",
        method="cw.guide.apply",
        payload={"lineup_id": "69c9014f24546dfbd2b26227"},
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
    assert persisted.scene_state["cw"]["guide"]["lineup_id"] == "69c9014f24546dfbd2b26227"


def test_cw_guide_current_service_reads_applied_guide_from_session(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {
        "artifact": "artifact-demo",
        "lineup_id": "69c9014f24546dfbd2b26227",
        "share_code": "##demo##",
        "title": "7群攻2银河学者",
    }
    service.save_session(loaded)

    payload = cw_service.handle(
        method="cw.guide.current",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert payload == {
        "artifact": "artifact-demo",
        "lineup_id": "69c9014f24546dfbd2b26227",
        "share_code": "##demo##",
        "title": "7群攻2银河学者",
    }


def test_apply_cw_guide_via_ui_waits_for_apply_button_to_settle_before_exit():
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None

    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    confirm_template = str((CW_ASSET_ROOT / "ensure2.png").resolve())
    wait_calls: list[str] = []
    locate_calls: list[str] = []
    texts: list[str] = []
    keys: list[tuple[str, int, float]] = []

    class RuntimeStub:
        def __init__(self):
            self._locate_results = [Box(left=10, top=20, width=40, height=20, source=apply_template), None]

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            wait_calls.append(template)
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            return None

        def type_text(self, text: str):
            texts.append(text)

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
    assert locate_calls == [
        str((CW_ASSET_ROOT / "enter_strategy_code.png").resolve()),
        apply_template,
        apply_template,
    ]
    assert keys == [("esc", 3, 1)]


def test_apply_cw_guide_via_ui_fails_when_apply_button_does_not_clear():
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None

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


def test_apply_cw_guide_via_ui_skips_strategy_click_when_guide_page_already_open():
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None

    strategy_template = str((CW_ASSET_ROOT / "strategy.png").resolve())
    enter_code_template = str((CW_ASSET_ROOT / "enter_strategy_code.png").resolve())
    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    wait_calls: list[str] = []
    locate_calls: list[str] = []
    clicks: list[tuple[float, float]] = []

    class RuntimeStub:
        def __init__(self):
            self._apply_locate_results = [Box(left=10, top=20, width=40, height=20, source=apply_template), None]

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            wait_calls.append(template)
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            clicks.append((x, y))

        def type_text(self, text: str):
            return None

        def locate(self, template: str, **kwargs):
            locate_calls.append(template)
            if template == enter_code_template:
                return Box(left=10, top=20, width=40, height=20, source=template)
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
    assert enter_code_template in locate_calls
    assert strategy_template not in wait_calls
    assert enter_code_template in wait_calls
    assert clicks[0] == (30, 30)


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
