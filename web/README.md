# web — 前端

原生 TypeScript + Canvas 2D，无框架、无运行时依赖。构建工具 Vite。
样式与配色以 `SaveWeb/屏幕截图 *.png` 为视觉基准。

## 目录

```
index.html            对局页入口
waiting.html          房间列表页入口
src/core/             纯逻辑（无 DOM、无 canvas，可单测）
  types.ts            线协议与游戏对象的类型定义
  geometry.ts         索引 ↔ (row, col)、4/8 邻、方向
  state.ts            ClientState + applyFrame（不可变帧应用）
  chains.ts           本地调兵链镜像（选择、比例、push/pop/clear）
  client.ts           WebSocket 连接（自动重连、心跳、发送队列）
src/render/            Canvas 绘制
  palette.ts          实测配色（玩家色 / 地块色 / UI 色）
  camera.ts           正投影摄像机：居中、缩放档位、命中测试、可见范围
  icons.ts            山峰 / 城堡 / 王冠 / 骷髅（矢量路径，缩放无关）
  painter.ts          paint(ctx, cam, state, chain, opts) 的唯一绘制入口
src/ui/               DOM 面板（全部是压在 canvas 上的浮动层）
  turn.ts             Turn N / Turn N. 计数器
  leaderboard.ts      ★ | Player | Army | Land
  chat.ts             聊天与系统消息（顶部一条窄拖动条）
  floating.ts         makeDraggable：面板拖动 + localStorage 位置记忆
src/input/            输入
  keyboard.ts         WASD / Q / E / ± / 方向键 / Enter
  pointer.ts          点击选中、点击调兵、拖拽平移、滚轮缩放
src/main.ts           唯一装配点（把上面四层接起来）
styles/               palette.css（CSS 变量）+ game.css + waiting.css
public/fonts/         Quicksand 500/700（与原站一致的几何无衬线数字）
scripts/shot.mjs      playwright-core + 本机 Edge 截图
scripts/e2e-page.mjs  真浏览器 × 真后端联调（两个页面开局 + 断言 + 拖动记忆 + 截图）
```

## 布局：全屏地图 + 四个浮动面板

地图 canvas 铺满整个视口（`position: fixed; inset: 0`），没有固定的侧边栏；
回合 / Surrender / 排行榜 / 聊天都是浮在 canvas 之上的独立 DOM 层。

| 面板 | 元素 | 默认位置 | 样式要点 |
| --- | --- | --- | --- |
| 回合计数（R1.2） | `#turn-counter` | 左上 `12,12` | 深色半透明底 + 白色 20px 粗体，文本 `Turn N` / `Turn N.` |
| 投降 | `#surrender-button` | 顶部居中 | 白底青字（沿用原站样式） |
| 排行榜（R6.8） | `#game-leaderboard-container` | 右上 `12,12` | 宽 300 px，表格外观不变 |
| 聊天（R6.10） | `#in-game-chat` | 右下 `12,12` | 宽 340 px、高 38vh，黑底灰字 |

**可拖动 + 记忆位置**（`src/ui/floating.ts`）：

- Pointer Events；移动阈值 4 px —— 阈值内视为点击，所以 **Surrender 仍是一颗能点的按钮**，
  拖动结束后随之而来的那次 `click` 会被 `200ms` 的守卫吞掉，不会误触投降。
- 聊天面板用顶部 18 px 的 `.chat-drag-handle` 作握把，不与消息滚动、输入框抢事件；
  其余三块整块可拖。
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
拖拽平移，中心被 `clampCamera` 夹在地图内（地图比视口小时自动居中）。
整个视口都能拖、能缩（面板区域除外，事件被面板自己吃掉）。

坐标换算只有两条公式，别在别处重复实现：

```
屏幕 x = cam.width  / 2 + (col - cam.cx) * cell
屏幕 y = cam.height / 2 + (row - cam.cy) * cell
```

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

## 命令

```powershell
pnpm exec tsc --noEmit   # 类型检查（strict + noUnusedLocals，未使用的 import 也会报错）
pnpm dev                 # :5173，/ws 与 /api 反代到 :8000
pnpm build               # → web/dist，由后端静态托管
node scripts/shot.mjs "http://127.0.0.1:5173/?demo=1" out.png 1600 900 1500
node scripts/shot.mjs "http://127.0.0.1:5173/?demo=1&demoRatio=0.5" ratio.png 1600 900 1500
node scripts/e2e-page.mjs http://127.0.0.1:8000 out.png 5000
```

> 截图脚本用 `playwright-core` + 本机 Edge（`channel: 'msedge'`），必须在 `web/` 目录下执行。
> 联调脚本在 1600×900 视口下断言 canvas 为 1600×900、格边长 `900/size ∈ [50,75]`（R2.3）、
> 四个浮动面板在视口内且覆盖在 canvas 之上，验证排行榜拖动后 `localStorage` 记住位置，
> 并在 `?demo=1&demoRatio=0.5` 页验证「选中格有 `50%` 文字」与「按一次方向键后比例回 100%」。

## 调试钩子

`main.ts` 把只读快照挂到 `window.__generalsDebug()`：`{ size, cell, zoom, width, height,
ratio, selected, chain, rect(i) }`。自动化脚本据此拿到真实几何（视野可能只有两三格，
从像素反推格边长会得出荒谬数字），不改变任何游戏状态。
