# Trail Daemon 设计

## 背景

`trail-cli` 当前是短命 CLI 架构：每条命令都会重新启动进程、初始化 runtime、重新加载 OCR 与图像匹配资源，并在命令结束后退出。真实实机验证已经暴露出四类问题：

1. 性能问题：`ocr read`、`image locate`、`cw` 阶段识别等命令每次都要重新加载模型或重建运行时，延迟偏高。
2. 权限问题：当《崩坏：星穹铁道》窗口以更高权限运行时，普通权限 CLI 无法可靠执行输入动作。
3. 状态问题：窗口句柄、DPI、截图策略、参考图索引、场景热状态无法跨命令复用，跨命令诊断链被切断。
4. 平台问题：扩展屏、混合 DPI、后台窗口、窗口级截图和屏幕级回退逻辑需要统一管理，分散在短命进程里很难稳定。

用户已经明确选择如下方向：

- 不是只做输入桥，而是做**单管理员全量 daemon**；
- CLI 保持普通权限，尽量保留现有命令面；
- daemon 自动连接、可自动启动，同时也提供显式生命周期管理；
- 目标不仅是解决输入权限，还要顺带解决 OCR/找图/窗口绑定/场景执行的重复加载和状态复用问题。

## 目标

1. 将窗口、截图、OCR、找图、输入、`cw` 场景执行、session 热状态全部收敛到常驻 daemon 中。
2. 保持 `trail ...` 现有命令面基本不变，使 skill 与 Agent 不需要整套重写。
3. 只有 daemon 运行在管理员权限；CLI 与 Agent 继续保持普通权限。
4. 支持“自动连接 + 自动启动 + 显式管理”：
   - 普通命令优先连接现有 daemon；
   - daemon 不存在时，按定义好的 bootstrap 机制启动；
   - 同时提供 `trail daemon install|start|stop|status|logs`。
5. 保持结构化 envelope 契约，包括 transport/bootstrap 失败路径。
6. 在扩展屏、混合 DPI、后台截图、已提权窗口输入等现实条件下保持稳定行为。

## 非目标

1. 第一版不支持远程调用，不暴露跨机器控制能力。
2. 第一版不拆分为“普通 daemon + 管理员输入 helper”双进程模型。
3. 第一版不改变 `guide` 的业务语义与 `cw` 的场景语义，只改变执行宿主与通信方式。
4. 第一版不追求全新命令体系，优先保证兼容现有 CLI 心智模型。

## 总体架构

### 进程模型

- `trail`
  - 非管理员薄 CLI。
  - 负责参数解析、workspace 解析、daemon 发现、请求构造、响应渲染。
- `traild`
  - 单管理员常驻 daemon。
  - 持有窗口控制器、截图器、OCR 引擎、图像匹配器、参考图索引、输入执行器、`cw` 场景执行器、session 热状态。

### 权限边界

- 只有 `traild` 是管理员进程。
- `trail` 本体不常驻管理员权限，也不要求 Agent 整体提权。
- 唯一允许显式触发提权的 CLI 管理动作是 `trail daemon install`：它负责一次性安装 daemon bootstrap 能力。日常普通命令和 `trail daemon start` 不再自行弹 UAC。

## Bootstrap 模型

这是本设计的关键约束，必须在 spec 中固定，而不能留给 implementation plan 自由发挥。

### 一次性安装

第一版采用**显式安装 + 后续自动启动**模型：

1. 用户首次执行 `trail daemon install`。
2. 该命令显式请求管理员授权。
3. 安装动作完成后，在 Windows 上注册一个**每用户私有、以最高权限运行**的 daemon 启动器。第一版推荐实现为计划任务；只要后续实现仍满足相同外部契约，也允许用等价的本机 bootstrap 设施替代。
4. 安装完成后写入 daemon manifest，记录：
   - bootstrap 标识（例如任务名）
   - daemon 可执行入口
   - 协议版本
   - manifest 版本
   - token 文件位置
   - 日志目录
   - workspace 注册根目录策略

### manifest 契约

manifest 分为两段，且字段语义必须固定：

- `install`
  - bootstrap 类型
  - bootstrap 标识
  - daemon 可执行入口
  - manifest 版本
  - 协议版本
  - token 文件位置
  - 日志目录
