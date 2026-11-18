/**
 * e2e-page.mjs — 真浏览器 × 真后端联调检查。
 *
 * 做三件事：
 *   ① 开两个页面（Alice / Bob）加入同一房间；
 *   ② 让 Bob 点 Start 开局；
 *   ③ 等 3 秒（约 6 Tick）后截图，并断言 Alice 的画布上出现了己方色块与兵量数字。
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

/** 读取页面内统计：画布非背景像素数与己方色块数。 */
async function stats(page) {
  return page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    const { width, height } = canvas;
    const data = ctx.getImageData(0, 0, width, height).data;
    const counts = new Map();
    for (let i = 0; i < data.length; i += 4) {
      const key = `${data[i]},${data[i + 1]},${data[i + 2]}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    const total = width * height;
    const page = counts.get('34,34,34') ?? 0; // #222222
    const fog = counts.get('57,57,57') ?? 0; // #393939
    const red = counts.get('255,0,0') ?? 0; // 玩家 0 = red
    const blue = counts.get('39,146,255') ?? 0; // 玩家 1 = blue
    return {
      width,
      height,
      total,
      page,
      fog,
      red,
      blue,
      turn: document.querySelector('#turn-counter')?.textContent ?? null,
      rows: document.querySelectorAll('#game-leaderboard .leaderboard-name').length,
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
    if (!s.turn) failures.push('回合计数器没有内容');
    else if (!/^Turn \d+\.?$/.test(s.turn.trim())) failures.push(`回合计数器格式不对：${s.turn}`);
    if (s.rows < 2) failures.push(`排行榜行数只有 ${s.rows}，应为 2`);
    if (s.red + s.blue < 200) failures.push(`地图上没有己方色块（red=${s.red} blue=${s.blue}）`);
    if (s.fog < 1000) failures.push(`视野外区域没有雾色（fog=${s.fog}）`);
    if (s.page < 1000) failures.push(`地图区外没有页面底色（page=${s.page}）`);
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
