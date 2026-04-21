> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。

# trail start 简化启动入口设计

## 背景

当前 `trail` 的原子启动链路对 Agent 来说偏长：

1. `trail daemon status/start/restart`
2. `trail window launch` 或 `trail window attach`
3. `trail session create`

这条链路虽然清晰，但对普通用户和首次接入项目 skill 的 Agent 都过于繁琐：

- Agent 需要自己决定先查 daemon、还是先查窗口、还是直接拉起游戏。
- 成功进入自动化前，还需要额外理解 `window attach`、`window launch`、`session create` 的职责差异。
- 一旦启动失败，Agent 很容易在 daemon/window/session 三层原子命令之间来回试错。

用户这次的目标不是改 `cw`、`guide` 这类场景命令，而是把**原子命令层**重新分成“简单命令”和“进阶命令”两层，并增加一个简单入口 `trail start`，让 Agent 在大多数情况下先学会：

- 启动环境：`trail start`
- 读屏：`trail ocr read`
- 动作：`trail input ...`

更细的 daemon/window/session/image/state 命令继续保留，但默认不先暴露给 Agent。

## 目标

1. 新增一个简单原子命令：`trail start`
2. `trail start` 在一次调用内完成环境收口：
   - daemon ready
   - 游戏 ready
   - 窗口 ready
   - session ready
3. `trail start` 成功时直接返回可用的 `session=<id>`，供后续场景命令继续使用。
4. 对同一工作区、同一窗口，`trail start` 优先复用已有 session，而不是默认创建多个并行 session。
5. 把项目文档与 skills 分成两层：
   - 基础 skill 只教 `trail start`、`trail ocr read`、`trail input ...`
   - 进阶 skill 再教 daemon/window/session 等深度原子命令

## 非目标

这次不做：

- 修改 `cw`、`guide`、`ocr` 等场景命令的业务语义
- 把场景命令纳入 simple/advanced 的分层讨论
- 删除或隐藏现有 daemon/window/session 原子命令
- 发明新的 daemon/window/session 底层能力，只在现有能力上增加编排入口
- 自动做“自动深度诊断模式”或复杂恢复策略

## 命令分层结论

### 简单原子命令

- `trail start`
- `trail ocr read`
- `trail input ...`

理由：

- 这是 Agent 最常用的 3 类基础能力：启动环境、观察环境、操作环境。
- 如果把 `input` 也放到进阶层，简单层会退化成“能看不能动”，不符合用户预期。
- `trail ocr read` 已经自带 screenshot 返回能力，所以仍适合留在简单层。

### 进阶原子命令

- `trail daemon ...`
- `trail window attach`
- `trail window launch`
- `trail session create`
- `trail screen shot`
- `trail image ...`
- `trail state dump`

理由：

- 这些命令本质上是拆解步骤、调试步骤、恢复步骤或强控制步骤。
- 它们不是要删除，而是默认不先教给 Agent。
- 当 `trail start` 失败，或简单层不能满足需求时，再由 advanced skill 暴露这些命令给 Agent 逐步拆解问题。

## 方案比较

### 方案 A：新增 `trail start`，并做 simple/advanced 文档分层

优点：

- 直接缩短 Agent 的常用启动链路
- 不破坏现有 daemon/window/session 命令
- 与“先简单、后进阶”的 skill 设计目标一致
- 对普通用户也更友好

缺点：

- 需要定义 session 复用策略
- 需要把多个现有错误路径收口进一个编排命令里

### 方案 B：不新增命令，只做文档分层

优点：

- 改动最小
- 不碰 CLI 行为

缺点：

- Agent 仍然需要自己串 `daemon -> window -> session`
- 只是“换了说明方式”，没有真正缩短操作链路

### 方案 C：新增 `trail start`，但总是新建 session

优点：

- 实现更简单

缺点：

- 与当前用户明确的产品直觉冲突：同一窗口不应随意制造多个 session
- 会让 session 语义更混乱

## 结论

采用 **方案 A**。

## `trail start` 的用户语义

`trail start` 是一个**幂等的简单启动入口**，目标不是“总是新建所有东西”，而是把环境收口到可用状态。

成功时，它对外至少保证两件事：

1. 游戏窗口可操作
2. 返回一个可继续使用的 `session=<id>`

冻结的内部顺序：

1. 检查 daemon 是否已安装；未安装则自动 install
2. 检查 daemon 是否可用；不可用则自动 start / restart 到 ready
3. 检查游戏窗口是否已存在；存在则直接 attach
4. 若窗口不存在，则按现有 `window launch` 规则启动游戏
5. attach 成功后，优先复用当前工作区里与该窗口匹配的可用 session
6. 只有在没有可复用 session 时，才创建新 session

