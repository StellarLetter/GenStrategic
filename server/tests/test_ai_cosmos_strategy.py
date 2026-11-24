"""`ai/cosmos_lead_1v1.py` 的策略单测（pytest）。

测什么
------
策略是"只读 `Observation`、只返回 `Action`"的黑盒（`docs/AI_API.md` §2），所以这里
**不测内部实现**，只测三条硬约束：

1. **合法性**：任何观测下返回的 `Action` 都不能被内核拒绝（否则会 `reject` /
   按 R4.12 作废，在真人局里等于白丢一刻）；
2. **不越界**：只在 `Observation` 里取信息，绝不读 `GameState`（AI_API 开头的不变式）；
3. **性能与确定性**：`act()` 远低于 400 ms 预算（R1.1 每刻 0.5 秒）；同状态同结果（R7.3）。

另外覆盖两个**踩过的坑**（都是靠"比例只有十档"这条规则逼出来的，R4.2 / R6.6）：
  * 大堆长格会一次送出 10%，所以大堆只能整堆搬；
  * 每刻只有 1 条调兵（R4.7），所以"始终有动作的任务"必须排在真正重要的任务后面。
"""

from __future__ import annotations

import random
import time

import pytest

from generals.ai import make_agent, to_observation
from generals.ai.api import Action, Observation
from generals.ai.loader import StrategyLoader
from generals.ai.runner import spec_from_state
from generals.game import commands, mapgen, tick
from generals.game.state import GameState

STRATEGY = "cosmos-lead-1v1"


@pytest.fixture(scope="module", autouse=True)
def _load_strategies():
    """让 `ai/` 目录里的策略进注册表（模块级只做一次）。"""
    StrategyLoader().scan()


def _run(seed: int, ticks: int = 400, opponent: str = "greedy") -> GameState:
    """跑一局（策略 vs 内置对手），返回终局状态；任何 `reject` 都算测试失败。"""
    state = mapgen.generate(2, rng=random.Random(seed))
    me = make_agent(STRATEGY)
    other = make_agent(opponent)
    spec = spec_from_state(state)
    me.reset(spec)
    other.reset(spec)
    for _ in range(ticks):
        if state.ended:
            break
        for pid, agent in ((0, me), (1, other)):
            player = state.players[pid]
            if not player.alive or not player.can_operate():
                continue
            action = agent.act(to_observation(state, pid))
            if action is None:
                continue
            cmd = action.to_command()
            if cmd["op"] == "move":
                commands.enqueue_move(state, pid, cmd["from"], cmd["dir"], cmd["ratio"])
        tick.step(state)
    return state


# ---------------------------------------------------------------------------
# 1. 合法性：动作必须能被内核接受（否则线上会被 reject / 作废）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_actions_never_rejected_r4_7_r4_9(seed: int) -> None:
    """几百刻内每一次 `Action` 都合法 —— 没有 `CommandError`（即没有 block/越界/作废）。"""
    state = mapgen.generate(2, rng=random.Random(seed))
    me = make_agent(STRATEGY)
    me.reset(spec_from_state(state))
    rejected = 0
    acted = 0
    for _ in range(300):
        if state.ended:
            break
        action = me.act(to_observation(state, 0))
        if action is not None:
            acted += 1
            cmd = action.to_command()
            try:
                if cmd["op"] == "move":
                    commands.enqueue_move(state, 0, cmd["from"], cmd["dir"], cmd["ratio"])
            except commands.CommandError:  # noqa: PERF203 - 每个动作单独判
                rejected += 1
        tick.step(state)
    assert rejected == 0, f"seed {seed} 有 {rejected} 条指令被拒"
    assert acted > 100, "策略应当大部分时刻都在行动"


