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
- Create: `scripts/verify-cw-resource-bundle-artifacts.py`。发布构建后检查 wheel、sdist、PyInstaller dist 都包含 generated 资源。
- Create: `trail/daemon/cw_resource_service.py`。daemon 生命周期内缓存 CW bundle、config、equipment catalog、equipment recognizer。
- Modify: `trail/scenes/cw/guide.py`。抽取 config normalization/enrichment 的 pure helpers；默认 fetch 走 bundle-first；显式 guide list/fetch 仍可联网。
- Modify: `trail/scenes/cw/equipment_resources.py`。增加 bundle manifest entry 生成/读取共用 helper；复用图标安全下载/写入逻辑。
- Modify: `trail/scenes/cw/equipment_recognition.py`。增加 feature serialization/deserialization 和 `VectorEquipmentIconRecognizer.from_precomputed_features`。
- Modify: `trail/scenes/cw/equipment.py`。让 read 使用 daemon recognizer provider；保留直接函数的 dev fallback；返回可复用 screenshot metadata。
- Modify: `trail/daemon/cw_service.py`。注入 `CwResourceService`，调整 `cw.equipment.read`、`cw.equipment.prepare`、默认 portal guide attachment 行为。
- Modify: `trail/daemon/server.py`。构造 `CwResourceService` 并传给 `CwService`。
- Modify: `trail/output/capture.py`。支持 action 返回/绑定 pre-captured screenshot path，避免 `with_selective_capture` 二次截图。
- Modify: `trail/runtime/operator.py`、`trail/runtime/window.py`。新增保存已有规范截图到 workspace 的 runtime API。
- Modify: `scripts/build-windows.ps1`、`pyproject.toml`。Inspect: `packaging/trail.spec` must keep `collect_data_files("trail")`; PyInstaller coverage is enforced by artifact verification instead of a spec-file edit.
- Modify tests: `tests/test_cw_guide.py`、`tests/test_cw_equipment.py`、`tests/test_daemon_protocol.py`、`tests/test_cw_rpc_contracts.py`、`tests/test_atomic_commands.py`、`tests/test_output_rendering.py`、`tests/test_output_debug.py`。
- Create tests: `tests/test_cw_static_resources.py`、`tests/test_build_cw_resource_bundle.py`。
- Modify docs/skills: `AGENTS.md`, `skills/trail-cw-prep/SKILL.md`, `skills/trail-cw-prep/references/command-surface.md`, `skills/trail-cw-portal/SKILL.md`, `skills/trail-cw-portal/references/portal-selection-rules.md`, `skills/trail-cw-portal/references/portal-refresh-policy.md`, `skills/trail-cw-guide/SKILL.md`, `skills/trail-cw-guide/references/confirmation-checklist.md`, and `skills/trail-cw-guide/references/guide-selection-criteria.md` when default portal guide summaries or equipment prepare/read runtime semantics change.

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

Also add a public build-time fetch wrapper so release scripts do not import a private helper:

```python
def download_cw_guide_config_data(*, timeout: int = 10) -> dict:
    return _fetch_cw_config_data(timeout=timeout)
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
    _write_json(root / "equipment" / "features.json", {"items": [], "equipment_feature_schema_version": 1, "recognizer_algorithm_version": "vector-mask-v1", "feature_size": [32, 32], "match_size": [64, 64], "min_score": 0.72, "min_gap": 0.05})
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

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
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


def write_bundle_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def bundle_file_entry(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    return {"path": relative, "sha256": _file_sha256(path), "size": path.stat().st_size}


def write_bundle_manifest(root: Path, *, source_manifest: dict[str, Any], source_kind: str = "package") -> dict[str, Any]:
    file_relatives = [
        "raw_config.json",
        "guide_config.json",
        "guide_config_enriched.json",
        "indexes.json",
        "equipment/manifest.json",
        "equipment/features.json",
    ]
    equipment_manifest = _read_json(root / "equipment" / "manifest.json")
    for item in equipment_manifest.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("local_path"), str):
            file_relatives.append(str(item["local_path"]))
    manifest = {
        **source_manifest,
        "source_kind": source_kind,
        "files": [bundle_file_entry(root, relative) for relative in file_relatives],
    }
    manifest["content_digest"] = _manifest_digest({key: value for key, value in manifest.items() if key != "content_digest"})
    write_bundle_json(root / "manifest.json", manifest)
    return manifest


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
    expected_digest = manifest.get("content_digest")
    if isinstance(expected_digest, str) and expected_digest:
        actual_digest = _manifest_digest({key: value for key, value in manifest.items() if key != "content_digest"})
        if expected_digest != actual_digest:
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw resource bundle manifest digest mismatch")


def _validate_equipment_features(payload: dict[str, Any]) -> None:
    if payload.get("equipment_feature_schema_version") != CW_EQUIPMENT_FEATURE_SCHEMA_VERSION:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment feature schema version unsupported")
    if payload.get("recognizer_algorithm_version") != "vector-mask-v1":
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment recognizer algorithm unsupported")
    if payload.get("feature_size") != [32, 32] or payload.get("match_size") != [64, 64]:
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment feature sizes unsupported")
    if not isinstance(payload.get("min_score"), (int, float)) or not isinstance(payload.get("min_gap"), (int, float)):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment feature thresholds missing")
    items = payload.get("items")
    if not isinstance(items, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment features missing items")
    for item in items:
        if not isinstance(item, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment feature item invalid")
        for key in ("cache_key", "name", "feature_rgba", "match_rgba", "feature_mask", "match_mask"):
            if key not in item:
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment feature item missing {key}")


def _validate_equipment_manifest(root: Path, payload: dict[str, Any]) -> None:
    items = payload.get("items")
    if not isinstance(items, list):
        raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment manifest missing items")
    for item in items:
        if not isinstance(item, dict):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", "cw equipment manifest item invalid")
        for key in ("cache_key", "name", "kind", "icon_url", "big_version", "local_path", "sha256", "size"):
            if key not in item:
                raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment manifest item missing {key}")
        icon_path = (root / str(item["local_path"])).resolve()
        if not icon_path.is_relative_to(root.resolve()) or not icon_path.is_file():
            raise TrailError("CW_RESOURCE_BUNDLE_MISSING", f"missing cw equipment icon: {item['local_path']}")
        if icon_path.stat().st_size != int(item["size"]) or _file_sha256(icon_path) != str(item["sha256"]):
            raise TrailError("CW_RESOURCE_BUNDLE_INVALID", f"cw equipment icon checksum mismatch: {item['local_path']}")


def load_cw_resource_bundle_from_path(root: str | Path) -> CwResourceBundle:
    bundle_root = Path(root)
    manifest = _read_json(bundle_root / "manifest.json")
    _validate_manifest(bundle_root, manifest)
    equipment_manifest = _read_json(bundle_root / "equipment" / "manifest.json")
    equipment_features = _read_json(bundle_root / "equipment" / "features.json")
    _validate_equipment_manifest(bundle_root, equipment_manifest)
    _validate_equipment_features(equipment_features)
    return CwResourceBundle(
        root=bundle_root,
        manifest=manifest,
        raw_config=_read_json(bundle_root / "raw_config.json"),
        guide_config=_read_json(bundle_root / "guide_config.json"),
        guide_config_enriched=_read_json(bundle_root / "guide_config_enriched.json"),
        indexes=_read_json(bundle_root / "indexes.json"),
        equipment_manifest=equipment_manifest,
        equipment_features=equipment_features,
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


def _load_static_resources():
    from trail.scenes.cw.static_resources import load_cw_resource_bundle_from_path

    return load_cw_resource_bundle_from_path


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
        config_normalizer=lambda data, timeout=10, workspace_root=None, enrich_traits=False: {
            "meta": {"big_version": data["rpg_game_big_version"]},
            "traits": [{"id": "t1", "name": "贝洛伯格", "layers": [2, 4, 6]}] if enrich_traits else [{"id": "t1", "name": "贝洛伯格"}],
            "roles": [{"id": "r1", "name": "希儿"}],
            "portal_list": [{"portal_id": "p1", "title": "机械城", "description": "desc"}],
            "strategy_list": [{"id": "a1", "title": "快攻"}],
        },
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
    assert equipment["items"][0]["big_version"] == "3.2"
    loaded = _load_static_resources()(bundle_root)
    assert loaded.equipment_manifest["items"][0]["big_version"] == "3.2"
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
    try:
        return _download_icon(url, timeout=timeout, max_bytes=max_bytes)
    except TrailError:
        raise
    except Exception as exc:
        raise TrailError("CW_EQUIPMENT_ICON_DOWNLOAD_FAILED", f"failed to download equipment icon: {url}") from exc


def write_verified_equipment_icon(path: Path, data: bytes) -> None:
    _write_verified_icon(path, data)


def equipment_bundle_manifest_entry(entry: EquipmentCatalogEntry, *, local_path: str, sha256: str | None = None, size: int | None = None) -> dict[str, Any]:
    payload = _manifest_entry(entry, local_path=local_path)
    payload["big_version"] = entry.big_version
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

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from PIL import Image

from trail.core.errors import TrailError
from trail.scenes.cw.equipment_recognition import build_precomputed_equipment_features
from trail.scenes.cw.equipment_resources import (
    build_cw_equipment_catalog,
    download_equipment_icon_bytes,
    equipment_bundle_manifest_entry,
    safe_equipment_cache_segment,
    write_verified_equipment_icon,
)
from trail.scenes.cw.guide import download_cw_guide_config_data, normalize_cw_guide_config_data
from trail.scenes.cw.static_resources import CW_RESOURCE_BUNDLE_SCHEMA_VERSION, bundle_file_entry, write_bundle_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "trail" / "scenes" / "cw" / "generated"


def _indexes(config: dict, equipment_items: list[dict]) -> dict:
    role_lookup_candidates = []
    for item in config.get("roles", []):
        if isinstance(item, dict) and item.get("name"):
            role_lookup_candidates.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "normalized_name": "".join(str(item.get("name") or "").split()).lower(),
                    "front_back_type": item.get("front_back_type"),
                    "trait_ids": list(item.get("trait_ids") or []),
                }
            )
    return {
        "traits_by_name": {item["name"]: item for item in config.get("traits", []) if isinstance(item, dict) and item.get("name")},
        "traits_by_id": {str(item["id"]): item for item in config.get("traits", []) if isinstance(item, dict) and item.get("id") is not None},
        "roles_by_name": {item["name"]: item for item in config.get("roles", []) if isinstance(item, dict) and item.get("name")},
        "roles_by_id": {str(item["id"]): item for item in config.get("roles", []) if isinstance(item, dict) and item.get("id") is not None},
        "portals_by_title": {item["title"]: item for item in config.get("portal_list", []) if isinstance(item, dict) and item.get("title")},
        "portals_by_id": {str(item["portal_id"]): item for item in config.get("portal_list", []) if isinstance(item, dict) and item.get("portal_id")},
        "strategies_by_title": {item["title"]: item for item in config.get("strategy_list", []) if isinstance(item, dict) and item.get("title")},
        "role_lookup_candidates": role_lookup_candidates,
        "equipment_by_cache_key": {item["cache_key"]: item for item in equipment_items if item.get("cache_key")},
        "equipment_by_name": {item["name"]: item for item in equipment_items if item.get("name")},
    }


def build_cw_resource_bundle(*, output_root: Path = DEFAULT_OUTPUT_ROOT, raw_config_fetcher=download_cw_guide_config_data, config_normalizer=normalize_cw_guide_config_data, icon_fetcher=download_equipment_icon_bytes, timeout: int = 10) -> dict:
    raw_config = raw_config_fetcher(timeout=timeout)
    big_version = str(raw_config["rpg_game_big_version"])
    bundle_root = Path(output_root) / big_version
    with TemporaryDirectory(prefix="trail-cw-resource-build-") as cache_dir:
        guide_config = config_normalizer(raw_config, timeout=timeout, workspace_root=cache_dir, enrich_traits=False)
        guide_config_enriched = config_normalizer(raw_config, timeout=timeout, workspace_root=cache_dir, enrich_traits=True)
    if not any(isinstance(item, dict) and item.get("layers") for item in guide_config_enriched.get("traits", [])):
        raise RuntimeError("cw enriched traits missing layer data")
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

    write_bundle_json(bundle_root / "raw_config.json", dict(raw_config))
    write_bundle_json(bundle_root / "guide_config.json", guide_config)
    write_bundle_json(bundle_root / "guide_config_enriched.json", guide_config_enriched)
    write_bundle_json(bundle_root / "equipment" / "manifest.json", {"items": equipment_items})
    write_bundle_json(bundle_root / "equipment" / "features.json", build_precomputed_equipment_features(icon_pairs))
    write_bundle_json(bundle_root / "indexes.json", _indexes(guide_config_enriched, equipment_items))

    file_relatives = [
        "raw_config.json",
        "guide_config.json",
        "guide_config_enriched.json",
        "indexes.json",
        "equipment/manifest.json",
        "equipment/features.json",
    ] + [item["local_path"] for item in equipment_items]
    files = [bundle_file_entry(bundle_root, relative) for relative in file_relatives]
    manifest = {
        "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
        "generator_schema_version": 1,
        "resource_version": big_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season_id": raw_config.get("season_id"),
        "sub_season_id": raw_config.get("sub_season_id"),
        "rpg_game_big_version": big_version,
        "rpg_game_lineup_tourn_filter": raw_config.get("rpg_game_lineup_tourn_filter"),
        "files": files,
    }
    manifest["content_digest"] = sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    write_bundle_json(bundle_root / "manifest.json", manifest)
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
    alt_entry = resources.EquipmentCatalogEntry(
        cache_key="advanced-e2",
        id="e2",
        name="光能电池",
        kind="advanced",
        category=None,
        category_name=None,
        icon_url="https://act-webstatic.mihoyo.com/e2.png",
        big_version="3.2",
    )
    icon = Image.new("RGBA", (80, 80), (255, 0, 0, 255))
    alt_icon = Image.new("RGBA", (80, 80), (245, 0, 0, 255))
    query = Image.new("RGBA", (70, 70), (250, 0, 0, 255))
    expected = recognition.VectorEquipmentIconRecognizer([(entry, icon), (alt_entry, alt_icon)]).recognize(query)

    payload = recognition.build_precomputed_equipment_features([(entry, icon), (alt_entry, alt_icon)])
    actual = recognition.VectorEquipmentIconRecognizer.from_precomputed_features(payload).recognize(query)

    assert payload["equipment_feature_schema_version"] == recognition.FEATURE_SCHEMA_VERSION
    assert payload["recognizer_algorithm_version"] == recognition.RECOGNIZER_ALGORITHM_VERSION
    assert payload["feature_size"] == list(recognition.FEATURE_SIZE)
    assert payload["match_size"] == list(recognition.MATCH_SIZE)
    assert payload["min_score"] == recognition.DEFAULT_MIN_SCORE
    assert payload["min_gap"] == recognition.DEFAULT_MIN_GAP
    assert all("feature_rgba" in item and "match_rgba" in item and "feature_mask" in item and "match_mask" in item for item in payload["items"])
    assert [(candidate.cache_key, candidate.score) for candidate in actual.candidates] == [(candidate.cache_key, candidate.score) for candidate in expected.candidates]
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
- Create: `scripts/verify-cw-resource-bundle-artifacts.py`
- Create: `tests/test_release_build.py`

- [ ] **Step 1: Add build script contract test**

Create `tests/test_release_build.py`:

```python
from pathlib import Path


