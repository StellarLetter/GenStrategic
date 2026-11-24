/**
 * replay.ts — 回放文件解析与「按 Tick 重放」游标（纯逻辑，不碰 DOM）。
 *
 * 契约见 `docs/REPLAY.md` §2：回放的 `frames` 与线协议**同构**（外加 `tick` 字段），
 * 因此重放 = 把帧按顺序喂给 `core/state.ts::applyFrame`，不需要任何回放专用的解码器。
 *
 * 本模块属于 `core` 层：不得 import DOM / render / ui / input。
 */

import { applyFrame, emptyState, type ClientState } from './state';
import type { ServerFrame } from './types';

/** 回放里的一名玩家（`docs/REPLAY.md` §2 `players`）。 */
export interface ReplayPlayer {
  playerId: number;
  seat: number;
  name: string;
  color: string;
  /** 机器人座位的策略名（`join.bot`）。 */
  bot: string | null;
  /** 真人座位的托管策略名。 */
  autopilot: string | null;
}

/** `docs/REPLAY.md` §2 `result`。 */
export interface ReplayResult {
  ended: boolean;
  winner: number | null;
  tick: number;
  turn: number;
  half: 0 | 1;
}

/** AI 调试条目的 `kind`（`docs/REPLAY.md` §2.2）。 */
export type AiKind = 'log' | 'action' | 'error' | 'reject' | 'drop';

/** 一条 AI 调试条目（字段随 `kind` 变化，未用的字段缺省）。 */
export interface ReplayAiEntry {
  tick: number;
  playerId: number | null;
  seat: number | null;
  strategy: string | null;
  kind: AiKind;
  level?: string;
  text?: string;
  action?: { op: string; from?: number; dir?: string; ratio?: number; tile?: number };
  op?: string;
  seq?: number;
  code?: string;
  from?: number;
  dir?: string;
  ratio?: number;
  reason?: string;
  cascade?: number;
}

/** 回放里的一帧：线协议下行帧 + `tick`。 */
export type ReplayFrame = ServerFrame & { tick: number };

/** 一份完整回放（`GET /api/replays/{id}`）。 */
export interface ReplayFile {
  format: string;
  version: number;
  id: string;
  room: string;
  seed: number;
  size: number;
  tickMs: number;
  startedAt: string;
  endedAt: string;
  reason: string;
  players: ReplayPlayer[];
  result: ReplayResult;
  truncated: boolean;
  aiTruncated: boolean;
  frames: ReplayFrame[];
  ai: ReplayAiEntry[];
}

/** `GET /api/replays` 里的一行。 */
export interface ReplaySummary {
  id: string;
  room: string;
  startedAt: string;
  endedAt: string;
  size: number;
  tickMs: number;
  turns: number;
  reason: string;
  ended: boolean;
  winner: number | null;
  winnerName: string | null;
  players: { playerId: number; name: string; color: string; strategy: string | null }[];
  frameCount: number;
  aiCount: number;
  truncated: boolean;
  aiTruncated: boolean;
  bytes: number;
}

/** `GET /api/replays` 的响应体。 */
export interface ReplayListResponse {
  directory: string;
  enabled: boolean;
  keep: number;
  count: number;
  active: string[];
  replays: ReplaySummary[];
}

/**
 * 校验并规范化一份回放 JSON。
 *
 * 前置：`raw` 是 `GET /api/replays/{id}` 的响应体（任意 JSON）。
 * 后置：返回 `ReplayFile`；`frames` 已按 `tick` **稳定排序**（同 tick 保持线上顺序），
 *       缺失字段按缺省值补齐（未知帧仍保留，`applyFrame` 会忽略它们）。
 * 抛出：`Error` —— `format` 不是 `generals.replay`，或 `frames` 不是数组。
 */
