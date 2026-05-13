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


def test_project_agents_records_design_principles_not_protocol_catalog() -> None:
    agents_path = PROJECT_ROOT / "AGENTS.md"
    agents = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""

    for expected in [
        "# 项目设计准则",
        "`README.md` 只面向普通用户",
        "不要在 `README.md` 编写命令协议、输出字段、恢复链路、renderer 契约、skill 拓扑或玩法流程细节",
        "发布完成后必须手动重写 release note",
        "Release note 面向用户和安装者",
        "默认文本是 Agent 主通道",
        "新命令必须先归类到已有 renderer 家族",
        "带截图的成功结果必须先输出截图路径，再立即提示 Agent 先读原始截图",
        "failure 输出顺序必须稳定",
        "`--verbose` 只追加开发和排障层",
        "输出变更至少补 renderer 单测",
        "玩法事实应按稳定板块分组",
        "自动收集 facts 时，缺失、stale、低置信和可恢复失败必须清晰呈现",
        "Agent 可见位置使用 1-based 表达",
        "daemon 采用单例异步续查模型",
        "控制面命令只负责状态查询、恢复和取消",
    ]:
        assert expected in agents

    assert "默认模式一律使用内部 canonical command 名" not in agents
    assert "当前 YAML allowlist 是" not in agents
    assert "success 首行固定" not in agents
    assert "ok cw.equipment.compose pos=" not in agents
    assert "team_size=null" not in agents

def test_cw_equipment_recommendation_docs_are_synced() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    prep_skill = (PROJECT_ROOT / "skills" / "trail-cw-prep" / "SKILL.md").read_text(encoding="utf-8")
    command_surface = (
        PROJECT_ROOT / "skills" / "trail-cw-prep" / "references" / "command-surface.md"
    ).read_text(encoding="utf-8")
    compose_docs = "\n".join(
        line
        for text in (agents, prep_skill, command_surface)
        for line in text.splitlines()
        if "compose" in line
        or "cw.equipment.compose" in line
        or "trail cw equipment compose" in line
    )

    for text in (prep_skill, command_surface):
        assert "# 装备优先级" in text
        assert "# 角色装备需求" in text
        assert "cw.equipment.compose" in text

    assert "装备合成" in agents
    assert "cw.equipment.compose" in command_surface
    assert "trail cw equipment compose" in command_surface
    assert "--slot <front|back|hand>:<1-based>" in command_surface
    assert "只写 session" not in compose_docs
    assert "shot path" in compose_docs
    assert "info read_image_first=1" in compose_docs
    assert "action=equip_existing" in compose_docs
    assert "consumed=0" in compose_docs
    assert "item kind=existing phase=pre_equip" in compose_docs
    assert "action=compose" in compose_docs
    assert "consumed=2" in compose_docs
    assert "item kind=material phase=pre_compose" in compose_docs
    assert "item kind=result phase=post_compose" in compose_docs
    assert "材料不足" in compose_docs
    assert "info todo=slots" in prep_skill
    assert "info todo=slots" in command_surface
    assert "装备识别、装备推荐和装备合成分别保持边界" in agents
    assert "推荐只表达当前攻略优选装备需求" in agents
    assert "当前只消费攻略 `优选装备` / `first_equipments`" in prep_skill
    assert "当前只推荐攻略 `优选装备` / `first_equipments`" in command_surface
    assert "装备推荐分块位于 `warn`、`ref` 之前" in prep_skill
    assert "这些分块位于 `warn`、`ref` 之前" in command_surface
