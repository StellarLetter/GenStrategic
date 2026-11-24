/**
 * controls.ts — 回放页底部控制条：播放/暂停、倍速、时间轴、Tick/回合文本。
 *
 * 只发回调、不持有回放逻辑（回放逻辑在 `core/replay.ts` 的 `ReplayCursor`）。
 */

import { formatClock } from '../core/replay';

export const SPEEDS = [0.5, 1, 2, 4, 8, 16] as const;
export type Speed = (typeof SPEEDS)[number];

export class ReplayControls {
  private readonly root: HTMLDivElement;
  private readonly playButton: HTMLButtonElement;
  private readonly speedSelect: HTMLSelectElement;
  private readonly slider: HTMLInputElement;
  private readonly tickLabel: HTMLSpanElement;
  private readonly clockLabel: HTMLSpanElement;
  private readonly hint: HTMLSpanElement;
  private maxTick = 0;
  private tickMs = 500;
  private seeking = false;

  /** 播放/暂停被点击。 */
  onToggle: () => void = () => {};
  /** 用户拖动时间轴到某个 Tick。 */
  onSeek: (tick: number) => void = () => {};
  /** 倍速切换。 */
  onSpeed: (speed: Speed) => void = () => {};

  constructor(parent: HTMLElement) {
    this.root = document.createElement('div');
    this.root.id = 'replay-controls';
    this.root.className = 'replay-floating';

    this.playButton = document.createElement('button');
    this.playButton.type = 'button';
    this.playButton.id = 'replay-play-button';
    this.playButton.className = 'replay-button';
    this.playButton.textContent = '▶ 播放';
    this.playButton.addEventListener('pointerdown', (ev) => ev.stopPropagation());
    this.playButton.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this.onToggle();
    });

    this.speedSelect = document.createElement('select');
    this.speedSelect.className = 'replay-select';
    this.speedSelect.title = '播放倍速';
    for (const speed of SPEEDS) {
      const option = document.createElement('option');
      option.value = String(speed);
      option.textContent = `${speed}×`;
      if (speed === 1) option.selected = true;
      this.speedSelect.appendChild(option);
    }
    this.speedSelect.addEventListener('change', () => {
      const value = Number(this.speedSelect.value);
      this.onSpeed((SPEEDS.includes(value as Speed) ? value : 1) as Speed);
    });

    this.slider = document.createElement('input');
    this.slider.type = 'range';
    this.slider.id = 'replay-seek';
    this.slider.min = '0';
    this.slider.max = '0';
    this.slider.value = '0';
    this.slider.step = '1';
    // 拖动时不打断播放状态：按下暂停更新、松开再跳（避免每像素重建一次状态）。
    this.slider.addEventListener('pointerdown', () => {
      this.seeking = true;
    });
    this.slider.addEventListener('input', () => {
      this.tickLabel.textContent = tickText(Number(this.slider.value));
    });
    const commit = (): void => {
      if (!this.seeking) return;
      this.seeking = false;
      this.onSeek(Number(this.slider.value));
    };
    this.slider.addEventListener('change', commit);
    this.slider.addEventListener('pointerup', commit);

    this.tickLabel = document.createElement('span');
    this.tickLabel.className = 'replay-tick-label';
    this.clockLabel = document.createElement('span');
    this.clockLabel.className = 'replay-clock-label';
    this.hint = document.createElement('span');
    this.hint.className = 'replay-controls-hint';

    this.root.append(this.playButton, this.speedSelect, this.slider, this.tickLabel, this.clockLabel, this.hint);
    parent.appendChild(this.root);
  }

  get element(): HTMLDivElement {
    return this.root;
  }

  /** 载入一局的时间轴范围。 */
  load(maxTick: number, tickMs: number): void {
    this.maxTick = Math.max(0, maxTick);
    this.tickMs = tickMs || 500;
    this.slider.max = String(this.maxTick);
    this.slider.value = '0';
    this.render(0, false);
  }

  /** 同步到当前 Tick 与播放状态。 */
  render(tick: number, playing: boolean): void {
    if (!this.seeking) this.slider.value = String(Math.max(0, tick));
    this.playButton.textContent = playing ? '⏸ 暂停' : '▶ 播放';
    this.tickLabel.textContent = tickText(Math.max(0, tick));
    this.clockLabel.textContent = `${formatClock(Math.max(0, tick) * this.tickMs)} / ${formatClock(this.maxTick * this.tickMs)}`;
  }

  /** 右侧提示（例如「对局中断」「帧流被截断」）。 */
  setHint(text: string): void {
    this.hint.textContent = text;
  }
}

function tickText(tick: number): string {
  return `Tick ${tick}`;
}