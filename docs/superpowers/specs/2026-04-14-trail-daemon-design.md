# Trail Daemon 设计

## 背景

`trail-cli` 当前以短命 CLI 进程为主，每执行一条命令都会重新初始化运行时。这已经暴露出三类问题：

1. 性能问题：OCR、图像匹配、窗口绑定、场景资源会被重复加载，`ocr read` 一类命令开销偏大。
2. 权限问题：游戏窗口以更高权限运行时，普通权限的 CLI 对输入链路不再可靠，导致点击、拖拽、按键不能稳定生效。
3. 状态问题：跨命令的窗口句柄、场景上下文、模型缓存与诊断信息无法自然复用，排障和实机 smoke 成本高。

本设计将 `trail-cli` 重构为“非管理员薄 CLI + 单管理员全量 daemon”的两层架构，在保持现有命令面的同时，把运行时代码、模型、场景执行和窗口交互集中到常驻进程中。

## 目标

1. 保持 `trail ...` 现有命令面基本不变，Agent 与 skill 不需要整套重写。
2. 把窗口、截图、OCR、找图、输入、`cw` 场景执行全部放进常驻 daemon，避免每条命令重复初始化。
3. 只有 daemon 运行在管理员权限；CLI 和 Agent 继续保持普通权限。
4. 支持“自动拉起 + 显式管理”：普通命令在 daemon 不存在时自动启动，同时提供 `trail daemon start|stop|status|logs`。
5. 保持结构化 envelope 输出契约：`ok`、`data`、`screenshot`、`timing`、`warnings`、`references`、`debug`、`error`。
6. 在扩展屏、混合 DPI、后台截图、已提权游戏窗口输入等现实条件下保持稳定行为。

## 非目标

1. 第一版不做远程访问，不支持跨机器控制。
2. 第一版不拆成普通 daemon + 管理员 helper 双进程。
3. 第一版不改变现有 `guide` 数据模型与 `cw` 业务语义，只改变执行宿主与调用方式。
4. 第一版不追求零迁移成本之外的全新命令体系；优先兼容现有 CLI。

## 总体架构

### 进程模型

- `traild`
  - 单管理员常驻进程。
  - 持有窗口控制器、输入驱动、OCR 引擎、图像匹配器、参考图索引、场景执行器、会话内存态。
  - 对外暴露本机限定的 RPC 服务。
- `trail`
  - 非管理员薄 CLI。
  - 负责参数解析、自动连接/自动拉起 daemon、构造 RPC 请求、打印响应 envelope。
  - 只保留帮助文案、轻量参数校验和 daemon 生命周期管理入口。

### 权限边界

- 只有 `traild` 是管理员进程。
- `trail` 绝不自提权，也不要求 Agent 整体提权。
- 所有真正触碰已提权游戏窗口的能力都经由 daemon 完成，包括：输入、窗口附着、截图、场景执行。

## 组件划分

### 1. CLI 薄壳层

职责：

- 保持现有命令入口：`window`、`screen`、`ocr`、`image`、`input`、`state`、`cw`、`guide`。
- 将命令统一映射为 RPC 请求，例如：
  - `input.click`
  - `ocr.read`
  - `cw.stage.detect`
  - `cw.slots.read`
- 自动探测 daemon 是否可用；不可用时按策略自动拉起。
- 把 daemon 响应原样渲染为现有 envelope 格式。

不负责：

- 直接初始化 OCR、图像匹配、窗口运行时。
- 直接执行窗口输入或场景动作。

### 2. Daemon RPC 层

职责：

- 监听本机 IPC/回环地址。
- 维护请求认证、版本协商、错误映射。
- 将 RPC 路由到具体服务：window、screen、ocr、image、input、session、cw。

设计要求：

- 本地专用，不暴露远程访问。
- 启动时生成一次性认证令牌。
- CLI 每次连接必须带令牌；令牌失效时提示重连或重启 daemon。

### 3. Runtime 服务层

daemon 内部常驻的服务对象：

- `WindowService`
  - 管理窗口附着、句柄缓存、DPI 信息、截图策略、窗口启动。
- `InputService`
  - 负责 click/drag/key 等输入执行与审计记录。
- `CaptureService`
  - 统一窗口截图，优先窗口级抓图；失败时按策略回退。
- `OcrService`
  - 常驻 OCR 模型实例。
- `ImageService`
  - 常驻图像匹配资源与参考图索引。
- `SceneService`
  - 持有 `cw` 场景执行器、阶段识别器、槽位读取器、商店与事件逻辑。
- `SessionService`
  - 维护 session 内存态并同步落盘。

### 4. 存储层

- 继续使用现有 `.trail/sessions`、`.trail/shots`、`.trail/artifacts`。
- daemon 内存中维护热状态，磁盘作为恢复与审计基础。
- daemon 重启后可从磁盘恢复必要 session 元数据；模型与运行时资源则重新加载。

## 命令与数据流

### 普通命令执行

