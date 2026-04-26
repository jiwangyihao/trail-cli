from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from trail.core.errors import TrailError
from trail.output.rendering import render_output
from trail.session.store import SessionStore


IMAGE_BACKED_SHOT_ROOT = Path(r"C:\Users\34404\source\repos\trail-cli\.trail\shots")
IMAGE_BACKED_OPEN_SHOP_SHOT = IMAGE_BACKED_SHOT_ROOT / "2033ad2b66ff4947b0b6644f94f3a6c1-f6e3c2d5f4.jpg"
IMAGE_BACKED_CLOSED_SHOP_SHOT = IMAGE_BACKED_SHOT_ROOT / "3e2fae9b5be74f99bbfce0953d9d1ec7-7ed1d69ed6.jpg"


def fake_shop_snapshot():
    return {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
    }


def fake_shop_snapshot_after_purchase():
    return {
        "items": [{"name": "阮·梅", "price": 30}],
        "coins": 22,
        "level": 8,
        "exp": "8/52",
        "reserve_full": False,
        "team_size": "7/7",
    }


def fake_shop_snapshot_target_unchanged_other_changed():
    return {
        "items": [{"name": "银狼", "price": 20}, {"name": "阮·梅", "price": 30}],
        "coins": 22,
        "level": 8,
        "exp": "8/52",
        "reserve_full": False,
        "team_size": "7/7",
    }


def load_cw_shop_module():
    try:
        return importlib.import_module("trail.scenes.cw.shop")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing trail.scenes.cw.shop: {exc}")


def _complete_guide_state(*, purchases: dict | None = None) -> dict:
    from tests.conftest import complete_cw_guide_state

    return complete_cw_guide_state(purchases=purchases)


def test_maybe_dump_shop_capture_writes_region_png(tmp_path, monkeypatch):
    shop_module = load_cw_shop_module()
    maybe_dump_shop_capture = getattr(shop_module, "_maybe_dump_shop_capture", None)
    assert maybe_dump_shop_capture is not None

    workspace = tmp_path / ".trail" / "shots"
    workspace.mkdir(parents=True)
    monkeypatch.setenv(shop_module.SHOP_DEBUG_CAPTURE_ENV, "1")

    calls: list[dict[str, int]] = []

    class RuntimeStub:
        window = SimpleNamespace(workspace=workspace)

        def screenshot(self, **capture):
            calls.append(capture)
            return b"fake-png"

    path = maybe_dump_shop_capture(RuntimeStub(), name="scan", capture=shop_module.SHOP_SCAN_REGION)

    assert path == workspace / "debug-shop-scan.png"
    assert path.read_bytes() == b"fake-png"
    assert calls == [shop_module.SHOP_SCAN_REGION]


def _rapidocr_piece(text: str, score: float = 0.99, *, bbox=None):
    return [([[0, 0], [1, 0], [1, 1], [0, 1]] if bbox is None else bbox), text, score]


def _image_for_region(region: dict[str, int]) -> Image.Image:
    return Image.new("RGB", (region["to_x"] - region["from_x"], region["to_y"] - region["from_y"]), color="white")


def _global_stage_status_regions() -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    from trail.scenes.cw import stage as stage_module

    return stage_module.CW_STATUS_TEAM_SIZE_REGION, stage_module.CW_STATUS_LEVEL_REGION, stage_module.CW_STATUS_EXP_REGION


