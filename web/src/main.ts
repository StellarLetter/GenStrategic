/**
 * main.ts — 唯一装配点。
 *
 * 职责：把 core（状态/网络/调兵链）、render（Canvas）、ui（DOM 面板）、input（键鼠）接起来。
 * 不含业务规则；规则全部在服务端内核（AGENT.md §4）。
 *
 * URL 参数：
 *   ?room=<id>      指定房间
 *   ?name=<nick>    昵称
 *   ?spectate=1     观战
 *   ?demo=1         本地演示模式（无服务端，用于视觉比对）
 */

import { GameClient, defaultWsUrl } from './core/client';
import {
  adjustRatio,
  clearChain,
  initialChain,
  popChain,
  pushClickMove,
  pushMove,
  toggleSelection,
  type ChainState,
} from './core/chains';
import { applyFrame, emptyState, type ClientState } from './core/state';
import type { ClientFrame, ServerFrame } from './core/types';
import { bindKeyboard } from './input/keyboard';
import { bindPointer } from './input/pointer';
import { cellSize, centerOnTile, hitTest, makeCamera, panPixels, panTiles, tileRect, zoomStep, type Camera } from './render/camera';
import { paint } from './render/painter';
import { Chat } from './ui/chat';
import { Leaderboard } from './ui/leaderboard';
import { TurnCounter } from './ui/turn';

const params = new URLSearchParams(location.search);
const DEMO = params.get('demo') === '1';

const root = document.getElementById('react-container') ?? document.body;
const page = document.createElement('div');
page.id = 'game-page';
page.className = 'page-enter-done';

const canvasWrap = document.createElement('div');
canvasWrap.className = 'game-canvas-wrap';
const canvas = document.createElement('canvas');
canvas.className = 'game-map-canvas';
canvasWrap.appendChild(canvas);

const sidePanel = document.createElement('div');
sidePanel.id = 'side-panel';

const canvasHost = document.createElement('div');
canvasHost.className = 'canvas-host';
canvasHost.appendChild(canvasWrap);

const panelHost = document.createElement('div');
panelHost.className = 'panel-host';

page.append(canvasHost, panelHost);
root.appendChild(page);

const startButton = document.createElement('button');
startButton.id = 'start-button';
startButton.textContent = 'Start';
startButton.title = '房主开房（P-01：目前为手动开局）';
page.appendChild(startButton);

const surrenderButton = document.createElement('button');
surrenderButton.id = 'surrender-button';
surrenderButton.textContent = 'Surrender';
page.appendChild(surrenderButton);
void sidePanel;

const turnCounter = new TurnCounter(page);
const leaderboard = new Leaderboard(panelHost);
const chat = new Chat(panelHost);

// ------------------------------------------------------------ 状态

let state: ClientState = emptyState(20);
let chain: ChainState = initialChain();
let cam: Camera = makeCamera(state.size, 800, 800, 1);
let clickMove = true;
/** 开局后待执行「镜头对准自己的首都」。 */
let pendingRecenter = false;

const client = new GameClient(defaultWsUrl());

function send(frame: ClientFrame): void {
  if (DEMO) return;
  client.send(frame);
}

// ------------------------------------------------------------ 渲染

const ctx = canvas.getContext('2d');
if (!ctx) throw new Error('canvas 2d context unavailable');

function resize(): void {
  const rect = canvasWrap.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(64, Math.floor(rect.width));
  const h = Math.max(64, Math.floor(rect.height));
  canvas.width = Math.floor(w * dpr);
  canvas.height = Math.floor(h * dpr);
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;
  cam = { ...cam, width: w, height: h, dpr };
  draw();
}

function draw(): void {
  paint(ctx!, cam, state, chain, { showChain: true, highlightTargets: clickMove });
  turnCounter.render(state);
  leaderboard.render(state);
  chat.render(state);
  syncButtons();
}

/** 按对局阶段切换按钮可见性（开局前显示 Start，开局后显示 Surrender）。 */
function syncButtons(): void {
  startButton.hidden = state.started;
  surrenderButton.hidden = !state.started || state.me === null;
}

