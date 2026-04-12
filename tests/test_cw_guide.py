from __future__ import annotations

import importlib
import json

import pytest

from trail.artifacts.store import ArtifactStore
from trail.cli import app
from trail.session.store import SessionStore


def fake_fetcher(url: str):
    return {"share_code": "##demo##", "on_field": {"希儿": 9}, "off_field": {"佩拉": 3}}


def fake_guide() -> dict:
    return {
        "artifact_id": "guide-demo",
        "share_code": "##demo##",
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 9,
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
    assert cw_state["slots"]["stale"] is True


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

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["guide"]["artifact"] == meta.artifact_id
    assert session.scene_state["cw"]["shop"]["stale"] is True
