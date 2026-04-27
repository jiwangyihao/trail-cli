# CW Catalog Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 CW 角色 OCR canonical 匹配、羁绊档位来源、shop/slots 羁绊展示，并把 Agent 可见槽位编号统一为 1-based。

**Architecture:** 新增纯函数 `trail/scenes/cw/catalog.py` 作为角色与羁绊 catalog 边界；`guide.py` 负责 raw config、raw 攻略列表和 enriched cache；`slots.py`、`shop.py` 只消费 catalog 结果并返回当前命令诊断；renderer 和 CLI 层负责 Agent 可见 `score`、warnings 和 1-based 适配。

**Tech Stack:** Python 3.12、pytest、Typer CLI、现有 daemon/session/envelope/output renderer、现有 CW guide/list/config API helper。

---

## 工作区与约束

- 开发 worktree: `C:\Users\34404\source\repos\trail-cli\.worktrees\cw-catalog-matching`
- 主仓库: `C:\Users\34404\source\repos\trail-cli`
- Spec: `docs/superpowers/specs/2026-04-26-cw-catalog-matching-design.md`
- Plan: `docs/superpowers/plans/2026-04-26-cw-catalog-matching.md`
- pytest 命令统一追加 `--basetemp .pytest-tmp`。
- 不新增正文前缀，不扩大 YAML allowlist。
- 默认文本使用 `score=...`，不输出 `match_score=...`。
- `raw_name/match_score/match_kind` 是当前 response/envelope 诊断，不写入 session。
- CLI 和默认文本使用 1-based；daemon/RPC/session 内部继续 0-based。
- 不创建 git commit，除非用户明确要求；任务结束以 `git status`、测试和 review 作为交付检查。

## 文件结构

- Create: `trail/scenes/cw/catalog.py`。纯函数 catalog、trait entry 清洗合并、角色 matching、slot/shop canonical 化、trait summary。
- Create: `tests/test_cw_catalog.py`。catalog helper 单元测试。
- Modify: `trail/scenes/cw/guide.py`。`fetch_cw_guide_config(enrich_traits=False)`、raw 攻略列表 enrichment、enriched cache。
- Modify: `tests/test_cw_guide.py`。guide config enrichment/cache/YAML 测试。
- Modify: `trail/daemon/cw_service.py`。scene warning 提升、shop/slots handler response snapshot、config enrich 边界。
- Modify: `trail/output/rendering.py`。slot/item `raw_name/score/match_kind/traits`、warning `pos/slot/idx/candidates/score`、1-based slot rendering。
- Modify: `trail/commands/cw.py`。CLI slot 参数从 1-based 转内部 0-based；CLI `0` 返回 `CW_OPTION_INVALID`。
- Modify: `trail/scenes/cw/slots.py`。使用 catalog canonical 化角色、trait summary、response diagnostics、failure 1-based 语义。
- Modify: `tests/test_cw_slots.py`。slots canonical、summary、1-based、warning promotion、role_count 测试。
- Modify: `trail/scenes/cw/shop.py`。shop item canonical、buy_slot canonical expect/confirm、fresh slots field summary、诊断不持久化。
- Modify: `tests/test_cw_shop.py`。shop canonical/status/buy_slot/buy_exp/summary/enrich 边界测试。
- Modify: `tests/test_output_rendering.py`、`tests/test_daemon_protocol.py`、`tests/test_output_debug.py`。renderer、daemon/RPC、docs 协议测试。
- Modify: `README.md`、`AGENTS.md`、`skills/trail-cw-prep/SKILL.md`、`skills/trail-cw-prep/references/command-surface.md`、受影响的 `trail-cw-entry` / `trail-cw-guide` / `trail-hsr` command surface 文档。

## Task 1: Catalog Helper

**Files:**
- Create: `trail/scenes/cw/catalog.py`
- Create: `tests/test_cw_catalog.py`

- [ ] **Step 1: Write failing catalog tests**

Create `tests/test_cw_catalog.py` with focused tests for role matching, trait cleaning, trait merge, and summary.

