# 货币战争阶段截图参考

这个目录用于存放**真实游戏截图**，给后续 Agent / 开发智能体对照页面语义时使用。

当前约定：
- 每张图都用顺序号命名，便于后续持续追加。
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
  - `剩余次数` 指的是当前投资环境页还能执行几次 `cw portal.refresh`
  - 当 `剩余次数` 归零后，继续运行 `cw portal.refresh` 不会再改变卡片内容，应优先考虑 `cw portal.select` 或 `cw portal.restart`
  - `cw portal.restart` 不是默认下一步；它更适合在**用户已经选定攻略、并且明确表示接受刷开局**时使用
  - 这类刷开局流程的推荐顺序是：
    1. `trail cw start ...` 进入投资环境页
    2. 观察当前三张卡是否包含目标攻略需要的投资环境
    3. 若未命中，则在当前页优先尝试 `trail cw portal.refresh --session <id>`
    4. 刷新次数耗尽仍未命中时，再执行 `trail cw portal.restart --session <id>` 回到新一轮投资环境页
    5. 之后继续按 `refresh -> restart` 的顺序循环，直到出现目标投资环境为止

## 04-world-chaoluguan.jpg

- 文件：`docs/cw-stage-reference/04-world-chaoluguan.jpg`
- 阶段名称：`大世界探索态（示例图：朝露公馆）`
- 关键视觉特征：
  - 左上角是**大世界小地图**，这是最强的稳定特征之一
  - 右上角有一排**工具栏图标**，在普通大世界里这一排通常最多
  - 右侧有**角色栏**
  - 左下角有**好友/私聊入口**
  - 右下角有完整的**操作区**，例如秘技（`E`）、普攻（左键）、疾跑（右键）
- 对应命令语义：
  - 这是 `trail cw enter --session <id>` 的正常起点之一
  - 如果当前 live 画面接近这张图，说明还在大世界，还没有进入货币战争首页
- 到达该页面后的推荐下一步：
  1. 若目标是进入货币战争链路，运行：
     - `trail cw enter --session <id>`
  2. 若只是记录当前界面或做一般 OCR/截图诊断，可运行：
     - `trail ocr read`
     - `trail screen shot`
- 额外说明：
  - **不要**把左上角的地点名（例如 `朝露公馆`）当作“大世界”的核心判据，因为地点会变化
  - **不要**把某个恰好出现在附近的功能入口（例如这张图里右侧能看到 `货币战争`）当作核心判据，因为玩家不一定停在对应入口附近
  - 真正稳定的判断，应优先依赖：左上小地图、右上工具栏、右侧角色栏、左下私聊入口、右下操作区这五类 UI 结构
  - 还要注意和玩法内探索环节区分：差分宇宙等模式也可能有小地图/角色栏/操作区，但它们的右上工具栏通常更少，且左上的手机入口常会被退出按钮替代

## 05-cw-boss-preview-page.jpg

- 文件：`docs/cw-stage-reference/05-cw-boss-preview-page.jpg`
- 阶段名称：`本场对局首领页（boss preview）`
- 关键视觉特征：
  - 右侧大标题是 `本场对局首领`
  - 页面中部是三张阵营卡，例如当前这张图里是：
    - `虫人兵器`
    - `火线动力机甲`
    - `灰手生命科技`
  - 每张卡上方都会出现 `阵营`
  - 右下主按钮是 `下一步`
- 对应命令语义：
  - 这是 `trail cw start --session <id> --mode ...` 在进入投资环境页之前可能经过的中间态之一
  - 正常情况下它不该作为停留决策点，而应由 `cw start` 自己继续推进
- 到达该页面后的推荐下一步：
  1. 如果是手动观测阶段，不要在这里跑 `cw portal.select|refresh|restart`
  2. 正常命令链里，应继续让：
     - `trail cw start --session <id> --mode ...`
     去点 `下一步` 并推进到真正的投资环境页
- 额外说明：
  - **这页不是投资环境页**
  - 这里显示的是本场对局首领/阵营信息，还没有出现真正的投资环境卡片与 `剩余次数`

## 06-cw-preparation-stage.jpg

- 文件：`docs/cw-stage-reference/06-cw-preparation-stage.jpg`
- 阶段名称：`备战阶段（局内准备页）`
- 关键视觉特征：
  - 顶部中央明确显示 `备战阶段` 和当前小节，例如 `1-1`
  - 中间是前台/后台区域的空槽位或已摆放槽位
  - 底部是一排手牌与 `购买经验`
  - 右下主按钮是 `出战`，右下角还会出现 `商店`
  - 右侧靠中位置会出现可领取的晶球/奖励提示，这是判断当前局内准备页的重要信号之一
- 对应命令语义：
  - 这通常是 `trail cw portal.select --session <id> --card-idx <n>` 成功后的结果页
  - 也是局内命令开始接管的阶段，例如：
    - `trail cw crystals collect --session <id>`
    - `trail cw slots ...`
    - `trail cw hand ...`
    - `trail cw shop ...`
