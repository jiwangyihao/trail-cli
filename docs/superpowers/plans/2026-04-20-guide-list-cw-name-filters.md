# Guide List CW Name Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `guide list cw` 增加名称级 `trait` / `role` 筛选、角色模糊候选与差异化 warning、列表内 `version` 摘要、以及对应 help / renderer / 文档闭环。

**Architecture:** 继续沿用现有 CLI -> daemon -> scene -> renderer 分层。CLI 只新增参数与本地互斥校验；scene 负责名称解析、候选构建、上游 `trait_ids` / `role_ids` 生成；daemon 负责把 scene 的 `role_warnings` 提升到 envelope 顶层，并把 trait miss 映射成标准 failure envelope；renderer 在既有 `guide.list.cw` 家族内补 `version`、候选块和 warning 文本。

**Tech Stack:** Python, Typer, daemon RPC, `trail.scenes.cw.guide`, `trail.output.rendering`, pytest, 现有文本协议与 skills/README 文档体系

---

## File Structure

### Modify
- `trail/commands/guide.py`
  - 新增 `--trait`、`--role`、`--role-id` 和 help 文案。
  - 复用现有 `--portal` / `--portal-id` 风格做本地互斥校验。
- `trail/daemon/command_service.py`
  - 转发 `trait` / `role` / `role_id`。
  - 把 scene 返回的 `role_warnings` 提升到顶层 `warnings`。
  - 捕获 `GuideTraitLookupError` 并组装标准 failure envelope。
- `trail/scenes/cw/guide.py`
  - 新增 trait / role 名称解析 helper、候选排序、私有 role tag lookup。
  - 扩展 `_build_guide_list_request_payload()` / `_fetch_cw_guide_list_data()` 支持 `role_ids`。
  - 让普通 list 与 portal fan-out 两条路径共用同一组 resolved filters。
  - success 返回 `role_candidates` 与 `role_warnings`。
- `trail/output/rendering.py`
  - `guide.list.cw` 补 `version`。
  - success 路径渲染 role candidate 的 `info` 与 `opt` 行。
  - warning 渲染支持 `query=` / `resolved=` 以及 trait 候选行 `warn trait=<name> trait_id=<id> score=<score>`。
- `tests/test_cw_guide.py`
  - scene 层名称解析、请求体、portal fan-out、候选 shape、version 归一化。
- `tests/test_guide_rpc_contracts.py`
  - CLI payload、互斥错误、multi-role 文本协议、portal+role / portal+trait 合同。
- `tests/test_output_rendering.py`
  - `version`、candidate block、grouped portal、trait failure warning、query/resolved warning 排序。
- `tests/test_atomic_commands.py`
  - `trail guide list cw --help` 锚点回归。
- `README.md`
  - 补名称级筛选与 `version` 输出说明。
- `skills/trail-cw-guide/SKILL.md`
  - 补 `trait` / `role` 用法、候选警告、`version` 的决策意义。
- `skills/trail-cw/SKILL.md`
  - 在 list 阶段选攻略的 guidance 里同步 `role` 和 `version`。

### Baseline Notes
- Worktree: `C:\Users\34404\source\repos\trail-cli\.worktrees\guide-list-cw-name-filters`
- Spec: `C:\Users\34404\source\repos\trail-cli\.worktrees\guide-list-cw-name-filters\docs\superpowers\specs\2026-04-20-guide-list-cw-name-filters-design.md`
- 基线验证已通过：
  - `uv run pytest --basetemp .trail/pytest-tmp/guide-list-baseline tests/test_cw_guide.py tests/test_guide_rpc_contracts.py tests/test_output_rendering.py -k "guide" -v`
  - 结果：`58 passed`
- 当前环境统一使用显式 `--basetemp`，例如 `uv run pytest --basetemp .trail/pytest-tmp/guide-list-task1-red tests/test_cw_guide.py -v`，不要回退到 `rtk pytest`。
- `guide.list.cw` 本轮继续拒绝 YAML；不要改 `trail/output/rendering.py` 里的 YAML allowlist。
- 未经用户明确要求，不创建 git commit。

## Task 1: Scene 层名称解析与上游过滤

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Modify: `tests/test_cw_guide.py`

- [ ] **Step 1: 写 scene 层红灯测试，先锁 trait / role 名称解析与请求体 shape**

在 `tests/test_cw_guide.py` 新增这些测试：