def _install_fake_shop_batch_ocr(monkeypatch, shop_module, *, events=None, items=None, coins=None):
    calls: list[dict[str, object]] = []
    items = [_rapidocr_piece("黑塔"), _rapidocr_piece("1")] if items is None else items
    coins = [_rapidocr_piece("62")] if coins is None else coins

    def fake_run_batch_ocr(runtime, targets, *, trace_prefix):
        del runtime
        keys = [target.key for target in targets]
        calls.append({"keys": keys, "trace_prefix": trace_prefix})
        if events is not None:
            events.append(("batch_ocr", trace_prefix, tuple(keys)))
        return SimpleNamespace(
            by_key={
                "items": SimpleNamespace(pieces=list(items), text=None),
                "coins": SimpleNamespace(pieces=list(coins), text="".join(str(piece[1]) for piece in coins)),
            }
        )

    monkeypatch.setattr(shop_module, "run_batch_ocr", fake_run_batch_ocr, raising=False)
    return calls


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
    ocr_image_error: Exception | None = None,
):
    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.ocr_calls: list[dict[str, int]] = []
            self.ocr_image_calls: list[dict[str, object]] = []
            self.capture_image_calls: list[dict[str, int]] = []
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
            raise AssertionError(f"shop scan should use batch OCR instead of runtime.ocr: {capture}")

        def ocr_image(self, image, **kwargs):
            self.ocr_image_calls.append({"size": image.size, "kwargs": kwargs})
            if ocr_image_error is not None:
                raise ocr_image_error
            return []

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            if capture in _global_stage_status_regions():
                raise AssertionError(f"shop scan should not capture global status region: {capture}")
            if capture not in (shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION):
                raise AssertionError(f"unexpected capture_image region: {capture}")
            self.capture_image_calls.append(capture)
            return _image_for_region(capture)

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
        def capture_image(self, *, from_x=None, from_y=None, to_x=None, to_y=None, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            region = {
                "from_x": int(round(capture["from_x"] * scale_x)),
                "from_y": int(round(capture["from_y"] * scale_y)),
                "to_x": int(round(capture["to_x"] * scale_x)),
                "to_y": int(round(capture["to_y"] * scale_y)),
            }
            cropped = image.crop((region["from_x"], region["from_y"], region["to_x"], region["to_y"]))
            return cropped.resize((capture["to_x"] - capture["from_x"], capture["to_y"] - capture["from_y"]))

        def ocr_image(self, image, **kwargs):
            del kwargs
            try:
                return adapter.run(image).pieces
            except TrailError as exc:
                if exc.code == "OCR_BACKEND_UNAVAILABLE":
                    pytest.skip(f"image-backed cw shop OCR backend unavailable: {exc}")
                raise

        def ocr(self, *, capture):
            raise AssertionError(f"image-backed shop scanner should use batch OCR, got runtime.ocr({capture})")

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


def test_parse_shop_items_preserves_empty_slot_from_ocr_positions():
    shop_module = load_cw_shop_module()
    parse_shop_items = getattr(shop_module, "_parse_shop_items", None)
    assert parse_shop_items is not None

    raw_items = [
        _rapidocr_piece("翡翠", bbox=[[30, 0], [150, 0], [150, 28], [30, 28]]),
        _rapidocr_piece("1", bbox=[[170, 0], [200, 0], [200, 28], [170, 28]]),
        _rapidocr_piece("三月七", bbox=[[320, 0], [440, 0], [440, 28], [320, 28]]),
        _rapidocr_piece("1", bbox=[[460, 0], [490, 0], [490, 28], [460, 28]]),
        _rapidocr_piece("万敌", bbox=[[820, 0], [940, 0], [940, 28], [820, 28]]),
        _rapidocr_piece("2", bbox=[[950, 0], [980, 0], [980, 28], [950, 28]]),
        _rapidocr_piece("符玄", bbox=[[1090, 0], [1210, 0], [1210, 28], [1090, 28]]),
        _rapidocr_piece("4", bbox=[[1230, 0], [1260, 0], [1260, 28], [1230, 28]]),
    ]

    assert parse_shop_items(raw_items) == (
        [
            {"slot": 1, "name": "翡翠", "price": 1},
            {"slot": 2, "name": "三月七", "price": 1},
            {"slot": 3, "name": None, "price": None},
            {"slot": 4, "name": "万敌", "price": 2},
            {"slot": 5, "name": "符玄", "price": 4},
        ],
        False,
    )


def test_build_cw_shop_scanner_reads_rapidocr_tuple_text_fields(monkeypatch):
    shop_module = load_cw_shop_module()
    build_cw_shop_scanner = getattr(shop_module, "build_cw_shop_scanner", None)
    assert build_cw_shop_scanner is not None

    expected_team_size_region = {"from_x": 835, "from_y": 188, "to_x": 1095, "to_y": 281}
    expected_coins_region = {"from_x": 1615, "from_y": 902, "to_x": 1698, "to_y": 956}
    expected_level_region = {"from_x": 220, "from_y": 880, "to_x": 360, "to_y": 950}
    expected_exp_region = {"from_x": 256, "from_y": 943, "to_x": 325, "to_y": 973}
    expected_shop_open_point = (1628, 992)

    assert shop_module.SHOP_OPEN_POINT == expected_shop_open_point
    assert shop_module.SHOP_COINS_REGION == expected_coins_region
    assert _global_stage_status_regions() == (expected_team_size_region, expected_level_region, expected_exp_region)

    batch_calls = _install_fake_shop_batch_ocr(
        monkeypatch,
        shop_module,
        items=[
            _rapidocr_piece("黑塔", 0.9996806085109711),
            _rapidocr_piece("1", 0.9982701539993286),
            _rapidocr_piece("阿格莱雅", 0.9880169034004211),
            _rapidocr_piece("1", 0.9931700229644775),
        ],
        coins=[_rapidocr_piece("62", 0.998634397983551)],
    )

    class Runtime:
        def __init__(self):
            self.capture_image_calls: list[dict[str, int]] = []

        def ocr(self, *, capture):
            raise AssertionError(f"shop scanner should not call runtime.ocr: {capture}")

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            if capture in _global_stage_status_regions():
                raise AssertionError(f"shop scanner should not capture global status: {capture}")
            self.capture_image_calls.append(capture)
            return _image_for_region(capture)

    runtime = Runtime()
    scanner = build_cw_shop_scanner(runtime)

    assert scanner() == {
        "items": [{"name": "黑塔", "price": 1}, {"name": "阿格莱雅", "price": 1}],
        "coins": 62,
        "reserve_full": False,
    }
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, expected_coins_region]
    assert batch_calls == [{"keys": ["items", "coins"], "trace_prefix": "cw_shop_batch_ocr"}]


def test_build_cw_shop_scanner_reads_image_backed_shop_page_when_fixture_available():
    shop_module = load_cw_shop_module()
    build_cw_shop_scanner = getattr(shop_module, "build_cw_shop_scanner", None)
    assert build_cw_shop_scanner is not None

    runtime = _load_image_backed_rapidocr_runtime(
        image_path=_require_image_backed_shop_shot(IMAGE_BACKED_OPEN_SHOP_SHOT),
        shop_module=shop_module,
    )
    scanner = build_cw_shop_scanner(runtime)
    snapshot = scanner()

    assert snapshot["items"]
    assert any(item.get("price") is not None for item in snapshot["items"])
    assert snapshot["coins"] is not None
    assert snapshot["reserve_full"] is False
    assert "level" not in snapshot
    assert "exp" not in snapshot


def test_build_cw_shop_scan_reader_reads_image_backed_shop_page_when_fixture_available(monkeypatch):
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
            raise AssertionError(f"shop scan reader should use batch OCR, got runtime.ocr({capture})")

        def capture_image(self, *, from_x=None, from_y=None, to_x=None, to_y=None, normalize=True):
            active = self._opened if self._opened_shop else self._closed
            return active.capture_image(from_x=from_x, from_y=from_y, to_x=to_x, to_y=to_y, normalize=normalize)

        def ocr_image(self, image, **kwargs):
            active = self._opened if self._opened_shop else self._closed
            return active.ocr_image(image, **kwargs)

    monkeypatch.setattr(shop_module, "sleep", lambda seconds: None)
    runtime = Runtime()
    reader = build_cw_shop_scan_snapshot_reader(runtime)
    snapshot = reader()

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert snapshot["items"]
    assert any(item.get("price") is not None for item in snapshot["items"])
    assert snapshot["coins"] is not None
    assert "team_size" not in snapshot
    assert "level" not in snapshot
    assert "exp" not in snapshot


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
        ([_rapidocr_piece("3/3")], "3/3"),
        ([_rapidocr_piece("4/6")], "4/6"),
        (
            [
                _rapidocr_piece("3", bbox=[[10, 10], [24, 10], [24, 34], [10, 34]]),
                _rapidocr_piece("/", bbox=[[25, 10], [33, 10], [33, 34], [25, 34]]),
                _rapidocr_piece("3", bbox=[[34, 10], [48, 10], [48, 34], [34, 34]]),
            ],
            "3/3",
        ),
    ],
)
def test_parse_shop_team_size_extracts_max_size_from_ratio_tokens(raw_items, expected):
    shop_module = load_cw_shop_module()
    parse_shop_team_size = getattr(shop_module, "_parse_shop_team_size", None)
    assert parse_shop_team_size is not None

    assert parse_shop_team_size(raw_items, default=None) == expected