- 到达该页面后的推荐下一步：
  1. 如果右侧有可领的晶球/奖励，优先运行：
     - `trail cw crystals collect --session <id>`
  2. 若要布阵、卖牌、看商店，再按局内流程运行对应 `cw slots` / `cw hand` / `cw shop` 命令
  3. 准备完成后再运行：
     - `trail cw battle start --session <id>`
- 额外说明：
  - 这里已经进入局内，出现了 `备战阶段`、`出战`、`商店` 和手牌栏

## 07-cw-shop-open-page.jpg

- 文件：`docs/cw-stage-reference/07-cw-shop-open-page.jpg`
- 阶段名称：`备战阶段 + 商店展开页`
- 关键视觉特征：
  - 顶部仍是 `备战阶段`，例如这张图里是 `1-3`
  - 上方会弹出一整排商店角色卡
  - 右侧有刷新按钮和当前金币/刷新次数
  - 中间仍能看到前台/后台槽位与已上阵角色
  - 右下主按钮仍是 `出战`
- 对应命令语义：
  - 这是局内准备页中，`trail cw shop open --session <id>` 之后的典型结果页之一
  - 也可能在商店已展开的情况下，继续接 `trail cw shop scan` / `trail cw shop buy-slot` / `trail cw shop refresh`
- 到达该页面后的推荐下一步：
  1. 若要读取当前商店内容，运行：
     - `trail cw shop scan --session <id>`
  2. 若要直接买牌，运行：
     - `trail cw shop buy-slot --session <id> --slot <n> --expect <角色名>`
  3. 若要收起商店回到普通备战页，运行：
     - `trail cw shop close --session <id>`
- 额外说明：
  - 这页仍属于局内 `备战阶段`，不是投资环境页，也不是结算页
  - 商店已经在顶部展开，且右侧出现了刷新按钮与相关货币信息

## 08-cw-round-settle-success.jpg

- 文件：`docs/cw-stage-reference/08-cw-round-settle-success.jpg`
- 阶段名称：`局内单局结算页（挑战成功 / 继续挑战）`
- 关键视觉特征：
  - 大标题是 `挑战成功`
  - 会显示当前小局关卡号，例如这张图里是 `1-4`
  - 底部主按钮是 `继续挑战`
  - 中间区域展示本小局的收益与数据统计
- 对应命令语义：
  - 这是局内战斗结束后的单局结算页，不是整局结算链
  - 当前命令链里，它通常是 `trail cw stage wait --session <id>` 之后可能停下来的页面之一
- 到达该页面后的推荐下一步：
  1. 若要继续当前对局的下一小节，运行：
     - `trail cw settle next --session <id>`
  2. 若只是做阶段确认，可先记录截图/OCR，再继续后续局内链路
- 额外说明：
  - 这页和整局结算链的区别在于：这里的主按钮是 `继续挑战`，而不是 `下一步 / 下一页 / 返回货币战争`

## 09-cw-preparation-full-warning.jpg

- 文件：`docs/cw-stage-reference/09-cw-preparation-full-warning.jpg`
- 阶段名称：`备战阶段满员提示页`
- 关键视觉特征：
  - 中间会出现红条：`备战席已满，请出售角色或提升等级`
  - 背景仍然是正常的局内 `备战阶段` 布局
  - 右下仍有 `出战` 和 `商店`
- 对应命令语义：
  - 这更像局内准备态上的**限制提示状态**，不是独立的大页面跳转
  - 通常出现在继续上阵、拖牌或摆放角色时超过当前可用席位
- 到达该页面后的推荐下一步：
  1. 先不要继续上阵更多角色
  2. 优先考虑：
     - `trail cw hand sell-one|sell-plan --session <id>`
     - `trail cw slots swap --session <id> --source ... --target ...`
     - 或先提升等级 / 扩容
- 额外说明：
  - 这页本质上仍属于局内 `备战阶段`，只是带了一个明确的容量限制警告

## 10-cw-mode-select-clean.jpg

- 文件：`docs/cw-stage-reference/10-cw-mode-select-clean.jpg`
- 阶段名称：`开始货币战争后的模式选择页（干净版）`
- 关键视觉特征：
  - 页面左侧是三张模式/入口卡
  - 页面上**没有** `继续进度 / 结束并结算`
  - 底部有继续按钮
- 对应命令语义：
  - 这属于 `trail cw start --session <id> --mode new --difficulty ... --battle-mode ...` 的中间态之一
  - 正常命令链里不应在这里停给 Agent 决策，而应继续推进到投资环境页
- 到达该页面后的推荐下一步：
  1. 若是手动观测阶段，不要在这里跑 `cw portal.select|refresh|restart`
  2. 正常命令链里，应继续让：
     - `trail cw start --session <id> --mode new --difficulty ... --battle-mode ...`
     去完成模式选择并进入投资环境页
- 额外说明：
  - 这页和 `02-cw-mode-select-with-progress.jpg` 同属“点了开始之后的模式选择页”，但这一张是**没有未收尾进度**的干净版本
