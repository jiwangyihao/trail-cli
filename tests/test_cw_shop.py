from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trail.core.errors import TrailError
from trail.output.rendering import render_output
from trail.session.store import SessionStore


IMAGE_BACKED_SHOT_ROOT = Path(r"C:\Users\34404\source\repos\trail-cli\.trail\shots")
IMAGE_BACKED_OPEN_SHOP_SHOT = IMAGE_BACKED_SHOT_ROOT / "2033ad2b66ff4947b0b6644f94f3a6c1-f6e3c2d5f4.jpg"
IMAGE_BACKED_CLOSED_SHOP_SHOT = IMAGE_BACKED_SHOT_ROOT / "3e2fae9b5be74f99bbfce0953d9d1ec7-7ed1d69ed6.jpg"


def fake_shop_snapshot():
    return ([{"name": "银狼", "price": 20}], 40, 7, False, 8)


def fake_shop_snapshot_after_purchase():
    return ([{"name": "阮·梅", "price": 30}], 22, 8, False, 8)


def fake_shop_snapshot_target_unchanged_other_changed():
    return ([{"name": "银狼", "price": 20}, {"name": "阮·梅", "price": 30}], 22, 8, False, 8)


def load_cw_shop_module():
    try:
        return importlib.import_module("trail.scenes.cw.shop")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.shop: {exc}")


def _rapidocr_piece(text: str, score: float = 0.99, *, bbox=None):
    return [([[0, 0], [1, 0], [1, 1], [0, 1]] if bbox is None else bbox), text, score]


def _build_cw_shop_scan_runtime(
    shop_module,
    *,
    click_error: Exception | None = None,
    click_error_on_call: int | None = None,
    click_records_before_error: bool = True,
    screenshot_path: str | None = None,
    warnings: list[dict] | None = None,
    references: list[dict] | None = None,
    collect_warnings_error: Exception | None = None,
):
    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, int]] = []
            self.capture_calls: list[dict[str, object]] = []
            self.reference_calls: list[tuple[str, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            next_click_index = len(self.clicks) + 1
            if click_error is not None and click_error_on_call == next_click_index:
                if click_records_before_error:
                    self.clicks.append((x, y))
                raise click_error
            self.clicks.append((x, y))

        def ocr(self, *, capture):
            self.ocr_calls.append(capture)
            if capture == shop_module.SHOP_TEAM_SIZE_REGION:
                return [_rapidocr_piece("3/3")]
            if capture == shop_module.SHOP_SCAN_REGION:
                return [_rapidocr_piece("黑塔"), _rapidocr_piece("1")]
            if capture == shop_module.SHOP_COINS_REGION:
                return [_rapidocr_piece("62")]
            if capture == shop_module.SHOP_LEVEL_REGION:
                return [_rapidocr_piece("LV.3")]
            if capture == shop_module.SHOP_MAX_TEAM_SIZE_REGION:
                return [_rapidocr_piece("8")]
            raise AssertionError(f"unexpected capture: {capture}")

        def capture_after_action(self, optional: bool = False, request_id: str | None = None):
            self.capture_calls.append({"optional": optional, "request_id": request_id})
            return screenshot_path

        def collect_warnings(self):
            if collect_warnings_error is not None:
                raise collect_warnings_error
            return deepcopy(warnings or [])

        def match_references(self, screenshot_path_value, limit: int = 3):
            self.reference_calls.append((str(screenshot_path_value), limit))
            return deepcopy(references or [])

    return Runtime()


def _require_image_backed_shop_shot(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"image-backed cw shop shot missing: {path}")
    return path


def _load_image_backed_rapidocr_runtime(*, image_path: Path, shop_module):
    try:
        from PIL import Image
        from trail.runtime.operator import RapidOcrAdapter
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"image-backed cw shop OCR dependencies unavailable: {exc}")

    try:
        adapter = RapidOcrAdapter()
        image = Image.open(image_path).convert("RGB")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"image-backed cw shop OCR setup unavailable: {exc}")

    width, height = image.size
    scale_x = width / shop_module.CW_WIDTH
    scale_y = height / shop_module.CW_HEIGHT

    class Runtime:
        def ocr(self, *, capture):
            region = {
                "from_x": int(round(capture["from_x"] * scale_x)),
                "from_y": int(round(capture["from_y"] * scale_y)),
                "to_x": int(round(capture["to_x"] * scale_x)),
                "to_y": int(round(capture["to_y"] * scale_y)),
            }
            cropped = image.crop((region["from_x"], region["from_y"], region["to_x"], region["to_y"]))
            try:
                return adapter.run(cropped).pieces
            except TrailError as exc:
                if exc.code == "OCR_BACKEND_UNAVAILABLE":
                    pytest.skip(f"image-backed cw shop OCR backend unavailable: {exc}")
                raise

    return Runtime()


def test_parse_shop_items_uses_rapidocr_tuple_text_instead_of_confidence():
    shop_module = load_cw_shop_module()
    parse_shop_items = getattr(shop_module, "_parse_shop_items", None)
    assert parse_shop_items is not None

    items, reserve_full = parse_shop_items(
        [
            _rapidocr_piece("黑塔", 0.9996806085109711),
            _rapidocr_piece("1", 0.9982701539993286),
            _rapidocr_piece("阿格莱雅", 0.9880169034004211),
            _rapidocr_piece("1", 0.9931700229644775),
        ]
    )

    assert items == [{"name": "黑塔", "price": 1}, {"name": "阿格莱雅", "price": 1}]
    assert reserve_full is False


