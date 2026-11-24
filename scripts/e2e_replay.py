"""回放端到端：真起 uvicorn + 真浏览器打开 `/replay.html` 验证播放（docs/REPLAY.md §5.5）。

流程：
1. 在临时目录里跑一局**离线**机器人对局，产出一份带策略日志的样本回放（同一套内核/录制器）；
2. 以 `GENERALS_REPLAY_DIR=<临时目录>` 起 uvicorn；
3. 调用 `web/scripts/e2e-replay.mjs`（Playwright + 系统 Edge）做浏览器断言；
4. 收尾删除临时目录。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\e2e_replay.py [--port 8124] [--keep]

退出码：0 = 全部通过；1 = 有失败（打印失败明细）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():  # pragma: no cover - 非 Windows
    PY = Path(sys.executable)


def make_sample_replay(directory: Path) -> str:
    """用 `tools/replay.py demo` 造一份样本回放，返回其 id（不再读盘解析 JSON）。

    前置：`directory` 可写（不存在会被创建）。
    后置：目录里至少有一份回放 + `index.json`；返回回放列表首行的 id。
    抛出：`RuntimeError` —— 生成失败。
    """
    directory.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(PY), str(ROOT / "tools" / "replay.py"), "demo", "--dir", str(directory), "--turns", "120"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"生成样本回放失败：{proc.stdout}{proc.stderr}")
    listing = subprocess.run(
        [str(PY), str(ROOT / "tools" / "replay.py"), "list", "--dir", str(directory), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if listing.returncode != 0:
        raise RuntimeError(f"列出回放失败：{listing.stdout}{listing.stderr}")
    import json

    rows = json.loads(listing.stdout)["replays"]
    if not rows:
        raise RuntimeError("样本目录里没有回放")
    return rows[0]["id"]


def wait_healthy(port: int, timeout: float = 25.0) -> bool:
    """等 `/healthz` 返回 200（返回是否成功）。前置/后置：无；抛出：无。"""
    import time

    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 服务还没起来
            time.sleep(0.3)
    return False


def run(port: int, keep: bool) -> int:
    """跑完整流程，返回进程退出码。"""
    workdir = Path(tempfile.mkdtemp(prefix="generals-e2e-replay-"))
    replay_dir = workdir / "replays"
    failures: list[str] = []
    proc: subprocess.Popen | None = None
    try:
        replay_id = make_sample_replay(replay_dir)
        print(f"[ok] 样本回放 {replay_id} @ {replay_dir}")

        env = dict(os.environ)
        env["GENERALS_REPLAY_DIR"] = str(replay_dir)
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
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if not wait_healthy(port):
            failures.append("uvicorn 未能在 25s 内就绪")
        else:
            print("[ok] 服务就绪")
            out_png = ROOT / ".dispatch" / "replay-e2e.png"
            out_png.parent.mkdir(parents=True, exist_ok=True)
            node = shutil.which("node")
            if node is None:
                failures.append("找不到 node（浏览器 e2e 需要 Node + Playwright）")
            else:
                result = subprocess.run(
                    [
                        node,
                        str(ROOT / "web" / "scripts" / "e2e-replay.mjs"),
                        f"http://127.0.0.1:{port}",
                        replay_id,
                        str(out_png),
                    ],
                    cwd=str(ROOT / "web"),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode != 0:
                    failures.append("浏览器端回放 e2e 失败（见上方 FAILURES / PAGE ERRORS）")
                else:
                    print(f"[ok] 浏览器回放 e2e 通过，截图 {out_png}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                proc.kill()
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            print(f"[keep] 临时目录 {workdir}")

    if failures:
        print("\nFAILURES:")
        for item in failures:
            print("  - " + item)
        return 1
    print("\n=== REPLAY E2E OK ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回放端到端（真服务 + 真浏览器）")
    ap.add_argument("--port", type=int, default=8124)
    ap.add_argument("--keep", action="store_true", help="保留临时回放目录（排错用）")
    args = ap.parse_args(argv)
    return run(args.port, args.keep)


if __name__ == "__main__":
    raise SystemExit(main())