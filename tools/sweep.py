"""参数扫描台：给策略的 `P` 打一组覆盖值，跑固定对手矩阵，输出胜率与稳健性。

用法::

    .\\.venv\\Scripts\\python.exe tools\\sweep.py --set reserve_growth=0.2:0.35:0.5
    .\\.venv\\Scripts\\python.exe tools\\sweep.py --grid reserve_growth=0.2,0.35 --grid sortie_turn=20,35
    .\\.venv\\Scripts\\python.exe tools\\sweep.py --baseline           # 只跑默认参数，出对手矩阵

设计：
  * 对手矩阵固定（含"突袭型"这种最能暴露防守漏洞的对手），每人两个座位（地图无对称，R2.4）；
  * 计分 = 胜 + 1 分、平 + 0.25 分、负 0 分（平局在真人局里并不等于赢，但也不是输）；
  * 同时记录平均回合数（越短越好：真人对手不会陪你打 2000 Tick）。
"""

from __future__ import annotations

import argparse
import itertools
import statistics
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import bench  # noqa: E402
import zoo  # noqa: E402,F401  - 注册陪练
from generals.ai.loader import StrategyLoader  # noqa: E402

#: 固定对手矩阵：覆盖"贪心铺地 / 蛇形扩张 / 抢城 / 龟缩反击 / 开局冲锋 / 突袭偷家"。
OPPONENTS: tuple[str, ...] = (
    "greedy",
    "zoo-snake",
    "zoo-city",
    "zoo-turtle",
    "zoo-rush",
    "zoo-raider",
)


def evaluate(
    params: dict[str, float],
    *,
    seeds: int = 3,
    opponents: tuple[str, ...] = OPPONENTS,
    max_ticks: int = 1200,
    agent_name: str = "cosmos-lead-1v1",
) -> dict:
    """按参数打一整轮对手矩阵，返回汇总。

    前置：策略已注册，且其类属性 `P` 可被临时替换（`_P` 会被恢复）。
    后置：返回 `{score, wins, losses, draws, games, turns, per_opp}`。
    抛出：策略加载/对局异常（不吞，便于发现真 bug）。
    """
    from generals.ai import get

    cls = get(agent_name)
    original = dict(cls.P)
    cls.P = {**original, **params}
    wins = losses = draws = 0
    turns: list[float] = []
    per_opp: dict[str, str] = {}
    started = time.perf_counter()
    try:
        for opp in opponents:
            w = l = d = 0
            for seed in range(seeds):
                for seat in (0, 1):
                    result = bench.play(seed, agent_name, opp, a_seat=seat, max_ticks=max_ticks)
                    if result.winner is None:
                        d += 1
                    elif result.a_won:
                        w += 1
                        turns.append(result.ticks / 2)
                    else:
                        l += 1
            wins += w
            losses += l
            draws += d
            per_opp[opp] = f"{w}W/{l}L/{d}D"
    finally:
        cls.P = original
    games = wins + losses + draws
    score = (wins + 0.25 * draws) / games if games else 0.0
    return {
        "score": score,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "games": games,
        "turns": statistics.fmean(turns) if turns else 0.0,
        "per_opp": per_opp,
        "seconds": time.perf_counter() - started,
        "params": params,
    }


def _fmt(tag: str, result: dict) -> str:
    return (
        f"{tag:<44s} 分 {result['score']:.3f} "
        f"({result['wins']}W/{result['losses']}L/{result['draws']}D) "
        f"回合 {result['turns']:>5.0f}  {result['seconds']:>5.1f}s"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="参数扫描")
    parser.add_argument("--set", action="append", default=[], help="name=v1:v2:v3")
    parser.add_argument("--grid", action="append", default=[], help="name=v1,v2,v3（多个 grid 交叉）")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--max-ticks", type=int, default=1200)
    parser.add_argument("--agent", default="cosmos-lead-1v1")
    parser.add_argument("--baseline", action="store_true", help="只跑默认参数")
    args = parser.parse_args(argv)

    StrategyLoader(_REPO / "ai").scan()

    if args.baseline or (not args.set and not args.grid):
        result = evaluate({}, seeds=args.seeds, max_ticks=args.max_ticks, agent_name=args.agent)
        print(_fmt("baseline (默认参数)", result))
        for opp, text in result["per_opp"].items():
            print(f"    {opp:<14s} {text}")
        return 0

    axes: list[tuple[str, list[float]]] = []
    for spec in args.set:
        name, values = spec.split("=", 1)
        axes.append((name, [float(v) for v in values.split(":")]))
    for spec in args.grid:
        name, values = spec.split("=", 1)
        axes.append((name, [float(v) for v in values.split(",")]))

    # 一维扫描只沿一个轴动；多维（grid）做笛卡尔积。
    combos: list[dict[str, float]] = []
    if len(axes) == 1:
        combos = [{axes[0][0]: v} for v in axes[0][1]]
    else:
        names = [n for n, _ in axes]
        for values in itertools.product(*[vs for _, vs in axes]):
            combos.append(dict(zip(names, values)))

    rows = []
    for combo in combos:
        result = evaluate(combo, seeds=args.seeds, max_ticks=args.max_ticks, agent_name=args.agent)
        label = " ".join(f"{k}={v:g}" for k, v in combo.items()) or "baseline"
        rows.append((label, result))
        print(_fmt(label, result))

    rows.sort(key=lambda item: (-item[1]["score"], item[1]["turns"]))
    print("\n=== 前三 ===")
    for label, result in rows[:3]:
        print(_fmt(label, result))
        print("    " + "  ".join(f"{k}:{v}" for k, v in result["per_opp"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())