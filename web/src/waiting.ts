/**
 * waiting.ts — 房间列表页（PROTOCOL §4 `GET /api/rooms`）。
 *
 * 只做 DOM 渲染与跳转；不涉及游戏规则。
 */

interface RoomSummary {
  id: string;
  name: string;
  players: number;
  maxPlayers: number;
  state: string;
}

const tbody = document.getElementById('room-tbody') as HTMLTableSectionElement;
const emptyHint = document.getElementById('empty-hint') as HTMLParagraphElement;
const nickname = document.getElementById('nickname') as HTMLInputElement;
const refreshButton = document.getElementById('refresh-button') as HTMLButtonElement;
const createButton = document.getElementById('create-button') as HTMLButtonElement;

nickname.value = localStorage.getItem('generals:name') ?? '';
nickname.addEventListener('change', () => {
  localStorage.setItem('generals:name', nickname.value.trim());
});

function roomUrl(roomId: string | null): string {
  const name = nickname.value.trim() || 'Player';
  localStorage.setItem('generals:name', name);
  const p = new URLSearchParams({ name });
  if (roomId) p.set('room', roomId);
  return `/?${p.toString()}`;
}

async function refresh(): Promise<void> {
  tbody.replaceChildren();
  try {
    const res = await fetch('/api/rooms');
    if (!res.ok) throw new Error(String(res.status));
    const rooms = (await res.json()) as RoomSummary[];
    emptyHint.hidden = rooms.length > 0;
    for (const room of rooms) {
      const tr = document.createElement('tr');

      const name = document.createElement('td');
      name.textContent = room.name || room.id;
      tr.appendChild(name);

      const players = document.createElement('td');
      players.textContent = `${room.players}/${room.maxPlayers}`;
      tr.appendChild(players);

      const state = document.createElement('td');
      state.textContent = room.state;
      tr.appendChild(state);

      const action = document.createElement('td');
      const button = document.createElement('button');
      button.type = 'button';
      const canJoin = room.players < room.maxPlayers && room.state === 'waiting';
      button.textContent = canJoin ? '加入' : '观战';
      button.addEventListener('click', () => {
        location.href = canJoin ? roomUrl(room.id) : `${roomUrl(room.id)}&spectate=1`;
      });
      action.appendChild(button);
      tr.appendChild(action);

      tbody.appendChild(tr);
    }
  } catch {
    emptyHint.hidden = false;
    emptyHint.textContent = '无法连接服务器。';
  }
}

refreshButton.addEventListener('click', () => void refresh());
createButton.addEventListener('click', () => {
  location.href = roomUrl(null);
});

void refresh();
