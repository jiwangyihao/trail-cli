# 项目设计准则

## 文档边界

- `README.md` 只面向普通用户，用于说明项目是什么、如何下载、如何开始使用、Agent 自动安装入口、能力概览、致谢与许可证。
- 不要在 `README.md` 编写命令协议、输出字段、恢复链路、renderer 契约、skill 拓扑或玩法流程细节；这些内容应保留在代码、测试、active skills、registry 或专项设计文档中。
- 影响普通用户或 Agent 使用方式的变更，必须同步更新相关 active skill；只有影响普通安装、用户入口或公开定位时才更新根目录 `README.md`。
- `docs/superpowers/specs` 与 `docs/superpowers/plans` 是临时设计和执行记录，不作为长期契约来源；需要锁定行为时，测试源代码、renderer、CLI/RPC、active skills、registry 或本文件。

## 发布准则

- 预发布必须由 release workflow 生成，并确认测试、Windows 打包、smoke check 与 release assets 上传全部通过。
- 发布完成后必须手动重写 release note；不要交付 workflow 自动生成的占位说明。
- Release note 面向用户和安装者，必须说明本次变化、推荐下载资产、安装方式、校验方式和验证结果；不要堆砌内部协议字段或实现日志。

## 测试分层与运行入口

- 默认快速回归命令使用 `uv run pytest`；pytest 配置固定 `--basetemp=.pytest-tmp`，避免 Windows 用户临时目录权限或扫描问题拖慢测试。
- 慢速真实集成测试必须标记 `@pytest.mark.slow`，默认跳过；需要完整真实后端、安装器、联网下载或真实 OCR 图片质量门禁时显式运行 `uv run pytest --run-slow`。
- 推荐本地并行快速回归使用 `uv run pytest -n 8`；Windows 下若 full suite 尾部抖动明显，优先使用 `uv run pytest -n 8 --dist worksteal`。
- 并发跑多条 pytest profile 时，必须为每条命令显式设置不同 `--basetemp`，避免多个 worker 同时清理 `.pytest-tmp`。
- 新增测试应保持 tmp、session 与 fixture 隔离，避免依赖执行顺序或共享用户环境，确保可被 `pytest-xdist` 分发。

## 输出协议设计准则

- 默认文本是 Agent 主通道；结构化格式只作为明确 allowlist 的兜底，不要让 YAML 或 envelope 泄漏成默认用户界面。
- 新命令必须先归类到已有 renderer 家族，再决定首行事实、正文前缀和顺序；不要为单个命令临时发明输出形态。
- 默认首行必须稳定表达命令结果和核心事实；会影响下一步决策的 `0`、`false`、`count`、`more`、`tainted` 等事实不能因为“看起来为空”而省略。
- 默认正文统一使用既有实体前缀和 `key=value` 编码；新增前缀、冻结字段、恢复语义或 verbose 事件类型必须同步更新 renderer、契约测试和相关 active skill。
- 带截图的成功结果必须先输出截图路径，再立即提示 Agent 先读原始截图，然后再输出压缩文本事实；标题行只用于分组，不承载 must-keep 事实。
- failure 输出顺序必须稳定，确保恢复链路可被 Agent 扫读；只有结果未知或失败显式可恢复时才输出恢复动作。
- `--verbose` 只追加开发和排障层，不改变默认文本协议的事实集合、顺序或恢复语义；debug 收集失败只能丢弃 debug，不得改变默认结果。
- 输出变更至少补 renderer 单测，并按影响面补 CLI stdout 测试或 RPC/契约测试增量。

## CW 场景设计准则

- `guide.fetch.cw`、`guide.list.cw`、`cw.start`、`cw.portal.*`、`cw.slots.*`、`cw.shop.*`、`cw.equipment.*`、`cw.battle.*` 等命令的默认文本必须面向 Agent 决策，而不是暴露内部结构。
- 玩法事实应按稳定板块分组：综合信息、攻略提示、角色信息、羁绊信息、装备信息、装备优先级、角色装备需求、商店信息。板块标题只改变阅读组织，不改变事实前缀和协议语义。
- 攻略选择、投资环境、普通备战、商店、装备、战斗和未知事件各阶段必须保持职责边界；跨阶段 handoff 只能表达下一步建议，不能把下游流程细节硬塞进上游输出。
- 自动收集 facts 时，缺失、stale、低置信和可恢复失败必须清晰呈现；soft warning 不应阻断可继续执行的后续收集或 handoff。
- Agent 可见位置使用 1-based 表达；daemon、RPC、session 内部状态可以保持 0-based，但不得泄漏到默认文本或 Agent 可见文档。
- 特殊角色身份必须使用业务上可区分的稳定事实。`银狼LV.999` 与普通 `银狼` 完全无关；涉及 shop、slots、sell_plan 时必须保留 cost/star 语义，不能用普通星级等价规则代替。
- 装备识别、装备推荐和装备合成分别保持边界：背包识别写入最近快照，推荐只表达当前攻略优选装备需求，合成是实际 UI mutation，必须通过截图和结果事实证明。

## Daemon 与恢复准则

- daemon 采用单例异步续查模型：常规续查应原样重发同一业务命令；`request=<job_id>` 只用于恢复、排障和控制面。
- 会产生 UI side effect 的命令必须通过 session mutation / request journal 保护，确保已执行、已持久化、结果未知和可恢复失败能被区分。
- 一旦 side effect 已发生但结果未知，必须保存足够的 request status / reconcile 信息，并把相关 session 标记为需要恢复的状态。
- 控制面命令只负责状态查询、恢复和取消；不要把业务行为塞进控制面。

## Skill 拓扑准则

- 只有 registry 中 `status=active` 且 `exposure=public` 的 scene entry 才能作为当前对外入口出现在 active 文档与测试中。
- `trail-hsr` 是对外总入口；场景入口负责把用户意图路由到当前 active scene；内部 skill 只由上游流程或 workflow handoff 注入。
- `trail-cw-guide`、`trail-cw-portal`、`trail-cw-prep`、`trail-cw-event-unknown` 等 skill 必须保持 public/internal 边界，不要互相抢 owner 职责。
- success 输出中的 strong handoff 是推荐的下一步 skill 切换信号；它只表达阶段转换，不应成为 README 或普通用户文档里的流程教程。
- `AGENTS.md` 的 active 拓扑说明不得出现 archive skill 名称或 legacy 场景 skill 名称；任何 active skill 都不得直接或间接调用 archive skill。
