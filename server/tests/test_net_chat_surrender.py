"""`Room` 的聊天历史与投降入口测试（lead 2026-11-18 补充裁决）。"""

from __future__ import annotations

import pytest

from conftest import FakeConn, frames_of_kind, make_room, pump, two_player_room
from generals.config import DEFAULT_CONFIG
from generals.net import CHAT_LOG_MAX, Room, RoomState


def _neighbor(room: Room, idx: int, direction: str) -> int | None:
    state = room.state
    size = state.size
    row, col = divmod(idx, size)
    dr, dc = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}[direction]
    r, c = row + dr, col + dc
    if 0 <= r < size and 0 <= c < size:
        return r * size + c
    return None


def _own_tile(room: Room, player_id: int) -> int:
    state = room.state
    for idx in sorted(state.visible_tiles(player_id)):
        if state.tiles[idx].owner != player_id:
            continue
        for nb in state.neighbors4(idx) if hasattr(state, "neighbors4") else state.neighbours4(idx):
            if state.tiles[nb].terrain.value != 2:
                return idx
    raise AssertionError("找不到可调兵的己方地块")


# ---------------------------------------------------------------------------
# chat_log：真人聊天
# ---------------------------------------------------------------------------


def test_chat_is_recorded_in_history() -> None:
    """`chat` 帧必须进 `chat_log`（供后加入者补齐）。"""
    room, a, b = two_player_room("chat-log")
    pump(a, b)
    room.handle(a, {"t": "chat", "text": "hello"})
    assert len(room.chat_log) == 1
    entry = room.chat_log[0]
    assert entry["t"] == "chat"
    assert entry["text"] == "hello"
    assert entry["name"] == "Alice"
    assert entry["system"] is False


def test_chat_log_is_bounded() -> None:
    """历史有上限（`CHAT_LOG_MAX`），避免长房间无界增长。"""
    room, a, b = two_player_room("chat-bound")
    pump(a, b)
    for i in range(CHAT_LOG_MAX + 25):
        room.handle(a, {"t": "chat", "text": f"m{i}"})
        pump(a, b)
    assert len(room.chat_log) == CHAT_LOG_MAX
    # 保留的是最新的那些
    assert room.chat_log[-1]["text"] == f"m{CHAT_LOG_MAX + 24}"


def test_late_joiner_receives_chat_history() -> None:
    """中途加入者必须补收历史消息（否则看不到之前的战报）。"""
    room, a, b = two_player_room("chat-late")
    pump(a, b)
    room.handle(a, {"t": "chat", "text": "before you came"})
    pump(a, b)

    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    texts = [f["text"] for f in watcher.drain_nowait() if f["t"] == "chat"]
    assert "before you came" in texts


def test_chat_history_precedes_nothing_but_after_joined() -> None:
    """`joined` 仍是新连接的第一帧（历史在其后）。"""
    room, a, b = two_player_room("chat-order")
    pump(a, b)
    room.handle(a, {"t": "chat", "text": "hi"})
    pump(a, b)
    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    frames = watcher.drain_nowait()
    assert frames[0]["t"] == "joined"


# ---------------------------------------------------------------------------
# chat_log：系统消息
# ---------------------------------------------------------------------------


def test_leave_system_message_uses_screenshot_format() -> None:
    """系统消息格式对齐原站：`"<A> left."`（带末尾句点）。"""
    room, a, b = two_player_room("sys-left")
    pump(a, b)
    room.remove(a)
    texts = [f["text"] for f in b.drain_nowait() if f["t"] == "chat" and f["system"]]
    assert "Alice left." in texts


def test_surrender_system_message_uses_screenshot_format() -> None:
    """`"<A> surrendered."` 并同时广播 `event{kind:"surrendered"}`。"""
    room, a, b = two_player_room("sys-surrender")
    pump(a, b)
    room.handle(a, {"t": "surrender"})
    frames = b.drain_nowait()
    assert any(f["t"] == "event" and f["kind"] == "surrendered" for f in frames)
    texts = [f["text"] for f in frames if f["t"] == "chat" and f["system"]]
    assert "Alice surrendered." in texts