def test_windows_build_generates_cw_resource_bundle_before_python_build():
    script = Path("scripts/build-windows.ps1").read_text(encoding="utf-8")
    assert "scripts\\build-cw-resource-bundle.py" in script
    assert "scripts\\verify-cw-resource-bundle-artifacts.py" in script
    assert script.index("build-cw-resource-bundle.py") < script.index("python -m build")
    assert script.index("verify-cw-resource-bundle-artifacts.py") > script.index("pyinstaller")


def test_pyproject_includes_cw_generated_resources():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "trail/scenes/cw/generated" in pyproject


def test_verify_cw_resource_bundle_artifacts_script_exists():
    script = Path("scripts/verify-cw-resource-bundle-artifacts.py").read_text(encoding="utf-8")
    assert "zipfile" in script
    assert "tarfile" in script
    assert "hashlib.sha256" in script
    assert "manifest.get(\"files\")" in script
    assert "equipment/features.json" in script
    assert "equipment/icons/" in script
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
uv run pytest tests/test_release_build.py -q
```

Expected: FAIL because `scripts/build-windows.ps1` does not call the CW bundle builder/verifier, `pyproject.toml` does not include `trail/scenes/cw/generated`, and `scripts/verify-cw-resource-bundle-artifacts.py` does not exist.

- [ ] **Step 3: Update Windows build script**

In `scripts/build-windows.ps1`, insert after third-party notices generation:

```powershell
  uv run python scripts\build-cw-resource-bundle.py
```

Then after PyInstaller, call artifact verification:

```powershell
  uv run python scripts\verify-cw-resource-bundle-artifacts.py dist
```

- [ ] **Step 4: Update wheel package data**

In `pyproject.toml`, add explicit hatch force include rules:

```toml
[tool.hatch.build.targets.sdist.force-include]
"trail/scenes/cw/generated" = "trail/scenes/cw/generated"

[tool.hatch.build.targets.wheel.force-include]
"trail/scenes/cw/generated" = "trail/scenes/cw/generated"
```

Keep this explicit rule even if hatch currently includes package data automatically, because generated JSON/PNG/features are release-critical.

- [ ] **Step 5: Add artifact verification script**

Create `scripts/verify-cw-resource-bundle-artifacts.py`:

```python
from __future__ import annotations

import sys
import tarfile
import zipfile
import hashlib
import json
from pathlib import Path


REQUIRED_RELATIVES = (
    "manifest.json",
    "raw_config.json",
    "guide_config.json",
    "guide_config_enriched.json",
    "indexes.json",
    "equipment/manifest.json",
    "equipment/features.json",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name.replace("\\", "/"): archive.read(name) for name in archive.namelist() if not name.endswith("/")}


