from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import re
from statistics import median
import sys
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from trail.core.errors import TrailError
from trail.runtime.model import Box, WindowBinding
from tests.support.fake_daemon import FakeDaemonClient, build_success_response


_TRACE_TS_PATTERN = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"


def _assert_finalized_trace_event(event: dict[str, object], *, step: str, ok: int) -> None:
    assert event["step"] == step
    assert type(event["ok"]) is int
    assert event["ok"] == ok
    assert type(event["dur_ms"]) is int
    assert event["dur_ms"] >= 0
    assert isinstance(event["ts"], str)
    assert re.fullmatch(_TRACE_TS_PATTERN, event["ts"])


def _find_trace_event(trace: list[dict[str, object]], step: str) -> dict[str, object]:
    return next(item for item in trace if item.get("step") == step)


@pytest.fixture
def isolated_user_launch_paths(monkeypatch, tmp_path) -> Path:
    import trail.runtime.launch_paths as launch_paths

    state_file = tmp_path / "isolated-game-paths.json"
    monkeypatch.setattr(launch_paths, "game_paths_path_for_user", lambda: state_file)
    return state_file


@pytest.fixture(autouse=True)
def _isolate_user_launch_paths(isolated_user_launch_paths: Path) -> Path:
    return isolated_user_launch_paths


def test_read_launch_paths_returns_empty_when_file_missing(tmp_path):
    from trail.runtime.launch_paths import read_launch_paths

    paths = read_launch_paths(tmp_path / "game-paths.json")

    assert paths == {}


def test_read_launch_paths_treats_invalid_json_as_empty(tmp_path):
    from trail.runtime.launch_paths import read_launch_paths

    state_file = tmp_path / "game-paths.json"
    state_file.write_text("{broken", encoding="utf-8")

    paths = read_launch_paths(state_file)

    assert paths == {}


def test_read_launch_paths_treats_non_mapping_payload_as_empty(tmp_path):
    from trail.runtime.launch_paths import read_launch_paths

    state_file = tmp_path / "game-paths.json"
    state_file.write_text('[]', encoding="utf-8")

    paths = read_launch_paths(state_file)

    assert paths == {}


@pytest.mark.parametrize(
    ("payload"),
    [
        ('{"official": []}'),
        ('{"official": {"last_success_game_path": 123}}'),
    ],
)
def test_read_launch_paths_treats_invalid_channel_bucket_as_empty(tmp_path, payload: str):
    from trail.runtime.launch_paths import read_launch_paths

    state_file = tmp_path / "game-paths.json"
    state_file.write_text(payload, encoding="utf-8")

    paths = read_launch_paths(state_file)

    assert paths == {}


def test_write_launch_path_persists_last_success_game_path_by_channel(tmp_path):
    from trail.runtime.launch_paths import read_launch_paths, write_launch_path

    state_file = tmp_path / "game-paths.json"

    write_launch_path("official", r"C:\Games\StarRail.exe", state_file)
    write_launch_path("bilibili", r"D:\Games\StarRail.exe", state_file)

    assert read_launch_paths(state_file) == {
        "official": {"last_success_game_path": r"C:\Games\StarRail.exe"},
        "bilibili": {"last_success_game_path": r"D:\Games\StarRail.exe"},
    }


def test_write_launch_path_keeps_sibling_channels_under_concurrent_writes(monkeypatch, tmp_path):
    import trail.runtime.launch_paths as launch_paths

    state_file = tmp_path / "game-paths.json"
    state_file.write_text("{}", encoding="utf-8")
    original_read_text = Path.read_text
    release_reads = threading.Event()
    read_count_lock = threading.Lock()
    read_count = 0
    start_barrier = threading.Barrier(3)

    def slow_read_text(self, *args, **kwargs):
        nonlocal read_count

        result = original_read_text(self, *args, **kwargs)
        if Path(self) == state_file:
            with read_count_lock:
                read_count += 1
                if read_count >= 2:
                    release_reads.set()
            release_reads.wait(timeout=0.05)
        return result

    def worker(channel: str, game_path: str):
        start_barrier.wait()
        launch_paths.write_launch_path(channel, game_path, state_file)

    monkeypatch.setattr(Path, "read_text", slow_read_text)
    first = threading.Thread(target=worker, args=("official", r"C:\Games\StarRail.exe"))
    second = threading.Thread(target=worker, args=("bilibili", r"D:\Games\StarRail.exe"))

    first.start()
    second.start()
    start_barrier.wait()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert launch_paths.read_launch_paths(state_file) == {
        "official": {"last_success_game_path": r"C:\Games\StarRail.exe"},
        "bilibili": {"last_success_game_path": r"D:\Games\StarRail.exe"},
    }


def test_write_launch_path_uses_atomic_replace(monkeypatch, tmp_path):
    import trail.runtime.launch_paths as launch_paths

    state_file = tmp_path / "game-paths.json"
    replace_calls: list[tuple[Path, Path]] = []
    original_replace = Path.replace

    def record_replace(self, target):
        replace_calls.append((Path(self), Path(target)))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", record_replace)

    launch_paths.write_launch_path("official", r"C:\Games\StarRail.exe", state_file)

    assert len(replace_calls) == 1
    temp_path, target = replace_calls[0]
    assert target == state_file
    assert temp_path != state_file
    assert temp_path.parent == state_file.parent


def test_read_launch_paths_does_not_observe_partial_write_during_atomic_update(monkeypatch, tmp_path):
    import trail.runtime.launch_paths as launch_paths

    state_file = tmp_path / "game-paths.json"
    state_file.write_text(
        '{"official": {"last_success_game_path": "C:/Games/old.exe"}}',
        encoding="utf-8",
    )
    original_write_text = Path.write_text
    partial_written = threading.Event()
    allow_write_finish = threading.Event()

    def slow_write_text(self, data, *args, **kwargs):
        path = Path(self)
        if path.parent == state_file.parent and path.name.startswith(state_file.name):
            original_write_text(path, '{"official": {', *args, **kwargs)
            partial_written.set()
            allow_write_finish.wait(timeout=1.0)
        return original_write_text(path, data, *args, **kwargs)

    def worker():
        launch_paths.write_launch_path("official", r"C:\Games\new.exe", state_file)

    monkeypatch.setattr(Path, "write_text", slow_write_text)
    thread = threading.Thread(target=worker)
    thread.start()

    assert partial_written.wait(timeout=1.0)
    assert launch_paths.read_launch_paths(state_file) == {
        "official": {"last_success_game_path": "C:/Games/old.exe"},
    }

    allow_write_finish.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert launch_paths.read_launch_paths(state_file) == {
        "official": {"last_success_game_path": r"C:\Games\new.exe"},
    }


def test_launch_paths_use_user_state_file_when_path_omitted(monkeypatch, tmp_path):
    import trail.runtime.launch_paths as launch_paths

    state_file = tmp_path / "game-paths.json"
    monkeypatch.setattr(launch_paths, "game_paths_path_for_user", lambda: state_file)

    launch_paths.write_launch_path("official", r"C:\Games\StarRail.exe")

    assert launch_paths.read_launch_paths() == {
        "official": {"last_success_game_path": r"C:\Games\StarRail.exe"},
    }


class _CommandRuntimeStub:
    def __init__(self):
        self._shot = Path(".trail/shots/daemon-command.png")
        self.ocr_result = [{"text": "银狼"}]
        self.ocr_calls: list[dict[str, object | None]] = []
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

    def ocr(self, *, capture=None, ocr=None, **kwargs):
        self.ocr_calls.append(
            {
                "capture": None if capture is None else dict(capture),
                "ocr": ocr,
                "kwargs": dict(kwargs),
            }
        )
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


def _rewrite_session_payload(store, session_id: str, **updates):
    path = store._path_for(session_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


FAST_OCR_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ocr"
FAST_DENSE_NOTICE_FIXTURE = FAST_OCR_FIXTURE_DIR / "fast-dense-notice.jpg"
FAST_SPARSE_LOGIN_FIXTURE = FAST_OCR_FIXTURE_DIR / "fast-sparse-login.jpg"
FAST_DENSE_NOTICE_ANCHORS = (
    "资讯",
    "公告",
    "4.1版本游戏优化及已知问题说明",
    "问题说明",
    "亲爱的开拓者：",
    "已知问题",
    "2026/04/10",
    "2026/03/30",
    "「无名勋礼」更新说明",
    "42版本新增关卡",
)
FAST_DENSE_NOTICE_ABSOLUTE_ANCHORS = (
    "资讯",
    "公告",
    "4.1版本游戏优化及已知问题说明",
    "问题说明",
    "亲爱的开拓者：",
    "已知问题",
    "2026/04/10",
    "2026/03/30",
    "「无名勋礼」更新说明",
    "42版本新增关卡",
)
FAST_SPARSE_LOGIN_ANCHORS = (
    "设置",
    "mi",
    "登出",
    "退出",
    "点击进入",
)
FAST_SPARSE_LOGIN_ABSOLUTE_ANCHORS = (
    "mi",
    "登出",
    "退出",
    "点击进入",
)
FAST_SPARSE_LOGIN_BOX_ANCHORS = ("登出", "退出")


@dataclass(frozen=True)
class FixtureOcrRun:
    capture_size: tuple[int, int]
    processed_size: tuple[int, int]
    pieces: list[object]


@dataclass(frozen=True)
class OcrQualityMetrics:
    anchor_delta: int
    median_center_shift_px: float
    median_iou: float
    variant_processed_size: tuple[int, int]


def benchmark_ocr_mode_against_native(
    fixture: Path,
    *,
    ocr_mode: str,
    anchors: tuple[str, ...],
    box_anchors: tuple[str, ...] | None = None,
) -> OcrQualityMetrics:
    native = run_fixture_ocr(fixture, ocr_mode="high")
    variant = run_fixture_ocr(fixture, ocr_mode=ocr_mode)
    return compute_anchor_box_metrics(native, variant, anchors=anchors, box_anchors=box_anchors)


def fixture_anchor_hits(fixture: Path, *, ocr_mode: str) -> set[str]:
    run = run_fixture_ocr(fixture, ocr_mode=ocr_mode)
    return set(
        _piece_text_box_map(
            run.pieces,
            capture_size=run.capture_size,
            processed_size=run.processed_size,
        )
    )


def run_fixture_ocr(fixture: Path, *, ocr_mode: str) -> FixtureOcrRun:
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    with Image.open(fixture) as image:
        capture_size = image.size
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
    capture_bytes = buffer.getvalue()

    class RecordingEngine:
        def __init__(self):
            self.adapter = operator_module.RapidOcrAdapter()
            self.processed_sizes: list[tuple[int, int]] = []

        def run(self, image, *, ocr=None):
            if isinstance(image, (bytes, bytearray)):
                with Image.open(BytesIO(image)) as decoded:
                    self.processed_sizes.append(decoded.size)
            else:
                self.processed_sizes.append(image.size)
            return self.adapter.run(image, ocr=ocr)

    engine = RecordingEngine()
    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: capture_bytes,
            capture_to_workspace=lambda request_id=None: fixture,
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=engine,
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
    )

    pieces = runtime.ocr(
        capture={},
        ocr=OcrRequestConfig(provider="cpu", ocr_mode=ocr_mode, retry_high="never"),
    )
    return FixtureOcrRun(capture_size=capture_size, processed_size=engine.processed_sizes[-1], pieces=pieces)


def compute_anchor_box_metrics(
    native: FixtureOcrRun,
    variant: FixtureOcrRun,
    *,
    anchors: tuple[str, ...],
    box_anchors: tuple[str, ...] | None = None,
) -> OcrQualityMetrics:
    native_map = _piece_text_box_map(native.pieces, capture_size=native.capture_size, processed_size=native.processed_size)
    variant_map = _piece_text_box_map(variant.pieces, capture_size=variant.capture_size, processed_size=variant.processed_size)
    native_hits = sum(1 for text in anchors if text in native_map)
    variant_hits = sum(1 for text in anchors if text in variant_map)
    shared_box_anchors = [text for text in (box_anchors or anchors) if text in native_map and text in variant_map]
    center_shifts = [_compute_box_center_shift(native_map[text], variant_map[text]) for text in shared_box_anchors]
    ious = [_compute_box_iou(native_map[text], variant_map[text]) for text in shared_box_anchors]
    return OcrQualityMetrics(
        anchor_delta=variant_hits - native_hits,
        median_center_shift_px=median(center_shifts) if center_shifts else 999.0,
        median_iou=median(ious) if ious else 0.0,
        variant_processed_size=variant.processed_size,
    )


def _piece_text_box_map(
    pieces: list[object],
    *,
    capture_size: tuple[int, int],
    processed_size: tuple[int, int],
) -> dict[str, dict[str, float]]:
    del capture_size, processed_size
    mapping: dict[str, dict[str, float]] = {}
    for piece in pieces:
        resolved = _piece_text_and_box(piece)
        if resolved is None:
            continue
        text, box = resolved
        if not text or text in mapping:
            continue
        mapping[text] = dict(box)
    return mapping


