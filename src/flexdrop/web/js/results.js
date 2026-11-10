// Results: simulation comparison of dispatch policies (from `flexdrop pipeline` reports).

import { clear, el, fmtMoney, fmtNum, fmtPct, POLICY_LABELS } from './util.js';

const SVG = 'http://www.w3.org/2000/svg';
const ORDER = ['independent', 'batching', 'flexdrop_noflex', 'flexdrop', 'flexdrop_l3'];

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

/** Horizontal bar chart; the FlexDrop bars are highlighted. */
function barChart(title, rows, format, note) {
  const width = 460;
  const rowH = 34;
  const labelW = 190;
  const height = rows.length * rowH + 10;
  const max = Math.max(...rows.map((r) => r.value), 1e-9);
  const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': title });
  rows.forEach((r, i) => {
    const y = i * rowH + 6;
    const w = ((width - labelW - 70) * r.value) / max;
    chart.append(
      svg('text', { x: 0, y: y + 17, 'font-size': 12, fill: '#3d4352' }, r.label),
      svg('rect', { x: labelW, y, width: Math.max(2, w), height: 22, rx: 5, fill: r.highlight ? '#fc8019' : '#cbd2dd' }),
      svg('text', { x: labelW + w + 6, y: y + 16, 'font-size': 12, 'font-weight': r.highlight ? 700 : 400, fill: '#1b1f2a' }, format(r.value)),
    );
  });
  return el('div', { class: 'card chart' }, el('h3', {}, title), chart, note ? el('div', { class: 'c-note' }, note) : null);
}

export class Results {
  constructor(container, engine) {
    this.container = container;
    this.engine = engine;
    this.data = null;
    this.scenario = null;
  }

  async load() {
    clear(this.container).append(el('p', { class: 'muted' }, 'Loading simulation results…'));
    try {
      this.data = await this.engine.api.get('/v1/reports/simulation');
    } catch (e) {
      clear(this.container).append(el('div', { class: 'card' }, el('p', { class: 'error' }, `Could not load results: ${e.message}`)));
      return;
    }
    this.scenario = this.scenario || (this.data.scenarios.campus ? 'campus' : Object.keys(this.data.scenarios)[0]);
    this.render();
  }