- `runtime`
  - `instance_id`
  - `pid`
  - `state`
  - `endpoint`
  - `token_generation`
  - `updated_at`
  - `last_transition_at`
  - `last_start_error`

`runtime.state` 的稳定状态集合固定为：

- `installed`
- `starting`
- `ready`
- `degraded`
- `stopping`
- `stopped`
- `crashed`

`instance_id` 与 `token_generation` 的生命周期固定为：

1. 每次 daemon 成功启动都生成新的 `instance_id`。
2. 每次生成新 token 都必须递增 `token_generation`。
3. 第一版要求“一次启动实例只对应一代 token”；也就是说，正常实现中 `instance_id` 与 `token_generation` 是一一对应的。
4. 当 daemon 进入 `stopped` 或 `crashed`，旧 token 必须立即失效；下一实例必须生成新的 `instance_id + token_generation` 组合。
5. CLI 在自动重连时，只要发现任一字段漂移，都必须视为新实例重连，而不是复用旧连接上下文。

### 访问控制要求

bootstrap、manifest、token 与 IPC endpoint 都必须限定为“安装用户本人”可访问：

1. manifest 文件必须设置为当前安装用户可读写，其他普通用户默认不可写。
2. token 文件必须设置更严格的 ACL，至少不允许同机其他普通用户读取。
3. IPC endpoint 必须绑定到安装用户上下文，不允许只靠“本机 + token”放宽到任意本机用户。
4. `trail daemon install` 必须负责写入这些 ACL，而不是留给后续实现自由选择。

### 日常自动启动

普通命令的启动链固定如下：

1. CLI 先读取本机 manifest。
2. 若 manifest 不存在，返回结构化错误 `DAEMON_BOOTSTRAP_REQUIRED`，明确提示先运行 `trail daemon install`。
3. 若 manifest 存在，CLI 先尝试连接现有 daemon。
4. 若连接失败，CLI 调用 bootstrap 启动器拉起 `traild`。
5. CLI 轮询 daemon manifest runtime 段，直到看到：
   - daemon 已注册 endpoint
   - token 已生成
   - 状态为 `ready` 或 `degraded`
6. 然后 CLI 再发业务请求。

CLI 必须同时校验：

- `runtime.instance_id` 是否与当前连接对象一致；
- `runtime.updated_at` 是否新于启动前快照；
- `protocol_version` 是否兼容；
- token 文件是否存在且 `token_generation` 未漂移。

### 为什么这样定义

这样做的目的，是避免“普通 CLI 静默自提权”这个不可控路径，同时又满足用户要求的“自动+显式”：

- 显式：`trail daemon install|start|stop|status|logs`
- 自动：任何普通命令在 daemon 缺失时都能自动尝试启动**已安装好的** bootstrap 目标

## Workspace 与路径归属

daemon 化后，`cwd` 不能再作为隐式真相源。第一版必须显式协议化 workspace。

### Workspace 规则

1. CLI 每次请求必须传 `workspace_root` 绝对路径。
2. daemon 内所有 `.trail` 路径都基于该 `workspace_root` 解析。
3. 不允许 daemon 用自身 `cwd` 推断 workspace。
4. daemon 内的 session、截图、artifact、引用图索引都按 `workspace_root` 隔离。
5. session、artifact、state 内所有持久化路径字段默认都存相对 `workspace_root` 的相对路径；只有超出 workspace 的路径才允许保留绝对路径。

### 路径返回规则

为了兼容现有 skill 和命令输出：

1. daemon 内部可以使用绝对路径。
2. CLI 对外打印 envelope 时：
   - `screenshot` 返回相对 `workspace_root` 的相对路径，例如 `.trail/shots/last-action.png`
   - `references[].path` 同样返回相对 `workspace_root` 的相对路径
3. 如果路径不属于当前 workspace，CLI 必须回退为绝对路径，而不能静默输出错误相对路径。

### 产物命名规则

为了避免并行请求覆盖同一张截图：

1. daemon 生成的截图、调试产物必须按 `request_id` 或等价唯一键命名，不允许把单个可覆写文件作为唯一真相源。
2. `references` 必须绑定到与当前 envelope 同一份 `screenshot`。
3. `last-action.png` 最多只能作为便捷别名，不能作为并发协议的唯一截图路径。

