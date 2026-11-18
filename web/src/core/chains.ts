/**
 * chains.ts — 调兵链的本地镜像与调兵比例状态机。
 *
 * 契约（docs/INTERFACES.md §4）：
 *   toggleSelection(ch, idx) -> ChainState      // R6.1 / R6.6 选中与比例规则
 *   pushMove(ch, dir) -> { chain, cmd }         // R6.2 键盘调兵
 *   pushClickMove(ch, target, size)             // R6.5 点击调兵
 *   clearChain / popChain                       // R6.4 Q / E
 *
 * 规则：
 *   R6.6 选中新地块 → 比例 100%；再次点击当前选中地块 → 50%；`+`/`-` 步长 10%（10%..100%）。
 *   R6.3 调兵链 FIFO；服务端 `commands` 帧回显会覆盖本地镜像。
 */

import { dirBetween, step } from './geometry';
import type { Dir, MoveRequest } from './types';

export const RATIO_MIN = 0.1;
export const RATIO_MAX = 1.0;
export const RATIO_STEP = 0.1;
export const CHAIN_LIMIT = 20;

export interface ChainState {
  selected: number | null;
  ratio: number;
  chain: MoveRequest[];
}

export function initialChain(): ChainState {
  return { selected: null, ratio: RATIO_MAX, chain: [] };
}

/** R6.6：点击地块。点新地块 → 选中 + 比例回 100%；再点当前选中 → 50%。 */
export function toggleSelection(ch: ChainState, index: number): ChainState {
  if (ch.selected === index) {
    return { ...ch, ratio: 0.5 };
  }
  return { ...ch, selected: index, ratio: RATIO_MAX };
}

export function adjustRatio(ch: ChainState, delta: number): ChainState {
  const next = Math.min(RATIO_MAX, Math.max(RATIO_MIN, round1(ch.ratio + delta)));
  if (next === ch.ratio) return ch;
  return { ...ch, ratio: next };
}

export interface PushResult {
  chain: ChainState;
  cmd: MoveRequest | null;
  error: string | null;
}

/**
 * 从当前选中地块朝 dir 入队一条调兵请求；成功后选中移到目标地块（R6.2）。
 * 前置：selected 非空；目标在界内且不是山地外观（R4.9，客户端只做粗筛，权威在服务端）。
 * 后置：chain 追加；超过 CHAIN_LIMIT 时 error='chain_full' 且不追加。
 */
export function pushMove(
  ch: ChainState,
  dir: Dir,
  size: number,
  isBlocked: (index: number) => boolean,
): PushResult {
  if (ch.selected === null) return { chain: ch, cmd: null, error: 'no_selection' };
  const target = step(ch.selected, dir, size);
  if (target === null) return { chain: ch, cmd: null, error: 'not_adjacent' };
  if (isBlocked(target)) return { chain: ch, cmd: null, error: 'blocked' };
  if (ch.chain.length >= CHAIN_LIMIT) return { chain: ch, cmd: null, error: 'chain_full' };
  const cmd: MoveRequest = { from: ch.selected, dir, ratio: ch.ratio };
  return {
    chain: { ...ch, selected: target, chain: [...ch.chain, cmd] },
    cmd,
    error: null,
  };
}

/** R6.5：点击已选中地块的 4 相邻地块 → 一次调兵，并选中该相邻地块。 */
export function pushClickMove(ch: ChainState, target: number, size: number): PushResult {
  if (ch.selected === null) return { chain: ch, cmd: null, error: 'no_selection' };
  const dir = dirBetween(ch.selected, target, size);
  if (!dir) return { chain: ch, cmd: null, error: 'not_adjacent' };
  return pushMove(ch, dir, size, () => false);
}

/** R6.4：Q 清空完整调兵链。 */
export function clearChain(ch: ChainState): ChainState {
  if (ch.chain.length === 0) return ch;
  return { ...ch, chain: [] };
}

/** R6.4：E 取消最新一条调兵请求，并把选中移回该请求的出发地块。 */
export function popChain(ch: ChainState): ChainState {
  if (ch.chain.length === 0) return ch;
  const chain = ch.chain.slice(0, -1);
  const removed = ch.chain[ch.chain.length - 1]!;
  return { ...ch, chain, selected: removed.from };
}

function round1(x: number): number {
  return Math.round(x * 10) / 10;
}
