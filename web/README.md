# web — 前端

原生 TypeScript + Canvas 2D，无框架、无运行时依赖。构建工具 Vite。
样式与配色以 `SaveWeb/屏幕截图 *.png` 为视觉基准。

## 目录

```
index.html            对局页入口
waiting.html          房间列表页入口
replay.html           回放页入口（?id=<id> 播放一局；不带 id 为最近对局列表）
src/core/             纯逻辑（无 DOM、无 canvas，可单测）
  types.ts            线协议与游戏对象的类型定义
  geometry.ts         索引 ↔ (row, col)、4/8 邻、方向
  state.ts            ClientState + applyFrame（不可变帧应用）
  replay.ts           回放文件解析 + ReplayCursor（按 Tick 重放/跳转）
  chains.ts           本地调兵链镜像（选择、比例、push/pop/clear）
  client.ts           WebSocket 连接（自动重连、心跳、发送队列）
src/render/            Canvas 绘制
  palette.ts          实测配色（玩家色 / 地块色 / UI 色）
  camera.ts           正投影摄像机：平移（不钳制）、以光标为锚点的缩放、命中测试、可见范围
  icons.ts            山峰 / 城堡 / 王冠 / 骷髅（矢量路径，缩放无关）
  painter.ts          paint(ctx, cam, state, chain, opts) 的唯一绘制入口
src/ui/               DOM 面板（全部是压在 canvas 上的浮动层）
  turn.ts             Turn N / Turn N. 计数器
  leaderboard.ts      ★ | Player | Army | Land（可折叠成「色块 + Army + Land」）
  chat.ts             聊天与系统消息（顶部一条窄拖动条，可收起成「聊天 ▸」小按钮）
  floating.ts         makeDraggable：面板拖动 + localStorage 位置记忆
src/replay/           回放页（复用 render/ui，不重复实现绘制）
  main.ts             回放页装配点：载入 → 游标重放 → 控制条/面板同步
  controls.ts         播放/暂停、0.5×–16× 倍速、时间轴（按 Tick）、Tick/时长文本
  ailog.ts            AI 调试流面板（按玩家/kind 筛、点条目跳到该刻）
  chatlist.ts         只读聊天（可回溯，按内容指纹整体重建）
src/input/            输入
  keyboard.ts         WASD / Q / E / ± / 方向键 / Enter
  pointer.ts          点击选中、点击调兵、拖拽平移、滚轮缩放（把光标坐标传给上层当锚点）
src/main.ts           唯一装配点（把上面四层接起来）
styles/               palette.css（CSS 变量）+ game.css + waiting.css + replay.css
public/fonts/         Quicksand 500/700（与原站一致的几何无衬线数字）
scripts/shot.mjs      playwright-core + 本机 Edge 截图
scripts/e2e-page.mjs  真浏览器 × 真后端联调（两个页面开局 + 断言 + 拖动记忆 + 交互细节 + 截图）
scripts/e2e-replay.mjs 回放页联调（列表、播放、跳转、AI 流筛选与跳条、倍速、键盘单步 + 截图）
```

## 布局：全屏地图 + 五个浮动面板

地图 canvas 铺满整个视口（`position: fixed; inset: 0`），没有固定的侧边栏；
回合 / Surrender / 全屏 / 排行榜 / 聊天都是浮在 canvas 之上的独立 DOM 层。

| 面板 | 元素 | 默认位置 | 样式要点 |
| --- | --- | --- | --- |
| 回合计数（R1.2） | `#turn-counter` | 左上 `12,12` | 深色半透明底 + 白色 20px 粗体，文本 `Turn N` / `Turn N.` |
| 投降 | `#surrender-button` | 顶部居中 | 白底青字（沿用原站样式） |
| 全屏 | `#fullscreen-button` | 顶部居中 Surrender 右侧 | 白底青字，文案 `全屏` / `退出全屏` |
| 排行榜（R6.8） | `#game-leaderboard-container` | 右上 `12,12` | 宽 300 px，表格外观不变；右下角外侧挂折叠按钮 |
| 聊天（R6.10） | `#in-game-chat` | 右下 `12,12` | 宽 340 px、高 38vh，黑底灰字 |

**可拖动 + 记忆位置**（`src/ui/floating.ts`）：

- Pointer Events；移动阈值 4 px —— 阈值内视为点击，所以 **Surrender / 全屏仍是能点的按钮**，
  拖动结束后随之而来的那次 `click` 会被 `200ms` 的守卫吞掉，不会误触。
