# CW 装备图标识别 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `trail cw equipment prepare|read`，从 CW raw config 准备装备图标缓存，并在备战页按已确认 `70x70` 网格识别装备背包图标。

**Architecture:** 资源目录、网格裁切、识别 backend 和命令编排分层实现，命令层只消费稳定接口。`cw.equipment.read` 作为 captured read 命令返回截图与装备格事实；`cw.equipment.prepare` 作为无截图资源准备命令只补齐缓存或按 `--refresh` 显式刷新同版本 URL 变化。

**Tech Stack:** Python 3.12, Typer CLI, Pillow, urllib, pytest, Trail daemon service, `trail.output.rendering` 文本协议。

---

## 文件结构

- Create: `trail/scenes/cw/equipment_resources.py`
  - 负责 raw config 装备 catalog 派生、`cache_key` 生成、安全化路径、图标下载、PIL 校验、manifest 原子读写和版本目录锁。
- Create: `trail/scenes/cw/equipment_grid.py`
  - 负责已确认静态 profile、`round_half_up`、三列六行坐标生成、`70x70` ROI 裁切、非整数列 floor/ceil 双候选。
- Create: `trail/scenes/cw/equipment_recognition.py`
  - 负责 `EquipmentIconRecognizer` 协议、vector recognizer、Top-K 预筛、alpha mask 精匹配、空格检测与不确定阈值。
- Create: `trail/scenes/cw/equipment.py`
  - 负责 `prepare_cw_equipment`、`read_cw_equipment`、runtime 截图读取、资源缓存准备、grid+recognizer 串联和命令 data shape。
- Modify: `trail/scenes/cw/guide.py`
  - 新增 `fetch_cw_raw_guide_config()`，稳定暴露 raw config，不改变 `fetch_cw_guide_config()` 的 normalized shape。
- Modify: `trail/commands/cw.py`
  - 新增 `equipment` 子命令组，暴露 `prepare --refresh` 和 `read`。
- Modify: `trail/daemon/cw_service.py`
  - 注册 equipment factories 和 handlers。
- Modify: `trail/daemon/command_service.py`
  - 将 `cw.equipment.read` 放入 `CW_CAPTURE_METHODS`；将 `cw.equipment.prepare` 放入无截图 session-save 路径。
- Modify: `trail/output/rendering.py`
  - 新增 `cw.equipment.prepare` 和 `cw.equipment.read` renderer，冻结首行、正文顺序、低置信 warn 和 YAML 不支持语义。
- Create: `tests/test_cw_equipment.py`
  - 覆盖 catalog、cache、grid、recognizer、scene orchestration。
- Modify: `tests/test_output_rendering.py`
  - 覆盖 renderer 输出协议、README/AGENTS 文档约束。
- Modify: `tests/test_cw_rpc_contracts.py`
  - 覆盖 CLI 到 daemon method/payload 映射和 YAML 不支持。
- Modify: `tests/test_daemon_protocol.py`
  - 覆盖 command_service 对 captured read / no-shot prepare 的路由。
- Modify: `README.md`
  - 说明装备命令、输出示例、缓存刷新语义。
- Modify: `AGENTS.md`
  - 冻结 renderer 家族、首行、正文顺序、YAML 语义和 must-keep 事实。
- Modify: `skills/trail-cw-prep/SKILL.md`
  - 说明普通备战阶段需要装备背包事实时使用 `cw.equipment.read`，并遵守截图优先。
- Modify: `skills/trail-cw-prep/references/command-surface.md`
  - 加入装备读取与资源准备命令。

---

### Task 0: 准备 worktree、基线和执行约束

**Files:**
- No source edits in this task unless `.worktrees/` is not ignored.

- [ ] **Step 1: 检查 worktree 根目录**

Run: `Test-Path .worktrees; Test-Path worktrees`

Expected: 优先使用项目内 `.worktrees`。如果 `.worktrees` 不存在但 `worktrees` 存在，使用 `worktrees`。

- [ ] **Step 2: 验证 worktree 根目录被忽略**

Run: `git check-ignore -q .worktrees; if ($LASTEXITCODE -eq 0) { "ignored" } else { "not ignored" }`

Expected: 输出 `ignored`。如果输出 `not ignored`，先把 `.worktrees/` 加入 `.gitignore` 并单独提交；没有用户要求时不要提交其它文件。

- [ ] **Step 3: 创建实现 worktree**

Run: `git worktree add ".worktrees/cw-equipment-icon-recognition" -b "feature/cw-equipment-icon-recognition"`

Expected: 创建 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-equipment-icon-recognition`。不要修改或回退主工作区已有未跟踪文档。

- [ ] **Step 4: 同步已批准 spec 与 plan**

在 worktree 中保留以下两个文档，内容与主工作区一致：

```text
C:\Users\34404\source\repos\trail-cli\.worktrees\cw-equipment-icon-recognition\docs\superpowers\specs\2026-04-26-cw-equipment-icon-recognition-design.md
C:\Users\34404\source\repos\trail-cli\.worktrees\cw-equipment-icon-recognition\docs\superpowers\plans\2026-04-27-cw-equipment-icon-recognition.md
```

后续所有实现子代理提示词必须同时提供主工作区 spec/plan 完整路径和 worktree 内 spec/plan 完整路径。新启动实现或 review 子代理提示词必须超过 2000 中文字；复用已有子代理会话时只补充相对上次的变化说明。

- [ ] **Step 5: 运行基线聚焦测试**

Workdir: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-equipment-icon-recognition`

Run: `pytest tests/test_cw_guide.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py -q`

Expected: PASS。如果基线失败，先报告具体失败并判断是否为既有问题；不要在未理解基线的情况下开始实现。

- [ ] **Step 6: 明确子代理执行顺序**

Task 1、Task 2、Task 3、Task 4 是独立底层模块，允许用多个实现子代理并发，前提是每个子代理只改自己的文件范围。Task 5 依赖 Task 1-4 的接口；Task 6 依赖 Task 5 的 data shape；Task 7 依赖 Task 6；Task 8 最后执行。每个写入任务完成后并发启动 3 到 5 个只读 review 子代理，全部无 Critical/Important 后再进入依赖它的任务。

---

### Task 1: 暴露 raw config 并派生装备 catalog

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Create: `trail/scenes/cw/equipment_resources.py`
- Create: `tests/test_cw_equipment.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_cw_equipment.py` 新增：

```python
from __future__ import annotations

import importlib


def load_equipment_resources_module():
    return importlib.import_module("trail.scenes.cw.equipment_resources")


def test_build_equipment_catalog_includes_advanced_and_basic_without_kind_collision():
    resources = load_equipment_resources_module()
    raw_config = {
        "rpg_game_big_version": "3.2",
        "equipment_list": [
            {
                "id": "same-id",
                "name": "进阶装甲",
                "icon": "https://act-webstatic.mihoyo.com/advanced.png",
                "category": "4",
                "category_name": "进阶",
                "compose_list": [
                    {
                        "childrens": [
                            {
                                "id": "same-id",
                                "name": "基础装甲",
                                "icon": "https://act-webstatic.mihoyo.com/basic.png",
                                "category": "1",
                                "category_name": "基础",
                            },
                            {
                                "id": "base-2",
                                "name": "基础电池",
                                "icon": "https://act-webstatic.mihoyo.com/battery.png",
                                "category": "1",
                                "category_name": "基础",
                            },
                        ]
                    }
                ],
            }
        ],
    }

    catalog = resources.build_cw_equipment_catalog(raw_config)

    assert [item.cache_key for item in catalog] == ["advanced-same-id", "basic-same-id", "basic-base-2"]
    assert [item.kind for item in catalog] == ["advanced", "basic", "basic"]
    assert [item.name for item in catalog] == ["进阶装甲", "基础装甲", "基础电池"]
    assert catalog[0].big_version == "3.2"


def test_build_equipment_catalog_uses_stable_noid_key_for_missing_id():
    resources = load_equipment_resources_module()
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [
                {
                    "name": "无 ID 装备",
                    "icon": "https://act-webstatic.mihoyo.com/no-id.png",
                    "compose_list": [],
                }
            ],
        }
    )

    assert len(catalog) == 1
    assert catalog[0].cache_key.startswith("advanced-noid-")
    assert catalog[0].id is None
```

