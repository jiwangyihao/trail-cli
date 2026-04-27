# CW 装备图标识别设计

## 背景

当前项目没有独立的装备识别命令。装备相关数据主要来自 `guide.fetch.cw` 的攻略接口字段，例如 `order_basic`、`order_compose`、角色 `first_equipments` 与 `second_equipments`。现有画面识别主要集中在 `cw.slots.read`、`cw.shop.scan`、`cw.portal.*`、`cw.strategy.*`、`cw.stage` 等流程。

现有图片 detect 依赖 `RuntimeOperator.locate()` 与 `PyScreezeMatcher.locate()`，最终调用 `pyscreeze.locate(template, screenshot, confidence=0.9)`。`cw.slots.read` 的星级检测也直接调用 `pyscreeze.locateAll(..., confidence=0.9)`。仓库中未发现多尺度模板匹配、SIFT、ORB 或模型分类实现。

因此，若干净装备图标资源是 `128x128`，而游戏内装备格显示为 `48x48`、更小或非标准尺寸，直接用现有 `runtime.locate()` 做模板匹配会受影响。游戏内装备格还带边框、圆角、品质背景或阴影，直接拿干净图标匹配带边框截图会进一步降低可靠性。

已确认约束：

- 装备识别必须以图标识别为主，文字 OCR 耗时与复杂度不可接受。
- 装备资源接近原始干净图标，尺寸约为 `128x128`。
- 装备资源来源为 CW guide config 的 `equipment_list[*].icon`，基础装备从所有进阶装备的 `compose_list[*].childrens[*].icon` 与对应装备信息综合派生。
- 游戏内装备格位置为规则网格，项目当前以 canonical `1920x1080` 坐标工作，可以先不考虑分辨率变化。
- 用户不一定能提供完整几十个装备格的截图，但可以接受通过相邻格子标定规则网格。
- 装备种类约一百多个。
- 第一版采用无模型识别，但必须预留后续模型分类 backend。
- 网格裁切坐标已通过实机截图和用户反馈确认首版 profile；后续实施计划必须使用该 profile，不在开发阶段纯猜。

## 已确认决策

- 不直接使用 `pyscreeze.locate()` 在整屏或装备资源 atlas 上做装备类别识别。
- 使用已确认静态 profile 裁出完整 `70x70` 装备格 ROI，不通过缩小内圈裁切来修正边框偏移。
- 将 `70x70` ROI resize / 归一化到资源特征空间，把问题从二维滑动查找转为固定类别相似度匹配。
- 装备 catalog 从 raw config 中派生，不要求用户提供外部装备图片目录。
- 第一版实现 `vector/template` backend：轻量特征预筛 Top-K，再做精匹配评分。
- 接口预留 `model` backend，后续可以替换或并行接入图标分类模型。
- 低置信或候选差距过小的格子输出 `uncertain=1`，不硬猜。
- 新识别命令 canonical command 固定为 `cw.equipment.read`，输出遵循现有默认文本协议，不新增正文前缀。

## 目标

1. 为货币战争装备图标识别设计可复用的网格裁剪、图标识别和命令输出边界。
2. 支持从规则网格页面批量识别几十个装备格。
3. 支持用一百多个 `128x128` 干净装备资源作为类别库。
4. 对游戏内缩放、非整数显示尺寸和边框干扰具备基础鲁棒性。
5. 输出每个格子的装备候选、分数、候选差距和不确定状态，便于调阈值与人工核验。
6. 为后续模型分类 backend 预留稳定接口，避免重写命令层与网格层。
7. 补齐单测、输出契约测试和必要文档。

## 非目标

- 不在第一版训练或内置模型分类器。
- 不依赖文字 OCR 识别装备名称。
- 不要求用户提供完整几十格截图才能确认布局；当前已通过包含三列、六行可见装备的截图确认首版规则网格坐标/profile。
- 不把装备干净图标直接与带边框装备格整图匹配。
- 不新增默认文本正文前缀。
- 不把 `cw.equipment.read` 加入 YAML allowlist，除非后续明确存在结构化兜底需求。
- 不改变现有 `guide.fetch.cw` 的攻略装备字段语义。

## 方案比较

### 方案 A：规则网格裁剪 + 向量/模板匹配（采用）

按已确认规则网格裁出完整 `70x70` 装备格 ROI，并将 ROI resize / 归一化到资源特征空间。装备资源预处理成同尺寸特征库。识别时先用低成本特征取 Top-K，再用更精细的相关性、mask 后像素距离或结构特征确认。

