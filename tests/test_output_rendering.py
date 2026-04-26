from pathlib import Path

import pytest

import trail.output.rendering as rendering_module
from trail.cli import app
from trail.output.rendering import (
    TEXT_RENDERERS,
    _append_common_success_lines,
    _render_cw_entry,
    _render_cw_portal_cards,
    print_output,
    render_output,
    set_output_options,
)
from tests.support.fake_daemon import build_success_response


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OCR_VERBOSE_ONLY_KEYS = (
    "ocr_mode_requested",
    "ocr_mode_effective",
    "ocr_scale_applied",
    "ocr_retry_high",
    "ocr_retry_reason",
)
LEGACY_OCR_CONTEXT_DEBUG_LINES = (
    "debug kind=context key=ocr_mode_requested",
    "debug kind=context key=ocr_mode_effective",
    "debug kind=context key=ocr_scale_applied",
    "debug kind=context key=ocr_retry_high",
    "debug kind=context key=ocr_retry_reason",
)
CW_ACTION_STAGE_COMMANDS = ("cw.battle.start", "cw.battle.continue", "cw.settle.next")
SCREENSHOT_FIRST_RULE_SNIPPET = (
    "如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续"
)
SKILL_GUIDANCE_PATHS = (
    PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md",
    PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md",
)
CW_ENTRY_SKILL_PATH = PROJECT_ROOT / "skills" / "trail-cw-entry" / "SKILL.md"
CW_ENTRY_PLAYER_LANGUAGE_MAPPING_PATH = (
    PROJECT_ROOT / "skills" / "trail-cw-entry" / "references" / "player-language-mapping.md"
)
CW_ENTRY_CONFIRMATION_CHECKLIST_PATH = (
    PROJECT_ROOT / "skills" / "trail-cw-entry" / "references" / "confirmation-checklist.md"
)
SIMPLE_COMMAND_SURFACE_PATH = (
    PROJECT_ROOT / "skills" / "trail-hsr" / "references" / "simple-command-surface.md"
)
OCR_AND_SCREENSHOT_REFERENCE_PATH = (
    PROJECT_ROOT / "skills" / "trail-hsr" / "references" / "ocr-and-screenshot.md"
)
ADVANCED_COMMAND_SURFACE_PATH = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr-advanced"
    / "references"
    / "advanced-command-surface.md"
)
REQUEST_STATUS_AND_TAINT_PATH = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr-advanced"
    / "references"
    / "request-status-and-taint.md"
)
RECOVERY_LADDER_PATH = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr-advanced"
    / "references"
    / "recovery-ladder.md"
)
WINDOW_LAUNCH_REFERENCE_PATH = (
    PROJECT_ROOT
    / "skills"
    / "trail-hsr-advanced"
    / "references"
    / "window-launch.md"
)
SKILL_GUIDANCE_REFERENCE_PATHS = (
    OCR_AND_SCREENSHOT_REFERENCE_PATH,
    ADVANCED_COMMAND_SURFACE_PATH,
)
ACTIVE_REFERENCE_SPEC_PATHS = (
    PROJECT_ROOT / "docs" / "superpowers" / "specs" / "2026-04-17-trail-output-format-design.md",
    PROJECT_ROOT / "docs" / "superpowers" / "specs" / "2026-04-20-cw-battle-run-design.md",
    PROJECT_ROOT / "docs" / "superpowers" / "specs" / "2026-04-20-cw-battle-action-buttons-design.md",
)


def _markdown_section(document: str, heading: str) -> str:
    marker = f"## {heading}\n"
    assert marker in document, f"missing section: {heading}"
    return document.split(marker, 1)[1].split("\n## ", 1)[0]


