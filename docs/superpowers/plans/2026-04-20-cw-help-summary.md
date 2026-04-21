> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。

# CW Help Summary 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 让 `trail cw --help` 与 `trail cw <group> --help` 提供稳定、简短、可扫读的职责摘要，并把相关 README / skill 术语同步到同一套边界说明。

**架构：** 保持现有 Typer 命令结构不变，只在 `trail/commands/cw.py` 的 `Typer(help=...)` 层补顶层总述和各子组摘要，不扩写叶子命令参数帮助。测试分成三层：`cw_app` 顶层总述、`cw --help` 命令表子组锚点、代表性子组页摘要；README / skill 同步通过独立文档断言锁住，不混进 CLI help 合约测试。

**技术栈：** Python 3.12、Typer、pytest、README / SKILL 文档约束。

---

## 文件地图

- Modify: `trail/commands/cw.py`
  责任：给 `cw_app` 和各子组 `Typer` 增加 help 摘要，冻结 `enter/start` 只在顶层总述里辨识。
- Modify: `tests/test_atomic_commands.py`
  责任：锁定 `trail cw --help` 顶层总述、命令表锚点，以及代表性子组页摘要。
- Modify: `README.md`
  责任：让 `命令面概览`、`货币战争流程`、`Skill 边界` 中与 `cw` 分组职责有关的说法和 CLI help 一致。
- Modify: `skills/trail-cw/SKILL.md`
  责任：同步 `enter/start/portal/stage/guide/invest` 的边界用语。
- Modify: `skills/trail-cw-guide/SKILL.md`
  责任：补一条显式职责边界句，把顶层 `trail guide ... cw` 与 `trail cw guide apply/current` 稳定区分开。
- Check: `skills/trail-cw-replenish/SKILL.md`
  责任：核对其现有文字是否已经保留“局内 invest 事件”边界；若已清楚则不改。
- Check: `skills/trail-cw-events/SKILL.md`
  责任：确认其现有措辞不会把 `event` 写成所有事件的总称；若无冲突则不改。
- Check: `skills/trail-cw-shop/SKILL.md`
  责任：确认其现有措辞不越界到 `portal/start` 职责；若无冲突则不改。
- Check: `skills/trail-cw-slots/SKILL.md`
  责任：确认其现有措辞不越界到 `portal/start` 职责；若无冲突则不改。
- Modify: `tests/test_output_rendering.py`
  责任：锁定 README / skill 文档中与 `cw` help 摘要直接相关的边界语句。

## 基线说明

- 工作目录固定在 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-help-summary`。
- 当前环境的全量 `uv run pytest` 会在 `C:\Users\34404\AppData\Local\Temp\pytest-of-34404` 上触发权限错误；本计划里的测试命令统一显式带 `--basetemp .pytest_tmp/<task-name>`。
- 未经用户明确要求，不创建 git commit。

### Task 1: 先锁住 `cw` help 合约

**Files:**
- Modify: `tests/test_atomic_commands.py`

- [ ] **Step 1: 增加帮助输出块提取 helper，避免直接对整份 Typer 表格做脆弱断言**

```python
CW_HELP_COMMANDS = {
    "enter",
    "start",
    "guide",
    "portal",
    "stage",
    "slots",
    "shop",
    "crystals",
    "hand",
    "replenish",
    "invest",
    "encounter",
    "fortune",
    "boss-preview",
    "battle",
    "settle",
    "event",
}


def _normalize_help(output: str) -> str:
    return " ".join(output.split())


def _extract_help_command_block(output: str, command_name: str) -> str:
    lines = output.splitlines()
    block: list[str] = []
    capture = False
    in_commands = False

    for raw_line in lines:
        line = raw_line.strip(" │")
        if "Commands" in line:
            in_commands = True
            continue
        if not line:
            if capture and block:
                break
            continue
        if not in_commands:
            continue
        token = line.split()[0]
        if token == command_name:
            capture = True
            block.append(line)
            continue
        if capture:
            if token in CW_HELP_COMMANDS:
                break
            block.append(line)

    return _normalize_help(" ".join(block))
