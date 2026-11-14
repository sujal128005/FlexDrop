// Student checkout: order -> standard vs flexible delivery -> explicit consent -> live tracking.

import { clear, el, fmtMoney, fmtTime, minutesBetween, shortId, toast } from './util.js';

const STATUS_STEPS = [
  ['PENDING', 'Order received'],
  ['PLANNED', 'Rider assigned'],
  ['PICKED_UP', 'Picked up'],
  ['DELIVERED', 'Delivered'],
];

export class Checkout {
  constructor(screen, myOrdersBox, engine) {
    this.screen = screen;
    this.box = myOrdersBox;
    this.engine = engine;
    const gates = engine.world.gates || [];
    this.state = {
      step: 'menu',
      category: 'FOOD',
      gateId: gates.find((g) => g.on_campus)?.id || gates[0]?.id,
      storeId: null,
      cart: new Map(),
      order: null,
      selected: null,
      busy: false,
    };
    this.mine = [];
    engine.onChange(() => this.renderTracking());
    this.render();
  }

  get world() { return this.engine.world; }

  /** Append to the phone screen, flattening arrays and skipping empty parts. */
  show(...parts) {
    for (const p of parts.flat(2)) if (p !== null && p !== undefined && p !== false) this.screen.append(p);
  }

  stores() {
    const gate = this.world.gates.find((g) => g.id === this.state.gateId);
    const list = this.world.stores.filter((s) => s.category === this.state.category);
    if (!gate) return list;
    const d = (s) => Math.hypot(s.lat - gate.lat, s.lng - gate.lng);
    return [...list].sort((a, b) => d(a) - d(b));
  }

  render() {
    clear(this.screen);
    if (!this.world.gates || !this.world.gates.length) {
      this.show(
        el('div', { class: 'app-head' }, el('span', { class: 'title' }, 'Checkout')),
        el('p', { class: 'muted' }, 'The checkout simulator needs the campus world. Start the UI with `flexdrop studio`.'),
      );
      return;
    }
    if (this.state.step === 'menu') this.renderMenu();
    else if (this.state.step === 'options') this.renderOptions();
    else this.renderConfirmed();
  }

  renderMenu() {
    const s = this.state;
    const stores = this.stores();
    if (!s.storeId || !stores.some((x) => x.id === s.storeId)) s.storeId = stores[0]?.id;
    const menu = this.world.menus[s.category] || [];

    const gateSelect = el(
      'select',
      { id: 'gate', onchange: (e) => { s.gateId = e.target.value; s.storeId = null; this.render(); } },
      this.world.gates.map((g) => el('option', { value: g.id, selected: g.id === s.gateId }, `${g.name}${g.on_campus ? '' : ' (off-campus)'}`)),
    );
    const storeSelect = el(
      'select',
      { id: 'store', onchange: (e) => { s.storeId = e.target.value; } },
      stores.slice(0, 15).map((st) => el('option', { value: st.id, selected: st.id === s.storeId }, st.name)),
    );
    const seg = el(
      'div',
      { class: 'seg' },
      el('button', { class: s.category === 'FOOD' ? 'active' : '', onclick: () => this.switchCategory('FOOD') }, 'Food'),
      el('button', { class: s.category === 'GROCERY' ? 'active' : '', onclick: () => this.switchCategory('GROCERY') }, 'Instamart'),
    );
    const items = menu.map((item) => {
      const qty = s.cart.get(item.name)?.qty || 0;
      return el(
        'div',
        { class: 'menu-item' },
        el('div', {}, el('div', { class: 'name' }, item.name), el('div', { class: 'meta' }, `${fmtMoney(item.price)} · ${item.temp.toLowerCase()}`)),
        el(
          'div',
          { class: 'qty' },
          el('button', { 'aria-label': `remove ${item.name}`, onclick: () => this.changeQty(item, -1) }, '-'),
          el('span', {}, String(qty)),
          el('button', { 'aria-label': `add ${item.name}`, onclick: () => this.changeQty(item, 1) }, '+'),
        ),
      );
    });
    const total = [...s.cart.values()].reduce((a, i) => a + i.price * i.qty, 0);
    const count = [...s.cart.values()].reduce((a, i) => a + i.qty, 0);
    this.show(
      el('div', { class: 'app-head' }, el('span', { class: 'title' }, this.world.campus ? `Order at ${this.world.campus}` : 'Order on campus'), el('span', { class: 'badge', id: 'phone-clock' }, fmtTime(this.engine.now()))),
      el('div', { class: 'field' }, el('label', { for: 'gate' }, 'Deliver to'), gateSelect),
      seg,
      el('div', { class: 'field' }, el('label', { for: 'store' }, s.category === 'FOOD' ? 'Restaurant (nearest first)' : 'Instamart store'), storeSelect),
      el('div', {}, items),
      el(
        'div',
        { class: 'cart-bar' },
        el('button', { class: 'btn primary block', id: 'btn-continue', disabled: count === 0 || s.busy, onclick: () => this.placeOrder() },
          s.busy ? 'Placing your order…' : count ? `Continue · ${count} item(s) · ${fmtMoney(total)}` : 'Add items to continue'),
      ),
    );
  }

