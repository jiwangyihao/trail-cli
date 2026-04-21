> 本文件已被 `docs/superpowers/specs/2026-04-21-trail-skill-system-redesign-design.md` 取代；仅供历史参考，不代表当前 active skill 拓扑。

# 游戏启动路径自动解析设计

## 背景

当前 `trail window launch` 必须显式传入 `--game-path`。这对普通用户和 Agent 都不够友好：

- 游戏已经安装在默认位置时，用户不应该还要手填路径。
- Agent 在无法附着窗口时，也不应该默认去全盘乱搜路径。
- 一旦用户或系统已经成功启动过一次，客户端应该能记住这条成功路径，而不是每次重新询问。

这轮设计要解决的不是“游戏内自动化”，而是**启动前的路径解析体验**。

同时，用户额外给出了两个重要产品约束：

1. 这是面向普通用户的客户端软件，不应按“项目/仓库”维度管理路径。
2. Agent 不应默认随机搜索路径；当默认路径和历史路径都失败时，应直接问用户。

## 参考结论

我补看了当前 `trail` 的实现与公开可见的 SRA 主线代码：

- `trail` 目前的 `trail window launch` 只是简单透传 `--game-path` 给 runtime，没有单独的路径解析层。
- SRA 的公开主线更偏向“统一配置持久化 + 窗口标题接管”，而不是默认做一套重的全盘搜索逻辑。

因此，这轮最合理的参考方向不是复制一个“自动搜盘器”，而是借鉴它的**用户状态集中持久化**思路：把成功启动路径当成客户端自己的长期状态来管理。

## 目标

1. `trail window launch` 在未显式提供 `--game-path` 时，能自动按固定顺序解析游戏路径。
2. 启动成功后，客户端会持久化最近一次成功路径，供下一次优先复用。
3. Agent 在找不到路径时，能拿到明确、可询问用户的失败信号，而不是自己乱搜。
4. 更新根 `AGENTS.md` 与相关 `skills/*/SKILL.md`，把这套路径解析规则同步进项目文档约束。

## 非目标

这轮不做：

- 默认自动扫描常见目录、注册表或全盘搜索
- 修改 `window attach` 的现有语义
- 修改 daemon 生命周期、session 管理或游戏内自动化流程
- 引入多游戏、多可执行文件的复杂安装库管理

## 方案比较

### 方案 A：继续强制 `--game-path`

优点：

- 实现最简单
- 行为最显式

缺点：

- 不符合普通用户体验
- 对默认安装路径场景完全没有帮助
- Agent 在无法 attach 时仍然需要反复询问路径

### 方案 B：历史成功路径 -> 默认路径 -> 直接问用户

优点：

- 行为稳定、可解释
- 不需要默认乱搜路径
- 普通用户首次成功后，后续基本无感
- Agent 的失败收口也更清晰

缺点：

- 需要新增一份用户级持久化状态
- 需要明确显式路径与自动路径的优先级和失败语义

### 方案 C：历史路径 -> 默认路径 -> 自动搜索 -> 再问用户

优点：

- 首次使用时理论上更“聪明”

缺点：

- 与“不要让 Agent 随便搜索”的产品约束冲突
- 更慢、更不透明
- 更容易误判，用户也更难理解到底用了哪条路径

## 结论

采用 **方案 B**。

固定顺序为：

1. 历史成功路径
2. 默认路径 `C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe`
3. 若仍失败，则明确返回“未找到游戏路径，请提供路径”类失败，让 Agent/CLI 直接问用户

补充冻结：

- 历史成功路径按 `channel` 分桶保存，而不是单个全局字符串
- 当前唯一冻结的默认路径只适用于 `official` 渠道
- `bilibili` / `global` 在未显式传 `--game-path` 且无历史成功路径时，不做额外默认搜索，直接进入“请用户提供路径”的失败分支

## 用户可见行为

### 1. 显式路径优先级

