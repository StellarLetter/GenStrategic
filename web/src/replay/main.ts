/**
 * main.ts — 回放页装配点（`docs/REPLAY.md` §5.1）。
 *
 * URL：
 *   /replay.html            列出最近的回放（点击进入）
 *   /replay.html?id=<id>    播放一份回放
 *   ?autoplay=0             不自动播放（默认自动播放）
 *
 * 职责：读 `/api/replays` → `core/replay.ts` 的游标重放 → 复用 `render/` 与 `ui/` 的面板。
 * 不含任何规则：画面完全由回放帧驱动（与对局页同一套 `applyFrame`）。
 */

import { initialChain, type ChainState } from '../core/chains';
import {
  formatClock,
  formatWhen,
  parseReplay,
  strategyOf,
  ReplayCursor,
  type ReplayFile,
  type ReplayListResponse,
  type ReplaySummary,
} from '../core/replay';
import type { ClientState } from '../core/state';
import { bindPointer } from '../input/pointer';
import { cellSize, hitTest, makeCamera, mapOrigin, panPixels, zoomAt, type Camera } from '../render/camera';
import { paint } from '../render/painter';
import { PLAYER_COLORS } from '../render/palette';
import { Leaderboard } from '../ui/leaderboard';
import { formatTurn } from '../ui/turn';
import { makeDraggable } from '../ui/floating';
import { AiLogPanel } from './ailog';
import { ChatList } from './chatlist';
import { ReplayControls, type Speed } from './controls';

const params = new URLSearchParams(location.search);
const replayId = params.get('id') ?? '';
const AUTOPLAY = params.get('autoplay') !== '0';

const page = document.createElement('div');
page.id = 'replay-page';
document.body.appendChild(page);

// ---------------------------------------------------------------- 地图

const canvas = document.createElement('canvas');
canvas.id = 'replay-canvas';
page.appendChild(canvas);

const ctx = canvas.getContext('2d');
if (!ctx) throw new Error('canvas 2d context unavailable');

const EMPTY_CHAIN: ChainState = initialChain();
let cam: Camera = makeCamera(20, 800, 800, 1);
let state: ClientState | null = null;

function resize(): void {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(64, Math.floor(window.innerWidth));
  const h = Math.max(64, Math.floor(window.innerHeight));
  canvas.width = Math.floor(w * dpr);
  canvas.height = Math.floor(h * dpr);
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;
  cam = { ...cam, width: w, height: h, dpr };
  draw();
}

function draw(): void {
  if (!state) return;
  paint(ctx!, cam, state, EMPTY_CHAIN, { showChain: false, highlightTargets: false });
  leaderboard.render(state);
  chatList.render(state);
  turnEl.textContent = formatTurn(state.turn, state.half);
}

// ---------------------------------------------------------------- 面板

const leaderboard = new Leaderboard(page);
const chatList = new ChatList(page);
const aiPanel = new AiLogPanel(page);
const controls = new ReplayControls(page);

const header = document.createElement('div');
header.id = 'replay-header';
header.className = 'replay-floating';
const headerTitle = document.createElement('div');
headerTitle.className = 'replay-header-title';
const headerMeta = document.createElement('div');
headerMeta.className = 'replay-header-meta';
const backLink = document.createElement('a');
backLink.className = 'replay-back-link';
backLink.href = '/';
backLink.textContent = '← 对局页';
const listLink = document.createElement('a');
listLink.className = 'replay-back-link';
listLink.href = '/replay.html';
listLink.textContent = '回放列表';
const headerLinks = document.createElement('div');
headerLinks.className = 'replay-header-links';
headerLinks.append(backLink, listLink);
header.append(headerTitle, headerMeta, headerLinks);
page.appendChild(header);

const turnEl = document.createElement('div');
turnEl.id = 'turn-counter';
page.appendChild(turnEl);

for (const el of [
  header,
  turnEl,
  leaderboard.element,
  chatList.element,
  aiPanel.element,
  controls.element,
]) {
  makeDraggable(el, { storageKey: `generals.replay.panel.${el.id}` });
}

// ---------------------------------------------------------------- 播放器

