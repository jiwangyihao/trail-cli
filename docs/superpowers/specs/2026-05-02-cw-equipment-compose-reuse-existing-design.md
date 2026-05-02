# cw.equipment.compose 复用已有进阶装备设计

## 背景

`cw.equipment.compose` 已经从 session-only 记录命令改为真实 UI mutation。当前实现会在读取装备背包后固定选择基础材料并执行一次合成拖拽，然后把合成结果装备给目标角色。

这会浪费一个已存在的目标进阶装备：如果背包里已经有用户请求的进阶装备，命令仍会尝试再合成一件。新的默认行为应该优先使用已有进阶装备；只有背包里没有可确认的目标进阶装备时，才走现有合成链路。

## 目标行为

- `cw.equipment.compose --name <装备> --slot <位置> --role <角色>` 的命令名与入口不变。
- 仍然先刷新 slots 并做现有 preflight 校验：目标角色、槽位、重复装备、三件装备上限等都必须在任何真实拖拽前完成。
- 第一次 `cw.equipment.read` 后，先在当前背包里查找目标进阶装备。
- 如果存在高置信目标进阶装备，直接把已有装备拖到目标角色槽位，不执行材料合成拖拽，也不要求基础材料充足。
- 如果不存在可确认目标进阶装备，保留现有合成流程、材料不足 warning、post-compose 验证与 post-equip 验证。
- 成功后仍只在 post-equip 验证通过后写入角色装备，并将装备快照标记为 stale。

## 目标装备匹配

- 使用 `build_cw_equipment_recipes(raw_config)` 获取目标进阶装备 recipe。
- 复用现有 `_item_matches_target_recipe(item, recipe)` 判定背包 item 是否是目标进阶装备。
- 目标匹配必须使用进阶装备身份：优先 `cache_key`，其次 `advanced:<equipment_id>`；只有 recipe 无稳定身份时才允许按目标进阶装备名称匹配。不得因为名称相同把基础装备、其它 `cache_key` 装备或非目标进阶装备当成可复用目标。
- 只复用 `uncertain != True` 的 item；低置信目标项不能直接装备。
- 多个匹配项存在时，选择 `idx` 最小的 item，保证行为稳定可测。
- 如果 recipe 缺失，已有装备匹配无法安全确认；此时沿用现有 missing recipe failure，不执行拖拽。
- `CW_EQUIPMENT_RECIPE_UNSUPPORTED` 只在没有可复用目标装备、准备进入合成路径时触发；已有目标进阶装备可直接装备，不因为基础材料配方不是两件而失败。

## 新增 helper 契约

- 新增 `select_existing_cw_equipment_target(snapshot, *, recipe) -> Mapping[str, Any] | None` 或等价私有 helper。
- helper 按 `idx` 升序遍历 snapshot `items` 中的 mapping item。
- helper 只返回 `uncertain is not True` 且 `_item_matches_target_recipe(item, recipe)` 为真的 item。
- 无匹配或仅有低置信目标项时返回 `None`，不抛错；这样可以回退到现有合成路径。
- recipe 缺失必须在调用 helper 前以 `CW_EQUIPMENT_RECIPE_MISSING` 失败，不能按名称 fallback 直接拖拽。

## 数据流

1. `read_cw_slots()` 在 side-effect suppression scope 内刷新角色快照。
2. `validate_cw_equipment_compose_preflight()` 校验目标角色与 session 状态，不写 session。
3. `apply_cw_equipment_read()` 在 side-effect suppression scope 内读取初始背包快照。
4. 新增已有目标装备选择 helper，从初始快照选择可复用 item。
5. 如果找到可复用 item：
   - `equip_from = item["pos"]` 或 `equipment:<idx>`。
   - 拖拽 `equip_from` 到目标角色坐标。
   - 再次 `apply_cw_equipment_read()` 获取 post-equip 快照。
   - 调用 `_verify_cw_equipment_post_equip(post_compose_snapshot=initial_snapshot, post_equip_snapshot=post_equip_snapshot, result_item=item)`，以初始快照作为 pre-equip 快照，按身份多重集合验证仅删除一件 selected item；其它同名、同 `cache_key` 或同身份目标装备仍存在时应通过。
   - 调用 `commit_cw_equipment_compose_record()` 写角色装备。
6. 如果未找到可复用 item：执行当前合成路径。

## 响应数据契约