def _tar_members(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as archive:
        result: dict[str, bytes] = {}
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                result[member.name.replace("\\", "/")] = extracted.read()
        return result


def _tree_members(path: Path) -> dict[str, bytes]:
    return {item.relative_to(path).as_posix(): item.read_bytes() for item in path.rglob("*") if item.is_file()}


def _load_members(path: Path) -> dict[str, bytes]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        return _zip_members(path)
    if path.name.endswith(".tar.gz"):
        return _tar_members(path)
    return _tree_members(path)


def _bundle_prefixes(members: dict[str, bytes]) -> list[str]:
    prefixes: list[str] = []
    for name in members:
        parts = name.split("/")
        for index in range(len(parts) - 4):
            if parts[index:index + 4] == ["trail", "scenes", "cw", "generated"] and len(parts) > index + 5:
                prefix = "/".join(parts[: index + 5])
                if f"{prefix}/manifest.json" == name:
                    prefixes.append(prefix)
    return sorted(set(prefixes))


def _verify_bundle_members(target: Path, members: dict[str, bytes]) -> None:
    prefixes = _bundle_prefixes(members)
    if len(prefixes) != 1:
        raise SystemExit(f"cw resource bundle root manifest missing or ambiguous in artifact: {target}")
    prefix = prefixes[0]
    missing = [relative for relative in REQUIRED_RELATIVES if f"{prefix}/{relative}" not in members]
    if missing:
        raise SystemExit(f"cw resource bundle missing files in {target}: {missing}")
    manifest = json.loads(members[f"{prefix}/manifest.json"].decode("utf-8"))
    files = manifest.get("files")
    if not isinstance(files, list):
        raise SystemExit(f"cw resource bundle manifest missing files[] in {target}")
    for item in files:
        if not isinstance(item, dict):
            raise SystemExit(f"cw resource bundle manifest has invalid file entry in {target}")
        relative = item.get("path")
        expected_hash = item.get("sha256")
        expected_size = item.get("size")
        if not isinstance(relative, str) or not isinstance(expected_hash, str) or not isinstance(expected_size, int):
            raise SystemExit(f"cw resource bundle manifest file entry incomplete in {target}")
        data = members.get(f"{prefix}/{relative}")
        if data is None:
            raise SystemExit(f"cw resource bundle manifest references missing file in {target}: {relative}")
        if len(data) != expected_size or _sha256(data) != expected_hash:
            raise SystemExit(f"cw resource bundle checksum mismatch in {target}: {relative}")
    if not any(str(item.get("path", "")).startswith("equipment/icons/") for item in files if isinstance(item, dict)):
        raise SystemExit(f"cw resource bundle manifest missing equipment icons in {target}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dist = Path(args[0] if args else "dist")
    targets = [*dist.glob("*.whl"), *dist.glob("*.tar.gz")]
    pyinstaller_dir = dist / "trail"
    if pyinstaller_dir.exists():
        targets.append(pyinstaller_dir)
    if not targets:
        raise SystemExit("no build artifacts found")
    for target in targets:
        _verify_bundle_members(target, _load_members(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run focused contract tests**

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


def test_fetch_cw_guide_config_bundle_missing_does_not_network_without_dev_fallback(monkeypatch, tmp_path):
    from trail.core.errors import TrailError
    from trail.scenes.cw import guide as guide_module

    monkeypatch.setattr(guide_module, "allow_cw_resource_dev_fallback", lambda: False)
    monkeypatch.setattr(
        guide_module,
        "load_default_cw_resource_bundle",
        lambda workspace_root=None: (_ for _ in ()).throw(TrailError("CW_RESOURCE_BUNDLE_MISSING", "missing")),
    )
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_config_data",
        lambda timeout=10: (_ for _ in ()).throw(AssertionError("network must not be called")),
    )

    with pytest.raises(TrailError) as exc_info:
        guide_module.fetch_cw_guide_config(workspace_root=tmp_path)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_MISSING"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_cw_guide.py::test_fetch_cw_guide_config_uses_resource_bundle_without_network tests/test_cw_guide.py::test_fetch_cw_guide_config_bundle_missing_does_not_network_without_dev_fallback -q
```

Expected: FAIL because loader hook is absent.

- [ ] **Step 3: Add default bundle loader hook**

In `trail/scenes/cw/static_resources.py`, add package loader and explicit dev fallback gate:

```python
def allow_cw_resource_dev_fallback() -> bool:
    return os.environ.get("TRAIL_CW_RESOURCE_DEV_FALLBACK") == "1"


def load_default_cw_resource_bundle(*, workspace_root: str | Path | None = None) -> CwResourceBundle:
    del workspace_root
    package_root = Path(__file__).resolve().parents[2]
    generated_root = package_root / "scenes" / "cw" / "generated"
    if not generated_root.is_dir():
        raise TrailError("CW_RESOURCE_BUNDLE_MISSING", "cw resource bundle missing")
    candidates = [path for path in generated_root.iterdir() if path.is_dir()]
    if len(candidates) != 1:
        raise TrailError("CW_RESOURCE_BUNDLE_MISSING", "cw resource bundle missing")
    return load_cw_resource_bundle_from_path(candidates[0])
```

- [ ] **Step 4: Switch guide defaults to bundle-first with dev fallback**

In `trail/scenes/cw/guide.py`, import `allow_cw_resource_dev_fallback` and `load_default_cw_resource_bundle`, then update fetch functions:

```python
def fetch_cw_raw_guide_config(*, timeout: int = 10, workspace_root: str | Path | None = None) -> dict:
    try:
        return dict(load_default_cw_resource_bundle(workspace_root=workspace_root).raw_config)
    except TrailError as error:
        if error.code not in {"CW_RESOURCE_BUNDLE_MISSING", "CW_RESOURCE_BUNDLE_INVALID"}:
            raise
        if not allow_cw_resource_dev_fallback():
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
        if not allow_cw_resource_dev_fallback():
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

- [ ] **Step 5: Move existing guide network/cache tests behind explicit dev fallback**

In `tests/test_cw_guide.py`, add `monkeypatch.setenv("TRAIL_CW_RESOURCE_DEV_FALLBACK", "1")` at the start of existing tests that intentionally exercise `_fetch_cw_config_data`, workspace config cache, or guide-list enrichment behavior. Update these tests first:

```python
test_fetch_cw_guide_config_returns_minimal_catalog
test_fetch_cw_guide_config_includes_strategy_list_without_changing_existing_shape
test_fetch_cw_guide_config_writes_and_reuses_workspace_cache
test_fetch_cw_guide_config_enriches_trait_layers_from_raw_lineup_list
test_fetch_cw_guide_config_default_does_not_request_guide_list
test_fetch_cw_guide_config_enriched_cache_hit_avoids_guide_list
test_fetch_cw_guide_config_enrichment_failure_without_cache_returns_base_traits
test_fetch_cw_guide_config_writes_complete_cache_with_missing_trait_ids
test_fetch_cw_guide_config_enrichment_reads_beyond_thirty_pages
test_fetch_cw_guide_config_enrichment_stops_on_repeated_next_page_token
test_fetch_cw_guide_config_enrichment_merges_name_only_raw_trait_with_base_id
test_fetch_cw_guide_config_enrichment_keeps_result_when_cache_write_fails
test_fetch_cw_raw_guide_config_returns_cached_raw_data
```

Do not set the environment variable in the new no-network tests from Step 1; those tests prove release default behavior.

- [ ] **Step 6: Run guide tests**

Run:

```powershell
uv run pytest tests/test_cw_guide.py::test_fetch_cw_guide_config_uses_resource_bundle_without_network tests/test_cw_guide.py::test_fetch_cw_guide_config_bundle_missing_does_not_network_without_dev_fallback tests/test_cw_guide.py::test_fetch_cw_guide_config_returns_minimal_catalog -q
```

Expected: PASS.

---

### Task 6A: Consume Bundle Config And Indexes In Static Filter Paths

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Modify: `trail/daemon/cw_service.py`
- Test: `tests/test_cw_guide.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: Add guide list filter no-config-network test**

Add to `tests/test_cw_guide.py`:

```python
def test_fetch_cw_guide_list_filter_resolution_uses_bundle_config_without_config_network(monkeypatch, tmp_path):
    from trail.scenes.cw import guide as guide_module

    raw_config = {
        "rpg_game_big_version": "3.2",
        "trait_info_list": [{"id": "101", "name": "贝洛伯格", "type": "faction"}],
        "role_list": [{"id": "201", "name": "希儿", "trait_ids": ["101"]}],
        "portal_list": [{"portal_id": "301", "title": "机械城", "description": "desc"}],
    }

    class Bundle:
        raw_config = raw_config
        guide_config = {"meta": {"big_version": "3.2"}}
        guide_config_enriched = {"meta": {"big_version": "3.2"}}
        indexes = {
            "traits_by_name": {"贝洛伯格": {"id": "101", "name": "贝洛伯格"}},
            "roles_by_name": {"希儿": {"id": "201", "name": "希儿"}},
            "portals_by_title": {"机械城": {"portal_id": "301", "title": "机械城", "description": "desc"}},
        }

    captured = {}
    monkeypatch.setattr(guide_module, "load_default_cw_resource_bundle", lambda workspace_root=None: Bundle())
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda timeout=10: (_ for _ in ()).throw(AssertionError("config network must not be called")))
    monkeypatch.setattr(
        guide_module,
        "_fetch_cw_guide_list_data",
        lambda **kwargs: captured.setdefault("kwargs", kwargs) or {"list": [], "next_page_token": None},
    )

    guide_module.fetch_cw_guide_list(
        page=1,
        limit=3,
        trait="贝洛伯格",
        role="希儿",
        portal="机械城",
        workspace_root=tmp_path,
    )

    assert captured["kwargs"]["trait_id"] == 101
    assert captured["kwargs"]["role_ids"] == ["201"]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_cw_guide.py::test_fetch_cw_guide_list_filter_resolution_uses_bundle_config_without_config_network -q
```

Expected: FAIL because `fetch_cw_guide_list` still calls `_get_cw_config_data` for filter config.

- [ ] **Step 3: Switch static filter config to bundle-first raw config**

In `trail/scenes/cw/guide.py`, replace this block inside `fetch_cw_guide_list`:

```python
if needs_config:
    raw_config = _get_cw_config_data(timeout=timeout, workspace_root=workspace_root)
```

with:

```python
if needs_config:
    raw_config = fetch_cw_raw_guide_config(timeout=timeout, workspace_root=workspace_root)
```

This keeps guide list itself dynamic while moving static filter resolution to bundle-first config.

- [ ] **Step 4: Update existing guide list filter tests for bundle-first config**

In `tests/test_cw_guide.py`, update existing guide list trait/role/portal name-filter tests so they monkeypatch `load_default_cw_resource_bundle` or `fetch_cw_raw_guide_config` instead of `_get_cw_config_data`. Keep tests that intentionally exercise old config cache under `TRAIL_CW_RESOURCE_DEV_FALLBACK=1` from Task 6 Step 5; guide list filter tests should prove release-default static filter resolution does not fetch config network.

- [ ] **Step 5: Keep portal/strategy/slots/shop bundle-first through existing config fetchers**

Run:

```powershell
rg "_get_cw_config_data" trail/daemon/cw_service.py
```

Expected: no matches. `trail/daemon/cw_service.py` must continue to call `fetch_cw_guide_config` for portal list, strategy list, slots, shop, and buy-exp, because that helper is the bundle-first boundary.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
uv run pytest tests/test_cw_guide.py::test_fetch_cw_guide_list_filter_resolution_uses_bundle_config_without_config_network -q
```

Expected: PASS.

---

### Task 6B: Workspace Equipment Override Resolver And Resource Identity

**Files:**
- Modify: `trail/scenes/cw/static_resources.py`
- Test: `tests/test_cw_static_resources.py`

- [ ] **Step 1: Add equipment override overlay test**

Add to `tests/test_cw_static_resources.py` using the `_bundle(tmp_path)` helper from Task 2:

```python
def test_load_default_cw_resource_bundle_overlays_workspace_equipment_without_replacing_config(monkeypatch, tmp_path):
    from trail.scenes.cw import static_resources

    package_root = _bundle(tmp_path / "package")
    base_identity = static_resources.load_cw_resource_bundle_from_path(package_root).identity
    override_root = tmp_path / "workspace" / ".trail" / "cache" / "cw-equipment-resource"
    _write_json(override_root / "equipment" / "manifest.json", {"items": []})
    _write_json(
        override_root / "equipment" / "features.json",
        {"items": [], "equipment_feature_schema_version": 1, "recognizer_algorithm_version": "vector-mask-v1", "feature_size": [32, 32], "match_size": [64, 64], "min_score": 0.33, "min_gap": 0.05},
    )
    files = []
    for relative in ["equipment/manifest.json", "equipment/features.json"]:
        path = override_root / relative
        files.append({"path": relative, "sha256": _sha256(path), "size": path.stat().st_size})
    _write_json(override_root / "manifest.json", {"bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION, "base_content_digest": base_identity, "files": files})

    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    bundle = static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert bundle.raw_config == {"rpg_game_big_version": "3.2"}
    assert bundle.equipment_features["min_score"] == 0.33
    assert bundle.source_kind == "package+workspace_equipment"


def test_load_default_cw_resource_bundle_ignores_stale_workspace_equipment_override(monkeypatch, tmp_path):
    from trail.scenes.cw import static_resources

    package_root = _bundle(tmp_path / "package")
    override_root = tmp_path / "workspace" / ".trail" / "cache" / "cw-equipment-resource"
    _write_json(override_root / "equipment" / "manifest.json", {"items": []})
    _write_json(override_root / "equipment" / "features.json", {"items": [], "equipment_feature_schema_version": 1, "recognizer_algorithm_version": "vector-mask-v1", "feature_size": [32, 32], "match_size": [64, 64], "min_score": 0.33, "min_gap": 0.05})
    files = []
    for relative in ["equipment/manifest.json", "equipment/features.json"]:
        path = override_root / relative
        files.append({"path": relative, "sha256": _sha256(path), "size": path.stat().st_size})
    _write_json(override_root / "manifest.json", {"bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION, "base_content_digest": "old-package", "files": files})
    monkeypatch.setattr(static_resources, "_package_bundle_candidates", lambda: [package_root])

    bundle = static_resources.load_default_cw_resource_bundle(workspace_root=tmp_path / "workspace")

    assert bundle.source_kind == "package"
    assert bundle.equipment_features["min_score"] == 0.72
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_cw_static_resources.py::test_load_default_cw_resource_bundle_overlays_workspace_equipment_without_replacing_config tests/test_cw_static_resources.py::test_load_default_cw_resource_bundle_ignores_stale_workspace_equipment_override -q
```

Expected: FAIL because workspace equipment override resolution is absent.

- [ ] **Step 3: Implement equipment-override-aware resolver**

In `trail/scenes/cw/static_resources.py`, extend `CwResourceBundle` with identity fields:

```python
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
    source_kind: str = "package"
    manifest_path: Path | None = None
    manifest_mtime: float = 0.0
    equipment_manifest_path: Path | None = None
    equipment_manifest_mtime: float = 0.0
    override_manifest_path: Path | None = None
    override_manifest_mtime: float = 0.0
    override_identity: str = ""
```

Add resolver helpers:

```python
CW_EQUIPMENT_OVERRIDE_RELATIVE = Path(".trail") / "cache" / "cw-equipment-resource"


def _workspace_equipment_override_root(workspace_root: str | Path | None) -> Path | None:
    if workspace_root is None:
        return None
    root = Path(workspace_root) / CW_EQUIPMENT_OVERRIDE_RELATIVE
    return root if (root / "manifest.json").is_file() else None


def cw_resource_source_signature(*, workspace_root: str | Path | None = None) -> tuple:
    override = _workspace_equipment_override_root(workspace_root)
    override_sig = None
    if override is not None:
        manifest_path = override / "manifest.json"
        manifest = _read_json(manifest_path)
        stat = manifest_path.stat()
        digest = manifest.get("content_digest")
        if not isinstance(digest, str) or not digest:
            digest = _manifest_digest({key: value for key, value in manifest.items() if key != "content_digest"})
        override_sig = (str(manifest_path), stat.st_mtime_ns, stat.st_size, digest)
    package_sig = tuple(
        (str(path / "manifest.json"), (path / "manifest.json").stat().st_mtime_ns, (path / "manifest.json").stat().st_size)
        for path in _package_bundle_candidates()
    )
    return (package_sig, override_sig)


def _package_bundle_candidates() -> list[Path]:
    package_root = Path(__file__).resolve().parents[2]
    generated_root = package_root / "scenes" / "cw" / "generated"
    if not generated_root.is_dir():
        return []
    return [path for path in generated_root.iterdir() if path.is_dir() and (path / "manifest.json").is_file()]


def load_cw_resource_bundle_from_path(root: str | Path, *, source_kind: str = "package") -> CwResourceBundle:
    bundle_root = Path(root)
    manifest_path = bundle_root / "manifest.json"
    manifest = _read_json(manifest_path)
    _validate_manifest(bundle_root, manifest)
    equipment_manifest = _read_json(bundle_root / "equipment" / "manifest.json")
    equipment_features = _read_json(bundle_root / "equipment" / "features.json")
    _validate_equipment_manifest(bundle_root, equipment_manifest)
    _validate_equipment_features(equipment_features)
    return CwResourceBundle(
        root=bundle_root,
        manifest=manifest,
        raw_config=_read_json(bundle_root / "raw_config.json"),
        guide_config=_read_json(bundle_root / "guide_config.json"),
        guide_config_enriched=_read_json(bundle_root / "guide_config_enriched.json"),
        indexes=_read_json(bundle_root / "indexes.json"),
        equipment_manifest=equipment_manifest,
        equipment_features=equipment_features,
        source_kind=source_kind,
        manifest_path=manifest_path,
        manifest_mtime=manifest_path.stat().st_mtime,
        equipment_manifest_path=bundle_root / "equipment" / "manifest.json",
        equipment_manifest_mtime=(bundle_root / "equipment" / "manifest.json").stat().st_mtime,
    )


def _overlay_workspace_equipment(bundle: CwResourceBundle, override_root: Path) -> CwResourceBundle:
    override_manifest_path = override_root / "manifest.json"
    override_manifest = _read_json(override_manifest_path)
    if override_manifest.get("base_content_digest") != bundle.identity:
        return bundle
    _validate_manifest(override_root, override_manifest)
    equipment_manifest = _read_json(override_root / "equipment" / "manifest.json")
    equipment_features = _read_json(override_root / "equipment" / "features.json")
    _validate_equipment_manifest(override_root, equipment_manifest)
    _validate_equipment_features(equipment_features)
    equipment_manifest_path = override_root / "equipment" / "manifest.json"
    override_identity = str(override_manifest.get("content_digest") or _manifest_digest({key: value for key, value in override_manifest.items() if key != "content_digest"}))
    return replace(
        bundle,
        source_kind="package+workspace_equipment",
        manifest={**bundle.manifest, "equipment_override_digest": override_identity},
        equipment_manifest=equipment_manifest,
        equipment_features=equipment_features,
        equipment_manifest_path=equipment_manifest_path,
        equipment_manifest_mtime=equipment_manifest_path.stat().st_mtime,
        override_manifest_path=override_manifest_path,
        override_manifest_mtime=override_manifest_path.stat().st_mtime,
        override_identity=override_identity,
    )


def load_default_cw_resource_bundle(*, workspace_root: str | Path | None = None) -> CwResourceBundle:
    candidates = _package_bundle_candidates()
    if len(candidates) != 1:
        raise TrailError("CW_RESOURCE_BUNDLE_MISSING", "cw resource bundle missing")
    bundle = load_cw_resource_bundle_from_path(candidates[0], source_kind="package")
    override = _workspace_equipment_override_root(workspace_root)
    return _overlay_workspace_equipment(bundle, override) if override is not None else bundle
```

Import `replace` from `dataclasses`. Remove the earlier duplicate `load_default_cw_resource_bundle` snippet from Task 6 when implementing this task; the final code should have one loader. Workspace equipment override must never replace `raw_config`, `guide_config`, `guide_config_enriched`, or `indexes`; it only overlays equipment manifest/features/icons for explicit `cw.equipment.prepare --refresh`.

- [ ] **Step 4: Run resource tests**

Run:

```powershell
uv run pytest tests/test_cw_static_resources.py -q
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
    from trail.scenes.cw.models import ensure_cw_state
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


@pytest.mark.parametrize("method", ["cw.start", "cw.portal.refresh", "cw.portal.restart"])
def test_cw_portal_family_does_not_fetch_guide_list_by_default(monkeypatch, tmp_path, method):
    from types import SimpleNamespace
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry
    from trail.scenes.cw.models import ensure_cw_state

    class Runtime:
        def ocr(self):
            return []
        def capture_after_action(self, optional=False, request_id=None):
            return tmp_path / ".trail" / "shots" / "portal.png"

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    ensure_cw_state(session)["entry"] = {"mode": "continue", "difficulty": "current", "battle_mode": "standard"}
    service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: Runtime()))

    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_config", lambda **kwargs: {"portal_list": []})
    monkeypatch.setattr(cw_service_module, "fetch_cw_guide_list", lambda **kwargs: (_ for _ in ()).throw(AssertionError("guide list must not be called")))
    monkeypatch.setattr(cw_service_module, "start_cw", lambda session, **kwargs: session)
    monkeypatch.setattr(cw_service_module, "summarize_portal_cards", lambda *args, **kwargs: [{"idx": 1, "portal_title": "机械城"}])
    monkeypatch.setattr(cw_service_module, "detect_portal_collection_matches", lambda runtime: [])
    monkeypatch.setattr(cw_service_module, "refresh_cw_portal", lambda *args, **kwargs: {"cards": [{"idx": 1, "portal_title": "机械城"}], "stale": False})
    monkeypatch.setattr(cw_service_module, "select_cw_portal", lambda *args, **kwargs: None)
    monkeypatch.setattr(cw_service_module, "wait_cw_portal_in_game", lambda *args, **kwargs: None)
    monkeypatch.setattr(cw_service_module, "restart_cw_portal_to_settlement_entry", lambda *args, **kwargs: None)

    payload = {"session_id": session.session_id}
    if method == "cw.start":
        payload.update({"mode": "continue", "difficulty": "current", "battle_mode": "standard"})
    result = service.handle_with_capture(method=method, payload=payload, workspace_root=str(tmp_path), session_service=session_service, request_id="r1")

    assert result["ok"] is True
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_portal_detect_does_not_fetch_guide_list_by_default tests/test_daemon_protocol.py::test_cw_portal_family_does_not_fetch_guide_list_by_default -q
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
        equipment_features = {"items": [], "equipment_feature_schema_version": 1, "recognizer_algorithm_version": "vector-mask-v1", "feature_size": [32, 32], "match_size": [64, 64], "min_score": 0.72, "min_gap": 0.05}
        equipment_manifest = {"items": []}
        root = tmp_path
        source_kind = "package"
        manifest = {"bundle_schema_version": 1, "resource_version": "3.2"}
        manifest_path = tmp_path / "manifest.json"
        manifest_mtime = 1.0

    def load_bundle(workspace_root=None):
        calls["load"] += 1
        return Bundle()

    class Recognizer:
        @classmethod
        def from_precomputed_features(cls, payload):
            calls["recognizer"] += 1
            return cls()

    service = CwResourceService(bundle_loader=load_bundle, recognizer_cls=Recognizer, source_signature=lambda workspace_root=None: ("test",))

    first = service.equipment_recognizer(workspace_root=str(tmp_path))
    second = service.equipment_recognizer(workspace_root=str(tmp_path))

    assert first is second
    assert calls["load"] == 1
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

import os
import shutil
from pathlib import Path
from threading import RLock
from typing import Any

from trail.scenes.cw.equipment_recognition import VectorEquipmentIconRecognizer
from trail.scenes.cw.static_resources import cw_resource_source_signature, load_default_cw_resource_bundle


class CwResourceService:
    def __init__(self, *, bundle_loader=load_default_cw_resource_bundle, recognizer_cls=VectorEquipmentIconRecognizer, source_signature=cw_resource_source_signature):
        self._bundle_loader = bundle_loader
        self._recognizer_cls = recognizer_cls
        self._source_signature = source_signature
        self._bundle_by_workspace: dict[str, Any] = {}
        self._source_signature_by_workspace: dict[str, tuple] = {}
        self._bundles: dict[tuple, Any] = {}
        self._recognizers: dict[tuple, Any] = {}
        self._lock = RLock()

    def _workspace_key(self, *, workspace_root: str) -> str:
        return str(Path(workspace_root).resolve())

    def _bundle_key(self, *, workspace_root: str, bundle) -> tuple:
        return (
            self._workspace_key(workspace_root=workspace_root),
            bundle.source_kind,
            str(bundle.manifest_path),
            str(getattr(bundle, "equipment_manifest_path", "")),
            bundle.manifest.get("bundle_schema_version"),
            bundle.manifest.get("resource_version"),
            bundle.big_version,
            bundle.manifest_mtime,
            getattr(bundle, "equipment_manifest_mtime", 0.0),
            str(getattr(bundle, "override_manifest_path", "")),
            getattr(bundle, "override_manifest_mtime", 0.0),
            getattr(bundle, "override_identity", ""),
            bundle.identity,
        )

    def bundle(self, *, workspace_root: str):
        workspace_key = self._workspace_key(workspace_root=workspace_root)
        with self._lock:
            source_signature = self._source_signature(workspace_root=workspace_root)
            cached = self._bundle_by_workspace.get(workspace_key)
            if cached is not None and self._source_signature_by_workspace.get(workspace_key) == source_signature:
                return cached
            bundle = self._bundle_loader(workspace_root=workspace_root)
            key = self._bundle_key(workspace_root=workspace_root, bundle=bundle)
            cached = self._bundles.get(key)
            if cached is None:
                self._bundles[key] = bundle
                cached = bundle
            self._bundle_by_workspace[workspace_key] = cached
            self._source_signature_by_workspace[workspace_key] = source_signature
            return cached

    def equipment_recognizer_for_bundle(self, *, workspace_root: str, bundle):
        key = self._bundle_key(workspace_root=workspace_root, bundle=bundle)
        with self._lock:
            cached = self._recognizers.get(key)
            if cached is None:
                cached = self._recognizer_cls.from_precomputed_features(bundle.equipment_features)
                self._recognizers[key] = cached
            return cached

    def equipment_read_resources(self, *, workspace_root: str) -> tuple[dict, Any]:
        bundle = self.bundle(workspace_root=workspace_root)
        return bundle.raw_config, self.equipment_recognizer_for_bundle(workspace_root=workspace_root, bundle=bundle)

    def equipment_recognizer(self, *, workspace_root: str):
        bundle = self.bundle(workspace_root=workspace_root)
        return self.equipment_recognizer_for_bundle(workspace_root=workspace_root, bundle=bundle)

    def invalidate_workspace(self, *, workspace_root: str) -> None:
        prefix = str(Path(workspace_root).resolve())
        with self._lock:
            self._bundle_by_workspace.pop(prefix, None)
            self._source_signature_by_workspace.pop(prefix, None)
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
def read_cw_equipment(
    runtime,
    *,
    workspace_root: str | Path | None = None,
    raw_config: dict[str, Any] | None = None,
    recognizer=None,
) -> dict[str, Any]:
    resolved_raw_config = raw_config
    if recognizer is None:
        if resolved_raw_config is None:
            resolved_raw_config = fetch_cw_raw_guide_config(workspace_root=workspace_root)
        catalog = build_cw_equipment_catalog(resolved_raw_config)
        prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=False)
        recognizer = VectorEquipmentIconRecognizer(load_cached_equipment_icons(catalog, workspace_root=workspace_root))
    image = _runtime_image(runtime)
    cells = list(iter_equipment_grid_cells(DEFAULT_EQUIPMENT_GRID_PROFILE, columns=10, rows=6))
    best_by_idx: dict[int, dict[str, Any]] = {}

    for crop in crop_equipment_cells(image, cells):
        if not crop_has_equipment_slot_markers(crop.image):
            continue
        item = _item_from_result(crop, recognizer.recognize(crop.image))
        if item is None:
            continue
        previous = best_by_idx.get(crop.cell.idx)
        item_score = item.get("score")
        previous_score = None if previous is None else previous.get("score")
        if previous is None or float(item_score if item_score is not None else -1.0) > float(previous_score if previous_score is not None else -1.0):
            best_by_idx[crop.cell.idx] = item

    items = _filter_isolated_equipment_items([best_by_idx[idx] for idx in sorted(best_by_idx)])
    return {
        "count": len(items),
        "uncertain": sum(1 for item in items if item.get("uncertain")),
        "empty": len(cells) - len(items),
        "items": items,
        "backend": "vector",
        "layout": "default",
        "columns": 10,
        "rows": 6,
        "stale": False,
    }
```

Then update `apply_cw_equipment_read` to accept recognizer while preserving recommendation inputs:

```python
def apply_cw_equipment_read(
    session: SessionModel,
    runtime,
    *,
    workspace_root: str | Path | None = None,
    raw_config: dict[str, Any] | None = None,
    recognizer=None,
) -> dict[str, Any]:
    resolved_raw_config = raw_config if raw_config is not None else fetch_cw_raw_guide_config(workspace_root=workspace_root)
    snapshot = read_cw_equipment(runtime, workspace_root=workspace_root, raw_config=resolved_raw_config, recognizer=recognizer)
    recommendations = build_cw_equipment_recommendations(session, snapshot=snapshot, raw_config=resolved_raw_config)
    if recommendations is not None:
        snapshot["recommendations"] = recommendations
    ensure_cw_state(session)["equipment"] = deepcopy(snapshot)
    return snapshot
```

- [ ] **Step 6: Use service in `cw.equipment.read` handler**

In `trail/daemon/cw_service.py`, add a local handler so the daemon path uses the cached bundle raw config and recognizer together:

```python
def run_equipment_read() -> dict:
    if self.cw_resource_service is None:
        return apply_cw_equipment_read(session, runtime(), workspace_root=workspace_root)
    raw_config, recognizer = self.cw_resource_service.equipment_read_resources(workspace_root=workspace_root)
    return apply_cw_equipment_read(
        session,
        runtime(),
        workspace_root=workspace_root,
        raw_config=raw_config,
        recognizer=recognizer,
    )
```

Then set:

```python
"cw.equipment.read": run_equipment_read,
```

`cw.equipment.prepare --refresh` is routed in Task 8A so that it writes a workspace equipment override before invalidating the cache.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_resource_service_caches_equipment_recognizer tests/test_cw_equipment.py -q
```

Expected: PASS.

---

### Task 8A: `cw.equipment.prepare --refresh` Writes Workspace Equipment Features

**Files:**
- Modify: `trail/daemon/cw_resource_service.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/scenes/cw/static_resources.py`
- Test: `tests/test_daemon_protocol.py`

- [ ] **Step 1: Add refresh override test**

Add to `tests/test_daemon_protocol.py`:

```python
def test_cw_equipment_prepare_refresh_invalidates_and_switches_to_workspace_override(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    calls = []

    class ResourceService:
        def refresh_equipment_workspace_override(self, *, workspace_root):
            calls.append(("refresh", workspace_root))
            return {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": True}

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CwService(runtime_service=SimpleNamespace(), cw_resource_service=ResourceService())

    result = service.handle(
        method="cw.equipment.prepare",
        payload={"session_id": session.session_id, "refresh": True},
        workspace_root=str(tmp_path),
        session_service=session_service,
    )

    assert result == {"big_version": "3.2", "count": 1, "cached": 0, "downloaded": 1, "refreshed": True}
    assert calls == [("refresh", str(tmp_path))]


def test_cw_equipment_prepare_default_uses_bundle_summary_without_downloading(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    class ResourceService:
        def equipment_prepare_summary(self, *, workspace_root):
            return {"big_version": "3.2", "count": 2, "cached": 2, "downloaded": 0, "refreshed": False}

    monkeypatch.setattr(
        cw_service_module,
        "prepare_cw_equipment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("default prepare must not download or prepare cache")),
    )

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CwService(runtime_service=SimpleNamespace(), cw_resource_service=ResourceService())

    result = service.handle(
        method="cw.equipment.prepare",
        payload={"session_id": session.session_id, "refresh": False},
        workspace_root=str(tmp_path),
        session_service=session_service,
    )

    assert result == {"big_version": "3.2", "count": 2, "cached": 2, "downloaded": 0, "refreshed": False}


def test_cw_equipment_prepare_default_without_resource_service_uses_bundle_summary(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from trail.daemon import cw_service as cw_service_module
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    class Bundle:
        big_version = "3.2"
        equipment_manifest = {"items": [{"name": "幸运星"}]}

    monkeypatch.setattr(cw_service_module, "load_default_cw_resource_bundle", lambda workspace_root=None: Bundle())
    monkeypatch.setattr(
        cw_service_module,
        "prepare_cw_equipment",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("default prepare must not download or prepare cache")),
    )

    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    service = CwService(runtime_service=SimpleNamespace(), cw_resource_service=None)

    result = service.handle(
        method="cw.equipment.prepare",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
    )

    assert result == {"big_version": "3.2", "count": 1, "cached": 1, "downloaded": 0, "refreshed": False}
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_equipment_prepare_refresh_invalidates_and_switches_to_workspace_override tests/test_daemon_protocol.py::test_cw_equipment_prepare_default_uses_bundle_summary_without_downloading tests/test_daemon_protocol.py::test_cw_equipment_prepare_default_without_resource_service_uses_bundle_summary -q
```

Expected: FAIL because refresh still calls `prepare_cw_equipment` directly.

- [ ] **Step 3: Add resource service refresh method**

In `trail/daemon/cw_resource_service.py`, add method behavior, using existing bundle config and equipment manifest as source of truth:

Import `build_precomputed_equipment_features`, `prepare_equipment_icon_cache`, `load_cached_equipment_icons`, and the static resource helpers used below.

```python
    def refresh_equipment_workspace_override(self, *, workspace_root: str) -> dict:
        with self._lock:
            bundle = self.bundle(workspace_root=workspace_root)
            override_root = Path(workspace_root) / ".trail" / "cache" / "cw-equipment-resource"
            staging_root = override_root.with_name("cw-equipment-resource.tmp")
            if staging_root.exists():
                shutil.rmtree(staging_root)
            staging_root.mkdir(parents=True, exist_ok=True)
            catalog = equipment_catalog_from_manifest(bundle.equipment_manifest)
            summary = prepare_equipment_icon_cache(catalog, workspace_root=workspace_root, refresh=True)
            icons = load_cached_equipment_icons(catalog, workspace_root=workspace_root)
            features = build_precomputed_equipment_features(icons)
            write_bundle_json(staging_root / "equipment" / "features.json", features)
            equipment_manifest = write_workspace_equipment_icons_from_cache(
                override_root=staging_root,
                catalog=catalog,
                workspace_root=workspace_root,
            )
            write_bundle_json(staging_root / "equipment" / "manifest.json", equipment_manifest)
            write_equipment_override_manifest(staging_root, base_bundle=bundle)
            if override_root.exists():
                shutil.rmtree(override_root)
            os.replace(staging_root, override_root)
            self.invalidate_workspace(workspace_root=workspace_root)
            return summary

    def equipment_prepare_summary(self, *, workspace_root: str) -> dict:
        bundle = self.bundle(workspace_root=workspace_root)
        items = bundle.equipment_manifest.get("items") if isinstance(bundle.equipment_manifest, dict) else []
        count = len(items) if isinstance(items, list) else 0
        return {"big_version": bundle.big_version, "count": count, "cached": count, "downloaded": 0, "refreshed": False}
```

Add these helpers to `trail/scenes/cw/static_resources.py`:

```python
from shutil import copy2

from trail.scenes.cw.equipment_resources import EQUIPMENT_ICON_CACHE_RELATIVE, EquipmentCatalogEntry, safe_equipment_cache_segment


def equipment_catalog_from_manifest(manifest: dict[str, Any]) -> list[EquipmentCatalogEntry]:
    catalog: list[EquipmentCatalogEntry] = []
    for item in manifest.get("items") or []:
        if not isinstance(item, dict):
            continue
        catalog.append(
            EquipmentCatalogEntry(
                cache_key=str(item["cache_key"]),
                id=None if item.get("id") is None else str(item.get("id")),
                name=str(item["name"]),
                kind=str(item.get("kind") or "advanced"),
                category=None if item.get("category") is None else str(item.get("category")),
                category_name=None if item.get("category_name") is None else str(item.get("category_name")),
                icon_url=str(item.get("icon_url") or ""),
                big_version=str(item.get("big_version") or ""),
            )
        )
    return catalog


def write_workspace_equipment_icons_from_cache(
    *,
    override_root: Path,
    catalog: list[EquipmentCatalogEntry],
    workspace_root: str | Path,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if not catalog:
        return {"items": items}
    cache_version_dir = Path(workspace_root) / EQUIPMENT_ICON_CACHE_RELATIVE / safe_equipment_cache_segment(catalog[0].big_version)
    for entry in catalog:
        source = cache_version_dir / "icons" / f"{safe_equipment_cache_segment(entry.cache_key)}.png"
        relative = f"equipment/icons/{safe_equipment_cache_segment(entry.cache_key)}.png"
        target = override_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, target)
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
                "local_path": relative,
                "sha256": _file_sha256(target),
                "size": target.stat().st_size,
            }
        )
    return {"items": items}


def write_equipment_override_manifest(override_root: Path, *, base_bundle: CwResourceBundle) -> dict[str, Any]:
    files = [
        bundle_file_entry(override_root, "equipment/manifest.json"),
        bundle_file_entry(override_root, "equipment/features.json"),
    ]
    equipment_manifest = _read_json(override_root / "equipment" / "manifest.json")
    for item in equipment_manifest.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("local_path"), str):
            files.append(bundle_file_entry(override_root, str(item["local_path"])))
    manifest = {
        "bundle_schema_version": CW_RESOURCE_BUNDLE_SCHEMA_VERSION,
        "source_kind": "workspace_equipment",
        "base_content_digest": base_bundle.identity,
        "base_resource_version": base_bundle.manifest.get("resource_version"),
        "rpg_game_big_version": base_bundle.big_version,
        "files": files,
    }
    manifest["content_digest"] = _manifest_digest({key: value for key, value in manifest.items() if key != "content_digest"})
    write_bundle_json(override_root / "manifest.json", manifest)
    return manifest
```

- [ ] **Step 4: Route prepare refresh through service**

In `trail/daemon/cw_service.py`, change the `cw.equipment.prepare` handler to:

Import `load_default_cw_resource_bundle` from `trail.scenes.cw.static_resources`.

```python
def _equipment_prepare_summary_from_bundle(*, workspace_root: str) -> dict:
    bundle = load_default_cw_resource_bundle(workspace_root=workspace_root)
    items = bundle.equipment_manifest.get("items") if isinstance(bundle.equipment_manifest, dict) else []
    count = len(items) if isinstance(items, list) else 0
    return {"big_version": bundle.big_version, "count": count, "cached": count, "downloaded": 0, "refreshed": False}


def run_equipment_prepare() -> dict:
    if bool(payload.get("refresh")) and self.cw_resource_service is not None:
        return self.cw_resource_service.refresh_equipment_workspace_override(workspace_root=workspace_root)
    if self.cw_resource_service is not None:
        return self.cw_resource_service.equipment_prepare_summary(workspace_root=workspace_root)
    return _equipment_prepare_summary_from_bundle(workspace_root=workspace_root)
```

Then set handler:

```python
"cw.equipment.prepare": run_equipment_prepare,
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_equipment_prepare_refresh_invalidates_and_switches_to_workspace_override tests/test_daemon_protocol.py::test_cw_equipment_prepare_default_uses_bundle_summary_without_downloading tests/test_daemon_protocol.py::test_cw_equipment_prepare_default_without_resource_service_uses_bundle_summary tests/test_cw_equipment.py::test_prepare_cw_equipment_builds_catalog_and_cache -q
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
    from types import SimpleNamespace
    from PIL import Image
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

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
        "trail.scenes.cw.equipment.fetch_cw_raw_guide_config",
        lambda workspace_root=None: {"rpg_game_big_version": "3.2", "equipment_list": []},
    )
    monkeypatch.setattr(
        "trail.scenes.cw.equipment.read_cw_equipment",
        lambda runtime, workspace_root=None, raw_config=None, recognizer=None, request_id=None: {
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
    assert "_screenshot" not in response["data"]
    saved_session = session_service.load_session(session.session_id)
    assert "_screenshot" not in saved_session.scene_state["cw"]["equipment"]
    assert runtime.capture_after_action_calls == 0


def test_cw_equipment_read_falls_back_to_capture_after_action_when_reuse_save_fails(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from PIL import Image
    from trail.daemon.cw_service import CwService
    from trail.daemon.session_service import SessionServiceRegistry

    class Runtime:
        def __init__(self):
            self.capture_after_action_calls = 0
        def capture_image(self, **kwargs):
            return Image.new("RGBA", (1920, 1080), "black")
        def save_capture_image_to_workspace(self, image, request_id=None):
            raise OSError("disk full")
        def capture_after_action(self, optional=False, request_id=None):
            self.capture_after_action_calls += 1
            return tmp_path / "fallback.jpg"

    class Recognizer:
        def recognize(self, image):
            from trail.scenes.cw.equipment_recognition import EquipmentRecognitionResult
            return EquipmentRecognitionResult(candidates=[], score=None, gap=None, uncertain=False, empty=True)

    runtime = Runtime()
    registry = SessionServiceRegistry()
    session_service = registry.for_workspace(str(tmp_path))
    session = session_service.create_session(window_binding={"title": "崩坏：星穹铁道", "hwnd": 1})
    cw_service = CwService(runtime_service=SimpleNamespace(get_runtime=lambda **kwargs: runtime), cw_resource_service=SimpleNamespace(equipment_read_resources=lambda workspace_root: ({"rpg_game_big_version": "3.2", "equipment_list": []}, Recognizer())))

    response = cw_service.handle_with_capture(
        method="cw.equipment.read",
        payload={"session_id": session.session_id},
        workspace_root=str(tmp_path),
        session_service=session_service,
        request_id="r1",
    )

    assert response["ok"] is True
    assert response["screenshot"].endswith("fallback.jpg")
    assert runtime.capture_after_action_calls == 1
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_daemon_protocol.py::test_cw_equipment_read_reuses_recognition_screenshot tests/test_daemon_protocol.py::test_cw_equipment_read_falls_back_to_capture_after_action_when_reuse_save_fails -q
```

Expected: FAIL because `with_selective_capture` still calls `capture_after_action`.

- [ ] **Step 3: Add save-existing-image API**

In `trail/runtime/window.py`:

```python
    def save_capture_image_to_workspace(self, image, request_id: str | None = None) -> Path:
        path = self.workspace / f"{_safe_capture_request_id(request_id)}.jpg"
        image.convert("RGB").save(path, format="JPEG", quality=90, optimize=True)
        return path
```

In `trail/runtime/operator.py`, add the protocol method to the existing `WindowController` protocol:

```python
class WindowController(Protocol):
    def save_capture_image_to_workspace(self, image, request_id: str | None = None) -> Path:
        pass
```

Then add the forwarding method to `RuntimeOperator`:

```python
def save_capture_image_to_workspace(self, image, request_id: str | None = None):
    save = getattr(self.window, "save_capture_image_to_workspace", None)
    if not callable(save):
        raise TrailError("SCREENSHOT_FAILED", "window controller does not support saving captured image")
    return save(image, request_id=request_id)
```

- [ ] **Step 4: Return screenshot metadata from equipment read**

In `trail/scenes/cw/equipment.py`, add `request_id: str | None = None` to `read_cw_equipment` and `apply_cw_equipment_read`. After `_runtime_image(runtime)` succeeds, save it when possible:

```python
def _save_reused_screenshot(runtime, image: Image.Image, *, request_id: str | None = None) -> str | None:
    save = getattr(runtime, "save_capture_image_to_workspace", None)
    if not callable(save):
        return None
    try:
        path = save(image, request_id=request_id)
    except Exception:
        return None
    return str(path) if path is not None else None
```

Inside `read_cw_equipment`, set `screenshot = _save_reused_screenshot(runtime, image, request_id=request_id) if request_id is not None else None` immediately after `image = _runtime_image(runtime)`, and include `"_screenshot": screenshot` in the returned dict only when `screenshot is not None`.

In `trail/daemon/cw_service.py`, add `request_id: str | None = None` to `_context`, pass it from `handle_with_capture`, and pass it into `apply_cw_equipment_read` only for `cw.equipment.read`:

```python
def _context(
    self,
    *,
    method: str,
    payload: dict,
    workspace_root: str,
    session_service,
    request_id: str | None = None,
    track_side_effects: bool = False,
    shared_capture_scope: bool = False,
):
```

```python
session, _, runtime, handlers, _, _, _ = self._context(
    method=method,
    payload=payload,
    workspace_root=workspace_root,
    session_service=session_service,
    request_id=request_id,
)
```

Update the `run_equipment_read` local handler from Task 8:

```python
def run_equipment_read() -> dict:
    if self.cw_resource_service is None:
        return apply_cw_equipment_read(session, runtime(), workspace_root=workspace_root, request_id=request_id)
    raw_config, recognizer = self.cw_resource_service.equipment_read_resources(workspace_root=workspace_root)
    return apply_cw_equipment_read(
        session,
        runtime(),
        workspace_root=workspace_root,
        raw_config=raw_config,
        recognizer=recognizer,
        request_id=request_id,
    )
```

`read_cw_equipment` may return private key `"_screenshot": path`, but `apply_cw_equipment_read` must strip that key before writing `cw_state.equipment`:

```python
resolved_raw_config = raw_config if raw_config is not None else fetch_cw_raw_guide_config(workspace_root=workspace_root)
snapshot = read_cw_equipment(
    runtime,
    workspace_root=workspace_root,
    raw_config=resolved_raw_config,
    recognizer=recognizer,
    request_id=request_id,
)
screenshot = snapshot.pop("_screenshot", None)
recommendations = build_cw_equipment_recommendations(session, snapshot=snapshot, raw_config=resolved_raw_config)
if recommendations is not None:
    snapshot["recommendations"] = recommendations
ensure_cw_state(session)["equipment"] = deepcopy(snapshot)
return {**deepcopy(snapshot), "_screenshot": screenshot} if screenshot is not None else snapshot
```

`with_selective_capture` consumes and removes `"_screenshot"` before building the envelope, so it never appears in response `data`, YAML, session, or `state.dump`.

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
uv run pytest tests/test_daemon_protocol.py::test_cw_equipment_read_reuses_recognition_screenshot tests/test_daemon_protocol.py::test_cw_equipment_read_falls_back_to_capture_after_action_when_reuse_save_fails tests/test_output_rendering.py::test_render_output_cw_equipment_read_orders_shot_items_info_warn_ref -q
```

Expected: PASS.

---

### Task 10: Output, YAML, Docs, And No-Network Contract Coverage

**Files:**
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_atomic_commands.py`
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_output_debug.py`
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: `skills/trail-cw-portal/SKILL.md`
- Modify: `skills/trail-cw-portal/references/portal-selection-rules.md`
- Modify: `skills/trail-cw-portal/references/portal-refresh-policy.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `skills/trail-cw-guide/references/confirmation-checklist.md`
- Modify: `skills/trail-cw-guide/references/guide-selection-criteria.md`

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
    monkeypatch.setattr(scene, "fetch_cw_raw_guide_config", lambda *a, **k: (_ for _ in ()).throw(AssertionError("raw config must not be fetched")))

    result = scene.read_cw_equipment(Runtime(), workspace_root=tmp_path, recognizer=Recognizer())

    assert result["count"] == 0
```

- [ ] **Step 2: Add YAML/state shape assertions**

Add to `tests/test_cw_rpc_contracts.py`:

```python
def test_cw_equipment_read_yaml_keeps_structured_fields_after_resource_bundle(cli_runner, fake_daemon_client, tmp_path):
    client = fake_daemon_client(
        {
            "cw.equipment.read": build_success_response(
                request_id="req-equipment-read-yaml-fields",
                data={
                    "count": 1,
                    "uncertain": 1,
                    "empty": 59,
                    "items": [
                        {
                            "pos": "equipment:1",
                            "idx": 1,
                            "row": 1,
                            "col": 1,
                            "center": {"x": 1855, "y": 275},
                            "name": "幸运星",
                            "score": 0.7,
                            "gap": 0.01,
                            "uncertain": True,
                            "alt": "光能电池",
                            "alt_score": 0.69,
                            "candidates": [{"name": "幸运星", "score": 0.7}],
                        }
                    ],
                    "backend": "vector",
                    "layout": "default",
                    "columns": 10,
                    "rows": 6,
                    "stale": False,
                },
                screenshot=".trail/shots/req-equipment-read-yaml-fields.png",
            )
        }
    )

    result = cli_runner.invoke(app, ["--format", "yaml", "cw", "equipment", "read", "--session", SESSION_ID])

    assert result.exit_code == 0
    assert "row: 1" in result.stdout
    assert "col: 1" in result.stdout
    assert "candidates:" in result.stdout
    assert "gap: 0.01" in result.stdout
    assert "idx=" not in result.stdout
    assert "row=" not in result.stdout
    assert "col=" not in result.stdout
    _assert_single_call(client, method="cw.equipment.read", payload={}, tmp_path=tmp_path)
```

- [ ] **Step 3: Extend `state.dump` YAML equipment snapshot assertions**

In `tests/test_atomic_commands.py`, extend `test_state_dump_yaml_includes_cw_equipment_snapshot` so the fake equipment item includes structured fields:

```python
"gap": 0.01,
"alt": "光能电池",
"alt_score": 0.69,
"candidates": [{"name": "生命之花", "score": 0.7}],
```

Then add assertions after the existing `row/col` assertions:

```python
assert "gap: 0.01" in result.stdout
assert "alt: 光能电池" in result.stdout
assert "alt_score: 0.69" in result.stdout
assert "candidates:" in result.stdout
```

- [ ] **Step 4: Add low confidence warn regression and default-text field guard**

Add to `tests/test_output_rendering.py`:

```python
def test_render_cw_equipment_read_keeps_default_fields_and_low_confidence_warn():
    payload = {
        "ok": True,
        "request_id": "r1",
        "screenshot": ".trail/shots/r1.jpg",
        "data": {
            "count": 1,
            "uncertain": 1,
            "empty": 59,
            "items": [
                {
                    "pos": "equipment:1",
                    "idx": 1,
                    "row": 1,
                    "col": 1,
                    "center": {"x": 1855, "y": 275},
                    "name": "幸运星",
                    "score": 0.7,
                    "gap": 0.01,
                    "uncertain": True,
                    "alt": "光能电池",
                    "alt_score": 0.69,
                    "candidates": [],
                }
            ],
            "backend": "vector",
            "layout": "default",
            "columns": 10,
            "rows": 6,
            "stale": False,
        },
        "warnings": [],
        "references": [],
        "error": None,
    }

    output = "\n".join(render_output("cw.equipment.read", payload).splitlines())

    assert "ok cw.equipment.read count=1 uncertain=1 empty=59" in output
    assert "shot path=.trail/shots/r1.jpg" in output
    assert "info read_image_first=1" in output
    assert "item pos=equipment:1 center=1855,275 name=幸运星 score=0.70 uncertain=1 gap=0.01 alt=光能电池 alt_score=0.69" in output
    assert "warn code=LOW_CONFIDENCE count=1" in output
    assert output.count("warn code=LOW_CONFIDENCE") == 1
    assert "idx=" not in output
    assert "row=" not in output
    assert "col=" not in output
    assert "box=" not in output
```

- [ ] **Step 5: Add `guide.config.cw` bundle/no-network CLI and YAML tests**

Add to `tests/test_cw_rpc_contracts.py`:

```python
def test_guide_config_cw_cli_and_yaml_keep_bundle_shape(cli_runner, fake_daemon_client, tmp_path):
    data = {
        "meta": {"big_version": "3.2", "season_id": "s1", "sub_season_id": "sub1"},
        "lineup_levels": [],
        "traits": [{"id": "t1", "name": "贝洛伯格", "layers": [2, 4, 6]}],
        "roles": [],
        "role_tags": [],
        "portal_list": [],
        "strategy_list": [],
    }
    default_client = fake_daemon_client({"guide.config.cw": build_success_response(request_id="req-guide-config", data=data)})

    default_result = cli_runner.invoke(app, ["guide", "config", "cw"])

    assert default_result.exit_code == 0
    assert "ok guide.config.cw" in default_result.stdout
    assert "大版本=3.2" in default_result.stdout
    assert default_client.calls[0]["method"] == "guide.config.cw"

    yaml_client = fake_daemon_client({"guide.config.cw": build_success_response(request_id="req-guide-config-yaml", data=data)})

    yaml_result = cli_runner.invoke(app, ["--format", "yaml", "guide", "config", "cw"])

    assert yaml_result.exit_code == 0
    assert "traits:" in yaml_result.stdout
    assert "贝洛伯格" in yaml_result.stdout
    assert yaml_client.calls[0]["method"] == "guide.config.cw"
```

- [ ] **Step 6: Update skill docs for changed portal guide summary behavior**

Update `AGENTS.md`, `skills/trail-cw-prep/SKILL.md`, `skills/trail-cw-prep/references/command-surface.md`, `skills/trail-cw-portal/SKILL.md`, `skills/trail-cw-portal/references/portal-selection-rules.md`, `skills/trail-cw-portal/references/portal-refresh-policy.md`, `skills/trail-cw-guide/SKILL.md`, `skills/trail-cw-guide/references/confirmation-checklist.md`, and `skills/trail-cw-guide/references/guide-selection-criteria.md` to state:

```markdown
- `cw.start` / `cw.portal.*` 默认输出不自动附加动态攻略摘要；需要动态攻略信息时显式使用 `guide.list.cw` / `guide.fetch.cw`，或依赖当前已选攻略。
- `cw.equipment.prepare` 默认只验证/汇总 bundle 装备资源，不下载图标；只有 `cw.equipment.prepare --refresh` 写 workspace equipment override 并刷新图标/特征。
- `cw.equipment.read` daemon 默认热路径使用 bundle recognizer，不调用 prepare/download/load icon cache。
- trail-cw-guide 无人值守模式不能假设 portal 卡片自带热度/版本/互动数据；需要这些动态信号时先显式调用 `guide.list.cw` / `guide.fetch.cw`。
- portal refresh policy 在默认输出无动态攻略摘要时不得使用攻略互动数据作为刷新条件；需要互动数据时先显式获取攻略列表/详情。
```

Do not update root `README.md` in this task.

- [ ] **Step 7: Run contract tests**

Run:

```powershell
uv run pytest tests/test_cw_equipment.py tests/test_cw_rpc_contracts.py tests/test_atomic_commands.py tests/test_output_rendering.py tests/test_output_debug.py -q
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