def test_parse_shop_exp_extracts_progress_ratio():
    shop_module = load_cw_shop_module()
    parse_shop_exp = getattr(shop_module, "_parse_shop_exp", None)
    assert parse_shop_exp is not None

    assert parse_shop_exp([_rapidocr_piece("4/52")], default=None) == "4/52"


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
    batch_calls = _install_fake_shop_batch_ocr(monkeypatch, shop_module, events=events)

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.capture_image_calls: list[dict[str, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            self.clicks.append(point)
            events.append(("click", point))

        def ocr(self, *, capture):
            raise AssertionError(f"shop scan reader should not call runtime.ocr: {capture}")

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            if capture in _global_stage_status_regions():
                raise AssertionError(f"shop scan reader should not capture global status: {capture}")
            self.capture_image_calls.append(capture)
            events.append(("capture_image", capture))
            return _image_for_region(capture)

    monkeypatch.setattr(shop_module, "sleep", lambda seconds: events.append(("wait", seconds)))

    runtime = Runtime()
    reader = build_cw_shop_scan_snapshot_reader(runtime)
    snapshot = reader()

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]
    assert batch_calls == [{"keys": ["items", "coins"], "trace_prefix": "cw_shop_batch_ocr"}]
    assert events == [
        ("click", shop_module.SHOP_SCAN_RESET_POINT),
        ("wait", shop_module.SHOP_SCAN_RESET_SETTLE_SECONDS),
        ("click", shop_module.SHOP_OPEN_POINT),
        ("wait", shop_module.SHOP_SCAN_OPEN_SETTLE_SECONDS),
        ("capture_image", shop_module.SHOP_SCAN_REGION),
        ("capture_image", shop_module.SHOP_COINS_REGION),
        ("batch_ocr", "cw_shop_batch_ocr", ("items", "coins")),
    ]
    assert snapshot == {
        "opened": True,
        "stale": False,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "reserve_full": False,
    }


def test_build_cw_shop_scan_snapshot_reader_does_not_capture_global_status_regions(monkeypatch):
    shop_module = load_cw_shop_module()
    build_cw_shop_scan_snapshot_reader = getattr(shop_module, "build_cw_shop_scan_snapshot_reader", None)
    assert build_cw_shop_scan_snapshot_reader is not None

    events: list[tuple[str, object]] = []
    _install_fake_shop_batch_ocr(monkeypatch, shop_module, events=events)

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.capture_image_calls: list[dict[str, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            point = (x, y)
            self.clicks.append(point)
            events.append(("click", point))

        def ocr(self, *, capture):
            raise AssertionError(f"shop scan reader should not call runtime.ocr: {capture}")

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            if capture in _global_stage_status_regions():
                raise AssertionError(f"shop scan reader should not capture global status: {capture}")
            self.capture_image_calls.append(capture)
            events.append(("capture_image", capture))
            return _image_for_region(capture)

    monkeypatch.setattr(shop_module, "sleep", lambda seconds: events.append(("wait", seconds)))

    runtime = Runtime()
    reader = build_cw_shop_scan_snapshot_reader(runtime)
    snapshot = reader()

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]
    assert events == [
        ("click", shop_module.SHOP_SCAN_RESET_POINT),
        ("wait", shop_module.SHOP_SCAN_RESET_SETTLE_SECONDS),
        ("click", shop_module.SHOP_OPEN_POINT),
        ("wait", shop_module.SHOP_SCAN_OPEN_SETTLE_SECONDS),
        ("capture_image", shop_module.SHOP_SCAN_REGION),
        ("capture_image", shop_module.SHOP_COINS_REGION),
        ("batch_ocr", "cw_shop_batch_ocr", ("items", "coins")),
    ]
    assert snapshot == {
        "opened": True,
        "stale": False,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "reserve_full": False,
    }


def test_read_shop_page_snapshot_uses_one_batch_ocr_without_global_status(monkeypatch):
    shop_module = load_cw_shop_module()
    read_shop_page_snapshot = getattr(shop_module, "_read_shop_page_snapshot", None)
    assert read_shop_page_snapshot is not None
    batch_calls = _install_fake_shop_batch_ocr(monkeypatch, shop_module)

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.capture_image_calls: list[dict[str, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def ocr(self, *, capture):
            raise AssertionError(f"shop page snapshot should not call runtime.ocr: {capture}")

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            if capture in _global_stage_status_regions():
                raise AssertionError(f"shop page snapshot should not capture global status: {capture}")
            self.capture_image_calls.append(capture)
            return _image_for_region(capture)

    runtime = Runtime()

    assert read_shop_page_snapshot(runtime, read_team_size=False) == {
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "reserve_full": False,
    }
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]
    assert batch_calls == [{"keys": ["items", "coins"], "trace_prefix": "cw_shop_batch_ocr"}]
    assert runtime.clicks == []


def test_read_shop_page_snapshot_reads_stage_status_when_requested(monkeypatch):
    shop_module = load_cw_shop_module()
    read_shop_page_snapshot = getattr(shop_module, "_read_shop_page_snapshot", None)
    assert read_shop_page_snapshot is not None
    _install_fake_shop_batch_ocr(monkeypatch, shop_module)

    class Runtime:
        def __init__(self):
            self.capture_image_calls: list[dict[str, int]] = []

        def ocr(self, *, capture):
            raise AssertionError(f"shop page snapshot should not call runtime.ocr: {capture}")

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            self.capture_image_calls.append(capture)
            return _image_for_region(capture)

    runtime = Runtime()
    snapshot = read_shop_page_snapshot(runtime, read_team_size=True)

    assert snapshot == {
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "reserve_full": False,
        "level": None,
        "exp": None,
        "team_size": None,
    }
    expected_team_size_region, expected_level_region, expected_exp_region = _global_stage_status_regions()
    assert runtime.capture_image_calls == [
        shop_module.SHOP_SCAN_REGION,
        shop_module.SHOP_COINS_REGION,
        expected_level_region,
        expected_exp_region,
        expected_team_size_region,
    ]


def test_read_shop_page_snapshot_returns_shop_facts_only_from_batch_result(monkeypatch):
    shop_module = load_cw_shop_module()
    read_shop_page_snapshot = getattr(shop_module, "_read_shop_page_snapshot", None)
    assert read_shop_page_snapshot is not None
    _install_fake_shop_batch_ocr(monkeypatch, shop_module, items=[_rapidocr_piece("备用区已满")], coins=[])

    class Runtime:
        def ocr(self, *, capture=None):
            raise AssertionError(f"shop page snapshot should not call runtime.ocr: {capture}")

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            if capture in _global_stage_status_regions():
                raise AssertionError(f"shop page snapshot should not capture global status: {capture}")
            return _image_for_region(capture)

    snapshot = read_shop_page_snapshot(Runtime(), read_team_size=False)

    assert snapshot == {
        "items": [],
        "coins": 0,
        "reserve_full": True,
    }


def test_build_cw_shop_scanner_remains_shop_page_only_for_buy_confirmation(monkeypatch):
    shop_module = load_cw_shop_module()
    build_cw_shop_scanner = getattr(shop_module, "build_cw_shop_scanner", None)
    assert build_cw_shop_scanner is not None
    _install_fake_shop_batch_ocr(monkeypatch, shop_module)

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []
            self.capture_image_calls: list[dict[str, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

        def ocr(self, *, capture):
            raise AssertionError(f"shop scanner should not call runtime.ocr: {capture}")

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            if capture in _global_stage_status_regions():
                raise AssertionError(f"shop scanner should not capture global status: {capture}")
            self.capture_image_calls.append(capture)
            return _image_for_region(capture)

    runtime = Runtime()
    scanner = build_cw_shop_scanner(runtime)

    assert scanner() == {
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "reserve_full": False,
    }
    assert runtime.clicks == []
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]


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
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "3/3",
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
        "reserve_full": False,
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

    session = build_fake_cw_session(tmp_path)
    refreshed = scan_cw_shop(session, scanner=fake_shop_snapshot)

    assert refreshed.scene_state["cw"]["shop"] == {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "reserve_full": False,
        "guide_summary": {
            "remaining_purchases": {},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }


def test_shop_scan_projects_stage_status_without_rescanning_global_regions(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    project_cw_shop_snapshot = getattr(shop_module, "project_cw_shop_snapshot", None)
    assert scan_cw_shop is not None
    assert project_cw_shop_snapshot is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {
        "status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}
    }

    scanner = lambda: {
        "opened": True,
        "stale": False,
        "items": [{"slot": 1, "name": "黑塔", "price": 1}],
        "coins": 62,
        "level": 99,
        "exp": "99/99",
        "reserve_full": False,
        "team_size": "9/9",
        "role_count": {"total": 9},
    }
    refreshed = scan_cw_shop(session, scanner=scanner)
    projected = project_cw_shop_snapshot(refreshed)

    assert "level" not in refreshed.scene_state["cw"]["shop"]
    assert "exp" not in refreshed.scene_state["cw"]["shop"]
    assert "team_size" not in refreshed.scene_state["cw"]["shop"]
    assert "role_count" not in refreshed.scene_state["cw"]["shop"]
    assert projected["stage_status"] == {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}
    assert projected["stage_status_stale"] is False
    projected["stage_status"]["level"] = 1
    assert refreshed.scene_state["cw"]["stage"]["status"]["level"] == 7


def test_parse_shop_items_uses_rapidocr_tuple_text_instead_of_confidence():
    shop_module = load_cw_shop_module()
    parse_shop_items = getattr(shop_module, "_parse_shop_items", None)
    assert parse_shop_items is not None

    raw_items = [
        [[0, 0], "黑塔", 0.9996806085109711],
        [[0, 0], "1", 0.9982701539993286],
        [[0, 0], "阿格莱雅", 0.9880169034004211],
        [[0, 0], "1", 0.9931700229644775],
    ]

    assert parse_shop_items(raw_items) == (
        [
            {"name": "黑塔", "price": 1},
            {"name": "阿格莱雅", "price": 1},
        ],
        False,
    )


def test_build_cw_shop_scanner_reads_rapidocr_tuple_snapshot_fields(monkeypatch):
    shop_module = load_cw_shop_module()
    build_cw_shop_scanner = getattr(shop_module, "build_cw_shop_scanner", None)
    assert build_cw_shop_scanner is not None
    _install_fake_shop_batch_ocr(
        monkeypatch,
        shop_module,
        items=[[[0, 0], "黑塔", 0.9996806085109711], [[0, 0], "1", 0.9982701539993286]],
        coins=[[[0, 0], "40", 0.9982701539993286]],
    )

    class RuntimeStub:
        def ocr(self, *, capture):
            raise AssertionError(f"shop scanner should not call runtime.ocr: {capture}")

        def capture_image(self, *, from_x, from_y, to_x, to_y, normalize=True):
            del normalize
            capture = {"from_x": from_x, "from_y": from_y, "to_x": to_x, "to_y": to_y}
            if capture in _global_stage_status_regions():
                raise AssertionError(f"shop scanner should not capture global status: {capture}")
            return _image_for_region(capture)

    assert build_cw_shop_scanner(RuntimeStub())() == {
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 40,
        "reserve_full": False,
    }


def test_parse_shop_level_prefers_level_text_over_progress_counter():
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    raw_items = [
        [[0, 0], "购买经验", 0.9986591637134552],
        [[0, 0], "LV.", 0.9367905457814535],
        [[0, 0], "3", 0.9983842372894287],
        [[0, 0], "0/4", 0.9961388905843099],
    ]

    assert parse_shop_level(raw_items, default=None) == 3


def test_parse_shop_level_accepts_digit_before_lv_token_when_progress_counter_exists():
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    raw_items = [
        [[0, 0], "购买经验", 0.998764380812645],
        [[0, 0], "7", 0.9988219141960144],
        [[0, 0], "LV.", 0.8283075491587321],
        [[0, 0], "4/52", 0.994941234588623],
    ]

    assert parse_shop_level(raw_items, default=None) == 7


def test_parse_shop_level_does_not_treat_progress_counter_as_level():
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    raw_items = [
        [[0, 0], "LV.", 0.9367905457814535],
        [[0, 0], "0/4", 0.9961388905843099],
    ]

    assert parse_shop_level(raw_items, default=None) is None


@pytest.mark.parametrize(
    "raw_items",
    [
        [
            [[0, 0], "LV.0/4", 0.9961388905843099],
        ],
        [
            [[0, 0], "0", 0.9961388905843099],
        ],
    ],
)
def test_parse_shop_level_rejects_merged_or_orphan_progress_digits(raw_items):
    shop_module = load_cw_shop_module()
    parse_shop_level = getattr(shop_module, "_parse_shop_level", None)
    assert parse_shop_level is not None

    assert parse_shop_level(raw_items, default=None) is None


def test_shop_scan_guide_summary_treats_incomplete_legacy_guide_as_not_loaded(tmp_path):
    shop_module = load_cw_shop_module()
    open_cw_shop = getattr(shop_module, "open_cw_shop", None)
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    assert open_cw_shop is not None
    assert scan_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = {
        "artifact": "legacy-artifact",
        "share_code": "##demo##",
        "remaining_purchases": {"银狼": 9},
    }
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
        "reserve_full": False,
        "stale": False,
    }
    assert "guide_summary" not in refreshed.scene_state["cw"]["shop"]


def test_shop_status_drops_stale_guide_summary_when_current_guide_is_incomplete(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    shop_cw_status = getattr(shop_module, "shop_cw_status", None)
    assert scan_cw_shop is not None
    assert shop_cw_status is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    assert "guide_summary" in session.scene_state["cw"]["shop"]

    session.scene_state["cw"]["guide"] = {"share_code": "##demo##", "remaining_purchases": {"银狼": 9}}

    status = shop_cw_status(session)

    assert status["items"] == [{"name": "银狼", "price": 20}]
    assert "guide_summary" not in status


@pytest.mark.parametrize(
    ("action_name", "callback_name", "expected_opened"),
    [
        ("open_cw_shop", "opener", True),
        ("refresh_cw_shop", "refresher", None),
        ("close_cw_shop", "closer", False),
    ],
)
def test_shop_open_refresh_close_drop_stale_guide_summary_when_current_guide_is_incomplete(
    tmp_path,
    action_name,
    callback_name,
    expected_opened,
):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    action = getattr(shop_module, action_name, None)
    assert scan_cw_shop is not None
    assert action is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    assert "guide_summary" in session.scene_state["cw"]["shop"]
    session.scene_state["cw"]["guide"] = {"share_code": "##demo##", "remaining_purchases": {"银狼": 9}}

    calls: list[str] = []
    refreshed = action(session, **{callback_name: lambda: calls.append(action_name)})

    shop_state = refreshed.scene_state["cw"]["shop"]
    assert shop_state["stale"] is True
    if expected_opened is not None:
        assert shop_state["opened"] is expected_opened
    assert "guide_summary" not in shop_state
    assert calls == [action_name]


@pytest.mark.parametrize(
    ("action_name", "callback_name"),
    [
        ("open_cw_shop", "opener"),
        ("refresh_cw_shop", "refresher"),
        ("close_cw_shop", "closer"),
    ],
)
def test_shop_open_refresh_close_rebuild_existing_guide_summary_from_current_complete_guide(
    tmp_path,
    action_name,
    callback_name,
):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    action = getattr(shop_module, action_name, None)
    assert scan_cw_shop is not None
    assert action is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path, purchases={"银狼": 1})
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    session.scene_state["cw"]["shop"]["guide_summary"] = {
        "remaining_purchases": {"银狼": 99},
        "constraints": {"min_coins": 1},
    }
    session.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 8, "mid_level": 9}

    refreshed = action(session, **{callback_name: lambda: None})

    assert refreshed.scene_state["cw"]["shop"]["guide_summary"] == {
        "remaining_purchases": {"银狼": 1},
        "constraints": {"min_coins": 40, "min_level": 8, "mid_level": 9},
    }


def test_shop_status_returns_current_snapshot(tmp_path):
    shop_module = load_cw_shop_module()
    shop_cw_status = getattr(shop_module, "shop_cw_status", None)
    assert shop_cw_status is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7, "exp": "4/52"}}
    scanned = getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)

    assert shop_cw_status(scanned) == {
        **scanned.scene_state["cw"]["shop"],
        "stage_status": {"stale": False, "level": 7, "exp": "4/52"},
        "stage_status_stale": False,
    }


def test_project_cw_shop_snapshot_reports_stale_when_stage_status_missing(tmp_path):
    shop_module = load_cw_shop_module()
    project_cw_shop_snapshot = getattr(shop_module, "project_cw_shop_snapshot", None)
    assert project_cw_shop_snapshot is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["stage"] = {"status": None}
    getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)

    projected = project_cw_shop_snapshot(session)

    assert "stage_status" not in projected
    assert projected["stage_status_stale"] is True