def _stage_payload() -> dict:
    return {
        "ok": True,
        "data": {"value": "shop", "stale": False},
        "screenshot": ".trail/shots/req-stage.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }


def _daemon_status_payload() -> dict:
    return {
        "ok": True,
        "data": {
            "install": {"protocol_version": 1},
            "runtime": {
                "state": "ready",
                "pid": 1234,
                "endpoint": "127.0.0.1:8765",
                "last_start_error": None,
            },
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }


def _assert_text_contains_in_order(text: str, *snippets: str) -> None:
    cursor = 0
    for snippet in snippets:
        index = text.find(snippet, cursor)
        assert index != -1, snippet
        cursor = index + len(snippet)


def _ocr_failure_payload(*, code: str, message: str, screenshot: str | None = None, debug: dict | None = None) -> dict:
    return {
        "ok": False,
        "data": {},
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": debug,
        "error": {"code": code, "message": message},
    }


def _cw_strategy_cards_payload(*, screenshot: str | None = None, cards: list[dict] | None = None) -> dict:
    payload = {
        "ok": True,
        "data": {
            "cards": cards
            if cards is not None
            else [
                {
                    "card_idx": 1,
                    "strategy_title": "回蓝",
                    "strategy_description": "启动回转",
                    "refresh_count": 0,
                    "guide_match": "优选",
                    "guide_loaded": 1,
                },
                {
                    "card_idx": 2,
                    "strategy_title": "暴击",
                    "strategy_description": "爆发增伤",
                    "refresh_count": 2,
                    "guide_match": "否",
                    "guide_loaded": 0,
                },
            ],
        },
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    if screenshot is not None:
        payload["image_guidance"] = {"read_image_first": True}
    return payload


def _cw_strategy_select_payload(*, screenshot: str | None = None) -> dict:
    payload = {
        "ok": True,
        "data": {
            "card_idx": 2,
            "strategy_title": "回蓝",
            "strategy_description": "启动回转",
            "refresh_count": 1,
            "guide_match": "次选",
            "guide_loaded": 0,
        },
        "screenshot": screenshot,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    if screenshot is not None:
        payload["image_guidance"] = {"read_image_first": True}
    return payload


@pytest.fixture(autouse=True)
def reset_output_options():
    set_output_options(output_format="text", verbose=False)
    yield
    set_output_options(output_format="text", verbose=False)


def test_render_output_adds_read_image_first_after_shot_for_stage_success():
    payload = _stage_payload()

    assert render_output("cw.stage.detect", payload).splitlines() == [
        "ok cw.stage.detect stage=shop stale=0",
        "shot path=.trail/shots/req-stage.png",
        "info read_image_first=1",
    ]


def test_render_output_failure_does_not_render_read_image_first_line():
    payload = {
        "request_id": "req-fail",
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-fail.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": {"code": "WINDOW_NOT_FOUND", "message": "window missing"},
    }

    rendered = render_output("ocr.read", payload)

    assert "info read_image_first=1" not in rendered
    assert rendered.splitlines() == [
        "fail ocr.read code=WINDOW_NOT_FOUND",
        "request id=req-fail",
        "shot path=.trail/shots/req-fail.png",
        'why msg="window missing"',
    ]


def test_readme_mentions_text_output_protocol() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "- 默认输出是紧凑文本协议，统一首行为 `<ok|fail> <command> <核心事实...>`，例如 `ok cw.shop.status count=2`" in readme
    assert (
        "- 默认模式是常规消费层；`--format yaml` 是结构化兜底，`--verbose` 是开发/排障层，不应作为终端 Agent 的常规依赖"
        in readme
    )
    assert (
        "- 默认模式绝不输出 YAML；只有显式指定 `--format yaml` 且命令进入 allowlist 时，才会在首行摘要后追加结构化块"
        in readme
    )
    assert (
        "- `shot path=...` 表示当前命令结果对应的截图路径；带截图的 success 结果会先输出 `shot path=...`，再输出 `info read_image_first=1`，然后才是实体行"
        in readme
    )
    assert "- `info read_image_first=1` 只出现在带截图的 success 文本路径，表示 Agent 必须先阅读本次命令返回的原始截图，再参考后续压缩文本" in readme
    assert "只要当前命令有截图，就会先输出 `shot path=...`，再输出 `info read_image_first=1`" not in readme
    assert "- 默认失败路径只要当前结果携带 `request_id`，就会保留 `request id=<id>`，用于恢复与排障" in readme
    assert (
        "- 只有结果未知或当前失败显式可恢复时，才会出现 `recover action=daemon.request_status request=<id>`；仅有 `request id=<id>` 不等于当前失败一定可恢复"
        in readme
    )
    assert (
        "- `tainted=1` 表示当前 failure 或状态带有运行态污染风险；继续执行前，先确认请求终态，再决定是否执行 `trail daemon reconcile-session --session <id>`"
        in readme
    )
    assert (
        "- `--verbose` 只追加 `debug kind=...` 调试行，不改变默认文本协议里的事实集合与顺序"
        in readme
    )
    assert (
        "```text\nok guide.fetch.cw 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2 最低金币=40 最低等级=7 中期等级=8\nguide 攻略标签=#7级搜牌|#银河学者|#适用超频博弈|#专家顾问\nguide 羁绊列表=1智识|1巡猎|2量子\nguide 投资环境=商店|事件 优选投资策略=快攻|回蓝 次选投资策略=暴击|连携\nguide 简易装备优先度=升级|买卡|打精英 进阶装备优先度=希儿|停云\nguide 阶段=前期阵容 前台=黑塔/star:1/rarity:1 后台=艾丝妲/star:1/rarity:1 羁绊=1智识\nguide 阶段=最终阵容 前台=希儿/carry:1/star:3/rarity:3 后台=佩拉/star:2/rarity:2 羁绊=1巡猎|2量子\nguide 阶段=最终阵容 角色=希儿 优选装备=高周波电锯|战场进化手册 次选装备=胜利之旗\nguide 运营思路=\"前期：过渡\\n中期：D牌\\n后期：补强\"\n```"
        in readme
    )
    assert (
        "```text\nok guide.list.cw count=2 more=1 next=token-2\nguide 攻略ID=abc 攻略标题=7群攻2银河学者 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45\nguide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3\n```"
        in readme
    )
    assert "guide 投资环境=购物区 count=1 more=1 next=group-token" in readme
    assert "guide.config.cw --format yaml" in readme
    assert "`trail guide fetch cw` 默认文本会直接返回你选中的完整攻略字段，字段名尽量使用货币战争页面里的中文文案；现在还会补充 `羁绊列表`、`运营思路`" in readme
    _assert_text_contains_in_order(
        readme,
        "ok ocr.read hits=2",
        "shot path=.trail/shots/req-ocr.png\ninfo read_image_first=1",
        "text value=点击进入",
    )
    _assert_text_contains_in_order(
        readme,
        "ok cw.shop.scan opened=1 stale=0 count=2",
        "shot path=.trail/shots/req-shop.png\ninfo read_image_first=1",
        "item idx=1 slot=1 name=希儿 cost=2",
        "info coins=40 reserve_full=0",
        "info stage_level=7 stage_exp=4/52 stage_team_size=3/3 stage_status_stale=0",
    )
    assert "```text\nok cw.shop.status count=2\nshot path=.trail/shots/req-shop.png\n" not in readme
    assert "`trail cw slots read` 会顺带刷新 `cw_state.stage.status`" in readme
    assert "`trail cw shop scan` 只扫描商店页商品/金币切片，不重新识别全局状态" in readme
    assert "`trail cw shop scan` 与 `trail cw shop status` 只投影 session 中已有的 `cw_state.stage.status`" in readme
    assert "`trail cw shop buy-exp` 是 shop action renderer family" in readme
    assert "`team_size=null` 是 must-keep null fact" in readme
    _assert_text_contains_in_order(
        readme,
        "ok cw.shop.buy_exp opened=1 stale=0 count=1",
        "shot path=.trail/shots/req-buy-exp.png\ninfo read_image_first=1",
        "item idx=1 slot=1 name=灵砂 cost=3",
        "info coins=36 level=4 exp=0/8 reserve_full=0 team_size=4/4",
    )
    assert "商店快照里的 `coins` / `level` / `exp` / `reserve_full` / `team_size` 当前只在 `trail cw shop scan` 与 `trail cw shop status` 暴露" not in readme
    assert "`guide.fetch.cw` 现在也进入 YAML allowlist" in readme
    assert "`羁绊列表`：按当前攻略各阶段阵容里出现过的羁绊去重汇总，并尽量保留层数" in readme
    assert "`优选装备` / `次选装备`：按角色展开的推荐装备列表" in readme
    assert "`运营思路`：取自攻略详情原始 `description` 文本" in readme
    assert "`最低金币`：这套攻略默认要求保留的最低金币阈值" in readme
    _assert_text_contains_in_order(
        readme,
        "fail input.click code=INPUT_BACKEND_MISSING tainted=1",
        "request id=req-42",
        'why msg="input backend missing"',
        "recover action=daemon.request_status request=req-42",
    )
    assert "trail daemon request-status --request-id <id>" in readme
    assert "默认输出结构化 envelope" not in readme
    assert "ok/data/screenshot/debug" not in readme


def test_agents_document_screenshot_first_protocol_facts() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert (
        "success 路径必须先输出首行，再按需要输出 `shot`；若当前结果带截图，再紧跟 `info read_image_first=1`；然后才是 `item`、`guide`、`text`、`slot`、`opt`、其余 `info` 这类实体行"
        in agents
    )
    assert (
        "若命令命中已配置 workflow handoff，success 路径允许在 `warn`、`ref` 之后追加一行尾行强提示 `info handoff_skill=... handoff_strength=... handoff_reason=...`，且该行必须是 success 输出最后一行。"
        in agents
    )
    assert "envelope 顶层若带 `screenshot`，同步生成 `image_guidance.read_image_first=1`" in agents
    assert "`image_guidance` 不进入 YAML body" in agents
    assert "`--verbose` 不为 `image_guidance` 新增独立 guidance 事件" in agents
    assert "`cw.shop.scan|status` 的 stage 投影固定使用 `stage_level/stage_exp/stage_team_size/stage_status_stale`" in agents


def test_readme_documents_verbose_major_action_trace_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "major action trace" in readme
    assert "step=<helper>" in readme
    assert "UTC RFC3339" in readme
    assert "ok=0|1" in readme
    assert "`trace` 只承载 finalized helper 动作事件" in readme
    assert "`context` 只承载跨动作请求级事实" in readme
    assert "`debug kind=trace step=ocr ...`" in readme


def test_agents_document_verbose_major_action_trace_contract() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "`--verbose` 只追加开发/排障层，不改变默认文本协议的事实集合和顺序。" in agents
    assert "major action trace 固定输出" in agents
    assert "UTC RFC3339" in agents
    assert "ok=0|1" in agents
    assert "trace/context" in agents
    assert "`trace` 只承载 finalized helper 动作事件" in agents
    assert "`context` 只承载跨动作请求级事实" in agents
    assert "debug kind=trace step=ocr ..." in agents
    assert "不再作为 top-level debug context 暴露" in agents
    assert "best-effort" in agents


def test_skills_document_screenshot_first_guidance_rule() -> None:
    for path in SKILL_GUIDANCE_REFERENCE_PATHS:
        text = path.read_text(encoding="utf-8")

        assert SCREENSHOT_FIRST_RULE_SNIPPET in text, path.as_posix()
        assert "`shot path=...`" in text, path.as_posix()
        assert "`info read_image_first=1`" in text, path.as_posix()


def test_agents_active_specs_document_screenshot_first_override() -> None:
    override_note = "若本文旧示例与当前 screenshot-first guidance 冲突，以 `2026-04-20-screenshot-first-guidance-design.md` 为准。"

    for path in ACTIVE_REFERENCE_SPEC_PATHS:
        text = path.read_text(encoding="utf-8")

        assert override_note in text, path.as_posix()
        assert "`info read_image_first=1`" in text, path.as_posix()

        if path.name == "2026-04-20-cw-battle-run-design.md":
            assert "主工作区" not in text, path.as_posix()
            assert "同名设计文档" not in text, path.as_posix()

        if path.name == "2026-04-17-trail-output-format-design.md":
            assert "`cw.shop.status` 现已收紧为无图的 session / artifact 汇总读" in text, path.as_posix()
            assert "带图示例改看 `cw.shop.scan`" in text, path.as_posix()
            assert "| `cw.shop.status` | `count`、每个 `item` 的 `slot/name/cost`；不再要求 `shot`，带图示例改看 `cw.shop.scan` |" in text, path.as_posix()
            assert "| `cw.shop.scan` | `opened`、`stale`、`count`、每个 `item` 的 `slot/name/cost`、`shot` |" in text, path.as_posix()
            assert "| `cw.shop.status` | `count`、每个 `item` 的 `slot/name/cost`、`shot` |" not in text, path.as_posix()


def test_readme_documents_ocr_provider_and_lang_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "trail ocr read --provider auto|cpu|dml" in readme
    assert "trail ocr read --lang ch" in readme
    assert "首版仅支持 `ch`" in readme
    assert "TRAIL_OCR_PROVIDER`、`TRAIL_OCR_LANG`、`TRAIL_OCR_USE_CLS`、`TRAIL_OCR_TEXT_SCORE" in readme
    assert "provider=auto` 会优先尝试 DirectML；如果当前环境不可用或本次 DML 推理失败，会自动回退 CPU" in readme
    assert "provider=dml` 会把 DirectML 视为硬约束；环境不可用或推理期 DML 失败都会返回 `OCR_PROVIDER_UNAVAILABLE`" in readme
    assert "lang` 首版仅支持 `ch`；其他值返回 `OCR_LANG_UNSUPPORTED`" in readme


def test_readme_documents_ocr_mode_and_retry_high_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "trail ocr read --ocr-mode fast|high" in readme
    assert "trail ocr read --retry-high auto|never|always" in readme
    assert "TRAIL_OCR_MODE`、`TRAIL_OCR_RETRY_HIGH` 用于设置低优先级默认值" in readme
    assert "默认 `ocr_mode=fast`" in readme
    assert "默认 `retry_high=auto`" in readme
    assert "`fast = 1280x720`" in readme
    assert "`high = native`" in readme
    assert "`retry_high=auto` 只在 `hits==0`、平均分过低、或出现 `OCR_LOW_CONFIDENCE` 时触发" in readme
    assert "`retry_high=always` 在 `ocr_mode=fast` 下会先跑 `fast`，再无条件补跑一次 `high`" in readme
    assert "`ocr_mode=high` 下 `retry_high` 为 no-op" in readme
    assert "模式与重试事实只在 `--verbose` 下出现" in readme


def test_readme_documents_trail_start_as_default_entry() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = readme.split("## Quick Start", 1)[1].split("## ", 1)[0]

    assert "trail start" in quick_start
    assert "trail ocr read" in quick_start
    assert "trail input" in quick_start
    assert "trail daemon install" not in quick_start
    assert "trail daemon status" not in quick_start
    assert "trail daemon start" not in quick_start
    assert "trail daemon request-status" not in quick_start
    assert "trail daemon reconcile-session" not in quick_start
    assert "trail window launch" not in quick_start
    assert "trail window attach" not in quick_start
    assert "trail session create" not in quick_start
    assert "trail screen shot" not in quick_start
    assert "trail image" not in quick_start
    assert "trail state dump" not in quick_start


def test_readme_and_active_skills_document_help_boundaries() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    hsr_skill = (PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md").read_text(encoding="utf-8")
    hsr_advanced_skill = (PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md").read_text(encoding="utf-8")
    simple_command_surface = SIMPLE_COMMAND_SURFACE_PATH.read_text(encoding="utf-8")
    advanced_command_surface = ADVANCED_COMMAND_SURFACE_PATH.read_text(encoding="utf-8")
    request_status_and_taint = REQUEST_STATUS_AND_TAINT_PATH.read_text(encoding="utf-8")

    assert "货币战争固定流程命令；`enter` 到首页，`start` 从首页进入投资环境页" in readme
    assert "常规 battle / settle 流程默认执行：`trail cw battle run --session <id>`；默认 timeout 现在是 `90s`" in readme
    assert "info next_action=cw.battle.run why=battle_flow_not_finished" in readme
    assert "结算页也属于 battle flow" in readme
    assert "`trail state dump --session <id> --format yaml`" in readme
    assert "`trail cw stage` 只适用于已进入货币战争后的内部阶段快速检测/等待，不用于登录页、大世界等非 CW 场景判断" in readme
    assert "`cw`：货币战争固定流程命令" in readme
    assert "`stage` 只用于已进入货币战争后的内部阶段快速检测/等待" in readme
    assert "`trail cw guide` 只负责当前对局已选攻略的 current/apply" in readme
    assert "`trail cw invest read|choose` 继续只表示局内 invest 事件" in readme
    assert "`trail cw portal select --session <id> --card-idx <n>`" in readme
    assert "`trail cw portal select|detect|refresh|restart` 只用于首页之后的投资环境选择页" in readme
    assert "`trail-hsr` 是对外总入口" in readme
    assert "`trail-<scene>-entry` 是对外场景入口" in readme
    assert "`trail-hsr-advanced` 是内部恢复层" in readme
    assert "`trail-hsr-advanced` 不作为用户入口" in readme
    assert "guide 投资环境=购物区 count=1 more=1 next=group-token" in readme
    assert "guide.config.cw --format yaml" in readme
    assert "artifact=" not in readme
    assert (
        "```text\nok guide.list.cw count=2 more=1 next=token-2\nguide id=abc idx=1 carry=希儿 hard=1 change_equip=0 expert=1\nguide id=def idx=2 hard=0 change_equip=1 expert=0\n```"
        not in readme
    )
    assert (
        "通用场景判断继续走 `trail start` / `trail ocr read` / `trail input ...`，不要把 `trail cw stage` 当成登录页、大世界等非 CW 场景检测器"
        in simple_command_surface
    )
    assert "artifact=" not in hsr_skill
    assert "artifact=" not in hsr_advanced_skill
    assert "trail daemon install" not in hsr_skill
    assert "trail daemon status" not in hsr_skill
    assert "trail daemon request-status --request-id <id>" in request_status_and_taint
    assert "trail daemon reconcile-session --session <id>" in request_status_and_taint
    assert "trail state dump --session <id> --format yaml" in advanced_command_surface


def test_readme_documents_battle_run_short_timeout_and_resume_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    stage_reference = (PROJECT_ROOT / "docs" / "cw-stage-reference" / "README.md").read_text(encoding="utf-8")
    entry_skill = CW_ENTRY_SKILL_PATH.read_text(encoding="utf-8")
    simple_surface = SIMPLE_COMMAND_SURFACE_PATH.read_text(encoding="utf-8")
    cw_flow_section = _markdown_section(readme, "货币战争流程")
    command_overview_section = _markdown_section(readme, "命令面概览")
    skill_boundary_section = _markdown_section(readme, "Skill 边界")
    settle_reference_section = _markdown_section(stage_reference, "08-cw-round-settle-success.jpg")
    entry_battle_resume_section = _markdown_section(entry_skill, "Battle Flow Resume")

    for section in (cw_flow_section, command_overview_section, skill_boundary_section):
        assert "默认 timeout 现在是 `90s`" in section
        assert "结算页也属于 battle flow" in section
        assert "info next_action=cw.battle.run why=battle_flow_not_finished" in section
        assert "trail cw battle clear-in-progress --session <id>" in section
        assert "只清内部提示位" in section
        assert "--timeout 570" not in section
        assert "timeout 570" not in section
        assert "570s" not in section

    assert "结算页也属于 battle flow" in settle_reference_section
    assert "`trail cw battle run --session <id>`" in settle_reference_section
    assert "若要继续当前对局的下一小节，运行" not in settle_reference_section
    assert "- `trail cw settle next --session <id>`" not in settle_reference_section
    assert "--timeout 570" not in settle_reference_section
    assert "570s" not in settle_reference_section

    assert "默认 timeout 现在是 `90s`" in entry_battle_resume_section
    assert "返回 `status=in_progress` 时，先读取本次截图" in entry_battle_resume_section
    assert "结算页也属于 battle flow" in entry_battle_resume_section
    assert "trail cw battle clear-in-progress --session <id>" in entry_battle_resume_section
    assert "只清 battle.run 的内部续跑提示位" in entry_battle_resume_section
    assert "不实现新的 skill 本体" in entry_battle_resume_section
    assert "--timeout 570" not in entry_battle_resume_section
    assert "570s" not in entry_battle_resume_section

    _assert_text_contains_in_order(
        simple_surface,
        "battle in-progress 当前只是场景/命令说明，不实现新的 skill 本体",
        "info next_action=cw.battle.run why=battle_flow_not_finished",
        "若仍在 battle flow 中，就继续运行 `trail cw battle run --session <id>`",
        "结算页也属于 battle flow",
        "默认 timeout 现在是 `90s`",
        "trail cw battle clear-in-progress --session <id>",
        "只清 battle.run 的内部续跑提示位",
    )
    assert "--timeout 570" not in simple_surface
    assert "570s" not in simple_surface

    assert "默认 timeout 现在是 `90s`" in readme
    assert "结算页也属于 battle flow" in readme
    assert "info next_action=cw.battle.run why=battle_flow_not_finished" in readme
    assert "trail cw battle clear-in-progress --session <id>" in readme
    assert "cw.battle.clear_in_progress" in agents
    assert "不加入 YAML allowlist" in agents
    assert "结算页也属于 battle flow" in stage_reference
    assert "仍在 battle flow 中就继续运行 `trail cw battle run --session <id>`" in entry_skill
    assert "battle in-progress" in simple_surface


def test_readme_documents_portal_detect_recovery_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    cw_flow_section = _markdown_section(readme, "货币战争流程")

    _assert_text_contains_in_order(
        cw_flow_section,
        "- 如果已经手动进入投资环境页，但 `cw start` 中途失败或 session 没有 fresh portal snapshot，使用 `trail cw portal detect --session <id>`；不要重复执行 `trail cw start`",
        "- `detect = 重识别当前三张卡，不点击`",
        "- detect 后可直接 `select`",
        "- `restart` 依旧要求已有开局真值；detect 不会补录 `mode/difficulty/battle_mode`",
    )
    assert "detect 后需要先 `trail cw portal refresh --session <id>`" not in cw_flow_section
    assert "detect 会补录 `mode/difficulty/battle_mode`" not in cw_flow_section


def test_readme_cw_start_exact_rank_documents_public_difficulty_surface() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "`trail cw start --session <id> --mode new|continue --difficulty lowest|current|highest|AX-X --battle-mode standard|overclock`"
        in readme
    )
    assert "`A0-1..A8-40`" in readme
    assert "`A7-3`" in readme
    assert "enemy_difficulty" not in readme
    assert "A7-3 对应难度 51" not in readme
    assert "--rank" not in readme


def test_skill_cw_start_exact_rank_documents_public_difficulty_surface() -> None:
    skill_text = CW_ENTRY_SKILL_PATH.read_text(encoding="utf-8")
    mapping_text = CW_ENTRY_PLAYER_LANGUAGE_MAPPING_PATH.read_text(encoding="utf-8")
    checklist_text = CW_ENTRY_CONFIRMATION_CHECKLIST_PATH.read_text(encoding="utf-8")
    combined = "\n".join((skill_text, mapping_text, checklist_text))

    assert "继续当前职级" in skill_text
    assert "回最高职级" in skill_text
    assert "AX-X" in skill_text
    assert "A0-1..A8-40" in combined
    assert "A7-3" in combined
    assert "difficulty=highest" in mapping_text
    assert "difficulty=AX-X" in mapping_text
    assert "继续当前职级" in checklist_text
    assert "回最高职级" in checklist_text
    for text in (skill_text, mapping_text, checklist_text):
        assert "enemy_difficulty" not in text
        assert "A7-3 对应难度 51" not in text
        assert "--rank" not in text


def test_readme_documents_handoff_tail_rule() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "- 已配置 workflow handoff 的 success 结果会在正常 success 内容、`warn`、`ref` 之后，额外追加一行尾行强提示：`info handoff_skill=... handoff_strength=... handoff_reason=...`；它始终是 success 输出最后一行"
        in readme
    )


def test_readme_documents_strategy_page_boundary() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`stage=invest` 时，不要默认走 `trail cw invest.*`" in readme
    assert (
        "如果 screenshot 或页面标题显示“请选择投资策略”，局内投资策略页应优先使用 `trail cw strategy detect|refresh|select`"
        in readme
    )
    assert "开局投资环境页仍是 `trail cw portal.*`" in readme
    assert "普通局内 invest 事件的兼容/粗粒度入口才是 `trail cw invest.*`" in readme
    assert "`strategy` 负责局内“请选择投资策略”页的识别/单卡刷新/选择" in readme
    assert "`invest` 只保留普通局内 invest 事件的兼容/粗粒度入口" in readme


def test_readme_documents_selected_guide_and_portal_auto_apply_flow() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    cw_flow_section = _markdown_section(readme, "货币战争流程")
    guide_section = _markdown_section(readme, "Guide 字段语义")
    skill_boundary_section = _markdown_section(readme, "Skill 边界")

    _assert_text_contains_in_order(
        cw_flow_section,
        "- 先用：`trail guide fetch cw <lineup_url_or_id> --select --session <id>` 将完整攻略写入当前 session；这一步不执行 UI 应用，也不创建当前攻略快照或额外追踪产物",
        "- 回到开局链路后，`trail cw portal select --session <id> --card-idx <n>` 成功后会自动应用当前已选攻略",
        "- `cw.portal.select` 成功进入普通备战后会自动收集水晶、slots/shop 预备事实并关闭商店；输出仍要求先读截图，再消费 `slot`、羁绊 `info`、商店 `item`、coins/stage `info`，最后按 handoff 切到 internal 的 `trail-cw-prep`",
        "- `trail cw guide apply --session <id>` 只作为手动兜底；如需回顾当前已选攻略：`trail cw guide current --session <id>`",
    )
    _assert_text_contains_in_order(
        guide_section,
        "- `trail guide fetch cw` 默认是完整攻略预览，不会建立当前已选攻略；确认后用 `trail guide fetch cw <lineup_url_or_id> --select --session <id>` 将完整攻略写入 session，不创建当前攻略快照或额外追踪产物",
        "- `trail cw portal select --session <id> --card-idx <n>` 成功后会自动应用当前已选攻略；`trail cw guide apply` 只作为手动兜底",
        "- `trail guide fetch cw` 负责看完整攻略；`trail cw guide current|apply` 负责回顾当前已选攻略；两者不要混用",
    )
    assert (
        "- `trail cw guide current|apply` 只返回当前已选攻略摘要：首行看 `攻略ID/攻略标题/攻略码/版本`，正文按需补 `guide 攻略标签=...`"
        in guide_section
    )
    assert (
        "- 货币战争里，先用 `trail guide fetch cw <lineup_url_or_id> --select --session <id>` 将完整攻略写入 session；回到开局链路后由 `trail cw portal select` 成功时自动应用，`trail cw guide apply` 只作为手动兜底"
        in skill_boundary_section
    )
    assert "info 攻略快照ID" not in readme
    assert "攻略快照ID" not in readme
    assert "current-guide artifact" not in readme
    assert "artifact id / 恢复追踪 id" not in readme
    assert "当前已应用攻略摘要" not in readme
    assert "在进入游戏并完成投资环境选择后，再执行：`trail cw guide apply --session <id> --lineup-id <lineup_id>`" not in readme


def test_readme_locks_cw_portal_select_success_screenshot_order() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    portal_example = (
        readme.split("```text\nok cw.portal.select idx=1 投资环境=击破概念股\n", 1)[1]
        .split("\n```", 1)[0]
    )
    portal_lines = ["ok cw.portal.select idx=1 投资环境=击破概念股", *portal_example.splitlines()]

    _assert_text_contains_in_order(
        "\n".join(portal_lines),
        "ok cw.portal.select idx=1 投资环境=击破概念股",
        "shot path=.trail/shots/req-portal-select.png",
        "info read_image_first=1",
        "info skill_info=运营思路 text=前期：先收集事实，再按后续策略处理",
        "slot pos=front:0 name=希儿 star=1 traits=巡猎",
        'info 羁绊=巡猎 档位="1,2" 当前角色=1 已激活档位=1/2 占比=0.50',
        "item idx=1 slot=1 name=银狼 cost=20",
        "info coins=40 reserve_full=0",
        "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
        "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered",
    )
    assert portal_lines[1:3] == [
        "shot path=.trail/shots/req-portal-select.png",
        "info read_image_first=1",
    ]
    assert portal_lines[-1] == "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"
    assert "`cw.portal.select` 应用攻略后会自动收集水晶、读取槽位、扫描商店并关闭商店" in readme
    assert "最终截图停留在无浮层普通备战页" in readme
    assert "即使 `cw.portal.select` 已输出 slots/shop 文本事实，Agent 仍必须先读 `shot path=...` 对应原始截图" in readme
    assert " opened=" not in portal_example
    assert " stale=" not in portal_example


def test_agents_document_cw_portal_select_auto_collect_contract() -> None:
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    _assert_text_contains_in_order(
        agents,
        "`cw.portal.select` success 首行固定为 `ok cw.portal.select idx=... 投资环境=...`",
        "`cw.portal.select` 成功进入备战页后会自动收集 slots/shop 预备事实",
        "`shot path=...` 后必须紧跟 `info read_image_first=1`",
        "`info skill_info=运营思路 text=...`",
        "（如有）之后复用 `cw.slots.read` 的 `slot` 行与羁绊 `info` 摘要",
        "复用商店 `item` 行与 `info coins/reserve_full/stage_level/stage_exp/stage_team_size/stage_status_stale` 投影",
        "`warn`、`ref` 之前输出",
        "success 最后一行必须是 `info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered`",
    )
    assert "自动收集得到的 shop `opened/stale` 不进入首行，也不作为 body 事实渲染" in agents
    assert "不要因为已有 slots/shop 文本就跳过截图" in agents


def test_readme_routes_stage_invest_to_strategy() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "如果 screenshot 或页面标题显示“请选择投资策略”" in readme
    assert "优先使用 `trail cw strategy detect|refresh|select`" in readme
    assert "开局投资环境页仍是 `trail cw portal.*`" in readme
    assert "普通局内 invest 事件的兼容/粗粒度入口才是 `trail cw invest.*`" in readme
    assert "`优选投资策略` / `次选投资策略`：是局内 `cw.strategy.detect|refresh` 里 `攻略推荐=优选|次选|否` 的来源，不用于 `cw.portal.*`" in readme


def test_strategy_protocol_is_frozen_in_readme_and_agents() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "`cw.strategy.detect|refresh` 的 `info 已加载攻略=0|1` 固定在所有 `opt` 行之后" in readme
    assert (
        "```text\nok cw.strategy.detect cards=2\nshot path=.trail/shots/req-strategy-detect.png\ninfo read_image_first=1\nopt idx=1 投资策略=回蓝 攻略推荐=优选 刷新次数=0\nopt idx=1 说明=启动回转\nopt idx=2 投资策略=暴击 攻略推荐=否 刷新次数=2\nopt idx=2 说明=爆发增伤\ninfo 已加载攻略=1\n```"
        in readme
    )
    assert (
        "```text\nok cw.strategy.select idx=2 投资策略=回蓝\nshot path=.trail/shots/req-strategy-select.png\ninfo read_image_first=1\n```"
        in readme
    )
    assert (
        "`优选投资策略` / `次选投资策略`：是局内 `cw.strategy.detect|refresh` 里 `攻略推荐=优选|次选|否` 的来源，不用于 `cw.portal.*`"
        in readme
    )
    assert "`cw.strategy.detect|refresh` success 首行固定为 `ok cw.strategy.<...> cards=<n>`" in agents
    assert "`cw.strategy.select` success 首行固定为 `ok cw.strategy.select idx=... 投资策略=...`" in agents
    assert "`cw.strategy.detect|refresh` 的 cards family 正文字段固定使用 `投资策略/攻略推荐/刷新次数`" in agents
    assert "`cw.strategy.detect|refresh` 必须输出 `info 已加载攻略=0|1`，且固定在所有 `opt` 行之后" in agents


def test_readme_includes_cw_strategy_refresh_example_and_flow() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "```text\nok cw.strategy.refresh cards=2\nshot path=.trail/shots/req-strategy-refresh.png\ninfo read_image_first=1\nopt idx=1 投资策略=连携 攻略推荐=否 刷新次数=1\nopt idx=1 说明=补充连段\nopt idx=2 投资策略=回蓝 攻略推荐=优选 刷新次数=0\nopt idx=2 说明=启动回转\ninfo 已加载攻略=1\n```"
        in readme
    )
    assert (
        "策略页显式流程示例：`trail cw stage detect --session <id>` -> `trail cw strategy detect --session <id>` -> `trail cw strategy refresh --session <id> --card-idx <n>` -> `trail cw strategy select --session <id> --card-idx <n>`"
        in readme
    )


def test_battle_run_as_default_entry_is_documented_across_readme_and_skills() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    cw_flow_section = _markdown_section(readme, "货币战争流程")
    command_overview_section = _markdown_section(readme, "命令面概览")
    skill_boundary_section = _markdown_section(readme, "Skill 边界")
    stage_reference = (PROJECT_ROOT / "docs" / "cw-stage-reference" / "README.md").read_text(encoding="utf-8")
    preparation_stage_section = _markdown_section(stage_reference, "06-cw-preparation-stage.jpg")
    advanced_command_surface = ADVANCED_COMMAND_SURFACE_PATH.read_text(encoding="utf-8")

    assert "`trail cw battle run --session <id>`；默认 timeout 现在是 `90s`" in cw_flow_section
    assert "info next_action=cw.battle.run why=battle_flow_not_finished" in cw_flow_section
    assert "结算页也属于 battle flow" in cw_flow_section
    assert "`trail state dump --session <id> --format yaml`" in cw_flow_section
    assert "`trail cw battle start` / `trail cw battle continue` / `trail cw settle next`" in cw_flow_section
    assert "只建议在内部 fallback 流程中手工拆链使用" in cw_flow_section
    assert "`battle` / `settle` 分组仍保留兼容原子命令" in command_overview_section
    assert "常规 battle / settle 默认入口是 `trail cw battle run --session <id>`" in command_overview_section
    assert "默认 timeout 现在是 `90s`" in command_overview_section
    assert "只建议在内部 fallback 流程使用" in command_overview_section
    assert "`trail-hsr` 是对外总入口" in skill_boundary_section
    assert "`trail-<scene>-entry` 是对外场景入口" in skill_boundary_section
    assert "当前 scene entry 一旦命中并接管某个具体场景，该 scene entry 就成为该场景内的唯一编排 owner" in skill_boundary_section
    assert "`trail-hsr-advanced` 是内部恢复层" in skill_boundary_section
    assert "`trail-hsr-advanced` 不作为用户入口" in skill_boundary_section
    assert "`trail state dump --session <id> --format yaml`" in advanced_command_surface
    assert "`trail cw battle run --session <id>`" in preparation_stage_section
    assert "默认 timeout 现在是 `90s`" in preparation_stage_section
    assert "`trail cw battle start --session <id>`" in preparation_stage_section
    assert "advanced/manual fallback" in preparation_stage_section


def test_render_output_renders_canonical_stage_wait_text():
    payload = _stage_payload()

    assert render_output("cw.stage.wait", payload).splitlines() == [
        "ok cw.stage.wait stage=shop stale=0",
        "shot path=.trail/shots/req-stage.png",
        "info read_image_first=1",
    ]


@pytest.mark.parametrize("command", CW_ACTION_STAGE_COMMANDS)
def test_render_output_renders_cw_action_stage_commands_with_shot(command: str):
    assert render_output(command, _stage_payload()).splitlines() == [
        f"ok {command} stage=shop stale=0",
        "shot path=.trail/shots/req-stage.png",
        "info read_image_first=1",
    ]


@pytest.mark.parametrize("command", CW_ACTION_STAGE_COMMANDS)
def test_render_output_cw_action_stage_commands_reject_yaml_output(command: str):
    assert render_output(command, _stage_payload(), output_format="yaml").splitlines() == [
        f"fail {command} code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-stage.png",
        f'why msg="yaml not supported for {command}"',
    ]


@pytest.mark.parametrize("command", CW_ACTION_STAGE_COMMANDS)
def test_render_output_renders_cw_action_stage_commands_stale_only_payload_without_stage(command: str):
    payload = {
        **_stage_payload(),
        "data": {"stale": True},
        "screenshot": ".trail/shots/req-stage-stale.png",
    }

    assert render_output(command, payload).splitlines() == [
        f"ok {command} stale=1",
        "shot path=.trail/shots/req-stage-stale.png",
        "info read_image_first=1",
    ]


def test_render_output_cw_battle_run_completed_summary():
    payload = {
        "ok": True,
        "data": {
            "status": "completed",
            "result": "win",
            "stage": "shop",
            "stale": False,
            "in_battle": False,
            "round": "1-1",
            "hp": 82,
            "coins": 4,
            "exp": 2,
            "settle_text": "挑战成功",
        },
        "screenshot": ".trail/shots/req-cw-battle-run.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=completed result=win stage=shop stale=0 in_battle=0",
        "shot path=.trail/shots/req-cw-battle-run.png",
        "info read_image_first=1",
        "info round=1-1 hp=82 coins=4 exp=2",
        "info settle_text=挑战成功",
    ]


def test_render_output_cw_battle_run_timeout_in_settle_chain():
    payload = {
        "ok": True,
        "data": {
            "status": "in_progress",
            "result": "win",
            "stage": "settle",
            "stale": True,
            "in_battle": False,
            "round": "1-1",
            "hp": 82,
            "coins": 4,
            "exp": 2,
            "settle_text": "挑战成功",
            "timeout_seconds": 90,
        },
        "screenshot": ".trail/shots/req-cw-battle-run-settle-timeout.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=in_progress result=win stage=settle stale=1 in_battle=0",
        "shot path=.trail/shots/req-cw-battle-run-settle-timeout.png",
        "info read_image_first=1",
        "info round=1-1 hp=82 coins=4 exp=2",
        "info settle_text=挑战成功",
        "info timeout_seconds=90",
        "info next_action=cw.battle.run why=battle_flow_not_finished",
    ]


def test_render_output_cw_battle_run_timeout_in_battle_only():
    payload = {
        "ok": True,
        "data": {
            "status": "in_progress",
            "stale": True,
            "in_battle": True,
            "timeout_seconds": 90,
        },
        "screenshot": ".trail/shots/req-cw-battle-run-timeout.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=in_progress stale=1 in_battle=1",
        "shot path=.trail/shots/req-cw-battle-run-timeout.png",
        "info read_image_first=1",
        "info timeout_seconds=90",
        "info next_action=cw.battle.run why=battle_flow_not_finished",
    ]


def test_render_output_cw_battle_run_in_progress_adds_next_action_hint():
    payload = {
        "ok": True,
        "data": {
            "status": "in_progress",
            "stale": True,
            "in_battle": True,
            "timeout_seconds": 90,
        },
        "screenshot": ".trail/shots/req-cw-battle-run-in-progress.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=in_progress stale=1 in_battle=1",
        "shot path=.trail/shots/req-cw-battle-run-in-progress.png",
        "info read_image_first=1",
        "info timeout_seconds=90",
        "info next_action=cw.battle.run why=battle_flow_not_finished",
    ]


def test_render_output_cw_battle_run_settle_in_progress_adds_next_action_hint():
    payload = {
        "ok": True,
        "data": {
            "status": "in_progress",
            "stage": "settle",
            "stale": True,
            "in_battle": False,
            "timeout_seconds": 90,
        },
        "screenshot": ".trail/shots/req-cw-battle-run-settle-in-progress.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.run", payload).splitlines() == [
        "ok cw.battle.run status=in_progress stage=settle stale=1 in_battle=0",
        "shot path=.trail/shots/req-cw-battle-run-settle-in-progress.png",
        "info read_image_first=1",
        "info timeout_seconds=90",
        "info next_action=cw.battle.run why=battle_flow_not_finished",
    ]


def test_render_output_cw_battle_clear_in_progress_summary():
    payload = {
        "ok": True,
        "data": {"cleared": False},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.battle.clear_in_progress", payload).splitlines() == [
        "ok cw.battle.clear_in_progress cleared=0",
    ]


@pytest.mark.parametrize(
    ("payload", "expected_lines"),
    [
        (
            {
                "ok": True,
                "data": {"status": "completed", "stale": False, "in_battle": False},
                "screenshot": ".trail/shots/req-cw-battle-run-sparse-completed.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok cw.battle.run status=completed stale=0 in_battle=0",
                "shot path=.trail/shots/req-cw-battle-run-sparse-completed.png",
                "info read_image_first=1",
            ],
        ),
        (
            {
                "ok": True,
                "data": {
                    "status": "in_progress",
                    "stale": True,
                    "in_battle": False,
                    "timeout_seconds": 90,
                },
                "screenshot": ".trail/shots/req-cw-battle-run-sparse-timeout.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok cw.battle.run status=in_progress stale=1 in_battle=0",
                "shot path=.trail/shots/req-cw-battle-run-sparse-timeout.png",
                "info read_image_first=1",
                "info timeout_seconds=90",
                "info next_action=cw.battle.run why=battle_flow_not_finished",
            ],
        ),
    ],
)
def test_render_output_cw_battle_run_omits_missing_result_and_stage(payload: dict, expected_lines: list[str]):
    rendered = render_output("cw.battle.run", payload).splitlines()

    assert rendered == expected_lines
    assert all("result=unknown" not in line for line in rendered)
    assert all("stage=unknown" not in line for line in rendered)
    assert all("stage=null" not in line for line in rendered)


def test_render_output_renders_cw_shop_status_without_shot_or_guidance():
    payload = {
        "ok": True,
        "data": {
            "items": [
                {"slot": 3, "name": "布洛妮娅", "price": 4},
                {"slot": 1, "name": "希儿", "price": 2},
                {"name": "无槽位条目", "price": 9},
                {"slot": 2, "name": "停云", "price": 1},
            ],
            "coins": 40,
            "level": 7,
            "exp": "4/52",
            "reserve_full": False,
            "team_size": "7/7",
            "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.status", payload).splitlines() == [
        "ok cw.shop.status count=4",
        "item idx=1 slot=1 name=希儿 cost=2",
        "item idx=2 slot=2 name=停云 cost=1",
        "item idx=3 slot=3 name=布洛妮娅 cost=4",
        "item idx=4 name=无槽位条目 cost=9",
        "info coins=40 reserve_full=0",
    ]


def test_render_output_renders_shop_stage_status_projection():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
            "stage_status": {"stale": False, "level": 7, "exp": "4/52", "team_size": "3/3"},
            "stage_status_stale": False,
        },
        "screenshot": ".trail/shots/req-shop-stage.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.shop.scan", payload).splitlines()

    assert lines[1:3] == ["shot path=.trail/shots/req-shop-stage.png", "info read_image_first=1"]
    assert lines[-1] == "info stage_level=7 stage_exp=4/52 stage_team_size=3/3 stage_status_stale=0"


def test_render_output_sections_shop_scan_items_and_status():
    payload = {
        "ok": True,
        "data": {
            "opened": True,
            "stale": False,
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "coins": 40,
            "reserve_full": False,
            "stage": "shop",
            "stage_stale": False,
            "stage_status": {"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2"},
            "stage_status_stale": False,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.scan", payload).splitlines() == [
        "ok cw.shop.scan opened=1 stale=0 count=1",
        "# 商店信息",
        "item idx=1 slot=1 name=银狼 cost=20",
        "info coins=40 reserve_full=0",
        "# 综合信息",
        "info stage=shop stale=0",
        "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
    ]


def test_render_output_sections_shop_status_only_when_multiple_fact_groups():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.shop.status", payload).splitlines()

    assert lines == ["ok cw.shop.status count=1", "item idx=1 slot=1 name=银狼 cost=20"]
    assert not any(line.startswith("# ") for line in lines)


def test_render_output_sections_shop_status_when_stage_projection_exists():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
            "stage_status": {"stale": False, "level": 3, "exp": "0/8", "team_size": "2/2"},
            "stage_status_stale": False,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.status", payload).splitlines() == [
        "ok cw.shop.status count=1",
        "# 商店信息",
        "item idx=1 slot=1 name=银狼 cost=20",
        "# 综合信息",
        "info stage_level=3 stage_exp=0/8 stage_team_size=2/2 stage_status_stale=0",
    ]


def test_render_output_shop_status_only_stage_stale_has_no_section_heading():
    payload = {
        "ok": True,
        "data": {"items": [], "stage_status_stale": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.status", payload).splitlines() == [
        "ok cw.shop.status count=0",
        "info stage_status_stale=1",
    ]


def test_render_output_sections_shop_buy_exp_and_preserves_null_team_size():
    payload = {
        "ok": True,
        "data": {
            "opened": True,
            "stale": False,
            "items": [],
            "coins": 36,
            "level": 4,
            "exp": "0/8",
            "reserve_full": False,
            "team_size": None,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.buy_exp", payload).splitlines() == [
        "ok cw.shop.buy_exp opened=1 stale=0 count=0",
        "# 商店信息",
        "info coins=36 reserve_full=0",
        "# 综合信息",
        "info level=4 exp=0/8 team_size=null",
    ]


def test_render_output_keeps_stage_status_stale_when_missing():
    payload = {
        "ok": True,
        "data": {"stage_status_stale": True},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert "info stage_status_stale=1" in render_output("cw.shop.scan", payload).splitlines()


def test_render_output_hides_stale_stage_values_but_keeps_stale_fact():
    payload = {
        "ok": True,
        "data": {
            "stage_status": {"stale": True, "level": 7, "exp": "4/52", "team_size": "3/3"},
            "stage_status_stale": True,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    rendered = render_output("cw.shop.scan", payload)

    assert "info stage_status_stale=1" in rendered
    assert "stage_level" not in rendered
    assert "stage_exp" not in rendered
    assert "stage_team_size" not in rendered


def test_render_output_hides_legacy_top_level_stage_values_when_projection_is_stale():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
            "coins": 40,
            "level": 7,
            "exp": "4/52",
            "reserve_full": False,
            "team_size": "3/3",
            "stage_status": {"stale": True, "level": 7, "exp": "4/52", "team_size": "3/3"},
            "stage_status_stale": True,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    rendered = render_output("cw.shop.scan", payload)

    assert "info stage_status_stale=1" in rendered
    assert "level=7" not in rendered
    assert "exp=4/52" not in rendered
    assert "team_size=3/3" not in rendered
    assert "stage_level" not in rendered
    assert "stage_exp" not in rendered


@pytest.mark.parametrize(
    ("command", "payload", "expected_tokens"),
    [
        (
            "cw.stage.detect",
            {
                "ok": True,
                "data": {"value": "shop", "stale": False},
                "screenshot": ".trail/shots/req-stage-detect-focused.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok cw.stage.detect",
                "stage=shop",
                "stale=0",
                "shot path=.trail/shots/req-stage-detect-focused.png",
            ],
        ),
        (
            "cw.shop.status",
            {
                "ok": True,
                "data": {
                    "items": [
                        {"slot": 2, "name": "停云", "price": 1},
                        {"slot": 1, "name": "希儿", "price": 2},
                    ]
                },
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok cw.shop.status count=2",
                "item idx=1 slot=1 name=希儿 cost=2",
                "item idx=2 slot=2 name=停云 cost=1",
            ],
        ),
        (
            "guide.list.cw",
            {
                "ok": True,
                "data": {
                    "list": [
                        {
                            "lineup_id": "guide-1",
                            "carry_roles": ["希儿"],
                            "final_role_cards": [
                                {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                                {"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False},
                            ],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                        }
                    ],
                    "next_page_token": "next-guide-token",
                },
                "screenshot": None,
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok guide.list.cw count=1 more=1 next=next-guide-token",
                "guide 攻略ID=guide-1",
                "idx=1",
                "主C=希儿",
                "攻略标签=#适用超频博弈|#专家顾问",
                "guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|佩拉/star:4/rarity:2",
            ],
        ),
        (
            "ocr.read",
            {
                "ok": True,
                "data": {
                    "result": [
                        {
                            "text": "点击进入",
                            "score": 0.98,
                            "box": {"left": 122, "top": 88, "width": 74, "height": 20},
                        },
                        {
                            "text": "开始挑战",
                            "score": 0.93,
                            "box": {"left": 410, "top": 502, "width": 120, "height": 36},
                        },
                    ]
                },
                "screenshot": ".trail/shots/req-ocr-focused.png",
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
            [
                "ok ocr.read hits=2",
                "shot path=.trail/shots/req-ocr-focused.png",
                "text value=点击进入 box=122,88,74,20 center=159,98",
                "text value=开始挑战 box=410,502,120,36 center=470,520",
            ],
        ),
    ],
)
def test_render_output_preserves_must_keep_facts(command: str, payload: dict, expected_tokens: list[str]):
    rendered = render_output(command, payload)

    for token in expected_tokens:
        assert token in rendered


def test_render_output_ocr_read_OCR_PROVIDER_UNAVAILABLE_is_hard_failure():
    payload = _ocr_failure_payload(
        code="OCR_PROVIDER_UNAVAILABLE",
        message="requested dml provider unavailable",
        screenshot=".trail/shots/req-ocr-dml.png",
        debug={"request_id": "req-ocr-dml"},
    )

    assert render_output("ocr.read", payload).splitlines() == [
        "fail ocr.read code=OCR_PROVIDER_UNAVAILABLE",
        "request id=req-ocr-dml",
        "shot path=.trail/shots/req-ocr-dml.png",
        'why msg="requested dml provider unavailable"',
    ]


def test_render_output_ocr_read_OCR_LANG_UNSUPPORTED_omits_recover():
    payload = _ocr_failure_payload(
        code="OCR_LANG_UNSUPPORTED",
        message="unsupported ocr lang: en",
        debug={"request_id": "req-ocr-lang"},
    )

    assert render_output("ocr.read", payload).splitlines() == [
        "fail ocr.read code=OCR_LANG_UNSUPPORTED",
        "request id=req-ocr-lang",
        'why msg="unsupported ocr lang: en"',
    ]


def test_render_output_ocr_read_success_does_not_expand_provider_or_lang_fields():
    payload = {
        "ok": True,
        "data": {
            "result": [
                {
                    "text": "点击进入",
                    "score": 0.98,
                    "box": {"left": 122, "top": 88, "width": 74, "height": 20},
                }
            ]
        },
        "screenshot": ".trail/shots/req-ocr-provider-hidden.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "trace": [
                {
                    "step": "ocr_provider",
                    "requested_provider": "auto",
                    "effective_provider": "cpu",
                    "lang": "ch",
                }
            ]
        },
        "error": None,
    }

    assert render_output("ocr.read", payload).splitlines() == [
        "ok ocr.read hits=1",
        "shot path=.trail/shots/req-ocr-provider-hidden.png",
        "info read_image_first=1",
        "text value=点击进入 box=122,88,74,20 center=159,98",
    ]


def test_render_output_ocr_read_success_keeps_ocr_mode_retry_context_verbose_only():
    payload = {
        "ok": True,
        "data": {
            "result": [
                {
                    "text": "点击进入",
                    "score": 0.98,
                    "box": {"left": 122, "top": 88, "width": 74, "height": 20},
                }
            ]
        },
        "screenshot": ".trail/shots/req-ocr-verbose-only-success.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-ocr-success",
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

    rendered = render_output("ocr.read", payload)

    assert rendered.splitlines() == [
        "ok ocr.read hits=1",
        "shot path=.trail/shots/req-ocr-verbose-only-success.png",
        "info read_image_first=1",
        "text value=点击进入 box=122,88,74,20 center=159,98",
    ]
    assert all(key not in rendered for key in OCR_VERBOSE_ONLY_KEYS)


def test_render_output_ocr_read_failure_keeps_ocr_mode_retry_context_verbose_only():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-ocr-verbose-only-fail.png",
        "timing": {},
        "warnings": [{"code": "OCR_LOW_CONFIDENCE", "message": "text may be incomplete"}],
        "references": [{"path": "refs/ocr.png", "similarity": 0.75}],
        "debug": {
            "request_id": "req-ocr-fail",
            "trace": [
                {
                    "step": "ocr",
                    "pieces": 0,
                    "mode_requested": "fast",
                    "mode_effective": "fast",
                    "scale_applied": "1280x720",
                    "retry_high": 0,
                    "retry_reason": "none",
                }
            ],
        },
        "error": {"code": "OCR_BACKEND_UNAVAILABLE", "message": "ocr backend unavailable"},
    }

    rendered = render_output("ocr.read", payload)

    assert rendered.splitlines() == [
        "fail ocr.read code=OCR_BACKEND_UNAVAILABLE",
        "request id=req-ocr-fail",
        "shot path=.trail/shots/req-ocr-verbose-only-fail.png",
        'why msg="ocr backend unavailable"',
        'warn code=OCR_LOW_CONFIDENCE msg="text may be incomplete"',
        "ref path=refs/ocr.png sim=0.75",
    ]
    assert all(key not in rendered for key in OCR_VERBOSE_ONLY_KEYS)


def test_render_output_renders_guide_list_with_paging_and_frozen_fields():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "abc",
                    "title": "购物阵容",
                    "version": "3.2",
                    "carry_roles": ["希儿", "停云"],
                    "final_role_cards": [
                        {"name": "希儿", "star": 5, "rarity": 3, "is_carry": True},
                        {"name": "布洛妮娅", "star": 5, "rarity": 3, "is_carry": False},
                        {"name": "佩拉", "star": 4, "rarity": 2, "is_carry": False},
                    ],
                    "support_hard": True,
                    "has_change_equip": False,
                    "has_expert": True,
                    "like": 123,
                    "favour": 45,
                },
                {
                    "lineup_id": "def",
                    "title": "事件阵容",
                    "version": "3.2",
                    "carry_roles": [],
                    "support_hard": False,
                    "has_change_equip": True,
                    "has_expert": False,
                    "like": 22,
                    "favour": 9,
                },
            ],
            "next_page_token": "token-2",
        },
        "screenshot": ".trail/shots/req-guide-list.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw count=2 more=1 next=token-2",
        "shot path=.trail/shots/req-guide-list.png",
        "info read_image_first=1",
        "guide 攻略ID=abc 攻略标题=购物阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45",
        "guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3|佩拉/star:4/rarity:2",
        "guide 攻略ID=def 攻略标题=事件阵容 版本=3.2 idx=2 攻略标签=#星徽攻略 点赞=22 收藏=9",
    ]


def test_render_output_guide_list_omits_next_when_not_paginated():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "abc",
                    "title": "购物阵容",
                    "version": "3.2",
                    "carry_roles": ["希儿"],
                    "support_hard": False,
                    "has_change_equip": False,
                    "has_expert": False,
                    "like": 7,
                    "favour": 3,
                }
            ],
            "next_page_token": None,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("guide.list.cw", payload).splitlines()

    assert lines == [
        "ok guide.list.cw count=1 more=0",
        "guide 攻略ID=abc 攻略标题=购物阵容 版本=3.2 idx=1 主C=希儿 点赞=7 收藏=3",
    ]
    assert all("最终阵容=" not in line for line in lines)
    assert "攻略标签=" not in "\n".join(lines)


def test_render_output_guide_list_falls_back_to_boolean_tags_when_labels_missing():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "abc",
                    "title": "仅布尔标签",
                    "version": "3.2",
                    "carry_roles": ["希儿"],
                    "labels": [],
                    "support_hard": True,
                }
            ],
            "next_page_token": None,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw count=1 more=0",
        "guide 攻略ID=abc 攻略标题=仅布尔标签 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈",
    ]


