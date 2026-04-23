from __future__ import annotations

from collections.abc import Mapping
import re
from time import sleep

from trail.core.errors import TrailError
from trail.runtime.ocr_config import OcrRequestConfig
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import STAGE_RESOURCE_ALIASES, _detect_cw_stage_from_ocr
from trail.session.models import SessionModel


CW_WIDTH = 1920
CW_HEIGHT = 1080
ENTRY_UI_WAIT_TIMEOUT = 10
ENTRY_GUIDE_HOTKEY = "f4"
ENTRY_GUIDE_OPEN_SETTLE_SECONDS = 2.0
ENTRY_COSMIC_STRIFE_SETTLE_SECONDS = 1.0
ENTRY_CURRENCY_WARS_SETTLE_SECONDS = 0.8
ENTRY_PARTICIPATE_SETTLE_SECONDS = 1.0
CURRENCY_WARS_ENTRY_POINT = (int(CW_WIDTH * 0.242), int(CW_HEIGHT * 0.30))
CURRENCY_WARS_PARTICIPATE_POINT = (int(CW_WIDTH * 0.7786), int(CW_HEIGHT * 0.8194))
STANDARD_BATTLE_MODE_POINT = (int(CW_WIDTH * 0.15625), int(CW_HEIGHT * 0.2315))
OVERCLOCK_BATTLE_MODE_POINT = (int(CW_WIDTH * 0.15625), int(CW_HEIGHT * 0.4167))
HOME_UNFINISHED_PROGRESS_PRIMARY = "继续进度"
HOME_UNFINISHED_PROGRESS_SECONDARY = ("结束并结算", "当前进度")
SETTLEMENT_CONTINUE_PRIMARY = ("挑战成功", "挑战失败")
SETTLEMENT_CONTINUE_NEXT = ("下一步", "下一页")
SETTLEMENT_CONTINUE_RETURN = "返回货币战争"
SETTLEMENT_CONTINUE_POINT = (960, 908)
SETTLEMENT_CONTINUE_SETTLE_SECONDS = 1.0
ENTRY_ENEMY_DIFFICULTY_REGION = {"from_x": 480, "from_y": 940, "to_x": 590, "to_y": 1005}
ENTRY_EXACT_DIFFICULTY_PATTERN = re.compile(r"^A(?P<rank>[0-8])-(?P<layer>[1-9]\d*)$")
ENTRY_DIFFICULTY_SETTLE_SECONDS = 0.5
ENTRY_DIFFICULTY_COARSE_THRESHOLD = 10
ENTRY_DIFFICULTY_COARSE_MAX_STEPS = 20
ENTRY_DIFFICULTY_FINE_MAX_STEPS = 12
ENTRY_DIFFICULTY_COARSE_START = (CW_WIDTH // 2, int(CW_HEIGHT * 0.75))
ENTRY_DIFFICULTY_COARSE_END = (CW_WIDTH // 2, 0)
ENTRY_RANK_BANDS = (
    {"rank_code": "A0", "rank_name": "黑铁", "max_layer": 3, "start_difficulty": 1, "ordinal_start": 1},
    {"rank_code": "A1", "rank_name": "青铜", "max_layer": 3, "start_difficulty": 6, "ordinal_start": 4},
    {"rank_code": "A2", "rank_name": "翠钢", "max_layer": 3, "start_difficulty": 11, "ordinal_start": 7},
    {"rank_code": "A3", "rank_name": "钴银", "max_layer": 5, "start_difficulty": 16, "ordinal_start": 10},
    {"rank_code": "A4", "rank_name": "冰钛", "max_layer": 5, "start_difficulty": 23, "ordinal_start": 15},
    {"rank_code": "A5", "rank_name": "紫金", "max_layer": 7, "start_difficulty": 30, "ordinal_start": 20},
    {"rank_code": "A6", "rank_name": "投资大师", "max_layer": 7, "start_difficulty": 39, "ordinal_start": 27},
    {"rank_code": "A7", "rank_name": "资本帝王", "max_layer": 9, "start_difficulty": 49, "ordinal_start": 34},
    {
        "rank_code": "A8",
        "rank_name": "财富造物主",
        "max_layer": 40,
        "start_difficulty": 61,
        "ordinal_start": 43,
        "difficulty_values": tuple(range(61, 71)) + tuple(range(74, 84)) + tuple(range(87, 97)) + tuple(range(99, 109)),
    },
)


class CwEnterStateError(TrailError):
    def __init__(self, *, page: str, stage: str | None = None):
        message = f"cw enter only supports world or home, current page: {page}"
        if stage is not None:
            message += f", stage: {stage}"
        super().__init__("CW_ENTER_ALREADY_PAST_HOME", message)
        self.data = {"page": page}
        if stage is not None:
            self.data["stage"] = stage


class CwStartStateError(TrailError):
    def __init__(self, *, page: str, stage: str | None = None):
        message = f"cw start only supports home, pre-invest pages, or invest, current page: {page}"
        if stage is not None:
            message += f", stage: {stage}"
        super().__init__("CW_START_PAGE_INVALID", message)
        self.data = {"page": page}
        if stage is not None:
            self.data["stage"] = stage


class CwStartEntryConflictError(TrailError):
    def __init__(self, *, field_name: str, recorded: str, requested: str):
        super().__init__("CW_START_ENTRY_CONFLICT", f"cw start conflicts with recorded {field_name}: {recorded} != {requested}")


class CwStartEntryTruthRequiredError(TrailError):
    def __init__(self, *, page: str, fields: tuple[str, ...]):
        joined = "/".join(fields)
        super().__init__("CW_START_ENTRY_TRUTH_REQUIRED", f"cw start requires recorded {joined} before continuing from {page}")


class CwStartProgressPendingError(TrailError):
    def __init__(self, *, after_start_click: bool = False):
        super().__init__(
            "CW_START_PROGRESS_PENDING",
            "cw start found unfinished home progress; ask whether to continue progress or end and settle before starting a new run",
        )
        self.data = {"page": "home"}
        self.completed_after_side_effect = after_start_click


class CwStartContinuePageError(TrailError):
    def __init__(self, *, page: str, stage: str | None = None):
        message = f"cw start --mode continue only supports whole-run settlement pages, current page: {page}"
        if stage is not None:
            message += f", stage: {stage}"
        super().__init__("CW_START_CONTINUE_PAGE_INVALID", message)
        self.data = {"page": page}
        if stage is not None:
            self.data["stage"] = stage


class CwStartNewPageError(TrailError):
    def __init__(self, *, page: str, stage: str | None = None):
        message = f"cw start --mode new only supports the true cw homepage or pre-invest pages, current page: {page}"
        if stage is not None:
            message += f", stage: {stage}"
        super().__init__("CW_START_NEW_PAGE_INVALID", message)
        self.data = {"page": page}
        if stage is not None:
            self.data["stage"] = stage


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _box_center(box: object) -> tuple[int, int]:
    if hasattr(box, "center"):
        center = getattr(box, "center")
        if isinstance(center, tuple) and len(center) == 2:
            return int(center[0]), int(center[1])

    if isinstance(box, Mapping):
        try:
            left = int(box["left"])
            top = int(box["top"])
            width = int(box["width"])
            height = int(box["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TrailError("CW_ENTRY_UI_INVALID", "货币战争进入链返回的坐标框无效") from exc
        return left + width // 2, top + height // 2

    raise TrailError("CW_ENTRY_UI_INVALID", "货币战争进入链返回的坐标框无效")


def _click_box_center(runtime, box: object) -> None:
    runtime.click_point(*_box_center(box))


def _transition_sleep(seconds: float) -> None:
    sleep(seconds)


def _locate(runtime, alias: str):
    return runtime.locate(_asset(alias))


def _wait(runtime, alias: str):
    box = runtime.wait_img(_asset(alias), timeout=ENTRY_UI_WAIT_TIMEOUT)
    if box is None:
        raise TrailError("CW_ENTRY_UI_NOT_FOUND", f"未识别到货币战争界面元素: {alias}")
    return box


def _extract_ocr_texts(ocr_result: object) -> list[str]:
    if not isinstance(ocr_result, (list, tuple)):
        return []
    texts: list[str] = []
    for piece in ocr_result:
        if isinstance(piece, Mapping):
            text = str(piece.get("text") or piece.get("ocr_text") or "").strip()
        elif isinstance(piece, (list, tuple)) and len(piece) >= 2 and isinstance(piece[1], str):
            text = piece[1].strip()
        else:
            continue
        if text:
            texts.append("".join(text.split()))
    return texts


def _find_entry_rank_band(rank_code: str) -> Mapping[str, object] | None:
    for band in ENTRY_RANK_BANDS:
        if band["rank_code"] == rank_code:
            return band
    return None


def _band_difficulty_values(band: Mapping[str, object]) -> tuple[int, ...]:
    values = band.get("difficulty_values")
    if isinstance(values, (list, tuple)):
        return tuple(int(value) for value in values)
    start_difficulty = int(band["start_difficulty"])
    max_layer = int(band["max_layer"])
    return tuple(range(start_difficulty, start_difficulty + max_layer))


def parse_cw_start_difficulty_token(value: str) -> dict[str, object] | None:
    if value in {"lowest", "current", "highest"}:
        return {"kind": "preset", "token": value}

    match = ENTRY_EXACT_DIFFICULTY_PATTERN.fullmatch(value)
    if match is None:
        return None

    rank_code = f"A{match.group('rank')}"
    layer = int(match.group("layer"))
    band = _find_entry_rank_band(rank_code)
    if band is None:
        return None

    difficulty_values = _band_difficulty_values(band)
    max_layer = len(difficulty_values)
    if layer < 1 or layer > max_layer:
        return None

    ordinal_start = int(band["ordinal_start"])
    return {
        "kind": "exact",
        "token": value,
        "rank_code": rank_code,
        "rank_name": band["rank_name"],
        "layer": layer,
        "target_enemy_difficulty": difficulty_values[layer - 1],
        "global_layer_ordinal": ordinal_start + layer - 1,
    }


def resolve_entry_rank_from_enemy_difficulty(value: int) -> dict[str, object] | None:
    for band in ENTRY_RANK_BANDS:
        difficulty_values = _band_difficulty_values(band)
        if value not in difficulty_values:
            continue

        layer = difficulty_values.index(value) + 1
        return {
            "token": f"{band['rank_code']}-{layer}",
            "rank_code": band["rank_code"],
            "rank_name": band["rank_name"],
            "layer": layer,
            "enemy_difficulty": value,
            "global_layer_ordinal": int(band["ordinal_start"]) + layer - 1,
        }
    return None


def _require_entry_rank_token(token: str) -> Mapping[str, object]:
    parsed = parse_cw_start_difficulty_token(token)
    if not isinstance(parsed, Mapping) or parsed.get("kind") != "exact":
        raise TrailError("CW_ENTRY_DIFFICULTY_INVALID", f"不支持的货币战争难度锚点: {token}")
    return parsed


def _extract_box_values(box: Mapping[object, object]) -> tuple[int, int, int, int] | None:
    try:
        return int(box["left"]), int(box["top"]), int(box["width"]), int(box["height"])
    except (KeyError, TypeError, ValueError):
        return None


def _extract_box_from_polygon(polygon: object) -> tuple[int, int, int, int] | None:
    if not isinstance(polygon, (list, tuple)):
        return None

    xs: list[int] = []
    ys: list[int] = []
    for point in polygon:
        try:
            if isinstance(point, Mapping):
                xs.append(int(point["x"]))
                ys.append(int(point["y"]))
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                xs.append(int(point[0]))
                ys.append(int(point[1]))
            else:
                return None
        except (KeyError, TypeError, ValueError):
            return None

    if not xs or not ys:
        return None

    left = min(xs)
    top = min(ys)
    return left, top, max(xs) - left, max(ys) - top


def _extract_center_box(center: object) -> tuple[int, int, int, int] | None:
    if isinstance(center, Mapping):
        try:
            return int(center["x"]), int(center["y"]), 0, 0
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        try:
            return int(center[0]), int(center[1]), 0, 0
        except (TypeError, ValueError):
            return None
    return None


def _extract_box(piece: object) -> tuple[int, int, int, int] | None:
    if isinstance(piece, Mapping):
        box = piece.get("box")
        if isinstance(box, Mapping):
            values = _extract_box_values(box)
            if values is not None:
                return values
        elif all(hasattr(box, attr) for attr in ("left", "top", "width", "height")):
            return int(box.left), int(box.top), int(box.width), int(box.height)

        polygon = piece.get("polygon") or piece.get("points")
        if polygon is not None:
            values = _extract_box_from_polygon(polygon)
            if values is not None:
                return values

        center = piece.get("center")
        values = _extract_center_box(center)
        if values is not None:
            return values

        values = _extract_box_values(piece)
        if values is not None:
            return values

    if isinstance(piece, (list, tuple)) and piece:
        values = _extract_box_from_polygon(piece[0])
        if values is not None:
            return values

    return None


def _read_ocr_piece(piece: object) -> str:
    if isinstance(piece, Mapping):
        return str(piece.get("text") or piece.get("ocr_text") or "").strip()
    if isinstance(piece, (list, tuple)) and len(piece) >= 2 and isinstance(piece[1], str):
        return piece[1].strip()
    return ""


def _boxes_overlap(left_box: tuple[int, int, int, int], right_box: tuple[int, int, int, int]) -> bool:
    left_a, top_a, width_a, height_a = left_box
    left_b, top_b, width_b, height_b = right_box
    right_a = left_a + width_a
    bottom_a = top_a + height_a
    right_b = left_b + width_b
    bottom_b = top_b + height_b
    return left_a < right_b and left_b < right_a and top_a < bottom_b and top_b < bottom_a


def _build_difficulty_recovery_error(
    *,
    requested_difficulty: str | None,
    reason: str,
    target_enemy_difficulty: int | None = None,
    current_enemy_difficulty: int | None = None,
    after_input: bool = False,
) -> TrailError:
    error = TrailError(
        "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
        "cw start cannot safely continue difficulty selection; ask agent to enter error recovery",
    )
    error.data = {
        "requested_difficulty": requested_difficulty,
        "target_enemy_difficulty": target_enemy_difficulty,
        "current_enemy_difficulty": current_enemy_difficulty,
        "reason": reason,
        "page": "entry.new",
    }
    error.tainted = False
    if after_input:
        error.known_failure_after_save = True
        error.completed_after_side_effect = True
    return error


def read_entry_enemy_difficulty(
    runtime,
    *,
    requested_difficulty: str | None = None,
    target_enemy_difficulty: int | None = None,
    after_input: bool = False,
) -> int:
    pieces = runtime.ocr(
        capture=ENTRY_ENEMY_DIFFICULTY_REGION,
        ocr=OcrRequestConfig(ocr_mode="high", retry_high="never"),
    )
    digit_runs: list[tuple[int, int, int, int, int, str]] = []
    for order, piece in enumerate(pieces if isinstance(pieces, (list, tuple)) else []):
        box = _extract_box(piece)
        if box is None:
            continue

        text = _read_ocr_piece(piece)
        digits = "".join(re.findall(r"\d+", text))
        if not digits:
            continue
        digit_runs.append((box[0], box[1], box[2], box[3], order, digits))

    if not digit_runs:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            reason="ocr_missing",
            after_input=after_input,
        )

    for index, current in enumerate(digit_runs):
        current_box = (current[0], current[1], current[2], current[3])
        for other in digit_runs[index + 1 :]:
            other_box = (other[0], other[1], other[2], other[3])
            if _boxes_overlap(current_box, other_box):
                raise _build_difficulty_recovery_error(
                    requested_difficulty=requested_difficulty,
                    target_enemy_difficulty=target_enemy_difficulty,
                    reason="ocr_conflict",
                    after_input=after_input,
                )

    current_enemy_difficulty = int("".join(part[5] for part in sorted(digit_runs, key=lambda part: (part[0], part[1], part[4]))))
    if resolve_entry_rank_from_enemy_difficulty(current_enemy_difficulty) is None:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            current_enemy_difficulty=current_enemy_difficulty,
            reason="ocr_unmapped",
            after_input=after_input,
        )
    return current_enemy_difficulty


def _home_has_unfinished_progress(runtime) -> bool:
    try:
        texts = _extract_ocr_texts(runtime.ocr())
    except Exception:
        return False
    joined = "".join(texts)
    return HOME_UNFINISHED_PROGRESS_PRIMARY in joined and any(
        keyword in joined for keyword in HOME_UNFINISHED_PROGRESS_SECONDARY
    )


def _detect_continue_settlement_page(runtime) -> dict[str, str] | None:
    try:
        texts = _extract_ocr_texts(runtime.ocr())
    except Exception:
        return None
    joined = "".join(texts)
    if SETTLEMENT_CONTINUE_RETURN in joined:
        return {"page": "settlement.return"}
    if any(keyword in joined for keyword in SETTLEMENT_CONTINUE_PRIMARY) and "下一步" in joined:
        return {"page": "settlement.entry"}
    if "下一页" in joined and any(keyword in joined for keyword in ("奖励", "对局评价", "小队生命值", "总经济", "标准博弈", "对局未完成")):
        return {"page": "settlement.followup"}
    return None


def _invalidate_stage(session: SessionModel) -> None:
    ensure_cw_state(session)["stage"] = {"stale": True}
    session.last_stage = None


def _invalidate_entry_snapshots(session: SessionModel) -> None:
    cw_state = ensure_cw_state(session)
    cw_state["slots"] = {**cw_state.get("slots", {}), "stale": True}
    cw_state["shop"] = {**cw_state.get("shop", {}), "stale": True}
    cw_state["sell_plan"] = {"stale": True}
    cw_state["portal"] = {**cw_state.get("portal", {}), "stale": True}
    _invalidate_stage(session)


def _select_battle_mode(runtime, *, battle_mode: str) -> None:
    target = OVERCLOCK_BATTLE_MODE_POINT if battle_mode == "overclock" else STANDARD_BATTLE_MODE_POINT
    runtime.click_point(*target)


def is_cw_exact_difficulty_token(value: str) -> bool:
    parsed = parse_cw_start_difficulty_token(value)
    return isinstance(parsed, dict) and parsed.get("kind") == "exact"


def _read_current_entry_rank(
    runtime,
    *,
    requested_difficulty: str,
    target_enemy_difficulty: int | None,
    after_input: bool,
) -> dict[str, object]:
    current_enemy_difficulty = read_entry_enemy_difficulty(
        runtime,
        requested_difficulty=requested_difficulty,
        target_enemy_difficulty=target_enemy_difficulty,
        after_input=after_input,
    )
    current_rank = resolve_entry_rank_from_enemy_difficulty(current_enemy_difficulty)
    if current_rank is None:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            current_enemy_difficulty=current_enemy_difficulty,
            reason="ocr_unmapped",
            after_input=after_input,
        )
    return current_rank


def _reset_entry_to_highest(
    runtime,
    *,
    requested_difficulty: str,
    target_enemy_difficulty: int,
    after_input: bool,
) -> dict[str, object]:
    box = _locate(runtime, "entry.difficulty.highest")
    if box is None:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            reason="highest_reset_unavailable",
            after_input=after_input,
        )
    _click_box_center(runtime, box)
    _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)
    return _read_current_entry_rank(
        runtime,
        requested_difficulty=requested_difficulty,
        target_enemy_difficulty=target_enemy_difficulty,
        after_input=True,
    )


