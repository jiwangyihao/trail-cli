from __future__ import annotations

from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Protocol

from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.model import Box, Region
from trail.runtime.window import WindowsWindowController


class WindowController(Protocol):
    def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None): ...
    def capture_to_workspace(self) -> Path: ...
    def prepare_input(self) -> None: ...
    def client_region(self): ...
    def to_screen_point(self, x: int | float, y: int | float) -> tuple[int, int]: ...


class ImageMatcher(Protocol):
    def locate(self, template: str, image) -> Box | None: ...


class OcrEngine(Protocol):
    def run(self, image) -> list[Any]: ...


class InputDriver(Protocol):
    def click(self, x: float, y: float, **kwargs) -> None: ...
    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None: ...
    def press(self, key: str) -> None: ...


class RuntimeOperator:
    def __init__(self, window: WindowController, matcher: ImageMatcher, ocr_engine: OcrEngine, input_driver: InputDriver):
        self.window = window
        self.matcher = matcher
        self.ocr_engine = ocr_engine
        self.input = input_driver

    def screenshot(self, *, from_x=None, from_y=None, to_x=None, to_y=None):
        return self.window.capture(from_x=from_x, from_y=from_y, to_x=to_x, to_y=to_y)

    def locate(self, template: str, **kwargs):
        image = self.screenshot(**kwargs)
        box = self.matcher.locate(template, image)
        if box is not None:
            return self._offset_box(box, **kwargs)

        sleep(0.1)
        retry_image = self.screenshot(**kwargs)
        retry_box = self.matcher.locate(template, retry_image)
        if retry_box is None:
            return None
        return self._offset_box(retry_box, **kwargs)

    def wait_img(self, template: str, timeout: int = 10, interval: float = 0.5):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            box = self.locate(template)
            if box is not None:
                return box
            sleep(interval)
        return None

    def ocr(self, **kwargs):
        image = self.screenshot(**kwargs)
        return self.ocr_engine.run(image)

    def _prepare_input_target(self) -> None:
        self.window.prepare_input()

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

    def drag_to(self, from_x: float, from_y: float, to_x: float, to_y: float):
        self._prepare_input_target()
        screen_from_x, screen_from_y = self._to_screen_point(from_x, from_y)
        screen_to_x, screen_to_y = self._to_screen_point(to_x, to_y)
        self.input.drag(screen_from_x, screen_from_y, screen_to_x, screen_to_y)

    def press_key(self, key: str, presses: int = 1, interval: float = 0.2):
        self._prepare_input_target()
        for index in range(presses):
            self.input.press(key)
            if index + 1 < presses:
                sleep(interval)

    def capture_after_action(self, optional: bool = False):
        try:
            return self.window.capture_to_workspace()
        except Exception:
            if optional:
                return None
            raise


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
    @staticmethod
    def _load_backend():
        try:
            import pyautogui  # type: ignore
        except Exception as exc:
            raise TrailError("INPUT_BACKEND_UNAVAILABLE", "pyautogui backend unavailable") from exc
        return pyautogui

    def click(self, x: float, y: float, **kwargs) -> None:
        pyautogui = self._load_backend()
        pyautogui.click(x, y)

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        pyautogui = self._load_backend()
        pyautogui.moveTo(from_x, from_y)
        pyautogui.dragTo(to_x, to_y, duration=0.5)

    def press(self, key: str) -> None:
        pyautogui = self._load_backend()
        pyautogui.press(key)


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
) -> RuntimeOperator:
    return RuntimeOperator(
        build_window_controller(window_title=window_title, window_binding=window_binding, workspace=workspace),
        build_image_matcher(),
        build_ocr_engine(),
        build_input_driver(),
    )
