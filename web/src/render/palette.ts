/**
 * palette.ts — 从原站截图取样得到的精确配色。
 *
 * 取样来源：SaveWeb/屏幕截图 2026-11-18 190403.png / 190453.png / 190745.png / 192622.png
 * 方法：逐格取边界主色与排行榜色块主色（见 .dispatch/palette*.py / samples.py）。
 *
 * 实测结论：
 *   - 页面底色 #222222；地图底色与视野外地块填充同为 #393939
 *     （原站的迷雾是一片连续底色，视野外地块之间**没有**网格线）。
 *   - 山形图标一律纯黑 #000000：视野外画在 #393939 上（几乎隐入底色），
 *     视野内画在 #acacac 上（RULES R6.9），线宽约格子的 2%。
 *   - 视野内中立平原 #dcdcdc、山地 #acacac、城市 #808080。
 *   - 网格线纯黑，宽度约为格子的 4%，只出现在视野内的地块之间。
 */

import type { ColorName, TerrainCode } from '../core/types';

/** 8 名玩家色（排行榜色块实测值）。 */
export const PLAYER_COLORS: Record<ColorName, string> = {
  red: '#ff0000',
  blue: '#2792ff',
  green: '#008000',
  teal: '#008080',
  purple: '#800080',
  pink: '#f032e6',
  maroon: '#9b0101',
  orange: '#fa8c01',
};

/** 颜色分配顺序（与 PROTOCOL §3.1 / server config.COLORS 一致）。 */
export const PLAYER_COLOR_ORDER: ColorName[] = [
  'red',
  'blue',
  'green',
  'teal',
  'purple',
  'pink',
  'maroon',
  'orange',
];

export const BG = {
  /** 页面底色（地图区域之外）。 */
  page: '#222222',
  /** 地图底色 = 视野外地块填充（两者同色，迷雾因此是连续一片）。 */
  fog: '#393939',
};

export const TILE = {
  /** 视野内中立平原。 */
  neutralPlain: '#dcdcdc',
  /** 视野内山地。 */
  mountain: '#acacac',
  /** 视野内中立城市。 */
  neutralCity: '#808080',
  /** 网格线（视野内地块之间的缝隙）。 */
  grid: '#000000',
  /** 山形图标描线色：视野内外都是纯黑。 */
  mountainStroke: '#000000',
  /** 有主地块（玩家色底）上的图标描线色。 */
  iconStroke: '#ffffff',
  /** 中立城市（#808080 中灰底）上的图标描线色：原站用深色线稿。 */
  iconStrokeOnNeutral: '#000000',
  /** 该格还要叠兵量数字时，图标退为背景纹理用的深色（原站城市格就是深色线稿 + 白数字）。 */
  iconStrokeDimmed: 'rgba(0,0,0,0.72)',
};

/** 排行榜已出局行底色。 */
export const DEAD_ROW = '#666666';

export const UI = {
  text: '#ffffff',
  chatBg: '#111111',
  chatText: '#dcdcdc',
  accent: '#75baba',
  gold: '#ffd700',
};

/**
 * 地块填充色。
 * `seen=false` ⇒ 视野外：一律 fog（RULES R6.9 只折叠"外观"，不折叠底色）。
 */
export function tileFill(terrain: TerrainCode, owner: ColorName | null, seen: boolean): string {
  if (!seen) return BG.fog;
  if (owner) return PLAYER_COLORS[owner];
  switch (terrain) {
    case 2:
      return TILE.mountain;
    case 3:
    case 4:
      return TILE.neutralCity;
    default:
      return TILE.neutralPlain;
  }
}
