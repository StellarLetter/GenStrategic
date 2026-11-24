"""回放命令行工具：列出 / 查看 / 翻查策略调试日志（docs/REPLAY.md §5.2）。

用法（仓库根，用仓库的 venv 解释器）::

    .venv\\Scripts\\python.exe tools\\replay.py list
    .venv\\Scripts\\python.exe tools\\replay.py list --dir .dispatch\\replays --json
    .venv\\Scripts\\python.exe tools\\replay.py show <id>
    .venv\\Scripts\\python.exe tools\\replay.py show <id> --ai
    .venv\\Scripts\\python.exe tools\\replay.py show <id> --ai --player 0 --kinds log,error --limit 200
    .venv\\Scripts\\python.exe tools\\replay.py tail <id> --tick 200 60   # 只看 200 之后的 60 刻

设计要点
--------
* 只读：本工具不写回放、不改目录（除了没有索引时由 `ReplayStore` 自己重建一次）。
* 目录解析与服务端一致（`GENERALS_REPLAY_DIR` > `<repo>/replays`），可用 `--dir` 覆盖。
* 「对局中不可读取」同样生效：正在录制的回放 `show` 会明确报错（R8.2）。
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="对局回放工具（docs/REPLAY.md）")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--dir", default="", help="回放目录（缺省 GENERALS_REPLAY_DIR 或 <repo>/replays）"
    )
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

    args = ap.parse_args(argv)
    directory = args.dir or None
    try:
        if args.command == "list":
            return cmd_list(_with_dir(args, directory))
        return cmd_show(_with_dir(args, directory))
    except ReplayRunning:
        print("该回放属于一局**仍在进行**的对局：对局结束前不可读取（R8.2）。", file=sys.stderr)
        return 2
    except ReplayNotFound:
        print(f"找不到回放: {args.id}（目录 {directory or ReplayStore().directory}）", file=sys.stderr)
        return 3
    except ReplayError as exc:
        print(f"读取失败: {exc}", file=sys.stderr)
        return 1


def _with_dir(args: argparse.Namespace, directory: str | None) -> argparse.Namespace:
    """归一化 `--dir`（空串 → None，交给仓库按环境变量/缺省解析）。"""
    args.dir = directory
    return args


if __name__ == "__main__":
    raise SystemExit(main())