```

- [ ] **Step 2: 先写失败测试，冻结 `trail cw --help` 的顶层总述与全子组锚点**

```python
def test_cw_help_exposes_scene_command_groups(cli_runner):
    result = cli_runner.invoke(app, ["cw", "--help"])
    normalized = _normalize_help(result.output)

    assert result.exit_code == 0
    assert "货币战争固定流程命令" in normalized
    assert "enter 到首页" in normalized
    assert "start 从首页进入投资环境页" in normalized
    assert "enter" in _extract_help_command_block(result.output, "enter")
    assert "start" in _extract_help_command_block(result.output, "start")

    expected_blocks = {
        "guide": "当前对局攻略",
        "portal": "投资环境页",
        "stage": "检测",
        "slots": "编队",
        "shop": "商店",
        "crystals": "结晶",
        "hand": "手牌",
        "replenish": "补给事件",
        "invest": "局内 invest 事件",
        "encounter": "遭遇事件",
        "fortune": "命运卜者事件",
        "boss-preview": "首领预览",
        "battle": "战斗",
        "settle": "结算",
        "event": "通用/特殊事件",
    }

    for command_name, anchor in expected_blocks.items():
        block = _extract_help_command_block(result.output, command_name)
        assert command_name in block
        assert anchor in block
```

- [ ] **Step 3: 先写失败测试，冻结高混淆子组页自己的摘要**

```python
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["cw", "portal", "--help"], "投资环境页上的选择/刷新/重开动作"),
        (["cw", "invest", "--help"], "局内 invest 事件"),
        (["cw", "stage", "--help"], "检测或等待当前货币战争阶段"),
        (["cw", "guide", "--help"], "当前对局已选攻略"),
        (["cw", "shop", "--help"], "读取商店、购买槽位并刷新或关闭"),
        (["cw", "event", "--help"], "通用/特殊事件"),
    ],
)
def test_cw_group_help_describes_expected_boundary(cli_runner, args, expected):
    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert expected in _normalize_help(result.output)


@pytest.mark.parametrize(
    ("args", "unexpected"),
    [
        (["cw", "portal", "--help"], "进入投资环境页"),
        (["cw", "guide", "--help"], "攻略列表"),
        (["cw", "stage", "--help"], "推进流程"),
        (["cw", "event", "--help"], "处理所有事件"),
    ],
)
def test_cw_group_help_avoids_forbidden_phrases(cli_runner, args, unexpected):
    result = cli_runner.invoke(app, args)

    assert result.exit_code == 0
    assert unexpected not in _normalize_help(result.output)