def _coarse_reduce_entry_difficulty(runtime) -> None:
    runtime.drag_to(*ENTRY_DIFFICULTY_COARSE_START, *ENTRY_DIFFICULTY_COARSE_END)
    _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)


def _step_reduce_entry_difficulty(
    runtime,
    *,
    requested_difficulty: str,
    target_enemy_difficulty: int | None,
    after_input: bool,
) -> None:
    box = _locate(runtime, "entry.difficulty.lowest")
    if box is None:
        raise _build_difficulty_recovery_error(
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            reason="step_arrow_missing",
            after_input=after_input,
        )
    _click_box_center(runtime, box)
    _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)


def _select_exact_difficulty(runtime, *, difficulty: str, parsed: Mapping[str, object]) -> None:
    target_enemy_difficulty = int(parsed["target_enemy_difficulty"])
    target_ordinal = int(parsed["global_layer_ordinal"])
    current = _read_current_entry_rank(
        runtime,
        requested_difficulty=difficulty,
        target_enemy_difficulty=target_enemy_difficulty,
        after_input=False,
    )
    after_input = False
    coarse_steps = 0
    fine_steps = 0
    iterations = 0
    max_iterations = ENTRY_DIFFICULTY_COARSE_MAX_STEPS + ENTRY_DIFFICULTY_FINE_MAX_STEPS

    while iterations < max_iterations:
        current_ordinal = int(current["global_layer_ordinal"])
        if current_ordinal == target_ordinal:
            return

        if current_ordinal < target_ordinal:
            iterations += 1
            current = _reset_entry_to_highest(
                runtime,
                requested_difficulty=difficulty,
                target_enemy_difficulty=target_enemy_difficulty,
                after_input=after_input,
            )
            after_input = True
            continue

        before = int(current["enemy_difficulty"])
        if current_ordinal - target_ordinal > ENTRY_DIFFICULTY_COARSE_THRESHOLD:
            if coarse_steps >= ENTRY_DIFFICULTY_COARSE_MAX_STEPS:
                break
            coarse_steps += 1
            iterations += 1
            _coarse_reduce_entry_difficulty(runtime)
            after_input = True
            current = _read_current_entry_rank(
                runtime,
                requested_difficulty=difficulty,
                target_enemy_difficulty=target_enemy_difficulty,
                after_input=True,
            )
            if int(current["enemy_difficulty"]) == before:
                raise _build_difficulty_recovery_error(
                    requested_difficulty=difficulty,
                    target_enemy_difficulty=target_enemy_difficulty,
                    current_enemy_difficulty=int(current["enemy_difficulty"]),
                    reason="coarse_no_progress",
                    after_input=True,
                )
            continue

        if fine_steps >= ENTRY_DIFFICULTY_FINE_MAX_STEPS:
            break
        fine_steps += 1
        iterations += 1
        _step_reduce_entry_difficulty(
            runtime,
            requested_difficulty=difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            after_input=after_input,
        )
        after_input = True
        current = _read_current_entry_rank(
            runtime,
            requested_difficulty=difficulty,
            target_enemy_difficulty=target_enemy_difficulty,
            after_input=True,
        )
        if int(current["enemy_difficulty"]) == before:
            raise _build_difficulty_recovery_error(
                requested_difficulty=difficulty,
                target_enemy_difficulty=target_enemy_difficulty,
                current_enemy_difficulty=int(current["enemy_difficulty"]),
                reason="step_no_progress",
                after_input=True,
            )

    raise _build_difficulty_recovery_error(
        requested_difficulty=difficulty,
        target_enemy_difficulty=target_enemy_difficulty,
        current_enemy_difficulty=int(current["enemy_difficulty"]),
        reason="iteration_budget_exhausted",
        after_input=after_input,
    )


