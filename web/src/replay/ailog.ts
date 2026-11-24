/**
 * ailog.ts — 回放页的「AI 调试流」面板（`docs/REPLAY.md` §2.2 / §5.1）。
 *
 * 纯展示 + 回调：点击条目由装配点决定（跳转到该条目所在 Tick）。
 * 支持按玩家筛、按 kind 筛、只看自己关心的级别。
 */

import type { AiKind, ReplayAiEntry, ReplayPlayer } from '../core/replay';
import { strategyOf } from '../core/replay';
import { PLAYER_COLORS } from '../render/palette';
import type { ColorName } from '../core/types';

const KIND_LABEL: Record<AiKind, string> = {
  log: '日志',
  action: '动作',
  error: '异常',
  reject: '被拒',
  drop: '作废',
};

const ALL_KINDS: AiKind[] = ['log', 'action', 'error', 'reject', 'drop'];

/** 一条条目的单行文本（回放页与 `tools/replay.py` 的输出语义一致）。 */
export function formatEntry(entry: ReplayAiEntry): string {
  switch (entry.kind) {
    case 'log':
      return `[${entry.level ?? 'debug'}] ${entry.text ?? ''}`;
    case 'action': {
      const action = entry.action;
      if (!action) return '(空动作)';
      if (action.op === 'move') return `move ${action.from} → ${action.dir} @${action.ratio}`;
      if (action.op === 'select') return `select ${action.tile}`;
      return action.op;
    }
    case 'error':
      return entry.text ?? '(异常)';
    case 'reject':
      return `reject ${entry.op ?? ''}#${entry.seq ?? 0} → ${entry.code ?? ''}`;
    case 'drop':
      return `drop ${entry.from} ${entry.dir} (${entry.reason ?? ''}, cascade=${entry.cascade ?? 0})`;
    default:
      return JSON.stringify(entry);
  }
}

export class AiLogPanel {
  private readonly root: HTMLDivElement;
  private readonly list: HTMLDivElement;
  private readonly playerSelect: HTMLSelectElement;
  private readonly countHint: HTMLSpanElement;
  private readonly kindToggles = new Map<AiKind, HTMLButtonElement>();
  private entries: ReplayAiEntry[] = [];
  private players: ReplayPlayer[] = [];
  private activeKinds = new Set<AiKind>(['log', 'action', 'error']);
  private activePlayer: number | null = null;
  private limit = 400;

  /** 点击某条条目（装配点实现：跳到该 Tick）。 */
  onPick: (tick: number) => void = () => {};