```

- [ ] **Step 4: 跑红灯，确认当前 `cw` help 仍然缺摘要**

Run: `uv run pytest tests/test_atomic_commands.py -k "cw_help_exposes_scene_command_groups or cw_group_help_describes_expected_boundary or cw_group_help_avoids_forbidden_phrases" -q --basetemp .pytest_tmp/cw-help-red`

Expected: FAIL，因为当前 `trail/commands/cw.py` 只有命令名，没有顶层总述和子组摘要。

### Task 2: 在 `trail/commands/cw.py` 补顶层与子组摘要

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `tests/test_atomic_commands.py`

- [ ] **Step 1: 先把 help 文案冻结成常量，避免后续散落改词**

```python
CW_APP_HELP = "货币战争固定流程命令：enter 到首页，start 从首页进入投资环境页；其余分组处理局内阶段与资源。"
CW_GUIDE_HELP = "应用或回顾当前对局已选攻略。"
CW_PORTAL_HELP = "投资环境页上的选择/刷新/重开动作。"
CW_STAGE_HELP = "检测或等待当前货币战争阶段。"
CW_SLOTS_HELP = "读取编队槽位并执行换位或上场。"
CW_SHOP_HELP = "读取商店、购买槽位并刷新或关闭。"
CW_CRYSTALS_HELP = "收取当前局内结晶产出。"
CW_HAND_HELP = "出售手牌或生成出售候选。"
CW_REPLENISH_HELP = "读取或选择局内补给事件。"
CW_INVEST_HELP = "读取或选择局内 invest 事件。"
CW_ENCOUNTER_HELP = "读取或选择局内遭遇事件。"
CW_FORTUNE_HELP = "读取或选择局内命运卜者事件。"
CW_BOSS_PREVIEW_HELP = "确认首领预览并继续战斗前阶段。"
CW_BATTLE_HELP = "处理战斗开始与战后继续。"
CW_SETTLE_HELP = "处理整局结算链页面。"
CW_EVENT_HELP = "处理其余通用/特殊事件节点。"
```

- [ ] **Step 2: 最小实现 `cw_app` 和各子组 `Typer(help=...)`**

```python
cw_app = typer.Typer(no_args_is_help=True, help=CW_APP_HELP)
cw_guide_app = typer.Typer(no_args_is_help=True, help=CW_GUIDE_HELP)
portal_app = typer.Typer(no_args_is_help=True, help=CW_PORTAL_HELP)
stage_app = typer.Typer(no_args_is_help=True, help=CW_STAGE_HELP)
slots_app = typer.Typer(no_args_is_help=True, help=CW_SLOTS_HELP)
shop_app = typer.Typer(no_args_is_help=True, help=CW_SHOP_HELP)
crystals_app = typer.Typer(no_args_is_help=True, help=CW_CRYSTALS_HELP)
hand_app = typer.Typer(no_args_is_help=True, help=CW_HAND_HELP)
replenish_app = typer.Typer(no_args_is_help=True, help=CW_REPLENISH_HELP)
invest_app = typer.Typer(no_args_is_help=True, help=CW_INVEST_HELP)
encounter_app = typer.Typer(no_args_is_help=True, help=CW_ENCOUNTER_HELP)
fortune_app = typer.Typer(no_args_is_help=True, help=CW_FORTUNE_HELP)
boss_preview_app = typer.Typer(no_args_is_help=True, help=CW_BOSS_PREVIEW_HELP)
battle_app = typer.Typer(no_args_is_help=True, help=CW_BATTLE_HELP)
settle_app = typer.Typer(no_args_is_help=True, help=CW_SETTLE_HELP)
event_app = typer.Typer(no_args_is_help=True, help=CW_EVENT_HELP)
```

- [ ] **Step 3: 跑绿灯，确认 CLI help 合约成立**

Run: `uv run pytest tests/test_atomic_commands.py -k "cw_help_exposes_scene_command_groups or cw_group_help_describes_expected_boundary or cw_group_help_avoids_forbidden_phrases" -q --basetemp .pytest_tmp/cw-help-green`

Expected: PASS，`trail cw --help` 出现顶层总述，`trail cw <group> --help` 对关键分组给出正确职责摘要。

- [ ] **Step 4: 手工冒烟 2 条帮助输出，确认 Rich/Typer 排版没有把摘要截断成不可读内容**

Run: `uv run trail cw --help`

Expected: 在 `Usage` 下能看到 `货币战争固定流程命令` 总述，并且命令表里的子组行带简短说明。

Run: `uv run trail cw portal --help`

Expected: 在该子组帮助页看到 `投资环境页上的选择/刷新/重开动作`，且没有把 `portal` 说成“进入投资环境页”。

Run: `uv run trail cw event --help`

Expected: 只描述其余通用/特殊事件节点，不把自己写成“处理所有事件”的总入口。

### Task 3: 同步 README / skills，并用独立文档测试锁住边界

**Files:**
- Modify: `README.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Check: `skills/trail-cw-replenish/SKILL.md`
- Check: `skills/trail-cw-events/SKILL.md`
- Check: `skills/trail-cw-shop/SKILL.md`
- Check: `skills/trail-cw-slots/SKILL.md`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 先写失败测试，锁定 README 与关键 skill 的边界句**

```python
def test_readme_and_cw_skills_document_help_boundaries() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    cw = (PROJECT_ROOT / "skills" / "trail-cw" / "SKILL.md").read_text(encoding="utf-8")
    guide = (PROJECT_ROOT / "skills" / "trail-cw-guide" / "SKILL.md").read_text(encoding="utf-8")
    replenish = (PROJECT_ROOT / "skills" / "trail-cw-replenish" / "SKILL.md").read_text(encoding="utf-8")
    events = (PROJECT_ROOT / "skills" / "trail-cw-events" / "SKILL.md").read_text(encoding="utf-8")
    shop = (PROJECT_ROOT / "skills" / "trail-cw-shop" / "SKILL.md").read_text(encoding="utf-8")
    slots = (PROJECT_ROOT / "skills" / "trail-cw-slots" / "SKILL.md").read_text(encoding="utf-8")

    assert "货币战争固定流程命令；`enter` 到首页，`start` 从首页进入投资环境页" in readme
    assert "`trail cw guide` 只负责当前对局攻略的 apply/current" in readme
    assert "`trail cw invest.read|choose` 继续只表示局内 invest 事件" in readme
    assert "`trail cw guide` 只负责当前对局攻略的 apply/current" in cw
    assert "`trail cw stage` 只负责检测或等待" in cw
    assert "`trail cw guide apply/current` 只面向当前对局已选攻略；攻略查询与拉取继续使用顶层 `trail guide ... cw`" in guide
    assert "`trail cw invest.read|choose` 继续只表示局内 invest 事件" in replenish
    assert "处理 Boss 预览、特殊事件、结算翻页与战斗继续" in events
    assert "不负责商店、补给和整局循环" in events
    assert "已经完成 `trail cw start` 和 `trail cw portal.select`" in shop
    assert "已经完成 `trail cw start` 和 `trail cw portal.select`" in slots
```

