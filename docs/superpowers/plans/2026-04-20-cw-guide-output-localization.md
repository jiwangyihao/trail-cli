# 货币战争攻略相关命令中文输出收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `guide.list.cw`、`cw.start/cw.portal.*`、`cw.guide.current|apply`、`guide.config.cw` 的默认文本协议统一收口到中文语义，同时保持 YAML shape、CLI flags 与 failure/recover 语义不变。

**Architecture:** 以 `trail/output/rendering.py` 为唯一实现中心，先用 golden tests 冻结中文协议，再把 guide 摘要、标签折叠和 portal 卡片字段映射抽成共享 helper。文档、技能文档、AGENTS 约束和 help 文案与 renderer 一起同步，避免出现“代码改了但 Agent 说明还在读旧 key”的分叉。

**Tech Stack:** Python 3.12, Typer CLI, pytest, YAML rendering, git worktree, uv

---

## File Map

- Modify: `trail/output/rendering.py`
  负责 `guide.list.cw`、`cw.start`、`cw.portal.select|refresh|restart`、`cw.guide.current|apply`、`guide.config.cw` 的默认文本协议。
- Modify: `tests/test_output_rendering.py`
  冻结 renderer 级中文输出 golden，包括 README/skills 边界断言。
- Modify: `tests/test_guide_rpc_contracts.py`
  冻结顶层 `guide.*` CLI 的 stdout contract。
- Modify: `tests/test_cw_rpc_contracts.py`
  冻结 `cw.start`、`cw.portal.*`、`cw.guide.*` 的 stdout contract。
- Modify: `tests/test_output_debug.py`
  冻结 `AGENTS.md` 的输出协议约束文本。
- Modify: `tests/test_atomic_commands.py`
  冻结 `guide` / `cw guide` 相关 help 文案，避免遗留旧术语。
- Modify: `README.md`
  更新命令示例、字段语义、命令边界说明。
- Modify: `AGENTS.md`
  固化这轮新增的中文字段约束与 must-keep 事实。
- Modify: `skills/trail-cw-guide/SKILL.md`
  让 Agent 改为读取 `攻略标签/投资环境/最终阵容/攻略快照ID` 等中文字段。
- Modify: `skills/trail-cw/SKILL.md`
  同步整局流程里的选攻略/回顾攻略说明。
- Modify: `trail/commands/guide.py`
  改 `guide list` 的 help/docstring，去掉 `hard/change_equip/expert/final_roles` 这类旧术语。
- Modify: `trail/commands/cw.py`
  改 `cw guide` / `cw portal` 相关 help 文案，让帮助信息和中文输出协议保持一致。

## Task 1: 冻结新的 renderer / CLI / help 契约

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_output_rendering.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_guide_rpc_contracts.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_cw_rpc_contracts.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_output_debug.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_atomic_commands.py`

- [ ] **Step 1: 写 `tests/test_output_rendering.py` 的失败断言**

在现有对应测试上改成中文协议，并补缺失 case。核心断言按下面这些目标写：

```python
assert render_output("guide.list.cw", payload).splitlines() == [
    "ok guide.list.cw count=2 more=1 next=token-2",
    "guide 攻略ID=abc 攻略标题=购物阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45",
    "guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3",
]

assert render_output("guide.list.cw", grouped_payload).splitlines() == [
    "ok guide.list.cw groups=2 count=2 more=0",
    "guide 投资环境=购物区 count=1 more=0",
    "guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45",
    "guide 投资环境=购物区 idx=1 最终阵容=希儿/carry:1/star:5/rarity:3",
]

assert render_output("guide.list.cw", grouped_paged_payload).splitlines() == [
    "ok guide.list.cw groups=1 count=1 more=1",
    "guide 投资环境=购物区 count=1 more=1 next=group-token",
    "guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈 点赞=123 收藏=45",
]

assert render_output("cw.start", portal_payload).splitlines() == [
    "ok cw.start cards=2",
    "shot path=.trail/shots/req-start.png",
    'opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1',
    'opt idx=1 说明="Alpha Desc"',
    'guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 版本=3.2 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45',
    'guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3',
    'opt idx=2 投资环境="Beta Portal" score=0.88 待收集=0',
    'opt idx=2 说明="Beta Desc"',
]

