# Spotter

Weather-aware truck routing. Enter a trip and get **three truck-legal routes, ranked by the
weather each one will actually meet** — not by a single forecast at the origin, and not by a
weighted score that hides the dangerous miles.

- **Live app:** <https://spotter-yshb.onrender.com>
- **Walkthrough:** [Part 1](https://www.loom.com/share/1e8e57ba17c54a7a83c0497e9b1a89e5) · [Part 2](https://www.loom.com/share/4a8aa3b0eceb42a58a16967ae5853734)

---

## What it does

1. Geocodes both endpoints (type-ahead, proxied through the server).
2. Asks a **heavy-goods-vehicle** routing engine for three distinct routes.
3. Samples the forecast at the 10 / 25 / 50 mile interval you choose, at **each checkpoint's
   estimated arrival time** — a truck that reaches Kansas at 2am gets the 2am forecast.
4. Classifies every checkpoint against the risk table, applies your load weight, and aggregates
   the result into mileage buckets.
5. Ranks the routes with the scope's exact priority order and maps the winner.
6. Shows a 48-hour **corridor heatmap** with an independent time slider.

Everything is derived per request. There is no database, no login and no stored trip.

---

## Quick start

Requirements: Python 3.12, Node 20+.

```bash
cp backend/.env.example backend/.env      # then paste your ORS key
make install                              # venv + pip + npm
make dev                                  # API :8000, app :5173
```

Open <http://localhost:5173>. Vite proxies `/api` to Django, so there is no CORS configuration
and no second origin in production.

Get a free key at <https://openrouteservice.org/dev/#/signup> (200 directions requests/day).

### Tests

```bash
make test     # 110 backend tests + strict TypeScript check
make lint     # ruff
```

---

## How a plan is built

```
POST /api/plan
      │
      ├─ serializers.py     validate coordinates, load, departure, interval
      │
      ├─ ors.py             base route ─┐
      │                     toll-free  ─┼─ 3 distinct candidates (parallel, ≤4 calls)
      │                     detours    ─┘
      │
      ├─ geo.py             cumulative distance → checkpoints → ETA per checkpoint
      │
      ├─ weather.py         one batched Open-Meteo call per 50 points, hourly, mph + inches
      │
      ├─ risk.py            classify → load rules → mileage buckets → ranking
      │
      └─ services.py        heatmap for the recommended corridor + response shaping
```

| File | Responsibility |
| --- | --- |
| `backend/api/risk.py` | Thresholds, classification, aggregation, ranking. Pure functions, no I/O. |
| `backend/api/geo.py` | Haversine, checkpoint sampling, corridor sampling, route overlap. |
| `backend/api/ors.py` | HGV routing client, candidate generation, ETA curve from the step table. |
| `backend/api/weather.py` | Batched hourly forecast client, caching, missing-data handling. |
| `backend/api/photon.py` | Place search proxy. |
| `backend/api/services.py` | Orchestration and response shaping. |
| `backend/api/views.py` | HTTP only: validate, cache, return. |
| `frontend/src/components/TripMap.tsx` | MapLibre layers: corridor heat, risk ribbon, routes, checkpoints. |

---

## The risk model

### Thresholds

| Condition | Low | Moderate | High | Severe | No travel |
| --- | --- | --- | --- | --- | --- |
| Wind (mph) | `< 25` | `25–34` | `35–44` | `45–54` | `≥ 55` |
| Rain (in/hr) | `< 0.10` | `0.10–0.25` | `0.25–0.50` | `0.50–1.00` | `> 1.00` |
| Snow (in/hr) | `< 0.5` | `0.5–1.0` | `1.0–2.0` | `2.0–3.0` | `> 3.0` |

A checkpoint takes the **worst** of the three conditions, then the load rules are applied on top.
Load rules can only make a verdict worse, never better.

### Boundary conventions

The table mixes `<`, `–`, and `>` notation, so the exact edges are pinned by tests:

- Bands are **lower-inclusive, upper-exclusive**: `25 ≤ wind < 35` is Moderate, `45 ≤ wind < 55`
  is Severe.
- Wind's last band is written `≥ 55`, so **exactly 55 mph is No Travel**.
- Rain and snow's last bands are written `0.50–1.00` / `> 1.00` and `2.0–3.0` / `> 3.0`, so
  **exactly 1.00 in/hr of rain and exactly 3.0 in/hr of snow are still Severe**; only a strictly
  greater rate is No Travel.

### Load rules

| Wind | Load | Result |
| --- | --- | --- |
| `≥ 55 mph` | any | No Travel |
| `45–54 mph` | `> 30,000 lb` | No Travel |
| `35–44 mph` | `> 40,000 lb` | Severe |

The spec says "over 30,000", so **exactly 30,000 lb and exactly 40,000 lb are unaffected** — also
pinned by tests.

### Missing data

A checkpoint with any missing variable is **Unknown**. Unknown is never treated as safe:

- It gets its own mileage bucket, shown on every route card and map legend.
- It scores **1.5** on the 0–4 risk scale: above Moderate, below High. A route full of gaps
  cannot win on "lowest average risk", but is not inflated to Severe either.
- A *confirmed* No Travel verdict survives missing data, because the verdict does not depend on
  the missing variable.
- Routes with more than 20% unknown miles carry an explicit warning.

### Aggregation and ranking

`severe_miles` **includes** `no_travel_miles`. Without that, a route with 100 miles of No Travel
weather would beat one with 100 miles of Severe weather on `severe_miles = 0`. They are reported
separately and combined for ranking.

Routes are sorted by this tuple, exactly, with no weighted score:

1. Fewest severe-risk miles
2. Fewest high-risk miles
3. Lowest average risk (miles-weighted, Unknown = 1.5)
4. Shortest travel time

---

## Three routes, and the quota problem

The routing provider's `alternative_routes` option **rejects any route longer than 100 km**, so it
cannot be used for a real trip. If you ask for it on a 600-mile haul you get a 400.

So candidates are generated honestly and then de-duplicated:

1. **Base** — the default HGV route.
2. **Avoids tolls** — the same endpoints with `avoid_features: ["tollways"]`.
3. **Alternate corridor** — a waypoint placed ~20 miles perpendicular from the midpoint of the
   base route, then re-routed through it.

The third one needs one more trick. A raw offset point usually lands in open country, and the
default snapping radius is 350 m, so the request fails with *"could not find routable point"*.
Passing `radiuses: [-1, 25000, -1]` lets the router snap the waypoint to the nearest
truck-accessible road within 25 km. That turned a 0-for-4 failure rate into 4-for-4 on mountain
corridors, at no extra API cost.

Every candidate is compared against the ones already kept using a sampled corridor-overlap test;
anything more than **90% overlapping is discarded**. If only two genuinely distinct routes exist,
the app returns two and says so. **No duplicate is ever invented to reach three.**

All three candidates are requested concurrently, so a plan costs ~8 s instead of ~20 s.

### Provider budget

The free ORS key allows **200 requests/day**, shared by every endpoint. A plan spends at most four
routing calls, and identical plans are cached for 15 minutes. That is roughly **50 fresh plans per
day**, which is why the cache keys on rounded coordinates, load bucket and departure hour.

---

## Why these providers

| Need | Choice | Why not the obvious alternative |
| --- | --- | --- |
| Truck routing | **openrouteservice `driving-hgv`** | Mapbox Directions has `driving`, `driving-traffic`, `walking`, `cycling` — **no HGV profile**. Car routing through Chicago → NYC is 13.9 h; the HGV profile says 19.0 h, because trucks are slower and restricted. |
| Tiles | **MapLibre GL JS + OpenFreeMap** | Free, no key, no per-map-load billing. Mapbox GL v2+ requires a token even for a demo. |
| Geocoding | **Photon** | Nominatim's usage policy forbids type-ahead search. |
| Weather | **Open-Meteo** | No key, hourly, and one request can carry dozens of coordinates. |

---

## API

### `POST /api/plan/`

```json
{
  "origin":      { "lat": 39.7392, "lon": -104.9903, "label": "Denver, Colorado" },
  "destination": { "lat": 39.0997, "lon": -94.5786,  "label": "Kansas City, Missouri" },
  "departureAt": "2026-05-01T13:00:00Z",
  "loadLbs": 44000,
  "intervalMiles": 25
}
```

`departureAt` must include a timezone offset (a naive timestamp is rejected rather than guessed
at), must not be more than an hour in the past, and must be within the 16-day forecast horizon.
`intervalMiles` must be 10, 25 or 50.

The response contains `routes[]` (ranked, with `summary`, `checkpoints` carrying ETAs and weather,
and `warnings`), plus a `heatmap` with one severity value per hour per corridor cell.

Errors use one envelope:

```json
{ "error": { "code": "invalid_request", "message": "…", "fields": { "departureAt": ["…"] } } }
```

Provider failures map to `502`, quota exhaustion to `503` with `Retry-After`. Internal errors never
leak stack traces.

### Other endpoints

- `GET /api/geocode/?q=denver` — proxied, cached place search.
- `GET /api/health/` — liveness probe.

---

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `ORS_API_KEY` | — | **Required.** Server-side only. |
| `DJANGO_DEBUG` | `true` | Set `false` in production. |
| `DJANGO_SECRET_KEY` | dev value | Generated by Render. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver,.onrender.com` | Comma separated. |
| `PLAN_CACHE_SECONDS` | `900` | Protects the routing quota. |
| `WEATHER_CACHE_SECONDS` | `1800` | Per 50-point batch. |
| `DJANGO_CACHE_DIR` | `backend/.cache` | File-backed cache. |

There is no database. `DATABASES = {}`, and the session, auth, admin and contenttypes apps are
absent, along with the middleware they need. CSRF middleware is omitted because no endpoint
authenticates with cookies.

---

## Deployment

`render.yaml` describes a single free web service that serves the API *and* the SPA:

- Build: `pip install -r requirements.txt && collectstatic`
- Start: `gunicorn config.wsgi:application`
- WhiteNoise serves `index.html` and hashed assets from the same origin as `/api`, so there is no
  CORS surface in production.

**The compiled frontend is committed** (`frontend/dist`). The free Python build environment is not
guaranteed to ship a Node toolchain, so committing the bundle keeps deploys deterministic. After
changing anything in `frontend/src`, run:

```bash
make build      # regenerates frontend/dist
```

and commit the result along with the source.

---

## Assumptions and limitations

- **One truck profile for all routes.** Routing uses a conservative 80,000 lb GVH truck so the
  three candidates are compared like-for-like. Your load weight drives the risk rules, not the
  routing geometry — a heavier load does not re-route around a bridge it cannot cross.
- **Alternates are heuristic.** The provider cannot produce true alternatives at this distance,
  so "avoid tolls" and a perpendicular detour stand in. Both are real, routable truck routes, but
  they are not an exhaustive search of the corridor.
- **Forecast horizon is 16 days**, and accuracy degrades sharply past ~5. Missing hours are
  Unknown, never Low.
- **The heatmap is a weather view, not a schedule.** Its slider is anchored to "now + N hours" and
  is deliberately independent of the departure time; the shaded band on the track shows when the
  truck is actually on the road.
- **Free-tier hosting sleeps.** The first request after idle takes ~30–50 s to wake the instance,
  then plans take ~8 s.
- **The trip panel is a bottom sheet on phones.** Below 960px the map fills the screen and the form
  and route list slide up from the bottom edge, so the map is never squeezed into a strip.
- **Caching is time-shifted.** A cached plan reuses the heatmap computed at plan time, so the
  slider labels can be up to one hour behind the wall clock.
- Weather is sampled along the line of the route; it does not model elevation, road surface,
  chains, or state-level closures.

---

## Walkthrough outline (≈7 minutes)

1. **The problem (45s).** A car route is not a truck route. Show the same trip as a car vs. HGV.
2. **Plan a trip (60s).** Denver → Kansas City, 44,000 lb, 25-mile sampling.
3. **Read the ranking (90s).** Point at the risk bar: severe, high, unknown, average. Explain the
   exact tuple order and why there is no weighted score.
4. **Follow the weather (60s).** Click checkpoints; show each ETA's wind/rain/snow, and that the
   forecast hour changes down the route.
5. **The heatmap (60s).** Drag the slider independently of departure; note the trip band.
6. **The hard parts (120s).** Truck profile, the 100 km `alternative_routes` limit, snapping
   waypoints with `radiuses`, the 200/day quota, and why missing data is never "safe".
7. **Trade-offs (60s).** No database, no auth, committed `dist`, and what I would do next
   (real truck dimensions, closure feeds, elevation).
