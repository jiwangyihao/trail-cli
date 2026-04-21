# Trail Skill 系统重设计说明

## 背景

当前仓库的 skill 系统已经出现三个结构性问题：

1. `skills/` 目录同时承载对外入口、场景编排、内部恢复、协议约束与历史遗留流程，职责层次混杂。
2. `README.md`、`AGENTS.md`、`skills/*/SKILL.md`、`tests/test_output_rendering.py` 之间存在大量文本级硬绑定，新增或调整一个 skill 的成本过高。
3. 旧的 `trail-cw*` skill 仍处于 active namespace 中，但用户已经决定这一组 skill 要彻底下线并重新设计。

同时，这次重设计还需要吸收两类外部经验：

1. `anthropics/skills` 强调 skill 的 `description` 是触发入口，`SKILL.md` 应保持短正文，复杂资料应下沉到 `references/`，并使用真实 prompt 做触发与效果验证。
2. `obra/superpowers` 强调 process-first 的 skill 设计，要求把“用户怎么触发”“内部怎么升级”“子 skill 怎么协作”明确分层，而不是把所有知识堆成一个大 prompt。

本设计的目标，是把当前 Trail skill 系统收敛成清晰的三层结构：对外总入口、对外场景入口、内部恢复层。

## 目标

1. 将 `trail-hsr` 重定义为《崩坏：星穹铁道》自动游玩的总入口 skill。
2. 将 `trail-hsr-advanced` 重定义为仅供内部升级调用的恢复 / control-plane skill。
3. 将未来各具体玩法 skill 统一收口为 `trail-<scene>-entry` 形式的对外场景入口。
4. 将旧 `trail-cw*` 从 active `skills/` namespace 中彻底移出，转入归档路径。
5. 引入可机读的 scene entry registry，替代只靠 prose 维护 skill 路由关系。
6. 将 skill 验证从全文句子硬编码收紧为结构测试、语义测试与触发 / 升级测试三层。

## 非目标

1. 本轮不重做新的 `trail-cw-entry`。
2. 本轮不改 CLI 原子命令的外部语义。
3. 本轮不试图一次性设计所有未来 scene skill 的细节。
4. 本轮不把 `trail-hsr-advanced` 暴露成用户直接入口。
5. 本轮不通过一个 skill 直接替代所有 scene entry skill。

## 当前状态总结

当前主分支实际存在 9 个 skill：

- `trail-hsr`
- `trail-hsr-advanced`
- `trail-cw`
- `trail-cw-battle-advanced`
- `trail-cw-guide`
- `trail-cw-events`
- `trail-cw-replenish`
- `trail-cw-shop`
- `trail-cw-slots`

其中：

1. `trail-hsr` 当前仍是 simple-first 风格，但把自身定位得过于接近“基本原子动作入口”。
2. `trail-hsr-advanced` 当前虽然承担恢复职责，但在现有文档体系中仍然与对外能力描述耦合过重。
3. `trail-cw*` 这一整组 skill（包括 `trail-cw-battle-advanced`）仍然位于 `skills/` 下，存在被继续视为 active skill 的风险。

## 方案概览

采纳“三层 skill 体系 + registry + archive namespace”方案：

1. `trail-hsr` 作为对外总入口 skill。
2. `trail-<scene>-entry` 作为对外场景入口 skill。
3. `trail-hsr-advanced` 作为内部恢复层 skill。
4. 旧 `trail-cw*` 从 `skills/` 中迁出，放入归档目录。
5. 新增 `skills/registry/scene-entries.yaml` 表达 scene skill 的上线状态与暴露面。

## 详细设计

### 1. Skill 角色划分

#### 1.1 `trail-hsr`

`trail-hsr` 是唯一的游戏总入口 skill，其触发语义不是“用户要执行一个基础原子动作”，而是：

- 用户希望 Agent 接管并自动游玩《崩坏：星穹铁道》
- 用户希望 Agent 从当前状态继续游戏任务
- 用户希望 Agent 继续推进游戏，但当前没有一个已经 `active` 的 scene entry 被直接命中

`trail-hsr` 负责：

