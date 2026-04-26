from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from trail.output.debug import collect_debug_events
from trail.output.rendering import render_output


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_OCR_CONTEXT_DEBUG_LINES = (
    "debug kind=context key=ocr_mode_requested",
    "debug kind=context key=ocr_mode_effective",
    "debug kind=context key=ocr_scale_applied",
    "debug kind=context key=ocr_retry_high",
    "debug kind=context key=ocr_retry_reason",
)


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


def test_collect_debug_events_canonicalizes_box_only_in_debug_layer():
    debug = {
        "trace": [
            {
                "step": "locate",
                "box": {"left": 10, "top": 20, "width": 30, "height": 40, "source": "template"},
            }
        ]
    }

    assert collect_debug_events(debug) == [{"kind": "trace", "step": "locate", "box": "10,20,30,40"}]
    assert debug["trace"][0]["box"] == {"left": 10, "top": 20, "width": 30, "height": 40, "source": "template"}


def test_collect_debug_events_keeps_malformed_box_raw_in_debug_layer():
    malformed_box = {"left": 1, "top": 2, "width": 3, "height": None}
    debug = {"trace": [{"step": "locate", "box": malformed_box}]}

    assert collect_debug_events(debug) == [{"kind": "trace", "step": "locate", "box": malformed_box}]


def test_collect_debug_events_keeps_non_dict_trace_legacy_shape():
    debug = {"trace": ["legacy-trace"]}

    assert collect_debug_events(debug) == [{"kind": "trace", "step": "unknown", "value": "legacy-trace"}]


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


def test_verbose_output_ocr_provider_trace_uses_existing_debug_pipeline():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-ocr-dml.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-ocr-dml",
            "trace": [
                {
                    "step": "ocr_provider",
                    "requested_provider": "dml",
                    "effective_provider": "dml",
                    "lang": "ch",
                    "available_providers": ["DmlExecutionProvider", "CPUExecutionProvider"],
                    "reason": "RuntimeError: explicit dml run failed",
                }
            ],
        },
        "error": {"code": "OCR_PROVIDER_UNAVAILABLE", "message": "requested dml provider unavailable"},
    }

    assert render_output("ocr.read", payload, verbose=True).splitlines() == [
        "fail ocr.read code=OCR_PROVIDER_UNAVAILABLE",
        "request id=req-ocr-dml",
        "shot path=.trail/shots/req-ocr-dml.png",
        'why msg="requested dml provider unavailable"',
        "debug kind=request msg=req-ocr-dml",
        'debug kind=trace step=ocr_provider requested_provider=dml effective_provider=dml lang=ch available_providers="[\'DmlExecutionProvider\', \'CPUExecutionProvider\']" reason="RuntimeError: explicit dml run failed"',
    ]


def test_verbose_output_ocr_mode_retry_context_uses_existing_debug_pipeline():
    payload = {
        "ok": True,
        "data": {"result": [{"text": "点击进入"}]},
        "screenshot": ".trail/shots/req-ocr-fast-retry.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-ocr-fast-retry",
            "trace": [
                {
                    "step": "ocr",
                    "pieces": 1,
                    "mode_requested": "fast",
                    "mode_effective": "high",
                    "scale_applied": "native",
                    "retry_high": 1,
                    "retry_reason": "low_confidence",
                }
            ],
        },
        "error": None,
    }

    rendered = render_output("ocr.read", payload, verbose=True)

    assert rendered.splitlines() == [
        "ok ocr.read hits=1",
        "shot path=.trail/shots/req-ocr-fast-retry.png",
        "info read_image_first=1",
        "text value=点击进入",
        "debug kind=request msg=req-ocr-fast-retry",
        "debug kind=trace step=ocr pieces=1 mode_requested=fast mode_effective=high scale_applied=native retry_high=1 retry_reason=low_confidence",
    ]
    assert all(line not in rendered for line in LEGACY_OCR_CONTEXT_DEBUG_LINES)


def test_default_output_does_not_leak_batch_ocr_debug_data():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
            "stage_status_stale": True,
        },
        "screenshot": ".trail/shots/req-shop-batch-ocr.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "trace": [
                {
                    "step": "cw_shop_batch_ocr",
                    "atlas": "debug-atlas.png",
                    "rect": "0,0,120,40",
                    "target_count": 2,
                }
            ]
        },
        "error": None,
    }

    rendered = render_output("cw.shop.scan", payload)

    assert "debug" not in rendered
    assert "atlas" not in rendered
    assert "rect" not in rendered