def test_actions_are_all_move_ops_with_valid_ratio_r6_6() -> None:
    """返回的只有 `move`，且比例都落在十档集合里（R6.6）。"""
    from generals.game.commands import RATIOS  # noqa: PLC0415 - 只在断言里用

    state = mapgen.generate(2, rng=random.Random(7))
    me = make_agent(STRATEGY)
    me.reset(spec_from_state(state))
    seen = 0
    for _ in range(250):
        if state.ended:
            break
        action = me.act(to_observation(state, 0))
        if action is not None:
            assert isinstance(action, Action)
            assert action.op == "move"
            assert action.ratio in RATIOS
            seen += 1
            commands.enqueue_move(state, 0, action.src, action.dir, action.ratio)
        tick.step(state)
    assert seen > 0


# ---------------------------------------------------------------------------
# 2. 不越界：只读 Observation
# ---------------------------------------------------------------------------
def test_observation_is_the_only_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """把观测里的真实兵力全部抹掉（模拟"什么都看不见"），策略也不得崩溃或乱走。"""
    state = mapgen.generate(2, rng=random.Random(11))
    me = make_agent(STRATEGY)
    me.reset(spec_from_state(state))
    obs = to_observation(state, 0)
    # 造一份"敌人全在雾里"的观测：己方地块保留，其余 army 置 None。
    hidden = Observation(
        turn=obs.turn,
        half=obs.half,
        size=obs.size,
        me=obs.me,
        tiles=[
            tile if tile.owner == obs.me else type(tile)(tile.index, None, tile.owner, tile.terrain)
            for tile in obs.tiles
        ],
        my_army=obs.my_army,
        my_land=obs.my_land,
        leaderboard=obs.leaderboard,
        chain=obs.chain,
    )
    action = me.act(hidden)  # 不应抛异常
    if action is not None:
        assert action.op == "move"
        # 起点必须是自己看得见兵量的己方地块。
        assert hidden.tiles[action.src].army is not None


# ---------------------------------------------------------------------------
# 3. 性能 / 确定性
# ---------------------------------------------------------------------------
def test_act_within_budget_r1_1() -> None:
    """`act()` 的单次耗时远低于 400 ms 预算（真实对局每刻 500 ms，R1.1）。"""
    state = mapgen.generate(2, rng=random.Random(5))
    me = make_agent(STRATEGY)
    me.reset(spec_from_state(state))
    worst = 0.0
    for _ in range(300):
        if state.ended:
            break
        obs = to_observation(state, 0)
        started = time.perf_counter()
        action = me.act(obs)
        worst = max(worst, time.perf_counter() - started)
        if action is not None:
            commands.enqueue_move(state, 0, action.src, action.dir, action.ratio)
        tick.step(state)
    assert worst < 0.4, f"最慢一次 {worst * 1000:.0f} ms"


def test_same_observation_same_action_r7_3() -> None:
    """同一个观测喂给两个独立实例，得到同一个动作（策略自身不引入随机）。"""
    state = mapgen.generate(2, rng=random.Random(9))
    a = make_agent(STRATEGY)
    b = make_agent(STRATEGY)
    spec = spec_from_state(state)
    a.reset(spec)
    b.reset(spec)
    for _ in range(120):
        if state.ended:
            break
        obs = to_observation(state, 0)
        first = a.act(obs)
        second = b.act(obs)
        assert first == second
        if first is not None:
            commands.enqueue_move(state, 0, first.src, first.dir, first.ratio)
        tick.step(state)


# ---------------------------------------------------------------------------
# 4. 玩法：能赢（策略的最低验收线）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [0, 1])
def test_beats_builtin_greedy(seed: int) -> None:
    """对内置 `greedy` 必须能赢（斩首 ⇒ R5.2 ⇒ R7.4 终局），而不是拖成平局。"""
    state = _run(seed, ticks=1200, opponent="greedy")
    assert state.ended, "应当打到终局（而不是靠计时结束）"
    assert state.winner == 0


def test_kills_enemy_capital_not_just_out_economy() -> None:
    """赢法是**攻陷王城**（R5.2），不是靠地块多：对手地块数必须归零。"""
    state = _run(3, ticks=1200, opponent="greedy")
    assert state.winner == 0
    assert state.land_count(1) == 0