1. 建立或复用进入游戏的基本工作流。
2. 维护“何时继续常规游玩、何时切 scene entry、何时升级到 advanced”的上层判断。
3. 引用 scene entry index，而不是内嵌具体场景流程。

`trail-hsr` 不负责：

1. 直接承载某个玩法的具体流程说明。
2. 直接暴露底层恢复链路细节。

#### 1.2 `trail-<scene>-entry`

未来每个具体玩法的入口 skill 统一采用 `trail-<scene>-entry` 命名。

例如：

- `trail-cw-entry`

这类 skill 是对外 skill，触发语义是：

- 用户明确要做某个具体玩法 / 场景任务

这类 skill 负责：

1. 承接来自 `trail-hsr` 的上层路由。
2. 对外暴露该场景的入口与编排方式。
3. 在需要时内部升级到 `trail-hsr-advanced`。

#### 1.3 `trail-hsr-advanced`

`trail-hsr-advanced` 是内部恢复 skill，不作为用户直达入口。

它负责：

1. 处理启动链路失效、环境异常、窗口接管异常、状态恢复等底层问题。
2. 为 `trail-hsr` 与未来 scene entry skill 提供统一的恢复梯子。
3. 下沉 daemon / window / session / screen / image / state 等内部操作说明。

它不负责：

1. 作为用户直达的主 skill。
2. 承担任何具体玩法的业务流程。

#### 1.4 调用边界与 owner 交接

为避免 `trail-hsr` 与 `trail-<scene>-entry` 双编排，本轮固定以下 owner 规则：

1. 用户表达的是“接管 / 继续玩星铁”这类总入口意图时，由 `trail-hsr` 作为唯一 owner。
2. 用户明确表达某个具体 scene，且 registry 中该 entry 满足 `status=active` 且 `exposure=public` 时，由对应 `trail-<scene>-entry` 作为唯一 owner。
3. 用户明确表达某个具体 scene，但 registry 中该 entry 仍是 `planned` 时，不得回退到 archive skill，也不得把该 scene 的完整流程重新塞回 `trail-hsr`；此时只允许由 `trail-hsr` 说明该 entry 尚未上线或继续一般性游戏接管。
4. `trail-hsr` 一旦把控制权交给某个 `trail-<scene>-entry`，该 scene entry 在当前场景内成为唯一编排 owner；`trail-hsr` 不再并行保留场景级循环。
5. `trail-hsr` 与 `trail-<scene>-entry` 都可以在需要时升级调用 `trail-hsr-advanced`。
6. `trail-hsr-advanced` 完成恢复后必须把控制权返回调用它的上层 skill；它永远不是长期 owner。
7. 任何 active skill 都不得直接或间接调用 archive skill。

### 2. 目录结构

#### 2.1 Active skills

```text
skills/
  trail-hsr/
    SKILL.md
    references/
      simple-command-surface.md
      ocr-and-screenshot.md
      scene-entry-index.md
    evals/
      triggers.json
  trail-hsr-advanced/
    SKILL.md
    references/
      advanced-command-surface.md
      recovery-ladder.md
      request-status-and-taint.md
      window-launch.md
    evals/
      escalations.json
  registry/
    scene-entries.yaml
    routing-competition.json
  shared/
    escalation-contract.md
```

#### 2.2 Archived skills

```text
docs/superpowers/archive/skills/
  2026-04-21-cw-skill-snapshot/
    trail-cw/
    trail-cw-battle-advanced/
    trail-cw-guide/
    trail-cw-events/
    trail-cw-replenish/
    trail-cw-shop/
    trail-cw-slots/
    README.md
```

设计原则：

1. `skills/` 只保留 active、可触发或可升级调用的当前 skill。
2. 历史 skill 一律移出 active namespace，避免继续被当作当前生效的 skill 集合。
3. archive 快照目录保留原 skill 目录名与内容，并在归档根目录的 `README.md` 中记录快照日期与原始来源路径。

### 3. Frontmatter 与正文规范

#### 3.1 `description` 语言

本项目本轮明确采用中文 `description`。

原因：

1. 用户已经明确要求 `description` 改成中文。
2. 本项目主要面对中文任务语境，skill 触发也应对齐真实用户表达。

