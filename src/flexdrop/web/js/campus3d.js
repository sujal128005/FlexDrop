// 3D operations view of IIITDM Kurnool and Kurnool city (three.js, bundled locally).
//
// The layout comes from the Studio site plan (`world.site_plan`, built by flexdrop/studio.py):
//   * NH 40 runs north to Kurnool city; the city is a street grid with restaurants and an
//     Instamart store on building plots, Konda Reddy Fort and the Tungabhadra river;
//   * the campus has ONE entrance gate facing the highway. Inside: the red clock tower on the
//     roundabout, the "I love IIITDM Kurnool" sign, the academic area (white blocks with red
//     fins, red brick panels, a skywalk on red columns, a pool and red pavers) and the hostel
//     wings behind it, with the Jagannathagattu hills and temple beyond the wall;
//   * every road is part of one graph, so riders only drive on roads (Dijkstra shortest paths)
//     and every trip to a hostel passes through the main gate, where the rider's Swiggy or
//     Instamart uniform is checked;
//   * ambient traffic (cars, autos, buses, trucks, bikes) keeps to the outer left lane and
//     yields, so it never drives through riders or other vehicles.
// 1 scene unit = 8 m. Vehicles and buildings are drawn larger than life for readability.

import * as THREE from '../vendor/three/three.module.min.js';
import { CSS2DObject, CSS2DRenderer } from '../vendor/three/CSS2DRenderer.js';
import { OrbitControls } from '../vendor/three/OrbitControls.js';
import { colorFor, fmtMoney, fmtSaving, shortId } from './util.js';
import {
  INSTAMART_BLUE, SWIGGY_ORANGE, bagTexture, barrierTexture, brandFor, brickTexture, clockTexture,
  emblem, facade, flagTexture, flowTexture, gatePanelTexture, glowTexture, hashString, instamartSign,
  lawnTexture, loveSignTexture, mulberry32, paverTexture, roadTexture, shirtTexture, shopFront,
  signTexture, skyTexture, soilTexture, fieldTexture, stoneTexture, stripeTexture, textTexture, waterTexture,
} from './scene_textures.js';

const UNIT_M = 8;
const M_PER_DEG = 111195;
const LANES = [-1.8, -0.9, 0, 0.9, 1.8]; // rider lanes (inner part of every road)
const ZOOM_LABEL_DISTANCE = 150;
const RIDER_BADGE_DISTANCE = 170;
const RIDER_GAP = 3.2; // minimum distance between two riders (scene units)

export function webglAvailable() {
  try {
    const c = document.createElement('canvas');
    return Boolean(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch {
    return false;
  }
}

// ------------------------------------------------------------------ geometry helpers
const V = (x, z) => new THREE.Vector3(x, 0, z);
const P3 = (x, y, z) => new THREE.Vector3(x, y, z);
const UP = new THREE.Vector3(0, 1, 0);

/** Rounded limb (capsule) from point a to point b. */
function limb(a, b, r, mat) {
  const dir = b.clone().sub(a);
  const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, Math.max(0.01, dir.length()), 2, 7), mat);
  m.position.copy(a).add(b).multiplyScalar(0.5);
  m.quaternion.setFromUnitVectors(UP, dir.normalize());
  return m;
}

/** Distance from p to segment ab in the ground plane; returns {d, t, q}. */
function segDist(p, a, b) {
  const abx = b.x - a.x;
  const abz = b.z - a.z;
  const len2 = abx * abx + abz * abz;
  let t = len2 ? ((p.x - a.x) * abx + (p.z - a.z) * abz) / len2 : 0;
  t = Math.max(0, Math.min(1, t));
  const q = V(a.x + abx * t, a.z + abz * t);
  return { d: Math.hypot(p.x - q.x, p.z - q.z), t, q };
}

/** Intersection of segments ab and cd (ground plane); returns {t, u, p} or null. */
function segIntersect(a, b, c, d) {
  const rx = b.x - a.x;
  const rz = b.z - a.z;
  const sx = d.x - c.x;
  const sz = d.z - c.z;
  const den = rx * sz - rz * sx;
  if (Math.abs(den) < 1e-9) return null;
  const t = ((c.x - a.x) * sz - (c.z - a.z) * sx) / den;
  const u = ((c.x - a.x) * rz - (c.z - a.z) * rx) / den;
  if (t < -1e-6 || t > 1 + 1e-6 || u < -1e-6 || u > 1 + 1e-6) return null;
  return { t, u, p: V(a.x + rx * t, a.z + rz * t) };
}

/** Offset a ground polyline sideways by d (positive = right-hand side of travel). */
function offsetLine(pts, d, closed = false) {
  if (!d || pts.length < 2) return pts.map((p) => p.clone());
  const n = pts.length;
  return pts.map((p, i) => {
    const a = closed ? pts[(i - 1 + n) % n] : pts[Math.max(0, i - 1)];
    const b = closed ? pts[(i + 1) % n] : pts[Math.min(n - 1, i + 1)];
    let dx = b.x - a.x;
    let dz = b.z - a.z;
    const l = Math.hypot(dx, dz) || 1;
    dx /= l;
    dz /= l;
    return V(p.x - dz * d, p.z + dx * d);
  });
}

function polyLength(pts) {
  let s = 0;
  for (let i = 1; i < pts.length; i += 1) s += pts[i - 1].distanceTo(pts[i]);
  return s;
}

function pointAlong(pts, f) {
  if (pts.length === 1) return pts[0].clone();
  const target = polyLength(pts) * Math.max(0, Math.min(1, f));
  let acc = 0;
  for (let i = 1; i < pts.length; i += 1) {
    const seg = pts[i - 1].distanceTo(pts[i]);
    if (acc + seg >= target) return pts[i - 1].clone().lerp(pts[i], seg ? (target - acc) / seg : 0);
    acc += seg;
  }
  return pts[pts.length - 1].clone();
}

/** Flat ribbon along a polyline (UV v runs along the length). */
function ribbon(pts, width, closed = false) {
  const P = closed ? [...pts, pts[0]] : pts;
  const pos = [];
  const uv = [];
  const idx = [];
  let along = 0;
  for (let i = 0; i < P.length; i += 1) {
    const a = closed && i === 0 ? P[P.length - 2] : P[Math.max(0, i - 1)];
    const b = closed && i === P.length - 1 ? P[1] : P[Math.min(P.length - 1, i + 1)];
    let dx = b.x - a.x;
    let dz = b.z - a.z;
    const l = Math.hypot(dx, dz) || 1;
    dx /= l;
    dz /= l;
    if (i > 0) along += P[i - 1].distanceTo(P[i]);
    const nx = -dz * (width / 2);
    const nz = dx * (width / 2);
    pos.push(P[i].x + nx, 0, P[i].z + nz, P[i].x - nx, 0, P[i].z - nz);
    uv.push(0, along / 40, 1, along / 40);
    if (i > 0) {
      const k = i * 2;
      idx.push(k - 2, k - 1, k, k - 1, k + 1, k);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

/** Insert points so no segment is longer than `step` (lets flat things follow the terrain). */
function densify(pts, step, closed = false) {
  const P = closed ? [...pts, pts[0]] : pts;
  const out = [P[0].clone()];
  for (let i = 1; i < P.length; i += 1) {
    const n = Math.max(1, Math.ceil(P[i - 1].distanceTo(P[i]) / step));
    for (let k = 1; k <= n; k += 1) out.push(P[i - 1].clone().lerp(P[i], k / n));
  }
  if (closed) out.pop();
  return out;
}

/** Ground polyline with cumulative lengths, for moving vehicles along it. */
function track(pts, closed) {
  const P = closed ? [...pts, pts[0]] : pts;
  const cum = [0];
  for (let i = 1; i < P.length; i += 1) cum.push(cum[i - 1] + P[i - 1].distanceTo(P[i]));
  return { pts: P, cum, total: cum[cum.length - 1], closed };
}

function sampleTrack(tr, s, out, dir) {
  let x = s;
  if (tr.closed) x = ((x % tr.total) + tr.total) % tr.total;
  else x = Math.max(0, Math.min(tr.total, x));
  let lo = 0;
  let hi = tr.cum.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (tr.cum[mid] <= x) lo = mid;
    else hi = mid;
  }
  const a = tr.pts[lo];
  const b = tr.pts[hi];
  const seg = tr.cum[hi] - tr.cum[lo] || 1;
  const f = (x - tr.cum[lo]) / seg;
  out.set(a.x + (b.x - a.x) * f, 0, a.z + (b.z - a.z) * f);
  dir.set(b.x - a.x, 0, b.z - a.z).normalize();
}

// ------------------------------------------------------------------ road graph
class MinHeap {
  constructor() { this.a = []; }

  get size() { return this.a.length; }

  push(item) {
    const a = this.a;
    a.push(item);
    let i = a.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (a[p][0] <= a[i][0]) break;
      [a[p], a[i]] = [a[i], a[p]];
      i = p;
    }
  }

  pop() {
    const a = this.a;
    const top = a[0];
    const last = a.pop();
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < a.length && a[l][0] < a[m][0]) m = l;
        if (r < a.length && a[r][0] < a[m][0]) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i], a[m]];
        i = m;
      }
    }
    return top;
  }
}

class RoadGraph {
  constructor() {
    this.pts = [];
    this.adj = [];
    this.edges = [];
    this.byKey = new Map();
  }

  /** Node at p; points closer than 5 cm share a node (that is how roads join). */
  node(p) {
    const k = `${Math.round(p.x * 20)},${Math.round(p.z * 20)}`;
    if (this.byKey.has(k)) return this.byKey.get(k);
    this.pts.push(V(p.x, p.z));
    this.adj.push([]);
    const id = this.pts.length - 1;
    this.byKey.set(k, id);
    return id;
  }

  edge(a, b, kind = 'road') {
    if (a === b || this.adj[a].some(([v]) => v === b)) return;
    const w = this.pts[a].distanceTo(this.pts[b]);
    this.adj[a].push([b, w]);
    this.adj[b].push([a, w]);
    this.edges.push([a, b, kind]);
  }

  snap(p) {
    let best = null;
    for (const [a, b, kind] of this.edges) {
      if (kind !== 'road') continue;
      const r = segDist(p, this.pts[a], this.pts[b]);
      if (!best || r.d < best.d) best = { ...r, a, b };
    }
    return best;
  }

  /** Shortest path between anchors ({node} or {point}); returns a ground polyline. */
  route(from, to) {
    const ends = (anchor) => {
      if (anchor.node !== undefined) return { costs: new Map([[anchor.node, 0]]), point: null, edge: null };
      const s = this.snap(anchor.point);
      return {
        costs: new Map([[s.a, s.q.distanceTo(this.pts[s.a])], [s.b, s.q.distanceTo(this.pts[s.b])]]),
        point: s.q,
        edge: [s.a, s.b],
      };
    };
    const S = ends(from);
    const T = ends(to);
    const dist = new Array(this.pts.length).fill(Infinity);
    const prev = new Array(this.pts.length).fill(-1);
    const heap = new MinHeap();
    for (const [n, c] of S.costs) {
      dist[n] = c;
      heap.push([c, n]);
    }
    while (heap.size) {
      const [d, u] = heap.pop();
      if (d > dist[u]) continue;
      for (const [v, w] of this.adj[u]) {
        if (d + w < dist[v]) {
          dist[v] = d + w;
          prev[v] = u;
          heap.push([dist[v], v]);
        }
      }
    }
    let bestNode = -1;
    let bestCost = Infinity;
    for (const [n, c] of T.costs) {
      if (dist[n] + c < bestCost) {
        bestCost = dist[n] + c;
        bestNode = n;
      }
    }
    if (S.edge && T.edge && S.edge[0] === T.edge[0] && S.edge[1] === T.edge[1]) {
      if (S.point.distanceTo(T.point) <= bestCost) return [S.point.clone(), T.point.clone()];
    }
    const nodes = [];
    for (let n = bestNode; n !== -1; n = prev[n]) nodes.push(n);
    nodes.reverse();
    const out = [];
    if (S.point) out.push(S.point.clone());
    for (const n of nodes) out.push(this.pts[n].clone());
    if (T.point) out.push(T.point.clone());
    return out.filter((p, i) => i === 0 || p.distanceTo(out[i - 1]) > 0.01);
  }
}

function shadowed(obj, cast = true) {
  obj.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = cast;
      o.receiveShadow = true;
    }
  });
  return obj;
}

function box(w, h, d, mat, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y, z);
  return m;
}

function cyl(rt, rb, h, mat, x = 0, y = 0, z = 0, seg = 16) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), mat);
  m.position.set(x, y, z);
  return m;
}

// Local footprints [x0, x1, z0, z1] (x across, z towards the front) used for layout checks.
const FOOTPRINT = {
  food: [-9.7, 9.7, -7, 11.6],
  grocery: [-15, 15, -10.5, 11.6],
  hostel: [-12, 12, -5.5, 8.4],
  pg: [-6, 6, -6, 7.2],
};

const NIGHT = {
  sky: [[0, '#0b1222'], [0.45, '#22335a'], [0.75, '#5b4a72'], [1, '#f2a46b']],
  fog: 0x2b3558,
  fogRange: [700, 2000],
  hemi: [0xa9bfff, 0x3a2f25, 0.5],
  ambient: 0.1,
  sun: [0xffc89a, 1.6, [-420, 300, 260]],
  exposure: 1.08,
  emissive: 1,
  lamps: 1,
};
const DAY = {
  sky: [[0, '#4f8fd8'], [0.55, '#9cc6f0'], [1, '#e6f1fb']],
  fog: 0xc9d8e6,
  fogRange: [800, 2400],
  hemi: [0xdfeaff, 0x6b5a45, 0.55],
  ambient: 0.12,
  sun: [0xfff4e0, 2.7, [-300, 520, 200]],
  exposure: 1.0,
  emissive: 0.08,
  lamps: 0,
};

// ------------------------------------------------------------------ scene
export class Campus3D {
  constructor(container, engine) {
    this.container = container;
    this.engine = engine;
    this.world = engine.world;
    this.plan = this.world.site_plan;
    this.center = this.world.center;
    this.kx = (M_PER_DEG * Math.cos((this.center.lat * Math.PI) / 180)) / UNIT_M;
    this.kz = M_PER_DEG / UNIT_M;
    this.pickables = [];
    this.riders = new Map();
    this.routeViews = [];
    this.beacons = [];
    this.effects = [];
    this.anchors = new Map();
    this.pathCache = new Map();
    this.zoomLabels = [];
    this.lit = []; // [material, nightIntensity] for windows, signs, bulbs
    this.facadeMats = []; // [material, dayMap, nightMap]
    this.glows = []; // sprites only visible at night
    this.obstacles = []; // {p, r, what}: static things that decor and vehicles must avoid
    this.landmarks = [];
    this.traffic = [];
    this.deliveredSeen = null;
    this.autoRotate = false;
    this.highlight = null;
    this.displayTime = engine.simNow;
    this.mode = 'night';
    this.verifications = 0;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.shadowMap.autoUpdate = false; // static shadows: re-rendered only when lighting changes
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.domElement.className = 'scene-canvas';
    container.append(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = 'scene-labels';
    container.append(this.labels.domElement);
    this.tooltip = document.createElement('div');
    this.tooltip.className = 'scene-tooltip hidden';
    container.append(this.tooltip);

    this.scene = new THREE.Scene();
    this.skies = { night: skyTexture(NIGHT.sky), day: skyTexture(DAY.sky) };
    this.scene.fog = new THREE.Fog(NIGHT.fog, ...NIGHT.fogRange);
    this.camera = new THREE.PerspectiveCamera(40, 1, 0.5, 8000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.47;
    this.controls.minDistance = 14;
    this.controls.maxDistance = 1800;
    this.controls.autoRotateSpeed = 0.5;
    this.controls.zoomToCursor = true; // scroll zooms towards the point under the mouse, like a map
    this.controls.zoomSpeed = 1.1;
    this.warmGlow = new THREE.SpriteMaterial({ map: glowTexture('rgba(255,190,110,1)'), blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.5 });
    this.lampGlow = new THREE.SpriteMaterial({ map: glowTexture('rgba(255,214,140,1)'), blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.55 });
    this.menuTex = textTexture('MENU', { bg: '#1f1a17', fg: '#ffe2a6', w: 128, h: 128, size: 34 });

    this.building = true;
    this.layoutWorld();
    this.buildLights();
    this.buildGround();
    this.buildRoads();
    this.buildCampus();
    this.buildHostels();
    this.buildStores();
    this.buildCity();
    this.buildPGsAndVillage();
    this.buildHills();
    this.buildLamps();
    this.buildTrees();
    this.buildGreenery();
    this.buildTraffic();
    this.applyTerrain();
    this.buildEnvironment();
    this.mergeStatic();
    this.building = false;
    this.routesGroup = new THREE.Group();
    this.ridersGroup = new THREE.Group();
    this.fxGroup = new THREE.Group();
    this.scene.add(this.routesGroup, this.ridersGroup, this.fxGroup);
    this.flowTex = flowTexture();
    this.resetView();
    const hour = Number(new Date(engine.simNow).toLocaleString('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', hour12: false }));
    this.setTimeOfDay(hour >= 7 && hour < 18 ? 'day' : 'night');

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.renderer.domElement.addEventListener('pointermove', (e) => this.onPointer(e));
    this.followBanner = document.createElement('div');
    this.followBanner.className = 'scene-follow hidden';
    const followText = document.createElement('span');
    const stopBtn = document.createElement('button');
    stopBtn.type = 'button';
    stopBtn.textContent = 'Stop following';
    stopBtn.addEventListener('click', () => this.follow(null));
    this.followBanner.append(followText, stopBtn);
    this.followText = followText;
    container.append(this.followBanner);
    // dragging the view ends follow mode (scrolling to zoom keeps following)
    this.renderer.domElement.addEventListener('pointerdown', () => { if (this.followId) this.follow(null); });
    this.renderer.domElement.addEventListener('pointerleave', () => {
      this.tooltip.classList.add('hidden');
      this.highlight = null;
    });
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.clock = new THREE.Clock();
    this.nextFrameAt = 0;
    this.slowFrames = 0;
    this.setQuality(this.initialQuality());
    this.renderer.setAnimationLoop(() => this.frame());
  }

  /**
   * Quality: 'high' (shadows, sky lighting, grass) or 'lite'. `?quality=high|lite` in the URL
   * forces one; otherwise software renderers start in lite and slow machines switch to it.
   */
  initialQuality() {
    const forced = new URLSearchParams(window.location.search).get('quality');
    if (forced === 'high' || forced === 'lite') {
      this.qualityLocked = true;
      return forced;
    }
    try {
      const gl = this.renderer.getContext();
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      const name = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      if (/swiftshader|llvmpipe|software|basic render/i.test(String(name))) return 'lite';
    } catch {
      /* renderer name unavailable: assume a real GPU */
    }
    return 'high';
  }

  setQuality(q) {
    if (this.quality === q) return;
    this.quality = q;
    const high = q === 'high';
    this.renderer.shadowMap.enabled = high;
    this.renderer.setPixelRatio(high ? Math.min(window.devicePixelRatio || 1, 1.5) : 1);
    for (const o of this.detail || []) o.visible = high;
    this.scene.traverse((o) => {
      if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { m.needsUpdate = true; });
    });
    this.setTimeOfDay(this.mode);
    this.renderer.shadowMap.needsUpdate = true;
    this.resize();
  }

  // ------------------------------------------------------------------ helpers
  project(lat, lng) {
    return V((lng - this.center.lng) * this.kx, -(lat - this.center.lat) * this.kz);
  }

  key(lat, lng) {
    return `${lat.toFixed(5)},${lng.toFixed(5)}`;
  }

  anchorFor(loc) {
    const a = this.anchors.get(this.key(loc.lat, loc.lng));
    return a ? { node: a.node } : { point: this.project(loc.lat, loc.lng) };
  }

