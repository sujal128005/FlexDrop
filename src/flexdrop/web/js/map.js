// Campus map: stores, hostel gates, riders and live delivery routes (Leaflet, bundled locally).

import { colorFor, el, shortId } from './util.js';

export class CampusMap {
  constructor(container, engine) {
    this.engine = engine;
    this.container = container;
    this.ready = typeof window.L !== 'undefined';
    if (!this.ready) {
      container.append(el('p', { class: 'muted', style: 'padding:16px' }, 'Map library failed to load.'));
      return;
    }
    const L = window.L;
    const world = engine.world;
    const center = world.center || averageCenter(world.stores);
    this.map = L.map(container, { zoomControl: true, preferCanvas: true }).setView([center.lat, center.lng], 14);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(this.map);
    this.routes = L.layerGroup().addTo(this.map);
    this.riders = L.layerGroup().addTo(this.map);
    this.pending = L.layerGroup().addTo(this.map);
    const statics = L.layerGroup().addTo(this.map);
    for (const s of world.stores) {
      L.circleMarker([s.lat, s.lng], {
        radius: 5,
        color: '#fff',
        weight: 1,
        fillColor: s.category === 'FOOD' ? '#fc8019' : '#0d9488',
        fillOpacity: 0.95,
      })
        .bindTooltip(`${s.name} (${s.category === 'FOOD' ? 'restaurant' : 'Instamart'})`)
        .addTo(statics);
    }
    for (const g of world.gates || []) {
      L.circleMarker([g.lat, g.lng], { radius: 7, color: '#fff', weight: 2, fillColor: '#2563eb', fillOpacity: 1 })
        .bindTooltip(g.id, { permanent: true, direction: 'top', className: 'gate-label', offset: [0, -6] })
        .addTo(statics);
    }
    this.bounds = [...world.stores, ...(world.gates || [])].map((p) => [p.lat, p.lng]);
    this.fitted = false;
  }

  /** Call when the map becomes visible: Leaflet cannot measure a hidden container. */
  invalidate() {
    if (!this.ready) return;
    setTimeout(() => {
      this.map.invalidateSize();
      if (!this.fitted && this.bounds.length && this.container.offsetWidth > 0) {
        this.map.fitBounds(this.bounds, { padding: [24, 24] });
        this.fitted = true;
      }
    }, 60);
  }

  render() {
    if (!this.ready) return;
    const L = window.L;
    const engine = this.engine;
    this.routes.clearLayers();
    this.riders.clearLayers();
    this.pending.clearLayers();

    const busy = new Set();
    for (const plan of engine.activePlans()) {
      busy.add(plan.partner_id);
      const color = colorFor(plan.plan_id);
      const grouped = plan.order_ids.length > 1;
      const start = engine.partnerStart.get(plan.plan_id) || engine.partners.get(plan.partner_id)?.location;
      const path = [];
      if (start) path.push([start.lat, start.lng]);
      for (const s of plan.stops) path.push([s.location.lat, s.location.lng]);
      L.polyline(path, {
        color,
        weight: grouped ? 5 : 2.5,
        opacity: grouped ? 0.9 : 0.6,
        dashArray: plan.status === 'PROPOSED' ? '6 6' : null,
      })
        .bindTooltip(`Plan ${shortId(plan.plan_id)} · ${plan.order_ids.length} order(s) · saving ₹${plan.expected_savings.toFixed(0)}`)
        .addTo(this.routes);
      for (const s of plan.stops) {
        L.circleMarker([s.location.lat, s.location.lng], {
          radius: s.type === 'PICKUP' ? 3 : 4,
          color,
          weight: 2,
          fillColor: '#fff',
          fillOpacity: 1,
        }).addTo(this.routes);
      }
      const pos = engine.riderPosition(plan);
      if (pos) {
        L.circleMarker([pos.lat, pos.lng], { radius: 7, color: '#fff', weight: 2, fillColor: '#7c3aed', fillOpacity: 1 })
          .bindTooltip(`Rider ${plan.partner_id} · ${plan.order_ids.length} order(s)`)
          .addTo(this.riders);
      }
    }
    for (const p of engine.partners.values()) {
      if (busy.has(p.id) || p.status === 'OFFLINE') continue;
      L.circleMarker([p.location.lat, p.location.lng], {
        radius: 4,
        color: '#fff',
        weight: 1,
        fillColor: '#9aa1ad',
        fillOpacity: 0.9,
      })
        .bindTooltip(`Rider ${p.id} (idle)`)
        .addTo(this.riders);
    }
    // pending orders waiting at stores (held for coordination or not yet ready)
    const waiting = new Map();
    for (const o of engine.orders.values()) {
      if (o.status !== 'PENDING') continue;
      const store = engine.world.stores.find((s) => s.id === o.store_id);
      if (!store) continue;
      waiting.set(store.id, { store, n: (waiting.get(store.id)?.n || 0) + 1, flex: (waiting.get(store.id)?.flex || 0) + (o.mode === 'FLEX' ? 1 : 0) });
    }
    for (const { store, n, flex } of waiting.values()) {
      L.circleMarker([store.lat, store.lng], {
        radius: 8 + 2 * Math.min(n, 6),
        color: '#f59e0b',
        weight: 2,
        fill: false,
        dashArray: '3 3',
      })
        .bindTooltip(`${store.name}: ${n} waiting (${flex} flexible)`)
        .addTo(this.pending);
    }
  }
}

function averageCenter(points) {
  if (!points.length) return { lat: 12.9716, lng: 77.5946 };
  const lat = points.reduce((s, p) => s + p.lat, 0) / points.length;
  const lng = points.reduce((s, p) => s + p.lng, 0) / points.length;
  return { lat, lng };
}
