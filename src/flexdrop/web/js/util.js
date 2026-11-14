// Formatting, DOM and random helpers (no innerHTML with untrusted data: use el()/text()).

const IST = 'Asia/Kolkata';

export function fmtTime(value) {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '--:--';
  return d.toLocaleTimeString('en-IN', { timeZone: IST, hour: '2-digit', minute: '2-digit', hour12: false });
}

/** Savings against solo delivery, worded for people: "saving ₹40 vs solo" or "₹12 more than solo". */
export function fmtSaving(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return 'saving n/a';
  const r = Math.round(v);
  if (r > 0) return `saving ${fmtMoney(r)} vs solo`;
  if (r === 0) return 'same cost as solo';
  return `${fmtMoney(-r)} more than solo`;
}

export function fmtMoney(v, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return 'n/a';
  return '₹' + Number(v).toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return 'n/a';
  return (v * 100).toFixed(digits) + '%';
}

export function fmtNum(v, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return 'n/a';
  return Number(v).toFixed(digits);
}

export function minutesBetween(a, b) {
  return Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60000);
}

/** Create an element: el('div', {class: 'x', onclick: fn}, child1, 'text', ...) */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function toast(message, kind = '') {
  const box = document.getElementById('toasts');
  const t = el('div', { class: `toast ${kind}` }, message);
  box.append(t);
  setTimeout(() => t.remove(), 4200);
}

/** Readable short id: first block of a UUID, or the tail of an order id like "FD-k3x-17". */
export function shortId(id) {
  if (!id) return '';
  const s = String(id);
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(s)) return s.slice(0, 8);
  return s.startsWith('FD-') ? s.slice(3) : s.slice(-10);
}

export function rand(min, max) {
  return min + Math.random() * (max - min);
}

export function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

export function poisson(lambda) {
  const L = Math.exp(-lambda);
  let k = 0;
  let p = 1;
  do { k += 1; p *= Math.random(); } while (p > L);
  return k - 1;
}

export function haversineKm(a, b) {
  const R = 6371.0088;
  const toRad = (x) => (x * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

const PALETTE = ['#16a34a', '#2563eb', '#7c3aed', '#db2777', '#0891b2', '#ca8a04', '#ea580c', '#4f46e5', '#059669', '#9333ea'];
export function colorFor(id) {
  let h = 0;
  for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export const POLICY_LABELS = {
  independent: 'Independent dispatch',
  batching: 'Conventional batching',
  flexdrop_noflex: 'Optimiser only (no flexibility)',
  flexdrop: 'FlexDrop (Level 1)',
  flexdrop_l3: 'FlexDrop Level 3',
};
