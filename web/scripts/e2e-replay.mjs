/**
 * e2e-replay.mjs — 回放页的真浏览器端到端（docs/REPLAY.md §5.1）。
 *
 * 前置：服务端已在 <baseUrl> 运行，且回放目录里**至少有一份已落盘的回放**
 *       （仓库根执行 `tools/replay.py demo --dir <同一个目录>` 可造一份样本）。
 * 用法：
 *   node scripts/e2e-replay.mjs http://127.0.0.1:8124 [replayId] [out.png]
 *
 * 断言（失败即退出码 1）：
 *   1. 列表页渲染出行、每行有回合/结果/帧数；
 *   2. 播放页按 `?id=` 载入：地图铺满视口短边（cell 与 R2.3 的 900/size 一致）、
 *      时间轴上限 = 回放最大 Tick；
 *   3. `__replaySeek(t)` 跳到任意 Tick 后，回合文本与 Tick/2 吻合；
 *   4. AI 调试流有行、可按玩家筛选、点条目会跳到该条目的 Tick；
 *   5. 播放推进：8× 倍速下 1.2 秒应前进约 19 Tick（允许区间）；
 *   6. 页面无 JS 异常（favicon 的 404 属既有约定，忽略）。
 */

import { chromium } from 'playwright-core';

const base = (process.argv[2] ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
const wantedId = process.argv[3] ?? '';
const outPng = process.argv[4] ?? '';

const failures = [];
const errors = [];
const check = (ok, message) => {
  if (!ok) failures.push(message);
  return ok;
};

const browser = await chromium.launch({ channel: 'msedge', args: ['--force-device-scale-factor=1'] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    // 既有约定：favicon.ico 没有路由，404 被各处 e2e 统一忽略（见 e2e-page.mjs）。
    if (m.type() === 'error' && !m.text().includes('404')) errors.push(`console: ${m.text()}`);
  });

  // ---------------------------------------------------------------- 列表页
  await page.goto(`${base}/replay.html`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#replay-list', { timeout: 15000 });
  const rows = await page.locator('#replay-list .replay-list-row').count();
  check(rows > 0, `列表页应该有回放行，实际 ${rows}`);
  const rowText = rows > 0 ? await page.locator('#replay-list .replay-list-row').first().innerText() : '';
  check(/胜者|中断/.test(rowText), `列表行应显示结果，实际 "${rowText}"`);

  const firstId = rows > 0
    ? (await page.locator('#replay-list .replay-list-row').first().getAttribute('data-id')) ?? ''
    : '';
  const replayId = wantedId || firstId || (await page.evaluate(async () => {
    const body = await (await fetch('/api/replays?limit=1')).json();
    return body.replays?.[0]?.id ?? '';
  }));
  check(!!replayId, '找不到任何可播放的回放 id');

  // ---------------------------------------------------------------- 播放页
  await page.goto(`${base}/replay.html?id=${encodeURIComponent(replayId)}&autoplay=0`, {
    waitUntil: 'networkidle',
  });
  await page.waitForFunction(() => window.__replayDebug && window.__replayDebug().maxTick > 0, null, {
    timeout: 15000,
  });

  const geo = await page.evaluate(() => window.__replayDebug());
  // 视口 1600×900 ⇒ 整图铺满短边（900），格边长 = 900/size（R2.3 的渲染约定）。
  const expectedCell = 900 / geo.size;
  check(Math.abs(geo.cell - expectedCell) < 1.5, `格边长 ${geo.cell} 应≈${expectedCell.toFixed(2)}`);
  check(geo.maxTick > 0, `时间轴上界应为正数，实际 ${geo.maxTick}`);
  check(geo.players >= 1, `应至少有一名玩家，实际 ${geo.players}`);
  check(geo.aiRows > 0 || geo.ai === 0, `AI 调试流应渲染条目，实际 ${geo.aiRows}/${geo.ai}`);

  // 跳到中段：回合文本必须等于 tick/2（R1.2）。
  const mid = Math.floor(geo.maxTick / 2);
  await page.evaluate((t) => window.__replaySeek(t), mid);
  await page.waitForTimeout(120);
  const atMid = await page.evaluate(() => window.__replayDebug());
  check(atMid.tick === mid, `seek(${mid}) 后 tick=${atMid.tick}`);
  check(/^Turn \d+\.?$/.test(atMid.turn), `回合文本格式异常："${atMid.turn}"`);
  const expectTurn = Math.floor(mid / 2);
  check(atMid.turn.startsWith(`Turn ${expectTurn}`), `tick ${mid} 应显示 Turn ${expectTurn}，实际 ${atMid.turn}`);
  check(atMid.origin.y <= 1 && Math.abs(atMid.origin.x - (1600 - 900) / 2) <= 8,
    `整图应铺满视口短边并居中，origin=${JSON.stringify(atMid.origin)}`);

  // 按玩家筛选 + 点条目跳转。
  const optionCount = await page.locator('#replay-ai-panel select option').count();
  check(optionCount >= 2, `玩家筛选应有「全部 + 每名玩家」，实际 ${optionCount} 项`);
  if (optionCount >= 2) {
    const before = await page.locator('.replay-ai-entry').count();
    await page.selectOption('#replay-ai-panel select', { index: 1 });
    await page.waitForTimeout(150);
    const after = await page.locator('.replay-ai-entry').count();
    check(after <= before, `按玩家筛选后条目数应变少（${before} → ${after}）`);
    await page.selectOption('#replay-ai-panel select', { index: 0 });
    await page.waitForTimeout(150);
  }
  const firstEntry = page.locator('.replay-ai-entry').first();
  if (await firstEntry.count()) {
    const entryTick = Number(await firstEntry.getAttribute('data-tick'));
    await page.evaluate(() => window.__replayPlay(false));
    await firstEntry.click();
    await page.waitForTimeout(200);
    const picked = await page.evaluate(() => window.__replayDebug());
    check(Number.isFinite(entryTick) && picked.tick >= entryTick,
      `点条目(t${entryTick}) 应跳到该刻，实际 tick=${picked.tick}`);
    await page.evaluate(() => window.__replayPlay(false));
  }

  // 播放推进（8×：1.2s ≈ 19 Tick）。
  await page.evaluate(() => window.__replaySeek(0));
  await page.selectOption('#replay-controls select', '8');
  await page.evaluate(() => window.__replayPlay(true));
  await page.waitForTimeout(1200);
  const played = await page.evaluate(() => window.__replayDebug());
  check(played.tick >= 8 && played.tick <= 40, `8× 播放 1.2s 应前进约 19 Tick，实际 ${played.tick}`);
  await page.evaluate(() => window.__replayPlay(false));

  // 键盘：空格暂停/继续、方向键单步。
  await page.evaluate(() => window.__replaySeek(50));
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(120);
  const stepped = await page.evaluate(() => window.__replayDebug());
  check(stepped.tick === 51, `右方向键应前进 1 Tick，实际 ${stepped.tick}`);

  if (outPng) await page.screenshot({ path: outPng });
  console.log('geo:', JSON.stringify({ size: geo.size, cell: geo.cell, maxTick: geo.maxTick, aiRows: geo.aiRows }));
  console.log('played:', JSON.stringify({ tick: played.tick }));
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
}
if (errors.length || failures.length) process.exit(1);
console.log('=== REPLAY PAGE E2E OK ===');