```python
from __future__ import annotations

from trail.scenes.cw.catalog import (
    build_cw_catalog,
    clean_cw_trait_entry,
    merge_cw_trait_entries,
    resolve_cw_role_name,
    summarize_cw_field_traits,
)


def _config():
    return {
        "traits": [
            {"id": "1004", "name": "公司", "layers": [{"layer": 2}, {"layer": 3}]},
            {"id": "1007", "name": "仙舟", "layers": [{"layer": 3}, {"layer": 5}, {"layer": 7}, {"layer": 10}]},
            {"id": "2002", "name": "击破", "layers": [{"layer": 2}, {"layer": 4}, {"layer": 6}, {"layer": 8}, {"layer": 10}]},
        ],
        "roles": [
            {"id": "1502", "name": "爻光", "trait_ids": ["1007"]},
            {"id": "1304", "name": "砂金", "trait_ids": ["1004"]},
            {"id": "1222", "name": "忘归人", "trait_ids": ["1007", "2002"]},
        ],
    }


def test_resolve_role_name_always_returns_config_role_for_non_empty_text():
    catalog = build_cw_catalog(_config())

    match = resolve_cw_role_name("交光", catalog, position={"kind": "slot", "area": "front", "index": 1})

    assert match.name == "爻光"
    assert match.role_id == "1502"
    assert match.raw_name == "交光"
    assert match.match_kind == "low_confidence"
    assert match.match_score == 0.5
    assert match.traits == ["仙舟"]
    assert match.warning is not None
    assert match.warning["code"] == "CW_ROLE_MATCH_LOW_CONFIDENCE"


def test_exact_role_match_has_no_diagnostics():
    catalog = build_cw_catalog(_config())

    match = resolve_cw_role_name("砂金", catalog, position={"kind": "slot", "area": "front", "index": 3})

    assert match.name == "砂金"
    assert match.raw_name is None
    assert match.match_kind == "exact"
    assert match.warning is None


def test_clean_trait_entry_keeps_stable_fields_and_strips_current_state():
    cleaned = clean_cw_trait_entry(
        {
            "trait_id": "2002",
            "trait_name": "击破",
            "trait_icon": "break.png",
            "trait_type": 1,
            "current_role_count": 4,
            "layers": [
                {"layer": 2, "quality": 0, "is_activated": True, "trait_desc": "二层"},
                {"layer": "4", "quality": 1, "is_activated": False, "trait_desc": "四层"},
            ],
            "remarks": [{"remark": "说明", "position": 0}],
            "role_ids": [1222, "1315"],
            "simple_desc": "击破说明",
        }
    )

    assert cleaned == {
        "id": "2002",
        "name": "击破",
        "icon": "break.png",
        "type": 1,
        "simple_desc": "击破说明",
        "remarks": [{"remark": "说明", "position": 0}],
        "role_ids": ["1222", "1315"],
        "layers": [
            {"layer": 2, "quality": 0, "trait_desc": "二层"},
            {"layer": 4, "quality": 1, "trait_desc": "四层"},
        ],
    }


def test_merge_trait_entries_unions_layers_remarks_and_role_ids():
    merged = merge_cw_trait_entries(
        {"id": "1004", "name": "公司", "layers": [{"layer": 2, "trait_desc": "二层"}], "role_ids": ["1304"]},
        {"id": "1004", "name": "公司", "layers": [{"layer": 3, "trait_desc": "三层"}], "role_ids": ["1304", "1008"]},
    )

    assert merged["layers"] == [{"layer": 2, "trait_desc": "二层"}, {"layer": 3, "trait_desc": "三层"}]
    assert merged["role_ids"] == ["1008", "1304"]


def test_summarize_field_traits_uses_enriched_layers_and_does_not_infer_missing_layers():
    catalog = build_cw_catalog(_config())
    front = [{"name": "砂金", "traits": ["公司"]}, {"name": "忘归人", "traits": ["仙舟", "击破"]}]

    summary = summarize_cw_field_traits(front=front, back=[], catalog=catalog)

    by_trait = {item["trait"]: item for item in summary}
    assert by_trait["公司"]["tiers"] == [2, 3]
    assert by_trait["公司"]["active_tier"] == 0
    assert by_trait["仙舟"]["tiers"] == [3, 5, 7, 10]
    assert by_trait["击破"]["tiers"] == [2, 4, 6, 8, 10]
```

- [ ] **Step 2: Run catalog tests and verify failure**

Run: `uv run pytest tests/test_cw_catalog.py -q --basetemp .pytest-tmp`

Expected: FAIL with `ModuleNotFoundError: No module named 'trail.scenes.cw.catalog'`.

- [ ] **Step 3: Implement catalog helper**

Create `trail/scenes/cw/catalog.py` with dataclasses and pure functions. Keep it free of network/cache imports.

