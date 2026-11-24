# generals — generals.io 复刻版

Python 后端（游戏内核 + 联网服务）+ 原生 TypeScript/Canvas 前端的 generals.io 复刻。
规则以 [`docs/RULES.md`](docs/RULES.md) 为**唯一事实源**，实现不做任何规则层面的猜测。

## 快速开始

```powershell
# 1) 依赖（首次）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "server[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
cd web; pnpm install; cd ..

# 2) 一键：构建前端 + 起服务（页面 / API / WebSocket 同一端口）
.\scripts\dev.ps1 -Port 8000
#    → http://127.0.0.1:8000/           对局页
#    → http://127.0.0.1:8000/waiting.html  房间列表页

# 3) 全量校验（后端 pytest → 前端 tsc → 前端构建 → 端到端冒烟）
.\scripts\test.ps1
```

开发时想要热更新，开两个终端：
```powershell
.\.venv\Scripts\python.exe -m generals --port 8000 --reload   # 后端
cd web; pnpm dev                                              # 前端 :5173，/api 与 /ws 反代到 :8000
```

## 目录

```
AGENT.md                  上手指南（先读这个）
docs/                     规则 / 架构 / 契约 / 线协议 / AI 接口
server/
  generals/game/          纯内核：状态、Tick 结算、战斗、指令、地图生成、视野投影
  generals/net/           房间、连接、席位、协议编解码、大厅
  generals/ai/            AI 接入（自动挂载 ai/ 目录 + registry + 进程外 HTTP）
  generals/transport/     FastAPI 适配（HTTP 路由 / WebSocket 端点）
  generals/app.py         唯一装配点
  tests/                  后端测试
ai/                       策略目录：放 *.py 自动挂载（见 docs/AI_API.md）；个人策略不入库，仅 sample_greedy.py
web/
  src/core/               纯逻辑：类型、几何、客户端状态、调兵链、连接
  src/render/             Canvas：调色板、摄像机、图标、绘制
  src/ui/                 排行榜、回合计数器、聊天、策略接管面板
  src/input/              键盘、指针
  src/main.ts             唯一装配点
scripts/                  一键脚本 + 端到端冒烟
```

## 实现状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 文档（RULES / ARCHITECTURE / INTERFACES / PROTOCOL / AI_API） | ✅ | 规则事实源 `R1.1–R7.4`；变更过程单列在 [`docs/DECISIONS.md`](docs/DECISIONS.md) |
| `server/generals/game` 纯内核 | ✅ | 随机地图（边长由人数决定、无对称、首都曼哈顿间距 ≥13）/ 中立敌对（R3.8）/ 调兵链与预调兵（R4.11）/ 作废级联（R4.12）/ 遭遇战 / 阵地战（含敌方 0 兵力地块按 R4.4 易手）/ 增长 / 王城继承 / 投降 / 视野投影 |
| `server/generals/net` 房间与连接 | ✅ | 大厅、席位、协议编解码、视野增量、慢客户端处理、刻时钟、`chain_dropped` 作废反馈、聊天栏只播三种消息且 `captured` 只在停止抵抗时播（R6.10）、边长随开局人数（R2.3） |
| `server/generals/ai` AI 接口 | ✅ | **`ai/` 目录自动挂载**全部策略（`StrategyLoader`，热重载 `POST /api/ai/reload`）+ `hold/greedy/random` 内置策略 + `RemoteAgent`（进程外 HTTP）；`bot` 机器人座位与 `autopilot` 策略托管都真正被房间驱动；比例调兵限定在真人的十档 `RATIOS`（R6.6）。编写方法见 [`docs/AI_API.md`](docs/AI_API.md) |
| `server/generals/transport` + `app` | ✅ | FastAPI 装配、`/api/rooms`、`/api/config`、`/api/ai/strategies`、`/api/ai/reload`、`/healthz`、`/ws`、静态托管 `web/dist` |
| `web` 前端 | ✅ | 整窗地图（`--bg-void` 非地图区）+ 可拖动浮层（回合/投降/全屏/记分板/聊天，位置记忆，R6.14）；光标锚点缩放与无边界平移（R6.15）；全屏、记分板折叠、聊天隐藏（R6.16）；选中格 `N%` 比例叠加（R6.6）、迷雾与黑色网格线、调兵链（WASD/Q/E/±）、预调兵、点击调兵、作废静默清链 |
| 协议级端到端 `scripts/e2e_smoke.py` | ✅ | 真起 uvicorn + 真 WS 客户端，覆盖 join/start/tick/move/chat/surrender/观战/3 人房/HTTP |
| 浏览器级端到端 `web/scripts/e2e-page.mjs` | ✅ | 真 Edge 双页面开局，断言画布尺寸（整窗）、浮层存在与拖动记忆、格边长（R2.3）、迷雾底色、网格线、排行榜、回合文本、聊天首行提示并截图 |
| 观战、房间列表页 | ✅ | `web/waiting.html` + `?spectate=1` |

一键验收：

```powershell
.\scripts\dev.ps1 -Port 8000    # 起服务，浏览器开 http://127.0.0.1:8000/
.\scripts\test.ps1              # pytest + tsc + build + e2e 全绿
```

## 许可

仅用于学习与演示。