def test_render_output_renders_grouped_portal_guide_lists():
    payload = {
        "ok": True,
        "data": {
            "portals": [
                {
                    "portal_title": "购物区",
                    "list": [
                                {
                                    "lineup_id": "shop-guide",
                                    "title": "购物区优选阵容",
                                    "version": "3.2",
                                    "carry_roles": ["希儿"],
                                    "support_hard": True,
                                    "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                            "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
                        }
                    ],
                    "more": False,
                    "next_page_token": None,
                },
                {
                    "portal_title": "事件区",
                    "list": [
                                {
                                    "lineup_id": "event-guide",
                                    "title": "事件区优选阵容",
                                    "version": "3.2",
                                    "carry_roles": ["停云"],
                                    "support_hard": False,
                                    "has_change_equip": True,
                            "has_expert": False,
                            "like": 22,
                            "favour": 9,
                            "final_role_cards": [{"name": "停云", "star": 4, "rarity": 2, "is_carry": True}],
                        }
                    ],
                    "more": False,
                    "next_page_token": None,
                },
            ],
            "count": 2,
            "more": False,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw groups=2 count=2 more=0",
        "guide 投资环境=购物区 count=1 more=0",
        "guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45",
        "guide 投资环境=购物区 idx=1 最终阵容=希儿/carry:1/star:5/rarity:3",
        "guide 投资环境=事件区 count=1 more=0",
        "guide 投资环境=事件区 攻略ID=event-guide 攻略标题=事件区优选阵容 版本=3.2 idx=1 主C=停云 攻略标签=#星徽攻略 点赞=22 收藏=9",
        "guide 投资环境=事件区 idx=1 最终阵容=停云/carry:1/star:4/rarity:2",
    ]


def test_render_output_renders_grouped_portal_guide_lists_with_group_next_only():
    payload = {
        "ok": True,
        "data": {
            "portals": [
                {
                    "portal_title": "购物区",
                    "list": [
                        {
                            "lineup_id": "shop-guide",
                            "title": "购物区优选阵容",
                            "version": "3.2",
                            "carry_roles": ["希儿"],
                            "labels": [],
                            "support_hard": True,
                            "like": 123,
                            "favour": 45,
                        }
                    ],
                    "more": True,
                    "next_page_token": "group-token",
                }
            ],
            "count": 1,
            "more": True,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw groups=1 count=1 more=1",
        "guide 投资环境=购物区 count=1 more=1 next=group-token",
        "guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈 点赞=123 收藏=45",
    ]


def test_render_output_guide_list_renders_version_in_item_summary():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "guide-version",
                    "title": "版本校验阵容",
                    "version": "3.2",
                    "carry_roles": ["黑塔"],
                    "support_hard": True,
                    "has_change_equip": False,
                    "has_expert": True,
                    "like": 31,
                    "favour": 12,
                }
            ],
            "next_page_token": None,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw count=1 more=0",
        "guide 攻略ID=guide-version 攻略标题=版本校验阵容 版本=3.2 idx=1 主C=黑塔 攻略标签=#适用超频博弈|#专家顾问 点赞=31 收藏=12",
    ]


