/**
 * e2e-page.mjs — 真浏览器 × 真后端联调检查。
 *
 * 做四件事：
 *   ① 开两个页面（Alice / Bob）加入同一房间；
 *   ② 让 Bob 点 Start 开局；
 *   ③ 等 3 秒（约 6 Tick）后截图，并断言画布尺寸、格边长（随 R2.3 的人数定尺寸而变）、
 *      迷雾底色、己方色块、四个浮动面板（存在 / 在视口内 / 覆盖在 canvas 之上）、
 *      回合文本、聊天栏首行提示；
 *   ④ 拖动排行榜 80 px：断言 left 变化、localStorage 记下了位置，且新开页面能恢复该位置。
 *
 * 阈值说明：全屏地图布局 —— 视口 1600×900 ⇒ canvas 1600×900（AGENT.md §5 的 UI 布局）；
 *   地图边长 = min(1600,900) = 900，2 人局 size ∈ [12,18]（R2.3）⇒ 格边长 = 900/size ∈ [50,75]。
 *   地图之外的非地图区是更深的 --bg-void (#141414)，地图内仍是迷雾色 #393939。
 *
 * 用法（必须先起后端：python -m generals --port 8200）：
 *   cd web && node scripts/e2e-page.mjs http://127.0.0.1:8200 ../.dispatch/live.png
 *
 * 退出码：0 = 通过；1 = 有断言失败（错误会打印）。
 */

import { chromium } from 'playwright-core';

const base = process.argv[2] ?? 'http://127.0.0.1:8200';
const outPng = process.argv[3] ?? '../.dispatch/live.png';
const room = `live-${Date.now().toString(36)}`;
const waitMs = Number(process.argv[4] ?? 3500);

const VIEWPORT = { width: 1600, height: 900 };
const PANEL_IDS = ['turn-counter', 'surrender-button', 'game-leaderboard-container', 'in-game-chat'];
const LB_KEY = 'generals.panel.game-leaderboard-container';

const errors = [];
const failures = [];

