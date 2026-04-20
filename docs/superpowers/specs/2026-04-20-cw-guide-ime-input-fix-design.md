# `cw guide apply` 中文输入法干扰修复设计

## 目标

解决 `trail cw guide apply` 在 Windows 下输入攻略码时会被当前中文输入法状态干扰的问题，同时满足下面两个约束：

1. 不切换用户当前输入法。
2. 不污染用户剪贴板。

本轮修复后，`cw guide apply` 在输入 share code 时应当与当前 IME 状态解耦；只要窗口聚焦正确，就能稳定把攻略码送进游戏输入框。

## 背景

当前链路如下：

- `trail/daemon/cw_service.py` 的 `_apply_guide()` 会先拉取攻略详情，再调用 `apply_cw_guide_via_ui(runtime, share_code=...)`。
- `trail/scenes/cw/guide.py` 的 `apply_cw_guide_via_ui()` 已经固定了 apply 流程：打开输入框、点击聚焦、输入攻略码、确认、点击 apply、等待按钮消失、最后 `esc` 退出。
- 实际输入动作通过 `runtime.type_text(share_code)` 进入 `trail/runtime/operator.py`。
- `RuntimeOperator.type_text()` 本身只是转发到输入驱动；Windows 下真正实现仍是 `PyAutoGuiInputDriver.type_text()`，当前实现直接调用 `pyautogui.write(text, interval=0)`。

这意味着 `cw guide apply` 的文本输入仍然依赖“当前活动键盘布局 + 当前输入法状态”。当用户停留在中文输入法或候选态时，share code 虽然是 ASCII 字符串，也可能被 IME 接管或组合，导致游戏里收到的不是预期文本。

项目里现有 Windows 输入已经有一个明确模式：

- 鼠标点击与拖拽优先走 Win32 原生事件。
- 单键按压在 Windows 下优先走 `keybd_event`。

因此，`type_text()` 继续依赖 `pyautogui.write(...)` 已经成为当前输入抽象里唯一仍受 IME 状态影响的缺口。

## 非目标

本轮不做下面这些扩面：

- 不改 `trail/scenes/cw/guide.py` 的 apply 节奏与模板等待逻辑。
- 不为 `cw guide apply` 增加专用业务层输入分支。
- 不引入剪贴板中转方案。
- 不在 apply 期间切换或恢复输入法。
- 不顺带修改 `hotkey()`、`press_key()` 或其它无关输入路径。

## 代码边界

本轮职责边界固定如下：

- `trail/runtime/operator.py`
  - 负责把 Windows `type_text()` 从 `pyautogui.write(...)` 改成原生 Unicode 文本注入。
  - 负责同时收敛 `type_text()` 的前置可用性检查：Windows 文本输入路径不能再被 `ensure_available() -> _load_backend() -> pyautogui` 这条旧依赖提前拦住。
  - 非 Windows 平台保持现有行为不变。
  - `hotkey()`、鼠标、单键按压等仍依赖现有后端或现有 Win32 分支；本轮只把“文本输入”的可用性检查与注入路径拆清。
  - 如果 Windows 原生输入 API 不可用或调用失败，直接抛输入错误，不静默回退到旧实现。
- `trail/scenes/cw/guide.py`
  - 不改业务流程。
  - 继续只调用 `runtime.type_text(share_code)`。
- `tests/test_runtime_backends.py`
  - 负责补 Windows 文本输入的驱动级测试。
- `tests/test_cw_guide.py`
  - 保持现有 `apply_cw_guide_via_ui()` 流程测试继续成立，证明业务步骤没有被改坏。

## 方案

### 方案选择

修复点放在输入抽象层，而不是 `cw guide apply` 业务层。

原因：

1. 根因在 Windows 文本输入实现，而不是攻略 apply 流程本身。
2. `RuntimeOperator.type_text()` 的职责就是“把文本稳定送到当前焦点控件”，IME 敏感性属于输入驱动缺陷。
3. 把修复放在驱动层后，后续其它 scene 若复用 `runtime.type_text()`，默认也能继承同样的稳定性。

### Windows 文本注入语义

Windows 下，`PyAutoGuiInputDriver.type_text()` 改为通过 `SendInput` 直接发 Unicode 键盘事件，不再调用 `pyautogui.write(...)`。

实现语义固定为：

1. 把 Python `str` 编码成 `utf-16-le`。
2. 按 2 字节一个码元顺序遍历。
3. 对每个 UTF-16 码元构造 `INPUT/KEYBDINPUT`，其中 `wVk=0`、`wScan=<UTF-16 码元>`。
4. 对每个 UTF-16 码元发送一对事件：
   - key down：`KEYEVENTF_UNICODE`
   - key up：`KEYEVENTF_UNICODE | KEYEVENTF_KEYUP`
5. 保持原始字符串顺序，不额外插入延时或热键。

采用 UTF-16 码元遍历，而不是直接按 Python code point 遍历，有两个目的：

- 与 Windows Unicode 键盘事件的底层表示保持一致。
- 即使未来有非 BMP 字符，也能自然拆成代理项对发送，不把实现锁死在 ASCII / BMP 子集上。

### 失败语义

Windows 注入路径不允许“失败后悄悄退回 `pyautogui.write(...)`”。

原因：

1. 旧路径正是本轮要移除的不稳定来源。
2. 静默回退会把“修复已生效”的表象和“IME 仍可能干扰”的真实行为混在一起，排障会更难。