def test_render_output_guide_list_renders_role_candidate_blocks_before_guides():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "lineup-herta",
                    "title": "黑塔阵容",
                    "version": "3.2",
                    "carry_roles": ["黑塔"],
                    "support_hard": True,
                    "has_change_equip": False,
                    "has_expert": False,
                    "like": 21,
                    "favour": 8,
                }
            ],
            "next_page_token": None,
            "role_candidates": [
                {
                    "query": "黑搭",
                    "role_resolution": "fuzzy",
                    "resolved": "黑塔",
                    "candidates": [
                        {
                            "query": "黑搭",
                            "role": "黑塔",
                            "id": "1001",
                            "selected": True,
                            "score": 0.96,
                            "front_back": "front",
                            "traits": ["智识"],
                            "role_tags": ["输出", "智识"],
                        },
                        {
                            "query": "黑搭",
                            "role": "大黑塔",
                            "id": "1002",
                            "selected": False,
                            "score": 0.82,
                            "front_back": "front",
                            "traits": ["智识"],
                            "role_tags": ["输出"],
                        },
                    ],
                }
            ],
        },
        "screenshot": ".trail/shots/req-guide-role.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [
            {
                "code": "GUIDE_ROLE_FUZZY_MATCH",
                "query": "黑搭",
                "resolved": "黑塔",
                "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
            }
        ],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw count=1 more=0",
        "shot path=.trail/shots/req-guide-role.png",
        "info read_image_first=1",
        "info role_query=黑搭 role_resolution=fuzzy resolved=黑塔 candidates=2",
        "opt query=黑搭 role=黑塔 id=1001 selected=1 score=0.96 front_back=front traits=智识 role_tags=输出|智识",
        "opt query=黑搭 role=大黑塔 id=1002 selected=0 score=0.82 front_back=front traits=智识 role_tags=输出",
        "guide 攻略ID=lineup-herta 攻略标题=黑塔阵容 版本=3.2 idx=1 主C=黑塔 攻略标签=#适用超频博弈 点赞=21 收藏=8",
        "warn code=GUIDE_ROLE_FUZZY_MATCH query=黑搭 resolved=黑塔 msg=角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
    ]


def test_render_output_guide_list_grouped_portal_role_candidates_render_once_before_groups():
    payload = {
        "ok": True,
        "data": {
            "portals": [
                {
                    "portal_title": "购物区",
                    "list": [
                        {
                            "lineup_id": "shop-guide",
                            "title": "购物区优选阵容",
                            "version": "3.2",
                            "carry_roles": ["黑塔"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": False,
                            "like": 21,
                            "favour": 8,
                        }
                    ],
                    "more": False,
                    "next_page_token": None,
                },
                {
                    "portal_title": "事件区",
                    "list": [
                        {
                            "lineup_id": "event-guide",
                            "title": "事件区优选阵容",
                            "version": "3.2",
                            "carry_roles": ["黑塔"],
                            "support_hard": False,
                            "has_change_equip": True,
                            "has_expert": True,
                            "like": 9,
                            "favour": 3,
                        }
                    ],
                    "more": False,
                    "next_page_token": None,
                },
            ],
            "count": 2,
            "more": False,
            "role_candidates": [
                {
                    "query": "黑搭",
                    "role_resolution": "fuzzy",
                    "resolved": "黑塔",
                    "candidates": [
                        {
                            "query": "黑搭",
                            "role": "黑塔",
                            "id": "1001",
                            "selected": True,
                            "score": 0.96,
                            "front_back": "front",
                            "traits": ["智识"],
                            "role_tags": ["输出", "智识"],
                        }
                    ],
                }
            ],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [
            {
                "code": "GUIDE_ROLE_FUZZY_MATCH",
                "query": "黑搭",
                "resolved": "黑塔",
                "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
            }
        ],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw groups=2 count=2 more=0",
        "info role_query=黑搭 role_resolution=fuzzy resolved=黑塔 candidates=1",
        "opt query=黑搭 role=黑塔 id=1001 selected=1 score=0.96 front_back=front traits=智识 role_tags=输出|智识",
        "guide 投资环境=购物区 count=1 more=0",
        "guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=黑塔 攻略标签=#适用超频博弈 点赞=21 收藏=8",
        "guide 投资环境=事件区 count=1 more=0",
        "guide 投资环境=事件区 攻略ID=event-guide 攻略标题=事件区优选阵容 版本=3.2 idx=1 主C=黑塔 攻略标签=#星徽攻略|#专家顾问 点赞=9 收藏=3",
        "warn code=GUIDE_ROLE_FUZZY_MATCH query=黑搭 resolved=黑塔 msg=角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
    ]


def test_render_output_guide_list_renders_multi_role_warnings_with_query_and_resolved():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "lineup-herta",
                    "title": "黑塔阵容",
                    "version": "3.2",
                    "carry_roles": ["黑塔"],
                    "support_hard": True,
                    "has_change_equip": False,
                    "has_expert": False,
                    "like": 21,
                    "favour": 8,
                }
            ],
            "next_page_token": None,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [
            {
                "code": "GUIDE_ROLE_FUZZY_MATCH",
                "query": "黑搭",
                "resolved": "黑塔",
                "message": "角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
            },
            {
                "code": "GUIDE_ROLE_SIMILAR_CANDIDATES",
                "query": "银狼",
                "resolved": "银狼",
                "message": "角色名虽已精确命中，但存在高相似候选，请确认目标角色是否正确",
            },
        ],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "ok guide.list.cw count=1 more=0",
        "guide 攻略ID=lineup-herta 攻略标题=黑塔阵容 版本=3.2 idx=1 主C=黑塔 攻略标签=#适用超频博弈 点赞=21 收藏=8",
        "warn code=GUIDE_ROLE_FUZZY_MATCH query=黑搭 resolved=黑塔 msg=角色名未精确命中，已按最相近角色继续筛选，请确认目标角色是否正确",
        "warn code=GUIDE_ROLE_SIMILAR_CANDIDATES query=银狼 resolved=银狼 msg=角色名虽已精确命中，但存在高相似候选，请确认目标角色是否正确",
    ]


def test_render_output_guide_list_trait_lookup_failure_renders_trait_candidate_warns():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [
            {"trait": "巡猎", "trait_id": "2001", "score": 0.91},
            {"trait": "智识", "trait_id": "2003", "score": 0.67},
        ],
        "references": [],
        "debug": {"request_id": "req-guide-trait-invalid"},
        "error": {"code": "GUIDE_TRAIT_INVALID", "message": "guide trait invalid: 巡烈"},
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "fail guide.list.cw code=GUIDE_TRAIT_INVALID",
        "request id=req-guide-trait-invalid",
        'why msg="guide trait invalid: 巡烈"',
        "warn trait=巡猎 trait_id=2001 score=0.91",
        "warn trait=智识 trait_id=2003 score=0.67",
    ]


def test_render_output_failure_uses_top_level_request_id_when_debug_missing():
    payload = {
        "request_id": "req-top-level-only",
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [
            {"trait": "巡猎", "trait_id": "2001", "score": 0.91},
        ],
        "references": [],
        "debug": None,
        "error": {"code": "GUIDE_TRAIT_INVALID", "message": "guide trait invalid: 巡烈"},
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "fail guide.list.cw code=GUIDE_TRAIT_INVALID",
        "request id=req-top-level-only",
        'why msg="guide trait invalid: 巡烈"',
        "warn trait=巡猎 trait_id=2001 score=0.91",
    ]


def test_render_output_guide_list_rejects_yaml_output():
    payload = {
        "ok": True,
        "data": {"list": [], "next_page_token": None},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.list.cw", payload, output_format="yaml").splitlines() == [
        "fail guide.list.cw code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for guide.list.cw"',
    ]


def test_readme_documents_guide_list_name_filters_and_version() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "trail guide list cw --trait <name>" in readme
    assert "trail guide list cw --role <name>" in readme
    assert "`trail guide list cw` 默认文本只保留选攻略最关键的摘要：`攻略ID/攻略标题/版本/主C/攻略标签/点赞/收藏`" in readme
    assert "guide 攻略ID=abc 攻略标题=7群攻2银河学者 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45" in readme


def test_readme_documents_guide_list_version_as_selection_fact() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`trail guide list cw` 默认文本只保留选攻略最关键的摘要：`攻略ID/攻略标题/版本/主C/攻略标签/点赞/收藏`" in readme
    assert "`guide list cw` 列表结果现在会直接返回 `version`，list 阶段就应把版本兼容性纳入筛选判断" in readme


