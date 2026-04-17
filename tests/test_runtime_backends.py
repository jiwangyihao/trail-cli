from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.model import Box, WindowBinding
from tests.support.fake_daemon import FakeDaemonClient, build_success_response


class _CommandRuntimeStub:
    def __init__(self):
        self._shot = Path(".trail/shots/daemon-command.png")
        self.ocr_result = [{"text": "银狼"}]
        self.locate_result = {"left": 1, "top": 2, "width": 3, "height": 4}
        self.wait_result = {"left": 5, "top": 6, "width": 7, "height": 8}
        self.warnings: list[dict] = []
        self.references: list[dict] = []
        self.trace: list[dict] = []
        self.clicks: list[tuple[int, int]] = []
        self.drags: list[tuple[int, int, int, int]] = []
        self.keys: list[tuple[str, int]] = []

    def capture_after_action(self, optional: bool = False):
        return self._shot

    def collect_warnings(self):
        warnings = list(self.warnings)
        self.warnings.clear()
        return warnings

    def match_references(self, screenshot_path, limit: int = 3):
        del screenshot_path, limit
        return list(self.references)

    def consume_debug_trace(self):
        trace = list(self.trace)
        self.trace.clear()
        return trace

    def ocr(self, **kwargs):
        del kwargs
        return self.ocr_result

    def locate(self, template: str, **kwargs):
        del template, kwargs
        return self.locate_result

    def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
        del template, timeout, interval
        return self.wait_result

    def click_point(self, x: int, y: int, **kwargs):
        del kwargs
        self.clicks.append((x, y))

    def drag_to(self, from_x: int, from_y: int, to_x: int, to_y: int):
        self.drags.append((from_x, from_y, to_x, to_y))

    def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
        del interval
        self.keys.append((key, presses))


class _CommandRuntimeServiceStub:
    def __init__(self, runtime: _CommandRuntimeStub):
        self.runtime = runtime
        self.runtime_calls: list[dict[str, object]] = []
        self.attach_calls: list[str] = []
        self.launch_calls: list[dict[str, object]] = []

    def get_runtime(self, *, workspace_root: str, window_binding: dict | None):
        self.runtime_calls.append({"workspace_root": workspace_root, "window_binding": window_binding})
        return self.runtime

    def attach_window(self, *, window_title: str):
        self.attach_calls.append(window_title)
        return {"title": window_title, "hwnd": 321}

    def launch_game(self, **payload):
        self.launch_calls.append(dict(payload))
        return {
            "started": True,
            "already_running": False,
            "path": payload["game_path"],
            "channel": payload["channel"],
            "args": list(payload.get("launch_args", [])),
        }


def _command_request(
    *,
    workspace_root: Path,
    method: str,
    payload: dict | None = None,
    session_id: str | None = None,
    verbose: bool = False,
    request_id: str | None = None,
):
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION

    return DaemonRequest(
        request_id=request_id or f"req-{method}",
        protocol_version=PROTOCOL_VERSION,
        workspace_root=str(workspace_root),
        session_id=session_id,
        verbose=verbose,
        method=method,
        payload=payload or {},
    )


def test_command_service_handles_window_methods(tmp_path: Path):
    from trail.daemon.command_service import CommandService

    runtime = _CommandRuntimeStub()
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    attach_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="window.attach",
            payload={"window_title": "Demo Window"},
        )
    )
    launch_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="window.launch",
            payload={
                "game_path": str(tmp_path / "StarRail.exe"),
                "channel": "bilibili",
                "launch_args": ["-popupwindow"],
                "use_cmd": True,
            },
        )
    )

    assert attach_payload["ok"] is True
    assert attach_payload["data"] == {"title": "Demo Window", "hwnd": 321}
    assert attach_payload["request_id"] == "req-window.attach"
    assert attach_payload["screenshot"] == ".trail/shots/daemon-command.png"
    assert launch_payload["ok"] is True
    assert launch_payload["data"] == {
        "started": True,
        "already_running": False,
        "path": str(tmp_path / "StarRail.exe"),
        "channel": "bilibili",
        "args": ["-popupwindow"],
    }
    assert launch_payload["request_id"] == "req-window.launch"
    assert launch_payload["screenshot"] is None
    assert runtime_service.attach_calls == ["Demo Window"]
    assert runtime_service.launch_calls == [
        {
            "game_path": str(tmp_path / "StarRail.exe"),
            "channel": "bilibili",
            "launch_args": ["-popupwindow"],
            "use_cmd": True,
        }
    ]
    assert runtime_service.runtime_calls == [
        {
            "workspace_root": str(tmp_path),
            "window_binding": {"title": "Demo Window", "hwnd": 321},
        }
    ]


