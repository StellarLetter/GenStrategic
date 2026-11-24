"""`ai` 包公开 API 汇总。

契约见 `docs/INTERFACES.md` §3 与 `docs/AI_API.md`。

导出：
- `Observation` / `Action` / `GameSpec` / `Row` / `Agent`：AI 接口数据结构与协议
- `RATIOS`：AI 可用的调兵比例集合（10%..100%，与真人一致，R6.6）
- `to_observation`：把一个 `GameState` 裁剪成某玩家视角的观测（内部调 `game.view.project`）
- `register` / `get` / `all_names`：策略注册表
- `AIRunner` / `make_agent`：把策略挂到房间座位上
- `RemoteAgent`：进程外（HTTP）策略客户端
- `log`：**策略调试日志**（写进本局回放，`docs/AI_API.md` §11）
- `debug`：日志模块本体（`ai.debug.capture` 供离线工具接管日志）
- `StrategyLoader` / `ai_dir`：策略目录**自动挂载**（默认 `<repo>/ai`，AI_API.md §3）
- `builtin`：内置策略模块（import 即完成 `hold` / `greedy` / `random` 的注册）
"""

from __future__ import annotations

from . import builtin, debug, registry
from .api import (
    RATIOS,
    Action,
    Agent,
    GameSpec,
    Observation,
    Row,
    to_observation,
    to_observation_for,
)
from .builtin import BUILTIN_NAMES, Greedy, Hold, Random, neighbours4
from .debug import log
from .loader import DEFAULT_AI_DIR, LoadReport, StrategyInfo, StrategyLoader, ai_dir
from .registry import all_names, get, register, registered
from .remote_client import RemoteAgent
from .runner import AIRunner, make_agent, spec_from_state

__all__ = [
    "AIRunner",
    "Action",
    "Agent",
    "BUILTIN_NAMES",
    "DEFAULT_AI_DIR",
    "GameSpec",
    "Greedy",
    "Hold",
    "LoadReport",
    "Observation",
    "RATIOS",
    "Random",
    "RemoteAgent",
    "Row",
    "StrategyInfo",
    "StrategyLoader",
    "ai_dir",
    "all_names",
    "builtin",
    "debug",
    "get",
    "log",
    "make_agent",
    "neighbours4",
    "register",
    "registered",
    "registry",
    "spec_from_state",
    "to_observation",
    "to_observation_for",
]
