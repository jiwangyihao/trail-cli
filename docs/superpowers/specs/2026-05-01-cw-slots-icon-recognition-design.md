# `cw.slots.read` 角色图标识别设计

## 背景

当前 `cw.slots.read` 的角色读取仍依赖逐槽位点击详情面板：点击槽位、等待面板、截图姓名区域、OCR、关闭面板，再把 OCR 文本归一化为角色名。默认全量读取最多涉及 `front(4) + back(6) + hand(9)` 共 19 个槽位，真实运行慢且容易受 OCR 错字、低置信和 UI 时序影响。

本次实验确认：CW 静态配置 `raw_config.json` 的 `role_list` 含角色 `icon` URL。通过一次整屏截图、固定槽位几何、场上卡面透视还原、头像遮罩、星级检测和费率色条辅助，可以直接识别槽位角色，不再逐个点开角色详情。

本设计已经按三轮只读 review 补齐实现边界：角色资源必须进入构建期静态包，识别截图必须通过 private `_screenshot` 复用，`cw.portal.select` 自动收集路径必须消费同一套 slots 输出契约，默认文本不得新增 `role_id` 字段。

## 实验结论

诊断产物保存在 `.trail/diagnostics/slot-icon-experiment/`，关键文件：

- `refined-geometry-v2/image1_overlay_v2.jpg`：第一张标注截图的拟合槽位框。
- `refined-geometry-v2/image2_overlay_v2.jpg`：第二张标注截图的后台后 3 个槽位框。
- `refined-geometry-v2/warp_sheet_v2.jpg`：所有槽位还原到 `103x120` 后的卡面。
- `slot-card-analysis/slot_regions_fee_star_sheet.jpg`：星级 ROI、费率条和头像区域校验。
- `star-fee-calibration/fixed_scale_065_mask_similarity_sheet.jpg`：星级小块遮罩和当前阵容图标识别结果。

已验证事实：

1. 前台和后台卡面存在透视/切变，必须用四点透视还原；手牌卡面基本是矩形。
2. 用户标注点结合几何约束后，可以得到稳定槽位框：前台 4 个槽位共用上/下边缘 `y`，后台 6 个槽位共用上/下边缘 `y`，手牌 9 个槽位共用矩形宽度和间距。
3. 统一还原尺寸采用 `103x120`，来自手牌标注宽高；前台和后台通过透视还原到同尺寸。
4. 头像相似度应排除右上角前后台站位黑框、左上角攻略标志、底部费率色条，并且只屏蔽检测到的星星小块，不能屏蔽整条星级行。
5. 星级检测复用 `assets/star.png`，但应在还原卡面的星级 ROI 内用固定三尺度 `[0.65, 0.75, 0.80]`、阈值 `0.78`、NMS 合并。实验中后台 3 号缇宝识别为 2 星，娜塔莎和希儿识别为 1 星，空槽未误检。
6. 费率色条可辅助识别。当前样本中 HSV `h≈86` 是绿色，`h≈114-115` 是蓝色，低饱和是灰色，`h≈127` 是紫色。
7. 在最新样本中，应用上述遮罩与三尺度星级检测后，所有有角色槽位图标识别命中；空槽最高角色相似度约 `0.10-0.14`，与有角色槽位明显分离。

## 目标

1. 将 `cw.slots.read` 的角色读取改为截图级图标识别，默认不再逐槽位点击详情。
2. 构建期把 `role_list.icon` 纳入 CW 静态资源包，release 默认无网络读取角色图标资源。
3. 保持 `cw.slots.read` 现有默认文本协议、session slots shape、局部读取语义和 stage/status 投影语义。
4. 在结构化响应和 session 中继续保留 `name`、`role_id`、`traits`、`star` 等稳定字段；默认文本 slot 行不新增 `role_id`。
5. 使用角色图标相似度、空槽模板、星级和费率色条做综合证据，降低相似头像或遮挡导致的误判。
6. 保证 `cw.slots.read` 与 `cw.portal.select` 自动收集路径的截图、输出事实和 warning 来自同一识别结果，不产生二次截图错位。

## 非目标

本次不做：

- 改变 `cw.slots.read` 的默认文本 renderer 家族、首行字段或 allowed prefix。
- 为 `cw.slots.read` 新增 YAML allowlist。
- 修改 `cw.slots.place`、`cw.hand.sell_plan`、装备推荐等下游业务语义。
- 让角色识别在运行时联网下载 icon。
- 依赖当前攻略限制候选角色；攻略仍可用于 canonicalization 和推荐，但基础识别应来自配置中的全量角色资源。
- 在默认路径保留逐槽位点击详情作为主要读取方式。
- 禁用 stage/status OCR、覆盖层关闭、商店扫描或装备读取中已有的必要 UI 操作；“不再点击/OCR”仅指 slots 角色姓名读取不再使用逐槽详情面板、`SLOT_NAME_REGION` 和姓名 OCR。

## 方案比较

### 方案 A：运行时下载角色 icon 并匹配

