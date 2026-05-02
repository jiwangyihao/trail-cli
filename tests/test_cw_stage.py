from __future__ import annotations

import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest
from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.model import Box
from trail.runtime.ocr_config import OcrRequestConfig
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw import stage as stage_scene
from trail.session.store import SessionStore


def _rapidocr_piece(text: str):
    return ([[0, 0], [10, 0], [10, 10], [0, 10]], text, 0.99)


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box(alias: str, *, left: int, top: int, width: int = 40, height: int = 20) -> Box:
    return Box(left=left, top=top, width=width, height=height, source=_asset(alias))


class _StageDetectorRuntime:
    def __init__(
        self,
        *,
        locate_hits: dict[str, object] | None = None,
        ocr_image_result: list[object] | None = None,
    ):
        self.locate_hits = locate_hits or {}
        self.ocr_image_result = ocr_image_result or []
        self.locate_calls: list[str] = []
        self.ocr_image_calls: list[dict[str, object]] = []
        self.screenshot_calls = 0
        self.matcher = SimpleNamespace(locate=self._locate)

    def screenshot(self, **kwargs):
        del kwargs
        self.screenshot_calls += 1
        return Image.new("RGB", (1280, 720), "white")

    def _locate(self, template: str, image):
        del image
        self.locate_calls.append(str(template))
        return self.locate_hits.get(str(template))

    def ocr_image(self, image, **kwargs):
        del image
        self.ocr_image_calls.append(kwargs)
        return self.ocr_image_result