assert render_output("cw.portal.select", payload).splitlines() == [
    'ok cw.portal.select idx=2 投资环境="Beta Portal"',
    'shot path=.trail/shots/req-portal-select.png',
]

assert render_output("cw.guide.current", payload).splitlines() == [
    "ok cw.guide.current 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2",
    "guide 攻略标签=#7级搜牌|#适用超频博弈",
    "info 攻略快照ID=art-1",
]

assert render_output("cw.guide.current", sparse_payload).splitlines() == [
    "ok cw.guide.current 攻略ID=abc 攻略码=##demo##",
]

assert render_output("cw.guide.apply", sparse_apply_payload).splitlines() == [
    "ok cw.guide.apply 攻略ID=abc 攻略码=##demo##",
    "shot path=.trail/shots/req-cw-guide-apply.png",
]

assert render_output("guide.config.cw", payload).splitlines() == [
    "ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2",
    "info 搜牌档位=1 羁绊=2 角色=3 角色标签=1 投资环境=2",
]

assert render_output("guide.config.cw", zero_payload).splitlines() == [
    "ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2",
    "info 搜牌档位=0 羁绊=0 角色=0 角色标签=0 投资环境=0",
]

assert render_output("guide.list.cw", fallback_tag_payload).splitlines() == [
    "ok guide.list.cw count=1 more=0",
    "guide 攻略ID=abc 攻略标题=仅布尔标签 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈",
]

assert "攻略标签=" not in render_output("guide.list.cw", no_tag_payload)
assert "攻略标签=" not in render_output("cw.start", no_tag_portal_payload)
```

额外补两个边界断言：

```python
assert "guide 攻略标签=" not in render_output("cw.guide.current", sparse_payload)
assert "待收集=0" in render_output("cw.portal.refresh", payload)
assert '攻略快照ID=' not in render_output("cw.guide.current", {"ok": True, "data": {"lineup_id": "abc", "share_code": "##demo##", "artifact": ""}})
assert '版本=""' not in render_output("cw.guide.current", {"ok": True, "data": {"lineup_id": "abc", "share_code": "##demo##", "version": ""}})
assert '攻略标题=""' not in render_output("cw.guide.current", {"ok": True, "data": {"lineup_id": "abc", "title": "", "share_code": "##demo##"}})
assert '攻略码=""' not in render_output("cw.guide.current", {"ok": True, "data": {"lineup_id": "abc", "share_code": ""}})
```

- [ ] **Step 2: 跑 renderer tests，确认 RED**

Run:

```bash
uv run pytest --basetemp .pytest-tmp tests/test_output_rendering.py -k "guide_list or cw_start or cw_portal or cw_guide or guide_config or readme_and_cw_skills_document_help_boundaries"
```

Expected:
- FAIL
- 失败点集中在旧英文字段名和旧 README/skills 断言

- [ ] **Step 3: 写 CLI contract / AGENTS / help 的失败断言**

把顶层和 cw 侧 golden 一次性改成中文协议，并补 help / AGENTS 文案断言。至少加入下面这些目标：

```python
assert result.stdout.splitlines() == [
    "ok guide.list.cw count=1 more=1 next=next-token",
    "guide 攻略ID=abc 攻略标题=7群攻2银河学者 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45",
    "guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3|佩拉/star:4/rarity:2",
]

assert result.stdout.splitlines() == [
    "fail guide.list.cw code=GUIDE_PORTAL_INVALID",
    "request id=req-portal-invalid",
    'why msg="guide portal invalid: 购物曲"',
    "warn portal=购物区 score=0.98",
]

assert result.stdout.splitlines() == _expected_lines(
    "ok cw.guide.apply 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2",
    screenshot=".trail/shots/req-cw-guide-apply.png",
    body=[
        "guide 攻略标签=#7级搜牌|#适用超频博弈",
        "info 攻略快照ID=art-1",
    ],
)

