/**
 * geometry.ts — 坐标换算与相邻关系。
 *
 * 契约：纯函数，无副作用，不触碰 DOM。
 * 规则引用：R2.1（index = row * size + col）、R4.1（相邻 = 4 相邻）、R3.5（视野 = 8 相邻）。
 */

import type { Dir } from './types';

/** 方向 → 行列增量。顺序固定，用于确定性遍历（RULES R7.3）。 */
export const DIRS: ReadonlyArray<{ dir: Dir; dc: number; dr: number }> = [
  { dir: 'up', dc: 0, dr: -1 },
  { dir: 'down', dc: 0, dr: 1 },
  { dir: 'left', dc: -1, dr: 0 },
  { dir: 'right', dc: 1, dr: 0 },
];

export function toIndex(size: number, row: number, col: number): number {
  return row * size + col;
}

export function toRC(size: number, index: number): [row: number, col: number] {
  return [Math.floor(index / size), index % size];
}

/** 4 相邻地块索引（越界过滤）。R4.1 */
export function neighbours4(index: number, size: number): number[] {
  const [r, c] = toRC(size, index);
  const out: number[] = [];
  if (r > 0) out.push((r - 1) * size + c);
  if (c > 0) out.push(r * size + (c - 1));
  if (c < size - 1) out.push(r * size + (c + 1));
  if (r < size - 1) out.push((r + 1) * size + c);
  return out;
}

/** 8 相邻地块索引（越界过滤）。R3.5 */
export function neighbours8(index: number, size: number): number[] {
  const [r, c] = toRC(size, index);
  const out: number[] = [];
  for (let dr = -1; dr <= 1; dr += 1) {
    for (let dc = -1; dc <= 1; dc += 1) {
      if (dr === 0 && dc === 0) continue;
      const rr = r + dr;
      const cc = c + dc;
      if (rr >= 0 && rr < size && cc >= 0 && cc < size) out.push(rr * size + cc);
    }
  }
  return out;
}

/** 从 from 朝 dir 出发是否在边界内。 */
export function step(index: number, dir: Dir, size: number): number | null {
  const [r, c] = toRC(size, index);
  const d = DIRS.find((x) => x.dir === dir);
  if (!d) return null;
  const rr = r + d.dr;
  const cc = c + d.dc;
  if (rr < 0 || rr >= size || cc < 0 || cc >= size) return null;
  return rr * size + cc;
}

/** 从 from 朝 to 的方向（必须是 4 相邻），否则 null。用于点击调兵 R6.5。 */
export function dirBetween(from: number, to: number, size: number): Dir | null {
  const [r1, c1] = toRC(size, from);
  const [r2, c2] = toRC(size, to);
  if (Math.abs(r1 - r2) + Math.abs(c1 - c2) !== 1) return null;
  if (r2 === r1 - 1) return 'up';
  if (r2 === r1 + 1) return 'down';
  if (c2 === c1 - 1) return 'left';
  return 'right';
}
