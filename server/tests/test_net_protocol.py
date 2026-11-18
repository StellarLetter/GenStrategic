"""`net.protocol` 单元测试：帧格式必须与 `docs/PROTOCOL.md` 逐字一致。"""

from __future__ import annotations

import json

import pytest

from generals.net import protocol


# ---------------------------------------------------------------------------
# parse_client_frame（PROTOCOL.md §1）
# ---------------------------------------------------------------------------


def test_parse_rejects_invalid_json_as_bad_frame() -> None:
    """PROTOCOL.md §5：JSON 解析失败 → `bad_frame`。"""
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.parse_client_frame("{not json")
    assert exc.value.code == "bad_frame"


@pytest.mark.parametrize("raw", ['[]', '"x"', "42", "null"])
def test_parse_rejects_non_object_as_bad_frame(raw: str) -> None:
    """PROTOCOL.md §2：上行帧必须是 `{ "t": ... }` 对象。"""
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.parse_client_frame(raw)
    assert exc.value.code == "bad_frame"


def test_parse_requires_frame_type() -> None:
    """缺少 `t` → `bad_frame`。"""
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.parse_client_frame('{"name": "Alice"}')
    assert exc.value.code == "bad_frame"


def test_parse_keeps_unknown_frame_type_for_forward_compat() -> None:
    """PROTOCOL.md §1：未知 `t` 一律忽略 —— 解析层**不报错**，交由调用方忽略。"""
    frame = protocol.parse_client_frame('{"t": "future_thing", "x": 1}')
    assert frame["t"] == "future_thing"


def test_parse_join_frame() -> None:
    """PROTOCOL.md §2.1 示例逐字可解析。"""
    frame = protocol.parse_client_frame(
        '{"t": "join", "name": "Alice", "room": "custom-1", "spectate": false}'
    )
    assert frame == {"t": "join", "name": "Alice", "room": "custom-1", "spectate": False}


# ---------------------------------------------------------------------------
# validate_join（PROTOCOL.md §2.1）
# ---------------------------------------------------------------------------


def test_validate_join_defaults() -> None:
    """缺省 `room`/`spectate`。"""
    spec = protocol.validate_join({"t": "join", "name": "Alice"})
    assert spec == {"name": "Alice", "room": None, "spectate": False, "bot": None}


def test_validate_join_truncates_name_to_16() -> None:
    """PROTOCOL.md §2.1：`name` 为 `string(1..16)`。"""
    spec = protocol.validate_join({"t": "join", "name": "x" * 40})
    assert len(spec["name"]) == 16


@pytest.mark.parametrize("name", ["", "   "])
def test_validate_join_rejects_empty_name(name: str) -> None:
    """空昵称 → `bad_frame`。"""
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.validate_join({"t": "join", "name": name})
    assert exc.value.code == "bad_frame"


def test_validate_join_empty_room_means_public() -> None:
    """空字符串房间号等价于公共房（PROTOCOL.md §2.1「缺省 = 公共房」）。"""
    assert protocol.validate_join({"t": "join", "name": "A", "room": ""})["room"] is None


def test_validate_join_accepts_bot_field() -> None:
    """INTERFACES §3：`join` 可带 `bot` 让进程内 AI 占座。"""
    spec = protocol.validate_join({"t": "join", "name": "Bot", "bot": "greedy"})
    assert spec["bot"] == "greedy"


# ---------------------------------------------------------------------------
# validate_commands（PROTOCOL.md §2.4）
# ---------------------------------------------------------------------------


def test_validate_commands_full_example() -> None:
    """PROTOCOL.md §2.4 示例逐条保真（顺序即 FIFO 语义，R6.3）。"""
    frame = {
        "t": "commands",
        "cmds": [
            {"op": "move", "from": 202, "dir": "up", "ratio": 1.0},
            {"op": "clear"},
            {"op": "pop"},
            {"op": "select", "tile": 205},
        ],
    }
    assert protocol.validate_commands(frame) == [
        {"op": "move", "from": 202, "dir": "up", "ratio": 1.0},
        {"op": "clear"},
        {"op": "pop"},
        {"op": "select", "tile": 205},
    ]