```python
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any, Mapping

LOOKUP_WHITESPACE_PATTERN = re.compile(r"\s+")
LOW_CONFIDENCE_THRESHOLD = 0.72
AMBIGUOUS_DELTA_THRESHOLD = 0.12


@dataclass(frozen=True)
class CwCatalog:
    roles: list[dict[str, Any]]
    traits: list[dict[str, Any]]
    trait_name_by_id: dict[str, str]
    trait_tiers_by_name: dict[str, list[int]]
    role_traits_by_name: dict[str, list[str]]


@dataclass(frozen=True)
class CwRoleMatch:
    name: str
    role_id: str | None
    traits: list[str]
    match_kind: str
    match_score: float
    raw_name: str | None = None
    warning: dict[str, Any] | None = None


def normalize_cw_lookup_text(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return LOOKUP_WHITESPACE_PATTERN.sub(" ", text.strip().lower()).strip()


def _coerce_positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def clean_cw_trait_entry(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    trait_id = entry.get("trait_id", entry.get("id"))
    name = str(entry.get("trait_name") or entry.get("name") or "").strip()
    if not name:
        return None
    cleaned: dict[str, Any] = {"name": name}
    if trait_id is not None:
        cleaned["id"] = str(trait_id)
    icon = entry.get("trait_icon", entry.get("icon"))
    if icon:
        cleaned["icon"] = icon
    if entry.get("trait_type", entry.get("type")) is not None:
        cleaned["type"] = entry.get("trait_type", entry.get("type"))
    if entry.get("simple_desc"):
        cleaned["simple_desc"] = entry.get("simple_desc")
    if isinstance(entry.get("remarks"), list):
        cleaned["remarks"] = list(entry["remarks"])
    role_ids = [str(item) for item in entry.get("role_ids") or [] if item is not None]
    if role_ids:
        cleaned["role_ids"] = sorted(dict.fromkeys(role_ids))
    layers = []
    for raw in entry.get("layers") or []:
        if not isinstance(raw, Mapping):
            continue
        layer = _coerce_positive_int(raw.get("layer"))
        if layer is None:
            continue
        item = {"layer": layer}
        if raw.get("quality") is not None:
            item["quality"] = raw.get("quality")
        if raw.get("trait_desc"):
            item["trait_desc"] = raw.get("trait_desc")
        layers.append(item)
    if layers:
        cleaned["layers"] = sorted({item["layer"]: item for item in layers}.values(), key=lambda item: item["layer"])
    return cleaned


def merge_cw_trait_entries(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key in ("id", "name", "icon", "type", "simple_desc"):
        if not merged.get(key) and right.get(key):
            merged[key] = right[key]
    role_ids = [str(item) for item in merged.get("role_ids") or []] + [str(item) for item in right.get("role_ids") or []]
    if role_ids:
        merged["role_ids"] = sorted(dict.fromkeys(role_ids))
    remarks = list(merged.get("remarks") or [])
    for item in right.get("remarks") or []:
        if item not in remarks:
            remarks.append(item)
    if remarks:
        merged["remarks"] = remarks
    by_layer = {item.get("layer"): dict(item) for item in merged.get("layers") or [] if item.get("layer") is not None}
    for item in right.get("layers") or []:
        layer = item.get("layer")
        if layer is None:
            continue
        existing = by_layer.get(layer)
        if existing is None or len(item) > len(existing):
            by_layer[layer] = dict(item)
    if by_layer:
        merged["layers"] = [by_layer[key] for key in sorted(by_layer)]
    return merged


def build_cw_catalog(guide_config: Mapping[str, Any] | None) -> CwCatalog:
    data = guide_config if isinstance(guide_config, Mapping) else {}
    traits = [item for item in (clean_cw_trait_entry(raw) for raw in data.get("traits") or []) if item]
    trait_name_by_id = {str(item["id"]): str(item["name"]) for item in traits if item.get("id") is not None}
    trait_tiers_by_name = {
        str(item["name"]): [int(layer["layer"]) for layer in item.get("layers") or [] if layer.get("layer") is not None]
        for item in traits
        if item.get("layers")
    }
    roles: list[dict[str, Any]] = []
    role_traits_by_name: dict[str, list[str]] = {}
    for raw in data.get("roles") or []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        trait_ids = [str(item) for item in raw.get("trait_ids") or []]
        role_traits = [trait_name_by_id[item] for item in trait_ids if item in trait_name_by_id]
        role = {"id": str(raw.get("id")) if raw.get("id") is not None else None, "name": name, "normalized_name": normalize_cw_lookup_text(name), "traits": role_traits}
        roles.append(role)
        role_traits_by_name[name] = role_traits
    return CwCatalog(roles=roles, traits=traits, trait_name_by_id=trait_name_by_id, trait_tiers_by_name=trait_tiers_by_name, role_traits_by_name=role_traits_by_name)


def resolve_cw_role_name(raw_name: object, catalog: CwCatalog, *, position: Mapping[str, Any] | None = None) -> CwRoleMatch | None:
    text = str(raw_name or "").strip()
    if not text:
        return None
    normalized = normalize_cw_lookup_text(text)
    ranked = []
    for role in catalog.roles:
        role_name = str(role.get("name") or "")
        role_normalized = str(role.get("normalized_name") or "")
        score = SequenceMatcher(a=normalized, b=role_normalized).ratio() if normalized and role_normalized else 0.0
        ranked.append((score, role_name, role))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    best_score, _, best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    exact = normalized == best.get("normalized_name")
    if exact:
        return CwRoleMatch(name=str(best["name"]), role_id=best.get("id"), traits=list(best.get("traits") or []), match_kind="exact", match_score=1.0)
    ambiguous = best_score - second_score < AMBIGUOUS_DELTA_THRESHOLD
    match_kind = "ambiguous" if ambiguous else "low_confidence" if best_score < LOW_CONFIDENCE_THRESHOLD else "fuzzy"
    candidates = [f"{role['name']}:{score:.2f}" for score, _, role in ranked[:3]]
    warning = {
        "code": "CW_ROLE_MATCH_AMBIGUOUS" if ambiguous else "CW_ROLE_MATCH_LOW_CONFIDENCE" if match_kind == "low_confidence" else "CW_ROLE_MATCH_FUZZY",
        "position": dict(position or {}),
        "query": text,
        "resolved": best["name"],
        "score": round(best_score, 2),
        "candidates": candidates,
        "message": "角色名未精确命中，请先看截图确认",
    }
    return CwRoleMatch(name=str(best["name"]), role_id=best.get("id"), traits=list(best.get("traits") or []), match_kind=match_kind, match_score=round(best_score, 2), raw_name=text, warning=warning)


def summarize_cw_field_traits(*, front: list[Any], back: list[Any], catalog: CwCatalog) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in list(front) + list(back):
        if not isinstance(value, Mapping):
            continue
        for trait in value.get("traits") or []:
            counts[str(trait)] = counts.get(str(trait), 0) + 1
    summary = []
    for trait, owned in counts.items():
        tiers = catalog.trait_tiers_by_name.get(trait)
        if not tiers:
            continue
        active = 0
        for tier in tiers:
            if owned >= tier:
                active = tier
        summary.append({"trait": trait, "tiers": tiers, "owned_roles": owned, "active_tier": active, "total_tiers": len(tiers), "ratio": round(active / len(tiers), 2)})
    return sorted(summary, key=lambda item: (-item["ratio"], -item["owned_roles"], item["trait"]))[:10]
```

- [ ] **Step 4: Run catalog tests and verify pass**

