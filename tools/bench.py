"""离线对局台：用**真实内核**跑策略互打，统计胜率/回合数/作废请求/异常耗时。

用途：策略自测与调参（不改服务端、不连网络）。
驱动方式与 `net.room.Room` 一致：每 Tick 先让各座位策略各提交至多 1 条指令
（走 `commands.*` 同一条校验路径），再 `tick.step()` 推进内核（R4.7 / R7.1）。

用法::

    .\\.venv\\Scripts\\python.exe tools\\bench.py --a cosmos --b greedy --seeds 0-19
    .\\.venv\\Scripts\\python.exe tools\\bench.py --a cosmos --b zoo-turtle --seeds 0-49 --swap
    .\\.venv\\Scripts\\python.exe tools\\bench.py --a cosmos --b greedy --seed 3 --trace

约定：
  * `--a` 总是「主策略」（统计它的胜率）；
  * `--swap` 表示同一批种子再打一局、交换座位（地图无对称，R2.4，两个座位必须都试）；
  * 结局：`state.ended` 即一方王城被攻陷（R5.2 → R7.4）；`--max-ticks` 到点未结束记 `draw`。
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

from generals.ai import make_agent, to_observation  # noqa: E402
from generals.ai.loader import StrategyLoader  # noqa: E402
from generals.ai.runner import spec_from_state  # noqa: E402
from generals.game import commands, mapgen, tick  # noqa: E402
from generals.game.state import GameState  # noqa: E402

#: 每个策略的 `act()` 单次耗时上限（与 `ai.runner.ACT_TIMEOUT` 对齐，超了算不合格）。
ACT_BUDGET_MS = 400.0

#: 一局默认最多多少 Tick（= 回合数 × 2）。
DEFAULT_MAX_TICKS = 2400

FOG_ARMED = "?"


# ----------------------------------------------------------------------
# 单局
# ----------------------------------------------------------------------
@dataclass
class MatchResult:
    """一局的结果。"""

    seed: int
    a_name: str
    b_name: str
    a_seat: int  # `--a` 策略所在的 player_id
    winner: int | None
    ticks: int
    a_army: int
    b_army: int
    a_land: int
    b_land: int
    drops: dict[int, int] = field(default_factory=dict)
    errors: dict[int, int] = field(default_factory=dict)
    max_act_ms: float = 0.0
    mean_act_ms: float = 0.0

    @property
    def a_won(self) -> bool:
        return self.winner == self.a_seat

    @property
    def outcome(self) -> str:
        if self.winner is None:
            return "draw"
        return "win" if self.a_won else "loss"


def _apply(state: GameState, pid: int, action) -> None:
    """把策略返回的 `Action` 投给内核（与 `Room._apply_command` 同一条路径）。"""
    cmd = action.to_command()
    op = cmd["op"]
    if op == "move":
        commands.enqueue_move(state, pid, cmd["from"], cmd["dir"], cmd["ratio"])
    elif op == "clear":
        commands.clear_chain(state, pid)
    elif op == "pop":
        commands.pop_chain(state, pid)


def play(
    seed: int,
    a_name: str,
    b_name: str,
    *,
    a_seat: int = 0,
    max_ticks: int = DEFAULT_MAX_TICKS,
    trace: bool = False,
    trace_every: int = 20,
) -> MatchResult:
    """跑一局 1v1，返回结果。

    前置：`a_name` / `b_name` 已注册；`a_seat in (0, 1)`。
    后置：跑满 `max_ticks` 或对局结束；异常/作废请求计入结果。
    抛出：`mapgen.MapGenError`（种子摆不下首都）。
    """
    state = mapgen.generate(2, rng=random.Random(seed))
    names = {a_seat: a_name, 1 - a_seat: b_name}
    agents = {}
    for pid in sorted(state.players):
        agents[pid] = make_agent(names[pid])
    spec = spec_from_state(state)
    for agent in agents.values():
        agent.reset(spec)

    errors = {pid: 0 for pid in agents}
    drops = {pid: 0 for pid in agents}
    act_ms: list[float] = []

    while not state.ended and state.tick < max_ticks:
        for pid in sorted(state.players):
            player = state.players[pid]
            if not player.alive or not player.can_operate():
                continue
            obs = to_observation(state, pid)
            started = time.perf_counter()
            try:
                action = agents[pid].act(obs)
            except Exception as exc:  # noqa: BLE001 - 策略 bug 不该中断台架
                errors[pid] += 1
                action = None
                if trace:
                    print(f"[seed {seed}] act 异常 pid={pid}: {type(exc).__name__}: {exc}")
            act_ms.append((time.perf_counter() - started) * 1000.0)
            if action is None:
                continue
            try:
                _apply(state, pid, action)
            except commands.CommandError as exc:
                errors[pid] += 1
                if trace:
                    print(f"[seed {seed}] 指令被拒 pid={pid}: {exc.code} {action}")
        report = tick.step(state)
        for item in report.dropped:
            drops[item.player_id] = drops.get(item.player_id, 0) + 1
            if trace:
                print(
                    f"[seed {seed}] 请求作废 pid={item.player_id} src={item.src} "
                    f"reason={item.reason} cascade={item.cascade}"
                )
        if trace and state.tick % trace_every == 0:
            print(render(state, seed=seed, focus=a_seat))

    a_tiles = state.land_count(a_seat)
    b_tiles = state.land_count(1 - a_seat)
    return MatchResult(
        seed=seed,
        a_name=a_name,
        b_name=b_name,
        a_seat=a_seat,
        winner=state.winner,
        ticks=state.tick,
        a_army=state.army_total(a_seat),
        b_army=state.army_total(1 - a_seat),
        a_land=a_tiles,
        b_land=b_tiles,
        drops=drops,
        errors=errors,
        max_act_ms=max(act_ms) if act_ms else 0.0,
        mean_act_ms=statistics.fmean(act_ms) if act_ms else 0.0,
    )


# ----------------------------------------------------------------------
# 文本棋盘（调试用）
# ----------------------------------------------------------------------
def render(state: GameState, *, seed: int = 0, focus: int = 0) -> str:
    """把权威状态画成等宽文本（调试用；AI 自己看不到这些信息）。"""
    size = state.size
    lines = [f"--- seed {seed} tick {state.tick} (Turn {state.tick // 2}) ---"]
    for row in range(size):
        cells = []
        for col in range(size):
            tile = state.tiles[row * size + col]
            if tile.is_mountain():
                cells.append("  ##  ")
                continue
            owner = "." if tile.owner is None else str(tile.owner)
            mark = {0: "P", 1: "C", 2: "#", 3: "$"}[int(tile.terrain)]
            cells.append(f"{owner}{mark}{tile.army:>3d} ")
        lines.append("".join(cells).rstrip())
    rows = []
    for pid in sorted(state.players):
        player = state.players[pid]
        rows.append(
            f"P{pid} army={state.army_total(pid)} land={state.land_count(pid)} "
            f"cap={player.capital} alive={player.alive}"
        )
    lines.append("  ".join(rows) + f"  focus=P{focus} winner={state.winner}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 批量
# ----------------------------------------------------------------------
def parse_seeds(text: str) -> list[int]:
    """解析 `0-9,20,31` 形式的种子列表。"""
    out: list[int] = []
    for chunk in text.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(chunk))
    return out


def summary(results: list[MatchResult]) -> str:
    """把一批对局汇总成一行表格文本。"""
    if not results:
        return "（无对局）"
    wins = sum(1 for r in results if r.a_won)
    draws = sum(1 for r in results if r.winner is None)
    losses = len(results) - wins - draws
    turns = [r.ticks / 2 for r in results if r.winner is not None]
    drops = sum(sum(r.drops.values()) for r in results)
    errors = sum(sum(r.errors.values()) for r in results)
    max_ms = max(r.max_act_ms for r in results)
    mean_ms = statistics.fmean(r.mean_act_ms for r in results)
    head = (
        f"{results[0].a_name} vs {results[0].b_name}: "
        f"{len(results)} 局 → 胜 {wins} / 负 {losses} / 平 {draws} "
        f"(胜率 {wins / len(results):.1%})"
    )
    detail = (
        f"    回合数 中位 {statistics.median(turns):.0f}"
        if turns
        else "    回合数 -"
    )
    return (
        f"{head}\n{detail}  最长 {max(turns):.0f}" if turns else head
    ) + (
        f"\n    作废请求 {drops} 次，异常 {errors} 次，"
        f"act 平均 {mean_ms:.2f}ms / 峰值 {max_ms:.1f}ms"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线对局台")
    parser.add_argument("--a", default="cosmos", help="主策略名（统计它的胜率）")
    parser.add_argument("--b", default="greedy", help="对手策略名")
    parser.add_argument("--seeds", default="0-19", help="种子列表，如 0-19,30")
    parser.add_argument("--max-ticks", type=int, default=DEFAULT_MAX_TICKS)
    parser.add_argument("--swap", action="store_true", help="同种子再打一局交换座位")
    parser.add_argument("--seed", type=int, default=None, help="只跑单个种子（调试）")
    parser.add_argument("--trace", action="store_true", help="打印棋盘与事件")
    parser.add_argument("--trace-every", type=int, default=20)
    parser.add_argument("--quiet", action="store_true", help="只输出汇总")
    args = parser.parse_args(argv)

    StrategyLoader(_REPO / "ai").scan()
    import zoo  # noqa: F401  - 注册自研对手（tools/zoo.py，不属于策略目录）

    if args.seed is not None:
        result = play(
            args.seed,
            args.a,
            args.b,
            max_ticks=args.max_ticks,
            trace=True,
            trace_every=args.trace_every,
        )
        print(summary([result]))
        print(f"座位: P{result.a_seat} = {args.a}  P{1 - result.a_seat} = {args.b}")
        print(f"{args.a}: army {result.a_army} land {result.a_land}")
        print(f"{args.b}: army {result.b_army} land {result.b_land}")
        return 0

    seeds = parse_seeds(args.seeds)
    results: list[MatchResult] = []
    for seed in seeds:
        seats = (0, 1) if args.swap else (0,)
        for seat in seats:
            result = play(
                seed,
                args.a,
                args.b,
                a_seat=seat,
                max_ticks=args.max_ticks,
                trace=args.trace,
                trace_every=args.trace_every,
            )
            results.append(result)
            if not args.quiet:
                print(
                    f"seed {seed:>3d} P{seat}: {result.outcome:<5s} "
                    f"ticks={result.ticks:<5d} army={result.a_army:<4d}/{result.b_army:<4d} "
                    f"land={result.a_land:<4d}/{result.b_land:<4d}"
                )
    print(summary(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())