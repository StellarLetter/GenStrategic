"""`chain_dropped`：调兵请求作废的转发（R4.12 / PROTOCOL.md §3.7）。

覆盖点
------
- `reason="src_lost"`：起点已不归该玩家控制（预调兵 R4.11 入队时不校验，结算时作废）；
- `reason="zero_army"`：按 R4.2 算出的调兵量 `<= 0`（兵力 1 的地块无法调兵）；
- `cascade`：因本请求作废而级联作废的下游请求条数（R4.12③）；
- **只发给本人**：其它玩家与观战者都收不到 `chain_dropped`，也收不到那份补发的 `commands`；
- 帧序：`chain_dropped` **紧随其后**就是新的 `commands` 全量快照（链已变短）；
- 字段：`playerId` 与 `joined.playerId` 同源，`dir` 为协议字面量。
"""

from __future__ import annotations

from conftest import FakeConn, frames_of_kind, make_room, pump, two_player_room
from generals.net import Room


def _drops(frames: list[dict]) -> list[dict]:
    """筛出 `chain_dropped` 事件帧。"""
    return [f for f in frames if f["t"] == "event" and f["kind"] == "chain_dropped"]


def _commands(frames: list[dict]) -> list[dict]:
    """筛出 `commands` 帧。"""
    return [f for f in frames if f["t"] == "commands"]


def _neighbor(room: Room, idx: int, direction: str) -> int | None:
    state = room.state
    size = state.size
    row, col = divmod(idx, size)
    dr, dc = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}[direction]
    r, c = row + dr, col + dc
    if 0 <= r < size and 0 <= c < size:
        return r * size + c
    return None


def _free_dir(room: Room, src: int) -> str:
    """从 `src` 找一个不越界的方向（用于构造 `move`）。"""
    for d in ("up", "down", "left", "right"):
        if _neighbor(room, src, d) is not None:
            return d
    raise AssertionError("没有可用方向")


def _own_tile(room: Room, player_id: int) -> int:
    return next(i for i, t in enumerate(room.state.tiles) if t.owner == player_id)


# ---------------------------------------------------------------------------
# src_lost
# ---------------------------------------------------------------------------


def test_src_lost_only_reaches_the_owner_r4_12() -> None:
    """起点不归自己 → 结算时作废，且 `chain_dropped` + 新 `commands` 只发给本人。"""
    room, a, b = two_player_room("drop-srclost")
    pump(a, b)
    foreign = _own_tile(room, b.player_id)  # 预调兵：A 从 B 的地块出发（R4.11 允许入队）
    room.handle(
        a,
        {"t": "commands", "cmds": [{"op": "move", "from": foreign, "dir": _free_dir(room, foreign), "ratio": 1.0}]},
    )
    pump(a, b)

    room.step_once()
    frames_a = a.drain_nowait()
    frames_b = b.drain_nowait()

    drops = _drops(frames_a)
    assert len(drops) == 1, [f["t"] for f in frames_a]
    drop = drops[0]
    assert drop["playerId"] == a.player_id, "playerId 应与 joined.playerId 同源"
    assert drop["from"] == foreign
    assert drop["dir"] in ("up", "down", "left", "right")
    assert drop["ratio"] == 1.0
    assert drop["reason"] == "src_lost"
    assert drop["cascade"] == 0

    assert _drops(frames_b) == [], "别人的作废请求不能发给 B"
    for f in frames_b:
        assert f["t"] != "chain_dropped"


def test_commands_snapshot_follows_drop_immediately_r4_12() -> None:
    """`chain_dropped` 之后**紧接着**就是新的 `commands` 全量快照（§3.7）。"""
    room, a, b = two_player_room("drop-order")
    pump(a, b)
    foreign = _own_tile(room, b.player_id)
    room.handle(
        a,
        {"t": "commands", "cmds": [{"op": "move", "from": foreign, "dir": _free_dir(room, foreign), "ratio": 1.0}]},
    )
    pump(a, b)

    room.step_once()
    frames = a.drain_nowait()
    kinds = [f["t"] for f in frames]
    idx = next(
        i for i, f in enumerate(frames) if f["t"] == "event" and f["kind"] == "chain_dropped"
    )
    assert kinds[idx + 1] == "commands", f"chain_dropped 后应紧跟 commands，实际 {kinds[idx:idx+3]}"
    # 链已变短：作废后快照为空。
    assert frames[idx + 1]["chain"] == []