def _select_lowest_difficulty(runtime) -> None:
    requested_difficulty = "lowest"
    near_bottom = _require_entry_rank_token("A1-1")
    bottom = _require_entry_rank_token("A0-1")
    near_bottom_ordinal = int(near_bottom["global_layer_ordinal"])
    bottom_ordinal = int(bottom["global_layer_ordinal"])
    current: Mapping[str, object] | None = None
    after_input = False
    iterations = 0
    max_iterations = ENTRY_DIFFICULTY_COARSE_MAX_STEPS + ENTRY_DIFFICULTY_FINE_MAX_STEPS

    while iterations < max_iterations:
        arrow_box = _locate(runtime, "entry.difficulty.lowest")
        if arrow_box is None:
            try:
                current = _read_current_entry_rank(
                    runtime,
                    requested_difficulty=requested_difficulty,
                    target_enemy_difficulty=None,
                    after_input=after_input,
                )
            except TrailError:
                _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)
                current = _read_current_entry_rank(
                    runtime,
                    requested_difficulty=requested_difficulty,
                    target_enemy_difficulty=None,
                    after_input=after_input,
                )

            current_ordinal = int(current["global_layer_ordinal"])
            if current_ordinal == bottom_ordinal:
                return

            before = int(current["enemy_difficulty"])
            if current_ordinal - near_bottom_ordinal > ENTRY_DIFFICULTY_COARSE_THRESHOLD:
                iterations += 1
                _coarse_reduce_entry_difficulty(runtime)
                after_input = True
                current = _read_current_entry_rank(
                    runtime,
                    requested_difficulty=requested_difficulty,
                    target_enemy_difficulty=None,
                    after_input=True,
                )
                if int(current["enemy_difficulty"]) == before:
                    raise _build_difficulty_recovery_error(
                        requested_difficulty=requested_difficulty,
                        current_enemy_difficulty=int(current["enemy_difficulty"]),
                        reason="coarse_no_progress",
                        after_input=True,
                    )
                continue

            _transition_sleep(ENTRY_DIFFICULTY_SETTLE_SECONDS)
            arrow_box = _locate(runtime, "entry.difficulty.lowest")
            if arrow_box is None:
                current = _read_current_entry_rank(
                    runtime,
                    requested_difficulty=requested_difficulty,
                    target_enemy_difficulty=None,
                    after_input=after_input,
                )
                if int(current["global_layer_ordinal"]) == bottom_ordinal:
                    return
                raise _build_difficulty_recovery_error(
                    requested_difficulty=requested_difficulty,
                    current_enemy_difficulty=int(current["enemy_difficulty"]),
                    reason="lowest_arrow_missing_non_bottom",
                    after_input=after_input,
                )
        else:
            current = _read_current_entry_rank(
                runtime,
                requested_difficulty=requested_difficulty,
                target_enemy_difficulty=None,
                after_input=after_input,
            )

        current_ordinal = int(current["global_layer_ordinal"])
        before = int(current["enemy_difficulty"])
        if current_ordinal - near_bottom_ordinal > ENTRY_DIFFICULTY_COARSE_THRESHOLD:
            iterations += 1
            _coarse_reduce_entry_difficulty(runtime)
            after_input = True
            current = _read_current_entry_rank(
                runtime,
                requested_difficulty=requested_difficulty,
                target_enemy_difficulty=None,
                after_input=True,
            )
            if int(current["enemy_difficulty"]) == before:
                raise _build_difficulty_recovery_error(
                    requested_difficulty=requested_difficulty,
                    current_enemy_difficulty=int(current["enemy_difficulty"]),
                    reason="coarse_no_progress",
                    after_input=True,
                )
            continue

        iterations += 1
        _step_reduce_entry_difficulty(
            runtime,
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=None,
            after_input=after_input,
        )
        after_input = True
        current = _read_current_entry_rank(
            runtime,
            requested_difficulty=requested_difficulty,
            target_enemy_difficulty=None,
            after_input=True,
        )
        if int(current["enemy_difficulty"]) == before:
            raise _build_difficulty_recovery_error(
                requested_difficulty=requested_difficulty,
                current_enemy_difficulty=int(current["enemy_difficulty"]),
                reason="step_no_progress",
                after_input=True,
            )

        if _locate(runtime, "entry.difficulty.lowest") is None:
            if int(current["global_layer_ordinal"]) == bottom_ordinal:
                return
            raise _build_difficulty_recovery_error(
                requested_difficulty=requested_difficulty,
                current_enemy_difficulty=int(current["enemy_difficulty"]),
                reason="lowest_arrow_missing_non_bottom",
                after_input=True,
            )

    current_enemy_difficulty = None if current is None else int(current["enemy_difficulty"])
    raise _build_difficulty_recovery_error(
        requested_difficulty=requested_difficulty,
        current_enemy_difficulty=current_enemy_difficulty,
        reason="iteration_budget_exhausted",
        after_input=after_input,
    )


