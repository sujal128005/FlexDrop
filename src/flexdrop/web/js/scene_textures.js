// Procedural textures and brand identity for the 3D operations view (canvas-generated, no image files).
import * as THREE from '../vendor/three/three.module.min.js';

// ------------------------------------------------------------------ math helpers
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function hashString(s) {
  let h = 2166136261;
  for (const ch of s) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;
  return h;
}

export const CUISINES = [
  [/biryani|handi|darbar|mahal|ruchulu|dhaba/i, 'Biryani · Rayalaseema spice', 'flame'],
  [/grill|peppers|royal|kebab|tandoor/i, 'Grill · North Indian', 'flame'],
  [/tiffin|idli|dosa|bhavan|bhojanam|mess|thali|meals/i, 'Tiffins · South Indian meals', 'leaf'],
  [/coffee|cafe|diner|bistro|juice/i, 'Cafe · Continental', 'cup'],
  [/patisserie|bakery|sweet|cake/i, 'Bakery · Desserts', 'star'],
  [/pizza|pasta/i, 'Pizza · Italian', 'pizza'],
  [/kitchen|family|hotel/i, 'Multi-cuisine · Family dining', 'bowl'],
];
export const SWIGGY_ORANGE = '#fc8019';
export const INSTAMART_BLUE = '#1f4fd1';
export const BRAND_COLORS = ['#e4572e', '#2a9d8f', '#e9a03b', '#8338ec', '#d62868', '#1d7fc1', '#3a9d3a', '#c0392b'];

export function brandFor(name) {
  const h = hashString(name);
  const hit = CUISINES.find(([re]) => re.test(name));
  const words = name.replace(/[^A-Za-z& ]/g, '').split(/\s+/).filter((w) => w && w !== '&');
  return {
    color: BRAND_COLORS[h % BRAND_COLORS.length],
    tagline: hit ? hit[1] : 'Multi-cuisine · Family dining',
    icon: hit ? hit[2] : 'bowl',
    initials: (words[0]?.[0] || 'F') + (words[1]?.[0] || ''),
  };
}

// ------------------------------------------------------------------ procedural textures
export function canvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return [c, c.getContext('2d')];
}