// ------------------------------------------------------------ 输入

function playerHomeTile(): number | null {
  if (state.me === null) return null;
  for (const [i, t] of state.tiles.entries()) if (t.owner === state.me) return i;
  return null;
}

function centerOnMe(): boolean {
  const home = playerHomeTile();
  if (home === null) return false;
  cam = centerOnTile(cam, home);
  return true;
}

function submitMove(dir: 'up' | 'down' | 'left' | 'right'): void {
  const blocked = (i: number): boolean => state.tiles[i]?.terrain === 2 && state.tiles[i]!.seen;
  const res = pushMove(chain, dir, cam.size, blocked);
  if (res.error || !res.cmd) return;
  chain = res.chain;
  send({ t: 'commands', cmds: [{ op: 'move', from: res.cmd.from, dir: res.cmd.dir, ratio: res.cmd.ratio }] });
  draw();
}

function onClickTile(index: number): void {
  const selected = chain.selected;
  if (clickMove && selected !== null && Math.abs((index % cam.size) - (selected % cam.size)) + Math.abs(Math.floor(index / cam.size) - Math.floor(selected / cam.size)) === 1) {
    const res = pushClickMove(chain, index, cam.size);
    if (!res.error && res.cmd) {
      chain = res.chain;
      send({ t: 'commands', cmds: [{ op: 'move', from: res.cmd.from, dir: res.cmd.dir, ratio: res.cmd.ratio }] });
      draw();
      return;
    }
  }
  chain = toggleSelection(chain, index);
  send({ t: 'commands', cmds: [{ op: 'select', tile: index }] });
  draw();
}

bindPointer(canvas, {
  hitTest: (x, y) => hitTest(cam, x, y),
  onClickTile,
  onPanPixels: (dx, dy) => {
    cam = panPixels(cam, dx, dy);
    draw();
  },
  onZoom: (dir) => {
    cam = zoomStep(cam, dir);
    draw();
  },
});

bindKeyboard({
  onMove: submitMove,
  onClear: () => {
    chain = clearChain(chain);
    send({ t: 'commands', cmds: [{ op: 'clear' }] });
    draw();
  },
  onPop: () => {
    chain = popChain(chain);
    send({ t: 'commands', cmds: [{ op: 'pop' }] });
    draw();
  },
  onRatio: (delta) => {
    chain = adjustRatio(chain, delta);
    draw();
  },
  onPan: (dx, dy) => {
    cam = panTiles(cam, -dx, -dy);
    draw();
  },
  onChat: () => chat.focus(),
  isTyping: () => chat.focused,
});

chat.onSend = (text) => send({ t: 'chat', text });
startButton.addEventListener('click', () => {
  if (DEMO) return;
  send({ t: 'start' });
  startButton.disabled = true;
});
surrenderButton.addEventListener('click', () => {
  if (DEMO) return;
  if (window.confirm('确定要投降吗？（将进入 40 Tick 过渡期）')) send({ t: 'surrender' });
});

window.addEventListener('resize', resize);

// ------------------------------------------------------------ 网络

client.onFrame = (frame: ServerFrame) => {
  const isStart = frame.t === 'game_start';
  state = applyFrame(state, frame);
  if (isStart) {
    cam = makeCamera(state.size, cam.width, cam.height, cam.dpr);
  }
  if (frame.t === 'tiles' && pendingRecenter && state.me !== null) {
    // 开局后第一次收到视野增量时，把镜头对到自己的首都（R6.1）。
    if (centerOnMe()) pendingRecenter = false;
  }
  if (state.chain.length !== chain.chain.length) {
    chain = { ...chain, chain: state.chain };
  }
  if (isStart && state.me !== null) pendingRecenter = true;
  draw();
};

client.onStatus = (status) => {
  page.dataset.status = status;
};