def _select_difficulty(runtime, *, difficulty: str) -> None:
    if difficulty == "current":
        return

    if difficulty == "highest":
        box = _locate(runtime, "entry.difficulty.highest")
        if box is not None:
            _click_box_center(runtime, box)
        return

    if difficulty == "lowest":
        _select_lowest_difficulty(runtime)
        return

    parsed = parse_cw_start_difficulty_token(difficulty)
    if isinstance(parsed, Mapping) and parsed.get("kind") == "exact":
        _select_exact_difficulty(runtime, difficulty=difficulty, parsed=parsed)
        return

    raise TrailError("CW_ENTRY_DIFFICULTY_INVALID", f"不支持的货币战争难度: {difficulty}")


def _handle_boss_info_flow(runtime) -> None:
    _click_box_center(runtime, _wait(runtime, "stage.settle"))


def _consume_click_blank_prompt(runtime) -> None:
    _click_box_center(runtime, _wait(runtime, "stage.boss_preview"))


def _handle_invest_environment_flow(runtime) -> None:
    _wait(runtime, "entry.invest_environment")


def _continue_from_settlement_chain(runtime, *, difficulty: str, battle_mode: str) -> None:
    for _ in range(6):
        current = _detect_current_enter_page(runtime, preferred_mode="continue")
        page = current["page"]
        if page == "settlement.entry" or page == "settlement.followup":
            runtime.click_point(*SETTLEMENT_CONTINUE_POINT)
            _transition_sleep(SETTLEMENT_CONTINUE_SETTLE_SECONDS)
            continue
        if page == "settlement.return":
            runtime.click_point(*SETTLEMENT_CONTINUE_POINT)
            _transition_sleep(SETTLEMENT_CONTINUE_SETTLE_SECONDS)
            start_box = _wait(runtime, "entry.start")
            _enter_from_start_page(runtime, mode="new", difficulty=difficulty, battle_mode=battle_mode, start_box=start_box)
            return
        raise CwStartContinuePageError(page=page, stage=current.get("stage"))
    raise TrailError("CW_START_CONTINUE_TIMEOUT", "cw start --mode continue did not finish the whole-run settlement chain in time")