其中 `launch -> attach` 的等待边界也冻结：

- 如果 `window launch` 成功返回 `started=1`，`trail start` 必须进入有限轮询，等待窗口达到 attach 条件
- 如果 `window launch` 成功返回 `started=1`，`trail start` 必须进入有限轮询，等待窗口达到 attach 条件
- 如果 `window launch` 成功返回 `started=0 already_running=1`，也沿用同一套 attach 轮询窗口，而不是立即报错
- 第一版等待上限固定为 `30s`
- 轮询间隔固定为 `1s`
- 超过上限仍无法 attach 时，按现有 `window attach` 失败语义收口，而不是继续无限等待

这里的 `ready` 语义也冻结：

- `daemon ready`：以当前 `daemon.status` 可见的 `state=ready` 为成功标准；`trail start` 不把 `starting` 视为已可用状态
- `window ready`：已拿到可用的 `window_binding`，后续场景命令无需再单独 `window attach`
- `session ready`：返回的 session 必须可继续用于后续 mutation；tainted session 不算 ready

成功结果需要固定包含：

- `session=<id>`
- `reused=0|1`
- `title=...`
- `hwnd=...`

其中：

- `reused=1` 表示复用了已有 session
- `reused=0` 表示本次新建 session

并且这条新命令的 canonical command 与首行事实也在这里冻结：

- 内部 canonical command：`start.run`
- 默认成功首行固定为：
  - `ok start.run session=<id> reused=<0|1> title=... hwnd=...`
- `title` 与 `hwnd` 都视为 must-keep 事实，不使用“或等价字段”的开放表述

## session 复用语义

当前实现里，从技术上讲，同一窗口可以重复 `session.create` 多次；但从产品语义上，这不应该是默认行为。

这次冻结为：

- 同一工作区、同一窗口、且 session 仍可用时，`trail start` 优先复用已有 session
- 只有在 session 缺失、坏掉，或窗口绑定不匹配时，才新建 session
- `trail start` 不负责“跨窗口迁移旧 session”这类复杂恢复，只做保守复用

这里的“可用”也具体冻结为：

- tainted session 不可复用
- 仅当 session 的 `window_binding` 与本次 attach 得到的 binding 完整匹配时，才允许复用
- 如果标题相同但 `hwnd` 已变化，视为不同窗口，不复用旧 session

若同一工作区中存在多个非 tainted 且 `window_binding` 完整匹配的 session，则 tie-breaker 也冻结为：

- 优先复用 `updated_at` 最新的 session
- 如果缺少可比的更新时间，则回退为 `created_at` 最新

这里的关键目标是避免简单入口制造多个平行的“这局上下文快照”。

## 参数边界

`trail start` 保留少量必要覆写参数：

- `--window-title`
- `--game-path`
- `--channel`

理由：

- 这些参数直接影响“如何找到游戏并建立会话”，属于启动入口的合理控制面
- 其他 daemon/window/session 的细碎参数仍留在进阶命令里，不放进简单入口

冻结的参数语义：

- `--window-title`：覆盖默认窗口标题
- `--game-path`：传给现有 `window launch` 逻辑，继续保持显式路径优先级最高
- `--channel`：传给现有 `window launch` 逻辑

## 错误语义

`trail start` 不重新发明一套模糊错误体系，而是尽量复用已有稳定错误语义：

- daemon 安装/启动问题：继续沿用 daemon 自己的错误码
- 游戏路径问题：继续沿用 `window launch` 那套错误码
- attach 问题：继续沿用 `window attach` 那套错误码
- session 创建问题：继续沿用 `session create` 的失败语义

设计重点是：

- `trail start` 是编排入口
- 不是一个重新包装所有错误含义的新协议层

但这条命令的 failure contract 仍要冻结到项目现有协议层：

- `start.run` 相关的 daemon-side orchestration 失败，必须继续遵守现有：
  - `fail start.run code=...`
  - 只要带 `request_id`，就必须输出 `request id=<id>`
  - 只有未知结果或显式可恢复时，才输出 `recover action=daemon.request_status request=<id>`
- `trail start` 在 daemon 未安装、install 失败这类 local pre-daemon 阶段失败时，对外也必须继续渲染为 `fail start.run code=...`；只是这类失败不强求 `request id` / `recover`
- 一旦进入 daemon-side `start.run` 编排，后续 attach / launch / session 复用 / create 的失败，都必须收口到单个 `start.run` 请求结果，而不是把多个底层 `request_id` 暴露给用户

但 `trail start` 仍然需要补充一点成功事实：

- 是否复用了 session（`reused=0|1`）

## 实现边界

### `trail.commands.start`

- 新增真正的 CLI 入口
- 负责调用编排逻辑
- 不直接重写 daemon/window/session 的底层细节

