/**
 * keyboard.ts — 键盘输入（R6.2 / R6.4 / R6.6）。
 *
 * 映射：
 *   W/S/A/D  → 向 4 相邻调兵并选中目标地块
 *   Q        → 清空完整调兵链
 *   E        → 取消最新调兵请求
 *   + / =    → 比例 +10%
 *   - / _    → 比例 -10%
 *   方向键   → 平移摄像机
 *   滚轮      → 缩放（见 pointer.ts）
 *   Enter    → 聚焦聊天框
 *
 * 只把「意图」回调出去，不直接改状态（装配在 main.ts）。
 */

export interface KeyboardHandlers {
  onMove: (dir: 'up' | 'down' | 'left' | 'right') => void;
  onClear: () => void;
  onPop: () => void;
  onRatio: (delta: number) => void;
  onPan: (dx: number, dy: number) => void;
  onChat: () => void;
  /** 聊天框是否获得焦点（此时不处理游戏按键）。 */
  isTyping: () => boolean;
}

const MOVE_KEYS: Record<string, 'up' | 'down' | 'left' | 'right'> = {
  w: 'up',
  s: 'down',
  a: 'left',
  d: 'right',
};

export function bindKeyboard(h: KeyboardHandlers): () => void {
  const handler = (ev: KeyboardEvent): void => {
    if (h.isTyping()) return;
    const key = ev.key.toLowerCase();

    const dir = MOVE_KEYS[key];
    if (dir) {
      ev.preventDefault();
      h.onMove(dir);
      return;
    }
    switch (key) {
      case 'q':
        ev.preventDefault();
        h.onClear();
        break;
      case 'e':
        ev.preventDefault();
        h.onPop();
        break;
      case '+':
      case '=':
        ev.preventDefault();
        h.onRatio(0.1);
        break;
      case '-':
      case '_':
        ev.preventDefault();
        h.onRatio(-0.1);
        break;
      case 'arrowup':
        ev.preventDefault();
        h.onPan(0, -0.5);
        break;
      case 'arrowdown':
        ev.preventDefault();
        h.onPan(0, 0.5);
        break;
      case 'arrowleft':
        ev.preventDefault();
        h.onPan(-0.5, 0);
        break;
      case 'arrowright':
        ev.preventDefault();
        h.onPan(0.5, 0);
        break;
      case 'enter':
        ev.preventDefault();
        h.onChat();
        break;
      default:
        break;
    }
  };
  window.addEventListener('keydown', handler);
  return () => window.removeEventListener('keydown', handler);
}
