/**
 * camera.ts — 正投影摄像机。
 *
 * 地图为正方形（R2.1）。默认视野 = **整张地图正好铺满可用区域**（与原站存档页的
 * `#gameMap { width/height: size * --cell-size }` 一致）；缩放在此基础上倍率变化。
 *
 * 契约：纯函数、无 DOM、无副作用（INTERFACES §4）。
 */

/** 缩放倍率档位（1 = 整图铺满）。 */
export const ZOOM_LEVELS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3] as const;

export interface Camera {
  size: number;
  /** 视口中心的地块坐标（浮点，以地块为单位；地块 r,c 的中心为 r+0.5）。 */
  cx: number;
  cy: number;
  /** 视口 CSS 像素宽高。 */
  width: number;
  height: number;
  /** 缩放倍率；1 = 整图正好铺满较短边。 */
  zoom: number;
  /** 设备像素比。 */
  dpr: number;
}

/** 单格 CSS 像素。 */
export function cellSize(cam: Camera): number {
  const base = Math.min(cam.width, cam.height) / cam.size;
  return base * cam.zoom;
}

/** 地图在屏幕上的边长（CSS 像素）。 */
export function mapPixels(cam: Camera): number {
  return cellSize(cam) * cam.size;
}

/** 地图左上角的屏幕坐标。 */
export function mapOrigin(cam: Camera): { x: number; y: number } {
  const m = mapPixels(cam);
  return { x: (cam.width - m) / 2, y: (cam.height - m) / 2 };
}

/** 新建摄像机：居中于地图中心，整图铺满。 */
export function makeCamera(size: number, width: number, height: number, dpr: number): Camera {
  return clampCamera({ size, cx: size / 2, cy: size / 2, width, height, zoom: 1, dpr });
}

/** 把摄像机中心夹紧，保证可视区域不跑出地图（地图比视口小则居中）。 */
export function clampCamera(cam: Camera): Camera {
  const cell = cellSize(cam);
  const halfW = cam.width / 2 / cell;
  const halfH = cam.height / 2 / cell;
  let { cx, cy } = cam;
  if (halfW * 2 >= cam.size) cx = cam.size / 2;
  else cx = Math.min(Math.max(cx, halfW), cam.size - halfW);
  if (halfH * 2 >= cam.size) cy = cam.size / 2;
  else cy = Math.min(Math.max(cy, halfH), cam.size - halfH);
  return { ...cam, cx, cy };
}

/** 居中到某个地块。 */
export function centerOnTile(cam: Camera, index: number): Camera {
  const row = Math.floor(index / cam.size);
  const col = index % cam.size;
  return clampCamera({ ...cam, cx: col + 0.5, cy: row + 0.5 });
}

/** 以像素为单位平移（拖拽时用）。 */
export function panPixels(cam: Camera, dx: number, dy: number): Camera {
  const cell = cellSize(cam);
  return clampCamera({ ...cam, cx: cam.cx - dx / cell, cy: cam.cy - dy / cell });
}

/** 以地块为单位平移（方向键用）。 */
export function panTiles(cam: Camera, dx: number, dy: number): Camera {
  return clampCamera({ ...cam, cx: cam.cx + dx, cy: cam.cy + dy });
}

/** 缩放到下一档/上一档。 */
export function zoomStep(cam: Camera, direction: 1 | -1): Camera {
  const i = nearestZoomIndex(cam.zoom);
  const next = Math.min(ZOOM_LEVELS.length - 1, Math.max(0, i + direction));
  return clampCamera({ ...cam, zoom: ZOOM_LEVELS[next]! });
}

function nearestZoomIndex(zoom: number): number {
  let best = 0;
  let bestD = Infinity;
  for (let i = 0; i < ZOOM_LEVELS.length; i += 1) {
    const d = Math.abs(ZOOM_LEVELS[i]! - zoom);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

/** 地块在屏幕上的矩形（含网格间隙）。 */
export interface TileRect {
  x: number;
  y: number;
  /** 含间隙的完整格子边长。 */
  w: number;
  /** 实际填充边长（= w - gap）。 */
  fill: number;
  gap: number;
}

/** 网格间隙宽度（CSS 像素）。原站 cell=53 时间隙为 2px。 */
export function gridGap(cell: number): number {
  return Math.max(1, Math.round(cell * 0.04));
}

/** 地块 (col,row) 的左上角屏幕坐标：以视口中心 + 摄像机中心换算。 */
export function tileRect(cam: Camera, index: number): TileRect {
  const cell = cellSize(cam);
  const row = Math.floor(index / cam.size);
  const col = index % cam.size;
  return {
    x: cam.width / 2 + (col - cam.cx) * cell,
    y: cam.height / 2 + (row - cam.cy) * cell,
    w: cell,
    fill: cell - gridGap(cell),
    gap: gridGap(cell),
  };
}

/** 屏幕坐标（相对画布左上角）→ 地块索引；越界返回 null。 */
export function hitTest(cam: Camera, screenX: number, screenY: number): number | null {
  const cell = cellSize(cam);
  const col = Math.floor((screenX - cam.width / 2) / cell + cam.cx);
  const row = Math.floor((screenY - cam.height / 2) / cell + cam.cy);
  if (row < 0 || row >= cam.size || col < 0 || col >= cam.size) return null;
  return row * cam.size + col;
}

/** 当前可见地块范围（含半格余量），用于裁剪绘制。 */
export function visibleRange(cam: Camera): {
  row0: number;
  row1: number;
  col0: number;
  col1: number;
} {
  const cell = cellSize(cam);
  const left = cam.cx - cam.width / 2 / cell;
  const top = cam.cy - cam.height / 2 / cell;
  return {
    row0: Math.max(0, Math.floor(top)),
    row1: Math.min(cam.size - 1, Math.ceil(top + cam.height / cell)),
    col0: Math.max(0, Math.floor(left)),
    col1: Math.min(cam.size - 1, Math.ceil(left + cam.width / cell)),
  };
}