在 `tests/test_cw_guide.py` 新增 raw helper 测试：

```python
def test_fetch_cw_raw_guide_config_returns_cached_raw_data(monkeypatch, tmp_path):
    from trail.scenes.cw import guide

    monkeypatch.setattr(
        guide,
        "_get_cw_config_data",
        lambda timeout=10, workspace_root=None: {"rpg_game_big_version": "3.2", "equipment_list": []},
    )

    assert guide.fetch_cw_raw_guide_config(workspace_root=tmp_path) == {
        "rpg_game_big_version": "3.2",
        "equipment_list": [],
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cw_equipment.py::test_build_equipment_catalog_includes_advanced_and_basic_without_kind_collision tests/test_cw_equipment.py::test_build_equipment_catalog_uses_stable_noid_key_for_missing_id tests/test_cw_guide.py::test_fetch_cw_raw_guide_config_returns_cached_raw_data -q`

Expected: FAIL，错误包含 `No module named 'trail.scenes.cw.equipment_resources'` 或 `fetch_cw_raw_guide_config` 不存在。

- [ ] **Step 3: 实现 raw helper**

在 `trail/scenes/cw/guide.py` 的 `fetch_cw_guide_config()` 前新增：

```python
def fetch_cw_raw_guide_config(*, timeout: int = 10, workspace_root: str | Path | None = None) -> dict:
    return _get_cw_config_data(timeout=timeout, workspace_root=workspace_root)
```

- [ ] **Step 4: 实现 catalog dataclass 与 helper**

创建 `trail/scenes/cw/equipment_resources.py`：

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha1
from typing import Any

from trail.core.errors import TrailError


@dataclass(frozen=True)
class EquipmentCatalogEntry:
    cache_key: str
    id: str | None
    name: str
    kind: str
    category: str | None
    category_name: str | None
    icon_url: str
    big_version: str


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cache_key(*, kind: str, equipment_id: str | None, name: str, icon_url: str) -> str:
    if equipment_id:
        return f"{kind}-{equipment_id}"
    digest = sha1(f"{name}\n{icon_url}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}-noid-{digest}"


def _entry_from_mapping(item: Mapping[str, Any], *, kind: str, big_version: str) -> EquipmentCatalogEntry | None:
    name = _text_or_none(item.get("name"))
    icon_url = _text_or_none(item.get("icon"))
    if name is None or icon_url is None:
        return None
    equipment_id = _text_or_none(item.get("id"))
    return EquipmentCatalogEntry(
        cache_key=_cache_key(kind=kind, equipment_id=equipment_id, name=name, icon_url=icon_url),
        id=equipment_id,
        name=name,
        kind=kind,
        category=_text_or_none(item.get("category")),
        category_name=_text_or_none(item.get("category_name")),
        icon_url=icon_url,
        big_version=big_version,
    )


def _append_unique(target: list[EquipmentCatalogEntry], seen: set[str], entry: EquipmentCatalogEntry | None) -> None:
    if entry is None or entry.cache_key in seen:
        return
    seen.add(entry.cache_key)
    target.append(entry)


def build_cw_equipment_catalog(raw_config: Mapping[str, Any]) -> list[EquipmentCatalogEntry]:
    big_version = _text_or_none(raw_config.get("rpg_game_big_version"))
    if big_version is None:
        raise TrailError("CW_EQUIPMENT_VERSION_MISSING", "cw equipment config missing rpg_game_big_version")
    equipment_list = raw_config.get("equipment_list")
    if not isinstance(equipment_list, list):
        raise TrailError("CW_EQUIPMENT_CONFIG_INVALID", "cw equipment config missing equipment_list")

    catalog: list[EquipmentCatalogEntry] = []
    seen: set[str] = set()
    for item in equipment_list:
        if not isinstance(item, Mapping):
            continue
        _append_unique(catalog, seen, _entry_from_mapping(item, kind="advanced", big_version=big_version))
        compose_list = item.get("compose_list")
        if not isinstance(compose_list, list):
            continue
        for compose in compose_list:
            if not isinstance(compose, Mapping):
                continue
            childrens = compose.get("childrens")
            if not isinstance(childrens, list):
                continue
            for child in childrens:
                if isinstance(child, Mapping):
                    _append_unique(catalog, seen, _entry_from_mapping(child, kind="basic", big_version=big_version))
    return catalog
```

- [ ] **Step 5: 运行 catalog 测试**

Run: `pytest tests/test_cw_equipment.py::test_build_equipment_catalog_includes_advanced_and_basic_without_kind_collision tests/test_cw_equipment.py::test_build_equipment_catalog_uses_stable_noid_key_for_missing_id tests/test_cw_guide.py::test_fetch_cw_raw_guide_config_returns_cached_raw_data -q`

Expected: PASS。

- [ ] **Step 6: 提交**

如果用户要求提交，再执行：

```bash
git add trail/scenes/cw/guide.py trail/scenes/cw/equipment_resources.py tests/test_cw_equipment.py tests/test_cw_guide.py
git commit -m "feat(cw): 派生装备资源目录"
```

---

### Task 2: 实现装备图标缓存与 prepare 结果

**Files:**
- Modify: `trail/scenes/cw/equipment_resources.py`
- Modify: `tests/test_cw_equipment.py`

- [ ] **Step 1: 写缓存契约失败测试**

在 `tests/test_cw_equipment.py` 追加：

```python
from io import BytesIO
from PIL import Image
import json
import pytest


def _png_bytes(color: str = "red") -> bytes:
    output = BytesIO()
    Image.new("RGBA", (16, 16), color=color).save(output, format="PNG")
    return output.getvalue()


def test_prepare_equipment_icon_cache_downloads_missing_and_reuses_complete_cache(tmp_path):
    resources = load_equipment_resources_module()
    calls: list[str] = []
    catalog = resources.build_cw_equipment_catalog(
        {
            "rpg_game_big_version": "3.2",
            "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
        }
    )

    def fetcher(url: str, *, timeout: float, max_bytes: int) -> bytes:
        calls.append(url)
        assert timeout == resources.EQUIPMENT_ICON_DOWNLOAD_TIMEOUT_SECONDS
        assert max_bytes == resources.EQUIPMENT_ICON_MAX_BYTES
        return _png_bytes()

    first = resources.prepare_equipment_icon_cache(catalog, workspace_root=tmp_path, fetcher=fetcher)
    second = resources.prepare_equipment_icon_cache(catalog, workspace_root=tmp_path, fetcher=fetcher)

    assert first == {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": False}
    assert second == {"big_version": "3.2", "count": 1, "cached": 1, "downloaded": 0, "refreshed": False}
    assert calls == ["https://act-webstatic.mihoyo.com/e1.png"]
    manifest = json.loads((tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["items"][0]["cache_key"] == "advanced-e1"
    assert manifest["items"][0]["local_path"] == "icons/advanced-e1.png"


def test_prepare_equipment_icon_cache_requires_safe_big_version(tmp_path):
    resources = load_equipment_resources_module()
    with pytest.raises(Exception) as exc_info:
        resources.build_cw_equipment_catalog({"equipment_list": []})

    assert getattr(exc_info.value, "code", None) == "CW_EQUIPMENT_VERSION_MISSING"
```

追加 refresh 与损坏缓存测试：

```python
def test_prepare_equipment_icon_cache_refresh_redownloads_changed_url_only_with_refresh(tmp_path):
    resources = load_equipment_resources_module()
    first_catalog = resources.build_cw_equipment_catalog(
        {"rpg_game_big_version": "3.2", "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/a.png"}]}
    )
    second_catalog = resources.build_cw_equipment_catalog(
        {"rpg_game_big_version": "3.2", "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/b.png"}]}
    )
    calls: list[str] = []

    def fetcher(url: str, *, timeout: float, max_bytes: int) -> bytes:
        del timeout, max_bytes
        calls.append(url)
        return _png_bytes("blue")

    resources.prepare_equipment_icon_cache(first_catalog, workspace_root=tmp_path, fetcher=fetcher)
    no_refresh = resources.prepare_equipment_icon_cache(second_catalog, workspace_root=tmp_path, fetcher=fetcher)
    refreshed = resources.prepare_equipment_icon_cache(second_catalog, workspace_root=tmp_path, fetcher=fetcher, refresh=True)

    assert no_refresh["downloaded"] == 0
    assert refreshed["downloaded"] == 1
    assert refreshed["refreshed"] is True
    assert calls == ["https://act-webstatic.mihoyo.com/a.png", "https://act-webstatic.mihoyo.com/b.png"]