def _build_stage_session(tmp_path: Path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ensure_cw_state(session)
    return session


def _seed_stage_status(session, *, stage: dict | None = None) -> dict:
    status = {"stale": False, "level": 7}
    session.scene_state["cw"]["stage"] = {
        "value": "shop",
        "stale": False,
        **(stage or {}),
        "status": status,
    }
    return status


class _ClickRuntime:
    def __init__(self):
        self.clicks: list[tuple[int, int]] = []

    def click_point(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


def test_build_cw_stage_detector_maps_resource_aliases_to_stage_values():
    class RuntimeSpy:
        def __init__(self):
            self.templates: list[str] = []
            self.matcher = SimpleNamespace(locate=self._locate)

        def screenshot(self, **kwargs):
            del kwargs
            return Image.new("RGB", (1280, 720), "white")

        def _locate(self, template: str, image):
            del image
            self.templates.append(template)
            if str(template).endswith("fortune_teller.png"):
                return {"left": 1, "top": 2, "width": 3, "height": 4}
            return None

    runtime = RuntimeSpy()

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "fortune"
    assert any(str(template).endswith("fortune_teller.png") for template in runtime.templates)


def test_build_cw_stage_detector_maps_fold_to_shop():
    class RuntimeSpy:
        def __init__(self):
            self.templates: list[str] = []
            self.matcher = SimpleNamespace(locate=self._locate)

        def screenshot(self, **kwargs):
            del kwargs
            return Image.new("RGB", (1280, 720), "white")

        def _locate(self, template: str, image):
            del image
            self.templates.append(template)
            if str(template).endswith("fold.png"):
                return {"left": 1, "top": 2, "width": 3, "height": 4}
            return None

    runtime = RuntimeSpy()

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "shop"


def test_build_cw_stage_detector_maps_replenish_template_to_replenish():
    class RuntimeSpy:
        def __init__(self):
            self.templates: list[str] = []
            self.matcher = SimpleNamespace(locate=self._locate)

        def screenshot(self, **kwargs):
            del kwargs
            return Image.new("RGB", (1280, 720), "white")

        def _locate(self, template: str, image):
            del image
            self.templates.append(template)
            if str(template).endswith("replenish_stage.png"):
                return {"left": 1, "top": 2, "width": 3, "height": 4}
            return None

    runtime = RuntimeSpy()

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "replenish"


def test_build_cw_stage_detector_falls_back_to_settle_keywords_from_ocr():
    class RuntimeSpy:
        def __init__(self):
            self.templates: list[str] = []
            self.ocr_calls = 0
            self.matcher = SimpleNamespace(locate=self._locate)

        def screenshot(self, **kwargs):
            del kwargs
            return Image.new("RGB", (1280, 720), "white")

        def _locate(self, template: str, image):
            del image
            self.templates.append(template)
            return None

        def ocr_image(self, image, **kwargs):
            del image, kwargs
            self.ocr_calls += 1
            return [
                {"text": "挑战成功"},
                {"text": "1-1奖励"},
                {"text": "继续挑战"},
            ]

    runtime = RuntimeSpy()

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "settle"
    assert runtime.ocr_calls == 1


def test_build_cw_stage_detector_reads_tuple_ocr_results_for_settle_keywords():
    class RuntimeSpy:
        def __init__(self):
            self.matcher = SimpleNamespace(locate=self._locate)

        def screenshot(self, **kwargs):
            del kwargs
            return Image.new("RGB", (1280, 720), "white")

        def _locate(self, template: str, image):
            del template, image
            return None

        def ocr_image(self, image, **kwargs):
            del image, kwargs
            return [
                ([[837.0, 199.0], [1081.0, 199.0]], "挑战成功", 0.98),
                ([[911.0, 880.0], [1011.0, 880.0]], "继续挑战", 0.99),
            ]

    detector = stage_scene.build_cw_stage_detector(RuntimeSpy())

    assert detector() == "settle"


def test_build_cw_stage_detector_maps_click_blank_with_layer_transition_ocr_to_layer_transition():
    runtime = _StageDetectorRuntime(
        locate_hits={_asset("stage.boss_preview"): _box("stage.boss_preview", left=440, top=480)},
        ocr_image_result=[_rapidocr_piece("点击空白处继续"), _rapidocr_piece("位面")],
    )

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "layer_transition"
    assert runtime.ocr_image_calls == [{"ocr": OcrRequestConfig(lang="ch")}]


def test_build_cw_stage_detector_keeps_true_boss_preview_when_ocr_mentions_boss():
    runtime = _StageDetectorRuntime(
        locate_hits={_asset("stage.boss_preview"): _box("stage.boss_preview", left=440, top=480)},
        ocr_image_result=[_rapidocr_piece("本场对局首领")],
    )

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "boss_preview"
    assert runtime.ocr_image_calls == [{"ocr": OcrRequestConfig(lang="ch")}]


def test_build_cw_stage_detector_does_not_treat_click_blank_without_layer_text_as_layer_transition():
    runtime = _StageDetectorRuntime(
        locate_hits={_asset("stage.boss_preview"): _box("stage.boss_preview", left=440, top=480)},
        ocr_image_result=[_rapidocr_piece("点击空白处继续")],
    )

    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() is None
    assert runtime.ocr_image_calls[0] == {"ocr": OcrRequestConfig(lang="ch")}


def test_build_cw_stage_detector_uses_single_screenshot_for_template_hits():
    class RuntimeSpy:
        def __init__(self):
            self.screenshot_calls = 0
            self.ocr_calls = 0
            self.matcher = SimpleNamespace(locate=self._locate)

        def screenshot(self, **kwargs):
            del kwargs
            self.screenshot_calls += 1
            return Image.new("RGB", (1280, 720), "white")

        def _locate(self, template, image):
            del image
            if str(template).endswith("fortune_teller.png"):
                return {"left": 1, "top": 2, "width": 3, "height": 4}
            return None

        def locate(self, template, **kwargs):
            del kwargs
            return self.matcher.locate(template, self.screenshot())

        def ocr_image(self, image, **kwargs):
            del image, kwargs
            self.ocr_calls += 1
            return []

    runtime = RuntimeSpy()
    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "fortune"
    assert runtime.screenshot_calls == 1
    assert runtime.ocr_calls == 0


def test_build_cw_stage_detector_uses_single_screenshot_before_ocr_fallback():
    class RuntimeSpy:
        def __init__(self):
            self.screenshot_calls = 0
            self.ocr_calls = 0
            self.legacy_ocr_calls = 0
            self.matcher = SimpleNamespace(locate=self._locate)

        def screenshot(self, **kwargs):
            del kwargs
            self.screenshot_calls += 1
            return Image.new("RGB", (1280, 720), "white")

        def _locate(self, template, image):
            del template, image
            return None

        def locate(self, template, **kwargs):
            del kwargs
            return self.matcher.locate(template, self.screenshot())

        def ocr(self, **kwargs):
            del kwargs
            self.legacy_ocr_calls += 1
            return self.ocr_image(self.screenshot())

        def ocr_image(self, image, **kwargs):
            del image, kwargs
            self.ocr_calls += 1
            return [_rapidocr_piece("挑战成功"), _rapidocr_piece("继续挑战")]

    runtime = RuntimeSpy()
    detector = stage_scene.build_cw_stage_detector(runtime)

    assert detector() == "settle"
    assert runtime.screenshot_calls == 1
    assert runtime.ocr_calls == 1
    assert runtime.legacy_ocr_calls == 0


def test_detect_cw_stage_refreshes_stage_snapshot(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})

    refreshed = stage_scene.detect_cw_stage(session, detector=lambda: "shop")

    assert refreshed.scene_state["cw"]["stage"] == {"value": "shop", "stale": False}
    assert refreshed.last_stage == {"scene": "cw", "value": "shop"}


def test_stage_status_parser_preserves_zero_role_counts():
    by_key = {
        ("stage_status", "level"): SimpleNamespace(pieces=[_rapidocr_piece("LV.7")]),
        ("stage_status", "exp"): SimpleNamespace(pieces=[_rapidocr_piece("4/52")]),
        ("stage_status", "team_size"): SimpleNamespace(pieces=[_rapidocr_piece("3/3")]),
    }

    status = stage_scene.parse_cw_stage_status(
        by_key,
        role_count={"front": 0, "back": 0, "hand": 0, "field": 0, "total": 0},
    )

    assert status == {
        "stale": False,
        "level": 7,
        "exp": "4/52",
        "team_size": "3/3",
        "role_count": {"front": 0, "back": 0, "hand": 0, "field": 0, "total": 0},
    }


def test_stage_replace_fields_preserves_existing_stage_status(tmp_path):
    session = _build_stage_session(tmp_path)
    status = {"stale": False, "level": 7, "role_count": {"front": 1}}
    session.scene_state["cw"]["stage"] = {
        "value": "shop",
        "stale": False,
        "error": {"code": "OLD", "message": "old"},
        "status": status,
    }

    stage_scene._replace_stage_fields(session, stale=True)

    assert session.scene_state["cw"]["stage"] == {
        "value": "shop",
        "stale": True,
        "error": {"code": "OLD", "message": "old"},
        "status": {"stale": False, "level": 7, "role_count": {"front": 1}},
    }
    assert session.scene_state["cw"]["stage"]["status"] == status
    assert session.scene_state["cw"]["stage"]["status"] is not status
    session.scene_state["cw"]["stage"]["status"]["role_count"]["front"] = 2
    assert status["role_count"]["front"] == 1


def test_stage_replace_fields_copies_new_status_when_no_existing_status(tmp_path):
    session = _build_stage_session(tmp_path)
    new_status = {"stale": False, "level": 8, "role_count": {"front": 2}}
    session.scene_state["cw"]["stage"] = {"value": "shop", "stale": False}

    stage_scene._replace_stage_fields(session, status=new_status)

    assert session.scene_state["cw"]["stage"]["status"] == new_status
    assert session.scene_state["cw"]["stage"]["status"] is not new_status
    session.scene_state["cw"]["stage"]["status"]["role_count"]["front"] = 3
    assert new_status["role_count"]["front"] == 2


def test_stage_replace_fields_replaces_existing_status(tmp_path):
    session = _build_stage_session(tmp_path)
    old_status = {"stale": False, "level": 7, "role_count": {"front": 1}}
    new_status = {"stale": False, "level": 8, "role_count": {"front": 2}}
    session.scene_state["cw"]["stage"] = {
        "value": "shop",
        "stale": False,
        "status": old_status,
    }

    stage_scene._replace_stage_fields(session, status=new_status)

    assert session.scene_state["cw"]["stage"]["status"] == new_status
    assert session.scene_state["cw"]["stage"]["status"] is not new_status
    session.scene_state["cw"]["stage"]["status"]["role_count"]["front"] = 3
    assert old_status["role_count"]["front"] == 1
    assert new_status["role_count"]["front"] == 2


def test_detect_cw_stage_preserves_existing_stage_status(tmp_path):
    session = _build_stage_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7}}

    refreshed = stage_scene.detect_cw_stage(session, detector=lambda: "shop")

    assert refreshed.scene_state["cw"]["stage"]["value"] == "shop"
    assert refreshed.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}


