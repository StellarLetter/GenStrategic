"""`python -m generals` 入口：一条命令起服务。

用法：
    .\\.venv\\Scripts\\python.exe -m generals              # 默认 127.0.0.1:8000
    .\\.venv\\Scripts\\python.exe -m generals --port 9000 --host 0.0.0.0

启动后：
    http://127.0.0.1:<port>/            → 对局页（需先构建前端：cd web && pnpm build）
    http://127.0.0.1:<port>/waiting.html → 房间列表页
"""

from __future__ import annotations

import argparse

import uvicorn

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """解析命令行并启动 uvicorn。

    前置：`web/dist` 已存在（否则只提供 API 与 WS，前端 404）。
    后置：阻塞直到进程收到中断信号。
    抛出：无。
    """
    ap = argparse.ArgumentParser(prog="generals", description="generals 复刻服务端")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    ap.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    ap.add_argument("--log-level", default="info", help="uvicorn 日志级别")
    ns = ap.parse_args(argv)

    uvicorn.run(
        "generals.app:app",
        host=ns.host,
        port=ns.port,
        reload=ns.reload,
        log_level=ns.log_level,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