## 组件划分

### 1. CLI 薄壳层

职责：

- 保持现有命令入口和大体参数形态；
- 将命令映射为 RPC 请求；
- 处理 daemon 发现、自动启动、版本检查、认证；
- 对 transport/bootstrap 失败合成结构化 envelope。

CLI 不负责：

- 直接初始化 OCR、找图、窗口 runtime；
- 直接执行任何窗口输入；
- 直接维护 session 热状态。

### 2. Daemon RPC 层

职责：

- 监听本机专用 IPC；
- 校验认证令牌；
- 暴露稳定方法集；
- 将请求路由到内部服务层；
- 返回统一响应对象。

协议要求：

- 仅允许本机访问；
- 请求必须包含：
  - `request_id`
  - `protocol_version`
  - `workspace_root`
  - `session_id`（如适用）
  - `verbose`（如适用）
  - `method`
  - `payload`
- 响应必须包含：
  - `request_id`
  - `ok`
  - `data`
  - `screenshot`
  - `timing`
  - `warnings`
  - `references`
  - `debug`
  - `error`

### 3. Runtime 服务层

- `WindowService`
  - 窗口附着、句柄缓存、DPI 读取、窗口启动、窗口级截图策略。
- `CaptureService`
  - 截图主逻辑，负责窗口级抓图、`PrintWindow` 回退、bbox 回退。
- `OcrService`
  - OCR 模型常驻与复用。
- `ImageService`
  - 图像匹配器与参考图索引常驻。
- `InputService`
  - click/drag/key 等输入动作；只允许暴露受控动作，不暴露任意脚本执行。
- `SceneService`
  - `cw` 阶段识别、槽位读取、商店、补给、事件、攻略应用等场景能力。
- `SessionService`
  - session 热状态管理、磁盘落盘、恢复、并发隔离。

## 命令兼容边界

### 原则

CLI 命令面是“尽量兼容”，但不能只写成口号。第一版按以下表冻结：

#### 继续由 CLI 本地处理

- `--help`
- 纯文案输出
- 纯参数校验
- `trail daemon install|start|stop|status|logs` 的本地调度部分

#### 通过 RPC 调 daemon 执行

- `window attach|launch`
- `screen shot`
- `ocr read`
- `image locate|wait`
- `input click|drag|key`
- `guide fetch|config|list`
- `state dump`
- `session create`
- 所有 `cw ...`

#### 保持的 envelope 契约

无论是业务失败，还是 transport/bootstrap 失败，CLI 对外都必须打印 JSON envelope。也就是说：

- daemon 执行成功/失败：daemon 返回 envelope，CLI 只做路径归一化。
- daemon 未安装、拉起失败、认证失败、版本不匹配、连接超时：CLI 本地生成 envelope。

新增稳定错误码：

- `DAEMON_BOOTSTRAP_REQUIRED`
- `DAEMON_START_FAILED`
- `DAEMON_UNAVAILABLE`
- `DAEMON_AUTH_FAILED`
- `DAEMON_VERSION_MISMATCH`

这些错误也必须落在同样的 envelope 结构中。

#### `request_id` 对外契约

`request_id` 属于 transport/审计元数据，不作为现有顶层 envelope 的新公开字段直接暴露。第一版规则固定为：

1. daemon 内部与 CLI transport 层必须始终持有 `request_id`。
2. 当命令正常成功或普通业务失败时，CLI 对外仍保持现有 envelope 顶层字段集合不变。
3. 当 transport/bootstrap 失败且结果需要后续排障时，CLI 必须把 `request_id` 放进 `debug` 或明确的管理查询面，而不是破坏现有顶层 JSON schema。
4. CLI 本地生成的 transport/bootstrap 失败 envelope 也必须生成 `request_id`，以便后续日志与状态查询关联。

## `--verbose` 与调试语义

- `--verbose` 变为 RPC 级调试开关。
- daemon 在 `verbose=true` 时可返回：
  - transport 诊断
  - 请求路由
  - 模型命中/初始化信息
  - 窗口与输入 trace
  - 场景中间步骤
- transport/bootstrap 层的 debug 也必须进入同一个 `debug` 字段，而不是单独打印到 stderr。

## Session、一致性与幂等性

这是第一版必须写清楚的核心语义。

### 真相源