def test_command_service_handles_screen_ocr_and_image_methods(tmp_path: Path):
    from trail.daemon.command_service import CommandService

    runtime = _CommandRuntimeStub()
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    screen_payload = service.handle(_command_request(workspace_root=tmp_path, method="screen.shot"))
    ocr_payload = service.handle(_command_request(workspace_root=tmp_path, method="ocr.read"))
    locate_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="image.locate",
            payload={"template": "demo.png"},
        )
    )
    wait_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="image.wait",
            payload={"template": "demo.png", "timeout": 12},
        )
    )

    assert screen_payload["ok"] is True
    assert screen_payload["data"] == {"captured": True}
    assert screen_payload["request_id"] == "req-screen.shot"
    assert ocr_payload["ok"] is True
    assert ocr_payload["data"] == {"result": [{"text": "银狼"}]}
    assert ocr_payload["request_id"] == "req-ocr.read"
    assert locate_payload["ok"] is True
    assert locate_payload["data"] == {"box": {"left": 1, "top": 2, "width": 3, "height": 4}}
    assert locate_payload["request_id"] == "req-image.locate"
    assert wait_payload["ok"] is True
    assert wait_payload["data"] == {"box": {"left": 5, "top": 6, "width": 7, "height": 8}}
    assert wait_payload["request_id"] == "req-image.wait"
    assert runtime_service.runtime_calls == [
        {"workspace_root": str(tmp_path), "window_binding": None},
        {"workspace_root": str(tmp_path), "window_binding": None},
        {"workspace_root": str(tmp_path), "window_binding": None},
        {"workspace_root": str(tmp_path), "window_binding": None},
    ]


def test_command_service_handles_input_methods_and_verbose_metadata(tmp_path: Path):
    from trail.daemon.command_service import CommandService
    from trail.daemon.session_service import SessionServiceRegistry

    runtime = _CommandRuntimeStub()
    runtime.warnings = [
        {
            "code": "WINDOW_NOT_FOREGROUND",
            "message": "输入命令执行后窗口不在前台，本次操作可能失败",
        }
    ]
    runtime.references = [{"path": "trail/scenes/cw/references/1-1.png", "similarity": 0.88}]
    runtime.trace = [{"step": "click", "point": [10, 20]}]
    runtime_service = _CommandRuntimeServiceStub(runtime)
    session_registry = SessionServiceRegistry()
    session_service = session_registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "Demo Window", "hwnd": 321})
    service = CommandService(runtime_service=runtime_service, session_service=session_registry)

    click_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="input.click",
            payload={"x": 10, "y": 20},
            session_id=session.session_id,
            verbose=True,
        )
    )
    drag_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="input.drag",
            payload={"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
            session_id=session.session_id,
        )
    )
    key_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="input.key",
            payload={"key": "space", "presses": 2},
            session_id=session.session_id,
        )
    )
    click_status = session_service.request_status("req-input.click")

    assert click_payload["ok"] is True
    assert click_payload["data"] == {"clicked": [10, 20]}
    assert click_payload["warnings"] == [
        {
            "code": "WINDOW_NOT_FOREGROUND",
            "message": "输入命令执行后窗口不在前台，本次操作可能失败",
        }
    ]
    assert click_payload["references"] == [
        {
            "path": "trail/scenes/cw/references/1-1.png",
            "similarity": 0.88,
            "screenshot": ".trail/shots/daemon-command.png",
        }
    ]
    assert click_payload["debug"] == {"trace": [{"step": "click", "point": [10, 20]}]}
    assert click_payload["request_id"] == "req-input.click"
    assert drag_payload["ok"] is True
    assert drag_payload["data"] == {"dragged": [1, 2, 3, 4]}
    assert drag_payload["request_id"] == "req-input.drag"
    assert key_payload["ok"] is True
    assert key_payload["data"] == {"key": "space", "presses": 2}
    assert key_payload["request_id"] == "req-input.key"
    assert click_status["session_id"] == session.session_id
    assert click_status["final_state"] == "completed"
    assert click_status["last_visible_stage"] == "responded"
    assert runtime.clicks == [(10, 20)]
    assert runtime.drags == [(1, 2, 3, 4)]
    assert runtime.keys == [("space", 2)]
    assert runtime_service.runtime_calls == [
        {"workspace_root": str(tmp_path), "window_binding": None},
        {"workspace_root": str(tmp_path), "window_binding": None},
        {"workspace_root": str(tmp_path), "window_binding": None},
    ]