def test_verbose_output_renders_cw_shop_batch_ocr_trace():
    payload = {
        "ok": True,
        "data": {"items": [], "opened": True, "stale": False, "stage_status_stale": True},
        "screenshot": ".trail/shots/req-shop-batch-ocr-verbose.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "trace": [
                {
                    "step": "cw_shop_batch_ocr",
                    "target_count": 2,
                    "atlas_width": 256,
                    "atlas_height": 64,
                    "ok": True,
                }
            ]
        },
        "error": None,
    }

    assert "debug kind=trace step=cw_shop_batch_ocr" in render_output("cw.shop.scan", payload, verbose=True)


def test_verbose_output_ocr_failure_keeps_ocr_trace_from_runtime(tmp_path):
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
    encoded_screenshot = payload["screenshot"].replace("\\", "\\\\")
    rendered = render_output("ocr.read", payload, verbose=True)
    lines = rendered.splitlines()
    ocr_line = next(line for line in lines if line.startswith("debug kind=trace step=ocr "))
    screenshot_line = next(line for line in lines if line.startswith("debug kind=trace step=capture_after_action "))

    assert lines[:3] == [
        "fail ocr.read code=OCR_BACKEND_UNAVAILABLE",
        f'shot path="{encoded_screenshot}"',
        'why msg="ocr backend unavailable"',
    ]
    assert screenshot_line.startswith("debug kind=trace step=capture_after_action optional=1 ")
    assert f'optional=1 screenshot="{encoded_screenshot}"' in screenshot_line
    assert "ts=" in screenshot_line
    assert "ok=1" in screenshot_line
    assert "kind=trace step=ocr" in ocr_line
    assert "ok=0" in ocr_line
    assert "mode_requested=fast" in ocr_line
    assert "mode_effective=fast" in ocr_line
    assert "scale_applied=1280x720" in ocr_line
    assert "retry_high=0" in ocr_line
    assert "retry_reason=none" in ocr_line
    assert "error_code=OCR_BACKEND_UNAVAILABLE" in ocr_line
    assert 'msg="ocr backend unavailable"' in ocr_line
    assert all(line not in rendered for line in LEGACY_OCR_CONTEXT_DEBUG_LINES)


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
    assert "当前 YAML allowlist 是 `daemon.status`、`state.dump`、`guide.fetch.cw`、`guide.config.cw`。" in agents
    assert "guide.list.cw 的默认文本改用 攻略ID/攻略标题/版本/主C/攻略标签/最终阵容" in agents
    assert "cw.start` / `cw.portal.select|refresh|restart` 的 portal 卡片字段使用 `投资环境/说明/待收集`" in agents
    assert "cw.guide.current|apply` 使用 `攻略ID/攻略标题/攻略码/版本`，并以 `info 攻略快照ID=...` 表示 artifact id" in agents
    assert "cw.guide.current|apply` 的 `攻略快照ID` 是 artifact id / 恢复追踪 id，不是 `shot path` 截图路径" in agents
    assert "guide.fetch.cw --select` 只负责把当前攻略写入 session，不扩张 success / YAML shape" in agents
    assert "cw.portal.select` 若响应 `data.skill_info` 非空，默认正文使用 `info skill_info=运营思路 text=...`" in agents
    assert "必须在 `warn`、`ref` 之前输出" in agents
    assert "cw.portal.select` 命中 workflow handoff 时，success 最后一行必须是 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`" in agents
    assert "cw.shop.buy_exp` 属于 shop action renderer family" in agents
    assert "team_size=null` 是 must-keep null fact" in agents
    assert "cw.shop.buy_exp` 不在 YAML allowlist，`--format yaml` 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`" in agents
    assert "不扩张 success / YAML shape" in agents
    assert "guide.fetch.cw --select` 只负责把当前攻略写入 session" in agents
    assert "由 `cw.portal.select` 成功时自动兑现当前已选攻略" in agents
    assert "current/apply` 只看当前已应用攻略摘要" not in agents
    assert "在进入游戏并完成投资环境选择后，再执行" not in agents
    assert "cw guide apply --session <id> --lineup-id <lineup_id>" not in agents
    assert "--lineup-id" not in agents
    assert "guide.config.cw` 使用 `赛季/子赛季/大版本/搜牌档位/羁绊/角色/角色标签/投资环境`" in agents
    assert "README.md` 示例与说明" in agents
    assert "renderer 单测" in agents
    assert "CLI stdout 测试或 RPC/契约测试增量" in agents
