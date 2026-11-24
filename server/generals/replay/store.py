"""对局回放的**落盘与读取**：`ReplayStore`。

职责
----
- 给每个开局房间发一个 `ReplayRecorder`（`begin`），对局结束时整份写成
  `<id>.json`（原子写：先 `*.json.tmp` 再 `os.replace`）；
- 维护回放目录索引（`index.json`，供列表接口免去逐份读大文件）；
- **对局中不可读取**：正在录制的回放只有内存缓冲、磁盘上还没有文件，
  `load()` 另外显式抛 `ReplayRunning`（即便有人拿到了 id）；
- 保留策略：目录内最多 `keep` 份，最旧先删（磁盘保险丝）。

文件布局（`docs/REPLAY.md` 是格式事实源）::

    <replay_dir>/
      index.json                 # {"format":"generals.replay.index","version":1,"replays":[<summary>…]}
      20261124T115620Z-public-1-3f9a2c.json
      …

本模块**不碰 `GameState`**（录制器只读它），只做 I/O；允许读时钟与随机数（文件名后缀），
它们不参与任何结算（R7.3 约束的是结算）。
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from generals.config import REPLAY_KEEP

from .record import ReplayLimits, ReplayRecorder, summarize

__all__ = [
    "INDEX_FORMAT",
    "ReplayError",
    "ReplayNotFound",
    "ReplayRunning",
    "ReplayStore",
    "default_replay_dir",
]

#: 索引文件格式标识。
INDEX_FORMAT: Final[str] = "generals.replay.index"
#: 索引文件版本。
INDEX_VERSION: Final[int] = 1
#: 回放 id 允许的字符（同时用作文件名主干，禁止路径分隔符与 `..`）。
_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
#: 文件名时间戳格式（UTC，字典序 == 时间序）。
_TS_FMT: Final[str] = "%Y%m%dT%H%M%SZ"


class ReplayError(Exception):
    """回放存储层的通用错误（读失败/写失败/坏 id）。"""


class ReplayNotFound(ReplayError):
    """回放不存在（或已被保留策略清理）。"""


class ReplayRunning(ReplayError):
    """该回放所属对局**仍在进行**：按规定不得读取（docs/REPLAY.md §4）。"""


def _utc_now() -> datetime:
    """当前 UTC 时间（回放模块允许读时钟：它只进文件名/元数据，不参与结算）。"""
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    """ISO-8601（毫秒精度、带 `+00:00`）——同时满足人读与字典序排序。"""
    return moment.isoformat(timespec="milliseconds")


def default_replay_dir() -> Path:
    """解析回放目录：环境变量 `GENERALS_REPLAY_DIR` > `<repo>/replays`。

    前置：无。
    后置：返回绝对路径（**不创建**目录）。
    抛出：无。
    """
    env = os.environ.get("GENERALS_REPLAY_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parents[3] / "replays").resolve()


def _safe_id(room_id: str) -> str:
    """把房间号压成文件名安全片段（非 `[A-Za-z0-9._-]` 一律换成 `-`）。

    前置：无。
    后置：返回 1..32 字符的片段；房间号全是怪字符时返回 `"room"`。
    抛出：无。
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(room_id)).strip("-._")
    return (cleaned or "room")[:32]