export function parseReplay(raw: unknown): ReplayFile {
  if (!raw || typeof raw !== 'object') throw new Error('回放内容不是对象');
  const obj = raw as Record<string, unknown>;
  if (obj['format'] !== 'generals.replay') throw new Error('不是 generals 回放文件');
  const framesRaw = obj['frames'];
  if (!Array.isArray(framesRaw)) throw new Error('回放缺少 frames');

  const frames: ReplayFrame[] = framesRaw.map((item, index) => {
    const frame = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>;
    const tick = Number(frame['tick']);
    return {
      ...(frame as unknown as ServerFrame),
      tick: Number.isFinite(tick) ? tick : index,
    } as ReplayFrame;
  });
  // Array.prototype.sort 在 ES2019+ 是稳定排序：同 tick 的帧保持原有先后（帧序即语义）。
  frames.sort((a, b) => a.tick - b.tick);

  const result = (obj['result'] as Record<string, unknown> | undefined) ?? {};
  return {
    format: 'generals.replay',
    version: Number(obj['version'] ?? 1),
    id: String(obj['id'] ?? ''),
    room: String(obj['room'] ?? ''),
    seed: Number(obj['seed'] ?? 0),
    size: Number(obj['size'] ?? 0),
    tickMs: Number(obj['tickMs'] ?? 500) || 500,
    startedAt: String(obj['startedAt'] ?? ''),
    endedAt: String(obj['endedAt'] ?? ''),
    reason: String(obj['reason'] ?? ''),
    players: Array.isArray(obj['players']) ? (obj['players'] as ReplayPlayer[]) : [],
    result: {
      ended: Boolean(result['ended']),
      winner: result['winner'] === null || result['winner'] === undefined ? null : Number(result['winner']),
      tick: Number(result['tick'] ?? 0),
      turn: Number(result['turn'] ?? 0),
      half: Number(result['half'] ?? 0) === 1 ? 1 : 0,
    },
    truncated: Boolean(obj['truncated']),
    aiTruncated: Boolean(obj['aiTruncated']),
    frames,
    ai: Array.isArray(obj['ai']) ? (obj['ai'] as ReplayAiEntry[]) : [],
  };
}

/** 玩家标记 / 策略名（回放里 `bot` 优先，其次 `autopilot`，都没有 = 人工）。 */
export function strategyOf(player: ReplayPlayer): string | null {
  return player.bot ?? player.autopilot ?? null;
}

/**
 * 回放游标：持有「某一 Tick 的客户端状态」，支持前进与任意跳转。
 *
 * 用法：
 * ```ts
 * const cursor = new ReplayCursor(file);
 * const state = cursor.seek(120);   // 跳到 Tick 120（含该刻的所有帧）
 * cursor.tick                      // 当前所处 Tick
 * ```
 *
 * 前置（构造）：`file.size > 0`。
 * 后置：`state` 初始为 `emptyState(file.size)`，`tick = -1`（还没应用任何帧）。
 * 抛出：构造不抛。
 */
export class ReplayCursor {
  readonly file: ReplayFile;
  readonly maxTick: number;
  private frames: ReplayFrame[];
  private index = 0;
  private stateValue: ClientState;
  private tickValue = -1;

  constructor(file: ReplayFile) {
    this.file = file;
    this.frames = file.frames;
    this.maxTick = Math.max(file.result.tick, this.frames.length > 0 ? this.frames[this.frames.length - 1]!.tick : 0);
    this.stateValue = emptyState(Math.max(1, file.size));
  }

  /** 当前客户端状态（观战者视角：全图真实信息，R3.10）。 */
  get state(): ClientState {
    return this.stateValue;
  }

  /** 当前所处的 Tick（`-1` = 还没开始）。 */
  get tick(): number {
    return this.tickValue;
  }

  /** 已应用到的帧下标（调试 / 进度条用）。 */
  get frameIndex(): number {
    return this.index;
  }

  /** 是否已播完（应用到最后一帧）。 */
  get atEnd(): boolean {
    return this.index >= this.frames.length;
  }

  /**
   * 跳到 `tick` 并返回该刻的状态（**含**该刻产生的所有帧）。
   *
   * 前置：`tick` 会被夹到 `[-1, maxTick]`。
   * 后置：往回跳时从空状态重放（保证结果与逐帧播放完全一致）；往前跳只增量应用。
   * 抛出：无。
   */
  seek(tick: number): ClientState {
    const target = Math.max(-1, Math.min(this.maxTick, Math.trunc(tick)));
    if (target < this.tickValue) this.rewind();
    const frames = this.frames;
    while (this.index < frames.length && frames[this.index]!.tick <= target) {
      this.stateValue = applyFrame(this.stateValue, frames[this.index]!);
      this.tickValue = Math.max(this.tickValue, frames[this.index]!.tick);
      this.index += 1;
    }
    return this.stateValue;
  }

  /** 回到「未开始」并丢弃所有已应用帧（跳转回溯用）。 */
  rewind(): void {
    this.stateValue = emptyState(Math.max(1, this.file.size));
    this.index = 0;
    this.tickValue = -1;
  }
}

/** 回放时长（毫秒）：`maxTick * tickMs`。 */
export function durationMs(file: ReplayFile, maxTick: number): number {
  return Math.max(0, maxTick) * (file.tickMs || 500);
}

/** `mm:ss` 时间文本（回放时长展示用）。 */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** 把 `ISO-8601` 转成本地 `YYYY-MM-DD HH:mm` 文本（解析失败原样返回）。 */
export function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}