assert "guide.list.cw 的默认文本改用 攻略ID/攻略标题/版本/主C/攻略标签/最终阵容" in agents
assert "投资环境" in _normalize_help(cli_runner.invoke(app, ["cw", "portal", "--help"]).output)
assert "攻略快照ID" in _normalize_help(cli_runner.invoke(app, ["cw", "guide", "--help"]).output)
assert "final_roles" not in _normalize_help(cli_runner.invoke(app, ["guide", "list", "cw", "--help"]).output)
assert "guide 投资环境=购物区 count=1 more=1 next=group-token" in readme
assert "guide.config.cw --format yaml" in readme
assert "攻略快照ID" in skills_text
```

- [ ] **Step 4: 跑 CLI contract / AGENTS / help tests，确认 RED**

Run:

```bash
uv run pytest --basetemp .pytest-tmp tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_output_debug.py tests/test_atomic_commands.py -k "guide_list or guide_config or cw_guide or cw_portal or project_agents or help"
```

Expected:
- FAIL
- stdout golden 与 help 文案仍停留在旧字段名

## Task 2: 实现 renderer 中文收口

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\trail\output\rendering.py`

- [ ] **Step 1: 先加共享 helper，避免 list / portal / current 再分叉**

在 `trail/output/rendering.py` 里新增或重组 helper，目标是把“攻略标签折叠 + 单条 guide 摘要 + 最终阵容 + 稀疏字段省略”统一到一处。`guide.fetch.cw` 现有标签 helper 必须作为 source of truth，被 list/portal/current/apply 复用，不允许再平行复制第二套标签函数。实现方向按下面这个骨架走：

```python
def _non_empty(value: Any) -> Any:
    if value == "":
        return None
    return value


def _guide_tags(item: dict[str, Any]) -> str | None:
    return _guide_fetch_tags(item)


def _append_guide_summary_line(lines: list[str], *, item: dict[str, Any], idx: int | None = None, gid: int | None = None, portal: str | None = None) -> None:
    _append_fact_line(
        lines,
        "guide",
        ("投资环境", _non_empty(portal)),
        ("攻略ID", _guide_id(item)),
        ("攻略标题", _non_empty(item.get("title"))),
        ("版本", _non_empty(item.get("version"))),
        ("idx", idx),
        ("gid", gid),
        ("主C", _first_carry_role(item)),
        ("攻略标签", _guide_tags(item)),
        ("点赞", item.get("like")),
        ("收藏", item.get("favour")),
    )


def _append_guide_final_roles_line(lines: list[str], *, item: dict[str, Any], idx: int | None = None, gid: int | None = None, portal: str | None = None) -> None:
    final_roles = _compact_role_cards(item.get("final_role_cards"))
    if final_roles is None:
        return
    _append_fact_line(lines, "guide", ("投资环境", _non_empty(portal)), ("idx", idx), ("gid", gid), ("最终阵容", final_roles))
```

- [ ] **Step 2: 用 helper 改 `guide.list.cw` 与 `cw.start/cw.portal.*`**

把 `_render_guide_list()`、`_render_cw_portal_cards()`、`_render_cw_portal_select()` 改成中文协议。关键点：

```python
lines.append(
    "opt "
    + _format_fact_sequence(
        ("idx", card_idx),
        ("投资环境", card.get("portal_title")),
        ("score", card.get("score")),
        ("待收集", 1 if card.get("new") else 0),
    )
)
lines.append("opt " + _format_fact_sequence(("idx", card_idx), ("说明", card.get("portal_description"))))

lines = [
    "ok " + command + " " + _format_fact_sequence(("idx", data.get("card_idx")), ("投资环境", data.get("portal_title")))
]
```

注意：
- `warn portal=... score=...` 的 failure 路径不要改。
- `count/more/next/cards/groups/idx/gid/score` 的顺序不要乱。
- grouped list 的 `guide 投资环境=... count=... more=... next=...` 要沿用现有 `next_page_token` 行为。

- [ ] **Step 3: 改 `cw.guide.current|apply`，先收口稀疏摘要边界**

把 `_render_cw_guide_summary()` 扩成中文摘要。实现骨架按下面这个方向：