  render() {
    clear(this.container);
    if (!this.data || !this.data.available) {
      this.container.append(el('div', { class: 'card' },
        el('h2', {}, 'No simulation results yet'),
        el('p', { class: 'muted' }, 'Run `flexdrop pipeline` (about 2 minutes) to generate them, then reload this tab.')));
      return;
    }
    const scenarios = Object.keys(this.data.scenarios);
    const results = this.data.scenarios[this.scenario];
    const policies = ORDER.filter((p) => results[p]);
    const base = results.independent;
    const fd = results.flexdrop;

    const switcher = el('div', { class: 'seg' },
      scenarios.map((s) => el('button', { class: s === this.scenario ? 'active' : '', onclick: () => { this.scenario = s; this.render(); } },
        s === 'campus' ? 'Campus' : s === 'city' ? 'City' : s)));
    this.container.append(el('div', { class: 'results-head' },
      el('div', {}, el('h2', {}, 'Simulation results: FlexDrop vs today\'s dispatch'),
        el('p', { class: 'muted small' }, `4-hour evening on a synthetic ${this.scenario}. ${base?.orders_total ?? ''} orders, identical for every policy.`)),
      switcher));

    if (base && fd) {
      const rel = (k) => ((fd[k] - base[k]) / base[k]) * 100;
      const onTimePp = (fd.on_time_rate - base.on_time_rate) * 100;
      const signed = (v, unit) => `${v > 0 ? '+' : ''}${v.toFixed(1)}${unit}`;
      // [label, value, detail, favourable?]  (on-time within the proposal's 1 pp tolerance counts as favourable)
      const cards = [
        ['Cost per order', signed(rel('fulfilment_cost_per_order'), '%'), `${fmtMoney(base.fulfilment_cost_per_order, 1)} → ${fmtMoney(fd.fulfilment_cost_per_order, 1)} incl. rewards`, rel('fulfilment_cost_per_order') < 0],
        ['Distance per order', signed(rel('km_per_order'), '%'), `${fmtNum(base.km_per_order, 2)} → ${fmtNum(fd.km_per_order, 2)} km`, rel('km_per_order') < 0],
        ['On-time rate', signed(onTimePp, ' pp'), `${fmtPct(base.on_time_rate)} → ${fmtPct(fd.on_time_rate)} (target: no worse than -1 pp)`, onTimePp >= -1],
        ['Orders coordinated', fmtPct(fd.coordinated_share, 0), `vs ${fmtPct(results.batching?.coordinated_share ?? 0, 0)} with batching`, true],
        ['Students choosing flexible', fmtPct(fd.flex_share_of_orders, 0), `avg reward ${fmtMoney(fd.rewards_per_order / Math.max(fd.flex_share_of_orders, 1e-9), 1)}`, true],
        ['Food-safety violations', String(fd.food_quality_violations), `batching: ${results.batching?.food_quality_violations ?? 'n/a'}`, fd.food_quality_violations === 0],
      ];
      this.container.append(el('div', { class: 'headline' },
        cards.map(([l, v, s, good]) => el('div', { class: `kpi ${good ? 'good' : 'bad'}` }, el('div', { class: 'k-label' }, l), el('div', { class: 'k-value' }, v), el('div', { class: 'k-sub' }, s)))));
    }

    const rows = (key) => policies.map((p) => ({ label: POLICY_LABELS[p] || p, value: results[p][key], highlight: p.startsWith('flexdrop') && p !== 'flexdrop_noflex' }));
    this.container.append(el('div', { class: 'charts' },
      barChart('Fulfilment cost per order (₹, incl. rewards)', rows('fulfilment_cost_per_order'), (v) => fmtMoney(v, 1), 'Lower is better.'),
      barChart('Distance per order (km)', rows('km_per_order'), (v) => fmtNum(v, 2), 'Lower is better: fewer, shorter trips.'),
      barChart('On-time rate (approved window)', rows('on_time_rate'), (v) => fmtPct(v), 'Higher is better.'),
      barChart('Orders sharing a route', rows('coordinated_share'), (v) => fmtPct(v, 0), 'Higher means more coordination.'),
    ));

    const rob = this.data.robustness?.[this.scenario];
    if (rob) {
      const body = Object.entries(rob).map(([p, s]) => el('tr', {},
        el('td', {}, POLICY_LABELS[p] || p),
        el('td', {}, `${s.cost_change_pct_mean.toFixed(1)}% ± ${s.cost_change_pct_std.toFixed(1)}`),
        el('td', {}, `${s.on_time_change_pp_mean >= 0 ? '+' : ''}${s.on_time_change_pp_mean.toFixed(1)} ± ${s.on_time_change_pp_std.toFixed(1)} pp`),
        el('td', {}, fmtPct(s.coordinated_share_mean)),
      ));
      this.container.append(el('div', { class: 'card', style: 'margin-bottom:16px' },
        el('h3', {}, 'Robustness across independent random scenarios (mean ± std)'),
        el('div', { class: 'table-wrap' }, el('table', { class: 'table' },
          el('thead', {}, el('tr', {}, el('th', {}, 'Policy'), el('th', {}, 'Cost vs independent'), el('th', {}, 'On-time vs independent'), el('th', {}, 'Coordinated'))),
          el('tbody', {}, body)))));
    }
    this.container.append(el('p', { class: 'note' },
      'Synthetic data and hypothetical costs (see docs/assumptions.md). "Optimiser only" is the ablation without customer flexibility: ',
      'the gap between it and FlexDrop is the value created by students choosing flexible delivery.'));
  }
}
