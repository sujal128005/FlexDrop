// Client-side engine: simulation clock, shared state, and the dispatch loop.
//
// The UI acts as three Swiggy systems talking to the real FlexDrop API:
//   * checkout  - creates orders, shows offers, records the student's choice
//   * dispatch  - calls /v1/optimize every simulated minute and accepts plans
//   * rider app - reports PICKED_UP / DELIVERED when the simulated clock passes a stop's ETA
// All timestamps sent to the API come from the simulated clock, so time can be fast-forwarded.

import { ApiError } from './api.js';
import { haversineKm, pick, poisson, shortId } from './util.js';

const ACTIVE = new Set(['PROPOSED', 'ACCEPTED', 'IN_PROGRESS']);

export class Engine {
  constructor(api, world) {
    this.api = api;
    this.world = world;
    this.zone = world.zone || null;
    const start = Date.parse(world.started_at || '') || Date.now();
    this.simNow = Math.ceil(start / 60000) * 60000;
    this.synced = false;
    this.orders = new Map();
    this.plans = new Map();
    this.partners = new Map();
    this.metrics = null;
    this.killSwitch = false;
    this.reported = new Set();
    this.partnerStart = new Map();
    this.logEntries = [];
    this.listeners = new Set();
    this.queue = Promise.resolve();
    this.auto = null;
    this.demandRate = 1.8;
    this.lastOpt = null;
    this.seq = 0;
    this.tag = Math.random().toString(36).slice(2, 7);
  }

  // ------------------------------------------------------------------ events
  onChange(fn) { this.listeners.add(fn); }
  emit() { for (const fn of this.listeners) { try { fn(this); } catch (e) { console.error(e); } } }

  log(text, kind = '') {
    this.logEntries.unshift({ t: this.simNow, text, kind });
    this.logEntries = this.logEntries.slice(0, 80);
  }

  /** Serialise all mutating work so auto-run ticks never overlap. */
  run(task) {
    const next = this.queue.then(task, task);
    this.queue = next.catch(() => {});
    return next;
  }

  now() { return new Date(this.simNow); }
  iso(t = this.simNow) { return new Date(t).toISOString(); }
  zoneQuery(prefix = '?') { return this.zone ? `${prefix}zone=${encodeURIComponent(this.zone)}` : ''; }

  // ------------------------------------------------------------------ data
  async refresh() {
    const z = this.zoneQuery('&');
    const [orders, plans, partners, metrics] = await Promise.all([
      this.api.get(`/v1/orders?since_hours=48&limit=2000${z}`),
      this.api.get(`/v1/plans?since_hours=48&limit=1000${z}`),
      this.api.get(`/v1/partners?limit=2000${z}`),
      this.api.get('/v1/metrics?window_hours=48'),
    ]);
    this.orders = new Map(orders.map((o) => [o.id, o]));
    this.plans = new Map(plans.map((p) => [p.plan_id, p]));
    this.partners = new Map(partners.map((p) => [p.id, p]));
    this.metrics = metrics.values;
    this.killSwitch = Boolean(this.metrics.kill_switch_enabled);
    if (!this.synced) {
      // after a page reload, resume the clock from the latest activity instead of rewinding
      this.synced = true;
      const latest = Math.max(0, ...orders.map((o) => Date.parse(o.placed_at)), ...plans.map((p) => Date.parse(p.start_at)));
      if (latest > this.simNow) this.simNow = Math.ceil(latest / 60000) * 60000;
    }
    this.emit();
  }

  activePlans() {
    return [...this.plans.values()].filter((p) => ACTIVE.has(p.status));
  }

  // ------------------------------------------------------------------ orders
  buildOrder({ store, gate, items, pastRate = 0.4 }) {
    const temps = [...new Set(items.map((i) => i.temp))];
    const volume = items.reduce((s, i) => s + i.volume * i.qty, 0);
    const weight = items.reduce((s, i) => s + i.weight * i.qty, 0);
    const count = items.reduce((s, i) => s + i.qty, 0);
    const basket = items.reduce((s, i) => s + i.price * i.qty, 0);
    this.seq += 1;
    return {
      id: `FD-${this.tag}-${this.seq}`,
      store_id: store.id,
      customer_id: `student-${gate.id}-${Math.floor(Math.random() * 5000)}`,
      drop: { lat: gate.lat, lng: gate.lng },
      placed_at: this.iso(),
      item_count: Math.max(1, count),
      basket_value: basket,
      handling: {
        temp_classes: temps,
        volume_l: Math.max(0.5, Math.round(volume * 100) / 100),
        weight_kg: Math.max(0.1, Math.round(weight * 100) / 100),
        leak_risk: items.some((i) => i.leak_risk),
        raw_meat: false,
        sealed: true,
        fragile: items.some((i) => i.fragile),
      },
      customer: { past_flex_acceptance_rate: pastRate, is_student: Boolean(gate.on_campus) },
    };
  }