优点是实现成本低、可解释、无需训练数据、便于输出 `score/gap/top2`。缺点是对图标遮挡、动态特效和极相似图标的鲁棒性弱于模型。

### 方案 B：装备资源 atlas + `pyscreeze.locate()`

把所有 `128x128` 装备资源拼成大图，再拿裁剪出的装备图标在 atlas 上 detect。

优点是概念上接近“在资源库里找图”。缺点是仍然使用二维滑动模板匹配，性能不一定更好；截图图标带边框、插值和压缩差异，资源是干净图标，直接 locate 仍容易失败；输出类别分数与候选差距也不如固定类别匹配清晰。

### 方案 C：模型分类（预留）

训练或引入图标分类模型，对裁剪后的装备格或中心图标直接输出类别概率。

优点是潜在鲁棒性最好，可通过数据增强覆盖缩放、边框、噪声和相似图标。缺点是需要样本、训练/推理依赖、模型版本管理和额外测试。第一版只预留接口，不直接实现。

## 模块设计

### 网格裁剪层

新增内部模块，建议命名为 `trail/scenes/cw/equipment_grid.py`，负责纯图像和坐标处理。

核心职责：

- 表达规则网格布局，例如 `origin_x/origin_y/cell_width/cell_height/gap_x/gap_y/rows/cols`。
- 从 canonical `1920x1080` 截图或指定页面截图中批量裁出装备格。
- 使用已确认 `70x70` profile 生成完整装备格 ROI；profile 的 `x/y` 表示 ROI 左上角，`box=(x,y,70,70)`。
- 保留 `idx/row/col/box` 这类定位事实，供输出和调试使用；默认文本中的 `box` 若需要输出，必须压成 `left,top,width,height`。

第一版不使用固定缩小裁切作为主方案。若实机验证发现个别 UI 状态需要兜底，可在识别层引入少量调试候选，但默认 profile 仍以完整 `70x70` ROI 为准。

### 识别 backend 层

新增内部模块，建议命名为 `trail/scenes/cw/equipment_recognition.py`，负责装备资源索引与图标识别。

建议接口：

```python
class EquipmentIconRecognizer(Protocol):
    def recognize(self, image) -> list[EquipmentCandidate]:
        pass
```

候选结果至少包含：

```python
{
    "equipment_id": "equip_high_frequency_chainsaw",
    "name": "高周波电锯",
    "score": 0.94,
    "backend": "vector",
}
```

第一版 `vector` backend：

1. 加载运行期图标缓存 manifest，生成装备 catalog。
2. 将资源统一转为 `128x128` RGB/RGBA 图。
3. 为每个资源计算低分辨率向量、颜色直方图、灰度/边缘向量等轻量特征，并保留 alpha mask 参与精匹配。
4. 对 `70x70` ROI resize / 归一化到资源特征空间，计算同类特征；透明边缘和装备格边框背景不应主导得分。
5. 用轻量特征快速筛选 Top-K；默认 Top-K 范围建议为 `5..10`，实施计划中固定具体默认值。
6. 对 Top-K 做精匹配评分，例如归一化相关性、alpha mask 后像素距离或灰度结构相似度；floor/ceil 双候选只对非整数列启用，避免 `格子数 * 资源数 * 候选数` 退化。
7. 返回按分数降序排列的候选列表。

后续 `model` backend 使用同一接口，输出同样的候选结构。命令层只读取候选列表，不依赖具体算法。

### 命令层

新增场景模块可命名为 `trail/scenes/cw/equipment.py`，负责串联 runtime、grid、recognizer 和 session。

识别命令：

```text
trail cw equipment read --session <id>
```

canonical command：

```text
cw.equipment.read
```

资源准备命令：

```text
trail cw equipment prepare --session <id> [--refresh]
```

canonical command：

```text
cw.equipment.prepare
```

`cw.equipment.prepare` 不产出截图，归入检测/状态摘要 renderer 家族；success 首行固定为：

```text
ok cw.equipment.prepare big_version=<version> count=<catalog_count> cached=<n> downloaded=<n> refreshed=0|1
```

`--refresh` 只影响同一 `rpg_game_big_version` 下 `icon_url` 与 manifest 不一致的条目；普通 prepare 只补齐缺失或损坏图标。`cw.equipment.read` 可以自动执行普通 prepare 语义，但不能因同版本 URL 变化访问网络，除非用户显式运行 `cw.equipment.prepare --refresh`。

命令层职责：

