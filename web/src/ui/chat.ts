/**
 * chat.ts — 右下角聊天与系统消息（事件）。
 *
 * 视觉基准：SaveWeb/屏幕截图 2026-11-18 190745.png
 *   黑底灰字，系统播报只三种（R6.10）：
 *     `▓ <capturer 色块> X captured <victim 色块> Y. 37.`
 *     `▓ <本人色块> X surrendered. 12.`
 *     `▓ <本人色块> X left. 32.`
 *   首行常驻提示 `Chat is being recorded.`（无回合号、无颜色块）。
 *   输入框 placeholder：`按 [enter] 聊天，按 [tab] 打开表情选择器。`
 */

import type { ClientState } from '../core/state';
import type { ChatLine, ColorName, SystemChatKind } from '../core/types';
import { PLAYER_COLORS } from '../render/palette';

const PLACEHOLDER = '按 [enter] 聊天，按 [tab] 打开表情选择器。';
const RECORDING_HINT = 'Chat is being recorded.';

export class Chat {
  private readonly root: HTMLDivElement;
  private readonly messages: HTMLDivElement;
  private readonly input: HTMLInputElement;
  private rendered = 0;
  /** 常驻提示行只插一次，且与是否已有播报无关。 */
  private hinted = false;

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
    if (!this.hinted) {
      // 原站的常驻提示行：进入对局即显示，早于任何播报（不带回合号，R6.10 不涉及）。
      this.messages.appendChild(
        renderLine({ turn: -1, name: '', color: 'red', text: RECORDING_HINT, system: true }),
      );
      this.hinted = true;
    }
    if (state.chat.length === this.rendered) return;
    for (let i = this.rendered; i < state.chat.length; i += 1) {
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
  if (!line.system) {
    body.appendChild(colorSwatch(line.color));
    body.appendChild(document.createTextNode(`${line.name}: ${line.text}`));
  } else {
    switch (line.kind) {
      case 'captured':
        body.appendChild(colorSwatch(line.color));
        body.appendChild(
          document.createTextNode(`${line.name} captured `),
        );
        if (line.target) {
          body.appendChild(colorSwatch(line.targetColor ?? line.color));
          body.appendChild(document.createTextNode(line.target));
        }
        body.appendChild(document.createTextNode('.'));
        break;
      case 'surrendered':
        body.appendChild(colorSwatch(line.color));
        body.appendChild(document.createTextNode(`${line.name} surrendered.`));
        break;
      case 'left':
        body.appendChild(colorSwatch(line.color));
        body.appendChild(document.createTextNode(`${line.name} left.`));
        break;
      default:
        // 没有结构化 kind 的是纯文本提示（如常驻的 "Chat is being recorded."）。
        body.textContent = line.text;
        break;
    }
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

export function systemLine(
  turn: number,
  text: string,
  color: ColorName = 'red',
  kind?: SystemChatKind,
  target?: string,
  targetColor?: ColorName,
): ChatLine {
  return { turn, name: '', color, text, system: true, kind, target, targetColor };
}