def _piece_text_and_box(piece: object) -> tuple[str, dict[str, float]] | None:
    if isinstance(piece, dict):
        text = str(piece.get("text") or "").strip()
        box = piece.get("box")
        if not text or not isinstance(box, dict):
            return None
        return text, {
            "left": float(box["left"]),
            "top": float(box["top"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
        }
    if not isinstance(piece, (list, tuple)) or len(piece) < 2:
        return None
    polygon, text = piece[0], str(piece[1]).strip()
    if not text or not isinstance(polygon, (list, tuple)):
        return None
    try:
        xs = [float(point[0]) for point in polygon]
        ys = [float(point[1]) for point in polygon]
    except (IndexError, TypeError, ValueError):
        return None
    if not xs or not ys:
        return None
    return text, {
        "left": min(xs),
        "top": min(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }


def _compute_box_center_shift(left: dict[str, float], right: dict[str, float]) -> float:
    left_center = (left["left"] + left["width"] / 2.0, left["top"] + left["height"] / 2.0)
    right_center = (right["left"] + right["width"] / 2.0, right["top"] + right["height"] / 2.0)
    return ((left_center[0] - right_center[0]) ** 2 + (left_center[1] - right_center[1]) ** 2) ** 0.5


def _compute_box_iou(left: dict[str, float], right: dict[str, float]) -> float:
    inter_left = max(left["left"], right["left"])
    inter_top = max(left["top"], right["top"])
    inter_right = min(left["left"] + left["width"], right["left"] + right["width"])
    inter_bottom = min(left["top"] + left["height"], right["top"] + right["height"])
    if inter_right <= inter_left or inter_bottom <= inter_top:
        return 0.0
    inter_area = (inter_right - inter_left) * (inter_bottom - inter_top)
    left_area = left["width"] * left["height"]
    right_area = right["width"] * right["height"]
    return inter_area / (left_area + right_area - inter_area)


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


def test_command_service_splits_ocr_payload_into_capture_and_ocr_options(tmp_path: Path):
    from trail.daemon.command_service import CommandService
    from trail.runtime.ocr_config import OcrRequestConfig

    runtime = _CommandRuntimeStub()
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="ocr.read",
            payload={
                "from_x": 1,
                "from_y": 2,
                "to_x": 3,
                "to_y": 4,
                "provider": "dml",
                "lang": "ch",
                "use_cls": "false",
                "text_score": "0.6",
            },
        )
    )

    assert payload["ok"] is True
    assert runtime.ocr_calls == [
        {
            "capture": {"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
            "ocr": OcrRequestConfig(provider="dml", lang="ch", use_cls=False, text_score=0.6),
            "kwargs": {},
        }
    ]


@pytest.mark.parametrize(
    ("invalid_payload", "expected_code", "expected_message"),
    [
        ({"provider": "gpu"}, "OCR_INPUT_INVALID", "unsupported ocr provider: gpu"),
        ({"lang": "en"}, "OCR_LANG_UNSUPPORTED", "unsupported ocr lang: en"),
        ({"text_score": "not-a-float"}, "OCR_INPUT_INVALID", "invalid ocr text score: not-a-float"),
        ({"provider": "dml", "use_clss": True}, "OCR_INPUT_INVALID", "unknown ocr payload fields: use_clss"),
    ],
)
def test_command_service_rejects_invalid_ocr_payload_values(
    tmp_path: Path,
    invalid_payload: dict[str, object],
    expected_code: str,
    expected_message: str,
):
    from trail.daemon.command_service import CommandService

    runtime = _CommandRuntimeStub()
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="ocr.read",
            payload=invalid_payload,
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == {"code": expected_code, "message": expected_message}
    assert runtime.ocr_calls == []


def test_runtime_operator_ocr_passes_explicit_ocr_options_to_engine_without_forwarding_them_to_screenshot(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    capture_calls: list[dict[str, object | None]] = []
    engine_calls: list[dict[str, object]] = []
    ocr_config = OcrRequestConfig(provider="auto", lang="ch", use_cls=True, text_score=0.6, ocr_mode="high", retry_high="never")

    class WindowStub:
        def capture(self, **kwargs):
            capture_calls.append(dict(kwargs))
            return b"demo-bytes"

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / ".trail" / "shots" / "req-ocr-read-provider.png"

    class EngineStub:
        def run(self, image, **kwargs):
            engine_calls.append({"image": image, "kwargs": dict(kwargs)})
            return operator_module.OcrRunResult(pieces=[{"text": "银狼"}], warnings=[], trace=[])

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr(
        capture={"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
        ocr=ocr_config,
    )

    assert capture_calls == [{"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4}]
    assert engine_calls == [{"image": b"demo-bytes", "kwargs": {"ocr": ocr_config}}]
    assert result == [{"text": "银狼"}]


def test_runtime_operator_ocr_requires_capture_dict_for_region_arguments(tmp_path: Path):
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: b"demo-bytes",
            capture_to_workspace=lambda request_id=None: tmp_path / ".trail" / "shots" / "req-ocr-read-provider.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, **kwargs: [{"text": "银狼"}]),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    with pytest.raises(TypeError):
        runtime.ocr(from_x=1)


def test_runtime_operator_fast_mode_downscales_fullscreen_to_1280x720(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")
    seen_sizes: list[tuple[int, int]] = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            del ocr
            assert not isinstance(image, (bytes, bytearray))
            seen_sizes.append(image.size)
            return operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "fast-fullscreen.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    runtime.ocr(capture={}, ocr=OcrRequestConfig(ocr_mode="fast", retry_high="never"))

    assert seen_sizes == [(1280, 720)]


def test_runtime_operator_fast_mode_keeps_bytes_contract_when_no_resize_needed(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (400, 120), color="white").save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    capture_calls: list[dict[str, int]] = []
    engine_calls: list[dict[str, object]] = []

    class WindowStub:
        def capture(self, **kwargs):
            capture_calls.append(dict(kwargs))
            return image_bytes

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / "fast-region.png"

    class EngineStub:
        def run(self, image, *, ocr=None):
            engine_calls.append({"image": image, "kwargs": {"ocr": ocr}})
            return operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    runtime.ocr(
        capture={"from_x": 10, "from_y": 20, "to_x": 410, "to_y": 140},
        ocr=OcrRequestConfig(ocr_mode="fast", retry_high="never"),
    )

    assert capture_calls == [{"from_x": 10, "from_y": 20, "to_x": 410, "to_y": 140}]
    assert engine_calls == [{"image": image_bytes, "kwargs": {"ocr": OcrRequestConfig(ocr_mode="fast", retry_high="never")}}]


def test_runtime_operator_fast_mode_maps_boxes_back_to_original_capture_space(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image, ocr
            return operator_module.OcrRunResult(
                pieces=[
                    [[[100.0, 50.0], [200.0, 50.0], [200.0, 100.0], [100.0, 100.0]], "按钮", 0.99],
                    {"text": "确认", "score": 0.88, "box": {"left": 300.0, "top": 120.0, "width": 80.0, "height": 40.0}},
                ],
                warnings=[],
                trace=[],
            )

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "fast-map.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="never"))

    assert result == [
        [[[150.0, 75.0], [300.0, 75.0], [300.0, 150.0], [150.0, 150.0]], "按钮", 0.99],
        {"text": "确认", "score": 0.88, "box": {"left": 450, "top": 180, "width": 120, "height": 60}},
    ]


def test_command_service_ocr_read_fast_mode_maps_boxes_back_to_original_capture_space(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.daemon.command_service import CommandService
    from trail.output.rendering import render_output

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")

    class WindowStub:
        def capture(self, **kwargs):
            del kwargs
            return buffer.getvalue()

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / ".trail" / "shots" / "req-ocr-fast-map.jpg"

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image, ocr
            return operator_module.OcrRunResult(
                pieces=[[[[100.0, 50.0], [200.0, 50.0], [200.0, 100.0], [100.0, 100.0]], "按钮", 0.99]],
                warnings=[],
                trace=[],
            )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="ocr.read",
            payload={"ocr_mode": "fast", "retry_high": "never", "provider": "cpu"},
        )
    )

    assert payload["ok"] is True
    assert payload["data"]["result"] == [
        [[[150.0, 75.0], [300.0, 75.0], [300.0, 150.0], [150.0, 150.0]], "按钮", 0.99]
    ]
    assert render_output("ocr.read", payload).splitlines() == [
        "ok ocr.read hits=1",
        f"shot path={payload['screenshot']}",
        "info read_image_first=1",
        "text value=按钮 box=150,75,150,75 center=225,112",
    ]


def test_command_service_ocr_read_fast_mode_dict_box_renders_without_crashing(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.daemon.command_service import CommandService
    from trail.output.rendering import render_output

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")

    class WindowStub:
        def capture(self, **kwargs):
            del kwargs
            return buffer.getvalue()

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / ".trail" / "shots" / "req-ocr-fast-dict-box.png"

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image, ocr
            return operator_module.OcrRunResult(
                pieces=[
                    {"text": "按钮", "score": 0.93, "box": {"left": 81.0, "top": 58.0, "width": 49.0, "height": 24.0}}
                ],
                warnings=[],
                trace=[],
            )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="ocr.read",
            payload={"ocr_mode": "fast", "retry_high": "never", "provider": "cpu"},
        )
    )

    assert payload["ok"] is True
    assert payload["data"]["result"] == [
        {"text": "按钮", "score": 0.93, "box": {"left": 122, "top": 87, "width": 74, "height": 36}}
    ]
    assert render_output("ocr.read", payload).splitlines() == [
        "ok ocr.read hits=1",
        f"shot path={payload['screenshot']}",
        "info read_image_first=1",
        "text value=按钮 box=122,87,74,36 center=159,105",
    ]


def test_runtime_operator_high_mode_keeps_existing_bytes_contract(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (640, 360), color="white").save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    engine_calls: list[dict[str, object]] = []
    ocr_config = OcrRequestConfig(provider="auto", lang="ch", use_cls=True, text_score=0.6, ocr_mode="high", retry_high="never")

    class WindowStub:
        def capture(self, **kwargs):
            return image_bytes

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / "high-bytes.png"

    class EngineStub:
        def run(self, image, **kwargs):
            engine_calls.append({"image": image, "kwargs": dict(kwargs)})
            return operator_module.OcrRunResult(pieces=[{"text": "银狼"}], warnings=[], trace=[])

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr(capture={"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4}, ocr=ocr_config)

    assert engine_calls == [{"image": image_bytes, "kwargs": {"ocr": ocr_config}}]
    assert result == [{"text": "银狼"}]


def test_runtime_operator_produces_OCR_LOW_CONFIDENCE_warning_from_scores(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image, ocr
            return operator_module.OcrRunResult(
                pieces=[{"text": "银狼", "score": 0.91}],
                warnings=[],
                trace=[],
            )

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "ocr-low-confidence.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="never"))

    assert result == [{"text": "银狼", "score": 0.91}]
    assert runtime.collect_warnings() == [
        {
            "code": "OCR_LOW_CONFIDENCE",
            "message": "ocr average score below 0.92; result may be incomplete",
        }
    ]


def test_runtime_operator_retry_high_always_returns_high_result(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")
    calls: list[dict[str, object]] = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            assert ocr is not None
            if isinstance(image, (bytes, bytearray)):
                with Image.open(BytesIO(image)) as decoded:
                    size = decoded.size
            else:
                size = image.size
            calls.append({"mode": ocr.ocr_mode, "size": size})
            if len(calls) == 1:
                return operator_module.OcrRunResult(
                    pieces=[{"text": "快档", "score": 0.99}],
                    warnings=[],
                    trace=[{"step": "ocr_provider", "attempt": "fast"}],
                )
            return operator_module.OcrRunResult(
                pieces=[{"text": "高精度", "score": 0.99}],
                warnings=[],
                trace=[{"step": "ocr_provider", "attempt": "high"}],
            )

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "ocr-retry-always.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="always"))
    trace = runtime.consume_debug_trace()

    assert calls == [
        {"mode": "fast", "size": (1280, 720)},
        {"mode": "high", "size": (1920, 1080)},
    ]
    assert result == [{"text": "高精度", "score": 0.99}]
    assert runtime.consume_debug_context() == {}
    assert [item["attempt"] for item in trace if item.get("step") == "ocr_provider"] == ["fast", "high"]
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=1)
    assert ocr_event["pieces"] == 1
    assert ocr_event["mode_requested"] == "fast"
    assert ocr_event["mode_effective"] == "high"
    assert ocr_event["scale_applied"] == "native"
    assert ocr_event["retry_high"] == 1
    assert ocr_event["retry_reason"] == "none"


def test_runtime_operator_retry_high_auto_keeps_fast_result_when_high_fails(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")
    calls: list[str] = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image
            assert ocr is not None
            calls.append(ocr.ocr_mode)
            if len(calls) == 1:
                return operator_module.OcrRunResult(
                    pieces=[{"text": "快档", "score": 0.91}],
                    warnings=[],
                    trace=[],
                )
            raise operator_module.OcrRunFailure(
                "OCR_BACKEND_UNAVAILABLE",
                "high retry failed",
                warnings=[{"code": "OCR_HIGH_FAILED", "message": "high retry failed"}],
            )

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "ocr-retry-auto-fallback.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="auto"))
    trace = runtime.consume_debug_trace()

    assert calls == ["fast", "high"]
    assert result == [{"text": "快档", "score": 0.91}]
    assert runtime.collect_warnings() == [
        {
            "code": "OCR_LOW_CONFIDENCE",
            "message": "ocr average score below 0.92; result may be incomplete",
        }
    ]
    assert runtime.consume_debug_context() == {}
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=1)
    assert ocr_event["pieces"] == 1
    assert ocr_event["mode_requested"] == "fast"
    assert ocr_event["mode_effective"] == "fast"
    assert ocr_event["scale_applied"] == "1280x720"
    assert ocr_event["retry_high"] == 1
    assert ocr_event["retry_reason"] == "low_confidence"
    assert ocr_event["retry_error_type"] == "OcrRunFailure"
    assert ocr_event["retry_error_code"] == "OCR_BACKEND_UNAVAILABLE"
    assert ocr_event["retry_msg"] == "high retry failed"


def test_runtime_operator_retry_high_auto_runtime_error_still_raises_and_keeps_failed_trace(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")
    calls: list[str] = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image
            assert ocr is not None
            calls.append(ocr.ocr_mode)
            if len(calls) == 1:
                return operator_module.OcrRunResult(
                    pieces=[{"text": "快档", "score": 0.91}],
                    warnings=[],
                    trace=[],
                )
            raise RuntimeError("high retry runtime failed")

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "ocr-retry-auto-runtime-fallback.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="high retry runtime failed"):
        runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="auto"))

    trace = runtime.consume_debug_trace()

    assert calls == ["fast", "high"]
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=0)
    assert ocr_event["pieces"] == 0
    assert ocr_event["mode_effective"] == "high"
    assert ocr_event["scale_applied"] == "native"
    assert ocr_event["retry_high"] == 1
    assert ocr_event["retry_reason"] == "low_confidence"
    assert ocr_event["error_type"] == "RuntimeError"
    assert ocr_event["msg"] == "high retry runtime failed"


def test_runtime_operator_returns_empty_result_when_fast_has_no_hits_and_high_fails(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")
    calls: list[str] = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image
            assert ocr is not None
            calls.append(ocr.ocr_mode)
            if len(calls) == 1:
                return operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])
            raise operator_module.OcrRunFailure("OCR_BACKEND_UNAVAILABLE", "high retry failed")

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "ocr-retry-empty.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="auto"))
    trace = runtime.consume_debug_trace()

    assert calls == ["fast", "high"]
    assert result == []
    assert runtime.collect_warnings() == []
    assert runtime.consume_debug_context() == {}
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=1)
    assert ocr_event["pieces"] == 0
    assert ocr_event["mode_requested"] == "fast"
    assert ocr_event["mode_effective"] == "high"
    assert ocr_event["scale_applied"] == "native"
    assert ocr_event["retry_high"] == 1
    assert ocr_event["retry_reason"] == "no_hits"
    assert ocr_event["retry_error_type"] == "OcrRunFailure"
    assert ocr_event["retry_error_code"] == "OCR_BACKEND_UNAVAILABLE"
    assert ocr_event["retry_msg"] == "high retry failed"


def test_runtime_operator_no_hits_high_runtime_error_still_raises_and_keeps_failed_trace(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")
    calls: list[str] = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image
            assert ocr is not None
            calls.append(ocr.ocr_mode)
            if len(calls) == 1:
                return operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])
            raise RuntimeError("high retry runtime failed")

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "ocr-retry-empty-runtime.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    with pytest.raises(RuntimeError, match="high retry runtime failed"):
        runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="auto"))

    trace = runtime.consume_debug_trace()

    assert calls == ["fast", "high"]
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=0)
    assert ocr_event["pieces"] == 0
    assert ocr_event["mode_effective"] == "high"
    assert ocr_event["scale_applied"] == "native"
    assert ocr_event["retry_high"] == 1
    assert ocr_event["retry_reason"] == "no_hits"
    assert ocr_event["error_type"] == "RuntimeError"
    assert ocr_event["msg"] == "high retry runtime failed"


def test_runtime_operator_high_mode_retry_high_is_noop(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (640, 360), color="white").save(buffer, format="PNG")
    calls: list[str] = []

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image
            assert ocr is not None
            calls.append(ocr.ocr_mode)
            return operator_module.OcrRunResult(pieces=[{"text": "原图"}], warnings=[], trace=[])

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / "ocr-high-noop.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="high", retry_high="always"))
    trace = runtime.consume_debug_trace()

    assert calls == ["high"]
    assert result == [{"text": "原图"}]
    assert runtime.consume_debug_context() == {}
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=1)
    assert ocr_event["pieces"] == 1
    assert ocr_event["mode_requested"] == "high"
    assert ocr_event["mode_effective"] == "high"
    assert ocr_event["scale_applied"] == "native"
    assert ocr_event["retry_high"] == 0
    assert ocr_event["retry_reason"] == "none"