因此失败语义固定为：

1. `SendInput`、相关 Win32 结构或所需 ctypes 绑定不可用时，直接抛 `TrailError`，code 固定为 `INPUT_BACKEND_UNAVAILABLE`。
2. `SendInput` 返回 `0`、返回值少于期望事件数、或结构初始化失败时，同样直接抛 `TrailError("INPUT_BACKEND_UNAVAILABLE", ...)`。
3. message 需要明确表意为 Windows 原生 Unicode 文本输入不可用或调用失败，不能继续沿用 `pyautogui backend unavailable` 这种会误导排障方向的文案。
4. daemon failure envelope、request / tainted 语义、recover 语义不因本轮修复而扩面；本轮不新增恢复动作。

此外，`type_text()` 的实现必须和前置检查一起收口：不能让 `RuntimeOperator.type_text()` 在进入 Windows 原生注入前，因为 `ensure_available()` 先触发 `pyautogui` 初始化而失败。实现可以通过拆分 Windows 文本输入专用预检查或调整 `ensure_available()` 责任边界来达成，但最终对外语义必须满足：Windows `runtime.type_text()` 可以在没有 `pyautogui.write(...)` 参与的前提下完成文本输入。

## 数据流

修复前：

`apply_cw_guide_via_ui()` -> `runtime.type_text()` -> `PyAutoGuiInputDriver.type_text()` -> `pyautogui.write()` -> 当前键盘布局 / IME 参与字符解释。

修复后：

`apply_cw_guide_via_ui()` -> `runtime.type_text()` -> `PyAutoGuiInputDriver.type_text()` -> Win32 Unicode 键盘事件 -> 焦点控件直接接收文本。

`cw guide apply` 的调用面、payload、artifact 与 session 落盘逻辑都不变；变化只发生在“share code 进入焦点输入框”的最后一跳。

## 测试

本轮按先红后绿的顺序补测试。

### 需要新增或调整的测试

1. `tests/test_runtime_backends.py`
   - 新增 Windows 驱动测试，验证 `type_text("##demo##")` 会调用 Win32 Unicode 输入路径。
   - 断言每个 UTF-16 码元都会产生一对 down / up 事件。
   - 断言 Windows 路径下不会再调用 `pyautogui.write(...)`。
   - 新增至少一条非 BMP 用例，证明实现按 UTF-16 码元而不是按 Python 字符遍历。
   - 新增负向测试：Windows 原生注入初始化失败、`SendInput` 返回 `0` 或短写时，必须抛 `INPUT_BACKEND_UNAVAILABLE`，且不会回退到 `pyautogui.write(...)`。
   - 新增最小非 Windows 回归测试，断言非 Windows 仍走 backend `write(text, interval=0)`，不会误进 Win32 路径。
2. `tests/test_cw_guide.py`
   - 保留现有 `apply_cw_guide_via_ui()` 节奏测试。
   - scene 层不新增 Win32 / IME 细节断言；如有必要，只做最小补充，确认 scene 层仍然只依赖 `runtime.type_text()`，而不新增业务侧特殊分支。

### 不新增的测试

- 不做真实 IME 集成测试；这类测试高度依赖本机输入法环境，稳定性不足。
- 不新增剪贴板相关测试，因为方案明确不使用剪贴板。

## 风险与取舍

### 为什么不在业务层单点修复

业务层专门为 `cw guide apply` 加一个“输入攻略码专用 helper”可以缩小改动面，但会把“文本输入稳定性”这个基础问题留在 scene 层，后续别的文本输入场景仍可能复发同类问题。

### 为什么不切输入法

切输入法虽然实现简单，但会显式改变用户环境状态；即使尝试恢复，也仍然属于用户可见副作用，与本轮约束冲突。

### 为什么不走剪贴板

剪贴板方案通常能绕开 IME，但会污染用户当前剪贴板内容；用户已经明确不接受这条路线。

### 为什么不走窗口消息

直接发窗口消息对传统文本控件常常可行，但游戏 UI 并不总是按标准控件处理字符消息，命中率和可移植性都更差；对当前项目来说风险高于收益。

## 文档影响

本轮默认不改 `README.md`、`skills/trail-cw/SKILL.md`、`skills/trail-cw-guide/SKILL.md`。

原因：

1. 不新增命令、参数或子命令。
2. 不改变 renderer 文本协议与恢复链路。
3. 不改变 Agent 的推荐调用顺序，只是修复内部输入稳定性。

只有当最终实现引入了新的、需要用户识别的稳定错误文案时，才重新评估是否需要同步 README 或相关 skill 文档。

## 验收标准

满足以下条件即可认为本轮设计达标：

1. `trail/scenes/cw/guide.py` 的 apply 流程不变。
2. Windows 下 `runtime.type_text()` 不再依赖 `pyautogui.write(...)`。
3. Windows 文本输入路径不会在进入 native path 前被 `pyautogui` 预检查拦截。
4. Windows native path 失败时抛 `INPUT_BACKEND_UNAVAILABLE`，且不会静默回退到旧路径。
5. 非 Windows 路径继续保持 `pyautogui.write(text, interval=0)` 行为不变。
6. 实现过程中不使用剪贴板，也不切换输入法。
7. 新增 runtime 测试能证明 Unicode 文本注入成功路径、失败路径与非 Windows 回归都被覆盖。
8. 现有 `cw guide apply` 相关测试继续通过。
