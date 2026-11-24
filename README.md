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
docs/                     规则 / 架构 / 契约 / 线协议 / AI 接口 / 回放 / 策略说明
server/
  generals/game/          纯内核：状态、Tick 结算、战斗、指令、地图生成、视野投影
  generals/net/           房间、连接、席位、协议编解码、大厅
  generals/replay/        对局回放：录制（ReplayRecorder）、落盘与索引（ReplayStore）
  generals/ai/            AI 接入（自动挂载 ai/ 目录 + registry + 调试日志 + 进程外 HTTP）
  generals/transport/     FastAPI 适配（HTTP 路由 / WebSocket 端点）
  generals/app.py         唯一装配点
  tests/                  后端测试
ai/                       策略目录：放 *.py 自动挂载（见 docs/AI_API.md）；个人策略不入库，仅 sample_greedy.py
replays/                  回放目录（每局自动落一份，运行期数据、不入库；见 docs/REPLAY.md）
tools/                    离线工具：ai_arena.py（策略对战基准，可 --replay-dir 留档）/ replay.py（回放 CLI）
web/
  src/core/               纯逻辑：类型、几何、客户端状态、调兵链、连接、回放游标
  src/render/             Canvas：调色板、摄像机、图标、绘制
  src/ui/                 排行榜、回合计数器、聊天、策略接管面板
  src/replay/             回放页（replay.html）装配、控制条、AI 调试流、只读聊天
  src/input/              键盘、指针
  src/main.ts             唯一装配点
scripts/                  一键脚本 + 端到端冒烟（smoke / replay）
```

## 对局回放与策略复盘

**每一局都自动录制、结束时自动保存**，不需要任何开关（R8.1）。回放里有完整帧流（观战者视角）
和各策略的调试条目：`log()` 日志、每刻动作、异常、被拒指令、被作废的调兵请求（R8.3 / R8.4）。

```powershell
# 网页：对局页顶部「回放」按钮，或直接
#   http://127.0.0.1:8000/replay.html            ← 最近的对局列表
#   http://127.0.0.1:8000/replay.html?id=<id>    ← 播放一局

