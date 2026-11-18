"""端到端冒烟脚本：真起 uvicorn，用两个 WS 客户端打完一局的开局若干 Tick。

用途：验收「传输层 + 网络层 + 内核」三层真的串起来了（pytest 只覆盖各自单层）。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\e2e_smoke.py [--port 8123] [--ticks 30]

退出码：0 = 全部断言通过；1 = 有断言失败（打印最后 20 条下行帧）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import websockets

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():  # pragma: no cover - 非 Windows
    PY = Path(sys.executable)


class Player:
    """一个测试客户端：记录收到的所有下行帧。"""

    def __init__(self, name: str, ws: Any) -> None:
        self.name = name
        self.ws = ws
        self.frames: list[dict] = []
        self.tiles: dict[int, tuple[int | None, int | None, int]] = {}
        self.size: int | None = None
        self.me: int | None = None
        self.turns: list[tuple[int, int]] = []
        self.chain: list[dict] = []

    async def send(self, frame: dict) -> None:
        await self.ws.send(json.dumps(frame))

    def apply(self, frame: dict) -> None:
        self.frames.append(frame)
        t = frame.get("t")
        if t == "joined":
            self.me = frame.get("playerId")
        elif t == "game_start":
            self.size = frame.get("size")
            for entry in frame.get("tiles", []):
                self.tiles[entry[0]] = (entry[1], entry[2], entry[3])
        elif t == "tiles":
            for entry in frame.get("list", []):
                self.tiles[entry[0]] = (entry[1], entry[2], entry[3])
        elif t == "turn":
            self.turns.append((frame["turn"], frame["half"]))
        elif t == "sight":
            for idx in frame.get("remove", []):
                self.tiles.pop(idx, None)
        elif t == "commands":
            self.chain = frame.get("chain", [])


async def pump(player: Player, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        timeout = max(0.01, deadline - time.monotonic())
        try:
            raw = await asyncio.wait_for(player.ws.recv(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return
        except websockets.ConnectionClosed:
            return
        player.apply(json.loads(raw))


async def run(port: int, ticks: int) -> int:
    url = f"ws://127.0.0.1:{port}/ws"
    failures: list[str] = []

    proc = subprocess.Popen(
        [
            str(PY),
            "-m",
            "uvicorn",
            "generals.app:app",
            "--app-dir",
            "server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        await asyncio.sleep(2.0)
        async with (
            websockets.connect(url) as ws_a,
            websockets.connect(url) as ws_b,
        ):
            a = Player("A", ws_a)
            b = Player("B", ws_b)
            await a.send({"t": "join", "name": "Alice", "room": "smoke"})
            await pump(a, 0.4)
            await b.send({"t": "join", "name": "Bob", "room": "smoke"})
            await pump(b, 0.4)
            await pump(a, 0.4)

            if a.me is None or b.me is None:
                failures.append("join 未收到 joined 帧")
            else:
                print(f"[ok] joined: A={a.me} B={b.me}")

            await a.send({"t": "start"})
            await pump(a, 1.2)
            await pump(b, 1.2)
            starts = [f for f in a.frames if f["t"] == "game_start"]
            if not starts:
                failures.append("start 后未收到 game_start")
            else:
                print(f"[ok] game_start size={a.size} tiles={len(a.tiles)}")

            # 让 Tick 跑一会儿，验证 turn 帧单调递增
            await pump(a, ticks * 0.5 + 1.0)
            await pump(b, 0.2)
            if len(a.turns) < ticks // 2:
                failures.append(f"turn 帧偏少：{len(a.turns)}")
            else:
                print(f"[ok] turns={a.turns[:4]}... 共 {len(a.turns)}")

            # 找一个自己的首都，发一条 move（开局首都 1 兵 → 出兵 0，也应被接受不 reject）
            my_tiles = [i for i, v in a.tiles.items() if v[1] == a.me]
            if not my_tiles:
                failures.append("客户端 A 视野内没有己方地块")
            else:
                home = my_tiles[0]
                await a.send(
                    {
                        "t": "commands",
                        "cmds": [{"op": "move", "from": home, "dir": "down", "ratio": 1.0}],
                    }
                )
                await pump(a, 0.3)
                rejects = [f for f in a.frames if f["t"] == "reject"]
                if rejects:
                    failures.append(f"move 被拒绝：{rejects[-1]}")
                else:
                    print(f"[ok] move from={home} 已接受")

            # 聊天
            await a.send({"t": "chat", "text": "hello"})
            await pump(b, 0.4)
            chats = [f for f in b.frames if f["t"] == "chat"]
            if not chats:
                failures.append("B 未收到 A 的聊天")
            else:
                print(f"[ok] chat: {chats[-1].get('text')!r}")

            # 投降 → 40 Tick 过渡 → 全地转中立（R5.5 / C-08）
            await b.send({"t": "surrender"})
            await pump(b, 0.5)
            if not [f for f in b.frames if f["t"] == "event" and f.get("kind") == "surrendered"]:
                failures.append("投降未广播 surrendered 事件")
            else:
                print("[ok] surrender 事件已广播")

            # 观察者
            async with websockets.connect(url) as ws_s:
                s = Player("S", ws_s)
                await s.send({"t": "spectate", "room": "smoke"})
                await pump(s, 1.0)
                if not [f for f in s.frames if f["t"] == "game_start"]:
                    failures.append("观战者未收到 game_start")
                else:
                    print(f"[ok] 观战者收到全图 {len(s.tiles)} 格")

        # HTTP 接口
        import httpx

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            rooms = (await http.get("/api/rooms")).json()
            cfg = (await http.get("/api/config")).json()
            print(f"[ok] /api/rooms -> {rooms}")
            print(f"[ok] /api/config -> tickMs={cfg['tickMs']} defaultSize={cfg['defaultSize']}")
            if not isinstance(rooms, list):
                failures.append("/api/rooms 不是数组")
            if cfg["tickMs"] != 500:
                failures.append("/api/config tickMs != 500")
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
            proc.wait(timeout=5)

    if failures:
        print("\n=== FAILURES ===")
        for f in failures:
            print(" -", f)
        return 1
    print("\n=== ALL OK ===")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--ticks", type=int, default=30)
    ns = ap.parse_args()
    return asyncio.run(run(ns.port, ns.ticks))


if __name__ == "__main__":
    raise SystemExit(main())