首次运行 `cw.slots.read` 时从 `raw_config.role_list.icon` 下载角色图标，缓存后用于匹配。

优点是实现少，适合实验。缺点是违反 bundle-first/no-network，首次运行可能慢或失败，且资源生命周期与装备静态资源包不一致。因此只保留为实验方法，不进入正式实现。

### 方案 B：构建期角色图标资源包 + 截图级识别

扩展现有 CW 静态资源包，构建期下载角色 icon、生成 manifest 和预计算特征。运行时 `cw.slots.read` 只截一次整屏，按固定几何还原各槽位卡面，执行空槽检测、角色匹配、星级检测和费率校验。

优点是满足 release 默认无网络，与装备图标识别和 `CwResourceService` 缓存模型一致，默认全量读取从 19 次点击详情变成一次整屏截图加本地图像计算。缺点是需要扩展资源包 schema、构建脚本、artifact verifier 和 daemon resource service。

采用本方案。

### 方案 C：全屏模板搜索反推位置

直接在整屏截图上搜索配置 icon，不依赖固定槽位几何。优点是理论上更自适应；缺点是容易被羁绊图标、装备栏、头像相似区域误导，且最终仍需映射回固定 `front/back/hand` 位置。不作为主方案，可作为未来诊断工具。

## 推荐设计

采用方案 B：构建期角色资源包 + 槽位几何还原 + 多证据识别。

### 资源包扩展

在 CW 静态资源包中新增角色资源：

- `roles/manifest.json`
- `roles/features.json`
- `roles/icons/<role-id>.png`
- `roles/empty/field-v1.png`
- `roles/empty/hand-v1.png`

新增冻结常量：

- `CW_ROLE_MANIFEST_SCHEMA_VERSION = 1`
- `CW_ROLE_FEATURE_SCHEMA_VERSION = 1`
- `CW_ROLE_RECOGNIZER_ALGORITHM_VERSION = "role-card-mask-v1"`
- `CW_SLOT_GEOMETRY_VERSION = "cw-slots-1920x1080-v2"`
- `CW_SLOT_EMPTY_TEMPLATE_VERSION = "cw-slots-empty-v1"`

`roles/manifest.json` 顶层字段：

- `role_manifest_schema_version=1`
- `resource_version=<rpg_game_big_version>`
- `empty_template_version="cw-slots-empty-v1"`
- `empty_templates.field.local_path="roles/empty/field-v1.png"`
- `empty_templates.hand.local_path="roles/empty/hand-v1.png"`
- `items=[...]`

每个 role item 至少包含：

- `role_id`：来自配置角色 `id`，必须转成非空字符串。
- `name`：角色显示名。
- `normalized_name`：与 `catalog.py` 查询一致的归一化名称。
- `icon_url`：来自 `raw_config.role_list.icon`。
- `local_path`：固定为 `roles/icons/<safe-role-id>.png`，只能在 `roles/icons/` 下。
- `sha256`、`size`：实际 PNG 文件校验信息。
- `rarity` 或 `cost`：用于费率色条辅助校验；如果上游同时存在两者，两个字段都保留。
- `front_back_type`：保留原配置值，供位置约束和诊断使用。
- `trait_ids`：保留原配置羁绊 ID 列表。

`roles/features.json` 顶层字段：

- `role_feature_schema_version=1`
- `recognizer_algorithm_version="role-card-mask-v1"`
- `geometry_version="cw-slots-1920x1080-v2"`
- `empty_template_version="cw-slots-empty-v1"`
- `target_size=[103,120]`
- `avatar_roi=[5,4,98,108]`
- `feature_size=[64,64]`
- `hist_bins=[16,16,16]`
- `min_score=0.58`
- `low_score=0.50`
- `min_gap=0.035`
- `empty_min_score=0.82`
- `empty_min_gap=0.08`
- `items=[...]`

每个 feature item 至少包含：

- `role_id`、`name`、`normalized_name`、`rarity`/`cost`、`front_back_type`、`trait_ids`。
- `icon_rgba`：角色 icon 归一化到 `64x64` 的 RGBA pixel payload。
- `icon_mask`：角色 icon alpha mask 归一化到 `64x64` 的 L pixel payload。
- `histogram`：基于 alpha mask 内像素计算的 HSV 直方图，长度为 `16*16*16`。

空槽模板来源固定为提交到源码树的种子图，并由构建脚本复制进 bundle：

- `trail/scenes/cw/assets/slots/empty-field-v1.png` -> `roles/empty/field-v1.png`
- `trail/scenes/cw/assets/slots/empty-hand-v1.png` -> `roles/empty/hand-v1.png`

种子图必须是已还原到 `103x120` 的 canonical PNG。`field` 模板适用于 `front/back` 空槽，`hand` 模板适用于手牌空槽。构建脚本必须校验两张种子图是 `103x120` RGBA/PNG，写入 hash 和 size；缺失或尺寸错误时构建失败。

`static_resources.py` 需要同步扩展：

