/**
 * chat.ts — 右下角聊天与系统消息（事件）。
 *
 * 视觉基准：SaveWeb/屏幕截图 2026-11-18 190745.png
 *   黑底灰字，系统消息形如 `▓ friend-general captured ▓ aaa!.37.`
 *   输入框 placeholder：`按 [enter] 聊天，按 [tab] 打开表情选择器。`
 */

import type { ClientState } from '../core/state';
import type { ChatLine, ColorName } from '../core/types';
import { PLAYER_COLORS } from '../render/palette';

const PLACEHOLDER = '按 [enter] 聊天，按 [tab] 打开表情选择器。';

export class Chat {
  private readonly root: HTMLDivElement;
  private readonly messages: HTMLDivElement;
  private readonly input: HTMLInputElement;
  private rendered = 0;

  onSend: (text: string) => void = () => {};

  constructor(root: HTMLElement) {
    this.root = document.createElement('div');
    this.root.id = 'in-game-chat';

    this.messages = document.createElement('div');
    this.messages.className = 'chat-messages-container';

    const wrapper = document.createElement('div');
    wrapper.className = 'chat-input-wrapper';
    this.input = document.createElement('input');
    this.input.id = 'chatroom-input';
    this.input.type = 'text';
    this.input.autocomplete = 'off';
    this.input.placeholder = PLACEHOLDER;
    wrapper.appendChild(this.input);

    this.root.append(this.messages, wrapper);
    root.appendChild(this.root);

    this.input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        const text = this.input.value.trim();
        if (text) this.onSend(text);
        this.input.value = '';
        this.input.blur();
        ev.preventDefault();
        ev.stopPropagation();
      } else if (ev.key === 'Escape') {
        this.input.blur();
        ev.stopPropagation();
      }
    });
  }

  /** 键盘输入进入聊天框。 */
  focus(): void {
    this.input.focus();
  }

  get focused(): boolean {
    return document.activeElement === this.input;
  }

  render(state: ClientState): void {
    if (state.chat.length === this.rendered) return;
    const start = Math.max(0, this.rendered - 0);
    for (let i = start; i < state.chat.length; i += 1) {
      this.messages.appendChild(renderLine(state.chat[i]!));
    }
    this.rendered = state.chat.length;
    this.messages.scrollTop = this.messages.scrollHeight;
  }
}

function colorSwatch(name: ColorName): HTMLSpanElement {
  const outer = document.createElement('span');
  outer.className = 'inline-color-block';
  const inner = document.createElement('span');
  inner.className = `inline-color-block ${name}`;
  inner.style.background = PLAYER_COLORS[name];
  outer.appendChild(inner);
  return outer;
}

function renderLine(line: ChatLine): HTMLParagraphElement {
  const p = document.createElement('p');
  p.className = 'chat-message server-chat-message';

  const spacer = document.createElement('span');
  spacer.className = 'announcement-spacer';
  p.appendChild(spacer);

  const body = document.createElement('span');
  if (line.system) {
    body.textContent = line.text;
  } else {
    body.appendChild(colorSwatch(line.color));
    body.appendChild(document.createTextNode(`${line.name}: ${line.text}`));
  }
  p.appendChild(body);

  if (line.turn >= 0) {
    const turn = document.createElement('span');
    turn.className = 'turn';
    turn.textContent = `${line.turn}.`;
    p.appendChild(turn);
  }
  return p;
}

export function systemLine(turn: number, text: string, color: ColorName = 'red'): ChatLine {
  return { turn, name: '', color, text, system: true };
}
