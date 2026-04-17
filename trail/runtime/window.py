from __future__ import annotations

import ctypes
import hashlib
import subprocess
import sys
from time import sleep
from pathlib import Path
from io import BytesIO
from ctypes.wintypes import POINT, RECT
from threading import Event, Lock

from PIL import Image, ImageGrab

from trail.core.errors import TrailError
from trail.runtime.model import Region, WindowBinding


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        awareness_context = getattr(ctypes.windll.user32, "SetProcessDpiAwarenessContext", None)
        if callable(awareness_context):
            awareness_context(-4)
            return
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        return


_enable_dpi_awareness()

GAME_CHANNEL_CONFIG = {
    "official": (1, 1),
    "bilibili": (14, 0),
    "global": None,
}

WINDOWS_RESERVED_CAPTURE_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
CANONICAL_CLIENT_WIDTH = 1920
CANONICAL_CLIENT_HEIGHT = 1080
CANONICAL_ASPECT_RATIO = CANONICAL_CLIENT_WIDTH / CANONICAL_CLIENT_HEIGHT

_WINDOWS_CAPTURE_SESSIONS: dict[int, "_WindowsCaptureSession"] = {}
_WINDOWS_CAPTURE_SESSIONS_LOCK = Lock()


def _safe_capture_request_id(request_id: str | None) -> str:
    if request_id is None:
        return "last-action"

    raw = str(request_id)
    sanitized = "".join(char if char.isascii() and (char.isalnum() or char in {"-", "_", "."}) else "-" for char in raw)
    sanitized = sanitized.strip(". -_")
    if not sanitized:
        sanitized = "request"
    if sanitized.split(".", 1)[0].upper() in WINDOWS_RESERVED_CAPTURE_STEMS:
        sanitized = f"request-{sanitized}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    prefix = sanitized[:80].rstrip(". -_") or "request"
    return f"{prefix}-{digest}"


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


def _resolve_window_region(hwnd: int) -> Region:
    import win32gui  # type: ignore

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return Region(left=left, top=top, width=right - left, height=bottom - top)


def _overlap_area(left: Region, right: Region) -> int:
    overlap_left = max(left.left, right.left)
    overlap_top = max(left.top, right.top)
    overlap_right = min(left.left + left.width, right.left + right.width)
    overlap_bottom = min(left.top + left.height, right.top + right.height)
    if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
        return 0
    return (overlap_right - overlap_left) * (overlap_bottom - overlap_top)


def _find_owned_overlay_target(main_hwnd: int, client_region: Region) -> tuple[int, Region] | None:
    if sys.platform != "win32":
        return None
    try:
        import win32gui  # type: ignore
    except Exception:
        return None

    client_area = client_region.width * client_region.height
    if client_area <= 0:
        return None

    best: tuple[int, int, Region] | None = None

    def callback(hwnd: int, extra) -> bool:
        del extra
        try:
            if win32gui.GetWindow(hwnd, 4) != main_hwnd:
                return True
            if not win32gui.IsWindowVisible(hwnd):
                return True
            region = _resolve_window_region(hwnd)
        except Exception:
            return True

        overlap = _overlap_area(region, client_region)
        if overlap < client_area * 0.5:
            return True

        nonlocal best
        if best is None or overlap > best[0]:
            best = (overlap, hwnd, region)
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        return None

    if best is None:
        return None
    return best[1], best[2]


def _window_capture_crop_box(frame_size: tuple[int, int], window_region: Region, client_region: Region) -> tuple[int, int, int, int]:
    frame_width, frame_height = frame_size
    scale_x = frame_width / window_region.width
    scale_y = frame_height / window_region.height
    left = max(0, round((client_region.left - window_region.left) * scale_x))
    top = max(0, round((client_region.top - window_region.top) * scale_y))
    right = min(frame_width, round((client_region.left - window_region.left + client_region.width) * scale_x))
    bottom = min(frame_height, round((client_region.top - window_region.top + client_region.height) * scale_y))
    return left, top, right, bottom


def _capture_with_windows_capture(hwnd: int, client_region: Region):
    from PIL import Image
    session = _get_windows_capture_session(hwnd)
    frame_buffer, frame_size = session.snapshot()
    crop_box = _window_capture_crop_box(frame_size, _resolve_window_region(hwnd), client_region)
    left, top, right, bottom = crop_box
    return Image.fromarray(frame_buffer[top:bottom, left:right, :3][:, :, ::-1], "RGB")


def _create_windows_capture(hwnd: int):
    from windows_capture import WindowsCapture

    return WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)


