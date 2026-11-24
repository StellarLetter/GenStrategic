"""`ai` 包公开 API 汇总。

契约见 `docs/INTERFACES.md` §3 与 `docs/AI_API.md`。

导出：
- `Observation` / `Action` / `GameSpec` / `Row` / `Agent`：AI 接口数据结构与协议
- `RATIOS`：AI 可用的调兵比例集合（10%..100%，与真人一致，R6.6）
- `to_observation`：把一个 `GameState` 裁剪成某玩家视角的观测（内部调 `game.view.project`）
- `register` / `get` / `all_names`：策略注册表
- `AIRunner` / `make_agent`：把策略挂到房间座位上
- `RemoteAgent`：进程外（HTTP）策略客户端
- `builtin`：内置策略模块（import 即完成 `hold` / `greedy` / `random` 的注册）
"""

from __future__ import annotations

from . import builtin, registry
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
from .builtin import Greedy, Hold, Random, neighbours4
from .registry import all_names, get, register, registered
from .remote_client import RemoteAgent
from .runner import AIRunner, make_agent, spec_from_state

__all__ = [
    "AIRunner",
    "Action",
    "Agent",
    "GameSpec",
    "Greedy",
    "Hold",
    "Observation",
    "RATIOS",
    "Random",
    "RemoteAgent",
    "Row",
    "all_names",
    "builtin",
    "get",
    "make_agent",
    "neighbours4",
    "register",
    "registered",
    "registry",
    "spec_from_state",
    "to_observation",
    "to_observation_for",
]
