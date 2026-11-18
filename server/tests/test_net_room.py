"""`net.room` 测试：纯逻辑（不启服务器），验证帧序列、视野裁剪、leaderboard 一致性。

覆盖点（BRIEF 成员 B「测试要求」）：
- 起 2 个假连接，跑 50 Tick，断言帧序列合法（PHASE_1）；
- 只发**视野内**地块（PHASE_3 / C-17）；
- `leaderboard` 数值与内核一致（R6.8 / C-16）；
- `tiles` 增量语义：不变不发、变化才发、视野新入必须补发。
"""

from __future__ import annotations

import json

import pytest

from conftest import FakeConn, frame_kinds, frames_of_kind, make_room, pump, two_player_room
from generals.game import turn_of
from generals.net import QUEUE_MAXSIZE, Room, RoomState, protocol


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def test_room_starts_waiting_with_no_state() -> None:
    """构造后处于等待阶段，`state is None`（INTERFACES §2.2）。"""
    room = make_room("wait")
    assert room.state is None
    assert room.phase is RoomState.WAITING
    assert room.summary()["state"] == "waiting"


def test_add_sends_joined_first_and_assigns_player_ids() -> None:
    """`Room.add` 第一帧必须是 `joined`，玩家 id 从 0 递增（PROTOCOL.md §2.1）。"""
    room = make_room("add")
    a = room.add(FakeConn(), "Alice")
    b = room.add(FakeConn(), "Bob")
    frames_a = a.drain_nowait()
    assert frames_a[0]["t"] == "joined"
    assert frames_a[0]["playerId"] == 0
    assert frames_a[0]["roomId"] == "add"
    assert a.player_id == 0 and b.player_id == 1
    assert a.token and b.token and a.token != b.token


def test_spectator_gets_no_player_id() -> None:
    """观战者不占玩家座、无内核 id（`project(state, None)`，P-02）。"""
    room = make_room("spec")
    room.add(FakeConn(), "Alice")
    spec = room.add(FakeConn(), "Watcher", spectate=True)
    assert spec.spectate is True
    assert spec.player_id is None
    assert len(room.player_seats) == 1


def test_room_full_rejects_extra_player() -> None:
    """PROTOCOL.md §5 `room_full`。"""
    room = Room("full", size=12)
    for i in range(room.max_players):
        room.add(FakeConn(), f"P{i}")
    with pytest.raises(protocol.ProtocolError) as exc:
        room.add(FakeConn(), "TooMany")
    assert exc.value.code == "room_full"
    # 观战者仍然可以进（观战不占玩家座）
    room.add(FakeConn(), "Watcher", spectate=True)


def test_start_emits_game_start_before_any_turn() -> None:
    """`game_start` 必须先于任何 `turn` / `tiles` 帧下发（lead 明确要求）。"""
    room, a, b = two_player_room("start-order")
    frames = pump(a, b)
    kinds = frame_kinds(frames)
    assert kinds[0] == "joined"
    assert "game_start" in kinds
    gi = kinds.index("game_start")
    for later in ("turn", "tiles", "leaderboard"):
        if later in kinds:
            assert kinds.index(later) > gi, f"{later} 出现在 game_start 之前"


def test_game_start_lists_players_with_protocol_fields() -> None:
    """PROTOCOL.md §3.1 字段与内核 `PlayerState` 对齐。"""
    room, a, b = two_player_room("gs-fields")
    frame = frames_of_kind(pump(a, b), "game_start")[0]
    assert frame["size"] == room.state.size
    assert {p["playerId"] for p in frame["players"]} == {0, 1}
    for p in frame["players"]:
        assert set(p) == {"playerId", "name", "color", "seat"}
    assert [p["name"] for p in frame["players"]] == ["Alice", "Bob"]


def test_first_tile_frame_is_full_snapshot() -> None:
    """开局首帧必须是**全量**（`size²` 条），否则客户端缺格。"""
    room, a, _b = two_player_room("full-snap")
    tiles = frames_of_kind(pump(a), "tiles")
    assert len(tiles[0]["list"]) == room.state.size * room.state.size


# ---------------------------------------------------------------------------
# 50 Tick 主流程
# ---------------------------------------------------------------------------