- `_FIXED_BUNDLE_RELATIVES` 增加 `roles/manifest.json`、`roles/features.json`、`roles/empty/field-v1.png`、`roles/empty/hand-v1.png`。
- 新增 `_clean_role_icon_relative()`，只允许 `roles/icons/*.png`。
- 新增 `_clean_role_empty_relative()`，只允许 `roles/empty/field-v1.png` 和 `roles/empty/hand-v1.png`。
- 新增 `_validate_role_manifest()`、`_validate_role_features()`、`_validate_role_feature_manifest_keys()`。
- `_validate_manifest_files_allowed()` 的 allowlist 必须包含所有 role icon 和两张 empty 模板。
- `_validate_no_unreferenced_bundle_files()` 继续禁止 `roles/` 下未被 manifest 引用的重复文件。
- `CwResourceBundle` 增加 `role_manifest`、`role_features`、`role_manifest_path`、`role_manifest_mtime`。

`scripts/build-cw-resource-bundle.py` 需要同步扩展：

- 从 `raw_config.role_list` 收集角色，下载 `icon`，写入 `roles/icons/`。
- 生成 `roles/manifest.json` 和 `roles/features.json`。
- `indexes.json` 继续保留 `roles_by_name`、`roles_by_id` 和 `role_lookup_candidates`；新增角色资源不得破坏现有 indexes shape。
- `write_bundle_manifest()` 的 `files` 必须纳入角色 manifest、features、empty templates 和所有 role icons。

`scripts/verify-cw-resource-bundle-artifacts.py` 需要同步扩展：

- `REQUIRED_RELATIVES` 增加 role 相关固定文件。
- archive 允许目录增加 `roles`、`roles/icons`、`roles/empty`。
- 校验 role manifest/features schema、feature 与 manifest 的 `role_id` 集合一致、role icon hash/size 正确。
- 继续拒绝重复 archive member、manifest 引用缺失文件、manifest 之外的 generated 文件、路径穿越和 symlink/junction。

首版不支持 workspace role override。现有 `.trail/cache/cw-equipment-resource` 与 `cw.equipment.prepare --refresh` 仍只覆盖装备资源；角色资源只从 package bundle 读取。`TRAIL_CW_RESOURCE_DEV_FALLBACK=1` 也不得让 `cw.slots.read` 在运行时下载角色 icon；角色资源缺失、损坏或 schema 不匹配时必须 fail fast 为 `CW_RESOURCE_BUNDLE_INVALID` 或 `CW_RESOURCE_BUNDLE_MISSING`。

### Daemon 资源缓存

`CwResourceService` 需要在现有 bundle key 上增加角色 recognizer 缓存：

- 新增 `role_recognizer_cls=VectorRoleIconRecognizer` 注入点。
- 新增 `_role_recognizers: dict[tuple[Any, ...], Any]`，使用与装备 recognizer 相同的 `_bundle_key()`。
- 新增 `role_recognizer_for_bundle(workspace_root, bundle)`，从 `bundle.role_features` 构造并缓存。
- 新增 `slots_read_resources(workspace_root)`，返回 `bundle.raw_config`、`bundle.guide_config_enriched` 和 role recognizer。
- `invalidate_workspace()` 必须同时清理 `_role_recognizers`。

`cw.slots.read` daemon handler 不应在热路径调用下载、prepare 或读取 workspace icon cache。它只从 `CwResourceService.slots_read_resources()` 取得预加载资源。无 `CwResourceService` 的测试或离线路径可以直接用 package bundle loader 构造 recognizer，但同样不能联网。

### 槽位几何

以 `1920x1080` canonical screenshot 为基准。运行时捕获整屏必须使用 `normalize=True`，并在尺寸不是 `1920x1080` 时失败为 `SLOTS_LAYOUT_MISMATCH`，不在识别器内做比例缩放。

目标卡面尺寸固定为 `103x120`。坐标为四点 `[tl,tr,br,bl]`，单位是 canonical screenshot 像素，`geometry_version="cw-slots-1920x1080-v2"`：

