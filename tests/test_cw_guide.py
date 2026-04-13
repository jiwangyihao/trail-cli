from __future__ import annotations

from contextlib import contextmanager
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


def test_cw_guide_apply_cli_runs_runtime_action_chain_and_persists_scene_state(
    cli_runner,
    fake_runtime,
    fake_session,
    tmp_path,
    monkeypatch,
):
    import trail.commands.cw as cw_cmd
    import trail.scenes.cw.guide as guide_scene

    artifact_store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    meta = artifact_store.create(scene="cw", kind="guide", payload=fake_guide())
    monkeypatch.setattr(cw_cmd, "artifact_store_factory", lambda: artifact_store, raising=False)
    clipboard_events: list[tuple[str, str | None]] = []

    @contextmanager
    def fake_temporary_clipboard_text(text: str):
        clipboard_events.append(("set", text))
        try:
            yield
        finally:
            clipboard_events.append(("restore", None))

    monkeypatch.setattr(guide_scene, "_temporary_clipboard_text", fake_temporary_clipboard_text, raising=False)
    monkeypatch.setattr(guide_scene, "_copy_text_to_clipboard", lambda text: clipboard_events.append(("legacy-copy", text)), raising=False)
    monkeypatch.setattr(guide_scene, "_paste_clipboard_text", lambda: clipboard_events.append(("legacy-paste", None)), raising=False)
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
    assert fake_runtime.hotkeys == [("ctrl", "v")]
    assert clipboard_events == [("set", "##demo##"), ("restore", None)]

    session = SessionStore(tmp_path / ".trail" / "sessions").load(fake_session)
    assert session.scene_state["cw"]["guide"]["artifact"] == meta.artifact_id
    assert session.scene_state["cw"]["guide"]["on_field"] == {"希儿": 9}
    assert session.scene_state["cw"]["guide"]["off_field"] == {"佩拉": 3}
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"希儿": 9, "佩拉": 3}
    assert session.scene_state["cw"]["constraints"]["min_coins"] == 40
    assert session.scene_state["cw"]["constraints"]["min_level"] == 7
    assert session.scene_state["cw"]["constraints"]["mid_level"] == 9
    assert session.scene_state["cw"]["shop"]["stale"] is True


def test_cw_guide_apply_cli_rejects_invalid_artifact(cli_runner, fake_runtime, fake_session, tmp_path, monkeypatch):
    import trail.commands.cw as cw_cmd
    import trail.scenes.cw.guide as guide_scene

    artifact_store = ArtifactStore(tmp_path / ".trail" / "artifacts")
    meta = artifact_store.create(scene="ocr", kind="guide", payload={"share_code": "##wrong##"})
    monkeypatch.setattr(cw_cmd, "artifact_store_factory", lambda: artifact_store, raising=False)
    copied: list[str] = []
    pasted: list[str] = []
    monkeypatch.setattr(guide_scene, "_copy_text_to_clipboard", lambda text: copied.append(text), raising=False)
    monkeypatch.setattr(guide_scene, "_paste_clipboard_text", lambda: pasted.append("paste"), raising=False)

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
    assert fake_runtime.clicks == []
    assert fake_runtime.keys == []


def test_temporary_clipboard_text_restores_previous_text_after_success(monkeypatch):
    guide_module = load_cw_guide_module()
    temporary_clipboard_text = getattr(guide_module, "_temporary_clipboard_text", None)
    assert temporary_clipboard_text is not None

    clipboard = {"value": "user clipboard"}
    writes: list[str | None] = []
    monkeypatch.setattr(guide_module, "_read_clipboard_text", lambda: clipboard["value"])
    monkeypatch.setattr(
        guide_module,
        "_write_clipboard_text",
        lambda text: writes.append(text) or clipboard.update(value=text),
    )
    monkeypatch.setattr(
        guide_module,
        "_clear_clipboard",
        lambda: writes.append(None) or clipboard.update(value=None),
    )

    with temporary_clipboard_text("##demo##"):
        assert clipboard["value"] == "##demo##"

    assert clipboard["value"] == "user clipboard"
    assert writes == ["##demo##", "user clipboard"]


def test_temporary_clipboard_text_restores_previous_text_after_failure(monkeypatch):
    guide_module = load_cw_guide_module()
    temporary_clipboard_text = getattr(guide_module, "_temporary_clipboard_text", None)
    assert temporary_clipboard_text is not None

    clipboard = {"value": "user clipboard"}
    writes: list[str | None] = []
    monkeypatch.setattr(guide_module, "_read_clipboard_text", lambda: clipboard["value"])
    monkeypatch.setattr(
        guide_module,
        "_write_clipboard_text",
        lambda text: writes.append(text) or clipboard.update(value=text),
    )
    monkeypatch.setattr(
        guide_module,
        "_clear_clipboard",
        lambda: writes.append(None) or clipboard.update(value=None),
    )

    with pytest.raises(RuntimeError, match="boom"):
        with temporary_clipboard_text("##demo##"):
            raise RuntimeError("boom")

    assert clipboard["value"] == "user clipboard"
    assert writes == ["##demo##", "user clipboard"]


def test_apply_cw_guide_via_ui_waits_for_apply_button_to_settle_before_exit(monkeypatch):
    guide_module = load_cw_guide_module()
    apply_cw_guide_via_ui = getattr(guide_module, "apply_cw_guide_via_ui", None)
    assert apply_cw_guide_via_ui is not None

    apply_template = str((CW_ASSET_ROOT / "apply_strategy.png").resolve())
    confirm_template = str((CW_ASSET_ROOT / "ensure2.png").resolve())
    wait_calls: list[str] = []
    locate_calls: list[str] = []
    hotkeys: list[tuple[str, ...]] = []
    keys: list[tuple[str, int, float]] = []

    class RuntimeStub:
        def __init__(self):
            self._locate_results = [Box(left=10, top=20, width=40, height=20, source=apply_template), None]

        def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
            wait_calls.append(template)
            return Box(left=10, top=20, width=40, height=20, source=template)

        def click_point(self, x: float, y: float, **kwargs):
            return None

        def hotkey(self, *combo: str):
            hotkeys.append(tuple(combo))

        def locate(self, template: str, **kwargs):
            locate_calls.append(template)
            if template != apply_template:
                return None
            if self._locate_results:
                return self._locate_results.pop(0)
            return None

        def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
            keys.append((key, presses, interval))

    @contextmanager
    def fake_temporary_clipboard_text(text: str):
        assert text == "##demo##"
        yield

    monkeypatch.setattr(guide_module, "_temporary_clipboard_text", fake_temporary_clipboard_text, raising=False)
    monkeypatch.setattr(guide_module, "_copy_text_to_clipboard", lambda text: None, raising=False)
    monkeypatch.setattr(guide_module, "_paste_clipboard_text", lambda: None, raising=False)

    apply_cw_guide_via_ui(RuntimeStub(), share_code="##demo##")

    assert confirm_template in wait_calls
    assert hotkeys == [("ctrl", "v")]
    assert locate_calls == [apply_template, apply_template]
    assert keys == [("esc", 3, 1)]


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