let file: ReplayFile | null = null;
let cursor: ReplayCursor | null = null;
let playing = false;
let speed: Speed = 1;
/** 播放基准：上一次推进的墙钟时间（毫秒）与该时刻对应的「已播 Tick 小数」。 */
let anchorWall = 0;
let anchorTick = 0;
let rafId = 0;

function currentTickFloat(): number {
  if (!file) return 0;
  const wall = performance.now();
  const perTick = (file.tickMs || 500) / speed;
  return anchorTick + (wall - anchorWall) / perTick;
}

function renderAt(tick: number, { seek = false }: { seek?: boolean } = {}): void {
  if (!cursor) return;
  const target = Math.max(0, Math.min(cursor.maxTick, Math.floor(tick)));
  state = cursor.seek(target);
  controls.render(cursor.tick, playing);
  aiPanel.highlightTick(cursor.tick);
  draw();
  if (seek) {
    anchorWall = performance.now();
    anchorTick = cursor.tick;
  }
}

function schedule(): void {
  if (rafId) cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(frame);
}

function frame(): void {
  rafId = 0;
  if (!playing || !cursor || !file) return;
  const tick = Math.min(cursor.maxTick, Math.floor(currentTickFloat()));
  renderAt(tick);
  if (cursor.tick >= cursor.maxTick) {
    // 播到最后一刻：停下并停在终点（不自动循环，避免"看起来还在播"）。
    playing = false;
    controls.render(cursor.tick, playing);
    return;
  }
  rafId = requestAnimationFrame(frame);
}

function setPlaying(next: boolean): void {
  if (!cursor) return;
  if (next && cursor.tick >= cursor.maxTick) {
    // 已在终点：从头发一遍。
    seekTo(0);
  }
  playing = next;
  anchorWall = performance.now();
  anchorTick = Math.max(0, cursor.tick);
  controls.render(cursor.tick, playing);
  if (playing) schedule();
  else if (rafId) {
    cancelAnimationFrame(rafId);
    rafId = 0;
  }
}

function seekTo(tick: number): void {
  playing = false;
  if (rafId) {
    cancelAnimationFrame(rafId);
    rafId = 0;
  }
  renderAt(tick, { seek: true });
}

controls.onToggle = () => setPlaying(!playing);
controls.onSpeed = (next) => {
  const nowTick = playing && file ? currentTickFloat() : cursor?.tick ?? 0;
  speed = next;
  anchorWall = performance.now();
  anchorTick = nowTick;
  if (playing) schedule();
};
controls.onSeek = (tick) => seekTo(tick);
aiPanel.onPick = (tick) => {
  seekTo(tick);
  // 跳过去之后继续播，符合"点日志→看当时局面"的直觉。
  setPlaying(true);
};

// ---------------------------------------------------------------- 输入（缩放/平移，无选中）

bindPointer(canvas, {
  hitTest: (x, y) => hitTest(cam, x, y),
  // 回放里点击地块不改变任何状态（不选中、不调兵）。
  onClickTile: () => {},
  onPanPixels: (dx, dy) => {
    cam = panPixels(cam, dx, dy);
    draw();
  },
  onZoom: (dir, x, y) => {
    cam = zoomAt(cam, dir, x, y);
    draw();
  },
});

window.addEventListener('resize', resize);

document.addEventListener('keydown', (ev) => {
  if (ev.target instanceof HTMLInputElement || ev.target instanceof HTMLSelectElement) return;
  if (ev.code === 'Space') {
    ev.preventDefault();
    setPlaying(!playing);
  } else if (ev.key === 'ArrowLeft') {
    ev.preventDefault();
    seekTo(Math.max(0, (cursor?.tick ?? 0) - (ev.shiftKey ? 10 : 1)));
  } else if (ev.key === 'ArrowRight') {
    ev.preventDefault();
    seekTo(Math.min(cursor?.maxTick ?? 0, (cursor?.tick ?? 0) + (ev.shiftKey ? 10 : 1)));
  }
});

// ---------------------------------------------------------------- 载入

function badge(text: string, tone = ''): HTMLSpanElement {
  const span = document.createElement('span');
  span.className = `replay-badge ${tone}`.trim();
  span.textContent = text;
  return span;
}