  switchCategory(cat) {
    this.state.category = cat;
    this.state.cart = new Map();
    this.state.storeId = null;
    this.render();
  }

  changeQty(item, delta) {
    const cart = this.state.cart;
    const qty = Math.max(0, (cart.get(item.name)?.qty || 0) + delta);
    if (qty === 0) cart.delete(item.name);
    else cart.set(item.name, { ...item, qty });
    this.render();
  }

  async placeOrder() {
    const s = this.state;
    const store = this.world.stores.find((x) => x.id === (document.getElementById('store')?.value || s.storeId));
    const gate = this.world.gates.find((g) => g.id === s.gateId);
    s.busy = true;
    this.render();
    try {
      const payload = this.engine.buildOrder({ store, gate, items: [...s.cart.values()], pastRate: 0.6 });
      const order = await this.engine.run(() => this.engine.createOrder(payload));
      s.order = { ...order, storeName: store.name, gateName: gate.name };
      s.selected = order.offers.length ? null : 'STANDARD';
      s.step = 'options';
      this.engine.log(`checkout: order ${shortId(order.id)} placed (${order.offers.length} flexible offer(s))`, 'ok');
    } catch (e) {
      toast(`Order failed: ${e.message}`, 'err');
    } finally {
      s.busy = false;
      this.render();
    }
  }

  renderOptions() {
    const s = this.state;
    const o = s.order;
    const std = el(
      'button',
      { class: `option ${s.selected === 'STANDARD' ? 'selected' : ''}`, onclick: () => { s.selected = 'STANDARD'; this.render(); } },
      el('div', {}, el('div', { class: 'o-title' }, 'Standard delivery'), el('div', { class: 'o-sub' }, `Arrives by ${fmtTime(o.standard_eta)} · ${minutesBetween(o.placed_at, o.standard_eta)} min`)),
      el('span', { class: 'badge' }, 'Fastest'),
    );
    const offers = o.offers.map((off) =>
      el(
        'button',
        { class: `option ${s.selected === off.offer_id ? 'selected' : ''}`, onclick: () => { s.selected = off.offer_id; this.render(); } },
        el('div', {}, el('div', { class: 'o-title' }, `Flexible · +${off.extra_minutes} min`), el('div', { class: 'o-sub' }, `Arrives by ${fmtTime(off.flex_eta)} at the latest`)),
        el('span', { class: 'reward' }, `Get ${fmtMoney(off.reward)}`),
      ),
    );
    const reasons = {
      no_economically_viable_offer: 'No flexible option right now: there are not enough nearby orders to combine yours with.',
      flexdrop_disabled: 'Flexible delivery is temporarily unavailable.',
      not_requested: 'Flexible delivery was not requested.',
    };
    this.show(
      el('div', { class: 'app-head' }, el('button', { class: 'back', 'aria-label': 'back', onclick: () => this.reset() }, '←'), el('span', { class: 'title' }, 'Delivery options'), el('span', {})),
      el('p', { class: 'muted small' }, `${o.storeName} → ${o.gateName}`),
      std,
      offers,
      o.offers.length
        ? el('div', { class: 'why' }, 'Why the reward? If you can wait a little, your rider may combine your order with others heading to your hostel. You get Swiggy rewards; deliveries get cheaper and greener. Your food never waits beyond safe limits.')
        : el('div', { class: 'why' }, reasons[o.offers_unavailable_reason] || 'Only standard delivery is available.'),
      el('button', { class: 'btn primary block', id: 'btn-confirm', disabled: !s.selected || s.busy, onclick: () => this.confirm() }, s.busy ? 'Confirming…' : 'Place order'),
    );
  }

