/**
 * icons.ts — 地块图标：王冠（首都）、城堡（城市）、山峰（山地）。
 *
 * 全部用 Canvas 路径绘制，缩放无关；风格对齐原站 SaveWeb 截图 192622.png / 190403.png：
 *   - 山峰 = 两段「∧∧」折线，线宽约格子的 5%
 *   - 城堡 = 圆顶 + 塔身 + 拱门线稿
 *   - 王冠 = 三尖冠 + 底带
 */

export type Ctx = CanvasRenderingContext2D;

/** 山峰图标：双峰折线（原站为「∧∧」，不是锯齿）。 */
export function drawMountain(ctx: Ctx, x: number, y: number, size: number, color = '#ffffff'): void {
  const w = size * 0.66;
  const h = size * 0.34;
  const left = x - w / 2;
  const bottom = y + h / 2;
  const peak = bottom - h;
  const mid = bottom - h * 0.28;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = Math.max(1, size * 0.055);
  ctx.lineJoin = 'miter';
  ctx.lineCap = 'butt';
  ctx.beginPath();
  ctx.moveTo(left, bottom);
  ctx.lineTo(left + w * 0.25, peak);
  ctx.lineTo(left + w * 0.5, bottom);
  ctx.lineTo(left + w * 0.75, peak);
  ctx.lineTo(left + w, bottom);
  ctx.stroke();
  void mid;
  ctx.restore();
}

/** 城堡图标（城市）：圆顶 + 塔身 + 拱门线稿。 */
export function drawCastle(ctx: Ctx, x: number, y: number, size: number, color = '#ffffff'): void {
  const w = size * 0.62;
  const h = size * 0.62;
  const left = x - w / 2;
  const top = y - h / 2;
  const base = top + h;
  const lw = Math.max(1, size * 0.055);
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = lw;
  ctx.lineJoin = 'miter';
  ctx.lineCap = 'round';

  // 顶部尖刺
  ctx.beginPath();
  ctx.moveTo(x, top);
  ctx.lineTo(x, top + h * 0.1);
  ctx.stroke();

  // 圆顶
  ctx.beginPath();
  ctx.arc(x, top + h * 0.28, w * 0.24, Math.PI, 0);
  ctx.stroke();

  // 塔身
  ctx.beginPath();
  ctx.moveTo(left + w * 0.18, base);
  ctx.lineTo(left + w * 0.18, top + h * 0.44);
  ctx.lineTo(left + w * 0.82, top + h * 0.44);
  ctx.lineTo(left + w * 0.82, base);
  ctx.stroke();

  // 两侧小塔
  ctx.beginPath();
  ctx.moveTo(left, base);
  ctx.lineTo(left, top + h * 0.56);
  ctx.lineTo(left + w * 0.18, top + h * 0.56);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(left + w, base);
  ctx.lineTo(left + w, top + h * 0.56);
  ctx.lineTo(left + w * 0.82, top + h * 0.56);
  ctx.stroke();

  // 拱门
  ctx.beginPath();
  const dw = w * 0.3;
  const dx = x - dw / 2;
  ctx.moveTo(dx, base);
  ctx.lineTo(dx, base - h * 0.22);
  ctx.quadraticCurveTo(x, base - h * 0.42, dx + dw, base - h * 0.22);
  ctx.lineTo(dx + dw, base);
  ctx.stroke();

  ctx.restore();
}

/** 王冠图标（首都）：三尖冠 + 底带。 */
export function drawCrown(ctx: Ctx, x: number, y: number, size: number, color = '#ffffff'): void {
  const w = size * 0.62;
  const h = size * 0.34;
  const bx = x - w / 2;
  const by = y - h / 2;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = Math.max(1, size * 0.06);
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  // 冠体：五个尖（低-高-低-高-低）
  ctx.beginPath();
  ctx.moveTo(bx, by + h * 0.62);
  ctx.lineTo(bx + w * 0.02, by + h * 0.06);
  ctx.lineTo(bx + w * 0.26, by + h * 0.5);
  ctx.lineTo(bx + w * 0.5, by);
  ctx.lineTo(bx + w * 0.74, by + h * 0.5);
  ctx.lineTo(bx + w * 0.98, by + h * 0.06);
  ctx.lineTo(bx + w, by + h * 0.62);
  ctx.stroke();
  // 底带
  ctx.beginPath();
  ctx.moveTo(bx - size * 0.02, by + h * 0.86);
  ctx.lineTo(bx + w + size * 0.02, by + h * 0.86);
  ctx.stroke();
  ctx.restore();
}

/** 骷髅图标（排行榜已出局）。 */
export function drawSkull(ctx: Ctx, x: number, y: number, size: number, color = '#111111'): void {
  const r = size * 0.34;
  ctx.save();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y - r * 0.15, r, Math.PI, 0);
  ctx.lineTo(x + r, y + r * 0.35);
  ctx.lineTo(x + r * 0.42, y + r * 0.35);
  ctx.lineTo(x + r * 0.42, y + r * 0.72);
  ctx.lineTo(x - r * 0.42, y + r * 0.72);
  ctx.lineTo(x - r * 0.42, y + r * 0.35);
  ctx.lineTo(x - r, y + r * 0.35);
  ctx.closePath();
  ctx.fill();
  // 眼窝
  ctx.fillStyle = '#ffffff';
  ctx.beginPath();
  ctx.arc(x - r * 0.42, y - r * 0.1, r * 0.26, 0, Math.PI * 2);
  ctx.arc(x + r * 0.42, y - r * 0.1, r * 0.26, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}