  async createOrder(payload) {
    const out = await this.api.post('/v1/orders', payload, { 'Idempotency-Key': `create-${payload.id}` });
    this.orders.set(out.id, out);
    return out;
  }

  async choose(orderId, offerId) {
    const body = offerId
      ? { order_id: orderId, decision: 'ACCEPT_FLEX', offer_id: offerId, source: 'flexdrop-studio' }
      : { order_id: orderId, decision: 'STANDARD', source: 'flexdrop-studio' };
    return this.api.post('/v1/preferences', body);
  }

  /** Synthetic demand: students at hostel gates order from nearby restaurants / nearest Instamart. */
  async generateOrders(n) {
    const food = this.world.stores.filter((s) => s.category === 'FOOD');
    const grocery = this.world.stores.filter((s) => s.category === 'GROCERY');
    const gates = this.world.gates;
    if (!food.length || !gates.length) return { created: 0, flex: 0 };
    let created = 0;
    let flex = 0;
    for (let i = 0; i < n; i += 1) {
      const gate = Math.random() < 0.85 ? pick(gates.filter((g) => g.on_campus)) : pick(gates);
      let store;
      let items;
      if (grocery.length && Math.random() < 0.2) {
        store = grocery.reduce((a, b) => (haversineKm(a, gate) <= haversineKm(b, gate) ? a : b));
        items = sampleItems(this.world.menus.GROCERY, 2, 4);
      } else {
        const weights = food.map((s) => Math.exp(-haversineKm(s, gate) / 1.5));
        store = weightedPick(food, weights);
        items = sampleItems(this.world.menus.FOOD, 1, 2);
      }
      const pastRate = Math.round((0.2 + Math.random() * 0.6) * 100) / 100;
      try {
        const order = await this.createOrder(this.buildOrder({ store, gate, items, pastRate }));
        created += 1;
        if (order.offers.length) {
          const best = order.offers.reduce((a, b) => (a.reward / a.extra_minutes >= b.reward / b.extra_minutes ? a : b));
          const p = Math.min(0.9, Math.max(0.05, 0.15 + 0.05 * best.reward - 0.02 * best.extra_minutes + 0.35 * pastRate));
          const accept = Math.random() < p;
          await this.choose(order.id, accept ? best.offer_id : null);
          flex += accept ? 1 : 0;
        }
      } catch (e) {
        this.log(`order rejected: ${e.message}`, 'err');
      }
    }
    if (created) this.log(`${created} new orders placed (${flex} chose flexible delivery)`, 'ok');
    return { created, flex };
  }