Run: `uv run pytest tests/test_cw_catalog.py -q --basetemp .pytest-tmp`

Expected: PASS.

## Task 2: Guide Config Enrichment And Cache

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Modify: `tests/test_cw_guide.py`

- [ ] **Step 1: Write failing guide enrichment tests**

Append tests to `tests/test_cw_guide.py` for explicit enrichment, default no-enrichment, raw list usage, cache, and YAML shape.

```python
def test_fetch_cw_guide_config_enriches_trait_layers_from_raw_lineup_list(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    payload = fake_cw_config_response()
    payload["data"]["trait_info_list"] = [
        {"trait_id": "1004", "trait_name": "公司", "layers": []},
        {"trait_id": "1007", "trait_name": "仙舟", "layers": []},
        {"trait_id": "2002", "trait_name": "击破", "layers": []},
    ]
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: payload["data"])
    calls = []

    def fake_list(**kwargs):
        calls.append(kwargs.get("trait_id"))
        return {
            "list": [
                {
                    "tourn_detail": {
                        "role_stages": [
                            {"stage": "Early", "traits": [{"trait_id": "1004", "trait_name": "公司", "layers": [{"layer": 2}, {"layer": 3}], "current_role_count": 2}]},
                            {"stage": "Middle", "traits": [{"trait_id": "1007", "trait_name": "仙舟", "layers": [{"layer": 3}, {"layer": 5}, {"layer": 7}, {"layer": 10}], "current_role_count": 3}]},
                            {"stage": "Final", "traits": [{"trait_id": "2002", "trait_name": "击破", "layers": [{"layer": 2}, {"layer": 4}, {"layer": 6}, {"layer": 8}, {"layer": 10}], "current_role_count": 4}]},
                        ]
                    }
                }
            ],
            "next_page_token": None,
        }

    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", fake_list)

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path, enrich_traits=True)

    by_name = {item["name"]: item for item in config["traits"]}
    assert [layer["layer"] for layer in by_name["公司"]["layers"]] == [2, 3]
    assert [layer["layer"] for layer in by_name["仙舟"]["layers"]] == [3, 5, 7, 10]
    assert [layer["layer"] for layer in by_name["击破"]["layers"]] == [2, 4, 6, 8, 10]
    assert calls == [None]


def test_fetch_cw_guide_config_default_does_not_request_guide_list(monkeypatch, tmp_path):
    guide_module = load_cw_guide_module()
    monkeypatch.setattr(guide_module, "_fetch_cw_config_data", lambda **kwargs: fake_cw_config_response()["data"])
    monkeypatch.setattr(guide_module, "_fetch_cw_guide_list_data", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not fetch list")))

    config = guide_module.fetch_cw_guide_config(workspace_root=tmp_path)

    assert config["traits"]
```

- [ ] **Step 2: Run guide enrichment tests and verify failure**

Run: `uv run pytest tests/test_cw_guide.py::test_fetch_cw_guide_config_enriches_trait_layers_from_raw_lineup_list tests/test_cw_guide.py::test_fetch_cw_guide_config_default_does_not_request_guide_list -q --basetemp .pytest-tmp`

Expected: FAIL because `enrich_traits` is not implemented.

- [ ] **Step 3: Implement enrichment cache in guide.py**

Modify signatures and helpers in `trail/scenes/cw/guide.py`:

```python
from trail.scenes.cw.catalog import clean_cw_trait_entry, merge_cw_trait_entries

CW_GUIDE_CONFIG_ENRICHED_CACHE_RELATIVE = Path(".trail") / "cache" / "cw-guide-config-enriched.json"


def _cw_guide_enriched_config_cache_path(*, workspace_root: str | Path | None = None) -> Path:
    root = Path.cwd() if workspace_root is None else Path(workspace_root)
    return root / CW_GUIDE_CONFIG_ENRICHED_CACHE_RELATIVE


def _cw_config_meta_key(data: Mapping[str, object]) -> dict[str, object]:
    return {
        "season_id": data.get("season_id"),
        "sub_season_id": data.get("sub_season_id"),
        "big_version": data.get("rpg_game_big_version"),
        "lineup_filter_version": data.get("rpg_game_lineup_tourn_filter"),
    }


def _iter_raw_lineup_trait_entries(lineup_list: object):
    if not isinstance(lineup_list, list):
        return
    for lineup in lineup_list:
        if not isinstance(lineup, Mapping):
            continue
        tourn_detail = lineup.get("tourn_detail") if isinstance(lineup.get("tourn_detail"), Mapping) else {}
        for stage in tourn_detail.get("role_stages") or []:
            if not isinstance(stage, Mapping):
                continue
            for trait in stage.get("traits") or []:
                if isinstance(trait, Mapping):
                    yield trait


def _merge_trait_entry_map(target: dict[str, dict[str, object]], raw_entry: Mapping[str, object]) -> None:
    cleaned = clean_cw_trait_entry(raw_entry)
    if cleaned is None:
        return
    key = str(cleaned.get("id") or cleaned.get("name"))
    target[key] = merge_cw_trait_entries(target[key], cleaned) if key in target else cleaned
```

Implement `_enrich_cw_config_traits(raw_config, normalized_config, timeout, workspace_root)` so it:

1. Loads valid enriched cache first.
2. Seeds trait map from base normalized traits.
3. Calls `_fetch_cw_guide_list_data(page=1, limit=CW_GUIDE_UPSTREAM_PAGE_SIZE, trait_id=None, role_ids=[], order=None, next_page_token=None, match_change_job=None, match_hard=None, timeout=timeout)`.
4. Scans every raw lineup stage trait entry.
5. Computes missing base trait ids without layers.
6. Calls `_fetch_cw_guide_list_data(... trait_id=int(missing_id) ...)` only for still-missing ids.
7. Writes valid enriched cache with `complete=1`, `meta`, `missing_trait_ids`, and normalized `traits` via temp file replace.

Change public config function:

```python
def fetch_cw_guide_config(*, timeout: int = 10, workspace_root: str | Path | None = None, enrich_traits: bool = False) -> dict:
    data = _get_cw_config_data(timeout=timeout, workspace_root=workspace_root)
    strategy_source = data.get("fight_augment_list")
    if not isinstance(strategy_source, list):
        strategy_source = data.get("strategy_list")
    config = {
        "meta": {...},
        "lineup_levels": _normalize_lineup_levels(data.get("label_list")),
        "traits": _normalize_traits(data.get("trait_info_list")),
        "roles": _normalize_roles(data.get("role_list")),
        "role_tags": _normalize_role_tags(data),
        "portal_list": _normalize_portal_list(data.get("portal_list")),
        "strategy_list": _normalize_strategy_list(strategy_source),
    }
    if enrich_traits:
        config["traits"] = _enrich_cw_config_traits(data, config, timeout=timeout, workspace_root=workspace_root)
    return config
```

- [ ] **Step 4: Add guide.config YAML and cache tests**

Add tests for:

- Enriched cache hit does not call `_fetch_cw_guide_list_data`.
- Request failure does not overwrite existing enriched cache.
- Successful exhaustion with missing traits writes valid cache with `missing_trait_ids`.
- `guide.config.cw --format yaml` keeps Chinese summary and includes enriched `traits[].layers`.

Run: `uv run pytest tests/test_cw_guide.py tests/test_output_rendering.py::test_render_output_renders_guide_config_summary_and_zero_counts -q --basetemp .pytest-tmp`

Expected: PASS.

## Task 3: Warning Promotion And Renderer Protocol

**Files:**
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: Write failing renderer tests**

Add tests in `tests/test_output_rendering.py`:

```python
def test_render_cw_slots_read_renders_role_match_diagnostics_and_warning_1_based():
    envelope = {
        "ok": True,
        "data": {"front": [{"name": "爻光", "raw_name": "交光", "match_score": 0.5, "match_kind": "low_confidence", "traits": ["仙舟"]}], "back": [], "hand": [], "stale": False},
        "screenshot": ".trail/shots/demo.jpg",
        "warnings": [{"code": "CW_ROLE_MATCH_LOW_CONFIDENCE", "position": {"kind": "slot", "area": "front", "index": 0}, "query": "交光", "resolved": "爻光", "score": 0.5, "candidates": ["爻光:0.50"], "message": "角色名未精确命中，请先看截图确认"}],
        "references": [],
        "error": None,
    }

    assert render_output("cw.slots.read", envelope).splitlines() == [
        "ok cw.slots.read front=1 back=0 hand=0 stale=0",
        "shot path=.trail/shots/demo.jpg",
        "info read_image_first=1",
        "slot pos=front:1 name=爻光 raw_name=交光 score=0.50 match_kind=low_confidence traits=仙舟",
        "warn code=CW_ROLE_MATCH_LOW_CONFIDENCE pos=front:1 query=交光 resolved=爻光 score=0.50 candidates=爻光:0.50 msg=角色名未精确命中，请先看截图确认",
    ]


def test_render_cw_shop_status_never_renders_screenshot_guidance():
    envelope = {"ok": True, "data": {"items": [{"slot": 1, "name": "刃", "traits": ["燃血"]}], "coins": 5, "stale": False}, "screenshot": None, "warnings": [], "references": [], "error": None}

    lines = render_output("cw.shop.status", envelope).splitlines()

    assert "shot path=" not in "\n".join(lines)
    assert "info read_image_first=1" not in lines
    assert "item idx=1 slot=1 name=刃 traits=燃血" in lines
```

- [ ] **Step 2: Run renderer tests and verify failure**

Run: `uv run pytest tests/test_output_rendering.py::test_render_cw_slots_read_renders_role_match_diagnostics_and_warning_1_based tests/test_output_rendering.py::test_render_cw_shop_status_never_renders_screenshot_guidance -q --basetemp .pytest-tmp`

Expected: FAIL because renderer does not yet render new fields or warning positions.

- [ ] **Step 3: Implement renderer helpers**

In `trail/output/rendering.py`:

- Add `_format_agent_slot_pos(area: str, index: int) -> str` returning `f"{area}:{index + 1}"`.
- Update `_append_cw_slot_lines()` to use 1-based pos and append `raw_name`, `score`, `match_kind`, `traits` in this order.
- Update `_append_cw_shop_items()` to append `traits`, `raw_name`, `score`, `match_kind` after `cost`.
- Extend `_append_warnings()` for `CW_ROLE_MATCH_*` warnings with `position` support.

Code shape:

```python
def _format_agent_slot_pos(area: str, index: int) -> str:
    return f"{area}:{index + 1}"


def _append_role_match_warning(lines: list[str], warning: dict[str, Any]) -> bool:
    code = warning.get("code")
    if code not in {"CW_ROLE_MATCH_LOW_CONFIDENCE", "CW_ROLE_MATCH_AMBIGUOUS", "CW_ROLE_MATCH_FUZZY"}:
        return False
    facts: list[tuple[str, Any]] = [("code", code)]
    position = _as_dict(warning.get("position"))
    if position.get("kind") == "slot":
        area = position.get("area")
        index = _coerce_int(position.get("index"))
        if isinstance(area, str) and index is not None:
            facts.append(("pos", _format_agent_slot_pos(area, index)))
    elif position.get("kind") == "shop":
        if position.get("slot") is not None:
            facts.append(("slot", position.get("slot")))
        elif position.get("idx") is not None:
            facts.append(("idx", position.get("idx")))
    facts.extend([
        ("query", warning.get("query")),
        ("resolved", warning.get("resolved")),
        ("score", _format_score_value(warning.get("score"))),
        ("candidates", _compact_text_or_sequence(warning.get("candidates"))),
        ("msg", warning.get("message")),
    ])
    _append_fact_line(lines, "warn", *facts)
    return True
```

- [ ] **Step 4: Promote scene warnings in CwService**

Add a helper in `trail/daemon/cw_service.py`:

```python
def _pop_scene_warnings(data: object) -> tuple[object, list[dict]]:
    if not isinstance(data, dict):
        return data, []
    payload = deepcopy(data)
    raw = payload.pop("warnings", None)
    return payload, deepcopy(raw) if isinstance(raw, list) else []


def _merge_envelope_warnings(envelope: dict, warnings: list[dict]) -> dict:
    if not warnings:
        return envelope
    merged = deepcopy(envelope)
    merged["warnings"] = [*deepcopy(merged.get("warnings") or []), *deepcopy(warnings)]
    return merged
```

In `handle_mutation()` success path, before `with_auto_capture`, wrap `result` so scene warnings are popped from data and merged into envelope after capture. This mirrors command_service role warning promotion and keeps warnings out of command data/session.

- [ ] **Step 5: Add daemon warning promotion tests**

In `tests/test_daemon_protocol.py`, add a test with a fake handler result containing `warnings`, plus runtime warning. Expected envelope has both warnings and `data` has no `warnings` key.

Run: `uv run pytest tests/test_output_rendering.py tests/test_daemon_protocol.py -q --basetemp .pytest-tmp`

Expected: PASS.

## Task 4: CLI 1-Based Slot Boundary

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/scenes/cw/slots.py`
- Modify: `tests/test_cw_slots.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] **Step 1: Write failing CLI conversion tests**

Add tests covering CLI conversion and direct daemon 0-based contract.

```python
def test_cw_slots_read_cli_converts_agent_visible_slot_to_internal_zero_based(cli_runner, monkeypatch):
    captured = {}
    monkeypatch.setattr("trail.commands.cw._print_cw", lambda command, *, session_id, payload=None: captured.update(command=command, session_id=session_id, payload=payload))

    result = cli_runner.invoke(app, ["cw", "slots", "read", "--session", "s" * 32, "--slot", "front:1"])

    assert result.exit_code == 0
    assert captured == {"command": "cw.slots.read", "session_id": "s" * 32, "payload": {"slot": ["front:0"]}}


def test_cw_hand_sell_cli_rejects_zero_slot(cli_runner):
    result = cli_runner.invoke(app, ["cw", "hand", "sell", "--session", "s" * 32, "--slot", "0"])

    assert result.exit_code == 0
    assert "fail cw.hand.sell code=CW_OPTION_INVALID" in result.output
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `uv run pytest tests/test_cw_slots.py::test_cw_slots_read_cli_converts_agent_visible_slot_to_internal_zero_based tests/test_cw_slots.py::test_cw_hand_sell_cli_rejects_zero_slot -q --basetemp .pytest-tmp`

Expected: FAIL because CLI still passes raw values.

- [ ] **Step 3: Implement CLI conversion helpers**

In `trail/commands/cw.py` add:

```python
def _parse_agent_slot_ref(value: str) -> str:
    area, sep, raw = value.partition(":")
    if sep != ":" or area not in {"front", "back", "hand"} or not raw.isdecimal():
        raise TrailError("CW_OPTION_INVALID", f"invalid slot position: {value}")
    index = int(raw)
    if index <= 0:
        raise TrailError("CW_OPTION_INVALID", f"invalid slot position: {value}")
    return f"{area}:{index - 1}"


def _parse_agent_hand_slot(value: int) -> int:
    if value <= 0:
        raise TrailError("CW_OPTION_INVALID", f"invalid hand slot: {value}")
    return value - 1
```

Use these helpers in `cw_slots_read`, `cw_slots_swap`, `_parse_place_actions`, and `cw_hand_sell`. Catch `TrailError` and print `_cw_input_invalid_response(str(error))` with the current command name.

- [ ] **Step 4: Update failure text boundary**

In `slots.py`, ensure errors created from internal slot references either avoid embedding raw internal refs or use renderer/CLI visible refs. Replace direct messages such as `target slot cannot field character: front:0` with structured error data or 1-based formatting before user-visible output.

Minimum implementation:

```python
def _format_agent_slot_reference(value: str) -> str:
    area, sep, raw = value.partition(":")
    if sep == ":" and raw.isdecimal():
        return f"{area}:{int(raw) + 1}"
    return value
