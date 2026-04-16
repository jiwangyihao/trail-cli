from __future__ import annotations

import ctypes
from io import BytesIO
from pathlib import Path
import sys
from time import monotonic, sleep
from typing import Any, Protocol

from PIL import Image, ImageChops, ImageOps, ImageStat

from trail.core.errors import TrailError
from trail.runtime.model import Box, Region
from trail.runtime.window import WindowsWindowController


class WindowController(Protocol):
    def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None): ...
    def capture_to_workspace(self, request_id: str | None = None) -> Path: ...
    def prepare_input(self) -> None: ...
    def is_foreground(self) -> bool: ...
    def client_region(self): ...
    def to_screen_point(self, x: int | float, y: int | float) -> tuple[int, int]: ...


class ImageMatcher(Protocol):
    def locate(self, template: str, image) -> Box | None: ...


class OcrEngine(Protocol):
    def run(self, image) -> list[Any]: ...


class InputDriver(Protocol):
    def ensure_available(self) -> None: ...
    def click(self, x: float, y: float, **kwargs) -> None: ...
    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None: ...
    def press(self, key: str) -> None: ...
    def hotkey(self, *keys: str) -> None: ...
    def type_text(self, text: str) -> None: ...


class RuntimeOperator:
    def __init__(
        self,
        window: WindowController,
        matcher: ImageMatcher,
        ocr_engine: OcrEngine,
        input_driver: InputDriver,
        *,
        reference_root: Path | None = None,
    ):
        self.window = window
        self.matcher = matcher
        self.ocr_engine = ocr_engine
        self.input = input_driver
        self.reference_root = Path.cwd() if reference_root is None else Path(reference_root)
        self._warnings: list[dict[str, Any]] = []
        self._trace: list[dict[str, Any]] = []

    def _record_trace(self, step: str, **payload: Any) -> None:
        self._trace.append({"step": step, **payload})

    @staticmethod
    def _serialize_box(box: Box | None) -> dict[str, Any] | None:
        if box is None:
            return None
        return {
            "left": box.left,
            "top": box.top,
            "width": box.width,
            "height": box.height,
            "source": box.source,
        }

    def _check_foreground_after_input(self) -> None:
        is_foreground = getattr(self.window, "is_foreground", None)
        if not callable(is_foreground):
            return
        try:
            foreground = bool(is_foreground())
        except Exception:
            return
        self._record_trace("foreground_check", foreground=foreground)
        if foreground:
            return
        self._warnings.append(
            {
                "code": "WINDOW_NOT_FOREGROUND",
                "message": "输入命令执行后窗口不在前台，本次操作可能失败；可能是窗口未在前台，或拉回前台失败",
            }
        )

    def collect_warnings(self) -> list[dict[str, Any]]:
        warnings = list(self._warnings)
        self._warnings.clear()
        return warnings

    def consume_debug_trace(self) -> list[dict[str, Any]]:
        trace = list(self._trace)
        self._trace.clear()
        return trace

    def match_references(self, screenshot_path: Path | str, limit: int = 3) -> list[dict[str, Any]]:
        return _match_reference_images(Path(screenshot_path), limit=limit, reference_root=self.reference_root)

    def screenshot(self, *, from_x=None, from_y=None, to_x=None, to_y=None):
        return self.window.capture(from_x=from_x, from_y=from_y, to_x=to_x, to_y=to_y)

    def locate(self, template: str, **kwargs):
        image = self.screenshot(**kwargs)
        box = self.matcher.locate(template, image)
        if box is not None:
            resolved = self._offset_box(box, **kwargs)
            self._record_trace("locate", template=template, kwargs=dict(kwargs), box=self._serialize_box(resolved), retried=False)
            return resolved

        sleep(0.1)
        retry_image = self.screenshot(**kwargs)
        retry_box = self.matcher.locate(template, retry_image)
        if retry_box is None:
            self._record_trace("locate", template=template, kwargs=dict(kwargs), box=None, retried=True)
            return None
        resolved = self._offset_box(retry_box, **kwargs)
        self._record_trace("locate", template=template, kwargs=dict(kwargs), box=self._serialize_box(resolved), retried=True)
        return resolved

    def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            box = self.locate(template)
            if box is not None:
                self._record_trace("wait_img", template=template, timeout=timeout, interval=interval, found=True)
                return box
            sleep(interval)
        self._record_trace("wait_img", template=template, timeout=timeout, interval=interval, found=False)
        return None

    def ocr(self, **kwargs):
        image = self.screenshot(**kwargs)
        result = self.ocr_engine.run(image)
        self._record_trace("ocr", kwargs=dict(kwargs), pieces=len(result or []))
        return result

    def _prepare_input_target(self) -> None:
        ensure_available = getattr(self.input, "ensure_available", None)
        if callable(ensure_available):
            ensure_available()
        self.window.prepare_input()
        self._record_trace("prepare_input")

    def _to_screen_point(self, x: int | float, y: int | float) -> tuple[int | float, int | float]:
        if hasattr(self.window, "to_screen_point"):
            return self.window.to_screen_point(x, y)

        if hasattr(self.window, "client_region"):
            region = self.window.client_region()

            def _convert(value: int | float, size: int, origin: int) -> int:
                if isinstance(value, float) and 0.0 <= value <= 1.0:
                    return origin + round(size * value)
                return origin + round(value)

            return _convert(x, region.width, region.left), _convert(y, region.height, region.top)

        return x, y

    def _offset_box(self, box: Box, **kwargs) -> Box:
        from_x = kwargs.get("from_x")
        from_y = kwargs.get("from_y")
        if from_x is None or from_y is None:
            return box

        if not hasattr(self.window, "client_region"):
            return box

        region = self.window.client_region()

        def _offset(value: int | float, size: int) -> int:
            if isinstance(value, float) and 0.0 <= value <= 1.0:
                return round(size * value)
            return round(value)

        return Box(
            left=box.left + _offset(from_x, region.width),
            top=box.top + _offset(from_y, region.height),
            width=box.width,
            height=box.height,
            source=box.source,
        )

    def click_point(self, x: float, y: float, **kwargs):
        self._prepare_input_target()
        screen_x, screen_y = self._to_screen_point(x, y)
        self.input.click(screen_x, screen_y, **kwargs)
        self._record_trace("click_point", point=[x, y], screen_point=[screen_x, screen_y])
        self._check_foreground_after_input()

    def drag_to(self, from_x: float, from_y: float, to_x: float, to_y: float):
        self._prepare_input_target()
        screen_from_x, screen_from_y = self._to_screen_point(from_x, from_y)
        screen_to_x, screen_to_y = self._to_screen_point(to_x, to_y)
        self.input.drag(screen_from_x, screen_from_y, screen_to_x, screen_to_y)
        self._record_trace(
            "drag_to",
            from_point=[from_x, from_y],
            to_point=[to_x, to_y],
            screen_from=[screen_from_x, screen_from_y],
            screen_to=[screen_to_x, screen_to_y],
        )
        self._check_foreground_after_input()

    def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
        self._prepare_input_target()
        for index in range(presses):
            self.input.press(key)
            if index + 1 < presses:
                sleep(interval)
        self._record_trace("press_key", key=key, presses=presses, interval=interval)
        self._check_foreground_after_input()

    def hotkey(self, *keys: str):
        self._prepare_input_target()
        self.input.hotkey(*keys)
        self._record_trace("hotkey", keys=list(keys))
        self._check_foreground_after_input()

    def type_text(self, text: str):
        self._prepare_input_target()
        self.input.type_text(text)
        self._record_trace("type_text", text=text)
        self._check_foreground_after_input()

    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        try:
            if request_id is None:
                path = self.window.capture_to_workspace()
            else:
                path = self.window.capture_to_workspace(request_id=request_id)
            self._record_trace("capture_after_action", optional=optional, screenshot=str(path))
            return path
        except Exception:
            if optional:
                self._record_trace("capture_after_action", optional=optional, screenshot=None)
                return None
            raise


REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
REFERENCE_IMAGE_SIZE = (64, 64)


def _iter_reference_images(base_dir: Path) -> list[Path]:
    trail_root = base_dir / "trail"
    if not trail_root.exists():
        return []
    return sorted(
        path
        for path in trail_root.rglob("*")
        if path.is_file() and path.suffix.lower() in REFERENCE_IMAGE_EXTENSIONS and "references" in path.parts
    )


def _load_normalized_image(path: Path):
    with Image.open(path) as image:
        return ImageOps.grayscale(image).resize(REFERENCE_IMAGE_SIZE)


def _compute_similarity(left: Path, right: Path) -> float:
    diff = ImageChops.difference(_load_normalized_image(left), _load_normalized_image(right))
    mean = ImageStat.Stat(diff).mean[0] / 255.0
    return round(max(0.0, 1.0 - mean), 4)


def _match_reference_images(screenshot_path: Path, *, limit: int = 3, reference_root: Path | None = None) -> list[dict[str, Any]]:
    if not screenshot_path.exists():
        return []

    base_dir = Path.cwd() if reference_root is None else Path(reference_root)
    matches: list[dict[str, Any]] = []
    for reference_path in _iter_reference_images(base_dir):
        try:
            similarity = _compute_similarity(screenshot_path, reference_path)
        except Exception:
            continue
        try:
            display_path = reference_path.relative_to(base_dir).as_posix()
        except ValueError:
            display_path = str(reference_path)
        matches.append({"path": display_path, "similarity": similarity})

    return sorted(matches, key=lambda item: (-item["similarity"], item["path"]))[:limit]