def test_command_service_returns_structured_errors_for_missing_ocr_and_images(tmp_path: Path):
    from trail.daemon.command_service import CommandService

    runtime = _CommandRuntimeStub()
    runtime.ocr_result = []
    runtime.locate_result = None
    runtime.wait_result = None
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    ocr_payload = service.handle(_command_request(workspace_root=tmp_path, method="ocr.read"))
    locate_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="image.locate",
            payload={"template": "missing.png"},
        )
    )
    wait_payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="image.wait",
            payload={"template": "missing.png", "timeout": 5},
        )
    )

    assert ocr_payload["ok"] is False
    assert ocr_payload["error"] == {"code": "OCR_NO_RESULT", "message": "OCR 无结果"}
    assert ocr_payload["request_id"] == "req-ocr.read"
    assert locate_payload["ok"] is False
    assert locate_payload["error"] == {"code": "IMAGE_NOT_FOUND", "message": "未找到 missing.png"}
    assert locate_payload["request_id"] == "req-image.locate"
    assert wait_payload["ok"] is False
    assert wait_payload["error"] == {"code": "IMAGE_NOT_FOUND", "message": "未找到 missing.png"}
    assert wait_payload["request_id"] == "req-image.wait"


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


def test_enable_dpi_awareness_prefers_per_monitor_v2_when_available(monkeypatch):
    import trail.runtime.window as window_module

    calls = []

    class User32:
        @staticmethod
        def SetProcessDpiAwarenessContext(value):
            calls.append(("SetProcessDpiAwarenessContext", value))
            return 1

        @staticmethod
        def SetProcessDPIAware():
            calls.append(("SetProcessDPIAware", None))
            return 1

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module.ctypes, "windll", SimpleNamespace(user32=User32()))

    window_module._enable_dpi_awareness()

    assert calls == [("SetProcessDpiAwarenessContext", -4)]


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
    monkeypatch.setattr(window_module, "_capture_with_windows_capture", lambda hwnd, region: (_ for _ in ()).throw(OSError("graphics capture unavailable")))
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
            if len(FakeImageGrab.calls) == 1:
                raise OSError("bbox grab unavailable")
            return Image.new("RGB", (10, 6), color="white")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module, "_capture_with_windows_capture", lambda hwnd, region: (_ for _ in ()).throw(OSError("graphics capture unavailable")))
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
        {"bbox": (20, 32, 60, 68), "all_screens": True, "window": None},
        {"bbox": None, "all_screens": False, "window": 1},
        {"bbox": (20, 32, 60, 68), "all_screens": True, "window": None},
    ]
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_prefers_bbox_grab_for_live_capture_when_hwnd_present(monkeypatch, tmp_path):
    import trail.runtime.window as window_module
    from PIL import Image

    class FakeImageGrab:
        calls: list[dict] = []

        @staticmethod
        def grab(*, bbox=None, all_screens=False, window=None):
            FakeImageGrab.calls.append({"bbox": bbox, "all_screens": all_screens, "window": window})
            if window is not None:
                return Image.new("RGB", (1920, 1080), color="black")
            return Image.new("RGB", (2688, 1512), color="white")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module, "_capture_with_windows_capture", lambda hwnd, region: (_ for _ in ()).throw(OSError("graphics capture unavailable")))
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)
    monkeypatch.setattr(window_module, "_capture_win32_window", lambda hwnd, region: Image.new("RGB", (4, 4), color="black"))

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
    image = Image.open(BytesIO(image_bytes))

    assert FakeImageGrab.calls == [
        {"bbox": (10, 20, 110, 80), "all_screens": True, "window": None},
        {"bbox": None, "all_screens": False, "window": 321},
    ]
    assert image.size == (1920, 1080)
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_scales_bbox_capture_using_window_dpi(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    class User32:
        def GetDpiForWindow(self, hwnd: int):
            return 120

    class FakeImageGrab:
        calls: list[dict] = []

        @staticmethod
        def grab(*, bbox=None, all_screens=False, window=None):
            FakeImageGrab.calls.append({"bbox": bbox, "all_screens": all_screens, "window": window})
            if window is not None:
                return Image.new("RGB", (1920, 1080), color="black")
            return Image.new("RGB", (1920, 1080), color="white")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module, "_capture_with_windows_capture", lambda hwnd, region: (_ for _ in ()).throw(OSError("graphics capture unavailable")))
    monkeypatch.setattr(window_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)
    monkeypatch.setattr(window_module, "_capture_win32_window", lambda hwnd, region: Image.new("RGB", (4, 4), color="black"))

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=321))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=256, top=-992, width=1536, height=864),
    )

    image_bytes = controller.capture()
    image = Image.open(BytesIO(image_bytes))

    assert FakeImageGrab.calls == [
        {"bbox": (320, -1240, 2240, -160), "all_screens": True, "window": None},
        {"bbox": None, "all_screens": False, "window": 321},
    ]
    assert image.size == (1920, 1080)
    assert image_bytes.startswith(b"\x89PNG")