def test_build_cw_shop_scanner_reads_rapidocr_tuple_text_fields():
    shop_module = load_cw_shop_module()
    build_cw_shop_scanner = getattr(shop_module, "build_cw_shop_scanner", None)
    assert build_cw_shop_scanner is not None

    expected_level_region = {
        "from_x": 224,
        "from_y": 880,
        "to_x": 350,
        "to_y": 938,
    }
    assert shop_module.SHOP_LEVEL_REGION == expected_level_region

    captures = [
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        expected_level_region,
        shop_module.SHOP_MAX_TEAM_SIZE_REGION,
    ]
    payloads = [
        [
            _rapidocr_piece("黑塔", 0.9996806085109711),
            _rapidocr_piece("1", 0.9982701539993286),
            _rapidocr_piece("阿格莱雅", 0.9880169034004211),
            _rapidocr_piece("1", 0.9931700229644775),
        ],
        [_rapidocr_piece("62", 0.998634397983551)],
        [
            _rapidocr_piece("购买经验", 0.998622477054596),
            _rapidocr_piece("LV.", 0.9367167353630066),
            _rapidocr_piece("3", 0.9983481764793396),
            _rapidocr_piece("0/4", 0.9960913062095642),
        ],
        [_rapidocr_piece("8", 0.99673992395401)],
    ]

    class Runtime:
        def __init__(self):
            self.calls: list[dict[str, int]] = []

        def ocr(self, *, capture):
            self.calls.append(capture)
            index = len(self.calls) - 1
            assert capture == captures[index]
            return payloads[index]

    runtime = Runtime()
    scanner = build_cw_shop_scanner(runtime)

    assert scanner() == (
        [{"name": "黑塔", "price": 1}, {"name": "阿格莱雅", "price": 1}],
        62,
        3,
        False,
        8,
    )
    assert runtime.calls == captures


def test_build_cw_shop_scanner_reads_image_backed_shop_page_when_fixture_available():
    shop_module = load_cw_shop_module()
    build_cw_shop_scanner = getattr(shop_module, "build_cw_shop_scanner", None)
    assert build_cw_shop_scanner is not None

    runtime = _load_image_backed_rapidocr_runtime(
        image_path=_require_image_backed_shop_shot(IMAGE_BACKED_OPEN_SHOP_SHOT),
        shop_module=shop_module,
    )
    scanner = build_cw_shop_scanner(runtime)
    items, coins, level, reserve_full, max_team_size = scanner()

    assert items
    assert any(item.get("price") is not None for item in items)
    assert coins is not None
    assert level is not None
    assert reserve_full is False
    assert max_team_size is None or isinstance(max_team_size, int)


def test_build_cw_shop_scan_reader_reads_image_backed_team_size_when_fixture_available(monkeypatch):
    shop_module = load_cw_shop_module()
    build_cw_shop_scan_snapshot_reader = getattr(shop_module, "build_cw_shop_scan_snapshot_reader", None)
    assert build_cw_shop_scan_snapshot_reader is not None

    class Runtime:
        def __init__(self):
            self._closed = _load_image_backed_rapidocr_runtime(
                image_path=_require_image_backed_shop_shot(IMAGE_BACKED_CLOSED_SHOP_SHOT),
                shop_module=shop_module,
            )
            self._opened = _load_image_backed_rapidocr_runtime(
                image_path=_require_image_backed_shop_shot(IMAGE_BACKED_OPEN_SHOP_SHOT),
                shop_module=shop_module,
            )
            self.clicks: list[tuple[int, int]] = []
            self._opened_shop = False

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))
            if (x, y) == shop_module.SHOP_OPEN_POINT:
                self._opened_shop = True

        def ocr(self, *, capture):
            active = self._opened if self._opened_shop else self._closed
            return active.ocr(capture=capture)

    monkeypatch.setattr(shop_module, "sleep", lambda seconds: None)
    runtime = Runtime()
    reader = build_cw_shop_scan_snapshot_reader(runtime)
    snapshot = reader()

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert snapshot["max_team_size"] == 3
    assert snapshot["items"]
    assert any(item.get("price") is not None for item in snapshot["items"])
    assert snapshot["coins"] is not None
    assert snapshot["level"] is not None


def test_parse_shop_level_prefers_level_text_over_progress_counter():
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    assert parse_shop_level(
        [
            _rapidocr_piece("购买经验", 0.998622477054596),
            _rapidocr_piece("LV.", 0.9367167353630066),
            _rapidocr_piece("3", 0.9983481764793396),
            _rapidocr_piece("0/4", 0.9960913062095642),
        ],
        default=None,
    ) == 3


def test_parse_shop_level_accepts_merged_level_token():
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    assert parse_shop_level([_rapidocr_piece("LV.3", 0.9983481764793396)], default=None) == 3