- [ ] **Step 2: 最小更新 README 的三处同步面**

```md
- `cw`：货币战争固定流程命令；`enter` 到首页，`start` 从首页进入投资环境页，其余分组处理投资环境页、攻略、阶段识别和局内事件/资源

- `trail cw enter --session <id>` 只负责把页面带到货币战争首页
- `trail cw start --session <id> ...` 负责把首页推进到投资环境页
- `trail cw guide` 只负责当前对局攻略的 apply/current；筛攻略和拉攻略仍使用 `trail guide ... cw`
- `trail cw invest.read|choose` 继续只表示局内 invest 事件，不是开局投资环境页命令
```

- [ ] **Step 3: 最小更新 `skills/trail-cw/SKILL.md` 与 `skills/trail-cw-guide/SKILL.md`；其余 skill 仅在断言失败时做单行修正**

```md
# skills/trail-cw/SKILL.md
- `trail cw guide` 只负责当前对局攻略的 apply/current；攻略查询与拉取继续使用 `trail guide ... cw`
- `trail cw stage` 只负责检测或等待，不代替分组动作执行

# skills/trail-cw-guide/SKILL.md
- `trail cw guide apply/current` 只面向当前对局已选攻略；攻略查询与拉取继续使用顶层 `trail guide ... cw`

# 仅当 Step 1 / Step 5 断言暴露漂移时，才最小修正：

# skills/trail-cw-replenish/SKILL.md
- 仅在“局内 invest 事件”边界缺失时补一行

# skills/trail-cw-events/SKILL.md / skills/trail-cw-shop/SKILL.md / skills/trail-cw-slots/SKILL.md
- 仅在已有边界句缺失或漂移时做单行修正
```

- [ ] **Step 4: 若文档断言暴露 check-only 文件漂移，只修正失败文件中的单句边界**

Run: `uv run pytest tests/test_output_rendering.py -k "readme_and_cw_skills_document_help_boundaries" -q --basetemp .pytest_tmp/cw-help-docs-red`

Expected: 若 FAIL，只修改失败断言对应的那一行边界句；若 PASS，`skills/trail-cw-replenish`、`skills/trail-cw-events`、`skills/trail-cw-shop`、`skills/trail-cw-slots` 保持不改。

- [ ] **Step 5: 跑绿灯，确认文档同步断言成立**

Run: `uv run pytest tests/test_output_rendering.py -k "readme_and_cw_skills_document_help_boundaries" -q --basetemp .pytest_tmp/cw-help-docs`

Expected: PASS，README 与关键 skill 中的边界句和 CLI help 设计保持一致。

### Task 4: 最终验证与交接

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `README.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Check: `skills/trail-cw-replenish/SKILL.md`
- Check: `skills/trail-cw-events/SKILL.md`
- Check: `skills/trail-cw-shop/SKILL.md`
- Check: `skills/trail-cw-slots/SKILL.md`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 运行本 feature 的最小回归集**

Run: `uv run pytest tests/test_atomic_commands.py tests/test_output_rendering.py -k "cw_help_exposes_scene_command_groups or cw_group_help_describes_expected_boundary or cw_group_help_avoids_forbidden_phrases or readme_and_cw_skills_document_help_boundaries" -q --basetemp .pytest_tmp/cw-help-final`

Expected: PASS，CLI help 与文档同步两组断言同时为绿。

- [ ] **Step 2: 用真实 CLI 输出做最终人工复核**

Run: `uv run trail cw --help`

Expected: `enter 到首页`、`start 从首页进入投资环境页` 出现在顶层总述；`guide/portal/stage/invest/...` 在命令表中各有一句职责摘要。

Run: `uv run trail cw guide --help`

Expected: 只描述当前对局攻略 apply/current，不出现“列表”“筛选”“拉取攻略”这类顶层 `trail guide` 术语。

Run: `uv run trail cw invest --help`

Expected: 明确写成局内 invest 事件，不与投资环境页混淆。

Run: `uv run trail cw stage --help`

Expected: 只描述检测/等待，不写成“推进流程”。

Run: `uv run trail cw event --help`

Expected: 只描述其余通用/特殊事件节点，不写成“处理所有事件”。

- [ ] **Step 3: 记录 worktree 与环境说明，准备交给子代理执行**

```text
Worktree: C:\Users\34404\source\repos\trail-cli\.worktrees\cw-help-summary
Verification note: 当前环境的 pytest 必须显式使用 --basetemp .pytest_tmp/<task-name>
No git commit unless the user explicitly asks for one.
```