def test_project_cw_shop_snapshot_filters_legacy_stage_fields_from_shop(tmp_path):
    shop_module = load_cw_shop_module()
    project_cw_shop_snapshot = getattr(shop_module, "project_cw_shop_snapshot", None)
    assert project_cw_shop_snapshot is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["shop"] = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "level": 99,
        "exp": "99/99",
        "team_size": "9/9",
        "role_count": {"total": 9},
        "reserve_full": False,
        "stale": False,
    }
    session.scene_state["cw"]["stage"] = {
        "status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3", "role_count": {"total": 3}}
    }

    projected = project_cw_shop_snapshot(session)

    assert projected["opened"] is True
    assert projected["items"] == [{"name": "黑塔", "price": 1}]
    assert projected["coins"] == 62
    assert projected["reserve_full"] is False
    assert projected["stale"] is False
    assert "level" not in projected
    assert "exp" not in projected
    assert "team_size" not in projected
    assert "role_count" not in projected
    assert projected["stage_status"] == {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3", "role_count": {"total": 3}}
    assert projected["stage_status_stale"] is False


def test_shop_status_without_selected_guide_filters_legacy_guide_summary(tmp_path):
    shop_module = load_cw_shop_module()
    shop_cw_status = getattr(shop_module, "shop_cw_status", None)
    assert shop_cw_status is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = None
    session.scene_state["cw"]["shop"] = {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "guide_summary": {
            "remaining_purchases": {"银狼": 4},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }

    assert shop_cw_status(session) == {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "reserve_full": False,
        "stage_status_stale": True,
        "stale": False,
    }


def test_shop_status_ignores_legacy_guide_remaining_purchases_in_summary(tmp_path):
    shop_module = load_cw_shop_module()
    shop_cw_status = getattr(shop_module, "shop_cw_status", None)
    assert shop_cw_status is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = _complete_guide_state()
    session.scene_state["cw"]["guide"]["remaining_purchases"] = {"银狼": 4}
    session.scene_state["cw"]["shop"] = {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "guide_summary": {"remaining_purchases": {"银狼": 4}},
        "stale": False,
    }

    assert shop_cw_status(session)["guide_summary"] == {
        "remaining_purchases": {"银狼": 4},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
    }


def test_shop_buy_slot_mutates_guide_purchase_state(tmp_path):
    shop_module = load_cw_shop_module()
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    before_guide = _complete_guide_state()
    session.scene_state["cw"]["guide"] = deepcopy(before_guide)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    session.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7, "role_count": {"total": 3}}}
    getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)
    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    expected_guide = deepcopy(before_guide)
    expected_guide["remaining_purchases"] = {"银狼": 0}
    assert refreshed.scene_state["cw"]["guide"] == expected_guide
    assert refreshed.scene_state["cw"]["shop"] == {
        "items": [{"name": "阮·梅", "price": 30}],
        "coins": 22,
        "reserve_full": False,
        "opened": True,
        "guide_summary": {
            "remaining_purchases": {"银狼": 0},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True
    assert refreshed.scene_state["cw"]["stage"]["status"] == {"stale": True, "level": 7, "role_count": {"total": 3}}


def test_shop_buy_slot_clears_sell_plan_and_marks_slots_stale(tmp_path):
    shop_module = load_cw_shop_module()
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    getattr(shop_module, "scan_cw_shop")(session, scanner=fake_shop_snapshot)
    session.scene_state["cw"]["sell_plan"] = {"items": [{"slot": 0, "name": "银狼"}]}

    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    assert refreshed.scene_state["cw"]["sell_plan"] == {}
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True


def test_shop_scan_includes_current_guide_remaining_purchases_in_summary(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    assert scan_cw_shop is not None

    from tests.conftest import build_fake_cw_session

    session = build_fake_cw_session(tmp_path)
    before_guide = _complete_guide_state()
    before_guide["remaining_purchases"] = {"银狼": 4}
    session.scene_state["cw"]["guide"] = deepcopy(before_guide)

    refreshed = scan_cw_shop(session, scanner=fake_shop_snapshot)

    assert refreshed.scene_state["cw"]["guide"] == before_guide
    assert refreshed.scene_state["cw"]["shop"]["guide_summary"] == {
        "remaining_purchases": {"银狼": 4},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
    }


def test_shop_buy_slot_decrements_current_guide_remaining_purchases_in_summary(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    before_guide = _complete_guide_state()
    before_guide["remaining_purchases"] = {"银狼": 4}
    session.scene_state["cw"]["guide"] = deepcopy(before_guide)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)

    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    expected_guide = deepcopy(before_guide)
    expected_guide["remaining_purchases"] = {"银狼": 3}
    assert refreshed.scene_state["cw"]["guide"] == expected_guide
    assert refreshed.scene_state["cw"]["shop"]["guide_summary"] == {
        "remaining_purchases": {"银狼": 3},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
    }


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
    assert "guide_summary" not in refreshed.scene_state["cw"]["shop"]
    assert refreshed.scene_state["cw"]["slots"]["stale"] is True


@pytest.mark.parametrize(
    "guide_state",
    [
        {"artifact": "legacy-artifact", "share_code": "##demo##", "remaining_purchases": {"银狼": 9}},
        {"share_code": "##demo##", "remaining_purchases": {"银狼": 9}},
    ],
    ids=["legacy-artifact", "incomplete-guide"],
)
def test_shop_buy_slot_legacy_or_incomplete_guide_omits_guide_summary(tmp_path, guide_state):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["guide"] = dict(guide_state)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)

    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=fake_shop_snapshot_after_purchase,
    )

    assert refreshed.scene_state["cw"]["guide"] == guide_state
    assert refreshed.scene_state["cw"]["shop"]["items"] == [{"name": "阮·梅", "price": 30}]
    assert "guide_summary" not in refreshed.scene_state["cw"]["shop"]


def test_shop_buy_slot_retries_confirmation_until_slot_changes(tmp_path, monkeypatch):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    before_guide = deepcopy(session.scene_state["cw"]["guide"])

    sleep_calls: list[float] = []
    snapshots = iter((fake_shop_snapshot(), fake_shop_snapshot_after_purchase()))

    monkeypatch.setattr(shop_module, "sleep", lambda seconds: sleep_calls.append(seconds))

    refreshed = buy_cw_shop_slot(
        session,
        slot=1,
        expect="银狼",
        buyer=fake_buy_success,
        scanner=lambda: next(snapshots),
    )

    expected_guide = deepcopy(before_guide)
    expected_guide["remaining_purchases"] = {"银狼": 0}
    assert refreshed.scene_state["cw"]["guide"] == expected_guide
    assert refreshed.scene_state["cw"]["shop"]["items"][0]["name"] == "阮·梅"
    assert refreshed.scene_state["cw"]["shop"]["items"][0]["price"] == 30
    assert sleep_calls == [shop_module.SHOP_BUY_CONFIRM_RETRY_SECONDS]


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

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    before_guide = deepcopy(session.scene_state["cw"]["guide"])

    with pytest.raises(TrailError) as exc_info:
        buy_cw_shop_slot(
            session,
            slot=slot,
            expect=expect,
            buyer=fake_buy_success,
            scanner=scanner,
        )

    assert exc_info.value.code == code
    assert session.scene_state["cw"]["guide"] == before_guide
    assert session.scene_state["cw"]["shop"]["items"] == [{"name": "银狼", "price": 20}]


def test_shop_buy_slot_rejects_other_slot_change_without_target_change(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    before_guide = deepcopy(session.scene_state["cw"]["guide"])
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
    assert session.scene_state["cw"]["guide"] == before_guide
    assert session.scene_state["cw"]["shop"] == before_shop


def test_shop_buy_slot_noop_failure_keeps_shop_snapshot_unchanged(tmp_path):
    shop_module = load_cw_shop_module()
    scan_cw_shop = getattr(shop_module, "scan_cw_shop", None)
    buy_cw_shop_slot = getattr(shop_module, "buy_cw_shop_slot", None)
    assert scan_cw_shop is not None
    assert buy_cw_shop_slot is not None

    from tests.conftest import build_fake_cw_session, fake_buy_success

    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["shop"] = {"opened": True, "stale": True}
    scan_cw_shop(session, scanner=fake_shop_snapshot)
    before_guide = deepcopy(session.scene_state["cw"]["guide"])
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
    assert session.scene_state["cw"]["guide"] == before_guide
    assert session.scene_state["cw"]["shop"] == before_shop


def test_buy_cw_shop_exp_updates_fresh_snapshot_and_marks_slots_stale(tmp_path: Path) -> None:
    shop_module = load_cw_shop_module()
    buy_cw_shop_exp = getattr(shop_module, "buy_cw_shop_exp", None)
    assert buy_cw_shop_exp is not None
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    cw_state = session.scene_state.setdefault("cw", {})
    cw_state["shop"] = {
        "opened": True,
        "stale": False,
        "items": [{"slot": 1, "name": "灵砂", "price": 3}],
        "coins": 40,
        "level": 3,
        "exp": "0/4",
        "team_size": "3/3",
    }
    cw_state["guide"] = _complete_guide_state(purchases={"灵砂": 1})
    cw_state["constraints"] = {"min_coins": 40, "min_level": 6, "mid_level": 9, "priority": {}, "positioning": {}}

    clicked = []

    def exp_buyer():
        clicked.append(True)

    def scanner():
        return {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "灵砂", "price": 3}],
            "coins": 36,
            "level": 4,
            "exp": "0/8",
            "reserve_full": False,
            "team_size": "4/4",
        }

    buy_cw_shop_exp(session, buyer=exp_buyer, scanner=scanner)

    assert clicked == [True]
    assert session.scene_state["cw"]["shop"]["coins"] == 36
    assert session.scene_state["cw"]["shop"]["level"] == 4
    assert session.scene_state["cw"]["shop"]["team_size"] == "4/4"
    assert session.scene_state["cw"]["shop"]["guide_summary"] == {
        "remaining_purchases": {"灵砂": 1},
        "constraints": {"min_coins": 40, "min_level": 6, "mid_level": 9},
    }
    assert session.scene_state["cw"]["slots"]["stale"] is True


