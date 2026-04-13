from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from trail.artifacts.store import ArtifactStore
from trail.session.store import SessionStore


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


class FakeRuntime:
    def __init__(self, screenshot_path: Path | None = None):
        self._shot = screenshot_path or Path("shot.png")
        self.locate_result = None
        self.locate_calls: list[str] = []
        self.wait_result = None
        self.wait_calls: list[str] = []
        self.ocr_result = []
        self.clicks: list[tuple[float, float]] = []
        self.drags: list[tuple[float, float, float, float]] = []
        self.keys: list[tuple[str, int, float]] = []
        self.hotkeys: list[tuple[str, ...]] = []

    def capture_after_action(self, optional: bool = False):
        return self._shot

    def ocr(self, **kwargs):
        return self.ocr_result

    def locate(self, template: str, **kwargs):
        self.locate_calls.append(template)
        return self.locate_result

    def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
        self.wait_calls.append(template)
        return self.wait_result

    def click_point(self, x: float, y: float, **kwargs):
        self.clicks.append((x, y))

    def drag_to(self, from_x: float, from_y: float, to_x: float, to_y: float):
        self.drags.append((from_x, from_y, to_x, to_y))

    def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
        self.keys.append((key, presses, interval))

    def hotkey(self, *keys: str):
        self.hotkeys.append(tuple(keys))


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch) -> FakeRuntime:
    runtime = FakeRuntime(tmp_path / "after.png")

    import trail.commands.cw as cw_cmd
    import trail.commands.guide as guide_cmd
    import trail.commands.image as image_cmd
    import trail.commands.input as input_cmd
    import trail.commands.ocr as ocr_cmd
    import trail.commands.screen as screen_cmd
    import trail.commands.session as session_cmd
    import trail.commands.state as state_cmd
    import trail.commands.window as window_cmd

    runtime_builder = lambda **kwargs: runtime

    for module in (cw_cmd, guide_cmd, image_cmd, input_cmd, ocr_cmd, screen_cmd, session_cmd, state_cmd, window_cmd):
        monkeypatch.setattr(module, "runtime_factory", runtime_builder, raising=False)

    monkeypatch.setattr(guide_cmd, "artifact_store_factory", lambda: ArtifactStore(tmp_path / ".trail" / "artifacts"), raising=False)

    monkeypatch.setattr(window_cmd, "attach_window", lambda window_title: {"title": window_title, "hwnd": 123})
    return runtime


@pytest.fixture
def fake_session(tmp_path, monkeypatch):
    sessions_dir = tmp_path / ".trail" / "sessions"
    store = SessionStore(sessions_dir)

    import trail.commands.cw as cw_cmd
    import trail.commands.session as session_cmd
    import trail.commands.state as state_cmd

    monkeypatch.setattr(session_cmd, "session_store_factory", lambda: store, raising=False)
    monkeypatch.setattr(state_cmd, "session_store_factory", lambda: store, raising=False)
    monkeypatch.setattr(cw_cmd, "session_store_factory", lambda: store, raising=False)

    session = store.create(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    return session.session_id


def build_fake_cw_session(tmp_path, purchases: dict | None = None):
    store = SessionStore(tmp_path)
    session = store.create(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    session.scene_state["cw"] = {
        "guide": {"remaining_purchases": purchases or {}},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        "slots": {"stale": True, "hand": []},
        "shop": {"stale": True},
        "stage": {"stale": True},
        "metrics": {},
    }
    return session


def fake_stage_detector():
    return "shop"


def fake_reader():
    return ["希儿"], ["佩拉"], ["银狼"]


def fake_shop_scan():
    return ["银狼"], 40, 7, False, 8


def fake_buy_success(**kwargs):
    return None


def fake_click(*args, **kwargs):
    return None


def fake_event_handler():
    return "special", "confirm"


def fake_fetcher(url: str):
    return {"share_code": "##demo##", "on_field": {"希儿": 9}, "off_field": {"佩拉": 3}}


def fake_guide():
    return {
        "artifact_id": "guide-demo",
        "share_code": "##demo##",
        "on_field": {"希儿": 9},
        "off_field": {"佩拉": 3},
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 9,
    }
