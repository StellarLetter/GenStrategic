"""纯游戏内核（零 I/O、零时间、零随机）：状态 + 地图生成 + 指令 + 结算 + 视野裁剪。

职责：对外只暴露本文件列出的公开符号（契约见 docs/INTERFACES.md §1）。
`game/**` 内**禁止** import fastapi / time / random 的模块级随机函数；
地图生成的随机性由调用方注入 ``random.Random``（见 :func:`mapgen.generate`）。
"""

from __future__ import annotations

from . import combat, commands, mapgen, rules, state, tick, view
from .combat import (
    BattleParticipant,
    BattleResult,
    EncounterResult,
    Flight,
    group_arrivals_by_dst,
    resolve_battle,
    resolve_encounters,
)
from .commands import (
    CHAIN_LIMIT,
    CommandError,
    Dir,
    MoveRequest,
    chain_snapshot,
    clear_chain,
    enqueue_move,
    launched_amount,
    pop_chain,
)
from .mapgen import (
    MAX_SIZE,
    MIN_SIZE,
    DEFAULT_SIZE,
    CAPITAL_MIN_MANHATTAN,
    MapGenError,
    generate,
    manhattan,
    size_for_players,
)
from .rules import (
    CaptureEvent,
    EliminationEvent,
    GrowthEvent,
    SurrenderEvent,
    apply_capture,
    apply_growth,
    begin_surrender,
    check_eliminations,
    check_game_over,
    verify_game_over,
    inherit_all,
    tick_surrender,
    transfer_to_neutral,
)
from .state import GameState, PlayerState, Terrain, Tile, turn_of
from .tick import (
    DROP_SRC_LOST,
    DROP_ZERO_ARMY,
    DroppedRequest,
    TickReport,
    launch_moves,
    step,
)
from .view import (
    TERRAIN_CITY_FROM_CAPITAL,
    TileView,
    diff_views,
    project,
    project_map,
    tile_views_to_tuples,
    visible_diff,
)

__all__ = [
    # 子模块（按 INTERFACES 的 `from generals.game import tick, view, ...` 用法）
    "state",
    "mapgen",
    "commands",
    "combat",
    "rules",
    "tick",
    "view",
    # state
    "Terrain",
    "Tile",
    "PlayerState",
    "GameState",
    "turn_of",
    # mapgen
    "generate",
    "MapGenError",
    "manhattan",
    "size_for_players",
    "CAPITAL_MIN_MANHATTAN",
    "MIN_SIZE",
    "MAX_SIZE",
    "DEFAULT_SIZE",
    # commands
    "Dir",
    "MoveRequest",
    "CommandError",
    "CHAIN_LIMIT",
    "enqueue_move",
    "clear_chain",
    "pop_chain",
    "chain_snapshot",
    "launched_amount",
    # combat
    "Flight",
    "EncounterResult",
    "BattleParticipant",
    "BattleResult",
    "resolve_encounters",
    "resolve_battle",
    "group_arrivals_by_dst",
    # rules
    "GrowthEvent",
    "CaptureEvent",
    "EliminationEvent",
    "SurrenderEvent",
    "apply_growth",
    "apply_capture",
    "inherit_all",
    "transfer_to_neutral",
    "tick_surrender",
    "check_eliminations",
    "begin_surrender",
    "check_game_over",
    "verify_game_over",
    # tick
    "TickReport",
    "DroppedRequest",
    "DROP_SRC_LOST",
    "DROP_ZERO_ARMY",
    "launch_moves",
    "step",
    # view
    "TileView",
    "TERRAIN_CITY_FROM_CAPITAL",
    "project",
    "project_map",
    "visible_diff",
    "diff_views",
    "tile_views_to_tuples",
]
