# Vendored libraries

Bundled so FlexDrop Studio needs no CDN (works offline except map tiles).

| Library | Version | License | Files | Changes |
|---|---|---|---|---|
| Leaflet | 1.9.4 | BSD-2-Clause | `leaflet/` | none |
| three.js | 0.160.0 | MIT | `three/three.module.min.js`, `three/OrbitControls.js`, `three/CSS2DRenderer.js` | the two example modules import `./three.module.min.js` instead of the bare specifier `three` (no import map needed, keeps the CSP strict) |