def test_target_capture_size_uses_scaled_client_region(monkeypatch):
    import trail.runtime.window as window_module

    class User32:
        def GetDpiForWindow(self, hwnd: int):
            return 168

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module.ctypes, "windll", SimpleNamespace(user32=User32()))

    target = window_module._target_capture_size(window_module.Region(left=183, top=160, width=1097, height=617), 321)

    assert target == (1920, 1080)


def test_windows_window_controller_uses_resolved_hwnd_when_binding_missing(monkeypatch, tmp_path):
    import trail.runtime.window as window_module
    from PIL import Image

    class FakeImageGrab:
        calls: list[dict] = []

        @staticmethod
        def grab(*, bbox=None, all_screens=False, window=None):
            FakeImageGrab.calls.append({"bbox": bbox, "all_screens": all_screens, "window": window})
            if window is not None:
                return Image.new("RGB", (1920, 1080), color="black")
            return Image.new("RGB", (2688, 1512), color="white")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module, "_capture_with_windows_capture", lambda hwnd, region: (_ for _ in ()).throw(OSError("graphics capture unavailable")))
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)
    monkeypatch.setattr(window_module, "_capture_win32_window", lambda hwnd, region: Image.new("RGB", (4, 4), color="black"))

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
    image = Image.open(BytesIO(image_bytes))

    assert FakeImageGrab.calls == [
        {"bbox": (10, 20, 110, 80), "all_screens": True, "window": None},
        {"bbox": None, "all_screens": False, "window": 654},
    ]
    assert image.size == (1920, 1080)
    assert image_bytes.startswith(b"\x89PNG")


def test_window_capture_crop_box_maps_client_region_into_frame_space():
    import trail.runtime.window as window_module

    window_region = window_module.Region(left=176, top=130, width=1111, height=654)
    client_region = window_module.Region(left=183, top=160, width=1097, height=617)

    crop = window_module._window_capture_crop_box((1920, 1080), window_region, client_region)

    assert crop == (12, 50, 1908, 1068)


def test_windows_window_controller_uses_windows_capture_backend_when_available(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    class FakeImageGrab:
        called = False

        @staticmethod
        def grab(*args, **kwargs):
            FakeImageGrab.called = True
            return Image.new("RGB", (1, 1), color="black")

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)
    monkeypatch.setattr(
        window_module,
        "_capture_with_windows_capture",
        lambda hwnd, region: Image.new("RGB", (1920, 1080), color="white"),
    )

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=321))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=183, top=160, width=1097, height=617),
    )

    image_bytes = controller.capture()
    image = Image.open(BytesIO(image_bytes))

    assert FakeImageGrab.called is False
    assert image.size == (1920, 1080)


