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
from trail.runtime.launch_paths import read_launch_paths, write_launch_path


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
DEFAULT_GAME_PATHS = {
    "official": Path(r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe"),
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
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002

_WINDOWS_CAPTURE_SESSIONS: dict[int, "_WindowsCaptureSession"] = {}
_WINDOWS_CAPTURE_SESSIONS_LOCK = Lock()


def _force_window_foreground(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    foreground_hwnd = user32.GetForegroundWindow()
    current_thread_id = kernel32.GetCurrentThreadId()
    foreground_thread_id = user32.GetWindowThreadProcessId(foreground_hwnd, None) if foreground_hwnd else 0
    target_thread_id = user32.GetWindowThreadProcessId(hwnd, None)

    attached_foreground = False
    attached_target = False
    try:
        if foreground_thread_id and foreground_thread_id != current_thread_id:
            user32.AttachThreadInput(foreground_thread_id, current_thread_id, True)
            attached_foreground = True
        if target_thread_id and target_thread_id != current_thread_id:
            user32.AttachThreadInput(target_thread_id, current_thread_id, True)
            attached_target = True

        user32.BringWindowToTop(hwnd)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached_target:
            user32.AttachThreadInput(target_thread_id, current_thread_id, False)
        if attached_foreground:
            user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)


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
        window_region = _resolve_window_region(hwnd)
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_dc, window_region.width, window_region.height)
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
        return _crop_window_image_to_region(image, hwnd=hwnd, client_region=region)
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


def _crop_window_image_to_region(image, *, hwnd: int, client_region: Region):
    crop_box = _window_capture_crop_box(image.size, _resolve_window_region(hwnd), client_region)
    left, top, right, bottom = crop_box
    if right <= left or bottom <= top:
        raise TrailError("SCREENSHOT_FAILED", "无法截取窗口内容")
    return image.crop(crop_box)


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


def _iter_launch_game_candidates(*, game_path: Path | None, channel: str):
    if game_path is not None:
        yield Path(game_path), "explicit"
        return

    raw_history = read_launch_paths().get(channel)
    history_path = raw_history.get("last_success_game_path") if isinstance(raw_history, dict) else None
    if isinstance(history_path, str) and history_path:
        yield Path(history_path), "history"

    default_path = DEFAULT_GAME_PATHS.get(channel)
    if default_path is not None:
        yield Path(default_path), "default"


def _launch_game_from_path(
    *,
    path: Path,
    channel: str,
    channel_config: tuple[int, int] | None,
    launch_args: list[str],
    use_cmd: bool,
) -> dict:
    if not path.exists():
        raise TrailError("GAME_PATH_NOT_FOUND", f"未找到游戏启动路径 {path}")

    if is_process_running("StarRail.exe"):
        return {
            "started": False,
            "already_running": True,
            "path": str(path),
            "channel": channel,
            "args": launch_args,
        }

    if channel_config is not None:
        change_game_config(path, channel=channel_config[0], sub_channel=channel_config[1])

    cwd = str(path.parent)
    if use_cmd:
        subprocess.Popen(["cmd", "/c", "start", "", str(path), *launch_args], cwd=cwd)
    else:
        subprocess.Popen([str(path)] + launch_args, cwd=cwd)
    return {
        "started": True,
        "already_running": False,
        "path": str(path),
        "channel": channel,
        "args": launch_args,
    }


def _build_game_path_persist_warning(error: OSError) -> dict[str, str]:
    base_message = "游戏已成功启动，但历史路径持久化失败"
    detail = str(error).strip()
    return {
        "code": "GAME_PATH_PERSIST_FAILED",
        "message": f"{base_message}: {detail}" if detail else base_message,
    }


def _build_explicit_game_launch_error(path: Path, error: Exception) -> TrailError:
    if isinstance(error, TrailError) and error.code == "GAME_PATH_NOT_FOUND":
        return error
    if isinstance(error, TrailError) and error.code == "GAME_LAUNCH_FAILED":
        return error

    detail = str(error).strip()
    message = f"显式提供的游戏路径启动失败: {path}"
    if detail:
        message = f"{message}: {detail}"
    return TrailError("GAME_LAUNCH_FAILED", message)


def launch_game(
    *,
    game_path: Path | None = None,
    channel: str = "official",
    launch_args: list[str] | None = None,
    use_cmd: bool = False,
) -> dict:
    args = list(launch_args or [])
    channel_config = GAME_CHANNEL_CONFIG.get(channel)
    if channel_config is None and channel != "global":
        raise TrailError("GAME_CHANNEL_INVALID", f"未知游戏渠道 {channel}")

    last_error: TrailError | OSError | None = None
    for path, source in _iter_launch_game_candidates(game_path=game_path, channel=channel):
        try:
            result = _launch_game_from_path(
                path=path,
                channel=channel,
                channel_config=channel_config,
                launch_args=args,
                use_cmd=use_cmd,
            )
            if result.get("started") is True and result.get("already_running") is not True:
                try:
                    write_launch_path(channel, str(path))
                except OSError as error:
                    result["warnings"] = [_build_game_path_persist_warning(error)]
            return result
        except (TrailError, OSError) as exc:
            if source == "explicit":
                raise _build_explicit_game_launch_error(path, exc) from exc
            last_error = exc
            continue

    if game_path is None:
        raise TrailError("GAME_PATH_REQUIRED", "请提供游戏路径") from last_error
    raise TrailError("GAME_PATH_NOT_FOUND", f"未找到游戏启动路径 {Path(game_path)}")


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


def _scale_canonical_capture_value(value: int | float, *, target_size: int, canonical_size: int) -> int | float:
    if isinstance(value, float) and 0.0 <= value <= 1.0:
        return value
    scaled = round(target_size * (float(value) / canonical_size))
    return min(max(scaled, 0), max(target_size, 0))


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
            if getattr(window, "isMinimized", False):
                ctypes.windll.user32.ShowWindow(int(hwnd), 9)
                sleep(0.1)
            _force_window_foreground(int(hwnd))
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

    def capture_image(self, *, from_x=None, from_y=None, to_x=None, to_y=None, normalize: bool = True):
        window = self._resolve_window()
        region = self._resolve_region(window)
        if all(value is not None for value in (from_x, from_y, to_x, to_y)):
            region = region.sub_region(
                _scale_canonical_capture_value(from_x, target_size=region.width, canonical_size=CANONICAL_CLIENT_WIDTH),
                _scale_canonical_capture_value(from_y, target_size=region.height, canonical_size=CANONICAL_CLIENT_HEIGHT),
                _scale_canonical_capture_value(to_x, target_size=region.width, canonical_size=CANONICAL_CLIENT_WIDTH),
                _scale_canonical_capture_value(to_y, target_size=region.height, canonical_size=CANONICAL_CLIENT_HEIGHT),
            )

        hwnd = getattr(window, "_hWnd", None)
        capture_hwnd = int(hwnd) if hwnd is not None else None
        capture_region = region
        if capture_hwnd is not None and all(value is None for value in (from_x, from_y, to_x, to_y)):
            overlay_target = _find_owned_overlay_target(capture_hwnd, region)
            if overlay_target is not None:
                capture_hwnd, capture_region = overlay_target
        if sys.platform == "win32" and capture_hwnd is not None:
            scaled_region = _scale_region_for_screen_capture(capture_region, capture_hwnd)
            try:
                image = _capture_with_windows_capture(capture_hwnd, capture_region)
            except Exception:
                try:
                    image = _grab_region_with_imagegrab(scaled_region)
                except Exception:
                    try:
                        image = _crop_window_image_to_region(
                            _grab_window_with_imagegrab(capture_hwnd),
                            hwnd=capture_hwnd,
                            client_region=capture_region,
                        )
                    except Exception:
                        try:
                            image = _capture_win32_window(capture_hwnd, capture_region)
                        except TrailError as exc:
                            if exc.code != "SCREENSHOT_FAILED":
                                raise
                            self.prepare_input()
                            image = _grab_region_with_imagegrab(scaled_region)
        else:
            image = _grab_region_with_imagegrab(region)
        if normalize:
            image = self._normalize_captured_image(
                image,
                target_size=_target_capture_size(region, int(hwnd) if hwnd is not None else None),
            )
        return image

    def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None) -> bytes:
        image = self.capture_image(from_x=from_x, from_y=from_y, to_x=to_x, to_y=to_y, normalize=True)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def capture_to_workspace(self, request_id: str | None = None) -> Path:
        path = self.workspace / f"{_safe_capture_request_id(request_id)}.jpg"
        image = Image.open(BytesIO(self.capture())).convert("RGB")
        image.save(path, format="JPEG", quality=90, optimize=True)
        return path
