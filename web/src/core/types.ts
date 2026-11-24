/**
 * types.ts — 与 docs/PROTOCOL.md 逐字对齐的协议类型。
 *
 * 该文件属于 `core` 层：不得 import DOM / render / ui / input。
 */

/** 玩家颜色名（顺序与排行榜一致，见 docs/PROTOCOL.md §3.1）。 */
export type ColorName =
  | 'red'
  | 'blue'
  | 'green'
  | 'teal'
  | 'purple'
  | 'pink'
  | 'maroon'
  | 'orange';

/**
 * 客户端可见地形。与 PROTOCOL §3.3 一致：
 * 0 平原 / 1 首都 / 2 山地 / 3 超市 / 4 首都转化型城市。
 * 视野外由服务端折叠为 0 或 2（RULES R6.9）。
 */
export type TerrainCode = 0 | 1 | 2 | 3 | 4;

/** 单个地块的可见信息。[index, army, ownerId, terrain] */
export interface TileView {
  index: number;
  army: number | null;
  owner: number | null;
  terrain: TerrainCode;
}

export type Dir = 'up' | 'down' | 'left' | 'right';

export interface MoveRequest {
  from: number;
  dir: Dir;
  ratio: number;
}

export interface PlayerInfo {
  playerId: number;
  name: string;
  color: ColorName;
  seat: number;
}

export interface LeaderRow {
  playerId: number;
  name: string;
  color: ColorName;
  army: number;
  land: number;
  alive: boolean;
  surrenderIn: number | null;
  star: number;
}

export interface ChatLine {
  turn: number;
  name: string;
  color: ColorName;
  text: string;
  system: boolean;
  /** 系统消息种类（R6.10）；缺省 = 无结构化的纯文本提示。 */
  kind?: SystemChatKind;
  /** `captured` 专用：被攻占者的名字与颜色（用于第二个色块）。 */
  target?: string;
  targetColor?: ColorName;
}

/** 只会有这三种系统播报（R6.10）。 */
export type SystemChatKind = 'captured' | 'left' | 'surrendered';

// ---------------------------------------------------------------- 下行帧

export interface FrameGameStart {
  t: 'game_start';
  size: number;
  players: PlayerInfo[];
}

export interface FrameTurn {
  t: 'turn';
  turn: number;
  half: 0 | 1;
}

export interface FrameTiles {
  t: 'tiles';
  list: [number, number | null, number | null, TerrainCode][];
}

export interface FrameSight {
  t: 'sight';
  add: number[];
  remove: number[];
}

export interface FrameLeaderboard {
  t: 'leaderboard';
  rows: LeaderRow[];
}

export interface FrameCommands {
  t: 'commands';
  chain: MoveRequest[];
}

export type EventKind =
  | 'captured'
  | 'left'
  | 'surrendered'
  | 'kicked'
  | 'game_over'
  | 'chain_dropped';

export interface FrameEvent {
  t: 'event';
  kind: EventKind;
  turn: number;
  who?: string;
  target?: string;
  /** `chain_dropped` 专用：请求的拥有者。 */
  playerId?: number;
  /** `chain_dropped` 专用：作废原因。 */
  reason?: 'src_lost' | 'zero_army';
  /** `chain_dropped` 专用：被作废的请求。 */
  from?: number;
  dir?: Dir;
  ratio?: number;
  /** `chain_dropped` 专用：级联一并作废的条数。 */
  cascade?: number;
}

export interface FrameChat {
  t: 'chat';
  turn: number;
  name: string;
  color: ColorName;
  text: string;
  system: boolean;
  kind?: SystemChatKind;
  target?: string;
  targetColor?: ColorName;
}

export interface FrameGameOver {
  t: 'game_over';
  winner: number | null;
  turn: number;
}

export interface FrameReject {
  t: 'reject';
  op: string;
  seq?: number;
  code: string;
}

export interface FrameJoined {
  t: 'joined';
  playerId: number;
  roomId: string;
  seat: number;
  token?: string;
}

/**
 * `autopilot` 回执（PROTOCOL §3.11）：某座位当前由哪个策略接管。
 * `strategy: null` = 人工操作。该帧广播给全房间。
 */
export interface FrameAutopilot {
  t: 'autopilot';
  playerId: number;
  strategy: string | null;
}

export type ServerFrame =
  | FrameGameStart
  | FrameTurn
  | FrameTiles
  | FrameSight
  | FrameLeaderboard
  | FrameCommands
  | FrameEvent
  | FrameChat
  | FrameGameOver
  | FrameReject
  | FrameJoined
  | FrameAutopilot;

// ---------------------------------------------------------------- 上行帧

export type ClientCommand =
  | { op: 'move'; from: number; dir: Dir; ratio: number }
  | { op: 'clear' }
  | { op: 'pop' }
  | { op: 'select'; tile: number };

export type ClientFrame =
  | { t: 'join'; name: string; room?: string; spectate?: boolean; bot?: string }
  | { t: 'spectate'; room: string }
  | { t: 'ready' }
  | { t: 'start' }
  | { t: 'commands'; cmds: ClientCommand[] }
  | { t: 'chat'; text: string }
  | { t: 'surrender' }
  | { t: 'autopilot'; strategy: string | null }
  | { t: 'ping'; n: number };
