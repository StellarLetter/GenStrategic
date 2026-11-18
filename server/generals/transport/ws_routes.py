"""WebSocket 路由：把一条 WS 连接接到 `net.session.Session`（PROTOCOL.md §1–§3）。

职责：只做「传输适配」——接收文本帧转交 `Session.on_message`，把 `Session` 的下行
文本帧写回 socket，断连时调用 `Session.on_close`。**不含任何游戏逻辑**。

契约见 docs/INTERFACES.md §2.3 / §5 与 docs/PROTOCOL.md。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..net.session import Session

__all__ = ["router"]

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """单条对局连接的生命周期。

    前置：`websocket.app.state.lobby` 已装配（见 `generals.app.create_app`）。
    后置：连接关闭后 `Session.on_close()` 已调用（离开房间、清理座位）。
    抛出：内部吞掉 `WebSocketDisconnect`；其它异常记录后同样清理。
    """
    await websocket.accept()
    lobby: Any = websocket.app.state.lobby
    session = Session(websocket.send_text, lobby)
    try:
        while True:
            raw = await websocket.receive_text()
            await session.on_message(raw)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # 客户端在发送过程中突然断开时 starlette 会抛 RuntimeError
        pass
    finally:
        await session.on_close()