- 确认当前页面适合读取装备格，或由调用方保证页面状态。
- 获取截图或局部截图。
- 调用网格裁剪层生成装备格候选图。
- 调用 recognizer backend 得到每个格子的候选列表。
- 应用阈值策略，标记识别成功、空格或不确定。
- 返回可渲染结果。第一版不把识别结果写入 `cw_state` 长期状态；session 只用于绑定当前 runtime/window。后续若需要持久化，应新增明确的 `cw_state.equipment.last_read` 设计。

## 阈值与不确定策略

每个格子最终输出包含 Top-1 和可选 Top-2。建议使用两个条件决定是否可信：

- `score >= min_score`
- `gap = top1_score - top2_score >= min_gap`

若任一条件不满足，输出 `uncertain=1`，并保留 Top-1 / Top-2 名称与分数，便于调阈值。默认文本可压缩为 `name/score/gap/uncertain/alt/alt_score`；结构化 data 中保留完整 candidates。空格识别可以先采用简单图像统计规则，例如中心区亮度/饱和度/边缘密度过低，后续再按真实截图调整。

阈值应集中在配置常量或 layout/profile 中，不散落在命令逻辑里。第一版测试可以使用保守阈值，实机验证后再收紧。

## Renderer 家族与输出协议

`cw.equipment.read` 归入列表读取 renderer 家族，类比 `cw.slots.read` / `cw.shop.scan` 的当前画面事实读取。success 首行字段顺序固定为：

```text
ok cw.equipment.read count=<n> uncertain=<n> empty=<n>
```

`count`、`uncertain`、`empty` 是 must-keep 事实，即使为 `0` 也不能省略。success 正文顺序固定为：首行 -> `shot` -> `info read_image_first=1` -> `item` -> 其余 `info` -> `warn` -> `ref`。`cw.equipment.read` 不加入 YAML allowlist；`--format yaml` 必须返回格式不支持错误。

`cw.equipment.prepare` 归入检测/状态摘要 renderer 家族，不产出截图。success 首行字段顺序固定为：

```text
ok cw.equipment.prepare big_version=<version> count=<n> cached=<n> downloaded=<n> refreshed=0|1
```

`count/cached/downloaded/refreshed` 是 must-keep 事实，即使为 `0` 也不能省略。`cw.equipment.prepare` 不加入 YAML allowlist。

## 输出设计

`cw.equipment.read` 默认文本使用现有前缀。示例：

```text
ok cw.equipment.read count=24 uncertain=2 empty=6
item idx=1 row=1 col=1 name=高周波电锯 score=0.94 gap=0.18
item idx=2 row=1 col=2 name=胜利之旗 score=0.88 gap=0.03 uncertain=1 alt=战场进化手册 alt_score=0.85
info backend=vector layout=default
warn code=LOW_CONFIDENCE count=2 msg="装备图标低置信，请先看截图确认"
```

如果命令产出截图，必须遵守现有输出协议：

```text
ok cw.equipment.read count=24 uncertain=2 empty=6
shot path=.trail/captures/cw-equipment-read-demo.jpg
info read_image_first=1
item idx=1 row=1 col=1 name=高周波电锯 score=0.94 gap=0.18
```

失败路径只用于整体不可执行的情况，例如 raw config 不可用、缺少 `rpg_game_big_version`、图标缓存补齐失败、补齐后图标仍无法被 PIL 打开、layout 缺失、截图失败或当前页面明显不匹配。单个格子低置信不让整个命令失败。

## 数据与资源管理

装备资源从 CW guide config 派生，而不是从用户维护的外部图片目录派生。当前 `.trail/cache/cw-guide-config.json` 已能看到 `equipment_list`，其中进阶装备包含 `id/name/desc/icon/priority/compose_list/category/category_name` 等字段；基础装备出现在各进阶装备的 `compose_list[*].childrens` 中，且同样包含 `id/name/icon/category/category_name` 等字段。

新增 normalization helper 时应生成稳定装备 catalog：

```python
{
    "id": "350701",
    "name": "财富宝钻",
    "icon": "https://act-webstatic.mihoyo.com/.../2bd44c60d52705959183b5014e9197ab.png",
    "kind": "advanced",
    "category": "4",
    "category_name": "...",
}
```

基础装备去重规则以 `id` 为主；`id` 缺失时再按 `name + icon` 去重。进阶装备直接来自 `equipment_list` 顶层；基础装备从所有 `compose_list[*].childrens` 汇总。若同一个基础装备在多个合成路径中重复出现，只保留一条 catalog entry。为避免基础装备和进阶装备同 id 覆盖，本地缓存 key 使用 `kind + id`；若 id 缺失，则使用 `noid-<sha1(name + icon_url)>`。