def _enter_from_start_page(runtime, *, mode: str, difficulty: str, battle_mode: str, start_box=None) -> None:
    if start_box is None:
        start_box = _wait(runtime, "entry.start")
    _click_box_center(runtime, start_box)
    current = _detect_current_enter_page(runtime, preferred_mode=mode)
    if current.get("page") == "home" and current.get("unfinished_progress") == "1":
        raise CwStartProgressPendingError(after_start_click=True)
    _select_battle_mode(runtime, battle_mode=battle_mode)
    if mode == "new":
        _enter_new_game(runtime, difficulty=difficulty)
        return
    _enter_continue_game(runtime)
    _handle_invest_environment_flow(runtime)


def _enter_from_world(runtime, *, mode: str, difficulty: str, battle_mode: str) -> None:
    del mode, difficulty, battle_mode
    runtime.press_key(ENTRY_GUIDE_HOTKEY)
    _transition_sleep(ENTRY_GUIDE_OPEN_SETTLE_SECONDS)
    _wait(runtime, "entry.menu")
    _click_box_center(runtime, _wait(runtime, "entry.cosmic_strife"))
    _transition_sleep(ENTRY_COSMIC_STRIFE_SETTLE_SECONDS)
    runtime.click_point(*CURRENCY_WARS_ENTRY_POINT)
    _transition_sleep(ENTRY_CURRENCY_WARS_SETTLE_SECONDS)
    runtime.click_point(*CURRENCY_WARS_PARTICIPATE_POINT)
    _transition_sleep(ENTRY_PARTICIPATE_SETTLE_SECONDS)
    _wait(runtime, "entry.start")