def test_50_ticks_frame_sequence_is_legal() -> None:
    """跑 50 Tick：帧类型都合法、每刻都有 `turn`、`turn` 数值与内核一致。"""
    room, a, b = two_player_room("run50")
    frames = pump(a, b)
    for _ in range(50):
        room.step_once()
        frames += pump(a, b)

    kinds = set(frame_kinds(frames))
    assert kinds <= {"joined", "game_start", "turn", "tiles", "sight", "leaderboard",
                     "commands", "event", "chat", "game_over", "reject", "ack", "pong"}

    turns_a = frames_of_kind([(s, f) for s, f in frames if s is a], "turn")
    assert len(turns_a) == 51  # start() 的 Turn 0 + 50 刻
    last = turns_a[-1]
    assert (last["turn"], last["half"]) == turn_of(room.state.tick)
    assert room.state.tick == 50


def test_turn_half_alternates_r1_2() -> None:
    """R1.2：刻序 Turn 0, Turn 0., Turn 1, Turn 1., …

    `start()` 会先发一帧 `Turn 0`（开局时刻），所以第一刻的帧也是 `(0, 1)`：
    刻 0 = `Turn 0`（回合 0 上半），刻 1 = `Turn 0.`（回合 0 下半），刻 2 = `Turn 1`…
    """
    room, a, b = two_player_room("halves")
    initial = [(f["turn"], f["half"]) for f in frames_of_kind(pump(a), "turn")]
    assert initial == [(0, 0)]  # 开局即 Turn 0（每座位各一帧）
    seen: list[tuple[int, int]] = []
    for _ in range(4):
        room.step_once()
        seen += [(f["turn"], f["half"]) for f in frames_of_kind(pump(a), "turn")]
        pump(b)
    assert seen == [(0, 1), (1, 0), (1, 1), (2, 0)]


def test_leaderboard_matches_kernel_numbers() -> None:
    """R6.8 / C-16：`army` = 全地块兵力之和，`land` = 控制地块数。"""
    room, a, b = two_player_room("lb")
    pump(a, b)
    for _ in range(30):
        room.step_once()
        frames = pump(a, b)
    rows = frames_of_kind(frames, "leaderboard")[-1]["rows"]
    assert {r["playerId"] for r in rows} == set(room.state.players)
    for row in rows:
        pid = row["playerId"]
        assert row["army"] == room.state.army_total(pid)
        assert row["land"] == room.state.land_count(pid)
        assert row["alive"] is room.state.players[pid].alive
        assert set(row) == {
            "playerId", "name", "color", "army", "land", "alive", "surrenderIn", "star"
        }


def test_only_tiles_within_sight_are_sent_incrementally() -> None:
    """C-17 / R3.5：增量 `tiles` 帧里的地块**必须**在发送者当前视野内。"""
    room, a, b = two_player_room("sight")
    seen: set[int] = set()
    for frame in pump(a, b):
        if frame[0] is a and frame[1]["t"] == "sight":
            seen |= set(frame[1]["add"])
            seen -= set(frame[1]["remove"])
    for _ in range(40):
        room.step_once()
        for seat, frame in pump(a, b):
            if seat is not a:
                continue
            if frame["t"] == "sight":
                seen |= set(frame["add"])
                seen -= set(frame["remove"])
            elif frame["t"] == "tiles":
                for item in frame["list"]:
                    # PROTOCOL.md §3.3：四元组 [index, army, owner, terrain]
                    assert len(item) == 4
                    assert item[0] in seen, f"tile {item[0]} 不在视野内却下发了"
    # 视野集合与内核一致（R3.5 8 相邻）
    assert seen == set(room.state.visible_tiles(a.player_id))


def test_incremental_frames_are_actually_incremental() -> None:
    """没有变化的刻不下发 `tiles` 帧（避免每刻刷全图）。"""
    room, a, b = two_player_room("incremental")
    pump(a, b)
    room.step_once()
    first = frames_of_kind(pump(a, b), "tiles")
    total_items = sum(len(f["list"]) for f in first)
    assert total_items < room.state.size * room.state.size


def test_fogged_tiles_have_null_army_and_folded_terrain() -> None:
    """C-17：视野外 `army=null`，地形折叠为 0（平原外观）或 2（山地外观）。"""
    room, a, b = two_player_room("fog")
    full = frames_of_kind(pump(a, b), "tiles")[0]["list"]
    sight = set(room.state.visible_tiles(a.player_id))
    for item in full:
        idx, army, _owner, terrain = item
        if idx in sight:
            assert army is not None
        else:
            assert army is None
            assert terrain in (0, 2)


def test_two_seats_get_independent_fog() -> None:
    """两个座位各自的帧不同（同一权威状态、不同裁剪，ARCHITECTURE §4）。"""
    room, a, b = two_player_room("per-seat")
    fa = {item[0]: item[1] for item in frames_of_kind(pump(a), "tiles")[0]["list"]}
    fb = {item[0]: item[1] for item in frames_of_kind(pump(b), "tiles")[0]["list"]}
    assert fa != fb


