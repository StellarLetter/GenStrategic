"""`net` 包公开 API 汇总。

契约见 `docs/INTERFACES.md` §2。其它模块（`transport`）只应从这里 import。

导出：
- `protocol`：帧编解码与校验（纯函数）
- `Room` / `Seat` / `RoomState` / `Connection`：房间、座位、连接抽象
- `Lobby` / `RoomSummary`：房间表
- `Session`：单连接状态机
"""

from __future__ import annotations

from . import protocol
from .lobby import PUBLIC_ROOM_PREFIX, Lobby, RoomSummary
from .room import CHAT_LOG_MAX, QUEUE_MAXSIZE, Connection, Room, RoomState, Seat
from .session import JOIN_TIMEOUT, Session

__all__ = [
    "Connection",
    "JOIN_TIMEOUT",
    "Lobby",
    "PUBLIC_ROOM_PREFIX",
    "CHAT_LOG_MAX",
    "QUEUE_MAXSIZE",
    "Room",
    "RoomState",
    "RoomSummary",
    "Seat",
    "Session",
    "protocol",
]
