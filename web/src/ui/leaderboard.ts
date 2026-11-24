/**
 * leaderboard.ts — 右侧排行榜面板（R6.8）。
 *
 * 视觉基准：SaveWeb/屏幕截图 2026-11-18 190453.png / 190745.png
 *   表头 `★ | Player | Army | Land`
 *   行：金色 ★ + 星数 / 玩家色块 + 名字（出局加骷髅）/ Army / Land
 *   已出局行底色 #666666。
 *
 * 折叠（纯本地 UI 状态，不发网络帧、不写 localStorage，刷新复位）：
 *   面板右下角的小切换按钮 `▾` / `▸`。折叠态**没有表头、没有玩家名、没有星数**，
 *   每行只有「玩家色块 + Army + Land」三个单元格（色块沿用 `.leaderboard-name.<color>`）。
 */

import type { ClientState } from '../core/state';
import type { LeaderRow as Row } from '../core/types';

const CONTAINER_ID = 'game-leaderboard-container';

export class Leaderboard {
  private readonly root: HTMLDivElement;
  private readonly table: HTMLTableElement;
  private readonly toggle: HTMLButtonElement;
  private collapsed = false;
  private lastFingerprint = '';
  /** 最近一次渲染用的行，供折叠/展开时就地重建（不依赖再来一帧 state）。 */
  private rows: Row[] = [];

  constructor(root: HTMLElement) {
    this.root = document.createElement('div');
    this.root.id = CONTAINER_ID;

    this.table = document.createElement('table');
    this.table.id = 'game-leaderboard';

    // 折叠/展开切换：只是本地 UI 状态（R6.14 之外的纯前端偏好）。
    this.toggle = document.createElement('button');
    this.toggle.type = 'button';
    this.toggle.id = 'leaderboard-toggle';
    // 按钮压在浮动面板里：按下的 pointerdown 不能冒泡到拖动握把，否则会启动拖动、
    // 指针被 panel 捕获后 click 就落不到按钮上（见 ui/floating.ts）。
    this.toggle.addEventListener('pointerdown', (ev) => ev.stopPropagation());
    this.toggle.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this.setCollapsed(!this.collapsed);
    });

    this.root.append(this.table, this.toggle);
    root.appendChild(this.root);
    this.syncToggle();
  }

  /** 根元素（装配点据此把它做成浮动层）。 */
  get element(): HTMLDivElement {
    return this.root;
  }

  /** 折叠/展开记分板（本地 UI 状态，不持久化）。 */
  setCollapsed(collapsed: boolean): void {
    if (collapsed === this.collapsed) return;
    this.collapsed = collapsed;
    this.root.classList.toggle('is-collapsed', collapsed);
    this.syncToggle();
    this.lastFingerprint = '';
    this.build(this.rows);
  }

  private syncToggle(): void {
    this.toggle.textContent = this.collapsed ? '▸' : '▾';
    this.toggle.title = this.collapsed ? '展开记分板' : '折叠记分板';
  }

  render(state: ClientState): void {
    const rows = state.leaderboard.length > 0 ? state.leaderboard : fallbackRows(state);
    const fp = `${this.collapsed ? 'c' : 'e'}|${fingerprint(rows)}`;
    if (fp === this.lastFingerprint) return;
    this.lastFingerprint = fp;
    this.rows = rows;
    this.build(rows);
  }

  private build(rows: Row[]): void {
    const tbody = document.createElement('tbody');

    // 表头只在展开态出现：折叠态完全没有表头。
    if (!this.collapsed) {
      const head = document.createElement('tr');
      for (let i = 0; i < 4; i += 1) {
        const td = document.createElement('td');
        if (i === 0) td.className = 'lb-star-col';
        head.appendChild(td);
      }
      head.children[0]!.innerHTML = '<span class="lb-stars" style="color:gold">★</span>';
      head.children[1]!.textContent = 'Player';
      head.children[2]!.textContent = 'Army';
      head.children[3]!.textContent = 'Land';
      tbody.appendChild(head);
    }

    for (const row of rows) {
      const tr = document.createElement('tr');
      // 玩家行带 .lb-row：折叠态没有表头，若表头样式（`tr:first-child td`）不排除玩家行，
      // 折叠后第一行玩家色块就会被表头的底色盖掉。
      tr.classList.add('lb-row');
      const dead = !row.alive || row.land === 0;
      if (dead) tr.classList.add('dead');

      // 星数栏只在展开态出现（折叠态没有星数）。
      if (!this.collapsed) {
        const star = document.createElement('td');
        star.className = 'lb-star-col';
        star.innerHTML = `<span class="lb-stars" style="color:gold">★</span><span class="lb-stars">${row.star}</span>`;
        tr.appendChild(star);
      }

      // 玩家色块单元格：展开态里它就是「色块 + 名字」那一格；折叠态只剩色块本身。
      const name = document.createElement('td');
      name.className = `leaderboard-name ${row.color}`;
      if (!this.collapsed) {
        if (dead) {
          const skull = document.createElement('span');
          skull.className = 'lb-skull';
          name.appendChild(skull);
        }
        const label = document.createElement('span');
        label.className = 'lb-name-text';
        label.textContent = row.name;
        name.appendChild(label);
      }
      tr.appendChild(name);

      const army = document.createElement('td');
      army.className = 'lb-number';
      army.textContent = String(row.army);
      tr.appendChild(army);

      const land = document.createElement('td');
      land.className = 'lb-number lb-land';
      land.textContent = String(row.land);
      tr.appendChild(land);

      tbody.appendChild(tr);
    }

    this.table.replaceChildren(tbody);
  }
}

function fallbackRows(state: ClientState): Row[] {
  return state.players.map((p) => ({
    playerId: p.playerId,
    name: p.name,
    color: p.color,
    army: 0,
    land: 0,
    alive: true,
    surrenderIn: null,
    star: 0,
  }));
}

function fingerprint(rows: Row[]): string {
  return rows.map((r) => `${r.playerId}:${r.name}:${r.color}:${r.army}:${r.land}:${r.alive}:${r.star}`).join('|');
}
