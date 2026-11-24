/**
 * floating.ts — 浮动面板拖动（纯 UI 偏好，不涉及任何游戏规则）。
 *
 * 契约：
 *   makeDraggable(el, opts?) -> detach()
 *     前置：el 已挂到 DOM（需要量宽高把位置夹回视口内）。
 *     后置：返回 detach()，移除全部监听；不改动 el 的内容。
 *
 * 行为：
 *   - Pointer Events 拖动；移动阈值 4px，未超过阈值视为点击、不改位置
 *     （Surrender 按钮这类「整块可拖」的元素因此仍能正常点击：拖动结束会吞掉那次 click）。
 *   - 位置以 px 的 left/top 表示（right/bottom 置 auto、transform 清掉），
 *     存 `localStorage['generals.panel.<id>'] = JSON.stringify({ left, top })`。
 *   - 初始化与 window resize 时把位置夹回视口，至少保留 minVisible 像素可见。
 *   - 拖动开始时把该面板提到最前（模块内 z-index 递增）。
 *
 * 面板是独立的 DOM 层，压在 canvas 之上：面板上的 pointer 事件不会到达 canvas，
 * 所以点击/拖动面板不会误选中地块（R6.1）。
 */

export interface FloatingOptions {
  /** 拖动握把；缺省整块可拖。 */
  handle?: HTMLElement;
  /** localStorage 键；缺省 `generals.panel.<el.id>`。 */
  storageKey?: string;
  /** 拖出视口后仍保留可见的最小像素数（默认 40）。 */
  minVisible?: number;
}

interface SavedPos {
  left: number;
  top: number;
}

/** 拖动阈值（CSS 像素）：小于它不改位置，视为一次点击。 */
const DRAG_THRESHOLD = 4;
/** 拖动结束后的这段时间内吞掉 click，避免按钮被误触。 */
const CLICK_GUARD_MS = 200;

/** 面板层级基数：每次开始拖动就把该面板提到最前。 */
let zTop = 100;

export function makeDraggable(el: HTMLElement, opts: FloatingOptions = {}): () => void {
  const handle = opts.handle ?? el;
  const storageKey = opts.storageKey ?? (el.id ? `generals.panel.${el.id}` : null);
  const minVisible = opts.minVisible ?? 40;

  /** 位置是否已固化成 px 的 left/top（未固化时沿用 CSS 默认锚点）。 */
  let normalized = false;
  let pressed = false;
  let moved = false;
  let activePointer = -1;
  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;
  let lastDragEnd = 0;

  /** 把 CSS 默认定位（left/right/bottom/transform）换算成 px 的 left/top。 */
  function normalize(): void {
    if (normalized) return;
    const r = el.getBoundingClientRect();
    el.style.transform = 'none';
    el.style.right = 'auto';
    el.style.bottom = 'auto';
    el.style.left = `${Math.round(r.left)}px`;
    el.style.top = `${Math.round(r.top)}px`;
    normalized = true;
  }

  /** 写入 left/top（夹回视口），返回实际落点。 */
  function setPos(left: number, top: number): SavedPos {
    const w = el.offsetWidth;
    const maxLeft = window.innerWidth - minVisible;
    const minLeft = minVisible - Math.max(w, minVisible);
    // 顶部不掉出视口（标题/拖动条始终可抓），底部允许露出 minVisible 像素。
    const maxTop = window.innerHeight - minVisible;
    const l = Math.min(maxLeft, Math.max(minLeft, left));
    const t = Math.min(maxTop, Math.max(0, top));
    el.style.transform = 'none';
    el.style.right = 'auto';
    el.style.bottom = 'auto';
    el.style.left = `${Math.round(l)}px`;
    el.style.top = `${Math.round(t)}px`;
    normalized = true;
    return { left: Math.round(l), top: Math.round(t) };
  }

  function currentPos(): SavedPos {
    const left = Number.parseFloat(el.style.left);
    const top = Number.parseFloat(el.style.top);
    return {
      left: Number.isFinite(left) ? left : 0,
      top: Number.isFinite(top) ? top : 0,
    };
  }

  function save(): void {
    if (!storageKey) return;
    const { left, top } = currentPos();
    try {
      window.localStorage.setItem(storageKey, JSON.stringify({ left, top }));
    } catch {
      // 隐私模式 / 配额满：位置只是偏好，静默失败即可。
    }
  }

  function restore(): void {
    const saved = readSaved(storageKey);
    if (saved) setPos(saved.left, saved.top);
  }

  function onDown(ev: PointerEvent): void {
    if (ev.button !== 0) return;
    normalize();
    pressed = true;
    moved = false;
    activePointer = ev.pointerId;
    startX = ev.clientX;
    startY = ev.clientY;
    const pos = currentPos();
    originLeft = pos.left;
    originTop = pos.top;
    handle.setPointerCapture(ev.pointerId);
  }

  function onMove(ev: PointerEvent): void {
    if (!pressed || ev.pointerId !== activePointer) return;
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (!moved) {
      if (Math.hypot(dx, dy) <= DRAG_THRESHOLD) return;
      moved = true;
      el.classList.add('is-dragging');
      el.style.zIndex = String((zTop += 1));
    }
    setPos(originLeft + dx, originTop + dy);
  }

  function finish(ev: PointerEvent): void {
    if (!pressed) return;
    pressed = false;
    if (handle.hasPointerCapture(ev.pointerId)) handle.releasePointerCapture(ev.pointerId);
    activePointer = -1;
    if (!moved) return;
    moved = false;
    el.classList.remove('is-dragging');
    lastDragEnd = performance.now();
    save();
  }

  /** 拖动之后的那一次 click 不能落到元素自己的处理上（Surrender 不能被误触）。 */
  function onClickCapture(ev: MouseEvent): void {
    if (performance.now() - lastDragEnd > CLICK_GUARD_MS) return;
    ev.stopPropagation();
    ev.preventDefault();
  }

  function onResize(): void {
    if (!normalized) return;
    const { left, top } = currentPos();
    setPos(left, top);
    save();
  }

  handle.addEventListener('pointerdown', onDown);
  handle.addEventListener('pointermove', onMove);
  handle.addEventListener('pointerup', finish);
  handle.addEventListener('pointercancel', finish);
  el.addEventListener('click', onClickCapture, true);
  window.addEventListener('resize', onResize);

  restore();

  return () => {
    handle.removeEventListener('pointerdown', onDown);
    handle.removeEventListener('pointermove', onMove);
    handle.removeEventListener('pointerup', finish);
    handle.removeEventListener('pointercancel', finish);
    el.removeEventListener('click', onClickCapture, true);
    window.removeEventListener('resize', onResize);
  };
}

function readSaved(storageKey: string | null): SavedPos | null {
  if (!storageKey) return null;
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { left?: unknown; top?: unknown } | null;
    const left = Number(parsed?.left);
    const top = Number(parsed?.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) return null;
    return { left, top };
  } catch {
    return null;
  }
}