def test_buy_cw_shop_exp_keeps_explicit_team_size_read_failure_as_null(tmp_path: Path) -> None:
    shop_module = load_cw_shop_module()
    buy_cw_shop_exp = getattr(shop_module, "buy_cw_shop_exp", None)
    assert buy_cw_shop_exp is not None
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    cw_state = session.scene_state.setdefault("cw", {})
    cw_state["shop"] = {
        "opened": True,
        "stale": False,
        "items": [],
        "coins": 40,
        "level": 3,
        "exp": "0/4",
        "team_size": "3/3",
    }

    buy_cw_shop_exp(
        session,
        buyer=lambda: None,
        scanner=lambda: {
            "opened": True,
            "stale": False,
            "items": [],
            "coins": 36,
            "level": 4,
            "exp": "0/8",
            "reserve_full": False,
            "team_size": None,
        },
    )

    assert session.scene_state["cw"]["shop"]["team_size"] is None


def test_buy_cw_shop_exp_treats_incomplete_guide_as_unloaded(tmp_path: Path) -> None:
    shop_module = load_cw_shop_module()
    buy_cw_shop_exp = getattr(shop_module, "buy_cw_shop_exp", None)
    assert buy_cw_shop_exp is not None
    session = SessionStore(tmp_path).create(window_binding={"title": "崩坏：星穹铁道"})
    cw_state = session.scene_state.setdefault("cw", {})
    cw_state["shop"] = {"opened": True, "stale": False, "items": [], "coins": 40, "level": 3, "exp": "0/4", "team_size": "3/3"}
    cw_state["guide"] = {"remaining_purchases": {"灵砂": 1}}
    cw_state["constraints"] = {"min_coins": 40, "min_level": 6, "mid_level": 9, "priority": {}, "positioning": {}}

    buy_cw_shop_exp(
        session,
        buyer=lambda: None,
        scanner=lambda: {
            "opened": True,
            "stale": False,
            "items": [],
            "coins": 36,
            "level": 4,
            "exp": "0/8",
            "reserve_full": False,
            "team_size": "4/4",
        },
    )

    assert "guide_summary" not in session.scene_state["cw"]["shop"]