def _detect_current_enter_page(
    runtime,
    *,
    session: SessionModel | None = None,
    preferred_mode: str | None = None,
) -> dict[str, str]:
    start_box = _locate(runtime, "entry.start")
    continue_box = _locate(runtime, "entry.continue")
    if (start_box is not None or continue_box is not None) and _home_has_unfinished_progress(runtime):
        return {"page": "home", "unfinished_progress": "1"}

    new_box = _locate(runtime, "entry.new")
    if new_box is not None and continue_box is not None:
        if preferred_mode == "continue":
            return {"page": "entry.continue"}
        return {"page": "entry.new"}

    if new_box is not None:
        return {"page": "entry.new"}

    if continue_box is not None:
        return {"page": "entry.continue"}

    if _locate(runtime, "entry.invest_environment") is not None:
        return {"page": "invest"}

    continue_page = _detect_continue_settlement_page(runtime)
    if continue_page is not None:
        return continue_page

    try:
        ocr_stage = _detect_cw_stage_from_ocr(runtime)
    except Exception:
        ocr_stage = None
    if ocr_stage is not None:
        return {"page": "in_game", "stage": ocr_stage}

    for alias, stage in STAGE_RESOURCE_ALIASES:
        if stage in {"settle", "game_over"}:
            continue
        if _locate(runtime, alias) is None:
            continue
        if stage == "boss_preview":
            return {"page": "stage.boss_preview", "stage": stage}
        return {"page": "in_game", "stage": stage}

    if start_box is not None:
        if session is not None:
            cw_state = ensure_cw_state(session)
            recorded_stage = cw_state.get("stage", {})
            if recorded_stage.get("stale") is False and recorded_stage.get("value") == "game_over":
                return {"page": "in_game", "stage": "game_over"}
            recorded_entry = cw_state.get("entry", {})
            if recorded_entry.get("page") == "home":
                return {"page": "home", "already_home": "1"}
        return {"page": "home", "already_home": "1"}

    return {"page": "world"}


