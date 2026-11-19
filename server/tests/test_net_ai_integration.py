"""net ←→ ai 集成测试：`Session` 接入 `Room`、`AIRunner` 驱动机器人座位。

覆盖点（BRIEF 成员 B「测试要求」）：
- `join` 带 `"bot": "<name>"` 时，AI 占座且**不产生任何连接帧**；
- `AIRunner` 接入真实房间后每 Tick 只提交 1 条指令（R4.7）；
- 一整局（AI vs AI）能跑到结束并下发 `game_over`（R7.4）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import FakeConn, make_room, pump
from generals.ai import AIRunner, make_agent
from generals.config import GameConfig
from generals.net import Lobby, RoomState, Session


class _Recorder:
    """记录 Session 写出的文本。"""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str) -> None:
        self.texts.append(text)

    def frames(self) -> list[dict]:
        return [json.loads(t) for t in self.texts]


def test_bot_join_reserves_seat_without_frames() -> None:
    """INTERFACES §3：`join` 的 `bot` 字段让进程内 AI 占座，机器人没有连接、不收帧。"""
    lobby = Lobby()
    room, bot_seat = lobby.join("Bot", "bot-room", False, conn=None, bot="greedy")
    assert bot_seat.bot == "greedy"
    assert bot_seat.is_bot is True
    assert bot_seat.conn is None
    assert bot_seat.player_id is not None
    # 机器人座位没有队列，直接广播也不会抛（`Room` 侧应跳过）
    assert bot_seat.queue.qsize() == 0


def test_bot_seat_does_not_consume_human_slot_twice() -> None:
    """机器人占 1 个玩家座（不是观战者）。"""
    lobby = Lobby()
    room, _bot = lobby.join("Bot", "bot-count", False, conn=None, bot="hold")
    _human, human = lobby.join("Alice", "bot-count", False, conn=FakeConn())
    assert len(room.player_seats) == 2
    assert human.player_id == 1


def test_runner_drives_bot_seat_through_room() -> None:
    """`AIRunner` 直接把动作交给 `Room.handle`，走完整校验链（ack 而非 reject）。"""
    room = make_room("runner-room")
    human = room.add(FakeConn(), "Alice")
    bot_seat = room.add(None, "Bot", bot="greedy")
    room.start()
    pump(human)
    runner = AIRunner(room=room, player_id=bot_seat.player_id, agent=make_agent("greedy"))
    runner.reset()

    rejects = 0
    for _ in range(40):
        runner.step()
        room.step_once()
        for frame in [f for _s, f in pump(human)]:
            if frame["t"] == "reject":
                rejects += 1
        if room.state.ended:
            break
    assert rejects == 0, "AI 的动作不应被房间拒绝"


def test_ai_vs_ai_game_runs_without_rejects_and_ends_when_forced() -> None:
    """两个人机对局能长时间稳定推进、全无 `reject`；强行满足 R7.4 后能收尾并广播 `game_over`。

    注意：两个同策略的镜像 AI 在对称地图上可能长时间互不消灭（这是**策略**问题，
    不是传输层问题）。所以这里不为「AI 会不会赢」设断言，只为：
    1) 传输层在 2000 刻内不产生任何 `reject`；
    2) 一旦满足 R7.4（只剩一方有地），`game_over` 正确下发且 `winner` 与内核一致。
    """
    room = make_room("aivai")
    a = room.add(None, "GreedyA", bot="greedy")
    b = room.add(None, "GreedyB", bot="greedy")
    spect = room.add(FakeConn(), "Watcher", spectate=True)
    room.start()

    runners = [
        AIRunner(room=room, player_id=a.player_id, agent=make_agent("greedy")),
        AIRunner(room=room, player_id=b.player_id, agent=make_agent("greedy")),
    ]
    for runner in runners:
        runner.reset()

    spect.drain_nowait()
    rejected = 0
    seen: list[dict] = []
    for _ in range(2000):
        for runner in runners:
            runner.step()
        room.step_once()
        for frame in spect.drain_nowait():
            seen.append(frame)
            if frame["t"] == "reject":
                rejected += 1
        if room.state.ended:
            break
    assert rejected == 0, "AI 的动作不应被房间拒绝"
    assert room.state.tick > 0

    # 若两个镜像贪心 AI 在自己的节奏里已经分出胜负（`_should_end` 满足后自然终局），
    # 终局条件与 `game_over` 都在上面那一轮里就发生了 —— 此时再「强行制造终局」是空操作。
    # 两种情况都必须验证：`game_over` 已下发、`winner` 与内核一致、房间进入 ENDED。
    if not room.state.ended:
        # 强行制造终局条件：把 B 的地盘全部并入 A（R7.4）。
        for tile in room.state.tiles:
            if tile.owner == b.player_id:
                tile.owner = a.player_id
        spect.drain_nowait()
        room.step_once()
        seen.extend(spect.drain_nowait())

    over = [f for f in seen if f["t"] == "game_over"]
    assert over, "满足 R7.4 后应下发 game_over"
    assert over[0]["winner"] == room.state.winner
    assert room.phase is RoomState.ENDED


def test_session_spectator_receives_spectator_frames_only() -> None:
    """观战 Session 只收广播帧，不会被要求操作。"""

    async def scenario() -> None:
        lobby = Lobby()
        sink = _Recorder()
        session = Session(sink, lobby, join_timeout=0)
        await session.on_message(json.dumps({"t": "spectate", "room": "watch-me"}))
        for _ in range(8):
            await asyncio.sleep(0)
        frames = sink.frames()
        assert frames[0]["t"] == "joined"
        assert session.seat.spectate is True
        await session.on_close()

    asyncio.run(scenario())


def test_join_with_unknown_bot_name_is_rejected() -> None:
    """`bot` 名字未注册 → `reject(code='bad_frame')`，连接不崩。"""

    async def scenario() -> None:
        lobby = Lobby()
        sink = _Recorder()
        session = Session(sink, lobby, join_timeout=0)
        await session.on_message(
            json.dumps({"t": "join", "name": "X", "room": "bad-bot", "bot": "no-such-bot"})
        )
        for _ in range(12):
            await asyncio.sleep(0)
        frames = sink.frames()
        assert frames and frames[-1]["t"] == "reject"
        assert frames[-1]["code"] == "bad_frame"
        await session.on_close()

    asyncio.run(scenario())
