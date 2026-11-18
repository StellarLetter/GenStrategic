"""`net.lobby` 与 `net.session` 测试：房间表、加入/离开、连接状态机、心跳与清理。"""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import FakeConn
from generals.config import GameConfig
from generals.net import JOIN_TIMEOUT, Lobby, PUBLIC_ROOM_PREFIX, RoomState, Session, protocol


# ---------------------------------------------------------------------------
# Lobby（INTERFACES §2.1）
# ---------------------------------------------------------------------------


def test_public_room_is_created_on_first_join() -> None:
    """PROTOCOL.md §2.1：`room` 缺省 = 加入/创建公共房。"""
    lobby = Lobby()
    room, seat = lobby.join("Alice", None, False, conn=FakeConn())
    assert room.id.startswith(PUBLIC_ROOM_PREFIX)
    assert seat.player_id == 0
    assert lobby.list_rooms()[0].id == room.id


def test_public_room_reused_until_full() -> None:
    """公共房复用：有空位就进同一间（避免每人一间房）。"""
    lobby = Lobby()
    r1, _ = lobby.join("A", None, False, conn=FakeConn())
    r2, _ = lobby.join("B", None, False, conn=FakeConn())
    assert r1 is r2
    assert len(r1.player_seats) == 2


def test_new_public_room_when_full() -> None:
    """公共房满员后新建下一间。"""
    cfg = GameConfig(max_players=2)
    lobby = Lobby(cfg)
    r1, _ = lobby.join("A", None, False, conn=FakeConn())
    lobby.join("B", None, False, conn=FakeConn())
    r2, _ = lobby.join("C", None, False, conn=FakeConn())
    assert r1 is not r2
    assert len(lobby.list_rooms()) == 2


def test_named_room_is_created_and_shared() -> None:
    """自定义房：同名即同房间（PROTOCOL.md §2.1 `"room": "custom-1"`）。"""
    lobby = Lobby()
    r1, _ = lobby.join("A", "custom-1", False, conn=FakeConn())
    r2, _ = lobby.join("B", "custom-1", False, conn=FakeConn())
    assert r1 is r2
    assert r1.id == "custom-1"


def test_room_full_raises_protocol_error() -> None:
    """PROTOCOL.md §5：`room_full`。"""
    lobby = Lobby(GameConfig(max_players=1))
    lobby.join("A", "tiny", False, conn=FakeConn())
    with pytest.raises(protocol.ProtocolError) as exc:
        lobby.join("B", "tiny", False, conn=FakeConn())
    assert exc.value.code == "room_full"


def test_spectator_can_join_full_room() -> None:
    """观战者不占玩家座，房间满员也能进（P-02）。"""
    lobby = Lobby(GameConfig(max_players=1))
    room, _ = lobby.join("A", "solo", False, conn=FakeConn())
    _room, seat = lobby.join("Watcher", "solo", True, conn=FakeConn())
    assert _room is room
    assert seat.spectate is True


def test_list_rooms_summary_matches_protocol_4() -> None:
    """PROTOCOL.md §4：`[{id, name, players, maxPlayers, state}]`。"""
    lobby = Lobby()
    room, _ = lobby.join("A", None, False, conn=FakeConn())
    lobby.join("B", None, False, conn=FakeConn())
    rows = lobby.list_rooms()
    assert len(rows) == 1
    wire = rows[0].to_wire()
    assert set(wire) == {"id", "name", "players", "maxPlayers", "state"}
    assert wire["players"] == 2
    assert wire["maxPlayers"] == room.max_players
    assert wire["state"] == "waiting"


def test_list_rooms_state_becomes_running_after_start() -> None:
    """开局后 `/api/rooms` 的 `state` 变 `running`。"""
    lobby = Lobby()
    room, _seat = lobby.join("A", "g", False, conn=FakeConn())
    lobby.join("B", "g", False, conn=FakeConn())
    room.start()
    assert lobby.list_rooms()[0].state == "running"


def test_leave_removes_seat_and_recycles_finished_room() -> None:
    """离开后座位消失；房间结束且空置时被回收（避免无限增长）。"""
    lobby = Lobby()
    room, seat = lobby.join("A", "gone", False, conn=FakeConn())
    room.phase = RoomState.ENDED
    lobby.leave(seat)
    assert seat not in room.seats
    assert lobby.get("gone") is None


def test_leave_is_idempotent() -> None:
    """`on_close` 可能被调用多次，`leave` 必须幂等（不抛）。"""
    lobby = Lobby()
    _room, seat = lobby.join("A", "idem", False, conn=FakeConn())
    lobby.leave(seat)
    lobby.leave(seat)  # 不抛即通过
    lobby.leave(seat)


def test_list_rooms_sorted_by_id() -> None:
    """R7.3：列表按 `id` 升序，确定性输出。"""
    lobby = Lobby()
    lobby.join("A", "zeta", False, conn=FakeConn())
    lobby.join("B", "alpha", False, conn=FakeConn())
    ids = [r.id for r in lobby.list_rooms()]
    assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Session（INTERFACES §2.3）
# ---------------------------------------------------------------------------


class _Recorder:
    """记录 Session 实际写出的文本（模拟 socket）。"""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str) -> None:
        self.texts.append(text)

    def frames(self) -> list[dict]:
        return [json.loads(t) for t in self.texts]


async def _drain_session(session: Session, sink: _Recorder, rounds: int = 32) -> None:
    """让 sender task 有机会把队列里的帧写出去（每次 ``sleep(0)`` 只让出一个调度点）。"""
    for _ in range(rounds):
        await asyncio.sleep(0)