def test_runtime_operator_ocr_image_emits_finalized_trace_and_keeps_provider_trace(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    class EngineStub:
        def run(self, image, *, ocr=None):
            assert isinstance(image, Image.Image)
            assert image.size == (201, 61)
            assert ocr is not None
            assert ocr.ocr_mode == "high"
            return operator_module.OcrRunResult(
                pieces=[{"text": "希儿", "score": 0.99}],
                warnings=[{"code": "OCR_ENGINE_HINT", "message": "hint"}],
                trace=[{"step": "ocr_provider", "attempt": "high"}],
            )

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: None,
            capture_to_workspace=lambda request_id=None: tmp_path / "ocr-image.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    result = runtime.ocr_image(
        Image.new("RGB", (201, 61), color="white"),
        ocr=OcrRequestConfig(provider="cpu", ocr_mode="high", retry_high="never"),
    )
    trace = runtime.consume_debug_trace()

    assert result == [{"text": "希儿", "score": 0.99}]
    assert runtime.collect_warnings() == [{"code": "OCR_ENGINE_HINT", "message": "hint"}]
    assert runtime.consume_debug_context() == {}
    assert any(item.get("step") == "ocr_provider" and item.get("attempt") == "high" for item in trace)
    ocr_event = _find_trace_event(trace, "ocr_image")
    _assert_finalized_trace_event(ocr_event, step="ocr_image", ok=1)
    assert ocr_event["pieces"] == 1
    assert ocr_event["mode_requested"] == "high"
    assert ocr_event["mode_effective"] == "high"
    assert ocr_event["scale_applied"] == "native"
    assert ocr_event["retry_high"] == 0
    assert ocr_event["retry_reason"] == "none"


def test_runtime_operator_ocr_failure_emits_finalized_ocr_trace_and_keeps_provider_trace(tmp_path: Path) -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: Image.new("RGB", (32, 32), color="white"),
            capture_to_workspace=lambda request_id=None: tmp_path / "shot.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(
            run=lambda image, ocr=None: (_ for _ in ()).throw(
                operator_module.OcrRunFailure(
                    "OCR_BACKEND_UNAVAILABLE",
                    "ocr backend unavailable",
                    trace=[{"step": "ocr_provider", "requested_provider": "cpu"}],
                )
            )
        ),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    with pytest.raises(operator_module.OcrRunFailure):
        runtime.ocr(capture={})

    trace = runtime.consume_debug_trace()
    assert any(item.get("step") == "ocr_provider" and item.get("requested_provider") == "cpu" for item in trace)
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=0)
    assert ocr_event["pieces"] == 0
    assert ocr_event["mode_requested"] == "fast"
    assert ocr_event["mode_effective"] == "fast"
    assert ocr_event["retry_high"] == 0
    assert ocr_event["retry_reason"] == "none"
    assert ocr_event["error_code"] == "OCR_BACKEND_UNAVAILABLE"
    assert ocr_event["error_type"] == "OcrRunFailure"


def test_runtime_operator_wait_img_and_locate_keep_raw_box_until_debug_layer() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: operator_module.Box(left=10, top=20, width=30, height=40, source="template")),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    runtime.wait_img("entry.start", timeout=1, interval=0.1)

    trace = runtime.consume_debug_trace()
    locate_event = _find_trace_event(trace, "locate")
    wait_event = _find_trace_event(trace, "wait_img")
    _assert_finalized_trace_event(locate_event, step="locate", ok=1)
    _assert_finalized_trace_event(wait_event, step="wait_img", ok=1)
    assert isinstance(locate_event["box"], dict)
    assert locate_event["box"] == {"left": 10, "top": 20, "width": 30, "height": 40, "source": "template"}
    assert isinstance(wait_event["box"], dict)
    assert wait_event["box"] == {"left": 10, "top": 20, "width": 30, "height": 40, "source": "template"}


def test_runtime_operator_wait_img_failure_keeps_failed_helper_trace() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: (_ for _ in ()).throw(TrailError("SCREENSHOT_FAILED", "无法截取窗口内容")),
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    with pytest.raises(TrailError) as exc_info:
        runtime.wait_img("entry.start", timeout=1, interval=0.1)

    assert exc_info.value.code == "SCREENSHOT_FAILED"
    trace = runtime.consume_debug_trace()
    wait_event = _find_trace_event(trace, "wait_img")
    _assert_finalized_trace_event(wait_event, step="wait_img", ok=0)
    assert wait_event["box"] is None
    assert wait_event["error_code"] == "SCREENSHOT_FAILED"
    assert wait_event["error_type"] == "TrailError"
    assert wait_event["msg"] == "无法截取窗口内容"


def test_runtime_operator_ocr_screenshot_failure_emits_failed_ocr_trace() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: (_ for _ in ()).throw(TrailError("SCREENSHOT_FAILED", "无法截取窗口内容")),
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: (_ for _ in ()).throw(AssertionError("ocr engine should not run"))),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    with pytest.raises(TrailError) as exc_info:
        runtime.ocr(capture={})

    assert exc_info.value.code == "SCREENSHOT_FAILED"
    trace = runtime.consume_debug_trace()
    _assert_finalized_trace_event(_find_trace_event(trace, "screenshot"), step="screenshot", ok=0)
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=0)
    assert ocr_event["pieces"] == 0
    assert ocr_event["error_code"] == "SCREENSHOT_FAILED"
    assert ocr_event["error_type"] == "TrailError"
    assert ocr_event["msg"] == "无法截取窗口内容"


@pytest.mark.parametrize(
    ("runner", "expected_step"),
    [
        (lambda runtime: runtime.click_point(10, 20), "click_point"),
        (lambda runtime: runtime.drag_to(10, 20, 30, 40), "drag_to"),
        (lambda runtime: runtime.press_key("f", presses=1), "press_key"),
        (lambda runtime: runtime.hotkey("ctrl", "l"), "hotkey"),
        (lambda runtime: runtime.type_text("abc"), "type_text"),
    ],
)
def test_runtime_operator_input_helpers_keep_parent_trace_when_prepare_fails(runner, expected_step) -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            prepare_input=lambda: None,
            is_foreground=lambda: False,
            to_screen_point=lambda x, y: (x, y),
            capture=lambda **kwargs: b"demo",
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            ensure_available=lambda: None,
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    with pytest.raises(operator_module.TrailError) as exc_info:
        runner(runtime)

    assert exc_info.value.code == "WINDOW_NOT_FOREGROUND"
    trace = runtime.consume_debug_trace()
    helper_event = _find_trace_event(trace, expected_step)
    _assert_finalized_trace_event(helper_event, step=expected_step, ok=0)
    assert helper_event["error_code"] == "WINDOW_NOT_FOREGROUND"
    assert any(item.get("step") == "prepare_input" for item in trace)
    assert any(item.get("step") == "foreground_prepare_check" for item in trace)


def test_runtime_operator_click_point_keeps_parent_trace_when_coordinate_conversion_fails() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            prepare_input=lambda: None,
            is_foreground=lambda: True,
            to_screen_point=lambda x, y: (_ for _ in ()).throw(RuntimeError("point convert failed")),
            capture=lambda **kwargs: b"demo",
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            ensure_available=lambda: None,
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    with pytest.raises(RuntimeError, match="point convert failed"):
        runtime.click_point(10, 20)

    trace = runtime.consume_debug_trace()
    helper_event = _find_trace_event(trace, "click_point")
    _assert_finalized_trace_event(helper_event, step="click_point", ok=0)
    assert helper_event["error_type"] == "RuntimeError"
    assert helper_event["msg"] == "point convert failed"


def test_runtime_operator_wait_img_timeout_emits_success_trace_with_empty_box(monkeypatch) -> None:
    import trail.runtime.operator as operator_module

    monotonic_values = iter([100.0, 100.0, 101.1])
    monkeypatch.setattr(operator_module, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(operator_module, "sleep", lambda seconds: None)

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    assert runtime.wait_img("entry.start", timeout=1, interval=0.0) is None

    trace = runtime.consume_debug_trace()
    wait_event = _find_trace_event(trace, "wait_img")
    _assert_finalized_trace_event(wait_event, step="wait_img", ok=1)
    assert wait_event["found"] == 0
    assert wait_event["box"] is None


def test_runtime_operator_post_input_foreground_error_keeps_parent_and_child_trace() -> None:
    import trail.runtime.operator as operator_module

    class WindowStub:
        def __init__(self):
            self.foreground_checks = 0

        def capture(self, **kwargs):
            del kwargs
            return b"demo"

        def capture_to_workspace(self, request_id=None):
            del request_id
            return Path("shot.png")

        def prepare_input(self):
            return None

        def is_foreground(self):
            self.foreground_checks += 1
            return self.foreground_checks == 1

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
    runtime.raise_post_input_foreground_error = True

    with pytest.raises(operator_module.TrailError) as exc_info:
        runtime.click_point(10, 20)

    assert exc_info.value.code == "WINDOW_NOT_FOREGROUND"
    assert exc_info.value.completed_after_side_effect is True
    assert clicks == [(10, 20)]
    trace = runtime.consume_debug_trace()
    _assert_finalized_trace_event(_find_trace_event(trace, "click_point"), step="click_point", ok=0)
    _assert_finalized_trace_event(_find_trace_event(trace, "foreground_check"), step="foreground_check", ok=0)
    assert runtime.collect_warnings() == [
        {
            "code": "WINDOW_NOT_FOREGROUND",
            "message": "输入命令执行后窗口不在前台，本次操作可能失败；可能是窗口未在前台，或拉回前台失败",
        }
    ]


def test_runtime_operator_screenshot_and_ocr_image_emit_finalized_trace() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[{"text": "进入"}], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    runtime.screenshot()
    runtime.ocr_image(Image.new("RGB", (32, 32), color="white"))

    trace = runtime.consume_debug_trace()
    screenshot_event = _find_trace_event(trace, "screenshot")
    ocr_event = _find_trace_event(trace, "ocr_image")
    _assert_finalized_trace_event(screenshot_event, step="screenshot", ok=1)
    _assert_finalized_trace_event(ocr_event, step="ocr_image", ok=1)
    assert screenshot_event["source"] == "raw"
    assert ocr_event["pieces"] == 1


def test_runtime_operator_foreground_checks_emit_finalized_trace_on_success_and_failure() -> None:
    import trail.runtime.operator as operator_module

    success_runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            prepare_input=lambda: None,
            is_foreground=lambda: True,
            to_screen_point=lambda x, y: (x, y),
            capture=lambda **kwargs: b"demo",
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            ensure_available=lambda: None,
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    success_runtime.click_point(10, 20)
    success_trace = success_runtime.consume_debug_trace()

    _assert_finalized_trace_event(_find_trace_event(success_trace, "prepare_input"), step="prepare_input", ok=1)
    _assert_finalized_trace_event(_find_trace_event(success_trace, "foreground_prepare_check"), step="foreground_prepare_check", ok=1)
    _assert_finalized_trace_event(_find_trace_event(success_trace, "foreground_check"), step="foreground_check", ok=1)

    failure_runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            prepare_input=lambda: None,
            is_foreground=lambda: False,
            to_screen_point=lambda x, y: (x, y),
            capture=lambda **kwargs: b"demo",
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            ensure_available=lambda: None,
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    with pytest.raises(operator_module.TrailError):
        failure_runtime.click_point(10, 20)

    failure_trace = failure_runtime.consume_debug_trace()
    _assert_finalized_trace_event(_find_trace_event(failure_trace, "prepare_input"), step="prepare_input", ok=0)
    _assert_finalized_trace_event(_find_trace_event(failure_trace, "foreground_prepare_check"), step="foreground_prepare_check", ok=0)


@pytest.mark.parametrize(
    ("pieces", "warnings", "expected_reason"),
    [
        ([], [{"code": "OCR_LOW_CONFIDENCE", "message": "engine warned"}], "no_hits"),
        ([{"text": "快档", "score": 0.91}], [{"code": "OCR_LOW_CONFIDENCE", "message": "engine warned"}], "low_confidence"),
        ([{"text": "快档"}], [{"code": "OCR_LOW_CONFIDENCE", "message": "engine warned"}], "warning"),
    ],
)
def test_runtime_operator_retry_reason_priority_prefers_no_hits_over_low_confidence_and_warning(
    tmp_path: Path,
    pieces: list[dict[str, object]],
    warnings: list[dict[str, str]],
    expected_reason: str,
):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")

    class EngineStub:
        def __init__(self):
            self.calls = 0

        def run(self, image, *, ocr=None):
            del image, ocr
            self.calls += 1
            if self.calls == 1:
                return operator_module.OcrRunResult(pieces=list(pieces), warnings=list(warnings), trace=[])
            return operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: buffer.getvalue(),
            capture_to_workspace=lambda request_id=None: tmp_path / f"retry-reason-{expected_reason}.png",
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="auto"))
    trace = runtime.consume_debug_trace()

    ocr_event = _find_trace_event(trace, "ocr")
    assert ocr_event["retry_high"] == 1
    assert ocr_event["retry_reason"] == expected_reason


