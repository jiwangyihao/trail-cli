> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。

# 货币战争攻略相关命令中文输出收口设计

## 背景

`guide.fetch.cw` 已经收口成以中文字段名为主的高信息密度文本协议，但同一条攻略链路上的其他命令仍然暴露大量原始字段名，主要包括：

- `guide.list.cw`
- `cw.start`
- `cw.portal.select`
- `cw.portal.refresh`
- `cw.portal.restart`
- `cw.guide.current`
- `cw.guide.apply`
- `guide.config.cw`

这会带来两个问题：

1. Agent 在 `list -> fetch -> apply -> current` 这条链路上需要来回切换多套命名体系，例如 `hard`、`change_equip`、`expert`、`final_roles`、`portal_list`。
2. 同样的 guide 语义，在不同命令里以英文缩写、布尔开关或内部字段名出现，不利于直接决策和协议记忆。

本轮目标是在不改变结构化 payload shape、也不改 CLI 参数名的前提下，把默认文本协议尽量统一到 `guide.fetch.cw` 的中文语义体系。

## 目标

1. 统一 guide 摘要家族的中文命名，减少 `guide.list.cw`、`cw.start`、`cw.portal.*`、`cw.guide.current|apply` 之间的语义跳变。
2. 保留控制类冻结字段：`count`、`more`、`next`、`idx`、`gid`、`score`、`cards`、`groups`。
3. 把用户更关心的内容字段收口为中文协议名，例如 `攻略标题`、`版本`、`主C`、`攻略标签`、`最终阵容`。
4. `guide.config.cw` 的默认文本改成中文统计摘要，但 YAML shape 保持不变。
5. 不改变 failure 路径顺序，不新增 YAML allowlist，不改 CLI flags，不改 daemon/scene 的结构化 key。

## 非目标

1. 不改 `guide.fetch.cw` 已经落稳的中文协议。
2. 不改 `guide.list.cw` / `guide.config.cw` 的 YAML 数据 shape。
3. 不把 `--match-hard`、`--portal-id` 之类输入参数中文化。
4. 不扩写 list/current 命令的信息密度到 `guide.fetch.cw` 的完整级别。

## 设计原则

1. 默认文本优先面向“立即决策”，不是忠实转录上游字段名。
2. 控制字段维持稳定，内容字段中文化。
3. 布尔类攻略特征优先折叠成 `攻略标签=#...`，避免继续暴露 `0/1` 技术位。
4. 同一类实体在不同命令里尽量复用同一字段名。
5. YAML 仍作为结构化兜底，文本层只做面向决策的语义收口。

## 冻结与顺序约束

1. success 路径顺序仍固定为：首行 -> `shot`（如有）-> 实体行（`guide` / `opt` / `info`）-> `warn` -> `ref`。
2. 本轮只中文化内容字段，不中文化控制字段；`count`、`more`、`next`、`idx`、`gid`、`score`、`cards`、`groups` 继续保持现名和现有顺序。
3. `guide.list.cw` 首行继续是：
   - 非分组：`ok guide.list.cw count=... more=... next=...`
   - 分组：`ok guide.list.cw groups=... count=... more=...`
4. `cw.start` / `cw.portal.refresh` / `cw.portal.restart` 首行继续是 `ok <command> cards=...`。
5. `待收集` 必须固定输出 `0/1`，不能因为值为 `0` 而省略。
6. `guide.config.cw` 的五个统计字段 `搜牌档位/羁绊/角色/角色标签/投资环境` 必须固定输出，即使值为 `0` 也不能省略。
7. `guide.list.cw` 的 failure 路径保持现有候选提示格式，继续使用 `warn portal=... score=...`，不改成中文字段名。

## 命令级设计

### 1. `guide.list.cw`

#### 非分组列表

每条攻略摘要改成：

```text
ok guide.list.cw count=1 more=0
guide 攻略ID=abc 攻略标题=购物阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45
guide idx=1 最终阵容=希儿/carry:1/star:5/rarity:3|布洛妮娅/star:5/rarity:3
```

映射规则：

- `id` -> `攻略ID`
- `title` -> `攻略标题`
- `version` -> `版本`
- `carry` -> `主C`
- `like` -> `点赞`
- `favour` -> `收藏`
- `final_roles` -> `最终阵容`
- `support_hard` / `has_change_equip` / `has_expert` 与现有 `labels` 一起折叠进 `攻略标签`

其中 `攻略标签` 的折叠语义与 `guide.fetch.cw` 保持一致：

- `support_hard` -> `#适用超频博弈`
- `has_change_equip` -> `#星徽攻略`
- `has_expert` -> `#专家顾问`

#### 分组列表

按投资环境分组时，把 `portal` 也改成中文：