```python
def test_build_guide_list_request_payload_includes_role_ids():
    guide_module = load_cw_guide_module()
    payload = json.loads(
        guide_module._build_guide_list_request_payload(
            page=1,
            limit=10,
            trait_id=321,
            role_ids=["1001", "1002"],
            order=None,
            next_page_token=None,
            match_change_job=None,
            match_hard=None,
        ).decode("utf-8")
    )
    assert payload["trait_ids"] == ["321", ""]
    assert payload["role_ids"] == ["1001", "1002"]


def fuzzy_role_config_response():
    payload = fake_cw_config_response()
    payload["data"]["role_list"] = [
        {"id": 1001, "name": "黑塔", "front_back_type": "front", "trait_details": [{"id": 2001}], "role_tags": ["输出", "智识"]},
        {"id": 1002, "name": "大黑塔", "front_back_type": "front", "trait_details": [{"id": 2001}], "role_tags": ["输出"]},
        {"id": 1003, "name": "银狼", "front_back_type": "back", "trait_details": [{"id": 2002}], "role_tags": ["减防"]},
        {"id": 1004, "name": "银狼Lv.999", "front_back_type": "back", "trait_details": [{"id": 2002}], "role_tags": ["减防"]},
        {"id": 1005, "name": "花火", "front_back_type": "back", "trait_details": [{"id": 2002}], "role_tags": ["辅助"]},
        {"id": 1006, "name": "火花", "front_back_type": "back", "trait_details": [{"id": 2002}], "role_tags": ["辅助"]},
    ]
    return payload


def test_fetch_cw_guide_list_resolves_trait_name_to_single_trait_id(monkeypatch):
    guide_module = load_cw_guide_module()
    captured_list_request = {}
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fake_cw_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured_list_request.update(kwargs) or {"list": [], "next_page_token": None},
        raising=False,
    )
    guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait="巡猎",
        trait_id=None,
        role=None,
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )
    assert captured_list_request["trait_id"] == 2001


def test_fetch_cw_guide_list_role_name_miss_returns_role_candidates_and_warning(monkeypatch):
    guide_module = load_cw_guide_module()
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: fuzzy_role_config_response()["data"], raising=False)
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: {"list": [fake_lineup_index_item(lineup_id="lineup-herta", title="黑塔阵容")], "next_page_token": None},
        raising=False,
    )
    payload = guide_module.fetch_cw_guide_list(
        page=1,
        limit=10,
        trait=None,
        trait_id=None,
        role="黑搭",
        role_id=None,
        order=None,
        next_page_token=None,
        match_change_job=None,
        match_hard=None,
    )
    assert payload["role_candidates"][0]["role_resolution"] == "fuzzy"
    assert payload["role_candidates"][0]["resolved"] == "黑塔"
    assert payload["role_warnings"][0]["code"] == "GUIDE_ROLE_FUZZY_MATCH"
```

同一轮里继续新增这些精确测试名：
- `test_fetch_cw_guide_list_trait_name_miss_raises_GuideTraitLookupError`
- `test_fetch_cw_guide_list_role_id_values_pass_through_to_role_ids`
- `test_fetch_cw_guide_list_role_exact_match_keeps_ambiguous_candidates`
- `test_fetch_cw_guide_list_multi_role_queries_keep_blocks_when_resolved_role_repeats`
- `test_fetch_cw_guide_list_portal_filter_reuses_resolved_role_ids`
- `test_fetch_cw_guide_list_portal_filter_reuses_resolved_trait_id`
- `test_normalize_lineup_summary_keeps_version_in_list_payload`

- [ ] **Step 2: 运行红灯，确认当前 scene 层还不支持这些语义**

Run:
`uv run pytest --basetemp .trail/pytest-tmp/guide-list-task1-red tests/test_cw_guide.py -k "trait or role or version or portal" -v`

Expected:
- FAIL，原因应集中在 `fetch_cw_guide_list()` 参数不支持 `trait` / `role` / `role_id`、请求体没有 `role_ids`、以及不存在新的 lookup error / candidate shape。

- [ ] **Step 3: 最小实现 scene 层名称解析与 payload 生成**

在 `trail/scenes/cw/guide.py` 做最小改动：

