"""线协议编解码：上行帧校验 + 下行帧构造。

契约见 `docs/INTERFACES.md` §2（`net`）与 `docs/PROTOCOL.md`（帧格式唯一事实源）。
本模块只做「字符串 / dict / 校验」，**不碰 socket、不碰 GameState**，可被纯逻辑单测。

设计约束
--------
- 所有 `encode_*` 返回**纯 dict**，字段名/大小写/嵌套与 `docs/PROTOCOL.md` §3 **逐字一致**；
  序列化（`json.dumps`）由 `net.session` 负责，便于测试里直接断言 dict。
- 上行帧校验只认 `docs/PROTOCOL.md` §2 定义的 7 种 `t`；未知 `t` 与未知字段一律忽略（向前兼容，见 PROTOCOL.md §1）。
- 所有错误都收敛为 `ProtocolError(code)`，`code` 取自 `docs/PROTOCOL.md` §5 错误码表。
"""

from __future__ import annotations

import json
import math
from typing import Any, Final

from generals.game.commands import quantize_ratio

__all__ = [
    "COLORS",
    "CLIENT_FRAME_TYPES",
    "DIRECTIONS",
    "EVENT_KINDS",
    "SYSTEM_CHAT_KINDS",
    "OPS",
    "ProtocolError",
    "parse_client_frame",
    "validate_chat",
    "validate_commands",
    "validate_join",
    "validate_autopilot",
    "validate_ping",
    "validate_spectate",
    "encode_joined",
    "encode_game_start",
    "encode_turn",
    "encode_tiles",
    "encode_sight",
    "encode_leaderboard",
    "encode_commands",
    "encode_autopilot",
    "encode_event",
    "encode_chat",
    "encode_chain_dropped",
    "encode_game_over",
    "encode_reject",
    "encode_ack",
    "encode_pong",
    "tile_update",
    "diff_tiles",
]

# ---------------------------------------------------------------------------
# 常量（线协议字面量，改动必须同步 docs/PROTOCOL.md）
# ---------------------------------------------------------------------------

#: 玩家配色，顺序与 `SaveWeb/屏幕截图 *.png` 排行榜色块一致（PROTOCOL.md §3.5 / ARCHITECTURE.md §5）。
COLORS: Final[tuple[str, ...]] = (
    "red",
    "blue",
    "green",
    "teal",
    "purple",
    "pink",
    "maroon",
    "orange",
)

#: 调兵方向字面量（PROTOCOL.md §2.4 / R4.1）。
DIRECTIONS: Final[tuple[str, ...]] = ("up", "down", "left", "right")

#: 上行指令 op 字面量（PROTOCOL.md §2.4）。
OPS: Final[tuple[str, ...]] = ("move", "clear", "pop", "select")

#: 上行帧类型集合（PROTOCOL.md §2）。
CLIENT_FRAME_TYPES: Final[frozenset[str]] = frozenset(
    {"join", "spectate", "ready", "start", "commands", "chat", "surrender", "ping", "autopilot"}
)

#: 系统聊天的 `kind` 集合（R6.10：聊天栏**只允许**这三种系统消息）。
SYSTEM_CHAT_KINDS: Final[tuple[str, ...]] = (
    "surrendered",
    "captured",
    "left",
)

#: 下行事件 kind 集合（PROTOCOL.md §3.7）。
#:
#: 注意：`chain_dropped` 虽然也是 `event` 帧，但字段结构与其它五种**不同**
#: （`seat`/`from`/`dir`/`ratio`/`reason`/`cascade`，没有 `turn`/`who`/`target`），
#: 因此必须用专用编码器 `encode_chain_dropped`，不要走 `encode_event`。
EVENT_KINDS: Final[tuple[str, ...]] = (
    "captured",
    "left",
    "surrendered",
    "kicked",
    "game_over",
    "chain_dropped",
)

