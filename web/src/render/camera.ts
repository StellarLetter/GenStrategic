/**
 * camera.ts — 正投影摄像机。
 *
 * 地图为正方形（R2.1）。默认视野 = **整张地图正好铺满可用区域**（与原站存档页的
 * `#gameMap { width/height: size * --cell-size }` 一致）；缩放在此基础上倍率变化。
 *
 * 位置**不做任何钳制**：中心 (cx, cy) 可以落在 [0, size] 之外，地图因此能被拖到视口外
 * 任意位置（滚轮缩放时以光标下的地图点为锚点，见 `zoomAt`）。中心远离地图时
 * `visibleRange` 会给出空范围（row0 > row1 / col0 > col1），绘制循环自然不画任何格子。
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

/**
 * 地图左上角的屏幕坐标（= 地块 0 的左上角）。
 *
 * 必须由中心算出（而**不是**「视口居中」的假设）：平移后地图会离开居中位置，
 * 中心远离地图时这个坐标会跑到视口外、甚至为负。
 */
export function mapOrigin(cam: Camera): { x: number; y: number } {
  const cell = cellSize(cam);
  return { x: cam.width / 2 - cam.cx * cell, y: cam.height / 2 - cam.cy * cell };
}

/** 新建摄像机：居中于地图中心，整图铺满（位置不钳制）。 */
export function makeCamera(size: number, width: number, height: number, dpr: number): Camera {
  return { size, cx: size / 2, cy: size / 2, width, height, zoom: 1, dpr };
}

/** 居中到某个地块。 */
export function centerOnTile(cam: Camera, index: number): Camera {
  const row = Math.floor(index / cam.size);
  const col = index % cam.size;
  return { ...cam, cx: col + 0.5, cy: row + 0.5 };
}

/** 以像素为单位平移（拖拽时用）。地图可以被拖到视口外任意位置，不夹回。 */
export function panPixels(cam: Camera, dx: number, dy: number): Camera {
  const cell = cellSize(cam);
  return { ...cam, cx: cam.cx - dx / cell, cy: cam.cy - dy / cell };
}

/** 以地块为单位平移（方向键用）。 */
export function panTiles(cam: Camera, dx: number, dy: number): Camera {
  return { ...cam, cx: cam.cx + dx, cy: cam.cy + dy };
}

/** 缩放到下一档/上一档（以视口中心为锚点，等价于 `zoomAt` 传入视口中心）。 */
export function zoomStep(cam: Camera, direction: 1 | -1): Camera {
  return zoomAt(cam, direction, cam.width / 2, cam.height / 2);
}

/**
 * 以屏幕点 (screenX, screenY)（相对画布左上角的 CSS 像素）为锚点缩放一档：
 * **光标下的那个地图点保持不动**。
 *
 * 推导：把光标换算成世界坐标，换档得到新 cell'，再反解出让该世界坐标仍落在
 * 同一屏幕坐标所需的中心：
 *
 *   col  = (sx - width  / 2) / cell  + cx      →  cx' = col - (sx - width  / 2) / cell'
 *   row  = (sy - height / 2) / cell  + cy      →  cy' = row - (sy - height / 2) / cell'
 */
export function zoomAt(cam: Camera, direction: 1 | -1, screenX: number, screenY: number): Camera {
  const i = nearestZoomIndex(cam.zoom);
  const next = Math.min(ZOOM_LEVELS.length - 1, Math.max(0, i + direction));
  const zoom = ZOOM_LEVELS[next]!;
  if (zoom === cam.zoom) return cam;

  const cell = cellSize(cam);
  const nextCell = cellSize({ ...cam, zoom });
  const offsetX = screenX - cam.width / 2;
  const offsetY = screenY - cam.height / 2;
  const col = offsetX / cell + cam.cx;
  const row = offsetY / cell + cam.cy;
  return { ...cam, zoom, cx: col - offsetX / nextCell, cy: row - offsetY / nextCell };
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

/**
 * 当前可见地块范围（含半格余量），用于裁剪绘制。
 * 中心远离地图时返回空范围（row0 > row1 / col0 > col1），调用方的 for 循环自然一次都不执行。
 */
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
