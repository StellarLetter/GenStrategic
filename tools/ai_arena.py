"""1v1 离线对战基准（自建，只依赖内核与 AI 接口，不读任何其它策略源码）。

用法示例：
    python .dispatch/lead/arena.py --a greedy --b sample-greedy --games 40 --swap
    python .dispatch/lead/arena.py --a aegis --b greedy,random,sample-greedy --games 30 --workers 14
    python .dispatch/lead/arena.py --a aegis --b greedy --games 1 --trace
    python .dispatch/lead/arena.py --a aegis --b cosmos-apex --games 20 --replay-dir .dispatch\\replays

设计要点
--------
* 与线上完全同构的决策顺序：每 Tick **先**让两个座位各 `act()` 一次并入队，**再** `tick.step()`。
* 指令走 `game.commands.enqueue_move` 这条真人与 AI 共用的校验路径；非法/被拒都计入 `rejects`。
* 策略异常计入 `errors`（线上会被吞掉当"本刻不操作"，这里同样不中断对局）。
* 地图由 `mapgen.generate(player_count=2, rng=Random(seed))` 生成 ⇒ 边长按 R2.3 公式 12..18。
* `--replay-dir <目录>` 时每局都留一份回放（格式与线上完全一致，见 `docs/REPLAY.md`）：
  策略的 `generals.ai.log()`、动作、被拒指令、异常都会进去，事后可用 `tools/replay.py` 复盘。
  多进程写同一目录是安全的（各 worker 原子写自己的文件、不碰 `index.json`，主进程收尾重建索引）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
import traceback
from dataclasses import dataclass, field
from multiprocessing import Pool
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "server") not in sys.path:
    sys.path.insert(0, str(REPO / "server"))

from generals.ai import make_agent, to_observation  # noqa: E402
from generals.ai.debug import capture  # noqa: E402
from generals.ai.loader import StrategyLoader  # noqa: E402
from generals.ai.runner import spec_from_state  # noqa: E402
from generals.game import commands, mapgen, tick  # noqa: E402
from generals.game.commands import CommandError  # noqa: E402
from generals.replay import ReplayStore  # noqa: E402

_READY = {"done": False}


def load_strategies(extra: tuple[str, ...] = ()) -> dict[str, str]:
    """挂载 `ai/` 目录 + 额外模块；返回 `{name: source}`。"""
    if not _READY["done"]:
        loader = StrategyLoader()
        report = loader.scan()
        for p in extra:
            path = Path(p)
            spec = importlib.util.spec_from_file_location(f"extra_{path.stem}", path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            assert spec and spec.loader
            spec.loader.exec_module(mod)
        _READY["done"] = True
        return {
            "errors": [f"{e.get('file')}: {e.get('error')}" for e in report.errors],
            "files": list(report.files),
        }
    return {}


# ---------------------------------------------------------------------------
# 单场对局
# ---------------------------------------------------------------------------


@dataclass
class MatchResult:
    a: str
    b: str
    seed: int
    size: int
    winner: int | None = None
    ended: bool = False
    ticks: int = 0
    reason: str = "timeout"  # capital | land | timeout
    cap_a: int | None = None  # 0/1 谁被攻陷王城
    end_capital: int | None = None
    army: tuple[int, int] = (0, 0)
    land: tuple[int, int] = (0, 0)
    cities: tuple[int, int] = (0, 0)
    rejects: tuple[int, int] = (0, 0)
    errors: tuple[int, int] = (0, 0)
    err_sample: tuple[str, str] = ("", "")
    max_ms: tuple[float, float] = (0.0, 0.0)
    timeline: list[list[int]] = field(default_factory=list)
    #: `--replay-dir` 生效时的回放 id（空串 = 本局没录回放，见 docs/REPLAY.md）。
    replay_id: str = ""

    @property
    def winner_name(self) -> str | None:
        if self.winner is None:
            return None
        return self.a if self.winner == 0 else self.b


def _count_cities(state: Any, pid: int) -> int:
    return sum(1 for t in state.tiles if t.owner == pid and t.is_city_like())


def run_match(job: tuple[str, str, int, int, bool, tuple[str, ...], str]) -> MatchResult:
    a_name, b_name, seed, max_ticks, trace, extra, replay_dir = job
    load_strategies(extra)

    state = mapgen.generate(player_count=2, rng=random.Random(seed))
    res = MatchResult(a=a_name, b=b_name, seed=seed, size=state.size)

    # 回放（可选）：与线上同一套 `ReplayStore` / `ReplayRecorder`（docs/REPLAY.md）。
    # 多进程各写各的文件，因此关掉索引维护；主进程收尾时 `rebuild()` 一次。
    recorder = None
    store = None
    if replay_dir:
        store = ReplayStore(replay_dir, keep=10**9, index_enabled=False)
        recorder = store.begin(
            room_id=f"arena-{a_name}-vs-{b_name}-{seed}",
            seed=seed,
            size=state.size,
            tick_ms=500,
            players=[
                {"playerId": 0, "seat": 0, "name": a_name, "color": "red", "bot": a_name,
                 "autopilot": None},
                {"playerId": 1, "seat": 1, "name": b_name, "color": "blue", "bot": b_name,
                 "autopilot": None},
            ],
        )
        if recorder is not None:
            recorder.attach(state)
            recorder.record_open(state)

    agents: dict[int, Any] = {}
    for pid, name in ((0, a_name), (1, b_name)):
        try:
            agents[pid] = make_agent(name)
        except Exception as exc:  # noqa: BLE001
            res.errors = (999, 999) if pid == 0 else (res.errors[0], 999)
            res.err_sample = (res.err_sample[0] or f"{type(exc).__name__}: {exc}", res.err_sample[1])
            return res

    spec = spec_from_state(state)
    names = {0: a_name, 1: b_name}
    for pid in (0, 1):
        try:
            with capture(_sink(recorder, pid, names[pid])):
                agents[pid].reset(spec)
        except Exception as exc:  # noqa: BLE001
            if recorder is not None:
                recorder.record_ai_error(pid, error=exc, seat=pid, strategy=names[pid])
            res.err_sample = (
                res.err_sample[0] or f"reset {type(exc).__name__}: {exc}"
            ) if pid == 0 else (res.err_sample[0], f"reset {type(exc).__name__}: {exc}")

    rejects = [0, 0]
    errors = [0, 0]
    errs = ["", ""]
    max_ms = [0.0, 0.0]
    deadline = max_ticks

    while not state.ended and state.tick < deadline:
        for pid in (0, 1):
            player = state.players[pid]
            if not player.can_operate():
                continue
            t0 = time.perf_counter()
            try:
                obs = to_observation(state, pid)
                with capture(_sink(recorder, pid, names[pid])):
                    action = agents[pid].act(obs)
            except Exception as exc:  # noqa: BLE001
                errors[pid] += 1
                if not errs[pid]:
                    errs[pid] = f"{type(exc).__name__}: {exc}"
                if recorder is not None:
                    recorder.record_ai_error(pid, error=exc, seat=pid, strategy=names[pid])
                continue
            finally:
                dt = (time.perf_counter() - t0) * 1000.0
                if dt > max_ms[pid]:
                    max_ms[pid] = dt
            if action is None:
                continue
            try:
                cmd = action.to_command()
            except Exception as exc:  # noqa: BLE001
                rejects[pid] += 1
                if not errs[pid]:
                    errs[pid] = f"bad action: {exc}"
                if recorder is not None:
                    recorder.record_ai_error(pid, error=exc, seat=pid, strategy=names[pid])
                continue
            if recorder is not None:
                recorder.record_ai_action(pid, action=cmd, seat=pid, strategy=names[pid])
            try:
                if cmd["op"] == "move":
                    commands.enqueue_move(state, pid, cmd["from"], cmd["dir"], cmd["ratio"])
                elif cmd["op"] == "clear":
                    commands.clear_chain(state, pid)
                elif cmd["op"] == "pop":
                    commands.pop_chain(state, pid)
            except CommandError as exc:
                rejects[pid] += 1
                if recorder is not None:
                    recorder.record_reject(
                        pid, op=str(cmd.get("op", "")), seq=0, code=getattr(exc, "code", "bad_frame")
                    )
            except Exception as exc:  # noqa: BLE001
                errors[pid] += 1
                if not errs[pid]:
                    errs[pid] = f"{type(exc).__name__}: {exc}"
                if recorder is not None:
                    recorder.record_ai_error(pid, error=exc, seat=pid, strategy=names[pid])
        report = tick.step(state)
        if recorder is not None:
            recorder.record_tick(state)
        if trace and (state.tick % 10 == 0 or state.ended):
            res.timeline.append(
                [
                    state.tick,
                    state.army_total(0),
                    state.army_total(1),
                    state.land_count(0),
                    state.land_count(1),
                    _count_cities(state, 0),
                    _count_cities(state, 1),
                ]
            )
        for cap in report.captured_capitals():
            if res.cap_a is None:
                res.cap_a = cap.new_owner

    res.ended = state.ended
    res.ticks = state.tick
    res.winner = state.winner
    res.army = (state.army_total(0), state.army_total(1))
    res.land = (state.land_count(0), state.land_count(1))
    res.cities = (_count_cities(state, 0), _count_cities(state, 1))
    res.rejects = (rejects[0], rejects[1])
    res.errors = (errors[0], errors[1])
    res.err_sample = (errs[0], errs[1])
    res.max_ms = (max_ms[0], max_ms[1])
    if res.cap_a is not None:
        res.reason = "capital"
        res.end_capital = res.cap_a
    elif state.ended:
        res.reason = "land"
    else:
        res.reason = "timeout"
    for pid in (0, 1):
        try:
            close = getattr(agents[pid], "close", None)
            if callable(close):
                close()
        except Exception:  # noqa: BLE001
            pass
    if store is not None and recorder is not None:
        if state.ended:
            recorder.record_close(state)
        res.replay_id = store.save(recorder, reason="ended" if state.ended else "timeout")["id"]
    return res


def _sink(recorder: Any, pid: int, strategy: str) -> Any:
    """给某个座位构造日志 sink（`recorder is None` 时返回 `None` = 静默丢弃）。

    前置：`recorder` 为 `ReplayRecorder` 或 `None`。
    后置：返回可交给 `generals.ai.debug.capture()` 的上下文管理器（`None` 亦可）。
    抛出：无。
    """
    if recorder is None:
        return None

    def sink(level: str, text: str) -> None:
        recorder.record_ai_log(pid, text=text, level=level, seat=pid, strategy=strategy)

    return sink


# ---------------------------------------------------------------------------
# 批量与统计
# ---------------------------------------------------------------------------


def plan_jobs(pairs: list[tuple[str, str]], games: int, seed0: int, swap: bool, max_ticks: int, trace: bool, extra: tuple[str, ...], replay_dir: str = ""):
    jobs = []
    for a, b in pairs:
        for i in range(games):
            seed = seed0 + i
            jobs.append((a, b, seed, max_ticks, trace, extra, replay_dir))
            if swap:
                jobs.append((b, a, seed + 100000, max_ticks, trace, extra, replay_dir))
    return jobs


def summarize(results: list[MatchResult], a: str, b: str) -> dict[str, Any]:
    wins = {a: 0, b: 0, "draw": 0}
    ticks = []
    for r in results:
        if r.winner_name == a:
            wins[a] += 1
        elif r.winner_name == b:
            wins[b] += 1
        else:
            wins["draw"] += 1
        ticks.append(r.ticks)
    total = len(results)
    return {
        "a": a,
        "b": b,
        "games": total,
        "wins_a": wins[a],
        "wins_b": wins[b],
        "draws": wins["draw"],
        "score_a": round((wins[a] + 0.5 * wins["draw"]) / total, 4) if total else 0.0,
        "median_ticks": sorted(ticks)[len(ticks) // 2] if ticks else 0,
        "mean_ticks": round(sum(ticks) / len(ticks), 1) if ticks else 0,
        "rejects_a": sum(r.rejects[0] for r in results),
        "rejects_b": sum(r.rejects[1] for r in results),
        "errors_a": sum(r.errors[0] for r in results),
        "errors_b": sum(r.errors[1] for r in results),
        "err_sample_a": next((r.err_sample[0] for r in results if r.err_sample[0]), ""),
        "err_sample_b": next((r.err_sample[1] for r in results if r.err_sample[1]), ""),
        "max_ms_a": round(max((r.max_ms[0] for r in results), default=0.0), 1),
        "max_ms_b": round(max((r.max_ms[1] for r in results), default=0.0), 1),
        "end_capital": sum(1 for r in results if r.reason == "capital"),
        "end_land": sum(1 for r in results if r.reason == "land"),
        "end_timeout": sum(1 for r in results if r.reason == "timeout"),
        "mean_size": round(sum(r.size for r in results) / total, 2) if total else 0,
        "cap_by_a": sum(1 for r in results if r.cap_a == 1),
        "cap_by_b": sum(1 for r in results if r.cap_a == 0),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="A 方策略名，逗号分隔")
    ap.add_argument("--b", required=True, help="B 方策略名，逗号分隔")
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed0", type=int, default=1000)
    ap.add_argument("--max-ticks", type=int, default=600)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--swap", action="store_true", help="同一种子换座位再打一局")
    ap.add_argument("--trace", action="store_true", help="记录每 10 Tick 的态势")
    ap.add_argument("--extra", default="", help="额外策略模块路径（逗号分隔）")
    ap.add_argument("--json", default="", help="把明细写到该 json")
    ap.add_argument(
        "--replay-dir",
        default="",
        help="每局都录一份回放到这个目录（格式同线上，见 docs/REPLAY.md；用 tools/replay.py 复盘）",
    )
    args = ap.parse_args(argv)

    extra = tuple(p for p in args.extra.split(",") if p)
    info = load_strategies(extra)
    if info.get("errors"):
        print("[loader] errors:", info["errors"])

    alist = [s for s in args.a.split(",") if s]
    blist = [s for s in args.b.split(",") if s]
    pairs = [(a, b) for a in alist for b in blist]
    jobs = plan_jobs(pairs, args.games, args.seed0, args.swap, args.max_ticks, args.trace, extra, args.replay_dir)

    t0 = time.perf_counter()
    results: list[MatchResult] = []
    if args.workers <= 1:
        for job in jobs:
            results.append(run_match(job))
    else:
        with Pool(min(args.workers, 20)) as pool:
            for r in pool.imap_unordered(run_match, jobs, chunksize=1):
                results.append(r)
    dt = time.perf_counter() - t0

    rows = []
    for a, b in pairs:
        sub = [r for r in results if (r.a == a and r.b == b) or (r.a == b and r.b == a)]
        norm = []
        for r in sub:
            if r.a == a:
                norm.append(r)
            else:  # 反向：交换视角
                norm.append(
                    MatchResult(
                        a=a,
                        b=b,
                        seed=r.seed,
                        size=r.size,
                        winner=None if r.winner is None else 1 - r.winner,
                        ended=r.ended,
                        ticks=r.ticks,
                        reason=r.reason,
                        cap_a=None if r.cap_a is None else 1 - r.cap_a,
                        army=(r.army[1], r.army[0]),
                        land=(r.land[1], r.land[0]),
                        cities=(r.cities[1], r.cities[0]),
                        rejects=(r.rejects[1], r.rejects[0]),
                        errors=(r.errors[1], r.errors[0]),
                        err_sample=(r.err_sample[1], r.err_sample[0]),
                        max_ms=(r.max_ms[1], r.max_ms[0]),
                    )
                )
        row = summarize(norm, a, b)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    print(f"[arena] {len(jobs)} games in {dt:.1f}s ({dt / max(1, len(jobs)):.3f}s/game, workers={args.workers})")
    if args.replay_dir:
        # 多进程各写各的回放文件、不维护索引；这里统一重建一次（docs/REPLAY.md §3）。
        rows_now = ReplayStore(args.replay_dir).rebuild()
        print(f"[arena] {len(rows_now)} replays -> {Path(args.replay_dir).resolve()}"
              f"（用 tools/replay.py list 查看）")
    if args.trace:
        for r in results:
            print(f"--- {r.a} vs {r.b} seed={r.seed} size={r.size} winner={r.winner_name} {r.reason}@{r.ticks}")
            for row in r.timeline:
                print("   tick/armyA/armyB/landA/landB/cityA/cityB:", row)
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "rows": rows,
                    "matches": [
                        {k: v for k, v in r.__dict__.items() if k != "timeline"} for r in results
                    ],
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"[arena] details -> {args.json}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
