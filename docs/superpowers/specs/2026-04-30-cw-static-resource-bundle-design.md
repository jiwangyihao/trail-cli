# CW 静态资源包与装备读取性能优化设计

## 背景

`cw.equipment.read` 当前每次执行都会读取 CW guide config、构建装备 catalog、检查或下载装备图标缓存、从磁盘加载图标，并重新构建 `VectorEquipmentIconRecognizer`。实机测量显示命令完整耗时约 4.8s 到 6.0s，其中截图和网格识别热路径约 0.5s，主要开销来自重复的配置/资源准备和 recognizer 构建。

同一批上游数据还服务于 `guide.config.cw`、角色/羁绊/投资环境/策略筛选、slots/shop/portal/strategy 的静态枚举匹配，以及装备图标识别。它们属于随 CW 赛季或游戏大版本更新的静态数据，适合在发布构建期封入安装包。

## 目标

- 发布构建期联网拉取 CW 静态配置，生成可随包发布的资源包。
- 资源包覆盖 raw config、标准化 config、补全羁绊信息、角色/羁绊/投资环境/策略 lookup/index、装备 catalog、装备图标和装备识别特征。
- 运行时默认不联网检查上游版本；热命令优先使用包内资源。
- 默认 CW 流程不调用 guide list/detail API；只有 `guide.list.cw`、`guide.fetch.cw`、显式 refresh/override 或未来明确 opt-in 网络模式可以访问动态攻略接口。
- daemon 缓存已加载资源包和装备 recognizer，避免每次命令重复解码图标和构建特征。
- `cw.equipment.read` 热路径保留截图、格子过滤、图标识别、session 写入和默认输出协议，避免额外截图。
- 保持现有 renderer、YAML allowlist、session 快照 shape 和 Agent 可见文本契约不变。

## 非目标

- 不把 guide list 或攻略摘要预拉入资源包；攻略列表和攻略详情仍是动态内容。
- 不在每次 `cw.equipment.read`、`guide.config.cw` 或 slots/shop/portal/strategy 命令中自动联网刷新配置。
- 不引入 OCR 装备识别；装备识别继续使用图标特征匹配。
- 不改变 `cw.equipment.read` 的默认文本字段、顺序、YAML 支持或 `cw_state.equipment` 持久化语义。
- 不新增运行时依赖；构建脚本第一版使用 stdlib 和项目现有依赖。如果后续必须新增依赖，需同步 `pyproject.toml` 与第三方 notices 生成/校验。

## 构建期资源包

新增构建期生成步骤，建议命名为 `scripts/build-cw-resource-bundle.py`，由 `scripts/build-windows.ps1` 在 `generate-third-party-notices` 之后、`python -m build` 和 PyInstaller 前执行。该步骤是发布构建的硬前置：联网、数据校验、图标下载或特征生成失败时直接失败，避免产出缺资源包的安装包。

资源包建议写入包内数据目录，例如 `trail/scenes/cw/generated/<big_version>/`：

- `manifest.json`：记录 `season_id`、`sub_season_id`、`rpg_game_big_version`、`rpg_game_lineup_tourn_filter`、生成时间、`bundle_schema_version`、`generator_schema_version`、`resource_version`、每个文件的 hash/size 和文件列表。
- `raw_config.json`：上游 `CW_GUIDE_CONFIG_API` 原始 `data`。
- `guide_config.json`：`fetch_cw_guide_config(enrich_traits=False)` 的标准化结果。
- `guide_config_enriched.json`：包含补全羁绊层数的标准化结果。构建期必须调用或等价复用 `guide.py` 中现有 enrichment 行为，包括分页、重复 token 终止、缺失 trait id 补拉、name-only 合并和失败回退语义，除非同步迁移对应测试。
- `indexes.json`：角色、羁绊、投资环境、策略和装备 catalog 的常用 lookup/index。第一版至少包含 trait name/id lookup、role name/id lookup、portal title/id lookup、strategy title/id lookup、role fuzzy 前置候选数据和 equipment cache_key/name/id lookup；派生规则必须与现有 normalize 与匹配行为保持一致。
- `equipment/manifest.json`：装备 catalog 与图标元数据。`items[]` 必须保留 `cache_key`、`id`、`name`、`kind`、`category`、`category_name`、`icon_url`、`big_version`、`local_path` 和校验信息，保证现有 session/YAML candidates 与 refresh 诊断字段可恢复。
- `equipment/icons/*.png`：经验证并统一保存的装备图标。下载和写入必须复用现有 `equipment_resources.py` 的安全边界，或保持相同的 HTTPS host allowlist、timeout、大小限制、PIL verify、RGBA 转 PNG 与原子写入语义。
- `equipment/features.json` 或等价二进制文件：装备识别所需的预计算信息。schema 必须能恢复当前 recognizer 所需的 `feature_rgba`、`match_rgba`、`feature_mask`、`match_mask` 或等价数据，并记录 `FEATURE_SIZE`、`MATCH_SIZE`、`min_score`、`min_gap`、`equipment_feature_schema_version` 和 `recognizer_algorithm_version`。

