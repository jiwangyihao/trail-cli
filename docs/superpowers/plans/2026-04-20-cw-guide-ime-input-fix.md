# `cw guide apply` 中文输入法修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Windows 下 `runtime.type_text()` 通过原生 Unicode 文本注入输入攻略码，绕过当前中文输入法状态，同时不改 `cw guide apply` 的业务流程、不切输入法、不污染剪贴板。

**Architecture:** 先在 `tests/test_runtime_backends.py` 用 TDD 锁定四类行为：Windows 成功路径、Windows 不被 `pyautogui` 预检查拦截、Windows 失败时不回退旧路径、非 Windows 保持旧行为。随后只在 `trail/runtime/operator.py` 收敛 `PyAutoGuiInputDriver` 的文本输入实现与前置可用性检查；`trail/scenes/cw/guide.py` 不改，只用 `tests/test_cw_guide.py` 做流程级回归确认。

**Tech Stack:** Python 3.12, pytest, ctypes/Win32 `SendInput`, uv

**Execution Notes:** 当前 worktree 环境里，`pytest` 默认临时目录会命中 `C:\Users\34404\AppData\Local\Temp\pytest-of-34404` 权限问题。所有测试命令统一追加 `--basetemp .pytest-tmp`。本轮不创建 git commit，除非用户后续显式要求。

---

## File Map

- `trail/runtime/operator.py`
  - `RuntimeOperator._prepare_input_target()` 目前会在输入前统一调用 `input.ensure_available()`。
  - `PyAutoGuiInputDriver.ensure_available()` 目前会强制 `_load_backend()`，导致 `type_text()` 在 Windows 下仍被 `pyautogui` 前置依赖拦住。
  - `PyAutoGuiInputDriver.type_text()` 目前直接调用 `pyautogui.write(text, interval=0)`，这是本轮唯一需要修的生产代码入口。
- `tests/test_runtime_backends.py`
  - 已有 `RuntimeOperator.type_text()` 的准备顺序测试。
  - 已有 Windows `click` / `drag` / `press` 的 Win32 路径测试。
  - 本轮在这一组附近追加 `type_text()` 的 Windows / 非 Windows 契约测试。
- `tests/test_cw_guide.py`
  - 已覆盖 `apply_cw_guide_via_ui()` 的节奏与 `runtime.type_text()` 调用。
  - 本轮不计划改文件，只跑回归验证业务流程未变。

### Task 1: 锁定 Windows 文本输入契约

**Files:**
- Modify: `tests/test_runtime_backends.py:3336-3373, 4732-4921`
- Test: `tests/test_runtime_backends.py`

- [ ] **Step 1: 写第一批失败测试，先锁住“Windows `type_text()` 不再依赖 `pyautogui.write(...)`”和“不会被旧的 `ensure_available()` 拦截”**

```python
def test_runtime_operator_type_text_on_windows_does_not_require_pyautogui_backend(monkeypatch):
    import trail.runtime.operator as operator_module

    calls: list[tuple[str, object]] = []

    class WindowStub:
        def capture(self, **kwargs):
            return Image.new("RGB", (20, 20), color="white")

        def capture_to_workspace(self):
            raise AssertionError("not used")

        def prepare_input(self):
            calls.append(("prepare_input", None))

        def is_foreground(self):
            return True

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            calls.append(("SendInput", count))
            return count

    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("pyautogui should not be loaded before native type_text"))),
    )

    runtime = operator_module.RuntimeOperator(
        window=WindowStub(),
        matcher=SimpleNamespace(locate=lambda template, image: None),
        ocr_engine=SimpleNamespace(run=lambda image: []),
        input_driver=operator_module.PyAutoGuiInputDriver(),
    )

    runtime.type_text("##demo##")

    assert calls == [
        ("prepare_input", None),
        ("SendInput", len("##demo##") * 2),
    ]


def test_pyautogui_input_driver_type_text_uses_sendinput_unicode_packets_on_windows(monkeypatch):
    import trail.runtime.operator as operator_module

    packets: list[tuple[int, int, int]] = []
    backend_calls: list[tuple[str, float]] = []

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            for index in range(count):
                record = inputs[index]
                packets.append((record.ki.wVk, record.ki.wScan, record.ki.dwFlags))
            return count

    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: SimpleNamespace(write=lambda text, interval=0: backend_calls.append((text, interval)))),
    )

    driver = operator_module.PyAutoGuiInputDriver()
    driver.type_text("##")

    assert backend_calls == []
    assert packets == [
        (0, ord("#"), operator_module.PyAutoGuiInputDriver.KEYEVENTF_UNICODE),
        (0, ord("#"), operator_module.PyAutoGuiInputDriver.KEYEVENTF_UNICODE | operator_module.PyAutoGuiInputDriver.KEYEVENTF_KEYUP),
        (0, ord("#"), operator_module.PyAutoGuiInputDriver.KEYEVENTF_UNICODE),
        (0, ord("#"), operator_module.PyAutoGuiInputDriver.KEYEVENTF_UNICODE | operator_module.PyAutoGuiInputDriver.KEYEVENTF_KEYUP),
    ]
```