```python
class GuideTraitLookupError(TrailError):
    def __init__(self, trait: str, *, candidates: list[dict[str, object]]):
        super().__init__("GUIDE_TRAIT_INVALID", f"guide trait invalid: {trait}")
        self.candidates = candidates


def _build_guide_list_request_payload(
    *,
    page: int,
    limit: int,
    trait_id: int | None,
    role_ids: list[str],
    order: str | None,
    next_page_token: str | None,
    match_change_job: bool | None,
    match_hard: bool | None,
):
    payload = {
        "game": "hkrpg",
        "page": str(page),
        "limit": str(limit),
        "lineup_type": "Tourn",
        "role_ids": role_ids,
        "trait_ids": [str(trait_id), ""] if trait_id is not None else [],
        "next_page_token": next_page_token or "",
        "match_change_job": False if match_change_job is None else match_change_job,
        "match_hard": False if match_hard is None else match_hard,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")
```

同时补这些 helper：
- trait 名称 canonical lookup 与 top 3 候选。
- role canonical lookup、字符重排 / 包含 / `SequenceMatcher` 排序。
- 原始 config 到 `role_id -> role_tags` 的私有 lookup。
- `role_candidates` / `role_warnings` 生成。
- `_fetch_cw_guide_list_data()` 和 portal fan-out 共享 `trait_id` / `role_ids`。

- [ ] **Step 4: 运行绿灯，确认 scene 层 contract 收稳**

Run:
`uv run pytest --basetemp .trail/pytest-tmp/guide-list-task1-green tests/test_cw_guide.py -v`

Expected:
- PASS，且新增失败 / success shape 都被锁住。

## Task 2: CLI / Daemon 输入合同与帮助页

**Files:**
- Modify: `trail/commands/guide.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_guide_rpc_contracts.py`
- Modify: `tests/test_atomic_commands.py`

- [ ] **Step 1: 写 CLI / daemon 红灯测试，锁 help、payload、互斥和 envelope 桥接**

在 `tests/test_guide_rpc_contracts.py` 新增这些测试：

```python
def test_guide_list_trait_and_role_payload_mapping(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client({"guide.list.cw": build_success_response(request_id="req-guide-list", data={"list": [], "next_page_token": None})})
    result = cli_runner.invoke(app, [
        "guide", "list", "cw",
        "--trait", "巡猎",
        "--role", "黑塔",
        "--role", "大黑塔",
    ])
    assert result.exit_code == 0
    assert client.calls[0]["payload"]["trait"] == "巡猎"
    assert client.calls[0]["payload"]["role"] == ["黑塔", "大黑塔"]


def test_guide_list_rejects_trait_and_trait_id_together(cli_runner):
    result = cli_runner.invoke(app, ["guide", "list", "cw", "--trait", "巡猎", "--trait-id", "2001"])
    assert result.stdout.splitlines() == [
        "fail guide.list.cw code=GUIDE_INPUT_INVALID",
        'why msg="guide options \'--trait\' and \'--trait-id\' are mutually exclusive"',
    ]
```

同一轮里继续新增这些精确测试名：
- `test_guide_list_rejects_role_and_role_id_together`
- `test_guide_list_with_portal_and_role_payload_mapping`
- `test_guide_list_with_portal_and_role_id_payload_mapping`
- `test_guide_list_with_portal_and_trait_payload_mapping`
- `test_command_service_handles_guide_list_cw_with_trait_and_roles`
- `test_command_service_promotes_role_warnings_to_envelope`
- `test_command_service_maps_GuideTraitLookupError_to_failure_envelope`

在 `tests/test_atomic_commands.py` 新增这些 help 锚点测试：
- `test_guide_list_help_mentions_trait_and_role_filters`
- `test_guide_list_help_mentions_repeatable_role_values`
- `test_guide_list_help_mentions_exact_id_filters`


- [ ] **Step 2: 运行红灯，确认 CLI / daemon 还未实现这些合同**

Run:
`uv run pytest --basetemp .trail/pytest-tmp/guide-list-task2-red tests/test_guide_rpc_contracts.py::test_guide_list_trait_and_role_payload_mapping tests/test_guide_rpc_contracts.py::test_guide_list_rejects_trait_and_trait_id_together tests/test_guide_rpc_contracts.py::test_guide_list_rejects_role_and_role_id_together tests/test_guide_rpc_contracts.py::test_guide_list_with_portal_and_role_payload_mapping tests/test_guide_rpc_contracts.py::test_guide_list_with_portal_and_role_id_payload_mapping tests/test_guide_rpc_contracts.py::test_guide_list_with_portal_and_trait_payload_mapping tests/test_guide_rpc_contracts.py::test_command_service_handles_guide_list_cw_with_trait_and_roles tests/test_guide_rpc_contracts.py::test_command_service_promotes_role_warnings_to_envelope tests/test_guide_rpc_contracts.py::test_command_service_maps_GuideTraitLookupError_to_failure_envelope tests/test_atomic_commands.py::test_guide_list_help_mentions_trait_and_role_filters tests/test_atomic_commands.py::test_guide_list_help_mentions_repeatable_role_values tests/test_atomic_commands.py::test_guide_list_help_mentions_exact_id_filters -v`

