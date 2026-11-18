/**
 * pointer.ts — 鼠标：点击选中 / 点击调兵 / 拖拽平移 / 滚轮缩放。
 *
 * 规则引用：
 *   R6.1 点击选中地块
 *   R6.5 点击调兵（可开关）：点击已选中地块的相邻地块 → 触发一次调兵
 *   R6.6 再次点击当前选中地块 → 比例 50%
 */

export interface PointerHandlers {
  /** 屏幕坐标 → 地块索引（越界 null）。 */
  hitTest: (x: number, y: number) => number | null;
  onClickTile: (index: number) => void;
  onPanPixels: (dx: number, dy: number) => void;
  onZoom: (direction: 1 | -1) => void;
}

const DRAG_THRESHOLD = 4;

export function bindPointer(el: HTMLCanvasElement, h: PointerHandlers): () => void {
  let downX = 0;
  let downY = 0;
  let dragging = false;
  let moved = false;

  const onDown = (ev: PointerEvent): void => {
    if (ev.button !== 0 && ev.button !== 1) return;
    downX = ev.clientX;
    downY = ev.clientY;
    dragging = true;
    moved = false;
    el.setPointerCapture(ev.pointerId);
  };

  const onMove = (ev: PointerEvent): void => {
    if (!dragging) return;
    const dx = ev.clientX - downX;
    const dy = ev.clientY - downY;
    if (!moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) moved = true;
    if (moved) {
      downX = ev.clientX;
      downY = ev.clientY;
      h.onPanPixels(dx, dy);
    }
  };

  const onUp = (ev: PointerEvent): void => {
    if (!dragging) return;
    dragging = false;
    el.releasePointerCapture(ev.pointerId);
    if (moved) return;
    const rect = el.getBoundingClientRect();
    const index = h.hitTest(ev.clientX - rect.left, ev.clientY - rect.top);
    if (index !== null) h.onClickTile(index);
  };

  const onWheel = (ev: WheelEvent): void => {
    ev.preventDefault();
    h.onZoom(ev.deltaY > 0 ? -1 : 1);
  };

  el.addEventListener('pointerdown', onDown);
  el.addEventListener('pointermove', onMove);
  el.addEventListener('pointerup', onUp);
  el.addEventListener('wheel', onWheel, { passive: false });

  return () => {
    el.removeEventListener('pointerdown', onDown);
    el.removeEventListener('pointermove', onMove);
    el.removeEventListener('pointerup', onUp);
    el.removeEventListener('wheel', onWheel);
  };
}