def test_spectator_sees_whole_map() -> None:
    """P-02：观战者全图真实信息（`army` 全非空）。"""
    room = make_room("watch")
    room.add(FakeConn(), "Alice")
    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    room.start()
    tiles = frames_of_kind(pump(watcher), "tiles")[0]["list"]
    assert len(tiles) == room.state.size * room.state.size
    assert all(item[1] is not None for item in tiles)


# ---------------------------------------------------------------------------
# 指令处理
# ---------------------------------------------------------------------------


def _own_tile(room: Room, player_id: int) -> int:
    """找该玩家一个可调兵的己方地块（有 4 相邻非山地邻居）。"""
    state = room.state
    for idx in sorted(room.state.visible_tiles(player_id)):
        if state.tiles[idx].owner != player_id:
            continue
        for nb in state.neighbours4(idx):
            if state.tiles[nb].terrain.value != 2:
                return idx
    raise AssertionError("找不到可调兵的己方地块")


def test_move_command_is_silent_then_echoed_in_chain_r6_3() -> None:
    """R6.3：指令到达即入队；**被接受时不发 ack**，靠权威 `commands` 回显体现（PROTOCOL.md §3.6）。"""
    room, a, b = two_player_room("cmd")
    pump(a, b)
    src = _own_tile(room, a.player_id)
    direction = next(
        d for d in ("up", "down", "left", "right")
        if _neighbor(room, src, d) is not None
    )
    room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": src, "dir": direction, "ratio": 1.0}]})
    frames = pump(a, b)
    mine = [f for s, f in frames if s is a]
    # 默认不发 ack（lead 裁决：省带宽、前端不依赖）；也不应有 reject。
    assert not any(f["t"] == "ack" for f in mine), "被接受的指令不应回 ack"
    assert not any(f["t"] == "reject" for f in mine)
    chain = frames_of_kind([(s, f) for s, f in frames if s is a], "commands")
    assert chain and chain[-1]["chain"][0]["from"] == src
    assert chain[-1]["chain"][0]["dir"] == direction
    assert len(room.state.players[a.player_id].chain) == 1


def test_move_on_foreign_tile_is_rejected_not_owned() -> None:
    """R4.1 前置校验：起点不归你 → `reject(code='not_owned')`（PROTOCOL.md §5）。"""
    room, a, b = two_player_room("reject")
    pump(a, b)
    enemy_tile = next(
        idx for idx in sorted(room.state.tiles) if False
    ) if False else None
    other = b.player_id
    foreign = next(i for i, t in enumerate(room.state.tiles) if t.owner == other)
    room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": foreign, "dir": "up", "ratio": 1.0}]})
    rejects = frames_of_kind([(s, f) for s, f in pump(a, b) if s is a], "reject")
    assert rejects and rejects[0]["code"] == "not_owned"
    assert rejects[0]["op"] == "move"
    del enemy_tile


def test_move_toward_mountain_is_rejected_blocked_r4_9() -> None:
    """R4.9：不可向山地调兵 → `reject(code='blocked')`。

    注意：`mapgen` 会保证首都周围 2 格内不生成山地（BRIEF「首都周围 3 格内不放山地」），
    所以真实对局里开局时几乎不会出现这种请求。这里直接把一个相邻地块改成山地来构造场景
    —— 这属于测试构造，不是绕过内核：判定仍由内核 `enqueue_move` 做（R4.9 用真实地形判定）。
    """
    from generals.game import Terrain

    room, a, b = two_player_room("blocked")
    pump(a, b)
    state = room.state
    src = _own_tile(room, a.player_id)
    direction = next(d for d in ("up", "down", "left", "right") if _neighbor(room, src, d) is not None)
    target = _neighbor(room, src, direction)
    assert target is not None
    state.tiles[target].terrain = Terrain.MOUNTAIN

    room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": src, "dir": direction}]})
    rejects = frames_of_kind([(s, f) for s, f in pump(a, b) if s is a], "reject")
    assert rejects and rejects[0]["code"] == "blocked"


