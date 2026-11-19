"""全局常量：刻长、房间上限、地图尺寸、调兵链上限等。

职责：集中放"可读的魔法数字"，其它模块只引用这里，不要各自硬编码。
契约见 docs/INTERFACES.md §1（内核侧常量）与 docs/PROTOCOL.md §4。
本模块是纯数据，无任何 I/O / 时间 / 随机依赖（R7.3）。
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 时间（R1.1 / R1.3）
# ---------------------------------------------------------------------------

#: 一个 Tick（半回合）的毫秒数。1 回合 = 1 秒 = 2 Tick（R1.3）。
TICK_MS: int = 500

#: 一回合包含的 Tick 数（R1.1）。turn_of(tick) 的除数。
TICKS_PER_TURN: int = 2

# ---------------------------------------------------------------------------
# 地图（R2.1 / INTERFACES §1.2）
# ---------------------------------------------------------------------------

#: 地图边长下限（R2.3 clamp 下界）。
MIN_SIZE: int = 10
#: 地图边长上限（R2.3 clamp 上界）。
MAX_SIZE: int = 40
#: 默认地图边长（仅供参考/UI 展示；**开局边长一律由 R2.3 公式算出**）。
DEFAULT_SIZE: int = 20
#: 一局最多玩家数（PlayerId ∈ 0..7，见 INTERFACES 约定）。
MAX_PLAYERS: int = 8

# ---------------------------------------------------------------------------
# 地图边长公式（R2.3）：size = clamp(SIZE_BASE + SIZE_PER_PLAYER*N + rand(0..JITTER), MIN, MAX)
# ---------------------------------------------------------------------------

#: R2.3 公式的常数项。
SIZE_BASE: int = 6
#: R2.3 公式里每名玩家贡献的边长。
SIZE_PER_PLAYER: int = 3
#: R2.3 公式里随机抖动的上界（含），取自地图种子：rand(0..SIZE_JITTER_MAX)。
SIZE_JITTER_MAX: int = 6

#: 地图生成：山地占全球块的比例（R2.7，参考原站，约 20%）。
MAPGEN_MOUNTAIN_RATIO: float = 0.20
#: 地图生成：城市（不含首都）占全球块的比例（R2.7，参考原站，约 4%）。
MAPGEN_CITY_RATIO: float = 0.04
#: 地图生成：首都周围 2 格（8 邻两步）内不放山地的半径（R2.7，避免被闷死）。
MAPGEN_CAPITAL_CLEAR_RADIUS: int = 2
#: 首都之间的最小**曼哈顿**距离（R2.5：L1 距离，4 相邻步数）。
#: 注意：这是曼哈顿距离，与切比雪夫距离不等价，不要复用切比雪夫工具函数。
CAPITAL_MIN_MANHATTAN: int = 13
#: 首都摆放放不下时的换种子重试上限（R2.6）；全部失败抛 MapGenError。
MAPGEN_MAX_ATTEMPTS: int = 64

# ---------------------------------------------------------------------------
# 兵力（R3.2 / R3.3 / R3.4）
# ---------------------------------------------------------------------------

#: 首都初始兵力（R3.2）。
CAPITAL_START_ARMY: int = 1
#: 城市/首都中立兵力下界（R3.3）。
CITY_NEUTRAL_ARMY_MIN: int = 40
#: 城市/首都中立兵力上界（R3.3）。
CITY_NEUTRAL_ARMY_MAX: int = 50
#: 首都与城市每回合增长量（R3.4 / R3.7）。
CITY_GROWTH_PER_TURN: int = 1
#: 平原增长周期：每 25 回合 +1，首次刷新在 Turn 25.（R3.4 / R3.7）。
PLAIN_GROWTH_PERIOD: int = 25
#: 平原每次刷新增长量（R3.4）。
PLAIN_GROWTH_AMOUNT: int = 1

# ---------------------------------------------------------------------------
# 停止抵抗 / 投降（R5.5）
# ---------------------------------------------------------------------------

#: 投降过渡期长度（Tick）（R5.5）。
SURRENDER_TICKS: int = 40
#: 王城继承时每块地保留的兵力下限（R5.2：max(army // 2, 1)）。
INHERIT_MIN_ARMY: int = 1

# ---------------------------------------------------------------------------
# 调兵链（R4.7 / R6.3 / INTERFACES §1.3）
# ---------------------------------------------------------------------------

#: 单名玩家的调兵链上限；超出抛 CommandError('chain_full')。
CHAIN_LIMIT: int = 20
#: 调兵比例默认值（R6.6）。
DEFAULT_RATIO: float = 1.0
#: 调兵比例下界 / 上界（R6.6：10%–100%，`+`/`-` 步长 10%）。
RATIO_MIN: float = 0.1
RATIO_MAX: float = 1.0

# ---------------------------------------------------------------------------
# 房间（net 层读取；内核不依赖）
# ---------------------------------------------------------------------------

#: 单房间最大连接数（含观战者）。
ROOM_MAX_CONNECTIONS: int = 32
#: 公共房自动回收阈值（Tick）。
ROOM_IDLE_END_TICKS: int = 600

#: 玩家颜色，顺序与参考截图排行榜一致（PROTOCOL §1 / net.protocol.COLORS）。
COLORS: tuple[str, ...] = (
    "red",
    "blue",
    "green",
    "teal",
    "purple",
    "pink",
    "maroon",
    "orange",
)

# ---------------------------------------------------------------------------
# 冻结配置对象（lead 补充契约：net / ai 层统一读这个）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GameConfig:
    """一局对局的冻结参数表。全部字段与模块级常量同源，避免两处漂移。

    ``game/**`` 内部只直接读模块级常量；``net`` / ``ai`` 需要"可传参的配置"时用本类
    （例如房间创建时按房间参数覆盖地图尺寸）。
    """

    tick_ms: int = TICK_MS  # 1 Tick = 500ms（R1.1）
    tick_per_second: int = TICKS_PER_TURN  # 每秒 2 Tick（R1.3）
    max_players: int = MAX_PLAYERS
    default_size: int = DEFAULT_SIZE
    min_size: int = MIN_SIZE
    max_size: int = MAX_SIZE
    chain_limit: int = CHAIN_LIMIT  # 调兵链上限（R4.7）
    surrender_ticks: int = SURRENDER_TICKS  # 投降过渡期（R5.5）
    capital_army: int = CAPITAL_START_ARMY  # R3.2
    city_army_min: int = CITY_NEUTRAL_ARMY_MIN  # R3.3
    city_army_max: int = CITY_NEUTRAL_ARMY_MAX  # R3.3
    plain_growth_period: int = PLAIN_GROWTH_PERIOD  # R3.4 / R3.7
    grow_per_tick: int = CITY_GROWTH_PER_TURN  # R3.4（城市/首都每回合 +1）

    def clamp_size(self, size: int) -> int:
        """把请求的地图边长夹到 ``[min_size, max_size]``（R2.1）。"""
        return max(self.min_size, min(self.max_size, int(size)))

    def clamp_players(self, player_count: int) -> int:
        """把请求的玩家数夹到 ``[1, max_players]``（PlayerId ∈ 0..7）。"""
        return max(1, min(self.max_players, int(player_count)))


#: 默认配置（绝大多数调用直接用这个）。
DEFAULT_CONFIG: GameConfig = GameConfig()

__all__ = [
    "GameConfig",
    "DEFAULT_CONFIG",
    "TICK_MS",
    "TICKS_PER_TURN",
    "MIN_SIZE",
    "MAX_SIZE",
    "DEFAULT_SIZE",
    "MAX_PLAYERS",
    "SIZE_BASE",
    "SIZE_PER_PLAYER",
    "SIZE_JITTER_MAX",
    "MAPGEN_MOUNTAIN_RATIO",
    "MAPGEN_CITY_RATIO",
    "MAPGEN_CAPITAL_CLEAR_RADIUS",
    "CAPITAL_MIN_MANHATTAN",
    "MAPGEN_MAX_ATTEMPTS",
    "CAPITAL_START_ARMY",
    "CITY_NEUTRAL_ARMY_MIN",
    "CITY_NEUTRAL_ARMY_MAX",
    "CITY_GROWTH_PER_TURN",
    "PLAIN_GROWTH_PERIOD",
    "PLAIN_GROWTH_AMOUNT",
    "SURRENDER_TICKS",
    "INHERIT_MIN_ARMY",
    "CHAIN_LIMIT",
    "DEFAULT_RATIO",
    "ROOM_MAX_CONNECTIONS",
    "ROOM_IDLE_END_TICKS",
    "COLORS",
]
