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
  generals/ai/            AI 接入（进程内 registry + 进程外 HTTP）
  generals/transport/     FastAPI 适配（HTTP 路由 / WebSocket 端点）
  generals/app.py         唯一装配点
  tests/                  后端测试
web/
  src/core/               纯逻辑：类型、几何、客户端状态、调兵链、连接
  src/render/             Canvas：调色板、摄像机、图标、绘制
  src/ui/                 排行榜、回合计数器、聊天
  src/input/              键盘、指针
  src/main.ts             唯一装配点
scripts/                  一键脚本 + 端到端冒烟
```

## 实现进度

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 文档骨架（RULES / ARCHITECTURE / INTERFACES / PROTOCOL / AI_API） | ✅ | 含 `C-01..C-18` 用户裁决、`P-01..P-06` 待澄清清单 |
| `server/generals/game` 纯内核 | 🚧 | Tick 结算 / 战斗 / 成长 / 视野投影已实现，正补规则全覆盖测试 |
| `server/generals/net` 房间与连接 | ✅ | 大厅、席位、协议编解码、视野增量、慢客户端处理 |
| `server/generals/ai` AI 接口 | ✅ | `register()` 注册 + `hold/greedy/random` 内置策略 + `RemoteAgent` |
| `server/generals/transport` + `app` | ✅ | FastAPI 装配、`/api/rooms`、`/api/config`、`/ws`、静态托管 `web/dist` |
| `web` 前端 | ✅ | 地图渲染、排行榜、聊天、调兵链（WASD/Q/E/±）、点击调兵、缩放平移 |
| 端到端冒烟 `scripts/e2e_smoke.py` | ✅ | 真起 uvicorn + 真 WS 客户端，覆盖 join/start/tick/move/chat/surrender/观战 |
| 观战、房间列表页 | ✅ | `web/waiting.html` + `?spectate=1` |

## 待澄清

实现过程中发现的、规则原文未覆盖或自相矛盾的点记录在
[`docs/RULES.md`](docs/RULES.md) 附录 B（`P-01..P-06`），均有可运行的临时取舍，未擅自当成定论。

## 许可

仅用于学习与演示。