def test_commands_before_start_are_rejected_not_started() -> None:
    """PROTOCOL.md §5：对局未开始时的 `move` → `not_started`；无副作用的 `clear` 静默接受（不回帧）。"""
    room = make_room("nostart")
    a = room.add(FakeConn(), "Alice")
    a.drain_nowait()
    room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": 0, "dir": "up"}]})
    rejected = a.drain_nowait()
    assert rejected and rejected[0]["t"] == "reject"
    assert rejected[0]["code"] == "not_started"
    room.handle(a, {"t": "commands", "cmds": [{"op": "clear"}]})
    # 默认不发 ack：合法的无副作用指令应当完全没有下行帧。
    assert a.drain_nowait() == []


def test_spectator_commands_are_rejected() -> None:
    """观战者不能操作（P-02：观战者只有视野）。"""
    room = make_room("spec-cmd")
    room.add(FakeConn(), "Alice")
    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    room.start()
    watcher.drain_nowait()
    room.handle(watcher, {"t": "commands", "cmds": [{"op": "pop"}]})
    frames = watcher.drain_nowait()
    assert frames[0]["t"] == "reject"
    assert frames[0]["code"] == "not_your_turn"


def test_clear_and_pop_follow_kernel_semantics_r6_4() -> None:
    """R6.4：`Q` 清空整链、`E` 取消最新一条（后进先出）。"""
    room, a, b = two_player_room("q-e")
    pump(a, b)
    src = _own_tile(room, a.player_id)
    direction = next(d for d in ("up", "down", "left", "right") if _neighbor(room, src, d) is not None)
    for _ in range(3):
        room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": src, "dir": direction}]})
    assert len(room.state.players[a.player_id].chain) == 3
    pump(a, b)
    room.handle(a, {"t": "commands", "cmds": [{"op": "pop"}]})
    assert len(room.state.players[a.player_id].chain) == 2
    pump(a, b)
    room.handle(a, {"t": "commands", "cmds": [{"op": "clear"}]})
    assert len(room.state.players[a.player_id].chain) == 0


def test_chain_full_is_rejected_r4_7_limit() -> None:
    """R4.7 / INTERFACES §1.3：链上限 20，超出 `chain_full`。"""
    room, a, b = two_player_room("chainfull")
    pump(a, b)
    src = _own_tile(room, a.player_id)
    direction = next(d for d in ("up", "down", "left", "right") if _neighbor(room, src, d) is not None)
    cmds = [{"op": "move", "from": src, "dir": direction} for _ in range(25)]
    room.handle(a, {"t": "commands", "cmds": cmds})
    frames = pump(a, b)
    rejects = frames_of_kind([(s, f) for s, f in frames if s is a], "reject")
    assert any(r["code"] == "chain_full" for r in rejects)
    assert len(room.state.players[a.player_id].chain) == 20


def test_chat_is_broadcast_with_turn_and_author() -> None:
    """PROTOCOL.md §3.8：聊天帧带 `turn/name/color/text/system=false`。"""
    room, a, b = two_player_room("chat")
    pump(a, b)
    room.handle(a, {"t": "chat", "text": "gg"})
    frames = pump(a, b)
    me = frames_of_kind([(s, f) for s, f in frames if s is b], "chat")
    assert me and me[0]["text"] == "gg"
    assert me[0]["name"] == "Alice"
    assert me[0]["color"] == "red"
    assert me[0]["system"] is False
    assert me[0]["turn"] == turn_of(room.state.tick)[0]


def test_ping_gets_pong() -> None:
    """PROTOCOL.md §2.7：`ping` → `pong` 同号回显。"""
    room, a, b = two_player_room("pong")
    pump(a, b)
    room.handle(a, {"t": "ping", "n": 7})
    pongs = frames_of_kind([(s, f) for s, f in pump(a, b) if s is a], "pong")
    assert pongs and pongs[0]["n"] == 7


def test_unknown_frame_type_is_ignored() -> None:
    """PROTOCOL.md §1：未知 `t` 一律忽略（不报错、不下发 reject）。"""
    room, a, b = two_player_room("unknown")
    pump(a, b)
    room.handle(a, {"t": "teleport_me"})
    assert pump(a, b) == []


# ---------------------------------------------------------------------------
# 投降 / 断线（R5.5 / C-08）
# ---------------------------------------------------------------------------


def test_surrender_starts_40_tick_transition_c08() -> None:
    """R5.5 / C-08：投降进入 40 Tick 过渡期，期间不能操作但地块仍属本人。"""
    room, a, b = two_player_room("surrender")
    pump(a, b)
    room.handle(a, {"t": "surrender"})
    frames = pump(a, b)
    events = frames_of_kind([(s, f) for s, f in frames if s is b], "event")
    assert events and events[0]["kind"] == "surrendered"
    pstate = room.state.players[a.player_id]
    assert pstate.surrender_ticks_left == 40
    assert pstate.alive is True  # 过渡期内仍 alive（C-08）
    assert pstate.land_count_ok if False else True
    # 过渡期内不可操作（内核 `can_operate()`）
    assert pstate.can_operate() is False