Expected:
- FAIL，原因应是缺参数、help 无说明、daemon 未转发新字段、trait / role 互斥未校验或 envelope 未提升 warning。

- [ ] **Step 3: 最小实现 CLI / daemon 输入与 help**

在 `trail/commands/guide.py` 增加新参数与 help，并复用 portal 风格做本地互斥：

```python
trait: str | None = typer.Option(None, "--trait", help="按羁绊名称筛选，免查 config。"),
role: list[str] | None = typer.Option(None, "--role", help="按角色名称筛选，可重复传入多个值。"),
role_id: list[str] | None = typer.Option(None, "--role-id", help="按角色 id 精确筛选，可重复传入多个值。"),
```

在 `trail/daemon/command_service.py`：
- 透传 `trait` / `role` / `role_id`
- 捕获 `GuideTraitLookupError`
- 成功时把 `data.pop("role_warnings", [])` 提升到 envelope 顶层 `warnings`

- [ ] **Step 4: 运行绿灯，确认 CLI / daemon 合同固定**

Run:
`uv run pytest --basetemp .trail/pytest-tmp/guide-list-task2-green tests/test_guide_rpc_contracts.py::test_guide_list_trait_and_role_payload_mapping tests/test_guide_rpc_contracts.py::test_guide_list_rejects_trait_and_trait_id_together tests/test_guide_rpc_contracts.py::test_guide_list_rejects_role_and_role_id_together tests/test_guide_rpc_contracts.py::test_guide_list_with_portal_and_role_payload_mapping tests/test_guide_rpc_contracts.py::test_guide_list_with_portal_and_role_id_payload_mapping tests/test_guide_rpc_contracts.py::test_guide_list_with_portal_and_trait_payload_mapping tests/test_guide_rpc_contracts.py::test_command_service_handles_guide_list_cw_with_trait_and_roles tests/test_guide_rpc_contracts.py::test_command_service_promotes_role_warnings_to_envelope tests/test_guide_rpc_contracts.py::test_command_service_maps_GuideTraitLookupError_to_failure_envelope tests/test_atomic_commands.py::test_guide_list_help_mentions_trait_and_role_filters tests/test_atomic_commands.py::test_guide_list_help_mentions_repeatable_role_values tests/test_atomic_commands.py::test_guide_list_help_mentions_exact_id_filters -v`

Expected:
- PASS，且 help 文案和 envelope 行为都已固定。

## Task 3: Renderer、文档与最终回归

**Files:**
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_guide_rpc_contracts.py`
- Modify: `README.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `skills/trail-cw/SKILL.md`

- [ ] **Step 1: 写 renderer / docs 红灯测试，先锁 `version`、candidate block、warning 行序**

在 `tests/test_output_rendering.py` 新增这些断言：

```python
def test_render_output_guide_list_renders_version_in_item_summary():
    payload = {
        "ok": True,
        "data": {
            "list": [
                {
                    "lineup_id": "abc",
                    "title": "购物阵容",
                    "version": "3.2",
                    "carry_roles": ["希儿"],
                    "support_hard": True,
                    "has_change_equip": False,
                    "has_expert": True,
                    "like": 123,
                    "favour": 45,
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
    assert render_output("guide.list.cw", payload).splitlines()[1] == (
        "guide id=abc title=购物阵容 version=3.2 idx=1 carry=希儿 hard=1 change_equip=0 expert=1 like=123 favour=45"
    )


def test_render_output_guide_list_renders_role_candidate_blocks_before_guides():
    payload = {
        "ok": True,
        "data": {
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
                            "score": 0.92,
                            "front_back": "front",
                            "traits": ["巡猎", "量子"],
                            "role_tags": ["输出", "量子"],
                        },
                        {
                            "query": "黑搭",
                            "role": "大黑塔",
                            "id": "1002",
                            "selected": False,
                            "score": 0.81,
                            "front_back": "front",
                            "traits": ["智识"],
                            "role_tags": ["输出"],
                        },
                    ],
                }
            ],
            "list": [],
            "next_page_token": None,
        },
        "screenshot": None,
        "timing": {},
        "warnings": [],
        "references": [],
        "debug": None,
        "error": None,
    }
    assert render_output("guide.list.cw", payload).splitlines()[1:4] == [
        "info role_query=黑搭 role_resolution=fuzzy resolved=黑塔 candidates=2",
        "opt query=黑搭 role=黑塔 id=1001 selected=1 score=0.92 front_back=front traits=巡猎|量子 role_tags=输出|量子",
        "opt query=黑搭 role=大黑塔 id=1002 selected=0 score=0.81 front_back=front traits=智识 role_tags=输出",
    ]
```

