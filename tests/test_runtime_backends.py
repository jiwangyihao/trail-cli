from __future__ import annotations

from io import BytesIO
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.model import Box, WindowBinding


def test_windows_window_controller_capture_uses_imagegrab(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    monkeypatch.setattr(window_module.sys, "platform", "linux")

    class FakeImageGrab:
        called_with = None

        @staticmethod
        def grab(*, bbox):
            FakeImageGrab.called_with = bbox
            return Image.new("RGB", (10, 6), color="white")

    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=1),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: object())
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=10, top=20, width=100, height=60),
    )

    image_bytes = controller.capture(from_x=0.1, from_y=0.2, to_x=0.5, to_y=0.8)

    assert FakeImageGrab.called_with == (20, 32, 60, 68)
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_prefers_win32_capture_when_hwnd_present(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    called = {}

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(
        window_module,
        "_capture_win32_window",
        lambda hwnd, region: called.update({"hwnd": hwnd, "region": region}) or Image.new("RGB", (4, 4), color="white"),
    )

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=321))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=10, top=20, width=100, height=60),
    )

    image_bytes = controller.capture()

    assert called["hwnd"] == 321
    assert called["region"] == window_module.Region(left=10, top=20, width=100, height=60)
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_uses_resolved_hwnd_when_binding_missing(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    called = {}

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(
        window_module,
        "_capture_win32_window",
        lambda hwnd, region: called.update({"hwnd": hwnd, "region": region}) or Image.new("RGB", (4, 4), color="white"),
    )

    fake_window = SimpleNamespace(title="Demo", _hWnd=654)
    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=None),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: fake_window)
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=10, top=20, width=100, height=60),
    )

    image_bytes = controller.capture()

    assert called["hwnd"] == 654
    assert called["region"] == window_module.Region(left=10, top=20, width=100, height=60)
    assert image_bytes.startswith(b"\x89PNG")


def test_pyscreeze_matcher_returns_box(monkeypatch):
    import trail.runtime.operator as operator_module

    fake_module = SimpleNamespace(locate=lambda template, image, confidence=0.9: (1, 2, 3, 4))
    monkeypatch.setitem(sys.modules, "pyscreeze", fake_module)

    matcher = operator_module.PyScreezeMatcher()
    box = matcher.locate("demo.png", Image.new("RGB", (20, 20), color="white"))

    assert box == Box(left=1, top=2, width=3, height=4, source="demo.png")


def test_pyscreeze_matcher_returns_none_when_backend_reports_not_found(monkeypatch):
    import trail.runtime.operator as operator_module

    class FakeImageNotFound(Exception):
        pass

    def locate(template, image, confidence=0.9):
        raise FakeImageNotFound("missing")

    fake_module = SimpleNamespace(locate=locate, ImageNotFoundException=FakeImageNotFound)
    monkeypatch.setitem(sys.modules, "pyscreeze", fake_module)

    matcher = operator_module.PyScreezeMatcher()

    assert matcher.locate("demo.png", Image.new("RGB", (20, 20), color="white")) is None


def test_rapidocr_adapter_runs_backend(monkeypatch):
    import trail.runtime.operator as operator_module

    class FakeRapidOCR:
        def __call__(self, image, use_det=True, use_cls=False, use_rec=True):
            return (["ok"], None)

    fake_module = SimpleNamespace(RapidOCR=lambda config_path=None: FakeRapidOCR())
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_module)

    adapter = operator_module.RapidOcrAdapter()
    result = adapter.run(Image.new("RGB", (20, 20), color="white"))

    assert result == ["ok"]


def test_attach_window_returns_window_binding(monkeypatch):
    import trail.runtime.window as window_module

    fake_window = SimpleNamespace(title="Demo Window", _hWnd=321, left=10, top=20, width=100, height=50)
    fake_module = SimpleNamespace(getWindowsWithTitle=lambda title: [fake_window])
    monkeypatch.setitem(sys.modules, "pygetwindow", fake_module)

    binding = window_module.attach_window("Demo Window")

    assert binding == WindowBinding(title="Demo Window", hwnd=321)


def test_attach_window_raises_when_backend_missing(monkeypatch):
    import trail.runtime.window as window_module

    monkeypatch.setitem(sys.modules, "pygetwindow", None)

    with pytest.raises(TrailError) as exc_info:
        window_module.attach_window("Demo Window")

    assert exc_info.value.code == "WINDOW_NOT_FOUND"
