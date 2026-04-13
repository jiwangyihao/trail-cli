from __future__ import annotations

import ctypes
import sys
from time import sleep
from pathlib import Path
from io import BytesIO
from ctypes.wintypes import POINT, RECT

from PIL import ImageGrab

from trail.core.errors import TrailError
from trail.runtime.model import Region, WindowBinding


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        return


_enable_dpi_awareness()


def _capture_win32_window(hwnd: int, region: Region):
    import win32gui  # type: ignore
    import win32ui  # type: ignore
    from PIL import Image

    if not hwnd or region.width <= 0 or region.height <= 0:
        raise TrailError("SCREENSHOT_FAILED", "无法截取窗口内容")

    hwnd_dc = None
    mfc_dc = None
    save_dc = None
    save_bitmap = None

    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_dc, region.width, region.height)
        save_dc.SelectObject(save_bitmap)

        flags = 1 | 2
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), flags)
        if result == 0:
            raise TrailError("SCREENSHOT_FAILED", "无法截取窗口内容")

        bmp_info = save_bitmap.GetInfo()
        bmp_data = save_bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_data,
            "raw",
            "BGRX",
            0,
            1,
        )
        return image
    finally:
        if save_bitmap is not None:
            win32gui.DeleteObject(save_bitmap.GetHandle())
        if save_dc is not None:
            save_dc.DeleteDC()
        if mfc_dc is not None:
            mfc_dc.DeleteDC()
        if hwnd_dc is not None:
            win32gui.ReleaseDC(hwnd, hwnd_dc)


def attach_window(window_title: str) -> WindowBinding:
    try:
        import pygetwindow  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise TrailError("WINDOW_NOT_FOUND", f"未找到窗口 {window_title}") from exc

    try:
        windows = pygetwindow.getWindowsWithTitle(window_title)
    except Exception as exc:
        raise TrailError("WINDOW_NOT_FOUND", f"未找到窗口 {window_title}") from exc

    for window in windows:
        if getattr(window, "title", None) == window_title:
            hwnd = getattr(window, "_hWnd", None)
            return WindowBinding(title=window_title, hwnd=None if hwnd is None else int(hwnd))

    raise TrailError("WINDOW_NOT_FOUND", f"未找到窗口 {window_title}")


def normalize_window_binding(window_binding: WindowBinding | dict | None = None, *, window_title: str | None = None) -> WindowBinding:
    if isinstance(window_binding, WindowBinding):
        return window_binding
    if isinstance(window_binding, dict):
        title = str(window_binding.get("title") or window_title or "崩坏：星穹铁道")
        hwnd = window_binding.get("hwnd")
        return WindowBinding(title=title, hwnd=None if hwnd is None else int(hwnd))
    return WindowBinding(title=window_title or "崩坏：星穹铁道")


class WindowsWindowController:
    def __init__(self, *, workspace: Path, window_binding: WindowBinding | dict | None = None, window_title: str | None = None):
        self.window_binding = normalize_window_binding(window_binding, window_title=window_title)
        self.window_title = self.window_binding.title
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _resolve_window(self):
        try:
            import pygetwindow  # type: ignore
        except Exception as exc:
            raise TrailError("WINDOW_NOT_FOUND", f"未找到窗口 {self.window_title}") from exc

        windows = pygetwindow.getWindowsWithTitle(self.window_title)
        for window in windows:
            if getattr(window, "title", None) != self.window_title:
                continue
            hwnd = getattr(window, "_hWnd", None)
            if self.window_binding.hwnd is not None and hwnd != self.window_binding.hwnd:
                continue
            return window

        raise TrailError("WINDOW_NOT_FOUND", f"未找到窗口 {self.window_title}")

    def _get_client_region(self, hwnd: int) -> Region:
        client_rect = RECT()
        ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(client_rect))
        left_top = POINT(client_rect.left, client_rect.top)
        ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(left_top))
        width = client_rect.right - client_rect.left
        height = client_rect.bottom - client_rect.top
        if width <= 0 or height <= 0:
            raise TrailError("WINDOW_REGION_INVALID", f"无法获取窗口区域 {self.window_title}")
        return Region(left=left_top.x, top=left_top.y, width=width, height=height)

    def _resolve_region(self, window=None) -> Region:
        window = self._resolve_window() if window is None else window
        hwnd = getattr(window, "_hWnd", None)
        if hwnd:
            return self._get_client_region(int(hwnd))
        left = int(getattr(window, "left", 0))
        top = int(getattr(window, "top", 0))
        width = int(getattr(window, "width", 0))
        height = int(getattr(window, "height", 0))
        if width <= 0 or height <= 0:
            raise TrailError("WINDOW_REGION_INVALID", f"无法获取窗口区域 {self.window_title}")
        return Region(left=left, top=top, width=width, height=height)

    def client_region(self) -> Region:
        return self._resolve_region()

    def to_screen_point(self, x: int | float, y: int | float) -> tuple[int, int]:
        region = self.client_region()

        def _convert(value: int | float, size: int, origin: int) -> int:
            if isinstance(value, float) and 0.0 <= value <= 1.0:
                return origin + round(size * value)
            return origin + round(value)

        return _convert(x, region.width, region.left), _convert(y, region.height, region.top)

    def prepare_input(self) -> None:
        window = self._resolve_window()
        if getattr(window, "isMinimized", False):
            window.restore()
            sleep(0.1)
        if not getattr(window, "isActive", False):
            window.activate()
            sleep(0.1)

    def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None) -> bytes:
        window = self._resolve_window()
        region = self._resolve_region(window)
        if all(value is not None for value in (from_x, from_y, to_x, to_y)):
            region = region.sub_region(from_x, from_y, to_x, to_y)

        hwnd = getattr(window, "_hWnd", None)
        if sys.platform == "win32" and hwnd is not None:
            image = _capture_win32_window(int(hwnd), region)
        else:
            image = ImageGrab.grab(
                bbox=(
                    region.left,
                    region.top,
                    region.left + region.width,
                    region.top + region.height,
                )
            )
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def capture_to_workspace(self) -> Path:
        path = self.workspace / "last-action.png"
        path.write_bytes(self.capture())
        return path
