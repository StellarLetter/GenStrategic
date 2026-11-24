"""`Room` 的聊天历史与投降入口测试（R6.10 系统消息 / R5.5 投降）。"""

from __future__ import annotations

import re

import pytest

from conftest import FakeConn, frames_of_kind, make_room, pump, two_player_room
from generals.config import DEFAULT_CONFIG
from generals.game import Terrain, turn_of
from generals.game.rules import CaptureEvent
from generals.game.tick import TickReport
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
    """R6.10 三种系统消息之一：`"<A> left."`（带末尾句点），`kind="left"` 只带一个色块。"""
    room, a, b = two_player_room("sys-left")
    pump(a, b)
    room.remove(a)
    chats = [f for f in b.drain_nowait() if f["t"] == "chat" and f["system"]]
    assert "Alice left." in [f["text"] for f in chats]
    left = next(f for f in chats if f["text"] == "Alice left.")
    assert left["kind"] == "left"
    assert left["name"] == "Alice"
    assert left["color"] == a.color
    assert "target" not in left and "targetColor" not in left


def test_surrender_system_message_uses_screenshot_format() -> None:
    """R6.10 三种系统消息之一：`"<A> surrendered."`，并同时广播 `event{kind:"surrendered"}`。"""
    room, a, b = two_player_room("sys-surrender")
    pump(a, b)
    room.handle(a, {"t": "surrender"})
    frames = b.drain_nowait()
    assert any(f["t"] == "event" and f["kind"] == "surrendered" for f in frames)
    chats = [f for f in frames if f["t"] == "chat" and f["system"]]
    assert "Alice surrendered." in [f["text"] for f in chats]
    surrendered = next(f for f in chats if f["text"] == "Alice surrendered.")
    assert surrendered["kind"] == "surrendered"
    assert surrendered["name"] == "Alice"
    assert surrendered["color"] == a.color
    assert "target" not in surrendered and "targetColor" not in surrendered


def test_no_joined_or_startup_system_message_r6_10() -> None:
    """R6.10：加入、开局等**不播**系统消息（聊天栏只有三种）。"""
    room = make_room("sys-nojoin")
    room.add(FakeConn(), "Alice")
    seat = room.add(FakeConn(), "Bob")
    room.start()
    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    for s in (seat, watcher):
        texts = [f["text"] for f in s.drain_nowait() if f["t"] == "chat" and f["system"]]
        assert texts == [], f"加入/开局不该产生系统消息，实际 {texts}"


def test_system_messages_are_recorded_in_history() -> None:
    """系统消息同样入历史（新观战者应该能看到历史战报）。"""
    room, a, b = two_player_room("sys-log")
    pump(a, b)
    room.handle(a, {"t": "surrender"})
    pump(a, b)
    assert any(e["system"] and e["text"] == "Alice surrendered." for e in room.chat_log)


def test_only_three_system_message_shapes_ever_appear_r6_10() -> None:
    """整局跑下来，`system=True` 的文本**只能**是 R6.10 的三种形状。"""
    room, a, b = two_player_room("sys-pattern")
    pump(a, b)
    room.handle(a, {"t": "surrender"})
    pump(a, b)
    for _ in range(60):
        room.step_once()
        pump(a, b)
        if room.state.ended:
            break
    rules = (
        re.compile(r"^.+\ssurrendered\.$"),  # "<name> surrendered."
        re.compile(r"^.+ captured .+\.$"),  # "<A> captured <B>."（回合号在 turn 字段）
        re.compile(r"^.+\sleft\.$"),  # "<name> left."
    )
    for entry in room.chat_log:
        if not entry.get("system"):
            continue
        assert any(r.match(entry["text"]) for r in rules), f"非法系统消息：{entry['text']!r}"


# ---------------------------------------------------------------------------
# captured（R6.10：带回合号后缀，且只在有明确攻占者时播）
# ---------------------------------------------------------------------------