  async confirm() {
    const s = this.state;
    s.busy = true;
    this.render();
    try {
      const offerId = s.selected === 'STANDARD' ? null : s.selected;
      const pref = await this.engine.run(() => this.engine.choose(s.order.id, offerId));
      const offer = s.order.offers.find((x) => x.offer_id === offerId);
      s.order = { ...s.order, ...pref, chosen: offer || null };
      this.mine.unshift(s.order);
      s.step = 'confirmed';
      this.engine.log(
        offer
          ? `checkout: student accepted +${offer.extra_minutes} min for ${fmtMoney(offer.reward)} (order ${shortId(s.order.id)})`
          : `checkout: student chose standard delivery (order ${shortId(s.order.id)})`,
        'ok',
      );
      await this.engine.run(() => this.engine.refresh());
      if (!this.engine.auto) {
        // let simulated time run so the student sees the rider assigned, pick up and deliver
        this.engine.setAuto(true);
        toast('Simulated time is running: 1 minute every 1.5 s. Use ⏸ Pause (top right) to stop it.', 'ok');
      }
    } catch (e) {
      toast(`Could not record choice: ${e.message}`, 'err');
    } finally {
      s.busy = false;
      this.render();
      this.renderTracking();
    }
  }

  renderConfirmed() {
    const o = this.state.order;
    const live = this.engine.orders.get(o.id) || o;
    const flex = live.mode === 'FLEX';
    const plan = live.plan_id ? this.engine.plans.get(live.plan_id) : null;
    const delivered = live.status === 'DELIVERED';
    this.show(
      el('div', { class: 'confirm-hero' },
        el('div', { class: 'big' }, delivered ? '✅' : '🛵'),
        el('h2', {}, delivered ? 'Delivered!' : 'Order confirmed'),
        el('p', { class: 'muted' }, flex
          ? `Arrives between ${fmtTime(live.standard_eta)} and ${fmtTime(live.latest_delivery_at)}`
          : `Arrives by ${fmtTime(live.standard_eta)}`),
        flex ? el('span', { class: 'reward' }, `${fmtMoney(live.reward)} reward · ${live.reward_status.toLowerCase()}`) : null,
      ),
      this.statusLine(live, plan),
      this.progress(live, plan),
      this.timeline(live),
      this.tripInfo(live, plan),
      this.planInfo(live),
      plan && !delivered
        ? el('button', { class: 'btn block', id: 'btn-watch', onclick: () => this.onWatch?.(plan.partner_id, plan.plan_id) }, `Watch rider ${plan.partner_id} in 3D`)
        : null,
      this.clockNote(),
      el('button', { class: 'btn block', onclick: () => this.reset() }, 'Place another order'),
    );
  }

  /** One-line, plain-language status for the student. */
  statusLine(order, plan) {
    const drop = plan?.stops.find((st) => st.order_id === order.id && st.type === 'DROP');
    const pickup = plan?.stops.find((st) => st.order_id === order.id && st.type === 'PICKUP');
    let text;
    if (order.status === 'DELIVERED') text = `Delivered${drop ? ` at ${fmtTime(drop.eta)}` : ''}. Enjoy your meal!`;
    else if (order.status === 'CANCELLED') text = 'This order was cancelled.';
    else if (order.status === 'PICKED_UP') text = `Your rider has your order and is on the way · arrives ~${fmtTime(drop?.eta || order.latest_delivery_at)}`;
    else if (plan) text = `Rider ${plan.partner_id} assigned · picking up at ~${fmtTime(pickup?.eta || plan.start_at)}, at your hostel ~${fmtTime(drop?.eta || order.latest_delivery_at)}`;
    else if (order.mode === 'FLEX') text = 'FlexDrop is looking for nearby orders to combine with yours. A rider is assigned within your approved window.';
    else text = 'The restaurant is preparing your food. A rider is assigned just in time for pickup.';
    return el('div', { class: `status-line status-${order.status}` }, text);
  }

