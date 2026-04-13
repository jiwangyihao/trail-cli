from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from trail.artifacts.store import ArtifactStore
from trail.cli import app
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
                "tourn_detail": {
                    "share_code": share_code,
                    "labels": [{"text": "9级搜牌"}],
                    "role_stages": [
                        {
                            "stage": "Opening",
                            "front_roles": [{"name": "黑塔", "star": 1}],
                            "back_roles": [{"name": "艾丝妲", "star": 1}],
                        },
                        {
                            "stage": "Final",
                            "front_roles": [{"name": "希儿", "star": 3}],
                            "back_roles": [{"name": "佩拉", "star": 2}],
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
                    "tourn_detail": {
                        "share_code": "LIST-CODE",
                        "labels": [{"text": "9级搜牌"}, {"text": "银河学者"}],
                        "support_hard": True,
                        "role_stages": [
                            {
                                "stage": "Opening",
                                "front_roles": [{"name": "黑塔"}],
                                "back_roles": [{"name": "艾丝妲"}],
                                "traits": [{"name": "智识"}],
                            },
                            {
                                "stage": "Final",
                                "front_roles": [{"name": "希儿"}, {"name": "布洛妮娅"}],
                                "back_roles": [{"name": "佩拉"}],
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
            "source_url": "https://www.miyoushe.com/sr/article/70472857",
            "article_id": "70472857",
            "title": "货币战争攻略码",
            "author": "测试作者",
            "uploader": "测试作者",
        },
    )

    assert refreshed.scene_state["cw"]["guide"] == {
        "artifact": "guide-demo",
        "share_code": "##demo##",
        "source_url": "https://www.miyoushe.com/sr/article/70472857",
        "article_id": "70472857",
        "title": "货币战争攻略码",
        "author": "测试作者",
        "uploader": "测试作者",
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
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


def test_guide_fetch_cw_cli_creates_artifact_from_remote_payload(cli_runner, fake_runtime, tmp_path, monkeypatch):
    import trail.commands.guide as guide_cmd
    import trail.scenes.cw.guide as guide_scene

    store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    captured_request: dict[str, object] = {}

    def fake_urlopen(request, timeout=10):
        captured_request["url"] = request.full_url
        captured_request["timeout"] = timeout
        captured_request["headers"] = {key.lower(): value for key, value in request.header_items()}
        return FakeHttpResponse(fake_lineup_detail_response())

    monkeypatch.setattr(guide_cmd, "artifact_store_factory", lambda: store, raising=False)
    monkeypatch.setattr(
        guide_scene,
        "urlopen",
        fake_urlopen,
        raising=False,
    )

    result = cli_runner.invoke(app, ["guide", "fetch", "cw", fake_lineup_url()])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    artifact_path = Path(payload["data"]["path"])
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert captured_request == {
        "url": "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/lineup/detail?id=70472857&game=hkrpg",
        "timeout": 10,
        "headers": {
            "accept": "application/json, text/plain, */*",
            "x-rpc-currencywar-tourn": "tourn",
            "x-rpc-platform": "pc",
        },
    }
    assert saved["share_code"] == "##REAL-CODE##"
    assert saved["source_url"] == fake_lineup_url()
    assert saved["lineup_id"] == "70472857"
    assert saved["title"] == "7群攻2银河学者"
    assert saved["author"] == "测试作者"
    assert saved["uploader"] == "测试作者"
    assert saved["min_level"] == 9
    assert saved["mid_level"] == 9
    assert saved["on_field"] == {"希儿": 9}
    assert saved["off_field"] == {"佩拉": 3}


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
        "min_coins": 40,
        "min_level": 9,
        "mid_level": 9,
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
    }


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
            "page": 2,
            "limit": 10,
            "trait_id": 321,
            "order": "hot",
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
                "share_code": "LIST-CODE",
                "labels": ["9级搜牌", "银河学者"],
                "final_traits": ["巡猎", "量子"],
                "final_roles": ["希儿", "布洛妮娅", "佩拉"],
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


def test_fetch_cw_guide_returns_artifact_meta(tmp_path):
    guide_module = load_cw_guide_module()
    fetch_cw_guide = getattr(guide_module, "fetch_cw_guide", None)
    assert fetch_cw_guide is not None

    store = ArtifactStore(tmp_path)
    artifact = fetch_cw_guide("https://example.invalid/cw", artifact_store=store, fetcher=fake_fetcher)

    assert artifact.scene == "cw"
    assert artifact.kind == "guide"


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


def test_cw_guide_apply_cli_runs_runtime_action_chain_and_persists_scene_state(
    cli_runner,
    fake_runtime,
    fake_session,
    tmp_path,
    monkeypatch,
):
    import trail.commands.cw as cw_cmd

    artifact_store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    meta = artifact_store.create(scene="cw", kind="guide", payload=fake_guide())
    monkeypatch.setattr(cw_cmd, "artifact_store_factory", lambda: artifact_store, raising=False)
    fake_runtime.wait_result = Box(left=10, top=20, width=40, height=20, source="guide.png")

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", fake_session, "--guide", meta.artifact_id])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["artifact"] == meta.artifact_id
    assert payload["data"]["share_code"] == "##demo##"
    assert fake_runtime.wait_calls == [
        str((CW_ASSET_ROOT / "strategy.png").resolve()),
        str((CW_ASSET_ROOT / "enter_strategy_code.png").resolve()),
        str((CW_ASSET_ROOT / "ensure2.png").resolve()),
        str((CW_ASSET_ROOT / "apply_strategy.png").resolve()),
    ]
    assert fake_runtime.clicks == [
        (30, 30),
        (30, 30),
        (960, 540),
        (30, 30),
        (30, 30),
    ]
    assert fake_runtime.keys == [("esc", 3, 1)]
    assert fake_runtime.hotkeys == []
    assert fake_runtime.texts == ["##demo##"]
    assert fake_runtime.locate_calls == [str((CW_ASSET_ROOT / "apply_strategy.png").resolve())]

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["guide"]["artifact"] == meta.artifact_id
    assert session.scene_state["cw"]["guide"]["on_field"] == {"希儿": 9}
    assert session.scene_state["cw"]["guide"]["off_field"] == {"佩拉": 3}
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"希儿": 9, "佩拉": 3}
    assert session.scene_state["cw"]["constraints"]["min_coins"] == 40
    assert session.scene_state["cw"]["constraints"]["min_level"] == 7
    assert session.scene_state["cw"]["constraints"]["mid_level"] == 9
    assert session.scene_state["cw"]["shop"]["stale"] is True


def test_guide_config_cw_cli_returns_envelope_with_catalog_without_runtime_side_effects(cli_runner, monkeypatch):
    import trail.commands.guide as guide_cmd
    import trail.scenes.cw.guide as guide_scene

    captured_request: dict[str, object] = {}

    def fake_urlopen(request, timeout=10):
        captured_request["url"] = request.full_url
        captured_request["headers"] = {key.lower(): value for key, value in request.header_items()}
        return FakeHttpResponse(fake_cw_config_response())

    monkeypatch.setattr(
        guide_cmd,
        "runtime_factory",
        lambda **kwargs: pytest.fail("guide config should not initialize runtime"),
        raising=False,
    )
    monkeypatch.setattr(guide_scene, "urlopen", fake_urlopen, raising=False)

    result = cli_runner.invoke(app, ["guide", "config", "cw"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
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
    assert captured_request == {
        "url": "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/config?game=hkrpg",
        "headers": {
            "accept": "application/json, text/plain, */*",
            "x-rpc-currencywar-tourn": "tourn",
            "x-rpc-platform": "pc",
        },
    }
    assert not Path(".trail/shots").exists()


def test_guide_config_invalid_scene_does_not_initialize_runtime_or_create_shots(cli_runner, monkeypatch):
    import trail.commands.guide as guide_cmd
    import trail.scenes.cw.guide as guide_scene

    monkeypatch.setattr(
        guide_cmd,
        "runtime_factory",
        lambda **kwargs: pytest.fail("guide config should not initialize runtime for invalid scene"),
        raising=False,
    )
    monkeypatch.setattr(
        guide_scene,
        "urlopen",
        lambda request, timeout=10: pytest.fail("invalid scene should fail before requesting api"),
        raising=False,
    )

    result = cli_runner.invoke(app, ["guide", "config", "ocr"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "SCENE_NOT_SUPPORTED",
        "message": "暂不支持场景 ocr",
    }
    assert not Path(".trail/shots").exists()


def test_guide_list_cw_cli_returns_envelope_without_runtime_side_effects(cli_runner, monkeypatch):
    import trail.commands.guide as guide_cmd
    import trail.scenes.cw.guide as guide_scene

    captured_request: dict[str, object] = {}

    def fake_urlopen(request, timeout=10):
        captured_request["url"] = request.full_url
        captured_request["method"] = request.get_method()
        captured_request["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured_request["body"] = json.loads(request.data.decode("utf-8"))
        return FakeHttpResponse(fake_lineup_index_response())

    monkeypatch.setattr(
        guide_cmd,
        "runtime_factory",
        lambda **kwargs: pytest.fail("guide list should not initialize runtime"),
        raising=False,
    )
    monkeypatch.setattr(guide_scene, "urlopen", fake_urlopen, raising=False)

    result = cli_runner.invoke(
        app,
        [
            "guide",
            "list",
            "cw",
            "--page",
            "3",
            "--limit",
            "5",
            "--trait-id",
            "1001",
            "--order",
            "new",
            "--next-page-token",
            "cursor-next",
            "--match-change-job",
            "true",
            "--match-hard",
            "false",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"] == {
        "list": [
            {
                "id": "lineup-demo",
                "title": "7群攻2银河学者",
                "nickname": "测试作者",
                "share_code": "LIST-CODE",
                "labels": ["9级搜牌", "银河学者"],
                "final_traits": ["巡猎", "量子"],
                "final_roles": ["希儿", "布洛妮娅", "佩拉"],
                "support_hard": True,
            }
        ],
        "next_page_token": "next-token-demo",
    }
    assert captured_request == {
        "url": "https://act-api-takumi.miyoushe.com/event/rpgcurrencywar/game/lineup/index",
        "method": "POST",
        "headers": {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "x-rpc-currencywar-tourn": "tourn",
            "x-rpc-platform": "pc",
        },
        "body": {
            "game": "hkrpg",
            "page": 3,
            "limit": 5,
            "trait_id": 1001,
            "order": "new",
            "next_page_token": "cursor-next",
            "match_change_job": True,
            "match_hard": False,
        },
    }
    assert not Path(".trail/shots").exists()


def test_cw_guide_apply_cli_rejects_invalid_artifact(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd

    artifact_store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    meta = artifact_store.create(scene="ocr", kind="guide", payload={"share_code": "##wrong##"})
    monkeypatch.setattr(cw_cmd, "artifact_store_factory", lambda: artifact_store, raising=False)

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", fake_session, "--guide", meta.artifact_id])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "GUIDE_SCENE_MISMATCH",
        "message": "guide artifact scene must be cw, got: ocr",
    }
    assert fake_runtime.wait_calls == []
    assert fake_runtime.hotkeys == []
    assert fake_runtime.texts == []
    assert fake_runtime.clicks == []
    assert fake_runtime.keys == []


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
    assert locate_calls == [apply_template, apply_template]
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


@pytest.mark.parametrize(
    ("artifact_id", "raw"),
    [
        ("0123456789abcdef0123456789abcdef", "{broken json"),
        (
            "fedcba9876543210fedcba9876543210",
            json.dumps({"artifact_id": "fedcba9876543210fedcba9876543210", "kind": "guide"}, ensure_ascii=False),
        ),
    ],
)
def test_cw_guide_apply_cli_classifies_invalid_artifact(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch, artifact_id, raw):
    import trail.commands.cw as cw_cmd

    artifacts_dir = tmp_path / ".trail" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / f"{artifact_id}.json").write_text(raw, encoding="utf-8")
    monkeypatch.setattr(cw_cmd, "artifact_store_factory", lambda: ArtifactStore(artifacts_dir), raising=False)

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", fake_session, "--guide", artifact_id])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "GUIDE_ARTIFACT_INVALID"


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
