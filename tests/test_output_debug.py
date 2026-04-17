from pathlib import Path

from trail.output.debug import collect_debug_events
from trail.output.rendering import render_output


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_collect_debug_events_preserves_trace_detail_and_context_fields():
    debug = {
        "request_id": "req-42",
        "trace": [{"step": "click", "point": [10, 20]}],
        "detail": "backend missing",
        "last_known_stage": "handler_completed",
        "stage_detail": "state persisted marker failed",
        "recovery_detail": "journal flush retry exhausted",
        "session_id": "session-1",
        "pid": 4321,
        "record": {"final_state": "completed"},
    }

    assert collect_debug_events(debug) == [
        {"kind": "request", "msg": "req-42"},
        {"kind": "trace", "step": "click", "point": [10, 20]},
        {"kind": "detail", "msg": "backend missing"},
        {"kind": "context", "key": "last_known_stage", "value": "handler_completed"},
        {"kind": "context", "key": "stage_detail", "value": "state persisted marker failed"},
        {"kind": "context", "key": "recovery_detail", "value": "journal flush retry exhausted"},
        {"kind": "context", "key": "session_id", "value": "session-1"},
        {"kind": "context", "key": "pid", "value": 4321},
        {"kind": "context", "key": "record", "value": {"final_state": "completed"}},
    ]


def test_render_output_does_not_leak_debug_lines_without_verbose():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-42",
            "trace": [{"step": "click", "point": [10, 20]}],
            "detail": "backend missing",
            "last_known_stage": "handler_completed",
        },
        "error": {"code": "INPUT_BACKEND_MISSING", "message": "input backend missing"},
    }

    assert render_output("input.click", payload).splitlines() == [
        "fail input.click code=INPUT_BACKEND_MISSING tainted=1",
        "request id=req-42",
        'why msg="input backend missing"',
        "recover action=daemon.request_status request=req-42",
    ]


def test_verbose_output_keeps_extra_debug_fields():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-42",
            "trace": [{"step": "click", "point": [10, 20]}],
            "detail": "backend missing",
            "last_known_stage": "handler_completed",
            "stage_detail": "state persisted marker failed",
            "recovery_detail": "journal flush retry exhausted",
            "session_id": "session-1",
            "pid": 4321,
            "record": {"final_state": "completed"},
        },
        "error": {"code": "INPUT_BACKEND_MISSING", "message": "input backend missing"},
    }

    assert render_output("input.click", payload, verbose=True).splitlines() == [
        "fail input.click code=INPUT_BACKEND_MISSING tainted=1",
        "request id=req-42",
        'why msg="input backend missing"',
        "recover action=daemon.request_status request=req-42",
        "debug kind=request msg=req-42",
        'debug kind=trace step=click point="[10, 20]"',
        'debug kind=detail msg="backend missing"',
        "debug kind=context key=last_known_stage value=handler_completed",
        'debug kind=context key=stage_detail value="state persisted marker failed"',
        'debug kind=context key=recovery_detail value="journal flush retry exhausted"',
        "debug kind=context key=session_id value=session-1",
        "debug kind=context key=pid value=4321",
        'debug kind=context key=record value="{\'final_state\': \'completed\'}"',
    ]


def test_project_agents_declares_renderer_contracts() -> None:
    agents_path = PROJECT_ROOT / "AGENTS.md"
    agents = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""

    assert (
        "- 默认模式一律使用内部 canonical command 名，必须是点号形式，例如 `cw.shop.buy_slot`、`daemon.request_status`、`screen.shot`；不要写成 `cw.shop.buy-slot`、`daemon request-status`。"
        in agents
    )
    assert "- 只要当前命令产出截图，就必须输出 `shot path=...`。" in agents
    assert "- 失败结果只要带 `request_id`，就必须输出 `request id=<id>` 供恢复或排障使用。" in agents
    assert "- 只有结果未知或当前失败显式可恢复时，才输出 `recover action=daemon.request_status request=<id>`。" in agents
    assert (
        "- 默认正文前缀只允许使用 `request`、`shot`、`item`、`guide`、`text`、`slot`、`opt`、`why`、`warn`、`ref`、`recover`、`info`；`debug` 仅用于 `--verbose` 追加层。"
        in agents
    )
    assert (
        "- failure 路径正文顺序固定为：`request` -> `shot` -> `why` -> `warn` -> `ref` -> `recover`；`debug` 只能在 `--verbose` 时追加在最后。"
        in agents
    )
    assert (
        "- 高频冻结字段至少包括：`session_id` -> `session`、`next_page_token` -> `next`、`similarity` -> `sim`、`confidence/score` -> `score`、`bbox/rect` -> `box`、条目序号 -> `idx`、人类消息 -> `msg`。"
        in agents
    )
    assert (
        "- 默认文本统一使用 `key=value`；除首行的 `ok|fail` 和 `<command>` 外，不再新增位置参数。"
        in agents
    )
    assert (
        "- 布尔值统一编码为 `0/1`；含空格、引号、反斜杠、换行、等号或逗号歧义的值必须使用双引号。"
        in agents
    )
    assert (
        "- 只省略语义缺失值，不能省略会影响下一步动作的 `0`、`false`、`count`、`more`、`tainted`。"
        in agents
    )
    assert "trail.output.debug.collect_debug_events" in agents
    assert "trail.output.debug.render_debug_lines" in agents
    assert "当前 YAML allowlist 是 `daemon.status`、`state.dump`、`guide.config.cw`。" in agents
    assert "README.md` 示例与说明" in agents
    assert "renderer 单测" in agents
    assert "CLI stdout 测试或 RPC/契约测试增量" in agents
