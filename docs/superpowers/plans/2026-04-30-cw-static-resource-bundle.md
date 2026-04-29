# CW Static Resource Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CW 随版本更新的静态配置、索引、装备图标和装备识别特征前移到发布构建期，并让 `cw.equipment.read` 在 daemon 热状态下跳过运行时资源准备和二次截图。

**Architecture:** 新增 CW resource bundle generator、bundle loader/resolver、daemon resource cache、precomputed equipment recognizer 入口和 screenshot reuse hook。默认 release 运行时只读包内 bundle，不自动联网；显式 guide list/fetch 仍是动态网络入口；输出协议、YAML shape 和 session shape 保持不变。

**Tech Stack:** Python 3.12、Typer、Pillow、PyInstaller、hatchling、pytest、现有 `trail` daemon/session/output 架构。

---

## 执行约束

- 按用户要求，实际开发必须在项目内 worktree 执行，建议路径为 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-static-resource-bundle`。
- 当前主工作区已有多处非本任务修改；实现时不要回退、覆盖或提交这些文件。
- 按项目 git 安全规则，除非用户另行明确要求，执行任务时不要创建 commit；每个任务末尾用 `git status --short` 和相关 diff/test 作为 checkpoint。
- 每个实现子代理都必须拿到本计划完整路径：`C:\Users\34404\source\repos\trail-cli\docs\superpowers\plans\2026-04-30-cw-static-resource-bundle.md`，以及 spec 完整路径：`C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-30-cw-static-resource-bundle-design.md`。
- 实现必须保持 `cw.equipment.read` 默认输出协议、YAML allowlist、`state.dump` 中 `cw_state.equipment` shape 和低置信 warn 语义不变。

## File Map

- Create: `trail/scenes/cw/static_resources.py`。CW bundle schema、manifest validation、package/workspace path resolution、JSON load helpers、bundle object、resource identity。
- Create: `scripts/build-cw-resource-bundle.py`。发布构建期联网生成 bundle，下载装备图标，写 manifest，生成 features。
- Create: `trail/daemon/cw_resource_service.py`。daemon 生命周期内缓存 CW bundle、config、equipment catalog、equipment recognizer。
- Modify: `trail/scenes/cw/guide.py`。抽取 config normalization/enrichment 的 pure helpers；默认 fetch 走 bundle-first；显式 guide list/fetch 仍可联网。
- Modify: `trail/scenes/cw/equipment_resources.py`。增加 bundle manifest entry 生成/读取共用 helper；复用图标安全下载/写入逻辑。
- Modify: `trail/scenes/cw/equipment_recognition.py`。增加 feature serialization/deserialization 和 `VectorEquipmentIconRecognizer.from_precomputed_features`。
- Modify: `trail/scenes/cw/equipment.py`。让 read 使用 daemon recognizer provider；保留直接函数的 dev fallback；返回可复用 screenshot metadata。
- Modify: `trail/daemon/cw_service.py`。注入 `CwResourceService`，调整 `cw.equipment.read`、`cw.equipment.prepare`、默认 portal guide attachment 行为。
- Modify: `trail/daemon/server.py`。构造 `CwResourceService` 并传给 `CwService`。
- Modify: `trail/output/capture.py`。支持 action 返回/绑定 pre-captured screenshot path，避免 `with_selective_capture` 二次截图。
- Modify: `trail/runtime/operator.py`、`trail/runtime/window.py`。新增保存已有规范截图到 workspace 的 runtime API。
- Modify: `scripts/build-windows.ps1`、`packaging/trail.spec`、`pyproject.toml`。接入 bundle 生成与 wheel/sdist/PyInstaller 产物校验。
- Modify tests: `tests/test_cw_guide.py`、`tests/test_cw_equipment.py`、`tests/test_daemon_protocol.py`、`tests/test_cw_rpc_contracts.py`、`tests/test_output_rendering.py`、`tests/test_output_debug.py`。
- Create tests: `tests/test_cw_static_resources.py`、`tests/test_build_cw_resource_bundle.py`。
- Modify docs/skills: `skills/trail-cw-prep/SKILL.md` and `skills/trail-cw-prep/references/command-surface.md` when default portal guide summaries are removed from Agent-visible CW flow.

---

### Task 0: 准备项目内 Worktree

**Files:**
- No source changes.

- [ ] **Step 1: 确认当前主工作区状态**

Run:

```powershell
rtk git status --short
```

Expected: 可能看到本任务 spec/plan 和其他用户修改。不要回退这些修改。

- [ ] **Step 2: 创建项目内 worktree**

Run:

```powershell
rtk git worktree add ".worktrees/cw-static-resource-bundle" HEAD
```

Expected: 创建 `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-static-resource-bundle`。

- [ ] **Step 3: 在 worktree 中检查基线**

Run:

```powershell
rtk git status --short
```

Workdir: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-static-resource-bundle`

Expected: worktree clean。若不 clean，停止并报告，不要清理主工作区。

---

### Task 1: 抽取 Guide Config Pure Helpers

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Test: `tests/test_cw_guide.py`

- [ ] **Step 1: 写失败测试，证明 normalization 可在不联网时复用**

Add to `tests/test_cw_guide.py`:

```python
def test_normalize_cw_guide_config_data_matches_fetch_config_shape(tmp_path):
    from trail.scenes.cw import guide as guide_module

    raw = {
        "season_id": "s1",
        "sub_season_id": "sub1",
        "rpg_game_big_version": "3.2",
        "rpg_game_lineup_tourn_filter": "filter1",
        "label_list": [{"id": "7", "text": "7级搜牌"}],
        "trait_info_list": [{"id": "t1", "name": "贝洛伯格", "type": "faction"}],
        "role_list": [{"id": "r1", "name": "希儿", "front_back_type": 1, "trait_ids": ["t1"]}],
        "portal_list": [{"portal_id": "p1", "title": "机械城", "description": "desc"}],
        "fight_augment_list": [{"id": "a1", "name": "快攻", "desc": "说明"}],
    }

    config = guide_module.normalize_cw_guide_config_data(raw, enrich_traits=False, workspace_root=tmp_path)

    assert config["meta"] == {
        "game": "hkrpg",
        "season_id": "s1",
        "sub_season_id": "sub1",
        "big_version": "3.2",
        "lineup_filter_version": "filter1",
    }
    assert config["lineup_levels"] == [{"id": "7", "name": "7级搜牌"}]
    assert config["traits"] == [{"id": "t1", "name": "贝洛伯格", "type": "faction"}]
    assert config["roles"][0]["name"] == "希儿"
    assert config["portal_list"] == [{"portal_id": "p1", "title": "机械城", "description": "desc"}]
    assert config["strategy_list"][0]["title"] == "快攻"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
uv run pytest tests/test_cw_guide.py::test_normalize_cw_guide_config_data_matches_fetch_config_shape -q
```