def test_prepare_equipment_icon_cache_redownloads_corrupt_file(tmp_path):
    resources = load_equipment_resources_module()
    catalog = resources.build_cw_equipment_catalog(
        {"rpg_game_big_version": "3.2", "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}]}
    )
    resources.prepare_equipment_icon_cache(catalog, workspace_root=tmp_path, fetcher=lambda url, timeout, max_bytes: _png_bytes("red"))
    icon_path = tmp_path / ".trail" / "cache" / "cw-equipment-icons" / "3.2" / "icons" / "advanced-e1.png"
    icon_path.write_text("broken", encoding="utf-8")
    calls: list[str] = []

    result = resources.prepare_equipment_icon_cache(
        catalog,
        workspace_root=tmp_path,
        fetcher=lambda url, timeout, max_bytes: calls.append(url) or _png_bytes("green"),
    )

    assert result["downloaded"] == 1
    assert calls == ["https://act-webstatic.mihoyo.com/e1.png"]
```

- [ ] **Step 2: 运行缓存测试确认失败**

Run: `pytest tests/test_cw_equipment.py::test_prepare_equipment_icon_cache_downloads_missing_and_reuses_complete_cache tests/test_cw_equipment.py::test_prepare_equipment_icon_cache_refresh_redownloads_changed_url_only_with_refresh tests/test_cw_equipment.py::test_prepare_equipment_icon_cache_redownloads_corrupt_file -q`

Expected: FAIL，`prepare_equipment_icon_cache` 不存在。

- [ ] **Step 3: 实现缓存常量、安全化和图标校验**

在 `trail/scenes/cw/equipment_resources.py` 追加：

```python
from contextlib import contextmanager
import json
import os
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from PIL import Image

EQUIPMENT_ICON_CACHE_RELATIVE = Path(".trail") / "cache" / "cw-equipment-icons"
EQUIPMENT_ICON_DOWNLOAD_TIMEOUT_SECONDS = 10.0
EQUIPMENT_ICON_MAX_BYTES = 2_000_000
EQUIPMENT_ICON_ALLOWED_HOSTS = {"act-webstatic.mihoyo.com"}
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value).strip(". ")
    if not cleaned or cleaned in {".", ".."} or cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        return sha1(value.encode("utf-8")).hexdigest()[:16]
    return cleaned


def _cache_root(workspace_root: str | Path | None) -> Path:
    root = Path.cwd() if workspace_root is None else Path(workspace_root)
    return root / EQUIPMENT_ICON_CACHE_RELATIVE


def _version_dir(big_version: str, *, workspace_root: str | Path | None) -> Path:
    return _cache_root(workspace_root) / _safe_segment(big_version)


def _validate_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in EQUIPMENT_ICON_ALLOWED_HOSTS:
        raise TrailError("CW_EQUIPMENT_ICON_URL_UNSUPPORTED", f"unsupported equipment icon url: {url}")