PyInstaller 当前通过 `collect_data_files("trail")` 收集包数据；wheel 构建需要确保 generated 资源进入包数据。如果现有 hatchling 规则不能包含新增 JSON/PNG/特征文件，需要补充明确的 wheel artifact 包含规则。发布构建还要校验 wheel、sdist 和 PyInstaller dist 都包含根 manifest、config JSON、装备 manifest、图标和 features，缺任一关键文件即失败。

## 运行时加载

新增一个 CW 静态资源 resolver，按模式加载资源：

1. release default：只读包内 generated 资源包，缺失或损坏直接失败，不自动联网。
2. explicit override：当用户显式启用或已有已验证 workspace override 时，workspace override 优先于包内资源；override 必须使用与包内资源相同的 manifest/schema。
3. dev fallback：仅源码开发环境或测试注入允许网络/cache 回退，且不能成为 release default 的隐式兜底。

release 默认路径只读包内资源，不主动联网检查上游是否有新大版本。若包内资源缺失或 manifest 损坏，返回明确错误，例如 `CW_RESOURCE_BUNDLE_MISSING` 或 `CW_RESOURCE_BUNDLE_INVALID`。如果 manifest 与显式请求或 workspace override 不一致，返回明确错误并提示升级包或执行显式刷新命令。

`fetch_cw_raw_guide_config` 改为优先返回资源包 `raw_config.json`。`fetch_cw_guide_config(enrich_traits=False)` 返回 `guide_config.json`，`enrich_traits=True` 返回 `guide_config_enriched.json`，不再在默认路径循环调用 guide list 补全羁绊。角色、羁绊、投资环境、策略解析第一版必须有 bundle-first/index-first 入口；外部返回 shape 保持不变。`cw.start`、`cw.portal.detect|refresh|restart|select` 等默认流程不得为了附加攻略摘要调用 guide list/detail API；如需攻略摘要，只能使用当前已选攻略、已有缓存或未来显式 opt-in 网络模式。

## 装备识别热路径

`VectorEquipmentIconRecognizer` 增加从预计算特征构建的入口，避免运行时反复从 PNG 生成 feature 和 mask。`from_precomputed_features` 必须与现有 PNG 构造器在同一输入下产生相同 candidates 顺序、score、gap、uncertain 和 empty 语义，并通过 golden parity tests 约束。

`cw.equipment.read` 不再调用 `prepare_equipment_icon_cache` 或 `load_cached_equipment_icons` 作为默认路径，也不得通过 resolver 间接触发 `Image.open(equipment/icons/*.png)`、`_normalized_rgba(icon, ...)` 或其他图标 feature 构建。默认调用链只从 daemon resource cache 获取已由 `features.json` 构建好的 recognizer。

daemon 增加独立 `CwResourceService`，或在 `CwService` 内实现同等边界清晰的不可变 resource 子服务，缓存以下对象：

- 当前 workspace 的 CW resource bundle。
- 当前大版本的 equipment catalog。
- 当前大版本的 equipment recognizer。

缓存 key 至少包含 normalized workspace root、resource source kind、manifest path 或 content digest、manifest schema version、resource version、big_version，以及 workspace override manifest mtime/content digest。缓存值不得持有 `SessionModel` 或 runtime，避免 session 间污染。显式 refresh/prepare 或 override identity 变化后应失效对应 key。这样同一 daemon 生命周期内重复执行 `cw.equipment.read` 时，只需要截图、裁剪格子、过滤 slot marker、调用 recognizer、写入 session。

## 截图复用

`cw.equipment.read` 当前为了识别调用 `runtime.capture_image(normalize=True)`，随后 `with_selective_capture` 又调用 `capture_after_action` 产出 `shot path=...`。设计上必须允许该命令把识别用的规范截图保存为 request screenshot，并让 envelope 使用同一路径，避免二次截图。

实现可以采用命令专用返回元数据或 request-scoped runtime hook，但 `with_selective_capture` 必须优先消费该 screenshot path 并跳过 `_capture_screenshot()` / `capture_after_action`。runtime 需要新增 `save_capture_image_to_workspace(image, request_id)` 或等价 API，把识别用的 1920x1080 规范图按 request id 保存并返回路径，同时保留 warnings、references 和 verbose debug 收集逻辑。

默认输出顺序不得改变：success 仍为首行、`shot path=...`、`info read_image_first=1`、实体行、warn/ref。envelope 顶层仍由 screenshot 生成 `image_guidance.read_image_first=1`。失败路径仍按现有 output 协议。