Expected: FAIL because `normalize_cw_guide_config_data` does not exist.

- [ ] **Step 3: Implement pure helper**

In `trail/scenes/cw/guide.py`, extract the body currently inside `fetch_cw_guide_config` into this helper:

```python
def normalize_cw_guide_config_data(
    data: Mapping[str, object],
    *,
    timeout: int = 10,
    workspace_root: str | Path | None = None,
    enrich_traits: bool = False,
) -> dict:
    strategy_source = data.get("fight_augment_list")
    if not isinstance(strategy_source, list):
        strategy_source = data.get("strategy_list")
    config = {
        "meta": {
            "game": "hkrpg",
            "season_id": data.get("season_id"),
            "sub_season_id": data.get("sub_season_id"),
            "big_version": data.get("rpg_game_big_version"),
            "lineup_filter_version": data.get("rpg_game_lineup_tourn_filter"),
        },
        "lineup_levels": _normalize_lineup_levels(data.get("label_list")),
        "traits": _normalize_traits(data.get("trait_info_list")),
        "roles": _normalize_roles(data.get("role_list")),
        "role_tags": _normalize_role_tags(data),
        "portal_list": _normalize_portal_list(data.get("portal_list")),
        "strategy_list": _normalize_strategy_list(strategy_source),
    }
    if enrich_traits:
        config["traits"] = _enrich_cw_config_traits(
            data,
            config,
            timeout=timeout,
            workspace_root=workspace_root,
        )
    return config
```

Then update `fetch_cw_guide_config`:

```python
def fetch_cw_guide_config(
    *,
    timeout: int = 10,
    workspace_root: str | Path | None = None,
    enrich_traits: bool = False,
) -> dict:
    data = _get_cw_config_data(timeout=timeout, workspace_root=workspace_root)
    return normalize_cw_guide_config_data(
        data,
        timeout=timeout,
        workspace_root=workspace_root,
        enrich_traits=enrich_traits,
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
uv run pytest tests/test_cw_guide.py::test_normalize_cw_guide_config_data_matches_fetch_config_shape tests/test_cw_guide.py::test_fetch_cw_guide_config_returns_minimal_catalog -q
```

Expected: PASS.

- [ ] **Step 5: Checkpoint**

Run:

```powershell
rtk git diff -- trail/scenes/cw/guide.py tests/test_cw_guide.py
rtk git status --short
```

Expected: only planned files changed in implementation worktree.

---

### Task 2: Bundle Schema, Loader, And Validation

**Files:**
- Create: `trail/scenes/cw/static_resources.py`
- Test: `tests/test_cw_static_resources.py`

- [ ] **Step 1: Write validation tests**

Create `tests/test_cw_static_resources.py` with these initial tests:

```python
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from trail.core.errors import TrailError
from trail.scenes.cw.static_resources import (
    CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
    load_cw_resource_bundle_from_path,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "generated" / "3.2"
    _write_json(root / "raw_config.json", {"rpg_game_big_version": "3.2"})
    _write_json(root / "guide_config.json", {"meta": {"big_version": "3.2"}})
    _write_json(root / "guide_config_enriched.json", {"meta": {"big_version": "3.2"}, "traits": []})
    _write_json(root / "indexes.json", {"traits_by_name": {}, "roles_by_name": {}, "portals_by_title": {}, "strategies_by_title": {}, "equipment_by_cache_key": {}})
    _write_json(root / "equipment" / "manifest.json", {"items": []})
    _write_json(root / "equipment" / "features.json", {"items": [], "equipment_feature_schema_version": 1})
    files = []
    for relative in [
        "raw_config.json",
        "guide_config.json",
        "guide_config_enriched.json",
        "indexes.json",
        "equipment/manifest.json",
        "equipment/features.json",
    ]:
        path = root / relative
        files.append({"path": relative, "sha256": _sha256(path), "size": path.stat().st_size})
    _write_json(
        root / "manifest.json",
        {
            "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
            "generator_schema_version": 1,
            "resource_version": "test",
            "season_id": "s1",
            "sub_season_id": "sub1",
            "rpg_game_big_version": "3.2",
            "rpg_game_lineup_tourn_filter": "filter1",
            "files": files,
        },
    )
    return root


def test_load_cw_resource_bundle_validates_manifest_files(tmp_path):
    bundle = load_cw_resource_bundle_from_path(_bundle(tmp_path))

    assert bundle.big_version == "3.2"
    assert bundle.raw_config == {"rpg_game_big_version": "3.2"}
    assert bundle.guide_config["meta"]["big_version"] == "3.2"


def test_load_cw_resource_bundle_rejects_old_schema(tmp_path):
    root = _bundle(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_schema_version"] = 0
    _write_json(manifest_path, manifest)

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_hash_mismatch(tmp_path):
    root = _bundle(tmp_path)
    _write_json(root / "raw_config.json", {"rpg_game_big_version": "changed"})

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
uv run pytest tests/test_cw_static_resources.py -q
```

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement `static_resources.py`**