def _stage_capture(room: Room, attacker: int, defender: int, *, take_capital: bool) -> tuple[int, int]:
    """构造一次必然发生的攻占：给攻击方首都堆兵，把其相邻非山地地块交给防守方。

    前置：`room.state` 已开局；攻击方首都有 4 相邻非山地地块。
    后置：返回 `(攻击方首都 index, 被攻占的 index)`；该目标此时仍属 `defender`。
          当 `take_capital=True` 时把目标**地形改成首都**并指向为防守方的王城，
          于是这次攻占按 R5.2 一次性吞掉防守方全部领土（内核判定条件是
          「目标真实地形是首都」且 `victim.capital == dst`），用于验证
          「攻陷王城只播一条 captured、随后播 left」。
    抛出：`AssertionError` —— 找不到可用相邻格。
    """
    state = room.state
    capital = next(i for i, t in enumerate(state.tiles) if t.owner == attacker)
    state.tiles[capital].army = 60  # R4.2：ceil(60*1.0)=60，min(60,59)=59，足够打死 1 兵
    for d in ("up", "down", "left", "right"):
        nb = _neighbor(room, capital, d)
        if nb is None or state.tiles[nb].is_mountain():
            continue
        state.tiles[nb].owner = defender
        state.tiles[nb].army = 1
        if take_capital:
            # 让目标格真的是一座首都（R2.2 地形），并作为防守方的王城（R5.3）。
            old = state.players[defender].capital
            if old is not None and old != nb:
                state.tiles[old].terrain = Terrain.PLAIN
            state.tiles[nb].terrain = Terrain.CAPITAL
            state.players[defender].capital = nb
        return capital, nb
    raise AssertionError("找不到可用的相邻非山地地块")


def _move(room: Room, seat, src: int, dst: int) -> None:
    """让 `seat` 从 `src` 朝 `dst` 方向入队一条 `move`（ratio 默认 1.0）。"""
    direction = next(
        d for d in ("up", "down", "left", "right") if _neighbor(room, src, d) == dst
    )
    room.handle(seat, {"t": "commands", "cmds": [{"op": "move", "from": src, "dir": direction}]})


def test_captured_text_has_no_turn_suffix_but_frame_carries_turn_r6_10() -> None:
    """R6.10：`captured` 的 `text` **不带**回合号，回合号只在 `chat` 帧的 `turn` 字段。

    前端 `web/src/ui/chat.ts::renderLine` 会把 `turn` 渲染成独立的 `.turn` 元素
    （视觉结果是 `Alice captured Bob. 37.`）。若 `text` 里也写回合号，界面会重复成
    `... 37. 37.`。

    只有**攻陷王城 / 停止抵抗**（`take_capital=True`）才播 `captured`（R6.10）。
    """
    room, a, b = two_player_room("cap-turn")
    pump(a, b)
    capital, target = _stage_capture(room, a.player_id, b.player_id, take_capital=True)
    _move(room, a, capital, target)
    pump(a, b)
    room.step_once()
    frames = b.drain_nowait()
    turn = turn_of(room.state.tick)[0]
    chats = [f for f in frames if f["t"] == "chat" and f["system"] and "captured" in f["text"]]
    assert chats, [f["text"] for f in frames if f["t"] == "chat"]
    captured = chats[0]
    assert captured["text"] == "Alice captured Bob."
    assert captured["turn"] == turn
    # 结构字段：前端 `chat.ts` 按 kind 渲染两个色块（攻占方 + 被攻占方）。
    assert captured["kind"] == "captured"
    assert captured["name"] == "Alice"
    assert captured["color"] == room.state.players[a.player_id].color
    assert captured["target"] == "Bob"
    assert captured["targetColor"] == room.state.players[b.player_id].color
    assert isinstance(captured["system"], bool) and captured["system"] is True
    # 整帧的字段顺序按 types.ts::FrameChat 契约。
    assert list(captured) == [
        "t",
        "turn",
        "name",
        "color",
        "text",
        "system",
        "kind",
        "target",
        "targetColor",
    ]
    # 事件帧照常广播（同样只有 who/target，回合号在 turn 字段）。
    events = [f for f in frames if f["t"] == "event" and f["kind"] == "captured"]
    assert events and events[0]["who"] == "Alice" and events[0]["target"] == "Bob"
    assert events[0]["turn"] == turn


def test_plain_tile_capture_is_silent_r6_10() -> None:
    """R6.10：**普通地块易主不播** `captured` —— 聊天与 `event` 帧都不发。

    一局里占格成千上万，逐格播报只会把聊天栏刷满；只有攻陷王城（停止抵抗）才播。
    """
    room, a, b = two_player_room("cap-plain")
    pump(a, b)
    capital, target = _stage_capture(room, a.player_id, b.player_id, take_capital=False)
    _move(room, a, capital, target)
    pump(a, b)
    room.step_once()
    frames = b.drain_nowait()
    assert any(f["t"] == "tiles" for f in frames), "本刻应当确实发生了占格（tiles 增量）"
    assert not [f for f in frames if f["t"] == "event" and f["kind"] == "captured"]
    assert not [f for f in frames if f["t"] == "chat" and "captured" in f["text"]]
    assert not [e for e in room.chat_log if e["system"] and "captured" in e["text"]]