#: PROTOCOL.md §5 错误码表（本模块会用到的全集，`ProtocolError.code` 必须取自这里）。
ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
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
)

#: 昵称长度上限（PROTOCOL.md §2.1：`string(1..16)`）。
NAME_MAX_LEN: Final[int] = 16

#: 策略名长度上限（`autopilot` 帧 / `join.bot`；与 `ai.loader` 保持一致）。
STRATEGY_MAX_LEN: Final[int] = 64


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    """上行帧不符合 schema。

    前置：无。
    后置：`code` ∈ `ERROR_CODES` 的子集（默认 `bad_frame`），`detail` 为人读原因，便于日志。
    抛出：本类由本模块的 `parse_*`/`validate_*` 抛出，由 `net.session` 转成 `reject` 帧。
    """

    def __init__(self, code: str = "bad_frame", detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# 基础解析工具
# ---------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    """严格的整数判定：`bool` 不算整数（`True`/`False` 是 Python 的 int 子类，但 JSON 语义上不是）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float))) and not isinstance(value, bool)


def _require_dict(frame: Any) -> dict:
    if not isinstance(frame, dict):
        raise ProtocolError("bad_frame", "frame must be a JSON object")
    return frame


def _require_int(frame: dict, key: str, *, default: int | None = None) -> int:
    if key not in frame:
        if default is None:
            raise ProtocolError("bad_frame", f"missing field {key!r}")
        return default
    value = frame[key]
    if not _is_int(value):
        raise ProtocolError("bad_frame", f"field {key!r} must be int")
    return int(value)


def _as_dir(value: Any) -> str:
    """把线协议方向字面量规范化为小写；非法值抛 `ProtocolError('bad_frame')`。"""
    if not isinstance(value, str):
        raise ProtocolError("bad_frame", "dir must be a string")
    lowered = value.strip().lower()
    if lowered not in DIRECTIONS:
        raise ProtocolError("bad_frame", f"unknown dir {value!r}")
    return lowered


def _as_ratio(value: Any, *, default: float = 1.0) -> float:
    """比例规范化：缺失 => `default`；非数或非有限值 => `bad_frame`；其余量化到 10% 整数倍。

    规则依据：R6.6 规定比例只取 10% 的整数倍（10%–100%，`+`/`-` 步长 10%）。
    量化用内核的 :func:`generals.game.commands.quantize_ratio`（就近取整），
    这样**线协议、前端与 AI 共用同一比例集合**，AI 无法调出真人无法选择的比例
    （AI_API.md §1.2）。比例不影响请求合法性、只影响调兵量，因此量化而不拒绝整条指令。
    """
    if value is None:
        return default
    if not _is_number(value):
        raise ProtocolError("bad_frame", "ratio must be a number")
    ratio = float(value)
    if not math.isfinite(ratio):
        raise ProtocolError("bad_frame", "ratio must be finite")
    # R6.6：比例只取 10% 的整数倍；越界与小数比例一律就近量化（不拒绝整条指令）。
    return quantize_ratio(ratio)


# ---------------------------------------------------------------------------
# 上行帧解析（PROTOCOL.md §2）
# ---------------------------------------------------------------------------


def parse_client_frame(raw: str) -> dict:
    """把一条 WS 文本帧解析成 dict，并校验顶层 `t`。

    前置：`raw` 是服务器收到的原始文本（UTF-8 已解码）。
    后置：返回的 dict 一定含 `t`（字符串），且 `t` ∈ `CLIENT_FRAME_TYPES`。
          **未知 `t` 也照常返回**，由调用方（`net.session`）静默忽略，以实现向前兼容（PROTOCOL.md §1）。
    抛出：`ProtocolError('bad_frame')` —— JSON 解析失败、顶层不是对象、缺 `t`、`t` 不是字符串。
    """
    if not isinstance(raw, str):
        raise ProtocolError("bad_frame", "frame must be text")
    try:
        frame = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:  # pragma: no cover - 分支合并
        raise ProtocolError("bad_frame", f"invalid json: {exc}") from exc
    if not isinstance(frame, dict):
        raise ProtocolError("bad_frame", "frame must be a JSON object")
    kind = frame.get("t")
    if not isinstance(kind, str) or not kind:
        raise ProtocolError("bad_frame", "missing frame type 't'")
    return frame


def validate_join(frame: dict) -> dict:
    """校验 `join` 帧（PROTOCOL.md §2.1）。

    前置：`frame["t"] == "join"`。
    后置：返回规范化 dict：`{"name": str, "room": str | None, "spectate": bool, "bot": str | None}`；
          `name` 已 strip 且长度 ∈ 1..16（超长截断到 16，符合 PROTOCOL.md「重名自动加后缀」的容量假设）。
    抛出：`ProtocolError('bad_frame')` —— `name` 缺失/非字符串/去空白后为空、`room`/`spectate`/`bot` 类型错误。
    """
    frame = _require_dict(frame)
    name = frame.get("name")
    if not isinstance(name, str):
        raise ProtocolError("bad_frame", "name must be a string")
    name = name.strip()
    if not name:
        raise ProtocolError("bad_frame", "name must not be empty")
    if len(name) > NAME_MAX_LEN:
        name = name[:NAME_MAX_LEN]

    room = frame.get("room")
    if room is not None and not isinstance(room, str):
        raise ProtocolError("bad_frame", "room must be a string")
    if isinstance(room, str):
        room = room.strip() or None  # 空串等价于「公共房」（PROTOCOL.md §2.1 缺省语义）
    spectate = frame.get("spectate", False)
    if not isinstance(spectate, bool):
        raise ProtocolError("bad_frame", "spectate must be bool")

    # `bot` 是 INTERFACES.md §3「接入方式一：进程内」的扩展字段，PROTOCOL.md 未列入；
    # 按 PROTOCOL.md §1「未知字段一律忽略」的对称原则处理：存在则透传，非法则报 bad_frame。
    bot = frame.get("bot")
    if bot is not None and not isinstance(bot, str):
        raise ProtocolError("bad_frame", "bot must be a string")
    if isinstance(bot, str):
        bot = bot.strip() or None

    return {"name": name, "room": room, "spectate": spectate, "bot": bot}


def validate_autopilot(frame: dict) -> dict:
    """校验 `autopilot` 帧（PROTOCOL.md §2.8）。

    前置：`frame["t"] == "autopilot"`。
    后置：返回 `{"strategy": str | None}`：
          `"strategy": null` 或空串 = **取消托管**（操作权回到真人）；
          非空字符串 = 交给该策略（是否已注册由 `Room` 校验，本函数只查结构）。
    抛出：`ProtocolError('bad_frame')` —— `strategy` 既不是 `null` 也不是字符串，
          或字符串超长（> `STRATEGY_MAX_LEN`）。
    """
    frame = _require_dict(frame)
    raw = frame.get("strategy", frame.get("bot"))
    if raw is None:
        return {"strategy": None}
    if not isinstance(raw, str):
        raise ProtocolError("bad_frame", "strategy must be a string or null")
    name = raw.strip()
    if not name:
        return {"strategy": None}
    if len(name) > STRATEGY_MAX_LEN:
        raise ProtocolError("bad_frame", "strategy name too long")
    return {"strategy": name}


def validate_spectate(frame: dict) -> dict:
    """校验 `spectate` 帧（PROTOCOL.md §2.2）。

    前置：`frame["t"] == "spectate"`。
    后置：返回 `{"room": str | None}`；空串/缺省等价于 `None`（= 公共房）。
    抛出：`ProtocolError('bad_frame')` —— `room` 不是字符串。
    """
    frame = _require_dict(frame)
    room = frame.get("room")
    if room is not None and not isinstance(room, str):
        raise ProtocolError("bad_frame", "room must be a string")
    if isinstance(room, str):
        room = room.strip() or None
    return {"room": room}


def validate_commands(frame: dict) -> list[dict]:
    """校验 `commands` 批（PROTOCOL.md §2.4）。

    前置：`frame["t"] == "commands"`。
    后置：返回**规范化后**的指令 dict 列表，每项形如
          `{"op": "move", "from": int, "dir": str, "ratio": float}` /
          `{"op": "clear"}` / `{"op": "pop"}` / `{"op": "select", "tile": int}`；
          顺序与上行完全一致（R6.3 调兵链 FIFO 语义要求顺序保真）。
          **未知 `op` 被丢弃**（PROTOCOL.md §1 向前兼容），不报错。
    抛出：`ProtocolError('bad_frame')` —— `cmds` 不是 list、某条不是 dict、字段类型/取值非法、
          单帧条数超过 `MAX_COMMANDS_PER_FRAME`。
    """
    frame = _require_dict(frame)
    cmds = frame.get("cmds")
    if not isinstance(cmds, list):
        raise ProtocolError("bad_frame", "cmds must be a list")
    if len(cmds) > MAX_COMMANDS_PER_FRAME:
        # PROTOCOL.md §5 的 `rate_limited` 用于「指令过于频繁」；一次性塞爆一帧也归此类。
        raise ProtocolError("rate_limited", "too many commands in one frame")

    out: list[dict] = []
    for raw_cmd in cmds:
        if not isinstance(raw_cmd, dict):
            raise ProtocolError("bad_frame", "each command must be an object")
        op = raw_cmd.get("op")
        if not isinstance(op, str):
            raise ProtocolError("bad_frame", "command.op must be a string")
        op = op.strip().lower()
        if op not in OPS:
            continue  # 向前兼容：未知 op 静默丢弃（PROTOCOL.md §1）
        if op == "move":
            src = _require_int(raw_cmd, "from")
            out.append(
                {
                    "op": "move",
                    "from": src,
                    "dir": _as_dir(raw_cmd.get("dir")),
                    "ratio": _as_ratio(raw_cmd.get("ratio")),
                }
            )
        elif op == "select":
            out.append({"op": "select", "tile": _require_int(raw_cmd, "tile")})
        else:  # clear / pop
            out.append({"op": op})
    return out


def validate_chat(frame: dict) -> str:
    """校验 `chat` 帧（PROTOCOL.md §2.5）。

    前置：`frame["t"] == "chat"`。
    后置：返回 strip 后的文本，长度 ∈ 1..`CHAT_MAX_LEN`（超长截断）。
    抛出：`ProtocolError('bad_frame')` —— `text` 缺失/非字符串/去空白后为空。
    """
    frame = _require_dict(frame)
    text = frame.get("text")
    if not isinstance(text, str):
        raise ProtocolError("bad_frame", "text must be a string")
    text = text.strip()
    if not text:
        raise ProtocolError("bad_frame", "text must not be empty")
    return text[:CHAT_MAX_LEN]


def validate_ping(frame: dict) -> dict:
    """校验 `ping` 帧（PROTOCOL.md §2.7）。

    前置：`frame["t"] == "ping"`。
    后置：返回 `{"n": int}`；`n` 缺省为 0（原样回显给客户端，不参与任何结算）。
    抛出：`ProtocolError('bad_frame')` —— `n` 存在但不是 int。
    """
    frame = _require_dict(frame)
    return {"n": _require_int(frame, "n", default=0)}


#: 单帧指令条数上限。PROTOCOL.md 未规定上限；这里给一个宽松护栏（远大于 R4.7 的每刻 1 条）。
MAX_COMMANDS_PER_FRAME: Final[int] = 64

#: 聊天文本长度上限。PROTOCOL.md 未规定；取 200 字符，避免被当作广播放大攻击面。
CHAT_MAX_LEN: Final[int] = 200


# ---------------------------------------------------------------------------
# 下行帧构造（PROTOCOL.md §3）—— 纯 dict，字段名与文档逐字一致
# ---------------------------------------------------------------------------


def encode_joined(player_id: int, room_id: str, seat: int, token: str) -> dict:
    """`joined` 帧（PROTOCOL.md §2.1 回应）。

    前置：`player_id` 为已分配玩家 id（观战者可沿用其 `playerId`，由 Room 决定）。
    后置：返回 `{"t": "joined", "playerId", "roomId", "seat", "token"}`。
    抛出：无。
    """
    return {
        "t": "joined",
        "playerId": int(player_id),
        "roomId": str(room_id),
        "seat": int(seat),
        "token": str(token),
    }


def encode_game_start(size: int, players: list[dict]) -> dict:
    """`game_start` 帧（PROTOCOL.md §3.1）。

    前置：`players` 每项含 `playerId/name/color/seat`。
    后置：返回 `{"t": "game_start", "size": int, "players": [...]}`，`players` **按 seat 升序**（R7.3 确定性）。
    抛出：`KeyError`（缺少必需键）—— 属于内部装配错误，不对外暴露。
    """
    rows = [
        {
            "playerId": int(p["playerId"]),
            "name": str(p["name"]),
            "color": str(p["color"]),
            "seat": int(p["seat"]),
        }
        for p in players
    ]
    rows.sort(key=lambda r: (r["seat"], r["playerId"]))
    return {"t": "game_start", "size": int(size), "players": rows}


def encode_turn(turn: int, half: int) -> dict:
    """`turn` 帧（PROTOCOL.md §3.2）。`half=0` ⇒ `Turn N`，`half=1` ⇒ `Turn N.`（R1.2）。"""
    return {"t": "turn", "turn": int(turn), "half": int(half)}


def tile_update(index: int, army: int | None, owner: int | None, terrain: int) -> list:
    """单条地块更新四元组 `[index, army, ownerId, terrain]`（PROTOCOL.md §3.3 正文）。

    注意：§3.3 的 JSON 示例曾被误写成 `["u", 202, 12, 0, 3]`（多一个 `"u"` 前缀），
    已按正文「地块四元组」修正（lead 裁决 2026-11-18）。四元组与前端的 `types.ts`
    以及内核 `view.diff_views()` 的产出形状一致。
    """
    return [int(index), army, owner, int(terrain)]


def encode_tiles(updates: list[list]) -> dict:
    """`tiles` 视野增量帧（PROTOCOL.md §3.3）。

    前置：`updates` 每项由 `tile_update` 生成，**按 index 升序**（R7.3 确定性）。
    后置：返回 `{"t": "tiles", "list": [...]}`。
    抛出：无。
    """
    return {"t": "tiles", "list": updates}


def diff_tiles(prev: list, cur: list) -> list[list]:
    """比较上一帧与当前帧的 `TileView` 列表，产出 `tiles` 帧的 `list` 字段。

    前置：`prev` 与 `cur` 等长（均为 `size*size`），元素是 `view.TileView` 或带
          `index/army/owner/terrain` 属性的对象。
    后置：返回**只含变化项**的更新列表，**按 index 升序**；未变化项不出现。
    抛出：无。
    """
    updates: list[list] = []
    if not prev:
        # 首帧：全量下发（客户端此时还没有任何地块数据）。
        for tv in cur:
            updates.append(tile_update(tv.index, tv.army, tv.owner, tv.terrain))
        return updates
    for new in cur:
        idx = new.index
        if idx >= len(prev):
            updates.append(tile_update(idx, new.army, new.owner, new.terrain))
            continue
        old = prev[idx]
        if (old.army, old.owner, old.terrain) != (new.army, new.owner, new.terrain):
            updates.append(tile_update(idx, new.army, new.owner, new.terrain))
    return updates


def encode_sight(add: list[int], remove: list[int]) -> dict:
    """`sight` 视野集合变化帧（PROTOCOL.md §3.4）。

    前置：`add` / `remove` 是地块 index 集合。
    后置：返回 `{"t": "sight", "add": [...], "remove": [...]}`，两列表**各自升序**（R7.3）。
    抛出：无。
    """
    return {
        "t": "sight",
        "add": sorted(int(i) for i in add),
        "remove": sorted(int(i) for i in remove),
    }


def encode_leaderboard(rows: list[dict]) -> dict:
    """`leaderboard` 帧（PROTOCOL.md §3.5）。

    前置：每行含 `playerId/name/color/army/land/alive/surrenderIn/star`。
    后置：返回 `{"t": "leaderboard", "rows": [...]}`，**按 `playerId` 升序**稳定输出（R7.3；
          「星数排序」是前端的展示职责，服务端只保证确定性顺序）。
    抛出：`KeyError`（内部装配错误）。
    """
    out = [
        {
            "playerId": int(r["playerId"]),
            "name": str(r["name"]),
            "color": str(r["color"]),
            "army": int(r["army"]),
            "land": int(r["land"]),
            "alive": bool(r["alive"]),
            "surrenderIn": r["surrenderIn"],
            "star": int(r["star"]),
        }
        for r in rows
    ]
    out.sort(key=lambda r: r["playerId"])
    return {"t": "leaderboard", "rows": out}


def encode_commands(chain: list[dict]) -> dict:
    """权威调兵链回显帧（PROTOCOL.md §3.6）。

    前置：`chain` 每项含 `from/dir/ratio`（顺序即 FIFO 顺序，R6.3）。
    后置：返回 `{"t": "commands", "chain": [{"from","dir","ratio"}, ...]}`，顺序保持不变。
    抛出：无。
    """
    return {
        "t": "commands",
        "chain": [
            {"from": int(c["from"]), "dir": str(c["dir"]), "ratio": float(c["ratio"])}
            for c in chain
        ],
    }


def encode_event(kind: str, turn: int, who: str | None = None, target: str | None = None) -> dict:
    """`event` 系统事件帧（PROTOCOL.md §3.7）。

    前置：`kind` ∈ `EVENT_KINDS`，且**不是** `chain_dropped`（后者字段结构不同，
          请用 `encode_chain_dropped`）。
    后置：返回 `{"t": "event", "kind", "turn", "who", "target"}`；示例里 `who`/`target` 恒出现，
          为 null 时显式给出 `None`（JSON `null`），保证前端解构安全。
    抛出：`ValueError` —— `kind` 不在 `EVENT_KINDS`，或是需要专用编码器的 `chain_dropped`。
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind!r}")
    if kind == "chain_dropped":
        raise ValueError("chain_dropped must use encode_chain_dropped()")
    return {"t": "event", "kind": kind, "turn": int(turn), "who": who, "target": target}


def encode_chat(
    turn: int,
    name: str,
    color: str,
    text: str,
    system: bool = False,
    *,
    kind: str | None = None,
    target: str | None = None,
    target_color: str | None = None,
) -> dict:
    """`chat` 下行帧（PROTOCOL.md §3.8）。

    前置：
      - `system=False`：真人发言，`name`/`color` 是发言人；
      - `system=True`：系统播报，`kind` ∈ `SYSTEM_CHAT_KINDS`（R6.10 只允许三种）。
        `captured` 还需 `target`/`target_color`（被攻占者及其颜色），`surrendered`/`left`
        两者为 `None`。
      - **`text` 里不含回合号**：回合号只走 `turn` 字段，前端渲染成独立的 `.turn` 元素。
    后置：返回 `{"t","turn","name","color","text","system"}`；系统消息额外带
          `"kind"`，`captured` 再带 `"target"`/`"targetColor"`（字段顺序与
          `web/src/core/types.ts::FrameChat` 对齐，便于前端按 `kind` 渲染两个色块）。
    抛出：`ValueError` —— `system=True` 且 `kind` 不是 `SYSTEM_CHAT_KINDS` 之一。
    """
    if system and kind is not None and kind not in SYSTEM_CHAT_KINDS:
        raise ValueError(f"unknown system chat kind: {kind!r}")
    frame = {
        "t": "chat",
        "turn": int(turn),
        "name": str(name),
        "color": str(color),
        "text": str(text),
        "system": bool(system),
    }
    if system and kind is not None:
        frame["kind"] = str(kind)
        if target is not None:
            frame["target"] = str(target)
            frame["targetColor"] = str(target_color) if target_color is not None else str(color)
    return frame


def encode_autopilot(player_id: int, strategy: str | None) -> dict:
    """构造 `autopilot` 回执帧（PROTOCOL.md §3.11）。

    前置：`player_id >= 0`；`strategy` 为已注册的策略名或 `None`（取消托管）。
    后置：返回 `{"t": "autopilot", "playerId", "strategy"}`；`strategy=null` 表示人工操作。
          该帧**广播给全房间**：机器人/托管状态对所有人可见，观战者也能看出谁在托管。
    抛出：无。
    """
    return {"t": "autopilot", "playerId": int(player_id), "strategy": strategy}


def encode_game_over(winner: int | None, turn: int) -> dict:
    """`game_over` 帧（PROTOCOL.md §3.9）。

    前置：`winner` 为胜者 playerId，或无胜者时 `None`（内核 0 名有地玩家的边角情况）。
    后置：返回 `{"t": "game_over", "winner", "turn"}`。
    抛出：无。
    """
    return {"t": "game_over", "winner": None if winner is None else int(winner), "turn": int(turn)}


def encode_reject(op: str, seq: int, code: str) -> dict:
    """`reject` 帧（PROTOCOL.md §3.10）。

    前置：`code` ∈ `ERROR_CODES`；`seq` 为该指令在**上行批内**的序号（从 0 起，便于前端定位）。
    后置：返回 `{"t": "reject", "op", "seq", "code"}`。
    抛出：无（`code` 未知时原样透传，便于向前兼容新错误码）。
    """
    return {"t": "reject", "op": str(op), "seq": int(seq), "code": str(code)}


def encode_ack(op: str, seq: int) -> dict:
    """指令 `ack` 帧（保留：帧定义仍然有效，但**默认不发**）。

    # 已定(PROTOCOL §2.4.1): 保留帧但默认不发，只在 reject 时回帧。
    """
    return {"t": "ack", "op": str(op), "seq": int(seq)}


def encode_chain_dropped(
    player_id: int,
    src: int,
    direction: str,
    ratio: float,
    reason: str,
    cascade: int = 0,
) -> dict:
    """`chain_dropped` 事件帧（PROTOCOL.md §3.7 / R4.12 调兵请求作废）。

    前置：`player_id` 是请求的拥有者（`PlayerId`，与 `joined.playerId` 同源）；
          `direction` 是协议字面量 `"up"|"down"|"left"|"right"`；
          `reason` ∈ `{"src_lost", "zero_army"}`（R4.12①）；
          `cascade` ≥ 0，表示因本请求级联作废的下游请求条数（不含自己，R4.12③）。
    后置：返回 `{"t": "event", "kind": "chain_dropped", "playerId", "from", "dir",
          "ratio", "reason", "cascade"}`。
    抛出：无（`reason` 未知时原样透传，便于向前兼容新原因）。
    """
    return {
        "t": "event",
        "kind": "chain_dropped",
        "playerId": int(player_id),
        "from": int(src),
        "dir": str(direction),
        "ratio": float(ratio),
        "reason": str(reason),
        "cascade": int(cascade),
    }


def encode_pong(n: int) -> dict:
    """`pong` 帧（PROTOCOL.md §2.7 回应）。原样回显 `n`。"""
    return {"t": "pong", "n": int(n)}