def test_parse_shop_level_accepts_single_positive_digit_without_progress_tokens():
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    assert parse_shop_level(
        [_rapidocr_piece("购买经验", 0.998622477054596), _rapidocr_piece("3", 0.9983481764793396)],
        default=None,
    ) == 3


@pytest.mark.parametrize(
    "raw_items",
    [
        [_rapidocr_piece("3", 0.9983481764793396), _rapidocr_piece("LV.", 0.9367167353630066)],
        [_rapidocr_piece("3", 0.9983481764793396), _rapidocr_piece("LV.", 0.9367167353630066), _rapidocr_piece("0/4", 0.9960913062095642)],
        [_rapidocr_piece("0/4", 0.9960913062095642), _rapidocr_piece("3", 0.9983481764793396), _rapidocr_piece("LV.", 0.9367167353630066)],
    ],
)
def test_parse_shop_level_accepts_digit_before_level_marker(raw_items):
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    assert parse_shop_level(raw_items, default=None) == 3


@pytest.mark.parametrize(
    "raw_items",
    [
        [_rapidocr_piece("LV.", 0.9367167353630066), _rapidocr_piece("0/4", 0.9960913062095642)],
        [_rapidocr_piece("LV.0/4", 0.9960913062095642)],
        [_rapidocr_piece("LV.", 0.9367167353630066), _rapidocr_piece("04", 0.9960913062095642)],
        [_rapidocr_piece("04", 0.9960913062095642), _rapidocr_piece("LV.", 0.9367167353630066)],
        [_rapidocr_piece("0", 0.9960913062095642)],
        [_rapidocr_piece("0/4", 0.9960913062095642)],
    ],
)
def test_parse_shop_level_rejects_progress_only_payloads(raw_items):
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    assert parse_shop_level(raw_items, default=None) is None


@pytest.mark.parametrize(
    ("raw_items", "expected"),
    [
        ([_rapidocr_piece("3/3")], 3),
        ([_rapidocr_piece("4/6")], 6),
        (
            [
                _rapidocr_piece("3", bbox=[[10, 10], [24, 10], [24, 34], [10, 34]]),
                _rapidocr_piece("/", bbox=[[25, 10], [33, 10], [33, 34], [25, 34]]),
                _rapidocr_piece("3", bbox=[[34, 10], [48, 10], [48, 34], [34, 34]]),
            ],
            3,
        ),
    ],
)
def test_parse_shop_team_size_extracts_max_size_from_ratio_tokens(raw_items, expected):
    shop_module = load_cw_shop_module()
    parse_shop_team_size = getattr(shop_module, "_parse_shop_team_size", None)
    assert parse_shop_team_size is not None

    assert parse_shop_team_size(raw_items, default=None) == expected


@pytest.mark.parametrize(
    "raw_items",
    [
        [_rapidocr_piece("1-1")],
        [_rapidocr_piece("80")],
        [_rapidocr_piece("Lv.3")],
        [_rapidocr_piece("0/4")],
        [_rapidocr_piece("1/2")],
        [_rapidocr_piece("1/3")],
        [_rapidocr_piece("1"), _rapidocr_piece("/"), _rapidocr_piece("2")],
    ],
)
def test_parse_shop_team_size_rejects_noise_payloads(raw_items):
    shop_module = load_cw_shop_module()
    parse_shop_team_size = getattr(shop_module, "_parse_shop_team_size", None)
    assert parse_shop_team_size is not None

    assert parse_shop_team_size(raw_items, default=None) is None


def test_parse_shop_team_size_rejects_split_tokens_without_continuous_bbox():
    shop_module = load_cw_shop_module()
    parse_shop_team_size = getattr(shop_module, "_parse_shop_team_size", None)
    assert parse_shop_team_size is not None

    raw_items = [
        _rapidocr_piece("3", bbox=[[10, 10], [24, 10], [24, 34], [10, 34]]),
        _rapidocr_piece("/", bbox=[[120, 55], [128, 55], [128, 79], [120, 79]]),
        _rapidocr_piece("3", bbox=[[220, 12], [234, 12], [234, 36], [220, 36]]),
    ]

    assert parse_shop_team_size(raw_items, default=None) is None


def test_build_cw_shop_scan_snapshot_reader_closes_then_reopens_before_scanning(monkeypatch):
    shop_module = load_cw_shop_module()
    build_cw_shop_scan_snapshot_reader = getattr(shop_module, "build_cw_shop_scan_snapshot_reader", None)
    assert build_cw_shop_scan_snapshot_reader is not None

    events: list[tuple[str, object]] = []

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            self.clicks.append(point)
            events.append(("click", point))

        def ocr(self, *, capture):
            self.ocr_calls.append(capture)
            events.append(("ocr", capture))
            if capture == shop_module.SHOP_TEAM_SIZE_REGION:
                return [_rapidocr_piece("3/3")]
            if capture == shop_module.SHOP_SCAN_REGION:
                return [_rapidocr_piece("黑塔"), _rapidocr_piece("1")]
            if capture == shop_module.SHOP_COINS_REGION:
                return [_rapidocr_piece("62")]
            if capture == shop_module.SHOP_LEVEL_REGION:
                return [_rapidocr_piece("LV.3")]
            raise AssertionError(f"unexpected capture: {capture}")

    monkeypatch.setattr(shop_module, "sleep", lambda seconds: events.append(("wait", seconds)))

    runtime = Runtime()
    reader = build_cw_shop_scan_snapshot_reader(runtime)
    snapshot = reader()

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == [
        shop_module.SHOP_TEAM_SIZE_REGION,
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        shop_module.SHOP_LEVEL_REGION,
    ]
    assert events == [
        ("click", shop_module.SHOP_SCAN_RESET_POINT),
        ("wait", shop_module.SHOP_SCAN_SETTLE_SECONDS),
        ("ocr", shop_module.SHOP_TEAM_SIZE_REGION),
        ("click", shop_module.SHOP_OPEN_POINT),
        ("wait", shop_module.SHOP_SCAN_SETTLE_SECONDS),
        ("ocr", shop_module.SHOP_SCAN_REGION),
        ("ocr", shop_module.SHOP_COINS_REGION),
        ("ocr", shop_module.SHOP_LEVEL_REGION),
    ]
    assert snapshot == {
        "opened": True,
        "stale": False,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "level": 3,
        "reserve_full": False,
        "max_team_size": 3,
    }