function boot(): void {
  resize();
  if (DEMO) {
    loadDemo();
    return;
  }
  const name = params.get('name') ?? `Player${Math.floor(Math.random() * 900 + 100)}`;
  const room = params.get('room') ?? undefined;
  const spectate = params.get('spectate') === '1';

  // 只在「首次连上」时 join 一次。GameClient 重连后也会触发 'open'，
  // 那时服务端仍保留着我们的座位，重复 join 会多占一个座位（PROTOCOL §2.1）。
  let joined = false;
  client.onStatus = (status) => {
    page.dataset.status = status;
    if (status === 'closed') {
      joined = false;
      return;
    }
    if (status !== 'open' || joined) return;
    joined = true;
    send(spectate ? { t: 'spectate', room: room ?? '' } : { t: 'join', name, room, spectate });
  };
  client.connect();
}

// ------------------------------------------------------------ 演示数据
// ?demo=1 时生成一份静态棋盘，用于无服务端的视觉比对（不参与任何规则判定）。

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface DemoTile {
  terrain: 0 | 1 | 2 | 3 | 4;
  army: number | null;
  owner: number | null;
  seen: boolean;
}

function buildDemo(size: number, seed: number): DemoTile[] {
  const rnd = mulberry32(seed);
  const tiles: DemoTile[] = Array.from({ length: size * size }, () => ({
    terrain: 0 as const,
    army: 0,
    owner: null,
    seen: false,
  }));

  const capA = 3 * size + 3;
  const capB = (size - 4) * size + (size - 4);

  // 山地（约 18%），但首都与首都周围 1 格保持平原
  const protectedCells = new Set<number>([capA, capB]);
  for (const cap of [capA, capB]) {
    const r0 = Math.floor(cap / size);
    const c0 = cap % size;
    for (let dr = -1; dr <= 1; dr += 1) {
      for (let dc = -1; dc <= 1; dc += 1) {
        const r = r0 + dr;
        const c = c0 + dc;
        if (r >= 0 && r < size && c >= 0 && c < size) protectedCells.add(r * size + c);
      }
    }
  }
  for (let i = 0; i < size * size; i += 1) {
    if (!protectedCells.has(i) && rnd() < 0.18) tiles[i]!.terrain = 2;
  }
  // 城市（约 5%），中立 40..50 兵（R3.3）
  for (let i = 0; i < size * size; i += 1) {
    if (tiles[i]!.terrain !== 0 || protectedCells.has(i)) continue;
    if (rnd() < 0.05) {
      tiles[i]!.terrain = 3;
      tiles[i]!.army = 40 + Math.floor(rnd() * 11);
    }
  }

  // 两块玩家领土（BFS 扩张）
  const blob = (start: number, ownerId: number, radius: number): void => {
    const queue: Array<[number, number]> = [[start, 0]];
    const seenCells = new Set<number>();
    while (queue.length) {
      const [idx, d] = queue.shift()!;
      if (idx < 0 || idx >= size * size) continue;
      if (seenCells.has(idx)) continue;
      seenCells.add(idx);
      const r = Math.floor(idx / size);
      const c = idx % size;
      const t = tiles[idx]!;
      if (t.terrain === 2) continue;
      t.owner = ownerId;
      t.seen = true;
      t.army = t.terrain === 3 ? t.army : 1 + Math.floor(rnd() * 9);
      if (d >= radius || rnd() < 0.3) continue;
      queue.push([r > 0 ? idx - size : -1, d + 1]);
      queue.push([r < size - 1 ? idx + size : -1, d + 1]);
      queue.push([c > 0 ? idx - 1 : -1, d + 1]);
      queue.push([c < size - 1 ? idx + 1 : -1, d + 1]);
    }
  };
  blob(capA, 0, 6);
  blob(capB, 1, 6);

  // 首都（R3.2：初始 1 兵；这里演示时给个较大的数字）
  tiles[capA] = { terrain: 1, army: 29, owner: 0, seen: true };
  tiles[capB] = { terrain: 1, army: 12, owner: 1, seen: true };

  // 视野：己方地块 ∪ 8 相邻（R3.5）
  const visible = new Set<number>();
  for (let r = 0; r < size; r += 1) {
    for (let c = 0; c < size; c += 1) {
      if (tiles[r * size + c]!.owner !== 0) continue;
      for (let dr = -1; dr <= 1; dr += 1) {
        for (let dc = -1; dc <= 1; dc += 1) {
          const rr = r + dr;
          const cc = c + dc;
          if (rr >= 0 && rr < size && cc >= 0 && cc < size) visible.add(rr * size + cc);
        }
      }
    }
  }
  for (const idx of visible) tiles[idx]!.seen = true;
  // 视野外：兵量与归属不可见，地形折叠为山地/平原（R3.6 / C-17）
  for (let i = 0; i < tiles.length; i += 1) {
    const t = tiles[i]!;
    if (visible.has(i)) continue;
    t.seen = false;
    t.army = null;
    t.owner = null;
    t.terrain = t.terrain === 2 || t.terrain === 3 ? 2 : 0;
  }
  return tiles;
}

