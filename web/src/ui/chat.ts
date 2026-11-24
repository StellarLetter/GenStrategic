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
 *
 * 收起（纯本地 UI 状态，不写 localStorage，刷新复位）：
 *   拖动条右端有一颗 `—` 按钮；收起后消息与输入框全部隐藏，面板缩成一颗
 *   「聊天 ▸」小按钮（仍然可拖），点它恢复完整面板。
 */

import type { ClientState } from '../core/state';
import type { ChatLine, ColorName, SystemChatKind } from '../core/types';
import { PLAYER_COLORS } from '../render/palette';

const PLACEHOLDER = '按 [enter] 聊天，按 [tab] 打开表情选择器。';
const RECORDING_HINT = 'Chat is being recorded.';

export class Chat {
  private readonly root: HTMLDivElement;
  private readonly handle: HTMLDivElement;
  private readonly hideButton: HTMLButtonElement;
  private readonly expandButton: HTMLButtonElement;
  private readonly messages: HTMLDivElement;
  private readonly input: HTMLInputElement;
  private rendered = 0;
  /** 常驻提示行只插一次，且与是否已有播报无关。 */
  private hinted = false;
  /** 是否已收起（纯本地 UI 状态，不写 localStorage，刷新复位）。 */
  private collapsed = false;

  onSend: (text: string) => void = () => {};

  constructor(root: HTMLElement) {
    this.root = document.createElement('div');
    this.root.id = 'in-game-chat';

    // 顶部窄拖动条：面板整体作为浮动层时用它作 handle，不占消息滚动与输入框的交互。
    // 收起后它仍然存在，且缩成一颗「聊天 ▸」小按钮 —— 所以面板收起后依然能拖。
    this.handle = document.createElement('div');
    this.handle.className = 'chat-drag-handle';

    // 两个切换按钮。
    //   · 隐藏按钮在展开态：按下时不冒泡到拖动握把（否则指针被面板捕获，click 落不到按钮上）；
    //     握把其余 340 px 仍然可拖，不损失拖动能力。
    //   · 展开按钮在收起态：它本身就是面板唯一可见的部分，**必须**能让 pointerdown 冒泡到
    //     握把，否则收起后就拖不动了。冒泡后 click 会被重定向到握把，因此「展开」还挂在
    //     面板根上兜底（见构造函数末尾），两条路径都是幂等的。
    this.hideButton = makeToggle('chat-hide-button', '—', '隐藏聊天');
    this.hideButton.addEventListener('pointerdown', (ev) => ev.stopPropagation());
    this.hideButton.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this.setCollapsed(true);
    });
    this.expandButton = makeToggle('chat-expand-button', '聊天 ▸', '展开聊天');
    this.expandButton.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this.setCollapsed(false);
    });
    this.handle.append(this.hideButton, this.expandButton);

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

    this.root.append(this.handle, this.messages, wrapper);
    root.appendChild(this.root);
    this.syncCollapsed();

    // 收起态下点面板任意处（此时只剩「聊天 ▸」那一颗）都展开。
    // 兜底用：展开按钮的 pointerdown 会让握把捕获指针，click 可能被重定向到握把而不是按钮。
    this.root.addEventListener('click', () => {
      if (this.collapsed) this.setCollapsed(false);
    });

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

  /** 根元素（装配点据此把它做成浮动层）。 */
  get element(): HTMLDivElement {
    return this.root;
  }

  /** 拖动握把：面板顶部的窄条（收起态下就是那颗「聊天 ▸」按钮所在的条）。 */
  get dragHandle(): HTMLDivElement {
    return this.handle;
  }

  /** 收起 / 展开聊天（收起 = 消息与输入框全部隐藏，只留「聊天 ▸」小按钮）。 */
  setCollapsed(collapsed: boolean): void {
    if (collapsed === this.collapsed) return;
    this.collapsed = collapsed;
    this.root.classList.toggle('is-collapsed', collapsed);
    this.syncCollapsed();
    this.keepInViewport();
    if (!collapsed) this.messages.scrollTop = this.messages.scrollHeight;
  }

  // 面板尺寸在收起/展开时变化很大（340×38vh ↔ 一颗小按钮）。若位置已经被拖动固化成
  // px 的 left/top，这里把它按新尺寸重新夹回视口，否则「点展开」后整个面板会跑到屏幕外
  // （pointerdown 时浮动层会把**收起态**的左上角固化成 left/top，随后撑开的宽度就溢出了）。
  private keepInViewport(): void {
    const { style } = this.root;
    if (!style.left) return; // 未固化：仍用 CSS 的 right/bottom 锚点，无需处理
    const left = Number.parseFloat(style.left);
    const top = Number.parseFloat(style.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) return;
    const w = this.root.offsetWidth;
    const h = this.root.offsetHeight;
    const maxLeft = Math.max(0, window.innerWidth - w);
    const maxTop = Math.max(0, window.innerHeight - h);
    style.left = `${Math.round(Math.min(maxLeft, Math.max(0, left)))}px`;
    style.top = `${Math.round(Math.min(maxTop, Math.max(0, top)))}px`;
  }

  private syncCollapsed(): void {
    this.hideButton.hidden = this.collapsed;
    this.expandButton.hidden = !this.collapsed;
  }

  /** 键盘输入进入聊天框；若聊天已收起，先展开再聚焦。 */
  focus(): void {
    this.setCollapsed(false);
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

/** 拖动条里的小切换按钮（收起 / 展开）；事件绑定由调用方决定。 */
function makeToggle(id: string, text: string, title: string): HTMLButtonElement {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.id = id;
  btn.className = 'chat-toggle';
  btn.textContent = text;
  btn.title = title;
  return btn;
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