def test_mark_cw_stage_status_stale_keeps_values(tmp_path):
    session = _build_stage_session(tmp_path)
    session.scene_state["cw"]["stage"] = {
        "value": "shop",
        "stale": False,
        "status": {"stale": False, "team_size": "3/3"},
    }

    stage_scene.mark_cw_stage_status_stale(session)

    assert session.scene_state["cw"]["stage"]["value"] == "shop"
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": True, "team_size": "3/3"}


def test_stage_error_and_stale_paths_preserve_existing_stage_status(tmp_path):
    session = _build_stage_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"value": "shop", "stale": False, "status": {"stale": False, "level": 7}}

    stage_scene.mark_cw_stage_stale(session)
    assert session.scene_state["cw"]["stage"] == {"stale": True, "status": {"stale": False, "level": 7}}

    session.scene_state["cw"]["stage"] = {"value": "shop", "stale": False, "status": {"stale": False, "level": 7}}
    with pytest.raises(TrailError):
        stage_scene.detect_cw_stage(
            session,
            detector=lambda: (_ for _ in ()).throw(TrailError("STAGE_AMBIGUOUS", "ambiguous")),
        )
    assert "value" not in session.scene_state["cw"]["stage"]
    assert session.scene_state["cw"]["stage"]["stale"] is True
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}


