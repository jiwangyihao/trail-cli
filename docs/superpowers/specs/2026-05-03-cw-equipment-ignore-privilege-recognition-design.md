# cw.equipment.read 忽略特权装备识别设计

## 背景

当前 CW 装备配置中，36 个普通进阶装备各有一个 `·特权` 版本。普通版与特权版使用完全相同的 `icon` URL，缓存后的图标像素也完全一致。现有向量识别器会同时索引普通版与特权版，因此真实装备识别正确时，top1/top2 经常是同一装备的普通版和特权版，分数相同且 `gap=0.0`，导致 `uncertain=1`。这会阻止后续装备合成/装备命令消费本来已经识别正确的进阶装备。

## 目标行为

- 装备识别阶段不再把特权装备作为独立候选。
- 游戏中出现的特权装备按同图标普通进阶装备识别。
- 攻略和合成逻辑继续使用普通装备身份；特权装备可替代普通装备需求。
- `uncertain` 继续表示“与下一件不同装备的区分度不足”，不能被普通/特权同图标平局污染。

## 设计

- 在装备识别资源构建阶段过滤特权装备条目。
- 过滤条件为 `category_name == "特权装备"` 或装备名以 `·特权` 结尾。
- `build_precomputed_equipment_features()` 跳过特权条目，保证 daemon bundle 热路径不索引特权装备。
- `VectorEquipmentIconRecognizer.__init__()` 也跳过特权条目，保证测试和非预计算路径行为一致。
- `VectorEquipmentIconRecognizer.from_precomputed_features()` 读取旧 payload 时同样跳过特权条目，避免旧缓存或测试 payload 继续产生普通/特权平局。
- 基础装备、普通进阶装备、星徽、宝钻保持可识别。

## 数据与输出

- `cw.equipment.read` 输出普通装备名称、普通 `cache_key` 和普通 `equipment_id`。
- 如果实际截图里是特权装备，仍按同图标普通装备输出。
- 不新增默认文本字段、不新增前缀、不改变 YAML allowlist。
- `candidates` 中不再包含特权装备候选。

## 测试

- 新增识别器单测：普通版和特权版图标相同时，候选只包含普通版，`uncertain=0`，且不会因为特权平局得到 `gap=0.0`。
- 新增预计算 payload 单测：features payload 中若包含特权装备，`from_precomputed_features()` 后识别候选仍排除特权版本。
- 用真实装备截图复测 `cw.equipment.read`，确认之前的 4 个进阶装备不再因普通/特权平局产生 `uncertain=1`。
