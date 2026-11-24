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
 *   ?demoRatio=0.5  （仅 demo）演示用调兵比例，用于截「选中格比例文字」（R6.6）
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
import { step } from './core/geometry';
import type { ClientFrame, ServerFrame } from './core/types';
import { bindKeyboard } from './input/keyboard';
import { bindPointer } from './input/pointer';
import { cellSize, hitTest, makeCamera, mapOrigin, panPixels, panTiles, tileRect, zoomAt, type Camera } from './render/camera';
import { paint } from './render/painter';
import { AutopilotPanel } from './ui/autopilot';
import { Chat } from './ui/chat';
import { makeDraggable } from './ui/floating';
import { Leaderboard } from './ui/leaderboard';
import { TurnCounter } from './ui/turn';

const params = new URLSearchParams(location.search);
const DEMO = params.get('demo') === '1';

/** 把 URL 里的 demoRatio 解析为 0.1..1 的比例；缺省 1（不显示比例文字）。 */
function clampRatio(raw: string | null): number {
  if (raw === null || raw === '') return 1;
  const v = Number(raw);
  if (!Number.isFinite(v)) return 1;
  return Math.min(1, Math.max(0.1, Math.round(v * 100) / 100));
}

const DEMO_RATIO = clampRatio(params.get('demoRatio'));

const root = document.getElementById('react-container') ?? document.body;
const page = document.createElement('div');
page.id = 'game-page';
page.className = 'page-enter-done';

// 全屏地图：canvas 铺满整个视口，面板全部改为压在上面的浮动层（见 styles/game.css）。
const canvas = document.createElement('canvas');
canvas.className = 'game-map-canvas';
page.appendChild(canvas);
root.appendChild(page);

const startButton = document.createElement('button');
startButton.id = 'start-button';
startButton.textContent = 'Start';
startButton.title = '房主开房（R1.4：目前为手动开局）';
page.appendChild(startButton);

const surrenderButton = document.createElement('button');
surrenderButton.id = 'surrender-button';
surrenderButton.textContent = 'Surrender';
page.appendChild(surrenderButton);

// 全屏按钮：白底青字，默认在顶部居中 Surrender 右侧；和别的面板一样可拖、位置记忆。
const fullscreenButton = document.createElement('button');
fullscreenButton.id = 'fullscreen-button';
page.appendChild(fullscreenButton);

/** 浏览器是否提供 Fullscreen API（不支持时按钮 disabled，并用 title 说明）。 */
const FULLSCREEN_SUPPORTED = typeof document.documentElement.requestFullscreen === 'function';
if (!FULLSCREEN_SUPPORTED) {
  fullscreenButton.disabled = true;
  fullscreenButton.title = '当前浏览器不支持全屏 API（Fullscreen API）';
}

/** 文案随全屏状态切换（`fullscreenchange` 也会回来同步一次）。 */
function syncFullscreenLabel(): void {
  fullscreenButton.textContent = document.fullscreenElement ? '退出全屏' : '全屏';
}
syncFullscreenLabel();

async function toggleFullscreen(): Promise<void> {
  if (!FULLSCREEN_SUPPORTED) return;
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch {
    // 用户拒绝 / 浏览器策略拒绝：保持原状，文案由 fullscreenchange 兜底。
  }
  syncFullscreenLabel();
  resize();
}

const turnCounter = new TurnCounter(page);
const leaderboard = new Leaderboard(page);
const chat = new Chat(page);
// 策略接管：下拉框内容来自 `GET /api/ai/strategies`（服务端自动挂载 ai/ 目录）。
const autopilot = new AutopilotPanel(page);

// 五个浮动面板：可拖动，位置存 localStorage['generals.panel.<id>']（见 ui/floating.ts）。
// 面板是独立 DOM 层，panel 上的 pointer 事件不会落到 canvas 上，点击面板不会选中地块。
// Surrender / 全屏整块可拖：拖动阈值内的 pointerup 仍按点击处理，点它们照常生效。
makeDraggable(turnCounter.element);
makeDraggable(surrenderButton);
makeDraggable(fullscreenButton);
makeDraggable(leaderboard.element);
makeDraggable(chat.element, { handle: chat.dragHandle });
makeDraggable(autopilot.element);

// ------------------------------------------------------------ 状态

let state: ClientState = emptyState(20);
let chain: ChainState = initialChain();
let cam: Camera = makeCamera(state.size, 800, 800, 1);
let clickMove = true;

const client = new GameClient(defaultWsUrl());

function send(frame: ClientFrame): void {
  if (DEMO) return;
  client.send(frame);
}

// ------------------------------------------------------------ 渲染

const ctx = canvas.getContext('2d');
if (!ctx) throw new Error('canvas 2d context unavailable');

function resize(): void {
  // 地图铺满整个视口（不再是「右侧 25% 面板」的布局），cellSize 的公式不变（R2.3）。
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
  paint(ctx!, cam, state, chain, { showChain: true, highlightTargets: clickMove });
  turnCounter.render(state);
  leaderboard.render(state);
  chat.render(state);
  autopilot.render(state);
  syncButtons();
}

/** 按对局阶段切换按钮可见性（开局前显示 Start，开局后显示 Surrender）。 */
function syncButtons(): void {
  startButton.hidden = state.started;
  surrenderButton.hidden = !state.started || state.me === null;
}

/**
 * 权威链刷新后，修正「选中地块」——它必须仍是「自己控制的地块」或「链中某条请求的目标」（R4.11）。
 * 否则从它继续调兵只会立刻再被作废一次，这里直接取消选中。
 */