Create `trail/scenes/cw/static_resources.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from trail.core.errors import TrailError


CW_RESOURCE_BUNDLE_SCHEMA_VERSION = 1
CW_EQUIPMENT_FEATURE_SCHEMA_VERSION = 1
CW_GENERATED_RELATIVE = Path("scenes") / "cw" / "generated"


@dataclass(frozen=True)
class CwResourceBundle:
    root: Path
    manifest: dict[str, Any]
    raw_config: dict[str, Any]
    guide_config: dict[str, Any]
    guide_config_enriched: dict[str, Any]
    indexes: dict[str, Any]
    equipment_manifest: dict[str, Any]
    equipment_features: dict[str, Any]

    @property
    def big_version(self) -> str:
        return str(self.manifest.get("rpg_game_big_version") or "")

    @property
    def identity(self) -> str:
        digest = self.manifest.get("content_digest")
        if isinstance(digest, str) and digest:
            return digest
        return _manifest_digest(self.manifest)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_MISSING", f"missing cw resource bundle file: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"invalid cw resource bundle file: {path}") from exc
    if not isinstance(payload, dict):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle file must be object: {path}")
    return payload


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _manifest_digest(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _validate_manifest(root: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("bundle_schema_version") != CW_RESOURCE_BUNDLE_SCHEMA_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle schema version unsupported")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle manifest missing files")
    for item in files:
        if not isinstance(item, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle file entry invalid")
        relative = item.get("path")
        expected_hash = item.get("sha256")
        expected_size = item.get("size")
        if not isinstance(relative, str) or not isinstance(expected_hash, str) or not isinstance(expected_size, int):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle file entry incomplete")
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle file escapes root: {relative}")
        if not path.is_file():
            raise TrailError("CW_RESOURCE_BUNDLE_MISSING", f"missing cw resource bundle file: {relative}")
        if path.stat().st_size != expected_size or _file_sha256(path) != expected_hash:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw resource bundle file checksum mismatch: {relative}")


def load_cw_resource_bundle_from_path(root: str | Path) -> CwResourceBundle:
    bundle_root = Path(root)
    manifest = _read_json(bundle_root / "manifest.json")
    _validate_manifest(bundle_root, manifest)
    return CwResourceBundle(
        root=bundle_root,
        manifest=manifest,
        raw_config=_read_json(bundle_root / "raw_config.json"),
        guide_config=_read_json(bundle_root / "guide_config.json"),
        guide_config_enriched=_read_json(bundle_root / "guide_config_enriched.json"),
        indexes=_read_json(bundle_root / "indexes.json"),
        equipment_manifest=_read_json(bundle_root / "equipment" / "manifest.json"),
        equipment_features=_read_json(bundle_root / "equipment" / "features.json"),
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
uv run pytest tests/test_cw_static_resources.py -q
```

Expected: PASS.

---

### Task 3: Build-Time Bundle Generator

**Files:**
- Create: `scripts/build-cw-resource-bundle.py`
- Modify: `trail/scenes/cw/equipment_resources.py`
- Test: `tests/test_build_cw_resource_bundle.py`

- [ ] **Step 1: Write generator tests with fake network and icons**

Create `tests/test_build_cw_resource_bundle.py`:

```python
from __future__ import annotations

from io import BytesIO
import importlib.util
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-cw-resource-bundle.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("build_cw_resource_bundle", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _png_bytes(color="red"):
    buffer = BytesIO()
    Image.new("RGBA", (24, 24), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_build_cw_resource_bundle_writes_manifest_and_features(tmp_path):
    module = _load_script()
    raw_config = {
        "season_id": "s1",
        "sub_season_id": "sub1",
        "rpg_game_big_version": "3.2",
        "rpg_game_lineup_tourn_filter": "filter1",
        "trait_info_list": [{"id": "t1", "name": "贝洛伯格", "type": "faction"}],
        "role_list": [{"id": "r1", "name": "希儿", "trait_ids": ["t1"]}],
        "portal_list": [{"portal_id": "p1", "title": "机械城", "description": "desc"}],
        "fight_augment_list": [{"id": "a1", "name": "快攻", "desc": "说明"}],
        "equipment_list": [{"id": "e1", "name": "幸运星", "icon": "https://act-webstatic.mihoyo.com/e1.png"}],
    }

    result = module.build_cw_resource_bundle(
        output_root=tmp_path / "generated",
        raw_config_fetcher=lambda timeout=10: raw_config,
        icon_fetcher=lambda url, timeout, max_bytes: _png_bytes(),
        timeout=1,
    )

    bundle_root = Path(result["bundle_root"])
    assert (bundle_root / "manifest.json").is_file()
    assert (bundle_root / "raw_config.json").is_file()
    assert (bundle_root / "guide_config_enriched.json").is_file()
    assert (bundle_root / "indexes.json").is_file()
    assert (bundle_root / "equipment" / "manifest.json").is_file()
    assert (bundle_root / "equipment" / "features.json").is_file()
    manifest = json.loads((bundle_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rpg_game_big_version"] == "3.2"
    equipment = json.loads((bundle_root / "equipment" / "manifest.json").read_text(encoding="utf-8"))
    assert equipment["items"][0]["cache_key"] == "advanced-e1"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_build_cw_resource_bundle.py -q
```

Expected: FAIL because script does not exist.

- [ ] **Step 3: Add public helpers for bundle equipment manifest entries and icon IO**

In `trail/scenes/cw/equipment_resources.py`, expose public wrappers reusing existing manifest, safe name, download, and verified-write behavior:

```python
def safe_equipment_cache_segment(value: str) -> str:
    return _safe_segment(value)


def download_equipment_icon_bytes(url: str, *, timeout: float, max_bytes: int) -> bytes:
    return _fetch_icon_bytes(_download_icon, url)


def write_verified_equipment_icon(path: Path, data: bytes) -> None:
    _write_verified_icon(path, data)


def equipment_bundle_manifest_entry(entry: EquipmentCatalogEntry, *, local_path: str, sha256: str | None = None, size: int | None = None) -> dict[str, Any]:
    payload = _manifest_entry(entry, local_path=local_path)
    if sha256 is not None:
        payload["sha256"] = sha256
    if size is not None:
        payload["size"] = size
    return payload
```

- [ ] **Step 4: Implement generator script**

Create `scripts/build-cw-resource-bundle.py`. Keep it importable and callable by tests:

```python
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

from PIL import Image

from trail.scenes.cw.equipment_recognition import build_precomputed_equipment_features
from trail.scenes.cw.equipment_resources import (
    build_cw_equipment_catalog,
    download_equipment_icon_bytes,
    equipment_bundle_manifest_entry,
    safe_equipment_cache_segment,
    write_verified_equipment_icon,
)
from trail.scenes.cw.guide import _fetch_cw_config_data, normalize_cw_guide_config_data
from trail.scenes.cw.static_resources import CW_RESOURCE_BUNDLE_SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "trail" / "scenes" / "cw" / "generated"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_entry(root: Path, relative: str) -> dict:
    path = root / relative
    return {"path": relative, "sha256": sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}


def _indexes(config: dict, equipment_items: list[dict]) -> dict:
    return {
        "traits_by_name": {item["name"]: item for item in config.get("traits", []) if isinstance(item, dict) and item.get("name")},
        "traits_by_id": {str(item["id"]): item for item in config.get("traits", []) if isinstance(item, dict) and item.get("id") is not None},
        "roles_by_name": {item["name"]: item for item in config.get("roles", []) if isinstance(item, dict) and item.get("name")},
        "roles_by_id": {str(item["id"]): item for item in config.get("roles", []) if isinstance(item, dict) and item.get("id") is not None},
        "portals_by_title": {item["title"]: item for item in config.get("portal_list", []) if isinstance(item, dict) and item.get("title")},
        "portals_by_id": {str(item["portal_id"]): item for item in config.get("portal_list", []) if isinstance(item, dict) and item.get("portal_id")},
        "strategies_by_title": {item["title"]: item for item in config.get("strategy_list", []) if isinstance(item, dict) and item.get("title")},
        "equipment_by_cache_key": {item["cache_key"]: item for item in equipment_items if item.get("cache_key")},
        "equipment_by_name": {item["name"]: item for item in equipment_items if item.get("name")},
    }


def build_cw_resource_bundle(*, output_root: Path = DEFAULT_OUTPUT_ROOT, raw_config_fetcher=_fetch_cw_config_data, icon_fetcher=_fetch_icon_bytes, timeout: int = 10) -> dict:
    raw_config = raw_config_fetcher(timeout=timeout)
    big_version = str(raw_config["rpg_game_big_version"])
    bundle_root = Path(output_root) / big_version
    guide_config = normalize_cw_guide_config_data(raw_config, timeout=timeout, enrich_traits=False)
    guide_config_enriched = normalize_cw_guide_config_data(raw_config, timeout=timeout, enrich_traits=True)
    catalog = build_cw_equipment_catalog(raw_config)

    equipment_items = []
    icon_pairs = []
    for entry in catalog:
        safe_key = safe_equipment_cache_segment(entry.cache_key)
        relative = f"equipment/icons/{safe_key}.png"
        path = bundle_root / relative
        data = icon_fetcher(entry.icon_url, timeout=timeout, max_bytes=2_000_000)
        write_verified_equipment_icon(path, data)
        equipment_items.append(equipment_bundle_manifest_entry(entry, local_path=relative, sha256=sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size))
        with Image.open(path) as image:
            icon_pairs.append((entry, image.convert("RGBA")))

    _write_json(bundle_root / "raw_config.json", dict(raw_config))
    _write_json(bundle_root / "guide_config.json", guide_config)
    _write_json(bundle_root / "guide_config_enriched.json", guide_config_enriched)
    _write_json(bundle_root / "equipment" / "manifest.json", {"items": equipment_items})
    _write_json(bundle_root / "equipment" / "features.json", build_precomputed_equipment_features(icon_pairs))
    _write_json(bundle_root / "indexes.json", _indexes(guide_config_enriched, equipment_items))

    files = [_file_entry(bundle_root, relative) for relative in [
        "raw_config.json",
        "guide_config.json",
        "guide_config_enriched.json",
        "indexes.json",
        "equipment/manifest.json",
        "equipment/features.json",
    ]]
    manifest = {
        "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
        "generator_schema_version": 1,
        "resource_version": big_version,
        "season_id": raw_config.get("season_id"),
        "sub_season_id": raw_config.get("sub_season_id"),
        "rpg_game_big_version": big_version,
        "rpg_game_lineup_tourn_filter": raw_config.get("rpg_game_lineup_tourn_filter"),
        "files": files,
    }
    _write_json(bundle_root / "manifest.json", manifest)
    return {"bundle_root": str(bundle_root), "big_version": big_version, "count": len(equipment_items)}


def main() -> None:
    result = build_cw_resource_bundle()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run pytest tests/test_build_cw_resource_bundle.py tests/test_cw_static_resources.py -q
```

Expected: PASS.

---

### Task 4: Precomputed Equipment Features

**Files:**
- Modify: `trail/scenes/cw/equipment_recognition.py`
- Test: `tests/test_cw_equipment.py`

- [ ] **Step 1: Write parity test**

Add to `tests/test_cw_equipment.py`:

```python
def test_vector_recognizer_from_precomputed_features_matches_png_constructor():
    resources = load_equipment_resources_module()
    recognition = load_equipment_recognition_module()
    entry = resources.EquipmentCatalogEntry(
        cache_key="advanced-e1",
        id="e1",
        name="幸运星",
        kind="advanced",
        category=None,
        category_name=None,
        icon_url="https://act-webstatic.mihoyo.com/e1.png",
        big_version="3.2",
    )
    icon = Image.new("RGBA", (80, 80), (255, 0, 0, 255))
    query = Image.new("RGBA", (70, 70), (255, 0, 0, 255))
    expected = recognition.VectorEquipmentIconRecognizer([(entry, icon)]).recognize(query)

    payload = recognition.build_precomputed_equipment_features([(entry, icon)])
    actual = recognition.VectorEquipmentIconRecognizer.from_precomputed_features(payload).recognize(query)

    assert [candidate.cache_key for candidate in actual.candidates] == [candidate.cache_key for candidate in expected.candidates]
    assert actual.score == expected.score
    assert actual.gap == expected.gap
    assert actual.uncertain == expected.uncertain
    assert actual.empty == expected.empty
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py::test_vector_recognizer_from_precomputed_features_matches_png_constructor -q
```

Expected: FAIL because feature builder and constructor do not exist.

- [ ] **Step 3: Implement feature serialization helpers**

In `trail/scenes/cw/equipment_recognition.py`, add JSON-safe pixel encoding helpers and public API:

```python
FEATURE_SCHEMA_VERSION = 1
RECOGNIZER_ALGORITHM_VERSION = "vector-mask-v1"


def _image_payload(image: Image.Image) -> dict:
    normalized = image.convert("RGBA")
    return {
        "mode": "RGBA",
        "size": list(normalized.size),
        "data": list(normalized.tobytes()),
    }


def _image_from_payload(payload: dict) -> Image.Image:
    size = payload.get("size")
    data = payload.get("data")
    if not isinstance(size, list) or len(size) != 2 or not isinstance(data, list):
        raise ValueError("invalid equipment feature image payload")
    return Image.frombytes("RGBA", (int(size[0]), int(size[1])), bytes(int(value) for value in data))


def _mask_payload(image: Image.Image) -> dict:
    normalized = image.convert("L")
    return {
        "mode": "L",
        "size": list(normalized.size),
        "data": list(normalized.tobytes()),
    }


def _mask_from_payload(payload: dict) -> Image.Image:
    size = payload.get("size")
    data = payload.get("data")
    if not isinstance(size, list) or len(size) != 2 or not isinstance(data, list):
        raise ValueError("invalid equipment feature mask payload")
    return Image.frombytes("L", (int(size[0]), int(size[1])), bytes(int(value) for value in data))


def build_precomputed_equipment_features(icons: Iterable[tuple[EquipmentCatalogEntry, Image.Image]]) -> dict:
    items = []
    for entry, icon in icons:
        feature = _normalized_rgba(icon, FEATURE_SIZE)
        match_image = _normalized_rgba(icon, MATCH_SIZE)
        items.append(
            {
                "cache_key": entry.cache_key,
                "id": entry.id,
                "name": entry.name,
                "kind": entry.kind,
                "category": entry.category,
                "category_name": entry.category_name,
                "icon_url": entry.icon_url,
                "big_version": entry.big_version,
                "feature_rgba": _image_payload(feature),
                "match_rgba": _image_payload(match_image),
                "feature_mask": _mask_payload(_alpha_mask(icon, FEATURE_SIZE)),
                "match_mask": _mask_payload(_alpha_mask(icon, MATCH_SIZE)),
            }
        )
    return {
        "equipment_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "recognizer_algorithm_version": RECOGNIZER_ALGORITHM_VERSION,
        "feature_size": list(FEATURE_SIZE),
        "match_size": list(MATCH_SIZE),
        "min_score": DEFAULT_MIN_SCORE,
        "min_gap": DEFAULT_MIN_GAP,
        "items": items,
    }
```

- [ ] **Step 4: Implement `from_precomputed_features`**

In `VectorEquipmentIconRecognizer`:

```python
    @classmethod
    def from_precomputed_features(cls, payload: dict, *, top_k: int = DEFAULT_TOP_K) -> "VectorEquipmentIconRecognizer":
        if payload.get("equipment_feature_schema_version") != FEATURE_SCHEMA_VERSION:
            raise ValueError("unsupported equipment feature schema version")
        recognizer = cls([], top_k=top_k, min_score=float(payload.get("min_score", DEFAULT_MIN_SCORE)), min_gap=float(payload.get("min_gap", DEFAULT_MIN_GAP)))
        indexed: list[_IndexedIcon] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            entry = EquipmentCatalogEntry(
                cache_key=str(item["cache_key"]),
                id=item.get("id"),
                name=str(item["name"]),
                kind=str(item.get("kind") or "advanced"),
                category=item.get("category"),
                category_name=item.get("category_name"),
                icon_url=str(item.get("icon_url") or ""),
                big_version=str(item.get("big_version") or ""),
            )
            indexed.append(
                _IndexedIcon(
                    entry=entry,
                    feature=_image_from_payload(item["feature_rgba"]),
                    match_image=_image_from_payload(item["match_rgba"]),
                    feature_mask=_mask_from_payload(item["feature_mask"]),
                    match_mask=_mask_from_payload(item["match_mask"]),
                )
            )
        recognizer._icons = indexed
        return recognizer
```

- [ ] **Step 5: Run parity test**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py::test_vector_recognizer_from_precomputed_features_matches_png_constructor -q
```

Expected: PASS.

---

### Task 5: Build And Packaging Integration

**Files:**
- Modify: `scripts/build-windows.ps1`
- Modify: `pyproject.toml`
- Create: `tests/test_release_build.py`

- [ ] **Step 1: Add build script contract test**

Create `tests/test_release_build.py`:

```python
from pathlib import Path


def test_windows_build_generates_cw_resource_bundle_before_python_build():
    script = Path("scripts/build-windows.ps1").read_text(encoding="utf-8")
    assert "scripts\\build-cw-resource-bundle.py" in script
    assert script.index("build-cw-resource-bundle.py") < script.index("python -m build")


def test_pyproject_includes_cw_generated_resources():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "trail/scenes/cw/generated" in pyproject
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
uv run pytest tests/test_release_build.py -q
```

Expected: FAIL.

- [ ] **Step 3: Update Windows build script**

In `scripts/build-windows.ps1`, insert after third-party notices generation:

```powershell
  uv run python scripts\build-cw-resource-bundle.py
```

Then after PyInstaller, add a lightweight check:

```powershell
  if (-not (Test-Path (Join-Path $Root 'trail\scenes\cw\generated'))) {
    throw 'missing generated cw resource bundle'
  }
  if (-not (Get-ChildItem (Join-Path $Root 'trail\scenes\cw\generated') -Recurse -Filter 'manifest.json')) {
    throw 'missing generated cw resource manifest'
  }
```

- [ ] **Step 4: Update wheel package data**

In `pyproject.toml`, add explicit hatch force include rules:

```toml
[tool.hatch.build.targets.wheel.force-include]
"trail/scenes/cw/generated" = "trail/scenes/cw/generated"
```

Keep this explicit rule even if hatch currently includes package data automatically, because generated JSON/PNG/features are release-critical.

- [ ] **Step 5: Run focused contract tests**

Run:

```powershell
uv run pytest tests/test_release_build.py -q
```

Expected: PASS.

---

### Task 6: Bundle-First Guide Config And Default No-Network

**Files:**
- Modify: `trail/scenes/cw/static_resources.py`
- Modify: `trail/scenes/cw/guide.py`
- Test: `tests/test_cw_guide.py`

- [ ] **Step 1: Write no-network guide config tests**

Add to `tests/test_cw_guide.py`:

```python
def test_fetch_cw_guide_config_uses_resource_bundle_without_network(monkeypatch, tmp_path):
    from trail.scenes.cw import guide as guide_module

    bundle = {
        "raw_config": {"rpg_game_big_version": "3.2"},
        "guide_config": {"meta": {"big_version": "3.2"}, "traits": []},
        "guide_config_enriched": {"meta": {"big_version": "3.2"}, "traits": [{"id": "t1", "name": "贝洛伯格", "layers": [2, 4, 6]}]},
    }

    class Bundle:
        raw_config = bundle["raw_config"]
        guide_config = bundle["guide_config"]
        guide_config_enriched = bundle["guide_config_enriched"]

    monkeypatch.setattr(guide_module, "load_default_cw_resource_bundle", lambda workspace_root=None: Bundle())
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_config_data",
        lambda timeout=10: (_ for _ in ()).throw(AssertionError("network must not be called")),
    )

    assert guide_module.fetch_cw_raw_guide_config(workspace_root=tmp_path) == {"rpg_game_big_version": "3.2"}
    assert guide_module.fetch_cw_guide_config(workspace_root=tmp_path)["meta"]["big_version"] == "3.2"
    assert guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)["traits"][0]["layers"] == [2, 4, 6]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_cw_guide.py::test_fetch_cw_guide_config_uses_resource_bundle_without_network -q