#### 3.2 `description` 语义

`description` 必须描述真实用户会表达的意图，而不是项目内部实现词。

同时固定两条硬规则：

1. `description` 只描述“何时使用”，不描述 workflow、命令序列、command family 或恢复步骤。
2. `description` 保持短句，不把 scene alias、内部术语、命令面和流程摘要堆成一个长入口提示词。

禁止把以下词当作主要触发入口：

- `daemon`
- `session`
- `tainted`
- “看看当前画面能做什么”
- “从哪个 skill 进”

这些词只允许出现在 skill 正文或 `references/` 中，作为 skill 已经触发后的内部工作流知识。

#### 3.3 `description` 初稿

`trail-hsr`：

```text
当用户希望 Agent 接管并自动游玩《崩坏：星穹铁道》，或从当前局面继续推进游戏任务时使用。
```

`trail-hsr-advanced`：

```text
当上层 Trail 技能在自动游玩过程中遇到启动失败、环境异常、窗口接管异常或需要恢复运行链路时使用。
```

这里特意把 `trail-hsr-advanced` 改成“上层技能使用”的内部描述，而不是用户直接触发描述。它仍然保留 `description`，但该 `description` 的服务对象是上层 skill / 模型内部的升级判断，而不是最终用户的直达触发文案。

#### 3.4 `SKILL.md` 正文骨架

`trail-hsr/SKILL.md`：

1. Role
2. Default Workflow
3. Scene Entry Index
4. Escalate to Advanced When
5. Reference Map

`trail-hsr-advanced/SKILL.md`：

1. Role
2. When To Use
3. Recovery Ladder
4. Command Families
5. Stop Conditions
6. Reference Map

正文原则：

1. 保持短正文。
2. 横切细节尽量进入 `references/`。
3. 正文以流程和边界为主，不再承担全部命令目录。

### 4. Scene entry registry

新增：

```text
skills/registry/scene-entries.yaml
```

建议结构：

```yaml
root_entry_skill: trail-hsr

entries:
  - scene: cw
    entry_skill: trail-cw-entry
    status: planned
    exposure: public
    aliases: ["货币战争", "Currency Wars", "cw"]

internal_skills:
  - name: trail-hsr-advanced
    status: active
    exposure: internal
    caller_roles: ["root_entry", "scene_entry"]
```

字段语义：

1. `scene`：场景稳定标识。
2. `entry_skill`：对应的对外场景入口 skill 名。
3. `status`：`planned` 或 `active`。
4. `exposure`：skill 的目标暴露面。只有 `status=active` 时才参与当前 public / internal contract；`planned + public` 只表示“上线后将对外暴露”，不表示当前已经可用。
5. `aliases`：用户可能使用的真实场景表达。
6. `root_entry_skill`：当前唯一的总入口 skill。
7. `internal_skills`：当前 active 但不对用户直出的内部 skill。
8. `caller_roles`：允许升级调用该 internal skill 的调用者角色枚举；本轮固定只允许 `root_entry` 与 `scene_entry`。

这份 registry 的职责：

1. 为 `trail-hsr` 提供 scene 索引来源。
2. 为 README、AGENTS 与测试提供单一事实源。
3. 明确过渡期状态：例如 `trail-cw-entry` 尚未重做完成时，可保留 `planned + public` 的目标定义，而不必继续引用旧 `trail-cw*`。
4. 只要 `status!=active`，该 entry 就不得出现在当前 active README 示例、current user-facing skill 清单、或 public trigger eval 的 winner 结果里。
5. 这份 registry 仅供 repo 内 skill 文档、README、AGENTS 与测试消费，不作为当前打包 runtime 资源接入 `trail` Python 运行时代码。

### 5. References 分工

`trail-hsr/references/simple-command-surface.md`

- `trail start`
- `trail ocr read`
- `trail input ...`
- 默认工作流边界

`trail-hsr/references/ocr-and-screenshot.md`

- screenshot-first
- OCR 模式
- `retry-high`
- 观察优先于立即动作的边界

`trail-hsr/references/scene-entry-index.md`