  constructor(parent: HTMLElement) {
    this.root = document.createElement('div');
    this.root.id = 'replay-ai-panel';
    this.root.className = 'replay-floating';

    const head = document.createElement('div');
    head.className = 'replay-panel-head';
    const title = document.createElement('span');
    title.className = 'replay-panel-title';
    title.textContent = 'AI 调试流';
    this.countHint = document.createElement('span');
    this.countHint.className = 'replay-panel-hint';
    head.append(title, this.countHint);

    const filters = document.createElement('div');
    filters.className = 'replay-ai-filters';

    this.playerSelect = document.createElement('select');
    this.playerSelect.className = 'replay-select';
    this.playerSelect.title = '只看某个玩家（含策略）';
    this.playerSelect.addEventListener('change', () => {
      const value = this.playerSelect.value;
      this.activePlayer = value === '' ? null : Number(value);
      this.rerender();
    });
    filters.appendChild(this.playerSelect);

    const kindBar = document.createElement('div');
    kindBar.className = 'replay-kind-bar';
    for (const kind of ALL_KINDS) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'replay-kind-toggle';
      btn.dataset['kind'] = kind;
      btn.textContent = KIND_LABEL[kind];
      btn.classList.toggle('is-on', this.activeKinds.has(kind));
      // pointerdown 不能冒泡到拖动握把，否则按钮点不动（同 ui/leaderboard.ts 的处理）。
      btn.addEventListener('pointerdown', (ev) => ev.stopPropagation());
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (this.activeKinds.has(kind)) this.activeKinds.delete(kind);
        else this.activeKinds.add(kind);
        btn.classList.toggle('is-on', this.activeKinds.has(kind));
        this.rerender();
      });
      this.kindToggles.set(kind, btn);
      kindBar.appendChild(btn);
    }
    filters.appendChild(kindBar);

    this.list = document.createElement('div');
    this.list.className = 'replay-ai-list';

    this.root.append(head, filters, this.list);
    parent.appendChild(this.root);
  }

  get element(): HTMLDivElement {
    return this.root;
  }

  /** 人类可读的头部父子关系（拖动握把 = 面板标题行）。 */
  get dragHandle(): HTMLElement {
    return this.root.querySelector('.replay-panel-head') as HTMLElement;
  }

  /**
   * 载入一局的玩家与条目。
   *
   * 前置：`players` 来自回放 `players`；`entries` 来自 `ai`（已按 tick 升序）。
   * 后置：玩家下拉框被重建（第一项 = 全部）；列表按当前筛选重绘。
   * 抛出：无。
   */
  load(players: ReplayPlayer[], entries: ReplayAiEntry[]): void {
    this.players = players;
    this.entries = entries;
    this.activePlayer = null;
    this.playerSelect.replaceChildren();
    const all = document.createElement('option');
    all.value = '';
    all.textContent = `全部玩家（${entries.length} 条）`;
    this.playerSelect.appendChild(all);
    const counts = new Map<number, number>();
    for (const entry of entries) {
      if (entry.playerId === null) continue;
      counts.set(entry.playerId, (counts.get(entry.playerId) ?? 0) + 1);
    }
    for (const player of players) {
      const option = document.createElement('option');
      option.value = String(player.playerId);
      const strategy = strategyOf(player);
      option.textContent = `${player.name}${strategy ? ` · ${strategy}` : ' · 人工'}（${counts.get(player.playerId) ?? 0}）`;
      this.playerSelect.appendChild(option);
    }
    this.rerender();
  }

  private matches(entry: ReplayAiEntry): boolean {
    if (!this.activeKinds.has(entry.kind)) return false;
    if (this.activePlayer !== null && entry.playerId !== this.activePlayer) return false;
    return true;
  }

  private rerender(): void {
    const shown = this.entries.filter((entry) => this.matches(entry));
    this.countHint.textContent = `${shown.length} / ${this.entries.length}`;
    const rows = document.createElement('div');
    const slice = shown.slice(0, this.limit);
    for (const entry of slice) {
      rows.appendChild(this.renderRow(entry));
    }
    if (shown.length > slice.length) {
      const more = document.createElement('p');
      more.className = 'replay-ai-more';
      more.textContent = `…还有 ${shown.length - slice.length} 条（用玩家 / 类型筛选缩小范围）`;
      rows.appendChild(more);
    }
    if (shown.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'replay-ai-more';
      empty.textContent = '（没有符合条件的条目）';
      rows.appendChild(empty);
    }
    this.list.replaceChildren(rows);
  }

  private renderRow(entry: ReplayAiEntry): HTMLDivElement {
    const row = document.createElement('div');
    row.className = `replay-ai-entry kind-${entry.kind}`;
    row.dataset['tick'] = String(entry.tick);

    const tick = document.createElement('span');
    tick.className = 'replay-ai-tick';
    tick.textContent = `t${entry.tick}`;

    const who = document.createElement('span');
    who.className = 'replay-ai-who';
    const player = this.players.find((p) => p.playerId === entry.playerId);
    if (player) {
      const swatch = document.createElement('span');
      swatch.className = 'inline-color-block';
      swatch.style.background = PLAYER_COLORS[player.color as ColorName] ?? '#888';
      who.appendChild(swatch);
      who.appendChild(document.createTextNode(player.name));
    } else {
      who.textContent = entry.playerId === null ? '系统' : `P${entry.playerId}`;
    }

    const kind = document.createElement('span');
    kind.className = 'replay-ai-kind';
    kind.textContent = KIND_LABEL[entry.kind] ?? entry.kind;

    const text = document.createElement('span');
    text.className = 'replay-ai-text';
    text.textContent = formatEntry(entry);

    row.append(tick, who, kind, text);
    row.title = '点击跳到该条目所在的 Tick';
    row.addEventListener('click', () => this.onPick(entry.tick));
    return row;
  }

  /** 高亮当前 Tick 附近的条目（播放时跟随）。 */
  highlightTick(tick: number): void {
    let best: HTMLElement | null = null;
    for (const node of Array.from(this.list.querySelectorAll<HTMLElement>('.replay-ai-entry'))) {
      const value = Number(node.dataset['tick']);
      const on = Number.isFinite(value) && value === tick;
      node.classList.toggle('is-now', on);
      if (on && !best) best = node;
    }
    best?.scrollIntoView({ block: 'nearest' });
  }
}