```

Expected: FAIL because loader hook is absent.

- [ ] **Step 3: Add default bundle loader hook**

In `trail/scenes/cw/static_resources.py`, add package loader and explicit dev fallback gate:

```python
def allow_cw_resource_dev_fallback() -> bool:
    return not bool(getattr(sys, "frozen", False))


def load_default_cw_resource_bundle(*, workspace_root: str | Path | None = None) -> CwResourceBundle:
    del workspace_root
    package_root = Path(__file__).resolve().parents[2]
    generated_root = package_root / "scenes" / "cw" / "generated"
    if not generated_root.is_dir():
        raise TrailError("CW_RESOURCE_BUNDLE_MISSING", "cw resource bundle missing")
    candidates = sorted(path for path in generated_root.iterdir() if path.is_dir())
    if not candidates:
        raise TrailError("CW_RESOURCE_BUNDLE_MISSING", "cw resource bundle missing")
    return load_cw_resource_bundle_from_path(candidates[-1])
```

- [ ] **Step 4: Switch guide defaults to bundle-first with dev fallback**

In `trail/scenes/cw/guide.py`, import `load_default_cw_resource_bundle` and update fetch functions:

```python
def fetch_cw_raw_guide_config(*, timeout: int = 10, workspace_root: str | Path | None = None) -> dict:
    try:
        return dict(load_default_cw_resource_bundle(workspace_root=workspace_root).raw_config)
    except TrailError as error:
        if error.code not in {"CW_RESOURCE_BUNDLE_MISSING", "CW_RESOURCE_BUNDLE_INVALID"}:
            raise
    return _get_cw_config_data(timeout=timeout, workspace_root=workspace_root)


def fetch_cw_guide_config(
    *,
    timeout: int = 10,
    workspace_root: str | Path | None = None,
    enrich_traits: bool = False,
) -> dict:
    try:
        bundle = load_default_cw_resource_bundle(workspace_root=workspace_root)
        return dict(bundle.guide_config_enriched if enrich_traits else bundle.guide_config)
    except TrailError as error:
        if error.code not in {"CW_RESOURCE_BUNDLE_MISSING", "CW_RESOURCE_BUNDLE_INVALID"}:
            raise
    data = _get_cw_config_data(timeout=timeout, workspace_root=workspace_root)
    return normalize_cw_guide_config_data(
        data,
        timeout=timeout,
        workspace_root=workspace_root,
        enrich_traits=enrich_traits,
    )
```

Use `allow_cw_resource_dev_fallback()` in `guide.py`: when bundle loading fails and fallback is not allowed, re-raise the bundle error; when fallback is allowed, use existing `_get_cw_config_data` behavior for source checkout tests.

- [ ] **Step 5: Run guide tests**

Run:

```powershell
uv run pytest tests/test_cw_guide.py::test_fetch_cw_guide_config_uses_resource_bundle_without_network tests/test_cw_guide.py::test_fetch_cw_guide_config_returns_minimal_catalog -q
```

Expected: PASS.

---

### Task 7: Stop Default Portal Guide Attachment Networking

**Files:**
- Modify: `trail/daemon/cw_service.py`
- Test: `tests/test_daemon_protocol.py` or `tests/test_cw_portal.py`

- [ ] **Step 1: Write no-network portal attachment test**

Add this test to `tests/test_daemon_protocol.py` near the existing `cw.portal.detect` tests:

```python
def test_cw_portal_detect_does_not_fetch_guide_list_by_default(monkeypatch, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    class Runtime:
        def capture_after_action(self, optional=False, request_id=None):
            del optional, request_id
            return tmp_path / ".trail" / "shots" / "portal.png"
        def collect_warnings(self):
            return []
        def match_references(self, screenshot_path, limit=3):
            del screenshot_path, limit
            return []

    runtime = Runtime()
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime))

    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr(
        cw_service_module,
        "fetch_cw_guide_list",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("guide list must not be called")),
    )
    monkeypatch.setattr(cw_service_module, "detect_cw_portal", lambda session, runtime, portal_list: {"cards": [{"idx": 1, "portal_title": "机械城"}], "stale": False})

    result = service.handle_with_capture(
        method="cw.portal.detect",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="r1",
    )

    assert result["ok"] is True
    assert "guides" not in result["data"]["cards"][0]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_portal_detect_does_not_fetch_guide_list_by_default -q
```

Expected: FAIL because `_attach_guides_to_cards` currently calls `fetch_cw_guide_list`.

- [ ] **Step 3: Make guide attachment cache-only by default**

In `trail/daemon/cw_service.py`, change `_attach_guides_to_cards` default behavior:

```python
def _attach_guides_to_cards(cards: list[dict[str, object]], *, timeout: int = 10, workspace_root: str | None = None, allow_network: bool = False) -> list[dict[str, object]]:
    if not allow_network:
        return cards
    # existing fetch_cw_guide_list logic remains below for future explicit opt-in
```

Ensure callers do not pass `allow_network=True` in default `cw.start` / `cw.portal.*` paths.

- [ ] **Step 4: Run portal/daemon focused tests**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py -q
```

Expected: PASS.

Expected: PASS or update expected portal summaries to omit network guides by default.

---

### Task 8: Daemon CwResourceService And Equipment Read Hot Path

**Files:**
- Create: `trail/daemon/cw_resource_service.py`
- Modify: `trail/daemon/server.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/scenes/cw/equipment.py`
- Test: `tests/test_daemon_protocol.py`, `tests/test_cw_equipment.py`

- [ ] **Step 1: Write service cache test**