def test_build_cw_shop_scan_snapshot_reader_continues_when_team_size_ocr_raises(monkeypatch):
    shop_module = load_cw_shop_module()
    build_cw_shop_scan_snapshot_reader = getattr(shop_module, "build_cw_shop_scan_snapshot_reader", None)
    assert build_cw_shop_scan_snapshot_reader is not None

    events: list[tuple[str, object]] = []

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            self.clicks.append(point)
            events.append(("click", point))

        def ocr(self, *, capture):
            self.ocr_calls.append(capture)
            events.append(("ocr", capture))
            if capture == shop_module.SHOP_TEAM_SIZE_REGION:
                raise RuntimeError("team size ocr unavailable")
            if capture == shop_module.SHOP_SCAN_REGION:
                return [_rapidocr_piece("黑塔"), _rapidocr_piece("1")]
            if capture == shop_module.SHOP_COINS_REGION:
                return [_rapidocr_piece("62")]
            if capture == shop_module.SHOP_LEVEL_REGION:
                return [_rapidocr_piece("LV.3")]
            raise AssertionError(f"unexpected capture: {capture}")

    monkeypatch.setattr(shop_module, "sleep", lambda seconds: events.append(("wait", seconds)))

    runtime = Runtime()
    reader = build_cw_shop_scan_snapshot_reader(runtime)
    snapshot = reader()

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == [
        shop_module.SHOP_TEAM_SIZE_REGION,
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        shop_module.SHOP_LEVEL_REGION,
    ]
    assert events == [
        ("click", shop_module.SHOP_SCAN_RESET_POINT),
        ("wait", shop_module.SHOP_SCAN_SETTLE_SECONDS),
        ("ocr", shop_module.SHOP_TEAM_SIZE_REGION),
        ("click", shop_module.SHOP_OPEN_POINT),
        ("wait", shop_module.SHOP_SCAN_SETTLE_SECONDS),
        ("ocr", shop_module.SHOP_SCAN_REGION),
        ("ocr", shop_module.SHOP_COINS_REGION),
        ("ocr", shop_module.SHOP_LEVEL_REGION),
    ]
    assert snapshot == {
        "opened": True,
        "stale": False,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "level": 3,
        "reserve_full": False,
        "max_team_size": None,
    }


def test_read_shop_page_snapshot_without_max_team_size_is_pure_ocr():
    shop_module = load_cw_shop_module()
    read_shop_page_snapshot = getattr(shop_module, "_read_shop_page_snapshot", None)
    assert read_shop_page_snapshot is not None

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def ocr(self, *, capture):
            self.ocr_calls.append(capture)
            if capture == shop_module.SHOP_SCAN_REGION:
                return [_rapidocr_piece("黑塔"), _rapidocr_piece("1")]
            if capture == shop_module.SHOP_COINS_REGION:
                return [_rapidocr_piece("62")]
            if capture == shop_module.SHOP_LEVEL_REGION:
                return [_rapidocr_piece("LV.3")]
            raise AssertionError(f"unexpected capture: {capture}")

    runtime = Runtime()

    assert read_shop_page_snapshot(runtime, read_max_team_size=False) == ([{"name": "黑塔", "price": 1}], 62, 3, False, None)
    assert runtime.ocr_calls == [
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        shop_module.SHOP_LEVEL_REGION,
    ]
    assert runtime.clicks == []


def test_build_cw_shop_scanner_remains_shop_page_only_for_buy_confirmation():
    shop_module = load_cw_shop_module()
    build_cw_shop_scanner = getattr(shop_module, "build_cw_shop_scanner", None)
    assert build_cw_shop_scanner is not None

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def ocr(self, *, capture):
            if capture == shop_module.SHOP_SCAN_REGION:
                return [_rapidocr_piece("黑塔"), _rapidocr_piece("1")]
            if capture == shop_module.SHOP_COINS_REGION:
                return [_rapidocr_piece("62")]
            if capture == shop_module.SHOP_LEVEL_REGION:
                return [_rapidocr_piece("LV.3")]
            if capture == shop_module.SHOP_MAX_TEAM_SIZE_REGION:
                return [_rapidocr_piece("8")]
            raise AssertionError(f"unexpected capture: {capture}")

    runtime = Runtime()
    scanner = build_cw_shop_scanner(runtime)

    assert scanner() == ([{"name": "黑塔", "price": 1}], 62, 3, False, 8)
    assert runtime.clicks == []