  label(text, kind, y, parent) {
    const div = document.createElement('div');
    div.className = `scene-label ${kind}`;
    div.textContent = text;
    const obj = new CSS2DObject(div);
    obj.position.set(0, y, 0);
    parent.add(obj);
    return obj;
  }

  /** Free-standing label at a ground position. */
  placeLabel(text, kind, pos, y) {
    const holder = new THREE.Object3D();
    holder.position.copy(pos);
    this.scene.add(holder);
    return this.label(text, kind, y, holder);
  }

  /** Name tag with logo shown only when the camera is close to the building. */
  zoomTag(parent, worldPos, y, title, subtitle, logoUrl) {
    const div = document.createElement('div');
    div.className = 'scene-tag';
    if (logoUrl) {
      const img = document.createElement('img');
      img.src = logoUrl;
      img.alt = '';
      div.append(img);
    }
    const text = document.createElement('div');
    const t = document.createElement('strong');
    t.textContent = title;
    const s = document.createElement('span');
    s.textContent = subtitle;
    text.append(t, s);
    div.append(text);
    const obj = new CSS2DObject(div);
    obj.position.set(0, y, 0);
    obj.visible = false;
    parent.add(obj);
    this.zoomLabels.push({ obj, pos: worldPos.clone() });
    return obj;
  }

  /** Shared plain material (same parameters, same material), so merged meshes batch well. */
  std(params) {
    this.matCache = this.matCache || new Map();
    const key = JSON.stringify(params, (k, val) => (val && val.isColor ? val.getHex() : val));
    if (!this.matCache.has(key)) this.matCache.set(key, new THREE.MeshStandardMaterial(params));
    return this.matCache.get(key);
  }

