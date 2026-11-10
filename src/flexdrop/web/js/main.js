// Boot: obtain a session (studio mode) or ask for an API key, then wire views and the clock.

import { Api, fetchSession } from './api.js';
import { Checkout } from './checkout.js';
import { Engine } from './engine.js';
import { CampusMap } from './map.js';
import { Ops } from './ops.js';
import { Results } from './results.js';
import { fmtTime, toast } from './util.js';

const KEY_STORAGE = 'flexdrop.apiKey';

async function connect() {
  const session = await fetchSession().catch(() => null);
  if (session) return { api: new Api(session.api_key), world: session.world };
  return loginWithKey();
}

/** Non-studio servers: ask for an API key (kept only for this browser tab). */
function loginWithKey() {
  const modal = document.getElementById('login');
  const keyInput = document.getElementById('login-key');
  const zoneInput = document.getElementById('login-zone');
  const error = document.getElementById('login-error');
  modal.classList.remove('hidden');
  try { keyInput.value = sessionStorage.getItem(KEY_STORAGE) || ''; } catch { /* storage unavailable */ }
  return new Promise((resolve) => {
    document.getElementById('login-btn').addEventListener('click', async () => {
      error.textContent = '';
      const api = new Api(keyInput.value.trim());
      const zone = zoneInput.value.trim() || null;
      try {
        const stores = await api.get(`/v1/stores${zone ? `?zone=${encodeURIComponent(zone)}` : ''}`);
        try { sessionStorage.setItem(KEY_STORAGE, api.key); } catch { /* ignore */ }
        modal.classList.add('hidden');
        resolve({
          api,
          world: {
            zone,
            stores: stores.map((s) => ({ id: s.id, name: s.name, category: s.category, lat: s.location.lat, lng: s.location.lng })),
            gates: [],
            menus: { FOOD: [], GROCERY: [] },
            started_at: new Date().toISOString(),
          },
        });
      } catch (e) {
        error.textContent = e.status === 401 || e.status === 403 ? 'Key rejected (needs the admin role).' : e.message;
      }
    });
  });
}

function setupTabs(map, results) {
  const tabs = [...document.querySelectorAll('.tab')];
  let resultsLoaded = false;
  for (const tab of tabs) {
    tab.addEventListener('click', () => {
      for (const t of tabs) {
        const active = t === tab;
        t.classList.toggle('active', active);
        t.setAttribute('aria-selected', String(active));
        document.getElementById(`view-${t.dataset.view}`).classList.toggle('active', active);
      }
      if (tab.dataset.view === 'ops') map.invalidate();
      if (tab.dataset.view === 'results' && !resultsLoaded) {
        resultsLoaded = true;
        results.load();
      }
      try { history.replaceState(null, '', `#${tab.dataset.view}`); } catch { /* ignore */ }
    });
  }
  const initial = location.hash.replace('#', '');
  const target = tabs.find((t) => t.dataset.view === initial);
  if (target) target.click();
}

function setupClock(engine) {
  const clock = document.getElementById('clock-now');
  const autoBtn = document.getElementById('btn-auto');
  const demand = document.getElementById('demand');
  const render = () => { clock.textContent = fmtTime(engine.now()); };
  engine.onChange(render);
  render();
  const step = (minutes) => async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { await engine.tick(minutes, false); } catch (err) { toast(err.message, 'err'); } finally { btn.disabled = false; render(); }
  };
  document.getElementById('btn-tick1').addEventListener('click', step(1));
  document.getElementById('btn-tick5').addEventListener('click', step(5));
  demand.addEventListener('change', () => { engine.demandRate = Number(demand.value); });
  autoBtn.addEventListener('click', () => {
    const on = !engine.auto;
    engine.setAuto(on, (err) => {
      toast(`Auto-run stopped: ${err.message}`, 'err');
      autoBtn.textContent = '▶ Auto';
    });
    autoBtn.textContent = on ? '⏸ Pause' : '▶ Auto';
    if (on) toast('Auto-run: 1 simulated minute every 1.5 s with student demand', 'ok');
  });
}

/**
 * Operations map: the 3D campus (three.js, loaded the first time the operations screen opens)
 * with the street map as an alternative view. If WebGL or the 3D module is unavailable, or the
 * world has no Studio site plan, the street map is used.
 */