def test_battle_completed_stage_update_preserves_existing_stage_status(tmp_path):
    from trail.scenes.cw import battle as battle_scene

    session = _build_stage_session(tmp_path)
    _seed_stage_status(session, stage={"error": {"code": "OLD", "message": "old"}})

    battle_scene._set_completed_stage(session, stage="replenish")

    assert session.scene_state["cw"]["stage"]["value"] == "replenish"
    assert session.scene_state["cw"]["stage"]["error"] == {"code": "OLD", "message": "old"}
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}


def test_portal_selection_stage_update_preserves_existing_stage_status(tmp_path, monkeypatch):
    from trail.scenes.cw import portal as portal_scene

    session = _build_stage_session(tmp_path)
    _seed_stage_status(session)
    session.scene_state["cw"]["entry"] = {
        "page": "invest",
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
    }
    session.scene_state["cw"]["portal"] = {
        "cards": [{"card_idx": 1, "portal_title": "A"}, {"card_idx": 2, "portal_title": "B"}],
        "mode": "continue",
        "difficulty": "current",
        "battle_mode": "standard",
        "stale": False,
    }
    monkeypatch.setattr(portal_scene, "_detect_current_enter_page", lambda runtime, session=None, preferred_mode=None: {"page": "invest"})

    portal_scene.select_cw_portal(session, card_idx=2, runtime=_ClickRuntime())

    assert session.scene_state["cw"]["stage"]["value"] == "shop"
    assert session.scene_state["cw"]["stage"]["stale"] is True
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}


def test_strategy_selection_stage_update_preserves_existing_stage_status(tmp_path, monkeypatch):
    from trail.scenes.cw import strategy as strategy_scene

    session = _build_stage_session(tmp_path)
    _seed_stage_status(session, stage={"value": "invest"})
    session.scene_state["cw"]["strategy"] = {
        "cards": [{"card_idx": 1, "strategy_title": "快攻"}],
        "stale": False,
    }
    monkeypatch.setattr(
        strategy_scene,
        "_detect_strategy_page_state",
        lambda runtime, session=None: {"page": "in_game", "stage": "invest", "title": "请选择投资策略"},
    )

    strategy_scene.select_cw_strategy(session, card_idx=1, runtime=_ClickRuntime())

    assert session.scene_state["cw"]["stage"]["value"] == "invest"
    assert session.scene_state["cw"]["stage"]["stale"] is True
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}


def test_entry_invalidation_stage_update_preserves_existing_stage_status(tmp_path):
    from trail.scenes.cw.entry import enter_cw

    session = _build_stage_session(tmp_path)
    _seed_stage_status(session)

    enter_cw(session, mode="continue", difficulty="highest", battle_mode="overclock")

    assert session.scene_state["cw"]["stage"]["value"] == "shop"
    assert session.scene_state["cw"]["stage"]["stale"] is True
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}


def test_guide_runtime_invalidation_stage_update_preserves_existing_stage_status(tmp_path):
    from trail.scenes.cw.guide import invalidate_cw_guide_runtime_state

    session = _build_stage_session(tmp_path)
    _seed_stage_status(session)

    invalidate_cw_guide_runtime_state(session)

    assert session.scene_state["cw"]["stage"]["value"] == "shop"
    assert session.scene_state["cw"]["stage"]["stale"] is True
    assert session.scene_state["cw"]["stage"]["status"] == {"stale": False, "level": 7}


def test_shop_stage_status_parsers_have_no_duplicate_implementations():
    root = Path(__file__).resolve().parents[1]
    source = (root / "trail" / "scenes" / "cw" / "shop.py").read_text(encoding="utf-8")

    assert "def _parse_shop_level" not in source
    assert "def _parse_shop_exp" not in source
    assert "def _parse_shop_team_size" not in source
    assert "def _parse_last_int" not in source
    assert "def _ocr_shop_capture_items" not in source
    assert "def _ocr_shop_exp_items" not in source
    assert "def _filter_ocr_items_to_region" not in source
    assert "def _ocr_box_center_in_region" not in source
    assert "BytesIO" not in source
    assert "ImageEnhance" not in source
    assert "OcrRequestConfig" not in source
    assert "SHOP_LEVEL_REGION" not in source
    assert "SHOP_EXP_REGION" not in source
    assert "SHOP_TEAM_SIZE_REGION" not in source
    assert "_parse_shop_level = _shared_parse_cw_stage_level" in source
    assert "_parse_shop_exp = _shared_parse_cw_stage_exp" in source
    assert "_parse_shop_team_size = _shared_parse_cw_stage_team_size" in source