- scene entry 概念说明
- 如何查看 registry
- 何时保留在 `trail-hsr`
- 何时切到 `trail-<scene>-entry`

`skills/shared/escalation-contract.md`

- 哪些异常由 public caller 判断升级到 advanced
- 升级调用时要携带哪些事实
- advanced 返回后由谁继续作为 owner
- `trail-hsr` 与未来 `trail-<scene>-entry` 都引用这一份共享契约，不各自发明版本

`trail-hsr-advanced/references/advanced-command-surface.md`

- daemon / window / session / screen / image / state 的命令面

`trail-hsr-advanced/references/recovery-ladder.md`

- 启动失败
- 结果未知
- 恢复顺序
- 何时停止自动重试

`trail-hsr-advanced/references/request-status-and-taint.md`

- `request id`
- `tainted=1`
- `reconcile-session`
- advanced 内部如何消费这些恢复事实

`trail-hsr-advanced/references/window-launch.md`

- channel
- 历史路径
- 默认路径
- 错误码与回退策略

### 6. 测试与验证策略

#### 6.1 结构测试

新增 skill 结构测试，至少覆盖：

1. active skill 只有当前允许的 skill。
2. 旧 `trail-cw*` 不再位于 `skills/` namespace 中。
3. 每个 active skill 都存在：
   - `SKILL.md`
   - `references/`
   - `name`
   - `description`
4. archive 根目录存在并带有来源清单 `README.md`。
5. registry 中的 `root_entry_skill`、`entries`、`internal_skills` 均满足 schema 约束。
6. `docs/superpowers/archive/skills/2026-04-21-cw-skill-snapshot/` 下必须完整保留：
   - `trail-cw/`
   - `trail-cw-battle-advanced/`
   - `trail-cw-guide/`
   - `trail-cw-events/`
   - `trail-cw-replenish/`
   - `trail-cw-shop/`
   - `trail-cw-slots/`
   且各目录都保留 `SKILL.md`。

#### 6.2 语义测试

测试不再依赖大量整句硬编码，而是检查稳定语义：

1. `trail-hsr` 的 description 覆盖：
   - 自动游玩
   - 继续任务
   - 总入口意图
2. `trail-hsr-advanced` 的 description 覆盖：
   - 上层技能升级调用
   - 启动失败或环境异常
   - 恢复运行链路
3. registry 中所有 `status=active` 的 `entry_skill` 必须真实存在。
4. `planned` 允许尚未落地，但不得指向 archive skill。
5. README 当前入口清单只允许出现 `status=active` 且 `exposure=public` 的 skill。
6. `trail-hsr-advanced` 只能出现在 internal active 清单里，不能作为用户入口列出。
7. public skill 的 `description` 不得出现命令名、`daemon`、`session`、`tainted`、archive skill 名，或明显 workflow 连接词。
8. internal skill 的 `description` 必须保持“上层 skill / 内部升级”视角，不得写成用户直达入口文案。

#### 6.3 用户触发 eval、竞争路由与内部升级测试

`trail-hsr`：

- 做用户意图触发 eval。
- 文件位置固定为 `skills/trail-hsr/evals/triggers.json`。
- 至少包含三类样本：
  1. `should-trigger`
  2. `should-not-trigger`
  3. 与 scene entry 竞争时谁应该赢的 near-miss 样本
- 每类样本至少 8 条。
- prompt 必须贴近真实用户表达，例如“帮我继续玩星铁”“帮我接管当前游戏继续任务”。

`trail-<scene>-entry`：

- 做具体玩法触发 eval。
- 文件位置固定为 `skills/trail-<scene>-entry/evals/triggers.json`。
- 至少包含三类样本：
  1. `should-trigger`
  2. `should-not-trigger`
  3. 与 `trail-hsr` 的竞争样本
- 每类样本至少 8 条。
- prompt 必须贴近用户对该玩法的真实任务说法。

`trail-hsr-advanced`：