def test_render_output_renders_guide_fetch_summary_text():
    payload = {
        "ok": True,
        "data": {
            "lineup_id": "abc",
            "title": "7群攻2银河学者",
            "share_code": "##demo##",
            "labels": ["7级搜牌", "银河学者"],
            "version": "3.2",
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 8,
            "support_hard": True,
            "has_change_equip": False,
            "has_expert": True,
            "operation_guide": "前期：过渡\n中期：D牌\n后期：补强",
            "portals": ["商店", "事件"],
            "first_fight_augments": ["快攻", "回蓝"],
            "second_fight_augments": ["暴击", "连携"],
            "order_basic": ["升级", "买卡", "打精英"],
            "order_compose": ["希儿", "停云"],
            "role_stages": [
                {
                    "stage": "Opening",
                    "front_roles": [{"name": "黑塔", "star": 1, "rarity": 1, "is_carry": False}],
                    "back_roles": [{"name": "艾丝妲", "star": 1, "rarity": 1, "is_carry": False}],
                    "traits": ["1智识"],
                },
                {
                    "stage": "Final",
                    "front_roles": [
                        {
                            "name": "希儿",
                            "star": 3,
                            "rarity": 3,
                            "is_carry": True,
                            "first_equipments": ["高周波电锯", "战场进化手册"],
                            "second_equipments": ["胜利之旗"],
                        }
                    ],
                    "back_roles": [{"name": "佩拉", "star": 2, "rarity": 2, "is_carry": False}],
                    "traits": ["1巡猎", "2量子"],
                },
            ],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.fetch.cw", payload).splitlines() == [
        "ok guide.fetch.cw 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2 最低金币=40 最低等级=7 中期等级=8",
        "guide 攻略标签=#7级搜牌|#银河学者|#适用超频博弈|#专家顾问",
        "guide 羁绊列表=1智识|1巡猎|2量子",
        "guide 投资环境=商店|事件 优选投资策略=快攻|回蓝 次选投资策略=暴击|连携",
        "guide 简易装备优先度=升级|买卡|打精英 进阶装备优先度=希儿|停云",
        "guide 阶段=前期阵容 前台=黑塔/star:1/rarity:1 后台=艾丝妲/star:1/rarity:1 羁绊=1智识",
        "guide 阶段=最终阵容 前台=希儿/carry:1/star:3/rarity:3 后台=佩拉/star:2/rarity:2 羁绊=1巡猎|2量子",
        "guide 阶段=最终阵容 角色=希儿 优选装备=高周波电锯|战场进化手册 次选装备=胜利之旗",
        'guide 运营思路="前期：过渡\\n中期：D牌\\n后期：补强"',
    ]


def test_render_output_allows_yaml_for_guide_fetch():
    payload = {
        "ok": True,
        "data": {
            "lineup_id": "abc",
            "title": "7群攻2银河学者",
            "share_code": "##demo##",
            "labels": ["7级搜牌", "银河学者"],
            "version": "3.2",
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 8,
            "support_hard": True,
            "has_change_equip": False,
            "has_expert": True,
            "operation_guide": "前期：过渡\n中期：D牌\n后期：补强",
            "portals": ["商店", "事件"],
            "first_fight_augments": ["快攻", "回蓝"],
            "second_fight_augments": ["暴击", "连携"],
            "order_basic": ["升级", "买卡", "打精英"],
            "order_compose": ["希儿", "停云"],
            "role_stages": [
                {
                    "stage": "Final",
                    "front_roles": [
                        {
                            "name": "希儿",
                            "star": 3,
                            "rarity": 3,
                            "is_carry": True,
                            "first_equipments": ["高周波电锯"],
                            "second_equipments": [],
                        }
                    ],
                    "back_roles": [],
                    "traits": ["1巡猎"],
                }
            ],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("guide.fetch.cw", payload, output_format="yaml").splitlines()

    assert lines[0:8] == [
        "ok guide.fetch.cw 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2 最低金币=40 最低等级=7 中期等级=8",
        "guide 攻略标签=#7级搜牌|#银河学者|#适用超频博弈|#专家顾问",
        "guide 羁绊列表=1巡猎",
        "guide 投资环境=商店|事件 优选投资策略=快攻|回蓝 次选投资策略=暴击|连携",
        "guide 简易装备优先度=升级|买卡|打精英 进阶装备优先度=希儿|停云",
        "guide 阶段=最终阵容 前台=希儿/carry:1/star:3/rarity:3 羁绊=1巡猎",
        "guide 阶段=最终阵容 角色=希儿 优选装备=高周波电锯",
        'guide 运营思路="前期：过渡\\n中期：D牌\\n后期：补强"',
    ]
    assert "lineup_id: abc" in lines
    assert "operation_guide: '前期：过渡" in lines
    assert any("first_equipments:" in line for line in lines)
    assert "title: 7群攻2银河学者" in lines


def test_render_output_guide_fetch_trait_list_keeps_highest_count_per_trait():
    payload = {
        "ok": True,
        "data": {
            "title": "测试攻略",
            "share_code": "##demo##",
            "version": "3.2",
            "min_coins": 40,
            "min_level": 7,
            "mid_level": 8,
            "labels": [],
            "role_stages": [
                {"stage": "Opening", "traits": ["2贝洛伯格", "1星间旅人"]},
                {"stage": "Middle", "traits": ["4贝洛伯格", "2量子同频"]},
                {"stage": "Final", "traits": ["6贝洛伯格", "3量子同频"]},
            ],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("guide.fetch.cw", payload).splitlines()

    assert "guide 羁绊列表=6贝洛伯格|1星间旅人|3量子同频" in lines


def test_render_output_renders_cw_enter_home_text():
    payload = {
        "ok": True,
        "data": {"page": "home"},
        "screenshot": ".trail/shots/req-enter.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "shot path=.trail/shots/req-enter.png",
        "info read_image_first=1",
        "info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered",
    ]


def test_render_output_renders_cw_enter_handoff_line():
    payload = {
        "ok": True,
        "data": {"page": "home"},
        "screenshot": ".trail/shots/req-enter-handoff.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "shot path=.trail/shots/req-enter-handoff.png",
        "info read_image_first=1",
        "info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered",
    ]


def test_render_output_renders_cw_enter_already_home_info():
    payload = {
        "ok": True,
        "data": {"page": "home", "already_home": True},
        "screenshot": ".trail/shots/req-enter-home.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "shot path=.trail/shots/req-enter-home.png",
        "info read_image_first=1",
        "info already_home=1",
        "info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered",
    ]


def test_render_output_renders_cw_enter_handoff_after_already_home_info():
    payload = {
        "ok": True,
        "data": {"page": "home", "already_home": True},
        "screenshot": ".trail/shots/req-enter-handoff-home.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "shot path=.trail/shots/req-enter-handoff-home.png",
        "info read_image_first=1",
        "info already_home=1",
        "info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered",
    ]


def test_render_output_renders_cw_enter_handoff_after_warnings_and_references():
    payload = {
        "ok": True,
        "data": {"page": "home", "already_home": True},
        "screenshot": ".trail/shots/req-enter-handoff-tail.png",
        "timing": {},
        "warnings": [
            {"code": "PORTAL_STALE", "message": "portal snapshot stale"},
        ],
        "references": [
            {"path": ".trail/artifacts/portal.json", "similarity": 0.88},
        ],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "shot path=.trail/shots/req-enter-handoff-tail.png",
        "info read_image_first=1",
        "info already_home=1",
        'warn code=PORTAL_STALE msg="portal snapshot stale"',
        "ref path=.trail/artifacts/portal.json sim=0.88",
        "info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered",
    ]


def test_workflow_handoff_common_success_lines_leave_handoff_for_finalize(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rendering_module,
        "_load_workflow_handoffs",
        lambda: {
            "unknown.success": {
                "default": {
                    "handoff_skill": "trail-generic-entry",
                    "handoff_strength": "strong",
                    "handoff_reason": "generic_ready",
                }
            }
        },
    )
    payload = {
        "ok": True,
        "data": {},
        "screenshot": ".trail/shots/req-common-handoff.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [{"code": "GENERIC_WARN", "message": "generic warning"}],
        "references": [{"path": ".trail/artifacts/generic.json", "similarity": 0.66}],
        "debug": None,
        "error": None,
    }

    assert _append_common_success_lines(["ok unknown.success"], payload, "unknown.success") == [
        "ok unknown.success",
        "shot path=.trail/shots/req-common-handoff.png",
        "info read_image_first=1",
        'warn code=GENERIC_WARN msg="generic warning"',
        'ref path=.trail/artifacts/generic.json sim=0.66',
    ]


def test_cw_enter_handoff_renderer_leaves_handoff_for_success_finalize(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rendering_module,
        "_load_workflow_handoffs",
        lambda: {
            "cw.enter": {
                "default": {
                    "handoff_skill": "trail-cw-entry",
                    "handoff_strength": "strong",
                    "handoff_reason": "scene_entered",
                }
            }
        },
    )
    payload = {
        "ok": True,
        "data": {"page": "home", "already_home": True},
        "screenshot": ".trail/shots/req-enter-renderer.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [{"code": "PORTAL_STALE", "message": "portal snapshot stale"}],
        "references": [{"path": ".trail/artifacts/portal.json", "similarity": 0.88}],
        "debug": None,
        "error": None,
    }

    assert _render_cw_entry("cw.enter", payload) == [
        "ok cw.enter page=home",
        "shot path=.trail/shots/req-enter-renderer.png",
        "info read_image_first=1",
        "info already_home=1",
        'warn code=PORTAL_STALE msg="portal snapshot stale"',
        'ref path=.trail/artifacts/portal.json sim=0.88',
    ]


def test_render_output_handoff_status_prefers_status_mapping_over_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rendering_module,
        "_load_workflow_handoffs",
        lambda: {
            "cw.enter": {
                "default": {
                    "handoff_skill": "trail-cw-entry",
                    "handoff_strength": "strong",
                    "handoff_reason": "scene_entered",
                },
                "statuses": {
                    "portal_ready": {
                        "handoff_skill": "trail-portal-entry",
                        "handoff_strength": "strong",
                        "handoff_reason": "portal_ready",
                    }
                },
            }
        },
    )
    payload = {
        "ok": True,
        "data": {"page": "home", "status": "portal_ready"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "info handoff_skill=trail-portal-entry handoff_strength=strong handoff_reason=portal_ready",
    ]


def test_render_output_handoff_status_omits_line_without_status_match_or_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rendering_module,
        "_load_workflow_handoffs",
        lambda: {
            "cw.enter": {
                "statuses": {
                    "portal_ready": {
                        "handoff_skill": "trail-portal-entry",
                        "handoff_strength": "strong",
                        "handoff_reason": "portal_ready",
                    }
                }
            }
        },
    )
    payload = {
        "ok": True,
        "data": {"page": "home", "status": "unknown"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
    ]


def test_render_output_handoff_status_survives_invalid_default_branch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rendering_module,
        "_load_workflow_handoffs",
        lambda: {
            "cw.enter": {
                "default": "broken",
                "statuses": {
                    "portal_ready": {
                        "handoff_skill": "trail-portal-entry",
                        "handoff_strength": "strong",
                        "handoff_reason": "portal_ready",
                    }
                },
            }
        },
    )
    payload = {
        "ok": True,
        "data": {"page": "home", "status": "portal_ready"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "info handoff_skill=trail-portal-entry handoff_strength=strong handoff_reason=portal_ready",
    ]


def test_render_output_handoff_status_survives_invalid_statuses_branch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rendering_module,
        "_load_workflow_handoffs",
        lambda: {
            "cw.enter": {
                "default": {
                    "handoff_skill": "trail-cw-entry",
                    "handoff_strength": "strong",
                    "handoff_reason": "scene_entered",
                },
                "statuses": "broken",
            }
        },
    )
    payload = {
        "ok": True,
        "data": {"page": "home", "status": "portal_ready"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.enter", payload).splitlines() == [
        "ok cw.enter page=home",
        "info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=scene_entered",
    ]


def test_render_output_generic_success_has_no_workflow_handoff():
    payload = {
        "ok": True,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("unknown.success", payload).splitlines() == [
        "ok unknown.success",
    ]


def test_render_output_cw_start_has_no_workflow_handoff():
    payload = {
        "ok": True,
        "data": {"cards": []},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.start", payload).splitlines() == [
        "ok cw.start cards=0",
    ]


def test_render_output_cw_start_handoff_tail_for_configured_custom_renderer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rendering_module,
        "_load_workflow_handoffs",
        lambda: {
            "cw.start": {
                "default": {
                    "handoff_skill": "trail-cw-entry",
                    "handoff_strength": "strong",
                    "handoff_reason": "portal_ready",
                }
            }
        },
    )
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {
                    "card_idx": 1,
                    "portal_title": "Alpha Portal",
                    "portal_description": "Alpha Desc",
                    "score": 0.99,
                    "new": 1,
                    "guides": [],
                }
            ]
        },
        "screenshot": ".trail/shots/req-start-handoff.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [{"code": "PORTAL_STALE", "message": "portal snapshot stale"}],
        "references": [{"path": ".trail/artifacts/portal.json", "similarity": 0.88}],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.start", payload).splitlines() == [
        "ok cw.start cards=1",
        "shot path=.trail/shots/req-start-handoff.png",
        "info read_image_first=1",
        'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
        'opt idx=1 说明="Alpha Desc"',
        'warn code=PORTAL_STALE msg="portal snapshot stale"',
        'ref path=.trail/artifacts/portal.json sim=0.88',
        'info handoff_skill=trail-cw-entry handoff_strength=strong handoff_reason=portal_ready',
    ]


def test_render_output_workflow_handoff_tail_for_configured_generic_success(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        rendering_module,
        "_load_workflow_handoffs",
        lambda: {
            "unknown.success": {
                "default": {
                    "handoff_skill": "trail-generic-entry",
                    "handoff_strength": "strong",
                    "handoff_reason": "generic_ready",
                }
            }
        },
    )
    payload = {
        "ok": True,
        "data": {},
        "screenshot": ".trail/shots/req-generic-handoff.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [{"code": "GENERIC_WARN", "message": "generic warning"}],
        "references": [{"path": ".trail/artifacts/generic.json", "similarity": 0.66}],
        "debug": None,
        "error": None,
    }

    assert render_output("unknown.success", payload).splitlines() == [
        "ok unknown.success",
        "shot path=.trail/shots/req-generic-handoff.png",
        "info read_image_first=1",
        'warn code=GENERIC_WARN msg="generic warning"',
        'ref path=.trail/artifacts/generic.json sim=0.66',
        'info handoff_skill=trail-generic-entry handoff_strength=strong handoff_reason=generic_ready',
    ]


def test_render_output_registers_cw_portal_detect_to_portal_cards_family():
    assert TEXT_RENDERERS["cw.portal.detect"] is _render_cw_portal_cards


def test_render_output_renders_cw_start_portal_cards_family():
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {
                    "card_idx": 1,
                    "portal_title": "Alpha Portal",
                    "portal_description": "Alpha Desc",
                    "score": 0.99,
                    "new": 1,
                    "guides": [
                        {
                            "lineup_id": "alpha-guide",
                            "title": "Alpha攻略",
                            "version": "3.2",
                            "labels": [],
                            "carry_roles": ["希儿"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                            "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
                        }
                    ],
                },
                {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
            ],
            "mode": "continue",
            "difficulty": "current",
            "battle_mode": "standard",
            "stale": False,
        },
        "screenshot": ".trail/shots/req-start.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.start", payload).splitlines() == [
        "ok cw.start cards=2",
        "shot path=.trail/shots/req-start.png",
        "info read_image_first=1",
        'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
        'opt idx=1 说明="Alpha Desc"',
        'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 版本=3.2 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
        'guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3',
        'opt idx=2 投资环境="Beta Portal" score=0.88 待收集=0',
        'opt idx=2 说明="Beta Desc"',
    ]


def test_render_output_cw_start_exact_rank_omits_internal_difficulty_fields_from_success_text():
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {
                    "card_idx": 1,
                    "portal_title": "Alpha Portal",
                    "portal_description": "Alpha Desc",
                    "score": 0.99,
                    "new": 1,
                    "guides": [],
                }
            ],
            "mode": "continue",
            "difficulty": "A7-3",
            "requested_difficulty": "A7-3",
            "target_enemy_difficulty": 51,
            "current_enemy_difficulty": 54,
            "reason": "exact_rank_requested",
            "page": "invest",
            "battle_mode": "standard",
            "stale": False,
        },
        "screenshot": ".trail/shots/req-start-exact-rank.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    rendered = render_output("cw.start", payload)

    assert rendered.splitlines() == [
        "ok cw.start cards=1",
        "shot path=.trail/shots/req-start-exact-rank.png",
        "info read_image_first=1",
        'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
        'opt idx=1 说明="Alpha Desc"',
    ]
    for forbidden in (
        "A7-3",
        "51",
        "requested_difficulty=",
        "target_enemy_difficulty=",
        "current_enemy_difficulty=",
        "reason=",
        "page=",
    ):
        assert forbidden not in rendered


def test_render_output_cw_start_recovery_failure_keeps_failure_order_and_omits_internal_difficulty_fields():
    payload = {
        "request_id": "req-cw-start-recovery",
        "ok": False,
        "data": {
            "tainted": False,
            "requested_difficulty": "A7-3",
            "target_enemy_difficulty": 51,
            "current_enemy_difficulty": 54,
            "reason": "difficulty_change_requires_confirmation",
            "page": "home",
        },
        "screenshot": ".trail/shots/req-cw-start-recovery.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-cw-start-recovery",
            "last_known_stage": "side_effect_applied",
        },
        "error": {
            "code": "CW_START_DIFFICULTY_RECOVERY_REQUIRED",
            "message": "cw start difficulty change requires request-status confirmation",
        },
    }

    rendered = render_output("cw.start", payload)

    assert rendered.splitlines() == [
        "fail cw.start code=CW_START_DIFFICULTY_RECOVERY_REQUIRED tainted=0",
        "request id=req-cw-start-recovery",
        "shot path=.trail/shots/req-cw-start-recovery.png",
        'why msg="cw start difficulty change requires request-status confirmation"',
        "recover action=daemon.request_status request=req-cw-start-recovery",
    ]
    for forbidden in (
        "A7-3",
        "51",
        "requested_difficulty=",
        "target_enemy_difficulty=",
        "current_enemy_difficulty=",
        "reason=",
        "page=",
    ):
        assert forbidden not in rendered


def test_render_output_cw_start_omits_guide_tag_field_when_portal_guide_has_no_tags():
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {
                    "card_idx": 1,
                    "portal_title": "No Tag Portal",
                    "portal_description": "No Tag Desc",
                    "score": 0.42,
                    "guides": [
                        {
                            "lineup_id": "plain-guide",
                            "title": "无标签攻略",
                            "version": "3.2",
                            "carry_roles": ["希儿"],
                            "labels": [],
                            "support_hard": False,
                            "has_change_equip": False,
                            "has_expert": False,
                        }
                    ],
                }
            ]
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    rendered = render_output("cw.start", payload)

    assert 'opt idx=1 投资环境="No Tag Portal" score=0.42 待收集=0' in rendered
    assert 'guide idx=1 gid=1 攻略ID=plain-guide 攻略标题=无标签攻略 版本=3.2 主C=希儿' in rendered
    assert "攻略标签=" not in rendered


def test_render_output_renders_cw_strategy_detect_text():
    payload = _cw_strategy_cards_payload(screenshot=".trail/shots/req-strategy-detect.png")

    assert render_output("cw.strategy.detect", payload).splitlines() == [
        "ok cw.strategy.detect cards=2",
        "shot path=.trail/shots/req-strategy-detect.png",
        "info read_image_first=1",
        "opt idx=1 投资策略=回蓝 攻略推荐=优选 刷新次数=0",
        "opt idx=1 说明=启动回转",
        "opt idx=2 投资策略=暴击 攻略推荐=否 刷新次数=2",
        "opt idx=2 说明=爆发增伤",
        "info 已加载攻略=1",
    ]


def test_render_output_renders_cw_strategy_select_text():
    payload = _cw_strategy_select_payload(screenshot=".trail/shots/req-strategy-select.png")

    assert render_output("cw.strategy.select", payload).splitlines() == [
        "ok cw.strategy.select idx=2 投资策略=回蓝",
        "shot path=.trail/shots/req-strategy-select.png",
        "info read_image_first=1",
    ]


def test_render_output_cw_strategy_detect_rejects_yaml_output():
    payload = _cw_strategy_cards_payload(screenshot=".trail/shots/req-strategy-detect-yaml.png")

    assert render_output("cw.strategy.detect", payload, output_format="yaml").splitlines() == [
        "fail cw.strategy.detect code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-strategy-detect-yaml.png",
        'why msg="yaml not supported for cw.strategy.detect"',
    ]


def test_render_output_keeps_cw_strategy_zero_values():
    payload = _cw_strategy_cards_payload(
        cards=[
            {
                "card_idx": 1,
                "strategy_title": "回蓝",
                "strategy_description": "启动回转",
                "refresh_count": 0,
                "guide_match": "次选",
                "guide_loaded": 0,
            }
        ]
    )

    assert render_output("cw.strategy.refresh", payload).splitlines() == [
        "ok cw.strategy.refresh cards=1",
        "opt idx=1 投资策略=回蓝 攻略推荐=次选 刷新次数=0",
        "opt idx=1 说明=启动回转",
        "info 已加载攻略=0",
    ]


