# CW Slots Icon Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `cw.slots.read` 默认角色读取从逐槽位详情/OCR 改为 bundle-first 的一次截图图标识别，并保持输出、session、portal auto-collect 和 release artifact 契约稳定。

**Architecture:** 先扩展 CW 静态资源包以携带角色 icon、empty 模板和预计算特征，再新增独立 `role_recognition` 模块负责几何、mask、星级、费率和 matcher。daemon 通过 `CwResourceService` 缓存 role recognizer，`slots.py` 只消费 recognizer 输出并复用现有 canonicalization/merge/stale 逻辑，capture 层统一消费 private `_screenshot` 避免二次截图。

**Tech Stack:** Python 3.12、Pillow、OpenCV/cv2、NumPy、pytest、uv、现有 daemon/session/output renderer 架构。

---

## File Map

- Create: `trail/scenes/cw/role_resources.py`。角色资源 catalog、safe path、下载/校验、manifest entry helper。
- Create: `trail/scenes/cw/role_recognition.py`。角色 feature payload、geometry、mask、empty/role/star/fee 识别、`VectorRoleIconRecognizer`。
- Create: `trail/scenes/cw/assets/slots/empty-field-v1.png`。`103x120` field empty 模板种子图。
- Create: `trail/scenes/cw/assets/slots/empty-hand-v1.png`。`103x120` hand empty 模板种子图。
- Create: `tests/fixtures/cw/slots-icon/current-prep.jpg`。真实准备页截图夹具，用于角色/空槽/星级/费率回归。
- Create: `tests/fixtures/cw/slots-icon/hand-empty-prep.jpg`。含手牌空槽的真实准备页截图夹具，用于 hand empty 模板。
- Modify: `trail/scenes/cw/static_resources.py`。bundle schema 扩展、role manifest/features 验证、allowlist/unreferenced 校验、bundle dataclass 字段。
- Modify: `scripts/build-cw-resource-bundle.py`。构建角色资源、empty 模板和 features，并纳入 manifest。
- Create: `scripts/extract-cw-slot-empty-templates.py`。从真实准备页截图按冻结几何提取 `103x120` empty 模板。
- Modify: `scripts/verify-cw-resource-bundle-artifacts.py`。release artifact verifier 支持 `roles/`。
- Modify: `trail/daemon/cw_resource_service.py`。新增 role recognizer 缓存和 `slots_read_resources()`。
- Modify: `trail/output/capture.py`。`with_auto_capture()` success 路径消费 `_screenshot`。
- Modify: `trail/daemon/cw_service.py`。`cw.slots.read` 和 `cw.portal.select` 使用 role resources、传递 `request_id`、提升 portal slots screenshot。
- Modify: `trail/scenes/cw/slots.py`。新增截图级 slots reader，扩展 `CwSlotsReadResult` private screenshot，处理 unknown 不污染 session。
- Modify: `trail/output/rendering.py`。仅在需要时补 warning 字段渲染；默认 slot 行不得输出 `role_id`。
- Modify: `tests/test_build_cw_resource_bundle.py`。构建角色资源测试。
- Modify: `tests/test_cw_static_resources.py`。loader/schema/override/no-network 测试。
- Modify: `tests/test_release_build.py`。artifact verifier 测试。
- Create: `tests/test_cw_role_recognition.py`。几何、mask、空槽、星级、费率和 matcher 单测。
- Create: `tests/test_cw_resource_service.py`。`CwResourceService` role recognizer 缓存和 no-network 单测。
- Create: `tests/test_output_capture.py`。`with_auto_capture()` private `_screenshot` 单测。
- Modify: `tests/test_cw_slots.py`。recognizer、geometry、mask、unknown、session 语义测试。
- Modify: `tests/test_daemon_protocol.py`。daemon capture、portal auto-collect、request_id、no second capture 测试。
- Modify: `tests/test_output_rendering.py`。默认文本/warning/verbose 泄漏契约测试。
- Modify: `skills/trail-cw-prep/SKILL.md` 和 `skills/trail-cw-prep/references/command-surface.md`。Agent 可见 slots 图标识别与先读截图说明。

## Execution Rules

- 执行实现前先创建项目内 worktree。使用已存在且被 git ignore 的目录：`C:\Users\34404\source\repos\trail-cli\.worktrees\cw-slots-icon-recognition`。
- 当前主工作区已有未提交的 spec/plan/generated diff。创建 worktree 后，必须把本 spec 和本 plan 同步复制到 worktree 分支，或在 worktree 内重新应用同等 patch，避免最终实现分支缺少审查通过的设计/计划文档。复制后在 worktree 中运行 `rtk git status --short`，确认 docs diff 和实现 diff 处在同一个 worktree。
- 本会话未收到提交请求，执行计划时不要运行 `git commit`。每个任务末尾只做 checkpoint：`rtk git status --short`、列出变更文件和测试结果。若用户明确要求提交，再按 Conventional Commits 使用中文 subject。
- 每个任务先写失败测试，再写最小实现，再运行 focused tests。不要一次性跳到全量实现。
- 不要修改 `docs/superpowers/specs` 或 `docs/superpowers/plans` 的测试扫描逻辑；这些文档不是契约来源。

---

### Task 1: Role Resource Schema And Loader

**Files:**
- Modify: `tests/test_cw_static_resources.py`
- Modify: `trail/scenes/cw/static_resources.py`
- Create: `trail/scenes/cw/role_resources.py`

- [ ] **Step 1: Write failing static resource tests**

Add helpers to `tests/test_cw_static_resources.py` near existing equipment helpers. Use deterministic byte payloads so tests do not need real PNG decoding in loader tests:

```python
def _valid_role_manifest(root: Path) -> dict:
    icon_relative = "roles/icons/r1.png"
    field_relative = "roles/empty/field-v1.png"
    hand_relative = "roles/empty/hand-v1.png"
    for relative, data in {
        icon_relative: b"role-icon-r1",
        field_relative: b"field-empty",
        hand_relative: b"hand-empty",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {
        "role_manifest_schema_version": 1,
        "resource_version": "3.2",
        "empty_template_version": "cw-slots-empty-v1",
        "empty_templates": {
            "field": {"local_path": field_relative, "sha256": _sha256(root / field_relative), "size": (root / field_relative).stat().st_size},
            "hand": {"local_path": hand_relative, "sha256": _sha256(root / hand_relative), "size": (root / hand_relative).stat().st_size},
        },
        "items": [
            {
                "role_id": "r1",
                "name": "Role A",
                "normalized_name": "role a",
                "icon_url": "https://example.test/r1.png",
                "local_path": icon_relative,
                "sha256": _sha256(root / icon_relative),
                "size": (root / icon_relative).stat().st_size,
                "rarity": "2",
                "front_back_type": "Common",
                "trait_ids": ["t1"],
            }
        ],
    }


def _valid_role_features() -> dict:
    return {
        "role_feature_schema_version": 1,
        "recognizer_algorithm_version": "role-card-mask-v1",
        "geometry_version": "cw-slots-1920x1080-v2",
        "empty_template_version": "cw-slots-empty-v1",
        "target_size": [103, 120],
        "avatar_roi": [5, 4, 98, 108],
        "feature_size": [64, 64],
        "hist_bins": [16, 16, 16],
        "min_score": 0.58,
        "low_score": 0.50,
        "min_gap": 0.035,
        "empty_min_score": 0.82,
        "empty_min_gap": 0.08,
        "items": [
            {
                "role_id": "r1",
                "name": "Role A",
                "normalized_name": "role a",
                "rarity": "2",
                "front_back_type": "Common",
                "trait_ids": ["t1"],
                "icon_rgba": _pixel_payload("RGBA", [64, 64]),
                "icon_mask": _pixel_payload("L", [64, 64]),
                "histogram": [0.0] * 4096,
            }
        ],
    }
```