- 运行中的单一真相源：daemon 内存中的 session 热状态。
- 可恢复副本：磁盘上的 `.trail/sessions/*.json`。

### 并发模型

1. 同一 `session_id` 的 mutating 请求必须串行执行。
2. 只读请求可以按策略并行，但不得穿透到会改变同一 session 真相源的执行路径。
3. `cw` 相关请求按 `session_id` 串行，不允许两个 CLI 同时推进同一局。
4. 所有会触碰真实窗口输入或窗口启动的 mutating 请求，还必须参与更高一级的 `window/input` 互斥，不得仅靠 `session_id` 串行。
5. 无 `session_id` 的 mutating 请求（例如纯输入命令）也必须进入同一套 `window/input` 锁模型。

### 请求状态机

所有 mutating RPC 都必须有 `request_id`，并记录到执行日志，状态至少包括：

- `accepted`
- `executing`
- `side_effect_applied`
- `state_persisted`
- `responded`

并定义以下机器可判定终态：

- `failed_before_side_effect`
- `applied_but_not_persisted`
- `persisted_but_response_unknown`
- `completed`

同一 `request_id` 在同一 `workspace_root + session_id + method` 范围内必须唯一。若 daemon 收到重复 `request_id`：

- 若原请求已进入终态，则直接返回该请求的最终记录，不得重复执行业务副作用；
- 若原请求仍在执行，则返回“请求进行中”状态，而不是启动第二次执行。

### 自动重试规则

- 只读 RPC 在 transport 失败时可以有限重试。
- mutating RPC 默认**不自动重试**。
- 如果 CLI 在 mutating RPC 上超时或断连，只能提示“执行结果未知”，并依赖 `request_id` 与审计日志/状态查询来判定，而不能盲目重放。

为此第一版需要显式提供 `request_id` 查询面，至少支持 daemon 内部和 CLI 管理命令查询某个请求的最终状态。

第一版将这一查询面固定为：

- `trail daemon request-status --request-id <id>`

其最小返回字段固定为：

- `request_id`
- `method`
- `workspace_root`
- `session_id`（如适用）
- `final_state`
- `last_visible_stage`
- `tainted`
- `started_at`
- `updated_at`

### 落盘顺序

mutating RPC 的提交顺序固定为：

1. 写执行日志 `accepted/executing`
2. 执行业务副作用
3. 更新内存 session 真相源
4. 持久化 session 磁盘副本
5. 记录 `state_persisted`
6. 生成响应 envelope

若在 2-5 任一步骤中断，daemon 必须在日志中留下 `request_id` 与最后可见阶段，供后续排障和人工判断是否已执行。

### 失败持久化矩阵

1. 默认失败不提交业务 session 状态变更。
2. 允许显式定义“安全失败可持久化”类别，例如某些仅更新 `stale/error` 标记的场景命令；这一类必须在后续 plan 中逐条列出。
3. `last_result`、`last_screenshot`、request journal 必须在所有失败路径都有明确更新策略，不能留给实现自由发挥。
4. daemon 重启后，如果某个 session 存在 `applied_but_not_persisted` 或 `persisted_but_response_unknown` 请求，必须把该 session 标为 `tainted`，在 reconcile 前拒绝继续执行 mutating 场景命令。

第一版在 spec 里先冻结默认失败持久化矩阵：

- `failed_before_side_effect`
  - 业务 session 状态：不更新
  - `last_result`：更新为失败 envelope
  - `last_screenshot`：若已有截图则更新，否则保持原值
  - request journal：终态记录
- `applied_but_not_persisted`
  - 业务 session 状态：不更新磁盘副本
  - `last_result`：更新为“结果未知”失败 envelope
  - `last_screenshot`：更新为当前请求截图（若有）
  - request journal：终态记录，并把 session 标为 `tainted`
- `persisted_but_response_unknown`
  - 业务 session 状态：以已持久化副本为准
  - `last_result`：更新为“结果未知”失败 envelope
  - `last_screenshot`：更新为当前请求截图（若有）
  - request journal：终态记录，并把 session 标为 `tainted`
- `completed`
  - 业务 session 状态：正常提交
  - `last_result`：更新为成功/业务失败 envelope
  - `last_screenshot`：按当前请求更新
  - request journal：终态记录