| slot | tl | tr | br | bl |
| --- | --- | --- | --- | --- |
| `front:1` | `(690.2,339.7)` | `(801.2,339.7)` | `(794.6,462.9)` | `(680.5,462.9)` |
| `front:2` | `(832.7,339.7)` | `(943.5,339.7)` | `(942.6,462.9)` | `(828.7,462.9)` |
| `front:3` | `(975.3,339.7)` | `(1085.9,339.7)` | `(1090.7,462.9)` | `(976.8,462.9)` |
| `front:4` | `(1117.8,339.7)` | `(1228.2,339.7)` | `(1238.7,462.9)` | `(1125.0,462.9)` |
| `back:1` | `(552.1,611.7)` | `(664.0,611.7)` | `(654.2,737.6)` | `(536.9,737.6)` |
| `back:2` | `(692.7,611.7)` | `(805.1,611.7)` | `(799.9,737.6)` | `(682.5,737.6)` |
| `back:3` | `(833.2,611.7)` | `(946.2,611.7)` | `(945.6,737.6)` | `(828.1,737.6)` |
| `back:4` | `(973.7,611.7)` | `(1087.4,611.7)` | `(1091.4,737.6)` | `(973.8,737.6)` |
| `back:5` | `(1114.2,611.7)` | `(1228.5,611.7)` | `(1237.1,737.6)` | `(1119.4,737.6)` |
| `back:6` | `(1254.7,611.7)` | `(1369.6,611.7)` | `(1382.9,737.6)` | `(1265.0,737.6)` |
| `hand:1` | `(381.3,860.0)` | `(484.7,860.0)` | `(484.7,980.0)` | `(381.3,980.0)` |
| `hand:2` | `(507.5,860.0)` | `(610.9,860.0)` | `(610.9,980.0)` | `(507.5,980.0)` |
| `hand:3` | `(633.6,860.0)` | `(737.0,860.0)` | `(737.0,980.0)` | `(633.6,980.0)` |
| `hand:4` | `(759.7,860.0)` | `(863.1,860.0)` | `(863.1,980.0)` | `(759.7,980.0)` |
| `hand:5` | `(885.8,860.0)` | `(989.2,860.0)` | `(989.2,980.0)` | `(885.8,980.0)` |
| `hand:6` | `(1012.0,860.0)` | `(1115.4,860.0)` | `(1115.4,980.0)` | `(1012.0,980.0)` |
| `hand:7` | `(1138.1,860.0)` | `(1241.5,860.0)` | `(1241.5,980.0)` | `(1138.1,980.0)` |
| `hand:8` | `(1264.2,860.0)` | `(1367.6,860.0)` | `(1367.6,980.0)` | `(1264.2,980.0)` |
| `hand:9` | `(1390.3,860.0)` | `(1493.7,860.0)` | `(1493.7,980.0)` | `(1390.3,980.0)` |

front/back 使用 `cv2.getPerspectiveTransform(src_quad, [(0,0),(102,0),(102,119),(0,119)])` 和 `cv2.warpPerspective()` 还原。hand 使用同一透视 API 处理矩形，避免裁切路径产生坐标差异。`--slot` 只处理目标槽位，但仍只需要一次整屏截图。

### `103x120` 区域定义

还原卡面坐标使用半开区间 `[x1,y1,x2,y2)`，原点在左上角：

- `target_size=(103,120)`。
- `avatar_roi=(5,4,98,108)`，用于角色 icon 图像相似度和 HSV 直方图。
- `base_mask` 初始全 0，先设置 `base_mask[4:108,5:98]=255`。
- 排除左上攻略标志：`base_mask[0:34,0:30]=0`。
- 排除右上前后台站位黑框：`base_mask[0:34,76:103]=0`。
- 排除底部费率条：`base_mask[112:120,0:103]=0`。
- 星级检测 ROI：`star_roi=(10,82,91,114)`。
- 费率采样条：`fee_strip=(4,112,99,119)`。

星星候选框会回写到 mask：对每个 NMS 后星星框，在 `103x120` 坐标系中扩张 2px，执行 `base_mask[y1:y2,x1:x2]=0`。扩张后坐标必须 clamp 到 `0<=x<=103`、`0<=y<=120`。不得因为检测到星级而屏蔽整条 `star_roi`。

### 空槽识别

空槽不是“角色低分”的唯一判断依据。正式识别包含两类空槽证据：

- `field` 空槽模板：适用于 `front/back`，来源 `roles/empty/field-v1.png`。
- `hand` 空槽模板：适用于 `hand`，来源 `roles/empty/hand-v1.png`。

空槽模板比较使用 `avatar_roi` 与同一 `base_mask`。空槽分数为 `0.70 * masked_mean_abs_similarity + 0.30 * hsv_hist_correlation`。判定规则：

- 若 `empty_score >= 0.82` 且 `empty_score - best_role_score >= 0.08`，判定为空槽，输出该位置为 `None`。
- 若 `best_role_score >= 0.58` 且 `best_role_score - empty_score >= 0.035`，进入角色判定。
- 若二者接近，保留 top role 作为低置信候选，`match_kind="low_confidence"`，并输出 `CW_ROLE_MATCH_LOW_CONFIDENCE` warning。
- 如果空槽模板高分且星级检测结果大于 0，以空槽为准，并在 verbose trace 记录 `ignored_star_boxes`，默认文本不输出该诊断。

### 角色相似度算法

角色 recognizer 使用 `roles/features.json` 中的预计算 feature，不读取磁盘上的 icon PNG 做热路径计算。

运行时处理步骤：