Update `_bundle()` so it writes `roles/manifest.json` and `roles/features.json` before `write_bundle_manifest()`. Then add tests:

```python
def test_load_cw_resource_bundle_validates_role_resources(tmp_path):
    bundle = load_cw_resource_bundle_from_path(_bundle(tmp_path))

    assert bundle.role_manifest["items"][0]["role_id"] == "r1"
    assert bundle.role_features["recognizer_algorithm_version"] == "role-card-mask-v1"


def test_load_cw_resource_bundle_rejects_role_icon_path_escape(tmp_path):
    root = _bundle(tmp_path)
    role_manifest = json.loads((root / "roles" / "manifest.json").read_text(encoding="utf-8"))
    role_manifest["items"][0]["local_path"] = "roles/icons/../escape.png"
    _write_json(root / "roles" / "manifest.json", role_manifest)
    _refresh_manifest_entry(root, "roles/manifest.json")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"


def test_load_cw_resource_bundle_rejects_role_feature_manifest_mismatch(tmp_path):
    root = _bundle(tmp_path)
    features = _valid_role_features()
    features["items"][0]["role_id"] = "missing-from-manifest"
    _write_json(root / "roles" / "features.json", features)
    _refresh_manifest_entry(root, "roles/features.json")

    with pytest.raises(TrailError) as exc_info:
        load_cw_resource_bundle_from_path(root)

    assert exc_info.value.code == "CW_RESOURCE_BUNDLE_INVALID"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/test_cw_static_resources.py -q`

Expected: fail because `CwResourceBundle` lacks `role_manifest`, fixed relatives do not include `roles/*`, and validators do not exist.

- [ ] **Step 3: Implement `role_resources.py` helpers**

Create `trail/scenes/cw/role_resources.py` with these public pieces:

```python
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Any

from PIL import Image

from trail.core.errors import TrailError
from trail.scenes.cw.equipment_resources import download_equipment_icon_bytes, safe_equipment_cache_segment

ROLE_ICON_MAX_BYTES = 512_000


@dataclass(frozen=True)
class RoleCatalogEntry:
    role_id: str
    name: str
    normalized_name: str
    icon_url: str
    rarity: str | None
    cost: str | None
    front_back_type: str | None
    trait_ids: list[str]


def normalize_role_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip().lower()).strip()


def safe_role_cache_segment(value: object) -> str:
    return safe_equipment_cache_segment(str(value or "role"))


def build_cw_role_catalog(raw_config: dict[str, Any]) -> list[RoleCatalogEntry]:
    items = raw_config.get("role_list")
    if not isinstance(items, list):
        return []
    result: list[RoleCatalogEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        icon_url = str(item.get("icon") or "").strip()
        if not role_id or not name or not icon_url:
            continue
        result.append(
            RoleCatalogEntry(
                role_id=role_id,
                name=name,
                normalized_name=normalize_role_name(name),
                icon_url=icon_url,
                rarity=None if item.get("rarity") is None else str(item.get("rarity")),
                cost=None if item.get("cost") is None else str(item.get("cost")),
                front_back_type=None if item.get("front_back_type") is None else str(item.get("front_back_type")),
                trait_ids=[str(value) for value in item.get("trait_ids") or []],
            )
        )
    return result


def download_role_icon_bytes(url: str, *, timeout: int, max_bytes: int = ROLE_ICON_MAX_BYTES) -> bytes:
    return download_equipment_icon_bytes(url, timeout=timeout, max_bytes=max_bytes)


def write_verified_role_icon(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(BytesIO(data)) as image:
            image.convert("RGBA").save(path, format="PNG")
    except Exception as exc:
        raise TrailError("CW_ROLE_ICON_INVALID", f"invalid cw role icon: {path}") from exc
```

- [ ] **Step 4: Extend `static_resources.py` minimally**

Add constants and dataclass fields:

```python
CW_ROLE_MANIFEST_SCHEMA_VERSION = 1
CW_ROLE_FEATURE_SCHEMA_VERSION = 1
CW_ROLE_RECOGNIZER_ALGORITHM_VERSION = "role-card-mask-v1"
CW_SLOT_GEOMETRY_VERSION = "cw-slots-1920x1080-v2"
CW_SLOT_EMPTY_TEMPLATE_VERSION = "cw-slots-empty-v1"
```

Extend `_FIXED_BUNDLE_RELATIVES` with `roles/manifest.json`, `roles/features.json`, `roles/empty/field-v1.png`, and `roles/empty/hand-v1.png`. Add `_clean_role_icon_relative()`, `_clean_role_empty_relative()`, `_validate_role_manifest()`, `_validate_role_features()`, and `_validate_role_feature_manifest_keys()` following the equipment validator pattern.

Update these functions explicitly:

- `write_bundle_manifest()` must read `roles/manifest.json`, validate it, append every normalized `roles/icons/*.png` local path and both `roles/empty/*.png` paths to `file_relatives`, and reject duplicate normalized paths across fixed, equipment, role icon, and role empty files.
- `_validate_manifest_files_allowed()` must allow fixed relatives, equipment icons, role icons, and role empty templates.
- Add `_validate_role_manifest_paths_listed()` matching the equipment helper so every role icon and empty template referenced by role manifest appears in `manifest.files`.
- `_validate_no_unreferenced_bundle_files()` remains global and must reject any extra file under `roles/`.
- `load_cw_resource_bundle_from_path()` must read role files, validate role manifest/features, verify role manifest paths listed, verify feature/manifest `role_id` sets match, and return `role_manifest`, `role_features`, `role_manifest_path`, and `role_manifest_mtime` in `CwResourceBundle`.
- `_overlay_workspace_equipment()` must preserve package `role_manifest` and `role_features`; workspace equipment override manifests containing `roles/manifest.json`, `roles/features.json`, `roles/icons/*`, or `roles/empty/*` must be rejected as unexpected override files.