class _WindowsCaptureSession:
    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._lock = Lock()
        self._frame_ready = Event()
        self._frame_buffer = None
        self._frame_size: tuple[int, int] | None = None
        capture = _create_windows_capture(hwnd)

        @capture.event
        def on_frame_arrived(frame, capture_control):
            with self._lock:
                self._frame_buffer = frame.frame_buffer.copy()
                self._frame_size = (frame.width, frame.height)
            self._frame_ready.set()

        @capture.event
        def on_closed():
            self._frame_ready.set()

        self.capture = capture
        self.control = capture.start_free_threaded()

    def is_finished(self) -> bool:
        return bool(self.control.is_finished())

    def snapshot(self) -> tuple[object, tuple[int, int]]:
        if not self._frame_ready.wait(5):
            raise TrailError("SCREENSHOT_FAILED", "windows graphics capture timed out")
        with self._lock:
            if self._frame_buffer is None or self._frame_size is None:
                raise TrailError("SCREENSHOT_FAILED", "windows graphics capture returned no frame")
            return self._frame_buffer.copy(), self._frame_size


def _get_windows_capture_session(hwnd: int) -> _WindowsCaptureSession:
    with _WINDOWS_CAPTURE_SESSIONS_LOCK:
        session = _WINDOWS_CAPTURE_SESSIONS.get(hwnd)
        if session is not None and not session.is_finished():
            return session
        session = _WindowsCaptureSession(hwnd)
        _WINDOWS_CAPTURE_SESSIONS[hwnd] = session
        return session


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