- `--game-path` 继续保留
- 只要用户或 Agent 显式传了 `--game-path`，就只尝试这条路径
- 显式路径不存在或启动失败时，直接报错
- 显式路径失败时，不回退到历史路径或默认路径

这条规则保证了“显式输入是硬约束”，避免系统偷偷换路径。

### 2. 自动路径解析顺序

当没有显式 `--game-path` 时，固定按以下顺序尝试：

1. 历史成功路径
2. 默认路径

如果两者都不存在或都启动失败，则：

- 返回清晰失败
- 文案应明确表达“未找到游戏路径”或“请提供路径”
- Agent 看到后应直接问用户，而不是继续自发搜索
- 失败必须带稳定错误码 `GAME_PATH_REQUIRED`

### 3. 历史成功路径更新规则

只有下面两种情况会写入历史成功路径：

- 用户显式提供的路径，并且本次启动成功
- 自动命中的路径，并且本次启动成功

以下情况都不应更新历史：

- `window attach` 成功
- 路径不存在
- 启动失败
- `window launch` 返回 `already_running=1`

## 持久化位置

这份历史成功路径属于**客户端自己的用户级状态**，不属于项目工作区。

建议放在：

- `~/.trail-daemon/` 下
- 与 daemon manifest 并列的独立小文件中

这轮直接冻结具体文件：

- `~/.trail-daemon/game-paths.json`

最小 JSON 结构也冻结为按渠道分桶：

```json
{
  "official": {
    "last_success_game_path": "C:\\Program Files\\miHoYo Launcher\\games\\Star Rail Game\\StarRail.exe"
  }
}
```

读取退化语义也在这里冻结：

- `game-paths.json` 不存在：等价于“无历史路径”，继续走默认路径 / `GAME_PATH_REQUIRED`
- `game-paths.json` 损坏、无法解析、或缺少当前 `channel` bucket：也等价于“无历史路径”，不得因此阻断启动流程

不建议塞进当前 manifest，原因是：

- manifest 更偏 daemon install/runtime 元数据
- 启动成功路径更像用户偏好与客户端状态
- 单独文件更容易扩展、迁移和排障

## 职责边界

### `trail.commands.window`

- 负责 CLI 选项解析
- 负责显式 `--game-path` 的用户入口
- 不承接路径解析策略

### `trail.runtime.window`

- 负责“历史路径 -> 默认路径 -> 失败”的解析顺序
- 负责验证路径存在、执行启动、成功后回写历史路径
- 负责把显式路径失败与自动路径失败区分开

这轮额外冻结一个共享 helper 边界：

- `resolve_daemon_home()` 仍然是唯一的 daemon home 来源
- 实现时应新增一个共享 helper 来解析 `game-paths.json` 的路径，避免 `trail.runtime.window` 复制 daemon 目录规则，或反向直接依赖 bootstrap 的实现细节

### daemon / runtime service

- 只负责透传调用与返回结果
- 不承接策略决策

## 默认路径

这轮冻结的默认路径为：

`C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe`

它只在“未显式提供路径且历史成功路径不可用”时作为第二优先级尝试。

补充：

- 这条默认路径只对 `official` 渠道生效
- `bilibili` / `global` 若没有历史成功路径，默认直接进入 `GAME_PATH_REQUIRED` 分支

## 失败语义

这轮不改 `window attach` 的失败语义。

对 `window launch`：

- 显式 `--game-path` 错误：继续返回明确的路径不存在/启动失败类错误
- 自动路径解析失败：返回新的稳定错误码 `GAME_PATH_REQUIRED`

固定语义：

- `GAME_PATH_NOT_FOUND`：显式提供的路径不存在
- `GAME_PATH_REQUIRED`：没有显式 `--game-path`，且系统已按“历史成功路径 -> 默认路径”尝试完毕，仍然无法确定可启动路径

对 `GAME_PATH_REQUIRED`，还要固定：