  // ------------------------------------------------------------------ dispatch
  async optimize() {
    const before = new Map([...this.partners.values()].map((p) => [p.id, p.location]));
    const out = await this.api.post('/v1/optimize', { as_of: this.iso(), ...(this.zone ? { zone: this.zone } : {}) });
    this.lastOpt = out;
    if (out.mode !== 'FLEXDROP') {
      this.log(`optimiser bypassed (${out.mode}); standard dispatch handles orders`, 'err');
      return out;
    }
    let grouped = 0;
    for (const plan of out.plans) {
      if (plan.order_ids.length > 1) grouped += 1;
      if (!this.partnerStart.has(plan.plan_id)) {
        const loc = before.get(plan.partner_id);
        if (loc) this.partnerStart.set(plan.plan_id, loc);
      }
      if (plan.status === 'PROPOSED') {
        try {
          await this.api.post(`/v1/plans/${plan.plan_id}/events`, { type: 'DISPATCH_ACCEPTED', at: this.iso() });
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 409)) throw e;
        }
      }
    }
    if (out.plans.length) {
      const saving = out.plans.reduce((s, p) => s + p.expected_savings, 0);
      this.log(
        `optimiser: ${out.plans.length} plan(s), ${grouped} coordinated, saving ₹${saving.toFixed(0)} vs solo; ` +
          `${out.held_order_ids.length} held for coordination (${out.latency_ms.toFixed(0)} ms)`,
        'opt',
      );
    }
    return out;
  }

  /** Rider app: report every stop whose ETA has passed, in route order. */
  async executeDueStops() {
    const now = this.simNow;
    for (const plan of this.plans.values()) {
      if (plan.status !== 'ACCEPTED' && plan.status !== 'IN_PROGRESS') continue;
      for (const stop of plan.stops) {
        const order = this.orders.get(stop.order_id);
        if (!order || order.status === 'CANCELLED') continue;
        if (stop.type === 'PICKUP' && (order.status === 'PICKED_UP' || order.status === 'DELIVERED')) continue;
        if (stop.type === 'DROP' && order.status === 'DELIVERED') continue;
        const key = `${plan.plan_id}:${plan.version}:${stop.order_id}:${stop.type}`;
        if (this.reported.has(key)) continue;
        if (Date.parse(stop.eta) > now) break;
        const type = stop.type === 'PICKUP' ? 'PICKED_UP' : 'DELIVERED';
        try {
          await this.api.post(`/v1/plans/${plan.plan_id}/events`, { type, order_id: stop.order_id, at: stop.eta });
          this.reported.add(key);
          order.status = type === 'PICKED_UP' ? 'PICKED_UP' : 'DELIVERED';
          if (type === 'DELIVERED' && order.mode === 'FLEX') {
            this.log(`order ${shortId(order.id)} delivered; ₹${order.reward} reward credited`, 'ok');
          }
        } catch (e) {
          this.reported.add(key);
          this.log(`event ${type} for ${shortId(stop.order_id)} skipped: ${e.message}`, 'err');
          break;
        }
      }
    }
  }

  /** Advance the simulated clock minute by minute. */
  tick(minutes = 1, withDemand = false) {
    return this.run(async () => {
      for (let i = 0; i < minutes; i += 1) {
        this.simNow += 60000;
        if (withDemand) await this.generateOrders(poisson(this.demandRate));
        await this.executeDueStops();
        await this.optimize();
        await this.refresh();
      }
    });
  }

  optimizeNow() {
    return this.run(async () => {
      const out = await this.optimize();
      await this.refresh();
      return out;
    });
  }

  generateNow(n) {
    return this.run(async () => {
      const res = await this.generateOrders(n);
      await this.refresh();
      return res;
    });
  }

  injectDelay() {
    return this.run(async () => {
      const candidates = this.activePlans()
        .filter((p) => p.status !== 'IN_PROGRESS')
        .sort((a, b) => b.order_ids.length - a.order_ids.length);
      const plan = candidates[0];
      if (!plan) {
        this.log('no unstarted plan to delay - generate orders and run the optimiser first', 'err');
        return null;
      }
      const pickup = plan.stops.find((s) => s.type === 'PICKUP');
      const newReady = new Date(this.simNow + 20 * 60000).toISOString();
      const res = await this.api.post(`/v1/plans/${plan.plan_id}/events`, {
        type: 'PREP_DELAY',
        order_id: pickup.order_id,
        new_ready_at: newReady,
        at: this.iso(),
      });
      const released = res.released_order_ids.length ? `; released ${res.released_order_ids.map(shortId).join(', ')} back to the pool` : '';
      this.log(`restaurant delayed order ${shortId(pickup.order_id)} by ~20 min → re-optimiser: ${res.action}${released}`, 'cre');
      await this.refresh();
      return res;
    });
  }

  toggleKill() {
    return this.run(async () => {
      const enabled = !this.killSwitch;
      await this.api.post('/v1/admin/kill-switch', {
        enabled,
        reason: enabled ? 'demo: operator disabled FlexDrop' : 'demo: re-enabled',
      });
      this.log(enabled ? 'KILL SWITCH ON - FlexDrop disabled, standard dispatch only' : 'kill switch off - FlexDrop active', enabled ? 'err' : 'ok');
      await this.refresh();
    });
  }

  setAuto(on, onError) {
    if (on && !this.auto) {
      this.auto = setInterval(() => {
        if (this.busy) return;
        this.busy = true;
        this.tick(1, true)
          .catch((e) => { this.setAuto(false); onError?.(e); })
          .finally(() => { this.busy = false; });
      }, 1500);
    } else if (!on && this.auto) {
      clearInterval(this.auto);
      this.auto = null;
    }
  }

  /** Interpolated rider position for an active plan at simulated time ``t`` (default: now). */
  riderPosition(plan, t = this.simNow) {
    const partner = this.partners.get(plan.partner_id);
    const start = this.partnerStart.get(plan.plan_id) || partner?.location;
    if (!start) return null;
    const pts = [{ t: Date.parse(plan.start_at), lat: start.lat, lng: start.lng }];
    for (const s of plan.stops) {
      pts.push({ t: Date.parse(s.eta), lat: s.location.lat, lng: s.location.lng });
      pts.push({ t: Date.parse(s.etd), lat: s.location.lat, lng: s.location.lng });
    }
    const now = t;
    if (now <= pts[0].t) return pts[0];
    for (let i = 1; i < pts.length; i += 1) {
      if (now <= pts[i].t) {
        const a = pts[i - 1];
        const b = pts[i];
        const f = b.t === a.t ? 1 : (now - a.t) / (b.t - a.t);
        return { lat: a.lat + (b.lat - a.lat) * f, lng: a.lng + (b.lng - a.lng) * f };
      }
    }
    return pts[pts.length - 1];
  }
}

function sampleItems(menu, min, max) {
  const n = min + Math.floor(Math.random() * (max - min + 1));
  const chosen = new Map();
  for (let i = 0; i < n; i += 1) {
    const item = pick(menu);
    const prev = chosen.get(item.name);
    chosen.set(item.name, { ...item, qty: (prev?.qty || 0) + 1 });
  }
  return [...chosen.values()];
}

function weightedPick(items, weights) {
  const total = weights.reduce((a, b) => a + b, 0);
  let r = Math.random() * total;
  for (let i = 0; i < items.length; i += 1) {
    r -= weights[i];
    if (r <= 0) return items[i];
  }
  return items[items.length - 1];
}