- 直接装备已有进阶装备时，scene response 必须携带 `action="equip_existing"` 或等价稳定分支字段。
- 直接装备路径返回 `existing_item={idx,pos,name,equipment_id,cache_key}`、`equip_action={drag_from,drag_to}`、`verified=True`、`consumed=0`、`post_equip_equipment_count=<n>`、`equipment_stale=True`。
- 直接装备路径不得返回 `compose_action`、`materials`、`result_item` 或 `post_compose_equipment_count`，除非 renderer 明确不会把它们渲染成合成事实。
- 合成路径保持当前 response shape：`materials`、`result_item`、`compose_action`、`equip_action`、`consumed=2`、`post_compose_equipment_count`、`post_equip_equipment_count`。

## 输出协议

- success 首行保持：`ok cw.equipment.compose pos=... name=... 装备=... count=...`。
- success 仍必须输出 `shot path=...` 和紧随其后的 `info read_image_first=1`。
- 直接装备已有进阶装备时，`# 综合信息` 输出 `info action=equip_existing drag_from=... drag_to=... verified=1 consumed=0 post_equip_equipment_count=... equipment_stale=1`。
- `consumed=0` 表示没有消耗基础合成材料；它不表示目标进阶装备没有从背包移动到角色。该 `0` 是会影响 Agent 决策的 must-keep fact，不能省略。
- 直接装备已有进阶装备时不输出 `action=compose`、`consumed=2` 或 `post_compose_equipment_count`，避免 Agent 误判发生过合成。
- `# 装备信息` 输出 `item kind=existing phase=pre_equip idx=... pos=... name=...`。
- `# 角色信息` 的 `slot` 行保持不变。
- failure 顺序、YAML 不支持、recover/tainted 语义均保持现状。

## 文档同步要求

- 更新 `AGENTS.md` 中 `cw.equipment.compose` 协议说明：该命令优先装备已有目标进阶装备；仅无可确认目标装备时才合成。
- 更新 `skills/trail-cw-prep/SKILL.md` 和 `skills/trail-cw-prep/references/command-surface.md`，说明 success 可能出现 `action=equip_existing consumed=0` 或现有 `action=compose consumed=2` 两类路径。
- 不更新根目录 `README.md`，除非实现过程中发现该行为已在普通用户入口中公开描述。

## 测试要求

- 已有目标进阶装备时，只发生一次装备拖拽，不发生合成拖拽，成功后写 session。
- 已有目标进阶装备且基础材料不足时，仍直接装备，不返回 `CW_EQUIPMENT_MATERIALS_MISSING`。
- 已有目标进阶装备但 `uncertain=True` 时，不直接装备；没有足够材料时应走现有材料不足 failure，且不拖拽、不写 session。
- 已有目标进阶装备但 recipe 缺失时，不按名称 fallback 直接装备，失败且不拖拽、不写 session。
- 已有目标进阶装备且 recipe unsupported 时，仍直接装备；只有无可复用目标并进入合成路径时才返回 unsupported recipe failure。
- 同名不同 `cache_key`、基础/进阶同名、基础/进阶 id 相同等场景不能误复用非目标装备。
- 目标角色已经记录同名装备、角色装备已满、slot/role mismatch 等 preflight failure 必须早于任何直接装备拖拽。
- 背包还有另一件同名或同身份目标装备时，直接装备一件后 post-equip 验证应通过。
- 直接装备已有进阶装备后的 post-equip 验证失败时，不写 session，daemon mutation 语义按拖拽后失败处理为 unknown/tainted。
- renderer 覆盖 `equip_existing` 输出，确认顺序仍为首行 -> `shot` -> `info read_image_first=1` -> `# 综合信息` -> `# 装备信息` -> `# 角色信息`；确认输出 `consumed=0`、`item kind=existing phase=pre_equip`，且没有 `action=compose`、`consumed=2`、`kind=result phase=post_compose`。
- renderer 继续覆盖空 `# 装备信息` 不输出、材料不足 failure 顺序不受影响、`--format yaml` 对 `cw.equipment.compose` 仍不支持。
- daemon/RPC contract 覆盖直接装备 success 必须有截图；缺截图返回 unknown；直接装备拖拽后异常或 post-equip 验证失败不写 session 并走 recover/tainted。

## 非目标

- 不新增 CLI 参数。
- 不改变 command 名、首行字段顺序或 YAML allowlist。
- 不改变已有无目标装备时的合成行为。
- 不实现“选择指定 idx 的已有装备”。多件同名目标装备时固定选择最小 idx。

## 自审

- 无 TBD/TODO 占位。
- 直接装备分支与现有合成分支的 side-effect 边界一致：真实拖拽后失败仍交给 daemon mutation unknown/tainted 语义。
- 输出字段明确区分已有装备复用与合成路径，避免协议歧义。