1. 对还原后的 `103x120` 卡面应用 `base_mask` 和星星小框 mask。
2. 同步 crop 图像和 mask 的 `avatar_roi=(5,4,98,108)`，得到 `93x104` 图像和 `93x104` mask。
3. 将图像 alpha composite 到不透明黑底 `(0,0,0,255)`，再用 `Image.Resampling.LANCZOS` resize 到 `64x64`。
4. 将 mask 用 `Image.Resampling.NEAREST` resize 到 `64x64`，有效像素为 mask 值 `>8` 的位置。
5. 对候选 role icon 的 `64x64` `icon_rgba` 做相同黑底 alpha composite；`icon_mask` 同样以 `>8` 判定有效像素。
6. 最终比较 mask 为 `runtime_mask AND icon_mask`；有效像素数 `<64` 时该 candidate score 记为 `0`。
7. 灰度值使用 `0.299R + 0.587G + 0.114B`。
8. `masked_ncc` 公式为 mask 加权 Pearson correlation：`corr=sum(w*(a-mean_a)*(b-mean_b))/sqrt(sum(w*(a-mean_a)^2)*sum(w*(b-mean_b)^2))`，再映射为 `(corr+1)/2` 并 clamp 到 `[0,1]`；分母为 0 时记为 `0`。
9. `masked_l1` 公式为 `1 - mean(abs(rgb_a-rgb_b))/255`，mean 只统计有效像素和 RGB 三通道，结果 clamp 到 `[0,1]`。
10. `hist_corr` 使用 OpenCV `cv2.cvtColor(RGB, COLOR_RGB2HSV)` 后的 HSV histogram intersection。H 范围 `[0,180)`，S/V 范围 `[0,256)`，bins 为 `[16,16,16]`；hist 只统计有效像素并归一化到 sum=1，分数为 `sum(min(hist_a,hist_b))`。
11. 角色基础分 `role_score = 0.50 * masked_ncc + 0.30 * masked_l1 + 0.20 * hist_corr`。
12. 取 top 8 candidates，按 `role_score` 降序保留到结构化诊断。

费率色条只影响 tie-break，不直接覆盖高置信图标结果。候选角色的费率 tier 使用 `rarity` 优先，`rarity` 缺失时使用 `cost`，二者都缺失时为 `unknown`。字段统一转成字符串后映射：

| tier | fee_color |
| --- | --- |
| `"1"` | `gray` |
| `"2"` | `green` |
| `"3"` | `blue` |
| `"4"` | `purple` |
| `"5"` | `gold` |
| other/missing | `unknown` |

费率色条 tie-break 规则：

- 若 top1 与 top2 分差 `<0.035`，且只有其中一个候选的 tier 与费率色条一致，则选择费率一致候选。
- 若 top1 分数 `>=0.58` 且分差 `>=0.035`，即使费率冲突也保留 top1，但该槽位标记低置信并输出 warning。
- 费率一致可以把 `confidence_reason` 记为 `icon_fee_match`；费率冲突记为 `icon_fee_conflict`。

确定识别规则：

- `best_role_score >= 0.58`。
- `best_role_score - second_role_score >= 0.035`。
- 未被空槽规则覆盖。

低置信规则：

- `0.50 <= best_role_score < 0.58`，或分差 `<0.035`，或费率冲突。
- 低置信仍可返回 canonical role，但必须在 response warning 中给出位置、query、resolved、score、candidates。
- 低置信诊断只保存在 response snapshot，不写入 persisted session slots。

unknown 规则：

- `best_role_score < 0.50` 且空槽规则未命中时，不得把该槽位返回为 `None`，因为 renderer 会把 `None` 渲染成 `empty=1` 并污染 role_count、装备推荐和手牌判断。
- 全量读取中出现 unknown 时，命令失败为 `SLOTS_RECOGNITION_UNCERTAIN`，携带识别截图和 warning detail，session slots 不更新。
- 局部读取中出现 unknown 且上一份 `cw_state["slots"]` 为 fresh 时，保留 previous slot value，response warning 增加 `preserved_previous=1`，本次 response/session 不把该槽位改为空。
- 局部读取中出现 unknown 且没有 fresh previous value 时，同样失败为 `SLOTS_RECOGNITION_UNCERTAIN`。

### 星级检测

星级检测复用 `trail/scenes/cw/assets/star.png`，但不再对完整卡面调用现有 `_count_slot_stars_in_image()`。新流程：

1. 只在 `star_roi=(10,82,91,114)` 内检测。
2. `assets/star.png` 读取为 RGBA，alpha composite 到不透明黑底后转灰度；ROI 同样转灰度。
3. 使用 OpenCV `cv2.matchTemplate(roi_gray, template_gray, cv2.TM_CCOEFF_NORMED)`。
4. 使用固定三尺度 `[0.65, 0.75, 0.80]`。
5. 模板缩放尺寸使用 `round(original_size * scale)`，宽高最小为 `1`，resize 插值使用 `cv2.INTER_AREA`。
6. 模板匹配阈值固定为 `0.78`。
7. 三个尺度的候选统一按 score 降序做 NMS。
8. NMS radius 固定为 `8px`，两个候选中心点欧氏距离 `<8` 时保留 score 高者。
9. 输出星级数量等于 NMS 后候选数，最大不超过 3。
10. 检出的星星小框回写到头像 mask 中，只屏蔽星星小块。