```text
ok guide.list.cw groups=1 count=1 more=0
guide 投资环境=购物区 count=1 more=0
guide 投资环境=购物区 攻略ID=shop-guide 攻略标题=购物区优选阵容 版本=3.2 idx=1 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45
guide 投资环境=购物区 idx=1 最终阵容=希儿/carry:1/star:5/rarity:3
```

`count`、`more`、`next`、`idx` 继续保留英文冻结字段名。

若 `labels` 为空或缺失，`攻略标签` 仍允许只基于 `support_hard/has_change_equip/has_expert` 生成；若这三类布尔特征也都不存在或都为 false，则整字段省略。

### 2. `cw.start` / `cw.portal.select` / `cw.portal.refresh` / `cw.portal.restart`

这组命令里有两类输出：投资环境卡片本身，以及卡片下挂的 guide 摘要。

#### 投资环境卡片行

把 `title` / `desc` / `new` 改成中文：

```text
ok cw.start cards=2
opt idx=1 投资环境="Alpha Portal" score=0.99 待收集=1
opt idx=1 说明="Alpha Desc"
```

其中：

- `title` -> `投资环境`
- `desc` -> `说明`
- `new` -> `待收集`

`idx` 与 `score` 保持冻结字段名。

`待收集` 统一按 `0/1` 输出；当当前卡片不是待收集状态时，也显式输出 `待收集=0`。

#### `cw.portal.select`

为避免同一家族前后命名不一致，`cw.portal.select` 也一并改为中文：

```text
ok cw.portal.select idx=2 投资环境=购物区
```

#### 卡片下挂的 guide 行

完全复用 `guide.list.cw` 的中文摘要格式，只额外保留 `gid` 作为“卡片内第几条推荐攻略”的稳定定位字段，例如：

```text
guide idx=1 gid=1 攻略ID=alpha-guide 攻略标题=Alpha攻略 版本=3.2 主C=希儿 攻略标签=#适用超频博弈|#专家顾问 点赞=123 收藏=45
guide idx=1 gid=1 最终阵容=希儿/carry:1/star:5/rarity:3
```

### 3. `cw.guide.current` / `cw.guide.apply`

当前这两个命令只输出 `id/artifact`，信息量不足，且 `artifact` 不是用户最先关心的事实。

本轮改为：

```text
ok cw.guide.current 攻略ID=abc 攻略标题=7群攻2银河学者 攻略码=##demo## 版本=3.2
guide 攻略标签=#7级搜牌|#适用超频博弈
info 攻略快照ID=art-1
```

规则：

- `cw.guide.current` 与 `cw.guide.apply` 共用同一套摘要规则；`cw.guide.apply` 只是可能额外带 `shot`。
- success 路径顺序固定为：首行 -> `shot`（如有）-> `guide 攻略标签=...`（如有）-> `info 攻略快照ID=...`（如有）。
- 首行按 `攻略ID -> 攻略标题 -> 攻略码 -> 版本` 的固定顺序尽力输出；缺失字段按位省略，不发明占位值。
- `guide 攻略标签=...` 仅在至少有 1 个标签时输出。
- `artifact` 下沉为 `info 攻略快照ID=...`；该值是 artifact id，用于恢复与追踪，不是 `shot path` 图片路径。
- failure / recover 语义完全不改

这里不直接展开 `投资环境`、`运营思路`、角色推荐装备等高密度内容，避免 `current|apply` 与 `fetch` 重复。

### 4. `guide.config.cw`

默认文本摘要从原始字段名收口成中文统计：

```text
ok guide.config.cw 赛季=12 子赛季=3 大版本=3.2
info 搜牌档位=1 羁绊=2 角色=3 角色标签=1 投资环境=2
```

映射规则：

- `season` -> `赛季`
- `sub_season` -> `子赛季`
- `big_version` -> `大版本`
- `lineup_levels` -> `搜牌档位`
- `traits` -> `羁绊`
- `roles` -> `角色`
- `role_tags` -> `角色标签`
- `portal_list` -> `投资环境`

`--format yaml` 继续先输出这两行，再跟原来的结构化 YAML。

这里的 `大版本` 表示全局 config 的 `big_version`，不是单条攻略自己的适配版本；单条攻略继续使用 `版本`。

## 共享 helper 设计

为避免 `guide.list.cw` 与 `cw.start` / `cw.portal.*` 再次分叉，本轮应把 guide 摘要行的字段拼装抽成共享 helper，并把 `guide.fetch.cw` 已有的标签折叠逻辑作为 source of truth，至少统一以下逻辑：

1. `攻略标签` 的折叠规则
2. 单条 guide 摘要的一行字段顺序
3. `最终阵容` 的第二行渲染
4. 门户分组场景下 `投资环境=...` 的前缀追加
5. 与 `guide.fetch.cw` 一致的标签顺序与布尔特征折叠语义

