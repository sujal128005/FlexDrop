// Operations dashboard: KPIs, controls, decision log, plans table (map lives in map.js).

import { clear, colorFor, el, fmtMoney, fmtNum, fmtPct, fmtTime, shortId, toast } from './util.js';

export class Ops {
  constructor(engine, map) {
    this.engine = engine;
    this.map = map;
    this.kpis = document.getElementById('kpis');
    this.logBox = document.getElementById('log');
    this.tableBody = document.querySelector('#plans-table tbody');
    this.count = document.getElementById('plans-count');
    this.optStatus = document.getElementById('opt-status');
    this.killBtn = document.getElementById('btn-kill');

    const guard = (btn, fn) => btn.addEventListener('click', async () => {
      btn.disabled = true;
      try { await fn(); } catch (e) { toast(e.message, 'err'); } finally { btn.disabled = false; }
    });
    guard(document.getElementById('btn-gen'), () => engine.generateNow(10));
    guard(document.getElementById('btn-opt'), () => engine.optimizeNow());
    guard(document.getElementById('btn-delay'), () => engine.injectDelay());
    guard(this.killBtn, () => engine.toggleKill());
    engine.onChange(() => this.render());
  }

  render() {
    this.renderKpis();
    this.renderLog();
    this.renderPlans();
    this.map?.render();
    const k = this.engine.killSwitch;
    this.killBtn.textContent = `Kill switch: ${k ? 'ON' : 'OFF'}`;
    this.killBtn.classList.toggle('on', k);
    const last = this.engine.lastOpt;
    this.optStatus.textContent = last
      ? `Last run ${fmtTime(last.as_of)} · ${last.mode} · ${last.status} · ${last.latency_ms.toFixed(0)} ms · ` +
        `held ${last.held_order_ids.length}, waiting for food ${last.deferred_order_ids.length}`
      : 'Optimiser idle - advance the clock or press "Run optimiser".';
  }

  renderKpis() {
    const m = this.engine.metrics || {};
    const plans = [...this.engine.plans.values()];
    const grouped = plans.filter((p) => p.order_ids.length > 1);
    const savings = plans.reduce((s, p) => s + Math.max(0, p.expected_savings), 0);
    const active = this.engine.activePlans().length;
    const orders = [...this.engine.orders.values()];
    const pending = orders.filter((o) => o.status === 'PENDING').length;
    const cards = [
      ['Orders', fmtNum(m.orders_total), `${pending} waiting · ${m.orders_delivered ?? 0} delivered`],
      ['On-time', fmtPct(m.on_time_rate), 'within approved window', true],
      ['Coordinated', fmtPct(m.coordinated_share), `${grouped.length} multi-order routes`, true],
      ['Saving vs solo', fmtMoney(savings), 'planned, all routes', true],
      ['Flexible uptake', fmtPct(m.offer_acceptance_rate), `${m.flex_orders ?? 0} flexible orders`],
      ['Active routes', fmtNum(active), `opt. P95 ${fmtNum(m.optimization_latency_p95_ms)} ms`],
    ];
    clear(this.kpis);
    for (const [label, value, sub, good] of cards) {
      this.kpis.append(el('div', { class: `kpi ${good ? 'good' : ''}` },
        el('div', { class: 'k-label' }, label), el('div', { class: 'k-value' }, value), el('div', { class: 'k-sub' }, sub)));
    }
  }

  renderLog() {
    clear(this.logBox);
    for (const entry of this.engine.logEntries.slice(0, 40)) {
      this.logBox.append(el('li', { class: entry.kind }, el('span', { class: 't' }, fmtTime(entry.t)), entry.text));
    }
    if (!this.engine.logEntries.length) {
      this.logBox.append(el('li', {}, 'Decisions will appear here. Try "Generate 10 orders", then advance the clock.'));
    }
  }

  renderPlans() {
    const plans = [...this.engine.plans.values()].slice(0, 40);
    this.count.textContent = `(${this.engine.plans.size} in the last 48 h, newest first)`;
    clear(this.tableBody);
    if (!plans.length) {
      this.tableBody.append(el('tr', {}, el('td', { colspan: 8, class: 'muted' }, 'No plans yet.')));
      return;
    }
    for (const p of plans) {
      const flexUsed = Object.values(p.flex_allocation).reduce((a, b) => a + b, 0);
      this.tableBody.append(
        el('tr', {},
          el('td', {}, el('span', { class: 'swatch', style: `background:${colorFor(p.plan_id)}` }), shortId(p.plan_id), p.version > 1 ? ` v${p.version}` : ''),
          el('td', {}, p.partner_id),
          el('td', {}, p.order_ids.map(shortId).join(', ')),
          el('td', {}, p.kind === 'FALLBACK' ? 'late-risk fallback' : p.order_ids.length > 1 ? 'coordinated' : 'single'),
          el('td', {}, el('span', { class: `badge status-${p.status}` }, p.status.replace('_', ' '))),
          el('td', {}, fmtMoney(p.cost_total, 1)),
          el('td', { class: p.expected_savings > 0 ? 'pos' : '' }, fmtMoney(p.expected_savings, 1)),
          el('td', {}, `${flexUsed.toFixed(1)} min`),
        ),
      );
    }
  }
}