def test_system_messages_are_recorded_in_history() -> None:
    """系统消息同样入历史（新观战者应该能看到历史战报）。"""
    room, a, b = two_player_room("sys-log")
    pump(a, b)
    room.handle(a, {"t": "surrender"})
    pump(a, b)
    assert any(e["system"] and e["text"] == "Alice surrendered." for e in room.chat_log)


# ---------------------------------------------------------------------------
# surrender（公开入口）
# ---------------------------------------------------------------------------


def test_room_has_public_surrender() -> None:
    """`Room.surrender(seat)` 是公开入口（不需要直接调私有方法）。"""
    room, a, b = two_player_room("pub-surrender")
    pump(a, b)
    room.surrender(a)
    assert room.state.players[a.player_id].surrender_ticks_left == DEFAULT_CONFIG.surrender_ticks


def test_surrender_uses_kernel_begin_surrender() -> None:
    """走内核 `rules.begin_surrender`：链立即清空（C-08），仍 `alive`。"""
    room, a, b = two_player_room("kernel-surrender")
    pump(a, b)
    src = _own_tile(room, a.player_id)
    direction = next(d for d in ("up", "down", "left", "right") if _neighbor(room, src, d) is not None)
    room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": src, "dir": direction}]})
    assert len(room.state.players[a.player_id].chain) == 1
    pump(a, b)
    room.surrender(a)
    pstate = room.state.players[a.player_id]
    assert pstate.surrender_ticks_left == DEFAULT_CONFIG.surrender_ticks
    assert pstate.chain == []
    assert pstate.alive is True
    assert pstate.can_operate() is False


def test_move_after_surrender_is_rejected_not_your_turn() -> None:
    """投降后 `move` 必须回 `not_your_turn`（R5.5「期间不能操作」）。"""
    room, a, b = two_player_room("surrender-move")
    pump(a, b)
    src = _own_tile(room, a.player_id)
    direction = next(d for d in ("up", "down", "left", "right") if _neighbor(room, src, d) is not None)
    room.surrender(a)
    pump(a, b)
    room.handle(a, {"t": "commands", "cmds": [{"op": "move", "from": src, "dir": direction}]})
    rejects = frames_of_kind([(s, f) for s, f in pump(a, b) if s is a], "reject")
    assert rejects and rejects[0]["code"] == "not_your_turn"


def test_surrender_twice_is_rejected() -> None:
    """重复投降不再广播事件，并回 `reject`。"""
    room, a, b = two_player_room("surrender-twice")
    pump(a, b)
    room.surrender(a)
    pump(a, b)
    events_before = sum(1 for e in room.chat_log if "surrendered" in e["text"])
    room.surrender(a)
    rejects = frames_of_kind([(s, f) for s, f in pump(a, b) if s is a], "reject")
    assert rejects, "重复投降应被拒绝"
    events_after = sum(1 for e in room.chat_log if "surrendered" in e["text"])
    assert events_after == events_before


def test_surrender_before_start_is_rejected_not_started() -> None:
    """未开局投降 → `not_started`。"""
    room = make_room("surrender-prestart")
    a = room.add(FakeConn(), "Alice")
    a.drain_nowait()
    room.surrender(a)
    rejected = a.drain_nowait()
    assert rejected and rejected[0]["t"] == "reject"
    assert rejected[0]["code"] == "not_started"


def test_spectator_surrender_is_rejected() -> None:
    """观战者没有座位，投降无意义 → `reject`。"""
    room = make_room("surrender-spec")
    room.add(FakeConn(), "Alice")
    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    room.start()
    watcher.drain_nowait()
    room.surrender(watcher)
    rejected = watcher.drain_nowait()
    assert rejected and rejected[0]["t"] == "reject"


def test_surrendered_player_is_called_in_leaderboard() -> None:
    """排行榜的 `surrenderIn` 反映内核计数（C-08）。"""
    room, a, b = two_player_room("surrender-lb")
    pump(a, b)
    room.surrender(a)
    room.step_once()
    rows = frames_of_kind(pump(a, b), "leaderboard")[-1]["rows"]
    row_a = next(r for r in rows if r["playerId"] == a.player_id)
    assert row_a["surrenderIn"] is not None
    assert row_a["alive"] is True