- 聊天面板用顶部 18 px 的 `.chat-drag-handle` 作握把，不与消息滚动、输入框抢事件；
  其余四块整块可拖。握把里的小按钮（`—` / `聊天 ▸`）自己决定是否让 `pointerdown` 冒泡：
  展开按钮必须冒泡，否则收起后的面板（只剩那颗按钮）就拖不动了。
- 拖动时把位置固化成 px 的 `left/top`（`right/bottom` 置 `auto`、清掉 `transform`），
  并 `z-index` 递增把该面板提到最前；位置写
  `localStorage['generals.panel.<元素 id>'] = {"left":…,"top":…}`。
- 初始化与 `window.resize` 时把位置夹回视口，至少保留 40 px 可见。
- 面板是独立 DOM 层，`pointerdown` 落在面板上不会到达 canvas，因此点面板不会选中地块（R6.1）。

**非地图区**用比页面底色更深的灰 `--bg-void: #141414`（页面、canvas 底、地图外侧都是它）；
棋盘内部仍是迷雾色 `#393939`，两者不要混用。

## 渲染顺序

`painter.paint()` 固定为：

1. 画布底 `--bg-void` `#141414`，再在地图范围内铺迷雾色 `#393939`（地图之外露出的就是更深的底）
2. 逐格填充——**视野内**先整格铺 `#000000` 再内缩填充（缝隙即纯黑网格线）；
   **视野外**整格铺 `#393939`，相邻格无缝，迷雾是一片连续底色（R6.9 只折叠"外观"，不折叠底色）
3. 地形图标：视野外仅折叠成山地的格子画山形；视野内画山峰 / 城堡 / 王冠（山形一律纯黑、线宽约格子的 2%）
4. 兵量数字（覆盖在图标之上；兵量为 0 不画）
5. 调兵链箭头
6. 选中地块白框
7. 选中格的**比例文字**（R6.6）：`chain.ratio < 1` 时在格子底部居中画 `NN%`，
   字号约格子的 21%，半透明白 + 深色柔化阴影；100% 不画（保持原站观感）。
   只画文字、**不加遮罩**，也不盖住中央的兵量数字。

**关键细节**：当某格要叠兵量数字时，图标改用半透明深色（`TILE.iconStrokeDimmed`）退成背景纹理，
否则白图标 + 白数字会糊在一起看不清。原站城市的深色线稿 + 白色数字就是这个道理。

## 调兵比例（R6.6）

- 选中新地块 → 100%；再次点击当前选中地块 → 50%；`+`/`-` 步长 10%（10%–100%）。
- **比例不自动保持**：每提交一次调兵（键盘 `WASD` 或点击调兵，两者都走 `core/chains.ts`
  的 `pushMove`）后立刻回到 100%。已入队的请求各自记着入队时的比例（R6.7）。

## 摄像机

`camera.ts` 是正投影：默认 `zoom = 1` 表示**整张地图正好铺满视口短边**（与原站存档页
`#gameMap { width/height: size * --cell-size }` 一致），滚轮在 `ZOOM_LEVELS` 之间切换，
拖拽 / 方向键平移，整个视口都能拖、能缩（面板区域除外，事件被面板自己吃掉）。

坐标换算只有两条公式，别在别处重复实现：

```
屏幕 x = cam.width  / 2 + (col - cam.cx) * cell
屏幕 y = cam.height / 2 + (row - cam.cy) * cell
```

地图左上角（= 地块 0 的左上角）因此是 `width/2 - cx*cell, height/2 - cy*cell`，
**不要**用「(视口 - 地图)/2」那种居中假设 —— 平移后地图并不居中。

### 缩放以光标为锚点（`zoomAt`）

`zoomAt(cam, direction, screenX, screenY)`：换档前后**光标下的那个地图点保持不动**。
做法是把光标换算成世界坐标，换档得到新 `cell'`，再反解出让该世界坐标仍落在同一屏幕
位置所需的中心：

```
col = (sx - width/2) / cell  + cx      →  cx' = col - (sx - width/2) / cell'
row = (sy - height/2) / cell + cy      →  cy' = row - (sy - height/2) / cell'
```

`pointer.ts` 的 wheel 回调把光标坐标（相对画布左上角）一起交给上层 `onZoom(dir, x, y)`，
`main.ts` 用它调 `zoomAt`。`zoomStep(cam, dir)` 仍保留导出（= 以视口中心为锚点），
但主流程不再使用。