  /**
   * Merge a model's meshes into as few draw calls as possible (local space). Plain coloured parts
   * become one mesh with vertex colours; textured or glowing parts are merged per material;
   * `keep` meshes stay separate (they change at run time).
   */
  mergeLocal(group, keep = new Set()) {
    group.updateMatrixWorld(true);
    const inv = new THREE.Matrix4().copy(group.matrixWorld).invert();
    this.vcMat = this.vcMat || new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.5, metalness: 0.2 });
    const buckets = new Map();
    const victims = [];
    const m = new THREE.Matrix4();
    const nm = new THREE.Matrix3();
    const v = new THREE.Vector3();
    group.traverse((o) => {
      if (!o.isMesh || keep.has(o) || Array.isArray(o.material) || o.material.transparent) return;
      const mat = o.material;
      const plain = !mat.map && !(mat.emissive && mat.emissive.getHex() !== 0);
      const target = plain ? this.vcMat : mat;
      const geo = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry;
      m.multiplyMatrices(inv, o.matrixWorld);
      nm.getNormalMatrix(m);
      let b = buckets.get(target);
      if (!b) buckets.set(target, (b = { pos: [], nor: [], uv: [], col: [] }));
      const P = geo.attributes.position;
      const c = mat.color || new THREE.Color(1, 1, 1);
      for (let k = 0; k < P.count; k += 1) {
        v.fromBufferAttribute(P, k).applyMatrix4(m);
        b.pos.push(v.x, v.y, v.z);
        v.fromBufferAttribute(geo.attributes.normal, k).applyMatrix3(nm).normalize();
        b.nor.push(v.x, v.y, v.z);
        b.uv.push(geo.attributes.uv.getX(k), geo.attributes.uv.getY(k));
        b.col.push(c.r, c.g, c.b);
      }
      victims.push(o);
    });
    for (const o of victims) o.parent.remove(o);
    for (const [mat, b] of buckets) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(b.pos, 3));
      geo.setAttribute('normal', new THREE.Float32BufferAttribute(b.nor, 3));
      geo.setAttribute('uv', new THREE.Float32BufferAttribute(b.uv, 2));
      if (mat === this.vcMat) geo.setAttribute('color', new THREE.Float32BufferAttribute(b.col, 3));
      geo.computeBoundingSphere();
      const mesh = new THREE.Mesh(geo, mat);
      mesh.castShadow = true;
      group.add(mesh);
    }
    return group;
  }

  litMaterial(params, nightIntensity) {
    const m = new THREE.MeshStandardMaterial(params);
    this.lit.push([m, nightIntensity]);
    return m;
  }

  /** Material that shows its own texture brightly at night (signs, panels). */
  glowingMap(tex, intensity = 0.8, extra = {}) {
    return this.litMaterial({ map: tex, emissiveMap: tex, emissive: 0xffffff, emissiveIntensity: intensity, roughness: 0.6, ...extra }, intensity);
  }

  facadeMaterial(f, intensity = 0.95) {
    const m = this.litMaterial({ map: f.night, emissiveMap: f.emissive, emissive: 0xffffff, emissiveIntensity: intensity, roughness: 0.8 }, intensity);
    this.facadeMats.push([m, f.day, f.night]);
    return m;
  }

  glowSprite(parent, x, y, z, s, mat = this.warmGlow) {
    const sp = new THREE.Sprite(mat);
    sp.position.set(x, y, z);
    sp.scale.set(s, s, 1);
    parent.add(sp);
    this.glows.push(sp);
    return sp;
  }

  pickable(object, tip, planId = null) {
    if (this.building) {
      // static building: an invisible box proxy is cheaper to ray-cast and survives merging
      object.updateWorldMatrix(true, true);
      const b = new THREE.Box3();
      object.traverse((o) => { if (o.isMesh) b.union(new THREE.Box3().setFromObject(o)); });
      if (b.isEmpty()) return;
      const size = b.getSize(new THREE.Vector3());
      const proxy = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z), this.proxyMat || (this.proxyMat = new THREE.MeshBasicMaterial()));
      b.getCenter(proxy.position);
      proxy.updateMatrixWorld(true);
      proxy.userData = { tip, planId };
      this.pickables.push(proxy);
      return;
    }
    object.traverse((o) => {
      if (o.isMesh) {
        o.userData.tip = tip;
        o.userData.planId = planId;
        this.pickables.push(o);
      }
    });
  }

  /**
   * Merge every static mesh that shares a material into one mesh (world-space geometry), so the
   * whole city draws in a few hundred calls instead of thousands. Animated parts are skipped.
   */
  mergeStatic() {
    this.scene.updateMatrixWorld(true);
    const skip = new Set();
    for (const root of [this.trafficGroup, this.barrier.pivot, this.flag]) root.traverse((o) => skip.add(o));
    const buckets = new Map();
    const victims = [];
    const normalMatrix = new THREE.Matrix3();
    const v = new THREE.Vector3();
    this.scene.traverse((o) => {
      if (!o.isMesh || o.isInstancedMesh || skip.has(o)) return;
      const g = o.geometry;
      if (!g.attributes.normal || !g.attributes.uv || g.attributes.color) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      if (mats.some((m) => m.transparent || m.vertexColors)) return;
      const geo = g.index ? g.toNonIndexed() : g;
      const groups = Array.isArray(o.material) && geo.groups.length ? geo.groups : [{ start: 0, count: geo.attributes.position.count, materialIndex: 0 }];
      normalMatrix.getNormalMatrix(o.matrixWorld);
      for (const grp of groups) {
        const mat = mats[grp.materialIndex];
        if (!mat) continue;
        const key = `${mat.uuid}|${o.castShadow ? 1 : 0}|${o.receiveShadow ? 1 : 0}`;
        let bucket = buckets.get(key);
        if (!bucket) {
          bucket = { mat, cast: o.castShadow, receive: o.receiveShadow, pos: [], nor: [], uv: [] };
          buckets.set(key, bucket);
        }
        const P = geo.attributes.position;
        const N = geo.attributes.normal;
        const U = geo.attributes.uv;
        for (let k = grp.start; k < grp.start + grp.count; k += 1) {
          v.fromBufferAttribute(P, k).applyMatrix4(o.matrixWorld);
          bucket.pos.push(v.x, v.y, v.z);
          v.fromBufferAttribute(N, k).applyMatrix3(normalMatrix).normalize();
          bucket.nor.push(v.x, v.y, v.z);
          bucket.uv.push(U.getX(k), U.getY(k));
        }
      }
      if (geo !== g) geo.dispose();
      victims.push(o);
    });
    for (const o of victims) {
      o.parent.remove(o);
      o.geometry.dispose();
    }
    this.merged = new THREE.Group();
    for (const b of buckets.values()) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(b.pos, 3));
      geo.setAttribute('normal', new THREE.Float32BufferAttribute(b.nor, 3));
      geo.setAttribute('uv', new THREE.Float32BufferAttribute(b.uv, 2));
      geo.computeBoundingSphere();
      const mesh = new THREE.Mesh(geo, b.mat);
      mesh.castShadow = b.cast;
      mesh.receiveShadow = b.receive;
      mesh.matrixAutoUpdate = false;
      this.merged.add(mesh);
    }
    this.scene.add(this.merged);
    this.mergeStats = { meshes: victims.length, merged: buckets.size };
  }

  obstacle(p, r, what) {
    this.obstacles.push({ p: V(p.x, p.z), r, what });
  }

  /** True if a circle at p with radius r is clear of roads (plus margin) and static obstacles. */
  clearOf(p, r, margin = 2) {
    for (const road of this.roads) {
      for (const [a, b] of road.segs) if (segDist(p, a, b).d < road.width / 2 + margin + r) return false;
    }
    for (const o of this.obstacles) if (o.p.distanceTo(p) < o.r + r + 1) return false;
    return true;
  }

  insideCampus(p, pad = 0) {
    const c = this.plan.campus;
    return p.x > c.x0 + pad && p.x < c.x1 - pad && p.z > c.z0 + pad && p.z < c.z1 - pad;
  }

  /** World-space axis-aligned box of a site's footprint. */
  siteBox(site) {
    const [x0, x1, z0, z1] = FOOTPRINT[site.kind];
    const f = site.forward;
    const right = V(f.z, -f.x);
    const xs = [];
    const zs = [];
    for (const lx of [x0, x1]) {
      for (const lz of [z0, z1]) {
        xs.push(site.pos.x + right.x * lx + f.x * lz);
        zs.push(site.pos.z + right.z * lx + f.z * lz);
      }
    }
    return { x0: Math.min(...xs), x1: Math.max(...xs), z0: Math.min(...zs), z1: Math.max(...zs) };
  }

  /** Terrain height: the campus plateau (hilltop) with smooth slopes down to the plain. */
  heightAt(x, z) {
    const pl = this.plan.plateau;
    if (!pl) return 0;
    const c = this.plan.campus;
    const fall = (d, band) => {
      const t = Math.min(1, Math.max(0, (d - pl.margin) / band));
      return 1 - t * t * (3 - 2 * t);
    };
    const hx = x < c.x0 ? fall(c.x0 - x, pl.slope_west) : fall(Math.max(0, x - c.x1), pl.slope);
    const hz = fall(Math.max(0, c.z0 - z, z - c.z1), pl.slope);
    return pl.height * hx * hz;
  }

  /** Bake a flat mesh into world space and bend its vertices over the terrain. */
  drape(mesh) {
    mesh.updateMatrixWorld(true);
    const geo = mesh.geometry.clone().applyMatrix4(mesh.matrixWorld);
    const P = geo.attributes.position;
    const tint = mesh.userData.tint ? [] : null;
    for (let k = 0; k < P.count; k += 1) {
      const x = P.getX(k);
      const z = P.getZ(k);
      P.setY(k, P.getY(k) + this.heightAt(x, z));
      if (tint) {
        const slope = Math.min(1, Math.hypot(this.heightAt(x + 1, z) - this.heightAt(x - 1, z), this.heightAt(x, z + 1) - this.heightAt(x, z - 1)) * 1.2);
        tint.push(1 - 0.25 * slope, 1 - 0.42 * slope, 1 - 0.55 * slope);
      }
    }
    if (tint) {
      geo.setAttribute('color', new THREE.Float32BufferAttribute(tint, 3));
      mesh.material.vertexColors = true;
    }
    geo.computeVertexNormals();
    mesh.geometry.dispose();
    mesh.geometry = geo;
    mesh.position.set(0, 0, 0);
    mesh.rotation.set(0, 0, 0);
    mesh.scale.set(1, 1, 1);
    mesh.userData.drape = false;
  }

  /** Lift everything built so far onto the terrain (the campus plateau and its slopes). */
  applyTerrain() {
    if (!this.plan.plateau) return;
    for (const proxy of this.pickables) {
      proxy.position.y += this.heightAt(proxy.position.x, proxy.position.z);
      proxy.updateMatrixWorld(true);
    }
    const m = new THREE.Matrix4();
    for (const o of [...this.scene.children]) {
      if (o.isLight || o === this.sunTarget || o === this.trafficGroup || o.userData.noLift) continue;
      if (o.userData.drape) {
        this.drape(o);
      } else if (o.isInstancedMesh) {
        for (let i = 0; i < o.count; i += 1) {
          o.getMatrixAt(i, m);
          m.elements[13] += this.heightAt(m.elements[12], m.elements[14]);
          o.setMatrixAt(i, m);
        }
        o.instanceMatrix.needsUpdate = true;
        o.computeBoundingSphere();
      } else if (o.userData.container) {
        for (const ch of o.children) ch.position.y += this.heightAt(ch.position.x, ch.position.z);
      } else {
        o.position.y += this.heightAt(o.position.x, o.position.z);
      }
    }
  }

  // ------------------------------------------------------------------ layout (pure geometry)
  layoutWorld() {
    const P = this.plan;
    this.roads = P.roads.map((r) => {
      const pts = r.pts.map(([x, z]) => V(x, z));
      const closed = Boolean(r.closed);
      const n = closed ? pts.length : pts.length - 1;
      const segs = Array.from({ length: n }, (_, i) => [pts[i], pts[(i + 1) % pts.length]]);
      return { ...r, pts, closed, segs };
    });
    const mk = (kind, ref) => ({
      kind,
      ref,
      name: ref.name,
      pos: V(ref.x, ref.z),
      forward: V(ref.fx, ref.fz).normalize(),
      front: V(ref.front[0], ref.front[1]),
    });
    const gates = this.world.gates || [];
    this.sites = [
      ...gates.filter((g) => g.on_campus).map((g) => mk('hostel', g)),
      ...gates.filter((g) => !g.on_campus).map((g) => mk('pg', g)),
      ...this.world.stores.map((s) => mk(s.category === 'FOOD' ? 'food' : 'grocery', s)),
    ];
    for (const s of this.sites) {
      s.box = this.siteBox(s);
      s.radius = Math.hypot(s.box.x1 - s.box.x0, s.box.z1 - s.box.z0) / 2;
      this.obstacle(V((s.box.x0 + s.box.x1) / 2, (s.box.z0 + s.box.z1) / 2), s.radius * 0.92, s.name);
    }
    this.gatePos = V(P.campus.gate[0], P.campus.gate[1]);

    // cut every road where it meets another road, a driveway or the gate, then chain the cuts
    const cuts = this.roads.map((r) => r.pts.map((p, i) => ({ i, t: 0, p })));
    for (let ri = 0; ri < this.roads.length; ri += 1) {
      for (let rj = ri + 1; rj < this.roads.length; rj += 1) {
        this.roads[ri].segs.forEach(([a, b], i) => {
          this.roads[rj].segs.forEach(([c, d], j) => {
            const x = segIntersect(a, b, c, d);
            if (!x) return;
            cuts[ri].push({ i, t: x.t, p: x.p });
            cuts[rj].push({ i: j, t: x.u, p: x.p });
          });
        });
      }
    }
    const attachTo = (point) => {
      let best = null;
      this.roads.forEach((r, ri) => {
        r.segs.forEach(([a, b], i) => {
          const s = segDist(point, a, b);
          if (!best || s.d < best.d) best = { ...s, ri, i };
        });
      });
      cuts[best.ri].push({ i: best.i, t: best.t, p: best.q });
      return best;
    };
    for (const s of this.sites) s.join = attachTo(s.front);
    attachTo(this.gatePos);
    const graph = new RoadGraph();
    this.roads.forEach((r, ri) => {
      const list = cuts[ri].sort((x, y) => x.i + x.t - (y.i + y.t));
      const ids = list.map((c) => graph.node(c.p));
      for (let k = 1; k < ids.length; k += 1) graph.edge(ids[k - 1], ids[k], 'road');
      if (r.closed) graph.edge(ids[ids.length - 1], ids[0], 'road');
    });
    for (const s of this.sites) {
      const j = graph.node(s.join.q);
      const f = graph.node(s.front);
      graph.edge(j, f, 'drive');
      s.frontNode = f;
      this.anchors.set(this.key(s.ref.lat, s.ref.lng), { node: f, front: s.front });
    }
    this.gateNode = graph.node(this.gatePos);
    this.graph = graph;
    const xs = this.roads.flatMap((r) => r.pts.map((p) => p.x));
    const zs = this.roads.flatMap((r) => r.pts.map((p) => p.z));
    this.bounds = { x0: Math.min(...xs) - 120, x1: Math.max(...xs) + 260, z0: Math.min(...zs) - 60, z1: Math.max(...zs) + 60 };
  }

  // ------------------------------------------------------------------ lights and ground
  buildLights() {
    this.hemi = new THREE.HemisphereLight(...NIGHT.hemi);
    this.ambient = new THREE.AmbientLight(0xffffff, NIGHT.ambient);
    this.sun = new THREE.DirectionalLight(NIGHT.sun[0], NIGHT.sun[1]);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(4096, 4096);
    const b = this.bounds;
    this.sunTarget = new THREE.Object3D();
    this.sunTarget.position.set((b.x0 + b.x1) / 2, 0, (b.z0 + b.z1) / 2);
    this.sun.target = this.sunTarget;
    const half = Math.max(b.x1 - b.x0, b.z1 - b.z0) / 2 + 40;
    Object.assign(this.sun.shadow.camera, { left: -half, right: half, top: half, bottom: -half, near: 10, far: 3000 });
    this.sun.shadow.bias = -0.0004;
    this.sun.shadow.normalBias = 0.6;
    this.scene.add(this.hemi, this.ambient, this.sun, this.sunTarget);
  }

  setTimeOfDay(mode) {
    const cfg = mode === 'day' ? DAY : NIGHT;
    this.mode = mode;
    this.scene.background = this.skies[mode];
    const ibl = this.envMaps && this.quality !== 'lite';
    this.scene.environment = ibl ? this.envMaps[mode] : null;
    this.scene.fog.color.set(cfg.fog);
    [this.scene.fog.near, this.scene.fog.far] = cfg.fogRange;
    this.hemi.color.set(cfg.hemi[0]);
    this.hemi.groundColor.set(cfg.hemi[1]);
    this.hemi.intensity = cfg.hemi[2] * (ibl ? 1 : 2);
    this.ambient.intensity = cfg.ambient * (ibl ? 1 : 2.5);
    this.sun.color.set(cfg.sun[0]);
    this.sun.intensity = cfg.sun[1];
    this.sun.position.set(this.sunTarget.position.x + cfg.sun[2][0], cfg.sun[2][1], this.sunTarget.position.z + cfg.sun[2][2]);
    this.renderer.toneMappingExposure = cfg.exposure;
    for (const [m, intensity] of this.lit) m.emissiveIntensity = intensity * cfg.emissive;
    for (const [m, day, night] of this.facadeMats) {
      m.map = mode === 'day' ? day : night;
      m.needsUpdate = true;
    }
    for (const g of this.glows) g.visible = cfg.lamps > 0;
    for (const v of this.traffic) v.lights.visible = cfg.lamps > 0;
    for (const r of this.riders.values()) r.group.userData.lamp.visible = Boolean(r.view) && cfg.lamps > 0;
    this.renderer.shadowMap.needsUpdate = true;
    this.container.classList.toggle('day', mode === 'day');
  }

  flat(w, d, mat, x, z, y, seg = 1) {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d, seg, seg), mat);
    m.userData.drape = true;
    m.rotation.x = -Math.PI / 2;
    m.position.set(x, y, z);
    m.receiveShadow = true;
    this.scene.add(m);
    return m;
  }

  buildGround() {
    const b = this.bounds;
    const W = (b.x1 - b.x0) * 2.2;
    const D = (b.z1 - b.z0) * 1.8;
    // far ground to the horizon (fog hides its edge), then the detailed soil around the world
    this.flat(9000, 9000, this.std({ color: 0x6f5a45, roughness: 1 }), (b.x0 + b.x1) / 2, (b.z0 + b.z1) / 2, -0.05);
    const land = this.flat(W, D, new THREE.MeshStandardMaterial({ map: soilTexture(W / 60, D / 60), roughness: 1 }), (b.x0 + b.x1) / 2, (b.z0 + b.z1) / 2, 0, 220);
    land.userData.tint = true;
    const c = this.plan.campus;
    this.flat(c.x1 - c.x0 + 8, c.z1 - c.z0 + 8, new THREE.MeshStandardMaterial({ map: lawnTexture((c.x1 - c.x0) / 14, (c.z1 - c.z0) / 14), color: 0xcfe0b0, roughness: 1 }), (c.x0 + c.x1) / 2, (c.z0 + c.z1) / 2, 0.03, 8);
    const { xs, zs } = this.plan.city;
    const cx0 = xs[0] - 40;
    const cx1 = xs[xs.length - 1] + 40;
    const cz0 = zs[0] - 40;
    const cz1 = zs[zs.length - 1] + 40;
    this.flat(cx1 - cx0, cz1 - cz0, this.std({ color: 0x8f8a82, roughness: 1 }), (cx0 + cx1) / 2, (cz0 + cz1) / 2, 0.03);
    // Tungabhadra river north of the city, with sandy banks
    const [rz0, rz1] = this.plan.river_z;
    const water = this.litMaterial({ map: waterTexture(40, 2), roughness: 0.15, metalness: 0.2, emissive: 0x0c2a3a, emissiveIntensity: 0.5 }, 0.5);
    this.river = this.flat(W, rz1 - rz0, water, (b.x0 + b.x1) / 2, (rz0 + rz1) / 2, 0.06);
    const sand = this.std({ color: 0xc9b089, roughness: 1 });
    this.flat(W, 6, sand, (b.x0 + b.x1) / 2, rz1 + 3, 0.05);
    this.flat(W, 6, sand, (b.x0 + b.x1) / 2, rz0 - 3, 0.05);
    this.placeLabel('Tungabhadra river', 'water', V(-20, (rz0 + rz1) / 2), 4);
    this.landmarks.push({ name: 'Tungabhadra river', pos: V(-60, (rz0 + rz1) / 2), forward: V(0, 1) });
  }

  // ------------------------------------------------------------------ roads
  buildRoads() {
    const curb = this.std({ color: 0xb3ada2, roughness: 0.95 });
    const tex = { highway: roadTexture(true), street: roadTexture(true), lane: roadTexture(false), campus: roadTexture(false) };
    const mats = Object.fromEntries(Object.entries(tex).map(([k, t]) => [k, new THREE.MeshStandardMaterial({ map: t, roughness: 0.85 })]));
    const y = { highway: 0.16, street: 0.14, lane: 0.13, campus: 0.12 };
    for (const r of this.roads) {
      const dense = densify(r.pts, 3, r.closed);
      const c = new THREE.Mesh(ribbon(dense, r.width + 2.4, r.closed), curb);
      c.position.y = y[r.kind] - 0.05;
      const m = new THREE.Mesh(ribbon(dense, r.width, r.closed), mats[r.kind]);
      m.position.y = y[r.kind];
      c.receiveShadow = true;
      m.receiveShadow = true;
      c.userData.drape = true;
      m.userData.drape = true;
      this.scene.add(c, m);
    }
    // plain asphalt over every junction so lane markings do not cross
    const plain = this.std({ color: 0x2b2f36, roughness: 0.9 });
    const zebra = new THREE.MeshStandardMaterial({ map: stripeTexture(['#e9e9e2', '#2b2f36', '#e9e9e2', '#2b2f36', '#e9e9e2', '#2b2f36', '#e9e9e2']), roughness: 0.9 });
    const degree = new Map();
    for (const [a, b, kind] of this.graph.edges) {
      if (kind !== 'road') continue;
      degree.set(a, (degree.get(a) || 0) + 1);
      degree.set(b, (degree.get(b) || 0) + 1);
    }
    const [rcx, rcz, rr] = this.plan.roundabout;
    const onRoad = (q) => this.roads.some((r) => r.segs.some(([a, b]) => segDist(q, a, b).d < 1));
    for (const [n, d] of degree) {
      if (d < 3) continue;
      const p = this.graph.pts[n];
      if (Math.hypot(p.x - rcx, p.z - rcz) < rr + 1) continue;
      const w = Math.max(...this.roads.filter((r) => r.segs.some(([a, b]) => segDist(p, a, b).d < 0.5)).map((r) => r.width));
      const patch = new THREE.Mesh(new THREE.PlaneGeometry(w, w), plain);
      patch.rotation.x = -Math.PI / 2;
      patch.position.set(p.x, 0.19, p.z);
      patch.receiveShadow = true;
      patch.userData.drape = true;
      this.scene.add(patch);
      if (w < 14) continue;
      for (const [dx, dz, rot] of [[0, w / 2 + 1.6, 0], [0, -w / 2 - 1.6, 0], [w / 2 + 1.6, 0, Math.PI / 2], [-w / 2 - 1.6, 0, Math.PI / 2]]) {
        const q = V(p.x + dx, p.z + dz);
        if (!onRoad(q)) continue;
        const zb = new THREE.Mesh(new THREE.PlaneGeometry(w - 2, 2.4), zebra);
        zb.rotation.set(-Math.PI / 2, 0, rot);
        zb.position.set(q.x, 0.2, q.z);
        zb.userData.drape = true;
        this.scene.add(zb);
      }
    }
    // driveways where a building's front is set back from the carriageway
    const drive = this.std({ color: 0x5b5f66, roughness: 0.95 });
    for (const s of this.sites) {
      if (s.join.d < this.roads[s.join.ri].width / 2 + 0.5) continue;
      const m = new THREE.Mesh(ribbon(densify([s.join.q, s.front], 2), 5), drive);
      m.position.y = 0.11;
      m.userData.drape = true;
      m.receiveShadow = true;
      this.scene.add(m);
    }
    // NH 40 bridge over the river, and road signs
    const hw = this.roads.find((r) => r.kind === 'highway');
    if (hw) {
      const [rz0, rz1] = this.plan.river_z;
      const x = hw.pts[0].x;
      const deck = this.std({ color: 0x9a958c, roughness: 0.9 });
      this.scene.add(shadowed(box(hw.width + 3, 1.2, rz1 - rz0 + 16, deck, x, -0.5, (rz0 + rz1) / 2)));
      for (const side of [-1, 1]) this.scene.add(shadowed(box(0.6, 1.6, rz1 - rz0 + 16, deck, x + side * (hw.width / 2 + 1.2), 0.8, (rz0 + rz1) / 2)));
      for (let z = rz0 + 4; z < rz1; z += 9) this.scene.add(shadowed(cyl(1.2, 1.4, 4, deck, x, -2, z, 10)));
      this.placeLabel('NH 40 to Kurnool city', 'road', V(x, -120), 6);
      this.placeLabel('NH 40 to Kadapa', 'road', V(x, 200), 6);
      this.landmarks.push({ name: 'NH 40 highway', pos: V(x, -110), forward: V(1, 0) });
    }
    const cp = this.plan.checkpost;
    this.placeLabel('Nandyal Checkpost', 'road', V(cp[0] + 30, cp[1] - 12), 6);
  }

  // ------------------------------------------------------------------ campus
  buildCampus() {
    const c = this.plan.campus;
    const [gx, gz] = c.gate;
    // compound wall with a single opening at the main gate
    const wallMat = this.std({ color: 0xd9d2c3, roughness: 0.9 });
    const capMat = this.std({ color: 0xa8432f, roughness: 0.8 });
    const opening = 12;
    const wallRuns = [
      [[c.x0, c.z0], [c.x1, c.z0]],
      [[c.x1, c.z0], [c.x1, c.z1]],
      [[c.x1, c.z1], [c.x0, c.z1]],
      [[c.x0, c.z1], [c.x0, gz + opening]],
      [[c.x0, gz - opening], [c.x0, c.z0]],
    ];
    this.wallSegs = [];
    for (const [[x0, z0], [x1, z1]] of wallRuns) {
      const len = Math.hypot(x1 - x0, z1 - z0);
      const g = new THREE.Group();
      g.add(box(len, 2.8, 0.8, wallMat, 0, 1.4, 0), box(len + 0.4, 0.4, 1.1, capMat, 0, 3.0, 0));
      for (let s = -len / 2; s <= len / 2 + 0.01; s += 8) g.add(box(1.3, 3.4, 1.3, wallMat, s, 1.7, 0));
      g.position.set((x0 + x1) / 2, 0, (z0 + z1) / 2);
      g.rotation.y = -Math.atan2(z1 - z0, x1 - x0);
      this.scene.add(shadowed(g));
      this.wallSegs.push([V(x0, z0), V(x1, z1)]);
    }
    this.buildMainGate(gx, gz);
    this.buildClockTower();
    this.buildLoveSign();
    this.buildAcademic();
    this.buildSports();
    this.placeLabel(this.world.campus || 'IIITDM Kurnool', 'campus big', V(-20, -32), 34);
  }

  buildMainGate(gx, gz) {
    const g = new THREE.Group();
    const grey = this.std({ color: 0x8a8d92, roughness: 0.7 });
    const dark = this.std({ color: 0x5c5f64, roughness: 0.6 });
    const red = this.std({ color: 0xb3322a, roughness: 0.6 });
    // local frame: x along the road (into the campus = +x), z across the road
    for (const z of [-10, 10]) g.add(box(4, 13, 4, grey, 0, 6.5, z));
    const panel = this.glowingMap(gatePanelTexture(), 0.75);
    const beam = new THREE.Mesh(new THREE.BoxGeometry(4.4, 6, 26), [grey, grey, dark, dark, grey, grey]);
    beam.position.set(0, 15.5, 0);
    const front = new THREE.Mesh(new THREE.PlaneGeometry(25, 6.1), panel);
    front.position.set(-2.22, 15.5, 0);
    front.rotation.y = -Math.PI / 2;
    const back = front.clone();
    back.position.x = 2.22;
    back.rotation.y = Math.PI / 2;
    g.add(beam, front, back);
    // red slatted canopy over the entry, on two red columns
    for (let x = 2.6; x <= 12; x += 0.8) g.add(box(0.32, 0.45, 23, red, x, 11.2, 0));
    g.add(box(10, 0.4, 0.5, red, 7.3, 10.8, -11.4), box(10, 0.4, 0.5, red, 7.3, 10.8, 11.4));
    for (const z of [-11.4, 11.4]) g.add(cyl(0.45, 0.45, 10.8, red, 12, 5.4, z, 12));
    // sliding gates parked open beside the pillars
    const bars = new THREE.MeshStandardMaterial({ map: stripeTexture(['#4b4f55', 'rgba(0,0,0,0)', '#4b4f55', 'rgba(0,0,0,0)', '#4b4f55', 'rgba(0,0,0,0)', '#4b4f55', 'rgba(0,0,0,0)']), transparent: true, alphaTest: 0.4, side: THREE.DoubleSide, metalness: 0.4, roughness: 0.5 });
    for (const side of [-1, 1]) {
      const sg = new THREE.Mesh(new THREE.PlaneGeometry(9, 2.6), bars);
      sg.rotation.y = Math.PI / 2;
      sg.position.set(0.8, 1.4, side * 16.5);
      g.add(sg, box(0.25, 0.25, 9, dark, 0.8, 2.75, side * 16.5), box(0.25, 0.25, 9, dark, 0.8, 0.15, side * 16.5));
    }
    // boom barrier (lifts while a verified rider enters)
    const post = box(0.7, 1.6, 0.7, this.std({ color: 0xf2c200 }), 5, 0.8, -5.8);
    const pivot = new THREE.Group();
    pivot.position.set(5, 1.45, -5.8);
    const arm = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.25, 10.6), new THREE.MeshStandardMaterial({ map: barrierTexture() }));
    arm.position.z = 5.3;
    pivot.add(arm);
    g.add(post, pivot);
    this.barrier = { pivot, until: 0 };
    // security cabin with a scanner light, and a guard
    const cabin = new THREE.Group();
    const cabinMat = this.facadeMaterial(facade({ base: '#e8e2d6', frame: '#7d7468', cols: 2, rows: 1, lit: 1, seed: 77 }), 0.9);
    cabin.add(box(4.2, 3.2, 4.2, cabinMat, 0, 1.6, 0), box(5, 0.4, 5, red, 0, 3.4, 0));
    const sec = new THREE.Mesh(new THREE.PlaneGeometry(3.6, 0.9), this.glowingMap(textTexture('SECURITY', { bg: '#1f3c78', w: 256, h: 64, size: 34 }), 0.8));
    sec.position.set(0, 2.9, 2.13);
    cabin.add(sec);
    this.scanner = this.litMaterial({ color: 0x22c55e, emissive: 0x22c55e, emissiveIntensity: 0.3 }, 0.3);
    cabin.add(box(0.5, 0.5, 0.5, this.scanner, 1.6, 3.85, 1.6));
    cabin.position.set(7, 0, -12.6);
    cabin.rotation.y = Math.PI / 2;
    g.add(cabin);
    const guard = new THREE.Group();
    guard.add(cyl(0.38, 0.38, 1.3, this.std({ color: 0x2d3a5c }), 0, 0.65, 0, 8));
    guard.add(cyl(0.45, 0.42, 1.2, this.std({ color: 0x9fb4d6 }), 0, 1.85, 0, 8));
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.36, 12, 10), this.std({ color: 0x8d5a3b }));
    head.position.y = 2.75;
    guard.add(head);
    guard.position.set(6.5, 0, -7.2);
    g.add(guard);
    // national flag outside the gate
    const pole = cyl(0.12, 0.16, 14, this.std({ color: 0xdddddd, metalness: 0.6 }), -6, 7, -15, 8);
    const flag = new THREE.Mesh(new THREE.PlaneGeometry(4.2, 2.8), new THREE.MeshStandardMaterial({ map: flagTexture(), side: THREE.DoubleSide }));
    flag.position.set(-6, 12.5, -12.8);
    flag.rotation.y = Math.PI / 2;
    g.add(pole, flag);
    this.flag = flag;
    for (const z of [-10, 10]) this.glowSprite(g, -2.6, 9, z, 7, this.lampGlow);
    g.position.set(gx, 0, gz);
    this.scene.add(shadowed(g));
    this.pickable(g, 'Main gate: the only entrance to IIITDM Kurnool. Riders show their Swiggy or Instamart uniform here.');
    this.placeLabel('Main gate (only entrance)', 'gate', V(gx - 4, gz), 22);
    this.zoomTag(g, V(gx, gz), 26, 'IIITDM Kurnool main gate', 'Single entrance · uniform check for every delivery rider', null);
    this.landmarks.push({ name: 'Main gate', pos: V(gx, gz), forward: V(-1, 0) });
    for (const z of [-10, 10, -16.5, 16.5]) this.obstacle(V(gx, gz + z), 2.5, 'gate');
    for (const z of [-11.4, 11.4]) this.obstacle(V(gx + 12, gz + z), 0.8, 'canopy column');
    this.obstacle(V(gx + 7, gz - 12.6), 3.2, 'security cabin');
    this.obstacle(V(gx - 6, gz - 15), 1, 'flag');
  }

  buildClockTower() {
    const [cx, cz, r] = this.plan.roundabout;
    const g = new THREE.Group();
    const island = new THREE.Mesh(new THREE.CircleGeometry(r - 3.6, 48), new THREE.MeshStandardMaterial({ map: lawnTexture(2, 2), roughness: 1 }));
    island.rotation.x = -Math.PI / 2;
    island.position.y = 0.22;
    const kerb = new THREE.Mesh(new THREE.TorusGeometry(r - 3.6, 0.35, 8, 48), new THREE.MeshStandardMaterial({ map: brickTexture() }));
    kerb.rotation.x = -Math.PI / 2;
    kerb.position.y = 0.3;
    g.add(island, kerb);
    const red = this.std({ color: 0xb8352b, roughness: 0.7 });
    const white = this.std({ color: 0xf2eee6, roughness: 0.6 });
    g.add(box(4.6, 1, 4.6, white, 0, 0.5, 0), box(3.2, 22, 3.2, red, 0, 12, 0));
    for (const y of [6, 12, 17.5]) g.add(box(3.5, 0.35, 3.5, white, 0, y, 0));
    const groove = this.std({ color: 0x8e2620, roughness: 0.8 });
    for (const [x, z, w, d] of [[0, 1.62, 0.4, 0.1], [0, -1.62, 0.4, 0.1], [1.62, 0, 0.1, 0.4], [-1.62, 0, 0.1, 0.4]]) g.add(box(w, 14, d, groove, x, 9, z));
    g.add(box(3.8, 3.8, 3.8, red, 0, 21, 0), box(4.2, 0.5, 4.2, white, 0, 23.1, 0));
    const roof = new THREE.Mesh(new THREE.ConeGeometry(2.9, 2.6, 4), red);
    roof.position.y = 24.6;
    roof.rotation.y = Math.PI / 4;
    g.add(roof);
    const face = this.glowingMap(clockTexture(), 0.9);
    for (const [x, z, ry] of [[0, 1.92, 0], [0, -1.92, Math.PI], [1.92, 0, Math.PI / 2], [-1.92, 0, -Math.PI / 2]]) {
      const m = new THREE.Mesh(new THREE.CircleGeometry(1.45, 32), face);
      m.position.set(x, 21, z);
      m.rotation.y = ry;
      g.add(m);
    }
    for (let k = 0; k < 10; k += 1) {
      const a = (k / 10) * Math.PI * 2;
      const shrub = new THREE.Mesh(new THREE.IcosahedronGeometry(0.7, 1), this.std({ color: k % 2 ? 0x3f7d2c : 0xd94f70, flatShading: true }));
      shrub.position.set(Math.sin(a) * 3.6, 0.7, Math.cos(a) * 3.6);
      g.add(shrub);
    }
    this.glowSprite(g, 0, 21, 0, 12, this.warmGlow);
    g.position.set(cx, 0, cz);
    this.scene.add(shadowed(g));
    this.pickable(g, 'Clock tower circle: the red clock tower at the heart of the campus');
    this.zoomTag(g, V(cx, cz), 29, 'Clock tower circle', 'Red clock tower on the central roundabout', null);
    this.landmarks.push({ name: 'Clock tower circle', pos: V(cx, cz), forward: V(-1, 0) });
    this.obstacle(V(cx, cz), r - 3, 'clock tower island');
  }

  buildLoveSign() {
    const g = new THREE.Group();
    const step = this.std({ color: 0x9c3c2e, roughness: 0.8 });
    const stone = this.std({ color: 0xcfc7b8, roughness: 0.9 });
    g.add(box(19, 0.45, 5.4, stone, 0, 0.22, 0), box(18, 0.45, 4.2, step, 0, 0.67, -0.3), box(17, 0.5, 3, stone, 0, 1.12, -0.6));
    const tex = loveSignTexture();
    const mat = this.litMaterial({ map: tex, emissiveMap: tex, emissive: 0xffffff, emissiveIntensity: 0.55, transparent: true, alphaTest: 0.35, side: THREE.DoubleSide, roughness: 0.4 }, 0.55);
    const shade = new THREE.MeshStandardMaterial({ map: tex, color: 0x7d7d7d, transparent: true, alphaTest: 0.35, side: THREE.DoubleSide });
    for (let k = 0; k < 8; k += 1) {
      const p = new THREE.Mesh(new THREE.PlaneGeometry(16, 2.5), k === 0 ? mat : shade);
      p.position.set(0, 2.55, -0.6 - k * 0.07);
      g.add(p);
    }
    this.glowSprite(g, 0, 2.6, 1.6, 12, this.warmGlow);
    g.position.set(-86, 0, -49);
    this.scene.add(shadowed(g));
    this.pickable(g, '"I love IIITDM Kurnool" sign on the steps by the entrance road');
    this.zoomTag(g, g.position, 7, 'I ♥ IIITDM KURNOOL', 'Photo spot on the entrance steps', null);
    this.landmarks.push({ name: 'I love IIITDM Kurnool sign', pos: g.position.clone(), forward: V(0, 1) });
    this.obstacle(V(-86, -49), 9.5, 'love sign');
  }

  /** White academic blocks with red vertical fins, brick panels, a skywalk, a pool and pavers. */
  buildAcademic() {
    const whiteMat = this.facadeMaterial(facade({ base: '#f2f0ea', frame: '#cfc9bf', cols: 14, rows: 4, lit: 0.5, seed: 61 }), 0.85);
    const roof = this.std({ color: 0xd8d3ca, roughness: 0.9 });
    const brick = new THREE.MeshStandardMaterial({ map: brickTexture('#a8432f', '#d9c7b5', 2, 3) });
    const red = this.std({ color: 0xb3322a, roughness: 0.6 });
    const offWhite = this.std({ color: 0xf4f1ea, roughness: 0.7 });
    const glass = this.litMaterial({ color: 0x6e8fae, emissive: 0xffd9a0, emissiveIntensity: 0.6, metalness: 0.3, roughness: 0.2 }, 0.6);
    this.flat(62, 30, new THREE.MeshStandardMaterial({ map: paverTexture(8, 4), roughness: 0.95 }), -12, -32, 0.06);
    this.flat(9, 28, new THREE.MeshStandardMaterial({ map: paverTexture(1, 4), roughness: 0.95 }), -47.5, -35, 0.07);
    this.flat(56, 22, new THREE.MeshStandardMaterial({ map: lawnTexture(6, 3), roughness: 1 }), -14, -75, 0.06);
    this.flat(56, 16, new THREE.MeshStandardMaterial({ map: lawnTexture(6, 2), roughness: 1 }), -14, 7.5, 0.06);
    const blocks = [
      // [cx, cz, w (x), d (z), h, name, fins on the long faces, brick end panels]
      [-14, -52, 48, 14, 18, 'Tungabhadra academic block', true, false],
      [-14, -12, 48, 14, 15, 'Lecture hall complex', true, true],
      [-38, -32, 10, 26, 12, 'Administration and library', false, true],
    ];
    const fins = [];
    for (const [cx, cz, w, d, h, name, finned, brickEnds] of blocks) {
      const g = new THREE.Group();
      const ends = brickEnds ? brick : whiteMat;
      g.add(box(w + 1, 0.8, d + 1, this.std({ color: 0xbdb5a8 }), 0, 0.4, 0));
      const body = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), [ends, ends, roof, roof, whiteMat, whiteMat]);
      body.position.y = h / 2 + 0.8;
      g.add(body, box(w + 0.6, 0.7, d + 0.6, this.std({ color: 0xe9e5dd }), 0, h + 1.15, 0));
      if (finned) {
        for (let x = -w / 2 + 1.2; x <= w / 2 - 1.2; x += 2.4) for (const side of [-1, 1]) fins.push([cx + x, cz + side * (d / 2 + 0.6), h]);
      }
      g.position.set(cx, 0, cz);
      this.scene.add(shadowed(g));
      this.pickable(g, `${this.world.campus || 'Campus'}: ${name}`);
      this.obstacle(V(cx, cz), Math.hypot(w, d) / 2, name);
    }
    const finMesh = new THREE.InstancedMesh(new THREE.BoxGeometry(0.4, 1, 1.2), red, fins.length);
    const m = new THREE.Matrix4();
    fins.forEach(([x, z, h], i) => finMesh.setMatrixAt(i, m.compose(new THREE.Vector3(x, h / 2 + 0.8, z), new THREE.Quaternion(), new THREE.Vector3(1, h, 1))));
    finMesh.castShadow = true;
    this.scene.add(finMesh);
    // block name in red letters: on the roof edge facing the gate road, and on the west end
    const nameMat = this.glowingMap(textTexture('TUNGABHADRA', { bg: '#f2f0ea', fg: '#b3322a', w: 1024, h: 128, size: 96 }), 0.5);
    const roofSign = new THREE.Group();
    roofSign.add(box(22, 3, 0.4, offWhite, 0, 0, 0));
    const rs = new THREE.Mesh(new THREE.PlaneGeometry(21.4, 2.6), nameMat);
    rs.position.z = -0.21;
    rs.rotation.y = Math.PI;
    roofSign.add(rs);
    roofSign.position.set(-14, 21.4, -56);
    this.scene.add(shadowed(roofSign));
    const endSign = new THREE.Mesh(new THREE.PlaneGeometry(12, 1.5), nameMat);
    endSign.position.set(-38.05, 16.4, -52);
    endSign.rotation.y = -Math.PI / 2;
    this.scene.add(endSign);
    // entrance canopy on red columns facing the clock tower
    const canopy = new THREE.Group();
    canopy.add(box(6.5, 0.6, 15, offWhite, 0, 7, 0));
    for (const z of [-6.6, -2.2, 2.2, 6.6]) canopy.add(cyl(0.45, 0.45, 7, red, -2.6, 3.5, z, 14));
    const door = new THREE.Mesh(new THREE.PlaneGeometry(6, 4.4), glass);
    door.position.set(3.24, 2.6, 0);
    door.rotation.y = -Math.PI / 2;
    canopy.add(door);
    canopy.position.set(-46.3, 0, -32);
    this.scene.add(shadowed(canopy));
    this.glowSprite(canopy, -1, 6.4, 0, 9, this.warmGlow);
    // skywalk on red columns between the two long blocks
    const sky = new THREE.Group();
    sky.add(box(3.4, 0.5, 26, offWhite, 0, 8.2, 0), box(3.4, 0.4, 26, offWhite, 0, 10.9, 0));
    for (const side of [-1, 1]) {
      const pane = new THREE.Mesh(new THREE.PlaneGeometry(26, 2.2), glass);
      pane.rotation.y = Math.PI / 2;
      pane.position.set(side * 1.65, 9.55, 0);
      sky.add(pane);
    }
    for (const z of [-6, 6]) for (const x of [-1.3, 1.3]) sky.add(cyl(0.38, 0.38, 8, red, x, 4, z, 12));
    sky.position.set(5.2, 0, -32);
    this.scene.add(shadowed(sky));
    this.pickable(sky, 'Skywalk on red columns between the academic blocks');
    // courtyard pool
    const pool = new THREE.Group();
    pool.add(box(19, 0.5, 8, this.std({ color: 0xece8df }), 0, 0.25, 0));
    const water = new THREE.Mesh(new THREE.PlaneGeometry(17.6, 6.6), this.litMaterial({ map: waterTexture(3, 1, '#2a8fbf', '#9ee3ff'), emissive: 0x1c6f9a, emissiveIntensity: 0.7, roughness: 0.1 }, 0.7));
    water.rotation.x = -Math.PI / 2;
    water.position.y = 0.52;
    pool.add(water);
    pool.position.set(-14, 0, -32);
    this.scene.add(pool);
    this.pickable(pool, 'Courtyard pool with a red paver deck');
    for (const x of [-30, 2]) {
      for (const z of [-41, -23]) {
        const planter = new THREE.Group();
        planter.add(cyl(1, 0.9, 0.8, red, 0, 0.4, 0, 12));
        const crown = new THREE.Mesh(new THREE.IcosahedronGeometry(1.4, 1), this.std({ color: 0x3e7d34, flatShading: true }));
        crown.position.y = 1.9;
        planter.add(crown);
        planter.position.set(x, 0, z);
        this.scene.add(shadowed(planter));
        this.obstacle(V(x, z), 1.5, 'planter');
      }
    }
    this.landmarks.push({ name: 'Academic area (Tungabhadra block)', pos: V(-14, -32), forward: V(-1, 0) });
    this.obstacle(V(-14, -32), 10, 'pool');
    this.obstacle(V(5.2, -32), 3, 'skywalk');
    this.obstacle(V(-47.5, -32), 4, 'canopy');
    // staff parking with a few cars, north-west of the roundabout
    const lotTex = stripeTexture(['#55585e', '#55585e', '#d8d8d0', '#55585e', '#55585e', '#55585e']);
    lotTex.wrapS = THREE.RepeatWrapping;
    lotTex.repeat.set(5, 1);
    this.flat(26, 11, new THREE.MeshStandardMaterial({ map: lotTex, roughness: 0.95 }), -72, -70, 0.07);
    const rnd = mulberry32(12);
    for (let k = 0; k < 5; k += 1) {
      const car = this.vehicleModel('car', rnd);
      car.userData.lights.visible = false;
      car.position.set(-82 + k * 5, 0, -70);
      this.scene.add(car);
    }
    this.obstacle(V(-72, -70), 13, 'parking');
  }

  buildSports() {
    const g = new THREE.Group();
    const trackMesh = new THREE.Mesh(new THREE.RingGeometry(17, 21, 64), this.std({ color: 0xa5432e, roughness: 0.95 }));
    trackMesh.rotation.x = -Math.PI / 2;
    trackMesh.scale.set(1.55, 1, 1);
    trackMesh.position.y = 0.08;
    const field = new THREE.Mesh(new THREE.CircleGeometry(17, 64), new THREE.MeshStandardMaterial({ map: lawnTexture(4, 4), roughness: 1 }));
    field.rotation.x = -Math.PI / 2;
    field.scale.set(1.55, 1, 1);
    field.position.y = 0.09;
    const lines = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(36, 20)), new THREE.LineBasicMaterial({ color: 0xffffff }));
    lines.rotation.x = -Math.PI / 2;
    lines.position.y = 0.12;
    g.add(trackMesh, field, lines);
    const post = this.std({ color: 0xffffff });
    for (const x of [-18, 18]) {
      const goal = new THREE.Group();
      goal.add(box(0.2, 2.2, 0.2, post, 0, 1.1, -3), box(0.2, 2.2, 0.2, post, 0, 1.1, 3), box(0.2, 0.2, 6.2, post, 0, 2.2, 0));
      goal.position.x = x;
      g.add(goal);
    }
    g.position.set(-56, 0, 62);
    this.scene.add(shadowed(g, false));
    this.pickable(g, 'Sports ground with running track');
    this.landmarks.push({ name: 'Sports ground', pos: V(-56, 62), forward: V(0, -1) });
    this.obstacle(V(-56, 62), 33, 'sports ground');
  }

  // ------------------------------------------------------------------ hostels
  buildHostels() {
    const hallStyle = {
      MVHR: ['#e3866b', 'MVHR'],
      SRKH: ['#d9765e', 'SRKH'],
      'Kalam Hall of Residence': ['#e59a7a', 'KALAM HALL'],
      'Kalpana Chawla Hall of Residence': ['#de8a74', 'KALPANA CHAWLA HALL'],
    };
    const red = this.std({ color: 0xa83a2c, roughness: 0.7 });
    const roof = this.std({ color: 0xc9b7a6, roughness: 0.9 });
    const tankMat = this.std({ color: 0x1d1f22, roughness: 0.6 });
    const ledge = this.std({ color: 0xf0e3d8 });
    this.sites.filter((s) => s.kind === 'hostel').forEach((site, i) => {
      const gate = site.ref;
      const [base, hallName] = hallStyle[gate.hall] || ['#e3866b', (gate.hall || gate.id).toUpperCase()];
      const side = this.facadeMaterial(facade({ base, frame: '#8e2f24', cols: 10, rows: 7, lit: 0.62, seed: 100 + i }), 0.95);
      const g = new THREE.Group();
      g.add(box(23, 0.8, 11, this.std({ color: 0x9a8a7a }), 0, 0.4, 0));
      const body = new THREE.Mesh(new THREE.BoxGeometry(22, 24, 10), [side, side, roof, roof, side, side]);
      body.position.y = 12.8;
      g.add(body);
      for (const x of [-11, 11]) g.add(box(1.2, 25, 10.6, red, x, 12.9, 0));
      g.add(box(23.4, 1, 10.8, red, 0, 25.3, 0));
      for (let k = 1; k <= 6; k += 1) g.add(box(18, 0.3, 1.3, ledge, 0, 0.8 + k * 3.4, 5.6));
      for (const x of [-6, 6]) g.add(cyl(1.1, 1.1, 1.8, tankMat, x, 26.7, -2, 14));
      g.add(box(7, 0.4, 3.4, ledge, 0, 4.2, 6.6));
      for (const x of [-3.2, 3.2]) g.add(cyl(0.3, 0.3, 4.2, red, x, 2.1, 7.9, 10));
      const wing = gate.name.match(/Wing\s+([AB])$/i)?.[1] || '';
      const plateTex = textTexture(`${hallName}${wing ? `  ·  WING ${wing}` : ''}`, { bg: '#7c2d12', w: 1024, h: 128, size: 72 });
      const plate = new THREE.Mesh(new THREE.PlaneGeometry(16, 2), this.glowingMap(plateTex, 0.75));
      plate.position.set(0, 23.4, 5.06);
      g.add(plate);
      this.glowSprite(g, 0, 3.6, 8.4, 6, this.warmGlow);
      g.position.copy(site.pos);
      g.lookAt(site.pos.clone().add(site.forward));
      this.scene.add(shadowed(g));
      this.label(gate.id, 'hostel', 31, g);
      this.zoomTag(g, site.pos, 35, gate.name, `${this.world.campus || 'Campus'} hostel · riders hand over at the wing entrance`, null);
      this.pickable(g, `${gate.name} (${this.world.campus || 'campus'} hostel)`);
      site.roofY = 27;
    });
  }

  // ------------------------------------------------------------------ restaurants and Instamart
  restaurant(site, i) {
    const s = site.ref;
    const brand = brandFor(s.name);
    const logo = emblem(brand);
    const g = new THREE.Group();
    const bases = ['#c96f3b', '#b5543c', '#d08c45', '#9c6644', '#c26a5a', '#b88a4a', '#a8735a', '#7f8f5a', '#6f7f9a'];
    const base = bases[i % bases.length];
    const wall = this.std({ color: base, roughness: 0.85 });
    const frontMat = this.facadeMaterial(shopFront(base, i + 11), 1.0);
    const roofMat = this.std({ color: 0x5b4636, roughness: 0.9 });
    const trim = this.std({ color: 0x3a2a20, roughness: 0.7 });
    const storeys = 1 + (hashString(s.name) % 2);
    g.add(box(11, 0.6, 10, this.std({ color: 0x8a8378 }), 0, 0.3, 0));
    const ground = new THREE.Mesh(new THREE.BoxGeometry(10, 6.5, 9), [wall, wall, roofMat, roofMat, frontMat, wall]);
    ground.position.y = 3.85;
    g.add(ground);
    if (storeys > 1) {
      const upper = this.facadeMaterial(facade({ base, frame: '#3a2a20', cols: 3, rows: 2, lit: 0.6, seed: 400 + i, w: 256, h: 256 }), 0.9);
      const up = new THREE.Mesh(new THREE.BoxGeometry(10, 5, 9), [upper, upper, roofMat, roofMat, upper, upper]);
      up.position.y = 9.6;
      g.add(up);
    }
    const top = storeys > 1 ? 12.1 : 7.1;
    g.add(box(10.4, 0.6, 9.4, trim, 0, top + 0.3, 0));
    const awning = new THREE.Mesh(
      new THREE.BoxGeometry(10.6, 0.3, 3),
      new THREE.MeshStandardMaterial({ map: stripeTexture([brand.color, '#f7f3e3', brand.color, '#f7f3e3', brand.color, '#f7f3e3']), roughness: 0.8 }),
    );
    awning.position.set(0, 5.5, 5.7);
    awning.rotation.x = 0.35;
    const sign = new THREE.Mesh(new THREE.BoxGeometry(10.4, 2.6, 0.4), [trim, trim, trim, trim, this.glowingMap(signTexture(s.name, brand), 0.85), trim]);
    sign.position.set(0, top + 2, 4.3);
    const logoMat = this.glowingMap(logo.texture, 0.7);
    const blade = new THREE.Mesh(new THREE.CylinderGeometry(1.3, 1.3, 0.25, 32), [trim, logoMat, logoMat]);
    blade.rotation.z = Math.PI / 2;
    blade.position.set(5.6, 6, 3.6);
    g.add(awning, sign, blade, box(2, 1.2, 1.6, this.std({ color: 0xb8bec8 }), -2.5, top + 1.2, -2));
    const tableMat = this.std({ color: 0xe8e1d5 });
    const umbrellaMat = this.std({ color: brand.color });
    const chairMat = this.std({ color: 0x6b4f3a });
    for (const x of [-3.6, 3.4]) {
      g.add(cyl(0.7, 0.7, 0.12, tableMat, x, 1.1, 7.6), cyl(0.08, 0.08, 1.1, tableMat, x, 0.55, 7.6, 6));
      const umbrella = new THREE.Mesh(new THREE.ConeGeometry(1.5, 0.8, 16), umbrellaMat);
      umbrella.position.set(x, 2.9, 7.6);
      g.add(umbrella, cyl(0.05, 0.05, 2.6, tableMat, x, 1.6, 7.6, 6));
      for (const dz of [-0.95, 0.95]) g.add(box(0.5, 0.6, 0.5, chairMat, x, 0.3, 7.6 + dz));
    }
    const stand = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1.3, 0.1), [trim, trim, trim, trim, new THREE.MeshStandardMaterial({ map: this.menuTex }), trim]);
    stand.position.set(-0.9, 0.95, 6.6);
    stand.rotation.x = -0.15;
    g.add(stand);
    this.glowSprite(g, 0, top + 2, 5, 15);
    g.scale.setScalar(1.4);
    site.roofY = (top + 4) * 1.4;
    site.brand = brand;
    const where = s.locality ? `${s.locality}, ${this.world.city || 'Kurnool'}` : this.world.city || '';
    this.zoomTag(g, site.pos, top + 6, s.name, where ? `${brand.tagline} · ${where}` : brand.tagline, logo.url);
    return g;
  }

  instamart(site) {
    const s = site.ref;
    const where = s.name.replace(/^Instamart\s*/i, '') || 'Hub';
    const blue = new THREE.Color(INSTAMART_BLUE);
    const navy = this.std({ color: 0x16378f });
    const g = new THREE.Group();
    g.add(box(30, 0.8, 21, this.std({ color: 0x8c949c }), 0, 0.4, 0));
    g.add(box(26, 10, 18, this.std({ color: 0xf1f4fb, roughness: 0.7 }), 0, 5.8, 0));
    g.add(box(27, 1.2, 19, this.std({ color: blue, roughness: 0.5, metalness: 0.2 }), 0, 11.4, 0));
    g.add(box(26.2, 1.6, 18.2, this.std({ color: blue }), 0, 8.8, 0));
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(20, 5), this.glowingMap(instamartSign(where), 0.6));
    sign.position.set(0, 14.2, 9.25);
    g.add(box(20.4, 5.4, 0.4, navy, 0, 14.2, 9), sign);
    const shutter = this.litMaterial({ color: 0xa7b1bc, emissive: 0x3b4a5c, emissiveIntensity: 0.5 }, 0.5);
    for (let d = -1; d <= 1; d += 1) {
      const door = new THREE.Mesh(new THREE.PlaneGeometry(5.6, 5.8), shutter);
      door.position.set(d * 7.6, 3.7, 9.02);
      g.add(door, box(6.2, 0.5, 0.6, navy, d * 7.6, 6.8, 9.2));
    }
    const solar = this.std({ color: 0x1e3a8a, metalness: 0.6, roughness: 0.25 });
    for (let x = -9; x <= 9; x += 6) {
      for (const z of [-4.5, 1.5]) {
        const p = box(5, 0.2, 4.4, solar, x, 12.6, z);
        p.rotation.x = -0.35;
        g.add(p);
      }
    }
    const crate = this.std({ color: 0xc58b4a, roughness: 0.9 });
    for (const [x, y, z] of [[-12.6, 0.9, 10.6], [-11.2, 0.9, 10.6], [-11.9, 2.1, 10.6]]) g.add(box(1.2, 1.2, 1.2, crate, x, y, z));
    const bag = new THREE.MeshStandardMaterial({ map: this.bagTex('instamart') });
    const scooterMat = this.std({ color: 0x2b2f36 });
    for (const x of [10.5, 12, 13.5]) {
      const scoot = new THREE.Group();
      scoot.add(box(0.6, 0.6, 1.8, scooterMat, 0, 0.7, 0), box(0.9, 0.8, 0.8, bag, 0, 1.35, -0.55));
      scoot.position.set(x, 0.4, 10.6);
      g.add(scoot);
    }
    site.roofY = 17;
    this.label(s.name, 'grocery', 21, g);
    const logo = emblem({ color: INSTAMART_BLUE, icon: 'bowl', initials: 'IM' });
    this.zoomTag(g, site.pos, 17, s.name, `Instamart dark store · ${where}${this.world.city ? `, ${this.world.city}` : ''}`, logo.url);
    return g;
  }

  buildStores() {
    this.siteByStore = new Map();
    this.sites.filter((s) => s.kind === 'food' || s.kind === 'grocery').forEach((site, i) => {
      const g = site.kind === 'grocery' ? this.instamart(site) : this.restaurant(site, i);
      g.position.copy(site.pos);
      g.lookAt(site.pos.clone().add(site.forward));
      this.scene.add(shadowed(g));
      this.pickable(g, `${site.ref.name} · ${site.kind === 'food' ? `${site.brand.tagline}, ${site.ref.locality}` : 'Instamart dark store'}`);
      this.siteByStore.set(site.ref.id, site);
    });
  }

  // ------------------------------------------------------------------ city
  buildCity() {
    const rnd = mulberry32(42);
    const palettes = [['#c9b79c', '#5b4a3a'], ['#b7c2c9', '#3b4650'], ['#d8c9a8', '#6b5a3a'], ['#c7a99a', '#5a3d32'], ['#a9b8a0', '#3f4d38'], ['#e0d2bd', '#6b5d4b']];
    const facades = palettes.map(([base, frame], k) => this.facadeMaterial(facade({ base, frame, cols: 4, rows: 5, lit: 0.5, seed: 500 + k, w: 256, h: 256 }), 0.8));
    const shops = ['Sri Lakshmi Textiles', 'Balaji Mobiles', 'Venkateswara Medicals', 'Sai Ram Electricals', 'Kurnool Opticals',
      'Mahalakshmi Jewellers', 'Raghavendra Hardware', 'Sri Sai Xerox', 'Ganesh Footwear', 'Vasavi Book Centre', 'Srinivasa Kirana', 'Anjaneya Tailors'];
    const boardColors = ['#1e3a8a', '#7c2d12', '#14532d', '#4c1d95', '#0f766e', '#9a3412'];
    const boards = shops.map((n, k) => this.glowingMap(textTexture(n, { bg: boardColors[k % 6], w: 512, h: 96, size: 44 }), 0.7));
    const roofMat = this.std({ color: 0x8c8074, roughness: 0.9 });
    const tank = this.std({ color: 0x1d1f22 });
    const [ccx, ccz] = this.plan.city.center;
    this.cityBoxes = [];
    for (const slot of this.plan.free_slots || []) {
      const p = V(slot.x, slot.z);
      const central = 1 - Math.min(1, Math.hypot(p.x - ccx, p.z - ccz) / 170);
      const h = 7 + rnd() * 6 + central * 16;
      const w = 15 + rnd() * 3;
      const d = 13 + rnd() * 2;
      const f = facades[Math.floor(rnd() * facades.length)];
      const g = new THREE.Group();
      const body = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), [f, f, roofMat, roofMat, f, f]);
      body.position.y = h / 2;
      g.add(body, box(w + 0.4, 0.5, d + 0.4, roofMat, 0, h + 0.25, 0));
      const board = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.8, 1.6), boards[Math.floor(rnd() * boards.length)]);
      board.position.set(0, 4.2, d / 2 + 0.05);
      g.add(board);
      if (rnd() < 0.5) g.add(cyl(0.9, 0.9, 1.4, tank, (rnd() - 0.5) * w * 0.6, h + 1.2, (rnd() - 0.5) * d * 0.5, 12));
      g.position.copy(p);
      g.lookAt(p.clone().add(V(slot.fx, slot.fz)));
      this.scene.add(shadowed(g));
      this.cityBoxes.push({ p, half: Math.hypot(w, d) / 2 });
      this.obstacle(p, Math.max(w, d) / 2, 'building');
    }
    // Konda Reddy Fort (Kondareddy Buruju), the old-town landmark
    const [fx, fz] = this.plan.fort;
    const fort = new THREE.Group();
    const stone = new THREE.MeshStandardMaterial({ map: stoneTexture('#8f8172', 6, 2), roughness: 0.95 });
    const lower = new THREE.Mesh(new THREE.CylinderGeometry(9, 10.5, 15, 8), stone);
    lower.position.y = 7.5;
    const upperT = new THREE.Mesh(new THREE.CylinderGeometry(5.2, 5.8, 7, 8), stone);
    upperT.position.y = 18.5;
    fort.add(lower, upperT);
    for (const [r, y, n] of [[9, 15.6, 16], [5.2, 22.4, 10]]) {
      for (let k = 0; k < n; k += 1) {
        const a = (k / n) * Math.PI * 2;
        fort.add(box(1.4, 1.4, 1.1, stone, Math.sin(a) * r, y, Math.cos(a) * r));
      }
    }
    const arch = new THREE.Mesh(new THREE.PlaneGeometry(3, 4.6), this.litMaterial({ color: 0x2a1d14, emissive: 0xffb060, emissiveIntensity: 0.4 }, 0.4));
    arch.position.set(0, 2.3, 10.2);
    fort.add(arch);
    this.glowSprite(fort, 0, 8, 12, 22, this.warmGlow);
    fort.position.set(fx, 0, fz);
    this.scene.add(shadowed(fort));
    this.pickable(fort, 'Konda Reddy Fort (Kondareddy Buruju), Old Town Kurnool');
    this.zoomTag(fort, V(fx, fz), 27, 'Konda Reddy Fort', 'Kondareddy Buruju · Old Town, Kurnool', null);
    this.landmarks.push({ name: 'Konda Reddy Fort', pos: V(fx, fz), forward: V(0, 1) });
    this.obstacle(V(fx, fz), 11, 'fort');
    this.placeLabel(`${this.world.city || 'Kurnool'} city`, 'city', V(ccx, ccz - 20), 46);
  }

  buildPGsAndVillage() {
    for (const [i, site] of this.sites.filter((s) => s.kind === 'pg').entries()) {
      const gate = site.ref;
      const side = this.facadeMaterial(facade({ base: '#c9b8d9', frame: '#5d4f70', cols: 4, rows: 6, lit: 0.6, seed: 300 + i, door: true }), 0.95);
      const roof = this.std({ color: 0x5d4f70 });
      const g = new THREE.Group();
      g.add(box(12, 0.8, 12, this.std({ color: 0x8a8296 }), 0, 0.4, 0));
      const body = new THREE.Mesh(new THREE.BoxGeometry(11, 18, 11), [side, side, roof, roof, side, side]);
      body.position.y = 9.8;
      g.add(body);
      const balcony = this.std({ color: 0x8f80a3 });
      for (let k = 1; k <= 3; k += 1) g.add(box(9, 0.45, 1.6, balcony, 0, k * 4.6 + 0.8, 6.3));
      const plate = new THREE.Mesh(new THREE.PlaneGeometry(8, 1.4), this.glowingMap(textTexture(gate.name.toUpperCase(), { bg: '#4c1d95', w: 512, h: 96, size: 48 }), 0.7));
      plate.position.set(0, 3, 5.56);
      g.add(plate);
      g.position.copy(site.pos);
      g.lookAt(site.pos.clone().add(site.forward));
      this.scene.add(shadowed(g));
      this.label(gate.id, 'pg', 24, g);
      this.zoomTag(g, site.pos, 27, gate.name, 'Paying-guest hostel opposite the campus gate', null);
      this.pickable(g, `${gate.name} (off-campus PG near ${this.world.campus || 'the campus'})`);
      site.roofY = 20;
    }
    // village houses and roadside shops outside the campus
    const rnd = mulberry32(77);
    const colors = [0xe8d7b0, 0xd6e4ee, 0xf0c9b8, 0xcfe3c4, 0xe9e1f2, 0xf3e3a6];
    const houses = [];
    const zones = [[-240, -125, -70, 0], [-205, -60, -205, -135], [-130, -40, -205, -60]];
    for (const [x0, x1, z0, z1] of zones) {
      for (let k = 0; k < 400 && houses.length < 80; k += 1) {
        const p = V(x0 + rnd() * (x1 - x0), z0 + rnd() * (z1 - z0));
        const w = 5 + rnd() * 4;
        if (!this.clearOf(p, w * 0.75, 3) || this.insideCampus(p, -6)) continue;
        if (houses.some((h) => h.p.distanceTo(p) < h.r + w * 0.75 + 2)) continue;
        houses.push({ p, r: w * 0.75, w, h: 3.5 + rnd() * 4, c: colors[Math.floor(rnd() * colors.length)], rot: Math.floor(rnd() * 4) * (Math.PI / 2) });
      }
    }
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({ roughness: 0.9 }), houses.length);
    const roofs = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), this.std({ color: 0x9c4f3a, roughness: 0.9 }), houses.length);
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const col = new THREE.Color();
    houses.forEach((h, i) => {
      q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), h.rot);
      mesh.setMatrixAt(i, m.compose(new THREE.Vector3(h.p.x, h.h / 2, h.p.z), q, new THREE.Vector3(h.w, h.h, h.w * 0.8)));
      mesh.setColorAt(i, col.setHex(h.c));
      roofs.setMatrixAt(i, m.compose(new THREE.Vector3(h.p.x, h.h + 0.3, h.p.z), q, new THREE.Vector3(h.w + 0.4, 0.6, h.w * 0.8 + 0.4)));
      this.obstacle(h.p, h.r, 'house');
    });
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    roofs.castShadow = true;
    this.scene.add(mesh, roofs);
    this.houses = houses;
  }

  // ------------------------------------------------------------------ Jagannathagattu hills, temple, boulders
  buildHills() {
    const hills = [
      [178, -32, 40, 32], [182, 75, 48, 44], [128, 185, 52, 38], [25, 190, 46, 28],
      [-300, 70, 42, 26], [-335, -110, 48, 30], [235, -170, 58, 42], [260, 40, 46, 36],
    ];
    this.hills = [];
    const rnd = mulberry32(99);
    const colA = new THREE.Color(0x9a6b4f);
    const colB = new THREE.Color(0x7d7468);
    const colC = new THREE.Color(0x4f6b32);
    for (const [x, z, r, h] of hills) {
      const p = V(x, z);
      if (!this.clearOf(p, r, 4) || this.insideCampus(p, -r)) continue;
      const geo = new THREE.SphereGeometry(r, 36, 14, 0, Math.PI * 2, 0, Math.PI / 2);
      const pos = geo.attributes.position;
      const colors = [];
      for (let k = 0; k < pos.count; k += 1) {
        const vx = pos.getX(k);
        const vy = pos.getY(k);
        const vz = pos.getZ(k);
        const n = 1 + Math.sin(vx * 0.21 + x) * Math.cos(vz * 0.17 + z) * 0.08 + (rnd() - 0.5) * 0.05;
        pos.setXYZ(k, vx * n, vy * (h / r) * (0.92 + rnd() * 0.16), vz * n);
        const c = colA.clone().lerp(colB, Math.min(1, (vy / r) * 1.3 + rnd() * 0.2));
        if (rnd() < 0.45) c.lerp(colC, 0.55 + rnd() * 0.3);
        colors.push(c.r, c.g, c.b);
      }
      geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 1 }));
      mesh.position.set(x, -0.4, z);
      this.scene.add(shadowed(mesh));
      this.hills.push({ p, r, h });
      this.obstacle(p, r, 'hill');
    }
    // granite boulders: on the hills and in the rocky strip behind the east hostels
    const rocks = [];
    for (const hl of this.hills) {
      for (let k = 0; k < 26; k += 1) {
        const a = rnd() * Math.PI * 2;
        const d = rnd() * hl.r * 0.85;
        const y = hl.h * Math.sqrt(Math.max(0, 1 - (d / hl.r) ** 2)) - 1;
        rocks.push([hl.p.x + Math.sin(a) * d, y, hl.p.z + Math.cos(a) * d, 1.5 + rnd() * 3.5]);
      }
    }
    const pl = this.plan.plateau;
    if (pl) {
      const c = this.plan.campus;
      for (let k = 0; k < 2500 && rocks.length < this.hills.length * 26 + 260; k += 1) {
        const side = Math.floor(rnd() * 4);
        const band = side === 3 ? pl.slope_west : pl.slope;
        const d = pl.margin + 4 + rnd() * (band - 6);
        const t = rnd();
        const p = side === 0 ? V(c.x0 + t * (c.x1 - c.x0), c.z0 - d) : side === 1 ? V(c.x1 + d, c.z0 + t * (c.z1 - c.z0))
          : side === 2 ? V(c.x0 + t * (c.x1 - c.x0), c.z1 + d) : V(c.x0 - d, c.z0 + t * (c.z1 - c.z0));
        const sz = 1.2 + rnd() * 3;
        if (this.clearOf(p, sz * 1.3, 2)) {
          rocks.push([p.x, 0, p.z, sz]);
          this.obstacle(p, sz * 1.3, 'boulder');
        }
      }
    }
    for (let k = 0; k < 400 && rocks.length < this.hills.length * 26 + 520; k += 1) {
      const p = V(76 + rnd() * 20, -86 + rnd() * 176);
      const s = 1 + rnd() * 2.6;
      if (this.clearOf(p, s * 1.3, 2)) rocks.push([p.x, 0, p.z, s]);
    }
    const rockMesh = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 0), new THREE.MeshStandardMaterial({ roughness: 0.95, flatShading: true }), rocks.length);
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const col = new THREE.Color();
    rocks.forEach(([x, y, z, s], i) => {
      q.setFromEuler(new THREE.Euler(rnd() * 3, rnd() * 3, rnd() * 3));
      rockMesh.setMatrixAt(i, m.compose(new THREE.Vector3(x, y + s * 0.5, z), q, new THREE.Vector3(s * 1.3, s * 0.9, s)));
      rockMesh.setColorAt(i, col.setHSL(0.06 + rnd() * 0.03, 0.16, 0.3 + rnd() * 0.12));
      if (y <= 0.1) this.obstacle(V(x, z), s * 1.3, 'boulder');
    });
    rockMesh.castShadow = true;
    rockMesh.receiveShadow = true;
    this.scene.add(rockMesh);
    // hill temple
    const [tx, tz] = this.plan.temple;
    const hill = this.hills.find((hl) => hl.p.distanceTo(V(tx, tz)) < hl.r);
    const ty = hill ? hill.h - 1.2 : 0;
    const t = new THREE.Group();
    const white = this.std({ color: 0xf3efe6, roughness: 0.8 });
    const ochre = this.std({ color: 0xe0a64a, roughness: 0.7 });
    t.add(box(12, 1, 10, this.std({ color: 0xb9ad9c }), 0, 0.5, 0), box(6, 4.5, 6, white, 0, 3.2, 0));
    for (let k = 0; k < 5; k += 1) t.add(box(5.2 - k * 0.9, 1.4, 5.2 - k * 0.9, k % 2 ? ochre : white, 0, 6.1 + k * 1.4, 0));
    t.add(cyl(0.3, 0.55, 1.4, this.std({ color: 0xd4a017, metalness: 0.7, roughness: 0.3 }), 0, 13.4, 0, 10));
    t.add(box(5, 3.5, 3.5, white, 0, 2.75, 4.6));
    for (const x of [-2.2, 2.2]) t.add(cyl(0.25, 0.25, 3, ochre, x, 2.5, 6.6, 8));
    this.glowSprite(t, 0, 9, 0, 16, this.warmGlow);
    t.position.set(tx, ty, tz);
    t.rotation.y = -Math.PI / 2;
    this.scene.add(shadowed(t));
    this.pickable(t, 'Jagannathagattu hill temple, beside the campus');
    this.zoomTag(t, V(tx, tz), 18, 'Jagannathagattu temple', 'Hill temple next to IIITDM Kurnool', null);
    this.landmarks.push({ name: 'Jagannathagattu hill temple', pos: V(tx, tz), forward: V(-1, 0), y: ty });
    this.placeLabel('Jagannathagattu hills', 'hills', V(tx, tz + 40), 34);
  }

  // ------------------------------------------------------------------ street furniture
  buildLamps() {
    const spots = [];
    for (const r of this.roads) {
      const step = r.kind === 'highway' ? 34 : r.kind === 'campus' ? 22 : 30;
      const sides = r.kind === 'highway' ? [-1, 1] : [1];
      for (const [a, b] of r.segs) {
        const len = a.distanceTo(b);
        const dir = b.clone().sub(a).normalize();
        const n = V(-dir.z, dir.x);
        for (let s = step / 2; s < len; s += step) {
          for (const side of sides) {
            const p = a.clone().add(dir.clone().multiplyScalar(s)).add(n.clone().multiplyScalar(side * (r.width / 2 + 1.6)));
            if (this.clearOf(p, 0.6, 0.6) && !spots.some((q) => q.distanceTo(p) < 8)) spots.push(p);
          }
        }
      }
    }
    const poles = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.22, 0.3, 9, 8), this.std({ color: 0x4b5260, metalness: 0.4, roughness: 0.5 }), spots.length);
    const bulbMat = this.litMaterial({ color: 0xfff1c2, emissive: 0xffd27a, emissiveIntensity: 2.4 }, 2.4);
    const bulbs = new THREE.InstancedMesh(new THREE.SphereGeometry(0.75, 10, 8), bulbMat, spots.length);
    const m = new THREE.Matrix4();
    const glowPos = [];
    spots.forEach((p, i) => {
      poles.setMatrixAt(i, m.makeTranslation(p.x, 4.5, p.z));
      bulbs.setMatrixAt(i, m.makeTranslation(p.x, 9.2, p.z));
      glowPos.push(p.x, 9.2 + this.heightAt(p.x, p.z), p.z);
      this.obstacle(p, 0.6, 'lamp');
    });
    poles.castShadow = true;
    const glowGeo = new THREE.BufferGeometry();
    glowGeo.setAttribute('position', new THREE.Float32BufferAttribute(glowPos, 3));
    const glowPoints = new THREE.Points(glowGeo, new THREE.PointsMaterial({
      map: this.lampGlow.map, size: 9, sizeAttenuation: true, transparent: true, opacity: 0.55,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    glowPoints.userData.noLift = true;
    this.glows.push(glowPoints);
    this.scene.add(poles, bulbs, glowPoints);
    this.lampSpots = spots;
  }

  buildTrees() {
    const rnd = mulberry32(7);
    const trees = [];
    const tryAdd = (p, r = 2.6) => {
      if (!this.clearOf(p, r, 1.5)) return false;
      if (trees.some((t) => t.distanceTo(p) < 5.2)) return false;
      if (this.wallSegs.some(([a, b]) => segDist(p, a, b).d < r + 1)) return false;
      trees.push(p);
      return true;
    };
    const c = this.plan.campus;
    for (let k = 0; k < 5000 && trees.length < 230; k += 1) tryAdd(V(c.x0 + 4 + rnd() * (c.x1 - c.x0 - 8), c.z0 + 4 + rnd() * (c.z1 - c.z0 - 8)));
    const hw = this.roads.find((r) => r.kind === 'highway');
    if (hw) {
      for (let z = hw.pts[0].z + 10; z < hw.pts[1].z; z += 17) for (const side of [-1, 1]) tryAdd(V(hw.pts[0].x + side * (hw.width / 2 + 6), z));
    }
    const b = this.bounds;
    for (let k = 0; k < 7000 && trees.length < 760; k += 1) {
      const p = V(b.x0 + rnd() * (b.x1 - b.x0), b.z0 + rnd() * (b.z1 - b.z0));
      if (p.z < this.plan.river_z[1] + 8) continue;
      tryAdd(p);
    }
    this.treeSpots = trees;
    const trunks = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.3, 0.55, 3.4, 8), this.std({ color: 0x5b4030, roughness: 1 }), trees.length);
    const crownGeo = new THREE.IcosahedronGeometry(2.4, 1);
    const crownMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.95 });
    const crowns = new THREE.InstancedMesh(crownGeo, crownMat, trees.length * 3);
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const color = new THREE.Color();
    const lobes = [[0, 4.6, 0, 1], [1.3, 4.0, 0.6, 0.72], [-1.0, 4.1, -0.8, 0.76]];
    trees.forEach((p, i) => {
      const s = 0.75 + rnd() * 0.55;
      const turn = rnd() * Math.PI * 2;
      trunks.setMatrixAt(i, m.compose(new THREE.Vector3(p.x, 1.7 * s, p.z), q, new THREE.Vector3(s, s, s)));
      const hue = 0.22 + rnd() * 0.09;
      lobes.forEach(([dx, dy, dz, k], j) => {
        const x = dx * Math.cos(turn) - dz * Math.sin(turn);
        const z = dx * Math.sin(turn) + dz * Math.cos(turn);
        crowns.setMatrixAt(i * 3 + j, m.compose(new THREE.Vector3(p.x + x * s, dy * s, p.z + z * s), q, new THREE.Vector3(s * k, s * k * 0.85, s * k)));
        crowns.setColorAt(i * 3 + j, color.setHSL(hue + (rnd() - 0.5) * 0.02, 0.45, 0.24 + rnd() * 0.1 + j * 0.02));
      });
      this.obstacle(p, 1.2, 'tree');
    });
    trunks.castShadow = true;
    crowns.castShadow = true;
    crowns.receiveShadow = true;
    this.scene.add(trunks, crowns);
  }

  /** Image-based lighting from the sky (soft, natural shading on every material). */
  buildEnvironment() {
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const make = (mode) => {
      const sc = new THREE.Scene();
      sc.add(new THREE.Mesh(new THREE.SphereGeometry(100, 32, 16), new THREE.MeshBasicMaterial({ map: this.skies[mode], side: THREE.BackSide })));
      const ground = new THREE.Mesh(new THREE.CircleGeometry(100, 32), new THREE.MeshBasicMaterial({ color: mode === 'day' ? 0x6f7f4a : 0x161a12 }));
      ground.rotation.x = -Math.PI / 2;
      ground.position.y = -3;
      const sun = new THREE.Mesh(new THREE.SphereGeometry(9, 16, 8), new THREE.MeshBasicMaterial({ color: mode === 'day' ? 0xfff6e0 : 0xffa860 }));
      sun.position.set(-55, mode === 'day' ? 70 : 18, 30);
      sc.add(ground, sun);
      return pmrem.fromScene(sc, 0.035).texture;
    };
    this.envMaps = { day: make('day'), night: make('night') };
    pmrem.dispose();
  }

  /** Grass tufts, flowering bushes and farm fields so the countryside is green, not desert. */
  buildGreenery() {
    const rnd = mulberry32(31);
    const b = this.bounds;
    const { xs, zs } = this.plan.city;
    const inCity = (p) => p.x > xs[0] - 36 && p.x < xs[xs.length - 1] + 36 && p.z > zs[0] - 36 && p.z < zs[zs.length - 1] + 36;
    const [rz0, rz1] = this.plan.river_z;
    const big = this.obstacles.filter((o) => o.r >= 3 && o.what !== 'hill');
    const roadClear = (p, r) => this.roads.every((road) => road.segs.every(([a, c]) => segDist(p, a, c).d > road.width / 2 + 1.4 + r));
    // farm fields on the open plain
    const fields = [];
    for (let k = 0; k < 600 && fields.length < 16; k += 1) {
      const p = V(b.x0 + rnd() * (b.x1 - b.x0), b.z0 + rnd() * (b.z1 - b.z0));
      const w = 30 + rnd() * 40;
      const d = 24 + rnd() * 30;
      const r = Math.hypot(w, d) / 2;
      if (inCity(p) || this.insideCampus(p, -60) || p.z < rz1 + r || !roadClear(p, r)) continue;
      if (this.obstacles.some((o) => (o.r >= 3 || o.what === 'hill') && o.p.distanceTo(p) < o.r + r)) continue;
      if (fields.some((f) => f.p.distanceTo(p) < f.r + r + 4)) continue;
      fields.push({ p, r });
      const tex = fieldTexture(50 + rnd() * 45, w / 10, d / 10);
      const f = this.flat(w, d, new THREE.MeshStandardMaterial({ map: tex, color: 0xc8c8b8, roughness: 1 }), p.x, p.z, 0.04);
      f.rotation.z = Math.floor(rnd() * 2) * (Math.PI / 2);
      this.obstacle(p, r * 0.8, 'field');
    }
    // grass tufts
    const tufts = [];
    for (let k = 0; k < 26000 && tufts.length < 4500; k += 1) {
      const p = V(b.x0 + rnd() * (b.x1 - b.x0), b.z0 + rnd() * (b.z1 - b.z0));
      if (inCity(p) || (p.z < rz1 + 4 && p.z > rz0 - 8) || !roadClear(p, 0.4)) continue;
      if (big.some((o) => o.p.distanceTo(p) < o.r + 0.6)) continue;
      tufts.push(p);
    }
    const tuftMesh = new THREE.InstancedMesh(new THREE.ConeGeometry(0.45, 1.3, 5), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true }), tufts.length);
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const col = new THREE.Color();
    tufts.forEach((p, i) => {
      const s = 0.6 + rnd() * 0.9;
      q.setFromEuler(new THREE.Euler((rnd() - 0.5) * 0.4, rnd() * 3, (rnd() - 0.5) * 0.4));
      tuftMesh.setMatrixAt(i, m.compose(new THREE.Vector3(p.x, 0.5 * s, p.z), q, new THREE.Vector3(s, s, s)));
      tuftMesh.setColorAt(i, col.setHSL(0.2 + rnd() * 0.08, 0.45 + rnd() * 0.2, 0.25 + rnd() * 0.15));
    });
    tuftMesh.receiveShadow = true;
    // flowering and green bushes (bougainvillea, crotons, lantana)
    const bushes = [];
    for (let k = 0; k < 9000 && bushes.length < 900; k += 1) {
      const p = V(b.x0 + rnd() * (b.x1 - b.x0), b.z0 + rnd() * (b.z1 - b.z0));
      if (inCity(p) || p.z < rz1 + 6 || !roadClear(p, 1.6)) continue;
      if (this.obstacles.some((o) => o.what !== 'field' && o.what !== 'hill' && o.p.distanceTo(p) < o.r + 1.6)) continue;
      if (bushes.some((x) => x.distanceTo(p) < 3)) continue;
      bushes.push(p);
    }
    const bushMesh = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1.2, 1), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.95, flatShading: true }), bushes.length);
    const flowers = [0xd63384, 0xe85d9c, 0xf2a03d, 0xf4e04d, 0xb23a8f];
    bushes.forEach((p, i) => {
      const s = 0.7 + rnd() * 0.8;
      bushMesh.setMatrixAt(i, m.compose(new THREE.Vector3(p.x, 0.8 * s, p.z), q.setFromEuler(new THREE.Euler(0, rnd() * 3, 0)), new THREE.Vector3(s * 1.2, s * 0.8, s)));
      bushMesh.setColorAt(i, rnd() < 0.3 ? col.setHex(flowers[Math.floor(rnd() * flowers.length)]) : col.setHSL(0.27 + rnd() * 0.07, 0.5, 0.22 + rnd() * 0.1));
    });
    bushMesh.castShadow = true;
    bushMesh.receiveShadow = true;
    this.scene.add(tuftMesh, bushMesh);
    this.detail = [tuftMesh];
  }

  // ------------------------------------------------------------------ vehicles
  /** Ambient vehicle model; local +z is the front. */
  vehicleModel(kind, rnd) {
    const g = new THREE.Group();
    const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
    this.vehMats = this.vehMats || {
      dark: this.std({ color: 0x1b1d21, roughness: 0.6 }),
      glass: this.std({ color: 0x24303d, metalness: 0.5, roughness: 0.2 }),
      head: new THREE.SpriteMaterial({ map: glowTexture('rgba(255,244,210,1)'), blending: THREE.AdditiveBlending, depthWrite: false }),
      tail: new THREE.SpriteMaterial({ map: glowTexture('rgba(255,60,40,1)'), blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.8 }),
    };
    const { dark, glass } = this.vehMats;
    const wheel = (x, z, r = 0.38) => {
      const w = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 0.3, 12), dark);
      w.rotation.z = Math.PI / 2;
      w.position.set(x, r, z);
      return w;
    };
    let len = 4.2;
    let width = 2;
    if (kind === 'car') {
      const paint = this.std({ color: pick([0xf2f2f2, 0xb8bcc2, 0xb22222, 0x1f4f8f, 0x2b2b2b, 0x7a1f2b, 0xd9d4c5]), metalness: 0.6, roughness: 0.3 });
      const [bodyGeo, cabinGeo] = this.carGeometry();
      g.add(new THREE.Mesh(bodyGeo, paint), new THREE.Mesh(cabinGeo, glass));
      for (const x of [-0.88, 0.88]) for (const z of [-1.35, 1.35]) g.add(wheel(x, z, 0.36));
    } else if (kind === 'auto') {
      const body = this.std({ color: pick([0xf2c200, 0x2e8b3d]), roughness: 0.5 });
      g.add(limb(P3(0, 0.85, -0.9), P3(0, 0.85, 0.75), 0.48, body));
      g.children[g.children.length - 1].scale.set(1.5, 1, 1);
      g.add(box(1.5, 0.12, 2.3, this.std({ color: 0x111111 }), 0, 2.15, -0.1));
      for (const [x, z] of [[-0.68, -1], [0.68, -1], [-0.68, 0.9], [0.68, 0.9]]) g.add(box(0.07, 1.2, 0.07, dark, x, 1.6, z));
      g.add(box(1.3, 0.6, 0.06, glass, 0, 1.6, 1.0));
      g.add(wheel(0, 1.1, 0.28), wheel(-0.62, -0.95, 0.28), wheel(0.62, -0.95, 0.28));
      len = 2.8;
      width = 1.6;
    } else if (kind === 'bus') {
      g.add(box(2.5, 2.9, 11, this.std({ color: 0xf3efe4 }), 0, 1.9, 0), box(2.52, 0.5, 11, this.std({ color: 0x2f8f4e }), 0, 1.0, 0));
      this.busBoard = this.busBoard || this.glowingMap(textTexture('KURNOOL', { bg: '#111', fg: '#ffb020', w: 256, h: 64, size: 40 }), 0.9);
      g.add(box(2.54, 0.9, 9.6, glass, 0, 2.5, -0.3), box(2.3, 0.5, 0.1, this.busBoard, 0, 3.0, 5.52));
      for (const x of [-1.1, 1.1]) for (const z of [-3.8, 3.6]) g.add(wheel(x, z, 0.5));
      len = 11;
      width = 2.5;
    } else if (kind === 'truck') {
      const cab = this.std({ color: pick([0xe07b1f, 0x1f5fa8, 0xc0392b, 0x2e8b57]), roughness: 0.5 });
      this.truckCargo = this.truckCargo || new THREE.MeshStandardMaterial({ map: stripeTexture(['#e9c46a', '#e76f51', '#2a9d8f', '#e9c46a', '#264653']), roughness: 0.7 });
      g.add(box(2.4, 2.3, 2.2, cab, 0, 1.6, 3.2), box(2.2, 0.8, 0.1, glass, 0, 2.2, 4.32), box(2.5, 2.8, 6, this.truckCargo, 0, 2.0, -1.2));
      for (const x of [-1.05, 1.05]) for (const z of [-3.2, -1.2, 3.2]) g.add(wheel(x, z, 0.5));
      len = 8.6;
      width = 2.5;
    } else {
      // motorbike with a rider in everyday clothes (never Swiggy orange or Instamart blue)
      const person = new THREE.Group();
      this.makeScooter(person, this.std({ color: pick([0x111111, 0x8b0000, 0x1c3d6e, 0x5a5a5a]), metalness: 0.5, roughness: 0.35 }), true);
      this.makePerson(person, {
        shirt: this.std({ color: pick([0xf5f5f5, 0x9b2335, 0x2e7d32, 0x6d6d6d, 0x5d4037, 0xe6d690, 0x37474f]), roughness: 0.7 }),
        pants: this.std({ color: pick([0x2f3542, 0x3e4a61, 0x4a3b2a]), roughness: 0.8 }),
        skin: this.std({ color: pick([0x8d5a3b, 0x7a4a2e, 0xa56b46, 0x6b4128]), roughness: 0.7 }),
        helmet: this.std({ color: pick([0x1f2328, 0xe9e9e9, 0x8b0000, 0x1c3d6e]), metalness: 0.2, roughness: 0.3 }),
      });
      person.scale.setScalar(1.15);
      g.add(person);
      len = 2.3;
      width = 0.8;
    }
    const lights = new THREE.Group();
    for (const x of width > 1 ? [-width / 3, width / 3] : [0]) {
      const h = new THREE.Sprite(this.vehMats.head);
      h.position.set(x, 0.9, len / 2 + 0.3);
      h.scale.set(1.4, 1.4, 1);
      const t = new THREE.Sprite(this.vehMats.tail);
      t.position.set(x, 0.9, -len / 2 - 0.1);
      t.scale.set(0.8, 0.8, 1);
      lights.add(h, t);
    }
    this.mergeLocal(g);
    g.add(lights);
    g.userData = { len, width, lights };
    g.scale.setScalar(1.25);
    return g;
  }

  /** Rounded car body and glass cabin (extruded side profiles), shared by all cars. */
  carGeometry() {
    if (this.carGeos) return this.carGeos;
    const body = new THREE.Shape();
    body.moveTo(-2.1, 0.3);
    body.lineTo(1.95, 0.3);
    body.quadraticCurveTo(2.15, 0.32, 2.12, 0.62);
    body.quadraticCurveTo(2.05, 0.9, 1.3, 0.95);
    body.lineTo(-1.6, 0.98);
    body.quadraticCurveTo(-2.12, 0.95, -2.12, 0.6);
    body.closePath();
    const cabin = new THREE.Shape();
    cabin.moveTo(-1.55, 0.9);
    cabin.lineTo(1.05, 0.9);
    cabin.quadraticCurveTo(0.6, 1.5, 0.3, 1.55);
    cabin.lineTo(-1.1, 1.55);
    cabin.quadraticCurveTo(-1.45, 1.5, -1.55, 0.9);
    const make = (shape, depth, bevel) => {
      const geo = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: true, bevelThickness: bevel, bevelSize: bevel, bevelSegments: 3, curveSegments: 10 });
      geo.rotateY(-Math.PI / 2);
      geo.translate(depth / 2, 0, 0);
      return geo;
    };
    this.carGeos = [make(body, 1.75, 0.1), make(cabin, 1.6, 0.06)];
    return this.carGeos;
  }

  /** Ambient traffic on the highway and city streets (India drives on the left). */
  buildTraffic() {
    const rnd = mulberry32(2024);
    this.trafficGroup = new THREE.Group();
    this.scene.add(this.trafficGroup);
    const { xs, zs } = this.plan.city;
    const loop = (x0, x1, z0, z1) => [V(x0, z0), V(x1, z0), V(x1, z1), V(x0, z1)];
    const paths = [];
    const hw = this.roads.find((r) => r.kind === 'highway');
    const highwayMix = ['truck', 'bus', 'car', 'car', 'bike', 'bike', 'auto'];
    if (hw) {
      const lane = hw.width / 2 - 2.6;
      paths.push({ pts: offsetLine(hw.pts, -lane), closed: false, n: 12, kinds: highwayMix });
      paths.push({ pts: offsetLine([...hw.pts].reverse(), -lane), closed: false, n: 12, kinds: highwayMix });
    }
    const streetLane = 14 / 2 - 2.4;
    const cityMix = ['car', 'auto', 'auto', 'bike', 'bike', 'car'];
    const rings = [loop(xs[0], xs[xs.length - 1], zs[0], zs[zs.length - 1]), loop(xs[1], xs[xs.length - 2], zs[1], zs[zs.length - 2])];
    rings.forEach((ring, k) => {
      for (const dir of [1, -1]) {
        const pts = dir > 0 ? ring : [...ring].reverse();
        paths.push({ pts: offsetLine(pts, -streetLane, true), closed: true, n: k === 0 ? 12 : 6, kinds: cityMix });
      }
    });
    const mid = zs[Math.floor(zs.length / 2)];
    const cp = this.roads.find((r) => r.id === 'checkpost');
    for (const line of [[V(xs[0], mid), V(xs[xs.length - 1], mid)], ...(cp ? [cp.pts] : [])]) {
      for (const dir of [1, -1]) {
        const pts = dir > 0 ? line : [...line].reverse();
        paths.push({ pts: offsetLine(pts, -streetLane), closed: false, n: 3, kinds: ['auto', 'bike', 'car'] });
      }
    }
    const speeds = { car: 11, auto: 7.5, bus: 8.5, truck: 7.5, bike: 10 };
    for (const p of paths) {
      const tr = track(p.pts, p.closed);
      for (let k = 0; k < p.n; k += 1) {
        const kind = p.kinds[Math.floor(rnd() * p.kinds.length)];
        const group = this.vehicleModel(kind, rnd);
        this.trafficGroup.add(group);
        const v = {
          kind,
          group,
          tr,
          s: (tr.total * (k + rnd() * 0.4)) / p.n,
          speed: speeds[kind] * (0.9 + rnd() * 0.2),
          cur: 0,
          len: group.userData.len * 1.25,
          lights: group.userData.lights,
          pos: V(0, 0),
          dir: V(0, 1),
          wait: 0,
          ghost: 0,
        };
        sampleTrack(tr, v.s, v.pos, v.dir);
        group.position.copy(v.pos);
        group.rotation.y = Math.atan2(v.dir.x, v.dir.z);
        group.traverse((o) => { o.castShadow = false; });
        this.traffic.push(v);
      }
    }
    // one instanced draw call of soft contact shadows for every vehicle and rider
    this.blobs = new THREE.InstancedMesh(
      new THREE.CircleGeometry(1, 20).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ map: glowTexture('rgba(0,0,0,1)'), color: 0x000000, transparent: true, opacity: 0.45, depthWrite: false }),
      this.traffic.length + 300,
    );
    this.blobs.frustumCulled = false;
    this.blobs.renderOrder = 1;
    this.blobs.userData.noLift = true;
    this.scene.add(this.blobs);
  }

  /** Place the contact shadows under every vehicle and rider (one draw call). */
  updateBlobs() {
    const m = this.blobMatrix || (this.blobMatrix = new THREE.Matrix4());
    const q = this.blobQuat || (this.blobQuat = new THREE.Quaternion());
    const sc = this.blobScale || (this.blobScale = new THREE.Vector3());
    const p = this.blobPos || (this.blobPos = new THREE.Vector3());
    let n = 0;
    for (const v of this.traffic) {
      const s = v.group.scale.x / 1.25;
      q.setFromAxisAngle(UP, v.group.rotation.y);
      p.set(v.pos.x, v.group.position.y + 0.25, v.pos.z);
      m.compose(p, q, sc.set(v.group.userData.width * 0.9 * s, 1, v.len * 0.55 * s));
      this.blobs.setMatrixAt(n++, m);
    }
    for (const r of this.riders.values()) {
      if (n >= this.blobs.instanceMatrix.count) break;
      q.setFromAxisAngle(UP, r.group.rotation.y);
      p.copy(r.group.position);
      p.y = this.heightAt(p.x, p.z) + 0.26;
      m.compose(p, q, sc.set(1.4, 1, 2.8));
      this.blobs.setMatrixAt(n++, m);
    }
    this.blobs.count = n;
    this.blobs.instanceMatrix.needsUpdate = true;
  }

  /** Move ambient vehicles; each one brakes for anything just ahead in its lane. */
  stepTraffic(dt) {
    const movers = [
      ...this.traffic.map((v) => ({ v, pos: v.pos, len: v.len })),
      ...[...this.riders.values()].map((r) => ({ v: null, pos: r.group.position, len: 6 })),
    ];
    const tmp = V(0, 0);
    for (const v of this.traffic) {
      let blocked = false;
      if (v.ghost > 0) {
        v.ghost -= dt;
      } else {
        for (const o of movers) {
          if (o.v === v) continue;
          tmp.set(o.pos.x - v.pos.x, 0, o.pos.z - v.pos.z);
          const along = tmp.x * v.dir.x + tmp.z * v.dir.z;
          if (along <= 0) continue;
          const lateral = Math.abs(tmp.x * v.dir.z - tmp.z * v.dir.x);
          if (along < (v.len + o.len) / 2 + 3 && lateral < 2.6) {
            blocked = true;
            break;
          }
        }
      }
      if (blocked) {
        v.cur = Math.max(0, v.cur - dt * 30);
        v.wait += dt;
        if (v.wait > 3.5) {
          v.ghost = 1.2;
          v.wait = 0;
        }
      } else {
        v.cur = Math.min(v.speed, v.cur + dt * 9);
        v.wait = 0;
      }
      v.s += v.cur * dt;
      if (!v.tr.closed && v.s > v.tr.total) v.s -= v.tr.total;
      sampleTrack(v.tr, v.s, v.pos, v.dir);
      v.group.position.set(v.pos.x, this.heightAt(v.pos.x, v.pos.z), v.pos.z);
      const yaw = Math.atan2(v.dir.x, v.dir.z);
      v.group.rotation.y += Math.atan2(Math.sin(yaw - v.group.rotation.y), Math.cos(yaw - v.group.rotation.y)) * Math.min(1, dt * 10);
      if (!v.tr.closed) v.group.scale.setScalar(1.25 * Math.max(0.01, Math.min(1, Math.min(v.s, v.tr.total - v.s) / 12)));
    }
  }

  // ------------------------------------------------------------------ riders
  /**
   * Seated person (local +z forward, origin on the ground under the seat): legs, hips, torso
   * leaning forward, arms reaching the handlebar, neck, head and helmet. Returns the parts that
   * change with the uniform.
   */
  makePerson(g, { shirt, pants, skin, helmet, chestMap = null, backpack = null, reflect = null }) {
    for (const side of [-1, 1]) {
      const hip = P3(side * 0.15, 1.12, -0.12);
      const knee = P3(side * 0.2, 1.17, 0.33);
      const ankle = P3(side * 0.21, 0.68, 0.43);
      g.add(limb(hip, knee, 0.105, pants), limb(knee, ankle, 0.085, pants));
      g.add(box(0.15, 0.1, 0.3, this.std({ color: 0x1f1f22, roughness: 0.6 }), side * 0.21, 0.6, 0.5));
    }
    g.add(limb(P3(-0.15, 1.12, -0.12), P3(0.15, 1.12, -0.12), 0.13, pants));
    const torso = new THREE.Group();
    torso.position.set(0, 1.12, -0.12);
    torso.rotation.x = 0.28;
    const chest = new THREE.Mesh(new THREE.CapsuleGeometry(0.2, 0.36, 3, 10), shirt);
    chest.position.y = 0.34;
    chest.scale.set(1.2, 1, 0.82);
    torso.add(chest);
    const parts = { torso };
    if (reflect) {
      for (const y of [0.2, 0.28]) {
        const band = new THREE.Mesh(new THREE.CylinderGeometry(0.206, 0.206, 0.035, 12, 1, true), reflect);
        band.position.y = y;
        band.scale.set(1.2, 1, 0.82);
        torso.add(band);
      }
      const imBand = new THREE.Mesh(new THREE.CylinderGeometry(0.212, 0.212, 0.1, 12, 1, true), this.std({ color: INSTAMART_BLUE, roughness: 0.6 }));
      imBand.position.y = 0.44;
      imBand.scale.set(1.2, 1, 0.82);
      torso.add(imBand);
      parts.band = imBand;
    }
    if (chestMap) {
      const decal = new THREE.Mesh(new THREE.PlaneGeometry(0.3, 0.15), chestMap);
      decal.position.set(0, 0.36, 0.172);
      torso.add(decal);
      parts.decal = decal;
    }
    if (backpack) {
      const bag = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.52, 0.3), backpack);
      bag.position.set(0, 0.4, -0.29);
      torso.add(bag);
      parts.bag = bag;
    }
    g.add(torso);
    // arms: shoulder, elbow, hand on the grip
    for (const side of [-1, 1]) {
      const shoulder = P3(side * 0.25, 1.64, 0.03);
      const elbow = P3(side * 0.34, 1.43, 0.32);
      const hand = P3(side * 0.36, 1.41, 0.62);
      g.add(limb(shoulder, elbow, 0.068, shirt), limb(elbow, hand, 0.058, shirt));
      const fist = new THREE.Mesh(new THREE.SphereGeometry(0.06, 6, 4), skin);
      fist.position.copy(hand);
      g.add(fist);
    }
    g.add(limb(P3(0, 1.7, 0.05), P3(0, 1.8, 0.08), 0.06, skin));
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.135, 10, 8), skin);
    head.position.set(0, 1.9, 0.1);
    const lid = new THREE.Mesh(new THREE.SphereGeometry(0.17, 12, 6, 0, Math.PI * 2, 0, Math.PI * 0.62), helmet);
    lid.position.set(0, 1.92, 0.08);
    lid.scale.set(1, 1.05, 1.12);
    const visor = new THREE.Mesh(new THREE.SphereGeometry(0.172, 8, 3, -0.9, 1.8, Math.PI * 0.32, Math.PI * 0.22), this.std({ color: 0x15191f, metalness: 0.6, roughness: 0.15 }));
    visor.position.copy(lid.position);
    visor.scale.copy(lid.scale);
    g.add(head, lid, visor);
    return parts;
  }

  /** Scooter or motorbike body (local +z forward). */
  makeScooter(g, bodyMat, motorbike = false) {
    const dark = this.std({ color: 0x1b1d21, roughness: 0.6 });
    const chrome = this.std({ color: 0xcfd3d8, metalness: 0.9, roughness: 0.25 });
    for (const z of [0.74, -0.64]) {
      const w = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.28, 0.13, 14), dark);
      w.rotation.z = Math.PI / 2;
      w.position.set(0, 0.28, z);
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.15, 8), chrome);
      hub.rotation.z = Math.PI / 2;
      hub.position.copy(w.position);
      g.add(w, hub);
    }
    if (motorbike) {
      g.add(limb(P3(0, 0.72, -0.5), P3(0, 0.8, 0.45), 0.13, dark));
      g.add(limb(P3(0, 1.0, 0.05), P3(0, 1.02, 0.42), 0.17, bodyMat)); // fuel tank
      g.add(limb(P3(0, 0.98, -0.6), P3(0, 1.0, -0.05), 0.11, dark)); // seat
      g.add(limb(P3(0, 0.45, 0.72), P3(0, 1.3, 0.55), 0.035, chrome)); // fork
    } else {
      g.add(box(0.5, 0.1, 0.72, dark, 0, 0.45, 0.12)); // floorboard
      g.add(limb(P3(0, 0.74, -0.78), P3(0, 0.74, -0.12), 0.28, bodyMat)); // rear body
      g.add(limb(P3(0, 1.03, -0.62), P3(0, 1.03, -0.05), 0.12, dark)); // seat
      const apron = limb(P3(0, 0.52, 0.46), P3(0, 1.28, 0.58), 0.2, bodyMat);
      apron.scale.x = 1.45;
      g.add(apron);
      g.add(limb(P3(0, 0.42, 0.62), P3(0, 0.52, 0.86), 0.12, bodyMat)); // front mudguard
    }
    g.add(limb(P3(-0.4, 1.4, 0.62), P3(0.4, 1.4, 0.62), 0.03, dark));
    for (const side of [-1, 1]) g.add(limb(P3(side * 0.3, 1.4, 0.6), P3(side * 0.36, 1.6, 0.56), 0.014, chrome));
    const lightMat = this.litMaterial({ color: 0xfff6d8, emissive: 0xfff2c8, emissiveIntensity: 1.2 }, 1.2);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.08, 12, 8), lightMat);
    head.position.set(0, 1.36, 0.74);
    g.add(head, box(0.22, 0.1, 0.03, this.std({ color: 0xf2d22e }), 0, 0.78, -1.06));
  }

  /** Delivery rider: a person in the Swiggy uniform (or with the Instamart band) on a scooter. */
  makeRider(id = '') {
    const g = new THREE.Group();
    const h = hashString(id);
    const skins = [0x8d5a3b, 0x7a4a2e, 0xa56b46, 0x6b4128, 0xb27a53];
    const helmets = [0x1f2328, 0xfc8019, 0x2b2f36, 0xe9e9e9, 0x8b0000];
    const bodies = [0xeeeeee, 0x9aa1ad, 0x1f4f8f, 0xb22222, 0x2b2b2b];
    this.makeScooter(g, this.std({ color: bodies[(h >>> 3) % bodies.length], metalness: 0.4, roughness: 0.35 }));
    const shirt = this.std({ color: SWIGGY_ORANGE, roughness: 0.65 });
    const reflect = this.litMaterial({ color: 0xdfe6ee, emissive: 0xdfe6ee, emissiveIntensity: 0.6, metalness: 0.6, roughness: 0.3 }, 0.6);
    const chestMat = new THREE.MeshStandardMaterial({ map: this.shirtTex('swiggy'), roughness: 0.6 });
    const bagMat = new THREE.MeshStandardMaterial({ map: this.bagTex('swiggy'), roughness: 0.6 });
    const parts = this.makePerson(g, {
      shirt,
      pants: this.std({ color: 0x2f3542, roughness: 0.8 }),
      skin: this.std({ color: skins[h % skins.length], roughness: 0.7 }),
      helmet: this.std({ color: helmets[(h >>> 5) % helmets.length], metalness: 0.2, roughness: 0.3 }),
      chestMap: chestMat,
      backpack: bagMat,
      reflect,
    });
    const extra = new THREE.Mesh(new THREE.BoxGeometry(0.44, 0.34, 0.38), new THREE.MeshStandardMaterial({ map: this.bagTex('instamart'), roughness: 0.6 }));
    extra.position.set(0, 1.25, -0.92);
    g.add(extra);
    this.mergeLocal(g, new Set([parts.decal, parts.band, parts.bag, extra]));
    for (const o of [parts.bag, extra]) o.castShadow = true;
    const lamp = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture('rgba(255,244,200,1)'), blending: THREE.AdditiveBlending, depthWrite: false }));
    lamp.position.set(0, 1.36, 0.95);
    lamp.scale.set(1.2, 1.2, 1);
    // highlight: brand-coloured halo on the road and a glowing marker above the helmet
    const halo = new THREE.Mesh(
      new THREE.RingGeometry(0.75, 1.05, 40),
      new THREE.MeshBasicMaterial({ color: SWIGGY_ORANGE, transparent: true, opacity: 0.85, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }),
    );
    halo.rotation.x = -Math.PI / 2;
    halo.position.y = 0.12;
    const disc = new THREE.Mesh(
      new THREE.CircleGeometry(0.75, 32),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false }),
    );
    disc.rotation.x = -Math.PI / 2;
    disc.position.y = 0.11;
    this.markerTex = this.markerTex || glowTexture('rgba(255,255,255,1)');
    const marker = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.markerTex, color: SWIGGY_ORANGE, blending: THREE.AdditiveBlending, depthWrite: false }));
    marker.position.y = 2.55;
    marker.scale.set(0.9, 0.9, 1);
    g.add(lamp, halo, disc, marker);
    g.scale.setScalar(2.6);
    g.userData = { lamp, disc, halo, marker, band: parts.band, chestMat, bagMat, extra, wheels: [], markerY: 2.55 };
    return g;
  }

  shirtTex(role) {
    this.shirtCache = this.shirtCache || {};
    if (!this.shirtCache[role]) {
      this.shirtCache[role] = role === 'instamart' ? shirtTexture('instamart', SWIGGY_ORANGE, INSTAMART_BLUE) : shirtTexture('swiggy', SWIGGY_ORANGE, null);
    }
    return this.shirtCache[role];
  }

  bagTex(role) {
    this.bagCache = this.bagCache || {};
    if (!this.bagCache[role]) this.bagCache[role] = role === 'instamart' ? bagTexture('instamart', INSTAMART_BLUE) : bagTexture('swiggy', SWIGGY_ORANGE);
    return this.bagCache[role];
  }

  /** Uniform for a rider: Instamart when the trip only collects groceries, Swiggy otherwise. */
  roleFor(partnerId, view) {
    if (!view) return hashString(partnerId) % 4 === 0 ? 'instamart' : 'swiggy';
    const pickups = view.plan.stops.filter((s) => s.type === 'PICKUP').map((s) => this.storeCategory(s));
    if (pickups.length && pickups.every((c) => c === 'GROCERY')) return 'instamart';
    return pickups.some((c) => c === 'GROCERY') ? 'mixed' : 'swiggy';
  }

  storeCategory(stop) {
    const k = this.key(stop.location.lat, stop.location.lng);
    const st = this.world.stores.find((s) => s.id === stop.store_id || this.key(s.lat, s.lng) === k);
    return st ? st.category : 'FOOD';
  }

  dressRider(rider, role) {
    if (rider.role === role) return;
    rider.role = role;
    const u = rider.group.userData;
    const insta = role === 'instamart';
    u.band.visible = insta;
    u.chestMat.map = this.shirtTex(insta ? 'instamart' : 'swiggy');
    u.chestMat.needsUpdate = true;
    u.bagMat.map = this.bagTex(insta ? 'instamart' : 'swiggy');
    u.bagMat.needsUpdate = true;
    u.extra.visible = role === 'mixed';
    const color = insta ? INSTAMART_BLUE : SWIGGY_ORANGE;
    u.halo.material.color.set(color);
    u.marker.material.color.set(color);
    rider.badge.element.className = `scene-label rider ${insta ? 'instamart' : 'swiggy'}`;
    rider.badge.element.textContent = insta ? 'Instamart rider' : role === 'mixed' ? 'Swiggy + Instamart' : 'Swiggy rider';
  }

  /** Roadside waiting spot near a location; riders are spread out so they never stack. */
  parkingSpot(loc, id = '') {
    const s = this.graph.snap(this.project(loc.lat, loc.lng));
    const a = this.graph.pts[s.a];
    const b = this.graph.pts[s.b];
    const dir = b.clone().sub(a).normalize();
    const h = hashString(id);
    const along = ((h % 9) - 4) * 4.5;
    const len = a.distanceTo(b);
    const t = Math.max(0, Math.min(len, s.q.distanceTo(a) + along));
    const p = a.clone().add(dir.clone().multiplyScalar(t));
    return p.add(V(-dir.z, dir.x).multiplyScalar(LANES[(h >>> 4) % LANES.length]));
  }

  syncRiders(planViews) {
    const seen = new Set();
    for (const p of this.engine.partners.values()) {
      if (p.status === 'OFFLINE') continue;
      seen.add(p.id);
      let rider = this.riders.get(p.id);
      if (!rider) {
        const group = this.makeRider(p.id);
        const holder = new THREE.Object3D();
        group.add(holder);
        rider = { group, view: null, role: null, inside: null, badge: this.label('', 'rider swiggy', 3.1, holder) };
        rider.badge.visible = false;
        rider.group.position.copy(this.parkingSpot(p.location, p.id));
        rider.group.rotation.y = Math.random() * Math.PI * 2;
        rider.group.traverse((o) => { o.castShadow = false; });
        this.ridersGroup.add(rider.group);
        this.riders.set(p.id, rider);
        this.pickable(rider.group, `Rider ${p.id}`);
      }
      const view = planViews.get(p.id) || null;
      rider.view = view;
      this.dressRider(rider, this.roleFor(p.id, view));
      const u = rider.group.userData;
      if (view) u.disc.material.color.copy(view.color);
      u.disc.visible = Boolean(view);
      u.lamp.visible = Boolean(view) && this.mode === 'night';
      const uniform = rider.role === 'instamart' ? 'Instamart uniform' : 'Swiggy uniform';
      const tip = view
        ? `Rider ${p.id} (${uniform}) · ${view.plan.order_ids.length} order(s) · ${view.plan.order_ids.length > 1 ? 'coordinated route' : 'single order'}`
        : `Rider ${p.id} (${uniform}) · idle, waiting at the roadside`;
      rider.group.traverse((o) => {
        if (o.isMesh) {
          o.userData.tip = tip;
          o.userData.planId = view ? view.plan.plan_id : null;
        }
      });
      if (!view) rider.idleTarget = this.parkingSpot(p.location, p.id);
    }
    for (const [id, rider] of this.riders) {
      if (!seen.has(id)) {
        const meshes = new Set();
        rider.group.traverse((o) => meshes.add(o));
        rider.badge.element.remove();
        this.pickables = this.pickables.filter((o) => !meshes.has(o));
        this.ridersGroup.remove(rider.group);
        this.riders.delete(id);
      }
    }
  }

  /** A rider crossed into the campus: uniform check at the main gate. */
  verifyAtGate(rider) {
    this.verifications += 1;
    const brand = rider.role === 'instamart' ? 'Instamart' : 'Swiggy';
    this.barrier.until = this.clock.elapsedTime + 1.8;
    const holder = new THREE.Group();
    holder.position.set(this.gatePos.x + 16, this.heightAt(this.gatePos.x, this.gatePos.z), this.gatePos.z + 8);
    const lbl = this.label(`✓ ${brand} uniform verified · entry ${this.verifications}`, 'verify', 6, holder);
    this.fxGroup.add(holder);
    this.effects.push({ ring: null, holder, lbl, age: 0, base: 6 });
  }

  // ------------------------------------------------------------------ plans on the road graph
  planLegs(plan, lane) {
    const cacheKey = `${plan.plan_id}:${plan.version}:${lane}`;
    const cached = this.pathCache.get(cacheKey);
    if (cached) return cached;
    const start = this.engine.partnerStart.get(plan.plan_id) || this.engine.partners.get(plan.partner_id)?.location;
    let from = start ? { point: this.project(start.lat, start.lng) } : null;
    let t = Date.parse(plan.start_at);
    const legs = [];
    for (const s of plan.stops) {
      const to = this.anchorFor(s.location);
      const base = from ? this.graph.route(from, to) : [to.node !== undefined ? this.graph.pts[to.node].clone() : to.point];
      const pts = offsetLine(base, lane);
      legs.push({ pts, t0: t, t1: Date.parse(s.eta), stop: s });
      t = Date.parse(s.etd);
      legs.push({ pts: [pts[pts.length - 1]], t0: Date.parse(s.eta), t1: t, dwell: true });
      from = to;
    }
    this.pathCache.set(cacheKey, legs);
    if (this.pathCache.size > 600) this.pathCache.delete(this.pathCache.keys().next().value);
    return legs;
  }

  positionOn(legs, time) {
    if (!legs.length) return null;
    for (const leg of legs) {
      if (time <= leg.t1) {
        if (leg.dwell) return leg.pts[0].clone();
        if (time <= leg.t0) return leg.pts[0].clone();
        return pointAlong(leg.pts, (time - leg.t0) / Math.max(1, leg.t1 - leg.t0));
      }
    }
    const last = legs[legs.length - 1];
    return last.pts[last.pts.length - 1].clone();
  }

  clearDynamic() {
    const groups = new Set([...this.routeViews.map((r) => r.group), ...this.beacons.map((b) => b.group)]);
    for (const obj of groups) {
      obj.parent?.remove(obj);
      obj.traverse((o) => {
        if (o.isCSS2DObject) o.element.remove();
        if (o.geometry) o.geometry.dispose();
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => {
          if (m.map && m.map !== this.flowTex) m.map.dispose();
          m.dispose();
        });
      });
    }
    this.pickables = this.pickables.filter((p) => !groups.has(p.userData.owner));
    this.routeViews = [];
    this.beacons = [];
  }

  buildRoutes() {
    const views = new Map();
    const plans = this.engine.activePlans().sort((a, b) => a.plan_id.localeCompare(b.plan_id));
    plans.forEach((plan, idx) => {
      const lane = LANES[idx % LANES.length];
      const legs = this.planLegs(plan, lane);
      const pts = [];
      for (const leg of legs) for (const p of leg.pts) if (!pts.length || pts[pts.length - 1].distanceTo(p) > 0.05) pts.push(p);
      const grouped = plan.order_ids.length > 1;
      const color = new THREE.Color(colorFor(plan.plan_id));
      const group = new THREE.Group();
      const view = { plan, legs, color, group, grouped, materials: [], labels: [] };
      const tip = `Plan ${shortId(plan.plan_id)} · rider ${plan.partner_id} · ${plan.order_ids.length} order(s) · ${fmtSaving(plan.expected_savings)}`;
      if (pts.length >= 2) {
        const path = new THREE.CurvePath();
        const lifted = densify(pts, 6).map((q) => q.setY(this.heightAt(q.x, q.z) + 0.8));
        for (let i = 1; i < lifted.length; i += 1) path.add(new THREE.LineCurve3(lifted[i - 1], lifted[i]));
        const length = polyLength(pts);
        const segs = Math.min(500, Math.max(12, Math.round(length / 4)));
        const tex = this.flowTex.clone();
        tex.needsUpdate = true;
        tex.repeat.set(length / 12, 1);
        const core = new THREE.MeshBasicMaterial({ color, map: tex, transparent: true, opacity: 0.95, depthWrite: false });
        const glow = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: grouped ? 0.3 : 0.15, blending: THREE.AdditiveBlending, depthWrite: false });
        const coreMesh = new THREE.Mesh(new THREE.TubeGeometry(path, segs, grouped ? 0.6 : 0.36, 5, false), core);
        const glowMesh = new THREE.Mesh(new THREE.TubeGeometry(path, segs, grouped ? 1.4 : 0.8, 5, false), glow);
        coreMesh.renderOrder = 2;
        glowMesh.renderOrder = 1;
        group.add(glowMesh, coreMesh);
        view.tex = tex;
        view.materials.push([core, 0.95], [glow, grouped ? 0.3 : 0.15]);
        for (const mesh of [coreMesh, glowMesh]) {
          mesh.userData = { tip, planId: plan.plan_id, owner: group };
          this.pickables.push(mesh);
        }
      }
      plan.stops.forEach((s, k) => {
        const leg = legs.find((l) => l.stop === s);
        const p = leg ? leg.pts[leg.pts.length - 1] : this.project(s.location.lat, s.location.lng);
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(2.2, 3.1, 40),
          new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false }),
        );
        ring.rotation.x = -Math.PI / 2;
        const py = this.heightAt(p.x, p.z);
        ring.position.set(p.x, py + 0.85, p.z);
        group.add(ring);
        view.materials.push([ring.material, 0.9]);
        if (grouped) {
          const beam = new THREE.Mesh(
            new THREE.CylinderGeometry(0.35, 0.35, 16, 12, 1, true),
            new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false }),
          );
          beam.position.set(p.x, py + 8.4, p.z);
          group.add(beam);
          view.materials.push([beam.material, 0.7]);
          const holder = new THREE.Object3D();
          holder.position.set(p.x, py, p.z);
          group.add(holder);
          const lbl = this.label(`${k + 1} ${s.type === 'PICKUP' ? 'pickup' : 'drop'}`, 'stop', 18.5, holder);
          lbl.element.style.background = `#${color.getHexString()}`;
          lbl.visible = false;
          view.labels.push({ lbl, pos: p.clone() });
        }
      });
      this.routesGroup.add(group);
      this.routeViews.push(view);
      views.set(plan.partner_id, view);
    });
    return views;
  }

  buildBeacons() {
    const waiting = new Map();
    for (const o of this.engine.orders.values()) {
      if (o.status !== 'PENDING') continue;
      const w = waiting.get(o.store_id) || { n: 0, flex: 0 };
      w.n += 1;
      if (o.mode === 'FLEX') w.flex += 1;
      waiting.set(o.store_id, w);
    }
    for (const [storeId, w] of waiting) {
      const site = this.siteByStore.get(storeId);
      if (!site) continue;
      const h = 10 + 4 * Math.min(w.n, 6);
      const group = new THREE.Group();
      group.position.copy(site.pos).setY(this.heightAt(site.pos.x, site.pos.z));
      const beam = new THREE.Mesh(
        new THREE.CylinderGeometry(1.6, 1.6, h, 24, 1, true),
        new THREE.MeshBasicMaterial({ color: 0xffb020, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }),
      );
      beam.position.y = site.roofY + h / 2;
      const orb = new THREE.Mesh(new THREE.SphereGeometry(1.6, 20, 16), this.std({ color: 0xffd27a, emissive: 0xffb020, emissiveIntensity: 1.8 }));
      orb.position.y = site.roofY + h;
      group.add(beam, orb);
      const lbl = this.label(`${w.n} waiting${w.flex ? ` · ${w.flex} flexible` : ''}`, 'waiting', site.roofY + h + 4, group);
      this.scene.add(group);
      this.beacons.push({ group, beam, orb, lbl, phase: Math.random() * Math.PI * 2 });
    }
  }

  spawnDeliveryEffects() {
    const delivered = [...this.engine.orders.values()].filter((o) => o.status === 'DELIVERED');
    if (this.deliveredSeen === null) {
      this.deliveredSeen = new Set(delivered.map((o) => o.id));
      return;
    }
    for (const o of delivered) {
      if (this.deliveredSeen.has(o.id)) continue;
      this.deliveredSeen.add(o.id);
      const plan = o.plan_id ? this.engine.plans.get(o.plan_id) : null;
      const stop = plan?.stops.find((s) => s.order_id === o.id && s.type === 'DROP');
      if (!stop) continue;
      const anchor = this.anchors.get(this.key(stop.location.lat, stop.location.lng));
      const p = anchor ? anchor.front : this.project(stop.location.lat, stop.location.lng);
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(1, 1.7, 48),
        new THREE.MeshBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 1, side: THREE.DoubleSide, depthWrite: false }),
      );
      ring.rotation.x = -Math.PI / 2;
      const py = this.heightAt(p.x, p.z);
      ring.position.set(p.x, py + 0.9, p.z);
      const holder = new THREE.Group();
      holder.position.set(p.x, py, p.z);
      const text = o.mode === 'FLEX' && o.reward ? `Delivered · +${fmtMoney(o.reward)} reward` : 'Delivered';
      const lbl = this.label(text, 'delivered', 12, holder);
      this.fxGroup.add(ring, holder);
      this.effects.push({ ring, holder, lbl, age: 0, base: 12 });
    }
  }

  // ------------------------------------------------------------------ public API
  render() {
    this.clearDynamic();
    const views = this.buildRoutes();
    this.buildBeacons();
    this.syncRiders(views);
    this.spawnDeliveryEffects();
  }

  invalidate() {
    this.resize();
  }

  resetView() {
    this.camera.position.set(250, 300, 300);
    this.controls.target.set(-70, 0, -150);
    this.controls.update();
  }

  /** Fly the camera close to a building or landmark (used by the "Zoom to" control). */
  focusOn(site) {
    const p = site.pos;
    const f = site.forward;
    const y = site.y ?? this.heightAt(p.x, p.z);
    const far = site.kind === 'hostel' || site.kind === 'grocery' ? 58 : site.kind ? 46 : 70;
    this.controls.target.set(p.x, y + 6, p.z);
    this.camera.position.set(p.x + f.x * far + f.z * 16, y + far * 0.6, p.z + f.z * far - f.x * 16);
    this.controls.update();
  }

  setAutoRotate(on) {
    this.autoRotate = on;
    this.controls.autoRotate = on;
  }

  resize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.labels.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  visible() {
    return this.container.offsetParent !== null && this.container.clientWidth > 0;
  }

  onPointer(e) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = this.raycaster.intersectObjects(this.pickables, false)[0];
    if (hit && hit.object.userData.tip) {
      this.tooltip.textContent = hit.object.userData.tip;
      this.tooltip.style.left = `${e.clientX - rect.left + 14}px`;
      this.tooltip.style.top = `${e.clientY - rect.top + 10}px`;
      this.tooltip.classList.remove('hidden');
      if (!this.followId) this.highlight = hit.object.userData.planId || null;
    } else {
      this.tooltip.classList.add('hidden');
      if (!this.followId) this.highlight = null;
    }
  }

  frame() {
    if (!this.visible()) return;
    const start = performance.now();
    // keep the page responsive on slow machines: after an expensive frame, give the browser time
    if (start < this.nextFrameAt) return;
    const dt = Math.min(0.1, this.clock.getDelta());
    const elapsed = this.clock.elapsedTime;
    const gap = this.engine.simNow - this.displayTime;
    this.displayTime = Math.abs(gap) < 400 ? this.engine.simNow : this.displayTime + gap * Math.min(1, dt * 2.2);
    const cam = this.camera.position;

    // targets on the road, then keep riders a body-width apart (several can share one stop)
    const list = [...this.riders.values()];
    const targets = list.map((r) => (r.view ? this.positionOn(r.view.legs, this.displayTime) : r.idleTarget?.clone()) || null);
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        const a = targets[i];
        const b = targets[j];
        if (!a || !b) continue;
        const d = a.distanceTo(b);
        if (d < RIDER_GAP) {
          const push = (d > 1e-3 ? b.clone().sub(a) : V(1, 0.4)).setLength((RIDER_GAP - d) / 2);
          a.sub(push);
          b.add(push);
        }
      }
    }
    for (const [k, rider] of list.entries()) {
      const target = targets[k];
      if (!target) continue;
      const g = rider.group;
      const prev = g.position.clone().setY(0);
      const next = prev.clone().lerp(target, Math.min(1, dt * 7));
      g.position.set(next.x, this.heightAt(next.x, next.z) + (rider.view ? 0.06 * Math.abs(Math.sin(elapsed * 9 + g.id)) : 0), next.z);
      const move = next.clone().sub(prev);
      if (move.lengthSq() > 1e-4) {
        const yaw = Math.atan2(move.x, move.z);
        g.rotation.y += Math.atan2(Math.sin(yaw - g.rotation.y), Math.cos(yaw - g.rotation.y)) * Math.min(1, dt * 8);
        for (const w of g.userData.wheels) w.rotation.x += move.length() * 1.5;
      }
      const inside = this.insideCampus(next, 0);
      if (rider.inside === false && inside && rider.view) this.verifyAtGate(rider);
      rider.inside = inside;
      const u = g.userData;
      u.marker.position.y = u.markerY + 0.1 * Math.sin(elapsed * 3 + g.id);
      u.halo.material.opacity = 0.55 + 0.3 * Math.sin(elapsed * 4 + g.id);
      rider.badge.visible = cam.distanceTo(g.position) < RIDER_BADGE_DISTANCE;
    }
    this.stepTraffic(dt);
    this.updateBlobs();
    if (this.followId) {
      const r = this.riders.get(this.followId);
      if (r) {
        const goal = r.group.position.clone().add(P3(0, 3, 0));
        const step = goal.sub(this.controls.target).multiplyScalar(Math.min(1, dt * 3));
        this.controls.target.add(step);
        this.camera.position.add(step);
        if (r.view) this.highlight = r.view.plan.plan_id;
      } else {
        this.follow(null);
      }
    }
    for (const view of this.routeViews) {
      if (view.tex) view.tex.offset.x -= dt * 0.9;
      const dim = this.highlight && this.highlight !== view.plan.plan_id;
      for (const [mat, base] of view.materials) mat.opacity = dim ? base * 0.15 : base;
      const focus = this.highlight === view.plan.plan_id;
      for (const { lbl, pos } of view.labels) lbl.visible = focus || cam.distanceTo(pos) < ZOOM_LABEL_DISTANCE;
    }
    for (const b of this.beacons) {
      b.beam.material.opacity = 0.22 + 0.18 * (0.5 + 0.5 * Math.sin(elapsed * 2.4 + b.phase));
      const s = 1 + 0.12 * Math.sin(elapsed * 3 + b.phase);
      b.orb.scale.set(s, s, s);
      b.lbl.visible = cam.distanceTo(b.group.position) < 330;
    }
    // main gate: the boom barrier lifts and the scanner flashes green while a rider is verified
    const open = elapsed < this.barrier.until;
    this.barrier.pivot.rotation.x += ((open ? -1.35 : 0) - this.barrier.pivot.rotation.x) * Math.min(1, dt * 6);
    this.scanner.emissiveIntensity = open ? 3 * (0.6 + 0.4 * Math.sin(elapsed * 20)) : 0.3 * (this.mode === 'night' ? 1 : 0.4);
    this.flag.rotation.z = 0.04 * Math.sin(elapsed * 2.2);
    for (const fx of this.effects) {
      fx.age += dt;
      if (fx.ring) {
        const s = 1 + fx.age * 8;
        fx.ring.scale.set(s, s, s);
        fx.ring.material.opacity = Math.max(0, 1 - fx.age / 2.2);
      }
      fx.lbl.position.y = fx.base + fx.age * 6;
      fx.lbl.element.style.opacity = String(Math.max(0, 1 - fx.age / 2.6));
    }
    this.effects = this.effects.filter((fx) => {
      if (fx.age < 2.8) return true;
      this.fxGroup.remove(fx.holder);
      fx.lbl.element.remove();
      if (fx.ring) {
        this.fxGroup.remove(fx.ring);
        fx.ring.geometry.dispose();
        fx.ring.material.dispose();
      }
      return false;
    });
    for (const z of this.zoomLabels) z.obj.visible = cam.distanceTo(z.pos) < ZOOM_LABEL_DISTANCE;
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
    this.adaptQuality(start, performance.now() - start);
  }

  /**
   * Keep the view smooth: if frames are slow for a few seconds, first render at native pixel
   * density 1, then switch to lite (no shadows, sky lighting or grass). Only a software renderer
   * (hundreds of ms per frame) gets frames spaced out so the rest of the page stays responsive.
   */
  adaptQuality(start, cost) {
    this.nextFrameAt = cost > 150 ? performance.now() + cost : 0;
    const interval = this.lastFrameAt ? start - this.lastFrameAt : 16;
    this.lastFrameAt = start;
    if (this.qualityLocked || interval > 1000) return; // tab was hidden or just resumed
    this.frameMs = this.frameMs ? this.frameMs * 0.95 + interval * 0.05 : interval;
    this.adaptFrames = (this.adaptFrames || 0) + 1;
    if (this.adaptFrames < 90 || this.frameMs < 45) return;
    this.adaptFrames = 0;
    this.frameMs = 0;
    if (this.quality === 'high' && this.renderer.getPixelRatio() > 1) {
      this.renderer.setPixelRatio(1);
      this.resize();
    } else if (this.quality === 'high') {
      this.setQuality('lite');
    }
  }

  /** Follow one rider with the camera (null stops). Highlights that rider's route. */
  follow(partnerId, planId = null) {
    this.followId = partnerId || null;
    this.followBanner.classList.toggle('hidden', !partnerId);
    if (!partnerId) {
      this.highlight = null;
      return;
    }
    this.followText.textContent = `Following rider ${partnerId}`;
    if (planId) this.highlight = planId;
    const r = this.riders.get(partnerId);
    if (r) {
      // look down from behind and well above the rider, so buildings rarely block the view
      const p = r.group.position;
      const yaw = r.group.rotation.y;
      this.controls.target.set(p.x, p.y + 3, p.z);
      this.camera.position.set(p.x - Math.sin(yaw) * 32, p.y + 52, p.z - Math.cos(yaw) * 32);
      this.controls.update();
    }
  }

  /** Geometry self-check used by the automated browser test: lists any overlaps in the world. */
  audit() {
    const problems = [];
    const boxOnRoad = (b, road, pad) => road.segs.some(([a, c]) => {
      const n = Math.max(2, Math.ceil(a.distanceTo(c)));
      const h = road.width / 2 + pad;
      for (let k = 0; k <= n; k += 1) {
        const p = a.clone().lerp(c, k / n);
        if (p.x > b.x0 - h && p.x < b.x1 + h && p.z > b.z0 - h && p.z < b.z1 + h) return true;
      }
      return false;
    });
    const overlap = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.z0 < b.z1 && b.z0 < a.z1;
    for (let i = 0; i < this.sites.length; i += 1) {
      const s = this.sites[i];
      for (const road of this.roads) if (boxOnRoad(s.box, road, -0.4)) problems.push(`${s.name} overlaps ${road.name || road.id}`);
      for (let j = i + 1; j < this.sites.length; j += 1) if (overlap(s.box, this.sites[j].box)) problems.push(`${s.name} overlaps ${this.sites[j].name}`);
      if (s.kind === 'hostel' && !(this.insideCampus(V(s.box.x0, s.box.z0), 1) && this.insideCampus(V(s.box.x1, s.box.z1), 1))) problems.push(`${s.name} crosses the campus wall`);
    }
    for (const b of this.cityBoxes) {
      for (const road of this.roads) if (road.segs.some(([a, c]) => segDist(b.p, a, c).d < b.half * 0.72 + road.width / 2)) problems.push('city building on a road');
    }
    for (const t of this.treeSpots) for (const road of this.roads) if (road.segs.some(([a, c]) => segDist(t, a, c).d < road.width / 2 + 1)) problems.push('tree on a road');
    // roads cross the campus wall only through the main gate opening
    for (const road of this.roads) {
      for (const [a, c] of road.segs) for (const [w0, w1] of this.wallSegs) if (segIntersect(a, c, w0, w1)) problems.push(`${road.name || road.id} crosses the campus wall`);
    }
    // every trip from the city to a hostel goes through the main gate
    const [ccx, ccz] = this.plan.city.center;
    let viaGate = 0;
    for (const s of this.sites.filter((x) => x.kind === 'hostel')) {
      const path = this.graph.route({ point: V(ccx, ccz) }, { node: s.frontNode });
      if (path.some((p) => p.distanceTo(this.gatePos) < 1)) viaGate += 1;
      else problems.push(`route to ${s.name} misses the main gate`);
    }
    // the road graph is connected
    const seen = new Set([0]);
    const stack = [0];
    while (stack.length) {
      for (const [v] of this.graph.adj[stack.pop()]) {
        if (!seen.has(v)) {
          seen.add(v);
          stack.push(v);
        }
      }
    }
    if (seen.size !== this.graph.pts.length) problems.push(`road graph has ${this.graph.pts.length - seen.size} unreachable nodes`);
    return { problems: [...new Set(problems)], sites: this.sites.length, graphNodes: this.graph.pts.length, hostelsViaGate: viaGate, vehicles: this.traffic.length };
  }
}
