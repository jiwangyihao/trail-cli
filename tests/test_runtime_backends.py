from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.model import Box, WindowBinding


def test_enable_dpi_awareness_calls_win32_api(monkeypatch):
    import trail.runtime.window as window_module

    calls = []

    class User32:
        @staticmethod
        def SetProcessDPIAware():
            calls.append("SetProcessDPIAware")
            return 1

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module.ctypes, "windll", SimpleNamespace(user32=User32()))

    window_module._enable_dpi_awareness()

    assert calls == ["SetProcessDPIAware"]


def test_windows_window_controller_capture_uses_imagegrab(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    monkeypatch.setattr(window_module.sys, "platform", "linux")

    class FakeImageGrab:
        called_with = None

        @staticmethod
        def grab(*, bbox, all_screens=False):
            FakeImageGrab.called_with = (bbox, all_screens)
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

    assert FakeImageGrab.called_with == ((20, 32, 60, 68), False)
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_capture_falls_back_to_printwindow_when_window_grab_fails(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    class FakeImageGrab:
        called_with = None

        @staticmethod
        def grab(*, bbox=None, all_screens=False, window=None):
            FakeImageGrab.called_with = {"bbox": bbox, "all_screens": all_screens, "window": window}
            raise OSError("window grab unavailable")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    called = {}
    monkeypatch.setattr(
        window_module,
        "_capture_win32_window",
        lambda hwnd, region: called.update({"hwnd": hwnd, "region": region}) or Image.new("RGB", (10, 6), color="white"),
    )
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=1),
    )
    prepare_calls: list[str] = []
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=1))
    monkeypatch.setattr(controller, "prepare_input", lambda: prepare_calls.append("prepare"))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=10, top=20, width=100, height=60),
    )

    image_bytes = controller.capture(from_x=0.1, from_y=0.2, to_x=0.5, to_y=0.8)

    assert prepare_calls == []
    assert FakeImageGrab.called_with == {"bbox": None, "all_screens": False, "window": 1}
    assert called == {
        "hwnd": 1,
        "region": window_module.Region(left=20, top=32, width=40, height=36),
    }
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_capture_falls_back_to_bbox_grab_when_window_grab_and_printwindow_fail(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    class FakeImageGrab:
        calls: list[dict] = []

        @staticmethod
        def grab(*, bbox=None, all_screens=False, window=None):
            call = {"bbox": bbox, "all_screens": all_screens, "window": window}
            FakeImageGrab.calls.append(call)
            if window is not None:
                raise OSError("window grab unavailable")
            return Image.new("RGB", (10, 6), color="white")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(
        window_module,
        "_capture_win32_window",
        lambda hwnd, region: (_ for _ in ()).throw(TrailError("SCREENSHOT_FAILED", "无法截取窗口内容")),
    )
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=1),
    )
    prepare_calls: list[str] = []
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=1))
    monkeypatch.setattr(controller, "prepare_input", lambda: prepare_calls.append("prepare"))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=10, top=20, width=100, height=60),
    )

    image_bytes = controller.capture(from_x=0.1, from_y=0.2, to_x=0.5, to_y=0.8)

    assert prepare_calls == ["prepare"]
    assert FakeImageGrab.calls == [
        {"bbox": None, "all_screens": False, "window": 1},
        {"bbox": (20, 32, 60, 68), "all_screens": True, "window": None},
    ]
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_prefers_window_grab_when_hwnd_present(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    class FakeImageGrab:
        called_with = None

        @staticmethod
        def grab(*, bbox=None, all_screens=False, window=None):
            FakeImageGrab.called_with = {"bbox": bbox, "all_screens": all_screens, "window": window}
            return Image.new("RGB", (4, 4), color="white")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)

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

    assert FakeImageGrab.called_with == {"bbox": None, "all_screens": False, "window": 321}
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_uses_resolved_hwnd_when_binding_missing(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    class FakeImageGrab:
        called_with = None

        @staticmethod
        def grab(*, bbox=None, all_screens=False, window=None):
            FakeImageGrab.called_with = {"bbox": bbox, "all_screens": all_screens, "window": window}
            return Image.new("RGB", (4, 4), color="white")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)

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

    assert FakeImageGrab.called_with == {"bbox": None, "all_screens": False, "window": 654}
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


