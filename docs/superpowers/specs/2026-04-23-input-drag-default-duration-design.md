# 输入拖动默认时长收敛设计

## 目标

把项目里未显式传入 `duration` 的拖动动作默认时长统一收敛到 `0.2s`，避免当前“无 duration 时几乎瞬移”的行为在大多数游戏场景里不可用。

同时满足两个约束：

1. 已显式传入的 `duration` 继续优先，不被默认值覆盖。
2. 场景代码和 daemon 默认都能继承这次修复，不要求 Agent 改变调用方式，也不要求扩面 CLI/RPC 契约。

## 背景

当前拖动链路如下：

- `trail input drag` 会调用 `trail/commands/input.py`。
- daemon 侧的 `trail/daemon/command_service.py::_drag()` 会转发到 `runtime.drag_to(...)`。
- 场景代码也直接调用 `trail/runtime/operator.py::RuntimeOperator.drag_to(...)`。
- 最终由 `PyAutoGuiInputDriver.drag(...)` 落到具体输入后端。

当前默认值不一致：

- `RuntimeOperator.drag_to()` 在未传 `duration` 时直接透传 `None`。
- Windows 原生拖动 `_virtual_drag()` 在 `duration is None` 时走“两段 `sleep(0.1)` + 一次跳点”的特殊分支。
- 非 Windows `pyautogui.dragTo()` 在 `duration is None` 时写死成 `0.5`。

这会带来两个问题：

1. 默认行为不统一，调用方很难推断“没传 duration 时到底会发生什么”。
2. 项目里大量场景代码调用的是 `runtime.drag_to(...)`，而不是手工 CLI；如果默认值只留在最底层，各层 trace 与未来新 driver 的语义都容易继续分叉。

## 非目标

本轮不做以下扩面：

- 不把默认时长做成配置项或环境变量。
- 不改现有场景里已经显式传入 `duration` 的业务节奏。
- 不改点击、按键、文本输入等其他输入动作的默认时长。
- 不新增 `trail input drag --duration` 或新的 daemon payload 字段。
- 不统一 direct `PyAutoGuiInputDriver.drag()` 在脱离 runtime 时的默认值语义；本轮只修项目实际调用链。

## 方案

### 方案选择

默认值收敛点固定放在 `RuntimeOperator.drag_to()`。

原因：

1. `RuntimeOperator.drag_to()` 是场景代码与 daemon 共用的输入抽象层，改这里后，scene 和原子命令都会默认生效。
2. 在 runtime 层完成归一化后，trace 记录到的是实际使用的时长，而不是 `None`。
3. 当前仓库内实际拖动调用链都经由 runtime 进入 driver；把修复点卡在 runtime，能命中用户要求的“项目中的场景命令会调用的那个方法”，同时保持改动最小。

### 具体改动

1. 在 `trail/runtime/operator.py` 定义共享常量，例如 `DEFAULT_DRAG_DURATION_SECONDS = 0.2`。
2. `RuntimeOperator.drag_to()` 在 `duration is None` 时先归一成该默认值，再传给 `self.input.drag(...)`，并把归一化后的值写入 trace。
3. `trail/daemon/command_service.py::_drag()`、`trail/commands/input.py` 与各 scene 调用点都不改 payload/参数面；它们继续沿用当前不带 `duration` 的调用方式，由 runtime 统一吃下默认值。
4. `PyAutoGuiInputDriver.drag()`、Windows `_virtual_drag()` 与非 Windows backend fallback 本轮不改默认分支；只要 runtime 传入了显式 `0.2`，现有“显式 duration 走分步拖动”的路径就会自然生效。

## 代码边界

- `trail/runtime/operator.py`
  - 负责定义默认拖动时长常量。
  - 负责把未显式传值的 runtime 调用统一归一到 `0.2s`。
  - 负责让 trace 记录真实生效的 `duration`。
- `trail/daemon/command_service.py`
  - 本轮不改 `_drag()` 的 payload 形状与调用方式。
- `trail/commands/input.py`
  - 本轮不新增 CLI 参数。
- `tests/test_runtime_backends.py`
  - 负责验证 runtime 默认值归一、trace 记录真实时长、显式覆盖仍生效。