# 终端
.venv\Scripts\python.exe tools\replay.py list
.venv\Scripts\python.exe tools\replay.py show <id> --ai --player 0
```

策略里这样写日志（详见 [`docs/AI_API.md`](docs/AI_API.md) §11）：

```python
from generals.ai import log
log(f"turn {obs.turn}: army={obs.my_army} land={obs.my_land}")
```

> **对局中读不到回放**（R8.2）：录制缓冲只在服务端内存里，对局结束才落盘；
> 接口读到「仍在进行」的回放会返回 409。文件格式与字段见 [`docs/REPLAY.md`](docs/REPLAY.md)。

## 1v1 策略

`ai/aegis.py` 是一份自包含的 1v1 策略（自动挂载，注册名 `aegis`）：**广占土地攒经济、单股主力朝
对手王城汇聚、抵近斩首**。设计推演、决策流水线与参数说明见
[`docs/STRATEGY_AEGIS.md`](docs/STRATEGY_AEGIS.md)。

离线对战基准（真实内核 + 真人与 AI 共用的校验路径，双座位各打一遍、逐局统计胜负与回合数）：

```powershell
.\.venv\Scripts\python.exe tools\ai_arena.py --a aegis --b greedy,sample-greedy --games 30 --swap
.\.venv\Scripts\python.exe tools\ai_arena.py --a aegis --b cosmos-apex --games 30 --seed0 3000 --swap --max-ticks 1400
```

> 按 `ai/README.md` 的发布约定，`ai/*.py` 不入库（仅 `sample_greedy.py` 例外），
> 因此策略文件留在本机、随本机运行；`tools/ai_arena.py` 与策略说明文档入库。

## 实现状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 文档（RULES / ARCHITECTURE / INTERFACES / PROTOCOL / AI_API） | ✅ | 规则事实源 `R1.1–R7.4`；变更过程单列在 [`docs/DECISIONS.md`](docs/DECISIONS.md) |
| `server/generals/game` 纯内核 | ✅ | 随机地图（边长由人数决定、无对称、首都曼哈顿间距 ≥13）/ 中立敌对（R3.8）/ 调兵链与预调兵（R4.11）/ 作废级联（R4.12）/ 遭遇战 / 阵地战（含敌方 0 兵力地块按 R4.4 易手）/ 增长 / 王城继承 / 投降 / 视野投影 |
| `server/generals/net` 房间与连接 | ✅ | 大厅、席位、协议编解码、视野增量、慢客户端处理、刻时钟、`chain_dropped` 作废反馈、聊天栏只播三种消息且 `captured` 只在停止抵抗时播（R6.10）、边长随开局人数（R2.3） |
| `server/generals/replay` 对局回放 | ✅ | **每局自动录制并落盘**（R8.1）；对局中不可读（R8.2，HTTP 409）；帧流与线协议同构、地块用观战者视角（R8.3）；策略日志/动作/异常/被拒/作废都留痕（R8.4）；中断也留档（R8.5）；保留上限与截断保险丝（R8.6）。格式见 [`docs/REPLAY.md`](docs/REPLAY.md) |
| `server/generals/ai` AI 接口 | ✅ | **`ai/` 目录自动挂载**全部策略（`StrategyLoader`，热重载 `POST /api/ai/reload`）+ `hold/greedy/random` 内置策略 + `RemoteAgent`（进程外 HTTP）；`bot` 机器人座位与 `autopilot` 策略托管都真正被房间驱动；比例调兵限定在真人的十档 `RATIOS`（R6.6）。编写方法见 [`docs/AI_API.md`](docs/AI_API.md) |
| `server/generals/transport` + `app` | ✅ | FastAPI 装配、`/api/rooms`、`/api/config`、`/api/ai/strategies`、`/api/ai/reload`、`/healthz`、`/ws`、静态托管 `web/dist` |
| `web` 前端 | ✅ | 整窗地图（`--bg-void` 非地图区）+ 可拖动浮层（回合/投降/全屏/回放/记分板/聊天，位置记忆，R6.14）；光标锚点缩放与无边界平移（R6.15）；全屏、记分板折叠、聊天隐藏（R6.16）；选中格 `N%` 比例叠加（R6.6）、迷雾与黑色网格线、调兵链（WASD/Q/E/±）、预调兵、点击调兵、作废静默清链 |
| `web` 回放页 `/replay.html` | ✅ | 对局列表 + 播放器（播放/暂停、0.5×–16× 倍速、时间轴按 Tick、空格/方向键单步）+ 排行榜/聊天/回合 + **AI 调试流面板**（按玩家与 kind 筛、点条目跳到该刻） |
| 协议级端到端 `scripts/e2e_smoke.py` | ✅ | 真起 uvicorn + 真 WS 客户端，覆盖 join/start/tick/move/chat/surrender/观战/3 人房/HTTP |
| 浏览器级端到端 `web/scripts/e2e-page.mjs` | ✅ | 真 Edge 双页面开局，断言画布尺寸（整窗）、浮层存在与拖动记忆、格边长（R2.3）、迷雾底色、网格线、排行榜、回合文本、聊天首行提示并截图 |
| 回放端到端 `scripts/e2e_replay.py` | ✅ | 自造样本回放 → 真服务 → 真 Edge（`web/scripts/e2e-replay.mjs`）：列表、播放、跳转与回合一致、AI 流筛选与跳条、倍速推进、键盘单步 |
| 回放 CLI `tools/replay.py` | ✅ | `list` / `show --ai` / `demo`（造样本）；离线基准 `tools/ai_arena.py --replay-dir` 每局留档 |
| 观战、房间列表页 | ✅ | `web/waiting.html` + `?spectate=1` |

一键验收：

```powershell
.\scripts\dev.ps1 -Port 8000    # 起服务，浏览器开 http://127.0.0.1:8000/
.\scripts\test.ps1              # pytest + tsc + build + e2e（含回放端到端）全绿
```

## 许可

仅用于学习与演示。
