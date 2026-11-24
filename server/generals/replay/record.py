"""对局回放**录制**：把一局里房间下发的帧流与策略调试日志攒在内存里。

设计要点
--------
- **内存缓冲、结束时整份落盘**（`replay.store.ReplayStore.save`）。因此**对局进行中磁盘上
  不存在这一局的回放文件**，「对局中不可读取回放」在物理层面就成立（还额外有 `store` 的
  显式守卫，见 `ReplayStore.load`）。
- 录制的帧与线协议（`docs/PROTOCOL.md` §3）**同构**：每条都带一个额外的 `tick` 字段，
  因此前端可以直接把回放帧喂给 `web/src/core/state.ts::applyFrame` 复现整局画面。
- 地块帧一律是**观战者视角**（`game.view.project(state, None)`，R3.10 全图真实），
  所以回放不需要重新模拟内核，也不会泄露给局中玩家（它只在结束后才存在）。
- 纯内存 + 纯数据：不做 I/O、不读时钟（时间戳由 `store` 在 begin/finish 时注入）。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Final

from generals.config import REPLAY_AI_LOG_MAX, REPLAY_AI_LOG_MAX_LEN, REPLAY_MAX_BYTES
from generals.game import view as game_view
from generals.game.state import GameState, turn_of

__all__ = [
    "FORMAT",
    "VERSION",
    "ReplayLimits",
    "ReplayRecorder",
    "summarize",
]

#: 回放文件格式标识（写在文件首行字段 `format`）。
FORMAT: Final[str] = "generals.replay"
#: 回放文件格式版本。字段名/语义变更必须 +1。
VERSION: Final[int] = 1


@dataclass(frozen=True)
class ReplayLimits:
    """录制上限（缺省取自 `generals.config`，见 docs/REPLAY.md）。

    - `max_bytes`：帧流估算字节上限，超限即停止录帧并置 `truncated=True`；
    - `ai_log_max`：AI 调试条目条数上限，超限即置 `aiTruncated=True`；
    - `ai_text_max`：单条调试文本的最大字符数（超出截断）。
    """

    max_bytes: int = REPLAY_MAX_BYTES
    ai_log_max: int = REPLAY_AI_LOG_MAX
    ai_text_max: int = REPLAY_AI_LOG_MAX_LEN


def _clip(text: Any, limit: int) -> str:
    """把任意对象转成文本并截断到 `limit` 个字符（前置后置：无；抛出：无）。"""
    try:
        out = text if isinstance(text, str) else str(text)
    except Exception:  # noqa: BLE001 - 策略可能把奇怪对象塞进来
        out = repr(text)
    if len(out) > limit:
        return out[: max(0, limit - 1)] + "…"
    return out


class ReplayRecorder:
    """一局对局的回放缓冲（**每个房间一个**，见 `net.room.Room`）。

    前置（构造）：`players` 是开局时的玩家清单（每项至少含 `playerId/name/color`）。
    后置：`frames` / `ai` 随录制增长；`tick` 恒等于所绑定 `GameState.tick`。
    抛出：构造与所有 `record_*` 都**不抛**（回放坏了绝不能拖垮对局）。

    线程约束：只能在房间所属的事件循环线程里写（与 `GameState` 相同）；`AI 日志` 可能
    来自策略的线程池线程（`Room.step_async`），那些调用被 `net.room` 收敛在主线程追加。
    """

    def __init__(
        self,
        *,
        replay_id: str,
        room_id: str,
        seed: int,
        size: int,
        tick_ms: int,
        players: list[dict],
        limits: ReplayLimits | None = None,
        started_at: str = "",
    ) -> None:
        self.id: str = str(replay_id)
        self.room_id: str = str(room_id)
        self.seed: int = int(seed)
        self.size: int = int(size)
        self.tick_ms: int = int(tick_ms)
        self.players: list[dict] = [dict(p) for p in players]
        self.limits: ReplayLimits = limits or ReplayLimits()
        self.started_at: str = str(started_at)
        self.frames: list[dict] = []
        self.ai: list[dict] = []
        self.truncated: bool = False
        self.ai_truncated: bool = False
        self._frame_bytes: int = 0
        self._state: GameState | None = None
        self._last_view: list | None = None
        self._finished: bool = False

    # ------------------------------------------------------------------
    # 绑定
    # ------------------------------------------------------------------
    def attach(self, state: GameState) -> None:
        """绑定权威状态（`tick` 与观战者投影都从它读）。

        前置：`state` 是本房间开局后的 `GameState`（只读使用）。
        后置：后续 `record_*` 的 `tick` = `state.tick`。
        抛出：无。
        """
        self._state = state

    @property
    def tick(self) -> int:
        """录制时刻的 Tick 序号（未绑定时为 0）。"""
        if self._state is None:
            return 0
        return int(self._state.tick)

    @property
    def frame_bytes(self) -> int:
        """帧流的估算字节数（不含 AI 条目）。"""
        return int(self._frame_bytes)

    # ------------------------------------------------------------------
    # 帧
    # ------------------------------------------------------------------
    def record_frame(self, frame: dict, *, size_hint: int | None = None) -> None:
        """追加一条**广播帧**（`net.room.Room.broadcast` 的入参）。

        前置：`frame` 是 `protocol.encode_*` 的产物（纯 dict）。
        后置：深拷贝一份并补上 `tick` 后入列；超过 `max_bytes` 时置 `truncated=True` 并丢弃。
              **深拷贝**是必要的：帧里的 `list` 可能被后续编码复用/就地排序。
        抛出：无 —— 任何内部异常都被吞掉（回放是旁路，不能影响对局）。
        """
        if self.truncated:
            return
        try:
            cost = int(size_hint if size_hint is not None else 64)
            if self._frame_bytes + cost > self.limits.max_bytes:
                self.truncated = True
                return
            entry = copy.deepcopy(frame)
            if isinstance(entry, dict):
                entry["tick"] = self.tick
            self.frames.append(entry)
            self._frame_bytes += cost
        except Exception:  # noqa: BLE001
            self.truncated = True

    def record_tiles(self, state: GameState, *, full: bool = False) -> None:
        """录制**观战者视角**的地块增量帧（PROTOCOL §3.3 的 `tiles` 帧）。

        前置：`state` 已 `attach`（同一对象）。
        后置：与上一次录制的观战者投影 diff，产生 `{"t":"tiles","list":[...]}`；
              无变化则不产生条目；`full=True`（开局）时全量。
        抛出：无。
        """
        if self.truncated:
            return
        try:
            projection = game_view.project(state, None)
            if full or not self._last_view:
                updates = [
                    [tv.index, tv.army, tv.owner, tv.terrain] for tv in projection
                ]
            else:
                updates = game_view.diff_views(self._last_view, projection)
            self._last_view = projection
            if not updates:
                return
            self.record_frame(
                {"t": "tiles", "list": updates},
                size_hint=24 * len(updates) + 48,
            )
        except Exception:  # noqa: BLE001
            self.truncated = True

    # ------------------------------------------------------------------
    # 离线工具用的整局帧序列（与线协议字段一致，见 docs/REPLAY.md §2.1）
    # ------------------------------------------------------------------
    def record_open(self, state: GameState) -> None:
        """录制开局三帧：`game_start` → 全量 `tiles`（观战者视角）→ `leaderboard`。

        前置：`state` 已 `attach`（`tools/ai_arena.py` 这类**不经房间**的离线对局用）。
        后置：帧序与 `Room.start()` 一致（`turn` 0 帧由随后的 `record_tick()` 给出）。
        抛出：无。
        """
        if self.truncated:
            return
        try:
            players = [
                {
                    "playerId": int(pid),
                    "name": state.players[pid].name,
                    "color": state.players[pid].color,
                    "seat": index,
                }
                for index, pid in enumerate(sorted(state.players))
            ]
            self.record_frame({"t": "game_start", "size": int(state.size), "players": players})
            self.record_tiles(state, full=True)
            self.record_frame(self._leaderboard_frame(state))
        except Exception:  # noqa: BLE001
            self.truncated = True

    def record_tick(self, state: GameState) -> None:
        """录制一刻：`turn` → `tiles`（增量）→ `leaderboard`（顺序同 `Room._advance`）。

        前置：`state` 已 `attach` 且本刻已结算（`state.tick` 即本刻序号）。
        后置：与房间广播的三类帧同序同字段；无地块变化时不产生 `tiles` 条目。
        抛出：无。
        """
        if self.truncated:
            return
        try:
            turn, half = turn_of(state.tick)
            self.record_frame({"t": "turn", "turn": int(turn), "half": int(half)})
            self.record_tiles(state)
            self.record_frame(self._leaderboard_frame(state))
        except Exception:  # noqa: BLE001
            self.truncated = True

    def record_close(self, state: GameState) -> None:
        """录制终局两帧：`game_over` → `event{kind:"game_over"}`。

        前置：`state` 已 `attach`（自然终局时 `state.ended` 为真）。
        后置：与 `Room._advance` 的终局帧一致（`winner` 可为 `null`）。
        抛出：无。
        """
        if self.truncated:
            return
        try:
            turn, _half = turn_of(state.tick)
            winner = None if state.winner is None else int(state.winner)
            self.record_frame({"t": "game_over", "winner": winner, "turn": int(turn)})
            who = (
                state.players[winner].name
                if winner is not None and winner in state.players
                else None
            )
            self.record_frame(
                {"t": "event", "kind": "game_over", "turn": int(turn), "who": who, "target": None}
            )
        except Exception:  # noqa: BLE001
            self.truncated = True

    @staticmethod
    def _leaderboard_frame(state: GameState) -> dict:
        """构造 `leaderboard` 帧（PROTOCOL §3.5 字段，按 `playerId` 升序，R7.3）。"""
        rows = [
            {
                "playerId": int(pid),
                "name": state.players[pid].name,
                "color": state.players[pid].color,
                "army": int(state.army_total(pid)),
                "land": int(state.land_count(pid)),
                "alive": bool(state.players[pid].alive),
                "surrenderIn": getattr(state.players[pid], "surrender_ticks_left", None),
                "star": 0,
            }
            for pid in sorted(state.players)
        ]
        return {"t": "leaderboard", "rows": rows}

    # ------------------------------------------------------------------
    # AI 调试条目（策略侧 `generals.ai.log`，见 docs/AI_API.md §11）
    # ------------------------------------------------------------------
    def _append_ai(self, entry: dict) -> None:
        """内部：把一条 AI 调试条目入列（带条数上限；`tick` 放最前面便于人读）。"""
        if self.ai_truncated:
            return
        if len(self.ai) >= self.limits.ai_log_max:
            self.ai_truncated = True
            return
        self.ai.append({"tick": self.tick, **entry})

    def record_ai_log(
        self,
        player_id: int,
        *,
        text: Any,
        level: str = "debug",
        seat: int | None = None,
        strategy: str | None = None,
    ) -> None:
        """记录一条策略自定义日志（`AI_API.md` §11 `log()`）。

        前置：`player_id` 是局中玩家 id。
        后置：追加 `{"tick","playerId","seat","strategy","kind":"log","level","text"}`。
        抛出：无（文本超长按 `limits.ai_text_max` 截断）。
        """
        try:
            self._append_ai(
                {
                    "playerId": int(player_id),
                    "seat": None if seat is None else int(seat),
                    "strategy": None if strategy is None else str(strategy),
                    "kind": "log",
                    "level": str(level or "debug"),
                    "text": _clip(text, self.limits.ai_text_max),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def record_ai_action(
        self,
        player_id: int,
        *,
        action: dict,
        seat: int | None = None,
        strategy: str | None = None,
    ) -> None:
        """记录策略本刻返回的动作（已量化的线协议指令 dict）。

        前置：`action` 是 `Action.to_command()` 的产物。
        后置：追加 `{"kind":"action","action":{...}}`；`None` 动作不入列。
        抛出：无。
        """
        try:
            if not isinstance(action, dict):
                return
            self._append_ai(
                {
                    "playerId": int(player_id),
                    "seat": None if seat is None else int(seat),
                    "strategy": None if strategy is None else str(strategy),
                    "kind": "action",
                    "action": copy.deepcopy(action),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def record_ai_error(
        self,
        player_id: int,
        *,
        error: Any,
        seat: int | None = None,
        strategy: str | None = None,
    ) -> None:
        """记录策略抛出的异常（`act`/`reset`/指令校验失败）。

        前置：`error` 可以是异常对象或文本。
        后置：追加 `{"kind":"error","text":"<Type>: <msg>"}`。
        抛出：无。
        """
        try:
            if isinstance(error, BaseException):
                text = f"{type(error).__name__}: {error}"
            else:
                text = str(error)
            self._append_ai(
                {
                    "playerId": int(player_id),
                    "seat": None if seat is None else int(seat),
                    "strategy": None if strategy is None else str(strategy),
                    "kind": "error",
                    "text": _clip(text, self.limits.ai_text_max),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def record_reject(self, player_id: int, *, op: str = "", seq: int = 0, code: str = "") -> None:
        """记录一次指令被服务端拒绝（`reject` 帧，PROTOCOL §5）。

        前置：`player_id` 是发出指令的玩家。
        后置：追加 `{"kind":"reject","op","seq","code"}`。
        抛出：无。
        """
        try:
            self._append_ai(
                {
                    "playerId": int(player_id),
                    "seat": None,
                    "strategy": None,
                    "kind": "reject",
                    "op": str(op),
                    "seq": int(seq),
                    "code": str(code),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def record_drop(
        self,
        player_id: int,
        *,
        src: int,
        direction: str,
        ratio: float,
        reason: str,
        cascade: int = 0,
    ) -> None:
        """记录一条**被作废**的调兵请求（R4.12 `chain_dropped`，只发给本人）。

        前置：`player_id` 是请求的拥有者。
        后置：追加 `{"kind":"drop","from","dir","ratio","reason","cascade"}`。
        抛出：无。
        """
        try:
            self._append_ai(
                {
                    "playerId": int(player_id),
                    "seat": None,
                    "strategy": None,
                    "kind": "drop",
                    "from": int(src),
                    "dir": str(direction),
                    "ratio": float(ratio),
                    "reason": str(reason),
                    "cascade": int(cascade),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------
    def finish(self, *, reason: str = "ended", ended_at: str = "") -> dict:
        """产出完整的回放 dict（**不落盘**，落盘由 `ReplayStore.save` 负责，且只做一次）。

        前置：无（未 `attach` 状态时也能产出，只是没有帧）。
        后置：返回与 `docs/REPLAY.md` 一致的 JSON 结构；重复调用返回同一份结果。
        抛出：无。
        """
        state = self._state
        ended = bool(state is not None and state.ended)
        winner = None
        turn = 0
        half = 0
        tick = 0
        if state is not None:
            tick = int(state.tick)
            turn, half = turn_of(state.tick)
            winner = None if state.winner is None else int(state.winner)
        return {
            "format": FORMAT,
            "version": VERSION,
            "id": self.id,
            "room": self.room_id,
            "seed": self.seed,
            "size": self.size,
            "tickMs": self.tick_ms,
            "startedAt": self.started_at,
            "endedAt": str(ended_at),
            "reason": str(reason),
            "players": copy.deepcopy(self.players),
            "result": {
                "ended": ended,
                "winner": winner,
                "tick": tick,
                "turn": turn,
                "half": half,
            },
            "truncated": bool(self.truncated),
            "aiTruncated": bool(self.ai_truncated),
            "frames": self.frames,
            "ai": self.ai,
        }


def summarize(payload: dict, *, bytes_: int = 0) -> dict:
    """把完整回放压成 `GET /api/replays` 的一行（不含帧与本条流）。

    前置：`payload` 是 `ReplayRecorder.finish()` 的产物。
    后置：返回 `{id, room, startedAt, endedAt, savedAt, size, tickMs, turns, reason,
          ended, winner, winnerName, frameCount, aiCount, truncated, aiTruncated, bytes}`；
          `savedAt` 取 `endedAt`（落盘时刻由 store 写 `endedAt`）；`winnerName` 查不到时为 `null`。
    抛出：无 —— 缺字段按缺省值降级。
    """
    players = payload.get("players") or []
    result = payload.get("result") or {}
    winner = result.get("winner")
    winner_name = None
    if winner is not None:
        for row in players:
            if row.get("playerId") == winner:
                winner_name = row.get("name")
                break
    frames = payload.get("frames") or []
    turn = int(result.get("turn", 0) or 0)
    return {
        "id": str(payload.get("id", "")),
        "room": str(payload.get("room", "")),
        "startedAt": str(payload.get("startedAt", "")),
        "endedAt": str(payload.get("endedAt", "")),
        "size": int(payload.get("size", 0) or 0),
        "tickMs": int(payload.get("tickMs", 0) or 0),
        "turns": turn,
        "reason": str(payload.get("reason", "")),
        "ended": bool(result.get("ended", False)),
        "winner": None if winner is None else int(winner),
        "winnerName": winner_name,
        "players": [
            {
                "playerId": int(row.get("playerId", -1)),
                "name": str(row.get("name", "")),
                "color": str(row.get("color", "")),
                "strategy": row.get("bot") or row.get("autopilot"),
            }
            for row in players
        ],
        "frameCount": len(frames),
        "aiCount": len(payload.get("ai") or []),
        "truncated": bool(payload.get("truncated", False)),
        "aiTruncated": bool(payload.get("aiTruncated", False)),
        "bytes": int(bytes_ or 0),
    }