def test_render_output_counts_only_valid_cw_strategy_cards():
    payload = _cw_strategy_cards_payload(
        cards=[
            None,
            "invalid-card",
            {
                "card_idx": 3,
                "strategy_title": "回蓝",
                "strategy_description": "启动回转",
                "refresh_count": 0,
                "guide_match": "优选",
                "guide_loaded": 1,
            },
        ]
    )

    assert render_output("cw.strategy.detect", payload).splitlines() == [
        "ok cw.strategy.detect cards=1",
        "opt idx=3 投资策略=回蓝 攻略推荐=优选 刷新次数=0",
        "opt idx=3 说明=启动回转",
        "info 已加载攻略=1",
    ]


def test_render_output_registers_cw_strategy_renderers():
    assert TEXT_RENDERERS["cw.strategy.detect"] is TEXT_RENDERERS["cw.strategy.refresh"]
    assert TEXT_RENDERERS["cw.strategy.detect"] is not _render_cw_portal_cards
    assert TEXT_RENDERERS["cw.strategy.select"] is not TEXT_RENDERERS["cw.strategy.detect"]
    assert TEXT_RENDERERS["cw.strategy.select"] is not TEXT_RENDERERS["cw.portal.select"]


def test_render_output_renders_cw_portal_detect_family():
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {
                    "card_idx": 1,
                    "portal_title": "Alpha Portal",
                    "portal_description": "Alpha Desc",
                    "score": 0.99,
                    "guides": [
                        {
                            "lineup_id": "alpha-guide",
                            "title": "Alpha攻略",
                            "carry_roles": ["希儿"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                        }
                    ],
                }
            ],
            "mode": None,
            "difficulty": None,
            "battle_mode": None,
            "stale": False,
        },
        "screenshot": ".trail/shots/req-portal-detect.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.portal.detect", payload).splitlines() == [
        "ok cw.portal.detect cards=1",
        "shot path=.trail/shots/req-portal-detect.png",
        "info read_image_first=1",
        'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=0',
        'opt idx=1 说明="Alpha Desc"',
        'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
    ]


def test_render_output_cw_portal_detect_rejects_yaml_output():
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {
                    "card_idx": 1,
                    "portal_title": "Alpha Portal",
                    "portal_description": "Alpha Desc",
                    "score": 0.99,
                }
            ],
            "mode": None,
            "difficulty": None,
            "battle_mode": None,
            "stale": False,
        },
        "screenshot": ".trail/shots/req-portal-detect-yaml.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.portal.detect", payload, output_format="yaml").splitlines() == [
        "fail cw.portal.detect code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-portal-detect-yaml.png",
        'why msg="yaml not supported for cw.portal.detect"',
    ]


def test_render_output_renders_cw_portal_detect_failure_without_recover():
    payload = {
        "request_id": "req-cw-portal-detect-fail",
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-cw-portal-detect-fail.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": {"code": "CW_PORTAL_PAGE_INVALID", "message": "cw portal action only supports invest, current page: home"},
    }

    assert render_output("cw.portal.detect", payload).splitlines() == [
        "fail cw.portal.detect code=CW_PORTAL_PAGE_INVALID",
        "request id=req-cw-portal-detect-fail",
        "shot path=.trail/shots/req-cw-portal-detect-fail.png",
        'why msg="cw portal action only supports invest, current page: home"',
    ]