def test_build_cw_shop_exp_buyer_clicks_exp_button_point(monkeypatch) -> None:
    shop_module = load_cw_shop_module()
    build_cw_shop_exp_buyer = getattr(shop_module, "build_cw_shop_exp_buyer", None)
    assert build_cw_shop_exp_buyer is not None
    expected_point = getattr(shop_module, "SHOP_EXP_BUY_POINT")
    sleep_calls: list[float] = []
    monkeypatch.setattr(shop_module, "sleep", lambda seconds: sleep_calls.append(seconds))

    class Runtime:
        def __init__(self):
            self.clicks: list[tuple[int, int]] = []

        def click_point(self, x: int, y: int, **kwargs):
            del kwargs
            self.clicks.append((x, y))

    runtime = Runtime()
    buyer = build_cw_shop_exp_buyer(runtime)
    buyer()

    assert runtime.clicks == [expected_point]
    assert sleep_calls == [shop_module.SHOP_BUY_CONFIRM_RETRY_SECONDS]


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


def test_cw_shop_scan_read_commands_persist_two_phase_snapshot(tmp_path: Path, monkeypatch):
    shop_module = load_cw_shop_module()
    _install_fake_shop_batch_ocr(monkeypatch, shop_module)
    runtime = _build_cw_shop_scan_runtime(shop_module)
    registry, service, session, cw_service, _ = _build_cw_harness(tmp_path, runtime=runtime)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_guide_state(purchases={"银狼": 2})
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = {"opened": False, "stale": True}
    loaded.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}}
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

    expected_shop_snapshot = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "reserve_full": False,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    expected_response = {
        **expected_shop_snapshot,
        "stage_status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
        "stage_status_stale": False,
    }

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == []
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]
    assert scanned == expected_response
    assert status == expected_response
    persisted_shop = service.load_session(session.session_id).scene_state["cw"]["shop"]
    assert persisted_shop == expected_shop_snapshot
    assert "level" not in persisted_shop
    assert "exp" not in persisted_shop
    assert "team_size" not in persisted_shop
    assert "role_count" not in persisted_shop