在 `tests/test_guide_rpc_contracts.py` 同一轮继续新增这些精确测试名：
- `test_guide_list_role_fuzzy_success_renders_candidates_and_warning`
- `test_guide_list_role_exact_ambiguous_success_renders_candidates_and_warning`
- `test_guide_list_multi_role_queries_keep_duplicate_candidate_blocks`
- `test_guide_list_trait_lookup_failure_renders_trait_candidates`

在 `tests/test_output_rendering.py` 同一轮继续新增这些精确测试名：
- `test_render_output_guide_list_grouped_portal_role_candidates_render_once_before_groups`
- `test_render_output_guide_list_renders_multi_role_warnings_with_query_and_resolved`
- `test_render_output_guide_list_trait_lookup_failure_renders_trait_candidate_warns`
- `test_render_output_guide_list_rejects_yaml_output`

文档断言至少要锁：
- `test_readme_documents_guide_list_name_filters_and_version`
- `test_skills_document_guide_list_version_as_selection_fact`

- [ ] **Step 2: 运行红灯，确认 renderer / docs 还没覆盖这些事实**

Run:
`uv run pytest --basetemp .trail/pytest-tmp/guide-list-task3-red tests/test_output_rendering.py tests/test_guide_rpc_contracts.py -v`

Expected:
- FAIL，原因应集中在 `version` 尚未输出、没有 `info` / `opt`、generic warning 未带 `query` / `resolved`、trait warning 不能按特殊 shape 渲染。

- [ ] **Step 3: 最小实现 renderer 与文档同步**

在 `trail/output/rendering.py`：
- `guide.list.cw` 的普通与 grouped 条目都加 `version`
- `role_candidates` 在 `guide` 条目前输出 `info` / `opt`
- grouped portal 时 candidate block 只输出一次
- `_append_warnings()` 支持：
  - `warn trait=<name> trait_id=<id> score=<score>`
  - `warn code=<code> query=<query> resolved=<role> msg=<message>`
- 保持 `guide.list.cw` 继续拒绝 YAML

同步更新：
- `README.md`
- `skills/trail-cw-guide/SKILL.md`
- `skills/trail-cw/SKILL.md`

- [ ] **Step 4: 跑最终相关套件，确认协议与文档闭环**

Run:
`uv run pytest --basetemp .trail/pytest-tmp/guide-list-task3-green tests/test_cw_guide.py tests/test_guide_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py -v`

Expected:
- PASS
- `guide.list.cw` 相关新增 contract 全绿

- [ ] **Step 5: 做最终工作树核对**

Run:
`git status --short`

Run:
`git diff -- trail/commands/guide.py trail/daemon/command_service.py trail/scenes/cw/guide.py trail/output/rendering.py tests/test_cw_guide.py tests/test_guide_rpc_contracts.py tests/test_output_rendering.py tests/test_atomic_commands.py README.md skills/trail-cw-guide/SKILL.md skills/trail-cw/SKILL.md docs/superpowers/specs/2026-04-20-guide-list-cw-name-filters-design.md docs/superpowers/plans/2026-04-20-guide-list-cw-name-filters.md`

Expected:
- 只看到本计划允许的文件改动。

## Self-Review Checklist

- [ ] spec 里的 3 个核心面都能在 plan 里找到落点：scene 解析、CLI/daemon/help、renderer/docs。
- [ ] 没有遗漏 portal fan-out + role/trait 的组合路径。
- [ ] 没有把 `guide.config.cw` 公开 contract 扩面成 spec 未要求的字段。
- [ ] 没有把 `guide.list.cw` 加进 YAML allowlist。
- [ ] 没有在计划里要求提交 git commit；如果后续执行时用户没要求提交，就不提交。