@pytest.mark.parametrize("command", ["cw.portal.refresh", "cw.portal.restart"])
def test_render_output_renders_cw_portal_refresh_family(command: str):
    payload = {
        "ok": True,
        "data": {
            "cards": [
                {
                    "card_idx": 1,
                    "portal_title": "Alpha Portal",
                    "portal_description": "Alpha Desc",
                    "score": 0.99,
                    "new": 1,
                    "guides": [
                        {
                            "lineup_id": "alpha-guide",
                            "title": "Alpha攻略",
                            "version": "3.2",
                            "carry_roles": ["希儿"],
                            "support_hard": True,
                            "has_change_equip": False,
                            "has_expert": True,
                            "like": 123,
                            "favour": 45,
                            "final_role_cards": [{"name": "希儿", "star": 5, "rarity": 3, "is_carry": True}],
                        }
                    ],
                },
                {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
            ],
            "mode": "continue",
            "difficulty": "current",
            "battle_mode": "standard",
            "stale": False,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output(command, payload).splitlines() == [
        f"ok {command} cards=2",
        'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
        'opt idx=1 说明="Alpha Desc"',
        'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 版本=3.2 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
        'guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3',
        'opt idx=2 投资环境="Beta Portal" score=0.88 待收集=0',
        'opt idx=2 说明="Beta Desc"',
    ]


def test_render_output_renders_cw_portal_select_summary_text():
    payload = {
        "ok": True,
        "data": {"card_idx": 2, "portal_title": "Beta Portal", "portal_description": "Beta Desc", "score": 0.88},
        "screenshot": ".trail/shots/req-portal-select.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.portal.select", payload).splitlines() == [
        'ok cw.portal.select idx=2 投资环境="Beta Portal"',
        "shot path=.trail/shots/req-portal-select.png",
        "info read_image_first=1",
        "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered",
    ]


def test_portal_select_renders_skill_info_before_warn_ref_and_handoff_last(capsys) -> None:
    print_output(
        "cw.portal.select",
        {
            "ok": True,
            "screenshot": ".trail/shots/portal.png",
            "data": {
                "card_idx": 1,
                "portal_title": "击破概念股",
                "skill_info": [{"name": "运营思路", "text": "前期 先读图"}],
            },
            "warnings": [{"code": "W", "message": "warn text"}],
            "references": [{"path": "p", "similarity": 0.9}],
        },
    )
    lines = capsys.readouterr().out.strip().splitlines()

    assert lines[0] == "ok cw.portal.select idx=1 投资环境=击破概念股"
    assert lines[1] == "shot path=.trail/shots/portal.png"
    assert lines[2] == "info read_image_first=1"
    assert lines.index('info skill_info=运营思路 text="前期 先读图"') < next(
        index for index, line in enumerate(lines) if line.startswith("warn ")
    )
    assert lines[-1] == "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered"


def test_portal_select_renders_collected_slots_and_shop_before_warn_ref_and_handoff(capsys) -> None:
    print_output(
        "cw.portal.select",
        {
            "ok": True,
            "screenshot": ".trail/shots/portal-prep.png",
            "data": {
                "card_idx": 1,
                "portal_title": "击破概念股",
                "skill_info": [{"name": "运营思路", "text": "先收集事实"}],
                "slots": {
                    "front": [{"name": "希儿", "star": 1, "traits": ["巡猎"]}],
                    "back": [],
                    "hand": [{"name": "停云"}],
                    "stale": False,
                    "trait_summary": [
                        {
                            "trait": "巡猎",
                            "tiers": [1, 2],
                            "owned_roles": 1,
                            "active_tier": 1,
                            "total_tiers": 2,
                            "ratio": 0.5,
                        }
                    ],
                },
                "shop": {
                    "opened": True,
                    "stale": False,
                    "items": [{"slot": 1, "name": "银狼", "price": 20}],
                    "coins": 40,
                    "reserve_full": False,
                    "stage_status": {"level": 3, "exp": "0/8", "team_size": "1/2", "stale": False},
                    "stage_status_stale": False,
                },
            },
            "warnings": [{"code": "W", "message": "warn text"}],
            "references": [{"path": "p", "similarity": 0.9}],
        },
    )
    lines = capsys.readouterr().out.strip().splitlines()

    assert lines == [
        "ok cw.portal.select idx=1 投资环境=击破概念股",
        "shot path=.trail/shots/portal-prep.png",
        "info read_image_first=1",
        "info skill_info=运营思路 text=先收集事实",
        "slot pos=front:0 name=希儿 star=1 traits=巡猎",
        "slot pos=hand:0 name=停云",
        'info 羁绊=巡猎 档位="1,2" 当前角色=1 已激活档位=1/2 占比=0.50',
        "item idx=1 slot=1 name=银狼 cost=20",
        "info coins=40 reserve_full=0",
        "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
        'warn code=W msg="warn text"',
        "ref path=p sim=0.9",
        "info handoff_skill=trail-cw-prep handoff_strength=strong handoff_reason=preparation_stage_entered",
    ]
    assert not any(" opened=" in line or " stale=" in line for line in lines)


def test_portal_select_skips_malformed_skill_info_items(capsys) -> None:
    print_output(
        "cw.portal.select",
        {
            "ok": True,
            "data": {
                "card_idx": 1,
                "portal_title": "击破概念股",
                "skill_info": [
                    "bad-item",
                    {"text": "缺少名称"},
                    {"name": "缺少文本"},
                    {"name": "", "text": "空名称"},
                    {"name": "空文本", "text": ""},
                    {"name": 123, "text": 456},
                    {"name": "运营思路", "text": "前期 先读图"},
                ],
            },
            "warnings": [],
            "references": [],
        },
    )
    lines = capsys.readouterr().out.strip().splitlines()

    assert [line for line in lines if line.startswith("info skill_info=")] == [
        "info skill_info=123 text=456",
        'info skill_info=运营思路 text="前期 先读图"',
    ]


def test_render_output_renders_cw_guide_current_summary_text():
    payload = {
        "ok": True,
        "data": {
            "lineup_id": "abc",
            "title": "7群攻2银河学者",
            "share_code": "##demo##",
            "version": "3.2",
            "labels": ["7级搜牌"],
            "support_hard": True,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.guide.current", payload).splitlines() == [
        "ok cw.guide.current 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2",
        "guide 攻略标签=#7级搜牌|#适用超频博弈",
    ]
    assert "攻略快照ID" not in render_output("cw.guide.current", payload)


def test_cw_guide_current_no_longer_outputs_artifact_snapshot_id():
    payload = {
        "ok": True,
        "data": {
            "lineup_id": "g1",
            "title": "测试攻略",
            "share_code": "##code##",
            "version": "4.0",
            "labels": [],
            "artifact": "legacy-artifact",
            "artifact_id": "legacy-artifact-id",
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    out = render_output("cw.guide.current", payload)

    assert "ok cw.guide.current 攻略ID=g1 攻略标题=测试攻略 攻略码=##code## 版本=4.0" in out
    assert "攻略快照ID" not in out
    assert "legacy-artifact" not in out


def test_render_output_renders_cw_guide_sparse_summaries_and_omits_empty_fields():
    sparse_payload = {
        "ok": True,
        "data": {"lineup_id": "abc", "share_code": "##demo##"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    sparse_apply_payload = {
        "ok": True,
        "data": {"lineup_id": "abc", "share_code": "##demo##"},
        "screenshot": ".trail/shots/req-cw-guide-apply.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.guide.current", sparse_payload).splitlines() == [
        "ok cw.guide.current 攻略ID=abc 攻略码=##demo##",
    ]
    assert render_output("cw.guide.apply", sparse_apply_payload).splitlines() == [
        "ok cw.guide.apply 攻略ID=abc 攻略码=##demo##",
        "shot path=.trail/shots/req-cw-guide-apply.png",
        "info read_image_first=1",
    ]
    assert "guide 攻略标签=" not in render_output("cw.guide.current", sparse_payload)
    assert "攻略快照ID=" not in render_output(
        "cw.guide.current",
        {
            "ok": True,
            "data": {"lineup_id": "abc", "share_code": "##demo##", "artifact": ""},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        },
    )
    assert '版本=""' not in render_output(
        "cw.guide.current",
        {
            "ok": True,
            "data": {"lineup_id": "abc", "share_code": "##demo##", "version": ""},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        },
    )
    assert '攻略标题=""' not in render_output(
        "cw.guide.current",
        {
            "ok": True,
            "data": {"lineup_id": "abc", "title": "", "share_code": "##demo##"},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        },
    )
    assert '攻略码=""' not in render_output(
        "cw.guide.current",
        {
            "ok": True,
            "data": {"lineup_id": "abc", "share_code": ""},
            "screenshot": None,
            "timing": {},
            "warnings": [],
            "references": [],
            "debug": None,
            "error": None,
        },
    )


def test_render_output_renders_guide_config_summary_and_zero_counts():
    payload = {
        "ok": True,
        "data": {
            "meta": {"season_id": 12, "sub_season_id": 3, "big_version": "3.2"},
            "lineup_levels": [{"id": 1}],
            "traits": [{"id": 1001}, {"id": 1002}],
            "roles": [{"id": 1}, {"id": 2}, {"id": 3}],
            "role_tags": [{"id": 11}],
            "portal_list": [{"portal_id": "shop"}, {"portal_id": "event"}],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    zero_payload = {
        "ok": True,
        "data": {
            "meta": {"season_id": 12, "sub_season_id": 3, "big_version": "3.2"},
            "lineup_levels": [],
            "traits": [],
            "roles": [],
            "role_tags": [],
            "portal_list": [],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.config.cw", payload).splitlines() == [
        "ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2",
        "info 搜牌档位=1 羁绊=2 角色=3 角色标签=1 投资环境=2",
    ]
    assert render_output("guide.config.cw", zero_payload).splitlines() == [
        "ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2",
        "info 搜牌档位=0 羁绊=0 角色=0 角色标签=0 投资环境=0",
    ]


def test_render_output_renders_cw_slots_summary_text():
    payload = {
        "ok": True,
        "data": {
            "front": [{"name": "希儿", "star": 4}, None],
            "back": [{"name": "佩拉", "rarity": 2}],
            "hand": [{"name": "停云", "cost": 2, "is_carry": True}, None],
            "stale": True,
        },
        "screenshot": ".trail/shots/req-slots.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.read", payload).splitlines() == [
        "ok cw.slots.read front=1 back=1 hand=1 stale=1",
        "shot path=.trail/shots/req-slots.png",
        "info read_image_first=1",
        "# 角色信息",
        "slot pos=front:0 name=希儿 star=4",
        "slot pos=front:1 empty=1",
        "slot pos=back:0 name=佩拉 rarity=2",
        "slot pos=hand:0 name=停云 carry=1 cost=2",
        "slot pos=hand:1 empty=1",
    ]


def test_render_output_adds_cw_slots_sections_without_breaking_screenshot_order():
    payload = {
        "ok": True,
        "data": {
            "front": [{"name": "希儿", "star": 1, "traits": ["巡猎"]}],
            "back": [],
            "hand": [{"name": "停云"}],
            "stale": False,
            "stage": "preparation",
            "stage_stale": False,
            "stage_status": {"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2"},
            "stage_status_stale": False,
            "trait_summary": [
                {"trait": "巡猎", "tiers": [1, 2], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.5},
            ],
        },
        "screenshot": ".trail/shots/req-slots.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.read", payload).splitlines() == [
        "ok cw.slots.read front=1 back=0 hand=1 stale=0",
        "shot path=.trail/shots/req-slots.png",
        "info read_image_first=1",
        "# 综合信息",
        "info stage=preparation stale=0",
        "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
        "# 角色信息",
        "slot pos=front:0 name=希儿 star=1 traits=巡猎",
        "slot pos=hand:0 name=停云",
        "# 羁绊信息",
        'info 羁绊=巡猎 档位="1,2" 当前角色=1 已激活档位=1/2 占比=0.50',
    ]


def test_render_output_skips_cw_slots_stage_line_when_stage_unknown():
    payload = {
        "ok": True,
        "data": {
            "front": [{"name": "希儿"}],
            "back": [],
            "hand": [],
            "stale": False,
            "stage": None,
            "stage_status": {"stale": False, "level": 3, "exp": "0/8", "team_size": "1/2"},
            "stage_status_stale": False,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.slots.read", payload).splitlines()

    assert lines == [
        "ok cw.slots.read front=1 back=0 hand=0 stale=0",
        "# 综合信息",
        "info stage_level=3 stage_exp=0/8 stage_team_size=1/2 stage_status_stale=0",
        "# 角色信息",
        "slot pos=front:0 name=希儿",
    ]
    assert "stage=null" not in "\n".join(lines)
    assert not any(line.startswith("info stage=") for line in lines)


def test_render_output_does_not_add_sections_to_single_body_commands():
    payload = {
        "ok": True,
        "data": {"value": "preparation", "stale": False},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    lines = render_output("cw.stage.detect", payload).splitlines()

    assert lines == ["ok cw.stage.detect stage=preparation stale=0"]
    assert not any(line.startswith("# ") for line in lines)


def test_render_output_does_not_add_sections_to_failures_or_single_action_outputs():
    failure = {
        "ok": False,
        "data": {},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-1"},
        "error": {"code": "X", "message": "failed"},
    }
    assert not any(line.startswith("# ") for line in render_output("cw.slots.read", failure).splitlines())

    place = {
        "ok": True,
        "data": {"front": ["希儿"], "back": [], "hand": [], "stale": False},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert not any(line.startswith("# ") for line in render_output("cw.slots.place", place).splitlines())

    strategy = {
        "ok": True,
        "data": {"cards": [{"name": "存钱"}]},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert not any(line.startswith("# ") for line in render_output("cw.strategy.detect", strategy).splitlines())


def test_render_output_renders_cw_slots_before_warn_and_ref():
    payload = {
        "ok": True,
        "data": {
            "front": [{"name": "希儿", "star": 4}],
            "back": [],
            "hand": [],
            "stale": False,
        },
        "screenshot": ".trail/shots/req-slots-order.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [{"code": "SLOTS_STALE", "message": "slots may be stale"}],
        "references": [{"path": "refs/slots.png", "similarity": 0.88}],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.read", payload).splitlines() == [
        "ok cw.slots.read front=1 back=0 hand=0 stale=0",
        "shot path=.trail/shots/req-slots-order.png",
        "info read_image_first=1",
        "# 角色信息",
        "slot pos=front:0 name=希儿 star=4",
        'warn code=SLOTS_STALE msg="slots may be stale"',
        "ref path=refs/slots.png sim=0.88",
    ]


def test_render_output_renders_cw_slots_traits_and_trait_summary():
    payload = {
        "ok": True,
        "data": {
            "front": [{"name": "希儿", "star": 4, "traits": ["巡猎", "量子"]}],
            "back": [{"name": "佩拉", "traits": ["量子"]}],
            "hand": [{"name": "布洛妮娅", "traits": ["巡猎", "辅助"]}],
            "trait_summary": [
                {"trait": "量子", "tiers": [1, 2], "owned_roles": 2, "active_tier": 2, "total_tiers": 2, "ratio": 1.0},
                {"trait": "巡猎", "tiers": [1, 2], "owned_roles": 1, "active_tier": 1, "total_tiers": 2, "ratio": 0.5},
            ],
            "stale": False,
        },
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.read", payload).splitlines() == [
        "ok cw.slots.read front=1 back=1 hand=1 stale=0",
        "# 角色信息",
        "slot pos=front:0 name=希儿 star=4 traits=巡猎|量子",
        "slot pos=back:0 name=佩拉 traits=量子",
        "slot pos=hand:0 name=布洛妮娅 traits=巡猎|辅助",
        "# 羁绊信息",
        'info 羁绊=量子 档位="1,2" 当前角色=2 已激活档位=2/2 占比=1.00',
        'info 羁绊=巡猎 档位="1,2" 当前角色=1 已激活档位=1/2 占比=0.50',
    ]


def test_render_output_renders_cw_slots_place_success_text():
    payload = {
        "ok": True,
        "data": {
            "front": ["希儿"],
            "back": ["佩拉"],
            "hand": [],
            "stale": True,
        },
        "screenshot": ".trail/shots/req-cw-slots-place.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.place", payload).splitlines() == [
        "ok cw.slots.place front=1 back=1 hand=0 stale=1",
        "shot path=.trail/shots/req-cw-slots-place.png",
        "info read_image_first=1",
    ]


def test_render_output_renders_cw_slots_place_known_failure_without_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-cw-slots-place-failed.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-cw-slots-place-failed"},
        "error": {
            "code": "SLOTS_CANNOT_BE_FIELDED",
            "message": "target slot cannot field character: front:0",
        },
    }

    assert render_output("cw.slots.place", payload).splitlines() == [
        "fail cw.slots.place code=SLOTS_CANNOT_BE_FIELDED",
        "request id=req-cw-slots-place-failed",
        "shot path=.trail/shots/req-cw-slots-place-failed.png",
        'why msg="target slot cannot field character: front:0"',
    ]


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (
            "cw.shop.scan",
            {
                "ok": True,
                "data": {"items": [], "opened": True, "stale": False, "stage_status_stale": True},
                "screenshot": ".trail/shots/req-shop-yaml.png",
                "image_guidance": {"read_image_first": True},
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
        ),
        (
            "cw.slots.read",
            {
                "ok": True,
                "data": {"front": [], "back": [], "hand": [], "stale": False},
                "screenshot": ".trail/shots/req-slots-yaml.png",
                "image_guidance": {"read_image_first": True},
                "timing": {},
                "warnings": [],
                "references": [],
                "debug": None,
                "error": None,
            },
        ),
    ],
)
def test_render_output_rejects_yaml_for_cw_scan_and_slots_read(command: str, payload: dict):
    assert render_output(command, payload, output_format="yaml").splitlines() == [
        f"fail {command} code=OUTPUT_FORMAT_NOT_SUPPORTED",
        f"shot path={payload['screenshot']}",
        f'why msg="yaml not supported for {command}"',
    ]


def test_render_output_renders_cw_hand_sell_success_text():
    payload = {
        "ok": True,
        "data": {
            "front": ["希儿"],
            "back": ["佩拉"],
            "hand": [None, "银狼", None],
            "stale": True,
        },
        "screenshot": ".trail/shots/req-cw-hand-sell.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.hand.sell", payload).splitlines() == [
        "ok cw.hand.sell front=1 back=1 hand=1 stale=1",
        "shot path=.trail/shots/req-cw-hand-sell.png",
        "info read_image_first=1",
    ]


def test_render_output_renders_cw_hand_sell_known_failure_without_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-cw-hand-sell-failed.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-cw-hand-sell-failed"},
        "error": {
            "code": "SLOTS_POSITION_INVALID",
            "message": "invalid hand slot: 7",
        },
    }

    assert render_output("cw.hand.sell", payload).splitlines() == [
        "fail cw.hand.sell code=SLOTS_POSITION_INVALID",
        "request id=req-cw-hand-sell-failed",
        "shot path=.trail/shots/req-cw-hand-sell-failed.png",
        'why msg="invalid hand slot: 7"',
    ]


def test_render_output_renders_cw_options_text():
    payload = {
        "ok": True,
        "data": {"options": [{"id": 1, "name": "量子力学"}, 2]},
        "screenshot": ".trail/shots/req-options.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.invest.read", payload).splitlines() == [
        "ok cw.invest.read count=2",
        "shot path=.trail/shots/req-options.png",
        "info read_image_first=1",
        "opt idx=1 id=1 name=量子力学",
        "opt idx=2 value=2",
    ]


def test_render_output_renders_cw_shop_buy_slot_summary_text():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 2, "name": "停云", "price": 10}, {"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
            "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        },
        "screenshot": ".trail/shots/req-buy-slot.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.buy_slot", payload).splitlines() == [
        "ok cw.shop.buy_slot opened=1 stale=0 count=2",
        "shot path=.trail/shots/req-buy-slot.png",
        "info read_image_first=1",
        "item idx=1 slot=1 name=银狼 cost=20",
        "item idx=2 slot=2 name=停云 cost=10",
    ]


def test_render_output_renders_cw_shop_scan_snapshot_info_text():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": False,
            "coins": 40,
            "level": 7,
            "exp": "4/52",
            "reserve_full": False,
            "team_size": "7/7",
            "guide_summary": {"constraints": {"min_coins": 40, "min_level": 7, "mid_level": 7}},
        },
        "screenshot": ".trail/shots/req-shop-scan.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.scan", payload).splitlines() == [
        "ok cw.shop.scan opened=1 stale=0 count=1",
        "shot path=.trail/shots/req-shop-scan.png",
        "info read_image_first=1",
        "item idx=1 slot=1 name=银狼 cost=20",
        "info coins=40 reserve_full=0",
    ]


def test_cw_shop_buy_exp_outputs_snapshot_facts(capsys) -> None:
    print_output(
        "cw.shop.buy_exp",
        {
            "ok": True,
            "screenshot": ".trail/shots/buy-exp.png",
            "data": {
                "opened": True,
                "stale": False,
                "items": [{"slot": 1, "name": "灵砂", "price": 3}],
                "coins": 36,
                "level": 4,
                "exp": "0/8",
                "reserve_full": False,
                "team_size": None,
            },
        },
    )
    lines = capsys.readouterr().out.strip().splitlines()

    assert lines[0] == "ok cw.shop.buy_exp opened=1 stale=0 count=1"
    assert lines[1] == "shot path=.trail/shots/buy-exp.png"
    assert lines[2] == "info read_image_first=1"
    assert lines[3:] == [
        "# 商店信息",
        "item idx=1 slot=1 name=灵砂 cost=3",
        "info coins=36 reserve_full=0",
        "# 综合信息",
        "info level=4 exp=0/8 team_size=null",
    ]


def test_render_output_renders_cw_shop_scan_with_empty_slot_placeholder():
    payload = {
        "ok": True,
        "data": {
            "items": [
                {"slot": 1, "name": "翡翠", "price": 1},
                {"slot": 2, "name": "三月七", "price": 1},
                {"slot": 3, "name": None, "price": None},
                {"slot": 4, "name": "万敌", "price": 2},
                {"slot": 5, "name": "符玄", "price": 4},
            ],
            "opened": True,
            "stale": False,
        },
        "screenshot": ".trail/shots/req-shop-scan-empty-slot.png",
        "image_guidance": {"read_image_first": True},
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.scan", payload).splitlines() == [
        "ok cw.shop.scan opened=1 stale=0 count=4",
        "shot path=.trail/shots/req-shop-scan-empty-slot.png",
        "info read_image_first=1",
        "item idx=1 slot=1 name=翡翠 cost=1",
        "item idx=2 slot=2 name=三月七 cost=1",
        "item idx=3 slot=3 empty=1",
        "item idx=4 slot=4 name=万敌 cost=2",
        "item idx=5 slot=5 name=符玄 cost=4",
    ]


def test_render_output_does_not_render_stale_shop_snapshot_info_for_open_command():
    payload = {
        "ok": True,
        "data": {
            "items": [{"slot": 1, "name": "银狼", "price": 20}],
            "opened": True,
            "stale": True,
            "coins": 40,
            "level": 7,
            "exp": "4/52",
            "reserve_full": False,
            "team_size": "7/7",
        },
        "screenshot": ".trail/shots/req-shop-open.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.open", payload).splitlines() == [
        "ok cw.shop.open opened=1 stale=1 count=1",
        "shot path=.trail/shots/req-shop-open.png",
        "info read_image_first=1",
        "item idx=1 slot=1 name=银狼 cost=20",
    ]


def test_render_output_renders_ocr_read_from_rapidocr_tuple_items():
    payload = {
        "ok": True,
        "data": {
            "result": [
                [[122, 88], [196, 88], [196, 108], [122, 108]],
            ]
        },
        "screenshot": ".trail/shots/req-ocr-raw.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    payload["data"]["result"] = [
        [
            [[122, 88], [196, 88], [196, 108], [122, 108]],
            "点击进入",
            0.98,
        ],
        [
            [[410, 502], [530, 502], [530, 538], [410, 538]],
            "开始挑战",
            0.93,
        ],
    ]

    assert render_output("ocr.read", payload).splitlines() == [
        "ok ocr.read hits=2",
        "shot path=.trail/shots/req-ocr-raw.png",
        "info read_image_first=1",
        "text value=点击进入 box=122,88,74,20 center=159,98",
        "text value=开始挑战 box=410,502,120,36 center=470,520",
    ]


def test_render_output_renders_cw_shop_refresh_action_with_snapshot_facts():
    payload = {
        "ok": True,
        "data": {"items": [{"slot": 1, "name": "银狼", "price": 20}], "opened": False, "stale": True},
        "screenshot": ".trail/shots/req-refresh.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.refresh", payload).splitlines() == [
        "ok cw.shop.refresh opened=0 stale=1 count=1",
        "shot path=.trail/shots/req-refresh.png",
        "info read_image_first=1",
        "item idx=1 slot=1 name=银狼 cost=20",
    ]


def test_render_output_renders_cw_event_handle_summary_text():
    payload = {
        "ok": True,
        "data": {"event_type": "special", "handled_action": "confirm"},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.event.handle", payload).splitlines() == [
        "ok cw.event.handle event_type=special handled_action=confirm"
    ]


def test_render_output_renders_cw_hand_sell_plan_text():
    payload = {
        "ok": True,
        "data": {
            "reference_only": True,
            "candidates": [],
            "todos": ["stage"],
            "items": [
                {
                    "slot": 0,
                    "name": "阮·梅",
                    "star": 1,
                    "target_star": None,
                    "current_star": None,
                    "category": "非攻略",
                    "recommendation": "不推荐",
                    "priority": 10,
                    "protected": False,
                    "reason": "缺少当前阶段，仅提供参考",
                }
            ],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.hand.sell_plan", payload).splitlines() == [
        "ok cw.hand.sell_plan count=1 reference_only=1 candidates=0 todos=1",
        "slot pos=hand:0 name=阮·梅 star=1 分类=非攻略 推荐度=不推荐 priority=10 protected=0 reason=缺少当前阶段，仅提供参考",
        "info todo=stage",
    ]


def test_render_output_filters_malformed_cw_hand_sell_plan_payload():
    payload = {
        "ok": True,
        "data": {
            "reference_only": True,
            "candidates": [],
            "todos": ["stage", None, "", "  "],
            "items": [
                {
                    "slot": 0,
                    "name": "阮·梅",
                    "category": "非攻略",
                    "recommendation": "不推荐",
                    "priority": 10,
                    "protected": "0",
                    "reason": "缺少当前阶段，仅提供参考",
                }
            ],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.hand.sell_plan", payload).splitlines() == [
        "ok cw.hand.sell_plan count=1 reference_only=1 candidates=0 todos=1",
        "slot pos=hand:0 name=阮·梅 分类=非攻略 推荐度=不推荐 priority=10 reason=缺少当前阶段，仅提供参考",
        "info todo=stage",
    ]


def test_render_output_rejects_yaml_for_cw_hand_sell_plan():
    payload = {
        "ok": True,
        "data": {
            "reference_only": True,
            "candidates": [],
            "todos": [],
            "items": [],
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.hand.sell_plan", payload, output_format="yaml").splitlines() == [
        "fail cw.hand.sell_plan code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for cw.hand.sell_plan"',
    ]


def test_render_output_rejects_yaml_for_cw_slots_place():
    payload = {
        "ok": True,
        "data": {
            "front": ["希儿"],
            "back": ["佩拉"],
            "hand": [],
            "stale": True,
        },
        "screenshot": ".trail/shots/req-cw-slots-place.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.slots.place", payload, output_format="yaml").splitlines() == [
        "fail cw.slots.place code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-cw-slots-place.png",
        'why msg="yaml not supported for cw.slots.place"',
    ]


def test_render_output_rejects_yaml_for_cw_hand_sell():
    payload = {
        "ok": True,
        "data": {
            "front": ["希儿"],
            "back": ["佩拉"],
            "hand": [None, "银狼", None],
            "stale": True,
        },
        "screenshot": ".trail/shots/req-cw-hand-sell.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.hand.sell", payload, output_format="yaml").splitlines() == [
        "fail cw.hand.sell code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-cw-hand-sell.png",
        'why msg="yaml not supported for cw.hand.sell"',
    ]


def test_render_output_rejects_yaml_for_cw_shop_buy_exp():
    payload = {
        "ok": True,
        "data": {
            "opened": True,
            "stale": False,
            "items": [],
            "coins": 36,
            "level": 4,
            "exp": "0/8",
            "reserve_full": False,
            "team_size": None,
        },
        "screenshot": ".trail/shots/req-cw-shop-buy-exp.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.shop.buy_exp", payload, output_format="yaml").splitlines() == [
        "fail cw.shop.buy_exp code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "shot path=.trail/shots/req-cw-shop-buy-exp.png",
        'why msg="yaml not supported for cw.shop.buy_exp"',
    ]


def test_render_output_accepts_yaml_string_format():
    assert render_output("daemon.status", _daemon_status_payload(), output_format="yaml").splitlines() == [
        "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1",
        "install:",
        "  protocol_version: 1",
        "runtime:",
        "  state: ready",
        "  pid: 1234",
        "  endpoint: 127.0.0.1:8765",
        "  last_start_error: null",
    ]


def test_render_output_appends_debug_lines_after_yaml_block_when_verbose():
    payload = _daemon_status_payload()
    payload["debug"] = {"request_id": "req-daemon-status", "detail": "bootstrap missing"}

    assert render_output("daemon.status", payload, output_format="yaml", verbose=True).splitlines() == [
        "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1",
        "install:",
        "  protocol_version: 1",
        "runtime:",
        "  state: ready",
        "  pid: 1234",
        "  endpoint: 127.0.0.1:8765",
        "  last_start_error: null",
        "debug kind=request msg=req-daemon-status",
        'debug kind=detail msg="bootstrap missing"',
    ]


def test_render_output_appends_yaml_and_debug_lines_for_allowlist_failure_when_verbose():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-daemon-fail", "detail": "bootstrap missing"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "daemon unavailable"},
    }

    assert render_output("daemon.status", payload, output_format="yaml", verbose=True).splitlines() == [
        "fail daemon.status code=DAEMON_UNAVAILABLE",
        "request id=req-daemon-fail",
        'why msg="daemon unavailable"',
        "{}",
        "debug kind=request msg=req-daemon-fail",
        'debug kind=detail msg="bootstrap missing"',
    ]


def test_render_output_keeps_debug_lines_for_non_allowlist_yaml_rejection_when_verbose():
    payload = {
        "ok": True,
        "data": {"result": [{"text": "点击进入"}]},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-ocr", "detail": "backend missing"},
        "error": None,
    }

    assert render_output("ocr.read", payload, output_format="yaml", verbose=True).splitlines() == [
        "fail ocr.read code=OUTPUT_FORMAT_NOT_SUPPORTED",
        "request id=req-ocr",
        'why msg="yaml not supported for ocr.read"',
        "debug kind=request msg=req-ocr",
        'debug kind=detail msg="backend missing"',
    ]


def test_print_output_uses_global_output_options(capsys):
    set_output_options(output_format="yaml", verbose=False)

    print_output("daemon.status", _daemon_status_payload())

    assert capsys.readouterr().out.splitlines() == [
        "ok daemon.status state=ready pid=1234 endpoint=127.0.0.1:8765 protocol=1",
        "install:",
        "  protocol_version: 1",
        "runtime:",
        "  state: ready",
        "  pid: 1234",
        "  endpoint: 127.0.0.1:8765",
        "  last_start_error: null",
    ]


def test_ocr_read_rejects_yaml_output(cli_runner, fake_daemon_client):
    fake_daemon_client(
        {
            "ocr.read": build_success_response(
                request_id="req-ocr-read",
                data={"result": [{"text": "点击进入"}]},
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "ocr", "read"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == [
        "fail ocr.read code=OUTPUT_FORMAT_NOT_SUPPORTED",
        'why msg="yaml not supported for ocr.read"',
    ]


def test_render_output_preserves_original_failure_for_non_allowlist_yaml():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-1.png",
        "timing": {},
        "warnings": [{"code": "OCR_LOW_CONFIDENCE", "message": "text may be incomplete"}],
        "references": [{"path": "refs/ocr.png", "similarity": 0.75}],
        "debug": {"request_id": "req-1"},
        "error": {"code": "OCR_BACKEND_UNAVAILABLE", "message": "ocr backend unavailable"},
    }

    assert render_output("ocr.read", payload, output_format="yaml").splitlines() == [
        "fail ocr.read code=OCR_BACKEND_UNAVAILABLE",
        "request id=req-1",
        "shot path=.trail/shots/req-1.png",
        'why msg="ocr backend unavailable"',
        'warn code=OCR_LOW_CONFIDENCE msg="text may be incomplete"',
        "ref path=refs/ocr.png sim=0.75",
    ]


def test_render_output_verbose_renders_ocr_facts_from_trace_from_capture_debug(tmp_path):
    from trail.output.capture import with_auto_capture

    class RuntimeStub:
        def capture_after_action(self, optional: bool = False):
            del optional
            return tmp_path / "ocr-context.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

        def consume_debug_trace(self):
            return [
                {
                    "step": "ocr",
                    "ts": "2026-04-24T08:15:31.004Z",
                    "ok": 1,
                    "dur_ms": 187,
                    "pieces": 1,
                    "mode_requested": "fast",
                    "mode_effective": "high",
                    "scale_applied": "native",
                    "retry_high": 1,
                    "retry_reason": "low_confidence",
                }
            ]

        def consume_debug_context(self):
            return {}

    payload = with_auto_capture(RuntimeStub(), lambda: {"result": [{"text": "点击进入"}]}, verbose=True)
    encoded_screenshot = payload["screenshot"].replace("\\", "\\\\")
    rendered = render_output("ocr.read", payload, verbose=True)
    lines = rendered.splitlines()
    ocr_line = next(line for line in lines if line.startswith("debug kind=trace step=ocr "))

    assert lines == [
        "ok ocr.read hits=1",
        f'shot path="{encoded_screenshot}"',
        "info read_image_first=1",
        "text value=点击进入",
        "debug kind=trace step=ocr ts=2026-04-24T08:15:31.004Z ok=1 dur_ms=187 pieces=1 mode_requested=fast mode_effective=high scale_applied=native retry_high=1 retry_reason=low_confidence",
    ]
    assert "mode_requested=fast" in ocr_line
    assert "mode_effective=high" in ocr_line
    assert "scale_applied=native" in ocr_line
    assert "retry_high=1" in ocr_line
    assert "retry_reason=low_confidence" in ocr_line
    assert all(line not in rendered for line in LEGACY_OCR_CONTEXT_DEBUG_LINES)


def test_render_output_verbose_includes_ocr_retry_high_zero_and_retry_reason_none_from_capture_trace(tmp_path):
    from trail.output.capture import with_auto_capture

    class RuntimeStub:
        def capture_after_action(self, optional: bool = False):
            del optional
            return tmp_path / "ocr-context-none.png"

        def collect_warnings(self):
            return []

        def match_references(self, screenshot_path, limit: int = 3):
            del screenshot_path, limit
            return []

        def consume_debug_trace(self):
            return [
                {
                    "step": "ocr",
                    "ts": "2026-04-24T08:15:31.004Z",
                    "ok": 1,
                    "dur_ms": 187,
                    "pieces": 1,
                    "mode_requested": "fast",
                    "mode_effective": "fast",
                    "scale_applied": "1280x720",
                    "retry_high": 0,
                    "retry_reason": "none",
                }
            ]

        def consume_debug_context(self):
            return {}

    payload = with_auto_capture(RuntimeStub(), lambda: {"result": [{"text": "点击进入"}]}, verbose=True)
    encoded_screenshot = payload["screenshot"].replace("\\", "\\\\")
    rendered = render_output("ocr.read", payload, verbose=True)
    lines = rendered.splitlines()
    ocr_line = next(line for line in lines if line.startswith("debug kind=trace step=ocr "))

    assert lines == [
        "ok ocr.read hits=1",
        f'shot path="{encoded_screenshot}"',
        "info read_image_first=1",
        "text value=点击进入",
        "debug kind=trace step=ocr ts=2026-04-24T08:15:31.004Z ok=1 dur_ms=187 pieces=1 mode_requested=fast mode_effective=fast scale_applied=1280x720 retry_high=0 retry_reason=none",
    ]
    assert "mode_requested=fast" in ocr_line
    assert "mode_effective=fast" in ocr_line
    assert "scale_applied=1280x720" in ocr_line
    assert "retry_high=0" in ocr_line
    assert "retry_reason=none" in ocr_line
    assert all(line not in rendered for line in LEGACY_OCR_CONTEXT_DEBUG_LINES)


def test_render_output_state_dump_tolerates_non_mapping_data():
    payload = {
        "ok": True,
        "data": "oops",
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("state.dump", payload).splitlines() == [
        "ok state.dump scene=unknown tainted=0"
    ]


def test_render_output_guide_config_tolerates_non_mapping_data():
    payload = {
        "ok": True,
        "data": "oops",
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("guide.config.cw", payload).splitlines() == [
        "ok guide.config.cw",
        "info 搜牌档位=0 羁绊=0 角色=0 角色标签=0 投资环境=0",
    ]


def test_render_output_guide_list_failure_renders_portal_candidates_as_warn_lines():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [
            {"portal": "购物区", "score": 0.98},
            {"portal": "事件区", "score": 0.81},
            {"portal": "补给区", "score": 0.74},
        ],
        "references": [],
        "debug": {"request_id": "req-portal-invalid"},
        "error": {"code": "GUIDE_PORTAL_INVALID", "message": "guide portal invalid: 购物曲"},
    }

    assert render_output("guide.list.cw", payload).splitlines() == [
        "fail guide.list.cw code=GUIDE_PORTAL_INVALID",
        "request id=req-portal-invalid",
        'why msg="guide portal invalid: 购物曲"',
        "warn portal=购物区 score=0.98",
        "warn portal=事件区 score=0.81",
        "warn portal=补给区 score=0.74",
    ]


def test_render_output_omits_recover_for_local_control_plane_failure():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-42", "detail": "timeout"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "daemon unavailable"},
    }

    assert render_output("daemon.start", payload).splitlines() == [
        "fail daemon.start code=DAEMON_UNAVAILABLE",
        "request id=req-42",
        'why msg="daemon unavailable"',
    ]


def test_render_output_window_launch_required_path_has_request_id_without_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-window-launch-required"},
        "error": {"code": "GAME_PATH_REQUIRED", "message": "请提供游戏路径"},
    }

    assert render_output("window.launch", payload).splitlines() == [
        "fail window.launch code=GAME_PATH_REQUIRED",
        "request id=req-window-launch-required",
        "why msg=请提供游戏路径",
    ]


def test_render_output_window_launch_explicit_missing_path_keeps_game_path_not_found():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-window-launch-missing"},
        "error": {"code": "GAME_PATH_NOT_FOUND", "message": "未找到游戏启动路径"},
    }

    assert render_output("window.launch", payload).splitlines() == [
        "fail window.launch code=GAME_PATH_NOT_FOUND",
        "request id=req-window-launch-missing",
        "why msg=未找到游戏启动路径",
    ]


def test_render_output_window_launch_explicit_launch_failure_keeps_stable_code():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-window-launch-failed"},
        "error": {"code": "GAME_LAUNCH_FAILED", "message": "显式提供的游戏路径启动失败: launch explode"},
    }

    assert render_output("window.launch", payload).splitlines() == [
        "fail window.launch code=GAME_LAUNCH_FAILED",
        "request id=req-window-launch-failed",
        'why msg="显式提供的游戏路径启动失败: launch explode"',
    ]


def test_render_output_window_launch_success_keeps_persist_failure_warning():
    path = r"C:\Games\StarRail.exe"
    payload = {
        "ok": True,
        "data": {
            "started": True,
            "already_running": False,
            "path": path,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [
            {
                "code": "GAME_PATH_PERSIST_FAILED",
                "message": "游戏已成功启动，但历史路径持久化失败: disk full",
            }
        ],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("window.launch", payload).splitlines() == [
        'ok window.launch started=1 already_running=0 path="C:\\\\Games\\\\StarRail.exe"',
        'warn code=GAME_PATH_PERSIST_FAILED msg="游戏已成功启动，但历史路径持久化失败: disk full"',
    ]


def test_render_output_start_run_success_keeps_fixed_first_line_order():
    payload = {
        "ok": True,
        "data": {
            "status": "launched_clicked_enter",
            "session": "sess-start-1",
            "reused": 1,
            "title": "崩坏：星穹铁道",
            "hwnd": 123,
        },
        "screenshot": ".trail/shots/req-start-run.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("start.run", payload).splitlines() == [
        "ok start.run status=launched_clicked_enter session=sess-start-1 reused=1 title=崩坏：星穹铁道 hwnd=123",
        "shot path=.trail/shots/req-start-run.png",
        "info read_image_first=1",
    ]


def test_render_output_start_run_success_keeps_reused_zero_fact():
    payload = {
        "ok": True,
        "data": {
            "status": "attached",
            "session": "sess-start-2",
            "reused": 0,
            "title": "崩坏：星穹铁道",
            "hwnd": 456,
        },
        "screenshot": ".trail/shots/req-start-run-2.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("start.run", payload).splitlines() == [
        "ok start.run status=attached session=sess-start-2 reused=0 title=崩坏：星穹铁道 hwnd=456",
        "shot path=.trail/shots/req-start-run-2.png",
        "info read_image_first=1",
    ]


def test_render_output_start_run_game_path_required_keeps_request_without_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-start-game-path"},
        "error": {"code": "GAME_PATH_REQUIRED", "message": "请提供游戏路径"},
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=GAME_PATH_REQUIRED",
        "request id=req-start-game-path",
        "why msg=请提供游戏路径",
    ]


def test_render_output_start_run_unknown_result_keeps_request_tainted_and_recover():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-start-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-start-unknown",
            "last_known_stage": "state_persisted",
        },
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-start-unknown",
        "shot path=.trail/shots/req-start-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-start-unknown",
    ]


def test_render_output_start_run_local_pre_daemon_failure_stays_on_start_run_token():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": {"code": "DAEMON_INSTALL_FAILED", "message": "daemon install failed"},
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=DAEMON_INSTALL_FAILED",
        'why msg="daemon install failed"',
    ]


def test_render_output_start_run_screenshot_required_keeps_tainted_recover_signal():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-start-no-shot",
            "last_known_stage": "side_effect_applied",
            "tainted": True,
        },
        "error": {
            "code": "START_RESULT_SCREENSHOT_REQUIRED",
            "message": "start.run success requires screenshot",
        },
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=START_RESULT_SCREENSHOT_REQUIRED tainted=1",
        "request id=req-start-no-shot",
        'why msg="start.run success requires screenshot"',
        "recover action=daemon.request_status request=req-start-no-shot",
    ]


def test_render_output_start_run_invalid_status_keeps_tainted_recover_signal():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-start-invalid-status",
            "last_known_stage": "side_effect_applied",
            "tainted": True,
        },
        "error": {
            "code": "START_RESULT_INVALID",
            "message": "start.run returned invalid status",
        },
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=START_RESULT_INVALID tainted=1",
        "request id=req-start-invalid-status",
        'why msg="start.run returned invalid status"',
        "recover action=daemon.request_status request=req-start-invalid-status",
    ]


def test_render_output_start_run_invalid_result_keeps_tainted_recover_signal():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-start-invalid-result",
            "last_known_stage": "side_effect_applied",
            "tainted": True,
        },
        "error": {
            "code": "START_RESULT_INVALID",
            "message": "start.run returned invalid result",
        },
    }

    assert render_output("start.run", payload).splitlines() == [
        "fail start.run code=START_RESULT_INVALID tainted=1",
        "request id=req-start-invalid-result",
        'why msg="start.run returned invalid result"',
        "recover action=daemon.request_status request=req-start-invalid-result",
    ]


def test_render_output_daemon_request_status_keeps_session_fact_for_reconcile_chain():
    payload = {
        "ok": True,
        "data": {
            "request_id": "req-42",
            "session_id": "sess-1",
            "final_state": "completed",
            "last_visible_stage": "responded",
            "tainted": False,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("daemon.request_status", payload).splitlines() == [
        "ok daemon.request_status request=req-42 session=sess-1 final_state=completed last_visible_stage=responded tainted=0"
    ]


def test_readme_documents_window_launch_path_resolution_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`trail window launch --channel official|bilibili|global`" in readme
    assert "显式 `--game-path` 仍可显式提供，且优先级最高、失败时不会回退" in readme
    assert "无显式路径时固定顺序：历史成功路径 -> 默认路径 -> 直接问用户" in readme
    assert "默认路径只覆盖 `official`" in readme
    assert r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe" in readme
    assert "`bilibili` / `global` 无历史成功路径时，通常仍需显式 `--game-path`" in readme
    assert "Agent 不应默认乱搜路径" in readme
    assert "`GAME_PATH_REQUIRED` 表示现在该直接问用户提供路径" in readme
    assert "`GAME_PATH_NOT_FOUND`" in readme
    assert "`GAME_LAUNCH_FAILED`" in readme
    assert "`GAME_PATH_PERSIST_FAILED`" in readme
    assert "trail window launch --game-path <StarRail.exe>" not in readme
    assert "自动搜索常见目录" not in readme
    assert "注册表" not in readme
    assert "全盘搜索" not in readme


def test_skill_docs_split_simple_and_advanced_commands() -> None:
    basic = (PROJECT_ROOT / "skills" / "trail-hsr" / "SKILL.md").read_text(encoding="utf-8")
    advanced = (PROJECT_ROOT / "skills" / "trail-hsr-advanced" / "SKILL.md").read_text(encoding="utf-8")
    simple_command_surface = SIMPLE_COMMAND_SURFACE_PATH.read_text(encoding="utf-8")
    advanced_reference_text = "\n".join(
        [
            ADVANCED_COMMAND_SURFACE_PATH.read_text(encoding="utf-8"),
            REQUEST_STATUS_AND_TAINT_PATH.read_text(encoding="utf-8"),
            RECOVERY_LADDER_PATH.read_text(encoding="utf-8"),
            WINDOW_LAUNCH_REFERENCE_PATH.read_text(encoding="utf-8"),
        ]
    )

    assert "references/simple-command-surface.md" in basic
    assert "references/advanced-command-surface.md" in advanced
    assert "trail start" in simple_command_surface
    assert "trail ocr read" in simple_command_surface
    assert "trail input" in simple_command_surface
    assert "trail daemon install" not in simple_command_surface
    assert "trail daemon status" not in simple_command_surface
    assert "trail daemon start" not in simple_command_surface
    assert "trail daemon request-status" not in simple_command_surface
    assert "trail daemon reconcile-session" not in simple_command_surface
    assert "trail window launch" not in simple_command_surface
    assert "trail window attach" not in simple_command_surface
    assert "trail session create" not in simple_command_surface
    assert "trail screen shot" not in simple_command_surface
    assert "trail image" not in simple_command_surface
    assert "trail state dump" not in simple_command_surface
    assert "trail daemon install" in advanced_reference_text
    assert "trail daemon status" in advanced_reference_text
    assert "trail daemon start" in advanced_reference_text
    assert "trail daemon request-status" in advanced_reference_text
    assert "trail daemon reconcile-session" in advanced_reference_text
    assert "trail window launch" in advanced_reference_text
    assert "trail window attach" in advanced_reference_text
    assert "trail session create" in advanced_reference_text
    assert "trail screen shot" in advanced_reference_text
    assert "trail image locate" in advanced_reference_text
    assert "trail image wait" in advanced_reference_text
    assert "trail state dump" in advanced_reference_text
    assert "trail state dump --session <id> --format yaml" in advanced_reference_text


def test_readme_documents_batch_place_sell_contract() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "`trail cw slots place --session <id> --action hand:0,front:0 --action hand:1,back:2`" in readme
    assert "`trail cw hand sell --session <id> --slot 0 --slot 2`" in readme
    assert "严格保序、遇错即停" in readme
    assert "重新执行 `trail cw slots read`" in readme


def test_advanced_skill_documents_window_launch_path_resolution_contract() -> None:
    window_launch = WINDOW_LAUNCH_REFERENCE_PATH.read_text(encoding="utf-8")

    assert "`trail window launch --channel official|bilibili|global`" in window_launch
    assert "显式 `--game-path` 仍可显式提供，且优先级最高、失败时不会回退" in window_launch
    assert "历史成功路径 -> 默认路径 -> 直接问用户" in window_launch
    assert "默认路径只覆盖 `official`" in window_launch
    assert r"C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe" in window_launch
    assert "`bilibili` / `global` 无历史成功路径时，通常仍需显式 `--game-path`" in window_launch
    assert "不默认乱搜路径" in window_launch
    assert "不扫注册表" in window_launch
    assert "不全盘搜索" in window_launch
    assert "`GAME_PATH_REQUIRED`" in window_launch
    assert "`GAME_PATH_NOT_FOUND`" in window_launch
    assert "`GAME_LAUNCH_FAILED`" in window_launch
    assert "`GAME_PATH_PERSIST_FAILED`" in window_launch
    assert "session=<id>" in window_launch


def test_render_output_renders_daemon_restart_summary():
    payload = {
        "ok": True,
        "data": {"stopped": True, "started": True, "already_running": False},
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }

    assert render_output("daemon.restart", payload).splitlines() == [
        "ok daemon.restart stopped=1 started=1 already_running=0"
    ]


def test_render_output_keeps_recover_for_unknown_result_failure():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-42.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-42",
            "detail": "OSError: flush failed",
            "last_known_stage": "state_persisted",
        },
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("input.click", payload).splitlines() == [
        "fail input.click code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-42",
        "shot path=.trail/shots/req-42.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-42",
    ]


def test_render_output_preserves_result_unknown_recovery_contract():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-unknown",
            "last_known_stage": "side_effect_applied",
            "detail": "flush failed",
        },
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("cw.shop.buy_slot", payload).splitlines() == [
        "fail cw.shop.buy_slot code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-unknown",
        "shot path=.trail/shots/req-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-unknown",
    ]


def test_render_output_preserves_cw_shop_scan_result_unknown_recovery_contract():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-shop-scan-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {
            "request_id": "req-shop-scan-unknown",
            "last_known_stage": "side_effect_applied",
            "detail": "flush failed",
        },
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("cw.shop.scan", payload).splitlines() == [
        "fail cw.shop.scan code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-shop-scan-unknown",
        "shot path=.trail/shots/req-shop-scan-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-shop-scan-unknown",
    ]


def test_render_output_preserves_cw_shop_buy_exp_result_unknown_recovery_contract():
    payload = {
        "ok": False,
        "data": {},
        "screenshot": ".trail/shots/req-cw-shop-buy-exp-unknown.png",
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": {"request_id": "req-cw-shop-buy-exp-unknown", "last_known_stage": "side_effect_applied", "detail": "flush failed"},
        "error": {"code": "DAEMON_UNAVAILABLE", "message": "mutation result unknown"},
    }

    assert render_output("cw.shop.buy_exp", payload).splitlines() == [
        "fail cw.shop.buy_exp code=DAEMON_UNAVAILABLE tainted=1",
        "request id=req-cw-shop-buy-exp-unknown",
        "shot path=.trail/shots/req-cw-shop-buy-exp-unknown.png",
        'why msg="mutation result unknown"',
        "recover action=daemon.request_status request=req-cw-shop-buy-exp-unknown",
    ]


def test_cli_accepts_yaml_format_option(cli_runner):
    result = cli_runner.invoke(app, ["--format", "yaml", "version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("trail ")


def test_cli_rejects_invalid_format_option(cli_runner):
    result = cli_runner.invoke(app, ["--format", "json", "version"])

    assert result.exit_code == 2
    assert "Invalid value for '--format'" in result.output