图标下载与缓存应独立于识别算法：catalog 保存远程 `icon` URL，资源缓存层负责下载并保存本地图标文件，识别层只消费已加载的 PIL image。

缓存分代只使用 raw config 中的 `rpg_game_big_version`，避免把 season、sub-season、lineup filter 或 catalog digest 组合进复杂签名。建议目录：

```text
.trail/cache/cw-equipment-icons/<rpg_game_big_version>/
```

该目录内保存：

```text
manifest.json
icons/<cache_key>.png
```

`manifest.json` 记录本 big version 的装备 `cache_key/id/name/kind/category/category_name/icon_url/local_path`。`rpg_game_big_version`、`cache_key` 和本地文件名必须安全化：禁止路径分隔符、`..`、绝对路径和 Windows 保留文件名；无法安全化的值应使用稳定 hash。缺少或为空的 `rpg_game_big_version` 是资源准备错误，不能落到 `unknown` 目录。

同一 `rpg_game_big_version` 下：

- 缓存完整且图标文件可被 PIL 打开时，`cw.equipment.read` 直接复用本地缓存，不访问网络。
- 缓存缺失或图标文件损坏时，只下载缺失或损坏项。
- 用户显式运行 `cw.equipment.prepare --refresh` 时，重新读取当前 raw config；若同一 `cache_key` 的 `icon_url` 与 manifest 不同，则重新下载该装备图标并更新 manifest。
- 不因为时间经过或识别低分自动刷新缓存。

当 `rpg_game_big_version` 改变时，创建新的版本目录；旧目录保留，后续可单独做 GC。写入图标和 manifest 时使用临时文件加原子 replace，避免半写缓存污染识别。

下载与并发安全：

- 只接受 `https` 图标 URL；第一版允许域名应限制为米游社静态资源域名或配置中的明确 allowlist。
- 下载必须设置 timeout 和响应大小上限，避免命令长期阻塞或写入异常大文件。
- 下载结果必须先由 PIL 打开验证，再原子 replace 到 `icons/<cache_key>.png`。
- 每个 `rpg_game_big_version` 目录需要文件锁或等价互斥，避免并发 prepare/read 写出半份 manifest。
- manifest 写入必须在锁内完成，并使用临时文件加原子 replace。

若资源有透明 alpha，特征计算需要明确背景处理策略。建议同时保留 alpha mask，用于精匹配阶段降低透明边缘和背景差异的影响。若游戏内中心图标有固定品质底色，后续可以为资源合成几个背景增强版本，但第一版不强制。

## 已确认 Grid Profile

已基于截图 `.trail/shots/b8f84c694dc1447e82373a1707e524c0-ccae41b735.jpg` 和用户提供的装备真值确认首版静态裁切 profile。该截图中顶部“装备追踪中”两个格子不属于 inventory grid；inventory grid 排布方向为从右到左、从上到下。

装备真值用于校准：

```text
C1R1 幸运星，C1R2 量产型装甲，C1R3 生命之花，C1R4 量产型装甲，C1R5 光能电池，C1R6 和平手枪
C2R1 以太钻头，C2R2 光能电池，C2R3 轮滑鞋，C2R4 量产型装甲，C2R5 和平手枪，C2R6 幸运星
C3R1 幸运星，C3R2 幸运星，C3R3 光能电池，C3R4 轮滑鞋
```

静态裁切 profile：

```text
flow=right_to_left_then_top_to_bottom
tracked_top_slot_count=2
tracked_top_slots_excluded=1
crop_size=70x70
col1_left=1820.0
col_step=79.75
row_origin_top=240.0
row_step=77.5
row_rounding=round_half_up
```

`tracked_top_slots_excluded=1` 是布尔值，表示顶部 `tracked_top_slot_count=2` 个“装备追踪中”格子不进入 inventory grid。`x/y` 坐标均表示 `70x70` ROI 左上角。

列坐标按浮点递推保存，避免后续列出现累计误差：

```text
x_left(col_from_right) = 1820.0 - (col_from_right - 1) * 79.75
```

当前可见列近似为：

```text
C1 x=1820.00
C2 x=1740.25
C3 x=1660.50
```

实际裁切实现可以二选一：

```text
优先：支持 float crop/resample，直接使用浮点 crop box。
兜底：对非整数列生成 floor/ceil 两个候选，识别时取分数更高者。
```

行坐标按半像素步长递推并 half-up 取整：

```text
y(row) = round_half_up(240.0 + (row - 1) * 77.5)
```

当前可见行得到：

```text
R1 y=240
R2 y=318
R3 y=395
R4 y=473
R5 y=550
R6 y=628
```

校准过程产物：

