from __future__ import annotations

from PIL import Image
import pytest

from trail.core.errors import TrailError
from trail.runtime.batch_ocr import BatchOcrTarget, pack_batch_ocr_targets, run_batch_ocr


def _image(width: int, height: int) -> Image.Image:
    return Image.new("RGB", (width, height), color="white")


def _piece_box_values(piece):
    box = piece["box"] if isinstance(piece, dict) else piece.box
    if isinstance(box, dict):
        return {key: box[key] for key in ("left", "top", "width", "height")}
    return {key: getattr(box, key) for key in ("left", "top", "width", "height")}


def test_rectangle_packer_import_and_minimal_pack_smoke():
    import rpack

    assert len(rpack.pack([(10, 10), (20, 5)])) == 2


def test_pack_batch_ocr_targets_validates_inputs():
    with pytest.raises(TrailError) as empty:
        pack_batch_ocr_targets([])
    assert empty.value.code == "BATCH_OCR_EMPTY_TARGETS"

    with pytest.raises(TrailError) as duplicate:
        pack_batch_ocr_targets([BatchOcrTarget("x", _image(1, 1)), BatchOcrTarget("x", _image(2, 2))])
    assert duplicate.value.code == "BATCH_OCR_DUPLICATE_KEY"

    with pytest.raises(TrailError) as invalid:
        pack_batch_ocr_targets([BatchOcrTarget("bad", Image.new("RGB", (0, 10)))])
    assert invalid.value.code == "BATCH_OCR_INVALID_TARGET_IMAGE"

    with pytest.raises(TrailError) as non_image:
        pack_batch_ocr_targets([BatchOcrTarget("bad", object())])
    assert non_image.value.code == "BATCH_OCR_INVALID_TARGET_IMAGE"


def test_pack_batch_ocr_targets_keeps_key_order_and_content_rects():
    targets = [
        BatchOcrTarget("items", _image(900, 70), padding=4),
        BatchOcrTarget("coins", _image(90, 60), padding=4),
        BatchOcrTarget("level", _image(140, 70), padding=4),
    ]

    atlas, packed = pack_batch_ocr_targets(targets, gap=24)

    assert [item.key for item in packed] == [target.key for target in targets]
    assert atlas.width > 0 and atlas.height > 0
    assert 1.0 < atlas.width / atlas.height < 12.0
    for target, item in zip(targets, packed, strict=True):
        assert item.content_rect["width"] == target.image.width
        assert item.content_rect["height"] == target.image.height
        assert item.rect["width"] == target.image.width + 2 * target.padding
        assert item.rect["height"] == target.image.height + 2 * target.padding


def test_pack_batch_ocr_targets_rects_do_not_overlap_and_atlas_covers_all_rects():
    targets = [BatchOcrTarget(idx, _image(40 + idx * 10, 20 + idx * 5), padding=3) for idx in range(5)]

    atlas, packed = pack_batch_ocr_targets(targets, gap=12)

    for item in packed:
        assert 0 <= item.rect["left"] < item.rect["right"] <= atlas.width
        assert 0 <= item.rect["top"] < item.rect["bottom"] <= atlas.height
    for left_index, left in enumerate(packed):
        for right in packed[left_index + 1 :]:
            assert (
                left.rect["right"] <= right.rect["left"]
                or right.rect["right"] <= left.rect["left"]
                or left.rect["bottom"] <= right.rect["top"]
                or right.rect["bottom"] <= left.rect["top"]
            )


def test_pack_batch_ocr_targets_impossible_constraints_raise_pack_failed():
    with pytest.raises(TrailError) as exc_info:
        pack_batch_ocr_targets([BatchOcrTarget("wide", _image(100, 10))], max_width=50)

    assert exc_info.value.code == "BATCH_OCR_PACK_FAILED"


class RuntimeSpy:
    def __init__(self, pieces=None, error: Exception | None = None):
        self.pieces = pieces or []
        self.error = error
        self.ocr_image_calls = []
        self.trace = []

    def ocr_image(self, image, *, ocr=None):
        self.ocr_image_calls.append({"size": image.size, "ocr": ocr})
        if self.error is not None:
            raise self.error
        return list(self.pieces)

    def _begin_debug_action(self, step: str, **payload):
        event = {"step": step, **payload}
        self.trace.append(event)

        class Action:
            def finish(_, *, ok: bool, **finish_payload):
                event.update(finish_payload)
                event["ok"] = 1 if ok else 0
                event["dur_ms"] = 0
                event["ts"] = "2026-04-26T00:00:00.000Z"

        return Action()

    def _finish_debug_action(self, action, *, ok: bool, **payload):
        action.finish(ok=ok, **payload)


