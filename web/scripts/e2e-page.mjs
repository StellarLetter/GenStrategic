/**
 * e2e-page.mjs — 真浏览器 × 真后端联调检查。
 *
 * 做三件事：
 *   ① 开两个页面（Alice / Bob）加入同一房间；
 *   ② 让 Bob 点 Start 开局；
 *   ③ 等 3 秒（约 6 Tick）后截图，并断言画布尺寸、格边长（随 R2.3 的人数定尺寸而变）、
 *      迷雾底色、己方色块、排行榜、回合文本、聊天栏首行提示。
 *
 * 阈值说明：视口 1600×900、右侧面板 400 px ⇒ 画布 1200×900；
 *   2 人局 size ∈ [12,18]（R2.3）⇒ 格边长 = 900/size ∈ [50,75]。
 *
 * 用法（必须先起后端：python -m generals --port 8200）：
 *   cd web && node scripts/e2e-page.mjs http://127.0.0.1:8200 ../.dispatch/live.png
 *
 * 退出码：0 = 通过；1 = 有断言失败（错误会打印）。
 */

import { chromium } from 'playwright-core';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const base = process.argv[2] ?? 'http://127.0.0.1:8200';
const outPng = resolve(process.argv[3] ?? '../.dispatch/live.png');
const room = `live-${Date.now().toString(36)}`;
const waitMs = Number(process.argv[4] ?? 3500);

const errors = [];
const failures = [];

function watch(page, tag) {
  page.on('pageerror', (e) => errors.push(`[${tag}] ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !m.text().includes('404')) errors.push(`[${tag}] ${m.text()}`);
  });
}

/** 读取页面内统计：画布尺寸、迷雾覆盖面、己方色块数、网格线间距、排行榜与回合文本。 */
async function stats(page) {
  return page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    const { width, height } = canvas;
    const data = ctx.getImageData(0, 0, width, height).data;
    const counts = new Map();
    const at = (x, y) => {
      const i = (y * width + x) * 4;
      return [data[i], data[i + 1], data[i + 2]];
    };
    for (let i = 0; i < data.length; i += 4) {
      const key = `${data[i]},${data[i + 1]},${data[i + 2]}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const fog = counts.get('57,57,57') ?? 0; // #393939 视野外/地图底色
    const red = counts.get('255,0,0') ?? 0; // 玩家 0 = red
    const blue = counts.get('39,146,255') ?? 0; // 玩家 1 = blue

    // 己方地块所在行：找出该行上的网格线中心，相邻中心间距即格边长（按 R2.3 由人数定 size）。
    let row = -1;
    for (let y = height - 8; y >= 0 && row < 0; y -= 1) {
      for (let x = 0; x < width; x += 1) {
        const [r, g, b] = at(x, y);
        if (r === 255 && g === 0 && b === 0) {
          row = y;
          break;
        }
      }
    }
    const gaps = [];
    if (row >= 0) {
      let run = 0;
      for (let x = 0; x < width; x += 1) {
        const [r, g, b] = at(x, row);
        const dark = (r + g + b) / 3 < 40; // 网格线是纯黑（#000000）
        if (dark) run += 1;
        else if (run > 0) {
          gaps.push(x - run / 2);
          run = 0;
        }
      }
    }
    const deltas = [];
    for (let i = 1; i < gaps.length; i += 1) deltas.push(gaps[i] - gaps[i - 1]);
    deltas.sort((a, b) => a - b);

    return {
      width,
      height,
      total: width * height,
      fog,
      red,
      blue,
      cell: deltas.length ? Math.round(deltas[Math.floor(deltas.length / 2)]) : 0,
      gridLines: gaps.length,
      turn: document.querySelector('#turn-counter')?.textContent ?? null,
      rows: document.querySelectorAll('#game-leaderboard .leaderboard-name').length,
      chatHint: document.querySelector('#in-game-chat .chat-messages-container p')?.textContent ?? null,
    };
  });
}

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: { width: 1600, height: 900 } });

try {
  const alice = await context.newPage();
  watch(alice, 'alice');
  await alice.goto(`${base}/?name=Alice&room=${room}`, { waitUntil: 'networkidle' });
  await alice.waitForTimeout(600);

  const bob = await context.newPage();
  watch(bob, 'bob');
  await bob.goto(`${base}/?name=Bob&room=${room}`, { waitUntil: 'networkidle' });
  await bob.waitForTimeout(600);

  // Bob 点 Start
  const startBtn = bob.locator('#start-button');
  if (!(await startBtn.isVisible())) failures.push('Bob 页面上看不到 Start 按钮');
  else await startBtn.click();

  await alice.waitForTimeout(waitMs);

  const s = await stats(alice);
  if (!s) failures.push('Alice 页面没有 canvas');
  else {
    console.log('stats:', JSON.stringify(s));
    // 画布尺寸：1600×900 视口下地图区 1200×900（右侧 400 px 面板）。
    if (s.width !== 1200 || s.height !== 900) failures.push(`画布尺寸不是 1200×900：${s.width}×${s.height}`);
    if (!s.turn) failures.push('回合计数器没有内容');
    else if (!/^Turn \d+\.?$/.test(s.turn.trim())) failures.push(`回合计数器格式不对：${s.turn}`);
    if (s.rows < 2) failures.push(`排行榜行数只有 ${s.rows}，应为 2`);
    if (s.red + s.blue < 200) failures.push(`地图上没有己方色块（red=${s.red} blue=${s.blue}）`);
    // 地图底色 = 迷雾色（R6.9）：视野外是一整片连续底色，不再露出页面底色。
    if (s.fog < 100000) failures.push(`迷雾覆盖面太小（fog=${s.fog}）`);
    // 格边长：按 R2.3，2 人局 size ∈ [12,18] ⇒ cell = 900/size ∈ [50,75]。
    if (s.gridLines < 2) failures.push(`视野内没找到网格线（gridLines=${s.gridLines}）`);
    else if (s.cell < 48 || s.cell > 78) failures.push(`格边长 ${s.cell} px 超出 2 人局的取值范围 [50,75]（R2.3）`);
    if (!s.chatHint || !s.chatHint.includes('Chat is being recorded.')) {
      failures.push(`聊天栏首行提示缺失：${JSON.stringify(s.chatHint)}`);
    }
  }

  await alice.screenshot({ path: outPng });
  console.log(`screenshot -> ${outPng}`);
  void pathToFileURL;
} finally {
  await browser.close();
}

if (errors.length) {
  console.error('PAGE ERRORS:');
  for (const e of errors) console.error('  ' + e);
}
if (failures.length) {
  console.error('FAILURES:');
  for (const f of failures) console.error('  - ' + f);
  process.exit(1);
}
if (errors.length) process.exit(1);
console.log('=== PAGE E2E OK ===');