def test_command_service_ocr_read_retry_high_success_suppresses_fast_warning_from_default_output(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.daemon.command_service import CommandService
    from trail.output.rendering import render_output

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")

    class WindowStub:
        def capture(self, **kwargs):
            del kwargs
            return buffer.getvalue()

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / ".trail" / "shots" / "req-ocr-retry-success.png"

    class EngineStub:
        def __init__(self):
            self.calls = 0

        def run(self, image, *, ocr=None):
            del image
            assert ocr is not None
            self.calls += 1
            if self.calls == 1:
                return operator_module.OcrRunResult(
                    pieces=[{"text": "快档", "score": 0.91}],
                    warnings=[],
                    trace=[],
                )
            return operator_module.OcrRunResult(
                pieces=[{"text": "高精度", "score": 0.99}],
                warnings=[],
                trace=[],
            )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )
    runtime_service = _CommandRuntimeServiceStub(runtime)
    service = CommandService(runtime_service=runtime_service)

    payload = service.handle(
        _command_request(
            workspace_root=tmp_path,
            method="ocr.read",
            payload={"ocr_mode": "fast", "retry_high": "always", "provider": "cpu"},
        )
    )

    assert payload["ok"] is True
    assert payload["warnings"] == []
    assert payload["data"]["result"] == [{"text": "高精度", "score": 0.99}]
    assert render_output("ocr.read", payload).splitlines() == [
        "ok ocr.read hits=1",
        f"shot path={payload['screenshot']}",
        "info read_image_first=1",
        "text value=高精度",
    ]


def test_with_auto_capture_ocr_failure_keeps_ocr_trace_on_real_runtime_failure(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.output.capture import with_auto_capture
    from trail.runtime.ocr_config import OcrRequestConfig

    buffer = BytesIO()
    Image.new("RGB", (1920, 1080), color="white").save(buffer, format="PNG")

    class WindowStub:
        def capture(self, **kwargs):
            del kwargs
            return buffer.getvalue()

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return Path(".trail/shots/req-ocr-failure-context.png")

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image, ocr
            raise operator_module.OcrRunFailure("OCR_BACKEND_UNAVAILABLE", "ocr backend unavailable")

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
            hotkey=lambda *args, **kwargs: None,
            type_text=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    payload = with_auto_capture(
        runtime,
        lambda: {"result": runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu", ocr_mode="fast", retry_high="always"))},
        verbose=True,
    )

    assert payload["ok"] is False
    assert payload["error"] == {"code": "OCR_BACKEND_UNAVAILABLE", "message": "ocr backend unavailable"}
    assert payload["debug"] is not None
    assert "ocr_mode_requested" not in payload["debug"]
    assert "ocr_mode_effective" not in payload["debug"]
    assert "ocr_scale_applied" not in payload["debug"]
    assert "ocr_retry_high" not in payload["debug"]
    assert "ocr_retry_reason" not in payload["debug"]
    ocr_event = _find_trace_event(payload["debug"]["trace"], "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=0)
    assert ocr_event["mode_requested"] == "fast"
    assert ocr_event["mode_effective"] == "fast"
    assert ocr_event["scale_applied"] == "1280x720"
    assert ocr_event["retry_high"] == 0
    assert ocr_event["retry_reason"] == "none"


@pytest.mark.slow
def test_runtime_operator_fast_mode_quality_gate_on_dense_notice_fixture():
    metrics = benchmark_ocr_mode_against_native(
        FAST_DENSE_NOTICE_FIXTURE,
        ocr_mode="fast",
        anchors=FAST_DENSE_NOTICE_ANCHORS,
    )

    assert metrics.variant_processed_size == (1280, 720)
    assert metrics.anchor_delta >= -1
    assert metrics.median_center_shift_px <= 2.0
    assert metrics.median_iou >= 0.85


@pytest.mark.slow
def test_runtime_operator_fast_mode_dense_notice_absolute_anchor_hits():
    hits = fixture_anchor_hits(FAST_DENSE_NOTICE_FIXTURE, ocr_mode="fast")

    assert set(FAST_DENSE_NOTICE_ABSOLUTE_ANCHORS).issubset(hits)


@pytest.mark.slow
def test_runtime_operator_fast_mode_quality_gate_on_sparse_login_fixture():
    metrics = benchmark_ocr_mode_against_native(
        FAST_SPARSE_LOGIN_FIXTURE,
        ocr_mode="fast",
        anchors=FAST_SPARSE_LOGIN_ANCHORS,
        box_anchors=FAST_SPARSE_LOGIN_BOX_ANCHORS,
    )

    assert metrics.variant_processed_size == (1280, 720)
    assert metrics.anchor_delta >= -1
    assert metrics.median_center_shift_px <= 2.0
    assert metrics.median_iou >= 0.85


@pytest.mark.slow
def test_runtime_operator_fast_mode_sparse_login_absolute_anchor_hits():
    hits = fixture_anchor_hits(FAST_SPARSE_LOGIN_FIXTURE, ocr_mode="fast")

    assert set(FAST_SPARSE_LOGIN_ABSOLUTE_ANCHORS).issubset(hits)


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


def test_session_service_reuses_latest_non_tainted_matching_session(tmp_path: Path):
    from trail.daemon.session_service import SessionService

    service = SessionService(workspace_root=tmp_path)

    old = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    _rewrite_session_payload(service._store, old.session_id, updated_at="2026-04-19T00:00:00+00:00")

    newest = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    _rewrite_session_payload(service._store, newest.session_id, updated_at="2026-04-19T01:00:00+00:00")

    tainted = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    tainted_path = service._store._path_for(tainted.session_id)
    tainted_payload = json.loads(tainted_path.read_text(encoding="utf-8"))
    tainted_payload.setdefault("scene_state", {}).setdefault("daemon", {})["tainted"] = True
    tainted_payload["updated_at"] = "2026-04-19T02:00:00+00:00"
    tainted_path.write_text(json.dumps(tainted_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    reusable = service.find_reusable_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})

    assert reusable is not None
    assert reusable.session_id == newest.session_id


def test_session_service_does_not_reuse_same_title_with_different_hwnd(tmp_path: Path):
    from trail.daemon.session_service import SessionService

    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    service.save_session(session)

    reusable = service.find_reusable_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 999})

    assert reusable is None


def test_session_service_falls_back_to_created_at_when_updated_at_missing(tmp_path: Path):
    from trail.daemon.session_service import SessionService

    service = SessionService(workspace_root=tmp_path)

    older = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    _rewrite_session_payload(
        service._store,
        older.session_id,
        updated_at=None,
        created_at="2026-04-19T00:00:00+00:00",
    )

    newer = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    _rewrite_session_payload(
        service._store,
        newer.session_id,
        updated_at=None,
        created_at="2026-04-19T01:00:00+00:00",
    )

    reusable = service.find_reusable_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})

    assert reusable is not None
    assert reusable.session_id == newer.session_id


def test_session_service_skips_corrupt_session_files_when_finding_reusable(tmp_path: Path):
    from trail.daemon.session_service import SessionService

    service = SessionService(workspace_root=tmp_path)
    good = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    corrupt_path = service._store._path_for("deadbeefdeadbeefdeadbeefdeadbeef")
    corrupt_path.write_text("{not-json", encoding="utf-8")

    reusable = service.find_reusable_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})

    assert reusable is not None
    assert reusable.session_id == good.session_id


def test_session_service_skips_corrupt_request_records_when_checking_taint(tmp_path: Path):
    from trail.daemon.session_service import SessionService

    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    corrupt_record = service._journal_root / "bad-request.json"
    corrupt_record.write_text("{broken", encoding="utf-8")

    assert service.is_session_tainted(session.session_id) is False


def test_session_service_reconcile_session_skips_corrupt_request_records(tmp_path: Path):
    from trail.daemon.session_service import SessionService

    service = SessionService(workspace_root=tmp_path)
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 123})
    session.scene_state.setdefault("daemon", {})["tainted"] = True
    service.save_session(session)
    corrupt_record = service._journal_root / "bad-request.json"
    corrupt_record.write_text("{broken", encoding="utf-8")

    result = service.reconcile_session(session.session_id)

    assert result == {"session_id": session.session_id, "tainted": False}


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


def test_find_owned_overlay_target_prefers_large_visible_owner_window(monkeypatch):
    import trail.runtime.window as window_module

    windows = {
        654: {"owner": 321, "visible": True, "rect": (12, 22, 110, 82)},
        655: {"owner": 321, "visible": True, "rect": (90, 90, 100, 100)},
        656: {"owner": 0, "visible": True, "rect": (12, 22, 110, 82)},
    }

    fake_win32gui = SimpleNamespace(
        GetWindow=lambda hwnd, flag: windows[hwnd]["owner"],
        IsWindowVisible=lambda hwnd: windows[hwnd]["visible"],
        GetWindowRect=lambda hwnd: windows[hwnd]["rect"],
        EnumWindows=lambda callback, extra: [callback(hwnd, extra) for hwnd in windows],
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)

    overlay = window_module._find_owned_overlay_target(321, window_module.Region(left=10, top=20, width=100, height=60))

    assert overlay == (654, window_module.Region(left=12, top=22, width=98, height=60))


def test_windows_window_controller_capture_uses_owned_overlay_window_when_present(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=321))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=10, top=20, width=100, height=60),
    )
    monkeypatch.setattr(
        window_module,
        "_find_owned_overlay_target",
        lambda hwnd, client_region: (654, window_module.Region(left=12, top=22, width=98, height=60)),
    )
    captured = {}
    monkeypatch.setattr(
        window_module,
        "_capture_with_windows_capture",
        lambda hwnd, region: captured.update({"hwnd": hwnd, "region": region}) or Image.new("RGB", (98, 60), color="white"),
    )

    image_bytes = controller.capture()

    assert captured == {
        "hwnd": 654,
        "region": window_module.Region(left=12, top=22, width=98, height=60),
    }
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_capture_warns_when_source_aspect_ratio_differs(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(
        window_module,
        "_capture_with_windows_capture",
        lambda hwnd, region: Image.new("RGB", (2000, 1000), color="white"),
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

    assert image.size == (1920, 1080)
    assert controller.collect_warnings() == [
        {
            "code": "WINDOW_CAPTURE_ASPECT_RATIO_MISMATCH",
            "message": "captured image aspect ratio differs from 1920x1080; resized to canonical output",
        }
    ]


def test_windows_window_controller_capture_image_preserves_region_size_when_normalize_false(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=321, title="Demo"))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=0, top=0, width=1920, height=1080),
    )
    monkeypatch.setattr(
        window_module,
        "_capture_with_windows_capture",
        lambda hwnd, client_region: Image.new("RGB", (client_region.width, client_region.height), color="white"),
    )

    image = controller.capture_image(from_x=100, from_y=200, to_x=301, to_y=261, normalize=False)

    assert image.size == (201, 61)


def test_windows_window_controller_capture_preserves_region_size_when_region_requested(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=321, title="Demo"))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=0, top=0, width=1920, height=1080),
    )
    monkeypatch.setattr(
        window_module,
        "_capture_with_windows_capture",
        lambda hwnd, client_region: Image.new("RGB", (client_region.width, client_region.height), color="white"),
    )

    image_bytes = controller.capture(from_x=100, from_y=200, to_x=301, to_y=261)
    image = Image.open(BytesIO(image_bytes))

    assert image.size == (201, 61)


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
    from PIL import Image
    from io import BytesIO

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    image_buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(image_buffer, format="PNG")
    monkeypatch.setattr(controller, "capture", lambda **kwargs: image_buffer.getvalue())

    path = controller.capture_to_workspace(request_id="req-123")

    assert path.parent == tmp_path
    assert path.name.startswith("req-123-")
    assert path.suffix == ".jpg"
    with Image.open(path) as image:
        assert image.format == "JPEG"
        assert image.size == (1, 1)


def test_capture_to_workspace_distinguishes_colliding_request_ids_and_avoids_reserved_names(monkeypatch, tmp_path):
    import trail.runtime.window as window_module
    from PIL import Image
    from io import BytesIO

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    image_buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(image_buffer, format="PNG")
    monkeypatch.setattr(controller, "capture", lambda **kwargs: image_buffer.getvalue())

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
    with Image.open(device) as image:
        assert image.format == "JPEG"
        assert image.size == (1, 1)


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

    session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            del kwargs
            self.text_det = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_cls = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_rec = SimpleNamespace(session=SimpleNamespace(session=session))

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec, kwargs
            return (["ok"], None)

    fake_module = SimpleNamespace(RapidOCR=FakeRapidOCR)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_module)

    adapter = operator_module.RapidOcrAdapter()
    result = adapter.run(Image.new("RGB", (20, 20), color="white"))

    assert result.pieces == ["ok"]


def test_rapidocr_adapter_runs_backend_with_explicit_ocr_options(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    calls: list[dict[str, bool]] = []
    session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            del kwargs
            self.text_det = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_cls = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_rec = SimpleNamespace(session=SimpleNamespace(session=session))

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image
            calls.append({"use_det": use_det, "use_cls": use_cls, "use_rec": use_rec, "kwargs": dict(kwargs)})
            return (["ok"], None)

    fake_module = SimpleNamespace(RapidOCR=FakeRapidOCR)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_module)

    adapter = operator_module.RapidOcrAdapter()
    result = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(use_cls=True))

    assert result.pieces == ["ok"]
    assert calls == [{"use_det": True, "use_cls": True, "use_rec": True, "kwargs": {"text_score": 0.5}}]


def test_rapidocr_adapter_passes_text_score_to_backend_call(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    calls: list[dict[str, object]] = []
    session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            del kwargs
            self.text_det = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_cls = SimpleNamespace(infer=SimpleNamespace(session=session))
            self.text_rec = SimpleNamespace(session=SimpleNamespace(session=session))

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image
            calls.append({"use_det": use_det, "use_cls": use_cls, "use_rec": use_rec, "kwargs": dict(kwargs)})
            return (["ok"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))

    adapter = operator_module.RapidOcrAdapter()
    result = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(text_score=0.77))

    assert result.pieces == ["ok"]
    assert calls == [{"use_det": True, "use_cls": False, "use_rec": True, "kwargs": {"text_score": 0.77}}]


@pytest.mark.parametrize(
    ("ocr_config", "expected_code", "expected_message"),
    [
        ("provider", "OCR_INPUT_INVALID", "unsupported ocr provider: gpu"),
        ("lang", "OCR_LANG_UNSUPPORTED", "unsupported ocr lang: en"),
    ],
)
def test_runtime_operator_ocr_rejects_invalid_ocr_request_config_before_engine_run(tmp_path: Path, ocr_config: str, expected_code: str, expected_message: str):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    engine_calls: list[dict[str, object]] = []

    config = OcrRequestConfig(provider="gpu") if ocr_config == "provider" else OcrRequestConfig(lang="en")

    class WindowStub:
        def capture(self, **kwargs):
            del kwargs
            buffer = BytesIO()
            Image.new("RGB", (20, 20), color="white").save(buffer, format="PNG")
            return buffer.getvalue()

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / "runtime-invalid.png"

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image
            engine_calls.append({"ocr": ocr})
            return operator_module.OcrRunResult(pieces=[{"text": "unexpected"}], warnings=[], trace=[])

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda *args, **kwargs: None),
        reference_root=tmp_path,
    )

    with pytest.raises(TrailError) as exc_info:
        runtime.ocr(capture={}, ocr=config)

    assert exc_info.value.code == expected_code
    assert str(exc_info.value) == expected_message
    assert engine_calls == []
    trace = runtime.consume_debug_trace()
    ocr_event = _find_trace_event(trace, "ocr")
    _assert_finalized_trace_event(ocr_event, step="ocr", ok=0)
    assert ocr_event["error_code"] == expected_code
    assert ocr_event["msg"] == expected_message