- 不做 `should-trigger` 的用户 prompt eval。
- 文件位置固定为 `skills/trail-hsr-advanced/evals/escalations.json`。
- 仍必须补一组 direct-user `should-not-trigger` / near-miss 样本，证明它不会被用户直接误命中；这组样本至少 8 条，且 `expected_winner` 只能是 public skill 或 `none`，不能是 `trail-hsr-advanced`。
- 改做上层升级调用测试，例如：
  1. 启动链路失败时是否应该升级
  2. 环境异常时是否应该升级
  3. 恢复完成后是否应回到上层 skill
- internal escalation 样本至少 6 条。

`skills/trail-hsr-advanced/evals/escalations.json` 采用 union schema：

1. direct-user negative 样本必须包含：
   - `sample_type`
   - `prompt`
   - `expected_winner`
2. internal escalation 样本必须包含：
   - `sample_type`
   - `caller_role`
   - `scenario`
   - `expected_action`
   - `expected_return_owner`

repo 级竞争路由样本：

- 文件位置固定为 `skills/registry/routing-competition.json`。
- 至少覆盖：
  1. `trail-hsr` vs `trail-<scene>-entry`
  2. `trail-hsr` vs `trail-hsr-advanced`
  3. 明确 scene 但 `status=planned` 的回退结果
  4. 当 repo 中存在 2 个及以上 `active + public` 的 scene entry 时，必须补 `scene-entry vs scene-entry` 的 near-miss / alias 冲突样本
- competition 样本至少 6 条。

每条 public trigger / competition 样本至少包含：

1. `prompt`
2. `sample_type`
3. `expected_winner`
4. 如涉及竞争，附 `candidates`

#### 6.4 评审循环 / rollout gate

为避免这次重设计沦为“只改文档不验证触发”，完成实现前必须跑一轮对照评审：

1. 选取 5-10 条真实中文 prompt。
2. 对比“旧 skill 集合”与“新 skill 集合”的触发结果、误触发情况与升级路径。
3. 至少做一轮人工 review，确认：
   - `trail-hsr` 没有过窄成原子动作 skill
   - `trail-hsr-advanced` 没有被直接误触发
   - active scene entry 与总入口之间的 winner 规则符合 registry 约束
4. 如发现问题，先修正 skill / registry / eval，再重新 review。

### 7. README / AGENTS / tests 收口

#### 7.1 README

README 只保留当前 active 结构：

1. `trail-hsr` 是对外总入口。
2. `trail-<scene>-entry` 是对外场景入口。
3. `trail-hsr-advanced` 是内部恢复层，不作为用户入口，不出现在推荐入口或用户直达清单中。

不再在 README 中把旧 `trail-cw*` 作为当前 active skill 体系进行介绍。只有 `status=active` 且 `exposure=public` 的 entry 才能进入当前可用 skill 清单；`planned` 只允许出现在 roadmap / 未来入口说明中。

#### 7.2 AGENTS

AGENTS 中与 skill 同步相关的规则应改成：

1. 所有 active skills 的变化必须同步更新对应文档与测试。
2. archive 文档不参与触发语义与 active contract。
3. scene entry 应通过 registry 收口，而不是继续在多个地方手写路径。
4. `trail-hsr-advanced` 属于 active internal skill，而不是 public inventory。
5. 任何 active skill 都不得直接或间接调用 archive skill。

#### 7.3 新测试文件

为避免继续把 skill 拓扑逻辑堆进 `tests/test_output_rendering.py`，本轮新增：

1. `tests/test_skill_registry.py`
   - 校验 `scene-entries.yaml`
   - 校验 public / internal / planned / active 语义
2. `tests/test_skill_structure.py`
   - 校验 skill 目录结构、frontmatter、references、eval 文件布局
3. `tests/test_skill_routing_contracts.py`
   - 校验总入口、scene entry、advanced 的 competition / escalation contract

#### 7.4 `tests/test_output_rendering.py`

当前与 skill 相关的 active guidance 断言需要收口：

1. 从 active 集合中移除旧 `trail-cw*`。
2. 新增 skill 结构 / registry 测试，而不是继续扩大整句文案断言范围。
3. 保留真正属于协议稳定面的 README / AGENTS 断言。

#### 7.5 活参考文档收口

当前仓库里仍有多份活参考 spec / plan 文档把旧 `trail-cw*` 视为 active 拓扑。实现时必须二选一：

