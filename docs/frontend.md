# FlexDrop Studio (browser prototype)

FlexDrop Studio is the clickable prototype for the competition demo. The FlexDrop service serves
it at `/app/`. It is plain HTML, CSS and JavaScript modules, so there is no Node or React build.
Two libraries are bundled in `src/flexdrop/web/vendor/` (see its README):
* **three.js 0.160** (MIT) for the 3D campus;
* **Leaflet 1.9.4** (BSD-2) for the alternative street map.

Nothing loads from a CDN. The only external requests are OpenStreetMap tiles in the street-map
view.

## Start it

```bash
flexdrop studio                      # then open http://127.0.0.1:8000/app/
flexdrop studio --start now          # use the real clock instead of the 19:30 IST dinner peak
flexdrop studio --port 8080 --partners 60
```

In Google Colab or Jupyter:

```python
from flexdrop.colab import launch
launch()          # starts Studio in the background and opens it in a new browser tab
```

`studio` builds a normal FlexDrop app and seeds a world modelled on **IIITDM Kurnool**
(Jagannathagattu, beside NH 40, south of Kurnool city, Andhra Pradesh):
* **one entrance gate** facing the highway, the red clock tower on the central roundabout, the
  "I ♥ IIITDM KURNOOL" sign, the academic area and **8 hostel wings** behind it: MVHR Wing A/B,
  SRKH Wing A/B, Kalam Hall of Residence Wing A/B and Kalpana Chawla Hall of Residence Wing A/B;
* 4 student PGs on the village road opposite the gate;
* **Kurnool city** to the north along NH 40, with 26 restaurants in named localities (Raj Vihar
  Centre, Budhawarpet, Old Town, Gandhi Nagar, Sampath Nagar, Kallur, and others), Konda Reddy
  Fort and the Tungabhadra river;
* 2 Instamart stores (Raj Vihar Centre and Nandyal Checkpost) and 40 riders.

The layout is a single **site plan** in `flexdrop/studio.py` (`site_plan()`, 1 unit = 8 m). It is
the source of truth for both the API world (store and drop-point coordinates) and the 3D view.
Two deliberate simplifications:
* **Distance is compressed.** The real campus is about 12 km from Kurnool's centre. The Studio
  puts the city about 3 km away, so a full delivery fits on screen and inside the hot-food age
  limit. Restaurants end up 2 to 4 km from the hostels.
* **Restaurant names** are inspired by real Kurnool eateries with the **last word changed**
  (for example "Ajwa Kitchen", "Hyderabadi's Biryani Corner"), because the demo invents orders,
  menus and timings and must not present them as a real business's. The UI shows this note.

Only the Studio world is modelled on Kurnool; the simulation scenarios behind the reported
results are unchanged. Studio uses a fresh temporary
database, creates a session API key and serves the UI. It is a demo mode and refuses to start
when `FLEXDROP_ENV=production`.

## The three screens

| Screen | What it shows | Real API calls |
|---|---|---|
| **Student checkout** | Phone-style app. Pick a hostel, restaurant and items; see *Standard (arrives by 19:59)* next to *Flexible +10 min, get ₹11*; place the order; track it (assigned, picked up, delivered, reward credited), including whether the rider is carrying other orders and how many flexible minutes FlexDrop actually used | `POST /v1/orders`, `POST /v1/preferences`, `GET /v1/orders` |
| **Operations** | **3D campus** (default) or street map. Roads, buildings, trees and riders, with day/night lighting and a "Zoom to" list for every building. Routes glow on the roads, and thicker paths are coordinated routes. KPI cards (on-time, coordinated share, savings vs solo, flexible uptake, latency); controls (generate orders, run optimiser, inject a restaurant delay, kill switch); a live decision log; the plans table | `POST /v1/optimize`, `POST /v1/plans/{id}/events`, `GET /v1/plans`, `GET /v1/partners`, `GET /v1/metrics`, `POST /v1/admin/kill-switch` |
| **Results** | Simulation comparison from `flexdrop pipeline`: headline changes vs independent dispatch, bar charts for cost, distance, on-time and coordination across the 5 policies, and the seed-robustness table. Campus and city toggle | `GET /v1/reports/simulation` |