def test_scan_cw_shop_converges_to_same_snapshot_from_opened_or_closed_start(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    assert scan_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    payload = {
        "opened": True,
        "stale": False,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 2,
        "level": 3,
        "reserve_full": False,
        "max_team_size": 3,
    }

    opened_session = build_fake_cw_session(tmp_path)
    opened_session.scene_state.setdefault("cw", {})["shop"] = {"opened": True, "stale": True}
    closed_session = build_fake_cw_session(tmp_path)
    closed_session.scene_state.setdefault("cw", {})["shop"] = {"opened": False, "stale": True}

    opened_result = scan_cw_shop(opened_session, scanner=lambda: dict(payload))
    closed_result = scan_cw_shop(closed_session, scanner=lambda: dict(payload))

    assert opened_result.scene_state["cw"]["shop"] == closed_result.scene_state["cw"]["shop"]
    assert opened_result.scene_state["cw"]["shop"] == {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 2,
        "level": 3,
        "reserve_full": False,
        "max_team_size": 3,
        "guide_summary": {
            "remaining_purchases": {},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }


def test_shop_open_marks_shop_opened_and_stale(tmp_path):
    shop_module = load_cw_shop_module()
    open_cw_shop = getattr(shop_module, "open_cw_shop", None)
    assert open_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    opened: list[str] = []
    refreshed = open_cw_shop(session, opener=lambda: opened.append("open"))

    assert refreshed.scene_state["cw"]["shop"] == {
        "stale": True,
        "opened": True,
    }
    assert opened == ["open"]


def test_shop_scan_refreshes_store_snapshot(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    assert scan_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    refreshed = scan_cw_shop(session, scanner=fake_shop_snapshot)

    assert refreshed.scene_state["cw"]["shop"] == {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 1},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }


def test_shop_scan_without_guide_keeps_opened_and_filters_guide_summary(tmp_path):
    shop_module = load_cw_shop_module()
    open_cw_shop = getattr(shop_module, "open_cw_shop", None)
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    assert open_cw_shop is not None
    assert scan_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = None
    session.scene_state["cw"]["constraints"] = {
        "min_coins": 40,
        "min_level": 7,
        "mid_level": 7,
        "priority": {"银狼": 99},
        "positioning": {"银狼": "on_field"},
    }

    open_cw_shop(session, opener=lambda: None)
    refreshed = scan_cw_shop(session, scanner=fake_shop_snapshot)

    assert refreshed.scene_state["cw"]["shop"] == {
        "opened": True,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }


def test_shop_status_returns_current_snapshot(tmp_path):
    shop_module = load_cw_shop_module()
    shop_cw_status = getattr(shop_module, "shop_cw_status", None)
    assert shop_cw_status is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    scanned = getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)

    assert shop_cw_status(scanned) == scanned.scene_state["cw"]["shop"]


def test_shop_buy_slot_mutates_remaining_purchases(tmp_path):
    shop_module = load_cw_shop_module()
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)
    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    assert refreshed.scene_state["cw"]["guide"]["remaining_purchases"]["银狼"] == 0
    assert refreshed.scene_state["cw"]["shop"] == {
        "items": [{"name": "阮·梅", "price": 30}],
        "coins": 22,
        "level": 8,
        "reserve_full": False,
        "max_team_size": 8,
        "opened": True,
        "guide_summary": {
            "remaining_purchases": {"银狼": 0},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True


def test_shop_buy_slot_without_guide_does_not_crash_or_create_guide(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = None
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)

    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    assert refreshed.scene_state["cw"]["guide"] is None
    assert refreshed.scene_state["cw"]["shop"]["opened"] is True
    assert refreshed.scene_state["cw"]["shop"]["items"] == [{"name": "阮·梅", "price": 30}]
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True


@pytest.mark.parametrize(
    ("slot", "expect", "scanner", "code"),
    [
        (1, "希儿", fake_shop_snapshot_after_purchase, "SHOP_SLOT_MISMATCH"),
        (1, "银狼", fake_shop_snapshot, "SHOP_BUY_NOT_CONFIRMED"),
    ],
)
def test_shop_buy_slot_rejects_invalid_purchase_without_consuming_purchase(tmp_path, slot, expect, scanner, code):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)

    with pytest.raises(TrailError) as exc_info:
        buy_cw_shop_slot(
            session,
            slot=slot,
            expect=expect,
            buyer=fake_buy_success,
            scanner=scanner,
        )

    assert exc_info.value.code == code
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 1}
    assert session.scene_state["cw"]["shop"]["items"] == [{"name": "银狼", "price": 20}]


def test_shop_buy_slot_rejects_other_slot_change_without_target_change(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    before_shop = deepcopy(session.scene_state["cw"]["shop"])

    with pytest.raises(TrailError) as exc_info:
        buy_cw_shop_slot(
            session,
            slot=1,
            expect="银狼",
            buyer=fake_buy_success,
            scanner=fake_shop_snapshot_target_unchanged_other_changed,
        )

    assert exc_info.value.code == "SHOP_BUY_NOT_CONFIRMED"
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 1}
    assert session.scene_state["cw"]["shop"] == before_shop