class ReplayStore:
    """回放仓库（进程内**唯一**实例，由 `app.py` 装配并注入 `Lobby`）。

    前置（构造）：`directory` 缺省用 `default_replay_dir()`；`limits` 缺省用
    `ReplayLimits()`（取自 `generals.config`）；`keep` 缺省 `REPLAY_KEEP`。
    后置：`enabled=False` 时 `begin()` 返回 `None`（房间不录制），其余查询接口返回空。
    抛出：构造不抛（目录在建盘时才创建）。

    线程约束：与 `Room` 相同——只在事件循环线程里调用（同步 I/O，文件不大）。
    """

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        limits: ReplayLimits | None = None,
        keep: int = REPLAY_KEEP,
        enabled: bool = True,
    ) -> None:
        self.directory: Path = Path(directory).expanduser().resolve() if directory else default_replay_dir()
        self.limits: ReplayLimits = limits or ReplayLimits()
        self.keep: int = max(1, int(keep))
        self.enabled: bool = bool(enabled)
        #: 正在录制的回放（id → recorder）。**它们的回放不可读**。
        self._active: dict[str, ReplayRecorder] = {}

    # ------------------------------------------------------------------
    # 录制生命周期
    # ------------------------------------------------------------------
    def new_id(self, room_id: str, now: datetime | None = None) -> str:
        """生成一个新的回放 id（`<UTC时间戳>-<房间号>-<6 位hex>`）。

        前置：无。
        后置：返回满足 `_ID_RE` 的字符串；后缀用 `secrets.token_hex`（与结算无关）。
        抛出：无。
        """
        moment = now or _utc_now()
        return f"{moment.strftime(_TS_FMT)}-{_safe_id(room_id)}-{secrets.token_hex(3)}"

    def begin(
        self,
        *,
        room_id: str,
        seed: int,
        size: int,
        tick_ms: int,
        players: list[dict],
    ) -> ReplayRecorder | None:
        """为一个刚开局的房间创建录制器并登记为「进行中」。

        前置：`players` 每项含 `playerId/name/color`（可含 `seat/bot/autopilot`）。
        后置：返回已登记 `startedAt` 的 `ReplayRecorder`（调用方负责 `attach(state)`）；
              `enabled=False` 时返回 `None`；该 id 在 `save()` 之前**不可读**。
        抛出：无 —— 登记失败返回 `None`（回放是旁路，绝不阻断开局）。
        """
        if not self.enabled:
            return None
        try:
            moment = _utc_now()
            replay_id = self.new_id(room_id, moment)
            recorder = ReplayRecorder(
                replay_id=replay_id,
                room_id=room_id,
                seed=seed,
                size=size,
                tick_ms=tick_ms,
                players=players,
                limits=self.limits,
                started_at=_iso(moment),
            )
            self._active[replay_id] = recorder
            return recorder
        except Exception:  # noqa: BLE001
            return None

    def save(self, recorder: ReplayRecorder, *, reason: str = "ended") -> dict:
        """把录制器整份落盘并更新索引（**每份只落一次**）。

        前置：`recorder` 来自本仓库的 `begin()`。
        后置：
        - `<id>.json` 原子写入（tmp + `os.replace`）；索引首行插入该行摘要；
        - 从「进行中」集合摘除（此后再读就是「已结束」）；
        - 超出 `keep` 的最旧回放被删除（含文件与索引行）。
        抛出：`ReplayError` —— 目录不可写 / 序列化失败等（调用方 `Room` 会吞掉并记日志）。
        """
        payload = recorder.finish(reason=reason, ended_at=_iso(_utc_now()))
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / f"{payload['id']}.json"
            tmp = target.with_name(target.name + ".tmp")
            try:
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        except (OSError, TypeError, ValueError) as exc:
            self._active.pop(payload["id"], None)
            raise ReplayError(f"cannot write replay {payload['id']}: {exc}") from exc
        self._active.pop(payload["id"], None)
        summary = summarize(payload, bytes_=len(text.encode("utf-8")))
        self._index_insert(summary)
        self._prune()
        return summary

    def abort(self, recorder: ReplayRecorder, *, reason: str = "aborted") -> dict | None:
        """同 `save(reason=...)`，但吞掉所有异常（房间 `close()` 用）。

        前置：同 `save()`。
        后置：成功返回摘要，失败返回 `None`（绝不抛）。
        抛出：无。
        """
        try:
            return self.save(recorder, reason=reason)
        except Exception:  # noqa: BLE001
            return None

    def is_active(self, replay_id: str) -> bool:
        """该 id 是否属于**仍在进行**的对局（进行中 = 不可读）。"""
        return str(replay_id) in self._active

    def active_ids(self) -> list[str]:
        """所有正在录制的回放 id（升序）。"""
        return sorted(self._active)

    # ------------------------------------------------------------------
    # 查询 / 读取
    # ------------------------------------------------------------------
    def list(self, *, limit: int = 50) -> list[dict]:
        """回放列表（新的在前）。

        前置：无。
        后置：返回 `summarize()` 行，最多 `limit` 条；索引缺失时按目录重建。
              文件已不存在但索引里还有的行会被剔除。
        抛出：无（读索引失败视为需要重建）。
        """
        if not self.enabled:
            return []
        rows = self._read_index()
        live = [row for row in rows if (self.directory / f"{row.get('id', '')}.json").is_file()]
        if len(live) != len(rows):
            self._write_index(live)
        return live[: max(1, int(limit))]

    def load(self, replay_id: str) -> dict:
        """读取一份完整回放（`frames` + `ai`）。

        前置：`replay_id` 是 `list()` 里的 id（或来自文件名）。
        后置：返回完整回放 dict（结构见 `record.ReplayRecorder.finish`）。
        抛出：
        - `ReplayRunning` —— 该对局**仍在进行**（对局中不可读取回放）；
        - `ReplayNotFound` —— id 非法 / 文件不存在；
        - `ReplayError` —— 文件损坏。
        """
        rid = str(replay_id).strip()
        if not _ID_RE.match(rid) or ".." in rid:
            raise ReplayNotFound(f"bad replay id: {replay_id!r}")
        if rid in self._active:
            raise ReplayRunning(f"replay {rid} belongs to a running match")
        path = self.directory / f"{rid}.json"
        if not path.is_file():
            raise ReplayNotFound(f"replay not found: {rid}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReplayError(f"cannot read replay {rid}: {exc}") from exc

    def delete(self, replay_id: str) -> bool:
        """删除一份回放（含索引行）。

        前置：无。
        后置：文件存在则删除并返回 `True`；不存在返回 `False`。
        抛出：`ReplayRunning` —— 对局仍在进行（不允许删掉正在录的那一局）。
        """
        rid = str(replay_id).strip()
        if not _ID_RE.match(rid) or ".." in rid:
            return False
        if rid in self._active:
            raise ReplayRunning(f"replay {rid} belongs to a running match")
        path = self.directory / f"{rid}.json"
        existed = path.is_file()
        if existed:
            try:
                path.unlink()
            except OSError as exc:
                raise ReplayError(f"cannot delete replay {rid}: {exc}") from exc
        self._write_index([row for row in self._read_index() if row.get("id") != rid])
        return existed

    def info(self) -> dict:
        """目录概况（`GET /api/replays` 的头部字段）。

        前置：无。
        后置：`{directory, enabled, keep, active, count}`；不列文件。
        抛出：无。
        """
        return {
            "directory": str(self.directory),
            "enabled": self.enabled,
            "keep": self.keep,
            "active": self.active_ids(),
            "count": len(self.list(limit=self.keep)),
        }

    # ------------------------------------------------------------------
    # 索引
    # ------------------------------------------------------------------
    def _index_path(self) -> Path:
        return self.directory / "index.json"

    def _read_index(self) -> list[dict]:
        """读索引行（内部）。

        前置：无。
        后置：返回按 `endedAt` 降序的摘要行；索引缺失/损坏时扫描目录重建。
        抛出：无。
        """
        path = self._index_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("replays")
            if isinstance(rows, list):
                out = [row for row in rows if isinstance(row, dict) and row.get("id")]
                if out:
                    return sorted(out, key=lambda r: str(r.get("endedAt", "")), reverse=True)
        except (OSError, ValueError):
            pass
        rows = self._rebuild_index()
        return rows

    def _rebuild_index(self) -> list[dict]:
        """扫描目录、逐份读取回放文件重建索引（内部；只在索引缺失时发生）。

        前置：无。
        后置：返回摘要行（新的在前）并把它写回索引；单份文件损坏只跳过它。
        抛出：无。
        """
        rows: list[dict] = []
        try:
            candidates = sorted(self.directory.glob("*.json"))
        except OSError:
            return []
        for path in candidates:
            if path.name == "index.json":
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get("format") != "generals.replay":
                continue
            try:
                rows.append(summarize(payload, bytes_=path.stat().st_size))
            except Exception:  # noqa: BLE001 - 结构怪异的旧文件直接跳过
                continue
        rows.sort(key=lambda r: str(r.get("endedAt", "")), reverse=True)
        if rows:
            self._write_index(rows)
        return rows

    def _write_index(self, rows: list[dict]) -> None:
        """原子写索引（内部；失败静默——索引只是列表用的缓存）。"""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            body = {
                "format": INDEX_FORMAT,
                "version": INDEX_VERSION,
                "replays": rows,
            }
            path = self._index_path()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            pass

    def _index_insert(self, summary: dict) -> None:
        """把一行摘要插到索引最前（同 id 覆盖）。"""
        rows = [row for row in self._read_index() if row.get("id") != summary.get("id")]
        rows.insert(0, summary)
        self._write_index(rows)

    def _prune(self) -> None:
        """按 `keep` 删除最旧的回放（内部；同时清理索引）。"""
        rows = self._read_index()
        if len(rows) <= self.keep:
            return
        drop, keep_rows = rows[self.keep :], rows[: self.keep]
        for row in drop:
            rid = str(row.get("id", ""))
            if not _ID_RE.match(rid):
                continue
            try:
                (self.directory / f"{rid}.json").unlink(missing_ok=True)
            except OSError:
                pass
        self._write_index(keep_rows)