- 其他 scene 文件
  - 不改业务调用点；继续调用 `runtime.drag_to(...)` 即可继承默认值修复。

## 数据流

修复后：

- 场景 / daemon / CLI 继续按当前方式调用、不显式传 `duration`
  - `runtime.drag_to(..., duration=None)`
  - 归一为 `0.2`
  - driver 按 `0.2s` 执行实际拖动
- 场景里已有显式 `duration` 的调用
  - 显式值原样透传
  - 默认值不参与覆盖

## 测试

本轮按“先补断言，再改实现”的方式推进。

需要新增或调整的测试：

1. `tests/test_runtime_backends.py`
   - 调整 `RuntimeOperator.drag_to()` 相关 stub / lambda，使其能接收并断言 `duration` 关键字参数。
   - 新增 runtime 层测试，验证未传 `duration` 时会传给 driver `0.2`。
   - 新增 runtime 层显式覆盖测试，验证 `runtime.drag_to(..., duration=<非 0.2 值>)` 时，`input_driver.drag(...)` 实际收到的仍是该显式值，而不是被重新归一成默认值。
   - 新增 trace 断言，验证默认调用记录 `duration=0.2`，显式传值时继续记录原值。
   - 保留显式 `duration` 的现有驱动测试，用来证明 runtime 传下去的显式值仍然能触发 driver 的分步拖动路径。
   - 不把 direct driver 默认值从 `0.5` 改到 `0.2` 作为本轮测试目标，因为本轮设计没有改那层默认语义。
   - 至少同步更新当前文件里直接 stub `input_driver.drag(...)` 的几个用例，避免测试桩签名继续停留在旧接口。

2. `tests/test_runtime_backends.py` 之外的共享测试桩
   - 如有共享 fake / stub 直接模拟 `input_driver.drag(...)` 或依赖 runtime trace，需要做最小签名对齐。
   - 本轮不需要补 CLI payload 或 daemon payload 测试，因为 `input.drag` 的请求形状不变。

不新增的测试：

- 不做真实桌面环境集成测试。
- 不做 direct driver 默认值语义的额外扩面测试。
- 不新增 CLI / daemon 参数透传测试，因为本轮没有新增参数，也不改变 RPC 契约。

## 文档影响

本轮默认不要求改 `README.md` 或 skill 文档。

原因：

1. 不新增命令、参数或 payload 字段。
2. 不改变默认文本协议、renderer、恢复链路或 YAML allowlist。
3. `trail input drag` 的调用方式不变，只是其内部默认拖动时长从“未定义 / 分叉默认”收敛成了稳定的 `0.2s`。

## 风险与取舍

### 为什么不只改 driver

只改 driver 看起来也能碰到部分默认路径，但 runtime trace 仍会记录 `duration=None`，而且仓库内实际场景调用关注的是 `runtime.drag_to(...)` 这层抽象。把修复点放在 runtime，才能同时解决“默认值真正生效”和“调试信息能看到真实默认值”两个问题。

### 为什么不做成配置项

当前需求已经明确是“默认应为 `0.2s`”。先把默认行为收敛，能以最小改动解决当前不可用问题；现在引入配置只会增加接口面和测试面。

### 为什么本轮不顺手统一 direct driver 默认语义

一旦 `RuntimeOperator.drag_to()` 在未传值时总是传入 `0.2`，项目里的 scene / daemon / CLI 默认路径就已经会稳定落到“显式 duration 的拖动逻辑”。继续去改 `PyAutoGuiInputDriver.drag()` 的 direct-call fallback，属于 future-proofing，而不是修复当前问题所必需的最小变更。

## 验收标准

满足以下条件即可认为本轮设计达标：

1. 未显式传 `duration` 的 `runtime.drag_to(...)` 默认按 `0.2s` 执行。
2. scene 侧现有 `runtime.drag_to(...)` 调用点无需修改即可继承新默认值。
3. 显式传入的 `duration` 继续优先，行为不变。
4. `RuntimeOperator` 的 trace 会记录真实生效的 `duration`，默认调用为 `0.2`。
5. `input.drag` 的 CLI / daemon 调用面与 payload 形状保持不变。
6. 相关 runtime 测试能覆盖默认值归一、trace 记录与显式覆盖回归。