def is_process_running(process_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return False
    return process_name.lower() in (result.stdout or "").lower()


def change_game_config(game_path: Path, *, channel: int, sub_channel: int) -> None:
    config_file = Path(game_path).parent / "config.ini"
    try:
        lines = config_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise TrailError("GAME_CONFIG_NOT_FOUND", f"未找到配置文件 {config_file}") from exc

    updated: list[str] = []
    channel_seen = False
    sub_channel_seen = False
    for line in lines:
        if line.startswith("channel="):
            updated.append(f"channel={channel}")
            channel_seen = True
        elif line.startswith("sub_channel="):
            updated.append(f"sub_channel={sub_channel}")
            sub_channel_seen = True
        else:
            updated.append(line)

    if not channel_seen:
        updated.append(f"channel={channel}")
    if not sub_channel_seen:
        updated.append(f"sub_channel={sub_channel}")
    config_file.write_text("\n".join(updated) + "\n", encoding="utf-8")


def launch_game(
    *,
    game_path: Path,
    channel: str = "official",
    launch_args: list[str] | None = None,
    use_cmd: bool = False,
) -> dict:
    path = Path(game_path)
    if not path.exists():
        raise TrailError("GAME_PATH_NOT_FOUND", f"未找到游戏启动路径 {path}")

    args = list(launch_args or [])
    if is_process_running("StarRail.exe"):
        return {
            "started": False,
            "already_running": True,
            "path": str(path),
            "channel": channel,
            "args": args,
        }

    channel_config = GAME_CHANNEL_CONFIG.get(channel)
    if channel_config is None and channel != "global":
        raise TrailError("GAME_CHANNEL_INVALID", f"未知游戏渠道 {channel}")
    if channel_config is not None:
        change_game_config(path, channel=channel_config[0], sub_channel=channel_config[1])

    cwd = str(path.parent)
    if use_cmd:
        subprocess.Popen(["cmd", "/c", "start", "", str(path), *args], cwd=cwd)
    else:
        subprocess.Popen([str(path)] + args, cwd=cwd)
    return {
        "started": True,
        "already_running": False,
        "path": str(path),
        "channel": channel,
        "args": args,
    }


def _grab_region_with_imagegrab(region: Region):
    kwargs = {
        "bbox": (
            region.left,
            region.top,
            region.left + region.width,
            region.top + region.height,
        )
    }
    if sys.platform == "win32":
        kwargs["all_screens"] = True
    return ImageGrab.grab(**kwargs)


def _scale_region_for_screen_capture(region: Region, hwnd: int | None) -> Region:
    if sys.platform != "win32" or hwnd is None:
        return region
    get_dpi = getattr(ctypes.windll.user32, "GetDpiForWindow", None)
    if not callable(get_dpi):
        return region
    try:
        dpi = get_dpi(hwnd)
    except Exception:
        return region
    if not dpi or dpi == 96:
        return region
    scale = dpi / 96
    return Region(
        left=round(region.left * scale),
        top=round(region.top * scale),
        width=round(region.width * scale),
        height=round(region.height * scale),
    )


def _target_capture_size(client_region: Region, hwnd: int | None) -> tuple[int, int]:
    del client_region, hwnd
    return CANONICAL_CLIENT_WIDTH, CANONICAL_CLIENT_HEIGHT


def _scale_canonical_point(value: int | float, *, target_size: int, canonical_size: int) -> int:
    if isinstance(value, float) and 0.0 <= value <= 1.0:
        scaled = round(target_size * value)
    else:
        scaled = round(target_size * (float(value) / canonical_size))
    return min(max(scaled, 0), max(target_size - 1, 0))


def _grab_window_with_imagegrab(hwnd: int):
    return ImageGrab.grab(window=hwnd)


def _live_capture_target_size(hwnd: int) -> tuple[int, int] | None:
    try:
        image = _grab_window_with_imagegrab(hwnd)
    except Exception:
        return None
    return image.width, image.height


class WindowsWindowController:
    def __init__(self, *, workspace: Path, window_binding: WindowBinding | dict | None = None, window_title: str | None = None):
        self.window_binding = normalize_window_binding(window_binding, window_title=window_title)
        self.window_title = self.window_binding.title
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._warnings: list[dict[str, str]] = []

    def collect_warnings(self) -> list[dict[str, str]]:
        warnings = list(self._warnings)
        self._warnings.clear()
        return warnings

    def _normalize_captured_image(self, image, *, target_size: tuple[int, int]):
        source_width, source_height = image.size
        target_width, target_height = target_size
        if source_width > 0 and source_height > 0:
            source_ratio = source_width / source_height
            if abs(source_ratio - CANONICAL_ASPECT_RATIO) > 0.01:
                self._warnings.append(
                    {
                        "code": "WINDOW_CAPTURE_ASPECT_RATIO_MISMATCH",
                        "message": "captured image aspect ratio differs from 1920x1080; resized to canonical output",
                    }
                )
        if image.size != target_size:
            image = image.resize((target_width, target_height))
        return image

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
        scaled_x = _scale_canonical_point(x, target_size=region.width, canonical_size=CANONICAL_CLIENT_WIDTH)
        scaled_y = _scale_canonical_point(y, target_size=region.height, canonical_size=CANONICAL_CLIENT_HEIGHT)
        return region.left + scaled_x, region.top + scaled_y

    def prepare_input(self) -> None:
        window = self._resolve_window()
        hwnd = getattr(window, "_hWnd", None)
        if sys.platform == "win32" and hwnd is not None:
            user32 = ctypes.windll.user32
            if getattr(window, "isMinimized", False):
                user32.ShowWindow(int(hwnd), 9)
                sleep(0.1)
            user32.BringWindowToTop(int(hwnd))
            user32.SetForegroundWindow(int(hwnd))
            sleep(0.1)
            return
        if getattr(window, "isMinimized", False):
            window.restore()
            sleep(0.1)
        if not getattr(window, "isActive", False):
            window.activate()
            sleep(0.1)

    def is_foreground(self) -> bool:
        window = self._resolve_window()
        return bool(getattr(window, "isActive", False))

    def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None) -> bytes:
        window = self._resolve_window()
        region = self._resolve_region(window)
        if all(value is not None for value in (from_x, from_y, to_x, to_y)):
            region = region.sub_region(from_x, from_y, to_x, to_y)

        hwnd = getattr(window, "_hWnd", None)
        capture_hwnd = int(hwnd) if hwnd is not None else None
        capture_region = region
        if capture_hwnd is not None and all(value is None for value in (from_x, from_y, to_x, to_y)):
            overlay_target = _find_owned_overlay_target(capture_hwnd, region)
            if overlay_target is not None:
                capture_hwnd, capture_region = overlay_target
        target_size = _target_capture_size(region, int(hwnd) if hwnd is not None else None)
        if sys.platform == "win32" and capture_hwnd is not None:
            try:
                image = _capture_with_windows_capture(capture_hwnd, capture_region)
            except Exception:
                try:
                    scaled_region = _scale_region_for_screen_capture(capture_region, capture_hwnd)
                    image = _grab_region_with_imagegrab(scaled_region)
                except Exception:
                    try:
                        image = _grab_window_with_imagegrab(capture_hwnd)
                    except Exception:
                        try:
                            image = _capture_win32_window(capture_hwnd, capture_region)
                        except TrailError as exc:
                            if exc.code != "SCREENSHOT_FAILED":
                                raise
                            self.prepare_input()
                            image = _grab_region_with_imagegrab(capture_region)
        else:
            image = _grab_region_with_imagegrab(region)
        image = self._normalize_captured_image(image, target_size=target_size)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def capture_to_workspace(self, request_id: str | None = None) -> Path:
        path = self.workspace / f"{_safe_capture_request_id(request_id)}.jpg"
        image = Image.open(BytesIO(self.capture())).convert("RGB")
        image.save(path, format="JPEG", quality=90, optimize=True)
        return path
