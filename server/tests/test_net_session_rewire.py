"""P0 回归测试：`attach_queue()` 换队列时必须唤醒**已阻塞**的 sender task。

历史缺陷（2026-11-18，e2e 发现）：`Session._sender_loop` 先在 `self.queue` 上
`await queue.get()` 阻塞，之后 `attach_queue()` 只重新赋值 `self.queue`；
旧队列上的 `get()` 永不返回 → `joined`/`game_start`/`tiles` 全部堆在座位队列里，
socket 一个字节也收不到，且连接不会断开（房间在 `/api/rooms` 里却是存在的）。

单层单测没抓到，是因为测试里 `Session(...)` 之后立刻 `join`，
sender task 还没被调度到 `get()`，第一次运行时读到的已经是新队列。
本文件**强制先让 sender 跑起来并阻塞**，再换队列。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import FakeConn
from generals.net import Lobby, Session


class _Recorder:
    """记录 Session 实际写出的文本（模拟 socket）。"""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str) -> None:
        self.texts.append(text)

    def frames(self) -> list[dict]:
        return [json.loads(t) for t in self.texts]


async def _let_sender_block(rounds: int = 5) -> None:
    """让 sender task 跑到 `await queue.get()` 并稳定阻塞在旧队列上。"""
    for _ in range(rounds):
        await asyncio.sleep(0)


async def _flush(rounds: int = 32) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


def test_attach_queue_wakes_sender_already_blocked_on_old_queue() -> None:
    """核心回归：sender 已阻塞 → 换队列 → 仍必须能把帧写出去。"""

    async def scenario() -> None:
        sink = _Recorder()
        session = Session(sink, Lobby(), join_timeout=0)
        # 关键：先让 sender 真正跑到 `await self.queue.get()` 上阻塞。
        await _let_sender_block()
        assert session._sender_task is not None and not session._sender_task.done()

        # 换队列（真实场景里由 `Room.add` 触发），然后往新队列塞一帧。
        new_queue: asyncio.Queue = asyncio.Queue(64)
        session.attach_queue(new_queue)
        await new_queue.put('{"t": "joined", "playerId": 0}')

        await _flush()
        assert sink.frames() == [{"t": "joined", "playerId": 0}], (
            "attach_queue 没有唤醒阻塞中的 sender：帧永远到不了 socket"
        )
        await session.on_close()

    asyncio.run(scenario())


def test_attach_queue_migrates_frames_pending_in_old_queue() -> None:
    """换队列时旧队列里未写出的帧必须搬到新队列（不能丢帧）。"""

    async def scenario() -> None:
        sink = _Recorder()
        session = Session(sink, Lobby(), join_timeout=0)
        await _let_sender_block()

        # 直接往旧队列塞两帧（模拟 `send()` 已入队但 sender 尚未写出）。
        session.queue.put_nowait('{"t": "a"}')
        session.queue.put_nowait('{"t": "b"}')

        new_queue: asyncio.Queue = asyncio.Queue(64)
        session.attach_queue(new_queue)
        await new_queue.put('{"t": "c"}')

        await _flush()
        assert sink.frames() == [{"t": "a"}, {"t": "b"}, {"t": "c"}]
        await session.on_close()

    asyncio.run(scenario())


def test_join_after_sender_blocked_still_delivers_joined() -> None:
    """端到端同构：ping（回 pong）→ 等 sender 阻塞 → join → 必须收到 `joined`。

    这正是 lead 给出的复现步骤：ping 先让 sender 跑过一轮，join 换队列后帧就断了。
    """

    async def scenario() -> None:
        lobby = Lobby()
        sink = _Recorder()
        session = Session(sink, lobby, join_timeout=0)
        await _let_sender_block()

        await session.on_message(json.dumps({"t": "ping", "n": 7}))
        await _flush()
        assert {"t": "pong", "n": 7} in sink.frames()

        await session.on_message(json.dumps({"t": "join", "name": "Alice", "room": "dbg"}))
        await _flush()
        kinds = [f["t"] for f in sink.frames()]
        assert "joined" in kinds, f"join 之后 joined 未送达，实际帧序列：{kinds}"
        await session.on_close()

    asyncio.run(scenario())


def test_attach_queue_is_idempotent_and_non_destructive() -> None:
    """重复 attach 同一队列不应丢帧或死循环。"""

    async def scenario() -> None:
        sink = _Recorder()
        session = Session(sink, Lobby(), join_timeout=0)
        await _let_sender_block()

        new_queue: asyncio.Queue = asyncio.Queue(64)
        session.attach_queue(new_queue)
        session.attach_queue(new_queue)
        await new_queue.put('{"t": "x"}')

        await _flush()
        assert sink.frames() == [{"t": "x"}]
        await session.on_close()

    asyncio.run(scenario())


def test_room_add_propagates_transport_errors_from_attach_queue() -> None:
    """`Room.add` 只应吞 `AttributeError`；其它异常必须冒泡（不得掩盖传输层缺陷）。"""
    from conftest import make_room

    class _BrokenConn(FakeConn):
        def attach_queue(self, queue) -> None:
            raise RuntimeError("transport exploded")

    room = make_room("propagate")
    with pytest.raises(RuntimeError):
        room.add(_BrokenConn(), "Alice")


def test_room_add_tolerates_conn_without_attach_queue() -> None:
    """裸 `Connection`（只实现 send/close）必须照常加入。"""
    from conftest import make_room

    room = make_room("plain-conn")
    seat = room.add(FakeConn(), "Alice")
    assert seat.player_id == 0
    assert seat.drain_nowait()[0]["t"] == "joined"


def test_room_add_swallows_attribute_error_only() -> None:
    """`attach_queue` 内部抛 `AttributeError` 时按「形状不匹配」忽略，不影响加入。"""
    from conftest import make_room

    class _ShapedConn(FakeConn):
        def attach_queue(self, queue) -> None:
            raise AttributeError("no such attribute")

    room = make_room("attr")
    seat = room.add(_ShapedConn(), "Alice")
    assert seat.player_id == 0