@pytest.mark.parametrize(
    ("ocr_config", "expected_code", "expected_message"),
    [
        ("provider", "OCR_INPUT_INVALID", "unsupported ocr provider: gpu"),
        ("lang", "OCR_LANG_UNSUPPORTED", "unsupported ocr lang: en"),
    ],
)
def test_runtime_operator_ocr_image_rejects_invalid_ocr_request_config_with_failed_helper_trace(
    tmp_path: Path,
    ocr_config: str,
    expected_code: str,
    expected_message: str,
):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    config = OcrRequestConfig(provider="gpu") if ocr_config == "provider" else OcrRequestConfig(lang="en")

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: None, capture_to_workspace=lambda request_id=None: tmp_path / "ocr-image-invalid.png"),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: (_ for _ in ()).throw(AssertionError("ocr engine should not run"))),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda *args, **kwargs: None),
        reference_root=tmp_path,
    )

    with pytest.raises(TrailError) as exc_info:
        runtime.ocr_image(Image.new("RGB", (20, 20), color="white"), ocr=config)

    assert exc_info.value.code == expected_code
    assert str(exc_info.value) == expected_message
    trace = runtime.consume_debug_trace()
    ocr_event = _find_trace_event(trace, "ocr_image")
    _assert_finalized_trace_event(ocr_event, step="ocr_image", ok=0)
    assert ocr_event["error_code"] == expected_code
    assert ocr_event["msg"] == expected_message


@pytest.mark.parametrize(
    ("ocr_config", "expected_code", "expected_message"),
    [
        ("provider", "OCR_INPUT_INVALID", "unsupported ocr provider: gpu"),
        ("lang", "OCR_LANG_UNSUPPORTED", "unsupported ocr lang: en"),
    ],
)
def test_rapidocr_adapter_rejects_invalid_ocr_request_config_without_backend_init(
    monkeypatch,
    ocr_config: str,
    expected_code: str,
    expected_message: str,
):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            raise AssertionError(f"unexpected RapidOCR init: {kwargs}")

    config = OcrRequestConfig(provider="gpu") if ocr_config == "provider" else OcrRequestConfig(lang="en")

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()

    with pytest.raises(TrailError) as exc_info:
        adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=config)

    assert exc_info.value.code == expected_code
    assert str(exc_info.value) == expected_message


def test_runtime_operator_ocr_uses_explicit_ocr_run_result_without_side_channel(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.output.capture import with_auto_capture
    from trail.runtime.ocr_config import OcrRequestConfig

    class WindowStub:
        def capture(self, **kwargs):
            del kwargs
            buffer = BytesIO()
            Image.new("RGB", (20, 20), color="white").save(buffer, format="PNG")
            return buffer.getvalue()

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / "ocr-run-result.png"

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image
            assert ocr is not None
            return operator_module.OcrRunResult(
                pieces=[{"text": "银狼"}],
                warnings=[{"code": "OCR_CPU", "message": "provider cpu"}],
                trace=[{"step": "ocr_provider", "requested_provider": ocr.provider, "effective_provider": "cpu", "lang": ocr.lang}],
            )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda *args, **kwargs: None),
        reference_root=tmp_path,
    )

    payload = with_auto_capture(
        runtime,
        lambda: {"result": runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="cpu"))},
        verbose=True,
    )

    assert payload["ok"] is True
    assert payload["data"] == {"result": [{"text": "银狼"}]}
    assert payload["warnings"] == [{"code": "OCR_CPU", "message": "provider cpu"}]
    assert any(item.get("step") == "ocr_provider" for item in payload["debug"]["trace"])


@pytest.mark.parametrize(
    ("provider", "available_providers", "expected_kwargs"),
    [
        (
            "auto",
            ["DmlExecutionProvider", "CPUExecutionProvider"],
            {"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True},
        ),
        (
            "auto",
            ["CPUExecutionProvider"],
            {"det_use_dml": False, "cls_use_dml": False, "rec_use_dml": False},
        ),
        (
            "cpu",
            ["DmlExecutionProvider", "CPUExecutionProvider"],
            {"det_use_dml": False, "cls_use_dml": False, "rec_use_dml": False},
        ),
    ],
)
def test_rapidocr_adapter_resolves_requested_provider_into_rapidocr_engine_flags(
    monkeypatch,
    provider: str,
    available_providers: list[str],
    expected_kwargs: dict[str, bool],
):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    ctor_calls: list[dict[str, bool]] = []

    def _providers():
        provider_name = "DmlExecutionProvider" if expected_kwargs["det_use_dml"] else "CPUExecutionProvider"
        session = SimpleNamespace(get_providers=lambda: [provider_name])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            ctor_calls.append(dict(kwargs))
            provider_tree = _providers()
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec, kwargs
            return (["ok"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: list(available_providers)),
    )

    adapter = operator_module.RapidOcrAdapter()
    result = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider=provider))

    assert result.pieces == ["ok"]
    assert ctor_calls == [expected_kwargs]


def test_rapidocr_adapter_dml_provider_hard_fails_when_unavailable(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OCR_PROVIDER_UNAVAILABLE, OcrRequestConfig

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            raise AssertionError(f"unexpected RapidOCR init: {kwargs}")

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()

    with pytest.raises(TrailError) as exc_info:
        adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="dml"))

    assert exc_info.value.code == OCR_PROVIDER_UNAVAILABLE


def test_rapidocr_adapter_singleflights_engine_init_for_same_cache_key(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    build_started = threading.Event()
    release_build = threading.Event()
    start_barrier = threading.Barrier(3)
    ctor_calls: list[dict[str, bool]] = []
    results: list[list[str]] = []
    errors: list[Exception] = []

    def _providers():
        session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            ctor_calls.append(dict(kwargs))
            build_started.set()
            release_build.wait(timeout=2)
            provider_tree = _providers()
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec, kwargs
            return (["ok"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()

    def worker(text_score: float):
        start_barrier.wait()
        try:
            results.append(
                adapter.run(
                    Image.new("RGB", (20, 20), color="white"),
                    ocr=OcrRequestConfig(provider="cpu", text_score=text_score),
                )
            )
        except Exception as exc:  # pragma: no cover - failure path asserted below
            errors.append(exc)

    left = threading.Thread(target=worker, args=(0.5,))
    right = threading.Thread(target=worker, args=(0.9,))
    left.start()
    right.start()
    start_barrier.wait()
    assert build_started.wait(timeout=2)
    threading.Event().wait(0.1)
    release_build.set()
    left.join(timeout=2)
    right.join(timeout=2)

    assert errors == []
    assert [result.pieces for result in results] == [["ok"], ["ok"]]
    assert ctor_calls == [{"det_use_dml": False, "cls_use_dml": False, "rec_use_dml": False}]


def test_rapidocr_adapter_auto_falls_back_to_cached_cpu_wrapper_when_dml_build_fails(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    ctor_calls: list[dict[str, bool]] = []

    def _providers():
        session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            ctor_calls.append(dict(kwargs))
            if kwargs["det_use_dml"]:
                raise RuntimeError("dml build failed")
            provider_tree = _providers()
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec, kwargs
            return (["ok"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()

    first = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="auto"))
    second = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="cpu"))

    assert first.pieces == ["ok"]
    assert second.pieces == ["ok"]
    assert ctor_calls == [
        {"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True},
        {"det_use_dml": False, "cls_use_dml": False, "rec_use_dml": False},
    ]


def test_rapidocr_adapter_auto_negative_caches_dml_build_failure_for_future_requests(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    ctor_calls: list[dict[str, bool]] = []
    run_calls: list[dict[str, object]] = []

    def _providers():
        session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            ctor_calls.append(dict(kwargs))
            if kwargs["det_use_dml"]:
                raise RuntimeError("dml build failed")
            provider_tree = _providers()
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec
            run_calls.append(dict(kwargs))
            return (["cpu"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()

    first = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="auto"))
    second = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="auto"))

    assert first.pieces == ["cpu"]
    assert second.pieces == ["cpu"]
    assert ctor_calls == [
        {"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True},
        {"det_use_dml": False, "cls_use_dml": False, "rec_use_dml": False},
    ]
    assert run_calls == [{"text_score": 0.5}, {"text_score": 0.5}]


def test_rapidocr_adapter_auto_negative_cache_does_not_change_explicit_dml_semantics(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OCR_PROVIDER_UNAVAILABLE, OcrRequestConfig

    ctor_calls: list[dict[str, bool]] = []

    def _providers():
        session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            ctor_calls.append(dict(kwargs))
            if kwargs["det_use_dml"]:
                raise RuntimeError("dml build failed")
            provider_tree = _providers()
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec, kwargs
            return (["cpu"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()

    warm = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="auto"))

    with pytest.raises(TrailError) as exc_info:
        adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="dml"))

    assert warm.pieces == ["cpu"]
    assert exc_info.value.code == OCR_PROVIDER_UNAVAILABLE
    assert ctor_calls == [
        {"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True},
        {"det_use_dml": False, "cls_use_dml": False, "rec_use_dml": False},
        {"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True},
    ]


def test_rapidocr_adapter_auto_evicts_cached_dml_wrapper_and_falls_back_to_cpu_when_run_fails(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OcrRequestConfig

    ctor_calls: list[dict[str, bool]] = []
    run_calls: list[dict[str, object]] = []

    def _providers(provider_name: str):
        session = SimpleNamespace(get_providers=lambda: [provider_name])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            ctor_calls.append(dict(kwargs))
            self.is_dml = bool(kwargs["det_use_dml"])
            self.calls = 0
            provider_tree = _providers("DmlExecutionProvider" if self.is_dml else "CPUExecutionProvider")
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec
            self.calls += 1
            run_calls.append({"dml": self.is_dml, "call": self.calls, "kwargs": dict(kwargs)})
            if self.is_dml and self.calls >= 2:
                raise RuntimeError("cached dml run failed")
            return (["dml" if self.is_dml else "cpu"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()
    dml_key = operator_module.OcrEngineKey(effective_provider="dml", model_identity="rapidocr:ppocrv4-mobile:ch", use_cls=False)
    cpu_key = operator_module.OcrEngineKey(effective_provider="cpu", model_identity="rapidocr:ppocrv4-mobile:ch", use_cls=False)

    warm = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="auto"))
    fallback = adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="auto"))

    assert warm.pieces == ["dml"]
    assert fallback.pieces == ["cpu"]
    assert any(item.get("fallback_from") == "dml" and "cached dml run failed" in item.get("reason", "") for item in fallback.trace)
    assert run_calls == [
        {"dml": True, "call": 1, "kwargs": {"text_score": 0.5}},
        {"dml": True, "call": 2, "kwargs": {"text_score": 0.5}},
        {"dml": False, "call": 1, "kwargs": {"text_score": 0.5}},
    ]
    assert ctor_calls == [
        {"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True},
        {"det_use_dml": False, "cls_use_dml": False, "rec_use_dml": False},
    ]
    assert dml_key not in adapter._engine_cache
    assert cpu_key in adapter._engine_cache


def test_rapidocr_adapter_explicit_dml_run_failure_does_not_fallback_to_cpu(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OCR_PROVIDER_UNAVAILABLE, OcrRequestConfig

    ctor_calls: list[dict[str, bool]] = []
    run_calls: list[dict[str, object]] = []

    def _providers(provider_name: str):
        session = SimpleNamespace(get_providers=lambda: [provider_name])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            ctor_calls.append(dict(kwargs))
            self.is_dml = bool(kwargs["det_use_dml"])
            provider_tree = _providers("DmlExecutionProvider" if self.is_dml else "CPUExecutionProvider")
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec
            run_calls.append({"dml": self.is_dml, "kwargs": dict(kwargs)})
            if self.is_dml:
                raise RuntimeError("explicit dml run failed")
            return (["cpu"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()
    dml_key = operator_module.OcrEngineKey(effective_provider="dml", model_identity="rapidocr:ppocrv4-mobile:ch", use_cls=False)
    cpu_key = operator_module.OcrEngineKey(effective_provider="cpu", model_identity="rapidocr:ppocrv4-mobile:ch", use_cls=False)

    with pytest.raises(TrailError) as exc_info:
        adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="dml"))

    assert exc_info.value.code == OCR_PROVIDER_UNAVAILABLE
    assert run_calls == [{"dml": True, "kwargs": {"text_score": 0.5}}]
    assert ctor_calls == [{"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True}]
    assert dml_key not in adapter._engine_cache
    assert cpu_key not in adapter._engine_cache


def test_rapidocr_adapter_dml_provider_validation_failure_is_not_cached(monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.runtime.ocr_config import OCR_PROVIDER_UNAVAILABLE, OcrRequestConfig

    ctor_calls: list[dict[str, bool]] = []

    def _providers():
        session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            ctor_calls.append(dict(kwargs))
            provider_tree = _providers()
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec, kwargs
            return (["ok"], None)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]),
    )

    adapter = operator_module.RapidOcrAdapter()

    with pytest.raises(TrailError) as first_error:
        adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="dml"))

    with pytest.raises(TrailError) as second_error:
        adapter.run(Image.new("RGB", (20, 20), color="white"), ocr=OcrRequestConfig(provider="dml"))

    assert first_error.value.code == OCR_PROVIDER_UNAVAILABLE
    assert second_error.value.code == OCR_PROVIDER_UNAVAILABLE
    assert ctor_calls == [
        {"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True},
        {"det_use_dml": True, "cls_use_dml": False, "rec_use_dml": True},
    ]


def test_runtime_operator_keeps_ocr_debug_and_warnings_request_local_under_concurrency(tmp_path: Path):
    import trail.runtime.operator as operator_module
    from trail.output.capture import with_auto_capture
    from trail.runtime.ocr_config import OcrRequestConfig

    barrier = threading.Barrier(2)
    payloads: dict[str, dict] = {}

    class WindowStub:
        def capture(self, **kwargs):
            del kwargs
            buffer = BytesIO()
            Image.new("RGB", (20, 20), color="white").save(buffer, format="PNG")
            return buffer.getvalue()

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / f"{threading.current_thread().name}.png"

    class EngineStub:
        def run(self, image, *, ocr=None):
            del image
            assert ocr is not None
            barrier.wait()
            return operator_module.OcrRunResult(
                pieces=[{"text": ocr.provider}],
                warnings=[{"code": f"OCR_{ocr.provider.upper()}", "message": f"provider {ocr.provider}"}],
                trace=[
                    {
                        "step": "ocr_provider",
                        "requested_provider": ocr.provider,
                        "effective_provider": ocr.provider,
                        "lang": ocr.lang,
                    }
                ],
            )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=EngineStub(),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda *args, **kwargs: None,
        ),
        reference_root=tmp_path,
    )

    def worker(name: str, provider: str):
        payloads[name] = with_auto_capture(
            runtime,
            lambda: {"result": runtime.ocr(capture={}, ocr=OcrRequestConfig(provider=provider))},
            verbose=True,
        )

    left = threading.Thread(target=worker, name="left", args=("left", "cpu"))
    right = threading.Thread(target=worker, name="right", args=("right", "auto"))
    left.start()
    right.start()
    left.join(timeout=2)
    right.join(timeout=2)

    left_provider_trace = [item for item in payloads["left"]["debug"]["trace"] if item.get("step") == "ocr_provider"]
    right_provider_trace = [item for item in payloads["right"]["debug"]["trace"] if item.get("step") == "ocr_provider"]

    assert payloads["left"]["warnings"] == [{"code": "OCR_CPU", "message": "provider cpu"}]
    assert payloads["right"]["warnings"] == [{"code": "OCR_AUTO", "message": "provider auto"}]
    assert left_provider_trace == [
        {
            "step": "ocr_provider",
            "requested_provider": "cpu",
            "effective_provider": "cpu",
            "lang": "ch",
        }
    ]
    assert right_provider_trace == [
        {
            "step": "ocr_provider",
            "requested_provider": "auto",
            "effective_provider": "auto",
            "lang": "ch",
        }
    ]


def test_with_auto_capture_preserves_provider_trace_for_dml_failure(tmp_path: Path, monkeypatch):
    import trail.runtime.operator as operator_module
    from trail.output.capture import with_auto_capture
    from trail.runtime.ocr_config import OcrRequestConfig

    def _providers(provider_name: str):
        session = SimpleNamespace(get_providers=lambda: [provider_name])
        return SimpleNamespace(
            text_det=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_cls=SimpleNamespace(infer=SimpleNamespace(session=session)),
            text_rec=SimpleNamespace(session=SimpleNamespace(session=session)),
        )

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            provider_tree = _providers("DmlExecutionProvider")
            self.text_det = provider_tree.text_det
            self.text_cls = provider_tree.text_cls
            self.text_rec = provider_tree.text_rec

        def __call__(self, image, use_det=True, use_cls=False, use_rec=True, **kwargs):
            del image, use_det, use_cls, use_rec, kwargs
            raise RuntimeError("explicit dml run failed")

    class WindowStub:
        def capture(self, **kwargs):
            del kwargs
            buffer = BytesIO()
            Image.new("RGB", (20, 20), color="white").save(buffer, format="PNG")
            return buffer.getvalue()

        def capture_to_workspace(self, request_id: str | None = None):
            del request_id
            return tmp_path / "ocr-dml-fail.png"

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"]),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=operator_module.RapidOcrAdapter(),
        input_driver=SimpleNamespace(click=lambda *args, **kwargs: None, drag=lambda *args, **kwargs: None, press=lambda *args, **kwargs: None),
        reference_root=tmp_path,
    )

    payload = with_auto_capture(
        runtime,
        lambda: {"result": runtime.ocr(capture={}, ocr=OcrRequestConfig(provider="dml"))},
        verbose=True,
    )

    provider_trace = [item for item in (payload.get("debug") or {}).get("trace", []) if item.get("step") == "ocr_provider"]

    assert payload["ok"] is False
    assert payload["error"] == {"code": "OCR_PROVIDER_UNAVAILABLE", "message": "requested dml provider unavailable"}
    assert provider_trace == [
        {
            "step": "ocr_provider",
            "requested_provider": "dml",
            "effective_provider": "dml",
            "lang": "ch",
            "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
            "reason": "RuntimeError: explicit dml run failed",
        }
    ]


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


def test_runtime_operator_locate_failure_trace_keeps_attempts_and_retried() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: (_ for _ in ()).throw(TrailError("SCREENSHOT_FAILED", "无法截取窗口内容")),
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    with pytest.raises(TrailError) as exc_info:
        runtime.locate("entry.start")

    assert exc_info.value.code == "SCREENSHOT_FAILED"
    trace = runtime.consume_debug_trace()
    locate_event = _find_trace_event(trace, "locate")
    _assert_finalized_trace_event(locate_event, step="locate", ok=0)
    assert locate_event["attempts"] == 1
    assert locate_event["retried"] == 0
    assert locate_event["box"] is None


@pytest.mark.parametrize(
    ("runner", "expected_step"),
    [
        (lambda runtime: runtime.click_point(10, 20), "click_point"),
        (lambda runtime: runtime.drag_to(10, 20, 30, 40), "drag_to"),
        (lambda runtime: runtime.press_key("f", presses=1), "press_key"),
        (lambda runtime: runtime.hotkey("ctrl", "l"), "hotkey"),
        (lambda runtime: runtime.type_text("abc"), "type_text"),
    ],
)
def test_runtime_operator_input_helpers_emit_finalized_trace_with_ok_ts_and_duration(runner, expected_step) -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            prepare_input=lambda: None,
            is_foreground=lambda: True,
            to_screen_point=lambda x, y: (x, y),
            capture=lambda **kwargs: b"demo",
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            ensure_available=lambda: None,
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    runner(runtime)

    trace = runtime.consume_debug_trace()
    _assert_finalized_trace_event(_find_trace_event(trace, "prepare_input"), step="prepare_input", ok=1)
    _assert_finalized_trace_event(_find_trace_event(trace, expected_step), step=expected_step, ok=1)


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
        drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(("drag", from_x, from_y, to_x, to_y, duration)),
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
        ("drag", 1, 2, 3, 4, 0.2),
        ("prepare_input",),
        ("press", "shift"),
    ]


