"""进程外策略客户端：用 HTTP 把 AI 接到对局里（AI_API.md §3）。

协议::

    POST <url>
    { "observation": {...} }
    → 200 { "action": {"op": "move", "src": 202, "dir": "up", "ratio": 1.0} }
    → 200 { "action": null }            # 本 Tick 不操作
    超时 / 非 200 / 结构非法 ⇒ 视为本 Tick 不操作

**关键约束**：`httpx` 是同步库；在事件循环里直接调用会阻塞整个 loop。
因此所有网络调用都通过 `asyncio.to_thread` 转移到线程池执行（BRIEF 成员 B §9）。
同时保留一个纯同步的 `decide()`，供**非异步**上下文（离线自对弈、脚本）使用。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Final

from .api import Action, GameSpec, Observation

__all__ = [
    "DEFAULT_TIMEOUT",
    "RemoteAgent",
    "encode_observation",
    "decode_action",
]

#: 默认超时（秒）。AI_API.md §3 写「默认 500ms」；这里与 1 Tick 对齐。
DEFAULT_TIMEOUT: Final[float] = 0.4


def encode_observation(obs: Observation) -> dict:
    """把 `Observation` 序列化成 AI_API.md §3 的 `observation` 字段。

    前置：`obs` 由 `ai.api.to_observation` 构造。
    后置：返回纯 dict（JSON 可序列化），字段名 snake_case 与 AI_API.md §1.1 一致：
          `turn/half/size/me/tiles/my_army/my_land/leaderboard/chain`；
          每个 `tiles` 项为 `{"index","army","owner","terrain"}`（`army` 可为 `null`）。
    抛出：无。
    """
    return {
        "turn": obs.turn,
        "half": obs.half,
        "size": obs.size,
        "me": obs.me,
        "tiles": [
            {"index": tv.index, "army": tv.army, "owner": tv.owner, "terrain": tv.terrain}
            for tv in obs.tiles
        ],
        "my_army": obs.my_army,
        "my_land": obs.my_land,
        "leaderboard": [
            {
                "playerId": row.player_id,
                "name": row.name,
                "color": row.color,
                "army": row.army,
                "land": row.land,
                "alive": row.alive,
            }
            for row in obs.leaderboard
        ],
        "chain": [
            {
                "from": int(getattr(req, "src", 0)),
                "dir": str(getattr(getattr(req, "dir", ""), "value", getattr(req, "dir", ""))),
                "ratio": float(getattr(req, "ratio", 1.0)),
            }
            for req in obs.chain
        ],
    }


def decode_action(payload: Any) -> Action | None:
    """把策略服务的响应体解析成 `Action`。

    前置：`payload` 是 `json.loads` 的结果（dict 或 None）。
    后置：
    - `payload is None` / 缺 `action` 键 / `action is None` → 返回 `None`（本 Tick 不操作）；
    - 合法 dict → 返回 `Action`；未知 `op` 抛 `ValueError`。
    抛出：`ValueError` —— 结构非法（调用方应视为「不操作」并记录）。
    """
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")
    raw = payload.get("action")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("action must be an object or null")
    op = raw.get("op")
    if not isinstance(op, str):
        raise ValueError("action.op must be a string")
    op = op.strip().lower()
    if op == "move":
        src = raw.get("from", raw.get("src"))
        if not isinstance(src, int) or isinstance(src, bool):
            raise ValueError("move.from must be int")
        direction = raw.get("dir")
        if not isinstance(direction, str):
            raise ValueError("move.dir must be a string")
        ratio = raw.get("ratio", 1.0)
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            raise ValueError("move.ratio must be a number")
        return Action(op="move", src=int(src), dir=direction.strip().lower(), ratio=float(ratio))
    if op == "select":
        tile = raw.get("tile")
        if not isinstance(tile, int) or isinstance(tile, bool):
            raise ValueError("select.tile must be int")
        return Action(op="select", tile=int(tile))
    if op in ("clear", "pop"):
        return Action(op=op)  # type: ignore[arg-type]
    raise ValueError(f"unknown action op: {op!r}")


@dataclass
class RemoteAgent:
    """把一个 HTTP 策略服务包装成 `Agent`（AI_API.md §3）。

    前置：`url` 形如 `http://127.0.0.1:9000/decide`（AI_API.md §3 的 `POST /decide`）。
    后置：
    - `act(obs)` 是**同步**接口（满足 `Agent` 协议）；若在事件循环里调用，
      调用方应改用 `ai.runner.AIRunner.step_async()`（它内部 `asyncio.to_thread`），
      或直接 `await agent.act_async(obs)`。
    - 超时/非 200/结构非法 ⇒ 返回 `None`（本 Tick 不操作），并把原因记到 `last_error`。
    抛出：构造不抛；`act()`/`act_async()` 不抛（网络问题一律降级为 `None`）。
    """

    url: str
    name: str = "remote"
    timeout: float = DEFAULT_TIMEOUT
    headers: dict[str, str] = field(default_factory=dict)
    size: int = 0
    last_error: str | None = None

    # ------------------------------------------------------------------
    def reset(self, spec: GameSpec) -> None:
        """开局初始化：记录地图边长（**不发网络请求**，避免开局被远端拖慢）。"""
        self.size = spec.size
        self.last_error = None

    def act(self, obs: Observation) -> Action | None:
        """同步决策：`POST {url}` 发 `{"observation": ...}`，解析 `{"action": ...}`。

        前置：无（`obs` 来自 `ai.api.to_observation`）。
        后置：返回 `Action` 或 `None`；任何错误都降级为 `None` 并写入 `last_error`。
        抛出：**不抛**（网络/解析异常全部吞掉，避免拖垮 Tick 时钟）。
        """
        try:
            return self._post_blocking({"observation": encode_observation(obs)})
        except Exception as exc:  # noqa: BLE001 - 进程外策略的任何失败都视为「不操作」
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    async def act_async(self, obs: Observation) -> Action | None:
        """异步决策：把同步 httpx 调用丢到线程池，**不阻塞事件循环**（BRIEF 成员 B §9）。

        前置：同 `act()`。
        后置：同 `act()`；内部使用 `asyncio.to_thread(self._post_blocking, payload)`。
        抛出：**不抛**。
        """
        try:
            return await asyncio.to_thread(
                self._post_blocking, {"observation": encode_observation(obs)}
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    # ------------------------------------------------------------------
    def _post_blocking(self, payload: dict) -> Action | None:
        """真正发 HTTP 请求（**同步、阻塞**；只能在线程池里调用）。

        前置：`payload` 为 `{"observation": {...}}` 形状的 dict。
        后置：返回解析后的 `Action` 或 `None`；HTTP 非 200 抛 `RuntimeError`。
        抛出：`httpx` 的各种网络异常、`json.JSONDecodeError`、`ValueError`、`RuntimeError`。
        """
        import httpx  # 延迟 import：让不装 httpx 的部署仍可使用进程内 AI

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.url, content=body, headers={"Content-Type": "application/json", **self.headers})
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return decode_action(resp.json())

    def close(self) -> None:
        """释放资源（当前无持久连接，空实现；`AIRunner.close()` 会调用）。前置/后置：无。抛出：无。"""
        return None