结构化诊断中的 `star_boxes` 使用还原卡面坐标，格式为 `[{"x": int, "y": int, "w": int, "h": int, "score": float, "scale": float}]`。默认文本只输出 `star=<n>`，不输出 boxes。

### 费率色条

费率色条从 `fee_strip=(4,112,99,119)` 采样，作为辅助证据，不直接覆盖角色图标识别结果。

初始 HSV 分桶：

- `gray`：平均 saturation `<28`。
- `green`：`75<=h<=98` 且 saturation `>=28`。
- `blue`：`105<=h<=122` 且 saturation `>=28`。
- `purple`：`123<=h<=137` 且 saturation `>=28`。
- `unknown`：不落入上述区间。

配置映射使用“角色相似度算法”中冻结的 tier 表：`rarity` 优先，`cost` 兜底，`1=gray`、`2=green`、`3=blue`、`4=purple`、`5=gold`，其他值为 `unknown`。当前样本必须覆盖灰、绿、蓝、紫；未覆盖颜色输出 `fee_color=unknown` 或 `gold` 进入 verbose trace，不阻止高置信图标识别。

### 数据流

`cw.slots.read` 角色读取流程：

1. `build_cw_status_reader()` 继续读取 stage/status，本设计不重写这部分。
2. `build_cw_slot_roles_reader()` 调用 `runtime.capture_image(normalize=True)` 捕获一次整屏 PIL 图像。
3. 读取角色 recognizer、几何配置、空槽模板和星级模板；这些资源均来自静态 bundle 或源码资产。
4. 解析 `targets`；无 targets 时处理全部 19 个槽位。
5. 对每个目标槽位按几何还原为 `103x120` 卡面。
6. 检测星级，生成星星 mask。
7. 检测空槽分数、费率色条和角色 icon candidates。
8. 构造原始 slot 值，例如 `{"name": "缇宝", "role_id": "...", "star": 2, "score": 0.63, "match_kind": "icon"}`。
9. 复用现有 catalog canonicalization，把名称转换为 canonical `name`、`role_id`、`traits`、`rarity/cost`。
10. 复用现有局部读取 merge、stale、equipment preservation 和 response snapshot 逻辑。

默认路径不再点击每个角色详情。旧点击/OCR reader 可以保留为测试替身或显式诊断路径，但必须满足以下约束：

- 默认 `slots_reader_factory(runtime)` 不再返回旧 reader。
- 旧 reader 不得由 `cw.slots.read` 正常 CLI/daemon 路径触发。
- 如果保留环境开关，只能使用 private/dev-gated 名称，例如 `TRAIL_CW_SLOTS_LEGACY_OCR_READER=1`，且测试必须覆盖默认路径未启用。
- 旧 reader 的 `SLOT_NAME_REGION` 和姓名 OCR 不得参与默认 role recognition。

### 截图复用契约

`cw.slots.read` 必须使用识别用的同一张截图作为 envelope 顶层截图。不能让 `with_selective_capture()` 在识别结束后再调用 `runtime.capture_after_action()` 生成第二张截图。

实现要求：

- `CwSlotsReadResult` 增加 private 字段 `screenshot: str | None = None`，或等价 metadata 字段。
- slots reader 捕获 PIL 图像后，如果 runtime 支持 `save_capture_image_to_workspace(image, request_id=...)`，必须保存该图像并把路径放入 result。
- daemon `cw.slots.read` handler 必须像 `cw.equipment.read` 一样把 `request_id` 传入 slots reader。
- `read_cw_slots()` 必须把 result 中的截图路径转移到 `response_snapshot["_screenshot"]`。
- `cw_service` handler 返回 `read_cw_slots(...).response_snapshot`，使 `with_selective_capture()` 的 `_pop_precaptured_screenshot()` 消费顶层 `_screenshot`。
- `_screenshot` 是 private transport 字段，不写入 `cw_state["slots"]`，不进入默认文本 data body，不进入 YAML。
- 如果 runtime 不支持保存复用截图，本地非 daemon 测试路径可以回退到现有 capture，但 daemon contract 测试必须覆盖不会二次调用 `capture_after_action()`。
- `SLOTS_RECOGNITION_UNCERTAIN` 这类 slots 识别失败也必须复用识别截图。实现可以使用带 `screenshot`/`warnings` 属性的 `TrailError` 子类，或扩展 capture failure path 读取异常上的 precaptured screenshot；不得在失败后再截一张与识别事实不一致的截图。
- mutation 路径目前使用 `with_auto_capture()`，而 `with_auto_capture()` 不会弹出 `_screenshot`。实现必须扩展 capture 层，让 `with_auto_capture()` 在 success 路径也调用 `_pop_precaptured_screenshot()` 并从 `data` 中移除 private 字段；如果弹出截图存在，则作为 envelope screenshot，不能再调用 `capture_after_action()`。该变更必须保持 failure 路径现有 optional capture 语义。

### `cw.portal.select` 自动收集路径

`cw.portal.select` 成功进入备战页后会自动收集 slots/equipment/shop。slots 图标识别改造必须覆盖这条嵌套路径。