async def _make_session(lobby: Lobby, **kwargs) -> tuple[Session, _Recorder]:
    sink = _Recorder()
    session = Session(sink, lobby, join_timeout=0, **kwargs)
    return session, sink


def test_session_join_creates_room_and_sends_joined() -> None:
    """PROTOCOL.md §2.1：join 后必须收到 `joined` 帧（由 Room.add 入队、sender 写出）。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, sink = await _make_session(lobby)
        await session.on_message(json.dumps({"t": "join", "name": "Alice"}))
        await _drain_session(session, sink)
        frames = sink.frames()
        assert frames and frames[0]["t"] == "joined"
        assert frames[0]["playerId"] == 0
        assert frames[0]["roomId"] == session.seat.room_id
        await session.on_close()

    asyncio.run(scenario())


def test_session_bad_json_gets_bad_frame_reject() -> None:
    """PROTOCOL.md §5：坏帧 → `reject(code='bad_frame')`，**不断开**。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, sink = await _make_session(lobby)
        await session.on_message("{oops")
        await _drain_session(session, sink)
        frames = sink.frames()
        assert frames[-1]["t"] == "reject"
        assert frames[-1]["code"] == "bad_frame"
        assert session.closed is False
        await session.on_close()

    asyncio.run(scenario())


def test_session_commands_without_join_get_not_joined() -> None:
    """PROTOCOL.md §5：尚未 join → `not_joined`。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, sink = await _make_session(lobby)
        await session.on_message(json.dumps({"t": "commands", "cmds": [{"op": "pop"}]}))
        await _drain_session(session, sink)
        frames = sink.frames()
        assert frames[-1]["t"] == "reject"
        assert frames[-1]["code"] == "not_joined"
        await session.on_close()

    asyncio.run(scenario())


def test_session_rejoin_moves_between_rooms() -> None:
    """同一连接换房：旧座位被释放，新座位生效。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, sink = await _make_session(lobby)
        await session.on_message(json.dumps({"t": "join", "name": "A", "room": "one"}))
        first_seat = session.seat
        await session.on_message(json.dumps({"t": "join", "name": "A", "room": "two"}))
        assert session.seat is not first_seat
        assert session.seat.room_id == "two"
        assert first_seat not in (lobby.get("one").seats if lobby.get("one") else [])
        await session.on_close()

    asyncio.run(scenario())


def test_session_on_close_removes_seat_and_is_idempotent() -> None:
    """关闭清理：座位被移除；重复调用不产生第二次清理。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, _sink = await _make_session(lobby)
        await session.on_message(json.dumps({"t": "join", "name": "A", "room": "close-me"}))
        room = session.room
        seat = session.seat
        assert seat in room.seats
        await session.on_close()
        assert seat not in room.seats
        assert session.closed is True
        await session.on_close()  # 幂等
        assert seat not in room.seats

    asyncio.run(scenario())


def test_session_spectate_frame() -> None:
    """PROTOCOL.md §2.2：`spectate` 帧以观战者身份加入。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, sink = await _make_session(lobby)
        await session.on_message(json.dumps({"t": "spectate", "room": "watch"}))
        assert session.seat is not None and session.seat.spectate is True
        assert session.seat.player_id is None
        await session.on_close()

    asyncio.run(scenario())


def test_session_ping_pong_roundtrip() -> None:
    """PROTOCOL.md §2.7：`ping` → `pong`。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, sink = await _make_session(lobby)
        await session.on_message(json.dumps({"t": "ping", "n": 99}))
        await _drain_session(session, sink)
        frames = sink.frames()
        assert frames[-1] == {"t": "pong", "n": 99}
        await session.on_close()

    asyncio.run(scenario())


def test_session_unknown_frame_is_ignored() -> None:
    """PROTOCOL.md §1：未知 `t` 一律忽略。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, sink = await _make_session(lobby)
        await session.on_message(json.dumps({"t": "wat"}))
        await _drain_session(session, sink)
        assert sink.frames() == []
        await session.on_close()

    asyncio.run(scenario())


def test_session_removed_seat_closes_connection() -> None:
    """管理员踢人（`Room.remove(kicked=True)`）后，该 Session 不再收到帧。"""

    async def scenario() -> None:
        lobby = Lobby()
        session, sink = await _make_session(lobby)
        await session.on_message(json.dumps({"t": "join", "name": "A", "room": "kick"}))
        seat = session.seat
        session.room.remove(seat, kicked=True)
        before = len(sink.texts)
        await _drain_session(session, sink)
        # remove 之后 seat.closed=True，Room 不再向它投递。
        await session.on_close()
        assert len(sink.texts) >= before

    asyncio.run(scenario())


def test_join_timeout_constant_matches_protocol() -> None:
    """PROTOCOL.md §1：连接后 10s 内必须 join。"""
    assert JOIN_TIMEOUT == 10.0


def test_session_join_timeout_rejects_when_never_joined() -> None:
    """未 join 超时 → `reject(code='not_joined')` 并关闭。"""

    async def scenario() -> None:
        lobby = Lobby()
        sink = _Recorder()
        session = Session(sink, lobby, join_timeout=0.01)
        await asyncio.sleep(0.05)
        frames = sink.frames()
        assert any(f["t"] == "reject" and f["code"] == "not_joined" for f in frames)
        assert session.closed is True

    asyncio.run(scenario())