1. 在这些文档顶部补“已被本 redesign supersede”的说明。
2. 或将它们一起迁入 archive 语义，不再作为当前 active 参考。

这里的“活参考文档”范围固定为：

1. `docs/superpowers/specs/` 下所有非 archive 文档
2. `docs/superpowers/plans/` 下所有非 archive 文档

如果这些文件中仍出现旧 `trail-cw*` 的 active-owner 路径或表述，则该文件必须带 supersede 说明；否则应迁入 archive 语义。

supersede 说明最小模板固定为：

```text
本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。
```

### 8. 迁移顺序

1. 先新增 `skills/registry/scene-entries.yaml`、`skills/registry/routing-competition.json` 与新的 skill 结构测试骨架。
2. 重写 README / AGENTS / 新旧测试，使仓库先摆脱对旧 `skills/trail-cw*` active 路径的硬依赖。
3. 重写 `trail-hsr`：
   - 去掉“基本原子动作 skill”定位。
   - 改成“总入口 + scene entry 索引 + 何时升级到 advanced”。
4. 重写 `trail-hsr-advanced`：
   - 改成纯内部恢复 skill。
   - 正文短化，细节进入 `references/`。
5. 在同一个原子变更里，将旧 `trail-cw*`（包括 `trail-cw-battle-advanced`）迁至 `docs/superpowers/archive/skills/...`，并补 archive `README.md`。
6. 收口仍引用旧 `trail-cw*` 拓扑的活参考文档。
7. 后续重做 `trail-cw-entry` 时，再将其从 `planned` 切换为 `active`。

这组迁移不接受长时间中间态；必须以单次可 review 的原子变更落地，避免出现“旧 skill 已删，但 README / tests / registry 还没切换”的不稳定窗口。

## 风险与待决事项

### 风险

1. 若 archive 仍残留在 `skills/` 可见路径下，可能继续被当作 active skill 使用。
2. 若 `trail-hsr` 的 description 仍然写成基础原子动作导向，会继续把总入口触发到过窄语义上。
3. 若 `trail-hsr-advanced` 仍按用户入口去设计，会破坏“对外入口 / 内部恢复”分层。
4. 若没有 competition / should-not-trigger / escalation 对照测试，新的 public/internal 边界仍可能只停留在文案层。

### 待决事项

1. 未来 `trail-cw-entry` 是否还会继续向下拆分子 skill，不在本 spec 决定。
2. `scene-entries.yaml` 是否需要进一步扩展字段（例如 owner、dependencies、version），本轮先不加入。

## 验证方式

完成实现后，至少验证：

1. active `skills/` 目录只包含当前有效 skill，旧 `trail-cw*` 与 `trail-cw-battle-advanced` 已完全移出 active namespace。
2. archive 根目录存在，且带来源清单 `README.md`。
3. `trail-hsr` 与 `trail-hsr-advanced` 的 frontmatter、references、registry、eval 布局均能通过结构测试。
4. `tests/test_skill_registry.py`、`tests/test_skill_structure.py`、`tests/test_skill_routing_contracts.py` 与 `tests/test_output_rendering.py` 相关子集均通过。
5. README / AGENTS / 活参考文档已切换到新 skill 体系，不再保留旧 `trail-cw*` 的 active owner 表述。
6. 用户触发 eval、competition eval 与 internal escalation 测试都能覆盖新角色划分。
7. 至少完成一轮“旧 skill 集合 vs 新 skill 集合”的人工 review 对照，确认触发与升级边界已经收口。

建议执行清单：

1. `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_skill_registry.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py -q`
2. `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_output_rendering.py -k "skill or readme or agents" -q`
3. `rg -nP "trail-cw(?!-entry)|trail-cw-battle-advanced|trail-cw-guide|trail-cw-events|trail-cw-replenish|trail-cw-shop|trail-cw-slots" README.md AGENTS.md skills tests docs/superpowers/specs docs/superpowers/plans --glob '!docs/superpowers/archive/**'`
   结果若非空，剩余命中必须都属于 supersede 说明或待迁移清单，不能再是 active owner 表述。