def test_get_windows_capture_session_reuses_live_session(monkeypatch):
    import trail.runtime.window as window_module

    created: list[int] = []
    window_module._WINDOWS_CAPTURE_SESSIONS.clear()

    class FakeSession:
        def __init__(self, hwnd: int):
            created.append(hwnd)
            self.hwnd = hwnd

        def is_finished(self) -> bool:
            return False

    monkeypatch.setattr(window_module, "_WindowsCaptureSession", FakeSession)

    first = window_module._get_windows_capture_session(321)
    second = window_module._get_windows_capture_session(321)

    assert first is second
    assert created == [321]


def test_get_windows_capture_session_recreates_finished_session(monkeypatch):
    import trail.runtime.window as window_module

    created: list[int] = []
    window_module._WINDOWS_CAPTURE_SESSIONS.clear()

    class FakeSession:
        def __init__(self, hwnd: int):
            created.append(hwnd)
            self.hwnd = hwnd
            self._finished = len(created) == 1

        def is_finished(self) -> bool:
            return self._finished

    monkeypatch.setattr(window_module, "_WindowsCaptureSession", FakeSession)

    first = window_module._get_windows_capture_session(321)
    second = window_module._get_windows_capture_session(321)

    assert first is not second
    assert created == [321, 321]


def test_capture_to_workspace_uses_request_id_filename(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "capture", lambda **kwargs: b"demo-bytes")

    path = controller.capture_to_workspace(request_id="req-123")

    assert path.parent == tmp_path
    assert path.name.startswith("req-123-")
    assert path.suffix == ".png"
    assert path.read_bytes() == b"demo-bytes"


def test_capture_to_workspace_distinguishes_colliding_request_ids_and_avoids_reserved_names(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "capture", lambda **kwargs: b"demo-bytes")

    plain = controller.capture_to_workspace(request_id="escape")
    parent = controller.capture_to_workspace(request_id="../escape")
    backslash = controller.capture_to_workspace(request_id=r"..\escape")
    device = controller.capture_to_workspace(request_id="CON")

    assert plain.resolve().parent == tmp_path.resolve()
    assert parent.resolve().parent == tmp_path.resolve()
    assert backslash.resolve().parent == tmp_path.resolve()
    assert len({plain.name, parent.name, backslash.name}) == 3
    assert device.resolve().parent == tmp_path.resolve()
    assert device.stem.upper() != "CON"
    assert device.read_bytes() == b"demo-bytes"


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
    monkeypatch.setattr(window_module.sys, "platform", "linux")

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


def test_windows_window_controller_prepare_input_uses_win32_foreground_apis_when_hwnd_present(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    calls: list[tuple[str, int, int | None]] = []

    class User32:
        def ShowWindow(self, hwnd: int, command: int):
            calls.append(("ShowWindow", hwnd, command))
            return 1

        def BringWindowToTop(self, hwnd: int):
            calls.append(("BringWindowToTop", hwnd, None))
            return 1

        def SetForegroundWindow(self, hwnd: int):
            calls.append(("SetForegroundWindow", hwnd, None))
            return 1

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(window_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    fake_window = SimpleNamespace(title="Demo", _hWnd=321, isMinimized=True, isActive=False)
    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: fake_window)

    controller.prepare_input()

    assert calls == [
        ("ShowWindow", 321, 9),
        ("BringWindowToTop", 321, None),
        ("SetForegroundWindow", 321, None),
    ]


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


def test_runtime_operator_capture_after_action_passes_request_id_to_window():
    import trail.runtime.operator as operator_module

    class WindowStub:
        def __init__(self):
            self.request_ids: list[str | None] = []

        def capture(self, **kwargs):
            del kwargs
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self, request_id=None):
            self.request_ids.append(request_id)
            return Path(f".trail/shots/{request_id}.png")

        def prepare_input(self):
            raise AssertionError("not used")

    window = WindowStub()
    runtime = operator_module.RuntimeOperator(
        window=window,
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda key: None),
    )

    path = runtime.capture_after_action(request_id="req-operator")

    assert path == Path(".trail/shots/req-operator.png")
    assert window.request_ids == ["req-operator"]


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


