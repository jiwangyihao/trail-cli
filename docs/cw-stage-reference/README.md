# 货币战争阶段截图参考

这个目录用于存放**真实游戏截图**，给后续 Agent / 开发智能体对照页面语义时使用。

当前约定：
- 每张图都用顺序号命名，便于后续持续追加。
- 所有纳入仓库的截图都要先做隐私脱敏；当前图片已把左下角 UID 区域遮盖。
- 推荐统一使用脚本：`python docs/cw-stage-reference/sanitize_stage_reference.py <input> --box 27,1050,123,25 --output <output>`
- 说明里要明确：
  - 这张图对应的页面/阶段
  - 识别这个阶段时最可靠的视觉特征
  - 它通常是哪个命令的结果
  - 到了这个页面后下一步通常该跑什么命令

## 01-cw-homepage-clean.jpg

- 文件：`docs/cw-stage-reference/01-cw-homepage-clean.jpg`
- 阶段名称：`货币战争首页（干净首页）`
- 关键视觉特征：
  - 左上角有 `货币战争 / 零和博弈`
  - 左侧有 `创业指南 / 优势布局 / 攻略大全`
  - 右下角主按钮为 `开始「货币战争」`
  - **没有** `继续进度` / `结束并结算`
- 对应命令语义：
  - 这是新语义下 `trail cw enter --session <id>` 的目标页面
  - 如果 `trail cw enter` 成功并返回 `ok cw.enter page=home`，理想情况下页面应接近这张图
- 到达该页面后的推荐下一步：
  1. Agent 先询问用户：
     - `攻略优先` 还是 `环境优先`
     - `standard` 还是 `overclock`
     - 是否接受刷开局
  2. 再运行：
     - `trail cw start --session <id> --mode new --difficulty current --battle-mode standard`
     - 或按用户选择替换 `mode / difficulty / battle-mode`
- 额外说明：
  - 如果页面上出现 `继续进度` / `结束并结算`，那就**不是**这张“干净首页”参考图，而是“点了开始之后的模式选择页（且带未收尾进度）”，后续应先问用户，不应直接执行 `cw start`

## 02-cw-mode-select-with-progress.jpg

- 文件：`docs/cw-stage-reference/02-cw-mode-select-with-progress.jpg`
- 阶段名称：`开始货币战争后的模式选择页（带未收尾进度的变体页）`
- 关键视觉特征：
  - 左上仍然是 `货币战争 / 零和博弈`
  - 页面左侧出现两张模式卡：`标准博弈` / `超频博弈`
  - 右下同时出现：
    - `结束并结算`
    - `继续进度`
  - 旁边还有 `当前进度 1-1` / `奖励` 之类的未收尾进度信息
- 对应命令语义：
  - 这页不是 `cw enter` 的目标首页
  - 它更接近：从真正首页点了一次 `开始「货币战争」` 之后，进入的“模式选择页”
  - 在当前新语义下，如果这页同时带未收尾进度，`trail cw start --mode new|continue ...` 都应该先稳定报错，而不是自动继续
- 到达该页面后的推荐下一步：
  1. Agent 必须先问用户：
     - 是要 `继续进度`
     - 还是 `结束并结算`
     - 还是稍后再开新局
  2. 在用户没明确前，不应自动继续执行 `cw start`
- 额外说明：
  - 这页之所以要单独存档，是因为它很容易被误认成普通首页或 `entry.continue` 中间页
  - 当前 feature 的关键语义之一就是：在这页上 `cw start` 要稳定报 `CW_START_PROGRESS_PENDING`，把决策权交给 Agent 去问用户

## 03-cw-invest-portal-page.jpg

- 文件：`docs/cw-stage-reference/03-cw-invest-portal-page.jpg`
- 阶段名称：`投资环境选择页`
- 关键视觉特征：
  - 左上角是 `图例 / 投资环境 / 攻略`
  - 中间横向摆放三张投资环境卡，例如当前这张图里是：
    - `劳务派遣合同`
    - `联席决策`
    - `银河学者概念股`
  - 每张卡下方有简短描述文案
  - 底部中间主按钮是 `确认`
  - 左下能看到 `剩余次数：1`
- 对应命令语义：
  - 这是 `trail cw start --session <id> --mode new --difficulty ... --battle-mode ...` 的目标页面
  - 也是 `trail cw portal.refresh --session <id>` 和 `trail cw portal.restart --session <id>` 成功后应回到的页面
- 到达该页面后的推荐下一步：
  1. 如果要接受当前三张卡之一，运行：
     - `trail cw portal.select --session <id> --card-idx <1|2|3>`
  2. 如果想刷新重看三张卡，运行：
     - `trail cw portal.refresh --session <id>`
  3. 如果要按当前环境反查攻略，运行：
     - `trail guide list cw --portal <portal_title> --limit 3`
- 额外说明：
  - 这页和首页最重要的区别是：已经没有 `开始「货币战争」`，而是明确出现三张投资环境卡和底部 `确认`
  - 这张图已按左下 UID OCR 框做马赛克脱敏