async function setupMapViews(engine) {
  const sceneEl = document.getElementById('scene3d');
  const mapEl = document.getElementById('map');
  const btn3d = document.getElementById('view-3d');
  const btn2d = document.getElementById('view-2d');
  const resetBtn = document.getElementById('btn-reset-view');
  const dayBtn = document.getElementById('btn-daynight');
  const zoomSel = document.getElementById('zoom-to');
  const rotateBtn = document.getElementById('btn-rotate');
  const opsPanel = document.getElementById('view-ops');
  const note = document.getElementById('world-note');
  if (note && engine.world.note) note.textContent = engine.world.note;
  const can3d = Boolean(engine.world.site_plan);
  let scene = null;
  let street = null;
  let loading = null;
  let current = can3d ? '3d' : '2d';
  if (!can3d) btn3d.disabled = true;

  const wireScene = () => {
    const syncDay = () => { dayBtn.textContent = scene.mode === 'day' ? 'Night view' : 'Day view'; };
    syncDay();
    dayBtn.addEventListener('click', () => {
      scene.setTimeOfDay(scene.mode === 'day' ? 'night' : 'day');
      syncDay();
    });
    const targets = [
      ...scene.sites.map((s) => ({ name: s.ref.name, kind: s.kind, site: s })),
      ...scene.landmarks.map((l) => ({ name: l.name, kind: 'landmark', site: l })),
    ];
    const groups = [['Landmarks', (t) => t.kind === 'landmark'], ['Hostels', (t) => t.kind === 'hostel'],
      ['Student PGs', (t) => t.kind === 'pg'], ['Instamart', (t) => t.kind === 'grocery'], ['Restaurants', () => true]];
    const used = new Set();
    for (const [title, test] of groups) {
      const og = document.createElement('optgroup');
      og.label = title;
      targets.forEach((t, i) => {
        if (used.has(i) || !test(t)) return;
        used.add(i);
        const o = document.createElement('option');
        o.value = String(i);
        o.textContent = t.name;
        og.append(o);
      });
      if (og.children.length) zoomSel.append(og);
    }
    zoomSel.addEventListener('change', () => {
      if (zoomSel.value === 'overview') scene.resetView();
      else scene.focusOn(targets[Number(zoomSel.value)].site);
    });
  };

  const show = (view) => {
    current = view;
    if (view === '3d' && !scene) {
      ensureScene();
      return;
    }
    sceneEl.classList.toggle('hidden', view !== '3d');
    mapEl.classList.toggle('hidden', view !== '2d');
    btn3d.classList.toggle('active', view === '3d');
    btn2d.classList.toggle('active', view === '2d');
    for (const b of [resetBtn, rotateBtn, dayBtn, zoomSel]) b.disabled = view !== '3d';
    if (view === '2d') {
      if (!street) street = new CampusMap(mapEl, engine);
      street.invalidate();
      street.render();
    } else {
      scene.invalidate();
      scene.render();
    }
  };

  // Build the 3D world once, the first time the operations screen is shown.
  const ensureScene = () => {
    if (scene || !can3d) return Promise.resolve(scene);
    if (!loading) {
      loading = (async () => {
        try {
          const mod = await import('./campus3d.js');
          if (mod.webglAvailable()) {
            sceneEl.classList.remove('hidden');
            scene = new mod.Campus3D(sceneEl, engine);
            wireScene();
          }
        } catch (e) {
          console.warn('3D view unavailable, using the street map', e);
        }
        if (!scene) {
          btn3d.disabled = true;
          current = '2d';
        }
        show(current);
        return scene;
      })();
    }
    return loading;
  };

  btn3d.addEventListener('click', () => !btn3d.disabled && show('3d'));
  btn2d.addEventListener('click', () => show('2d'));
  resetBtn.addEventListener('click', () => {
    scene?.resetView();
    zoomSel.value = 'overview';
  });
  rotateBtn.addEventListener('click', () => {
    if (!scene) return;
    const on = !scene.autoRotate;
    scene.setAutoRotate(on);
    rotateBtn.classList.toggle('on', on);
  });
  return {
    get scene() {
      return scene;
    },
    render() {
      if (current === '3d') scene?.render();
      else street?.render();
    },
    invalidate() {
      if (!opsPanel.classList.contains('active')) return;
      if (current === '3d' && !scene) show('3d');
      else if (current === '3d') scene.invalidate();
      else show('2d');
    },
  };
}

async function main() {
  let ctx;
  try {
    ctx = await connect();
  } catch (e) {
    toast(`Cannot connect: ${e.message}`, 'err');
    return;
  }
  const engine = new Engine(ctx.api, ctx.world);
  const map = await setupMapViews(engine);
  const ops = new Ops(engine, map);
  const checkout = new Checkout(document.getElementById('phone-screen'), document.getElementById('my-orders'), engine);
  const results = new Results(document.getElementById('results'), engine);
  window.flexdrop = { engine, ops, checkout, results, get scene() { return map.scene; } }; // handy for debugging from the console
  setupTabs(map, results);
  setupClock(engine);
  try {
    await engine.run(() => engine.refresh());
    engine.log('connected - campus world loaded', 'ok');
    engine.emit();
  } catch (e) {
    toast(`Initial load failed: ${e.message}`, 'err');
  }
}

main();