- [ ] **Step 2: 运行这一批测试，确认它们在当前实现下失败**

Run: `uv run pytest tests/test_runtime_backends.py -k "type_text_on_windows or type_text_uses_sendinput" --basetemp .pytest-tmp -v`

Expected: FAIL，当前失败原因应是：
- `RuntimeOperator.type_text()` 仍然先触发 `ensure_available() -> _load_backend()`。
- `PyAutoGuiInputDriver.type_text()` 仍然走 `pyautogui.write(...)`，不会产生 `SendInput` 包。

### Task 2: 用最小生产代码让 Windows happy path 变绿

**Files:**
- Modify: `trail/runtime/operator.py:67-73, 514-518, 1022-1101`
- Test: `tests/test_runtime_backends.py`

- [ ] **Step 1: 在 `PyAutoGuiInputDriver` 附近加入 Win32 Unicode 文本输入结构与常量**

```python
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == ctypes.sizeof(ctypes.c_ulonglong) else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("value", INPUT_UNION),
    ]
```

- [ ] **Step 2: 最小修改 `PyAutoGuiInputDriver`，让 Windows `type_text()` 走 `SendInput`，并让 `ensure_available()` 不再把文本路径绑到 `pyautogui`**

```python
class PyAutoGuiInputDriver:
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    INPUT_KEYBOARD = 1

    def ensure_available(self) -> None:
        if sys.platform == "win32":
            return
        self._load_backend()

    @classmethod
    def _utf16_code_units(cls, text: str) -> list[int]:
        encoded = text.encode("utf-16-le")
        return [encoded[index] | (encoded[index + 1] << 8) for index in range(0, len(encoded), 2)]

    @classmethod
    def _unicode_key_input(cls, code_unit: int, flags: int) -> INPUT:
        return INPUT(
            type=cls.INPUT_KEYBOARD,
            ki=KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=flags, time=0, dwExtraInfo=0),
        )

    @classmethod
    def _send_windows_unicode_text(cls, text: str) -> None:
        code_units = cls._utf16_code_units(text)
        if not code_units:
            return
        user32 = getattr(getattr(ctypes, "windll", None), "user32", None)
        if user32 is None or not hasattr(user32, "SendInput"):
            raise TrailError("INPUT_BACKEND_UNAVAILABLE", "windows unicode input unavailable")

        payload = []
        for code_unit in code_units:
            payload.append(cls._unicode_key_input(code_unit, cls.KEYEVENTF_UNICODE))
            payload.append(cls._unicode_key_input(code_unit, cls.KEYEVENTF_UNICODE | cls.KEYEVENTF_KEYUP))

        buffer = (INPUT * len(payload))(*payload)
        sent = user32.SendInput(len(payload), buffer, ctypes.sizeof(INPUT))
        if sent != len(payload):
            raise TrailError("INPUT_BACKEND_UNAVAILABLE", "windows unicode input failed")

    def type_text(self, text: str) -> None:
        if sys.platform == "win32":
            self._send_windows_unicode_text(text)
            return
        pyautogui = self._load_backend()
        pyautogui.write(text, interval=0)
```

- [ ] **Step 3: 运行 Task 1 的测试，确认 happy path 变绿**

Run: `uv run pytest tests/test_runtime_backends.py -k "type_text_on_windows or type_text_uses_sendinput" --basetemp .pytest-tmp -v`

Expected: PASS

### Task 3: 锁定剩余边界并做回归验证

**Files:**
- Modify: `tests/test_runtime_backends.py:4732-4921`
- Test: `tests/test_runtime_backends.py`, `tests/test_cw_guide.py`

