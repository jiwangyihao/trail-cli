from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from trail.artifacts.store import ArtifactStore
from trail.cli import app
from trail.core.errors import TrailError
from trail.session.store import SessionStore


def fake_fetcher(url: str):
    return {"share_code": "##demo##", "on_field": {"希儿": 9}, "off_field": {"佩拉": 3}}


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


def fake_miyoushe_post_response(share_code: str = "##REAL-CODE##") -> dict:
    return {
        "retcode": 0,
        "message": "OK",
        "data": {
            "post": {
                "post": {
                    "post_id": "70472857",
                    "subject": "货币战争攻略码",
                    "content": f"<p>【7群攻2银河学者】7级定战力</p><p>{share_code}</p>",
                    "structured_content": json.dumps(
                        [{"insert": f"【7群攻2银河学者】7级定战力\n{share_code}\n"}],
                        ensure_ascii=False,
                    ),
                }
            }
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
    assert cw_state["guide"]["remaining_purchases"] == {"希儿": 9, "佩拉": 3}
    assert cw_state["constraints"]["min_coins"] == 40
    assert cw_state["constraints"]["min_level"] == 7
    assert cw_state["constraints"]["mid_level"] == 9
    assert cw_state["slots"]["stale"] is True


def test_guide_fetch_cw_cli_creates_artifact_from_remote_payload(cli_runner, fake_runtime, tmp_path, monkeypatch):
    import trail.commands.guide as guide_cmd
    import trail.scenes.cw.guide as guide_scene

    store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    monkeypatch.setattr(guide_cmd, "artifact_store_factory", lambda: store, raising=False)
    monkeypatch.setattr(
        guide_scene,
        "urlopen",
        lambda request, timeout=10: FakeHttpResponse(fake_miyoushe_post_response()),
        raising=False,
    )

    result = cli_runner.invoke(app, ["guide", "fetch", "cw", "https://www.miyoushe.com/sr/article/70472857"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    artifact_path = Path(payload["data"]["path"])
    saved = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert saved["share_code"] == "##REAL-CODE##"
    assert saved["source_url"] == "https://www.miyoushe.com/sr/article/70472857"
    assert saved["title"] == "货币战争攻略码"


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


def test_cw_guide_apply_resolves_artifact_and_persists_scene_state(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd

    artifact_store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    meta = artifact_store.create(scene="cw", kind="guide", payload=fake_guide())
    monkeypatch.setattr(cw_cmd, "artifact_store_factory", lambda: artifact_store, raising=False)

    result = cli_runner.invoke(app, ["cw", "guide", "apply", "--session", fake_session, "--guide", meta.artifact_id])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["artifact"] == meta.artifact_id
    assert payload["data"]["share_code"] == "##demo##"

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["guide"]["artifact"] == meta.artifact_id
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"希儿": 9, "佩拉": 3}
    assert session.scene_state["cw"]["constraints"]["min_coins"] == 40
    assert session.scene_state["cw"]["constraints"]["min_level"] == 7
    assert session.scene_state["cw"]["constraints"]["mid_level"] == 9
    assert session.scene_state["cw"]["shop"]["stale"] is True


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


@pytest.mark.parametrize(
    "structured_content",
    [
        json.dumps({"ops": [{"insert": {"image": "https://example.invalid/demo.png"}}]}, ensure_ascii=False),
        json.dumps(["unexpected", {"insert": {"image": "https://example.invalid/demo.png"}}], ensure_ascii=False),
    ],
)
def test_extract_post_text_falls_back_to_html_when_structured_content_shape_unexpected(structured_content):
    guide_module = load_cw_guide_module()
    extract_post_text = getattr(guide_module, "_extract_post_text", None)
    assert extract_post_text is not None

    post = {
        "post": {
            "content": "<p>【回退】</p><p>##HTML-FALLBACK##</p>",
            "structured_content": structured_content,
        }
    }

    assert "##HTML-FALLBACK##" in extract_post_text(post)


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