def test_drop_does_not_disturb_other_players_chain_r4_12() -> None:
    """A 的请求作废不影响 B 自己的调兵链回显。"""
    room, a, b = two_player_room("drop-others")
    pump(a, b)
    foreign = _own_tile(room, b.player_id)
    room.handle(
        a,
        {"t": "commands", "cmds": [{"op": "move", "from": foreign, "dir": _free_dir(room, foreign), "ratio": 1.0}]},
    )
    pump(a, b)

    # B 入队 2 条：每 Tick 只弹 1 条（R4.7），因此结算后链里还剩 1 条。
    own_b = _own_tile(room, b.player_id)
    d_b = _free_dir(room, own_b)
    room.state.tiles[own_b].army = 40  # 确保第 1 条真的能发起（R4.2）
    room.handle(
        b,
        {
            "t": "commands",
            "cmds": [
                {"op": "move", "from": own_b, "dir": d_b, "ratio": 1.0},
                {"op": "move", "from": own_b, "dir": d_b, "ratio": 1.0},
            ],
        },
    )
    pump(a, b)

    room.step_once()
    frames_b = b.drain_nowait()
    assert _drops(frames_b) == [], "B 没有请求作废，不该收到 chain_dropped"
    # B 自己的链仍在（起点合法），回显里应保有剩下的那 1 条。
    chain_frames = _commands(frames_b)
    assert any(len(f["chain"]) == 1 for f in chain_frames), [f["chain"] for f in chain_frames]


# ---------------------------------------------------------------------------
# zero_army
# ---------------------------------------------------------------------------


def _to_no_growth_tick(room: Room, *seats) -> None:
    """推进到「上半回合」（`Turn N`）的前一刻，并清空各座位队列。

    R3.7 / R3.4：增长只发生在 `Turn N.`（下半回合，`tick % 2 == 1`）。
    为了构造 `zero_army`（R4.2 算出 0 兵），必须让下一次结算落在**不增长**的刻上，
    否则首都兵力会先从 1 涨到 2，就调得出 1 兵了。
    """
    room.step_once()  # tick=1 → Turn 0.（会增长）
    for seat in seats:
        seat.drain_nowait()
    assert room.state.tick % 2 == 1, "下一次 step 应落在不增长的上半回合"


def test_zero_army_is_dropped_with_reason_r4_2_r4_12() -> None:
    """R3.2 首都初始兵力 1 ⇒ R4.2 算出 0 兵 ⇒ 作废，`reason="zero_army"`。"""
    room, a, b = two_player_room("drop-zero")
    pump(a, b)
    _to_no_growth_tick(room, a, b)
    capital = _own_tile(room, a.player_id)
    room.state.tiles[capital].army = 1  # 增长会把它变成 2，这里按 R3.2 的初始值重置
    room.handle(
        a,
        {"t": "commands", "cmds": [{"op": "move", "from": capital, "dir": _free_dir(room, capital), "ratio": 1.0}]},
    )
    pump(a, b)

    room.step_once()
    drops = _drops(a.drain_nowait())
    assert len(drops) == 1, [f["t"] for f in a.drain_nowait()]
    assert drops[0]["reason"] == "zero_army"
    assert drops[0]["from"] == capital
    b.drain_nowait()


# ---------------------------------------------------------------------------
# cascade
# ---------------------------------------------------------------------------


def test_cascade_counts_dependents_r4_12_3() -> None:
    """首条请求作废 → 以它目标地块为起点的下游请求一并作废，`cascade` 报告条数。"""
    room, a, b = two_player_room("drop-cascade")
    pump(a, b)
    _to_no_growth_tick(room, a, b)
    capital = _own_tile(room, a.player_id)
    room.state.tiles[capital].army = 1
    direction = _free_dir(room, capital)
    hop = _neighbor(room, capital, direction)
    assert hop is not None
    # ① 首都兵力 1 → 必然 zero_army；② 从①的目标出发（预调兵，R4.11 允许）→ 级联作废。
    room.handle(
        a,
        {
            "t": "commands",
            "cmds": [
                {"op": "move", "from": capital, "dir": direction, "ratio": 1.0},
                {"op": "move", "from": hop, "dir": _free_dir(room, hop), "ratio": 1.0},
            ],
        },
    )
    pump(a, b)

    room.step_once()
    drops = _drops(a.drain_nowait())
    b.drain_nowait()
    assert drops, "应有作废事件"
    first = drops[0]
    assert first["from"] == capital, "首条（zero_army）应先报"
    assert first["cascade"] >= 1, f"应有下游级联，实际 {first['cascade']}"
    assert {d["reason"] for d in drops} <= {"src_lost", "zero_army"}