def test_shop_buy_slot_noop_failure_keeps_shop_snapshot_unchanged(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    before_shop = deepcopy(session.scene_state["cw"]["shop"])

    with pytest.raises(TrailError) as exc_info:
        buy_cw_shop_slot(
            session,
            slot=1,
            expect="银狼",
            buyer=fake_buy_success,
            scanner=fake_shop_snapshot,
        )

    assert exc_info.value.code == "SHOP_BUY_NOT_CONFIRMED"
    assert session.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 1}
    assert session.scene_state["cw"]["shop"] == before_shop


def test_shop_refresh_invalidates_snapshot(tmp_path):
    shop_module = load_cw_shop_module()
    refresh_cw_shop = getattr(shop_module, "refresh_cw_shop", None)
    assert refresh_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    scanned = getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)
    refreshed_calls: list[str] = []
    refreshed = refresh_cw_shop(scanned, refresher=lambda: refreshed_calls.append("refresh"))

    assert refreshed.scene_state["cw"]["shop"]["stale"] is True
    assert refreshed_calls == ["refresh"]


def test_shop_close_marks_shop_closed_and_stale(tmp_path):
    shop_module = load_cw_shop_module()
    close_cw_shop = getattr(shop_module, "close_cw_shop", None)
    assert close_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    closed: list[str] = []
    refreshed = close_cw_shop(session, closer=lambda: closed.append("close"))

    assert refreshed.scene_state["cw"]["shop"] == {
        "stale": True,
        "opened": False,
    }
    assert closed == ["close"]


def _build_cw_harness(tmp_path: Path, *, runtime=None):
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    registry = SessionServiceRegistry()
    service = registry.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    runtime = SimpleNamespace() if runtime is None else runtime
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: runtime)
    cw_service = CwService(runtime_service=runtime_service)
    command_service = CommandService(runtime_service=runtime_service, session_service=registry, cw_service=cw_service)
    return registry, service, session, cw_service, command_service


def _run_cw_mutation(*, command_service, session, workspace_root: Path, request_id: str, method: str, payload: dict):
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION

    return command_service.handle(
        DaemonRequest(
            request_id=request_id,
            protocol_version=PROTOCOL_VERSION,
            workspace_root=str(workspace_root),
            session_id=session.session_id,
            verbose=False,
            method=method,
            payload={"session_id": session.session_id, **payload},
        )
    )


def _set_shop(session, shop: dict):
    session.scene_state.setdefault("cw", {})["shop"] = deepcopy(shop)
    return session


@pytest.mark.parametrize(
    ("method", "request_id", "payload", "setup_patches", "expected_key", "expected_value"),
    [
        (
            "cw.shop.open",
            "req-shop-open-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_opener_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.open_cw_shop", lambda session, opener: _set_shop(session, {"opened": True, "stale": True})),
            ),
            "opened",
            True,
        ),
        (
            "cw.shop.buy_slot",
            "req-shop-buy-1",
            {"slot": 2, "expect": "希儿"},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_buyer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: object()),
                monkeypatch.setattr(
                    "trail.daemon.cw_service.buy_cw_shop_slot",
                    lambda session, slot, expect, buyer, scanner: _set_shop(session, {"slot": slot, "expect": expect, "opened": True, "stale": False}),
                ),
            ),
            "expect",
            "希儿",
        ),
        (
            "cw.shop.refresh",
            "req-shop-refresh-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_refresher_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.refresh_cw_shop", lambda session, refresher: _set_shop(session, {"refreshed": True, "opened": True, "stale": False})),
            ),
            "refreshed",
            True,
        ),
        (
            "cw.shop.close",
            "req-shop-close-1",
            {},
            lambda monkeypatch: (
                monkeypatch.setattr("trail.daemon.cw_service.shop_closer_factory", lambda runtime: object()),
                monkeypatch.setattr("trail.daemon.cw_service.close_cw_shop", lambda session, closer: _set_shop(session, {"opened": False, "stale": True})),
            ),
            "opened",
            False,
        ),
    ],
)
def test_cw_shop_mutating_commands_flow_through_command_service_journal(
    tmp_path: Path,
    monkeypatch,
    method: str,
    request_id: str,
    payload: dict,
    setup_patches,
    expected_key: str,
    expected_value,
):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    setup_patches(monkeypatch)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=request_id,
        method=method,
        payload=payload,
    )
    status = service.request_status(request_id)

    assert envelope["ok"] is True
    assert envelope["data"][expected_key] == expected_value
    assert status["final_state"] == "completed"
    assert service.load_session(session.session_id).scene_state["cw"]["shop"][expected_key] == expected_value


