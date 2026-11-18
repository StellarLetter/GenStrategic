"""单条 WS 连接的状态机：连接 → 已加入 → 对局中 → 关闭。

契约见 `docs/INTERFACES.md` §2.3（`Session`）。

关键设计
--------
- `Session` 同时充当 `net.room.Connection`（实现 `async def send()` / `async def close()`）：
  `Room` 只往 `Session.queue` 里塞文本，真正的写出由本类的 sender task 完成。
- **慢客户端不阻塞其它人**：队列有界（`QUEUE_MAXSIZE`）；`Room` 发现入队失败会踢人，
  `Session.close()` 负责收尾。
- 心跳：PROTOCOL.md §1 规定「连接后 10s 内必须 `join`，否则断开」；本类还提供
  `ping` 透传与空闲 watchdog（默认关闭，见 `idle_timeout`）。
- 关闭清理：`on_close()` 幂等，负责 `Lobby.leave()` + 取消 sender/reaper task。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Final

from . import protocol
from .lobby import Lobby
from .room import QUEUE_MAXSIZE, Room, Seat

__all__ = [
    "Session",
    "JOIN_TIMEOUT",
]

#: 连接后必须在此秒数内发送 `join`，否则断开（PROTOCOL.md §1）。
JOIN_TIMEOUT: Final[float] = 10.0

#: 「下行队列已被替换」的唤醒信号（内部）。
#:
#: `attach_queue()` 只改 `self.queue` 无法唤醒**已阻塞**在旧队列 `get()` 上的 sender task，
#: 于是新队列里的帧（`joined`/`game_start`/…）永远不会被取出（P0 缺陷）。
#: 投递本哨兵值给旧队列即可让它醒来并重读 `self.queue`。
_REWIRE: Final[object] = object()


class Session:
    """一条 WS 连接（INTERFACES §2.3）。

    前置（构造）：
    - `send_text` 是 transport 提供的「把文本写到该 socket」的协程函数；
      它应吞掉自身的网络异常，或抛出由 `_sender_loop` 捕获的异常类型。
    - `lobby` 是共享的房间表（`transport` 或 `app` 持有单例）。
    后置：
    - 构造即启动两个后台 task：sender（消费队列写 socket）与 reaper（`join` 超时）；
    - `seat is None` 表示尚未加入任何房间。
    抛出：无。

    并发约束：`on_message` / `on_close` / `send` / `close` 都可以在事件循环里被并发调用；
    内部只用 `asyncio.Queue` 与简单的布尔标志，不持有锁。
    """

    def __init__(
        self,
        send_text: Any,
        lobby: Lobby,
        *,
        idle_timeout: float | None = None,
        join_timeout: float = JOIN_TIMEOUT,
    ) -> None:
        self._send_text = send_text
        self._lobby = lobby
        self.room: Room | None = None
        self.seat: Seat | None = None
        self.idle_timeout = idle_timeout
        self.join_timeout = join_timeout
        self.closed: bool = False

        self.queue: asyncio.Queue[str] = asyncio.Queue(QUEUE_MAXSIZE)
        self._sender_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._start_tasks()

    # ------------------------------------------------------------------
    # Connection 协议实现
    # ------------------------------------------------------------------
    def attach_queue(self, queue: asyncio.Queue) -> None:
        """由 `Room.add` 调用：把该座位的下行队列交给本连接消费。

        前置：`queue` 是 `Seat.queue`（由 `Room` 写入）。
        后置：
        - 本连接改用该队列（`self.queue` 指向它）；
        - 旧队列里**尚未写出**的帧被搬到新队列（不丢帧：丢帧会破坏客户端的增量状态）；
        - 若 sender task 正阻塞在旧队列上，投一个 `_REWIRE` 唤醒它，让它下一圈重读 `self.queue`。
        抛出：无（旧队列满时停止搬运，只可能发生在异常场景）。

        设计说明：`Room.broadcast()` 同步写入 `Seat.queue`；若 `Session` 另持一个队列，
        两边永远对不上（帧会堆在座位队列里而 socket 收不到）。这里是唯一的桥接点。
        注意**必须唤醒 sender**：`await old_queue.get()` 不会因为 `self.queue` 被重新赋值而返回，
        否则 `joined` 之后的帧会永远卡住（P0 缺陷，2026-11-18 由 e2e 发现）。
        """
        old = self.queue
        if old is queue:
            return
        self.queue = queue
        # 1) 搬迁旧队列中未写出的帧（保持顺序）。
        while True:
            try:
                item = old.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _REWIRE:
                continue
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:  # pragma: no cover - join 阶段的旧队列必然是空的
                break
        # 2) 唤醒可能正阻塞在旧队列上的 sender task。
        try:
            old.put_nowait(_REWIRE)
        except asyncio.QueueFull:  # pragma: no cover - 旧队列已满则该 task 已被唤醒
            pass

    async def send(self, text: str) -> None:
        """把一条已编码帧放入下行队列（`net.room.Connection` 实现）。

        前置：`text` 是可被 socket 直接写出的字符串。
        后置：入队成功即返回；**不**等待真正写出（避免慢客户端拖住 Tick 循环）。
        抛出：无 —— 队列满时静默丢弃并标记慢客户端（`Room` 侧会因此踢人）。
        """
        if self.closed:
            return
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:
            # 队列满 = 慢客户端。主动关闭，交由 `on_close` 清理座位。
            await self.close()

    async def close(self) -> None:
        """关闭连接（幂等）。

        前置：无。
        后置：`closed = True`；取消后台 task；`on_close()` 已完成座位清理。
        抛出：无。
        """
        await self.on_close()

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------
    def _start_tasks(self) -> None:
        """启动 sender / reaper 后台任务（内部）。"""
        self._sender_task = asyncio.create_task(self._sender_loop())
        if self.join_timeout and self.join_timeout > 0:
            self._reaper_task = asyncio.create_task(self._join_reaper())

    async def _sender_loop(self) -> None:
        """消费 `queue` 并逐条写出（内部）。

        前置：`self._send_text` 是协程函数。
        后置：连接关闭或写出失败时调用 `on_close()` 并退出。
        抛出：`asyncio.CancelledError` —— 关闭时按约定向上传播。
        """
        try:
            while not self.closed:
                queue = self.queue  # 每次循环重读：`attach_queue()` 可能已换成座位队列
                text = await queue.get()
                if text is _REWIRE:
                    # 只是「队列被换过」的唤醒信号，让下一圈重读 `self.queue`。
                    continue
                try:
                    await self._send_text(text)
                except Exception:  # noqa: BLE001 - 传输层异常一律视为断线
                    await self.on_close()
                    return
        except asyncio.CancelledError:
            raise

    async def _join_reaper(self) -> None:
        """`join` 超时看门狗（PROTOCOL.md §1：10s 内未 join 则断开）。

        前置：`join_timeout > 0`。
        后置：超时且仍未加入任何房间 → 发送 `reject(code='not_joined')` 并关闭。
        抛出：`asyncio.CancelledError` —— 关闭时按约定向上传播。
        """
        try:
            await asyncio.sleep(self.join_timeout)
        except asyncio.CancelledError:
            return
        if self.seat is None and not self.closed:
            await self.send(json.dumps(protocol.encode_reject("join", 0, "not_joined")))
            # 给 sender 一个调度机会把 reject 写出去，再关闭。
            await asyncio.sleep(0)
            await self.on_close()

    def _cancel_tasks(self) -> None:
        """取消后台任务（内部；幂等）。"""
        for task in (self._reaper_task, self._sender_task):
            if task is not None and not task.done():
                task.cancel()

    # ------------------------------------------------------------------
    # 消息入口
    # ------------------------------------------------------------------
    async def on_message(self, raw: str) -> None:
        """处理一条上行文本帧。

        前置：`raw` 是 socket 收到的原始文本（JSON）。
        后置：
        - `join` / `spectate` 行帧完成房间加入，并（由 `Room.add`）下发 `joined`；
        - 其它帧在已加入时转交 `Room.handle()`；未加入时回 `reject(code='not_joined')`；
        - 未知 `t` 静默忽略（PROTOCOL.md §1 向前兼容）；
        - 任何 `ProtocolError` 都转成 `reject` 帧，**不断开连接**（除非是 join 阶段的致命错误）。
        抛出：无 —— 本方法是 WS 层的唯一入口，必须自己不冒泡。
        """
        if self.closed:
            return
        try:
            frame = protocol.parse_client_frame(raw)
        except protocol.ProtocolError as exc:
            await self.send(json.dumps(protocol.encode_reject("unknown", 0, exc.code)))
            return

        kind = frame.get("t")
        try:
            if kind == "join":
                await self._on_join(frame)
            elif kind == "spectate":
                await self._on_spectate(frame)
            elif kind == "ping":
                await self.send(json.dumps(protocol.encode_pong(protocol.validate_ping(frame)["n"])))
            elif kind in protocol.CLIENT_FRAME_TYPES:
                await self._forward(frame)
            else:
                return  # 未知 t：忽略
        except protocol.ProtocolError as exc:
            await self.send(json.dumps(protocol.encode_reject(str(kind), 0, exc.code)))

    async def _forward(self, frame: dict) -> None:
        """把已加入连接的业务帧转给 `Room.handle`（内部）。"""
        if self.seat is None or self.room is None:
            await self.send(json.dumps(protocol.encode_reject(str(frame.get("t")), 0, "not_joined")))
            return
        try:
            self.room.handle(self.seat, frame)
        except protocol.ProtocolError as exc:
            await self.send(json.dumps(protocol.encode_reject(str(frame.get("t")), 0, exc.code)))

    async def _on_join(self, frame: dict) -> None:
        """处理 `join`（PROTOCOL.md §2.1）。

        前置：`frame["t"] == "join"`。
        后置：已加入任何房间时，**先离开旧房间再进入新房间**（同一连接换房）。
              `joined` 帧由 `Room.add` 直接入本连接队列。
        抛出：`protocol.ProtocolError` —— 结构非法或 `room_full`。
        """
        spec = protocol.validate_join(frame)
        if self.seat is not None:
            self._leave_current()
        room, seat = self._lobby.join(
            spec["name"], spec["room"], spec["spectate"], conn=self, bot=spec["bot"]
        )
        self.room = room
        self.seat = seat
        if self._reaper_task is not None and not self._reaper_task.done():
            self._reaper_task.cancel()

    async def _on_spectate(self, frame: dict) -> None:
        """处理 `spectate`（PROTOCOL.md §2.2）：以观战者身份加入房间。"""
        spec = protocol.validate_spectate(frame)
        if self.seat is not None:
            self._leave_current()
        room, seat = self._lobby.join("spectator", spec["room"], True, conn=self)
        self.room = room
        self.seat = seat

    def _leave_current(self) -> None:
        """离开当前房间（内部；幂等）。"""
        seat = self.seat
        if seat is None:
            return
        self.seat = None
        self.room = None
        with contextlib.suppress(Exception):
            self._lobby.leave(seat)

    async def on_close(self) -> None:
        """连接关闭清理（幂等）。

        前置：无。
        后置：
        - `seat` 已从房间移除（`Room.remove` 会广播 `left` 并按 R5.5 开始投降过渡）；
        - 后台 task 已取消；`closed = True`；
        - 重复调用不会产生第二次 `left` 广播。
        抛出：无。
        """
        if self.closed:
            return
        self.closed = True
        self._leave_current()
        self._cancel_tasks()