实现要求：

- `_select_portal_and_apply_selected_guide()` 调用 `read_cw_slots()` 时必须使用新的 icon reader 和 `CwResourceService.slots_read_resources()`。
- portal auto-collect 内嵌 slots reader 必须接收外层 `request_id`，并使用它保存识别截图。
- `slots_snapshot = _response_snapshot_or_fallback(...)` 后必须弹出嵌套 `_screenshot`，避免把 private 字段留在 `data.slots`。
- 若 `selected_data` 尚无顶层 `_screenshot`，使用 slots 的 `_screenshot` 作为 `cw.portal.select` 顶层截图。
- 因 `cw.portal.select` 是 mutation command，顶层 `_screenshot` 必须被扩展后的 `with_auto_capture()` 消费并从 data 移除；不得泄漏到 rendered data 或 session。
- slots warnings 继续通过 `_pop_response_snapshot_warnings(slots_snapshot)` 汇总到 `selected_data["warnings"]`。
- 若内嵌 slots 识别因 `SLOTS_RECOGNITION_UNCERTAIN` 失败，`cw.portal.select` 不应在已执行选环境、应用攻略或收集水晶后把整体结果变成普通失败；该 auto-collect 失败降级为 `warn code=CW_SLOTS_AUTO_COLLECT_UNCERTAIN ...`，保留已完成的 portal facts，并继续后续 equipment/shop 收集。若 session 中已有 fresh slots，可在 warning 中标记 `preserved_previous=1` 并复用 previous projection；否则 `data.slots` 缺省或 stale，不输出误导性的空槽。
- equipment 和 shop 的 warnings 继续保留当前 soft-warning 语义；slots 低置信 warning 不得被 equipment stale suppression 过滤。
- `cw.portal.select` 默认正文标题顺序保持现有 renderer：`# 综合信息`、`# 攻略提示`、`# 角色信息`、`# 羁绊信息`、`# 装备信息`、`# 装备优先级`、`# 角色装备需求`、`# 商店信息`。
- `cw.portal.select` 带截图 success 仍按项目协议先输出 `shot path=...`，紧跟 `info read_image_first=1`。
- 自动收集的 `data.slots` 不包含 `_screenshot`，但其 slot facts、warnings 和顶层 screenshot 必须来自同一次 slots 图标识别。

### 输出与诊断

默认文本协议保持不变：`ok cw.slots.read front=<n> back=<n> hand=<n> stale=0|1`，正文仍输出 `slot`、羁绊 `info`、stage/status `info` 与 warning。

默认文本 slot 行保持现有 renderer 字段：

- 常规角色：`pos`、`name`、`star`、`traits`、`rarity`、`carry`、`cost`。
- 空槽：`pos`、`empty=1`。
- 低置信角色：继续允许 `raw_name`、`score`、`match_kind`、`traits`、`star`。

`role_id` 属于结构化 response/session 字段，不进入默认文本 slot 行。若 renderer 未来要输出 `role_id`，必须另行更新 `AGENTS.md`、renderer 契约测试和 active skills；本次不做。

结构化 slot item 可以包含以下诊断字段：

- `match_kind="icon" | "low_confidence" | "ambiguous" | "unknown"`。
- `score` 或 `match_score`：最终用于排序和 warning 的 role score。
- `raw_name`：top candidate 原始名称；图标路径下不是 OCR 文本。
- `candidates`：最多 8 个，格式 `[{"name": str, "role_id": str, "score": float, "fee_match": bool | None}]`。
- `empty_score`：空槽模板分数。
- `fee_color`：`gray|green|blue|purple|gold|unknown`。
- `confidence_reason`：例如 `icon_clear`、`icon_fee_match`、`icon_fee_conflict`、`empty_clear`。

持久化约束：

- `cw_state["slots"]` 只保存 canonical role facts：`name`、`role_id`、`traits`、`star`、`rarity/cost`、`equipments` 等稳定字段。
- `raw_name`、`score`、`match_score`、`match_kind`、`candidates`、`empty_score`、`fee_color`、`star_boxes`、`confidence_reason` 不持久化到 session。
- warnings 只存在于 response/envelope，不写入 `cw_state["slots"]`。

warning 使用现有 role match warning codes，不新增正文前缀：

- 低置信：`CW_ROLE_MATCH_LOW_CONFIDENCE`。
- 多候选接近：`CW_ROLE_MATCH_AMBIGUOUS`。
- 兼容 fuzzy 归一化：继续使用现有 `CW_ROLE_MATCH_FUZZY`。

slot warning payload 使用现有 renderer 支持字段：

```json
{
  "code": "CW_ROLE_MATCH_LOW_CONFIDENCE",
  "position": {"kind": "slot", "area": "front", "index": 0},
  "query": "缇宝",
  "resolved": "缇宝",
  "score": 0.54,
  "candidates": ["缇宝:0.54", "希儿:0.51"],
  "message": "角色图标低置信，请先看截图确认"
}
```