function loadDemo(): void {
  const size = 20;
  const demoTiles = buildDemo(size, 987654321);
  state = {
    ...emptyState(size),
    started: true,
    me: 0,
    players: [
      { playerId: 0, name: 'You', color: 'red', seat: 0 },
      { playerId: 1, name: 'Timelink', color: 'pink', seat: 1 },
      { playerId: 2, name: 'kezr', color: 'blue', seat: 2 },
      { playerId: 3, name: 'MrMinister', color: 'purple', seat: 3 },
      { playerId: 4, name: 'rtsharthr', color: 'orange', seat: 4 },
      { playerId: 5, name: 'Anonychan', color: 'teal', seat: 5 },
      { playerId: 6, name: 'aaa!', color: 'green', seat: 6 },
      { playerId: 7, name: 'WeYoung', color: 'maroon', seat: 7 },
    ],
    turn: 102,
    half: 1,
    leaderboard: [
      { playerId: 0, name: 'You', color: 'red', army: 688, land: 110, alive: true, surrenderIn: null, star: 47 },
      { playerId: 1, name: 'Timelink', color: 'pink', army: 379, land: 141, alive: true, surrenderIn: null, star: 37 },
      { playerId: 5, name: 'Anonychan', color: 'teal', army: 0, land: 0, alive: false, surrenderIn: null, star: 50 },
      { playerId: 6, name: 'aaa!', color: 'green', army: 0, land: 0, alive: false, surrenderIn: null, star: 44 },
      { playerId: 2, name: 'kezr', color: 'blue', army: 0, land: 0, alive: false, surrenderIn: null, star: 30 },
      { playerId: 3, name: 'MrMinister', color: 'purple', army: 0, land: 0, alive: false, surrenderIn: null, star: 28 },
      { playerId: 4, name: 'rtsharthr', color: 'orange', army: 0, land: 0, alive: false, surrenderIn: null, star: 0 },
      { playerId: 7, name: 'WeYoung', color: 'maroon', army: 0, land: 0, alive: false, surrenderIn: null, star: 33 },
    ],
    chat: [
      { turn: -1, name: '', color: 'red', text: 'Chat is being recorded.', system: true },
      { turn: 37, name: '', color: 'red', text: 'Timelink captured aaa!.', system: true },
      { turn: 39, name: '', color: 'green', text: 'aaa! left.', system: true },
    ],
    tiles: demoTiles.map((t) => ({ ...t })),
  };
  cam = makeCamera(size, cam.width, cam.height, cam.dpr);
  const capA = 3 * size + 3;
  chain = { ...chain, selected: capA, ratio: 1, chain: [{ from: capA, dir: 'right', ratio: 1 }] };
  draw();
}

boot();

/** 供调试与截图对比使用。 */
export function debugSnapshot(): {
  state: ClientState;
  cam: Camera;
  rect: (i: number) => ReturnType<typeof tileRect>;
  cell: number;
} {
  return { state, cam, rect: (i: number) => tileRect(cam, i), cell: cellSize(cam) };
}