Add to `tests/test_daemon_protocol.py`:

```python
def test_cw_resource_service_caches_equipment_recognizer(monkeypatch, tmp_path):
    from trail.daemon.cw_resource_service import CwResourceService

    calls = {"load": 0, "recognizer": 0}

    class Bundle:
        big_version = "3.2"
        identity = "id1"
        equipment_features = {"items": [], "equipment_feature_schema_version": 1, "min_score": 0.72, "min_gap": 0.05}
        equipment_manifest = {"items": []}
        root = tmp_path

    def load_bundle(workspace_root=None):
        calls["load"] += 1
        return Bundle()

    class Recognizer:
        @classmethod
        def from_precomputed_features(cls, payload):
            calls["recognizer"] += 1
            return cls()

    service = CwResourceService(bundle_loader=load_bundle, recognizer_cls=Recognizer)

    first = service.equipment_recognizer(workspace_root=str(tmp_path))
    second = service.equipment_recognizer(workspace_root=str(tmp_path))

    assert first is second
    assert calls["recognizer"] == 1
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_resource_service_caches_equipment_recognizer -q
```

Expected: FAIL because service does not exist.

- [ ] **Step 3: Implement `CwResourceService`**

Create `trail/daemon/cw_resource_service.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from trail.scenes.cw.equipment_recognition import VectorEquipmentIconRecognizer
from trail.scenes.cw.static_resources import load_default_cw_resource_bundle


class CwResourceService:
    def __init__(self, *, bundle_loader=load_default_cw_resource_bundle, recognizer_cls=VectorEquipmentIconRecognizer):
        self._bundle_loader = bundle_loader
        self._recognizer_cls = recognizer_cls
        self._bundles: dict[tuple[str, str], Any] = {}
        self._recognizers: dict[tuple[str, str], Any] = {}

    def bundle(self, *, workspace_root: str):
        bundle = self._bundle_loader(workspace_root=workspace_root)
        key = (str(Path(workspace_root).resolve()), bundle.identity)
        cached = self._bundles.get(key)
        if cached is None:
            self._bundles[key] = bundle
            cached = bundle
        return cached

    def equipment_recognizer(self, *, workspace_root: str):
        bundle = self.bundle(workspace_root=workspace_root)
        key = (str(Path(workspace_root).resolve()), bundle.identity)
        cached = self._recognizers.get(key)
        if cached is None:
            cached = self._recognizer_cls.from_precomputed_features(bundle.equipment_features)
            self._recognizers[key] = cached
        return cached

    def invalidate_workspace(self, *, workspace_root: str) -> None:
        prefix = str(Path(workspace_root).resolve())
        self._bundles = {key: value for key, value in self._bundles.items() if key[0] != prefix}
        self._recognizers = {key: value for key, value in self._recognizers.items() if key[0] != prefix}
```

- [ ] **Step 4: Inject service into daemon**

In `trail/daemon/server.py`, import and wire:

```python
from trail.daemon.cw_resource_service import CwResourceService
```

Where `CwService` is created, pass `cw_resource_service=CwResourceService()`.

In `trail/daemon/cw_service.py`, update constructor:

```python
class CwService:
    def __init__(self, *, runtime_service, cw_resource_service=None):
        self.runtime_service = runtime_service
        self.cw_resource_service = cw_resource_service
```

- [ ] **Step 5: Let equipment read accept recognizer**

In `trail/scenes/cw/equipment.py`, add optional recognizer parameter:

```python
def read_cw_equipment(runtime, *, workspace_root: str | Path | None = None, recognizer=None) -> dict[str, Any]:
    if recognizer is None:
        raw_config = fetch_cw_raw_guide_config(workspace_root=workspace_root)
        catalog = build_cw_equipment_catalog(raw_config)
        prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=False)
        recognizer = VectorEquipmentIconRecognizer(load_cached_equipment_icons(catalog, workspace_root=workspace_root))
    image = _runtime_image(runtime)
    ...
```

Then update `apply_cw_equipment_read` to accept recognizer.

- [ ] **Step 6: Use service in `cw.equipment.read` handler**

In `trail/daemon/cw_service.py`, change handler:

```python
"cw.equipment.read": lambda: apply_cw_equipment_read(
    session,
    runtime(),
    workspace_root=workspace_root,
    recognizer=(self.cw_resource_service.equipment_recognizer(workspace_root=workspace_root) if self.cw_resource_service is not None else None),
),
```

For `cw.equipment.prepare`, after refresh call:

```python
if self.cw_resource_service is not None and bool(payload.get("refresh")):
    self.cw_resource_service.invalidate_workspace(workspace_root=workspace_root)
```

- [ ] **Step 7: Run focused tests**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_resource_service_caches_equipment_recognizer tests/test_cw_equipment.py -q
```

Expected: PASS.

---

### Task 9: Screenshot Reuse For `cw.equipment.read`

**Files:**
- Modify: `trail/runtime/window.py`
- Modify: `trail/runtime/operator.py`
- Modify: `trail/output/capture.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/scenes/cw/equipment.py`
- Test: `tests/test_daemon_protocol.py`, `tests/test_output_rendering.py`

- [ ] **Step 1: Write capture reuse test**

Add to `tests/test_daemon_protocol.py` near existing `cw.equipment.read` tests:

```python
def test_cw_equipment_read_reuses_recognition_screenshot(monkeypatch, tmp_path):
    from PIL import Image
    from trail.daemon.cw_service import CwService

    class Runtime:
        def __init__(self):
            self.capture_after_action_calls = 0
        def capture_image(self, **kwargs):
            return Image.new("RGBA", (1920, 1080), "black")
        def save_capture_image_to_workspace(self, image, request_id=None):
            path = tmp_path / f"{request_id}.jpg"
            image.convert("RGB").save(path)
            return path
        def capture_after_action(self, optional=False, request_id=None):
            self.capture_after_action_calls += 1
            return tmp_path / "unexpected.jpg"

    runtime = Runtime()
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime))

    monkeypatch.setattr(
        "trail.scenes.cw.equipment.read_cw_equipment",
        lambda runtime, workspace_root=None, recognizer=None, request_id=None: {
            "count": 0,
            "uncertain": 0,
            "empty": 60,
            "items": [],
            "backend": "vector",
            "layout": "default",
            "columns": 10,
            "rows": 6,
            "stale": False,
            "_screenshot": str(tmp_path / "r1.jpg"),
        },
    )

    response = cw_service.handle_with_capture(
        method="cw.equipment.read",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="r1",
    )

    assert response["ok"] is True
    assert response["screenshot"].endswith("r1.jpg")
    assert runtime.capture_after_action_calls == 0