### 不钳制地图位置

`panPixels` / `panTiles` / `centerOnTile` / `zoomAt` **都不再把中心夹在地图内**，
地图可以被拖到视口外任意位置（`clampCamera` 已删除）。只保留最小/最大缩放档位
（`ZOOM_LEVELS` 不变）；开局时把镜头对到自己的首都（`centerOnTile`），不另做居中。

中心远离地图时 `visibleRange()` 会给出空范围（`row0 > row1` / `col0 > col1`），
`painter.paint()` 的 `for (row = row0; row <= row1; …)` 自然一次都不执行；
地图完全移出视口后画布上只剩 `--bg-void` `#141414`（迷雾底色 `#393939` 随地图一起移走）。

> 副作用（预期）：开局把镜头**精确**对到自己的首都，而首都常常靠地图边角，
> 于是视口里会露出一块 `#141414` 非地图区（不再是「整图自动居中、全图可见」）。
> 拖一下或缩一档就能把全图看全。

## 全屏

`#fullscreen-button`（白底青字，与 Surrender 同一套观感）默认在顶部居中 Surrender 右侧，
和别的面板一样可拖、位置记在 `localStorage['generals.panel.fullscreen-button']`。

- 点它：`document.fullscreenElement ? exitFullscreen() : documentElement.requestFullscreen()`
  （Promise 拒绝——用户/策略拒绝——静默忽略）。
- 文案随状态切换：`全屏` / `退出全屏`，并监听 `document.fullscreenchange` 同步；
  进入/退出全屏会改变视口尺寸，所以在那里（和既有的 `window.resize`）都会调 `resize()`。
- 浏览器没有 Fullscreen API（`!document.documentElement.requestFullscreen`）时按钮
  `disabled`，`title` 说明原因。

## 记分板折叠 / 聊天收起（都不记忆，刷新复位）

两者都只是**本地 UI 状态，不发网络帧、不写 localStorage**。

- **记分板折叠**：面板右下角外侧的 `#leaderboard-toggle`（`▾` / `▸`，title 写「折叠/展开记分板」）。
  折叠态**没有表头、没有玩家名、没有星数**，每行只剩「玩家色块 + Army + Land」三个单元格
  （色块沿用 `.leaderboard-name.<color>` 的背景色，已出局的数字格仍标灰 `--dead-row`）。
  展开态与原来完全一致（`★ | Player | Army | Land`）。
- **聊天收起**：拖动条右端的 `#chat-hide-button`（`—`）。收起后消息容器与输入框
  `display: none`，面板缩成一颗白底青字的 `聊天 ▸` 小按钮（还在同一个拖动握把里，所以仍可拖）；
  点它恢复完整面板（拖动条 / 消息 / 输入框 / placeholder 都不变）。
  因为面板尺寸变化很大，`Chat.setCollapsed()` 在切换后会把已固化的 `left/top` 重新夹回视口，
  否则「点展开」会让撑开的面板溢到屏幕外。
  键盘 `Enter` 走到 `chat.focus()` 时，若聊天已收起会先自动展开再聚焦。

## 状态与网络

- `core/state.ts` 只保存「服务端告诉我的一切」，**不做任何战斗预测**（R7.3）。
- 地块 `seen` 判定唯一依据：`tiles` 帧里 `army !== null`（服务端对视野外发 `army = null`）。
- `core/client.ts` 连 `ws(s)://<同源>/ws`，指数退避重连（500ms→8s），5 秒一次 `ping`。
- 战斗、增长、继承全部由服务端算完再下发；前端只显示。

## 演示模式

`?demo=1` 是不连服务端的本地静态棋盘，用于视觉比对截图：

| URL | 用途 |
| --- | --- |
| `/?demo=1` | 默认演示局（选中格比例 100%，不显示比例文字） |
| `/?demo=1&demoRatio=0.5` | 选中格比例为 50%，用于截「选中格比例文字」（R6.6） |

## 回放页（`replay.html`）

对局页顶部的「回放」按钮打开列表；`?id=<id>` 直接播一局。见 `docs/REPLAY.md`。

- **重放方式**：回放的 `frames` 与线协议同构（每帧多一个 `tick`），所以播放器就是把帧按序
  喂给 `core/state.ts::applyFrame` —— 与对局页共用同一套状态机与绘制代码，没有专用解码器。