class PyScreezeMatcher:
    def locate(self, template: str, image) -> Box | None:
        try:
            import pyscreeze  # type: ignore
        except Exception as exc:
            raise TrailError("IMAGE_BACKEND_UNAVAILABLE", "pyscreeze backend unavailable") from exc

        screenshot = Image.open(BytesIO(image)) if isinstance(image, (bytes, bytearray)) else image
        try:
            box = pyscreeze.locate(template, screenshot, confidence=0.9)
        except Exception as exc:
            image_not_found = getattr(pyscreeze, "ImageNotFoundException", None)
            if image_not_found is not None and isinstance(exc, image_not_found):
                return None
            raise
        if box is None:
            return None
        left, top, width, height = box
        return Box(left=left, top=top, width=width, height=height, source=template)


class RapidOcrAdapter:
    def __init__(self):
        self._engine = None

    def run(self, image) -> list[Any]:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except Exception as exc:
            raise TrailError("OCR_BACKEND_UNAVAILABLE", "rapidocr backend unavailable") from exc

        if self._engine is None:
            self._engine = RapidOCR()

        screenshot = Image.open(BytesIO(image)) if isinstance(image, (bytes, bytearray)) else image
        result, _ = self._engine(screenshot, use_det=True, use_cls=False, use_rec=True)
        return result or []


class PyAutoGuiInputDriver:
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    KEYEVENTF_KEYUP = 0x0002
    VIRTUAL_KEY_OVERRIDES = {
        "esc": 0x1B,
        "escape": 0x1B,
        "enter": 0x0D,
        "return": 0x0D,
        "space": 0x20,
        "tab": 0x09,
    }

    @staticmethod
    def _load_backend():
        try:
            import pyautogui  # type: ignore
        except Exception as exc:
            raise TrailError("INPUT_BACKEND_UNAVAILABLE", "pyautogui backend unavailable") from exc
        return pyautogui

    @staticmethod
    def _should_use_virtual_screen_path(*coords: float) -> bool:
        return sys.platform == "win32"

    @staticmethod
    def _virtual_click(x: float, y: float) -> None:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(round(x), round(y))
        user32.mouse_event(PyAutoGuiInputDriver.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(PyAutoGuiInputDriver.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    @staticmethod
    def _virtual_drag(from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(round(from_x), round(from_y))
        user32.mouse_event(PyAutoGuiInputDriver.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        sleep(0.1)
        user32.SetCursorPos(round(to_x), round(to_y))
        sleep(0.1)
        user32.mouse_event(PyAutoGuiInputDriver.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    @classmethod
    def _virtual_key_code(cls, key: str) -> int:
        normalized = key.lower()
        if normalized in cls.VIRTUAL_KEY_OVERRIDES:
            return cls.VIRTUAL_KEY_OVERRIDES[normalized]
        if len(normalized) == 1:
            return ord(normalized.upper())
        raise TrailError("INPUT_BACKEND_UNAVAILABLE", f"unsupported windows key: {key}")

    @classmethod
    def _virtual_press(cls, key: str) -> None:
        key_code = cls._virtual_key_code(key)
        user32 = ctypes.windll.user32
        user32.keybd_event(key_code, 0, 0, 0)
        user32.keybd_event(key_code, 0, cls.KEYEVENTF_KEYUP, 0)

    def click(self, x: float, y: float, **kwargs) -> None:
        if self._should_use_virtual_screen_path(x, y):
            self._virtual_click(x, y)
            return
        pyautogui = self._load_backend()
        pyautogui.click(x, y)

    def ensure_available(self) -> None:
        self._load_backend()

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        if self._should_use_virtual_screen_path(from_x, from_y, to_x, to_y):
            self._virtual_drag(from_x, from_y, to_x, to_y)
            return
        pyautogui = self._load_backend()
        pyautogui.moveTo(from_x, from_y)
        pyautogui.dragTo(to_x, to_y, duration=0.5)

    def press(self, key: str) -> None:
        if sys.platform == "win32":
            self._virtual_press(key)
            return
        pyautogui = self._load_backend()
        pyautogui.press(key)

    def hotkey(self, *keys: str) -> None:
        pyautogui = self._load_backend()
        pyautogui.hotkey(*keys)

    def type_text(self, text: str) -> None:
        pyautogui = self._load_backend()
        pyautogui.write(text, interval=0)


def build_window_controller(
    *,
    window_title: str = "崩坏：星穹铁道",
    window_binding: dict | None = None,
    workspace: Path | None = None,
) -> WindowController:
    shots_dir = Path(".trail/shots") if workspace is None else Path(workspace)
    return WindowsWindowController(window_title=window_title, window_binding=window_binding, workspace=shots_dir)


def build_image_matcher() -> ImageMatcher:
    return PyScreezeMatcher()


def build_ocr_engine() -> OcrEngine:
    return RapidOcrAdapter()


def build_input_driver() -> InputDriver:
    return PyAutoGuiInputDriver()


def build_runtime(
    *,
    window_title: str = "崩坏：星穹铁道",
    window_binding: dict | None = None,
    workspace: Path | None = None,
    reference_root: Path | None = None,
) -> RuntimeOperator:
    return RuntimeOperator(
        build_window_controller(window_title=window_title, window_binding=window_binding, workspace=workspace),
        build_image_matcher(),
        build_ocr_engine(),
        build_input_driver(),
        reference_root=reference_root,
    )