def test_runtime_operator_matches_reference_images_from_workspace_root_when_cwd_differs(tmp_path, monkeypatch):
    import trail.runtime.operator as operator_module

    workspace_root = tmp_path / "workspace"
    other_cwd = tmp_path / "daemon-home"
    reference_dir = workspace_root / "trail" / "scenes" / "cw" / "references"
    reference_dir.mkdir(parents=True)
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    shot_path = workspace_root / ".trail" / "shots" / "shot.png"
    shot_path.parent.mkdir(parents=True)
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
        reference_root=workspace_root,
    )

    matches = runtime.match_references(shot_path)

    assert [match["path"] for match in matches] == [
        "trail/scenes/cw/references/1.png",
        "trail/scenes/cw/references/2.png",
    ]
    assert matches[0]["similarity"] > matches[1]["similarity"]


def test_runtime_service_builds_runtime_with_workspace_reference_root(tmp_path, monkeypatch):
    from trail.daemon.runtime_service import RuntimeService

    workspace_root = tmp_path / "workspace"
    captured: list[dict[str, object]] = []
    built_runtime = object()

    monkeypatch.setattr(
        "trail.runtime.operator.build_runtime",
        lambda **kwargs: captured.append(kwargs) or built_runtime,
    )

    runtime = RuntimeService().get_runtime(workspace_root=str(workspace_root), window_binding=None)

    assert runtime is built_runtime
    assert captured == [
        {
            "workspace": workspace_root / ".trail" / "shots",
            "window_title": "崩坏：星穹铁道",
            "window_binding": None,
            "reference_root": workspace_root,
        }
    ]


def test_command_service_binds_references_to_current_screenshot(tmp_path: Path):
    from trail.daemon.command_service import CommandService

    runtime = _CommandRuntimeStub()
    runtime._shot = tmp_path / ".trail" / "shots" / "req-ocr-read.png"
    runtime.references = [
        {
            "path": str(tmp_path / "trail" / "scenes" / "cw" / "references" / "1-1.png"),
            "similarity": 0.88,
            "screenshot": "stale.png",
        }
    ]
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    payload = service.handle(_command_request(workspace_root=tmp_path, method="ocr.read"))

    assert type(payload["screenshot"]) is str
    assert type(payload["references"][0]) is dict
    assert payload["screenshot"] == ".trail/shots/req-ocr-read.png"
    assert payload["references"] == [
        {
            "path": "trail/scenes/cw/references/1-1.png",
            "similarity": 0.88,
            "screenshot": ".trail/shots/req-ocr-read.png",
        }
    ]


def test_command_service_normalizes_workspace_absolute_screenshot_and_rebinds_references(tmp_path: Path):
    from trail.daemon.command_service import CommandService

    runtime = _CommandRuntimeStub()
    runtime._shot = tmp_path / "input-fail.png"
    runtime.references = [
        {
            "path": str(tmp_path / "trail" / "scenes" / "cw" / "references" / "1-1.png"),
            "similarity": 0.88,
            "screenshot": "stale.png",
        }
    ]
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    payload = service.handle(_command_request(workspace_root=tmp_path, method="ocr.read"))

    assert type(payload["screenshot"]) is str
    assert type(payload["references"][0]) is dict
    assert payload["screenshot"] == "input-fail.png"
    assert payload["references"] == [
        {
            "path": "trail/scenes/cw/references/1-1.png",
            "similarity": 0.88,
            "screenshot": "input-fail.png",
        }
    ]


def test_command_service_capture_chain_keeps_request_scoped_screenshot_inside_workspace(monkeypatch, tmp_path: Path):
    from trail.daemon.command_service import CommandService
    from trail.runtime.operator import RuntimeOperator
    import trail.runtime.window as window_module

    controller = window_module.WindowsWindowController(
        workspace=tmp_path / ".trail" / "shots",
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "capture", lambda **kwargs: b"demo-bytes")
    runtime = RuntimeOperator(
        window=controller,
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
        reference_root=tmp_path,
    )
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    service = CommandService(runtime_service=runtime_service)

    payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="screen.shot",
            request_id=r"..\CON",
        )
    )

    assert payload["screenshot"] is not None
    assert type(payload["screenshot"]) is str
    screenshot = Path(payload["screenshot"])
    screenshot_path = tmp_path / screenshot

    assert payload["screenshot"].startswith(".trail/shots/")
    assert ".." not in screenshot.parts
    assert screenshot_path.stem.upper() != "CON"
    assert screenshot_path.exists()
    assert screenshot_path.resolve().parent == (tmp_path / ".trail" / "shots").resolve()
    assert screenshot_path.read_bytes() == b"demo-bytes"


