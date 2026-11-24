/**
 * turn.ts — 左上角回合计数器（R1.2）。
 *
 * 视觉基准：SaveWeb/屏幕截图 2026-11-18 190745.png 左上 `Turn 102.`
 * 上半回合显示 `Turn N`，下半回合显示 `Turn N.`（带句点）。
 */

import type { ClientState } from '../core/state';

export class TurnCounter {
  private readonly el: HTMLDivElement;
  private last = '';

  constructor(root: HTMLElement) {
    this.el = document.createElement('div');
    this.el.id = 'turn-counter';
    root.appendChild(this.el);
  }

  /** 根元素（装配点据此把它做成浮动层）。 */
  get element(): HTMLDivElement {
    return this.el;
  }

  render(state: ClientState): void {
    const text = formatTurn(state.turn, state.half);
    if (text === this.last) return;
    this.last = text;
    this.el.textContent = text;
  }
}

export function formatTurn(turn: number, half: 0 | 1): string {
  return `Turn ${turn}${half === 1 ? '.' : ''}`;
}