def _download_icon(url: str, *, timeout: float, max_bytes: int) -> bytes:
    _validate_https_url(url)
    request = Request(url, headers={"user-agent": "trail-cli"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise TrailError("CW_EQUIPMENT_ICON_TOO_LARGE", "equipment icon response too large")
    return data


def _open_verified_png(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.convert("RGBA").load()
        return True
    except Exception:
        return False


def _write_verified_icon(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with Image.open(BytesIO(data)) as image:
        image.convert("RGBA").save(tmp_path, format="PNG")
    if not _open_verified_png(tmp_path):
        tmp_path.unlink(missing_ok=True)
        raise TrailError("CW_EQUIPMENT_ICON_INVALID", "equipment icon cannot be opened by PIL")
    os.replace(tmp_path, path)
```

- [ ] **Step 4: 实现 manifest、文件锁和 prepare**

继续在 `equipment_resources.py` 追加：

```python
@contextmanager
def _version_lock(version_dir: Path):
    version_dir.mkdir(parents=True, exist_ok=True)
    lock_path = version_dir / ".prepare.lock"
    deadline = monotonic() + 30.0
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if monotonic() >= deadline:
                raise TrailError("CW_EQUIPMENT_CACHE_LOCK_TIMEOUT", "equipment icon cache lock timeout")
            sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


def _manifest_path(version_dir: Path) -> Path:
    return version_dir / "manifest.json"


def _load_manifest(version_dir: Path) -> dict[str, Any]:
    path = _manifest_path(version_dir)
    if not path.is_file():
        return {"items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    return payload if isinstance(payload, dict) else {"items": []}


def _write_manifest(version_dir: Path, manifest: dict[str, Any]) -> None:
    tmp_path = _manifest_path(version_dir).with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, _manifest_path(version_dir))


def _manifest_items_by_key(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = manifest.get("items")
    if not isinstance(items, list):
        return {}
    return {str(item.get("cache_key")): dict(item) for item in items if isinstance(item, dict) and item.get("cache_key")}


def _manifest_entry(entry: EquipmentCatalogEntry, *, local_path: str) -> dict[str, Any]:
    return {
        "cache_key": entry.cache_key,
        "id": entry.id,
        "name": entry.name,
        "kind": entry.kind,
        "category": entry.category,
        "category_name": entry.category_name,
        "icon_url": entry.icon_url,
        "local_path": local_path,
    }


def prepare_equipment_icon_cache(
    catalog: list[EquipmentCatalogEntry],
    *,
    workspace_root: str | Path | None = None,
    refresh: bool = False,
    fetcher=_download_icon,
) -> dict[str, Any]:
    if not catalog:
        return {"big_version": "", "count": 0, "cached": 0, "downloaded": 0, "refreshed": bool(refresh)}
    big_version = catalog[0].big_version
    version_dir = _version_dir(big_version, workspace_root=workspace_root)
    downloaded = 0
    cached = 0
    with _version_lock(version_dir):
        manifest = _load_manifest(version_dir)
        previous_by_key = _manifest_items_by_key(manifest)
        next_items: list[dict[str, Any]] = []
        for entry in catalog:
            safe_key = _safe_segment(entry.cache_key)
            local_rel = f"icons/{safe_key}.png"
            local_path = version_dir / "icons" / f"{safe_key}.png"
            previous = previous_by_key.get(entry.cache_key)
            url_changed = bool(previous and previous.get("icon_url") != entry.icon_url)
            needs_download = not local_path.is_file() or not _open_verified_png(local_path) or (refresh and url_changed)
            if needs_download:
                data = fetcher(entry.icon_url, timeout=EQUIPMENT_ICON_DOWNLOAD_TIMEOUT_SECONDS, max_bytes=EQUIPMENT_ICON_MAX_BYTES)
                _write_verified_icon(local_path, data)
                downloaded += 1
            else:
                cached += 1
            next_items.append(_manifest_entry(entry, local_path=local_rel))
        _write_manifest(version_dir, {"big_version": big_version, "items": next_items})
    return {"big_version": big_version, "count": len(catalog), "cached": cached, "downloaded": downloaded, "refreshed": bool(refresh)}


def load_cached_equipment_icons(
    catalog: list[EquipmentCatalogEntry],
    *,
    workspace_root: str | Path | None = None,
) -> list[tuple[EquipmentCatalogEntry, Image.Image]]:
    if not catalog:
        return []
    version_dir = _version_dir(catalog[0].big_version, workspace_root=workspace_root)
    loaded: list[tuple[EquipmentCatalogEntry, Image.Image]] = []
    for entry in catalog:
        path = version_dir / "icons" / f"{_safe_segment(entry.cache_key)}.png"
        if not path.is_file() or not _open_verified_png(path):
            raise TrailError("CW_EQUIPMENT_ICON_CACHE_INCOMPLETE", f"equipment icon cache incomplete: {entry.cache_key}")
        with Image.open(path) as image:
            loaded.append((entry, image.convert("RGBA")))
    return loaded
```

- [ ] **Step 5: 运行缓存测试**

Run: `pytest tests/test_cw_equipment.py::test_prepare_equipment_icon_cache_downloads_missing_and_reuses_complete_cache tests/test_cw_equipment.py::test_prepare_equipment_icon_cache_refresh_redownloads_changed_url_only_with_refresh tests/test_cw_equipment.py::test_prepare_equipment_icon_cache_redownloads_corrupt_file tests/test_cw_equipment.py::test_prepare_equipment_icon_cache_requires_safe_big_version -q`

Expected: PASS。

- [ ] **Step 6: 提交**

如果用户要求提交，再执行：

```bash
git add trail/scenes/cw/equipment_resources.py tests/test_cw_equipment.py
git commit -m "feat(cw): 准备装备图标缓存"
```

---

### Task 3: 实现已确认装备网格 profile 与 ROI 裁切

**Files:**
- Create: `trail/scenes/cw/equipment_grid.py`
- Modify: `tests/test_cw_equipment.py`

- [ ] **Step 1: 写网格失败测试**

在 `tests/test_cw_equipment.py` 追加：

```python
def load_equipment_grid_module():
    return importlib.import_module("trail.scenes.cw.equipment_grid")


def test_default_equipment_grid_profile_locks_confirmed_coordinates():
    grid = load_equipment_grid_module()
    cells = list(grid.iter_equipment_grid_cells(grid.DEFAULT_EQUIPMENT_GRID_PROFILE, columns=3, rows=6))

    assert cells[0].idx == 1
    assert cells[0].row == 1
    assert cells[0].col == 1
    assert cells[0].box == {"left": 1820, "top": 240, "width": 70, "height": 70}
    assert cells[1].box == {"left": 1740, "top": 240, "width": 70, "height": 70}
    assert cells[2].box == {"left": 1660, "top": 240, "width": 70, "height": 70}
    assert cells[9].row == 4
    assert cells[9].col == 1
    assert cells[9].box["top"] == 473
    assert len(cells) == 18


def test_crop_equipment_cells_returns_70x70_images_and_float_candidates():
    grid = load_equipment_grid_module()
    source = Image.new("RGB", (1920, 1080), color="black")
    cells = list(grid.iter_equipment_grid_cells(grid.DEFAULT_EQUIPMENT_GRID_PROFILE, columns=2, rows=1))

    crops = grid.crop_equipment_cells(source, cells)

    assert [crop.cell.idx for crop in crops] == [1, 2]
    assert [crop.image.size for crop in crops] == [(70, 70), (70, 70)]
    assert crops[0].variant == "exact"
    assert crops[1].variant in {"float", "floor", "ceil"}
```

- [ ] **Step 2: 运行网格测试确认失败**

Run: `pytest tests/test_cw_equipment.py::test_default_equipment_grid_profile_locks_confirmed_coordinates tests/test_cw_equipment.py::test_crop_equipment_cells_returns_70x70_images_and_float_candidates -q`

Expected: FAIL，`trail.scenes.cw.equipment_grid` 不存在。

- [ ] **Step 3: 实现 grid module**

创建 `trail/scenes/cw/equipment_grid.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from PIL import Image


@dataclass(frozen=True)
class EquipmentGridProfile:
    flow: str = "right_to_left_then_top_to_bottom"
    tracked_top_slot_count: int = 2
    tracked_top_slots_excluded: bool = True
    crop_width: int = 70
    crop_height: int = 70
    col1_left: float = 1820.0
    col_step: float = 79.75
    row_origin_top: float = 240.0
    row_step: float = 77.5


@dataclass(frozen=True)
class EquipmentGridCell:
    idx: int
    row: int
    col: int
    x: float
    y: int
    box: dict[str, int]


@dataclass(frozen=True)
class EquipmentCrop:
    cell: EquipmentGridCell
    image: Image.Image
    variant: str


DEFAULT_EQUIPMENT_GRID_PROFILE = EquipmentGridProfile()


def round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _box_for(profile: EquipmentGridProfile, *, col: int, row: int) -> tuple[float, int, dict[str, int]]:
    x = profile.col1_left - (col - 1) * profile.col_step
    y = round_half_up(profile.row_origin_top + (row - 1) * profile.row_step)
    return x, y, {"left": int(x), "top": y, "width": profile.crop_width, "height": profile.crop_height}


def iter_equipment_grid_cells(
    profile: EquipmentGridProfile = DEFAULT_EQUIPMENT_GRID_PROFILE,
    *,
    columns: int = 3,
    rows: int = 6,
) -> Iterable[EquipmentGridCell]:
    idx = 1
    for row in range(1, rows + 1):
        for col in range(1, columns + 1):
            x, y, box = _box_for(profile, col=col, row=row)
            yield EquipmentGridCell(idx=idx, row=row, col=col, x=x, y=y, box=box)
            idx += 1


def _crop_exact(source: Image.Image, *, left: int, top: int, width: int, height: int) -> Image.Image:
    return source.crop((left, top, left + width, top + height)).resize((width, height), Image.Resampling.LANCZOS)


def crop_equipment_cells(source: Image.Image, cells: Iterable[EquipmentGridCell]) -> list[EquipmentCrop]:
    crops: list[EquipmentCrop] = []
    for cell in cells:
        left = cell.x
        top = cell.y
        width = cell.box["width"]
        height = cell.box["height"]
        if float(left).is_integer():
            crops.append(EquipmentCrop(cell=cell, image=_crop_exact(source, left=int(left), top=top, width=width, height=height), variant="exact"))
            continue
        floor_left = int(left)
        ceil_left = floor_left + 1
        crops.append(EquipmentCrop(cell=cell, image=_crop_exact(source, left=floor_left, top=top, width=width, height=height), variant="floor"))
        crops.append(EquipmentCrop(cell=cell, image=_crop_exact(source, left=ceil_left, top=top, width=width, height=height), variant="ceil"))
    return crops
```

- [ ] **Step 4: 运行网格测试**

Run: `pytest tests/test_cw_equipment.py::test_default_equipment_grid_profile_locks_confirmed_coordinates tests/test_cw_equipment.py::test_crop_equipment_cells_returns_70x70_images_and_float_candidates -q`

Expected: PASS。

- [ ] **Step 5: 提交**

如果用户要求提交，再执行：

```bash
git add trail/scenes/cw/equipment_grid.py tests/test_cw_equipment.py
git commit -m "feat(cw): 固定装备背包裁切网格"
```

---

### Task 4: 实现 vector recognizer、空格和不确定策略

**Files:**
- Create: `trail/scenes/cw/equipment_recognition.py`
- Modify: `tests/test_cw_equipment.py`

- [ ] **Step 1: 写识别失败测试**

在 `tests/test_cw_equipment.py` 追加：

```python
def load_equipment_recognition_module():
    return importlib.import_module("trail.scenes.cw.equipment_recognition")


def test_vector_equipment_recognizer_picks_best_candidate_with_gap():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    red_entry = resources.EquipmentCatalogEntry("advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2")
    blue_entry = resources.EquipmentCatalogEntry("advanced-blue", "blue", "蓝装", "advanced", None, None, "https://act-webstatic.mihoyo.com/blue.png", "3.2")
    recognizer = recognition.VectorEquipmentIconRecognizer(
        [(red_entry, Image.new("RGBA", (128, 128), "red")), (blue_entry, Image.new("RGBA", (128, 128), "blue"))]
    )

    result = recognizer.recognize(Image.new("RGBA", (70, 70), "red"))

    assert result.empty is False
    assert result.uncertain is False
    assert result.candidates[0].name == "红装"
    assert result.candidates[0].score > 0.95
    assert result.gap > 0.1


def test_vector_equipment_recognizer_marks_low_gap_uncertain():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    dark = resources.EquipmentCatalogEntry("advanced-dark", "dark", "暗红", "advanced", None, None, "https://act-webstatic.mihoyo.com/dark.png", "3.2")
    light = resources.EquipmentCatalogEntry("advanced-light", "light", "亮红", "advanced", None, None, "https://act-webstatic.mihoyo.com/light.png", "3.2")
    recognizer = recognition.VectorEquipmentIconRecognizer(
        [(dark, Image.new("RGBA", (128, 128), (200, 0, 0, 255))), (light, Image.new("RGBA", (128, 128), (210, 0, 0, 255)))],
        min_gap=0.5,
    )

    result = recognizer.recognize(Image.new("RGBA", (70, 70), (205, 0, 0, 255)))

    assert result.uncertain is True
    assert len(result.candidates) == 2
    assert result.candidates[0].score >= result.candidates[1].score


def test_vector_equipment_recognizer_detects_empty_roi():
    recognition = load_equipment_recognition_module()
    resources = load_equipment_resources_module()
    entry = resources.EquipmentCatalogEntry("advanced-red", "red", "红装", "advanced", None, None, "https://act-webstatic.mihoyo.com/red.png", "3.2")
    recognizer = recognition.VectorEquipmentIconRecognizer([(entry, Image.new("RGBA", (128, 128), "red"))])

    result = recognizer.recognize(Image.new("RGBA", (70, 70), "black"))

    assert result.empty is True
    assert result.candidates == []
```

- [ ] **Step 2: 运行识别测试确认失败**

Run: `pytest tests/test_cw_equipment.py::test_vector_equipment_recognizer_picks_best_candidate_with_gap tests/test_cw_equipment.py::test_vector_equipment_recognizer_marks_low_gap_uncertain tests/test_cw_equipment.py::test_vector_equipment_recognizer_detects_empty_roi -q`

Expected: FAIL，`equipment_recognition` 不存在。

- [ ] **Step 3: 实现 recognizer dataclass 和 score**

创建 `trail/scenes/cw/equipment_recognition.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageChops, ImageStat

from trail.scenes.cw.equipment_resources import EquipmentCatalogEntry


FEATURE_SIZE = (32, 32)
MATCH_SIZE = (64, 64)
DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.72
DEFAULT_MIN_GAP = 0.05


@dataclass(frozen=True)
class EquipmentCandidate:
    equipment_id: str | None
    cache_key: str
    name: str
    score: float
    backend: str = "vector"


@dataclass(frozen=True)
class EquipmentRecognitionResult:
    candidates: list[EquipmentCandidate]
    score: float | None
    gap: float | None
    uncertain: bool
    empty: bool


class EquipmentIconRecognizer(Protocol):
    def recognize(self, image: Image.Image) -> EquipmentRecognitionResult: ...


@dataclass(frozen=True)
class _IndexedIcon:
    entry: EquipmentCatalogEntry
    feature: Image.Image
    match_image: Image.Image


def _normalized_rgba(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.convert("RGBA").resize(size, Image.Resampling.LANCZOS)


def _mean_abs_similarity(left: Image.Image, right: Image.Image) -> float:
    diff = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    mean = sum(ImageStat.Stat(diff).mean) / (3 * 255.0)
    return max(0.0, min(1.0, 1.0 - mean))


def _is_empty_roi(image: Image.Image) -> bool:
    rgb = image.convert("RGB")
    stat = ImageStat.Stat(rgb)
    brightness = sum(stat.mean) / 3.0
    spread = sum(stat.stddev) / 3.0
    return brightness < 12.0 and spread < 8.0


class VectorEquipmentIconRecognizer:
    def __init__(
        self,
        icons: list[tuple[EquipmentCatalogEntry, Image.Image]],
        *,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
        min_gap: float = DEFAULT_MIN_GAP,
    ):
        self.top_k = max(1, int(top_k))
        self.min_score = float(min_score)
        self.min_gap = float(min_gap)
        self._icons = [
            _IndexedIcon(entry=entry, feature=_normalized_rgba(image, FEATURE_SIZE), match_image=_normalized_rgba(image, MATCH_SIZE))
            for entry, image in icons
        ]

    def recognize(self, image: Image.Image) -> EquipmentRecognitionResult:
        if _is_empty_roi(image):
            return EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)
        feature = _normalized_rgba(image, FEATURE_SIZE)
        ranked = sorted(
            ((icon, _mean_abs_similarity(feature, icon.feature)) for icon in self._icons),
            key=lambda item: (-item[1], item[0].entry.name),
        )[: self.top_k]
        match_image = _normalized_rgba(image, MATCH_SIZE)
        refined = sorted(
            ((icon, (_mean_abs_similarity(match_image, icon.match_image) + feature_score) / 2.0) for icon, feature_score in ranked),
            key=lambda item: (-item[1], item[0].entry.name),
        )
        candidates = [
            EquipmentCandidate(
                equipment_id=icon.entry.id,
                cache_key=icon.entry.cache_key,
                name=icon.entry.name,
                score=round(float(score), 4),
            )
            for icon, score in refined
        ]
        top_score = candidates[0].score if candidates else None
        second_score = candidates[1].score if len(candidates) > 1 else 0.0
        gap = round(float(top_score - second_score), 4) if top_score is not None else None
        uncertain = bool(top_score is None or top_score < self.min_score or (gap is not None and gap < self.min_gap))
        return EquipmentRecognitionResult(candidates=candidates, score=top_score, gap=gap, uncertain=uncertain, empty=False)
```

- [ ] **Step 4: 运行识别测试**

Run: `pytest tests/test_cw_equipment.py::test_vector_equipment_recognizer_picks_best_candidate_with_gap tests/test_cw_equipment.py::test_vector_equipment_recognizer_marks_low_gap_uncertain tests/test_cw_equipment.py::test_vector_equipment_recognizer_detects_empty_roi -q`

Expected: PASS。

- [ ] **Step 5: 提交**

如果用户要求提交，再执行：

```bash
git add trail/scenes/cw/equipment_recognition.py tests/test_cw_equipment.py
git commit -m "feat(cw): 识别装备图标候选"
```

---

### Task 5: 实现 scene 层 prepare/read 编排

**Files:**
- Create: `trail/scenes/cw/equipment.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_cw_equipment.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: 写 scene 编排失败测试**

在 `tests/test_cw_equipment.py` 追加：

```python
def load_equipment_scene_module():
    return importlib.import_module("trail.scenes.cw.equipment")


def test_prepare_cw_equipment_builds_catalog_and_cache(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    raw_config = {"rpg_game_big_version": "3.2", "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}]}
    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda catalog, workspace_root=None, refresh=False: {"big_version": "3.2", "count": len(catalog), "cached": 0, "downloaded": 1, "refreshed": refresh})

    result = scene.prepare_cw_equipment(workspace_root=tmp_path, refresh=True)

    assert result == {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": True}


def test_read_cw_equipment_recognizes_best_variants_and_counts(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    grid = load_equipment_grid_module()
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    raw_config = {"rpg_game_big_version": "3.2", "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}]}
    entry = resources.build_cw_equipment_catalog(raw_config)[0]

    class Runtime:
        def capture_image(self, **kwargs):
            assert kwargs == {"normalize": False}
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def __init__(self):
            self.calls = 0

        def recognize(self, image):
            self.calls += 1
            if self.calls == 1:
                return recognition.EquipmentRecognitionResult(
                    candidates=[recognition.EquipmentCandidate("e1", "advanced-e1", "幸运星", 0.93)],
                    score=0.93,
                    gap=0.2,
                    uncertain=False,
                    empty=False,
                )
            return recognition.EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)

    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda workspace_root=None: raw_config)
    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda catalog, workspace_root=None, refresh=False: {"big_version": "3.2", "count": 1, "cached": 1, "downloaded": 0, "refreshed": False})
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda catalog, workspace_root=None: [(entry, Image.new("RGBA", (128, 128), "red"))])
    monkeypatch.setattr(scene, "VectorEquipmentIconRecognizer", lambda icons: Recognizer())
    monkeypatch.setattr(scene, "iter_equipment_grid_cells", lambda profile, columns=3, rows=6: [grid.EquipmentGridCell(1, 1, 1, 1820.0, 240, {"left": 1820, "top": 240, "width": 70, "height": 70}), grid.EquipmentGridCell(2, 1, 2, 1740.25, 240, {"left": 1740, "top": 240, "width": 70, "height": 70})])
    monkeypatch.setattr(scene, "crop_equipment_cells", lambda image, cells: [grid.EquipmentCrop(cell, Image.new("RGBA", (70, 70), "red"), "exact") for cell in cells])

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path)

    assert result["count"] == 1
    assert result["uncertain"] == 0
    assert result["empty"] == 1
    assert result["items"][0]["idx"] == 1
    assert result["items"][0]["name"] == "幸运星"
    assert result["backend"] == "vector"
    assert result["layout"] == "default"
```

- [ ] **Step 2: 运行 scene 测试确认失败**

Run: `pytest tests/test_cw_equipment.py::test_prepare_cw_equipment_builds_catalog_and_cache tests/test_cw_equipment.py::test_read_cw_equipment_recognizes_best_variants_and_counts -q`

Expected: FAIL，`trail.scenes.cw.equipment` 不存在。

- [ ] **Step 3: 实现 scene module**

创建 `trail/scenes/cw/equipment.py`：

```python
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.scenes.cw.equipment_grid import DEFAULT_EQUIPMENT_GRID_PROFILE, crop_equipment_cells, iter_equipment_grid_cells
from trail.scenes.cw.equipment_recognition import VectorEquipmentIconRecognizer
from trail.scenes.cw.equipment_resources import build_cw_equipment_catalog, load_cached_equipment_icons, prepare_equipment_icon_cache
from trail.scenes.cw.guide import fetch_cw_raw_guide_config


def prepare_cw_equipment(*, workspace_root: str | Path | None = None, refresh: bool = False) -> dict[str, Any]:
    raw_config = fetch_cw_raw_guide_config(workspace_root=workspace_root)
    catalog = build_cw_equipment_catalog(raw_config)
    return prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=refresh)


def _runtime_image(runtime) -> Image.Image:
    capture_image = getattr(runtime, "capture_image", None)
    if callable(capture_image):
        image = capture_image(normalize=False)
    else:
        screenshot = runtime.screenshot()
        image = Image.open(BytesIO(screenshot)) if isinstance(screenshot, (bytes, bytearray)) else screenshot
    if not isinstance(image, Image.Image):
        raise TrailError("CW_EQUIPMENT_SCREENSHOT_INVALID", "equipment read requires PIL screenshot")
    if image.width < 1920 or image.height < 1080:
        raise TrailError("CW_EQUIPMENT_LAYOUT_MISMATCH", "equipment grid requires canonical 1920x1080 screenshot")
    return image.convert("RGBA")


def _item_from_result(crop, result) -> dict[str, Any] | None:
    if result.empty:
        return None
    top = result.candidates[0] if result.candidates else None
    alt = result.candidates[1] if len(result.candidates) > 1 else None
    return {
        "idx": crop.cell.idx,
        "row": crop.cell.row,
        "col": crop.cell.col,
        "box": crop.cell.box,
        "name": top.name if top else None,
        "equipment_id": top.equipment_id if top else None,
        "cache_key": top.cache_key if top else None,
        "score": result.score,
        "gap": result.gap,
        "uncertain": result.uncertain,
        "alt": alt.name if alt else None,
        "alt_score": alt.score if alt else None,
        "candidates": [candidate.__dict__ for candidate in result.candidates],
    }


def read_cw_equipment(runtime, *, workspace_root: str | Path | None = None) -> dict[str, Any]:
    raw_config = fetch_cw_raw_guide_config(workspace_root=workspace_root)
    catalog = build_cw_equipment_catalog(raw_config)
    prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=False)
    icons = load_cached_equipment_icons(catalog, workspace_root=workspace_root)
    recognizer = VectorEquipmentIconRecognizer(icons)
    screenshot = _runtime_image(runtime)
    cells = list(iter_equipment_grid_cells(DEFAULT_EQUIPMENT_GRID_PROFILE, columns=3, rows=6))
    crops = crop_equipment_cells(screenshot, cells)
    best_by_idx: dict[int, dict[str, Any] | None] = {}
    for crop in crops:
        result = recognizer.recognize(crop.image)
        item = _item_from_result(crop, result)
        current = best_by_idx.get(crop.cell.idx)
        if current is None or (item is not None and float(item.get("score") or 0.0) > float(current.get("score") or 0.0)):
            best_by_idx[crop.cell.idx] = item
    items = [item for _, item in sorted(best_by_idx.items()) if item is not None]
    empty = len(cells) - len(items)
    uncertain = sum(1 for item in items if item.get("uncertain"))
    return {
        "count": len(items),
        "uncertain": uncertain,
        "empty": empty,
        "items": items,
        "backend": "vector",
        "layout": "default",
    }
```

- [ ] **Step 4: 注册 daemon handler 与 command_service 路由**

修改 `trail/daemon/cw_service.py`：

```python
from trail.scenes.cw.equipment import prepare_cw_equipment, read_cw_equipment
```

在 `handlers` 中加入：

```python
"cw.equipment.prepare": lambda: prepare_cw_equipment(workspace_root=workspace_root, refresh=bool(payload.get("refresh"))),
"cw.equipment.read": lambda: read_cw_equipment(runtime(), workspace_root=workspace_root),
```

修改 `trail/daemon/command_service.py`：

```python
CW_CAPTURE_METHODS = {
    "cw.slots.read",
    "cw.portal.detect",
    "cw.strategy.detect",
    "cw.equipment.read",
}

CW_SESSION_SAVE_METHODS = {
    "cw.battle.clear_in_progress",
    "cw.equipment.prepare",
}
```

- [ ] **Step 5: 写 daemon 路由测试**

在 `tests/test_daemon_protocol.py` 追加或放到 CW command_service 路由测试附近：

```python
def test_command_service_routes_cw_equipment_read_through_capture(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from trail.daemon.command_service import CommandService
    from trail.daemon.cw_service import CwService
    from trail.daemon.models import DaemonRequest
    from trail.daemon.protocol import PROTOCOL_VERSION
    from trail.daemon.session_service import SessionServiceRegistry

    sessions = SessionServiceRegistry()
    service = sessions.for_workspace(str(tmp_path))
    session = service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    calls: list[str] = []

    class Runtime:
        def capture_after_action(self, optional=False, request_id=None):
            calls.append(f"capture:{request_id}")
            return tmp_path / ".trail" / "shots" / "equipment.png"
        def collect_warnings(self): return []
        def match_references(self, screenshot_path, limit=3): return []

    monkeypatch.setattr("trail.daemon.cw_service.read_cw_equipment", lambda runtime, workspace_root=None: calls.append("read") or {"count": 0, "uncertain": 0, "empty": 18, "items": [], "backend": "vector", "layout": "default"})
    runtime_service = SimpleNamespace(get_runtime=lambda **kwargs: Runtime())
    command_service = CommandService(runtime_service=runtime_service, session_service=sessions, cw_service=CwService(runtime_service=runtime_service))

    response = command_service.handle(DaemonRequest(request_id="req-equipment-read", protocol_version=PROTOCOL_VERSION, workspace_root=str(tmp_path), session_id=session.session_id, verbose=False, method="cw.equipment.read", payload={}))

    assert response["ok"] is True
    assert response["screenshot"] == ".trail/shots/equipment.png"
    assert calls == ["read", "capture:req-equipment-read"]
```

- [ ] **Step 6: 运行 scene 和 daemon 测试**

Run: `pytest tests/test_cw_equipment.py::test_prepare_cw_equipment_builds_catalog_and_cache tests/test_cw_equipment.py::test_read_cw_equipment_recognizes_best_variants_and_counts tests/test_daemon_protocol.py::test_command_service_routes_cw_equipment_read_through_capture -q`

Expected: PASS。

- [ ] **Step 7: 提交**

如果用户要求提交，再执行：

```bash
git add trail/scenes/cw/equipment.py trail/daemon/cw_service.py trail/daemon/command_service.py tests/test_cw_equipment.py tests/test_daemon_protocol.py
git commit -m "feat(cw): 接入装备读取场景编排"
```

---

### Task 6: 接入 CLI 与文本 renderer

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`

- [ ] **Step 1: 写 renderer 失败测试**

在 `tests/test_output_rendering.py` 追加：

```python
def test_render_output_cw_equipment_prepare_summary_keeps_zero_values():
    payload = {"ok": True, "data": {"big_version": "3.2", "count": 2, "cached": 0, "downloaded": 0, "refreshed": False}, "screenshot": None, "timing": {}, "warnings": [], "references": [], "debug": None, "error": None}

    assert render_output("cw.equipment.prepare", payload).splitlines() == [
        "ok cw.equipment.prepare big_version=3.2 count=2 cached=0 downloaded=0 refreshed=0",
    ]


def test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref():
    payload = {
        "ok": True,
        "data": {
            "count": 1,
            "uncertain": 1,
            "empty": 17,
            "backend": "vector",
            "layout": "default",
            "items": [
                {"idx": 1, "row": 1, "col": 1, "box": {"left": 1820, "top": 240, "width": 70, "height": 70}, "name": "幸运星", "score": 0.88, "gap": 0.03, "uncertain": True, "alt": "和平手枪", "alt_score": 0.85}
            ],
        },
        "screenshot": ".trail/shots/req-equipment.png",
        "timing": {},
        "warnings": [],
        "references": [{"path": "trail/references/cw/equipment.png", "similarity": 0.9}],
        "debug": None,
        "error": None,
    }

    assert render_output("cw.equipment.read", payload).splitlines() == [
        "ok cw.equipment.read count=1 uncertain=1 empty=17",
        "shot path=.trail/shots/req-equipment.png",
        "info read_image_first=1",
        "item idx=1 row=1 col=1 box=1820,240,70,70 name=幸运星 score=0.88 gap=0.03 uncertain=1 alt=和平手枪 alt_score=0.85",
        "info backend=vector layout=default",
        'warn code=LOW_CONFIDENCE count=1 msg="装备图标低置信，请先看截图确认"',
        "ref path=trail/references/cw/equipment.png sim=0.9",
    ]
```

- [ ] **Step 2: 写 CLI RPC 失败测试**

在 `tests/test_cw_rpc_contracts.py` 追加：

```python
def test_cw_equipment_prepare_maps_to_canonical_command(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client({"cw.equipment.prepare": build_success_response(request_id="req-equipment-prepare", data={"big_version": "3.2", "count": 2, "cached": 1, "downloaded": 1, "refreshed": True})})

    result = cli_runner.invoke(app, ["cw", "equipment", "prepare", "--session", SESSION_ID, "--refresh"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.equipment.prepare big_version=3.2 count=2 cached=1 downloaded=1 refreshed=1"]
    _assert_single_call(client, method="cw.equipment.prepare", payload={"refresh": True}, tmp_path=tmp_path)


def test_cw_equipment_read_maps_to_canonical_command_and_rejects_yaml(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client({"cw.equipment.read": build_success_response(request_id="req-equipment-read", data={"count": 0, "uncertain": 0, "empty": 18, "items": [], "backend": "vector", "layout": "default"}, screenshot=".trail/shots/req-equipment-read.png")})

    result = cli_runner.invoke(app, ["cw", "equipment", "read", "--session", SESSION_ID])
    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "cw", "equipment", "read", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["ok cw.equipment.read count=0 uncertain=0 empty=18", "shot path=.trail/shots/req-equipment-read.png", "info read_image_first=1", "info backend=vector layout=default"]
    assert yaml_result.exit_code == 0
    assert yaml_result.stdout.splitlines() == ["fail cw.equipment.read code=OUTPUT_FORMAT_NOT_SUPPORTED", "shot path=.trail/shots/req-equipment-read.png", 'why msg="yaml not supported for cw.equipment.read"']
    assert client.calls[0]["method"] == "cw.equipment.read"
    _assert_single_call(client, method="cw.equipment.read", payload={}, tmp_path=tmp_path)
```

- [ ] **Step 3: 运行输出和 CLI 测试确认失败**

Run: `pytest tests/test_output_rendering.py::test_render_output_cw_equipment_prepare_summary_keeps_zero_values tests/test_output_rendering.py::test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref tests/test_cw_rpc_contracts.py::test_cw_equipment_prepare_maps_to_canonical_command tests/test_cw_rpc_contracts.py::test_cw_equipment_read_maps_to_canonical_command_and_rejects_yaml -q`

Expected: FAIL，renderer 或 CLI command 不存在。

- [ ] **Step 4: 实现 renderer**

修改 `trail/output/rendering.py`，新增：

```python
def _render_cw_equipment_prepare(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    return _render_success_summary(
        command,
        payload,
        ("big_version", data.get("big_version")),
        ("count", data.get("count") if "count" in data else 0),
        ("cached", data.get("cached") if "cached" in data else 0),
        ("downloaded", data.get("downloaded") if "downloaded" in data else 0),
        ("refreshed", bool(data.get("refreshed"))),
    )


def _render_cw_equipment_read(command: str, payload: dict[str, Any]) -> list[str]:
    data = _as_dict(payload.get("data"))
    lines = [
        "ok "
        + command
        + " "
        + _format_fact_sequence(
            ("count", data.get("count") if "count" in data else 0),
            ("uncertain", data.get("uncertain") if "uncertain" in data else 0),
            ("empty", data.get("empty") if "empty" in data else 0),
        )
    ]
    _append_success_capture_block(lines, payload)
    for item in _as_list(data.get("items")):
        if not isinstance(item, dict):
            continue
        _append_fact_line(
            lines,
            "item",
            ("idx", item.get("idx")),
            ("row", item.get("row")),
            ("col", item.get("col")),
            ("box", _format_box(item.get("box"))),
            ("name", item.get("name")),
            ("score", _format_score_value(item.get("score"))),
            ("gap", _format_score_value(item.get("gap"))),
            ("uncertain", bool(item.get("uncertain")) if "uncertain" in item else None),
            ("alt", item.get("alt")),
            ("alt_score", _format_score_value(item.get("alt_score"))),
        )
    _append_fact_line(lines, "info", ("backend", data.get("backend")), ("layout", data.get("layout")))
    uncertain_count = _coerce_int(data.get("uncertain")) or 0
    if uncertain_count > 0:
        _append_fact_line(lines, "warn", ("code", "LOW_CONFIDENCE"), ("count", uncertain_count), ("msg", "装备图标低置信，请先看截图确认"))
    _append_warnings(lines, payload)
    _append_references(lines, payload)
    return lines
```

并把两个 renderer 加入 `TEXT_RENDERERS`：

```python
"cw.equipment.prepare": _render_cw_equipment_prepare,
"cw.equipment.read": _render_cw_equipment_read,
```

不要把任何 `cw.equipment.*` 加入 `YAML_ALLOWLIST`。

- [ ] **Step 5: 实现 Typer command**

修改 `trail/commands/cw.py`：

```python
CW_EQUIPMENT_HELP = "读取货币战争装备背包图标；prepare 只准备资源缓存，read 会截图并识别当前装备网格。"
equipment_app = typer.Typer(no_args_is_help=True, help=CW_EQUIPMENT_HELP)
cw_app.add_typer(equipment_app, name="equipment")


@equipment_app.command("prepare")
def cw_equipment_prepare(
    session: str = typer.Option(..., "--session"),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    _print_cw("cw.equipment.prepare", session_id=session, payload={"refresh": refresh})


@equipment_app.command("read")
def cw_equipment_read(session: str = typer.Option(..., "--session")) -> None:
    _print_cw("cw.equipment.read", session_id=session)
```

- [ ] **Step 6: 运行输出和 CLI 测试**

Run: `pytest tests/test_output_rendering.py::test_render_output_cw_equipment_prepare_summary_keeps_zero_values tests/test_output_rendering.py::test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref tests/test_cw_rpc_contracts.py::test_cw_equipment_prepare_maps_to_canonical_command tests/test_cw_rpc_contracts.py::test_cw_equipment_read_maps_to_canonical_command_and_rejects_yaml -q`

Expected: PASS。

- [ ] **Step 7: 提交**

如果用户要求提交，再执行：

```bash
git add trail/commands/cw.py trail/output/rendering.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py
git commit -m "feat(cw): 暴露装备读取命令输出"
```

---

### Task 7: 更新协议文档、README 和 active skill

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: 写文档断言失败测试**

在 `tests/test_output_rendering.py` 追加：

```python
def test_readme_and_agents_document_cw_equipment_protocol() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    prep_skill = (PROJECT_ROOT / "skills" / "trail-cw-prep" / "SKILL.md").read_text(encoding="utf-8")
    prep_surface = (PROJECT_ROOT / "skills" / "trail-cw-prep" / "references" / "command-surface.md").read_text(encoding="utf-8")

    assert "trail cw equipment prepare --session <id> [--refresh]" in readme
    assert "trail cw equipment read --session <id>" in readme
    assert "ok cw.equipment.read count=1 uncertain=1 empty=17" in readme
    assert "ok cw.equipment.prepare big_version=3.2 count=2 cached=1 downloaded=1 refreshed=0" in readme
    assert "`cw.equipment.read` 归入列表读取 renderer 家族" in agents
    assert "`cw.equipment.prepare` 归入检测/状态摘要 renderer 家族" in agents
    assert "cw.equipment.read" in prep_skill
    assert "cw.equipment.prepare" in prep_surface
```

- [ ] **Step 2: 运行文档测试确认失败**

Run: `pytest tests/test_output_rendering.py::test_readme_and_agents_document_cw_equipment_protocol -q`

Expected: FAIL，文档尚未包含装备命令说明。

- [ ] **Step 3: 更新 AGENTS 协议约束**

在 `AGENTS.md` 的 `guide / cw 攻略摘要约束` 或相邻 CW 命令协议段加入：

```markdown
- `cw.equipment.read` 归入列表读取 renderer 家族；canonical command 固定为 `cw.equipment.read`，success 首行固定为 `ok cw.equipment.read count=<n> uncertain=<n> empty=<n>`，`count/uncertain/empty` 即使为 `0` 也必须保留。
- `cw.equipment.read` success 正文顺序固定为首行 -> `shot` -> `info read_image_first=1` -> `item` -> `info` -> `warn` -> `ref`；`item` 行字段固定使用 `idx/row/col/box/name/score/gap/uncertain/alt/alt_score`，`box` 固定为 `left,top,width,height`。
- `cw.equipment.read` 单格低置信不失败；当 `uncertain>0` 时默认文本输出 `warn code=LOW_CONFIDENCE count=... msg=...`。
- `cw.equipment.read` 第一版不写入 `cw_state` 长期状态；它不加入 YAML allowlist，`--format yaml` 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。
- `cw.equipment.prepare` 归入检测/状态摘要 renderer 家族；canonical command 固定为 `cw.equipment.prepare`，不产截图，success 首行固定为 `ok cw.equipment.prepare big_version=<version> count=<n> cached=<n> downloaded=<n> refreshed=0|1`，这些事实即使为 `0` 也必须保留。
- `cw.equipment.prepare --refresh` 才处理同一 `rpg_game_big_version` 下 `cache_key` 的 `icon_url` 变化；普通 prepare/read 只补齐缺失或损坏图标。`cw.equipment.prepare` 不加入 YAML allowlist。
```

- [ ] **Step 4: 更新 README**

在 `货币战争流程` 的普通备战相关命令附近补：

```markdown
- 如需读取当前装备背包，先准备或补齐图标缓存：`trail cw equipment prepare --session <id>`；如果确认同一版本资源 URL 变更，显式运行 `trail cw equipment prepare --session <id> --refresh`。
- 读取装备背包：`trail cw equipment read --session <id>`。该命令会返回截图，必须先读 `shot path=...` 对应原始截图，再消费 `item` 行；低置信格子会保留 `alt/alt_score` 并输出 `warn code=LOW_CONFIDENCE`。
```

在输出示例区加入：

```text
ok cw.equipment.prepare big_version=3.2 count=2 cached=1 downloaded=1 refreshed=0
```

```text
ok cw.equipment.read count=1 uncertain=1 empty=17
shot path=.trail/shots/req-equipment-read.png
info read_image_first=1
item idx=1 row=1 col=1 box=1820,240,70,70 name=幸运星 score=0.88 gap=0.03 uncertain=1 alt=和平手枪 alt_score=0.85
info backend=vector layout=default
warn code=LOW_CONFIDENCE count=1 msg="装备图标低置信，请先看截图确认"
```

- [ ] **Step 5: 更新 active skill**

在 `skills/trail-cw-prep/SKILL.md` 的 `Command Surface` 和事实消费说明附近加入：

```markdown
- `trail cw equipment read --session <id>`：读取当前装备背包图标；返回截图时必须先读原始截图，再消费 `item` 行。低置信 `uncertain=1` 只表示需要人工/多模态核验，不等于命令失败。
- `trail cw equipment prepare --session <id> [--refresh]`：准备装备图标缓存；只有明确要刷新同版本 URL 变化时才使用 `--refresh`。
```

在 `skills/trail-cw-prep/references/command-surface.md` 加入：

```markdown
- `trail cw equipment prepare --session <id> [--refresh]`：准备装备图标缓存；普通读取会自动补齐缺失/损坏缓存，`--refresh` 只在确认同版本 URL 变化时使用。
- `trail cw equipment read --session <id>`：读取当前装备背包图标；截图优先，低置信格子看 `uncertain/alt/alt_score`。
```

- [ ] **Step 6: 运行文档测试**

Run: `pytest tests/test_output_rendering.py::test_readme_and_agents_document_cw_equipment_protocol -q`

Expected: PASS。

- [ ] **Step 7: 提交**

如果用户要求提交，再执行：

```bash
git add AGENTS.md README.md skills/trail-cw-prep/SKILL.md skills/trail-cw-prep/references/command-surface.md tests/test_output_rendering.py
git commit -m "docs(cw): 说明装备识别命令协议"
```

---

### Task 8: 全量验证与收尾

**Files:**
- No source edits unless verification exposes a real failure.

- [ ] **Step 1: 运行装备聚焦测试**

Run: `pytest tests/test_cw_equipment.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py -q`

Expected: PASS。

- [ ] **Step 2: 运行 CW 相关回归测试**

Run: `pytest tests/test_cw_slots.py tests/test_cw_shop.py tests/test_cw_stage.py tests/test_cw_strategy.py tests/test_cw_guide.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py -q`

Expected: PASS。

- [ ] **Step 3: 检查 YAML allowlist 未扩张**

Run: `python -c "from trail.output.rendering import YAML_ALLOWLIST; print(sorted(YAML_ALLOWLIST))"`

Expected: 输出只包含 `daemon.status`、`guide.config.cw`、`guide.fetch.cw`、`state.dump`。不包含 `cw.equipment.read` 或 `cw.equipment.prepare`。

- [ ] **Step 4: 检查工作树**

Run: `git status --short`

Expected: 只包含本计划涉及的 source、test、README、AGENTS、skill 文档、spec/plan 文档。不要修改或回退用户已有未跟踪文件。

- [ ] **Step 5: 最终提交**

如果用户要求提交，且前面任务没有逐步提交，则执行：

```bash
git add AGENTS.md README.md trail/scenes/cw/guide.py trail/scenes/cw/equipment.py trail/scenes/cw/equipment_grid.py trail/scenes/cw/equipment_recognition.py trail/scenes/cw/equipment_resources.py trail/commands/cw.py trail/daemon/cw_service.py trail/daemon/command_service.py trail/output/rendering.py tests/test_cw_equipment.py tests/test_cw_guide.py tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py skills/trail-cw-prep/SKILL.md skills/trail-cw-prep/references/command-surface.md docs/superpowers/specs/2026-04-26-cw-equipment-icon-recognition-design.md docs/superpowers/plans/2026-04-27-cw-equipment-icon-recognition.md
git commit -m "feat(cw): 新增装备图标识别命令"
```

---

## 自检

- Spec coverage: 计划覆盖 raw config 资源来源、基础/进阶 catalog 去重、`cache_key` 防覆盖、big version 缓存分代、缺失版本失败、安全化、HTTPS allowlist、timeout/大小上限、PIL 校验、文件锁、原子 manifest、普通 prepare/read 补缺失损坏、`--refresh` 才处理 URL 变化、已确认 `70x70` grid profile、float/floor-ceil 裁切、vector recognizer、Top-K、低置信和空格策略、`cw.equipment.prepare/read` 命令、renderer 首行与顺序、YAML 不支持、README/AGENTS/active skill 文档同步。
- Placeholder scan: 计划未留下未定义的文件路径、未命名函数或延后填写项；每个代码变更步骤都给出具体函数名、测试名、命令和期望结果。
- Type consistency: `EquipmentCatalogEntry.cache_key`、`prepare_equipment_icon_cache()`、`load_cached_equipment_icons()`、`EquipmentGridCell.box`、`VectorEquipmentIconRecognizer.recognize()`、`prepare_cw_equipment()`、`read_cw_equipment()`、`cw.equipment.prepare`、`cw.equipment.read` 在任务间命名一致。
- Git safety: 提交步骤均标注“如果用户要求提交”，当前计划生成不会自动提交；实现阶段不得回退用户已有未跟踪或未提交变更。