`--verbose` 可追加图像识别 trace，但必须经过 `trail.output.debug.collect_debug_events` 和 `render_debug_lines`，不得直接拼接 stdout。建议 trace 字段：`geometry_version`、`recognizer_algorithm_version`、`slot`、`top_candidates`、`empty_score`、`fee_color`、`star_boxes`、`mask_version`。默认模式不得泄漏 verbose 事件。

## 测试计划

新增或调整测试：

1. 资源包构建测试：角色 manifest、icon、features、empty templates 写入并被 `load_cw_resource_bundle_from_path()` 校验。
2. 静态资源 loader 测试：bundle 缺失/损坏角色资源 fail fast；release 不联网；workspace equipment override 不影响 role resources。
3. artifact verifier 测试：wheel/sdist/windows zip/pyinstaller tree 都校验 `roles/` 目录、role icons、empty templates、重复 member、未引用文件和 schema mismatch。
4. `CwResourceService` 测试：同一 bundle key 缓存 role recognizer；`invalidate_workspace()` 清理 role recognizer；热路径不调用下载/prepare。
5. 槽位几何测试：19 个槽位坐标、透视还原目标尺寸、`--slot` 子集处理、非 `1920x1080` 截图失败。
6. mask/ROI 测试：`base_mask`、`star_roi`、`fee_strip`、星星小框回写 mask 坐标完全匹配本 spec。
7. 空槽测试：front/back 空槽和 hand 空槽分别识别为空，不误判为角色；空槽星级误检被忽略。
8. 角色匹配测试：使用真实截图夹具验证银枝、缇宝、星期日、灵砂、忘归人、那刻夏、阮•梅、大丽花、藿藿、娜塔莎、希儿、遐蝶、海瑟音、波提欧。
9. 星级测试：三尺度检测能识别缇宝 2 星、娜塔莎 1 星、希儿 1 星，空槽不误检。
10. 费率色条测试：灰、绿、蓝、紫样本分类正确，并在候选接近时影响 tie-break。
11. 输出契约测试：`cw.slots.read` 默认文本字段和 warning 顺序不变；`role_id` 不出现在默认文本；局部读取 stale/merge 语义不变。
12. RPC/capture 测试：daemon `cw.slots.read` 返回顶层 screenshot 与识别截图一致，且成功路径不调用第二次 `capture_after_action()`。
13. `cw.portal.select` 自动收集测试：嵌套 `data.slots` 不含 `_screenshot`；顶层 screenshot 使用 slots 识别截图；slots warning 被汇总到顶层 warnings；equipment auto collect warning suppression 不过滤 slots warning。
14. 逐槽详情禁用测试：默认 `cw.slots.read` 不调用逐槽详情点击、不读取 `SLOT_NAME_REGION`、不调用姓名 OCR 或 legacy reader；允许覆盖层关闭、stage/status OCR 和其他非角色姓名读取所需 UI 操作。
15. verbose debug 测试：`--verbose` 输出 `debug kind=trace` 图像识别事件；默认模式不输出 debug、不改变事实集合和顺序、diagnostic 字段不进入 data/session。
16. YAML allowlist 测试：`trail --format yaml cw slots read` 继续返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。
17. active skill 文档测试或文本检查：同步 `skills/trail-cw-prep/SKILL.md` 和 command surface，说明先读截图、slots 图标识别低置信 warning 与 portal auto-collect 行为。

## 风险与缓解

1. **UI 布局随版本变化**：把几何版本和目标尺寸集中定义，并通过真实截图夹具锁定；后续变化只更新几何配置和测试。
2. **相似头像误判**：结合相似度 margin、费率色条、空槽分数和 catalog 位置约束；低置信输出 warning。
3. **星级遮挡头像**：只屏蔽检测到的星星小块，避免整行屏蔽损失头像信息。
4. **未覆盖费率颜色**：未覆盖颜色统一归为 `unknown`，不阻断高置信图标识别；新增样本后只通过测试扩充分桶。
5. **资源包体积增加**：角色 icon 数量有限，规模小于装备特征；verifier 继续校验 artifact 内容和 hash。
6. **截图错位**：强制 `_screenshot` 复用识别截图，并用 daemon contract 测试禁止成功路径二次 capture。

## 验收标准

1. `cw.slots.read` 默认角色读取不再逐槽位点击详情面板。
2. release 默认运行不联网即可识别角色图标。
3. 最新实验阵容能稳定识别角色、空槽、星级和费率色条。
4. `cw.slots.read` 顶层 screenshot 与角色识别使用的是同一张截图。
5. `cw.portal.select` 自动收集的 slots facts、warnings 和顶层 screenshot 语义一致，且不把 `_screenshot` 泄漏到 `data.slots`。
6. 现有 `cw.slots.read` 输出协议、session slots shape 和下游装备推荐/出售计划语义保持兼容。
7. 快速回归 `uv run pytest` 通过，相关新增 focused tests 覆盖资源包、识别、输出、capture 和 no-network 行为。
