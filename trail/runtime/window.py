from __future__ import annotations

from pathlib import Path
from io import BytesIO

from PIL import ImageGrab

from trail.core.errors import TrailError
from trail.runtime.model import Region, WindowBinding


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

    def _resolve_region(self) -> Region:
        window = self._resolve_window()
        left = int(getattr(window, "left", 0))
        top = int(getattr(window, "top", 0))
        width = int(getattr(window, "width", 0))
        height = int(getattr(window, "height", 0))
        if width <= 0 or height <= 0:
            raise TrailError("WINDOW_REGION_INVALID", f"无法获取窗口区域 {self.window_title}")
        return Region(left=left, top=top, width=width, height=height)

    def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None) -> bytes:
        region = self._resolve_region()
        if all(value is not None for value in (from_x, from_y, to_x, to_y)):
            region = region.sub_region(from_x, from_y, to_x, to_y)

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