这条边界进一步冻结为：

- CLI 侧只负责 local pre-daemon 的 install / start 收口，以及发起单个 daemon-side `start.run` 请求
- 不允许 CLI 直接读 `.trail/sessions` 或自行拼装 session 复用逻辑

### 编排层

- 推荐新增一个清晰的编排单元，用来组织：
  - daemon ready
  - attach / launch
  - session 复用 / 创建
- 该层负责把多步操作压缩成一个简单命令，但不篡改底层命令的核心语义

这条编排层应放在 daemon 侧服务/command 层，而不是散落在 CLI 多步调用里。这样才能：

- 对外提供单一 `start.run` request / request id / recover 语义
- 让 session 复用继续以 daemon/session service 为事实源
- 避免 CLI 与 daemon 双边同时写一半编排逻辑

### 现有原子命令

- `trail daemon ...`
- `trail window attach`
- `trail window launch`
- `trail session create`

这些都继续保留，作为 advanced skill 的内容。

### 场景命令

- `cw`
- `guide`
- `ocr`

这次不改它们的业务语义，也不纳入 simple/advanced 的命令分层讨论。

## 文档与 skill 分层

### 基础 skill

第一次教给 Agent 的命令集合收口为：

- `trail start`
- `trail ocr read`
- `trail input ...`

并明确写出升级路径：

- 先试 `trail start`
- 如果失败或结果不满足需求，再加载 advanced skill

### advanced skill

需要包含：

- `trail daemon ...`
- `trail window attach`
- `trail window launch`
- `trail session create`
- `trail screen shot`
- `trail image ...`
- `trail state dump`

### README / help

- README 需要同时介绍 simple/advanced 两层入口
- `trail start --help` 需要清楚解释它会自动做哪些事，以及保留哪些覆写参数
- 现有进阶命令的 help 不需要降级，但文档层不应继续把它们当成默认起手式

这里的文档同步范围也进一步冻结：

- 基础 skill：继续使用 `skills/trail-hsr/SKILL.md`，并改成 simple-first
- advanced skill：新增单独的 `skills/trail-hsr-advanced/SKILL.md`
- `skills/trail-cw/SKILL.md` 不参与 simple/advanced 分层本身，但其前置说明需要改成“默认先经 `trail-hsr` 的 `trail start` 建好 session，再进入 `cw`”
- README 需要重写当前默认起手式，不能继续把 `daemon -> window -> session` 当成普通用户/Agent 的默认第一入口

## 验证方案

### 1. 启动链路回归

- daemon 已 ready 时，`trail start` 不重复创建无意义步骤
- daemon 未 ready时，`trail start` 能自动收口到 ready
- 窗口已存在时，优先 attach
- 窗口不存在时，走现有 `window launch`

### 2. session 语义回归

- 有可复用 session 时返回 `reused=1`
- 无可复用 session 时返回 `reused=0`
- 同一窗口不会因 `trail start` 默认制造多个 session

### 3. 参数回归

- `--window-title`
- `--game-path`
- `--channel`

都能继续影响启动行为，且优先级明确。

### 4. 文档分层回归

- 基础 skill 只暴露简单层命令
- advanced skill 才暴露 daemon/window/session/image/state 等进阶命令
- README / help 与 skill 分层保持一致
- `skills/trail-hsr/SKILL.md` 必须以 `trail start` 作为默认起手式
- `skills/trail-hsr-advanced/SKILL.md` 必须承接 daemon/window/session/screen/image/state 原子命令

## 风险与对策

### 风险 1：`trail start` 变成“黑盒命令”

对策：

- 成功输出明确给出 `session=<id>` 与 `reused=0|1`
- 失败时尽量保留现有稳定错误码，不吞细节
- 需要深度排障时，始终能切回 advanced skill

### 风险 2：session 复用规则不清，制造隐性状态

对策：

- 冻结“同工作区、同窗口、可用 session 优先复用”
- 输出里显式告诉 Agent 是复用还是新建

### 风险 3：simple/advanced 只写文档，不形成真实产品分层

对策：

- 必须新增真实 `trail start` 命令
- 基础 skill 的默认入口必须改成 `trail start`
- advanced skill 再承接细粒度原子命令

## 最终建议

当前最合理的下一步是：

1. 新增 `trail start`
2. 让它收口 daemon install、daemon ready、游戏、窗口、session 5 步
3. 成功直接返回 `session=<id>` 与 `reused=0|1`
4. 基础 skill 只先暴露：
   - `trail start`
   - `trail ocr read`
   - `trail input ...`
5. 其余 daemon/window/session/image/state 原子命令整体下沉到 advanced skill

这样既能显著缩短 Agent 的常用启动链路，也不会牺牲深度调试能力。