```

- [ ] **Step 2: Run test to verify failure**

Run the new test with `uv run pytest ... -q`.

Expected: FAIL because `with_selective_capture` still calls `capture_after_action`.

- [ ] **Step 3: Add save-existing-image API**

In `trail/runtime/window.py`:

```python
    def save_capture_image_to_workspace(self, image, request_id: str | None = None) -> Path:
        path = self.workspace / f"{_safe_capture_request_id(request_id)}.jpg"
        image.convert("RGB").save(path, format="JPEG", quality=90, optimize=True)
        return path
```

In `trail/runtime/operator.py`:

```python
    def save_capture_image_to_workspace(self, image, request_id: str | None = None):
        save = getattr(self.window, "save_capture_image_to_workspace", None)
        if not callable(save):
            raise TrailError("SCREENSHOT_FAILED", "window controller does not support saving captured image")
        return save(image, request_id=request_id)
```

- [ ] **Step 4: Return screenshot metadata from equipment read**

In `trail/scenes/cw/equipment.py`, after `_runtime_image(runtime)` succeeds, save it when possible:

```python
def _save_reused_screenshot(runtime, image: Image.Image, *, request_id: str | None = None) -> str | None:
    save = getattr(runtime, "save_capture_image_to_workspace", None)
    if not callable(save):
        return None
    path = save(image, request_id=request_id)
    return str(path) if path is not None else None
```

Pass `request_id` through `apply_cw_equipment_read` from daemon handler. Add to snapshot under private key `"_screenshot": path`; `with_selective_capture` consumes and removes that key before building the envelope, so it never appears in `data` or YAML.

- [ ] **Step 5: Teach capture wrapper to consume pre-captured screenshot**

In `trail/output/capture.py`, add helper:

```python
def _pop_precaptured_screenshot(data):
    if isinstance(data, dict):
        screenshot = data.pop("_screenshot", None)
        return data, screenshot
    return data, None
```

In `with_selective_capture`, after `data = fn()`:

```python
        data, screenshot = _pop_precaptured_screenshot(data)
        if screenshot is None:
            screenshot = _capture_screenshot(resolved_runtime, optional=False)
```

Keep `with_auto_capture` unchanged unless a mutation path also needs reuse later.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_equipment_read_reuses_recognition_screenshot tests/test_output_rendering.py::test_render_output_cw_equipment_read_includes_screenshot_guidance -q
```

Expected: PASS. If the rendering test currently has a different name, rename the existing test to `test_render_output_cw_equipment_read_includes_screenshot_guidance` in the same task so this command stays exact.

---

### Task 10: Output, YAML, Docs, And No-Network Contract Coverage

**Files:**
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_output_debug.py`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`

- [ ] **Step 1: Add default no-network tests for `cw.equipment.read`**

In `tests/test_cw_equipment.py`, add monkeypatch assertions that default daemon-injected recognizer path does not call network/cache helpers:

```python
def test_read_cw_equipment_with_injected_recognizer_does_not_prepare_or_load_icons(monkeypatch, tmp_path):
    scene = load_equipment_scene_module()
    recognition = load_equipment_recognition_module()

    class Runtime:
        def capture_image(self, **kwargs):
            return Image.new("RGBA", (1920, 1080), "black")

    class Recognizer:
        def recognize(self, image):
            return recognition.EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)

    monkeypatch.setattr(scene, "prepare_equipment_icon_cache", lambda *a, **k: (_ for _ in ()).throw(AssertionError("prepare must not be called")))
    monkeypatch.setattr(scene, "load_cached_equipment_icons", lambda *a, **k: (_ for _ in ()).throw(AssertionError("load icons must not be called")))

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path, recognizer=Recognizer())

    assert result["count"] == 0
```

- [ ] **Step 2: Add YAML/state shape assertions**

Ensure `tests/test_cw_rpc_contracts.py` keeps `cw.equipment.read --format yaml` expected shape with `row/col/candidates` and default text without `idx/row/col/box`.

- [ ] **Step 3: Add low confidence warn regression**

Ensure `tests/test_output_rendering.py` has or gets a test asserting:

```python
assert "warn code=LOW_CONFIDENCE count=1" in output
```

for `cw.equipment.read` payload with `uncertain=1`.

- [ ] **Step 4: Update skill docs for changed portal guide summary behavior**

Update `skills/trail-cw-prep/SKILL.md` and `skills/trail-cw-prep/references/command-surface.md` to state that `cw.start` / `cw.portal.*` default output does not fetch dynamic guide summaries; Agents should use the already selected guide or explicit `guide.list.cw` / `guide.fetch.cw` when dynamic攻略信息 is needed. Do not update root `README.md` in this task.

- [ ] **Step 5: Run contract tests**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_output_debug.py -q
```

Expected: PASS.

---

### Task 11: Full Verification And Review

**Files:**
- No new source unless failures require fixes.

- [ ] **Step 1: Run focused CW suite**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py tests/test_cw_guide.py tests/test_daemon_protocol.py tests/test_cw_rpc_contracts.py -q
```

Expected: PASS.

- [ ] **Step 2: Run output protocol suite**

Run:

```powershell
uv run pytest tests/test_output_rendering.py tests/test_output_debug.py tests/test_skill_routing_contracts.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full test suite if focused suites pass**

Run:

```powershell
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 4: Build smoke check**

Run:

```powershell
uv run python scripts\build-cw-resource-bundle.py
uv run python -m build
```

Expected: both commands exit 0 and generated bundle files are included in wheel contents.

- [ ] **Step 5: Request review with subagents**

Dispatch 3-5 read-only review subagents with the full plan path and spec path. Each prompt must be over 2000 characters for fresh subagents. Ask them to review implementation diff, output protocol, no-network guarantees, and performance path. Do not proceed until all pass or feedback is addressed.

- [ ] **Step 6: Final checkpoint**

Run:

```powershell
rtk git status --short
rtk git diff --check
```

Expected: no whitespace errors. Report modified files and verification evidence. Do not commit unless user explicitly requests a commit.