def test_cw_shop_scan_flows_through_command_service_mutation_journal_and_persists_snapshot(tmp_path: Path, monkeypatch):
    shop_module = load_cw_shop_module()
    _install_fake_shop_batch_ocr(monkeypatch, shop_module)
    runtime = _build_cw_shop_scan_runtime(shop_module)
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_guide_state(purchases={"银狼": 2})
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = {"opened": False, "stale": True}
    loaded.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}}
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

    expected_shop_snapshot = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "reserve_full": False,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    expected_response = {
        **expected_shop_snapshot,
        "stage_status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
        "stage_status_stale": False,
    }

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == []
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]
    assert envelope["ok"] is True
    assert envelope["data"] == expected_response
    assert status["final_state"] == "completed"
    assert persisted.scene_state["cw"]["shop"] == expected_shop_snapshot


def test_cw_shop_status_flows_through_command_service_projection(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = {"remaining_purchases": {"银狼": 2}}
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "level": 99,
        "exp": "99/99",
        "team_size": "9/9",
        "role_count": {"total": 9},
        "reserve_full": False,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    loaded.scene_state["cw"]["stage"] = {"status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}}
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-status",
        method="cw.shop.status",
        payload={},
    )
    persisted_shop = service.load_session(session.session_id).scene_state["cw"]["shop"]

    assert envelope["ok"] is True
    assert envelope["data"]["stage_status"] == {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"}
    assert envelope["data"]["stage_status_stale"] is False
    assert "level" not in envelope["data"]
    assert "exp" not in envelope["data"]
    assert "team_size" not in envelope["data"]
    assert "role_count" not in envelope["data"]
    assert "level" not in persisted_shop
    assert "exp" not in persisted_shop
    assert "team_size" not in persisted_shop
    assert "role_count" not in persisted_shop
    assert persisted_shop["guide_summary"] == {
        "remaining_purchases": {"银狼": 2},
        "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
    }


@pytest.mark.parametrize("missing_key", ["cw", "shop"])
def test_cw_shop_status_projects_when_cw_or_shop_state_missing(tmp_path: Path, missing_key: str):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    loaded = service.load_session(session.session_id)
    if missing_key == "cw":
        loaded.scene_state.pop("cw", None)
    else:
        loaded.scene_state.setdefault("cw", {}).pop("shop", None)
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id=f"req-cw-shop-status-missing-{missing_key}",
        method="cw.shop.status",
        payload={},
    )
    persisted_shop = service.load_session(session.session_id).scene_state["cw"]["shop"]

    assert envelope["ok"] is True
    assert envelope["data"]["stale"] is True
    assert envelope["data"]["stage_status_stale"] is True
    assert "level" not in envelope["data"]
    assert "exp" not in envelope["data"]
    assert "team_size" not in envelope["data"]
    assert "role_count" not in envelope["data"]
    assert persisted_shop == {"stale": True}


def test_cw_shop_status_recovers_corrupted_cw_state_to_shop_only_projection(tmp_path: Path):
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path)
    del registry, cw_service
    loaded = service.load_session(session.session_id)
    loaded.scene_state["cw"] = "corrupted"
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-status-corrupted-cw",
        method="cw.shop.status",
        payload={},
    )
    persisted_cw = service.load_session(session.session_id).scene_state["cw"]

    assert envelope["ok"] is True
    assert envelope["data"] == {"stale": True, "stage_status_stale": True}
    assert persisted_cw["shop"] == {"stale": True}