```python
def _render_cw_guide_summary(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    lines = [
        "ok " + command + " " + _format_fact_sequence(
            ("攻略ID", data.get("lineup_id") or data.get("id")),
            ("攻略标题", _non_empty(data.get("title"))),
            ("攻略码", _non_empty(data.get("share_code"))),
            ("版本", _non_empty(data.get("version"))),
        )
    ]
    _append_shot(lines, payload)
    _append_fact_line(lines, "guide", ("攻略标签", _guide_tags(data)))
    _append_fact_line(lines, "info", ("攻略快照ID", _non_empty(data.get("artifact") or data.get("artifact_id"))))
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines
```

额外要求：
- `current` 稀疏 payload 允许只剩首行。
- `apply` 在同样稀疏的 payload 下只比 `current` 多一条 `shot`。
- `攻略标题/攻略码/版本/攻略快照ID` 遇到空字符串时按缺失值省略，不能输出 `key=""`。

- [ ] **Step 4: 改 `guide.config.cw`，单独处理 `0` 计数与顺序**

实现骨架按下面这个方向：

```python


def _render_guide_config(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    meta = _as_dict(data.get("meta")) or data
    lines = [
        "ok " + command + " " + _format_fact_sequence(
            ("赛季", meta.get("season_id")),
            ("子赛季", meta.get("sub_season_id")),
            ("大版本", meta.get("big_version")),
        )
    ]
    _append_shot(lines, payload)
    lines.append(
        "info "
        + _format_fact_sequence(
            ("搜牌档位", len(data.get("lineup_levels") or [])),
            ("羁绊", len(data.get("traits") or [])),
            ("角色", len(data.get("roles") or [])),
            ("角色标签", len(data.get("role_tags") or [])),
            ("投资环境", len(data.get("portal_list") or [])),
        )
    )
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines
```

注意：
- 不要在已经追加实体行后再调用会自动补 `shot` 的公共 helper。
- 五个统计字段即使都是 `0` 也必须保留。

- [ ] **Step 5: 跑核心回归，确认 GREEN**

Run:

```bash
uv run pytest --basetemp .pytest-tmp tests/test_output_rendering.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py -k "guide_list or guide_config or cw_start or cw_portal or cw_guide"
```

Expected:
- PASS
- 不再出现旧字段名 `hard/change_equip/expert/final_roles/title/desc/new/artifact`

## Task 3: 同步 README / AGENTS / skills / help 并补文档断言

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\README.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\AGENTS.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\skills\trail-cw-guide\SKILL.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\skills\trail-cw\SKILL.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\trail\commands\guide.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\trail\commands\cw.py`

- [ ] **Step 1: 更新 README 示例和字段语义**

至少补这 6 段示例/说明：

```text
ok guide.list.cw count=2 more=1 next=token-2
guide 攻略ID=abc 攻略标题=7群攻2银河学者 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45
guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3

ok guide.list.cw groups=1 count=1 more=1
guide 投资环境=购物区 count=1 more=1 next=group-token
guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈 点赞=123 收藏=45

ok cw.start cards=2
opt idx=1 投资环境="购物区" score=0.99 待收集=1
opt idx=1 说明="花金币买角色和升级"

ok cw.portal.select idx=2 投资环境="购物区"

ok cw.guide.current 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2
guide 攻略标签=#7级搜牌|#适用超频博弈
info 攻略快照ID=art-1

ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2
info 搜牌档位=3 羁绊=42 角色=80 角色标签=11 投资环境=6

