from __future__ import annotations

import re
from time import monotonic, sleep

from trail.core.errors import TrailError
from trail.scenes.cw.events import build_cw_battle_continuer, build_cw_battle_starter, build_cw_settle_continuer
from trail.scenes.cw.models import ensure_cw_state
from trail.scenes.cw.stage import build_cw_stage_detector, mark_cw_stage_stale
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
_BATTLE_START_KEYWORDS: tuple[str, ...] = ("开始战斗", "开始挑战", "出战")
_SETTLEMENT_ENTRY_KEYWORDS: tuple[str, ...] = ("挑战成功", "挑战失败", "继续挑战")
_SETTLEMENT_FOLLOWUP_KEYWORDS: tuple[str, ...] = ("下一步", "下一页")
_GAME_OVER_KEYWORDS: tuple[str, ...] = ("游戏结束", "本局结束")
_BATTLE_PROGRESS_KEYWORDS: tuple[str, ...] = ("自动战斗", "倍速", "暂停")
CW_BATTLE_RUN_POLL_INTERVAL_SECONDS = 0.5
_DETECTED_STAGE_UNSET = object()


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


def _joined_ocr_text(runtime, *, capture=None) -> str:
    ocr = getattr(runtime, "ocr", None)
    if not callable(ocr):
        return ""
    try:
        pieces = ocr(capture=capture)
    except Exception:
        return ""
    return "".join(_read_ocr_piece(piece).strip() for piece in pieces or [])


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _has_battle_start(runtime) -> bool:
    return _contains_any(_joined_ocr_text(runtime), _BATTLE_START_KEYWORDS)


def _has_settlement_entry(runtime) -> bool:
    headline = _joined_ocr_text(runtime, capture=SETTLEMENT_HEADLINE_CAPTURE)
    if _contains_any(headline, _SETTLEMENT_ENTRY_KEYWORDS):
        return True
    return _contains_any(_joined_ocr_text(runtime), _SETTLEMENT_ENTRY_KEYWORDS)


def _is_continue_only_settlement_entry(runtime) -> bool:
    headline = _joined_ocr_text(runtime, capture=SETTLEMENT_HEADLINE_CAPTURE) or _joined_ocr_text(runtime)
    return "继续挑战" in headline and "挑战成功" not in headline and "挑战失败" not in headline


def _has_settlement_followup(runtime) -> bool:
    return _contains_any(_joined_ocr_text(runtime), _SETTLEMENT_FOLLOWUP_KEYWORDS)


def _has_game_over(runtime) -> bool:
    return _contains_any(_joined_ocr_text(runtime), _GAME_OVER_KEYWORDS)


def _has_positive_battle_anchor(runtime) -> bool:
    return _contains_any(_joined_ocr_text(runtime), _BATTLE_PROGRESS_KEYWORDS)


def _read_settle_headline(runtime) -> tuple[str, str]:
    headline = _joined_ocr_text(runtime, capture=SETTLEMENT_HEADLINE_CAPTURE) or _joined_ocr_text(runtime)
    if "挑战成功" in headline:
        return "win", "挑战成功"
    if "挑战失败" in headline:
        return "lose", "挑战失败"
    if "挑战结束" in headline:
        page_text = headline if "继续挑战" in headline else f"{headline}{_joined_ocr_text(runtime)}"
        if "继续挑战" in page_text:
            return "win", "挑战结束"
    raise TrailError("CW_SETTLEMENT_UNREADABLE", "无法识别货币战争结算结果")


def _read_optional_settlement_metrics(runtime) -> dict[str, str | int]:
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


def classify_cw_battle_page(runtime, *, session: SessionModel, detected_stage: object = _DETECTED_STAGE_UNSET) -> str:
    del session

    if _has_battle_start(runtime):
        return "battle_start"
    if _has_settlement_entry(runtime):
        return "settle_entry"
    if _has_settlement_followup(runtime):
        return "settle_followup"
    if _has_game_over(runtime):
        return "game_over"

    if detected_stage is _DETECTED_STAGE_UNSET:
        detected_stage = build_cw_stage_detector(runtime)()
    if detected_stage == "game_over":
        return "game_over"
    if detected_stage == "settle":
        return "settle_entry"
    if detected_stage == "preparation":
        return "battle_start"
    if detected_stage in STABLE_BATTLE_RETURN_STAGES:
        return "stable_stage"

    if _has_positive_battle_anchor(runtime):
        return "battle_progress"
    return "unknown"


def parse_cw_settlement_summary(runtime) -> dict[str, str | int]:
    result, settle_text = _read_settle_headline(runtime)
    summary: dict[str, str | int] = {
        "result": result,
        "settle_text": settle_text,
    }
    summary.update(_read_optional_settlement_metrics(runtime))
    return summary


def _start_battle(runtime) -> None:
    build_cw_battle_starter(runtime)()


def _continue_after_settlement(runtime) -> None:
    build_cw_battle_continuer(runtime)()


def _advance_settlement_page(runtime) -> None:
    build_cw_settle_continuer(runtime)()


def _set_completed_stage(session: SessionModel, *, stage: str) -> None:
    ensure_cw_state(session)["stage"] = {"value": stage, "stale": False}
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
    summary: dict[str, str | int],
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
    summary: dict[str, str | int] = {}

    try:
        while True:
            detected_stage = build_cw_stage_detector(runtime)()
            state = classify_cw_battle_page(runtime, session=session, detected_stage=detected_stage)
            if state == "battle_start" and (
                (started_chain and detected_stage == "preparation") or resume_in_battle
            ):
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
            if state in {"battle_progress", "settle_entry", "settle_followup"}:
                started_chain = True
            if state in {"settle_entry", "settle_followup"}:
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
                    summary = parse_cw_settlement_summary(runtime)
                except TrailError as error:
                    if error.code != "CW_SETTLEMENT_UNREADABLE" or not _is_continue_only_settlement_entry(runtime):
                        raise
                _continue_after_settlement(runtime)
                continue

            if state == "settle_followup":
                _advance_settlement_page(runtime)
                continue

            if state == "unknown":
                sleep(min(CW_BATTLE_RUN_POLL_INTERVAL_SECONDS, remaining))
                continue

            raise _unknown_battle_state(state=state)
    except Exception:
        _store_resume_in_battle(session, enabled=False)
        raise