- [ ] **Step 1: 追加非 BMP、失败不回退、非 Windows 回归三类失败测试**

```python
def test_pyautogui_input_driver_type_text_uses_utf16_code_units_for_non_bmp(monkeypatch):
    import trail.runtime.operator as operator_module

    scans: list[int] = []

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            for index in range(count):
                scans.append(inputs[index].ki.wScan)
            return count

    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=User32()))

    driver = operator_module.PyAutoGuiInputDriver()
    driver.type_text("😀")

    assert scans == [0xD83D, 0xD83D, 0xDE00, 0xDE00]


def test_pyautogui_input_driver_type_text_raises_without_fallback_when_sendinput_short_writes(monkeypatch):
    import trail.runtime.operator as operator_module

    backend_calls: list[tuple[str, float]] = []

    class User32:
        def SendInput(self, count: int, inputs, size: int):
            return count - 1

    monkeypatch.setattr(operator_module.sys, "platform", "win32")
    monkeypatch.setattr(operator_module.ctypes, "windll", SimpleNamespace(user32=User32()))
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: SimpleNamespace(write=lambda text, interval=0: backend_calls.append((text, interval)))),
    )

    driver = operator_module.PyAutoGuiInputDriver()

    with pytest.raises(TrailError) as exc_info:
        driver.type_text("##demo##")

    assert exc_info.value.code == "INPUT_BACKEND_UNAVAILABLE"
    assert backend_calls == []


def test_pyautogui_input_driver_type_text_uses_backend_write_off_windows(monkeypatch):
    import trail.runtime.operator as operator_module

    backend_calls: list[tuple[str, float]] = []

    monkeypatch.setattr(operator_module.sys, "platform", "linux")
    monkeypatch.setattr(
        operator_module.PyAutoGuiInputDriver,
        "_load_backend",
        staticmethod(lambda: SimpleNamespace(write=lambda text, interval=0: backend_calls.append((text, interval)))),
    )

    driver = operator_module.PyAutoGuiInputDriver()
    driver.type_text("##demo##")

    assert backend_calls == [("##demo##", 0)]
```

- [ ] **Step 2: 运行新增测试，确认它们先红**

Run: `uv run pytest tests/test_runtime_backends.py -k "utf16_code_units or short_writes or backend_write_off_windows" --basetemp .pytest-tmp -v`

Expected: 至少有一条 FAIL。当前最可能的失败点是：
- 实现还没有精确验证 `SendInput` 的短写。
- 非 BMP 码元序列还没有被测试驱动到正确形状。
- 非 Windows 回归还未显式锁定。

- [ ] **Step 3: 只补足让边界测试通过的最小实现**

```python
class PyAutoGuiInputDriver:
    @classmethod
    def _send_windows_unicode_text(cls, text: str) -> None:
        code_units = cls._utf16_code_units(text)
        if not code_units:
            return

        user32 = getattr(getattr(ctypes, "windll", None), "user32", None)
        if user32 is None or not hasattr(user32, "SendInput"):
            raise TrailError("INPUT_BACKEND_UNAVAILABLE", "windows unicode input unavailable")

        payload = []
        for code_unit in code_units:
            payload.append(cls._unicode_key_input(code_unit, cls.KEYEVENTF_UNICODE))
            payload.append(cls._unicode_key_input(code_unit, cls.KEYEVENTF_UNICODE | cls.KEYEVENTF_KEYUP))

        buffer = (INPUT * len(payload))(*payload)
        sent = user32.SendInput(len(payload), buffer, ctypes.sizeof(INPUT))
        if sent == 0 or sent != len(payload):
            raise TrailError("INPUT_BACKEND_UNAVAILABLE", "windows unicode input failed")
```

- [ ] **Step 4: 跑完整相关测试，确认输入层与 scene 回归都通过**

Run: `uv run pytest tests/test_runtime_backends.py tests/test_cw_guide.py --basetemp .pytest-tmp`

Expected: PASS，且 `tests/test_cw_guide.py` 无需修改。

- [ ] **Step 5: 记录结果，不提交**

```text
已完成：Windows `type_text()` native Unicode 注入、失败不回退、非 Windows 回归保护、guide apply 流程回归。
未完成：无。
Git commit：跳过，等待用户显式要求。
```