def test_fake_daemon_client_moves_request_id_into_debug_when_verbose():
    client = FakeDaemonClient(
        responses={
            "input.click": build_success_response(
                request_id="req-fake-verbose",
                data={"clicked": [10, 20]},
                screenshot=".trail/shots/req-fake-verbose.png",
            )
        }
    )

    payload = client.call(
        method="input.click",
        payload={"x": 10, "y": 20},
        workspace_root="C:/repo",
        session_id=None,
        verbose=True,
    )

    assert payload == {
        "ok": True,
        "data": {"clicked": [10, 20]},
        "screenshot": ".trail/shots/req-fake-verbose.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-fake-verbose"},
        "error": None,
    }


def test_fake_daemon_client_preserves_request_id_in_debug_for_daemon_errors():
    client = FakeDaemonClient(
        responses={
            "screen.shot": {
                "request_id": "req-fake-daemon-error",
                "ok": False,
                "data": {},
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": {"detail": "bootstrap missing"},
                "error": {
                    "code": "DAEMON_BOOTSTRAP_REQUIRED",
                    "message": "daemon bootstrap not installed",
                },
            }
        }
    )

    payload = client.call(
        method="screen.shot",
        payload={},
        workspace_root="C:/repo",
        session_id=None,
        verbose=False,
    )

    assert payload == {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "detail": "bootstrap missing",
            "request_id": "req-fake-daemon-error",
        },
        "error": {
            "code": "DAEMON_BOOTSTRAP_REQUIRED",
            "message": "daemon bootstrap not installed",
        },
    }


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


def test_pyautogui_input_driver_click_uses_win32_cursor_for_primary_coords_on_windows(monkeypatch):
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

    backend_calls: list[tuple[str, tuple[int, ...]]] = []
    user32 = User32()
    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=user32))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: SimpleNamespace(click=lambda x, y: backend_calls.append(("click", (x, y))))),
    )

    driver = operator_module.PyAutoGuiInputDriver()
    driver.click(960, 1014)

    assert backend_calls == []
    assert user32.calls == [
        ("SetCursorPos", (960, 1014)),
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


def test_pyautogui_input_driver_drag_uses_win32_cursor_for_primary_coords_on_windows(monkeypatch):
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

    backend_calls: list[tuple[str, tuple[int, ...]]] = []
    user32 = User32()
    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=user32))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(
            lambda: SimpleNamespace(
                moveTo=lambda x, y: backend_calls.append(("moveTo", (x, y))),
                dragTo=lambda x, y, duration=0.5: backend_calls.append(("dragTo", (x, y))),
            )
        ),
    )

    driver = operator_module.PyAutoGuiInputDriver()
    driver.drag(960, 1014, 1000, 1020)

    assert backend_calls == []
    assert user32.calls == [
        ("SetCursorPos", (960, 1014)),
        ("mouse_event", (0x0002, 0, 0, 0, 0)),
        ("SetCursorPos", (1000, 1020)),
        ("mouse_event", (0x0004, 0, 0, 0, 0)),
    ]


def test_pyautogui_input_driver_press_uses_win32_key_events_on_windows(monkeypatch):
    import trail.runtime.operator as operator_module

    class User32:
        def __init__(self):
            self.calls: list[tuple[str, tuple[int, int, int, int]]] = []

        def keybd_event(self, key_code: int, scan_code: int, flags: int, extra: int):
            self.calls.append(("keybd_event", (key_code, scan_code, flags, extra)))
            return 1

    backend_calls: list[str] = []
    user32 = User32()
    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=user32))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: SimpleNamespace(press=lambda key: backend_calls.append(key))),
    )

    driver = operator_module.PyAutoGuiInputDriver()
    driver.press("esc")

    assert backend_calls == []
    assert user32.calls == [
        ("keybd_event", (0x1B, 0, 0, 0)),
        ("keybd_event", (0x1B, 0, 0x0002, 0)),
    ]