1. 用户执行 `trail <command>`。
2. CLI 解析参数并生成 RPC 请求。
3. 若 daemon 不存在：
   - 尝试自动拉起 `traild`。
   - 获取认证令牌并建立连接。
4. daemon 执行对应服务。
5. daemon 返回统一 envelope。
6. CLI 将 envelope 直接打印给用户/Agent。

### `--verbose` 语义

- `--verbose` 改为 RPC 级调试标记。
- CLI 只转发该标记。
- daemon 决定是否返回 `debug.trace`、模型热身信息、场景中间步骤、窗口/输入诊断信息。

### `session` 语义

- `session_id` 继续作为显式上下文锚点。
- CLI 不持有运行态，只传 `session_id + payload`。
- daemon 负责在内存中维护 session 热状态，并在命令结束后同步落盘。

## 截图与输入策略

### 截图

- Windows 下优先使用窗口级抓图接口，目标是直接得到游戏窗口内容，且分辨率稳定为 `1920x1080`。
- 当窗口级抓图不可用时，再考虑 `PrintWindow` 或 bbox 回退策略。
- 对扩展屏、混合 DPI、后台窗口，daemon 统一封装兼容逻辑，CLI 不再关心具体平台差异。

### 输入

- 所有输入操作都通过管理员 daemon 执行。
- 输入前由 daemon 统一完成窗口准备、焦点确认和必要的错误诊断。
- 输入请求必须记录审计日志：时间、session、窗口、动作类型、坐标/按键。

## 安全与运维

### 安全约束

1. daemon 仅允许本机访问。
2. daemon 启动时生成一次性认证令牌。
3. CLI 必须带令牌才能访问 daemon。
4. daemon 不执行任意代码，不暴露通用 shell 能力，只暴露受控 RPC 方法。
5. 输入类 RPC 全部写入审计日志。

### 生命周期命令

新增：

- `trail daemon start`
- `trail daemon stop`
- `trail daemon status`
- `trail daemon logs`

其中：

- `start` 支持显式预热 OCR/图像模型。
- `stop` 只停止 daemon，不删除 session 文件。
- `status` 至少展示：
  - 是否管理员
  - 监听地址/IPC 标识
  - 启动时间
  - 已加载模型
  - 当前绑定窗口
  - 当前 session 数
- `logs` 只展示 daemon 自身日志，不混入截图产物。

## 错误处理

- daemon 未启动：CLI 自动拉起；失败则返回明确错误。
- daemon 版本不匹配或协议不兼容：返回稳定错误码并提示重启。
- 令牌失效：返回认证错误，CLI 提示重新连接或重启 daemon。
- 窗口句柄失效：返回 `WINDOW_NOT_FOUND`。
- 输入能力不可用：返回 `INPUT_BACKEND_UNAVAILABLE`。
- OCR/图像模型加载失败：返回结构化错误，不让 CLI 伪装成功。

## 验证标准

### 功能

1. 现有 CLI 命令面基本保持不变。
2. `trail input click` 在已提权游戏窗口下真实生效。
3. `trail screen shot` 在扩展屏 + 混合 DPI 下稳定得到 `1920x1080` 游戏窗口图像。
4. `trail ocr read`、`trail image locate`、`trail cw ...` 能通过 daemon 正常执行。

### 性能

1. 同一 daemon 生命周期内，第二次 `ocr read` 显著快于第一次。
2. 图像匹配与场景命令不再每次重复初始化重资源。

### 安全

1. CLI 保持非管理员权限。
2. 未携带认证令牌的本机请求不能调用 daemon。
3. 输入动作存在可审计日志。

## 迁移策略

### 第一阶段

- 建立 daemon 骨架、RPC 协议和生命周期命令。
- 先迁移 `window`、`screen`、`ocr`、`image`、`input`。

### 第二阶段

- 迁移 `session/state` 的热状态维护。
- 将 `cw` 场景逻辑迁入 daemon。

### 第三阶段

- 调整 skill/README，明确 `trail` 已成为 RPC 薄壳。
- 增加性能与实机回归验证。

## 风险与缓解

1. 单管理员全量 daemon 权限面偏大。
   - 缓解：严格限制 RPC 方法、启用令牌认证、保留审计日志。
2. 迁移范围较大，容易一次性改动过多。
   - 缓解：按阶段迁移，优先 runtime，再迁移场景。
3. daemon 崩溃会影响全部命令。
   - 缓解：加入健康检查、自动重连与显式 `status/logs`。

## 成功判定

当以下条件同时满足，即认为本设计完成：

1. 非管理员 CLI 能稳定驱动管理员 daemon 完成截图、OCR、找图、输入和 `cw` 场景命令。
2. 同一 daemon 生命周期内，重资源只加载一次并被跨命令复用。
3. 实机环境下，已提权游戏窗口输入恢复稳定，扩展屏混合 DPI 截图稳定输出 `1920x1080`。
4. 现有 skill/Agent 基本无需改写命令面，仅需继续消费同样的 envelope。