`tainted` 语义在第一版必须固定如下：

1. `tainted` 持久化在 session 磁盘副本与内存真相源中，不能只存在于日志里。
2. `state dump` 与 daemon 管理查询都必须能直接返回 `tainted` 状态。
3. 只有显式 reconcile 或管理清除动作才能解除 `tainted`，普通命令不得隐式清除。
4. 第一版固定两个外部入口：
   - `trail daemon request-status --request-id <id>` 用于按请求排障；
   - `trail daemon reconcile-session --session <id>` 用于显式 reconcile/清除 `tainted`。
5. `failed_before_side_effect` / `applied_but_not_persisted` / `persisted_but_response_unknown` / `completed` 四类终态下，必须分别定义：
   - 是否更新业务 session 状态
   - 是否更新 `last_result`
   - 是否更新 `last_screenshot`
   - request journal 的最终记录内容

## 截图与输入策略

### 截图

- Windows 下优先使用窗口级抓图，以确保游戏窗口内容与分辨率稳定；
- 目标输出分辨率固定为游戏窗口内部的 `1920x1080`；
- 当窗口级抓图失败时，再按策略回退到 `PrintWindow`，最后才考虑 bbox 回退；
- 扩展屏、混合 DPI、后台窗口都由 daemon 内部兼容。

### 输入

- 所有输入都在管理员 daemon 内执行；
- 输入前由 daemon 统一完成窗口准备、句柄校验和诊断；
- 所有输入请求都必须写审计日志：时间、workspace、session、窗口、动作、坐标/按键、`request_id`。

### `window launch` 语义

- 第一版接受 `window launch` 经由管理员 daemon 拉起游戏，因此其结果进程可以是管理员权限；这被视为 daemon 化后的正式语义，而不是与旧 CLI 本地启动语义严格等价。
- 如果未来要保留“以普通用户令牌启动游戏”的能力，需要作为后续单独功能显式设计，而不是在第一版实现里隐式追加。 

## 状态与运维

### daemon runtime 状态

`trail daemon status` 不能只返回静态字段，必须返回明确的 daemon 状态机：

- `installed`
- `starting`
- `ready`
- `degraded`
- `stopping`
- `stopped`
- `crashed`

并至少展示：

- 是否管理员
- bootstrap 类型与标识
- 监听 endpoint
- daemon PID
- 启动时间
- 协议版本
- token 文件状态
- 当前 workspace 数
- 当前 session 数
- 每个 session 当前绑定窗口摘要
- 已加载模型/索引摘要
- 最后一次启动错误（如果存在）

`status` 还必须能区分：

- daemon 尚未启动但 bootstrap 已安装；
- daemon 正在启动但尚未 ready；
- daemon 已停止且可再次启动；
- daemon 崩溃且 manifest 仍残留旧 runtime 记录。

### 生命周期命令

- `trail daemon install`
  - 一次性安装 bootstrap。
- `trail daemon start`
  - 触发已安装 daemon 启动；若未安装则返回 `DAEMON_BOOTSTRAP_REQUIRED`。
- `trail daemon stop`
  - 请求 daemon drain in-flight 请求后退出；不删除 session 文件。
- `trail daemon logs`
  - 只看 daemon 自身日志，不混入业务截图产物。

另需补一个面向实现与排障的请求查询入口，例如 `trail daemon request-status --request-id <id>`，用于返回 request journal 的机器可消费状态。

## 错误处理

- 未安装 bootstrap：`DAEMON_BOOTSTRAP_REQUIRED`
- bootstrap 启动失败：`DAEMON_START_FAILED`
- daemon 连接失败：`DAEMON_UNAVAILABLE`
- token 无效：`DAEMON_AUTH_FAILED`
- 协议不兼容：`DAEMON_VERSION_MISMATCH`
- 窗口句柄失效：`WINDOW_NOT_FOUND`
- 输入后端不可用：`INPUT_BACKEND_UNAVAILABLE`
- 模型加载失败：保持已有业务错误码风格，返回结构化错误

所有这些错误都必须进入统一 envelope，而不是退化为纯 stderr 文本。

## 测试分层

daemon 化后，测试策略必须同步升级，不能继续只依赖“进程内 fake runtime”。第一版测试分为五层：

### 1. 纯领域单元测试