# ---------------------------------------------------------------------------
# 观战者 / 多玩家隔离
# ---------------------------------------------------------------------------


def test_spectator_never_receives_chain_dropped_r4_12() -> None:
    """作废是**私有**通知：观战者不该收到 `chain_dropped`。"""
    room = make_room("drop-spect")
    room.add(FakeConn(), "Alice")
    room.add(FakeConn(), "Bob")
    room.start()
    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    watcher.drain_nowait()
    seats = [s for s in room.seats if not s.spectate]
    for s in seats:
        s.drain_nowait()

    a = next(s for s in seats if s.name == "Alice")
    foreign = _own_tile(room, next(s.player_id for s in seats if s.name == "Bob"))
    room.handle(
        a,
        {"t": "commands", "cmds": [{"op": "move", "from": foreign, "dir": _free_dir(room, foreign), "ratio": 1.0}]},
    )
    for s in seats:
        s.drain_nowait()
    watcher.drain_nowait()

    room.step_once()
    frames = watcher.drain_nowait()
    assert _drops(frames) == []
    assert not [f for f in frames if f["t"] == "event" and f["kind"] == "chain_dropped"]
    # 观战者的队列里也不该出现「因作废而补发的空链」（他的 commands 只跟自己的链有关）。
    assert all(f["t"] != "chain_dropped" for f in frames)
    for s in seats:
        s.drain_nowait()


def test_no_drop_frame_when_chain_is_legal_r4_12() -> None:
    """正常入队并成功发起的请求**不**产生 `chain_dropped`。"""
    room, a, b = two_player_room("drop-none")
    pump(a, b)
    capital = _own_tile(room, a.player_id)
    room.state.tiles[capital].army = 30  # 让它真的有兵可调（R4.2）
    room.handle(
        a,
        {"t": "commands", "cmds": [{"op": "move", "from": capital, "dir": _free_dir(room, capital), "ratio": 1.0}]},
    )
    pump(a, b)

    room.step_once()
    assert _drops(a.drain_nowait()) == []
    assert _drops(b.drain_nowait()) == []


def test_two_players_each_get_only_their_own_drops_r4_12() -> None:
    """两人同刻都有作废请求时，各自只收到自己的那一条（按 player id 排序）。"""
    room, a, b = two_player_room("drop-two")
    pump(a, b)
    _to_no_growth_tick(room, a, b)
    foreign = _own_tile(room, b.player_id)  # A 从 B 的王城出发 → src_lost
    room.handle(
        a,
        {"t": "commands", "cmds": [{"op": "move", "from": foreign, "dir": _free_dir(room, foreign), "ratio": 1.0}]},
    )
    capital_b = _own_tile(room, b.player_id)
    room.state.tiles[capital_b].army = 1  # B 从自己兵力 1 的王城出发 → zero_army
    room.handle(
        b,
        {"t": "commands", "cmds": [{"op": "move", "from": capital_b, "dir": _free_dir(room, capital_b), "ratio": 1.0}]},
    )
    pump(a, b)

    room.step_once()
    drops_a = _drops(a.drain_nowait())
    drops_b = _drops(b.drain_nowait())
    assert len(drops_a) == 1 and len(drops_b) == 1
    assert drops_a[0]["playerId"] == a.player_id
    assert drops_b[0]["playerId"] == b.player_id
    assert drops_a[0]["from"] == foreign
    assert drops_b[0]["from"] == capital_b
    # 各人的补发快照都是自己的链（都已清空）。
    assert drops_a[0]["reason"] in ("src_lost", "zero_army")
    assert drops_b[0]["reason"] in ("src_lost", "zero_army")