from __future__ import annotations

from base64 import b64decode
from pathlib import Path

from trail.core.errors import TrailError
from trail.runtime.model import WindowBinding


_PNG_PLACEHOLDER = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9pQ1d1sAAAAASUVORK5CYII="
)


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


class WindowsWindowController:
    def __init__(self, *, window_title: str, workspace: Path):
        self.window_title = window_title
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None) -> bytes:
        return _PNG_PLACEHOLDER

    def capture_to_workspace(self) -> Path:
        path = self.workspace / "last-action.png"
        path.write_bytes(self.capture())
        return path
