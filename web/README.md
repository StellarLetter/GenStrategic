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
src/ui/               DOM 面板
  turn.ts             Turn N / Turn N. 计数器
  leaderboard.ts      ★ | Player | Army | Land
  chat.ts             聊天与系统消息
src/input/            输入
  keyboard.ts         WASD / Q / E / ± / 方向键 / Enter
  pointer.ts          点击选中、点击调兵、拖拽平移、滚轮缩放
src/main.ts           唯一装配点（把上面四层接起来）
styles/               palette.css（CSS 变量）+ game.css + waiting.css
public/fonts/         Quicksand 500/700（与原站一致的几何无衬线数字）
scripts/shot.mjs      playwright-core + 本机 Edge 截图
scripts/e2e-page.mjs  真浏览器 × 真后端联调（两个页面开局 + 断言 + 截图）
```

## 渲染顺序

`painter.paint()` 固定为：

1. 地图底色 `#393939`（与原站一致：地图区域底色 = 迷雾色）
2. 逐格填充——**视野内**先整格铺 `#000000` 再内缩填充（缝隙即纯黑网格线）；
   **视野外**整格铺 `#393939`，相邻格无缝，迷雾是一片连续底色（R6.9 只折叠"外观"，不折叠底色）
3. 地形图标：视野外仅折叠成山地的格子画山形；视野内画山峰 / 城堡 / 王冠（山形一律纯黑、线宽约格子的 2%）
4. 兵量数字（覆盖在图标之上；兵量为 0 不画）
5. 调兵链箭头
6. 选中地块白框

**关键细节**：当某格要叠兵量数字时，图标改用半透明深色（`TILE.iconStrokeDimmed`）退成背景纹理，
否则白图标 + 白数字会糊在一起看不清。原站城市的深色线稿 + 白色数字就是这个道理。

## 摄像机

`camera.ts` 是正投影：默认 `zoom = 1` 表示**整张地图正好铺满视口短边**（与原站存档页
`#gameMap { width/height: size * --cell-size }` 一致），滚轮在 `ZOOM_LEVELS` 之间切换，
拖拽平移，中心被 `clampCamera` 夹在地图内（地图比视口小时自动居中）。

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

## 命令

```powershell
pnpm exec tsc --noEmit   # 类型检查（strict + noUnusedLocals，未使用的 import 也会报错）
pnpm dev                 # :5173，/ws 与 /api 反代到 :8000
pnpm build               # → web/dist，由后端静态托管
node scripts/shot.mjs "http://127.0.0.1:5173/?demo=1" out.png 1600 900 1500
node scripts/e2e-page.mjs http://127.0.0.1:8000 out.png 5000
```

> 截图脚本用 `playwright-core` + 本机 Edge（`channel: 'msedge'`），必须在 `web/` 目录下执行。
