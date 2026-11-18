/**
 * shot.mjs — 用系统 Edge 给页面截图，用于视觉比对。
 *
 * 用法：
 *   node scripts/shot.mjs <url> <out.png> [width] [height] [waitMs]
 */

import { chromium } from 'playwright-core';

const [, , url, out, w = '1600', h = '900', wait = '1200'] = process.argv;
if (!url || !out) {
  console.error('usage: node shot.mjs <url> <out.png> [w] [h] [waitMs]');
  process.exit(2);
}

const browser = await chromium.launch({
  channel: 'msedge',
  args: ['--force-device-scale-factor=1'],
});
try {
  const page = await browser.newPage({
    viewport: { width: Number(w), height: Number(h) },
    deviceScaleFactor: 1,
  });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(Number(wait));
  await page.screenshot({ path: out });
  if (errors.length) {
    console.error('PAGE ERRORS:');
    for (const e of errors) console.error('  ' + e);
  }
  const info = await page.evaluate(() => ({
    canvas: (() => {
      const c = document.querySelector('canvas');
      return c ? { w: c.width, h: c.height } : null;
    })(),
    leaderboard: !!document.querySelector('#game-leaderboard'),
    turn: document.querySelector('#turn-counter')?.textContent ?? null,
  }));
  console.log(JSON.stringify(info));
} finally {
  await browser.close();
}