def test_validate_commands_drops_unknown_op() -> None:
    """PROTOCOL.md §1：未知字段/取值向前兼容 —— 未知 `op` 静默丢弃。"""
    out = protocol.validate_commands(
        {"t": "commands", "cmds": [{"op": "teleport"}, {"op": "pop"}]}
    )
    assert out == [{"op": "pop"}]


def test_validate_commands_ratio_clamped_to_unit_interval() -> None:
    """PROTOCOL.md §2.4：`ratio: float(0..1]` → 越界钳制；0 视为极小正数。"""
    out = protocol.validate_commands(
        {"t": "commands", "cmds": [{"op": "move", "from": 1, "dir": "down", "ratio": 9.0}]}
    )
    assert out[0]["ratio"] == 1.0
    out0 = protocol.validate_commands(
        {"t": "commands", "cmds": [{"op": "move", "from": 1, "dir": "down", "ratio": 0}]}
    )
    assert 0.0 < out0[0]["ratio"] <= 1.0


def test_validate_commands_ratio_nan_is_bad_frame() -> None:
    """非有限比例 → `bad_frame`（不能把 NaN 塞进内核）。"""
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.validate_commands(
            {"t": "commands", "cmds": [{"op": "move", "from": 1, "dir": "up", "ratio": float("nan")}]}
        )
    assert exc.value.code == "bad_frame"


def test_validate_commands_rejects_unknown_dir() -> None:
    """方向只能是 up/down/left/right（R4.1 4 相邻）。"""
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.validate_commands(
            {"t": "commands", "cmds": [{"op": "move", "from": 1, "dir": "upleft"}]}
        )
    assert exc.value.code == "bad_frame"


def test_validate_commands_move_requires_from() -> None:
    """`move` 必须有 `from`。"""
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_commands({"t": "commands", "cmds": [{"op": "move", "dir": "up"}]})


def test_validate_commands_rejects_bool_as_int() -> None:
    """`true` 不是合法的地块 index（Python 的 bool 是 int 子类，必须显式排除）。"""
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_commands({"t": "commands", "cmds": [{"op": "select", "tile": True}]})


def test_validate_commands_rejects_non_list() -> None:
    """`cmds` 必须是数组。"""
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.validate_commands({"t": "commands", "cmds": {"op": "pop"}})
    assert exc.value.code == "bad_frame"


def test_validate_commands_rate_limits_huge_batch() -> None:
    """单帧条数爆炸 → `rate_limited`（PROTOCOL.md §5）。"""
    cmds = [{"op": "pop"} for _ in range(protocol.MAX_COMMANDS_PER_FRAME + 1)]
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.validate_commands({"t": "commands", "cmds": cmds})
    assert exc.value.code == "rate_limited"


# ---------------------------------------------------------------------------
# validate_chat / validate_ping
# ---------------------------------------------------------------------------


def test_validate_chat_strips_and_limits() -> None:
    """PROTOCOL.md §2.5：`chat.text` 去空白、非空、限长。"""
    assert protocol.validate_chat({"t": "chat", "text": "  gg  "}) == "gg"
    assert len(protocol.validate_chat({"t": "chat", "text": "y" * 999})) == protocol.CHAT_MAX_LEN


def test_validate_chat_rejects_empty() -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_chat({"t": "chat", "text": "   "})


def test_validate_ping_echoes_n() -> None:
    """PROTOCOL.md §2.7：`ping.n` 原样回显。"""
    assert protocol.validate_ping({"t": "ping", "n": 12}) == {"n": 12}
    assert protocol.validate_ping({"t": "ping"}) == {"n": 0}


# ---------------------------------------------------------------------------
# 下行帧构造：逐字段对照 PROTOCOL.md §3
# ---------------------------------------------------------------------------


def test_encode_joined_matches_protocol_2_1() -> None:
    """PROTOCOL.md §2.1 回应帧。"""
    assert protocol.encode_joined(3, "room-x", 0, "tok") == {
        "t": "joined",
        "playerId": 3,
        "roomId": "room-x",
        "seat": 0,
        "token": "tok",
    }