继续保留现有场景与领域逻辑的单元测试，例如：

- `cw` 阶段识别
- 槽位读取与合并逻辑
- guide 业务解析

这些测试不依赖 RPC。

### 2. daemon 服务层测试

新增：

- `WindowService` / `CaptureService` / `OcrService` / `ImageService` / `InputService` / `SessionService` 的进程内测试
- 用 fake backend 验证缓存复用、workspace 解析、session 串行化、日志顺序

### 3. CLI-RPC 契约测试

新增：

- fake daemon + 真 CLI 的契约测试
- 验证普通命令经由 RPC 后仍输出兼容 envelope
- 验证 transport/bootstrap/auth/version 失败时，CLI 本地 envelope 也稳定
- 验证 `guide` 命令 daemon 化后仍保持当前命令面与输出契约

### 4. Bootstrap 与存储一致性测试

新增：

- manifest 发现
- token 轮换
- workspace 隔离
- session 落盘与恢复
- mutating RPC 的 request log 状态机
- 路径字段相对化与 `last_result/last_screenshot` 失败持久化矩阵

### 5. 实机 smoke

必须保留，且作为第一版验收的一部分：

- 已提权游戏窗口输入真实生效
- 扩展屏 + 混合 DPI 下截图稳定为 `1920x1080`
- 后台截图可用
- 采用显式预热；预热完成后连续执行至少 3 次 `ocr read`，以后 2 次的中位耗时低于第一次 warm 请求

## 迁移策略

### 第一阶段：daemon 骨架与 runtime RPC 化

- 建立 manifest、bootstrap、RPC 层、token 认证、daemon lifecycle 命令
- 迁移 `window`、`screen`、`ocr`、`image`、`input`
- 建立 CLI 对 transport/bootstrap 失败的 envelope 封装

### 第二阶段：session 热状态与一致性

- 引入 `SessionService`
- 定义 request log 状态机
- 完成 workspace 隔离与落盘恢复
- 将 `session create`、`state dump` 迁入 daemon
- 从这一阶段开始，所有 session 读写都必须经 daemon；不允许保留本地 CLI 对 `.trail/sessions` 的双写路径

这一阶段与第三阶段的 cutover 规则固定为：

- `cw` 不允许在“本地直写 session”与“daemon 管理 session”之间 mixed-mode 共存。
- 因此 phase 2 完成后，`cw` 要么已经通过 daemon 包装执行现有逻辑，要么 phase 2/3 必须合并为单次 cutover；implementation plan 不得再自由选择双真相源过渡。

### 第三阶段：`cw` 场景迁移

- 将 `cw` 场景执行器迁入 daemon
- 保持现有命令面与 skill 基本兼容
- 增加场景 trace 与诊断输出

### 第四阶段：文档与实机验收

- 更新 README 与 skill
- 执行性能 smoke、权限 smoke、多显示器/DPI smoke

## 风险与缓解

1. 单管理员全量 daemon 权限面偏大。
   - 缓解：本机 IPC、一次性 token、固定 RPC 方法集、审计日志。
2. bootstrap 安装与自动启动路径在 Windows 上天然复杂。
   - 缓解：把 `install` 作为显式前置步骤写入正式契约，避免模糊的“普通 CLI 静默提权”。
3. mutating RPC 不能安全自动重试。
   - 缓解：引入 `request_id` 与执行日志状态机，禁止盲重试。
4. daemon 崩溃会影响全部命令。
   - 缓解：`status/logs`、健康状态机、重连策略、磁盘恢复。

## 成功判定

当以下条件同时满足，即认为本设计完成：

1. `trail` 保持普通权限，`traild` 为唯一管理员进程。
2. 普通命令在 daemon 不存在时，能通过已安装 bootstrap 自动启动；未安装时，返回稳定的 `DAEMON_BOOTSTRAP_REQUIRED` envelope。
3. 同一 daemon 生命周期内，OCR/图像匹配/窗口绑定等重资源只初始化一次并被跨命令复用。
4. 已提权游戏窗口输入真实恢复稳定。
5. 扩展屏 + 混合 DPI + 后台窗口场景下，截图稳定输出 `1920x1080` 游戏窗口内容。
6. 现有 skill/Agent 不需要重写命令面，只需继续消费兼容 envelope。