def test_runtime_operator_drag_defaults_duration_to_point_two_seconds():
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
        drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(("drag", from_x, from_y, to_x, to_y, duration)),
        press=lambda key: calls.append(("press", key)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.drag_to(1, 2, 3, 4)

    assert calls == [
        ("prepare_input",),
        ("drag", 1, 2, 3, 4, 0.2),
    ]
    trace = runtime.consume_debug_trace()
    prepare_event = trace[0]
    drag_event = trace[1]

    _assert_finalized_trace_event(prepare_event, step="prepare_input", ok=1)
    _assert_finalized_trace_event(drag_event, step="drag_to", ok=1)
    assert drag_event["from_point"] == [1, 2]
    assert drag_event["to_point"] == [3, 4]
    assert drag_event["duration"] == 0.2
    assert drag_event["screen_from"] == [1, 2]
    assert drag_event["screen_to"] == [3, 4]


def test_runtime_operator_drag_keeps_explicit_duration():
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
        drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(("drag", from_x, from_y, to_x, to_y, duration)),
        press=lambda key: calls.append(("press", key)),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=input_driver,
    )

    runtime.drag_to(1, 2, 3, 4, duration=0.35)

    assert calls == [
        ("prepare_input",),
        ("drag", 1, 2, 3, 4, 0.35),
    ]
    trace = runtime.consume_debug_trace()
    prepare_event = trace[0]
    drag_event = trace[1]

    _assert_finalized_trace_event(prepare_event, step="prepare_input", ok=1)
    _assert_finalized_trace_event(drag_event, step="drag_to", ok=1)
    assert drag_event["from_point"] == [1, 2]
    assert drag_event["to_point"] == [3, 4]
    assert drag_event["duration"] == 0.35
    assert drag_event["screen_from"] == [1, 2]
    assert drag_event["screen_to"] == [3, 4]


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
    trace = runtime.consume_debug_trace()
    _assert_finalized_trace_event(trace[0], step="prepare_input", ok=1)
    _assert_finalized_trace_event(trace[1], step="hotkey", ok=1)
    assert trace[1]["keys"] == ["ctrl", "v"]


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
    trace = runtime.consume_debug_trace()
    _assert_finalized_trace_event(trace[0], step="prepare_input", ok=1)
    _assert_finalized_trace_event(trace[1], step="type_text", ok=1)
    assert trace[1]["text"] == "##demo##"


def test_runtime_operator_type_text_on_windows_does_not_require_pyautogui_backend(monkeypatch):
    import trail.runtime.operator as operator_module

    calls: list[tuple[str, object | None]] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input", None))

        def is_foreground(self):
            return True

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            calls.append(("SendInput", count))
            return count

    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("pyautogui should not be loaded before native type_text"))),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=operator_module.PyAutoGuiInputDriver(),
    )

    runtime.type_text("##demo##")

    assert calls == [
        ("prepare_input", None),
        ("SendInput", len("##demo##") * 2),
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
        drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(("drag", from_x, from_y, to_x, to_y, duration)),
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
        ("drag", 101, 202, 130, 240, 0.2),
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
        drag=lambda from_x, from_y, to_x, to_y, duration=None: calls.append(("drag", from_x, from_y, to_x, to_y, duration)),
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
        ("drag", 200, 300, 900, 500, 0.2),
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

    calls: list[tuple[str, tuple[int, ...] | tuple[()]]] = []

    class User32:
        def ShowWindow(self, hwnd: int, command: int):
            calls.append(("ShowWindow", (hwnd, command)))
            return 1

        def GetForegroundWindow(self):
            calls.append(("GetForegroundWindow", ()))
            return 123

        def GetWindowThreadProcessId(self, hwnd: int, process_id):
            del process_id
            calls.append(("GetWindowThreadProcessId", (hwnd,)))
            return {123: 11, 321: 22}[hwnd]

        def AttachThreadInput(self, source: int, target: int, attach: bool):
            calls.append(("AttachThreadInput", (source, target, int(attach))))
            return 1

        def BringWindowToTop(self, hwnd: int):
            calls.append(("BringWindowToTop", (hwnd,)))
            return 1

        def SetForegroundWindow(self, hwnd: int):
            calls.append(("SetForegroundWindow", (hwnd,)))
            return 1

        def keybd_event(self, key_code: int, scan_code: int, flags: int, extra: int):
            calls.append(("keybd_event", (key_code, scan_code, flags, extra)))
            return 1

        def SetFocus(self, hwnd: int):
            calls.append(("SetFocus", (hwnd,)))
            return 1

        def SetActiveWindow(self, hwnd: int):
            calls.append(("SetActiveWindow", (hwnd,)))
            return 1

    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(
        window_module.ctypes,
        "windll",
        SimpleNamespace(user32=User32(), kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 99)),
    )
    fake_window = SimpleNamespace(title="Demo", _hWnd=321, isMinimized=True, isActive=False)
    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: fake_window)

    controller.prepare_input()

    assert calls == [
        ("ShowWindow", (321, 9)),
        ("GetForegroundWindow", ()),
        ("GetWindowThreadProcessId", (123,)),
        ("GetWindowThreadProcessId", (321,)),
        ("AttachThreadInput", (11, 99, 1)),
        ("AttachThreadInput", (22, 99, 1)),
        ("BringWindowToTop", (321,)),
        ("keybd_event", (0x12, 0, 0, 0)),
        ("keybd_event", (0x12, 0, 0x0002, 0)),
        ("SetForegroundWindow", (321,)),
        ("SetFocus", (321,)),
        ("SetActiveWindow", (321,)),
        ("AttachThreadInput", (22, 99, 0)),
        ("AttachThreadInput", (11, 99, 0)),
    ]


def test_windows_window_controller_scales_canonical_points_to_client_region(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(
        controller,
        "client_region",
        lambda: window_module.Region(left=496, top=-1034, width=1536, height=864),
    )

    assert controller.to_screen_point(960, 540) == (1264, -602)
    assert controller.to_screen_point(1920, 1080) == (2031, -171)


def test_windows_window_controller_scales_canonical_capture_region_to_client_region(monkeypatch, tmp_path):
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
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    monkeypatch.setattr(controller, "_resolve_window", lambda: object())
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=256, top=-992, width=1536, height=864),
    )

    image_bytes = controller.capture(from_x=364, from_y=280, to_x=1689, to_y=334)

    assert FakeImageGrab.called_with == ((547, -768, 1607, -725), False)
    assert image_bytes.startswith(b"\x89PNG")


def test_crop_window_image_to_region_uses_window_relative_offsets(monkeypatch):
    import trail.runtime.window as window_module

    monkeypatch.setattr(
        window_module,
        "_resolve_window_region",
        lambda hwnd: window_module.Region(left=100, top=200, width=200, height=100),
    )

    cropped = window_module._crop_window_image_to_region(
        Image.new("RGB", (200, 100), color="white"),
        hwnd=321,
        client_region=window_module.Region(left=150, top=225, width=100, height=50),
    )

    assert cropped.size == (100, 50)