function loadReplay(payload: ReplayFile): void {
  file = payload;
  cursor = new ReplayCursor(payload);
  state = cursor.state;
  cam = makeCamera(Math.max(1, payload.size), cam.width, cam.height, cam.dpr);

  const winner = payload.result.winner;
  const winnerName = payload.players.find((p) => p.playerId === winner)?.name ?? null;
  headerTitle.textContent = `回放 · 房间 ${payload.room}`;
  headerMeta.replaceChildren(
    badge(`${payload.size}×${payload.size}`, 'is-dim'),
    badge(`Turn ${payload.result.turn}`, 'is-dim'),
    badge(formatWhen(payload.startedAt), 'is-dim'),
    badge(
      payload.result.ended ? (winnerName ? `胜者 ${winnerName}` : '无人获胜') : `中断（${payload.reason}）`,
      payload.result.ended ? 'is-ok' : 'is-warn',
    ),
  );
  for (const player of payload.players) {
    const strategy = strategyOf(player);
    const chip = document.createElement('span');
    chip.className = 'replay-player-chip';
    const dot = document.createElement('span');
    dot.className = 'replay-player-dot';
    dot.style.background = PLAYER_COLORS[(player.color as keyof typeof PLAYER_COLORS) ?? 'red'] ?? '#888';
    chip.append(dot, document.createTextNode(`${player.name}${strategy ? ` · ${strategy}` : ''}`));
    headerMeta.appendChild(chip);
  }

  const hints: string[] = [];
  if (!payload.result.ended) hints.push(`对局中断（${payload.reason}）`);
  if (payload.truncated) hints.push('帧流触到上限，后半段未录');
  if (payload.aiTruncated) hints.push('AI 条目触到上限');
  controls.setHint(hints.join(' · '));

  aiPanel.load(payload.players, payload.ai);
  controls.load(cursor.maxTick, payload.tickMs);
  renderAt(0, { seek: true });
  if (AUTOPLAY) setPlaying(true);
}

async function boot(): Promise<void> {
  resize();
  if (!replayId) {
    await showList();
    return;
  }
  headerTitle.textContent = `回放 · ${replayId}`;
  headerMeta.replaceChildren(badge('载入中…', 'is-dim'));
  try {
    const response = await fetch(`/api/replays/${encodeURIComponent(replayId)}`);
    if (response.status === 409) {
      throw new Error('这一局仍在进行：回放要等对局结束后才能读取（R8.2）。');
    }
    if (response.status === 404) throw new Error('找不到这份回放（可能已被保留策略清理）。');
    if (!response.ok) throw new Error(`读取回放失败（HTTP ${response.status}）`);
    loadReplay(parseReplay(await response.json()));
  } catch (err) {
    showError(err instanceof Error ? err.message : String(err));
  }
}

function showError(message: string): void {
  const box = document.createElement('div');
  box.id = 'replay-error';
  box.className = 'replay-floating';
  box.textContent = message;
  page.appendChild(box);
  headerMeta.replaceChildren(badge('无法播放', 'is-warn'));
}

// ---------------------------------------------------------------- 列表模式

async function showList(): Promise<void> {
  headerTitle.textContent = '对局回放';
  headerMeta.replaceChildren(badge('载入中…', 'is-dim'));
  canvas.style.opacity = '0.25';
  try {
    const response = await fetch('/api/replays?limit=200');
    if (!response.ok) throw new Error(`读取回放列表失败（HTTP ${response.status}）`);
    const body = (await response.json()) as ReplayListResponse;
    renderList(body);
  } catch (err) {
    showError(err instanceof Error ? err.message : String(err));
  }
}

