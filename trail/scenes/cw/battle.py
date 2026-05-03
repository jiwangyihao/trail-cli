from __future__ import annotations

import re
from dataclasses import dataclass
from time import monotonic, sleep

from trail.core.errors import TrailError
from trail.runtime.resources import resolve_scene_asset
from trail.scenes.cw.events import build_cw_battle_continuer, build_cw_battle_starter, build_cw_settle_continuer
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import _replace_stage_fields, build_cw_stage_detector, mark_cw_stage_stale
from trail.session.models import SessionModel

SETTLEMENT_HEADLINE_CAPTURE = {
    "from_x": 0.30,
    "from_y": 0.10,
    "to_x": 0.70,
    "to_y": 0.28,
}
SETTLEMENT_ROUND_CAPTURE = {
    "from_x": 0.22,
    "from_y": 0.22,
    "to_x": 0.48,
    "to_y": 0.42,
}
SETTLEMENT_STATS_CAPTURE = {
    "from_x": 0.20,
    "from_y": 0.22,
    "to_x": 0.60,
    "to_y": 0.52,
}
_CW_REFERENCE_WIDTH = 1920
_CW_REFERENCE_HEIGHT = 1080
_CW_ACTION_OCR_REGION = {
    "from_x": 0.30,
    "from_y": 0.72,
    "to_x": 0.70,
    "to_y": 0.92,
}
LAYER_TRANSITION_CONTINUE_POINT = (960, 903)
GAME_OVER_RETURN_TEXT = "返回货币战争"
GAME_OVER_RETURN_POINT = (960, 908)
GAME_OVER_RETURN_SETTLE_SECONDS = 1.0
GAME_OVER_RETURN_HOME_WAIT_SECONDS = 3
GAME_OVER_RETURN_HOME_WAIT_INTERVAL_SECONDS = 0.5
GAME_OVER_RETURN_STILL_PAGE_KEYWORDS = (GAME_OVER_RETURN_TEXT, "对局未完成", "挑战失败", "伤害统计", "小队生命值")

STABLE_BATTLE_RETURN_STAGES: frozenset[str] = frozenset(
    {
        "encounter",
        "event",
        "fortune",
        "invest",
        "replenish",
        "shop",
    }
)
_BATTLE_START_KEYWORDS: tuple[str, ...] = ("备战阶段", "开始战斗", "开始挑战", "出战")
_SETTLEMENT_ENTRY_KEYWORDS: tuple[str, ...] = ("挑战成功", "挑战失败", "继续挑战")
_SETTLEMENT_FOLLOWUP_KEYWORDS: tuple[str, ...] = ("下一步", "下一页", "前往结算")
_GAME_OVER_KEYWORDS: tuple[str, ...] = ("游戏结束", "本局结束", "对局未完成", "返回货币战争")
_BATTLE_PROGRESS_KEYWORDS: tuple[str, ...] = ("自动战斗", "倍速", "暂停")
CW_BATTLE_RUN_POLL_INTERVAL_SECONDS = 0.5
_DETECTED_STAGE_UNSET = object()


def _asset(alias: str) -> str:
    return str(resolve_scene_asset("cw", alias))


def _read_ocr_piece(item: object) -> str:
    if isinstance(item, dict):
        for key in ("text", "value", "name"):
            value = item.get(key)
            if isinstance(value, str | int | float):
                return str(value)
        return ""

    if isinstance(item, (list, tuple)):
        if len(item) >= 2 and isinstance(item[1], str | int | float):
            return str(item[1])
        for value in item:
            if isinstance(value, str | int | float):
                return str(value)
        return ""

    if isinstance(item, str | int | float):
        return str(item)
    return ""