def test_encode_game_start_matches_protocol_3_1() -> None:
    """PROTOCOL.md §3.1：字段 `size` + `players[{playerId,name,color,seat}]`。"""
    frame = protocol.encode_game_start(
        26,
        [
            {"playerId": 7, "name": "Bob", "color": "blue", "seat": 1},
            {"playerId": 3, "name": "Alice", "color": "red", "seat": 0},
        ],
    )
    assert frame == {
        "t": "game_start",
        "size": 26,
        "players": [
            {"playerId": 3, "name": "Alice", "color": "red", "seat": 0},
            {"playerId": 7, "name": "Bob", "color": "blue", "seat": 1},
        ],
    }
    # R7.3：按 seat 升序，不依赖入参顺序。
    assert [p["seat"] for p in frame["players"]] == [0, 1]


def test_encode_turn_matches_protocol_3_2() -> None:
    """PROTOCOL.md §3.2：`half: 0 ⇒ Turn N`，`half: 1 ⇒ Turn N.`。"""
    assert protocol.encode_turn(12, 1) == {"t": "turn", "turn": 12, "half": 1}
    assert protocol.encode_turn(12, 0) == {"t": "turn", "turn": 12, "half": 0}


def test_encode_tiles_matches_protocol_3_3() -> None:
    """PROTOCOL.md §3.3：`[index, army, ownerId, terrain]`（**四元组**，无 `"u"` 前缀）。"""
    updates = [
        protocol.tile_update(202, 12, 0, 3),
        protocol.tile_update(205, 0, None, 2),
    ]
    assert protocol.encode_tiles(updates) == {
        "t": "tiles",
        "list": [[202, 12, 0, 3], [205, 0, None, 2]],
    }
    # 可 JSON 序列化，null 不会被转成字符串
    assert json.loads(json.dumps(protocol.encode_tiles(updates)))["list"][1][2] is None
    # 每项恰好 4 个元素（前端按解构写死，多一个前缀就会错位）
    assert all(len(item) == 4 for item in protocol.encode_tiles(updates)["list"])



def test_encode_sight_matches_protocol_3_4() -> None:
    """PROTOCOL.md §3.4：`add` / `remove` 各自升序（R7.3）。"""
    assert protocol.encode_sight([202, 201], [140]) == {
        "t": "sight",
        "add": [201, 202],
        "remove": [140],
    }


def test_encode_leaderboard_matches_protocol_3_5() -> None:
    """PROTOCOL.md §3.5：行字段 `playerId, name, color, army, land, alive, surrenderIn, star`。"""
    frame = protocol.encode_leaderboard(
        [
            {
                "playerId": 3,
                "name": "Alice",
                "color": "red",
                "army": 2499,
                "land": 208,
                "alive": True,
                "surrenderIn": None,
                "star": 47,
            }
        ]
    )
    assert frame == {
        "t": "leaderboard",
        "rows": [
            {
                "playerId": 3,
                "name": "Alice",
                "color": "red",
                "army": 2499,
                "land": 208,
                "alive": True,
                "surrenderIn": None,
                "star": 47,
            }
        ],
    }


def test_encode_commands_matches_protocol_3_6() -> None:
    """PROTOCOL.md §3.6：`chain: [{from, dir, ratio}]`，顺序 = FIFO 顺序（R6.3）。"""
    frame = protocol.encode_commands(
        [
            {"from": 202, "dir": "up", "ratio": 1.0},
            {"from": 210, "dir": "left", "ratio": 0.5},
        ]
    )
    assert frame == {
        "t": "commands",
        "chain": [
            {"from": 202, "dir": "up", "ratio": 1.0},
            {"from": 210, "dir": "left", "ratio": 0.5},
        ],
    }


def test_encode_event_matches_protocol_3_7() -> None:
    """PROTOCOL.md §3.7：`kind ∈ {captured,left,surrendered,kicked,game_over}`。"""
    assert protocol.encode_event("captured", 37, who="Alice", target="Bob") == {
        "t": "event",
        "kind": "captured",
        "turn": 37,
        "who": "Alice",
        "target": "Bob",
    }
    with pytest.raises(ValueError):
        protocol.encode_event("exploded", 1)