function renderList(body: ReplayListResponse): void {
  headerMeta.replaceChildren(
    badge(`${body.replays.length} 份`, 'is-dim'),
    badge(body.enabled ? '自动录制：开' : '自动录制：关', body.enabled ? 'is-ok' : 'is-warn'),
    badge(body.directory, 'is-dim'),
  );

  const panel = document.createElement('div');
  panel.id = 'replay-list';
  panel.className = 'replay-floating';
  const head = document.createElement('div');
  head.className = 'replay-panel-head';
  const title = document.createElement('span');
  title.className = 'replay-panel-title';
  title.textContent = '最近的对局（点击播放）';
  head.appendChild(title);
  panel.appendChild(head);
  makeDraggable(panel, { storageKey: 'generals.replay.panel.list' });

  if (body.active.length > 0) {
    const live = document.createElement('p');
    live.className = 'replay-list-live';
    live.textContent = `进行中的 ${body.active.length} 局暂不可读（对局结束才落盘，R8.2）：${body.active.join('、')}`;
    panel.appendChild(live);
  }
  if (body.replays.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'replay-list-live';
    empty.textContent = '还没有回放：开一局打完，这里就会出现。';
    panel.appendChild(empty);
  }

  const table = document.createElement('table');
  table.className = 'replay-list-table';
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>时间</th><th>房间</th><th>玩家</th><th>回合</th><th>结果</th><th>帧 / AI</th><th>大小</th></tr>';
  const tbody = document.createElement('tbody');
  for (const row of body.replays) tbody.appendChild(listRow(row));
  table.append(thead, tbody);
  panel.appendChild(table);
  page.appendChild(panel);

  // 列表模式不需要播放控制条与 AI 面板。
  controls.element.hidden = true;
  aiPanel.element.hidden = true;
  chatList.element.hidden = true;
  leaderboard.element.hidden = true;
  turnEl.hidden = true;
}

function listRow(row: ReplaySummary): HTMLTableRowElement {
  const tr = document.createElement('tr');
  tr.className = 'replay-list-row';
  tr.dataset['id'] = row.id;

  const when = document.createElement('td');
  when.textContent = formatWhen(row.startedAt);
  const room = document.createElement('td');
  room.textContent = row.room;
  const players = document.createElement('td');
  players.className = 'replay-list-players';
  for (const player of row.players) {
    const dot = document.createElement('span');
    dot.className = 'replay-player-dot';
    dot.style.background = PLAYER_COLORS[(player.color as keyof typeof PLAYER_COLORS) ?? 'red'] ?? '#888';
    dot.title = `${player.name}${player.strategy ? ` · ${player.strategy}` : ' · 人工'}`;
    players.appendChild(dot);
  }
  players.appendChild(
    document.createTextNode(row.players.map((p) => p.strategy ?? '人工').join(' vs ')),
  );
  const turns = document.createElement('td');
  turns.textContent = `${row.turns}（${formatClock(row.turns * 2 * (row.tickMs || 500))}）`;
  const result = document.createElement('td');
  result.textContent = row.ended ? (row.winnerName ? `胜者 ${row.winnerName}` : '无人获胜') : `中断（${row.reason}）`;
  if (!row.ended) result.className = 'is-warn';
  const counts = document.createElement('td');
  counts.textContent = `${row.frameCount} / ${row.aiCount}${row.truncated || row.aiTruncated ? ' ⚠' : ''}`;
  const size = document.createElement('td');
  size.textContent = `${(row.bytes / 1024).toFixed(1)}K`;

  tr.append(when, room, players, turns, result, counts, size);
  tr.addEventListener('click', () => {
    location.href = `/replay.html?id=${encodeURIComponent(row.id)}`;
  });
  return tr;
}

// ---------------------------------------------------------------- 调试钩子（e2e 用）

declare global {
  interface Window {
    __replayDebug?: () => {
      id: string;
      tick: number;
      maxTick: number;
      playing: boolean;
      size: number;
      cell: number;
      origin: { x: number; y: number };
      turn: string;
      frames: number;
      ai: number;
      chat: number;
      players: number;
      aiRows: number;
    };
    __replaySeek?: (tick: number) => void;
    __replayPlay?: (playing: boolean) => void;
  }
}

window.__replayDebug = () => ({
  id: file?.id ?? replayId,
  tick: cursor?.tick ?? -1,
  maxTick: cursor?.maxTick ?? 0,
  playing,
  size: state?.size ?? 0,
  cell: cellSize(cam),
  origin: mapOrigin(cam),
  turn: turnEl.textContent ?? '',
  frames: file?.frames.length ?? 0,
  ai: file?.ai.length ?? 0,
  chat: state?.chat.length ?? 0,
  players: state?.players.length ?? 0,
  aiRows: aiPanel.element.querySelectorAll('.replay-ai-entry').length,
});
window.__replaySeek = (tick: number) => seekTo(tick);
window.__replayPlay = (next: boolean) => setPlaying(next);

void boot();