def test_runtime_operator_locate_retries_once_after_initial_miss():
    import trail.runtime.operator as operator_module

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

    class MatcherStub:
        def __init__(self):
            self.calls = 0

        def locate(self, template, image):
            self.calls += 1
            if self.calls == 1:
                return None
            return Box(left=1, top=2, width=3, height=4, source=template)

    matcher = MatcherStub()
    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=matcher,
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda *args, **kwargs: None),
    )

    box = runtime.locate("demo.png")

    assert matcher.calls == 2
    assert box == Box(left=1, top=2, width=3, height=4, source="demo.png")


def test_runtime_operator_prepares_window_before_input_actions():
    import trail.runtime.operator as operator_module

    calls: list[tuple] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input",))

    input_driver = SimpleNamespace(
        click=lambda x, y, **kwargs: calls.append(("click", x, y)),
        drag=lambda from_x, from_y, to_x, to_y: calls.append(("drag", from_x, from_y, to_x, to_y)),
        press=lambda key: calls.append(("press", key)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.click_point(10, 20)
    runtime.drag_to(1, 2, 3, 4)
    runtime.press_key("shift")

    assert calls == [
        ("prepare_input",),
        ("click", 10, 20),
        ("prepare_input",),
        ("drag", 1, 2, 3, 4),
        ("prepare_input",),
        ("press", "shift"),
    ]


def test_runtime_operator_prepares_window_before_hotkey_actions():
    import trail.runtime.operator as operator_module

    calls: list[tuple] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input",))

    input_driver = SimpleNamespace(
        click=lambda x, y, **kwargs: calls.append(("click", x, y)),
        drag=lambda from_x, from_y, to_x, to_y: calls.append(("drag", from_x, from_y, to_x, to_y)),
        press=lambda key: calls.append(("press", key)),
        hotkey=lambda *keys: calls.append(("hotkey", keys)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.hotkey("ctrl", "v")

    assert calls == [
        ("prepare_input",),
        ("hotkey", ("ctrl", "v")),
    ]


def test_runtime_operator_prepares_window_before_type_text_actions():
    import trail.runtime.operator as operator_module

    calls: list[tuple] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input",))

    input_driver = SimpleNamespace(
        ensure_available=lambda: calls.append(("ensure_available",)),
        click=lambda x, y, **kwargs: calls.append(("click", x, y)),
        drag=lambda from_x, from_y, to_x, to_y: calls.append(("drag", from_x, from_y, to_x, to_y)),
        press=lambda key: calls.append(("press", key)),
        hotkey=lambda *keys: calls.append(("hotkey", keys)),
        type_text=lambda text: calls.append(("type_text", text)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.type_text("##demo##")

    assert calls == [
        ("ensure_available",),
        ("prepare_input",),
        ("type_text", "##demo##"),
    ]


def test_runtime_operator_click_and_drag_translate_window_relative_pixels():
    import trail.runtime.operator as operator_module

    calls: list[tuple] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input",))

        def client_region(self):
            return operator_module.Region(left=100, top=200, width=1000, height=500)

    input_driver = SimpleNamespace(
        click=lambda x, y, **kwargs: calls.append(("click", x, y)),
        drag=lambda from_x, from_y, to_x, to_y: calls.append(("drag", from_x, from_y, to_x, to_y)),
        press=lambda key: calls.append(("press", key)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.click_point(10, 20)
    runtime.drag_to(1, 2, 30, 40)

    assert calls == [
        ("prepare_input",),
        ("click", 110, 220),
        ("prepare_input",),
        ("drag", 101, 202, 130, 240),
    ]


def test_runtime_operator_keeps_ratio_support_for_scene_commands():
    import trail.runtime.operator as operator_module

    calls: list[tuple] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input",))

        def client_region(self):
            return operator_module.Region(left=100, top=200, width=1000, height=500)

    input_driver = SimpleNamespace(
        click=lambda x, y, **kwargs: calls.append(("click", x, y)),
        drag=lambda from_x, from_y, to_x, to_y: calls.append(("drag", from_x, from_y, to_x, to_y)),
        press=lambda key: calls.append(("press", key)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.click_point(0.5, 0.25)
    runtime.drag_to(0.1, 0.2, 0.8, 0.6)

    assert calls == [
        ("prepare_input",),
        ("click", 600, 325),
        ("prepare_input",),
        ("drag", 200, 300, 900, 500),
    ]


def test_runtime_operator_locate_offsets_box_to_window_relative_pixels():
    import trail.runtime.operator as operator_module

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            raise AssertionError("not used")

        def client_region(self):
            return operator_module.Region(left=100, top=200, width=1000, height=500)

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: operator_module.Box(left=5, top=6, width=7, height=8, source=template)),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda *args, **kwargs: None),
    )

    box = runtime.locate("demo.png", from_x=0.1, from_y=0.2, to_x=0.5, to_y=0.6)

    assert box == operator_module.Box(left=105, top=106, width=7, height=8, source="demo.png")


def test_runtime_operator_locate_offsets_box_when_crop_uses_pixel_region():
    import trail.runtime.operator as operator_module

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            raise AssertionError("not used")

        def client_region(self):
            return operator_module.Region(left=100, top=200, width=1000, height=500)

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: operator_module.Box(left=5, top=6, width=7, height=8, source=template)),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda *args, **kwargs: None),
    )

    box = runtime.locate("demo.png", from_x=364, from_y=280, to_x=1689, to_y=334)

    assert box == operator_module.Box(left=369, top=286, width=7, height=8, source="demo.png")


def test_windows_window_controller_prepare_input_restores_and_activates_window(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    actions: list[str] = []

    fake_window = SimpleNamespace(
        title="Demo",
        _hWnd=321,
        isMinimized=True,
        isActive=False,
        restore=lambda: actions.append("restore"),
        activate=lambda: actions.append("activate"),
    )

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: fake_window)

    controller.prepare_input()

    assert actions == ["restore", "activate"]


def test_change_game_config_updates_channel_values(tmp_path):
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")
    config = tmp_path / "config.ini"
    config.write_text("channel=0\nsub_channel=0\n", encoding="utf-8")

    window_module.change_game_config(executable, channel=14, sub_channel=0)

    assert config.read_text(encoding="utf-8") == "channel=14\nsub_channel=0\n"


def test_launch_game_skips_when_process_already_running(tmp_path, monkeypatch):
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")

    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: process_name == "StarRail.exe")

    result = window_module.launch_game(game_path=executable, channel="official")

    assert result == {
        "started": False,
        "already_running": True,
        "path": str(executable),
        "channel": "official",
        "args": [],
    }


def test_launch_game_uses_popen_with_bilibili_channel(tmp_path, monkeypatch):
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")
    config = tmp_path / "config.ini"
    config.write_text("channel=0\nsub_channel=0\n", encoding="utf-8")
    calls: dict[str, object] = {}

    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)
    monkeypatch.setattr(
        window_module.subprocess,
        "Popen",
        lambda args, **kwargs: calls.update({"args": args, "kwargs": kwargs}) or SimpleNamespace(),
    )

    result = window_module.launch_game(
        game_path=executable,
        channel="bilibili",
        launch_args=["-popupwindow"],
        use_cmd=False,
    )

    assert result == {
        "started": True,
        "already_running": False,
        "path": str(executable),
        "channel": "bilibili",
        "args": ["-popupwindow"],
    }
    assert config.read_text(encoding="utf-8") == "channel=14\nsub_channel=0\n"
    assert calls == {
        "args": [str(executable), "-popupwindow"],
        "kwargs": {"cwd": str(tmp_path)},
    }


def test_launch_game_uses_cmd_start_when_requested(tmp_path, monkeypatch):
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")
    config = tmp_path / "config.ini"
    config.write_text("channel=0\nsub_channel=0\n", encoding="utf-8")
    calls: dict[str, object] = {}

    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)
    monkeypatch.setattr(
        window_module.subprocess,
        "Popen",
        lambda args, **kwargs: calls.update({"args": args, "kwargs": kwargs}) or SimpleNamespace(),
    )

    result = window_module.launch_game(
        game_path=executable,
        channel="official",
        launch_args=["-popupwindow"],
        use_cmd=True,
    )

    assert result == {
        "started": True,
        "already_running": False,
        "path": str(executable),
        "channel": "official",
        "args": ["-popupwindow"],
    }
    assert calls == {
        "args": ["cmd", "/c", "start", "", str(executable), "-popupwindow"],
        "kwargs": {"cwd": str(tmp_path)},
    }


def test_runtime_operator_warns_when_window_not_foreground_after_input():
    import trail.runtime.operator as operator_module

    class WindowStub:
        def __init__(self):
            self.prepare_calls = 0

        def capture(self, **kwargs):
            del kwargs
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            self.prepare_calls += 1

        def is_foreground(self):
            return False

        def to_screen_point(self, x, y):
            return x, y

    clicks: list[tuple[int, int]] = []
    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=SimpleNamespace(
            ensure_available=lambda: None,
            click=lambda x, y, **kwargs: clicks.append((x, y)),
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    runtime.click_point(10, 20)

    assert clicks == [(10, 20)]
    assert runtime.collect_warnings() == [
        {
            "code": "WINDOW_NOT_FOREGROUND",
            "message": "输入命令执行后窗口不在前台，本次操作可能失败；可能是窗口未在前台，或拉回前台失败",
        }
    ]


def test_runtime_operator_matches_reference_images_from_project_tree(tmp_path, monkeypatch):
    import trail.runtime.operator as operator_module

    monkeypatch.chdir(tmp_path)
    reference_dir = tmp_path / "trail" / "scenes" / "cw" / "references"
    reference_dir.mkdir(parents=True)

    shot_path = tmp_path / "shot.png"
    Image.new("RGB", (32, 32), color="red").save(shot_path)
    Image.new("RGB", (32, 32), color="red").save(reference_dir / "1.png")
    Image.new("RGB", (32, 32), color="blue").save(reference_dir / "2.png")

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: None, capture_to_workspace=lambda: shot_path, prepare_input=lambda: None),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=SimpleNamespace(
            ensure_available=lambda: None,
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    matches = runtime.match_references(shot_path)

    assert [Path(match["path"]).name for match in matches] == ["1.png", "2.png"]
    assert matches[0]["similarity"] > matches[1]["similarity"]


def test_pyautogui_input_driver_click_uses_win32_cursor_for_virtual_screen_coords(monkeypatch):
    import trail.runtime.operator as operator_module

    class User32:
        def __init__(self):
            self.calls: list[tuple[str, tuple[int, ...]]] = []

        def SetCursorPos(self, x: int, y: int):
            self.calls.append(("SetCursorPos", (x, y)))
            return 1

        def mouse_event(self, flags: int, dx: int, dy: int, data: int, extra: int):
            self.calls.append(("mouse_event", (flags, dx, dy, data, extra)))
            return 1

    user32 = User32()
    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=user32))

    driver = operator_module.PyAutoGuiInputDriver()
    driver.click(1408, -1196)

    assert user32.calls == [
        ("SetCursorPos", (1408, -1196)),
        ("mouse_event", (0x0002, 0, 0, 0, 0)),
        ("mouse_event", (0x0004, 0, 0, 0, 0)),
    ]


def test_pyautogui_input_driver_drag_uses_win32_cursor_for_virtual_screen_coords(monkeypatch):
    import trail.runtime.operator as operator_module

    class User32:
        def __init__(self):
            self.calls: list[tuple[str, tuple[int, ...]]] = []

        def SetCursorPos(self, x: int, y: int):
            self.calls.append(("SetCursorPos", (x, y)))
            return 1

        def mouse_event(self, flags: int, dx: int, dy: int, data: int, extra: int):
            self.calls.append(("mouse_event", (flags, dx, dy, data, extra)))
            return 1

    user32 = User32()
    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=user32))

    driver = operator_module.PyAutoGuiInputDriver()
    driver.drag(1408, -1196, 1410, -1190)

    assert user32.calls == [
        ("SetCursorPos", (1408, -1196)),
        ("mouse_event", (0x0002, 0, 0, 0, 0)),
        ("SetCursorPos", (1410, -1190)),
        ("mouse_event", (0x0004, 0, 0, 0, 0)),
    ]