@pytest.mark.parametrize("kind", sorted(protocol.EVENT_KINDS))
def test_encode_event_accepts_every_documented_kind(kind: str) -> None:
    assert protocol.encode_event(kind, 1)["kind"] == kind


def test_encode_chat_matches_protocol_3_8() -> None:
    """PROTOCOL.md §3.8：`turn, name, color, text, system`。"""
    assert protocol.encode_chat(12, "Alice", "red", "gg", system=False) == {
        "t": "chat",
        "turn": 12,
        "name": "Alice",
        "color": "red",
        "text": "gg",
        "system": False,
    }


def test_encode_game_over_matches_protocol_3_9() -> None:
    """PROTOCOL.md §3.9：`winner` 可为 null。"""
    assert protocol.encode_game_over(3, 294) == {"t": "game_over", "winner": 3, "turn": 294}
    assert protocol.encode_game_over(None, 294) == {"t": "game_over", "winner": None, "turn": 294}


def test_encode_reject_matches_protocol_3_10() -> None:
    """PROTOCOL.md §3.10：`op, seq, code`。"""
    assert protocol.encode_reject("move", 4, "not_owned") == {
        "t": "reject",
        "op": "move",
        "seq": 4,
        "code": "not_owned",
    }


def test_encode_pong_shares_ping_field() -> None:
    """PROTOCOL.md §2.7：`pong` 与 `ping` 同构（`n`）。"""
    assert protocol.encode_pong(12) == {"t": "pong", "n": 12}


def test_colors_match_players_and_8_entries() -> None:
    """PROTOCOL.md §3.5 / ARCHITECTURE §5：8 种玩家色，顺序固定。"""
    assert protocol.COLORS == (
        "red",
        "blue",
        "green",
        "teal",
        "purple",
        "pink",
        "maroon",
        "orange",
    )


def test_error_codes_cover_protocol_section_5() -> None:
    """PROTOCOL.md §5 的错误码表必须被本模块全部识别。"""
    expected = {
        "bad_frame",
        "not_joined",
        "not_started",
        "not_your_turn",
        "not_owned",
        "not_adjacent",
        "blocked",
        "chain_full",
        "room_full",
        "rate_limited",
    }
    assert protocol.ERROR_CODES == expected


# ---------------------------------------------------------------------------
# diff_tiles
# ---------------------------------------------------------------------------


class _TV:
    """最小 `TileView` 替身（只带 index/army/owner/terrain）。"""

    def __init__(self, index: int, army: int | None, owner: int | None, terrain: int) -> None:
        self.index = index
        self.army = army
        self.owner = owner
        self.terrain = terrain

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, _TV)
            and (self.index, self.army, self.owner, self.terrain)
            == (other.index, other.army, other.owner, other.terrain)
        )


def test_diff_tiles_only_changed_and_sorted() -> None:
    """PROTOCOL.md §3.3：只发「本刻发生变化」的地块，且按 index 升序（R7.3）。"""
    before = [_TV(0, 1, 0, 0), _TV(1, 2, 0, 0), _TV(2, None, None, 2)]
    after = [_TV(0, 5, 0, 0), _TV(1, 2, 0, 0), _TV(2, None, None, 0)]
    assert protocol.diff_tiles(before, after) == [[0, 5, 0, 0], [2, None, None, 0]]


def test_diff_tiles_empty_prev_means_full_snapshot() -> None:
    """首帧（无上一帧）下发全量，客户端才有初始状态。"""
    after = [_TV(0, 1, None, 0), _TV(1, 0, None, 2)]
    assert protocol.diff_tiles([], after) == [[0, 1, None, 0], [1, 0, None, 2]]


def test_diff_tiles_no_change_yields_empty() -> None:
    before = [_TV(0, 1, 0, 0)]
    after = [_TV(0, 1, 0, 0)]
    assert protocol.diff_tiles(before, after) == []


def test_diff_tiles_detects_army_appearing_from_fog() -> None:
    """视野重入：`army` 由 `None` 变数字也算变化（否则客户端永远看不到真实兵量）。"""
    before = [_TV(0, None, None, 0)]
    after = [_TV(0, 17, 2, 1)]
    assert protocol.diff_tiles(before, after) == [[0, 17, 2, 1]]