def test_run_batch_ocr_returns_target_local_rapidocr_tuple_coordinates():
    target = BatchOcrTarget("items", _image(100, 40), padding=4)
    _, [packed] = pack_batch_ocr_targets([target])
    x = packed.content_rect["left"]
    y = packed.content_rect["top"]
    runtime = RuntimeSpy([([(x + 10, y + 5), (x + 60, y + 5), (x + 60, y + 20), (x + 10, y + 20)], "黑塔", 0.99)])

    result = run_batch_ocr(runtime, [target])

    assert result.by_key["items"].text == "黑塔"
    assert result.by_key["items"].pieces == [([(10, 5), (60, 5), (60, 20), (10, 20)], "黑塔", 0.99)]
    assert runtime.ocr_image_calls[0]["ocr"].ocr_mode == "high"
    assert runtime.ocr_image_calls[0]["ocr"].retry_high == "never"


def test_run_batch_ocr_drops_gap_missing_box_and_empty_text():
    targets = [BatchOcrTarget("left", _image(60, 20)), BatchOcrTarget("right", _image(60, 20))]
    _, packed = pack_batch_ocr_targets(targets, gap=80)
    left_rect = packed[0].content_rect
    gap_x = packed[0].rect["right"] + 10
    runtime = RuntimeSpy([
        {"text": "左", "box": {"left": left_rect["left"] + 1, "top": left_rect["top"] + 1, "width": 10, "height": 10}},
        {"text": "噪声", "box": {"left": gap_x, "top": left_rect["top"], "width": 10, "height": 10}},
        {"text": "无框"},
        {"text": ""},
    ])

    result = run_batch_ocr(runtime, targets, gap=80)

    assert result.by_key["left"].text == "左"
    assert result.by_key["right"].text is None
    assert [drop["reason"] for drop in result.dropped] == ["outside_target", "missing_box", "empty_text"]
    assert runtime.trace[0]["step"] == "batch_ocr"
    assert runtime.trace[0]["ok"] == 1
    assert runtime.trace[0]["drop_count"] == 3


def test_run_batch_ocr_supports_dict_boxes_half_open_boundaries_and_stable_sorting():
    target = BatchOcrTarget("one", _image(80, 40), padding=4)
    _, [packed] = pack_batch_ocr_targets([target])
    x = packed.content_rect["left"]
    y = packed.content_rect["top"]
    runtime = RuntimeSpy([
        {"text": "儿", "box": {"left": x + 30, "top": y + 10, "width": 10, "height": 10}},
        {"text": "希", "box": {"left": x + 10, "top": y + 10, "width": 10, "height": 10}},
        {"text": "界外", "box": {"left": packed.content_rect["right"], "top": y + 1, "width": 10, "height": 10}},
    ])

    result = run_batch_ocr(runtime, [target])

    assert result.by_key["one"].text == "希儿"
    assert [drop["reason"] for drop in result.dropped] == ["outside_target"]


def test_run_batch_ocr_assigns_missing_box_to_single_target():
    runtime = RuntimeSpy([{"text": "希"}, {"text": "儿"}])

    result = run_batch_ocr(runtime, [BatchOcrTarget("one", _image(80, 40))])

    assert result.by_key["one"].text == "希儿"


def test_run_batch_ocr_drops_piece_crossing_two_targets():
    targets = [BatchOcrTarget("left", _image(30, 20)), BatchOcrTarget("right", _image(30, 20))]
    _, packed = pack_batch_ocr_targets(targets, gap=0)
    left = packed[0].content_rect
    right = packed[1].content_rect
    runtime = RuntimeSpy([
        {"text": "跨界", "box": {"left": left["right"] - 5, "top": left["top"], "width": right["left"] - left["right"] + 10, "height": 10}}
    ])

    result = run_batch_ocr(runtime, targets, gap=0)

    assert result.by_key["left"].text is None
    assert result.by_key["right"].text is None
    assert result.dropped[0]["reason"] == "cross_target_boundary"


def test_pack_batch_ocr_targets_candidate_scoring_avoids_extreme_single_column():
    targets = [BatchOcrTarget(str(index), _image(80, 40), padding=4) for index in range(8)]

    atlas, _ = pack_batch_ocr_targets(targets, gap=12, target_aspect=16 / 9)

    assert atlas.height < 8 * (40 + 2 * 4 + 12)
    assert 0.4 < atlas.width / atlas.height < 6.0