def test_capital_capture_announces_captured_only_once_r6_10() -> None:
    """攻陷王城会一次性继承全部领土（R5.2），内核逐块发事件 —— 聊天只播一条。"""
    room, a, b = two_player_room("cap-dedupe")
    pump(a, b)
    capital, target = _stage_capture(room, a.player_id, b.player_id, take_capital=True)
    _move(room, a, capital, target)
    pump(a, b)
    room.step_once()
    # 只看一个座位：广播帧在每个座位队列各有一份。
    texts = [f["text"] for f in b.drain_nowait() if f["t"] == "chat" and f["system"]]
    captured = [t for t in texts if " captured " in t]
    assert captured == ["Alice captured Bob."], texts
    assert "Bob left." in texts, texts


def test_no_captured_message_without_a_victor_r6_10() -> None:
    """R6.10 / R5.5：投降过渡期满地块转中立 —— 没有攻占者，**不播** `captured`。"""
    room, a, b = two_player_room("cap-neutral")
    pump(a, b)
    report = TickReport(
        tick=41,
        turn=41,
        half=0,
        captures=[
            CaptureEvent(
                tile=10, new_owner=None, previous_owner=a.player_id, amount=5, reason="neutral"
            ),
        ],
    )
    room._broadcast_report_events(report, 41)
    frames = b.drain_nowait()
    assert not [f for f in frames if f["t"] == "event" and f["kind"] == "captured"]
    assert not [f for f in frames if f["t"] == "chat" and "captured" in f["text"]]


def test_surrender_then_capture_then_left_all_announced_r6_10() -> None:
    """R6.10：三种消息可先后都播 —— 投降 → 过渡期内被攻占（王城）→ 出局（left）。"""
    room, a, b = two_player_room("three-msgs")
    pump(a, b)
    room.handle(a, {"t": "surrender"})
    pump(a, b)
    # 过渡期内 A 仍占着地块（R5.5）且不能操作；B 攻陷 A 的王城 → captured + A 出局 → left。
    capital, target = _stage_capture(room, b.player_id, a.player_id, take_capital=True)
    _move(room, b, capital, target)
    for _ in range(20):
        room.step_once()
        pump(a, b)
        if room.state.ended:
            break
    texts = [e["text"] for e in room.chat_log if e["system"]]
    assert "Alice surrendered." in texts, texts
    assert "Bob captured Alice." in texts, texts
    assert "Alice left." in texts, texts


def test_left_is_announced_once_even_if_player_then_disconnects_r6_10() -> None:
    """出局播过 `left` 后，再关页面**不重复播**（R6.10 只播三种，不刷屏）。"""
    room, a, b = two_player_room("left-once")
    pump(a, b)
    capital, target = _stage_capture(room, b.player_id, a.player_id, take_capital=True)
    _move(room, b, capital, target)
    pump(a, b)
    for _ in range(5):
        room.step_once()
        pump(a, b)
        if room.state.ended:
            break
    before = sum(1 for e in room.chat_log if e["system"] and e["text"] == "Alice left.")
    assert before == 1, [e["text"] for e in room.chat_log if e["system"]]
    # A 已出局/已断开：再 remove 不应再播一条
    for seat in list(room.seats):
        if seat.player_id == a.player_id:
            room.remove(seat)
    after = sum(1 for e in room.chat_log if e["system"] and e["text"] == "Alice left.")
    assert after == 1


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
    """走内核 `rules.begin_surrender`：链立即清空（R5.5），仍 `alive`。"""
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
    """排行榜的 `surrenderIn` 反映内核计数（R5.5）。"""
    room, a, b = two_player_room("surrender-lb")
    pump(a, b)
    room.surrender(a)
    room.step_once()
    rows = frames_of_kind(pump(a, b), "leaderboard")[-1]["rows"]
    row_a = next(r for r in rows if r["playerId"] == a.player_id)
    assert row_a["surrenderIn"] is not None
    assert row_a["alive"] is True