- `why msg` 必须明确表达“请提供游戏路径”
- 该失败是终态用户输入问题，不应附带 `recover`
- Agent 看到 `code=GAME_PATH_REQUIRED` 时，应直接问用户提供路径，而不是继续自行搜索

设计重点不是把所有路径问题都映射成一个错误码，而是要让 Agent 和普通用户都能分辨：

- 这是显式路径本身错了
- 还是系统已经尝试过历史路径与默认路径，但仍需要人工提供路径

## 文档同步范围

这轮不仅要改行为，还要同步文档：

### 根 `AGENTS.md`

- 已先补上通用项目规则：所有影响普通用户或 Agent 使用方式的变更，都必须同步更新相关 `skills/*/SKILL.md`

### 相关 `skills/*/SKILL.md`

要同步写清：

- `skills/trail-hsr/SKILL.md`
- `skills/trail-cw/SKILL.md`
- `trail window launch` 现在支持未显式传路径时自动解析
- 自动解析顺序是“历史成功路径 -> 默认路径 -> 直接问用户”
- Agent 不应默认乱搜路径
- 什么时候仍然应该显式传 `--game-path`

### README / CLI help

也必须同步写清：

- `window launch` 在未显式传 `--game-path` 时会先尝试历史成功路径，再尝试默认路径
- 当前冻结的默认路径仅覆盖 `official` 渠道
- `bilibili` / `global` 若没有历史成功路径，通常仍需要显式提供 `--game-path`
- 显式 `--game-path` 的优先级最高，失败时不会回退

## 验证方案

### 1. 路径解析顺序

- 没有显式 `--game-path` 时，先尝试历史成功路径
- 历史路径不可用时，再尝试默认路径
- 两者都失败时，返回明确失败，不再继续搜索

### 2. 显式路径硬约束

- 显式给了错误路径时，不允许偷偷回退到历史路径或默认路径

### 3. 成功回写

- 只有真正启动成功才更新历史路径
- attach 成功、路径不存在、启动失败都不应改历史
- `already_running=1` 不算“成功启动”，不更新历史路径

补充冻结：

- 若游戏已成功拉起，但回写 `game-paths.json` 失败，对外仍返回启动成功
- 这类情况通过 warning 收口，不回滚启动结果
- warning code 固定为 `GAME_PATH_PERSIST_FAILED`

### 4. CLI / 协议回归

- 不传 `--game-path` 时也能正常 `window launch`
- 传了显式 `--game-path` 时，优先级仍然最高
- `code=GAME_PATH_REQUIRED` 必须稳定出现，作为 Agent 直接问用户的信号
- `GAME_PATH_REQUIRED` 不得带 `recover`
- 启动成功但历史路径回写失败时，仍返回 success，并带 `warn code=GAME_PATH_PERSIST_FAILED`

### 5. 文档回归

- `AGENTS.md` 与相关 `skills/*/SKILL.md` 都同步更新
- README / help 文案与真实行为一致

## 风险与对策

### 风险 1：历史路径失效

对策：

- 自动回退默认路径
- 两者都失败再直接问用户

### 风险 2：用户显式路径被偷偷覆盖

对策：

- 显式路径始终是硬约束
- 一旦显式路径失败，直接失败，不做自动 fallback

### 风险 3：Agent 误把失败当成可自动搜索

对策：

- 在 SKILL 与 AGENTS 里明确写死：默认不要乱搜路径
- 自动解析只限历史路径和默认路径

## 最终建议

当前最合理的下一步，不是做更激进的自动搜索，而是先把以下行为落成稳定接口：

- `window launch` 默认支持自动解析
- 解析顺序固定为“历史成功路径 -> 默认路径 -> 直接问用户”
- 显式路径仍然保留且是硬约束
- 历史成功路径持久化在 `~/.trail-daemon` 下单独文件
- `AGENTS.md` 与相关 `skills/*/SKILL.md` 同步更新