def test_run_batch_ocr_returns_shifted_representation_when_object_piece_is_read_only():
    class ImmutableBox:
        __slots__ = ("_left", "_top", "_width", "_height")

        def __init__(self, left: int, top: int, width: int, height: int):
            self._left = left
            self._top = top
            self._width = width
            self._height = height

        @property
        def left(self):
            return self._left

        @property
        def top(self):
            return self._top

        @property
        def width(self):
            return self._width

        @property
        def height(self):
            return self._height

    class ReadOnlyPiece:
        __slots__ = ("text", "_box")

        def __init__(self, text: str, left: int, top: int, width: int, height: int):
            self.text = text
            self._box = ImmutableBox(left, top, width, height)

        @property
        def box(self):
            return self._box

    target = BatchOcrTarget("immutable", _image(60, 30), padding=4)
    _, [packed] = pack_batch_ocr_targets([target])
    piece = ReadOnlyPiece("只读", packed.content_rect["left"] + 7, packed.content_rect["top"] + 9, 10, 8)

    result = run_batch_ocr(RuntimeSpy([piece]), [target])

    [shifted_piece] = result.by_key["immutable"].pieces
    assert shifted_piece is not piece
    assert _piece_box_values(shifted_piece) == {"left": 7, "top": 9, "width": 10, "height": 8}
    assert piece.box.left == packed.content_rect["left"] + 7
    assert piece.box.top == packed.content_rect["top"] + 9


def test_run_batch_ocr_ignores_non_numeric_coordinate_aliases_when_shifting_boxes():
    class AliasBox:
        def __init__(self, left: int, top: int, width: int, height: int):
            self.left = left
            self.top = top
            self.width = width
            self.height = height
            self.right = None

    class AliasPiece:
        def __init__(self, text: str, left: int, top: int, width: int, height: int):
            self.text = text
            self.box = AliasBox(left, top, width, height)

    target = BatchOcrTarget("alias", _image(80, 40), padding=4)
    _, [packed] = pack_batch_ocr_targets([target])
    x = packed.content_rect["left"]
    y = packed.content_rect["top"]
    runtime = RuntimeSpy([
        {"text": "字", "box": {"left": x + 6, "top": y + 5, "width": 10, "height": 8, "right": None}},
        AliasPiece("段", x + 22, y + 7, 12, 9),
    ])

    result = run_batch_ocr(runtime, [target])

    assert result.by_key["alias"].text == "字段"
    assert [_piece_box_values(piece) for piece in result.by_key["alias"].pieces] == [
        {"left": 6, "top": 5, "width": 10, "height": 8},
        {"left": 22, "top": 7, "width": 12, "height": 9},
    ]


def test_run_batch_ocr_supports_object_style_box_and_out_of_atlas_drop():
    class Box:
        def __init__(self, left: int, top: int, width: int, height: int):
            self.left = left
            self.top = top
            self.width = width
            self.height = height

    class BoxPiece:
        def __init__(self, text: str, left: int, top: int, width: int, height: int):
            self.text = text
            self.box = Box(left, top, width, height)

    target = BatchOcrTarget("box", _image(60, 30), padding=4)
    _, [packed] = pack_batch_ocr_targets([target])
    runtime = RuntimeSpy([
        BoxPiece("内", packed.content_rect["left"] + 5, packed.content_rect["top"] + 5, 10, 10),
        BoxPiece("外", packed.content_rect["right"] + 100, packed.content_rect["bottom"] + 100, 10, 10),
    ])

    result = run_batch_ocr(runtime, [target])

    assert result.by_key["box"].text == "内"
    assert result.by_key["box"].pieces[0].box.left == 5
    assert result.by_key["box"].pieces[0].box.top == 5
    assert result.by_key["box"].pieces[0].box.width == 10
    assert result.by_key["box"].pieces[0].box.height == 10
    assert result.dropped[0]["reason"] == "outside_target"


def test_run_batch_ocr_reraises_ocr_error_and_records_failed_action():
    runtime = RuntimeSpy(error=RuntimeError("ocr boom"))

    with pytest.raises(RuntimeError, match="ocr boom"):
        run_batch_ocr(runtime, [BatchOcrTarget("front", _image(50, 20))], trace_prefix="cw_slots_batch_ocr")

    assert runtime.trace[0]["step"] == "cw_slots_batch_ocr"
    assert runtime.trace[0]["ok"] == 0
