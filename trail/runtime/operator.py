from __future__ import annotations

import ctypes
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import sys
import threading
from time import monotonic, sleep
from typing import Any, Protocol

from PIL import Image, ImageChops, ImageOps, ImageStat

from trail.core.errors import TrailError
from trail.runtime.model import Box, Region
from trail.runtime.ocr_config import OCR_PROVIDER_UNAVAILABLE, OcrRequestConfig, normalize_runtime_ocr_request_config
from trail.runtime.window import WindowsWindowController


OCR_LOW_CONFIDENCE = "OCR_LOW_CONFIDENCE"
OCR_LOW_CONFIDENCE_MESSAGE = "ocr average score below 0.92; result may be incomplete"
OCR_LOW_CONFIDENCE_THRESHOLD = 0.92


class WindowController(Protocol):
    def capture(self, *, from_x=None, from_y=None, to_x=None, to_y=None): ...
    def capture_to_workspace(self, request_id: str | None = None) -> Path: ...
    def prepare_input(self) -> None: ...
    def is_foreground(self) -> bool: ...
    def client_region(self): ...
    def to_screen_point(self, x: int | float, y: int | float) -> tuple[int, int]: ...


class ImageMatcher(Protocol):
    def locate(self, template: str, image) -> Box | None: ...


@dataclass(frozen=True)
class OcrRunResult:
    pieces: list[Any]
    warnings: list[dict[str, Any]]
    trace: list[dict[str, Any]]


class OcrRunFailure(TrailError):
    def __init__(self, code: str, message: str, *, warnings: list[dict[str, Any]] | None = None, trace: list[dict[str, Any]] | None = None):
        super().__init__(code, message)
        self.warnings = [dict(item) for item in (warnings or [])]
        self.trace = [dict(item) for item in (trace or [])]


@dataclass(frozen=True)
class _OcrAttemptResult:
    mode: str
    scale_applied: str
    pieces: list[Any]
    warnings: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    hits: int
    average_score: float | None


class OcrEngine(Protocol):
    def run(self, image, *, ocr: OcrRequestConfig | None = None) -> OcrRunResult: ...


class InputDriver(Protocol):
    def ensure_available(self) -> None: ...
    def click(self, x: float, y: float, **kwargs) -> None: ...
    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None: ...
    def press(self, key: str) -> None: ...
    def hotkey(self, *keys: str) -> None: ...
    def type_text(self, text: str) -> None: ...


