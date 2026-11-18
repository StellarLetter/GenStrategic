"""net / ai 测试共用夹具。

设计要点
--------
- **不 mock 内核**：内核已按 `docs/INTERFACES.md §1` 落地，测试直接用真实内核，
  这样 net 层对内核的使用方式（字段名、返回结构）在测试里就被锁死了。
- `FakeConn`：实现 `net.room.Connection` 协议（`async send` / `async close`），
  但它**不消费** `Seat.queue` —— 所有帧都留在队里，由测试用 `seat.drain()` 取。
  好处：同步测试（不需要事件循环）也能断言帧序列。
- `pump(room, seats)`：把若干座位队列里的帧全部取出并归并成 `[(seat, frame), ...]`。
- 注意队列上限 `QUEUE_MAXSIZE = 64`：测试里每走一步都要 `pump`，否则会被当作慢客户端踢掉。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Iterator

import pytest

# 让 `import generals.*` 在 `pytest server/tests` 直接可用（无需安装 -e）。
_SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

from generals.config import DEFAULT_CONFIG, GameConfig  # noqa: E402
from generals.net import Room, Seat  # noqa: E402

__all__ = [
    "FakeConn",
    "frame_kinds",
    "frames_of_kind",
    "make_room",
    "pump",
    "two_player_room",
]


class FakeConn:
    """测试用连接：实现 `Connection` 协议，但从不自动取走帧（队列由测试驱动）。

    前置：无。
    后置：`send()` 只把文本记进 `sent`（便于断言「Room 是否绕过队列直连」——
          按设计它**不应**被调用）。`close()` 置 `closed=True`。
    抛出：无。
    """

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: bool = False

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def close(self) -> None:
        self.closed = True


def make_room(room_id: str = "test-room", **kwargs) -> Room:
    """构造一个确定性的测试房间（固定地图种子 + 小地图，跑得快）。

    前置：`kwargs` 可含 `size` / `seed` / `config`。
    后置：返回**未开局**的 `Room`；`size` 缺省 12（保证 4 人以内首都放得下）。
    抛出：无。
    """
    kwargs.setdefault("size", 12)
    kwargs.setdefault("seed", 20261118)
    return Room(room_id, config=kwargs.pop("config", DEFAULT_CONFIG), **kwargs)


def two_player_room(room_id: str = "duo") -> tuple[Room, Seat, Seat]:
    """起一个 2 人房间并**立即开局**。

    前置：无。
    后置：返回 `(room, seat_a, seat_b)`；`room.state` 非空、`phase == RUNNING`，
          且两个座位的队列里已含 `joined` / `game_start` / 全量 `tiles` / `sight` /
          `leaderboard` / `turn`。
    抛出：无。
    """
    room = make_room(room_id)
    seat_a = room.add(FakeConn(), "Alice")
    seat_b = room.add(FakeConn(), "Bob")
    room.start()
    return room, seat_a, seat_b


def pump(*seats: Seat) -> list[tuple[Seat, dict]]:
    """把若干座位队列里的帧全部取出，返回 `[(seat, frame), ...]`（按座位、再按到达顺序）。

    前置：`seats` 均为 `Seat`。
    后置：这些座位的队列被清空（避免 64 条上限把座位踢掉）。
    抛出：无。
    """
    out: list[tuple[Seat, dict]] = []
    for seat in seats:
        for frame in seat.drain_nowait():
            out.append((seat, frame))
    return out


def frame_kinds(pairs: Iterable[tuple[Seat, dict]]) -> list[str]:
    """提取帧类型序列（按到达顺序）。"""
    return [frame["t"] for _seat, frame in pairs]


def frames_of_kind(pairs: Iterable[tuple[Seat, dict]], kind: str) -> list[dict]:
    """过滤出某类型的所有帧。"""
    return [frame for _seat, frame in pairs if frame["t"] == kind]


@pytest.fixture()
def config() -> GameConfig:
    """默认配置（tick_ms=500 / max_players=8 / chain_limit=20）。"""
    return DEFAULT_CONFIG
