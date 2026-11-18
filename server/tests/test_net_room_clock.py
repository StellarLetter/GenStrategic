"""`net.room.Room.run()` 的 asyncio 时钟测试（真实事件循环）。

覆盖点：
- `run()` 按 `tick_ms` 推进并在结束后自行返回；
- `cancel()` 时按约定向上传播 `CancelledError`；
- 慢客户端不会拖住 Tick 时钟（其它座位照常收帧）。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from conftest import FakeConn, make_room
from generals.config import GameConfig
from generals.net import Room, RoomState, Seat


def _fast_config(**kwargs) -> GameConfig:
    """把刻长压到 1ms，让测试跑得快（不改变任何规则）。"""
    base = {"tick_ms": 1}
    base.update(kwargs)
    return GameConfig(**base)


class _DrainingConn(FakeConn):
    """模拟一个健康客户端：后台持续把座位队列里的帧取走。"""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict] = []
        self._task: asyncio.Task | None = None

    async def pump_forever(self, seat: Seat) -> None:
        while not self.closed:
            try:
                text = seat.queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0)
                continue
            self.frames.append(json.loads(text))


def test_start_autostarts_clock_in_running_loop() -> None:
    """`start()` 必须自动拉起 Tick 时钟（回归：e2e 曾发现没人调 `run()`，房间永远 Turn 0）。"""

    async def scenario() -> None:
        room = Room("auto", config=_fast_config(), size=10)
        a = room.add(FakeConn(), "Alice")
        b = room.add(FakeConn(), "Bob")
        room.start()
        # 开局后时钟任务应当已经在跑。
        assert room._loop_task is not None and not room._loop_task.done()

        async def drain(seat: Seat) -> None:
            while not seat.queue.empty():
                seat.queue.get_nowait()

        deadline = time.monotonic() + 5.0
        while room.state is not None and room.state.tick < 10 and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
            await drain(a)
            await drain(b)
        assert room.state is not None and room.state.tick >= 10, "start() 没有自动起时钟"
        room.close()
        await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_start_without_loop_does_not_crash() -> None:
    """同步（无事件循环）调用 `start()` 不应抛 —— 交由 `step_once()` 手动推进。"""
    room = Room("sync", size=10)
    room.add(FakeConn(), "Alice")
    room.add(FakeConn(), "Bob")
    room.start()  # 无 loop，`_ensure_clock` 静默跳过
    assert room._loop_task is None
    tick_before = room.state.tick
    room.step_once()
    assert room.state.tick == tick_before + 1


def test_run_advances_ticks_and_returns_when_ended() -> None:
    """`run()` 用 asyncio 每 `tick_ms` 推 1 Tick，结束时自行返回（INTERFACES §2.2）。"""

    async def scenario() -> None:
        room = Room("clock", config=_fast_config(), size=10)
        a = room.add(FakeConn(), "Alice")
        b = room.add(FakeConn(), "Bob")
        room.start()

        async def drain(seat: Seat) -> None:
            while not seat.queue.empty():
                seat.queue.get_nowait()

        task = asyncio.create_task(room.run())
        deadline = time.monotonic() + 5.0
        while room.state is not None and room.state.tick < 30 and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
            await drain(a)
            await drain(b)
        assert room.state is not None and room.state.tick >= 30, "run() 没有推进 Tick"
        room.state.ended = True  # 让 run() 自然收敛并返回
        await asyncio.wait_for(task, timeout=5.0)
        assert room.phase is RoomState.ENDED

    asyncio.run(scenario())


def test_run_cancel_propagates_cancelled_error() -> None:
    """取消 `run()` 时必须向上传播 `CancelledError`（调用方负责收尾）。"""

    async def scenario() -> None:
        room = Room("cancel", config=_fast_config(), size=10)
        room.add(FakeConn(), "Alice")
        room.add(FakeConn(), "Bob")
        room.start()
        task = asyncio.create_task(room.run())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_run_starts_room_if_still_waiting() -> None:
    """`run()` 在等待阶段会先自行 `start()`（INTERFACES §2.2 的可重入语义）。"""

    async def scenario() -> None:
        room = Room("autorun", config=_fast_config(), size=10)
        a = room.add(FakeConn(), "Alice")
        b = room.add(FakeConn(), "Bob")
        assert room.phase is RoomState.WAITING
        task = asyncio.create_task(room.run())
        await asyncio.sleep(0.01)
        assert room.phase is RoomState.RUNNING
        assert room.state is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        a.drain_nowait()
        b.drain_nowait()

    asyncio.run(scenario())


def test_slow_client_does_not_stall_clock() -> None:
    """一个不消费队列的慢客户端被踢掉后，其它座位继续正常收帧。"""

    async def scenario() -> None:
        room = Room("stall", config=_fast_config(), size=10)
        slow = room.add(FakeConn(), "Slow")       # 永不消费
        fast = room.add(FakeConn(), "Fast")
        room.start()

        received: list[dict] = []

        async def fast_pump() -> None:
            while not fast.closed:
                try:
                    received.append(json.loads(fast.queue.get_nowait()))
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0)
                    continue

        pump_task = asyncio.create_task(fast_pump())
        run_task = asyncio.create_task(room.run())
        await asyncio.sleep(0.3)
        run_task.cancel()
        pump_task.cancel()
        # 两个任务可能都在 cancel 之前就自然结束了（`fast_pump` 在 seat 被踢后退出、
        # `run` 在房间结束后返回），因此只断言「不再运行」，不强制要求 CancelledError。
        for task in (run_task, pump_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert slow not in room.seats, "慢客户端应被踢出"
        assert any(f["t"] == "turn" for f in received), "健康客户端应持续收到 turn 帧"

    asyncio.run(scenario())


def test_room_close_cancels_run_task() -> None:
    """`Room.close()` 停止时钟并关闭所有座位（幂等）。"""

    async def scenario() -> None:
        room = Room("close", config=_fast_config(), size=10)
        room.add(FakeConn(), "Alice")
        room.add(FakeConn(), "Bob")
        room.start()
        room._loop_task = asyncio.create_task(room.run())
        await asyncio.sleep(0.01)
        room.close()
        room.close()
        assert room.phase is RoomState.ENDED
        assert all(seat.closed for seat in room.seats)
        await asyncio.sleep(0.01)

    asyncio.run(scenario())