## The 3D campus and city

The 3D view is one consistent world rather than lines drawn over a map.

* **Road network.** NH 40 (with a bridge over the Tungabhadra), the city street grid, the
  Nandyal Checkpost road, the village road, and inside the campus the entrance road, the clock
  tower roundabout, the campus road and the hostel avenue. All of them form one graph.
* **One entrance.** The compound wall has a single opening at the main gate. Every route to a
  hostel passes through it, which the automated audit checks for all 8 wings. The gate has the
  grey portal with the institute name in Telugu, Hindi and English, an emblem, the red slatted
  canopy, sliding gates, a security cabin with a guard, a boom barrier and the national flag.
* **Uniform check.** When a rider enters the campus the barrier lifts, the scanner flashes green
  and a tag reads "Swiggy uniform verified" or "Instamart uniform verified".
* **Hilltop campus.** The campus stands on a plateau above the plain (height exaggerated for
  readability), with green slopes and boulders; the entrance road climbs from NH 40 to the gate.
  Riders and routes follow the terrain.
* **Green countryside.** Grassland, grass tufts, flowering bushes, farm fields and neem-like
  trees around the campus and city, and taller rocky hills.
* **Riders in uniform.** Every rider is a person on a scooter: legs on the floorboard, hands on
  the handlebar, helmet and visor. Swiggy riders wear the orange full-sleeve shirt with
  reflective stripes and the Swiggy print, and carry an orange Swiggy backpack. Riders on grocery-only trips wear the Instamart band and carry
  a blue Instamart bag. Mixed trips (the FlexDrop case) carry both bags. Every rider has a
  brand-coloured halo and marker, so riders stand out from ordinary traffic, and a name badge
  appears when you zoom in.
* **Real-world traffic.** Cars, autos, APSRTC-style buses, trucks and motorbikes drive on the
  highway and city streets on the left-hand outer lane. They brake for anything ahead, so they
  never pass through riders or each other. Riders use the inner lanes.
* **Riders drive only on roads.** Every leg of every plan is routed on the road graph (Dijkstra
  shortest paths), so no route or rider passes through a building, tree or wall. Each plan gets
  its own lane, so overlapping routes stay readable.
* **Collision-free layout.** Buildings sit on plots set back from the streets; trees, lamps,
  houses and boulders are only placed in free space. The automated browser test calls
  `window.flexdrop.scene.audit()` and fails on any overlap, any road crossing the campus wall, a
  hostel route that misses the gate, or a disconnected road graph.
* **Buildings and landmarks.**
  * Academic area: white blocks with red vertical fins (the Tungabhadra block), red brick end
    panels, an entrance canopy on red columns, a skywalk on red columns, a courtyard pool, red
    pavers and lawns; staff parking and a sports ground with a running track.
  * Hostels: salmon-red six-storey wings with hall and wing name boards, balconies, entrance
    canopies and water tanks, with granite boulders behind them.
  * Jagannathagattu hills with the hill temple; Konda Reddy Fort in Old Town.
  * Restaurants: signboard with a generated logo, cuisine line, awning, outdoor tables, menu
    stand. Instamart: blue branded dark store with loading bays, solar roof and parked scooters.
  * City blocks with shop boards, village houses near the campus.
* **Live layers.** Glowing animated paths on the roads (thick = coordinated route); numbered stop
  beams; amber beacons over restaurants while orders wait; a green pulse with the reward amount
  on delivery.
* **Day / Night** toggle (night lights windows, signs, street lamps and vehicle lights).
  **Zoom to** flies to any landmark, hostel, PG, Instamart store or restaurant, and name tags
  with the logo appear when you are close.