def _enter_new_game(runtime, *, difficulty: str) -> None:
    _click_box_center(runtime, _wait(runtime, "entry.new"))
    _select_difficulty(runtime, difficulty=difficulty)
    _click_box_center(runtime, _wait(runtime, "entry.start_game"))
    _handle_boss_info_flow(runtime)
    _consume_click_blank_prompt(runtime)
    _handle_invest_environment_flow(runtime)


def _enter_continue_game(runtime) -> None:
    _click_box_center(runtime, _wait(runtime, "entry.continue"))
    _click_box_center(runtime, _wait(runtime, "stage.boss_preview"))


def _run_entry_chain(runtime, *, mode: str, difficulty: str, battle_mode: str) -> None:
    current = _detect_current_enter_page(runtime)
    if current["page"] == "home":
        return {"page": "home", "already_home": True}
    if current["page"] == "world":
        _enter_from_world(runtime, mode=mode, difficulty=difficulty, battle_mode=battle_mode)
        return {"page": "home"}
    raise CwEnterStateError(page=current["page"], stage=current.get("stage"))


def _persist_start_entry(
    session: SessionModel,
    *,
    mode: str | None,
    difficulty: str | None,
    battle_mode: str | None,
    guard_conflicts: bool,
) -> None:
    if mode is None or difficulty is None or battle_mode is None:
        missing_fields = tuple(
            field_name
            for field_name, value in (("mode", mode), ("difficulty", difficulty), ("battle_mode", battle_mode))
            if value is None
        )
        raise CwStartEntryTruthRequiredError(page="cw.start.persist", fields=missing_fields)
    cw_state = ensure_cw_state(session)
    existing = cw_state.get("entry") if isinstance(cw_state.get("entry"), Mapping) else {}
    if guard_conflicts:
        for field_name, requested in (("mode", mode), ("difficulty", difficulty), ("battle_mode", battle_mode)):
            recorded = existing.get(field_name)
            if recorded is not None and recorded != requested:
                raise CwStartEntryConflictError(field_name=field_name, recorded=str(recorded), requested=requested)
    _invalidate_entry_snapshots(session)
    cw_state["entry"] = {
        "page": "invest",
        "mode": mode,
        "difficulty": difficulty,
        "battle_mode": battle_mode,
    }


