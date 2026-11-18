"""游戏内核数据结构与只读查询（GameState / Tile / PlayerState / turn_of）。

职责：定义纯数据模型 + O(1)/O(k) 查询；不含结算逻辑。
契约见 docs/INTERFACES.md §1.1。
本模块无 I/O / 时间 / 随机依赖（R7.3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from .. import config


class Terrain(IntEnum):
    """地块真实地形（R2.2）。

    - ``PLAIN``    平原
    - ``CAPITAL``  王城（仅开局时存在；被攻占后转 CITY，见 C-09）
    - ``MOUNTAIN`` 山地（不可通行，R4.9）
    - ``CITY``     城市（含"首都转化型城市"，二者逻辑等同，见 R5.4 / C-09）
    """

    PLAIN = 0
    CAPITAL = 1
    MOUNTAIN = 2
    CITY = 3


#: 是否为"城市化"地形（享受每回合 +1 增长，R3.4）—— 用整数值集合，避免枚举成员比较。
_CITY_LIKE_VALUES: frozenset[int] = frozenset(
    {int(Terrain.CAPITAL), int(Terrain.CITY)}
)


@dataclass
class Tile:
    """地块。可变结构，仅内核内部与 view 层读取。

    - ``terrain``：真实地形（R2.2）。
    - ``army``：兵力，``>= 0``（R3.1）。
    - ``owner``：控制者 player_id；``None`` = 中立（R3.1 / C-03）。
    - ``city_from_capital``：内部字段，标记"由首都转化而来的城市"（R5.4 / C-09）。
      为 ``True`` 时 ``terrain`` 必为 ``CITY``，且视野外渲染为平原（C-17）。
    """

    terrain: Terrain = Terrain.PLAIN
    army: int = 0
    owner: int | None = None
    city_from_capital: bool = False

    def is_mountain(self) -> bool:
        """真实地形是否山地（R4.9：服务端以真实地形判定，山地 = 不可通行）。"""
        return int(self.terrain) == int(Terrain.MOUNTAIN)

    def is_city_like(self) -> bool:
        """是否享受"城市级"每回合 +1 增长（R3.4：首都与城市）。"""
        return int(self.terrain) in _CITY_LIKE_VALUES

    def is_capital(self) -> bool:
        """真实地形是否王城（R5.1；被攻占后转 CITY，见 C-09）。"""
        return int(self.terrain) == int(Terrain.CAPITAL)

    def is_plain(self) -> bool:
        """真实地形是否平原（R3.4 平原增长判定）。"""
        return int(self.terrain) == int(Terrain.PLAIN)

    def is_ordinary_city(self) -> bool:
        """是否普通城市（非"首都转化型城市"），用于 C-17 视野外折叠判定。"""
        return int(self.terrain) == int(Terrain.CITY) and not self.city_from_capital


@dataclass
class PlayerState:
    """一名玩家的状态（INTERFACES §1.1）。

    - ``capital``：王城 index；王城被攻占/转化后为 ``None``（R5.1 / R5.2）。
    - ``chain``：调兵链，FIFO；每 Tick 至多消费 1 条（R4.7 / R6.3）。
      元素类型为 :class:`generals.game.commands.MoveRequest`（延迟导入避免循环依赖）。
    - ``surrender_ticks_left``：投降过渡期剩余 Tick（C-08）；``None`` = 未投降。
    """

    player_id: int
    name: str = ""
    color: str = ""
    alive: bool = True
    capital: int | None = None
    chain: list = field(default_factory=list)
    surrender_ticks_left: int | None = None

    # --- 便捷判定 ---------------------------------------------------------

    def is_surrendering(self) -> bool:
        """是否处于投降过渡期（C-08：仍增长、仍参战，只是不能操作）。"""
        return self.surrender_ticks_left is not None

    def can_operate(self) -> bool:
        """是否允许发起指令（前置条件之一，见 INTERFACES §1.3 / R5.5）。"""
        return self.alive and not self.is_surrendering()

    def star_count(self) -> int:
        """排行榜星数 = 王城数（有王城 1 星，无则 0；R5.1）。"""
        return 1 if self.capital is not None else 0


def turn_of(tick: int) -> tuple[int, int]:
    """把全局 Tick 序号映射为 ``(turn, half)``（R1.2）。

    前置：``tick >= 0``。
    后置：``tick == 0 -> (0, 0)`` 即 ``Turn 0``；``half == 0`` ⇒ ``Turn N``，
          ``half == 1`` ⇒ ``Turn N.``（视觉上带点）。
    抛出：``ValueError`` —— ``tick`` 为负。
    """
    if tick < 0:
        raise ValueError(f"tick 不能为负：{tick}")
    return (tick // config.TICKS_PER_TURN, tick % config.TICKS_PER_TURN)


@dataclass
class GameState:
    """一局游戏的权威状态（INTERFACES §1.1）。无任何模块级全局状态。"""

    size: int
    tiles: list[Tile] = field(default_factory=list)
    players: dict[int, PlayerState] = field(default_factory=dict)
    tick: int = 0
    ended: bool = False
    winner: int | None = None

    # --- 基本几何 ---------------------------------------------------------

    @property
    def tile_count(self) -> int:
        """地块总数 = ``size * size``（R2.1）。"""
        return self.size * self.size

    def index(self, row: int, col: int) -> int:
        """``(row, col) -> index``（R2.1：``index = row * size + col``）。

        前置：``0 <= row < size`` 且 ``0 <= col < size``。
        抛出：``IndexError`` —— 越界。
        """
        if not (0 <= row < self.size and 0 <= col < self.size):
            raise IndexError(f"坐标越界：row={row}, col={col}, size={self.size}")
        return row * self.size + col

    def rowcol(self, idx: int) -> tuple[int, int]:
        """``index -> (row, col)``（R2.1）。

        前置：``0 <= idx < size*size``。
        抛出：``IndexError`` —— 越界。
        """
        if not (0 <= idx < self.tile_count):
            raise IndexError(f"地块 index 越界：{idx}")
        return divmod(idx, self.size)

    def neighbours4(self, idx: int) -> list[int]:
        """4 相邻（上/下/左/右）的地块 index 列表（C-03）。

        前置：``0 <= idx < size*size``。
        后置：按 ``[上, 下, 左, 右]`` 顺序返回，越界的方向自动过滤（不 wrap-around）。
        抛出：``IndexError`` —— ``idx`` 越界。
        """
        row, col = self.rowcol(idx)
        out: list[int] = []
        if row > 0:
            out.append(idx - self.size)
        if row < self.size - 1:
            out.append(idx + self.size)
        if col > 0:
            out.append(idx - 1)
        if col < self.size - 1:
            out.append(idx + 1)
        return out

    def neighbours8(self, idx: int) -> list[int]:
        """8 相邻（含斜角）的地块 index 列表（R3.5 视野用；C-03 明确写 8 相邻才算）。

        前置：``0 <= idx < size*size``。
        后置：按行优先（上排→中排→下排，行内左→右）返回，越界方向自动过滤。
        抛出：``IndexError`` —— ``idx`` 越界。
        """
        row, col = self.rowcol(idx)
        out: list[int] = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                r, c = row + dr, col + dc
                if 0 <= r < self.size and 0 <= c < self.size:
                    out.append(r * self.size + c)
        return out

    def step_index(self, idx: int, direction: "object") -> int | None:
        """沿 ``direction`` 走一步得到的 index；越界返回 ``None``。

        ``direction`` 为 :class:`generals.game.commands.Dir`（R4.1：仅 4 方向）。
        抛出：``IndexError`` —— ``idx`` 越界。
        """
        from .commands import Dir  # 局部导入：避免 commands <-> state 循环依赖

        row, col = self.rowcol(idx)
        dr, dc = Dir(direction).delta
        r, c = row + dr, col + dc
        if 0 <= r < self.size and 0 <= c < self.size:
            return r * self.size + c
        return None

    # --- 纯查询 -----------------------------------------------------------

    def visible_tiles(self, player_id: int) -> set[int]:
        """该玩家的视野集合（R3.5 / C-03）。

        前置：无。
        后置：己方全部地块 ∪ 其 8 相邻地块；玩家已出局（``alive=False``）或不存在时返回空集。
        """
        player = self.players.get(player_id)
        if player is None or not player.alive:
            return set()
        visible: set[int] = set()
        # R7.3：按 index 升序遍历，保证不同实现得到同一集合（集合本身无序，此处只为确定性).
        for idx in range(self.tile_count):
            if self.tiles[idx].owner == player_id:
                visible.add(idx)
                visible.update(self.neighbours8(idx))
        return visible

    def army_total(self, player_id: int) -> int:
        """该玩家全部地块兵力之和（R6.8，面板 Army 列）。"""
        return sum(t.army for t in self.tiles if t.owner == player_id)

    def land_count(self, player_id: int) -> int:
        """该玩家控制地块数（C-16：含首都与城市）。"""
        return sum(1 for t in self.tiles if t.owner == player_id)

    def owned_tiles(self, player_id: int) -> list[int]:
        """该玩家控制地块 index 列表，按 index 升序（R7.3）。"""
        return [i for i, t in enumerate(self.tiles) if t.owner == player_id]

    def alive_players(self) -> list[int]:
        """``alive=True`` 的玩家 id 升序列表（R7.3）。"""
        return sorted(pid for pid, p in self.players.items() if p.alive)

    def players_with_land(self) -> list[int]:
        """``alive`` 且拥有至少 1 块地的玩家 id 升序列表（R7.4 / C-12）。"""
        return [pid for pid in self.alive_players() if self.land_count(pid) > 0]

    def leaderboard(self) -> list[dict]:
        """排行榜数据（R6.8）：按 ``(-army, -land, player_id)`` 排序。

        后置：每行 ``{"player_id", "name", "color", "army", "land", "alive", "surrendered",
        "star"}``；已出局玩家排在拥有地块的玩家之后（按 alive 降序优先）。
        """
        rows: list[dict] = []
        for pid in sorted(self.players):
            p = self.players[pid]
            rows.append(
                {
                    "player_id": pid,
                    "name": p.name,
                    "color": p.color,
                    "army": self.army_total(pid),
                    "land": self.land_count(pid),
                    "alive": p.alive,
                    "surrendered": p.is_surrendering(),
                    "star": p.star_count(),
                }
            )
        rows.sort(key=lambda r: (not r["alive"], -r["army"], -r["land"], r["player_id"]))
        return rows

    # --- 构造辅助 ---------------------------------------------------------

    @classmethod
    def empty(cls, size: int) -> "GameState":
        """创建 ``size × size`` 全平原、无玩家的空状态（供 mapgen / 测试使用）。"""
        return cls(size=size, tiles=[Tile() for _ in range(size * size)])


__all__ = ["Terrain", "Tile", "PlayerState", "GameState", "turn_of"]
