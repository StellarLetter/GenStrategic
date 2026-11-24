/**
 * leaderboard.ts — 右侧排行榜面板（R6.8）。
 *
 * 视觉基准：SaveWeb/屏幕截图 2026-11-18 190453.png / 190745.png
 *   表头 `★ | Player | Army | Land`
 *   行：金色 ★ + 星数 / 玩家色块 + 名字（出局加骷髅）/ Army / Land
 *   已出局行底色 #666666。
 */

import type { ClientState } from '../core/state';
import type { LeaderRow as Row } from '../core/types';

const CONTAINER_ID = 'game-leaderboard-container';

export class Leaderboard {
  private readonly root: HTMLDivElement;
  private readonly table: HTMLTableElement;
  private lastFingerprint = '';

  constructor(root: HTMLElement) {
    this.root = document.createElement('div');
    this.root.id = CONTAINER_ID;
    this.table = document.createElement('table');
    this.table.id = 'game-leaderboard';
    this.root.appendChild(this.table);
    root.appendChild(this.root);
  }

  /** 根元素（装配点据此把它做成浮动层）。 */
  get element(): HTMLDivElement {
    return this.root;
  }

  render(state: ClientState): void {
    const rows = state.leaderboard.length > 0 ? state.leaderboard : fallbackRows(state);
    const fp = fingerprint(rows);
    if (fp === this.lastFingerprint) return;
    this.lastFingerprint = fp;

    const tbody = document.createElement('tbody');

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

    for (const row of rows) {
      const tr = document.createElement('tr');
      if (!row.alive || row.land === 0) tr.classList.add('dead');

      const star = document.createElement('td');
      star.className = 'lb-star-col';
      star.innerHTML = `<span class="lb-stars" style="color:gold">★</span><span class="lb-stars">${row.star}</span>`;
      tr.appendChild(star);

      const name = document.createElement('td');
      name.className = `leaderboard-name ${row.color}`;
      if (!row.alive || row.land === 0) {
        const skull = document.createElement('span');
        skull.className = 'lb-skull';
        name.appendChild(skull);
      }
      const label = document.createElement('span');
      label.className = 'lb-name-text';
      label.textContent = row.name;
      name.appendChild(label);
      tr.appendChild(name);

      const army = document.createElement('td');
      army.textContent = String(row.army);
      tr.appendChild(army);

      const land = document.createElement('td');
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
  return rows.map((r) => `${r.playerId}:${r.army}:${r.land}:${r.alive}:${r.star}`).join('|');
}