def _coerce_ocr_pieces(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def _joined_ocr_text(runtime, *, capture=None) -> str:
    ocr = getattr(runtime, "ocr", None)
    if not callable(ocr):
        return ""
    try:
        pieces = _coerce_ocr_pieces(ocr(capture=capture))
    except Exception:
        return ""
    return "".join(_read_ocr_piece(piece).strip() for piece in pieces or [])


@dataclass
class BattleObservation:
    runtime: object
    detected_stage: object
    page_ocr_pieces: list[object]
    page_text: str
    _headline_text: str | None = None
    _round_text: str | None = None
    _stats_text: str | None = None

    def headline_text(self) -> str:
        if self._headline_text is None:
            self._headline_text = _joined_ocr_text(self.runtime, capture=SETTLEMENT_HEADLINE_CAPTURE)
        return self._headline_text

    def round_text(self) -> str:
        if self._round_text is None:
            self._round_text = _joined_ocr_text(self.runtime, capture=SETTLEMENT_ROUND_CAPTURE)
        return self._round_text

    def stats_text(self) -> str:
        if self._stats_text is None:
            self._stats_text = _joined_ocr_text(self.runtime, capture=SETTLEMENT_STATS_CAPTURE)
        return self._stats_text


def observe_cw_battle_page(runtime, *, detected_stage: object) -> BattleObservation:
    ocr = getattr(runtime, "ocr", None)
    pieces: list[object] = []
    if callable(ocr):
        try:
            pieces = _coerce_ocr_pieces(ocr())
        except TypeError:
            pieces = _coerce_ocr_pieces(ocr(capture=None))
        except Exception:
            pieces = []
    page_text = "".join(_read_ocr_piece(piece).strip() for piece in pieces)
    return BattleObservation(
        runtime=runtime,
        detected_stage=detected_stage,
        page_ocr_pieces=pieces,
        page_text=page_text,
    )


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _page_text(runtime, observation: BattleObservation | None = None) -> str:
    if observation is not None:
        return observation.page_text
    return _joined_ocr_text(runtime)


def _has_battle_start(runtime, observation: BattleObservation | None = None) -> bool:
    return _contains_any(_page_text(runtime, observation), _BATTLE_START_KEYWORDS)


def _has_settlement_entry(runtime, observation: BattleObservation | None = None) -> bool:
    if observation is not None:
        page_text = observation.page_text
        if "挑战结束" in page_text and "继续挑战" in page_text:
            return True
        if _contains_any(page_text, _SETTLEMENT_ENTRY_KEYWORDS):
            return True
        headline = observation.headline_text()
        if _contains_any(headline, ("挑战成功", "挑战失败", "继续挑战")):
            return True
        if "挑战结束" in headline and "继续挑战" in page_text:
            return True
        return False

    headline = _joined_ocr_text(runtime, capture=SETTLEMENT_HEADLINE_CAPTURE)
    if _contains_any(headline, _SETTLEMENT_ENTRY_KEYWORDS):
        return True
    page_text = _joined_ocr_text(runtime)
    if "挑战结束" in headline and "继续挑战" in page_text:
        return True
    if _contains_any(page_text, _SETTLEMENT_ENTRY_KEYWORDS):
        return True
    return False


def _is_continue_only_settlement_entry(runtime, observation: BattleObservation | None = None) -> bool:
    if observation is not None:
        headline = observation.headline_text() or observation.page_text
    else:
        headline = _joined_ocr_text(runtime, capture=SETTLEMENT_HEADLINE_CAPTURE) or _joined_ocr_text(runtime)
    return "继续挑战" in headline and "挑战成功" not in headline and "挑战失败" not in headline


def _has_settlement_followup(runtime, observation: BattleObservation | None = None) -> bool:
    return _contains_any(_page_text(runtime, observation), _SETTLEMENT_FOLLOWUP_KEYWORDS)


def _has_game_over(runtime, observation: BattleObservation | None = None) -> bool:
    return _contains_any(_page_text(runtime, observation), _GAME_OVER_KEYWORDS)


def _extract_game_over_hp(page_text: str) -> int | None:
    if "小队生命值" not in page_text:
        hp_match = re.search(r"(?:生命值|生命|血量|HP)\D{0,24}(\d+)", page_text, re.IGNORECASE)
        if hp_match is not None:
            return int(hp_match.group(1))
        return None
    before_economy = page_text.split("总经济", 1)[0]
    digits = re.findall(r"\d+", before_economy.split("小队生命值", 1)[1])
    if len(digits) == 1:
        return int(digits[0])
    return None


def _read_game_over_summary(runtime, observation: BattleObservation | None = None) -> dict[str, object]:
    page_text = _page_text(runtime, observation)
    summary: dict[str, object] = {
        "stage": "game_over",
        "game_over": True,
    }
    hp_value = _extract_game_over_hp(page_text)
    if "对局未完成" in page_text or "挑战失败" in page_text or "返回货币战争" in page_text:
        if "对局未完成" in page_text:
            settle_text = "对局未完成"
        elif "挑战失败" in page_text:
            settle_text = "挑战失败"
        else:
            settle_text = "返回货币战争"
        summary.update(
            {
                "result": "lose",
                "settle_text": settle_text,
                "end_reason": "global_battle_failed",
                "restart_candidate": True,
            }
        )
    elif "游戏结束" in page_text or "本局结束" in page_text:
        summary.update({"settle_text": "游戏结束", "end_reason": "game_over"})

    round_match = re.search(r"(\d+-\d+)", page_text)
    if round_match is not None:
        summary["round"] = round_match.group(1)
    if hp_value is not None:
        summary["hp"] = hp_value
    score_match = re.search(r"(?:积分|score)\D*(\d+)", page_text, re.IGNORECASE)
    if score_match is not None:
        summary["score"] = int(score_match.group(1))
    promotion_match = re.search(r"晋升点\D*([\d+]+)", page_text)
    if promotion_match is not None:
        summary["promotion_points"] = promotion_match.group(1)
    return summary


def _has_positive_battle_anchor(runtime, observation: BattleObservation | None = None) -> bool:
    return _contains_any(_page_text(runtime, observation), _BATTLE_PROGRESS_KEYWORDS)


def _classify_stage_without_ocr(detected_stage: object) -> str | None:
    if detected_stage == "preparation":
        return "battle_start"
    if detected_stage == "layer_transition":
        return "layer_transition"
    if detected_stage in STABLE_BATTLE_RETURN_STAGES:
        return "stable_stage"
    return None


def _read_settle_headline(runtime, observation: BattleObservation | None = None) -> tuple[str, str]:
    if observation is not None:
        headline = observation.headline_text() or observation.page_text
    else:
        headline = _joined_ocr_text(runtime, capture=SETTLEMENT_HEADLINE_CAPTURE) or _joined_ocr_text(runtime)
    if "挑战成功" in headline:
        return "win", "挑战成功"
    if "挑战失败" in headline:
        return "lose", "挑战失败"
    if "挑战结束" in headline:
        page_text = headline if "继续挑战" in headline else f"{headline}{_page_text(runtime, observation)}"
        if "继续挑战" in page_text:
            return "win", "挑战结束"
    raise TrailError("CW_SETTLEMENT_UNREADABLE", "无法识别货币战争结算结果")


def _read_optional_settlement_metrics(runtime, observation: BattleObservation | None = None) -> dict[str, str | int]:
    if observation is not None:
        round_text = observation.round_text()
        stats_text = observation.stats_text()
    else:
        round_text = _joined_ocr_text(runtime, capture=SETTLEMENT_ROUND_CAPTURE)
        stats_text = _joined_ocr_text(runtime, capture=SETTLEMENT_STATS_CAPTURE)
    combined_text = f"{round_text} {stats_text}".strip()
    metrics: dict[str, str | int] = {}

    round_match = re.search(r"(\d+-\d+)", round_text or combined_text)
    if round_match is not None:
        metrics["round"] = round_match.group(1)

    for key, pattern in (
        ("hp", r"(?:生命|血量|HP)\D*(\d+)"),
        ("coins", r"(?:金币|钱币|coin(?:s)?)\D*(\d+)"),
        ("exp", r"(?:经验|EXP|exp)\D*(\d+)"),
    ):
        match = re.search(pattern, combined_text)
        if match is not None:
            metrics[key] = int(match.group(1))

    return metrics


def classify_cw_battle_page(
    runtime,
    *,
    session: SessionModel,
    detected_stage: object = _DETECTED_STAGE_UNSET,
    observation: BattleObservation | None = None,
) -> str:
    del session

    detected_state = _classify_stage_without_ocr(detected_stage)
    if detected_state is not None:
        return detected_state

    if observation is None:
        observation = observe_cw_battle_page(runtime, detected_stage=detected_stage)

    if _has_battle_start(runtime, observation):
        return "battle_start"
    if _has_game_over(runtime, observation):
        if _has_settlement_followup(runtime, observation):
            return "game_over_followup"
        return "game_over"
    if _has_settlement_followup(runtime, observation):
        return "settle_followup"
    if _has_settlement_entry(runtime, observation):
        return "settle_entry"

    if detected_stage is _DETECTED_STAGE_UNSET:
        detected_stage = build_cw_stage_detector(runtime)()
    detected_state = _classify_stage_without_ocr(detected_stage)
    if detected_state is not None:
        return detected_state
    if detected_stage == "game_over":
        return "game_over"
    if detected_stage == "settle":
        return "settle_entry"
    if detected_stage == "preparation":
        return "battle_start"
    if detected_stage == "layer_transition":
        return "layer_transition"
    if detected_stage in STABLE_BATTLE_RETURN_STAGES:
        return "stable_stage"

    if _has_positive_battle_anchor(runtime, observation):
        return "battle_progress"
    return "unknown"


def parse_cw_settlement_summary(runtime, observation: BattleObservation | None = None) -> dict[str, object]:
    result, settle_text = _read_settle_headline(runtime, observation=observation)
    summary: dict[str, object] = {
        "result": result,
        "settle_text": settle_text,
    }
    summary.update(_read_optional_settlement_metrics(runtime, observation=observation))
    return summary


def _start_battle(runtime) -> None:
    build_cw_battle_starter(runtime)()


def _normalized_action_text(text: object) -> str:
    return "".join(str(text or "").split())


def _normalize_observation_piece(piece: object) -> dict[str, float | str] | None:
    if isinstance(piece, dict):
        text = str(piece.get("text") or "").strip()
        box = piece.get("box")
        if not text or not isinstance(box, dict):
            return None
        try:
            left = float(box["left"])
            top = float(box["top"])
            width = float(box["width"])
            height = float(box["height"])
        except (KeyError, TypeError, ValueError):
            return None
        return {"text": text, "left": left, "top": top, "width": width, "height": height}

    if isinstance(piece, (list, tuple)) and len(piece) >= 2:
        polygon = piece[0]
        text = str(piece[1] or "").strip()
        if not text or not isinstance(polygon, (list, tuple)):
            return None
        try:
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
        except (TypeError, ValueError, IndexError):
            return None
        if not xs or not ys:
            return None
        left = min(xs)
        top = min(ys)
        return {"text": text, "left": left, "top": top, "width": max(xs) - left, "height": max(ys) - top}

    return None


def _box_center(box: dict[str, float | str]) -> tuple[int, int]:
    return (
        int(float(box["left"]) + float(box["width"]) / 2.0),
        int(float(box["top"]) + float(box["height"]) / 2.0),
    )


def _is_in_action_ocr_region(point: tuple[int, int]) -> bool:
    x, y = point
    left = _CW_REFERENCE_WIDTH * _CW_ACTION_OCR_REGION["from_x"]
    right = _CW_REFERENCE_WIDTH * _CW_ACTION_OCR_REGION["to_x"]
    top = _CW_REFERENCE_HEIGHT * _CW_ACTION_OCR_REGION["from_y"]
    bottom = _CW_REFERENCE_HEIGHT * _CW_ACTION_OCR_REGION["to_y"]
    return left <= x <= right and top <= y <= bottom


def _find_action_button_box_from_observation(
    observation: BattleObservation,
    *,
    allowed_texts: set[str],
) -> dict[str, float | str] | None:
    normalized_allowed = {_normalized_action_text(text) for text in allowed_texts}
    for piece in observation.page_ocr_pieces:
        normalized_piece = _normalize_observation_piece(piece)
        if normalized_piece is None:
            continue
        if _normalized_action_text(normalized_piece["text"]) not in normalized_allowed:
            continue
        if not _is_in_action_ocr_region(_box_center(normalized_piece)):
            continue
        return normalized_piece
    return None


def _continue_after_settlement(runtime, observation: BattleObservation | None = None) -> None:
    if observation is not None:
        button = _find_action_button_box_from_observation(observation, allowed_texts={"继续挑战", "继续"})
        if button is not None:
            runtime.click_point(*_box_center(button))
            return
    build_cw_battle_continuer(runtime)()


def _advance_settlement_page(runtime, observation: BattleObservation | None = None) -> None:
    if observation is not None:
        button = _find_action_button_box_from_observation(observation, allowed_texts={"下一步", "下一页", "前往结算"})
        if button is not None:
            runtime.click_point(*_box_center(button))
            return
    build_cw_settle_continuer(runtime)()


def _advance_layer_transition(runtime) -> None:
    runtime.click_point(*LAYER_TRANSITION_CONTINUE_POINT)


def _wait_for_game_over_return_home(runtime) -> bool:
    home_template_visible = False
    wait_img = getattr(runtime, "wait_img", None)
    if callable(wait_img):
        try:
            if wait_img(
                _asset("entry.start"),
                timeout=GAME_OVER_RETURN_HOME_WAIT_SECONDS,
                interval=GAME_OVER_RETURN_HOME_WAIT_INTERVAL_SECONDS,
            ) is not None:
                home_template_visible = True
        except Exception:
            pass

    if not home_template_visible:
        locate = getattr(runtime, "locate", None)
        if not callable(locate):
            return False
        for alias in ("entry.start", "entry.continue"):
            try:
                if locate(_asset(alias)) is not None:
                    home_template_visible = True
                    break
            except Exception:
                continue
    if not home_template_visible:
        return False

    page_text = _joined_ocr_text(runtime)
    return bool(page_text) and not _contains_any(page_text, GAME_OVER_RETURN_STILL_PAGE_KEYWORDS)


def _return_from_game_over(runtime, observation: BattleObservation | None = None) -> bool:
    if observation is not None:
        button = _find_action_button_box_from_observation(observation, allowed_texts={GAME_OVER_RETURN_TEXT})
        if button is not None:
            runtime.click_point(*_box_center(button))
            sleep(GAME_OVER_RETURN_SETTLE_SECONDS)
            return _wait_for_game_over_return_home(runtime)
    runtime.click_point(*GAME_OVER_RETURN_POINT)
    sleep(GAME_OVER_RETURN_SETTLE_SECONDS)
    return _wait_for_game_over_return_home(runtime)


def _has_game_over_return(runtime, observation: BattleObservation | None = None) -> bool:
    return GAME_OVER_RETURN_TEXT in _page_text(runtime, observation)


def _mark_game_over_return_home(session: SessionModel, *, returned_home: bool) -> None:
    if returned_home:
        ensure_cw_state(session)["entry"] = {"page": "home"}
    mark_cw_stage_stale(session)


def _set_completed_stage(session: SessionModel, *, stage: str) -> None:
    _replace_stage_fields(session, value=stage, stale=False)
    session.last_stage = {"scene": "cw", "value": stage}


def _persist_battle_round(session: SessionModel, summary: dict[str, object]) -> None:
    battle_round = summary.get("round")
    if isinstance(battle_round, str) and battle_round:
        ensure_cw_state(session).setdefault("metrics", {})["last_battle_round"] = battle_round


def _battle_resume_state(session: SessionModel) -> dict[str, object]:
    cw_state = ensure_cw_state(session)
    resume = cw_state.get("battle_resume")
    if isinstance(resume, dict):
        return resume
    resume = {}
    cw_state["battle_resume"] = resume
    return resume


def _seed_resume_in_battle(session: SessionModel) -> bool:
    return bool(_battle_resume_state(session).get("in_battle_hint"))


def _store_resume_in_battle(session: SessionModel, *, enabled: bool) -> None:
    resume = _battle_resume_state(session)
    if enabled:
        resume["in_battle_hint"] = True
        return
    resume.pop("in_battle_hint", None)


def _finalize_battle_result(session: SessionModel, result: dict[str, object]) -> dict[str, object]:
    _persist_battle_round(session, result)
    _store_resume_in_battle(
        session,
        enabled=result.get("status") == "in_progress" and result.get("in_battle") is True,
    )
    return result


def _timeout_battle_run(
    session: SessionModel,
    *,
    timeout: int | float,
    summary: dict[str, object],
    settle_chain_seen: bool,
) -> dict[str, object]:
    mark_cw_stage_stale(session)
    if settle_chain_seen:
        return {
            **summary,
            "status": "in_progress",
            "stage": "settle",
            "stale": True,
            "in_battle": False,
            "timeout_seconds": timeout,
        }
    return {
        "status": "in_progress",
        "stale": True,
        "in_battle": True,
        "timeout_seconds": timeout,
    }


def _unknown_battle_state(*, state: str, detail: object | None = None) -> TrailError:
    if isinstance(detail, str) and detail:
        message = f"无法识别货币战争 battle.run 状态: {detail}"
    else:
        message = f"无法识别货币战争 battle.run 状态: {state}"
    return TrailError("CW_BATTLE_STATE_UNKNOWN", message)


def run_cw_battle(session: SessionModel, *, runtime, timeout: int | float) -> dict[str, object]:
    deadline = monotonic() + timeout
    started_chain = False
    start_attempted = False
    resume_in_battle = _seed_resume_in_battle(session)
    settle_chain_seen = False
    summary: dict[str, object] = {}

    try:
        while True:
            detected_stage = build_cw_stage_detector(runtime)()
            observation: BattleObservation | None = None
            state = _classify_stage_without_ocr(detected_stage)
            if state is None:
                observation = observe_cw_battle_page(runtime, detected_stage=detected_stage)
                state = classify_cw_battle_page(
                    runtime,
                    session=session,
                    detected_stage=detected_stage,
                    observation=observation,
                )
            if state == "battle_start" and (started_chain or resume_in_battle):
                completed_stage = detected_stage if detected_stage == "preparation" else "preparation"
                _set_completed_stage(session, stage=completed_stage)
                return _finalize_battle_result(
                    session,
                    {
                        **summary,
                        "status": "completed",
                        "stage": completed_stage,
                        "stale": False,
                        "in_battle": False,
                    },
                )
            if state in {"battle_progress", "settle_entry", "settle_followup", "layer_transition"}:
                started_chain = True
            if state == "game_over_followup":
                started_chain = True
            if state in {"settle_entry", "settle_followup", "game_over_followup"}:
                settle_chain_seen = True

            if state == "stable_stage":
                stage = detected_stage
                if stage not in STABLE_BATTLE_RETURN_STAGES:
                    raise _unknown_battle_state(state=state, detail=stage)
                _set_completed_stage(session, stage=stage)
                return _finalize_battle_result(
                    session,
                    {
                        **summary,
                        "status": "completed",
                        "stage": stage,
                        "stale": False,
                        "in_battle": False,
                    },
                )

            if state == "game_over":
                has_return_button = _has_game_over_return(runtime, observation=observation)
                if observation is not None:
                    game_over_summary = _read_game_over_summary(runtime, observation=observation)
                    if "result" not in summary or game_over_summary.get("result") == "lose" or has_return_button:
                        summary.update(game_over_summary)
                if has_return_button:
                    returned_home = _return_from_game_over(runtime, observation=observation)
                    summary["returned_home"] = returned_home
                    _mark_game_over_return_home(session, returned_home=returned_home)
                else:
                    _set_completed_stage(session, stage="game_over")
                return _finalize_battle_result(
                    session,
                    {
                        **summary,
                        "status": "completed",
                        "stage": "game_over",
                        "stale": False,
                        "in_battle": False,
                    },
                )

            if state == "unknown" and not (started_chain or start_attempted or resume_in_battle):
                raise _unknown_battle_state(state=state)

            remaining = deadline - monotonic()
            if remaining <= 0:
                if state == "layer_transition":
                    mark_cw_stage_stale(session)
                    return _finalize_battle_result(
                        session,
                        {
                            **summary,
                            "status": "in_progress",
                            "stage": "layer_transition",
                            "stale": True,
                            "in_battle": False,
                            "timeout_seconds": timeout,
                        },
                    )
                return _finalize_battle_result(
                    session,
                    _timeout_battle_run(
                        session,
                        timeout=timeout,
                        summary=summary,
                        settle_chain_seen=settle_chain_seen,
                    ),
                )

            if state == "battle_start":
                _start_battle(runtime)
                start_attempted = True
                continue

            if state == "battle_progress":
                sleep(min(CW_BATTLE_RUN_POLL_INTERVAL_SECONDS, remaining))
                continue

            if state == "settle_entry":
                try:
                    summary = parse_cw_settlement_summary(runtime, observation=observation)
                except TrailError as error:
                    if error.code != "CW_SETTLEMENT_UNREADABLE" or not _is_continue_only_settlement_entry(
                        runtime,
                        observation=observation,
                    ):
                        raise
                _continue_after_settlement(runtime, observation=observation)
                continue

            if state == "settle_followup":
                _advance_settlement_page(runtime, observation=observation)
                continue

            if state == "game_over_followup":
                if observation is not None:
                    summary.update(_read_game_over_summary(runtime, observation=observation))
                _advance_settlement_page(runtime, observation=observation)
                continue

            if state == "layer_transition":
                _advance_layer_transition(runtime)
                continue

            if state == "unknown":
                sleep(min(CW_BATTLE_RUN_POLL_INTERVAL_SECONDS, remaining))
                continue

            raise _unknown_battle_state(state=state)
    except Exception:
        _store_resume_in_battle(session, enabled=False)
        raise