def _run_start_chain(
    runtime,
    *,
    current: dict[str, str],
    mode: str,
    difficulty: str,
    battle_mode: str,
    existing_entry: Mapping[str, object],
) -> dict[str, str | None]:
    page = current["page"]
    if page == "home":
        if current.get("unfinished_progress") == "1":
            raise CwStartProgressPendingError()
        if mode == "continue":
            raise CwStartContinuePageError(page="home")
        start_box = _locate(runtime, "entry.start")
        _enter_from_start_page(runtime, mode=mode, difficulty=difficulty, battle_mode=battle_mode, start_box=start_box)
        return {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode}
    if page == "settlement.entry" or page == "settlement.followup" or page == "settlement.return":
        if mode != "continue":
            raise CwStartNewPageError(page=page)
        _continue_from_settlement_chain(runtime, difficulty=difficulty, battle_mode=battle_mode)
        return {"mode": "new", "difficulty": difficulty, "battle_mode": battle_mode}
    if page == "entry.new":
        if mode != "new":
            raise CwStartContinuePageError(page="entry.new")
        _select_battle_mode(runtime, battle_mode=battle_mode)
        _enter_new_game(runtime, difficulty=difficulty)
        return {"mode": "new", "difficulty": difficulty, "battle_mode": battle_mode}
    if page == "entry.continue":
        if mode != "new":
            raise CwStartContinuePageError(page="entry.continue")
        recorded_difficulty = existing_entry.get("difficulty") if isinstance(existing_entry.get("difficulty"), str) else None
        if recorded_difficulty is None:
            raise CwStartEntryTruthRequiredError(page="entry.continue", fields=("difficulty",))
        _select_battle_mode(runtime, battle_mode=battle_mode)
        _enter_continue_game(runtime)
        _handle_invest_environment_flow(runtime)
        return {"mode": "continue", "difficulty": recorded_difficulty, "battle_mode": battle_mode}
    if page == "stage.boss_preview":
        if mode != "new":
            raise CwStartContinuePageError(page="stage.boss_preview", stage="boss_preview")
        recorded_mode = existing_entry.get("mode") if isinstance(existing_entry.get("mode"), str) else None
        recorded_difficulty = existing_entry.get("difficulty") if isinstance(existing_entry.get("difficulty"), str) else None
        recorded_battle_mode = existing_entry.get("battle_mode") if isinstance(existing_entry.get("battle_mode"), str) else None
        if recorded_mode is None or recorded_difficulty is None or recorded_battle_mode is None:
            raise CwStartEntryTruthRequiredError(
                page="stage.boss_preview",
                fields=("mode", "difficulty", "battle_mode"),
            )
        _consume_click_blank_prompt(runtime)
        _handle_invest_environment_flow(runtime)
        return {
            "mode": recorded_mode,
            "difficulty": recorded_difficulty,
            "battle_mode": recorded_battle_mode,
        }
    if page == "invest":
        return {"mode": mode, "difficulty": difficulty, "battle_mode": battle_mode}
    raise CwStartStateError(page=page, stage=current.get("stage"))


def start_cw(
    session: SessionModel,
    *,
    mode: str,
    difficulty: str,
    battle_mode: str,
    runtime,
) -> SessionModel:
    cw_state = ensure_cw_state(session)
    existing_entry = cw_state.get("entry") if isinstance(cw_state.get("entry"), Mapping) else {}
    current = _detect_current_enter_page(runtime, session=session, preferred_mode=mode)
    if current["page"] == "home" and current.get("unfinished_progress") == "1":
        raise CwStartProgressPendingError()
    if current["page"] == "invest":
        _persist_start_entry(
            session,
            mode=mode,
            difficulty=difficulty,
            battle_mode=battle_mode,
            guard_conflicts=True,
        )
        return session

    resolved_entry = _run_start_chain(
        runtime,
        current=current,
        mode=mode,
        difficulty=difficulty,
        battle_mode=battle_mode,
        existing_entry=existing_entry,
    )
    _persist_start_entry(
        session,
        mode=resolved_entry["mode"],
        difficulty=resolved_entry["difficulty"],
        battle_mode=resolved_entry["battle_mode"],
        guard_conflicts=False,
    )
    return session


def enter_cw(
    session: SessionModel,
    *,
    mode: str | None = None,
    difficulty: str | None = None,
    battle_mode: str | None = None,
    runtime=None,
) -> SessionModel:
    del mode, difficulty, battle_mode
    entry: dict[str, object] = {"page": "home"}

    if runtime is not None:
        current = _detect_current_enter_page(runtime, session=session)
        if current["page"] == "home":
            entry = {"page": "home", "already_home": True}
        elif current["page"] == "world":
            entry = _run_entry_chain(
                runtime,
                mode="ignored",
                difficulty="current",
                battle_mode="standard",
            )
        else:
            raise CwEnterStateError(page=current["page"], stage=current.get("stage"))

    cw_state = ensure_cw_state(session)
    cw_state["entry"] = entry
    _invalidate_entry_snapshots(session)
    return session