def test_cw_shop_scan_read_commands_persist_two_phase_snapshot(tmp_path: Path):
    shop_module = load_cw_shop_module()
    runtime = _build_cw_shop_scan_runtime(shop_module)
    registry, service, session, cw_service, _ = _build_cw_harness(tmp_path, runtime=runtime)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = {"opened": False, "stale": True}
    service.save_session(loaded)

    scanned = cw_service.handle(
        method="cw.shop.scan",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )
    status = cw_service.handle(
        method="cw.shop.status",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=service,
    )

    expected_snapshot = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "level": 3,
        "reserve_full": False,
        "max_team_size": 3,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == [
        shop_module.SHOP_TEAM_SIZE_REGION,
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        shop_module.SHOP_LEVEL_REGION,
    ]
    assert scanned == expected_snapshot
    assert status == expected_snapshot
    assert service.load_session(session.session_id).scene_state["cw"]["shop"] == expected_snapshot


def test_cw_shop_scan_flows_through_command_service_mutation_journal_and_persists_snapshot(tmp_path: Path):
    shop_module = load_cw_shop_module()
    runtime = _build_cw_shop_scan_runtime(shop_module)
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = {"opened": False, "stale": True}
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-scan",
        method="cw.shop.scan",
        payload={},
    )
    status = service.request_status("req-cw-shop-scan")
    persisted = service.load_session(session.session_id)

    expected_snapshot = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "level": 3,
        "reserve_full": False,
        "max_team_size": 3,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == [
        shop_module.SHOP_TEAM_SIZE_REGION,
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        shop_module.SHOP_LEVEL_REGION,
    ]
    assert envelope["ok"] is True
    assert envelope["data"] == expected_snapshot
    assert status["final_state"] == "completed"
    assert persisted.scene_state["cw"]["shop"] == expected_snapshot


def test_cw_shop_scan_marks_applied_but_not_persisted_when_save_fails_after_clicks(
    tmp_path: Path,
    monkeypatch,
):
    shop_module = load_cw_shop_module()
    runtime = _build_cw_shop_scan_runtime(shop_module)
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    before_shop = {
        "opened": False,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = deepcopy(before_shop)
    service.save_session(loaded)

    def fail_save(*args, **kwargs):
        raise OSError("flush failed")

    monkeypatch.setattr(service, "save_session", fail_save)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-scan-save-fail",
        method="cw.shop.scan",
        payload={},
    )
    status = service.request_status("req-cw-shop-scan-save-fail")
    persisted = service.load_session(session.session_id)

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == [
        shop_module.SHOP_TEAM_SIZE_REGION,
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        shop_module.SHOP_LEVEL_REGION,
    ]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert envelope["debug"]["detail"] == "OSError: flush failed"
    assert envelope["debug"]["last_known_stage"] == "side_effect_applied"
    assert render_output("cw.shop.scan", envelope).splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-scan-save-fail",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-scan-save-fail",
    ]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert persisted.scene_state["cw"]["shop"] == before_shop


def test_cw_shop_scan_marks_applied_but_not_persisted_when_click_side_effect_raises_after_ui_action(
    tmp_path: Path,
):
    shop_module = load_cw_shop_module()
    runtime = _build_cw_shop_scan_runtime(
        shop_module,
        click_error=RuntimeError("click after effect boom"),
        click_error_on_call=1,
    )
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    before_shop = {
        "opened": False,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = deepcopy(before_shop)
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-scan-click-late-fail",
        method="cw.shop.scan",
        payload={},
    )
    status = service.request_status("req-cw-shop-scan-click-late-fail")
    persisted = service.load_session(session.session_id)

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert envelope["debug"]["detail"] == "RuntimeError: click after effect boom"
    assert envelope["debug"]["last_known_stage"] == "side_effect_applied"
    assert render_output("cw.shop.scan", envelope).splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-scan-click-late-fail",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-scan-click-late-fail",
    ]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert persisted.scene_state["cw"]["shop"] == before_shop


def test_cw_shop_scan_keeps_failed_before_side_effect_when_click_fails_before_input(
    tmp_path: Path,
):
    shop_module = load_cw_shop_module()
    runtime = _build_cw_shop_scan_runtime(
        shop_module,
        click_error=TrailError("INPUT_BACKEND_MISSING", "input backend missing"),
        click_error_on_call=1,
    )
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    before_shop = {
        "opened": False,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = deepcopy(before_shop)
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-scan-click-preflight-fail",
        method="cw.shop.scan",
        payload={},
    )
    status = service.request_status("req-cw-shop-scan-click-preflight-fail")
    persisted = service.load_session(session.session_id)

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "INPUT_BACKEND_MISSING",
        "message": "input backend missing",
    }
    assert envelope.get("debug") is None
    assert render_output("cw.shop.scan", envelope).splitlines() == [
        "fail cw.shop.scan code=INPUT_BACKEND_MISSING",
        "request id=req-cw-shop-scan-click-preflight-fail",
        'why msg="input backend missing"',
    ]
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False
    assert persisted.scene_state["cw"]["shop"] == before_shop