def test_known_cw_stage_writers_use_preserving_helper():
    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "trail" / "scenes" / "cw" / "battle.py",
        root / "trail" / "scenes" / "cw" / "portal.py",
        root / "trail" / "scenes" / "cw" / "strategy.py",
        root / "trail" / "scenes" / "cw" / "entry.py",
        root / "trail" / "scenes" / "cw" / "guide.py",
    ]
    direct_stage_assignment = re.compile(r'(?:cw_state|ensure_cw_state\(session\))\["stage"\]\s*=')

    offenders = []
    for path in checked_files:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if direct_stage_assignment.search(line):
                offenders.append(f"{path.relative_to(root)}:{line_no}: {line.strip()}")

    assert offenders == []


def test_wait_cw_stage_retries_until_stage_is_detected(tmp_path):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    stages = iter([None, None, "settle"])

    refreshed = stage_scene.wait_cw_stage(session, detector=lambda: next(stages), timeout=1)

    assert refreshed.scene_state["cw"]["stage"] == {"value": "settle", "stale": False}
    assert refreshed.last_stage == {"scene": "cw", "value": "settle"}


def test_wait_cw_stage_sleeps_between_detection_attempts(tmp_path, monkeypatch):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    stages = iter([None, "settle"])
    sleeps: list[float] = []

    monkeypatch.setattr(stage_scene, "sleep", lambda seconds: sleeps.append(seconds))

    refreshed = stage_scene.wait_cw_stage(session, detector=lambda: next(stages), timeout=1)

    assert refreshed.scene_state["cw"]["stage"] == {"value": "settle", "stale": False}
    assert sleeps == [0.5]


def test_wait_cw_stage_raises_timeout_when_stage_missing(tmp_path, monkeypatch):
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    ticks = iter([0.0, 0.2, 1.2])

    monkeypatch.setattr(stage_scene, "monotonic", lambda: next(ticks))

    with pytest.raises(TrailError) as exc_info:
        stage_scene.wait_cw_stage(session, detector=lambda: None, timeout=1)

    assert exc_info.value.code == "STAGE_TIMEOUT"
    assert str(exc_info.value) == "等待货币战争阶段超时"


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


def _set_stage(session, stage: dict):
    session.scene_state.setdefault("cw", {})["stage"] = dict(stage)
    value = stage.get("value")
    if isinstance(value, str) and not stage.get("stale"):
        session.last_stage = {"scene": "cw", "value": value}
    return session


def test_cw_stage_detect_service_persists_stage_state(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.detect_cw_stage",
        lambda session, detector: _set_stage(session, {"value": "preparation", "stale": False}),
    )

    result = cw_service.handle(
        method="cw.stage.detect",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert result["value"] == "preparation"
    assert service.load_session(session.session_id).scene_state["cw"]["stage"]["value"] == "preparation"


def test_cw_stage_wait_service_persists_waited_stage_state(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.wait_cw_stage",
        lambda session, detector, timeout: _set_stage(session, {"value": "settle", "stale": False}),
    )

    result = cw_service.handle(
        method="cw.stage.wait",
        payload={"session_id": session.session_id, "timeout": 120},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    assert result["value"] == "settle"
    assert service.load_session(session.session_id).scene_state["cw"]["stage"] == {"value": "settle", "stale": False}


def test_cw_stage_detect_service_preserves_last_stage_snapshot(tmp_path: Path, monkeypatch):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    monkeypatch.setattr("trail.daemon.cw_service.stage_detector_factory", lambda runtime: object())
    monkeypatch.setattr(
        "trail.daemon.cw_service.detect_cw_stage",
        lambda session, detector: _set_stage(session, {"value": "event", "stale": False}),
    )

    result = cw_service.handle(
        method="cw.stage.detect",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )
    persisted = service.load_session(session.session_id)

    assert result["value"] == "event"
    assert persisted.scene_state["cw"]["stage"]["value"] == "event"
    assert persisted.last_stage == {"scene": "cw", "value": "event"}
