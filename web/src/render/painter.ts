/**
 * painter.ts — 地图绘制。
 *
 * 契约：`paint(ctx, cam, state, chain, opts)` 只读 ClientState，不修改它、不发网络请求。
 * 视觉基准：SaveWeb/屏幕截图 2026-11-18 190403.png（全局）/ 192622.png（放大）。
 *
 * 绘制顺序（对齐原站）：
 *   ① 地图底色 → ② 逐格填充（视野内先铺满网格线色再内缩填充；视野外整格填雾色，
 *      使迷雾连成一片、不出现网格线）→ ③ 地形/控制图标 → ④ 兵量数字（覆盖在图标之上）
 *   → ⑤ 调兵链箭头 → ⑥ 选中描边
 */

import type { ChainState } from '../core/chains';
import { playerOf, type ClientState, type Tile } from '../core/state';
import { DIRS, dirBetween, toRC } from '../core/geometry';
import type { ColorName, TerrainCode } from '../core/types';
import { cellSize, tileRect, visibleRange, type Camera } from './camera';
import { drawCastle, drawCrown, drawMountain } from './icons';
import { BG, PLAYER_COLORS, TILE, tileFill } from './palette';

export interface PaintOptions {
  /** 显示调兵箭头（本地调兵链镜像）。 */
  showChain: boolean;
  /** 高亮「点击调兵」目标。 */
  highlightTargets: boolean;
}

const NUMBER_FONT =
  "'Quicksand', 'Segoe UI', 'Microsoft YaHei', -apple-system, sans-serif";

export function paint(
  ctx: CanvasRenderingContext2D,
  cam: Camera,
  state: ClientState,
  chain: ChainState,
  opts: PaintOptions,
): void {
  ctx.save();
  ctx.setTransform(cam.dpr, 0, 0, cam.dpr, 0, 0);

  // ① 地图底色（与原站一致：地图区域底色 = 迷雾色）
  ctx.fillStyle = BG.fog;
  ctx.fillRect(0, 0, cam.width, cam.height);

  const { row0, row1, col0, col1 } = visibleRange(cam);
  const cell = cellSize(cam);

  // ② 填充：视野内 = 黑色格子打底 + 内缩地块（缝隙因此是纯黑网格线）；
  //    视野外 = 整格雾色（相邻地块无缝，迷雾是一片连续底色）。
  for (let row = row0; row <= row1; row += 1) {
    for (let col = col0; col <= col1; col += 1) {
      const index = row * cam.size + col;
      const tile = state.tiles[index];
      if (!tile) continue;
      const r = tileRect(cam, index);
      if (tile.seen) {
        ctx.fillStyle = TILE.grid;
        ctx.fillRect(r.x, r.y, r.w, r.w);
        ctx.fillStyle = tileFill(tile.terrain, colorNameOf(state, tile), true);
        ctx.fillRect(r.x + r.gap / 2, r.y + r.gap / 2, r.fill, r.fill);
      } else {
        ctx.fillStyle = BG.fog;
        ctx.fillRect(r.x, r.y, r.w, r.w);
      }
    }
  }

  // ③ 图标
  for (let row = row0; row <= row1; row += 1) {
    for (let col = col0; col <= col1; col += 1) {
      const index = row * cam.size + col;
      const tile = state.tiles[index];
      if (!tile) continue;
      const r = tileRect(cam, index);
      drawTileBadge(ctx, tile, r.x + r.w / 2, r.y + r.w / 2, r.fill, armyLabel(tile) !== '');
    }
  }

  // ④ 兵量
  for (let row = row0; row <= row1; row += 1) {
    for (let col = col0; col <= col1; col += 1) {
      const index = row * cam.size + col;
      const tile = state.tiles[index];
      if (!tile) continue;
      const r = tileRect(cam, index);
      drawArmy(ctx, tile, r.x + r.w / 2, r.y + r.w / 2, r.fill);
    }
  }

  // ⑤ 调兵链
  if (opts.showChain) drawChain(ctx, cam, chain);

  // ⑥ 选中描边
  if (chain.selected !== null && chain.selected < state.tiles.length) {
    const r = tileRect(cam, chain.selected);
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = Math.max(2, Math.round(cell * 0.055));
    ctx.strokeRect(r.x + r.gap / 2, r.y + r.gap / 2, r.fill, r.fill);
  }

  ctx.restore();
}

function colorNameOf(state: ClientState, tile: Tile): ColorName | null {
  if (tile.owner === null) return null;
  return playerOf(state, tile.owner)?.color ?? null;
}