- **`ReplayCursor`**：`seek(tick)` 往前只增量应用，往回则从空状态重放（保证任意跳转的结果与
  逐帧播放完全一致）；`maxTick` 取 `result.tick` 与最后一帧 `tick` 的较大者。
- **播放**：`requestAnimationFrame` 驱动，每秒推进 `1000 / tickMs * speed` 个 Tick；
  播到最后一刻自动停在终点。`空格` 播放/暂停，`←/→` 单步、`Shift+←/→` 十步。
- **AI 调试流**（`src/replay/ailog.ts`）：按玩家下拉框、按 `kind`（日志/动作/异常/被拒/作废）
  切换筛选；点条目跳到该条目的 Tick 并继续播放；播放时高亮当前刻的条目。
- **只读面板**：排行榜、回合、聊天都复用 `ui/` 的实现；聊天因为要**回溯**，改用
  `chatlist.ts` 按内容指纹整体重建（对局页的增量追加在往回拖时会错乱）。
- 列表模式（无 `?id=`）隐藏播放相关面板，显示最近 200 份回放（时间/房间/玩家与策略/回合/结果/帧数/AI 条目/大小）。

## 命令

```powershell
pnpm exec tsc --noEmit   # 类型检查（strict + noUnusedLocals，未使用的 import 也会报错）
pnpm dev                 # :5173，/ws 与 /api 反代到 :8000
pnpm build               # → web/dist，由后端静态托管
node scripts/shot.mjs "http://127.0.0.1:5173/?demo=1" out.png 1600 900 1500
node scripts/shot.mjs "http://127.0.0.1:5173/?demo=1&demoRatio=0.5" ratio.png 1600 900 1500
node scripts/e2e-page.mjs http://127.0.0.1:8000 out.png 5000
node scripts/e2e-replay.mjs http://127.0.0.1:8124 <replayId> out.png   # 回放页（用 scripts/e2e_replay.py 起服务）
```

> 截图脚本用 `playwright-core` + 本机 Edge（`channel: 'msedge'`），必须在 `web/` 目录下执行。
> 联调脚本在 1600×900 视口下断言 canvas 为 1600×900、格边长 `900/size ∈ [50,75]`（R2.3）、
> 五个浮动面板（含 `#fullscreen-button`）在视口内且覆盖在 canvas 之上，验证排行榜拖动后
> `localStorage` 记住位置，并在 `?demo=1&demoRatio=0.5` 页验证「选中格有 `50%` 文字」与
> 「按一次方向键后比例回 100%」。此外在 `?demo=1` 页验交互细节：
>
> 1. **缩放锚点**：鼠标移到某点 → `wheel(0, -120)` 一次 → `tileAt(x, y)` 仍是同一格且 `cell` 变大；
> 2. **无钳制平移**：把地图向左上拖 2700 px → `origin.x < -800`，画布采样只剩 `#141414`；
> 3. **全屏按钮**：`#fullscreen-button` 存在；stub `requestFullscreen` 后点击，断言被调用；
> 4. **记分板折叠**：折叠后表头 `Player/Army/Land` 与玩家名文本都消失，每行 3 个单元
>    （色块 + 2 个数字），再点一次恢复；
> 5. **聊天收起**：消息容器 `display: none`、展开按钮可见；展开后消息恢复且 `#chatroom-input` 可聚焦。

## 调试钩子

`main.ts` 把只读快照挂到 `window.__generalsDebug()`：

| 字段 | 含义 |
| --- | --- |
| `size / cell / zoom / width / height` | 地图边长、格边长（CSS px）、缩放档、视口尺寸 |
| `origin` | 地图左上角的屏幕坐标（= 地块 0 的左上角；平移后可负、跑出视口） |
| `ratio / selected / chain` | 调兵链：比例、选中地块、链长 |
| `rect(i)` | 某格在屏幕上的矩形（含 gap / fill） |
| `tileAt(x, y)` | 屏幕坐标（相对画布左上角的 CSS px）→ 地块索引，越界 `null` |

自动化脚本据此拿到真实几何（视野可能只有两三格，从像素反推格边长会得出荒谬数字），
不改变任何游戏状态。

回放页另有两组（只在 `/replay.html` 上存在）：

| 钩子 | 含义 |
| --- | --- |
| `window.__replayDebug()` | `{id, tick, maxTick, playing, size, cell, origin, turn, frames, ai, chat, players, aiRows}` |
| `window.__replaySeek(tick)` / `window.__replayPlay(bool)` | 跳转 / 播放暂停（e2e 用来验证游标与播放推进） |