function pruneSelection(): void {
  const selected = chain.selected;
  if (selected === null || state.me === null) return;
  const owned = state.tiles[selected]?.owner === state.me;
  const isChainTarget = chain.chain.some(
    (req) => step(req.from, req.dir, cam.size) === selected,
  );
  if (!owned && !isChainTarget) chain = { ...chain, selected: null };
}

// ------------------------------------------------------------ 输入

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
  onZoom: (dir, x, y) => {
    // 以光标下的那个地图点为锚点缩放：缩放前后该点保持不动（camera.zoomAt）。
    cam = zoomAt(cam, dir, x, y);
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
// 策略接管：选「我自己操作」发 `strategy: null` 收回操作权（PROTOCOL §2.8）。
// 开局前后都可以切换：服务端以 `autopilot` 回执为准，前端不本地假设。
autopilot.onSelect = (strategy) => send({ t: 'autopilot', strategy });
startButton.addEventListener('click', () => {
  if (DEMO) return;
  send({ t: 'start' });
  startButton.disabled = true;
});
surrenderButton.addEventListener('click', () => {
  if (DEMO) return;
  if (window.confirm('确定要投降吗？（将进入 40 Tick 过渡期）')) send({ t: 'surrender' });
});
fullscreenButton.addEventListener('click', () => {
  void toggleFullscreen();
});
// 进入/退出全屏都会改变视口尺寸：同步文案并重算画布（canvas 铺满视口，R6.14）。
document.addEventListener('fullscreenchange', () => {
  syncFullscreenLabel();
  resize();
});

window.addEventListener('resize', resize);

// ------------------------------------------------------------ 网络

client.onFrame = (frame: ServerFrame) => {
  const isStart = frame.t === 'game_start';
  state = applyFrame(state, frame);
  if (isStart) {
    // 开局把镜头交给 makeCamera：整图正好铺满视口短边并居中（不再「对准自己的首都」——
    // 平移已不钳制（R6.15），对准首都反而会把地图推到视口外、露一片非地图区）。
    cam = makeCamera(state.size, cam.width, cam.height, cam.dpr);
  }
  if (frame.t === 'commands') {
    // 服务端 `commands` 是权威快照（R6.3）。作废后链会变短，必须整份覆盖，
    // 只比长度会漏掉「同长度但内容变了」的情况。作废是静默的：只清链与选中，不弹提示。
    chain = { ...chain, chain: state.chain };
    pruneSelection();
  }
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
  // 视野外：兵量与归属不可见，地形折叠为山地/平原（R3.6 / R6.9）
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
      { turn: 30, name: 'Timelink', color: 'pink', text: 'Timelink captured aaa!.', system: true, kind: 'captured', target: 'aaa!', targetColor: 'green' },
      { turn: 32, name: 'aaa!', color: 'green', text: 'aaa! left.', system: true, kind: 'left' },
      { turn: 44, name: 'kezr', color: 'blue', text: 'kezr surrendered.', system: true, kind: 'surrendered' },
      { turn: 46, name: 'You', color: 'red', text: 'You captured kezr.', system: true, kind: 'captured', target: 'kezr', targetColor: 'blue' },
    ],
    tiles: demoTiles.map((t) => ({ ...t })),
  };
  cam = makeCamera(size, cam.width, cam.height, cam.dpr);
  const capA = 3 * size + 3;
  // ?demoRatio=<0.1..1> 时把比例设成非 100%，用来截「选中格比例文字」（R6.6）。
  chain = {
    ...chain,
    selected: capA,
    ratio: DEMO_RATIO,
    chain: [{ from: capA, dir: 'right', ratio: DEMO_RATIO }],
  };
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

/**
 * 调试钩子：把几何事实挂到 `window.__generalsDebug`，供 `scripts/e2e-page.mjs`
 * 与截图脚本读取（纯读取，不改变任何游戏状态）。
 *
 * 有了它，自动化就不必从像素反推格边长 —— 视野可能只有两三格，按「网格线间距」
 * 反推会得出荒谬的数字（例如把两块相距很远的可见区当成相邻格子）。
 *
 * 字段：`size / cell / zoom / width / height`（几何）、`origin`（地图左上角的屏幕坐标，
 * 平移无钳制后可能为负）、`ratio / selected / chain`（调兵链），以及
 * `rect(i)`（某格矩形）与 `tileAt(x, y)`（屏幕坐标 → 地块索引），
 * 后者用于验证「滚轮缩放以光标为锚点」。
 */
declare global {
  interface Window {
    __generalsDebug?: () => {
      size: number;
      cell: number;
      zoom: number;
      width: number;
      height: number;
      /** 地图左上角的屏幕坐标（无钳制平移后可能为负、跑出视口）。 */
      origin: { x: number; y: number };
      ratio: number;
      selected: number | null;
      chain: number;
      rect: (i: number) => { x: number; y: number; w: number; fill: number; gap: number };
      /** 屏幕坐标（相对画布左上角的 CSS 像素）→ 地块索引；越界 null。 */
      tileAt: (x: number, y: number) => number | null;
    };
  }
}

window.__generalsDebug = () => ({
  size: state.size,
  cell: cellSize(cam),
  zoom: cam.zoom,
  width: cam.width,
  height: cam.height,
  origin: mapOrigin(cam),
  ratio: chain.ratio,
  selected: chain.selected,
  chain: chain.chain.length,
  rect: (i: number) => tileRect(cam, i),
  tileAt: (x: number, y: number) => hitTest(cam, x, y),
});