guide.config.cw --format yaml 会先输出中文摘要，再附原始英文 key 的结构化 YAML data
```

并补一段职责说明：

```text
- `guide.fetch.cw` 看完整攻略
- `guide.list.cw` 看筛选摘要
- `cw.guide.current|apply` 看当前已应用攻略
- `guide.config.cw` 看筛选枚举和全局配置规模
```

- [ ] **Step 2: 更新 AGENTS / skills / help 文案，并把断言锚点写进测试**

把项目级协议和技能文档里的旧术语替换成新术语。至少覆盖这些目标：

```text
- `guide.list.cw` 默认条目字段冻结为 `攻略ID/攻略标题/版本/主C/攻略标签/点赞/收藏/最终阵容`
- `cw.start` / `cw.portal.select|refresh|restart` 的 portal 卡片字段使用 `投资环境/说明/待收集`
- `cw.guide.current|apply` 使用 `攻略ID/攻略标题/攻略码/版本`，并以 `info 攻略快照ID=...` 表示 artifact id
- `guide.config.cw` 使用 `赛季/子赛季/大版本/搜牌档位/羁绊/角色/角色标签/投资环境`
```

`trail/commands/guide.py` 和 `trail/commands/cw.py` 的 help/docstring 目标示例：

```python
"""列出可选攻略。默认字段面向“选攻略”，会保留攻略标签、主C、版本、点赞/收藏与最终阵容等高价值信息。"""

CW_GUIDE_HELP = "当前攻略摘要会输出 攻略ID、攻略标题、攻略码、版本、攻略标签 与 攻略快照ID。"
CW_PORTAL_HELP = "投资环境卡片会输出 投资环境、说明、待收集、score，以及下挂攻略摘要。"
```

同时在测试里新增稳定锚点，而不是只跑现有 `readme/skills/help` 过滤：

```python
assert "guide 投资环境=购物区 count=1 more=1 next=group-token" in readme
assert "guide.config.cw --format yaml 会先输出中文摘要" in readme
assert "攻略快照ID" in trail_cw_guide_skill
assert "投资环境卡片会输出 投资环境、说明、待收集" in trail_cw_skill
assert "攻略快照ID" in _normalize_help(cli_runner.invoke(app, ["cw", "guide", "--help"]).output)
assert "投资环境卡片" in _normalize_help(cli_runner.invoke(app, ["cw", "portal", "--help"]).output)
```

- [ ] **Step 3: 跑文档/帮助相关断言，确认 GREEN**

Run:

```bash
uv run pytest --basetemp .pytest-tmp tests/test_output_rendering.py tests/test_output_debug.py tests/test_atomic_commands.py -k "readme or skills or help or project_agents"
```

Expected:
- PASS
- README / AGENTS / skills / help 里不再残留 `hard/change_equip/expert/final_roles/artifact`

## Task 4: 端到端验证与开发前检查

**Files:**
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\trail\output\rendering.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_output_rendering.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_guide_rpc_contracts.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_cw_rpc_contracts.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_output_debug.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\tests\test_atomic_commands.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\README.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\AGENTS.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\skills\trail-cw-guide\SKILL.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\skills\trail-cw\SKILL.md`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\trail\commands\guide.py`
- Modify: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-guide-output-localization\trail\commands\cw.py`

- [ ] **Step 1: 跑定向回归，覆盖所有受影响面**

Run:

```bash
uv run pytest --basetemp .pytest-tmp tests/test_output_rendering.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_output_debug.py tests/test_atomic_commands.py -k "guide or cw_portal or cw_guide or project_agents or readme or skills or help"
```

Expected:
- PASS
- 直接相关 golden 全绿

- [ ] **Step 2: 跑全量 pytest，确认没有把别的 renderer 家族打坏**

Run:

```bash
uv run pytest --basetemp .pytest-tmp
```

Expected:
- PASS
- 0 failures

- [ ] **Step 3: 开发前自检 worktree 状态**

Run:

```bash
rtk git status --short
rtk git diff -- trail/output/rendering.py tests/test_output_rendering.py tests/test_guide_rpc_contracts.py tests/test_cw_rpc_contracts.py tests/test_output_debug.py tests/test_atomic_commands.py README.md AGENTS.md skills/trail-cw-guide/SKILL.md skills/trail-cw/SKILL.md trail/commands/guide.py trail/commands/cw.py
```

Expected:
- 只出现本轮计划里的文件
- 没有意外文件被改进来

- [ ] **Step 4: 不主动提交，等待用户明确要求 commit**

本仓库对话规则要求：未得到用户明确要求前，不创建 git commit。完成实现后只汇报 worktree 路径、改动文件、测试结果；如用户随后要求提交，再单独走 git 提交流程。