def test_cw_shop_scan_marks_applied_but_not_persisted_when_ocr_image_raises_after_open_click(
    tmp_path: Path,
):
    shop_module = load_cw_shop_module()
    runtime = _build_cw_shop_scan_runtime(
        shop_module,
        ocr_image_error=RuntimeError("batch OCR boom"),
    )
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["shop"] = {"opened": False, "stale": True}
    service.save_session(loaded)

    envelope = _run_cw_mutation(
        command_service=command_service,
        session=session,
        workspace_root=tmp_path,
        request_id="req-cw-shop-scan-ocr-image-fail",
        method="cw.shop.scan",
        payload={},
    )
    status = service.request_status("req-cw-shop-scan-ocr-image-fail")

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]
    assert runtime.ocr_image_calls
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "DAEMON_UNAVAILABLE",
        "message": "mutation result unknown",
    }
    assert envelope["debug"]["detail"] == "RuntimeError: batch OCR boom"
    assert envelope["debug"]["last_known_stage"] == "side_effect_applied"
    assert render_output("cw.shop.scan", envelope).splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-scan-ocr-image-fail",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-scan-ocr-image-fail",
    ]
    assert status["final_state"] == "applied_but_not_persisted"
    assert status["tainted"] is True


def test_cw_shop_scan_marks_applied_but_not_persisted_when_save_fails_after_clicks(
    tmp_path: Path,
    monkeypatch,
):
    shop_module = load_cw_shop_module()
    _install_fake_shop_batch_ocr(monkeypatch, shop_module)
    runtime = _build_cw_shop_scan_runtime(shop_module)
    registry, service, session, cw_service, command_service = _build_cw_harness(tmp_path, runtime=runtime)
    before_shop = {
        "opened": False,
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_guide_state(purchases={"银狼": 2})
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
    assert runtime.ocr_calls == []
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]
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
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_guide_state(purchases={"银狼": 2})
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
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_guide_state(purchases={"银狼": 2})
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
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_guide_state(purchases={"银狼": 2})
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
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_guide_state(purchases={"银狼": 2})
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


def test_cw_shop_scan_ignores_metadata_boom_after_persist(
    tmp_path: Path,
    monkeypatch,
):
    shop_module = load_cw_shop_module()
    _install_fake_shop_batch_ocr(monkeypatch, shop_module)
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
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        "stale": True,
    }
    loaded = service.load_session(session.session_id)
    loaded.scene_state.setdefault("cw", {})["guide"] = _complete_guide_state(purchases={"银狼": 2})
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
    expected_shop_snapshot = {
        "opened": True,
        "items": [{"name": "黑塔", "price": 1}],
        "coins": 62,
        "reserve_full": False,
        "guide_summary": {
            "remaining_purchases": {"银狼": 2},
            "constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7},
        },
        "stale": False,
    }
    expected_response = {**expected_shop_snapshot, "stage_status_stale": True}

    assert runtime.clicks == [shop_module.SHOP_SCAN_RESET_POINT, shop_module.SHOP_OPEN_POINT]
    assert runtime.ocr_calls == []
    assert runtime.capture_image_calls == [shop_module.SHOP_SCAN_REGION, shop_module.SHOP_COINS_REGION]
    assert envelope["ok"] is True
    assert envelope["data"] == expected_response
    assert envelope["warnings"] == []
    assert envelope["screenshot"] == ".trail/shots/req-cw-shop-scan-metadata-boom.png"
    assert envelope["references"] == [
        {
            "path": "trail/scenes/cw/references/shop.png",
            "similarity": 0.97,
            "screenshot": ".trail/shots/req-cw-shop-scan-metadata-boom.png",
        }
    ]
    assert status["final_state"] == "completed"
    assert status["tainted"] is False
    assert persisted.scene_state["cw"]["shop"] == expected_shop_snapshot


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
    before_guide = _complete_guide_state(purchases={"银狼": 1})
    loaded.scene_state.setdefault("cw", {})["guide"] = deepcopy(before_guide)
    loaded.scene_state["cw"]["constraints"] = {"min_coins": 40, "min_level": 7, "mid_level": 7}
    loaded.scene_state["cw"]["shop"] = {
        "items": [{"name": "银狼", "price": 20}],
        "coins": 40,
        "level": 7,
        "exp": "4/52",
        "reserve_full": False,
        "team_size": "7/7",
        "opened": True,
        "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
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
    assert persisted.scene_state["cw"]["guide"] == before_guide
    assert persisted.scene_state["cw"]["shop"]["items"] == [{"name": "银狼", "price": 20}]