  progress(order, plan) {
    const drop = plan?.stops.find((st) => st.order_id === order.id && st.type === 'DROP');
    const start = Date.parse(order.placed_at);
    const end = Date.parse(drop?.eta || order.latest_delivery_at);
    const frac = order.status === 'DELIVERED' ? 1 : Math.max(0.03, Math.min(0.97, (this.engine.simNow - start) / Math.max(60000, end - start)));
    const bar = el('div', { class: 'progress' }, el('span', { style: `width:${(frac * 100).toFixed(0)}%` }));
    return bar;
  }

  /** The rider's trip: every pickup and drop in order, with this student's stop marked. */
  tripInfo(order, plan) {
    if (!plan) return null;
    const name = (st) => {
      const k = `${st.location.lat.toFixed(5)},${st.location.lng.toFixed(5)}`;
      const match = (p) => `${p.lat.toFixed(5)},${p.lng.toFixed(5)}` === k;
      if (st.type === 'PICKUP') return this.world.stores.find(match)?.name || 'Restaurant';
      return this.world.gates.find(match)?.name || 'Drop point';
    };
    return el('div', { class: 'trip' },
      el('div', { class: 'trip-title' }, plan.order_ids.length > 1 ? `Shared trip · ${plan.order_ids.length} orders` : 'Trip'),
      el('ol', {}, plan.stops.map((st) => el('li', { class: st.order_id === order.id ? 'mine' : '' },
        el('span', { class: `tag ${st.type === 'PICKUP' ? 'pick' : 'drop'}` }, st.type === 'PICKUP' ? 'Pickup' : 'Drop'),
        el('span', {}, `${name(st)}${st.order_id === order.id ? ' (your order)' : ''}`),
        el('span', { class: 'muted' }, fmtTime(st.eta)),
      ))),
    );
  }

  clockNote() {
    if (this.engine.auto) return el('p', { class: 'muted small center' }, 'Simulated time runs fast: 1 minute every 1.5 seconds.');
    return el('div', { class: 'why' },
      'Simulated time is paused, so your order will not move. ',
      el('button', { class: 'linkish', onclick: () => { this.engine.setAuto(true); this.render(); } }, 'Resume time'),
    );
  }

  timeline(order) {
    const idx = STATUS_STEPS.findIndex(([k]) => k === order.status);
    return el('ul', { class: 'timeline' },
      STATUS_STEPS.map(([key, label], i) => el('li', { class: i < idx || order.status === 'DELIVERED' ? 'done' : i === idx ? 'current' : '' }, el('span', { class: 'bullet' }), label)),
      order.status === 'CANCELLED' ? el('li', { class: 'current' }, el('span', { class: 'bullet' }), 'Cancelled') : null,
    );
  }

  planInfo(order) {
    if (!order.plan_id) return null; // the status line already explains the wait
    const plan = this.engine.plans.get(order.plan_id);
    if (!plan) return null;
    const others = plan.order_ids.length - 1;
    const used = plan.flex_allocation[order.id] || 0;
    return el('div', { class: 'why' },
      `Rider ${plan.partner_id}. `,
      others > 0 ? `Your rider is also carrying ${others} other order(s) on this trip. ` : 'Your order travels on its own. ',
      order.mode === 'FLEX' ? `FlexDrop used ${used.toFixed(1)} of your ${order.max_extra_wait} flexible minutes.` : '',
    );
  }

  renderTracking() {
    if (this.state.step === 'confirmed') this.render();
    const clock = document.getElementById('phone-clock');
    if (clock) clock.textContent = fmtTime(this.engine.now());
    clear(this.box);
    if (!this.mine.length) {
      this.box.append(el('p', { class: 'muted' }, 'No orders yet.'));
      return;
    }
    for (const mineOrder of this.mine.slice(0, 6)) {
      const o = this.engine.orders.get(mineOrder.id) || mineOrder;
      this.box.append(
        el('div', { class: 'my-order' },
          el('div', { class: 'row' }, el('strong', {}, `${mineOrder.storeName} → ${mineOrder.gateName}`), el('span', { class: `badge status-${o.status}` }, o.status.replace('_', ' '))),
          el('div', { class: 'row muted' },
            el('span', {}, o.mode === 'FLEX' ? `Flexible +${o.max_extra_wait} min · ${fmtMoney(o.reward)}` : 'Standard'),
            el('span', {}, `by ${fmtTime(o.latest_delivery_at)}`)),
        ),
      );
    }
  }

  reset() {
    Object.assign(this.state, { step: 'menu', cart: new Map(), order: null, selected: null });
    this.render();
  }
}