```

Use it in `SLOTS_CANNOT_BE_FIELDED` and other slot-related `TrailError` messages.

- [ ] **Step 5: Run CLI/slot tests**

Run: `uv run pytest tests/test_cw_slots.py tests/test_daemon_protocol.py -q --basetemp .pytest-tmp`

Expected: PASS.

## Task 5: Slots Catalog Integration

**Files:**
- Modify: `trail/scenes/cw/slots.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_cw_slots.py`

- [ ] **Step 1: Write failing slots integration tests**

Add tests for no-guide full config canonical, low confidence diagnostics, no fake tiers, role_count from merged snapshot, and diagnostics not persisted.

```python
def test_slots_read_uses_full_config_catalog_without_selected_guide(tmp_path):
    slots_module = load_cw_slots_module()
    session = build_fake_cw_session(tmp_path)
    guide_config = {"traits": [{"id": "1007", "name": "仙舟", "layers": [{"layer": 3}]}], "roles": [{"id": "1502", "name": "爻光", "trait_ids": ["1007"]}]}

    refreshed = slots_module.read_cw_slots(
        session,
        reader=lambda: ([{"name": "交光", "star": 1}, None, None, None], [None] * 6, [None] * 9),
        guide_config=guide_config,
    )

    stored = refreshed.scene_state["cw"]["slots"]["front"][0]
    assert stored == {"name": "爻光", "role_id": "1502", "star": 1, "traits": ["仙舟"]}
    assert "raw_name" not in stored
```

- [ ] **Step 2: Run slots integration tests and verify failure**

Run: `uv run pytest tests/test_cw_slots.py -q --basetemp .pytest-tmp`

Expected: FAIL for new assertions.

- [ ] **Step 3: Refactor slots to consume catalog**

In `slots.py`:

- Import `build_cw_catalog`, `resolve_cw_role_name`, `summarize_cw_field_traits`.
- Remove local role candidate matching helpers that are superseded by catalog.
- Build catalog at start of `read_cw_slots()` when `guide_config` is present.
- Canonicalize raw front/back/hand values before merge.
- Store only canonical stable facts in `cw_state["slots"]`.
- Build a response snapshot that includes transient `raw_name/match_score/match_kind` for current command output.
- Attach warnings as `response_snapshot["warnings"]` for CwService promotion.
- Keep `stage.status.role_count` based on merged stored values.

Suggested shape:

```python
def _canonicalize_slot_area(values, *, area: str, catalog) -> tuple[list[Any], list[dict], list[dict]]:
    canonical = []
    response = []
    warnings = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or not value.get("name"):
            canonical.append(None)
            response.append(None)
            continue
        match = resolve_cw_role_name(value.get("name"), catalog, position={"kind": "slot", "area": area, "index": index})
        if match is None:
            canonical.append(None)
            response.append(None)
            continue
        stable = {"name": match.name, "role_id": match.role_id, "star": value.get("star"), "traits": match.traits}
        stable = {key: item for key, item in stable.items() if item is not None and item != []}
        diagnostic = dict(stable)
        if match.raw_name is not None:
            diagnostic.update({"raw_name": match.raw_name, "match_score": match.match_score, "match_kind": match.match_kind})
        if match.warning:
            warnings.append(match.warning)
        canonical.append(stable)
        response.append(diagnostic)
    return canonical, response, warnings
```

- [ ] **Step 4: Ensure daemon uses enriched config for slots**

In `cw_service.py`, change `cw.slots.read` to pass `fetch_cw_guide_config(workspace_root=workspace_root, enrich_traits=True)`.

- [ ] **Step 5: Run slots and rendering tests**

Run: `uv run pytest tests/test_cw_catalog.py tests/test_cw_slots.py tests/test_output_rendering.py -q --basetemp .pytest-tmp`

Expected: PASS.

## Task 6: Shop Catalog Integration

**Files:**
- Modify: `trail/scenes/cw/shop.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `tests/test_cw_shop.py`

- [ ] **Step 1: Write failing shop tests**

Add tests for scan canonicalization, status no diagnostics, stale summary suppression, buy_slot canonical confirmation, and no enriched config on mutating shop actions.

```python
def test_shop_scan_canonicalizes_items_and_keeps_diagnostics_out_of_status(tmp_path):
    shop_module = load_cw_shop_module()
    session = build_fake_cw_session(tmp_path)
    guide_config = {"traits": [{"id": "1007", "name": "仙舟", "layers": [{"layer": 3}]}], "roles": [{"id": "1502", "name": "爻光", "trait_ids": ["1007"]}]}

    scanned = shop_module.scan_cw_shop(
        session,
        scanner=lambda: {"items": [{"slot": 2, "name": "交光", "price": 1}], "coins": 5, "reserve_full": False, "stale": False},
        guide_config=guide_config,
    )

    stored = scanned.scene_state["cw"]["shop"]["items"][0]
    assert stored == {"slot": 2, "name": "爻光", "role_id": "1502", "price": 1, "traits": ["仙舟"]}
    status = shop_module.shop_cw_status(scanned)
    assert "raw_name" not in status["items"][0]
    assert "match_score" not in status["items"][0]
```

- [ ] **Step 2: Run shop tests and verify failure**

Run: `uv run pytest tests/test_cw_shop.py -q --basetemp .pytest-tmp`

Expected: FAIL for new assertions.

- [ ] **Step 3: Implement shop canonicalization**

In `shop.py`:

- Add `guide_config: dict[str, Any] | None = None` to `scan_cw_shop()`, `buy_cw_shop_slot()`, `buy_cw_shop_exp()` as needed.
- Build base catalog from non-enriched config for item canonicalization.
- Preserve stable item fields in `_normalized_shop_items()` so `role_id` and `traits` are not dropped.
- Keep transient diagnostics only in returned response snapshot.
- Do not write `trait_summary` into `cw_state.shop`.
- Add `project_cw_shop_snapshot(session, guide_config=None, include_field_trait_summary=False)` logic that only includes field summary when slots fresh.

Suggested canonical item helper:

```python
def _canonicalize_shop_items(items: list[dict[str, Any]], *, catalog) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    stored = []
    response = []
    warnings = []
    for order, item in enumerate(items, start=1):
        name = item.get("name")
        if name is None:
            stored.append(dict(item))
            response.append(dict(item))
            continue
        match = resolve_cw_role_name(name, catalog, position={"kind": "shop", "slot": item.get("slot"), "idx": order})
        stable = {**item, "name": match.name, "role_id": match.role_id, "traits": match.traits}
        stable = {key: value for key, value in stable.items() if value is not None and value != []}
        diagnostic = dict(stable)
        if match.raw_name is not None:
            diagnostic.update({"raw_name": match.raw_name, "match_score": match.match_score, "match_kind": match.match_kind})
        if match.warning:
            warnings.append(match.warning)
        stored.append(stable)
        response.append(diagnostic)
    return stored, response, warnings
```

- [ ] **Step 4: Wire CwService shop config boundaries**

In `cw_service.py`:

- `cw.shop.scan`: pass base `fetch_cw_guide_config(workspace_root=workspace_root)` for item canonicalization; only pass enriched config to projection if slots fresh and field summary is needed.
- `cw.shop.status`: do not fetch enriched config when slots stale/missing; use persisted canonical snapshot.
- `cw.shop.buy_slot/buy_exp`: pass base config only; never fetch enriched config for field summary.
- `cw.shop.buy_slot --expect`: compare against canonical names after scanner canonicalization.

- [ ] **Step 5: Run shop suite**

Run: `uv run pytest tests/test_cw_shop.py tests/test_daemon_protocol.py tests/test_output_rendering.py -q --basetemp .pytest-tmp`

Expected: PASS.

## Task 7: Docs, Skills, And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `skills/trail-cw-prep/SKILL.md`
- Modify: `skills/trail-cw-prep/references/command-surface.md`
- Modify: affected `skills/trail-cw-entry/**`, `skills/trail-cw-guide/**`, `skills/trail-hsr/**` command references that mention 0-based slots or shop/slots output.
- Modify: `tests/test_output_debug.py`
- Modify: `tests/test_output_rendering.py`

- [ ] **Step 1: Update docs examples**

Replace Agent-visible 0-based examples with 1-based examples:

```text
trail cw slots read --session <id> --slot front:1 --slot hand:4
trail cw slots place --session <id> --action hand:1,front:1 --action hand:2,back:3
trail cw hand sell --session <id> --slot 1 --slot 3
slot pos=front:1 name=爻光 raw_name=交光 score=0.50 match_kind=low_confidence traits=仙舟|战技点|欢愉
```

Document:

- `slots.read` and `shop.scan` canonicalize names against CW config.
- Low confidence warnings require reading screenshots first.
- `shop.scan/status` field trait summary only appears when slots snapshot is fresh.
- `cw.shop.status` does not output screenshots.

- [ ] **Step 2: Update doc assertion tests**

Add or update assertions in `tests/test_output_debug.py` and `tests/test_output_rendering.py`:

```python
def test_docs_describe_agent_visible_one_based_slots_and_role_matching():
    readme = Path("README.md").read_text(encoding="utf-8")
    agents = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "--slot front:1" in readme
    assert "hand:1,front:1" in readme
    assert "score=0.50" in readme
    assert "raw_name" in readme
    assert "默认文本输出改为 Agent 可见 1-based" in agents
    assert "cw.shop.status 不产出截图" in agents
```

- [ ] **Step 3: Run targeted verification**

Run:

```powershell
uv run pytest tests/test_cw_catalog.py tests/test_cw_guide.py tests/test_cw_slots.py tests/test_cw_shop.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_daemon_protocol.py -q --basetemp .pytest-tmp
```

Expected: all selected tests pass.

- [ ] **Step 4: Run protocol and whitespace checks**

Run:

```powershell
uv run python -c "from trail.scenes.cw.catalog import build_cw_catalog; print(build_cw_catalog({'traits': [], 'roles': []}).roles)"
```

Expected: `git.exe diff --check` no output; smoke prints `[]`.

- [ ] **Step 5: Request code review**

Use `requesting-code-review` with the full diff and this plan/spec. Fix all Critical/Important findings before final handoff.

## Final Verification Matrix

Before claiming completion, run:

```powershell
uv run pytest tests/test_cw_catalog.py tests/test_cw_guide.py tests/test_cw_slots.py tests/test_cw_shop.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_daemon_protocol.py -q --basetemp .pytest-tmp
```

Expected:

- pytest selected suite passes.
- `git.exe diff --check` has no output.
- `git.exe status --short` shows only intended files for this feature.

## Self-Review

- Spec coverage: catalog helper, guide enrichment/cache, slots, shop, renderer/warnings, 1-based CLI/text, docs/tests are all mapped to tasks.
- Placeholder scan: no unfinished placeholder markers remain.
- Type consistency: `match_score` is internal response/envelope data; default text renders `score`. `match_kind` appears only for non-exact matches in default text. CLI converts Agent-visible 1-based to internal 0-based before daemon RPC.