`cw.equipment.read` 文本和结构化契约保持冻结：首行 `ok cw.equipment.read count=<n> uncertain=<n> empty=<n>`；默认 `item` 字段为 `pos/center/name/score/uncertain`，仅 `uncertain=1` 时输出 `gap/alt/alt_score`；默认文本不输出 `idx/row/col/box`；低置信仍输出 `warn code=LOW_CONFIDENCE count=...`；`--format yaml` 和 `state.dump` 继续保留结构化 `row/col/candidates/gap/alt/alt_score`；成功继续写入 `cw_state.equipment` 最近快照。

## 刷新与过期策略

默认命令不联网。资源更新依赖发布新包。

`cw.equipment.prepare --refresh` 保留为显式装备刷新入口，但第一版不拉取 guide config/list、不替换包内 config/index。它只基于已解析的 bundle catalog 刷新或修复 workspace 装备图标/特征缓存，并失效 daemon equipment recognizer cache。若后续需要让完整 config bundle 也支持显式刷新，应另设明确命令或模式，并使用同一 manifest/schema 写 workspace override。

当用户运行的包内资源落后于游戏大版本时，默认命令可能仍使用包内旧配置。由于默认不联网，这不是每次命令可自动发现的问题；需要通过发布新包或显式刷新流程解决。错误提示应在缺资源、manifest 损坏、显式 refresh 失败等可检测场景中保持清晰。

## 测试计划

- 构建脚本单测：模拟 config、guide list 羁绊补全、图标下载和特征生成，验证 manifest 与文件输出。
- 构建产物校验：wheel、sdist、PyInstaller dist 都包含 generated manifest、config JSON、装备 manifest、图标和 features。
- 资源 resolver 单测：release default 包内资源、显式 workspace override 优先、开发回退、manifest 缺失/损坏、旧 schema 拒绝、override shape 与包内 shape 一致。
- 默认不联网回归测试：monkeypatch `_fetch_cw_config_data`、guide list enrichment、装备图标下载、cache prepare 直接失败，验证 `guide.config.cw`、静态枚举解析路径和 `cw.equipment.read` 仍从 bundle 返回。
- guide config 单测：默认从 bundle 返回 raw/normalized/enriched config，`enrich_traits=True` 不再触发 guide list 循环；`guide.config.cw --format yaml` 结构兜底不变。
- index 单测：角色、羁绊、投资环境、策略 lookup 与现有解析结果保持一致。
- equipment 单测：recognizer 可从预计算 feature 构建；与 PNG 构造器有 golden parity tests；默认 read 不调用 prepare/download/load icons；输出数据 shape 不变。
- daemon 单测：同一大版本重复 `cw.equipment.read` 只构建一次 resource bundle 和 recognizer；refresh/prepare 后缓存失效。
- capture 单测：`cw.equipment.read` 复用识别截图作为 envelope screenshot，避免二次 `capture_after_action`。
- CLI/RPC/YAML 契约测试：`guide.config.cw` bundle 读取和 YAML、`cw.equipment.prepare` summary/zero/YAML unsupported、`cw.equipment.read --format yaml` shape、`state.dump` 中的 `cw_state.equipment`、renderer allowed prefixes 与截图顺序保持不变。
- 文档同步测试：若 Agent 可见行为变化，至少同步 active CW skill 中的 `cw.equipment.prepare/read` 说明和项目输出协议；第一版不更新根 `README.md`，除非新增普通安装或公开入口说明。
- 可观测性测试：如果新增 verbose trace，必须统一经过现有 debug collect/render 管线，并验证第二次及后续 daemon 调用出现 resource/recognizer cache hit，且没有 prepare/load/build trace。

## 验收标准

- daemon 热状态下重复执行 `cw.equipment.read`，资源准备与 recognizer 构建不再出现在每次调用路径。
- 实机 daemon warm path p50 应接近现有截图和识别热路径；截图复用落地后，daemon `cw.equipment.read` p50 目标低于 1s，p95 可单独记录宽限。完整 CLI 耗时受进程/transport/renderer 影响，单独记录，不作为硬性低于 1s 承诺。
- `guide.config.cw` 和需要静态枚举的 CW 命令默认不因缺少 workspace cache 而联网拉 config。
- 发布构建缺少 CW 静态资源包时失败，不发布半成品。

## 实施顺序建议

1. 定义 bundle schema、manifest 校验和测试 fixture。
2. 实现构建期 generator，并接入 wheel、sdist、PyInstaller 包含校验。
3. 实现 CW resource resolver 和 release/default/dev/override 模式。
4. 切换 `fetch_cw_raw_guide_config`、`fetch_cw_guide_config` 和静态枚举 lookup 到 bundle-first/index-first。
5. 增加装备 feature bundle 和 `VectorEquipmentIconRecognizer.from_precomputed_features` parity tests。
6. 接入 daemon `CwResourceService` 缓存和 `cw.equipment.read` 默认热路径。
7. 实现截图复用，避免 `with_selective_capture` 二次截图。
8. 补齐 renderer、CLI/RPC/YAML/session、默认不联网、release 构建和 skill 文档测试。
