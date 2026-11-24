"""回放命令行工具：列出 / 查看 / 翻查策略调试日志 / 造样本（docs/REPLAY.md §5.2）。

用法（仓库根，用仓库的 venv 解释器）::

    .venv\\Scripts\\python.exe tools\\replay.py list
    .venv\\Scripts\\python.exe tools\\replay.py list --dir .dispatch\\replays --json
    .venv\\Scripts\\python.exe tools\\replay.py show <id>
    .venv\\Scripts\\python.exe tools\\replay.py show <id> --ai
    .venv\\Scripts\\python.exe tools\\replay.py show <id> --ai --player 0 --kinds log,error --limit 200
    .venv\\Scripts\\python.exe tools\\replay.py demo --dir .dispatch\\replays     # 造一份样本回放

设计要点
--------
* `list` / `show` 只读：不写回放、不改目录（除了没有索引时由 `ReplayStore` 自己重建一次）。
* 目录解析与服务端一致（`GENERALS_REPLAY_DIR` > `<repo>/replays`），可用 `--dir` 覆盖。
* 「对局中不可读取」同样生效：正在录制的回放 `show` 会明确报错（R8.2）。
* `demo` 起一局**离线**机器人对局（同一套内核 + 同一套录制器），产出一份带
  策略日志 / 动作 / 被拒 / 作废条目的样本，供回放页与 e2e 做视觉与功能验收。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "server") not in sys.path:
    sys.path.insert(0, str(REPO / "server"))

from generals.replay import (  # noqa: E402
    ReplayError,
    ReplayNotFound,
    ReplayRunning,
    ReplayStore,
)

#: `--kinds` 的合法取值（docs/REPLAY.md §2.2）。
AI_KINDS = ("log", "action", "error", "reject", "drop")


def _store(directory: str | None) -> ReplayStore:
    """按 `--dir` 或环境/缺省目录构造仓库（只读用）。"""
    return ReplayStore(directory) if directory else ReplayStore()


def _fmt_duration(ticks: int, tick_ms: int) -> str:
    """Tick 数 → `mm:ss`（tick_ms 缺省时按 500ms）。"""
    seconds = ticks * (tick_ms or 500) / 1000.0
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def cmd_list(args: argparse.Namespace) -> int:
    """列出回放（新的在前）。"""
    store = _store(args.dir)
    rows = store.list(limit=args.limit)
    active = store.active_ids()
    if args.json:
        print(json.dumps({"directory": str(store.directory), "active": active, "replays": rows},
                         ensure_ascii=False, indent=1))
        return 0
    print(f"目录: {store.directory}（保留 {store.keep} 份，当前 {len(rows)} 份）")
    if active:
        print(f"进行中（不可读取）: {', '.join(active)}")
    if not rows:
        print("（还没有回放）")
        return 0
    print(f"{'id':<40} {'回合':>5} {'时长':>6} {'帧':>6} {'AI':>5} {'大小':>8}  结果")
    for row in rows:
        winner = row.get("winnerName") or "-"
        reason = row.get("reason") or ""
        flag = " ⚠截断" if row.get("truncated") or row.get("aiTruncated") else ""
        print(
            f"{row['id']:<40} {row.get('turns', 0):>5} "
            f"{_fmt_duration(int(row.get('turns', 0)) * 2, int(row.get('tickMs', 500))):>6} "
            f"{row.get('frameCount', 0):>6} {row.get('aiCount', 0):>5} "
            f"{row.get('bytes', 0) / 1024:>7.1f}K  "
            f"{'ended' if row.get('ended') else reason} · 胜者 {winner}{flag}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """打印一局的概要；`--ai` 时同时打印调试条目。"""
    store = _store(args.dir)
    payload = store.load(args.id)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    result = payload.get("result") or {}
    print(f"id      : {payload.get('id')}")
    print(f"房间    : {payload.get('room')}   种子: {payload.get('seed')}   边长: {payload.get('size')}"
          f"   刻长: {payload.get('tickMs')}ms")
    print(f"时间    : {payload.get('startedAt')} → {payload.get('endedAt')}")
    print(
        f"结果    : {'自然终局' if result.get('ended') else '中断(' + str(payload.get('reason')) + ')'}"
        f"   胜者 {result.get('winner')}   回合 {result.get('turn')}"
        f"   时长 {_fmt_duration(int(result.get('tick', 0)), int(payload.get('tickMs', 500)))}"
    )
    print(f"帧/条目 : {len(payload.get('frames') or [])} 帧 / {len(payload.get('ai') or [])} 条")
    if payload.get("truncated") or payload.get("aiTruncated"):
        print("⚠ 本局触到录制上限（truncated / aiTruncated）")
    print("玩家    :")
    for row in payload.get("players") or []:
        strategy = row.get("bot") or row.get("autopilot") or "人工"
        mark = " ★" if row.get("playerId") == result.get("winner") else ""
        print(f"  [{row.get('playerId')}] seat {row.get('seat')}  {row.get('name')} "
              f"({row.get('color')})  策略={strategy}{mark}")

    counts: dict[str, int] = {}
    for entry in payload.get("ai") or []:
        counts[str(entry.get("kind"))] = counts.get(str(entry.get("kind")), 0) + 1
    if counts:
        print("AI 条目 : " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if not args.ai:
        return 0
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()} or set(AI_KINDS)
    tick_from = args.tick if args.tick is not None else -1
    tick_to = tick_from + args.window if tick_from >= 0 else None
    shown = 0
    print("\n--- AI 调试流 ---")
    for entry in payload.get("ai") or []:
        if entry.get("kind") not in kinds:
            continue
        if args.player is not None and entry.get("playerId") != args.player:
            continue
        tick = int(entry.get("tick", 0))
        if tick_from >= 0 and not (tick_from <= tick < (tick_to if tick_to is not None else tick_from + 10**9)):
            continue
        shown += 1
        if shown > args.limit:
            print(f"…（还有更多，用 --limit 放大；当前上限 {args.limit}）")
            break
        where = f"t{tick:>4}"
        who = f"P{entry.get('playerId')}" if entry.get("playerId") is not None else "P?"
        kind = entry.get("kind")
        if kind == "log":
            print(f"{where} {who} [{entry.get('level')}] {entry.get('text')}")
        elif kind == "action":
            action = entry.get("action") or {}
            if action.get("op") == "move":
                print(f"{where} {who} move {action.get('from')} {action.get('dir')} "
                      f"{action.get('ratio')}")
            else:
                print(f"{where} {who} {action.get('op')}")
        elif kind == "error":
            print(f"{where} {who} !! {entry.get('text')}")
        elif kind == "reject":
            print(f"{where} {who} reject {entry.get('op')}#{entry.get('seq')} → {entry.get('code')}")
        elif kind == "drop":
            print(f"{where} {who} drop {entry.get('from')} {entry.get('dir')} "
                  f"({entry.get('reason')}, cascade={entry.get('cascade')})")
        else:
            print(f"{where} {who} {json.dumps(entry, ensure_ascii=False)}")
    if shown == 0:
        print("（没有匹配的条目）")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """造一份样本回放：离线跑一局「会写日志的演示策略 vs greedy」，含被拒与作废条目。

    前置：`args.dir` 是目标目录（不存在会创建）。
    后置：落一份回放（自然终局，附策略日志/动作/异常/被拒/作废），打印其 id。
          供回放页视觉验收与 `web/scripts/e2e-replay.mjs` 使用（docs/REPLAY.md §5.5）。
    抛出：`ReplayError` —— 目录不可写。
    """
    from generals.ai import register
    from generals.ai.api import Action, neighbours4
    from generals.ai.debug import log
    from generals.config import DEFAULT_CONFIG
    from generals.net.room import Room

    @register("replay-demo")
    class _DemoStrategy:
        """演示策略：每刻写日志；按贪心派兵；在指定回合故意派非法指令（留 reject 痕迹）。"""

        name = "replay-demo"
        label = "演示策略（写日志）"
        description = "回放样本用：每刻写日志、贪心扩张、故意触发一次拒绝与一次作废"

        def reset(self, spec) -> None:
            self.turn_seen = -1
            self.size = spec.size
            log(f"reset: size={spec.size} players={spec.player_count}", level="info")

        def act(self, obs):
            if obs.turn != self.turn_seen:
                self.turn_seen = obs.turn
                log(f"turn {obs.turn}: army={obs.my_army} land={obs.my_land} chain={len(obs.chain)}")
            if obs.turn == 20:
                log("故意派一条越界调兵：应当被服务端拒绝（reject 留痕）", level="warn")
                return Action(op="move", src=0, dir="up", ratio=1.0)
            if obs.turn == 40:
                log("故意从一块不归自己的地块派兵：入队合法，结算时作废（drop 留痕）", level="warn")
                for tile in obs.tiles:
                    if tile.owner == obs.me or tile.terrain == 2:
                        continue
                    if neighbours4(tile.index, obs.size).get("up") is None:
                        continue
                    return Action(op="move", src=tile.index, dir="up", ratio=1.0)
            if obs.turn and obs.turn % 50 == 0 and obs.half == 0:
                log("阶段小结：继续扩张，留意对方向我方王城汇聚", level="info")
            for tile in obs.tiles:
                if tile.owner != obs.me or not tile.army or tile.army <= 1:
                    continue
                for direction, nb in neighbours4(tile.index, obs.size).items():
                    target = obs.tiles[nb]
                    if target.owner == obs.me or target.terrain == 2:
                        continue
                    return Action(op="move", src=tile.index, dir=direction, ratio=1.0)
            return None

    store = ReplayStore(args.dir, keep=10**6) if args.dir else ReplayStore(keep=10**6)
    room = Room(f"replay-demo-{args.turns}", DEFAULT_CONFIG, seed=args.seed, replay_store=store)
    room.add(None, "DemoStrategy", bot="replay-demo")
    rival = room.add(None, "Greedy", bot="greedy")
    room.start()
    for _ in range(args.turns * 2):
        if room.state.ended:
            break
        room.step_once()
        if room.state.tick == max(2, args.turns - 20):
            # 收尾：让对手投降 → 40 Tick 过渡期后自然终局（顺带产生 surrendered 播报）。
            room.handle(rival, {"t": "surrender"})
    room.close()
    rows = store.list(limit=1)
    if not rows:
        print("生成失败：没有落盘任何回放", file=sys.stderr)
        return 1
    row = rows[0]
    print(
        f"demo replay: {row['id']}\n"
        f"  目录 {store.directory}\n"
        f"  回合 {row['turns']}  帧 {row['frameCount']}  AI 条目 {row['aiCount']}  "
        f"{'自然终局' if row['ended'] else '未终局'}  胜者 {row['winnerName']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--dir", default="", help="回放目录（缺省 GENERALS_REPLAY_DIR 或 <repo>/replays）"
    )
    ap = argparse.ArgumentParser(description="对局回放工具（docs/REPLAY.md）")
    ap.add_argument("--dir", default="", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", parents=[common], help="列出回放")
    p_list.add_argument("--limit", type=int, default=30)
    p_list.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", parents=[common], help="查看一份回放")
    p_show.add_argument("id")
    p_show.add_argument("--ai", action="store_true", help="同时打印 AI 调试流")
    p_show.add_argument("--player", type=int, default=None, help="只看某个 playerId")
    p_show.add_argument("--kinds", default="", help="只看这些 kind（逗号分隔：log,action,error,reject,drop）")
    p_show.add_argument("--tick", type=int, default=None, help="起始 Tick（配合 --window）")
    p_show.add_argument("--window", type=int, default=20, help="从 --tick 起看多少刻")
    p_show.add_argument("--limit", type=int, default=500)
    p_show.add_argument("--json", action="store_true", help="直接输出整份回放 JSON")

    p_demo = sub.add_parser("demo", parents=[common], help="造一份样本回放（含策略日志/拒绝/作废）")
    p_demo.add_argument("--turns", type=int, default=220, help="打到第几回合（默认 220）")
    p_demo.add_argument("--seed", type=int, default=424242, help="地图种子（默认 424242，可复现）")

    args = ap.parse_args(argv)
    directory = args.dir or None
    try:
        if args.command == "list":
            return cmd_list(_with_dir(args, directory))
        if args.command == "demo":
            return cmd_demo(_with_dir(args, directory))
        return cmd_show(_with_dir(args, directory))
    except ReplayRunning:
        print("该回放属于一局**仍在进行**的对局：对局结束前不可读取（R8.2）。", file=sys.stderr)
        return 2
    except ReplayNotFound:
        print(f"找不到回放: {getattr(args, 'id', '?')}（目录 {directory or ReplayStore().directory}）",
              file=sys.stderr)
        return 3
    except ReplayError as exc:
        print(f"回放处理失败: {exc}", file=sys.stderr)
        return 1


def _with_dir(args: argparse.Namespace, directory: str | None) -> argparse.Namespace:
    """归一化 `--dir`（空串 → None，交给仓库按环境变量/缺省解析）。"""
    args.dir = directory
    return args


if __name__ == "__main__":
    raise SystemExit(main())