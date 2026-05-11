from __future__ import annotations

from collections.abc import Mapping
import inspect
from pathlib import Path
from time import monotonic
from time import sleep

from trail.core.errors import TrailError


START_RUN_CLICK_ENTER_TEXT = "点击进入"


def _normalize_ocr_text(piece: object) -> str:
    if isinstance(piece, Mapping):
        text = piece.get("text") or piece.get("ocr_text") or ""
        return "".join(str(text).split())
    if isinstance(piece, (list, tuple)) and len(piece) >= 2 and isinstance(piece[1], str):
        return "".join(piece[1].split())
    return ""


def _extract_box(piece: object) -> Mapping[str, object] | None:
    if isinstance(piece, (list, tuple)) and piece:
        return _extract_box_from_polygon(piece[0])
    if isinstance(piece, Mapping):
        return _extract_box_from_mapping(piece)
    return None


def _extract_box_from_mapping(piece: Mapping[str, object]) -> Mapping[str, object] | None:
    box = piece.get("box")
    if isinstance(box, Mapping):
        return box

    polygon = piece.get("polygon") or piece.get("points")
    if polygon is not None:
        return _extract_box_from_polygon(polygon)

    center = piece.get("center")
    if isinstance(center, Mapping):
        try:
            center_x = float(center["x"])
            center_y = float(center["y"])
        except (KeyError, TypeError, ValueError):
            return None
        return {"left": center_x, "top": center_y, "width": 0.0, "height": 0.0}
    if isinstance(center, (list, tuple)) and len(center) == 2:
        try:
            center_x = float(center[0])
            center_y = float(center[1])
        except (TypeError, ValueError):
            return None
        return {"left": center_x, "top": center_y, "width": 0.0, "height": 0.0}

    if all(key in piece for key in ("left", "top", "width", "height")):
        return piece
    return None


def _extract_box_from_polygon(polygon: object) -> Mapping[str, object] | None:
    if not isinstance(polygon, (list, tuple)):
        return None
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (IndexError, TypeError, ValueError):
        return None
    if not xs or not ys:
        return None
    left = min(xs)
    top = min(ys)
    return {
        "left": left,
        "top": top,
        "width": max(xs) - left,
        "height": max(ys) - top,
    }


def _box_center(box: Mapping[str, object]) -> tuple[int, int] | None:
    try:
        left = int(box["left"])
        top = int(box["top"])
        width = int(box["width"])
        height = int(box["height"])
    except (KeyError, TypeError, ValueError):
        return None
    return left + width // 2, top + height // 2


def _window_binding_to_dict(binding: object) -> dict[str, object]:
    if isinstance(binding, Mapping):
        return dict(binding)
    to_dict = getattr(binding, "to_dict", None)
    if callable(to_dict):
        serialized = to_dict()
        if isinstance(serialized, Mapping):
            return dict(serialized)
    raise TypeError(f"{type(binding).__name__!r} object is not a mapping")


class RuntimeService:
    def __init__(self) -> None:
        self._runtimes: dict[tuple[str, str | None], object] = {}

    def get_runtime(self, *, workspace_root: str, window_binding: dict | None):
        from trail.runtime.operator import build_runtime

        workspace = Path(workspace_root) / ".trail" / "shots"
        binding_key = None if window_binding is None else repr(sorted(window_binding.items()))
        cache_key = (str(Path(workspace_root)), binding_key)
        if cache_key not in self._runtimes:
            window_title = "崩坏：星穹铁道"
            if isinstance(window_binding, dict):
                maybe_title = window_binding.get("title")
                if isinstance(maybe_title, str) and maybe_title:
                    window_title = maybe_title
            build_kwargs = {
                "workspace": workspace,
                "window_title": window_title,
                "window_binding": window_binding,
            }
            parameters = inspect.signature(build_runtime).parameters.values()
            if any(
                parameter.name == "reference_root" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            ):
                build_kwargs["reference_root"] = Path(workspace_root)
            self._runtimes[cache_key] = build_runtime(**build_kwargs)
        return self._runtimes[cache_key]

    def attach_window(self, *, window_title: str):
        from trail.runtime.window import attach_window

        return attach_window(window_title)

    def launch_game(self, **payload):
        from trail.runtime.window import launch_game

        resolved = dict(payload)
        if "game_path" in resolved:
            game_path = resolved["game_path"]
            resolved["game_path"] = None if game_path is None else Path(game_path)
        return launch_game(**resolved)

    def start_run(
        self,
        *,
        workspace_root: str | None = None,
        window_title: str,
        game_path: str | None = None,
        channel: str = "official",
        timeout_seconds: int = 30,
        interval_seconds: int = 1,
        cancellation_token=None,
    ):
        if cancellation_token is not None:
            cancellation_token.throw_if_cancelled()
        try:
            binding = _window_binding_to_dict(self.attach_window(window_title=window_title))
            return {**binding, "status": "attached"}
        except TrailError as error:
            if error.code != "WINDOW_NOT_FOUND":
                raise

        if cancellation_token is not None:
            cancellation_token.throw_if_cancelled()
        launch_result = self.launch_game(game_path=game_path, channel=channel)
        if not launch_result.get("started") and not launch_result.get("already_running"):
            raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")

        binding: dict[str, object] | None = None
        last_error: TrailError | None = None
        deadline = monotonic() + timeout_seconds
        while True:
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled()
            try:
                binding = _window_binding_to_dict(self.attach_window(window_title=window_title))
                break
            except TrailError as error:
                if error.code != "WINDOW_NOT_FOUND":
                    raise
                last_error = error
            if monotonic() >= deadline:
                break
            sleep(interval_seconds)
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled()

        if binding is not None:
            return {
                **binding,
                "status": self._post_launch_status(
                    workspace_root=workspace_root,
                    binding=binding,
                    timeout_seconds=timeout_seconds,
                    interval_seconds=interval_seconds,
                    cancellation_token=cancellation_token,
                ),
            }
        if last_error is not None:
            raise last_error
        raise TrailError("WINDOW_NOT_FOUND", f"window not found: {window_title}")

    def _post_launch_status(
        self,
        *,
        workspace_root: str | None,
        binding: dict[str, object],
        timeout_seconds: int,
        interval_seconds: int,
        cancellation_token=None,
    ) -> str:
        if workspace_root is None:
            return "launched_needs_check"

        runtime = self.get_runtime(workspace_root=workspace_root, window_binding=binding)
        if runtime is None:
            return "launched_needs_check"

        deadline = monotonic() + timeout_seconds
        while True:
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled()
            target = self._detect_click_enter_target(runtime)
            if target is not None:
                if cancellation_token is not None:
                    cancellation_token.throw_if_cancelled()
                runtime.click_point(*target)
                return "launched_clicked_enter"
            if monotonic() >= deadline:
                return "launched_needs_check"
            sleep(interval_seconds)
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled()

    def _detect_click_enter_target(self, runtime) -> tuple[int, int] | None:
        try:
            pieces = runtime.ocr() or []
        except Exception:
            return None

        if not isinstance(pieces, (list, tuple)):
            return None

        for piece in pieces:
            if START_RUN_CLICK_ENTER_TEXT not in _normalize_ocr_text(piece):
                continue
            box = _extract_box(piece)
            if box is None:
                continue
            center = _box_center(box)
            if center is not None:
                return center
        return None