* Drag to rotate, scroll to zoom, right-drag to pan. **Auto-rotate** for a presentation.
* **Quality and speed.** With a GPU the view uses soft shadows (rendered once, not every frame),
  sky-based lighting and grass. If frames get slow it first drops to pixel density 1, then to a
  lite mode without shadows, sky lighting or grass; software renderers start in lite mode and
  space frames out so the page stays responsive. Force a mode with `/app/?quality=high` or
  `/app/?quality=lite`. Scrolling zooms towards the mouse pointer, like a map.
* **Street map** tiles come from OpenStreetMap, whose usage policy requires a Referer header; the
  app sends its origin (`Referrer-Policy: strict-origin-when-cross-origin`).

The 3D view needs the Studio site plan. Against a normal deployment the operations screen uses
the street map.

## Following an order

When the student confirms an order, simulated time starts running on its own (1 minute every 1.5
seconds; **Pause** at the top right stops it). The phone then shows, live:
* a plain-language status ("looking for nearby orders to combine", "Rider P012 assigned, picking
  up at 19:04", "on the way", "Delivered") and a progress bar to the arrival time;
* the 4-step timeline (received, rider assigned, picked up, delivered);
* the rider's whole trip: every pickup and drop in order, with the student's own stops marked,
  and how many of the approved flexible minutes FlexDrop actually used;
* **Watch rider in 3D**: switches to Operations and follows that rider with the camera, with its
  route highlighted. Drag the view or press **Stop following** to take the camera back.

## The simulated clock

Each control on the top bar plays the role of one Swiggy system:

* **+1 min / +5 min** advance simulated time. For every minute:
  * the **rider app** reports `PICKED_UP` / `DELIVERED` for stops whose ETA has passed;
  * **dispatch** calls `/v1/optimize` and accepts the proposed plans.
* **Auto** also generates student demand (Quiet / Evening / Peak) and runs one simulated minute
  every 1.5 seconds.

Placing an order in the student checkout starts **Auto** for you. Every timestamp sent to the API comes from the simulated clock, so a 30-minute evening plays out
in under a minute. Customer choices in generated demand follow a simple utility (bigger reward and
shorter wait are more attractive). The student checkout lets you make the choice yourself.

## Suggested 3-minute demo script

1. **Operations (3D campus):** *Generate 10 orders* twice. Point out the amber beacons (orders held
   at restaurants, waiting to be combined) and the thick glowing coordinated routes. Hover one to
   show its numbered stops. Zoom to the *Main gate* to watch uniformed riders being verified.
2. **Student checkout:** order from a Kurnool restaurant to MVHR Wing A and show the standard vs flexible
   options and the reward. Accept +10 min.
3. Press **+5 min** a few times. The order moves through the timeline, and the explanation shows
   that the rider is carrying 2 orders and that FlexDrop used only about 3 of the 10 approved
   minutes.
4. **Operations:** *Inject restaurant delay*. The log shows the CRE splitting the route and
   releasing the delayed order without breaking anyone's approved window.
5. **Kill switch ON**, then *Run optimiser*: FlexDrop steps aside (`FALLBACK_KILL_SWITCH`) and
   standard dispatch continues. Turn it off again.
6. **Results:** −15.7% cost per order on campus with on-time unchanged, and the robustness table.

## Production use

The same UI works against a normal deployment (`flexdrop serve`). `/app/session` then returns
404 and the UI asks for an API key (admin role), kept only in the browser tab's session storage.
The checkout simulator needs the demo campus and is only available in Studio. The operations and
results screens work with any zone.

Security: the pages are static files with a strict Content-Security-Policy (`script-src 'self'`,
no inline scripts, no `eval`, `connect-src 'self'`). All dynamic text is inserted with
`textContent`, never `innerHTML`.

## Automated browser check (optional)

```bash
flexdrop studio --port 8765 &
node scripts/ui_smoke.js /tmp/shots 1 http://127.0.0.1:8765   # needs Node.js + Playwright
```

The script clicks through checkout, operations (generate, advance, delay, auto-run) and results,
checks a mobile viewport for horizontal overflow, saves screenshots and fails on any JavaScript
error.