/**
 * 地形图标。
 * 视野外：只有折叠成「山地外观」的地块画山形（R6.9），描线纯黑（#000000，在迷雾上很淡）。
 * 视野内：山地画山形（黑线 + #acacac 底）、城市画城堡、首都画王冠。
 *
 * `dimmed` = 该格还要叠兵量数字。此时图标改用半透明深色，退成背景纹理，
 * 让白色数字读得清（原站城市格上正是深色线稿 + 白色数字）。
 */
function drawTileBadge(
  ctx: CanvasRenderingContext2D,
  tile: Tile,
  cx: number,
  cy: number,
  cellPx: number,
  dimmed: boolean,
): void {
  const terrain: TerrainCode = tile.terrain;
  if (!tile.seen) {
    if (terrain === 2) drawMountain(ctx, cx, cy, cellPx, TILE.mountainStroke);
    return;
  }
  const stroke = dimmed
    ? TILE.iconStrokeDimmed
    : tile.owner === null && (terrain === 3 || terrain === 4)
      ? TILE.iconStrokeOnNeutral
      : TILE.iconStroke;
  switch (terrain) {
    case 2:
      drawMountain(ctx, cx, cy, cellPx, TILE.mountainStroke);
      break;
    case 3:
    case 4:
      drawCastle(ctx, cx, cy, cellPx, stroke);
      break;
    case 1:
      drawCrown(ctx, cx, cy, cellPx, stroke);
      break;
    default:
      break;
  }
}

/** 该地块要显示的兵量文本；空串表示不画数字。 */
function armyLabel(tile: Tile): string {
  if (!tile.seen || tile.army === null || tile.army <= 0) return '';
  if (tile.terrain === 2) return ''; // 山地无兵量
  return String(tile.army);
}

/**
 * 兵量数字：白色 + 深色柔化阴影，覆盖在图标之上（原站即如此）。
 * 兵量为 0 的地块不画数字（对齐原站视觉：空地/零兵格是纯色块）。
 */
function drawArmy(
  ctx: CanvasRenderingContext2D,
  tile: Tile,
  cx: number,
  cy: number,
  cellPx: number,
): void {
  const text = armyLabel(tile);
  if (text === '') return;
  const scale = text.length >= 4 ? 0.26 : text.length === 3 ? 0.32 : 0.38;
  const size = Math.max(7, cellPx * scale);
  ctx.save();
  ctx.font = `700 ${size}px ${NUMBER_FONT}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.shadowColor = 'rgba(0,0,0,0.9)';
  ctx.shadowBlur = Math.max(2, size * 0.3);
  ctx.fillStyle = '#ffffff';
  ctx.fillText(text, cx, cy + cellPx * 0.015);
  ctx.restore();
}

function drawChain(ctx: CanvasRenderingContext2D, cam: Camera, chain: ChainState): void {
  if (chain.chain.length === 0) return;
  const cell = cellSize(cam);
  for (const req of chain.chain) {
    const from = tileRect(cam, req.from);
    const [r, c] = toRC(cam.size, req.from);
    const d = DIRS.find((x) => x.dir === req.dir);
    if (!d) continue;
    const tx = c + d.dc;
    const ty = r + d.dr;
    if (tx < 0 || ty < 0 || tx >= cam.size || ty >= cam.size) continue;
    const to = tileRect(cam, ty * cam.size + tx);
    const x0 = from.x + from.w / 2;
    const y0 = from.y + from.w / 2;
    const x1 = to.x + to.w / 2;
    const y1 = to.y + to.w / 2;
    const ax = x0 + (x1 - x0) * 0.42;
    const ay = y0 + (y1 - y0) * 0.42;
    ctx.save();
    ctx.strokeStyle = '#ffffff';
    ctx.fillStyle = '#ffffff';
    ctx.lineWidth = Math.max(1.5, cell * 0.05);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(x0 + (x1 - x0) * 0.12, y0 + (y1 - y0) * 0.12);
    ctx.lineTo(ax, ay);
    ctx.stroke();
    const ah = Math.max(4, cell * 0.13);
    const ang = Math.atan2(y1 - y0, x1 - x0);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(ax - ah * Math.cos(ang - Math.PI / 6), ay - ah * Math.sin(ang - Math.PI / 6));
    ctx.lineTo(ax - ah * Math.cos(ang + Math.PI / 6), ay - ah * Math.sin(ang + Math.PI / 6));
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
}

/** 供 UI / 调试：玩家色。 */
export function playerColor(name: ColorName): string {
  return PLAYER_COLORS[name];
}

/** 点击调兵：判断 (from → to) 是否 4 相邻。 */
export function isAdjacent(from: number, to: number, size: number): boolean {
  return dirBetween(from, to, size) !== null;
}
