"""房间表：公共房自动创建/回收、加入/离开、房间列表。

契约见 `docs/INTERFACES.md` §2.1（`Lobby` / `RoomSummary`）。

职责边界
--------
- 本模块只管「房间 id → Room」的映射与座位路由，**不含任何游戏逻辑**；
  指令处理与 Tick 时钟都在 `net.room`。
- 公共房（`room_id is None`）：优先复用有空位的 waiting/running 房间；没有则新建 `public-N`。
- 私有房（`room_id` 非空）：已存在则加入，不存在则创建该 id 的房间（PROTOCOL.md §2.1「缺省 = 公共房」、
  「room 非空即自定义房」的语义）。
- 房间回收：房间 `phase is ENDED` 且没有任何连接时从表中删除（避免无限增长）。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Final

from generals.config import DEFAULT_CONFIG, GameConfig

from . import protocol
from .room import Connection, Room, RoomState, Seat

__all__ = [
    "Lobby",
    "RoomSummary",
    "PUBLIC_ROOM_PREFIX",
]

#: 公共房 id 前缀；`public-1`、`public-2`… 逐个递增。
PUBLIC_ROOM_PREFIX: Final[str] = "public-"


@dataclass(frozen=True)
class RoomSummary:
    """`GET /api/rooms` 的一行（PROTOCOL.md §4）。

    字段与线协议的 `[{id, name, players, maxPlayers, state}]` 逐字一致；
    `Room.summary()` 直接产出同名 dict，本类供内部强类型使用。
    """

    id: str
    name: str
    players: int
    maxPlayers: int  # noqa: N815 - 线协议字段名为 camelCase，保持一致
    state: str

    def to_wire(self) -> dict:
        """转成 `GET /api/rooms` 的 JSON 行（camleCase 字段名，PROTOCOL.md §4）。

        前置：无。
        后置：返回 `{"id", "name", "players", "maxPlayers", "state"}`。
        抛出：无。
        """
        return {
            "id": self.id,
            "name": self.name,
            "players": self.players,
            "maxPlayers": self.maxPlayers,
            "state": self.state,
        }


class Lobby:
    """房间表 + 座位分配（INTERFACES §2.1）。

    前置（构造）：`config` 提供 `max_players`/`default_size` 等常量，缺省用 `DEFAULT_CONFIG`。
    后置：空表，公共房计数器从 0 开始。
    抛出：无。

    并发约束：本类所有方法都是**同步**的。它们只能在事件循环线程里调用
    （`net.session` / `transport` 均在 loop 内调用），因此不需要加锁。
    """

    def __init__(self, config: GameConfig | None = None) -> None:
        self.config: GameConfig = config or DEFAULT_CONFIG
        self._rooms: dict[str, Room] = {}
        self._counter = itertools.count(1)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def list_rooms(self) -> list[RoomSummary]:
        """列出所有房间（PROTOCOL.md §4 `/api/rooms`）。

        前置：无。
        后置：返回按 `id` 升序（R7.3 确定性）的 `RoomSummary` 列表；顺带回收空房间。
        抛出：无。
        """
        self._gc()
        return [
            RoomSummary(**room.summary())
            for room in sorted(self._rooms.values(), key=lambda r: r.id)
        ]

    def get(self, room_id: str) -> Room | None:
        """按 id 取房间。

        前置：无。
        后置：存在则返回 `Room`，否则 `None`。
        抛出：无。
        """
        return self._rooms.get(room_id)

    def rooms(self) -> list[Room]:
        """返回所有 `Room` 对象（供测试/统计；transport 层请用 `list_rooms()`）。"""
        return sorted(self._rooms.values(), key=lambda r: r.id)

    # ------------------------------------------------------------------
    # 加入 / 离开
    # ------------------------------------------------------------------
    def join(
        self,
        name: str,
        room_id: str | None = None,
        spectate: bool = False,
        *,
        conn: Connection | None = None,
        bot: str | None = None,
    ) -> tuple[Room, Seat]:
        """把一名连接（或 AI 座位）加入/创建房间并返回 `(Room, Seat)`。

        前置：
        - `name` 已由 `protocol.validate_join` 规范化（非空、≤16 字符）；
        - `room_id is None` 表示公共房。
        后置：
        - 返回的 `Room` 一定在房间表中，且 `Seat` 已在房间 `seats` 里（`joined` 帧已入该座位队列）；
        - 公共房优先复用「未结束且有玩家空位」的房间，否则新建 `public-N`；
        - 私有房不存在则新建；
        - 若座位加入后房间仍为 waiting 且玩家满员，**不自动开局**（P-01：房主手动开）。
        抛出：
        - `protocol.ProtocolError('room_full')` —— 目标房间玩家座已满或已结束。
        """
        if bot is not None:
            # 机器人名字必须已注册，否则开局后 `AIRunner` 造不出来 —— 早失败优于晚失败。
            from generals.ai import registered as _ai_registered

            if not _ai_registered(bot):
                raise protocol.ProtocolError(
                    "bad_frame", f"unknown bot strategy: {bot!r}"
                )
        room = self._resolve_room(room_id, spectate=spectate)
        seat = room.add(conn, name, spectate, bot=bot)
        return room, seat

    def leave(self, seat: Seat) -> None:
        """把座位移出房间，并在房间空置且已结束时回收房间。

        前置：`seat` 来自本 `Lobby` 的某次 `join`（也可能已离开）。
        后置：
        - 房间存在时调用 `Room.remove(seat)`（广播 `left` 事件）；
        - 房间已结束且没有任何座位时从表中删除，并 `Room.close()`。
        抛出：无（未知房间静默忽略，保证 `on_close` 幂等）。
        """
        room = self._rooms.get(seat.room_id)
        if room is None:
            return
        room.remove(seat, kicked=seat.kicked)
        self._maybe_recycle(room)

    def _maybe_recycle(self, room: Room) -> None:
        """房间已结束且无座位时回收（内部）。

        前置：`room` 可能已在表中被删除。
        后置：满足条件则 `close()` + 从表移除。
        抛出：无。
        """
        if not room.seats:
            room.close()
            self._rooms.pop(room.id, None)

    def _gc(self) -> None:
        """回收所有「已结束且无座位」的房间（内部）。

        前置：无。
        后置：表中不再含有此类房间。
        抛出：无。
        """
        for room in list(self._rooms.values()):
            if room.phase is RoomState.ENDED and not room.seats:
                room.close()
                self._rooms.pop(room.id, None)

    # ------------------------------------------------------------------
    # 房间解析
    # ------------------------------------------------------------------
    def _resolve_room(self, room_id: str | None, *, spectate: bool = False) -> Room:
        """把 `room_id` 解析成实际 `Room`（不存在则创建）。

        前置：无。
        后置：返回一个**可容纳该座位**的房间（玩家座未满或允许观战）。
        抛出：
        - `protocol.ProtocolError('room_full')` —— 显式指定的房间存在但已满/已结束；
        - `protocol.ProtocolError('room_full')` —— 公共房全部满员（不无限新建，避免刷房）。
        """
        if room_id is not None:
            room = self._rooms.get(room_id)
            if room is None:
                room = self._create(room_id)
            elif room.phase is RoomState.ENDED:
                raise protocol.ProtocolError("room_full", "room ended")
            elif not spectate and len(room.player_seats) >= room.max_players:
                raise protocol.ProtocolError("room_full", "no free player seat")
            return room

        # 公共房：复用第一个有空位的房间（按 id 升序，确定性）。
        for room in sorted(self._rooms.values(), key=lambda r: r.id):
            if room.phase is RoomState.ENDED:
                continue
            if spectate:
                return room
            if len(room.player_seats) < room.max_players:
                return room
        return self._create(f"{PUBLIC_ROOM_PREFIX}{next(self._counter)}")

    def _create(self, room_id: str) -> Room:
        """新建并登记房间（内部）。

        前置：`room_id` 不在表中。
        后置：`Room` 已登记，`state is None`（等待阶段）。
        抛出：`ValueError` —— 房间 id 重复（编程错误）。
        """
        if room_id in self._rooms:
            raise ValueError(f"room already exists: {room_id}")
        room = Room(room_id, self.config)
        self._rooms[room_id] = room
        return room