function watch(page, tag) {
  page.on('pageerror', (e) => errors.push(`[${tag}] ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && !m.text().includes('404')) errors.push(`[${tag}] ${m.text()}`);
  });
}

/** 读取页面内统计：画布尺寸、迷雾覆盖面、己方色块数、网格线间距、面板几何、排行榜与回合文本。 */
async function stats(page) {
  return page.evaluate((panelIds) => {
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
    const voidBg = counts.get('20,20,20') ?? 0; // #141414 非地图区
    const red = counts.get('255,0,0') ?? 0; // 玩家 0 = red
    const blue = counts.get('39,146,255') ?? 0; // 玩家 1 = blue

    // 己方地块所在行：找出该行上的网格线中心，相邻中心间距即格边长（按 R2.3 由人数定 size）。
    // 注意：可见区可能只有两三格，这里只统计**相邻**（间距在合理范围内）的间距，
    // 否则会把「两块相距很远的可见区」误当成一格（真正的格边长由 __generalsDebug 给出）。
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
    for (let i = 1; i < gaps.length; i += 1) {
      const d = gaps[i] - gaps[i - 1];
      if (d >= 20 && d <= 160) deltas.push(d);
    }
    deltas.sort((a, b) => a - b);

    const rectOf = (id) => {
      const el = document.getElementById(id);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        left: Math.round(r.left),
        top: Math.round(r.top),
        right: Math.round(r.right),
        bottom: Math.round(r.bottom),
        width: Math.round(r.width),
        height: Math.round(r.height),
        position: cs.position,
        zIndex: Number(cs.zIndex),
        visible: r.width > 0 && r.height > 0 && cs.display !== 'none',
      };
    };
    const panels = {};
    for (const id of panelIds) panels[id] = rectOf(id);

    return {
      width,
      height,
      total: width * height,
      fog,
      voidBg,
      red,
      blue,
      cell: deltas.length ? Math.round(deltas[Math.floor(deltas.length / 2)]) : 0,
      gridLines: gaps.length,
      turn: document.querySelector('#turn-counter')?.textContent ?? null,
      rows: document.querySelectorAll('#game-leaderboard .leaderboard-name').length,
      chatHint: document.querySelector('#in-game-chat .chat-messages-container p')?.textContent ?? null,
      panels,
      canvasRect: (() => {
        const r = canvas.getBoundingClientRect();
        return { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
      })(),
    };
  }, PANEL_IDS);
}

/**
 * 比例检查（R6.6），用 `?demo=1&demoRatio=0.5` 的本地演示页（不连服务端）：
 *   ① 选中格**底部**要有浅色 `50%` 文字（说明比例叠加显示在格子里，而不是别处）；
 *   ② 按一次方向键提交调兵后，比例必须立刻回到 100%（比例不自动保持）。
 * 返回 { ok, reasons, inkBefore, inkAfter, ratioBefore, ratioAfter }。
 */
async function checkRatio(context, base) {
  const reasons = [];
  const page = await context.newPage();
  watch(page, 'ratio');
  await page.goto(`${base}/?demo=1&demoRatio=0.5`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);

  const before = await page.evaluate(() => {
    const geo = window.__generalsDebug?.();
    if (!geo || geo.selected === null) return null;
    const canvas = document.querySelector('canvas');
    const ctx = canvas.getContext('2d');
    const dpr = canvas.width / parseFloat(canvas.style.width || String(canvas.width));
    const r = geo.rect(geo.selected);
    // 格子底部一带（避开中央的兵量数字与调兵箭头）。
    const x0 = Math.round((r.x + r.gap / 2) * dpr);
    const y0 = Math.round((r.y + r.gap / 2 + r.fill * 0.66) * dpr);
    const w = Math.max(1, Math.round(r.fill * dpr));
    const h = Math.max(1, Math.round(r.fill * 0.34 * dpr));
    const data = ctx.getImageData(x0, y0, w, h).data;
    let ink = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] > 200 && data[i + 1] > 200 && data[i + 2] > 200) ink += 1;
    }
    return { ratio: geo.ratio, selected: geo.selected, ink };
  });
  if (!before) {
    await page.close();
    return { ok: false, reasons: ['demo 页拿不到 __generalsDebug 或没有选中格'] };
  }
  if (before.ratio !== 0.5) reasons.push(`demoRatio=0.5 时比例应为 0.5，实际 ${before.ratio}`);
  if (before.ink < 20) reasons.push(`选中格底部没有看到比例文字（浅色像素=${before.ink}）`);

  // 按 D 提交一次调兵：比例应回到 100%（R6.6「比例不自动保持」）。
  await page.keyboard.press('KeyD');
  await page.waitForTimeout(150);
  const after = await page.evaluate(() => {
    const geo = window.__generalsDebug?.();
    return geo ? { ratio: geo.ratio, chain: geo.chain } : null;
  });
  if (!after) reasons.push('按 D 之后拿不到调试快照');
  else {
    if (after.ratio !== 1) reasons.push(`提交一次调兵后比例应回到 1.0，实际 ${after.ratio}`);
    if (!(after.chain >= 1)) reasons.push(`按 D 之后调兵链应有请求，实际 ${after.chain}`);
  }

  await page.close();
  return {
    ok: reasons.length === 0,
    reasons,
    inkBefore: before.ink,
    ratioBefore: before.ratio,
    ratioAfter: after ? after.ratio : null,
  };
}

/** 用 page.mouse 把排行榜横向拖动 dx 像素（负数 = 向左）；返回前后矩形与 localStorage 值。 */
async function dragLeaderboard(page, dx = -80) {
  const box = await page.locator('#game-leaderboard-container').boundingBox();
  if (!box) return null;
  const before = { left: Math.round(box.x), top: Math.round(box.y) };
  const startX = box.x + box.width / 2;
  const startY = box.y + Math.min(60, box.height / 2);
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  // 分多步移动，确保越过 4 px 的拖动阈值（见 src/ui/floating.ts）。
  for (let i = 1; i <= 8; i += 1) await page.mouse.move(startX + (dx * i) / 8, startY);
  await page.mouse.up();
  await page.waitForTimeout(150);
  const afterBox = await page.locator('#game-leaderboard-container').boundingBox();
  const stored = await page.evaluate((key) => {
    const raw = window.localStorage.getItem(key);
    return raw === null ? null : JSON.parse(raw);
  }, LB_KEY);
  return { before, after: afterBox ? { left: Math.round(afterBox.x), top: Math.round(afterBox.y) } : null, stored };
}

const browser = await chromium.launch({ channel: 'msedge', headless: true });
const context = await browser.newContext({ viewport: VIEWPORT });

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
    // 画布尺寸：全屏地图布局 ⇒ 1600×900 视口下 canvas 就是 1600×900。
    if (s.width !== VIEWPORT.width || s.height !== VIEWPORT.height) {
      failures.push(`画布尺寸不是 ${VIEWPORT.width}×${VIEWPORT.height}：${s.width}×${s.height}`);
    }
    if (!s.turn) failures.push('回合计数器没有内容');
    else if (!/^Turn \d+\.?$/.test(s.turn.trim())) failures.push(`回合计数器格式不对：${s.turn}`);
    if (s.rows < 2) failures.push(`排行榜行数只有 ${s.rows}，应为 2`);
    if (s.red + s.blue < 200) failures.push(`地图上没有己方色块（red=${s.red} blue=${s.blue}）`);
    // 地图底色 = 迷雾色（R6.9）：视野外是一整片连续底色，不再露出页面底色。
    if (s.fog < 100000) failures.push(`迷雾覆盖面太小（fog=${s.fog}）`);
    // 非地图区是更深的灰（--bg-void #141414）：地图铺满短边、长边两侧留出更深的底。
    if (s.voidBg < 50000) failures.push(`非地图区（#141414）面积太小：${s.voidBg}（应为地图外侧的深灰底）`);
    // 格边长：按 R2.3，2 人局 size ∈ [12,18] ⇒ cell = 900/size ∈ [50,75]。
    // 几何事实由窗口内的 __generalsDebug 钩子给出（视野可能只有两三格，不能靠像素反推）；
    // 同时仍要求视野内能找到网格线，保证地图确实画出来了。
    if (s.gridLines < 2) failures.push(`视野内没找到网格线（gridLines=${s.gridLines}）`);
    const geo = await alice.evaluate(() => window.__generalsDebug?.() ?? null);
    if (!geo) {
      failures.push('页面没有 __generalsDebug 调试钩子（src/main.ts）');
    } else {
      console.log('geo:', JSON.stringify(geo));
      if (geo.size < 12 || geo.size > 18) {
        failures.push(`2 人局地图边长应为 [12,18]（R2.3），实际 ${geo.size}`);
      }
      const expected = Math.min(VIEWPORT.width, VIEWPORT.height) / geo.size;
      if (Math.abs(geo.cell - expected) > 1) {
        failures.push(`格边长 ${geo.cell} 与 900/size=${expected.toFixed(2)} 不符`);
      }
      if (geo.cell < 48 || geo.cell > 78) {
        failures.push(`格边长 ${geo.cell} px 超出 2 人局的取值范围 [50,75]（R2.3）`);
      }
      if (geo.ratio !== 1) failures.push(`开局比例应为 100%，实际 ${geo.ratio}`);
    }
    if (!s.chatHint || !s.chatHint.includes('Chat is being recorded.')) {
      failures.push(`聊天栏首行提示缺失：${JSON.stringify(s.chatHint)}`);
    }

    // 四个浮动面板：都在 DOM、都可见、矩形都在视口内。
    for (const id of PANEL_IDS) {
      const p = s.panels[id];
      if (!p) {
        failures.push(`面板 #${id} 不在 DOM 中`);
        continue;
      }
      if (!p.visible) {
        failures.push(`面板 #${id} 不可见（宽高为 0）`);
        continue;
      }
      if (p.left < -1 || p.top < -1 || p.right > VIEWPORT.width + 1 || p.bottom > VIEWPORT.height + 1) {
        failures.push(`面板 #${id} 超出视口：${JSON.stringify(p)}`);
      }
      // 浮动层：position:fixed 且与 canvas（铺满视口）有重叠 —— 也就是压在 canvas 之上。
      if (p.position !== 'fixed' && p.position !== 'absolute') {
        failures.push(`面板 #${id} 不是浮动层（position=${p.position}）`);
      }
      const overlap =
        Math.max(0, Math.min(p.right, s.canvasRect.right) - Math.max(p.left, s.canvasRect.left)) *
        Math.max(0, Math.min(p.bottom, s.canvasRect.bottom) - Math.max(p.top, s.canvasRect.top));
      if (overlap <= 0) failures.push(`面板 #${id} 与 canvas 没有重叠区域`);
      if (!(p.zIndex >= 1)) failures.push(`面板 #${id} 的 z-index 不大于 0（${p.zIndex}）`);
    }

    // 拖动排行榜 80 px：left 变化 + localStorage 记录 + 新页面能恢复。
    const drag = await dragLeaderboard(bob, -80);
    if (!drag || !drag.after) {
      failures.push('拖动排行榜失败：拿不到面板矩形');
    } else {
      console.log('drag:', JSON.stringify(drag));
      if (!(drag.after.left < drag.before.left - 40)) {
        failures.push(`拖动排行榜后 left 没变（前 ${drag.before.left} → 后 ${drag.after.left}）`);
      }
      if (!drag.stored || typeof drag.stored.left !== 'number') {
        failures.push(`localStorage['${LB_KEY}'] 没写入位置：${JSON.stringify(drag.stored)}`);
      } else if (drag.stored.left !== drag.after.left) {
        failures.push(
          `localStorage 位置与面板实际 left 不一致：stored=${drag.stored.left} actual=${drag.after.left}`,
        );
      }
      // 刷新验证：新开的 demo 页（同源、无服务端）应把排行榜恢复到保存的 left。
      const verify = await context.newPage();
      watch(verify, 'verify');
      await verify.goto(`${base}/?demo=1`, { waitUntil: 'networkidle' });
      await verify.waitForTimeout(300);
      const restored = await verify.evaluate(
        (id) => {
          const el = document.getElementById(id);
          return el ? Math.round(el.getBoundingClientRect().left) : null;
        },
        'game-leaderboard-container',
      );
      await verify.close();
      if (restored === null) failures.push('新页面没有排行榜，无法验证位置恢复');
      else if (drag.stored && restored !== drag.stored.left) {
        failures.push(`刷新后排行榜位置没恢复：期望 ${drag.stored.left}，实际 ${restored}`);
      }
    }

    // 比例（R6.6）：选中格底部要有 `NN%` 文字；提交一次调兵后比例必须回到 100%。
    const ratio = await checkRatio(context, base);
    console.log('ratio:', JSON.stringify(ratio));
    if (!ratio.ok) failures.push(...ratio.reasons);
  }

  await alice.screenshot({ path: outPng });
  console.log(`screenshot -> ${outPng}`);
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