def test_cw_shop_scan_keeps_failed_before_side_effect_when_window_not_foreground_happens_before_input(
    tmp_path: Path,
):
    shop_module = load_cw_shop_module()
    runtime = _build_cw_shop_scan_runtime(
        shop_module,
        click_error=TrailError("WINDOW_NOT_FOREGROUND", "窗口不在前台，无法执行输入"),
        click_error_on_call=1,
        click_records_before_error=False,
    )
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    before_shop = {
        "opened": False,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = deepcopy(before_shop)
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-scan-window-not-foreground-pre",
        method="cw.shop.scan",
        payload={},
    )
    status = service.request_status("req-cw-shop-scan-window-not-foreground-pre")
    persisted = service.load_session(session.session_id)

    assert runtime.clicks == []
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "WINDOW_NOT_FOREGROUND",
        "message": "窗口不在前台，无法执行输入",
    }
    assert envelope.get("debug") is None
    assert render_output("cw.shop.scan", envelope).splitlines() == [
        "fail cw.shop.scan code=WINDOW_NOT_FOREGROUND",
        "request id=req-cw-shop-scan-window-not-foreground-pre",
        "why msg=窗口不在前台，无法执行输入",
    ]
    assert status["final_state"] == "failed_before_side_effect"
    assert status["tainted"] is False
    assert persisted.scene_state["cw"]["shop"] == before_shop


def test_cw_shop_scan_marks_applied_but_not_persisted_when_click_reports_window_not_foreground_after_input(
    tmp_path: Path,
):
    shop_module = load_cw_shop_module()
    error = TrailError("WINDOW_NOT_FOREGROUND", "窗口不在前台，无法执行输入")
    error.completed_after_side_effect = True
    runtime = _build_cw_shop_scan_runtime(
        shop_module,
        click_error=error,
        click_error_on_call=1,
    )
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    before_shop = {
        "opened": False,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = deepcopy(before_shop)
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-scan-window-not-foreground",
        method="cw.shop.scan",
        payload={},
    )
    status = service.request_status("req-cw-shop-scan-window-not-foreground")
    persisted = service.load_session(session.session_id)

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert envelope["debug"]["detail"] == "TrailError: 窗口不在前台，无法执行输入"
    assert envelope["debug"]["last_known_stage"] == "side_effect_applied"
    assert render_output("cw.shop.scan", envelope).splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-scan-window-not-foreground",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-scan-window-not-foreground",
    ]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert persisted.scene_state["cw"]["shop"] == before_shop


def test_cw_shop_scan_marks_persisted_but_response_unknown_when_metadata_boom_happens_after_persist(
    tmp_path: Path,
):
    shop_module = load_cw_shop_module()
    absolute_screenshot_path = tmp_path / ".trail" / "shots" / "req-cw-shop-scan-metadata-boom.png"
    runtime = _build_cw_shop_scan_runtime(
        shop_module,
        screenshot_path=str(absolute_screenshot_path),
        references=[{"path": "trail/scenes/cw/references/shop.png", "similarity": 0.97}],
        collect_warnings_error=RuntimeError("metadata boom"),
    )
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    before_shop = {
        "opened": False,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = deepcopy(before_shop)
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-scan-metadata-boom",
        method="cw.shop.scan",
        payload={},
    )
    status = service.request_status("req-cw-shop-scan-metadata-boom")
    persisted = service.load_session(session.session_id)
    expected_snapshot = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "level": 3,
        "reserve_full": False,
        "max_team_size": 3,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == [
        shop_module.SHOP_TEAM_SIZE_REGION,
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        shop_module.SHOP_LEVEL_REGION,
    ]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert envelope["screenshot"] == ".trail/shots/req-cw-shop-scan-metadata-boom.png"
    assert envelope["debug"]["detail"] == "RuntimeError: metadata boom"
    assert envelope["debug"]["last_known_stage"] == "state_persisted"
    assert render_output("cw.shop.scan", envelope).splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-scan-metadata-boom",
        "shot path=.trail/shots/req-cw-shop-scan-metadata-boom.png",
        'why msg="mutation result unknown"',
        "ref path=trail/scenes/cw/references/shop.png sim=0.97",
        "recover action=daemon.request_status request=req-cw-shop-scan-metadata-boom",
    ]
    assert status["final_state"] == "persisted_but_response_unknown"
    assert status["tainted"] is True
    assert persisted.scene_state["cw"]["shop"] == expected_snapshot


def test_cw_shop_buy_slot_marks_applied_but_not_persisted_when_confirmation_fails_after_purchase(
    tmp_path: Path,
    monkeypatch,
):
    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = Runtime()
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 1}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "reserve_full": False,
        "max_team_size": 8,
        "opened": True,
        "guide_summary": {
            "remaining_purchases": {"银狼": 1},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    service.save_session(loaded)
    monkeypatch.setattr(
        "trail.daemon.cw_service.shop_buyer_factory",
        lambda runtime: lambda slot, expect: runtime.click_point(111, 222),
    )
    monkeypatch.setattr("trail.daemon.cw_service.shop_scanner_factory", lambda runtime: fake_shop_snapshot)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-buy-late-fail",
        method="cw.shop.buy_slot",
        payload={"slot": 1, "expect": "银狼"},
    )
    status = service.request_status("req-cw-shop-buy-late-fail")
    persisted = service.load_session(session.session_id)

    assert runtime.clicks == [(111, 222)]
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert "shop purchase not confirmed for slot 1: 银狼" in envelope["debug"]["detail"]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True
    assert persisted.scene_state["cw"]["guide"]["remaining_purchases"] == {"银狼": 1}
    assert persisted.scene_state["cw"]["shop"]["items"] == [{"name": "银狼", "price": 20}]