Add tests for legal equipment override preserving package role resources and for an override containing `roles/features.json` being rejected with `CW_RESOURCE_BUNDLE_INVALID`.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/test_cw_static_resources.py -q`

Expected: pass all static resource tests.

- [ ] **Step 6: Checkpoint**

Run: `rtk git status --short`

Expected: modified `trail/scenes/cw/static_resources.py`, new `trail/scenes/cw/role_resources.py`, modified `tests/test_cw_static_resources.py`, no unintended unrelated changes from this task.

---

### Task 2: Build Script And Artifact Verifier

**Files:**
- Modify: `tests/test_build_cw_resource_bundle.py`
- Modify: `tests/test_release_build.py`
- Modify: `scripts/build-cw-resource-bundle.py`
- Create: `scripts/extract-cw-slot-empty-templates.py`
- Modify: `scripts/verify-cw-resource-bundle-artifacts.py`
- Create: `trail/scenes/cw/assets/slots/empty-field-v1.png`
- Create: `trail/scenes/cw/assets/slots/empty-hand-v1.png`

- [ ] **Step 1: Write failing build bundle tests**

Update `_raw_config()` in `tests/test_build_cw_resource_bundle.py` so the role has an icon and rarity:

```python
"role_list": [{"id": "r1", "name": "希儿", "icon": "https://act-webstatic.mihoyo.com/r1.png", "rarity": "2", "front_back_type": "Common", "trait_ids": ["t1"]}],
```

Change the icon fetcher test double to return different PNG colors by URL:

```python
def _icon_fetcher(url, timeout, max_bytes):
    color = "blue" if url.endswith("r1.png") else "red"
    return _png_bytes(color)