def test_windows_window_controller_scales_canonical_capture_region_before_windows_capture(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    captured: dict[str, object] = {}
    monkeypatch.setattr(window_module.sys, "platform", "win32")
    monkeypatch.setattr(
        window_module,
        "_capture_with_windows_capture",
        lambda hwnd, region: captured.update({"hwnd": hwnd, "region": region}) or Image.new("RGB", (1060, 43), color="white"),
    )

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

    image_bytes = controller.capture(from_x=364, from_y=280, to_x=1689, to_y=334)

    assert captured == {
        "hwnd": 321,
        "region": window_module.Region(left=547, top=-768, width=1060, height=43),
    }
    assert image_bytes.startswith(b"\x89PNG")


def test_windows_window_controller_final_bbox_fallback_scales_region_using_window_dpi(monkeypatch, tmp_path):
    import trail.runtime.window as window_module

    class User32:
        def GetDpiForWindow(self, hwnd: int):
            return 120

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
    monkeypatch.setattr(window_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    monkeypatch.setattr(window_module, "ImageGrab", FakeImageGrab)
    monkeypatch.setattr(window_module, "_capture_with_windows_capture", lambda hwnd, region: (_ for _ in ()).throw(OSError("graphics capture unavailable")))
    monkeypatch.setattr(window_module, "_capture_win32_window", lambda hwnd, region: (_ for _ in ()).throw(TrailError("SCREENSHOT_FAILED", "无法截取窗口内容")))

    controller = window_module.WindowsWindowController(
        workspace=tmp_path,
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    prepare_calls: list[str] = []
    monkeypatch.setattr(controller, "_resolve_window", lambda: SimpleNamespace(_hWnd=321))
    monkeypatch.setattr(controller, "prepare_input", lambda: prepare_calls.append("prepare"))
    monkeypatch.setattr(
        controller,
        "_resolve_region",
        lambda window=None: window_module.Region(left=256, top=-992, width=1536, height=864),
    )

    image_bytes = controller.capture(from_x=364, from_y=280, to_x=1689, to_y=334)

    assert prepare_calls == ["prepare"]
    assert FakeImageGrab.calls == [
        {"bbox": (684, -960, 2009, -906), "all_screens": True, "window": None},
        {"bbox": None, "all_screens": False, "window": 321},
        {"bbox": (684, -960, 2009, -906), "all_screens": True, "window": None},
    ]
    assert image_bytes.startswith(b"\x89PNG")


def test_change_game_config_updates_channel_values(tmp_path):
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")
    config = tmp_path / "config.ini"
    config.write_text("channel=0\nsub_channel=0\n", encoding="utf-8")

    window_module.change_game_config(executable, channel=14, sub_channel=0)

    assert config.read_text(encoding="utf-8") == "channel=14\nsub_channel=0\n"


def test_launch_game_uses_history_path_before_default_for_official(tmp_path, monkeypatch):
    import trail.runtime.launch_paths as launch_paths
    import trail.runtime.window as window_module

    history = tmp_path / "history.exe"
    history.write_text("demo", encoding="utf-8")
    default = tmp_path / "default.exe"
    default.write_text("demo", encoding="utf-8")
    state_file = tmp_path / "game-paths.json"
    config = tmp_path / "config.ini"
    config.write_text("channel=0\nsub_channel=0\n", encoding="utf-8")
    calls: dict[str, object] = {}

    monkeypatch.setattr(launch_paths, "game_paths_path_for_user", lambda: state_file)
    launch_paths.write_launch_path("official", str(history), state_file)
    monkeypatch.setattr(window_module, "DEFAULT_GAME_PATHS", {"official": default})
    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)
    monkeypatch.setattr(
        window_module.subprocess,
        "Popen",
        lambda args, **kwargs: calls.update({"args": args, "kwargs": kwargs}) or SimpleNamespace(),
    )

    result = window_module.launch_game(game_path=None, channel="official")

    assert result["path"] == str(history)
    assert calls == {
        "args": [str(history)],
        "kwargs": {"cwd": str(tmp_path)},
    }


def test_launch_game_falls_back_to_default_when_history_path_missing(tmp_path, monkeypatch):
    import trail.runtime.launch_paths as launch_paths
    import trail.runtime.window as window_module

    default = tmp_path / "default.exe"
    default.write_text("demo", encoding="utf-8")
    state_file = tmp_path / "game-paths.json"
    config = tmp_path / "config.ini"
    config.write_text("channel=0\nsub_channel=0\n", encoding="utf-8")
    calls: dict[str, object] = {}

    monkeypatch.setattr(launch_paths, "game_paths_path_for_user", lambda: state_file)
    launch_paths.write_launch_path("official", str(tmp_path / "missing.exe"), state_file)
    monkeypatch.setattr(window_module, "DEFAULT_GAME_PATHS", {"official": default})
    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)
    monkeypatch.setattr(
        window_module.subprocess,
        "Popen",
        lambda args, **kwargs: calls.update({"args": args, "kwargs": kwargs}) or SimpleNamespace(),
    )

    result = window_module.launch_game(game_path=None, channel="official")

    assert result["path"] == str(default)
    assert calls == {
        "args": [str(default)],
        "kwargs": {"cwd": str(tmp_path)},
    }


def test_launch_game_falls_back_to_default_when_history_launch_fails(tmp_path, monkeypatch):
    import trail.runtime.launch_paths as launch_paths
    import trail.runtime.window as window_module

    history = tmp_path / "history.exe"
    history.write_text("demo", encoding="utf-8")
    default = tmp_path / "default.exe"
    default.write_text("demo", encoding="utf-8")
    state_file = tmp_path / "game-paths.json"
    attempts: list[Path] = []
    calls: dict[str, object] = {}

    monkeypatch.setattr(launch_paths, "game_paths_path_for_user", lambda: state_file)
    launch_paths.write_launch_path("official", str(history), state_file)
    monkeypatch.setattr(window_module, "DEFAULT_GAME_PATHS", {"official": default})
    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)

    def fake_change_game_config(path: Path, *, channel: int, sub_channel: int) -> None:
        del channel, sub_channel
        attempts.append(Path(path))
        if Path(path) == history:
            raise TrailError("GAME_CONFIG_NOT_FOUND", "history launch failed")

    monkeypatch.setattr(window_module, "change_game_config", fake_change_game_config)
    monkeypatch.setattr(
        window_module.subprocess,
        "Popen",
        lambda args, **kwargs: calls.update({"args": args, "kwargs": kwargs}) or SimpleNamespace(),
    )

    result = window_module.launch_game(game_path=None, channel="official")

    assert attempts == [history, default]
    assert result["path"] == str(default)
    assert calls == {
        "args": [str(default)],
        "kwargs": {"cwd": str(tmp_path)},
    }


def test_launch_game_global_without_history_requires_user_path(monkeypatch):
    import trail.runtime.window as window_module

    monkeypatch.setattr(window_module, "read_launch_paths", lambda path=None: {})

    with pytest.raises(TrailError) as exc_info:
        window_module.launch_game(game_path=None, channel="global")

    assert exc_info.value.code == "GAME_PATH_REQUIRED"
    assert "请提供游戏路径" in str(exc_info.value)


def test_launch_game_bilibili_without_history_requires_user_path(monkeypatch):
    import trail.runtime.window as window_module

    monkeypatch.setattr(window_module, "read_launch_paths", lambda path=None: {})

    with pytest.raises(TrailError) as exc_info:
        window_module.launch_game(game_path=None, channel="bilibili")

    assert exc_info.value.code == "GAME_PATH_REQUIRED"
    assert "请提供游戏路径" in str(exc_info.value)


def test_launch_game_explicit_missing_path_is_hard_error_without_fallback(tmp_path, monkeypatch):
    import trail.runtime.launch_paths as launch_paths
    import trail.runtime.window as window_module

    history = tmp_path / "history.exe"
    history.write_text("demo", encoding="utf-8")
    default = tmp_path / "default.exe"
    default.write_text("demo", encoding="utf-8")
    state_file = tmp_path / "game-paths.json"

    monkeypatch.setattr(launch_paths, "game_paths_path_for_user", lambda: state_file)
    launch_paths.write_launch_path("official", str(history), state_file)
    monkeypatch.setattr(window_module, "DEFAULT_GAME_PATHS", {"official": default})
    monkeypatch.setattr(window_module, "change_game_config", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback used")))
    monkeypatch.setattr(
        window_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback used")),
    )

    with pytest.raises(TrailError) as exc_info:
        window_module.launch_game(game_path=tmp_path / "missing.exe", channel="official")

    assert exc_info.value.code == "GAME_PATH_NOT_FOUND"
    assert "未找到游戏启动路径" in str(exc_info.value)


@pytest.mark.parametrize(("failure_stage"), ["config", "popen"])
def test_launch_game_explicit_existing_path_launch_failure_raises_stable_error_without_fallback(
    tmp_path, monkeypatch, failure_stage: str
):
    import trail.runtime.launch_paths as launch_paths
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")
    history = tmp_path / "history.exe"
    history.write_text("demo", encoding="utf-8")
    default = tmp_path / "default.exe"
    default.write_text("demo", encoding="utf-8")
    state_file = tmp_path / "game-paths.json"
    config_attempts: list[Path] = []
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(launch_paths, "game_paths_path_for_user", lambda: state_file)
    launch_paths.write_launch_path("official", str(history), state_file)
    monkeypatch.setattr(window_module, "DEFAULT_GAME_PATHS", {"official": default})
    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)

    def fake_change_game_config(path: Path, *, channel: int, sub_channel: int) -> None:
        del channel, sub_channel
        config_attempts.append(Path(path))
        if Path(path) != executable:
            raise AssertionError("fallback used")
        if failure_stage == "config":
            raise TrailError("GAME_CONFIG_NOT_FOUND", "config explode")

    def fake_popen(args, **kwargs):
        popen_calls.append((list(args), dict(kwargs)))
        if failure_stage == "popen" and list(args) == [str(executable)]:
            raise OSError("launch explode")
        raise AssertionError("fallback used")

    monkeypatch.setattr(window_module, "change_game_config", fake_change_game_config)
    monkeypatch.setattr(window_module.subprocess, "Popen", fake_popen)

    with pytest.raises(TrailError) as exc_info:
        window_module.launch_game(game_path=executable, channel="official")

    assert exc_info.value.code == "GAME_LAUNCH_FAILED"
    assert str(executable) in str(exc_info.value)
    if failure_stage == "config":
        assert "config explode" in str(exc_info.value)
        assert config_attempts == [executable]
        assert popen_calls == []
    else:
        assert "launch explode" in str(exc_info.value)
        assert config_attempts == [executable]
        assert popen_calls == [([str(executable)], {"cwd": str(tmp_path)})]


@pytest.mark.parametrize(("failure_stage"), ["config", "popen"])
def test_launch_game_explicit_existing_path_launch_failure_does_not_write_history(tmp_path, monkeypatch, failure_stage: str):
    import trail.runtime.launch_paths as launch_paths
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")
    history = tmp_path / "history.exe"
    history.write_text("demo", encoding="utf-8")
    state_file = tmp_path / "game-paths.json"
    write_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(launch_paths, "game_paths_path_for_user", lambda: state_file)
    launch_paths.write_launch_path("official", str(history), state_file)
    original_history_text = state_file.read_text(encoding="utf-8")
    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)

    def fake_change_game_config(path: Path, *, channel: int, sub_channel: int) -> None:
        del channel, sub_channel
        if Path(path) != executable:
            raise AssertionError("unexpected fallback path")
        if failure_stage == "config":
            raise TrailError("GAME_CONFIG_NOT_FOUND", "config explode")

    def fake_popen(args, **kwargs):
        if list(args) != [str(executable)]:
            raise AssertionError("unexpected fallback path")
        if failure_stage == "popen":
            raise OSError("launch explode")
        raise AssertionError("popen should not be called when config fails")

    monkeypatch.setattr(window_module, "change_game_config", fake_change_game_config)
    monkeypatch.setattr(window_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(window_module, "write_launch_path", lambda *args, **kwargs: write_calls.append((args, kwargs)))

    with pytest.raises(TrailError) as exc_info:
        window_module.launch_game(game_path=executable, channel="official")

    assert exc_info.value.code == "GAME_LAUNCH_FAILED"
    if failure_stage == "config":
        assert "config explode" in str(exc_info.value)
    else:
        assert "launch explode" in str(exc_info.value)

    assert write_calls == []
    assert state_file.read_text(encoding="utf-8") == original_history_text
    assert launch_paths.read_launch_paths(state_file) == {
        "official": {"last_success_game_path": str(history)}
    }


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


def test_launch_game_writes_history_only_after_real_start(tmp_path, monkeypatch):
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")
    state_file = tmp_path / "game-paths.json"
    events: list[tuple[object, ...]] = []

    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)
    monkeypatch.setattr(
        window_module,
        "change_game_config",
        lambda path, *, channel, sub_channel: events.append(("config", Path(path), channel, sub_channel)),
    )
    monkeypatch.setattr(
        window_module.subprocess,
        "Popen",
        lambda args, cwd=None: events.append(("popen", list(args), cwd)) or SimpleNamespace(),
    )

    def fake_write_launch_path(channel: str, game_path: str, path=None) -> None:
        del path
        events.append(("write", channel, game_path))
        state_file.write_text(game_path, encoding="utf-8")

    monkeypatch.setattr(window_module, "write_launch_path", fake_write_launch_path, raising=False)

    payload = window_module.launch_game(game_path=executable, channel="official")

    assert payload["started"] is True
    assert state_file.read_text(encoding="utf-8") == str(executable)
    assert events == [
        ("config", executable, 1, 1),
        ("popen", [str(executable)], str(tmp_path)),
        ("write", "official", str(executable)),
    ]


def test_launch_game_does_not_write_history_when_already_running(tmp_path, monkeypatch):
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")
    writes: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: True)
    monkeypatch.setattr(
        window_module,
        "change_game_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("config should not change")),
    )
    monkeypatch.setattr(
        window_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("process should not start")),
    )
    monkeypatch.setattr(window_module, "write_launch_path", lambda *args, **kwargs: writes.append((args, kwargs)), raising=False)

    payload = window_module.launch_game(game_path=executable, channel="official")

    assert payload["started"] is False
    assert payload["already_running"] is True
    assert writes == []


def test_launch_game_returns_warning_when_history_persist_fails_after_start(tmp_path, monkeypatch):
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")

    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)
    monkeypatch.setattr(window_module, "change_game_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(window_module.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        window_module,
        "write_launch_path",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        raising=False,
    )

    payload = window_module.launch_game(game_path=executable, channel="official")

    assert payload["started"] is True
    assert payload["already_running"] is False
    assert payload["path"] == str(executable)
    assert payload["warnings"] == [
        {
            "code": "GAME_PATH_PERSIST_FAILED",
            "message": "游戏已成功启动，但历史路径持久化失败: disk full",
        }
    ]


def test_launch_game_explicit_success_writes_only_to_isolated_user_state_file(
    tmp_path,
    monkeypatch,
    isolated_user_launch_paths,
):
    import trail.runtime.launch_paths as launch_paths
    import trail.runtime.window as window_module

    executable = tmp_path / "StarRail.exe"
    executable.write_text("demo", encoding="utf-8")

    monkeypatch.setattr(window_module, "is_process_running", lambda process_name: False)
    monkeypatch.setattr(window_module, "change_game_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(window_module.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace())

    payload = window_module.launch_game(game_path=executable, channel="bilibili")

    assert payload["started"] is True
    assert isolated_user_launch_paths.exists()
    assert launch_paths.read_launch_paths(isolated_user_launch_paths) == {
        "bilibili": {"last_success_game_path": str(executable)}
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


def test_runtime_operator_rejects_input_when_window_not_foreground_before_input():
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

    with pytest.raises(TrailError) as exc_info:
        runtime.click_point(10, 20)

    assert exc_info.value.code == "WINDOW_NOT_FOREGROUND"
    assert str(exc_info.value) == "窗口不在前台，无法执行输入"
    assert clicks == []
    assert runtime.collect_warnings() == []


def test_runtime_operator_constructs_debug_trace_recorder() -> None:
    import trail.runtime.operator as operator_module
    from trail.runtime.debug_recorder import DebugTraceRecorder

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    assert isinstance(runtime._debug_recorder, DebugTraceRecorder)


def test_runtime_operator_recorder_failure_does_not_override_original_error() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            prepare_input=lambda: None,
            is_foreground=lambda: False,
            to_screen_point=lambda x, y: (x, y),
            capture=lambda **kwargs: b"demo",
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            ensure_available=lambda: None,
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )
    runtime._debug_recorder.append_trace = lambda payload: (_ for _ in ()).throw(RuntimeError("recorder boom"))

    with pytest.raises(operator_module.TrailError) as exc_info:
        runtime.click_point(10, 20)

    assert exc_info.value.code == "WINDOW_NOT_FOREGROUND"
    assert str(exc_info.value) == "窗口不在前台，无法执行输入"


def test_runtime_operator_begin_scope_failure_drops_request_debug_without_polluting_next_request(monkeypatch) -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )
    original_begin_scope = runtime._debug_recorder.begin_scope
    state = {"calls": 0}

    def flaky_begin_scope() -> None:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("begin boom")
        original_begin_scope()

    monkeypatch.setattr(runtime._debug_recorder, "begin_scope", flaky_begin_scope)

    runtime.begin_capture_scope()
    runtime._record_trace("failed_begin")
    runtime._set_debug_context(source="failed_begin")
    assert runtime.consume_debug_trace() == []
    assert runtime.consume_debug_context() == {}
    runtime.end_capture_scope()

    runtime.begin_capture_scope()
    assert runtime.consume_debug_trace() == []
    assert runtime.consume_debug_context() == {}
    runtime.end_capture_scope()


