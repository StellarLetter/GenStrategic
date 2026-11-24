"""AI 座位驱动：把 `Observation` 喂给策略，把 `Action` 回灌房间。

契约见 `docs/AI_API.md` §1 与 `docs/INTERFACES.md` §3。

流程（每个 Tick 之前）::

    obs = ai.api.to_observation(state, player_id)
    action = agent.act(obs)              # 异常 → 视为本 Tick 不操作
    room.handle(seat, {"t": "commands", "cmds": [action.to_command()]})

约束
----
- **绝不同步阻塞事件循环**：策略可能是进程外 HTTP 客户端（`ai.remote_client`），
  它在内部用 `asyncio.to_thread`；本模块也提供 `async def tick_async()` 供远程策略使用。
- AI 看到的视野与人类完全一致（`ai.api.to_observation` 内部调 `game.view.project`）。
- `Action` 一律经 `Room.handle()` 走与人类相同的校验路径，**没有作弊通道**（AI_API.md 开头）。
- 本模块负责把策略的**调试日志**（`generals.ai.log`）、本刻动作、异常喂给房间的回放录制器
  （`docs/REPLAY.md`）；未录制回放时这些调用都是空操作。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Final

from generals.game.state import GameState

from . import api, builtin  # noqa: F401  - builtin 的 import 触发策略注册（register 装饰器）
from . import debug as ai_debug
from .api import Action, Agent, GameSpec, Observation, Row
from .registry import get

__all__ = [
    "AIRunner",
    "make_agent",
    "spec_from_state",
]

#: 单个策略 `act()` 的软超时（秒）。超时按「本 Tick 不操作」处理，避免拖住 Tick 时钟。
ACT_TIMEOUT: Final[float] = 1.0


def spec_from_state(state: GameState, *, tick_ms: int = 500) -> GameSpec:
    """由权威状态构造 `GameSpec`（`Agent.reset()` 的入参）。

    前置：`state` 非空。
    后置：`players` 按 `player_id` 升序；`player_count = len(players)`。
    抛出：无。
    """
    rows = tuple(
        Row(
            player_id=pid,
            name=state.players[pid].name,
            color=state.players[pid].color,
            army=int(state.army_total(pid)),
            land=int(state.land_count(pid)),
            alive=bool(state.players[pid].alive),
        )
        for pid in sorted(state.players)
    )
    return GameSpec(size=state.size, player_count=len(rows), players=rows, tick_ms=tick_ms)


def make_agent(name: str, *args: Any, **kwargs: Any) -> Agent:
    """按注册名实例化一个策略（每个座位一个独立实例，避免状态互串）。

    前置：`name` 已通过 `ai.registry.register` 注册。
    后置：返回新实例；若构造器需要参数，经 `*args` / `**kwargs` 传入。
    抛出：`KeyError` —— 未注册的名字。
    """
    cls = get(name)
    return cls(*args, **kwargs)


@dataclass
class AIRunner:
    """一名 AI 玩家的驱动（INTERFACES §3 / BRIEF 成员 B §8）。

    前置：
    - `room` 已 `start()` 且 `room.state` 非空；
    - `seat` 是该 AI 在 `room` 里的座位（`seat.player_id == player_id`）。
    后置：`step()` / `step_async()` 每次只提交**一条**指令（R4.7 每刻 1 条）。
    抛出：构造不抛；`step()` 内部异常被吞掉（视为本 Tick 不操作），但会记在 `last_error` 里。
    """

    room: Any
    player_id: int
    agent: Agent

    def __post_init__(self) -> None:
        self.last_error: BaseException | None = None
        self.last_action: Action | None = None
        #: 该 runner 驱动的策略名（`Room` 用它判断「策略换了要不要重建实例」）。
        self.strategy_name: str | None = getattr(self.agent, "name", None)
        #: 该 runner 绑定的座位序号（`Room` 侧排错用）。
        self.seat_seq: int | None = None
        self._started = False
        self._seat = None

    # ------------------------------------------------------------------
    @property
    def seat(self) -> Any:
        """该 AI 在房间里的 `Seat`（按 `player_id` 反查，惰性缓存）。"""
        if self._seat is not None and getattr(self._seat, "player_id", None) == self.player_id:
            return self._seat
        for seat in getattr(self.room, "seats", []):
            if seat.player_id == self.player_id:
                self._seat = seat
                return seat
        return None

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """调用策略的 `reset(spec)`（开局后一次）。

        前置：`room.state` 非空。
        后置：`agent.reset(GameSpec)` 已调用；`_started = True`。
        抛出：无（异常被捕获并记在 `last_error`）。
        """
        state = getattr(self.room, "state", None)
        if state is None or self._started:
            return
        try:
            with ai_debug.capture(self._log_sink()):
                self.agent.reset(
                    spec_from_state(state, tick_ms=getattr(self.room, "tick_ms", 500))
                )
        except Exception as exc:  # noqa: BLE001 - 策略实现方的 bug 不应拖垮对局
            self._record_error(exc)
        self._started = True

    # ------------------------------------------------------------------
    # 回放接线（docs/REPLAY.md §3）：日志 / 动作 / 异常都进本局回放
    # ------------------------------------------------------------------
    def _log_sink(self) -> Any:
        """构造绑到本座位录制器的日志 sink（无录制器时返回 `None` = 静默丢弃）。

        前置：无（`room` 可以是任意实现了 `record_ai_log` 的对象）。
        后置：返回 `(level, text) -> None`；`tick` 由房间按录制时的 `state.tick` 填。
        抛出：无。
        """
        record = getattr(self.room, "record_ai_log", None)
        if not callable(record):
            return None

        def sink(level: str, text: str) -> None:
            record(
                self.player_id,
                text,
                level=level,
                seat=self.seat_seq,
                strategy=self.strategy_name,
            )

        return sink

    def _record_error(self, error: Any) -> None:
        """把异常记为 `last_error` 并写进回放（`kind="error"`）。前置/后置：无；抛出：无。"""
        self.last_error = error if isinstance(error, BaseException) else Exception(str(error))
        record = getattr(self.room, "record_ai_error", None)
        if callable(record):
            try:
                record(self.player_id, error, seat=self.seat_seq, strategy=self.strategy_name)
            except Exception:  # noqa: BLE001 - 回放故障不影响对局
                pass

    def _record_action(self, command: dict) -> None:
        """把本刻动作（已量化为线协议指令）写进回放（`kind="action"`）。抛出：无。"""
        record = getattr(self.room, "record_ai_action", None)
        if callable(record):
            try:
                record(self.player_id, command, seat=self.seat_seq, strategy=self.strategy_name)
            except Exception:  # noqa: BLE001
                pass

    def observation(self) -> Observation | None:
        """构造当前观测（调用 `ai.api.to_observation`）。

        前置：`room.state` 非空。
        后置：返回 `Observation`；状态缺失时返回 `None`。
        抛出：`KeyError` —— `player_id` 不在局中（由 `act()` 捕获）。
        """
        state = getattr(self.room, "state", None)
        if state is None:
            return None
        return api.to_observation(state, self.player_id)

    # ------------------------------------------------------------------
    def step(self) -> Action | None:
        """同步推进一次决策（进程内策略）。

        前置：`room.state` 非空且 `not state.ended`。
        后置：
        - 调 `agent.act(obs)`，得到 `Action | None`；
        - 非 `None` 时立即 `room.handle(seat, {"t":"commands","cmds":[cmd]})`（指令到达即入队，R6.3）；
        - 返回本次动作（`None` 表示本 Tick 不操作）。
        抛出：无 —— 异常被吞并记在 `last_error`。
        """
        self.last_action = None
        state = getattr(self.room, "state", None)
        if state is None or state.ended:
            return None
        seat = self.seat
        if seat is None:
            return None
        if not self._started:
            self.reset()
        try:
            obs = api.to_observation(state, self.player_id)
            with ai_debug.capture(self._log_sink()):
                action = self.agent.act(obs)
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            return None
        if action is None:
            return None
        self.last_action = action
        try:
            command = action.to_command()
        except Exception as exc:  # noqa: BLE001 - 策略给了非法 Action：本刻不操作
            self._record_error(exc)
            return None
        self._record_action(command)
        try:
            self.room.handle(seat, {"t": "commands", "cmds": [command]})
        except Exception as exc:  # noqa: BLE001 - 房间/协议层拒绝：记录即可
            self._record_error(exc)
        return action

    async def step_async(self) -> Action | None:
        """异步版 `step()`：把可能阻塞的 `agent.act()` 放到线程池（进程外策略用）。

        前置：同 `step()`。
        后置：同 `step()`；区别是 `agent.act()` 在线程里执行，且受 `ACT_TIMEOUT` 软超时保护
              （超时则本 Tick 不操作，**不取消**底层线程，避免阻塞 httpx 连接池）。
              日志 sink 用 `ai.debug.capture()` 绑定在**当前上下文**：`asyncio.to_thread`
              会复制 context，因此线程里的 `generals.ai.log()` 也能写进本局回放。
        抛出：无。
        """
        self.last_action = None
        state = getattr(self.room, "state", None)
        if state is None or state.ended:
            return None
        seat = self.seat
        if seat is None:
            return None
        if not self._started:
            self.reset()
        try:
            obs = api.to_observation(state, self.player_id)
            with ai_debug.capture(self._log_sink()):
                action = await asyncio.wait_for(
                    asyncio.to_thread(self.agent.act, obs), timeout=ACT_TIMEOUT
                )
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            self._record_error(exc)
            return None
        if action is None:
            return None
        self.last_action = action
        try:
            command = action.to_command()
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            return None
        self._record_action(command)
        try:
            self.room.handle(seat, {"t": "commands", "cmds": [command]})
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
        return action

    def close(self) -> None:
        """释放策略资源（若策略实现了 `close()` 则调用）。前置/后置：无。抛出：无。"""
        with contextlib.suppress(Exception):
            closer = getattr(self.agent, "close", None)
            if callable(closer):
                closer()
