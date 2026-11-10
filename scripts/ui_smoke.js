// Optional browser end-to-end check of FlexDrop Studio (requires Node.js + Playwright).
// Usage: start `flexdrop studio --port 8765`, then: node scripts/ui_smoke.js <screenshot-dir> [scale] [base-url]
const { chromium } = require('playwright');
const OUT = process.argv[2] || '.';
const SCALE = Number(process.argv[3] || 1);
const BASE = process.argv[4] || 'http://127.0.0.1:8765';

async function waitIdle(page) {
  await page.waitForFunction(() => !document.querySelector('#btn-tick5').disabled, null, { timeout: 90000 });
}

(async () => {
  const browser = await chromium.launch({ args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: SCALE });
  const errors = [];
  page.on('console', (m) => {
    if (m.type() === 'error' && !/tile.openstreetmap|ERR_|Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message));
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#btn-continue');

  await page.click('.tab[data-view="ops"]');
  await page.click('#btn-gen');
  await page.waitForTimeout(2500);
  await page.click('#btn-gen');
  await page.waitForTimeout(2500);

  await page.click('.tab[data-view="checkout"]');
  await page.click('.menu-item:nth-child(1) .qty button:last-child');
  await page.click('.menu-item:nth-child(2) .qty button:last-child');
  await page.screenshot({ path: `${OUT}/1-checkout-menu.png` });
  await page.click('#btn-continue');
  await page.waitForSelector('#btn-confirm');
  const offers = await page.$$('.option');
  console.log('options shown:', offers.length);
  await offers[offers.length > 1 ? offers.length - 1 : 0].click();
  await page.screenshot({ path: `${OUT}/2-checkout-options.png` });
  await page.click('#btn-confirm');
  await page.waitForSelector('.confirm-hero', { timeout: 90000 });
  await page.screenshot({ path: `${OUT}/3-checkout-confirmed.png` });

  await page.click('.tab[data-view="ops"]');
  await page.click('#btn-tick5');
  await waitIdle(page);
  await page.click('#btn-gen');
  await page.waitForTimeout(2500);
  await page.click('#btn-tick5');
  await waitIdle(page);
  await page.click('#btn-delay');
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/4-operations.png` });
  for (let i = 0; i < 3; i += 1) {
    await page.click('#btn-tick5');
    await waitIdle(page);
  }
  await page.screenshot({ path: `${OUT}/5-operations-later.png` });
  const kpis = await page.$$eval('.kpi', (n) => n.map((x) => x.innerText.replace(/\n/g, ' ')));
  console.log('KPIs:', JSON.stringify(kpis));
  const logs = await page.$$eval('#log li', (n) => n.slice(0, 8).map((x) => x.innerText));
  console.log('LOG:', logs.join(' | '));

  await page.click('.tab[data-view="checkout"]');
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/6-checkout-tracking.png` });

  await page.click('.tab[data-view="results"]');
  await page.waitForSelector('.charts', { timeout: 15000 });
  await page.screenshot({ path: `${OUT}/7-results.png`, fullPage: true });

  await page.click('.tab[data-view="ops"]');
  await page.click('#btn-auto');
  await page.waitForTimeout(7000);
  await page.click('#btn-auto');
  await page.waitForTimeout(3000);
  console.log('after auto:', await page.$eval('#plans-count', (n) => n.innerText));

  const m = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: SCALE });
  m.on('pageerror', (e) => errors.push('MOBILE ' + e.message));
  await m.goto(`${BASE}/app/`, { waitUntil: 'networkidle' });
  await m.waitForSelector('#btn-continue');
  const overflow = await m.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  console.log('mobile horizontal overflow:', overflow);
  await m.screenshot({ path: `${OUT}/8-mobile.png` });

  // UI style rule: no em dashes / en dashes anywhere in the visible text of any screen
  for (const view of ['checkout', 'ops', 'results']) {
    await page.click(`.tab[data-view="${view}"]`);
    await page.waitForTimeout(400);
    const text = await page.evaluate(() => document.body.innerText);
    if (/[\u2014\u2013\u2212]/.test(text)) errors.push(`dash character visible on the ${view} screen`);
  }
  const has3d = await page.evaluate(() => Boolean(document.querySelector('#scene3d canvas')));
  console.log('3D scene rendered:', has3d);
  if (has3d) {
    // geometry self-check: no overlaps, roads only cross the campus wall at the single gate,
    // every hostel route passes through the main gate, connected road graph
    const audit = await page.evaluate(() => window.flexdrop.scene.audit());
    console.log('3D audit:', JSON.stringify(audit));
    if (audit.problems.length) errors.push(`3D audit: ${audit.problems.join('; ')}`);
    if (audit.hostelsViaGate !== 8) errors.push('not every hostel route passes through the main gate');
  }
  console.log('ERRORS:', errors.length ? errors.join('\n') : 'none');
  await browser.close();
})().catch((e) => {
  console.error('DRIVER FAILED', e);
  process.exit(1);
});