def test_runtime_operator_end_scope_failure_does_not_pollute_next_request_when_debug_not_consumed(monkeypatch) -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )
    original_end_scope = runtime._debug_recorder.end_scope
    state = {"calls": 0}

    def flaky_end_scope() -> None:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("end boom")
        original_end_scope()

    monkeypatch.setattr(runtime._debug_recorder, "end_scope", flaky_end_scope)

    runtime.begin_capture_scope()
    runtime._record_trace("stale")
    runtime._set_debug_context(source="stale")
    runtime.end_capture_scope()

    runtime.begin_capture_scope()
    assert runtime.consume_debug_trace() == []
    assert runtime.consume_debug_context() == {}
    runtime.end_capture_scope()


def test_runtime_operator_drops_unconsumed_debug_when_request_scope_ends() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    runtime.begin_capture_scope()
    runtime._record_trace("stale")
    runtime._set_debug_context(stale=1)
    runtime.end_capture_scope()

    assert runtime.consume_debug_trace() == []
    assert runtime.consume_debug_context() == {}

    runtime.begin_capture_scope()
    runtime._record_trace("fresh")
    runtime._set_debug_context(fresh=1)
    trace = runtime.consume_debug_trace()
    context = runtime.consume_debug_context()
    runtime.end_capture_scope()

    assert trace == [{"step": "fresh"}]
    assert context == {"fresh": 1}
    assert runtime.consume_debug_trace() == []
    assert runtime.consume_debug_context() == {}


def test_runtime_operator_consume_then_write_trace_does_not_leave_late_debug_outside_scope() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    runtime.begin_capture_scope()
    runtime._record_trace("early")
    assert runtime.consume_debug_trace() == [{"step": "early"}]
    runtime._record_trace("late")
    runtime.end_capture_scope()

    assert runtime.consume_debug_trace() == []

    runtime.begin_capture_scope()
    runtime._record_trace("fresh")
    assert runtime.consume_debug_trace() == [{"step": "fresh"}]
    runtime.end_capture_scope()
    assert runtime.consume_debug_trace() == []


def test_runtime_operator_consume_then_write_context_does_not_leave_late_debug_outside_scope() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    runtime.begin_capture_scope()
    runtime._set_debug_context(early=1)
    assert runtime.consume_debug_context() == {"early": 1}
    runtime._set_debug_context(late=1)
    runtime.end_capture_scope()

    assert runtime.consume_debug_context() == {}

    runtime.begin_capture_scope()
    runtime._set_debug_context(fresh=1)
    assert runtime.consume_debug_context() == {"fresh": 1}
    runtime.end_capture_scope()
    assert runtime.consume_debug_context() == {}


def test_runtime_operator_uses_debug_recorder_for_scope_trace_and_context_wiring() -> None:
    import trail.runtime.operator as operator_module

    calls: list[tuple[str, object | None]] = []

    class SpyRecorder:
        def begin_scope(self) -> None:
            calls.append(("begin_scope", None))

        def end_scope(self) -> None:
            calls.append(("end_scope", None))

        def append_trace(self, payload: dict[str, object]) -> None:
            calls.append(("append_trace", dict(payload)))

        def set_context(self, **payload: object) -> None:
            calls.append(("set_context", dict(payload)))

        def consume_trace(self) -> list[dict[str, object]]:
            calls.append(("consume_trace", None))
            return [{"step": "from_spy"}]

        def consume_context(self) -> dict[str, object]:
            calls.append(("consume_context", None))
            return {"source": "spy"}

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(capture=lambda **kwargs: b"demo", capture_to_workspace=lambda request_id=None: Path("shot.png")),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )
    runtime._debug_recorder = SpyRecorder()
    runtime._trace = [{"step": "legacy_global"}]
    runtime._debug_context = {"source": "legacy_global"}

    runtime.begin_capture_scope()
    runtime._request_local.trace = [{"step": "legacy_request"}]
    runtime._request_local.debug_context = {"source": "legacy_request"}
    runtime._record_trace("prepare_input", source="runtime")
    runtime._set_debug_context(mode_requested="fast")
    trace = runtime.consume_debug_trace()
    context = runtime.consume_debug_context()
    runtime.end_capture_scope()

    assert calls == [
        ("begin_scope", None),
        ("append_trace", {"step": "prepare_input", "source": "runtime"}),
        ("set_context", {"mode_requested": "fast"}),
        ("consume_trace", None),
        ("consume_context", None),
        ("end_scope", None),
    ]
    assert trace == [{"step": "from_spy"}]
    assert context == {"source": "spy"}


def test_runtime_operator_warns_when_window_leaves_foreground_after_input():
    import trail.runtime.operator as operator_module

    class WindowStub:
        def __init__(self):
            self.prepare_calls = 0
            self.foreground_checks = 0

        def capture(self, **kwargs):
            del kwargs
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            self.prepare_calls += 1

        def is_foreground(self):
            self.foreground_checks += 1
            return self.foreground_checks == 1

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
    trace = runtime.consume_debug_trace()
    _assert_finalized_trace_event(_find_trace_event(trace, "foreground_check"), step="foreground_check", ok=0)
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
    [event] = runtime.consume_debug_trace()
    _assert_finalized_trace_event(event, step="capture_after_action", ok=1)
    assert event["optional"] == 0
    assert Path(event["screenshot"]) == Path(".trail/shots/req-operator.png")


def test_runtime_operator_capture_after_action_optional_failure_keeps_failed_trace() -> None:
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: b"demo",
            capture_to_workspace=lambda request_id=None: (_ for _ in ()).throw(RuntimeError("capture failed")),
        ),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image, ocr=None: operator_module.OcrRunResult(pieces=[], warnings=[], trace=[])),
        input_driver=SimpleNamespace(
            click=lambda *args, **kwargs: None,
            drag=lambda *args, **kwargs: None,
            press=lambda key: None,
            hotkey=lambda *keys: None,
            type_text=lambda text: None,
        ),
    )

    assert runtime.capture_after_action(optional=True) is None
    [event] = runtime.consume_debug_trace()
    _assert_finalized_trace_event(event, step="capture_after_action", ok=0)
    assert event["optional"] == 1
    assert event["error_type"] == "RuntimeError"
    assert event["msg"] == "capture failed"


def test_runtime_operator_capture_after_action_waits_after_recent_input(monkeypatch, tmp_path):
    import trail.runtime.operator as operator_module

    sleep_calls: list[float] = []
    monotonic_values = iter([100.0, 100.25])

    monkeypatch.setattr(operator_module, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(operator_module, "sleep", lambda seconds: sleep_calls.append(seconds))

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            prepare_input=lambda: None,
            is_foreground=lambda: True,
            to_screen_point=lambda x, y: (x, y),
            capture_to_workspace=lambda request_id=None: tmp_path / f"{request_id or 'shot'}.png",
        ),
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

    runtime.click_point(10, 20)
    path = runtime.capture_after_action(request_id="req-delay")

    assert path == tmp_path / "req-delay.png"
    assert sleep_calls == [pytest.approx(0.75, rel=0.001)]
    trace = runtime.consume_debug_trace()
    delay_event = _find_trace_event(trace, "capture_settle_delay")
    _assert_finalized_trace_event(delay_event, step="capture_settle_delay", ok=1)
    assert delay_event["seconds"] == pytest.approx(0.75, rel=0.001)


def test_runtime_operator_collect_warnings_includes_window_warnings():
    import trail.runtime.operator as operator_module

    runtime = operator_module.RuntimeOperator(
        window=SimpleNamespace(
            capture=lambda **kwargs: b"demo",
            capture_to_workspace=lambda request_id=None: Path("shot.png"),
            collect_warnings=lambda: [
                {
                    "code": "WINDOW_CAPTURE_ASPECT_RATIO_MISMATCH",
                    "message": "captured image aspect ratio differs from 1920x1080; resized to canonical output",
                }
            ],
        ),
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

    assert runtime.collect_warnings() == [
        {
            "code": "WINDOW_CAPTURE_ASPECT_RATIO_MISMATCH",
            "message": "captured image aspect ratio differs from 1920x1080; resized to canonical output",
        }
    ]


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
    from PIL import Image
    from io import BytesIO

    controller = window_module.WindowsWindowController(
        workspace=tmp_path / ".trail" / "shots",
        window_binding=WindowBinding(title="Demo", hwnd=321),
    )
    image_buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(image_buffer, format="PNG")
    monkeypatch.setattr(controller, "capture", lambda **kwargs: image_buffer.getvalue())
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
    with Image.open(screenshot_path) as image:
        assert image.format == "JPEG"
        assert image.size == (1, 1)


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


def test_pyautogui_input_driver_type_text_uses_sendinput_unicode_packets_on_windows(monkeypatch):
    import trail.runtime.operator as operator_module

    packets: list[tuple[int, int, int]] = []
    backend_calls: list[tuple[str, float]] = []

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            for index in range(count):
                record = inputs[index]
                packets.append((record.ki.wVk, record.ki.wScan, record.ki.dwFlags))
            return count

    user32 = User32()
    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=user32))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: SimpleNamespace(write=lambda text, interval=0: backend_calls.append((text, interval)))),
    )

    driver = operator_module.PyAutoGuiInputDriver()
    driver.type_text("##")

    assert backend_calls == []
    assert packets == [
        (0, ord("#"), 0x0004),
        (0, ord("#"), 0x0004 | operator_module.PyAutoGuiInputDriver.KEYEVENTF_KEYUP),
        (0, ord("#"), 0x0004),
        (0, ord("#"), 0x0004 | operator_module.PyAutoGuiInputDriver.KEYEVENTF_KEYUP),
    ]


def test_pyautogui_input_driver_type_text_uses_utf16_code_units_for_non_bmp(monkeypatch):
    import trail.runtime.operator as operator_module

    packets: list[tuple[int, int]] = []

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            for index in range(count):
                record = inputs[index]
                packets.append((record.ki.wScan, record.ki.dwFlags))
            return count

    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=User32()))

    driver = operator_module.PyAutoGuiInputDriver()
    driver.type_text("😀")

    assert packets == [
        (0xD83D, operator_module.PyAutoGuiInputDriver.KEYEVENTF_UNICODE),
        (
            0xD83D,
            operator_module.PyAutoGuiInputDriver.KEYEVENTF_UNICODE | operator_module.PyAutoGuiInputDriver.KEYEVENTF_KEYUP,
        ),
        (0xDE00, operator_module.PyAutoGuiInputDriver.KEYEVENTF_UNICODE),
        (
            0xDE00,
            operator_module.PyAutoGuiInputDriver.KEYEVENTF_UNICODE | operator_module.PyAutoGuiInputDriver.KEYEVENTF_KEYUP,
        ),
    ]


def test_pyautogui_input_driver_type_text_raises_without_fallback_when_sendinput_short_writes(monkeypatch):
    import trail.runtime.operator as operator_module

    backend_calls: list[tuple[str, float]] = []

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            return count - 1

    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: SimpleNamespace(write=lambda text, interval=0: backend_calls.append((text, interval)))),
    )

    driver = operator_module.PyAutoGuiInputDriver()

    with pytest.raises(TrailError) as exc_info:
        driver.type_text("##demo##")

    assert exc_info.value.code == "INPUT_BACKEND_UNAVAILABLE"
    assert backend_calls == []


def test_pyautogui_input_driver_type_text_uses_backend_write_off_windows(monkeypatch):
    import trail.runtime.operator as operator_module

    backend_calls: list[tuple[str, float]] = []

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            raise AssertionError("SendInput should not be used off Windows")

    monkeypatch.setattr(operator_module.sys, "platform", "linux")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: SimpleNamespace(write=lambda text, interval=0: backend_calls.append((text, interval)))),
    )

    driver = operator_module.PyAutoGuiInputDriver()
    driver.type_text("##demo##")

    assert backend_calls == [("##demo##", 0)]


def test_pyautogui_input_driver_input_struct_matches_full_win32_layout():
    import ctypes
    from ctypes import wintypes

    import trail.runtime.operator as operator_module

    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == ctypes.sizeof(ctypes.c_ulonglong) else ctypes.c_ulong

    class MirrorMouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class MirrorKeybdInput(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class MirrorHardwareInput(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class MirrorInputUnion(ctypes.Union):
        _fields_ = [
            ("mi", MirrorMouseInput),
            ("ki", MirrorKeybdInput),
            ("hi", MirrorHardwareInput),
        ]

    class MirrorInput(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("value", MirrorInputUnion),
        ]

    assert ctypes.sizeof(operator_module.INPUT_UNION) == ctypes.sizeof(MirrorInputUnion)
    assert ctypes.sizeof(operator_module.INPUT) == ctypes.sizeof(MirrorInput)


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


def test_pyautogui_input_driver_drag_respects_explicit_duration_on_windows(monkeypatch):
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

    sleep_calls: list[float] = []
    user32 = User32()
    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=user32))
    monkeypatch.setattr(operator_module, "sleep", lambda seconds: sleep_calls.append(seconds))

    driver = operator_module.PyAutoGuiInputDriver()
    driver.drag(960, 1014, 1000, 1020, duration=0.2)

    cursor_positions = [args for name, args in user32.calls if name == "SetCursorPos"]
    assert cursor_positions[0] == (960, 1014)
    assert cursor_positions[-1] == (1000, 1020)
    assert len(cursor_positions) > 2
    assert sum(sleep_calls) == pytest.approx(0.2)


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


def test_pyautogui_input_driver_press_supports_function_keys_on_windows(monkeypatch):
    import trail.runtime.operator as operator_module

    class User32:
        def __init__(self):
            self.calls: list[tuple[str, tuple[int, int, int, int]]] = []

        def keybd_event(self, key_code: int, scan_code: int, flags: int, extra: int):
            self.calls.append(("keybd_event", (key_code, scan_code, flags, extra)))
            return 1

    user32 = User32()
    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=user32))

    driver = operator_module.PyAutoGuiInputDriver()
    driver.press("f4")

    assert user32.calls == [
        ("keybd_event", (0x73, 0, 0, 0)),
        ("keybd_event", (0x73, 0, 0x0002, 0)),
    ]