```text
匹配叠加图：.trail/shots/equipment-known-matches-overlay.jpg
裁切预览：.trail/shots/equipment-static-crops-v4-70x70-c3r4-adjusted.jpg
最终叠加图：.trail/shots/equipment-static-overlay-v4-70x70-c3r4-adjusted.jpg
```

若未来页面滚动或装备数量超过当前可见范围，继续使用同一水平递推；垂直方向从当前首屏六行向下递推前，需要用新的滚动截图确认滚动偏移。

## 测试策略

1. 资源索引测试：使用少量 fixture 图标，验证 manifest 加载、尺寸归一化、特征缓存和重复 id/name 处理。
2. 网格裁剪测试：锁定已确认 profile，覆盖三列六行、`C1/C2/C3` 浮点列坐标、`R4=473` half-up 用例、`70x70` ROI、float crop 或 floor/ceil 双候选，以及已知装备真值 fixture。
3. 缓存契约测试：覆盖 `rpg_game_big_version` 缺失失败、目录名/文件名安全化、完整缓存不访问网络、缺失图标补齐、损坏图标重下、big version 切换新建目录、同版本 URL 变化只在 `cw.equipment.prepare --refresh` 时重下、并发 prepare 不产生半写 manifest。
4. 缩放鲁棒性测试：把 `128x128` 资源缩到 `48x48`、`64x64`、非整数尺寸，再放进带边框格子，验证 resize 回 `128x128` 后能识别。
5. 混淆测试：准备两个颜色或轮廓接近的装备，验证 `gap` 太小时返回 `uncertain=1`，并保留 Top-1 / Top-2 分数。
6. 轻量性能测试：用几十个格子和一百多个资源跑一次，防止算法退化到不可用；断言资源特征按版本缓存、每个 ROI 特征只算一次、精匹配只对 Top-K 执行。
7. 输出契约测试：覆盖 `cw.equipment.read` 与 `cw.equipment.prepare` 首行、`item` 行、`info backend/layout`、低置信 `warn` 行、截图 guidance 顺序和 YAML 不支持语义。
8. 文档测试：更新 README 与相关 active skill，说明装备识别依赖截图确认和低置信处理。

真实截图不要求第一版完整覆盖几十格；先用合成图锁定算法边界，拿到样例截图后再补回归 fixture。

## 实施顺序建议

1. 实现已确认 grid profile 的纯函数裁切，包括 float 列坐标或 floor/ceil 双候选兜底。
2. 实现从 raw config 派生装备 catalog，包括进阶装备与 `compose_list` 基础装备。
3. 实现图标下载/缓存与 `cw.equipment.prepare`。
4. 实现 `vector` recognizer。
5. 用合成图和实机 profile fixture 验证静态裁切结果。
6. 用合成带边框装备格验证缩放与低置信策略。
7. 接入 `cw.equipment.read` scene 层与 renderer。
8. 补 CLI/RPC 契约测试、README 和 active skill 文档。
9. 实机截图验证阈值，必要时加入 Top-K 预筛优化。
10. 若无模型方案无法稳定区分相似图标，再实现 `model` backend。

## 风险与缓解

- 相似图标误判：使用 `gap` 阈值和 `uncertain=1`，避免硬猜。
- 边框或背景干扰识别：默认保留完整 `70x70` ROI，通过 alpha mask、背景鲁棒特征和 Top-K 精匹配降低影响；不通过继续缩小静态裁切框解决。
- 一百多个资源带来性能压力：资源特征进程内缓存，先 Top-K 预筛，再精匹配。
- 上游 config 装备字段未被现有 normalized config 暴露：新增 raw config 装备 catalog helper，不能只依赖当前 `fetch_cw_guide_config()` 的返回 shape。
- 图标 URL 下载失败或同版本换图：按 `rpg_game_big_version` 分代保存 manifest；缺失或损坏项可自动补齐，同版本 URL 变化只在显式 `cw.equipment.prepare --refresh` 时更新，下载失败时返回资源准备错误而不是输出错误识别结果。
- 网格坐标后续列累计误差：profile 使用浮点 `col_step=79.75` 递推，裁切层支持 float crop/resample 或 floor/ceil 双候选。
- 当前只确认首屏六行：超过首屏或滚动状态需要新增滚动截图校准，不能直接假设无限向下有效。
- 资源和游戏内图标存在颜色处理差异：保留灰度、颜色、边缘多种特征，后续可加入合成背景增强。
- 后续模型方案改动过大：第一版固定 `EquipmentIconRecognizer` 接口和候选结构，模型 backend 只替换识别层。