`cw.guide.current|apply` 可以复用同一个标签 helper，但首行仍走独立 summary helper，因为它们的核心事实与 list 家族不同。

## 兼容性与风险

### 兼容性

1. 这轮会改变默认文本协议，因此需要同步更新 README、AGENTS 和技能文档。
2. YAML 数据 shape 不变，依赖 `--format yaml` 的自动化仍可继续使用现有 key。
3. failure 路径、恢复语义、请求号等控制流约定不变。

### 风险

1. 旧测试和 README 示例大量使用 `hard/change_equip/expert/final_roles/portal/title/desc/new`，需要一次性同步，否则很容易产生假失败。
2. `guide.list.cw` 相关历史设计文档里曾冻结英文字段名；本轮以项目级 `AGENTS.md`、README 和最新测试为准，并明确保留 list 阶段已有的 `版本` 事实，不回退到旧英文字段约束。
3. `cw.guide.current` 在从 artifact 恢复时，部分字段可能为空；渲染器必须容忍稀疏 payload，避免为追求“中文完整摘要”而输出伪造值。

## 测试计划

至少覆盖以下断言：

1. `tests/test_output_rendering.py`
   - `guide.list.cw` 非分组列表使用中文字段名
   - `guide.list.cw` 保留 `版本`，并验证 `攻略标签` 在稀疏 payload 下的省略/回退规则
   - `guide.list.cw` 分组列表把 `portal=` 改为 `投资环境=`
   - `cw.start` / `cw.portal.refresh|restart` 的 `opt` 行改用 `投资环境/说明/待收集`，且 `待收集=0/1` 都有断言
   - `cw.portal.select` 改成 `投资环境=`
   - 卡片下挂 guide 行改用中文摘要字段
   - `cw.guide.current|apply` 输出首行核心事实、`guide 攻略标签=` 与 `info 攻略快照ID=`
   - `cw.guide.current|apply` 对稀疏 payload 只省略缺失字段，不输出伪造占位值
   - `guide.config.cw` 输出中文摘要统计，并验证 0 计数也保留
   - `guide.config.cw --format yaml` 的 renderer 级断言同步更新
2. `tests/test_guide_rpc_contracts.py`
   - `guide.list.cw` / `guide.config.cw` CLI stdout golden 更新
   - `guide.config.cw --format yaml` 仍保留中文摘要 + 原 YAML
3. `tests/test_cw_rpc_contracts.py`
   - `cw.start` / `cw.portal.select` / `cw.portal.refresh` / `cw.portal.restart` stdout golden 更新
   - `cw.guide.current` / `cw.guide.apply` stdout golden 更新
4. `tests/test_output_debug.py`
   - `AGENTS.md` 里新增的 guide/cw 中文协议约束断言同步更新
5. README / skills / help 相关断言
   - `tests/test_output_rendering.py` 里现有 README / skills 文本断言同步扩展，覆盖 `guide.list.cw` 分组示例、`cw.portal.*` 的 `投资环境/说明/待收集`、`cw.guide.current|apply` 与 `guide.fetch.cw` 的职责边界、`guide.config.cw` 的中文统计首屏与 YAML shape 说明
   - `tests/test_atomic_commands.py` 中与 `guide` / `cw guide` 相关的 help 断言同步更新，避免 help/docstring 仍残留 `hard/change_equip/expert/final_roles/artifact` 这类旧术语

## 文档同步

本轮至少同步：

1. `README.md`
2. `AGENTS.md`
3. `skills/trail-cw-guide/SKILL.md`
4. `skills/trail-cw/SKILL.md`
5. `trail/commands/guide.py` 与相关 help/docstring

README 示例应明确体现：

- `guide.list.cw` 已中文化的条目摘要
- `guide.list.cw` 分组视图里的 `投资环境=...`
- `cw.start` / `cw.portal.*` 的 `投资环境/说明/待收集`
- `cw.guide.current|apply` 的新摘要，以及它与 `guide.fetch.cw` 的职责边界
- `guide.config.cw` 的中文统计首屏与 `--format yaml` 仍保持英文 shape

## 验证策略

实现阶段按 TDD 执行：

1. 先改 golden tests，确认 RED
2. 再最小实现 renderer helper 与相关命令输出
3. 最后跑定向回归：
   - `tests/test_output_rendering.py`
   - `tests/test_guide_rpc_contracts.py`
   - `tests/test_cw_rpc_contracts.py`
   - `tests/test_output_debug.py`
4. README / AGENTS / skills / help 文案与断言必须作为 mandatory sync 一次性更新，不是“视影响再补”的可选项
