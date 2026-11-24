/**
 * chatlist.ts — 回放页的聊天/系统消息列表。
 *
 * 与对局页的 `ui/chat.ts` 区别：回放的聊天是**只读**且会被**回溯**（拖时间轴往回调），
 * 所以按「已渲染条数」增量追加的做法不成立 —— 这里按状态里的条数直接重建（上限 200 条，
 * 重建开销可以忽略），并在内容指纹未变化时跳过。
 */

import type { ClientState } from '../core/state';
import type { ChatLine, ColorName } from '../core/types';
import { PLAYER_COLORS } from '../render/palette';

const RECORDING_HINT = 'Chat is being recorded.';

export class ChatList {
  private readonly root: HTMLDivElement;
  private readonly messages: HTMLDivElement;
  private lastFingerprint = '';

  constructor(parent: HTMLElement) {
    this.root = document.createElement('div');
    this.root.id = 'replay-chat';
    this.root.className = 'replay-floating';

    const head = document.createElement('div');
    head.className = 'replay-panel-head';
    const title = document.createElement('span');
    title.className = 'replay-panel-title';
    title.textContent = '聊天 / 播报';
    head.appendChild(title);

    this.messages = document.createElement('div');
    this.messages.className = 'chat-messages-container replay-chat-messages';
    const hint = document.createElement('p');
    hint.className = 'chat-message server-chat-message';
    hint.textContent = RECORDING_HINT;
    this.messages.appendChild(hint);

    this.root.append(head, this.messages);
    parent.appendChild(this.root);
  }

  get element(): HTMLDivElement {
    return this.root;
  }

  get dragHandle(): HTMLElement {
    return this.root.querySelector('.replay-panel-head') as HTMLElement;
  }

  /** 用当前状态里的聊天重绘（内容没变则直接返回）。 */
  render(state: ClientState): void {
    const fp = state.chat.map((line) => `${line.turn}|${line.kind ?? ''}|${line.text}`).join('\n');
    if (fp === this.lastFingerprint) return;
    this.lastFingerprint = fp;
    const body = state.chat.map((line) => renderLine(line));
    const hint = document.createElement('p');
    hint.className = 'chat-message server-chat-message';
    hint.textContent = RECORDING_HINT;
    this.messages.replaceChildren(hint, ...body);
    this.messages.scrollTop = this.messages.scrollHeight;
  }
}

function colorSwatch(name: ColorName): HTMLSpanElement {
  const outer = document.createElement('span');
  outer.className = 'inline-color-block';
  outer.style.background = PLAYER_COLORS[name] ?? '#888';
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
  } else if (line.kind === 'captured') {
    body.appendChild(colorSwatch(line.color));
    body.appendChild(document.createTextNode(`${line.name} captured `));
    if (line.target) {
      body.appendChild(colorSwatch(line.targetColor ?? line.color));
      body.appendChild(document.createTextNode(line.target));
    }
    body.appendChild(document.createTextNode('.'));
  } else if (line.kind === 'surrendered') {
    body.appendChild(colorSwatch(line.color));
    body.appendChild(document.createTextNode(`${line.name} surrendered.`));
  } else if (line.kind === 'left') {
    body.appendChild(colorSwatch(line.color));
    body.appendChild(document.createTextNode(`${line.name} left.`));
  } else {
    body.textContent = line.text;
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