```

In `test_build_cw_resource_bundle_writes_manifest_and_loadable_bundle()`, add expected files:

```python
"roles/manifest.json",
"roles/features.json",
"roles/icons/r1.png",
"roles/empty/field-v1.png",
"roles/empty/hand-v1.png",
```

Assert loaded role resources:

```python
assert loaded.role_manifest["items"][0]["role_id"] == "r1"
assert loaded.role_features["items"][0]["role_id"] == "r1"
```

- [ ] **Step 2: Write failing artifact verifier tests**

Update `_cw_bundle_entries()` in `tests/test_release_build.py` to include role manifest/features and role files. Define helper functions `_valid_role_manifest_payload()` and `_valid_role_feature_payload()` next to existing equipment payload helpers; both helpers must return the same role_id `r1` and `normalized_name="role a"`. Add focused rejection tests for every verifier/runtime sync risk:

```python
def test_verify_target_rejects_unreferenced_role_file(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "trail_cli-0.1.0-py3-none-any.whl"
    entries = _cw_bundle_entries("trail/scenes/cw/generated/3.2")
    entries["trail/scenes/cw/generated/3.2/roles/icons/extra.png"] = b"extra"
    _write_zip(archive_path, entries)

    with pytest.raises(ValueError, match="unreferenced generated file"):
        verifier.verify_target(archive_path)


def test_verify_target_rejects_role_feature_manifest_mismatch(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "trail_cli-0.1.0-py3-none-any.whl"
    bundle_root = "trail/scenes/cw/generated/3.2"
    entries = _cw_bundle_entries(bundle_root, {"roles/features.json": {**_valid_role_feature_payload(), "items": [{**_valid_role_feature_payload()["items"][0], "role_id": "missing"}]}})
    _write_zip(archive_path, entries)

    with pytest.raises(ValueError, match="role feature"):
        verifier.verify_target(archive_path)


def test_verify_target_rejects_role_icon_checksum_mismatch(tmp_path: Path) -> None:
    verifier = _load_verifier()
    archive_path = tmp_path / "trail_cli-0.1.0-py3-none-any.whl"
    bundle_root = "trail/scenes/cw/generated/3.2"
    role_manifest = _valid_role_manifest_payload()
    role_manifest["items"][0]["sha256"] = "0" * 64
    entries = _cw_bundle_entries(bundle_root, {"roles/manifest.json": role_manifest})
    _write_zip(archive_path, entries)

    with pytest.raises(ValueError, match="role manifest icon checksum mismatch"):
        verifier.verify_target(archive_path)
```

Also update `test_verify_cw_resource_bundle_artifacts_script_exists()` required strings to include `roles/features.json`, `roles/icons/`, `roles/empty/`, `_validate_role_manifest`, and `_validate_role_features`.

Update `test_pyproject_includes_cw_generated_resources()` or add a sibling test to assert the source distribution includes `trail/scenes/cw/assets/slots`, because `scripts/build-cw-resource-bundle.py` needs the seed templates when rebuilding from source.

- [ ] **Step 3: Run tests and verify failure**

Run: `uv run pytest tests/test_build_cw_resource_bundle.py tests/test_release_build.py -q`

Expected: fail because builder/verifier do not know role resources.

- [ ] **Step 4: Add empty template seed images**

Create versioned fixture screenshots first by copying real screenshots into `tests/fixtures/cw/slots-icon/`: copy `.trail/shots/b6dc59ad5fd54017bf5b956266d2e57a-7ff6c0b64b.jpg` to `tests/fixtures/cw/slots-icon/current-prep.jpg`, and copy `.trail/shots/d4e4b01a553b4802a6ad6bf9cd9685a4-55063a4673.jpg` to `tests/fixtures/cw/slots-icon/hand-empty-prep.jpg` after confirming `hand:9` is empty. Then create `trail/scenes/cw/assets/slots/empty-field-v1.png` and `trail/scenes/cw/assets/slots/empty-hand-v1.png` as real `103x120` RGBA canonical crops. Do not generate pure-color images.

Before running the extraction command, create `scripts/extract-cw-slot-empty-templates.py` with this complete self-contained script. It intentionally duplicates only the two extraction quads used for seed creation so Task 2 does not depend on `role_recognition.py`, which is created later.

```python
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

TARGET_SIZE = (103, 120)
QUADS = {
    "front:2": ((832.7, 339.7), (943.5, 339.7), (942.6, 462.9), (828.7, 462.9)),
    "hand:9": ((1390.3, 860.0), (1493.7, 860.0), (1493.7, 980.0), (1390.3, 980.0)),
}


def _load_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
    if rgba.size != (1920, 1080):
        raise SystemExit(f"expected 1920x1080 screenshot: {path}")
    return rgba


def _warp_slot(image: Image.Image, slot: str) -> Image.Image:
    if slot not in QUADS:
        raise SystemExit(f"unsupported extraction slot: {slot}")
    source = np.array(QUADS[slot], dtype=np.float32)
    target = np.array(((0, 0), (102, 0), (102, 119), (0, 119)), dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    warped = cv2.warpPerspective(np.array(image.convert("RGBA")), matrix, TARGET_SIZE)
    return Image.fromarray(warped, "RGBA")


def _save_checked(image: Image.Image, path: Path) -> None:
    if image.size != TARGET_SIZE or image.mode != "RGBA":
        raise SystemExit(f"invalid empty template output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--field-shot", required=True, type=Path)
    parser.add_argument("--field-slot", required=True)
    parser.add_argument("--hand-shot", required=True, type=Path)
    parser.add_argument("--hand-slot", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()

    field = _warp_slot(_load_image(args.field_shot), args.field_slot)
    hand = _warp_slot(_load_image(args.hand_shot), args.hand_slot)
    _save_checked(field, args.output_root / "empty-field-v1.png")
    _save_checked(hand, args.output_root / "empty-hand-v1.png")


if __name__ == "__main__":
    main()
```

```powershell
uv run python scripts\extract-cw-slot-empty-templates.py --field-shot tests\fixtures\cw\slots-icon\current-prep.jpg --field-slot front:2 --hand-shot tests\fixtures\cw\slots-icon\hand-empty-prep.jpg --hand-slot hand:9 --output-root trail\scenes\cw\assets\slots
```

Expected: both files exist, are valid RGBA PNGs, and have exact size `103x120`. If no hand-empty screenshot exists, capture one before executing this step and record the shot path in the task report.

- [ ] **Step 5: Implement build script role resources**

In `scripts/build-cw-resource-bundle.py`, import role helpers and add `_build_precomputed_role_features()` wrapping `role_recognition.build_precomputed_role_features()`. In `build_cw_resource_bundle()`:

- Build `role_catalog = build_cw_role_catalog(raw_config)`.
- For each role entry, write `roles/icons/{safe_role_id}.png`.
- Copy seed empty templates into `roles/empty/` after verifying size `103x120`.
- Write `roles/manifest.json` and `roles/features.json`.
- Pass role files through `write_bundle_manifest()`, which must append role icons and empty templates to `manifest.files` and reject duplicate normalized paths.

- [ ] **Step 6: Implement artifact verifier role checks**

In `scripts/verify-cw-resource-bundle-artifacts.py`:

- Add role required relatives.
- Allow generated directories `roles`, `roles/icons`, `roles/empty`.
- Load and validate role manifest/features using public validators from `static_resources.py`.
- Verify role manifest/features `role_id` sets match.
- Verify every role icon and empty template hash/size from role manifest against archive bytes.
- Add role icon and empty template paths to `allowed_references`.

- [ ] **Step 7: Run focused tests**

Run: `uv run pytest tests/test_build_cw_resource_bundle.py tests/test_release_build.py -q`

Expected: pass all focused tests.

- [ ] **Step 8: Regenerate checked-in CW bundle**

Run: `uv run python scripts\build-cw-resource-bundle.py`

Expected: generated `trail/scenes/cw/generated/4.2/roles/` exists, manifest includes role resources, and existing generated `raw_config.json` remains schema-compatible.

- [ ] **Step 9: Checkpoint**

Run: `rtk git status --short`

Expected: build script, verifier, tests, two seed assets, and generated bundle files changed.

---

### Task 3: Role Recognition Core

**Files:**
- Create: `trail/scenes/cw/role_recognition.py`
- Create: `tests/test_cw_role_recognition.py`

- [ ] **Step 1: Write failing recognizer unit tests**

Create `tests/test_cw_role_recognition.py`. Keep these tests independent from session/canonicalization tests so algorithm drift is caught before daemon integration.

Use direct helper assertions:

```python
from PIL import Image

from trail.scenes.cw.role_recognition import (
    CW_SLOT_GEOMETRY_VERSION,
    SLOT_TARGET_SIZE,
    fee_color_for_tier,
    fee_color_from_crop,
    iter_slot_specs,
    role_card_base_mask,
    choose_role_candidate_by_fee,
    classify_role_confidence,
    detect_slot_stars,
    RoleCandidate,
    STAR_MATCH_METHOD,
    STAR_NMS_RADIUS,
    STAR_SCALES,
    STAR_THRESHOLD,
)


def test_slot_geometry_has_19_specs_and_target_size():
    specs = list(iter_slot_specs())

    assert CW_SLOT_GEOMETRY_VERSION == "cw-slots-1920x1080-v2"
    assert SLOT_TARGET_SIZE == (103, 120)
    assert len(specs) == 19
    assert [(spec.area, spec.index) for spec in specs] == [
        ("front", 0), ("front", 1), ("front", 2), ("front", 3),
        ("back", 0), ("back", 1), ("back", 2), ("back", 3), ("back", 4), ("back", 5),
        ("hand", 0), ("hand", 1), ("hand", 2), ("hand", 3), ("hand", 4), ("hand", 5), ("hand", 6), ("hand", 7), ("hand", 8),
    ]
    assert specs[0].area == "front"
    assert specs[0].index == 0
    assert specs[0].quad == ((690.2, 339.7), (801.2, 339.7), (794.6, 462.9), (680.5, 462.9))
    assert specs[9].quad == ((1254.7, 611.7), (1369.6, 611.7), (1382.9, 737.6), (1265.0, 737.6))
    assert specs[-1].area == "hand"
    assert specs[-1].index == 8
    assert specs[-1].quad == ((1390.3, 860.0), (1493.7, 860.0), (1493.7, 980.0), (1390.3, 980.0))


def test_base_mask_excludes_fixed_regions():
    mask = role_card_base_mask([])

    assert mask.size == (103, 120)
    assert mask.getpixel((50, 50)) == 255
    assert mask.getpixel((5, 4)) == 0
    assert mask.getpixel((10, 10)) == 0
    assert mask.getpixel((90, 10)) == 0
    assert mask.getpixel((50, 115)) == 0


def test_star_constants_are_frozen():
    assert STAR_SCALES == (0.65, 0.75, 0.80)
    assert STAR_THRESHOLD == 0.78
    assert STAR_NMS_RADIUS == 8
    assert STAR_MATCH_METHOD == "TM_CCOEFF_NORMED"


def test_fee_tier_mapping_is_frozen():
    assert fee_color_for_tier("1") == "gray"
    assert fee_color_for_tier("2") == "green"
    assert fee_color_for_tier("3") == "blue"
    assert fee_color_for_tier("4") == "purple"
    assert fee_color_for_tier("5") == "gold"
    assert fee_color_for_tier(None) == "unknown"


def test_fee_color_from_crop_classifies_green_strip():
    crop = Image.new("RGBA", (103, 120), (0, 0, 0, 255))
    for x in range(4, 99):
        for y in range(112, 119):
            crop.putpixel((x, y), (91, 150, 56, 255))

    assert fee_color_from_crop(crop) == "green"


def test_fee_color_from_crop_classifies_all_known_colors():
    samples = {
        "gray": (92, 92, 92, 255),
        "green": (91, 150, 56, 255),
        "blue": (50, 112, 190, 255),
        "purple": (140, 75, 185, 255),
    }
    for expected, color in samples.items():
        crop = Image.new("RGBA", (103, 120), (0, 0, 0, 255))
        for x in range(4, 99):
            for y in range(112, 119):
                crop.putpixel((x, y), color)
        assert fee_color_from_crop(crop) == expected
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/test_cw_role_recognition.py -q`

Expected: import failure because `role_recognition.py` does not exist.

- [ ] **Step 3: Implement geometry, mask, fee helpers**

Create `trail/scenes/cw/role_recognition.py` with frozen geometry and helpers. Include:

- `SlotSpec` dataclass with `area`, `index`, `quad`.
- `SLOT_TARGET_SIZE = (103, 120)`.
- `iter_slot_specs()` returning all 19 specs in front/back/hand order.
- `role_card_base_mask(star_boxes)` implementing mask rectangles from the spec.
- `fee_color_for_tier(value)` and `fee_color_from_crop(crop)`.

- [ ] **Step 4: Add matcher payload tests**

Add tests for `build_precomputed_role_features()`, `VectorRoleIconRecognizer.from_precomputed_features()`, empty templates, and matcher scoring. Use the exact signature `build_precomputed_role_features(catalog: Iterable[RoleCatalogEntry], icons_by_role_id: Mapping[str, Image.Image])` and `VectorRoleIconRecognizer.from_precomputed_features(payload, empty_templates={"field": field_image, "hand": hand_image})`:

```python
def test_precomputed_role_features_round_trip():
    icon = Image.new("RGBA", (64, 64), (200, 40, 60, 255))
    field_empty = Image.new("RGBA", (103, 120), (20, 22, 26, 255))
    hand_empty = Image.new("RGBA", (103, 120), (32, 34, 40, 255))
    payload = build_precomputed_role_features([
        RoleCatalogEntry(
            role_id="r1",
            name="希儿",
            normalized_name="希儿",
            icon_url="https://example.test/r1.png",
            rarity="2",
            cost=None,
            front_back_type="Common",
            trait_ids=["t1"],
        ),
    ], {"r1": icon})

    recognizer = VectorRoleIconRecognizer.from_precomputed_features(payload, empty_templates={"field": field_empty, "hand": hand_empty})
    assert recognizer.algorithm_version == "role-card-mask-v1"
    assert recognizer.empty_template_version == "cw-slots-empty-v1"


def test_role_score_uses_ncc_l1_and_hist_weights():
    left = Image.new("RGBA", (64, 64), (0, 0, 0, 255))
    for x in range(64):
        for y in range(64):
            left.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256, 255))
    right = left.copy()
    mask = Image.new("L", (64, 64), 255)

    parts = role_similarity_parts(left, right, mask)
    assert parts.masked_ncc == 1.0
    assert parts.masked_l1 == 1.0
    assert parts.hist_corr == 1.0
    assert parts.score == 1.0


def test_star_detection_detects_template_and_returns_schema():
    from pathlib import Path

    crop = Image.new("RGBA", (103, 120), (0, 0, 0, 255))
    star = Image.open(Path("trail/scenes/cw/assets/star.png")).convert("RGBA")
    crop.alpha_composite(star.resize((round(star.width * 0.75), round(star.height * 0.75))), dest=(20, 90))
    detections = detect_slot_stars(crop)
    assert len(detections) == 1
    assert set(detections[0]) == {"x", "y", "w", "h", "score", "scale"}
    assert detections[0]["scale"] in {0.65, 0.75, 0.80}


def test_fee_tie_break_and_conflict_rules():
    assert choose_role_candidate_by_fee([
        RoleCandidate(name="A", role_id="a", score=0.56, rarity="2"),
        RoleCandidate(name="B", role_id="b", score=0.55, rarity="3"),
    ], fee_color="blue").role_id == "b"
    result = classify_role_confidence(
        top=RoleCandidate(name="A", role_id="a", score=0.70, rarity="2"),
        second=RoleCandidate(name="B", role_id="b", score=0.60, rarity="3"),
        fee_color="blue",
        empty_score=0.10,
    )
    assert result.match_kind == "low_confidence"
    assert result.confidence_reason == "icon_fee_conflict"
```

- [ ] **Step 5: Implement feature payload and recognizer**

Implement:

- `build_precomputed_role_features(catalog: Iterable[RoleCatalogEntry], icons_by_role_id: Mapping[str, Image.Image])` with pixel payloads and HSV histograms.
- `VectorRoleIconRecognizer.from_precomputed_features(payload, *, empty_templates: Mapping[str, Image.Image])`.
- `recognize_crop(crop, area)` returning `RoleRecognitionResult` with candidates, score, empty flag, star, fee color, and diagnostics.
- `RoleCandidate`, `choose_role_candidate_by_fee()`, and `classify_role_confidence()` implementing fee tie-break and fee conflict low-confidence rules from the spec.
- Star detection using `cv2.matchTemplate(roi_gray, template_gray, cv2.TM_CCOEFF_NORMED)` and NMS radius 8.
- Empty detection against `empty_templates["field"]` for `front/back` and `empty_templates["hand"]` for `hand`; recognizer construction must fail if either template is missing or not `103x120`.
- `warp_slot_crop(image, slot_spec)` must fail with `TrailError("SLOTS_LAYOUT_MISMATCH", "slots icon reader requires canonical 1920x1080 screenshot")` when input image size is not `(1920, 1080)`.
- Real screenshot fixture test must load `tests/fixtures/cw/slots-icon/current-prep.jpg`, warp `front:2`, and assert it matches the field empty template above threshold. The fixture file is created in Task 2 from the known diagnostic screenshot and must be versioned with the tests.
- Real full-recognition fixture test must load `tests/fixtures/cw/slots-icon/current-prep.jpg`, run the icon reader with generated role features, and assert these visible facts: `front:1=银枝`, `front:2=None`, `front:3=星期日`, `front:4=灵砂`, `back:1=忘归人`, `back:2=None`, `back:3=缇宝 star=2`, `back:4=那刻夏`, `back:5=None`, `back:6=阮•梅`, `hand:1=大丽花`, `hand:2=藿藿`, `hand:3=大丽花`, `hand:4=娜塔莎 star=1`, `hand:5=希儿 star=1`, `hand:6=遐蝶`, `hand:7=海瑟音`, `hand:8=波提欧`, `hand:9=藿藿`.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/test_cw_role_recognition.py -q`

Expected: pass all recognition core tests.

- [ ] **Step 7: Checkpoint**

Run: `rtk git status --short`

Expected: new recognition module and focused tests changed.

---

### Task 4: Capture Reuse Contract

**Files:**
- Modify: `trail/output/capture.py`
- Create: `tests/test_output_capture.py`

- [ ] **Step 1: Write failing direct `with_auto_capture()` unit test**

Create `tests/test_output_capture.py` and test capture behavior without portal code, so this task only depends on `trail/output/capture.py`:

```python
from trail.output.capture import with_auto_capture


class Runtime:
    def __init__(self):
        self.capture_after_action_calls = 0
        self.reference_screenshots = []

    def capture_after_action(self, optional: bool = False, request_id: str | None = None):
        self.capture_after_action_calls += 1
        return "unexpected.png"

    def collect_warnings(self):
        return [{"code": "RUNTIME_WARNING", "message": "runtime warning"}]

    def match_references(self, screenshot_path, limit: int = 3):
        self.reference_screenshots.append(str(screenshot_path))
        return [{"path": str(screenshot_path), "similarity": 1.0}]


def test_with_auto_capture_consumes_precaptured_screenshot_success():
    runtime = Runtime()

    response = with_auto_capture(runtime, lambda: {"value": 1, "_screenshot": "slots-reused.png"})

    assert response["ok"] is True
    assert response["screenshot"] == "slots-reused.png"
    assert response["data"] == {"value": 1}
    assert response["warnings"] == [{"code": "RUNTIME_WARNING", "message": "runtime warning"}]
    assert response["references"] == [{"path": "slots-reused.png", "similarity": 1.0}]
    assert runtime.reference_screenshots == ["slots-reused.png"]
    assert runtime.capture_after_action_calls == 0
```

- [ ] **Step 2: Run test and verify failure**

Run: `uv run pytest tests/test_output_capture.py -q`.

Expected: fail because `with_auto_capture()` captures after action and does not pop `_screenshot`.

- [ ] **Step 3: Implement `with_auto_capture()` success pop**

In `trail/output/capture.py`, change success path:

```python
        data, screenshot = _pop_precaptured_screenshot(data)
        if screenshot is None:
            screenshot = _capture_screenshot(resolved_runtime, optional=False)
        metadata = _safe_collect_capture_metadata(resolved_runtime, screenshot=screenshot, verbose=effective_verbose)
        return command_success(
            data=data,
            screenshot=screenshot,
            timing={"elapsed_ms": int((perf_counter() - started) * 1000)},
            **metadata,
        )
```

Do not change failure handling.

- [ ] **Step 4: Run focused capture tests**

Run: `uv run pytest tests/test_output_capture.py tests/test_daemon_protocol.py::test_command_service_handles_cw_portal_select_waits_extra_before_capture -q`

Expected: new precapture test passes; existing portal delay capture test still passes when no `_screenshot` is provided.

- [ ] **Step 5: Checkpoint**

Run: `rtk git status --short`

Expected: capture and daemon protocol tests changed only for this task.

---

### Task 5: Daemon Resource Service Integration

**Files:**
- Modify: `trail/daemon/cw_resource_service.py`
- Create: `tests/test_cw_resource_service.py`

- [ ] **Step 1: Write failing service cache test**

Create `tests/test_cw_resource_service.py` with a focused cache test:

```python
def test_cw_resource_service_caches_role_recognizer(tmp_path):
    from trail.daemon.cw_resource_service import CwResourceService

    calls = []

    class RoleRecognizer:
        @classmethod
        def from_precomputed_features(cls, payload, *, empty_templates):
            calls.append((payload, empty_templates))
            return {"recognizer": len(calls)}

    bundle = SimpleNamespace(
        raw_config={"rpg_game_big_version": "3.2"},
        guide_config_enriched={"roles": [], "traits": []},
        role_features={"items": []},
        role_manifest={"empty_templates": {"field": {"local_path": "roles/empty/field-v1.png"}, "hand": {"local_path": "roles/empty/hand-v1.png"}}, "items": []},
        root=tmp_path,
        equipment_features={"items": [], "equipment_feature_schema_version": 1, "recognizer_algorithm_version": "vector-mask-v1", "feature_size": [32, 32], "match_size": [64, 64], "min_score": 0.72, "min_gap": 0.05},
        manifest={"bundle_schema_version": 1, "resource_version": "3.2"},
        source_kind="package",
        manifest_path="manifest.json",
        equipment_manifest_path="equipment/manifest.json",
        role_manifest_path="roles/manifest.json",
        manifest_mtime=1,
        equipment_manifest_mtime=1,
        role_manifest_mtime=1,
        override_manifest_path=None,
        override_manifest_mtime=0,
        override_identity="",
        identity="bundle-1",
        big_version="3.2",
    )
    service = CwResourceService(
        bundle_loader=lambda workspace_root=None: bundle,
        source_signature=lambda workspace_root=None: ("sig",),
        role_recognizer_cls=RoleRecognizer,
    )

    first = service.slots_read_resources(workspace_root=tmp_path)[2]
    second = service.slots_read_resources(workspace_root=tmp_path)[2]

    assert first is second
    assert len(calls) == 1
```

Extract the setup in the first test into `_role_service_fixture(tmp_path)` returning `(service, bundle, calls)`, then add two more tests in the same file:

```python
def test_cw_resource_service_invalidates_role_recognizer(tmp_path):
    service, bundle, calls = _role_service_fixture(tmp_path)

    first = service.slots_read_resources(workspace_root=tmp_path)[2]
    service.invalidate_workspace(workspace_root=tmp_path)
    second = service.slots_read_resources(workspace_root=tmp_path)[2]

    assert first is not second
    assert len(calls) == 2


def test_cw_resource_service_slots_resources_do_not_prepare_or_download(monkeypatch, tmp_path):
    service, bundle, calls = _role_service_fixture(tmp_path)
    monkeypatch.setenv("TRAIL_CW_RESOURCE_DEV_FALLBACK", "1")
    monkeypatch.setattr("trail.daemon.cw_resource_service.prepare_equipment_icon_cache", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prepare not allowed")))
    monkeypatch.setattr("trail.daemon.cw_resource_service.load_cached_equipment_icons", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache load not allowed")))
    monkeypatch.setattr("trail.scenes.cw.role_resources.download_role_icon_bytes", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("role download not allowed")))

    raw_config, guide_config, recognizer = service.slots_read_resources(workspace_root=tmp_path)

    assert raw_config == bundle.raw_config
    assert guide_config == bundle.guide_config_enriched
    assert recognizer == {"recognizer": 1}
```

- [ ] **Step 2: Run test and verify failure**

Run: `uv run pytest tests/test_cw_resource_service.py -q` or the selected daemon test node.

Expected: fail because service lacks role recognizer APIs.

- [ ] **Step 3: Implement resource service APIs**

In `trail/daemon/cw_resource_service.py`:

- Import `VectorRoleIconRecognizer`.
- Add `role_recognizer_cls` constructor argument.
- Add `_role_recognizers` dict.
- Extend `_bundle_key()` with `role_manifest_path` and `role_manifest_mtime`.
- Implement `role_recognizer_for_bundle()` so it loads `field` and `hand` empty templates from `bundle.root` plus `bundle.role_manifest["empty_templates"]`, then calls `role_recognizer_cls.from_precomputed_features(bundle.role_features, empty_templates=templates)`.
- Implement `slots_read_resources()` returning `(bundle.raw_config, bundle.guide_config_enriched, role_recognizer)`.
- Clear `_role_recognizers` in `invalidate_workspace()`.

- [ ] **Step 4: Run focused service tests**

Run: `uv run pytest tests/test_cw_resource_service.py tests/test_daemon_protocol.py::test_command_service_uses_cached_cw_equipment_recognizer -q`

Expected: role service test passes and equipment recognizer cache test still passes.

- [ ] **Step 5: Checkpoint**

Run: `rtk git status --short`

Expected: resource service and tests changed.

---

### Task 6: Slots Reader Integration

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_cw_slots.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: Write failing screenshot propagation unit test**

In `tests/test_cw_slots.py`, add:

```python
def test_slots_read_response_snapshot_carries_precaptured_screenshot(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots")
    result_cls = getattr(slots_module, "CwSlotsReadResult")
    session = build_fake_cw_session(tmp_path)

    refreshed = read_cw_slots(
        session,
        reader=lambda: result_cls(
            front=[{"name": "希儿", "star": 1}, None, None, None],
            back=[None] * 6,
            hand=[None] * 9,
            screenshot=str(tmp_path / ".trail" / "shots" / "slots.png"),
        ),
        guide_config={"traits": [], "roles": [{"id": "1001", "name": "希儿", "trait_ids": []}]},
    )

    assert refreshed.response_snapshot["_screenshot"].endswith("slots.png")
    assert "_screenshot" not in refreshed.scene_state["cw"]["slots"]
```

- [ ] **Step 2: Write failing unknown behavior tests**

Use a sentinel dict from reader:

```python
unknown = {"match_kind": "unknown", "raw_name": "", "score": 0.12}
```

Add tests:

```python
def test_slots_read_unknown_full_snapshot_fails_without_marking_empty(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots")
    session = build_fake_cw_session(tmp_path)
    previous_slots = {"front": [{"name": "希儿"}], "back": [None] * 6, "hand": [None] * 9, "stale": False}
    session.scene_state["cw"]["slots"] = deepcopy(previous_slots)

    with pytest.raises(TrailError) as exc_info:
        read_cw_slots(session, reader=lambda: ([{"match_kind": "unknown", "score": 0.12}, None, None, None], [None] * 6, [None] * 9))

    assert exc_info.value.code == "SLOTS_RECOGNITION_UNCERTAIN"
    assert session.scene_state["cw"]["slots"] == previous_slots
```

```python
def test_slots_read_unknown_target_preserves_fresh_previous(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots")
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {
        "front": [{"name": "希儿", "role_id": "1001", "star": 1}],
        "back": [None] * 6,
        "hand": [None] * 9,
        "stale": False,
    }

    refreshed = read_cw_slots(
        session,
        reader=lambda: ([{"match_kind": "unknown", "score": 0.12}, None, None, None], [None] * 6, [None] * 9),
        targets=["front:0"],
    )

    assert refreshed.response_snapshot["front"][0]["name"] == "希儿"
    assert refreshed.response_snapshot["warnings"][0]["preserved_previous"] == 1
    assert refreshed.scene_state["cw"]["slots"]["front"][0]["name"] == "希儿"


def test_slots_read_unknown_target_with_stale_previous_fails(tmp_path):
    slots_module = load_cw_slots_module()
    read_cw_slots = getattr(slots_module, "read_cw_slots")
    session = build_fake_cw_session(tmp_path)
    session.scene_state["cw"]["slots"] = {"front": [{"name": "希儿"}], "back": [None] * 6, "hand": [None] * 9, "stale": True}

    with pytest.raises(TrailError) as exc_info:
        read_cw_slots(
            session,
            reader=lambda: ([{"match_kind": "unknown", "score": 0.12}, None, None, None], [None] * 6, [None] * 9),
            targets=["front:0"],
        )

    assert exc_info.value.code == "SLOTS_RECOGNITION_UNCERTAIN"
    assert session.scene_state["cw"]["slots"]["stale"] is True
```

- [ ] **Step 3: Run tests and verify failure**

Run: `uv run pytest tests/test_cw_slots.py::test_slots_read_response_snapshot_carries_precaptured_screenshot tests/test_cw_slots.py::test_slots_read_unknown_full_snapshot_fails_without_marking_empty tests/test_cw_slots.py::test_slots_read_unknown_target_preserves_fresh_previous tests/test_cw_slots.py::test_slots_read_unknown_target_with_stale_previous_fails -q`

Expected: fail because result lacks screenshot and unknown handling.

- [ ] **Step 4: Implement `CwSlotsReadResult` screenshot and unknown handling**

In `trail/scenes/cw/slots.py`:

- Add `screenshot: str | None = None` to `CwSlotsReadResult`.
- In `read_cw_slots()`, capture `screenshot` from result and add `_screenshot` to response snapshot after canonicalization.
- Add helper `_is_unknown_slot_value(value)` checking dict `match_kind == "unknown"`.
- Handle unknown immediately after `reader()` returns and before `_canonicalize_area_snapshot()`, `_slots_read_empty_snapshot()`, merge, or trait summary. If unknown exists in parsed targets, either preserve fresh previous for targeted reads or raise `CwSlotsRecognitionUncertainError("SLOTS_RECOGNITION_UNCERTAIN", "槽位角色图标识别不确定，请先看截图确认", screenshot=screenshot, warnings=unknown_warnings)`.
- Add warning payload with `preserved_previous=1` for preserved targeted unknown.
- Extend `SLOT_MATCH_DIAGNOSTIC_KEYS` and `_strip_area_match_diagnostics()` coverage to remove `candidates`, `empty_score`, `fee_color`, `star_boxes`, and `confidence_reason` from persisted `cw_state["slots"]` and preserved response projections.

- [ ] **Step 5: Implement icon slots reader factory**

In `slots.py`, add `build_cw_slot_icon_reader(runtime, recognizer, targets=None, request_id=None, dismiss_initial_overlay=True)`. It must:

- Optionally dismiss initial overlay using existing overlay helper.
- Capture `runtime.capture_image(normalize=True)` once.
- Fail with `TrailError("SLOTS_LAYOUT_MISMATCH", "slots icon reader requires canonical 1920x1080 screenshot")` if the captured image is not `1920x1080`.
- Save with `save_capture_image_to_workspace(image, request_id=request_id)` when available.
- Return `CwSlotsReadResult(front=recognized_front, back=recognized_back, hand=recognized_hand, stage_status=stage_status, stage=detected_stage, screenshot=screenshot_path)`.

Keep legacy OCR reader behind a private gate or rename it to make default path explicit. Add tests that default `cw.slots.read` does not call逐槽详情点击、`SLOT_NAME_REGION`、姓名 OCR or legacy reader; allow overlay dismiss and stage/status OCR.

- [ ] **Step 6: Wire daemon `cw.slots.read`**

In `cw_service.py`, change handler to fetch `raw_config`, enriched guide config, and role recognizer from `CwResourceService.slots_read_resources()`. Pass `request_id` to `slots_reader_factory` or new icon reader factory.

Add a direct daemon `cw.slots.read` test that returns `_screenshot` from the reader and asserts `payload["screenshot"]` is the reused path, `_screenshot` is absent from `payload["data"]`, and `capture_after_action()` is not called. This covers the selective capture path separately from portal mutation capture.

Add a direct daemon failure test for `SLOTS_RECOGNITION_UNCERTAIN`: make the reader return `CwSlotsReadResult(front=[{"match_kind": "unknown", "score": 0.12}, None, None, None], back=[None] * 6, hand=[None] * 9, screenshot="slots-uncertain.png")`. Assert `read_cw_slots()` raises `CwSlotsRecognitionUncertainError`, the daemon failure envelope uses `slots-uncertain.png`, includes the warning detail, and does not call `capture_after_action()` after failure.

Implement this by adding `CwSlotsRecognitionUncertainError(TrailError)` in `slots.py` with `screenshot` and `warnings` attributes, and by extending `with_selective_capture()` failure handling to prefer `getattr(exc, "screenshot", None)` over `_capture_optional_screenshot()` when present. Keep generic `TrailError` failure behavior unchanged.

- [ ] **Step 7: Run focused slots tests**

Run: `uv run pytest tests/test_cw_slots.py -q`

Expected: all slots tests pass.

- [ ] **Step 8: Checkpoint**

Run: `rtk git status --short`

Expected: slots integration files and tests changed.

---

### Task 7: Portal Auto-Collect Integration

**Files:**
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: Write failing portal request_id test**

Update the existing test `tests/test_daemon_protocol.py::test_cw_portal_select_preserves_auto_collect_response_diagnostics_and_promotes_warnings`, and add a new focused test named `test_command_service_uses_portal_slots_precaptured_screenshot`. Monkeypatch `slots_reader_factory` or the new icon reader factory to assert `request_id == "req-cw-portal-select-diagnostics"` and return a snapshot with `_screenshot`. Ensure `runtime_service.get_runtime` returns the same runtime object for `CommandService` and `CwService`, so `capture_after_action_calls` is observable.

Expected assertions:

```python
assert payload["screenshot"].endswith("slots-reused.png")
assert "_screenshot" not in payload["data"]
assert "_screenshot" not in payload["data"]["slots"]
assert payload["warnings"] == [slot_warning, shop_warning]
```

Add a second portal warning-order test where runtime warnings, selected-data warnings, slot warnings, equipment warnings, and shop warnings are all present. Assert the final envelope order is stable: existing selected warning, slot warning, equipment warning, shop warning, then runtime capture warning if current merge helpers append runtime warnings last. If current helper order differs, preserve the existing helper order and freeze it in the assertion.

Add a third portal auto-collect uncertain test: make `read_cw_slots()` raise `TrailError("SLOTS_RECOGNITION_UNCERTAIN", "槽位角色图标识别不确定，请先看截图确认")` after portal select/apply succeeds. Assert `cw.portal.select` still returns `ok=True`, includes `warn code=CW_SLOTS_AUTO_COLLECT_UNCERTAIN`, does not output empty slots, and continues equipment/shop collection. If a fresh previous slots snapshot exists, assert warning has `preserved_previous=1` and projected slots come from previous snapshot.

- [ ] **Step 2: Run test and verify failure**

Run: `uv run pytest tests/test_daemon_protocol.py::test_cw_portal_select_preserves_auto_collect_response_diagnostics_and_promotes_warnings -q`

Expected: fail until portal path forwards request_id and strips nested `_screenshot`.

- [ ] **Step 3: Implement portal screenshot promotion**

In `_select_portal_and_apply_selected_guide()`:

- Accept `request_id: str | None = None`.
- Pass `request_id` to slots reader factory.
- After `slots_snapshot = _response_snapshot_or_fallback(slots_result, ensure_cw_state(session).get("slots") or {})`, do `slots_screenshot = slots_snapshot.pop("_screenshot", None)`.
- If `slots_screenshot` is not `None` and `selected_data` has no existing `_screenshot`, set `selected_data["_screenshot"] = slots_screenshot`.
- Keep warnings pop after screenshot pop.
- Catch `TrailError` with code `SLOTS_RECOGNITION_UNCERTAIN` around auto-collect slots only, append `CW_SLOTS_AUTO_COLLECT_UNCERTAIN` warning, and continue equipment/shop collection. Do not catch unrelated slots errors.

In `run_portal_select()`, pass outer `request_id` to `_select_portal_and_apply_selected_guide()`.

- [ ] **Step 4: Run portal tests**

Run: `uv run pytest tests/test_daemon_protocol.py::test_cw_portal_select_preserves_auto_collect_response_diagnostics_and_promotes_warnings tests/test_daemon_protocol.py::test_command_service_uses_portal_slots_precaptured_screenshot tests/test_daemon_protocol.py::test_cw_portal_select_slots_uncertain_is_soft_auto_collect_warning -q`

Expected: both tests pass.

- [ ] **Step 5: Checkpoint**

Run: `rtk git status --short`

Expected: portal path and daemon tests changed.

---

### Task 8: Output, Verbose, YAML, And Skill Docs

**Files:**
- Modify: `tests/test_output_rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: `skills/trail-cw-entry/SKILL.md`
- Modify: `skills/trail-cw-portal/SKILL.md`

- [ ] **Step 1: Write output contract tests**

Add or update renderer tests to assert:

- `role_id` is absent from default `slot` lines.
- Low confidence warning renders with `pos=front:1` and message.
- Default mode does not include `debug kind=trace` or diagnostic fields.

Expected line sample:

```python
assert "slot pos=front:1 name=缇宝 star=2 traits=昼之半神,群攻" in output
assert "role_id" not in "\n".join(output)
assert "debug kind=trace" not in "\n".join(output)
```

- [ ] **Step 2: Write YAML allowlist test**

In `tests/test_cw_rpc_contracts.py`, follow the existing command invocation helper style in that file and assert `cw.slots.read` still rejects YAML format. If the file uses `CommandService` fixtures instead of `cli_runner`, use the existing fixture rather than adding a new CLI harness:

```python
def test_cw_slots_read_rejects_yaml_format(cli_runner, tmp_path):
    result = cli_runner.invoke(["--format", "yaml", "cw", "slots", "read", "--session", "s1"])
    assert "OUTPUT_FORMAT_NOT_SUPPORTED" in result.output
```

Adapt to the repository's existing CLI helper style in that file.

- [ ] **Step 3: Run tests and verify failure or pass**

Run: `uv run pytest tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -q`

Expected: output tests fail only if renderer leaks new fields; YAML test should pass if current contract already rejects it.

- [ ] **Step 4: Adjust renderer only if tests show leakage**

If `role_id` appears in default text, remove it from `_append_cw_slot_lines()`. Do not add a new prefix or change slot ordering.

- [ ] **Step 5: Update active skill docs**

In `skills/trail-cw-prep/SKILL.md` and `skills/trail-cw-prep/references/command-surface.md`, add concise guidance:

```markdown
`cw.slots.read` 默认使用角色图标识别，不再逐槽位点击详情读取姓名。带截图 success 后必须先读本次 `shot path` 指向的截图，再消费 `slot`、羁绊和装备/商店事实；若出现 `CW_ROLE_MATCH_LOW_CONFIDENCE` 或 `SLOTS_RECOGNITION_UNCERTAIN`，先核对截图再做换位、出售或购买决策。
```

In `skills/trail-cw-entry/SKILL.md` and `skills/trail-cw-portal/SKILL.md`, add one sentence that `cw.portal.select` auto-collect may report `CW_SLOTS_AUTO_COLLECT_UNCERTAIN`; the skill should continue from portal facts, read the screenshot first, and refresh slots only if needed.

- [ ] **Step 6: Run docs and output focused checks**

Run: `uv run pytest tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -q`

Expected: pass.

- [ ] **Step 7: Checkpoint**

Run: `rtk git status --short`

Expected: renderer tests, RPC tests, and active skill docs changed.

---

### Task 9: End-To-End Verification

**Files:**
- No source edits unless verification finds failures.

- [ ] **Step 1: Run focused resource and slots suites**

Run: `uv run pytest tests/test_cw_static_resources.py tests/test_build_cw_resource_bundle.py tests/test_release_build.py tests/test_cw_role_recognition.py tests/test_cw_slots.py -q`

Expected: pass.

- [ ] **Step 2: Run daemon/output suites**

Run: `uv run pytest tests/test_daemon_protocol.py tests/test_output_rendering.py tests/test_cw_rpc_contracts.py -q`

Expected: pass.

- [ ] **Step 3: Run full quick regression**

Run: `uv run pytest`

Expected: all non-slow tests pass with existing skipped count.

- [ ] **Step 4: Verify release artifacts if build output is available**

Run: `uv run python scripts\verify-cw-resource-bundle-artifacts.py dist`

Expected: `ok cw_resource_bundle_artifacts count=实际产物数量` when `dist` contains artifacts. If `dist` is absent, record that artifact verification was not runnable in this workspace.

- [ ] **Step 5: Final workspace check**

Run: `rtk git status --short`

Expected: only intended implementation, tests, generated resources, assets, and skill docs changed.

- [ ] **Step 6: Completion report**

Report:

- Spec path implemented: `docs/superpowers/specs/2026-05-01-cw-slots-icon-recognition-design.md`.
- Key behavior changed: bundle-first role icon recognition, no default slot detail OCR, screenshot reuse, portal auto-collect compatibility.
- Verification commands and exact pass/fail results.
- No commit created unless the user explicitly requested one.