class RuntimeOperator:
    FAST_OCR_MAX_SIZE = (1280, 720)

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
        self._debug_context: dict[str, Any] = {}
        self._request_local = threading.local()
        self._last_input_at: float | None = None

    def _capture_scope_active(self) -> bool:
        return bool(getattr(self._request_local, "capture_scope_depth", 0))

    def begin_capture_scope(self) -> None:
        depth = int(getattr(self._request_local, "capture_scope_depth", 0)) + 1
        self._request_local.capture_scope_depth = depth
        if depth == 1:
            self._request_local.warnings = []
            self._request_local.trace = []
            self._request_local.debug_context = {}

    def end_capture_scope(self) -> None:
        depth = int(getattr(self._request_local, "capture_scope_depth", 0))
        self._request_local.capture_scope_depth = max(0, depth - 1)

    POST_INPUT_CAPTURE_DELAY_SECONDS = 1.0

    def _record_trace(self, step: str, **payload: Any) -> None:
        self._active_trace_buffer().append({"step": step, **payload})

    def _append_trace(self, payload: dict[str, Any]) -> None:
        self._active_trace_buffer().append(dict(payload))

    def _request_warnings_buffer(self) -> list[dict[str, Any]]:
        warnings = getattr(self._request_local, "warnings", None)
        if warnings is None:
            warnings = []
            self._request_local.warnings = warnings
        return warnings

    def _request_trace_buffer(self) -> list[dict[str, Any]]:
        trace = getattr(self._request_local, "trace", None)
        if trace is None:
            trace = []
            self._request_local.trace = trace
        return trace

    def _request_debug_context_buffer(self) -> dict[str, Any]:
        debug_context = getattr(self._request_local, "debug_context", None)
        if debug_context is None:
            debug_context = {}
            self._request_local.debug_context = debug_context
        return debug_context

    def _active_warnings_buffer(self) -> list[dict[str, Any]]:
        if self._capture_scope_active():
            return self._request_warnings_buffer()
        return self._warnings

    def _active_trace_buffer(self) -> list[dict[str, Any]]:
        if self._capture_scope_active():
            return self._request_trace_buffer()
        return self._trace

    def _active_debug_context_buffer(self) -> dict[str, Any]:
        if self._capture_scope_active():
            return self._request_debug_context_buffer()
        return self._debug_context

    def _append_warning(self, warning: dict[str, Any]) -> None:
        self._active_warnings_buffer().append(dict(warning))

    def _set_debug_context(self, **payload: Any) -> None:
        self._active_debug_context_buffer().update(payload)

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
        self._append_warning(
            {
                "code": "WINDOW_NOT_FOREGROUND",
                "message": "输入命令执行后窗口不在前台，本次操作可能失败；可能是窗口未在前台，或拉回前台失败",
            }
        )

    def _ensure_foreground_before_input(self) -> None:
        is_foreground = getattr(self.window, "is_foreground", None)
        if not callable(is_foreground):
            return
        try:
            foreground = bool(is_foreground())
        except Exception:
            return
        self._record_trace("foreground_prepare_check", foreground=foreground)
        if foreground:
            return
        raise TrailError("WINDOW_NOT_FOREGROUND", "窗口不在前台，无法执行输入")

    def _mark_input_action(self) -> None:
        self._last_input_at = monotonic()

    def _wait_for_post_input_settle(self) -> None:
        if self._last_input_at is None:
            return
        elapsed = monotonic() - self._last_input_at
        remaining = self.POST_INPUT_CAPTURE_DELAY_SECONDS - elapsed
        if remaining <= 0:
            return
        sleep(remaining)
        self._record_trace("capture_settle_delay", seconds=round(remaining, 3))

    def collect_warnings(self) -> list[dict[str, Any]]:
        if self._capture_scope_active():
            warnings = list(self._request_warnings_buffer())
            self._request_local.warnings = []
            return warnings
        warnings = list(self._warnings)
        self._warnings.clear()
        collect_window_warnings = getattr(self.window, "collect_warnings", None)
        if callable(collect_window_warnings):
            warnings.extend(collect_window_warnings() or [])
        return warnings

    def consume_debug_trace(self) -> list[dict[str, Any]]:
        if self._capture_scope_active():
            trace = list(self._request_trace_buffer())
            self._request_local.trace = []
            return trace
        trace = list(self._trace)
        self._trace.clear()
        return trace

    def consume_debug_context(self) -> dict[str, Any]:
        if self._capture_scope_active():
            debug_context = dict(self._request_debug_context_buffer())
            self._request_local.debug_context = {}
            return debug_context
        debug_context = dict(self._debug_context)
        self._debug_context.clear()
        return debug_context

    def match_references(self, screenshot_path: Path | str, limit: int = 3) -> list[dict[str, Any]]:
        return _match_reference_images(Path(screenshot_path), limit=limit, reference_root=self.reference_root)

    def screenshot(self, *, from_x=None, from_y=None, to_x=None, to_y=None):
        return self.window.capture(from_x=from_x, from_y=from_y, to_x=to_x, to_y=to_y)

    @staticmethod
    def _decode_ocr_image(image):
        if isinstance(image, Image.Image):
            return image
        if isinstance(image, (bytes, bytearray)):
            with Image.open(BytesIO(image)) as decoded:
                return decoded.copy()
        return image

    def _prepare_ocr_image(self, image, *, ocr: OcrRequestConfig):
        if ocr.ocr_mode != "fast":
            return image, 1.0, 1.0
        if isinstance(image, (bytes, bytearray)):
            with Image.open(BytesIO(image)) as decoded:
                if decoded.width <= self.FAST_OCR_MAX_SIZE[0] and decoded.height <= self.FAST_OCR_MAX_SIZE[1]:
                    return image, 1.0, 1.0
                source = decoded.copy()
        else:
            decoded = self._decode_ocr_image(image)
            if not isinstance(decoded, Image.Image):
                return decoded, 1.0, 1.0
            if decoded.width <= self.FAST_OCR_MAX_SIZE[0] and decoded.height <= self.FAST_OCR_MAX_SIZE[1]:
                return decoded, 1.0, 1.0
            source = decoded.copy()
        source_width = source.width
        source_height = source.height
        scaled = source
        scaled.thumbnail(self.FAST_OCR_MAX_SIZE)
        return scaled, source_width / scaled.width, source_height / scaled.height

    @staticmethod
    def _scale_ocr_box(box: dict[str, Any], *, scale_x: float, scale_y: float) -> dict[str, Any]:
        scaled = dict(box)
        for key, factor in (("left", scale_x), ("top", scale_y), ("width", scale_x), ("height", scale_y)):
            value = scaled.get(key)
            if isinstance(value, (int, float)):
                scaled[key] = int(round(value * factor))
        return scaled

    @staticmethod
    def _scale_ocr_polygon(polygon, *, scale_x: float, scale_y: float):
        if not isinstance(polygon, (list, tuple)):
            return polygon
        scaled_points = []
        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                return polygon
            x = point[0]
            y = point[1]
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                return polygon
            scaled_point = [x * scale_x, y * scale_y]
            scaled_points.append(tuple(scaled_point) if isinstance(point, tuple) else scaled_point)
        return tuple(scaled_points) if isinstance(polygon, tuple) else scaled_points

    def _map_ocr_piece_to_capture_space(self, piece, *, scale_x: float, scale_y: float):
        if isinstance(piece, dict):
            box = piece.get("box")
            if not isinstance(box, dict):
                return piece
            mapped_piece = dict(piece)
            mapped_piece["box"] = self._scale_ocr_box(box, scale_x=scale_x, scale_y=scale_y)
            return mapped_piece
        if scale_x == 1.0 and scale_y == 1.0:
            return piece
        if not isinstance(piece, (list, tuple)) or not piece:
            return piece
        mapped_piece = list(piece)
        mapped_piece[0] = self._scale_ocr_polygon(mapped_piece[0], scale_x=scale_x, scale_y=scale_y)
        return tuple(mapped_piece) if isinstance(piece, tuple) else mapped_piece

    def _map_ocr_pieces_to_capture_space(self, pieces: list[Any], *, scale_x: float, scale_y: float) -> list[Any]:
        if scale_x == 1.0 and scale_y == 1.0:
            return list(pieces)
        return [self._map_ocr_piece_to_capture_space(piece, scale_x=scale_x, scale_y=scale_y) for piece in pieces]

    @staticmethod
    def _extract_ocr_score(piece: Any) -> float | None:
        if isinstance(piece, dict):
            score = piece.get("score")
            if isinstance(score, (int, float)):
                return float(score)
            return None
        if isinstance(piece, (list, tuple)) and len(piece) >= 3:
            score = piece[2]
            if isinstance(score, (int, float)):
                return float(score)
        return None

    def _average_ocr_score(self, pieces: list[Any]) -> float | None:
        scores = [score for piece in pieces if (score := self._extract_ocr_score(piece)) is not None]
        if not scores:
            return None
        return sum(scores) / len(scores)

    @staticmethod
    def _has_warning_code(warnings: list[dict[str, Any]], code: str) -> bool:
        return any(item.get("code") == code for item in warnings)

    def _build_ocr_warnings(self, *, pieces: list[Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        collected = [dict(item) for item in warnings]
        average_score = self._average_ocr_score(pieces)
        if average_score is not None and average_score < OCR_LOW_CONFIDENCE_THRESHOLD and not self._has_warning_code(collected, OCR_LOW_CONFIDENCE):
            collected.append({"code": OCR_LOW_CONFIDENCE, "message": OCR_LOW_CONFIDENCE_MESSAGE})
        return collected

    def _run_ocr_attempt(self, image, *, ocr_config: OcrRequestConfig) -> _OcrAttemptResult:
        prepared_image, scale_x, scale_y = self._prepare_ocr_image(image, ocr=ocr_config)
        scale_applied = "native"
        if ocr_config.ocr_mode == "fast" and (scale_x != 1.0 or scale_y != 1.0):
            scale_applied = f"{self.FAST_OCR_MAX_SIZE[0]}x{self.FAST_OCR_MAX_SIZE[1]}"
        self._set_debug_context(
            ocr_mode_effective=ocr_config.ocr_mode,
            ocr_scale_applied=scale_applied,
        )
        result = self.ocr_engine.run(prepared_image, ocr=ocr_config)
        if not isinstance(result, OcrRunResult):
            raise TypeError("ocr engine must return OcrRunResult")
        pieces = self._map_ocr_pieces_to_capture_space(result.pieces, scale_x=scale_x, scale_y=scale_y)
        warnings = self._build_ocr_warnings(pieces=pieces, warnings=result.warnings)
        return _OcrAttemptResult(
            mode=ocr_config.ocr_mode,
            scale_applied=scale_applied,
            pieces=pieces,
            warnings=warnings,
            trace=[dict(item) for item in result.trace],
            hits=len(pieces),
            average_score=self._average_ocr_score(pieces),
        )

    def _ocr_retry_reason(self, attempt: _OcrAttemptResult) -> str:
        if attempt.hits == 0:
            return "no_hits"
        if attempt.average_score is not None and attempt.average_score < OCR_LOW_CONFIDENCE_THRESHOLD:
            return "low_confidence"
        if self._has_warning_code(attempt.warnings, OCR_LOW_CONFIDENCE):
            return "warning"
        return "none"

    def _should_retry_high(self, *, ocr_config: OcrRequestConfig, attempt: _OcrAttemptResult) -> tuple[bool, str]:
        if ocr_config.ocr_mode != "fast" or ocr_config.retry_high == "never":
            return False, "none"
        if ocr_config.retry_high == "always":
            return True, "none"
        reason = self._ocr_retry_reason(attempt)
        return reason != "none", reason

    @staticmethod
    def _to_high_ocr_config(ocr_config: OcrRequestConfig) -> OcrRequestConfig:
        return OcrRequestConfig(
            provider=ocr_config.provider,
            lang=ocr_config.lang,
            use_cls=ocr_config.use_cls,
            text_score=ocr_config.text_score,
            ocr_mode="high",
            retry_high=ocr_config.retry_high,
        )

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

    def ocr(
        self,
        *,
        capture: dict[str, Any] | None = None,
        ocr: OcrRequestConfig | None = None,
    ):
        capture_payload = dict(capture or {})
        ocr_config = normalize_runtime_ocr_request_config(ocr)
        image = self.screenshot(**capture_payload)
        self._set_debug_context(
            ocr_mode_requested=ocr_config.ocr_mode,
            ocr_mode_effective=ocr_config.ocr_mode,
            ocr_scale_applied="native",
            ocr_retry_high=0,
            ocr_retry_reason="none",
        )
        try:
            fast_attempt = self._run_ocr_attempt(image, ocr_config=ocr_config)
        except OcrRunFailure as exc:
            for warning in exc.warnings:
                self._append_warning(warning)
            for trace in exc.trace:
                self._append_trace(trace)
            raise
        retry_high, retry_reason = self._should_retry_high(ocr_config=ocr_config, attempt=fast_attempt)
        final_attempt = fast_attempt
        final_warnings = list(fast_attempt.warnings)
        effective_mode = fast_attempt.mode
        scale_applied = fast_attempt.scale_applied
        trace_payloads = list(fast_attempt.trace)

        if retry_high:
            try:
                high_attempt = self._run_ocr_attempt(image, ocr_config=self._to_high_ocr_config(ocr_config))
            except TrailError as exc:
                trace_payloads.extend([dict(item) for item in getattr(exc, "trace", [])])
                if fast_attempt.hits == 0:
                    final_attempt = None
                    final_warnings = []
                    effective_mode = "high"
                    scale_applied = "native"
            else:
                trace_payloads.extend(high_attempt.trace)
                if high_attempt.hits > 0 or fast_attempt.hits == 0:
                    final_attempt = high_attempt
                    final_warnings = list(high_attempt.warnings)
                    effective_mode = high_attempt.mode
                    scale_applied = high_attempt.scale_applied

        if final_attempt is None:
            pieces: list[Any] = []
        else:
            pieces = list(final_attempt.pieces)
            final_warnings = list(final_attempt.warnings)
            effective_mode = final_attempt.mode
            scale_applied = final_attempt.scale_applied

        for warning in final_warnings:
            self._append_warning(warning)
        for trace in trace_payloads:
            self._append_trace(trace)
        self._set_debug_context(
            ocr_mode_requested=ocr_config.ocr_mode,
            ocr_mode_effective=effective_mode,
            ocr_scale_applied=scale_applied,
            ocr_retry_high=1 if retry_high else 0,
            ocr_retry_reason=retry_reason,
        )
        self._record_trace("ocr", kwargs=dict(capture_payload), pieces=len(pieces))
        return pieces

    def _prepare_input_target(self) -> None:
        ensure_available = getattr(self.input, "ensure_available", None)
        if callable(ensure_available):
            ensure_available()
        self.window.prepare_input()
        self._record_trace("prepare_input")
        self._ensure_foreground_before_input()

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
        self._mark_input_action()
        self._record_trace("click_point", point=[x, y], screen_point=[screen_x, screen_y])
        self._check_foreground_after_input()

    def drag_to(self, from_x: float, from_y: float, to_x: float, to_y: float):
        self._prepare_input_target()
        screen_from_x, screen_from_y = self._to_screen_point(from_x, from_y)
        screen_to_x, screen_to_y = self._to_screen_point(to_x, to_y)
        self.input.drag(screen_from_x, screen_from_y, screen_to_x, screen_to_y)
        self._mark_input_action()
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
        self._mark_input_action()
        self._record_trace("press_key", key=key, presses=presses, interval=interval)
        self._check_foreground_after_input()

    def hotkey(self, *keys: str):
        self._prepare_input_target()
        self.input.hotkey(*keys)
        self._mark_input_action()
        self._record_trace("hotkey", keys=list(keys))
        self._check_foreground_after_input()

    def type_text(self, text: str):
        self._prepare_input_target()
        self.input.type_text(text)
        self._mark_input_action()
        self._record_trace("type_text", text=text)
        self._check_foreground_after_input()

    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        try:
            self._wait_for_post_input_settle()
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


@dataclass(frozen=True)
class OcrEngineKey:
    effective_provider: str
    model_identity: str
    use_cls: bool


@dataclass
class _PendingOcrEngineBuild:
    ready: threading.Event
    wrapper: "CachedOcrEngine | None" = None
    error: Exception | None = None


class CachedOcrEngine:
    def __init__(self, engine, *, key: OcrEngineKey, session_providers: dict[str, list[str]]):
        self.engine = engine
        self.key = key
        self.session_providers = {name: list(providers) for name, providers in session_providers.items()}
        self._run_lock = threading.Lock()

    def run(self, image, *, ocr: OcrRequestConfig) -> list[Any]:
        with self._run_lock:
            result, _ = self.engine(image, use_det=True, use_cls=ocr.use_cls, use_rec=True, text_score=ocr.text_score)
        return result or []


class RapidOcrAdapter:
    def __init__(self):
        self._engine_cache: dict[OcrEngineKey, CachedOcrEngine] = {}
        self._pending_builds: dict[OcrEngineKey, _PendingOcrEngineBuild] = {}
        self._auto_dml_negative_cache: dict[OcrEngineKey, str] = {}
        self._cache_lock = threading.Lock()

    @staticmethod
    def _available_providers() -> list[str]:
        try:
            import onnxruntime  # type: ignore
        except Exception:
            return []
        get_available_providers = getattr(onnxruntime, "get_available_providers", None)
        if not callable(get_available_providers):
            return []
        providers = get_available_providers() or []
        return [str(provider) for provider in providers]

    @staticmethod
    def _provider_name(effective_provider: str) -> str:
        if effective_provider == "dml":
            return "DmlExecutionProvider"
        return "CPUExecutionProvider"

    @staticmethod
    def _format_exception_reason(error: Exception) -> str:
        message = str(error)
        if not message:
            return type(error).__name__
        return f"{type(error).__name__}: {message}"

    @staticmethod
    def _model_identity(ocr_config: OcrRequestConfig) -> str:
        return f"rapidocr:ppocrv4-mobile:{ocr_config.lang}"

    def _provider_attempts(self, requested_provider: str, *, available_providers: list[str]) -> tuple[str, ...]:
        dml_available = "DmlExecutionProvider" in available_providers
        if requested_provider == "cpu":
            return ("cpu",)
        if requested_provider == "dml":
            if not dml_available:
                raise TrailError(OCR_PROVIDER_UNAVAILABLE, "requested dml provider unavailable")
            return ("dml",)
        if dml_available:
            return ("dml", "cpu")
        return ("cpu",)

    def _build_run_result(
        self,
        *,
        pieces: list[Any],
        ocr_config: OcrRequestConfig,
        effective_provider: str,
        available_providers: list[str],
        fallback_from: str | None = None,
        reason: str | None = None,
    ) -> OcrRunResult:
        trace: dict[str, Any] = {
            "step": "ocr_provider",
            "requested_provider": ocr_config.provider,
            "effective_provider": effective_provider,
            "lang": ocr_config.lang,
            "available_providers": list(available_providers),
        }
        if fallback_from is not None:
            trace["fallback_from"] = fallback_from
        if reason is not None:
            trace["reason"] = reason
        return OcrRunResult(pieces=list(pieces), warnings=[], trace=[trace])

    def _provider_failure(
        self,
        *,
        ocr_config: OcrRequestConfig,
        effective_provider: str,
        available_providers: list[str],
        reason: str | None = None,
    ) -> OcrRunFailure:
        result = self._build_run_result(
            pieces=[],
            ocr_config=ocr_config,
            effective_provider=effective_provider,
            available_providers=available_providers,
            reason=reason,
        )
        return OcrRunFailure(
            OCR_PROVIDER_UNAVAILABLE,
            "requested dml provider unavailable",
            warnings=result.warnings,
            trace=result.trace,
        )

    @staticmethod
    def _session_providers(engine, *, use_cls: bool) -> dict[str, list[str]]:
        providers = {
            "det": list(engine.text_det.infer.session.get_providers()),
            "rec": list(engine.text_rec.session.session.get_providers()),
        }
        if use_cls:
            providers["cls"] = list(engine.text_cls.infer.session.get_providers())
        return providers

    def _validate_session_providers(
        self,
        *,
        effective_provider: str,
        session_providers: dict[str, list[str]],
    ) -> None:
        expected = self._provider_name(effective_provider)
        for name, providers in session_providers.items():
            if providers and providers[0] == expected:
                continue
            raise TrailError(OCR_PROVIDER_UNAVAILABLE, f"requested {effective_provider} provider unavailable")

    def _build_engine(self, *, effective_provider: str, use_cls: bool):
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        use_dml = effective_provider == "dml"
        return RapidOCR(
            det_use_dml=use_dml,
            cls_use_dml=use_dml and use_cls,
            rec_use_dml=use_dml,
        )

    def _build_cached_engine(self, *, key: OcrEngineKey):
        engine = self._build_engine(effective_provider=key.effective_provider, use_cls=key.use_cls)
        session_providers = self._session_providers(engine, use_cls=key.use_cls)
        self._validate_session_providers(
            effective_provider=key.effective_provider,
            session_providers=session_providers,
        )
        return CachedOcrEngine(engine, key=key, session_providers=session_providers)

    def _get_or_build_cached_engine(self, key: OcrEngineKey) -> CachedOcrEngine:
        while True:
            with self._cache_lock:
                cached = self._engine_cache.get(key)
                if cached is not None:
                    return cached
                pending = self._pending_builds.get(key)
                if pending is None:
                    pending = _PendingOcrEngineBuild(ready=threading.Event())
                    self._pending_builds[key] = pending
                    owner = True
                else:
                    owner = False

            if owner:
                try:
                    wrapper = self._build_cached_engine(key=key)
                except Exception as exc:
                    with self._cache_lock:
                        pending = self._pending_builds.pop(key, pending)
                        pending.error = exc
                        pending.ready.set()
                    raise

                with self._cache_lock:
                    pending = self._pending_builds.pop(key, pending)
                    self._engine_cache[key] = wrapper
                    self._auto_dml_negative_cache.pop(key, None)
                    pending.wrapper = wrapper
                    pending.ready.set()
                return wrapper

            pending.ready.wait()
            if pending.wrapper is not None:
                return pending.wrapper
            if pending.error is not None:
                raise pending.error

    def _discard_cached_engine(self, key: OcrEngineKey, wrapper: CachedOcrEngine | None = None) -> None:
        with self._cache_lock:
            cached = self._engine_cache.get(key)
            if cached is None:
                return
            if wrapper is not None and cached is not wrapper:
                return
            self._engine_cache.pop(key, None)

    def _mark_auto_dml_negative(self, key: OcrEngineKey, reason: str) -> None:
        with self._cache_lock:
            self._auto_dml_negative_cache[key] = reason

    def _auto_dml_negative_reason(self, key: OcrEngineKey) -> str | None:
        with self._cache_lock:
            return self._auto_dml_negative_cache.get(key)

    def run(self, image, *, ocr: OcrRequestConfig | None = None) -> OcrRunResult:
        ocr_config = normalize_runtime_ocr_request_config(ocr)
        try:
            import rapidocr_onnxruntime  # type: ignore
        except Exception as exc:
            raise TrailError("OCR_BACKEND_UNAVAILABLE", "rapidocr backend unavailable") from exc

        screenshot = Image.open(BytesIO(image)) if isinstance(image, (bytes, bytearray)) else image
        available_providers = self._available_providers()
        last_error: Exception | None = None
        fallback_reason: str | None = None
        try:
            attempts = self._provider_attempts(ocr_config.provider, available_providers=available_providers)
        except TrailError as exc:
            raise self._provider_failure(
                ocr_config=ocr_config,
                effective_provider="unavailable",
                available_providers=available_providers,
                reason=str(exc),
            ) from exc

        for effective_provider in attempts:
            key = OcrEngineKey(
                effective_provider=effective_provider,
                model_identity=self._model_identity(ocr_config),
                use_cls=bool(ocr_config.use_cls),
            )
            if ocr_config.provider == "auto" and effective_provider == "dml":
                negative_reason = self._auto_dml_negative_reason(key)
                if negative_reason is not None:
                    fallback_reason = negative_reason
                    continue
            try:
                wrapper = self._get_or_build_cached_engine(key)
            except TrailError as exc:
                last_error = exc
                if ocr_config.provider == "auto" and effective_provider == "dml":
                    fallback_reason = str(exc)
                    self._mark_auto_dml_negative(key, fallback_reason)
                    continue
                raise self._provider_failure(
                    ocr_config=ocr_config,
                    effective_provider=effective_provider,
                    available_providers=available_providers,
                    reason=str(exc),
                ) from exc
            except Exception as exc:
                mapped_error: Exception = exc
                if effective_provider == "dml":
                    mapped_error = self._provider_failure(
                        ocr_config=ocr_config,
                        effective_provider=effective_provider,
                        available_providers=available_providers,
                        reason=self._format_exception_reason(exc),
                    )
                    if ocr_config.provider == "auto":
                        fallback_reason = self._format_exception_reason(exc)
                        self._mark_auto_dml_negative(key, fallback_reason)
                        last_error = mapped_error
                        continue
                    raise mapped_error from exc
                last_error = mapped_error
                raise
            try:
                pieces = wrapper.run(screenshot, ocr=ocr_config)
            except Exception as exc:
                if effective_provider == "dml":
                    self._discard_cached_engine(key, wrapper)
                    if ocr_config.provider == "auto":
                        fallback_reason = self._format_exception_reason(exc)
                        last_error = self._provider_failure(
                            ocr_config=ocr_config,
                            effective_provider=effective_provider,
                            available_providers=available_providers,
                            reason=fallback_reason,
                        )
                        continue
                    raise self._provider_failure(
                        ocr_config=ocr_config,
                        effective_provider=effective_provider,
                        available_providers=available_providers,
                        reason=self._format_exception_reason(exc),
                    ) from exc
                raise
            return self._build_run_result(
                pieces=pieces,
                ocr_config=ocr_config,
                effective_provider=effective_provider,
                available_providers=available_providers,
                fallback_from="dml" if fallback_reason is not None else None,
                reason=fallback_reason,
            )

        if isinstance(last_error, TrailError):
            raise last_error
        if last_error is not None:
            raise last_error
        raise TrailError(OCR_PROVIDER_UNAVAILABLE, "requested dml provider unavailable")


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
        if normalized.startswith("f") and normalized[1:].isdigit():
            function_index = int(normalized[1:])
            if 1 <= function_index <= 24:
                return 0x6F + function_index
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