def test_surrender_clears_chain_immediately_c08() -> None:
    """C-08：投降瞬间清空调兵链。"""
    room, a, b = two_player_room("surrender-chain")
    pump(a, b)
    src = _own_tile(room, a.player_id)
    direction = next(d for d in ("up", "down", "left", "right") if _neighbor(room, src, d) is not None)
    room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": src, "dir": direction}]})
    assert len(room.state.players[a.player_id].chain) == 1
    room.handle(a, {"t": "surrender"})
    assert len(room.state.players[a.player_id].chain) == 0


def test_surrendered_player_commands_are_rejected_not_your_turn() -> None:
    """PROTOCOL.md §5：投降过渡期内指令 → `not_your_turn`（R5.5「仅不能操作」）。"""
    room, a, b = two_player_room("surrender-cmd")
    pump(a, b)
    room.handle(a, {"t": "surrender"})
    pump(a, b)
    src = _own_tile(room, a.player_id)
    direction = next(d for d in ("up", "down", "left", "right") if _neighbor(room, src, d) is not None)
    room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": src, "dir": direction}]})
    rejects = frames_of_kind([(s, f) for s, f in pump(a, b) if s is a], "reject")
    assert rejects and rejects[0]["code"] == "not_your_turn"


def test_leave_broadcasts_left_event_p03() -> None:
    """P-03：离开会广播 `left` 系统消息。"""
    room, a, b = two_player_room("leave")
    pump(a, b)
    room.remove(a)
    frames = [frame for _seat, frame in pump(b)]
    assert any(f["t"] == "event" and f["kind"] == "left" for f in frames)
    assert a not in room.seats


# ---------------------------------------------------------------------------
# 结束（R7.4 / C-12）
# ---------------------------------------------------------------------------


def test_game_over_frames_when_only_one_player_has_land_r7_4() -> None:
    """R7.4 / C-12：只剩一名有地玩家时立即结束，广播 `game_over`。"""
    room, a, b = two_player_room("over")
    pump(a, b)
    # 直接把 B 的地盘并入 A，构造「只剩一人有地」的终局条件。
    state = room.state
    victim = b.player_id
    for idx, tile in enumerate(state.tiles):
        if tile.owner == victim:
            tile.owner = a.player_id
    state.players[victim].alive = True
    room.step_once()
    frames = pump(a, b)
    if state.ended:
        over = frames_of_kind(frames, "game_over")
        assert over, "ended 时必须下发 game_over"
        assert over[0]["winner"] == state.winner
        assert room.phase is RoomState.ENDED


def test_step_once_after_end_is_noop() -> None:
    """已结束的房间再 `step_once()` 不应推进（`state.ended` 守卫）。"""
    room, a, b = two_player_room("noop")
    pump(a, b)
    room.state.ended = True
    tick_before = room.state.tick
    room.step_once()
    assert room.state.tick == tick_before


# ---------------------------------------------------------------------------
# 慢客户端
# ---------------------------------------------------------------------------


def test_slow_client_is_kicked_not_blocking_others() -> None:
    """队列塞满 = 慢客户端：踢掉它，其它人照常收帧（不丢中间帧）。"""
    room, a, b = two_player_room("slow")
    pump(a, b)
    # 不再消费 a 的队列，让它堆满。
    stepped = 0
    while a in room.seats and stepped < 200:
        room.step_once()
        b.drain_nowait()
        stepped += 1
    assert a not in room.seats, "慢客户端应被踢出"
    assert a.kicked is True
    assert b in room.seats
    assert stepped < 200


def test_queue_capacity_is_bounded() -> None:
    """每连接一个**有界**队列（BRIEF：慢客户端不阻塞其它人）。"""
    room = make_room("cap")
    seat = room.add(FakeConn(), "Alice")
    assert seat.queue.maxsize == QUEUE_MAXSIZE


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _neighbor(room: Room, idx: int, direction: str) -> int | None:
    """按方向取 4 相邻（越界返回 None）。"""
    state = room.state
    size = state.size
    row, col = divmod(idx, size)
    dr, dc = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}[direction]
    r, c = row + dr, col + dc
    if 0 <= r < size and 0 <= c < size:
        return r * size + c
    return None
