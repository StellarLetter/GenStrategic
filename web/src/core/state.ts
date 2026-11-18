/**
 * state.ts — 客户端状态树与帧应用。
 *
 * 契约（docs/INTERFACES.md §4）：
 *   applyFrame(state, frame) -> ClientState   // 不可变返回；未知帧原样返回
 *
 * 本模块只维护「服务端告诉我的东西」，不做任何战斗预测（RULES R7.3）。
 */

import type {
  ChatLine,
  ColorName,
  LeaderRow,
  MoveRequest,
  PlayerInfo,
  ServerFrame,
  TerrainCode,
} from './types';

export interface Tile {
  army: number | null;
  owner: number | null;
  terrain: TerrainCode;
  /** 是否为「已知」地块（进入过视野）。用于区分视野外未知与视野内平地。 */
  seen: boolean;
}

export interface ClientState {
  /** 是否已收到 game_start。 */
  started: boolean;
  size: number;
  /** Turn 序号（R1.2）。 */
  turn: number;
  /** 0 = Turn N；1 = Turn N. */
  half: 0 | 1;
  /** 自己的 playerId；观战为 null。 */
  me: number | null;
  players: PlayerInfo[];
  tiles: Tile[];
  sight: Set<number>;
  leaderboard: LeaderRow[];
  chain: MoveRequest[];
  chat: ChatLine[];
  over: boolean;
  winner: number | null;
  /** 上一次 reject 的错误码，用于 UI 闪一下。 */
  lastError: string | null;
}

export function emptyState(size = 20): ClientState {
  return {
    started: false,
    size,
    turn: 0,
    half: 0,
    me: null,
    players: [],
    tiles: Array.from({ length: size * size }, () => ({
      army: null,
      owner: null,
      terrain: 0 as TerrainCode,
      seen: false,
    })),
    sight: new Set(),
    leaderboard: [],
    chain: [],
    chat: [],
    over: false,
    winner: null,
    lastError: null,
  };
}

export function playerOf(state: ClientState, id: number | null): PlayerInfo | undefined {
  if (id === null) return undefined;
  return state.players.find((p) => p.playerId === id);
}

export function colorOf(state: ClientState, id: number | null): ColorName | null {
  return playerOf(state, id)?.color ?? null;
}

const MAX_CHAT = 200;

/**
 * 应用一个服务端帧，返回新状态（结构共享，未变化的部分保持原引用）。
 * 未知 `t` 直接原样返回（PROTOCOL §1 向前兼容）。
 */
export function applyFrame(state: ClientState, frame: ServerFrame): ClientState {
  switch (frame.t) {
    case 'joined':
      // 观战者的 playerId 由服务端以 -1 表示（PROTOCOL §3.0）。
      return { ...state, me: frame.playerId >= 0 ? frame.playerId : null };
    case 'game_start':
      return {
        ...emptyState(frame.size),
        started: true,
        me: state.me,
        players: frame.players,
        // 聊天历史在重连后由服务端补发，这里不清空以免丢线；其余状态从零开始。
        chat: state.me === null ? [] : state.chat,
      };
    case 'turn':
      if (state.turn === frame.turn && state.half === frame.half) return state;
      return { ...state, turn: frame.turn, half: frame.half };
    case 'tiles': {
      if (frame.list.length === 0) return state;
      const tiles = state.tiles.slice();
      let sight: Set<number> | null = null;
      for (const [index, army, owner, terrain] of frame.list) {
        if (index < 0 || index >= tiles.length) continue;
        // `army === null` 即服务端判定该地块在本方视野外（PROTOCOL §3.3 / RULES C-17）。
        const seen = army !== null;
        tiles[index] = { army, owner, terrain, seen };
        if (seen) {
          sight ??= new Set(state.sight);
          sight.add(index);
        }
      }
      return { ...state, tiles, sight: sight ?? state.sight };
    }
    case 'sight': {
      const sight = new Set(state.sight);
      for (const i of frame.add) sight.add(i);
      for (const i of frame.remove) sight.delete(i);
      return { ...state, sight };
    }
    case 'leaderboard':
      return { ...state, leaderboard: frame.rows };
    case 'commands':
      return { ...state, chain: frame.chain };
    case 'chat': {
      const line: ChatLine = {
        turn: frame.turn,
        name: frame.name,
        color: frame.color,
        text: frame.text,
        system: frame.system,
      };
      const chat = [...state.chat, line];
      if (chat.length > MAX_CHAT) chat.splice(0, chat.length - MAX_CHAT);
      return { ...state, chat };
    }
    case 'game_over':
      return { ...state, over: true, winner: frame.winner, turn: frame.turn };
    case 'reject':
      return { ...state, lastError: frame.code };
    default:
      return state;
  }
}

/** 批量应用，减少中间态分配。 */
export function applyFrames(state: ClientState, frames: ServerFrame[]): ClientState {
  let s = state;
  for (const f of frames) s = applyFrame(s, f);
  return s;
}