export function toTexture(c, color = true) {
  const t = new THREE.CanvasTexture(c);
  if (color) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

export function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

export function drawIcon(g, icon, cx, cy, s, color) {
  g.save();
  g.translate(cx, cy);
  g.fillStyle = color;
  g.strokeStyle = color;
  g.lineWidth = s * 0.09;
  g.lineCap = 'round';
  if (icon === 'bowl') {
    g.beginPath();
    g.arc(0, -s * 0.05, s * 0.42, 0, Math.PI);
    g.fill();
    for (const dx of [-0.18, 0, 0.18]) {
      g.beginPath();
      g.moveTo(dx * s, -s * 0.18);
      g.quadraticCurveTo(dx * s + s * 0.08, -s * 0.32, dx * s, -s * 0.45);
      g.stroke();
    }
  } else if (icon === 'cup') {
    roundRect(g, -s * 0.3, -s * 0.3, s * 0.48, s * 0.6, s * 0.08);
    g.fill();
    g.beginPath();
    g.arc(s * 0.2, 0, s * 0.15, -Math.PI / 2, Math.PI / 2);
    g.stroke();
  } else if (icon === 'pizza') {
    g.beginPath();
    g.moveTo(0, s * 0.42);
    g.lineTo(-s * 0.38, -s * 0.35);
    g.quadraticCurveTo(0, -s * 0.55, s * 0.38, -s * 0.35);
    g.closePath();
    g.fill();
  } else if (icon === 'leaf') {
    g.beginPath();
    g.moveTo(-s * 0.35, s * 0.35);
    g.quadraticCurveTo(-s * 0.4, -s * 0.4, s * 0.4, -s * 0.4);
    g.quadraticCurveTo(s * 0.35, s * 0.35, -s * 0.35, s * 0.35);
    g.fill();
  } else if (icon === 'star') {
    g.beginPath();
    for (let i = 0; i < 10; i += 1) {
      const r = i % 2 ? s * 0.18 : s * 0.42;
      const a = (i / 10) * Math.PI * 2 - Math.PI / 2;
      g.lineTo(Math.cos(a) * r, Math.sin(a) * r);
    }
    g.closePath();
    g.fill();
  } else {
    g.beginPath();
    g.moveTo(0, -s * 0.45);
    g.quadraticCurveTo(s * 0.35, -s * 0.05, s * 0.2, s * 0.35);
    g.quadraticCurveTo(0, s * 0.45, -s * 0.2, s * 0.35);
    g.quadraticCurveTo(-s * 0.35, -s * 0.05, 0, -s * 0.45);
    g.fill();
  }
  g.restore();
}

/** Round brand emblem (logo) as a data URL and a texture. */
export function emblem(brand, size = 256) {
  const [c, g] = canvas(size, size);
  g.fillStyle = brand.color;
  g.beginPath();
  g.arc(size / 2, size / 2, size / 2 - 4, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = 'rgba(255,255,255,0.9)';
  g.lineWidth = size * 0.04;
  g.beginPath();
  g.arc(size / 2, size / 2, size / 2 - size * 0.09, 0, Math.PI * 2);
  g.stroke();
  drawIcon(g, brand.icon, size / 2, size * 0.4, size * 0.42, '#ffffff');
  g.fillStyle = '#ffffff';
  g.font = `800 ${size * 0.2}px Inter, Segoe UI, Arial, sans-serif`;
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText(brand.initials.toUpperCase(), size / 2, size * 0.74);
  return { url: c.toDataURL('image/png'), texture: toTexture(c) };
}

/** Shop signboard: logo emblem, name and tagline (high resolution for close-ups). */
export function signTexture(name, brand, { bg = '#22160f', fg = '#ffe2a6' } = {}) {
  const [c, g] = canvas(1024, 256);
  const grad = g.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, bg);
  grad.addColorStop(1, '#0f0a07');
  g.fillStyle = grad;
  g.fillRect(0, 0, 1024, 256);
  g.strokeStyle = brand.color;
  g.lineWidth = 10;
  g.strokeRect(8, 8, 1008, 240);
  // emblem
  g.fillStyle = brand.color;
  g.beginPath();
  g.arc(128, 128, 96, 0, Math.PI * 2);
  g.fill();
  drawIcon(g, brand.icon, 128, 112, 110, '#ffffff');
  g.fillStyle = '#fff';
  g.font = '800 40px Inter, Segoe UI, Arial, sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText(brand.initials.toUpperCase(), 128, 182);
  // name + tagline
  g.textAlign = 'left';
  let size = 92;
  g.font = `800 ${size}px Inter, Segoe UI, Arial, sans-serif`;
  while (g.measureText(name).width > 740 && size > 40) {
    size -= 4;
    g.font = `800 ${size}px Inter, Segoe UI, Arial, sans-serif`;
  }
  g.fillStyle = fg;
  g.fillText(name, 252, 110);
  g.font = '500 40px Inter, Segoe UI, Arial, sans-serif';
  g.fillStyle = 'rgba(255,255,255,0.75)';
  g.fillText(brand.tagline, 254, 192);
  return toTexture(c);
}

export function instamartSign(location) {
  const [c, g] = canvas(1024, 256);
  g.fillStyle = INSTAMART_BLUE;
  g.fillRect(0, 0, 1024, 256);
  g.fillStyle = '#ffffff';
  roundRect(g, 26, 34, 188, 188, 36);
  g.fill();
  g.fillStyle = INSTAMART_BLUE;
  // shopping-bag emblem
  roundRect(g, 66, 96, 108, 100, 14);
  g.fill();
  g.strokeStyle = INSTAMART_BLUE;
  g.lineWidth = 12;
  g.beginPath();
  g.arc(120, 96, 30, Math.PI, 0);
  g.stroke();
  g.fillStyle = '#ffffff';
  g.font = '800 46px Inter, Segoe UI, Arial, sans-serif';
  g.textAlign = 'center';
  g.fillText('IM', 120, 162);
  g.textAlign = 'left';
  g.font = '800 104px Inter, Segoe UI, Arial, sans-serif';
  g.fillText('Instamart', 248, 128);
  const tagline = `${location} · Groceries in minutes`;
  let tsize = 42;
  g.font = `600 ${tsize}px Inter, Segoe UI, Arial, sans-serif`;
  while (g.measureText(tagline).width > 740 && tsize > 22) {
    tsize -= 2;
    g.font = `600 ${tsize}px Inter, Segoe UI, Arial, sans-serif`;
  }
  g.fillStyle = 'rgba(255,255,255,0.85)';
  g.fillText(tagline, 252, 202);
  return toTexture(c);
}

export function textTexture(text, { bg = '#1f2937', fg = '#ffffff', w = 512, h = 128, size = 64 } = {}) {
  const [c, g] = canvas(w, h);
  g.fillStyle = bg;
  g.fillRect(0, 0, w, h);
  g.fillStyle = fg;
  let fontSize = size;
  g.font = `700 ${fontSize}px Inter, Segoe UI, Arial, sans-serif`;
  while (g.measureText(text).width > w - 30 && fontSize > 16) {
    fontSize -= 4;
    g.font = `700 ${fontSize}px Inter, Segoe UI, Arial, sans-serif`;
  }
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText(text, w / 2, h / 2 + 2);
  return toTexture(c);
}

/** Facade with a window grid. Night map has warm lit windows, day map has sky-reflecting glass. */
export function facade({ base, frame, cols, rows, lit = 0.55, seed = 1, w = 512, h = 512, door = false }) {
  const rnd = mulberry32(seed);
  const [cn, gn] = canvas(w, h);
  const [cd, gd] = canvas(w, h);
  const [ce, ge] = canvas(w, h);
  for (const g of [gn, gd]) {
    g.fillStyle = base;
    g.fillRect(0, 0, w, h);
  }
  ge.fillStyle = '#000';
  ge.fillRect(0, 0, w, h);
  const cw = w / cols;
  const rh = h / rows;
  for (let r = 0; r < rows; r += 1) {
    for (const g of [gn, gd]) {
      g.fillStyle = 'rgba(0,0,0,0.12)';
      g.fillRect(0, r * rh, w, 3);
    }
    for (let col = 0; col < cols; col += 1) {
      const x = col * cw + cw * 0.18;
      const y = r * rh + rh * 0.22;
      const ww = cw * 0.64;
      const hh = rh * 0.52;
      const on = rnd() < lit;
      for (const g of [gn, gd]) {
        g.fillStyle = frame;
        g.fillRect(x - 3, y - 3, ww + 6, hh + 6);
      }
      gn.fillStyle = on ? '#ffd58a' : '#22304a';
      gn.fillRect(x, y, ww, hh);
      const glass = gd.createLinearGradient(x, y, x + ww, y + hh);
      glass.addColorStop(0, '#9cc3e6');
      glass.addColorStop(0.5, '#5f8fbf');
      glass.addColorStop(1, '#88b4dc');
      gd.fillStyle = glass;
      gd.fillRect(x, y, ww, hh);
      gd.fillStyle = 'rgba(255,255,255,0.45)';
      gd.fillRect(x, y, ww * 0.3, 3);
      if (on) {
        ge.fillStyle = rnd() < 0.3 ? '#ffe9b8' : '#ffc46b';
        ge.fillRect(x, y, ww, hh);
      }
    }
  }
  if (door) {
    for (const g of [gn, gd]) {
      g.fillStyle = '#3b2a1e';
      g.fillRect(w * 0.42, h * 0.8, w * 0.16, h * 0.2);
    }
    ge.fillStyle = '#6b4a20';
    ge.fillRect(w * 0.44, h * 0.82, w * 0.12, h * 0.18);
  }
  return { night: toTexture(cn), day: toTexture(cd), emissive: toTexture(ce) };
}

export function shopFront(base, seed) {
  const rnd = mulberry32(seed);
  const [cn, gn] = canvas(512, 256);
  const [cd, gd] = canvas(512, 256);
  const [ce, ge] = canvas(512, 256);
  for (const g of [gn, gd]) {
    g.fillStyle = base;
    g.fillRect(0, 0, 512, 256);
  }
  ge.fillStyle = '#000';
  ge.fillRect(0, 0, 512, 256);
  gn.fillStyle = '#ffdca0';
  gn.fillRect(36, 68, 300, 148);
  gd.fillStyle = '#8fb6d9';
  gd.fillRect(36, 68, 300, 148);
  for (const g of [gn, gd]) {
    g.fillStyle = 'rgba(70,45,20,0.6)';
    for (let x = 36; x <= 336; x += 100) g.fillRect(x, 68, 5, 148);
    g.fillRect(36, 68, 300, 5);
    g.fillStyle = '#5a3b22';
    g.fillRect(372, 60, 100, 196);
  }
  ge.fillStyle = rnd() < 0.5 ? '#ffcf80' : '#ffe2a8';
  ge.fillRect(36, 68, 300, 148);
  ge.fillStyle = '#7a5428';
  ge.fillRect(384, 72, 76, 184);
  return { night: toTexture(cn), day: toTexture(cd), emissive: toTexture(ce) };
}

export function stripeTexture(stripes) {
  const [c, g] = canvas(256, 64);
  const w = 256 / stripes.length;
  stripes.forEach((col, i) => {
    g.fillStyle = col;
    g.fillRect(i * w, 0, w, 64);
  });
  return toTexture(c);
}

export function roadTexture(dashed) {
  const [c, g] = canvas(128, 512);
  g.fillStyle = '#2a2e35';
  g.fillRect(0, 0, 128, 512);
  const rnd = mulberry32(dashed ? 3 : 4);
  for (let i = 0; i < 1500; i += 1) {
    g.fillStyle = `rgba(255,255,255,${rnd() * 0.06})`;
    g.fillRect(rnd() * 128, rnd() * 512, 1.5, 1.5);
  }
  g.fillStyle = '#d8d8d0';
  g.fillRect(5, 0, 4, 512);
  g.fillRect(119, 0, 4, 512);
  if (dashed) {
    g.fillStyle = '#e8c24a';
    for (let y = 16; y < 512; y += 128) g.fillRect(60, y, 8, 68);
  }
  const t = toTexture(c);
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.RepeatWrapping;
  return t;
}

export function flowTexture() {
  const [c, g] = canvas(128, 16);
  const grad = g.createLinearGradient(0, 0, 128, 0);
  grad.addColorStop(0, 'rgba(255,255,255,0.35)');
  grad.addColorStop(0.55, 'rgba(255,255,255,0.35)');
  grad.addColorStop(0.78, 'rgba(255,255,255,1)');
  grad.addColorStop(1, 'rgba(255,255,255,0.35)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 16);
  const t = toTexture(c, false);
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.RepeatWrapping;
  return t;
}

export function glowTexture(color) {
  const [c, g] = canvas(64, 64);
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, color);
  grad.addColorStop(0.4, color.replace('1)', '0.5)'));
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  return toTexture(c);
}

export function skyTexture(stops) {
  const [c, g] = canvas(16, 512);
  const grad = g.createLinearGradient(0, 0, 0, 512);
  stops.forEach(([o, col]) => grad.addColorStop(o, col));
  g.fillStyle = grad;
  g.fillRect(0, 0, 16, 512);
  return toTexture(c);
}


const FONT = 'Inter, Segoe UI, Arial, sans-serif';

function repeatTex(t, rx, ry) {
  t.wrapS = THREE.RepeatWrapping;
  t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(rx, ry);
  return t;
}

/** Red brick panel. */
export function brickTexture(base = '#a8432f', mortar = '#d9c7b5', rx = 1, ry = 1) {
  const [c, g] = canvas(256, 256);
  g.fillStyle = mortar;
  g.fillRect(0, 0, 256, 256);
  const rnd = mulberry32(17);
  for (let row = 0; row < 16; row += 1) {
    const off = row % 2 ? 16 : 0;
    for (let col = -1; col < 9; col += 1) {
      const shade = 0.85 + rnd() * 0.3;
      g.fillStyle = base;
      g.globalAlpha = 1;
      g.fillRect(col * 32 + off + 1, row * 16 + 1, 30, 14);
      g.fillStyle = `rgba(${shade > 1 ? 255 : 0},0,0,${Math.abs(1 - shade) * 0.6})`;
      g.fillRect(col * 32 + off + 1, row * 16 + 1, 30, 14);
    }
  }
  return repeatTex(toTexture(c), rx, ry);
}

/** Red clay pavers used in the campus courtyards and plazas. */
export function paverTexture(rx = 1, ry = 1) {
  const [c, g] = canvas(256, 256);
  g.fillStyle = '#8c4a3a';
  g.fillRect(0, 0, 256, 256);
  const rnd = mulberry32(23);
  for (let y = 0; y < 256; y += 16) {
    for (let x = 0; x < 256; x += 32) {
      const o = (y / 16) % 2 ? 16 : 0;
      const l = 38 + rnd() * 10;
      g.fillStyle = `hsl(10, 45%, ${l}%)`;
      g.fillRect(x + o + 1, y + 1, 30, 14);
    }
  }
  return repeatTex(toTexture(c), rx, ry);
}

/** Countryside ground: grassland with darker scrub, a few dry patches and red Rayalaseema soil. */
export function soilTexture(rx, ry) {
  const [c, g] = canvas(512, 512);
  g.fillStyle = '#6a7b42';
  g.fillRect(0, 0, 512, 512);
  const rnd = mulberry32(5);
  for (let i = 0; i < 3200; i += 1) {
    const r = 2 + rnd() * 11;
    const k = rnd();
    if (k < 0.72) g.fillStyle = `hsla(${72 + rnd() * 34}, ${30 + rnd() * 18}%, ${22 + rnd() * 16}%, 0.5)`;
    else if (k < 0.9) g.fillStyle = `hsla(${45 + rnd() * 10}, 35%, ${38 + rnd() * 10}%, 0.4)`;
    else g.fillStyle = `hsla(${16 + rnd() * 10}, 38%, ${32 + rnd() * 10}%, 0.45)`;
    g.beginPath();
    g.arc(rnd() * 512, rnd() * 512, r, 0, Math.PI * 2);
    g.fill();
  }
  for (let i = 0; i < 5000; i += 1) {
    g.fillStyle = `rgba(${rnd() < 0.5 ? '30,60,20' : '170,190,110'},${0.15 + rnd() * 0.2})`;
    g.fillRect(rnd() * 512, rnd() * 512, 1.5, 3);
  }
  return repeatTex(toTexture(c), rx, ry);
}

/** Crop rows for farm fields. */
export function fieldTexture(hue = 95, rx = 1, ry = 1) {
  const [c, g] = canvas(128, 128);
  g.fillStyle = `hsl(${hue}, 40%, 30%)`;
  g.fillRect(0, 0, 128, 128);
  for (let y = 0; y < 128; y += 8) {
    g.fillStyle = `hsl(${hue + 8}, 48%, 40%)`;
    g.fillRect(0, y, 128, 5);
  }
  return repeatTex(toTexture(c), rx, ry);
}

/** Grass with mowing stripes. */
export function lawnTexture(rx, ry) {
  const [c, g] = canvas(256, 256);
  for (let i = 0; i < 8; i += 1) {
    g.fillStyle = i % 2 ? '#4f8a3c' : '#5a9645';
    g.fillRect(0, i * 32, 256, 32);
  }
  const rnd = mulberry32(9);
  for (let i = 0; i < 900; i += 1) {
    g.fillStyle = `rgba(20,60,10,${rnd() * 0.25})`;
    g.fillRect(rnd() * 256, rnd() * 256, 2, 2);
  }
  return repeatTex(toTexture(c), rx, ry);
}

/** Grey granite with veins (fort walls, hills). */
export function stoneTexture(base = '#8d8173', rx = 1, ry = 1) {
  const [c, g] = canvas(256, 256);
  g.fillStyle = base;
  g.fillRect(0, 0, 256, 256);
  const rnd = mulberry32(31);
  for (let row = 0; row < 8; row += 1) {
    let x = row % 2 ? -20 : 0;
    while (x < 256) {
      const w = 26 + rnd() * 30;
      g.fillStyle = `rgba(${rnd() < 0.5 ? '255,240,220' : '40,30,20'},${0.06 + rnd() * 0.12})`;
      g.fillRect(x + 1, row * 32 + 1, w - 2, 30);
      g.strokeStyle = 'rgba(30,24,18,0.45)';
      g.lineWidth = 2;
      g.strokeRect(x + 1, row * 32 + 1, w - 2, 30);
      x += w;
    }
  }
  return repeatTex(toTexture(c), rx, ry);
}

/** IIITDM-style emblem (generic: gear, book and circuit motif). */
function drawInstituteEmblem(g, cx, cy, r) {
  g.save();
  g.translate(cx, cy);
  g.fillStyle = '#f4f1ea';
  g.beginPath();
  g.arc(0, 0, r, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = '#9f2b22';
  g.lineWidth = r * 0.08;
  g.beginPath();
  g.arc(0, 0, r * 0.9, 0, Math.PI * 2);
  g.stroke();
  // gear teeth
  g.fillStyle = '#1f3c78';
  for (let i = 0; i < 12; i += 1) {
    g.save();
    g.rotate((i / 12) * Math.PI * 2);
    g.fillRect(-r * 0.07, -r * 0.72, r * 0.14, r * 0.16);
    g.restore();
  }
  g.beginPath();
  g.arc(0, 0, r * 0.58, 0, Math.PI * 2);
  g.fill();
  // open book
  g.fillStyle = '#f4f1ea';
  g.beginPath();
  g.moveTo(-r * 0.38, -r * 0.12);
  g.quadraticCurveTo(-r * 0.18, -r * 0.24, 0, -r * 0.12);
  g.quadraticCurveTo(r * 0.18, -r * 0.24, r * 0.38, -r * 0.12);
  g.lineTo(r * 0.38, r * 0.24);
  g.quadraticCurveTo(r * 0.18, r * 0.12, 0, r * 0.24);
  g.quadraticCurveTo(-r * 0.18, r * 0.12, -r * 0.38, r * 0.24);
  g.closePath();
  g.fill();
  g.strokeStyle = '#1f3c78';
  g.lineWidth = r * 0.03;
  g.beginPath();
  g.moveTo(0, -r * 0.12);
  g.lineTo(0, r * 0.24);
  g.stroke();
  g.restore();
}

/** Main gate name panel: emblem plus the institute name in Telugu, Hindi and English. */
export function gatePanelTexture() {
  const [c, g] = canvas(2048, 512);
  const grad = g.createLinearGradient(0, 0, 0, 512);
  grad.addColorStop(0, '#7b7f86');
  grad.addColorStop(1, '#5d6168');
  g.fillStyle = grad;
  g.fillRect(0, 0, 2048, 512);
  g.fillStyle = 'rgba(255,255,255,0.06)';
  for (let x = 0; x < 2048; x += 128) g.fillRect(x, 0, 2, 512);
  drawInstituteEmblem(g, 1024, 112, 92);
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillStyle = '#ffffff';
  const fit = (text, weight, size, family, y, maxW = 1940) => {
    let s = size;
    g.font = `${weight} ${s}px ${family}`;
    while (g.measureText(text).width > maxW && s > 18) {
      s -= 2;
      g.font = `${weight} ${s}px ${family}`;
    }
    g.fillText(text, 1024, y);
  };
  fit('భారతీయ సమాచార సాంకేతిక పరిజ్ఞాన, ఆకృతి మరియు తయారీ సంస్థ, కర్నూలు', 700, 58,
    "'Noto Sans Telugu', 'Nirmala UI', Gautami, Vani, sans-serif", 248);
  fit('भारतीय सूचना प्रौद्योगिकी, अभिकल्पन एवं विनिर्माण संस्थान, कर्नूल', 700, 58,
    "'Noto Sans Devanagari', 'Nirmala UI', Mangal, sans-serif", 330);
  fit('INDIAN INSTITUTE OF INFORMATION TECHNOLOGY DESIGN AND MANUFACTURING KURNOOL', 800, 64, FONT, 418);
  g.font = `600 30px ${FONT}`;
  g.fillStyle = 'rgba(255,255,255,0.75)';
  g.fillText('Jagannathagattu, Kurnool, Andhra Pradesh', 1024, 482);
  return toTexture(c);
}

/** "I ♥ IIITDM KURNOOL" letters on a transparent background (stacked to look extruded). */
export function loveSignTexture() {
  const [c, g] = canvas(2048, 320);
  g.textBaseline = 'middle';
  g.font = `900 220px ${FONT}`;
  const parts = [['I', '#ffffff'], [' ', null], ['♥', '#e0262b'], [' ', null], ['IIITDM KURNOOL', '#ffffff']];
  const total = parts.reduce((w, [t]) => w + g.measureText(t).width, 0);
  let x = (2048 - total) / 2;
  for (const [t, col] of parts) {
    if (col) {
      g.fillStyle = col;
      g.fillText(t, x, 170);
    }
    x += g.measureText(t).width;
  }
  return toTexture(c);
}

/** Clock face. */
export function clockTexture() {
  const [c, g] = canvas(256, 256);
  g.fillStyle = '#fbf6e8';
  g.beginPath();
  g.arc(128, 128, 124, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = '#2b2b2b';
  g.lineWidth = 8;
  g.stroke();
  for (let i = 0; i < 12; i += 1) {
    const a = (i / 12) * Math.PI * 2;
    g.lineWidth = i % 3 ? 4 : 9;
    g.beginPath();
    g.moveTo(128 + Math.sin(a) * 96, 128 - Math.cos(a) * 96);
    g.lineTo(128 + Math.sin(a) * 114, 128 - Math.cos(a) * 114);
    g.stroke();
  }
  g.lineCap = 'round';
  g.lineWidth = 10;
  g.beginPath();
  g.moveTo(128, 128);
  g.lineTo(128 + 52, 128 - 30);
  g.stroke();
  g.lineWidth = 6;
  g.beginPath();
  g.moveTo(128, 128);
  g.lineTo(128 - 10, 128 - 92);
  g.stroke();
  return toTexture(c);
}

/** Indian national flag. */
export function flagTexture() {
  const [c, g] = canvas(300, 200);
  g.fillStyle = '#ff9933';
  g.fillRect(0, 0, 300, 67);
  g.fillStyle = '#ffffff';
  g.fillRect(0, 67, 300, 66);
  g.fillStyle = '#138808';
  g.fillRect(0, 133, 300, 67);
  g.strokeStyle = '#000080';
  g.lineWidth = 3;
  g.beginPath();
  g.arc(150, 100, 26, 0, Math.PI * 2);
  g.stroke();
  for (let i = 0; i < 24; i += 1) {
    const a = (i / 24) * Math.PI * 2;
    g.beginPath();
    g.moveTo(150, 100);
    g.lineTo(150 + Math.cos(a) * 26, 100 + Math.sin(a) * 26);
    g.lineWidth = 1.2;
    g.stroke();
  }
  return toTexture(c);
}

/** Delivery bag print: brand word on the brand colour with reflective edging. */
export function bagTexture(word, bg) {
  const [c, g] = canvas(256, 256);
  g.fillStyle = bg;
  g.fillRect(0, 0, 256, 256);
  g.fillStyle = '#e8edf2';
  g.fillRect(0, 0, 256, 14);
  g.fillRect(0, 242, 256, 14);
  g.fillStyle = '#ffffff';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  let s = 72;
  g.font = `900 ${s}px ${FONT}`;
  while (g.measureText(word).width > 230 && s > 20) {
    s -= 2;
    g.font = `900 ${s}px ${FONT}`;
  }
  g.fillText(word, 128, 128);
  return toTexture(c);
}

/** Uniform shirt front: brand colour, reflective stripes and the brand word on the chest. */
export function shirtTexture(word, bg, band) {
  const [c, g] = canvas(256, 256);
  g.fillStyle = bg;
  g.fillRect(0, 0, 256, 256);
  if (band) {
    g.fillStyle = band;
    g.fillRect(0, 70, 256, 70);
  }
  g.fillStyle = '#dfe6ee';
  g.fillRect(0, 168, 256, 14);
  g.fillRect(0, 200, 256, 14);
  g.fillStyle = '#ffffff';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  let s = 56;
  g.font = `900 ${s}px ${FONT}`;
  while (g.measureText(word).width > 220 && s > 18) {
    s -= 2;
    g.font = `900 ${s}px ${FONT}`;
  }
  g.fillText(word, 128, 106);
  return toTexture(c);
}

/** Water with soft caustics. */
export function waterTexture(rx = 1, ry = 1, deep = '#1f6f96', light = '#5fb6d9') {
  const [c, g] = canvas(256, 256);
  g.fillStyle = deep;
  g.fillRect(0, 0, 256, 256);
  const rnd = mulberry32(13);
  g.strokeStyle = light;
  g.globalAlpha = 0.35;
  for (let i = 0; i < 70; i += 1) {
    g.lineWidth = 1 + rnd() * 2;
    g.beginPath();
    const x = rnd() * 256;
    const y = rnd() * 256;
    g.moveTo(x, y);
    g.quadraticCurveTo(x + 12, y - 8, x + 26, y);
    g.stroke();
  }
  return repeatTex(toTexture(c), rx, ry);
}

/** Red and white stripes for the boom barrier. */
export function barrierTexture() {
  const [c, g] = canvas(256, 32);
  for (let i = 0; i < 8; i += 1) {
    g.fillStyle = i % 2 ? '#ffffff' : '#d32f2f';
    g.fillRect(i * 32, 0, 32, 32);
  }
  return toTexture(c);
}
